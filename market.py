# ============================================================
# TradeMind 6.8
# market.py
#
# Binance Spot market data
#
# 1H  -> Main Structure -> Major Liquidity -> Sweep
# 15M -> Confirmation
# 5M  -> ILM
# 1M  -> Local context / ambiguity
#
# D1 / W1 НЕ ИСПОЛЬЗУЮТСЯ
#
# ВАЖНО:
# - Major Liquidity строится ТОЛЬКО из 1H
# - 15M используется для strength / local context
# - 5M и 1M не создают Major Liquidity
# - Major уровень ближе 0.30% к цене отбрасывается
# - уже swept liquidity (за последние 30 часов) не считается свежей
# - текущая формирующаяся 1H свеча не используется
# - klines кэшируются по TTL, HTTP с retry/backoff
# ============================================================

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

import requests


# ============================================================
# VERSION
# ============================================================

MARKET_VERSION = "6.8"


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

LOOKBACK_1H = 180
LOOKBACK_15M = 200
LOOKBACK_5M = 200
LOOKBACK_1M = 30     # было 200 — избыточно


# ============================================================
# KLINES CACHE
# ============================================================

KLINES_TTL = {
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
SWING_RIGHT = 2


# ============================================================
# LIQUIDITY SETTINGS
# ============================================================

CLUSTER_DISTANCE_PCT = 0.15

ZONE_WIDTH_PCT = 0.20

MIN_ZONE_GAP_PCT = 0.70

MIN_MAJOR_DISTANCE_PCT = 0.30

MIN_MAJOR_STRENGTH = 60.0

MAX_LEVELS_PER_SIDE = 4

MAX_TOTAL_LEVELS = 8


# ============================================================
# AGE
# ============================================================

MAX_LEVEL_AGE_1H = 120

# Окно, в котором проверяем «уже снят ли уровень».
# Если уровень был снят давно (>30 свечей назад) —
# считаем его снова валидным.
SWEEP_RECENT_LOOKBACK_1H = 30


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
# HTTP SESSION (thread-local)
# ============================================================

_thread_local = threading.local()


def _get_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)

    if session is None:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "TradeMind/6.8",
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

def _get(
    endpoint: str,
    params: Dict[str, Any],
    retries: int = HTTP_RETRIES,
) -> Any:

    url = f"{BASE_URL}/{endpoint}"
    session = _get_session()
    last_exc: Optional[Exception] = None

    for attempt in range(retries):

        try:
            response = session.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code == 429:
                wait = int(
                    response.headers.get(
                        "Retry-After",
                        2 ** attempt,
                    )
                )
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

def _normalize_symbol(symbol: Optional[str]) -> str:

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

def get_current_price(symbol: str = "SOLUSDT") -> float:

    symbol = _normalize_symbol(symbol)

    data = _get(
        "ticker/price",
        {"symbol": symbol},
    )

    return float(data["price"])


# ============================================================
# KLINES (with cache)
# ============================================================

def _fetch_klines(
    interval: str,
    limit: int,
    symbol: str,
) -> List[Dict[str, Any]]:

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
            "open_time": int(item[0]),
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
            "close_time": int(item[6]),
        })

    return candles


def get_klines(
    interval: str,
    limit: int,
    symbol: str = "SOLUSDT",
) -> List[Dict[str, Any]]:

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

def candle_open(c: Dict[str, Any]) -> float:
    return float(c.get("open", 0))


def candle_high(c: Dict[str, Any]) -> float:
    return float(c.get("high", 0))


def candle_low(c: Dict[str, Any]) -> float:
    return float(c.get("low", 0))


def candle_close(c: Dict[str, Any]) -> float:
    return float(c.get("close", 0))


def candle_body(c: Dict[str, Any]) -> float:
    return abs(candle_close(c) - candle_open(c))


def candle_range(c: Dict[str, Any]) -> float:
    return max(candle_high(c) - candle_low(c), 1e-12)


def body_ratio(c: Dict[str, Any]) -> float:
    return candle_body(c) / candle_range(c)


def is_bullish(c: Dict[str, Any]) -> bool:
    return candle_close(c) > candle_open(c)


def is_bearish(c: Dict[str, Any]) -> bool:
    return candle_close(c) < candle_open(c)


