# -*- coding: utf-8 -*-
"""
TradeMind backtest v9.18 research.
Строки укорочены для корректного копирования.
"""

import argparse
import traceback
from collections import Counter
from datetime import datetime, timezone

from market import (
    get_klines,
    get_klines_history,
    _normalize_symbol,
    find_major_liquidity,
    detect_sweep,
    _analyze_d1_context,
)
from strategy import analyze, get_1h_direction


# --- CONFIG ---

BT_LOOKBACK_D1 = 60
BT_LOOKBACK_1H = 1200
BT_LOOKBACK_15M = 4800
BT_LOOKBACK_5M = 14400
BT_LOOKBACK_1M = 500

WARMUP_1H = 150
DEFAULT_MAX_HOURS = 24

MIN_SCORE_BY_SYMBOL = {
    "default": 90,
    "INJUSDT": 88,
    "BCHUSDT": 92,
    "APTUSDT": 90,
}

BANNED_SYMBOLS = {
    "BTCUSDT", "SOLUSDT", "SUIUSDT",
}

UNPROFITABLE_SYMBOLS = {
    "ETHUSDT", "DOTUSDT", "XRPUSDT", "LINKUSDT",
}

ALL_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT",
    "XRPUSDT", "LINKUSDT", "DOTUSDT",
    "BCHUSDT", "APTUSDT", "SUIUSDT",
    "INJUSDT",
]

COOLDOWN_V910 = {
    "default": {2: 3, 3: 6},
    "APTUSDT": {2: 6, 3: 12},
    "INJUSDT": {2: 4, 3: 8},
    "BCHUSDT": {2: 4, 3: 8},
}

TRAILING_ENABLED = True
TRAILING_TRIGGER_R = 1.3
TRAILING_DISTANCE_R = 0.8

BE_PROFIT_OFFSET_R = 0.0
PARTIAL_ENABLED = True

# Каждый блок — короткие строки, без длинных
PARTIAL_CONFIGS = {
    "strong": {
        "partial_1_r": 0.8,
        "partial_2_r": 1.5,
        "be_at_r": 1.0,
        "partial_1_pct": 40,
        "partial_2_pct": 30,
        "trailing_trigger_r": 1.3,
        "trailing_distance_r": 0.8,
    },
    "default": {
        "partial_1_r": 0.7,
        "partial_2_r": 1.3,
        "be_at_r": 1.0,
        "partial_1_pct": 50,
        "partial_2_pct": 25,
        "trailing_trigger_r": 1.3,
        "trailing_distance_r": 0.8,
    },
    "weak": {
        "partial_1_r": 0.5,
        "partial_2_r": 1.1,
        "be_at_r": 0.8,
        "partial_1_pct": 50,
        "partial_2_pct": 25,
        "trailing_trigger_r": 1.3,
        "trailing_distance_r": 0.8,
    },
}

WEAK_SYMBOLS = {
    "SUIUSDT", "SOLUSDT", "APTUSDT", "BCHUSDT",
}

LIMIT_FILL_MAX_CANDLES = 12

RESEARCH_MODE = True


def get_min_score(symbol):
    if symbol in MIN_SCORE_BY_SYMBOL:
        return MIN_SCORE_BY_SYMBOL[symbol]
    return MIN_SCORE_BY_SYMBOL["default"]


def get_partial_config(symbol, score):
    if score >= 95:
        return PARTIAL_CONFIGS["strong"]
    if symbol in WEAK_SYMBOLS:
        return PARTIAL_CONFIGS["weak"]
    return PARTIAL_CONFIGS["default"]


def log(msg):
    print("[BT] " + str(msg), flush=True)


def ts_to_str(ms):
    try:
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "N/A"


def candles_until(candles, ts):
    return [c for c in candles if c["close_time"] < ts]


def has_active_position(trades, ts_now):
    if not trades:
        return False
    return trades[-1]["exit_ts"] > ts_now


def pnl_pct(entry, exit_price, direction):
    if direction == "LONG":
        return (exit_price - entry) / entry * 100
    return (entry - exit_price) / entry * 100


