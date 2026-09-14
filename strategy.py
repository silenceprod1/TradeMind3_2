"""
TradeMind 5.3
D1 -> 1H -> Major Liquidity -> Sweep -> 15M Confirmation
-> 5M V/L -> 5M FVG Inversion -> Entry -> SL -> Structural TP

STRICT VERSION

Главные правила:

1. D1 определяет активный тренд.
2. 1H обязан совпадать с D1.
3. Major Liquidity берётся ТОЛЬКО с 1H.
4. Слабые одиночные swing-уровни отбрасываются.
5. Близкие уровни объединяются в liquidity cluster.
6. Sweep обязан снять Major Liquidity.
7. Sweep обязан быть значимым и свежим.
8. После Sweep нужен 15M confirmation.
9. После 15M confirmation нужен 5M V/L.
10. Recovery >= 1/3 манипуляции.
11. Нужна сильная reversal-свеча.
12. Нужна обязательная 5M FVG inversion.
13. Entry только после полного подтверждения.
14. SL за экстремумом Sweep.
15. TP один и структурный.
16. Фиксированный 1:2 НЕ используется.
17. Не входить в середине движения.
18. Не догонять цену.
19. Высокий Score не может обходить Hard Gate.
"""

from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, List, Tuple
import math


# ============================================================
# VERSION
# ============================================================

STRATEGY_VERSION = "5.3"


# ============================================================
# HARD FILTERS
# ============================================================

MIN_SCORE_READY = 80

MAX_ENTRY_DISTANCE_PCT = 0.75

MIN_RECOVERY_RATIO = 1.0 / 3.0

SL_BUFFER_PCT = 0.10

MIN_TP_DISTANCE_PCT = 0.15
MAX_TP_DISTANCE_PCT = 8.0

STRUCTURE_LOOKBACK = 60


# ============================================================
# MAJOR LIQUIDITY
# ============================================================

LIQUIDITY_LOOKBACK = 80

SWING_RADIUS = 2

# Максимальное расстояние между уровнями одного кластера.
LEVEL_CLUSTER_PCT = 0.15

# Минимальное количество участников для сильного кластера.
MIN_CLUSTER_MEMBERS = 2

# Максимальное количество Major Liquidity.
MAX_MAJOR_LEVELS = 6


# ============================================================
# STRICT SWEEP
# ============================================================

MAX_SWEEP_AGE_5M = 6

# Sweep меньше 0.10% считаем микро-проколом.
MIN_SWEEP_DEPTH_PCT = 0.10

MIN_REVERSAL_BODY_RATIO = 0.35

REQUIRE_5M_IMBALANCE_INVERSION = True


# ============================================================
# 15M CONFIRMATION
# ============================================================

REQUIRE_15M_CONFIRMATION = True

MIN_15M_BODY_RATIO = 0.30


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
    sweep_depth_pct: Optional[float] = None
    sweep_age_5m: Optional[int] = None

    structural_target: Optional[float] = None

    d1_context: Optional[str] = None
    w1_context: Optional[str] = None
    h1_context: Optional[str] = None

    imbalance_context: Optional[str] = None
    imbalance_5m: Optional[str] = None

    manipulation_pct: Optional[float] = None
    recovery_pct: Optional[float] = None
    recovery_ratio: Optional[float] = None

    major_levels: Optional[List[Dict[str, Any]]] = None

    score_breakdown: Optional[List[str]] = None

    def to_dict(self):
        return asdict(self)


# ============================================================
# BASIC HELPERS
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


def distance_pct(a, b):

    a = f(a)
    b = f(b)

    if a is None or b is None or b == 0:
        return None

    return abs(a - b) / abs(b) * 100.0


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


# ============================================================
# SWING STRUCTURE
# ============================================================

def swing_highs(candles, radius=SWING_RADIUS):

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


def swing_lows(candles, radius=SWING_RADIUS):

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

    data = last(
        candles,
        STRUCTURE_LOOKBACK
    )

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
# MAJOR LIQUIDITY — STRICT
# ============================================================

