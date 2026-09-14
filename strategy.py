# strategy.py — TradeMind 4.1
#
# Логика:
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
# Фильтр противоположной major liquidity:
# < 1.5R  -> NO TRADE
# 1.5R–2R -> TRADE разрешён, но есть предупреждение
# >= 2R   -> обычный READY


MIN_SCORE = 80

# Наш обязательный RR
REQUIRED_RR = 2.0

# Если противоположная major liquidity ближе этого расстояния,
# сетап считается слишком опасным.
MIN_LIQUIDITY_CLEARANCE_R = 1.5

# Sweep должен быть достаточно свежим
MAX_SWEEP_AGE_CANDLES = 12

# Максимальное удаление текущей цены от уровня sweep.
MAX_DISTANCE_FROM_SWEEP_PCT = 0.015

# Минимальный размер риска от цены входа.
MIN_RISK_PCT = 0.001

# Буфер за экстремумом sweep.
SL_BUFFER_PCT = 0.0005
SL_BUFFER_ABS = 0.05


# ============================================================
# HELPERS
# ============================================================

def _price(value):
    """
    Безопасно превращает значение в float.
    """
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


def _safe_pct_distance(a, b):
    """
    Процентное расстояние между двумя ценами.
    """
    a = _price(a)
    b = _price(b)

    if a is None or b is None or a == 0:
        return None

    return abs(a - b) / a


# ============================================================
# 1H CONTEXT
# ============================================================

def get_context(candles_1h):
    """
    Определяем общий 1H контекст.

    Возвращает:
        LONG
        SHORT
        NEUTRAL
    """

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
    """
    Проверяет, насколько свежий sweep.

    Не разрешаем входить в старый sweep,
    который уже давно потерял актуальность.
    """

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
        # Если market.py не передал время,
        # считаем sweep допустимым.
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

    # Binance timestamp обычно в миллисекундах.
    if sweep_time < 10_000_000_000:
        sweep_time *= 1000

    if latest_time < 10_000_000_000:
        latest_time *= 1000

    # 5M candle = 300000 ms
    age_candles = (latest_time - sweep_time) / 300000

    return age_candles <= MAX_SWEEP_AGE_CANDLES


# ============================================================
# 15M CONFIRMATION
# ============================================================

def confirm_15m(candles_15m, direction, sweep):
    """
    Подтверждение после sweep.

    LONG:
        желательно восстановление выше зоны sweep /
        bullish continuation.

    SHORT:
        желательно восстановление ниже зоны sweep /
        bearish continuation.

    Важный принцип:
        sweep -> потом confirmation.
    """

    if not candles_15m or len(candles_15m) < 3:
        return False

    recent = candles_15m[-3:]

    closes = []
    opens = []

    for candle in recent:
        o = _candle_open(candle)
        c = _candle_close(candle)

        if o is not None:
            opens.append(o)

        if c is not None:
            closes.append(c)

    if len(closes) < 2:
        return False

    last_close = closes[-1]
    prev_close = closes[-2]

    sweep_level = None

    if sweep:
        sweep_level = (
            sweep.get("level")
            or sweep.get("price")
            or sweep.get("sweep_level")
        )

    sweep_level = _price(sweep_level)

    if direction == "LONG":

        bullish_candle = (
            len(opens) >= 1
            and closes[-1] > opens[-1]
        )

        higher_close = last_close > prev_close

        if sweep_level is not None:
            reclaimed = last_close > sweep_level
            return reclaimed and (bullish_candle or higher_close)

        return bullish_candle or higher_close

    if direction == "SHORT":

        bearish_candle = (
            len(opens) >= 1
            and closes[-1] < opens[-1]
        )

        lower_close = last_close < prev_close

        if sweep_level is not None:
            rejected = last_close < sweep_level
            return rejected and (bearish_candle or lower_close)

        return bearish_candle or lower_close

    return False


# ============================================================
# 5M TRIGGER
# ============================================================

