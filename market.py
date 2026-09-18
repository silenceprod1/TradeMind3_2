# ============================================================
# TradeMind 7.5
# market.py
#
# Цель версии:
#
# - Major Liquidity = ТОЛЬКО подтверждённые 1H swing highs/lows
# - свежие 1H уровни не отбрасываются из-за strength
# - нет искусственного MIN_DISTANCE
# - нет искусственного GAP между Major зонами
# - нет жёсткого MAX_LEVEL_AGE для Major
# - 15M НЕ превращается в Major Liquidity
# - Round Numbers НЕ превращаются в Major Liquidity
# - 15M используется только как дополнительный контекст
# - текущая цена берётся напрямую из Binance
# - FVG 5M / 15M остаётся доступным
# - swept/taken liquidity исключается
# - сохранена совместимость с strategy.py / bot.py
#
# Pipeline:
#
# 1H structure
#      ↓
# Major Liquidity
#      ↓
# Sweep
#      ↓
# 15M Confirmation
#      ↓
# 5M ILM
#      ↓
# Entry
#
# ============================================================

from __future__ import annotations

import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# VERSION
# ============================================================

MARKET_VERSION = "7.5"


# ============================================================
# BINANCE
# ============================================================

BASE_URL = "https://api.binance.com/api/v3"

REQUEST_TIMEOUT = 10
HTTP_RETRIES = 3


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

KLINES_TTL = {
    "1d": 180,
    "1h": 15,
    "15m": 8,
    "5m": 5,
    "1m": 2,
}


_klines_cache: Dict[
    Tuple[str, str, int],
    Tuple[float, List[Dict[str, Any]]]
] = {}

_klines_lock = threading.RLock()


# ============================================================
# SWING SETTINGS
# ============================================================

# ------------------------------------------------------------
# 1H
# ------------------------------------------------------------
#
# 2 свечи слева + 1 свеча справа.
#
# Это позволяет получать свежие подтверждённые swing'и
# быстрее, чем старый вариант 2 + 2.
#
# ВАЖНО:
# последняя незакрытая свеча всегда исключается
# из построения Major Liquidity.
# ------------------------------------------------------------

SWING_LEFT = 2
SWING_RIGHT = 1


# ------------------------------------------------------------
# 15M
# ------------------------------------------------------------

SWING_LEFT_15M = 2
SWING_RIGHT_15M = 1


# ------------------------------------------------------------
# D1
# ------------------------------------------------------------

SWING_LEFT_D1 = 3
SWING_RIGHT_D1 = 3


# ============================================================
# LIQUIDITY SETTINGS
# ============================================================

# Расстояние, на котором два 1H swing'а считаются одним
# кластером.

CLUSTER_DISTANCE_PCT = 0.15


# Размер визуальной зоны вокруг уровня.

ZONE_WIDTH_PCT = 0.20


# ------------------------------------------------------------
# MAJOR LIMITS
# ------------------------------------------------------------
#
# Major строится только из 1H.
#
# Никаких жёстких фильтров:
#
# - MIN_MAJOR_DISTANCE
# - MIN_MAJOR_STRENGTH
# - MIN_ZONE_GAP
# - MAX_MAJOR_AGE
#
# ------------------------------------------------------------

MAX_LEVELS_PER_SIDE = 8
MAX_TOTAL_LEVELS = 16


# Дополнительные 15M уровни.
#
# Они существуют отдельно и НЕ входят в Major Liquidity.

MAX_15M_LEVELS_PER_SIDE = 6


# ============================================================
# ROUND NUMBERS
# ============================================================

ROUND_MAX_COUNT = 5
ROUND_MAX_DISTANCE_PCT = 20.0
ROUND_STRENGTH = 45.0


# ============================================================
# FVG
# ============================================================

FVG_MIN_SIZE_PCT_5M = 0.05
FVG_MIN_SIZE_PCT_15M = 0.10

FVG_MAX_ZONES_PER_TF = 5
FVG_MAX_LOOKBACK = 100


# ============================================================
# SWEEP
# ============================================================

SWEEP_RECENT_LOOKBACK_1H = 30
SWEEP_RECENT_LOOKBACK_15M = 60

SWEPT_MIN_DEPTH_PCT = 0.15

MIN_SWEEP_DEPTH_PCT = 0.08

MAX_SWEEP_LOOKBACK_1H = 8


# ============================================================
# LOCAL TOUCH
# ============================================================

LOCAL_TOUCH_DISTANCE_PCT = 0.20


# ============================================================
# D1
# ============================================================

D1_SWING_LOOKBACK = 60
D1_POINT_LOOKBACK = 30


# ============================================================
# HTTP THREAD LOCAL
# ============================================================

_thread_local = threading.local()


def _get_session():
    """
    Отдельная requests.Session для каждого worker thread.
    """

    session = getattr(
        _thread_local,
        "session",
        None,
    )

    if session is None:

        session = requests.Session()

        session.headers.update({
            "User-Agent": "TradeMind/7.5",
            "Accept": "application/json",
        })

        adapter = requests.adapters.HTTPAdapter(
            pool_connections=8,
            pool_maxsize=8,
            max_retries=0,
        )

        session.mount(
            "https://",
            adapter,
        )

        session.mount(
            "http://",
            adapter,
        )

        _thread_local.session = session

    return session


# ============================================================
# HTTP GET
# ============================================================

def _get(
    endpoint,
    params,
    retries=HTTP_RETRIES,
):

    url = f"{BASE_URL}/{endpoint}"

    session = _get_session()

    last_exc = None

    for attempt in range(retries):

        try:

            response = session.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            # ------------------------------------------------
            # RATE LIMIT
            # ------------------------------------------------

            if response.status_code == 429:

                wait_header = response.headers.get(
                    "Retry-After"
                )

                if wait_header is not None:

                    try:
                        wait = float(
                            wait_header
                        )

                    except Exception:

                        wait = 2 ** attempt

                else:

                    wait = 2 ** attempt

                time.sleep(
                    min(wait, 10)
                )

                continue

            # ------------------------------------------------
            # SERVER ERROR
            # ------------------------------------------------

            if (
                500
                <= response.status_code
                < 600
            ):

                time.sleep(
                    0.5 * (2 ** attempt)
                )

                continue

            response.raise_for_status()

            return response.json()

        except requests.RequestException as exc:

            last_exc = exc

            if attempt < retries - 1:

                time.sleep(
                    0.5 * (2 ** attempt)
                )

    if last_exc is not None:
        raise last_exc

    raise RuntimeError(
        f"HTTP failed: {endpoint} {params}"
    )


# ============================================================
# SYMBOL
# ============================================================

