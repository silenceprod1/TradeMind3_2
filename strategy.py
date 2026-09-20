"""
TradeMind 8.6 — strategy.py

Retest Entry: вход на 0.5% откате от trigger-close.
"""

from typing import Any, Dict, List, Optional, Tuple


STRATEGY_VERSION = "8.6"

ALLOW_SHORT = True

MAX_ILM_AGE_FOR_ENTRY = 4
ATR_SL_MULT_SOFT = 0.8

ENABLE_SESSION_FILTER = False
SESSION_BLOCK_START_HOUR = 2
SESSION_BLOCK_END_HOUR = 7
SESSION_FILTER_EXEMPT = {"BTCUSDT", "ETHUSDT"}

RETEST_OFFSET_PCT = 0.5

MIN_SCORE_READY = 85
REQUIRE_BOS_FOR_READY = True
SL_BUFFER_PCT = 0.20
STRUCTURAL_SL_LOOKBACK_15M = 50
ENTRY_TOLERANCE_PCT = 0.7
FIXED_RR = 2.0

MIN_SWEEP_DEPTH_PCT = 0.15
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
MIN_BODY_RATIO_TRIGGER_5M = 0.50
VOLATILITY_ATR_SPIKE_MULT = 2.5
ENABLE_VOLATILITY_FILTER = True

VOLUME_CONFIRMATION_ENABLED = False
VOLUME_CONFIRMATION_MULT = 1.2
VOLUME_CONFIRMATION_LOOKBACK = 20

MIN_BODY_RATIO = 0.35
MAX_5M_ILM_CANDLES = 60
MAX_15M_CONFIRM_CANDLES = 24
MAX_ILM_AGE_CANDLES_5M = 36

MIN_5M_RECOVERY_RATIO = 0.20
MIN_5M_ILM_SWEEP_DISTANCE_PCT = 3.0

MIN_TREND_ACTIVITY_READY = 0.40
COUNTER_TREND_MIN_SCORE = 92

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
        aliases = {"open": "o", "high": "h", "low": "l",
                   "close": "c", "open_time": "time",
                   "volume": "v"}
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
def _vol(c): return _v(c, "volume") or 0.0


def _body(c):
    o, cl = _o(c), _c(c)
    if o is None or cl is None:
        return 0.0
    return abs(cl - o)


def _range(c):
    h, l = _h(c), _l(c)
    if h is None or l is None:
        return 0.0
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
    if a is None or b is None or b == 0:
        return None
    return abs(a - b) / abs(b) * 100


def calculate_atr(candles, period=14):
    if not candles or len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = _h(candles[i])
        l = _l(candles[i])
        pc = _c(candles[i - 1])
        if h is None or l is None or pc is None:
            continue
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def _avg_atr(candles, fast=14, slow=50):
    if not candles or len(candles) < slow + 5:
        return None, None
    fast_val = calculate_atr(candles, fast)
    slow_val = calculate_atr(candles, slow)
    return fast_val, slow_val


def _swing_high(c, i):
    if i < 2 or i >= len(c) - 2:
        return False
    cur = _h(c[i])
    l1, l2 = _h(c[i - 1]), _h(c[i - 2])
    r1, r2 = _h(c[i + 1]), _h(c[i + 2])
    if any(x is None for x in (cur, l1, l2, r1, r2)):
        return False
    return cur > l1 and cur >= l2 and cur >= r1 and cur > r2


def _swing_low(c, i):
    if i < 2 or i >= len(c) - 2:
        return False
    cur = _l(c[i])
    l1, l2 = _l(c[i - 1]), _l(c[i - 2])
    r1, r2 = _l(c[i + 1]), _l(c[i + 2])
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
    if not candles or len(candles) < 15:
        return "NEUTRAL"
    candles = candles[-60:]
    highs = _swing_highs(candles)
    lows = _swing_lows(candles)

    bull = False
    bear = False
    if len(highs) >= 2 and len(lows) >= 2:
        bull = highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1]
        bear = highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1]

    if not bull and not bear:
        recent = candles[-8:]
        bb = sum(_body(c) for c in recent if _bull(c))
        sb = sum(_body(c) for c in recent if _bear(c))
        if bb > 0 and bb > sb * 1.4:
            bull = True
        elif sb > 0 and sb > bb * 1.4:
            bear = True

    if bull and not bear:
        return "LONG"
    if bear and not bull:
        return "SHORT"
    return "NEUTRAL"


