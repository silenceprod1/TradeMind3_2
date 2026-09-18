"""
TradeMind 6.4 — Market Data
Binance Spot = reference chart.

Liquidity model:

1H structure
    ↓
1H swing highs / lows
    ↓
cluster nearby levels
    ↓
evaluate structural importance
    ↓
remove minor/intermediate levels
    ↓
Major Liquidity zones

D1 / W1 are NOT used.

Compatibility:
- strategy.py
- bot.py
- existing TradeMind interfaces
"""

import requests
import math
from typing import Optional, List, Dict, Any


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://api.binance.com/api/v3"

SYMBOL = "SOLUSDT"
TIMEOUT = 10

# Main liquidity settings
MAX_MAJOR_LEVELS = 4

# Swing detection
SWING_LEFT = 2
SWING_RIGHT = 2

# Cluster distance.
# IMPORTANT:
# This is percentage, not decimal.
#
# 0.15 = 0.15%
#
CLUSTER_PCT = 0.15

# Maximum width of one liquidity zone.
ZONE_MAX_WIDTH_PCT = 0.40

# Minimum distance between two independent major zones.
MIN_ZONE_GAP_PCT = 0.70

# Don't display liquidity too close to current price.
MIN_CURRENT_DISTANCE_PCT = 0.50

# How many 1H candles are used for structural analysis.
STRUCTURE_LOOKBACK = 80

# Ignore extremely old levels.
MAX_LEVEL_AGE_1H = 80

# Number of recent 1H candles excluded because they may still be forming
# / not have enough right-side confirmation.
EXCLUDE_LAST_1H = 4

# Sweep/taken detection tolerance
TAKEN_ZONE_TOLERANCE_PCT = 0.15


# ============================================================
# HTTP
# ============================================================

