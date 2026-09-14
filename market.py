# market.py
# TradeMind 4.4
# Binance Spot market data
# 1H Major Liquidity -> 5M Sweep -> 15M CHoCH/BOS
# Sweep carries exact time + extreme for Strategy 4.4
# No cache

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import time


BASE_URL = "https://api.binance.com/api/v3"
SYMBOL = "SOLUSDT"

REQUEST_TIMEOUT = 6


# ============================================================
# PARAMETERS
# ============================================================

# Structure
SWING_LEFT = 4
SWING_RIGHT = 4

# Major Liquidity
MAJOR_LOOKBACK = 150
MAJOR_MIN_DISTANCE_PCT = 0.005       # 0.5%
MAJOR_MIN_GAP_PCT = 0.007            # 0.7%
MAJOR_MIN_GAP_ABS = 0.50
MAJOR_MAX_LEVELS = 6

# Sweep
SWEEP_LOOKBACK = 12                  # last 12 closed 5M candles
SWEEP_MIN_PENETRATION_PCT = 0.0005   # 0.05%
SWEEP_MAX_PENETRATION_PCT = 0.012    # 1.2%

# Strategy 4.4 freshness
MAX_SWEEP_AGE_CANDLES = 6            # 30 minutes on 5M

# Structure
STRUCTURE_LOOKBACK = 80


# ============================================================
# BINANCE API
# ============================================================

def _get(path, params=None):
    """
    Universal GET request to Binance Spot API.
    """

    url = BASE_URL + path

    response = requests.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# PRICE
# ============================================================

def get_price(symbol=SYMBOL):
    """
    Current instrument price.
    """

    data = _get(
        "/ticker/price",
        {"symbol": symbol}
    )

    return float(data["price"])


# ============================================================
# KLINES
# ============================================================

def get_klines(symbol, interval, limit=200):
    """
    Binance candles.

    Returns:
        open_time
        open
        high
        low
        close
        volume
        close_time
    """

    raw = _get(
        "/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
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
            "close_time": int(row[6])
        })

    return candles


def get_closed_klines(symbol, interval, limit=200):
    """
    Returns only closed candles.

    Binance's last candle can still be forming,
    therefore it is removed.
    """

    candles = get_klines(
        symbol,
        interval,
        limit
    )

    if len(candles) <= 1:
        return candles

    return candles[:-1]


# ============================================================
# PARALLEL MARKET DATA
# ============================================================

def get_market_data(symbol=SYMBOL):
    """
    Gets price + 1H + 15M + 5M in parallel.
    """

    results = {}

    jobs = {
        "price": lambda: get_price(symbol),

        "candles_1h": lambda: get_closed_klines(
            symbol,
            "1h",
            200
        ),

        "candles_15m": lambda: get_closed_klines(
            symbol,
            "15m",
            200
        ),

        "candles_5m": lambda: get_closed_klines(
            symbol,
            "5m",
            200
        )
    }

    with ThreadPoolExecutor(max_workers=4) as executor:

        futures = {
            executor.submit(func): name
            for name, func in jobs.items()
        }

        for future in as_completed(futures):

            name = futures[future]

            try:
                results[name] = future.result()

            except Exception as exc:

                raise RuntimeError(
                    f"{symbol} {name} request failed: {exc}"
                ) from exc

    return {
        "symbol": symbol,
        "price": results["price"],
        "candles_1h": results["candles_1h"],
        "candles_15m": results["candles_15m"],
        "candles_5m": results["candles_5m"]
    }


# ============================================================
# SWING LEVELS
# ============================================================

