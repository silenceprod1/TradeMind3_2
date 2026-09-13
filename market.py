# -*- coding: utf-8 -*-

"""
TradeMind 3.5
Market data: Binance Spot

Логика ликвидности:
- только 1H major liquidity
- мелкие уровни отбрасываются
- минимальная дистанция major liquidity: 0.5%
- близкие уровни группируются
- sweep разрешён только по найденным major levels
"""

import requests


BASE_URL = "https://api.binance.com/api/v3"
SYMBOL = "SOLUSDT"


# =========================================================
# BINANCE
# =========================================================

def _get(path, params):

    response = requests.get(
        BASE_URL + path,
        params=params,
        timeout=10
    )

    response.raise_for_status()

    return response.json()


def get_price(symbol=SYMBOL):

    data = _get(
        "/ticker/price",
        {
            "symbol": symbol
        }
    )

    return float(data["price"])


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

            "close_time": x[6]

        })

    return candles


def get_closed_klines(
    symbol=SYMBOL,
    interval="1h",
    limit=200
):

    candles = get_klines(
        symbol,
        interval,
        limit
    )

    if len(candles) <= 1:
        return candles

    # Последняя свеча может быть ещё незакрыта.
    # Для анализа стратегии используем только закрытые.
    return candles[:-1]


# =========================================================
# MARKET DATA
# =========================================================

def get_market_data(symbol=SYMBOL):

    price = get_price(symbol)

    candles_1h = get_closed_klines(
        symbol,
        "1h",
        200
    )

    candles_15m = get_closed_klines(
        symbol,
        "15m",
        200
    )

    candles_5m = get_closed_klines(
        symbol,
        "5m",
        200
    )

    # ВАЖНО:
    # main.py ожидает именно эти ключи:
    #
    # candles_1h
    # candles_15m
    # candles_5m
    #
    # Раньше здесь были "1h", "15m", "5m",
    # из-за чего main.py получал KeyError.

    return {

        "symbol": symbol,

        "price": price,

        "candles_1h": candles_1h,

        "candles_15m": candles_15m,

        "candles_5m": candles_5m

    }


# =========================================================
# SWING LEVELS
# =========================================================

def find_swing_levels(
    candles,
    left=4,
    right=4
):

    highs = []

    lows = []

    if len(candles) < left + right + 1:
        return highs, lows

    for i in range(
        left,
        len(candles) - right
    ):

        current_high = candles[i]["high"]

        current_low = candles[i]["low"]

        left_highs = [
            x["high"]
            for x in candles[
                i-left:i
            ]
        ]

        right_highs = [
            x["high"]
            for x in candles[
                i+1:i+right+1
            ]
        ]

        left_lows = [
            x["low"]
            for x in candles[
                i-left:i
            ]
        ]

        right_lows = [
            x["low"]
            for x in candles[
                i+1:i+right+1
            ]
        ]

        # =================================================
        # MAJOR HIGH CANDIDATE
        # =================================================

        if (
            current_high >= max(left_highs)
            and current_high > max(right_highs)
        ):

            highs.append({

                "price": current_high,

                "index": i,

                "open_time":
                    candles[i]["open_time"]

            })

        # =================================================
        # MAJOR LOW CANDIDATE
        # =================================================

        if (
            current_low <= min(left_lows)
            and current_low < min(right_lows)
        ):

            lows.append({

                "price": current_low,

                "index": i,

                "open_time":
                    candles[i]["open_time"]

            })

    return highs, lows


# =========================================================
# MAJOR LIQUIDITY
# =========================================================

