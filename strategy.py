"""
TradeMind 4.5 — Strategy Engine

CORE:
1H Context
    ↓
15M Major Liquidity
    ↓
Liquidity Sweep
    ↓
15M Confirmation
    ↓
FIRST 3 REAL 5M CANDLES
    ↓
Entry
    ↓
SL beyond sweep
    ↓
ONE TP exactly 1:2

IMPORTANT:
- No entry in the middle of a move.
- No confirmation = no entry.
- No late 5M trigger.
- Only major liquidity.
- One TP only.
- RR exactly 1:2.
- Supports both price= and current_price=.
"""

from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, List


# =========================================================
# VERSION
# =========================================================

STRATEGY_VERSION = "4.5"


# =========================================================
# SETTINGS
# =========================================================

RR_TARGET = 2.0

# Maximum distance from swept liquidity to current price.
# If exceeded, we do not chase.
MAX_ENTRY_DISTANCE_PCT = 0.50

# SL buffer beyond sweep extreme.
SL_BUFFER_PCT = 0.10

# Minimum score for READY.
MIN_SCORE_READY = 80

# Only first 3 real 5M candles after 15M confirmation.
MAX_5M_CANDLES_AFTER_CONFIRM = 3

# Sweep must not be older than this many 5M candles.
MAX_SWEEP_AGE_5M = 6

# Minimum candle body/range for displacement.
MIN_DISPLACEMENT_BODY = 0.45

# Minimum 15M body/range for strong confirmation.
MIN_15M_BODY = 0.35

# Major liquidity requirements.
MIN_MAJOR_STRENGTH = 0.60


# =========================================================
# RESULT OBJECT
# =========================================================

@dataclass
class Setup:

    status: str = "WAIT"
    stage: str = "WAIT"

    direction: Optional[str] = None

    score: int = 0
    reason: str = ""

    zone_low: Optional[float] = None
    zone_high: Optional[float] = None

    entry: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None

    rr: float = RR_TARGET
    one_tp: bool = True

    liquidity_type: Optional[str] = None

    confirmation_15m: Optional[str] = None
    confirmation: Optional[str] = None

    order_flow: Optional[str] = None

    sweep_extreme: Optional[float] = None

    context_1h: Optional[str] = None
    context_15m: Optional[str] = None

    distance_from_sweep_pct: Optional[float] = None

    opposite_liquidity: Optional[float] = None

    def to_dict(self):

        d = asdict(self)

        d["strategy_version"] = STRATEGY_VERSION

        return d


# =========================================================
# BASIC HELPERS
# =========================================================

def f(x):

    try:
        return float(x)

    except (TypeError, ValueError):

        return None


def val(c, key, idx=None):

    if isinstance(c, dict):

        value = c.get(key)

        if value is None and key:

            value = c.get(key[0])

        return f(value)

    if (
        idx is not None
        and isinstance(c, (list, tuple))
        and len(c) > idx
    ):

        return f(c[idx])

    return None


def timestamp(c):

    """
    Binance kline timestamp.

    Standard Binance list:
    [open_time, open, high, low, close, ...]
    """

    if isinstance(c, dict):

        for key in (
            "timestamp",
            "time",
            "open_time",
            "openTime",
            "ts",
        ):

            value = c.get(key)

            if value is not None:

                try:
                    return int(float(value))

                except (
                    TypeError,
                    ValueError,
                ):
                    pass

        return None

    if isinstance(c, (list, tuple)):

        if len(c) > 0:

            try:
                return int(float(c[0]))

            except (
                TypeError,
                ValueError,
            ):
                return None

    return None


def o(c):
    return val(c, "open", 1)


def h(c):
    return val(c, "high", 2)


def l(c):
    return val(c, "low", 3)


def cl(c):
    return val(c, "close", 4)


def last(candles, n):

    if not candles:

        return []

    return (
        candles[-n:]
        if len(candles) >= n
        else candles
    )


def candle_range(c):

    H = h(c)
    L = l(c)

    if H is None or L is None:

        return None

    return max(H - L, 1e-9)


def candle_body_ratio(c):

    O = o(c)
    C = cl(c)
    R = candle_range(c)

    if (
        O is None
        or C is None
        or R is None
    ):

        return None

    return abs(C - O) / R


