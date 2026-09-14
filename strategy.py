# strategy.py — TradeMind 4.4.2
#
# LOGIC:
#
# 1H context
#      ↓
# Major Liquidity
#      ↓
# Fresh 5M Major Liquidity Sweep
#      ↓
# 15M Confirmation AFTER Sweep
#      ↓
# 5M Trigger AFTER 15M Confirmation
#      ↓
# Trigger MUST remain close to Sweep
#      ↓
# Entry MUST remain close to Sweep
#      ↓
# SL behind actual Sweep extreme
#      ↓
# TP STRICTLY 2R
#
# ANTI-CHASE:
# - Sweep max 6 x 5M candles old
# - Entry max 0.75% from Sweep
# - Trigger max 0.75% from Sweep
# - Trigger max 3 x 5M candles after 15M confirmation
# - Old Sweep = NO TRADE
# - Late Trigger = NO TRADE
#
# LIQUIDITY:
# < 1.50R = NO TRADE
# 1.50–1.80R = -10
# 1.80–2.00R = -5
# >= 2.00R = no penalty
#
# TP ALWAYS 2R.
# ONE TP ONLY.
# LONG / SHORT symmetric.


MIN_SCORE = 80
REQUIRED_RR = 2.0


# ============================================================
# LIQUIDITY
# ============================================================

MIN_LIQUIDITY_CLEARANCE_R = 1.5
LIQUIDITY_WARNING_R = 1.8

LIQUIDITY_PENALTY_CLOSE = 10
LIQUIDITY_PENALTY_WARNING = 5


# ============================================================
# ANTI-CHASE
# ============================================================

# Maximum age of Sweep.
# 6 x 5M = approximately 30 minutes.
MAX_SWEEP_AGE_CANDLES = 6

# Much tighter than previous 1.5%.
# Price must remain genuinely close to the Sweep.
MAX_DISTANCE_FROM_SWEEP_PCT = 0.0075

# Maximum number of 5M candles allowed
# after 15M confirmation for a trigger.
MAX_TRIGGER_AGE_AFTER_CONFIRMATION = 3

# Minimum risk.
MIN_RISK_PCT = 0.001

# SL buffer.
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


def _candle_time(candle):

    if isinstance(candle, dict):

        return (
            candle.get("time")
            or candle.get("timestamp")
            or candle.get("open_time")
            or candle.get("openTime")
        )

    try:
        return candle[0]

    except Exception:
        return None


def _timestamp_ms(value):

    if value is None:
        return None

    try:
        value = int(float(value))

    except Exception:
        return None

    if value < 10_000_000_000:
        value *= 1000

    return value


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
        or sweep.get("sweep_level")
        or sweep.get("price")
    )


def _get_sweep_direction(sweep):

    if not sweep:
        return None

    return _normalize_direction(
        sweep.get("direction")
        or sweep.get("side")
        or sweep.get("signal")
    )


