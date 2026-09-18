"""
TradeMind Strategy 7.0

Основная логика:

1H → Major Liquidity → Sweep → 15M Confirmation → 5M ILM → Entry

Правила:
- D1/W1 НЕ участвуют в торговом решении.
- Направление берём только с 1H.
- Major Liquidity = только 1H liquidity.
- LONG:
    1H LONG
    ↓
    SSL sweep
    ↓
    15M bullish confirmation
    ↓
    5M V-ILM
    ↓
    Entry
    ↓
    следующая Major BSL
- SHORT:
    1H SHORT
    ↓
    BSL sweep
    ↓
    15M bearish confirmation
    ↓
    5M L-ILM
    ↓
    Entry
    ↓
    следующая Major SSL

TP:
- только следующая свежая Major Liquidity;
- D1 Point B НЕ используется;
- локальные 5M/15M swing НЕ используются как основной TP;
- если ближайшая Major Liquidity даёт RR < 1:2 — вход запрещён;
- TP не отодвигается искусственно ради RR.

SL:
- за sweep/ILM extreme;
- buffer = 0.20%.

READY:
- score >= 80;
- trend activity >= 0.45;
- recovery >= 0.45;
- RR >= 1:2;
- все обязательные этапы пройдены.

Контртрендовые сделки НЕ разрешаются.
"""

from typing import Any, Dict, List, Optional


STRATEGY_VERSION = "7.0"

# ============================================================
# CORE SETTINGS
# ============================================================

MIN_SCORE_READY = 80
MIN_RR = 2.0

SL_BUFFER_PCT = 0.20

MIN_SWEEP_DEPTH_PCT = 0.15

MIN_5M_RECOVERY_RATIO = 0.33
MIN_V_RECOVERY_FOR_READY = 0.45

MIN_BODY_RATIO = 0.35

MAX_SWEEP_AGE_1H = 8
MAX_15M_CONFIRM_CANDLES = 12
MAX_5M_ILM_CANDLES = 40

MIN_5M_ILM_SWEEP_DISTANCE_PCT = 0.75

# Только для проверки, что target не совпадает со sweep.
MIN_TARGET_DISTANCE_PCT = 0.30

MIN_TREND_ACTIVITY_READY = 0.45

# Fallback targets НЕ используются как TP сделки.
# Оставлены только для совместимости API.
FALLBACK_5M_LOOKBACK = 60
FALLBACK_15M_LOOKBACK = 60
MIN_FALLBACK_DISTANCE_PCT = 0.30

# ============================================================
# FVG
# ============================================================

FVG_TOLERANCE_PCT = 0.10

FVG_SWEEP_BONUS = 10
FVG_ENTRY_BONUS = 5
FVG_MAX_BONUS = 15


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
            "open": "o",
            "high": "h",
            "low": "l",
            "close": "c",
            "open_time": "time",
        }

        alias = aliases.get(key)

        if alias:
            value = candle.get(alias)

    if value is None:
        return default

    converted = _f(value)

    return converted if converted is not None else default


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

    if r <= 0:
        return 0.0

    return _body(c) / r


def _bull(c):
    o = _o(c)
    cl = _c(c)

    return (
        o is not None
        and cl is not None
        and cl > o
    )


def _bear(c):
    o = _o(c)
    cl = _c(c)

    return (
        o is not None
        and cl is not None
        and cl < o
    )


def _distance_pct(a, b):
    a = _f(a)
    b = _f(b)

    if a is None or b is None or b == 0:
        return None

    return abs(a - b) / abs(b) * 100.0


# ============================================================
# TREND ACTIVITY
# ============================================================

def measure_trend_activity(candles_1h, direction):
    """
    Насколько последние 1H свечи реально поддерживают направление.

    LONG:
        сумма bullish bodies / сумма всех bodies

    SHORT:
        сумма bearish bodies / сумма всех bodies
    """

    if not candles_1h or len(candles_1h) < 15:
        return 0.0

    recent = candles_1h[-20:]

    total = 0.0
    directional = 0.0

    for candle in recent:
        body = _body(candle)

        total += body

        if direction == "LONG" and _bull(candle):
            directional += body

        elif direction == "SHORT" and _bear(candle):
            directional += body

    if total <= 0:
        return 0.0

    return directional / total


# ============================================================
# 1H STRUCTURE
# ============================================================

def _swing_high(candles, i):
    if i < 2 or i >= len(candles) - 2:
        return False

    cur = _h(candles[i])

    l1 = _h(candles[i - 1])
    l2 = _h(candles[i - 2])

    r1 = _h(candles[i + 1])
    r2 = _h(candles[i + 2])

    if any(
        x is None
        for x in (cur, l1, l2, r1, r2)
    ):
        return False

    return (
        cur > l1
        and cur >= l2
        and cur >= r1
        and cur > r2
    )


def _swing_low(candles, i):
    if i < 2 or i >= len(candles) - 2:
        return False

    cur = _l(candles[i])

    l1 = _l(candles[i - 1])
    l2 = _l(candles[i - 2])

    r1 = _l(candles[i + 1])
    r2 = _l(candles[i + 2])

    if any(
        x is None
        for x in (cur, l1, l2, r1, r2)
    ):
        return False

    return (
        cur < l1
        and cur <= l2
        and cur <= r1
        and cur < r2
    )


def _swing_highs(candles):
    result = []

    if not candles:
        return result

    for i in range(len(candles)):
        if _swing_high(candles, i):
            price = _h(candles[i])

            if price is not None:
                result.append((i, price))

    return result


def _swing_lows(candles):
    result = []

    if not candles:
        return result

    for i in range(len(candles)):
        if _swing_low(candles, i):
            price = _l(candles[i])

            if price is not None:
                result.append((i, price))

    return result


