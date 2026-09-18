"""
TradeMind 6.8
1H Context -> Major Liquidity -> Sweep -> 15M Confirmation -> 5M ILM -> Entry

Изменения vs 6.7:
- Тайминг-фильтр: READY только в торговые сессии MSK (09-13, 15-20).
- Активный тренд 1H: минимум MIN_TREND_ACTIVITY_READY для READY.
- D1 контекст: bonus за aligned trend, штраф за counter-trend.
- Recovery ratio для READY поднят до MIN_V_RECOVERY_FOR_READY (V-образность).
- TP resolution: D1 Point B -> Major -> Local swing.

Логика (как в видео):
- D1 задаёт глобальный тренд и точки A/B.
- 1H даёт контекст и уровень для sweep.
- 15M подтверждает разворот.
- 5M ILM даёт точку входа.
- TP = D1 Point B (приоритет), иначе major liquidity, иначе local swing.
- RR >= 1:2.
- Только в торговые сессии.
"""

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# VERSION
# ============================================================

STRATEGY_VERSION = "6.8"


# ============================================================
# SETTINGS
# ============================================================

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

# ---------- TIMING ----------

TRADING_SESSIONS_MSK = [
    (9, 13),
    (15, 20),
]

# ---------- TREND ACTIVITY ----------

MIN_TREND_ACTIVITY_READY = 0.45

# ---------- FALLBACK TP ----------

FALLBACK_5M_LOOKBACK = 60
FALLBACK_15M_LOOKBACK = 60

MIN_FALLBACK_DISTANCE_PCT = 0.30


# ============================================================
# TIMING
# ============================================================

def is_trading_session(now_utc=None):
    """True если текущее время попадает в торговую сессию (MSK)."""

    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    msk = now_utc + timedelta(hours=3)
    hour = msk.hour

    for start, end in TRADING_SESSIONS_MSK:
        if start <= hour < end:
            return True

    return False


def current_msk_hour(now_utc=None):
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    return (now_utc + timedelta(hours=3)).hour


# ============================================================
# BASIC HELPERS
# ============================================================

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
        }
        alias = aliases.get(key)
        if alias:
            value = candle.get(alias)

    if value is None:
        return default

    converted = _f(value)
    if converted is None:
        return default
    return converted


def _o(c): return _v(c, "open")
def _h(c): return _v(c, "high")
def _l(c): return _v(c, "low")
def _c(c): return _v(c, "close")
def _t(c): return _v(c, "open_time")


def _body(candle):
    opening = _o(candle)
    closing = _c(candle)
    if opening is None or closing is None:
        return 0.0
    return abs(closing - opening)


def _range(candle):
    high = _h(candle)
    low = _l(candle)
    if high is None or low is None:
        return 0.0
    return max(0.0, high - low)


def _body_ratio(candle):
    r = _range(candle)
    if r <= 0:
        return 0.0
    return _body(candle) / r


def _bull(candle):
    o = _o(candle)
    c = _c(candle)
    return o is not None and c is not None and c > o


def _bear(candle):
    o = _o(candle)
    c = _c(candle)
    return o is not None and c is not None and c < o


def _distance_pct(a, b):
    a = _f(a)
    b = _f(b)
    if a is None or b is None or b == 0:
        return None
    return abs(a - b) / abs(b) * 100


# ============================================================
# TREND ACTIVITY
# ============================================================

def measure_trend_activity(candles_1h, direction):
    """
    0..1 — насколько силён и активен тренд в направлении.
    Считает долю направленных тел свечей от общего объёма тел.
    """

    if not candles_1h or len(candles_1h) < 15:
        return 0.0

    recent = candles_1h[-20:]

    total_body = 0.0
    directional_body = 0.0

    for c in recent:
        body = _body(c)
        total_body += body

        if direction == "LONG" and _bull(c):
            directional_body += body
        elif direction == "SHORT" and _bear(c):
            directional_body += body

    if total_body <= 0:
        return 0.0

    return directional_body / total_body


# ============================================================
# 1H SWINGS
# ============================================================

def _swing_high(candles, index):
    if index < 2 or index >= len(candles) - 2:
        return False
    current = _h(candles[index])
    left_1 = _h(candles[index - 1])
    left_2 = _h(candles[index - 2])
    right_1 = _h(candles[index + 1])
    right_2 = _h(candles[index + 2])

    if any(x is None for x in (current, left_1, left_2, right_1, right_2)):
        return False

    return (
        current > left_1 and current >= left_2
        and current >= right_1 and current > right_2
    )


