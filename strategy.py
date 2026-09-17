"""
TradeMind 6.3.1
1H -> Major Liquidity -> Sweep -> 15M Confirmation -> 5M ILM -> Entry

Основная логика:

1H:
    главный timeframe направления и структуры.

LONG:
    1H LONG
    -> ждём снятие SSL
    -> 15M confirmation
    -> 5M V-ILM
    -> LONG

SHORT:
    1H SHORT
    -> ждём снятие BSL
    -> 15M confirmation
    -> 5M L-ILM
    -> SHORT

TP:
    следующая свежая major liquidity
    в направлении сделки.

RR:
    минимум 1:2.

SL:
    за экстремумом sweep / ILM
    с буфером SL_BUFFER_PCT.

Критическая защита:
    LONG  => SL < Entry < TP
    SHORT => TP < Entry < SL

Если геометрия сделки неправильная,
READY невозможен независимо от score.
"""


from typing import Any, Dict, List, Optional


# ============================================================
# VERSION
# ============================================================

STRATEGY_VERSION = "6.3.1"


# ============================================================
# GLOBAL SETTINGS
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


# ============================================================
# BASIC HELPERS
# ============================================================

def _f(x):
    """
    Безопасное преобразование в float.
    """

    try:
        return float(x)

    except (TypeError, ValueError):

        return None


def _v(
    candle,
    key,
    default=None,
):
    """
    Получение OHLCV значения.

    Поддерживаются оба варианта:

    open / high / low / close / open_time

    и короткие:

    o / h / l / c / time
    """

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
    """
    Размер тела свечи.
    """

    opening = _o(candle)

    closing = _c(candle)

    if opening is None or closing is None:

        return 0.0

    return abs(
        closing - opening
    )


def _range(candle):
    """
    Полный диапазон свечи.
    """

    high = _h(candle)

    low = _l(candle)

    if high is None or low is None:

        return 0.0

    return max(
        0.0,
        high - low,
    )


def _body_ratio(candle):
    """
    Доля тела свечи от полного диапазона.

    Например:

    body = 0.6
    range = 1.0

    ratio = 0.60
    """

    candle_range = _range(candle)

    if candle_range <= 0:

        return 0.0

    return (
        _body(candle)
        / candle_range
    )


def _bull(candle):
    """
    Бычья свеча.
    """

    opening = _o(candle)

    closing = _c(candle)

    return (
        opening is not None
        and closing is not None
        and closing > opening
    )


def _bear(candle):
    """
    Медвежья свеча.
    """

    opening = _o(candle)

    closing = _c(candle)

    return (
        opening is not None
        and closing is not None
        and closing < opening
    )


def _distance_pct(
    a,
    b,
):
    """
    Процентное расстояние между двумя ценами.
    """

    a = _f(a)

    b = _f(b)

    if a is None or b is None or b == 0:

        return None

    return (
        abs(a - b)
        / abs(b)
        * 100
    )


# ============================================================
# 1H SWINGS
# ============================================================

def _swing_high(
    candles,
    index,
):
    """
    Локальный swing high.

    Используем 2 свечи слева
    и 2 свечи справа.
    """

    if (
        index < 2
        or index >= len(candles) - 2
    ):

        return False

    current = _h(
        candles[index]
    )

    left_1 = _h(
        candles[index - 1]
    )

    left_2 = _h(
        candles[index - 2]
    )

    right_1 = _h(
        candles[index + 1]
    )

    right_2 = _h(
        candles[index + 2]
    )

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


def _swing_low(
    candles,
    index,
):
    """
    Локальный swing low.
    """

    if (
        index < 2
        or index >= len(candles) - 2
    ):

        return False

    current = _l(
        candles[index]
    )

    left_1 = _l(
        candles[index - 1]
    )

    left_2 = _l(
        candles[index - 2]
    )

    right_1 = _l(
        candles[index + 1]
    )

    right_2 = _l(
        candles[index + 2]
    )

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