def find_swing_levels(
    candles,
    left=SWING_LEFT,
    right=SWING_RIGHT
):
    """
    Finds confirmed swing highs/lows.

    Only closed candles are used.
    """

    highs = []
    lows = []

    if len(candles) < left + right + 1:

        return {
            "highs": highs,
            "lows": lows
        }

    for i in range(
        left,
        len(candles) - right
    ):

        current = candles[i]

        current_high = current["high"]
        current_low = current["low"]

        is_high = True
        is_low = True

        # Left side
        for j in range(i - left, i):

            if candles[j]["high"] >= current_high:
                is_high = False

            if candles[j]["low"] <= current_low:
                is_low = False

        # Right side
        for j in range(i + 1, i + right + 1):

            if candles[j]["high"] >= current_high:
                is_high = False

            if candles[j]["low"] <= current_low:
                is_low = False

        if is_high:

            highs.append({
                "price": current_high,
                "open_time": current["open_time"],
                "index": i,
                "type": "HIGH"
            })

        if is_low:

            lows.append({
                "price": current_low,
                "open_time": current["open_time"],
                "index": i,
                "type": "LOW"
            })

    return {
        "highs": highs,
        "lows": lows
    }


# ============================================================
# MAJOR LIQUIDITY
# ============================================================

def find_major_liquidity(
    candles,
    current_price=None,
    max_levels=MAJOR_MAX_LEVELS
):
    """
    Finds major 1H liquidity.

    Rules:
    - 1H only
    - last 150 closed candles
    - confirmed swing levels
    - minimum 0.5% from current price
    - minimum gap 0.7% or $0.50
    - maximum 6 levels
    """

    if not candles:
        return []

    if current_price is None:
        current_price = candles[-1]["close"]

    candles = candles[-MAJOR_LOOKBACK:]

    swings = find_swing_levels(
        candles,
        left=SWING_LEFT,
        right=SWING_RIGHT
    )

    candidates = []

    # --------------------------------------------------------
    # HIGH LIQUIDITY
    # --------------------------------------------------------

    for level in swings["highs"]:

        price = level["price"]

        if price <= current_price:
            continue

        distance_pct = (
            price - current_price
        ) / current_price

        if distance_pct < MAJOR_MIN_DISTANCE_PCT:
            continue

        candidates.append({
            "price": price,
            "type": "HIGH",
            "distance_pct": distance_pct,
            "open_time": level["open_time"],
            "index": level["index"]
        })

    # --------------------------------------------------------
    # LOW LIQUIDITY
    # --------------------------------------------------------

    for level in swings["lows"]:

        price = level["price"]

        if price >= current_price:
            continue

        distance_pct = (
            current_price - price
        ) / current_price

        if distance_pct < MAJOR_MIN_DISTANCE_PCT:
            continue

        candidates.append({
            "price": price,
            "type": "LOW",
            "distance_pct": distance_pct,
            "open_time": level["open_time"],
            "index": level["index"]
        })

    # Closest first
    candidates.sort(
        key=lambda x: x["distance_pct"]
    )

    selected = []

    for candidate in candidates:

        too_close = False

        for existing in selected:

            price_diff = abs(
                candidate["price"]
                - existing["price"]
            )

            avg_price = (
                candidate["price"]
                + existing["price"]
            ) / 2

            gap_pct = price_diff / avg_price

            if (
                gap_pct < MAJOR_MIN_GAP_PCT
                or price_diff < MAJOR_MIN_GAP_ABS
            ):

                too_close = True
                break

        if too_close:
            continue

        selected.append(candidate)

        if len(selected) >= max_levels:
            break

    selected.sort(
        key=lambda x: x["distance_pct"]
    )

    return selected


# ============================================================
# CANDLE HELPERS
# ============================================================

def _candle_body(candle):

    return abs(
        candle["close"] - candle["open"]
    )


def _candle_range(candle):

    return candle["high"] - candle["low"]


def _bullish(candle):

    return candle["close"] > candle["open"]


def _bearish(candle):

    return candle["close"] < candle["open"]


# ============================================================
# SWEEP QUALITY
# ============================================================