def get_higher_timeframe_direction(candles_1h, candles_d1=None, candles_w1=None):
    return get_1h_direction(candles_1h)


def _level_price(l):
    return _f(l.get("price")) if isinstance(l, dict) else _f(l)


def _level_side(l):
    if not isinstance(l, dict):
        return None
    return str(l.get("side") or l.get("direction") or "").upper()


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
    return bool(l.get("swept") or l.get("taken")
                or l.get("used") or l.get("consumed"))


def _levels_for_direction(major_levels, direction):
    result = []
    expected = "SSL" if direction == "LONG" else "BSL"
    for level in major_levels or []:
        price = _level_price(level)
        if price is None:
            continue
        side = _level_side(level)
        lt = _level_type(level)
        if side == direction or lt == expected or lt.startswith(expected + "_"):
            result.append(level)
    return result


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
    if not fvgs:
        return 0, False, False
    sweep_extreme = _f((sweep or {}).get("extreme"))
    entry_p = _f(entry)
    sweep_inside = (is_inside_fvg(sweep_extreme, fvgs, direction)
                    if sweep_extreme is not None else False)
    entry_inside = (is_inside_fvg(entry_p, fvgs, direction)
                    if entry_p is not None else False)
    bonus = 0
    if sweep_inside:
        bonus += FVG_SWEEP_BONUS
    if entry_inside:
        bonus += FVG_ENTRY_BONUS
    return min(bonus, FVG_MAX_BONUS), sweep_inside, entry_inside


def _sweep_candidate_score(candle, level, depth):
    strength = _level_strength(level)
    touches = level.get("touches", 1) if isinstance(level, dict) else 1
    return depth * 4.0 + strength / 20.0 + min(touches, 5) * 3.0


def find_sweep(candles_1h, major_levels, direction):
    if direction not in {"LONG", "SHORT"}:
        return None
    if not candles_1h or len(candles_1h) < 3:
        return None

    levels = _levels_for_direction(major_levels, direction)
    if not levels:
        return None

    recent = candles_1h[-MAX_SWEEP_AGE_1H:]
    candidates = []
    total_candles = len(candles_1h)

    for idx, candle in enumerate(reversed(recent)):
        candle_idx_1h = total_candles - 1 - idx

        if VOLUME_CONFIRMATION_ENABLED:
            if not _has_volume_confirmation(candles_1h, candle_idx_1h):
                continue

        for level in levels:
            if _is_swept_level(level):
                continue
            price = _level_price(level)
            if price is None:
                continue

            if direction == "LONG":
                low = _l(candle)
                close = _c(candle)
                if low is None or close is None:
                    continue
                depth = (price - low) / price * 100
                if (low < price and depth >= MIN_SWEEP_DEPTH_PCT
                        and close > price):
                    open_p = _o(candle) or close
                    body = abs(close - open_p)
                    lower_wick = min(open_p, close) - low
                    is_rejection = lower_wick > body or _bull(candle)
                    if not is_rejection:
                        continue

                    is_invalidated = False
                    consecutive_below = 0
                    for k in range(candle_idx_1h + 1, total_candles):
                        c_after = candles_1h[k]
                        c_after_close = _c(c_after)
                        if c_after_close is not None and c_after_close < low:
                            consecutive_below += 1
                            if consecutive_below >= 2:
                                is_invalidated = True
                                break
                        else:
                            consecutive_below = 0
                    if is_invalidated:
                        continue

                    candidates.append({
                        "swept": True, "direction": "LONG", "level": price,
                        "extreme": low, "open_time": _t(candle), "price": low,
                        "liquidity_type": "SSL",
                        "touches": level.get("touches", 1),
                        "strength": level.get("strength", 0),
                        "depth_pct": depth,
                        "_score": (_sweep_candidate_score(candle, level, depth)
                                   - idx * 2.0),
                    })
            else:
                high = _h(candle)
                close = _c(candle)
                if high is None or close is None:
                    continue
                depth = (high - price) / price * 100
                if (high > price and depth >= MIN_SWEEP_DEPTH_PCT
                        and close < price):
                    open_p = _o(candle) or close
                    body = abs(close - open_p)
                    upper_wick = high - max(open_p, close)
                    is_rejection = upper_wick > body or _bear(candle)
                    if not is_rejection:
                        continue

                    is_invalidated = False
                    consecutive_above = 0
                    for k in range(candle_idx_1h + 1, total_candles):
                        c_after = candles_1h[k]
                        c_after_close = _c(c_after)
                        if c_after_close is not None and c_after_close > high:
                            consecutive_above += 1
                            if consecutive_above >= 2:
                                is_invalidated = True
                                break
                        else:
                            consecutive_above = 0
                    if is_invalidated:
                        continue

                    candidates.append({
                        "swept": True, "direction": "SHORT", "level": price,
                        "extreme": high, "open_time": _t(candle), "price": high,
                        "liquidity_type": "BSL",
                        "touches": level.get("touches", 1),
                        "strength": level.get("strength", 0),
                        "depth_pct": depth,
                        "_score": (_sweep_candidate_score(candle, level, depth)
                                   - idx * 2.0),
                    })

    if not candidates:
        return None
    best = max(candidates, key=lambda x: x["_score"])
    best.pop("_score", None)
    return best


