import requests
import time
from typing import Any, Dict, List, Optional


# ============================================================
# TRADEMIND MARKET 6.2.1
# BINANCE SPOT
# 1H MAJOR LIQUIDITY + LOCAL LIQUIDITY
# ============================================================

MARKET_VERSION = "6.2.1"

BASE_URL = "https://api.binance.com/api/v3"

DEFAULT_SYMBOL = "SOLUSDT"

REQUEST_TIMEOUT = 10


# ============================================================
# LIQUIDITY CONFIG
# ============================================================

# Максимальная дистанция между swing-точками
# для объединения в один liquidity cluster.
LEVEL_CLUSTER_PCT = 0.35

# Минимум касаний для MAJOR liquidity.
MIN_MAJOR_TOUCHES = 2

# Отдельный лимит для каждой стороны.
MAX_MAJOR_LEVELS = 20
MAX_LEVELS_EACH_SIDE = 20

# Основной lookback 1H.
LIQUIDITY_LOOKBACK = 160

# Сколько последних 1H свечей проверять для нового sweep.
SWEEP_LOOKBACK_1H = 8

# Минимальная глубина sweep.
MIN_SWEEP_DEPTH_PCT = 0.08

# Минимальный размер локального кластера.
MIN_LOCAL_TOUCHES = 2

# Максимальная ширина гибридной зоны.
HYBRID_ZONE_MAX_PCT = 0.40

# Local liquidity.
LOCAL_LOOKBACK_15M = 120
LOCAL_LOOKBACK_5M = 160
LOCAL_LOOKBACK_1M = 180


# ============================================================
# HTTP
# ============================================================

