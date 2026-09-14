# market.py
# TradeMind 3.10
# Binance Spot market data
# Sweep 2.0 + CHoCH/BOS
# No cache

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed


BASE_URL = "https://api.binance.com/api/v3"
SYMBOL = "SOLUSDT"

REQUEST_TIMEOUT = 6

# Параметры структуры
SWING_LEFT = 4
SWING_RIGHT = 4

# Для major liquidity
MAJOR_LOOKBACK = 150
MAJOR_MIN_DISTANCE_PCT = 0.005       # 0.5%
MAJOR_MIN_GAP_PCT = 0.007            # 0.7%
MAJOR_MIN_GAP_ABS = 0.50
MAJOR_MAX_LEVELS = 6

# Sweep
SWEEP_LOOKBACK = 12
SWEEP_MIN_PENETRATION_PCT = 0.0005   # 0.05%
SWEEP_MAX_PENETRATION_PCT = 0.012    # 1.2%

# Structure
STRUCTURE_LOOKBACK = 80


# ============================================================
# BINANCE API
# ============================================================

def _get(path, params=None):
    """
    Универсальный GET-запрос к Binance Spot API.
    """
    url = BASE_URL + path

    response = requests.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()
    return response.json()


# ============================================================
# PRICE
# ============================================================

def get_price(symbol=SYMBOL):
    """
    Текущая цена инструмента.
    """
    data = _get(
        "/ticker/price",
        {"symbol": symbol}
    )

    return float(data["price"])


# ============================================================
# KLINES
# ============================================================

def get_klines(symbol, interval, limit=200):
    """
    Получение свечей Binance.

    Возвращает:
    {
        open_time,
        open,
        high,
        low,
        close,
        volume,
        close_time
    }
    """

    raw = _get(
        "/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
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
            "close_time": int(row[6])
        })

    return candles


def get_closed_klines(symbol, interval, limit=200):
    """
    Только закрытые свечи.
    Последняя свеча Binance обычно текущая и ещё не закрыта.
    """

    candles = get_klines(
        symbol,
        interval,
        limit
    )

    if len(candles) <= 1:
        return candles

    return candles[:-1]


# ============================================================
# PARALLEL MARKET DATA
# ============================================================

def get_market_data(symbol=SYMBOL):
    """
    Получает price + 1H + 15M + 5M ПАРАЛЛЕЛЬНО.

    Это важно для сканирования 9 монет каждые 15 секунд.
    """

    results = {}

    jobs = {
        "price": lambda: get_price(symbol),
        "candles_1h": lambda: get_closed_klines(
            symbol,
            "1h",
            200
        ),
        "candles_15m": lambda: get_closed_klines(
            symbol,
            "15m",
            200
        ),
        "candles_5m": lambda: get_closed_klines(
            symbol,
            "5m",
            200
        )
    }

    with ThreadPoolExecutor(max_workers=4) as executor:

        futures = {
            executor.submit(func): name
            for name, func in jobs.items()
        }

        for future in as_completed(futures):
            name = futures[future]

            try:
                results[name] = future.result()

            except Exception as exc:
                raise RuntimeError(
                    f"{symbol} {name} request failed: {exc}"
                ) from exc

    return {
        "symbol": symbol,
        "price": results["price"],
        "candles_1h": results["candles_1h"],
        "candles_15m": results["candles_15m"],
        "candles_5m": results["candles_5m"]
    }


# ============================================================
# SWING LEVELS
# ============================================================

