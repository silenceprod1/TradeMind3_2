from dataclasses import dataclass
from typing import Any, Dict, List, Optional


STRATEGY_VERSION = "6.0.0"


# ============================================================
# CONFIG
# ============================================================

MIN_SCORE_READY = 80

# Минимальный допустимый RR
MIN_RR = 2.0

# Буфер за extreme sweep
# LONG  -> SL ниже extreme
# SHORT -> SL выше extreme
SL_BUFFER_PCT = 0.20

# Минимальная манипуляция на 5M
MIN_5M_MANIPULATION_PCT = 0.08

# Минимальное восстановление после манипуляции
MIN_5M_RECOVERY_PCT = 0.33

# Минимальное расстояние от Entry до TP,
# чтобы TP не оказался фактически в том же месте
MIN_TP_DISTANCE_PCT = 0.15

SWING_LOOKBACK_D1 = 120
SWING_LOOKBACK_1H = 80
SWING_LOOKBACK_15M = 80
SWING_LOOKBACK_5M = 60


# ============================================================
# RESULT
# ============================================================

@dataclass
class StrategyResult:

    direction: Optional[str] = None

    stage: str = "D1"

    score: int = 0

    reason: str = ""

    liquidity: Optional[Dict[str, Any]] = None

    sweep: Optional[Dict[str, Any]] = None

    confirmation_15m: Optional[Dict[str, Any]] = None

    confirmation_5m: Optional[Dict[str, Any]] = None

    trigger_5m: Optional[Dict[str, Any]] = None

    entry: Optional[float] = None

    stop_loss: Optional[float] = None

    take_profit: Optional[float] = None

    rr: Optional[float] = None

    def to_dict(self):

        return {
            "direction": self.direction,

            "stage": self.stage,

            "score": self.score,

            "reason": self.reason,

            "liquidity": self.liquidity,

            "sweep": self.sweep,

            "confirmation_15m": self.confirmation_15m,

            "confirmation_5m": self.confirmation_5m,

            "confirmation": self.confirmation_5m,

            "trigger_5m": self.trigger_5m,

            "entry": self.entry,

            "stop_loss": self.stop_loss,

            "take_profit": self.take_profit,

            "rr": self.rr,
        }


# ============================================================
# BASIC HELPERS
# ============================================================

def _safe_float(value, default=None):

    try:
        return float(value)
    except Exception:
        return default


def _pct(a, b):

    if a is None or b in (None, 0):
        return 0.0

    return abs(a - b) / abs(b) * 100.0


def _body(candle):

    if not candle:
        return 0.0

    return abs(
        _safe_float(candle.get("close"), 0.0)
        -
        _safe_float(candle.get("open"), 0.0)
    )


def _range(candle):

    if not candle:
        return 0.0

    return (
        _safe_float(candle.get("high"), 0.0)
        -
        _safe_float(candle.get("low"), 0.0)
    )


def _body_ratio(candle):

    r = _range(candle)

    if r <= 0:
        return 0.0

    return _body(candle) / r


def _bullish(candle):

    return (
        _safe_float(candle.get("close"), 0.0)
        >
        _safe_float(candle.get("open"), 0.0)
    )


def _bearish(candle):

    return (
        _safe_float(candle.get("close"), 0.0)
        <
        _safe_float(candle.get("open"), 0.0)
    )


# ============================================================
# SWINGS
# ============================================================

def _swing_highs(candles, lookback):

    if not candles:
        return []

    data = candles[-lookback:]

    result = []

    for i in range(1, len(data) - 1):

        current = _safe_float(
            data[i].get("high")
        )

        if current is None:
            continue

        left = _safe_float(
            data[i - 1].get("high")
        )

        right = _safe_float(
            data[i + 1].get("high")
        )

        if left is None or right is None:
            continue

        if (
            current > left
            and current >= right
        ):

            result.append({
                "index": i,
                "price": current,
                "time": data[i].get("open_time"),
            })

    return result


def _swing_lows(candles, lookback):

    if not candles:
        return []

    data = candles[-lookback:]

    result = []

    for i in range(1, len(data) - 1):

        current = _safe_float(
            data[i].get("low")
        )

        if current is None:
            continue

        left = _safe_float(
            data[i - 1].get("low")
        )

        right = _safe_float(
            data[i + 1].get("low")
        )

        if left is None or right is None:
            continue

        if (
            current < left
            and current <= right
        ):

            result.append({
                "index": i,
                "price": current,
                "time": data[i].get("open_time"),
            })

    return result