def check_5m_trigger(candles_5m, direction, sweep):
    """
    Ищем непосредственный 5M trigger после 15M confirmation.

    LONG:
        bullish candle + движение вверх.

    SHORT:
        bearish candle + движение вниз.
    """

    if not candles_5m or len(candles_5m) < 3:
        return False

    recent = candles_5m[-3:]

    last = recent[-1]
    prev = recent[-2]

    last_open = _candle_open(last)
    last_close = _candle_close(last)

    prev_close = _candle_close(prev)

    if (
        last_open is None
        or last_close is None
        or prev_close is None
    ):
        return False

    if direction == "LONG":

        bullish = last_close > last_open
        momentum = last_close > prev_close

        return bullish and momentum

    if direction == "SHORT":

        bearish = last_close < last_open
        momentum = last_close < prev_close

        return bearish and momentum

    return False


# ============================================================
# ORDER FLOW
# ============================================================

def check_order_flow(order_flow, direction):
    """
    Order flow является дополнительным фильтром.

    Отсутствие order flow НЕ блокирует сделку.
    """

    if not order_flow:
        return None

    try:
        value = float(order_flow)
    except Exception:
        return None

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
    """
    Не входим слишком далеко от sweep.
    """

    if not sweep:
        return False

    level = (
        sweep.get("level")
        or sweep.get("price")
        or sweep.get("sweep_level")
    )

    level = _price(level)
    price = _price(price)

    if level is None or price is None or level == 0:
        return True

    distance = abs(price - level) / level

    return distance <= MAX_DISTANCE_FROM_SWEEP_PCT


# ============================================================
# SWEEP EXTREME
# ============================================================

def get_sweep_extreme(sweep, candles_5m, direction):
    """
    Берём реальный экстремум sweep.

    Приоритет:

    1. extreme из market.py
    2. low/high самого sweep
    3. candle sweep_time
    4. candle, пересекшую уровень
    5. fallback на liquidity level
    """

    if not sweep:
        return None

    # --------------------------------------------------------
    # 1. Уже переданный extreme
    # --------------------------------------------------------

    candidates = [
        sweep.get("sweep_extreme"),
        sweep.get("extreme"),
        sweep.get("sweep_low"),
        sweep.get("sweep_high"),
    ]

    for value in candidates:
        value = _price(value)

        if value is not None:
            return value

    # --------------------------------------------------------
    # 2. sweep candle
    # --------------------------------------------------------

    sweep_time = (
        sweep.get("sweep_time")
        or sweep.get("time")
        or sweep.get("timestamp")
    )

    if sweep_time is not None and candles_5m:

        try:
            sweep_time_int = int(float(sweep_time))
        except Exception:
            sweep_time_int = None

        if sweep_time_int is not None:

            if sweep_time_int < 10_000_000_000:
                sweep_time_int *= 1000

            best_candle = None
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

                distance = abs(candle_time - sweep_time_int)

                if best_distance is None or distance < best_distance:
                    best_distance = distance
                    best_candle = candle

            if best_candle is not None:

                if direction == "LONG":
                    value = _candle_low(best_candle)

                    if value is not None:
                        return value

                if direction == "SHORT":
                    value = _candle_high(best_candle)

                    if value is not None:
                        return value

    # --------------------------------------------------------
    # 3. Ищем candle, которая пересекла уровень
    # --------------------------------------------------------

    level = (
        sweep.get("level")
        or sweep.get("price")
        or sweep.get("sweep_level")
    )

    level = _price(level)

    if level is not None and candles_5m:

        for candle in reversed(candles_5m[-MAX_SWEEP_AGE_CANDLES:]):

            high = _candle_high(candle)
            low = _candle_low(candle)

            if high is None or low is None:
                continue

            if direction == "LONG" and low < level:

                return low

            if direction == "SHORT" and high > level:

                return high

    # --------------------------------------------------------
    # 4. fallback
    # --------------------------------------------------------

    return level