def find_swing_levels(
    candles,
    left=SWING_LEFT,
    right=SWING_RIGHT
):
    """
    Поиск swing high / swing low.

    Используются только закрытые свечи.
    """

    highs = []
    lows = []

    if len(candles) < left + right + 1:
        return {
            "highs": highs,
            "lows": lows
        }

    for i in range(left, len(candles) - right):

        current = candles[i]

        current_high = current["high"]
        current_low = current["low"]

        is_high = True
        is_low = True

        for j in range(i - left, i):
            if candles[j]["high"] >= current_high:
                is_high = False

            if candles[j]["low"] <= current_low:
                is_low = False

        for j in range(i + 1, i + right + 1):
            if candles[j]["high"] >= current_high:
                is_high = False

            if candles[j]["low"] <= current_low:
                is_low = False

        if is_high:
            highs.append({
                "price": current_high,
                "open_time": current["open_time"],
                "index": i,
                "type": "HIGH"
            })

        if is_low:
            lows.append({
                "price": current_low,
                "open_time": current["open_time"],
                "index": i,
                "type": "LOW"
            })

    return {
        "highs": highs,
        "lows": lows
    }


# ============================================================
# MAJOR LIQUIDITY
# ============================================================

def find_major_liquidity(
    candles,
    current_price=None,
    max_levels=MAJOR_MAX_LEVELS
):
    """
    Находит КРУПНЫЕ уровни ликвидности.

    Правила:
    - только 1H;
    - последние 150 закрытых свечей;
    - только swing levels;
    - минимум 0.5% от цены;
    - между выбранными уровнями минимум 0.7%
      или $0.50;
    - максимум 6 уровней;
    - уровни сортируются по близости.
    """

    if not candles:
        return []

    if current_price is None:
        current_price = candles[-1]["close"]

    candles = candles[-MAJOR_LOOKBACK:]

    swings = find_swing_levels(
        candles,
        left=SWING_LEFT,
        right=SWING_RIGHT
    )

    candidates = []

    # --------------------------------------------------------
    # HIGH LIQUIDITY
    # --------------------------------------------------------

    for level in swings["highs"]:

        price = level["price"]

        if price <= current_price:
            continue

        distance_pct = (
            price - current_price
        ) / current_price

        if distance_pct < MAJOR_MIN_DISTANCE_PCT:
            continue

        candidates.append({
            "price": price,
            "type": "HIGH",
            "distance_pct": distance_pct,
            "open_time": level["open_time"]
        })

    # --------------------------------------------------------
    # LOW LIQUIDITY
    # --------------------------------------------------------

    for level in swings["lows"]:

        price = level["price"]

        if price >= current_price:
            continue

        distance_pct = (
            current_price - price
        ) / current_price

        if distance_pct < MAJOR_MIN_DISTANCE_PCT:
            continue

        candidates.append({
            "price": price,
            "type": "LOW",
            "distance_pct": distance_pct,
            "open_time": level["open_time"]
        })

    # Ближайшие сначала
    candidates.sort(
        key=lambda x: x["distance_pct"]
    )

    selected = []

    for candidate in candidates:

        too_close = False

        for existing in selected:

            price_diff = abs(
                candidate["price"]
                - existing["price"]
            )

            avg_price = (
                candidate["price"]
                + existing["price"]
            ) / 2

            gap_pct = price_diff / avg_price

            if (
                gap_pct < MAJOR_MIN_GAP_PCT
                or price_diff < MAJOR_MIN_GAP_ABS
            ):
                too_close = True
                break

        if too_close:
            continue

        selected.append(candidate)

        if len(selected) >= max_levels:
            break

    # От ближнего к дальнему
    selected.sort(
        key=lambda x: x["distance_pct"]
    )

    return selected


# ============================================================
# CANDLE HELPERS
# ============================================================

def _candle_body(candle):
    return abs(
        candle["close"] - candle["open"]
    )


def _candle_range(candle):
    return candle["high"] - candle["low"]


def _bullish(candle):
    return candle["close"] > candle["open"]


def _bearish(candle):
    return candle["close"] < candle["open"]


# ============================================================
# SWEEP 2.0
# ============================================================