def _has_volume_confirmation(candles_1h, candle_index,
                             lookback=VOLUME_CONFIRMATION_LOOKBACK,
                             mult=VOLUME_CONFIRMATION_MULT):
    if not candles_1h or candle_index < 0 or candle_index >= len(candles_1h):
        return True
    if candle_index < lookback:
        return True
    start = candle_index - lookback
    vols = [_vol(candles_1h[i]) for i in range(start, candle_index)]
    vols = [v for v in vols if v > 0]
    if not vols:
        return True
    avg = sum(vols) / len(vols)
    if avg <= 0:
        return True
    current = _vol(candles_1h[candle_index])
    if current <= 0:
        return True
    return current >= avg * mult


def measure_trend_activity(candles_1h, direction):
    if not candles_1h or len(candles_1h) < 15:
        return 0.0
    recent = candles_1h[-20:]
    total = 0.0
    directional = 0.0
    for c in recent:
        b = _body(c)
        total += b
        if direction == "LONG" and _bull(c):
            directional += b
        elif direction == "SHORT" and _bear(c):
            directional += b
    return directional / total if total > 0 else 0.0


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
    if not sweep or direction not in {"LONG", "SHORT"} or not candles_15m:
        return False, None, None, False

    sweep_time = _f(sweep.get("open_time"))

    candidates = []
    for candle in candles_15m:
        t = _t(candle)
        if sweep_time is None or (t is not None and t > sweep_time):
            candidates.append(candle)

    candidates = candidates[-MAX_15M_CONFIRM_CANDLES:]
    if len(candidates) < 3:
        return False, None, None, False

    fallback = None

    for i in range(1, len(candidates)):
        candle = candidates[i]

        if _body_ratio(candle) < MIN_BODY_RATIO:
            continue

        close = _c(candle)
        if close is None:
            continue

        prev = candidates[i - 1]
        prev_high = _h(prev)
        prev_low = _l(prev)
        prev_body = _body(prev)

        if direction == "LONG":
            if not _bull(candle):
                continue

            local_highs = []
            for j in range(i - 1):
                if _is_local_high_15m(candidates, j):
                    h = _h(candidates[j])
                    if h is not None:
                        local_highs.append(h)

            if not local_highs:
                continue

            reference = max(local_highs[-3:])
            bos = close > reference

            if bos:
                return (True, "15M BOS", _t(candle), True)

            is_engulfing = (
                prev_high is not None
                and close > prev_high
                and _body(candle) > prev_body
            )
            if is_engulfing and fallback is None:
                fallback = (True, "15M engulfing (no BOS)", _t(candle), False)

        else:
            if not _bear(candle):
                continue

            local_lows = []
            for j in range(i - 1):
                if _is_local_low_15m(candidates, j):
                    lo = _l(candidates[j])
                    if lo is not None:
                        local_lows.append(lo)

            if not local_lows:
                continue

            reference = min(local_lows[-3:])
            bos = close < reference

            if bos:
                return (True, "15M BOS", _t(candle), True)

            is_engulfing = (
                prev_low is not None
                and close < prev_low
                and _body(candle) > prev_body
            )
            if is_engulfing and fallback is None:
                fallback = (True, "15M engulfing (no BOS)", _t(candle), False)

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


