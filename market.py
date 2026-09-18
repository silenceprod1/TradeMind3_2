"""
TradeMind 6.5
Market Data + Dynamic Major Liquidity

Reference:
Binance Spot

Hierarchy:

1H candles
    ↓
1H swing highs / lows
    ↓
cluster nearby swings
    ↓
structural scoring
    ↓
freshness / touches
    ↓
remove weak intermediate levels
    ↓
Major BSL + Major SSL

15M / 5M / 1M:
    ONLY reinforce existing 1H zones.
    They NEVER create Major Liquidity themselves.
"""

import requests


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://api.binance.com/api/v3"

SYMBOL = "SOLUSDT"

TIMEOUT = 10


# ------------------------------------------------------------
# Liquidity settings
# ------------------------------------------------------------

# Swings closer than this are considered one liquidity cluster.
CLUSTER_DISTANCE_PCT = 0.15

# Maximum width of a liquidity zone.
ZONE_WIDTH_PCT = 0.20

# Minimum distance between two separate Major zones.
MIN_ZONE_GAP_PCT = 0.70

# Number of Major zones per side.
MAX_LEVELS_PER_SIDE = 4

# Total maximum returned.
MAX_TOTAL_LEVELS = 8

# Local timeframe reinforcement.
LOCAL_TOUCH_DISTANCE_PCT = 0.20

# A level with too little structural importance is ignored.
MIN_MAJOR_STRENGTH = 58

# How many 1H candles are considered for structural liquidity.
LOOKBACK_1H = 180

# Don't keep very old zones indefinitely.
MAX_LEVEL_AGE_1H = 120


# ============================================================
# BINANCE REQUEST
# ============================================================

