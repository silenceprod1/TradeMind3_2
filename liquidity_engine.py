"""
TradeMind Liquidity Engine 1.0

Определение Major Liquidity для TradeMind.

Основная идея:

1H structure
    ↓
Major BSL / SSL
    ↓
Order Book confirmation
    ↓
Open Interest confirmation
    ↓
Taker Buy / Sell confirmation
    ↓
Liquidity strength

ВАЖНО:
- Основной источник Major Liquidity = 1H структура.
- Стакан / OI / flow НЕ создают Major уровень сами по себе.
- Они только подтверждают или ослабляют структурный уровень.
- D1 / W1 здесь НЕ используются.
- Локальные 5M / 15M уровни НЕ становятся Major Liquidity.
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# CONFIG
# ============================================================

BINANCE_FUTURES_URL = "https://fapi.binance.com"

REQUEST_TIMEOUT = 8

DEFAULT_1H_LIMIT = 300
DEFAULT_ORDERBOOK_LIMIT = 1000

# Минимальное расстояние между разными структурными уровнями.
MIN_LEVEL_DISTANCE_PCT = 0.10

# Кластеризация близких swing levels.
CLUSTER_DISTANCE_PCT = 0.20

# Максимальное количество Major уровней.
MAX_MAJOR_LEVELS = 12

# Сколько последних свечей использовать для поиска структуры.
STRUCTURE_LOOKBACK = 160

# Swing strength.
SWING_LEFT = 2
SWING_RIGHT = 2

# Минимальная глубина для order book confirmation.
ORDERBOOK_MIN_DISTANCE_PCT = 0.05

# Насколько близко должна находиться стаканная ликвидность
# к структурному уровню, чтобы считаться подтверждением.
ORDERBOOK_CONFIRM_DISTANCE_PCT = 0.35

# Минимальный коэффициент дисбаланса стакана.
ORDERBOOK_IMBALANCE_MIN = 1.15

# ============================================================
# HTTP SESSION
# ============================================================

_SESSION = requests.Session()

_SESSION.headers.update(
    {
        "User-Agent": "TradeMind-LiquidityEngine/1.0",
        "Accept": "application/json",
    }
)


# ============================================================
# BASIC HELPERS
# ============================================================

def _f(value: Any, default: Optional[float] = None) -> Optional[float]:
    """
    Безопасное преобразование в float.
    """
    try:
        if value is None:
            return default

        result = float(value)

        if not math.isfinite(result):
            return default

        return result

    except (TypeError, ValueError):
        return default


def _pct_distance(a: float, b: float) -> float:
    """
    Процентное расстояние между двумя ценами.
    """
    if a <= 0 or b <= 0:
        return 999.0

    return abs(a - b) / b * 100.0


def _safe_symbol(symbol: str) -> str:
    """
    Binance Futures symbol.
    """
    return str(symbol or "").upper().replace("/", "")


def _request(
    endpoint: str,
    params: Optional[Dict[str, Any]] = None,
) -> Optional[Any]:
    """
    Безопасный GET-запрос к Binance Futures.
    """
    try:
        response = _SESSION.get(
            BINANCE_FUTURES_URL + endpoint,
            params=params or {},
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        return response.json()

    except Exception:
        return None


# ============================================================
# BINANCE 1H KLINES
# ============================================================

def fetch_1h_candles(
    symbol: str,
    limit: int = DEFAULT_1H_LIMIT,
) -> List[Dict[str, Any]]:
    """
    Получает 1H свечи Binance Futures.

    Возвращает нормализованный список:
    {
        open_time,
        open,
        high,
        low,
        close,
        volume
    }
    """

    symbol = _safe_symbol(symbol)

    if not symbol:
        return []

    data = _request(
        "/fapi/v1/klines",
        {
            "symbol": symbol,
            "interval": "1h",
            "limit": max(50, min(int(limit), 1500)),
        },
    )

    if not isinstance(data, list):
        return []

    candles: List[Dict[str, Any]] = []

    for row in data:
        try:
            if len(row) < 6:
                continue

            candles.append(
                {
                    "open_time": int(row[0]),
                    "open": _f(row[1]),
                    "high": _f(row[2]),
                    "low": _f(row[3]),
                    "close": _f(row[4]),
                    "volume": _f(row[5], 0.0),
                }
            )

        except Exception:
            continue

    return candles


# ============================================================
# CURRENT PRICE
# ============================================================

def fetch_current_price(symbol: str) -> Optional[float]:
    """
    Текущая цена Binance Futures.
    """

    symbol = _safe_symbol(symbol)

    data = _request(
        "/fapi/v1/ticker/price",
        {
            "symbol": symbol,
        },
    )

    if not isinstance(data, dict):
        return None

    return _f(data.get("price"))


# ============================================================
# ORDER BOOK
# ============================================================

def fetch_order_book(
    symbol: str,
    limit: int = DEFAULT_ORDERBOOK_LIMIT,
) -> Dict[str, Any]:
    """
    Получает текущий стакан Binance Futures.

    Возвращает:
    {
        bids: [(price, qty), ...],
        asks: [(price, qty), ...]
    }
    """

    symbol = _safe_symbol(symbol)

    data = _request(
        "/fapi/v1/depth",
        {
            "symbol": symbol,
            "limit": max(
                5,
                min(int(limit), 1000),
            ),
        },
    )

    if not isinstance(data, dict):
        return {
            "bids": [],
            "asks": [],
        }

    bids: List[Tuple[float, float]] = []
    asks: List[Tuple[float, float]] = []

    for row in data.get("bids", []):
        try:
            if len(row) < 2:
                continue

            price = _f(row[0])
            qty = _f(row[1], 0.0)

            if price is not None and qty is not None:
                bids.append((price, qty))

        except Exception:
            continue

    for row in data.get("asks", []):
        try:
            if len(row) < 2:
                continue

            price = _f(row[0])
            qty = _f(row[1], 0.0)

            if price is not None and qty is not None:
                asks.append((price, qty))

        except Exception:
            continue

    return {
        "bids": bids,
        "asks": asks,
    }


# ============================================================
# OPEN INTEREST
# ============================================================

def fetch_open_interest(symbol: str) -> Optional[float]:
    """
    Текущий Open Interest Binance Futures.
    """

    symbol = _safe_symbol(symbol)

    data = _request(
        "/fapi/v1/openInterest",
        {
            "symbol": symbol,
        },
    )

    if not isinstance(data, dict):
        return None

    return _f(data.get("openInterest"))


# ============================================================
# 1H OPEN INTEREST HISTORY
# ============================================================

def fetch_open_interest_history(
    symbol: str,
    period: str = "1h",
    limit: int = 24,
) -> List[Dict[str, Any]]:
    """
    История OI.

    Используется как дополнительное подтверждение активности,
    а не для построения самого Major уровня.
    """

    symbol = _safe_symbol(symbol)

    data = _request(
        "/futures/data/openInterestHist",
        {
            "symbol": symbol,
            "period": period,
            "limit": max(1, min(int(limit), 500)),
        },
    )

    if not isinstance(data, list):
        return []

    result: List[Dict[str, Any]] = []

    for row in data:
        if not isinstance(row, dict):
            continue

        result.append(
            {
                "timestamp": int(
                    _f(row.get("timestamp"), 0.0) or 0
                ),
                "sumOpenInterest": _f(
                    row.get("sumOpenInterest")
                ),
                "sumOpenInterestValue": _f(
                    row.get("sumOpenInterestValue")
                ),
            }
        )

    return result


# ============================================================
# TAKER BUY / SELL
# ============================================================

def fetch_taker_volume(
    symbol: str,
    period: str = "1h",
    limit: int = 24,
) -> List[Dict[str, Any]]:
    """
    Получает taker buy/sell volume.

    Endpoint Binance Futures:
    /futures/data/takerlongshortRatio

    Важно:
    Это дополнительный flow confirmation.
    """

    symbol = _safe_symbol(symbol)

    data = _request(
        "/futures/data/takerlongshortRatio",
        {
            "symbol": symbol,
            "period": period,
            "limit": max(1, min(int(limit), 500)),
        },
    )

    if not isinstance(data, list):
        return []

    result: List[Dict[str, Any]] = []

    for row in data:
        if not isinstance(row, dict):
            continue

        buy = _f(row.get("buyVol"), 0.0) or 0.0
        sell = _f(row.get("sellVol"), 0.0) or 0.0

        ratio = _f(row.get("buySellRatio"))

        if ratio is None:
            if sell > 0:
                ratio = buy / sell
            else:
                ratio = 1.0

        result.append(
            {
                "timestamp": int(
                    _f(row.get("timestamp"), 0.0) or 0
                ),
                "buy_volume": buy,
                "sell_volume": sell,
                "ratio": ratio,
            }
        )

    return result


# ============================================================
# SWING DETECTION
# ============================================================

def _is_swing_high(
    candles: List[Dict[str, Any]],
    index: int,
) -> bool:

    if index < SWING_LEFT:
        return False

    if index + SWING_RIGHT >= len(candles):
        return False

    current = _f(candles[index].get("high"))

    if current is None:
        return False

    left = candles[
        index - SWING_LEFT:index
    ]

    right = candles[
        index + 1:index + SWING_RIGHT + 1
    ]

    for candle in left + right:
        high = _f(candle.get("high"))

        if high is None:
            return False

        if high >= current:
            return False

    return True


def _is_swing_low(
    candles: List[Dict[str, Any]],
    index: int,
) -> bool:

    if index < SWING_LEFT:
        return False

    if index + SWING_RIGHT >= len(candles):
        return False

    current = _f(candles[index].get("low"))

    if current is None:
        return False

    left = candles[
        index - SWING_LEFT:index
    ]

    right = candles[
        index + 1:index + SWING_RIGHT + 1
    ]

    for candle in left + right:
        low = _f(candle.get("low"))

        if low is None:
            return False

        if low <= current:
            return False

    return True


# ============================================================
# STRUCTURAL SWINGS
# ============================================================

def find_structural_swings(
    candles: List[Dict[str, Any]],
) -> Tuple[
    List[Dict[str, Any]],
    List[Dict[str, Any]],
]:
    """
    Находит 1H swing highs / lows.

    Только 1H.
    """

    if not candles:
        return [], []

    source = candles[-STRUCTURE_LOOKBACK:]

    highs: List[Dict[str, Any]] = []
    lows: List[Dict[str, Any]] = []

    for i in range(len(source)):

        candle = source[i]

        if _is_swing_high(source, i):
            price = _f(candle.get("high"))

            if price is not None:
                highs.append(
                    {
                        "price": price,
                        "index": i,
                        "open_time": candle.get(
                            "open_time"
                        ),
                        "volume": _f(
                            candle.get("volume"),
                            0.0,
                        ),
                    }
                )

        if _is_swing_low(source, i):
            price = _f(candle.get("low"))

            if price is not None:
                lows.append(
                    {
                        "price": price,
                        "index": i,
                        "open_time": candle.get(
                            "open_time"
                        ),
                        "volume": _f(
                            candle.get("volume"),
                            0.0,
                        ),
                    }
                )

    return highs, lows


# ============================================================
# CLUSTER LEVELS
# ============================================================

def cluster_swing_levels(
    swings: List[Dict[str, Any]],
    side: str,
) -> List[Dict[str, Any]]:
    """
    Объединяет близкие swing levels.

    side:
        BSL
        SSL
    """

    if not swings:
        return []

    ordered = sorted(
        swings,
        key=lambda x: float(
            x.get("price", 0.0)
        ),
    )

    clusters: List[List[Dict[str, Any]]] = []

    for swing in ordered:

        price = _f(swing.get("price"))

        if price is None:
            continue

        if not clusters:
            clusters.append([swing])
            continue

        last_cluster = clusters[-1]

        reference_price = _f(
            last_cluster[-1].get("price")
        )

        if reference_price is None:
            clusters.append([swing])
            continue

        distance = _pct_distance(
            price,
            reference_price,
        )

        if distance <= CLUSTER_DISTANCE_PCT:
            last_cluster.append(swing)
        else:
            clusters.append([swing])

    levels: List[Dict[str, Any]] = []

    for cluster in clusters:

        prices = [
            _f(x.get("price"))
            for x in cluster
        ]

        prices = [
            x for x in prices
            if x is not None
        ]

        if not prices:
            continue

        price = sum(prices) / len(prices)

        touches = len(cluster)

        latest_index = max(
            int(
                x.get("index", 0)
            )
            for x in cluster
        )

        strength = 50.0

        # Повторные касания повышают силу.
        strength += min(
            20.0,
            max(0, touches - 1) * 8.0,
        )

        # Чем свежее swing, тем сильнее.
        age = len(
            swings
        ) - latest_index

        if age <= 10:
            strength += 15
        elif age <= 25:
            strength += 10
        elif age <= 50:
            strength += 5

        strength = min(
            90.0,
            strength,
        )

        levels.append(
            {
                "price": price,
                "side": side,
                "type": (
                    "BSL_1H"
                    if side == "BSL"
                    else "SSL_1H"
                ),
                "source": "1H",
                "timeframe": "1H",
                "touches": touches,
                "strength": round(
                    strength,
                    2,
                ),
                "freshness": (
                    "fresh"
                    if age <= 25
                    else "normal"
                ),
                "priority": round(
                    strength,
                    2,
                ),
                "swing_index": latest_index,
            }
        )

    return levels


# ============================================================
# ORDER BOOK LIQUIDITY NEAR PRICE
# ============================================================

def _aggregate_book_liquidity(
    entries: List[Tuple[float, float]],
    reference_price: float,
    max_distance_pct: float,
) -> float:
    """
    Суммарный объём заявок в заданном диапазоне.
    """

    total = 0.0

    for price, quantity in entries:

        if price <= 0 or quantity <= 0:
            continue

        distance = _pct_distance(
            price,
            reference_price,
        )

        if distance <= max_distance_pct:
            total += price * quantity

    return total


def find_orderbook_confirmation(
    order_book: Dict[str, Any],
    level_price: float,
    side: str,
) -> Dict[str, Any]:
    """
    Проверяет наличие крупной стаканной ликвидности
    рядом с Major уровнем.

    Для BSL:
        смотрим asks выше уровня.

    Для SSL:
        смотрим bids ниже уровня.
    """

    bids = order_book.get(
        "bids",
        [],
    )

    asks = order_book.get(
        "asks",
        [],
    )

    if side == "BSL":

        relevant = [
            (price, qty)
            for price, qty in asks
            if price >= level_price
        ]

        opposite = [
            (price, qty)
            for price, qty in bids
        ]

    else:

        relevant = [
            (price, qty)
            for price, qty in bids
            if price <= level_price
        ]

        opposite = [
            (price, qty)
            for price, qty in asks
        ]

    relevant_volume = _aggregate_book_liquidity(
        relevant,
        level_price,
        ORDERBOOK_CONFIRM_DISTANCE_PCT,
    )

    opposite_volume = _aggregate_book_liquidity(
        opposite,
        level_price,
        ORDERBOOK_CONFIRM_DISTANCE_PCT,
    )

    if opposite_volume > 0:
        imbalance = (
            relevant_volume
            / opposite_volume
        )
    else:
        imbalance = (
            2.0
            if relevant_volume > 0
            else 1.0
        )

    confirmed = (
        relevant_volume > 0
        and imbalance >= ORDERBOOK_IMBALANCE_MIN
    )

    strength_bonus = 0.0

    if confirmed:
        strength_bonus += 8.0

    if imbalance >= 1.50:
        strength_bonus += 5.0

    if imbalance >= 2.00:
        strength_bonus += 5.0

    return {
        "confirmed": confirmed,
        "relevant_volume": relevant_volume,
        "opposite_volume": opposite_volume,
        "imbalance": round(
            imbalance,
            3,
        ),
        "bonus": min(
            15.0,
            strength_bonus,
        ),
    }


# ============================================================
# OI CONFIRMATION
# ============================================================

def analyze_oi_confirmation(
    history: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Анализирует изменение OI.

    Используется только как подтверждение активности.
    """

    values: List[float] = []

    for row in history:
        value = _f(
            row.get(
                "sumOpenInterestValue"
            )
        )

        if value is None:
            value = _f(
                row.get(
                    "sumOpenInterest"
                )
            )

        if value is not None:
            values.append(value)

    if len(values) < 2:
        return {
            "available": False,
            "change_pct": 0.0,
            "rising": False,
            "falling": False,
            "bonus": 0.0,
        }

    first = values[0]
    last = values[-1]

    if first <= 0:
        return {
            "available": False,
            "change_pct": 0.0,
            "rising": False,
            "falling": False,
            "bonus": 0.0,
        }

    change_pct = (
        (last - first)
        / first
        * 100.0
    )

    rising = change_pct >= 2.0
    falling = change_pct <= -2.0

    bonus = 0.0

    if abs(change_pct) >= 2.0:
        bonus += 3.0

    if abs(change_pct) >= 5.0:
        bonus += 3.0

    return {
        "available": True,
        "change_pct": round(
            change_pct,
            3,
        ),
        "rising": rising,
        "falling": falling,
        "bonus": min(
            6.0,
            bonus,
        ),
    }