def _calculate_sweep_quality(
    candle,
    level,
    direction
):
    """
    Calculates sweep quality.
    """

    level_price = level["price"]

    candle_range = _candle_range(candle)

    if candle_range <= 0:

        return {
            "quality": "WEAK",
            "penetration_pct": 0.0,
            "rejection_pct": 0.0,
            "body_strength": 0.0,
            "score": 0
        }

    if direction == "LONG":

        # Downside sweep
        penetration = max(
            0.0,
            level_price - candle["low"]
        )

        penetration_pct = (
            penetration / level_price
        )

        # Close back above liquidity
        rejection = max(
            0.0,
            candle["close"] - level_price
        )

        rejection_pct = (
            rejection / level_price
        )

        body_strength = (
            max(
                0.0,
                candle["close"] - candle["open"]
            )
            / candle_range
        )

    else:

        # Upside sweep
        penetration = max(
            0.0,
            candle["high"] - level_price
        )

        penetration_pct = (
            penetration / level_price
        )

        # Close back below liquidity
        rejection = max(
            0.0,
            level_price - candle["close"]
        )

        rejection_pct = (
            rejection / level_price
        )

        body_strength = (
            max(
                0.0,
                candle["open"] - candle["close"]
            )
            / candle_range
        )

    score = 0

    # Normal penetration
    if penetration_pct >= SWEEP_MIN_PENETRATION_PCT:
        score += 30

    # Not excessively deep
    if penetration_pct <= SWEEP_MAX_PENETRATION_PCT:
        score += 15

    # Returned beyond level
    if rejection_pct > 0:
        score += 30

    # Directional body
    if body_strength >= 0.50:
        score += 25

    elif body_strength >= 0.30:
        score += 15

    if score >= 80:
        quality = "STRONG"

    elif score >= 55:
        quality = "NORMAL"

    else:
        quality = "WEAK"

    return {
        "quality": quality,
        "penetration_pct": penetration_pct,
        "rejection_pct": rejection_pct,
        "body_strength": body_strength,
        "score": min(score, 100)
    }


# ============================================================
# SWEEP 4.4
# ============================================================

def detect_sweep(
    candles_5m,
    major_levels,
    lookback=SWEEP_LOOKBACK
):
    """
    TradeMind 4.4 Sweep.

    ONLY major 1H liquidity.

    LONG:
        5M low pierces 1H LOW
        -> 5M closes back above level

    SHORT:
        5M high pierces 1H HIGH
        -> 5M closes back below level

    Important:
        Sweep contains exact timestamp and extreme.

    This is required by Strategy 4.4:

        fresh sweep
        -> 15M confirmation
        -> 5M trigger
        -> entry near sweep
        -> SL behind sweep extreme
        -> TP 2R
    """

    if not candles_5m:
        return None

    if not major_levels:
        return None

    candles = candles_5m[-lookback:]

    # Newest confirmed sweep first
    for candle in reversed(candles):

        for level in major_levels:

            level_price = level["price"]
            level_type = level["type"]

            # =================================================
            # LONG SWEEP
            # =================================================

            if level_type == "LOW":

                if candle["low"] < level_price:

                    # Must close back above liquidity
                    if candle["close"] <= level_price:
                        continue

                    quality = _calculate_sweep_quality(
                        candle,
                        level,
                        "LONG"
                    )

                    # Reject very weak sweep
                    if quality["score"] < 40:
                        continue

                    sweep_extreme = candle["low"]

                    return {
                        "swept": True,
                        "direction": "LONG",

                        # Major liquidity
                        "level": level_price,
                        "price": level_price,
                        "liquidity_type": "LOW",
                        "liquidity_open_time": level.get(
                            "open_time"
                        ),

                        # Exact sweep candle
                        "open_time": candle["open_time"],
                        "close_time": candle["close_time"],

                        "sweep_time": candle["open_time"],
                        "sweep_close_time": candle[
                            "close_time"
                        ],

                        # Actual sweep candle OHLC
                        "open": candle["open"],
                        "close": candle["close"],
                        "high": candle["high"],
                        "low": candle["low"],

                        # Critical for SL
                        "sweep_extreme": sweep_extreme,
                        "extreme": sweep_extreme,

                        # Quality
                        "quality": quality["quality"],
                        "score": quality["score"],
                        "penetration_pct": quality[
                            "penetration_pct"
                        ],
                        "rejection_pct": quality[
                            "rejection_pct"
                        ],
                        "body_strength": quality[
                            "body_strength"
                        ],

                        # Debug
                        "candle_index": (
                            len(candles_5m)
                            - len(candles)
                            + candles.index(candle)
                        )
                    }

            # =================================================
            # SHORT SWEEP
            # =================================================

            if level_type == "HIGH":

                if candle["high"] > level_price:

                    # Must close back below liquidity
                    if candle["close"] >= level_price:
                        continue

                    quality = _calculate_sweep_quality(
                        candle,
                        level,
                        "SHORT"
                    )

                    if quality["score"] < 40:
                        continue

                    sweep_extreme = candle["high"]

                    return {
                        "swept": True,
                        "direction": "SHORT",

                        # Major liquidity
                        "level": level_price,
                        "price": level_price,
                        "liquidity_type": "HIGH",
                        "liquidity_open_time": level.get(
                            "open_time"
                        ),

                        # Exact sweep candle
                        "open_time": candle["open_time"],
                        "close_time": candle["close_time"],

                        "sweep_time": candle["open_time"],
                        "sweep_close_time": candle[
                            "close_time"
                        ],

                        # Actual sweep candle OHLC
                        "open": candle["open"],
                        "close": candle["close"],
                        "high": candle["high"],
                        "low": candle["low"],

                        # Critical for SL
                        "sweep_extreme": sweep_extreme,
                        "extreme": sweep_extreme,

                        # Quality
                        "quality": quality["quality"],
                        "score": quality["score"],
                        "penetration_pct": quality[
                            "penetration_pct"
                        ],
                        "rejection_pct": quality[
                            "rejection_pct"
                        ],
                        "body_strength": quality[
                            "body_strength"
                        ],

                        # Debug
                        "candle_index": (
                            len(candles_5m)
                            - len(candles)
                            + candles.index(candle)
                        )
                    }

    return None