def get_1h_direction(candles):
    """
    Основное направление.

    Приоритет:
    1. HH + HL → LONG
    2. LH + LL → SHORT
    3. если структура недостаточно чистая —
       body activity последних 8 свечей.

    D1/W1 здесь намеренно отсутствуют.
    """

    if not candles or len(candles) < 15:
        return "NEUTRAL"

    candles = candles[-60:]

    highs = _swing_highs(candles)
    lows = _swing_lows(candles)

    bull = False
    bear = False

    if len(highs) >= 2 and len(lows) >= 2:

        last_high = highs[-1][1]
        prev_high = highs[-2][1]

        last_low = lows[-1][1]
        prev_low = lows[-2][1]

        bull = (
            last_high > prev_high
            and last_low > prev_low
        )

        bear = (
            last_high < prev_high
            and last_low < prev_low
        )

    if not bull and not bear:

        recent = candles[-8:]

        bullish_body = sum(
            _body(c)
            for c in recent
            if _bull(c)
        )

        bearish_body = sum(
            _body(c)
            for c in recent
            if _bear(c)
        )

        if (
            bullish_body > 0
            and bullish_body > bearish_body * 1.4
        ):
            bull = True

        elif (
            bearish_body > 0
            and bearish_body > bullish_body * 1.4
        ):
            bear = True

    if bull and not bear:
        return "LONG"

    if bear and not bull:
        return "SHORT"

    return "NEUTRAL"


def get_higher_timeframe_direction(
    candles_1h,
    candles_d1=None,
    candles_w1=None,
):
    """
    Совместимость со старым bot.py.

    D1/W1 намеренно игнорируются.
    """

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
        level.get("side")
        or level.get("direction")
        or ""
    ).upper()


def _level_type(level):
    if not isinstance(level, dict):
        return ""

    return str(
        level.get("type")
        or ""
    ).upper()


def _level_strength(level):
    if not isinstance(level, dict):
        return 0.0

    value = (
        level.get("strength")
        or level.get("touches")
        or 0
    )

    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _is_swept_level(level):
    """
    Не используем swept как основной фильтр Major Liquidity,
    но если market.py явно пометил уровень consumed/taken,
    для TP его использовать нельзя.
    """

    if not isinstance(level, dict):
        return False

    return bool(
        level.get("swept")
        or level.get("taken")
        or level.get("used")
        or level.get("consumed")
    )


def _levels_for_direction(
    major_levels,
    direction,
):
    """
    LONG → SSL.
    SHORT → BSL.

    ВАЖНО:
    Major Liquidity предполагается 1H.
    """

    result = []

    expected_type = (
        "SSL"
        if direction == "LONG"
        else "BSL"
    )

    for level in major_levels or []:

        price = _level_price(level)

        if price is None:
            continue

        side = _level_side(level)
        level_type = _level_type(level)

        if (
            side == direction
            or level_type == expected_type
            or level_type.startswith(
                expected_type + "_"
            )
        ):
            result.append(level)

    return result


# ============================================================
# FVG
# ============================================================

def is_inside_fvg(
    price,
    fvgs,
    direction,
    tolerance_pct=FVG_TOLERANCE_PCT,
):
    p = _f(price)

    if p is None or not fvgs:
        return False

    expected = (
        "bullish"
        if direction == "LONG"
        else "bearish"
    )

    for fvg in fvgs:

        if not isinstance(fvg, dict):
            continue

        if fvg.get("type") != expected:
            continue

        top = _f(fvg.get("top"))
        bottom = _f(fvg.get("bottom"))

        if top is None or bottom is None:
            continue

        tolerance = (
            top * tolerance_pct / 100.0
        )

        if (
            bottom - tolerance
            <= p
            <= top + tolerance
        ):
            return True

    return False


def compute_fvg_bonus(
    sweep,
    entry,
    fvgs,
    direction,
):
    """
    Возвращает:

    bonus,
    sweep_inside,
    entry_inside
    """

    if not fvgs:
        return 0, False, False

    sweep_extreme = _f(
        (sweep or {}).get("extreme")
    )

    entry_price = _f(entry)

    sweep_inside = False
    entry_inside = False

    if sweep_extreme is not None:
        sweep_inside = is_inside_fvg(
            sweep_extreme,
            fvgs,
            direction,
        )

    if entry_price is not None:
        entry_inside = is_inside_fvg(
            entry_price,
            fvgs,
            direction,
        )

    bonus = 0

    if sweep_inside:
        bonus += FVG_SWEEP_BONUS

    if entry_inside:
        bonus += FVG_ENTRY_BONUS

    return (
        min(bonus, FVG_MAX_BONUS),
        sweep_inside,
        entry_inside,
    )


# ============================================================
# 1H SWEEP
# ============================================================

def _sweep_candidate_score(
    candle,
    level,
    depth,
):
    strength = _level_strength(level)

    touches = (
        level.get("touches", 1)
        if isinstance(level, dict)
        else 1
    )

    return (
        depth * 4.0
        + strength / 20.0
        + min(touches, 5) * 3.0
    )