def _normalize_symbol(symbol):

    if not symbol:
        return "SOLUSDT"

    symbol = str(
        symbol
    ).upper().strip()

    if symbol in COINS:
        return COINS[symbol]

    if symbol.endswith("USDT"):
        return symbol

    return f"{symbol}USDT"


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(
    symbol="SOLUSDT",
):

    symbol = _normalize_symbol(
        symbol
    )

    data = _get(
        "ticker/price",
        {
            "symbol": symbol
        },
    )

    return float(
        data["price"]
    )


# ============================================================
# KLINES FETCH
# ============================================================

def _fetch_klines(
    interval,
    limit,
    symbol,
):

    raw = _get(
        "klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        },
    )

    candles = []

    for item in raw:

        candles.append({
            "open_time": int(
                item[0]
            ),
            "open": float(
                item[1]
            ),
            "high": float(
                item[2]
            ),
            "low": float(
                item[3]
            ),
            "close": float(
                item[4]
            ),
            "volume": float(
                item[5]
            ),
            "close_time": int(
                item[6]
            ),
        })

    return candles


# ============================================================
# KLINES CACHE
# ============================================================

def get_klines(
    interval,
    limit,
    symbol="SOLUSDT",
):

    symbol = _normalize_symbol(
        symbol
    )

    key = (
        symbol,
        interval,
        limit,
    )

    ttl = KLINES_TTL.get(
        interval,
        5,
    )

    now = time.time()

    with _klines_lock:

        cached = _klines_cache.get(
            key
        )

        if cached is not None:

            ts, data = cached

            if (
                now - ts
                < ttl
            ):

                return data

    data = _fetch_klines(
        interval,
        limit,
        symbol,
    )

    with _klines_lock:

        _klines_cache[key] = (
            time.time(),
            data,
        )

    return data


# ============================================================
# CANDLE HELPERS
# ============================================================

def candle_open(c):

    return float(
        c.get(
            "open",
            0,
        )
    )


def candle_high(c):

    return float(
        c.get(
            "high",
            0,
        )
    )


def candle_low(c):

    return float(
        c.get(
            "low",
            0,
        )
    )


def candle_close(c):

    return float(
        c.get(
            "close",
            0,
        )
    )


def candle_body(c):

    return abs(
        candle_close(c)
        -
        candle_open(c)
    )


def candle_range(c):

    return max(
        candle_high(c)
        -
        candle_low(c),
        1e-12,
    )


def body_ratio(c):

    return (
        candle_body(c)
        /
        candle_range(c)
    )


def distance_pct(
    a,
    b,
):

    if b == 0:
        return 999.0

    return (
        abs(a - b)
        /
        abs(b)
        *
        100.0
    )


# ============================================================
# SWINGS
# ============================================================

def _find_swings(
    candles,
    left,
    right,
    kind,
):

    result = []

    if len(candles) < (
        left
        +
        right
        +
        1
    ):

        return result

    for i in range(
        left,
        len(candles) - right,
    ):

        candle = candles[i]

        # ====================================================
        # SWING HIGH
        # ====================================================

        if kind == "high":

            price = candle_high(
                candle
            )

            left_side = candles[
                i - left:i
            ]

            right_side = candles[
                i + 1:
                i + 1 + right
            ]

            left_ok = all(
                price > candle_high(x)
                for x in left_side
            )

            right_ok = all(
                price >= candle_high(x)
                for x in right_side
            )

            if (
                left_ok
                and
                right_ok
            ):

                result.append({
                    "price": price,
                    "index": i,
                    "time": candle.get(
                        "open_time"
                    ),
                    "type": "BSL",
                })

        # ====================================================
        # SWING LOW
        # ====================================================

        else:

            price = candle_low(
                candle
            )

            left_side = candles[
                i - left:i
            ]

            right_side = candles[
                i + 1:
                i + 1 + right
            ]

            left_ok = all(
                price < candle_low(x)
                for x in left_side
            )

            right_ok = all(
                price <= candle_low(x)
                for x in right_side
            )

            if (
                left_ok
                and
                right_ok
            ):

                result.append({
                    "price": price,
                    "index": i,
                    "time": candle.get(
                        "open_time"
                    ),
                    "type": "SSL",
                })

    return result


# ============================================================
# SWING PUBLIC FUNCTIONS
# ============================================================

def find_swing_highs(c):

    return _find_swings(
        c,
        SWING_LEFT,
        SWING_RIGHT,
        "high",
    )


def find_swing_lows(c):

    return _find_swings(
        c,
        SWING_LEFT,
        SWING_RIGHT,
        "low",
    )


def find_swing_highs_15m(c):

    return _find_swings(
        c,
        SWING_LEFT_15M,
        SWING_RIGHT_15M,
        "high",
    )


def find_swing_lows_15m(c):

    return _find_swings(
        c,
        SWING_LEFT_15M,
        SWING_RIGHT_15M,
        "low",
    )


def find_swing_highs_d1(c):

    return _find_swings(
        c,
        SWING_LEFT_D1,
        SWING_RIGHT_D1,
        "high",
    )


def find_swing_lows_d1(c):

    return _find_swings(
        c,
        SWING_LEFT_D1,
        SWING_RIGHT_D1,
        "low",
    )


# ============================================================
# D1 CONTEXT
# ============================================================

