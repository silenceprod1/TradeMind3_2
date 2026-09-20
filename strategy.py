# -*- coding: utf-8 -*-
"""
TradeMind strategy v9.19.
MIN_SCORE_READY = 85 (per-symbol фильтр в bot.py).
"""

from typing import Any, Dict, List, Optional, Tuple


STRATEGY_VERSION = "9.19"

ALLOW_SHORT = True

MAX_ILM_AGE_FOR_ENTRY = 6
ATR_SL_MULT_SOFT = 0.8

ENABLE_SESSION_FILTER = False
SESSION_BLOCK_START_HOUR = 2
SESSION_BLOCK_END_HOUR = 7
SESSION_FILTER_EXEMPT = set()
SESSION_FILTER_EXEMPT.add("BTCUSDT")
SESSION_FILTER_EXEMPT.add("ETHUSDT")

RETEST_OFFSET_PCT = 0.0

MIN_SCORE_READY = 85
REQUIRE_BOS_FOR_READY = True
SL_BUFFER_PCT = 0.20
STRUCTURAL_SL_LOOKBACK_15M = 50
ENTRY_TOLERANCE_PCT = 1.0
FIXED_RR = 2.0

MIN_SWEEP_DEPTH_PCT = 0.12
MAX_SWEEP_AGE_1H = 24

USE_ATR_SCALING = True
ATR_SL_MULT = 1.2
ATR_SL_MAX_MULT = 3.0
MIN_SL_DISTANCE_PCT = 0.35
MAX_SL_DISTANCE_PCT = 4.0

ENABLE_SIDEWAYS_FILTER = False
ENABLE_POSITION_FILTER = False
ENABLE_D1_BLOCK = False

SL_USE_1H_SWINGS = True
SL_USE_SWEEP_EXTREME = True
MIN_SL_ATR_MULT = 1.2
MIN_BODY_RATIO_TRIGGER_5M = 0.40
VOLATILITY_ATR_SPIKE_MULT = 2.5
ENABLE_VOLATILITY_FILTER = True

VOLUME_CONFIRMATION_ENABLED = False
VOLUME_CONFIRMATION_MULT = 1.2
VOLUME_CONFIRMATION_LOOKBACK = 20

MIN_BODY_RATIO = 0.35
MAX_5M_ILM_CANDLES = 60
MAX_15M_CONFIRM_CANDLES = 24
MAX_ILM_AGE_CANDLES_5M = 48

MIN_5M_RECOVERY_RATIO = 0.15
MIN_5M_ILM_SWEEP_DISTANCE_PCT = 5.0

MIN_TREND_ACTIVITY_READY = 0.35
COUNTER_TREND_MIN_SCORE = 88

FVG_TOLERANCE_PCT = 0.10
FVG_SWEEP_BONUS = 10
FVG_ENTRY_BONUS = 5
FVG_MAX_BONUS = 15

ILM_TRIGGER_WINDOW = 5

READY_PROMOTE_TIERS = (
    (95, 0.20, False),
    (90, 0.25, True),
    (88, 0.30, True),
)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _v(candle, key, default=None):
    if not isinstance(candle, dict):
        return default
    value = candle.get(key)
    if value is None:
        aliases = {
            "open": "o", "high": "h", "low": "l",
            "close": "c", "open_time": "time",
            "volume": "v",
        }
        alias = aliases.get(key)
        if alias:
            value = candle.get(alias)
    if value is None:
        return default
    conv = _f(value)
    if conv is not None:
        return conv
    return default


def _o(c):
    return _v(c, "open")


def _h(c):
    return _v(c, "high")


def _l(c):
    return _v(c, "low")


def _c(c):
    return _v(c, "close")


def _t(c):
    return _v(c, "open_time")


def _vol(c):
    return _v(c, "volume") or 0.0


def _body(c):
    o = _o(c)
    cl = _c(c)
    if o is None or cl is None:
        return 0.0
    return abs(cl - o)


def _range(c):
    h = _h(c)
    l = _l(c)
    if h is None or l is None:
        return 0.0
    return max(0.0, h - l)


def _body_ratio(c):
    r = _range(c)
    if r > 0:
        return _body(c) / r
    return 0.0


def _bull(c):
    o = _o(c)
    cl = _c(c)
    if o is None or cl is None:
        return False
    return cl > o


def _bear(c):
    o = _o(c)
    cl = _c(c)
    if o is None or cl is None:
        return False
    return cl < o


def _dist_pct(a, b):
    a = _f(a)
    b = _f(b)
    if a is None or b is None or b == 0:
        return None
    return abs(a - b) / abs(b) * 100


def calculate_atr(candles, period=14):
    if not candles:
        return None
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = _h(candles[i])
        l = _l(candles[i])
        pc = _c(candles[i - 1])
        if h is None or l is None or pc is None:
            continue
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def _avg_atr(candles, fast=14, slow=50):
    if not candles:
        return None, None
    if len(candles) < slow + 5:
        return None, None
    fv = calculate_atr(candles, fast)
    sv = calculate_atr(candles, slow)
    return fv, sv


def _swing_high(c, i):
    if i < 2 or i >= len(c) - 2:
        return False
    cur = _h(c[i])
    l1 = _h(c[i - 1])
    l2 = _h(c[i - 2])
    r1 = _h(c[i + 1])
    r2 = _h(c[i + 2])
    if any(x is None for x in (cur, l1, l2, r1, r2)):
        return False
    return cur > l1 and cur >= l2 and cur >= r1 and cur > r2


