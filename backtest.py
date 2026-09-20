# -*- coding: utf-8 -*-
"""
TradeMind backtest v9.18 (компактная версия).

ЗАПУСК:
  python backtest.py
  python backtest.py --symbols ETHUSDT,SOLUSDT --min-score ETHUSDT=94
"""

import argparse
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


BT_D1 = 60
BT_1H = 1200
BT_15M = 4800
BT_5M = 14400
BT_1M = 500
WARMUP = 150

MIN_SCORE_BY_SYMBOL = {
    "default": 90,
    "INJUSDT": 88,
    "BCHUSDT": 92,
    "APTUSDT": 90,
}

BANNED_SYMBOLS = set(["BTCUSDT", "SOLUSDT", "SUIUSDT"])
UNPROFITABLE_SYMBOLS = set([
    "ETHUSDT", "DOTUSDT", "XRPUSDT", "LINKUSDT",
])

ALL_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT",
    "XRPUSDT", "LINKUSDT", "DOTUSDT",
    "BCHUSDT", "APTUSDT", "SUIUSDT",
    "INJUSDT",
]

COOLDOWN = {
    "default": {2: 3, 3: 6},
    "APTUSDT": {2: 6, 3: 12},
    "INJUSDT": {2: 4, 3: 8},
    "BCHUSDT": {2: 4, 3: 8},
}

TRAIL_ENABLED = True
TRAIL_TRIG_R = 1.3
TRAIL_DIST_R = 0.8
BE_OFFSET_R = 0.0

PARTIALS = {
    "strong": [0.8, 1.5, 1.0, 40, 30],
    "default": [0.7, 1.3, 1.0, 50, 25],
    "weak": [0.5, 1.1, 0.8, 50, 25],
}

WEAK = set(["SUIUSDT", "SOLUSDT", "APTUSDT", "BCHUSDT"])

RESEARCH_MODE = True


def get_ms(sym):
    if sym in MIN_SCORE_BY_SYMBOL:
        return MIN_SCORE_BY_SYMBOL[sym]
    return MIN_SCORE_BY_SYMBOL["default"]


def get_cfg(sym, score):
    if score >= 95:
        return PARTIALS["strong"]
    if sym in WEAK:
        return PARTIALS["weak"]
    return PARTIALS["default"]


def log(m):
    print("[BT] " + str(m), flush=True)


def ts_str(ms):
    try:
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "N/A"


def c_until(candles, ts):
    return [c for c in candles if c["close_time"] < ts]


def active(trades, ts):
    if not trades:
        return False
    return trades[-1]["exit (_ts"] > ts


def pnlts_p(entry_ms, exit_p, - direction):
    if direction == "L selfONG":
        return (exit.last_p - entry) / entry * 100
    return (entry - exit_p) / entry * 100


def blend_pnl(entry, final_exit, direction,
              p1d, p1e, p1p, p2d, p2e, p2p):
    w1 = 0.0
    w2 = 0.0
    if p1d and p1e is not None:
        w1 = p1p / 100.0
    if p2d and p2e is not None:
        w2 = p2p / 100.0
    rem = max(0.0, 1.0 - w1 - w2)
    total = 0.0
    if w1 > 0:
        total += pnl_p(entry, p1e, direction) * w1
    if w2 > 0:
        total += pnl_p(entry, p2e, direction) * w2
    if rem > 0:
        total += pnl_p(entry, final_exit, direction) * rem
    return total


class CD:
    def __init__(self, sym):
        self.sym = sym
        self.n = 0
        self.last_sl = None
        self.cfg = COOLDOWN.get(sym, COOLDOWN["default"])

    def on(self, rtype, ts_ms):
        if self.last_sl is not None:
            gap =_sl) / 3600000.0
            if gap > 24 and self.n > 0:
                self.n = 0
                self.last_sl = None
        if rtype in ("TP", "BE"):
            self.n = 0
            self.last_sl = None
        elif rtype == "SL":
            self.n += 1
            if self.n > 5:
                self.n = 5
            self.last_sl = ts_ms

    def ok(self, ts_ms):
        if self.n < 2:
            return True
        if self.last_sl is None:
            return True
        h = None
        keys = sorted(self.cfg.keys())
        for k in keys:
            if self.n >= k:
                h = self.cfg[k]
        if h is None:
            h = max(self.cfg.values())
        el = (ts_ms - self.last_sl) / 3600000.0
        return el >= h