def _calculate_sweep_quality(
    candle,
    level,
    direction
):
    """
    Оценивает качество sweep.

    Возвращает:
    {
        quality,
        penetration_pct,
        rejection_pct,
        body_strength,
        score
    }
    """

    level_price = level["price"]

    candle_range = _candle_range(candle)

    if candle_range <= 0:
        return {
            "quality": "WEAK",
            "penetration_pct": 0.0,
            "rejection_pct": 0.0,
            "body_strength": 0.0,
            "score": 0
        }

    if direction == "LONG":

        # Sweep вниз
        penetration = max(
            0.0,
            level_price - candle["low"]
        )

        penetration_pct = (
            penetration / level_price
        )

        # Насколько хорошо закрылись обратно выше уровня
        rejection = max(
            0.0,
            candle["close"] - level_price
        )

        rejection_pct = (
            rejection / level_price
        )

        body_strength = (
            max(
                0.0,
                candle["close"] - candle["open"]
            )
            / candle_range
        )

    else:

        # Sweep вверх
        penetration = max(
            0.0,
            candle["high"] - level_price
        )

        penetration_pct = (
            penetration / level_price
        )

        # Насколько хорошо закрылись обратно ниже
        rejection = max(
            0.0,
            level_price - candle["close"]
        )

        rejection_pct = (
            rejection / level_price
        )

        body_strength = (
            max(
                0.0,
                candle["open"] - candle["close"]
            )
            / candle_range
        )

    score = 0

    # Есть нормальный прокол
    if penetration_pct >= SWEEP_MIN_PENETRATION_PCT:
        score += 30

    # Не слишком глубокий прокол
    if penetration_pct <= SWEEP_MAX_PENETRATION_PCT:
        score += 15

    # Возврат за уровень
    if rejection_pct > 0:
        score += 30

    # Направленная свеча
    if body_strength >= 0.50:
        score += 25
    elif body_strength >= 0.30:
        score += 15

    if score >= 80:
        quality = "STRONG"

    elif score >= 55:
        quality = "NORMAL"

    else:
        quality = "WEAK"

    return {
        "quality": quality,
        "penetration_pct": penetration_pct,
        "rejection_pct": rejection_pct,
        "body_strength": body_strength,
        "score": min(score, 100)
    }


def detect_sweep(
    candles_5m,
    major_levels,
    lookback=SWEEP_LOOKBACK
):
    """
    Sweep 2.0.

    Ищет только крупную ликвидность.

    LONG:
        цена прокалывает LOW
        → закрывается обратно выше LOW
        → желательно bullish candle

    SHORT:
        цена прокалывает HIGH
        → закрывается обратно ниже HIGH
        → желательно bearish candle

    Возвращает наиболее свежий подтверждённый sweep.
    """

    if not candles_5m:
        return None

    if not major_levels:
        return None

    candles = candles_5m[-lookback:]

    # Идём от самой свежей свечи назад
    for candle in reversed(candles):

        for level in major_levels:

            level_price = level["price"]
            level_type = level["type"]

            # =================================================
            # LONG SWEEP
            # =================================================

            if level_type == "LOW":

                if candle["low"] < level_price:

                    # Цена должна вернуться выше уровня
                    if candle["close"] > level_price:

                        quality = _calculate_sweep_quality(
                            candle,
                            level,
                            "LONG"
                        )

                        # Совсем слабые sweep отбрасываем
                        if quality["score"] < 40:
                            continue

                        return {
                            "direction": "LONG",
                            "level": level_price,
                            "liquidity_type": "LOW",

                            "open_time": candle["open_time"],
                            "close_time": candle["close_time"],

                            "open": candle["open"],
                            "close": candle["close"],
                            "high": candle["high"],
                            "low": candle["low"],

                            "quality": quality["quality"],
                            "score": quality["score"],

                            "penetration_pct": quality[
                                "penetration_pct"
                            ],

                            "rejection_pct": quality[
                                "rejection_pct"
                            ],

                            "body_strength": quality[
                                "body_strength"
                            ]
                        }

            # =================================================
            # SHORT SWEEP
            # =================================================

            if level_type == "HIGH":

                if candle["high"] > level_price:

                    # Цена должна вернуться ниже уровня
                    if candle["close"] < level_price:

                        quality = _calculate_sweep_quality(
                            candle,
                            level,
                            "SHORT"
                        )

                        if quality["score"] < 40:
                            continue

                        return {
                            "direction": "SHORT",
                            "level": level_price,
                            "liquidity_type": "HIGH",

                            "open_time": candle["open_time"],
                            "close_time": candle["close_time"],

                            "open": candle["open"],
                            "close": candle["close"],
                            "high": candle["high"],
                            "low": candle["low"],

                            "quality": quality["quality"],
                            "score": quality["score"],

                            "penetration_pct": quality[
                                "penetration_pct"
                            ],

                            "rejection_pct": quality[
                                "rejection_pct"
                            ],

                            "body_strength": quality[
                                "body_strength"
                            ]
                        }

    return None