def _swing_low(c, i):
    if i < 2 or i >= len(c) - 2:
        return False
    cur = _l(c[i])
    l1 = _l(c[i - 1])
    l2 = _l(c[i - 2])
    r1 = _l(c[i + 1])
    r2 = _l(c[i + 2])
    if any(x is None for x in (cur, l1, l2, r1, r2)):
        return False
    return cur < l1 and cur <= l2 and cur <= r1 and cur < r2


def _swing_highs(c):
    r = []
    if not c:
        return r
    for i in range(len(c)):
        if _swing_high(c, i):
            p = _h(c[i])
            if p is not None:
                r.append((i, p))
    return r


def _swing_lows(c):
    r = []
    if not c:
        return r
    for i in range(len(c)):
        if _swing_low(c, i):
            p = _l(c[i])
            if p is not None:
                r.append((i, p))
    return r


def get_1h_direction(candles):
    if not candles:
        return "NEUTRAL"
    if len(candles) < 15:
        return "NEUTRAL"
    candles = candles[-60:]
    highs = _swing_highs(candles)
    lows = _swing_lows(candles)
    bull = False
    bear = False
    if len(highs) >= 2 and len(lows) >= 2:
        bull = (highs[-1][1] > highs[-2][1]
                and lows[-1][1] > lows[-2][1])
        bear = (highs[-1][1] < highs[-2][1]
                and lows[-1][1] < lows[-2][1])
    if not bull and not bear:
        recent = candles[-8:]
        bb = 0.0
        sb = 0.0
        for c in recent:
            if _bull(c):
                bb += _body(c)
            if _bear(c):
                sb += _body(c)
        if bb > 0 and bb > sb * 1.4:
            bull = True
        elif sb > 0 and sb > bb * 1.4:
            bear = True
    if bull and not bear:
        return "LONG"
    if bear and not bull:
        return "SHORT"
    return "NEUTRAL"


def get_higher_tf_direction(c1h, c1d=None, c1w=None):
    return get_1h_direction(c1h)


def _level_price(l):
    if isinstance(l, dict):
        return _f(l.get("price"))
    return _f(l)


def _level_side(l):
    if not isinstance(l, dict):
        return None
    side = l.get("side") or l.get("direction") or ""
    return str(side).upper()


def _level_type(l):
    if not isinstance(l, dict):
        return ""
    return str(l.get("type") or "").upper()


def _level_strength(l):
    if not isinstance(l, dict):
        return 0
    v = l.get("strength") or l.get("touches") or 0
    try:
        return float(v)
    except Exception:
        return 0


def _is_swept_level(l):
    if not isinstance(l, dict):
        return False
    return bool(l.get("swept")
                or l.get("taken")
                or l.get("used")
                or l.get("consumed"))


def _levels_for_dir(levels, direction):
    res = []
    exp = "SSL" if direction == "LONG" else "BSL"
    for lv in levels or []:
        price = _level_price(lv)
        if price is None:
            continue
        side = _level_side(lv)
        lt = _level_type(lv)
        if side == direction:
            res.append(lv)
        elif lt == exp:
            res.append(lv)
        elif lt.startswith(exp + "_"):
            res.append(lv)
    return res


def is_inside_fvg(price, fvgs, direction,
                  tol=FVG_TOLERANCE_PCT):
    p = _f(price)
    if p is None or not fvgs:
        return False
    exp = "bullish" if direction == "LONG" else "bearish"
    for fvg in fvgs:
        if fvg.get("type") != exp:
            continue
        top = _f(fvg.get("top"))
        bottom = _f(fvg.get("bottom"))
        if top is None or bottom is None:
            continue
        t = top * tol / 100
        if (bottom - t) <= p <= (top + t):
            return True
    return False


def compute_fvg_bonus(sweep, entry, fvgs, direction):
    if not fvgs:
        return 0, False, False
    se = _f((sweep or {}).get("extreme"))
    ep = _f(entry)
    si = False
    if se is not None:
        si = is_inside_fvg(se, fvgs, direction)
    ei = False
    if ep is not None:
        ei = is_inside_fvg(ep, fvgs, direction)
    b = 0
    if si:
        b += FVG_SWEEP_BONUS
    if ei:
        b += FVG_ENTRY_BONUS
    return min(b, FVG_MAX_BONUS), si, ei


def _sweep_cand_score(candle, level, depth):
    strength = _level_strength(level)
    touches = 1
    if isinstance(level, dict):
        touches = level.get("touches", 1)
    return (depth * 4.0
            + strength / 20.0
            + min(touches, 5) * 3.0)


def _has_vol_conf(candles, idx,
                  lookback=VOLUME_CONFIRMATION_LOOKBACK,
                  mult=VOLUME_CONFIRMATION_MULT):
    if not candles:
        return True
    if idx < 0 or idx >= len(candles):
        return True
    if idx < lookback:
        return True
    start = idx - lookback
    vols = []
    for i in range(start, idx):
        v = _vol(candles[i])
        if v > 0:
            vols.append(v)
    if not vols:
        return True
    avg = sum(vols) / len(vols)
    if avg <= 0:
        return True
    cur = _vol(candles[idx])
    if cur <= 0:
        return True
    return cur >= avg * mult