def blended_pnl_dual(entry, final_exit, direction,
                     p1_done, p1_exit, p1_pct,
                     p2_done, p2_exit, p2_pct):
    p1_w = 0.0
    p2_w = 0.0
    if p1_done and p1_exit is not None:
        p1_w = p1_pct / 100.0
    if p2_done and p2_exit is not None:
        p2_w = p2_pct / 100.0
    rem_w = max(0.0, 1.0 - p1_w - p2_w)
    total = 0.0
    if p1_w > 0:
        total += pnl_pct(entry, p1_exit, direction) * p1_w
    if p2_w > 0:
        total += pnl_pct(entry, p2_exit, direction) * p2_w
    if rem_w > 0:
        total += pnl_pct(entry, final_exit, direction) * rem_w
    return total


class CooldownMgr:
    RESET_AFTER_HOURS = 24
    MAX_CONSEC = 5

    def __init__(self, symbol, verbose=True):
        self.symbol = symbol
        self.consec = 0
        self.last_sl_ts = None
        self.verbose = verbose
        cfg = COOLDOWN_V910.get(symbol)
        if cfg is None:
            cfg = COOLDOWN_V910["default"]
        self.cfg = cfg

    def on_result(self, result_type, ts_ms):
        if self.last_sl_ts is not None:
            gap = (ts_ms - self.last_sl_ts) / 3600000.0
            if gap > self.RESET_AFTER_HOURS:
                if self.consec > 0 and self.verbose:
                    print("[CD] " + self.symbol
                          + ": auto-reset > 24h", flush=True)
                self.consec = 0
                self.last_sl_ts = None

        if result_type in ("TP", "BE"):
            if self.consec > 0 and self.verbose:
                print("[CD] " + self.symbol + ": "
                      + result_type + " сброс серии", flush=True)
            self.consec = 0
            self.last_sl_ts = None
        elif result_type == "SL":
            self.consec = min(self.consec + 1, self.MAX_CONSEC)
            self.last_sl_ts = ts_ms
            if self.verbose:
                print("[CD] " + self.symbol + ": SL #"
                      + str(self.consec), flush=True)

    def can_trade(self, ts_ms):
        if self.consec < 2:
            return True
        if self.last_sl_ts is None:
            return True
        hours = None
        for thresh in sorted(self.cfg.keys()):
            if self.consec >= thresh:
                hours = self.cfg[thresh]
        if hours is None:
            hours = max(self.cfg.values())
        elapsed = (ts_ms - self.last_sl_ts) / 3600000.0
        ok = elapsed >= hours
        if not ok and self.verbose:
            print("[CD] " + self.symbol + ": blocked, "
                  + str(round(elapsed, 1)) + "h < "
                  + str(hours) + "h", flush=True)
        return ok


def classify_reason(result, market_info):
    stage = result.get("stage", "WAIT")
    reason = str(result.get("reason", "")).lower()

    if stage == "READY":
        return "READY"
    if "session filter" in reason:
        return "session_blocked"
    if "нет актуальной major" in reason:
        n_bsl = market_info.get("bsl_count", 0)
        n_ssl = market_info.get("ssl_count", 0)
        if n_bsl == 0 and n_ssl == 0:
            return "no_levels_both"
        if n_bsl == 0:
            return "no_levels_bsl"
        if n_ssl == 0:
            return "no_levels_ssl"
        return "no_levels_matching"
    if "ilm" in reason:
        return "no_5m_ilm"
    if "слишком старый" in reason:
        return "ilm_too_old"
    if "sweep есть" in reason and "15m" in reason:
        return "no_15m_conf"
    if "ждём" in reason and "sweep" in reason:
        return "no_sweep"
    if "rr" in reason and "<" in reason:
        return "rr_too_low"
    if "tp" in reason:
        return "tp_failed"
    if "trend" in reason and "блок" in reason:
        return "trend_blocked"
    if "recovery" in reason and "блок" in reason:
        return "v_recovery_blocked"
    if "score" in reason and "блок" in reason:
        return "score_too_low"
    if "d1" in reason and "neutral" in reason:
        return "d1_blocked"
    if "volatility spike" in reason:
        return "volatility_spike"
    if "v9.10 promote" in reason:
        return "v910_promote"
    if "blocked" in reason or "заблок" in reason:
        return "other_blocked"
    return "stage_" + stage.lower()