def _get_sweep_time(sweep):

    if not sweep:
        return None

    return _timestamp_ms(
        sweep.get("sweep_time")
        or sweep.get("time")
        or sweep.get("timestamp")
        or sweep.get("open_time")
        or sweep.get("openTime")
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

def get_sweep_age(sweep, candles_5m):

    if not sweep or not candles_5m:
        return None

    sweep_time = _get_sweep_time(sweep)

    latest_time = _timestamp_ms(
        _candle_time(candles_5m[-1])
    )

    if sweep_time is None or latest_time is None:
        return None

    age = (
        latest_time - sweep_time
    ) / 300000

    return max(0, age)


def sweep_is_fresh(sweep, candles_5m):

    if not sweep or not candles_5m:
        return False

    sweep_time = _get_sweep_time(sweep)

    latest_time = _timestamp_ms(
        _candle_time(candles_5m[-1])
    )

    if sweep_time is None or latest_time is None:
        return False

    age_candles = (
        latest_time - sweep_time
    ) / 300000

    if age_candles < 0:
        return False

    return age_candles <= MAX_SWEEP_AGE_CANDLES


# ============================================================
# DISTANCE FROM SWEEP
# ============================================================

def get_distance_from_sweep_pct(
    price,
    sweep
):

    level = _get_sweep_level(sweep)
    price = _price(price)

    if level is None or price is None:
        return None

    if level == 0:
        return None

    return (
        abs(price - level)
        / level
        * 100
    )


def distance_from_sweep_ok(
    price,
    sweep,
    direction=None
):

    distance = get_distance_from_sweep_pct(
        price,
        sweep
    )

    if distance is None:
        return False

    return (
        distance
        <= MAX_DISTANCE_FROM_SWEEP_PCT * 100
    )


# ============================================================
# 15M CONFIRMATION
# ============================================================

def get_15m_confirmation_time(
    candles_15m,
    direction,
    sweep
):

    if not candles_15m or len(candles_15m) < 2:
        return None

    direction = _normalize_direction(direction)

    if direction not in ("LONG", "SHORT"):
        return None

    sweep_time = _get_sweep_time(sweep)
    level = _get_sweep_level(sweep)

    if sweep_time is None or level is None:
        return None

    candidates = []

    for candle in candles_15m[-8:]:

        candle_time = _timestamp_ms(
            _candle_time(candle)
        )

        o = _candle_open(candle)
        h = _candle_high(candle)
        l = _candle_low(candle)
        c = _candle_close(candle)

        if None in (
            candle_time,
            o,
            h,
            l,
            c
        ):
            continue

        # Confirmation MUST happen after Sweep.
        if candle_time <= sweep_time:
            continue

        candidates.append({
            "time": candle_time,
            "open": o,
            "high": h,
            "low": l,
            "close": c
        })

    if not candidates:
        return None

    # First valid confirmation after Sweep.
    for candle in candidates:

        # ====================================================
        # LONG
        # ====================================================

        if direction == "LONG":

            reclaim = (
                candle["close"] > level
            )

            bullish = (
                candle["close"] > candle["open"]
            )

            if reclaim and bullish:
                return candle["time"]

        # ====================================================
        # SHORT
        # ====================================================

        elif direction == "SHORT":

            rejection = (
                candle["close"] < level
            )

            bearish = (
                candle["close"] < candle["open"]
            )

            if rejection and bearish:
                return candle["time"]

    return None


def confirm_15m(
    candles_15m,
    direction,
    sweep
):

    return (
        get_15m_confirmation_time(
            candles_15m,
            direction,
            sweep
        )
        is not None
    )


# ============================================================
# 5M TRIGGER
# ============================================================

def get_5m_trigger_time(
    candles_5m,
    direction,
    sweep,
    confirmation_time=None
):

    if not candles_5m or len(candles_5m) < 3:
        return None

    direction = _normalize_direction(direction)

    if direction not in ("LONG", "SHORT"):
        return None

    sweep_time = _get_sweep_time(sweep)
    sweep_level = _get_sweep_level(sweep)

    if sweep_time is None:
        return None

    if sweep_level is None:
        return None

    if confirmation_time is None:
        return None

    candidates = []

    for candle in candles_5m:

        candle_time = _timestamp_ms(
            _candle_time(candle)
        )

        o = _candle_open(candle)
        h = _candle_high(candle)
        l = _candle_low(candle)
        c = _candle_close(candle)

        if None in (
            candle_time,
            o,
            h,
            l,
            c
        ):
            continue

        # Must happen after Sweep.
        if candle_time <= sweep_time:
            continue

        # Must happen after confirmation.
        if candle_time <= confirmation_time:
            continue

        # Trigger MUST remain close to Sweep.
        if not distance_from_sweep_ok(
            c,
            sweep,
            direction
        ):
            continue

        candidates.append({
            "time": candle_time,
            "open": o,
            "high": h,
            "low": l,
            "close": c
        })

    # No late trigger.
    if len(candidates) == 0:
        return None

    if len(candidates) > MAX_TRIGGER_AGE_AFTER_CONFIRMATION:
        candidates = candidates[
            :MAX_TRIGGER_AGE_AFTER_CONFIRMATION
        ]

    if not candidates:
        return None

    # We only accept the FIRST valid trigger window.
    # This prevents chasing a move many candles later.
    for index in range(len(candidates)):

        last = candidates[index]

        if index == 0:
            continue

        prev = candidates[index - 1]

        # ====================================================
        # LONG
        # ====================================================

        if direction == "LONG":

            bullish = (
                last["close"] > last["open"]
            )

            momentum = (
                last["close"] > prev["close"]
            )

            break_prev_high = (
                last["close"] > prev["high"]
            )

            reclaim = (
                last["close"] > sweep_level
            )

            if (
                bullish
                and momentum
                and break_prev_high
                and reclaim
            ):
                return last["time"]

        # ====================================================
        # SHORT
        # ====================================================

        elif direction == "SHORT":

            bearish = (
                last["close"] < last["open"]
            )

            momentum = (
                last["close"] < prev["close"]
            )

            break_prev_low = (
                last["close"] < prev["low"]
            )

            rejection = (
                last["close"] < sweep_level
            )

            if (
                bearish
                and momentum
                and break_prev_low
                and rejection
            ):
                return last["time"]

    return None


def check_5m_trigger(
    candles_5m,
    direction,
    sweep,
    confirmation_time=None
):

    return (
        get_5m_trigger_time(
            candles_5m,
            direction,
            sweep,
            confirmation_time
        )
        is not None
    )


# ============================================================
# ORDER FLOW
# ============================================================

def check_order_flow(
    order_flow,
    direction
):

    if order_flow is None:
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

    elif direction == "SHORT":

        if value < 0:
            return True

        if value > 0:
            return False

    return None


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

    # ========================================================
    # 1. Explicit extreme from market.py
    # ========================================================

    if direction == "LONG":

        candidates = [
            sweep.get("sweep_extreme"),
            sweep.get("extreme"),
            sweep.get("sweep_low"),
            sweep.get("low")
        ]

    elif direction == "SHORT":

        candidates = [
            sweep.get("sweep_extreme"),
            sweep.get("extreme"),
            sweep.get("sweep_high"),
            sweep.get("high")
        ]

    else:
        return None

    for value in candidates:

        value = _price(value)

        if value is not None:
            return value

    # ========================================================
    # 2. Actual Sweep candle
    # ========================================================

    sweep_time = _get_sweep_time(sweep)

    if sweep_time is not None and candles_5m:

        best = None
        best_distance = None

        for candle in candles_5m:

            candle_time = _timestamp_ms(
                _candle_time(candle)
            )

            if candle_time is None:
                continue

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

    # ========================================================
    # 3. Crossing candle fallback
    # ========================================================

    level = _get_sweep_level(sweep)

    if level is not None and candles_5m:

        recent = candles_5m[
            -MAX_SWEEP_AGE_CANDLES:
        ]

        for candle in reversed(recent):

            high = _candle_high(candle)
            low = _candle_low(candle)

            if high is None or low is None:
                continue

            if direction == "LONG":

                if low < level:
                    return low

            elif direction == "SHORT":

                if high > level:
                    return high

    return None


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

    if (
        entry is None
        or sweep_extreme is None
    ):
        return None

    direction = _normalize_direction(direction)

    buffer = max(
        sweep_extreme * SL_BUFFER_PCT,
        SL_BUFFER_ABS
    )

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        sl = sweep_extreme - buffer

        if sl >= entry:
            return None

        return sl

    # ========================================================
    # SHORT
    # ========================================================

    if direction == "SHORT":

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
                "LONG"
            ):
                continue

            candidates.append(value)

        elif direction == "SHORT":

            if value >= entry:
                continue

            if side_text in (
                "HIGH",
                "H",
                "SHORT"
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
# LIQUIDITY PENALTY
# ============================================================

def get_liquidity_penalty(liquidity_r):

    if liquidity_r is None:
        return 0

    if liquidity_r < MIN_LIQUIDITY_CLEARANCE_R:
        return 0

    if liquidity_r < LIQUIDITY_WARNING_R:
        return LIQUIDITY_PENALTY_CLOSE

    if liquidity_r < REQUIRED_RR:
        return LIQUIDITY_PENALTY_WARNING

    return 0


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
            "liquidity_penalty": 0
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
            "liquidity_penalty": 0
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
            "liquidity_penalty": 0
        }

    # ========================================================
    # STRICT 2R
    # ========================================================

    if direction == "LONG":

        tp = entry + risk * REQUIRED_RR

    elif direction == "SHORT":

        tp = entry - risk * REQUIRED_RR

    else:

        return {
            "valid": False,
            "tp": None,
            "rr": None,
            "reason": "invalid_direction",
            "opposing_liquidity": None,
            "liquidity_r": None,
            "liquidity_penalty": 0
        }

    # ========================================================
    # OPPOSING MAJOR LIQUIDITY
    # ========================================================

    opposing = find_nearest_opposite_liquidity(
        major_levels,
        entry,
        direction
    )

    liquidity_r = None

    if opposing is not None:

        liquidity_distance = abs(
            opposing - entry
        )

        liquidity_r = (
            liquidity_distance / risk
        )

        # HARD BLOCK.
        if liquidity_r < MIN_LIQUIDITY_CLEARANCE_R:

            return {
                "valid": False,
                "tp": tp,
                "rr": REQUIRED_RR,
                "reason": "opposing_liquidity_too_close",
                "opposing_liquidity": opposing,
                "liquidity_r": liquidity_r,
                "liquidity_penalty": 0
            }

        # WARNING 1.50–1.80R.
        if liquidity_r < LIQUIDITY_WARNING_R:

            return {
                "valid": True,
                "tp": tp,
                "rr": REQUIRED_RR,
                "reason": (
                    "2R target; opposing major "
                    f"liquidity at {liquidity_r:.2f}R"
                ),
                "opposing_liquidity": opposing,
                "liquidity_r": liquidity_r,
                "liquidity_penalty": (
                    LIQUIDITY_PENALTY_CLOSE
                )
            }

        # WARNING 1.80–2.00R.
        if liquidity_r < REQUIRED_RR:

            return {
                "valid": True,
                "tp": tp,
                "rr": REQUIRED_RR,
                "reason": (
                    "2R target; opposing major "
                    f"liquidity at {liquidity_r:.2f}R"
                ),
                "opposing_liquidity": opposing,
                "liquidity_r": liquidity_r,
                "liquidity_penalty": (
                    LIQUIDITY_PENALTY_WARNING
                )
            }

    return {
        "valid": True,
        "tp": tp,
        "rr": REQUIRED_RR,
        "reason": "2R target",
        "opposing_liquidity": opposing,
        "liquidity_r": liquidity_r,
        "liquidity_penalty": 0
    }