def _swing_low(candles, index):
    if index < 2 or index >= len(candles) - 2:
        return False
    current = _l(candles[index])
    left_1 = _l(candles[index - 1])
    left_2 = _l(candles[index - 2])
    right_1 = _l(candles[index + 1])
    right_2 = _l(candles[index + 2])

    if any(x is None for x in (current, left_1, left_2, right_1, right_2)):
        return False

    return (
        current < left_1 and current <= left_2
        and current <= right_1 and current < right_2
    )


def _swing_highs(candles):
    result = []
    if not candles:
        return result
    for i in range(len(candles)):
        if _swing_high(candles, i):
            p = _h(candles[i])
            if p is not None:
                result.append((i, p))
    return result


def _swing_lows(candles):
    result = []
    if not candles:
        return result
    for i in range(len(candles)):
        if _swing_low(candles, i):
            p = _l(candles[i])
            if p is not None:
                result.append((i, p))
    return result


# ============================================================
# 1H DIRECTION
# ============================================================

def get_1h_direction(candles):
    if not candles or len(candles) < 15:
        return "NEUTRAL"

    candles = candles[-60:]

    highs = _swing_highs(candles)
    lows = _swing_lows(candles)

    bullish_structure = False
    bearish_structure = False

    if len(highs) >= 2 and len(lows) >= 2:
        prev_h = highs[-2][1]
        last_h = highs[-1][1]
        prev_l = lows[-2][1]
        last_l = lows[-1][1]

        bullish_structure = last_h > prev_h and last_l > prev_l
        bearish_structure = last_h < prev_h and last_l < prev_l

    if not bullish_structure and not bearish_structure:
        recent = candles[-8:]
        bull_b = sum(_body(c) for c in recent if _bull(c))
        bear_b = sum(_body(c) for c in recent if _bear(c))

        if bull_b > 0 and bull_b > bear_b * 1.4:
            bullish_structure = True
        elif bear_b > 0 and bear_b > bull_b * 1.4:
            bearish_structure = True

    if bullish_structure and not bearish_structure:
        return "LONG"
    if bearish_structure and not bullish_structure:
        return "SHORT"
    return "NEUTRAL"


def get_higher_timeframe_direction(candles_1h, candles_d1=None, candles_w1=None):
    return get_1h_direction(candles_1h)


# ============================================================
# LIQUIDITY HELPERS
# ============================================================

def _level_price(level):
    if isinstance(level, dict):
        return _f(level.get("price"))
    return _f(level)


def _level_side(level):
    if not isinstance(level, dict):
        return None
    return str(
        level.get("side") or level.get("direction") or ""
    ).upper()


def _level_type(level):
    if not isinstance(level, dict):
        return ""
    return str(level.get("type") or "").upper()


def _level_strength(level):
    if not isinstance(level, dict):
        return 0
    value = level.get("strength") or level.get("touches") or 0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def _is_swept_level(level):
    if not isinstance(level, dict):
        return False
    return bool(
        level.get("swept")
        or level.get("taken")
        or level.get("used")
        or level.get("consumed")
    )


# ============================================================
# FIND CORRECT LEVELS
# ============================================================

def _levels_for_direction(major_levels, direction):
    result = []
    expected_type = "SSL" if direction == "LONG" else "BSL"

    for level in major_levels or []:
        price = _level_price(level)
        if price is None:
            continue

        side = _level_side(level)
        level_type = _level_type(level)

        if side == direction:
            result.append(level)
            continue

        if level_type == expected_type:
            result.append(level)
            continue

        if level_type.startswith(expected_type + "_"):
            result.append(level)
            continue

    return result


# ============================================================
# SWEEP
# ============================================================