def find_sweep(
    candles_1h,
    major_levels,
    direction,
):
    """
    Ищем только свежий sweep последних MAX_SWEEP_AGE_1H
    подтверждённых 1H свечей.

    LONG:
        цена прокалывает SSL
        затем закрывается выше SSL
        bullish body

    SHORT:
        цена прокалывает BSL
        затем закрывается ниже BSL
        bearish body
    """

    if direction not in {
        "LONG",
        "SHORT",
    }:
        return None

    if not candles_1h or len(candles_1h) < 3:
        return None

    levels = _levels_for_direction(
        major_levels,
        direction,
    )

    if not levels:
        return None

    recent = candles_1h[
        -MAX_SWEEP_AGE_1H:
    ]

    candidates = []

    for reverse_index, candle in enumerate(
        reversed(recent)
    ):

        for level in levels:

            # Если market.py пометил уровень consumed,
            # текущий sweep по нему больше не рассматриваем.
            if _is_swept_level(level):
                continue

            level_price = _level_price(level)

            if level_price is None:
                continue

            # -----------------------------
            # LONG
            # -----------------------------

            if direction == "LONG":

                low = _l(candle)
                close = _c(candle)

                if low is None or close is None:
                    continue

                depth = (
                    (level_price - low)
                    / level_price
                    * 100.0
                )

                valid = (
                    low < level_price
                    and depth >= MIN_SWEEP_DEPTH_PCT
                    and close > level_price
                    and _bull(candle)
                )

                if not valid:
                    continue

                candidates.append({
                    "swept": True,
                    "direction": "LONG",
                    "level": level_price,
                    "extreme": low,
                    "open_time": _t(candle),
                    "price": low,
                    "liquidity_type": "SSL",
                    "touches": level.get(
                        "touches",
                        1,
                    ),
                    "strength": level.get(
                        "strength",
                        0,
                    ),
                    "depth_pct": depth,
                    "_score": (
                        _sweep_candidate_score(
                            candle,
                            level,
                            depth,
                        )
                        - reverse_index * 2.0
                    ),
                })

            # -----------------------------
            # SHORT
            # -----------------------------

            else:

                high = _h(candle)
                close = _c(candle)

                if high is None or close is None:
                    continue

                depth = (
                    (high - level_price)
                    / level_price
                    * 100.0
                )

                valid = (
                    high > level_price
                    and depth >= MIN_SWEEP_DEPTH_PCT
                    and close < level_price
                    and _bear(candle)
                )

                if not valid:
                    continue

                candidates.append({
                    "swept": True,
                    "direction": "SHORT",
                    "level": level_price,
                    "extreme": high,
                    "open_time": _t(candle),
                    "price": high,
                    "liquidity_type": "BSL",
                    "touches": level.get(
                        "touches",
                        1,
                    ),
                    "strength": level.get(
                        "strength",
                        0,
                    ),
                    "depth_pct": depth,
                    "_score": (
                        _sweep_candidate_score(
                            candle,
                            level,
                            depth,
                        )
                        - reverse_index * 2.0
                    ),
                })

    if not candidates:
        return None

    best = max(
        candidates,
        key=lambda x: x["_score"],
    )

    best.pop("_score", None)

    return best


# ============================================================
# 15M CONFIRMATION
# ============================================================

def _local_high_15m(candles, i):
    if i < 1 or i >= len(candles) - 1:
        return False

    cur = _h(candles[i])
    left = _h(candles[i - 1])
    right = _h(candles[i + 1])

    if (
        cur is None
        or left is None
        or right is None
    ):
        return False

    return (
        cur >= left
        and cur > right
    )


def _local_low_15m(candles, i):
    if i < 1 or i >= len(candles) - 1:
        return False

    cur = _l(candles[i])
    left = _l(candles[i - 1])
    right = _l(candles[i + 1])

    if (
        cur is None
        or left is None
        or right is None
    ):
        return False

    return (
        cur <= left
        and cur < right
    )


def confirmation_15m(
    candles_15m,
    sweep,
    direction,
):
    """
    После 1H sweep ждём структурное подтверждение на 15M.

    LONG:
        bullish displacement
        close выше последнего локального high

    SHORT:
        bearish displacement
        close ниже последнего локального low
    """

    if (
        not sweep
        or direction not in {
            "LONG",
            "SHORT",
        }
        or not candles_15m
    ):
        return False, None, None

    sweep_time = _f(
        sweep.get("open_time")
    )

    candidates = []

    for candle in candles_15m:

        candle_time = _t(candle)

        if sweep_time is None:
            candidates.append(candle)

        elif (
            candle_time is not None
            and candle_time > sweep_time
        ):
            candidates.append(candle)

    candidates = candidates[
        -MAX_15M_CONFIRM_CANDLES:
    ]

    if len(candidates) < 3:
        return False, None, None

    for i in range(
        1,
        len(candidates),
    ):

        candle = candidates[i]

        if (
            _body_ratio(candle)
            < MIN_BODY_RATIO
        ):
            continue

        close = _c(candle)

        if close is None:
            continue

        # ====================================================
        # LONG
        # ====================================================

        if direction == "LONG":

            local_highs = []

            for j in range(i - 1):

                if _local_high_15m(
                    candidates,
                    j,
                ):

                    high = _h(
                        candidates[j]
                    )

                    if high is not None:
                        local_highs.append(
                            high
                        )

            if not local_highs:
                continue

            reference = max(
                local_highs[-3:]
            )

            if (
                _bull(candle)
                and close > reference
            ):
                return (
                    True,
                    "15M bullish structure break",
                    _t(candle),
                )

        # ====================================================
        # SHORT
        # ====================================================

        else:

            local_lows = []

            for j in range(i - 1):

                if _local_low_15m(
                    candidates,
                    j,
                ):

                    low = _l(
                        candidates[j]
                    )

                    if low is not None:
                        local_lows.append(
                            low
                        )

            if not local_lows:
                continue

            reference = min(
                local_lows[-3:]
            )

            if (
                _bear(candle)
                and close < reference
            ):
                return (
                    True,
                    "15M bearish structure break",
                    _t(candle),
                )

    return False, None, None


# ============================================================
# 5M ILM
# ============================================================

def _is_local_high(candles, i):
    if i < 1 or i >= len(candles) - 1:
        return False

    cur = _h(candles[i])
    left = _h(candles[i - 1])
    right = _h(candles[i + 1])

    if (
        cur is None
        or left is None
        or right is None
    ):
        return False

    return (
        cur >= left
        and cur > right
    )


def _is_local_low(candles, i):
    if i < 1 or i >= len(candles) - 1:
        return False

    cur = _l(candles[i])
    left = _l(candles[i - 1])
    right = _l(candles[i + 1])

    if (
        cur is None
        or left is None
        or right is None
    ):
        return False

    return (
        cur <= left
        and cur < right
    )