def _get(path: str, params: Dict[str, Any]) -> Any:
    response = requests.get(
        BASE_URL + path,
        params=params,
        timeout=TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# PRICE
# ============================================================

def get_price(symbol: str = SYMBOL) -> float:
    data = _get(
        "/ticker/price",
        {
            "symbol": symbol
        },
    )

    return float(data["price"])


# ============================================================
# KLINES
# ============================================================

def get_klines(
    symbol: str = SYMBOL,
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

    for x in raw:
        candles.append(
            {
                "open_time": x[0],
                "open": float(x[1]),
                "high": float(x[2]),
                "low": float(x[3]),
                "close": float(x[4]),
                "volume": float(x[5]),
                "close_time": x[6],
            }
        )

    return candles


# ============================================================
# MARKET DATA
# ============================================================

def get_market_data(symbol: str = SYMBOL) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "price": get_price(symbol),

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

        "candles_1m": get_klines(
            symbol,
            "1m",
            200,
        ),
    }


# ============================================================
# SAFE NUMBER HELPERS
# ============================================================

def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _distance_pct(a: float, b: float) -> float:
    if b == 0:
        return 999.0

    return abs(a - b) / abs(b) * 100.0


# ============================================================
# SWINGS
# ============================================================

def _swing_high(
    candles: List[Dict[str, Any]],
    i: int,
) -> bool:

    if i < SWING_LEFT:
        return False

    if i >= len(candles) - SWING_RIGHT:
        return False

    value = candles[i]["high"]

    left = candles[
        i - SWING_LEFT:i
    ]

    right = candles[
        i + 1:i + 1 + SWING_RIGHT
    ]

    for candle in left:
        if value <= candle["high"]:
            return False

    for candle in right:
        if value < candle["high"]:
            return False

    return True


def _swing_low(
    candles: List[Dict[str, Any]],
    i: int,
) -> bool:

    if i < SWING_LEFT:
        return False

    if i >= len(candles) - SWING_RIGHT:
        return False

    value = candles[i]["low"]

    left = candles[
        i - SWING_LEFT:i
    ]

    right = candles[
        i + 1:i + 1 + SWING_RIGHT
    ]

    for candle in left:
        if value >= candle["low"]:
            return False

    for candle in right:
        if value > candle["low"]:
            return False

    return True


# ============================================================
# CANDLE STRUCTURE
# ============================================================

def _body(candle: Dict[str, Any]) -> float:
    return abs(
        candle["close"] - candle["open"]
    )


def _range(candle: Dict[str, Any]) -> float:
    return max(
        candle["high"] - candle["low"],
        0.00000001,
    )


def _body_ratio(candle: Dict[str, Any]) -> float:
    return _body(candle) / _range(candle)


def _bull(candle: Dict[str, Any]) -> bool:
    return candle["close"] > candle["open"]


def _bear(candle: Dict[str, Any]) -> bool:
    return candle["close"] < candle["open"]


# ============================================================
# CLUSTERING
# ============================================================

def _cluster_swing_points(
    points: List[Dict[str, Any]],
    cluster_pct: float = CLUSTER_PCT,
) -> List[Dict[str, Any]]:
    """
    Merge nearby 1H swing points.

    Example:

        101.30
        101.42
        101.55

    become one liquidity zone instead of
    three independent levels.

    cluster_pct is expressed in percent.
    """

    if not points:
        return []

    points = sorted(
        points,
        key=lambda x: x["price"],
    )

    clusters = []

    for point in points:

        if not clusters:
            clusters.append(
                {
                    "points": [point],
                    "prices": [point["price"]],
                    "latest_index": point["index"],
                }
            )
            continue

        current = clusters[-1]

        center = sum(
            current["prices"]
        ) / len(current["prices"])

        distance = _distance_pct(
            point["price"],
            center,
        )

        if distance <= cluster_pct:

            current["points"].append(point)
            current["prices"].append(
                point["price"]
            )

            current["latest_index"] = max(
                current["latest_index"],
                point["index"],
            )

        else:

            clusters.append(
                {
                    "points": [point],
                    "prices": [point["price"]],
                    "latest_index": point["index"],
                }
            )

    result = []

    for cluster in clusters:

        prices = cluster["prices"]

        low = min(prices)
        high = max(prices)

        center = sum(prices) / len(prices)

        result.append(
            {
                "price": center,
                "zone_low": low,
                "zone_high": high,
                "points": cluster["points"],
                "touches": len(prices),
                "latest_index": cluster["latest_index"],
            }
        )

    return result


# ============================================================
# STRUCTURAL STRENGTH
# ============================================================

def _rejection_strength(
    candles: List[Dict[str, Any]],
    level: float,
    side: str,
) -> float:
    """
    Estimates whether price strongly rejected the area
    after interacting with it.

    This is NOT order-book liquidity.
    It is candle-derived structural evidence.
    """

    if not candles:
        return 0.0

    recent = candles[-80:]

    best = 0.0

    for i, candle in enumerate(recent):

        high_distance = _distance_pct(
            candle["high"],
            level,
        )

        low_distance = _distance_pct(
            candle["low"],
            level,
        )

        if side == "SSL":

            if low_distance > 0.25:
                continue

            move = candle["close"] - candle["low"]

            if move <= 0:
                continue

            strength = (
                move
                / max(
                    candle["high"] - candle["low"],
                    0.00000001,
                )
            )

        else:

            if high_distance > 0.25:
                continue

            move = candle["high"] - candle["close"]

            if move <= 0:
                continue

            strength = (
                move
                / max(
                    candle["high"] - candle["low"],
                    0.00000001,
                )
            )

        best = max(
            best,
            min(strength, 1.0),
        )

    return best * 15.0


def _structural_strength(
    candles_1h: List[Dict[str, Any]],
    cluster: Dict[str, Any],
    side: str,
) -> float:

    touches = cluster["touches"]

    # --------------------------------------------------------
    # Repeated 1H interaction
    # --------------------------------------------------------

    touch_score = min(
        touches * 10.0,
        30.0,
    )

    # --------------------------------------------------------
    # Recency
    # --------------------------------------------------------

    latest_index = cluster["latest_index"]

    age = max(
        0,
        len(candles_1h) - 1 - latest_index,
    )

    if age <= 8:
        recency_score = 15.0

    elif age <= 20:
        recency_score = 12.0

    elif age <= 40:
        recency_score = 8.0

    elif age <= 60:
        recency_score = 4.0

    else:
        recency_score = 0.0

    # --------------------------------------------------------
    # Rejection
    # --------------------------------------------------------

    rejection_score = _rejection_strength(
        candles_1h,
        cluster["price"],
        side,
    )

    # --------------------------------------------------------
    # Structural displacement
    # --------------------------------------------------------

    displacement_score = 0.0

    start = max(
        0,
        latest_index - 3,
    )

    end = min(
        len(candles_1h),
        latest_index + 8,
    )

    local = candles_1h[
        start:end
    ]

    if local:

        if side == "SSL":

            lowest = min(
                c["low"]
                for c in local
            )

            highest_close = max(
                c["close"]
                for c in local
            )

            if lowest < cluster["price"]:

                displacement = (
                    highest_close - cluster["price"]
                ) / cluster["price"] * 100

                displacement_score = min(
                    displacement * 8.0,
                    20.0,
                )

        else:

            highest = max(
                c["high"]
                for c in local
            )

            lowest_close = min(
                c["close"]
                for c in local
            )

            if highest > cluster["price"]:

                displacement = (
                    cluster["price"] - lowest_close
                ) / cluster["price"] * 100

                displacement_score = min(
                    displacement * 8.0,
                    20.0,
                )

    total = (
        touch_score
        + recency_score
        + rejection_score
        + displacement_score
    )

    return max(
        0.0,
        min(total, 100.0),
    )


# ============================================================
# LOCAL REINFORCEMENT
# ============================================================

def _local_touches(
    candles: Optional[List[Dict[str, Any]]],
    level: float,
    pct: float = 0.12,
) -> int:

    if not candles:
        return 0

    count = 0

    for candle in candles[-160:]:

        high_distance = _distance_pct(
            candle["high"],
            level,
        )

        low_distance = _distance_pct(
            candle["low"],
            level,
        )

        if (
            high_distance <= pct
            or low_distance <= pct
        ):
            count += 1

    return count


def _local_reinforcement(
    candles_15m: Optional[List[Dict[str, Any]]],
    candles_5m: Optional[List[Dict[str, Any]]],
    level: float,
) -> int:

    touches = 0

    touches += min(
        _local_touches(
            candles_15m,
            level,
            0.12,
        ),
        3,
    )

    touches += min(
        _local_touches(
            candles_5m,
            level,
            0.10,
        ),
        2,
    )

    return touches


# ============================================================
# ZONE WIDTH
# ============================================================

def _build_zone(
    cluster: Dict[str, Any],
) -> Dict[str, float]:

    raw_low = cluster["zone_low"]
    raw_high = cluster["zone_high"]
    center = cluster["price"]

    raw_width_pct = _distance_pct(
        raw_high,
        raw_low,
    )

    # If cluster itself is too wide,
    # create a controlled zone around the center.
    if raw_width_pct > ZONE_MAX_WIDTH_PCT:

        half = ZONE_MAX_WIDTH_PCT / 2.0

        zone_low = center * (
            1.0 - half / 100.0
        )

        zone_high = center * (
            1.0 + half / 100.0
        )

    else:

        # Small padding so that the zone is
        # actually tradable as an area.
        padding = 0.05

        zone_low = raw_low * (
            1.0 - padding / 100.0
        )

        zone_high = raw_high * (
            1.0 + padding / 100.0
        )

        width = _distance_pct(
            zone_high,
            zone_low,
        )

        if width > ZONE_MAX_WIDTH_PCT:

            half = ZONE_MAX_WIDTH_PCT / 2.0

            zone_low = center * (
                1.0 - half / 100.0
            )

            zone_high = center * (
                1.0 + half / 100.0
            )

    return {
        "zone_low": zone_low,
        "zone_high": zone_high,
    }


# ============================================================
# TAKEN / SWEPT LEVEL
# ============================================================

def _is_level_taken(
    candles_1h: List[Dict[str, Any]],
    level: Dict[str, Any],
) -> bool:
    """
    Determines whether a major liquidity zone has already
    been clearly consumed.

    This is candle-derived and therefore only a proxy.
    """

    if not candles_1h:
        return False

    price = float(
        level["price"]
    )

    side = level.get(
        "type",
        "",
    )

    zone_low = float(
        level.get(
            "zone_low",
            price,
        )
    )

    zone_high = float(
        level.get(
            "zone_high",
            price,
        )
    )

    recent = candles_1h[-30:]

    for candle in recent:

        high = candle["high"]
        low = candle["low"]
        close = candle["close"]

        if side == "SSL":

            # Price traded below the zone and then
            # recovered back above it = SSL taken.
            if (
                low < zone_low
                and close > zone_high
            ):
                return True

        elif side == "BSL":

            # Price traded above the zone and then
            # closed back below it = BSL taken.
            if (
                high > zone_high
                and close < zone_low
            ):
                return True

    return False


# ============================================================
# MAJOR LEVEL SELECTION
# ============================================================

def _select_major_zones(
    candidates: List[Dict[str, Any]],
    current_price: float,
    side: str,
    max_levels: int,
) -> List[Dict[str, Any]]:
    """
    Select only structurally important zones.

    This is the important part of the new liquidity model.

    We DON'T simply do:

        sort by strength
        take first 12

    because that causes almost every swing to appear major.

    Instead:

        1. sort structurally
        2. enforce current-price distance
        3. enforce separation
        4. take max 4
    """

    if not candidates:
        return []

    filtered = []

    for candidate in candidates:

        price = float(
            candidate["price"]
        )

        distance = _distance_pct(
            price,
            current_price,
        )

        if distance < MIN_CURRENT_DISTANCE_PCT:
            continue

        candidate["distance_pct"] = distance

        filtered.append(candidate)

    if not filtered:
        return []

    # Strongest structural candidates first.
    filtered.sort(
        key=lambda x: (
            -float(
                x.get(
                    "strength",
                    0,
                )
            ),
            -int(
                x.get(
                    "touches",
                    1,
                )
            ),
            float(
                x.get(
                    "distance_pct",
                    999,
                )
            ),
        )
    )

    selected = []

    for candidate in filtered:

        if len(selected) >= max_levels:
            break

        price = float(
            candidate["price"]
        )

        too_close = False

        for existing in selected:

            existing_price = float(
                existing["price"]
            )

            gap_pct = _distance_pct(
                price,
                existing_price,
            )

            if gap_pct < MIN_ZONE_GAP_PCT:
                too_close = True
                break

        if too_close:
            continue

        selected.append(candidate)

    # For presentation, sort by distance from price.
    selected.sort(
        key=lambda x: float(
            x["distance_pct"]
        )
    )

    return selected


# ============================================================
# MAJOR LIQUIDITY
# ============================================================

def find_major_liquidity(
    candles_1h: List[Dict[str, Any]],
    current_price: float,
    max_levels: int = MAX_MAJOR_LEVELS,
    candles_15m: Optional[List[Dict[str, Any]]] = None,
    candles_5m: Optional[List[Dict[str, Any]]] = None,
    candles_1m: Optional[List[Dict[str, Any]]] = None,
    include_swept: bool = False,
) -> List[Dict[str, Any]]:
    """
    Find MAJOR liquidity.

    ONLY 1H swing highs/lows create major liquidity.

    15M / 5M / 1M:
        - cannot create new major levels
        - only reinforce an existing 1H zone

    Returns compatible dictionaries for TradeMind strategy.
    """

    if not candles_1h:
        return []

    if len(candles_1h) < 20:
        return []

    current_price = float(
        current_price
    )

    # --------------------------------------------------------
    # Work only with confirmed historical candles.
    # --------------------------------------------------------

    base = candles_1h[
        :-EXCLUDE_LAST_1H
    ]

    if len(base) < 20:
        return []

    # Keep structural window manageable.
    if len(base) > STRUCTURE_LOOKBACK:
        base = base[
            -STRUCTURE_LOOKBACK:
        ]

    # --------------------------------------------------------
    # Collect 1H swings
    # --------------------------------------------------------

    highs = []
    lows = []

    for i in range(
        SWING_LEFT,
        len(base) - SWING_RIGHT,
    ):

        candle = base[i]

        if _swing_high(
            base,
            i,
        ):

            highs.append(
                {
                    "price": candle["high"],
                    "index": i,
                }
            )

        if _swing_low(
            base,
            i,
        ):

            lows.append(
                {
                    "price": candle["low"],
                    "index": i,
                }
            )

    # --------------------------------------------------------
    # Cluster separately.
    #
    # This prevents a nearby HIGH and LOW from accidentally
    # becoming one liquidity pool.
    # --------------------------------------------------------

    high_clusters = _cluster_swing_points(
        highs,
        CLUSTER_PCT,
    )

    low_clusters = _cluster_swing_points(
        lows,
        CLUSTER_PCT,
    )

    candidates = []

    # ========================================================
    # BSL
    # ========================================================

    for cluster in high_clusters:

        price = float(
            cluster["price"]
        )

        # Only BSL above current price.
        if price <= current_price:
            continue

        zone = _build_zone(
            cluster
        )

        structural = _structural_strength(
            base,
            cluster,
            "BSL",
        )

        local_reinforcement = _local_reinforcement(
            candles_15m,
            candles_5m,
            price,
        )

        # Local timeframes strengthen,
        # but cannot create the level.
        local_score = min(
            local_reinforcement * 2.0,
            10.0,
        )

        # Stronger weighting for repeated 1H touches.
        touches_bonus = min(
            cluster["touches"] * 4.0,
            12.0,
        )

        strength = (
            structural
            + local_score
            + touches_bonus
        )

        strength = max(
            1.0,
            min(
                strength,
                100.0,
            )
        )

        level = {
            "price": price,
            "side": "SHORT",
            "type": "BSL",
            "kind": "1H MAJOR BSL",

            "touches": cluster["touches"],
            "local_touches": local_reinforcement,

            "strength": round(
                strength,
                1,
            ),

            "zone_low": zone["zone_low"],
            "zone_high": zone["zone_high"],

            "age_1h": max(
                0,
                len(base) - 1 - cluster["latest_index"],
            ),

            "source": "1H",

            "swept": False,
            "taken": False,
        }

        level["taken"] = _is_level_taken(
            candles_1h,
            level,
        )

        level["swept"] = level["taken"]

        if level["taken"] and not include_swept:
            continue

        candidates.append(
            level
        )

    # ========================================================
    # SSL
    # ========================================================

    for cluster in low_clusters:

        price = float(
            cluster["price"]
        )

        # Only SSL below current price.
        if price >= current_price:
            continue

        zone = _build_zone(
            cluster
        )

        structural = _structural_strength(
            base,
            cluster,
            "SSL",
        )

        local_reinforcement = _local_reinforcement(
            candles_15m,
            candles_5m,
            price,
        )

        local_score = min(
            local_reinforcement * 2.0,
            10.0,
        )

        touches_bonus = min(
            cluster["touches"] * 4.0,
            12.0,
        )

        strength = (
            structural
            + local_score
            + touches_bonus
        )

        strength = max(
            1.0,
            min(
                strength,
                100.0,
            )
        )

        level = {
            "price": price,
            "side": "LONG",
            "type": "SSL",
            "kind": "1H MAJOR SSL",

            "touches": cluster["touches"],
            "local_touches": local_reinforcement,

            "strength": round(
                strength,
                1,
            ),

            "zone_low": zone["zone_low"],
            "zone_high": zone["zone_high"],

            "age_1h": max(
                0,
                len(base) - 1 - cluster["latest_index"],
            ),

            "source": "1H",

            "swept": False,
            "taken": False,
        }

        level["taken"] = _is_level_taken(
            candles_1h,
            level,
        )

        level["swept"] = level["taken"]

        if level["taken"] and not include_swept:
            continue

        candidates.append(
            level
        )

    # ========================================================
    # Split by side.
    # ========================================================

    bsl = [
        x
        for x in candidates
        if x["type"] == "BSL"
    ]

    ssl = [
        x
        for x in candidates
        if x["type"] == "SSL"
    ]

    # Select only true major zones.
    selected_bsl = _select_major_zones(
        bsl,
        current_price,
        "BSL",
        max_levels,
    )

    selected_ssl = _select_major_zones(
        ssl,
        current_price,
        "SSL",
        max_levels,
    )

    result = (
        selected_bsl
        + selected_ssl
    )

    # --------------------------------------------------------
    # Final sorting:
    # nearest relevant major zones first.
    # --------------------------------------------------------

    result.sort(
        key=lambda x: (
            float(
                x["distance_pct"]
            ),
            -float(
                x["strength"]
            ),
        )
    )

    return result


# ============================================================
# COMPATIBILITY ALIASES
# ============================================================

def get_fresh_liquidity(
    *args,
    **kwargs,
):
    return find_major_liquidity(
        *args,
        **kwargs,
    )


def get_all_major_liquidity(
    *args,
    **kwargs,
):
    return find_major_liquidity(
        *args,
        **kwargs,
    )


# ============================================================
# TARGET LIQUIDITY
# ============================================================

def get_target_liquidity(
    major_levels: List[Dict[str, Any]],
    current_price: float,
    direction: str,
    exclude_level: Optional[float] = None,
):
    """
    Find next major unswept liquidity in trade direction.

    LONG:
        target must be ABOVE current price.

    SHORT:
        target must be BELOW current price.

    Already taken/swept levels are ignored.
    """

    price = float(
        current_price
    )

    excluded = (
        float(exclude_level)
        if exclude_level is not None
        else None
    )

    candidates = []

    for level in major_levels or []:

        try:
            level_price = float(
                level["price"]
            )
        except Exception:
            continue

        # ----------------------------------------------------
        # Exclude sweep level itself.
        # ----------------------------------------------------

        if excluded is not None:

            if (
                _distance_pct(
                    level_price,
                    excluded,
                )
                < 0.05
            ):
                continue

        # ----------------------------------------------------
        # Never use already consumed liquidity as a fresh TP.
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Direction
        # ----------------------------------------------------

        if direction == "LONG":

            if level_price > price:
                candidates.append(
                    level
                )

        elif direction == "SHORT":

            if level_price < price:
                candidates.append(
                    level
                )

    if not candidates:
        return None

    # Nearest valid major liquidity.
    return min(
        candidates,
        key=lambda x: abs(
            float(x["price"]) - price
        ),
    )


# ============================================================
# SWEEP
# ============================================================

def detect_sweep(
    candles_1h,
    current_price,
    direction=None,
    major_levels=None,
):
    """
    Delegates sweep detection to strategy.py.

    Major liquidity comes ONLY from this market.py.
    """

    from strategy import (
        get_1h_direction,
        find_sweep,
    )

    if direction is None:

        direction = get_1h_direction(
            candles_1h
        )

    if major_levels is None:

        major_levels = find_major_liquidity(
            candles_1h,
            current_price,
            MAX_MAJOR_LEVELS,
        )

    return find_sweep(
        candles_1h,
        major_levels,
        direction,
    )


detect_fresh_sweep = detect_sweep


# ============================================================
# DEBUG / PRESENTATION HELPERS
# ============================================================

def format_major_liquidity(
    levels: List[Dict[str, Any]],
) -> str:
    """
    Optional helper for Telegram/debug output.
    """

    if not levels:
        return "Major Liquidity не найдена."

    lines = [
        "💧 MAJOR LIQUIDITY",
        "",
    ]

    for level in levels:

        side = level.get(
            "type",
            "?",
        )

        price = float(
            level.get(
                "price",
                0,
            )
        )

        strength = float(
            level.get(
                "strength",
                0,
            )
        )

        zone_low = float(
            level.get(
                "zone_low",
                price,
            )
        )

        zone_high = float(
            level.get(
                "zone_high",
                price,
            )
        )

        if side == "SSL":

            emoji = "🟢"

        else:

            emoji = "🔴"

        if (
            abs(
                zone_high - zone_low
            )
            < 0.0000001
        ):

            zone_text = (
                f"${price:.4f}"
            )

        else:

            zone_text = (
                f"${zone_low:.4f}"
                f"–"
                f"${zone_high:.4f}"
            )

        lines.append(
            f"{emoji} {side} ZONE "
            f"{zone_text} "
            f"• S{strength:.0f}"
        )

    return "\n".join(
        lines
    )


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "BASE_URL",
    "SYMBOL",
    "get_price",
    "get_klines",
    "get_market_data",

    "_swing_high",
    "_swing_low",

    "find_major_liquidity",
    "get_fresh_liquidity",
    "get_all_major_liquidity",

    "get_target_liquidity",

    "detect_sweep",
    "detect_fresh_sweep",

    "format_major_liquidity",
]