# =========================================================
# 1H MARKET STRUCTURE
# =========================================================

def context_1h(candles):

    """
    More conservative 1H context.

    We compare recent swing structure rather than
    simply looking at the latest candle.

    Returns:
        bullish
        bearish
        neutral
    """

    d = last(candles, 32)

    if len(d) < 12:

        return "neutral"

    highs = [h(x) for x in d]
    lows = [l(x) for x in d]

    if any(
        x is None
        for x in highs + lows
    ):

        return "neutral"

    # Split into older and newer structure.
    mid = len(d) // 2

    old_high = max(highs[:mid])
    new_high = max(highs[mid:])

    old_low = min(lows[:mid])
    new_low = min(lows[mid:])

    # Bullish structure:
    # higher high + higher low
    bullish = (
        new_high > old_high
        and new_low > old_low
    )

    # Bearish structure:
    # lower high + lower low
    bearish = (
        new_high < old_high
        and new_low < old_low
    )

    if bullish and not bearish:

        return "bullish"

    if bearish and not bullish:

        return "bearish"

    return "neutral"


# =========================================================
# 15M MARKET STRUCTURE
# =========================================================

def context_15m(candles):

    d = last(candles, 12)

    if len(d) < 6:

        return "neutral"

    highs = [h(x) for x in d]
    lows = [l(x) for x in d]

    if any(
        x is None
        for x in highs + lows
    ):

        return "neutral"

    mid = len(d) // 2

    old_high = max(highs[:mid])
    new_high = max(highs[mid:])

    old_low = min(lows[:mid])
    new_low = min(lows[mid:])

    bullish = (
        new_high > old_high
        and new_low > old_low
    )

    bearish = (
        new_high < old_high
        and new_low < old_low
    )

    if bullish and not bearish:

        return "bullish"

    if bearish and not bullish:

        return "bearish"

    return "neutral"


# =========================================================
# MAJOR LIQUIDITY
# =========================================================

def normalize_major_levels(major_levels):

    """
    Normalize different possible scanner formats.

    Supported:

    {
        "price": 102.9,
        "side": "LONG",
        "strength": 0.8
    }

    or

    {
        "level": 102.9,
        "side": "SHORT"
    }
    """

    result = []

    if not major_levels:

        return result

    for zone in major_levels:

        if isinstance(zone, (int, float)):

            result.append(
                {
                    "price": float(zone),
                    "side": "",
                    "strength": 1.0,
                }
            )

            continue

        if not isinstance(zone, dict):

            continue

        price = f(
            zone.get(
                "price",
                zone.get("level"),
            )
        )

        if price is None:

            continue

        side = str(
            zone.get(
                "side",
                "",
            )
        ).upper()

        strength = f(
            zone.get(
                "strength",
                1.0,
            )
        )

        if strength is None:

            strength = 1.0

        result.append(
            {
                "price": price,
                "side": side,
                "strength": strength,
            }
        )

    return result


def nearest_major_above(
    major_levels,
    price,
):

    levels = normalize_major_levels(
        major_levels
    )

    candidates = []

    for zone in levels:

        level = zone["price"]

        if (
            level > price
            and zone["strength"]
            >= MIN_MAJOR_STRENGTH
        ):

            candidates.append(level)

    if not candidates:

        return None

    return min(candidates)


def nearest_major_below(
    major_levels,
    price,
):

    levels = normalize_major_levels(
        major_levels
    )

    candidates = []

    for zone in levels:

        level = zone["price"]

        if (
            level < price
            and zone["strength"]
            >= MIN_MAJOR_STRENGTH
        ):

            candidates.append(level)

    if not candidates:

        return None

    return max(candidates)


# =========================================================
# 15M CONFIRMATION
# =========================================================

