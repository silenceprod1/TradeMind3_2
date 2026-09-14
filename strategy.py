"""
TradeMind 5.0
D1 -> 1H -> Major Liquidity Sweep -> 5M V/L -> Entry -> SL -> Structural TP

TP НЕ фиксированный 1:2.
LONG  = структурный HIGH
SHORT = структурный LOW

Rules:
- D1 and 1H must agree.
- No major sweep = no trade.
- Sweep alone = no trade.
- 5M reversal is mandatory.
- Recovery must be >= 1/3 of manipulation.
- No chasing.
- SL behind sweep extreme.
- One TP only.
"""

from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, List
import math


STRATEGY_VERSION = "5.0"

MIN_SCORE_READY = 80

MAX_ENTRY_DISTANCE_PCT = 0.75
MIN_RECOVERY_RATIO = 1.0 / 3.0

SL_BUFFER_PCT = 0.10

MIN_TP_DISTANCE_PCT = 0.15
MAX_TP_DISTANCE_PCT = 8.0

STRUCTURE_LOOKBACK = 40
SWEEP_LOOKBACK = 36


# ============================================================
# SETUP
# ============================================================

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
    rr: Optional[float] = None

    one_tp: bool = True

    liquidity_type: Optional[str] = None

    confirmation_15m: Optional[str] = None
    confirmation: Optional[str] = None

    order_flow: Optional[str] = None

    sweep_extreme: Optional[float] = None

    structural_target: Optional[float] = None

    d1_context: Optional[str] = None
    w1_context: Optional[str] = None
    h1_context: Optional[str] = None

    imbalance_context: Optional[str] = None

    manipulation_pct: Optional[float] = None
    recovery_pct: Optional[float] = None

    def to_dict(self):
        return asdict(self)


# ============================================================
# HELPERS
# ============================================================

def f(x):
    try:
        x = float(x)
        if not math.isfinite(x):
            return None
        return x
    except (TypeError, ValueError):
        return None


def val(c, key, idx=None):
    if isinstance(c, dict):

        aliases = {
            "open": ["open", "o"],
            "high": ["high", "h"],
            "low": ["low", "l"],
            "close": ["close", "c"],
            "time": [
                "time",
                "timestamp",
                "ts",
                "open_time",
                "openTime"
            ]
        }

        for k in aliases.get(key, [key]):
            if k in c:
                if key == "time":
                    return c[k]
                return f(c[k])

        return None

    if (
        idx is not None
        and isinstance(c, (list, tuple))
        and len(c) > idx
    ):
        if key == "time":
            return c[idx]
        return f(c[idx])

    return None


def o(c):
    return val(c, "open", 1)


def h(c):
    return val(c, "high", 2)


def l(c):
    return val(c, "low", 3)


def cl(c):
    return val(c, "close", 4)


def candle_time(c):
    return val(c, "time", 0)


def last(candles, n):
    if not candles:
        return []
    return candles[-n:]


def candle_direction(c):
    op = o(c)
    close = cl(c)

    if op is None or close is None:
        return "neutral"

    if close > op:
        return "bullish"

    if close < op:
        return "bearish"

    return "neutral"


def candle_range(c):
    high = h(c)
    low = l(c)

    if high is None or low is None:
        return 0.0

    return max(high - low, 0.0)


def candle_body_ratio(c):
    rng = candle_range(c)

    if rng <= 0:
        return 0.0

    op = o(c)
    close = cl(c)

    if op is None or close is None:
        return 0.0

    return abs(close - op) / rng


def distance_pct(a, b):
    if a is None or b in (None, 0):
        return None

    return abs(a - b) / abs(b) * 100.0


# ============================================================
# SWING STRUCTURE
# ============================================================

def swing_highs(candles, radius=2):

    result = []

    if len(candles) < radius * 2 + 1:
        return result

    for i in range(
        radius,
        len(candles) - radius
    ):

        value = h(candles[i])

        if value is None:
            continue

        neighbours = []

        for j in range(
            i - radius,
            i + radius + 1
        ):

            if j == i:
                continue

            v = h(candles[j])

            if v is not None:
                neighbours.append(v)

        if len(neighbours) != radius * 2:
            continue

        if value >= max(neighbours):
            result.append((i, value))

    return result


