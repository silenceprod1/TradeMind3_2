"""
TradeMind 5.8 — Market Data

Source:
Binance Spot API.

Used as market/structure reference for:
D1 -> 1H -> 15M -> 5M.

The bot itself can trade on BingX separately.
"""

import requests
import time


BASE_URL = "https://api.binance.com/api/v3"

DEFAULT_SYMBOL = "SOLUSDT"

REQUEST_TIMEOUT = 10

# Percent cluster tolerance.
# Example:
# SOL 100.00 and 100.30 can belong to one pool.
LEVEL_CLUSTER_PCT = 0.35

MIN_MAJOR_TOUCHES = 2

MAX_MAJOR_LEVELS = 6

LIQUIDITY_LOOKBACK = 120

SWEEP_LOOKBACK_1H = 4

MIN_SWEEP_DEPTH_PCT = 0.08


# ---------------------------------------------------------
# HTTP
# ---------------------------------------------------------

def _get(path, params):
    response = requests.get(
        BASE_URL + path,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


# ---------------------------------------------------------
# PRICE
# ---------------------------------------------------------

def get_price(symbol=DEFAULT_SYMBOL):
    data = _get(
        "/ticker/price",
        {
            "symbol": symbol,
        },
    )

    return float(data["price"])


# ---------------------------------------------------------
# KLINES
# ---------------------------------------------------------

def get_klines(
    symbol=DEFAULT_SYMBOL,
    interval="1h",
    limit=200,
):
    raw = _get(
        "/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        },
    )

    candles = []

    for row in raw:

        candles.append({
            "open_time": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "close_time": int(row[6]),
        })

    # Binance includes the currently forming candle.
    # Strategy must work with CLOSED candles only.
    if len(candles) > 1:

        now_ms = int(time.time() * 1000)

        if candles[-1]["close_time"] > now_ms:
            candles = candles[:-1]

    return candles


# ---------------------------------------------------------
# MARKET DATA
# ---------------------------------------------------------

def get_market_data(symbol=DEFAULT_SYMBOL):

    price = get_price(symbol)

    return {
        "symbol": symbol,
        "price": price,

        "candles_d1": get_klines(
            symbol,
            "1d",
            200,
        ),

        "candles_w1": get_klines(
            symbol,
            "1w",
            100,
        ),

        "candles_1h": get_klines(
            symbol,
            "1h",
            200,
        ),

        "candles_15m": get_klines(
            symbol,
            "15m",
            200,
        ),

        "candles_5m": get_klines(
            symbol,
            "5m",
            200,
        ),
    }


# ---------------------------------------------------------
# LOCAL EXTREMES
# ---------------------------------------------------------

def _local_swing_high(
    candles,
    index,
):
    if index <= 0:
        return False

    if index >= len(candles) - 1:
        return False

    current = candles[index]["high"]

    left = candles[index - 1]["high"]
    right = candles[index + 1]["high"]

    return (
        current > left
        and current >= right
    )


def _local_swing_low(
    candles,
    index,
):
    if index <= 0:
        return False

    if index >= len(candles) - 1:
        return False

    current = candles[index]["low"]

    left = candles[index - 1]["low"]
    right = candles[index + 1]["low"]

    return (
        current < left
        and current <= right
    )


# ---------------------------------------------------------
# PERCENT DISTANCE
# ---------------------------------------------------------

def _pct_distance(a, b):

    if b in (
        None,
        0,
    ):
        return 999.0

    return abs(a - b) / abs(b) * 100.0


# ---------------------------------------------------------
# CLUSTER
# ---------------------------------------------------------

def _cluster_levels(
    items,
):
    """
    Items:
        {
            type: HIGH / LOW,
            price: float,
            index: int,
            time: int
        }

    HIGH and LOW are NEVER clustered together.
    """

    if not items:
        return []

    result = []

    # Separate sides first.
    for side in (
        "HIGH",
        "LOW",
    ):

        side_items = [
            x
            for x in items
            if x["type"] == side
        ]

        side_items.sort(
            key=lambda x: x["price"]
        )

        clusters = []

        for item in side_items:

            placed = False

            for cluster in clusters:

                distance = _pct_distance(
                    item["price"],
                    cluster["price"],
                )

                if distance <= LEVEL_CLUSTER_PCT:

                    cluster["members"].append(
                        item
                    )

                    cluster["price"] = (
                        sum(
                            x["price"]
                            for x in cluster["members"]
                        )
                        /
                        len(cluster["members"])
                    )

                    cluster["last_index"] = max(
                        cluster["last_index"],
                        item["index"],
                    )

                    placed = True
                    break

            if not placed:

                clusters.append({
                    "type": side,
                    "price": item["price"],
                    "members": [item],
                    "last_index": item["index"],
                })

        result.extend(
            clusters
        )

    return result


# ---------------------------------------------------------
# MAJOR LIQUIDITY
# ---------------------------------------------------------

def find_major_liquidity(
    candles_1h,
    current_price,
    max_levels=MAX_MAJOR_LEVELS,
):
    """
    Returns only meaningful 1H liquidity pools.

    HIGH above current -> SHORT sweep candidate.
    LOW below current -> LONG sweep candidate.

    Major liquidity:
    - local extrema from 3-candle structure;
    - repeated levels preferred;
    - HIGH/LOW never mixed;
    - already swept pools excluded.
    """

    if (
        not candles_1h
        or len(candles_1h) < 15
    ):
        return []

    data = candles_1h[
        -LIQUIDITY_LOOKBACK:
    ]

    raw = []

    for i in range(
        1,
        len(data) - 1,
    ):

        if _local_swing_high(
            data,
            i,
        ):

            raw.append({
                "type": "HIGH",
                "price": data[i]["high"],
                "index": i,
                "time": data[i]["open_time"],
            })

        if _local_swing_low(
            data,
            i,
        ):

            raw.append({
                "type": "LOW",
                "price": data[i]["low"],
                "index": i,
                "time": data[i]["open_time"],
            })

    clusters = _cluster_levels(raw)

    candidates = []

    for cluster in clusters:

        price = cluster["price"]

        touches = len(
            cluster["members"]
        )

        last_index = cluster[
            "last_index"
        ]

        # ---------------------------------------------
        # HIGH above price
        # ---------------------------------------------

        if (
            cluster["type"] == "HIGH"
            and price > current_price
        ):

            # Has this level already been taken?
            swept = False

            for candle in data[
                last_index + 1:
            ]:

                if candle["high"] > price:

                    swept = True
                    break

            if swept:
                continue

            strength = min(
                1.0,
                0.45
                + 0.15 * max(
                    0,
                    touches - 1,
                ),
            )

            candidates.append({
                "price": round(
                    price,
                    8,
                ),

                "level": round(
                    price,
                    8,
                ),

                "type": "HIGH",

                "side": "SHORT",

                "liquidity_type":
                    "BSL / 1H major swing high",

                "touches": touches,

                "strength": round(
                    strength,
                    3,
                ),

                "time": cluster[
                    "members"
                ][-1]["time"],
            })

        # ---------------------------------------------
        # LOW below price
        # ---------------------------------------------

        if (
            cluster["type"] == "LOW"
            and price < current_price
        ):

            swept = False

            for candle in data[
                last_index + 1:
            ]:

                if candle["low"] < price:

                    swept = True
                    break

            if swept:
                continue

            strength = min(
                1.0,
                0.45
                + 0.15 * max(
                    0,
                    touches - 1,
                ),
            )

            candidates.append({
                "price": round(
                    price,
                    8,
                ),

                "level": round(
                    price,
                    8,
                ),

                "type": "LOW",

                "side": "LONG",

                "liquidity_type":
                    "SSL / 1H major swing low",

                "touches": touches,

                "strength": round(
                    strength,
                    3,
                ),

                "time": cluster[
                    "members"
                ][-1]["time"],
            })

    # Prefer:
    # 1. repeated liquidity
    # 2. stronger liquidity
    # 3. nearest level

    candidates.sort(
        key=lambda x: (
            -x["touches"],
            -x["strength"],
            _pct_distance(
                x["price"],
                current_price,
            ),
        )
    )

    return candidates[
        :max_levels
    ]


# ---------------------------------------------------------
# SWEEP
# ---------------------------------------------------------

def detect_sweep(
    candles_1h,
    current_price,
    direction,
):
    """
    Detect fresh directional sweep.

    LONG:
        price takes LOW/SSL and rejects upward.

    SHORT:
        price takes HIGH/BSL and rejects downward.

    Only closed 1H candles are used.
    """

    if not candles_1h:
        return None

    levels = find_major_liquidity(
        candles_1h,
        current_price,
        max_levels=20,
    )

    if not levels:
        return None

    wanted_side = (
        "LONG"
        if direction == "LONG"
        else "SHORT"
    )

    data = candles_1h[
        -SWEEP_LOOKBACK_1H:
    ]

    # Check newest levels first.
    levels = sorted(
        levels,
        key=lambda x: x.get(
            "time",
            0,
        ),
        reverse=True,
    )

    for level_data in levels:

        if level_data["side"] != wanted_side:
            continue

        level = level_data["price"]

        for candle in data:

            # -----------------------------------------
            # LONG sweep
            # -----------------------------------------

            if direction == "LONG":

                depth = (
                    level - candle["low"]
                )

                depth_pct = (
                    depth / level * 100
                    if level
                    else 0
                )

                swept = (
                    candle["low"] < level
                    and depth_pct >= MIN_SWEEP_DEPTH_PCT
                )

                rejection = (
                    candle["close"] > level
                    and candle["close"] > candle["open"]
                )

                if swept and rejection:

                    return {
                        "swept": True,

                        "direction": "LONG",

                        "level": level,

                        "extreme": candle["low"],

                        "time": candle["open_time"],

                        "strength":
                            level_data["strength"],

                        "touches":
                            level_data["touches"],

                        "liquidity_type":
                            level_data[
                                "liquidity_type"
                            ],

                        "type": "SSL SWEEP",
                    }

            # -----------------------------------------
            # SHORT sweep
            # -----------------------------------------

            else:

                depth = (
                    candle["high"] - level
                )

                depth_pct = (
                    depth / level * 100
                    if level
                    else 0
                )

                swept = (
                    candle["high"] > level
                    and depth_pct >= MIN_SWEEP_DEPTH_PCT
                )

                rejection = (
                    candle["close"] < level
                    and candle["close"] < candle["open"]
                )

                if swept and rejection:

                    return {
                        "swept": True,

                        "direction": "SHORT",

                        "level": level,

                        "extreme": candle["high"],

                        "time": candle["open_time"],

                        "strength":
                            level_data["strength"],

                        "touches":
                            level_data["touches"],

                        "liquidity_type":
                            level_data[
                                "liquidity_type"
                            ],

                        "type": "BSL SWEEP",
                    }

    return None


# Compatibility alias
get_major_liquidity = find_major_liquidity


__all__ = [
    "get_price",
    "get_klines",
    "get_market_data",
    "find_major_liquidity",
    "get_major_liquidity",
    "detect_sweep",
]