def _ilm_long_candidate(candles, i, sweep_level, sweep_extreme):
    m = candles[i]
    if not _is_local_low(candles, i):
        return None

    ml, mh = _l(m), _h(m)
    if ml is None or mh is None:
        return None

    before = candles[max(0, i - 2):i]
    if not before:
        return None

    bl = [_l(x) for x in before if _l(x) is not None]
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

    trigger_idx = None
    for j in range(i + 1, min(len(candles), i + 4)):
        trig = candles[j]
        tc = _c(trig)
        if (_bull(trig) and _body_ratio(trig) >= MIN_BODY_RATIO_TRIGGER_5M
                and tc is not None and tc > mh):
            trigger_idx = j
            break

    if trigger_idx is None:
        return None

    trig = candles[trigger_idx]
    tc = _c(trig)
    if tc is None:
        return None

    recovery = (tc - ml) / m_range
    if recovery < MIN_5M_RECOVERY_RATIO:
        return None

    if sweep_level is not None:
        dist = abs(ml - sweep_level) / sweep_level * 100
        if dist > MIN_5M_ILM_SWEEP_DISTANCE_PCT:
            return None

    if (sweep_extreme is not None
            and ml > sweep_extreme * (1 + MIN_5M_ILM_SWEEP_DISTANCE_PCT / 100)):
        return None

    return {
        "direction": "LONG",
        "extreme": ml,
        "trigger_time": _t(trig),
        "trigger_price": tc,
        "reason": "5M V-ILM",
        "recovery_ratio": recovery,
        "manipulation_pct": m_pct,
        "age_candles": len(candles) - 1 - trigger_idx,
        "_score": recovery * 30 + _body_ratio(trig) * 20 + m_pct * 5,
    }


def _ilm_short_candidate(candles, i, sweep_level, sweep_extreme):
    m = candles[i]
    if not _is_local_high(candles, i):
        return None

    mh, ml = _h(m), _l(m)
    if mh is None or ml is None:
        return None

    before = candles[max(0, i - 2):i]
    if not before:
        return None

    bh = [_h(x) for x in before if _h(x) is not None]
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

    trigger_idx = None
    for j in range(i + 1, min(len(candles), i + 4)):
        trig = candles[j]
        tc = _c(trig)
        if (_bear(trig) and _body_ratio(trig) >= MIN_BODY_RATIO_TRIGGER_5M
                and tc is not None and tc < ml):
            trigger_idx = j
            break

    if trigger_idx is None:
        return None

    trig = candles[trigger_idx]
    tc = _c(trig)
    if tc is None:
        return None

    recovery = (mh - tc) / m_range
    if recovery < MIN_5M_RECOVERY_RATIO:
        return None

    if sweep_level is not None:
        dist = abs(mh - sweep_level) / sweep_level * 100
        if dist > MIN_5M_ILM_SWEEP_DISTANCE_PCT:
            return None

    if (sweep_extreme is not None
            and mh < sweep_extreme * (1 - MIN_5M_ILM_SWEEP_DISTANCE_PCT / 100)):
        return None

    return {
        "direction": "SHORT",
        "extreme": mh,
        "trigger_time": _t(trig),
        "trigger_price": tc,
        "reason": "5M L-ILM",
        "recovery_ratio": recovery,
        "manipulation_pct": m_pct,
        "age_candles": len(candles) - 1 - trigger_idx,
        "_score": recovery * 30 + _body_ratio(trig) * 20 + m_pct * 5,
    }