def _sweep_candidate_score(candle, level, depth):
    strength = _level_strength(level)
    touches = level.get("touches", 1) if isinstance(level, dict) else 1

    return (
        depth * 4.0
        + strength / 20.0
        + min(touches, 5) * 3.0
    )


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

    for candle_idx, candle in enumerate(reversed(recent)):
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

                if (
                    low < price
                    and depth >= MIN_SWEEP_DEPTH_PCT
                    and close > price
                    and _bull(candle)
                ):
                    candidates.append({
                        "swept": True,
                        "direction": "LONG",
                        "level": price,
                        "extreme": low,
                        "open_time": _t(candle),
                        "price": low,
                        "liquidity_type": "SSL",
                        "touches": level.get("touches", 1),
                        "strength": level.get("strength", 0),
                        "depth_pct": depth,
                        "_score": (
                            _sweep_candidate_score(candle, level, depth)
                            - candle_idx * 2.0
                        ),
                    })

            else:
                high = _h(candle)
                close = _c(candle)
                if high is None or close is None:
                    continue

                depth = (high - price) / price * 100

                if (
                    high > price
                    and depth >= MIN_SWEEP_DEPTH_PCT
                    and close < price
                    and _bear(candle)
                ):
                    candidates.append({
                        "swept": True,
                        "direction": "SHORT",
                        "level": price,
                        "extreme": high,
                        "open_time": _t(candle),
                        "price": high,
                        "liquidity_type": "BSL",
                        "touches": level.get("touches", 1),
                        "strength": level.get("strength", 0),
                        "depth_pct": depth,
                        "_score": (
                            _sweep_candidate_score(candle, level, depth)
                            - candle_idx * 2.0
                        ),
                    })

    if not candidates:
        return None

    best = max(candidates, key=lambda x: x["_score"])
    best.pop("_score", None)
    return best


# ============================================================
# 15M LOCAL STRUCTURE
# ============================================================

def _local_high_15m(candles, index):
    if index < 1 or index >= len(candles) - 1:
        return False
    current = _h(candles[index])
    left = _h(candles[index - 1])
    right = _h(candles[index + 1])
    if current is None or left is None or right is None:
        return False
    return current >= left and current > right


def _local_low_15m(candles, index):
    if index < 1 or index >= len(candles) - 1:
        return False
    current = _l(candles[index])
    left = _l(candles[index - 1])
    right = _l(candles[index + 1])
    if current is None or left is None or right is None:
        return False
    return current <= left and current < right


# ============================================================
# 15M CONFIRMATION
# ============================================================

def confirmation_15m(candles_15m, sweep, direction):

    if not sweep:
        return False, None, None
    if direction not in {"LONG", "SHORT"}:
        return False, None, None
    if not candles_15m:
        return False, None, None

    sweep_time = _f(sweep.get("open_time"))

    candidates = []
    for candle in candles_15m:
        candle_time = _t(candle)
        if sweep_time is None:
            candidates.append(candle)
        elif candle_time is not None and candle_time > sweep_time:
            candidates.append(candle)

    candidates = candidates[-MAX_15M_CONFIRM_CANDLES:]

    if len(candidates) < 3:
        return False, None, None

    for i in range(1, len(candidates)):
        candle = candidates[i]

        if _body_ratio(candle) < MIN_BODY_RATIO:
            continue

        close = _c(candle)
        if close is None:
            continue

        if direction == "LONG":
            local_highs = []
            for j in range(i - 1):
                if _local_high_15m(candidates, j):
                    h = _h(candidates[j])
                    if h is not None:
                        local_highs.append(h)

            if not local_highs:
                continue

            reference = max(local_highs[-3:])

            if _bull(candle) and close > reference:
                return True, "15M bullish structure break", _t(candle)

        else:
            local_lows = []
            for j in range(i - 1):
                if _local_low_15m(candidates, j):
                    lo = _l(candidates[j])
                    if lo is not None:
                        local_lows.append(lo)

            if not local_lows:
                continue

            reference = min(local_lows[-3:])

            if _bear(candle) and close < reference:
                return True, "15M bearish structure break", _t(candle)

    return False, None, None


# ============================================================
# 5M LOCAL SWINGS
# ============================================================

def _is_local_high(candles, index):
    if index < 1 or index >= len(candles) - 1:
        return False
    current = _h(candles[index])
    left = _h(candles[index - 1])
    right = _h(candles[index + 1])
    if current is None or left is None or right is None:
        return False
    return current >= left and current > right


def _is_local_low(candles, index):
    if index < 1 or index >= len(candles) - 1:
        return False
    current = _l(candles[index])
    left = _l(candles[index - 1])
    right = _l(candles[index + 1])
    if current is None or left is None or right is None:
        return False
    return current <= left and current < right


# ============================================================
# 5M ILM
# ============================================================

