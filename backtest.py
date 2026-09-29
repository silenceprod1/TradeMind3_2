# -*- coding: utf-8 -*-
"""
TradeMind walk-forward backtest v1.0.

Идея: разбить историю на N окон по W дней,
прогнать стратегию на каждом окне отдельно,
показать стабильность метрик.

Использование:
    python backtest_wf.py
    python backtest_wf.py --days=60 --windows=6
"""

import sys
import time
from statistics import mean, stdev

from market import (
    get_klines_history,
    _normalize_symbol, find_major_liquidity,
    detect_sweep, _analyze_d1_context,
)
from strategy import analyze, get_1h_direction

from backtest import (
    sim, get_cfg, get_ms, c_until,
    CFG_STRONG, CFG_DEF, CFG_WEAK,
    FEE_PCT, SLIP_PCT, WEAK_SYMS,
    MIN_SCORES,
)


# ============================================================
# КОНФИГ WF
# ============================================================

WF_DAYS = 60           # сколько дней истории всего тянем
WF_WINDOWS = 6         # на сколько окон делим
WF_WARMUP_HOURS = 200  # сколько 1H-свечей нужно для прогрева
WF_MAX_HOURS = 24      # макс. время удержания сделки

SYMS = [
    "XRPUSDT", "BCHUSDT", "SUIUSDT", "INJUSDT",
    # "APTUSDT",  # убран — убыточный
]


# ============================================================
# ХЕЛПЕРЫ
# ============================================================

def tf_hours_to_ms(h):
    return int(h * 3600 * 1000)


def get_window_slice(candles, start_ms, end_ms):
    return [c for c in candles
            if start_ms <= c["open_time"] <= end_ms]


def compute_stats(trades):
    """Считает WR, Total R, Avg R, MDD, BE+."""
    if not trades:
        return {
            "n": 0, "tp": 0, "sl": 0, "be": 0,
            "be_pos": 0, "to": 0,
            "wr": 0.0, "total": 0.0, "avg": 0.0,
            "mdd": 0.0,
        }

    tp = sl = be = be_pos = to = 0
    for t in trades:
        rt = t["result"]
        pnl = t.get("pnl", 0.0)
        if rt == "TP":
            tp += 1
        elif rt == "SL":
            sl += 1
        elif rt == "BE":
            be += 1
            if pnl > 0.1:
                be_pos += 1
        elif rt == "TIMEOUT":
            to += 1

    r = tp + sl
    wr = (tp / r * 100.0) if r > 0 else 0.0
    total = sum(t["pnl"] for t in trades)

    eq = peak = mdd = 0.0
    for t in trades:
        eq += t["pnl"]
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)

    avg = total / len(trades) if trades else 0.0

    return {
        "n": len(trades),
        "tp": tp, "sl": sl, "be": be, "be_pos": be_pos,
        "to": to, "wr": wr, "total": total,
        "avg": avg, "mdd": mdd,
    }


# ============================================================
# ПРОГОН ОДНОГО ОКНА
# ============================================================

def run_window(sym, c1h, c15, c5, c1m, c1d,
               win_start_ms, win_end_ms,
               warmup_ms):
    """
    Прогоняет стратегию на конкретном окне [win_start, win_end].
    Для прогрева берёт данные ДО win_start (warmup_ms).
    """
    sym_n = _normalize_symbol(sym)
    ms = get_ms(sym_n)

    trades = []
    cooldown_until_ms = 0

    for i in range(len(c1h)):
        ts = c1h[i]["open_time"]

        # Начало окна — пропускаем
        if ts < win_start_ms:
            continue
        # Конец окна — стоп
        if ts > win_end_ms:
            break

        # Cooldown
        if ts < cooldown_until_ms:
            continue

        # Собираем контекст
        cc1h = c1h[:i]
        cc15 = c_until(c15, ts)
        cc5 = c_until(c5, ts)
        cc1 = c_until(c1m, ts)
        ccd1 = c_until(c1d, ts)

        if len(cc15) < 60 or len(cc5) < 60:
            continue

        price = cc1[-1]["close"] if cc1 else cc1h[-1]["close"]

        # Ликвидность
        try:
            lv = find_major_liquidity(
                cc1h, price, 12, cc15, cc5, cc1)
        except Exception:
            continue

        # Анализ
        try:
            d = get_1h_direction(cc1h)
            sw = None
            if d != "NEUTRAL":
                sw = detect_sweep(cc1h, price, d, lv)
            d1c = None
            if len(ccd1) >= 20:
                try:
                    d1c = _analyze_d1_context(ccd1, price)
                    if isinstance(d1c, dict):
                        d1c["candles_d1"] = ccd1
                except Exception:
                    d1c = None
            r = analyze(
                cc1h, cc15, cc5, price, lv, sw,
                candles_1m=cc1, d1_context=d1c,
                fvgs=[], symbol=sym_n)
        except Exception:
            continue

        stage = r.get("stage", "WAIT")
        score = int(r.get("score", 0))

        if stage != "READY":
            continue
        if score < ms:
            continue

        e = r.get("entry")
        s = r.get("sl")
        t = r.get("tp")
        if e is None or s is None or t is None:
            continue

        trade = {
            "coin": sym_n,
            "direction": r["direction"],
            "entry": float(e),
            "sl": float(s),
            "tp": float(t),
            "score": score,
        }

        cfg = get_cfg(sym_n, score)
        rtype, xp, xts, held, pnl, ph = sim(
            trade, c5, ts, WF_MAX_HOURS, cfg)

        if rtype == "NO_FILL":
            continue

        trade.update({
            "result": rtype,
            "exit_price": xp,
            "exit_ts": xts,
            "pnl": pnl,
            "partial_hit": ph,
            "held_bars": held,
        })
        trades.append(trade)

        # Cooldown после SL: 3ч, после 2 SL подряд — 6ч
        if rtype == "SL":
            cooldown_until_ms = xts + 3 * 3600 * 1000

    return trades


