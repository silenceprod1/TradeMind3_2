"""
Диагностический бэктест v3.
Логирует exceptions, разбивает no_major_level по типам, фиксит классификатор.
"""

import argparse
import traceback
from collections import Counter
from datetime import datetime, timezone

from market import (
    get_klines,
    _normalize_symbol,
    find_major_liquidity,
    detect_sweep,
)
from strategy import analyze, get_1h_direction


BT_LOOKBACK_1H = 500
BT_LOOKBACK_15M = 500
BT_LOOKBACK_5M = 1000
BT_LOOKBACK_1M = 500

WARMUP_1H = 150
DEFAULT_MAX_HOURS = 12


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


def classify_reason(result, market_info):
    """
    Возвращает ключ причины, почему сетап не READY.
    Учитывает market_info — сколько BSL/SSL было найдено.
    """
    stage = result.get("stage", "WAIT")
    reason = str(result.get("reason", "")).lower()

    if stage == "READY":
        return "READY"

    # Разбиваем no_major_level по типам
    if "нет актуальной major" in reason or "нет актуальной major ssl" in reason or "нет актуальной major bsl" in reason:
        n_bsl = market_info.get("bsl_count", 0)
        n_ssl = market_info.get("ssl_count", 0)

        if n_bsl == 0 and n_ssl == 0:
            return "no_levels_both"
        if n_bsl == 0:
            return "no_levels_bsl"
        if n_ssl == 0:
            return "no_levels_ssl"
        return "no_levels_matching"

    # Сначала проверяем продвинутые стадии — они важнее
    if "15m" in reason and "ждём" in reason:
        return "no_15m_conf"
    if "ilm" in reason:
        return "no_5m_ilm"
    if "rr" in reason and "<" in reason:
        return "rr_too_low"
    if "trend" in reason and "блок" in reason:
        return "trend_blocked"
    if "recovery" in reason and "блок" in reason:
        return "v_recovery_blocked"
    if "score" in reason and "блок" in reason:
        return "score_too_low"

    # Потом уже sweep
    if "ждём" in reason and "sweep" in reason:
        return "no_sweep"

    if "blocked" in reason or "заблок" in reason:
        return "other_blocked"

    return f"stage_{stage.lower()}"


def simulate_trade(trade, candles_5m, start_ts, max_hours):
    direction = trade["direction"]
    sl = trade["sl"]
    tp = trade["tp"]
    entry = trade["entry"]

    deadline = start_ts + max_hours * 3600 * 1000
    last_seen = None
    held = 0

    for c in candles_5m:
        if c["open_time"] < start_ts:
            continue
        if c["open_time"] > deadline:
            break

        held += 1
        last_seen = c

        high = c["high"]
        low = c["low"]

        if direction == "LONG":
            hit_tp = high >= tp
            hit_sl = low <= sl
        else:
            hit_tp = low <= tp
            hit_sl = high >= sl

        if hit_tp and hit_sl:
            return ("SL", sl, c["open_time"], held)
        if hit_tp:
            return ("TP", tp, c["open_time"], held)
        if hit_sl:
            return ("SL", sl, c["open_time"], held)

    if last_seen is not None:
        return ("TIMEOUT", last_seen["close"],
                last_seen["open_time"], held)

    return ("TIMEOUT", entry, start_ts, 0)