def _swing_highs(
    candles,
):
    """
    Возвращает список:

    [
        (index, price),
        ...
    ]
    """

    result = []

    if not candles:

        return result

    for i in range(
        len(candles)
    ):

        if _swing_high(
            candles,
            i,
        ):

            price = _h(
                candles[i]
            )

            if price is not None:

                result.append(
                    (
                        i,
                        price,
                    )
                )

    return result


def _swing_lows(
    candles,
):
    """
    Возвращает swing lows.
    """

    result = []

    if not candles:

        return result

    for i in range(
        len(candles)
    ):

        if _swing_low(
            candles,
            i,
        ):

            price = _l(
                candles[i]
            )

            if price is not None:

                result.append(
                    (
                        i,
                        price,
                    )
                )

    return result


# ============================================================
# 1H DIRECTION
# ============================================================

def get_1h_direction(
    candles,
):
    """
    1H — единственный главный timeframe направления.

    LONG:
        HH + HL
        или заметное бычье body dominance.

    SHORT:
        LH + LL
        или заметное медвежье body dominance.

    NEUTRAL:
        структура неясная.

    D1/W1 здесь полностью отсутствуют.
    """

    if (
        not candles
        or len(candles) < 15
    ):

        return "NEUTRAL"

    candles = candles[-60:]

    highs = _swing_highs(
        candles
    )

    lows = _swing_lows(
        candles
    )

    bullish_structure = False

    bearish_structure = False

    if (
        len(highs) >= 2
        and len(lows) >= 2
    ):

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

    # --------------------------------------------------------
    # Recent body dominance
    # --------------------------------------------------------

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
        and bullish_body
        > bearish_body * 1.15
    ):

        bullish_structure = True

    if (
        bearish_body > 0
        and bearish_body
        > bullish_body * 1.15
    ):

        bearish_structure = True

    # --------------------------------------------------------
    # Conflict = neutral
    # --------------------------------------------------------

    if (
        bullish_structure
        and not bearish_structure
    ):

        return "LONG"

    if (
        bearish_structure
        and not bullish_structure
    ):

        return "SHORT"

    return "NEUTRAL"


def get_higher_timeframe_direction(
    candles_1h,
    candles_d1=None,
    candles_w1=None,
):
    """
    Backward-compatible function.

    D1/W1 намеренно игнорируются.

    Основное направление определяется
    только 1H.
    """

    return get_1h_direction(
        candles_1h
    )


# ============================================================
# LIQUIDITY HELPERS
# ============================================================

def _level_price(
    level,
):
    """
    Получение цены уровня.
    """

    if isinstance(
        level,
        dict,
    ):

        return _f(
            level.get("price")
        )

    return _f(level)


def _level_side(
    level,
):
    """
    Получение направления уровня.

    LONG  = SSL снизу
    SHORT = BSL сверху
    """

    if not isinstance(
        level,
        dict,
    ):

        return None

    return str(
        level.get("side")
        or level.get("direction")
        or ""
    ).upper()


def _level_type(
    level,
):
    if not isinstance(
        level,
        dict,
    ):

        return ""

    return str(
        level.get("type")
        or ""
    ).upper()


def _level_strength(
    level,
):
    if not isinstance(
        level,
        dict,
    ):

        return 0

    value = (
        level.get("strength")
        or level.get("touches")
        or 0
    )

    try:
        return float(value)

    except (
        TypeError,
        ValueError,
    ):

        return 0


# ============================================================
# SWEEP
# ============================================================

