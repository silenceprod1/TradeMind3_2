# -*- coding: utf-8 -*-
"""
TradeMind backtest v9.20.1 — 40 дней, все монеты из COINS.
Работает через market.py v7.9 (get_market_data_history)
и strategy.py v9.20 (analyze с Anti-FOMO).
"""

import json
import time
from collections import defaultdict
from datetime import datetime

from market import (
    get_market_data_history,
    find_major_liquidity,
    detect_sweep,
    BACKTEST_LOOKBACK_DAYS,
)

from strategy import (
    analyze,
    get_1h_direction,
    ALLOW_SHORT,
    STRATEGY_VERSION,
)


# ------------- CONFIG -------------

COINS = {
    "BTC": "BTCUSDT",
    "XRP": "XRPUSDT",
    "LINK": "LINKUSDT",
    "BCH": "BCHUSDT",
    "APT": "APTUSDT",
    "SUI": "SUIUSDT",
    "INJ": "INJUSDT",
}

MIN_SCORE_MAP = {
    "default": 90,
    "INJUSDT": 88,
    "BCHUSDT": 92,
    "APTUSDT": 90,
}

MIN_RR = 2.0
DAYS = BACKTEST_LOOKBACK_DAYS  # 40

# Partial / BE / Trailing — как в bot.py v9.20.1
PARTIAL_TP_ENABLED = True
PARTIAL_TP_TRIGGER_R = 0.7
PARTIAL_TP_PERCENT = 50

PARTIAL_TP_2_ENABLED = True
PARTIAL_TP_2_TRIGGER_R = 1.3
PARTIAL_TP_2_PERCENT = 25

BREAKEVEN_TRIGGER_R = 0.7
TRAILING_ENABLED = True
TRAILING_TRIGGER_R = 1.3
TRAILING_DISTANCE_R = 0.8

COOLDOWN_AFTER_SL_HOURS = 3

STEP_5M = 3          # шаг прокрутки в 5м-барах (3 = раз в 15 мин)
MAX_HOLD_5M = 144    # макс. удержание — 12 часов (144 * 5м)

OUT_TRADES = "backtest_trades.json"


def get_min_score(sym):
    return MIN_SCORE_MAP.get(sym, MIN_SCORE_MAP["default"])


# ------------- SIMULATION -------------

def simulate_trade(direction, entry, sl, tp, future_5m):
    if not future_5m:
        return "OPEN", float(entry), {"p1": False, "p2": False, "be": False}

    entry = float(entry)
    tp = float(tp)
    sl_cur = float(sl)
    sl0 = float(sl)
    risk = abs(entry - sl0)
    if risk <= 0:
        return "SL", entry, {"p1": False, "p2": False, "be": False}

    p1_done = False
    p2_done = False
    be_done = False
    best = entry

    for c in future_5m:
        hi = float(c["high"])
        lo = float(c["low"])

        if direction == "LONG":
            if lo <= sl_cur:
                if be_done and abs(sl_cur - entry) < 1e-9:
                    res = "BE"
                elif p1_done or p2_done:
                    res = "PARTIAL_TP+SL"
                else:
                    res = "SL"
                return res, sl_cur, {"p1": p1_done, "p2": p2_done, "be": be_done}
            if hi >= tp:
                return "TP", tp, {"p1": p1_done, "p2": p2_done, "be": be_done}

            if hi > best:
                best = hi
            mr = (best - entry) / risk

            if PARTIAL_TP_ENABLED and not p1_done and mr >= PARTIAL_TP_TRIGGER_R:
                p1_done = True
            if (PARTIAL_TP_2_ENABLED and not p2_done and p1_done
                    and mr >= PARTIAL_TP_2_TRIGGER_R):
                p2_done = True
            if (not be_done and p1_done and mr >= BREAKEVEN_TRIGGER_R
                    and entry > sl_cur):
                sl_cur = entry
                be_done = True
            if TRAILING_ENABLED and mr >= TRAILING_TRIGGER_R:
                ns = best - risk * TRAILING_DISTANCE_R
                if ns > sl_cur:
                    sl_cur = ns

        else:  # SHORT
            if hi >= sl_cur:
                if be_done and abs(sl_cur - entry) < 1e-9:
                    res = "BE"
                elif p1_done or p2_done:
                    res = "PARTIAL_TP+SL"
                else:
                    res = "SL"
                return res, sl_cur, {"p1": p1_done, "p2": p2_done, "be": be_done}
            if lo <= tp:
                return "TP", tp, {"p1": p1_done, "p2": p2_done, "be": be_done}

            if lo < best:
                best = lo
            mr = (entry - best) / risk

            if PARTIAL_TP_ENABLED and not p1_done and mr >= PARTIAL_TP_TRIGGER_R:
                p1_done = True
            if (PARTIAL_TP_2_ENABLED and not p2_done and p1_done
                    and mr >= PARTIAL_TP_2_TRIGGER_R):
                p2_done = True
            if (not be_done and p1_done and mr >= BREAKEVEN_TRIGGER_R
                    and entry < sl_cur):
                sl_cur = entry
                be_done = True
            if TRAILING_ENABLED and mr >= TRAILING_TRIGGER_R:
                ns = best + risk * TRAILING_DISTANCE_R
                if ns < sl_cur:
                    sl_cur = ns

    return "OPEN", entry, {"p1": p1_done, "p2": p2_done, "be": be_done}