def simulate_trade(symbol, trades_list, trade, candles_5m,
                   start_ts, max_hours, cfg,
                   use_breakeven=False,
                   use_partial_tp=False,
                   use_trailing=False):
    direction = trade["direction"]
    entry = float(trade["entry"])
    sl_initial = float(trade["sl"])
    tp = float(trade["tp"])

    risk = abs(entry - sl_initial)
    if risk <= 0:
        return ("ERROR", entry, start_ts, 0, 0.0, False)

    p1_r = cfg["partial_1_r"]
    p2_r = cfg["partial_2_r"]
    be_r = cfg["be_at_r"]
    p1_pct = cfg["partial_1_pct"]
    p2_pct = cfg["partial_2_pct"]
    trail_trig = cfg.get("trailing_trigger_r",
                         TRAILING_TRIGGER_R)
    trail_dist = cfg.get("trailing_distance_r",
                         TRAILING_DISTANCE_R)

    fill_deadline = start_ts + 12 * 5 * 60 * 1000
    filled = False
    fill_ts = None

    for c in candles_5m:
        if c["open_time"] < start_ts:
            continue
        if c["open_time"] > fill_deadline:
            break
        if direction == "LONG":
            if c["low"] <= entry:
                filled = True
                fill_ts = c["open_time"]
                break
        else:
            if c["high"] >= entry:
                filled = True
                fill_ts = c["open_time"]
                break

    if not filled:
        return ("NO_FILL", entry, fill_deadline, 0, 0.0, False)

    current_sl = sl_initial
    best_price = entry
    p1_done = False
    p1_exit = None
    p2_done = False
    p2_exit = None
    be_moved = False

    deadline = fill_ts + max_hours * 3600 * 1000
    last_seen = None
    held = 0

    for c in candles_5m:
        if c["open_time"] < fill_ts:
            continue
        if c["open_time"] > deadline:
            break

        held += 1
        last_seen = c
        high = c["high"]
        low = c["low"]

        if direction == "LONG":
            hit_tp = high >= tp
            hit_sl = low <= current_sl
        else:
            hit_tp = low <= tp
            hit_sl = high >= current_sl

        # exit type
        exit_t = "SL"
        if be_moved and abs(current_sl - entry) < risk * 0.05:
            exit_t = "BE"
        elif direction == "LONG" and current_sl > entry:
            exit_t = "BE"
        elif direction == "SHORT" and current_sl < entry:
            exit_t = "BE"

        if hit_sl and hit_tp:
            final = blended_pnl_dual(
                entry, current_sl, direction,
                p1_done, p1_exit, p1_pct,
                p2_done, p2_exit, p2_pct)
            return (exit_t, current_sl, c["open_time"],
                    held, final, p1_done or p2_done)

        if hit_sl:
            final = blended_pnl_dual(
                entry, current_sl, direction,
                p1_done, p1_exit, p1_pct,
                p2_done, p2_exit, p2_pct)
            return (exit_t, current_sl, c["open_time"],
                    held, final, p1_done or p2_done)

        if hit_tp:
            final = blended_pnl_dual(
                entry, tp, direction,
                p1_done, p1_exit, p1_pct,
                p2_done, p2_exit, p2_pct)
            return ("TP", tp, c["open_time"],
                    held, final, p1_done or p2_done)

        if direction == "LONG":
            if high > best_price:
                best_price = high
            move_r = (best_price - entry) / risk
        else:
            if low < best_price:
                best_price = low
            move_r = (entry - best_price) / risk

        if (use_partial_tp and PARTIAL_ENABLED
                and not p1_done and move_r >= p1_r):
            if direction == "LONG":
                p1_exit = entry + risk * p1_r
            else:
                p1_exit = entry - risk * p1_r
            p1_done = True

        if (use_partial_tp and PARTIAL_ENABLED
                and p1_done and not p2_done
                and move_r >= p2_r):
            if direction == "LONG":
                p2_exit = entry + risk * p2_r
            else:
                p2_exit = entry - risk * p2_r
            p2_done = True

        be_ready = (not use_partial_tp) or p1_done

        if (use_breakeven and be_ready
                and not be_moved and move_r >= be_r):
            if use_partial_tp:
                be_off = BE_PROFIT_OFFSET_R * risk
            else:
                be_off = 0.0

            if direction == "LONG":
                new_be = entry + be_off
                if new_be > current_sl:
                    current_sl = new_be
                    be_moved = True
            elif direction == "SHORT":
                new_be = entry - be_off
                if new_be < current_sl:
                    current_sl = new_be
                    be_moved = True

        if (use_trailing and TRAILING_ENABLED
                and move_r >= trail_trig):
            if direction == "LONG":
                new_sl = best_price - risk * trail_dist
                if new_sl > current_sl:
                    current_sl = new_sl
            else:
                new_sl = best_price + risk * trail_dist
                if new_sl < current_sl:
                    current_sl = new_sl

    if last_seen is not None:
        exit_price = last_seen["close"]
        final = blended_pnl_dual(
            entry, exit_price, direction,
            p1_done, p1_exit, p1_pct,
            p2_done, p2_exit, p2_pct)
        return ("TIMEOUT", exit_price,
                last_seen["open_time"], held, final,
                p1_done or p2_done)

    return ("TIMEOUT", entry, start_ts, 0, 0.0, False)