def detect_5m_ilm(candles_5m, sweep, direction, confirmation_time=None):
    if not sweep or direction not in {"LONG", "SHORT"}:
        return False, None

    start = _f(confirmation_time) or _f(sweep.get("open_time"))
    candles = []
    for candle in candles_5m or []:
        t = _t(candle)
        if start is None or (t is not None and t > start):
            candles.append(candle)

    candles = candles[-MAX_5M_ILM_CANDLES:]
    if len(candles) < 5:
        return False, None

    sweep_level = _f(sweep.get("level"))
    sweep_extreme = _f(sweep.get("extreme"))

    candidates = []
    for i in range(2, len(candles) - 2):
        if direction == "LONG":
            ilm = _ilm_long_candidate(candles, i, sweep_level, sweep_extreme)
        else:
            ilm = _ilm_short_candidate(candles, i, sweep_level, sweep_extreme)
        if ilm:
            candidates.append(ilm)

    if not candidates:
        return False, None
    best = max(candidates, key=lambda x: x["_score"])
    best.pop("_score", None)
    return True, best


# ============================================================
# v8.6 RETEST ENTRY
# ============================================================

def calculate_entry(ilm, current_price, direction):
    """
    v8.6 Retest Entry:
    - Возвращает retest-цену (trigger ± offset)
    - В live бот ставит лимитку по этой цене
    - Если цена откатится — сделка исполнится
    - Если уйдёт в сторону — сигнал сгорит
    """
    if not ilm:
        return None
    trigger = _f(ilm.get("trigger_price"))
    if trigger is None or trigger <= 0:
        return None

    offset = RETEST_OFFSET_PCT / 100.0

    if direction == "LONG":
        # Вход ниже триггера — ждём откат
        return trigger * (1 - offset)
    elif direction == "SHORT":
        # Вход выше триггера — ждём откат
        return trigger * (1 + offset)
    return trigger


def find_structural_stop_level(candles_15m, direction, entry,
                                ilm_extreme, sweep_extreme=None,
                                candles_1h=None):
    entry_f = _f(entry)
    if entry_f is None:
        return ilm_extreme

    candidates = []

    if candles_15m and len(candles_15m) >= 10:
        window = candles_15m[-STRUCTURAL_SL_LOOKBACK_15M:]
        if direction == "LONG":
            candidates.extend([p for _, p in _swing_lows(window)])
        elif direction == "SHORT":
            candidates.extend([p for _, p in _swing_highs(window)])

    if SL_USE_1H_SWINGS and candles_1h and len(candles_1h) >= 20:
        w1h = candles_1h[-60:]
        if direction == "LONG":
            candidates.extend([p for _, p in _swing_lows(w1h)])
        elif direction == "SHORT":
            candidates.extend([p for _, p in _swing_highs(w1h)])

    se = _f(sweep_extreme) if SL_USE_SWEEP_EXTREME else None
    if se is not None:
        candidates.append(se)

    ie = _f(ilm_extreme)
    if ie is not None:
        candidates.append(ie)

    candidates = [c for c in candidates if c is not None]
    if not candidates:
        return ilm_extreme

    if direction == "LONG":
        if se is not None and se < entry_f:
            return se
        below = [c for c in candidates if c < entry_f]
        if not below:
            return ilm_extreme
        return max(below)

    if direction == "SHORT":
        if se is not None and se > entry_f:
            return se
        above = [c for c in candidates if c > entry_f]
        if not above:
            return ilm_extreme
        return min(above)

    return ilm_extreme