# ============================================================
# MAIN
# ============================================================

def main():
    total_days = WF_DAYS
    n_windows = WF_WINDOWS

    # Парсим аргументы
    for a in sys.argv[1:]:
        if a.startswith("--days="):
            try:
                total_days = int(a.split("=", 1)[1])
            except Exception:
                pass
        elif a.startswith("--windows="):
            try:
                n_windows = int(a.split("=", 1)[1])
            except Exception:
                pass

    print("")
    print("#" * 78)
    print(f"### WALK-FORWARD BACKTEST v1.0")
    print(f"### History: {total_days} days | Windows: {n_windows}")
    print(f"### Symbols: {SYMS}")
    print("#" * 78)

    # Тянем историю
    need_1h = int(total_days * 24) + WF_WARMUP_HOURS + 50
    need_15m = int(total_days * 96) + 200
    need_5m = int(total_days * 288) + 500
    need_d1 = 250

    print("")
    print(">>> Loading history...")

    all_data = {}
    for sym in SYMS:
        try:
            c1d = get_klines_history("1d", need_d1, sym)
            c1h = get_klines_history("1h", need_1h, sym)
            c15 = get_klines_history("15m", need_15m, sym)
            c5 = get_klines_history("5m", need_5m, sym)
            all_data[sym] = (c1d, c1h, c15, c5)
            print(f"  {sym}: 1H={len(c1h)} 15M={len(c15)} 5M={len(c5)}")
        except Exception as e:
            print(f"  {sym}: ERROR {e}")

    if not all_data:
        print("Нет данных.")
        return

    # Определяем общий диапазон
    min_ts = None
    max_ts = None
    for sym, (_, c1h, _, _) in all_data.items():
        if not c1h:
            continue
        t0 = c1h[0]["open_time"]
        t1 = c1h[-1]["open_time"]
        if min_ts is None or t0 > min_ts:
            min_ts = t0  # берём ПЕРВУЮ общую свечу
        if max_ts is None or t1 < max_ts:
            max_ts = t1

    if min_ts is None or max_ts is None:
        print("Нет общих свечей.")
        return

    span_ms = max_ts - min_ts
    span_days = span_ms / (24 * 3600 * 1000)
    win_ms = span_ms // n_windows

    print("")
    print(f">>> Span: {span_days:.1f} days")
    print(f">>> Window size: {win_ms / (24 * 3600 * 1000):.1f} days")
    print("")

    # Прогоняем каждое окно
    window_results = []

    for w in range(n_windows):
        w_start = min_ts + w * win_ms
        w_end = min_ts + (w + 1) * win_ms - 1

        # Warmup — данные до начала окна (для расчёта EMA, ATR, свингов)
        warmup_start = w_start - tf_hours_to_ms(WF_WARMUP_HOURS)

        print("=" * 78)
        print(f"### WINDOW {w + 1}/{n_windows} "
              f"({(w_start - min [],
_               ts) / (24 * 3600 * 1000):. c1f1} "
              f"→ {(w_end - min_ts) / (24 * 3600 * 1000):.1df} days)")
        print("=" * 78_w)

        w_trades = []
        for sym in SYMS:
            if sym not in all_data:
                continue
            c1d, c1h, c15, c5 = all_data[sym]

            # Обрезаем массивы: всё что раньше w_end
            c1h_w = [c for c in c1h if c["open_time"] <= w_end]
            c15_w = [c for c in c15 if c["open_time"] <= w_end]
            c5_w = [c for c in c5 if c["open_time"] <= w_end]
            c1d_w = [c for c in c1d if c["open_time"] <= w_end]

            tr = run_window(
                sym, c1h_w, c15_w, c5_w,, w_start, w_end, warmup_start)
            w_trades.extend(tr)

        st = compute_stats(w_trades)
        window_results.append(st)

        print(f"  Trades: {st['n']:3d} | "
              f"TP: {st['tp']:2d} | "
              f"SL: {st['sl']:2d} | "
              f"BE+: {st['be_pos']:2d} | "
              f"WR: {st['wr']:5.1f}% | "
              f"Avg: {st['avg']:+.2f}% | "
              f"Total: {st['total']:+.2f}% | "
              f"MDD: {st['mdd']:.2f}%")
        print("")

    # ============================================================
    # ИТОГИ
    # ============================================================

    print("=" * 78)
    print("### WALK-FORWARD SUMMARY")
    print("=" * 78)
    print("")
    print(f"{'Win':<5} {'N':<5} {'TP':<4} {'SL':<4} "
          f"{'BE+':<4} {'WR%':<7} {'Avg%':<9} "
          f"{'Total%':<10} {'MDD%':<7}")
    print("-" * 78)

    for i, st in enumerate(window_results):
        print(f"{i + 1:<5} {st['n']:<5} {st['tp']:<4} "
              f"{st['sl']:<4} {st['be_pos']:<4} "
              f"{st['wr']:<7.1f} {st['avg']:<+9.2f} "
              f"{st['total']:<+10.2f} {st['mdd']:<7.2f}")

    print("-" * 78)

    # Агрегаты
    wrs = [st["wr"] for st in window_results if st["n"] > 0]
    totals = [st["total"] for st in window_results]
    avgs = [st["avg"] for st in window_results if st["n"] > 0]

    if wrs:
        wr_mean = mean(wrs)
        wr_std = stdev(wrs) if len(wrs) > 1 else 0.0
    else:
        wr_mean = wr_std = 0.0

    total_sum = sum(totals)
    avg_mean = mean(avgs) if avgs else 0.0

    n_total = sum(st["n"] for st in window_results)
    tp_total = sum(st["tp"] for st in window_results)
    sl_total = sum(st["sl"] for st in window_results)
    be_total = sum(st["be"] for st in window_results)

    print("")
    print(f"Total trades: {n_total}")
    print(f"Total TP: {tp_total}, SL: {sl_total}, BE: {be_total}")
    print(f"WR mean: {wr_mean:.1f}%  (std: {wr_std:.1f})")
    print(f"Avg per window: {avg_mean:+.2f}%")
    print(f"Total sum: {total_sum:+.2f}%")
    print("")

    # ============================================================
    # ВЕРДИКТ
    # ============================================================

    print("=" * 78)
    print("### VERDICT")
    print("=" * 78)
    print("")

    if n_total < 15:
        print("⚠️  Мало сделок (<15) — выводы ненадёжны.")
        print("    Прогони на большем периоде (--days=90).")
    else:
        if wr_mean >= 45 and wr_std <= 12:
            print("✅ STRATEGY IS ROBUST")
            print(f"   WR стабильно высокий ({wr_mean:.1f}%), "
                  f"разброс небольшой ({wr_std:.1f}).")
            print("   Можно торговать в реале.")

        elif wr_mean >= 45 and wr_std > 12:
            print("⚠️  STRATEGY IS VOLATILE")
            print(f"   WR высокий ({wr_mean:.1f}%), но разброс "
                  f"большой ({wr_std:.1f}).")
            print("   Возможно, некоторые окна дают WR 20-30%.")
            print("   Рекомендуется: снизить риск, добавить фильтр по режиму.")

        elif wr_mean >= 35:
            print("⚠️  STRATEGY IS MARGINAL")
            print(f"   WR средний ({wr_mean:.1f}%). "
                  f"Не факт, что edge положительный.")
            print("   Рекомендуется: пересмотреть параметры, "
                  "попробовать walk-forward оптимизацию.")

        else:
            print("❌ STRATEGY IS OVERFIT")
            print(f"   WR низкий ({wr_mean:.1f}%). "
                  f"Стратегия НЕ работает на новых данных.")
            print("   Рекомендуется: полностью пересмотреть логику.")

        # Проверка на «минусовые окна»
        neg_windows = sum(1 for t in totals if t < 0)
        if neg_windows >= n_windows // 2:
            print("")
            print(f"❌ {neg_windows} из {n_windows} окон "
                  f"убыточные — стратегия нестабильна.")

    print("")


if __name__ == "__main__":
    main()