def find_sweep(candles_1h, major_levels, direction):
    if direction not in ("LONG", "SHORT"):
        return None
    if not candles_1h:
        return None
    if len(candles_1h) < 3:
        return None

    levels = _levels_for_dir(major_levels, direction)
    if not levels:
        return None

    recent = candles_1h[-MAX_SWEEP_AGE_1H:]
    candidates = []
    total = len(candles_1h)

    for idx in range(len(recent)):
        c = recent[len(recent) - 1 - idx]
        cidx = total - 1 - idx

        if VOLUME_CONFIRMATION_ENABLED:
            if not _has_vol_conf(candles_1h, cidx):
                continue

        for level in levels:
            if _is_swept_level(level):
                continue
            price = _level_price(level)
            if price is None:
                continue

            if direction == "LONG":
                low = _l(c)
                close = _c(c)
                if low is None or close is None:
                    continue
                depth = (price - low) / price * 100
                if depth < MIN_SWEEP_DEPTH_PCT:
                    continue
                if not (low < price and close > price):
                    continue
                op = _o(c) or close
                body = abs(close - op)
                wick = min(op, close) - low
                rej = wick > body or _bull(c)
                if not rej:
                    continue

                inv = False
                consec = 0
                for k in range(cidx + 1, total):
                    ca = candles_1h[k]
                    cc = _c(ca)
                    if cc is not None and cc < low:
                        consec += 1
                        if consec >= 2:
                            inv = True
                            break
                    else:
                        consec = 0
                if inv:
                    continue

                t = level.get("touches", 1)
                s = level.get("strength", 0)
                candidates.append({
                    "swept": True,
                    "direction": "LONG",
                    "level": price,
                    "extreme": low,
                    "open_time": _t(c),
                    "price": low,
                    "liquidity_type": "SSL",
                    "touches": t,
                    "strength": s,
                    "depth_pct": depth,
                    "_score": (_sweep_cand_score(c, level, depth)
                               - idx * 2.0),
                })
            else:
                high = _h(c)
                close = _c(c)
                if high is None or close is None:
                    continue
                depth = (high - price) / price * 100
                if depth < MIN_SWEEP_DEPTH_PCT:
                    continue
                if not (high > price and close < price):
                    continue
                op = _o(c) or close
                body = abs(close - op)
                wick = high - max(op, close)
                rej = wick > body or _bear(c)
                if not rej:
                    continue

                inv = False
                consec = 0
                for k in range(cidx + 1, total):
                    ca = candles_1h[k]
                    cc = _c(ca)
                    if cc is not None and cc > high:
                        consec += 1
                        if consec >= 2:
                            inv = True
                            break
                    else:
                        consec = 0
                if inv:
                    continue

                t = level.get("touches", 1)
                s = level.get("strength", 0)
                candidates.append({
                    "swept": True,
                    "direction": "SHORT",
                    "level": price,
                    "extreme": high,
                    "open_time": _t(c),
                    "price": high,
                    "liquidity_type": "BSL",
                    "touches": t,
                    "strength": s,
                    "depth_pct": depth,
                    "_score": (_sweep_cand_score(c, level, depth)
                               - idx * 2.0),
                })

    if not candidates:
        return None
    best = None
    best_score = -1e9
    for cand in candidates:
        sc = cand["_score"]
        if sc > best_score:
            best_score = sc
            best = cand
    if best is not None:
        best.pop("_score", None)
    return best


def measure_trend_activity(candles_1h, direction):
    if not candles_1h:
        return 0.0
    if len(candles_1h) < 15:
        return 0.0
    recent = candles_1h[-20:]
    total = 0.0
    direc = 0.0
    for c in recent:
        b = _body(c)
        total += b
        if direction == "LONG" and _bull(c):
            direc += b
        elif direction == "SHORT" and _bear(c):
            direc += b
    if total > 0:
        return direc / total
    return 0.0


def _is_local_high_15m(c, i):
    if i < 1 or i >= len(c) - 1:
        return False
    cur = _h(c[i])
    l = _h(c[i - 1])
    r = _h(c[i + 1])
    if cur is None or l is None or r is None:
        return False
    return cur >= l and cur > r


def _is_local_low_15m(c, i):
    if i < 1 or i >= len(c) - 1:
        return False
    cur = _l(c[i])
    l = _l(c[i - 1])
    r = _l(c[i + 1])
    if cur is None or l is None or r is None:
        return False
    return cur <= l and cur < r


def confirmation_15m(candles_15m, sweep, direction):
    if not sweep or not candles_15m:
        return False, None, None, False
    if direction not in ("LONG", "SHORT"):
        return False, None, None, False

    sweep_t = _f(sweep.get("open_time"))
    candidates = []
    for c in candles_15m:
        t = _t(c)
        if sweep_t is None:
            candidates.append(c)
        elif t is not None and t > sweep_t:
            candidates.append(c)

    candidates = candidates[-MAX_15M_CONFIRM_CANDLES:]
    if len(candidates) < 3:
        return False, None, None, False

    fallback = None

    for i in range(1, len(candidates)):
        c = candidates[i]
        if _body_ratio(c) < MIN_BODY_RATIO:
            continue
        close = _c(c)
        if close is None:
            continue
        prev = candidates[i - 1]
        prev_h = _h(prev)
        prev_l = _l(prev)
        prev_b = _body(prev)

        if direction == "LONG":
            if not _bull(c):
                continue
            highs = []
            for j in range(i - 1):
                if _is_local_high_15m(candidates, j):
                    h = _h(candidates[j])
                    if h is not None:
                        highs.append(h)
            if not highs:
                continue
            ref = max(highs[-3:])
            bos = close > ref
            if bos:
                return (True, "15M BOS",
                        _t(c), True)
            engulf = (prev_h is not None
                      and close > prev_h
                      and _body(c) > prev_b)
            if engulf and fallback is None:
                fallback = (True, "15M engulf",
                            _t(c), False)
        else:
            if not _bear(c):
                continue
            lows = []
            for j in range(i - 1):
                if _is_local_low_15m(candidates, j):
                    lo = _l(candidates[j])
                    if lo is not None:
                        lows.append(lo)
            if not lows:
                continue
            ref = min(lows[-3:])
            bos = close < ref
            if bos:
                return (True, "15M BOS",
                        _t(c), True)
            engulf = (prev_l is not None
                      and close < prev_l
                      and _body(c) > prev_b)
            if engulf and fallback is None:
                fallback = (True, "15M engulf",
                            _t(c), False)

    if fallback is not None:
        return fallback
    return False, None, None, False