# ============================================================
# STRUCTURE HELPERS
# ============================================================

def _get_confirmed_swings(candles):
    """
    Возвращает swing highs/lows для определения структуры.
    """

    swings = find_swing_levels(
        candles,
        left=SWING_LEFT,
        right=SWING_RIGHT
    )

    return swings


def _last_two_highs(swings):
    highs = swings["highs"]

    if len(highs) < 2:
        return None

    return highs[-2], highs[-1]


def _last_two_lows(swings):
    lows = swings["lows"]

    if len(lows) < 2:
        return None

    return lows[-2], lows[-1]


# ============================================================
# CHoCH / BOS
# ============================================================

def detect_structure(
    candles_15m,
    direction=None,
    lookback=STRUCTURE_LOOKBACK
):
    """
    Определяет структуру 15M.

    BOS:
        продолжение существующей структуры.

    CHoCH:
        изменение направления структуры.

    Не является обязательным условием входа.
    Используется как дополнительный Score.

    direction:
        LONG / SHORT / None

    Возвращает:
    {
        "direction": ...,
        "structure": ...,
        "score": ...,
        "broken_level": ...,
        "candle": ...
    }
    """

    if not candles_15m:
        return {
            "direction": direction,
            "structure": "NONE",
            "score": 0,
            "broken_level": None,
            "candle": None
        }

    candles = candles_15m[-lookback:]

    if len(candles) < 15:
        return {
            "direction": direction,
            "structure": "NONE",
            "score": 0,
            "broken_level": None,
            "candle": None
        }

    swings = _get_confirmed_swings(candles)

    highs = swings["highs"]
    lows = swings["lows"]

    if len(highs) < 2 or len(lows) < 2:
        return {
            "direction": direction,
            "structure": "NONE",
            "score": 0,
            "broken_level": None,
            "candle": None
        }

    last_high = highs[-1]
    previous_high = highs[-2]

    last_low = lows[-1]
    previous_low = lows[-2]

    # Последние закрытые свечи
    recent = candles[-8:]

    # =========================================================
    # LONG
    # =========================================================

    if direction == "LONG":

        # CHoCH:
        # после sweep вниз рынок пробивает последний swing high
        for candle in reversed(recent):

            if candle["close"] > last_high["price"]:

                return {
                    "direction": "LONG",
                    "structure": "CHoCH",
                    "score": 10,
                    "broken_level": last_high["price"],
                    "candle": candle
                }

        # BOS:
        # bullish продолжение через предыдущий high
        if last_high["price"] > previous_high["price"]:

            for candle in reversed(recent):

                if candle["close"] > previous_high["price"]:

                    return {
                        "direction": "LONG",
                        "structure": "BOS",
                        "score": 7,
                        "broken_level": previous_high["price"],
                        "candle": candle
                    }

    # =========================================================
    # SHORT
    # =========================================================

    if direction == "SHORT":

        # CHoCH:
        # после sweep вверх рынок пробивает последний swing low
        for candle in reversed(recent):

            if candle["close"] < last_low["price"]:

                return {
                    "direction": "SHORT",
                    "structure": "CHoCH",
                    "score": 10,
                    "broken_level": last_low["price"],
                    "candle": candle
                }

        # BOS
        if last_low["price"] < previous_low["price"]:

            for candle in reversed(recent):

                if candle["close"] < previous_low["price"]:

                    return {
                        "direction": "SHORT",
                        "structure": "BOS",
                        "score": 7,
                        "broken_level": previous_low["price"],
                        "candle": candle
                    }

    # Если направление не задано
    if direction is None:

        latest_close = candles[-1]["close"]

        if latest_close > last_high["price"]:
            structure = "BULLISH"
            structure_direction = "LONG"

        elif latest_close < last_low["price"]:
            structure = "BEARISH"
            structure_direction = "SHORT"

        else:
            structure = "RANGE"
            structure_direction = None

        return {
            "direction": structure_direction,
            "structure": structure,
            "score": 0,
            "broken_level": None,
            "candle": candles[-1]
        }

    return {
        "direction": direction,
        "structure": "NONE",
        "score": 0,
        "broken_level": None,
        "candle": candles[-1]
    }