# ============================================================
# FLOW CONFIRMATION
# ============================================================

def analyze_flow_confirmation(
    taker_history: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Анализирует taker buy/sell flow.
    """

    if not taker_history:
        return {
            "available": False,
            "buy_volume": 0.0,
            "sell_volume": 0.0,
            "ratio": 1.0,
            "dominance": "NEUTRAL",
            "bonus": 0.0,
        }

    recent = taker_history[-6:]

    buy = sum(
        _f(
            row.get(
                "buy_volume"
            ),
            0.0,
        )
        or 0.0
        for row in recent
    )

    sell = sum(
        _f(
            row.get(
                "sell_volume"
            ),
            0.0,
        )
        or 0.0
        for row in recent
    )

    if sell > 0:
        ratio = buy / sell
    else:
        ratio = 2.0 if buy > 0 else 1.0

    if ratio >= 1.20:
        dominance = "BUYERS"
    elif ratio <= 0.83:
        dominance = "SELLERS"
    else:
        dominance = "NEUTRAL"

    bonus = 0.0

    if ratio >= 1.20 or ratio <= 0.83:
        bonus += 2.0

    if ratio >= 1.50 or ratio <= 0.67:
        bonus += 3.0

    return {
        "available": True,
        "buy_volume": buy,
        "sell_volume": sell,
        "ratio": round(
            ratio,
            3,
        ),
        "dominance": dominance,
        "bonus": min(
            5.0,
            bonus,
        ),
    }


# ============================================================
# LEVEL STRENGTH
# ============================================================

def calculate_level_strength(
    structural_strength: float,
    orderbook_confirmation: Dict[str, Any],
    oi_confirmation: Dict[str, Any],
    flow_confirmation: Dict[str, Any],
) -> float:
    """
    Итоговая сила Major уровня.

    Структура имеет главный вес.
    """

    strength = float(
        structural_strength
    )

    strength += float(
        orderbook_confirmation.get(
            "bonus",
            0.0,
        )
        or 0.0
    )

    strength += float(
        oi_confirmation.get(
            "bonus",
            0.0,
        )
        or 0.0
    )

    strength += float(
        flow_confirmation.get(
            "bonus",
            0.0,
        )
        or 0.0
    )

    return round(
        min(
            100.0,
            strength,
        ),
        2,
    )


# ============================================================
# FILTER LEVELS BY CURRENT PRICE
# ============================================================

def filter_major_levels_by_price(
    levels: List[Dict[str, Any]],
    current_price: float,
) -> List[Dict[str, Any]]:
    """
    BSL только выше текущей цены.
    SSL только ниже текущей цены.

    Это критически важно для TradeMind.
    """

    result: List[Dict[str, Any]] = []

    for level in levels:

        price = _f(
            level.get("price")
        )

        side = str(
            level.get("side") or ""
        ).upper()

        if price is None:
            continue

        if side == "BSL":
            if price <= current_price:
                continue

        elif side == "SSL":
            if price >= current_price:
                continue

        else:
            continue

        result.append(level)

    return result


# ============================================================
# REMOVE DUPLICATES
# ============================================================

def deduplicate_levels(
    levels: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Убирает почти одинаковые Major уровни.
    """

    result: List[Dict[str, Any]] = []

    sorted_levels = sorted(
        levels,
        key=lambda x: (
            str(
                x.get("side")
            ),
            float(
                x.get("price", 0.0)
            ),
        ),
    )

    for level in sorted_levels:

        price = _f(
            level.get("price")
        )

        if price is None:
            continue

        duplicate = False

        for existing in result:

            existing_price = _f(
                existing.get("price")
            )

            if existing_price is None:
                continue

            if (
                level.get("side")
                == existing.get("side")
                and _pct_distance(
                    price,
                    existing_price,
                )
                <= MIN_LEVEL_DISTANCE_PCT
            ):
                duplicate = True

                # Оставляем более сильный.
                if (
                    float(
                        level.get(
                            "strength",
                            0.0,
                        )
                    )
                    >
                    float(
                        existing.get(
                            "strength",
                            0.0,
                        )
                    )
                ):
                    existing.update(level)

                break

        if not duplicate:
            result.append(level)

    return result


# ============================================================
# SORT MAJOR LEVELS
# ============================================================

def sort_major_levels(
    levels: List[Dict[str, Any]],
    current_price: float,
) -> List[Dict[str, Any]]:
    """
    Сортировка по направлению и расстоянию.

    Приоритет:
        1. правильная сторона
        2. близость
        3. strength
    """

    def key(level: Dict[str, Any]):
        price = _f(
            level.get("price"),
            current_price,
        )

        distance = _pct_distance(
            price,
            current_price,
        )

        strength = float(
            level.get(
                "strength",
                0.0,
            )
            or 0.0
        )

        # Небольшое преимущество более сильному уровню,
        # но расстояние остаётся главным фактором.
        adjusted = (
            distance
            - strength * 0.001
        )

        return adjusted

    return sorted(
        levels,
        key=key,
    )


# ============================================================
# BUILD STRUCTURAL MAJOR LEVELS
# ============================================================

def build_structural_major_levels(
    candles_1h: List[Dict[str, Any]],
    current_price: float,
) -> List[Dict[str, Any]]:
    """
    Основной структурный движок Major Liquidity.

    Только 1H.
    """

    highs, lows = find_structural_swings(
        candles_1h
    )

    bsl = cluster_swing_levels(
        highs,
        "BSL",
    )

    ssl = cluster_swing_levels(
        lows,
        "SSL",
    )

    levels = bsl + ssl

    levels = filter_major_levels_by_price(
        levels,
        current_price,
    )

    levels = deduplicate_levels(
        levels
    )

    levels = sort_major_levels(
        levels,
        current_price,
    )

    return levels[:MAX_MAJOR_LEVELS]


# ============================================================
# ENRICH LEVELS WITH MARKET DATA
# ============================================================

def enrich_levels(
    symbol: str,
    levels: List[Dict[str, Any]],
    current_price: float,
    order_book: Optional[Dict[str, Any]] = None,
    oi_history: Optional[List[Dict[str, Any]]] = None,
    taker_history: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Добавляет к структурным уровням:

    - order book confirmation
    - OI confirmation
    - taker flow
    - final strength

    Структура остаётся основой.
    """

    if order_book is None:
        order_book = fetch_order_book(
            symbol
        )

    if oi_history is None:
        oi_history = fetch_open_interest_history(
            symbol
        )

    if taker_history is None:
        taker_history = fetch_taker_volume(
            symbol
        )

    oi_confirmation = analyze_oi_confirmation(
        oi_history
    )

    flow_confirmation = analyze_flow_confirmation(
        taker_history
    )

    enriched: List[Dict[str, Any]] = []

    for level in levels:

        price = _f(
            level.get("price")
        )

        if price is None:
            continue

        side = str(
            level.get("side") or ""
        ).upper()

        book_confirmation = find_orderbook_confirmation(
            order_book,
            price,
            side,
        )

        final_strength = calculate_level_strength(
            float(
                level.get(
                    "strength",
                    50.0,
                )
                or 50.0
            ),
            book_confirmation,
            oi_confirmation,
            flow_confirmation,
        )

        distance = _pct_distance(
            price,
            current_price,
        )

        enriched_level = dict(
            level
        )

        enriched_level.update(
            {
                "strength": final_strength,
                "structural_strength": float(
                    level.get(
                        "strength",
                        50.0,
                    )
                    or 50.0
                ),
                "distance_pct": round(
                    distance,
                    3,
                ),
                "orderbook": book_confirmation,
                "oi": oi_confirmation,
                "flow": flow_confirmation,
                "market_confirmation": (
                    bool(
                        book_confirmation.get(
                            "confirmed",
                            False,
                        )
                    )
                    or bool(
                        oi_confirmation.get(
                            "available",
                            False,
                        )
                    )
                    or bool(
                        flow_confirmation.get(
                            "available",
                            False,
                        )
                    )
                ),
                "source": "1H+FUTURES",
                "major": True,
            }
        )

        enriched.append(
            enriched_level
        )

    return sort_major_levels(
        enriched,
        current_price,
    )


# ============================================================
# FIND MAJOR LIQUIDITY
# ============================================================

def find_major_liquidity(
    symbol: str,
    current_price: Optional[float] = None,
    candles_1h: Optional[
        List[Dict[str, Any]]
    ] = None,
    use_futures_confirmation: bool = True,
) -> List[Dict[str, Any]]:
    """
    Главная функция.

    Можно использовать двумя способами:

    1)
        find_major_liquidity(
            "SOLUSDT",
            current_price=113.96,
            candles_1h=candles
        )

    2)
        find_major_liquidity(
            "SOLUSDT"
        )

    Во втором случае цена и свечи будут
    загружены автоматически.
    """

    symbol = _safe_symbol(
        symbol
    )

    if not symbol:
        return []

    if current_price is None:
        current_price = fetch_current_price(
            symbol
        )

    if current_price is None or current_price <= 0:
        return []

    if candles_1h is None:
        candles_1h = fetch_1h_candles(
            symbol
        )

    if len(candles_1h) < 20:
        return []

    levels = build_structural_major_levels(
        candles_1h,
        current_price,
    )

    if not levels:
        return []

    if not use_futures_confirmation:
        return levels

    try:
        return enrich_levels(
            symbol,
            levels,
            current_price,
        )

    except Exception:
        # Структурные уровни должны продолжать работать,
        # даже если Futures confirmation временно недоступен.
        return levels


# ============================================================
# GET PRIMARY LEVEL
# ============================================================

def get_primary_major_level(
    levels: List[Dict[str, Any]],
    current_price: float,
    side: str,
) -> Optional[Dict[str, Any]]:
    """
    Получает ближайшую Major Liquidity
    нужного типа.

    LONG:
        ближайший SSL ниже цены

    SHORT:
        ближайший BSL выше цены
    """

    side = str(
        side or ""
    ).upper()

    candidates: List[
        Dict[str, Any]
    ] = []

    for level in levels:

        level_side = str(
            level.get("side") or ""
        ).upper()

        price = _f(
            level.get("price")
        )

        if price is None:
            continue

        if side == "SSL":
            if level_side != "SSL":
                continue

            if price >= current_price:
                continue

        elif side == "BSL":
            if level_side != "BSL":
                continue

            if price <= current_price:
                continue

        else:
            continue

        candidates.append(level)

    if not candidates:
        return None

    return min(
        candidates,
        key=lambda x: _pct_distance(
            _f(
                x.get("price"),
                current_price,
            )
            or current_price,
            current_price,
        ),
    )


# ============================================================
# GET NEXT TARGET
# ============================================================

def get_next_major_target(
    levels: List[Dict[str, Any]],
    current_price: float,
    direction: str,
    excluded_price: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Следующая свежая Major Liquidity.

    LONG:
        следующий BSL выше цены.

    SHORT:
        следующий SSL ниже цены.

    Никаких D1 / W1 / локальных TP.
    """

    direction = str(
        direction or ""
    ).upper()

    target_side = (
        "BSL"
        if direction == "LONG"
        else "SSL"
    )

    candidates: List[
        Dict[str, Any]
    ] = []

    for level in levels:

        price = _f(
            level.get("price")
        )

        side = str(
            level.get("side") or ""
        ).upper()

        if price is None:
            continue

        if side != target_side:
            continue

        if excluded_price is not None:
            if _pct_distance(
                price,
                excluded_price,
            ) < 0.05:
                continue

        if direction == "LONG":

            if price <= current_price:
                continue

        elif direction == "SHORT":

            if price >= current_price:
                continue

        else:
            continue

        candidates.append(level)

    if not candidates:
        return None

    return min(
        candidates,
        key=lambda x: _pct_distance(
            _f(
                x.get("price"),
                current_price,
            )
            or current_price,
            current_price,
        ),
    )


