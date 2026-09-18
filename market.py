# ============================================================
# TradeMind 6.7
# market.py
#
# Binance Spot — источник рыночных данных
#
# Система:
#
# 1H → Main Structure
#    ↓
# Major Liquidity
#    ↓
# Sweep
#    ↓
# 15M Confirmation
#    ↓
# 5M ILM
#    ↓
# Entry
#
# ВАЖНО:
# - D1/W1 НЕ используются
# - 19 монет поддерживаются
# - LONG и SHORT анализируются независимо
# - LONG → SSL
# - SHORT → BSL
# - мелкие уровни возле цены не считаются MAJOR
# - Major Liquidity пересчитывается при каждом скане
# ============================================================

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import requests


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://api.binance.com/api/v3"

REQUEST_TIMEOUT = 10

# ------------------------------------------------------------
# 19 MONETS
# ------------------------------------------------------------

COINS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "BNB": "BNBUSDT",
    "XRP": "XRPUSDT",
    "DOGE": "DOGEUSDT",
    "ADA": "ADAUSDT",
    "AVAX": "AVAXUSDT",
    "LINK": "LINKUSDT",
    "HYPE": "HYPEUSDT",
    "SUI": "SUIUSDT",
    "TRX": "TRXUSDT",
    "DOT": "DOTUSDT",
    "LTC": "LTCUSDT",
    "BCH": "BCHUSDT",
    "NEAR": "NEARUSDT",
    "APT": "APTUSDT",
    "ARB": "ARBUSDT",
    "OP": "OPUSDT",
}


# ------------------------------------------------------------
# LOOKBACK
# ------------------------------------------------------------

LOOKBACK_1H = 180
LOOKBACK_15M = 200
LOOKBACK_5M = 200
LOOKBACK_1M = 200


# ------------------------------------------------------------
# SWING
# ------------------------------------------------------------

SWING_LEFT = 2
SWING_RIGHT = 2


# ------------------------------------------------------------
# LIQUIDITY
# ------------------------------------------------------------

CLUSTER_DISTANCE_PCT = 0.15

ZONE_WIDTH_PCT = 0.20

# Минимальная дистанция между двумя Major зонами
MIN_ZONE_GAP_PCT = 0.70

# ============================================================
# КРИТИЧЕСКИЙ ФИЛЬТР
# ============================================================

# Никакой Major Liquidity практически на текущей цене.
#
# Например:
#
# цена 106.10
#
# 106.12 = 0.019% → NO MAJOR
# 106.20 = 0.094% → NO MAJOR
# 106.40 = 0.282% → NO MAJOR
# 106.55 = 0.424% → может пройти
#
MIN_MAJOR_DISTANCE_PCT = 0.30


# ------------------------------------------------------------
# MAJOR STRENGTH
# ------------------------------------------------------------

MIN_MAJOR_STRENGTH = 58


# ------------------------------------------------------------
# LIMITS
# ------------------------------------------------------------

MAX_LEVELS_PER_SIDE = 4

MAX_TOTAL_LEVELS = 8


# ------------------------------------------------------------
# AGE
# ------------------------------------------------------------

MAX_LEVEL_AGE_1H = 120


# ------------------------------------------------------------
# LOCAL TOUCH
# ------------------------------------------------------------

LOCAL_TOUCH_DISTANCE_PCT = 0.20


# ------------------------------------------------------------
# SWEEP
# ------------------------------------------------------------

MIN_SWEEP_DEPTH_PCT = 0.08


# ============================================================
# HTTP SESSION
# ============================================================

_session = requests.Session()

_session.headers.update(
    {
        "User-Agent": "TradeMind/6.7",
        "Accept": "application/json",
    }
)


# ============================================================
# HTTP
# ============================================================

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
# SYMBOL NORMALIZATION
# ============================================================

def _normalize_symbol(
    symbol: Optional[str],
) -> str:

    if not symbol:
        return "SOLUSDT"

    symbol = str(symbol).upper().strip()

    # Разрешаем название монеты:
    # SOL → SOLUSDT
    if symbol in COINS:
        return COINS[symbol]

    # Уже полный Binance symbol
    if symbol.endswith("USDT"):
        return symbol

    return symbol + "USDT"


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(
    symbol: str = "SOLUSDT",
) -> float:

    symbol = _normalize_symbol(symbol)

    data = _get(
        "ticker/price",
        {
            "symbol": symbol,
        },
    )

    return float(data["price"])


# ============================================================
# KLINES
# ============================================================