def cluster_levels(levels):

    if not levels:
        return []

    highs = [
        x for x in levels
        if x.get("type") == "HIGH"
    ]

    lows = [
        x for x in levels
        if x.get("type") == "LOW"
    ]

    result = []

    for group in (highs, lows):

        group = sorted(
            group,
            key=lambda x: x["price"]
        )

        clusters = []

        for level in group:

            price = f(
                level.get("price")
            )

            if price is None:
                continue

            placed = False

            for cluster in clusters:

                center = f(
                    cluster["price"]
                )

                if center is None:
                    continue

                dist = distance_pct(
                    price,
                    center
                )

                if (
                    dist is not None
                    and dist <= LEVEL_CLUSTER_PCT
                ):

                    old_members = cluster[
                        "members"
                    ]

                    cluster["price"] = (
                        (
                            center
                            * old_members
                        )
                        + price
                    ) / (
                        old_members + 1
                    )

                    cluster["members"] += 1

                    cluster["indices"].append(
                        level["index"]
                    )

                    cluster["strength"] = min(
                        1.0,
                        0.55
                        + (
                            cluster["members"]
                            - 1
                        ) * 0.12
                    )

                    placed = True
                    break

            if not placed:

                clusters.append({
                    "price": price,
                    "type": level["type"],
                    "members": 1,
                    "indices": [
                        level["index"]
                    ],
                    "strength": 0.55
                })

        result.extend(clusters)

    return result


def get_major_liquidity(
    candles_1h,
    current_price=None
):
    """
    Источник Major Liquidity:
        ТОЛЬКО 1H.

    Приоритет:
        1. Повторные swing levels.
        2. Близкие swing levels.
        3. Сила кластера.
        4. Близость к текущей цене.

    Обычный одиночный swing теперь не считается
    автоматически Major Liquidity.
    """

    if not candles_1h:
        return []

    data = last(
        candles_1h,
        LIQUIDITY_LOOKBACK
    )

    raw = []

    highs = swing_highs(
        data,
        SWING_RADIUS
    )

    lows = swing_lows(
        data,
        SWING_RADIUS
    )

    for index, price in highs:

        raw.append({
            "price": price,
            "type": "HIGH",
            "index": index
        })

    for index, price in lows:

        raw.append({
            "price": price,
            "type": "LOW",
            "index": index
        })

    if not raw:
        return []

    clusters = cluster_levels(raw)

    current = f(
        current_price
    )

    prepared = []

    for cluster in clusters:

        price = f(
            cluster.get("price")
        )

        if price is None:
            continue

        members = int(
            cluster.get(
                "members",
                1
            )
        )

        strength = f(
            cluster.get(
                "strength",
                0.55
            )
        ) or 0.55

        dist = None

        if current is not None:

            dist = distance_pct(
                current,
                price
            )

        # ----------------------------------------------------
        # Дополнительная оценка важности
        # ----------------------------------------------------

        importance = strength

        # Повторные касания сильно повышают значение.
        if members >= 3:
            importance += 0.20

        elif members >= 2:
            importance += 0.10

        # Слишком далёкие уровни не должны доминировать
        # над рабочими уровнями.
        if dist is not None:

            if dist <= 3.0:
                importance += 0.10

            elif dist <= 6.0:
                importance += 0.05

        importance = min(
            1.0,
            importance
        )

        prepared.append({
            "price": round(
                price,
                8
            ),
            "type": cluster["type"],
            "strength": round(
                strength,
                2
            ),
            "importance": round(
                importance,
                2
            ),
            "members": members,
            "index": max(
                cluster.get(
                    "indices",
                    [0]
                )
            ),
            "distance_pct": (
                round(
                    dist,
                    3
                )
                if dist is not None
                else None
            )
        })

    if not prepared:
        return []

    # --------------------------------------------------------
    # Сначала сильные кластеры.
    # --------------------------------------------------------

    prepared.sort(
        key=lambda x: (
            -x["importance"],
            -x["members"],
            x["distance_pct"]
            if x["distance_pct"] is not None
            else 999999
        )
    )

    # --------------------------------------------------------
    # Не даём одному типу ликвидности полностью
    # забить список.
    # --------------------------------------------------------

    selected = []

    high_count = 0
    low_count = 0

    for level in prepared:

        if len(selected) >= MAX_MAJOR_LEVELS:
            break

        if level["type"] == "HIGH":

            if high_count >= 4:
                continue

            high_count += 1

        else:

            if low_count >= 4:
                continue

            low_count += 1

        selected.append(level)

    # Если есть хотя бы 2 уровня — отлично.
    # Если есть только один сильный уровень, оставляем его.
    selected.sort(
        key=lambda x: (
            x["distance_pct"]
            if x["distance_pct"] is not None
            else 999999
        )
    )

    return selected


def nearest_major_levels(
    candles_1h,
    current_price
):

    levels = get_major_liquidity(
        candles_1h,
        current_price
    )

    current = f(
        current_price
    )

    if current is None:
        return levels

    return sorted(
        levels,
        key=lambda x: (
            x["distance_pct"]
            if x["distance_pct"] is not None
            else 999999
        )
    )