# ============================================================
# FULL ANALYSIS
# ============================================================

def analyze_liquidity(
    symbol: str,
    current_price: Optional[float] = None,
    candles_1h: Optional[
        List[Dict[str, Any]]
    ] = None,
) -> Dict[str, Any]:
    """
    Полный анализ ликвидности.

    Возвращает:
    {
        symbol,
        price,
        major_levels,
        primary_ssl,
        primary_bsl,
        next_bsl,
        next_ssl,
        oi,
        order_book,
        flow,
        timestamp
    }
    """

    symbol = _safe_symbol(
        symbol
    )

    if current_price is None:
        current_price = fetch_current_price(
            symbol
        )

    if current_price is None:
        return {
            "symbol": symbol,
            "price": None,
            "major_levels": [],
            "primary_ssl": None,
            "primary_bsl": None,
            "next_bsl": None,
            "next_ssl": None,
            "error": "Не удалось получить цену Binance Futures.",
            "timestamp": int(
                time.time()
            ),
        }

    levels = find_major_liquidity(
        symbol,
        current_price=current_price,
        candles_1h=candles_1h,
        use_futures_confirmation=True,
    )

    primary_ssl = get_primary_major_level(
        levels,
        current_price,
        "SSL",
    )

    primary_bsl = get_primary_major_level(
        levels,
        current_price,
        "BSL",
    )

    next_bsl = get_next_major_target(
        levels,
        current_price,
        "LONG",
    )

    next_ssl = get_next_major_target(
        levels,
        current_price,
        "SHORT",
    )

    current_oi = fetch_open_interest(
        symbol
    )

    order_book = fetch_order_book(
        symbol
    )

    oi_history = fetch_open_interest_history(
        symbol
    )

    taker_history = fetch_taker_volume(
        symbol
    )

    oi_confirmation = analyze_oi_confirmation(
        oi_history
    )

    flow_confirmation = analyze_flow_confirmation(
        taker_history
    )

    return {
        "symbol": symbol,
        "price": current_price,

        "major_levels": levels,

        "primary_ssl": primary_ssl,
        "primary_bsl": primary_bsl,

        "next_bsl": next_bsl,
        "next_ssl": next_ssl,

        "open_interest": current_oi,

        "order_book": order_book,

        "oi_confirmation": oi_confirmation,

        "flow_confirmation": flow_confirmation,

        "timestamp": int(
            time.time()
        ),
    }


