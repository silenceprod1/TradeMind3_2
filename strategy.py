"""
TradeMind 6.5
1H Context -> Major Liquidity -> Sweep -> 15M Confirmation -> 5M ILM -> Entry

ВАЖНО:

1H используется как главный контекст.

Но LONG и SHORT сценарии анализируются НЕЗАВИСИМО.

1H LONG:
    LONG имеет приоритет,
    но SHORT не запрещён.

1H SHORT:
    SHORT имеет приоритет,
    но LONG не запрещён.

1H NEUTRAL:
    полноценный вход запрещён.

LONG:
    Major SSL
    -> SSL Sweep
    -> 15M confirmation
    -> 5M V-ILM
    -> Entry
    -> SL
    -> TP
    -> RR >= 1:2

SHORT:
    Major BSL
    -> BSL Sweep
    -> 15M confirmation
    -> 5M L-ILM
    -> Entry
    -> SL
    -> TP
    -> RR >= 1:2

D1/W1:
    полностью НЕ используются.

TP:
    следующая свежая Major Liquidity.

Никакого искусственного TP ради RR.

Геометрия:

LONG:
    SL < Entry < TP

SHORT:
    TP < Entry < SL
"""

from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# VERSION
# ============================================================

STRATEGY_VERSION = "6.5"


# ============================================================
# SETTINGS
# ============================================================

MIN_SCORE_READY = 80

MIN_RR = 2.0

SL_BUFFER_PCT = 0.20

MIN_SWEEP_DEPTH_PCT = 0.08

MIN_5M_RECOVERY_RATIO = 0.33

MIN_BODY_RATIO = 0.35

MAX_SWEEP_AGE_1H = 8

MAX_5M_ILM_CANDLES = 30

MAX_15M_CONFIRM_CANDLES = 12

MIN_5M_ILM_SWEEP_DISTANCE_PCT = 0.75

MIN_TARGET_DISTANCE_PCT = 0.05

# Если противоположный сценарий относительно 1H
# полностью подтверждён, допускаем его.
#
# Но для его READY нужен более высокий score.
COUNTER_TREND_MIN_SCORE = 90


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

    if converted is None:
        return default

    return converted


def _o(candle):
    return _v(candle, "open")


def _h(candle):
    return _v(candle, "high")


def _l(candle):
    return _v(candle, "low")


def _c(candle):
    return _v(candle, "close")


def _t(candle):
    return _v(candle, "open_time")


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

    candle_range = _range(candle)

    if candle_range <= 0:
        return 0.0

    return _body(candle) / candle_range


def _bull(candle):

    opening = _o(candle)
    closing = _c(candle)

    return (
        opening is not None
        and closing is not None
        and closing > opening
    )


def _bear(candle):

    opening = _o(candle)
    closing = _c(candle)

    return (
        opening is not None
        and closing is not None
        and closing < opening
    )


def _distance_pct(a, b):

    a = _f(a)
    b = _f(b)

    if a is None or b is None or b == 0:
        return None

    return abs(a - b) / abs(b) * 100


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

    if any(
        x is None
        for x in (
            current,
            left_1,
            left_2,
            right_1,
            right_2,
        )
    ):
        return False

    return (
        current > left_1
        and current >= left_2
        and current >= right_1
        and current > right_2
    )