def _ilm_long_candidate(
    candles,
    i,
    sweep_level,
    sweep_extreme,
):
    manipulation = candles[i]

    if not _is_local_low(
        candles,
        i,
    ):
        return None

    manipulation_low = _l(
        manipulation
    )

    manipulation_high = _h(
        manipulation
    )

    if (
        manipulation_low is None
        or manipulation_high is None
    ):
        return None

    before = candles[
        max(0, i - 2):i
    ]

    after = candles[
        i + 1:min(
            len(candles),
            i + 4,
        )
    ]

    if not before or not after:
        return None

    before_lows = [
        _l(c)
        for c in before
        if _l(c) is not None
    ]

    after_closes = [
        _c(c)
        for c in after
        if _c(c) is not None
    ]

    if (
        not before_lows
        or not after_closes
    ):
        return None

    left_reference = min(
        before_lows
    )

    right_close = max(
        after_closes
    )

    if left_reference <= manipulation_low:
        return None

    manipulation_range = (
        left_reference
        - manipulation_low
    )

    if manipulation_range <= 0:
        return None

    manipulation_pct = (
        manipulation_range
        / left_reference
        * 100.0
    )

    if (
        manipulation_pct
        < MIN_SWEEP_DEPTH_PCT
    ):
        return None

    recovery = (
        right_close
        - manipulation_low
    ) / manipulation_range

    if (
        recovery
        < MIN_5M_RECOVERY_RATIO
    ):
        return None

    trigger_index = None

    for j in range(
        i + 1,
        min(
            len(candles),
            i + 4,
        ),
    ):

        trigger = candles[j]
        trigger_close = _c(trigger)

        if (
            _bull(trigger)
            and _body_ratio(trigger)
            >= MIN_BODY_RATIO
            and trigger_close is not None
            and trigger_close
            > manipulation_high
        ):
            trigger_index = j
            break

    if trigger_index is None:
        return None

    trigger = candles[
        trigger_index
    ]

    trigger_close = _c(trigger)

    # Distance from 1H sweep
    if sweep_level is not None:

        distance = (
            abs(
                manipulation_low
                - sweep_level
            )
            / sweep_level
            * 100.0
        )

        if (
            distance
            > MIN_5M_ILM_SWEEP_DISTANCE_PCT
        ):
            return None

    # ILM extreme must remain reasonably
    # close to the original sweep extreme.
    if (
        sweep_extreme is not None
        and manipulation_low
        > sweep_extreme
        * (
            1
            + MIN_5M_ILM_SWEEP_DISTANCE_PCT
            / 100.0
        )
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
        "recovery_ratio": recovery,
        "manipulation_pct": manipulation_pct,
        "_score": (
            recovery * 30.0
            + _body_ratio(trigger) * 20.0
            + manipulation_pct * 5.0
        ),
    }


def _ilm_short_candidate(
    candles,
    i,
    sweep_level,
    sweep_extreme,
):
    manipulation = candles[i]

    if not _is_local_high(
        candles,
        i,
    ):
        return None

    manipulation_high = _h(
        manipulation
    )

    manipulation_low = _l(
        manipulation
    )

    if (
        manipulation_high is None
        or manipulation_low is None
    ):
        return None

    before = candles[
        max(0, i - 2):i
    ]

    after = candles[
        i + 1:min(
            len(candles),
            i + 4,
        )
    ]

    if not before or not after:
        return None

    before_highs = [
        _h(c)
        for c in before
        if _h(c) is not None
    ]

    after_closes = [
        _c(c)
        for c in after
        if _c(c) is not None
    ]

    if (
        not before_highs
        or not after_closes
    ):
        return None

    left_reference = max(
        before_highs
    )

    right_close = min(
        after_closes
    )

    if manipulation_high <= left_reference:
        return None

    manipulation_range = (
        manipulation_high
        - left_reference
    )

    if manipulation_range <= 0:
        return None

    manipulation_pct = (
        manipulation_range
        / left_reference
        * 100.0
    )

    if (
        manipulation_pct
        < MIN_SWEEP_DEPTH_PCT
    ):
        return None

    recovery = (
        manipulation_high
        - right_close
    ) / manipulation_range

    if (
        recovery
        < MIN_5M_RECOVERY_RATIO
    ):
        return None

    trigger_index = None

    for j in range(
        i + 1,
        min(
            len(candles),
            i + 4,
        ),
    ):

        trigger = candles[j]
        trigger_close = _c(trigger)

        if (
            _bear(trigger)
            and _body_ratio(trigger)
            >= MIN_BODY_RATIO
            and trigger_close is not None
            and trigger_close
            < manipulation_low
        ):
            trigger_index = j
            break

    if trigger_index is None:
        return None

    trigger = candles[
        trigger_index
    ]

    trigger_close = _c(trigger)

    if sweep_level is not None:

        distance = (
            abs(
                manipulation_high
                - sweep_level
            )
            / sweep_level
            * 100.0
        )

        if (
            distance
            > MIN_5M_ILM_SWEEP_DISTANCE_PCT
        ):
            return None

    if (
        sweep_extreme is not None
        and manipulation_high
        < sweep_extreme
        * (
            1
            - MIN_5M_ILM_SWEEP_DISTANCE_PCT
            / 100.0
        )
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
        "recovery_ratio": recovery,
        "manipulation_pct": manipulation_pct,
        "_score": (
            recovery * 30.0
            + _body_ratio(trigger) * 20.0
            + manipulation_pct * 5.0
        ),
    }


def detect_5m_ilm(
    candles_5m,
    sweep,
    direction,
    confirmation_time=None,
):
    if (
        not sweep
        or direction not in {
            "LONG",
            "SHORT",
        }
    ):
        return False, None

    start = (
        _f(confirmation_time)
        or _f(sweep.get("open_time"))
    )

    candles = []

    for candle in candles_5m or []:

        candle_time = _t(candle)

        if start is None:
            candles.append(candle)

        elif (
            candle_time is not None
            and candle_time > start
        ):
            candles.append(candle)

    candles = candles[
        -MAX_5M_ILM_CANDLES:
    ]

    if len(candles) < 5:
        return False, None

    sweep_level = _f(
        sweep.get("level")
    )

    sweep_extreme = _f(
        sweep.get("extreme")
    )

    candidates = []

    for i in range(
        2,
        len(candles) - 2,
    ):

        if direction == "LONG":

            ilm = _ilm_long_candidate(
                candles,
                i,
                sweep_level,
                sweep_extreme,
            )

        else:

            ilm = _ilm_short_candidate(
                candles,
                i,
                sweep_level,
                sweep_extreme,
            )

        if ilm:
            candidates.append(ilm)

    if not candidates:
        return False, None

    best = max(
        candidates,
        key=lambda x: x["_score"],
    )

    best.pop("_score", None)

    return True, best


# ============================================================
# MAJOR TARGET
# ============================================================