def _ilm_long_candidate(candles, i, sweep_level, sweep_extreme):

    manipulation = candles[i]
    if not _is_local_low(candles, i):
        return None

    manipulation_low = _l(manipulation)
    manipulation_high = _h(manipulation)

    if manipulation_low is None or manipulation_high is None:
        return None

    before = candles[max(0, i - 2):i]
    after = candles[i + 1:min(len(candles), i + 4)]

    if not before or not after:
        return None

    before_lows = [_l(x) for x in before if _l(x) is not None]
    after_closes = [_c(x) for x in after if _c(x) is not None]

    if not before_lows or not after_closes:
        return None

    left_reference = min(before_lows)
    right_close = max(after_closes)

    if left_reference <= manipulation_low:
        return None

    manipulation_range = left_reference - manipulation_low
    if manipulation_range <= 0:
        return None

    manipulation_pct = manipulation_range / left_reference * 100
    if manipulation_pct < MIN_SWEEP_DEPTH_PCT:
        return None

    recovery_ratio = (right_close - manipulation_low) / manipulation_range
    if recovery_ratio < MIN_5M_RECOVERY_RATIO:
        return None

    trigger_index = None
    for j in range(i + 1, min(len(candles), i + 4)):
        trigger = candles[j]
        trigger_close = _c(trigger)

        if (
            _bull(trigger)
            and _body_ratio(trigger) >= MIN_BODY_RATIO
            and trigger_close is not None
            and trigger_close > manipulation_high
        ):
            trigger_index = j
            break

    if trigger_index is None:
        return None

    trigger = candles[trigger_index]
    trigger_close = _c(trigger)

    if sweep_level is not None:
        dist = abs(manipulation_low - sweep_level) / sweep_level * 100
        if dist > MIN_5M_ILM_SWEEP_DISTANCE_PCT:
            return None

    if (
        sweep_extreme is not None
        and manipulation_low > sweep_extreme
        * (1 + MIN_5M_ILM_SWEEP_DISTANCE_PCT / 100)
    ):
        return None

    return {
        "direction": "LONG",
        "extreme": manipulation_low,
        "trigger_time": _t(trigger),
        "trigger_price": trigger_close,
        "reason": (
            "5M V-ILM: downside manipulation "
            "+ recovery + bullish displacement"
        ),
        "recovery_ratio": recovery_ratio,
        "manipulation_pct": manipulation_pct,
        "_score": (
            recovery_ratio * 30
            + _body_ratio(trigger) * 20
            + manipulation_pct * 5
        ),
    }


def _ilm_short_candidate(candles, i, sweep_level, sweep_extreme):

    manipulation = candles[i]
    if not _is_local_high(candles, i):
        return None

    manipulation_high = _h(manipulation)
    manipulation_low = _l(manipulation)

    if manipulation_high is None or manipulation_low is None:
        return None

    before = candles[max(0, i - 2):i]
    after = candles[i + 1:min(len(candles), i + 4)]

    if not before or not after:
        return None

    before_highs = [_h(x) for x in before if _h(x) is not None]
    after_closes = [_c(x) for x in after if _c(x) is not None]

    if not before_highs or not after_closes:
        return None

    left_reference = max(before_highs)
    right_close = min(after_closes)

    if manipulation_high <= left_reference:
        return None

    manipulation_range = manipulation_high - left_reference
    if manipulation_range <= 0:
        return None

    manipulation_pct = manipulation_range / left_reference * 100
    if manipulation_pct < MIN_SWEEP_DEPTH_PCT:
        return None

    recovery_ratio = (manipulation_high - right_close) / manipulation_range
    if recovery_ratio < MIN_5M_RECOVERY_RATIO:
        return None

    trigger_index = None
    for j in range(i + 1, min(len(candles), i + 4)):
        trigger = candles[j]
        trigger_close = _c(trigger)

        if (
            _bear(trigger)
            and _body_ratio(trigger) >= MIN_BODY_RATIO
            and trigger_close is not None
            and trigger_close < manipulation_low
        ):
            trigger_index = j
            break

    if trigger_index is None:
        return None

    trigger = candles[trigger_index]
    trigger_close = _c(trigger)

    if sweep_level is not None:
        dist = abs(manipulation_high - sweep_level) / sweep_level * 100
        if dist > MIN_5M_ILM_SWEEP_DISTANCE_PCT:
            return None

    if (
        sweep_extreme is not None
        and manipulation_high < sweep_extreme
        * (1 - MIN_5M_ILM_SWEEP_DISTANCE_PCT / 100)
    ):
        return None

    return {
        "direction": "SHORT",
        "extreme": manipulation_high,
        "trigger_time": _t(trigger),
        "trigger_price": trigger_close,
        "reason": (
            "5M L-ILM: upside manipulation "
            "+ recovery + bearish displacement"
        ),
        "recovery_ratio": recovery_ratio,
        "manipulation_pct": manipulation_pct,
        "_score": (
            recovery_ratio * 30
            + _body_ratio(trigger) * 20
            + manipulation_pct * 5
        ),
    }


