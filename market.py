# market.py
# TradeMind 6.6
# Binance Spot — источник рыночных данных
#
# Логика:
# 1H -> MAJOR liquidity
# 15M -> confirmation
# 5M -> ILM
# 1M -> только local context
#
# ВАЖНО:
# MAJOR liquidity пересчитывается заново при каждом get_market_data().
# Слишком близкие к текущей цене уровни НЕ считаются MAJOR.

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://api.binance.com/api/v3"
SYMBOL = "SOLUSDT"

REQUEST_TIMEOUT = 10

# Сколько свечей загружаем
LOOKBACK_1H = 180
LOOKBACK_15M = 200
LOOKBACK_5M = 200
LOOKBACK_1M = 200

# Свинг:
# 2 свечи слева + сама свеча + 2 справа
SWING_LEFT = 2
SWING_RIGHT = 2

# Кластеризация ликвидности
CLUSTER_DISTANCE_PCT = 0.15

# Размер рабочей зоны вокруг уровня
ZONE_WIDTH_PCT = 0.20

# Минимальное расстояние между MAJOR зонами
MIN_ZONE_GAP_PCT = 0.70

# ============================================================
# НОВОЕ
# ============================================================

# Уровень ближе этого расстояния к текущей цене
# НЕ МОЖЕТ быть MAJOR.
#
# Пример:
# SOL = 106.10
# MIN_MAJOR_DISTANCE_PCT = 0.30
#
# 106.12 -> 0.019% -> ОТБРАСЫВАЕМ
# 106.40 -> 0.282% -> ОТБРАСЫВАЕМ
# 106.55 -> 0.424% -> МОЖЕТ быть MAJOR
#
MIN_MAJOR_DISTANCE_PCT = 0.30

# Минимальная сила структуры
MIN_MAJOR_STRENGTH = 58

# Максимальное количество зон на сторону
MAX_LEVELS_PER_SIDE = 4

# Максимум всех зон
MAX_TOTAL_LEVELS = 8

# Возраст MAJOR-свича в 1H
MAX_LEVEL_AGE_1H = 120

# Local touch
LOCAL_TOUCH_DISTANCE_PCT = 0.20


# ============================================================
# HTTP
# ============================================================

_session = requests.Session()
_session.headers.update(
    {
        "User-Agent": "TradeMind/6.6",
        "Accept": "application/json",
    }
)


def _get(
    endpoint: str,
    params: Dict[str, Any],
) -> Any:
    url = f"{BASE_URL}/{endpoint}"

    response = _session.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# BINANCE
# ============================================================

def get_current_price() -> float:
    data = _get(
        "ticker/price",
        {"symbol": SYMBOL},
    )

    return float(data["price"])


def get_klines(
    interval: str,
    limit: int,
) -> List[Dict[str, Any]]:
    raw = _get(
        "klines",
        {
            "symbol": SYMBOL,
            "interval": interval,
            "limit": limit,
        },
    )

    candles: List[Dict[str, Any]] = []

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
# HELPERS
# ============================================================

def pct_distance(
    price_a: float,
    price_b: float,
) -> float:
    if price_b == 0:
        return 999.0

    return abs(price_a - price_b) / price_b * 100.0


def is_above(
    level: float,
    price: float,
) -> bool:
    return level > price


def is_below(
    level: float,
    price: float,
) -> bool:
    return level < price


def _safe_float(
    value: Any,
    default: float = 0.0,
) -> float:
    try:
        return float(value)
    except Exception:
        return default


# ============================================================
# SWING DETECTION
# ============================================================

