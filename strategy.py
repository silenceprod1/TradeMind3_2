from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


# ============================================================
# TRADEMIND STRATEGY 6.2.1
#
# 1H -> MAJOR LIQUIDITY -> SWEEP
# -> 15M CONFIRMATION -> 5M ILM -> ENTRY
#
# D1/W1 REMOVED COMPLETELY.
# ============================================================

STRATEGY_VERSION = "6.2.1"

MIN_SCORE_READY = 80
MIN_RR = 2.0

# SL buffer beyond sweep extreme.
# 0.20 = 0.20%
SL_BUFFER_PCT = 0.20

# Minimum 5M manipulation.
MIN_5M_MANIPULATION_PCT = 0.08

# Recovery must be >= 1/3.
MIN_5M_RECOVERY_RATIO = 0.33

# Minimum candle body / total range.
MIN_DISPLACEMENT_BODY_RATIO = 0.35

# Minimum FVG.
MIN_FVG_PCT = 0.03

# Ignore microscopic target.
MIN_TP_DISTANCE_PCT = 0.15

# Maximum distance between 5M ILM and sweep extreme.
MAX_ILM_SWEEP_DISTANCE_PCT = 0.75

# 1H structure lookback.
H1_LOOKBACK = 80

# 15M confirmation.
M15_LOOKBACK = 12

# 5M trigger.
M5_LOOKBACK = 30

# Minimum number of candles needed.
MIN_CANDLES_STRUCTURE = 15


# ============================================================
# BASIC HELPERS
# ============================================================

def _safe_float(
    value,
    default=None,
):

    try:

        if value is None:
            return default

        return float(value)

    except Exception:

        return default


def _pct(
    a,
    b,
):

    a = _safe_float(a)
    b = _safe_float(b)

    if (
        a is None
        or b in (
            None,
            0,
        )
    ):

        return 0.0

    return (
        abs(a - b)
        /
        abs(b)
        *
        100.0
    )


def _body(
    candle,
):

    return abs(

        _safe_float(
            candle.get(
                "close"
            ),
            0,
        )

        -

        _safe_float(
            candle.get(
                "open"
            ),
            0,
        )

    )


def _range(
    candle,
):

    return (

        _safe_float(
            candle.get(
                "high"
            ),
            0,
        )

        -

        _safe_float(
            candle.get(
                "low"
            ),
            0,
        )

    )


def _body_ratio(
    candle,
):

    rng = _range(
        candle
    )

    if rng <= 0:
        return 0.0

    return (
        _body(candle)
        /
        rng
    )


def _bullish(
    candle,
):

    return (

        _safe_float(
            candle.get(
                "close"
            ),
            0,
        )

        >

        _safe_float(
            candle.get(
                "open"
            ),
            0,
        )

    )


def _bearish(
    candle,
):

    return (

        _safe_float(
            candle.get(
                "close"
            ),
            0,
        )

        <

        _safe_float(
            candle.get(
                "open"
            ),
            0,
        )

    )


def _high(
    candle,
):

    return _safe_float(
        candle.get(
            "high"
        )
    )


def _low(
    candle,
):

    return _safe_float(
        candle.get(
            "low"
        )
    )


def _open(
    candle,
):

    return _safe_float(
        candle.get(
            "open"
        )
    )


def _close(
    candle,
):

    return _safe_float(
        candle.get(
            "close"
        )
    )


# ============================================================
# SWINGS
# ============================================================

def _swing_highs(
    candles,
):

    result = []

    if not candles:
        return result

    data = candles[
        -H1_LOOKBACK:
    ]

    for i in range(
        1,
        len(data) - 1,
    ):

        left = _high(
            data[i - 1]
        )

        current = _high(
            data[i]
        )

        right = _high(
            data[i + 1]
        )

        if None in (
            left,
            current,
            right,
        ):
            continue

        if (
            current > left
            and current >= right
        ):

            result.append({

                "price":
                    current,

                "index":
                    i,

                "time":
                    data[i].get(
                        "open_time"
                    ),

            })

    return result


def _swing_lows(
    candles,
):

    result = []

    if not candles:
        return result

    data = candles[
        -H1_LOOKBACK:
    ]

    for i in range(
        1,
        len(data) - 1,
    ):

        left = _low(
            data[i - 1]
        )

        current = _low(
            data[i]
        )

        right = _low(
            data[i + 1]
        )

        if None in (
            left,
            current,
            right,
        ):
            continue

        if (
            current < left
            and current <= right
        ):

            result.append({

                "price":
                    current,

                "index":
                    i,

                "time":
                    data[i].get(
                        "open_time"
                    ),

            })

    return result


# ============================================================
# STRUCTURE
# ============================================================