# ============================================================
# FVG
# ============================================================

def find_imbalances(candles):

    result = []

    data = last(
        candles,
        80
    )

    if len(data) < 3:
        return result

    for i in range(
        2,
        len(data)
    ):

        first = data[i - 2]
        third = data[i]

        first_high = h(first)
        first_low = l(first)

        third_high = h(third)
        third_low = l(third)

        if None in (
            first_high,
            first_low,
            third_high,
            third_low
        ):
            continue

        # Bullish FVG
        if third_low > first_high:

            result.append({
                "direction": "bullish",
                "low": first_high,
                "high": third_low,
                "index": i
            })

        # Bearish FVG
        if third_high < first_low:

            result.append({
                "direction": "bearish",
                "low": third_high,
                "high": first_low,
                "index": i
            })

    return result


def imbalance_context(
    candles,
    direction
):

    gaps = find_imbalances(
        candles
    )

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

    recent = gaps[-8:]

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
# 15M CONFIRMATION
# ============================================================

def confirmation_15m(
    candles_15m,
    direction,
    sweep_level_value
):
    """
    После Major Liquidity Sweep:

    LONG:
        15M должен показать bullish reclaim
        уровня sweep.

    SHORT:
        15M должен показать bearish reclaim
        уровня sweep.

    Это отдельный Hard Gate.
    """

    if not candles_15m:

        return (
            False,
            "15M данные отсутствуют."
        )

    data = last(
        candles_15m,
        8
    )

    if len(data) < 2:

        return (
            False,
            "Недостаточно 15M свечей."
        )

    level = f(
        sweep_level_value
    )

    if level is None:

        return (
            False,
            "Нет уровня Sweep для 15M confirmation."
        )

    # Проверяем последние закрытые свечи.
    latest = data[-1]

    latest_close = cl(latest)

    if latest_close is None:

        return (
            False,
            "Нет close последней 15M свечи."
        )

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        # Последняя свеча должна закрыться выше уровня.
        if latest_close <= level:

            return (
                False,
                "15M ещё не вернулся выше sweep."
            )

        # Нужна bullish структура последних свечей.
        bullish_count = 0

        for candle in data[-3:]:

            if (
                candle_direction(candle)
                == "bullish"
            ):
                bullish_count += 1

        body = candle_body_ratio(
            latest
        )

        if (
            bullish_count >= 1
            and body >= MIN_15M_BODY_RATIO
        ):

            return (
                True,
                (
                    "15M bullish confirmation: "
                    "reclaim + bullish body."
                )
            )

        return (
            False,
            "15M reclaim есть, но свечное подтверждение слабое."
        )

    # ========================================================
    # SHORT
    # ========================================================

    if latest_close >= level:

        return (
            False,
            "15M ещё не вернулся ниже sweep."
        )

    bearish_count = 0

    for candle in data[-3:]:

        if (
            candle_direction(candle)
            == "bearish"
        ):
            bearish_count += 1

    body = candle_body_ratio(
        latest
    )

    if (
        bearish_count >= 1
        and body >= MIN_15M_BODY_RATIO
    ):

        return (
            True,
            (
                "15M bearish confirmation: "
                "reclaim + bearish body."
            )
        )

    return (
        False,
        "15M reclaim есть, но свечное подтверждение слабое."
    )


# ============================================================
# 5M FVG INVERSION
# ============================================================

def fvg_inversion_5m(
    candles,
    direction,
    sweep_index=None
):

    if not candles:

        return (
            False,
            "Нет 5M данных.",
            None
        )

    data = last(
        candles,
        40
    )

    if len(data) < 5:

        return (
            False,
            "Недостаточно 5M данных для FVG.",
            None
        )

    gaps = find_imbalances(
        data
    )

    if not gaps:

        return (
            False,
            "5M FVG не найден.",
            None
        )

    latest = data[-1]

    op = o(latest)
    close = cl(latest)

    if op is None or close is None:

        return (
            False,
            "Нет OHLC последней 5M свечи.",
            None
        )

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        bearish = [
            g for g in gaps
            if g["direction"] == "bearish"
        ]

        if not bearish:

            return (
                False,
                "Нет bearish 5M FVG для LONG inversion.",
                None
            )

        # Ищем свежие FVG, а не обязательно последний.
        for gap in reversed(bearish):

            gap_high = gap["high"]

            if (
                close > gap_high
                and op <= gap_high
            ):

                return (
                    True,
                    (
                        "5M bearish FVG "
                        "инвертирован bullish body close."
                    ),
                    gap
                )

        return (
            False,
            "Bearish 5M FVG ещё не инвертирован вверх.",
            bearish[-1]
        )

    # ========================================================
    # SHORT
    # ========================================================

    bullish = [
        g for g in gaps
        if g["direction"] == "bullish"
    ]

    if not bullish:

        return (
            False,
            "Нет bullish 5M FVG для SHORT inversion.",
            None
        )

    for gap in reversed(bullish):

        gap_low = gap["low"]

        if (
            close < gap_low
            and op >= gap_low
        ):

            return (
                True,
                (
                    "5M bullish FVG "
                    "инвертирован bearish body close."
                ),
                gap
            )

    return (
        False,
        "Bullish 5M FVG ещё не инвертирован вниз.",
        bullish[-1]
    )


