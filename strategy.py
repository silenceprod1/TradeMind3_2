"""
TradeMind 5.8 — Strategy Engine

FLOW:
D1/W1
  -> 1H trend
  -> Major liquidity
  -> 1H liquidity sweep
  -> 15M confirmation
  -> 5M ILM
  -> Entry
  -> SL behind sweep
  -> EXACT 1:2 TP

Core rules:
- D1 defines direction.
- W1 is fallback when D1 is neutral/unavailable.
- D1 and 1H must agree.
- LONG only after downside liquidity sweep.
- SHORT only after upside liquidity sweep.
- 15M confirmation is mandatory.
- 5M ILM is mandatory.
- Recovery >= 1/3 of manipulation.
- Body close matters; wick alone is not enough.
- TP is exactly 1:2.
- One TP only.
- No chasing.
- No trade in the middle.
"""

from dataclasses import dataclass, asdict
from typing import Optional
import math


STRATEGY_VERSION = "5.8"

MIN_SCORE_READY = 80

# Anti-chase
MAX_ENTRY_DISTANCE_PCT = 0.75

# ILM
MIN_RECOVERY_RATIO = 1.0 / 3.0
MIN_REVERSAL_BODY_RATIO = 0.35

# Sweep
MAX_SWEEP_AGE_1H = 3
MIN_SWEEP_DEPTH_PCT = 0.08

# 15M
MAX_15M_CONFIRM_AGE = 8
MIN_15M_BODY_RATIO = 0.35

# 5M
MAX_5M_TRIGGER_AGE = 3
MIN_5M_BODY_RATIO = 0.40

# Structure
STRUCTURE_LOOKBACK = 40

# SL
SL_BUFFER_PCT = 0.10

# TP
REQUIRED_RR = 2.0
MIN_TP_DISTANCE_PCT = 0.10


@dataclass
class Setup:

    status: str = "WAIT"
    stage: str = "WAIT"
    direction: Optional[str] = None
    score: int = 0
    reason: str = ""

    entry: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    rr: Optional[float] = None

    one_tp: bool = True

    liquidity_type: Optional[str] = None
    sweep_level: Optional[float] = None
    sweep_extreme: Optional[float] = None

    confirmation_15m: Optional[str] = None
    confirmation: Optional[str] = None
    order_flow: Optional[str] = None

    structural_target: Optional[float] = None

    d1_context: Optional[str] = None
    w1_context: Optional[str] = None
    h1_context: Optional[str] = None

    imbalance_context: Optional[str] = None

    manipulation_pct: Optional[float] = None
    recovery_pct: Optional[float] = None
    recovery_ratio: Optional[float] = None

    def to_dict(self):
        return asdict(self)


def f(value):

    try:

        x = float(value)

        return (
            x
            if math.isfinite(x)
            else None
        )

    except Exception:

        return None


def val(candle, key):

    if not isinstance(candle, dict):
        return None

    aliases = {

        "open": (
            "open",
            "o",
        ),

        "high": (
            "high",
            "h",
        ),

        "low": (
            "low",
            "l",
        ),

        "close": (
            "close",
            "c",
        ),

        "volume": (
            "volume",
            "v",
        ),

        "time": (
            "open_time",
            "time",
            "timestamp",
            "ts",
            "openTime",
        ),
    }

    for name in aliases.get(
        key,
        (key,),
    ):

        if name in candle:

            if key == "time":
                return candle[name]

            return f(
                candle[name]
            )

    return None


def o(c):
    return val(
        c,
        "open",
    )


def h(c):
    return val(
        c,
        "high",
    )


def l(c):
    return val(
        c,
        "low",
    )


def cl(c):
    return val(
        c,
        "close",
    )


def candle_time(c):
    return val(
        c,
        "time",
    )


def last(candles, n):

    if not candles:
        return []

    return candles[-n:]


def body_ratio(c):

    oo = o(c)
    hh = h(c)
    ll = l(c)
    cc = cl(c)

    if None in (
        oo,
        hh,
        ll,
        cc,
    ):
        return 0.0

    rng = hh - ll

    if rng <= 0:
        return 0.0

    return abs(
        cc - oo
    ) / rng


def bullish(c):

    oo = o(c)
    cc = cl(c)

    return (
        oo is not None
        and cc is not None
        and cc > oo
    )