# ============================================================
# DISTANCE
# ============================================================

def distance_pct(price_a: float, price_b: float) -> float:

    if price_b == 0:
        return 999.0

    return abs(price_a - price_b) / abs(price_b) * 100.0


# ============================================================
# SWING HIGHS
# ============================================================

def find_swing_highs(
    candles: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    result = []

    if len(candles) < SWING_LEFT + SWING_RIGHT + 1:
        return result

    for i in range(SWING_LEFT, len(candles) - SWING_RIGHT):

        candle = candles[i]
        high = candle_high(candle)

        left = candles[i - SWING_LEFT:i]
        right = candles[i + 1:i + 1 + SWING_RIGHT]

        # Fix: плоские хаи больше не дублируются.
        # Слева строго больше, справа >=.
        left_ok = all(high > candle_high(x) for x in left)
        right_ok = all(high >= candle_high(x) for x in right)

        if left_ok and right_ok:
            result.append({
                "price": high,
                "index": i,
                "time": candle.get("open_time"),
                "type": "BSL",
            })

    return result


# ============================================================
# SWING LOWS
# ============================================================

def find_swing_lows(
    candles: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    result = []

    if len(candles) < SWING_LEFT + SWING_RIGHT + 1:
        return result

    for i in range(SWING_LEFT, len(candles) - SWING_RIGHT):

        candle = candles[i]
        low = candle_low(candle)

        left = candles[i - SWING_LEFT:i]
        right = candles[i + 1:i + 1 + SWING_RIGHT]

        # Fix: плоские лоу больше не дублируются.
        left_ok = all(low < candle_low(x) for x in left)
        right_ok = all(low <= candle_low(x) for x in right)

        if left_ok and right_ok:
            result.append({
                "price": low,
                "index": i,
                "time": candle.get("open_time"),
                "type": "SSL",
            })

    return result


# ============================================================
# CLUSTER
# ============================================================

def cluster_levels(
    levels: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    if not levels:
        return []

    levels = sorted(levels, key=lambda x: x["price"])

    clusters: List[List[Dict[str, Any]]] = []

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

def freshness_score(
    level: Dict[str, Any],
    candles_1h: List[Dict[str, Any]],
) -> float:

    last_index = int(level.get("last_index", 0))
    age = len(candles_1h) - 1 - last_index

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

        high = candle_high(candle)
        low = candle_low(candle)

        # Fix: свеча, прошедшая сквозь уровень,
        # теперь тоже считается касанием.
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

def level_has_been_swept(
    level_price: float,
    level_type: str,
    candles: List[Dict[str, Any]],
) -> bool:
    """
    Проверяем sweep только в недавнем окне.
    Старый sweep (>30 свечей назад) не дисквалифицирует уровень.
    """

    if not candles:
        return False

    recent = candles[-SWEEP_RECENT_LOOKBACK_1H:]

    for candle in recent:

        high = candle_high(candle)
        low = candle_low(candle)
        close = candle_close(candle)

        if level_type == "BSL":
            if high > level_price and close < level_price:
                return True

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

    score = 48.0

    touches = int(level.get("touches", 1))

    if touches >= 2:
        score += 8
    if touches >= 3:
        score += 6
    if touches >= 4:
        score += 5

    score += freshness_score(level, candles_1h)

    local_touches = count_local_touches(
        level["price"],
        candles_15m,
    )

    if local_touches >= 2:
        score += 4
    if local_touches >= 4:
        score += 4

    return min(round(score, 2), 100.0)


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

        age = (
            len(candles_1h)
            - 1
            - int(level.get("last_index", 0))
        )
        if age > MAX_LEVEL_AGE_1H:
            continue

        if level_has_been_swept(
            level_price,
            level_type,
            candles_1h,
        ):
            continue

        strength = calculate_strength(
            level,
            candles_1h,
            candles_15m,
        )
        if strength < MIN_MAJOR_STRENGTH:
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
            "status": "FRESH",
            "swept": False,
            "taken": False,
            "used": False,
            "consumed": False,
        })

    candidates.sort(
        key=lambda x: (
            x["strength"],
            x["touches"],
            -x["distance_pct"],
        ),
        reverse=True,
    )

    selected = []

    for candidate in candidates:

        too_close = False

        for existing in selected:
            gap = distance_pct(
                candidate["price"],
                existing["price"],
            )
            if gap < MIN_ZONE_GAP_PCT:
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
# BUILD MAJOR LIQUIDITY
# ============================================================

def _build_major_liquidity(
    candles_1h: List[Dict[str, Any]],
    candles_15m: List[Dict[str, Any]],
    price: float,
) -> Dict[str, List[Dict[str, Any]]]:

    if not candles_1h:
        return {"BSL": [], "SSL": []}

    confirmed_1h = candles_1h[:-1]

    if len(confirmed_1h) < 10:
        return {"BSL": [], "SSL": []}

    swing_highs = find_swing_highs(confirmed_1h)
    swing_lows = find_swing_lows(confirmed_1h)

    bsl_clusters = cluster_levels(swing_highs)
    ssl_clusters = cluster_levels(swing_lows)

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

    combined = (
        [("BSL", x) for x in bsl]
        + [("SSL", x) for x in ssl]
    )

    combined.sort(
        key=lambda x: x[1]["strength"],
        reverse=True,
    )

    combined = combined[:MAX_TOTAL_LEVELS]

    result_bsl = [
        level for side, level in combined if side == "BSL"
    ]
    result_ssl = [
        level for side, level in combined if side == "SSL"
    ]

    result_bsl.sort(key=lambda x: x["distance_pct"])
    result_ssl.sort(key=lambda x: x["distance_pct"])

    return {
        "BSL": result_bsl,
        "SSL": result_ssl,
    }


# ============================================================
# PUBLIC MAJOR LIQUIDITY
# ============================================================

def find_major_liquidity(
    candles_1h: List[Dict[str, Any]],
    price: float,
    max_levels: int = 12,
    candles_15m: Optional[List[Dict[str, Any]]] = None,
    candles_5m: Optional[List[Dict[str, Any]]] = None,
    candles_1m: Optional[List[Dict[str, Any]]] = None,
):
    """
    Возвращает FLAT список уровней.
    Каждый уровень имеет поле "type": "BSL" | "SSL".
    """

    if candles_15m is None:
        candles_15m = []

    liquidity = _build_major_liquidity(
        candles_1h,
        candles_15m,
        price,
    )

    levels = liquidity["BSL"] + liquidity["SSL"]

    levels.sort(key=lambda x: x["distance_pct"])

    return levels[:max_levels]


# ============================================================
# TARGET LIQUIDITY
# ============================================================

def get_target_liquidity(
    major_liquidity: Any,
    direction: str,
    entry: float,
) -> Optional[Dict[str, Any]]:

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

def detect_sweep(
    candles_1h: List[Dict[str, Any]],
    price: float,
    direction: str,
    levels: Any = None,
) -> Optional[Dict[str, Any]]:
    """
    LONG  -> SSL sweep
    SHORT -> BSL sweep

    Только MAJOR уровни.
    Возвращает лучший (самый близкий по глубине) sweep.
    """

    if direction not in {"LONG", "SHORT"}:
        return None

    if not candles_1h:
        return None

    # ---------- Normalize levels ----------
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

    # ========================================================
    # LONG -> SSL
    # ========================================================

    if direction == "LONG":

        for candle in reversed(recent):

            low = candle_low(candle)
            close = candle_close(candle)
            open_price = candle_open(candle)

            candidates = []

            for level in valid_levels:

                level_price = float(level.get("price", 0))
                if level_price <= 0:
                    continue
                if level_price >= price:
                    continue
                if low >= level_price:
                    continue

                depth_pct = (
                    (level_price - low) / level_price * 100.0
                )
                if depth_pct < MIN_SWEEP_DEPTH_PCT:
                    continue
                if close <= level_price:
                    continue
                if close <= open_price:
                    continue

                candidates.append((
                    abs(level_price - low),
                    level,
                    depth_pct,
                ))

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

    # ========================================================
    # SHORT -> BSL
    # ========================================================

    if direction == "SHORT":

        for candle in reversed(recent):

            high = candle_high(candle)
            close = candle_close(candle)
            open_price = candle_open(candle)

            candidates = []

            for level in valid_levels:

                level_price = float(level.get("price", 0))
                if level_price <= 0:
                    continue
                if level_price <= price:
                    continue
                if high <= level_price:
                    continue

                depth_pct = (
                    (high - level_price) / level_price * 100.0
                )
                if depth_pct < MIN_SWEEP_DEPTH_PCT:
                    continue
                if close >= level_price:
                    continue
                if close >= open_price:
                    continue

                candidates.append((
                    abs(high - level_price),
                    level,
                    depth_pct,
                ))

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

def get_market_data(symbol: str = "SOLUSDT") -> Dict[str, Any]:

    symbol = _normalize_symbol(symbol)

    # ---------- Parallel klines ----------
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_1h = ex.submit(get_klines, "1h", LOOKBACK_1H, symbol)
        f_15m = ex.submit(get_klines, "15m", LOOKBACK_15M, symbol)
        f_5m = ex.submit(get_klines, "5m", LOOKBACK_5M, symbol)
        f_1m = ex.submit(get_klines, "1m", LOOKBACK_1M, symbol)

        candles_1h = f_1h.result()
        candles_15m = f_15m.result()
        candles_5m = f_5m.result()
        candles_1m = f_1m.result()

    # ---------- Price ----------
    # Берём close последней 1m свечи.
    # Это экономит один HTTP-запрос на символ.
    if candles_1m:
        price = float(candles_1m[-1]["close"])
    else:
        price = get_current_price(symbol)

    major_liquidity = _build_major_liquidity(
        candles_1h,
        candles_15m,
        price,
    )

    return {
        "symbol": symbol,
        "price": price,

        "candles_1h": candles_1h,
        "candles_15m": candles_15m,
        "candles_5m": candles_5m,
        "candles_1m": candles_1m,

        "candles": {
            "1h": candles_1h,
            "15m": candles_15m,
            "5m": candles_5m,
            "1m": candles_1m,
        },

        "major_liquidity": major_liquidity,

        "updated_at": time.time(),
        "market_version": MARKET_VERSION,
    }


# ============================================================
# COMPATIBILITY ALIAS
# ============================================================

def get_major_liquidity(
    price: Optional[float] = None,
    symbol: str = "SOLUSDT",
):

    symbol = _normalize_symbol(symbol)

    candles_1h = get_klines("1h", LOOKBACK_1H, symbol)
    candles_15m = get_klines("15m", LOOKBACK_15M, symbol)

    if price is None:
        candles_1m = get_klines("1m", LOOKBACK_1M, symbol)
        if candles_1m:
            price = float(candles_1m[-1]["close"])
        else:
            price = get_current_price(symbol)

    return _build_major_liquidity(
        candles_1h,
        candles_15m,
        price,
    )


# ============================================================
# SNAPSHOT
# ============================================================

def market_snapshot(symbol: str = "SOLUSDT"):
    return get_market_data(symbol)


# ============================================================
# DEBUG
# ============================================================

def format_major_liquidity(data: Dict[str, Any]) -> str:

    symbol = data.get("symbol", "SOLUSDT")
    price = float(data.get("price", 0))
    liquidity = data.get("major_liquidity", {})

    lines = [
        f"💠 {symbol}",
        f"💰 Цена: ${price:.6f}",
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
                f"• T{level['touches']}"
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
                f"• T{level['touches']}"
            )

    return "\n".join(lines)


def debug_symbol(symbol: str):

    symbol = _normalize_symbol(symbol)

    print("")
    print("=" * 60)
    print(f"TradeMind Market {MARKET_VERSION}")
    print(f"Symbol: {symbol}")
    print(f"Major distance: {MIN_MAJOR_DISTANCE_PCT}%")
    print(f"Min strength: {MIN_MAJOR_STRENGTH}")
    print(f"Klines TTL: {KLINES_TTL}")
    print("=" * 60)

    try:
        data = get_market_data(symbol)
        print(format_major_liquidity(data))
    except Exception as error:
        print(f"MARKET ERROR: {error}")


# ============================================================
# MAIN TEST
# ============================================================

if __name__ == "__main__":

    print(f"TradeMind market.py {MARKET_VERSION}")
    print("Binance Spot")
    print(f"Supported coins: {len(COINS)}")
    print("Major Liquidity only")
    print(f"Minimum Major distance: {MIN_MAJOR_DISTANCE_PCT}%")
    print("")

    debug_symbol("SOL")