def _swing_low(candles, index):

    if index < 2 or index >= len(candles) - 2:
        return False

    current = _l(candles[index])

    left_1 = _l(candles[index - 1])
    left_2 = _l(candles[index - 2])

    right_1 = _l(candles[index + 1])
    right_2 = _l(candles[index + 2])

    if any(
        x is None
        for x in (
            current,
            left_1,
            left_2,
            right_1,
            right_2,
        )
    ):
        return False

    return (
        current < left_1
        and current <= left_2
        and current <= right_1
        and current < right_2
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

        previous_high = highs[-2][1]
        latest_high = highs[-1][1]

        previous_low = lows[-2][1]
        latest_low = lows[-1][1]

        bullish_structure = (
            latest_high > previous_high
            and latest_low > previous_low
        )

        bearish_structure = (
            latest_high < previous_high
            and latest_low < previous_low
        )

    recent = candles[-8:]

    bullish_body = sum(
        _body(candle)
        for candle in recent
        if _bull(candle)
    )

    bearish_body = sum(
        _body(candle)
        for candle in recent
        if _bear(candle)
    )

    if (
        bullish_body > 0
        and bullish_body > bearish_body * 1.15
    ):
        bullish_structure = True

    if (
        bearish_body > 0
        and bearish_body > bullish_body * 1.15
    ):
        bearish_structure = True

    if bullish_structure and not bearish_structure:
        return "LONG"

    if bearish_structure and not bullish_structure:
        return "SHORT"

    return "NEUTRAL"


def get_higher_timeframe_direction(
    candles_1h,
    candles_d1=None,
    candles_w1=None,
):
    """
    Backward compatibility.

    D1/W1 intentionally ignored.
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
        return 0

    value = (
        level.get("strength")
        or level.get("touches")
        or 0
    )

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

def _levels_for_direction(
    major_levels,
    direction,
):
    """
    LONG  -> только SSL
    SHORT -> только BSL

    Важно:
    функция НЕ смотрит на 1H direction.

    Поэтому SHORT может анализироваться
    даже когда 1H = LONG.
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

        if side == direction:
            result.append(level)
            continue

        if expected_type in level_type:
            result.append(level)

    return result


# ============================================================
# SWEEP
# ============================================================

def find_sweep(
    candles_1h,
    major_levels,
    direction,
):
    """
    Ищем sweep независимо от 1H direction.

    LONG:
        SSL sweep.

    SHORT:
        BSL sweep.

    Берём самый свежий валидный sweep.
    """

    if direction not in {"LONG", "SHORT"}:
        return None

    if not candles_1h or len(candles_1h) < 3:
        return None

    levels = _levels_for_direction(
        major_levels,
        direction,
    )

    if not levels:
        return None

    recent = candles_1h[-MAX_SWEEP_AGE_1H:]

    # Новейшие свечи первыми.
    for candle in reversed(recent):

        for level in levels:

            if _is_swept_level(level):
                continue

            price = _level_price(level)

            if price is None:
                continue

            # ------------------------------------------------
            # LONG / SSL
            # ------------------------------------------------

            if direction == "LONG":

                low = _l(candle)
                close = _c(candle)

                if low is None or close is None:
                    continue

                depth = (
                    (price - low)
                    / price
                    * 100
                )

                if (
                    low < price
                    and depth >= MIN_SWEEP_DEPTH_PCT
                    and close > price
                    and _bull(candle)
                ):

                    return {
                        "swept": True,
                        "direction": "LONG",
                        "level": price,
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
                    }

            # ------------------------------------------------
            # SHORT / BSL
            # ------------------------------------------------

            else:

                high = _h(candle)
                close = _c(candle)

                if high is None or close is None:
                    continue

                depth = (
                    (high - price)
                    / price
                    * 100
                )

                if (
                    high > price
                    and depth >= MIN_SWEEP_DEPTH_PCT
                    and close < price
                    and _bear(candle)
                ):

                    return {
                        "swept": True,
                        "direction": "SHORT",
                        "level": price,
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
                    }

    return None


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

def confirmation_15m(
    candles_15m,
    sweep,
    direction,
):
    """
    15M confirmation теперь смотрит
    локальную структуру, а не просто
    предыдущую свечу.

    LONG:
        bullish body
        +
        close above local 15M high.

    SHORT:
        bearish body
        +
        close below local 15M low.
    """

    if not sweep:
        return False, None, None

    if direction not in {"LONG", "SHORT"}:
        return False, None, None

    if not candles_15m:
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

    # Проверяем последние локальные
    # структуры.
    for i in range(
        1,
        len(candidates) - 1,
    ):

        candle = candidates[i]

        if _body_ratio(candle) < MIN_BODY_RATIO:
            continue

        close = _c(candle)

        if close is None:
            continue

        # ---------------------------------------------
        # LONG
        # ---------------------------------------------

        if direction == "LONG":

            local_highs = []

            for j in range(i):
                if _local_high_15m(
                    candidates,
                    j,
                ):
                    high = _h(candidates[j])
                    if high is not None:
                        local_highs.append(high)

            if not local_highs:
                continue

            reference = max(local_highs[-3:])

            if (
                _bull(candle)
                and close > reference
            ):
                return (
                    True,
                    "15M bullish structure break",
                    _t(candle),
                )

        # ---------------------------------------------
        # SHORT
        # ---------------------------------------------

        else:

            local_lows = []

            for j in range(i):
                if _local_low_15m(
                    candidates,
                    j,
                ):
                    low = _l(candidates[j])
                    if low is not None:
                        local_lows.append(low)

            if not local_lows:
                continue

            reference = min(local_lows[-3:])

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

    return (
        current >= left
        and current > right
    )


def _is_local_low(candles, index):

    if index < 1 or index >= len(candles) - 1:
        return False

    current = _l(candles[index])
    left = _l(candles[index - 1])
    right = _l(candles[index + 1])

    if current is None or left is None or right is None:
        return False

    return (
        current <= left
        and current < right
    )


# ============================================================
# 5M ILM
# ============================================================

def detect_5m_ilm(
    candles_5m,
    sweep,
    direction,
    confirmation_time=None,
):
    """
    LONG:
        V-ILM

    SHORT:
        L-ILM
    """

    if not sweep:
        return False, None

    if direction not in {"LONG", "SHORT"}:
        return False, None

    start_time = _f(
        confirmation_time
    )

    if start_time is None:
        start_time = _f(
            sweep.get("open_time")
        )

    candles = []

    for candle in candles_5m or []:

        candle_time = _t(candle)

        if start_time is None:
            candles.append(candle)

        elif (
            candle_time is not None
            and candle_time > start_time
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

    # ========================================================
    # LONG V
    # ========================================================

    if direction == "LONG":

        for i in range(
            2,
            len(candles) - 2,
        ):

            manipulation = candles[i]

            if not _is_local_low(candles, i):
                continue

            manipulation_low = _l(manipulation)
            manipulation_high = _h(manipulation)

            if (
                manipulation_low is None
                or manipulation_high is None
            ):
                continue

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
                continue

            before_lows = [
                _l(x)
                for x in before
                if _l(x) is not None
            ]

            after_closes = [
                _c(x)
                for x in after
                if _c(x) is not None
            ]

            if not before_lows or not after_closes:
                continue

            left_reference = min(before_lows)
            right_close = max(after_closes)

            if left_reference <= manipulation_low:
                continue

            manipulation_range = (
                left_reference
                - manipulation_low
            )

            if manipulation_range <= 0:
                continue

            manipulation_pct = (
                manipulation_range
                / left_reference
                * 100
            )

            if manipulation_pct < MIN_SWEEP_DEPTH_PCT:
                continue

            recovery_ratio = (
                right_close - manipulation_low
            ) / manipulation_range

            if recovery_ratio < MIN_5M_RECOVERY_RATIO:
                continue

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
                    and trigger_close > manipulation_high
                ):
                    trigger_index = j
                    break

            if trigger_index is None:
                continue

            trigger = candles[trigger_index]
            trigger_close = _c(trigger)

            if sweep_level is not None:

                distance = (
                    abs(
                        manipulation_low
                        - sweep_level
                    )
                    / sweep_level
                    * 100
                )

                if distance > MIN_5M_ILM_SWEEP_DISTANCE_PCT:
                    continue

            if (
                sweep_extreme is not None
                and manipulation_low > sweep_extreme
                * (
                    1
                    + MIN_5M_ILM_SWEEP_DISTANCE_PCT
                    / 100
                )
            ):
                continue

            return (
                True,
                {
                    "direction": "LONG",
                    "extreme": manipulation_low,
                    "trigger_time": _t(trigger),
                    "trigger_price": trigger_close,
                    "reason": (
                        "5M V-ILM: downside manipulation "
                        "+ recovery + bullish displacement"
                    ),
                },
            )

    # ========================================================
    # SHORT L
    # ========================================================

    else:

        for i in range(
            2,
            len(candles) - 2,
        ):

            manipulation = candles[i]

            if not _is_local_high(candles, i):
                continue

            manipulation_high = _h(manipulation)
            manipulation_low = _l(manipulation)

            if (
                manipulation_high is None
                or manipulation_low is None
            ):
                continue

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
                continue

            before_highs = [
                _h(x)
                for x in before
                if _h(x) is not None
            ]

            after_closes = [
                _c(x)
                for x in after
                if _c(x) is not None
            ]

            if not before_highs or not after_closes:
                continue

            left_reference = max(before_highs)
            right_close = min(after_closes)

            if manipulation_high <= left_reference:
                continue

            manipulation_range = (
                manipulation_high
                - left_reference
            )

            if manipulation_range <= 0:
                continue

            manipulation_pct = (
                manipulation_range
                / left_reference
                * 100
            )

            if manipulation_pct < MIN_SWEEP_DEPTH_PCT:
                continue

            recovery_ratio = (
                manipulation_high
                - right_close
            ) / manipulation_range

            if recovery_ratio < MIN_5M_RECOVERY_RATIO:
                continue

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
                    and trigger_close < manipulation_low
                ):
                    trigger_index = j
                    break

            if trigger_index is None:
                continue

            trigger = candles[trigger_index]
            trigger_close = _c(trigger)

            if sweep_level is not None:

                distance = (
                    abs(
                        manipulation_high
                        - sweep_level
                    )
                    / sweep_level
                    * 100
                )

                if distance > MIN_5M_ILM_SWEEP_DISTANCE_PCT:
                    continue

            if (
                sweep_extreme is not None
                and manipulation_high < sweep_extreme
                * (
                    1
                    - MIN_5M_ILM_SWEEP_DISTANCE_PCT
                    / 100
                )
            ):
                continue

            return (
                True,
                {
                    "direction": "SHORT",
                    "extreme": manipulation_high,
                    "trigger_time": _t(trigger),
                    "trigger_price": trigger_close,
                    "reason": (
                        "5M L-ILM: upside manipulation "
                        "+ recovery + bearish displacement"
                    ),
                },
            )

    return False, None