# ============================================================
# MARKET STRUCTURE
# ============================================================

def structure_context(
    candles,
    swing_lookback=80
):

    if not candles or len(candles) < 20:
        return "NEUTRAL"

    data = candles[-swing_lookback:]

    highs = _swing_highs(
        data,
        len(data)
    )

    lows = _swing_lows(
        data,
        len(data)
    )

    # --------------------------------------------------------
    # CLASSICAL STRUCTURE
    # --------------------------------------------------------

    if len(highs) >= 2 and len(lows) >= 2:

        previous_high = highs[-2]["price"]

        latest_high = highs[-1]["price"]

        previous_low = lows[-2]["price"]

        latest_low = lows[-1]["price"]

        # HH + HL
        if (
            latest_high > previous_high
            and latest_low > previous_low
        ):
            return "BULLISH"

        # LH + LL
        if (
            latest_high < previous_high
            and latest_low < previous_low
        ):
            return "BEARISH"

    latest_close = _safe_float(
        data[-1].get("close")
    )

    if latest_close is None:
        return "NEUTRAL"

    # --------------------------------------------------------
    # BULLISH BOS
    # --------------------------------------------------------

    if highs:

        latest_swing_high = highs[-1]["price"]

        if latest_close > latest_swing_high:
            return "BULLISH"

    # --------------------------------------------------------
    # BEARISH BOS
    # --------------------------------------------------------

    if lows:

        latest_swing_low = lows[-1]["price"]

        if latest_close < latest_swing_low:
            return "BEARISH"

    # --------------------------------------------------------
    # RECENT IMPULSE
    # --------------------------------------------------------

    recent = data[-12:]

    if len(recent) >= 6:

        first_close = _safe_float(
            recent[0].get("close")
        )

        last_close = _safe_float(
            recent[-1].get("close")
        )

        if (
            first_close is not None
            and last_close is not None
            and first_close != 0
        ):

            move = (
                (last_close - first_close)
                / first_close
                * 100.0
            )

            if move >= 1.0:
                return "BULLISH"

            if move <= -1.0:
                return "BEARISH"

    return "NEUTRAL"


# ============================================================
# D1 / W1 CONTEXT
# ============================================================

def get_higher_timeframe_direction(
    candles_d1,
    candles_w1
):

    d1 = structure_context(
        candles_d1,
        SWING_LOOKBACK_D1
    )

    # D1 главный
    if d1 in ("BULLISH", "BEARISH"):
        return d1, d1

    # W1 fallback
    w1 = structure_context(
        candles_w1,
        SWING_LOOKBACK_D1
    )

    if w1 in ("BULLISH", "BEARISH"):
        return w1, d1

    return None, d1


# ============================================================
# LIQUIDITY NORMALIZATION
# ============================================================

def _normalize_liquidity_levels(
    major_levels
):

    if not major_levels:
        return []

    result = []

    for level in major_levels:

        if not isinstance(level, dict):
            continue

        price = level.get(
            "price",
            level.get("level")
        )

        price = _safe_float(price)

        if price is None or price <= 0:
            continue

        item = dict(level)

        item["price"] = price

        result.append(item)

    return result


# ============================================================
# DIRECTIONAL LIQUIDITY FOR SWEEP
# ============================================================

def get_directional_liquidity(
    major_levels,
    current_price,
    direction
):

    current_price = _safe_float(
        current_price
    )

    if current_price is None:
        return None

    levels = _normalize_liquidity_levels(
        major_levels
    )

    candidates = []

    for level in levels:

        price = level["price"]

        # Already swept liquidity
        # is not fresh.
        if level.get("swept") is True:
            continue

        if direction == "LONG":

            # SSL below price
            if price < current_price:

                candidates.append(level)

        elif direction == "SHORT":

            # BSL above price
            if price > current_price:

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
# NEXT MAJOR LIQUIDITY TARGET
# ============================================================

