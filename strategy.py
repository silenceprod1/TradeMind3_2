from dataclasses import dataclass
from typing import Any, Dict, List, Optional


STRATEGY_VERSION = "5.8.3"


# ============================================================
# CONFIG
# ============================================================

MIN_SCORE_READY = 80

RR = 2.0

# Буфер за экстремумом sweep.
# SL не ставим ровно на high/low sweep.
SL_BUFFER_PCT = 0.05

MIN_5M_MANIPULATION_PCT = 0.08
MIN_5M_RECOVERY_PCT = 0.33

MIN_15M_CONFIRMATION_BODY = True

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

def _safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _pct(a, b):
    if b in (None, 0):
        return 0.0

    return abs(a - b) / abs(b) * 100.0


def _body(candle):
    return abs(
        _safe_float(candle.get("close"))
        - _safe_float(candle.get("open"))
    )


def _range(candle):
    return (
        _safe_float(candle.get("high"))
        - _safe_float(candle.get("low"))
    )


def _body_ratio(candle):
    r = _range(candle)

    if r <= 0:
        return 0.0

    return _body(candle) / r


def _bullish(candle):
    return (
        _safe_float(candle.get("close"))
        > _safe_float(candle.get("open"))
    )


def _bearish(candle):
    return (
        _safe_float(candle.get("close"))
        < _safe_float(candle.get("open"))
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

        current = data[i]["high"]

        if (
            current > data[i - 1]["high"]
            and current >= data[i + 1]["high"]
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

        current = data[i]["low"]

        if (
            current < data[i - 1]["low"]
            and current <= data[i + 1]["low"]
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

def structure_context(candles, swing_lookback=80):

    if not candles or len(candles) < 20:
        return "NEUTRAL"

    data = candles[-swing_lookback:]

    highs = _swing_highs(data, len(data))
    lows = _swing_lows(data, len(data))

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

    latest_close = data[-1]["close"]

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

        first_close = recent[0]["close"]
        last_close = recent[-1]["close"]

        if first_close:

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

def get_higher_timeframe_direction(candles_d1, candles_w1):

    d1 = structure_context(
        candles_d1,
        SWING_LOOKBACK_D1
    )

    if d1 in ("BULLISH", "BEARISH"):
        return d1, d1

    w1 = structure_context(
        candles_w1,
        SWING_LOOKBACK_D1
    )

    if w1 in ("BULLISH", "BEARISH"):
        return w1, d1

    return None, d1


# ============================================================
# LIQUIDITY
# ============================================================

def _normalize_liquidity_levels(major_levels):

    if not major_levels:
        return []

    result = []

    for level in major_levels:

        price = level.get(
            "price",
            level.get("level")
        )

        if price is None:
            continue

        item = dict(level)

        item["price"] = _safe_float(price)

        result.append(item)

    return result


def get_directional_liquidity(
    major_levels,
    current_price,
    direction
):

    levels = _normalize_liquidity_levels(
        major_levels
    )

    candidates = []

    for level in levels:

        price = level["price"]

        if price <= 0:
            continue

        if level.get("swept") is True:
            continue

        if direction == "LONG":

            if price < current_price:
                candidates.append(level)

        elif direction == "SHORT":

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
# FVG
# ============================================================

def find_fvg(candles, direction):

    if not candles or len(candles) < 3:
        return None

    data = candles[-3:]

    c1 = data[0]
    c3 = data[2]

    if direction == "LONG":

        if c3["low"] > c1["high"]:

            return {
                "direction": "LONG",
                "low": c1["high"],
                "high": c3["low"],
            }

    if direction == "SHORT":

        if c3["high"] < c1["low"]:

            return {
                "direction": "SHORT",
                "low": c3["high"],
                "high": c1["low"],
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

    if direction == "LONG":

        recent_high = max(
            c["high"]
            for c in recent[:-1]
        )

        candle = recent[-1]

        body_close = (
            candle["close"] > recent_high
        )

        bullish_body = _bullish(candle)

        if body_close and bullish_body:

            return {
                "confirmed": True,
                "direction": "LONG",
                "price": candle["close"],
                "time": candle.get("open_time"),
                "type": "15M BULLISH CONFIRMATION",
            }

    if direction == "SHORT":

        recent_low = min(
            c["low"]
            for c in recent[:-1]
        )

        candle = recent[-1]

        body_close = (
            candle["close"] < recent_low
        )

        bearish_body = _bearish(candle)

        if body_close and bearish_body:

            return {
                "confirmed": True,
                "direction": "SHORT",
                "price": candle["close"],
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

    if direction == "LONG":

        lowest = min(
            data[:-1],
            key=lambda x: x["low"]
        )

        manipulation_low = lowest["low"]

        reference = max(
            c["high"]
            for c in data[:-1]
        )

        recovery = (
            data[-1]["close"]
            - manipulation_low
        )

        total_range = (
            reference
            - manipulation_low
        )

        if total_range <= 0:
            return None

        recovery_ratio = (
            recovery / total_range
        )

        last = data[-1]

        bullish_close = (
            last["close"] > last["open"]
        )

        meaningful_manipulation = (
            _pct(
                manipulation_low,
                reference
            )
            >= MIN_5M_MANIPULATION_PCT
        )

        if (
            meaningful_manipulation
            and recovery_ratio >= MIN_5M_RECOVERY_PCT
            and bullish_close
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
                "price": last["close"],
                "time": last.get("open_time"),
            }

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        highest = max(
            data[:-1],
            key=lambda x: x["high"]
        )

        manipulation_high = highest["high"]

        reference = min(
            c["low"]
            for c in data[:-1]
        )

        recovery = (
            manipulation_high
            - data[-1]["close"]
        )

        total_range = (
            manipulation_high
            - reference
        )

        if total_range <= 0:
            return None

        recovery_ratio = (
            recovery / total_range
        )

        last = data[-1]

        bearish_close = (
            last["close"] < last["open"]
        )

        meaningful_manipulation = (
            _pct(
                manipulation_high,
                reference
            )
            >= MIN_5M_MANIPULATION_PCT
        )

        if (
            meaningful_manipulation
            and recovery_ratio >= MIN_5M_RECOVERY_PCT
            and bearish_close
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
                "price": last["close"],
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

    if confirmation_5m:

        price = confirmation_5m.get(
            "price",
            current_price
        )

        if price:
            return float(price)

    if current_price:
        return float(current_price)

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
    TradeMind SL:

    LONG:
        ниже extreme 1H sweep

    SHORT:
        выше extreme 1H sweep

    SL НЕ ставится ровно на extreme.
    Добавляется технический buffer.

    Если sweep отсутствует или extreme
    невозможно определить — SL = None.
    """

    if not sweep:
        return None

    extreme = sweep.get("extreme")

    if extreme is None:
        return None

    extreme = _safe_float(
        extreme,
        None
    )

    if extreme is None or extreme <= 0:
        return None

    buffer = (
        extreme
        * SL_BUFFER_PCT
        / 100.0
    )

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        return extreme - buffer

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        return extreme + buffer

    return None


# ============================================================
# TAKE PROFIT — EXACT 1:2
# ============================================================

def calculate_take_profit(
    entry,
    stop_loss,
    direction
):

    if entry is None or stop_loss is None:
        return None

    risk = abs(
        entry - stop_loss
    )

    if risk <= 0:
        return None

    if direction == "LONG":

        return entry + (
            risk * RR
        )

    if direction == "SHORT":

        return entry - (
            risk * RR
        )

    return None


# ============================================================
# STRUCTURAL TARGET CHECK
# ============================================================

def validate_target(
    entry,
    stop_loss,
    take_profit,
    direction,
    major_levels
):

    if (
        entry is None
        or stop_loss is None
        or take_profit is None
    ):
        return False, "Невозможно рассчитать RR."

    risk = abs(
        entry - stop_loss
    )

    if risk <= 0:
        return False, "Некорректный SL."

    reward = abs(
        take_profit - entry
    )

    rr = reward / risk

    # --------------------------------------------------------
    # EXACT 1:2
    # --------------------------------------------------------

    if abs(rr - RR) > 0.001:
        return False, "RR не равен 1:2."

    # --------------------------------------------------------
    # CHECK SL DIRECTION
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
    # MAJOR LIQUIDITY
    # --------------------------------------------------------

    levels = _normalize_liquidity_levels(
        major_levels
    )

    for level in levels:

        price = level["price"]

        # Уже снятая ликвидность не является
        # актуальной целью.
        if level.get("swept") is True:
            continue

        if direction == "LONG":

            if (
                price > entry
                and abs(
                    price - take_profit
                )
                <= risk * 0.15
            ):

                return False, (
                    "TP слишком близко к "
                    "major liquidity."
                )

        if direction == "SHORT":

            if (
                price < entry
                and abs(
                    price - take_profit
                )
                <= risk * 0.15
            ):

                return False, (
                    "TP слишком близко к "
                    "major liquidity."
                )

    return True, "TP валиден."


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

    if d1 in ("BULLISH", "BEARISH"):
        score += 20

    if h1 == direction:
        score += 20

    if liquidity:
        score += 10

    if sweep:
        score += 20

    if confirmation_15:
        score += 10

    if confirmation_5:
        score += 10

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
        current_price,
        None
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

    expected_h1 = direction

    if h1 != expected_h1:

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

    # Sweep должен иметь экстремум,
    # потому что от него рассчитывается SL.
    sweep_extreme = sweep.get("extreme")

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
    # 8. SL — ЗА SWEEP EXTREME
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
            "5M trigger есть, "
            "но SL невозможно определить "
            "за sweep extreme."
        )

        return result.to_dict()

    # --------------------------------------------------------
    # SL MUST BE ON CORRECT SIDE
    # --------------------------------------------------------

    if trade_direction == "LONG":

        if stop_loss >= entry:

            result.score = 60

            result.reason = (
                "LONG: SL должен быть "
                "ниже Entry и за sweep extreme."
            )

            return result.to_dict()

    elif trade_direction == "SHORT":

        if stop_loss <= entry:

            result.score = 60

            result.reason = (
                "SHORT: SL должен быть "
                "выше Entry и за sweep extreme."
            )

            return result.to_dict()

    # ========================================================
    # 9. EXACT 1:2 TP
    # ========================================================

    take_profit = calculate_take_profit(
        entry,
        stop_loss,
        trade_direction
    )

    result.take_profit = take_profit

    if take_profit is None:

        result.score = 70

        result.reason = (
            "Невозможно рассчитать TP 1:2."
        )

        return result.to_dict()

    # ========================================================
    # 10. VALIDATE TP / RR
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

        return result.to_dict()

    # ========================================================
    # 11. RR
    # ========================================================

    risk = abs(
        entry - stop_loss
    )

    reward = abs(
        take_profit - entry
    )

    result.rr = (
        reward / risk
        if risk > 0
        else None
    )

    # Жёсткая защита READY.
    if (
        result.entry is None
        or result.stop_loss is None
        or result.take_profit is None
        or result.rr is None
        or abs(result.rr - RR) > 0.001
    ):

        result.stage = "WAIT"
        result.score = 70

        result.reason = (
            "Entry / SL / TP / RR невалидны. "
            "Вход запрещён."
        )

        return result.to_dict()

    # ========================================================
    # 12. FINAL SCORE
    # ========================================================

    result.score = calculate_score(
        d1=d1,
        h1=h1,
        direction=trade_direction,
        liquidity=liquidity,
        sweep=sweep,
        confirmation_15=confirm_15,
        confirmation_5=confirm_5,
        rr_valid=rr_valid,
    )

    label = score_label(
        result.score
    )

    # ========================================================
    # FINAL DECISION
    # ========================================================

    if (
        result.score >= MIN_SCORE_READY
        and result.entry is not None
        and result.stop_loss is not None
        and result.take_profit is not None
        and result.rr is not None
        and abs(result.rr - RR) <= 0.001
    ):

        result.stage = "READY"

        result.reason = (
            f"{label} SETUP → "
            f"{trade_direction}. "
            "D1 → 1H → MAJOR SWEEP → "
            "15M → 5M ILM подтверждены. "
            "SL за sweep extreme. "
            "TP = EXACT 1:2."
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
    "find_fvg",
    "confirmation_15m",
    "detect_5m_ilm",
    "calculate_entry",
    "calculate_stop",
    "calculate_take_profit",
    "validate_target",
    "calculate_score",
    "score_label",
]