def _analyze_d1_context(
    candles_d1,
    price,
):

    empty = {
        "trend": "NEUTRAL",
        "point_a": None,
        "point_b": None,
        "last_swing_high": None,
        "last_swing_low": None,
    }

    if not candles_d1:
        return empty

    if len(candles_d1) < 20:
        return empty

    # Последняя D1 свеча может быть незакрыта.

    confirmed = candles_d1[:-1]

    if len(confirmed) < 15:
        return empty

    swing_highs = find_swing_highs_d1(
        confirmed
    )

    swing_lows = find_swing_lows_d1(
        confirmed
    )

    trend = "NEUTRAL"

    # ========================================================
    # Сильная D1 структура
    # ========================================================

    if (
        len(swing_highs) >= 3
        and
        len(swing_lows) >= 3
    ):

        h1 = swing_highs[-3]["price"]
        h2 = swing_highs[-2]["price"]
        h3 = swing_highs[-1]["price"]

        l1 = swing_lows[-3]["price"]
        l2 = swing_lows[-2]["price"]
        l3 = swing_lows[-1]["price"]

        if (
            h3 > h2 > h1
            and
            l3 > l2 > l1
        ):

            trend = "LONG"

        elif (
            h3 < h2 < h1
            and
            l3 < l2 < l1
        ):

            trend = "SHORT"

    # ========================================================
    # Более мягкий D1 trend
    # ========================================================

    if (
        trend == "NEUTRAL"
        and
        len(swing_highs) >= 2
        and
        len(swing_lows) >= 2
    ):

        if (
            swing_highs[-1]["price"]
            >
            swing_highs[-2]["price"]
            and
            swing_lows[-1]["price"]
            >
            swing_lows[-2]["price"]
        ):

            trend = "LONG"

        elif (
            swing_highs[-1]["price"]
            <
            swing_highs[-2]["price"]
            and
            swing_lows[-1]["price"]
            <
            swing_lows[-2]["price"]
        ):

            trend = "SHORT"

    # ========================================================
    # Point A / Point B
    # ========================================================

    point_a = None
    point_b = None

    cutoff_index = max(
        0,
        len(confirmed)
        -
        D1_POINT_LOOKBACK,
    )

    recent_lows = [
        x
        for x in swing_lows
        if x["index"] >= cutoff_index
    ]

    recent_highs = [
        x
        for x in swing_highs
        if x["index"] >= cutoff_index
    ]

    if trend == "LONG":

        if recent_lows:

            point_a = min(
                x["price"]
                for x in recent_lows
            )

        elif swing_lows:

            point_a = min(
                x["price"]
                for x in swing_lows[-5:]
            )

        highs_above = [
            x["price"]
            for x in swing_highs
            if x["price"] > price
        ]

        if highs_above:

            point_b = min(
                highs_above
            )

    elif trend == "SHORT":

        if recent_highs:

            point_a = max(
                x["price"]
                for x in recent_highs
            )

        elif swing_highs:

            point_a = max(
                x["price"]
                for x in swing_highs[-5:]
            )

        lows_below = [
            x["price"]
            for x in swing_lows
            if x["price"] < price
        ]

        if lows_below:

            point_b = max(
                lows_below
            )

    return {
        "trend": trend,
        "point_a": point_a,
        "point_b": point_b,
        "last_swing_high": (
            swing_highs[-1]["price"]
            if swing_highs
            else None
        ),
        "last_swing_low": (
            swing_lows[-1]["price"]
            if swing_lows
            else None
        ),
    }


# ============================================================
# FVG
# ============================================================

def detect_fvgs(
    candles,
    tf,
    price,
):

    if not candles:
        return []

    if len(candles) < 3:
        return []

    if tf == "5m":

        min_size = (
            FVG_MIN_SIZE_PCT_5M
        )

    else:

        min_size = (
            FVG_MIN_SIZE_PCT_15M
        )

    if len(candles) > FVG_MAX_LOOKBACK:

        lookback = candles[
            -FVG_MAX_LOOKBACK:
        ]

    else:

        lookback = candles

    results = []

    for i in range(
        1,
        len(lookback) - 1,
    ):

        c1 = lookback[i - 1]
        c3 = lookback[i + 1]

        h1 = candle_high(c1)
        l1 = candle_low(c1)

        h3 = candle_high(c3)
        l3 = candle_low(c3)

        # ====================================================
        # BULLISH FVG
        # ====================================================

        if l3 > h1:

            top = l3
            bottom = h1

            fvg_type = "bullish"

        # ====================================================
        # BEARISH FVG
        # ====================================================

        elif h3 < l1:

            top = l1
            bottom = h3

            fvg_type = "bearish"

        else:

            continue

        if (
            top <= bottom
            or
            bottom <= 0
        ):

            continue

        size_pct = (
            (
                top
                -
                bottom
            )
            /
            bottom
            *
            100.0
        )

        if size_pct < min_size:
            continue

        # ====================================================
        # FILLED CHECK
        # ====================================================

        filled = False

        for j in range(
            i + 2,
            len(lookback),
        ):

            ch = candle_high(
                lookback[j]
            )

            cl = candle_low(
                lookback[j]
            )

            if fvg_type == "bullish":

                if cl <= bottom:

                    filled = True
                    break

            else:

                if ch >= top:

                    filled = True
                    break

        if filled:
            continue

        results.append({
            "type": fvg_type,
            "tf": tf,
            "top": round(
                top,
                8,
            ),
            "bottom": round(
                bottom,
                8,
            ),
            "middle": round(
                (
                    top
                    +
                    bottom
                )
                /
                2,
                8,
            ),
            "open_time": (
                lookback[i].get(
                    "open_time"
                )
            ),
            "size_pct": round(
                size_pct,
                4,
            ),
            "distance_pct": round(
                distance_pct(
                    price,
                    (
                        top
                        +
                        bottom
                    )
                    /
                    2,
                ),
                4,
            ),
        })

    # Самые свежие FVG сначала.

    results.sort(
        key=lambda x:
        x.get(
            "open_time",
            0,
        ),
        reverse=True,
    )

    return results[
        :FVG_MAX_ZONES_PER_TF
    ]


def collect_fvgs(
    candles_5m,
    candles_15m,
    price,
):

    fvgs = []

    fvgs.extend(
        detect_fvgs(
            candles_5m,
            "5m",
            price,
        )
    )

    fvgs.extend(
        detect_fvgs(
            candles_15m,
            "15m",
            price,
        )
    )

    return fvgs


# ============================================================
# CLUSTER LEVELS
# ============================================================

def cluster_levels(
    levels,
):

    if not levels:
        return []

    levels = sorted(
        levels,
        key=lambda x:
        x["price"],
    )

    clusters = []

    for level in levels:

        if not clusters:

            clusters.append([
                level
            ])

            continue

        current = clusters[-1]

        avg_price = (
            sum(
                x["price"]
                for x in current
            )
            /
            len(current)
        )

        if (
            distance_pct(
                level["price"],
                avg_price,
            )
            <= CLUSTER_DISTANCE_PCT
        ):

            current.append(
                level
            )

        else:

            clusters.append([
                level
            ])

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

        times = [
            x.get(
                "time",
                0,
            )
            for x in cluster
        ]

        result.append({
            "price": (
                sum(prices)
                /
                len(prices)
            ),
            "touches": len(
                cluster
            ),
            "first_index": min(
                indices
            ),
            "last_index": max(
                indices
            ),
            "first_time": min(
                times
            ),
            "last_time": max(
                times
            ),
        })

    return result


# ============================================================
# FRESHNESS
# ============================================================

def freshness_score(
    level,
    candles,
):

    last_index = int(
        level.get(
            "last_index",
            0,
        )
    )

    age = (
        len(candles)
        -
        1
        -
        last_index
    )

    # --------------------------------------------------------
    # Свежесть влияет только на score.
    #
    # Она НИКОГДА не удаляет Major.
    # --------------------------------------------------------

    if age <= 3:
        return 30.0

    if age <= 10:
        return 25.0

    if age <= 30:
        return 20.0

    if age <= 60:
        return 15.0

    if age <= 100:
        return 10.0

    if age <= 180:
        return 5.0

    return 2.0


