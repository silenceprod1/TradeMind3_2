"""
TradeMind backtest v9.13 (90 дней).
BE в entry, be_at_r=1.0, trailing 1.3/0.8, cooldown per-symbol v3.
Banned: BTC/SOL/SUI. Unprofitable: ETH/DOT/XRP.
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


# ── 90 дней ──
BT_LOOKBACK_D1 = 180
BT_LOOKBACK_1H = 2160
BT_LOOKBACK_15M = 8640
BT_LOOKBACK_5M = 25920
BT_LOOKBACK_1M = 500

WARMUP_1H = 150
DEFAULT_MAX_HOURS = 24

BANNED_SYMBOLS = {"BTCUSDT", "SOLUSDT", "SUIUSDT"}
UNPROFITABLE_SYMBOLS = {"ETHUSDT", "DOTUSDT", "XRPUSDT"}

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
PARTIAL_CONFIGS = {
    "strong": {
        "partial_1_r": 0.8, "partial_2_r": 1.5,
        "be_at_r": 1.0,
        "partial_1_pct": 40, "partial_2_pct": 30,
    },
    "default": {
        "partial_1_r": 0.7, "partial_2_r": 1.3,
        "be_at_r": 1.0,
        "partial_1_pct": 50, "partial_2_pct": 25,
    },
    "weak": {
        "partial_1_r": 0.5, "partial_2_r": 1.1,
        "be_at_r": 0.8,
        "partial_1_pct": 50, "partial_2_pct": 25,
    },
}
WEAK_SYMBOLS = {"SUIUSDT", "SOLUSDT", "APTUSDT", "BCHUSDT"}

LIMIT_FILL_MAX_CANDLES = 12


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
            hours_gap = (ts_ms - self.last_sl_ts) / 3600000.0
            if hours_gap > self.RESET_AFTER_HOURS and self.consec > 0:
                if self.verbose:
                    print("[CD] " + self.symbol + ": auto-reset "
                          + "(> " + str(self.RESET_AFTER_HOURS)
                          + "h без SL, было " + str(self.consec) + ")",
                          flush=True)
                self.consec = 0
                self.last_sl_ts = None

        if result_type == "TP" or result_type == "BE":
            if self.consec > 0 and self.verbose:
                print("[CD] " + self.symbol + ": " + result_type
                      + " -> серия сброшена (было "
                      + str(self.consec) + " SL)", flush=True)
            self.consec = 0
            self.last_sl_ts = None
        elif result_type == "SL":
            self.consec = min(self.consec + 1, self.MAX_CONSEC)
            self.last_sl_ts = ts_ms
            if self.verbose:
                ts_str = ts_to_str(ts_ms)
                print("[CD] " + self.symbol + ": SL #"
                      + str(self.consec) + " @ " + ts_str, flush=True)

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
        elapsed_h = (ts_ms - self.last_sl_ts) / 3600000.0
        ok = elapsed_h >= hours
        if not ok and self.verbose:
            print("[CD] " + self.symbol + ": blocked, "
                  + str(round(elapsed_h, 1)) + "h < " + str(hours)
                  + "h (consec=" + str(self.consec) + ")", flush=True)
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
    if "ilm" in reason or "5m ilm" in reason:
        return "no_5m_ilm"
    if "слишком старый" in reason:
        return "ilm_too_old"
    if "sweep есть" in reason and "15m" in reason:
        return "no_15m_conf"
    if "ждём" in reason and "sweep" in reason:
        return "no_sweep"
    if "rr" in reason and "<" in reason:
        return "rr_too_low"
    if "tp" in reason or "major liquidity не найдена" in reason:
        return "tp_failed"
    if "trend" in reason and "блок" in reason:
        return "trend_blocked"
    if "recovery" in reason and "блок" in reason:
        return "v_recovery_blocked"
    if "score" in reason and "блок" in reason:
        return "score_too_low"
    if "d1" in reason and ("neutral" in reason or "d1_" in reason):
        return "d1_blocked"
    if "volatility spike" in reason:
        return "volatility_spike"
    if "v9.10 promote" in reason:
        return "v910_promote"
    if "blocked" in reason or "заблок" in reason:
        return "other_blocked"
    return "stage_" + stage.lower()


def simulate_trade_v913(trade, candles_5m, start_ts, max_hours, cfg,
                        use_breakeven=False, use_partial_tp=False,
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

    fill_check_until = start_ts + LIMIT_FILL_MAX_CANDLES * 5 * 60 * 1000
    limit_filled = False
    fill_ts = None

    for c in candles_5m:
        if c["open_time"] < start_ts:
            continue
        if c["open_time"] > fill_check_until:
            break
        if direction == "LONG":
            if c["low"] <= entry:
                limit_filled = True
                fill_ts = c["open_time"]
                break
        else:
            if c["high"] >= entry:
                limit_filled = True
                fill_ts = c["open_time"]
                break

    if not limit_filled:
        return ("NO_FILL", entry, fill_check_until, 0, 0.0, False)

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
            return (exit_t, current_sl, c["open_time"], held, final,
                    p1_done or p2_done)

        if hit_sl:
            final = blended_pnl_dual(
                entry, current_sl, direction,
                p1_done, p1_exit, p1_pct,
                p2_done, p2_exit, p2_pct)
            return (exit_t, current_sl, c["open_time"], held, final,
                    p1_done or p2_done)

        if hit_tp:
            final = blended_pnl_dual(
                entry, tp, direction,
                p1_done, p1_exit, p1_pct,
                p2_done, p2_exit, p2_pct)
            return ("TP", tp, c["open_time"], held, final,
                    p1_done or p2_done)

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
                and p1_done and not p2_done and move_r >= p2_r):
            if direction == "LONG":
                p2_exit = entry + risk * p2_r
            else:
                p2_exit = entry - risk * p2_r
            p2_done = True

        be_ready = (not use_partial_tp) or p1_done

        if use_breakeven and be_ready and not be_moved and move_r >= be_r:
            if use_partial_tp:
                be_offset = BE_PROFIT_OFFSET_R * risk
            else:
                be_offset = 0.0

            if direction == "LONG":
                new_be = entry + be_offset
                if new_be > current_sl:
                    current_sl = new_be
                    be_moved = True
            elif direction == "SHORT":
                new_be = entry - be_offset
                if new_be < current_sl:
                    current_sl = new_be
                    be_moved = True

        if (use_trailing and TRAILING_ENABLED
                and move_r >= TRAILING_TRIGGER_R):
            if direction == "LONG":
                new_sl = best_price - risk * TRAILING_DISTANCE_R
                if new_sl > current_sl:
                    current_sl = new_sl
            else:
                new_sl = best_price + risk * TRAILING_DISTANCE_R
                if new_sl < current_sl:
                    current_sl = new_sl

    if last_seen is not None:
        exit_price = last_seen["close"]
        final = blended_pnl_dual(
            entry, exit_price, direction,
            p1_done, p1_exit, p1_pct,
            p2_done, p2_exit, p2_pct)
        return ("TIMEOUT", exit_price, last_seen["open_time"], held,
                final, p1_done or p2_done)

    return ("TIMEOUT", entry, start_ts, 0, 0.0, False)


def run_backtest(symbol, max_hours,
                 use_breakeven=False, use_partial_tp=False,
                 use_trailing=False,
                 exclude_unprofitable=True):
    symbol = _normalize_symbol(symbol)
    log("Символ: " + symbol
        + " (BE=" + str(use_breakeven)
        + " partial=" + str(use_partial_tp)
        + " trail=" + str(use_trailing)
        + " cooldown=per-symbol)")

    banned = set(BANNED_SYMBOLS)
    if exclude_unprofitable:
        banned = banned | UNPROFITABLE_SYMBOLS

    if symbol in banned:
        log("[v9.13] SKIP banned symbol: " + symbol)
        empty_diag = {
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
        return [], empty_diag

    candles_d1 = get_klines_history("1d", BT_LOOKBACK_D1, symbol)
    candles_1h = get_klines_history("1h", BT_LOOKBACK_1H, symbol)
    candles_15m = get_klines_history("15m", BT_LOOKBACK_15M, symbol)
    candles_5m = get_klines_history("5m", BT_LOOKBACK_5M, symbol)
    candles_1m = get_klines("1m", BT_LOOKBACK_1M, symbol)

    if not candles_1h:
        log("Нет данных 1H")
        return [], {}

    data_msg = ("Данных: D1=" + str(len(candles_d1))
                + " 1H=" + str(len(candles_1h))
                + " 15M=" + str(len(candles_15m))
                + " 5M=" + str(len(candles_5m))
                + " 1M=" + str(len(candles_1m)))
    log(data_msg)

    if len(candles_1h) <= WARMUP_1H:
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

    total = len(candles_1h) - WARMUP_1H
    log("Шагов: " + str(total))

    for i in range(WARMUP_1H, len(candles_1h)):
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
            levels = find_major_liquidity(c1h, price, 12, c15, c5, c1)
        except Exception as exc:
            err = type(exc).__name__
            exception_counter["market_" + err] += 1
            continue

        n_bsl = sum(1 for l in levels if l.get("type") == "BSL")
        n_ssl = sum(1 for l in levels if l.get("type") == "SSL")

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
                sweep = detect_sweep(c1h, price, direction, levels)

            d1_context = None
            if len(cd1) >= 20:
                try:
                    d1_context = _analyze_d1_context(cd1, price)
                except Exception:
                    d1_context = None

            result = analyze(c1h, c15, c5, price, levels, sweep,
                             candles_1m=c1, d1_context=d1_context,
                             fvgs=[], symbol=symbol)
        except Exception as exc:
            err = type(exc).__name__
            key = "analyze_" + err
            exception_counter[key] += 1
            if exception_counter[key] == 1:
                print("[BT] FIRST EXCEPTION " + err + ": " + str(exc),
                      flush=True)
                traceback.print_exc()
            continue

        stage = result.get("stage", "WAIT")
        score = int(result.get("score", 0))
        market_info = {"bsl_count": n_bsl, "ssl_count": n_ssl}
        reason_key = classify_reason(result, market_info)

        stage_counter[stage] += 1
        reason_counter[reason_key] += 1

        if score >= 50 and stage != "READY":
            nm = {
                "ts": ts_now,
                "direction": result.get("direction", "?"),
                "stage": stage,
                "score": score,
                "reason_key": reason_key,
                "reason": str(result.get("reason", ""))[:80],
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

        (res_type, exit_price, exit_ts, held, trade_pnl,
         partial_hit) = simulate_trade_v913(
            trade, candles_5m, ts_now, max_hours,
            cfg=cfg,
            use_breakeven=use_breakeven,
            use_partial_tp=use_partial_tp,
            use_trailing=use_trailing,
        )

        if res_type == "NO_FILL":
            no_fill_count += 1
            no_fill_by_coin[symbol] += 1
            log("[" + str(i).rjust(4) + "] "
                + trade["direction"].ljust(5)
                + " entry=" + str(round(entry, 4))
                + " -> NO_FILL")
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

        partial_tag = "P" if partial_hit else " "
        log("[" + str(i).rjust(4) + "] "
            + trade["direction"].ljust(5)
            + " entry=" + str(round(entry, 4))
            + " sl=" + str(round(sl, 4))
            + " tp=" + str(round(tp, 4))
            + " rr=" + str(round(trade["rr"], 2))
            + " score=" + str(score)
            + " " + partial_tag
            + " -> " + res_type.ljust(7)
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
                 use_breakeven=False, use_partial_tp=False,
                 use_trailing=False):
    labels = []
    if use_breakeven:
        labels.append("BEentry")
    if use_partial_tp:
        labels.append("PARTIALx2")
    if use_trailing:
        labels.append("TRAIL1.3/0.8")
    labels.append("CDv3")
    label = "+".join(labels)

    print()
    print("=" * 70)
    print("ОТЧЁТ БЭКТЕСТА v9.13 - " + symbol + " [" + label + "]")
    print("=" * 70)

    stage_counter = diag.get("stage_counter", Counter())
    reason_counter = diag.get("reason_counter", Counter())
    exception_counter = diag.get("exception_counter", Counter())
    market_stats = diag.get("market_stats", Counter())
    near_misses = diag.get("near_misses", [])
    cooldown_skips = diag.get("cooldown_skips", 0)
    no_fill_count = diag.get("no_fill_count", 0)

    if diag.get("banned"):
        print()
        print("[SKIP] " + symbol + " в BANNED_SYMBOLS.")
        print("=" * 70)
        return

    total = sum(stage_counter.values())
    total_exc = sum(exception_counter.values())

    print()
    print("Всего шагов проанализировано: " + str(total))
    print("Пропущено через exception: " + str(total_exc))
    if cooldown_skips > 0:
        print("Пропущено через cooldown: " + str(cooldown_skips))
    if no_fill_count > 0:
        print("NO_FILL (лимитка не исполнилась): " + str(no_fill_count))
    print()

    if exception_counter:
        print("EXCEPTIONS:")
        for k, v in exception_counter.most_common(10):
            print("  " + k.ljust(40) + " " + str(v))
        print()

    print("MARKET:")
    for k in ["both", "only_ssl", "only_bsl", "empty"]:
        v = market_stats.get(k, 0)
        pct = v / total * 100 if total else 0
        print("  " + k.ljust(15) + " " + str(v).rjust(4)
              + "  (" + ("%.1f%%" % pct) + ")")
    print()

    print("РАСПРЕДЕЛЕНИЕ ПО СТАДИЯМ:")
    for stage in ["READY", "15M_CONFIRMED", "SWEPT", "WAIT"]:
        cnt = stage_counter.get(stage, 0)
        pct = cnt / total * 100 if total else 0
        print("  " + stage.ljust(16) + " " + str(cnt).rjust(4)
              + "  (" + ("%.1f%%" % pct) + ")")

    print()
    print("ТОП ПРИЧИН ОСТАНОВКИ:")
    for reason, cnt in reason_counter.most_common(15):
        pct = cnt / total * 100 if total else 0
        print("  " + reason.ljust(26) + " " + str(cnt).rjust(4)
              + "  (" + ("%.1f%%" % pct) + ")")

    print()
    print("=" * 70)
    print("ТОП-15 'ПОЧТИ СРАБОТАВШИХ'")
    print("=" * 70)
    if not near_misses:
        print("Нет сетапов с score >= 50")
    else:
        hdr = ("Дата".ljust(17) + "Напр.".ljust(6)
               + "Stage".ljust(16) + "Score".ljust(6)
               + "Trend".ljust(7) + "BSL/SSL".ljust(10) + "Reason")
        print(hdr)
        print("-" * 70)
        for nm in near_misses:
            trend_s = "%.2f" % nm["trend"]
            bs_s = str(nm["bsl"]) + "/" + str(nm["ssl"])
            line = (ts_to_str(nm["ts"]).ljust(17)
                    + nm["direction"].ljust(6)
                    + nm["stage"].ljust(16)
                    + str(nm["score"]).ljust(6)
                    + trend_s.ljust(7)
                    + bs_s.ljust(10)
                    + nm["reason_key"])
            print(line)

    print()
    print("=" * 70)
    print("СДЕЛКИ (READY + filled)")
    print("=" * 70)

    if not trades:
        print("Нет исполненных сделок.")
        return

    tp = sum(1 for t in trades if t["result"] == "TP")
    sl = sum(1 for t in trades if t["result"] == "SL")
    be = sum(1 for t in trades if t["result"] == "BE")
    timeout = sum(1 for t in trades if t["result"] == "TIMEOUT")
    partial_hits = sum(1 for t in trades if t.get("partial_hit"))

    resolved = tp + sl
    win_rate = tp / resolved * 100 if resolved else 0

    total_pnl = sum(t["pnl"] for t in trades)
    avg_pnl = total_pnl / len(trades)
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] < 0]
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0

    equity = 0
    peak = 0
    max_dd = 0
    for t in trades:
        equity += t["pnl"]
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    print("Всего сделок (filled): " + str(len(trades)))
    print("  TP:      " + str(tp))
    print("  SL:      " + str(sl))
    print("  BE:      " + str(be))
    print("  Timeout: " + str(timeout))
    if use_partial_tp:
        pct = partial_hits / len(trades) * 100
        print("  Partial hits: " + str(partial_hits)
              + " (" + ("%.1f%%" % pct) + ")")
    print("Win rate: " + ("%.1f%%" % win_rate))
    print("Total PnL: " + ("%+.2f%%" % total_pnl))
    print("Avg PnL:   " + ("%+.2f%%" % avg_pnl))
    print("Avg win:   " + ("%+.2f%%" % avg_win))
    print("Avg loss:  " + ("%+.2f%%" % avg_loss))
    print("Max DD:    " + ("%.2f%%" % (-max_dd)))


def run_multi_backtest_with_hours(max_hours, use_breakeven=False,
                                  use_partial_tp=False,
                                  use_trailing=False,
                                  symbols=None,
                                  exclude_unprofitable=True):
    if symbols is None:
        symbols = ALL_SYMBOLS

    labels = []
    if use_breakeven:
        labels.append("BEentry")
    if use_partial_tp:
        labels.append("PARTIALx2")
    if use_trailing:
        labels.append("TRAIL1.3/0.8")
    labels.append("CDv3")
    label = "+".join(labels)

    print()
    print("#" * 70)
    print("### MULTI BACKTEST v9.13 - " + label
          + " - " + str(len(symbols)) + " монет x 90 дней")
    print("#" * 70)
    print("### Banned: " + str(sorted(BANNED_SYMBOLS)))
    if exclude_unprofitable:
        print("### +Excluded: " + str(sorted(UNPROFITABLE_SYMBOLS)))
    print("### BE: в ENTRY (offset=" + str(BE_PROFIT_OFFSET_R) + "R)")
    print("### be_at_r: strong=1.0, default=1.0, weak=0.8")
    print("### Trailing: trigger " + str(TRAILING_TRIGGER_R)
          + "R, dist " + str(TRAILING_DISTANCE_R) + "R")
    print("#" * 70)

    all_summary = []
    for sym in symbols:
        try:
            trades, diag = run_backtest(
                sym, max_hours,
                use_breakeven=use_breakeven,
                use_partial_tp=use_partial_tp,
                use_trailing=use_trailing,
                exclude_unprofitable=exclude_unprofitable,
            )
            print_report(sym, trades, diag,
                         use_breakeven=use_breakeven,
                         use_partial_tp=use_partial_tp,
                         use_trailing=use_trailing)

            no_fill = diag.get("no_fill_count", 0)
            banned = diag.get("banned", False)

            if banned:
                all_summary.append(
                    (sym, 0, 0, 0, 0, 0, 0, 0.0, 0, True))
            elif trades:
                tp = sum(1 for t in trades if t["result"] == "TP")
                sl = sum(1 for t in trades if t["result"] == "SL")
                be = sum(1 for t in trades if t["result"] == "BE")
                to = sum(1 for t in trades if t["result"] == "TIMEOUT")
                resolved = tp + sl
                wr = tp / resolved * 100 if resolved else 0
                pnl = sum(t["pnl"] for t in trades)
                all_summary.append(
                    (sym, len(trades), tp, sl, be, to, wr, pnl,
                     no_fill, False))
            else:
                all_summary.append(
                    (sym, 0, 0, 0, 0, 0, 0, 0.0, no_fill, False))
        except Exception as exc:
            print("[BT] " + sym + " FAILED: " + str(exc), flush=True)
            traceback.print_exc()
            all_summary.append(
                (sym, 0, 0, 0, 0, 0, 0, 0.0, 0, False))

    print()
    print("=" * 78)
    print("СВОДКА - " + label
          + " (max_hours=" + str(max_hours) + ", 90 дней)")
    print("=" * 78)
    hdr = ("Символ".ljust(10) + "Filled".ljust(8) + "NoFill".ljust(8)
           + "TP".ljust(5) + "SL".ljust(5) + "BE".ljust(5) + "TO".ljust(5)
           + "WR".ljust(7) + "PnL".ljust(10) + "Stat".ljust(8))
    print(hdr)
    print("-" * 78)

    total_trades = 0
    total_tp = 0
    total_sl = 0
    total_be = 0
    total_to = 0
    total_pnl = 0.0
    total_no_fill = 0

    for row in all_summary:
        (sym, cnt, tp, sl, be, to, wr, pnl, no_fill, banned) = row
        stat = "BANNED" if banned else "OK"
        line = (sym.ljust(10)
                + str(cnt).ljust(8)
                + str(no_fill).ljust(8)
                + str(tp).ljust(5)
                + str(sl).ljust(5)
                + str(be).ljust(5)
                + str(to).ljust(5)
                + ("%.1f" % wr).ljust(7)
                + ("%+.2f%%" % pnl).ljust(10)
                + "  " + stat)
        print(line)
        total_trades += cnt
        total_tp += tp
        total_sl += sl
        total_be += be
        total_to += to
        total_pnl += pnl
        total_no_fill += no_fill

    print("-" * 78)
    resolved = total_tp + total_sl
    total_wr = total_tp / resolved * 100 if resolved else 0
    print("ИТОГО".ljust(10)
          + str(total_trades).ljust(8)
          + str(total_no_fill).ljust(8)
          + str(total_tp).ljust(5)
          + str(total_sl).ljust(5)
          + str(total_be).ljust(5)
          + str(total_to).ljust(5)
          + ("%.1f" % total_wr).ljust(7)
          + ("%+.2f%%" % total_pnl))
    print()
    print("Всего filled сделок: " + str(total_trades))
    print("NO_FILL (лимитка не исполнилась): " + str(total_no_fill))
    total_signals = total_trades + total_no_fill
    if total_signals > 0:
        fill_rate = total_trades / total_signals * 100
        print("Fill rate: " + ("%.1f%%" % fill_rate))
    print("  TP: " + str(total_tp)
          + "  SL: " + str(total_sl)
          + "  BE: " + str(total_be)
          + "  Timeout: " + str(total_to))
    print("Win rate: " + ("%.1f%%" % total_wr)
          + " (от " + str(resolved) + " закрытых)")
    print("Sum PnL: " + ("%+.2f%%" % total_pnl))
    print("=" * 78)


def run_multi_backtest():
    run_multi_backtest_with_hours(
        DEFAULT_MAX_HOURS,
        use_breakeven=True,
        use_partial_tp=True,
        use_trailing=True,
    )


def main():
    parser = argparse.ArgumentParser(
        description="TradeMind v9.13 backtest (90 дней)")
    parser.add_argument("--symbol", default="INJUSDT")
    parser.add_argument("--max-hours", type=int,
                        default=DEFAULT_MAX_HOURS)
    parser.add_argument("--single", action="store_true")
    parser.add_argument("--no-be", action="store_true")
    parser.add_argument("--no-partial", action="store_true")
    parser.add_argument("--no-trailing", action="store_true")
    parser.add_argument("--symbols", default=None)
    parser.add_argument("--include-unprofitable",
                        action="store_true",
                        help="включить ETH/DOT/XRP")
    args = parser.parse_args()

    use_be = not args.no_be
    use_partial = not args.no_partial
    use_trailing = not args.no_trailing
    exclude_unprof = not args.include_unprofitable

    if args.single:
        trades, diag = run_backtest(
            args.symbol, args.max_hours,
            use_breakeven=use_be,
            use_partial_tp=use_partial,
            use_trailing=use_trailing,
            exclude_unprofitable=exclude_unprof,
        )
        print_report(args.symbol, trades, diag,
                     use_breakeven=use_be,
                     use_partial_tp=use_partial,
                     use_trailing=use_trailing)
    else:
        symbols = None
        if args.symbols:
            symbols = [s.strip().upper()
                       for s in args.symbols.split(",")
                       if s.strip()]
        run_multi_backtest_with_hours(
            args.max_hours,
            use_breakeven=use_be,
            use_partial_tp=use_partial,
            use_trailing=use_trailing,
            symbols=symbols,
            exclude_unprofitable=exclude_unprof,
        )


if __name__ == "__main__":
    main()