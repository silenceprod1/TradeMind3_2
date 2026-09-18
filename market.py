# ============================================================
# TradeMind 7.5
# market.py
#
# Цель версии:
# - убрать зависимость от старой ликвидности;
# - Major Liquidity = подтверждённые 1H swing highs/lows;
# - свежие 1H уровни получают приоритет;
# - старые уровни не удаляются только из-за возраста;
# - нет MIN_DISTANCE;
# - нет MIN_STRENGTH gate;
# - нет MIN_ZONE_GAP;
# - нет жёсткого MAX_LEVEL_AGE;
# - 15M используется только как дополнительная локальная
#   ликвидность;
# - 5M/15M FVG сохраняются;
# - текущая цена берётся напрямую с Binance;
# - последняя незакрытая свеча не считается подтверждённой;
# - detect_sweep() совместим со strategy.py;
# - сохранена совместимость с bot.py;
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

# 1H.
#
# 2 свечи слева + 1 справа.
#
# Это позволяет обнаруживать свежий swing быстрее,
# чем классический 2 + 2.
#
# ВАЖНО:
# swing всё равно строится ТОЛЬКО по ЗАКРЫТЫМ свечам.
#
SWING_LEFT = 2
SWING_RIGHT = 1


# 15M

SWING_LEFT_15M = 2
SWING_RIGHT_15M = 1


# D1

SWING_LEFT_D1 = 3
SWING_RIGHT_D1 = 3


# ============================================================
# LIQUIDITY SETTINGS
# ============================================================

# Расстояние для объединения практически одинаковых
# 1H экстремумов в один кластер.

CLUSTER_DISTANCE_PCT = 0.15


# Ширина визуальной зоны вокруг уровня.

ZONE_WIDTH_PCT = 0.20


# ------------------------------------------------------------
# Количество Major уровней.
# ------------------------------------------------------------
#
# Здесь специально увеличено количество.
#
# Старый вариант мог оставить только несколько уровней,
# из-за чего бот цеплялся за старый уровень.
#
MAX_MAJOR_PER_SIDE = 8
MAX_MAJOR_TOTAL = 16


# ------------------------------------------------------------
# 15M additional levels.
# ------------------------------------------------------------

MAX_15M_LEVELS_PER_SIDE = 4


# ------------------------------------------------------------
# Дедупликация.
# ------------------------------------------------------------
#
# Если 15M почти полностью совпадает с 1H,
# второй уровень не нужен.
#
LEVEL_DUPLICATE_DISTANCE_PCT = 0.05


# ============================================================
# RECENCY / PRIORITY
# ============================================================
#
# Это НЕ фильтр удаления.
#
# Возраст используется только для ранжирования.
#
# Чем свежее подтверждённый 1H swing,
# тем выше его priority.
#
# Это ключевое изменение 7.5.
# ============================================================