# ============================================================
# LOCAL TOUCHES
# ============================================================

def count_local_touches(
    level_price,
    candles,
):

    touches = 0

    if not candles:
        return 0

    for candle in candles:

        high = candle_high(
            candle
        )

        low = candle_low(
            candle
        )

        # ----------------------------------------------------
        # Реальное пересечение уровня.
        # ----------------------------------------------------

        if (
            low
            <= level_price
            <= high
        ):

            touches += 1

            continue

        # ----------------------------------------------------
        # Близость high.
        # ----------------------------------------------------

        if (
            distance_pct(
                high,
                level_price,
            )
            <= LOCAL_TOUCH_DISTANCE_PCT
        ):

            touches += 1

            continue

        # ----------------------------------------------------
        # Близость low.
        # ----------------------------------------------------

        if (
            distance_pct(
                low,
                level_price,
            )
            <= LOCAL_TOUCH_DISTANCE_PCT
        ):

            touches += 1

    return touches


# ============================================================
# SWEPT LEVEL
# ============================================================

def level_has_been_swept(
    level_price,
    level_type,
    candles,
    lookback,
):

    if not candles:
        return False

    if lookback <= 0:
        return False

    recent = candles[
        -lookback:
    ]

    for candle in recent:

        high = candle_high(
            candle
        )

        low = candle_low(
            candle
        )

        close = candle_close(
            candle
        )

        # ====================================================
        # BSL
        # ====================================================

        if level_type == "BSL":

            if (
                high > level_price
                and
                close < level_price
            ):

                depth_pct = (
                    (
                        high
                        -
                        level_price
                    )
                    /
                    level_price
                    *
                    100.0
                )

                if (
                    depth_pct
                    >= SWEPT_MIN_DEPTH_PCT
                ):

                    return True

        # ====================================================
        # SSL
        # ====================================================

        elif level_type == "SSL":

            if (
                low < level_price
                and
                close > level_price
            ):

                depth_pct = (
                    (
                        level_price
                        -
                        low
                    )
                    /
                    level_price
                    *
                    100.0
                )

                if (
                    depth_pct
                    >= SWEPT_MIN_DEPTH_PCT
                ):

                    return True

    return False


# ============================================================
# STRENGTH
# ============================================================

def calculate_strength(
    level,
    candles,
    candles_15m,
    max_age=None,
    base=45.0,
):

    score = base

    touches = int(
        level.get(
            "touches",
            1,
        )
    )

    # ========================================================
    # CLUSTER
    # ========================================================

    if touches >= 2:
        score += 8

    if touches >= 3:
        score += 7

    if touches >= 4:
        score += 5

    if touches >= 5:
        score += 5

    # ========================================================
    # FRESHNESS
    # ========================================================

    score += freshness_score(
        level,
        candles,
    )

    # ========================================================
    # LOCAL INTERACTION
    # ========================================================

    local_touches = count_local_touches(
        level["price"],
        candles_15m,
    )

    if local_touches >= 2:
        score += 4

    if local_touches >= 4:
        score += 4

    if local_touches >= 7:
        score += 3

    return min(
        round(
            score,
            2,
        ),
        100.0,
    )


# ============================================================
# SELECT STRUCTURAL LEVELS
# ============================================================

def _select_zones(
    levels,
    price,
    candles_ref,
    candles_15m,
    level_type,
    min_strength=None,
    max_age=None,
    sweep_lookback=30,
    source="1H",
):

    candidates = []

    for level in levels:

        level_price = float(
            level.get(
                "price",
                0,
            )
        )

        if level_price <= 0:
            continue

        # ====================================================
        # BSL
        # ====================================================

        if level_type == "BSL":

            if level_price <= price:
                continue

        # ====================================================
        # SSL
        # ====================================================

        elif level_type == "SSL":

            if level_price >= price:
                continue

        else:

            continue

        distance = distance_pct(
            price,
            level_price,
        )

        # ====================================================
        # AGE
        # ====================================================

        age = (
            len(candles_ref)
            -
            1
            -
            int(
                level.get(
                    "last_index",
                    0,
                )
            )
        )

        # ====================================================
        # SWEPT
        # ====================================================

        swept = level_has_been_swept(
            level_price,
            level_type,
            candles_ref,
            sweep_lookback,
        )

        if swept:
            continue

        # ====================================================
        # STRENGTH
        #
        # IMPORTANT:
        #
        # strength НЕ является gate.
        # ====================================================

        strength = calculate_strength(
            level,
            candles_ref,
            candles_15m,
            max_age,
        )

        # ====================================================
        # ZONE
        # ====================================================

        zone_low = (
            level_price
            *
            (
                1
                -
                ZONE_WIDTH_PCT
                /
                100.0
            )
        )

        zone_high = (
            level_price
            *
            (
                1
                +
                ZONE_WIDTH_PCT
                /
                100.0
            )
        )

        candidates.append({
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

            "age_1h": max(
                0,
                age,
            ),

            "source": source,

            "status": "FRESH",

            "swept": False,
            "taken": False,
            "used": False,
            "consumed": False,
        })

    # ========================================================
    # PRIORITY
    #
    # 1. Ближе к цене
    # 2. Более свежий
    # 3. Более сильный
    #
    # Таким образом свежая актуальная ликвидность
    # не проигрывает автоматически старому сильному уровню.
    # ========================================================

    candidates.sort(
        key=lambda x: (
            x["distance_pct"],
            x["age_1h"],
            -x["strength"],
        )
    )

    return candidates


# ============================================================
# ROUND NUMBERS
# ============================================================

def _round_step(
    price,
):

    if price <= 0:
        return 1.0

    exp = (
        math.floor(
            math.log10(price)
        )
        -
        1
    )

    return 10 ** exp


