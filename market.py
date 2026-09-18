"""
TradeMind — market.py 7.7

MAJOR LIQUIDITY:
    ТОЛЬКО 1H

LONG:
    ищем SSL ниже текущей цены
    ↓
    SSL SWEEP
    ↓
    15M CONFIRMATION
    ↓
    5M ILM
    ↓
    ENTRY
    ↓
    следующий 1H BSL

SHORT:
    ищем BSL выше текущей цены
    ↓
    BSL SWEEP
    ↓
    15M CONFIRMATION
    ↓
    5M ILM
    ↓
    ENTRY
    ↓
    следующий 1H SSL

ВАЖНО:
- D1/W1 не являются частью Major Liquidity.
- 15M/5M/1M не являются Major Liquidity.
- Round numbers не являются Major Liquidity.
- BSL ниже цены НЕ является активным BSL.
- SSL выше цены НЕ является активным SSL.
- Недавний sweep не удаляется заранее.
- Последняя незакрытая свеча не используется как подтверждённая.
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

MARKET_VERSION = "7.7"


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
# SWEEP
# ============================================================

RECENT_SWEEP_LOOKBACK_1H = 30
RECENT_SWEEP_LOOKBACK_15M = 60

SWEEP_MIN_DEPTH_PCT = 0.15
SWEEP_MIN_DEPTH_DETECT_PCT = 0.08

MAX_SWEEP_LOOKBACK = 8


# ============================================================
# FVG
# ============================================================

FVG_5M_MIN_GAP_PCT = 0.05
FVG_15M_MIN_GAP_PCT = 0.10

MAX_FVG_ZONES = 5
FVG_LOOKBACK = 100


# ============================================================
# HTTP SESSION
# ============================================================

_SESSION: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _SESSION

    if _SESSION is None:
        _SESSION = requests.Session()

        _SESSION.headers.update(
            {
                "User-Agent": "TradeMind/7.7",
                "Accept": "application/json",
            }
        )

    return _SESSION


# ============================================================
# GENERIC HELPERS
# ============================================================

def _f(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None

        value = float(value)

        if not math.isfinite(value):
            return None

        return value

    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None

        return int(float(value))

    except (TypeError, ValueError):
        return None


def _pct_distance(
    a: Any,
    b: Any,
) -> Optional[float]:

    a = _f(a)
    b = _f(b)

    if a is None or b is None or b == 0:
        return None

    return abs(a - b) / abs(b) * 100.0


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
        return COINS[symbol]

    if symbol.endswith("USDT"):
        return symbol

    return f"{symbol}USDT"


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

    if symbol.endswith("USDT"):
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

    if o is None or c is None:
        return 0.0

    return abs(
        c - o
    )


def _range(
    candle: Any,
) -> float:

    h = _h(candle)
    l = _l(candle)

    if h is None or l is None:
        return 0.0

    return max(
        0.0,
        h - l,
    )


def _body_ratio(
    candle: Any,
) -> float:

    r = _range(candle)

    if r <= 0:
        return 0.0

    return _body(candle) / r


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
# BINANCE
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

    session = _get_session()

    response = session.get(
        url,
        params=params or {},
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    data = response.json()

    if (
        isinstance(data, dict)
        and data.get("code") is not None
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
            now - cached["time"]
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

    _PRICE_CACHE[pair] = {
        "time": now,
        "price": price,
    }

    return price


# ============================================================
# FETCH KLINES
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
            not isinstance(row, list)
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

    key = (
        f"{_normalize_symbol(symbol)}:"
        f"{interval}:"
        f"{limit}"
    )

    now = time.time()

    cached = _KLINE_CACHE.get(
        key
    )

    if cached:

        if (
            now - cached["time"]
            < CACHE_TTL.get(
                interval,
                5,
            )
        ):
            return list(
                cached["candles"]
            )

    candles = _fetch_klines(
        symbol,
        interval,
        limit,
    )

    _KLINE_CACHE[key] = {
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
        time.time() * 1000
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

        if close_time <= now_ms:
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
        left + right + 1
    ):
        return highs, lows

    for i in range(
        left,
        len(candles) - right,
    ):

        current_high = _h(
            candles[i]
        )

        current_low = _l(
            candles[i]
        )

        if (
            current_high is None
            or current_low is None
        ):
            continue

        left_highs = [
            _h(candles[j])
            for j in range(
                i - left,
                i,
            )
        ]

        right_highs = [
            _h(candles[j])
            for j in range(
                i + 1,
                i + right + 1,
            )
        ]

        left_lows = [
            _l(candles[j])
            for j in range(
                i - left,
                i,
            )
        ]

        right_lows = [
            _l(candles[j])
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
                current_high
                > max(left_highs)
                and current_high
                >= max(right_highs)
            ):
                highs.append(
                    (
                        i,
                        current_high,
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
                current_low
                < min(left_lows)
                and current_low
                <= min(right_lows)
            ):
                lows.append(
                    (
                        i,
                        current_low,
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
# CLUSTER LEVELS
# ============================================================

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

        previous = clusters[-1][-1]

        distance = (
            abs(
                price - previous
            )
            / previous
            * 100
        )

        if distance <= distance_pct:
            clusters[-1].append(
                price
            )
        else:
            clusters.append(
                [price]
            )

    result = []

    for cluster in clusters:

        average = (
            sum(cluster)
            / len(cluster)
        )

        result.append(
            {
                "price": average,
                "touches": len(
                    cluster
                ),
                "members": list(
                    cluster
                ),
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

    p = _f(price)

    if p is None or p <= 0:
        return 0

    tolerance = (
        p
        * tolerance_pct
        / 100
    )

    touches = 0

    for candle in candles or []:

        high = _h(candle)
        low = _l(candle)

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
# RECENT SWEEP CHECK
# ============================================================

def level_has_been_swept(
    candles: List[Dict[str, Any]],
    level_price: float,
    side: str,
    lookback: int = RECENT_SWEEP_LOOKBACK_1H,
    min_depth_pct: float = SWEEP_MIN_DEPTH_PCT,
) -> bool:

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

        high = _h(candle)
        low = _l(candle)
        close = _c(candle)

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
                * 100
            )

            if (
                low < p
                and depth >= min_depth_pct
                and close > p
            ):
                return True

        elif side == "BSL":

            depth = (
                (high - p)
                / p
                * 100
            )

            if (
                high > p
                and depth >= min_depth_pct
                and close < p
            ):
                return True

    return False


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
        max(
            0.0,
            strength,
        ),
        3,
    )


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
            + max(
                0.0,
                distance,
            )
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

    priority = (
        fresh * 0.45
        + distance_score * 0.20
        + cluster_score * 0.15
        + strength_score * 0.20
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
# SELECT MAJOR SIDE
# ============================================================

def _select_major_side(
    levels: List[Dict[str, Any]],
    current_price: float,
    side: str,
    max_count: int,
) -> List[Dict[str, Any]]:

    """
    КРИТИЧЕСКИЙ ФИЛЬТР.

    BSL:
        ТОЛЬКО выше текущей цены.

    SSL:
        ТОЛЬКО ниже текущей цены.

    Это НЕ strength gate.
    Это не искусственный distance gate.
    Это базовая геометрия ликвидности.
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
        # BSL MUST BE ABOVE PRICE
        # ====================================================

        if side == "BSL":

            if price <= current:
                continue

        # ====================================================
        # SSL MUST BE BELOW PRICE
        # ====================================================

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

        item["priority"] = (
            calculate_priority(
                item,
                current,
            )
        )

        # НЕ удаляем recent sweep.
        #
        # Strategy должна сама решить,
        # был ли sweep только что.
        item["recently_swept"] = False

        candidates.append(
            item
        )

    # Сначала priority.
    candidates.sort(
        key=lambda x: (
            x.get(
                "priority",
                0,
            ),
            x.get(
                "freshness",
                0,
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
) -> Dict[str, List[Dict[str, Any]]]:

    confirmed = get_confirmed_candles(
        candles_1h
    )

    if len(confirmed) < 10:
        return {
            "BSL": [],
            "SSL": [],
        }

    highs, lows = _find_swings(
        confirmed,
        left=2,
        right=1,
    )

    raw_bsl = []

    for index, price in highs:

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
            }
        )

    raw_ssl = []

    for index, price in lows:

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
            }
        )

    # ========================================================
    # CLUSTER 1H SWINGS
    # ========================================================

    def cluster_1h(
        source: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:

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

            distance = _pct_distance(
                item["price"],
                previous,
            )

            if (
                distance is not None
                and distance
                <= CLUSTER_DISTANCE_PCT
            ):
                clusters[-1].append(
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

            average_price = (
                sum(prices)
                / len(prices)
            )

            newest = max(
                cluster,
                key=lambda x: (
                    x.get(
                        "index",
                        0,
                    )
                ),
            )

            result.append(
                {
                    "price": average_price,
                    "side": newest[
                        "side"
                    ],
                    "type": newest[
                        "type"
                    ],
                    "source": "1H",
                    "index": newest.get(
                        "index"
                    ),
                    "open_time": newest.get(
                        "open_time"
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

    clustered_bsl = cluster_1h(
        raw_bsl
    )

    clustered_ssl = cluster_1h(
        raw_ssl
    )

    # ========================================================
    # IMPORTANT:
    #
    # FILTER BY CURRENT PRICE
    # ========================================================

    bsl = _select_major_side(
        clustered_bsl,
        current_price,
        "BSL",
        MAX_MAJOR_PER_SIDE,
    )

    ssl = _select_major_side(
        clustered_ssl,
        current_price,
        "SSL",
        MAX_MAJOR_PER_SIDE,
    )

    return {
        "BSL": bsl,
        "SSL": ssl,
    }


# ============================================================
# 15M LOCAL CONTEXT
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

    raw_bsl = []

    for index, price in highs:

        raw_bsl.append(
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

    raw_ssl = []

    for index, price in lows:

        raw_ssl.append(
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

    # Здесь тоже сохраняем только правильную
    # сторону цены, но это LOCAL CONTEXT,
    # не Major.

    bsl = _select_major_side(
        raw_bsl,
        current_price,
        "BSL",
        MAX_15M_PER_SIDE,
    )

    for item in bsl:
        item["type"] = "BSL_15M"
        item["source"] = "15M"

    ssl = _select_major_side(
        raw_ssl,
        current_price,
        "SSL",
        MAX_15M_PER_SIDE,
    )

    for item in ssl:
        item["type"] = "SSL_15M"
        item["source"] = "15M"

    return {
        "BSL": bsl,
        "SSL": ssl,
    }


# ============================================================
# ROUND LEVELS — NEVER MAJOR
# ============================================================

def _round_step(
    price: float,
) -> float:

    if price >= 100000:
        return 5000.0

    if price >= 10000:
        return 500.0

    if price >= 1000:
        return 50.0

    if price >= 100:
        return 5.0

    if price >= 10:
        return 0.5

    if price >= 1:
        return 0.05

    if price >= 0.1:
        return 0.01

    return 0.001


def _round_levels(
    current_price: float,
    count_each_side: int = 4,
) -> Tuple[
    List[Dict[str, Any]],
    List[Dict[str, Any]],
]:

    p = _f(
        current_price
    )

    if p is None or p <= 0:
        return [], []

    step = _round_step(
        p
    )

    center = (
        round(
            p / step
        )
        * step
    )

    bsl = []
    ssl = []

    for i in range(
        1,
        count_each_side + 1,
    ):

        upper = (
            center
            + step * i
        )

        lower = (
            center
            - step * i
        )

        bsl.append(
            {
                "price": upper,
                "side": "BSL",
                "type": "BSL_ROUND",
                "source": "ROUND",
                "touches": 1,
                "strength": 0,
                "age": 0,
            }
        )

        ssl.append(
            {
                "price": lower,
                "side": "SSL",
                "type": "SSL_ROUND",
                "source": "ROUND",
                "touches": 1,
                "strength": 0,
                "age": 0,
            }
        )

    return bsl, ssl


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
    MAJOR = ONLY 1H.

    Возвращается плоский список,
    совместимый с bot.py / strategy.py.

    BSL:
        только выше current_price.

    SSL:
        только ниже current_price.
    """

    price = _f(
        current_price
    )

    if price is None:
        return []

    major = _build_1h_major_liquidity(
        candles_1h or [],
        price,
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
        int(max_levels),
    )

    # Сначала обеспечиваем обе стороны.
    each_side = max(
        1,
        total_limit // 2,
    )

    bsl = bsl[
        :each_side
    ]

    ssl = ssl[
        :each_side
    ]

    combined = (
        bsl + ssl
    )

    # Priority для отображения/рабочего выбора.
    combined.sort(
        key=lambda x: (
            x.get(
                "priority",
                0,
            ),
            x.get(
                "freshness",
                0,
            ),
        ),
        reverse=True,
    )

    return combined[
        :total_limit
    ]


# ============================================================
# TARGET
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

        side = str(
            level.get(
                "side"
            )
            or level.get(
                "type"
            )
            or ""
        ).upper()

        if expected_side not in side:
            continue

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
# SWEEP
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
    Detect fresh sweep of active 1H Major.

    LONG:
        SSL ниже цены
        low < SSL
        close > SSL
        bullish reclaim

    SHORT:
        BSL выше цены
        high > BSL
        close < BSL
        bearish reclaim
    """

    if direction not in {
        "LONG",
        "SHORT",
    }:
        return None

    price = _f(
        current_price
    )

    if price is None:
        return None

    expected = (
        "SSL"
        if direction == "LONG"
        else "BSL"
    )

    if isinstance(
        major_levels,
        dict,
    ):

        levels = major_levels.get(
            expected,
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
            and expected in str(
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
        candles_1h or []
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
            # LONG / SSL SWEEP
            # =================================================

            if direction == "LONG":

                # SSL должен находиться
                # ниже текущей цены.
                if lp >= price:
                    continue

                depth = (
                    (lp - low)
                    / lp
                    * 100
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

                    score = (
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
                            "open_time": _t(
                                candle
                            ),
                            "price": low,
                            "liquidity_type": "SSL",
                            "touches": int(
                                touches
                            ),
                            "strength": strength,
                            "depth_pct": depth,
                            "age_in_scan": age_index,
                            "_score": score,
                        }
                    )

            # =================================================
            # SHORT / BSL SWEEP
            # =================================================

            else:

                # BSL должен находиться
                # выше текущей цены.
                if lp <= price:
                    continue

                depth = (
                    (high - lp)
                    / lp
                    * 100
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

                    score = (
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
                            "open_time": _t(
                                candle
                            ),
                            "price": high,
                            "liquidity_type": "BSL",
                            "touches": int(
                                touches
                            ),
                            "strength": strength,
                            "depth_pct": depth,
                            "age_in_scan": age_index,
                            "_score": score,
                        }
                    )

    if not candidates:
        return None

    best = max(
        candidates,
        key=lambda x: x.get(
            "_score",
            0,
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

        # Bullish FVG.
        if third_low > first_high:

            bottom = first_high
            top = third_low

            gap_pct = (
                (top - bottom)
                / bottom
                * 100
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

        # Bearish FVG.
        elif third_high < first_low:

            bottom = third_high
            top = first_low

            gap_pct = (
                (top - bottom)
                / bottom
                * 100
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
        # MAJOR — ONLY 1H
        # ====================================================

        "major_liquidity": major,

        # ====================================================
        # LOCAL — 15M
        # ====================================================

        "context_liquidity": context,

        # ====================================================
        # FVG
        # ====================================================

        "fvgs": fvgs,

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
    }


# ============================================================
# GET MAJOR LIQUIDITY
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

        "fvgs": market.get(
            "fvgs",
            [],
        ),
    }


# ============================================================
# FORMAT
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

    context_bsl = (
        context.get(
            "BSL",
            [],
        )
        if isinstance(
            context,
            dict,
        )
        else []
    )

    context_ssl = (
        context.get(
            "SSL",
            [],
        )
        if isinstance(
            context,
            dict,
        )
        else []
    )

    lines = []

    lines.append(
        "💧 MAJOR LIQUIDITY · 1H"
    )

    if price is not None:
        lines.append(
            f"Цена: {price:.6f}"
        )

    lines.append("")

    # ========================================================
    # BSL
    # ========================================================

    lines.append(
        "🔴 BSL · 1H · ВЫШЕ ЦЕНЫ"
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

            distance = (
                _pct_distance(
                    p,
                    price,
                )
                if price
                else None
            )

            distance_text = (
                f"{distance:.2f}%"
                if distance
                is not None
                else "?"
            )

            strength = (
                level.get(
                    "strength",
                    0,
                )
            )

            lines.append(
                f"• ${p:.6f} "
                f"• {distance_text} "
                f"• S{float(strength):.0f} "
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
        "🟢 SSL · 1H · НИЖЕ ЦЕНЫ"
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

            distance = (
                _pct_distance(
                    p,
                    price,
                )
                if price
                else None
            )

            distance_text = (
                f"{distance:.2f}%"
                if distance
                is not None
                else "?"
            )

            strength = (
                level.get(
                    "strength",
                    0,
                )
            )

            lines.append(
                f"• ${p:.6f} "
                f"• {distance_text} "
                f"• S{float(strength):.0f} "
                f"[1H]"
            )

    else:
        lines.append(
            "• нет активных уровней"
        )

    lines.append("")

    # ========================================================
    # LOCAL
    # ========================================================

    lines.append(
        "🧭 LOCAL CONTEXT · 15M"
    )

    context_bsl_text = []

    for level in context_bsl:

        p = _f(
            level.get(
                "price"
            )
        )

        if p is not None:
            context_bsl_text.append(
                f"${p:.6f}"
            )

    context_ssl_text = []

    for level in context_ssl:

        p = _f(
            level.get(
                "price"
            )
        )

        if p is not None:
            context_ssl_text.append(
                f"${p:.6f}"
            )

    lines.append(
        "BSL: "
        + (
            ", ".join(
                context_bsl_text
            )
            or "нет"
        )
    )

    lines.append(
        "SSL: "
        + (
            ", ".join(
                context_ssl_text
            )
            or "нет"
        )
    )

    lines.append("")

    lines.append(
        "⚠️ 15M не является Major Liquidity."
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

    context = market[
        "context_liquidity"
    ]

    price = market[
        "price"
    ]

    # Дополнительная проверка,
    # чтобы мы сами сразу увидели,
    # если алгоритм когда-нибудь вернёт
    # невозможный уровень.

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

        "context": context,

        "validation": {
            "invalid_bsl": invalid_bsl,
            "invalid_ssl": invalid_ssl,
            "major_geometry_ok": (
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
                context.get(
                    "BSL",
                    [],
                )
            ),

            "context_ssl": len(
                context.get(
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

    "cluster_levels",
    "get_level_age",
    "freshness_score",
    "freshness_label",
    "count_local_touches",
    "level_has_been_swept",

    "calculate_strength",
    "calculate_priority",

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