FRESH_1H_CANDLES = 3
RECENT_1H_CANDLES = 10
ACTIVE_1H_CANDLES = 30
OLD_1H_CANDLES = 60


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
            pool_connections=12,
            pool_maxsize=12,
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
            # Rate limit
            # ------------------------------------------------

            if response.status_code == 429:

                retry_after = (
                    response.headers.get(
                        "Retry-After"
                    )
                )

                if retry_after is not None:

                    try:
                        wait = float(
                            retry_after
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
            # Binance server errors
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
            "symbol": symbol,
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

            timestamp, data = cached

            if (
                now - timestamp
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
# CONFIRMED CANDLES
# ============================================================
#
# Binance отдаёт последнюю текущую свечу.
#
# Она может быть незакрыта.
#
# Для структуры используем только candles[:-1].
#
# ============================================================

def get_confirmed_candles(
    candles,
):

    if not candles:
        return []

    if len(candles) <= 1:
        return []

    return candles[:-1]


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

    if (
        len(candles)
        <
        left + right + 1
    ):
        return result

    for i in range(
        left,
        len(candles) - right,
    ):

        candle = candles[i]

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
# AGE
# ============================================================

def get_level_age(
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

    return max(
        0,
        age,
    )


# ============================================================
# FRESHNESS SCORE
# ============================================================

def freshness_score(
    level,
    candles,
):

    age = get_level_age(
        level,
        candles,
    )

    # --------------------------------------------------------
    # Fresh
    # --------------------------------------------------------

    if age <= FRESH_1H_CANDLES:
        return 35.0

    # --------------------------------------------------------
    # Recent
    # --------------------------------------------------------

    if age <= RECENT_1H_CANDLES:
        return 28.0

    # --------------------------------------------------------
    # Active
    # --------------------------------------------------------

    if age <= ACTIVE_1H_CANDLES:
        return 21.0

    # --------------------------------------------------------
    # Older
    # --------------------------------------------------------

    if age <= OLD_1H_CANDLES:
        return 13.0

    # --------------------------------------------------------
    # Old but still valid.
    #
    # IMPORTANT:
    # never delete only because of age.
    # --------------------------------------------------------

    return 5.0


# ============================================================
# RECENCY CATEGORY
# ============================================================

def freshness_label(
    age,
):

    if age <= FRESH_1H_CANDLES:
        return "FRESH"

    if age <= RECENT_1H_CANDLES:
        return "RECENT"

    if age <= ACTIVE_1H_CANDLES:
        return "ACTIVE"

    if age <= OLD_1H_CANDLES:
        return "OLD"

    return "VERY_OLD"


# ============================================================
# LOCAL TOUCHES
# ============================================================

def count_local_touches(
    level_price,
    candles,
):

    touches = 0

    for candle in candles:

        high = candle_high(
            candle
        )

        low = candle_low(
            candle
        )

        # Цена реально проходила уровень.

        if (
            low
            <=
            level_price
            <=
            high
        ):

            touches += 1

            continue

        # High близко.

        if (
            distance_pct(
                high,
                level_price,
            )
            <= LOCAL_TOUCH_DISTANCE_PCT
        ):

            touches += 1

            continue

        # Low близко.

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

        # ----------------------------------------------------
        # BSL
        # ----------------------------------------------------

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
                    >=
                    SWEPT_MIN_DEPTH_PCT
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
                    >=
                    SWEPT_MIN_DEPTH_PCT
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

    # --------------------------------------------------------
    # Cluster strength
    # --------------------------------------------------------

    if touches >= 2:
        score += 8

    if touches >= 3:
        score += 7

    if touches >= 4:
        score += 5

    if touches >= 5:
        score += 5

    # --------------------------------------------------------
    # Freshness
    # --------------------------------------------------------

    score += freshness_score(
        level,
        candles,
    )

    # --------------------------------------------------------
    # Local interaction
    # --------------------------------------------------------

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
# LEVEL PRIORITY
# ============================================================
#
# 7.5:
#
# Уровень не удаляется из-за:
# - расстояния;
# - strength;
# - возраста;
# - gap.
#
# Но главный уровень должен быть актуальным.
#
# Priority учитывает:
#
# 1. свежесть;
# 2. расстояние;
# 3. cluster touches;
# 4. strength.
#
# ВАЖНО:
# distance всё ещё важно, но свежий уровень может опередить
# старый уровень, если старый находится лишь немного ближе.
#
# ============================================================

def calculate_priority(
    level,
):

    distance = float(
        level.get(
            "distance_pct",
            999.0,
        )
    )

    strength = float(
        level.get(
            "strength",
            0.0,
        )
    )

    age = int(
        level.get(
            "age_1h",
            999,
        )
    )

    touches = int(
        level.get(
            "touches",
            1,
        )
    )

    source = str(
        level.get(
            "source",
            "1H",
        )
    ).upper()

    # --------------------------------------------------------
    # Recency component
    # --------------------------------------------------------

    if age <= FRESH_1H_CANDLES:
        recency = 100.0

    elif age <= RECENT_1H_CANDLES:
        recency = 85.0

    elif age <= ACTIVE_1H_CANDLES:
        recency = 70.0

    elif age <= OLD_1H_CANDLES:
        recency = 50.0

    else:
        recency = 25.0

    # --------------------------------------------------------
    # Distance component
    #
    # Ближе = лучше.
    #
    # Но distance не должен полностью уничтожать свежесть.
    # --------------------------------------------------------

    distance_component = (
        100.0
        /
        (
            1.0
            +
            distance
        )
    )

    distance_component = min(
        distance_component * 2.0,
        100.0,
    )

    # --------------------------------------------------------
    # Cluster component
    # --------------------------------------------------------

    cluster_component = min(
        40.0
        +
        touches * 10.0,
        100.0,
    )

    # --------------------------------------------------------
    # Strength component
    # --------------------------------------------------------

    strength_component = min(
        strength,
        100.0,
    )

    # --------------------------------------------------------
    # Source bonus
    #
    # 1H Major должен быть выше 15M.
    # --------------------------------------------------------

    source_bonus = 0.0

    if source == "1H":
        source_bonus = 20.0

    elif source == "15M":
        source_bonus = 5.0

    elif source == "ROUND":
        source_bonus = -10.0

    # --------------------------------------------------------
    # Final priority
    # --------------------------------------------------------

    priority = (
        recency * 0.45
        +
        distance_component * 0.30
        +
        cluster_component * 0.10
        +
        strength_component * 0.10
        +
        source_bonus
    )

    return round(
        priority,
        4,
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

        # ----------------------------------------------------
        # BSL above price
        # ----------------------------------------------------

        if level_type == "BSL":

            if level_price <= price:
                continue

        # ----------------------------------------------------
        # SSL below price
        # ----------------------------------------------------

        elif level_type == "SSL":

            if level_price >= price:
                continue

        else:
            continue

        # ----------------------------------------------------
        # Distance.
        #
        # Только информационный параметр.
        #
        # НИКАКОГО MIN_DISTANCE.
        # ----------------------------------------------------

        distance = distance_pct(
            price,
            level_price,
        )

        # ----------------------------------------------------
        # Age.
        #
        # Только для priority.
        #
        # НИКАКОГО MAX_AGE gate.
        # ----------------------------------------------------

        age = get_level_age(
            level,
            candles_ref,
        )

        # ----------------------------------------------------
        # Swept?
        #
        # Это единственный структурный hard gate.
        # ----------------------------------------------------

        swept = level_has_been_swept(
            level_price,
            level_type,
            candles_ref,
            sweep_lookback,
        )

        if swept:
            continue

        # ----------------------------------------------------
        # Strength.
        #
        # НЕ gate.
        # ----------------------------------------------------

        strength = calculate_strength(
            level,
            candles_ref,
            candles_15m,
            max_age,
        )

        # ----------------------------------------------------
        # Zone.
        # ----------------------------------------------------

        zone_low = (
            level_price
            *
            (
                1.0
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
                1.0
                +
                ZONE_WIDTH_PCT
                /
                100.0
            )
        )

        result = {
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

            "freshness": freshness_label(
                age
            ),

            "source": source,

            "status": "FRESH",

            "swept": False,
            "taken": False,
            "used": False,
            "consumed": False,

            "priority": 0.0,
        }

        result["priority"] = calculate_priority(
            result
        )

        candidates.append(
            result
        )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # Сначала priority.
    #
    # Поэтому свежая текущая ликвидность не проигрывает
    # автоматически старому уровню только потому, что
    # старый уровень имеет чуть меньшую дистанцию.
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x: (
            -x.get(
                "priority",
                0.0,
            ),
            x.get(
                "distance_pct",
                999.0,
            ),
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
        - 1
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
                    1.0
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
                    1.0
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

                "freshness": "ROUND",

                "source": "ROUND",

                "status": "FRESH",

                "swept": False,
                "taken": False,
                "used": False,
                "consumed": False,

                "priority": 0.0,
            })

            current += step

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
                    1.0
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
                    1.0
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

                "freshness": "ROUND",

                "source": "ROUND",

                "status": "FRESH",

                "swept": False,
                "taken": False,
                "used": False,
                "consumed": False,

                "priority": 0.0,
            })

            current -= step

    for level in results:

        level["priority"] = (
            calculate_priority(
                level
            )
        )

    return results


# ============================================================
# MERGE SOURCES
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

            # ------------------------------------------------
            # Duplicate check.
            # ------------------------------------------------

            duplicate = any(
                distance_pct(
                    price,
                    existing_price,
                )
                <
                LEVEL_DUPLICATE_DISTANCE_PCT
                for existing_price
                in seen_prices
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
        key=lambda x: (
            -x.get(
                "priority",
                0.0,
            ),
            x.get(
                "distance_pct",
                999.0,
            ),
        )
    )

    return merged


# ============================================================
# BUILD MAJOR LIQUIDITY
# ============================================================

def _build_major_liquidity(
    candles_1h,
    candles_15m,
    price,
):

    if not candles_1h:

        return {
            "BSL": [],
            "SSL": [],
        }

    # ========================================================
    # CONFIRMED 1H
    # ========================================================

    confirmed_1h = get_confirmed_candles(
        candles_1h
    )

    if len(confirmed_1h) < 10:

        return {
            "BSL": [],
            "SSL": [],
        }

    # ========================================================
    # 1H SWINGS
    # ========================================================

    swing_highs_1h = find_swing_highs(
        confirmed_1h
    )

    swing_lows_1h = find_swing_lows(
        confirmed_1h
    )

    # ========================================================
    # CLUSTER 1H
    # ========================================================

    clustered_highs = cluster_levels(
        swing_highs_1h
    )

    clustered_lows = cluster_levels(
        swing_lows_1h
    )

    # ========================================================
    # 1H MAJOR
    # ========================================================

    bsl_1h = _select_zones(
        clustered_highs,
        price,
        confirmed_1h,
        (
            candles_15m
            if candles_15m
            else []
        ),
        "BSL",
        min_strength=None,
        max_age=None,
        sweep_lookback=SWEEP_RECENT_LOOKBACK_1H,
        source="1H",
    )

    ssl_1h = _select_zones(
        clustered_lows,
        price,
        confirmed_1h,
        (
            candles_15m
            if candles_15m
            else []
        ),
        "SSL",
        min_strength=None,
        max_age=None,
        sweep_lookback=SWEEP_RECENT_LOOKBACK_1H,
        source="1H",
    )

    # ========================================================
    # 15M STRUCTURE
    # ========================================================

    bsl_15m = []
    ssl_15m = []

    if candles_15m:

        confirmed_15m = get_confirmed_candles(
            candles_15m
        )

        if len(confirmed_15m) >= 10:

            swing_highs_15m = find_swing_highs_15m(
                confirmed_15m
            )

            swing_lows_15m = find_swing_lows_15m(
                confirmed_15m
            )

            clustered_highs_15m = cluster_levels(
                swing_highs_15m
            )

            clustered_lows_15m = cluster_levels(
                swing_lows_15m
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
    # Round levels are context only.
    #
    # Они НЕ должны вытеснять 1H Major.
    #
    # ========================================================

    bsl_round = find_round_number_levels(
        price,
        "BSL",
    )

    ssl_round = find_round_number_levels(
        price,
        "SSL",
    )

    # ========================================================
    # MERGE
    # ========================================================
    #
    # Приоритет:
    #
    # 1H
    # ↓
    # 15M
    # ↓
    # ROUND
    #
    # ========================================================

    bsl = _merge_sources(
        [
            bsl_1h,
            bsl_15m,
            bsl_round,
        ],
        MAX_MAJOR_PER_SIDE,
    )

    ssl = _merge_sources(
        [
            ssl_1h,
            ssl_15m,
            ssl_round,
        ],
        MAX_MAJOR_PER_SIDE,
    )

    # ========================================================
    # COMBINED LIMIT
    # ========================================================

    combined = (
        [
            ("BSL", x)
            for x in bsl
        ]
        +
        [
            ("SSL", x)
            for x in ssl
        ]
    )

    combined.sort(
        key=lambda x: (
            -x[1].get(
                "priority",
                0.0,
            ),
            x[1].get(
                "distance_pct",
                999.0,
            ),
        )
    )

    combined = combined[
        :MAX_MAJOR_TOTAL
    ]

    result_bsl = [
        level
        for side, level
        in combined
        if side == "BSL"
    ]

    result_ssl = [
        level
        for side, level
        in combined
        if side == "SSL"
    ]

    # --------------------------------------------------------
    # Final side sorting.
    #
    # Для отображения и стратегии:
    # ближайшие актуальные уровни идут первыми.
    # --------------------------------------------------------

    result_bsl.sort(
        key=lambda x: (
            x.get(
                "distance_pct",
                999.0,
            ),
            -x.get(
                "priority",
                0.0,
            ),
        )
    )

    result_ssl.sort(
        key=lambda x: (
            x.get(
                "distance_pct",
                999.0,
            ),
            -x.get(
                "priority",
                0.0,
            ),
        )
    )

    return {
        "BSL": result_bsl,
        "SSL": result_ssl,
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

    levels = (
        liquidity["BSL"]
        +
        liquidity["SSL"]
    )

    levels.sort(
        key=lambda x: (
            x.get(
                "distance_pct",
                999.0,
            ),
            -x.get(
                "priority",
                0.0,
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

        for level in levels:

            level_price = float(
                level.get(
                    "price",
                    0,
                )
            )

            if level_price <= 0:
                continue

            if direction == "LONG":

                if level_price <= entry:
                    continue

            else:

                if level_price >= entry:
                    continue

            if level.get(
                "swept",
                False,
            ):
                continue

            if level.get(
                "taken",
                False,
            ):
                continue

            if level.get(
                "used",
                False,
            ):
                continue

            if level.get(
                "consumed",
                False,
            ):
                continue

            candidates.append(
                level
            )

    else:

        expected_type = (
            "BSL"
            if direction == "LONG"
            else "SSL"
        )

        candidates = []

        for level in major_liquidity:

            if (
                level.get("type")
                != expected_type
            ):
                continue

            level_price = float(
                level.get(
                    "price",
                    0,
                )
            )

            if level_price <= 0:
                continue

            if direction == "LONG":

                if level_price <= entry:
                    continue

            else:

                if level_price >= entry:
                    continue

            if level.get(
                "swept",
                False,
            ):
                continue

            if level.get(
                "taken",
                False,
            ):
                continue

            if level.get(
                "used",
                False,
            ):
                continue

            if level.get(
                "consumed",
                False,
            ):
                continue

            candidates.append(
                level
            )

    if not candidates:
        return None

    # --------------------------------------------------------
    # Target:
    #
    # Ближайшая НЕСНЯТАЯ liquidity.
    #
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Dict
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # List
    # --------------------------------------------------------

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

    valid_levels = [
        x
        for x in normalized
        if (
            x.get("type")
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

    # --------------------------------------------------------
    # Последняя свеча 1H незакрыта.
    # --------------------------------------------------------

    confirmed = get_confirmed_candles(
        candles_1h
    )

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

                # Bullish candle.

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

                # Ближайший к экстремуму level.

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

                    "priority": float(
                        level.get(
                            "priority",
                            0,
                        )
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

                # Bearish candle.

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

                    "priority": float(
                        level.get(
                            "priority",
                            0,
                        )
                    ),
                }

    return None


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

    confirmed = get_confirmed_candles(
        candles_d1
    )

    if len(confirmed) < 15:
        return empty

    swing_highs = find_swing_highs_d1(
        confirmed
    )

    swing_lows = find_swing_lows_d1(
        confirmed
    )

    trend = "NEUTRAL"

    # --------------------------------------------------------
    # Strong structure.
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Soft D1 trend.
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Point A / B.
    # --------------------------------------------------------

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

    if (
        len(candles)
        >
        FVG_MAX_LOOKBACK
    ):

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

        c1 = lookback[
            i - 1
        ]

        c3 = lookback[
            i + 1
        ]

        h1 = candle_high(
            c1
        )

        l1 = candle_low(
            c1
        )

        h3 = candle_high(
            c3
        )

        l3 = candle_low(
            c3
        )

        # ----------------------------------------------------
        # Bullish FVG.
        # ----------------------------------------------------

        if l3 > h1:

            top = l3
            bottom = h1

            fvg_type = "bullish"

        # ----------------------------------------------------
        # Bearish FVG.
        # ----------------------------------------------------

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

        if (
            size_pct
            <
            min_size
        ):
            continue

        # ----------------------------------------------------
        # Filled check.
        # ----------------------------------------------------

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
                lookback[i]
                .get(
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

    # --------------------------------------------------------
    # Сначала свежие.
    # --------------------------------------------------------

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


# ============================================================
# COLLECT FVG
# ============================================================

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
# MARKET DATA
# ============================================================

def get_market_data(
    symbol="SOLUSDT",
):

    symbol = _normalize_symbol(
        symbol
    )

    # ========================================================
    # Параллельная загрузка.
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
    # АКТУАЛЬНАЯ ЦЕНА.
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
    # MAJOR LIQUIDITY.
    # ========================================================

    major_liquidity = (
        _build_major_liquidity(
            candles_1h,
            candles_15m,
            price,
        )
    )

    # ========================================================
    # D1.
    # ========================================================

    d1_context = (
        _analyze_d1_context(
            candles_d1,
            price,
        )
    )

    # ========================================================
    # FVG.
    # ========================================================

    fvgs = collect_fvgs(
        candles_5m,
        candles_15m,
        price,
    )

    # ========================================================
    # STRUCTURE DEBUG.
    # ========================================================

    confirmed_1h = get_confirmed_candles(
        candles_1h
    )

    latest_confirmed_1h = (
        confirmed_1h[-1]
        if confirmed_1h
        else None
    )

    latest_1h_swing_high = None
    latest_1h_swing_low = None

    swing_highs = find_swing_highs(
        confirmed_1h
    )

    swing_lows = find_swing_lows(
        confirmed_1h
    )

    if swing_highs:

        latest_1h_swing_high = (
            swing_highs[-1]
        )

    if swing_lows:

        latest_1h_swing_low = (
            swing_lows[-1]
        )

    # ========================================================
    # RETURN.
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

        "major_liquidity": major_liquidity,

        "d1_context": d1_context,

        "fvgs": fvgs,

        "latest_confirmed_1h": (
            latest_confirmed_1h
        ),

        "latest_1h_swing_high": (
            latest_1h_swing_high
        ),

        "latest_1h_swing_low": (
            latest_1h_swing_low
        ),

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

        "📅 D1 CONTEXT",

        (
            f"Trend: "
            f"{d1.get('trend', 'NEUTRAL')}"
        ),

        (
            f"A: {d1.get('point_a')} "
            f"B: {d1.get('point_b')}"
        ),

        "",
    ]

    # ========================================================
    # BSL
    # ========================================================

    lines.append(
        "🔴 BSL"
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

        for i, level in enumerate(
            bsl,
            1,
        ):

            lines.append(
                f"{i}. "
                f"${level['price']:.6f} "
                f"• "
                f"{level['distance_pct']:.2f}% "
                f"• "
                f"S{level['strength']:.0f} "
                f"• "
                f"{level.get('source', '?')} "
                f"• "
                f"{level.get('freshness', '?')} "
                f"• "
                f"age {level.get('age_1h', 0)}"
            )

    # ========================================================
    # SSL
    # ========================================================

    lines.append("")

    lines.append(
        "🟢 SSL"
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

        for i, level in enumerate(
            ssl,
            1,
        ):

            lines.append(
                f"{i}. "
                f"${level['price']:.6f} "
                f"• "
                f"{level['distance_pct']:.2f}% "
                f"• "
                f"S{level['strength']:.0f} "
                f"• "
                f"{level.get('source', '?')} "
                f"• "
                f"{level.get('freshness', '?')} "
                f"• "
                f"age {level.get('age_1h', 0)}"
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

        for fvg in fvgs:

            icon = (
                "🟢"
                if fvg["type"]
                ==
                "bullish"
                else
                "🔴"
            )

            lines.append(
                f"{icon} "
                f"{fvg['tf'].upper()} "
                f"${fvg['bottom']:.6f}"
                f"–"
                f"${fvg['top']:.6f} "
                f"("
                f"{fvg['size_pct']:.3f}%"
                f") "
                f"• "
                f"{fvg.get('distance_pct', 0):.2f}%"
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
    print("=" * 80)

    print(
        f"TradeMind Market "
        f"{MARKET_VERSION}"
    )

    print(
        f"Symbol: {symbol}"
    )

    print("=" * 80)

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
            get_confirmed_candles(
                data["candles_1h"]
            )
        )

        highs = find_swing_highs(
            confirmed_1h
        )

        lows = find_swing_lows(
            confirmed_1h
        )

        print(
            f"BSL swings: "
            f"{len(highs)}"
        )

        for level in highs[-15:]:

            age = (
                len(confirmed_1h)
                -
                1
                -
                level["index"]
            )

            print(
                f"  BSL "
                f"${level['price']:.6f} "
                f"index={level['index']} "
                f"age={age}"
            )

        print(
            f"SSL swings: "
            f"{len(lows)}"
        )

        for level in lows[-15:]:

            age = (
                len(confirmed_1h)
                -
                1
                -
                level["index"]
            )

            print(
                f"  SSL "
                f"${level['price']:.6f} "
                f"index={level['index']} "
                f"age={age}"
            )

        print("")
        print(
            "LATEST CONFIRMED 1H:"
        )

        latest = data.get(
            "latest_confirmed_1h"
        )

        if latest:

            print(
                f"open=${latest['open']:.6f} "
                f"high=${latest['high']:.6f} "
                f"low=${latest['low']:.6f} "
                f"close=${latest['close']:.6f} "
                f"time={latest['open_time']}"
            )

        else:

            print(
                "— нет"
            )

        print("")
        print(
            "LATEST 1H SWING HIGH:"
        )

        latest_high = data.get(
            "latest_1h_swing_high"
        )

        print(
            latest_high
        )

        print("")
        print(
            "LATEST 1H SWING LOW:"
        )

        latest_low = data.get(
            "latest_1h_swing_low"
        )

        print(
            latest_low
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
            "MAJOR BSL:",
            len(
                data.get(
                    "major_liquidity",
                    {}
                ).get(
                    "BSL",
                    []
                )
            )
        )

        print(
            "MAJOR SSL:",
            len(
                data.get(
                    "major_liquidity",
                    {}
                ).get(
                    "SSL",
                    []
                )
            )
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