def find_sweep(
    candles_1h,
    major_levels,
    direction,
):
    """
    Ищем sweep только в направлении
    текущего 1H сценария.

    LONG:
        SSL снизу
        -> цена прокалывает SSL
        -> закрывается обратно выше.

    SHORT:
        BSL сверху
        -> цена прокалывает BSL
        -> закрывается обратно ниже.
    """

    if direction not in {
        "LONG",
        "SHORT",
    }:

        return None

    if (
        not candles_1h
        or len(candles_1h) < 3
    ):

        return None

    recent = candles_1h[
        -MAX_SWEEP_AGE_1H:
    ]

    # Идём от самой новой свечи
    # к старым.
    for candle in reversed(
        recent
    ):

        for level in (
            major_levels or []
        ):

            if (
                _level_side(level)
                != direction
            ):

                continue

            price = _level_price(
                level
            )

            if price is None:

                continue

            # ------------------------------------------------
            # LONG / SSL
            # ------------------------------------------------

            if direction == "LONG":

                low = _l(candle)

                close = _c(candle)

                if (
                    low is None
                    or close is None
                ):

                    continue

                depth = (
                    (price - low)
                    / price
                    * 100
                )

                rejected = (
                    close > price
                    and _bull(candle)
                )

                if (
                    low < price
                    and depth
                    >= MIN_SWEEP_DEPTH_PCT
                    and rejected
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
                        )
                        if isinstance(
                            level,
                            dict,
                        )
                        else 1,
                        "strength": level.get(
                            "strength",
                            0,
                        )
                        if isinstance(
                            level,
                            dict,
                        )
                        else 0,
                    }

            # ------------------------------------------------
            # SHORT / BSL
            # ------------------------------------------------

            else:

                high = _h(candle)

                close = _c(candle)

                if (
                    high is None
                    or close is None
                ):

                    continue

                depth = (
                    (high - price)
                    / price
                    * 100
                )

                rejected = (
                    close < price
                    and _bear(candle)
                )

                if (
                    high > price
                    and depth
                    >= MIN_SWEEP_DEPTH_PCT
                    and rejected
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
                        )
                        if isinstance(
                            level,
                            dict,
                        )
                        else 1,
                        "strength": level.get(
                            "strength",
                            0,
                        )
                        if isinstance(
                            level,
                            dict,
                        )
                        else 0,
                    }

    return None


# ============================================================
# 15M CONFIRMATION
# ============================================================

def confirmation_15m(
    candles_15m,
    sweep,
    direction,
):
    """
    После sweep ждём подтверждение 15M.

    LONG:
        bullish body close
        выше high предыдущей 15M свечи.

    SHORT:
        bearish body close
        ниже low предыдущей 15M свечи.

    Wick НЕ считается подтверждением.
    """

    if not sweep:

        return (
            False,
            None,
            None,
        )

    if direction not in {
        "LONG",
        "SHORT",
    }:

        return (
            False,
            None,
            None,
        )

    if not candles_15m:

        return (
            False,
            None,
            None,
        )

    sweep_time = _f(
        sweep.get(
            "open_time"
        )
    )

    candidates = []

    for candle in candles_15m:

        candle_time = _t(candle)

        if sweep_time is None:

            candidates.append(
                candle
            )

        elif (
            candle_time is not None
            and candle_time > sweep_time
        ):

            candidates.append(
                candle
            )

    candidates = candidates[
        -MAX_15M_CONFIRM_CANDLES:
    ]

    if len(candidates) < 2:

        return (
            False,
            None,
            None,
        )

    for i in range(
        1,
        len(candidates),
    ):

        candle = candidates[i]

        previous = candidates[i - 1]

        if (
            _body_ratio(candle)
            < MIN_BODY_RATIO
        ):

            continue

        close = _c(candle)

        if close is None:

            continue

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if direction == "LONG":

            previous_high = _h(
                previous
            )

            if (
                previous_high is None
            ):

                continue

            if (
                _bull(candle)
                and close > previous_high
            ):

                return (
                    True,
                    "15M bullish body close / structure break",
                    _t(candle),
                )

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        elif direction == "SHORT":

            previous_low = _l(
                previous
            )

            if (
                previous_low is None
            ):

                continue

            if (
                _bear(candle)
                and close < previous_low
            ):

                return (
                    True,
                    "15M bearish body close / structure break",
                    _t(candle),
                )

    return (
        False,
        None,
        None,
    )


# ============================================================
# 5M LOCAL SWINGS
# ============================================================

def _is_local_high(
    candles,
    index,
):
    """
    5M локальный high.

    Используем 1 свечу слева
    и 1 справа.

    Это нужно, чтобы бот не называл
    случайный high в чопе manipulation.
    """

    if (
        index < 1
        or index >= len(candles) - 1
    ):

        return False

    current = _h(
        candles[index]
    )

    left = _h(
        candles[index - 1]
    )

    right = _h(
        candles[index + 1]
    )

    if (
        current is None
        or left is None
        or right is None
    ):

        return False

    return (
        current >= left
        and current > right
    )