# ============================================================
# SWEEP FRESHNESS
# ============================================================

def get_sweep_age_candles(
    sweep,
    candles_5m
):
    """
    Returns how many closed 5M candles have passed
    since the sweep.

    0 = newest closed candle
    1 = one candle ago
    etc.
    """

    if not sweep:
        return None

    sweep_time = sweep.get("sweep_time")

    if sweep_time is None:
        sweep_time = sweep.get("open_time")

    if sweep_time is None:
        return None

    try:
        sweep_time = int(sweep_time)
    except (TypeError, ValueError):
        return None

    if not candles_5m:
        return None

    closed_after = [
        candle
        for candle in candles_5m
        if candle.get("open_time", 0) > sweep_time
    ]

    return len(closed_after)


def sweep_is_fresh(
    sweep,
    candles_5m,
    max_age=MAX_SWEEP_AGE_CANDLES
):
    """
    Strategy 4.4 freshness check.

    If timestamp is missing -> False.

    Old sweep -> False.
    """

    age = get_sweep_age_candles(
        sweep,
        candles_5m
    )

    if age is None:
        return False

    return age <= max_age


# ============================================================
# SWEEP DISTANCE
# ============================================================

def get_sweep_distance_pct(
    current_price,
    sweep
):
    """
    Distance from current price to sweep level.

    Used to prevent entering in the middle of a move.
    """

    if not sweep:
        return None

    level = (
        sweep.get("level")
        or sweep.get("price")
    )

    if level is None:
        return None

    if current_price is None:
        return None

    try:
        return abs(
            float(current_price) - float(level)
        ) / float(level)

    except (TypeError, ValueError, ZeroDivisionError):
        return None


# ============================================================
# STRUCTURE HELPERS
# ============================================================

def _get_confirmed_swings(candles):

    return find_swing_levels(
        candles,
        left=SWING_LEFT,
        right=SWING_RIGHT
    )


def _last_two_highs(swings):

    highs = swings["highs"]

    if len(highs) < 2:
        return None

    return highs[-2], highs[-1]


def _last_two_lows(swings):

    lows = swings["lows"]

    if len(lows) < 2:
        return None

    return lows[-2], lows[-1]