def find_round_number_levels(
    price,
    side,
    max_count=ROUND_MAX_COUNT,
    max_distance_pct=ROUND_MAX_DISTANCE_PCT,
):

    if price <= 0:
        return []

    step = _round_step(
        price
    )

    if step <= 0:
        return []

    results = []

    # ========================================================
    # BSL
    # ========================================================

    if side == "BSL":

        first = (
            math.ceil(
                price / step
            )
            *
            step
        )

        if first <= price:
            first += step

        current = first

        for _ in range(
            max_count
        ):

            dist = (
                current
                -
                price
            ) / price * 100.0

            if (
                dist
                >
                max_distance_pct
            ):

                break

            zl = (
                current
                *
                (
                    1
                    -
                    ZONE_WIDTH_PCT
                    /
                    100.0
                )
            )

            zh = (
                current
                *
                (
                    1
                    +
                    ZONE_WIDTH_PCT
                    /
                    100.0
                )
            )

            results.append({
                "price": round(
                    current,
                    8,
                ),
                "zone_low": round(
                    zl,
                    8,
                ),
                "zone_high": round(
                    zh,
                    8,
                ),
                "type": "BSL",
                "strength": ROUND_STRENGTH,
                "touches": 1,
                "distance_pct": round(
                    dist,
                    4,
                ),
                "age_1h": 0,
                "source": "ROUND",
                "status": "FRESH",
                "swept": False,
                "taken": False,
                "used": False,
                "consumed": False,
            })

            current += step

    # ========================================================
    # SSL
    # ========================================================

    else:

        first = (
            math.floor(
                price / step
            )
            *
            step
        )

        if first >= price:
            first -= step

        current = first

        for _ in range(
            max_count
        ):

            if current <= 0:
                break

            dist = (
                price
                -
                current
            ) / price * 100.0

            if (
                dist
                >
                max_distance_pct
            ):

                break

            zl = (
                current
                *
                (
                    1
                    -
                    ZONE_WIDTH_PCT
                    /
                    100.0
                )
            )

            zh = (
                current
                *
                (
                    1
                    +
                    ZONE_WIDTH_PCT
                    /
                    100.0
                )
            )

            results.append({
                "price": round(
                    current,
                    8,
                ),
                "zone_low": round(
                    zl,
                    8,
                ),
                "zone_high": round(
                    zh,
                    8,
                ),
                "type": "SSL",
                "strength": ROUND_STRENGTH,
                "touches": 1,
                "distance_pct": round(
                    dist,
                    4,
                ),
                "age_1h": 0,
                "source": "ROUND",
                "status": "FRESH",
                "swept": False,
                "taken": False,
                "used": False,
                "consumed": False,
            })

            current -= step

    return results


# ============================================================
# MERGE LEVELS
# ============================================================

def _merge_sources(
    sources,
    limit,
):

    merged = []

    seen_prices = []

    for source in sources:

        for level in source:

            if len(merged) >= limit:
                break

            price = float(
                level.get(
                    "price",
                    0,
                )
            )

            if price <= 0:
                continue

            duplicate = any(
                distance_pct(
                    price,
                    p,
                )
                < 0.05
                for p in seen_prices
            )

            if duplicate:
                continue

            merged.append(
                level
            )

            seen_prices.append(
                price
            )

        if len(merged) >= limit:
            break

    merged.sort(
        key=lambda x:
        x.get(
            "distance_pct",
            999.0,
        )
    )

    return merged


# ============================================================
# BUILD MAJOR LIQUIDITY
# ============================================================
#
# ВАЖНЕЙШЕЕ ИЗМЕНЕНИЕ 7.5:
#
# major_liquidity содержит ТОЛЬКО 1H.
#
# 15M и ROUND возвращаются отдельно:
#
# {
#     "BSL": [...1H Major...],
#     "SSL": [...1H Major...],
#     "BSL_15M": [...],
#     "SSL_15M": [...],
#     "BSL_ROUND": [...],
#     "SSL_ROUND": [...]
# }
#
# Это предотвращает ситуацию, когда 15M или round level
# становится главным уровнем стратегии.
#
# ============================================================

def _build_major_liquidity(
    candles_1h,
    candles_15m,
    price,
):

    empty = {
        "BSL": [],
        "SSL": [],

        "BSL_15M": [],
        "SSL_15M": [],

        "BSL_ROUND": [],
        "SSL_ROUND": [],
    }

    if not candles_1h:
        return empty

    # Последняя 1H свеча может быть незакрыта.

    confirmed_1h = candles_1h[:-1]

    if len(confirmed_1h) < 10:
        return empty

    # ========================================================
    # 1H STRUCTURE
    # ========================================================

    swing_highs_1h = find_swing_highs(
        confirmed_1h
    )

    swing_lows_1h = find_swing_lows(
        confirmed_1h
    )

    clustered_highs = cluster_levels(
        swing_highs_1h
    )

    clustered_lows = cluster_levels(
        swing_lows_1h
    )

    # ========================================================
    # 1H MAJOR BSL
    # ========================================================

    bsl_1h = _select_zones(
        clustered_highs,
        price,
        confirmed_1h,
        candles_15m,
        "BSL",
        min_strength=None,
        max_age=None,
        sweep_lookback=SWEEP_RECENT_LOOKBACK_1H,
        source="1H",
    )

    # ========================================================
    # 1H MAJOR SSL
    # ========================================================

    ssl_1h = _select_zones(
        clustered_lows,
        price,
        confirmed_1h,
        candles_15m,
        "SSL",
        min_strength=None,
        max_age=None,
        sweep_lookback=SWEEP_RECENT_LOOKBACK_1H,
        source="1H",
    )

    # ========================================================
    # MAJOR ONLY
    #
    # Никаких 15M / ROUND в этом списке.
    # ========================================================

    bsl_major = bsl_1h[
        :MAX_LEVELS_PER_SIDE
    ]

    ssl_major = ssl_1h[
        :MAX_LEVELS_PER_SIDE
    ]

    # ========================================================
    # 15M CONTEXT
    # ========================================================

    bsl_15m = []
    ssl_15m = []

    if candles_15m:

        confirmed_15m = candles_15m[:-1]

        if len(confirmed_15m) >= 10:

            swing_highs_15m = (
                find_swing_highs_15m(
                    confirmed_15m
                )
            )

            swing_lows_15m = (
                find_swing_lows_15m(
                    confirmed_15m
                )
            )

            clustered_highs_15m = (
                cluster_levels(
                    swing_highs_15m
                )
            )

            clustered_lows_15m = (
                cluster_levels(
                    swing_lows_15m
                )
            )

            bsl_15m = _select_zones(
                clustered_highs_15m,
                price,
                confirmed_15m,
                confirmed_15m,
                "BSL",
                min_strength=None,
                max_age=None,
                sweep_lookback=SWEEP_RECENT_LOOKBACK_15M,
                source="15M",
            )

            ssl_15m = _select_zones(
                clustered_lows_15m,
                price,
                confirmed_15m,
                confirmed_15m,
                "SSL",
                min_strength=None,
                max_age=None,
                sweep_lookback=SWEEP_RECENT_LOOKBACK_15M,
                source="15M",
            )

            bsl_15m = bsl_15m[
                :MAX_15M_LEVELS_PER_SIDE
            ]

            ssl_15m = ssl_15m[
                :MAX_15M_LEVELS_PER_SIDE
            ]

    # ========================================================
    # ROUND NUMBERS
    # ========================================================
    #
    # Только контекст.
    # НЕ Major.
    # ========================================================

    bsl_round = (
        find_round_number_levels(
            price,
            "BSL",
        )
    )

    ssl_round = (
        find_round_number_levels(
            price,
            "SSL",
        )
    )

    # ========================================================
    # RETURN
    # ========================================================

    return {
        # ----------------------------------------------------
        # ONLY 1H MAJOR
        # ----------------------------------------------------

        "BSL": bsl_major,
        "SSL": ssl_major,

        # ----------------------------------------------------
        # 15M CONTEXT
        # ----------------------------------------------------

        "BSL_15M": bsl_15m,
        "SSL_15M": ssl_15m,

        # ----------------------------------------------------
        # ROUND CONTEXT
        # ----------------------------------------------------

        "BSL_ROUND": bsl_round,
        "SSL_ROUND": ssl_round,
    }


