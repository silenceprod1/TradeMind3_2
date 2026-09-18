"""
TradeMind market.py 7.8

CORE:
    MAJOR LIQUIDITY = 1H ONLY

    1H direction
        ↓
    current 1H structural leg
        ↓
    major 1H liquidity
        ↓
    sweep
        ↓
    15M confirmation
        ↓
    5M ILM
        ↓
    entry

IMPORTANT:
- D1 is context only.
- 15M is local context only.
- 5M is trigger only.
- Round numbers are NOT Major Liquidity.
- Major Liquidity comes ONLY from confirmed 1H swing highs/lows.
- BSL must be above current price.
- SSL must be below current price.
- Do not hide fresh sweeps before strategy.py sees them.
- Do not use hard age expiration.
- Do not use artificial minimum distance to declare a level major.
"""

from __future__ import annotations

import json
import math
import time
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# VERSION
# ============================================================

MARKET_VERSION = "7.8"


# ============================================================
# BINANCE
# ============================================================

BINANCE_BASE_URL = "https://api.binance.com/api/v3"
REQUEST_TIMEOUT = 10


# ============================================================
# COINS
# ============================================================

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


# ============================================================
# LOOKBACK
# ============================================================

LOOKBACK_D1 = 60
LOOKBACK_1H = 180
LOOKBACK_15M = 200
LOOKBACK_5M = 200
LOOKBACK_1M = 30


# ============================================================
# CACHE
# ============================================================

CACHE_TTL = {
    "1d": 180,
    "1h": 15,
    "15m": 8,
    "5m": 5,
    "1m": 2,
}

_KLINE_CACHE: Dict[str, Dict[str, Any]] = {}
_PRICE_CACHE: Dict[str, Dict[str, Any]] = {}

_SESSION: Optional[requests.Session] = None


# ============================================================
# LIQUIDITY SETTINGS
# ============================================================

# Swing points closer than this are treated as one liquidity cluster.
CLUSTER_DISTANCE_PCT = 0.15

# Used only for touch detection / zone description.
ZONE_WIDTH_PCT = 0.20

# Maximum Major levels returned.
MAX_MAJOR_PER_SIDE = 8
MAX_MAJOR_TOTAL = 16

# Local 15M context.
MAX_15M_PER_SIDE = 4

# Duplicate protection.
DUPLICATE_DISTANCE_PCT = 0.05


# ============================================================
# FRESHNESS
# ============================================================

FRESH_AGE_1H = 3
FRESH_AGE_2 = 10
FRESH_AGE_3 = 30
FRESH_AGE_4 = 60


# ============================================================
# CURRENT LEG
# ============================================================

"""
The current structural leg is NOT used as a hard distance filter.

It is used to rank liquidity.

Example LONG:

        current price
             ↑
             │
        current leg
             │
        last meaningful
        1H swing low
             │
             ↓

A very old level 10% away should not automatically
beat a structurally relevant level closer to the
current movement.
"""

CURRENT_LEG_LOOKBACK = 60

CURRENT_LEG_RECENT_SWINGS = 12


# ============================================================
# SWEEP
# ============================================================

MAX_SWEEP_LOOKBACK = 8

SWEEP_MIN_DEPTH_PCT = 0.15
SWEEP_MIN_DEPTH_DETECT_PCT = 0.08

RECENT_SWEEP_LOOKBACK_1H = 30
RECENT_SWEEP_LOOKBACK_15M = 60


# ============================================================
# FVG
# ============================================================

FVG_5M_MIN_GAP_PCT = 0.05
FVG_15M_MIN_GAP_PCT = 0.10

MAX_FVG_ZONES = 5
FVG_LOOKBACK = 100


# ============================================================
# SESSION
# ============================================================

def _get_session() -> requests.Session:

    global _SESSION

    if _SESSION is None:

        _SESSION = requests.Session()

        _SESSION.headers.update(
            {
                "User-Agent": "TradeMind/7.8",
                "Accept": "application/json",
            }
        )

    return _SESSION


# ============================================================
# GENERIC HELPERS
# ============================================================

def _f(
    value: Any,
) -> Optional[float]:

    try:

        if value is None:
            return None

        value = float(value)

        if not math.isfinite(value):
            return None

        return value

    except (
        TypeError,
        ValueError,
    ):
        return None


def _safe_int(
    value: Any,
) -> Optional[int]:

    try:

        if value is None:
            return None

        return int(
            float(value)
        )

    except (
        TypeError,
        ValueError,
    ):
        return None


def _pct_distance(
    a: Any,
    b: Any,
) -> Optional[float]:

    a = _f(a)
    b = _f(b)

    if (
        a is None
        or b is None
        or b == 0
    ):
        return None

    return (
        abs(a - b)
        / abs(b)
        * 100.0
    )


def _normalize_symbol(
    symbol: str,
) -> str:

    if not symbol:
        raise ValueError(
            "Пустой symbol."
        )

    symbol = str(
        symbol
    ).upper().strip()

    if symbol in COINS:
        return COINS[
            symbol
        ]

    if symbol.endswith(
        "USDT"
    ):
        return symbol

    return (
        f"{symbol}USDT"
    )


def _coin_name(
    symbol: str,
) -> str:

    symbol = str(
        symbol
    ).upper().strip()

    for name, pair in COINS.items():

        if symbol == name:
            return name

        if symbol == pair:
            return name

    if symbol.endswith(
        "USDT"
    ):
        return symbol[:-4]

    return symbol


# ============================================================
# CANDLE HELPERS
# ============================================================

def _v(
    candle: Any,
    key: str,
    default: Any = None,
) -> Any:

    if not isinstance(
        candle,
        dict,
    ):
        return default

    value = candle.get(
        key
    )

    if value is None:

        aliases = {
            "open": "o",
            "high": "h",
            "low": "l",
            "close": "c",
            "volume": "v",
            "open_time": "time",
            "close_time": "time_close",
        }

        alias = aliases.get(
            key
        )

        if alias:
            value = candle.get(
                alias
            )

    if value is None:
        return default

    return value


def _o(
    candle: Any,
) -> Optional[float]:

    return _f(
        _v(
            candle,
            "open",
        )
    )


def _h(
    candle: Any,
) -> Optional[float]:

    return _f(
        _v(
            candle,
            "high",
        )
    )


def _l(
    candle: Any,
) -> Optional[float]:

    return _f(
        _v(
            candle,
            "low",
        )
    )


def _c(
    candle: Any,
) -> Optional[float]:

    return _f(
        _v(
            candle,
            "close",
        )
    )


def _vol(
    candle: Any,
) -> Optional[float]:

    return _f(
        _v(
            candle,
            "volume",
        )
    )


def _t(
    candle: Any,
) -> Optional[int]:

    return _safe_int(
        _v(
            candle,
            "open_time",
        )
    )


def _ct(
    candle: Any,
) -> Optional[int]:

    return _safe_int(
        _v(
            candle,
            "close_time",
        )
    )


def _body(
    candle: Any,
) -> float:

    o = _o(candle)
    c = _c(candle)

    if (
        o is None
        or c is None
    ):
        return 0.0

    return abs(
        c - o
    )


