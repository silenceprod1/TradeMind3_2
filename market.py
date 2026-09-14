"""
TradeMind 5.3
Market data layer.

Источник структуры:
Binance Spot SOLUSDT

Таймфреймы:
D1 -> W1 -> 1H -> 15M -> 5M
"""

import requests


# =========================================================
# CONFIG
# =========================================================

BASE_URL = "https://api.binance.com/api/v3"

SYMBOL = "SOLUSDT"

REQUEST_TIMEOUT = 10


# =========================================================
# BINANCE REQUEST
# =========================================================

def _get(path, params=None):

    if params is None:
        params = {}

    response = requests.get(
        BASE_URL + path,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    return response.json()


# =========================================================
# PRICE
# =========================================================

def get_price(symbol=SYMBOL):

    data = _get(
        "/ticker/price",
        {
            "symbol": symbol
        }
    )

    return float(
        data["price"]
    )


# =========================================================
# KLINES
# =========================================================

def get_klines(
    symbol=SYMBOL,
    interval="1h",
    limit=200
):

    raw = _get(
        "/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
    )

    candles = []

    for x in raw:

        candles.append({
            "open_time": x[0],

            "open": float(x[1]),

            "high": float(x[2]),

            "low": float(x[3]),

            "close": float(x[4]),

            "volume": float(x[5]),

            "close_time": x[6],
        })

    return candles


# =========================================================
# MARKET DATA
# =========================================================

def get_market_data(
    symbol=SYMBOL
):

    price = get_price(
        symbol
    )

    candles_d1 = get_klines(
        symbol,
        "1d",
        200
    )

    candles_w1 = get_klines(
        symbol,
        "1w",
        200
    )

    candles_1h = get_klines(
        symbol,
        "1h",
        200
    )

    candles_15m = get_klines(
        symbol,
        "15m",
        200
    )

    candles_5m = get_klines(
        symbol,
        "5m",
        200
    )

    return {

        "symbol": symbol,

        "price": price,

        "candles_d1": candles_d1,

        "candles_w1": candles_w1,

        "candles_1h": candles_1h,

        "candles_15m": candles_15m,

        "candles_5m": candles_5m,

        # Совместимость со старым кодом
        "d1": candles_d1,

        "w1": candles_w1,

        "1h": candles_1h,

        "15m": candles_15m,

        "5m": candles_5m,
    }


# =========================================================
# MAJOR LIQUIDITY
# =========================================================

def _local_swing_high(
    candles,
    i,
    left=2,
    right=2
):

    if (
        i < left
        or i + right >= len(candles)
    ):
        return False

    high = candles[i]["high"]

    left_highs = [
        candles[j]["high"]
        for j in range(
            i - left,
            i
        )
    ]

    right_highs = [
        candles[j]["high"]
        for j in range(
            i + 1,
            i + right + 1
        )
    ]

    return (
        high > max(left_highs)
        and high >= max(right_highs)
    )


def _local_swing_low(
    candles,
    i,
    left=2,
    right=2
):

    if (
        i < left
        or i + right >= len(candles)
    ):
        return False

    low = candles[i]["low"]

    left_lows = [
        candles[j]["low"]
        for j in range(
            i - left,
            i
        )
    ]

    right_lows = [
        candles[j]["low"]
        for j in range(
            i + 1,
            i + right + 1
        )
    ]

    return (
        low < min(left_lows)
        and low <= min(right_lows)
    )


def _distance_pct(
    a,
    b
):

    if not b:
        return 999

    return (
        abs(a - b)
        / b
        * 100
    )


def _cluster_levels(
    levels,
    cluster_pct=0.35
):

    if not levels:
        return []

    levels = sorted(
        levels,
        key=lambda x: x["level"]
    )

    groups = []

    for level in levels:

        if not groups:

            groups.append({
                "level": level["level"],
                "touches": 1,
                "indices": [
                    level["index"]
                ],
                "side": level["side"],
            })

            continue

        previous = groups[-1]

        distance = _distance_pct(
            level["level"],
            previous["level"]
        )

        if distance <= cluster_pct:

            touches = previous[
                "touches"
            ]

            previous["level"] = (
                (
                    previous["level"]
                    * touches
                )
                + level["level"]
            ) / (
                touches + 1
            )

            previous["touches"] += 1

            previous[
                "indices"
            ].append(
                level["index"]
            )

        else:

            groups.append({
                "level": level["level"],
                "touches": 1,
                "indices": [
                    level["index"]
                ],
                "side": level["side"],
            })

    return groups


def find_major_liquidity(
    candles_1h,
    current_price,
    max_levels=6
):

    if not candles_1h:
        return []

    candles = candles_1h[
        -200:
    ]

    raw = []

    for i in range(
        2,
        len(candles) - 2
    ):

        if _local_swing_high(
            candles,
            i
        ):

            level = candles[i]["high"]

            if level > current_price:

                raw.append({
                    "level": level,
                    "index": i,
                    "side": "SHORT",
                    "type": "1H major swing high",
                })

        if _local_swing_low(
            candles,
            i
        ):

            level = candles[i]["low"]

            if level < current_price:

                raw.append({
                    "level": level,
                    "index": i,
                    "side": "LONG",
                    "type": "1H major swing low",
                })

    if not raw:
        return []

    clusters = _cluster_levels(
        raw,
        cluster_pct=0.35
    )

    result = []

    for group in clusters:

        # Только действительно meaningful уровни.
        # Повторные касания имеют больший вес.
        touches = group["touches"]

        strength = min(
            1.0,
            0.50
            + 0.15 * max(
                0,
                touches - 1
            )
        )

        result.append({

            "level": group["level"],

            "price": group["level"],

            "touches": touches,

            "indices": group["indices"],

            "side": group["side"],

            "type": (
                "HIGH"
                if group["side"] == "SHORT"
                else "LOW"
            ),

            "liquidity_type": (
                "1H major swing high"
                if group["side"] == "SHORT"
                else "1H major swing low"
            ),

            "strength": round(
                strength,
                3
            ),
        })

    # Сначала самые сильные,
    # затем ближайшие к цене.
    result.sort(
        key=lambda x: (
            -x["touches"],
            _distance_pct(
                x["level"],
                current_price
            )
        )
    )

    return result[
        :max_levels
    ]


def get_major_liquidity(
    candles_1h,
    current_price
):

    return find_major_liquidity(
        candles_1h,
        current_price
    )


# =========================================================
# SWEEP
# =========================================================

def detect_sweep(
    candles_1h,
    current_price,
    direction
):

    levels = find_major_liquidity(
        candles_1h,
        current_price,
        max_levels=10
    )

    wanted_side = (
        "LONG"
        if direction == "LONG"
        else "SHORT"
    )

    for level in levels:

        if level["side"] != wanted_side:
            continue

        price = level["level"]

        recent = candles_1h[
            -3:
        ]

        if direction == "LONG":

            swept = any(
                candle["low"] < price
                for candle in recent
            )

            rejection = (
                recent[-1]["close"]
                > price
                or
                recent[-1]["close"]
                > recent[-1]["open"]
            )

        else:

            swept = any(
                candle["high"] > price
                for candle in recent
            )

            rejection = (
                recent[-1]["close"]
                < price
                or
                recent[-1]["close"]
                < recent[-1]["open"]
            )

        if swept and rejection:

            return {

                "swept": True,

                "direction": direction,

                "level": price,

                "price": price,

                "strength": min(
                    1.0,
                    0.60
                    + 0.10
                    * level["touches"]
                ),

                "liquidity_type": (
                    level[
                        "liquidity_type"
                    ]
                ),
            }

    return None