import requests
import time


# ============================================================
# TRADEMIND MARKET 6.1.0
# ============================================================

MARKET_VERSION = "6.1.0"

BASE_URL = "https://api.binance.com/api/v3"

DEFAULT_SYMBOL = "SOLUSDT"

REQUEST_TIMEOUT = 10


# ============================================================
# LIQUIDITY CONFIG
# ============================================================

# Допустимое расстояние между swing-high / swing-low
# для объединения их в один кластер.
LEVEL_CLUSTER_PCT = 0.35

# Минимальное количество касаний,
# чтобы уровень считался MAJOR.
MIN_MAJOR_TOUCHES = 2

# Внутреннее количество уровней.
# Наружу можно вернуть больше 6, чтобы стратегия
# всегда могла найти следующий TP.
MAX_MAJOR_LEVELS = 20

# Сколько 1H свечей анализируем.
LIQUIDITY_LOOKBACK = 120

# Сколько последних 1H свечей проверяем на Sweep.
SWEEP_LOOKBACK_1H = 6

# Минимальная глубина Sweep.
MIN_SWEEP_DEPTH_PCT = 0.08


# ============================================================
# HTTP
# ============================================================

def _get(path, params):

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

def get_price(symbol=DEFAULT_SYMBOL):

    data = _get(
        "/ticker/price",
        {
            "symbol": symbol,
        },
    )

    return float(data["price"])


# ============================================================
# KLINES
# ============================================================

def get_klines(
    symbol=DEFAULT_SYMBOL,
    interval="1h",
    limit=200,
):

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
            "open_time": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "close_time": int(row[6]),
        })

    # --------------------------------------------------------
    # Remove currently forming candle.
    # --------------------------------------------------------

    if len(candles) > 1:

        now_ms = int(
            time.time() * 1000
        )

        if candles[-1]["close_time"] > now_ms:

            candles = candles[:-1]

    return candles


# ============================================================
# FULL MARKET DATA
# ============================================================

def get_market_data(
    symbol=DEFAULT_SYMBOL,
):

    price = get_price(symbol)

    return {

        "symbol": symbol,

        "price": price,

        "candles_d1": get_klines(
            symbol,
            "1d",
            200,
        ),

        "candles_w1": get_klines(
            symbol,
            "1w",
            100,
        ),

        "candles_1h": get_klines(
            symbol,
            "1h",
            200,
        ),

        "candles_15m": get_klines(
            symbol,
            "15m",
            200,
        ),

        "candles_5m": get_klines(
            symbol,
            "5m",
            200,
        ),
    }


# ============================================================
# SWING HIGH
# ============================================================

def _local_swing_high(
    candles,
    index,
):

    if not candles:
        return False

    if index <= 0:
        return False

    if index >= len(candles) - 1:
        return False

    current = candles[index]["high"]

    left = candles[index - 1]["high"]

    right = candles[index + 1]["high"]

    return (
        current > left
        and current >= right
    )


# ============================================================
# SWING LOW
# ============================================================

def _local_swing_low(
    candles,
    index,
):

    if not candles:
        return False

    if index <= 0:
        return False

    if index >= len(candles) - 1:
        return False

    current = candles[index]["low"]

    left = candles[index - 1]["low"]

    right = candles[index + 1]["low"]

    return (
        current < left
        and current <= right
    )


# ============================================================
# PERCENT DISTANCE
# ============================================================

def _pct_distance(
    a,
    b,
):

    if b in (
        None,
        0,
    ):
        return 999.0

    return (
        abs(a - b)
        / abs(b)
        * 100.0
    )


# ============================================================
# CLUSTER LIQUIDITY
# ============================================================