def detect_5m_ilm(candles_5m, sweep, direction, confirmation_time=None):

    if not sweep:
        return False, None
    if direction not in {"LONG", "SHORT"}:
        return False, None

    start_time = _f(confirmation_time)
    if start_time is None:
        start_time = _f(sweep.get("open_time"))

    candles = []
    for candle in candles_5m or []:
        candle_time = _t(candle)
        if start_time is None:
            candles.append(candle)
        elif candle_time is not None and candle_time > start_time:
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
# TARGET (MAJOR)
# ============================================================

def next_target(major_levels, direction, current_price, exclude_level=None):

    price = _f(current_price)
    excluded = _f(exclude_level)

    if price is None:
        return None

    candidates = []

    for level in major_levels or []:
        if _is_swept_level(level):
            continue

        level_price = _level_price(level)
        if level_price is None:
            continue

        if excluded is not None:
            distance = _distance_pct(level_price, excluded)
            if (
                distance is not None
                and distance < MIN_TARGET_DISTANCE_PCT
            ):
                continue

        if direction == "LONG":
            if level_price > price:
                candidates.append(level_price)
        elif direction == "SHORT":
            if level_price < price:
                candidates.append(level_price)

    if not candidates:
        return None

    return min(candidates, key=lambda x: abs(x - price))


# ============================================================
# TARGET (LOCAL SWING FALLBACK)
# ============================================================

def _scan_local_targets(candles, direction, entry, lookback, label):

    results = []
    if not candles or len(candles) < 5:
        return results

    recent = candles[-lookback:]
    if len(recent) < 5:
        return results

    for i in range(1, len(recent) - 1):
        candle = recent[i]

        if direction == "LONG":
            if not _is_local_high(recent, i):
                continue
            price = _h(candle)
            if price is None or price <= entry:
                continue
            distance_pct = (price - entry) / entry * 100
        else:
            if not _is_local_low(recent, i):
                continue
            price = _l(candle)
            if price is None or price >= entry:
                continue
            distance_pct = (entry - price) / entry * 100

        if distance_pct < MIN_FALLBACK_DISTANCE_PCT:
            continue

        results.append({
            "price": price,
            "distance_pct": distance_pct,
            "source": label,
            "time": _t(candle),
        })

    return results


def detect_local_swing_target(candles_5m, candles_15m, direction, entry):

    entry = _f(entry)
    if entry is None or entry <= 0:
        return None

    candidates = []
    candidates.extend(_scan_local_targets(
        candles_5m, direction, entry,
        FALLBACK_5M_LOOKBACK, "5M local swing",
    ))
    candidates.extend(_scan_local_targets(
        candles_15m, direction, entry,
        FALLBACK_15M_LOOKBACK, "15M local swing",
    ))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x["distance_pct"])
    return candidates[0]


# ============================================================
# RESOLVE TP
# ============================================================

def resolve_target(
    major_levels, direction, entry, sweep_level,
    candles_5m, candles_15m, d1_context=None,
):

    # 1. D1 Point B
    if d1_context:
        point_b = _f(d1_context.get("point_b"))
        if point_b is not None:
            if direction == "LONG" and point_b > entry:
                dist = (point_b - entry) / entry * 100
                if dist >= MIN_TARGET_DISTANCE_PCT:
                    return (
                        point_b,
                        "d1",
                        f"TP = D1 Point B ({point_b:.4f}).",
                    )
            elif direction == "SHORT" and point_b < entry:
                dist = (entry - point_b) / entry * 100
                if dist >= MIN_TARGET_DISTANCE_PCT:
                    return (
                        point_b,
                        "d1",
                        f"TP = D1 Point B ({point_b:.4f}).",
                    )

    # 2. Major liquidity
    tp_major = next_target(major_levels, direction, entry, sweep_level)

    if tp_major is not None:
        return (
            tp_major,
            "major",
            "TP = следующая свежая Major Liquidity.",
        )

    # 3. Local swing fallback
    fallback = detect_local_swing_target(
        candles_5m, candles_15m, direction, entry,
    )

    if fallback is not None:
        return (
            fallback["price"],
            "local",
            f"TP = ближайший {fallback['source']} "
            f"(major liquidity отсутствует).",
        )

    return None, None, "Ни major, ни local TP не найдены."