def _is_local_high(c, i):
    if i < 1 or i >= len(c) - 1:
        return False
    cur = _h(c[i])
    l = _h(c[i - 1])
    r = _h(c[i + 1])
    if cur is None or l is None or r is None:
        return False
    return cur >= l and cur > r


def _is_local_low(c, i):
    if i < 1 or i >= len(c) - 1:
        return False
    cur = _l(c[i])
    l = _l(c[i - 1])
    r = _l(c[i + 1])
    if cur is None or l is None or r is None:
        return False
    return cur <= l and cur < r


def _ilm_long(candles, i, sweep_lvl, sweep_ext):
    m = candles[i]
    if not _is_local_low(candles, i):
        return None
    ml = _l(m)
    mh = _h(m)
    if ml is None or mh is None:
        return None
    before = candles[max(0, i - 2):i]
    if not before:
        return None
    bl = []
    for x in before:
        v = _l(x)
        if v is not None:
            bl.append(v)
    if not bl:
        return None
    left_ref = min(bl)
    if left_ref <= ml:
        return None
    m_range = left_ref - ml
    if m_range <= 0:
        return None
    m_pct = m_range / left_ref * 100
    if m_pct < MIN_SWEEP_DEPTH_PCT:
        return None

    trig_idx = None
    end = min(len(candles), i + 1 + ILM_TRIGGER_WINDOW)
    for j in range(i + 1, end):
        trig = candles[j]
        tc = _c(trig)
        if (tc is not None
                and _bull(trig)
                and _body_ratio(trig)
                >= MIN_BODY_RATIO_TRIGGER_5M
                and tc > mh):
            trig_idx = j
            break
    if trig_idx is None:
        return None
    trig = candles[trig_idx]
    tc = _c(trig)
    if tc is None:
        return None
    rec = (tc - ml) / m_range
    if rec < MIN_5M_RECOVERY_RATIO:
        return None
    if sweep_lvl is not None:
        d = abs(ml - sweep_lvl) / sweep_lvl * 100
        if d > MIN_5M_ILM_SWEEP_DISTANCE_PCT:
            return None
    if sweep_ext is not None:
        lim = sweep_ext * (1
            + MIN_5M_ILM_SWEEP_DISTANCE_PCT / 100)
        if ml > lim:
            return None
    return {
        "direction": "LONG",
        "extreme": ml,
        "trigger_time": _t(trig),
        "trigger_price": tc,
        "reason": "5M V-ILM",
        "recovery_ratio": rec,
        "manipulation_pct": m_pct,
        "age_candles": len(candles) - 1 - trig_idx,
        "_score": (rec * 30
                   + _body_ratio(trig) * 20
                   + m_pct * 5),
    }


def _ilm_short(candles, i, sweep_lvl, sweep_ext):
    m = candles[i]
    if not _is_local_high(candles, i):
        return None
    mh = _h(m)
    ml = _l(m)
    if mh is None or ml is None:
        return None
    before = candles[max(0, i - 2):i]
    if not before:
        return None
    bh = []
    for x in before:
        v = _h(x)
        if v is not None:
            bh.append(v)
    if not bh:
        return None
    left_ref = max(bh)
    if mh <= left_ref:
        return None
    m_range = mh - left_ref
    if m_range <= 0:
        return None
    m_pct = m_range / left_ref * 100
    if m_pct < MIN_SWEEP_DEPTH_PCT:
        return None

    trig_idx = None
    end = min(len(candles), i + 1 + ILM_TRIGGER_WINDOW)
    for j in range(i + 1, end):
        trig = candles[j]
        tc = _c(trig)
        if (tc is not None
                and _bear(trig)
                and _body_ratio(trig)
                >= MIN_BODY_RATIO_TRIGGER_5M
                and tc < ml):
            trig_idx = j
            break
    if trig_idx is None:
        return None
    trig = candles[trig_idx]
    tc = _c(trig)
    if tc is None:
        return None
    rec = (mh - tc) / m_range
    if rec < MIN_5M_RECOVERY_RATIO:
        return None
    if sweep_lvl is not None:
        d = abs(mh - sweep_lvl) / sweep_lvl * 100
        if d > MIN_5M_ILM_SWEEP_DISTANCE_PCT:
            return None
    if sweep_ext is not None:
        lim = sweep_ext * (1
            - MIN_5M_ILM_SWEEP_DISTANCE_PCT / 100)
        if mh < lim:
            return None
    return {
        "direction": "SHORT",
        "extreme": mh,
        "trigger_time": _t(trig),
        "trigger_price": tc,
        "reason": "5M L-ILM",
        "recovery_ratio": rec,
        "manipulation_pct": m_pct,
        "age_candles": len(candles) - 1 - trig_idx,
        "_score": (rec * 30
                   + _body_ratio(trig) * 20
                   + m_pct * 5),
    }


