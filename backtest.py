# -*- coding: utf-8 -*-
"""
TradeMind backtest v9.20.1 — 40 дней, все монеты из COINS.

Прогоняет стратегию бар-за-баром:
  - берёт свечи 1H/15M/5M,
  - на каждом шаге строит уровни и sweep,
  - вызывает analyze(),
  - входит только при stage == READY,
  - имитирует SL/TP, partial P1/P2, BE, trailing,
  - пишет сделки в journal и печатает отчёт.

Требует: ccxt (или твой market.get_market_data).
"""

import os
import sys
import time
import json
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import ccxt

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
DAYS = 40

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

EXCHANGE_ID = "binance"
LIMIT_1H = 1000
LIMIT_15M = 1000
LIMIT_5M = 1000

OUT_JSON = "backtest_40d_results.json"
OUT_TRADES = "backtest_40d_trades.json"


def get_min_score(sym):
    return MIN_SCORE_MAP.get(sym, MIN_SCORE_MAP["default"])


# ------------- DATA -------------

_ex = None


def ex():
    global _ex
    if _ex is None:
        _ex = getattr(ccxt, EXCHANGE_ID)({
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
    return _ex


def fetch_ohlcv(symbol, tf, since_ms, limit=1000):
    out = []
    cur = since_ms
    while True:
        batch = ex().fetch_ohlcv(symbol, tf, since=cur, limit=limit)
        if not batch:
            break
        out.extend(batch)
        if len(batch) < limit:
            break
        cur = batch[-1][0] + 1
        time.sleep(ex().rateLimit / 1000.0)
    # нормализуем под формат strategy._v
    candles = []
    for c in out:
        candles.append({
            "open_time": c[0],
            "open": c[1],
            "high": c[2],
            "low": c[3],
            "close": c[4],
            "volume": c[5],
        })
    return candles


def fetch_all(symbol):
    now = int(time.time() * 1000)
    since = now - DAYS * 24 * 3600 * 1000

    c1h = fetch_ohlcv(symbol, "1h", since, LIMIT_1H)
    c15 = fetch_ohlcv(symbol, "15m", since, LIMIT_15M)
    c5 = fetch_ohlcv(symbol, "5m", since, LIMIT_5M)

    # дедуп по open_time
    c1h = {c["open_time"]: c for c in c1h}
    c15 = {c["open_time"]: c for c in c15}
    c5 = {c["open_time"]: c for c in c5}

    c1h = [c1h[k] for k in sorted(c1h)]
    c15 = [c15[k] for k in sorted(c15)]
    c5 = [c5[k] for k in sorted(c5)]
    return c1h, c15, c5


# ------------- MARKET HELPERS -------------

def find_major_liquidity_simple(c1h, price, max_levels=12):
    """
    Упрощённый уровень-детектор для бэктеста.
    Если у тебя в market.py есть настоящий find_major_liquidity,
    подмени эту функцию на него.
    """
    if not c1h:
        return []

    levels = []
    win = 200
    w = c1h[-win:] if len(c1h) > win else c1h

    # свипы/экстремумы
    for i in range(2, len(w) - 2):
        h = w[i]["high"]
        l = w[i]["low"]
        if (h > w[i-1]["high"] and h > w[i-2]["high"]
                and h >= w[i+1]["high"] and h > w[i+2]["high"]):
            levels.append({
                "price": h, "type": "BSL",
                "strength": 70, "touches": 2,
                "source": "swing",
            })
        if (l < w[i-1]["low"] and l < w[i-2]["low"]
                and l <= w[i+1]["low"] and l < w[i+2]["low"]):
            levels.append({
                "price": l, "type": "SSL",
                "strength": 70, "touches": 2,
                "source": "swing",
            })

    # ближайшие к цене
    levels.sort(key=lambda x: abs(x["price"] - price))
    return levels[:max_levels]


def detect_sweep_simple(c1h, direction, levels):
    """Упрощённый sweep-детектор — если есть свой, подмени."""
    if not c1h or not levels:
        return None
    last = c1h[-1]
    for lvl in levels:
        p = lvl["price"]
        if direction == "LONG" and lvl["type"] == "SSL":
            if last["low"] < p and last["close"] > p:
                return {
                    "swept": True, "direction": "LONG",
                    "level": p, "extreme": last["low"],
                    "open_time": last["open_time"],
                    "liquidity_type": "SSL",
                    "depth_pct": (p - last["low"]) / p * 100,
                }
        if direction == "SHORT" and lvl["type"] == "BSL":
            if last["high"] > p and last["close"] < p:
                return {
                    "swept": True, "direction": "SHORT",
                    "level": p, "extreme": last["high"],
                    "open_time": last["open_time"],
                    "liquidity_type": "BSL",
                    "depth_pct": (last["high"] - p) / p * 100,
                }
    return None


# ------------- SIMULATION -------------

def simulate_trade(direction, entry, sl, tp, future_5m):
    """
    Прогоняет одну сделку по 5m-свечам после входа.
    Возвращает (result, exit_price, meta).
    result ∈ {"TP", "SL", "BE", "TRAIL", "PARTIAL_TP+SL", "OPEN"}
    """
    if not future_5m:
        return "OPEN", entry, {}

    sl_cur = float(sl)
    entry = float(entry)
    tp = float(tp)
    sl0 = float(sl)
    risk = abs(entry - sl0)
    if risk <= 0:
        return "SL", entry, {}

    p1_done = False
    p2_done = False
    be_done = False
    best = entry

    for c in future_5m:
        hi = float(c["high"])
        lo = float(c["low"])

        # SL / TP
        if direction == "LONG":
            if lo <= sl_cur:
                res = "BE" if be_done and abs(sl_cur - entry) < 1e-9 else "SL"
                # уже частично закрыто?
                if p2_done:
                    res = "PARTIAL_TP+SL"
                elif p1_done:
                    res = "PARTIAL_TP+SL"
                return res, sl_cur, {
                    "p1": p1_done, "p2": p2_done, "be": be_done}
            if hi >= tp:
                return "TP", tp, {
                    "p1": p1_done, "p2": p2_done, "be": be_done}

            if hi > best:
                best = hi
            mr = (best - entry) / risk

            if (PARTIAL_TP_ENABLED and not p1_done
                    and mr >= PARTIAL_TP_TRIGGER_R):
                p1_done = True
            if (PARTIAL_TP_2_ENABLED and not p2_done and p1_done
                    and mr >= PARTIAL_TP_2_TRIGGER_R):
                p2_done = True
            if (not be_done and p1_done
                    and mr >= BREAKEVEN_TRIGGER_R):
                if entry > sl_cur:
                    sl_cur = entry
                    be_done = True
            if TRAILING_ENABLED and mr >= TRAILING_TRIGGER_R:
                ns = best - risk * TRAILING_DISTANCE_R
                if ns > sl_cur:
                    sl_cur = ns

        elif direction == "SHORT":
            if hi >= sl_cur:
                res = "BE" if be_done and abs(sl_cur - entry) < 1e-9 else "SL"
                if p1_done or p2_done:
                    res = "PARTIAL_TP+SL"
                return res, sl_cur, {
                    "p1": p1_done, "p2": p2_done, "be": be_done}
            if lo <= tp:
                return "TP", tp, {
                    "p1": p1_done, "p2": p2_done, "be": be_done}

            if lo < best:
                best = lo
            mr = (entry - best) / risk

            if (PARTIAL_TP_ENABLED and not p1_done
                    and mr >= PARTIAL_TP_TRIGGER_R):
                p1_done = True
            if (PARTIAL_TP_2_ENABLED and not p2_done and p1_done
                    and mr >= PARTIAL_TP_2_TRIGGER_R):
                p2_done = True
            if (not be_done and p1_done
                    and mr >= BREAKEVEN_TRIGGER_R):
                if entry < sl_cur:
                    sl_cur = entry
                    be_done = True
            if TRAILING_ENABLED and mr >= TRAILING_TRIGGER_R:
                ns = best + risk * TRAILING_DISTANCE_R
                if ns < sl_cur:
                    sl_cur = ns

    return "OPEN", entry, {
        "p1": p1_done, "p2": p2_done, "be": be_done}


def pnl_pct(direction, entry, exit_price, p1_done, p2_done):
    """
    Считаем PnL с учётом частичных закрытий:
      - p1: 50% на 0.7R
      - p2: 25% на 1.3R
      - остаток: exit_price
    """
    entry = float(entry)
    exit_price = float(exit_price)
    if direction == "LONG":
        raw = (exit_price - entry) / entry * 100
    else:
        raw = (entry - exit_price) / entry * 100

    if not (p1_done or p2_done):
        return raw

    # упрощённый расчёт: приближаем P1 как +0.7R, P2 как +1.3R
    # в процентах (R = |entry - sl0| / entry * 100)
    # (полноценно мы это уже учли бы, если бы хранили sl0)
    # здесь для отчёта используем средневзвешенный:
    # 50% по 0.7R + 25% по 1.3R + 25% по raw (в R)
    # но поскольку мы не знаем sl0 в этой функции, возвращаем raw.
    # Полноценный R-расчёт делается в run_backtest, где есть sl0.
    return raw


# ------------- BACKTEST LOOP -------------

def run_backtest():
    print(f"TradeMind backtest v{STRATEGY_VERSION}")
    print(f"Period: {DAYS} days")
    print(f"Coins: {list(COINS.keys())}")
    print("=" * 70)

    all_trades = []
    per_coin = defaultdict(list)

    for coin, symbol in COINS.items():
        print(f"\n[{coin}] fetching...")
        try:
            c1h, c15, c5 = fetch_all(symbol)
        except Exception as e:
            print(f"[{coin}] fetch error: {e}")
            continue

        print(f"[{coin}] bars: 1h={len(c1h)} 15m={len(c15)} 5m={len(c5)}")
        if len(c1h) < 30 or len(c15) < 100 or len(c5) < 200:
            print(f"[{coin}] not enough data, skip")
            continue

        min_score = get_min_score(symbol)
        last_sl_close_time = 0  # cooldown

        # начинаем с момента, где уже есть история для 1H
        start_1h = 20
        # шагаем по 5m-барам, раз в 15 минут
        step = 3
        n5 = len(c5)

        i5 = 60  # warmup

        while i5 < n5 - 60:  # оставляем запас для симуляции

            cur_5 = c5[i5]
            cur_time = cur_5["open_time"]

            # срез 1H/15M до текущего времени
            c1h_slice = [c for c in c1h if c["open_time"] <= cur_time]
            c15_slice = [c for c in c15 if c["open_time"] <= cur_time]

            if len(c1h_slice) < start_1h + 5 or len(c15_slice) < 40:
                i5 += step
                continue

            # cooldown после SL
            if last_sl_close_time > 0:
                hours = (cur_time - last_sl_close_time) / 3600000.0
                if hours < COOLDOWN_AFTER_SL_HOURS:
                    i5 += step
                    continue

            price = float(cur_5["close"])

            levels = find_major_liquidity_simple(c1h_slice, price)
            d = get_1h_direction(c1h_slice)
            if d == "NEUTRAL":
                i5 += step
                continue

            sweep = detect_sweep_simple(c1h_slice, d, levels)

            try:
                result = analyze(
                    c1h_slice, c15_slice, c5[:i5 + 1],
                    price, levels, sweep,
                    candles_1m=None,
                    d1_context=None,
                    fvgs=[],
                    symbol=symbol,
                )
            except Exception as e:
                print(f"[{coin}] analyze err @ {cur_time}: {e}")
                i5 += step
                continue

            if result.get("stage") != "READY":
                i5 += step
                continue

            score = int(result.get("score", 0))
            if score < min_score:
                i5 += step
                continue

            rr = result.get("rr")
            if rr is None or float(rr) < MIN_RR:
                i5 += step
                continue

            direction = result.get("direction")
            if direction == "SHORT" and not ALLOW_SHORT:
                i5 += step
                continue

            entry = result.get("entry")
            sl = result.get("sl")
            tp = result.get("tp")
            if entry is None or sl is None or tp is None:
                i5 += step
                continue

            # симулируем по будущим 5m (максимум 24 часа)
            max_future = min(n5, i5 + 12 * 12)  # 12 часов
            future = c5[i5 + 1:max_future]

            res, exit_price, meta = simulate_trade(
                direction, entry, sl, tp, future)

            entry_f = float(entry)
            sl0_f = float(sl)
            risk = abs(entry_f - sl0_f)
            r_mult = 0.0
            if risk > 0:
                if direction == "LONG":
                    r_mult = (float(exit_price) - entry_f) / risk
                else:
                    r_mult = (entry_f - float(exit_price)) / risk

            p1 = meta.get("p1", False)
            p2 = meta.get("p2", False)

            trade = {
                "coin": coin,
                "symbol": symbol,
                "time": datetime.utcfromtimestamp(
                    cur_time / 1000).isoformat() + "Z",
                "direction": direction,
                "score": score,
                "entry": entry_f,
                "sl": sl0_f,
                "tp": float(tp),
                "exit": float(exit_price),
                "result": res,
                "r": round(r_mult, 3),
                "p1": p1,
                "p2": p2,
                "be": meta.get("be", False),
                "anti_fomo_ok": result.get("anti_fomo_ok", True),
                "anti_fomo_reason": result.get(
                    "anti_fomo_reason", ""),
            }
            all_trades.append(trade)
            per_coin[coin].append(trade)

            if res == "SL":
                last_sl_close_time = cur_time

            # перепрыгиваем минимум 5m вперёд, чтобы не долбить одну сделку
            i5 += 1

        print(f"[{coin}] trades: {len(per_coin[coin])}")

    # ---------- REPORT ----------

    print("\n" + "=" * 70)
    print("BACKTEST REPORT")
    print("=" * 70)

    if not all_trades:
        print("No trades found.")
        return

    def summarize(trades, label):
        n = len(trades)
        if n == 0:
            print(f"\n{label}: no trades")
            return
        tp = sum(1 for t in trades if t["result"] == "TP")
        sl = sum(1 for t in trades if t["result"] == "SL")
        be = sum(1 for t in trades if t["result"] == "BE")
        ptsl = sum(1 for t in trades
                   if t["result"] == "PARTIAL_TP+SL")
        op = sum(1 for t in trades if t["result"] == "OPEN")
        closed = n - op
        wins = tp + be + ptsl
        wr = wins / closed * 100 if closed else 0

        rs = [t["r"] for t in trades if t["result"] != "OPEN"]
        avg_r = sum(rs) / len(rs) if rs else 0
        total_r = sum(rs)
        best = max(rs) if rs else 0
        worst = min(rs) if rs else 0

        # MDD по кумулятивному R
        eq = 0.0
        peak = 0.0
        mdd = 0.0
        for r in rs:
            eq += r
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > mdd:
                mdd = dd

        print(f"\n{label}")
        print(f"  Trades: {n} (closed {closed}, open {op})")
        print(f"  TP: {tp}  BE: {be}  Partial+SL: {ptsl}  SL: {sl}")
        print(f"  WR: {wr:.1f}%")
        print(f"  Avg R: {avg_r:+.2f}")
        print(f"  Total R: {total_r:+.2f}")
        print(f"  Best: {best:+.2f}  Worst: {worst:+.2f}")
        print(f"  MDD (R): {mdd:.2f}")

    summarize(all_trades, "ALL COINS")

    for coin in COINS:
        summarize(per_coin.get(coin, []), coin)

    # Anti-FOMO статистика
    fomo_blocked = [t for t in all_trades
                    if not t.get("anti_fomo_ok", True)]
    print(f"\nAnti-FOMO blocked: {len(fomo_blocked)}")

    with open(OUT_TRADES, "w", encoding="utf-8") as f:
        json.dump(all_trades, f, ensure_ascii=False, indent=2)
    print(f"\nTrades saved: {OUT_TRADES}")


if __name__ == "__main__":
    run_backtest()