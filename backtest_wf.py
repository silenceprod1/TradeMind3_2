# -*- coding: utf-8 -*-
"""
TradeMind walk-forward backtest v1.1
Fix: убраны проблемные f-строки для копипаста.
"""

import sys
from statistics import mean, stdev

from market import (
    get_klines_history,
    _normalize_symbol, find_major_liquidity,
    detect_sweep, _analyze_d1_context,
)
from strategy import analyze, get_1h_direction

from backtest import (
    sim, get_cfg, get_ms, c_until,
    FEE_PCT, SLIP_PCT,
)


WF_DAYS = 60
WF_WINDOWS = 6
WF_WARMUP_HOURS = 200
WF_MAX_HOURS = 24

SYMS = [
    "XRPUSDT", "BCHUSDT", "SUIUSDT", "INJUSDT",
]


def tf_hours_to_ms(h):
    return int(h * 3600 * 1000)


def compute_stats(trades):
    if not trades:
        return {
            "n": 0, "tp": 0, "sl": 0, "be": 0,
            "be_pos": 0, "to": 0,
            "wr": 0.0, "total": 0.0, "avg": 0.0,
            "mdd": 0.0,
        }

    tp = 0
    sl = 0
    be = 0
    be_pos = 0
    to = 0
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
    if r > 0:
        wr = tp / r * 100.0
    else:
        wr = 0.0

    total = sum(t["pnl"] for t in trades)
    eq = 0.0
    peak = 0.0
    mdd = 0.0
    for t in trades:
        eq += t["pnl"]
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > mdd:
            mdd = dd

    avg = 0.0
    if trades:
        avg = total / len(trades)

    return {
        "n": len(trades),
        "tp": tp, "sl": sl, "be": be,
        "be_pos": be_pos, "to": to,
        "wr": wr, "total": total,
        "avg": avg, "mdd": mdd,
    }


def run_window(sym, c1h, c15, c5, c1m, c1d,
               win_start_ms, win_end_ms):
    sym_n = _normalize_symbol(sym)
    ms = get_ms(sym_n)

    trades = []
    cooldown_until_ms = 0

    for i in range(len(c1h)):
        ts = c1h[i]["open_time"]

        if ts < win_start_ms:
            continue
        if ts > win_end_ms:
            break

        if ts < cooldown_until_ms:
            continue

        cc1h = c1h[:i]
        cc15 = c_until(c15, ts)
        cc5 = c_until(c5, ts)
        cc1 = c_until(c1m, ts)
        ccd1 = c_until(c1d, ts)

        if len(cc15) < 60 or len(cc5) < 60:
            continue

        if cc1:
            price = cc1[-1]["close"]
        else:
            price = cc1h[-1]["close"]

        try:
            lv = find_major_liquidity(
                cc1h, price, 12, cc15, cc5, cc1)
        except Exception:
            continue

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

        if rtype == "SL":
            cooldown_until_ms = xts + 3 * 3600 * 1000

    return trades


def days_between(ts1, ts2):
    return (ts2 - ts1) / (24 * 3600 * 1000)