# ============================================================
# SWEEP
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

    if direction in (
        "LONG",
        "SHORT"
    ):
        return direction

    return None


def sweep_confirmed(
    sweep,
    direction
):

    if not sweep:
        return False

    if not sweep.get("swept"):
        return False

    return (
        sweep_direction(sweep)
        == direction
    )


def validate_sweep_against_liquidity(
    sweep,
    major_levels,
    direction
):

    if not sweep or not major_levels:
        return False

    level = sweep_level(
        sweep
    )

    if level is None:
        return False

    wanted = (
        "LOW"
        if direction == "LONG"
        else "HIGH"
    )

    for major in major_levels:

        if str(
            major.get(
                "type",
                ""
            )
        ).upper() != wanted:
            continue

        major_price = f(
            major.get("price")
        )

        if major_price is None:
            continue

        dist = distance_pct(
            level,
            major_price
        )

        if (
            dist is not None
            and dist <= LEVEL_CLUSTER_PCT
        ):
            return True

    return False


# ============================================================
# SWEEP EXTREME
# ============================================================

def find_sweep_extreme(
    candles,
    direction,
    level
):

    if level is None:
        return None

    data = last(
        candles,
        MAX_SWEEP_AGE_5M
    )

    if direction == "LONG":

        values = [
            l(c)
            for c in data
            if (
                l(c) is not None
                and l(c) < level
            )
        ]

        if values:
            return min(values)

    else:

        values = [
            h(c)
            for c in data
            if (
                h(c) is not None
                and h(c) > level
            )
        ]

        if values:
            return max(values)

    return None


def calculate_sweep_age(
    candles,
    level,
    direction
):

    if not candles or level is None:
        return None

    data = last(
        candles,
        MAX_SWEEP_AGE_5M
    )

    for age, candle in enumerate(
        reversed(data)
    ):

        if direction == "LONG":

            low_value = l(candle)

            if (
                low_value is not None
                and low_value < level
            ):
                return age

        else:

            high_value = h(candle)

            if (
                high_value is not None
                and high_value > level
            ):
                return age

    return None


# ============================================================
# SWEEP QUALITY
# ============================================================

def validate_sweep_quality(
    candles_5m,
    direction,
    level,
    extreme
):

    if (
        not candles_5m
        or level is None
        or extreme is None
    ):

        return (
            False,
            "Недостаточно данных для Sweep.",
            None,
            None
        )

    depth = distance_pct(
        extreme,
        level
    )

    if depth is None:

        return (
            False,
            "Не удалось определить глубину Sweep.",
            None,
            None
        )

    # MICRO SWEEP BLOCK
    if depth < MIN_SWEEP_DEPTH_PCT:

        return (
            False,
            (
                f"Микро-прокол: "
                f"{depth:.3f}% < "
                f"{MIN_SWEEP_DEPTH_PCT:.2f}%."
            ),
            depth,
            None
        )

    age = calculate_sweep_age(
        candles_5m,
        level,
        direction
    )

    if age is None:

        return (
            False,
            "Свежий Sweep не найден.",
            depth,
            None
        )

    if age > MAX_SWEEP_AGE_5M:

        return (
            False,
            (
                f"Sweep устарел: "
                f"{age} свечей 5M."
            ),
            depth,
            age
        )

    return (
        True,
        (
            f"Sweep valid: "
            f"depth={depth:.3f}%, "
            f"age={age}."
        ),
        depth,
        age
    )


# ============================================================
# INTERNAL FRESH SWEEP
# ============================================================

