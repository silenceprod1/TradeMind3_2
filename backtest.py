# -*- coding: utf-8 -*-
"""
TradeMind backtest v9.34.
10 пар: XRP, BCH, APT, SUI, INJ, SOL, ADA, AVAX, LINK, ARB.
90 дней истории.

v9.34 fixes:
- sim(): правильный порядок SL/TP/trailing + средневзвешенная цена выхода
- COOLDOWN теперь единый источник (используется и в bot.py)
- MIN_SCORES синхронизированы со strategy.COIN_CONFIGS

Запуск:
  python backtest.py
  python backtest.py --research
  python backtest.py --max-hours=48
"""

from market import (
    get_klines,
    get_klines_history,
    _normalize_symbol,
    find_major_liquidity,
    detect_sweep,
    _analyze_d1_context,
)
from strategy import analyze, get_1h_direction, STRATEGY_VERSION


# ============================================================
# CONFIG
# ============================================================

BT_D1   = 250
BT_1H   = 2200        # ~90 дней
BT_15M  = 8800        # ~90 дней
BT_5M   = 26400       # ~90 дней
BT_1M   = 500
WARMUP  = 150

FEE_PCT  = 0.08
SLIP_PCT = 0.05

# v9.34: синхронизировано со strategy.COIN_CONFIGS
MIN_SCORES = {
    "default":  90,
    "XRPUSDT":  91,
    "BCHUSDT":  90,
    "APTUSDT":  94,
    "SUIUSDT":  94,
    "INJUSDT":  92,
    "SOLUSDT":  91,
    "ADAUSDT":  90,
    "AVAXUSDT": 91,
    "LINKUSDT": 90,
    "ARBUSDT":  91,
}

BANNED = {"ETHUSDT", "DOTUSDT", "BTCUSDT"}

ALL_SYMS = [
    "XRPUSDT", "BCHUSDT", "APTUSDT", "SUIUSDT", "INJUSDT",
    "SOLUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "ARBUSDT",
]

# v9.34: единый источник cooldown — используется и bot.py
COOLDOWN = {
    "default":   {2: 3,  3: 6},
    "APTUSDT":   {2: 6,  3: 12},
    "INJUSDT":   {2: 4,  3: 8},
    "BCHUSDT":   {2: 4,  3: 8},
    "SUIUSDT":   {2: 6,  3: 10},
    "SOLUSDT":   {2: 5,  3: 10},
    "AVAXUSDT":  {2: 5,  3: 10},
    "ARBUSDT":   {2: 5,  3: 10},
}

TRAIL_TRIG = 1.5
TRAIL_DIST = 0.8

CFG_STRONG = (1.2, 2.0, 1.5, 30, 30)
CFG_DEF    = (1.0, 1.8, 1.4, 40, 25)
CFG_WEAK   = (0.8, 1.5, 1.2, 50, 25)

WEAK_SYMS = {"SUIUSDT", "APTUSDT", "BCHUSDT",
             "ARBUSDT", "AVAXUSDT"}

RESEARCH_MODE = False


# ============================================================
# HELPERS
# ============================================================

def get_ms(sym):
    return MIN_SCORES.get(sym, MIN_SCORES["default"])


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
    """
    v9.34: возвращает (weighted_pnl_pct, weighted_exit_price).
    """
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
    weighted_exit = 0.0
    total_w = 0.0

    if w1 > 0.0:
        t += pnl_p(e, p1e, d) * w1
        weighted_exit += p1e * w1
        total_w += w1
    if w2 > 0.0:
        t += pnl_p(e, p2e, d) * w2
        weighted_exit += p2e * w2
        total_w += w2
    if rw > 0.0:
        t += pnl_p(e, fx, d) * rw
        weighted_exit += fx * rw
        total_w += rw

    if total_w > 0.0:
        weighted_exit = weighted_exit / total_w
    else:
        weighted_exit = fx

    t -= (FEE_PCT + SLIP_PCT)
    return t, weighted_exit


