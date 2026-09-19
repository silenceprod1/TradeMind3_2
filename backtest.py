"""
Диагностический бэктест v9.1.

Изменения v9.1 vs v9:
- BE по R (не по %): срабатывает на +1R
- Partial TP 50% на +1R
- Trailing по R: включается на +1.5R, дистанция 1R
- CLI флаги --be / --partial / --trailing для A/B теста
- PnL считается с учётом partial exit
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


BT_LOOKBACK_D1 = 90
BT_LOOKBACK_1H = 1000
BT_LOOKBACK_15M = 4000
BT_LOOKBACK_5M = 12000
BT_LOOKBACK_1M = 200

WARMUP_1H = 150
DEFAULT_MAX_HOURS = 24

BREAKEVEN_TRIGGER_R = 1.0
PARTIAL_TP_ENABLED = True
PARTIAL_TP_TRIGGER_R = 1.0
PARTIAL_TP_PERCENT = 50
TRAILING_TRIGGER_R = 1.5
TRAILING_DISTANCE_R = 1.0


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


def blended_pnl(entry, final_exit, direction,
                partial_done, partial_exit, partial_pct):
    if not partial_done or partial_exit is None:
        return pnl_pct(entry, final_exit, direction)
    w = partial_pct / 100.0
    p1 = pnl_pct(entry, partial_exit, direction)
    p2 = pnl_pct(entry, final_exit, direction)
    return p1 * w + p2 * (1 - w)


def classify_reason(result, market_info):
    stage = result.get("stage", "WAIT")
    reason = str(result.get("reason", "")).lower()

    if stage == "READY":
        return "READY"

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


def simulate_trade(trade, candles_5m, start_ts, max_hours,
                   use_breakeven=False, use_partial_tp=False,
                   use_trailing=False):
    direction = trade["direction"]
    entry = float(trade["entry"])
    sl_initial = float(trade["sl"])
    tp = float(trade["tp"])

    risk = abs(entry - sl_initial)
    if risk <= 0:
        return ("ERROR", entry, start_ts, 0, 0.0, False)

    current_sl = sl_initial
    best_price = entry
    partial_done = False
    partial_exit = None

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
            hit_sl = low <= current_sl
        else:
            hit_tp = low <= tp
            hit_sl = high >= current_sl

        if hit_sl and hit_tp:
            final_pnl = blended_pnl(entry, current_sl, direction,
                                    partial_done, partial_exit,
                                    PARTIAL_TP_PERCENT)
            return ("SL", current_sl, c["open_time"], held,
                    final_pnl, partial_done)
        if hit_sl:
            final_pnl = blended_pnl(entry, current_sl, direction,
                                    partial_done, partial_exit,
                                    PARTIAL_TP_PERCENT)
            return ("SL", current_sl, c["open_time"], held,
                    final_pnl, partial_done)
        if hit_tp:
            final_pnl = blended_pnl(entry, tp, direction,
                                    partial_done, partial_exit,
                                    PARTIAL_TP_PERCENT)
            return ("TP", tp, c["open_time"], held,
                    final_pnl, partial_done)

        if direction == "LONG":
            if high > best_price:
                best_price = high
            move_r = (best_price - entry) / risk
        else:
            if low < best_price:
                best_price = low
            move_r = (entry - best_price) / risk

        if (use_partial_tp and PARTIAL_TP_ENABLED
                and not partial_done
                and move_r >= PARTIAL_TP_TRIGGER_R):
            if direction == "LONG":
                partial_exit = entry + risk * PARTIAL_TP_TRIGGER_R
            else:
                partial_exit = entry - risk * PARTIAL_TP_TRIGGER_R
            partial_done = True

        if use_breakeven and move_r >= BREAKEVEN_TRIGGER_R:
            if direction == "LONG" and entry > current_sl:
                current_sl = entry
            elif direction == "SHORT" and entry < current_sl:
                current_sl = entry

        if use_trailing and move_r >= TRAILING_TRIGGER_R:
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
        final_pnl = blended_pnl(entry, exit_price, direction,
                                partial_done, partial_exit,
                                PARTIAL_TP_PERCENT)
        return ("TIMEOUT", exit_price, last_seen["open_time"], held,
                final_pnl, partial_done)

    return ("TIMEOUT", entry, start_ts, 0, 0.0, False)


def run_backtest(symbol, max_hours,
                 use_breakeven=False, use_partial_tp=False,
                 use_trailing=False):
    symbol = _normalize_symbol(symbol)
    log(f"Символ: {symbol} "
        f"(BE={use_breakeven} partial={use_partial_tp} trail={use_trailing})")

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
                             candles_1m=c1, d1_context=d1_context, fvgs=[])
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
                "ts": ts_now, "direction": result.get("direction", "?"),
                "stage": stage, "score": score, "reason_key": reason_key,
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

        (res_type, exit_price, exit_ts, held, trade_pnl,
         partial_hit) = simulate_trade(
            trade, candles_5m, ts_now, max_hours,
            use_breakeven=use_breakeven,
            use_partial_tp=use_partial_tp,
            use_trailing=use_trailing,
        )

        trade["result"] = res_type
        trade["exit_price"] = exit_price
        trade["exit_ts"] = exit_ts
        trade["held_5m"] = held
        trade["pnl"] = trade_pnl
        trade["partial_hit"] = partial_hit

        trades.append(trade)

        partial_tag = "💰" if partial_hit else "  "
        log(f"[{i:4}] {trade['direction']:5} "
            f"entry={entry:.4f} sl={sl:.4f} tp={tp:.4f} "
            f"rr={trade['rr']:.2f} score={score} "
            f"{partial_tag} -> {res_type:7} pnl={trade['pnl']:+.2f}%")

    near_misses.sort(key=lambda x: x["score"], reverse=True)
    diag = {
        "stage_counter": stage_counter, "reason_counter": reason_counter,
        "exception_counter": exception_counter, "market_stats": market_stats,
        "near_misses": near_misses[:15],
    }
    return trades, diag


def print_report(symbol, trades, diag, use_breakeven=False,
                 use_partial_tp=False, use_trailing=False):
    labels = []
    if use_breakeven:
        labels.append("BE")
    if use_partial_tp:
        labels.append("PARTIAL")
    if use_trailing:
        labels.append("TRAIL")
    label = "+".join(labels) if labels else "BASIC"

    print()
    print("=" * 70)
    print(f"ОТЧЁТ БЭКТЕСТА v9.1 - {symbol} [{label}]")
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
    print("СДЕЛКИ (READY)")
    print("=" * 70)

    if not trades:
        print("Нет READY-сигналов.")
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

    print(f"Всего сделок: {len(trades)}")
    print(f"  TP:      {tp}")
    print(f"  SL:      {sl}")
    print(f"  Timeout: {timeout}")
    if use_partial_tp:
        print(f"  Partial hits: {partial_hits} "
              f"({partial_hits/len(trades)*100:.1f}%)")
    print(f"Win rate: {win_rate:.1f}%")
    print(f"Total PnL: {total_pnl:+.2f}%")
    print(f"Avg PnL:   {avg_pnl:+.2f}%")
    print(f"Avg win:   {avg_win:+.2f}%")
    print(f"Avg loss:  {avg_loss:+.2f}%")
    print(f"Max DD:    -{max_dd:.2f}%")


def run_multi_backtest_with_hours(max_hours, use_breakeven=False,
                                  use_partial_tp=False, use_trailing=False):
    symbols = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT",
        "ADAUSDT", "AVAXUSDT", "LINKUSDT", "NEARUSDT", "APTUSDT",
    ]

    labels = []
    if use_breakeven:
        labels.append("BE")
    if use_partial_tp:
        labels.append("PARTIAL")
    if use_trailing:
        labels.append("TRAIL")
    label = "+".join(labels) if labels else "BASIC"

    print()
    print("#" * 70)
    print(f"### MULTI BACKTEST v9.1 - {label} - "
          f"{len(symbols)} монет x 40 дней")
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

            if trades:
                tp = sum(1 for t in trades if t["result"] == "TP")
                sl = sum(1 for t in trades if t["result"] == "SL")
                timeout = sum(1 for t in trades if t["result"] == "TIMEOUT")
                resolved = tp + sl
                wr = tp / resolved * 100 if resolved else 0
                pnl = sum(t["pnl"] for t in trades)
                all_summary.append((sym, len(trades), tp, sl, timeout, wr, pnl))
            else:
                all_summary.append((sym, 0, 0, 0, 0, 0, 0.0))
        except Exception as exc:
            print(f"[BT] {sym} FAILED: {exc}", flush=True)
            all_summary.append((sym, 0, 0, 0, 0, 0, 0.0))

    print()
    print("=" * 70)
    print(f"СВОДКА - {label} (max_hours={max_hours}, 40 дней)")
    print("=" * 70)
    print(f"{'Символ':<10}{'Сделок':<8}{'TP':<5}{'SL':<5}"
          f"{'TO':<5}{'WinRate':<10}{'PnL':<10}")
    print("-" * 70)

    total_trades = 0
    total_tp = 0
    total_sl = 0
    total_to = 0
    total_pnl = 0.0

    for sym, cnt, tp, sl, timeout, wr, pnl in all_summary:
        print(f"{sym:<10}{cnt:<8}{tp:<5}{sl:<5}"
              f"{timeout:<5}{wr:<10.1f}{pnl:+.2f}%")
        total_trades += cnt
        total_tp += tp
        total_sl += sl
        total_to += timeout
        total_pnl += pnl

    print("-" * 70)
    resolved = total_tp + total_sl
    total_wr = total_tp / resolved * 100 if resolved else 0
    print(f"{'ИТОГО':<10}{total_trades:<8}{total_tp:<5}{total_sl:<5}"
          f"{total_to:<5}{total_wr:<10.1f}{total_pnl:+.2f}%")
    print()
    print(f"Всего сделок: {total_trades}")
    print(f"  TP: {total_tp}  SL: {total_sl}  Timeout: {total_to}")
    print(f"Win rate: {total_wr:.1f}% (от {resolved} закрытых)")
    print(f"Sum PnL: {total_pnl:+.2f}%")
    print("=" * 70)


def run_multi_backtest():
    run_multi_backtest_with_hours(DEFAULT_MAX_HOURS)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="SOLUSDT")
    parser.add_argument("--max-hours", type=int, default=DEFAULT_MAX_HOURS)
    parser.add_argument("--multi", action="store_true")
    parser.add_argument("--be", action="store_true",
                        help="включить breakeven по R")
    parser.add_argument("--partial", action="store_true",
                        help="включить partial TP 50 процентов на +1R")
    parser.add_argument("--trailing", action="store_true",
                        help="включить trailing по R")
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