def detect_fresh_sweep(
    candles_1h,
    candles_5m,
    current_price
):

    levels = get_major_liquidity(
        candles_1h,
        current_price
    )

    if not levels or not candles_5m:
        return None

    data = last(
        candles_5m,
        MAX_SWEEP_AGE_5M
    )

    candidates = []

    for level in levels:

        price = f(
            level.get("price")
        )

        if price is None:
            continue

        level_type = str(
            level.get(
                "type",
                ""
            )
        ).upper()

        # ====================================================
        # LONG
        # ====================================================

        if level_type == "LOW":

            lows = [
                l(c)
                for c in data
                if l(c) is not None
            ]

            if not lows:
                continue

            extreme = min(lows)

            if extreme >= price:
                continue

            depth = distance_pct(
                extreme,
                price
            )

            if (
                depth is None
                or depth < MIN_SWEEP_DEPTH_PCT
            ):
                continue

            latest_close = cl(data[-1])

            if latest_close is None:
                continue

            # Reclaim
            if latest_close <= price:
                continue

            age = calculate_sweep_age(
                candles_5m,
                price,
                "LONG"
            )

            if age is None or age > MAX_SWEEP_AGE_5M:
                continue

            candidates.append({
                "swept": True,
                "direction": "LONG",
                "level": price,
                "extreme": extreme,
                "strength": level.get(
                    "strength",
                    0.55
                ),
                "liquidity_type": "LOW",
                "sweep_depth_pct": depth,
                "sweep_age_5m": age
            })

        # ====================================================
        # SHORT
        # ====================================================

        elif level_type == "HIGH":

            highs = [
                h(c)
                for c in data
                if h(c) is not None
            ]

            if not highs:
                continue

            extreme = max(highs)

            if extreme <= price:
                continue

            depth = distance_pct(
                extreme,
                price
            )

            if (
                depth is None
                or depth < MIN_SWEEP_DEPTH_PCT
            ):
                continue

            latest_close = cl(data[-1])

            if latest_close is None:
                continue

            if latest_close >= price:
                continue

            age = calculate_sweep_age(
                candles_5m,
                price,
                "SHORT"
            )

            if age is None or age > MAX_SWEEP_AGE_5M:
                continue

            candidates.append({
                "swept": True,
                "direction": "SHORT",
                "level": price,
                "extreme": extreme,
                "strength": level.get(
                    "strength",
                    0.55
                ),
                "liquidity_type": "HIGH",
                "sweep_depth_pct": depth,
                "sweep_age_5m": age
            })

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (
            x.get(
                "sweep_age_5m",
                999
            ),
            -x.get(
                "strength",
                0
            ),
            -x.get(
                "sweep_depth_pct",
                0
            )
        )
    )

    return candidates[0]


# ============================================================
# 5M V/L REVERSAL
# ============================================================