def sim(trade, c5, start_ts, max_h, cfg, use_be, use_pt, use_trail):
    d = trade["direction"]
    entry = float(trade["entry"])
    sl0 = float(trade["sl"])
    tp = float(trade["tp"])
    risk = abs(entry - sl0)
    if risk <= 0:
        return ("ERROR", entry, start_ts, 0, 0.0, False)

    p1r = cfg[0]
    p2r = cfg[1]
    be_r = cfg[2]
    p1p = cfg[3]
    p2p = cfg[4]

    fill_max = start_ts + 12 * 5 * 60 * 1000
    filled = False
    fts = None
    for c in c5:
        if c["open_time"] < start_ts:
            continue
        if c["open_time"] > fill_max:
            break
        if d == "LONG":
            if c["low"] <= entry:
                filled = True
                fts = c["open_time"]
                break
        else:
            if c["high"] >= entry:
                filled = True
                fts = c["open_time"]
                break
    if not filled:
        return ("NO_FILL", entry, fill_max, 0, 0.0, False)

    sl = sl0
    best = entry
    p1d = False
    p1e = None
    p2d = False
    p2e = None
    bem = False

    deadline = fts + max_h * 3600 * 1000
    last = None
    held = 0

    for c in c5:
        if c["open_time"] < fts:
            continue
        if c["open_time"] > deadline:
            break
        held += 1
        last = c
        hi = c["high"]
        lo = c["low"]

        if d == "LONG":
            htp = hi >= tp
            hsl = lo <= sl
        else:
            htp = lo <= tp
            hsl = hi >= sl

        et = "SL"
        if bem and abs(sl - entry) < risk * 0.05:
            et = "BE"
        elif d == "LONG" and sl > entry:
            et = "BE"
        elif d == "SHORT" and sl < entry:
            et = "BE"

        if hsl and htp:
            f = blend_pnl(entry, sl, d, p1d, p1e, p1p,
                          p2d, p2e, p2p)
            return (et, sl, c["open_time"], held, f,
                    p1d or p2d)
        if hsl:
            f = blend_pnl(entry, sl, d, p1d, p1e, p1p,
                          p2d, p2e, p2p)
            return (et, sl, c["open_time"], held, f,
                    p1d or p2d)
        if htp:
            f = blend_pnl(entry, tp, d, p1d, p1e, p1p,
                          p2d, p2e, p2p)
            return ("TP", tp, c["open_time"], held, f,
                    p1d or p2d)

        if d == "LONG":
            if hi > best:
                best = hi
            mr = (best - entry) / risk
        else:
            if lo < best:
                best = lo
            mr = (entry - best) / risk

        if use_pt and not p1d and mr >= p1r:
            if d == "LONG":
                p1e = entry + risk * p1r
            else:
                p1e = entry - risk * p1r
            p1d = True

        if use_pt and p1d and not p2d and mr >= p2r:
            if d == "LONG":
                p2e = entry + risk * p2r
            else:
                p2e = entry - risk * p2r
            p2d = True

        br = (not use_pt) or p1d
        if use_be and br and not bem and mr >= be_r:
            if use_pt:
                off = BE_OFFSET_R * risk
            else:
                off = 0.0
            if d == "LONG":
                nb = entry + off
                if nb > sl:
                    sl = nb
                    bem = True
            else:
                nb = entry - off
                if nb < sl:
                    sl = nb
                    bem = True

        if use_trail and TRAIL_ENABLED and mr >= TRAIL_TRIG_R:
            if d == "LONG":
                ns = best - risk * TRAIL_DIST_R
                if ns > sl:
                    sl = ns
            else:
                ns = best + risk * TRAIL_DIST_R
                if ns < sl:
                    sl = ns

    if last is not None:
        ep = last["close"]
        f = blend_pnl(entry, ep, d, p1d, p1e, p1p,
                      p2d, p2e, p2p)
        return ("TIMEOUT", ep, last["open_time"], held, f,
                p1d or p2d)

    return ("TIMEOUT", entry, start_ts, 0, 0.0, False)


