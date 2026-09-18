# ============================================================
# TradeMind 7.2
# market.py
#
# Binance Spot market data
#
# D1  -> Trend + Point A + Point B (согласно видео)
# 1H  -> Main Structure -> Major Liquidity -> Sweep
# 15M -> Confirmation + fallback liquidity
# 5M  -> ILM
# 1M  -> Local context
#
# Изменения vs 7.1:
# - Добавлен D1 анализ: тренд, точка A (старт движения),
#   точка B (цель — противоположная ликвидность).
# - d1_context возвращается в market_data.
# - D1 свечи кэшируются с TTL 300 сек.
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

MARKET_VERSION = "7.2"


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
# KLINES CACHE
# ============================================================

KLINES_TTL = {
    "1d": 300,
    "1h": 60,
    "15m": 30,
    "5m": 15,
    "1m": 5,
}

_klines_cache: Dict[Tuple[str, str, int], Tuple[float, List[Dict[str, Any]]]] = {}
_klines_lock = threading.RLock()


# ============================================================
# SWING SETTINGS
# ============================================================

SWING_LEFT = 2
SWING_RIGHT = 1

SWING_LEFT_15M = 2
SWING_RIGHT_15M = 1

SWING_LEFT_D1 = 3
SWING_RIGHT_D1 = 3


# ============================================================
# LIQUIDITY SETTINGS
# ============================================================

CLUSTER_DISTANCE_PCT = 0.15

ZONE_WIDTH_PCT = 0.20

MIN_ZONE_GAP_PCT = 0.70

MIN_MAJOR_DISTANCE_PCT = 0.15

MIN_MAJOR_STRENGTH = 58.0

MIN_MINOR_STRENGTH = 45.0

MIN_ROUND_STRENGTH = 50.0

MAX_LEVELS_PER_SIDE = 4

MAX_TOTAL_LEVELS = 8

MIN_LEVELS_PER_SIDE_1H = 2


# ============================================================
# ROUND NUMBERS
# ============================================================

ROUND_MAX_COUNT = 5
ROUND_MAX_DISTANCE_PCT = 20.0


# ============================================================
# AGE
# ============================================================

MAX_LEVEL_AGE_1H = 120
MAX_LEVEL_AGE_15M = 200

SWEEP_RECENT_LOOKBACK_1H = 30
SWEEP_RECENT_LOOKBACK_15M = 60

SWEPT_MIN_DEPTH_PCT = 0.15


# ============================================================
# D1 CONTEXT
# ============================================================

D1_SWING_LOOKBACK = 60
D1_POINT_LOOKBACK = 30


# ============================================================
# LOCAL
# ============================================================

LOCAL_TOUCH_DISTANCE_PCT = 0.20


# ============================================================
# SWEEP
# ============================================================

MIN_SWEEP_DEPTH_PCT = 0.08

MAX_SWEEP_LOOKBACK_1H = 8


# ============================================================
# HTTP SESSION
# ============================================================

_thread_local = threading.local()