def _cluster_levels(items):

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
            if item["type"] == side
        ]

        side_items.sort(
            key=lambda x: x["price"]
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

                    cluster["price"] = (
                        sum(
                            x["price"]
                            for x
                            in cluster[
                                "members"
                            ]
                        )
                        /
                        len(
                            cluster[
                                "members"
                            ]
                        )
                    )

                    cluster["last_index"] = max(
                        cluster["last_index"],
                        item["index"],
                    )

                    placed = True

                    break

            if not placed:

                clusters.append({

                    "type": side,

                    "price": item["price"],

                    "members": [item],

                    "last_index": item["index"],

                })

        result.extend(
            clusters
        )

    return result


# ============================================================
# DETECT IF CLUSTER WAS SWEPT
# ============================================================

def _cluster_sweep_info(
    data,
    cluster,
):

    price = cluster["price"]

    last_index = cluster["last_index"]

    sweep_candle = None

    sweep_index = None

    # IMPORTANT:
    # We look chronologically after the last
    # member of the cluster.

    for i in range(
        last_index + 1,
        len(data),
    ):

        candle = data[i]

        if cluster["type"] == "HIGH":

            if candle["high"] > price:

                sweep_candle = candle

                sweep_index = i

                break

        else:

            if candle["low"] < price:

                sweep_candle = candle

                sweep_index = i

                break

    if sweep_candle is None:

        return {
            "swept": False,
            "sweep_candle": None,
            "sweep_index": None,
        }

    return {
        "swept": True,
        "sweep_candle": sweep_candle,
        "sweep_index": sweep_index,
    }


# ============================================================
# FIND MAJOR LIQUIDITY
# ============================================================

def find_major_liquidity(
    candles_1h,
    current_price,
    max_levels=MAX_MAJOR_LEVELS,
    include_swept=False,
):

    if (
        not candles_1h
        or len(candles_1h) < 15
    ):
        return []

    current_price = float(
        current_price
    )

    # --------------------------------------------------------
    # Use latest 1H history.
    # --------------------------------------------------------

    data = candles_1h[
        -LIQUIDITY_LOOKBACK:
    ]

    if len(data) < 3:
        return []

    # --------------------------------------------------------
    # Collect raw swing points.
    # --------------------------------------------------------

    raw = []

    for i in range(
        1,
        len(data) - 1,
    ):

        if _local_swing_high(
            data,
            i,
        ):

            raw.append({

                "type": "HIGH",

                "price": data[i]["high"],

                "index": i,

                "time": data[i]["open_time"],

            })

        if _local_swing_low(
            data,
            i,
        ):

            raw.append({

                "type": "LOW",

                "price": data[i]["low"],

                "index": i,

                "time": data[i]["open_time"],

            })

    if not raw:
        return []

    # --------------------------------------------------------
    # Cluster nearby swing points.
    # --------------------------------------------------------

    clusters = _cluster_levels(
        raw
    )

    candidates = []

    for cluster in clusters:

        price = cluster["price"]

        touches = len(
            cluster["members"]
        )

        # ----------------------------------------------------
        # Major filter.
        # ----------------------------------------------------

        if touches < MIN_MAJOR_TOUCHES:
            continue

        # ----------------------------------------------------
        # Sweep status.
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # If already swept, it is not fresh liquidity.
        # ----------------------------------------------------

        if (
            swept
            and not include_swept
        ):
            continue

        # ----------------------------------------------------
        # Strength.
        # ----------------------------------------------------

        strength = min(
            1.0,
            0.45
            + 0.15
            * max(
                0,
                touches - 1,
            ),
        )

        # ----------------------------------------------------
        # Latest member time.
        # ----------------------------------------------------

        latest_member = max(
            cluster["members"],
            key=lambda x: x["index"],
        )

        level_time = latest_member[
            "time"
        ]

        # ====================================================
        # HIGH / BSL
        # ====================================================

        if (
            cluster["type"] == "HIGH"
            and price > current_price
        ):

            candidates.append({

                "price": round(
                    price,
                    8,
                ),

                "level": round(
                    price,
                    8,
                ),

                "type": "HIGH",

                "side": "SHORT",

                "liquidity_type":
                    "BSL / 1H major swing high",

                "touches": touches,

                "strength": round(
                    strength,
                    3,
                ),

                "time": level_time,

                "swept": swept,

                "sweep_candle":
                    sweep_candle,

                "sweep_index":
                    sweep_index,

            })

        # ====================================================
        # LOW / SSL
        # ====================================================

        if (
            cluster["type"] == "LOW"
            and price < current_price
        ):

            candidates.append({

                "price": round(
                    price,
                    8,
                ),

                "level": round(
                    price,
                    8,
                ),

                "type": "LOW",

                "side": "LONG",

                "liquidity_type":
                    "SSL / 1H major swing low",

                "touches": touches,

                "strength": round(
                    strength,
                    3,
                ),

                "time": level_time,

                "swept": swept,

                "sweep_candle":
                    sweep_candle,

                "sweep_index":
                    sweep_index,

            })

    # --------------------------------------------------------
    # Sort by distance from current price.
    #
    # This is important for TP:
    # the next major liquidity should be the
    # nearest valid unswept major level.
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x: (
            _pct_distance(
                x["price"],
                current_price,
            ),

            -x["touches"],

            -x["strength"],

        )
    )

    return candidates[
        :max_levels
    ]


# ============================================================
# GET FRESH LIQUIDITY
# ============================================================

