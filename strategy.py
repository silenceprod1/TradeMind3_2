from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


# ============================================================
# TRADEMIND STRATEGY 6.1
# SMART MONEY / MAJOR LIQUIDITY
# ============================================================

STRATEGY_VERSION = "6.1.0"

MIN_SCORE_READY = 80
MIN_RR = 2.0

# SL buffer behind sweep extreme
SL_BUFFER_PCT = 0.20

# Minimum 5M manipulation
MIN_5M_MANIPULATION_PCT = 0.08

# Minimum recovery after manipulation
MIN_5M_RECOVERY_PCT = 0.33

# Ignore microscopic TP distances
MIN_TP_DISTANCE_PCT = 0.15

# Swing lookbacks
D1_LOOKBACK = 120
W1_LOOKBACK = 100
H1_LOOKBACK = 80
M15_LOOKBACK = 80
M5_LOOKBACK = 60


# ============================================================
# BASIC HELPERS
# ============================================================

def _safe_float(value, default=None):

    try:
        if value is None:
            return default

        return float(value)

    except Exception:
        return default


def _pct(a, b):

    a = _safe_float(a)
    b = _safe_float(b)

    if a is None or b in (None, 0):
        return 0.0

    return abs(a - b) / abs(b) * 100.0


def _body(candle):

    return abs(
        _safe_float(candle.get("close"), 0)
        -
        _safe_float(candle.get("open"), 0)
    )


def _range(candle):

    return (
        _safe_float(candle.get("high"), 0)
        -
        _safe_float(candle.get("low"), 0)
    )


def _body_ratio(candle):

    rng = _range(candle)

    if rng <= 0:
        return 0.0

    return _body(candle) / rng


def _bullish(candle):

    return (
        _safe_float(candle.get("close"), 0)
        >
        _safe_float(candle.get("open"), 0)
    )


def _bearish(candle):

    return (
        _safe_float(candle.get("close"), 0)
        <
        _safe_float(candle.get("open"), 0)
    )


# ============================================================
# SWINGS
# ============================================================

def _swing_highs(candles):

    result = []

    if not candles:
        return result

    data = candles[-H1_LOOKBACK:]

    for i in range(1, len(data) - 1):

        left = _safe_float(
            data[i - 1].get("high")
        )

        current = _safe_float(
            data[i].get("high")
        )

        right = _safe_float(
            data[i + 1].get("high")
        )

        if (
            current is not None
            and left is not None
            and right is not None
            and current > left
            and current >= right
        ):
            result.append({
                "price": current,
                "index": i,
                "time": data[i].get("open_time"),
            })

    return result


def _swing_lows(candles):

    result = []

    if not candles:
        return result

    data = candles[-H1_LOOKBACK:]

    for i in range(1, len(data) - 1):

        left = _safe_float(
            data[i - 1].get("low")
        )

        current = _safe_float(
            data[i].get("low")
        )

        right = _safe_float(
            data[i + 1].get("low")
        )

        if (
            current is not None
            and left is not None
            and right is not None
            and current < left
            and current <= right
        ):
            result.append({
                "price": current,
                "index": i,
                "time": data[i].get("open_time"),
            })

    return result


# ============================================================
# STRUCTURE
# ============================================================