# ============================================================
# STOP LOSS
# ============================================================

def calculate_sl(entry, sweep_extreme, direction):
    """
    SL ставится за реальным экстремумом sweep.
    """

    entry = _price(entry)
    sweep_extreme = _price(sweep_extreme)

    if entry is None or sweep_extreme is None:
        return None

    if direction == "LONG":

        # SL ниже low sweep
        buffer = max(
            sweep_extreme * SL_BUFFER_PCT,
            SL_BUFFER_ABS
        )

        sl = sweep_extreme - buffer

        if sl >= entry:
            return None

        return sl

    if direction == "SHORT":

        # SL выше high sweep
        buffer = max(
            sweep_extreme * SL_BUFFER_PCT,
            SL_BUFFER_ABS
        )

        sl = sweep_extreme + buffer

        if sl <= entry:
            return None

        return sl

    return None


# ============================================================
# OPPOSITE MAJOR LIQUIDITY
# ============================================================

def find_nearest_opposite_liquidity(
    major_levels,
    entry,
    direction
):
    """
    Ищет ближайшую крупную ликвидность,
    находящуюся по направлению TP.
    """

    entry = _price(entry)

    if entry is None:
        return None

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

        if direction == "LONG":

            if value <= entry:
                continue

            # Если явно LOW — это не нужная нам верхняя liquidity.
            if side:
                side_text = str(side).upper()

                if side_text in ("LOW", "L", "LONG"):
                    continue

            candidates.append(value)

        elif direction == "SHORT":

            if value >= entry:
                continue

            if side:
                side_text = str(side).upper()

                if side_text in ("HIGH", "H", "SHORT"):
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
    """
    TP ВСЕГДА = ровно 2R.

    ВАЖНО:

    Мы больше НЕ уменьшаем TP из-за liquidity.

    Вместо этого liquidity используется как фильтр:

        liquidity < 1.5R
            -> NO TRADE

        liquidity 1.5R–2R
            -> TRADE разрешён

        liquidity >= 2R
            -> нормальный READY
    """

    entry = _price(entry)
    sl = _price(sl)

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
    # TP = 2R
    # --------------------------------------------------------

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
        }

    # --------------------------------------------------------
    # Opposing major liquidity
    # --------------------------------------------------------

    opposing = find_nearest_opposite_liquidity(
        major_levels or [],
        entry,
        direction
    )

    liquidity_r = None

    if opposing is not None:

        liquidity_distance = abs(opposing - entry)

        liquidity_r = liquidity_distance / risk

        # Слишком близкая liquidity.
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

        # Liquidity между 1.5R и 2R.
        if liquidity_r < REQUIRED_RR:

            return {
                "valid": True,
                "tp": tp,
                "rr": REQUIRED_RR,
                "reason": (
                    f"2R target; opposing liquidity "
                    f"at {liquidity_r:.2f}R"
                ),
                "opposing_liquidity": opposing,
                "liquidity_r": liquidity_r,
            }

    # Liquidity отсутствует или находится дальше 2R.
    return {
        "valid": True,
        "tp": tp,
        "rr": REQUIRED_RR,
        "reason": "2R target",
        "opposing_liquidity": opposing,
        "liquidity_r": liquidity_r,
    }


# ============================================================
# SWEEP SCORE
# ============================================================