def run_backtest(symbol, max_hours):
    symbol = _normalize_symbol(symbol)
    log(f"Символ: {symbol}")

    candles_1h = get_klines("1h", BT_LOOKBACK_1H, symbol)
    candles_15m = get_klines("15m", BT_LOOKBACK_15M, symbol)
    candles_5m = get_klines("5m", BT_LOOKBACK_5M, symbol)
    candles_1m = get_klines("1m", BT_LOOKBACK_1M, symbol)

    if not candles_1h:
        log("Нет данных 1H")
        return [], {}

    log(f"Данных: 1H={len(candles_1h)} 15M={len(candles_15m)} "
        f"5M={len(candles_5m)} 1M={len(candles_1m)}")

    if len(candles_1h) <= WARMUP_1H:
        return [], {}

    trades = []
    stage_counter = Counter()
    reason_counter = Counter()
    exception_counter = Counter()
    market_stats = Counter()

    near_misses = []

    total = len(candles_1h) - WARMUP_1H
    log(f"Шагов: {total}")

    for i in range(WARMUP_1H, len(candles_1h)):

        ts_now = candles_1h[i]["open_time"]

        if has_active_position(trades, ts_now):
            continue

        c1h = candles_1h[:i]
        c15 = candles_until(candles_15m, ts_now)
        c5 = candles_until(candles_5m, ts_now)
        c1 = candles_until(candles_1m, ts_now)

        if len(c15) < 60 or len(c5) < 60:
            exception_counter["not_enough_candles"] += 1
            continue

        price = c1[-1]["close"] if c1 else c1h[-1]["close"]

        # --- market ---
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

        # --- strategy ---
        try:
            direction = get_1h_direction(c1h)

            sweep = None
            if direction != "NEUTRAL":
                sweep = detect_sweep(c1h, price, direction, levels)

            result = analyze(
                c1h, c15, c5, price,
                levels, sweep,
                candles_1m=c1,
                d1_context=None,
                fvgs=[],
            )

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
                "stage": stage,
                "score": score,
                "reason_key": reason_key,
                "reason": str(result.get("reason", ""))[:80],
                "trend": result.get("trend_activity", 0),
                "bsl": n_bsl,
                "ssl": n_ssl,
            })

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

        res_type, exit_price, exit_ts, held = simulate_trade(
            trade, candles_5m, ts_now, max_hours)

        trade["result"] = res_type
        trade["exit_price"] = exit_price
        trade["exit_ts"] = exit_ts
        trade["held_5m"] = held
        trade["pnl"] = pnl_pct(entry, exit_price, trade["direction"])

        trades.append(trade)

        log(
            f"[{i:3}] {trade['direction']:5} "
            f"entry={entry:.4f} sl={sl:.4f} tp={tp:.4f} "
            f"rr={trade['rr']:.2f} score={score} "
            f"→ {res_type:7} pnl={trade['pnl']:+.2f}%"
        )

    near_misses.sort(key=lambda x: x["score"], reverse=True)

    diag = {
        "stage_counter": stage_counter,
        "reason_counter": reason_counter,
        "exception_counter": exception_counter,
        "market_stats": market_stats,
        "near_misses": near_misses[:15],
    }

    return trades, diag


def print_report(symbol, trades, diag):

    print()
    print("=" * 70)
    print(f"ОТЧЁТ БЭКТЕСТА v3 — {symbol}")
    print("=" * 70)

    stage_counter = diag.get("stage_counter", Counter())
    reason_counter = diag.get("reason_counter", Counter())
    exception_counter = diag.get("exception_counter", Counter())
    market_stats = diag.get("market_stats", Counter())
    near_misses = diag.get("near_misses", [])

    total = sum(stage_counter.values())
    total_exc = sum(exception_counter.values())

    print()
    print(f"Всего шагов проанализировано: {total}")
    print(f"Пропущено через exception: {total_exc}")
    print()

    if exception_counter:
        print("EXCEPTIONS (что падало):")
        for k, v in exception_counter.most_common(10):
            print(f"  {k:40} {v}")
        print()

    print("MARKET: сколько уровней находил find_major_liquidity:")
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
            print(
                f"{ts_to_str(nm['ts']):<17}"
                f"{nm['direction']:<6}"
                f"{nm['stage']:<16}"
                f"{nm['score']:<6}"
                f"{nm['trend']:<7.2f}"
                f"{nm['bsl']}/{nm['ssl']:<7}"
                f"{nm['reason_key']}"
            )

    print()
    print("=" * 70)
    print("СДЕЛКИ (READY)")
    print("=" * 70)

    if not trades:
        print("Нет READY-сигналов.")
        return

    tp = sum(1 for t in trades if t["result"] == "TP")
    sl = sum(1 for t in trades if t["result"] == "SL")
    timeout = sum(1 for t in trades if t["result"] == "TIMEOUT")

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

    print(f"Всего сделок: {len(trades)}")
    print(f"  TP:      {tp}")
    print(f"  SL:      {sl}")
    print(f"  Timeout: {timeout}")
    print(f"Win rate: {win_rate:.1f}%")
    print(f"Total PnL: {total_pnl:+.2f}%")
    print(f"Avg PnL:   {avg_pnl:+.2f}%")
    print(f"Avg win:   {avg_win:+.2f}%")
    print(f"Avg loss:  {avg_loss:+.2f}%")
    print(f"Max DD:    -{max_dd:.2f}%")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="SOLUSDT")
    parser.add_argument("--max-hours", type=int, default=DEFAULT_MAX_HOURS)
    args = parser.parse_args()

    trades, diag = run_backtest(args.symbol, args.max_hours)
    print_report(args.symbol, trades, diag)


if __name__ == "__main__":
    main()