def bearish(c):

    oo = o(c)
    cc = cl(c)

    return (
        oo is not None
        and cc is not None
        and cc < oo
    )


def pct_distance(
    a,
    b,
):

    if (
        a is None
        or b in (
            None,
            0,
        )
    ):
        return None

    return (
        abs(a - b)
        / abs(b)
        * 100.0
    )


# =========================================================
# MARKET STRUCTURE
# =========================================================

def swing_highs(
    candles,
    radius=2,
):

    result = []

    if len(candles) < (
        radius * 2 + 1
    ):
        return result

    for i in range(
        radius,
        len(candles) - radius,
    ):

        value = h(
            candles[i]
        )

        if value is None:
            continue

        neighbors = []

        for j in range(
            i - radius,
            i + radius + 1,
        ):

            if j == i:
                continue

            v = h(
                candles[j]
            )

            if v is not None:
                neighbors.append(v)

        if len(neighbors) == (
            radius * 2
        ):

            if value >= max(
                neighbors
            ):

                result.append(
                    (
                        i,
                        value,
                    )
                )

    return result


def swing_lows(
    candles,
    radius=2,
):

    result = []

    if len(candles) < (
        radius * 2 + 1
    ):
        return result

    for i in range(
        radius,
        len(candles) - radius,
    ):

        value = l(
            candles[i]
        )

        if value is None:
            continue

        neighbors = []

        for j in range(
            i - radius,
            i + radius + 1,
        ):

            if j == i:
                continue

            v = l(
                candles[j]
            )

            if v is not None:
                neighbors.append(v)

        if len(neighbors) == (
            radius * 2
        ):

            if value <= min(
                neighbors
            ):

                result.append(
                    (
                        i,
                        value,
                    )
                )

    return result


def structure_context(
    candles,
):

    data = last(
        candles,
        STRUCTURE_LOOKBACK,
    )

    highs = swing_highs(
        data,
        2,
    )

    lows = swing_lows(
        data,
        2,
    )

    if (
        len(highs) < 2
        or len(lows) < 2
    ):

        return "neutral"

    previous_high = highs[-2][1]
    latest_high = highs[-1][1]

    previous_low = lows[-2][1]
    latest_low = lows[-1][1]

    if (
        latest_high > previous_high
        and latest_low > previous_low
    ):

        return "bullish"

    if (
        latest_high < previous_high
        and latest_low < previous_low
    ):

        return "bearish"

    return "neutral"


def context_d1(candles):

    return (
        structure_context(candles)
        if candles
        else "neutral"
    )


def context_w1(candles):

    return (
        structure_context(candles)
        if candles
        else "neutral"
    )


def context_1h(candles):

    return (
        structure_context(candles)
        if candles
        else "neutral"
    )


# =========================================================
# 1H IMBALANCE
# =========================================================

def find_fvgs(
    candles,
):

    result = []

    data = last(
        candles,
        40,
    )

    if len(data) < 3:
        return result

    for i in range(
        2,
        len(data),
    ):

        a = data[i - 2]
        c = data[i]

        a_high = h(a)
        a_low = l(a)

        c_high = h(c)
        c_low = l(c)

        if None in (
            a_high,
            a_low,
            c_high,
            c_low,
        ):
            continue

        # Bullish FVG
        if c_low > a_high:

            result.append({
                "direction": "bullish",
                "low": a_high,
                "high": c_low,
                "index": i,
            })

        # Bearish FVG
        if c_high < a_low:

            result.append({
                "direction": "bearish",
                "low": c_high,
                "high": a_low,
                "index": i,
            })

    return result


def imbalance_context(
    candles,
    direction,
):

    fvgs = find_fvgs(
        candles
    )

    if not fvgs:
        return "none"

    wanted = (
        "bullish"
        if direction == "LONG"
        else "bearish"
    )

    opposite = (
        "bearish"
        if direction == "LONG"
        else "bullish"
    )

    recent = fvgs[-6:]

    if any(
        x["direction"] == wanted
        for x in recent
    ):

        return "respected"

    if any(
        x["direction"] == opposite
        for x in recent
    ):

        return "caution"

    return "none"


# =========================================================
# 15M CONFIRMATION
# =========================================================

