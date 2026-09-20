# -*- coding: utf-8 -*-
"""
TradeMind backtest v9.19 FINAL.
7 пар: BTC, XRP, LINK, BCH, APT, SUI, INJ.

Отсеяны: ETH, SOL, DOT (по research).
"""

import argparse
from datetime import datetime

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

MIN_SCORES = {
    "default": 90,
    "INJUSDT": 88,
    "BCHUSDT": 92,
    "APTUSDT": 90,
}

BANNED = set()
BANNED.add("ETHUSDT")
BANNED.add("SOLUSDT")
BANNED.add("DOTUSDT")

ALL_SYMS = [
    "BTCUSDT",
    "XRPUSDT",
    "LINKUSDT",
    "BCHUSDT",
    "APTUSDT",
    "SUIUSDT",
    "INJUSDT",
]

COOLDOWN = {}
COOLDOWN["default"] = {2: 3, 3: 6}
COOLDOWN["APTUSDT"] = {2: 6, 3: 12}
COOLDOWN["INJUSDT"] = {2: 4, 3: 8}
COOLDOWN["BCHUSDT"] = {2: 4, 3: 8}

TRAIL_TRIG = 1.3
TRAIL_DIST = 0.8

CFG_STRONG = (0.8, 1.5, 1.0, 40, 30)
CFG_DEF = (0.7, 1.3, 1.0, 50, 25)
CFG_WEAK = (0.5, 1.1, 0.8, 50, 25)

WEAK_SYMS = set()
WEAK_SYMS.add("SUIUSDT")
WEAK_SYMS.add("APTUSDT")
WEAK_SYMS.add("BCHUSDT")

RESEARCH_MODE = False


def get_ms(sym):
    if sym in MIN_SCORES:
        return MIN_SCORES[sym]
    return MIN_SCORES["default"]


def get_cfg(sym, score):
    if score >= 95:
        return CFG_STRONG
    if sym in WEAK_SYMS:
        return CFG_WEAK
    return CFG_DEF


def log(m):
    print("[BT] " + str(m), flush=True)


def c_until(cs, ts):
    out = []
    for c in cs:
        if c["close_time"] < ts:
            out.append(c)
    return out


def is_active(trades, ts):
    if not trades:
        return False
    return trades[-1]["exit_ts"] > ts


def pnl_p(e, x, d):
    if d == "LONG":
        return (x - e) / e * 100.0
    return (e - x) / e * 100.0


def blend(e, fx, d, p1d, p1e, p1p, p2d, p2e, p2p):
    w1 = 0.0
    if p1d and p1e is not None:
        w1 = p1p / 100.0
    w2 = 0.0
    if p2d and p2e is not None:
        w2 = p2p / 100.0
    rw = 1.0 - w1 - w2
    if rw < 0.0:
        rw = 0.0
    t = 0.0
    if w1 > 0.0:
        t = t + pnl_p(e, p1e, d) * w1
    if w2 > 0.0:
        t = t + pnl_p(e, p2e, d) * w2
    if rw > 0.0:
        t = t + pnl_p(e, fx, d) * rw
    return t


class CD:
    def __init__(self, sym):
        self.sym = sym
        self.n = 0
        self.last = None
        cfg = COOLDOWN.get(sym)
        if cfg is None:
            cfg = COOLDOWN["default"]
        self.cfg = cfg

    def reset(self):
        self.n = 0
        self.last = None

    def on(self, rt, ts):
        if self.last is not None:
            gap = (ts - self.last) / 3600000.0
            if gap > 24.0 and self.n > 0:
                self.reset()
        if rt == "TP" or rt == "BE":
            self.reset()
        elif rt == "SL":
            self.n = self.n + 1
            if self.n > 5:
                self.n = 5
            self.last = ts

    def ok(self, ts):
        if self.n < 2:
            return True
        if self.last is None:
            return True
        h = None
        ks = sorted(self.cfg.keys())
        for k in ks:
            if self.n >= k:
                h = self.cfg[k]
        if h is None:
            h = max(self.cfg.values())
        el = (ts - self.last) / 3600000.0
        return el >= h