def swing_lows(candles, radius=2):

    result = []

    if len(candles) < radius * 2 + 1:
        return result

    for i in range(
        radius,
        len(candles) - radius
    ):

        value = l(candles[i])

        if value is None:
            continue

        neighbours = []

        for j in range(
            i - radius,
            i + radius + 1
        ):

            if j == i:
                continue

            v = l(candles[j])

            if v is not None:
                neighbours.append(v)

        if len(neighbours) != radius * 2:
            continue

        if value <= min(neighbours):
            result.append((i, value))

    return result


def market_structure(candles):

    data = last(candles, STRUCTURE_LOOKBACK)

    highs = swing_highs(data)
    lows = swing_lows(data)

    if len(highs) < 2 or len(lows) < 2:
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
    return market_structure(candles)


def context_w1(candles):
    return market_structure(candles)


def context_1h(candles):
    return market_structure(candles)


# ============================================================
# IMBALANCE
# ============================================================

def find_imbalances(candles):

    result = []

    data = last(candles, 40)

    if len(data) < 3:
        return result

    for i in range(2, len(data)):

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
            c_low
        ):
            continue

        # Bullish FVG
        if c_low > a_high:

            result.append({
                "direction": "bullish",
                "low": a_high,
                "high": c_low,
                "index": i
            })

        # Bearish FVG
        if c_high < a_low:

            result.append({
                "direction": "bearish",
                "low": c_high,
                "high": a_low,
                "index": i
            })

    return result


def imbalance_context(candles, direction):

    gaps = find_imbalances(candles)

    if not gaps:
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

    recent = gaps[-6:]

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


# ============================================================
# LIQUIDITY SWEEP
# ============================================================

def sweep_level(sweep):

    if not sweep:
        return None

    return f(
        sweep.get(
            "level",
            sweep.get(
                "price"
            )
        )
    )


def sweep_direction(sweep):

    if not sweep:
        return None

    direction = str(
        sweep.get(
            "direction",
            ""
        )
    ).upper()

    if direction in {
        "LONG",
        "SHORT"
    }:
        return direction

    return None


def sweep_confirmed(sweep, direction):

    if not sweep:
        return False

    if not sweep.get("swept"):
        return False

    return (
        sweep_direction(sweep)
        == direction
    )


def find_sweep_extreme(
    candles,
    direction,
    level
):

    if level is None:
        return None

    data = last(
        candles,
        SWEEP_LOOKBACK
    )

    if direction == "LONG":

        values = [
            l(x)
            for x in data
            if (
                l(x) is not None
                and l(x) < level
            )
        ]

        if values:
            return min(values)

    else:

        values = [
            h(x)
            for x in data
            if (
                h(x) is not None
                and h(x) > level
            )
        ]

        if values:
            return max(values)

    return level


# ============================================================
# 5M V / L REVERSAL
# ============================================================