# ============================================================
# CHoCH / BOS
# ============================================================

def detect_structure(
    candles_15m,
    direction=None,
    lookback=STRUCTURE_LOOKBACK
):
    """
    Determines 15M structure.

    CHoCH:
        structure change

    BOS:
        structure continuation

    Used as additional score.
    """

    if not candles_15m:

        return {
            "direction": direction,
            "structure": "NONE",
            "score": 0,
            "broken_level": None,
            "candle": None,
            "confirmation_time": None
        }

    candles = candles_15m[-lookback:]

    if len(candles) < 15:

        return {
            "direction": direction,
            "structure": "NONE",
            "score": 0,
            "broken_level": None,
            "candle": None,
            "confirmation_time": None
        }

    swings = _get_confirmed_swings(candles)

    highs = swings["highs"]
    lows = swings["lows"]

    if len(highs) < 2 or len(lows) < 2:

        return {
            "direction": direction,
            "structure": "NONE",
            "score": 0,
            "broken_level": None,
            "candle": None,
            "confirmation_time": None
        }

    last_high = highs[-1]
    previous_high = highs[-2]

    last_low = lows[-1]
    previous_low = lows[-2]

    recent = candles[-8:]

    # =========================================================
    # LONG
    # =========================================================

    if direction == "LONG":

        # CHoCH
        for candle in reversed(recent):

            if candle["close"] > last_high["price"]:

                return {
                    "direction": "LONG",
                    "structure": "CHoCH",
                    "score": 10,
                    "broken_level": last_high["price"],
                    "candle": candle,
                    "confirmation_time": candle["open_time"]
                }

        # BOS
        if last_high["price"] > previous_high["price"]:

            for candle in reversed(recent):

                if candle["close"] > previous_high["price"]:

                    return {
                        "direction": "LONG",
                        "structure": "BOS",
                        "score": 7,
                        "broken_level": previous_high["price"],
                        "candle": candle,
                        "confirmation_time": candle["open_time"]
                    }

    # =========================================================
    # SHORT
    # =========================================================

    if direction == "SHORT":

        # CHoCH
        for candle in reversed(recent):

            if candle["close"] < last_low["price"]:

                return {
                    "direction": "SHORT",
                    "structure": "CHoCH",
                    "score": 10,
                    "broken_level": last_low["price"],
                    "candle": candle,
                    "confirmation_time": candle["open_time"]
                }

        # BOS
        if last_low["price"] < previous_low["price"]:

            for candle in reversed(recent):

                if candle["close"] < previous_low["price"]:

                    return {
                        "direction": "SHORT",
                        "structure": "BOS",
                        "score": 7,
                        "broken_level": previous_low["price"],
                        "candle": candle,
                        "confirmation_time": candle["open_time"]
                    }

    # =========================================================
    # NO STRUCTURE
    # =========================================================

    if direction is None:

        latest_close = candles[-1]["close"]

        if latest_close > last_high["price"]:

            structure = "BULLISH"
            structure_direction = "LONG"

        elif latest_close < last_low["price"]:

            structure = "BEARISH"
            structure_direction = "SHORT"

        else:

            structure = "RANGE"
            structure_direction = None

        return {
            "direction": structure_direction,
            "structure": structure,
            "score": 0,
            "broken_level": None,
            "candle": candles[-1],
            "confirmation_time": None
        }

    return {
        "direction": direction,
        "structure": "NONE",
        "score": 0,
        "broken_level": None,
        "candle": candles[-1],
        "confirmation_time": None
    }


# ============================================================
# SWEEP + STRUCTURE
# ============================================================