def sim(trade, c5, start, max_h, cfg):
    d = trade["direction"]
    e = float(trade["entry"])
    sl0 = float(trade["sl"])
    tp = float(trade["tp"])
    risk = abs(e - sl0)
    if risk <= 0.0:
        return ("ERROR", e, start, 0, 0.0, False)

    p1r = cfg[0]
    p2r = cfg[1]
    be_r = cfg[2]
    p1p = cfg[3]
    p2p = cfg[4]

    fill_max = start + 12 * 5 * 60 * 1000
    filled = False
    fts = None

    for c in c5:
        ot = c["open_time"]
        if ot < start:
            continue
        if ot > fill_max:
            break
        if d == "LONG":
            if c["low"] <= e:
                filled = True
                fts = ot
                break
        else:
            if c["high"] >= e:
                filled = True
                fts = ot
                break

    if not filled:
        return ("NO_FILL", e, fill_max, 0, 0.0, False)

    sl = sl0
    best = e
    p1d = False
    p1e = None
    p2d = False
    p2e = None
    bem = False

    dl = fts + max_h * 3600 * 1000
    last = None
    held = 0

    for c in c5:
        ot = c["open_time"]
        if ot < fts:
            continue
        if ot > dl:
            break

        held = held + 1
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
        if bem:
            diff = abs(sl - e)
            lim = risk * 0.05
            if diff < lim:
                et = "BE"
        elif d == "LONG" and sl > e:
            et = "BE"
        elif d == "SHORT" and sl < e:
            et = "BE"

        if hsl and htp:
            f = blend(e, sl, d, p1d, p1e, p1p,
                      p2d, p2e, p2p)
            return (et, sl, ot, held, f, p1d or p2d)

        if hsl:
            f = blend(e, sl, d, p1d, p1e, p1p,
                      p2d, p2e, p2p)
            return (et, sl, ot, held, f, p1d or p2d)

        if htp:
            f = blend(e, tp, d, p1d, p1e, p1p,
                      p2d, p2e, p2p)
            return ("TP", tp, ot, held, f, p1d or p2d)

        if d == "LONG":
            if hi > best:
                best = hi
            mr = (best - e) / risk
        else:
            if lo < best:
                best = lo
            mr = (e - best) / risk

        if not p1d and mr >= p1r:
            if d == "LONG":
                p1e = e + risk * p1r
            else:
                p1e = e - risk * p1r
            p1d = True

        if p1d and not p2d and mr >= p2r:
            if d == "LONG":
                p2e = e + risk * p2r
            else:
                p2e = e - risk * p2r
            p2d = True

        if not bem and mr >= be_r:
            if d == "LONG":
                if e > sl:
                    sl = e
                    bem = True
            else:
                if e < sl:
                    sl = e
                    bem = True

        if mr >= TRAIL_TRIG:
            if d == "LONG":
                ns = best - risk * TRAIL_DIST
                if ns > sl:
                    sl = ns
            else:
                ns = best + risk * TRAIL_DIST
                if ns < sl:
                    sl = ns

    if last is not None:
        xp = last["close"]
        f = blend(e, xp, d, p1d, p1e, p1p,
                  p2d, p2e, p2p)
        return ("TIMEOUT", xp, last["open_time"],
                held, f, p1d or p2d)

    return ("TIMEOUT", e, start, 0, 0.0, False)


def run_one(sym, max_h):
    sym = _normalize_symbol(sym)
    ms = get_ms(sym)
    log("Символ: " + sym + "  min_score=" + str(ms))

    if not RESEARCH_MODE:
        if sym in BANNED:
            log("SKIP banned: " + sym)
            return []

    c1d = get_klines_history("1d", BT_D1, sym)
    c1h = get_klines_history("1h", BT_1H, sym)
    c15 = get_klines_history("15m", BT_15M, sym)
    c5 = get_klines_history("5m", BT_5M, sym)
    c1 = get_klines("1m", BT_1M, sym)

    if not c1h:
        log("Нет данных")
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

        if is_active(trades, ts):
            continue
        if not cdm.ok(ts):
            continue

        cc1h = c1h[:i]
        cc15 = c_until(c15, ts)
        cc5 = c_until(c5, ts)
        cc1 = c_until(c1, ts)
        ccd1 = c_until(c1d, ts)

        if len(cc15) < 60:
            continue
        if len(cc5) < 60:
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
                sw = detect_sweep(
                    cc1h, price, d, lv)
            d1c = None
            if len(ccd1) >= 20:
                try:
                    d1c = _analyze_d1_context(ccd1, price)
                except Exception:
                    d1c = None
            r = analyze(
                cc1h, cc15, cc5, price, lv, sw,
                candles_1m=cc1, d1_context=d1c,
                fvgs=[], symbol=sym)
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
            "score": score,
        }

        cfg = get_cfg(sym, score)
        res = sim(trade, c5, ts, max_h, cfg)
        rtype = res[0]
        xp = res[1]
        xts = res[2]
        held = res[3]
        pnl = res[4]
        ph = res[5]

        if rtype == "NO_FILL":
            log("[" + str(i) + "] NO_FILL")
            cdm.on("NO_FILL", xts)
            continue

        trade["result"] = rtype
        trade["exit_price"] = xp
        trade["exit_ts"] = xts
        trade["pnl"] = pnl
        trade["partial_hit"] = ph

        trades.append(trade)
        cdm.on(rtype, xts)

        pt = "P" if ph else " "
        pl = "%+.2f%%" % pnl
        line = "[" + str(i) + "] "
        line = line + trade["direction"] + " "
        line = line + "score=" + str(score) + " "
        line = line + pt + " -> " + rtype + " " + pl
        log(line)

    return trades