# ============================================================
# SCORE
# ============================================================

def get_sweep_bonus(sweep):

    if not sweep:
        return 0

    try:
        quality = float(
            sweep.get("quality", 0)
        )

    except Exception:
        return 0

    if quality >= 80:
        return 10

    if quality >= 60:
        return 7

    if quality >= 40:
        return 4

    return 0


def get_structure_bonus(
    candles_15m,
    direction
):

    try:

        from market import detect_structure

        structure = detect_structure(
            candles_15m,
            direction
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


def calculate_base_score(
    has_major_liquidity,
    sweep,
    confirmed_15m,
    trigger_5m,
    context_1h,
    direction,
    sweep_bonus,
    structure_bonus,
    order_flow_result
):

    score = 0

    if has_major_liquidity:
        score += 20

    if sweep:
        score += 20

    score += sweep_bonus

    if confirmed_15m:
        score += 20

    if trigger_5m:
        score += 15

    if context_1h == direction:
        score += 5

    score += structure_bonus

    if order_flow_result is True:
        score += 5

    elif order_flow_result is False:
        score -= 5

    return max(
        0,
        min(
            100,
            int(score)
        )
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

    # ========================================================
    # BASIC
    # ========================================================

    if price is None:

        return {
            "status": "WAIT",
            "stage": "NO_DATA",
            "score": 0,
            "direction": None,
            "reason": "invalid_price"
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
            "reason": "missing_candles"
        }

    # ========================================================
    # MAJOR LIQUIDITY
    # ========================================================

    if not major_levels:

        return {
            "status": "WAIT",
            "stage": "WAIT_SWEEP",
            "score": 0,
            "direction": None,
            "reason": "no_major_liquidity"
        }

    # ========================================================
    # 1H CONTEXT
    # ========================================================

    context_1h = get_context(
        candles_1h
    )

    # ========================================================
    # NO SWEEP
    # ========================================================

    if not sweep:

        return {
            "status": "WAIT",
            "stage": "WAIT_SWEEP",
            "score": 20,
            "direction": None,
            "context_1h": context_1h,
            "reason": "no_major_liquidity_sweep",
            "major_levels": major_levels
        }

    # ========================================================
    # DIRECTION
    # ========================================================

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
            "sweep": sweep
        }

    # ========================================================
    # SWEEP FRESHNESS
    # ========================================================

    sweep_age = get_sweep_age(
        sweep,
        candles_5m
    )

    if not sweep_is_fresh(
        sweep,
        candles_5m
    ):

        return {
            "status": "WAIT",
            "stage": "WAIT_SWEEP",
            "score": 20,
            "direction": direction,
            "context_1h": context_1h,
            "reason": "sweep_too_old_or_time_missing",
            "sweep_age": sweep_age,
            "sweep": sweep
        }

    # ========================================================
    # ENTRY DISTANCE
    # ========================================================

    distance_pct = (
        get_distance_from_sweep_pct(
            price,
            sweep
        )
    )

    if not distance_from_sweep_ok(
        price,
        sweep,
        direction
    ):

        return {
            "status": "WAIT",
            "stage": "WAIT_SWEEP",
            "score": 20,
            "direction": direction,
            "context_1h": context_1h,
            "reason": "price_too_far_from_sweep",
            "sweep_age": sweep_age,
            "distance_from_sweep_pct": distance_pct,
            "max_distance_pct": (
                MAX_DISTANCE_FROM_SWEEP_PCT * 100
            ),
            "sweep": sweep
        }

    # ========================================================
    # SWEEP SCORE
    # ========================================================

    sweep_bonus = get_sweep_bonus(
        sweep
    )

    # ========================================================
    # 15M CONFIRMATION
    # ========================================================

    confirmation_15m_time = (
        get_15m_confirmation_time(
            candles_15m,
            direction,
            sweep
        )
    )

    confirmed_15m = (
        confirmation_15m_time is not None
    )

    # ========================================================
    # STRUCTURE
    # ========================================================

    structure_bonus, structure = (
        get_structure_bonus(
            candles_15m,
            direction
        )
    )

    # ========================================================
    # ORDER FLOW
    # ========================================================

    order_flow_result = (
        check_order_flow(
            order_flow,
            direction
        )
    )

    # ========================================================
    # NO 15M CONFIRMATION
    # ========================================================

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
            order_flow_result=None
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
            "distance_from_sweep_pct": distance_pct,
            "reason": (
                "no_15m_confirmation_after_sweep"
            )
        }

    # ========================================================
    # 5M TRIGGER
    # ========================================================

    trigger_5m_time = (
        get_5m_trigger_time(
            candles_5m,
            direction,
            sweep,
            confirmation_15m_time
        )
    )

    trigger_5m = (
        trigger_5m_time is not None
    )

    # ========================================================
    # NO 5M TRIGGER
    # ========================================================

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
            order_flow_result=order_flow_result
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
            "distance_from_sweep_pct": distance_pct,
            "confirmation_15m_time": (
                confirmation_15m_time
            ),
            "reason": (
                "no_5m_trigger_in_valid_window"
            )
        }

    # ========================================================
    # FIND TRIGGER CANDLE
    # ========================================================

    trigger_candle = None

    if trigger_5m_time is not None:

        for candle in candles_5m:

            candle_time = _timestamp_ms(
                _candle_time(candle)
            )

            if candle_time == trigger_5m_time:

                trigger_candle = candle
                break

    if trigger_candle is None:

        return {
            "status": "WAIT",
            "stage": "WAIT_5M_TRIGGER",
            "score": 20,
            "direction": direction,
            "reason": "trigger_candle_not_found",
            "sweep": sweep
        }

    # ========================================================
    # TRIGGER DISTANCE
    # ========================================================

    trigger_close = _candle_close(
        trigger_candle
    )

    trigger_distance_pct = (
        get_distance_from_sweep_pct(
            trigger_close,
            sweep
        )
    )

    if not distance_from_sweep_ok(
        trigger_close,
        sweep,
        direction
    ):

        return {
            "status": "WAIT",
            "stage": "WAIT_SWEEP",
            "score": 20,
            "direction": direction,
            "context_1h": context_1h,
            "reason": "trigger_too_far_from_sweep",
            "sweep": sweep,
            "sweep_age": sweep_age,
            "distance_from_sweep_pct": (
                trigger_distance_pct
            ),
            "max_distance_pct": (
                MAX_DISTANCE_FROM_SWEEP_PCT * 100
            )
        }

    # ========================================================
    # FULL SCORE
    # ========================================================

    score = calculate_base_score(
        has_major_liquidity=True,
        sweep=sweep,
        confirmed_15m=True,
        trigger_5m=True,
        context_1h=context_1h,
        direction=direction,
        sweep_bonus=sweep_bonus,
        structure_bonus=structure_bonus,
        order_flow_result=order_flow_result
    )

    # ========================================================
    # SWEEP EXTREME
    # ========================================================

    sweep_extreme = get_sweep_extreme(
        sweep,
        candles_5m,
        direction
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
            "sweep": sweep
        }

    # ========================================================
    # ENTRY
    #
    # IMPORTANT:
    # Entry is current price ONLY if current price
    # remains close to the original Sweep.
    # ========================================================

    entry = price

    if not distance_from_sweep_ok(
        entry,
        sweep,
        direction
    ):

        return {
            "status": "WAIT",
            "stage": "WAIT_SWEEP",
            "score": 20,
            "direction": direction,
            "context_1h": context_1h,
            "reason": "entry_chased_price",
            "sweep": sweep,
            "sweep_age": sweep_age,
            "distance_from_sweep_pct": (
                get_distance_from_sweep_pct(
                    entry,
                    sweep
                )
            ),
            "max_distance_pct": (
                MAX_DISTANCE_FROM_SWEEP_PCT * 100
            )
        }

    # ========================================================
    # SL
    # ========================================================

    sl = calculate_sl(
        entry,
        sweep_extreme,
        direction
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
            "sweep": sweep
        }

    # ========================================================
    # RISK
    # ========================================================

    risk = abs(
        entry - sl
    )

    if risk <= 0:

        return {
            "status": "WAIT",
            "stage": "WAIT_5M_TRIGGER",
            "score": score,
            "direction": direction,
            "reason": "zero_risk"
        }

    # ========================================================
    # TP — STRICT 2R
    # ========================================================

    tp_result = calculate_tp(
        entry,
        sl,
        direction,
        major_levels
    )

    # ========================================================
    # HARD LIQUIDITY BLOCK
    # ========================================================

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

            "reason": tp_result.get("reason"),

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

            "liquidity_penalty": 0,

            "sweep": sweep,
            "sweep_extreme": sweep_extreme,
            "sweep_age": sweep_age,

            "distance_from_sweep_pct": distance_pct,

            "confirmation_15m_time": (
                confirmation_15m_time
            ),

            "trigger_5m_time": (
                trigger_5m_time
            )
        }

    # ========================================================
    # LIQUIDITY PENALTY
    # ========================================================

    liquidity_penalty = tp_result.get(
        "liquidity_penalty",
        0
    )

    final_score = max(
        0,
        score - liquidity_penalty
    )

    # ========================================================
    # FINAL SCORE FILTER
    # ========================================================

    if final_score < MIN_SCORE:

        return {
            "status": "WAIT",
            "stage": "SCORE_FILTER",
            "score": final_score,
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

            "liquidity_penalty": (
                liquidity_penalty
            ),

            "sweep": sweep,
            "sweep_extreme": sweep_extreme,
            "sweep_age": sweep_age,

            "distance_from_sweep_pct": (
                distance_pct
            ),

            "trigger_distance_pct": (
                trigger_distance_pct
            ),

            "confirmation_15m_time": (
                confirmation_15m_time
            ),

            "trigger_5m_time": (
                trigger_5m_time
            )
        }

    # ========================================================
    # READY
    # ========================================================

    return {
        "status": "READY",
        "stage": "READY",

        "score": final_score,
        "direction": direction,

        "context_1h": context_1h,
        "structure": structure,

        # ENTRY / SL / TP
        "entry": entry,
        "sl": sl,
        "tp": tp_result.get("tp"),
        "rr": REQUIRED_RR,

        "risk": risk,

        "risk_pct": (
            risk / entry * 100
            if entry
            else None
        ),

        # SWEEP
        "sweep": sweep,
        "sweep_extreme": sweep_extreme,
        "sweep_age": sweep_age,

        # DISTANCE
        "distance_from_sweep_pct": distance_pct,

        "trigger_distance_pct": (
            trigger_distance_pct
        ),

        # CONFIRMATION
        "confirmation_15m_time": (
            confirmation_15m_time
        ),

        # TRIGGER
        "trigger_5m_time": (
            trigger_5m_time
        ),

        # LIQUIDITY
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

        "liquidity_penalty": (
            liquidity_penalty
        ),

        "reason": (
            "TradeMind 4.4.2 READY: "
            "fresh major sweep → "
            "15M confirmation → "
            "fast 5M trigger → "
            "trigger near sweep → "
            "entry near sweep → "
            "SL behind sweep extreme → "
            "strict 2R TP"
        )
    }