def _is_local_low(
    candles,
    index,
):
    """
    5M локальный low.
    """

    if (
        index < 1
        or index >= len(candles) - 1
    ):

        return False

    current = _l(
        candles[index]
    )

    left = _l(
        candles[index - 1]
    )

    right = _l(
        candles[index + 1]
    )

    if (
        current is None
        or left is None
        or right is None
    ):

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
    5M ILM.

    LONG:
        V-model

        local downside manipulation
        ->
        recovery
        ->
        bullish displacement.

    SHORT:
        L-model

        local upside manipulation
        ->
        recovery
        ->
        bearish displacement.

    Важный принцип:

    ILM extreme должен быть на правильной
    стороне относительно sweep / entry.

    Никакого READY с перевёрнутым SL.
    """

    if not sweep:

        return (
            False,
            None,
        )

    if direction not in {
        "LONG",
        "SHORT",
    }:

        return (
            False,
            None,
        )

    # --------------------------------------------------------
    # Starting point
    # --------------------------------------------------------

    start_time = _f(
        confirmation_time
    )

    if start_time is None:

        start_time = _f(
            sweep.get(
                "open_time"
            )
        )

    candles = []

    for candle in (
        candles_5m or []
    ):

        candle_time = _t(candle)

        if start_time is None:

            candles.append(
                candle
            )

        elif (
            candle_time is not None
            and candle_time > start_time
        ):

            candles.append(
                candle
            )

    candles = candles[
        -MAX_5M_ILM_CANDLES:
    ]

    if len(candles) < 5:

        return (
            False,
            None,
        )

    sweep_level = _f(
        sweep.get(
            "level"
        )
    )

    sweep_extreme = _f(
        sweep.get(
            "extreme"
        )
    )

    # ========================================================
    # LONG V-ILM
    # ========================================================

    if direction == "LONG":

        for i in range(
            2,
            len(candles) - 2,
        ):

            manipulation = candles[i]

            if not _is_local_low(
                candles,
                i,
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

            if (
                not before
                or not after
            ):

                continue

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

            after_highs = [
                _h(x)
                for x in after
                if _h(x) is not None
            ]

            if (
                not before_lows
                or not after_closes
                or not after_highs
            ):

                continue

            left_reference = min(
                before_lows
            )

            right_close = max(
                after_closes
            )

            right_high = max(
                after_highs
            )

            if (
                left_reference
                <= manipulation_low
            ):

                continue

            # -----------------------------------------------
            # Manipulation depth
            # -----------------------------------------------

            manipulation_pct = (
                (
                    left_reference
                    - manipulation_low
                )
                / left_reference
                * 100
            )

            if (
                manipulation_pct
                < MIN_SWEEP_DEPTH_PCT
            ):

                continue

            # -----------------------------------------------
            # Recovery
            # -----------------------------------------------

            denominator = (
                left_reference
                - manipulation_low
            )

            if denominator <= 0:

                continue

            recovery_ratio = (
                right_close
                - manipulation_low
            ) / denominator

            if (
                recovery_ratio
                < MIN_5M_RECOVERY_RATIO
            ):

                continue

            # -----------------------------------------------
            # Trigger
            # -----------------------------------------------

            trigger_index = None

            for j in range(
                i + 1,
                min(
                    len(candles),
                    i + 4,
                ),
            ):

                trigger = candles[j]

                if (
                    _bull(trigger)
                    and _body_ratio(trigger)
                    >= MIN_BODY_RATIO
                ):

                    trigger_close = _c(
                        trigger
                    )

                    if (
                        trigger_close is not None
                        and trigger_close
                        > manipulation_high
                    ):

                        trigger_index = j

                        break

            if trigger_index is None:

                continue

            trigger = candles[
                trigger_index
            ]

            trigger_close = _c(
                trigger
            )

            # -----------------------------------------------
            # Must be close enough to sweep
            # -----------------------------------------------

            if sweep_level is not None:

                distance = (
                    abs(
                        manipulation_low
                        - sweep_level
                    )
                    / sweep_level
                    * 100
                )

                if (
                    distance
                    > MIN_5M_ILM_SWEEP_DISTANCE_PCT
                ):

                    continue

            # -----------------------------------------------
            # If 1H sweep extreme exists,
            # ILM low should not magically be above
            # the swept zone.
            # -----------------------------------------------

            if (
                sweep_extreme is not None
                and manipulation_low
                > sweep_extreme
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
                    "trigger_time": _t(
                        trigger
                    ),
                    "trigger_price": trigger_close,
                    "reason": (
                        "5M V-ILM: "
                        "downside manipulation "
                        "+ recovery "
                        "+ bullish displacement"
                    ),
                },
            )

    # ========================================================
    # SHORT L-ILM
    # ========================================================

    else:

        for i in range(
            2,
            len(candles) - 2,
        ):

            manipulation = candles[i]

            if not _is_local_high(
                candles,
                i,
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

            if (
                not before
                or not after
            ):

                continue

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

            after_lows = [
                _l(x)
                for x in after
                if _l(x) is not None
            ]

            if (
                not before_highs
                or not after_closes
                or not after_lows
            ):

                continue

            left_reference = max(
                before_highs
            )

            right_close = min(
                after_closes
            )

            right_low = min(
                after_lows
            )

            if (
                left_reference
                >= manipulation_high
            ):

                # Для upside manipulation
                # reference должен быть ниже
                # экстремума.
                #
                # Поэтому берём локальный
                # предыдущий high как baseline.
                pass

            # ------------------------------------------------
            # Для SHORT manipulation должна быть
            # выше предыдущего reference.
            # ------------------------------------------------

            if (
                manipulation_high
                <= left_reference
            ):

                continue

            # -----------------------------------------------
            # Manipulation depth
            # -----------------------------------------------

            manipulation_pct = (
                (
                    manipulation_high
                    - left_reference
                )
                / left_reference
                * 100
            )

            if (
                manipulation_pct
                < MIN_SWEEP_DEPTH_PCT
            ):

                continue

            # -----------------------------------------------
            # Recovery
            #
            # Полный manipulation range:
            #
            # high - left_reference
            #
            # Для SHORT recovery:
            # high -> downward movement.
            # -----------------------------------------------

            manipulation_range = (
                manipulation_high
                - left_reference
            )

            if manipulation_range <= 0:

                continue

            recovery_distance = (
                manipulation_high
                - right_close
            )

            recovery_ratio = (
                recovery_distance
                / manipulation_range
            )

            # Если цена ушла вниз
            # хотя бы на требуемую долю
            # manipulation range.
            #
            # При этом допускаем и более глубокое
            # восстановление.
            if (
                recovery_ratio
                < MIN_5M_RECOVERY_RATIO
            ):

                continue

            # -----------------------------------------------
            # Trigger
            # -----------------------------------------------

            trigger_index = None

            for j in range(
                i + 1,
                min(
                    len(candles),
                    i + 4,
                ),
            ):

                trigger = candles[j]

                if (
                    _bear(trigger)
                    and _body_ratio(trigger)
                    >= MIN_BODY_RATIO
                ):

                    trigger_close = _c(
                        trigger
                    )

                    if (
                        trigger_close is not None
                        and trigger_close
                        < manipulation_low
                    ):

                        trigger_index = j

                        break

            if trigger_index is None:

                continue

            trigger = candles[
                trigger_index
            ]

            trigger_close = _c(
                trigger
            )

            # -----------------------------------------------
            # Sweep proximity
            # -----------------------------------------------

            if sweep_level is not None:

                distance = (
                    abs(
                        manipulation_high
                        - sweep_level
                    )
                    / sweep_level
                    * 100
                )

                if (
                    distance
                    > MIN_5M_ILM_SWEEP_DISTANCE_PCT
                ):

                    continue

            # -----------------------------------------------
            # ILM high should remain around
            # the swept BSL region.
            # -----------------------------------------------

            if (
                sweep_extreme is not None
                and manipulation_high
                < sweep_extreme
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
                    "trigger_time": _t(
                        trigger
                    ),
                    "trigger_price": trigger_close,
                    "reason": (
                        "5M L-ILM: "
                        "upside manipulation "
                        "+ recovery "
                        "+ bearish displacement"
                    ),
                },
            )

    return (
        False,
        None,
    )


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
    TP = ближайшая свежая major liquidity
    в направлении сделки.

    LONG:
        ближайшая major liquidity выше Entry.

    SHORT:
        ближайшая major liquidity ниже Entry.

    Уже использованный sweep-level
    исключается.
    """

    price = _f(
        current_price
    )

    excluded = _f(
        exclude_level
    )

    if price is None:

        return None

    candidates = []

    for level in (
        major_levels or []
    ):

        level_price = _level_price(
            level
        )

        if level_price is None:

            continue

        # ----------------------------------------------------
        # Exclude sweep level
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # LONG target
        # ----------------------------------------------------

        if direction == "LONG":

            if (
                level_price > price
            ):

                candidates.append(
                    level_price
                )

        # ----------------------------------------------------
        # SHORT target
        # ----------------------------------------------------

        elif direction == "SHORT":

            if (
                level_price < price
            ):

                candidates.append(
                    level_price
                )

    if not candidates:

        return None

    return min(
        candidates,
        key=lambda x: abs(
            x - price
        ),
    )