def stats(trades):
    tp = 0
    sl = 0
    be = 0
    to = 0
    ph = 0
    for t in trades:
        rt = t["result"]
        if rt == "TP":
            tp = tp + 1
        elif rt == "SL":
            sl = sl + 1
        elif rt == "BE":
            be = be + 1
        elif rt == "TIMEOUT":
            to = to + 1
        if t.get("partial_hit"):
            ph = ph + 1

    r = tp + sl
    if r > 0:
        wr = tp / r * 100.0
    else:
        wr = 0.0

    total = 0.0
    for t in trades:
        total = total + t["pnl"]

    return (len(trades), tp, sl, be, to, ph, wr, total)


def rep(sym, trades):
    print("")
    print("=" * 60)
    print("ОТЧЁТ - " + sym)
    print("=" * 60)

    if not trades:
        print("Нет сделок.")
        return

    st = stats(trades)
    n = st[0]
    tp = st[1]
    sl = st[2]
    be = st[3]
    to = st[4]
    ph = st[5]
    wr = st[6]
    total = st[7]

    print("Всего сделок: " + str(n))
    print("  TP: " + str(tp))
    print("  SL: " + str(sl))
    print("  BE: " + str(be))
    print("  Timeout: " + str(to))
    print("  Partial: " + str(ph))
    print("Win rate: " + ("%.1f%%" % wr))
    print("Total PnL: " + ("%+.2f%%" % total))


def run_multi(max_h, syms):
    mode = "PROD"
    if RESEARCH_MODE:
        mode = "RESEARCH"

    print("")
    print("#" * 70)
    print("### MULTI v9.19 [" + mode + "]")
    print("#" * 70)
    print("### MIN_SCORE: " + str(MIN_SCORES))
    print("### BANNED: " + str(sorted(BANNED)))
    print("#" * 70)

    summary = []

    for sym in syms:
        try:
            trades = run_one(sym, max_h)
            rep(sym, trades)
            st = stats(trades)
            summary.append((sym,) + st)
        except Exception as ex:
            print("[BT] " + sym + " FAILED: " + str(ex))
            summary.append(
                (sym, 0, 0, 0, 0, 0, 0, 0.0, 0.0))

    print("")
    print("=" * 78)
    print("СВОДКА v9.19 [" + mode + "]")
    print("=" * 78)

    hdr = "Символ     MS  Fill  TP  SL  BE  TO   WR      PnL"
    print(hdr)
    print("-" * 78)

    tt = 0
    ttp = 0
    tsl = 0
    tpnl = 0.0

    for row in summary:
        sym = row[0]
        n = row[1]
        tp = row[2]
        sl = row[3]
        be = row[4]
        to = row[5]
        wr = row[7]
        pnl = row[8]
        ms = get_ms(sym)

        line = sym.ljust(10)
        line = line + " " + str(ms).ljust(3)
        line = line + " " + str(n).ljust(5)
        line = line + " " + str(tp).ljust(3)
        line = line + " " + str(sl).ljust(3)
        line = line + " " + str(be).ljust(3)
        line = line + " " + str(to).ljust(3)
        line = line + " " + ("%.1f" % wr).ljust(7)
        line = line + " " + ("%+.2f%%" % pnl)
        print(line)

        tt = tt + n
        ttp = ttp + tp
        tsl = tsl + sl
        tpnl = tpnl + pnl

    print("-" * 78)
    r = ttp + tsl
    if r > 0:
        twr = ttp / r * 100.0
    else:
        twr = 0.0

    line = "ИТОГО".ljust(15)
    line = line + str(tt).ljust(6)
    line = line + str(ttp).ljust(4)
    line = line + str(tsl).ljust(4)
    line = line + " " * 12
    line = line + ("%.1f" % twr).ljust(7)
    line = line + " " + ("%+.2f%%" % tpnl)
    print(line)
    print("=" * 78)


def main():
    global RESEARCH_MODE

    p = argparse.ArgumentParser()
    p.add_argument("--max-hours", type=int, default=24)
    p.add_argument("--research", action="store_true")
    a = p.parse_args()

    if a.research:
        RESEARCH_MODE = True

    syms = list(ALL_SYMS)
    run_multi(a.max_hours, syms)


if __name__ == "__main__":
    main()