def structure_context(
    candles,
):

    if (
        not candles
        or len(candles)
        < MIN_CANDLES_STRUCTURE
    ):

        return {

            "direction":
                "NEUTRAL",

            "bos":
                False,

            "impulse":
                0.0,

            "structure_score":
                0,

            "reason":
                "Недостаточно 1H данных",

        }

    highs = _swing_highs(
        candles
    )

    lows = _swing_lows(
        candles
    )

    if (
        len(highs) < 2
        or len(lows) < 2
    ):

        return {

            "direction":
                "NEUTRAL",

            "bos":
                False,

            "impulse":
                0.0,

            "structure_score":
                0,

            "reason":
                "Недостаточно swing-структуры",

        }

    last_high = highs[-1][
        "price"
    ]

    previous_high = highs[-2][
        "price"
    ]

    last_low = lows[-1][
        "price"
    ]

    previous_low = lows[-2][
        "price"
    ]

    bullish_score = 0
    bearish_score = 0

    # --------------------------------------------------------
    # HH
    # --------------------------------------------------------

    if last_high > previous_high:

        bullish_score += 2

    elif last_high < previous_high:

        bearish_score += 2

    # --------------------------------------------------------
    # HL / LL
    # --------------------------------------------------------

    if last_low > previous_low:

        bullish_score += 2

    elif last_low < previous_low:

        bearish_score += 2

    # --------------------------------------------------------
    # Recent body displacement.
    # --------------------------------------------------------

    recent = candles[
        -12:
    ]

    start = _open(
        recent[0]
    )

    end = _close(
        recent[-1]
    )

    impulse = 0.0

    if (
        start is not None
        and start != 0
        and end is not None
    ):

        impulse = (

            (
                end
                -
                start
            )
            /
            start
            *
            100.0

        )

    if impulse >= 0.80:

        bullish_score += 1

    elif impulse <= -0.80:

        bearish_score += 1

    # --------------------------------------------------------
    # Recent BOS using CLOSED candles.
    # --------------------------------------------------------

    recent_structure = candles[
        -8:
    ]

    latest = recent_structure[
        -1
    ]

    latest_close = _close(
        latest
    )

    previous_highs = [

        _high(c)

        for c
        in recent_structure[:-1]

        if _high(c) is not None

    ]

    previous_lows = [

        _low(c)

        for c
        in recent_structure[:-1]

        if _low(c) is not None

    ]

    bullish_bos = False
    bearish_bos = False

    if (
        latest_close is not None
        and previous_highs
    ):

        bullish_bos = (
            latest_close
            >
            max(previous_highs)
        )

    if (
        latest_close is not None
        and previous_lows
    ):

        bearish_bos = (
            latest_close
            <
            min(previous_lows)
        )

    if bullish_bos:
        bullish_score += 2

    if bearish_bos:
        bearish_score += 2

    # --------------------------------------------------------
    # Direction.
    # --------------------------------------------------------

    if (
        bullish_score
        >
        bearish_score
    ):

        return {

            "direction":
                "BULLISH",

            "bos":
                bullish_bos,

            "impulse":
                round(
                    impulse,
                    3,
                ),

            "structure_score":
                bullish_score,

            "last_high":
                last_high,

            "previous_high":
                previous_high,

            "last_low":
                last_low,

            "previous_low":
                previous_low,

            "reason":
                "1H бычья структура",

        }

    if (
        bearish_score
        >
        bullish_score
    ):

        return {

            "direction":
                "BEARISH",

            "bos":
                bearish_bos,

            "impulse":
                round(
                    impulse,
                    3,
                ),

            "structure_score":
                bearish_score,

            "last_high":
                last_high,

            "previous_high":
                previous_high,

            "last_low":
                last_low,

            "previous_low":
                previous_low,

            "reason":
                "1H медвежья структура",

        }

    return {

        "direction":
            "NEUTRAL",

        "bos":
            False,

        "impulse":
            round(
                impulse,
                3,
            ),

        "structure_score":
            0,

        "last_high":
            last_high,

        "previous_high":
            previous_high,

        "last_low":
            last_low,

        "previous_low":
            previous_low,

        "reason":
            "1H структура нейтральна",

    }


# ============================================================
# 1H DIRECTION
# ============================================================

def get_1h_direction(
    candles_1h,
):

    structure = structure_context(
        candles_1h
    )

    if (
        structure.get(
            "direction"
        )
        == "BULLISH"
    ):

        return {

            "direction":
                "LONG",

            "source":
                "1H",

            "structure":
                structure,

        }

    if (
        structure.get(
            "direction"
        )
        == "BEARISH"
    ):

        return {

            "direction":
                "SHORT",

            "source":
                "1H",

            "structure":
                structure,

        }

    return {

        "direction":
            "NEUTRAL",

        "source":
            "1H",

        "structure":
            structure,

    }


# ============================================================
# BACKWARD COMPATIBILITY
#
# Старый код может случайно вызвать функцию.
# D1/W1 больше не используются.
# ============================================================

def get_higher_timeframe_direction(
    candles_1h=None,
    candles_d1=None,
    candles_w1=None,
):

    return get_1h_direction(
        candles_1h
    )


# ============================================================
# LIQUIDITY NORMALIZATION
# ============================================================

def _normalize_liquidity_levels(
    major_levels,
):

    result = []

    if not major_levels:
        return result

    for level in major_levels:

        if not isinstance(
            level,
            dict,
        ):
            continue

        price = _safe_float(

            level.get(
                "price"
            )

            if level.get(
                "price"
            ) is not None

            else level.get(
                "level"
            )

        )

        if (
            price is None
            or price <= 0
        ):
            continue

        item = dict(
            level
        )

        item[
            "price"
        ] = price

        if item.get(
            "swept"
        ) is None:

            item[
                "swept"
            ] = False

        # ----------------------------------------------------
        # Compatibility:
        # market.py 6.2.1 uses:
        #
        # SHORT = BSL / ABOVE
        # LONG  = SSL / BELOW
        # ----------------------------------------------------

        if item.get(
            "position"
        ) == "ABOVE":

            item[
                "side"
            ] = "SHORT"

        elif item.get(
            "position"
        ) == "BELOW":

            item[
                "side"
            ] = "LONG"

        elif item.get(
            "side"
        ) == "ABOVE":

            item[
                "side"
            ] = "SHORT"

        elif item.get(
            "side"
        ) == "BELOW":

            item[
                "side"
            ] = "LONG"

        result.append(
            item
        )

    return result


# ============================================================
# LIQUIDITY SIDES
# ============================================================

def get_liquidity_sides(
    major_levels,
    current_price,
    max_levels_each_side=6,
):

    current_price = _safe_float(
        current_price
    )

    if current_price is None:

        return {

            "above":
                [],

            "below":
                [],

        }

    levels = _normalize_liquidity_levels(
        major_levels
    )

    above = []
    below = []

    for level in levels:

        price = level[
            "price"
        ]

        if (
            level.get(
                "swept"
            )
            is True
        ):
            continue

        item = dict(
            level
        )

        distance = _pct(
            price,
            current_price,
        )

        item[
            "distance_pct"
        ] = round(
            distance,
            4,
        )

        # ----------------------------------------------------
        # BSL
        # ----------------------------------------------------

        if price > current_price:

            item[
                "position"
            ] = "ABOVE"

            item[
                "side"
            ] = "SHORT"

            item[
                "liquidity_type"
            ] = "BSL"

            above.append(
                item
            )

        # ----------------------------------------------------
        # SSL
        # ----------------------------------------------------

        elif price < current_price:

            item[
                "position"
            ] = "BELOW"

            item[
                "side"
            ] = "LONG"

            item[
                "liquidity_type"
            ] = "SSL"

            below.append(
                item
            )

    above.sort(

        key=lambda x: (

            x[
                "distance_pct"
            ],

            -_safe_float(
                x.get(
                    "touches"
                ),
                0,
            ),

            -_safe_float(
                x.get(
                    "strength"
                ),
                0,
            ),

        )

    )

    below.sort(

        key=lambda x: (

            x[
                "distance_pct"
            ],

            -_safe_float(
                x.get(
                    "touches"
                ),
                0,
            ),

            -_safe_float(
                x.get(
                    "strength"
                ),
                0,
            ),

        )

    )

    return {

        "above":
            above[
                :max_levels_each_side
            ],

        "below":
            below[
                :max_levels_each_side
            ],

    }