def v_l_reversal(
    candles,
    direction,
    sweep_level_value,
    sweep_extreme
):

    data = last(candles, 24)

    if len(data) < 5:
        return (
            False,
            "Недостаточно 5M данных.",
            None,
            None
        )

    if (
        sweep_level_value is None
        or sweep_extreme is None
    ):
        return (
            False,
            "Нет корректного sweep.",
            None,
            None
        )

    manipulation = abs(
        sweep_extreme
        - sweep_level_value
    )

    if manipulation <= 0:
        return (
            False,
            "Манипуляция слишком мала.",
            None,
            None
        )

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        lows = [
            l(x)
            for x in data
            if l(x) is not None
        ]

        if not lows:
            return (
                False,
                "Нет 5M low.",
                None,
                None
            )

        manipulation_low = min(lows)

        if manipulation_low >= sweep_level_value:
            return (
                False,
                "Нет снятия ликвидности снизу.",
                manipulation,
                0
            )

        low_index = None

        for i, candle in enumerate(data):

            value = l(candle)

            if (
                value is not None
                and value <= manipulation_low
            ):
                low_index = i

        if low_index is None:
            return (
                False,
                "Не найден экстремум манипуляции.",
                manipulation,
                0
            )

        after = data[low_index:]

        closes = [
            cl(x)
            for x in after
            if cl(x) is not None
        ]

        if not closes:
            return (
                False,
                "Нет close после манипуляции.",
                manipulation,
                0
            )

        recovery = (
            max(closes)
            - manipulation_low
        )

        recovery_ratio = (
            recovery
            / manipulation
        )

        if recovery_ratio < MIN_RECOVERY_RATIO:

            return (
                False,
                (
                    "V-разворот слабый: "
                    f"{recovery_ratio:.2f}x "
                    "восстановления."
                ),
                manipulation,
                recovery
            )

        latest = data[-1]

        close = cl(latest)

        if (
            candle_direction(latest)
            != "bullish"
            or close is None
            or close <= sweep_level_value
        ):

            return (
                False,
                (
                    "Нет bullish body close "
                    "выше sweep."
                ),
                manipulation,
                recovery
            )

        # Проверка агрессивности последней свечи.
        if candle_body_ratio(latest) < 0.35:

            return (
                False,
                "Bullish reversal недостаточно сильный.",
                manipulation,
                recovery
            )

        return (
            True,
            (
                "5M V-reversal подтвержден: "
                "снятие снизу + восстановление "
                ">= 1/3 + bullish close."
            ),
            manipulation,
            recovery
        )

    # ========================================================
    # SHORT
    # ========================================================

    highs = [
        h(x)
        for x in data
        if h(x) is not None
    ]

    if not highs:
        return (
            False,
            "Нет 5M high.",
            None,
            None
        )

    manipulation_high = max(highs)

    if manipulation_high <= sweep_level_value:

        return (
            False,
            "Нет снятия ликвидности сверху.",
            manipulation,
            0
        )

    high_index = None

    for i, candle in enumerate(data):

        value = h(candle)

        if (
            value is not None
            and value >= manipulation_high
        ):
            high_index = i

    if high_index is None:
        return (
            False,
            "Не найден экстремум манипуляции.",
            manipulation,
            0
        )

    after = data[high_index:]

    closes = [
        cl(x)
        for x in after
        if cl(x) is not None
    ]

    if not closes:
        return (
            False,
            "Нет close после манипуляции.",
            manipulation,
            0
        )

    recovery = (
        manipulation_high
        - min(closes)
    )

    recovery_ratio = (
        recovery
        / manipulation
    )

    if recovery_ratio < MIN_RECOVERY_RATIO:

        return (
            False,
            (
                "L-разворот слабый: "
                f"{recovery_ratio:.2f}x "
                "восстановления."
            ),
            manipulation,
            recovery
        )

    latest = data[-1]

    close = cl(latest)

    if (
        candle_direction(latest)
        != "bearish"
        or close is None
        or close >= sweep_level_value
    ):

        return (
            False,
            (
                "Нет bearish body close "
                "ниже sweep."
            ),
            manipulation,
            recovery
        )

    if candle_body_ratio(latest) < 0.35:

        return (
            False,
            "Bearish reversal недостаточно сильный.",
            manipulation,
            recovery
        )

    return (
        True,
        (
            "5M L-reversal подтвержден: "
            "снятие сверху + восстановление "
            ">= 1/3 + bearish close."
        ),
        manipulation,
        recovery
    )


# ============================================================
# STRUCTURAL TARGET
# ============================================================

def structural_target(
    candles_1h,
    direction,
    entry
):

    data = last(
        candles_1h,
        STRUCTURE_LOOKBACK
    )

    if direction == "LONG":

        highs = swing_highs(
            data,
            2
        )

        candidates = [
            value
            for _, value in highs
            if (
                value is not None
                and value > entry
            )
        ]

        if not candidates:
            return None

        # Ближайший структурный HIGH
        return min(candidates)

    lows = swing_lows(
        data,
        2
    )

    candidates = [
        value
        for _, value in lows
        if (
            value is not None
            and value < entry
        )
    ]

    if not candidates:
        return None

    # Ближайший структурный LOW
    return max(candidates)


