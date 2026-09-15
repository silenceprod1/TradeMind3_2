import requests
import time

BASE_URL = "https://api.binance.com/api/v3"

DEFAULT_SYMBOL = "SOLUSDT"

REQUEST_TIMEOUT = 10

LEVEL_CLUSTER_PCT = 0.35
MIN_MAJOR_TOUCHES = 2
MAX_MAJOR_LEVELS = 6
LIQUIDITY_LOOKBACK = 120

SWEEP_LOOKBACK_1H = 4
MIN_SWEEP_DEPTH_PCT = 0.08


def _get(path, params):
    response = requests.get(
        BASE_URL + path,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


def get_price(symbol=DEFAULT_SYMBOL):

    data = _get(
        "/ticker/price",
        {
            "symbol": symbol,
        },
    )

    return float(data["price"])


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

    if len(candles) > 1:

        now_ms = int(
            time.time() * 1000
        )

        if candles[-1]["close_time"] > now_ms:
            candles = candles[:-1]

    return candles


def get_market_data(
    symbol=DEFAULT_SYMBOL,
):

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


def _pct_distance(
    a,
    b,
):

    if b in (
        None,
        0,
    ):
        return 999.0

    return (
        abs(a - b)
        / abs(b)
        * 100.0
    )


def _cluster_levels(items):

    if not items:
        return []

    result = []

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

                if (
                    distance
                    <= LEVEL_CLUSTER_PCT
                ):

                    cluster["members"].append(
                        item
                    )

                    cluster["price"] = (
                        sum(
                            x["price"]
                            for x
                            in cluster["members"]
                        )
                        /
                        len(
                            cluster["members"]
                        )
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


def find_major_liquidity(
    candles_1h,
    current_price,
    max_levels=MAX_MAJOR_LEVELS,
    include_swept=False,
):

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

    clusters = _cluster_levels(
        raw
    )

    candidates = []

    for cluster in clusters:

        price = cluster["price"]

        touches = len(
            cluster["members"]
        )

        last_index = cluster[
            "last_index"
        ]

        swept = False
        sweep_candle = None

        for candle in data[
            last_index + 1:
        ]:

            if cluster["type"] == "HIGH":

                if candle["high"] > price:

                    swept = True

                    sweep_candle = candle

                    break

            else:

                if candle["low"] < price:

                    swept = True

                    sweep_candle = candle

                    break

        if swept and not include_swept:
            continue

        strength = min(
            1.0,
            0.45
            + 0.15 * max(
                0,
                touches - 1,
            ),
        )

        if (
            cluster["type"] == "HIGH"
            and price > current_price
        ):

            candidates.append({
                "price": round(price, 8),
                "level": round(price, 8),
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
                "swept": swept,
                "sweep_candle":
                    sweep_candle,
            })

        if (
            cluster["type"] == "LOW"
            and price < current_price
        ):

            candidates.append({
                "price": round(price, 8),
                "level": round(price, 8),
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
                "swept": swept,
                "sweep_candle":
                    sweep_candle,
            })

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


def detect_sweep(
    candles_1h,
    current_price,
    direction,
):

    if (
        not candles_1h
        or direction not in {
            "LONG",
            "SHORT",
        }
    ):
        return None

    levels = find_major_liquidity(
        candles_1h,
        current_price,
        max_levels=50,
        include_swept=True,
    )

    if not levels:
        return None

    data = candles_1h[
        -SWEEP_LOOKBACK_1H:
    ]

    for level_data in levels:

        if (
            level_data["side"]
            != direction
        ):
            continue

        level = level_data["price"]

        for candle in data:

            high = candle["high"]
            low = candle["low"]

            open_price = candle["open"]
            close = candle["close"]

            if direction == "LONG":

                depth = level - low

                depth_pct = (
                    depth / level * 100.0
                    if level
                    else 0.0
                )

                swept = (
                    low < level
                    and depth_pct
                    >= MIN_SWEEP_DEPTH_PCT
                )

                rejection = (
                    close > level
                    and close > open_price
                )

                if swept and rejection:

                    return {
                        "swept": True,
                        "direction": "LONG",
                        "level": level,
                        "extreme": low,
                        "time": candle["open_time"],
                        "strength":
                            level_data["strength"],
                        "touches":
                            level_data["touches"],
                        "liquidity_type":
                            level_data["liquidity_type"],
                        "type": "SSL SWEEP",
                        "depth_pct":
                            round(
                                depth_pct,
                                4,
                            ),
                    }

            else:

                depth = high - level

                depth_pct = (
                    depth / level * 100.0
                    if level
                    else 0.0
                )

                swept = (
                    high > level
                    and depth_pct
                    >= MIN_SWEEP_DEPTH_PCT
                )

                rejection = (
                    close < level
                    and close < open_price
                )

                if swept and rejection:

                    return {
                        "swept": True,
                        "direction": "SHORT",
                        "level": level,
                        "extreme": high,
                        "time": candle["open_time"],
                        "strength":
                            level_data["strength"],
                        "touches":
                            level_data["touches"],
                        "liquidity_type":
                            level_data["liquidity_type"],
                        "type": "BSL SWEEP",
                        "depth_pct":
                            round(
                                depth_pct,
                                4,
                            ),
                    }

    return None


get_major_liquidity = find_major_liquidity


__all__ = [
    "get_price",
    "get_klines",
    "get_market_data",
    "find_major_liquidity",
    "get_major_liquidity",
    "detect_sweep",
]