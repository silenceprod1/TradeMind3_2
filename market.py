"""
TradeMind 5.6
Market data layer.

Источник структуры:
Binance Spot

Таймфреймы:
D1 -> W1 -> 1H -> 15M -> 5M

Ликвидность:
только крупные 1H swing HIGH / LOW.
HIGH и LOW обрабатываются отдельно.
Текущая незакрытая свеча не используется.
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

    return float(data["price"])


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

    # =====================================================
    # УБИРАЕМ НЕЗАКРЫТУЮ СВЕЧУ
    # =====================================================

    if len(candles) > 1:
        candles = candles[:-1]

    return candles


# =========================================================
# MARKET DATA
# =========================================================

def get_market_data(
    symbol=SYMBOL
):

    price = get_price(symbol)

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
# HELPERS
# =========================================================

def _distance_pct(a, b):

    if not b:
        return 999.0

    return (
        abs(a - b)
        / abs(b)
        * 100
    )


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


# =========================================================
# LEVEL CLUSTERING
# =========================================================
#
# ВАЖНО:
# HIGH и LOW кластеризуются ОТДЕЛЬНО.
# Нельзя объединять HIGH и LOW в один уровень.
# =========================================================

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
                "type": level["type"],
            })

            continue

        previous = groups[-1]

        # =================================================
        # НИКОГДА НЕ СМЕШИВАЕМ HIGH И LOW
        # =================================================

        if (
            previous["side"]
            != level["side"]
        ):

            groups.append({
                "level": level["level"],
                "touches": 1,
                "indices": [
                    level["index"]
                ],
                "side": level["side"],
                "type": level["type"],
            })

            continue

        distance = _distance_pct(
            level["level"],
            previous["level"]
        )

        if distance <= cluster_pct:

            touches = previous["touches"]

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

            previous["indices"].append(
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
                "type": level["type"],
            })

    return groups


# =========================================================
# MAJOR LIQUIDITY
# =========================================================

def find_major_liquidity(
    candles_1h,
    current_price,
    max_levels=6
):

    if not candles_1h:
        return []

    candles = candles_1h[-200:]

    raw_highs = []
    raw_lows = []

    # =====================================================
    # СОБИРАЕМ HIGH И LOW ОТДЕЛЬНО
    # =====================================================

    for i in range(
        2,
        len(candles) - 2
    ):

        # =================================================
        # SWING HIGH
        # =================================================

        if _local_swing_high(
            candles,
            i
        ):

            level = candles[i]["high"]

            # Только ликвидность ВЫШЕ текущей цены.
            # Она интересна для потенциального SHORT sweep.

            if level > current_price:

                raw_highs.append({
                    "level": level,
                    "index": i,
                    "side": "SHORT",
                    "type": "HIGH",
                })

        # =================================================
        # SWING LOW
        # =================================================

        if _local_swing_low(
            candles,
            i
        ):

            level = candles[i]["low"]

            # Только ликвидность НИЖЕ текущей цены.
            # Она интересна для потенциального LONG sweep.

            if level < current_price:

                raw_lows.append({
                    "level": level,
                    "index": i,
                    "side": "LONG",
                    "type": "LOW",
                })

    # =====================================================
    # КЛАСТЕРИЗУЕМ РАЗДЕЛЬНО
    # =====================================================

    high_clusters = _cluster_levels(
        raw_highs,
        cluster_pct=0.35
    )

    low_clusters = _cluster_levels(
        raw_lows,
        cluster_pct=0.35
    )

    clusters = (
        high_clusters
        + low_clusters
    )

    if not clusters:
        return []

    result = []

    # =====================================================
    # ФИЛЬТР КРУПНОЙ ЛИКВИДНОСТИ
    # =====================================================
    #
    # Минимум 2 касания.
    #
    # Один случайный локальный high/low
    # не считаем major liquidity.
    # =====================================================

    for group in clusters:

        touches = group["touches"]

        if touches < 2:
            continue

        strength = min(
            1.0,
            0.50
            + 0.15
            * max(
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

            "type": group["type"],

            "liquidity_type": (
                "1H major swing high"
                if group["type"] == "HIGH"
                else
                "1H major swing low"
            ),

            "strength": round(
                strength,
                3
            ),
        })

    if not result:
        return []

    # =====================================================
    # СОРТИРОВКА
    # =====================================================
    #
    # Сначала сила уровня,
    # затем расстояние от текущей цены.
    # =====================================================

    result.sort(
        key=lambda x: (
            -x["touches"],
            _distance_pct(
                x["level"],
                current_price
            )
        )
    )

    return result[:max_levels]


# =========================================================
# PUBLIC LIQUIDITY FUNCTION
# =========================================================

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

        recent = candles_1h[-3:]

        if not recent:
            continue

        # =================================================
        # LONG
        # =================================================

        if direction == "LONG":

            # Цена должна снять ликвидность снизу.

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

        # =================================================
        # SHORT
        # =================================================

        else:

            # Цена должна снять ликвидность сверху.

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

        # =================================================
        # SWEEP FOUND
        # =================================================

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

                "touches": level["touches"],

                "liquidity_type": (
                    level[
                        "liquidity_type"
                    ]
                ),

                "type": level["type"],
            }

    return None