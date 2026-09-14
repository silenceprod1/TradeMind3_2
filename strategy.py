"""
TradeMind 5.1
D1 -> 1H -> Major Liquidity -> Sweep -> 5M V/L -> Entry -> SL -> Structural TP

ОСНОВНАЯ ЛОГИКА:

D1:
    определяем активный тренд

1H:
    подтверждаем направление D1
    ищем Major Liquidity
    ждём Sweep

5M:
    после Sweep ждём V/L reversal
    восстановление >= 1/3 манипуляции
    bullish/bearish body close
    подтверждение разворота

ENTRY:
    после подтверждения

SL:
    за экстремумом sweep

TP:
    структурный HIGH / LOW
    TP НЕ фиксированный 1:2

ВАЖНО:
    - один TP
    - не входим в середине движения
    - не догоняем цену
    - sweep без Major Liquidity невозможен
    - D1 + 1H должны совпадать
"""

from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, List, Tuple
import math


STRATEGY_VERSION = "5.1"

MIN_SCORE_READY = 80

# Максимальное расстояние цены от sweep
MAX_ENTRY_DISTANCE_PCT = 0.75

# Минимальное восстановление манипуляции
MIN_RECOVERY_RATIO = 1.0 / 3.0

# Буфер SL за экстремумом
SL_BUFFER_PCT = 0.10

# TP
MIN_TP_DISTANCE_PCT = 0.15
MAX_TP_DISTANCE_PCT = 8.0

# Структура
STRUCTURE_LOOKBACK = 60

# Поиск ликвидности
LIQUIDITY_LOOKBACK = 60

# Сколько свечей нужно для swing
SWING_RADIUS = 2

# Минимальная относительная дистанция между уровнями,
# чтобы не считать почти одинаковые уровни разными.
LEVEL_CLUSTER_PCT = 0.15

# Максимальный возраст sweep в 5M свечах
MAX_SWEEP_AGE_5M = 18


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

    major_levels: Optional[List[Dict[str, Any]]] = None

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
            "open": [
                "open",
                "o"
            ],
            "high": [
                "high",
                "h"
            ],
            "low": [
                "low",
                "l"
            ],
            "close": [
                "close",
                "c"
            ],
            "time": [
                "time",
                "timestamp",
                "ts",
                "open_time",
                "openTime"
            ]
        }

        for k in aliases.get(
            key,
            [key]
        ):

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

    return max(
        high - low,
        0.0
    )


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

def swing_highs(
    candles,
    radius=SWING_RADIUS
):

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

            result.append(
                (i, value)
            )

    return result


def swing_lows(
    candles,
    radius=SWING_RADIUS
):

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

            result.append(
                (i, value)
            )

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
# MAJOR LIQUIDITY
# ============================================================

def cluster_levels(
    levels: List[Dict[str, Any]]
):

    if not levels:
        return []

    sorted_levels = sorted(
        levels,
        key=lambda x: x["price"]
    )

    clusters = []

    for level in sorted_levels:

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

                cluster["members"] += 1

                cluster["indices"].append(
                    level["index"]
                )

                # weighted average
                cluster["price"] = (
                    (
                        center
                        * (
                            cluster["members"] - 1
                        )
                    )
                    + price
                ) / cluster["members"]

                cluster["strength"] = min(
                    1.0,
                    cluster["strength"]
                    + 0.08
                )

                placed = True
                break

        if not placed:

            clusters.append({
                "price": price,
                "type": level["type"],
                "index": level["index"],
                "indices": [level["index"]],
                "members": 1,
                "strength": 0.55
            })

    return clusters