# ============================================================
# ENTRY / STOP / TP / RR
# ============================================================

def calculate_entry(current_price, direction):
    price = _f(current_price)
    if price is None:
        return None
    if direction not in {"LONG", "SHORT"}:
        return None
    return price


def calculate_stop(entry, extreme, direction):
    entry = _f(entry)
    extreme = _f(extreme)
    if entry is None or extreme is None:
        return None

    if direction == "LONG":
        sl = extreme * (1 - SL_BUFFER_PCT / 100)
        if sl >= entry:
            return None
        return sl

    if direction == "SHORT":
        sl = extreme * (1 + SL_BUFFER_PCT / 100)
        if sl <= entry:
            return None
        return sl

    return None


def calculate_take_profit(major_levels, direction, entry, sweep_level=None):
    return next_target(
        major_levels=major_levels,
        direction=direction,
        current_price=entry,
        exclude_level=sweep_level,
    )


def calculate_rr(entry, sl, tp, direction=None):
    entry = _f(entry)
    sl = _f(sl)
    tp = _f(tp)

    if entry is None or sl is None or tp is None:
        return None

    if direction == "LONG":
        if not (sl < entry < tp):
            return None
    elif direction == "SHORT":
        if not (tp < entry < sl):
            return None
    else:
        if sl == entry or tp == entry:
            return None

    risk = abs(entry - sl)
    reward = abs(tp - entry)

    if risk <= 0:
        return None

    return reward / risk


def validate_trade_geometry(entry, sl, tp, direction):
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


def validate_target(entry, tp, direction):
    entry = _f(entry)
    tp = _f(tp)
    if entry is None or tp is None:
        return False
    if direction == "LONG":
        return tp > entry
    if direction == "SHORT":
        return tp < entry
    return False


# ============================================================
# SCORE
# ============================================================

def _score(
    direction,
    context_direction,
    d1_context,
    sweep,
    confirmation_strength,
    ilm,
    rr,
    major_strength,
    tp_source="major",
):
    """
    Максимум 100.

    Context alignment    0-15
    D1 alignment         0-10 / penalty -10
    Sweep                0-20
    15M confirmation     0-15
    5M ILM               0-15
    RR                   0-20
    Liquidity strength   0-10
    Local TP penalty     x0.9
    """

    score = 0

    # Context
    if direction == context_direction:
        score += 15
    elif context_direction == "NEUTRAL":
        score += 5

    # D1 alignment
    if d1_context:
        d1_trend = d1_context.get("trend", "NEUTRAL")

        if d1_trend == direction:
            score += 10
        elif d1_trend == "NEUTRAL":
            score += 3
        else:
            score -= 10

    # Sweep
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

    # 15M confirmation
    if confirmation_strength >= 0.75:
        score += 15
    elif confirmation_strength >= 0.50:
        score += 11
    elif confirmation_strength > 0:
        score += 7

    # 5M ILM
    if ilm:
        recovery = ilm.get("recovery_ratio", 0)
        if recovery >= 0.66:
            score += 15
        elif recovery >= 0.50:
            score += 11
        else:
            score += 7

    # RR
    if rr is not None:
        if rr >= 4.0:
            score += 20
        elif rr >= 3.0:
            score += 16
        elif rr >= 2.5:
            score += 12
        elif rr >= 2.0:
            score += 8

    # Liquidity strength
    score += min(10, major_strength / 10.0)

    # Local TP penalty
    if tp_source == "local":
        score = score * 0.9

    return int(min(100, max(0, round(score))))


# ============================================================
# SINGLE SCENARIO
# ============================================================