# ============================================================
# ORDER FLOW
# ============================================================

def flow_check(
    flow,
    direction
):

    if not flow:
        return None, "нет данных"

    absorption = str(
        flow.get(
            "absorption",
            ""
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
            "buy"
        }:
            return True, "buyer absorption"

        if delta is not None and delta > 0:
            return True, "positive delta"

        if cvd is not None and cvd > 0:
            return True, "rising CVD"

    else:

        if absorption in {
            "sellers",
            "seller",
            "sell"
        }:
            return True, "seller absorption"

        if delta is not None and delta < 0:
            return True, "negative delta"

        if cvd is not None and cvd < 0:
            return True, "falling CVD"

    return False, (
        "order flow не подтверждает направление"
    )


# ============================================================
# BUILD TRADE
# ============================================================

def build_trade(
    direction,
    entry,
    sweep_extreme,
    target
):

    entry = f(entry)
    sweep_extreme = f(sweep_extreme)
    target = f(target)

    if None in (
        entry,
        sweep_extreme,
        target
    ):
        return (
            None,
            None,
            None,
            None
        )

    if direction == "LONG":

        sl = (
            sweep_extreme
            * (
                1
                - SL_BUFFER_PCT / 100
            )
        )

        if not (
            sl < entry < target
        ):
            return (
                None,
                None,
                None,
                None
            )

    else:

        sl = (
            sweep_extreme
            * (
                1
                + SL_BUFFER_PCT / 100
            )
        )

        if not (
            target < entry < sl
        ):
            return (
                None,
                None,
                None,
                None
            )

    risk = abs(
        entry - sl
    )

    reward = abs(
        target - entry
    )

    if risk <= 0 or reward <= 0:
        return (
            None,
            None,
            None,
            None
        )

    rr = reward / risk

    return (
        round(entry, 6),
        round(sl, 6),
        round(target, 6),
        round(rr, 2)
    )


# ============================================================
# ANTI CHASE
# ============================================================

def anti_chase(
    entry,
    sweep_level_value
):

    distance = distance_pct(
        entry,
        sweep_level_value
    )

    if distance is None:
        return False

    return (
        distance
        <= MAX_ENTRY_DISTANCE_PCT
    )


# ============================================================
# MAIN ANALYZE
# ============================================================

def analyze(
    candles_1h: List[Any],
    candles_15m: Optional[List[Any]] = None,
    candles_5m: Optional[List[Any]] = None,
    current_price: Optional[float] = None,
    major_levels=None,
    order_flow: Optional[Dict[str, Any]] = None,
    sweep: Optional[Dict[str, Any]] = None,
    candles_d1: Optional[List[Any]] = None,
    candles_w1: Optional[List[Any]] = None,
    price: Optional[float] = None,
):

    result = Setup()

    current = (
        f(current_price)
        if current_price is not None
        else f(price)
    )

    # --------------------------------------------------------
    # DATA
    # --------------------------------------------------------

    if (
        current is None
        or not candles_1h
        or not candles_5m
    ):

        result.reason = (
            "Недостаточно рыночных данных."
        )

        return result.to_dict()

    # --------------------------------------------------------
    # D1
    # --------------------------------------------------------

    if candles_d1:

        d1 = context_d1(
            candles_d1
        )

    else:

        d1 = "neutral"

    result.d1_context = d1

    # W1 fallback

    if d1 == "neutral" and candles_w1:

        w1 = context_w1(
            candles_w1
        )

        result.w1_context = w1

        if w1 in {
            "bullish",
            "bearish"
        }:
            d1 = w1

    # Без D1 направления
    # не торгуем.

    if d1 not in {
        "bullish",
        "bearish"
    }:

        result.score = 25
        result.stage = "D1"

        result.reason = (
            "D1 не показывает активный тренд "
            "→ ждём. Если D1 неясный, "
            "не входим."
        )

        return result.to_dict()

    # --------------------------------------------------------
    # 1H
    # --------------------------------------------------------

    h1 = context_1h(
        candles_1h
    )

    result.h1_context = h1

    if d1 != h1:

        result.score = 35
        result.stage = "1H"

        result.reason = (
            f"D1={d1}, 1H={h1}. "
            "Старшие таймфреймы не синхронизированы "
            "→ вход запрещён."
        )

        return result.to_dict()

    direction = (
        "LONG"
        if d1 == "bullish"
        else "SHORT"
    )

    result.direction = direction

    # --------------------------------------------------------
    # MAJOR SWEEP
    # --------------------------------------------------------

    if not sweep_confirmed(
        sweep,
        direction
    ):

        result.score = 45
        result.stage = "SWEEP"

        result.reason = (
            f"D1 + 1H = {direction}, "
            "но major liquidity sweep "
            "ещё не подтвержден."
        )

        return result.to_dict()

    level = sweep_level(
        sweep
    )

    if level is None:

        result.score = 40
        result.stage = "SWEEP"

        result.reason = (
            "Sweep не содержит корректного уровня."
        )

        return result.to_dict()

    result.zone_low = level
    result.zone_high = level

    result.liquidity_type = sweep.get(
        "liquidity_type",
        "major liquidity"
    )

    strength = f(
        sweep.get(
            "strength"
        )
    )

    if (
        strength is not None
        and strength < 0.60
    ):

        result.score = 45
        result.stage = "SWEEP"

        result.reason = (
            "Sweep есть, но ликвидность "
            "недостаточно сильная."
        )

        return result.to_dict()

    # --------------------------------------------------------
    # SWEEP EXTREME
    # --------------------------------------------------------

    extreme = f(
        sweep.get(
            "extreme",
            sweep.get(
                "sweep_extreme"
            )
        )
    )

    if extreme is None:

        extreme = find_sweep_extreme(
            candles_5m,
            direction,
            level
        )

    if extreme is None:

        result.score = 50
        result.stage = "SWEEP"

        result.reason = (
            "Не удалось определить "
            "экстремум sweep."
        )

        return result.to_dict()

    result.sweep_extreme = extreme

    # --------------------------------------------------------
    # ANTI CHASE
    # --------------------------------------------------------

    if not anti_chase(
        current,
        level
    ):

        dist = distance_pct(
            current,
            level
        )

        result.score = 55
        result.stage = "SWEPT"

        result.reason = (
            f"Цена ушла от sweep на "
            f"{dist:.2f}% → "
            "не догоняем."
        )

        return result.to_dict()

    # --------------------------------------------------------
    # 5M V/L
    # --------------------------------------------------------

    (
        trigger_ok,
        trigger_reason,
        manipulation,
        recovery
    ) = v_l_reversal(
        candles_5m,
        direction,
        level,
        extreme
    )

    result.confirmation = (
        trigger_reason
    )

    if manipulation:

        result.manipulation_pct = (
            manipulation
            / level
            * 100
        )

    if recovery:

        result.recovery_pct = (
            recovery
            / level
            * 100
        )

    if not trigger_ok:

        result.score = 65
        result.stage = "5M"

        result.reason = (
            "Major sweep есть, "
            "но 5M V/L подтверждение "
            "ещё не сформировано."
        )

        return result.to_dict()

    # --------------------------------------------------------
    # IMBALANCE
    # --------------------------------------------------------

    imb = imbalance_context(
        candles_1h,
        direction
    )

    result.imbalance_context = imb

    # --------------------------------------------------------
    # STRUCTURAL TP
    # --------------------------------------------------------

    target = structural_target(
        candles_1h,
        direction,
        current
    )

    if target is None:

        result.score = 70
        result.stage = "TP"

        result.reason = (
            "Нет валидного структурного "
            "High/Low для TP."
        )

        return result.to_dict()

    tp_distance = distance_pct(
        target,
        current
    )

    if tp_distance is None:

        result.score = 70
        result.stage = "TP"

        result.reason = (
            "Не удалось определить "
            "дистанцию до TP."
        )

        return result.to_dict()

    if tp_distance < MIN_TP_DISTANCE_PCT:

        result.score = 70
        result.stage = "TP"

        result.reason = (
            "Структурный TP слишком близко."
        )

        return result.to_dict()

    if tp_distance > MAX_TP_DISTANCE_PCT:

        result.score = 70
        result.stage = "TP"

        result.reason = (
            "Структурный TP слишком далеко."
        )

        return result.to_dict()

    # --------------------------------------------------------
    # ENTRY / SL / TP
    # --------------------------------------------------------

    (
        entry,
        sl,
        tp,
        rr
    ) = build_trade(
        direction,
        current,
        extreme,
        target
    )

    if None in (
        entry,
        sl,
        tp,
        rr
    ):

        result.score = 70
        result.stage = "TRADE"

        result.reason = (
            "Невалидная геометрия "
            "Entry / SL / TP."
        )

        return result.to_dict()

    result.entry = entry
    result.sl = sl
    result.tp = tp
    result.rr = rr
    result.structural_target = target

    # --------------------------------------------------------
    # ORDER FLOW
    # --------------------------------------------------------

    flow_ok, flow_text = flow_check(
        order_flow,
        direction
    )

    result.order_flow = flow_text

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = 80

    # D1 + 1H sync
    score += 5

    # Strong sweep
    if strength is not None:

        if strength >= 0.80:
            score += 5

        elif strength < 0.70:
            score -= 3

    # Imbalance
    if imb == "respected":
        score += 5

    elif imb == "caution":
        score -= 4

    # Recovery quality
    if (
        recovery is not None
        and manipulation is not None
        and manipulation > 0
    ):

        ratio = (
            recovery
            / manipulation
        )

        if ratio >= 0.50:
            score += 5

        elif ratio >= MIN_RECOVERY_RATIO:
            score += 2

    # Order flow
    if flow_ok is True:
        score += 5

    elif flow_ok is False:
        score -= 5

    # RR is informational only.
    # Strategy no longer requires 1:2.

    if rr >= 2.0:
        score += 2

    elif rr < 1.0:
        score -= 3

    score = max(
        0,
        min(
            100,
            int(score)
        )
    )

    # --------------------------------------------------------
    # FINAL
    # --------------------------------------------------------

    if score < MIN_SCORE_READY:

        result.score = score
        result.stage = "READY"

        result.reason = (
            "Сетап сформирован, "
            "но качество ниже минимального score."
        )

        return result.to_dict()

    result.status = "READY"
    result.stage = "READY"
    result.score = score

    result.reason = (
        f"D1 {d1} → "
        f"1H {h1} → "
        "Major Liquidity Sweep → "
        "5M V/L Reversal → "
        "Entry → SL behind sweep → "
        "Structural TP."
    )

    return result.to_dict()


# ============================================================
# SOL
# ============================================================

def analyze_sol(
    candles_1h,
    candles_15m=None,
    candles_5m=None,
    current_price=None,
    order_flow=None,
    sweep=None,
    major_levels=None,
    candles_d1=None,
    candles_w1=None,
    price=None,
):

    return analyze(
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,
        current_price=current_price,
        price=price,
        major_levels=major_levels,
        order_flow=order_flow,
        sweep=sweep,
        candles_d1=candles_d1,
        candles_w1=candles_w1,
    )


# ============================================================
# GENERIC SYMBOL
# ============================================================

def analyze_symbol(
    symbol,
    candles_1h,
    candles_15m=None,
    candles_5m=None,
    current_price=None,
    order_flow=None,
    sweep=None,
    major_levels=None,
    candles_d1=None,
    candles_w1=None,
    price=None,
):

    return analyze(
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,
        current_price=current_price,
        price=price,
        major_levels=major_levels,
        order_flow=order_flow,
        sweep=sweep,
        candles_d1=candles_d1,
        candles_w1=candles_w1,
    )


__all__ = [
    "STRATEGY_VERSION",
    "Setup",
    "analyze",
    "analyze_sol",
    "analyze_symbol",
]