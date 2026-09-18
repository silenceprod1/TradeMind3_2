"""
TradeMind market.py 8.0

LIQUIDITY ENGINE INTEGRATION

CORE:
    1H Direction
        ↓
    1H Structural Major Liquidity
        ↓
    Liquidity Engine:
        • 1H structure
        • Order Book
        • Open Interest
        • Taker Flow
        ↓
    Sweep
        ↓
    15M Confirmation
        ↓
    5M ILM
        ↓
    Entry

IMPORTANT:
- D1 is context only.
- 15M is local context only.
- 5M is trigger only.
- Major Liquidity = 1H ONLY.
- Round numbers are NOT Major Liquidity.
- BSL must be above current price.
- SSL must be below current price.
- Fresh sweeps are NOT deleted before strategy.py sees them.
- No hard age expiration.
- No artificial minimum distance filter.
- liquidity_engine.py is the primary liquidity source.
- Current structural engine remains a safe fallback.
"""

from __future__ import annotations

import inspect
import json
import math
import time
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# LIQUIDITY ENGINE
# ============================================================

try:
    import liquidity_engine

    LIQUIDITY_ENGINE_AVAILABLE = True

except Exception as exc:

    liquidity_engine = None

    LIQUIDITY_ENGINE_AVAILABLE = False

    LIQUIDITY_ENGINE_IMPORT_ERROR = str(exc)


# ============================================================
# VERSION
# ============================================================

MARKET_VERSION = "8.0"


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
LOOKBACK_1H = 300
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

_LIQUIDITY_CACHE: Dict[str, Dict[str, Any]] = {}

_SESSION: Optional[requests.Session] = None


# ============================================================
# LIQUIDITY SETTINGS
# ============================================================

CLUSTER_DISTANCE_PCT = 0.15

ZONE_WIDTH_PCT = 0.20

MAX_MAJOR_PER_SIDE = 8

MAX_MAJOR_TOTAL = 16

MAX_15M_PER_SIDE = 4

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
                "User-Agent": f"TradeMind/{MARKET_VERSION}",
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

        result.append(
            {
                "price": (
                    sum(prices)
                    / len(prices)
                ),

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

    # If liquidity_engine.py already
    # calculated a strength, preserve it.
    existing = _f(
        level.get(
            "strength"
        )
    )

    if existing is not None:

        return round(
            existing,
            3,
        )

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

    last_high = (
        max(
            swings_high,
            key=lambda x: x[0],
        )
        if swings_high
        else None
    )

    last_low = (
        max(
            swings_low,
            key=lambda x: x[0],
        )
        if swings_low
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

    structural_bonus = _f(
        level.get(
            "structural_bonus"
        )
    ) or 0.0

    # Liquidity-engine bonus.
    engine_score = _f(
        level.get(
            "liquidity_score"
        )
    )

    if engine_score is None:

        engine_score = _f(
            level.get(
                "engine_score"
            )
        )

    if engine_score is None:

        engine_score = 0.0

    engine_score = max(
        0.0,
        min(
            engine_score,
            1.