def structure_context(candles):

    if not candles or len(candles) < 10:

        return {
            "direction": "NEUTRAL",
            "bos": False,
            "impulse": 0.0,
            "reason": "Недостаточно данных",
        }

    highs = _swing_highs(candles)
    lows = _swing_lows(candles)

    if len(highs) < 2 or len(lows) < 2:

        return {
            "direction": "NEUTRAL",
            "bos": False,
            "impulse": 0.0,
            "reason": "Недостаточно swing-структуры",
        }

    last_high = highs[-1]["price"]
    previous_high = highs[-2]["price"]

    last_low = lows[-1]["price"]
    previous_low = lows[-2]["price"]

    # --------------------------------------------------------
    # STRUCTURE
    # --------------------------------------------------------

    bullish_structure = (
        last_high >= previous_high
        and
        last_low >= previous_low
    )

    bearish_structure = (
        last_high <= previous_high
        and
        last_low <= previous_low
    )

    # --------------------------------------------------------
    # BOS
    # --------------------------------------------------------

    closes = candles[-8:]

    latest_close = _safe_float(
        closes[-1].get("close")
    )

    previous_range_high = max(
        _safe_float(c.get("high"), 0)
        for c in closes[:-1]
    )

    previous_range_low = min(
        _safe_float(c.get("low"), 0)
        for c in closes[:-1]
    )

    bullish_bos = (
        latest_close is not None
        and latest_close > previous_range_high
    )

    bearish_bos = (
        latest_close is not None
        and latest_close < previous_range_low
    )

    # --------------------------------------------------------
    # RECENT IMPULSE
    # --------------------------------------------------------

    recent = candles[-12:]

    start = _safe_float(
        recent[0].get("open")
    )

    end = _safe_float(
        recent[-1].get("close")
    )

    impulse = 0.0

    if start not in (None, 0) and end is not None:

        impulse = (
            (end - start)
            / start
            * 100.0
        )

    # --------------------------------------------------------
    # IMPORTANT:
    # Structure is NOT required to have both
    # HH+HL or LH+LL perfectly.
    #
    # This prevents 1H from becoming NEUTRAL
    # too easily.
    # --------------------------------------------------------

    bullish_score = 0
    bearish_score = 0

    if last_high > previous_high:
        bullish_score += 1

    if last_low > previous_low:
        bullish_score += 1

    if last_high < previous_high:
        bearish_score += 1

    if last_low < previous_low:
        bearish_score += 1

    if bullish_bos:
        bullish_score += 2

    if bearish_bos:
        bearish_score += 2

    if impulse >= 1.0:
        bullish_score += 1

    if impulse <= -1.0:
        bearish_score += 1

    if bullish_score > bearish_score:

        return {
            "direction": "BULLISH",
            "bos": bullish_bos,
            "impulse": impulse,
            "reason": "Бычья структура / BOS",
        }

    if bearish_score > bullish_score:

        return {
            "direction": "BEARISH",
            "bos": bearish_bos,
            "impulse": impulse,
            "reason": "Медвежья структура / BOS",
        }

    return {
        "direction": "NEUTRAL",
        "bos": False,
        "impulse": impulse,
        "reason": "Структура не имеет явного преимущества",
    }


# ============================================================
# HIGHER TIMEFRAME DIRECTION
# ============================================================

def get_higher_timeframe_direction(
    candles_d1,
    candles_w1=None,
):

    d1 = structure_context(candles_d1)

    if d1["direction"] == "BULLISH":

        return {
            "direction": "LONG",
            "source": "D1",
            "structure": d1,
        }

    if d1["direction"] == "BEARISH":

        return {
            "direction": "SHORT",
            "source": "D1",
            "structure": d1,
        }

    # --------------------------------------------------------
    # W1 FALLBACK
    # --------------------------------------------------------

    if candles_w1:

        w1 = structure_context(candles_w1)

        if w1["direction"] == "BULLISH":

            return {
                "direction": "LONG",
                "source": "W1",
                "structure": w1,
            }

        if w1["direction"] == "BEARISH":

            return {
                "direction": "SHORT",
                "source": "W1",
                "structure": w1,
            }

    return {
        "direction": "NEUTRAL",
        "source": None,
        "structure": None,
    }


# ============================================================
# LIQUIDITY NORMALIZATION
# ============================================================

def _normalize_liquidity_levels(
    major_levels,
):

    result = []

    if not major_levels:
        return result

    for level in major_levels:

        if not isinstance(level, dict):
            continue

        price = _safe_float(
            level.get("price")
            if level.get("price") is not None
            else level.get("level")
        )

        if price is None or price <= 0:
            continue

        item = dict(level)

        item["price"] = price

        result.append(item)

    return result


# ============================================================
# BOTH LIQUIDITY SIDES
# ============================================================

