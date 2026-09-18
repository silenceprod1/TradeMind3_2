"""
TradeMind Liquidity Engine 1.0

Назначение:
    Надёжное определение Major Liquidity на 1H.

CORE:
    MAJOR LIQUIDITY = ONLY 1H

    BSL = Buy-Side Liquidity
          → только ВЫШЕ текущей цены

    SSL = Sell-Side Liquidity
          → только НИЖЕ текущей цены

ВАЖНО:
    - D1/W1 не используются.
    - 15M/5M не используются для Major Liquidity.
    - Round numbers не являются ликвидностью.
    - Major Liquidity строится только из 1H swing highs/lows.
    - Свежий sweep не удаляет уровень.
    - Старый уровень не удаляется только из-за возраста.
    - Несколько близких swing-точек объединяются в liquidity pool.
    - Сила уровня определяется структурой, количеством касаний
      и расстоянием до текущей цены.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# VERSION
# ============================================================

LIQUIDITY_ENGINE_VERSION = "1.0"


# ============================================================
# SETTINGS
# ============================================================

# Swing detection.
SWING_LEFT = 3
SWING_RIGHT = 2

# Для дополнительной строгой проверки.
STRONG_SWING_LEFT = 4
STRONG_SWING_RIGHT = 3

# Максимальное количество кандидатов.
MAX_SWINGS = 80

# Максимальное количество Major уровней.
MAX_BSL = 8
MAX_SSL = 8
MAX_TOTAL = 16

# Кластеризация.
CLUSTER_DISTANCE_PCT = 0.15

# Минимальная дистанция между финальными уровнями.
DEDUP_DISTANCE_PCT = 0.05

# Максимальный вес старости.
# Это НЕ удаление уровня.
AGE_MAX = 180

# Минимальный structural score,
# ниже которого уровень считается слабым кандидатом.
MIN_STRUCTURAL_SCORE = 15.0


# ============================================================
# GENERIC
# ============================================================

def _f(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None

        value = float(value)

        if not math.isfinite(value):
            return None

        return value

    except (TypeError, ValueError):
        return None


def _i(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None

        return int(float(value))

    except (TypeError, ValueError):
        return None


# ============================================================
# CANDLE ACCESS
# ============================================================

def _v(
    candle: Any,
    key: str,
    default: Any = None,
) -> Any:

    if isinstance(candle, dict):

        value = candle.get(key)

        if value is not None:
            return value

        aliases = {
            "open": "o",
            "high": "h",
            "low": "l",
            "close": "c",
            "volume": "v",

            "open_time": "time",
            "close_time": "time_close",
        }

        alias = aliases.get(key)

        if alias:
            return candle.get(
                alias,
                default,
            )

        return default

    # Binance raw kline fallback.
    if isinstance(candle, (list, tuple)):

        indexes = {
            "open_time": 0,
            "open": 1,
            "high": 2,
            "low": 3,
            "close": 4,
            "volume": 5,
            "close_time": 6,
        }

        index = indexes.get(key)

        if index is not None and len(candle) > index:
            return candle[index]

    return default


def _o(candle: Any) -> Optional[float]:
    return _f(_v(candle, "open"))


def _h(candle: Any) -> Optional[float]:
    return _f(_v(candle, "high"))


def _l(candle: Any) -> Optional[float]:
    return _f(_v(candle, "low"))


def _c(candle: Any) -> Optional[float]:
    return _f(_v(candle, "close"))


def _t(candle: Any) -> Optional[int]:
    return _i(_v(candle, "open_time"))


def _ct(candle: Any) -> Optional[int]:
    return _i(_v(candle, "close_time"))


def _volume(candle: Any) -> float:
    return _f(
        _v(candle, "volume")
    ) or 0.0


# ============================================================
# CANDLE MATH
# ============================================================

def _range(candle: Any) -> float:

    high = _h(candle)
    low = _l(candle)

    if high is None or low is None:
        return 0.0

    return max(
        0.0,
        high - low,
    )


def _body(candle: Any) -> float:

    open_price = _o(candle)
    close_price = _c(candle)

    if (
        open_price is None
        or close_price is None
    ):
        return 0.0

    return abs(
        close_price - open_price
    )


def _body_ratio(candle: Any) -> float:

    r = _range(candle)

    if r <= 0:
        return 0.0

    return _body(candle) / r


def _bullish(candle: Any) -> bool:

    o = _o(candle)
    c = _c(candle)

    return (
        o is not None
        and c is not None
        and c > o
    )


def _bearish(candle: Any) -> bool:

    o = _o(candle)
    c = _c(candle)

    return (
        o is not None
        and c is not None
        and c < o
    )


def _distance_pct(
    a: float,
    b: float,
) -> float:

    if b == 0:
        return 999.0

    return (
        abs(a - b)
        / abs(b)
        * 100.0
    )


# ============================================================
# CONFIRMED CANDLES
# ============================================================

def confirmed_candles(
    candles: List[Any],
) -> List[Any]:

    if not candles:
        return []

    result = []

    # We deliberately keep candles whose close_time
    # cannot be determined.
    #
    # This makes the engine compatible with both
    # dict candles and simplified test candles.

    import time

    now_ms = int(
        time.time() * 1000
    )

    for candle in candles:

        close_time = _ct(candle)

        if close_time is None:

            result.append(
                candle
            )

            continue

        if close_time <= now_ms:

            result.append(
                candle
            )

    return result


# ============================================================
# BASIC SWING DETECTION
# ============================================================

def detect_swing_highs(
    candles: List[Any],
    left: int = SWING_LEFT,
    right: int = SWING_RIGHT,
) -> List[Dict[str, Any]]:

    result = []

    if not candles:
        return result

    if len(candles) < (
        left + right + 1
    ):
        return result

    for i in range(
        left,
        len(candles) - right,
    ):

        center = candles[i]

        high = _h(center)

        if high is None:
            continue

        left_values = []

        right_values = []

        valid = True

        for j in range(
            i - left,
            i,
        ):

            value = _h(
                candles[j]
            )

            if value is None:
                valid = False
                break

            left_values.append(value)

        if not valid:
            continue

        for j in range(
            i + 1,
            i + right + 1,
        ):

            value = _h(
                candles[j]
            )

            if value is None:
                valid = False
                break

            right_values.append(value)

        if not valid:
            continue

        # Strict left.
        #
        # This prevents random local highs
        # from becoming Major Liquidity.
        if high <= max(left_values):
            continue

        # Right can be equal.
        # This allows double-top style pools.
        if high < max(right_values):
            continue

        result.append(
            {
                "index": i,
                "price": high,
                "open_time": _t(center),
                "type": "SWING_HIGH",
                "side": "BSL",
            }
        )

    return result[-MAX_SWINGS:]


# ============================================================
# BASIC SWING LOW DETECTION
# ============================================================

def detect_swing_lows(
    candles: List[Any],
    left: int = SWING_LEFT,
    right: int = SWING_RIGHT,
) -> List[Dict[str, Any]]:

    result = []

    if not candles:
        return result

    if len(candles) < (
        left + right + 1
    ):
        return result

    for i in range(
        left,
        len(candles) - right,
    ):

        center = candles[i]

        low = _l(center)

        if low is None:
            continue

        left_values = []

        right_values = []

        valid = True

        for j in range(
            i - left,
            i,
        ):

            value = _l(
                candles[j]
            )

            if value is None:
                valid = False
                break

            left_values.append(value)

        if not valid:
            continue

        for j in range(
            i + 1,
            i + right + 1,
        ):

            value = _l(
                candles[j]
            )

            if value is None:
                valid = False
                break

            right_values.append(value)

        if not valid:
            continue

        # Strict left.
        if low >= min(left_values):
            continue

        # Right can be equal.
        if low > min(right_values):
            continue

        result.append(
            {
                "index": i,
                "price": low,
                "open_time": _t(center),
                "type": "SWING_LOW",
                "side": "SSL",
            }
        )

    return result[-MAX_SWINGS:]


# ============================================================
# STRONG SWINGS
# ============================================================

def detect_strong_swing_highs(
    candles: List[Any],
) -> List[Dict[str, Any]]:

    return detect_swing_highs(
        candles,
        STRONG_SWING_LEFT,
        STRONG_SWING_RIGHT,
    )


def detect_strong_swing_lows(
    candles: List[Any],
) -> List[Dict[str, Any]]:

    return detect_swing_lows(
        candles,
        STRONG_SWING_LEFT,
        STRONG_SWING_RIGHT,
    )


# ============================================================
# VOLUME STRENGTH
# ============================================================

def _volume_strength(
    candles: List[Any],
    index: int,
) -> float:

    if not candles:
        return 0.0

    if index < 0 or index >= len(candles):
        return 0.0

    current_volume = _volume(
        candles[index]
    )

    if current_volume <= 0:
        return 0.0

    start = max(
        0,
        index - 20,
    )

    volumes = [
        _volume(candles[i])
        for i in range(
            start,
            index,
        )
    ]

    volumes = [
        x for x in volumes
        if x > 0
    ]

    if not volumes:
        return 0.0

    average = (
        sum(volumes)
        / len(volumes)
    )

    if average <= 0:
        return 0.0

    ratio = (
        current_volume
        / average
    )

    if ratio >= 2.0:
        return 1.0

    if ratio >= 1.5:
        return 0.75

    if ratio >= 1.2:
        return 0.50

    if ratio >= 1.0:
        return 0.25

    return 0.0


# ============================================================
# IMPULSE STRENGTH
# ============================================================

def _impulse_strength(
    candles: List[Any],
    index: int,
) -> float:

    if (
        index < 0
        or index >= len(candles)
    ):
        return 0.0

    start = max(
        0,
        index - 3,
    )

    end = min(
        len(candles),
        index + 4,
    )

    ranges = [
        _range(candles[i])
        for i in range(
            start,
            end,
        )
    ]

    ranges = [
        x for x in ranges
        if x > 0
    ]

    if not ranges:
        return 0.0

    center_range = _range(
        candles[index]
    )

    if center_range <= 0:
        return 0.0

    average = (
        sum(ranges)
        / len(ranges)
    )

    if average <= 0:
        return 0.0

    ratio = (
        center_range
        / average
    )

    if ratio >= 2.0:
        return 1.0

    if ratio >= 1.5:
        return 0.75

    if ratio >= 1.2:
        return 0.50

    if ratio >= 1.0:
        return 0.25

    return 0.0


# ============================================================
# SWING STRUCTURAL SCORE
# ============================================================

def structural_score(
    candles: List[Any],
    swing: Dict[str, Any],
    current_price: float,
) -> float:

    index = _i(
        swing.get("index")
    )

    price = _f(
        swing.get("price")
    )

    if (
        index is None
        or price is None
        or current_price <= 0
    ):
        return 0.0

    score = 0.0

    # --------------------------------------------------------
    # Base swing
    # --------------------------------------------------------

    score += 25.0

    # --------------------------------------------------------
    # Age
    # --------------------------------------------------------

    age = max(
        0,
        len(candles) - 1 - index,
    )

    age_ratio = min(
        age / AGE_MAX,
        1.0,
    )

    score += (
        15.0
        * (1.0 - age_ratio)
    )

    # --------------------------------------------------------
    # Volume
    # --------------------------------------------------------

    score += (
        _volume_strength(
            candles,
            index,
        )
        * 15.0
    )

    # --------------------------------------------------------
    # Impulse
    # --------------------------------------------------------

    score += (
        _impulse_strength(
            candles,
            index,
        )
        * 15.0
    )

    # --------------------------------------------------------
    # Body quality
    # --------------------------------------------------------

    score += (
        _body_ratio(
            candles[index]
        )
        * 10.0
    )

    # --------------------------------------------------------
    # Distance
    #
    # Distance affects ranking, not existence.
    # --------------------------------------------------------

    distance = _distance_pct(
        price,
        current_price,
    )

    if distance <= 1.0:
        score += 20.0

    elif distance <= 3.0:
        score += 15.0

    elif distance <= 7.0:
        score += 10.0

    elif distance <= 15.0:
        score += 5.0

    return min(
        100.0,
        round(
            score,
            3,
        ),
    )


# ============================================================
# CLUSTER SWINGS
# ============================================================

def cluster_swings(
    swings: List[Dict[str, Any]],
    distance_pct: float = CLUSTER_DISTANCE_PCT,
) -> List[Dict[str, Any]]:

    if not swings:
        return []

    valid = []

    for swing in swings:

        price = _f(
            swing.get("price")
        )

        if price is None or price <= 0:
            continue

        valid.append(
            swing
        )

    if not valid:
        return []

    valid.sort(
        key=lambda x: x["price"]
    )

    clusters = []

    for swing in valid:

        if not clusters:

            clusters.append(
                [swing]
            )

            continue

        previous = clusters[-1][-1]

        previous_price = _f(
            previous.get("price")
        )

        current_price = _f(
            swing.get("price")
        )

        if (
            previous_price is None
            or current_price is None
        ):
            clusters.append(
                [swing]
            )
            continue

        distance = _distance_pct(
            current_price,
            previous_price,
        )

        if distance <= distance_pct:

            clusters[-1].append(
                swing
            )

        else:

            clusters.append(
                [swing]
            )

    result = []

    for cluster in clusters:

        prices = [
            _f(x.get("price"))
            for x in cluster
        ]

        prices = [
            x for x in prices
            if x is not None
        ]

        if not prices:
            continue

        indices = [
            _i(x.get("index"))
            for x in cluster
        ]

        indices = [
            x for x in indices
            if x is not None
        ]

        if not indices:
            continue

        scores = [
            _f(
                x.get(
                    "structural_score"
                )
            ) or 0.0
            for x in cluster
        ]

        average_price = (
            sum(prices)
            / len(prices)
        )

        result.append(
            {
                "price": average_price,

                "members": prices,

                "touches": len(
                    cluster
                ),

                "cluster_size": len(
                    cluster
                ),

                "first_index": min(
                    indices
                ),

                "last_index": max(
                    indices
                ),

                "max_structural_score": max(
                    scores,
                    default=0.0,
                ),

                "average_structural_score": (
                    sum(scores)
                    / len(scores)
                    if scores
                    else 0.0
                ),

                "open_time": max(
                    [
                        x.get("open_time")
                        for x in cluster
                        if x.get("open_time")
                        is not None
                    ],
                    default=None,
                ),
            }
        )

    return result


# ============================================================
# CLUSTER STRENGTH
# ============================================================

def cluster_strength(
    cluster: Dict[str, Any],
) -> float:

    if not isinstance(
        cluster,
        dict,
    ):
        return 0.0

    touches = _f(
        cluster.get(
            "touches"
        )
    ) or 1.0

    cluster_size = _f(
        cluster.get(
            "cluster_size"
        )
    ) or 1.0

    structural = _f(
        cluster.get(
            "max_structural_score"
        )
    ) or 0.0

    average_structural = _f(
        cluster.get(
            "average_structural_score"
        )
    ) or 0.0

    # Multiple reactions at approximately
    # the same price are important.
    touch_score = min(
        touches,
        6.0,
    ) / 6.0

    cluster_score = min(
        cluster_size,
        6.0,
    ) / 6.0

    structural_score_value = (
        structural / 100.0
    )

    average_score_value = (
        average_structural / 100.0
    )

    strength = (
        touch_score * 35.0
        + cluster_score * 20.0
        + structural_score_value * 30.0
        + average_score_value * 15.0
    )

    return round(
        min(
            100.0,
            strength,
        ),
        3,
    )


# ============================================================
# AGE / FRESHNESS
# ============================================================

def level_age(
    cluster: Dict[str, Any],
    candles: List[Any],
) -> int:

    last_index = _i(
        cluster.get(
            "last_index"
        )
    )

    if last_index is None:
        return len(candles)

    return max(
        0,
        len(candles)
        - 1
        - last_index,
    )


def freshness(
    age: int,
) -> float:

    age = max(
        0,
        int(age),
    )

    if age <= 3:
        return 1.0

    if age <= 10:
        return 0.85

    if age <= 30:
        return 0.65

    if age <= 60:
        return 0.45

    if age <= 120:
        return 0.30

    return 0.20


def freshness_label(
    age: int,
) -> str:

    if age <= 3:
        return "FRESH"

    if age <= 10:
        return "RECENT"

    if age <= 30:
        return "NORMAL"

    if age <= 60:
        return "OLD"

    return "VERY_OLD"


# ============================================================
# LIQUIDITY POOL BUILDER
# ============================================================

def _build_pool(
    cluster: Dict[str, Any],
    candles: List[Any],
    current_price: float,
    side: str,
) -> Dict[str, Any]:

    price = _f(
        cluster.get(
            "price"
        )
    )

    if price is None:
        raise ValueError(
            "Liquidity cluster has no price."
        )

    age = level_age(
        cluster,
        candles,
    )

    fresh = freshness(
        age
    )

    strength = cluster_strength(
        cluster
    )

    distance = _distance_pct(
        price,
        current_price,
    )

    structural = _f(
        cluster.get(
            "max_structural_score"
        )
    ) or 0.0

    # --------------------------------------------------------
    # Priority
    # --------------------------------------------------------

    distance_component = (
        1.0
        / (
            1.0
            + distance
        )
    )

    priority = (
        strength * 0.45
        + fresh * 100.0 * 0.20
        + structural * 0.20
        + distance_component * 100.0 * 0.15
    )

    priority = round(
        min(
            100.0,
            priority,
        ),
        3,
    )

    pool = {
        "price": round(
            price,
            8,
        ),

        "side": side,

        "type": (
            "BSL_1H"
            if side == "BSL"
            else "SSL_1H"
        ),

        "source": "1H",

        "timeframe": "1H",

        "open_time": cluster.get(
            "open_time"
        ),

        "index": cluster.get(
            "last_index"
        ),

        "first_index": cluster.get(
            "first_index"
        ),

        "last_index": cluster.get(
            "last_index"
        ),

        "touches": int(
            cluster.get(
                "touches",
                1,
            )
        ),

        "cluster_size": int(
            cluster.get(
                "cluster_size",
                1,
            )
        ),

        "members": list(
            cluster.get(
                "members",
                [],
            )
        ),

        "age": age,

        "freshness": fresh,

        "freshness_label": freshness_label(
            age
        ),

        "structural_score": round(
            structural,
            3,
        ),

        "strength": round(
            strength,
            3,
        ),

        "distance_pct": round(
            distance,
            5,
        ),

        "priority": priority,

        # These fields intentionally remain false.
        # Sweep detection belongs to strategy.py.
        "swept": False,

        "taken": False,

        "consumed": False,

        "recent_sweep": False,

        "recently_swept": False,
    }

    return pool


# ============================================================
# DEDUPLICATION
# ============================================================

def _deduplicate(
    levels: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    result = []

    for level in levels:

        price = _f(
            level.get("price")
        )

        if price is None:
            continue

        duplicate = False

        for existing in result:

            existing_price = _f(
                existing.get(
                    "price"
                )
            )

            if existing_price is None:
                continue

            if (
                _distance_pct(
                    price,
                    existing_price,
                )
                <= DEDUP_DISTANCE_PCT
            ):

                duplicate = True

                # Keep the stronger pool.
                if (
                    level.get(
                        "priority",
                        0.0,
                    )
                    >
                    existing.get(
                        "priority",
                        0.0,
                    )
                ):

                    result.remove(
                        existing
                    )

                    result.append(
                        level
                    )

                break

        if not duplicate:

            result.append(
                level
            )

    return result


# ============================================================
# MAIN ENGINE
# ============================================================

def detect_major_liquidity(
    candles_1h: List[Any],
    current_price: float,
    max_bsl: int = MAX_BSL,
    max_ssl: int = MAX_SSL,
) -> Dict[str, Any]:

    """
    Main public engine.

    Returns:

    {
        "source": "1H_ONLY",
        "timeframe": "1H",
        "price": ...,
        "BSL": [...],
        "SSL": [...],
        "all": [...],
        "diagnostics": {...}
    }
    """

    price = _f(
        current_price
    )

    if price is None or price <= 0:

        return {
            "source": "1H_ONLY",
            "timeframe": "1H",
            "price": None,
            "BSL": [],
            "SSL": [],
            "all": [],
            "diagnostics": {
                "error": "invalid_current_price",
            },
        }

    candles = confirmed_candles(
        candles_1h or []
    )

    if len(candles) < 10:

        return {
            "source": "1H_ONLY",
            "timeframe": "1H",
            "price": price,
            "BSL": [],
            "SSL": [],
            "all": [],
            "diagnostics": {
                "error": "not_enough_1h_candles",
                "candles": len(candles),
            },
        }

    # ========================================================
    # SWINGS
    # ========================================================

    swing_highs = detect_swing_highs(
        candles
    )

    swing_lows = detect_swing_lows(
        candles
    )

    # ========================================================
    # STRONG SWINGS
    # ========================================================

    strong_highs = detect_strong_swing_highs(
        candles
    )

    strong_lows = detect_strong_swing_lows(
        candles
    )

    strong_high_indices = {
        x.get("index")
        for x in strong_highs
    }

    strong_low_indices = {
        x.get("index")
        for x in strong_lows
    }

    # ========================================================
    # SCORE SWINGS
    # ========================================================

    scored_highs = []

    for swing in swing_highs:

        swing = dict(
            swing
        )

        index = swing.get(
            "index"
        )

        score = structural_score(
            candles,
            swing,
            price,
        )

        # Strong swing bonus.
        if index in strong_high_indices:

            score = min(
                100.0,
                score + 15.0,
            )

            swing[
                "strong_swing"
            ] = True

        else:

            swing[
                "strong_swing"
            ] = False

        swing[
            "structural_score"
        ] = score

        if score >= MIN_STRUCTURAL_SCORE:

            scored_highs.append(
                swing
            )

    scored_lows = []

    for swing in swing_lows:

        swing = dict(
            swing
        )

        index = swing.get(
            "index"
        )

        score = structural_score(
            candles,
            swing,
            price,
        )

        if index in strong_low_indices:

            score = min(
                100.0,
                score + 15.0,
            )

            swing[
                "strong_swing"
            ] = True

        else:

            swing[
                "strong_swing"
            ] = False

        swing[
            "structural_score"
        ] = score

        if score >= MIN_STRUCTURAL_SCORE:

            scored_lows.append(
                swing
            )

    # ========================================================
    # GEOMETRY FILTER
    # ========================================================

    # BSL must be ABOVE current price.
    bsl_candidates = [
        x
        for x in scored_highs
        if (
            _f(x.get("price"))
            is not None
            and _f(x.get("price"))
            > price
        )
    ]

    # SSL must be BELOW current price.
    ssl_candidates = [
        x
        for x in scored_lows
        if (
            _f(x.get("price"))
            is not None
            and _f(x.get("price"))
            < price
        )
    ]

    # ========================================================
    # CLUSTER
    # ========================================================

    bsl_clusters = cluster_swings(
        bsl_candidates
    )

    ssl_clusters = cluster_swings(
        ssl_candidates
    )

    # ========================================================
    # BUILD POOLS
    # ========================================================

    bsl = []

    for cluster in bsl_clusters:

        pool = _build_pool(
            cluster,
            candles,
            price,
            "BSL",
        )

        bsl.append(
            pool
        )

    ssl = []

    for cluster in ssl_clusters:

        pool = _build_pool(
            cluster,
            candles,
            price,
            "SSL",
        )

        ssl.append(
            pool
        )

    # ========================================================
    # DEDUP
    # ========================================================

    bsl = _deduplicate(
        bsl
    )

    ssl = _deduplicate(
        ssl
    )

    # ========================================================
    # SORT
    # ========================================================

    # Priority determines which levels are considered
    # structurally meaningful.
    #
    # This does NOT mean the nearest level is automatically
    # the best trading target.
    #
    # strategy.py can select the next valid Major level
    # by price distance.

    bsl.sort(
        key=lambda x: (
            x.get(
                "priority",
                0.0,
            ),
            x.get(
                "strength",
                0.0,
            ),
            -x.get(
                "distance_pct",
                999.0,
            ),
        ),
        reverse=True,
    )

    ssl.sort(
        key=lambda x: (
            x.get(
                "priority",
                0.0,
            ),
            x.get(
                "strength",
                0.0,
            ),
            -x.get(
                "distance_pct",
                999.0,
            ),
        ),
        reverse=True,
    )

    # ========================================================
    # LIMIT
    # ========================================================

    bsl = bsl[
        :max(
            1,
            int(max_bsl),
        )
    ]

    ssl = ssl[
        :max(
            1,
            int(max_ssl),
        )
    ]

    # ========================================================
    # COMBINED
    # ========================================================

    combined = (
        bsl
        + ssl
    )

    combined.sort(
        key=lambda x: (
            x.get(
                "priority",
                0.0,
            ),
            x.get(
                "strength",
                0.0,
            ),
        ),
        reverse=True,
    )

    combined = combined[
        :MAX_TOTAL
    ]

    # ========================================================
    # DIAGNOSTICS
    # ========================================================

    diagnostics = {
        "engine_version":
            LIQUIDITY_ENGINE_VERSION,

        "candles_1h":
            len(candles),

        "raw_swing_highs":
            len(swing_highs),

        "raw_swing_lows":
            len(swing_lows),

        "strong_swing_highs":
            len(strong_highs),

        "strong_swing_lows":
            len(strong_lows),

        "scored_bsl_candidates":
            len(bsl_candidates),

        "scored_ssl_candidates":
            len(ssl_candidates),

        "bsl_clusters":
            len(bsl_clusters),

        "ssl_clusters":
            len(ssl_clusters),

        "final_bsl":
            len(bsl),

        "final_ssl":
            len(ssl),

        "geometry_rule":
            "BSL > current / SSL < current",

        "major_source":
            "1H_ONLY",
    }

    return {
        "engine_version":
            LIQUIDITY_ENGINE_VERSION,

        "source":
            "1H_ONLY",

        "timeframe":
            "1H",

        "price":
            price,

        "BSL":
            bsl,

        "SSL":
            ssl,

        "all":
            combined,

        "diagnostics":
            diagnostics,
    }


# ============================================================
# SIMPLE API
# ============================================================

def get_major_bsl(
    candles_1h: List[Any],
    current_price: float,
    limit: int = MAX_BSL,
) -> List[Dict[str, Any]]:

    result = detect_major_liquidity(
        candles_1h,
        current_price,
        max_bsl=limit,
        max_ssl=MAX_SSL,
    )

    return result.get(
        "BSL",
        [],
    )


def get_major_ssl(
    candles_1h: List[Any],
    current_price: float,
    limit: int = MAX_SSL,
) -> List[Dict[str, Any]]:

    result = detect_major_liquidity(
        candles_1h,
        current_price,
        max_bsl=MAX_BSL,
        max_ssl=limit,
    )

    return result.get(
        "SSL",
        [],
    )


def get_major_levels(
    candles_1h: List[Any],
    current_price: float,
    limit: int = 12,
) -> List[Dict[str, Any]]:

    result = detect_major_liquidity(
        candles_1h,
        current_price,
        max_bsl=max(
            1,
            limit // 2,
        ),
        max_ssl=max(
            1,
            limit // 2,
        ),
    )

    return result.get(
        "all",
        [],
    )[:limit]


# ============================================================
# DIRECTION-AWARE API
# ============================================================

def get_liquidity_for_direction(
    candles_1h: List[Any],
    current_price: float,
    direction: str,
    limit: int = 8,
) -> List[Dict[str, Any]]:

    direction = str(
        direction
    ).upper()

    result = detect_major_liquidity(
        candles_1h,
        current_price,
        max_bsl=MAX_BSL,
        max_ssl=MAX_SSL,
    )

    if direction == "LONG":

        # LONG manipulates SSL.
        return result.get(
            "SSL",
            [],
        )[:limit]

    if direction == "SHORT":

        # SHORT manipulates BSL.
        return result.get(
            "BSL",
            [],
        )[:limit]

    return []


# ============================================================
# TARGET API
# ============================================================

def get_next_target(
    candles_1h: List[Any],
    current_price: float,
    direction: str,
    exclude_price: Optional[float] = None,
) -> Optional[Dict[str, Any]]:

    price = _f(
        current_price
    )

    if price is None:
        return None

    direction = str(
        direction
    ).upper()

    result = detect_major_liquidity(
        candles_1h,
        price,
    )

    if direction == "LONG":

        levels = result.get(
            "BSL",
            [],
        )

        candidates = []

        for level in levels:

            lp = _f(
                level.get("price")
            )

            if lp is None:
                continue

            if lp <= price:
                continue

            if exclude_price is not None:

                if (
                    _distance_pct(
                        lp,
                        exclude_price,
                    )
                    <= DEDUP_DISTANCE_PCT
                ):
                    continue

            candidates.append(
                level
            )

        if not candidates:
            return None

        return min(
            candidates,
            key=lambda x: _f(
                x.get("price")
            ) or 999999999,
        )

    if direction == "SHORT":

        levels = result.get(
            "SSL",
            [],
        )

        candidates = []

        for level in levels:

            lp = _f(
                level.get("price")
            )

            if lp is None:
                continue

            if lp >= price:
                continue

            if exclude_price is not None:

                if (
                    _distance_pct(
                        lp,
                        exclude_price,
                    )
                    <= DEDUP_DISTANCE_PCT
                ):
                    continue

            candidates.append(
                level
            )

        if not candidates:
            return None

        return min(
            candidates,
            key=lambda x: abs(
                (
                    _f(
                        x.get("price")
                    )
                    or 0.0
                )
                - price
            ),
        )

    return None


# ============================================================
# DEBUG
# ============================================================

def debug_liquidity(
    candles_1h: List[Any],
    current_price: float,
) -> Dict[str, Any]:

    result = detect_major_liquidity(
        candles_1h,
        current_price,
    )

    return {
        "engine_version":
            LIQUIDITY_ENGINE_VERSION,

        "price":
            current_price,

        "source":
            "1H_ONLY",

        "BSL":
            result.get(
                "BSL",
                [],
            ),

        "SSL":
            result.get(
                "SSL",
                [],
            ),

        "diagnostics":
            result.get(
                "diagnostics",
                {},
            ),
    }


# ============================================================
# FORMAT
# ============================================================

def format_liquidity(
    result: Dict[str, Any],
) -> str:

    if not isinstance(
        result,
        dict,
    ):
        return "❌ Некорректные данные."

    price = _f(
        result.get(
            "price"
        )
    )

    bsl = result.get(
        "BSL",
        [],
    )

    ssl = result.get(
        "SSL",
        [],
    )

    lines = []

    lines.append(
        "💧 MAJOR LIQUIDITY ENGINE"
    )

    lines.append(
        "Источник: 1H ONLY"
    )

    if price is not None:

        lines.append(
            f"Цена: ${price:.6f}"
        )

    lines.append("")

    # --------------------------------------------------------
    # BSL
    # --------------------------------------------------------

    lines.append(
        "🔴 BSL · ВЫШЕ ЦЕНЫ"
    )

    if bsl:

        for level in bsl:

            p = _f(
                level.get(
                    "price"
                )
            )

            if p is None:
                continue

            distance = (
                _distance_pct(
                    p,
                    price,
                )
                if price
                else 0.0
            )

            strength = (
                _f(
                    level.get(
                        "strength"
                    )
                )
                or 0.0
            )

            touches = int(
                level.get(
                    "touches",
                    1,
                )
            )

            freshness = (
                level.get(
                    "freshness_label",
                    "?",
                )
            )

            lines.append(
                f"• ${p:.6f}"
                f" · {distance:.2f}%"
                f" · S{strength:.0f}"
                f" · {touches}T"
                f" · {freshness}"
            )

    else:

        lines.append(
            "• нет"
        )

    lines.append("")

    # --------------------------------------------------------
    # SSL
    # --------------------------------------------------------

    lines.append(
        "🟢 SSL · НИЖЕ ЦЕНЫ"
    )

    if ssl:

        for level in ssl:

            p = _f(
                level.get(
                    "price"
                )
            )

            if p is None:
                continue

            distance = (
                _distance_pct(
                    p,
                    price,
                )
                if price
                else 0.0
            )

            strength = (
                _f(
                    level.get(
                        "strength"
                    )
                )
                or 0.0
            )

            touches = int(
                level.get(
                    "touches",
                    1,
                )
            )

            freshness = (
                level.get(
                    "freshness_label",
                    "?",
                )
            )

            lines.append(
                f"• ${p:.6f}"
                f" · {distance:.2f}%"
                f" · S{strength:.0f}"
                f" · {touches}T"
                f" · {freshness}"
            )

    else:

        lines.append(
            "• нет"
        )

    lines.append("")

    # --------------------------------------------------------
    # DIAGNOSTICS
    # --------------------------------------------------------

    diagnostics = result.get(
        "diagnostics",
        {},
    )

    lines.append(
        "🔧 DEBUG"
    )

    lines.append(
        f"1H candles: "
        f"{diagnostics.get('candles_1h', 0)}"
    )

    lines.append(
        f"Swing highs: "
        f"{diagnostics.get('raw_swing_highs', 0)}"
    )

    lines.append(
        f"Swing lows: "
        f"{diagnostics.get('raw_swing_lows', 0)}"
    )

    lines.append(
        f"BSL final: "
        f"{diagnostics.get('final_bsl', 0)}"
    )

    lines.append(
        f"SSL final: "
        f"{diagnostics.get('final_ssl', 0)}"
    )

    return "\n".join(
        lines
    )


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "LIQUIDITY_ENGINE_VERSION",

    "detect_swing_highs",
    "detect_swing_lows",

    "detect_strong_swing_highs",
    "detect_strong_swing_lows",

    "cluster_swings",
    "cluster_strength",

    "structural_score",

    "detect_major_liquidity",

    "get_major_bsl",
    "get_major_ssl",
    "get_major_levels",

    "get_liquidity_for_direction",

    "get_next_target",

    "debug_liquidity",

    "format_liquidity",
]


# ============================================================
# CLI TEST
# ============================================================

if __name__ == "__main__":

    import json
    import sys

    print(
        "TradeMind Liquidity Engine "
        f"{LIQUIDITY_ENGINE_VERSION}"
    )

    print(
        ""
    )

    print(
        "Этот файл предназначен для "
        "использования из market.py / bot.py."
    )

    if len(sys.argv) > 1:

        print(
            "Symbol argument received:",
            sys.argv[1],
        )

    print(
        json.dumps(
            {
                "status": "OK",
                "engine":
                    LIQUIDITY_ENGINE_VERSION,
                "source":
                    "1H_ONLY",
            },
            ensure_ascii=False,
            indent=2,
        )
    )