# ============================================================
# DIRECTIONAL LIQUIDITY
# ============================================================

def get_directional_liquidity(
    major_levels,
    current_price,
    direction,
):

    sides = get_liquidity_sides(

        major_levels,

        current_price,

    )

    if direction == "LONG":

        return (

            sides[
                "below"
            ][0]

            if sides[
                "below"
            ]

            else None

        )

    if direction == "SHORT":

        return (

            sides[
                "above"
            ][0]

            if sides[
                "above"
            ]

            else None

        )

    return None


# ============================================================
# NEXT MAJOR TARGET
# ============================================================

def get_next_major_target(
    major_levels,
    entry,
    direction,
    excluded_price=None,
):

    entry = _safe_float(
        entry
    )

    if entry is None:
        return None

    excluded_price = _safe_float(
        excluded_price
    )

    levels = _normalize_liquidity_levels(
        major_levels
    )

    candidates = []

    for level in levels:

        price = level[
            "price"
        ]

        # ----------------------------------------------------
        # Swept liquidity NEVER becomes a fresh TP.
        # ----------------------------------------------------

        if level.get(
            "swept"
        ) is True:

            continue

        # ----------------------------------------------------
        # Exclude sweep level.
        # ----------------------------------------------------

        if (
            excluded_price is not None
            and
            _pct(
                price,
                excluded_price,
            )
            < 0.10
        ):

            continue

        if direction == "LONG":

            if price > entry:

                item = dict(
                    level
                )

                item[
                    "liquidity_type"
                ] = "BSL"

                candidates.append(
                    item
                )

        elif direction == "SHORT":

            if price < entry:

                item = dict(
                    level
                )

                item[
                    "liquidity_type"
                ] = "SSL"

                candidates.append(
                    item
                )

    if not candidates:
        return None

    if direction == "LONG":

        candidates.sort(
            key=lambda x:
                x["price"]
        )

    else:

        candidates.sort(

            key=lambda x:
                x["price"],

            reverse=True,

        )

    return candidates[0]


# ============================================================
# FVG
# ============================================================

def find_fvg(
    candles,
    direction=None,
):

    if (
        not candles
        or len(candles) < 3
    ):
        return None

    data = candles[
        -20:
    ]

    found = []

    for i in range(
        2,
        len(data),
    ):

        c1 = data[
            i - 2
        ]

        c2 = data[
            i - 1
        ]

        c3 = data[
            i
        ]

        h1 = _high(c1)
        l1 = _low(c1)

        h2 = _high(c2)
        l2 = _low(c2)

        h3 = _high(c3)
        l3 = _low(c3)

        if None in (
            h1,
            l1,
            h2,
            l2,
            h3,
            l3,
        ):
            continue

        # ----------------------------------------------------
        # Bullish FVG
        # ----------------------------------------------------

        if l3 > h1:

            size_pct = _pct(
                l3,
                h1,
            )

            if size_pct >= MIN_FVG_PCT:

                found.append({

                    "direction":
                        "LONG",

                    "low":
                        h1,

                    "high":
                        l3,

                    "mid":
                        (
                            h1
                            +
                            l3
                        )
                        /
                        2.0,

                    "size_pct":
                        round(
                            size_pct,
                            4,
                        ),

                    "time":
                        c3.get(
                            "open_time"
                        ),

                    "index":
                        i,

                    "body_high":
                        max(
                            _open(c2)
                            or h2,
                            _close(c2)
                            or h2,
                        ),

                    "body_low":
                        min(
                            _open(c2)
                            or l2,
                            _close(c2)
                            or l2,
                        ),

                })

        # ----------------------------------------------------
        # Bearish FVG
        # ----------------------------------------------------

        if h3 < l1:

            size_pct = _pct(
                l1,
                h3,
            )

            if size_pct >= MIN_FVG_PCT:

                found.append({

                    "direction":
                        "SHORT",

                    "low":
                        h3,

                    "high":
                        l1,

                    "mid":
                        (
                            h3
                            +
                            l1
                        )
                        /
                        2.0,

                    "size_pct":
                        round(
                            size_pct,
                            4,
                        ),

                    "time":
                        c3.get(
                            "open_time"
                        ),

                    "index":
                        i,

                    "body_high":
                        max(
                            _open(c2)
                            or h3,
                            _close(c2)
                            or h3,
                        ),

                    "body_low":
                        min(
                            _open(c2)
                            or l1,
                            _close(c2)
                            or l1,
                        ),

                })

    if not found:
        return None

    if direction:

        directional = [

            x

            for x in found

            if x[
                "direction"
            ]
            == direction

        ]

        if directional:

            return directional[-1]

    return found[-1]


# ============================================================
# FVG SUPPORT
# ============================================================

def fvg_supports_direction(
    candles,
    fvg,
    direction,
):

    if (
        not fvg
        or not candles
    ):
        return False

    if (
        fvg.get(
            "direction"
        )
        != direction
    ):
        return False

    last = candles[-1]

    close = _close(
        last
    )

    if close is None:
        return False

    low = _safe_float(
        fvg.get("low")
    )

    high = _safe_float(
        fvg.get("high")
    )

    if None in (
        low,
        high,
    ):
        return False

    if direction == "LONG":

        return close >= low

    if direction == "SHORT":

        return close <= high

    return False


# ============================================================
# 15M CONFIRMATION
# ============================================================