def run_backtest(symbol, max_hours,
                 use_breakeven=False,
                 use_partial_tp=False,
                 use_trailing=False):
    symbol = _normalize_symbol(symbol)
    min_score = get_min_score(symbol)

    log("Символ: " + symbol + " (min_score="
        + str(min_score) + ")")

    banned = set()
    if not RESEARCH_MODE:
        banned = set(BANNED_SYMBOLS)
        banned = banned | UNPROFITABLE_SYMBOLS

    if symbol in banned:
        log("[v9.18] SKIP banned: " + symbol)
        return [], {
            "stage_counter": Counter(),
            "reason_counter": Counter(),
            "exception_counter": Counter(),
            "market_stats": Counter(),
            "near_misses": [],
            "cooldown_skips": 0,
            "no_fill_count": 0,
            "no_fill_by_coin": {},
            "banned": True,
        }

    candles_d1 = get_klines_history("1d", 60, symbol)
    candles_1h = get_klines_history("1h", 1200, symbol)
    candles_15m = get_klines_history("15m", 4800, symbol)
    candles_5m = get_klines_history("5m", 14400, symbol)
    candles_1m = get_klines("1m", 500, symbol)

    if not candles_1h:
        log("Нет данных 1H")
        return [], {}

    log("Данных: 1H=" + str(len(candles_1h))
        + " 5M=" + str(len(candles_5m)))

    if len(candles_1h) <= 150:
        return [], {}

    trades = []
    stage_counter = Counter()
    reason_counter = Counter()
    exception_counter = Counter()
    market_stats = Counter()
    near_misses = []
    cooldown_skips = 0
    no_fill_count = 0
    no_fill_by_coin = Counter()

    cooldown_mgr = CooldownMgr(symbol, verbose=True)

    total = len(candles_1h) - 150
    log("Шагов: " + str(total))

    for i in range(150, len(candles_1h)):
        ts_now = candles_1h[i]["open_time"]

        if has_active_position(trades, ts_now):
            continue
        if not cooldown_mgr.can_trade(ts_now):
            cooldown_skips += 1
            continue

        c1h = candles_1h[:i]
        c15 = candles_until(candles_15m, ts_now)
        c5 = candles_until(candles_5m, ts_now)
        c1 = candles_until(candles_1m, ts_now)
        cd1 = candles_until(candles_d1, ts_now)

        if len(c15) < 60 or len(c5) < 60:
            exception_counter["n_short"] += 1
            continue

        price = c1[-1]["close"] if c1 else c1h[-1]["close"]

        try:
            levels = find_major_liquidity(
                c1h, price, 12, c15, c5, c1)
        except Exception as exc:
            err = type(exc).__name__
            exception_counter["market_" + err] += 1
            continue

        n_bsl = sum(1 for l in levels
                    if l.get("type") == "BSL")
        n_ssl = sum(1 for l in levels
                    if l.get("type") == "SSL")

        if n_bsl == 0 and n_ssl == 0:
            market_stats["empty"] += 1
        elif n_bsl == 0:
            market_stats["only_ssl"] += 1
        elif n_ssl == 0:
            market_stats["only_bsl"] += 1
        else:
            market_stats["both"] += 1

        try:
            direction = get_1h_direction(c1h)
            sweep = None
            if direction != "NEUTRAL":
                sweep = detect_sweep(
                    c1h, price, direction, levels)

            d1_context = None
            if len(cd1) >= 20:
                try:
                    d1_context = _analyze_d1_context(
                        cd1, price)
                except Exception:
                    d1_context = None

            result = analyze(
                c1h, c15, c5, price, levels, sweep,
                candles_1m=c1, d1_context=d1_context,
                fvgs=[], symbol=symbol)
        except Exception as exc:
            err = type(exc).__name__
            key = "analyze_" + err
            exception_counter[key] += 1
            continue

        stage = result.get("stage", "WAIT")
        score = int(result.get("score", 0))
        market_info = {
            "bsl_count": n_bsl,
            "ssl_count": n_ssl,
        }
        reason_key = classify_reason(result, market_info)

        if stage == "READY" and score < min_score:
            stage_counter["READY_LOW"] += 1
            reason_counter["ready_low"] += 1
            continue

        stage_counter[stage] += 1
        reason_counter[reason_key] += 1

        if score >= 50 and stage != "READY":
            nm = {
                "ts": ts_now,
                "direction": result.get("direction", "?"),
                "stage": stage,
                "score": score,
                "reason_key": reason_key,
                "trend": result.get("trend_activity", 0),
                "bsl": n_bsl,
                "ssl": n_ssl,
            }
            near_misses.append(nm)

        if stage != "READY":
            continue

        entry = result.get("entry")
        sl = result.get("sl")
        tp = result.get("tp")
        if entry is None or sl is None or tp is None:
            continue

        trade = {
            "coin": symbol,
            "direction": result["direction"],
            "entry": float(entry),
            "sl": float(sl),
            "tp": float(tp),
            "rr": result.get("rr"),
            "score": score,
            "open_ts": ts_now,
        }

        cfg = get_partial_config(symbol, score)

        res = simulate_trade(
            symbol, trades, trade, candles_5m,
            ts_now, max_hours, cfg,
            use_breakeven=use_breakeven,
            use_partial_tp=use_partial_tp,
            use_trailing=use_trailing)

        (res_type, exit_price, exit_ts, held,
         trade_pnl, partial_hit) = res

        if res_type == "NO_FILL":
            no_fill_count += 1
            no_fill_by_coin[symbol] += 1
            log("[" + str(i) + "] NO_FILL")
            cooldown_mgr.on_result("NO_FILL", exit_ts)
            continue

        trade["result"] = res_type
        trade["exit_price"] = exit_price
        trade["exit_ts"] = exit_ts
        trade["held_5m"] = held
        trade["pnl"] = trade_pnl
        trade["partial_hit"] = partial_hit
        trade["partial_cfg"] = cfg

        trades.append(trade)
        cooldown_mgr.on_result(res_type, exit_ts)

        tag = "P" if partial_hit else " "
        log("[" + str(i) + "] "
            + trade["direction"] + " "
            + "score=" + str(score) + " " + tag
            + " -> " + res_type
            + " pnl=" + ("%+.2f%%" % trade["pnl"]))

    if cooldown_skips > 0:
        log("Cooldown skips: " + str(cooldown_skips))
    if no_fill_count > 0:
        log("NO_FILL: " + str(no_fill_count))

    near_misses.sort(key=lambda x: x["score"], reverse=True)

    diag = {
        "stage_counter": stage_counter,
        "reason_counter": reason_counter,
        "exception_counter": exception_counter,
        "market_stats": market_stats,
        "near_misses": near_misses[:15],
        "cooldown_skips": cooldown_skips,
        "no_fill_count": no_fill_count,
        "no_fill_by_coin": dict(no_fill_by_coin),
    }
    return trades, diag