def get_major_liquidity(
    candles_1h,
    current_price=None
):

    """
    Главный источник Major Liquidity.

    Уровни строятся ТОЛЬКО из 1H swing highs/lows.

    Возвращает:
        [
            {
                price,
                type: HIGH / LOW,
                strength,
                members,
                index
            }
        ]
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

    levels = cluster_levels(raw)

    current = f(
        current_price
    )

    result = []

    for level in levels:

        price = f(
            level.get("price")
        )

        if price is None:
            continue

        if current is not None:

            dist = distance_pct(
                current,
                price
            )

        else:

            dist = None

        item = {
            "price": round(
                price,
                8
            ),
            "type": level["type"],
            "strength": round(
                level["strength"],
                2
            ),
            "members": level["members"],
            "index": level["index"],
            "distance_pct": (
                round(dist, 3)
                if dist is not None
                else None
            )
        }

        result.append(item)

    # Сначала сильнейшие
    result.sort(
        key=lambda x: (
            -x["strength"],
            x["distance_pct"]
            if x["distance_pct"] is not None
            else 999999
        )
    )

    return result


# ============================================================
# MAJOR LIQUIDITY NEAREST LEVELS
# ============================================================

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
        return []

    result = []

    for level in levels:

        price = f(
            level.get("price")
        )

        if price is None:
            continue

        result.append(
            dict(level)
        )

    result.sort(
        key=lambda x: abs(
            x["price"] - current
        )
    )

    return result


# ============================================================
# IMBALANCE
# ============================================================

def find_imbalances(candles):

    result = []

    data = last(
        candles,
        50
    )

    if len(data) < 3:
        return result

    for i in range(
        2,
        len(data)
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
# SWEEP HELPERS
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


# ============================================================
# VALIDATE SWEEP AGAINST MAJOR LIQUIDITY
# ============================================================

def validate_sweep_against_liquidity(
    sweep,
    major_levels,
    direction
):

    """
    КРИТИЧЕСКОЕ ПРАВИЛО:

    Sweep должен находиться рядом
    с реальным Major Liquidity уровнем.

    Это не позволяет внешнему модулю
    прислать произвольный sweep.
    """

    if not sweep:
        return False

    if not major_levels:
        return False

    level = sweep_level(
        sweep
    )

    if level is None:
        return False

    expected_type = (
        "LOW"
        if direction == "LONG"
        else "HIGH"
    )

    for major in major_levels:

        major_price = f(
            major.get("price")
        )

        major_type = str(
            major.get("type", "")
        ).upper()

        if (
            major_price is None
            or major_type != expected_type
        ):
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
        36
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
# FIND FRESH SWEEP
# ============================================================

def detect_fresh_sweep(
    candles_1h,
    candles_5m,
    current_price
):

    """
    Внутренний fallback.

    Если bot.py не передал sweep,
    стратегия сама пытается найти его
    по Major Liquidity.

    LONG:
        цена должна снять LOW

    SHORT:
        цена должна снять HIGH
    """

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

    if not data:
        return None

    latest_close = cl(
        data[-1]
    )

    if latest_close is None:
        return None

    candidates = []

    for level in levels:

        price = f(
            level.get("price")
        )

        if price is None:
            continue

        level_type = str(
            level.get("type", "")
        ).upper()

        if level_type == "LOW":

            lows = [
                l(c)
                for c in data
                if l(c) is not None
            ]

            if not lows:
                continue

            extreme = min(lows)

            if extreme < price:

                recovery = (
                    latest_close > price
                )

                if recovery:

                    candidates.append({
                        "swept": True,
                        "direction": "LONG",
                        "level": price,
                        "extreme": extreme,
                        "strength": level.get(
                            "strength",
                            0.55
                        ),
                        "liquidity_type": "LOW"
                    })

        elif level_type == "HIGH":

            highs = [
                h(c)
                for c in data
                if h(c) is not None
            ]

            if not highs:
                continue

            extreme = max(highs)

            if extreme > price:

                recovery = (
                    latest_close < price
                )

                if recovery:

                    candidates.append({
                        "swept": True,
                        "direction": "SHORT",
                        "level": price,
                        "extreme": extreme,
                        "strength": level.get(
                            "strength",
                            0.55
                        ),
                        "liquidity_type": "HIGH"
                    })

    if not candidates:
        return None

    # Ближайший к текущей цене
    candidates.sort(
        key=lambda x: abs(
            x["level"]
            - latest_close
        )
    )

    return candidates[0]


# ============================================================
# 5M V / L REVERSAL
# ============================================================

def v_l_reversal(
    candles,
    direction,
    sweep_level_value,
    sweep_extreme
):

    data = last(
        candles,
        30
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
                "Не найден экстремум LONG sweep.",
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
                    "V-разворот слабый: "
                    f"{recovery_ratio:.2f}x "
                    "восстановления."
                ),
                manipulation,
                recovery
            )

        latest = data[-1]

        close = cl(
            latest
        )

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

        if candle_body_ratio(
            latest
        ) < 0.35:

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
                ">= 1/3 + bullish body close."
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
            "Не найден экстремум SHORT sweep.",
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
                "L-разворот слабый: "
                f"{recovery_ratio:.2f}x "
                "восстановления."
            ),
            manipulation,
            recovery
        )

    latest = data[-1]

    close = cl(
        latest
    )

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

    if candle_body_ratio(
        latest
    ) < 0.35:

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
            ">= 1/3 + bearish body close."
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

        candidates = []

        for index, value in highs:

            if (
                value is not None
                and value > entry
            ):

                candidates.append({
                    "index": index,
                    "price": value
                })

        if not candidates:
            return None

        # Берём ближайший структурный HIGH,
        # но не последний формирующийся шум.
        candidates.sort(
            key=lambda x: x["index"],
            reverse=True
        )

        recent = candidates[:5]

        recent.sort(
            key=lambda x: x["price"]
        )

        return recent[0]["price"]

    lows = swing_lows(
        data,
        SWING_RADIUS
    )

    candidates = []

    for index, value in lows:

        if (
            value is not None
            and value < entry
        ):

            candidates.append({
                "index": index,
                "price": value
            })

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x["index"],
        reverse=True
    )

    recent = candidates[:5]

    recent.sort(
        key=lambda x: x["price"],
        reverse=True
    )

    return recent[0]["price"]


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

    if (
        risk <= 0
        or reward <= 0
    ):

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

        d1 = "neutral"

    result.d1_context = d1

    # W1 fallback

    if (
        d1 == "neutral"
        and candles_w1
    ):

        w1 = context_w1(
            candles_w1
        )

        result.w1_context = w1

        if w1 in {
            "bullish",
            "bearish"
        }:

            d1 = w1

    # D1 unclear

    if d1 not in {
        "bullish",
        "bearish"
    }:

        result.score = 25
        result.stage = "D1"

        result.reason = (
            "D1 не показывает активный тренд "
            "→ ждём."
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

    calculated_levels = get_major_liquidity(
        candles_1h,
        current
    )

    result.major_levels = (
        calculated_levels
    )

    # Если bot.py передал уровни,
    # всё равно проверяем их с внутренними.
    if major_levels:

        valid_external = []

        for item in major_levels:

            if not isinstance(
                item,
                dict
            ):
                continue

            p = f(
                item.get(
                    "price",
                    item.get(
                        "level"
                    )
                )
            )

            if p is None:
                continue

            item_copy = dict(item)
            item_copy["price"] = p

            valid_external.append(
                item_copy
            )

        if valid_external:

            calculated_levels = (
                valid_external
            )

    if not calculated_levels:

        result.score = 45
        result.stage = "LIQUIDITY"

        result.reason = (
            "Major Liquidity на 1H "
            "не найдена → ждём."
        )

        return result.to_dict()

    # ========================================================
    # SWEEP
    # ========================================================

    active_sweep = sweep

    # Если sweep не передан,
    # пробуем найти его самостоятельно.
    if not active_sweep:

        active_sweep = (
            detect_fresh_sweep(
                candles_1h,
                candles_5m,
                current
            )
        )

    # Если sweep передан,
    # он обязан быть подтверждён.
    if not sweep_confirmed(
        active_sweep,
        direction
    ):

        result.score = 45
        result.stage = "SWEEP"

        result.reason = (
            f"D1 + 1H = {direction}, "
            "но Major Liquidity Sweep "
            "ещё не подтверждён."
        )

        return result.to_dict()

    # ========================================================
    # SWEEP MUST MATCH MAJOR LIQUIDITY
    # ========================================================

    if not validate_sweep_against_liquidity(
        active_sweep,
        calculated_levels,
        direction
    ):

        result.score = 45
        result.stage = "SWEEP"

        result.reason = (
            "Sweep получен, но он не совпадает "
            "с Major Liquidity 1H → "
            "Sweep отклонён."
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

    strength = f(
        active_sweep.get(
            "strength"
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
            "экстремум sweep."
        )

        return result.to_dict()

    result.sweep_extreme = extreme

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
            f"Цена ушла от sweep на "
            f"{dist:.2f}% → "
            "не догоняем."
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
            "Major Liquidity Sweep есть, "
            "но 5M V/L подтверждение "
            "ещё не сформировано."
        )

        return result.to_dict()

    # ========================================================
    # IMBALANCE
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

    # D1 + 1H
    score += 5

    # Sweep strength
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

    # Recovery
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

    # RR informational only.
    # Fixed 1:2 НЕ требуется.

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

    # ========================================================
    # FINAL
    # ========================================================

    result.score = score

    if score < MIN_SCORE_READY:

        result.stage = "READY"

        result.reason = (
            "Сетап сформирован, "
            "но качество ниже "
            "минимального score."
        )

        return result.to_dict()

    result.status = "READY"
    result.stage = "READY"

    result.reason = (
        f"D1 {d1} → "
        f"1H {h1} → "
        "Major Liquidity → "
        "Sweep → "
        "5M V/L Reversal → "
        "Entry → "
        "SL behind sweep → "
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
]