def analyze_sweep_structure(
    candles_15m,
    candles_5m,
    major_levels,
    lookback=SWEEP_LOOKBACK
):
    """
    Full helper:

    1H Major Liquidity
          ↓
       5M Sweep
          ↓
       15M CHoCH/BOS

    The returned sweep contains:
        sweep_time
        sweep_extreme
        level
        direction
        quality
    """

    sweep = detect_sweep(
        candles_5m,
        major_levels,
        lookback=lookback
    )

    if sweep is None:

        return {
            "sweep": None,
            "structure": None,
            "score_bonus": 0
        }

    direction = sweep["direction"]

    structure = detect_structure(
        candles_15m,
        direction=direction
    )

    return {
        "sweep": sweep,
        "structure": structure,
        "score_bonus": structure["score"]
    }


# ============================================================
# OPPOSING LIQUIDITY
# ============================================================

def find_opposing_liquidity(
    current_price,
    direction,
    major_levels
):
    """
    Finds nearest major opposing liquidity.

    LONG:
        HIGH above price

    SHORT:
        LOW below price
    """

    if not major_levels:
        return None

    candidates = []

    for level in major_levels:

        price = level["price"]

        if direction == "LONG":

            if (
                level["type"] == "HIGH"
                and price > current_price
            ):

                candidates.append(level)

        elif direction == "SHORT":

            if (
                level["type"] == "LOW"
                and price < current_price
            ):

                candidates.append(level)

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: abs(
            x["price"] - current_price
        )
    )

    return candidates[0]


# ============================================================
# DEBUG
# ============================================================

def market_summary(symbol=SYMBOL):
    """
    Quick market.py test.
    """

    data = get_market_data(symbol)

    price = data["price"]

    major_levels = find_major_liquidity(
        data["candles_1h"],
        current_price=price
    )

    combined = analyze_sweep_structure(
        data["candles_15m"],
        data["candles_5m"],
        major_levels
    )

    sweep = combined["sweep"]

    if sweep:

        sweep_age = get_sweep_age_candles(
            sweep,
            data["candles_5m"]
        )

        sweep_distance = get_sweep_distance_pct(
            price,
            sweep
        )

        sweep["age_candles_5m"] = sweep_age
        sweep["distance_pct"] = sweep_distance

        sweep["fresh"] = (
            sweep_age is not None
            and sweep_age <= MAX_SWEEP_AGE_CANDLES
        )

    return {
        "symbol": symbol,
        "price": price,
        "major_levels": major_levels,
        "sweep": sweep,
        "structure": combined["structure"]
    }


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":

    print("TradeMind market.py 4.4 test")
    print("-" * 50)

    try:

        result = market_summary(SYMBOL)

        print(
            f"Symbol: {result['symbol']}"
        )

        print(
            f"Price: {result['price']}"
        )

        print("\nMajor Liquidity:")

        for level in result["major_levels"]:

            print(
                f"  {level['type']}: "
                f"{level['price']:.6f} "
                f"({level['distance_pct'] * 100:.2f}%)"
            )

        print("\nSweep:")

        sweep = result["sweep"]

        if sweep:

            age = sweep.get(
                "age_candles_5m",
                "?"
            )

            distance = sweep.get(
                "distance_pct"
            )

            if distance is not None:
                distance_text = (
                    f"{distance * 100:.2f}%"
                )
            else:
                distance_text = "?"

            print(
                f"  Direction: {sweep['direction']}"
            )

            print(
                f"  Level: {sweep['level']}"
            )

            print(
                f"  Extreme: {sweep['sweep_extreme']}"
            )

            print(
                f"  Sweep time: {sweep['sweep_time']}"
            )

            print(
                f"  Quality: {sweep['quality']}"
            )

            print(
                f"  Score: {sweep['score']}"
            )

            print(
                f"  Age: {age} x 5M"
            )

            print(
                f"  Distance: {distance_text}"
            )

            print(
                f"  Fresh: {sweep.get('fresh')}"
            )

        else:

            print("  NONE")

        print("\n15M Structure:")

        if result["structure"]:

            structure = result["structure"]

            print(
                f"  {structure['structure']} "
                f"Score={structure['score']}"
            )

            print(
                f"  Confirmation time="
                f"{structure.get('confirmation_time')}"
            )

        else:

            print("  NONE")

    except Exception as exc:

        print(
            f"ERROR: {exc}"
        )