# ============================================================
# ENTRY
# ============================================================

def calculate_entry(
    current_price,
    direction,
):
    """
    В текущем режиме Entry =
    текущая рыночная цена.

    Проверка направления выполняется
    позже в validate_trade_geometry().
    """

    price = _f(
        current_price
    )

    if price is None:

        return None

    if direction not in {
        "LONG",
        "SHORT",
    }:

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
    """
    SL строится за экстремумом.

    LONG:
        SL ниже extreme.

    SHORT:
        SL выше extreme.

    ВАЖНО:
        Функция не разрешает неправильную
        геометрию.
    """

    entry = _f(entry)

    extreme = _f(extreme)

    if (
        entry is None
        or extreme is None
    ):

        return None

    if direction == "LONG":

        sl = (
            extreme
            * (
                1
                - SL_BUFFER_PCT / 100
            )
        )

        # Критическая защита.
        # Для LONG SL обязан быть ниже Entry.

        if sl >= entry:

            return None

        return sl

    if direction == "SHORT":

        sl = (
            extreme
            * (
                1
                + SL_BUFFER_PCT / 100
            )
        )

        # Для SHORT SL обязан быть выше Entry.

        if sl <= entry:

            return None

        return sl

    return None


# ============================================================
# TAKE PROFIT
# ============================================================