def calculate_stop(entry, structural_level, direction, atr=None):
    entry = _f(entry)
    level = _f(structural_level)
    if entry is None or level is None:
        return None

    if USE_ATR_SCALING and atr is not None and atr > 0:
        min_dist = atr * ATR_SL_MULT_SOFT
        max_dist = atr * ATR_SL_MAX_MULT
    else:
        min_dist = entry * MIN_SL_DISTANCE_PCT / 100
        max_dist = entry * MAX_SL_DISTANCE_PCT / 100

    if direction == "LONG":
        sl = level * (1 - SL_BUFFER_PCT / 100)
        if (entry - sl) < min_dist:
            sl = entry - min_dist
        if (entry - sl) > max_dist:
            sl = entry - max_dist
        return sl if sl < entry else None

    if direction == "SHORT":
        sl = level * (1 + SL_BUFFER_PCT / 100)
        if (sl - entry) < min_dist:
            sl = entry + min_dist
        if (sl - entry) > max_dist:
            sl = entry + max_dist
        return sl if sl > entry else None

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


def _score(direction, context_direction, sweep, confirmation_strength,
           bos, ilm, rr, major_strength, fvg_bonus):
    score = 0

    if direction == context_direction:
        score += 15
    elif context_direction == "NEUTRAL":
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

    if confirmation_strength >= 0.75:
        score += 15
    elif confirmation_strength >= 0.50:
        score += 11
    elif confirmation_strength > 0:
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

    score += min(10, major_strength / 10.0)
    score += fvg_bonus

    return int(min(100, max(0, round(score))))