def _get_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)

    if session is None:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "TradeMind/7.2",
            "Accept": "application/json",
        })

        adapter = requests.adapters.HTTPAdapter(
            pool_connections=4,
            pool_maxsize=4,
            max_retries=0,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        _thread_local.session = session

    return session


# ============================================================
# HTTP
# ============================================================

def _get(endpoint, params, retries=HTTP_RETRIES):

    url = f"{BASE_URL}/{endpoint}"
    session = _get_session()
    last_exc = None

    for attempt in range(retries):
        try:
            response = session.get(
                url, params=params, timeout=REQUEST_TIMEOUT,
            )

            if response.status_code == 429:
                wait = int(response.headers.get("Retry-After", 2 ** attempt))
                time.sleep(min(wait, 10))
                continue

            if 500 <= response.status_code < 600:
                time.sleep(0.5 * (2 ** attempt))
                continue

            response.raise_for_status()
            return response.json()

        except requests.RequestException as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(0.5 * (2 ** attempt))

    if last_exc is not None:
        raise last_exc

    raise RuntimeError(f"HTTP failed: {endpoint} {params}")


# ============================================================
# SYMBOL
# ============================================================

def _normalize_symbol(symbol):
    if not symbol:
        return "SOLUSDT"
    symbol = str(symbol).upper().strip()
    if symbol in COINS:
        return COINS[symbol]
    if symbol.endswith("USDT"):
        return symbol
    return f"{symbol}USDT"


# ============================================================
# PRICE
# ============================================================

def get_current_price(symbol="SOLUSDT"):
    symbol = _normalize_symbol(symbol)
    data = _get("ticker/price", {"symbol": symbol})
    return float(data["price"])


# ============================================================
# KLINES
# ============================================================

def _fetch_klines(interval, limit, symbol):

    raw = _get("klines", {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    })

    candles = []

    for item in raw:
        candles.append({
            "open_time": int(item[0]),
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
            "close_time": int(item[6]),
        })

    return candles


def get_klines(interval, limit, symbol="SOLUSDT"):

    symbol = _normalize_symbol(symbol)
    key = (symbol, interval, limit)
    ttl = KLINES_TTL.get(interval, 10)

    now = time.time()

    with _klines_lock:
        cached = _klines_cache.get(key)
        if cached is not None:
            ts, data = cached
            if now - ts < ttl:
                return data

    data = _fetch_klines(interval, limit, symbol)

    with _klines_lock:
        _klines_cache[key] = (now, data)

    return data


# ============================================================
# CANDLE HELPERS
# ============================================================

def candle_open(c): return float(c.get("open", 0))
def candle_high(c): return float(c.get("high", 0))
def candle_low(c): return float(c.get("low", 0))
def candle_close(c): return float(c.get("close", 0))


def candle_body(c):
    return abs(candle_close(c) - candle_open(c))


def candle_range(c):
    return max(candle_high(c) - candle_low(c), 1e-12)


def body_ratio(c):
    return candle_body(c) / candle_range(c)


def is_bullish(c):
    return candle_close(c) > candle_open(c)


def is_bearish(c):
    return candle_close(c) < candle_open(c)


# ============================================================
# DISTANCE
# ============================================================

def distance_pct(price_a, price_b):
    if price_b == 0:
        return 999.0
    return abs(price_a - price_b) / abs(price_b) * 100.0


# ============================================================
# SWINGS (generic)
# ============================================================

def _find_swings(candles, left, right, kind):

    result = []

    if len(candles) < left + right + 1:
        return result

    for i in range(left, len(candles) - right):

        candle = candles[i]

        if kind == "high":
            price = candle_high(candle)
            left_side = candles[i - left:i]
            right_side = candles[i + 1:i + 1 + right]

            left_ok = all(price > candle_high(x) for x in left_side)
            right_ok = all(price >= candle_high(x) for x in right_side)

            if left_ok and right_ok:
                result.append({
                    "price": price,
                    "index": i,
                    "time": candle.get("open_time"),
                    "type": "BSL",
                })
        else:
            price = candle_low(candle)
            left_side = candles[i - left:i]
            right_side = candles[i + 1:i + 1 + right]

            left_ok = all(price < candle_low(x) for x in left_side)
            right_ok = all(price <= candle_low(x) for x in right_side)

            if left_ok and right_ok:
                result.append({
                    "price": price,
                    "index": i,
                    "time": candle.get("open_time"),
                    "type": "SSL",
                })

    return result


def find_swing_highs(candles):
    return _find_swings(candles, SWING_LEFT, SWING_RIGHT, "high")


def find_swing_lows(candles):
    return _find_swings(candles, SWING_LEFT, SWING_RIGHT, "low")


def find_swing_highs_15m(candles):
    return _find_swings(candles, SWING_LEFT_15M, SWING_RIGHT_15M, "high")


def find_swing_lows_15m(candles):
    return _find_swings(candles, SWING_LEFT_15M, SWING_RIGHT_15M, "low")


def find_swing_highs_d1(candles):
    return _find_swings(candles, SWING_LEFT_D1, SWING_RIGHT_D1, "high")


def find_swing_lows_d1(candles):
    return _find_swings(candles, SWING_LEFT_D1, SWING_RIGHT_D1, "low")


# ============================================================
# D1 CONTEXT
# ============================================================

def _analyze_d1_context(candles_d1, price):
    """
    Определяет дневной тренд, точку A (старт движения) и
    точку B (цель — противоположная ликвидность).
    """

    empty = {
        "trend": "NEUTRAL",
        "point_a": None,
        "point_b": None,
        "last_swing_high": None,
        "last_swing_low": None,
    }

    if not candles_d1 or len(candles_d1) < 20:
        return empty

    confirmed = candles_d1[:-1]

    if len(confirmed) < 15:
        return empty

    swing_highs = find_swing_highs_d1(confirmed)
    swing_lows = find_swing_lows_d1(confirmed)

    # ---------- Trend ----------

    trend = "NEUTRAL"

    if len(swing_highs) >= 3 and len(swing_lows) >= 3:

        h1 = swing_highs[-3]["price"]
        h2 = swing_highs[-2]["price"]
        h3 = swing_highs[-1]["price"]

        l1 = swing_lows[-3]["price"]
        l2 = swing_lows[-2]["price"]
        l3 = swing_lows[-1]["price"]

        if h3 > h2 > h1 and l3 > l2 > l1:
            trend = "LONG"
        elif h3 < h2 < h1 and l3 < l2 < l1:
            trend = "SHORT"

    # Softer fallback
    if trend == "NEUTRAL" and len(swing_highs) >= 2 and len(swing_lows) >= 2:
        if (
            swing_highs[-1]["price"] > swing_highs[-2]["price"]
            and swing_lows[-1]["price"] > swing_lows[-2]["price"]
        ):
            trend = "LONG"
        elif (
            swing_highs[-1]["price"] < swing_highs[-2]["price"]
            and swing_lows[-1]["price"] < swing_lows[-2]["price"]
        ):
            trend = "SHORT"

    # ---------- Point A / Point B ----------

    point_a = None
    point_b = None

    cutoff_index = len(confirmed) - D1_POINT_LOOKBACK

    recent_lows = [
        l for l in swing_lows if l["index"] >= cutoff_index
    ]
    recent_highs = [
        h for h in swing_highs if h["index"] >= cutoff_index
    ]

    if trend == "LONG":
        if recent_lows:
            point_a = min(l["price"] for l in recent_lows)
        elif swing_lows:
            point_a = min(l["price"] for l in swing_lows[-5:])

        highs_above = [
            h["price"] for h in swing_highs if h["price"] > price
        ]
        if highs_above:
            point_b = min(highs_above)

    elif trend == "SHORT":
        if recent_highs:
            point_a = max(h["price"] for h in recent_highs)
        elif swing_highs:
            point_a = max(h["price"] for h in swing_highs[-5:])

        lows_below = [
            l["price"] for l in swing_lows if l["price"] < price
        ]
        if lows_below:
            point_b = max(lows_below)

    return {
        "trend": trend,
        "point_a": point_a,
        "point_b": point_b,
        "last_swing_high": (
            swing_highs[-1]["price"] if swing_highs else None
        ),
        "last_swing_low": (
            swing_lows[-1]["price"] if swing_lows else None
        ),
    }


# ============================================================
# CLUSTER
# ============================================================

def cluster_levels(levels):

    if not levels:
        return []

    levels = sorted(levels, key=lambda x: x["price"])

    clusters = []

    for level in levels:
        if not clusters:
            clusters.append([level])
            continue

        current = clusters[-1]
        avg_price = sum(x["price"] for x in current) / len(current)
        dist = distance_pct(level["price"], avg_price)

        if dist <= CLUSTER_DISTANCE_PCT:
            current.append(level)
        else:
            clusters.append([level])

    result = []

    for cluster in clusters:
        prices = [x["price"] for x in cluster]
        indices = [x["index"] for x in cluster]
        times = [x.get("time", 0) for x in cluster]

        result.append({
            "price": sum(prices) / len(prices),
            "touches": len(cluster),
            "first_index": min(indices),
            "last_index": max(indices),
            "first_time": min(times),
            "last_time": max(times),
        })

    return result


# ============================================================
# FRESHNESS
# ============================================================

def freshness_score(level, candles, max_age):

    last_index = int(level.get("last_index", 0))
    age = len(candles) - 1 - last_index

    if age <= 10: return 25.0
    if age <= 30: return 20.0
    if age <= 60: return 15.0
    if age <= 90: return 10.0
    if age <= max_age: return 5.0
    return 0.0


# ============================================================
# LOCAL TOUCHES
# ============================================================

def count_local_touches(level_price, candles):

    touches = 0

    for candle in candles:
        high = candle_high(candle)
        low = candle_low(candle)

        if low <= level_price <= high:
            touches += 1
            continue

        if distance_pct(high, level_price) <= LOCAL_TOUCH_DISTANCE_PCT:
            touches += 1
            continue

        if distance_pct(low, level_price) <= LOCAL_TOUCH_DISTANCE_PCT:
            touches += 1

    return touches


# ============================================================
# LEVEL SWEPT
# ============================================================

def level_has_been_swept(level_price, level_type, candles, lookback):

    if not candles:
        return False

    recent = candles[-lookback:]

    for candle in recent:
        high = candle_high(candle)
        low = candle_low(candle)
        close = candle_close(candle)

        if level_type == "BSL":
            if high > level_price and close < level_price:
                depth_pct = (high - level_price) / level_price * 100.0
                if depth_pct >= SWEPT_MIN_DEPTH_PCT:
                    return True

        elif level_type == "SSL":
            if low < level_price and close > level_price:
                depth_pct = (level_price - low) / level_price * 100.0
                if depth_pct >= SWEPT_MIN_DEPTH_PCT:
                    return True

    return False


# ============================================================
# STRENGTH
# ============================================================

def calculate_strength(level, candles, candles_15m, max_age, base=48.0):

    score = base

    touches = int(level.get("touches", 1))
    if touches >= 2: score += 8
    if touches >= 3: score += 6
    if touches >= 4: score += 5

    score += freshness_score(level, candles, max_age)

    local_touches = count_local_touches(level["price"], candles_15m)
    if local_touches >= 2: score += 4
    if local_touches >= 4: score += 4

    return min(round(score, 2), 100.0)


# ============================================================
# SELECT ZONES
# ============================================================

def _select_zones(
    levels,
    price,
    candles_ref,
    candles_15m,
    level_type,
    min_strength,
    max_age,
    sweep_lookback,
    source,
):

    candidates = []

    for level in levels:

        level_price = float(level.get("price", 0))
        if level_price <= 0:
            continue

        if level_type == "BSL":
            if level_price <= price:
                continue
        elif level_type == "SSL":
            if level_price >= price:
                continue
        else:
            continue

        distance = distance_pct(price, level_price)
        if distance < MIN_MAJOR_DISTANCE_PCT:
            continue

        age = len(candles_ref) - 1 - int(level.get("last_index", 0))
        if age > max_age:
            continue

        if level_has_been_swept(
            level_price, level_type, candles_ref, sweep_lookback,
        ):
            continue

        strength = calculate_strength(
            level, candles_ref, candles_15m, max_age,
        )

        if strength < min_strength:
            continue

        zone_low = level_price * (1 - ZONE_WIDTH_PCT / 100.0)
        zone_high = level_price * (1 + ZONE_WIDTH_PCT / 100.0)

        candidates.append({
            "price": round(level_price, 8),
            "zone_low": round(zone_low, 8),
            "zone_high": round(zone_high, 8),
            "type": level_type,
            "strength": round(strength, 2),
            "touches": int(level.get("touches", 1)),
            "distance_pct": round(distance, 4),
            "age_1h": age,
            "source": source,
            "status": "FRESH",
            "swept": False,
            "taken": False,
            "used": False,
            "consumed": False,
        })

    candidates.sort(
        key=lambda x: (x["strength"], x["touches"], -x["distance_pct"]),
        reverse=True,
    )

    selected = []

    for candidate in candidates:
        too_close = False
        for existing in selected:
            if distance_pct(candidate["price"], existing["price"]) < MIN_ZONE_GAP_PCT:
                too_close = True
                break

        if too_close:
            continue

        selected.append(candidate)

        if len(selected) >= MAX_LEVELS_PER_SIDE:
            break

    selected.sort(key=lambda x: x["distance_pct"])

    return selected


# ============================================================
# ROUND NUMBERS
# ============================================================

def _round_step(price):
    if price <= 0:
        return 1.0
    exp = math.floor(math.log10(price)) - 1
    return 10 ** exp


def find_round_number_levels(
    price, side,
    max_count=ROUND_MAX_COUNT,
    max_distance_pct=ROUND_MAX_DISTANCE_PCT,
):

    if price <= 0:
        return []

    step = _round_step(price)
    if step <= 0:
        return []

    results = []

    if side == "BSL":
        first = math.ceil(price / step) * step
        if first <= price:
            first += step

        current = first

        for _ in range(max_count):
            dist = (current - price) / price * 100
            if dist > max_distance_pct:
                break

            zone_low = current * (1 - ZONE_WIDTH_PCT / 100.0)
            zone_high = current * (1 + ZONE_WIDTH_PCT / 100.0)

            results.append({
                "price": round(current, 8),
                "zone_low": round(zone_low, 8),
                "zone_high": round(zone_high, 8),
                "type": "BSL",
                "strength": MIN_ROUND_STRENGTH,
                "touches": 1,
                "distance_pct": round(dist, 4),
                "age_1h": 0,
                "source": "ROUND",
                "status": "FRESH",
                "swept": False, "taken": False,
                "used": False, "consumed": False,
            })

            current += step

    else:
        first = math.floor(price / step) * step
        if first >= price:
            first -= step

        current = first

        for _ in range(max_count):
            dist = (price - current) / price * 100
            if dist > max_distance_pct:
                break

            zone_low = current * (1 - ZONE_WIDTH_PCT / 100.0)
            zone_high = current * (1 + ZONE_WIDTH_PCT / 100.0)

            results.append({
                "price": round(current, 8),
                "zone_low": round(zone_low, 8),
                "zone_high": round(zone_high, 8),
                "type": "SSL",
                "strength": MIN_ROUND_STRENGTH,
                "touches": 1,
                "distance_pct": round(dist, 4),
                "age_1h": 0,
                "source": "ROUND",
                "status": "FRESH",
                "swept": False, "taken": False,
                "used": False, "consumed": False,
            })

            current -= step

    return results


# ============================================================
# MERGE SOURCES
# ============================================================

def _merge_sources(sources, limit):

    merged = []
    seen_prices = []

    for source in sources:
        for level in source:

            if len(merged) >= limit:
                break

            too_close = any(
                distance_pct(level["price"], p) < MIN_ZONE_GAP_PCT
                for p in seen_prices
            )
            if too_close:
                continue

            merged.append(level)
            seen_prices.append(level["price"])

        if len(merged) >= limit:
            break

    merged.sort(key=lambda x: x["distance_pct"])
    return merged


# ============================================================
# BUILD MAJOR LIQUIDITY
# ============================================================

def _build_major_liquidity(candles_1h, candles_15m, price):

    if not candles_1h:
        return {"BSL": [], "SSL": []}

    confirmed_1h = candles_1h[:-1]

    if len(confirmed_1h) < 10:
        return {"BSL": [], "SSL": []}

    bsl_clusters_1h = cluster_levels(find_swing_highs(confirmed_1h))
    ssl_clusters_1h = cluster_levels(find_swing_lows(confirmed_1h))

    bsl_1h = _select_zones(
        bsl_clusters_1h, price, confirmed_1h, candles_15m,
        "BSL", MIN_MAJOR_STRENGTH, MAX_LEVEL_AGE_1H,
        SWEEP_RECENT_LOOKBACK_1H, "1H",
    )

    ssl_1h = _select_zones(
        ssl_clusters_1h, price, confirmed_1h, candles_15m,
        "SSL", MIN_MAJOR_STRENGTH, MAX_LEVEL_AGE_1H,
        SWEEP_RECENT_LOOKBACK_1H, "1H",
    )

    bsl_15m = []
    ssl_15m = []

    need_15m_bsl = len(bsl_1h) < MIN_LEVELS_PER_SIDE_1H
    need_15m_ssl = len(ssl_1h) < MIN_LEVELS_PER_SIDE_1H

    if (need_15m_bsl or need_15m_ssl) and candles_15m:

        confirmed_15m = candles_15m[:-1]

        if len(confirmed_15m) >= 10:

            bsl_clusters_15m = cluster_levels(
                find_swing_highs_15m(confirmed_15m)
            )
            ssl_clusters_15m = cluster_levels(
                find_swing_lows_15m(confirmed_15m)
            )

            if need_15m_bsl:
                bsl_15m = _select_zones(
                    bsl_clusters_15m, price, confirmed_15m, candles_15m,
                    "BSL", MIN_MINOR_STRENGTH, MAX_LEVEL_AGE_15M,
                    SWEEP_RECENT_LOOKBACK_15M, "15M",
                )

            if need_15m_ssl:
                ssl_15m = _select_zones(
                    ssl_clusters_15m, price, confirmed_15m, candles_15m,
                    "SSL", MIN_MINOR_STRENGTH, MAX_LEVEL_AGE_15M,
                    SWEEP_RECENT_LOOKBACK_15M, "15M",
                )

    bsl_round = []
    ssl_round = []

    if len(bsl_1h) + len(bsl_15m) < MIN_LEVELS_PER_SIDE_1H:
        bsl_round = find_round_number_levels(price, "BSL")

    if len(ssl_1h) + len(ssl_15m) < MIN_LEVELS_PER_SIDE_1H:
        ssl_round = find_round_number_levels(price, "SSL")

    bsl = _merge_sources([bsl_1h, bsl_15m, bsl_round], MAX_LEVELS_PER_SIDE)
    ssl = _merge_sources([ssl_1h, ssl_15m, ssl_round], MAX_LEVELS_PER_SIDE)

    combined = [("BSL", x) for x in bsl] + [("SSL", x) for x in ssl]

    combined.sort(key=lambda x: x[1]["strength"], reverse=True)
    combined = combined[:MAX_TOTAL_LEVELS]

    result_bsl = [lvl for side, lvl in combined if side == "BSL"]
    result_ssl = [lvl for side, lvl in combined if side == "SSL"]

    result_bsl.sort(key=lambda x: x["distance_pct"])
    result_ssl.sort(key=lambda x: x["distance_pct"])

    return {"BSL": result_bsl, "SSL": result_ssl}


# ============================================================
# PUBLIC MAJOR LIQUIDITY
# ============================================================

def find_major_liquidity(
    candles_1h, price, max_levels=12,
    candles_15m=None, candles_5m=None, candles_1m=None,
):

    if candles_15m is None:
        candles_15m = []

    liquidity = _build_major_liquidity(candles_1h, candles_15m, price)

    levels = liquidity["BSL"] + liquidity["SSL"]
    levels.sort(key=lambda x: x["distance_pct"])

    return levels[:max_levels]


# ============================================================
# TARGET LIQUIDITY
# ============================================================

def get_target_liquidity(major_liquidity, direction, entry):

    if not major_liquidity:
        return None

    if isinstance(major_liquidity, dict):
        if direction == "LONG":
            levels = major_liquidity.get("BSL", [])
            candidates = [
                x for x in levels
                if x.get("price", 0) > entry
                and not x.get("swept", False)
                and not x.get("taken", False)
                and not x.get("used", False)
            ]
        elif direction == "SHORT":
            levels = major_liquidity.get("SSL", [])
            candidates = [
                x for x in levels
                if x.get("price", 0) < entry
                and not x.get("swept", False)
                and not x.get("taken", False)
                and not x.get("used", False)
            ]
        else:
            return None
    else:
        levels = major_liquidity
        if direction == "LONG":
            candidates = [
                x for x in levels
                if x.get("type") == "BSL"
                and x.get("price", 0) > entry
                and not x.get("swept", False)
                and not x.get("taken", False)
                and not x.get("used", False)
            ]
        elif direction == "SHORT":
            candidates = [
                x for x in levels
                if x.get("type") == "SSL"
                and x.get("price", 0) < entry
                and not x.get("swept", False)
                and not x.get("taken", False)
                and not x.get("used", False)
            ]
        else:
            return None

    if not candidates:
        return None

    candidates.sort(key=lambda x: abs(x["price"] - entry))
    return candidates[0]


# ============================================================
# DETECT SWEEP
# ============================================================

def detect_sweep(candles_1h, price, direction, levels=None):

    if direction not in {"LONG", "SHORT"}:
        return None

    if not candles_1h:
        return None

    normalized_levels = []

    if isinstance(levels, dict):
        for side in ("BSL", "SSL"):
            for level in levels.get(side, []):
                if not isinstance(level, dict):
                    continue
                item = dict(level)
                item.setdefault("type", side)
                normalized_levels.append(item)
    elif isinstance(levels, list):
        for level in levels:
            if not isinstance(level, dict):
                continue
            normalized_levels.append(dict(level))

    expected_type = "SSL" if direction == "LONG" else "BSL"

    valid_levels = [
        level for level in normalized_levels
        if level.get("type") == expected_type
        and not level.get("swept", False)
        and not level.get("taken", False)
        and not level.get("used", False)
    ]

    if not valid_levels:
        return None

    confirmed = candles_1h[:-1]
    if not confirmed:
        return None

    recent = confirmed[-MAX_SWEEP_LOOKBACK_1H:]

    if direction == "LONG":

        for candle in reversed(recent):

            low = candle_low(candle)
            close = candle_close(candle)
            open_price = candle_open(candle)

            candidates = []

            for level in valid_levels:

                level_price = float(level.get("price", 0))
                if level_price <= 0: continue
                if level_price >= price: continue
                if low >= level_price: continue

                depth_pct = (level_price - low) / level_price * 100.0
                if depth_pct < MIN_SWEEP_DEPTH_PCT: continue
                if close <= level_price: continue
                if close <= open_price: continue

                candidates.append((abs(level_price - low), level, depth_pct))

            if candidates:
                candidates.sort(key=lambda x: x[0])
                _, level, depth_pct = candidates[0]

                return {
                    "swept": True,
                    "direction": "LONG",
                    "liquidity_type": "SSL",
                    "level": float(level["price"]),
                    "extreme": low,
                    "price": low,
                    "depth_pct": round(depth_pct, 4),
                    "open_time": candle.get("open_time"),
                    "strength": float(level.get("strength", 0)),
                    "touches": int(level.get("touches", 1)),
                }

    if direction == "SHORT":

        for candle in reversed(recent):

            high = candle_high(candle)
            close = candle_close(candle)
            open_price = candle_open(candle)

            candidates = []

            for level in valid_levels:

                level_price = float(level.get("price", 0))
                if level_price <= 0: continue
                if level_price <= price: continue
                if high <= level_price: continue

                depth_pct = (high - level_price) / level_price * 100.0
                if depth_pct < MIN_SWEEP_DEPTH_PCT: continue
                if close >= level_price: continue
                if close >= open_price: continue

                candidates.append((abs(high - level_price), level, depth_pct))

            if candidates:
                candidates.sort(key=lambda x: x[0])
                _, level, depth_pct = candidates[0]

                return {
                    "swept": True,
                    "direction": "SHORT",
                    "liquidity_type": "BSL",
                    "level": float(level["price"]),
                    "extreme": high,
                    "price": high,
                    "depth_pct": round(depth_pct, 4),
                    "open_time": candle.get("open_time"),
                    "strength": float(level.get("strength", 0)),
                    "touches": int(level.get("touches", 1)),
                }

    return None


# ============================================================
# MARKET DATA
# ============================================================

def get_market_data(symbol="SOLUSDT"):

    symbol = _normalize_symbol(symbol)

    with ThreadPoolExecutor(max_workers=5) as ex:
        f_d1 = ex.submit(get_klines, "1d", LOOKBACK_D1, symbol)
        f_1h = ex.submit(get_klines, "1h", LOOKBACK_1H, symbol)
        f_15m = ex.submit(get_klines, "15m", LOOKBACK_15M, symbol)
        f_5m = ex.submit(get_klines, "5m", LOOKBACK_5M, symbol)
        f_1m = ex.submit(get_klines, "1m", LOOKBACK_1M, symbol)

        candles_d1 = f_d1.result()
        candles_1h = f_1h.result()
        candles_15m = f_15m.result()
        candles_5m = f_5m.result()
        candles_1m = f_1m.result()

    price = (
        float(candles_1m[-1]["close"])
        if candles_1m
        else get_current_price(symbol)
    )

    major_liquidity = _build_major_liquidity(
        candles_1h, candles_15m, price,
    )

    d1_context = _analyze_d1_context(candles_d1, price)

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

        "major_liquidity": major_liquidity,
        "d1_context": d1_context,

        "updated_at": time.time(),
        "market_version": MARKET_VERSION,
    }