def get_next_major_target(
    major_levels,
    entry,
    direction
):
    """
    TP is ALWAYS the next major UNSWEPT
    liquidity in the direction of the trade.

    LONG:
        next major liquidity ABOVE Entry

    SHORT:
        next major liquidity BELOW Entry

    The swept liquidity that created the setup
    is never reused as TP.
    """

    entry = _safe_float(entry)

    if entry is None or entry <= 0:
        return None

    levels = _normalize_liquidity_levels(
        major_levels
    )

    candidates = []

    for level in levels:

        price = level["price"]

        # Never use already swept liquidity.
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
            reverse=True
        )

    return candidates[0]


# ============================================================
# FVG
# ============================================================

def find_fvg(
    candles,
    direction
):

    if not candles or len(candles) < 3:
        return None

    data = candles[-3:]

    c1 = data[0]
    c3 = data[2]

    c1_high = _safe_float(
        c1.get("high")
    )

    c1_low = _safe_float(
        c1.get("low")
    )

    c3_high = _safe_float(
        c3.get("high")
    )

    c3_low = _safe_float(
        c3.get("low")
    )

    if None in (
        c1_high,
        c1_low,
        c3_high,
        c3_low,
    ):
        return None

    if direction == "LONG":

        if c3_low > c1_high:

            return {
                "direction": "LONG",
                "low": c1_high,
                "high": c3_low,
            }

    if direction == "SHORT":

        if c3_high < c1_low:

            return {
                "direction": "SHORT",
                "low": c3_high,
                "high": c1_low,
            }

    return None


# ============================================================
# 15M CONFIRMATION
# ============================================================

def confirmation_15m(
    candles_15m,
    direction,
    sweep
):

    if not candles_15m or len(candles_15m) < 10:
        return None

    recent = candles_15m[-8:]

    candle = recent[-1]

    if direction == "LONG":

        previous = recent[:-1]

        highs = [
            _safe_float(c.get("high"))
            for c in previous
        ]

        highs = [
            x for x in highs
            if x is not None
        ]

        if not highs:
            return None

        recent_high = max(highs)

        close = _safe_float(
            candle.get("close")
        )

        if close is None:
            return None

        body_close = (
            close > recent_high
        )

        bullish_body = _bullish(
            candle
        )

        if (
            body_close
            and bullish_body
        ):

            return {
                "confirmed": True,
                "direction": "LONG",
                "price": close,
                "time": candle.get("open_time"),
                "type": "15M BULLISH CONFIRMATION",
            }

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        previous = recent[:-1]

        lows = [
            _safe_float(c.get("low"))
            for c in previous
        ]

        lows = [
            x for x in lows
            if x is not None
        ]

        if not lows:
            return None

        recent_low = min(lows)

        close = _safe_float(
            candle.get("close")
        )

        if close is None:
            return None

        body_close = (
            close < recent_low
        )

        bearish_body = _bearish(
            candle
        )

        if (
            body_close
            and bearish_body
        ):

            return {
                "confirmed": True,
                "direction": "SHORT",
                "price": close,
                "time": candle.get("open_time"),
                "type": "15M BEARISH CONFIRMATION",
            }

    return None


# ============================================================
# 5M ILM
# ============================================================