def _analyze_scenario(candles_1h, candles_15m, candles_5m, current_price,
                       major_levels, direction, context_direction,
                       d1_context=None, fvgs=None, symbol=None):
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
        "sl_distance_pct": None,
        "atr_15m": None,
        "sl_source": None,
    }

    if direction == "SHORT" and not ALLOW_SHORT:
        result["reason"] = "SHORT disabled"
        return result

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

    conf_ok, conf_text, conf_time, bos = confirmation_15m(
        candles_15m, sweep, direction)

    result["confirmation_15m"] = conf_ok
    result["confirmation_15m_time"] = conf_time
    result["confirmation"] = conf_text
    result["bos"] = bos

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

    ilm_age = int(ilm.get("age_candles", 0))
    if ilm_age > MAX_ILM_AGE_CANDLES_5M:
        result["score"] = 65
        result["reason"] = f"ILM устарел ({ilm_age} свечей 5M)."
        return result

    if ilm_age > MAX_ILM_AGE_FOR_ENTRY:
        result["score"] = 68
        result["reason"] = (
            f"ILM триггер слишком старый для входа "
            f"({ilm_age} > {MAX_ILM_AGE_FOR_ENTRY})."
        )
        return result

    entry = calculate_entry(ilm, price, direction)
    if entry is None:
        result["score"] = 68
        result["reason"] = "Не удалось определить Entry (ILM trigger)."
        return result

    dist_pct = _distance_pct(price, entry)
    if dist_pct is None or dist_pct > ENTRY_TOLERANCE_PCT:
        result["score"] = 68
        result["reason"] = (
            f"Цена ушла от entry на {dist_pct:.2f}% "
            f"(лимит {ENTRY_TOLERANCE_PCT}%)."
        )
        return result

    ilm_extreme = _f(ilm.get("extreme"))

    if ENABLE_VOLATILITY_FILTER:
        atr_fast, atr_slow = _avg_atr(candles_15m, fast=14, slow=50)
        if (atr_fast is not None and atr_slow is not None
                and atr_slow > 0
                and atr_fast > atr_slow * VOLATILITY_ATR_SPIKE_MULT):
            result["score"] = 70
            result["reason"] = (
                f"Volatility spike: ATR {atr_fast:.4f} "
                f"> {VOLATILITY_ATR_SPIKE_MULT}x avg {atr_slow:.4f}"
            )
            return result

    atr_15m = calculate_atr(candles_15m, 14)

    structural_level = find_structural_stop_level(
        candles_15m, direction, entry, ilm_extreme,
        sweep_extreme=sweep.get("extreme") if sweep else None,
        candles_1h=candles_1h,
    )

    sl = calculate_stop(entry, structural_level, direction, atr=atr_15m)
    if sl is None:
        result["score"] = 68
        result["reason"] = "Не удалось построить структурный SL."
        return result

    tp = calculate_tp_by_rr(entry, sl, direction, FIXED_RR)
    if tp is None:
        result["score"] = 70
        result["reason"] = "Не удалось рассчитать TP."
        return result

    if not validate_geometry(entry, sl, tp, direction):
        result["score"] = 68
        result["reason"] = "Некорректная геометрия сделки."
        return result

    result["geometry_valid"] = True

    rr_value = calculate_rr(entry, sl, tp)
    if rr_value is None:
        result["score"] = 68
        result["reason"] = "Не удалось рассчитать RR."
        return result

    result.update({
        "entry": round(entry, 8),
        "sl": round(sl, 8),
        "tp": round(tp, 8),
        "rr": round(rr_value, 3),
        "tp_reason": f"Fixed RR 1:{FIXED_RR}",
    })

    try:
        result["sl_distance_pct"] = round(abs(entry - sl) / entry * 100, 3)
        result["atr_15m"] = round(atr_15m, 6) if atr_15m else None
        if atr_15m and abs(entry - sl) < atr_15m * ATR_SL_MULT_SOFT * 1.001:
            result["sl_source"] = "atr_floor"
        else:
            result["sl_source"] = "structural"
    except Exception:
        pass

    try:
        major_strength = max([_level_strength(l) for l in levels] or [0])
    except Exception:
        major_strength = 0

    fvg_bonus, fvg_sweep, fvg_entry = compute_fvg_bonus(
        sweep, entry, fvgs or [], direction)
    result["fvg_bonus"] = fvg_bonus
    result["fvg_sweep"] = fvg_sweep
    result["fvg_entry"] = fvg_entry

    conf_strength = 0.8 if conf_ok else 0.6

    score = _score(
        direction=direction,
        context_direction=context_direction,
        sweep=sweep,
        confirmation_strength=conf_strength,
        bos=bos,
        ilm=ilm,
        rr=rr_value,
        major_strength=major_strength,
        fvg_bonus=fvg_bonus,
    )
    result["score"] = score

    trend_ok = trend_activity >= MIN_TREND_ACTIVITY_READY
    bos_ok = (not REQUIRE_BOS_FOR_READY) or bos

    ready_ok = (
        score >= MIN_SCORE_READY
        and trend_ok
        and bos_ok
    )

    if ready_ok:
        result["stage"] = "READY"
        result["reason"] = (
            f"Sweep → 15M → 5M ILM → Retest entry. "
            f"Trend {trend_activity:.2f}. RR 1:{FIXED_RR}. BOS."
        )
    else:
        result["stage"] = "15M_CONFIRMED"
        blocks = []
        if score < MIN_SCORE_READY:
            blocks.append(f"score {score}")
        if not trend_ok:
            blocks.append(f"trend {trend_activity:.2f}")
        if not bos_ok:
            blocks.append("no_bos")
        result["reason"] = ("READY заблокирован: " + ", ".join(blocks))

    return result