# ============================================================
# TARGET
# ============================================================

def next_target(
    major_levels,
    direction,
    current_price,
    exclude_level=None,
):
    """
    Следующая свежая Major Liquidity.
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

    return min(
        candidates,
        key=lambda x: abs(x - price),
    )


# ============================================================
# ENTRY
# ============================================================

def calculate_entry(
    current_price,
    direction,
):

    price = _f(current_price)

    if price is None:
        return None

    if direction not in {"LONG", "SHORT"}:
        return None

    return price


# ============================================================
# STOP
# ============================================================

def calculate_stop(
    entry,
    extreme,
    direction,
):

    entry = _f(entry)
    extreme = _f(extreme)

    if entry is None or extreme is None:
        return None

    if direction == "LONG":

        sl = extreme * (
            1 - SL_BUFFER_PCT / 100
        )

        if sl >= entry:
            return None

        return sl

    if direction == "SHORT":

        sl = extreme * (
            1 + SL_BUFFER_PCT / 100
        )

        if sl <= entry:
            return None

        return sl

    return None


# ============================================================
# TP
# ============================================================

def calculate_take_profit(
    major_levels,
    direction,
    entry,
    sweep_level=None,
):

    return next_target(
        major_levels=major_levels,
        direction=direction,
        current_price=entry,
        exclude_level=sweep_level,
    )


# ============================================================
# RR
# ============================================================

def calculate_rr(
    entry,
    sl,
    tp,
    direction=None,
):

    entry = _f(entry)
    sl = _f(sl)
    tp = _f(tp)

    if (
        entry is None
        or sl is None
        or tp is None
    ):
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


# ============================================================
# GEOMETRY
# ============================================================

def validate_trade_geometry(
    entry,
    sl,
    tp,
    direction,
):

    entry = _f(entry)
    sl = _f(sl)
    tp = _f(tp)

    if (
        entry is None
        or sl is None
        or tp is None
    ):
        return False

    if direction == "LONG":
        return sl < entry < tp

    if direction == "SHORT":
        return tp < entry < sl

    return False


def validate_target(
    entry,
    tp,
    direction,
):

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
    sweep,
    confirmation,
    ilm,
    rr,
    major_strength=0,
):
    """
    Максимум 100.

    Direction/context 20
    Sweep              20
    15M                20
    5M ILM             20
    RR                 15
    Liquidity           5
    """

    score = 0

    if direction != "NEUTRAL":
        score += 20

    if sweep:
        score += 20

    if confirmation:
        score += 20

    if ilm:
        score += 20

    if rr is not None and rr >= MIN_RR:
        score += 15

    if major_strength >= 3:
        score += 5

    return min(100, score)


# ============================================================
# SINGLE SCENARIO ANALYSIS
# ============================================================

def _analyze_scenario(
    candles_1h,
    candles_15m,
    candles_5m,
    current_price,
    major_levels,
    direction,
):
    """
    Анализ одного направления.

    Эта функция НЕ знает,
    какой сейчас общий 1H context.

    Поэтому LONG и SHORT могут
    анализироваться одновременно.
    """

    result = {
        "stage": "WAIT",
        "direction": direction,
        "score": 0,
        "reason": "",
        "entry": None,
        "sl": None,
        "tp": None,
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

    levels = _levels_for_direction(
        major_levels,
        direction,
    )

    if not levels:

        result["score"] = 30

        result["reason"] = (
            f"Нет актуальной Major "
            f"{'SSL' if direction == 'LONG' else 'BSL'}."
        )

        return result

    # --------------------------------------------------------
    # SWEEP
    # --------------------------------------------------------

    sweep = find_sweep(
        candles_1h,
        levels,
        direction,
    )

    result["sweep"] = sweep

    if sweep is None:

        result["score"] = 35

        result["reason"] = (
            f"Ждём "
            f"{'SSL sweep' if direction == 'LONG' else 'BSL sweep'}."
        )

        return result

    result["stage"] = "SWEPT"

    result["sweep_extreme"] = sweep.get(
        "extreme"
    )

    # --------------------------------------------------------
    # 15M
    # --------------------------------------------------------

    (
        confirmation_ok,
        confirmation_text,
        confirmation_time,
    ) = confirmation_15m(
        candles_15m,
        sweep,
        direction,
    )

    result["confirmation_15m"] = confirmation_ok
    result["confirmation_15m_time"] = confirmation_time
    result["confirmation"] = confirmation_text

    if not confirmation_ok:

        result["score"] = 60

        result["reason"] = (
            "Sweep есть. Ждём 15M confirmation."
        )

        return result

    result["stage"] = "15M_CONFIRMED"

    # --------------------------------------------------------
    # 5M ILM
    # --------------------------------------------------------

    ilm_ok, ilm = detect_5m_ilm(
        candles_5m,
        sweep,
        direction,
        confirmation_time,
    )

    result["ilm"] = ilm

    if not ilm_ok:

        result["score"] = 70

        result["reason"] = (
            "15M подтверждение есть. "
            "Ждём 5M ILM."
        )

        return result

    # --------------------------------------------------------
    # ENTRY
    # --------------------------------------------------------

    entry = calculate_entry(
        price,
        direction,
    )

    if entry is None:
        result["score"] = 72
        result["reason"] = "Не удалось определить Entry."
        return result

    # --------------------------------------------------------
    # EXTREME
    # --------------------------------------------------------

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

        result["score"] = 72

        result["reason"] = (
            "Не удалось определить экстремум для SL."
        )

        return result

    if direction == "LONG":

        extreme = min(candidates)

        if extreme >= entry:

            result["score"] = 72

            result["reason"] = (
                "LONG invalid: extreme >= Entry."
            )

            return result

    else:

        extreme = max(candidates)

        if extreme <= entry:

            result["score"] = 72

            result["reason"] = (
                "SHORT invalid: extreme <= Entry."
            )

            return result

    # --------------------------------------------------------
    # SL
    # --------------------------------------------------------

    sl = calculate_stop(
        entry,
        extreme,
        direction,
    )

    if sl is None:

        result["score"] = 72

        result["reason"] = (
            "Не удалось построить корректный SL."
        )

        return result

    # --------------------------------------------------------
    # TP
    # --------------------------------------------------------

    tp = calculate_take_profit(
        major_levels,
        direction,
        entry,
        sweep.get("level"),
    )

    if tp is None:

        result["score"] = 75

        result["reason"] = (
            "Следующая свежая Major Liquidity "
            "не найдена."
        )

        return result

    # --------------------------------------------------------
    # TARGET
    # --------------------------------------------------------

    if not validate_target(
        entry,
        tp,
        direction,
    ):

        result["score"] = 75

        result["reason"] = (
            "TP находится не в направлении сделки."
        )

        return result

    # --------------------------------------------------------
    # GEOMETRY
    # --------------------------------------------------------

    if not validate_trade_geometry(
        entry,
        sl,
        tp,
        direction,
    ):

        result["score"] = 72

        result["reason"] = (
            "Некорректная геометрия сделки."
        )

        return result

    result["geometry_valid"] = True

    # --------------------------------------------------------
    # RR
    # --------------------------------------------------------

    rr_value = calculate_rr(
        entry,
        sl,
        tp,
        direction,
    )

    if rr_value is None:

        result["score"] = 72

        result["reason"] = (
            "Не удалось рассчитать RR."
        )

        return result

    result.update({
        "entry": round(entry, 6),
        "sl": round(sl, 6),
        "tp": round(tp, 6),
        "rr": rr_value,
    })

    # --------------------------------------------------------
    # MAJOR STRENGTH
    # --------------------------------------------------------

    major_strength = 0

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

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = _score(
        direction=direction,
        context_direction=direction,
        sweep=sweep,
        confirmation=True,
        ilm=True,
        rr=rr_value,
        major_strength=major_strength,
    )

    # --------------------------------------------------------
    # RR BLOCK
    # --------------------------------------------------------

    if rr_value < MIN_RR:

        result["score"] = min(
            score,
            79,
        )

        result["stage"] = "15M_CONFIRMED"

        result["reason"] = (
            f"RR 1:{rr_value:.2f} < 1:2. "
            "Вход запрещён."
        )

        result["tp_reason"] = (
            "TP = ближайшая свежая "
            "Major Liquidity."
        )

        return result

    # --------------------------------------------------------
    # READY
    # --------------------------------------------------------

    result["score"] = score

    if score >= MIN_SCORE_READY:

        result["stage"] = "READY"

        result["reason"] = (
            "Sweep → 15M confirmation → "
            "5M ILM → корректная геометрия."
        )

        result["tp_reason"] = (
            "TP = следующая свежая Major Liquidity."
        )

    else:

        result["stage"] = "WAIT"

        result["reason"] = (
            "Сетап сформирован, но score недостаточен."
        )

    return result


# ============================================================
# MAIN ANALYSIS
# ============================================================

def analyze(
    candles_1h: List[Any],
    candles_15m: List[Any],
    candles_5m: List[Any],
    current_price: float,
    major_levels=None,
    sweep=None,
    order_flow=None,
    candles_1m=None,
):
    """
    ГЛАВНАЯ ФУНКЦИЯ.

    Теперь анализируются ОБА сценария:

        LONG
        SHORT

    независимо от текущего 1H context.

    1H context используется для:

        1. отображения основного направления;
        2. приоритета сценария;
        3. защиты от полностью хаотичного рынка.

    Но LONG не выключает SHORT.
    """

    price = _f(current_price)

    context_direction = get_1h_direction(
        candles_1h
    )

    base = {
        "stage": "WAIT",
        "direction": context_direction,
        "context_direction": context_direction,
        "score": 0,
        "reason": "",
        "entry": None,
        "sl": None,
        "tp": None,
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
        "long": None,
        "short": None,
    }

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
    # ANALYSE BOTH DIRECTIONS
    # ========================================================

    long_result = _analyze_scenario(
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,
        current_price=price,
        major_levels=major_levels,
        direction="LONG",
    )

    short_result = _analyze_scenario(
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,
        current_price=price,
        major_levels=major_levels,
        direction="SHORT",
    )

    base["long"] = long_result
    base["short"] = short_result

    # ========================================================
    # NEUTRAL CONTEXT
    # ========================================================

    if context_direction == "NEUTRAL":

        base["score"] = max(
            long_result["score"],
            short_result["score"],
        )

        base["reason"] = (
            "1H NEUTRAL. "
            "Ищем структуру, но READY "
            "не разрешаем без ясного 1H контекста."
        )

        return base

    # ========================================================
    # READY CANDIDATES
    # ========================================================

    ready = []

    if (
        long_result.get("stage") == "READY"
        and long_result.get("score", 0)
        >= MIN_SCORE_READY
    ):
        ready.append(long_result)

    if (
        short_result.get("stage") == "READY"
        and short_result.get("score", 0)
        >= MIN_SCORE_READY
    ):
        ready.append(short_result)

    # ========================================================
    # CONTEXT PRIORITY
    # ========================================================

    if ready:

        # Сначала основной 1H сценарий.
        aligned = [
            x
            for x in ready
            if x["direction"] == context_direction
        ]

        counter = [
            x
            for x in ready
            if x["direction"] != context_direction
        ]

        # Основной сценарий имеет приоритет.
        if aligned:

            chosen = max(
                aligned,
                key=lambda x: x["score"],
            )

        elif counter:

            # Контртренд разрешаем только
            # при очень сильном полном сетапе.
            counter_ready = [
                x
                for x in counter
                if x["score"]
                >= COUNTER_TREND_MIN_SCORE
            ]

            if not counter_ready:

                base["score"] = max(
                    long_result["score"],
                    short_result["score"],
                )

                base["reason"] = (
                    "Есть контртрендовый сценарий, "
                    "но он недостаточно сильный "
                    "для READY."
                )

                return base

            chosen = max(
                counter_ready,
                key=lambda x: x["score"],
            )

        else:

            chosen = None

        if chosen:

            base.update(chosen)

            base["context_direction"] = (
                context_direction
            )

            base["long"] = long_result
            base["short"] = short_result

            return base

    # ========================================================
    # NO READY
    # ========================================================

    # Выбираем наиболее продвинутый сценарий
    # для основного UI.

    candidates = [
        long_result,
        short_result,
    ]

    def stage_weight(result):

        weights = {
            "READY": 5,
            "15M_CONFIRMED": 4,
            "SWEPT": 3,
            "WAIT": 1,
        }

        return weights.get(
            result.get("stage"),
            0,
        )

    # При равной стадии приоритет
    # основного 1H направления.
    aligned_candidates = [
        x
        for x in candidates
        if x["direction"] == context_direction
    ]

    pool = (
        aligned_candidates
        if aligned_candidates
        else candidates
    )

    chosen = max(
        pool,
        key=lambda x: (
            stage_weight(x),
            x.get("score", 0),
        ),
    )

    base.update({
        "stage": chosen.get("stage", "WAIT"),
        "direction": chosen.get(
            "direction",
            context_direction,
        ),
        "score": chosen.get("score", 0),
        "reason": chosen.get("reason", ""),
        "entry": chosen.get("entry"),
        "sl": chosen.get("sl"),
        "tp": chosen.get("tp"),
        "rr": chosen.get("rr"),
        "sweep": chosen.get("sweep"),
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
        "ilm": chosen.get("ilm"),
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
    })

    base["context_direction"] = (
        context_direction
    )

    # Если основной 1H сценарий ждёт SSL,
    # но BSL уже рядом/готов к sweep,
    # показываем это отдельно через short.
    if (
        context_direction == "LONG"
        and short_result["stage"] in {
            "SWEPT",
            "15M_CONFIRMED",
            "READY",
        }
    ):

        base["reason"] = (
            f"{base['reason']} "
            f"SHORT-сценарий также активен: "
            f"{short_result['stage']}."
        )

    elif (
        context_direction == "SHORT"
        and long_result["stage"] in {
            "SWEPT",
            "15M_CONFIRMED",
            "READY",
        }
    ):

        base["reason"] = (
            f"{base['reason']} "
            f"LONG-сценарий также активен: "
            f"{long_result['stage']}."
        )

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

    "get_1h_direction",
    "get_higher_timeframe_direction",

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

    "analyze",
    "analyze_sol",
]