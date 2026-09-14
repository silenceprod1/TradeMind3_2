# -*- coding: utf-8 -*-

"""
TradeMind 3.9
Market data: Binance Spot

Оптимизация:
- без кэша
- параллельная загрузка price / 1H / 15M / 5M
- меньше задержка одного цикла
- подходит для сканирования каждые 15 секунд
- только 1H major liquidity
- мелкие уровни отбрасываются
- минимум 0.5% от текущей цены
- близкие уровни группируются
- sweep только по major liquidity
"""

import requests
from concurrent.futures import ThreadPoolExecutor


BASE_URL = "https://api.binance.com/api/v3"

SYMBOL = "SOLUSDT"

# Таймаут одного HTTP-запроса.
REQUEST_TIMEOUT = 5

# Для Binance Spot этого более чем достаточно.
MAX_WORKERS = 4


# =========================================================
# BINANCE
# =========================================================

def _get(path, params):

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

    # Последняя свеча может быть незакрыта.
    # Стратегия работает только с закрытыми свечами.
    return candles[:-1]


# =========================================================
# PARALLEL MARKET DATA
# =========================================================

def get_market_data(symbol=SYMBOL):

    """
    Получаем все данные одной монеты параллельно.

    Раньше:

        price
        ↓
        1H
        ↓
        15M
        ↓
        5M

    Теперь:

        price ─┐
        1H    ─┤
        15M   ─┼── одновременно
        5M    ─┘

    Это сильно уменьшает задержку цикла.
    """

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        future_price = executor.submit(
            get_price,
            symbol
        )

        future_1h = executor.submit(
            get_closed_klines,
            symbol,
            "1h",
            200
        )

        future_15m = executor.submit(
            get_closed_klines,
            symbol,
            "15m",
            200
        )

        future_5m = executor.submit(
            get_closed_klines,
            symbol,
            "5m",
            200
        )

        price = future_price.result()

        candles_1h = future_1h.result()

        candles_15m = future_15m.result()

        candles_5m = future_5m.result()

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
        # MAJOR HIGH
        # =================================================

        if (
            current_high >= max(left_highs)
            and
            current_high > max(right_highs)
        ):

            highs.append({

                "price": current_high,

                "index": i,

                "open_time":
                    candles[i]["open_time"]

            })

        # =================================================
        # MAJOR LOW
        # =================================================

        if (
            current_low <= min(left_lows)
            and
            current_low < min(right_lows)
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

    Минимальная дистанция:
    0.5%

    Минимальный gap между major levels:
    0.7% или $0.50.
    """

    current_price = float(
        current_price
    )

    if not candles_1h:
        return []

    # Последние 150 закрытых 1H свечей.
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

        # Только выше текущей цены.
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

        # Только ниже текущей цены.
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
    # SORT
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

    LONG:
        прокол 1H MAJOR LOW
        закрытие обратно выше
        зелёная свеча.

    SHORT:
        прокол 1H MAJOR HIGH
        закрытие обратно ниже
        красная свеча.
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

    # Самая новая свеча проверяется первой.
    for candle in reversed(recent):

        for level in valid_levels:

            level_price = float(
                level["price"]
            )

            # =================================================
            # LONG
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