def print_report(symbol, trades, diag,
                 use_breakeven=False,
                 use_partial_tp=False,
                 use_trailing=False):
    print()
    print("=" * 70)
    print("ОТЧЁТ - " + symbol)
    print("=" * 70)

    if diag.get("banned"):
        print("[SKIP] " + symbol + " banned")
        return

    if not trades:
        print("Нет сделок.")
        return

    tp = sum(1 for t in trades if t["result"] == "TP")
    sl = sum(1 for t in trades if t["result"] == "SL")
    be = sum(1 for t in trades if t["result"] == "BE")
    to = sum(1 for t in trades if t["result"] == "TIMEOUT")
    p_hits = sum(1 for t in trades if t.get("partial_hit"))

    resolved = tp + sl
    wr = tp / resolved * 100 if resolved else 0

    total_pnl = sum(t["pnl"] for t in trades)
    avg_pnl = total_pnl / len(trades)
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] < 0]
    avg_w = sum(wins) / len(wins) if wins else 0
    avg_l = sum(losses) / len(losses) if losses else 0

    eq = 0
    peak = 0
    max_dd = 0
    for t in trades:
        eq += t["pnl"]
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > max_dd:
            max_dd = dd

    print("Всего сделок: " + str(len(trades)))
    print("  TP:      " + str(tp))
    print("  SL:      " + str(sl))
    print("  BE:      " + str(be))
    print("  Timeout: " + str(to))
    print("  Partial: " + str(p_hits))
    print("Win rate: " + ("%.1f%%" % wr))
    print("Total PnL: " + ("%+.2f%%" % total_pnl))
    print("Avg PnL:   " + ("%+.2f%%" % avg_pnl))
    print("Avg win:   " + ("%+.2f%%" % avg_w))
    print("Avg loss:  " + ("%+.2f%%" % avg_l))
    print("Max DD:    " + ("%.2f%%" % (-max_dd)))