def main():
    total_days = WF_DAYS
    n_windows = WF_WINDOWS

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
    print("=" * 78)
    print("### WALK-FORWARD BACKTEST v1.1")
    print("### History days: " + str(total_days))
    print("### Windows: " + str(n_windows))
    print("### Symbols: " + str(SYMS))
    print("=" * 78)

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
            print("  " + sym + ": 1H=" + str(len(c1h))
                  + " 15M=" + str(len(c15))
                  + " 5M=" + str(len(c5)))
        except Exception as e:
            print("  " + sym + ": ERROR " + str(e))

    if not all_data:
        print("Нет данных.")
        return

    min_ts = None
    max_ts = None
    for sym in all_data:
        c1d, c1h, c15, c5 = all_data[sym]
        if not c1h:
            continue
        t0 = c1h[0]["open_time"]
        t1 = c1h[-1]["open_time"]
        if min_ts is None or t0 > min_ts:
            min_ts = t0
        if max_ts is None or t1 < max_ts:
            max_ts = t1

    if min_ts is None or max_ts is None:
        print("Нет общих свечей.")
        return

    span_ms = max_ts - min_ts
    span_days = days_between(min_ts, max_ts)
    win_ms = span_ms // n_windows

    print("")
    print(">>> Span days: " + str(round(span_days, 1)))
    print(">>> Window size days: " + str(round(days_between(0, win_ms), 1)))
    print("")

    window_results = []

    for w in range(n_windows):
        w_start = min_ts + w * win_ms
        w_end = min_ts + (w + 1) * win_ms - 1

        print("=" * 78)
        wd1 = days_between(min_ts, w_start)
        wd2 = days_between(min_ts, w_end)
        print("### WINDOW " + str(w + 1) + "/" + str(n_windows))
        print("### From day " + str(round(wd1, 1))
              + " to day " + str(round(wd2, 1)))
        print("=" * 78)

        w_trades = []
        for sym in SYMS:
            if sym not in all_data:
                continue
            c1d, c1h, c15, c5 = all_data[sym]

            c1h_w = [c for c in c1h if c["open_time"] <= w_end]
            c15_w = [c for c in c15 if c["open_time"] <= w_end]
            c5_w = [c for c in c5 if c["open_time"] <= w_end]
            c1d_w = [c for c in c1d if c["open_time"] <= w_end]

            tr = run_window(
                sym, c1h_w, c15_w, c5_w, [],
                c1d_w, w_start, w_end)
            w_trades.extend(tr)

        st = compute_stats(w_trades)
        window_results.append(st)

        line = "  N=" + str(st["n"])
        line += " TP=" + str(st["tp"])
        line += " SL=" + str(st["sl"])
        line += " BE+=" + str(st["be_pos"])
        line += " WR=" + str(round(st["wr"], 1)) + "%"
        line += " Avg=" + str(round(st["avg"], 2)) + "%"
        line += " Total=" + str(round(st["total"], 2)) + "%"
        line += " MDD=" + str(round(st["mdd"], 2)) + "%"
        print(line)
        print("")

    print("=" * 78)
    print("### WALK-FORWARD SUMMARY")
    print("=" * 78)
    print("")

    header = "Win  N   TP  SL  BE+ WR%    Avg%    Total%   MDD%"
    print(header)
    print("-" * 78)

    for i in range(len(window_results)):
        st = window_results[i]
        line = str(i + 1).ljust(4)
        line += str(st["n"]).ljust(4)
        line += str(st["tp"]).ljust(4)
        line += str(st["sl"]).ljust(4)
        line += str(st["be_pos"]).ljust(4)
        line += str(round(st["wr"], 1)).ljust(7)
        line += str(round(st["avg"], 2)).ljust(8)
        line += str(round(st["total"], 2)).ljust(9)
        line += str(round(st["mdd"], 2))
        print(line)

    print("-" * 78)

    wrs = []
    totals = []
    avgs = []
    for st in window_results:
        if st["n"] > 0:
            wrs.append(st["wr"])
            avgs.append(st["avg"])
        totals.append(st["total"])

    if wrs:
        wr_mean = mean(wrs)
        if len(wrs) > 1:
            wr_std = stdev(wrs)
        else:
            wr_std = 0.0
    else:
        wr_mean = 0.0
        wr_std = 0.0

    total_sum = sum(totals)
    if avgs:
        avg_mean = mean(avgs)
    else:
        avg_mean = 0.0

    n_total = 0
    tp_total = 0
    sl_total = 0
    be_total = 0
    for st in window_results:
        n_total += st["n"]
        tp_total += st["tp"]
        sl_total += st["sl"]
        be_total += st["be"]

    print("")
    print("Total trades: " + str(n_total))
    print("Total TP: " + str(tp_total))
    print("Total SL: " + str(sl_total))
    print("Total BE: " + str(be_total))
    print("WR mean: " + str(round(wr_mean, 1))
          + "%  (std: " + str(round(wr_std, 1)) + ")")
    print("Avg per window: " + str(round(avg_mean, 2)) + "%")
    print("Total sum: " + str(round(total_sum, 2)) + "%")
    print("")

    print("=" * 78)
    print("### VERDICT")
    print("=" * 78)
    print("")

    if n_total < 15:
        print("Мало сделок (<15). Выводы ненадёжны.")
        print("Прогони на большем периоде: --days=90")
    else:
        if wr_mean >= 45 and wr_std <= 12:
            print("OK - STRATEGY IS ROBUST")
            print("WR mean " + str(round(wr_mean, 1))
                  + "% std " + str(round(wr_std, 1)))
            print("Можно торговать в реале.")
        elif wr_mean >= 45 and wr_std > 12:
            print("WARN - STRATEGY IS VOLATILE")
            print("WR mean " + str(round(wr_mean, 1))
                  + "% std " + str(round(wr_std, 1)))
            print("Снизить риск, добавить фильтр режима.")
        elif wr_mean >= 35:
            print("WARN - STRATEGY IS MARGINAL")
            print("WR mean " + str(round(wr_mean, 1)) + "%")
            print("Edge слабый. Пересмотреть параметры.")
        else:
            print("FAIL - STRATEGY IS OVERFIT")
            print("WR mean " + str(round(wr_mean, 1)) + "%")
            print("Стратегия не работает на новых данных.")

        neg = 0
        for t in totals:
            if t < 0:
                neg += 1
        if neg >= n_windows // 2:
            print("")
            print("FAIL: " + str(neg) + " из "
                  + str(n_windows) + " окон убыточные.")

    print("")


if __name__ == "__main__":
    main()