def find_major_liquidity(
    candles_1h,
    current_price,
    max_levels=6
):

    """
    Используем ТОЛЬКО 1H swing levels.

    Не используем:
    - 5M liquidity
    - 15M liquidity
    - мелкие локальные уровни

    Минимальная дистанция от текущей цены:
    0.5%

    Близкие major levels:
    минимум 0.7% между уровнями
    либо $0.50.
    """

    current_price = float(
        current_price
    )

    if not candles_1h:
        return []

    # Берём последние 150 закрытых 1H свечей.
    candles = candles_1h[-150:]

    highs, lows = find_swing_levels(
        candles,
        left=4,
        right=4
    )

    candidates = []

    # =====================================================
    # MINIMUM DISTANCE
    # =====================================================

    MIN_DISTANCE = 0.005

    min_distance = (
        current_price *
        MIN_DISTANCE
    )

    # =====================================================
    # HIGH LIQUIDITY
    # =====================================================

    for swing in highs:

        level_price = float(
            swing["price"]
        )

        distance = (
            level_price -
            current_price
        )

        # Только уровни ВЫШЕ текущей цены.
        # Минимум 0.5%.

        if distance >= min_distance:

            candidates.append({

                "price": level_price,

                "direction": "SHORT",

                "type":
                    "1H MAJOR HIGH",

                "index":
                    swing["index"],

                "open_time":
                    swing["open_time"]

            })

    # =====================================================
    # LOW LIQUIDITY
    # =====================================================

    for swing in lows:

        level_price = float(
            swing["price"]
        )

        distance = (
            current_price -
            level_price
        )

        # Только уровни НИЖЕ текущей цены.
        # Минимум 0.5%.

        if distance >= min_distance:

            candidates.append({

                "price": level_price,

                "direction": "LONG",

                "type":
                    "1H MAJOR LOW",

                "index":
                    swing["index"],

                "open_time":
                    swing["open_time"]

            })

    # =====================================================
    # SORT BY DISTANCE
    # =====================================================

    candidates.sort(
        key=lambda x:
        abs(
            x["price"] -
            current_price
        )
    )

    # =====================================================
    # REMOVE NEAR-DUPLICATES
    # =====================================================

    selected = []

    # Между крупными уровнями минимум 0.7%
    # или $0.50.

    MIN_LEVEL_GAP = max(
        current_price * 0.007,
        0.50
    )

    for candidate in candidates:

        too_close = False

        for existing in selected:

            if abs(
                candidate["price"] -
                existing["price"]
            ) < MIN_LEVEL_GAP:

                too_close = True

                break

        if too_close:
            continue

        selected.append(
            candidate
        )

        if len(selected) >= max_levels:
            break

    return selected


# =========================================================
# SWEEP DETECTION
# =========================================================

def detect_sweep(
    candles_5m,
    major_levels,
    lookback=8
):

    """
    Sweep разрешён ТОЛЬКО по major_levels.

    Никаких самостоятельных 5M/15M liquidity levels.

    LONG:
        цена прокалывает 1H MAJOR LOW
        и закрывается обратно выше уровня
        зелёной свечой.

    SHORT:
        цена прокалывает 1H MAJOR HIGH
        и закрывается обратно ниже уровня
        красной свечой.
    """

    if not major_levels:
        return None

    if not candles_5m:
        return None

    if len(candles_5m) < 3:
        return None

    recent = candles_5m[
        -lookback:
    ]

    # =====================================================
    # SAFETY FILTER
    # =====================================================

    valid_levels = []

    for level in major_levels:

        if level.get("type") not in (
            "1H MAJOR HIGH",
            "1H MAJOR LOW"
        ):
            continue

        try:

            level_price = float(
                level["price"]
            )

        except Exception:

            continue

        if level_price <= 0:
            continue

        valid_levels.append(
            level
        )

    if not valid_levels:
        return None

    # =====================================================
    # CHECK SWEEP
    # =====================================================

    # Смотрим от самой новой свечи назад.
    for candle in reversed(recent):

        for level in valid_levels:

            level_price = float(
                level["price"]
            )

            # =================================================
            # LONG
            # Sweep 1H MAJOR LOW
            # =================================================

            if level["direction"] == "LONG":

                if (
                    candle["low"]
                    < level_price

                    and

                    candle["close"]
                    > level_price

                    and

                    candle["close"]
                    > candle["open"]
                ):

                    return {

                        "direction":
                            "LONG",

                        "level":
                            level_price,

                        "swept":
                            True,

                        "liquidity_type":
                            level["type"],

                        "open_time":
                            candle["open_time"],

                        "close":
                            candle["close"],

                        "high":
                            candle["high"],

                        "low":
                            candle["low"]

                    }

            # =================================================
            # SHORT
            # Sweep 1H MAJOR HIGH
            # =================================================

            if level["direction"] == "SHORT":

                if (
                    candle["high"]
                    > level_price

                    and

                    candle["close"]
                    < level_price

                    and

                    candle["close"]
                    < candle["open"]
                ):

                    return {

                        "direction":
                            "SHORT",

                        "level":
                            level_price,

                        "swept":
                            True,

                        "liquidity_type":
                            level["type"],

                        "open_time":
                            candle["open_time"],

                        "close":
                            candle["close"],

                        "high":
                            candle["high"],

                        "low":
                            candle["low"]

                    }

    return None