def analyze(candles_1h, candles_15m, candles_5m, current_price,
            major_levels=None, sweep=None, order_flow=None,
            candles_1m=None, d1_context=None, fvgs=None, symbol=None):
    price = _f(current_price)
    context_direction = get_1h_direction(candles_1h)

    base = {
        "stage": "WAIT",
        "direction": context_direction,
        "context_direction": context_direction,
        "d1_trend": (d1_context or {}).get("trend", "NEUTRAL"),
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

    if price is None or not candles_1h or not candles_15m or not candles_5m:
        base["reason"] = "Недостаточно рыночных данных."
        return base

    long_result = _analyze_scenario(
        candles_1h, candles_15m, candles_5m, price,
        major_levels, "LONG", context_direction,
        d1_context=d1_context, fvgs=fvgs, symbol=symbol,
    )

    short_result = _analyze_scenario(
        candles_1h, candles_15m, candles_5m, price,
        major_levels, "SHORT", context_direction,
        d1_context=d1_context, fvgs=fvgs, symbol=symbol,
    )

    base["long"] = long_result
    base["short"] = short_result

    if context_direction == "NEUTRAL":
        best = max([long_result, short_result],
                   key=lambda x: x.get("score", 0))
        base.update({
            "stage": "WAIT" if best.get("stage") == "READY"
                     else best.get("stage", "WAIT"),
            "direction": "NEUTRAL",
            "score": best.get("score", 0),
            "reason": "1H NEUTRAL. READY не разрешаем.",
            "entry": best.get("entry"),
            "sl": best.get("sl"),
            "tp": best.get("tp"),
            "tp_source": best.get("tp_source"),
            "rr": best.get("rr"),
            "sweep": best.get("sweep"),
            "confirmation_15m": best.get("confirmation_15m", False),
            "confirmation_15m_time": best.get("confirmation_15m_time"),
            "confirmation": best.get("confirmation"),
            "bos": best.get("bos", False),
            "ilm": best.get("ilm"),
            "sweep_extreme": best.get("sweep_extreme"),
            "tp_reason": best.get("tp_reason"),
            "geometry_valid": best.get("geometry_valid", False),
            "trend_activity": best.get("trend_activity", 0.0),
            "fvg_bonus": best.get("fvg_bonus", 0),
            "fvg_sweep": best.get("fvg_sweep", False),
            "fvg_entry": best.get("fvg_entry", False),
        })
        return base

    ready = []
    if (long_result.get("stage") == "READY"
            and long_result.get("score", 0) >= MIN_SCORE_READY):
        ready.append(long_result)
    if (short_result.get("stage") == "READY"
            and short_result.get("score", 0) >= MIN_SCORE_READY):
        ready.append(short_result)

    if ready:
        aligned = [x for x in ready if x["direction"] == context_direction]
        counter = [x for x in ready if x["direction"] != context_direction]

        if aligned:
            chosen = max(aligned, key=lambda x: x["score"])
        elif counter:
            counter_ready = [x for x in counter
                             if x["score"] >= COUNTER_TREND_MIN_SCORE]
            if not counter_ready:
                base["score"] = max(long_result["score"],
                                    short_result["score"])
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
        return {"READY": 5, "15M_CONFIRMED": 4,
                "SWEPT": 3, "WAIT": 1}.get(r.get("stage"), 0)

    aligned_candidates = [x for x in candidates
                          if x["direction"] == context_direction]
    pool = aligned_candidates if aligned_candidates else candidates

    chosen = max(pool, key=lambda x: (stage_weight(x), x.get("score", 0)))

    base.update({
        "stage": chosen.get("stage", "WAIT"),
        "direction": chosen.get("direction", context_direction),
        "score": chosen.get("score", 0),
        "reason": chosen.get("reason", ""),
        "entry": chosen.get("entry"),
        "sl": chosen.get("sl"),
        "tp": chosen.get("tp"),
        "tp_source": chosen.get("tp_source"),
        "rr": chosen.get("rr"),
        "sweep": chosen.get("sweep"),
        "confirmation_15m": chosen.get("confirmation_15m", False),
        "confirmation_15m_time": chosen.get("confirmation_15m_time"),
        "confirmation": chosen.get("confirmation"),
        "bos": chosen.get("bos", False),
        "ilm": chosen.get("ilm"),
        "sweep_extreme": chosen.get("sweep_extreme"),
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
    "get_higher_timeframe_direction",
    "measure_trend_activity",
    "find_sweep",
    "confirmation_15m",
    "detect_5m_ilm",
    "calculate_entry",
    "calculate_stop",
    "calculate_tp_by_rr",
    "calculate_rr",
    "find_structural_stop_level",
    "validate_geometry",
    "analyze",
    "analyze_sol",
]