def confirm_15m(
    candles_15m,
    direction,
    sweep,
):

    if (
        not candles_15m
        or not sweep
    ):

        return (
            False,
            "Нет 15M данных.",
            None,
        )

    level = f(
        sweep.get("level")
    )

    if level is None:

        return (
            False,
            "Sweep level отсутствует.",
            None,
        )

    sweep_time = sweep.get(
        "time"
    )

    candidates = []

    for candle in candles_15m:

        ct = candle_time(
            candle
        )

        if sweep_time is None:

            candidates.append(
                candle
            )

            continue

        try:

            if float(ct) >= float(
                sweep_time
            ):

                candidates.append(
                    candle
                )

        except Exception:

            candidates.append(
                candle
            )

    candidates = last(
        candidates,
        MAX_15M_CONFIRM_AGE,
    )

    if len(candidates) < 2:

        return (
            False,
            "После sweep ещё недостаточно 15M свечей.",
            None,
        )

    for candle in candidates:

        close = cl(candle)

        if close is None:
            continue

        ratio = body_ratio(
            candle
        )

        if ratio < MIN_15M_BODY_RATIO:
            continue

        if direction == "LONG":

            if (
                bullish(candle)
                and close > level
            ):

                return (
                    True,
                    "15M bullish body close выше sweep level.",
                    candle,
                )

        if direction == "SHORT":

            if (
                bearish(candle)
                and close < level
            ):

                return (
                    True,
                    "15M bearish body close ниже sweep level.",
                    candle,
                )

    return (
        False,
        "Sweep есть, но 15M confirmation отсутствует.",
        None,
    )


# =========================================================
# 5M ILM
# =========================================================

def calculate_recovery(
    candles_5m,
    direction,
    sweep_level,
    sweep_extreme,
):

    if None in (
        sweep_level,
        sweep_extreme,
    ):

        return (
            None,
            None,
            None,
        )

    manipulation = abs(
        sweep_extreme
        - sweep_level
    )

    if manipulation <= 0:

        return (
            None,
            None,
            None,
        )

    data = last(
        candles_5m,
        12,
    )

    if direction == "LONG":

        closes = [
            cl(x)
            for x in data
            if cl(x) is not None
        ]

        if not closes:

            return (
                manipulation,
                None,
                None,
            )

        recovery = max(
            0.0,
            max(closes)
            - sweep_extreme,
        )

        return (
            manipulation,
            recovery,
            recovery / manipulation,
        )

    highs = [
        h(x)
        for x in data
        if h(x) is not None
    ]

    if not highs:

        return (
            manipulation,
            None,
            None,
        )

    recovery = max(
        0.0,
        sweep_extreme
        - min(highs),
    )

    return (
        manipulation,
        recovery,
        recovery / manipulation,
    )


