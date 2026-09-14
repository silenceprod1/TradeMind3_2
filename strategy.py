# strategy.py — TradeMind 4.2
#
# Логика:
#
# 1H context
#      ↓
# Major Liquidity
#      ↓
# 5M Major Liquidity Sweep
#      ↓
# 15M Confirmation
#      ↓
# 5M Trigger
#      ↓
# Entry
#      ↓
# SL за фактическим экстремумом sweep
#      ↓
# TP = строго 2R
#
# ВАЖНО:
# - Только major liquidity
# - LONG / SHORT симметрично
# - Sweep обязателен
# - 15M confirmation обязателен
# - 5M trigger обязателен
# - Один TP
# - RR = 1:2
# - opposing liquidity < 1.5R = NO TRADE


MIN_SCORE = 80

REQUIRED_RR = 2.0

MIN_LIQUIDITY_CLEARANCE_R = 1.5

MAX_SWEEP_AGE_CANDLES = 12

MAX_DISTANCE_FROM_SWEEP_PCT = 0.015

MIN_RISK_PCT = 0.001

SL_BUFFER_PCT = 0.0005
SL_BUFFER_ABS = 0.05


# ============================================================
# HELPERS
# ============================================================

def _price(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _candle_high(candle):
    if isinstance(candle, dict):
        return _price(
            candle.get("high")
            if candle.get("high") is not None
            else candle.get("h")
        )

    try:
        return float(candle[2])
    except Exception:
        return None


def _candle_low(candle):
    if isinstance(candle, dict):
        return _price(
            candle.get("low")
            if candle.get("low") is not None
            else candle.get("l")
        )

    try:
        return float(candle[3])
    except Exception:
        return None


def _candle_close(candle):
    if isinstance(candle, dict):
        return _price(
            candle.get("close")
            if candle.get("close") is not None
            else candle.get("c")
        )

    try:
        return float(candle[4])
    except Exception:
        return None


def _candle_open(candle):
    if isinstance(candle, dict):
        return _price(
            candle.get("open")
            if candle.get("open") is not None
            else candle.get("o")
        )

    try:
        return float(candle[1])
    except Exception:
        return None


def _candle_time(candle):
    if isinstance(candle, dict):
        return candle.get("time") or candle.get("timestamp")

    try:
        return candle[0]
    except Exception:
        return None


def _normalize_direction(direction):
    if direction is None:
        return None

    direction = str(direction).upper()

    if direction in ("BUY", "BULLISH"):
        return "LONG"

    if direction in ("SELL", "BEARISH"):
        return "SHORT"

    if direction in ("LONG", "SHORT"):
        return direction

    return None


def _get_sweep_level(sweep):
    if not sweep:
        return None

    return _price(
        sweep.get("level")
        or sweep.get("price")
        or sweep.get("sweep_level")
    )


def _get_sweep_direction(sweep):
    if not sweep:
        return None

    return _normalize_direction(
        sweep.get("direction")
        or sweep.get("side")
        or sweep.get("signal")
    )


# ============================================================
# 1H CONTEXT
# ============================================================

def get_context(candles_1h):

    if not candles_1h or len(candles_1h) < 5:
        return "NEUTRAL"

    closes = []

    for candle in candles_1h[-10:]:

        close = _candle_close(candle)

        if close is not None:
            closes.append(close)

    if len(closes) < 5:
        return "NEUTRAL"

    recent = closes[-3:]
    previous = closes[:-3]

    if not previous:
        return "NEUTRAL"

    recent_avg = sum(recent) / len(recent)
    previous_avg = sum(previous) / len(previous)

    if recent_avg > previous_avg:
        return "LONG"

    if recent_avg < previous_avg:
        return "SHORT"

    return "NEUTRAL"


# ============================================================
# SWEEP FRESHNESS
# ============================================================

def sweep_is_fresh(sweep, candles_5m):

    if not sweep:
        return False

    if not candles_5m:
        return False

    sweep_time = (
        sweep.get("sweep_time")
        or sweep.get("time")
        or sweep.get("timestamp")
    )

    if sweep_time is None:
        return True

    try:
        sweep_time = int(float(sweep_time))
    except Exception:
        return True

    latest_time = _candle_time(candles_5m[-1])

    if latest_time is None:
        return True

    try:
        latest_time = int(float(latest_time))
    except Exception:
        return True

    if sweep_time < 10_000_000_000:
        sweep_time *= 1000

    if latest_time < 10_000_000_000:
        latest_time *= 1000

    age_candles = (
        latest_time - sweep_time
    ) / 300000

    return age_candles <= MAX_SWEEP_AGE_CANDLES


# ============================================================
# SWEEP AGE
# ============================================================

def get_sweep_age(sweep, candles_5m):

    if not sweep or not candles_5m:
        return None

    sweep_time = (
        sweep.get("sweep_time")
        or sweep.get("time")
        or sweep.get("timestamp")
    )

    latest_time = _candle_time(candles_5m[-1])

    if sweep_time is None or latest_time is None:
        return None

    try:
        sweep_time = int(float(sweep_time))
        latest_time = int(float(latest_time))
    except Exception:
        return None

    if sweep_time < 10_000_000_000:
        sweep_time *= 1000

    if latest_time < 10_000_000_000:
        latest_time *= 1000

    age = (
        latest_time - sweep_time
    ) / 300000

    return max(0, age)


# ============================================================
# 15M CONFIRMATION
# ============================================================

def confirm_15m(candles_15m, direction, sweep):

    if not candles_15m or len(candles_15m) < 3:
        return False

    direction = _normalize_direction(direction)

    if direction not in ("LONG", "SHORT"):
        return False

    level = _get_sweep_level(sweep)

    recent = candles_15m[-3:]

    data = []

    for candle in recent:

        o = _candle_open(candle)
        h = _candle_high(candle)
        l = _candle_low(candle)
        c = _candle_close(candle)

        if None in (o, h, l, c):
            continue

        data.append({
            "open": o,
            "high": h,
            "low": l,
            "close": c,
        })

    if len(data) < 2:
        return False

    last = data[-1]
    prev = data[-2]

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        bullish = last["close"] > last["open"]

        higher_close = (
            last["close"] > prev["close"]
        )

        if level is not None:

            reclaimed = (
                last["close"] > level
            )

            # Последняя свеча должна вернуть цену
            # выше sweep level.
            if not reclaimed:
                return False

            return bullish or higher_close

        return bullish or higher_close

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        bearish = last["close"] < last["open"]

        lower_close = (
            last["close"] < prev["close"]
        )

        if level is not None:

            rejected = (
                last["close"] < level
            )

            if not rejected:
                return False

            return bearish or lower_close

        return bearish or lower_close

    return False


# ============================================================
# 5M TRIGGER
# ============================================================

def check_5m_trigger(candles_5m, direction, sweep):

    if not candles_5m or len(candles_5m) < 3:
        return False

    direction = _normalize_direction(direction)

    if direction not in ("LONG", "SHORT"):
        return False

    last = candles_5m[-1]
    prev = candles_5m[-2]

    last_open = _candle_open(last)
    last_high = _candle_high(last)
    last_low = _candle_low(last)
    last_close = _candle_close(last)

    prev_high = _candle_high(prev)
    prev_low = _candle_low(prev)
    prev_close = _candle_close(prev)

    if None in (
        last_open,
        last_high,
        last_low,
        last_close,
        prev_high,
        prev_low,
        prev_close,
    ):
        return False

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        bullish = last_close > last_open

        momentum = last_close > prev_close

        break_prev_high = (
            last_close > prev_high
        )

        # Для LONG нужен bullish trigger
        # и подтверждение движения вверх.
        return (
            bullish
            and momentum
            and break_prev_high
        )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        bearish = last_close < last_open

        momentum = last_close < prev_close

        break_prev_low = (
            last_close < prev_low
        )

        return (
            bearish
            and momentum
            and break_prev_low
        )

    return False


# ============================================================
# ORDER FLOW
# ============================================================

def check_order_flow(order_flow, direction):

    if not order_flow:
        return None

    try:
        value = float(order_flow)
    except Exception:
        return None

    direction = _normalize_direction(direction)

    if direction == "LONG":

        if value > 0:
            return True

        if value < 0:
            return False

    if direction == "SHORT":

        if value < 0:
            return True

        if value > 0:
            return False

    return None


# ============================================================
# DISTANCE FROM SWEEP
# ============================================================

def distance_from_sweep_ok(price, sweep, direction):

    if not sweep:
        return False

    level = _get_sweep_level(sweep)

    price = _price(price)

    if level is None or price is None:
        return True

    if level == 0:
        return True

    distance = abs(price - level) / level

    return distance <= MAX_DISTANCE_FROM_SWEEP_PCT


# ============================================================
# SWEEP EXTREME
# ============================================================

def get_sweep_extreme(
    sweep,
    candles_5m,
    direction
):

    if not sweep:
        return None

    direction = _normalize_direction(direction)

    # --------------------------------------------------------
    # 1. Explicit extreme
    # --------------------------------------------------------

    candidates = [
        sweep.get("sweep_extreme"),
        sweep.get("extreme"),
    ]

    if direction == "LONG":

        candidates.extend([
            sweep.get("sweep_low"),
            sweep.get("low"),
        ])

    if direction == "SHORT":

        candidates.extend([
            sweep.get("sweep_high"),
            sweep.get("high"),
        ])

    for value in candidates:

        value = _price(value)

        if value is not None:
            return value

    # --------------------------------------------------------
    # 2. Sweep candle by timestamp
    # --------------------------------------------------------

    sweep_time = (
        sweep.get("sweep_time")
        or sweep.get("time")
        or sweep.get("timestamp")
    )

    if sweep_time is not None and candles_5m:

        try:
            sweep_time = int(float(sweep_time))
        except Exception:
            sweep_time = None

        if sweep_time is not None:

            if sweep_time < 10_000_000_000:
                sweep_time *= 1000

            best = None
            best_distance = None

            for candle in candles_5m:

                candle_time = _candle_time(candle)

                if candle_time is None:
                    continue

                try:
                    candle_time = int(float(candle_time))
                except Exception:
                    continue

                if candle_time < 10_000_000_000:
                    candle_time *= 1000

                distance = abs(
                    candle_time - sweep_time
                )

                if (
                    best_distance is None
                    or distance < best_distance
                ):
                    best_distance = distance
                    best = candle

            if best is not None:

                if direction == "LONG":
                    return _candle_low(best)

                if direction == "SHORT":
                    return _candle_high(best)

    # --------------------------------------------------------
    # 3. Find candle crossing liquidity
    # --------------------------------------------------------

    level = _get_sweep_level(sweep)

    if level is not None and candles_5m:

        for candle in reversed(
            candles_5m[-MAX_SWEEP_AGE_CANDLES:]
        ):

            high = _candle_high(candle)
            low = _candle_low(candle)

            if high is None or low is None:
                continue

            if direction == "LONG":

                if low < level:
                    return low

            if direction == "SHORT":

                if high > level:
                    return high

    return level


# ============================================================
# STOP LOSS
# ============================================================

def calculate_sl(
    entry,
    sweep_extreme,
    direction
):

    entry = _price(entry)
    sweep_extreme = _price(sweep_extreme)

    if entry is None or sweep_extreme is None:
        return None

    direction = _normalize_direction(direction)

    if direction == "LONG":

        buffer = max(
            sweep_extreme * SL_BUFFER_PCT,
            SL_BUFFER_ABS,
        )

        sl = sweep_extreme - buffer

        if sl >= entry:
            return None

        return sl

    if direction == "SHORT":

        buffer = max(
            sweep_extreme * SL_BUFFER_PCT,
            SL_BUFFER_ABS,
        )

        sl = sweep_extreme + buffer

        if sl <= entry:
            return None

        return sl

    return None


# ============================================================
# OPPOSING LIQUIDITY
# ============================================================

def find_nearest_opposite_liquidity(
    major_levels,
    entry,
    direction
):

    entry = _price(entry)

    if entry is None:
        return None

    direction = _normalize_direction(direction)

    candidates = []

    for level in major_levels or []:

        if isinstance(level, dict):

            value = (
                level.get("price")
                or level.get("level")
                or level.get("value")
            )

            side = (
                level.get("side")
                or level.get("type")
                or level.get("direction")
            )

        else:

            value = level
            side = None

        value = _price(value)

        if value is None:
            continue

        side_text = (
            str(side).upper()
            if side is not None
            else ""
        )

        if direction == "LONG":

            if value <= entry:
                continue

            if side_text in (
                "LOW",
                "L",
                "LONG",
            ):
                continue

            candidates.append(value)

        elif direction == "SHORT":

            if value >= entry:
                continue

            if side_text in (
                "HIGH",
                "H",
                "SHORT",
            ):
                continue

            candidates.append(value)

    if not candidates:
        return None

    if direction == "LONG":
        return min(candidates)

    if direction == "SHORT":
        return max(candidates)

    return None


# ============================================================
# TAKE PROFIT
# ============================================================

def calculate_tp(
    entry,
    sl,
    direction,
    major_levels=None
):

    entry = _price(entry)
    sl = _price(sl)

    direction = _normalize_direction(direction)

    if entry is None or sl is None:

        return {
            "valid": False,
            "tp": None,
            "rr": None,
            "reason": "invalid_entry_or_sl",
            "opposing_liquidity": None,
            "liquidity_r": None,
        }

    risk = abs(entry - sl)

    if risk <= 0:

        return {
            "valid": False,
            "tp": None,
            "rr": None,
            "reason": "zero_risk",
            "opposing_liquidity": None,
            "liquidity_r": None,
        }

    risk_pct = risk / entry

    if risk_pct < MIN_RISK_PCT:

        return {
            "valid": False,
            "tp": None,
            "rr": None,
            "reason": "risk_too_small",
            "opposing_liquidity": None,
            "liquidity_r": None,
        }

    # --------------------------------------------------------
    # STRICT 2R
    # --------------------------------------------------------

    if direction == "LONG":

        tp = entry + (
            risk * REQUIRED_RR
        )

    elif direction == "SHORT":

        tp = entry - (
            risk * REQUIRED_RR
        )

    else:

        return {
            "valid": False,
            "tp": None,
            "rr": None,
            "reason": "invalid_direction",
            "opposing_liquidity": None,
            "liquidity_r": None,
        }

    # --------------------------------------------------------
    # Opposing major liquidity
    # --------------------------------------------------------

    opposing = find_nearest_opposite_liquidity(
        major_levels,
        entry,
        direction,
    )

    liquidity_r = None

    if opposing is not None:

        liquidity_distance = abs(
            opposing - entry
        )

        liquidity_r = (
            liquidity_distance / risk
        )

        # <1.5R = block
        if liquidity_r < MIN_LIQUIDITY_CLEARANCE_R:

            return {
                "valid": False,
                "tp": tp,
                "rr": REQUIRED_RR,
                "reason": (
                    "opposing_liquidity_too_close"
                ),
                "opposing_liquidity": opposing,
                "liquidity_r": liquidity_r,
            }

        # 1.5R–2R = valid, warning
        if liquidity_r < REQUIRED_RR:

            return {
                "valid": True,
                "tp": tp,
                "rr": REQUIRED_RR,
                "reason": (
                    f"2R target; opposing "
                    f"liquidity at "
                    f"{liquidity_r:.2f}R"
                ),
                "opposing_liquidity": opposing,
                "liquidity_r": liquidity_r,
            }

    return {
        "valid": True,
        "tp": tp,
        "rr": REQUIRED_RR,
        "reason": "2R target",
        "opposing_liquidity": opposing,
        "liquidity_r": liquidity_r,
    }


# ============================================================
# SCORE — SWEEP
# ============================================================

def get_sweep_bonus(sweep):

    if not sweep:
        return 0

    quality = sweep.get("quality")

    try:
        quality = float(quality)
    except Exception:
        return 0

    if quality >= 80:
        return 10

    if quality >= 60:
        return 7

    if quality >= 40:
        return 4

    return 0


# ============================================================
# STRUCTURE BONUS
# ============================================================

def get_structure_bonus(
    candles_15m,
    direction
):

    try:

        from market import detect_structure

        structure = detect_structure(
            candles_15m,
            direction,
        )

    except Exception:

        return 0, None

    if not structure:
        return 0, None

    if isinstance(structure, dict):

        structure_type = (
            structure.get("type")
            or structure.get("structure")
            or structure.get("signal")
        )

    else:

        structure_type = structure

    if structure_type is None:
        return 0, None

    text = str(
        structure_type
    ).upper()

    if "CHOCH" in text:
        return 10, structure_type

    if "BOS" in text:
        return 7, structure_type

    return 0, structure_type


# ============================================================
# DYNAMIC SCORE
# ============================================================

def calculate_base_score(
    has_major_liquidity,
    sweep,
    confirmed_15m,
    trigger_5m,
    context_1h,
    direction,
    sweep_bonus,
    structure_bonus,
    order_flow_result,
):

    score = 0

    # Major liquidity exists
    if has_major_liquidity:
        score += 20

    # Major sweep
    if sweep:
        score += 20

    # Sweep quality
    score += sweep_bonus

    # 15M confirmation
    if confirmed_15m:
        score += 20

    # 5M trigger
    if trigger_5m:
        score += 15

    # 1H alignment
    if context_1h == direction:
        score += 5

    # Structure
    score += structure_bonus

    # Order flow
    if order_flow_result is True:
        score += 5

    elif order_flow_result is False:
        score -= 5

    return max(
        0,
        min(100, int(score)),
    )


# ============================================================
# MAIN ANALYSIS
# ============================================================

def analyze(
    price,
    candles_1h,
    candles_15m,
    candles_5m,
    major_levels,
    sweep,
    order_flow=None
):

    price = _price(price)

    # --------------------------------------------------------
    # BASIC
    # --------------------------------------------------------

    if price is None:

        return {
            "status": "WAIT",
            "stage": "NO_DATA",
            "score": 0,
            "direction": None,
            "reason": "invalid_price",
        }

    if (
        not candles_1h
        or not candles_15m
        or not candles_5m
    ):

        return {
            "status": "WAIT",
            "stage": "NO_DATA",
            "score": 0,
            "direction": None,
            "reason": "missing_candles",
        }

    # --------------------------------------------------------
    # MAJOR LIQUIDITY
    # --------------------------------------------------------

    if not major_levels:

        return {
            "status": "WAIT",
            "stage": "WAIT_SWEEP",
            "score": 0,
            "direction": None,
            "reason": "no_major_liquidity",
        }

    # --------------------------------------------------------
    # 1H CONTEXT
    # --------------------------------------------------------

    context_1h = get_context(
        candles_1h
    )

    # --------------------------------------------------------
    # NO SWEEP
    # --------------------------------------------------------

    if not sweep:

        return {
            "status": "WAIT",
            "stage": "WAIT_SWEEP",
            "score": 20,
            "direction": None,
            "context_1h": context_1h,
            "reason": "no_major_liquidity_sweep",
            "major_levels": major_levels,
        }

    # --------------------------------------------------------
    # DIRECTION
    # --------------------------------------------------------

    direction = _get_sweep_direction(
        sweep
    )

    if direction is None:

        return {
            "status": "WAIT",
            "stage": "WAIT_SWEEP",
            "score": 20,
            "direction": None,
            "context_1h": context_1h,
            "reason": "sweep_direction_missing",
            "sweep": sweep,
        }

    # --------------------------------------------------------
    # SWEEP FRESHNESS
    # --------------------------------------------------------

    sweep_age = get_sweep_age(
        sweep,
        candles_5m,
    )

    if not sweep_is_fresh(
        sweep,
        candles_5m,
    ):

        return {
            "status": "WAIT",
            "stage": "WAIT_SWEEP",
            "score": 30,
            "direction": direction,
            "context_1h": context_1h,
            "reason": "sweep_too_old",
            "sweep_age": sweep_age,
            "sweep": sweep,
        }

    # --------------------------------------------------------
    # DISTANCE
    # --------------------------------------------------------

    if not distance_from_sweep_ok(
        price,
        sweep,
        direction,
    ):

        return {
            "status": "WAIT",
            "stage": "WAIT_SWEEP",
            "score": 35,
            "direction": direction,
            "context_1h": context_1h,
            "reason": "price_too_far_from_sweep",
            "sweep_age": sweep_age,
            "sweep": sweep,
        }

    # --------------------------------------------------------
    # SWEEP SCORE
    # --------------------------------------------------------

    sweep_bonus = get_sweep_bonus(
        sweep
    )

    # --------------------------------------------------------
    # 15M CONFIRMATION
    # --------------------------------------------------------

    confirmed_15m = confirm_15m(
        candles_15m,
        direction,
        sweep,
    )

    # --------------------------------------------------------
    # STRUCTURE
    # --------------------------------------------------------

    structure_bonus, structure = (
        get_structure_bonus(
            candles_15m,
            direction,
        )
    )

    # --------------------------------------------------------
    # ORDER FLOW
    # --------------------------------------------------------

    order_flow_result = (
        check_order_flow(
            order_flow,
            direction,
        )
    )

    # --------------------------------------------------------
    # NO 15M
    # --------------------------------------------------------

    if not confirmed_15m:

        score = calculate_base_score(
            has_major_liquidity=True,
            sweep=sweep,
            confirmed_15m=False,
            trigger_5m=False,
            context_1h=context_1h,
            direction=direction,
            sweep_bonus=sweep_bonus,
            structure_bonus=0,
            order_flow_result=None,
        )

        return {
            "status": "WAIT",
            "stage": "SWEPT",
            "score": score,
            "direction": direction,
            "context_1h": context_1h,
            "structure": structure,
            "sweep": sweep,
            "sweep_age": sweep_age,
            "reason": "no_15m_confirmation",
        }

    # --------------------------------------------------------
    # 15M CONFIRMED
    # --------------------------------------------------------

    trigger_5m = check_5m_trigger(
        candles_5m,
        direction,
        sweep,
    )

    if not trigger_5m:

        score = calculate_base_score(
            has_major_liquidity=True,
            sweep=sweep,
            confirmed_15m=True,
            trigger_5m=False,
            context_1h=context_1h,
            direction=direction,
            sweep_bonus=sweep_bonus,
            structure_bonus=structure_bonus,
            order_flow_result=order_flow_result,
        )

        return {
            "status": "WAIT",
            "stage": "WAIT_5M_TRIGGER",
            "score": score,
            "direction": direction,
            "context_1h": context_1h,
            "structure": structure,
            "sweep": sweep,
            "sweep_age": sweep_age,
            "reason": "no_5m_trigger",
        }

    # --------------------------------------------------------
    # FULL SCORE
    # --------------------------------------------------------

    score = calculate_base_score(
        has_major_liquidity=True,
        sweep=sweep,
        confirmed_15m=True,
        trigger_5m=True,
        context_1h=context_1h,
        direction=direction,
        sweep_bonus=sweep_bonus,
        structure_bonus=structure_bonus,
        order_flow_result=order_flow_result,
    )

    # --------------------------------------------------------
    # SWEEP EXTREME
    # --------------------------------------------------------

    sweep_extreme = get_sweep_extreme(
        sweep,
        candles_5m,
        direction,
    )

    if sweep_extreme is None:

        return {
            "status": "WAIT",
            "stage": "WAIT_5M_TRIGGER",
            "score": score,
            "direction": direction,
            "context_1h": context_1h,
            "structure": structure,
            "reason": "cannot_determine_sweep_extreme",
            "sweep": sweep,
        }

    # --------------------------------------------------------
    # ENTRY
    # --------------------------------------------------------

    entry = price

    # --------------------------------------------------------
    # SL
    # --------------------------------------------------------

    sl = calculate_sl(
        entry,
        sweep_extreme,
        direction,
    )

    if sl is None:

        return {
            "status": "WAIT",
            "stage": "WAIT_5M_TRIGGER",
            "score": score,
            "direction": direction,
            "context_1h": context_1h,
            "structure": structure,
            "reason": "invalid_stop_loss",
            "sweep_extreme": sweep_extreme,
            "sweep": sweep,
        }

    risk = abs(
        entry - sl
    )

    if risk <= 0:

        return {
            "status": "WAIT",
            "stage": "WAIT_5M_TRIGGER",
            "score": score,
            "direction": direction,
            "reason": "zero_risk",
        }

    # --------------------------------------------------------
    # TP 2R + LIQUIDITY FILTER
    # --------------------------------------------------------

    tp_result = calculate_tp(
        entry,
        sl,
        direction,
        major_levels,
    )

    if not tp_result["valid"]:

        return {
            "status": "WAIT",
            "stage": "LIQUIDITY_FILTER",
            "score": score,
            "direction": direction,
            "context_1h": context_1h,
            "structure": structure,

            "entry": entry,
            "sl": sl,
            "tp": tp_result.get("tp"),
            "rr": tp_result.get("rr"),

            "reason": tp_result.get(
                "reason"
            ),

            "opposing_liquidity": (
                tp_result.get(
                    "opposing_liquidity"
                )
            ),

            "liquidity_r": (
                tp_result.get(
                    "liquidity_r"
                )
            ),

            "sweep": sweep,
            "sweep_extreme": sweep_extreme,
        }

    # --------------------------------------------------------
    # FINAL SCORE
    # --------------------------------------------------------

    if score < MIN_SCORE:

        return {
            "status": "WAIT",
            "stage": "LOW_SCORE",
            "score": score,
            "direction": direction,

            "context_1h": context_1h,
            "structure": structure,

            "entry": entry,
            "sl": sl,
            "tp": tp_result.get("tp"),
            "rr": tp_result.get("rr"),

            "reason": "score_below_minimum",

            "opposing_liquidity": (
                tp_result.get(
                    "opposing_liquidity"
                )
            ),

            "liquidity_r": (
                tp_result.get(
                    "liquidity_r"
                )
            ),

            "sweep": sweep,
            "sweep_extreme": sweep_extreme,
        }

    # --------------------------------------------------------
    # READY
    # --------------------------------------------------------

    liquidity_warning = None

    liquidity_r = tp_result.get(
        "liquidity_r"
    )

    if (
        liquidity_r is not None
        and liquidity_r < REQUIRED_RR
    ):

        liquidity_warning = (
            f"Opposing major liquidity "
            f"at {liquidity_r:.2f}R"
        )

    return {
        "status": "READY",
        "stage": "READY",

        "score": score,
        "direction": direction,

        # Context
        "context_1h": context_1h,
        "structure": structure,

        # Trade
        "entry": entry,
        "sl": sl,
        "tp": tp_result.get("tp"),
        "rr": REQUIRED_RR,

        # Sweep
        "sweep": sweep,
        "sweep_extreme": sweep_extreme,
        "sweep_age": sweep_age,

        # Liquidity
        "opposing_liquidity": (
            tp_result.get(
                "opposing_liquidity"
            )
        ),

        "liquidity_r": liquidity_r,

        "liquidity_warning": (
            liquidity_warning
        ),

        # TP
        "tp_reason": tp_result.get(
            "reason"
        ),

        # Order flow
        "order_flow": order_flow_result,

        # Trigger
        "trigger_5m": True,
        "confirmation_15m": True,

        # Risk
        "risk_distance": risk,
        "risk_pct": risk / entry,

        # Human-readable reason
        "reason": (
            "TradeMind 4.2 READY: "
            "major sweep → "
            "15M confirmation → "
            "5M trigger → "
            "TP 2R"
        ),
    }