# ============================================================
# FIND MAJOR LIQUIDITY
# ============================================================

def find_major_liquidity(
    candles_1h,
    price,
    max_levels=12,
    candles_15m=None,
    candles_5m=None,
    candles_1m=None,
):

    if candles_15m is None:
        candles_15m = []

    liquidity = _build_major_liquidity(
        candles_1h,
        candles_15m,
        price,
    )

    # ========================================================
    # IMPORTANT
    #
    # Возвращаем только BSL + SSL.
    #
    # То есть только настоящие 1H Major.
    # ========================================================

    levels = (
        liquidity["BSL"]
        +
        liquidity["SSL"]
    )

    # Ближайшие уровни первыми.

    levels.sort(
        key=lambda x: (
            x.get(
                "distance_pct",
                999.0,
            ),
            x.get(
                "age_1h",
                999999,
            ),
            -x.get(
                "strength",
                0,
            ),
        )
    )

    return levels[
        :max_levels
    ]


# ============================================================
# TARGET LIQUIDITY
# ============================================================

def get_target_liquidity(
    major_liquidity,
    direction,
    entry,
):

    if not major_liquidity:
        return None

    # ========================================================
    # DICT
    # ========================================================

    if isinstance(
        major_liquidity,
        dict,
    ):

        expected_side = (
            "BSL"
            if direction == "LONG"
            else "SSL"
        )

        levels = major_liquidity.get(
            expected_side,
            [],
        )

        candidates = []

        for x in levels:

            level_price = float(
                x.get(
                    "price",
                    0,
                )
            )

            if direction == "LONG":

                if level_price <= entry:
                    continue

            else:

                if level_price >= entry:
                    continue

            if x.get(
                "swept",
                False,
            ):

                continue

            if x.get(
                "taken",
                False,
            ):

                continue

            if x.get(
                "used",
                False,
            ):

                continue

            if x.get(
                "consumed",
                False,
            ):

                continue

            candidates.append(
                x
            )

    # ========================================================
    # LIST
    # ========================================================

    else:

        expected_type = (
            "BSL"
            if direction == "LONG"
            else "SSL"
        )

        candidates = []

        for x in major_liquidity:

            if x.get(
                "type"
            ) != expected_type:

                continue

            level_price = float(
                x.get(
                    "price",
                    0,
                )
            )

            if direction == "LONG":

                if level_price <= entry:
                    continue

            else:

                if level_price >= entry:
                    continue

            if x.get(
                "swept",
                False,
            ):

                continue

            if x.get(
                "taken",
                False,
            ):

                continue

            if x.get(
                "used",
                False,
            ):

                continue

            if x.get(
                "consumed",
                False,
            ):

                continue

            candidates.append(
                x
            )

    if not candidates:
        return None

    # Ближайшая unswept Major liquidity.

    candidates.sort(
        key=lambda x:
        abs(
            float(
                x["price"]
            )
            -
            entry
        )
    )

    return candidates[0]


# ============================================================
# SWEEP DETECTOR
# ============================================================

def detect_sweep(
    candles_1h,
    price,
    direction,
    levels=None,
):

    if direction not in {
        "LONG",
        "SHORT",
    }:

        return None

    if not candles_1h:
        return None

    normalized = []

    # ========================================================
    # DICT
    # ========================================================

    if isinstance(
        levels,
        dict,
    ):

        for side in (
            "BSL",
            "SSL",
        ):

            for level in levels.get(
                side,
                [],
            ):

                if not isinstance(
                    level,
                    dict,
                ):

                    continue

                item = dict(
                    level
                )

                item.setdefault(
                    "type",
                    side,
                )

                normalized.append(
                    item
                )

    # ========================================================
    # LIST
    # ========================================================

    elif isinstance(
        levels,
        list,
    ):

        for level in levels:

            if not isinstance(
                level,
                dict,
            ):

                continue

            normalized.append(
                dict(level)
            )

    expected_type = (
        "SSL"
        if direction == "LONG"
        else "BSL"
    )

    # ========================================================
    # ONLY FRESH MAJOR
    # ========================================================

    valid_levels = [
        x
        for x in normalized
        if (
            x.get(
                "type"
            )
            ==
            expected_type
            and
            not x.get(
                "swept",
                False,
            )
            and
            not x.get(
                "taken",
                False,
            )
            and
            not x.get(
                "used",
                False,
            )
            and
            not x.get(
                "consumed",
                False,
            )
        )
    ]

    if not valid_levels:
        return None

    # Последняя 1H свеча незакрыта.

    confirmed = candles_1h[:-1]

    if not confirmed:
        return None

    recent = confirmed[
        -MAX_SWEEP_LOOKBACK_1H:
    ]

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        for candle in reversed(
            recent
        ):

            low = candle_low(
                candle
            )

            close = candle_close(
                candle
            )

            op = candle_open(
                candle
            )

            candidates = []

            for level in valid_levels:

                lp = float(
                    level.get(
                        "price",
                        0,
                    )
                )

                if lp <= 0:
                    continue

                if lp >= price:
                    continue

                if low >= lp:
                    continue

                depth = (
                    (
                        lp
                        -
                        low
                    )
                    /
                    lp
                    *
                    100.0
                )

                if (
                    depth
                    <
                    MIN_SWEEP_DEPTH_PCT
                ):

                    continue

                # Body reclaim.

                if close <= lp:
                    continue

                # Бычья свеча.

                if close <= op:
                    continue

                candidates.append(
                    (
                        abs(
                            lp
                            -
                            low
                        ),
                        level,
                        depth,
                    )
                )

            if candidates:

                candidates.sort(
                    key=lambda x:
                    x[0]
                )

                _, level, depth = (
                    candidates[0]
                )

                return {
                    "swept": True,
                    "direction": "LONG",
                    "liquidity_type": "SSL",
                    "level": float(
                        level["price"]
                    ),
                    "extreme": low,
                    "price": low,
                    "depth_pct": round(
                        depth,
                        4,
                    ),
                    "open_time": candle.get(
                        "open_time"
                    ),
                    "strength": float(
                        level.get(
                            "strength",
                            0,
                        )
                    ),
                    "touches": int(
                        level.get(
                            "touches",
                            1,
                        )
                    ),
                    "source": level.get(
                        "source",
                        "1H",
                    ),
                }

    # ========================================================
    # SHORT
    # ========================================================

    if direction == "SHORT":

        for candle in reversed(
            recent
        ):

            high = candle_high(
                candle
            )

            close = candle_close(
                candle
            )

            op = candle_open(
                candle
            )

            candidates = []

            for level in valid_levels:

                lp = float(
                    level.get(
                        "price",
                        0,
                    )
                )

                if lp <= 0:
                    continue

                if lp <= price:
                    continue

                if high <= lp:
                    continue

                depth = (
                    (
                        high
                        -
                        lp
                    )
                    /
                    lp
                    *
                    100.0
                )

                if (
                    depth
                    <
                    MIN_SWEEP_DEPTH_PCT
                ):

                    continue

                # Body reclaim.

                if close >= lp:
                    continue

                # Медвежья свеча.

                if close >= op:
                    continue

                candidates.append(
                    (
                        abs(
                            high
                            -
                            lp
                        ),
                        level,
                        depth,
                    )
                )

            if candidates:

                candidates.sort(
                    key=lambda x:
                    x[0]
                )

                _, level, depth = (
                    candidates[0]
                )

                return {
                    "swept": True,
                    "direction": "SHORT",
                    "liquidity_type": "BSL",
                    "level": float(
                        level["price"]
                    ),
                    "extreme": high,
                    "price": high,
                    "depth_pct": round(
                        depth,
                        4,
                    ),
                    "open_time": candle.get(
                        "open_time"
                    ),
                    "strength": float(
                        level.get(
                            "strength",
                            0,
                        )
                    ),
                    "touches": int(
                        level.get(
                            "touches",
                            1,
                        )
                    ),
                    "source": level.get(
                        "source",
                        "1H",
                    ),
                }

    return None