def detect_5m_ilm(
    candles_5m,
    direction,
    sweep
):

    if not candles_5m or len(candles_5m) < 10:
        return None

    data = candles_5m[-10:]

    last = data[-1]

    previous = data[:-1]

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        lowest_candle = min(
            previous,
            key=lambda x: _safe_float(
                x.get("low"),
                0.0
            )
        )

        manipulation_low = _safe_float(
            lowest_candle.get("low")
        )

        if manipulation_low is None:
            return None

        highs = [
            _safe_float(
                c.get("high")
            )
            for c in previous
        ]

        highs = [
            x for x in highs
            if x is not None
        ]

        if not highs:
            return None

        reference = max(highs)

        total_range = (
            reference
            - manipulation_low
        )

        if total_range <= 0:
            return None

        close = _safe_float(
            last.get("close")
        )

        if close is None:
            return None

        recovery = (
            close
            - manipulation_low
        )

        recovery_ratio = (
            recovery
            / total_range
        )

        meaningful_manipulation = (
            _pct(
                manipulation_low,
                reference
            )
            >= MIN_5M_MANIPULATION_PCT
        )

        bullish_close = _bullish(
            last
        )

        body_ratio = _body_ratio(
            last
        )

        if (
            meaningful_manipulation
            and recovery_ratio >= MIN_5M_RECOVERY_PCT
            and bullish_close
            and body_ratio >= 0.35
        ):

            return {
                "confirmed": True,
                "direction": "LONG",
                "type": "5M ILM V-REVERSAL",
                "manipulation": manipulation_low,
                "recovery_ratio": round(
                    recovery_ratio,
                    3
                ),
                "body_ratio": round(
                    body_ratio,
                    3
                ),
                "price": close,
                "time": last.get("open_time"),
            }

    # ========================================================
    # SHORT
    # ========================================================

    if direction == "SHORT":

        highest_candle = max(
            previous,
            key=lambda x: _safe_float(
                x.get("high"),
                0.0
            )
        )

        manipulation_high = _safe_float(
            highest_candle.get("high")
        )

        if manipulation_high is None:
            return None

        lows = [
            _safe_float(
                c.get("low")
            )
            for c in previous
        ]

        lows = [
            x for x in lows
            if x is not None
        ]

        if not lows:
            return None

        reference = min(lows)

        total_range = (
            manipulation_high
            - reference
        )

        if total_range <= 0:
            return None

        close = _safe_float(
            last.get("close")
        )

        if close is None:
            return None

        recovery = (
            manipulation_high
            - close
        )

        recovery_ratio = (
            recovery
            / total_range
        )

        meaningful_manipulation = (
            _pct(
                manipulation_high,
                reference
            )
            >= MIN_5M_MANIPULATION_PCT
        )

        bearish_close = _bearish(
            last
        )

        body_ratio = _body_ratio(
            last
        )

        if (
            meaningful_manipulation
            and recovery_ratio >= MIN_5M_RECOVERY_PCT
            and bearish_close
            and body_ratio >= 0.35
        ):

            return {
                "confirmed": True,
                "direction": "SHORT",
                "type": "5M ILM L-REVERSAL",
                "manipulation": manipulation_high,
                "recovery_ratio": round(
                    recovery_ratio,
                    3
                ),
                "body_ratio": round(
                    body_ratio,
                    3
                ),
                "price": close,
                "time": last.get("open_time"),
            }

    return None


# ============================================================
# ENTRY
# ============================================================

def calculate_entry(
    current_price,
    direction,
    confirmation_5m
):
    """
    Entry is based on the confirmed 5M trigger.

    We DO NOT simply use current price if a valid
    5M confirmation exists.

    This prevents the bot from waiting for an
    arbitrary distant Entry.
    """

    if confirmation_5m:

        price = _safe_float(
            confirmation_5m.get("price")
        )

        if price is not None and price > 0:
            return price

    current_price = _safe_float(
        current_price
    )

    if current_price is not None and current_price > 0:
        return current_price

    return None


# ============================================================
# STOP LOSS
# ============================================================

def calculate_stop(
    candles_5m,
    direction,
    sweep
):
    """
    SL is always behind the actual sweep extreme.

    LONG:
        extreme - 0.20%

    SHORT:
        extreme + 0.20%

    No fallback to random 5M high/low.
    """

    if not sweep:
        return None

    extreme = _safe_float(
        sweep.get("extreme")
    )

    if extreme is None or extreme <= 0:
        return None

    buffer = (
        extreme
        * SL_BUFFER_PCT
        / 100.0
    )

    if direction == "LONG":

        return extreme - buffer

    if direction == "SHORT":

        return extreme + buffer

    return None


# ============================================================
# TAKE PROFIT
# ============================================================

def calculate_take_profit(
    entry,
    stop_loss,
    direction,
    major_levels
):
    """
    NEW TP DOCTRINE:

    TP = NEXT MAJOR UNSWEPT LIQUIDITY.

    We do NOT force TP to 2R.

    Example:

        risk = $0.20
        next liquidity = $0.60 away

        RR = 1:3

        TP stays at liquidity.

    If next liquidity gives only 1:1.5:

        NO TRADE.

    We never move TP artificially farther
    just to manufacture 1:2.
    """

    entry = _safe_float(entry)

    stop_loss = _safe_float(
        stop_loss
    )

    if (
        entry is None
        or stop_loss is None
        or entry <= 0
        or stop_loss <= 0
    ):
        return None

    target = get_next_major_target(
        major_levels,
        entry,
        direction
    )

    if not target:
        return None

    tp = _safe_float(
        target.get("price")
    )

    if tp is None or tp <= 0:
        return None

    if direction == "LONG":

        if tp <= entry:
            return None

    elif direction == "SHORT":

        if tp >= entry:
            return None

    else:
        return None

    distance_pct = _pct(
        tp,
        entry
    )

    if distance_pct < MIN_TP_DISTANCE_PCT:
        return None

    return tp