def next_target(
    major_levels,
    direction,
    current_price,
    exclude_level=None,
):
    """
    Только следующая Major Liquidity.

    LONG → ближайший unswept BSL выше Entry.
    SHORT → ближайший unswept SSL ниже Entry.

    Никаких D1.
    Никаких local fallback.
    """

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

            distance = _distance_pct(
                level_price,
                excluded,
            )

            if (
                distance is not None
                and distance
                < MIN_TARGET_DISTANCE_PCT
            ):
                continue

        if (
            direction == "LONG"
            and level_price > price
        ):
            candidates.append(
                level_price
            )

        elif (
            direction == "SHORT"
            and level_price < price
        ):
            candidates.append(
                level_price
            )

    if not candidates:
        return None

    return min(
        candidates,
        key=lambda x: abs(x - price),
    )


# ============================================================
# LOCAL TARGET — COMPATIBILITY ONLY
# ============================================================

def _scan_local_targets(
    candles,
    direction,
    entry,
    lookback,
    label,
):
    """
    Оставлено для совместимости.

    НЕ используется для реального TP
    в resolve_target().
    """

    results = []

    if not candles or len(candles) < 5:
        return results

    recent = candles[
        -lookback:
    ]

    if len(recent) < 5:
        return results

    for i in range(
        1,
        len(recent) - 1,
    ):

        candle = recent[i]

        if direction == "LONG":

            if not _is_local_high(
                recent,
                i,
            ):
                continue

            price = _h(candle)

            if (
                price is None
                or price <= entry
            ):
                continue

            distance = (
                price - entry
            ) / entry * 100.0

        else:

            if not _is_local_low(
                recent,
                i,
            ):
                continue

            price = _l(candle)

            if (
                price is None
                or price >= entry
            ):
                continue

            distance = (
                entry - price
            ) / entry * 100.0

        if (
            distance
            < MIN_FALLBACK_DISTANCE_PCT
        ):
            continue

        results.append({
            "price": price,
            "distance_pct": distance,
            "source": label,
            "time": _t(candle),
        })

    return results


def detect_local_swing_target(
    candles_5m,
    candles_15m,
    direction,
    entry,
):
    """
    Совместимость со старым кодом.

    Не используется как TP рабочего сетапа.
    """

    entry = _f(entry)

    if entry is None or entry <= 0:
        return None

    candidates = []

    candidates.extend(
        _scan_local_targets(
            candles_5m,
            direction,
            entry,
            FALLBACK_5M_LOOKBACK,
            "5M local swing",
        )
    )

    candidates.extend(
        _scan_local_targets(
            candles_15m,
            direction,
            entry,
            FALLBACK_15M_LOOKBACK,
            "15M local swing",
        )
    )

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x["distance_pct"]
    )

    return candidates[0]


def resolve_target(
    major_levels,
    direction,
    entry,
    sweep_level,
    candles_5m,
    candles_15m,
    d1_context=None,
):
    """
    НОВАЯ ЦЕЛЬ:

    D1 полностью исключён.

    TP:
        следующая Major Liquidity.

    Если Major TP отсутствует:
        TP отсутствует.

    Мы НЕ используем local swing,
    потому что пользователь закрепил
    Major Liquidity как основной target.
    """

    tp_major = next_target(
        major_levels=major_levels,
        direction=direction,
        current_price=entry,
        exclude_level=sweep_level,
    )

    if tp_major is not None:
        return (
            tp_major,
            "major",
            "TP = следующая свежая Major Liquidity 1H.",
        )

    return (
        None,
        None,
        "Следующая Major Liquidity не найдена.",
    )


# ============================================================
# ENTRY / STOP / RR
# ============================================================

def calculate_entry(
    current_price,
    direction,
):
    price = _f(current_price)

    if (
        price is None
        or direction not in {
            "LONG",
            "SHORT",
        }
    ):
        return None

    return price


def calculate_stop(
    entry,
    extreme,
    direction,
):
    e = _f(entry)
    x = _f(extreme)

    if e is None or x is None:
        return None

    if direction == "LONG":

        stop = (
            x
            * (
                1
                - SL_BUFFER_PCT / 100.0
            )
        )

        if stop < e:
            return stop

        return None

    if direction == "SHORT":

        stop = (
            x
            * (
                1
                + SL_BUFFER_PCT / 100.0
            )
        )

        if stop > e:
            return stop

        return None

    return None


def calculate_take_profit(
    major_levels,
    direction,
    entry,
    sweep_level=None,
):
    return next_target(
        major_levels,
        direction,
        entry,
        sweep_level,
    )


def calculate_rr(
    entry,
    sl,
    tp,
    direction=None,
):
    e = _f(entry)
    s = _f(sl)
    t = _f(tp)

    if (
        e is None
        or s is None
        or t is None
    ):
        return None

    if direction == "LONG":

        if not (
            s < e < t
        ):
            return None

    elif direction == "SHORT":

        if not (
            t < e < s
        ):
            return None

    else:

        if (
            s == e
            or t == e
        ):
            return None

    risk = abs(
        e - s
    )

    reward = abs(
        t - e
    )

    if risk <= 0:
        return None

    return reward / risk


def validate_trade_geometry(
    entry,
    sl,
    tp,
    direction,
):
    e = _f(entry)
    s = _f(sl)
    t = _f(tp)

    if (
        e is None
        or s is None
        or t is None
    ):
        return False

    if direction == "LONG":
        return s < e < t

    if direction == "SHORT":
        return t < e < s

    return False


def validate_target(
    entry,
    tp,
    direction,
):
    e = _f(entry)
    t = _f(tp)

    if e is None or t is None:
        return False

    if direction == "LONG":
        return t > e

    if direction == "SHORT":
        return t < e

    return False


# ============================================================
# SCORE
# ============================================================