def calculate_take_profit(
    major_levels,
    direction,
    entry,
    sweep_level=None,
):
    """
    TP = следующая major liquidity.

    Не двигаем TP искусственно ради RR.

    Если следующая liquidity даёт RR < 1:2,
    сделка будет запрещена.
    """

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
    """
    Расчёт RR.

    Для корректного направления:

    LONG:
        SL < Entry < TP

    SHORT:
        TP < Entry < SL

    Если геометрия неверная,
    возвращается None.
    """

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

        if not (
            sl < entry < tp
        ):

            return None

    elif direction == "SHORT":

        if not (
            tp < entry < sl
        ):

            return None

    else:

        if sl == entry:

            return None

        if tp == entry:

            return None

    risk = abs(
        entry - sl
    )

    reward = abs(
        tp - entry
    )

    if risk <= 0:

        return None

    return (
        reward / risk
    )


# ============================================================
# TRADE GEOMETRY
# ============================================================

def validate_trade_geometry(
    entry,
    sl,
    tp,
    direction,
):
    """
    Жёсткая проверка геометрии.

    LONG:
        SL < Entry < TP

    SHORT:
        TP < Entry < SL

    Никакой score не может обойти эту проверку.
    """

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

        return (
            sl < entry
            and entry < tp
        )

    if direction == "SHORT":

        return (
            tp < entry
            and entry < sl
        )

    return False


