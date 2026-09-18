"""
TradeMind 6.9

Изменения vs 6.8:
- УБРАНЫ торговые сессии. Бот работает 24/7.
- Добавлен FVG (imbalance) как бонус к score:
  * sweep extreme внутри FVG +10
  * entry внутри FVG +5
  * максимум +15
- Поля result: fvg_bonus, fvg_sweep, fvg_entry
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


STRATEGY_VERSION = "6.9"


MIN_SCORE_READY = 80
MIN_RR = 2.0
SL_BUFFER_PCT = 0.20
MIN_SWEEP_DEPTH_PCT = 0.15
MIN_5M_RECOVERY_RATIO = 0.33
MIN_V_RECOVERY_FOR_READY = 0.45
MIN_BODY_RATIO = 0.35
MAX_SWEEP_AGE_1H = 8
MAX_5M_ILM_CANDLES = 40
MAX_15M_CONFIRM_CANDLES = 12
MIN_5M_ILM_SWEEP_DISTANCE_PCT = 0.75
MIN_TARGET_DISTANCE_PCT = 0.30
COUNTER_TREND_MIN_SCORE = 90

MIN_TREND_ACTIVITY_READY = 0.45

FALLBACK_5M_LOOKBACK = 60
FALLBACK_15M_LOOKBACK = 60
MIN_FALLBACK_DISTANCE_PCT = 0.30

# ---------- FVG ----------
FVG_TOLERANCE_PCT = 0.10
FVG_SWEEP_BONUS = 10
FVG_ENTRY_BONUS = 5
FVG_MAX_BONUS = 15


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
        aliases = {"open": "o", "high": "h", "low": "l", "close": "c", "open_time": "time"}
        alias = aliases.get(key)
        if alias:
            value = candle.get(alias)
    if value is None:
        return default
    converted = _f(value)
    return converted if converted is not None else default


def _o(c): return _v(c, "open")
def _h(c): return _v(c, "high")
def _l(c): return _v(c, "low")
def _c(c): return _v(c, "close")
def _t(c): return _v(c, "open_time")


def _body(c):
    o, cl = _o(c), _c(c)
    if o is None or cl is None: return 0.0
    return abs(cl - o)


def _range(c):
    h, l = _h(c), _l(c)
    if h is None or l is None: return 0.0
    return max(0.0, h - l)


def _body_ratio(c):
    r = _range(c)
    return _body(c) / r if r > 0 else 0.0


def _bull(c):
    o, cl = _o(c), _c(c)
    return o is not None and cl is not None and cl > o


def _bear(c):
    o, cl = _o(c), _c(c)
    return o is not None and cl is not None and cl < o


def _distance_pct(a, b):
    a, b = _f(a), _f(b)
    if a is None or b is None or b == 0: return None
    return abs(a - b) / abs(b) * 100


# ============================================================
# TREND ACTIVITY
# ============================================================

def measure_trend_activity(candles_1h, direction):
    if not candles_1h or len(candles_1h) < 15:
        return 0.0
    recent = candles_1h[-20:]
    total = 0.0
    directional = 0.0
    for c in recent:
        b = _body(c)
        total += b
        if direction == "LONG" and _bull(c): directional += b
        elif direction == "SHORT" and _bear(c): directional += b
    return directional / total if total > 0 else 0.0


# ============================================================
# 1H SWINGS / DIRECTION
# ============================================================

def _swing_high(c, i):
    if i < 2 or i >= len(c) - 2: return False
    cur = _h(c[i]); l1, l2 = _h(c[i-1]), _h(c[i-2]); r1, r2 = _h(c[i+1]), _h(c[i+2])
    if any(x is None for x in (cur, l1, l2, r1, r2)): return False
    return cur > l1 and cur >= l2 and cur >= r1 and cur > r2


def _swing_low(c, i):
    if i < 2 or i >= len(c) - 2: return False
    cur = _l(c[i]); l1, l2 = _l(c[i-1]), _l(c[i-2]); r1, r2 = _l(c[i+1]), _l(c[i+2])
    if any(x is None for x in (cur, l1, l2, r1, r2)): return False
    return cur < l1 and cur <= l2 and cur <= r1 and cur < r2


def _swing_highs(c):
    r = []
    if not c: return r
    for i in range(len(c)):
        if _swing_high(c, i):
            p = _h(c[i])
            if p is not None: r.append((i, p))
    return r


def _swing_lows(c):
    r = []
    if not c: return r
    for i in range(len(c)):
        if _swing_low(c, i):
            p = _l(c[i])
            if p is not None: r.append((i, p))
    return r


def get_1h_direction(candles):
    if not candles or len(candles) < 15:
        return "NEUTRAL"
    candles = candles[-60:]
    highs = _swing_highs(candles)
    lows = _swing_lows(candles)

    bull = False; bear = False
    if len(highs) >= 2 and len(lows) >= 2:
        bull = highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1]
        bear = highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1]

    if not bull and not bear:
        recent = candles[-8:]
        bb = sum(_body(c) for c in recent if _bull(c))
        sb = sum(_body(c) for c in recent if _bear(c))
        if bb > 0 and bb > sb * 1.4: bull = True
        elif sb > 0 and sb > bb * 1.4: bear = True

    if bull and not bear: return "LONG"
    if bear and not bull: return "SHORT"
    return "NEUTRAL"


def get_higher_timeframe_direction(candles_1h, candles_d1=None, candles_w1=None):
    return get_1h_direction(candles_1h)


# ============================================================
# LIQUIDITY HELPERS
# ============================================================

def _level_price(l): return _f(l.get("price")) if isinstance(l, dict) else _f(l)
def _level_side(l):
    if not isinstance(l, dict): return None
    return str(l.get("side") or l.get("direction") or "").upper()
def _level_type(l):
    if not isinstance(l, dict): return ""
    return str(l.get("type") or "").upper()
def _level_strength(l):
    if not isinstance(l, dict): return 0
    v = l.get("strength") or l.get("touches") or 0
    try: return float(v)
    except: return 0
def _is_swept_level(l):
    if not isinstance(l, dict): return False
    return bool(l.get("swept") or l.get("taken") or l.get("used") or l.get("consumed"))


def _levels_for_direction(major_levels, direction):
    result = []
    expected = "SSL" if direction == "LONG" else "BSL"
    for level in major_levels or []:
        price = _level_price(level)
        if price is None: continue
        side = _level_side(level)
        lt = _level_type(level)
        if side == direction or lt == expected or lt.startswith(expected + "_"):
            result.append(level)
    return result


# ============================================================
# FVG HELPERS
# ============================================================

def is_inside_fvg(price, fvgs, direction, tolerance_pct=FVG_TOLERANCE_PCT):
    p = _f(price)
    if p is None or not fvgs:
        return False
    expected = "bullish" if direction == "LONG" else "bearish"
    for fvg in fvgs:
        if fvg.get("type") != expected:
            continue
        top = _f(fvg.get("top"))
        bottom = _f(fvg.get("bottom"))
        if top is None or bottom is None:
            continue
        tol = top * tolerance_pct / 100
        if (bottom - tol) <= p <= (top + tol):
            return True
    return False


def compute_fvg_bonus(sweep, entry, fvgs, direction):
    """
    Возвращает (bonus, sweep_inside, entry_inside).
    """
    if not fvgs:
        return 0, False, False

    sweep_extreme = _f((sweep or {}).get("extreme"))
    entry_p = _f(entry)

    sweep_inside = (
        is_inside_fvg(sweep_extreme, fvgs, direction)
        if sweep_extreme is not None else False
    )
    entry_inside = (
        is_inside_fvg(entry_p, fvgs, direction)
        if entry_p is not None else False
    )

    bonus = 0
    if sweep_inside: bonus += FVG_SWEEP_BONUS
    if entry_inside: bonus += FVG_ENTRY_BONUS
    return min(bonus, FVG_MAX_BONUS), sweep_inside, entry_inside


# ============================================================
# SWEEP
# ============================================================

def _sweep_candidate_score(candle, level, depth):
    strength = _level_strength(level)
    touches = level.get("touches", 1) if isinstance(level, dict) else 1
    return depth * 4.0 + strength / 20.0 + min(touches, 5) * 3.0


def find_sweep(candles_1h, major_levels, direction):
    if direction not in {"LONG", "SHORT"}: return None
    if not candles_1h or len(candles_1h) < 3: return None

    levels = _levels_for_direction(major_levels, direction)
    if not levels: return None

    recent = candles_1h[-MAX_SWEEP_AGE_1H:]
    candidates = []

    for idx, candle in enumerate(reversed(recent)):
        for level in levels:
            if _is_swept_level(level): continue
            price = _level_price(level)
            if price is None: continue

            if direction == "LONG":
                low = _l(candle); close = _c(candle)
                if low is None or close is None: continue
                depth = (price - low) / price * 100
                if low < price and depth >= MIN_SWEEP_DEPTH_PCT and close > price and _bull(candle):
                    candidates.append({
                        "swept": True, "direction": "LONG", "level": price,
                        "extreme": low, "open_time": _t(candle), "price": low,
                        "liquidity_type": "SSL",
                        "touches": level.get("touches", 1),
                        "strength": level.get("strength", 0),
                        "depth_pct": depth,
                        "_score": _sweep_candidate_score(candle, level, depth) - idx * 2.0,
                    })
            else:
                high = _h(candle); close = _c(candle)
                if high is None or close is None: continue
                depth = (high - price) / price * 100
                if high > price and depth >= MIN_SWEEP_DEPTH_PCT and close < price and _bear(candle):
                    candidates.append({
                        "swept": True, "direction": "SHORT", "level": price,
                        "extreme": high, "open_time": _t(candle), "price": high,
                        "liquidity_type": "BSL",
                        "touches": level.get("touches", 1),
                        "strength": level.get("strength", 0),
                        "depth_pct": depth,
                        "_score": _sweep_candidate_score(candle, level, depth) - idx * 2.0,
                    })

    if not candidates: return None
    best = max(candidates, key=lambda x: x["_score"])
    best.pop("_score", None)
    return best


# ============================================================
# 15M CONFIRMATION
# ============================================================

def _local_high_15m(c, i):
    if i < 1 or i >= len(c) - 1: return False
    cur, l, r = _h(c[i]), _h(c[i-1]), _h(c[i+1])
    if cur is None or l is None or r is None: return False
    return cur >= l and cur > r


def _local_low_15m(c, i):
    if i < 1 or i >= len(c) - 1: return False
    cur, l, r = _l(c[i]), _l(c[i-1]), _l(c[i+1])
    if cur is None or l is None or r is None: return False
    return cur <= l and cur < r


def confirmation_15m(candles_15m, sweep, direction):
    if not sweep or direction not in {"LONG", "SHORT"} or not candles_15m:
        return False, None, None

    sweep_time = _f(sweep.get("open_time"))
    candidates = []
    for candle in candles_15m:
        t = _t(candle)
        if sweep_time is None or (t is not None and t > sweep_time):
            candidates.append(candle)

    candidates = candidates[-MAX_15M_CONFIRM_CANDLES:]
    if len(candidates) < 3:
        return False, None, None

    for i in range(1, len(candidates)):
        candle = candidates[i]
        if _body_ratio(candle) < MIN_BODY_RATIO: continue
        close = _c(candle)
        if close is None: continue

        if direction == "LONG":
            lh = [_h(candidates[j]) for j in range(i - 1)
                  if _local_high_15m(candidates, j) and _h(candidates[j]) is not None]
            if not lh: continue
            ref = max(lh[-3:])
            if _bull(candle) and close > ref:
                return True, "15M bullish structure break", _t(candle)
        else:
            ll = [_l(candidates[j]) for j in range(i - 1)
                  if _local_low_15m(candidates, j) and _l(candidates[j]) is not None]
            if not ll: continue
            ref = min(ll[-3:])
            if _bear(candle) and close < ref:
                return True, "15M bearish structure break", _t(candle)

    return False, None, None


# ============================================================
# 5M ILM
# ============================================================

def _is_local_high(c, i):
    if i < 1 or i >= len(c) - 1: return False
    cur, l, r = _h(c[i]), _h(c[i-1]), _h(c[i+1])
    if cur is None or l is None or r is None: return False
    return cur >= l and cur > r


def _is_local_low(c, i):
    if i < 1 or i >= len(c) - 1: return False
    cur, l, r = _l(c[i]), _l(c[i-1]), _l(c[i+1])
    if cur is None or l is None or r is None: return False
    return cur <= l and cur < r


def _ilm_long_candidate(candles, i, sweep_level, sweep_extreme):
    m = candles[i]
    if not _is_local_low(candles, i): return None

    ml, mh = _l(m), _h(m)
    if ml is None or mh is None: return None

    before = candles[max(0, i - 2):i]
    after = candles[i + 1:min(len(candles), i + 4)]
    if not before or not after: return None

    bl = [_l(x) for x in before if _l(x) is not None]
    ac = [_c(x) for x in after if _c(x) is not None]
    if not bl or not ac: return None

    left_ref = min(bl)
    right_close = max(ac)
    if left_ref <= ml: return None

    m_range = left_ref - ml
    if m_range <= 0: return None

    m_pct = m_range / left_ref * 100
    if m_pct < MIN_SWEEP_DEPTH_PCT: return None

    recovery = (right_close - ml) / m_range
    if recovery < MIN_5M_RECOVERY_RATIO: return None

    trigger_idx = None
    for j in range(i + 1, min(len(candles), i + 4)):
        trig = candles[j]; tc = _c(trig)
        if _bull(trig) and _body_ratio(trig) >= MIN_BODY_RATIO and tc is not None and tc > mh:
            trigger_idx = j; break

    if trigger_idx is None: return None

    trig = candles[trigger_idx]
    tc = _c(trig)

    if sweep_level is not None:
        dist = abs(ml - sweep_level) / sweep_level * 100
        if dist > MIN_5M_ILM_SWEEP_DISTANCE_PCT: return None

    if (sweep_extreme is not None
            and ml > sweep_extreme * (1 + MIN_5M_ILM_SWEEP_DISTANCE_PCT / 100)):
        return None

    return {
        "direction": "LONG", "extreme": ml,
        "trigger_time": _t(trig), "trigger_price": tc,
        "reason": "5M V-ILM: downside manipulation + recovery + bullish displacement",
        "recovery_ratio": recovery, "manipulation_pct": m_pct,
        "_score": recovery * 30 + _body_ratio(trig) * 20 + m_pct * 5,
    }


def _ilm_short_candidate(candles, i, sweep_level, sweep_extreme):
    m = candles[i]
    if not _is_local_high(candles, i): return None

    mh, ml = _h(m), _l(m)
    if mh is None or ml is None: return None

    before = candles[max(0, i - 2):i]
    after = candles[i + 1:min(len(candles), i + 4)]
    if not before or not after: return None

    bh = [_h(x) for x in before if _h(x) is not None]
    ac = [_c(x) for x in after if _c(x) is not None]
    if not bh or not ac: return None

    left_ref = max(bh)
    right_close = min(ac)
    if mh <= left_ref: return None

    m_range = mh - left_ref
    if m_range <= 0: return None

    m_pct = m_range / left_ref * 100
    if m_pct < MIN_SWEEP_DEPTH_PCT: return None

    recovery = (mh - right_close) / m_range
    if recovery < MIN_5M_RECOVERY_RATIO: return None

    trigger_idx = None
    for j in range(i + 1, min(len(candles), i + 4)):
        trig = candles[j]; tc = _c(trig)
        if _bear(trig) and _body_ratio(trig) >= MIN_BODY_RATIO and tc is not None and tc < ml:
            trigger_idx = j; break

    if trigger_idx is None: return None

    trig = candles[trigger_idx]
    tc = _c(trig)

    if sweep_level is not None:
        dist = abs(mh - sweep_level) / sweep_level * 100
        if dist > MIN_5M_ILM_SWEEP_DISTANCE_PCT: return None

    if (sweep_extreme is not None
            and mh < sweep_extreme * (1 - MIN_5M_ILM_SWEEP_DISTANCE_PCT / 100)):
        return None

    return {
        "direction": "SHORT", "extreme": mh,
        "trigger_time": _t(trig), "trigger_price": tc,
        "reason": "5M L-ILM: upside manipulation + recovery + bearish displacement",
        "recovery_ratio": recovery, "manipulation_pct": m_pct,
        "_score": recovery * 30 + _body_ratio(trig) * 20 + m_pct * 5,
    }


def detect_5m_ilm(candles_5m, sweep, direction, confirmation_time=None):
    if not sweep or direction not in {"LONG", "SHORT"}: return False, None

    start = _f(confirmation_time) or _f(sweep.get("open_time"))
    candles = []
    for candle in candles_5m or []:
        t = _t(candle)
        if start is None or (t is not None and t > start):
            candles.append(candle)

    candles = candles[-MAX_5M_ILM_CANDLES:]
    if len(candles) < 5: return False, None

    sweep_level = _f(sweep.get("level"))
    sweep_extreme = _f(sweep.get("extreme"))

    candidates = []
    for i in range(2, len(candles) - 2):
        if direction == "LONG":
            ilm = _ilm_long_candidate(candles, i, sweep_level, sweep_extreme)
        else:
            ilm = _ilm_short_candidate(candles, i, sweep_level, sweep_extreme)
        if ilm: candidates.append(ilm)

    if not candidates: return False, None
    best = max(candidates, key=lambda x: x["_score"])
    best.pop("_score", None)
    return True, best


# ============================================================
# TARGETS
# ============================================================

def next_target(major_levels, direction, current_price, exclude_level=None):
    price = _f(current_price)
    excluded = _f(exclude_level)
    if price is None: return None

    candidates = []
    for level in major_levels or []:
        if _is_swept_level(level): continue
        lp = _level_price(level)
        if lp is None: continue
        if excluded is not None:
            d = _distance_pct(lp, excluded)
            if d is not None and d < MIN_TARGET_DISTANCE_PCT: continue
        if direction == "LONG" and lp > price: candidates.append(lp)
        elif direction == "SHORT" and lp < price: candidates.append(lp)

    if not candidates: return None
    return min(candidates, key=lambda x: abs(x - price))


def _scan_local_targets(candles, direction, entry, lookback, label):
    results = []
    if not candles or len(candles) < 5: return results
    recent = candles[-lookback:]
    if len(recent) < 5: return results

    for i in range(1, len(recent) - 1):
        candle = recent[i]
        if direction == "LONG":
            if not _is_local_high(recent, i): continue
            p = _h(candle)
            if p is None or p <= entry: continue
            d = (p - entry) / entry * 100
        else:
            if not _is_local_low(recent, i): continue
            p = _l(candle)
            if p is None or p >= entry: continue
            d = (entry - p) / entry * 100
        if d < MIN_FALLBACK_DISTANCE_PCT: continue
        results.append({"price": p, "distance_pct": d, "source": label, "time": _t(candle)})
    return results


def detect_local_swing_target(candles_5m, candles_15m, direction, entry):
    entry = _f(entry)
    if entry is None or entry <= 0: return None
    candidates = []
    candidates.extend(_scan_local_targets(candles_5m, direction, entry,
                                          FALLBACK_5M_LOOKBACK, "5M local swing"))
    candidates.extend(_scan_local_targets(candles_15m, direction, entry,
                                          FALLBACK_15M_LOOKBACK, "15M local swing"))
    if not candidates: return None
    candidates.sort(key=lambda x: x["distance_pct"])
    return candidates[0]


def resolve_target(major_levels, direction, entry, sweep_level,
                   candles_5m, candles_15m, d1_context=None):
    if d1_context:
        pb = _f(d1_context.get("point_b"))
        if pb is not None:
            if direction == "LONG" and pb > entry:
                d = (pb - entry) / entry * 100
                if d >= MIN_TARGET_DISTANCE_PCT:
                    return pb, "d1", f"TP = D1 Point B ({pb:.4f})."
            elif direction == "SHORT" and pb < entry:
                d = (entry - pb) / entry * 100
                if d >= MIN_TARGET_DISTANCE_PCT:
                    return pb, "d1", f"TP = D1 Point B ({pb:.4f})."

    tp_major = next_target(major_levels, direction, entry, sweep_level)
    if tp_major is not None:
        return tp_major, "major", "TP = следующая свежая Major Liquidity."

    fb = detect_local_swing_target(candles_5m, candles_15m, direction, entry)
    if fb is not None:
        return fb["price"], "local", f"TP = ближайший {fb['source']}."

    return None, None, "Ни major, ни local TP не найдены."


# ============================================================
# ENTRY / STOP / RR / GEOMETRY
# ============================================================

def calculate_entry(current_price, direction):
    p = _f(current_price)
    if p is None or direction not in {"LONG", "SHORT"}: return None
    return p


def calculate_stop(entry, extreme, direction):
    e, x = _f(entry), _f(extreme)
    if e is None or x is None: return None
    if direction == "LONG":
        sl = x * (1 - SL_BUFFER_PCT / 100)
        return sl if sl < e else None
    if direction == "SHORT":
        sl = x * (1 + SL_BUFFER_PCT / 100)
        return sl if sl > e else None
    return None


def calculate_take_profit(major_levels, direction, entry, sweep_level=None):
    return next_target(major_levels, direction, entry, sweep_level)


def calculate_rr(entry, sl, tp, direction=None):
    e, s, t = _f(entry), _f(sl), _f(tp)
    if e is None or s is None or t is None: return None

    if direction == "LONG":
        if not (s < e < t): return None
    elif direction == "SHORT":
        if not (t < e < s): return None
    else:
        if s == e or t == e: return None

    risk = abs(e - s)
    reward = abs(t - e)
    if risk <= 0: return None
    return reward / risk


def validate_trade_geometry(entry, sl, tp, direction):
    e, s, t = _f(entry), _f(sl), _f(tp)
    if e is None or s is None or t is None: return False
    if direction == "LONG": return s < e < t
    if direction == "SHORT": return t < e < s
    return False


def validate_target(entry, tp, direction):
    e, t = _f(entry), _f(tp)
    if e is None or t is None: return False
    if direction == "LONG": return t > e
    if direction == "SHORT": return t < e
    return False


# ============================================================
# SCORE
# ============================================================

def _score(direction, context_direction, d1_context, sweep,
           confirmation_strength, ilm, rr, major_strength,
           tp_source="major", fvg_bonus=0):
    score = 0

    if direction == context_direction: score += 15
    elif context_direction == "NEUTRAL": score += 5

    if d1_context:
        d1t = d1_context.get("trend", "NEUTRAL")
        if d1t == direction: score += 10
        elif d1t == "NEUTRAL": score += 3
        else: score -= 10

    if sweep:
        depth = sweep.get("depth_pct", 0)
        if depth >= 0.40: score += 20
        elif depth >= 0.25: score += 15
        elif depth >= 0.15: score += 10
        else: score += 6

    if confirmation_strength >= 0.75: score += 15
    elif confirmation_strength >= 0.50: score += 11
    elif confirmation_strength > 0: score += 7

    if ilm:
        rec = ilm.get("recovery_ratio", 0)
        if rec >= 0.66: score += 15
        elif rec >= 0.50: score += 11
        else: score += 7

    if rr is not None:
        if rr >= 4.0: score += 20
        elif rr >= 3.0: score += 16
        elif rr >= 2.5: score += 12
        elif rr >= 2.0: score += 8

    score += min(10, major_strength / 10.0)

    # FVG bonus
    score += fvg_bonus

    if tp_source == "local":
        score = score * 0.9

    return int(min(100, max(0, round(score))))


# ============================================================
# SINGLE SCENARIO
# ============================================================

def _analyze_scenario(candles_1h, candles_15m, candles_5m, current_price,
                      major_levels, direction, context_direction,
                      d1_context=None, fvgs=None):

    result = {
        "stage": "WAIT", "direction": direction, "score": 0, "reason": "",
        "entry": None, "sl": None, "tp": None, "tp_source": None, "rr": None,
        "sweep": None, "major_levels": major_levels or [],
        "confirmation_15m": False, "confirmation_15m_time": None,
        "confirmation": None, "ilm": None, "sweep_extreme": None,
        "tp_reason": None, "geometry_valid": False, "trend_activity": 0.0,
        "fvg_bonus": 0, "fvg_sweep": False, "fvg_entry": False,
    }

    price = _f(current_price)
    if price is None or not candles_1h or not candles_15m or not candles_5m:
        result["reason"] = "Недостаточно рыночных данных."
        return result

    trend_activity = measure_trend_activity(candles_1h, direction)
    result["trend_activity"] = round(trend_activity, 3)

    levels = _levels_for_direction(major_levels, direction)
    if not levels:
        result["score"] = 20
        result["reason"] = (
            f"Нет актуальной Major "
            f"{'SSL' if direction == 'LONG' else 'BSL'}."
        )
        return result

    sweep = find_sweep(candles_1h, levels, direction)
    result["sweep"] = sweep

    if sweep is None:
        result["score"] = 25
        result["reason"] = (
            f"Ждём {'SSL sweep' if direction == 'LONG' else 'BSL sweep'}."
        )
        return result

    result["stage"] = "SWEPT"
    result["sweep_extreme"] = sweep.get("extreme")

    conf_ok, conf_text, conf_time = confirmation_15m(candles_15m, sweep, direction)
    result["confirmation_15m"] = conf_ok
    result["confirmation_15m_time"] = conf_time
    result["confirmation"] = conf_text

    if not conf_ok:
        result["score"] = 50
        result["reason"] = "Sweep есть. Ждём 15M confirmation."
        return result

    result["stage"] = "15M_CONFIRMED"

    ilm_ok, ilm = detect_5m_ilm(candles_5m, sweep, direction, conf_time)
    result["ilm"] = ilm

    if not ilm_ok:
        result["score"] = 65
        result["reason"] = "15M подтверждение есть. Ждём 5M ILM."
        return result

    entry = calculate_entry(price, direction)
    if entry is None:
        result["score"] = 68
        result["reason"] = "Не удалось определить Entry."
        return result

    ilm_extreme = _f((ilm or {}).get("extreme"))
    sweep_extreme = _f(sweep.get("extreme"))
    candidates = [x for x in (ilm_extreme, sweep_extreme) if x is not None]

    if not candidates:
        result["score"] = 68
        result["reason"] = "Не удалось определить экстремум для SL."
        return result

    if direction == "LONG":
        extreme = min(candidates)
        if extreme >= entry:
            result["score"] = 68
            result["reason"] = "LONG invalid: extreme >= Entry."
            return result
    else:
        extreme = max(candidates)
        if extreme <= entry:
            result["score"] = 68
            result["reason"] = "SHORT invalid: extreme <= Entry."
            return result

    sl = calculate_stop(entry, extreme, direction)
    if sl is None:
        result["score"] = 68
        result["reason"] = "Не удалось построить корректный SL."
        return result

    tp, tp_source, tp_reason = resolve_target(
        major_levels=major_levels, direction=direction, entry=entry,
        sweep_level=sweep.get("level"),
        candles_5m=candles_5m, candles_15m=candles_15m,
        d1_context=d1_context,
    )
    result["tp_reason"] = tp_reason
    result["tp_source"] = tp_source

    if tp is None:
        result["score"] = 70
        result["reason"] = tp_reason
        return result

    if not validate_target(entry, tp, direction):
        result["score"] = 70
        result["reason"] = "TP находится не в направлении сделки."
        return result

    if not validate_trade_geometry(entry, sl, tp, direction):
        result["score"] = 68
        result["reason"] = "Некорректная геометрия сделки."
        return result

    result["geometry_valid"] = True

    rr_value = calculate_rr(entry, sl, tp, direction)
    if rr_value is None:
        result["score"] = 68
        result["reason"] = "Не удалось рассчитать RR."
        return result

    result.update({
        "entry": round(entry, 6),
        "sl": round(sl, 6),
        "tp": round(tp, 6),
        "rr": rr_value,
    })

    try:
        major_strength = max([_level_strength(l) for l in levels] or [0])
    except: major_strength = 0

    # FVG bonus
    fvg_bonus, fvg_sweep, fvg_entry = compute_fvg_bonus(
        sweep, entry, fvgs or [], direction,
    )
    result["fvg_bonus"] = fvg_bonus
    result["fvg_sweep"] = fvg_sweep
    result["fvg_entry"] = fvg_entry

    if rr_value < MIN_RR:
        result["score"] = min(
            _score(direction, context_direction, d1_context, sweep,
                   0.6, ilm, None, major_strength, tp_source, fvg_bonus),
            79,
        )
        result["stage"] = "15M_CONFIRMED"
        result["reason"] = (
            f"RR 1:{rr_value:.2f} < 1:2. Вход запрещён. "
            f"Ждём ретест."
        )
        return result

    conf_strength = 0.8 if conf_ok else 0.6

    score = _score(direction, context_direction, d1_context, sweep,
                   conf_strength, ilm, rr_value, major_strength,
                   tp_source, fvg_bonus)
    result["score"] = score

    trend_ok = trend_activity >= MIN_TREND_ACTIVITY_READY
    recovery = (ilm or {}).get("recovery_ratio", 0)
    v_ok = recovery >= MIN_V_RECOVERY_FOR_READY

    ready_ok = score >= MIN_SCORE_READY and trend_ok and v_ok

    if ready_ok:
        result["stage"] = "READY"
        fvg_tag = ""
        if fvg_sweep and fvg_entry: fvg_tag = " FVG sweep+entry."
        elif fvg_sweep: fvg_tag = " FVG sweep."
        elif fvg_entry: fvg_tag = " FVG entry."
        result["reason"] = (
            f"Sweep → 15M → 5M ILM. "
            f"Trend {trend_activity:.2f}, "
            f"Recovery {recovery:.2f}, "
            f"TP {tp_source}.{fvg_tag}"
        )
    else:
        result["stage"] = "15M_CONFIRMED"
        blocks = []
        if score < MIN_SCORE_READY: blocks.append(f"score {score}")
        if not trend_ok: blocks.append(f"trend {trend_activity:.2f}")
        if not v_ok: blocks.append(f"recovery {recovery:.2f}")
        result["reason"] = "Сетап есть, READY заблокирован: " + ", ".join(blocks)

    return result


# ============================================================
# MAIN ANALYSIS
# ============================================================

def analyze(candles_1h, candles_15m, candles_5m, current_price,
            major_levels=None, sweep=None, order_flow=None,
            candles_1m=None, d1_context=None, fvgs=None):

    price = _f(current_price)
    context_direction = get_1h_direction(candles_1h)

    base = {
        "stage": "WAIT", "direction": context_direction,
        "context_direction": context_direction,
        "d1_trend": (d1_context or {}).get("trend", "NEUTRAL"),
        "d1_point_a": (d1_context or {}).get("point_a"),
        "d1_point_b": (d1_context or {}).get("point_b"),
        "score": 0, "reason": "",
        "entry": None, "sl": None, "tp": None, "tp_source": None, "rr": None,
        "sweep": None, "major_levels": major_levels or [],
        "confirmation_15m": False, "confirmation_15m_time": None,
        "confirmation": None, "ilm": None, "sweep_extreme": None,
        "tp_reason": None, "geometry_valid": False, "trend_activity": 0.0,
        "fvg_bonus": 0, "fvg_sweep": False, "fvg_entry": False,
        "long": None, "short": None,
    }

    if price is None or not candles_1h or not candles_15m or not candles_5m:
        base["reason"] = "Недостаточно рыночных данных."
        return base

    long_result = _analyze_scenario(
        candles_1h, candles_15m, candles_5m, price,
        major_levels, "LONG", context_direction,
        d1_context=d1_context, fvgs=fvgs,
    )
    short_result = _analyze_scenario(
        candles_1h, candles_15m, candles_5m, price,
        major_levels, "SHORT", context_direction,
        d1_context=d1_context, fvgs=fvgs,
    )

    base["long"] = long_result
    base["short"] = short_result

    if context_direction == "NEUTRAL":
        best = max([long_result, short_result], key=lambda x: x.get("score", 0))
        base.update({
            "stage": "WAIT" if best.get("stage") == "READY" else best.get("stage", "WAIT"),
            "direction": "NEUTRAL",
            "score": best.get("score", 0),
            "reason": "1H NEUTRAL. READY не разрешаем.",
            "entry": best.get("entry"), "sl": best.get("sl"), "tp": best.get("tp"),
            "tp_source": best.get("tp_source"), "rr": best.get("rr"),
            "sweep": best.get("sweep"),
            "confirmation_15m": best.get("confirmation_15m", False),
            "confirmation_15m_time": best.get("confirmation_15m_time"),
            "confirmation": best.get("confirmation"),
            "ilm": best.get("ilm"), "sweep_extreme": best.get("sweep_extreme"),
            "tp_reason": best.get("tp_reason"),
            "geometry_valid": best.get("geometry_valid", False),
            "trend_activity": best.get("trend_activity", 0.0),
            "fvg_bonus": best.get("fvg_bonus", 0),
            "fvg_sweep": best.get("fvg_sweep", False),
            "fvg_entry": best.get("fvg_entry", False),
        })
        return base

    ready = []
    if long_result.get("stage") == "READY" and long_result.get("score", 0) >= MIN_SCORE_READY:
        ready.append(long_result)
    if short_result.get("stage") == "READY" and short_result.get("score", 0) >= MIN_SCORE_READY:
        ready.append(short_result)

    if ready:
        aligned = [x for x in ready if x["direction"] == context_direction]
        counter = [x for x in ready if x["direction"] != context_direction]

        if aligned:
            chosen = max(aligned, key=lambda x: x["score"])
        elif counter:
            counter_ready = [x for x in counter if x["score"] >= COUNTER_TREND_MIN_SCORE]
            if not counter_ready:
                base["score"] = max(long_result["score"], short_result["score"])
                base["reason"] = "Контртренд недостаточно сильный."
                return base
            chosen = max(counter_ready, key=lambda x: x["score"])
        else:
            chosen = None

        if chosen:
            base.update(chosen)
            base["context_direction"] = context_direction
            base["long"] = long_result
            base["short"] = short_result
            return base

    candidates = [long_result, short_result]

    def stage_weight(r):
        return {"READY": 5, "15M_CONFIRMED": 4, "SWEPT": 3, "WAIT": 1}.get(r.get("stage"), 0)

    aligned_candidates = [x for x in candidates if x["direction"] == context_direction]
    pool = aligned_candidates if aligned_candidates else candidates

    chosen = max(pool, key=lambda x: (stage_weight(x), x.get("score", 0)))

    base.update({
        "stage": chosen.get("stage", "WAIT"),
        "direction": chosen.get("direction", context_direction),
        "score": chosen.get("score", 0),
        "reason": chosen.get("reason", ""),
        "entry": chosen.get("entry"), "sl": chosen.get("sl"), "tp": chosen.get("tp"),
        "tp_source": chosen.get("tp_source"), "rr": chosen.get("rr"),
        "sweep": chosen.get("sweep"),
        "confirmation_15m": chosen.get("confirmation_15m", False),
        "confirmation_15m_time": chosen.get("confirmation_15m_time"),
        "confirmation": chosen.get("confirmation"),
        "ilm": chosen.get("ilm"), "sweep_extreme": chosen.get("sweep_extreme"),
        "tp_reason": chosen.get("tp_reason"),
        "geometry_valid": chosen.get("geometry_valid", False),
        "trend_activity": chosen.get("trend_activity", 0.0),
        "fvg_bonus": chosen.get("fvg_bonus", 0),
        "fvg_sweep": chosen.get("fvg_sweep", False),
        "fvg_entry": chosen.get("fvg_entry", False),
    })
    base["context_direction"] = context_direction
    return base


def analyze_sol(*args, **kwargs):
    return analyze(*args, **kwargs)


__all__ = [
    "STRATEGY_VERSION", "MIN_SCORE_READY", "MIN_RR", "SL_BUFFER_PCT",
    "MIN_TREND_ACTIVITY_READY", "MIN_V_RECOVERY_FOR_READY",
    "FVG_TOLERANCE_PCT", "FVG_SWEEP_BONUS", "FVG_ENTRY_BONUS",
    "get_1h_direction", "get_higher_timeframe_direction",
    "measure_trend_activity",
    "is_inside_fvg", "compute_fvg_bonus",
    "find_sweep", "confirmation_15m", "detect_5m_ilm",
    "calculate_entry", "calculate_stop", "calculate_take_profit", "calculate_rr",
    "validate_trade_geometry", "validate_target",
    "next_target", "detect_local_swing_target", "resolve_target",
    "analyze", "analyze_sol",
]