def confirmation_15m(
    candles_15m,
    direction,
    sweep=None,
):

    if (
        not candles_15m
        or len(candles_15m) < 8
    ):
        return None

    data = candles_15m[
        -M15_LOOKBACK:
    ]

    # --------------------------------------------------------
    # Determine where the sweep happened.
    # --------------------------------------------------------

    sweep_time = None

    if sweep:

        sweep_time = sweep.get(
            "time"
        )

    # --------------------------------------------------------
    # Only candles after sweep.
    # --------------------------------------------------------

    if sweep_time is not None:

        after_sweep = [

            c

            for c in data

            if (
                c.get(
                    "open_time"
                )
                is not None

                and

                c.get(
                    "open_time"
                )
                >= sweep_time
            )

        ]

        if len(after_sweep) >= 2:

            data = after_sweep

    if len(data) < 2:
        return None

    current = data[-1]

    current_close = _close(
        current
    )

    current_open = _open(
        current
    )

    current_high = _high(
        current
    )

    current_low = _low(
        current
    )

    if None in (
        current_close,
        current_open,
        current_high,
        current_low,
    ):

        return None

    previous = data[
        :-1
    ]

    previous_highs = [

        _high(c)

        for c in previous

        if _high(c) is not None

    ]

    previous_lows = [

        _low(c)

        for c in previous

        if _low(c) is not None

    ]

    if not previous_highs:
        return None

    if not previous_lows:
        return None

    previous_high = max(
        previous_highs
    )

    previous_low = min(
        previous_lows
    )

    body_ratio = _body_ratio(
        current
    )

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        bos = (
            current_close
            >
            previous_high
        )

        displacement = (

            _bullish(
                current
            )

            and

            body_ratio
            >=
            MIN_DISPLACEMENT_BODY_RATIO

        )

        # Strong body close above the
        # previous structure, not wick.
        body_close = (
            current_close
            >
            previous_high
        )

        if (
            bos
            and displacement
            and body_close
        ):

            return {

                "confirmed":
                    True,

                "direction":
                    "LONG",

                "price":
                    current_close,

                "bos":
                    True,

                "body_ratio":
                    round(
                        body_ratio,
                        3,
                    ),

                "body_close":
                    current_close,

                "broken_level":
                    previous_high,

                "reason":
                    "15M bullish BOS body close",

                "time":
                    current.get(
                        "open_time"
                    ),

            }

    # ========================================================
    # SHORT
    # ========================================================

    if direction == "SHORT":

        bos = (
            current_close
            <
            previous_low
        )

        displacement = (

            _bearish(
                current
            )

            and

            body_ratio
            >=
            MIN_DISPLACEMENT_BODY_RATIO

        )

        body_close = (
            current_close
            <
            previous_low
        )

        if (
            bos
            and displacement
            and body_close
        ):

            return {

                "confirmed":
                    True,

                "direction":
                    "SHORT",

                "price":
                    current_close,

                "bos":
                    True,

                "body_ratio":
                    round(
                        body_ratio,
                        3,
                    ),

                "body_close":
                    current_close,

                "broken_level":
                    previous_low,

                "reason":
                    "15M bearish BOS body close",

                "time":
                    current.get(
                        "open_time"
                    ),

            }

    return None


# ============================================================
# 5M ILM
#
# LONG:
# manipulation down
# -> V recovery
# -> body close above opposing structure/FVG
#
# SHORT:
# manipulation up
# -> L recovery
# -> body close below opposing structure/FVG
# ============================================================