# ============================================================
# RR CALCULATION
# ============================================================

def calculate_rr(
    entry,
    stop_loss,
    take_profit
):

    entry = _safe_float(entry)

    stop_loss = _safe_float(
        stop_loss
    )

    take_profit = _safe_float(
        take_profit
    )

    if (
        entry is None
        or stop_loss is None
        or take_profit is None
    ):
        return None

    risk = abs(
        entry - stop_loss
    )

    reward = abs(
        take_profit - entry
    )

    if risk <= 0:
        return None

    return reward / risk


# ============================================================
# TARGET VALIDATION
# ============================================================

def validate_target(
    entry,
    stop_loss,
    take_profit,
    direction,
    major_levels
):
    """
    STRICT TARGET RULE:

        RR >= 1:2

    Not exactly 1:2.

    1:2.0  -> valid
    1:2.3  -> valid
    1:3.0  -> valid

    1:1.99 -> invalid
    1:1.8  -> invalid
    1:1.5  -> invalid
    """

    entry = _safe_float(entry)

    stop_loss = _safe_float(
        stop_loss
    )

    take_profit = _safe_float(
        take_profit
    )

    if (
        entry is None
        or stop_loss is None
        or take_profit is None
    ):

        return False, "Entry / SL / TP отсутствуют."

    if (
        entry <= 0
        or stop_loss <= 0
        or take_profit <= 0
    ):

        return False, "Некорректные Entry / SL / TP."

    # --------------------------------------------------------
    # Direction validation
    # --------------------------------------------------------

    if direction == "LONG":

        if stop_loss >= entry:

            return False, (
                "LONG: SL должен быть ниже Entry."
            )

        if take_profit <= entry:

            return False, (
                "LONG: TP должен быть выше Entry."
            )

    elif direction == "SHORT":

        if stop_loss <= entry:

            return False, (
                "SHORT: SL должен быть выше Entry."
            )

        if take_profit >= entry:

            return False, (
                "SHORT: TP должен быть ниже Entry."
            )

    else:

        return False, "Неизвестное направление."

    # --------------------------------------------------------
    # RR
    # --------------------------------------------------------

    rr = calculate_rr(
        entry,
        stop_loss,
        take_profit
    )

    if rr is None:

        return False, (
            "Невозможно рассчитать RR."
        )

    # --------------------------------------------------------
    # HARD MINIMUM 1:2
    # --------------------------------------------------------

    if rr < MIN_RR:

        return False, (
            f"RR {rr:.2f} меньше минимального 1:2."
        )

    # --------------------------------------------------------
    # Check that TP corresponds to an actual
    # major unswept liquidity level.
    # --------------------------------------------------------

    target = get_next_major_target(
        major_levels,
        entry,
        direction
    )

    if not target:

        return False, (
            "Следующая крупная неснятая "
            "ликвидность не найдена."
        )

    target_price = _safe_float(
        target.get("price")
    )

    if target_price is None:

        return False, (
            "Цена target liquidity неизвестна."
        )

    # TP should be the actual target,
    # not an artificial 2R price.
    tolerance = max(
        abs(entry) * 0.001,
        0.00000001
    )

    if abs(
        take_profit - target_price
    ) > tolerance:

        return False, (
            "TP не совпадает со следующей "
            "крупной неснятой ликвидностью."
        )

    return True, (
        f"TP = next major liquidity. "
        f"RR = {rr:.2f}."
    )


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    d1,
    h1,
    direction,
    liquidity,
    sweep,
    confirmation_15,
    confirmation_5,
    rr_valid
):

    score = 0

    # --------------------------------------------------------
    # Higher timeframe
    # --------------------------------------------------------

    if d1 in (
        "BULLISH",
        "BEARISH"
    ):
        score += 20

    # --------------------------------------------------------
    # 1H sync
    # --------------------------------------------------------

    if h1 == direction:
        score += 20

    # --------------------------------------------------------
    # Major liquidity
    # --------------------------------------------------------

    if liquidity:
        score += 10

    # --------------------------------------------------------
    # Sweep
    # --------------------------------------------------------

    if sweep:
        score += 20

    # --------------------------------------------------------
    # 15M
    # --------------------------------------------------------

    if confirmation_15:
        score += 10

    # --------------------------------------------------------
    # 5M
    # --------------------------------------------------------

    if confirmation_5:
        score += 10

    # --------------------------------------------------------
    # Valid target + RR
    # --------------------------------------------------------

    if rr_valid:
        score += 10

    return min(
        100,
        score
    )