def confirm_15m(
    candles,
    direction,
    sweep_level,
):

    """
    Mandatory confirmation after sweep.

    Returns:
        ok
        reason
        confirmation_timestamp
    """

    d = last(candles, 6)

    if (
        len(d) < 4
        or sweep_level is None
    ):

        return False, None, None

    current = d[-1]

    O = o(current)
    H = h(current)
    L = l(current)
    C = cl(current)

    if any(
        x is None
        for x in (
            O,
            H,
            L,
            C,
        )
    ):

        return False, None, None

    body_ratio = candle_body_ratio(
        current
    )

    if body_ratio is None:

        return False, None, None

    previous_highs = [
        h(x)
        for x in d[:-1]
    ]

    previous_lows = [
        l(x)
        for x in d[:-1]
    ]

    if any(
        x is None
        for x in (
            previous_highs
            + previous_lows
        )
    ):

        return False, None, None

    previous_high = max(
        previous_highs
    )

    previous_low = min(
        previous_lows
    )

    conf_ts = timestamp(current)

    # =====================================================
    # SHORT
    # =====================================================

    if direction == "SHORT":

        bearish_reclaim = (
            C < sweep_level
            and C < O
        )

        bearish_break = (
            C < previous_low
            and C < O
            and body_ratio
            >= MIN_15M_BODY
        )

        strong_rejection = (
            H > sweep_level
            and C < sweep_level
            and C
            < H
            - (
                candle_range(current)
                * 0.55
            )
        )

        if bearish_break:

            return (
                True,
                "15M bearish BOS after upside sweep",
                conf_ts,
            )

        if (
            bearish_reclaim
            and strong_rejection
        ):

            return (
                True,
                "15M bearish rejection after upside sweep",
                conf_ts,
            )

        if bearish_reclaim:

            return (
                True,
                "15M close back below swept liquidity",
                conf_ts,
            )

    # =====================================================
    # LONG
    # =====================================================

    if direction == "LONG":

        bullish_reclaim = (
            C > sweep_level
            and C > O
        )

        bullish_break = (
            C > previous_high
            and C > O
            and body_ratio
            >= MIN_15M_BODY
        )

        strong_rejection = (
            L < sweep_level
            and C > sweep_level
            and C
            > L
            + (
                candle_range(current)
                * 0.55
            )
        )

        if bullish_break:

            return (
                True,
                "15M bullish BOS after downside sweep",
                conf_ts,
            )

        if (
            bullish_reclaim
            and strong_rejection
        ):

            return (
                True,
                "15M bullish rejection after downside sweep",
                conf_ts,
            )

        if bullish_reclaim:

            return (
                True,
                "15M close back above swept liquidity",
                conf_ts,
            )

    return False, None, None


# =========================================================
# 5M TRIGGER
# =========================================================

def trigger_on_candle(
    candle,
    previous_candle,
    direction,
):

    """
    Evaluate exactly ONE real 5M candle.

    We intentionally do not search for a trigger first
    and then select the candle later.

    This prevents late triggers.
    """

    if previous_candle is None:

        return False, None

    O = o(candle)
    H = h(candle)
    L = l(candle)
    C = cl(candle)

    pH = h(previous_candle)
    pL = l(previous_candle)

    if any(
        x is None
        for x in (
            O,
            H,
            L,
            C,
            pH,
            pL,
        )
    ):

        return False, None

    R = max(
        H - L,
        1e-9,
    )

    body = abs(
        C - O
    )

    body_ratio = (
        body / R
    )

    # =====================================================
    # LONG
    # =====================================================

    if direction == "LONG":

        bullish_displacement = (
            C > O
            and C > pH
            and body_ratio
            >= MIN_DISPLACEMENT_BODY
        )

        bullish_rejection = (
            C > O
            and L < pL
            and C
            > L + R * 0.55
        )

        if bullish_displacement:

            return (
                True,
                "5M bullish displacement / micro BOS",
            )

        if bullish_rejection:

            return (
                True,
                "5M bullish rejection after liquidity sweep",
            )

    # =====================================================
    # SHORT
    # =====================================================

    if direction == "SHORT":

        bearish_displacement = (
            C < O
            and C < pL
            and body_ratio
            >= MIN_DISPLACEMENT_BODY
        )

        bearish_rejection = (
            C < O
            and H > pH
            and C
            < H - R * 0.55
        )

        if bearish_displacement:

            return (
                True,
                "5M bearish displacement / micro BOS",
            )

        if bearish_rejection:

            return (
                True,
                "5M bearish rejection after liquidity sweep",
            )

    return False, None


# =========================================================
# STRICT 5M CONFIRMATION
# =========================================================