def detect_5m_ilm(
    candles_5m,
    direction,
    sweep=None,
    confirmation=None,
):

    if (
        not candles_5m
        or len(candles_5m) < 12
    ):
        return None

    data = candles_5m[
        -M5_LOOKBACK:
    ]

    sweep_extreme = None
    sweep_time = None

    if sweep:

        sweep_extreme = _safe_float(
            sweep.get(
                "extreme"
            )
        )

        sweep_time = sweep.get(
            "time"
        )

    confirmation_time = None

    if confirmation:

        confirmation_time = (
            confirmation.get(
                "time"
            )
        )

    # --------------------------------------------------------
    # Prefer candles after 15M confirmation.
    # --------------------------------------------------------

    if confirmation_time is not None:

        post_confirmation = [

            c

            for c in data

            if (
                c.get(
                    "open_time"
                )
                is not None

                and

                c.get(
                    "open_time"
                )
                >= confirmation_time
            )

        ]

        if len(
            post_confirmation
        ) >= 4:

            data = post_confirmation

    elif sweep_time is not None:

        post_sweep = [

            c

            for c in data

            if (
                c.get(
                    "open_time"
                )
                is not None

                and

                c.get(
                    "open_time"
                )
                >= sweep_time
            )

        ]

        if len(
            post_sweep
        ) >= 4:

            data = post_sweep

    if len(data) < 5:
        return None

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        # Last candle is the trigger.
        trigger = data[-1]

        trigger_close = _close(
            trigger
        )

        trigger_open = _open(
            trigger
        )

        if None in (
            trigger_close,
            trigger_open,
        ):
            return None

        search = data[
            :-1
        ]

        if len(search) < 4:
            return None

        # ----------------------------------------------------
        # Find the local manipulation low.
        # ----------------------------------------------------

        extreme_index = min(

            range(
                len(search)
            ),

            key=lambda i:
                _low(
                    search[i]
                )
                if _low(
                    search[i]
                )
                is not None

                else float(
                    "inf"
                ),

        )

        manipulation = search[
            extreme_index
        ]

        manipulation_low = _low(
            manipulation
        )

        if manipulation_low is None:
            return None

        # ----------------------------------------------------
        # V shape requires recovery after the low.
        # ----------------------------------------------------

        recovery_candles = search[
            extreme_index + 1:
        ]

        if not recovery_candles:
            return None

        recovery_highs = [

            _high(c)

            for c
            in recovery_candles

            if _high(c) is not None

        ]

        if not recovery_highs:
            return None

        recovery_high = max(
            recovery_highs
        )

        manipulation_size = (
            recovery_high
            -
            manipulation_low
        )

        if manipulation_size <= 0:
            return None

        manipulation_pct = (

            manipulation_size
            /
            manipulation_low
            *
            100.0

        )

        if (
            manipulation_pct
            <
            MIN_5M_MANIPULATION_PCT
        ):
            return None

        # ----------------------------------------------------
        # Recovery must be >= 1/3.
        # ----------------------------------------------------

        recovery = (
            trigger_close
            -
            manipulation_low
        )

        recovery_ratio = (
            recovery
            /
            manipulation_size
        )

        if (
            recovery_ratio
            <
            MIN_5M_RECOVERY_RATIO
        ):
            return None

        # ----------------------------------------------------
        # Sweep proximity.
        # ----------------------------------------------------

        if sweep_extreme is not None:

            distance = _pct(

                manipulation_low,

                sweep_extreme,

            )

            if (
                distance
                >
                MAX_ILM_SWEEP_DISTANCE_PCT
            ):

                return None

        body_ratio = _body_ratio(
            trigger
        )

        if (
            not _bullish(
                trigger
            )
            or
            body_ratio
            <
            MIN_DISPLACEMENT_BODY_RATIO
        ):

            return None

        # ----------------------------------------------------
        # Relevant bearish body / structure.
        # ----------------------------------------------------

        bearish_bodies = [

            c

            for c
            in search[
                max(
                    0,
                    extreme_index - 3,
                ):
            ]

            if _bearish(c)

        ]

        inversion_level = None

        if bearish_bodies:

            inversion_level = max(

                max(
                    _open(c),
                    _close(c),
                )

                for c
                in bearish_bodies

                if (
                    _open(c)
                    is not None
                    and
                    _close(c)
                    is not None
                )

            )

        if inversion_level is None:

            previous_highs = [

                _high(c)

                for c
                in search

                if _high(c) is not None

            ]

            if previous_highs:

                inversion_level = max(
                    previous_highs
                )

        if inversion_level is None:
            return None

        # ----------------------------------------------------
        # BODY close beyond inversion level.
        # ----------------------------------------------------

        if (
            trigger_close
            <=
            inversion_level
        ):

            return None

        fvg = find_fvg(
            data,
            "LONG"
        )

        fvg_ok = False

        if fvg:

            fvg_ok = (
                trigger_close
                >=
                fvg["low"]
            )

        # FVG is strengthening confirmation,
        # but body inversion is mandatory.
        return {

            "confirmed":
                True,

            "direction":
                "LONG",

            "pattern":
                "V",

            "extreme":
                manipulation_low,

            "price":
                trigger_close,

            "trigger_price":
                trigger_close,

            "manipulation_pct":
                round(
                    manipulation_pct,
                    4,
                ),

            "recovery_ratio":
                round(
                    recovery_ratio,
                    3,
                ),

            "body_ratio":
                round(
                    body_ratio,
                    3,
                ),

            "inversion_level":
                inversion_level,

            "body_close":
                trigger_close,

            "fvg":
                fvg,

            "fvg_ok":
                fvg_ok,

            "reason":
                "5M V-ILM + bullish body inversion",

            "time":
                trigger.get(
                    "open_time"
                ),

        }

    # ========================================================
    # SHORT
    # ========================================================

    if direction == "SHORT":

        trigger = data[-1]

        trigger_close = _close(
            trigger
        )

        trigger_open = _open(
            trigger
        )

        if None in (
            trigger_close,
            trigger_open,
        ):
            return None

        search = data[
            :-1
        ]

        if len(search) < 4:
            return None

        # ----------------------------------------------------
        # Find local manipulation high.
        # ----------------------------------------------------

        extreme_index = max(

            range(
                len(search)
            ),

            key=lambda i:
                _high(
                    search[i]
                )
                if _high(
                    search[i]
                )
                is not None

                else float(
                    "-inf"
                ),

        )

        manipulation = search[
            extreme_index
        ]

        manipulation_high = _high(
            manipulation
        )

        if manipulation_high is None:
            return None

        recovery_candles = search[
            extreme_index + 1:
        ]

        if not recovery_candles:
            return None

        recovery_lows = [

            _low(c)

            for c
            in recovery_candles

            if _low(c) is not None

        ]

        if not recovery_lows:
            return None

        recovery_low = min(
            recovery_lows
        )

        manipulation_size = (
            manipulation_high
            -
            recovery_low
        )

        if manipulation_size <= 0:
            return None

        manipulation_pct = (

            manipulation_size
            /
            manipulation_high
            *
            100.0

        )

        if (
            manipulation_pct
            <
            MIN_5M_MANIPULATION_PCT
        ):
            return None

        recovery = (
            manipulation_high
            -
            trigger_close
        )

        recovery_ratio = (
            recovery
            /
            manipulation_size
        )

        if (
            recovery_ratio
            <
            MIN_5M_RECOVERY_RATIO
        ):
            return None

        if sweep_extreme is not None:

            distance = _pct(

                manipulation_high,

                sweep_extreme,

            )

            if (
                distance
                >
                MAX_ILM_SWEEP_DISTANCE_PCT
            ):

                return None

        body_ratio = _body_ratio(
            trigger
        )

        if (
            not _bearish(
                trigger
            )
            or
            body_ratio
            <
            MIN_DISPLACEMENT_BODY_RATIO
        ):

            return None

        # ----------------------------------------------------
        # Relevant bullish body.
        # ----------------------------------------------------

        bullish_bodies = [

            c

            for c
            in search[
                max(
                    0,
                    extreme_index - 3,
                ):
            ]

            if _bullish(c)

        ]

        inversion_level = None

        if bullish_bodies:

            inversion_level = min(

                min(
                    _open(c),
                    _close(c),
                )

                for c
                in bullish_bodies

                if (
                    _open(c)
                    is not None
                    and
                    _close(c)
                    is not None
                )

            )

        if inversion_level is None:

            previous_lows = [

                _low(c)

                for c
                in search

                if _low(c) is not None

            ]

            if previous_lows:

                inversion_level = min(
                    previous_lows
                )

        if inversion_level is None:
            return None

        # ----------------------------------------------------
        # BODY close below inversion level.
        # ----------------------------------------------------

        if (
            trigger_close
            >=
            inversion_level
        ):

            return None

        fvg = find_fvg(
            data,
            "SHORT"
        )

        fvg_ok = False

        if fvg:

            fvg_ok = (
                trigger_close
                <=
                fvg["high"]
            )

        return {

            "confirmed":
                True,

            "direction":
                "SHORT",

            "pattern":
                "L",

            "extreme":
                manipulation_high,

            "price":
                trigger_close,

            "trigger_price":
                trigger_close,

            "manipulation_pct":
                round(
                    manipulation_pct,
                    4,
                ),

            "recovery_ratio":
                round(
                    recovery_ratio,
                    3,
                ),

            "body_ratio":
                round(
                    body_ratio,
                    3,
                ),

            "inversion_level":
                inversion_level,

            "body_close":
                trigger_close,

            "fvg":
                fvg,

            "fvg_ok":
                fvg_ok,

            "reason":
                "5M L-ILM + bearish body inversion",

            "time":
                trigger.get(
                    "open_time"
                ),

        }

    return None


# ============================================================
# ENTRY
# ============================================================

def calculate_entry(
    current_price,
    confirmation_5m=None,
):

    if confirmation_5m:

        price = _safe_float(
            confirmation_5m.get(
                "trigger_price"
            )

            if confirmation_5m.get(
                "trigger_price"
            ) is not None

            else confirmation_5m.get(
                "price"
            )

        )

        if price is not None:

            return price

    return _safe_float(
        current_price
    )


# ============================================================
# STOP LOSS
# ============================================================