def confirm_5m_ilm(
    candles_5m,
    direction,
    sweep,
):

    if (
        not candles_5m
        or not sweep
    ):

        return (
            False,
            "Нет 5M данных.",
            None,
            None,
            None,
        )

    level = f(
        sweep.get("level")
    )

    extreme = f(
        sweep.get("extreme")
    )

    if (
        level is None
        or extreme is None
    ):

        return (
            False,
            "Sweep extreme отсутствует.",
            None,
            None,
            None,
        )

    manipulation = abs(
        extreme - level
    )

    if manipulation <= 0:

        return (
            False,
            "Нет выраженной manipulation.",
            None,
            None,
            None,
        )

    ratio = None
    recovery = None

    data = last(
        candles_5m,
        12,
    )

    # =====================================================
    # LONG = V REVERSAL
    # =====================================================

    if direction == "LONG":

        lowest_index = None
        lowest = None

        for i, candle in enumerate(
            data
        ):

            value = l(candle)

            if value is None:
                continue

            if value < level:

                if (
                    lowest is None
                    or value < lowest
                ):

                    lowest = value
                    lowest_index = i

        if lowest is None:

            return (
                False,
                "5M не показал снятие ликвидности снизу.",
                None,
                None,
                None,
            )

        post = data[
            lowest_index:
        ]

        closes = [
            cl(x)
            for x in post
            if cl(x) is not None
        ]

        if not closes:

            return (
                False,
                "Нет recovery.",
                None,
                None,
                None,
            )

        recovery = (
            max(closes)
            - lowest
        )

        ratio = (
            recovery
            / manipulation
        )

        if ratio < MIN_RECOVERY_RATIO:

            return (
                False,
                f"V-recovery слабый: {ratio:.2f} < 0.33.",
                manipulation,
                recovery,
                ratio,
            )

        for candle in post[
            -MAX_5M_TRIGGER_AGE:
        ]:

            close = cl(candle)

            if (
                bullish(candle)
                and close is not None
                and close > level
                and body_ratio(candle)
                >= MIN_5M_BODY_RATIO
            ):

                return (
                    True,
                    "5M ILM LONG: V-reversal + recovery + bullish body close.",
                    manipulation,
                    recovery,
                    ratio,
                )

        return (
            False,
            "5M V есть, но нет bullish ILM body close.",
            manipulation,
            recovery,
            ratio,
        )

    # =====================================================
    # SHORT = L REVERSAL
    # =====================================================

    highest_index = None
    highest = None

    for i, candle in enumerate(
        data
    ):

        value = h(candle)

        if value is None:
            continue

        if value > level:

            if (
                highest is None
                or value > highest
            ):

                highest = value
                highest_index = i

    if highest is None:

        return (
            False,
            "5M не показал снятие ликвидности сверху.",
            None,
            None,
            None,
        )

    post = data[
        highest_index:
    ]

    closes = [
        cl(x)
        for x in post
        if cl(x) is not None
    ]

    if not closes:

        return (
            False,
            "Нет recovery.",
            None,
            None,
            None,
        )

    recovery = (
        highest
        - min(closes)
    )

    ratio = (
        recovery
        / manipulation
    )

    if ratio < MIN_RECOVERY_RATIO:

        return (
            False,
            f"L-recovery слабый: {ratio:.2f} < 0.33.",
            manipulation,
            recovery,
            ratio,
        )

    for candle in post[
        -MAX_5M_TRIGGER_AGE:
    ]:

        close = cl(candle)

        if (
            bearish(candle)
            and close is not None
            and close < level
            and body_ratio(candle)
            >= MIN_5M_BODY_RATIO
        ):

            return (
                True,
                "5M ILM SHORT: L-reversal + recovery + bearish body close.",
                manipulation,
                recovery,
                ratio,
            )

    return (
        False,
        "5M L есть, но нет bearish ILM body close.",
        manipulation,
        recovery,
        ratio,
    )


# =========================================================
# STRUCTURAL TARGET
# =========================================================

def find_structural_target(
    candles_1h,
    direction,
    entry,
):

    data = last(
        candles_1h,
        STRUCTURE_LOOKBACK,
    )

    if direction == "LONG":

        highs = swing_highs(
            data,
            2,
        )

        candidates = [
            value
            for _, value in highs
            if value > entry
        ]

        if not candidates:
            return None

        return min(
            candidates
        )

    lows = swing_lows(
        data,
        2,
    )

    candidates = [
        value
        for _, value in lows
        if value < entry
    ]

    if not candidates:
        return None

    return max(
        candidates
    )


# =========================================================
# TRADE GEOMETRY
# =========================================================

def build_trade(
    direction,
    entry,
    sweep_extreme,
    structural_target,
):

    entry = f(entry)
    extreme = f(sweep_extreme)
    target = f(structural_target)

    if None in (
        entry,
        extreme,
        target,
    ):

        return None

    if direction == "LONG":

        sl = extreme * (
            1.0
            - SL_BUFFER_PCT / 100.0
        )

        if not (
            sl < entry < target
        ):

            return None

        risk = (
            entry - sl
        )

        tp = (
            entry
            + risk * REQUIRED_RR
        )

        if tp > target:
            return None

    else:

        sl = extreme * (
            1.0
            + SL_BUFFER_PCT / 100.0
        )

        if not (
            target < entry < sl
        ):

            return None

        risk = (
            sl - entry
        )

        tp = (
            entry
            - risk * REQUIRED_RR
        )

        if tp < target:
            return None

    if risk <= 0:
        return None

    rr = (
        abs(tp - entry)
        / risk
    )

    return {
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

        "rr": round(
            rr,
            2,
        ),
    }


# =========================================================
# DIRECTION
# =========================================================

def direction_from_context(
    d1,
    h1,
):

    if (
        d1 == "bullish"
        and h1 == "bullish"
    ):

        return "LONG"

    if (
        d1 == "bearish"
        and h1 == "bearish"
    ):

        return "SHORT"

    return None