def detect_5m_ilm(candles_5m, sweep, direction,
                   conf_time=None):
    if not sweep:
        return False, None
    if direction not in ("LONG", "SHORT"):
        return False, None

    start = _f(conf_time) or _f(sweep.get("open_time"))
    candles = []
    for c in candles_5m or []:
        t = _t(c)
        if start is None:
            candles.append(c)
        elif t is not None and t > start:
            candles.append(c)

    candles = candles[-MAX_5M_ILM_CANDLES:]
    if len(candles) < 5:
        return False, None

    sl = _f(sweep.get("level"))
    se = _f(sweep.get("extreme"))

    cands = []
    for i in range(2, len(candles) - 2):
        if direction == "LONG":
            ilm = _ilm_long(candles, i, sl, se)
        else:
            ilm = _ilm_short(candles, i, sl, se)
        if ilm:
            cands.append(ilm)

    if not cands:
        return False, None
    best = None
    best_score = -1e9
    for cand in cands:
        sc = cand["_score"]
        if sc > best_score:
            best_score = sc
            best = cand
    if best is not None:
        best.pop("_score", None)
    return True, best


def calculate_entry(ilm, price, direction):
    if not ilm:
        return None
    trig = _f(ilm.get("trigger_price"))
    if trig is None or trig <= 0:
        return None
    offset = RETEST_OFFSET_PCT / 100.0
    if offset <= 0:
        return trig
    if direction == "LONG":
        return trig * (1 - offset)
    if direction == "SHORT":
        return trig * (1 + offset)
    return trig


def find_structural_sl(candles_15m, direction, entry,
                        ilm_ext, sweep_ext=None,
                        candles_1h=None):
    ef = _f(entry)
    if ef is None:
        return ilm_ext
    cands = []

    if candles_15m:
        if len(candles_15m) >= 10:
            w = candles_15m[-STRUCTURAL_SL_LOOKBACK_15M:]
            if direction == "LONG":
                for _, p in _swing_lows(w):
                    cands.append(p)
            elif direction == "SHORT":
                for _, p in _swing_highs(w):
                    cands.append(p)

    if SL_USE_1H_SWINGS and candles_1h:
        if len(candles_1h) >= 20:
            w1 = candles_1h[-60:]
            if direction == "LONG":
                for _, p in _swing_lows(w1):
                    cands.append(p)
            elif direction == "SHORT":
                for _, p in _swing_highs(w1):
                    cands.append(p)

    se = None
    if SL_USE_SWEEP_EXTREME:
        se = _f(sweep_ext)
        if se is not None:
            cands.append(se)
    ie = _f(ilm_ext)
    if ie is not None:
        cands.append(ie)

    cands = [c for c in cands if c is not None]
    if not cands:
        return ilm_ext

    if direction == "LONG":
        if se is not None and se < ef:
            return se
        below = [c for c in cands if c < ef]
        if not below:
            return ilm_ext
        return max(below)
    if direction == "SHORT":
        if se is not None and se > ef:
            return se
        above = [c for c in cands if c > ef]
        if not above:
            return ilm_ext
        return min(above)
    return ilm_ext


def calculate_stop(entry, struct_level, direction, atr=None):
    entry = _f(entry)
    level = _f(struct_level)
    if entry is None or level is None:
        return None

    if USE_ATR_SCALING and atr is not None and atr > 0:
        min_d = atr * ATR_SL_MULT_SOFT
        max_d = atr * ATR_SL_MAX_MULT
    else:
        min_d = entry * MIN_SL_DISTANCE_PCT / 100
        max_d = entry * MAX_SL_DISTANCE_PCT / 100

    if direction == "LONG":
        sl = level * (1 - SL_BUFFER_PCT / 100)
        if (entry - sl) < min_d:
            sl = entry - min_d
        if (entry - sl) > max_d:
            sl = entry - max_d
        if sl < entry:
            return sl
        return None
    if direction == "SHORT":
        sl = level * (1 + SL_BUFFER_PCT / 100)
        if (sl - entry) < min_d:
            sl = entry + min_d
        if (sl - entry) > max_d:
            sl = entry + max_d
        if sl > entry:
            return sl
        return None
    return None


def calculate_tp_by_rr(entry, sl, direction, rr=FIXED_RR):
    entry = _f(entry)
    sl = _f(sl)
    if entry is None or sl is None:
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    if direction == "LONG":
        return entry + rr * risk
    if direction == "SHORT":
        return entry - rr * risk
    return None


def calculate_rr(entry, sl, tp):
    entry = _f(entry)
    sl = _f(sl)
    tp = _f(tp)
    if entry is None or sl is None or tp is None:
        return None
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk <= 0:
        return None
    return reward / risk


def validate_geometry(entry, sl, tp, direction):
    entry = _f(entry)
    sl = _f(sl)
    tp = _f(tp)
    if entry is None or sl is None or tp is None:
        return False
    if direction == "LONG":
        return sl < entry < tp
    if direction == "SHORT":
        return tp < entry < sl
    return False