# ============================================================
# SWEEP + STRUCTURE COMBINED
# ============================================================

def analyze_sweep_structure(
    candles_15m,
    candles_5m,
    major_levels,
    lookback=SWEEP_LOOKBACK
):
    """
    Полный блок:

    Major Liquidity
          ↓
       Sweep 2.0
          ↓
      15M CHoCH/BOS

    Это вспомогательная функция.
    Старый detect_sweep() остаётся совместимым.
    """

    sweep = detect_sweep(
        candles_5m,
        major_levels,
        lookback=lookback
    )

    if sweep is None:
        return {
            "sweep": None,
            "structure": None,
            "score_bonus": 0
        }

    direction = sweep["direction"]

    structure = detect_structure(
        candles_15m,
        direction=direction
    )

    return {
        "sweep": sweep,
        "structure": structure,
        "score_bonus": structure["score"]
    }


# ============================================================
# LIQUIDITY TARGET HELPER
# ============================================================

def find_opposing_liquidity(
    current_price,
    direction,
    major_levels
):
    """
    Находит ближайшую крупную противоположную ликвидность.

    LONG:
        ищем HIGH выше цены.

    SHORT:
        ищем LOW ниже цены.

    Используется новой логикой TP:
        если уровень ближе 2R,
        TP не должен улетать за него.
    """

    if not major_levels:
        return None

    candidates = []

    for level in major_levels:

        price = level["price"]

        if direction == "LONG":

            if (
                level["type"] == "HIGH"
                and price > current_price
            ):
                candidates.append(level)

        elif direction == "SHORT":

            if (
                level["type"] == "LOW"
                and price < current_price
            ):
                candidates.append(level)

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: abs(
            x["price"] - current_price
        )
    )

    return candidates[0]


# ============================================================
# DEBUG / TEST
# ============================================================

def market_summary(symbol=SYMBOL):
    """
    Быстрая проверка market.py.
    """

    data = get_market_data(symbol)

    price = data["price"]

    major_levels = find_major_liquidity(
        data["candles_1h"],
        current_price=price
    )

    combined = analyze_sweep_structure(
        data["candles_15m"],
        data["candles_5m"],
        major_levels
    )

    return {
        "symbol": symbol,
        "price": price,
        "major_levels": major_levels,
        "sweep": combined["sweep"],
        "structure": combined["structure"]
    }


if __name__ == "__main__":

    print("TradeMind market.py test")
    print("-" * 50)

    try:
        result = market_summary(SYMBOL)

        print(
            f"Symbol: {result['symbol']}"
        )

        print(
            f"Price: {result['price']}"
        )

        print("\nMajor Liquidity:")

        for level in result["major_levels"]:
            print(
                f"  {level['type']}: "
                f"{level['price']:.6f} "
                f"({level['distance_pct'] * 100:.2f}%)"
            )

        print("\nSweep:")

        if result["sweep"]:
            print(
                f"  {result['sweep']['direction']} "
                f"{result['sweep']['quality']} "
                f"Score={result['sweep']['score']}"
            )
        else:
            print("  NONE")

        print("\nStructure:")

        if result["structure"]:
            print(
                f"  {result['structure']['structure']} "
                f"Score={result['structure']['score']}"
            )
        else:
            print("  NONE")

    except Exception as exc:

        print(
            f"ERROR: {exc}"
        )