def get_liquidity_sides(
    major_levels,
    current_price,
    max_levels_each_side=6,
):

    current_price = _safe_float(
        current_price
    )

    if current_price is None:
        return {
            "above": [],
            "below": [],
        }

    levels = _normalize_liquidity_levels(
        major_levels
    )

    above = []
    below = []

    for level in levels:

        price = level["price"]

        # Already swept = not fresh
        if level.get("swept") is True:
            continue

        item = dict(level)

        # ----------------------------------------------------
        # ABOVE = BSL
        # ----------------------------------------------------

        if price > current_price:

            item["side"] = "ABOVE"

            item["liquidity_type"] = (
                "BSL"
            )

            item["distance_pct"] = round(
                _pct(
                    price,
                    current_price,
                ),
                4,
            )

            above.append(item)

        # ----------------------------------------------------
        # BELOW = SSL
        # ----------------------------------------------------

        elif price < current_price:

            item["side"] = "BELOW"

            item["liquidity_type"] = (
                "SSL"
            )

            item["distance_pct"] = round(
                _pct(
                    price,
                    current_price,
                ),
                4,
            )

            below.append(item)

    # Closest BSL first
    above.sort(
        key=lambda x: (
            x["distance_pct"],
            -x.get("touches", 0),
            -x.get("strength", 0),
        )
    )

    # Closest SSL first
    below.sort(
        key=lambda x: (
            x["distance_pct"],
            -x.get("touches", 0),
            -x.get("strength", 0),
        )
    )

    return {
        "above": above[
            :max_levels_each_side
        ],
        "below": below[
            :max_levels_each_side
        ],
    }


# ============================================================
# DIRECTIONAL LIQUIDITY
# ============================================================

def get_directional_liquidity(
    major_levels,
    current_price,
    direction,
):

    sides = get_liquidity_sides(
        major_levels,
        current_price,
    )

    if direction == "LONG":

        if not sides["below"]:
            return None

        return sides["below"][0]

    if direction == "SHORT":

        if not sides["above"]:
            return None

        return sides["above"][0]

    return None


# ============================================================
# NEXT MAJOR TARGET
# ============================================================

def get_next_major_target(
    major_levels,
    entry,
    direction,
):

    entry = _safe_float(entry)

    if entry is None:
        return None

    levels = _normalize_liquidity_levels(
        major_levels
    )

    candidates = []

    for level in levels:

        price = level["price"]

        # Never use already swept liquidity
        if level.get("swept") is True:
            continue

        if direction == "LONG":

            if price > entry:

                item = dict(level)

                item["liquidity_type"] = (
                    "BSL"
                )

                candidates.append(item)

        elif direction == "SHORT":

            if price < entry:

                item = dict(level)

                item["liquidity_type"] = (
                    "SSL"
                )

                candidates.append(item)

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
# 1H FVG / IMBALANCE
# ============================================================

def find_fvg(
    candles,
    direction=None,
):

    if not candles or len(candles) < 3:
        return None

    data = candles[-20:]

    found = []

    for i in range(2, len(data)):

        c1 = data[i - 2]
        c2 = data[i - 1]
        c3 = data[i]

        h1 = _safe_float(c1.get("high"))
        l1 = _safe_float(c1.get("low"))

        h3 = _safe_float(c3.get("high"))
        l3 = _safe_float(c3.get("low"))

        if None in (
            h1,
            l1,
            h3,
            l3,
        ):
            continue

        # ----------------------------------------------------
        # Bullish FVG
        # ----------------------------------------------------

        if l3 > h1:

            found.append({
                "direction": "LONG",
                "low": h1,
                "high": l3,
                "size_pct": _pct(
                    l3,
                    h1,
                ),
                "time": c3.get("open_time"),
            })

        # ----------------------------------------------------
        # Bearish FVG
        # ----------------------------------------------------

        if h3 < l1:

            found.append({
                "direction": "SHORT",
                "low": h3,
                "high": l1,
                "size_pct": _pct(
                    l1,
                    h3,
                ),
                "time": c3.get("open_time"),
            })

    if not found:
        return None

    if direction:

        directional = [
            x
            for x in found
            if x["direction"] == direction
        ]

        if directional:
            return directional[-1]

    return found[-1]


# ============================================================
# 15M CONFIRMATION
# ============================================================

def confirmation_15m(
    candles_15m,
    direction,
):

    if (
        not candles_15m
        or len(candles_15m) < 8
    ):
        return None

    current = candles_15m[-1]

    current_close = _safe_float(
        current.get("close")
    )

    if current_close is None:
        return None

    previous = candles_15m[-8:-1]

    previous_high = max(
        _safe_float(
            x.get("high"),
            0
        )
        for x in previous
    )

    previous_low = min(
        _safe_float(
            x.get("low"),
            0
        )
        for x in previous
    )

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        if (
            current_close > previous_high
            and _bullish(current)
        ):

            return {
                "confirmed": True,
                "direction": "LONG",
                "price": current_close,
                "reason":
                    "15M bullish displacement / BOS",
            }

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        if (
            current_close < previous_low
            and _bearish(current)
        ):

            return {
                "confirmed": True,
                "direction": "SHORT",
                "price": current_close,
                "reason":
                    "15M bearish displacement / BOS",
            }

    return None