# ============================================================
# TARGET VALIDATION
# ============================================================

def validate_target(
    entry,
    tp,
    direction,
):
    """
    Проверяет, что TP находится
    действительно в направлении сделки.
    """

    entry = _f(entry)

    tp = _f(tp)

    if (
        entry is None
        or tp is None
    ):

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
    sweep,
    confirmation,
    ilm,
    rr,
    major_strength=0,
):
    """
    Score.

    Направление        20
    Sweep              20
    15M confirmation   20
    5M ILM             20
    RR >= 2             15
    Major strength      5

    Максимум 100.
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

    if (
        rr is not None
        and rr >= MIN_RR
    ):

        score += 15

    if major_strength >= 3:

        score += 5

    return min(
        100,
        score,
    )


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
    Главный анализ TradeMind.

    Pipeline:

        1H
        ↓
        Major Liquidity
        ↓
        Sweep
        ↓
        15M Confirmation
        ↓
        5M ILM
        ↓
        Entry
        ↓
        SL
        ↓
        TP
        ↓
        RR
        ↓
        READY
    """

    result = {
        "stage": "WAIT",
        "direction": None,
        "score": 0,
        "reason": "",
        "entry": None,
        "sl": None,
        "tp": None,
        "rr": None,
        "sweep": sweep,
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
    }

    # ========================================================
    # DATA CHECK
    # ========================================================

    price = _f(
        current_price
    )

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
    # 1H DIRECTION
    # ========================================================

    direction = get_1h_direction(
        candles_1h
    )

    result["direction"] = direction

    if direction == "NEUTRAL":

        result["score"] = 25

        result["reason"] = (
            "1H не даёт однозначного направления."
        )

        return result

    # ========================================================
    # MAJOR LIQUIDITY CHECK
    # ========================================================

    if not major_levels:

        result["score"] = 30

        result["reason"] = (
            "Не найдена крупная 1H ликвидность."
        )

        return result

    # ========================================================
    # SWEEP CHECK
    # ========================================================

    if sweep is None:

        result["score"] = 35

        if direction == "LONG":

            result["reason"] = (
                "1H LONG → ждём снятие SSL."
            )

        else:

            result["reason"] = (
                "1H SHORT → ждём снятие BSL."
            )

        return result

    # ========================================================
    # SWEEP
    # ========================================================

    sweep_direction = str(
        sweep.get(
            "direction",
            "",
        )
    ).upper()

    # Не принимаем sweep
    # противоположного направления.

    if sweep_direction != direction:

        result["score"] = 35

        result["sweep"] = None

        result["reason"] = (
            "Sweep найден, но его направление "
            "не совпадает с 1H."
        )

        return result

    result["stage"] = "SWEPT"

    result["sweep"] = sweep

    result["sweep_extreme"] = sweep.get(
        "extreme"
    )

    # ========================================================
    # 15M CONFIRMATION
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

        result["score"] = 60

        result["reason"] = (
            "Sweep есть. "
            "Ждём подтверждение 15M."
        )

        return result

    # ========================================================
    # 15M CONFIRMED
    # ========================================================

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

        result["score"] = 70

        result["reason"] = (
            "15M подтверждение есть. "
            "Ждём 5M ILM."
        )

        return result

    # ========================================================
    # ENTRY
    # ========================================================

    entry = calculate_entry(
        price,
        direction,
    )

    if entry is None:

        result["score"] = 72

        result["reason"] = (
            "Не удалось определить Entry."
        )

        return result

    # ========================================================
    # EXTREME
    # ========================================================

    ilm_extreme = _f(
        (ilm or {}).get(
            "extreme"
        )
    )

    sweep_extreme = _f(
        sweep.get(
            "extreme"
        )
    )

    # Для LONG выбираем более низкий
    # экстремум.

    if direction == "LONG":

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
                "Не удалось определить "
                "экстремум для SL."
            )

            return result

        extreme = min(
            candidates
        )

    # Для SHORT выбираем более высокий
    # экстремум.

    else:

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
                "Не удалось определить "
                "экстремум для SL."
            )

            return result

        extreme = max(
            candidates
        )

    # ========================================================
    # CRITICAL EXTREME VALIDATION
    # ========================================================

    if direction == "LONG":

        if extreme >= entry:

            result["score"] = 72

            result["reason"] = (
                "Некорректный LONG: "
                "экстремум ILM/Sweep находится "
                "на или выше Entry. "
                "SL был бы выше Entry."
            )

            return result

    elif direction == "SHORT":

        if extreme <= entry:

            result["score"] = 72

            result["reason"] = (
                "Некорректный SHORT: "
                "экстремум ILM/Sweep находится "
                "на или ниже Entry. "
                "SL был бы ниже Entry."
            )

            return result

    # ========================================================
    # STOP LOSS
    # ========================================================

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

    # ========================================================
    # TP
    # ========================================================

    tp = calculate_take_profit(
        major_levels,
        direction,
        entry,
        sweep.get(
            "level"
        ),
    )

    if tp is None:

        result["score"] = 75

        result["reason"] = (
            "Следующая свежая major liquidity "
            "не найдена."
        )

        return result

    # ========================================================
    # TARGET VALIDATION
    # ========================================================

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

    # ========================================================
    # TRADE GEOMETRY
    # ========================================================

    geometry_valid = (
        validate_trade_geometry(
            entry,
            sl,
            tp,
            direction,
        )
    )

    result["geometry_valid"] = (
        geometry_valid
    )

    if not geometry_valid:

        result["score"] = 72

        if direction == "LONG":

            result["reason"] = (
                "Некорректная геометрия LONG: "
                "должно быть SL < Entry < TP."
            )

        else:

            result["reason"] = (
                "Некорректная геометрия SHORT: "
                "должно быть TP < Entry < SL."
            )

        return result

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

        result["score"] = 72

        result["reason"] = (
            "Не удалось рассчитать корректный RR."
        )

        return result

    # ========================================================
    # MAJOR STRENGTH
    # ========================================================

    major_strength = 0

    try:

        major_strength = max(
            [
                _level_strength(
                    level
                )
                for level in (
                    major_levels or []
                )
            ]
            or [0]
        )

    except Exception:

        major_strength = 0

    # ========================================================
    # SCORE
    # ========================================================

    score = _score(
        direction=direction,
        sweep=sweep,
        confirmation=True,
        ilm=True,
        rr=rr_value,
        major_strength=major_strength,
    )

    # ========================================================
    # RR < 2
    # ========================================================

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

    if rr_value < MIN_RR:

        result["score"] = min(
            score,
            79,
        )

        result["stage"] = (
            "15M_CONFIRMED"
        )

        result["reason"] = (
            "Следующая major liquidity "
            "слишком близко: "
            f"RR 1:{rr_value:.2f} < 1:2."
        )

        result["tp_reason"] = (
            "TP = следующая свежая "
            "major liquidity; "
            "при RR < 1:2 вход запрещён."
        )

        return result

    # ========================================================
    # FINAL GEOMETRY CHECK
    # ========================================================

    if not validate_trade_geometry(
        entry,
        sl,
        tp,
        direction,
    ):

        result["stage"] = (
            "15M_CONFIRMED"
        )

        result["score"] = min(
            score,
            79,
        )

        result["geometry_valid"] = False

        result["reason"] = (
            "Финальная проверка геометрии "
            "не пройдена. READY запрещён."
        )

        return result

    # ========================================================
    # READY
    # ========================================================

    result["stage"] = "READY"

    result["score"] = max(
        MIN_SCORE_READY,
        score,
    )

    result["geometry_valid"] = True

    result["reason"] = (
        "1H → Major Liquidity → "
        "Sweep → 15M → 5M ILM."
    )

    result["tp_reason"] = (
        "TP = следующая свежая "
        "major liquidity."
    )

    return result


# ============================================================
# SOL COMPATIBILITY
# ============================================================

def analyze_sol(
    *args,
    **kwargs,
):
    """
    Backward compatibility.
    """

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