def confirm_5m_after_15m(
    candles_5m,
    direction,
    confirmation_ts,
):

    """
    Only first 3 REAL 5M candles after 15M confirmation.

    If timestamps are missing, we reject the setup instead
    of guessing which candles belong to the confirmation window.
    """

    if (
        not candles_5m
        or confirmation_ts is None
    ):

        return False, None

    after = []

    for candle in candles_5m:

        ts = timestamp(candle)

        if (
            ts is not None
            and ts > confirmation_ts
        ):

            after.append(candle)

    if not after:

        return False, None

    # Strict first 3.
    after = after[
        :MAX_5M_CANDLES_AFTER_CONFIRM
    ]

    all_timestamps = [
        timestamp(x)
        for x in candles_5m
    ]

    for candle in after:

        ts = timestamp(candle)

        if ts is None:

            continue

        try:

            idx = all_timestamps.index(
                ts
            )

        except ValueError:

            continue

        if idx <= 0:

            continue

        previous = candles_5m[
            idx - 1
        ]

        ok, reason = trigger_on_candle(
            candle,
            previous,
            direction,
        )

        if ok:

            return True, reason

    return False, None


# =========================================================
# ORDER FLOW
# =========================================================

def flow_check(
    flow,
    direction,
):

    if not flow:

        return None, None

    absorption = str(
        flow.get(
            "absorption",
            "",
        )
    ).lower()

    delta = f(
        flow.get(
            "delta"
        )
    )

    cvd = f(
        flow.get(
            "cvd_change"
        )
    )

    if direction == "LONG":

        if absorption in {
            "buyers",
            "buyer",
            "buy",
        }:

            return (
                True,
                "buyer absorption",
            )

        if (
            delta is not None
            and delta > 0
        ):

            return (
                True,
                "positive delta",
            )

        if (
            cvd is not None
            and cvd > 0
        ):

            return (
                True,
                "rising CVD",
            )

    if direction == "SHORT":

        if absorption in {
            "sellers",
            "seller",
            "sell",
        }:

            return (
                True,
                "seller absorption",
            )

        if (
            delta is not None
            and delta < 0
        ):

            return (
                True,
                "negative delta",
            )

        if (
            cvd is not None
            and cvd < 0
        ):

            return (
                True,
                "falling CVD",
            )

    return (
        False,
        "order flow does not support direction",
    )


# =========================================================
# SWEEP EXTREME
# =========================================================

def sweep_extreme(
    candles_5m,
    direction,
    level,
):

    if (
        not candles_5m
        or level is None
    ):

        return None

    recent = last(
        candles_5m,
        12,
    )

    if direction == "SHORT":

        highs = [
            h(x)
            for x in recent
            if h(x) is not None
            and h(x) > level
        ]

        if highs:

            return max(highs)

        return level

    lows = [
        l(x)
        for x in recent
        if l(x) is not None
        and l(x) < level
    ]

    if lows:

        return min(lows)

    return level


# =========================================================
# SWEEP TIMESTAMP
# =========================================================

def get_sweep_timestamp(
    sweep,
):

    if not sweep:

        return None

    for key in (
        "timestamp",
        "time",
        "sweep_timestamp",
        "sweep_time",
        "ts",
        "open_time",
        "openTime",
    ):

        if key not in sweep:

            continue

        value = sweep.get(key)

        if value is None:

            continue

        try:

            return int(
                float(value)
            )

        except (
            TypeError,
            ValueError,
        ):

            pass

    return None


# =========================================================
# EXACT 1:2 TRADE
# =========================================================

def calculate_trade(
    direction,
    entry,
    sl,
):

    entry = f(entry)
    sl = f(sl)

    if (
        entry is None
        or sl is None
    ):

        return None, None, None

    if direction == "LONG":

        if sl >= entry:

            return (
                None,
                None,
                None,
            )

    elif direction == "SHORT":

        if sl <= entry:

            return (
                None,
                None,
                None,
            )

    else:

        return (
            None,
            None,
            None,
        )

    risk = abs(
        entry - sl
    )

    if risk <= 0:

        return (
            None,
            None,
            None,
        )

    if direction == "LONG":

        tp = (
            entry
            + RR_TARGET * risk
        )

    else:

        tp = (
            entry
            - RR_TARGET * risk
        )

    return (
        round(entry, 6),
        round(sl, 6),
        round(tp, 6),
    )


# =========================================================
# OPPOSITE LIQUIDITY
# =========================================================