# ============================================================
# 5M SMART MONEY ILM
# ============================================================

def detect_5m_ilm(
    candles_5m,
    direction,
):

    if (
        not candles_5m
        or len(candles_5m) < 12
    ):
        return None

    data = candles_5m[
        -M5_LOOKBACK:
    ]

    last = data[-1]

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        previous = data[-10:-1]

        if not previous:
            return None

        manipulation_candle = min(
            previous,
            key=lambda x:
                _safe_float(
                    x.get("low"),
                    0,
                ),
        )

        manipulation_low = _safe_float(
            manipulation_candle.get("low")
        )

        recovery_high = max(
            _safe_float(
                x.get("high"),
                0,
            )
            for x in previous
        )

        last_close = _safe_float(
            last.get("close")
        )

        last_open = _safe_float(
            last.get("open")
        )

        if None in (
            manipulation_low,
            recovery_high,
            last_close,
            last_open,
        ):
            return None

        manipulation_size = (
            recovery_high
            -
            manipulation_low
        )

        manipulation_pct = (
            manipulation_size
            /
            manipulation_low
            *
            100.0
            if manipulation_low
            else 0.0
        )

        recovery = (
            last_close
            -
            manipulation_low
        )

        recovery_ratio = (
            recovery
            /
            manipulation_size
            if manipulation_size > 0
            else 0.0
        )

        body_ratio = _body_ratio(last)

        if (
            manipulation_pct
            >= MIN_5M_MANIPULATION_PCT
            and
            recovery_ratio
            >= MIN_5M_RECOVERY_PCT
            and
            last_close > last_open
            and
            body_ratio >= 0.35
        ):

            return {
                "confirmed": True,
                "direction": "LONG",
                "pattern": "V",
                "extreme": manipulation_low,
                "price": last_close,
                "manipulation_pct":
                    round(
                        manipulation_pct,
                        4,
                    ),
                "recovery_ratio":
                    round(
                        recovery_ratio,
                        3,
                    ),
                "body_ratio":
                    round(
                        body_ratio,
                        3,
                    ),
                "reason":
                    "5M V-reversal после снятия SSL",
            }

    # ========================================================
    # SHORT
    # ========================================================

    if direction == "SHORT":

        previous = data[-10:-1]

        if not previous:
            return None

        manipulation_candle = max(
            previous,
            key=lambda x:
                _safe_float(
                    x.get("high"),
                    0,
                ),
        )

        manipulation_high = _safe_float(
            manipulation_candle.get("high")
        )

        recovery_low = min(
            _safe_float(
                x.get("low"),
                0,
            )
            for x in previous
        )

        last_close = _safe_float(
            last.get("close")
        )

        last_open = _safe_float(
            last.get("open")
        )

        if None in (
            manipulation_high,
            recovery_low,
            last_close,
            last_open,
        ):
            return None

        manipulation_size = (
            manipulation_high
            -
            recovery_low
        )

        manipulation_pct = (
            manipulation_size
            /
            manipulation_high
            *
            100.0
            if manipulation_high
            else 0.0
        )

        recovery = (
            manipulation_high
            -
            last_close
        )

        recovery_ratio = (
            recovery
            /
            manipulation_size
            if manipulation_size > 0
            else 0.0
        )

        body_ratio = _body_ratio(last)

        if (
            manipulation_pct
            >= MIN_5M_MANIPULATION_PCT
            and
            recovery_ratio
            >= MIN_5M_RECOVERY_PCT
            and
            last_close < last_open
            and
            body_ratio >= 0.35
        ):

            return {
                "confirmed": True,
                "direction": "SHORT",
                "pattern": "L",
                "extreme": manipulation_high,
                "price": last_close,
                "manipulation_pct":
                    round(
                        manipulation_pct,
                        4,
                    ),
                "recovery_ratio":
                    round(
                        recovery_ratio,
                        3,
                    ),
                "body_ratio":
                    round(
                        body_ratio,
                        3,
                    ),
                "reason":
                    "5M L-reversal после снятия BSL",
            }

    return None


# ============================================================
# ENTRY
# ============================================================

def calculate_entry(
    current_price,
    confirmation_5m=None,
):

    if confirmation_5m:

        price = _safe_float(
            confirmation_5m.get("price")
        )

        if price is not None:
            return price

    return _safe_float(
        current_price
    )