class CD:
    def __init__(self, sym):
        self.sym = sym
        self.n = 0
        self.last = None
        self.cfg = COOLDOWN.get(sym, COOLDOWN["default"])

    def reset(self):
        self.n = 0
        self.last = None

    def on(self, rt, ts):
        if self.last is not None:
            gap = (ts - self.last) / 3600000.0
            if gap > 24.0 and self.n > 0:
                self.reset()
        if rt in ("TP", "BE"):
            self.reset()
        elif rt == "SL":
            self.n += 1
            if self.n > 5:
                self.n = 5
            self.last = ts

    def ok(self, ts):
        if self.n < 2:
            return True
        if self.last is None:
            return True
        h = None
        for k in sorted(self.cfg.keys()):
            if self.n >= k:
                h = self.cfg[k]
        if h is None:
            h = max(self.cfg.values())
        el = (ts - self.last) / 3600000.0
        return el >= h


# ============================================================
# TRADE SIMULATION (v9.34 — исправлен порядок)
# ============================================================

def sim(trade, c5, start, max_h, cfg):
    """
    v9.34:
    - Trailing обновляется ПОСЛЕ проверки SL/TP (как в реале на 5m баре)
    - Возвращаем средневзвешенную цену выхода
    - Если в одной свече и SL, и TP — консервативно SL, но с
      учётом того, что трейлинг уже мог сдвинуться в предыдущих барах
    """
    d = trade["direction"]
    e = float(trade["entry"])
    sl0 = float(trade["sl"])
    tp = float(trade["tp"])
    risk = abs(e - sl0)
    if risk <= 0.0:
        return ("ERROR", e, start, 0, 0.0, False)

    p1r, p2r, be_r, p1p, p2p = cfg

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
    p1d = False; p1e = None
    p2d = False; p2e = None
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

        held += 1
        last = c
        hi = c["high"]
        lo = c["low"]

        # --- 1. Проверяем SL/TP по текущему (ещё не сдвинутому) SL
        if d == "LONG":
            htp = hi >= tp
            hsl = lo <= sl
        else:
            htp = lo <= tp
            hsl = hi >= sl

        et = "SL"
        if bem:
            if abs(sl - e) < risk * 0.05:
                et = "BE"
        elif d == "LONG" and sl > e:
            et = "BE"
        elif d == "SHORT" and sl < e:
            et = "BE"

        if hsl and htp:
            f, xp = blend(e, sl, d, p1d, p1e, p1p, p2d, p2e, p2p)
            return (et, xp, ot, held, f, p1d or p2d)

        if hsl:
            f, xp = blend(e, sl, d, p1d, p1e, p1p, p2d, p2e, p2p)
            return (et, xp, ot, held, f, p1d or p2d)

        if htp:
            f, xp = blend(e, tp, d, p1d, p1e, p1p, p2d, p2e, p2p)
            return ("TP", xp, ot, held, f, p1d or p2d)

        # --- 2. Обновляем best / partials / trailing по этой свече
        if d == "LONG":
            if hi > best:
                best = hi
            mr = (best - e) / risk
        else:
            if lo < best:
                best = lo
            mr = (e - best) / risk

        if not p1d and mr >= p1r:
            p1e = e + risk * p1r if d == "LONG" else e - risk * p1r
            p1d = True

        if p1d and not p2d and mr >= p2r:
            p2e = e + risk * p2r if d == "LONG" else e - risk * p2r
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
        xp_in = last["close"]
        f, xp = blend(e, xp_in, d, p1d, p1e, p1p, p2d, p2e, p2p)
        return ("TIMEOUT", xp, last["open_time"], held, f, p1d or p2d)

    return ("TIMEOUT", e, start, 0, 0.0, False)


# ============================================================
# RUN ONE SYMBOL
# ============================================================