def run_multi(max_hours,
              use_breakeven=False,
              use_partial_tp=False,
              use_trailing=False,
              symbols=None):
    if symbols is None:
        symbols = ALL_SYMBOLS

    mode = "RESEARCH" if RESEARCH_MODE else "PROD"

    print()
    print("#" * 70)
    print("### MULTI v9.18 [" + mode + "] "
          + str(len(symbols)) + " монет x 40 дней")
    print("#" * 70)
    print("### MIN_SCORE: " + str(MIN_SCORE_BY_SYMBOL))
    print("#" * 70)

    all_summary = []
    for sym in symbols:
        try:
            trades, diag = run_backtest(
                sym, max_hours,
                use_breakeven=use_breakeven,
                use_partial_tp=use_partial_tp,
                use_trailing=use_trailing)

            print_report(sym, trades, diag,
                         use_breakeven=use_breakeven,
                         use_partial_tp=use_partial_tp,
                         use_trailing=use_trailing)

            no_fill = diag.get("no_fill_count", 0)
            banned = diag.get("banned", False)

            if banned:
                all_summary.append(
                    (sym, 0, 0, 0, 0, 0, 0.0, 0, True))
            elif trades:
                tp = sum(1 for t in trades
                         if t["result"]
 ==                "TP")
                sl = sum( wr1 for t in trades
                         if t =[" tpresult"] == "SL")
                be / = sum(1 for t in trades
 r                         if t["result"] == "BE")
                to = sum(1 for t in trades
                         if t["result"] == "TIMEOUT")
                r = tp + sl * 100 if r else 0
                pnl = sum(t["pnl"] for t in trades)
                all_summary.append(
                    (sym, len(trades), tp, sl, be, to,
                     wr, pnl, no_fill, False))
            else:
                all_summary.append(
                    (sym, 0, 0, 0, 0, 0, 0.0, no_fill, False))
        except Exception as exc:
            print("[BT] " + sym + " FAILED: " + str(exc))
            traceback.print_exc()
            all_summary.append(
                (sym, 0, 0, 0, 0, 0, 0.0, 0, False))

    print()
    print("=" * 78)
    print("СВОДКА v9.18 [" + mode + "] 40 дней")
    print("=" * 78)
    hdr = ("Символ".ljust(10) + "MS".ljust(5)
           + "Fill".ljust(6) + "NoF".ljust(5)
           + "TP".ljust(4) + "SL".ljust(4) + "BE".ljust(4)
           + "TO".ljust(4) + "WR".ljust(7)
           + "PnL".ljust(10) + "Stat")
    print(hdr)
    print("-" * 78)

    tt = 0
    ttp = 0
    tsl = 0
    tbe = 0
    tto = 0
    tpnl = 0.0
    tnf = 0

    for row in all_summary:
        (sym, cnt, tp, sl, be, to, wr,
         pnl, no_fill, banned) = row
        stat = "BANNED" if banned else "OK"
        ms = get_min_score(sym)
        line = (sym.ljust(10)
                + str(ms).ljust(5)
                + str(cnt).ljust(6)
                + str(no_fill).ljust(5)
                + str(tp).ljust(4)
                + str(sl).ljust(4)
                + str(be).ljust(4)
                + str(to).ljust(4)
                + ("%.1f" % wr).ljust(7)
                + ("%+.2f%%" % pnl).ljust(10)
                + stat)
        print(line)
        tt += cnt
        ttp += tp
        tsl += sl
        tbe += be
        tto += to
        tpnl += pnl
        tnf += no_fill

    print("-" * 78)
    r = ttp + tsl
    twr = ttp / r * 100 if r else 0
    print("ИТОГО".ljust(15)
          + str(tt).ljust(6)
          + str(tnf).ljust(5)
          + str(ttp).ljust(4)
          + str(tsl).ljust(4)
          + str(tbe).ljust(4)
          + str(tto).ljust(4)
          + ("%.1f" % twr).ljust(7)
          + ("%+.2f%%" % tpnl))
    print()
    print("Всего сделок: " + str(tt))
    print("NO_FILL: " + str(tnf))
    print("Sum PnL: " + ("%+.2f%%" % tpnl))
    print("=" * 78)


def main():
    global RESEARCH_MODE

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="INJUSDT")
    parser.add_argument("--max-hours", type=int, default=24)
    parser.add_argument("--single", action="store_true")
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--research", action="store_true")
    parser.add_argument("--prod", action="store_true")
    parser.add_argument("--min-score", action="append",
                        default=[])
    args = parser.parse_args()

    if args.prod:
        RESEARCH_MODE = False
    if args.research:
        RESEARCH_MODE = True

    for override in args.min_score:
        try:
            sym, val = override.split("=")
            MIN_SCORE_BY_SYMBOL[sym.strip().upper()] = int(val)
        except Exception:
            print("[WARN] bad --min-score: " + override)

    if args.single:
        trades, diag = run_backtest(
            args.symbol, args.max_hours,
            use_breakeven=True,
            use_partial_tp=True,
            use_trailing=True)
        print_report(args.symbol, trades, diag, True, True, True)
    else:
        symbols = None
        if args.symbols:
            symbols = [s.strip().upper()
                       for s in args.symbols.split(",")
                       if s.strip()]
        run_multi(args.max_hours,
                  use_breakeven=True,
                  use_partial_tp=True,
                  use_trailing=True,
                  symbols=symbols)


if __name__ == "__main__":
    main()