def _get(path, params):

    response = requests.get(
        BASE_URL + path,
        params=params,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# PRICE
# ============================================================

def get_price(symbol=SYMBOL):

    data = _get(
        "/ticker/price",
        {
            "symbol": symbol,
        },
    )

    return float(data["price"])


# ============================================================
# KLINES
# ============================================================

def get_klines(
    symbol=SYMBOL,
    interval="1h",
    limit=200,
):

    limit = max(
        1,
        min(int(limit), 1000),
    )

    raw = _get(
        "/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        },
    )

    candles = []

    for x in raw:

        candles.append(
            {
                "open_time": int(x[0]),
                "open": float(x[1]),
                "high": float(x[2]),
                "low": float(x[3]),
                "close": float(x[4]),
                "volume": float(x[5]),
                "close_time": int(x[6]),
            }
        )

    return candles


# ============================================================
# MARKET DATA
# ============================================================

def get_market_data(symbol=SYMBOL):

    candles_1h = get_klines(
        symbol,
        "1h",
        200,
    )

    candles_15m = get_klines(
        symbol,
        "15m",
        200,
    )

    candles_5m = get_klines(
        symbol,
        "5m",
        200,
    )

    candles_1m = get_klines(
        symbol,
        "1m",
        200,
    )

    price = get_price(symbol)

    major_levels = find_major_liquidity(
        candles_1h=candles_1h,
        current_price=price,
        max_levels=MAX_TOTAL_LEVELS,
        candles_15m=candles_15m,
        candles_5m=candles_5m,
        candles_1m=candles_1m,
    )

    return {
        "symbol": symbol,
        "price": price,

        "candles_1h": candles_1h,
        "candles_15m": candles_15m,
        "candles_5m": candles_5m,
        "candles_1m": candles_1m,

        "major_liquidity": major_levels,
    }


# ============================================================
# SWINGS
# ============================================================

def _swing_high(candles, i):

    if i < 2:
        return False

    if i >= len(candles) - 2:
        return False

    current = candles[i]["high"]

    left_1 = candles[i - 1]["high"]
    left_2 = candles[i - 2]["high"]

    right_1 = candles[i + 1]["high"]
    right_2 = candles[i + 2]["high"]

    return (
        current > left_1
        and current >= left_2
        and current >= right_1
        and current > right_2
    )


def _swing_low(candles, i):

    if i < 2:
        return False

    if i >= len(candles) - 2:
        return False

    current = candles[i]["low"]

    left_1 = candles[i - 1]["low"]
    left_2 = candles[i - 2]["low"]

    right_1 = candles[i + 1]["low"]
    right_2 = candles[i + 2]["low"]

    return (
        current < left_1
        and current <= left_2
        and current <= right_1
        and current < right_2
    )


# ============================================================
# COLLECT 1H SWINGS
# ============================================================

def _collect_swings(candles):

    highs = []
    lows = []

    if not candles:
        return highs, lows

    candles = candles[-LOOKBACK_1H:]

    for i in range(len(candles)):

        if _swing_high(candles, i):

            highs.append(
                {
                    "price": candles[i]["high"],
                    "index": i,
                    "type": "BSL",
                }
            )

        if _swing_low(candles, i):

            lows.append(
                {
                    "price": candles[i]["low"],
                    "index": i,
                    "type": "SSL",
                }
            )

    return highs, lows


# ============================================================
# CLUSTER SWINGS
# ============================================================

def _cluster_swings(
    points,
    distance_pct=CLUSTER_DISTANCE_PCT,
):

    if not points:
        return []

    points = sorted(
        points,
        key=lambda x: x["price"],
    )

    clusters = []

    for point in points:

        price = point["price"]

        if not clusters:

            clusters.append(
                {
                    "points": [point],
                    "price": price,
                }
            )

            continue

        current = clusters[-1]

        center = current["price"]

        distance = (
            abs(price - center)
            / center
            * 100
        )

        if distance <= distance_pct:

            current["points"].append(point)

            prices = [
                x["price"]
                for x in current["points"]
            ]

            current["price"] = (
                sum(prices)
                / len(prices)
            )

        else:

            clusters.append(
                {
                    "points": [point],
                    "price": price,
                }
            )

    return clusters


# ============================================================
# LOCAL TOUCHES
# ============================================================

def _local_touches(
    candles,
    level,
    distance_pct=LOCAL_TOUCH_DISTANCE_PCT,
):

    if not candles:
        return 0

    count = 0

    for candle in candles[-160:]:

        high = candle["high"]
        low = candle["low"]

        high_distance = (
            abs(high - level)
            / level
            * 100
        )

        low_distance = (
            abs(low - level)
            / level
            * 100
        )

        if (
            high_distance <= distance_pct
            or low_distance <= distance_pct
        ):
            count += 1

    return count


# ============================================================
# SWEEPED / TAKEN
# ============================================================

def _level_has_been_swept(
    level,
    candles_1h,
    current_price,
):

    price = level["price"]
    level_type = level["type"]

    if not candles_1h:
        return False

    # We intentionally don't use the latest candle
    # as a definitive sweep if it is still forming.
    closed = candles_1h[:-1]

    # Search recent history after the swing.
    start_index = max(
        0,
        level["index"] - 2,
    )

    relevant = closed[start_index:]

    for candle in relevant:

        high = candle["high"]
        low = candle["low"]
        close = candle["close"]

        # ----------------------------------------------------
        # BSL
        # ----------------------------------------------------

        if level_type == "BSL":

            if (
                high > price
                and close < price
            ):
                return True

        # ----------------------------------------------------
        # SSL
        # ----------------------------------------------------

        elif level_type == "SSL":

            if (
                low < price
                and close > price
            ):
                return True

    return False


# ============================================================
# FRESHNESS
# ============================================================

def _freshness_score(
    index,
    candle_count,
):

    age = (
        candle_count
        - 1
        - index
    )

    if age <= 8:
        return 20

    if age <= 20:
        return 16

    if age <= 40:
        return 12

    if age <= 80:
        return 7

    return 2


# ============================================================
# STRUCTURAL SCORE
# ============================================================

def _cluster_strength(
    cluster,
    candles_1h,
    candles_15m=None,
    candles_5m=None,
    candles_1m=None,
):

    points = cluster["points"]

    touches = len(points)

    latest_index = max(
        p["index"]
        for p in points
    )

    freshness = _freshness_score(
        latest_index,
        len(candles_1h),
    )

    # --------------------------------------------------------
    # Base structural score
    # --------------------------------------------------------

    # 50 base
    # + repeated swings
    # + freshness
    # + local reinforcement

    strength = 50

    # Repeated 1H swing touches.
    strength += min(
        18,
        max(0, touches - 1) * 6,
    )

    # Freshness.
    strength += freshness

    # Local reinforcement.
    level_price = cluster["price"]

    local = (
        _local_touches(
            candles_15m,
            level_price,
        )
        + _local_touches(
            candles_5m,
            level_price,
        )
    )

    strength += min(
        12,
        local,
    )

    return min(
        100,
        round(strength),
    )


# ============================================================
# ZONE SELECTION
# ============================================================

def _select_major_zones(
    candidates,
    current_price,
    max_levels,
):

    if not candidates:
        return []

    # Strongest first.
    candidates = sorted(
        candidates,
        key=lambda x: (
            -x["strength"],
            -x["touches"],
            -x["freshness"],
        ),
    )

    selected = []

    for candidate in candidates:

        if len(selected) >= max_levels:
            break

        price = candidate["price"]

        # ----------------------------------------------------
        # Don't keep levels too close to current price
        # if they are obviously intermediate noise.
        #
        # Very close levels can still be useful,
        # so we only reject them if another selected
        # zone already covers the same area.
        # ----------------------------------------------------

        duplicate = False

        for existing in selected:

            distance = (
                abs(price - existing["price"])
                / existing["price"]
                * 100
            )

            if distance < MIN_ZONE_GAP_PCT:

                duplicate = True
                break

        if duplicate:
            continue

        selected.append(candidate)

    # Sort by actual price.
    selected.sort(
        key=lambda x: x["price"]
    )

    return selected


# ============================================================
# MAJOR LIQUIDITY
# ============================================================

def find_major_liquidity(
    candles_1h,
    current_price,
    max_levels=MAX_TOTAL_LEVELS,
    candles_15m=None,
    candles_5m=None,
    candles_1m=None,
    include_swept=False,
):

    """
    Только 1H создаёт Major Liquidity.

    15M/5M/1M:
        только усиливают уже существующие 1H зоны.

    Возвращаем:
        BSL above price
        SSL below price

    Динамически пересчитывается каждый вызов.
    """

    if (
        not candles_1h
        or len(candles_1h) < 20
    ):
        return []

    price = float(current_price)

    # --------------------------------------------------------
    # Exclude currently forming 1H candle.
    # --------------------------------------------------------

    closed = candles_1h[:-1]

    if len(closed) < 10:
        return []

    # --------------------------------------------------------
    # Collect raw swings.
    # --------------------------------------------------------

    highs, lows = _collect_swings(
        closed
    )

    # --------------------------------------------------------
    # Cluster independently:
    #
    # BSL with BSL
    # SSL with SSL
    # --------------------------------------------------------

    bsl_clusters = _cluster_swings(
        highs
    )

    ssl_clusters = _cluster_swings(
        lows
    )

    candidates = []

    # ========================================================
    # BSL
    # ========================================================

    for cluster in bsl_clusters:

        level_price = cluster["price"]

        # BSL must currently be ABOVE price.
        if level_price <= price:
            continue

        points = cluster["points"]

        latest_index = max(
            p["index"]
            for p in points
        )

        age = (
            len(closed)
            - 1
            - latest_index
        )

        if age > MAX_LEVEL_AGE_1H:
            continue

        touches = len(points)

        local_touches = (
            _local_touches(
                candles_15m,
                level_price,
            )
            + _local_touches(
                candles_5m,
                level_price,
            )
            + _local_touches(
                candles_1m,
                level_price,
            )
        )

        freshness = _freshness_score(
            latest_index,
            len(closed),
        )

        strength = _cluster_strength(
            cluster,
            closed,
            candles_15m,
            candles_5m,
            candles_1m,
        )

        swept = _level_has_been_swept(
            {
                "price": level_price,
                "type": "BSL",
                "index": latest_index,
            },
            closed,
            price,
        )

        if swept and not include_swept:
            continue

        if strength < MIN_MAJOR_STRENGTH:
            continue

        candidates.append(
            {
                "price": level_price,
                "side": "SHORT",
                "type": "BSL",
                "kind": "1H MAJOR BSL",

                "touches": touches,

                "local_touches": local_touches,

                "strength": strength,

                "freshness": freshness,

                "age_1h": age,

                "swept": swept,

                "taken": swept,

                "zone_low": (
                    level_price
                    * (
                        1
                        - ZONE_WIDTH_PCT
                        / 100
                    )
                ),

                "zone_high": (
                    level_price
                    * (
                        1
                        + ZONE_WIDTH_PCT
                        / 100
                    )
                ),

                "index": latest_index,
            }
        )

    # ========================================================
    # SSL
    # ========================================================

    for cluster in ssl_clusters:

        level_price = cluster["price"]

        # SSL must currently be BELOW price.
        if level_price >= price:
            continue

        points = cluster["points"]

        latest_index = max(
            p["index"]
            for p in points
        )

        age = (
            len(closed)
            - 1
            - latest_index
        )

        if age > MAX_LEVEL_AGE_1H:
            continue

        touches = len(points)

        local_touches = (
            _local_touches(
                candles_15m,
                level_price,
            )
            + _local_touches(
                candles_5m,
                level_price,
            )
            + _local_touches(
                candles_1m,
                level_price,
            )
        )

        freshness = _freshness_score(
            latest_index,
            len(closed),
        )

        strength = _cluster_strength(
            cluster,
            closed,
            candles_15m,
            candles_5m,
            candles_1m,
        )

        swept = _level_has_been_swept(
            {
                "price": level_price,
                "type": "SSL",
                "index": latest_index,
            },
            closed,
            price,
        )

        if swept and not include_swept:
            continue

        if strength < MIN_MAJOR_STRENGTH:
            continue

        candidates.append(
            {
                "price": level_price,
                "side": "LONG",
                "type": "SSL",
                "kind": "1H MAJOR SSL",

                "touches": touches,

                "local_touches": local_touches,

                "strength": strength,

                "freshness": freshness,

                "age_1h": age,

                "swept": swept,

                "taken": swept,

                "zone_low": (
                    level_price
                    * (
                        1
                        - ZONE_WIDTH_PCT
                        / 100
                    )
                ),

                "zone_high": (
                    level_price
                    * (
                        1
                        + ZONE_WIDTH_PCT
                        / 100
                    )
                ),

                "index": latest_index,
            }
        )

    # ========================================================
    # Separate sides
    # ========================================================

    bsl = [
        x
        for x in candidates
        if x["type"] == "BSL"
    ]

    ssl = [
        x
        for x in candidates
        if x["type"] == "SSL"
    ]

    # ========================================================
    # Select real Major zones
    # ========================================================

    selected_bsl = _select_major_zones(
        bsl,
        price,
        MAX_LEVELS_PER_SIDE,
    )

    selected_ssl = _select_major_zones(
        ssl,
        price,
        MAX_LEVELS_PER_SIDE,
    )

    result = (
        selected_bsl
        + selected_ssl
    )

    # ========================================================
    # Sort by distance from current price
    # ========================================================

    result.sort(
        key=lambda x: abs(
            x["price"] - price
        )
    )

    return result[:max_levels]


# ============================================================
# COMPATIBILITY
# ============================================================

def get_fresh_liquidity(
    *args,
    **kwargs,
):

    return find_major_liquidity(
        *args,
        **kwargs,
    )


def get_all_major_liquidity(
    *args,
    **kwargs,
):

    return find_major_liquidity(
        *args,
        **kwargs,
    )


# ============================================================
# TARGET
# ============================================================

def get_target_liquidity(
    major_levels,
    current_price,
    direction,
    exclude_level=None,
):

    price = float(current_price)

    excluded = (
        float(exclude_level)
        if exclude_level is not None
        else None
    )

    candidates = []

    for level in major_levels or []:

        if _level_has_been_swept_local(level):
            continue

        level_price = _level_price(level)

        if level_price is None:
            continue

        if excluded is not None:

            if (
                abs(
                    level_price
                    - excluded
                )
                / excluded
                * 100
                < 0.05
            ):
                continue

        if direction == "LONG":

            if level_price > price:
                candidates.append(level)

        elif direction == "SHORT":

            if level_price < price:
                candidates.append(level)

    if not candidates:
        return None

    return min(
        candidates,
        key=lambda x: abs(
            _level_price(x) - price
        ),
    )


def _level_has_been_swept_local(level):

    if not isinstance(level, dict):
        return False

    return bool(
        level.get("swept")
        or level.get("taken")
        or level.get("used")
        or level.get("consumed")
    )


# ============================================================
# SWEEP COMPATIBILITY
# ============================================================

def detect_sweep(
    candles_1h,
    current_price,
    direction=None,
    major_levels=None,
):

    from strategy import (
        get_1h_direction,
        find_sweep,
    )

    if direction is None:

        direction = get_1h_direction(
            candles_1h
        )

    if major_levels is None:

        major_levels = find_major_liquidity(
            candles_1h,
            current_price,
            MAX_TOTAL_LEVELS,
        )

    return find_sweep(
        candles_1h,
        major_levels,
        direction,
    )


detect_fresh_sweep = detect_sweep


# ============================================================
# DEBUG
# ============================================================

def format_major_liquidity(
    major_levels,
    current_price,
):

    lines = []

    price = float(current_price)

    bsl = [
        x
        for x in major_levels or []
        if x.get("type") == "BSL"
    ]

    ssl = [
        x
        for x in major_levels or []
        if x.get("type") == "SSL"
    ]

    bsl.sort(
        key=lambda x: x["price"]
    )

    ssl.sort(
        key=lambda x: x["price"],
        reverse=True,
    )

    lines.append(
        f"PRICE: ${price:.4f}"
    )

    lines.append("")
    lines.append("BSL:")

    for level in bsl:

        distance = (
            abs(
                level["price"]
                - price
            )
            / price
            * 100
        )

        lines.append(
            f"  ${level['price']:.4f}"
            f" | +{distance:.2f}%"
            f" | S{level['strength']}"
            f" | touches={level['touches']}"
            f" | age={level['age_1h']}H"
        )

    lines.append("")
    lines.append("SSL:")

    for level in ssl:

        distance = (
            abs(
                level["price"]
                - price
            )
            / price
            * 100
        )

        lines.append(
            f"  ${level['price']:.4f}"
            f" | -{distance:.2f}%"
            f" | S{level['strength']}"
            f" | touches={level['touches']}"
            f" | age={level['age_1h']}H"
        )

    return "\n".join(lines)