# ============================================================
# STOP LOSS
# ============================================================

def calculate_stop(
    sweep,
    direction,
):

    if not sweep:
        return None

    extreme = _safe_float(
        sweep.get("extreme")
    )

    if extreme is None:
        return None

    buffer = (
        SL_BUFFER_PCT
        /
        100.0
    )

    if direction == "LONG":

        return (
            extreme
            *
            (1.0 - buffer)
        )

    if direction == "SHORT":

        return (
            extreme
            *
            (1.0 + buffer)
        )

    return None


# ============================================================
# TAKE PROFIT
# ============================================================

def calculate_take_profit(
    major_levels,
    entry,
    direction,
):

    target = get_next_major_target(
        major_levels,
        entry,
        direction,
    )

    if not target:
        return None

    tp = _safe_float(
        target.get("price")
    )

    if tp is None:
        return None

    if (
        _pct(
            tp,
            entry,
        )
        < MIN_TP_DISTANCE_PCT
    ):
        return None

    return tp


# ============================================================
# RR
# ============================================================

def calculate_rr(
    entry,
    stop_loss,
    take_profit,
    direction,
):

    entry = _safe_float(entry)
    stop_loss = _safe_float(stop_loss)
    take_profit = _safe_float(take_profit)

    if None in (
        entry,
        stop_loss,
        take_profit,
    ):
        return 0.0

    if direction == "LONG":

        risk = entry - stop_loss
        reward = take_profit - entry

    elif direction == "SHORT":

        risk = stop_loss - entry
        reward = entry - take_profit

    else:

        return 0.0

    if risk <= 0:
        return 0.0

    return reward / risk


# ============================================================
# TARGET VALIDATION
# ============================================================

def validate_target(
    major_levels,
    entry,
    take_profit,
    direction,
):

    if take_profit is None:
        return False

    target = get_next_major_target(
        major_levels,
        entry,
        direction,
    )

    if not target:
        return False

    target_price = _safe_float(
        target.get("price")
    )

    if target_price is None:
        return False

    tolerance = max(
        target_price * 0.001,
        0.000001,
    )

    return (
        abs(
            target_price
            -
            take_profit
        )
        <= tolerance
    )


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    d1_ok,
    h1_ok,
    liquidity_ok,
    sweep_ok,
    confirmation_15m_ok,
    confirmation_5m_ok,
    target_ok,
):

    score = 0

    if d1_ok:
        score += 20

    if h1_ok:
        score += 20

    if liquidity_ok:
        score += 10

    if sweep_ok:
        score += 20

    if confirmation_15m_ok:
        score += 10

    if confirmation_5m_ok:
        score += 10

    if target_ok:
        score += 10

    return min(
        score,
        100,
    )


# ============================================================
# RESULT
# ============================================================

@dataclass
class StrategyResult:

    direction: str = "NEUTRAL"

    stage: str = "WAIT"

    score: int = 0

    reason: str = ""

    liquidity: Optional[
        Dict[str, Any]
    ] = None

    sweep: Optional[
        Dict[str, Any]
    ] = None

    confirmation_15m: Optional[
        Dict[str, Any]
    ] = None

    confirmation_5m: Optional[
        Dict[str, Any]
    ] = None

    trigger_5m: Optional[
        Dict[str, Any]
    ] = None

    entry: Optional[
        float
    ] = None

    stop_loss: Optional[
        float
    ] = None

    take_profit: Optional[
        float
    ] = None

    rr: Optional[
        float
    ] = None

    # --------------------------------------------------------
    # BOTH LIQUIDITY SIDES
    # --------------------------------------------------------

    liquidity_above: Optional[
        List[Dict[str, Any]]
    ] = None

    liquidity_below: Optional[
        List[Dict[str, Any]]
    ] = None

    major_liquidity_above: Optional[
        List[Dict[str, Any]]
    ] = None

    major_liquidity_below: Optional[
        List[Dict[str, Any]]
    ] = None

    # --------------------------------------------------------
    # SMART MONEY CONTEXT
    # --------------------------------------------------------

    htf_source: Optional[
        str
    ] = None

    d1_structure: Optional[
        Dict[str, Any]
    ] = None

    h1_structure: Optional[
        Dict[str, Any]
    ] = None

    fvg_1h: Optional[
        Dict[str, Any]
    ] = None

    tp_reason: Optional[
        str
    ] = None

    sweep_extreme: Optional[
        float
    ] = None

    def to_dict(self):

        data = asdict(self)

        # Compatibility aliases
        data["confirmation"] = (
            self.confirmation_5m
        )

        data["trigger_5m"] = (
            self.trigger_5m
        )

        return data