def get_klines(
    interval: str,
    limit: int,
    symbol: str = "SOLUSDT",
) -> List[Dict[str, Any]]:

    symbol = _normalize_symbol(symbol)

    raw = _get(
        "klines",
        {
            "symbol": symbol,
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
# MARKET CANDLES
# ============================================================

def get_market_candles(
    symbol: str,
) -> Dict[str, List[Dict[str, Any]]]:

    symbol = _normalize_symbol(symbol)

    return {
        "1h": get_klines(
            "1h",
            LOOKBACK_1H,
            symbol,
        ),

        "15m": get_klines(
            "15m",
            LOOKBACK_15M,
            symbol,
        ),

        "5m": get_klines(
            "5m",
            LOOKBACK_5M,
            symbol,
        ),

        "1m": get_klines(
            "1m",
            LOOKBACK_1M,
            symbol,
        ),
    }


# ============================================================
# BASIC HELPERS
# ============================================================

def pct_distance(
    price_a: float,
    price_b: float,
) -> float:

    if price_b == 0:
        return 999.0

    return (
        abs(price_a - price_b)
        / abs(price_b)
        * 100.0
    )


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

    if not candles:
        return swings

    start = SWING_LEFT

    end = len(candles) - SWING_RIGHT

    for i in range(start, end):

        current = candles[i]

        high = current["high"]

        left = candles[
            i - SWING_LEFT:i
        ]

        right = candles[
            i + 1:
            i + 1 + SWING_RIGHT
        ]

        if (
            all(
                high >= x["high"]
                for x in left
            )
            and
            all(
                high >= x["high"]
                for x in right
            )
        ):

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

    if not candles:
        return swings

    start = SWING_LEFT

    end = len(candles) - SWING_RIGHT

    for i in range(start, end):

        current = candles[i]

        low = current["low"]

        left = candles[
            i - SWING_LEFT:i
        ]

        right = candles[
            i + 1:
            i + 1 + SWING_RIGHT
        ]

        if (
            all(
                low <= x["low"]
                for x in left
            )
            and
            all(
                low <= x["low"]
                for x in right
            )
        ):

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

    clusters: List[
        List[Dict[str, Any]]
    ] = []

    for level in levels:

        if not clusters:

            clusters.append(
                [level]
            )

            continue

        current_cluster = clusters[-1]

        average_price = (
            sum(
                x["price"]
                for x in current_cluster
            )
            /
            len(current_cluster)
        )

        distance = pct_distance(
            level["price"],
            average_price,
        )

        if distance <= CLUSTER_DISTANCE_PCT:

            current_cluster.append(
                level
            )

        else:

            clusters.append(
                [level]
            )

    result: List[
        Dict[str, Any]
    ] = []

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

        average_price = (
            sum(prices)
            /
            len(prices)
        )

        result.append(
            {
                "price": average_price,
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

    index = int(
        level["last_index"]
    )

    age = (
        len(candles)
        - 1
        - index
    )

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

    if not candles:
        return touches

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

        if (
            high_distance
            <= LOCAL_TOUCH_DISTANCE_PCT
        ):

            touches += 1

        elif (
            low_distance
            <= LOCAL_TOUCH_DISTANCE_PCT
        ):

            touches += 1

    return touches


# ============================================================
# SWEEP DETECTION
# ============================================================

def level_has_been_swept(
    level_price: float,
    level_type: str,
    candles: List[Dict[str, Any]],
) -> bool:

    if not candles:
        return False

    for candle in candles:

        high = candle["high"]

        low = candle["low"]

        close = candle["close"]

        # ----------------------------------------------------
        # BSL
        # ----------------------------------------------------

        if level_type == "BSL":

            if (
                high > level_price
                and
                close < level_price
            ):

                return True

        # ----------------------------------------------------
        # SSL
        # ----------------------------------------------------

        elif level_type == "SSL":

            if (
                low < level_price
                and
                close > level_price
            ):

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
        level.get(
            "touches",
            1,
        )
    )

    # --------------------------------------------------------
    # REPEATED TOUCHES
    # --------------------------------------------------------

    if touches >= 2:
        score += 8

    if touches >= 3:
        score += 6

    if touches >= 4:
        score += 5

    # --------------------------------------------------------
    # FRESHNESS
    # --------------------------------------------------------

    score += freshness_score(
        level,
        candles_1h,
    )

    # --------------------------------------------------------
    # 15M LOCAL CONFIRMATION
    # --------------------------------------------------------

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
# SELECT MAJOR ZONES
# ============================================================

def select_major_zones(
    levels: List[Dict[str, Any]],
    price: float,
    candles_1h: List[Dict[str, Any]],
    candles_15m: List[Dict[str, Any]],
    level_type: str,
) -> List[Dict[str, Any]]:

    candidates: List[
        Dict[str, Any]
    ] = []

    for level in levels:

        level_price = _safe_float(
            level.get("price")
        )

        if level_price <= 0:
            continue

        # ----------------------------------------------------
        # DIRECTION
        # ----------------------------------------------------

        if level_type == "BSL":

            if level_price <= price:
                continue

        elif level_type == "SSL":

            if level_price >= price:
                continue

        else:
            continue

        # ----------------------------------------------------
        # DISTANCE FROM CURRENT PRICE
        # ----------------------------------------------------

        distance = pct_distance(
            price,
            level_price,
        )

        if (
            distance
            < MIN_MAJOR_DISTANCE_PCT
        ):
            continue

        # ----------------------------------------------------
        # AGE
        # ----------------------------------------------------

        age = (
            len(candles_1h)
            - 1
            - int(
                level["last_index"]
            )
        )

        if age > MAX_LEVEL_AGE_1H:
            continue

        # ----------------------------------------------------
        # SWEPT
        # ----------------------------------------------------

        if level_has_been_swept(
            level_price,
            level_type,
            candles_1h,
        ):
            continue

        # ----------------------------------------------------
        # STRENGTH
        # ----------------------------------------------------

        strength = calculate_strength(
            level,
            candles_1h,
            candles_15m,
        )

        if (
            strength
            < MIN_MAJOR_STRENGTH
        ):
            continue

        # ----------------------------------------------------
        # ZONE
        # ----------------------------------------------------

        zone_low = (
            level_price
            * (
                1
                - ZONE_WIDTH_PCT / 100
            )
        )

        zone_high = (
            level_price
            * (
                1
                + ZONE_WIDTH_PCT / 100
            )
        )

        candidates.append(
            {
                "price": round(
                    level_price,
                    8,
                ),

                "zone_low": round(
                    zone_low,
                    8,
                ),

                "zone_high": round(
                    zone_high,
                    8,
                ),

                "type": level_type,

                "strength": round(
                    strength,
                    2,
                ),

                "touches": int(
                    level.get(
                        "touches",
                        1,
                    )
                ),

                "distance_pct": round(
                    distance,
                    4,
                ),

                "age_1h": age,

                "status": "FRESH",

                "swept": False,

                "taken": False,

                "used": False,

                "consumed": False,
            }
        )

    # --------------------------------------------------------
    # СИЛЬНЫЕ ПЕРВЫМИ
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x: (
            x["strength"],
            x["touches"],
            -x["distance_pct"],
        ),
        reverse=True,
    )

    selected: List[
        Dict[str, Any]
    ] = []

    # --------------------------------------------------------
    # УБИРАЕМ БЛИЗКИЕ ДУБЛИКАТЫ
    # --------------------------------------------------------

    for candidate in candidates:

        too_close = False

        for existing in selected:

            distance = pct_distance(
                candidate["price"],
                existing["price"],
            )

            if (
                distance
                < MIN_ZONE_GAP_PCT
            ):

                too_close = True

                break

        if too_close:
            continue

        selected.append(
            candidate
        )

        if (
            len(selected)
            >= MAX_LEVELS_PER_SIDE
        ):
            break

    # --------------------------------------------------------
    # UI → БЛИЖАЙШИЕ ПЕРВЫМИ
    # --------------------------------------------------------

    selected.sort(
        key=lambda x:
        x["distance_pct"]
    )

    return selected


# ============================================================
# MAJOR LIQUIDITY INTERNAL
# ============================================================

def _build_major_liquidity(
    candles_1h: List[Dict[str, Any]],
    candles_15m: List[Dict[str, Any]],
    price: float,
) -> Dict[str, List[Dict[str, Any]]]:

    if not candles_1h:
        return {
            "BSL": [],
            "SSL": [],
        }

    # --------------------------------------------------------
    # НЕ ИСПОЛЬЗУЕМ ФОРМИРУЮЩУЮСЯ 1H СВЕЧУ
    # --------------------------------------------------------

    confirmed_1h = candles_1h[:-1]

    if len(confirmed_1h) < 10:
        return {
            "BSL": [],
            "SSL": [],
        }

    #