def _analyze_scenario(
    candles_1h,
    candles_15m,
    candles_5m,
    current_price,
    major_levels,
    direction,
    context_direction,
    d1_context=None,
):

    result = {
        "stage": "WAIT",
        "direction": direction,
        "score": 0,
        "reason": "",
        "entry": None,
        "sl": None,
        "tp": None,
        "tp_source": None,
        "rr": None,
        "sweep": None,
        "major_levels": major_levels or [],
        "confirmation_15m": False,
        "confirmation_15m_time": None,
        "confirmation": None,
        "ilm": None,
        "sweep_extreme": None,
        "tp_reason": None,
        "geometry_valid": False,
        "trend_activity": 0.0,
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
            f"Ждём "
            f"{'SSL sweep' if direction == 'LONG' else 'BSL sweep'}."
        )
        return result

    result["stage"] = "SWEPT"
    result["sweep_extreme"] = sweep.get("extreme")

    (
        confirmation_ok,
        confirmation_text,
        confirmation_time,
    ) = confirmation_15m(candles_15m, sweep, direction)

    result["confirmation_15m"] = confirmation_ok
    result["confirmation_15m_time"] = confirmation_time
    result["confirmation"] = confirmation_text

    if not confirmation_ok:
        result["score"] = 50
        result["reason"] = "Sweep есть. Ждём 15M confirmation."
        return result

    result["stage"] = "15M_CONFIRMED"

    ilm_ok, ilm = detect_5m_ilm(
        candles_5m, sweep, direction, confirmation_time,
    )
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
        major_levels=major_levels,
        direction=direction,
        entry=entry,
        sweep_level=sweep.get("level"),
        candles_5m=candles_5m,
        candles_15m=candles_15m,
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
        major_strength = max(
            [_level_strength(level) for level in levels] or [0]
        )
    except Exception:
        major_strength = 0

    # RR block
    if rr_value < MIN_RR:
        result["score"] = min(
            _score(
                direction=direction,
                context_direction=context_direction,
                d1_context=d1_context,
                sweep=sweep,
                confirmation_strength=0.6,
                ilm=ilm,
                rr=None,
                major_strength=major_strength,
                tp_source=tp_source,
            ),
            79,
        )
        result["stage"] = "15M_CONFIRMED"
        result["reason"] = (
            f"RR 1:{rr_value:.2f} < 1:2. Вход запрещён. "
            f"Ждём ретест к более выгодной цене."
        )
        return result

    conf_strength = 0.8 if result.get("confirmation_15m") else 0.6

    score = _score(
        direction=direction,
        context_direction=context_direction,
        d1_context=d1_context,
        sweep=sweep,
        confirmation_strength=conf_strength,
        ilm=ilm,
        rr=rr_value,
        major_strength=major_strength,
        tp_source=tp_source,
    )

    result["score"] = score

    # ---------- EXTRA READY GATES ----------

    in_session = is_trading_session()

    trend_ok = trend_activity >= MIN_TREND_ACTIVITY_READY

    recovery = (ilm or {}).get("recovery_ratio", 0)
    v_ok = recovery >= MIN_V_RECOVERY_FOR_READY

    ready_gates_ok = (
        score >= MIN_SCORE_READY
        and in_session
        and trend_ok
        and v_ok
    )

    if ready_gates_ok:
        result["stage"] = "READY"
        result["reason"] = (
            f"Sweep → 15M → 5M ILM. "
            f"Trend: {trend_activity:.2f}, "
            f"Recovery: {recovery:.2f}, "
            f"TP: {tp_source}."
        )
    else:
        result["stage"] = "15M_CONFIRMED"
        blocks = []
        if score < MIN_SCORE_READY:
            blocks.append(f"score {score}")
        if not in_session:
            blocks.append("вне сессии")
        if not trend_ok:
            blocks.append(f"trend {trend_activity:.2f}")
        if not v_ok:
            blocks.append(f"recovery {recovery:.2f}")

        result["reason"] = (
            f"Сетап есть, но READY заблокирован: "
            + ", ".join(blocks)
        )

    return result


# ============================================================
# MAIN ANALYSIS
# ============================================================