def calculate_stop(
    sweep,
    direction,
    trigger_5m=None,
):

    if not sweep:
        return None

    sweep_extreme = _safe_float(
        sweep.get(
            "extreme"
        )
    )

    if sweep_extreme is None:
        return None

    # --------------------------------------------------------
    # If ILM extreme is even more extreme,
    # SL must protect beyond the actual manipulation.
    # --------------------------------------------------------

    ilm_extreme = None

    if trigger_5m:

        ilm_extreme = _safe_float(
            trigger_5m.get(
                "extreme"
            )
        )

    extreme = sweep_extreme

    if direction == "LONG":

        if (
            ilm_extreme is not None
            and
            ilm_extreme
            <
            extreme
        ):

            extreme = ilm_extreme

    elif direction == "SHORT":

        if (
            ilm_extreme is not None
            and
            ilm_extreme
            >
            extreme
        ):

            extreme = ilm_extreme

    buffer = (
        SL_BUFFER_PCT
        /
        100.0
    )

    if direction == "LONG":

        return (
            extreme
            *
            (
                1.0
                -
                buffer
            )
        )

    if direction == "SHORT":

        return (
            extreme
            *
            (
                1.0
                +
                buffer
            )
        )

    return None


# ============================================================
# TAKE PROFIT
# ============================================================

def calculate_take_profit(
    major_levels,
    entry,
    direction,
    sweep=None,
):

    excluded_price = None

    if sweep:

        excluded_price = _safe_float(
            sweep.get(
                "level"
            )
        )

    target = get_next_major_target(

        major_levels,

        entry,

        direction,

        excluded_price=excluded_price,

    )

    if not target:
        return None

    tp = _safe_float(
        target.get(
            "price"
        )
    )

    if tp is None:
        return None

    if (
        _pct(
            tp,
            entry,
        )
        <
        MIN_TP_DISTANCE_PCT
    ):

        return None

    return tp


# ============================================================
# RR
# ============================================================

def calculate_rr(
    entry,
    stop_loss,
    take_profit,
    direction,
):

    entry = _safe_float(
        entry
    )

    stop_loss = _safe_float(
        stop_loss
    )

    take_profit = _safe_float(
        take_profit
    )

    if None in (
        entry,
        stop_loss,
        take_profit,
    ):

        return 0.0

    if direction == "LONG":

        risk = (
            entry
            -
            stop_loss
        )

        reward = (
            take_profit
            -
            entry
        )

    elif direction == "SHORT":

        risk = (
            stop_loss
            -
            entry
        )

        reward = (
            entry
            -
            take_profit
        )

    else:

        return 0.0

    if risk <= 0:
        return 0.0

    if reward <= 0:
        return 0.0

    return (
        reward
        /
        risk
    )


# ============================================================
# TARGET VALIDATION
# ============================================================

def validate_target(
    major_levels,
    entry,
    take_profit,
    direction,
    sweep=None,
):

    if take_profit is None:
        return False

    excluded_price = None

    if sweep:

        excluded_price = _safe_float(
            sweep.get(
                "level"
            )
        )

    target = get_next_major_target(

        major_levels,

        entry,

        direction,

        excluded_price=excluded_price,

    )

    if not target:
        return False

    target_price = _safe_float(
        target.get(
            "price"
        )
    )

    if target_price is None:
        return False

    tolerance = max(

        target_price
        *
        0.001,

        0.000001,

    )

    return (

        abs(
            target_price
            -
            take_profit
        )
        <=
        tolerance

    )


# ============================================================
# SCORE
#
# IMPORTANT:
# Score cannot manufacture a trade.
# Mandatory stages must exist.
# ============================================================

def calculate_score(
    h1_ok,
    liquidity_ok,
    sweep_ok,
    confirmation_15m_ok,
    confirmation_5m_ok,
    target_ok,
    rr_ok,
    fvg_ok=False,
    strong_liquidity=False,
):

    score = 0

    # 1H direction.
    if h1_ok:
        score += 20

    # Major liquidity exists.
    if liquidity_ok:
        score += 15

    # Major sweep.
    if sweep_ok:
        score += 20

    # 15M.
    if confirmation_15m_ok:
        score += 15

    # 5M ILM.
    if confirmation_5m_ok:
        score += 15

    # Target.
    if target_ok:
        score += 5

    # RR.
    if rr_ok:
        score += 5

    # Optional FVG strengthening.
    if fvg_ok:
        score += 3

    # Strong repeated liquidity.
    if strong_liquidity:
        score += 2

    return min(
        score,
        100,
    )


# ============================================================
# RESULT
# ============================================================

@dataclass
class StrategyResult:

    direction: str = "NEUTRAL"

    stage: str = "WAIT"

    score: int = 0

    reason: str = ""

    liquidity: Optional[
        Dict[str, Any]
    ] = None

    sweep: Optional[
        Dict[str, Any]
    ] = None

    confirmation_15m: Optional[
        Dict[str, Any]
    ] = None

    confirmation_5m: Optional[
        Dict[str, Any]
    ] = None

    trigger_5m: Optional[
        Dict[str, Any]
    ] = None

    entry: Optional[
        float
    ] = None

    stop_loss: Optional[
        float
    ] = None

    take_profit: Optional[
        float
    ] = None

    rr: Optional[
        float
    ] = None

    liquidity_above: Optional[
        List[Dict[str, Any]]
    ] = None

    liquidity_below: Optional[
        List[Dict[str, Any]]
    ] = None

    major_liquidity_above: Optional[
        List[Dict[str, Any]]
    ] = None

    major_liquidity_below: Optional[
        List[Dict[str, Any]]
    ] = None

    htf_source: Optional[
        str
    ] = None

    h1_structure: Optional[
        Dict[str, Any]
    ] = None

    fvg_1h: Optional[
        Dict[str, Any]
    ] = None

    fvg_support: Optional[
        bool
    ] = None

    tp_reason: Optional[
        str
    ] = None

    sweep_extreme: Optional[
        float
    ] = None

    def to_dict(
        self,
    ):

        data = asdict(
            self
        )

        # ----------------------------------------------------
        # Compatibility aliases.
        # ----------------------------------------------------

        data[
            "confirmation"
        ] = self.confirmation_5m

        data[
            "trigger_5m"
        ] = self.trigger_5m

        data[
            "sl"
        ] = self.stop_loss

        data[
            "tp"
        ] = self.take_profit

        return data


# ============================================================
# MAIN ANALYSIS
# ============================================================