def v_l_reversal(
    candles,
    direction,
    sweep_level_value,
    sweep_extreme
):

    data = last(
        candles,
        MAX_SWEEP_AGE_5M + 10
    )

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
            "Нет корректного Sweep.",
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

        low_index = None

        for i in range(
            len(data) - 1,
            -1,
            -1
        ):

            low_value = l(
                data[i]
            )

            if (
                low_value is not None
                and low_value <= sweep_extreme
            ):

                low_index = i
                break

        if low_index is None:

            return (
                False,
                "Не найден экстремум LONG Sweep.",
                manipulation,
                0
            )

        after = data[
            low_index:
        ]

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
            - sweep_extreme
        )

        recovery_ratio = (
            recovery
            / manipulation
        )

        if recovery_ratio < MIN_RECOVERY_RATIO:

            return (
                False,
                (
                    f"V-recovery слабый: "
                    f"{recovery_ratio:.2f}x."
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
                "Нет bullish body close выше Sweep.",
                manipulation,
                recovery
            )

        body_ratio = candle_body_ratio(
            latest
        )

        if body_ratio < MIN_REVERSAL_BODY_RATIO:

            return (
                False,
                (
                    f"Bullish reversal слабый: "
                    f"body={body_ratio:.2f}."
                ),
                manipulation,
                recovery
            )

        return (
            True,
            (
                "5M V-reversal подтвержден: "
                "Sweep + recovery >= 1/3 "
                "+ strong bullish body."
            ),
            manipulation,
            recovery
        )

    # ========================================================
    # SHORT
    # ========================================================

    high_index = None

    for i in range(
        len(data) - 1,
        -1,
        -1
    ):

        high_value = h(
            data[i]
        )

        if (
            high_value is not None
            and high_value >= sweep_extreme
        ):

            high_index = i
            break

    if high_index is None:

        return (
            False,
            "Не найден экстремум SHORT Sweep.",
            manipulation,
            0
        )

    after = data[
        high_index:
    ]

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
        sweep_extreme
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
                f"L-recovery слабый: "
                f"{recovery_ratio:.2f}x."
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
            "Нет bearish body close ниже Sweep.",
            manipulation,
            recovery
        )

    body_ratio = candle_body_ratio(
        latest
    )

    if body_ratio < MIN_REVERSAL_BODY_RATIO:

        return (
            False,
            (
                f"Bearish reversal слабый: "
                f"body={body_ratio:.2f}."
            ),
            manipulation,
            recovery
        )

    return (
        True,
        (
            "5M L-reversal подтвержден: "
            "Sweep + recovery >= 1/3 "
            "+ strong bearish body."
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

    if len(data) < 5:
        return None

    if direction == "LONG":

        highs = swing_highs(
            data,
            SWING_RADIUS
        )

        candidates = [
            {
                "index": index,
                "price": value
            }
            for index, value in highs
            if (
                value is not None
                and value > entry
            )
        ]

        if not candidates:
            return None

        # Для LONG выбираем ближайший валидный
        # структурный HIGH.
        candidates.sort(
            key=lambda x: x["price"]
        )

        return candidates[0]["price"]

    lows = swing_lows(
        data,
        SWING_RADIUS
    )

    candidates = [
        {
            "index": index,
            "price": value
        }
        for index, value in lows
        if (
            value is not None
            and value < entry
        )
    ]

    if not candidates:
        return None

    # Для SHORT — ближайший структурный LOW.
    candidates.sort(
        key=lambda x: x["price"],
        reverse=True
    )

    return candidates[0]["price"]


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

        if absorption in (
            "buyers",
            "buyer",
            "buy"
        ):
            return True, "buyer absorption"

        if delta is not None and delta > 0:
            return True, "positive delta"

        if cvd is not None and cvd > 0:
            return True, "rising CVD"

    else:

        if absorption in (
            "sellers",
            "seller",
            "sell"
        ):
            return True, "seller absorption"

        if delta is not None and delta < 0:
            return True, "negative delta"

        if cvd is not None and cvd < 0:
            return True, "falling CVD"

    return (
        False,
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
    sweep_extreme = f(
        sweep_extreme
    )
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

    # ========================================================
    # DATA
    # ========================================================

    if (
        current is None
        or not candles_1h
        or not candles_5m
    ):

        result.reason = (
            "Недостаточно рыночных данных."
        )

        return result.to_dict()

    # ========================================================
    # D1
    # ========================================================

    if candles_d1:

        d1 = context_d1(
            candles_d1
        )

    else:

        # Если D1 не передан, стратегия не должна
        # самостоятельно придумывать направление.
        d1 = "neutral"

    result.d1_context = d1

    # ========================================================
    # W1 FALLBACK
    # ========================================================

    if (
        d1 == "neutral"
        and candles_w1
    ):

        w1 = context_w1(
            candles_w1
        )

        result.w1_context = w1

        if w1 in (
            "bullish",
            "bearish"
        ):

            d1 = w1

    # ========================================================
    # D1 GATE
    # ========================================================

    if d1 not in (
        "bullish",
        "bearish"
    ):

        result.score = 25
        result.stage = "D1"

        result.reason = (
            "D1 не показывает активный тренд "
            "→ вход запрещён."
        )

        return result.to_dict()

    # ========================================================
    # 1H
    # ========================================================

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

    # ========================================================
    # MAJOR LIQUIDITY
    # ========================================================

    # ВАЖНО:
    # входной major_levels намеренно НЕ используется
    # как источник истины.

    calculated_levels = get_major_liquidity(
        candles_1h,
        current
    )

    result.major_levels = (
        calculated_levels
    )

    if not calculated_levels:

        result.score = 45
        result.stage = "LIQUIDITY"

        result.reason = (
            "Major Liquidity на 1H не найдена "
            "→ ждём."
        )

        return result.to_dict()

    # ========================================================
    # SWEEP
    # ========================================================

    active_sweep = None

    # Сначала проверяем переданный sweep.
    if sweep:

        if (
            sweep_confirmed(
                sweep,
                direction
            )
            and validate_sweep_against_liquidity(
                sweep,
                calculated_levels,
                direction
            )
        ):

            active_sweep = sweep

    # Если внешний sweep невалидный —
    # ищем свежий самостоятельно.
    if not active_sweep:

        active_sweep = detect_fresh_sweep(
            candles_1h,
            candles_5m,
            current
        )

    if not sweep_confirmed(
        active_sweep,
        direction
    ):

        result.score = 45
        result.stage = "SWEEP"

        result.reason = (
            f"D1 + 1H = {direction}, "
            "но корректного Major Liquidity "
            "Sweep нет."
        )

        return result.to_dict()

    # ========================================================
    # SWEEP VS MAJOR LIQUIDITY
    # ========================================================

    if not validate_sweep_against_liquidity(
        active_sweep,
        calculated_levels,
        direction
    ):

        result.score = 45
        result.stage = "SWEEP"

        result.reason = (
            "Sweep отклонён: "
            "уровень не является Major Liquidity 1H."
        )

        return result.to_dict()

    level = sweep_level(
        active_sweep
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

    result.liquidity_type = (
        active_sweep.get(
            "liquidity_type",
            "major liquidity"
        )
    )

    # ========================================================
    # SWEEP EXTREME
    # ========================================================

    extreme = f(
        active_sweep.get(
            "extreme",
            active_sweep.get(
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
            "экстремум Sweep."
        )

        return result.to_dict()

    result.sweep_extreme = extreme

    # ========================================================
    # STRICT SWEEP QUALITY
    # ========================================================

    (
        sweep_ok,
        sweep_reason,
        sweep_depth,
        sweep_age
    ) = validate_sweep_quality(
        candles_5m,
        direction,
        level,
        extreme
    )

    result.sweep_depth_pct = sweep_depth
    result.sweep_age_5m = sweep_age

    if not sweep_ok:

        result.score = 55
        result.stage = "SWEEP"

        result.reason = (
            "Sweep найден, но не проходит "
            f"strict filter: {sweep_reason}"
        )

        return result.to_dict()

    # ========================================================
    # ANTI CHASE
    # ========================================================

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
            f"Цена ушла от Sweep на "
            f"{dist:.2f}% → не догоняем."
        )

        return result.to_dict()

    # ========================================================
    # 15M CONFIRMATION
    # ========================================================

    (
        confirmation_15m_ok,
        confirmation_15m_reason
    ) = confirmation_15m(
        candles_15m,
        direction,
        level
    )

    result.confirmation_15m = (
        confirmation_15m_reason
    )

    if REQUIRE_15M_CONFIRMATION:

        if not confirmation_15m_ok:

            result.score = 60
            result.stage = "15M"

            result.reason = (
                "Major Liquidity Sweep есть, "
                "но 15M confirmation отсутствует."
            )

            return result.to_dict()

    # ========================================================
    # 5M V/L
    # ========================================================

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

    result.confirmation = trigger_reason

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

    if (
        recovery is not None
        and manipulation is not None
        and manipulation > 0
    ):

        result.recovery_ratio = (
            recovery
            / manipulation
        )

    if not trigger_ok:

        result.score = 65
        result.stage = "5M"

        result.reason = (
            "15M confirmation есть, "
            "но 5M V/L ещё не подтверждён."
        )

        return result.to_dict()

    # ========================================================
    # 5M FVG INVERSION
    # ========================================================

    (
        fvg_ok,
        fvg_reason,
        fvg
    ) = fvg_inversion_5m(
        candles_5m,
        direction
    )

    result.imbalance_5m = fvg_reason

    if REQUIRE_5M_IMBALANCE_INVERSION:

        if not fvg_ok:

            result.score = 70
            result.stage = "5M_FVG"

            result.reason = (
                "V/L есть, но обязательная "
                f"5M FVG inversion отсутствует: "
                f"{fvg_reason}"
            )

            return result.to_dict()

    # ========================================================
    # 1H IMBALANCE CONTEXT
    # ========================================================

    imb = imbalance_context(
        candles_1h,
        direction
    )

    result.imbalance_context = imb

    # ========================================================
    # STRUCTURAL TP
    # ========================================================

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
            "HIGH/LOW для TP."
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

    # ========================================================
    # ENTRY / SL / TP
    # ========================================================

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

    # ========================================================
    # ORDER FLOW
    # ========================================================

    flow_ok, flow_text = flow_check(
        order_flow,
        direction
    )

    result.order_flow = flow_text

    # ========================================================
    # SCORE
    # ========================================================

    score = 80

    breakdown = [
        "Base: +80",
        "D1 + 1H synchronized: +5",
        "Major Liquidity Sweep: mandatory",
        "15M confirmation: mandatory",
        "5M V/L: mandatory",
        "5M FVG inversion: mandatory"
    ]

    score += 5

    # --------------------------------------------------------
    # Sweep depth
    # --------------------------------------------------------

    if sweep_depth is not None:

        if sweep_depth >= 0.30:

            score += 5

            breakdown.append(
                f"Strong sweep depth: +5 "
                f"({sweep_depth:.2f}%)"
            )

        else:

            breakdown.append(
                f"Valid sweep depth: +0 "
                f"({sweep_depth:.2f}%)"
            )

    # --------------------------------------------------------
    # Liquidity strength
    # --------------------------------------------------------

    strength = f(
        active_sweep.get(
            "strength"
        )
    )

    if strength is not None:

        if strength >= 0.80:

            score += 3

            breakdown.append(
                "Major Liquidity strength >= 0.80: +3"
            )

        elif strength < 0.70:

            score -= 2

            breakdown.append(
                "Weak liquidity strength: -2"
            )

    # --------------------------------------------------------
    # 15M
    # --------------------------------------------------------

    if confirmation_15m_ok:

        score += 3

        breakdown.append(
            "15M confirmation: +3"
        )

    # --------------------------------------------------------
    # 1H imbalance
    # --------------------------------------------------------

    if imb == "respected":

        score += 3

        breakdown.append(
            "1H imbalance respected: +3"
        )

    elif imb == "caution":

        score -= 3

        breakdown.append(
            "1H imbalance caution: -3"
        )

    # --------------------------------------------------------
    # Recovery
    # --------------------------------------------------------

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

            breakdown.append(
                f"Recovery >= 50%: +5 "
                f"({ratio:.2f}x)"
            )

        else:

            score += 2

            breakdown.append(
                f"Recovery >= 1/3: +2 "
                f"({ratio:.2f}x)"
            )

    # --------------------------------------------------------
    # FVG
    # --------------------------------------------------------

    if fvg_ok:

        score += 5

        breakdown.append(
            "5M FVG inversion confirmed: +5"
        )

    # --------------------------------------------------------
    # Order flow
    # --------------------------------------------------------

    if flow_ok is True:

        score += 5

        breakdown.append(
            "Order flow confirms: +5"
        )

    elif flow_ok is False:

        score -= 5

        breakdown.append(
            "Order flow против направления: -5"
        )

    # --------------------------------------------------------
    # RR informational
    # --------------------------------------------------------

    if rr >= 2.0:

        score += 2

        breakdown.append(
            f"Structural RR >= 2: +2 ({rr:.2f})"
        )

    elif rr < 1.0:

        score -= 3

        breakdown.append(
            f"Poor RR < 1: -3 ({rr:.2f})"
        )

    else:

        breakdown.append(
            f"RR informational: +0 ({rr:.2f})"
        )

    score = max(
        0,
        min(
            100,
            int(score)
        )
    )

    result.score_breakdown = breakdown

    # ========================================================
    # FINAL HARD GATES
    # ========================================================

    if not sweep_ok:

        result.score = score
        result.status = "WAIT"
        result.stage = "SWEEP"

        result.reason = (
            "Hard Gate: Sweep quality не пройдена."
        )

        return result.to_dict()

    if REQUIRE_15M_CONFIRMATION:

        if not confirmation_15m_ok:

            result.score = score
            result.status = "WAIT"
            result.stage = "15M"

            result.reason = (
                "Hard Gate: 15M confirmation "
                "не подтверждена."
            )

            return result.to_dict()

    if not trigger_ok:

        result.score = score
        result.status = "WAIT"
        result.stage = "5M"

        result.reason = (
            "Hard Gate: 5M V/L "
            "не подтверждён."
        )

        return result.to_dict()

    if REQUIRE_5M_IMBALANCE_INVERSION:

        if not fvg_ok:

            result.score = score
            result.status = "WAIT"
            result.stage = "5M_FVG"

            result.reason = (
                "Hard Gate: 5M FVG inversion "
                "не подтверждена."
            )

            return result.to_dict()

    # ========================================================
    # SCORE GATE
    # ========================================================

    result.score = score

    if score < MIN_SCORE_READY:

        result.status = "WAIT"
        result.stage = "READY"

        result.reason = (
            "Все обязательные условия выполнены, "
            "но Score ниже минимального."
        )

        return result.to_dict()

    # ========================================================
    # READY
    # ========================================================

    result.status = "READY"
    result.stage = "READY"

    result.reason = (
        f"D1 {d1} → "
        f"1H {h1} → "
        "Major Liquidity → "
        "Fresh Sweep → "
        "15M Confirmation → "
        "5M V/L → "
        "5M FVG Inversion → "
        "Entry → "
        "SL behind Sweep → "
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


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "STRATEGY_VERSION",
    "Setup",
    "analyze",
    "analyze_sol",
    "analyze_symbol",
    "get_major_liquidity",
    "nearest_major_levels",
    "detect_fresh_sweep",
    "find_imbalances",
    "fvg_inversion_5m",
    "confirmation_15m",
    "validate_sweep_quality",
    "v_l_reversal",
]