def run_one(sym, max_h):
    sym = _normalize_symbol(sym)
    ms = get_ms(sym)
    log("Символ: " + sym + " (min_score=" + str(ms) + ")")

    banned = set()
    if not RESEARCH_MODE:
        banned = set(BANNED_SYMBOLS)
        banned.update(UNPROFITABLE_SYMBOLS)

    if sym in banned:
        log("SKIP banned: " + sym)
        return []

    c1d = get_klines_history("1d", BT_D1, sym)
    c1h = get_klines_history("1h", BT_1H, sym)
    c15 = get_klines_history("15m", BT_15M, sym)
    c5 = get_klines_history("5m", BT_5M, sym)
    c1 = get_klines("1m", BT_1M, sym)

    if not c1h:
        log("Нет данных 1H")
        return []

    log("1H=" + str(len(c1h)) + " 5M=" + str(len(c5)))

    if len(c1h) <= WARMUP:
        return []

    trades = []
    cdm = CD(sym)
    total = len(c1h) - WARMUP
    log("Шагов: " + str(total))

    for i in range(WARMUP, len(c1h)):
        ts = c1h[i]["open_time"]

        if active(trades, ts):
            continue
        if not cdm.ok(ts):
            continue

        cc1h = c1h[:i]
        cc15 = c_until(c15, ts)
        cc5 = c_until(c5, ts)
        cc1 = c_until(c1, ts)
        ccd1 = c_until(c1d, ts)

        if len(cc15) < 60 or len(cc5) < 60:
            continue

        price = cc1[-1]["close"] if cc1 else cc1h[-1]["close"]

        try:
            levels = find_major_liquidity(
                cc1h, price, 12, cc15, cc5, cc1)
        except Exception:
            continue

        try:
            d = get_1h_direction(cc1h)
            sweep = None
            if d != "NEUTRAL":
                sweep = detect_sweep(
                    cc1h, price, d, levels)
            d1c = None
            if len(ccd1) >= 20:
                try:
                    d1c = _analyze_d1_context(ccd1, price)
                except Exception:
                    d1c = None
            r = analyze(cc1h, cc15, cc5, price,
                        levels, sweep, candles_1m=cc1,
                        d1_context=d1c, fvgs=[],
                        symbol=sym)
        except Exception:
            continue

        stage = r.get("stage", "WAIT")
        score = int(r.get("score", 0))

        if stage == "READY" and score < ms:
            continue
        if stage != "READY":
            continue

        e = r.get("entry")
        s = r.get("sl")
        t = r.get("tp")
        if e is None or s is None or t is None:
            continue

        trade = {
            "coin": sym,
            "direction": r["direction"],
            "entry": float(e),
            "sl": float(s),
            "tp": float(t),
            "rr": r.get("rr"),
            "score": score,
        }

        cfg = get_cfg(sym, score)
        res = sim(trade, c5, ts, max_h, cfg,
                  True, True, True)
        rtype, ep, ets, held, pnl, ph = res

        if rtype == "NO_FILL":
            log("[" + str(i) + "] NO_FILL")
            cdm.on("NO_FILL", ets)
            continue

        trade["result"] = rtype
        trade["exit_price"] = ep
        trade["exit_ts"] = ets
        trade["pnl"] = pnl
        trade["partial_hit"] = ph

        trades.append(trade)
        cdm.on(rtype, ets)

        tg = "P" if ph else " "
        log("[" + str(i) + "] "
            + trade["direction"] + " score="
            + str(score) + " " + tg + " -> "
            + rtype + " pnl="
            + ("%+.2f%%" % pnl))

    return trades


def rep(sym, trades):
    print()
    print("=" * 70)
    print("ОТЧЁТ - " + sym)
    print("=" * 70)
    if not trades:
        print("Нет сделок.")
        return

    tp = 0
    sl = 0
    be = 0
    to = 0
    ph = 0
    for t in trades:
        if t["result"] == "TP":
            tp += 1
        elif t["result"] == "SL":
            sl += 1
        elif t["result"] == "BE":
            be += 1
        elif t["result"] == "TIMEOUT":
            to += 1
        if t.get("partial_hit"):
            ph += 1

    r = tp + sl
    if r > 0:
        wr = tp / r * 100
    else:
        wr = 0.0

    total = 0.0
    for t in trades:
        total += t["pnl"]

    eq = 0.0
    peak = 0.0
    dd_max = 0.0
    for t in trades:
        eq += t["pnl"]
        if eq > peak:
            peak = eq
        dd = peak - eq
        if dd > dd_max:
            dd_max = dd

    print("Всего сделок: " + str(len(trades)))
    print("  TP:      " + str(tp))
    print("  SL:      " + str(sl))
    print("  BE:      " + str(be))
    print("  Timeout: " + str(to))
    print("  Partial: " + str(ph))
    print("Win rate: " + ("%.1f%%" % wr))
    print("Total PnL: " + ("%+.2f%%" % total))
    print("Max DD:    " + ("%.2f%%" % (-dd_max)))