# ============================================================
# MARKET DATA
# ============================================================

def get_market_data(
    symbol="SOLUSDT",
):

    symbol = _normalize_symbol(
        symbol
    )

    # ========================================================
    # PARALLEL DOWNLOAD
    # ========================================================

    with ThreadPoolExecutor(
        max_workers=5
    ) as ex:

        f_d1 = ex.submit(
            get_klines,
            "1d",
            LOOKBACK_D1,
            symbol,
        )

        f_1h = ex.submit(
            get_klines,
            "1h",
            LOOKBACK_1H,
            symbol,
        )

        f_15m = ex.submit(
            get_klines,
            "15m",
            LOOKBACK_15M,
            symbol,
        )

        f_5m = ex.submit(
            get_klines,
            "5m",
            LOOKBACK_5M,
            symbol,
        )

        f_1m = ex.submit(
            get_klines,
            "1m",
            LOOKBACK_1M,
            symbol,
        )

        candles_d1 = f_d1.result()
        candles_1h = f_1h.result()
        candles_15m = f_15m.result()
        candles_5m = f_5m.result()
        candles_1m = f_1m.result()

    # ========================================================
    # CURRENT PRICE
    # ========================================================

    try:

        price = get_current_price(
            symbol
        )

    except Exception:

        price = (
            float(
                candles_1m[-1]["close"]
            )
            if candles_1m
            else 0.0
        )

    # ========================================================
    # LIQUIDITY
    # ========================================================

    major_liquidity = (
        _build_major_liquidity(
            candles_1h,
            candles_15m,
            price,
        )
    )

    # ========================================================
    # D1 CONTEXT
    # ========================================================

    d1_context = (
        _analyze_d1_context(
            candles_d1,
            price,
        )
    )

    # ========================================================
    # FVG
    # ========================================================

    fvgs = collect_fvgs(
        candles_5m,
        candles_15m,
        price,
    )

    # ========================================================
    # RETURN
    # ========================================================

    return {
        "symbol": symbol,

        "price": price,

        "candles_d1": candles_d1,

        "candles_1h": candles_1h,

        "candles_15m": candles_15m,

        "candles_5m": candles_5m,

        "candles_1m": candles_1m,

        "candles": {
            "1d": candles_d1,
            "1h": candles_1h,
            "15m": candles_15m,
            "5m": candles_5m,
            "1m": candles_1m,
        },

        # ----------------------------------------------------
        # MAIN
        # ----------------------------------------------------

        "major_liquidity": major_liquidity,

        # ----------------------------------------------------
        # D1 compatibility
        # ----------------------------------------------------

        "d1_context": d1_context,

        # ----------------------------------------------------
        # FVG
        # ----------------------------------------------------

        "fvgs": fvgs,

        "updated_at": time.time(),

        "market_version": MARKET_VERSION,
    }


# ============================================================
# GET MAJOR LIQUIDITY
# ============================================================

def get_major_liquidity(
    price=None,
    symbol="SOLUSDT",
):

    symbol = _normalize_symbol(
        symbol
    )

    candles_1h = get_klines(
        "1h",
        LOOKBACK_1H,
        symbol,
    )

    candles_15m = get_klines(
        "15m",
        LOOKBACK_15M,
        symbol,
    )

    if price is None:

        price = get_current_price(
            symbol
        )

    return _build_major_liquidity(
        candles_1h,
        candles_15m,
        price,
    )


# ============================================================
# SNAPSHOT
# ============================================================

def market_snapshot(
    symbol="SOLUSDT",
):

    return get_market_data(
        symbol
    )


# ============================================================
# FORMAT LIQUIDITY
# ============================================================