# ============================================================
# SCORE LABEL
# ============================================================

def score_label(score):

    if score >= 90:
        return "A+"

    if score >= 80:
        return "TRADE"

    if score >= 70:
        return "WAIT"

    return "NO TRADE"


# ============================================================
# MAIN ANALYZE
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

    result = StrategyResult()

    current_price = _safe_float(
        current_price
    )

    # ========================================================
    # 1. D1 / W1
    # ========================================================

    direction, d1 = get_higher_timeframe_direction(
        candles_d1,
        candles_w1
    )

    result.stage = "D1"

    if direction is None:

        result.score = 20

        result.reason = (
            f"D1={d1} → "
            "старший таймфрейм не определён."
        )

        return result.to_dict()

    result.direction = (
        "LONG"
        if direction == "BULLISH"
        else "SHORT"
    )

    trade_direction = result.direction

    # ========================================================
    # 2. 1H
    # ========================================================

    h1 = structure_context(
        candles_1h,
        SWING_LOOKBACK_1H
    )

    result.stage = "1H"

    if h1 != direction:

        result.score = 35

        result.reason = (
            f"D1={d1}, 1H={h1} → "
            "1H НЕ СИНХРОНИЗИРОВАН."
        )

        return result.to_dict()

    # ========================================================
    # 3. MAJOR LIQUIDITY
    # ========================================================

    liquidity = get_directional_liquidity(
        major_levels,
        current_price,
        trade_direction
    )

    result.liquidity = liquidity

    result.stage = "LIQUIDITY"

    if not liquidity:

        result.score = 45

        result.reason = (
            f"D1={d1}, 1H={h1} → "
            "ждём MAJOR LIQUIDITY."
        )

        return result.to_dict()

    # ========================================================
    # 4. 1H SWEEP
    # ========================================================

    result.stage = "SWEEP"

    if sweep is None:

        result.score = 55

        result.reason = (
            f"D1={d1}, 1H={h1} → "
            f"ждём {trade_direction} MAJOR SWEEP."
        )

        return result.to_dict()

    sweep_direction = sweep.get(
        "direction"
    )

    if sweep_direction != trade_direction:

        result.score = 50

        result.reason = (
            "Sweep есть, но направление "
            "не соответствует сетапу."
        )

        return result.to_dict()

    sweep_extreme = _safe_float(
        sweep.get("extreme")
    )

    if sweep_extreme is None:

        result.score = 55

        result.reason = (
            "Sweep подтверждён, "
            "но extreme не определён. "
            "Вход запрещён."
        )

        return result.to_dict()

    result.sweep = sweep

    # ========================================================
    # 5. 15M CONFIRMATION
    # ========================================================

    result.stage = "15M"

    confirm_15 = confirmation_15m(
        candles_15m,
        trade_direction,
        sweep
    )

    result.confirmation_15m = confirm_15

    if confirm_15 is None:

        result.score = 65

        result.reason = (
            f"{trade_direction} sweep подтверждён. "
            "Ждём 15M confirmation."
        )

        return result.to_dict()

    # ========================================================
    # 6. 5M ILM
    # ========================================================

    result.stage = "5M"

    confirm_5 = detect_5m_ilm(
        candles_5m,
        trade_direction,
        sweep
    )

    result.confirmation_5m = confirm_5

    result.trigger_5m = confirm_5

    if confirm_5 is None:

        result.score = 75

        result.reason = (
            "15M confirmation есть. "
            "Ждём 5M ILM trigger."
        )

        return result.to_dict()

    # ========================================================
    # 7. ENTRY
    # ========================================================

    entry = calculate_entry(
        current_price,
        trade_direction,
        confirm_5
    )

    result.entry = entry

    if entry is None or entry <= 0:

        result.score = 70

        result.reason = (
            "5M trigger есть, "
            "но Entry невозможно определить."
        )

        return result.to_dict()

    # ========================================================
    # 8. STOP LOSS
    # ========================================================

    stop_loss = calculate_stop(
        candles_5m,
        trade_direction,
        sweep
    )

    result.stop_loss = stop_loss

    if stop_loss is None:

        result.score = 70

        result.reason = (
            "SL невозможно определить "
            "за sweep extreme."
        )

        return result.to_dict()

    # --------------------------------------------------------
    # SL position validation
    # --------------------------------------------------------

    if trade_direction == "LONG":

        if stop_loss >= entry:

            result.score = 60

            result.reason = (
                "LONG: SL должен быть "
                "ниже Entry."
            )

            return result.to_dict()

    elif trade_direction == "SHORT":

        if stop_loss <= entry:

            result.score = 60

            result.reason = (
                "SHORT: SL должен быть "
                "выше Entry."
            )

            return result.to_dict()

    # ========================================================
    # 9. NEXT MAJOR LIQUIDITY
    # ========================================================

    target = get_next_major_target(
        major_levels,
        entry,
        trade_direction
    )

    if not target:

        result.score = 70

        result.reason = (
            "Следующая крупная "
            "неснятая ликвидность "
            "для TP не найдена."
        )

        return result.to_dict()

    # ========================================================
    # 10. TP = NEXT MAJOR LIQUIDITY
    # ========================================================

    take_profit = calculate_take_profit(
        entry,
        stop_loss,
        trade_direction,
        major_levels
    )

    result.take_profit = take_profit

    if take_profit is None:

        result.score = 70

        result.reason = (
            "TP на следующей крупной "
            "ликвидности невозможно определить."
        )

        return result.to_dict()

    # ========================================================
    # 11. RR
    # ========================================================

    rr = calculate_rr(
        entry,
        stop_loss,
        take_profit
    )

    result.rr = rr

    if rr is None:

        result.score = 70

        result.reason = (
            "Невозможно рассчитать RR."
        )

        return result.to_dict()

    # ========================================================
    # 12. HARD RR FILTER
    # ========================================================

    if rr < MIN_RR:

        result.score = 70

        result.reason = (
            f"Следующая крупная ликвидность "
            f"даёт только RR 1:{rr:.2f}. "
            f"Минимум 1:2 → NO TRADE."
        )

        result.stage = "WAIT"

        return result.to_dict()

    # ========================================================
    # 13. TARGET VALIDATION
    # ========================================================

    rr_valid, target_reason = validate_target(
        entry,
        stop_loss,
        take_profit,
        trade_direction,
        major_levels
    )

    if not rr_valid:

        result.score = 70

        result.reason = target_reason

        result.stage = "WAIT"

        return result.to_dict()

    # ========================================================
    # 14. FINAL SCORE
    # ========================================================

    result.score = calculate_score(
        d1=d1,
        h1=h1,
        direction=trade_direction,
        liquidity=liquidity,
        sweep=sweep,
        confirmation_15=confirm_15,
        confirmation_5=confirm_5,
        rr_valid=True,
    )

    label = score_label(
        result.score
    )

    # ========================================================
    # 15. FINAL READY
    # ========================================================

    if (
        result.score >= MIN_SCORE_READY

        and result.entry is not None

        and result.stop_loss is not None

        and result.take_profit is not None

        and result.rr is not None

        and result.rr >= MIN_RR
    ):

        result.stage = "READY"

        result.reason = (
            f"{label} SETUP → "
            f"{trade_direction}. "

            "D1 → 1H → MAJOR SWEEP → "
            "15M → 5M ILM подтверждены. "

            f"SL за sweep extreme "
            f"+ {SL_BUFFER_PCT:.2f}% buffer. "

            "TP = следующая крупная "
            "неснятая ликвидность. "

            f"RR = 1:{result.rr:.2f}."
        )

    else:

        result.stage = "WAIT"

        result.reason = (
            f"Score {result.score}/100 → "
            "сетап недостаточно качественный."
        )

    return result.to_dict()


# ============================================================
# EXPORTS
# ============================================================

__all__ = [

    "STRATEGY_VERSION",

    "StrategyResult",

    "analyze",

    "structure_context",

    "get_higher_timeframe_direction",

    "get_directional_liquidity",

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

    "score_label",
]