def analyze(
    candles_1h,
    candles_15m,
    candles_5m,
    current_price,
    major_levels=None,
    sweep=None,
    order_flow=None,
    candles_1m=None,
    d1_context=None,
):

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
        "tp_source": None,
        "rr": None,
        "sweep": None,
        "major_levels": major_levels or [],
        "confirmation_15m": False,
        "confirmation_15m_time": None,
        "confirmation": None,
        "ilm": None,
        "sweep_extreme": None,
        "tp_reason": None,
        "geometry_valid": False,
        "trend_activity": 0.0,
        "long": None,
        "short": None,
    }

    if price is None or not candles_1h or not candles_15m or not candles_5m:
        base["reason"] = "Недостаточно рыночных данных."
        return base

    long_result = _analyze_scenario(
        candles_1h, candles_15m, candles_5m,
        price, major_levels, "LONG", context_direction,
        d1_context=d1_context,
    )

    short_result = _analyze_scenario(
        candles_1h, candles_15m, candles_5m,
        price, major_levels, "SHORT", context_direction,
        d1_context=d1_context,
    )

    base["long"] = long_result
    base["short"] = short_result

    # ---------- NEUTRAL 1H ----------
    if context_direction == "NEUTRAL":
        best = max(
            [long_result, short_result],
            key=lambda x: x.get("score", 0),
        )

        base.update({
            "stage": (
                "WAIT"
                if best.get("stage") == "READY"
                else best.get("stage", "WAIT")
            ),
            "direction": "NEUTRAL",
            "score": best.get("score", 0),
            "reason": (
                "1H NEUTRAL. Следим за структурой, "
                "но READY не разрешаем."
            ),
            "entry": best.get("entry"),
            "sl": best.get("sl"),
            "tp": best.get("tp"),
            "tp_source": best.get("tp_source"),
            "rr": best.get("rr"),
            "sweep": best.get("sweep"),
            "confirmation_15m": best.get("confirmation_15m", False),
            "confirmation_15m_time": best.get("confirmation_15m_time"),
            "confirmation": best.get("confirmation"),
            "ilm": best.get("ilm"),
            "sweep_extreme": best.get("sweep_extreme"),
            "tp_reason": best.get("tp_reason"),
            "geometry_valid": best.get("geometry_valid", False),
            "trend_activity": best.get("trend_activity", 0.0),
        })
        return base

    # ---------- READY CANDIDATES ----------
    ready = []

    if (
        long_result.get("stage") == "READY"
        and long_result.get("score", 0) >= MIN_SCORE_READY
    ):
        ready.append(long_result)

    if (
        short_result.get("stage") == "READY"
        and short_result.get("score", 0) >= MIN_SCORE_READY
    ):
        ready.append(short_result)

    if ready:
        aligned = [x for x in ready if x["direction"] == context_direction]
        counter = [x for x in ready if x["direction"] != context_direction]

        if aligned:
            chosen = max(aligned, key=lambda x: x["score"])
        elif counter:
            counter_ready = [
                x for x in counter
                if x["score"] >= COUNTER_TREND_MIN_SCORE
            ]
            if not counter_ready:
                base["score"] = max(
                    long_result["score"], short_result["score"],
                )
                base["reason"] = (
                    "Есть контртрендовый сценарий, "
                    "но он недостаточно сильный для READY."
                )
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

    # ---------- NO READY ----------
    candidates = [long_result, short_result]

    def stage_weight(r):
        return {
            "READY": 5,
            "15M_CONFIRMED": 4,
            "SWEPT": 3,
            "WAIT": 1,
        }.get(r.get("stage"), 0)

    aligned_candidates = [
        x for x in candidates
        if x["direction"] == context_direction
    ]
    pool = aligned_candidates if aligned_candidates else candidates

    chosen = max(
        pool,
        key=lambda x: (stage_weight(x), x.get("score", 0)),
    )

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
        "ilm": chosen.get("ilm"),
        "sweep_extreme": chosen.get("sweep_extreme"),
        "tp_reason": chosen.get("tp_reason"),
        "geometry_valid": chosen.get("geometry_valid", False),
        "trend_activity": chosen.get("trend_activity", 0.0),
    })

    base["context_direction"] = context_direction

    if (
        context_direction == "LONG"
        and short_result["stage"] in {"SWEPT", "15M_CONFIRMED", "READY"}
    ):
        base["reason"] = (
            f"{base['reason']} "
            f"SHORT-сценарий также активен: {short_result['stage']}."
        )
    elif (
        context_direction == "SHORT"
        and long_result["stage"] in {"SWEPT", "15M_CONFIRMED", "READY"}
    ):
        base["reason"] = (
            f"{base['reason']} "
            f"LONG-сценарий также активен: {long_result['stage']}."
        )

    return base


# ============================================================
# SOL COMPATIBILITY
# ============================================================

def analyze_sol(*args, **kwargs):
    return analyze(*args, **kwargs)


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "STRATEGY_VERSION",
    "MIN_SCORE_READY",
    "MIN_RR",
    "SL_BUFFER_PCT",
    "TRADING_SESSIONS_MSK",
    "MIN_TREND_ACTIVITY_READY",
    "MIN_V_RECOVERY_FOR_READY",
    "get_1h_direction",
    "get_higher_timeframe_direction",
    "measure_trend_activity",
    "is_trading_session",
    "current_msk_hour",
    "find_sweep",
    "confirmation_15m",
    "detect_5m_ilm",
    "calculate_entry",
    "calculate_stop",
    "calculate_take_profit",
    "calculate_rr",
    "validate_trade_geometry",
    "validate_target",
    "next_target",
    "detect_local_swing_target",
    "resolve_target",
    "analyze",
    "analyze_sol",
]