# ------------- BACKTEST LOOP -------------

def run_coin(coin, symbol):
    print(f"\n[{coin}] loading {DAYS} days...")
    snap = get_market_data_history(symbol, days=DAYS)
    c1h_full = snap["candles_1h"]
    c15_full = snap["candles_15m"]
    c5_full = snap["candles_5m"]

    print(f"[{coin}] bars: 1h={len(c1h_full)} 15m={len(c15_full)} 5m={len(c5_full)}")

    if len(c1h_full) < 50 or len(c15_full) < 200 or len(c5_full) < 400:
        print(f"[{coin}] not enough data, skip")
        return []

    min_score = get_min_score(symbol)
    trades = []
    last_sl_time = 0
    n5 = len(c5_full)

    # указатели для быстрого среза 1h/15m
    idx_1h = 0
    idx_15 = 0

    i5 = 200
    while i5 < n5 - MAX_HOLD_5M:
        cur = c5_full[i5]
        cur_time = cur["open_time"]

        # двигаем указатели 1h / 15m
        while idx_1h + 1 < len(c1h_full) and c1h_full[idx_1h + 1]["open_time"] <= cur_time:
            idx_1h += 1
        while idx_15 + 1 < len(c15_full) and c15_full[idx_15 + 1]["open_time"] <= cur_time:
            idx_15 += 1

        if idx_1h < 30 or idx_15 < 60:
            i5 += STEP_5M
            continue

        # cooldown
        if last_sl_time > 0:
            hours = (cur_time - last_sl_time) / 3600000.0
            if hours < COOLDOWN_AFTER_SL_HOURS:
                i5 += STEP_5M
                continue

        c1h = c1h_full[:idx_1h + 1]
        c15 = c15_full[:idx_15 + 1]
        c5 = c5_full[:i5 + 1]
        price = float(cur["close"])

        d = get_1h_direction(c1h)
        if d == "NEUTRAL":
            i5 += STEP_5M
            continue

        levels = find_major_liquidity(c1h, price, 12, c15, c5, [])
        if not levels:
            i5 += STEP_5M
            continue

        sweep = detect_sweep(c1h, price, d, levels)

        try:
            result = analyze(
                c1h, c15, c5, price, levels, sweep,
                candles_1m=None,
                d1_context=None,
                fvgs=[],
                symbol=symbol,
            )
        except Exception as e:
            print(f"[{coin}] analyze err @ {cur_time}: {e}")
            i5 += STEP_5M
            continue

        if result.get("stage") != "READY":
            i5 += STEP_5M
            continue

        score = int(result.get("score", 0))
        if score < min_score:
            i5 += STEP_5M
            continue

        rr = result.get("rr")
        if rr is None or float(rr) < MIN_RR:
            i5 += STEP_5M
            continue

        direction = result.get("direction")
        if direction == "SHORT" and not ALLOW_SHORT:
            i5 += STEP_5M
            continue

        entry = result.get("entry")
        sl = result.get("sl")
        tp = result.get("tp")
        if entry is None or sl is None or tp is None:
            i5 += STEP_5M
            continue

        future = c5_full[i5 + 1:i5 + 1 + MAX_HOLD_5M]
        res, exit_p, meta = simulate_trade(direction, entry, sl, tp, future)

        entry_f = float(entry)
        risk = abs(entry_f - float(sl))
        r_mult = 0.0
        if risk > 0:
            if direction == "LONG":
                r_mult = (float(exit_p) - entry_f) / risk
            else:
                r_mult = (entry_f - float(exit_p)) / risk

        trades.append({
            "coin": coin,
            "symbol": symbol,
            "time": datetime.utcfromtimestamp(cur_time / 1000).isoformat() + "Z",
            "direction": direction,
            "score": score,
            "entry": entry_f,
            "sl": float(sl),
            "tp": float(tp),
            "exit": float(exit_p),
            "result": res,
            "r": round(r_mult, 3),
            "p1": meta.get("p1", False),
            "p2": meta.get("p2", False),
            "be": meta.get("be", False),
        })

        if res == "SL":
            last_sl_time = cur_time

        i5 += 1  # после сделки сдвигаемся на 1 бар, чтобы не дублировать

    print(f"[{coin}] trades: {len(trades)}")
    return trades