def format_major_liquidity(
    data,
):

    symbol = data.get(
        "symbol",
        "SOLUSDT",
    )

    price = float(
        data.get(
            "price",
            0,
        )
    )

    liquidity = data.get(
        "major_liquidity",
        {},
    )

    d1 = data.get(
        "d1_context",
        {},
    )

    fvgs = data.get(
        "fvgs",
        [],
    )

    lines = [
        f"💠 {symbol}",
        f"💰 Цена: ${price:.6f}",
        "",
        "📊 MAJOR LIQUIDITY — 1H",
        "",
        "📅 D1 CONTEXT",
        f"Trend: {d1.get('trend', 'NEUTRAL')}",
        f"A: {d1.get('point_a')}  B: {d1.get('point_b')}",
        "",
    ]

    # ========================================================
    # BSL MAJOR
    # ========================================================

    lines.append(
        "🔴 BSL — 1H MAJOR"
    )

    bsl = liquidity.get(
        "BSL",
        [],
    )

    if not bsl:

        lines.append(
            "— нет"
        )

    else:

        for i, lvl in enumerate(
            bsl,
            1,
        ):

            lines.append(
                f"{i}. "
                f"${lvl['price']:.6f} "
                f"• {lvl['distance_pct']:.2f}% "
                f"• S{lvl['strength']:.0f} "
                f"• {lvl.get('source', '?')} "
                f"• age {lvl.get('age_1h', 0)}"
            )

    # ========================================================
    # SSL MAJOR
    # ========================================================

    lines.append("")

    lines.append(
        "🟢 SSL — 1H MAJOR"
    )

    ssl = liquidity.get(
        "SSL",
        [],
    )

    if not ssl:

        lines.append(
            "— нет"
        )

    else:

        for i, lvl in enumerate(
            ssl,
            1,
        ):

            lines.append(
                f"{i}. "
                f"${lvl['price']:.6f} "
                f"• {lvl['distance_pct']:.2f}% "
                f"• S{lvl['strength']:.0f} "
                f"• {lvl.get('source', '?')} "
                f"• age {lvl.get('age_1h', 0)}"
            )

    # ========================================================
    # 15M CONTEXT
    # ========================================================

    lines.append("")

    lines.append(
        "📐 15M CONTEXT"
    )

    bsl_15m = liquidity.get(
        "BSL_15M",
        [],
    )

    ssl_15m = liquidity.get(
        "SSL_15M",
        [],
    )

    if not bsl_15m:

        lines.append(
            "BSL: —"
        )

    else:

        lines.append(
            "BSL:"
        )

        for lvl in bsl_15m[:4]:

            lines.append(
                f"  ${lvl['price']:.6f} "
                f"• {lvl['distance_pct']:.2f}%"
            )

    if not ssl_15m:

        lines.append(
            "SSL: —"
        )

    else:

        lines.append(
            "SSL:"
        )

        for lvl in ssl_15m[:4]:

            lines.append(
                f"  ${lvl['price']:.6f} "
                f"• {lvl['distance_pct']:.2f}%"
            )

    # ========================================================
    # ROUND CONTEXT
    # ========================================================

    lines.append("")

    lines.append(
        "🔢 ROUND CONTEXT"
    )

    bsl_round = liquidity.get(
        "BSL_ROUND",
        [],
    )

    ssl_round = liquidity.get(
        "SSL_ROUND",
        [],
    )

    if bsl_round:

        lines.append(
            "BSL:"
        )

        for lvl in bsl_round[:3]:

            lines.append(
                f"  ${lvl['price']:.6f}"
            )

    if ssl_round:

        lines.append(
            "SSL:"
        )

        for lvl in ssl_round[:3]:

            lines.append(
                f"  ${lvl['price']:.6f}"
            )

    # ========================================================
    # FVG
    # ========================================================

    lines.append("")

    lines.append(
        "⚡ FVG (IMBALANCE)"
    )

    if not fvgs:

        lines.append(
            "— нет незакрытых"
        )

    else:

        for f in fvgs:

            icon = (
                "🟢"
                if f["type"] == "bullish"
                else "🔴"
            )

            lines.append(
                f"{icon} "
                f"{f['tf'].upper()} "
                f"${f['bottom']:.6f}"
                f"–"
                f"${f['top']:.6f} "
                f"({f['size_pct']:.3f}%) "
                f"• "
                f"{f.get('distance_pct', 0):.2f}%"
            )

    return "\n".join(
        lines
    )


# ============================================================
# DEBUG SYMBOL
# ============================================================

def debug_symbol(
    symbol,
):

    symbol = _normalize_symbol(
        symbol
    )

    print("")
    print("=" * 70)

    print(
        f"TradeMind Market {MARKET_VERSION}"
    )

    print(
        f"Symbol: {symbol}"
    )

    print("=" * 70)

    try:

        data = get_market_data(
            symbol
        )

        print(
            format_major_liquidity(
                data
            )
        )

        print("")
        print(
            "1H SWINGS:"
        )

        confirmed_1h = (
            data["candles_1h"][:-1]
        )

        highs = find_swing_highs(
            confirmed_1h
        )

        lows = find_swing_lows(
            confirmed_1h
        )

        print(
            f"BSL swings: {len(highs)}"
        )

        for x in highs[-15:]:

            print(
                f"  BSL "
                f"${x['price']:.6f} "
                f"index={x['index']}"
            )

        print(
            f"SSL swings: {len(lows)}"
        )

        for x in lows[-15:]:

            print(
                f"  SSL "
                f"${x['price']:.6f} "
                f"index={x['index']}"
            )

        print("")
        print(
            "MAJOR 1H LEVELS:"
        )

        major = data.get(
            "major_liquidity",
            {},
        )

        for side in (
            "BSL",
            "SSL",
        ):

            print(
                f"{side}:"
            )

            for lvl in major.get(
                side,
                [],
            ):

                print(
                    f"  ${lvl['price']:.6f} "
                    f"distance={lvl['distance_pct']:.3f}% "
                    f"strength={lvl['strength']:.1f} "
                    f"age={lvl['age_1h']} "
                    f"source={lvl['source']}"
                )

        print("")
        print(
            "15M CONTEXT:"
        )

        for side in (
            "BSL_15M",
            "SSL_15M",
        ):

            print(
                f"{side}:"
            )

            for lvl in major.get(
                side,
                [],
            ):

                print(
                    f"  ${lvl['price']:.6f} "
                    f"distance={lvl['distance_pct']:.3f}%"
                )

        print("")
        print(
            "LATEST CANDLES:"
        )

        for tf, candles in [
            (
                "1H",
                data["candles_1h"],
            ),
            (
                "15M",
                data["candles_15m"],
            ),
            (
                "5M",
                data["candles_5m"],
            ),
            (
                "1M",
                data["candles_1m"],
            ),
        ]:

            if not candles:
                continue

            last = candles[-1]

            print(
                f"{tf}: "
                f"open=${last['open']:.6f} "
                f"high=${last['high']:.6f} "
                f"low=${last['low']:.6f} "
                f"close=${last['close']:.6f} "
                f"time={last['open_time']}"
            )

        print("")
        print(
            "FVG COUNT:",
            len(
                data.get(
                    "fvgs",
                    [],
                )
            )
        )

        print("")
        print(
            "MARKET DATA OK"
        )

    except Exception as error:

        print(
            f"MARKET ERROR: {error}"
        )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print(
        f"TradeMind market.py "
        f"{MARKET_VERSION}"
    )

    print(
        f"Supported coins: "
        f"{len(COINS)}"
    )

    print("")

    debug_symbol(
        "SOL"
    )