def _score(direction, ctx_dir, sweep, conf_str, bos, ilm,
           rr, maj_str, fvg_bonus):
    score = 0
    if direction == ctx_dir:
        score += 15
    elif ctx_dir == "NEUTRAL":
        score += 8
    else:
        score += 5

    if sweep:
        depth = sweep.get("depth_pct", 0)
        if depth >= 0.40:
            score += 20
        elif depth >= 0.25:
            score += 15
        elif depth >= 0.15:
            score += 10
        else:
            score += 6

    if conf_str >= 0.75:
        score += 15
    elif conf_str >= 0.50:
        score += 11
    elif conf_str > 0:
        score += 7

    if bos:
        score += 10

    if ilm:
        rec = ilm.get("recovery_ratio", 0)
        if rec >= 0.66:
            score += 15
        elif rec >= 0.50:
            score += 11
        else:
            score += 7

    if rr is not None and rr >= FIXED_RR:
        score += 15

    score += min(10, maj_str / 10.0)
    score += fvg_bonus
    return int(min(100, max(0, round(score))))


def _apply_ready_promote(result):
    if result.get("stage") != "15M_CONFIRMED":
        return result
    reason = result.get("reason", "")
    if "READY заблокирован" not in reason:
        return result
    if any(result.get(k) is None
           for k in ("entry", "sl", "tp", "rr")):
        return result

    score = int(result.get("score", 0))
    trend = float(result.get("trend_activity", 0.0))
    bos = bool(result.get("bos", False))

    for sc_min, tr_min, need_bos in READY_PROMOTE_TIERS:
        if score < sc_min:
            continue
        if trend < tr_min:
            continue
        if need_bos and not bos:
            continue
        result["stage"] = "READY"
        result["reason"] = (
            f"v9.19 promote: score={score} "
            f"trend={trend:.2f} bos={bos}"
        )
        result["_v910_promoted"] = True
        return result
    return result


def _analyze_scenario(c1h, c15, c5, price,
                      levels, direction, ctx_dir,
                      d1_context=None, fvgs=None,
                      symbol=None):
    result = {
        "stage": "WAIT",
        "direction": direction,
        "score": 0,
        "reason": "",
        "entry": None,
        "sl": None,
        "tp": None,
        "tp_source": "fixed_rr",
        "rr": None,
        "sweep": None,
        "major_levels": levels or [],
        "confirmation_15m": False,
        "confirmation_15m_time": None,
        "confirmation": None,
        "bos": False,
        "ilm": None,
        "sweep_extreme": None,
        "tp_reason": None,
        "geometry_valid": False,
        "trend_activity": 0.0,
        "fvg_bonus": 0,
        "fvg_sweep": False,
        "fvg_entry": False,
        "sl_distance_pct": None,
        "atr_15m": None,
        "sl_source": None,
    }

    if direction == "SHORT" and not ALLOW_SHORT:
        result["reason"] = "SHORT disabled"
        return result

    price = _f(price)
    if price is None or not c1h or not c15 or not c5:
        result["reason"] = "Недостаточно данных."
        return result

    trend = measure_trend_activity(c1h, direction)
    result["trend_activity"] = round(trend, 3)

    lv = _levels_for_dir(levels, direction)
    if not lv:
        result["score"] = 20
        t = "SSL" if direction == "LONG" else "BSL"
        result["reason"] = f"Нет Major {t}."
        return result

    sweep = find_sweep(c1h, lv, direction)
    result["sweep"] = sweep

    if sweep is None:
        result["score"] = 25
        t = "SSL sweep" if direction == "LONG" else "BSL sweep"
        result["reason"] = f"Ждём {t}."
        return result

    result["stage"] = "SWEPT"
    result["sweep_extreme"] = sweep.get("extreme")

    conf_ok, conf_text, conf_t, bos = confirmation_15m(
        c15, sweep, direction)

    result["confirmation_15m"] = conf_ok
    result["confirmation_15m_time"] = conf_t
    result["confirmation"] = conf_text
    result["bos"] = bos

    if not conf_ok:
        result["score"] = 50
        result["reason"] = "Ждём 15M."
        return result

    result["stage"] = "15M_CONFIRMED"

    ilm_ok, ilm = detect_5m_ilm(c5, sweep, direction,
                                 conf_t)
    result["ilm"] = ilm

    if not ilm_ok:
        result["score"] = 65
        result["reason"] = "Ждём 5M ILM."
        return result

    age = int(ilm.get("age_candles", 0))
    if age > MAX_ILM_AGE_CANDLES_5M:
        result["score"] = 65
        result["reason"] = f"ILM устарел ({age})"
        return result

    if age > MAX_ILM_AGE_FOR_ENTRY:
        result["score"] = 68
        result["reason"] = (
            f"ILM старый ({age} > "
            f"{MAX_ILM_AGE_FOR_ENTRY})"
        )
        return result

    entry = calculate_entry(ilm, price, direction)
    if entry is None:
        result["score"] = 68
        result["reason"] = "Нет Entry."
        return result

    dist = _dist_pct(price, entry)
    if dist is None or dist > ENTRY_TOLERANCE_PCT:
        result["score"] = 68
        result["reason"] = (
            f"Цена ушла на {dist:.2f}%"
        )
        return result

    ilm_ext = _f(ilm.get("extreme"))

    if ENABLE_VOLATILITY_FILTER:
        fa, sa = _avg_atr(c15, fast=14, slow=50)
        if (fa is not None and sa is not None
                and sa > 0
                and fa > sa * VOLATILITY_ATR_SPIKE_MULT):
            result["score"] = 70
            result["reason"] = "Volatility spike"
            return result

    atr_15m = calculate_atr(c15, 14)

    struct_lvl = find_structural_sl(
        c15, direction, entry, ilm_ext,
        sweep_ext=sweep.get("extreme") if sweep else None,
        candles_1h=c1h,
    )

    sl = calculate_stop(entry, struct_lvl, direction,
                        atr=atr_15m)
    if sl is None:
        result["score"] = 68
        result["reason"] = "Нет SL."
        return result

    tp = calculate_tp_by_rr(entry, sl, direction, FIXED_RR)
    if tp is None:
        result["score"] = 70
        result["reason"] = "Нет TP."
        return result

    if not validate_geometry(entry, sl, tp, direction):
        result["score"] = 68
        result["reason"] = "Геометрия сломана."
        return result

    result["geometry_valid"] = True

    rr = calculate_rr(entry, sl, tp)
    if rr is None:
        result["score"] = 68
        result["reason"] = "Нет RR."
        return result

    result.update({
        "entry": round(entry, 8),
        "sl": round(sl, 8),
        "tp": round(tp, 8),
        "rr": round(rr, 3),
        "tp_reason": f"Fixed RR 1:{FIXED_RR}",
    })

    try:
        sdp = abs(entry - sl) / entry * 100
        result["sl_distance_pct"] = round(sdp, 3)
        if atr_15m:
            result["atr_15m"] = round(atr_15m, 6)
        if atr_15m:
            if abs(entry - sl) < atr_15m * ATR_SL_MULT_SOFT:
                result["sl_source"] = "atr_floor"
            else:
                result["sl_source"] = "structural"
    except Exception:
        pass

    try:
        ms = max([_level_strength(l) for l in lv] or [0])
    except Exception:
        ms = 0

    fb, fs, fe = compute_fvg_bonus(
        sweep, entry, fvgs or [], direction)
    result["fvg_bonus"] = fb
    result["fvg_sweep"] = fs
    result["fvg_entry"] = fe

    conf_str = 0.8 if conf_ok else 0.6

    score = _score(
        direction=direction,
        ctx_dir=ctx_dir,
        sweep=sweep,
        conf_str=conf_str,
        bos=bos,
        ilm=ilm,
        rr=rr,
        maj_str=ms,
        fvg_bonus=fb,
    )
    result["score"] = score

    trend_ok = trend >= MIN_TREND_ACTIVITY_READY
    bos_ok = (not REQUIRE_BOS_FOR_READY) or bos

    ready_ok = (score >= MIN_SCORE_READY
                and trend_ok and bos_ok)

    if ready_ok:
        result["stage"] = "READY"
        result["reason"] = (
            f"Sweep→15M→5M ILM. "
            f"Trend {trend:.2f}. RR {FIXED_RR}. BOS."
        )
        return result

    result["stage"] = "15M_CONFIRMED"
    blocks = []
    if score < MIN_SCORE_READY:
        blocks.append(f"score {score}")
    if not trend_ok:
        blocks.append(f"trend {trend:.2f}")
    if not bos_ok:
        blocks.append("no_bos")
    result["reason"] = ("READY заблокирован: "
                        + ", ".join(blocks))
    result = _apply_ready_promote(result)
    return result


