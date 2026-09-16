"""TradeMind 6.3 market data. Binance Spot is the reference chart."""

import requests


BASE_URL = "https://api.binance.com/api/v3"

SYMBOL = "SOLUSDT"

TIMEOUT = 10


def _get(path, params):
    response = requests.get(
        BASE_URL + path,
        params=params,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


def get_price(symbol=SYMBOL):
    data = _get(
        "/ticker/price",
        {"symbol": symbol},
    )

    return float(data["price"])


def get_klines(
    symbol=SYMBOL,
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


def get_market_data(symbol=SYMBOL):

    return {
        "symbol": symbol,
        "price": get_price(symbol),

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

        "candles_1m": get_klines(
            symbol,
            "1m",
            200,
        ),
    }


def _swing_high(candles, i):

    if i < 2 or i >= len(candles) - 2:
        return False

    x = candles[i]["high"]

    return (
        x > candles[i - 1]["high"]
        and x >= candles[i - 2]["high"]
        and x >= candles[i + 1]["high"]
        and x > candles[i + 2]["high"]
    )


def _swing_low(candles, i):

    if i < 2 or i >= len(candles) - 2:
        return False

    x = candles[i]["low"]

    return (
        x < candles[i - 1]["low"]
        and x <= candles[i - 2]["low"]
        and x <= candles[i + 1]["low"]
        and x < candles[i + 2]["low"]
    )


def _cluster(
    points,
    pct=0.35,
):
    if not points:
        return []

    points = sorted(
        points,
        key=lambda x: x[0],
    )

    result = []

    for price, index in points:

        if not result:

            result.append({
                "price": price,
                "touches": 1,
                "index": index,
            })

            continue

        last = result[-1]

        center = last["price"]

        distance_pct = (
            abs(price - center)
            / center
            * 100
        )

        if distance_pct <= pct:

            touches = last["touches"]

            last["price"] = (
                center * touches
                + price
            ) / (touches + 1)

            last["touches"] += 1

            last["index"] = max(
                last["index"],
                index,
            )

        else:

            result.append({
                "price": price,
                "touches": 1,
                "index": index,
            })

    return result


def _local_touches(
    candles,
    level,
    pct=0.40,
):
    if not candles:
        return 0

    count = 0

    for candle in candles[-160:]:

        high_distance = (
            abs(candle["high"] - level)
            / level
            * 100
        )

        low_distance = (
            abs(candle["low"] - level)
            / level
            * 100
        )

        if (
            high_distance <= pct
            or low_distance <= pct
        ):
            count += 1

    return count


def find_major_liquidity(
    candles_1h,
    current_price,
    max_levels=12,
    candles_15m=None,
    candles_5m=None,
    candles_1m=None,
    include_swept=False,
):
    """
    База — только major liquidity с 1H.

    15M / 5M / 1M используются только
    для усиления/кластеризации зоны.

    Мелкие уровни отдельно не создаются.
    """

    if (
        not candles_1h
        or len(candles_1h) < 20
    ):
        return []

    base = candles_1h[:-4]

    highs = []

    lows = []

    for i, candle in enumerate(base):

        if _swing_high(base, i):

            highs.append((
                candle["high"],
                i,
            ))

        if _swing_low(base, i):

            lows.append((
                candle["low"],
                i,
            ))

    clusters = (
        _cluster(highs)
        + _cluster(lows)
    )

    result = []

    for cluster in clusters:

        price = cluster["price"]

        local_touches = (
            _local_touches(
                candles_15m,
                price,
            )
            + _local_touches(
                candles_5m,
                price,
            )
            + _local_touches(
                candles_1m,
                price,
            )
        )

        strength = min(
            100,
            55
            + cluster["touches"] * 8
            + min(local_touches, 10) * 2,
        )

        if price > current_price:

            result.append({
                "price": price,
                "side": "SHORT",
                "type": "BSL",
                "kind": "1H MAJOR BSL",
                "touches": cluster["touches"],
                "local_touches": local_touches,
                "strength": strength,

                "zone_low": price * (
                    1 - 0.002
                ),

                "zone_high": price * (
                    1 + 0.002
                ),
            })

        elif price < current_price:

            result.append({
                "price": price,
                "side": "LONG",
                "type": "SSL",
                "kind": "1H MAJOR SSL",
                "touches": cluster["touches"],
                "local_touches": local_touches,
                "strength": strength,

                "zone_low": price * (
                    1 - 0.002
                ),

                "zone_high": price * (
                    1 + 0.002
                ),
            })

    result.sort(
        key=lambda x: (
            -x["strength"],
            abs(
                x["price"]
                - current_price
            ),
        )
    )

    return result[:max_levels]


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

        level_price = float(
            level["price"]
        )

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

        if (
            direction == "LONG"
            and level_price > price
        ):
            candidates.append(level)

        elif (
            direction == "SHORT"
            and level_price < price
        ):
            candidates.append(level)

    if not candidates:
        return None

    return min(
        candidates,
        key=lambda x: abs(
            float(x["price"]) - price
        ),
    )


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
            12,
        )

    return find_sweep(
        candles_1h,
        major_levels,
        direction,
    )


detect_fresh_sweep = detect_sweep