# ============================================================
# COMPATIBILITY HELPERS
# ============================================================

def get_major_ssl(
    levels: List[Dict[str, Any]],
    current_price: float,
) -> Optional[Dict[str, Any]]:
    """
    Compatibility helper.
    """

    return get_primary_major_level(
        levels,
        current_price,
        "SSL",
    )


def get_major_bsl(
    levels: List[Dict[str, Any]],
    current_price: float,
) -> Optional[Dict[str, Any]]:
    """
    Compatibility helper.
    """

    return get_primary_major_level(
        levels,
        current_price,
        "BSL",
    )


def get_next_bsl(
    levels: List[Dict[str, Any]],
    current_price: float,
) -> Optional[Dict[str, Any]]:
    """
    Compatibility helper.
    """

    return get_next_major_target(
        levels,
        current_price,
        "LONG",
    )


def get_next_ssl(
    levels: List[Dict[str, Any]],
    current_price: float,
) -> Optional[Dict[str, Any]]:
    """
    Compatibility helper.
    """

    return get_next_major_target(
        levels,
        current_price,
        "SHORT",
    )


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    symbol = "SOLUSDT"

    print(
        "=" * 60
    )

    print(
        "TradeMind Liquidity Engine 1.0"
    )

    print(
        "=" * 60
    )

    result = analyze_liquidity(
        symbol
    )

    print(
        f"Symbol: {result.get('symbol')}"
    )

    print(
        f"Price: {result.get('price')}"
    )

    print(
        "\nMAJOR LIQUIDITY:"
    )

    for level in result.get(
        "major_levels",
        [],
    ):

        print(
            f"{level.get('type')} "
            f"${level.get('price'):.4f} "
            f"strength={level.get('strength')} "
            f"distance={level.get('distance_pct')}%"
        )

    print(
        "\nPRIMARY SSL:"
    )

    print(
        result.get(
            "primary_ssl"
        )
    )

    print(
        "\nPRIMARY BSL:"
    )

    print(
        result.get(
            "primary_bsl"
        )
    )

    print(
        "\nNEXT BSL:"
    )

    print(
        result.get(
            "next_bsl"
        )
    )

    print(
        "\nNEXT SSL:"
    )

    print(
        result.get(
            "next_ssl"
        )
    )

    print(
        "\nOI:"
    )

    print(
        result.get(
            "oi_confirmation"
        )
    )

    print(
        "\nFLOW:"
    )

    print(
        result.get(
            "flow_confirmation"
        )
    )

    print(
        "=" * 60
    )