def analyze(
    candles_1h,
    candles_15m,
    candles_5m,
    current_price,
    major_levels,
    sweep=None,
    order_flow=None,
    candles_1m=None,
    **_legacy_kwargs,
):

    current_price = _safe_float(
        current_price
    )

    if current_price is None:

        return StrategyResult(

            reason:
                "Нет текущей цены"

        ).to_dict()

    # ========================================================
    # 1H = MAIN DIRECTION
    # ========================================================

    h1_context = get_1h_direction(
        candles_1h
    )

    direction = h1_context[
        "direction"
    ]

    h1_structure = h1_context[
        "structure"
    ]

    # ========================================================
    # LIQUIDITY SIDES
    # ========================================================

    liquidity_sides = (
        get_liquidity_sides(

            major_levels,

            current_price,

            max_levels_each_side=6,

        )
    )

    above = liquidity_sides[
        "above"
    ]

    below = liquidity_sides[
        "below"
    ]

    result = StrategyResult(

        direction=
            direction,

        htf_source=
            "1H",

        h1_structure=
            h1_structure,

        liquidity_above=
            above,

        liquidity_below=
            below,

        major_liquidity_above=
            above,

        major_liquidity_below=
            below,

    )

    # ========================================================
    # 1H NEUTRAL = NO TRADE
    # ========================================================

    if direction == "NEUTRAL":

        result.stage = (
            "WAIT"
        )

        result.score = 0

        result.reason = (

            "1H не даёт "
            "однозначного направления"

        )

        return result.to_dict()

    # ========================================================
    # VERIFY 1H
    # ========================================================

    expected_h1 = (

        "BULLISH"

        if direction == "LONG"

        else "BEARISH"

    )

    h1_ok = (

        h1_structure.get(
            "direction"
        )
        ==
        expected_h1

    )

    # This should normally be true,
    # but keep a hard safety block.
    if not h1_ok:

        result.stage = (
            "WAIT"
        )

        result.score = 20

        result.reason = (

            "1H структура "
            "не подтверждает направление"

        )

        return result.to_dict()

    # ========================================================
    # 1H FVG
    # ========================================================

    fvg_1h = find_fvg(

        candles_1h,

        direction,

    )

    result.fvg_1h = (
        fvg_1h
    )

    result.fvg_support = (

        fvg_supports_direction(

            candles_1h,

            fvg_1h,

            direction,

        )

        if fvg_1h

        else False

    )

    # ========================================================
    # DIRECTIONAL MAJOR LIQUIDITY
    # ========================================================

    if direction == "LONG":

        liquidity = (

            below[0]

            if below

            else None

        )

        wait_reason = (
            "Ждём sweep SSL"
        )

    else:

        liquidity = (

            above[0]

            if above

            else None

        )

        wait_reason = (
            "Ждём sweep BSL"
        )

    result.liquidity = (
        liquidity
    )

    liquidity_ok = (
        liquidity is not None
    )

    # ========================================================
    # NO MAJOR LIQUIDITY
    # ========================================================

    if not liquidity_ok:

        result.stage = (
            "WAIT"
        )

        result.score = (
            calculate_score(

                h1_ok=True,

                liquidity_ok=False,

                sweep_ok=False,

                confirmation_15m_ok=False,

                confirmation_5m_ok=False,

                target_ok=False,

                rr_ok=False,

                fvg_ok=bool(
                    result.fvg_support
                ),

            )
        )

        result.reason = (

            "Нет свежей major liquidity "
            "в направлении 1H сценария"

        )

        return result.to_dict()

    # ========================================================
    # WAIT FOR SWEEP
    # ========================================================

    active_sweep = sweep

    if active_sweep is None:

        result.stage = (
            "WAIT"
        )

        strong_liquidity = (

            _safe_float(
                liquidity.get(
                    "touches"
                ),
                0,
            )
            >=
            3

        )

        result.score = (
            calculate_score(

                h1_ok=True,

                liquidity_ok=True,

                sweep_ok=False,

                confirmation_15m_ok=False,

                confirmation_5m_ok=False,

                target_ok=False,

                rr_ok=False,

                fvg_ok=bool(
                    result.fvg_support
                ),

                strong_liquidity=
                    strong_liquidity,

            )
        )

        result.reason = (
            wait_reason
        )

        return result.to_dict()

    # ========================================================
    # VERIFY SWEEP DIRECTION
    # ========================================================

    sweep_direction = (
        active_sweep.get(
            "direction"
        )
    )

    if (
        sweep_direction
        !=
        direction
    ):

        result.stage = (
            "WAIT"
        )

        result.score = 40

        result.reason = (

            "Sweep не соответствует "
            "направлению 1H"

        )

        return result.to_dict()

    # ========================================================
    # VERIFY SWEEP TYPE
    # ========================================================

    expected_liquidity_type = (

        "SSL"

        if direction == "LONG"

        else "BSL"

    )

    if (
        active_sweep.get(
            "liquidity_type"
        )
        !=
        expected_liquidity_type
    ):

        result.stage = (
            "WAIT"
        )

        result.score = 40

        result.reason = (

            "Снята не та сторона "
            "major liquidity"

        )

        return result.to_dict()

    result.sweep = (
        active_sweep
    )

    result.sweep_extreme = (
        _safe_float(
            active_sweep.get(
                "extreme"
            )
        )
    )

    # ========================================================
    # 15M CONFIRMATION
    # ========================================================

    conf_15m = confirmation_15m(

        candles_15m,

        direction,

        active_sweep,

    )

    result.confirmation_15m = (
        conf_15m
    )

    if not conf_15m:

        result.stage = (
            "SWEPT"
        )

        result.score = (
            calculate_score(

                h1_ok=True,

                liquidity_ok=True,

                sweep_ok=True,

                confirmation_15m_ok=False,

                confirmation_5m_ok=False,

                target_ok=False,

                rr_ok=False,

                fvg_ok=bool(
                    result.fvg_support
                ),

            )
        )

        result.reason = (

            "Major liquidity снята. "
            "Ждём 15M confirmation"

        )

        return result.to_dict()

    # ========================================================
    # 5M ILM
    # ========================================================

    conf_5m = detect_5m_ilm(

        candles_5m,

        direction,

        active_sweep,

        conf_15m,

    )

    result.confirmation_5m = (
        conf_5m
    )

    result.trigger_5m = (
        conf_5m
    )

    if not conf_5m:

        result.stage = (
            "15M_CONFIRMED"
        )

        result.score = (
            calculate_score(

                h1_ok=True,

                liquidity_ok=True,

                sweep_ok=True,

                confirmation_15m_ok=True,

                confirmation_5m_ok=False,

                target_ok=False,

                rr_ok=False,

                fvg_ok=bool(
                    result.fvg_support
                ),

            )
        )

        result.reason = (

            "15M подтверждение есть. "
            "Ждём 5M ILM"

        )

        return result.to_dict()

    # ========================================================
    # ENTRY
    # ========================================================

    entry = calculate_entry(

        current_price,

        conf_5m,

    )

    result.entry = (
        entry
    )

    if entry is None:

        result.stage = (
            "NO_TRADE"
        )

        result.score = 75

        result.reason = (
            "Не удалось определить Entry"
        )

        return result.to_dict()

    # ========================================================
    # STOP LOSS
    # ========================================================

    stop_loss = calculate_stop(

        active_sweep,

        direction,

        conf_5m,

    )

    result.stop_loss = (
        stop_loss
    )

    if stop_loss is None:

        result.stage = (
            "NO_TRADE"
        )

        result.score = 75

        result.reason = (
            "Не удалось рассчитать SL"
        )

        return result.to_dict()

    # ========================================================
    # SL SAFETY
    # ========================================================

    if direction == "LONG":

        if entry <= stop_loss:

            result.stage = (
                "NO_TRADE"
            )

            result.score = 75

            result.reason = (
                "Entry находится "
                "ниже или на SL"
            )

            return result.to_dict()

    else:

        if entry >= stop_loss:

            result.stage = (
                "NO_TRADE"
            )

            result.score = 75

            result.reason = (
                "Entry находится "
                "выше или на SL"
            )

            return result.to_dict()

    # ========================================================
    # TP = NEXT FRESH MAJOR LIQUIDITY
    # ========================================================

    target = get_next_major_target(

        major_levels,

        entry,

        direction,

        excluded_price=(
            active_sweep.get(
                "level"
            )
        ),

    )

    if not target:

        result.stage = (
            "NO_TRADE"
        )

        result.score = 75

        result.reason = (

            "Нет следующей свежей "
            "major liquidity для TP"

        )

        return result.to_dict()

    take_profit = _safe_float(

        target.get(
            "price"
        )

    )

    result.take_profit = (
        take_profit
    )

    result.tp_reason = (

        "Следующая свежая "
        "major liquidity "
        f"({target.get('liquidity_type', 'LIQUIDITY')})"

    )

    if take_profit is None:

        result.stage = (
            "NO_TRADE"
        )

        result.score = 75

        result.reason = (
            "Некорректный TP"
        )

        return result.to_dict()

    # ========================================================
    # TP MUST BE IN TRADE DIRECTION
    # ========================================================

    if direction == "LONG":

        if take_profit <= entry:

            result.stage = (
                "NO_TRADE"
            )

            result.score = 75

            result.reason = (
                "TP находится "
                "не выше Entry"
            )

            return result.to_dict()

    else:

        if take_profit >= entry:

            result.stage = (
                "NO_TRADE"
            )

            result.score = 75

            result.reason = (
                "TP находится "
                "не ниже Entry"
            )

            return result.to_dict()

    # ========================================================
    # MIN TP DISTANCE
    # ========================================================

    tp_distance_pct = _pct(

        take_profit,

        entry,

    )

    if (
        tp_distance_pct
        <
        MIN_TP_DISTANCE_PCT
    ):

        result.stage = (
            "NO_TRADE"
        )

        result.score = 75

        result.reason = (
            "TP слишком близко"
        )

        return result.to_dict()

    # ========================================================
    # RR
    # ========================================================

    rr = calculate_rr(

        entry,

        stop_loss,

        take_profit,

        direction,

    )

    result.rr = round(
        rr,
        2,
    )

    # ========================================================
    # HARD RR FILTER
    #
    # Если следующая liquidity ближе 2R:
    # НЕ двигаем TP дальше.
    # Просто NO TRADE.
    # ========================================================

    if rr < MIN_RR:

        result.stage = (
            "NO_TRADE"
        )

        result.score = 70

        result.reason = (

            f"RR {rr:.2f} < 1:2. "
            "Вход запрещён. "
            "TP не отодвигаем искусственно."

        )

        return result.to_dict()

    # ========================================================
    # TARGET VALIDATION
    # ========================================================

    target_ok = validate_target(

        major_levels,

        entry,

        take_profit,

        direction,

        sweep=active_sweep,

    )

    if not target_ok:

        result.stage = (
            "NO_TRADE"
        )

        result.score = 75

        result.reason = (

            "TP не совпадает "
            "со следующей свежей "
            "major liquidity"

        )

        return result.to_dict()

    # ========================================================
    # FINAL SCORE
    # ========================================================

    strong_liquidity = (

        _safe_float(
            liquidity.get(
                "touches"
            ),
            0,
        )
        >=
        3

    )

    fvg_ok = bool(
        conf_5m.get(
            "fvg_ok"
        )
    ) or bool(
        result.fvg_support
    )

    score = calculate_score(

        h1_ok=True,

        liquidity_ok=True,

        sweep_ok=True,

        confirmation_15m_ok=True,

        confirmation_5m_ok=True,

        target_ok=True,

        rr_ok=True,

        fvg_ok=fvg_ok,

        strong_liquidity=
            strong_liquidity,

    )

    result.score = (
        score
    )

    # ========================================================
    # READY
    # ========================================================

    if (
        score
        >=
        MIN_SCORE_READY
    ):

        result.stage = (
            "READY"
        )

        result.reason = (

            "Полный сетап: "
            "1H → Major Liquidity "
            "→ Sweep → 15M Confirmation "
            "→ 5M ILM → Entry → SL → TP "
            f"→ RR {rr:.2f}"

        )

        return result.to_dict()

    # ========================================================
    # FINAL SAFETY
    # ========================================================

    result.stage = (
        "NO_TRADE"
    )

    result.reason = (
        "Сетап не достиг "
        "минимального score"
    )

    return result.to_dict()


# ============================================================
# EXPORTS
# ============================================================

__all__ = [

    "STRATEGY_VERSION",

    "MIN_SCORE_READY",

    "MIN_RR",

    "SL_BUFFER_PCT",

    "structure_context",

    "get_1h_direction",

    "get_higher_timeframe_direction",

    "get_directional_liquidity",

    "get_liquidity_sides",

    "get_next_major_target",

    "find_fvg",

    "fvg_supports_direction",

    "confirmation_15m",

    "detect_5m_ilm",

    "calculate_entry",

    "calculate_stop",

    "calculate_take_profit",

    "calculate_rr",

    "validate_target",

    "calculate_score",

    "StrategyResult",

    "analyze",

]