def analyze(candles_1h, candles_15m, candles_5m,
            current_price, major_levels=None,
            sweep=None, order_flow=None,
            candles_1m=None, d1_context=None,
            fvgs=None, symbol=None):
    price = _f(current_price)
    ctx_dir = get_1h_direction(candles_1h)

    base = {
        "stage": "WAIT",
        "direction": ctx_dir,
        "context_direction": ctx_dir,
        "d1_trend": (d1_context or {}).get("trend",
                                            "NEUTRAL"),
        "d1_point_a": (d1_context or {}).get("point_a"),
        "d1_point_b": (d1_context or {}).get("point_b"),
        "score": 0,
        "reason": "",
        "entry": None,
        "sl": None,
        "tp": None,
        "tp_source": "fixed_rr",
        "rr": None,
        "sweep": None,
        "major_levels": major_levels or [],
        "confirmation_15m": False,
        "confirmation_15m_time": None,
        "confirmation": None,
        "bos": False,
        "ilm": None,
        "sweep_extreme": None,
        "tp_reason": None,
        "geometry_valid": False,
        "trend_activity": 0.0,
        "fvg_bonus": 0,
        "fvg_sweep": False,
        "fvg_entry": False,
        "long": None,
        "short": None,
    }

    if price is None:
        base["reason"] = "Недостаточно данных."
        return base
    if not candles_1h or not candles_15m or not candles_5m:
        base["reason"] = "Недостаточно данных."
        return base

    lr = _analyze_scenario(
        candles_1h, candles_15m, candles_5m, price,
        major_levels, "LONG", ctx_dir,
        d1_context=d1_context, fvgs=fvgs,
        symbol=symbol)

    sr = _analyze_scenario(
        candles_1h, candles_15m, candles_5m, price,
        major_levels, "SHORT", ctx_dir,
        d1_context=d1_context, fvgs=fvgs,
        symbol=symbol)

    base["long"] = lr
    base["short"] = sr

    if ctx_dir == "NEUTRAL":
        if lr.get("score", 0) >= sr.get("score", 0):
            best = lr
        else:
            best = sr
        stage = best.get("stage", "WAIT")
        if stage == "READY":
            stage = "WAIT"
        base.update({
            "stage": stage,
            "direction": "NEUTRAL",
            "score": best.get("score", 0),
            "reason": "1H NEUTRAL",
            "entry": best.get("entry"),
            "sl": best.get("sl"),
            "tp": best.get("tp"),
            "tp_source": best.get("tp_source"),
            "rr": best.get("rr"),
            "sweep": best.get("sweep"),
            "confirmation_15m": best.get(
                "confirmation_15m", False),
            "confirmation_15m_time": best.get(
                "confirmation_15m_time"),
            "confirmation": best.get("confirmation"),
            "bos": best.get("bos", False),
            "ilm": best.get("ilm"),
            "sweep_extreme": best.get("sweep_extreme"),
            "tp_reason": best.get("tp_reason"),
            "geometry_valid": best.get(
                "geometry_valid", False),
            "trend_activity": best.get(
                "trend_activity", 0.0),
            "fvg_bonus": best.get("fvg_bonus", 0),
            "fvg_sweep": best.get("fvg_sweep", False),
            "fvg_entry": best.get("fvg_entry", False),
        })
        return base

    ready = []
    if lr.get("stage") == "READY":
        if lr.get("score", 0) >= MIN_SCORE_READY:
            ready.append(lr)
    if sr.get("stage") == "READY":
        if sr.get("score", 0) >= MIN_SCORE_READY:
            ready.append(sr)

    if ready:
        aligned = []
        counter = []
        for x in ready:
            if x["direction"] == ctx_dir:
                aligned.append(x)
            else:
                counter.append(x)

        chosen = None
        if aligned:
            best_sc = -1
            for a in aligned:
                if a["score"] > best_sc:
                    best_sc = a["score"]
                    chosen = a
        elif counter:
            cr = []
            for x in counter:
                if x["score"] >= COUNTER_TREND_MIN_SCORE:
                    cr.append(x)
            if not cr:
                base["score"] = max(lr["score"],
                                     sr["score"])
                base["reason"] = "Counter-тренд слаб."
                return base
            best_sc = -1
            for x in cr:
                if x["score"] > best_sc:
                    best_sc = x["score"]
                    chosen = x

        if chosen:
            base.update(chosen)
            base["context_direction"] = ctx_dir
            base["long"] = lr
            base["short"] = sr
            return base

    candidates = [lr, sr]

    def stage_wt(r):
        m = {
            "READY": 5, "15M_CONFIRMED": 4,
            "SWEPT": 3, "WAIT": 1,
        }
        return m.get(r.get("stage"), 0)

    aligned_c = []
    for x in candidates:
        if x["direction"] == ctx_dir:
            aligned_c.append(x)

    if aligned_c:
        pool = aligned_c
    else:
        pool = candidates

    chosen = None
    best_k = None
    for x in pool:
        k = (stage_wt(x), x.get("score", 0))
        if best_k is None or k > best_k:
            best_k = k
            chosen = x

    if chosen is not None:
        base.update({
            "stage": chosen.get("stage", "WAIT"),
            "direction": chosen.get("direction", ctx_dir),
            "score": chosen.get("score", 0),
            "reason": chosen.get("reason", ""),
            "entry": chosen.get("entry"),
            "sl": chosen.get("sl"),
            "tp": chosen.get("tp"),
            "tp_source": chosen.get("tp_source"),
            "rr": chosen.get("rr"),
            "sweep": chosen.get("sweep"),
            "confirmation_15m": chosen.get(
                "confirmation_15m", False),
            "confirmation_15m_time": chosen.get(
                "confirmation_15m_time"),
            "confirmation": chosen.get("confirmation"),
            "bos": chosen.get("bos", False),
            "ilm": chosen.get("ilm"),
            "sweep_extreme": chosen.get("sweep_extreme"),
            "tp_reason": chosen.get("tp_reason"),
            "geometry_valid": chosen.get(
                "geometry_valid", False),
            "trend_activity": chosen.get(
                "trend_activity", 0.0),
            "fvg_bonus": chosen.get("fvg_bonus", 0),
            "fvg_sweep": chosen.get("fvg_sweep", False),
            "fvg_entry": chosen.get("fvg_entry", False),
        })
    base["context_direction"] = ctx_dir
    return base


def analyze_sol(*args, **kwargs):
    return analyze(*args, **kwargs)


__all__ = [
    "STRATEGY_VERSION",
    "ALLOW_SHORT",
    "MIN_SCORE_READY",
    "REQUIRE_BOS_FOR_READY",
    "FIXED_RR",
    "SL_BUFFER_PCT",
    "MIN_SWEEP_DEPTH_PCT",
    "USE_ATR_SCALING",
    "MAX_ILM_AGE_FOR_ENTRY",
    "ATR_SL_MULT_SOFT",
    "RETEST_OFFSET_PCT",
    "calculate_atr",
    "get_1h_direction",
    "get_higher_tf_direction",
    "measure_trend_activity",
    "find_sweep",
    "confirmation_15m",
    "detect_5m_ilm",
    "calculate_entry",
    "calculate_stop",
    "calculate_tp_by_rr",
    "calculate_rr",
    "find_structural_sl",
    "validate_geometry",
    "analyze",
    "analyze_sol",
]