def run_one(sym, max_h):
    sym = _normalize_symbol(sym)
    ms = get_ms(sym)
    log("Символ: " + sym + "  min_score=" + str(ms))

    if not RESEARCH_MODE and sym in BANNED:
        log("SKIP banned: " + sym)
        return []

    c1d = get_klines_history("1d", BT_D1, sym)
    c1h = get_klines_history("1h", BT_1H, sym)
    c15 = get_klines_history("15m", BT_15M, sym)
    c5  = get_klines_history("5m", BT_5M, sym)
    c1  = get_klines("1m", BT_1M, sym)

    if not c1h:
        log("Нет данных")
        return []

    log("1H=" + str(len(c1h)) + " 5M=" + str(len(c5)))

    if len(c1h) <= WARMUP:
        return []

    trades = []
    cdm = CD(sym)
    log("Шагов: " + str(len(c1h) - WARMUP))

    for i in range(WARMUP, len(c1h)):
        ts = c1h[i]["open_time"]

        if is_active(trades, ts):
            continue
        if not cdm.ok(ts):
            continue

        cc1h = c1h[:i]
        cc15 = c_until(c15, ts)
        cc5  = c_until(c5, ts)
        cc1  = c_until(c1, ts)
        ccd1 = c_until(c1d, ts)

        if len(cc15) < 60 or len(cc5) < 60:
            continue

        if cc1:
            price = cc1[-1]["close"]
        else:
            price = cc1h[-1]["close"]

        try:
            lv = find_major_liquidity(cc1h, price, 12, cc15, cc5, cc1)
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
                fvgs=[], symbol=sym,
            )
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
            "coin": sym,
            "direction": r["direction"],
            "entry": float(e),
            "sl": float(s),
            "tp": float(t),
            "score": score,
        }

        cfg = get_cfg(sym, score)
        res = sim(trade, c5, ts, max_h, cfg)
        rtype, xp, xts, held, pnl, ph = res

        if rtype == "NO_FILL":
            log("[" + str(i) + "] NO_FILL")
            cdm.on("NO_FILL", xts)
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
        cdm.on(rtype, xts)

        pt = "P" if ph else " "
        line = ("[" + str(i) + "] " + trade["direction"] +
                " score=" + str(score) + " " + pt +
                " -> " + rtype + " " + ("%+.2f%%" % pnl))
        log(line)

    return trades


# ============================================================
# STATS / REPORT
# ============================================================

def stats(trades):
    tp = sl = be = to = ph = 0
    for t in trades:
        rt = t["result"]
        if rt == "TP":
            tp += 1
        elif rt == "SL":
            sl += 1
        elif rt == "BE":
            be += 1
        elif rt == "TIMEOUT":
            to += 1
        if t.get("partial_hit"):
            ph += 1

    r = tp + sl
    wr = (tp / r * 100.0) if r > 0 else 0.0
    total = sum(t["pnl"] for t in trades)

    eq = 0.0
    peak = 0.0
    mdd = 0.0
    for t in trades:
        eq += t["pnl"]
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)

    avg = (total / len(trades)) if trades else 0.0

    return {
        "n": len(trades),
        "tp": tp, "sl": sl, "be": be, "to": to, "ph": ph,
        "wr": wr, "total": total, "avg": avg, "mdd": mdd,
    }


def rep(sym, trades):
    print("")
    print("=" * 60)
    print("ОТЧЁТ - " + sym)
    print("=" * 60)

    if not trades:
        print("Нет сделок.")
        return

    st = stats(trades)
    print("Всего сделок: " + str(st["n"]))
    print("  TP:      " + str(st["tp"]))
    print("  SL:      " + str(st["sl"]))
    print("  BE:      " + str(st["be"]))
    print("  Timeout: " + str(st["to"]))
    print("  Partial: " + str(st["ph"]))
    print("Win rate: " + ("%.1f%%" % st["wr"]))
    print("Avg PnL:  " + ("%+.3f%%" % st["avg"]))
    print("Total PnL:" + (" %+.2f%%" % st["total"]))
    print("MDD:      " + ("%.2f%%" % st["mdd"]))