def _get(
    path: str,
    params: Dict[str, Any],
):

    response = requests.get(
        BASE_URL + path,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# PRICE
# ============================================================

def get_price(
    symbol: str = DEFAULT_SYMBOL,
) -> float:

    data = _get(
        "/ticker/price",
        {
            "symbol": symbol,
        },
    )

    return float(
        data["price"]
    )


# ============================================================
# KLINES
# ============================================================

def get_klines(
    symbol: str = DEFAULT_SYMBOL,
    interval: str = "1h",
    limit: int = 200,
) -> List[Dict[str, Any]]:

    raw = _get(
        "/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        },
    )

    candles = []

    for row in raw:

        candles.append({

            "open_time":
                int(row[0]),

            "open":
                float(row[1]),

            "high":
                float(row[2]),

            "low":
                float(row[3]),

            "close":
                float(row[4]),

            "volume":
                float(row[5]),

            "close_time":
                int(row[6]),

        })

    # --------------------------------------------------------
    # Удаляем формирующуюся свечу.
    # --------------------------------------------------------

    if len(candles) > 1:

        now_ms = int(
            time.time() * 1000
        )

        if (
            candles[-1]["close_time"]
            > now_ms
        ):

            candles = candles[:-1]

    return candles


# ============================================================
# FULL MARKET DATA
# ============================================================

def get_market_data(
    symbol: str = DEFAULT_SYMBOL,
):

    price = get_price(symbol)

    candles_1h = get_klines(
        symbol,
        "1h",
        200,
    )

    candles_15m = get_klines(
        symbol,
        "15m",
        200,
    )

    candles_5m = get_klines(
        symbol,
        "5m",
        200,
    )

    candles_1m = get_klines(
        symbol,
        "1m",
        200,
    )

    return {

        "symbol":
            symbol,

        "price":
            price,

        # ----------------------------------------------------
        # D1/W1 намеренно отсутствуют.
        # ----------------------------------------------------

        "candles_1h":
            candles_1h,

        "candles_15m":
            candles_15m,

        "candles_5m":
            candles_5m,

        "candles_1m":
            candles_1m,

    }


# ============================================================
# SAFE NUMBER
# ============================================================

def _safe_float(
    value,
    default: Optional[float] = None,
):

    try:

        if value is None:
            return default

        return float(value)

    except Exception:

        return default


# ============================================================
# PERCENT DISTANCE
# ============================================================

def _pct_distance(
    a: float,
    b: float,
) -> float:

    if b in (
        None,
        0,
    ):

        return 999.0

    return (
        abs(a - b)
        /
        abs(b)
        *
        100.0
    )


# ============================================================
# LOCAL SWING HIGH
# ============================================================

def _local_swing_high(
    candles,
    index: int,
) -> bool:

    if not candles:
        return False

    if index <= 0:
        return False

    if index >= len(candles) - 1:
        return False

    current = _safe_float(
        candles[index].get("high")
    )

    left = _safe_float(
        candles[index - 1].get("high")
    )

    right = _safe_float(
        candles[index + 1].get("high")
    )

    if None in (
        current,
        left,
        right,
    ):

        return False

    return (
        current > left
        and current >= right
    )


# ============================================================
# LOCAL SWING LOW
# ============================================================

def _local_swing_low(
    candles,
    index: int,
) -> bool:

    if not candles:
        return False

    if index <= 0:
        return False

    if index >= len(candles) - 1:
        return False

    current = _safe_float(
        candles[index].get("low")
    )

    left = _safe_float(
        candles[index - 1].get("low")
    )

    right = _safe_float(
        candles[index + 1].get("low")
    )

    if None in (
        current,
        left,
        right,
    ):

        return False

    return (
        current < left
        and current <= right
    )


# ============================================================
# RAW SWINGS
# ============================================================

def _collect_swings(
    candles,
):

    result = []

    if not candles:
        return result

    for i in range(
        1,
        len(candles) - 1,
    ):

        candle = candles[i]

        if _local_swing_high(
            candles,
            i,
        ):

            high = _safe_float(
                candle.get("high")
            )

            if high is not None:

                result.append({

                    "type":
                        "HIGH",

                    "price":
                        high,

                    "index":
                        i,

                    "time":
                        candle.get(
                            "open_time"
                        ),

                })

        if _local_swing_low(
            candles,
            i,
        ):

            low = _safe_float(
                candle.get("low")
            )

            if low is not None:

                result.append({

                    "type":
                        "LOW",

                    "price":
                        low,

                    "index":
                        i,

                    "time":
                        candle.get(
                            "open_time"
                        ),

                })

    return result


# ============================================================
# CLUSTER LEVELS
# ============================================================

def _cluster_levels(
    items,
):

    if not items:
        return []

    result = []

    for side in (
        "HIGH",
        "LOW",
    ):

        side_items = [

            item

            for item in items

            if item.get("type")
            == side

        ]

        side_items.sort(
            key=lambda x:
                x["price"]
        )

        clusters = []

        for item in side_items:

            placed = False

            for cluster in clusters:

                distance = _pct_distance(

                    item["price"],

                    cluster["price"],

                )

                if (
                    distance
                    <= LEVEL_CLUSTER_PCT
                ):

                    cluster[
                        "members"
                    ].append(item)

                    prices = [

                        x["price"]

                        for x
                        in cluster[
                            "members"
                        ]

                    ]

                    cluster["price"] = (
                        sum(prices)
                        /
                        len(prices)
                    )

                    cluster[
                        "last_index"
                    ] = max(

                        cluster[
                            "last_index"
                        ],

                        item[
                            "index"
                        ],

                    )

                    cluster[
                        "first_index"
                    ] = min(

                        cluster[
                            "first_index"
                        ],

                        item[
                            "index"
                        ],

                    )

                    placed = True

                    break

            if not placed:

                clusters.append({

                    "type":
                        side,

                    "price":
                        item["price"],

                    "members":
                        [item],

                    "last_index":
                        item["index"],

                    "first_index":
                        item["index"],

                })

        result.extend(
            clusters
        )

    return result


# ============================================================
# CLUSTER SWEEP
# ============================================================

def _cluster_sweep_info(
    data,
    cluster,
):

    price = _safe_float(
        cluster.get("price")
    )

    if price is None:

        return {

            "swept":
                False,

            "sweep_candle":
                None,

            "sweep_index":
                None,

        }

    last_index = int(
        cluster.get(
            "last_index",
            0,
        )
    )

    for i in range(
        last_index + 1,
        len(data),
    ):

        candle = data[i]

        high = _safe_float(
            candle.get("high")
        )

        low = _safe_float(
            candle.get("low")
        )

        if cluster.get(
            "type"
        ) == "HIGH":

            if (
                high is not None
                and high > price
            ):

                return {

                    "swept":
                        True,

                    "sweep_candle":
                        candle,

                    "sweep_index":
                        i,

                }

        else:

            if (
                low is not None
                and low < price
            ):

                return {

                    "swept":
                        True,

                    "sweep_candle":
                        candle,

                    "sweep_index":
                        i,

                }

    return {

        "swept":
            False,

        "sweep_candle":
            None,

        "sweep_index":
            None,

    }


# ============================================================
# BUILD MAJOR LEVEL
# ============================================================

def _build_level(
    cluster,
    current_price,
    data,
):

    price = _safe_float(
        cluster.get("price")
    )

    if price is None:
        return None

    touches = len(
        cluster.get(
            "members",
            [],
        )
    )

    latest_member = max(

        cluster[
            "members"
        ],

        key=lambda x:
            x.get(
                "index",
                0,
            ),

    )

    level_time = (
        latest_member.get(
            "time"
        )
    )

    sweep_info = _cluster_sweep_info(
        data,
        cluster,
    )

    swept = sweep_info[
        "swept"
    ]

    sweep_candle = sweep_info[
        "sweep_candle"
    ]

    sweep_index = sweep_info[
        "sweep_index"
    ]

    # --------------------------------------------------------
    # Base strength.
    # --------------------------------------------------------

    strength = min(

        1.0,

        0.40
        +
        0.15
        *
        max(
            0,
            touches - 1,
        ),

    )

    distance_pct = _pct_distance(

        price,

        current_price,

    )

    # ========================================================
    # BSL
    # ========================================================

    if (
        cluster["type"]
        == "HIGH"
        and price > current_price
    ):

        return {

            "price":
                round(
                    price,
                    8,
                ),

            "level":
                round(
                    price,
                    8,
                ),

            "type":
                "HIGH",

            "side":
                "SHORT",

            "position":
                "ABOVE",

            "liquidity_type":
                "BSL / 1H major swing high",

            "source":
                "1H",

            "touches":
                touches,

            "cluster_size":
                touches,

            "strength":
                round(
                    strength,
                    3,
                ),

            "distance_pct":
                round(
                    distance_pct,
                    4,
                ),

            "time":
                level_time,

            "created_index":
                cluster[
                    "first_index"
                ],

            "last_touch_index":
                cluster[
                    "last_index"
                ],

            "swept":
                swept,

            "sweep_candle":
                sweep_candle,

            "sweep_index":
                sweep_index,

        }

    # ========================================================
    # SSL
    # ========================================================

    if (
        cluster["type"]
        == "LOW"
        and price < current_price
    ):

        return {

            "price":
                round(
                    price,
                    8,
                ),

            "level":
                round(
                    price,
                    8,
                ),

            "type":
                "LOW",

            "side":
                "LONG",

            "position":
                "BELOW",

            "liquidity_type":
                "SSL / 1H major swing low",

            "source":
                "1H",

            "touches":
                touches,

            "cluster_size":
                touches,

            "strength":
                round(
                    strength,
                    3,
                ),

            "distance_pct":
                round(
                    distance_pct,
                    4,
                ),

            "time":
                level_time,

            "created_index":
                cluster[
                    "first_index"
                ],

            "last_touch_index":
                cluster[
                    "last_index"
                ],

            "swept":
                swept,

            "sweep_candle":
                sweep_candle,

            "sweep_index":
                sweep_index,

        }

    return None


# ============================================================
# FIND MAJOR LIQUIDITY
# ============================================================

def find_major_liquidity(
    candles_1h,
    current_price,
    max_levels=MAX_MAJOR_LEVELS,
    include_swept=False,
    candles_15m=None,
    candles_5m=None,
    candles_1m=None,
):

    if (
        not candles_1h
        or len(candles_1h) < 15
    ):

        return []

    current_price = _safe_float(
        current_price
    )

    if current_price is None:
        return []

    data = candles_1h[
        -LIQUIDITY_LOOKBACK:
    ]

    raw = _collect_swings(
        data
    )

    if not raw:
        return []

    clusters = _cluster_levels(
        raw
    )

    above = []
    below = []

    for cluster in clusters:

        touches = len(
            cluster.get(
                "members",
                [],
            )
        )

        if touches < MIN_MAJOR_TOUCHES:
            continue

        level = _build_level(

            cluster,

            current_price,

            data,

        )

        if level is None:
            continue

        if (
            level["swept"]
            and not include_swept
        ):
            continue

        # ----------------------------------------------------
        # Hybrid lower timeframe confirmation.
        # ----------------------------------------------------

        local_bonus = _local_liquidity_bonus(

            level,

            candles_15m,

            candles_5m,

            candles_1m,

        )

        if local_bonus:

            level[
                "local_bonus"
            ] = local_bonus

            level[
                "strength"
            ] = round(

                min(

                    1.0,

                    level[
                        "strength"
                    ]
                    +
                    local_bonus[
                        "bonus"
                    ],

                ),

                3,

            )

            level[
                "local_touches"
            ] = local_bonus[
                "touches"
            ]

            level[
                "zone_low"
            ] = local_bonus[
                "zone_low"
            ]

            level[
                "zone_high"
            ] = local_bonus[
                "zone_high"
            ]

        else:

            level[
                "local_bonus"
            ] = None

        if level[
            "position"
        ] == "ABOVE":

            above.append(level)

        elif level[
            "position"
        ] == "BELOW":

            below.append(level)

    # --------------------------------------------------------
    # Separate sorting.
    # --------------------------------------------------------

    above.sort(

        key=lambda x: (

            x["distance_pct"],

            -x["touches"],

            -x["strength"],

        )

    )

    below.sort(

        key=lambda x: (

            x["distance_pct"],

            -x["touches"],

            -x["strength"],

        )

    )

    above = above[
        :MAX_LEVELS_EACH_SIDE
    ]

    below = below[
        :MAX_LEVELS_EACH_SIDE
    ]

    return (
        above
        +
        below
    )


# ============================================================
# LOCAL LIQUIDITY BONUS
# ============================================================

def _local_liquidity_bonus(
    major_level,
    candles_15m=None,
    candles_5m=None,
    candles_1m=None,
):

    price = _safe_float(
        major_level.get("price")
    )

    if price is None:
        return None

    sources = []

    if candles_15m:
        sources.append(
            (
                "15M",
                candles_15m[
                    -LOCAL_LOOKBACK_15M:
                ],
            )
        )

    if candles_5m:
        sources.append(
            (
                "5M",
                candles_5m[
                    -LOCAL_LOOKBACK_5M:
                ],
            )
        )

    if candles_1m:
        sources.append(
            (
                "1M",
                candles_1m[
                    -LOCAL_LOOKBACK_1M:
                ],
            )
        )

    if not sources:
        return None

    all_prices = []

    source_counts = {}

    for timeframe, candles in sources:

        count = 0

        for i in range(
            1,
            len(candles) - 1,
        ):

            candle = candles[i]

            candidate = None

            if (
                major_level[
                    "position"
                ]
                == "ABOVE"
            ):

                if _local_swing_high(
                    candles,
                    i,
                ):

                    candidate = _safe_float(
                        candle.get(
                            "high"
                        )
                    )

            else:

                if _local_swing_low(
                    candles,
                    i,
                ):

                    candidate = _safe_float(
                        candle.get(
                            "low"
                        )
                    )

            if candidate is None:
                continue

            distance = _pct_distance(
                candidate,
                price,
            )

            # Local liquidity must actually be near
            # the major area.
            if distance <= HYBRID_ZONE_MAX_PCT:

                all_prices.append(
                    candidate
                )

                count += 1

        source_counts[
            timeframe
        ] = count

    if len(all_prices) < MIN_LOCAL_TOUCHES:
        return None

    center = (
        sum(all_prices)
        /
        len(all_prices)
    )

    zone_half = (
        center
        *
        HYBRID_ZONE_MAX_PCT
        /
        100.0
    )

    zone_low = center - zone_half
    zone_high = center + zone_half

    # --------------------------------------------------------
    # Do not allow the hybrid zone to become excessive.
    # --------------------------------------------------------

    max_width = (
        price
        *
        HYBRID_ZONE_MAX_PCT
        /
        100.0
    )

    if (
        zone_high
        -
        zone_low
        >
        max_width
    ):

        zone_low = (
            center
            -
            max_width / 2.0
        )

        zone_high = (
            center
            +
            max_width / 2.0
        )

    bonus = min(

        0.25,

        0.03
        *
        len(all_prices),

    )

    return {

        "touches":
            len(all_prices),

        "bonus":
            bonus,

        "zone_low":
            round(
                zone_low,
                8,
            ),

        "zone_high":
            round(
                zone_high,
                8,
            ),

        "source_counts":
            source_counts,

    }


# ============================================================
# FRESH LIQUIDITY
# ============================================================

def get_fresh_liquidity(
    candles_1h,
    current_price,
    max_levels=MAX_MAJOR_LEVELS,
    candles_15m=None,
    candles_5m=None,
    candles_1m=None,
):

    return find_major_liquidity(

        candles_1h,

        current_price,

        max_levels=max_levels,

        include_swept=False,

        candles_15m=candles_15m,

        candles_5m=candles_5m,

        candles_1m=candles_1m,

    )


# ============================================================
# ALL MAJOR LIQUIDITY
# ============================================================

def get_all_major_liquidity(
    candles_1h,
    current_price,
    max_levels=MAX_MAJOR_LEVELS,
    candles_15m=None,
    candles_5m=None,
    candles_1m=None,
):

    return find_major_liquidity(

        candles_1h,

        current_price,

        max_levels=max_levels,

        include_swept=True,

        candles_15m=candles_15m,

        candles_5m=candles_5m,

        candles_1m=candles_1m,

    )


# ============================================================
# TARGET LIQUIDITY
# ============================================================

def get_target_liquidity(
    candles_1h,
    entry,
    direction,
    max_levels=MAX_MAJOR_LEVELS,
):

    entry = _safe_float(
        entry
    )

    if entry is None:
        return None

    levels = get_fresh_liquidity(

        candles_1h,

        entry,

        max_levels=max_levels,

    )

    candidates = []

    for level in levels:

        if level.get(
            "swept"
        ) is True:
            continue

        price = _safe_float(
            level.get("price")
        )

        if price is None:
            continue

        if direction == "LONG":

            if price > entry:

                candidates.append(
                    level
                )

        elif direction == "SHORT":

            if price < entry:

                candidates.append(
                    level
                )

    if not candidates:
        return None

    if direction == "LONG":

        candidates.sort(
            key=lambda x:
                x["price"]
        )

    else:

        candidates.sort(

            key=lambda x:
                x["price"],

            reverse=True,

        )

    return candidates[0]


# ============================================================
# DETECT SWEEP
# ============================================================

def detect_sweep(
    candles_1h,
    current_price,
    direction,
):

    if (
        not candles_1h
        or direction not in {
            "LONG",
            "SHORT",
        }
    ):

        return None

    current_price = _safe_float(
        current_price
    )

    if current_price is None:
        return None

    levels = find_major_liquidity(

        candles_1h,

        current_price,

        max_levels=MAX_MAJOR_LEVELS,

        include_swept=True,

    )

    if not levels:
        return None

    data = candles_1h[
        -SWEEP_LOOKBACK_1H:
    ]

    # --------------------------------------------------------
    # Newest first.
    # --------------------------------------------------------

    for candle_offset in range(
        len(data) - 1,
        -1,
        -1,
    ):

        candle = data[
            candle_offset
        ]

        high = _safe_float(
            candle.get("high")
        )

        low = _safe_float(
            candle.get("low")
        )

        open_price = _safe_float(
            candle.get("open")
        )

        close = _safe_float(
            candle.get("close")
        )

        if None in (
            high,
            low,
            open_price,
            close,
        ):
            continue

        # ====================================================
        # LONG
        # ====================================================

        if direction == "LONG":

            ssl_levels = [

                x

                for x in levels

                if x.get(
                    "side"
                )
                == "LONG"

            ]

            # Ближайшая major SSL
            ssl_levels.sort(

                key=lambda x:
                    abs(
                        x["price"]
                        -
                        current_price
                    )

            )

            for level_data in ssl_levels:

                level = _safe_float(
                    level_data.get(
                        "price"
                    )
                )

                if level is None:
                    continue

                depth_pct = (

                    (
                        level
                        -
                        low
                    )
                    /
                    level
                    *
                    100.0

                    if level
                    else 0.0

                )

                swept = (

                    low < level

                    and

                    depth_pct
                    >= MIN_SWEEP_DEPTH_PCT

                )

                rejection = (

                    close > level

                    and

                    close > open_price

                )

                if swept and rejection:

                    return {

                        "swept":
                            True,

                        "direction":
                            "LONG",

                        "level":
                            level,

                        "extreme":
                            low,

                        "time":
                            candle.get(
                                "open_time"
                            ),

                        "candle_index":
                            candle_offset,

                        "strength":
                            level_data.get(
                                "strength",
                                0,
                            ),

                        "touches":
                            level_data.get(
                                "touches",
                                0,
                            ),

                        "liquidity_type":
                            "SSL",

                        "type":
                            "SSL SWEEP",

                        "depth_pct":
                            round(
                                depth_pct,
                                4,
                            ),

                        "body_close":
                            close,

                    }

        # ====================================================
        # SHORT
        # ====================================================

        else:

            bsl_levels = [

                x

                for x in levels

                if x.get(
                    "side"
                )
                == "SHORT"

            ]

            bsl_levels.sort(

                key=lambda x:
                    abs(
                        x["price"]
                        -
                        current_price
                    )

            )

            for level_data in bsl_levels:

                level = _safe_float(
                    level_data.get(
                        "price"
                    )
                )

                if level is None:
                    continue

                depth_pct = (

                    (
                        high
                        -
                        level
                    )
                    /
                    level
                    *
                    100.0

                    if level
                    else 0.0

                )

                swept = (

                    high > level

                    and

                    depth_pct
                    >= MIN_SWEEP_DEPTH_PCT

                )

                rejection = (

                    close < level

                    and

                    close < open_price

                )

                if swept and rejection:

                    return {

                        "swept":
                            True,

                        "direction":
                            "SHORT",

                        "level":
                            level,

                        "extreme":
                            high,

                        "time":
                            candle.get(
                                "open_time"
                            ),

                        "candle_index":
                            candle_offset,

                        "strength":
                            level_data.get(
                                "strength",
                                0,
                            ),

                        "touches":
                            level_data.get(
                                "touches",
                                0,
                            ),

                        "liquidity_type":
                            "BSL",

                        "type":
                            "BSL SWEEP",

                        "depth_pct":
                            round(
                                depth_pct,
                                4,
                            ),

                        "body_close":
                            close,

                    }

    return None


# ============================================================
# COMPATIBILITY
# ============================================================

get_major_liquidity = (
    find_major_liquidity
)


# ============================================================
# EXPORTS
# ============================================================

__all__ = [

    "MARKET_VERSION",

    "get_price",

    "get_klines",

    "get_market_data",

    "find_major_liquidity",

    "get_major_liquidity",

    "get_fresh_liquidity",

    "get_all_major_liquidity",

    "get_target_liquidity",

    "detect_sweep",

]