def _score(
    direction,
    context_direction,
    sweep,
    confirmation_strength,
    ilm,
    rr,
    major_strength,
    tp_source="major",
    fvg_bonus=0,
):
    """
    Score только по рабочей структуре.

    D1 больше НЕ участвует.
    """

    score = 0

    # --------------------------------------------------------
    # 1H direction
    # --------------------------------------------------------

    if direction == context_direction:
        score += 20

    elif context_direction == "NEUTRAL":
        score += 0

    else:
        # Контртренд запрещаем отдельно.
        score -= 20

    # --------------------------------------------------------
    # SWEEP
    # --------------------------------------------------------

    if sweep:

        depth = sweep.get(
            "depth_pct",
            0,
        )

        if depth >= 0.40:
            score += 20

        elif depth >= 0.25:
            score += 15

        elif depth >= 0.15:
            score += 10

        else:
            score += 5

    # --------------------------------------------------------
    # 15M confirmation
    # --------------------------------------------------------

    if confirmation_strength >= 0.75:
        score += 15

    elif confirmation_strength >= 0.50:
        score += 11

    elif confirmation_strength > 0:
        score += 7

    # --------------------------------------------------------
    # 5M ILM
    # --------------------------------------------------------

    if ilm:

        recovery = ilm.get(
            "recovery_ratio",
            0,
        )

        if recovery >= 0.66:
            score += 15

        elif recovery >= 0.50:
            score += 11

        else:
            score += 7

    # --------------------------------------------------------
    # RR
    # --------------------------------------------------------

    if rr is not None:

        if rr >= 4.0:
            score += 15

        elif rr >= 3.0:
            score += 13

        elif rr >= 2.5:
            score += 11

        elif rr >= 2.0:
            score += 8

    # --------------------------------------------------------
    # Major strength
    # --------------------------------------------------------

    score += min(
        10,
        major_strength / 10.0,
    )

    # --------------------------------------------------------
    # FVG
    # --------------------------------------------------------

    score += fvg_bonus

    # --------------------------------------------------------
    # Local TP should never be used for
    # actual READY setup.
    # --------------------------------------------------------

    if tp_source == "local":
        score *= 0.9

    return int(
        min(
            100,
            max(
                0,
                round(score),
            ),
        )
    )


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
    fvgs=None,
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

        "fvg_bonus": 0,
        "fvg_sweep": False,
        "fvg_entry": False,
    }

    price = _f(current_price)

    if (
        price is None
        or not candles_1h
        or not candles_15m
        or not candles_5m
    ):
        result["reason"] = (
            "Недостаточно рыночных данных."
        )

        return result

    # ========================================================
    # 1H direction gate
    # ========================================================

    if (
        context_direction == "NEUTRAL"
        or direction != context_direction
    ):
        result["score"] = 0

        result["reason"] = (
            "1H направление не разрешает этот сценарий."
        )

        return result

    # ========================================================
    # Trend activity
    # ========================================================

    trend_activity = measure_trend_activity(
        candles_1h,
        direction,
    )

    result["trend_activity"] = round(
        trend_activity,
        3,
    )

    # ========================================================
    # Major Liquidity
    # ========================================================

    levels = _levels_for_direction(
        major_levels,
        direction,
    )

    if not levels:

        result["score"] = 20

        result["reason"] = (
            "Нет актуальной Major "
            f"{'SSL' if direction == 'LONG' else 'BSL'} 1H."
        )

        return result

    # ========================================================
    # Sweep
    # ========================================================

    sweep = find_sweep(
        candles_1h,
        levels,
        direction,
    )

    result["sweep"] = sweep

    if sweep is None:

        result["score"] = 25

        result["reason"] = (
            "Ждём "
            f"{'SSL sweep' if direction == 'LONG' else 'BSL sweep'}."
        )

        return result

    result["stage"] = "SWEPT"

    result["sweep_extreme"] = sweep.get(
        "extreme"
    )

    # ========================================================
    # 15M confirmation
    # ========================================================

    (
        confirmation_ok,
        confirmation_text,
        confirmation_time,
    ) = confirmation_15m(
        candles_15m,
        sweep,
        direction,
    )

    result["confirmation_15m"] = (
        confirmation_ok
    )

    result["confirmation_15m_time"] = (
        confirmation_time
    )

    result["confirmation"] = (
        confirmation_text
    )

    if not confirmation_ok:

        result["score"] = 50

        result["reason"] = (
            "Sweep есть. Ждём 15M confirmation."
        )

        return result

    result["stage"] = (
        "15M_CONFIRMED"
    )

    # ========================================================
    # 5M ILM
    # ========================================================

    ilm_ok, ilm = detect_5m_ilm(
        candles_5m,
        sweep,
        direction,
        confirmation_time,
    )

    result["ilm"] = ilm

    if not ilm_ok:

        result["score"] = 65

        result["reason"] = (
            "15M подтверждение есть. "
            "Ждём 5M ILM."
        )

        return result

    # ========================================================
    # Entry
    # ========================================================

    entry = calculate_entry(
        price,
        direction,
    )

    if entry is None:

        result["score"] = 68

        result["reason"] = (
            "Не удалось определить Entry."
        )

        return result

    # ========================================================
    # SL extreme
    # ========================================================

    ilm_extreme = _f(
        (ilm or {}).get("extreme")
    )

    sweep_extreme = _f(
        sweep.get("extreme")
    )

    candidates = [
        x
        for x in (
            ilm_extreme,
            sweep_extreme,
        )
        if x is not None
    ]

    if not candidates:

        result["score"] = 68

        result["reason"] = (
            "Не удалось определить экстремум для SL."
        )

        return result

    if direction == "LONG":

        extreme = min(
            candidates
        )

        if extreme >= entry:

            result["score"] = 68

            result["reason"] = (
                "LONG invalid: extreme >= Entry."
            )

            return result

    else:

        extreme = max(
            candidates
        )

        if extreme <= entry:

            result["score"] = 68

            result["reason"] = (
                "SHORT invalid: extreme <= Entry."
            )

            return result

    sl = calculate_stop(
        entry,
        extreme,
        direction,
    )

    if sl is None:

        result["score"] = 68

        result["reason"] = (
            "Не удалось построить корректный SL."
        )

        return result

    # ========================================================
    # TP — ONLY MAJOR
    # ========================================================

    tp, tp_source, tp_reason = resolve_target(
        major_levels=major_levels,
        direction=direction,
        entry=entry,
        sweep_level=sweep.get("level"),
        candles_5m=candles_5m,
        candles_15m=candles_15m,
        d1_context=None,
    )

    result["tp_reason"] = tp_reason
    result["tp_source"] = tp_source

    if tp is None:

        result["score"] = 70

        result["reason"] = tp_reason

        return result

    # ========================================================
    # TP direction
    # ========================================================

    if not validate_target(
        entry,
        tp,
        direction,
    ):

        result["score"] = 70

        result["reason"] = (
            "TP находится не в направлении сделки."
        )

        return result

    # ========================================================
    # Geometry
    # ========================================================

    if not validate_trade_geometry(
        entry,
        sl,
        tp,
        direction,
    ):

        result["score"] = 68

        result["reason"] = (
            "Некорректная геометрия сделки."
        )

        return result

    result["geometry_valid"] = True

    # ========================================================
    # RR
    # ========================================================

    rr_value = calculate_rr(
        entry,
        sl,
        tp,
        direction,
    )

    if rr_value is None:

        result["score"] = 68

        result["reason"] = (
            "Не удалось рассчитать RR."
        )

        return result

    result.update({
        "entry": round(
            entry,
            6,
        ),
        "sl": round(
            sl,
            6,
        ),
        "tp": round(
            tp,
            6,
        ),
        "rr": rr_value,
    })

    # ========================================================
    # Major strength
    # ========================================================

    try:

        major_strength = max(
            [
                _level_strength(level)
                for level in levels
            ]
            or [0]
        )

    except Exception:
        major_strength = 0

    # ========================================================
    # FVG
    # ========================================================

    (
        fvg_bonus,
        fvg_sweep,
        fvg_entry,
    ) = compute_fvg_bonus(
        sweep,
        entry,
        fvgs or [],
        direction,
    )

    result["fvg_bonus"] = (
        fvg_bonus
    )

    result["fvg_sweep"] = (
        fvg_sweep
    )

    result["fvg_entry"] = (
        fvg_entry
    )

    # ========================================================
    # RR < 1:2 → NO ENTRY
    # ========================================================

    if rr_value < MIN_RR:

        score = _score(
            direction=direction,
            context_direction=context_direction,
            sweep=sweep,
            confirmation_strength=0.8,
            ilm=ilm,
            rr=None,
            major_strength=major_strength,
            tp_source=tp_source,
            fvg_bonus=fvg_bonus,
        )

        result["score"] = min(
            score,
            79,
        )

        result["stage"] = (
            "15M_CONFIRMED"
        )

        result["reason"] = (
            f"RR 1:{rr_value:.2f} < 1:2. "
            "Вход запрещён. "
            "Ближайшая Major Liquidity слишком близко."
        )

        return result

    # ========================================================
    # Score
    # ========================================================

    score = _score(
        direction=direction,
        context_direction=context_direction,
        sweep=sweep,
        confirmation_strength=0.8,
        ilm=ilm,
        rr=rr_value,
        major_strength=major_strength,
        tp_source=tp_source,
        fvg_bonus=fvg_bonus,
    )

    result["score"] = score

    # ========================================================
    # READY conditions
    # ========================================================

    trend_ok = (
        trend_activity
        >= MIN_TREND_ACTIVITY_READY
    )

    recovery = (
        ilm or {}
    ).get(
        "recovery_ratio",
        0,
    )

    v_ok = (
        recovery
        >= MIN_V_RECOVERY_FOR_READY
    )

    rr_ok = (
        rr_value
        >= MIN_RR
    )

    mandatory_stages_ok = (
        sweep is not None
        and confirmation_ok
        and ilm_ok
        and tp_source == "major"
        and result["geometry_valid"]
    )

    ready_ok = (
        score >= MIN_SCORE_READY
        and trend_ok
        and v_ok
        and rr_ok
        and mandatory_stages_ok
    )

    # ========================================================
    # READY
    # ========================================================

    if ready_ok:

        result["stage"] = "READY"

        fvg_tag = ""

        if (
            fvg_sweep
            and fvg_entry
        ):
            fvg_tag = (
                " FVG sweep + entry."
            )

        elif fvg_sweep:
            fvg_tag = (
                " FVG sweep."
            )

        elif fvg_entry:
            fvg_tag = (
                " FVG entry."
            )

        result["reason"] = (
            "1H direction → Major sweep → "
            "15M confirmation → 5M ILM. "
            f"Trend {trend_activity:.2f}, "
            f"Recovery {recovery:.2f}, "
            f"RR 1:{rr_value:.2f}. "
            f"TP = Major 1H.{fvg_tag}"
        )

        return result

    # ========================================================
    # NOT READY
    # ========================================================

    result["stage"] = (
        "15M_CONFIRMED"
    )

    blocks = []

    if score < MIN_SCORE_READY:
        blocks.append(
            f"score {score}"
        )

    if not trend_ok:
        blocks.append(
            f"trend {trend_activity:.2f}"
        )

    if not v_ok:
        blocks.append(
            f"recovery {recovery:.2f}"
        )

    if not rr_ok:
        blocks.append(
            f"RR 1:{rr_value:.2f}"
        )

    if tp_source != "major":
        blocks.append(
            "TP не Major"
        )

    if blocks:

        result["reason"] = (
            "Сетап есть, READY заблокирован: "
            + ", ".join(blocks)
        )

    else:

        result["reason"] = (
            "Все этапы пройдены, "
            "но READY не подтверждён."
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
    fvgs=None,
):
    """
    Главная функция стратегии.

    D1/W1 полностью исключены из торгового решения.

    Всегда:

        1H
         ↓
        Major 1H
         ↓
        Sweep
         ↓
        15M
         ↓
        5M ILM
         ↓
        Entry
         ↓
        Major TP
    """

    price = _f(
        current_price
    )

    context_direction = (
        get_1h_direction(
            candles_1h
        )
    )

    base = {
        "stage": "WAIT",

        "direction": context_direction,

        "context_direction": (
            context_direction
        ),

        # Compatibility fields.
        # D1 не участвует.
        "d1_trend": "NEUTRAL",
        "d1_point_a": None,
        "d1_point_b": None,

        "score": 0,
        "reason": "",

        "entry": None,
        "sl": None,
        "tp": None,

        "tp_source": None,
        "rr": None,

        "sweep": None,
        "major_levels": (
            major_levels or []
        ),

        "confirmation_15m": False,
        "confirmation_15m_time": None,

        "confirmation": None,

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

    # ========================================================
    # DATA CHECK
    # ========================================================

    if (
        price is None
        or not candles_1h
        or not candles_15m
        or not candles_5m
    ):

        base["reason"] = (
            "Недостаточно рыночных данных."
        )

        return base

    # ========================================================
    # NEUTRAL 1H
    # ========================================================

    if context_direction == "NEUTRAL":

        base["stage"] = "WAIT"
        base["direction"] = "NEUTRAL"
        base["score"] = 0

        base["reason"] = (
            "1H NEUTRAL. "
            "Направление сделки не разрешено."
        )

        # Для dashboard всё равно оставляем
        # сценарии LONG/SHORT для диагностики.

        long_result = _analyze_scenario(
            candles_1h,
            candles_15m,
            candles_5m,
            price,
            major_levels,
            "LONG",
            context_direction,
            d1_context=None,
            fvgs=fvgs,
        )

        short_result = _analyze_scenario(
            candles_1h,
            candles_15m,
            candles_5m,
            price,
            major_levels,
            "SHORT",
            context_direction,
            d1_context=None,
            fvgs=fvgs,
        )

        base["long"] = (
            long_result
        )

        base["short"] = (
            short_result
        )

        return base

    # ========================================================
    # ONLY ALIGNED DIRECTION
    # ========================================================

    if context_direction == "LONG":

        long_result = _analyze_scenario(
            candles_1h,
            candles_15m,
            candles_5m,
            price,
            major_levels,
            "LONG",
            context_direction,
            d1_context=None,
            fvgs=fvgs,
        )

        # SHORT полностью запрещён.
        short_result = {
            "stage": "WAIT",
            "direction": "SHORT",
            "score": 0,
            "reason": (
                "SHORT запрещён: "
                "1H направление LONG."
            ),
            "entry": None,
            "sl": None,
            "tp": None,
            "tp_source": None,
            "rr": None,
            "sweep": None,
            "major_levels": (
                major_levels or []
            ),
            "confirmation_15m": False,
            "confirmation_15m_time": None,
            "confirmation": None,
            "ilm": None,
            "sweep_extreme": None,
            "tp_reason": None,
            "geometry_valid": False,
            "trend_activity": 0.0,
            "fvg_bonus": 0,
            "fvg_sweep": False,
            "fvg_entry": False,
        }

        chosen = long_result

    else:

        short_result = _analyze_scenario(
            candles_1h,
            candles_15m,
            candles_5m,
            price,
            major_levels,
            "SHORT",
            context_direction,
            d1_context=None,
            fvgs=fvgs,
        )

        # LONG полностью запрещён.
        long_result = {
            "stage": "WAIT",
            "direction": "LONG",
            "score": 0,
            "reason": (
                "LONG запрещён: "
                "1H направление SHORT."
            ),
            "entry": None,
            "sl": None,
            "tp": None,
            "tp_source": None,
            "rr": None,
            "sweep": None,
            "major_levels": (
                major_levels or []
            ),
            "confirmation_15m": False,
            "confirmation_15m_time": None,
            "confirmation": None,
            "ilm": None,
            "sweep_extreme": None,
            "tp_reason": None,
            "geometry_valid": False,
            "trend_activity": 0.0,
            "fvg_bonus": 0,
            "fvg_sweep": False,
            "fvg_entry": False,
        }

        chosen = short_result

    # ========================================================
    # SAVE BOTH
    # ========================================================

    base["long"] = (
        long_result
    )

    base["short"] = (
        short_result
    )

    # ========================================================
    # COPY CHOSEN RESULT
    # ========================================================

    base.update({
        "stage": chosen.get(
            "stage",
            "WAIT",
        ),

        "direction": chosen.get(
            "direction",
            context_direction,
        ),

        "score": chosen.get(
            "score",
            0,
        ),

        "reason": chosen.get(
            "reason",
            "",
        ),

        "entry": chosen.get(
            "entry"
        ),

        "sl": chosen.get(
            "sl"
        ),

        "tp": chosen.get(
            "tp"
        ),

        "tp_source": chosen.get(
            "tp_source"
        ),

        "rr": chosen.get(
            "rr"
        ),

        "sweep": chosen.get(
            "sweep"
        ),

        "confirmation_15m": chosen.get(
            "confirmation_15m",
            False,
        ),

        "confirmation_15m_time": chosen.get(
            "confirmation_15m_time"
        ),

        "confirmation": chosen.get(
            "confirmation"
        ),

        "ilm": chosen.get(
            "ilm"
        ),

        "sweep_extreme": chosen.get(
            "sweep_extreme"
        ),

        "tp_reason": chosen.get(
            "tp_reason"
        ),

        "geometry_valid": chosen.get(
            "geometry_valid",
            False,
        ),

        "trend_activity": chosen.get(
            "trend_activity",
            0.0,
        ),

        "fvg_bonus": chosen.get(
            "fvg_bonus",
            0,
        ),

        "fvg_sweep": chosen.get(
            "fvg_sweep",
            False,
        ),

        "fvg_entry": chosen.get(
            "fvg_entry",
            False,
        ),
    })

    # Compatibility.
    base["context_direction"] = (
        context_direction
    )

    # D1 explicitly neutral.
    base["d1_trend"] = "NEUTRAL"
    base["d1_point_a"] = None
    base["d1_point_b"] = None

    return base


# ============================================================
# SOL COMPATIBILITY
# ============================================================

def analyze_sol(
    *args,
    **kwargs,
):
    return analyze(
        *args,
        **kwargs,
    )


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "STRATEGY_VERSION",

    "MIN_SCORE_READY",
    "MIN_RR",
    "SL_BUFFER_PCT",

    "MIN_TREND_ACTIVITY_READY",
    "MIN_V_RECOVERY_FOR_READY",

    "FVG_TOLERANCE_PCT",
    "FVG_SWEEP_BONUS",
    "FVG_ENTRY_BONUS",
    "FVG_MAX_BONUS",

    "get_1h_direction",
    "get_higher_timeframe_direction",

    "measure_trend_activity",

    "is_inside_fvg",
    "compute_fvg_bonus",

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