# ============================================================
# MAIN ANALYSIS
# ============================================================

def analyze(
    candles_1h,
    candles_15m,
    candles_5m,
    current_price,
    major_levels,
    sweep=None,
    candles_d1=None,
    candles_w1=None,
    order_flow=None,
):

    current_price = _safe_float(
        current_price
    )

    if current_price is None:

        return StrategyResult(
            reason="Нет текущей цены"
        ).to_dict()

    # ========================================================
    # LIQUIDITY SIDES
    # ========================================================

    liquidity_sides = get_liquidity_sides(
        major_levels,
        current_price,
        max_levels_each_side=6,
    )

    above = liquidity_sides[
        "above"
    ]

    below = liquidity_sides[
        "below"
    ]

    # ========================================================
    # HIGHER TIMEFRAME
    # ========================================================

    htf = get_higher_timeframe_direction(
        candles_d1,
        candles_w1,
    )

    direction = htf[
        "direction"
    ]

    result = StrategyResult(
        direction=direction,
        liquidity_above=above,
        liquidity_below=below,
        major_liquidity_above=above,
        major_liquidity_below=below,
        htf_source=htf.get("source"),
        d1_structure=(
            structure_context(candles_d1)
            if candles_d1
            else None
        ),
    )

    # ========================================================
    # NO HTF DIRECTION
    # ========================================================

    if direction == "NEUTRAL":

        result.stage = "WAIT"

        result.score = 0

        result.reason = (
            "D1/W1 не дают направления"
        )

        return result.to_dict()

    # ========================================================
    # 1H STRUCTURE
    # ========================================================

    h1 = structure_context(
        candles_1h
    )

    result.h1_structure = h1

    result.fvg_1h = find_fvg(
        candles_1h,
        direction,
    )

    expected_h1 = (
        "BULLISH"
        if direction == "LONG"
        else "BEARISH"
    )

    h1_ok = (
        h1["direction"]
        ==
        expected_h1
    )

    # --------------------------------------------------------
    # Do not kill setup immediately if 1H structure is
    # temporarily neutral.
    #
    # But a direct opposite 1H structure is blocked.
    # --------------------------------------------------------

    if (
        h1["direction"]
        not in {
            expected_h1,
            "NEUTRAL",
        }
    ):

        result.stage = "WAIT"

        result.score = 35

        result.reason = (
            "1H структура направлена против D1"
        )

        return result.to_dict()

    # ========================================================
    # DIRECTIONAL LIQUIDITY
    # ========================================================

    liquidity = (
        below[0]
        if direction == "LONG" and below
        else
        above[0]
        if direction == "SHORT" and above
        else None
    )

    result.liquidity = liquidity

    liquidity_ok = (
        liquidity is not None
    )

    # ========================================================
    # WAIT FOR SWEEP
    # ========================================================

    if not liquidity_ok:

        result.stage = "WAIT"

        result.score = calculate_score(
            True,
            h1_ok,
            False,
            False,
            False,
            False,
            False,
        )

        result.reason = (
            "Нет свежей major liquidity "
            "в направлении сценария"
        )

        return result.to_dict()

    # ========================================================
    # SWEEP
    # ========================================================

    active_sweep = sweep

    if active_sweep is None:

        result.stage = "WAIT"

        result.score = calculate_score(
            True,
            h1_ok,
            True,
            False,
            False,
            False,
            False,
        )

        if direction == "LONG":

            result.reason = (
                "Ждём sweep SSL"
            )

        else:

            result.reason = (
                "Ждём sweep BSL"
            )

        return result.to_dict()

    # ========================================================
    # VERIFY SWEEP DIRECTION
    # ========================================================

    sweep_direction = active_sweep.get(
        "direction"
    )

    if sweep_direction != direction:

        result.stage = "WAIT"

        result.score = 45

        result.reason = (
            "Sweep произошёл не в сторону "
            "текущего сценария"
        )

        return result.to_dict()

    result.sweep = active_sweep

    result.sweep_extreme = _safe_float(
        active_sweep.get("extreme")
    )

    # ========================================================
    # AFTER SWEEP → 15M
    # ========================================================

    conf_15m = confirmation_15m(
        candles_15m,
        direction,
    )

    result.confirmation_15m = (
        conf_15m
    )

    if not conf_15m:

        result.stage = "SWEPT"

        result.score = calculate_score(
            True,
            h1_ok,
            True,
            True,
            False,
            False,
            False,
        )

        result.reason = (
            "Major liquidity снята. "
            "Ждём 15M confirmation"
        )

        return result.to_dict()

    # ========================================================
    # AFTER 15M → 5M
    # ========================================================

    conf_5m = detect_5m_ilm(
        candles_5m,
        direction,
    )

    result.confirmation_5m = (
        conf_5m
    )

    result.trigger_5m = (
        conf_5m
    )

    if not conf_5m:

        result.stage = "15M_CONFIRMED"

        result.score = calculate_score(
            True,
            h1_ok,
            True,
            True,
            True,
            False,
            False,
        )

        result.reason = (
            "15M подтверждение есть. "
            "Ждём 5M ILM trigger"
        )

        return result.to_dict()

    # ========================================================
    # ENTRY
    # ========================================================

    entry = calculate_entry(
        current_price,
        conf_5m,
    )

    result.entry = entry

    # ========================================================
    # STOP
    # ========================================================

    stop_loss = calculate_stop(
        active_sweep,
        direction,
    )

    result.stop_loss = stop_loss

    if stop_loss is None:

        result.stage = "WAIT"

        result.score = 70

        result.reason = (
            "Не удалось рассчитать SL"
        )

        return result.to_dict()

    # ========================================================
    # TP = NEXT MAJOR LIQUIDITY
    # ========================================================

    target = get_next_major_target(
        major_levels,
        entry,
        direction,
    )

    if not target:

        result.stage = "WAIT"

        result.score = 70

        result.reason = (
            "Нет следующей неснятой "
            "major liquidity для TP"
        )

        return result.to_dict()

    take_profit = _safe_float(
        target.get("price")
    )

    result.take_profit = (
        take_profit
    )

    result.tp_reason = (
        "Следующая major liquidity"
    )

    # ========================================================
    # RR
    # ========================================================

    rr = calculate_rr(
        entry,
        stop_loss,
        take_profit,
        direction,
    )

    result.rr = round(
        rr,
        2,
    )

    # ========================================================
    # HARD RR FILTER
    # ========================================================

    if rr < MIN_RR:

        result.stage = "WAIT"

        result.score = 70

        result.reason = (
            f"RR {rr:.2f} < 1:2. "
            "Вход запрещён"
        )

        return result.to_dict()

    # ========================================================
    # TARGET VALIDATION
    # ========================================================

    target_ok = validate_target(
        major_levels,
        entry,
        take_profit,
        direction,
    )

    if not target_ok:

        result.stage = "WAIT"

        result.score = 70

        result.reason = (
            "TP не совпадает "
            "со следующей major liquidity"
        )

        return result.to_dict()

    # ========================================================
    # FINAL SCORE
    # ========================================================

    score = calculate_score(
        True,
        h1_ok,
        True,
        True,
        True,
        True,
        True,
    )

    result.score = score

    # ========================================================
    # READY
    # ========================================================

    if score >= MIN_SCORE_READY:

        result.stage = "READY"

        result.reason = (
            "Полный SMC-сетап: "
            "D1 → 1H → Major Liquidity "
            "→ Sweep → 15M → 5M ILM"
        )

        return result.to_dict()

    # ========================================================
    # FALLBACK
    # ========================================================

    result.stage = "WAIT"

    result.reason = (
        "Сетап не достиг минимального score"
    )

    return result.to_dict()


# ============================================================
# EXPORTS
# ============================================================

__all__ = [

    "STRATEGY_VERSION",

    "MIN_SCORE_READY",

    "MIN_RR",

    "SL_BUFFER_PCT",

    "structure_context",

    "get_higher_timeframe_direction",

    "get_directional_liquidity",

    "get_liquidity_sides",

    "get_next_major_target",

    "find_fvg",

    "confirmation_15m",

    "detect_5m_ilm",

    "calculate_entry",

    "calculate_stop",

    "calculate_take_profit",

    "calculate_rr",

    "validate_target",

    "calculate_score",

    "analyze",

]