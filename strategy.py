"""TradeMind 6.3 strategy: 1H -> Major Liquidity -> Sweep -> 15M -> 5M ILM."""

from typing import Any, Dict, List, Optional


STRATEGY_VERSION = "6.3"

MIN_SCORE_READY = 80
MIN_RR = 2.0

SL_BUFFER_PCT = 0.20

MIN_SWEEP_DEPTH_PCT = 0.08
MIN_5M_RECOVERY_RATIO = 0.33
MIN_BODY_RATIO = 0.35

MAX_SWEEP_AGE_1H = 8


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _v(c, key, default=None):
    if not isinstance(c, dict):
        return default

    x = c.get(key)

    if x is None:
        aliases = {
            "open": "o",
            "high": "h",
            "low": "l",
            "close": "c",
            "open_time": "time",
        }

        x = c.get(aliases.get(key))

    return _f(x)


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


def _swing_high(candles, i):
    if i < 2 or i >= len(candles) - 2:
        return False

    x = _h(candles[i])

    if x is None:
        return False

    return (
        x > _h(candles[i - 1])
        and x >= _h(candles[i - 2])
        and x >= _h(candles[i + 1])
        and x > _h(candles[i + 2])
    )


def _swing_low(candles, i):
    if i < 2 or i >= len(candles) - 2:
        return False

    x = _l(candles[i])

    if x is None:
        return False

    return (
        x < _l(candles[i - 1])
        and x <= _l(candles[i - 2])
        and x <= _l(candles[i + 1])
        and x < _l(candles[i + 2])
    )


