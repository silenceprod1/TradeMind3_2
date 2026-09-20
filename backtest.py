"""
TradeMind backtest v9.10 — BE+PARTIAL+TRAIL+COOLDOWN v2.

Изменения vs v9.9:
- BANNED_SYMBOLS: SOL/SUI/BTC отключены (WR<15% или PnL<-2% на 90d)
- Per-symbol cooldown после СЕРИИ SL (2→3h, 3+→6h)
- Динамический partial по score и символу (strong/default/weak)
- Два partial уровня (например 0.7R + 1.3R)
- BE на 0.5R (было 1.0R)
- Trailing: триггер 1.3R / дистанция 0.8R (плотнее)
- Fill rate NO_FILL: то же окно 12×5M = 1 час
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


# ============================================================
# CONFIG v9.10
# ============================================================

BT_LOOKBACK_D1 = 180
BT_LOOKBACK_1H = 2160
BT_LOOKBACK_15M = 8640
BT_LOOKBACK_5M = 25920
BT_LOOKBACK_1M = 500

WARMUP_1H = 150
DEFAULT_MAX_HOURS = 24

# ─── Banned: не торгуем, но показываем в отчёте ───
BANNED_SYMBOLS = {"SOLUSDT", "SUIUSDT", "BTCUSDT"}

# ─── Per-symbol cooldown (часов после N-го подряд SL) ───
COOLDOWN_V910 = {
    "default": {2: 3, 3: 6},
    "APTUSDT": {2: 6, 3: 12},
    "INJUSDT": {2: 4, 3: 8},
    "BCHUSDT": {2: 4, 3: 8},
}

# ─── Trailing (общий для всех, в R-множителях) ───
TRAILING_ENABLED = True
TRAILING_TRIGGER_R = 1.3
TRAILING_DISTANCE_R = 0.8

# ─── Partial / BE — динамика по score и символу ───
PARTIAL_ENABLED = True
PARTIAL_CONFIGS = {
    "strong": {  # score >= 95
        "partial_1_r": 0.8, "partial_2_r": 1.5,
        "be_at_r": 0.6,
        "partial_1_pct": 40, "partial_2_pct": 30,
    },
    "default": {
        "partial_1_r": 0.7, "partial_2_r": 1.3,
        "be_at_r": 0.5,
        "partial_1_pct": 50, "partial_2_pct": 25,
    },
    "weak": {  # слабые пары исторически
        "partial_1_r": 0.5, "partial_2_r": 1.1,
        "be_at_r": 0.4,
        "partial_1_pct": 50, "partial_2_pct": 25,
    },
}
WEAK_SYMBOLS = {"SUIUSDT", "SOLUSDT", "APTUSDT", "BCHUSDT"}

# ─── NO_FILL: сколько свечей 5M ждём лимитку ───
LIMIT_FILL_MAX_CANDLES = 12  # 1 час


def get_partial_config(symbol, score):
    if score >= 95:
        return PARTIAL_CONFIGS["strong"]
    if symbol in WEAK_SYMBOLS:
        return PARTIAL_CONFIGS["weak"]
    return PARTIAL_CONFIGS["default"]


# ============================================================
# LOG / UTILS
# ============================================================

def log(msg):
    print(f"[BT] {msg}", flush=True)


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
    """
    PnL с учётом двух partial-тейков.
    Оставшаяся позиция = 100% - p1_pct - p2_pct (если оба сработали).
    """
    p1_w = (p1_pct / 100.0) if p1_done and p1_exit is not None else 0.0
    p2_w = (p2_pct / 100.0) if p2_done and p2_exit is not None else 0.0
    rem_w = max(0.0, 1.0 - p1_w - p2_w)

    total = 0.0
    if p1_w > 0:
        total += pnl_pct(entry, p1_exit, direction) * p1_w
    if p2_w > 0:
        total += pnl_pct(entry, p2_exit, direction) * p2_w
    if rem_w > 0:
        total += pnl_pct(entry, final_exit, direction) * rem_w
    return total


# ============================================================
# COOLDOWN MANAGER (per-symbol, серия SL)
# ============================================================

class CooldownMgr:
    """
    Считает SL-серию ПОДРЯД.
    Правила:
      - TP → серия сбрасывается
      - SL → серия +1, запоминаем время
      - NO_FILL / TIMEOUT → нейтрально
    Пауза = COOLDOWN_V910[symbol] после N-го подряд SL.
    """
    def __init__(self, symbol, verbose=True):
        self.symbol = symbol
        self.consec = 0
        self.last_sl_ts = None
        self.verbose = verbose
        self.cfg = COOLDOWN_V910.get(symbol, COOLDOWN_V910["default"])

    def on_result(self, result_type, ts_sec_ms):
        if result_type == "TP":
            if self.consec > 0 and self.verbose:
                print(f"[CD] {self.symbol}: TP → серия сброшена "
                      f"(было {self.consec} SL)", flush=True)
            self.consec = 0
            self.last_sl_ts = None
        elif result_type == "SL":
            self.consec += 1
            self.last_sl_ts = ts_sec_ms
            if self.verbose:
                print(f"[CD] {self.symbol}: SL #{self.consec} "
                      f"@ {ts_to_str(ts_sec_ms)}", flush=True)

    def can_trade(self, ts_sec_ms):
        if self.consec < 2:
            return True
        if self.last_sl_ts is None:
            return True
        # выбираем максимальный применимый порог
        hours = None
        for thresh in sorted(self.cfg.keys()):
            if self.consec >= thresh:
                hours = self.cfg[thresh]
        if hours is None:
            hours = max(self.cfg.values())
        elapsed_h = (ts_sec_ms - self.last_sl_ts) / 3600000.0
        ok = elapsed_h >= hours
        if not ok and self.verbose:
            print(f"[CD] {self.symbol}: blocked, "
                  f"{ currentelapsed_h:.1f}h < {hours}h (consec={self.consec})_s",
                  flush=True)
        return ok


# ============================================================
l# REASON CLASSIFIER (как было)
# ================================= =========================== sl=

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
    if "blocked" in reason or "заблок" in reason:
        return "other_blocked"

    return f"stage_{stage.lower()}"


# ============================================================
# SIMULATE TRADE v9.10 (dual partial + dynamic BE)
# ============================================================

def simulate_trade_v910(trade, candles_5m, start_ts, max_hours,
                        cfg,
                        use_breakeven=False, use_partial_tp=False,
                        use_trailing=False):
    """
    Симуляция одной сделки с:
      - NO_FILL проверкой (лимитка 12×5M = 1ч)
      - двумя partial уровнями из cfg
      - dynamic BE на cfg['be_at_r']
      - trailing после TRAILING_TRIGGER_R

    Возвращает: (result, exit_price, exit_ts, held, pnl, partial_hit)
    result ∈ {"NO_FILL", "TP", "SL", "TIMEOUT", "ERROR"}
    """
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

    # ─── NO_FILL ───
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

    # ─── trade simulation ───
   _initial
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

        # ─── SL / TP first ───
        if hit_sl and hit_tp:
            # conservative: считаем SL (как в v9.9)
            final = blended_pnl_dual(
                entry, current_sl, direction,
                p1_done, p1_exit, p1_pct,
                p2_done, p2_exit, p2_pct)
            return ("SL", current_sl, c["open_time"], held, final,
                    p1_done or p2_done)

        if hit_sl:
            final = blended_pnl_dual(
                entry, current_sl, direction,
                p1_done, p1_exit, p1_pct,
                p2_done, p2_exit, p2_pct)
            return ("SL", current_sl, c["open_time"], held, final,
                    p1_done or p2_done)

        if hit_tp:
            final = blended_pnl_dual(
                entry, tp, direction,
                p1_done, p1_exit, p1_pct,
                p2_done, p2_exit, p2_pct)
            return ("TP", tp, c["open_time"], held, final,
                    p1_done or p2_done)

        # ─── update best, move_r ───
        if direction == "LONG":
            if high > best_price:
                best_price = high
            move_r = (best_price - entry) / risk
        else:
            if low < best_price:
                best_price = low
            move_r = (entry - best_price) / risk

        # ─── partial 1 ───
        if (use_partial_tp and PARTIAL_ENABLED
                and not p1_done and move_r >= p1_r):
            if direction == "LONG":
                p1_exit = entry + risk * p1_r
            else:
                p1_exit = entry - risk * p1_r
            p1_done = True

        # ─── partial 2 ───
        if (use_partial_tp and PARTIAL_ENABLED
                and p1_done and not p2_done and move_r >= p2_r):
            if direction == "LONG":
                p2_exit = entry + risk * p2_r
            else:
                p2_exit = entry - risk * p2_r
            p2_done = True

        # ─── BE ───
        if use_breakeven and not be_moved and move_r >= be_r:
            if direction == "LONG" and entry > current_sl:
                current_sl = entry
                be_moved = True
            elif direction == "SHORT" and entry < current_sl:
                current_sl = entry
                be_moved = True

        # ─── trailing ───
        if use_trailing and TRAILING_ENABLED and move_r >= TRAILING_TRIGGER_R:
            if direction == "LONG":
                new_sl = best_price - risk * TRAILING_DISTANCE_R
                if new_sl > current_sl:
                    current_sl = new_sl
            else:
                new_sl = best_price + risk * TRAILING_DISTANCE_R
                if new_sl < current_sl:
                    current_sl = new_sl

    # ─── timeout ───
    if last_seen is not None:
        exit_price = last_seen["close"]
        final = blended_pnl_dual(
            entry, exit_price, direction,
            p1_done, p1_exit, p1_pct,
            p2_done, p2_exit, p2_pct)
        return ("TIMEOUT", exit_price, last_seen["open_time"], held, final,
                p1_done or p2_done)

    return ("TIMEOUT", entry, start_ts, 0, 0.0, False)


# ============================================================
# RUN BACKTEST v9.10
# ============================================================

def run_backtest(symbol, max_hours,
                 use_breakeven=False, use_partial_tp=False,
                 use_trailing=False):
    symbol = _normalize_symbol(symbol)
    log(f"Символ: {symbol} "
        f"(BE={use_breakeven} partial={use_partial_tp} "
        f"trail={use_trailing} cooldown=per-symbol)")

    if symbol in BANNED_SYMBOLS:
        log(f"[v9.10] SKIP banned symbol: {symbol}")
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

    candles_d1 = get_klines_history("1d", BT_LOOKBACK_D1, symbol)
    candles_1h = get_klines_history("1h", BT_LOOKBACK_1H, symbol)
    candles_15m = get_klines_history("15m", BT_LOOKBACK_15M, symbol)
    candles_5m = get_klines_history("5m", BT_LOOKBACK_5M, symbol)
    candles_1m = get_klines("1m", BT_LOOKBACK_1M, symbol)

    if not candles_1h:
        log("Нет данных 1H")
        return [], {}

    log(f"Данных: D1={len(candles_d1)} 1H={len(candles_1h)} "
        f"15M={len(candles_15m)} 5M={len(candles_5m)} 1M={len(candles_1m)}")

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
    log(f"Шагов: {total}")

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
            exception_counter["not_enough_candles"] += 1
            continue

        price = c1[-1]["close"] if c1 else c1h[-1]["close"]

        try:
            levels = find_major_liquidity(c1h, price, 12, c15, c5, c1)
        except Exception as exc:
            exception_counter[f"market:{type(exc).__name__}"] += 1
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
                             candles_1m=c1, d1_context=d1_context, fvgs=[],
                             symbol=symbol)
        except Exception as exc:
            exception_counter[f"analyze:{type(exc).__name__}"] += 1
            if exception_counter[f"analyze:{type(exc).__name__}"] == 1:
                print(f"[BT] FIRST EXCEPTION {type(exc).__name__}: {exc}",
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
            near_misses.append({
                "ts": ts_now,
                "direction": result.get("direction", "?"),
                "stage": stage, "score": score,
                "reason_key": reason_key,
                "reason": str(result.get("reason", ""))[:80],
                "trend": result.get("trend_activity", 0),
                "bsl": n_bsl, "ssl": n_ssl,
            })

        if stage != "READY":
            continue

        entry = result.get("entry")
        sl = result.get("sl")
        tp = result.get("tp")
        if entry is None or sl is None or tp is None:
            continue

        trade = {
            "coin": symbol, "direction": result["direction"],
            "entry": float(entry), "sl": float(sl), "tp": float(tp),
            "rr": result.get("rr"), "score": score, "open_ts": ts_now,
        }

        cfg = get_partial_config(symbol, score)

        (res_type, exit_price, exit_ts, held, trade_pnl,
         partial_hit) = simulate_trade_v910(
            trade, candles_5m, ts_now, max_hours,
            cfg=cfg,
            use_breakeven=use_breakeven,
            use_partial_tp=use_partial_tp,
            use_trailing=use_trailing,
        )

        # ─── NO_FILL: лимитка не исполнилась ───
        if res_type == "NO_FILL":
            no_fill_count += 1
            no_fill_by_coin[symbol] += 1
            log(f"[{i:4}] {trade['direction']:5} "
                f"entry={entry:.4f} -> NO_FILL")
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

        # ─── cooldown update ───
        cooldown_mgr.on_result(res_type, exit_ts)

        partial_tag = "P" if partial_hit else " "
        log(f"[{i:4}] {trade['direction']:5} "
            f"entry={entry:.4f} sl={sl:.4f} tp={tp:.4f} "
            f"rr={trade['rr']:.2f} score={score} "
            f"{partial_tag} -> {res_type:7} pnl={trade['pnl']:+.2f}%")

    if cooldown_skips > 0:
        log(f"Cooldown skips: {cooldown_skips}")
    if no_fill_count > 0:
        log(f"NO_FILL: {no_fill_count}")

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


# ============================================================
# REPORT
# ============================================================

def print_report(symbol, trades, diag,
                 use_breakeven=False, use_partial_tp=False,
                 use_trailing=False):
    labels = []
    if use_breakeven:
        labels.append("BE")
    if use_partial_tp:
        labels.append("PARTIAL×2")
    if use_trailing:
        labels.append("TRAIL")
    labels.append("CDv2")
    label = "+".join(labels)

    print()
    print("=" * 70)
    print(f"ОТЧЁТ БЭКТЕСТА v9.10 - {symbol} [{label}]")
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
        print(f"⛔ {symbol} в BANNED_SYMBOLS — пропущен по v9.10.")
        print("=" * 70)
        return

    total = sum(stage_counter.values())
    total_exc = sum(exception_counter.values())

    print()
    print(f"Всего шагов проанализировано: {total}")
    print(f"Пропущено через exception: {total_exc}")
    if cooldown_skips > 0:
        print(f"Пропущено через cooldown: {cooldown_skips}")
    if no_fill_count > 0:
        print(f"NO_FILL (лимитка не исполнилась): {no_fill_count}")
    print()

    if exception_counter:
        print("EXCEPTIONS:")
        for k, v in exception_counter.most_common(10):
            print(f"  {k:40} {v}")
        print()

    print("MARKET:")
    for k in ["both", "only_ssl", "only_bsl", "empty"]:
        v = market_stats.get(k, 0)
        pct = v / total * 100 if total else 0
        print(f"  {k:15} {v:4}  ({pct:.1f}%)")
    print()

    print("РАСПРЕДЕЛЕНИЕ ПО СТАДИЯМ:")
    for stage in ["READY", "15M_CONFIRMED", "SWEPT", "WAIT"]:
        cnt = stage_counter.get(stage, 0)
        pct = cnt / total * 100 if total else 0
        print(f"  {stage:16} {cnt:4}  ({pct:.1f}%)")

    print()
    print("ТОП ПРИЧИН ОСТАНОВКИ:")
    for reason, cnt in reason_counter.most_common(15):
        pct = cnt / total * 100 if total else 0
        print(f"  {reason:26} {cnt:4}  ({pct:.1f}%)")

    print()
    print("=" * 70)
    print("ТОП-15 'ПОЧТИ СРАБОТАВШИХ'")
    print("=" * 70)
    if not near_misses:
        print("Нет сетапов с score >= 50")
    else:
        print(f"{'Дата':<17}{'Напр.':<6}{'Stage':<16}"
              f"{'Score':<6}{'Trend':<7}{'BSL/SSL':<10}{'Reason'}")
        print("-" * 70)
        for nm in near_misses:
            print(f"{ts_to_str(nm['ts']):<17}"
                  f"{nm['direction']:<6}"
                  f"{nm['stage']:<16}"
                  f"{nm['score']:<6}"
                  f"{nm['trend']:<7.2f}"
                  f"{nm['bsl']}/{nm['ssl']:<7}"
                  f"{nm['reason_key']}")

    print()
    print("=" * 70)
    print("СДЕЛКИ (READY + filled)")
    print("=" * 70)

    if not trades:
        print("Нет исполненных сделок.")
        return

    tp = sum(1 for t in trades if t["result"] == "TP")
    sl = sum(1 for t in trades if t["result"] == "SL")
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

    print(f"Всего сделок (filled): {len(trades)}")
    print(f"  TP:      {tp}")
    print(f"  SL:      {sl}")
    print(f"  Timeout: {timeout}")
    if use_partial_tp:
        print(f"  Partial hits: {partial_hits} "
              f"({partial_hits / len(trades) * 100:.1f}%)")
    print(f"Win rate: {win_rate:.1f}%")
    print(f"Total PnL: {total_pnl:+.2f}%")
    print(f"Avg PnL:   {avg_pnl:+.2f}%")
    print(f"Avg win:   {avg_win:+.2f}%")
    print(f"Avg loss:  {avg_loss:+.2f}%")
    print(f"Max DD:    -{max_dd:.2f}%")


# ============================================================
# MULTI BACKTEST
# ============================================================

def run_multi_backtest_with_hours(max_hours, use_breakeven=False,
                                  use_partial_tp=False,
                                  use_trailing=False):
    symbols = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT",
        "XRPUSDT", "LINKUSDT", "DOTUSDT",
        "BCHUSDT", "APTUSDT", "SUIUSDT",
        "INJUSDT",
    ]

    labels = []
    if use_breakeven:
        labels.append("BE")
    if use_partial_tp:
        labels.append("PARTIAL×2")
    if use_trailing:
        labels.append("TRAIL")
    labels.append("CDv2")
    label = "+".join(labels)

    print()
    print("#" * 70)
    print(f"### MULTI BACKTEST v9.10 - {label} - "
          f"{len(symbols)} монет x 90 дней")
    print("#" * 70)
    print(f"### Banned: {sorted(BANNED_SYMBOLS)}")
    print(f"### Cooldown: {COOLDOWN_V910}")
    print("#" * 70)

    all_summary = []
    for sym in symbols:
        try:
            trades, diag = run_backtest(
                sym, max_hours,
                use_breakeven=use_breakeven,
                use_partial_tp=use_partial_tp,
                use_trailing=use_trailing,
            )
            print_report(sym, trades, diag,
                         use_breakeven=use_breakeven,
                         use_partial_tp=use_partial_tp,
                         use_trailing=use_trailing)

            no_fill = diag.get("no_fill_count", 0)
            banned = diag.get("banned", False)

            if banned:
                all_summary.append((sym, 0, 0, 0, 0, 0, 0.0, 0, True))
            elif trades:
                tp = sum(1 for t in trades if t["result"] == "TP")
                sl = sum(1 for t in trades if t["result"] == "SL")
                timeout = sum(1 for t in trades
                              if t["result"] == "TIMEOUT")
                resolved = tp + sl
                wr = tp / resolved * 100 if resolved else 0
                pnl = sum(t["pnl"] for t in trades)
                all_summary.append((sym, len(trades), tp, sl, timeout, wr,
                                    pnl, no_fill, False))
            else:
                all_summary.append((sym, 0, 0, 0, 0, 0, 0.0, no_fill, False))
        except Exception as exc:
            print(f"[BT] {sym} FAILED: {exc}", flush=True)
            traceback.print_exc()
            all_summary.append((sym, 0, 0, 0, 0, 0, 0.0, 0, False))

    print()
    print("=" * 70)
    print(f"СВОДКА - {label} (max_hours={max_hours}, 90 дней)")
    print("=" * 70)
    print(f"{'Символ':<10}{'Filled':<8}{'NoFill':<8}{'TP':<5}{'SL':<5}"
          f"{'TO':<5}{'WR':<8}{'PnL':<10}{'Stat':<8}")
    print("-" * 70)

    total_trades = 0
    total_tp = 0
    total_sl = 0
    total_to = 0
    total_pnl = 0.0
    total_no_fill = 0

    for (sym, cnt, tp, sl, timeout, wr, pnl, no_fill, banned) in all_summary:
        stat = "BANNED" if banned else "OK"
        print(f"{sym:<10}{cnt:<8}{no_fill:<8}{tp:<5}{sl:<5}"
              f"{timeout:<5}{wr:<8.1f}{pnl:+.2f}%  {stat}")
        total_trades += cnt
        total_tp += tp
        total_sl += sl
        total_to += timeout
        total_pnl += pnl
        total_no_fill += no_fill

    print("-" * 70)
    resolved = total_tp + total_sl
    total_wr = total_tp / resolved * 100 if resolved else 0
    print(f"{'ИТОГО':<10}{total_trades:<8}{total_no_fill:<8}"
          f"{total_tp:<5}{total_sl:<5}{total_to:<5}"
          f"{total_wr:<8.1f}{total_pnl:+.2f}%")
    print()
    print(f"Всего filled сделок: {total_trades}")
    print(f"NO_FILL (лимитка не исполнилась): {total_no_fill}")
    total_signals = total_trades + total_no_fill
    if total_signals > 0:
        fill_rate = total_trades / total_signals * 100
        print(f"Fill rate: {fill_rate:.1f}%")
    print(f"  TP: {total_tp}  SL: {total_sl}  Timeout: {total_to}")
    print(f"Win rate: {total_wr:.1f}% (от {resolved} закрытых)")
    print(f"Sum PnL: {total_pnl:+.2f}%")
    print("=" * 70)


def run_multi_backtest():
    run_multi_backtest_with_hours(DEFAULT_MAX_HOURS)


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--max-hours", type=int,
                        default=DEFAULT_MAX_HOURS)
    parser.add_argument("--multi", action="store_true")
    parser.add_argument("--be", action="store_true")
    parser.add_argument("--partial", action="store_true")
    parser.add_argument("--trailing", action="store_true")
    args = parser.parse_args()

    if args.multi:
        run_multi_backtest_with_hours(
            args.max_hours,
            use_breakeven=args.be,
            use_partial_tp=args.partial,
            use_trailing=args.trailing,
        )
    else:
        trades, diag = run_backtest(
            args.symbol, args.max_hours,
            use_breakeven=args.be,
            use_partial_tp=args.partial,
            use_trailing=args.trailing,
        )
        print_report(args.symbol, trades, diag,
                     use_breakeven=args.be,
                     use_partial_tp=args.partial,
                     use_trailing=args.trailing)


if __name__ == "__main__":
    main()