def run_multi(max_h=24, syms=None):
    if syms is None:
        syms = list(ALL_SYMS)

    mode = "RESEARCH" if RESEARCH_MODE else "PROD"

    print("")
    print("#" * 70)
    print("### MULTI v" + STRATEGY_VERSION + " [90 days] [" + mode + "]")
    print("#" * 70)
    print("### MIN_SCORE: " + str(MIN_SCORES))
    print("### BANNED:   " + str(sorted(BANNED)))
    print("### FEES:     " + ("%.3f%%" % FEE_PCT) +
          " + SLIP " + ("%.3f%%" % SLIP_PCT))
    print("### FIXED_RR: 2.0")
    print("### CFG_STRONG: " + str(CFG_STRONG))
    print("### CFG_DEF:    " + str(CFG_DEF))
    print("### CFG_WEAK:   " + str(CFG_WEAK))
    print("### COOLDOWN:   " + str(COOLDOWN))
    print("### HISTORY:    90 days (1H=2200, 5M=26400)")
    print("#" * 70)

    summary = []
    for sym in syms:
        try:
            trades = run_one(sym, max_h)
            rep(sym, trades)
            st = stats(trades)
            summary.append((sym, st))
        except Exception as ex:
            print("[BT] " + sym + " FAILED: " + str(ex))
            summary.append((sym, {
                "n": 0, "tp": 0, "sl": 0, "be": 0, "to": 0,
                "ph": 0, "wr": 0.0, "total": 0.0,
                "avg": 0.0, "mdd": 0.0,
            }))

    print("")
    print("=" * 82)
    print("СВОДКА v" + STRATEGY_VERSION + " [" + mode + "]")
    print("=" * 82)
    print("Символ      MS   N   TP  SL  BE  TO   WR      Avg     Total     MDD")
    print("-" * 82)

    t_n = t_tp = t_sl = t_be = t_to = 0
    t_total = 0.0

    for sym, st in summary:
        ms = get_ms(sym)
        line  = sym.ljust(11)
        line += str(ms).ljust(4)
        line += str(st["n"]).ljust(4)
        line += str(st["tp"]).ljust(4)
        line += str(st["sl"]).ljust(4)
        line += str(st["be"]).ljust(4)
        line += str(st["to"]).ljust(4)
        line += ("%.1f" % st["wr"]).ljust(8)
        line += ("%+.3f" % st["avg"]).ljust(8)
        line += ("%+.2f" % st["total"]).ljust(10)
        line += "%.2f" % st["mdd"]
        print(line)

        t_n     += st["n"]
        t_tp    += st["tp"]
        t_sl    += st["sl"]
        t_be    += st["be"]
        t_to    += st["to"]
        t_total += st["total"]

    print("-" * 82)
    r = t_tp + t_sl
    twr  = (t_tp / r * 100.0) if r > 0 else 0.0
    tavg = (t_total / t_n) if t_n else 0.0
    line  = "ИТОГО".ljust(15)
    line += str(t_n).ljust(4)
    line += str(t_tp).ljust(4)
    line += str(t_sl).ljust(4)
    line += str(t_be).ljust(4)
    line += str(t_to).ljust(4)
    line += ("%.1f" % twr).ljust(8)
    line += ("%+.3f" % tavg).ljust(8)
    line += ("%+.2f" % t_total)
    print(line)
    print("=" * 82)


# ============================================================
# ENTRY POINT
# ============================================================

def main(max_hours=24, research=False, syms=None):
    global RESEARCH_MODE
    RESEARCH_MODE = bool(research)
    if syms is None:
        syms = list(ALL_SYMS)
    run_multi(max_hours, syms)


if __name__ == "__main__":
    import sys as _sys
    _mh = 24
    _rs = False
    for _a in _sys.argv[1:]:
        if _a.startswith("--max-hours="):
            try:
                _mh = int(_a.split("=", 1)[1])
            except Exception:
                pass
        elif _a == "--research":
            _rs = True
    main(_mh, _rs)