def get_1h_direction(candles):
    """
    1H — единственный главный timeframe направления.

    LONG:
        HH + HL
        или явное бычье импульсное преимущество.

    SHORT:
        LH + LL
        или явное медвежье импульсное преимущество.

    NEUTRAL:
        структура неясная.
    """

    if not candles or len(candles) < 15:
        return "NEUTRAL"

    candles = candles[-60:]

    highs = [
        (i, _h(x))
        for i, x in enumerate(candles)
        if _swing_high(candles, i)
    ]

    lows = [
        (i, _l(x))
        for i, x in enumerate(candles)
        if _swing_low(candles, i)
    ]

    bullish_structure = False
    bearish_structure = False

    if len(highs) >= 2 and len(lows) >= 2:

        bullish_structure = (
            highs[-1][1] > highs[-2][1]
            and lows[-1][1] > lows[-2][1]
        )

        bearish_structure = (
            highs[-1][1] < highs[-2][1]
            and lows[-1][1] < lows[-2][1]
        )

    recent = candles[-8:]

    bullish_body = sum(
        _body(x)
        for x in recent
        if _bull(x)
    )

    bearish_body = sum(
        _body(x)
        for x in recent
        if _bear(x)
    )

    if bullish_body > bearish_body * 1.15:
        bullish_structure = True

    if bearish_body > bullish_body * 1.15:
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
    Старое имя оставлено только для совместимости.

    D1/W1 больше НЕ используются.
    """

    return get_1h_direction(candles_1h)


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


def find_sweep(
    candles_1h,
    major_levels,
    direction,
):
    """
    LONG:
        снимаем SSL снизу
        затем цена возвращается выше уровня.

    SHORT:
        снимаем BSL сверху
        затем цена возвращается ниже уровня.
    """

    if direction not in {"LONG", "SHORT"}:
        return None

    if not candles_1h or len(candles_1h) < 3:
        return None

    recent = candles_1h[-MAX_SWEEP_AGE_1H:]

    for candle in recent:

        for level in major_levels or []:

            if _level_side(level) != direction:
                continue

            price = _level_price(level)

            if price is None:
                continue

            if direction == "LONG":

                low = _l(candle)
                close = _c(candle)

                if low is None or close is None:
                    continue

                depth = (
                    (price - low) / price * 100
                )

                rejected = (
                    close > price
                    and _bull(candle)
                )

                if (
                    low < price
                    and depth >= MIN_SWEEP_DEPTH_PCT
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
                        "touches": level.get("touches", 1),
                    }

            else:

                high = _h(candle)
                close = _c(candle)

                if high is None or close is None:
                    continue

                depth = (
                    (high - price) / price * 100
                )

                rejected = (
                    close < price
                    and _bear(candle)
                )

                if (
                    high > price
                    and depth >= MIN_SWEEP_DEPTH_PCT
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
                        "touches": level.get("touches", 1),
                    }

    return None


def confirmation_15m(
    candles_15m,
    sweep,
    direction,
):
    """
    После sweep ждём закрытие 15M телом
    за ближайшей структурой.

    Wick не считается подтверждением.
    """

    if not sweep:
        return False, None, None

    sweep_time = _f(
        sweep.get("open_time")
    )

    candidates = []

    for candle in candles_15m or []:

        candle_time = _t(candle)

        if sweep_time is None:
            candidates.append(candle)

        elif candle_time is not None and candle_time > sweep_time:
            candidates.append(candle)

    candidates = candidates[-12:]

    if len(candidates) < 2:
        return False, None, None

    for i in range(1, len(candidates)):

        candle = candidates[i]
        previous = candidates[i - 1]

        if _body_ratio(candle) < MIN_BODY_RATIO:
            continue

        if direction == "LONG":

            if (
                _bull(candle)
                and _c(candle) > _h(previous)
            ):
                return (
                    True,
                    "15M bullish body close / structure break",
                    _t(candle),
                )

        elif direction == "SHORT":

            if (
                _bear(candle)
                and _c(candle) < _l(previous)
            ):
                return (
                    True,
                    "15M bearish body close / structure break",
                    _t(candle),
                )

    return False, None, None


def detect_5m_ilm(
    candles_5m,
    sweep,
    direction,
    confirmation_time=None,
):
    """
    5M ILM.

    LONG:
        V-образная модель:
        manipulation вниз
        -> recovery
        -> bullish trigger.

    SHORT:
        L-образная зеркальная модель.
    """

    if not sweep:
        return False, None

    start_time = (
        _f(confirmation_time)
        or _f(sweep.get("open_time"))
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

    candles = candles[-30:]

    if len(candles) < 5:
        return False, None

    for i in range(2, len(candles) - 2):

        manipulation = candles[i]

        before = candles[
            max(0, i - 2):i
        ]

        after = candles[
            i + 1:min(len(candles), i + 4)
        ]

        if not before or not after:
            continue

        if direction == "LONG":

            lows_before = [
                _l(x)
                for x in before
                if _l(x) is not None
            ]

            closes_after = [
                _c(x)
                for x in after
                if _c(x) is not None
            ]

            low = _l(manipulation)

            if (
                not lows_before
                or not closes_after
                or low is None
            ):
                continue

            left = min(lows_before)
            right = max(closes_after)

            manipulation_pct = (
                (left - low)
                / left
                * 100
            )

            recovery_ratio = (
                (right - low)
                / (left - low)
                if left > low
                else 0
            )

            trigger = candles[
                min(i + 1, len(candles) - 1)
            ]

            valid = (
                manipulation_pct
                >= MIN_SWEEP_DEPTH_PCT
                and recovery_ratio
                >= MIN_5M_RECOVERY_RATIO
                and right > _h(manipulation)
                and _body_ratio(trigger)
                >= MIN_BODY_RATIO
            )

            if valid:

                return True, {
                    "direction": "LONG",
                    "extreme": low,
                    "trigger_time": _t(trigger),
                    "reason": (
                        "5M V-ILM: "
                        "downside manipulation + recovery"
                    ),
                }

        else:

            highs_after = [
                _h(x)
                for x in after
                if _h(x) is not None
            ]

            closes_before = [
                _c(x)
                for x in before
                if _c(x) is not None
            ]

            high = _h(manipulation)

            if (
                not highs_after
                or not closes_before
                or high is None
            ):
                continue

            right = max(highs_after)
            left = min(closes_before)

            manipulation_pct = (
                (high - left)
                / left
                * 100
            )

            recovery_ratio = (
                (high - left)
                / (high - right)
                if high > right
                else 0
            )

            trigger = candles[
                min(i + 1, len(candles) - 1)
            ]

            valid = (
                manipulation_pct
                >= MIN_SWEEP_DEPTH_PCT
                and recovery_ratio
                >= MIN_5M_RECOVERY_RATIO
                and right < _l(manipulation)
                and _body_ratio(trigger)
                >= MIN_BODY_RATIO
            )

            if valid:

                return True, {
                    "direction": "SHORT",
                    "extreme": high,
                    "trigger_time": _t(trigger),
                    "reason": (
                        "5M L-ILM: "
                        "upside manipulation + recovery"
                    ),
                }

    return False, None


def next_target(
    major_levels,
    direction,
    current_price,
    exclude_level=None,
):
    """
    TP = следующая свежая major liquidity
    в направлении сделки.
    """

    price = _f(current_price)
    excluded = _f(exclude_level)

    if price is None:
        return None

    candidates = []

    for level in major_levels or []:

        level_price = _level_price(level)

        if level_price is None:
            continue

        if excluded is not None:

            if (
                abs(level_price - excluded)
                / excluded
                * 100
                < 0.05
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


def _score(
    direction,
    sweep,
    confirmation,
    ilm,
    rr,
    major_strength=0,
):
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
        "major_levels": major_levels or [],
        "confirmation_15m": False,
        "confirmation_15m_time": None,
        "ilm": None,
        "sweep_extreme": None,
        "tp_reason": None,
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

    if not major_levels:

        result["score"] = 30
        result["reason"] = (
            "Не найдена крупная 1H ликвидность."
        )

        return result

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

    result["stage"] = "SWEPT"

    result["sweep"] = sweep

    result["sweep_extreme"] = sweep.get(
        "extreme"
    )

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

    result["stage"] = "15M_CONFIRMED"

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

    entry = price

    extreme = (
        _f((ilm or {}).get("extreme"))
        or _f(sweep.get("extreme"))
    )

    if extreme is None:

        result["score"] = 72
        result["reason"] = (
            "Не удалось определить экстремум для SL."
        )

        return result

    if direction == "LONG":

        sl = (
            extreme
            * (1 - SL_BUFFER_PCT / 100)
        )

    else:

        sl = (
            extreme
            * (1 + SL_BUFFER_PCT / 100)
        )

    tp = next_target(
        major_levels,
        direction,
        entry,
        sweep.get("level"),
    )

    if tp is None:

        result["score"] = 75
        result["reason"] = (
            "Следующая свежая major liquidity "
            "не найдена."
        )

        return result

    risk = abs(entry - sl)
    reward = abs(tp - entry)

    rr_value = (
        reward / risk
        if risk > 0
        else None
    )

    score = _score(
        direction,
        sweep,
        True,
        True,
        rr_value,
        int(
            sweep.get("touches")
            or 1
        ),
    )

    result.update({
        "entry": round(entry, 6),
        "sl": round(sl, 6),
        "tp": round(tp, 6),
        "rr": rr_value,
    })

    if (
        rr_value is None
        or rr_value < MIN_RR
    ):

        result["score"] = min(
            score,
            79,
        )

        result["stage"] = (
            "15M_CONFIRMED"
        )

        result["reason"] = (
            f"Следующая major liquidity "
            f"слишком близко: "
            f"RR 1:{rr_value:.2f} < 1:2."
        )

        result["tp_reason"] = (
            "TP = следующая свежая major liquidity; "
            "при RR < 1:2 вход запрещён."
        )

        return result

    result["stage"] = "READY"

    result["score"] = max(
        MIN_SCORE_READY,
        score,
    )

    result["reason"] = (
        "1H → Major Liquidity → "
        "Sweep → 15M → 5M ILM."
    )

    result["tp_reason"] = (
        "TP = следующая свежая major liquidity."
    )

    return result


def analyze_sol(*args, **kwargs):
    return analyze(*args, **kwargs)


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
    "next_target",
    "analyze",
    "analyze_sol",
]