# ------------- REPORT -------------

def summarize(trades, label):
    n = len(trades)
    if n == 0:
        print(f"\n{label}: no trades")
        return
    tp = sum(1 for t in trades if t["result"] == "TP")
    sl = sum(1 for t in trades if t["result"] == "SL")
    be = sum(1 for t in trades if t["result"] == "BE")
    ptsl = sum(1 for t in trades if t["result"] == "PARTIAL_TP+SL")
    op = sum(1 for t in trades if t["result"] == "OPEN")
    closed = n - op
    wins = tp + be + ptsl
    wr = wins / closed * 100 if closed else 0

    rs = [t["r"] for t in trades if t["result"] != "OPEN"]
    avg_r = sum(rs) / len(rs) if rs else 0
    total_r = sum(rs)
    best = max(rs) if rs else 0
    worst = min(rs) if rs else 0

    eq = 0.0
    peak = 0.0
    mdd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)

    print(f"\n{label}")
    print(f"  Trades: {n} (closed {closed}, open {op})")
    print(f"  TP: {tp}  BE: {be}  Partial+SL: {ptsl}  SL: {sl}")
    print(f"  WR: {wr:.1f}%")
    print(f"  Avg R: {avg_r:+.2f}")
    print(f"  Total R: {total_r:+.2f}")
    print(f"  Best: {best:+.2f}  Worst: {worst:+.2f}")
    print(f"  MDD (R): {mdd:.2f}")


def main():
    print(f"TradeMind backtest — strategy {STRATEGY_VERSION}")
    print(f"Period: {DAYS} days")
    print(f"Coins: {list(COINS.keys())}")
    print("=" * 70)

    all_trades = []
    per_coin = defaultdict(list)
    t0 = time.time()

    for coin, symbol in COINS.items():
        try:
            trades = run_coin(coin, symbol)
        except Exception as e:
            print(f"[{coin}] FAILED: {e}")
            continue
        all_trades.extend(trades)
        per_coin[coin].extend(trades)

    print("\n" + "=" * 70)
    print(f"BACKTEST REPORT (elapsed {time.time()-t0:.1f}s)")
    print("=" * 70)

    summarize(all_trades, "ALL COINS")
    for coin in COINS:
        summarize(per_coin.get(coin, []), coin)

    with open(OUT_TRADES, "w", encoding="utf-8") as f:
        json.dump(all_trades, f, ensure_ascii=False, indent=2)
    print(f"\nTrades saved: {OUT_TRADES}")


if __name__ == "__main__":
    main()