def find_swing_highs(
    candles: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    swings: List[Dict[str, Any]] = []

    start = SWING_LEFT
    end = len(candles) - SWING_RIGHT

    for i in range(start, end):
        current = candles[i]
        high = current["high"]

        left = candles[
            i - SWING_LEFT:i
        ]

        right = candles[
            i + 1:i + 1 + SWING_RIGHT
        ]

        if all(high >= x["high"] for x in left) and \
           all(high >= x["high"] for x in right):

            swings.append(
                {
                    "price": high,
                    "index": i,
                    "time": current["open_time"],
                    "type": "BSL",
                }
            )

    return swings


def find_swing_lows(
    candles: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    swings: List[Dict[str, Any]] = []

    start = SWING_LEFT
    end = len(candles) - SWING_RIGHT

    for i in range(start, end):
        current = candles[i]
        low = current["low"]

        left = candles[
            i - SWING_LEFT:i
        ]

        right = candles[
            i + 1:i + 1 + SWING_RIGHT
        ]

        if all(low <= x["low"] for x in left) and \
           all(low <= x["low"] for x in right):

            swings.append(
                {
                    "price": low,
                    "index": i,
                    "time": current["open_time"],
                    "type": "SSL",
                }
            )

    return swings


# ============================================================
# CLUSTERING
# ============================================================

def cluster_levels(
    levels: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not levels:
        return []

    levels = sorted(
        levels,
        key=lambda x: x["price"],
    )

    clusters: List[List[Dict[str, Any]]] = []

    for level in levels:

        if not clusters:
            clusters.append([level])
            continue

        current_cluster = clusters[-1]

        average_price = sum(
            x["price"]
            for x in current_cluster
        ) / len(current_cluster)

        distance = pct_distance(
            level["price"],
            average_price,
        )

        if distance <= CLUSTER_DISTANCE_PCT:
            current_cluster.append(level)
        else:
            clusters.append([level])

    result: List[Dict[str, Any]] = []

    for cluster in clusters:

        prices = [
            x["price"]
            for x in cluster
        ]

        times = [
            x["time"]
            for x in cluster
        ]

        indices = [
            x["index"]
            for x in cluster
        ]

        avg_price = sum(prices) / len(prices)

        result.append(
            {
                "price": avg_price,
                "touches": len(cluster),
                "first_time": min(times),
                "last_time": max(times),
                "first_index": min(indices),
                "last_index": max(indices),
            }
        )

    return result


# ============================================================
# FRESHNESS
# ============================================================

def freshness_score(
    level: Dict[str, Any],
    candles: List[Dict[str, Any]],
) -> float:

    index = int(level["last_index"])

    age = len(candles) - 1 - index

    if age <= 10:
        return 25.0

    if age <= 30:
        return 20.0

    if age <= 60:
        return 15.0

    if age <= 90:
        return 10.0

    if age <= MAX_LEVEL_AGE_1H:
        return 5.0

    return 0.0


# ============================================================
# LOCAL TOUCHES
# ============================================================

def count_local_touches(
    level_price: float,
    candles: List[Dict[str, Any]],
) -> int:

    touches = 0

    for candle in candles:

        high = candle["high"]
        low = candle["low"]

        high_distance = pct_distance(
            high,
            level_price,
        )

        low_distance = pct_distance(
            low,
            level_price,
        )

        if high_distance <= LOCAL_TOUCH_DISTANCE_PCT:
            touches += 1

        elif low_distance <= LOCAL_TOUCH_DISTANCE_PCT:
            touches += 1

    return touches


# ============================================================
# SWEPT DETECTION
# ============================================================

def level_has_been_swept(
    level_price: float,
    level_type: str,
    candles: List[Dict[str, Any]],
) -> bool:

    for candle in candles:

        high = candle["high"]
        low = candle["low"]
        close = candle["close"]

        # BSL:
        # цена забрала хай,
        # затем закрылась обратно ниже уровня
        if level_type == "BSL":

            if high > level_price and close < level_price:
                return True

        # SSL:
        # цена забрала лоу,
        # затем закрылась обратно выше уровня
        elif level_type == "SSL":

            if low < level_price and close > level_price:
                return True

    return False


# ============================================================
# STRENGTH
# ============================================================

def calculate_strength(
    level: Dict[str, Any],
    candles_1h: List[Dict[str, Any]],
    candles_15m: List[Dict[str, Any]],
) -> float:

    score = 50.0

    touches = int(
        level.get("touches", 1)
    )

    # Повторные тесты уровня
    if touches >= 2:
        score += 8

    if touches >= 3:
        score += 6

    if touches >= 4:
        score += 5

    # Свежесть
    score += freshness_score(
        level,
        candles_1h,
    )

    # Локальные подтверждения
    local_touches = count_local_touches(
        level["price"],
        candles_15m,
    )

    if local_touches >= 2:
        score += 4

    if local_touches >= 4:
        score += 4

    return min(
        round(score, 2),
        100.0,
    )


# ============================================================
# MAJOR ZONE SELECTION
# ============================================================

def select_major_zones(
    levels: List[Dict[str, Any]],
    price: float,
    candles_1h: List[Dict[str, Any]],
    candles_15m: List[Dict[str, Any]],
    level_type: str,
) -> List[Dict[str, Any]]:

    candidates: List[Dict[str, Any]] = []

    for level in levels:

        level_price = level["price"]

        # ----------------------------------------------------
        # Направление
        # ----------------------------------------------------

        if level_type == "BSL":

            if level_price <= price:
                continue

        elif level_type == "SSL":

            if level_price >= price:
                continue

        # ----------------------------------------------------
        # НОВЫЙ ФИЛЬТР ДИСТАНЦИИ
        # ----------------------------------------------------

        distance = pct_distance(
            price,
            level_price,
        )

        if distance < MIN_MAJOR_DISTANCE_PCT:
            # Это уже практически текущая цена.
            # Такой уровень не должен называться MAJOR.
            continue

        # ----------------------------------------------------
        # Слишком старый уровень
        # ----------------------------------------------------

        age = (
            len(candles_1h)
            - 1
            - int(level["last_index"])
        )

        if age > MAX_LEVEL_AGE_1H:
            continue

        # ----------------------------------------------------
        # Sweep
        # ----------------------------------------------------

        if level_has_been_swept(
            level_price,
            level_type,
            candles_1h,
        ):
            continue

        # ----------------------------------------------------
        # Strength
        # ----------------------------------------------------

        strength = calculate_strength(
            level,
            candles_1h,
            candles_15m,
        )

        if strength < MIN_MAJOR_STRENGTH:
            continue

        zone_low = (
            level_price
            * (1 - ZONE_WIDTH_PCT / 100)
        )

        zone_high = (
            level_price
            * (1 + ZONE_WIDTH_PCT / 100)
        )

        candidates.append(
            {
                "price": round(level_price, 4),
                "zone_low": round(zone_low, 4),
                "zone_high": round(zone_high, 4),
                "type": level_type,
                "strength": round(strength, 2),
                "touches": level["touches"],
                "distance_pct": round(distance, 3),
                "age_1h": age,
                "status": "FRESH",
            }
        )

    # --------------------------------------------------------
    # Сначала сильные зоны
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x: (
            x["strength"],
            x["touches"],
            -x["distance_pct"],
        ),
        reverse=True,
    )

    selected: List[Dict[str, Any]] = []

    for candidate in candidates:

        too_close = False

        for existing in selected:

            distance = pct_distance(
                candidate["price"],
                existing["price"],
            )

            if distance < MIN_ZONE_GAP_PCT:
                too_close = True
                break

        if too_close:
            continue

        selected.append(candidate)

        if len(selected) >= MAX_LEVELS_PER_SIDE:
            break

    # --------------------------------------------------------
    # Для отображения сортируем по близости к цене
    # --------------------------------------------------------

    selected.sort(
        key=lambda x: x["distance_pct"]
    )

    return selected


# ============================================================
# MAJOR LIQUIDITY
# ============================================================

def find_major_liquidity(
    candles_1h: List[Dict[str, Any]],
    candles_15m: List[Dict[str, Any]],
    price: float,
) -> Dict[str, List[Dict[str, Any]]]:

    # --------------------------------------------------------
    # ВАЖНО:
    # последняя 1H свеча может быть ещё формирующейся.
    # Не используем её как окончательный swing.
    # --------------------------------------------------------

    confirmed_1h = candles_1h[:-1]

    swing_highs = find_swing_highs(
        confirmed_1h
    )

    swing_lows = find_swing_lows(
        confirmed_1h
    )

    # --------------------------------------------------------
    # Кластеры
    # --------------------------------------------------------

    bsl_clusters = cluster_levels(
        swing_highs
    )

    ssl_clusters = cluster_levels(
        swing_lows
    )

    # --------------------------------------------------------
    # Выбор MAJOR
    # --------------------------------------------------------

    bsl = select_major_zones(
        bsl_clusters,
        price,
        confirmed_1h,
        candles_15m,
        "BSL",
    )

    ssl = select_major_zones(
        ssl_clusters,
        price,
        confirmed_1h,
        candles_15m,
        "SSL",
    )

    # --------------------------------------------------------
    # Общий лимит
    # --------------------------------------------------------

    combined = (
        [("BSL", x) for x in bsl]
        + [("SSL", x) for x in ssl]
    )

    combined.sort(
        key=lambda item: item[1]["strength"],
        reverse=True,
    )

    combined = combined[
        :MAX_TOTAL_LEVELS
    ]

    result_bsl = [
        x for side, x in combined
        if side == "BSL"
    ]

    result_ssl = [
        x for side, x in combined
        if side == "SSL"
    ]

    # После общего лимита снова сортируем
    result_bsl.sort(
        key=lambda x: x["distance_pct"]
    )

    result_ssl.sort(
        key=lambda x: x["distance_pct"]
    )

    return {
        "BSL": result_bsl,
        "SSL": result_ssl,
    }


# ============================================================
# TARGET LIQUIDITY
# ============================================================

def get_target_liquidity(
    major_liquidity: Dict[str, List[Dict[str, Any]]],
    direction: str,
    entry: float,
) -> Optional[Dict[str, Any]]:

    if direction == "LONG":

        levels = major_liquidity.get(
            "BSL",
            [],
        )

        candidates = [
            x
            for x in levels
            if x["price"] > entry
        ]

    else:

        levels = major_liquidity.get(
            "SSL",
            [],
        )

        candidates = [
            x
            for x in levels
            if x["price"] < entry
        ]

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: abs(
            x["price"] - entry
        )
    )

    return candidates[0]


# ============================================================
# MARKET DATA
# ============================================================

def get_market_data() -> Dict[str, Any]:

    # --------------------------------------------------------
    # ВАЖНО:
    # всё загружается заново.
    # Никакого старого кеша зон.
    # --------------------------------------------------------

    candles_1h = get_klines(
        "1h",
        LOOKBACK_1H,
    )

    candles_15m = get_klines(
        "15m",
        LOOKBACK_15M,
    )

    candles_5m = get_klines(
        "5m",
        LOOKBACK_5M,
    )

    candles_1m = get_klines(
        "1m",
        LOOKBACK_1M,
    )

    price = get_current_price()

    # --------------------------------------------------------
    # ПОЛНЫЙ ПЕРЕСЧЁТ MAJOR
    # --------------------------------------------------------

    major_liquidity = find_major_liquidity(
        candles_1h,
        candles_15m,
        price,
    )

    return {
        "symbol": SYMBOL,
        "price": price,

        "candles": {
            "1h": candles_1h,
            "15m": candles_15m,
            "5m": candles_5m,
            "1m": candles_1m,
        },

        "major_liquidity": major_liquidity,

        "updated_at": time.time(),
    }


# ============================================================
# COMPATIBILITY
# ============================================================

def get_major_liquidity(
    price: Optional[float] = None,
) -> Dict[str, List[Dict[str, Any]]]:

    candles_1h = get_klines(
        "1h",
        LOOKBACK_1H,
    )

    candles_15m = get_klines(
        "15m",
        LOOKBACK_15M,
    )

    if price is None:
        price = get_current_price()

    return find_major_liquidity(
        candles_1h,
        candles_15m,
        price,
    )


def market_snapshot() -> Dict[str, Any]:
    return get_market_data()


# ============================================================
# DEBUG
# ============================================================

def format_major_liquidity(
    data: Dict[str, Any],
) -> str:

    price = data["price"]
    liquidity = data["major_liquidity"]

    lines = []

    lines.append(
        f"💰 SOL: ${price:.4f}"
    )

    lines.append("")
    lines.append("🔴 BSL MAJOR")

    bsl = liquidity.get("BSL", [])

    if not bsl:
        lines.append("— нет актуальных зон")

    for i, zone in enumerate(bsl, 1):

        lines.append(
            f"{i}. ${zone['price']:.4f} "
            f"• {zone['distance_pct']:.2f}% "
            f"• S{zone['strength']:.0f}"
        )

    lines.append("")
    lines.append("🟢 SSL MAJOR")

    ssl = liquidity.get("SSL", [])

    if not ssl:
        lines.append("— нет актуальных зон")

    for i, zone in enumerate(ssl, 1):

        lines.append(
            f"{i}. ${zone['price']:.4f} "
            f"• {zone['distance_pct']:.2f}% "
            f"• S{zone['strength']:.0f}"
        )

    return "\n".join(lines)


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    print("TradeMind market.py 6.6")

    try:
        data = get_market_data()

        print(
            format_major_liquidity(data)
        )

    except Exception as e:
        print(
            f"MARKET ERROR: {e}"
        )