# =========================================================
# MAIN ANALYSIS
# =========================================================

def analyze(
    candles_1h,
    candles_15m=None,
    candles_5m=None,
    current_price=None,
    major_levels=None,
    order_flow=None,
    sweep=None,
    candles_d1=None,
    candles_w1=None,
    price=None,
):

    p = f(
        current_price
        if current_price is not None
        else price
    )

    result = Setup()

    if (
        p is None
        or not candles_d1
        or not candles_1h
        or not candles_15m
        or not candles_5m
    ):

        result.reason = (
            "Недостаточно рыночных данных."
        )

        return result.to_dict()

    # =====================================================
    # 1. D1 / W1
    # =====================================================

    d1 = context_d1(
        candles_d1
    )

    w1 = context_w1(
        candles_w1
    )

    result.d1_context = d1
    result.w1_context = w1

    if d1 == "neutral":

        if w1 in {
            "bullish",
            "bearish",
        }:

            d1 = w1

            result.d1_context = (
                f"fallback:{w1}"
            )

        else:

            result.stage = "D1"

            result.score = 20

            result.reason = (
                "D1/W1 не дают ясного направления → NO TRADE."
            )

            return result.to_dict()

    # =====================================================
    # 2. 1H SYNCHRONIZATION
    # =====================================================

    h1 = context_1h(
        candles_1h
    )

    result.h1_context = h1

    direction = direction_from_context(
        d1,
        h1,
    )

    if direction is None:

        result.stage = "1H"

        result.score = 35

        result.reason = (
            f"D1={d1}, 1H={h1} → "
            "тренды не синхронизированы."
        )

        return result.to_dict()

    result.direction = direction

    # =====================================================
    # 3. MAJOR SWEEP
    # =====================================================

    if (
        not sweep
        or not sweep.get("swept")
    ):

        result.stage = "SWEEP"

        result.score = 45

        result.reason = (
            f"D1 + 1H подтверждают {direction}, "
            "но major liquidity sweep отсутствует."
        )

        return result.to_dict()

    sweep_direction = str(
        sweep.get(
            "direction",
            "",
        )
    ).upper()

    if sweep_direction != direction:

        result.stage = "SWEEP"

        result.score = 40

        result.reason = (
            "Sweep произошёл не в направлении D1/1H."
        )

        return result.to_dict()

    level = f(
        sweep.get("level")
    )

    extreme = f(
        sweep.get("extreme")
    )

    if (
        level is None
        or extreme is None
    ):

        result.stage = "SWEEP"

        result.score = 40

        result.reason = (
            "Sweep не содержит корректных level/extreme."
        )

        return result.to_dict()

    result.sweep_level = level

    result.sweep_extreme = extreme

    result.liquidity_type = sweep.get(
        "liquidity_type",
        "major liquidity",
    )

    # =====================================================
    # 4. ANTI CHASE
    # =====================================================

    distance = pct_distance(
        p,
        level,
    )

    if (
        distance is None
        or distance > MAX_ENTRY_DISTANCE_PCT
    ):

        result.stage = "5M"

        result.score = 55

        result.reason = (
            f"После sweep цена слишком далеко "
            f"({distance:.2f}%). Не догоняем."
        )

        return result.to_dict()

    # =====================================================
    # 5. 15M CONFIRMATION — MANDATORY
    # =====================================================

    (
        ok15,
        conf15,
        candle15,
    ) = confirm_15m(
        candles_15m,
        direction,
        sweep,
    )

    result.confirmation_15m = conf15

    if not ok15:

        result.stage = "15M"

        result.score = 65

        result.reason = (
            "Sweep есть, но обязательного "
            "15M confirmation ещё нет."
        )

        return result.to_dict()

    # =====================================================
    # 6. 5M ILM — MANDATORY
    # =====================================================

    (
        ok5,
        conf5,
        manipulation,
        recovery,
        recovery_ratio,
    ) = confirm_5m_ilm(
        candles_5m,
        direction,
        sweep,
    )

    result.confirmation = conf5

    result.recovery_ratio = (
        recovery_ratio
    )

    if manipulation:

        result.manipulation_pct = (
            manipulation
            / level
            * 100.0
        )

    if recovery:

        result.recovery_pct = (
            recovery
            / level
            * 100.0
        )

    if not ok5:

        result.stage = "5M"

        result.score = 72

        result.reason = (
            "15M подтверждение есть, "
            "но полноценного 5M ILM ещё нет."
        )

        return result.to_dict()

    # =====================================================
    # 7. IMBALANCE CONTEXT
    # =====================================================

    result.imbalance_context = (
        imbalance_context(
            candles_1h,
            direction,
        )
    )

    # =====================================================
    # 8. STRUCTURAL TARGET
    # =====================================================

    structural_target = (
        find_structural_target(
            candles_1h,
            direction,
            p,
        )
    )

    if structural_target is None:

        result.stage = "TP"

        result.score = 70

        result.reason = (
            "Нет валидного структурного target."
        )

        return result.to_dict()

    result.structural_target = (
        structural_target
    )

    # =====================================================
    # 9. EXACT 1:2
    # =====================================================

    trade = build_trade(
        direction,
        p,
        extreme,
        structural_target,
    )

    if not trade:

        result.stage = "TP"

        result.score = 70

        result.reason = (
            "Структурная цель не позволяет получить "
            "чистый RR 1:2 без нарушения системы."
        )

        return result.to_dict()

    tp_distance = pct_distance(
        trade["tp"],
        p,
    )

    if (
        tp_distance is None
        or tp_distance < MIN_TP_DISTANCE_PCT
    ):

        result.stage = "TP"

        result.score = 70

        result.reason = (
            "TP 1:2 слишком близко к Entry."
        )

        return result.to_dict()

    result.entry = trade[
        "entry"
    ]

    result.sl = trade[
        "sl"
    ]

    result.tp = trade[
        "tp"
    ]

    result.rr = trade[
        "rr"
    ]

    result.one_tp = True

    # =====================================================
    # 10. SCORE
    # =====================================================

    score = 80

    if (
        result.imbalance_context
        == "respected"
    ):

        score += 8

    elif (
        result.imbalance_context
        == "caution"
    ):

        score -= 5

    strength = f(
        sweep.get("strength")
    )

    if strength is not None:

        if strength >= 0.90:

            score += 7

        elif strength >= 0.80:

            score += 5

        elif strength < 0.70:

            score -= 4

    if (
        recovery_ratio is not None
        and recovery_ratio >= 0.50
    ):

        score += 5

    elif (
        recovery_ratio is not None
        and recovery_ratio
        >= 1.0 / 3.0
    ):

        score += 2

    # =====================================================
    # OPTIONAL ORDER FLOW
    # =====================================================

    if order_flow:

        absorption = str(
            order_flow.get(
                "absorption",
                "",
            )
        ).lower()

        delta = f(
            order_flow.get(
                "delta"
            )
        )

        if direction == "LONG":

            if absorption in {
                "buyer",
                "buyers",
                "buy",
            }:

                score += 5

            elif (
                delta is not None
                and delta > 0
            ):

                score += 5

        else:

            if absorption in {
                "seller",
                "sellers",
                "sell",
            }:

                score += 5

            elif (
                delta is not None
                and delta < 0
            ):

                score += 5

    score = max(
        0,
        min(
            int(score),
            100,
        ),
    )

    result.score = score

    if score < MIN_SCORE_READY:

        result.stage = "WAIT"

        result.reason = (
            "Setup сформирован, но score ниже 80."
        )

        return result.to_dict()

    # =====================================================
    # READY
    # =====================================================

    result.status = "READY"

    result.stage = "READY"

    result.reason = (
        f"D1 {d1} → 1H {h1} → "
        "Major Liquidity Sweep → "
        "15M Confirmation → "
        "5M ILM → "
        "Entry → SL → EXACT 1:2 TP."
    )

    return result.to_dict()


# =========================================================
# COMPATIBILITY
# =========================================================

def analyze_sol(
    **kwargs
):

    return analyze(
        **kwargs
    )


def analyze_symbol(
    symbol,
    **kwargs,
):

    return analyze(
        **kwargs
    )


# =========================================================
# EXPORTS
# =========================================================

__all__ = [
    "STRATEGY_VERSION",
    "MIN_SCORE_READY",
    "Setup",
    "analyze",
    "analyze_sol",
    "analyze_symbol",
]