def run_multi(max_h, symbols=None):
    if symbols is None:
        symbols = ALL_SYMBOLS

    mode = "RESEARCH" if RESEARCH_MODE else "PROD"

    print()
    print("#" * 70)
    print("### MULTI v9.18 [" + mode + "]")
    print("#" * 70)
    print("### MIN_SCORE: " + str(MIN_SCORE_BY_SYMBOL))
    print("#" * 70)

    summary = []
    for sym in symbols:
        try:
            trades = run_one(sym, max_h)
            rep(sym, trades)

            if trades:
                tp = 0
                sl = 0
                be = 0
                to = 0
                for t in trades:
                    if t["result"] == "TP":
                        tp += 1
                    elif t["result"] == "SL":
                        sl += 1
                    elif t["result"] == "BE":
                        be += 1
                    elif t["result"] == "TIMEOUT":
                        to += 1
                r = tp + sl
                if r > 0:
                    wr = tp / r * 100
                else:
                    wr = 0.0
                pnl = 0.0
                for t in trades:
                    pnl += t["pnl"]
                summary.append(
                    (sym, len(trades), tp, sl, be, to,
                     wr, pnl))
            else:
                summary.append(
                    (sym, 0, 0, 0, 0, 0, 0.0, 0.0))
        except Exception as e:
            print("[BT] " + sym + " FAILED: " + str(e))
            summary.append(
                (sym, 0, 0, 0, 0, 0, 0.0, 0.0))

    print()
    print("=" * 78)
    print("СВОДКА v9.18 [" + mode + "]")
    print("=" * 78)
    print("Символ     MS  Fill  TP  SL  BE  TO   WR      PnL")
    print("-" * 78)

    tt = 0
    ttp = 0
    tsl = 0
    tpnl = 0.0

    for row in summary:
        sym, cnt, tp, sl, be, to, wr, pnl = row
        ms = get_ms(sym)
        line = (sym.ljust(10)
                + " " + str(ms).ljust(3)
                + " " + str(cnt).ljust(5)
                + " " + str(tp).ljust(3)
                + " " + str(sl).ljust(3)
                + " " + str(be).ljust(3)
                + " " + str(to).ljust(3)
                + " " + ("%.1f" % wr).ljust(7)
                + " " + ("%+.2f%%" % pnl))
        print(line)
        tt += cnt
        ttp += tp
        tsl += sl
        tpnl += pnl

    print("-" * 78)
    r = ttp + tsl
    if r > 0:
        twr = ttp / r * 100
    else:
        twr = 0.0
    print("ИТОГО".ljust(15)
          + str(tt).ljust(6)
          + str(ttp).ljust(4)
          + str(tsl).ljust(4)
          + " " * 12
          + ("%.1f" % twr).ljust(7)
          + " " + ("%+.2f%%" % tpnl))
    print("=" * 78)


def main():
    global RESEARCH_MODE

    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="INJUSDT")
    p.add_argument("--max-hours", type=int, default=24)
    p.add_argument("--single", action="store_true")
    p.add_argument("--symbols", default=None)
    p.add_argument("--research", action="store_true")
    p.add_argument("--prod", action="store_true")
    p.add_argument("--min-score", action="append",
                   default=[])
    a = p.parse_args()

    if a.prod:
        RESEARCH_MODE = False
    if a.research:
        RESEARCH_MODE = True

    for ov in a.min_score:
        try:
            parts = ov.split("=")
            sy = parts[0].strip().upper()
            va = int(parts[1])
            MIN_SCORE_BY_SYMBOL[sy] = va
        except Exception:
            print("[WARN] bad --min-score: " + ov)

    if a.single:
        trades = run_one(a.symbol, a.max_hours)
        rep(a.symbol, trades)
    else:
        syms = None
        if a.symbols:
            syms = []
            for s in a.symbols.split(","):
                s = s.strip().upper()
                if s:
                    syms.append(s)
        run_multi(a.max_hours, syms)


if __name__ == "__main__":
    main()