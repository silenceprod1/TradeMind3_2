"""
Простой walk-forward бэктест TradeMind.

Прогоняет текущую логику strategy.py + market.py по историческим
данным одной монеты. Считает win rate, средний PnL, max drawdown.

БЕЗ LOOKAHEAD: на каждом шаге используются только закрытые свечи.
"""

import argparse
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
    else:
        return (entry - exit_price) / entry * 100


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
        return ("TIMEOUT", last_seen["close"], last_seen["open_time"], held)

    return ("TIMEOUT", entry, start_ts, 0)


def run_backtest(symbol, max_hours):
    symbol = _normalize_symbol(symbol)

    log(f"Символ: {symbol}")
    log("Загрузка данных...")

    candles_1h = get_klines("1h", BT_LOOKBACK_1H, symbol)
    candles_15m = get_klines("15m", BT_LOOKBACK_15M, symbol)
    candles_5m = get_klines("5m", BT_LOOKBACK_5M, symbol)
    candles_1m = get_klines("1m", BT_LOOKBACK_1M, symbol)

    if not candles_1h:
        log("Нет данных по 1H.")
        return []

    log(f"Загружено: 1H={len(candles_1h)} 15M={len(candles_15m)} "
        f"5M={len(candles_5m)} 1M={len(candles_1m)}")

    if len(candles_1h) <= WARMUP_1H:
        log(f"Мало данных 1H (нужно > {WARMUP_1H}).")
        return []

    trades = []
    total = len(candles_1h) - WARMUP_1H
    log(f"Шагов бэктеста: {total}")

    for i in range(WARMUP_1H, len(candles_1h)):

        ts_now = candles_1h[i]["open_time"]

        if has_active_position(trades, ts_now):
            continue

        c1h = candles_1h[:i]
        c15 = candles_until(candles_15m, ts_now)
        c5 = candles_until(candles_5m, ts_now)
        c1 = candles_until(candles_1m, ts_now)

        if len(c15) < 60 or len(c5) < 60:
            continue

        if c1:
            price = c1[-1]["close"]
        else:
            price = c1h[-1]["close"]

        try:
            levels = find_major_liquidity(c1h, price, 12, c15, c5, c1)
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

        except Exception:
            continue

        if result.get("stage") != "READY":
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
            "score": result.get("score"),
            "open_ts": ts_now,
            "signal_index": i,
        }

        res_type, exit_price, exit_ts, held = simulate_trade(
            trade, candles_5m, ts_now, max_hours,
        )

        trade["result"] = res_type
        trade["exit_price"] = exit_price
        trade["exit_ts"] = exit_ts
        trade["held_5m"] = held
        trade["pnl"] = pnl_pct(entry, exit_price, trade["direction"])

        trades.append(trade)

        log(
            f"[{i:3}] {trade['direction']:5} "
            f"entry={entry:.4f} sl={sl:.4f} tp={tp:.4f} "
            f"rr={trade['rr']:.2f} score={trade['score']} "
            f"→ {res_type:7} "
            f"pnl={trade['pnl']:+.2f}% ({held} × 5m)"
        )

    return trades


def print_report(symbol, trades):

    print()
    print("=" * 70)
    print(f"ОТЧЁТ БЭКТЕСТА — {symbol}")
    print("=" * 70)

    total = len(trades)

    if total == 0:
        print("За период не было ни одного READY-сигнала.")
        print()
        print("Возможные причины:")
        print("  1. Фильтры слишком строгие")
        print("  2. Мало данных")
        print("  3. Стратегия не подходит инструменту на этом периоде")
        return

    tp = sum(1 for t in trades if t["result"] == "TP")
    sl = sum(1 for t in trades if t["result"] == "SL")
    timeout = sum(1 for t in trades if t["result"] == "TIMEOUT")

    resolved = tp + sl
    win_rate = tp / resolved * 100 if resolved else 0

    total_pnl = sum(t["pnl"] for t in trades)
    avg_pnl = total_pnl / total

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

    longs = [t for t in trades if t["direction"] == "LONG"]
    shorts = [t for t in trades if t["direction"] == "SHORT"]

    print()
    print(f"Всего сделок: {total}")
    print(f"  LONG:  {len(longs)}")
    print(f"  SHORT: {len(shorts)}")
    print()
    print("Результаты:")
    print(f"  TP:      {tp}")
    print(f"  SL:      {sl}")
    print(f"  Timeout: {timeout}")
    print()
    print(f"Win rate:      {win_rate:.1f}%  (от {resolved} закрытых по TP/SL)")
    print(f"Total PnL:     {total_pnl:+.2f}%")
    print(f"Avg PnL:       {avg_pnl:+.2f}%")
    print(f"Avg win:       {avg_win:+.2f}%")
    print(f"Avg loss:      {avg_loss:+.2f}%")
    print(f"Max DD:        -{max_dd:.2f}%")

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        pf = gross_win / gross_loss
        print(f"Profit Factor: {pf:.2f}")

    print()
    print("=" * 70)
    print("ВСЕ СДЕЛКИ")
    print("=" * 70)
    print(f"{'Дата (UTC)':<17}{'Напр.':<6}{'Entry':<12}"
          f"{'RR':<6}{'Score':<6}{'Результат':<10}{'PnL':<10}")
    print("-" * 70)

    for t in trades:
        print(
            f"{ts_to_str(t['open_ts']):<17}"
            f"{t['direction']:<6}"
            f"{t['entry']:<12.4f}"
            f"{t['rr']:<6.2f}"
            f"{t['score']:<6}"
            f"{t['result']:<10}"
            f"{t['pnl']:+.2f}%"
        )


def main():
    parser = argparse.ArgumentParser(description="TradeMind backtest")
    parser.add_argument("--symbol", default="SOLUSDT")
    parser.add_argument("--max-hours", type=int, default=DEFAULT_MAX_HOURS)
    parser.add_argument("--multi", action="store_true")

    args = parser.parse_args()
    symbols = [args.symbol]

    if args.multi:
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT",
                   "BNBUSDT", "XRPUSDT", "DOGEUSDT"]

    all_summary = []

    for sym in symbols:
        print()
        print("-" * 70)
        print(f"> {sym}")
        print("-" * 70)

        trades = run_backtest(sym, args.max_hours)
        print_report(sym, trades)

        if trades:
            tp = sum(1 for t in trades if t["result"] == "TP")
            sl = sum(1 for t in trades if t["result"] == "SL")
            resolved = tp + sl
            wr = tp / resolved * 100 if resolved else 0
            pnl = sum(t["pnl"] for t in trades)
            all_summary.append((sym, len(trades), wr, pnl))

    if args.multi and all_summary:
        print()
        print("-" * 70)
        print("СВОДКА")
        print("-" * 70)
        print(f"{'Символ':<12}{'Сделок':<10}{'WinRate':<12}{'PnL':<12}")
        print("-" * 70)
        for sym, cnt, wr, pnl in all_summary:
            print(f"{sym:<12}{cnt:<10}{wr:<12.1f}{pnl:+.2f}%")


if __name__ == "__main__":
    main()