def nearest_opposite_liquidity(
    major_levels,
    direction,
    entry,
):

    if not major_levels:

        return None

    levels = normalize_major_levels(
        major_levels
    )

    candidates = []

    for zone in levels:

        level = zone["price"]

        side = zone["side"]

        strength = zone["strength"]

        if (
            strength
            < MIN_MAJOR_STRENGTH
        ):

            continue

        if direction == "LONG":

            if (
                level > entry
                and side != "LONG"
            ):

                candidates.append(
                    level
                )

        elif direction == "SHORT":

            if (
                level < entry
                and side != "SHORT"
            ):

                candidates.append(
                    level
                )

    if not candidates:

        return None

    if direction == "LONG":

        return min(candidates)

    return max(candidates)


# =========================================================
# MAIN ANALYZER
# =========================================================

def analyze(
    candles_1h: List[Any],
    candles_15m: List[Any],
    candles_5m: List[Any],
    current_price: float = None,
    major_levels=None,
    order_flow: Optional[
        Dict[str, Any]
    ] = None,
    sweep: Optional[
        Dict[str, Any]
    ] = None,
    price: float = None,
):

    """
    Main TradeMind 4.5 analyzer.

    Compatibility:
        analyze(..., current_price=...)
        analyze(..., price=...)

    Both work.
    """

    # =====================================================
    # PRICE COMPATIBILITY
    # =====================================================

    if current_price is not None:

        p = f(
            current_price
        )

    else:

        p = f(
            price
        )

    result = Setup()

    if (
        p is None
        or not candles_1h
        or not candles_15m
        or not candles_5m
    ):

        result.reason = (
            "Недостаточно рыночных данных."
        )

        return result.to_dict()

    # =====================================================
    # CONTEXT
    # =====================================================

    ctx1h = context_1h(
        candles_1h
    )

    ctx15 = context_15m(
        candles_15m
    )

    result.context_1h = ctx1h
    result.context_15m = ctx15

    # =====================================================
    # SWEEP REQUIRED
    # =====================================================

    if (
        not sweep
        or not sweep.get(
            "swept"
        )
    ):

        result.score = 25

        result.stage = "WAIT"

        result.reason = (
            "Нет подтвержденного sweep "
            "крупной ликвидности. "
            "В середине диапазона не входим."
        )

        return result.to_dict()

    # =====================================================
    # SWEEP DIRECTION
    # =====================================================

    direction = str(
        sweep.get(
            "direction",
            "",
        )
    ).upper()

    level = f(
        sweep.get(
            "level",
            sweep.get(
                "price"
            ),
        )
    )

    strength = f(
        sweep.get(
            "strength"
        )
    )

    if strength is None:

        strength = 1.0

    if (
        direction not in {
            "LONG",
            "SHORT",
        }
        or level is None
    ):

        result.score = 20

        result.reason = (
            "Sweep содержит "
            "некорректное направление "
            "или уровень."
        )

        return result.to_dict()

    result.direction = direction

    result.liquidity_type = sweep.get(
        "liquidity_type",
        "major liquidity",
    )

    # =====================================================
    # MAJOR LIQUIDITY QUALITY
    # =====================================================

    if strength < MIN_MAJOR_STRENGTH:

        result.score = 35

        result.stage = "SWEEP"

        result.reason = (
            "Sweep обнаружен, "
            "но сила ликвидности "
            "недостаточна."
        )

        return result.to_dict()

    # =====================================================
    # 1H DIRECTION FILTER
    # =====================================================

    # Important:
    # We do NOT completely ban counter-trend setups,
    # but they receive a lower score and must have
    # exceptionally strong confirmation.

    context_supports_direction = (
        (
            direction == "LONG"
            and ctx1h == "bullish"
        )
        or
        (
            direction == "SHORT"
            and ctx1h == "bearish"
        )
    )

    context_opposes_direction = (
        (
            direction == "LONG"
            and ctx1h == "bearish"
        )
        or
        (
            direction == "SHORT"
            and ctx1h == "bullish"
        )
    )

    # =====================================================
    # DISTANCE FROM SWEEP
    # =====================================================

    distance_pct = (
        abs(p - level)
        / max(
            abs(level),
            1e-9,
        )
        * 100
    )

    result.distance_from_sweep_pct = (
        round(
            distance_pct,
            4,
        )
    )

    if (
        distance_pct
        > MAX_ENTRY_DISTANCE_PCT
    ):

        result.score = 35

        result.stage = "SWEEP"

        result.reason = (
            f"После sweep цена ушла "
            f"на {distance_pct:.2f}% — "
            f"больше лимита "
            f"{MAX_ENTRY_DISTANCE_PCT:.2f}%. "
            f"Не догоняем."
        )

        return result.to_dict()

    # =====================================================
    # SWEEP FRESHNESS
    # =====================================================

    sweep_ts = get_sweep_timestamp(
        sweep
    )

    latest_5m_ts = timestamp(
        candles_5m[-1]
    )

    if (
        sweep_ts is not None
        and latest_5m_ts is not None
    ):

        age_5m = max(
            0,
            (
                latest_5m_ts
                - sweep_ts
            )
            // (
                5 * 60 * 1000
            ),
        )

        if (
            age_5m
            > MAX_SWEEP_AGE_5M
        ):

            result.score = 40

            result.stage = "SWEEP"

            result.reason = (
                "Sweep слишком старый. "
                "Сетап протух."
            )

            return result.to_dict()

    # =====================================================
    # 15M CONFIRMATION
    # =====================================================

    (
        confirmation_ok,
        confirmation_reason,
        confirmation_ts,
    ) = confirm_15m(
        candles_15m,
        direction,
        level,
    )

    result.confirmation_15m = (
        confirmation_reason
    )

    if not confirmation_ok:

        result.score = 55

        result.stage = "SWEEP"

        result.reason = (
            "Sweep есть, "
            "но 15M confirmation "
            "отсутствует → "
            "вход запрещен."
        )

        return result.to_dict()

    result.stage = (
        "15M_CONFIRMED"
    )

    # =====================================================
    # STRICT FIRST 3 x 5M
    # =====================================================

    (
        trigger_ok,
        trigger_reason,
    ) = confirm_5m_after_15m(
        candles_5m,
        direction,
        confirmation_ts,
    )

    result.confirmation = (
        trigger_reason
    )

    if not trigger_ok:

        result.score = 65

        result.stage = (
            "15M_CONFIRMED"
        )

        result.reason = (
            "15M confirmation есть, "
            "но в первых 3 реальных "
            "5M свечах trigger "
            "не найден → "
            "вход запрещен."
        )

        return result.to_dict()

    # =====================================================
    # ORDER FLOW
    # =====================================================

    (
        flow_ok,
        flow_reason,
    ) = flow_check(
        order_flow,
        direction,
    )

    result.order_flow = (
        flow_reason
        if flow_reason
        else "нет данных"
    )

    # If explicit order flow exists and opposes
    # the trade, reject.
    if flow_ok is False:

        if order_flow:

            result.score = 68

            result.stage = (
                "5M_TRIGGER"
            )

            result.reason = (
                "5M trigger есть, "
                "но order flow "
                "не поддерживает "
                "направление."
            )

            return result.to_dict()

    # =====================================================
    # SWEEP EXTREME
    # =====================================================

    extreme = f(
        sweep.get(
            "extreme",
            sweep.get(
                "sweep_extreme"
            ),
        )
    )

    if extreme is None:

        extreme = sweep_extreme(
            candles_5m,
            direction,
            level,
        )

    if extreme is None:

        extreme = level

    result.sweep_extreme = extreme

    # =====================================================
    # SL
    # =====================================================

    if direction == "LONG":

        # For LONG, SL goes below sweep low.
        sl = (
            extreme
            * (
                1
                - SL_BUFFER_PCT
                / 100
            )
        )

    else:

        # For SHORT, SL goes above sweep high.
        sl = (
            extreme
            * (
                1
                + SL_BUFFER_PCT
                / 100
            )
        )

    # =====================================================
    # EXACT 1:2
    # =====================================================

    (
        entry,
        sl,
        tp,
    ) = calculate_trade(
        direction,
        p,
        sl,
    )

    if (
        entry is None
        or sl is None
        or tp is None
    ):

        result.score = 60

        result.reason = (
            "Невалидная геометрия "
            "Entry / SL."
        )

        return result.to_dict()

    # =====================================================
    # OPPOSING MAJOR LIQUIDITY
    # =====================================================

    opposite = (
        nearest_opposite_liquidity(
            major_levels,
            direction,
            entry,
        )
    )

    result.opposite_liquidity = (
        opposite
    )

    if opposite is not None:

        if direction == "LONG":

            if tp >= opposite:

                result.score = 70

                result.stage = (
                    "5M_TRIGGER"
                )

                result.reason = (
                    "Следующая крупная "
                    "ликвидность находится "
                    "до нормального TP 1:2. "
                    "RR 1:2 не имеет "
                    "достаточного пространства "
                    "→ NO TRADE."
                )

                return result.to_dict()

        if direction == "SHORT":

            if tp <= opposite:

                result.score = 70

                result.stage = (
                    "5M_TRIGGER"
                )

                result.reason = (
                    "Следующая крупная "
                    "ликвидность находится "
                    "до нормального TP 1:2. "
                    "RR 1:2 не имеет "
                    "достаточного пространства "
                    "→ NO TRADE."
                )

                return result.to_dict()

    # =====================================================
    # ENTRY QUALITY
    # =====================================================

    entry_distance = (
        abs(entry - level)
        / max(
            abs(level),
            1e-9,
        )
        * 100
    )

    # If entry is still close to liquidity,
    # quality is good.
    entry_quality_good = (
        entry_distance
        <= 0.25
    )

    # =====================================================
    # SCORE 2.0
    # =====================================================

    score = 80

    # 1H context.
    if context_supports_direction:

        score += 7

    elif context_opposes_direction:

        score -= 8

    # 15M context.
    if (
        ctx15
        == direction.lower()
    ):

        score += 5

    # Order flow.
    if flow_ok is True:

        score += 8

    # Entry quality.
    if entry_quality_good:

        score += 3

    # Strong sweep.
    if strength >= 0.80:

        score += 2

    score = max(
        0,
        min(
            score,
            100,
        ),
    )

    # =====================================================
    # MANDATORY 1H FILTER FOR WEAK COUNTER-TREND
    # =====================================================

    if context_opposes_direction:

        # Counter-trend is allowed only if score
        # remains very strong after the penalty.
        if score < 85:

            result.score = score

            result.stage = (
                "WAIT"
            )

            result.reason = (
                "Sweep/confirmation есть, "
                "но 1H направлен против "
                "сделки и качество недостаточно "
                "высокое → NO TRADE."
            )

            return result.to_dict()

    # =====================================================
    # MINIMUM SCORE
    # =====================================================

    if score < MIN_SCORE_READY:

        result.score = score

        result.stage = (
            "WAIT"
        )

        result.reason = (
            f"Score {score} ниже "
            f"минимального "
            f"{MIN_SCORE_READY} "
            f"→ NO TRADE."
        )

        return result.to_dict()

    # =====================================================
    # READY
    # =====================================================

    result.status = "READY"

    result.stage = "READY"

    result.score = score

    result.entry = entry

    result.sl = sl

    result.tp = tp

    result.rr = RR_TARGET

    result.one_tp = True

    result.reason = (
        "1H context → "
        "Major Liquidity → "
        "Sweep → "
        "15M confirmation → "
        "first 3 real 5M candles → "
        "Entry → "
        "SL beyond sweep → "
        "ONE TP exactly 1:2."
    )

    return result.to_dict()


# =========================================================
# SOL WRAPPER
# =========================================================

def analyze_sol(
    candles_1h,
    candles_15m,
    candles_5m,
    current_price=None,
    order_flow=None,
    sweep=None,
    major_levels=None,
    price=None,
):

    return analyze(
        candles_1h,
        candles_15m,
        candles_5m,
        current_price=current_price,
        major_levels=major_levels,
        order_flow=order_flow,
        sweep=sweep,
        price=price,
    )


# =========================================================
# OPTIONAL ALIASES
# =========================================================

def analyze_symbol(
    candles_1h,
    candles_15m,
    candles_5m,
    current_price=None,
    major_levels=None,
    order_flow=None,
    sweep=None,
    price=None,
):

    """
    Generic compatibility wrapper for multi-coin scanner.
    """

    return analyze(
        candles_1h,
        candles_15m,
        candles_5m,
        current_price=current_price,
        major_levels=major_levels,
        order_flow=order_flow,
        sweep=sweep,
        price=price,
    )