def _range(
    candle: Any,
) -> float:

    h = _h(candle)
    l = _l(candle)

    if (
        h is None
        or l is None
    ):
        return 0.0

    return max(
        0.0,
        h - l,
    )


def _body_ratio(
    candle: Any,
) -> float:

    r = _range(
        candle
    )

    if r <= 0:
        return 0.0

    return (
        _body(candle)
        / r
    )


def _bull(
    candle: Any,
) -> bool:

    o = _o(candle)
    c = _c(candle)

    return (
        o is not None
        and c is not None
        and c > o
    )


def _bear(
    candle: Any,
) -> bool:

    o = _o(candle)
    c = _c(candle)

    return (
        o is not None
        and c is not None
        and c < o
    )


# ============================================================
# BINANCE REQUEST
# ============================================================

def _get(
    endpoint: str,
    params: Optional[
        Dict[str, Any]
    ] = None,
) -> Any:

    url = (
        f"{BINANCE_BASE_URL}/"
        f"{endpoint}"
    )

    response = _get_session().get(
        url,
        params=params or {},
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    data = response.json()

    if (
        isinstance(
            data,
            dict,
        )
        and data.get("code")
        is not None
    ):

        raise RuntimeError(
            f"Binance API error "
            f"{data.get('code')}: "
            f"{data.get('msg', 'unknown error')}"
        )

    return data


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(
    symbol: str,
) -> float:

    pair = _normalize_symbol(
        symbol
    )

    now = time.time()

    cached = _PRICE_CACHE.get(
        pair
    )

    if cached:

        if (
            now
            - cached["time"]
            < CACHE_TTL["1m"]
        ):

            return float(
                cached["price"]
            )

    data = _get(
        "ticker/price",
        {
            "symbol": pair,
        },
    )

    price = _f(
        data.get(
            "price"
        )
    )

    if price is None:

        raise RuntimeError(
            f"Binance не вернул "
            f"цену для {pair}."
        )

    _PRICE_CACHE[
        pair
    ] = {
        "time": now,
        "price": price,
    }

    return price


# ============================================================
# KLINES
# ============================================================

def _fetch_klines(
    symbol: str,
    interval: str,
    limit: int,
) -> List[Dict[str, Any]]:

    pair = _normalize_symbol(
        symbol
    )

    data = _get(
        "klines",
        {
            "symbol": pair,
            "interval": interval,
            "limit": limit,
        },
    )

    candles = []

    for row in data:

        if (
            not isinstance(
                row,
                list,
            )
            or len(row) < 11
        ):
            continue

        candles.append(
            {
                "open_time": _safe_int(
                    row[0]
                ),
                "open": _f(
                    row[1]
                ),
                "high": _f(
                    row[2]
                ),
                "low": _f(
                    row[3]
                ),
                "close": _f(
                    row[4]
                ),
                "volume": _f(
                    row[5]
                ),
                "close_time": _safe_int(
                    row[6]
                ),
                "quote_volume": _f(
                    row[7]
                ),
                "trades": _safe_int(
                    row[8]
                ),
                "taker_buy_base": _f(
                    row[9]
                ),
                "taker_buy_quote": _f(
                    row[10]
                ),
            }
        )

    return candles


def get_klines(
    symbol: str,
    interval: str,
    limit: int = 100,
) -> List[Dict[str, Any]]:

    pair = _normalize_symbol(
        symbol
    )

    key = (
        f"{pair}:"
        f"{interval}:"
        f"{limit}"
    )

    now = time.time()

    cached = _KLINE_CACHE.get(
        key
    )

    if cached:

        ttl = CACHE_TTL.get(
            interval,
            5,
        )

        if (
            now
            - cached["time"]
            < ttl
        ):

            return list(
                cached["candles"]
            )

    candles = _fetch_klines(
        pair,
        interval,
        limit,
    )

    _KLINE_CACHE[
        key
    ] = {
        "time": now,
        "candles": candles,
    }

    return list(
        candles
    )


# ============================================================
# CONFIRMED CANDLES
# ============================================================

def get_confirmed_candles(
    candles: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    if not candles:
        return []

    now_ms = int(
        time.time()
        * 1000
    )

    result = []

    for candle in candles:

        close_time = _ct(
            candle
        )

        if close_time is None:

            result.append(
                candle
            )

            continue

        if (
            close_time
            <= now_ms
        ):

            result.append(
                candle
            )

    return result


# ============================================================
# SWINGS
# ============================================================

def _find_swings(
    candles: List[Dict[str, Any]],
    left: int = 2,
    right: int = 1,
) -> Tuple[
    List[Tuple[int, float]],
    List[Tuple[int, float]],
]:

    highs = []
    lows = []

    if not candles:
        return highs, lows

    if len(candles) < (
        left
        + right
        + 1
    ):
        return highs, lows

    for i in range(
        left,
        len(candles) - right,
    ):

        high = _h(
            candles[i]
        )

        low = _l(
            candles[i]
        )

        if (
            high is None
            or low is None
        ):
            continue

        left_highs = [
            _h(
                candles[j]
            )
            for j in range(
                i - left,
                i,
            )
        ]

        right_highs = [
            _h(
                candles[j]
            )
            for j in range(
                i + 1,
                i + right + 1,
            )
        ]

        left_lows = [
            _l(
                candles[j]
            )
            for j in range(
                i - left,
                i,
            )
        ]

        right_lows = [
            _l(
                candles[j]
            )
            for j in range(
                i + 1,
                i + right + 1,
            )
        ]

        if all(
            x is not None
            for x in (
                left_highs
                + right_highs
            )
        ):

            if (
                high
                > max(
                    left_highs
                )
                and high
                >= max(
                    right_highs
                )
            ):

                highs.append(
                    (
                        i,
                        high,
                    )
                )

        if all(
            x is not None
            for x in (
                left_lows
                + right_lows
            )
        ):

            if (
                low
                < min(
                    left_lows
                )
                and low
                <= min(
                    right_lows
                )
            ):

                lows.append(
                    (
                        i,
                        low,
                    )
                )

    return highs, lows


def find_swing_highs(
    candles: List[Dict[str, Any]],
    left: int = 2,
    right: int = 1,
) -> List[Tuple[int, float]]:

    highs, _ = _find_swings(
        candles,
        left,
        right,
    )

    return highs


def find_swing_lows(
    candles: List[Dict[str, Any]],
    left: int = 2,
    right: int = 1,
) -> List[Tuple[int, float]]:

    _, lows = _find_swings(
        candles,
        left,
        right,
    )

    return lows


# ============================================================
# PUBLIC SWING ALIASES
# ============================================================

def get_1h_swings(
    candles: List[Dict[str, Any]],
) -> Dict[str, Any]:

    highs, lows = _find_swings(
        get_confirmed_candles(
            candles
        ),
        2,
        1,
    )

    return {
        "highs": highs,
        "lows": lows,
    }


def get_15m_swings(
    candles: List[Dict[str, Any]],
) -> Dict[str, Any]:

    highs, lows = _find_swings(
        get_confirmed_candles(
            candles
        ),
        2,
        1,
    )

    return {
        "highs": highs,
        "lows": lows,
    }


# ============================================================
# CLUSTER
# ============================================================

def _cluster_swing_points(
    points: List[Tuple[int, float]],
) -> List[Dict[str, Any]]:

    if not points:
        return []

    points = sorted(
        points,
        key=lambda x: x[1],
    )

    clusters = []

    for point in points:

        if not clusters:

            clusters.append(
                [point]
            )

            continue

        previous_price = (
            clusters[-1][-1][1]
        )

        distance = (
            abs(
                point[1]
                - previous_price
            )
            / previous_price
            * 100.0
        )

        if (
            distance
            <= CLUSTER_DISTANCE_PCT
        ):

            clusters[-1].append(
                point
            )

        else:

            clusters.append(
                [point]
            )

    result = []

    for cluster in clusters:

        prices = [
            x[1]
            for x in cluster
        ]

        indices = [
            x[0]
            for x in cluster
        ]

        average_price = (
            sum(prices)
            / len(prices)
        )

        result.append(
            {
                "price": average_price,
                "touches": len(
                    cluster
                ),
                "cluster_size": len(
                    cluster
                ),
                "first_index": min(
                    indices
                ),
                "last_index": max(
                    indices
                ),
                "members": prices,
            }
        )

    return result


def cluster_levels(
    prices: List[float],
    distance_pct: float = CLUSTER_DISTANCE_PCT,
) -> List[Dict[str, Any]]:

    valid = [
        float(x)
        for x in prices
        if _f(x) is not None
        and float(x) > 0
    ]

    if not valid:
        return []

    valid.sort()

    clusters = []

    for price in valid:

        if not clusters:

            clusters.append(
                [price]
            )

            continue

        previous = (
            clusters[-1][-1]
        )

        distance = (
            abs(
                price
                - previous
            )
            / previous
            * 100.0
        )

        if (
            distance
            <= distance_pct
        ):

            clusters[-1].append(
                price
            )

        else:

            clusters.append(
                [price]
            )

    result = []

    for cluster in clusters:

        result.append(
            {
                "price": (
                    sum(cluster)
                    / len(cluster)
                ),
                "touches": len(
                    cluster
                ),
                "members": cluster,
            }
        )

    return result


# ============================================================
# AGE
# ============================================================

def get_level_age(
    level: Dict[str, Any],
    current_index: Optional[int] = None,
) -> int:

    if not isinstance(
        level,
        dict,
    ):
        return 999999

    age = level.get(
        "age"
    )

    if age is not None:

        try:

            return max(
                0,
                int(age),
            )

        except (
            TypeError,
            ValueError,
        ):
            pass

    index = level.get(
        "index"
    )

    if (
        index is None
        or current_index is None
    ):
        return 999999

    try:

        return max(
            0,
            int(current_index)
            - int(index),
        )

    except (
        TypeError,
        ValueError,
    ):

        return 999999


# ============================================================
# FRESHNESS
# ============================================================

def freshness_score(
    age: int,
) -> float:

    age = max(
        0,
        int(age),
    )

    if age <= FRESH_AGE_1H:
        return 1.0

    if age <= FRESH_AGE_2:
        return 0.85

    if age <= FRESH_AGE_3:
        return 0.65

    if age <= FRESH_AGE_4:
        return 0.40

    return 0.20


def freshness_label(
    age: int,
) -> str:

    age = max(
        0,
        int(age),
    )

    if age <= FRESH_AGE_1H:
        return "FRESH"

    if age <= FRESH_AGE_2:
        return "RECENT"

    if age <= FRESH_AGE_3:
        return "NORMAL"

    return "OLD"


# ============================================================
# TOUCHES
# ============================================================

def count_local_touches(
    candles: List[Dict[str, Any]],
    price: float,
    tolerance_pct: float = ZONE_WIDTH_PCT,
) -> int:

    p = _f(
        price
    )

    if p is None or p <= 0:
        return 0

    tolerance = (
        p
        * tolerance_pct
        / 100.0
    )

    touches = 0

    for candle in candles or []:

        high = _h(
            candle
        )

        low = _l(
            candle
        )

        if (
            high is None
            or low is None
        ):
            continue

        if (
            low - tolerance
            <= p
            <= high + tolerance
        ):

            touches += 1

    return touches


# ============================================================
# STRENGTH
# ============================================================

def calculate_strength(
    level: Dict[str, Any],
) -> float:

    if not isinstance(
        level,
        dict,
    ):
        return 0.0

    touches = (
        _f(
            level.get(
                "touches"
            )
        )
        or 1.0
    )

    cluster_size = (
        _f(
            level.get(
                "cluster_size"
            )
        )
        or touches
        or 1.0
    )

    # Repeated touches matter more than
    # a single isolated swing.
    strength = (
        min(
            touches,
            8.0,
        )
        * 10.0
        +
        min(
            cluster_size,
            8.0,
        )
        * 5.0
    )

    return round(
        strength,
        3,
    )


# ============================================================
# CURRENT STRUCTURAL LEG
# ============================================================

def _get_current_structural_leg(
    candles_1h: List[Dict[str, Any]],
    current_price: float,
) -> Dict[str, Any]:

    """
    Determines the most recent meaningful
    structural swing pair.

    This does NOT delete old levels.

    It is only used to rank them.

    Returns:

        {
            "high": ...,
            "low": ...,
            "high_index": ...,
            "low_index": ...,
            "leg_direction": ...
        }
    """

    candles = get_confirmed_candles(
        candles_1h
    )

    if len(candles) < 10:

        return {
            "high": None,
            "low": None,
            "high_index": None,
            "low_index": None,
            "leg_direction": "NEUTRAL",
        }

    swings_high, swings_low = (
        _find_swings(
            candles,
            left=2,
            right=1,
        )
    )

    swings_high = (
        swings_high[
            -CURRENT_LEG_RECENT_SWINGS:
        ]
    )

    swings_low = (
        swings_low[
            -CURRENT_LEG_RECENT_SWINGS:
        ]
    )

    current = _f(
        current_price
    )

    if current is None:

        return {
            "high": None,
            "low": None,
            "high_index": None,
            "low_index": None,
            "leg_direction": "NEUTRAL",
        }

    # Last swing high below/above current.
    valid_highs = [
        (i, p)
        for i, p in swings_high
    ]

    valid_lows = [
        (i, p)
        for i, p in swings_low
    ]

    last_high = (
        max(
            valid_highs,
            key=lambda x: x[0],
        )
        if valid_highs
        else None
    )

    last_low = (
        max(
            valid_lows,
            key=lambda x: x[0],
        )
        if valid_lows
        else None
    )

    leg_direction = "NEUTRAL"

    if (
        last_high
        and last_low
    ):

        if (
            last_low[0]
            < last_high[0]
        ):

            leg_direction = "LONG"

        elif (
            last_high[0]
            < last_low[0]
        ):

            leg_direction = "SHORT"

    return {
        "high": (
            last_high[1]
            if last_high
            else None
        ),
        "low": (
            last_low[1]
            if last_low
            else None
        ),
        "high_index": (
            last_high[0]
            if last_high
            else None
        ),
        "low_index": (
            last_low[0]
            if last_low
            else None
        ),
        "leg_direction": leg_direction,
    }


# ============================================================
# PRIORITY
# ============================================================

def calculate_priority(
    level: Dict[str, Any],
    current_price: float,
) -> float:

    if not isinstance(
        level,
        dict,
    ):
        return 0.0

    price = _f(
        level.get(
            "price"
        )
    )

    current = _f(
        current_price
    )

    if (
        price is None
        or current is None
        or current <= 0
    ):
        return 0.0

    age = get_level_age(
        level
    )

    fresh = freshness_score(
        age
    )

    distance = (
        _pct_distance(
            price,
            current,
        )
        or 100.0
    )

    distance_score = (
        1.0
        / (
            1.0
            + distance
        )
    )

    touches = (
        _f(
            level.get(
                "touches"
            )
        )
        or 1.0
    )

    cluster_score = min(
        touches / 5.0,
        1.0,
    )

    strength = calculate_strength(
        level
    )

    strength_score = min(
        strength / 100.0,
        1.0,
    )

    # Current structural relevance.
    structural_bonus = _f(
        level.get(
            "structural_bonus"
        )
    ) or 0.0

    priority = (
        fresh * 0.35
        + distance_score * 0.15
        + cluster_score * 0.15
        + strength_score * 0.15
        + structural_bonus * 0.20
    )

    return round(
        priority,
        6,
    )


# ============================================================
# DUPLICATE FILTER
# ============================================================

def _remove_duplicates(
    levels: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    result = []

    for level in levels:

        price = _f(
            level.get(
                "price"
            )
        )

        if price is None:
            continue

        duplicate = False

        for existing in result:

            existing_price = _f(
                existing.get(
                    "price"
                )
            )

            if existing_price is None:
                continue

            distance = _pct_distance(
                price,
                existing_price,
            )

            if (
                distance is not None
                and distance
                <= DUPLICATE_DISTANCE_PCT
            ):

                duplicate = True
                break

        if not duplicate:

            result.append(
                level
            )

    return result


# ============================================================
# STRUCTURAL RELEVANCE
# ============================================================

def _structural_relevance(
    level_price: float,
    level_index: int,
    leg: Dict[str, Any],
    direction: str,
    current_price: float,
) -> float:

    """
    Scores how relevant the level is to
    the current 1H structural leg.

    This is a ranking bonus, NOT a hard filter.
    """

    score = 0.0

    high = _f(
        leg.get(
            "high"
        )
    )

    low = _f(
        leg.get(
            "low"
        )
    )

    high_index = leg.get(
        "high_index"
    )

    low_index = leg.get(
        "low_index"
    )

    current = _f(
        current_price
    )

    if current is None:
        return 0.0

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        # SSL is the important manipulation side.
        if level_price < current:

            # Recent structural low.
            if (
                low is not None
                and low_index is not None
            ):

                distance_from_low = (
                    _pct_distance(
                        level_price,
                        low,
                    )
                    or 100.0
                )

                if (
                    distance_from_low
                    <= 0.30
                ):

                    score += 0.65

                elif (
                    distance_from_low
                    <= 0.75
                ):

                    score += 0.40

            # If the level is the actual
            # current structural low.
            if (
                low_index is not None
                and level_index
                == low_index
            ):

                score += 0.35

    # ========================================================
    # SHORT
    # ========================================================

    elif direction == "SHORT":

        # BSL is the important manipulation side.
        if level_price > current:

            if (
                high is not None
                and high_index is not None
            ):

                distance_from_high = (
                    _pct_distance(
                        level_price,
                        high,
                    )
                    or 100.0
                )

                if (
                    distance_from_high
                    <= 0.30
                ):

                    score += 0.65

                elif (
                    distance_from_high
                    <= 0.75
                ):

                    score += 0.40

            if (
                high_index is not None
                and level_index
                == high_index
            ):

                score += 0.35

    return min(
        score,
        1.0,
    )


# ============================================================
# SELECT MAJOR SIDE
# ============================================================

def _select_major_side(
    levels: List[Dict[str, Any]],
    current_price: float,
    side: str,
    max_count: int,
    direction: str,
    leg: Dict[str, Any],
) -> List[Dict[str, Any]]:

    """
    CRITICAL.

    BSL:
        ONLY above current price.

    SSL:
        ONLY below current price.

    No arbitrary distance gate.

    No age deletion.

    No strength deletion.

    The current structural leg is used
    to rank relevance.
    """

    current = _f(
        current_price
    )

    if current is None:
        return []

    side = str(
        side
    ).upper()

    candidates = []

    for raw in levels or []:

        if not isinstance(
            raw,
            dict,
        ):
            continue

        price = _f(
            raw.get(
                "price"
            )
        )

        if price is None:
            continue

        # ====================================================
        # GEOMETRY
        # ====================================================

        if side == "BSL":

            if price <= current:
                continue

        elif side == "SSL":

            if price >= current:
                continue

        else:
            continue

        item = dict(
            raw
        )

        item["side"] = side

        item["type"] = (
            "BSL_1H"
            if side == "BSL"
            else "SSL_1H"
        )

        item["source"] = "1H"

        age = get_level_age(
            item
        )

        item["age"] = age

        item["freshness"] = (
            freshness_score(
                age
            )
        )

        item["freshness_label"] = (
            freshness_label(
                age
            )
        )

        item["strength"] = (
            calculate_strength(
                item
            )
        )

        # ====================================================
        # STRUCTURAL RELEVANCE
        # ====================================================

        level_index = _safe_int(
            item.get(
                "index"
            )
        )

        if level_index is None:

            level_index = (
                item.get(
                    "last_index"
                )
            )

        if level_index is None:

            level_index = 0

        structural_bonus = (
            _structural_relevance(
                price,
                int(
                    level_index
                ),
                leg,
                direction,
                current,
            )
        )

        item[
            "structural_bonus"
        ] = structural_bonus

        # ====================================================
        # IMPORTANT:
        # DO NOT FILTER SWEPT LEVELS HERE.
        # ====================================================

        item[
            "recently_swept"
        ] = False

        item[
            "recent_sweep"
        ] = False

        item[
            "swept"
        ] = False

        item[
            "priority"
        ] = calculate_priority(
            item,
            current,
        )

        candidates.append(
            item
        )

    # ========================================================
    # SORT
    # ========================================================

    candidates.sort(
        key=lambda x: (
            x.get(
                "priority",
                0.0,
            ),
            x.get(
                "structural_bonus",
                0.0,
            ),
            x.get(
                "freshness",
                0.0,
            ),
            x.get(
                "strength",
                0.0,
            ),
        ),
        reverse=True,
    )

    candidates = _remove_duplicates(
        candidates
    )

    return candidates[
        :max_count
    ]


# ============================================================
# BUILD 1H MAJOR
# ============================================================

def _build_1h_major_liquidity(
    candles_1h: List[Dict[str, Any]],
    current_price: float,
    direction: Optional[str] = None,
) -> Dict[str, Any]:

    confirmed = get_confirmed_candles(
        candles_1h
    )

    if len(confirmed) < 10:

        return {
            "BSL": [],
            "SSL": [],
            "source": "1H_ONLY",
            "structural_leg": {},
        }

    # ========================================================
    # CURRENT LEG
    # ========================================================

    leg = _get_current_structural_leg(
        confirmed,
        current_price,
    )

    if direction is None:
        direction = "NEUTRAL"

    direction = str(
        direction
    ).upper()

    # ========================================================
    # SWINGS
    # ========================================================

    swing_highs, swing_lows = (
        _find_swings(
            confirmed,
            left=2,
            right=1,
        )
    )

    # ========================================================
    # RAW BSL
    # ========================================================

    raw_bsl = []

    for index, price in swing_highs:

        candle = confirmed[
            index
        ]

        raw_bsl.append(
            {
                "price": price,
                "side": "BSL",
                "type": "BSL_1H",
                "source": "1H",
                "index": index,
                "open_time": _t(
                    candle
                ),
                "touches": 1,
                "cluster_size": 1,
            }
        )

    # ========================================================
    # RAW SSL
    # ========================================================

    raw_ssl = []

    for index, price in swing_lows:

        candle = confirmed[
            index
        ]

        raw_ssl.append(
            {
                "price": price,
                "side": "SSL",
                "type": "SSL_1H",
                "source": "1H",
                "index": index,
                "open_time": _t(
                    candle
                ),
                "touches": 1,
                "cluster_size": 1,
            }
        )

    # ========================================================
    # CLUSTER
    # ========================================================

    def cluster_points(
        source: List[
            Dict[str, Any]
        ],
    ) -> List[
        Dict[str, Any]
    ]:

        if not source:
            return []

        source = sorted(
            source,
            key=lambda x: x[
                "price"
            ],
        )

        clusters = []

        for item in source:

            if not clusters:

                clusters.append(
                    [item]
                )

                continue

            previous = (
                clusters[-1][-1][
                    "price"
                ]
            )

            distance = (
                abs(
                    item["price"]
                    - previous
                )
                / previous
                * 100.0
            )

            if (
                distance
                <= CLUSTER_DISTANCE_PCT
            ):

                clusters[
                    -1
                ].append(
                    item
                )

            else:

                clusters.append(
                    [item]
                )

        result = []

        for cluster in clusters:

            prices = [
                x["price"]
                for x in cluster
            ]

            indices = [
                x["index"]
                for x in cluster
            ]

            open_times = [
                x.get(
                    "open_time"
                )
                for x in cluster
            ]

            average_price = (
                sum(prices)
                / len(prices)
            )

            newest_index = max(
                indices
            )

            result.append(
                {
                    "price": average_price,
                    "side": cluster[
                        0
                    ][
                        "side"
                    ],
                    "type": cluster[
                        0
                    ][
                        "type"
                    ],
                    "source": "1H",
                    "index": newest_index,
                    "open_time": max(
                        [
                            x
                            for x in open_times
                            if x is not None
                        ],
                        default=None,
                    ),
                    "touches": len(
                        cluster
                    ),
                    "cluster_size": len(
                        cluster
                    ),
                    "members": prices,
                }
            )

        return result

    clustered_bsl = cluster_points(
        raw_bsl
    )

    clustered_ssl = cluster_points(
        raw_ssl
    )

    # ========================================================
    # ACTIVE MAJOR
    # ========================================================

    bsl = _select_major_side(
        clustered_bsl,
        current_price,
        "BSL",
        MAX_MAJOR_PER_SIDE,
        direction,
        leg,
    )

    ssl = _select_major_side(
        clustered_ssl,
        current_price,
        "SSL",
        MAX_MAJOR_PER_SIDE,
        direction,
        leg,
    )

    return {
        "BSL": bsl,
        "SSL": ssl,
        "source": "1H_ONLY",
        "structural_leg": leg,
    }


# ============================================================
# 15M CONTEXT
# ============================================================

def _build_15m_context(
    candles_15m: List[Dict[str, Any]],
    current_price: float,
) -> Dict[str, List[Dict[str, Any]]]:

    confirmed = get_confirmed_candles(
        candles_15m
    )

    if len(confirmed) < 6:

        return {
            "BSL": [],
            "SSL": [],
        }

    highs, lows = _find_swings(
        confirmed,
        left=2,
        right=1,
    )

    bsl = []

    for index, price in highs:

        bsl.append(
            {
                "price": price,
                "side": "BSL",
                "type": "BSL_15M",
                "source": "15M",
                "index": index,
                "open_time": _t(
                    confirmed[index]
                ),
                "touches": 1,
            }
        )

    ssl = []

    for index, price in lows:

        ssl.append(
            {
                "price": price,
                "side": "SSL",
                "type": "SSL_15M",
                "source": "15M",
                "index": index,
                "open_time": _t(
                    confirmed[index]
                ),
                "touches": 1,
            }
        )

    # 15M is context only.
    bsl = _select_major_side(
        bsl,
        current_price,
        "BSL",
        MAX_15M_PER_SIDE,
        "NEUTRAL",
        {},
    )

    ssl = _select_major_side(
        ssl,
        current_price,
        "SSL",
        MAX_15M_PER_SIDE,
        "NEUTRAL",
        {},
    )

    for level in bsl:

        level["type"] = (
            "BSL_15M"
        )

        level["source"] = (
            "15M"
        )

    for level in ssl:

        level["type"] = (
            "SSL_15M"
        )

        level["source"] = (
            "15M"
        )

    return {
        "BSL": bsl,
        "SSL": ssl,
    }


# ============================================================
# FIND MAJOR LIQUIDITY
# ============================================================

def find_major_liquidity(
    candles_1h: List[Dict[str, Any]],
    current_price: float,
    max_levels: int = 12,
    candles_15m: Optional[
        List[Dict[str, Any]]
    ] = None,
    candles_5m: Optional[
        List[Dict[str, Any]]
    ] = None,
    candles_1m: Optional[
        List[Dict[str, Any]]
    ] = None,
) -> List[Dict[str, Any]]:

    """
    Compatibility signature for bot.py.

    MAJOR = 1H ONLY.

    Direction is deliberately not inferred here.
    Both active sides are returned.

    IMPORTANT:
        BSL > current price
        SSL < current price
    """

    price = _f(
        current_price
    )

    if price is None:
        return []

    major = _build_1h_major_liquidity(
        candles_1h or [],
        price,
        direction="NEUTRAL",
    )

    bsl = major.get(
        "BSL",
        [],
    )

    ssl = major.get(
        "SSL",
        [],
    )

    total_limit = max(
        2,
        int(
            max_levels
        ),
    )

    # Keep both sides available.
    per_side = max(
        1,
        total_limit // 2,
    )

    bsl = bsl[
        :per_side
    ]

    ssl = ssl[
        :per_side
    ]

    combined = (
        bsl
        + ssl
    )

    # Priority is for display.
    # Target selection in strategy.py
    # should use distance to price.
    combined.sort(
        key=lambda x: (
            x.get(
                "priority",
                0.0,
            ),
            x.get(
                "freshness",
                0.0,
            ),
            x.get(
                "strength",
                0.0,
            ),
        ),
        reverse=True,
    )

    return combined[
        :total_limit
    ]


# ============================================================
# TARGET LIQUIDITY
# ============================================================

def get_target_liquidity(
    major_levels: Any,
    direction: str,
    current_price: float,
    exclude_level: Optional[
        float
    ] = None,
) -> Optional[
    Dict[str, Any]
]:

    price = _f(
        current_price
    )

    if price is None:
        return None

    direction = str(
        direction
    ).upper()

    expected_side = (
        "BSL"
        if direction == "LONG"
        else "SSL"
    )

    if isinstance(
        major_levels,
        dict,
    ):

        levels = major_levels.get(
            expected_side,
            [],
        )

    else:

        levels = [
            x
            for x in (
                major_levels
                or []
            )
            if isinstance(
                x,
                dict,
            )
        ]

    candidates = []

    excluded = _f(
        exclude_level
    )

    for level in levels:

        if not isinstance(
            level,
            dict,
        ):
            continue

        lp = _f(
            level.get(
                "price"
            )
        )

        if lp is None:
            continue

        side_text = str(
            level.get(
                "side"
            )
            or level.get(
                "type"
            )
            or ""
        ).upper()

        if expected_side not in side_text:
            continue

        # Do not target the sweep level itself.
        if excluded is not None:

            distance = _pct_distance(
                lp,
                excluded,
            )

            if (
                distance is not None
                and distance
                <= DUPLICATE_DISTANCE_PCT
            ):

                continue

        if (
            direction == "LONG"
            and lp > price
        ):

            candidates.append(
                level
            )

        elif (
            direction == "SHORT"
            and lp < price
        ):

            candidates.append(
                level
            )

    if not candidates:
        return None

    # Target = NEXT major liquidity,
    # therefore distance is primary.
    candidates.sort(
        key=lambda x: abs(
            _f(
                x["price"]
            )
            - price
        )
    )

    return candidates[0]


# ============================================================
# DETECT SWEEP
# ============================================================

def detect_sweep(
    candles_1h: List[Dict[str, Any]],
    current_price: float,
    direction: str,
    major_levels: Any,
) -> Optional[
    Dict[str, Any]
]:

    """
    Detects a recent 1H sweep.

    LONG:
        SSL below price
        low < SSL
        close > SSL
        bullish reclaim

    SHORT:
        BSL above price
        high > BSL
        close < BSL
        bearish reclaim
    """

    direction = str(
        direction
    ).upper()

    if direction not in {
        "LONG",
        "SHORT",
    }:

        return None

    current = _f(
        current_price
    )

    if current is None:
        return None

    expected_side = (
        "SSL"
        if direction == "LONG"
        else "BSL"
    )

    if isinstance(
        major_levels,
        dict,
    ):

        levels = major_levels.get(
            expected_side,
            [],
        )

    else:

        levels = [
            x
            for x in (
                major_levels
                or []
            )
            if isinstance(
                x,
                dict,
            )
            and expected_side
            in str(
                x.get(
                    "side"
                )
                or x.get(
                    "type"
                )
                or ""
            ).upper()
        ]

    if not levels:
        return None

    candles = get_confirmed_candles(
        candles_1h
    )

    recent = candles[
        -MAX_SWEEP_LOOKBACK:
    ]

    candidates = []

    for age_index, candle in enumerate(
        reversed(recent)
    ):

        high = _h(
            candle
        )

        low = _l(
            candle
        )

        close = _c(
            candle
        )

        if (
            high is None
            or low is None
            or close is None
        ):
            continue

        for level in levels:

            lp = _f(
                level.get(
                    "price"
                )
                if isinstance(
                    level,
                    dict,
                )
                else level
            )

            if lp is None:
                continue

            # =================================================
            # LONG
            # =================================================

            if direction == "LONG":

                # SSL must be below price.
                if lp >= current:
                    continue

                depth = (
                    (lp - low)
                    / lp
                    * 100.0
                )

                if (
                    low < lp
                    and depth
                    >= SWEEP_MIN_DEPTH_DETECT_PCT
                    and close > lp
                    and _bull(candle)
                ):

                    touches = (
                        _f(
                            level.get(
                                "touches"
                            )
                        )
                        or 1.0
                    )

                    strength = (
                        _f(
                            level.get(
                                "strength"
                            )
                        )
                        or 0.0
                    )

                    candidate_score = (
                        depth * 4.0
                        + strength / 20.0
                        + min(
                            touches,
                            5.0,
                        ) * 3.0
                        + (
                            10.0
                            - age_index
                        )
                    )

                    candidates.append(
                        {
                            "swept": True,
                            "direction": "LONG",
                            "level": lp,
                            "extreme": low,
                            "price": low,
                            "open_time": _t(
                                candle
                            ),
                            "liquidity_type": "SSL",
                            "touches": int(
                                touches
                            ),
                            "strength": strength,
                            "depth_pct": depth,
                            "age_in_scan": age_index,
                            "_score": candidate_score,
                        }
                    )

            # =================================================
            # SHORT
            # =================================================

            elif direction == "SHORT":

                # BSL must be above price.
                if lp <= current:
                    continue

                depth = (
                    (high - lp)
                    / lp
                    * 100.0
                )

                if (
                    high > lp
                    and depth
                    >= SWEEP_MIN_DEPTH_DETECT_PCT
                    and close < lp
                    and _bear(candle)
                ):

                    touches = (
                        _f(
                            level.get(
                                "touches"
                            )
                        )
                        or 1.0
                    )

                    strength = (
                        _f(
                            level.get(
                                "strength"
                            )
                        )
                        or 0.0
                    )

                    candidate_score = (
                        depth * 4.0
                        + strength / 20.0
                        + min(
                            touches,
                            5.0,
                        ) * 3.0
                        + (
                            10.0
                            - age_index
                        )
                    )

                    candidates.append(
                        {
                            "swept": True,
                            "direction": "SHORT",
                            "level": lp,
                            "extreme": high,
                            "price": high,
                            "open_time": _t(
                                candle
                            ),
                            "liquidity_type": "BSL",
                            "touches": int(
                                touches
                            ),
                            "strength": strength,
                            "depth_pct": depth,
                            "age_in_scan": age_index,
                            "_score": candidate_score,
                        }
                    )

    if not candidates:
        return None

    best = max(
        candidates,
        key=lambda x: x.get(
            "_score",
            0.0,
        ),
    )

    best.pop(
        "_score",
        None,
    )

    return best


# ============================================================
# FVG
# ============================================================

def detect_fvg(
    candles: List[Dict[str, Any]],
    timeframe: str = "5m",
) -> List[Dict[str, Any]]:

    if not candles:
        return []

    timeframe = str(
        timeframe
    ).lower()

    minimum_gap = (
        FVG_15M_MIN_GAP_PCT
        if timeframe == "15m"
        else FVG_5M_MIN_GAP_PCT
    )

    candles = get_confirmed_candles(
        candles
    )[-FVG_LOOKBACK:]

    if len(candles) < 3:
        return []

    result = []

    for i in range(
        2,
        len(candles),
    ):

        first = candles[
            i - 2
        ]

        third = candles[
            i
        ]

        first_high = _h(
            first
        )

        first_low = _l(
            first
        )

        third_high = _h(
            third
        )

        third_low = _l(
            third
        )

        if (
            first_high is None
            or first_low is None
            or third_high is None
            or third_low is None
        ):
            continue

        # ====================================================
        # BULLISH FVG
        # ====================================================

        if third_low > first_high:

            bottom = first_high
            top = third_low

            gap_pct = (
                (top - bottom)
                / bottom
                * 100.0
            )

            if gap_pct >= minimum_gap:

                result.append(
                    {
                        "type": "bullish",
                        "bottom": bottom,
                        "top": top,
                        "gap_pct": gap_pct,
                        "open_time": _t(
                            third
                        ),
                        "timeframe": timeframe,
                    }
                )

        # ====================================================
        # BEARISH FVG
        # ====================================================

        elif third_high < first_low:

            bottom = third_high
            top = first_low

            gap_pct = (
                (top - bottom)
                / bottom
                * 100.0
            )

            if gap_pct >= minimum_gap:

                result.append(
                    {
                        "type": "bearish",
                        "bottom": bottom,
                        "top": top,
                        "gap_pct": gap_pct,
                        "open_time": _t(
                            third
                        ),
                        "timeframe": timeframe,
                    }
                )

    result.sort(
        key=lambda x: (
            x.get(
                "open_time",
                0,
            )
            or 0
        ),
        reverse=True,
    )

    return result[
        :MAX_FVG_ZONES
    ]


def get_fvg_zones(
    candles_5m: List[Dict[str, Any]],
    candles_15m: Optional[
        List[Dict[str, Any]]
    ] = None,
) -> List[Dict[str, Any]]:

    result = []

    result.extend(
        detect_fvg(
            candles_5m,
            "5m",
        )
    )

    if candles_15m:

        result.extend(
            detect_fvg(
                candles_15m,
                "15m",
            )
        )

    return result[
        :MAX_FVG_ZONES * 2
    ]


# ============================================================
# MARKET DATA
# ============================================================

def get_market_data(
    symbol: str,
) -> Dict[str, Any]:

    pair = _normalize_symbol(
        symbol
    )

    price = get_current_price(
        pair
    )

    candles_d1 = get_klines(
        pair,
        "1d",
        LOOKBACK_D1,
    )

    candles_1h = get_klines(
        pair,
        "1h",
        LOOKBACK_1H,
    )

    candles_15m = get_klines(
        pair,
        "15m",
        LOOKBACK_15M,
    )

    candles_5m = get_klines(
        pair,
        "5m",
        LOOKBACK_5M,
    )

    candles_1m = get_klines(
        pair,
        "1m",
        LOOKBACK_1M,
    )

    major = _build_1h_major_liquidity(
        candles_1h,
        price,
        "NEUTRAL",
    )

    context = _build_15m_context(
        candles_15m,
        price,
    )

    fvgs = get_fvg_zones(
        candles_5m,
        candles_15m,
    )

    return {
        "symbol": pair,

        "coin": _coin_name(
            pair
        ),

        "price": price,

        "candles_d1": candles_d1,

        "candles_1h": candles_1h,

        "candles_15m": candles_15m,

        "candles_5m": candles_5m,

        "candles_1m": candles_1m,

        # ====================================================
        # MAJOR = ONLY 1H
        # ====================================================

        "major_liquidity": major,

        "major_bsl": major.get(
            "BSL",
            [],
        ),

        "major_ssl": major.get(
            "SSL",
            [],
        ),

        # ====================================================
        # LOCAL CONTEXT = 15M
        # ====================================================

        "context_liquidity": context,

        "context_bsl": context.get(
            "BSL",
            [],
        ),

        "context_ssl": context.get(
            "SSL",
            [],
        ),

        # ====================================================
        # FVG
        # ====================================================

        "fvgs": fvgs,

        # ====================================================
        # CURRENT LEG
        # ====================================================

        "structural_leg": major.get(
            "structural_leg",
            {},
        ),
    }


# ============================================================
# MAJOR LIQUIDITY API
# ============================================================

def get_major_liquidity(
    symbol: str,
) -> Dict[str, Any]:

    market = get_market_data(
        symbol
    )

    major = market[
        "major_liquidity"
    ]

    return {
        "symbol": market[
            "symbol"
        ],

        "price": market[
            "price"
        ],

        "BSL": major.get(
            "BSL",
            [],
        ),

        "SSL": major.get(
            "SSL",
            [],
        ),

        "major_liquidity": major,

        "context_liquidity": market[
            "context_liquidity"
        ],

        "structural_leg": major.get(
            "structural_leg",
            {},
        ),
    }


# ============================================================
# MARKET SNAPSHOT
# ============================================================

def market_snapshot(
    symbol: str,
) -> Dict[str, Any]:

    market = get_market_data(
        symbol
    )

    major = market[
        "major_liquidity"
    ]

    context = market[
        "context_liquidity"
    ]

    return {
        "market_version": MARKET_VERSION,

        "symbol": market[
            "symbol"
        ],

        "coin": market[
            "coin"
        ],

        "price": market[
            "price"
        ],

        "major_source": "1H_ONLY",

        "major_bsl": major.get(
            "BSL",
            [],
        ),

        "major_ssl": major.get(
            "SSL",
            [],
        ),

        "context_bsl": context.get(
            "BSL",
            [],
        ),

        "context_ssl": context.get(
            "SSL",
            [],
        ),

        "structural_leg": major.get(
            "structural_leg",
            {},
        ),

        "fvgs": market.get(
            "fvgs",
            [],
        ),
    }


# ============================================================
# FORMAT MAJOR
# ============================================================

def format_major_liquidity(
    data: Any,
) -> str:

    if isinstance(
        data,
        str,
    ):

        try:

            data = get_major_liquidity(
                data
            )

        except Exception as exc:

            return (
                "❌ Не удалось получить "
                f"ликвидность: {exc}"
            )

    if not isinstance(
        data,
        dict,
    ):

        return (
            "❌ Некорректные данные "
            "ликвидности."
        )

    price = _f(
        data.get(
            "price"
        )
    )

    bsl = data.get(
        "BSL",
        [],
    )

    ssl = data.get(
        "SSL",
        [],
    )

    context = data.get(
        "context_liquidity",
        {},
    )

    lines = []

    lines.append(
        "💧 MAJOR LIQUIDITY · 1H"
    )

    if price is not None:

        lines.append(
            f"Цена: ${price:.6f}"
        )

    lines.append("")

    # ========================================================
    # BSL
    # ========================================================

    lines.append(
        "🔴 BSL · ТОЛЬКО ВЫШЕ ЦЕНЫ"
    )

    if bsl:

        for level in bsl:

            p = _f(
                level.get(
                    "price"
                )
            )

            if p is None:
                continue

            # Safety check.
            if (
                price is not None
                and p <= price
            ):
                continue

            distance = (
                _pct_distance(
                    p,
                    price,
                )
                if price is not None
                else None
            )

            strength = (
                _f(
                    level.get(
                        "strength"
                    )
                )
                or 0.0
            )

            freshness = (
                level.get(
                    "freshness_label"
                )
                or "?"
            )

            lines.append(
                f"• ${p:.6f} "
                f"• {distance:.2f}% "
                f"• S{strength:.0f} "
                f"• {freshness} "
                f"[1H]"
                if distance is not None
                else
                f"• ${p:.6f} "
                f"• S{strength:.0f} "
                f"• {freshness} "
                f"[1H]"
            )

    else:

        lines.append(
            "• нет активных уровней"
        )

    lines.append("")

    # ========================================================
    # SSL
    # ========================================================

    lines.append(
        "🟢 SSL · ТОЛЬКО НИЖЕ ЦЕНЫ"
    )

    if ssl:

        for level in ssl:

            p = _f(
                level.get(
                    "price"
                )
            )

            if p is None:
                continue

            # Safety check.
            if (
                price is not None
                and p >= price
            ):
                continue

            distance = (
                _pct_distance(
                    p,
                    price,
                )
                if price is not None
                else None
            )

            strength = (
                _f(
                    level.get(
                        "strength"
                    )
                )
                or 0.0
            )

            freshness = (
                level.get(
                    "freshness_label"
                )
                or "?"
            )

            lines.append(
                f"• ${p:.6f} "
                f"• {distance:.2f}% "
                f"• S{strength:.0f} "
                f"• {freshness} "
                f"[1H]"
                if distance is not None
                else
                f"• ${p:.6f} "
                f"• S{strength:.0f} "
                f"• {freshness} "
                f"[1H]"
            )

    else:

        lines.append(
            "• нет активных уровней"
        )

    lines.append("")

    # ========================================================
    # STRUCTURE
    # ========================================================

    leg = data.get(
        "structural_leg",
        {},
    )

    if isinstance(
        leg,
        dict,
    ):

        lines.append(
            "🧭 1H STRUCTURAL LEG"
        )

        leg_direction = (
            leg.get(
                "leg_direction"
            )
            or "NEUTRAL"
        )

        lines.append(
            f"Direction: {leg_direction}"
        )

        leg_low = _f(
            leg.get(
                "low"
            )
        )

        leg_high = _f(
            leg.get(
                "high"
            )
        )

        if leg_low is not None:

            lines.append(
                f"Low: ${leg_low:.6f}"
            )

        if leg_high is not None:

            lines.append(
                f"High: ${leg_high:.6f}"
            )

        lines.append("")

    # ========================================================
    # LOCAL
    # ========================================================

    lines.append(
        "🧭 LOCAL CONTEXT · 15M"
    )

    if isinstance(
        context,
        dict,
    ):

        context_bsl = context.get(
            "BSL",
            [],
        )

        context_ssl = context.get(
            "SSL",
            [],
        )

        bsl_text = []

        for level in context_bsl:

            p = _f(
                level.get(
                    "price"
                )
            )

            if p is not None:

                bsl_text.append(
                    f"${p:.6f}"
                )

        ssl_text = []

        for level in context_ssl:

            p = _f(
                level.get(
                    "price"
                )
            )

            if p is not None:

                ssl_text.append(
                    f"${p:.6f}"
                )

        lines.append(
            "BSL: "
            + (
                ", ".join(
                    bsl_text
                )
                or "нет"
            )
        )

        lines.append(
            "SSL: "
            + (
                ", ".join(
                    ssl_text
                )
                or "нет"
            )
        )

    lines.append("")

    lines.append(
        "⚠️ 15M не является Major."
    )

    return "\n".join(
        lines
    )


# ============================================================
# DEBUG
# ============================================================

def debug_symbol(
    symbol: str,
) -> Dict[str, Any]:

    market = get_market_data(
        symbol
    )

    major = market[
        "major_liquidity"
    ]

    price = market[
        "price"
    ]

    invalid_bsl = []

    for level in major.get(
        "BSL",
        [],
    ):

        p = _f(
            level.get(
                "price"
            )
        )

        if (
            p is not None
            and p <= price
        ):

            invalid_bsl.append(
                p
            )

    invalid_ssl = []

    for level in major.get(
        "SSL",
        [],
    ):

        p = _f(
            level.get(
                "price"
            )
        )

        if (
            p is not None
            and p >= price
        ):

            invalid_ssl.append(
                p
            )

    return {
        "market_version": MARKET_VERSION,

        "symbol": market[
            "symbol"
        ],

        "price": price,

        "major_source": "1H_ONLY",

        "major": major,

        "structural_leg": major.get(
            "structural_leg",
            {},
        ),

        "validation": {
            "invalid_bsl": invalid_bsl,
            "invalid_ssl": invalid_ssl,

            "geometry_ok": (
                not invalid_bsl
                and not invalid_ssl
            ),
        },

        "counts": {
            "major_bsl": len(
                major.get(
                    "BSL",
                    [],
                )
            ),

            "major_ssl": len(
                major.get(
                    "SSL",
                    [],
                )
            ),

            "context_bsl": len(
                market[
                    "context_liquidity"
                ].get(
                    "BSL",
                    [],
                )
            ),

            "context_ssl": len(
                market[
                    "context_liquidity"
                ].get(
                    "SSL",
                    [],
                )
            ),
        },

        "fvgs": market.get(
            "fvgs",
            [],
        ),
    }


# ============================================================
# CACHE
# ============================================================

def clear_market_cache() -> None:

    _KLINE_CACHE.clear()

    _PRICE_CACHE.clear()


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "MARKET_VERSION",
    "COINS",

    "get_current_price",
    "get_klines",
    "get_confirmed_candles",

    "find_swing_highs",
    "find_swing_lows",

    "get_1h_swings",
    "get_15m_swings",

    "cluster_levels",

    "get_level_age",
    "freshness_score",
    "freshness_label",

    "count_local_touches",

    "calculate_strength",
    "calculate_priority",

    "level_has_been_swept",

    "find_major_liquidity",
    "get_major_liquidity",
    "get_target_liquidity",

    "detect_sweep",

    "detect_fvg",
    "get_fvg_zones",

    "get_market_data",
    "market_snapshot",

    "format_major_liquidity",
    "debug_symbol",

    "clear_market_cache",
]


# ============================================================
# COMPATIBILITY: LEVEL SWEPT
# ============================================================

def level_has_been_swept(
    candles: List[Dict[str, Any]],
    level_price: float,
    side: str,
    lookback: int = RECENT_SWEEP_LOOKBACK_1H,
    min_depth_pct: float = SWEEP_MIN_DEPTH_PCT,
) -> bool:

    """
    Compatibility helper.

    IMPORTANT:
    This function exists for compatibility/debugging.

    _select_major_side() DOES NOT call it.

    Therefore a fresh sweep remains visible
    to strategy.py.
    """

    p = _f(
        level_price
    )

    if p is None or p <= 0:
        return False

    side = str(
        side
    ).upper()

    recent = get_confirmed_candles(
        candles or []
    )[-lookback:]

    for candle in recent:

        high = _h(
            candle
        )

        low = _l(
            candle
        )

        close = _c(
            candle
        )

        if (
            high is None
            or low is None
            or close is None
        ):
            continue

        if side == "SSL":

            depth = (
                (p - low)
                / p
                * 100.0
            )

            if (
                low < p
                and depth
                >= min_depth_pct
                and close > p
            ):

                return True

        elif side == "BSL":

            depth = (
                (high - p)
                / p
                * 100.0
            )

            if (
                high > p
                and depth
                >= min_depth_pct
                and close < p
            ):

                return True

    return False


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":

    import sys

    symbol = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "SOL"
    )

    print(
        f"TradeMind market.py "
        f"{MARKET_VERSION}"
    )

    try:

        result = debug_symbol(
            symbol
        )

        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )

    except Exception as exc:

        print(
            f"ERROR: {exc}"
        )