# ============================================================
# COMPAT
# ============================================================

def get_major_liquidity(price=None, symbol="SOLUSDT"):

    symbol = _normalize_symbol(symbol)
    candles_1h = get_klines("1h", LOOKBACK_1H, symbol)
    candles_15m = get_klines("15m", LOOKBACK_15M, symbol)

    if price is None:
        candles_1m = get_klines("1m", LOOKBACK_1M, symbol)
        price = (
            float(candles_1m[-1]["close"])
            if candles_1m
            else get_current_price(symbol)
        )

    return _build_major_liquidity(candles_1h, candles_15m, price)


def market_snapshot(symbol="SOLUSDT"):
    return get_market_data(symbol)


# ============================================================
# DEBUG
# ============================================================

def format_major_liquidity(data):

    symbol = data.get("symbol", "SOLUSDT")
    price = float(data.get("price", 0))
    liquidity = data.get("major_liquidity", {})
    d1 = data.get("d1_context", {})

    lines = [
        f"💠 {symbol}",
        f"💰 Цена: ${price:.6f}",
        "",
        "📅 D1 CONTEXT",
        f"Trend: {d1.get('trend', 'NEUTRAL')}",
        f"Point A: {d1.get('point_a')}",
        f"Point B: {d1.get('point_b')}",
        "",
    ]

    lines.append("🔴 MAJOR BSL")
    bsl = liquidity.get("BSL", [])
    if not bsl:
        lines.append("— нет актуальных зон")
    else:
        for i, level in enumerate(bsl, 1):
            lines.append(
                f"{i}. ${level['price']:.6f} "
                f"• {level['distance_pct']:.2f}% "
                f"• S{level['strength']:.0f} "
                f"• {level.get('source', '?')}"
            )

    lines.append("")
    lines.append("🟢 MAJOR SSL")
    ssl = liquidity.get("SSL", [])
    if not ssl:
        lines.append("— нет актуальных зон")
    else:
        for i, level in enumerate(ssl, 1):
            lines.append(
                f"{i}. ${level['price']:.6f} "
                f"• {level['distance_pct']:.2f}% "
                f"• S{level['strength']:.0f} "
                f"• {level.get('source', '?')}"
            )

    return "\n".join(lines)


def debug_symbol(symbol):

    symbol = _normalize_symbol(symbol)

    print("")
    print("=" * 60)
    print(f"TradeMind Market {MARKET_VERSION}")
    print(f"Symbol: {symbol}")
    print("=" * 60)

    try:
        data = get_market_data(symbol)
        print(format_major_liquidity(data))
    except Exception as error:
        print(f"MARKET ERROR: {error}")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print(f"TradeMind market.py {MARKET_VERSION}")
    print(f"Supported coins: {len(COINS)}")
    print("")
    debug_symbol("SOL")