def get_sweep_bonus(sweep):
    """
    Дополнительные баллы за качество sweep.
    """

    if not sweep:
        return 0

    quality = sweep.get("quality")

    try:
        quality = float(quality)
    except Exception:
        quality = None

    if quality is None:
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
    """
    Используем detect_structure из market.py,
    если он доступен.

    CHoCH / BOS являются бонусом,
    а не обязательным условием.
    """

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

    text = str(structure_type).upper()

    if "CHOCH" in text:
        return 10, structure_type

    if "BOS" in text:
        return 7, structure_type

    return 0, structure_type


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
    """
    Главный анализ TradeMind 4.1.

    Порядок:

    1. Sweep
    2. Freshness
    3. Direction
    4. 1H context
    5. 15M confirmation
    6. 5M trigger
    7. Entry
    8. SL
    9. TP 2R
    10. Liquidity danger filter
    11. Score
    12. READY / WAIT / NO TRADE
    """

    price = _price(price)

    # --------------------------------------------------------
    # Basic validation
    # --------------------------------------------------------

    if price is None:
        return {
            "status": "WAIT",
            "stage": "NO_DATA",
            "score": 0,
            "direction": None,
            "reason": "invalid_price",
        }

    if not candles_1h or not candles_15m or not candles_5m:
        return {
            "status": "WAIT",
            "stage": "NO_DATA",
            "score": 0,
            "direction": None,
            "reason": "missing_candles",
        }

    if not major_levels:
        return {
            "status": "WAIT",
            "stage": "NO_LIQUIDITY",
            "score": 20,
            "direction": None,
            "reason": "no_major_liquidity",
        }

    # --------------------------------------------------------
    # 1. SWEEP
    # --------------------------------------------------------

    if not sweep:
        return {
            "status": "WAIT",
            "stage": "WAIT_SWEEP",
            "score": 30,
            "direction": None,
            "reason": "no_major_liquidity_sweep",
        }

    direction = (
        sweep.get("direction")
        or sweep.get("side")
        or sweep.get("signal")
    )

    if direction is None:
        return {
            "status": "WAIT",
            "stage": "INVALID_SWEEP",
            "score": 30,
            "direction": None,
            "reason": "sweep_direction_missing",
        }

    direction = str(direction).upper()

    # Нормализуем BUY/SELL.
    if direction in ("BUY", "BULLISH"):
        direction = "LONG"

    if direction in ("SELL", "BEARISH"):
        direction = "SHORT"

    if direction not in ("LONG", "SHORT"):
        return {
            "status": "WAIT",
            "stage": "INVALID_SWEEP",
            "score": 30,
            "direction": None,
            "reason": "invalid_direction",
        }

    # --------------------------------------------------------
    # 2. SWEEP FRESHNESS
    # --------------------------------------------------------

    if not sweep_is_fresh(sweep, candles_5m):
        return {
            "status": "WAIT",
            "stage": "OLD_SWEEP",
            "score": 35,
            "direction": direction,
            "reason": "sweep_too_old",
            "sweep": sweep,
        }

    # --------------------------------------------------------
    # 3. DISTANCE FROM SWEEP
    # --------------------------------------------------------

    if not distance_from_sweep_ok(
        price,
        sweep,
        direction
    ):
        return {
            "status": "WAIT",
            "stage": "CHASE",
            "score": 40,
            "direction": direction,
            "reason": "price_too_far_from_sweep",
            "sweep": sweep,
        }

    # --------------------------------------------------------
    # 4. 1H CONTEXT
    # --------------------------------------------------------

    context_1h = get_context(candles_1h)

    # --------------------------------------------------------
    # 5. 15M CONFIRMATION
    # --------------------------------------------------------

    confirmed_15m = confirm_15m(
        candles_15m,
        direction,
        sweep
    )

    if not confirmed_15m:
        return {
            "status": "WAIT",
            "stage": "WAIT_15M_CONFIRMATION",
            "score": 55,
            "direction": direction,
            "context_1h": context_1h,
            "reason": "no_15m_confirmation",
            "sweep": sweep,
        }

    # --------------------------------------------------------
    # 6. 5M TRIGGER
    # --------------------------------------------------------

    trigger_5m = check_5m_trigger(
        candles_5m,
        direction,
        sweep
    )

    if not trigger_5m:
        return {
            "status": "WAIT",
            "stage": "WAIT_5M_TRIGGER",
            "score": 70,
            "direction": direction,
            "context_1h": context_1h,
            "reason": "no_5m_trigger",
            "sweep": sweep,
        }

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = 75

    # Sweep quality
    score += get_sweep_bonus(sweep)

    # Structure
    structure_bonus, structure = get_structure_bonus(
        candles_15m,
        direction
    )

    score += structure_bonus

    # 1H alignment
    if context_1h == direction:
        score += 5

    # Contrarian 1H context does not automatically kill trade.
    # It simply gives no bonus.

    # Order flow is optional.
    order_flow_result = check_order_flow(
        order_flow,
        direction
    )

    if order_flow_result is True:
        score += 5

    elif order_flow_result is False:
        # Contrary order flow is a warning, but we do not
        # completely block the setup.
        score -= 5

    # Limit
    score = max(0, min(100, int(score)))

    # --------------------------------------------------------
    # 7. ENTRY
    # --------------------------------------------------------

    entry = price

    # --------------------------------------------------------
    # 8. SWEEP EXTREME
    # --------------------------------------------------------

    sweep_extreme = get_sweep_extreme(
        sweep,
        candles_5m,
        direction
    )

    if sweep_extreme is None:
        return {
            "status": "WAIT",
            "stage": "NO_SWEEP_EXTREME",
            "score": score,
            "direction": direction,
            "context_1h": context_1h,
            "structure": structure,
            "reason": "cannot_determine_sweep_extreme",
            "sweep": sweep,
        }

    # --------------------------------------------------------
    # 9. STOP LOSS
    # --------------------------------------------------------

    sl = calculate_sl(
        entry,
        sweep_extreme,
        direction
    )

    if sl is None:
        return {
            "status": "WAIT",
            "stage": "INVALID_SL",
            "score": score,
            "direction": direction,
            "context_1h": context_1h,
            "structure": structure,
            "reason": "invalid_stop_loss",
            "sweep_extreme": sweep_extreme,
        }

    risk = abs(entry - sl)

    if risk <= 0:
        return {
            "status": "WAIT",
            "stage": "INVALID_RISK",
            "score": score,
            "direction": direction,
            "reason": "zero_risk",
        }

    # --------------------------------------------------------
    # 10. TAKE PROFIT
    # --------------------------------------------------------

    tp_result = calculate_tp(
        entry,
        sl,
        direction,
        major_levels
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
            "reason": tp_result.get("reason"),
            "opposing_liquidity": tp_result.get(
                "opposing_liquidity"
            ),
            "liquidity_r": tp_result.get(
                "liquidity_r"
            ),
            "sweep": sweep,
            "sweep_extreme": sweep_extreme,
        }

    tp = tp_result["tp"]
    rr = tp_result["rr"]

    # --------------------------------------------------------
    # 11. FINAL SCORE CHECK
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
            "tp": tp,
            "rr": rr,
            "reason": "score_below_minimum",
            "tp_reason": tp_result.get("reason"),
            "opposing_liquidity": tp_result.get(
                "opposing_liquidity"
            ),
            "liquidity_r": tp_result.get(
                "liquidity_r"
            ),
            "sweep": sweep,
            "sweep_extreme": sweep_extreme,
        }

    # --------------------------------------------------------
    # 12. READY
    # --------------------------------------------------------

    liquidity_warning = None

    liquidity_r = tp_result.get("liquidity_r")

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
        "tp": tp,
        "rr": rr,

        # Sweep
        "sweep": sweep,
        "sweep_extreme": sweep_extreme,

        # Liquidity
        "opposing_liquidity": tp_result.get(
            "opposing_liquidity"
        ),
        "liquidity_r": liquidity_r,
        "liquidity_warning": liquidity_warning,

        # TP
        "tp_reason": tp_result.get("reason"),

        # Order flow
        "order_flow": order_flow_result,

        # Risk
        "risk_distance": risk,
        "risk_pct": risk / entry,

        # Human-readable reason
        "reason": (
            "TradeMind 4.1 READY: "
            "major sweep → 15M confirmation → "
            "5M trigger → TP 2R"
        ),
    }