def get_fresh_liquidity(
    candles_1h,
    current_price,
    max_levels=MAX_MAJOR_LEVELS,
):

    return find_major_liquidity(

        candles_1h,

        current_price,

        max_levels=max_levels,

        include_swept=False,

    )


# ============================================================
# GET ALL MAJOR LIQUIDITY
# ============================================================

def get_all_major_liquidity(
    candles_1h,
    current_price,
    max_levels=MAX_MAJOR_LEVELS,
):

    return find_major_liquidity(

        candles_1h,

        current_price,

        max_levels=max_levels,

        include_swept=True,

    )


# ============================================================
# GET TARGET LIQUIDITY
# ============================================================

def get_target_liquidity(
    candles_1h,
    entry,
    direction,
    max_levels=MAX_MAJOR_LEVELS,
):

    entry = float(entry)

    levels = get_fresh_liquidity(

        candles_1h,

        entry,

        max_levels=max_levels,

    )

    candidates = []

    for level in levels:

        price = level["price"]

        if level.get("swept") is True:
            continue

        if direction == "LONG":

            if price > entry:

                candidates.append(level)

        elif direction == "SHORT":

            if price < entry:

                candidates.append(level)

    if not candidates:
        return None

    if direction == "LONG":

        candidates.sort(
            key=lambda x: x["price"]
        )

    else:

        candidates.sort(
            key=lambda x: x["price"],
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

    # --------------------------------------------------------
    # We need ALL levels here because the level which
    # produced the sweep is, by definition, already swept.
    # --------------------------------------------------------

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
    # IMPORTANT:
    # Check newest candles FIRST.
    # --------------------------------------------------------

    for candle in reversed(data):

        high = candle["high"]

        low = candle["low"]

        open_price = candle["open"]

        close = candle["close"]

        # ====================================================
        # LONG
        # ====================================================

        if direction == "LONG":

            for level_data in levels:

                if (
                    level_data["side"]
                    != "LONG"
                ):
                    continue

                level = level_data[
                    "price"
                ]

                # ------------------------------------------------
                # Sweep must actually take SSL.
                # ------------------------------------------------

                depth = (
                    level - low
                )

                depth_pct = (

                    depth
                    / level
                    * 100.0

                    if level
                    else 0.0

                )

                swept = (

                    low < level

                    and depth_pct
                    >= MIN_SWEEP_DEPTH_PCT

                )

                # ------------------------------------------------
                # Rejection:
                # candle closes back above liquidity
                # and is bullish.
                # ------------------------------------------------

                rejection = (

                    close > level

                    and close > open_price

                )

                if swept and rejection:

                    return {

                        "swept": True,

                        "direction": "LONG",

                        "level": level,

                        "extreme": low,

                        "time":
                            candle[
                                "open_time"
                            ],

                        "strength":
                            level_data[
                                "strength"
                            ],

                        "touches":
                            level_data[
                                "touches"
                            ],

                        "liquidity_type":
                            level_data[
                                "liquidity_type"
                            ],

                        "type":
                            "SSL SWEEP",

                        "depth_pct":
                            round(
                                depth_pct,
                                4,
                            ),

                    }

        # ====================================================
        # SHORT
        # ====================================================

        else:

            for level_data in levels:

                if (
                    level_data["side"]
                    != "SHORT"
                ):
                    continue

                level = level_data[
                    "price"
                ]

                # ------------------------------------------------
                # Sweep must actually take BSL.
                # ------------------------------------------------

                depth = (
                    high - level
                )

                depth_pct = (

                    depth
                    / level
                    * 100.0

                    if level
                    else 0.0

                )

                swept = (

                    high > level

                    and depth_pct
                    >= MIN_SWEEP_DEPTH_PCT

                )

                # ------------------------------------------------
                # Rejection:
                # candle closes back below liquidity
                # and is bearish.
                # ------------------------------------------------

                rejection = (

                    close < level

                    and close < open_price

                )

                if swept and rejection:

                    return {

                        "swept": True,

                        "direction": "SHORT",

                        "level": level,

                        "extreme": high,

                        "time":
                            candle[
                                "open_time"
                            ],

                        "strength":
                            level_data[
                                "strength"
                            ],

                        "touches":
                            level_data[
                                "touches"
                            ],

                        "liquidity_type":
                            level_data[
                                "liquidity_type"
                            ],

                        "type":
                            "BSL SWEEP",

                        "depth_pct":
                            round(
                                depth_pct,
                                4,
                            ),

                    }

    return None


# ============================================================
# COMPATIBILITY ALIAS
# ============================================================

get_major_liquidity = find_major_liquidity


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