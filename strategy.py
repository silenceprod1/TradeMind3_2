# -*- coding: utf-8 -*-

"""
TradeMind 3.10

Стратегия:

1H context
→ 15M key zone
→ Major liquidity sweep 2.0
→ 15M confirmation ПОСЛЕ sweep
→ CHoCH / BOS как дополнительный Score
→ 5M trigger ПОСЛЕ confirmation
→ Entry
→ SL за экстремумом sweep
→ TP по ближайшей крупной противоположной ликвидности
→ если ликвидность дальше 2R → TP = 2R
→ один TP

ВАЖНО:

CHoCH / BOS НЕ являются обязательным условием входа.

Они только усиливают Score:

CHoCH = +10
BOS   = +7

Если структуры нет:
сделка всё равно может пройти,
если выполнены основные условия стратегии.
"""

# =========================================================
# OPTIONAL STRUCTURE IMPORT
# =========================================================

try:
    from market import detect_structure
except Exception:
    detect_structure = None


# =========================================================
# 1H CONTEXT
# =========================================================

def get_context(candles):

    if len(candles) < 12:
        return "neutral"

    first = candles[-12:-6]
    second = candles[-6:]

    first_high = max(x["high"] for x in first)
    first_low = min(x["low"] for x in first)

    second_high = max(x["high"] for x in second)
    second_low = min(x["low"] for x in second)

    if second_high > first_high and second_low > first_low:
        return "bullish"

    if second_high < first_high and second_low < first_low:
        return "bearish"

    return "neutral"


# =========================================================
# 15M CONFIRMATION
# =========================================================

def confirm_15m(candles, direction, after_time=None):

    if len(candles) < 6:
        return False, "Недостаточно 15M данных", None

    candidates = candles[-5:]

    if after_time is not None:
        candidates = [
            candle
            for candle in candidates
            if candle["open_time"] > after_time
        ]

    if not candidates:
        return (
            False,
            "После sweep ещё нет новой 15M свечи",
            None
        )

    for last in reversed(candidates):

        idx = candles.index(last)

        previous = candles[
            max(0, idx - 4):
            idx
        ]

        if len(previous) < 1:
            continue

        candle_range = max(
            last["high"] - last["low"],
            0.000001
        )

        body = abs(
            last["close"] - last["open"]
        )

        body_ratio = body / candle_range

        previous_high = max(
            x["high"] for x in previous
        )

        previous_low = min(
            x["low"] for x in previous
        )

        # =====================================================
        # LONG
        # =====================================================

        if direction == "LONG":

            # Сильное bullish подтверждение
            if (
                last["close"] > last["open"]
                and last["close"] > previous_high
                and body_ratio >= 0.45
            ):
                return (
                    True,
                    "15M bullish confirmation",
                    last["open_time"]
                )

            # Bullish rejection
            if (
                last["low"] < previous_low
                and last["close"] > last["open"]
                and last["close"] >
                last["low"] + candle_range * 0.60
            ):
                return (
                    True,
                    "15M bullish rejection",
                    last["open_time"]
                )

        # =====================================================
        # SHORT
        # =====================================================

        if direction == "SHORT":

            # Сильное bearish подтверждение
            if (
                last["close"] < last["open"]
                and last["close"] < previous_low
                and body_ratio >= 0.45
            ):
                return (
                    True,
                    "15M bearish confirmation",
                    last["open_time"]
                )

            # Bearish rejection
            if (
                last["high"] > previous_high
                and last["close"] < last["open"]
                and last["close"] <
                last["high"] - candle_range * 0.60
            ):
                return (
                    True,
                    "15M bearish rejection",
                    last["open_time"]
                )

    return (
        False,
        "Нет подтверждения на 15M",
        None
    )


# =========================================================
# 5M TRIGGER
# =========================================================

def check_5m_trigger(candles, direction, after_time=None):

    if len(candles) < 6:
        return False, "Недостаточно 5M данных", None

    candidates = candles[-8:]

    if after_time is not None:
        candidates = [
            candle
            for candle in candidates
            if candle["open_time"] > after_time
        ]

    if not candidates:
        return (
            False,
            "После 15M confirmation ещё нет нового 5M trigger",
            None
        )

    for last in reversed(candidates):

        idx = candles.index(last)

        previous = candles[
            max(0, idx - 4):
            idx
        ]

        if len(previous) < 1:
            continue

        candle_range = max(
            last["high"] - last["low"],
            0.000001
        )

        body = abs(
            last["close"] - last["open"]
        )

        body_ratio = body / candle_range

        previous_high = max(
            x["high"] for x in previous
        )

        previous_low = min(
            x["low"] for x in previous
        )

        # =====================================================
        # LONG
        # =====================================================

        if direction == "LONG":

            if (
                last["close"] > last["open"]
                and last["close"] > previous_high
                and body_ratio >= 0.45
            ):
                return (
                    True,
                    "5M bullish trigger",
                    last["open_time"]
                )

            if (
                last["low"] < previous_low
                and last["close"] > last["open"]
                and last["close"] >
                last["low"] + candle_range * 0.55
            ):
                return (
                    True,
                    "5M bullish rejection",
                    last["open_time"]
                )

        # =====================================================
        # SHORT
        # =====================================================

        if direction == "SHORT":

            if (
                last["close"] < last["open"]
                and last["close"] < previous_low
                and body_ratio >= 0.45
            ):
                return (
                    True,
                    "5M bearish trigger",
                    last["open_time"]
                )

            if (
                last["high"] > previous_high
                and last["close"] < last["open"]
                and last["close"] <
                last["high"] - candle_range * 0.55
            ):
                return (
                    True,
                    "5M bearish rejection",
                    last["open_time"]
                )

    return (
        False,
        "Нет подтверждения на 5M",
        None
    )


# =========================================================
# ORDER FLOW
# =========================================================

def check_order_flow(order_flow, direction):

    if not order_flow:
        return None, "Order Flow не подключён"

    absorption = str(
        order_flow.get("absorption", "")
    ).lower()

    delta = order_flow.get("delta")

    if direction == "LONG":

        if absorption in (
            "buyer",
            "buyers",
            "buy"
        ):
            return True, "Buyer absorption"

        if delta is not None:
            try:
                if float(delta) > 0:
                    return True, "Positive delta"
            except Exception:
                pass

    if direction == "SHORT":

        if absorption in (
            "seller",
            "sellers",
            "sell"
        ):
            return True, "Seller absorption"

        if delta is not None:
            try:
                if float(delta) < 0:
                    return True, "Negative delta"
            except Exception:
                pass

    return False, "Order Flow против направления"


# =========================================================
# DISTANCE FROM SWEEP
# =========================================================

def distance_from_sweep_ok(price, sweep_level):

    distance = abs(
        float(price) -
        float(sweep_level)
    )

    if distance > float(price) * 0.015:
        return False

    return True


# =========================================================
# SWEEP EXTREME
# =========================================================

def get_sweep_extreme(
    candles_15m,
    direction,
    sweep_level,
    sweep_time=None
):
    """
    Фактический экстремум sweep-свечи.

    LONG:
        SL ниже low sweep.

    SHORT:
        SL выше high sweep.

    ВАЖНО:
    Если sweep произошёл на 5M, а 15M sweep_time
    не совпал с 15M свечой, используем fallback
    по фактическому пересечению уровня.
    """

    fallback = float(sweep_level)

    if not candles_15m:
        return fallback

    # ---------------------------------------------------------
    # Сначала ищем конкретную свечу
    # ---------------------------------------------------------

    if sweep_time is not None:

        for candle in reversed(candles_15m):

            if candle.get("open_time") == sweep_time:

                if direction == "LONG":
                    return float(candle["low"])

                if direction == "SHORT":
                    return float(candle["high"])

    # ---------------------------------------------------------
    # Fallback:
    # ищем фактическое пересечение уровня
    # ---------------------------------------------------------

    for candle in reversed(candles_15m[-8:]):

        if direction == "LONG":

            if (
                float(candle["low"]) <= fallback
                and float(candle["close"]) > fallback
            ):
                return float(candle["low"])

        elif direction == "SHORT":

            if (
                float(candle["high"]) >= fallback
                and float(candle["close"]) < fallback
            ):
                return float(candle["high"])

    return fallback


# =========================================================
# MAJOR LEVEL PRICE
# =========================================================

def get_level_price(level):

    if isinstance(level, (int, float)):
        return float(level)

    if not isinstance(level, dict):
        return None

    for key in (
        "price",
        "level",
        "value",
        "high",
        "low"
    ):

        value = level.get(key)

        if value is not None:

            try:
                return float(value)
            except Exception:
                continue

    return None


# =========================================================
# FIND OPPOSITE LIQUIDITY
# =========================================================

def find_nearest_opposite_liquidity(
    entry,
    direction,
    major_levels
):
    """
    LONG:
        ближайшая крупная зона выше ENTRY.

    SHORT:
        ближайшая крупная зона ниже ENTRY.
    """

    if not major_levels:
        return None

    candidates = []

    for level in major_levels:

        price = get_level_price(level)

        if price is None:
            continue

        if direction == "LONG":

            if price > entry:
                candidates.append(price)

        elif direction == "SHORT":

            if price < entry:
                candidates.append(price)

    if not candidates:
        return None

    if direction == "LONG":
        return min(candidates)

    return max(candidates)


# =========================================================
# CALCULATE SL
# =========================================================

def calculate_sl(
    entry,
    direction,
    sweep_extreme
):
    """
    SL находится за экстремумом sweep.
    """

    entry = float(entry)
    extreme = float(sweep_extreme)

    buffer = max(
        entry * 0.0005,
        0.05
    )

    if direction == "LONG":
        return extreme - buffer

    return extreme + buffer


# =========================================================
# CALCULATE TP
# =========================================================

def calculate_tp(
    entry,
    sl,
    direction,
    major_levels
):
    """
    Логика TP:

    1. Рассчитываем 2R.

    2. Ищем ближайшую крупную
       противоположную ликвидность.

    3. Если зона дальше 2R:
       TP = 2R.

    4. Если зона ближе 2R:
       TP перед зоной.

    5. Всегда один TP.
    """

    entry = float(entry)
    sl = float(sl)

    risk = abs(entry - sl)

    if risk <= 0:
        return None, None, None

    # =====================================================
    # 2R
    # =====================================================

    if direction == "LONG":
        tp_2r = entry + risk * 2
    else:
        tp_2r = entry - risk * 2

    # =====================================================
    # Ближайшая противоположная ликвидность
    # =====================================================

    liquidity = find_nearest_opposite_liquidity(
        entry,
        direction,
        major_levels
    )

    # Нет зоны → 2R
    if liquidity is None:

        return (
            tp_2r,
            2.0,
            "TP = 2R, крупная противоположная ликвидность не найдена"
        )

    # =====================================================
    # LONG
    # =====================================================

    if direction == "LONG":

        # Ликвидность дальше 2R
        if liquidity >= tp_2r:

            return (
                tp_2r,
                2.0,
                "TP = 2R, ближайшая ликвидность дальше"
            )

        distance_to_zone = liquidity - entry

        if distance_to_zone <= 0:

            return (
                tp_2r,
                2.0,
                "TP = 2R"
            )

        # Оставляем небольшой запас перед ликвидностью
        tp_buffer = max(
            distance_to_zone * 0.08,
            entry * 0.0002
        )

        tp = liquidity - tp_buffer

    # =====================================================
    # SHORT
    # =====================================================

    else:

        # Ликвидность дальше 2R
        if liquidity <= tp_2r:

            return (
                tp_2r,
                2.0,
                "TP = 2R, ближайшая ликвидность дальше"
            )

        distance_to_zone = entry - liquidity

        if distance_to_zone <= 0:

            return (
                tp_2r,
                2.0,
                "TP = 2R"
            )

        tp_buffer = max(
            distance_to_zone * 0.08,
            entry * 0.0002
        )

        tp = liquidity + tp_buffer

    # =====================================================
    # Фактический RR
    # =====================================================

    actual_reward = abs(
        tp - entry
    )

    actual_rr = actual_reward / risk

    return (
        tp,
        actual_rr,
        "TP перед ближайшей крупной противоположной ликвидностью"
    )


# =========================================================
# SWEEP QUALITY BONUS
# =========================================================

def get_sweep_bonus(sweep):
    """
    Дополнительные баллы за качество Sweep 2.0.

    STRONG  → +5
    NORMAL  → +3
    WEAK    → +0

    Сам sweep всё равно остаётся обязательной
    частью основной стратегии.
    """

    if not sweep:
        return 0

    quality = str(
        sweep.get("quality", "")
    ).upper()

    sweep_score = sweep.get("score")

    # Если market.py дал quality
    if quality == "STRONG":
        return 5

    if quality == "NORMAL":
        return 3

    # Если quality отсутствует,
    # используем numeric score
    try:

        if sweep_score is not None:

            sweep_score = float(
                sweep_score
            )

            if sweep_score >= 80:
                return 5

            if sweep_score >= 55:
                return 3

    except Exception:
        pass

    return 0


# =========================================================
# STRUCTURE ANALYSIS
# =========================================================

def get_structure_analysis(
    candles_15m,
    direction
):
    """
    Получает CHoCH/BOS.

    CHoCH = +10
    BOS   = +7
    NONE  = +0

    Если structure недоступна:
    сделка НЕ блокируется.
    """

    default = {
        "structure": "NONE",
        "score": 0,
        "broken_level": None,
        "candle": None,
        "direction": direction
    }

    if not candles_15m:
        return default

    if detect_structure is None:
        return default

    try:

        structure = detect_structure(
            candles_15m,
            direction=direction
        )

        if not structure:
            return default

        structure_type = str(
            structure.get(
                "structure",
                "NONE"
            )
        ).upper()

        # -----------------------------------------------------
        # CHoCH
        # -----------------------------------------------------

        if structure_type == "CHoCH":

            return {
                **structure,
                "score": 10
            }

        # -----------------------------------------------------
        # BOS
        # -----------------------------------------------------

        if structure_type == "BOS":

            return {
                **structure,
                "score": 7
            }

        return {
            **structure,
            "score": 0
        }

    except Exception:

        return default


# =========================================================
# MAIN ANALYSIS
# =========================================================

def analyze(
    candles_1h,
    candles_15m,
    candles_5m,
    current_price,
    sweep=None,
    order_flow=None,
    major_levels=None,
    structure=None
):

    price = float(current_price)

    context_1h = get_context(
        candles_1h
    )

    context_15m = get_context(
        candles_15m
    )

    result = {

        "status": "WAIT",

        "direction": None,

        "stage": "WAIT",

        "score": 0,

        "reason": "",

        "entry": None,

        "sl": None,

        "tp": None,

        "rr": 2.0,

        "one_tp": True,

        "liquidity_type": None,

        "sweep_level": None,

        "sweep_extreme": None,

        "sweep_time": None,

        "sweep_quality": None,

        "sweep_score": 0,

        "sweep_bonus": 0,

        "structure": "NONE",

        "structure_score": 0,

        "structure_broken_level": None,

        "confirmation_15m": False,

        "confirmation_15m_text": None,

        "confirmation_15m_time": None,

        "trigger_5m": False,

        "trigger_5m_text": None,

        "trigger_5m_time": None,

        "order_flow": None,

        "context_1h": context_1h,

        "context_15m": context_15m,

        "major_levels": major_levels or [],

        "tp_reason": None
    }

    # =====================================================
    # NO SWEEP
    # =====================================================

    # Совместимость со старым и новым market.py.
    #
    # Старый вариант мог использовать:
    # sweep["swept"] = True
    #
    # Новый market.py возвращает sweep напрямую,
    # без поля "swept".
    #
    # Поэтому главным условием теперь является
    # наличие корректного sweep.

    if not sweep:

        result["stage"] = "WAIT"

        result["score"] = 30

        result["reason"] = (
            "Нет sweep крупной ликвидности — "
            "в середине не входим."
        )

        return result

    # =====================================================
    # SWEEP VALIDATION
    # =====================================================

    direction = sweep.get(
        "direction"
    )

    if direction not in (
        "LONG",
        "SHORT"
    ):

        result["stage"] = "WAIT"

        result["score"] = 30

        result["reason"] = (
            "Sweep имеет некорректное направление."
        )

        return result

    if sweep.get("level") is None:

        result["stage"] = "WAIT"

        result["score"] = 30

        result["reason"] = (
            "У sweep отсутствует уровень ликвидности."
        )

        return result

    # =====================================================
    # SWEEP DATA
    # =====================================================

    level = float(
        sweep["level"]
    )

    sweep_time = sweep.get(
        "open_time"
    )

    result["direction"] = direction

    result["sweep_level"] = level

    result["sweep_time"] = sweep_time

    result["liquidity_type"] = sweep.get(
        "liquidity_type"
    )

    result["sweep_quality"] = sweep.get(
        "quality"
    )

    try:
        result["sweep_score"] = int(
            sweep.get("score", 0)
        )
    except Exception:
        result["sweep_score"] = 0

    # =====================================================
    # SWEEP BONUS
    # =====================================================

    sweep_bonus = get_sweep_bonus(
        sweep
    )

    result["sweep_bonus"] = sweep_bonus

    # =====================================================
    # 15M CONFIRMATION
    # =====================================================

    (
        confirmation_15m,
        confirmation_text,
        confirmation_time
    ) = confirm_15m(
        candles_15m,
        direction,
        after_time=sweep_time
    )

    result["confirmation_15m"] = (
        confirmation_15m
    )

    result["confirmation_15m_text"] = (
        confirmation_text
    )

    result["confirmation_15m_time"] = (
        confirmation_time
    )

    # =====================================================
    # NO 15M CONFIRMATION
    # =====================================================

    if not confirmation_15m:

        result["stage"] = "SWEPT"

        result["status"] = "WAIT"

        # Sweep quality влияет на ожидание,
        # но не превращает sweep без confirmation
        # в готовую сделку.

        result["score"] = min(
            55 + sweep_bonus,
            79
        )

        result["reason"] = (
            "Sweep крупной ликвидности есть, "
            "но нет нового подтверждения на 15M."
        )

        return result

    # =====================================================
    # 15M CONFIRMED
    # =====================================================

    # Базовый Score после полноценного 15M confirmation
    score = 75

    result["stage"] = "15M_CONFIRMED"

    # Качество sweep
    score += sweep_bonus

    # =====================================================
    # CHoCH / BOS
    # =====================================================

    if structure is None:

        structure = get_structure_analysis(
            candles_15m,
            direction
        )

    if structure:

        structure_type = str(
            structure.get(
                "structure",
                "NONE"
            )
        ).upper()

        if structure_type == "CHOCH":
            structure_type = "CHoCH"

        elif structure_type == "BOS":
            structure_type = "BOS"

        else:
            structure_type = "NONE"

        if structure_type == "CHoCH":
            structure_score = 10

        elif structure_type == "BOS":
            structure_score = 7

        else:
            structure_score = 0

        result["structure"] = structure_type

        result["structure_score"] = (
            structure_score
        )

        result["structure_broken_level"] = (
            structure.get(
                "broken_level"
            )
        )

        score += structure_score

    # =====================================================
    # 5M TRIGGER
    # =====================================================

    (
        trigger_ok,
        trigger_text,
        trigger_time
    ) = check_5m_trigger(
        candles_5m,
        direction,
        after_time=confirmation_time
    )

    result["trigger_5m"] = trigger_ok

    result["trigger_5m_text"] = trigger_text

    result["trigger_5m_time"] = trigger_time

    # =====================================================
    # NO 5M TRIGGER
    # =====================================================

    if not trigger_ok:

        result["status"] = "WAIT"

        result["score"] = min(
            score,
            100
        )

        result["reason"] = (
            "15M подтверждение есть, "
            "но нет нового 5M trigger → "
            "нет входа."
        )

        return result

    # =====================================================
    # 5M CONFIRMED
    # =====================================================

    score += 20

    # =====================================================
    # 1H CONTEXT BONUS
    # =====================================================

    if (
        direction == "LONG"
        and context_1h == "bullish"
    ):
        score += 5

    elif (
        direction == "SHORT"
        and context_1h == "bearish"
    ):
        score += 5

    # =====================================================
    # 15M CONTEXT BONUS
    # =====================================================

    if (
        direction == "LONG"
        and context_15m == "bullish"
    ):
        score += 5

    elif (
        direction == "SHORT"
        and context_15m == "bearish"
    ):
        score += 5

    # =====================================================
    # ORDER FLOW
    # =====================================================

    flow_ok, flow_text = check_order_flow(
        order_flow,
        direction
    )

    result["order_flow"] = flow_text

    # Order Flow не подключён:
    # ничего не добавляем и не блокируем.

    if flow_ok is False:

        result["stage"] = "15M_CONFIRMED"

        result["status"] = "WAIT"

        result["score"] = min(
            score,
            100
        )

        result["reason"] = (
            "Order Flow против направления."
        )

        return result

    if flow_ok is True:
        score += 5

    # =====================================================
    # DON'T CHASE
    # =====================================================

    if not distance_from_sweep_ok(
        price,
        level
    ):

        result["stage"] = "MISSED"

        result["status"] = "WAIT"

        result["score"] = 65

        result["reason"] = (
            "Движение уже ушло от sweep — "
            "не догоняем."
        )

        return result

    # =====================================================
    # FINAL SCORE
    # =====================================================

    score = min(
        score,
        100
    )

    # =====================================================
    # SCORE < 80
    # =====================================================

    if score < 80:

        result["stage"] = "15M_CONFIRMED"

        result["status"] = "WAIT"

        result["score"] = score

        result["reason"] = (
            "TradeMind Score ниже 80 — "
            "вход запрещён."
        )

        return result

    # =====================================================
    # ENTRY
    # =====================================================

    entry = price

    # =====================================================
    # SWEEP EXTREME
    # =====================================================

    sweep_extreme = get_sweep_extreme(
        candles_15m,
        direction,
        level,
        sweep_time
    )

    result["sweep_extreme"] = round(
        sweep_extreme,
        4
    )

    # =====================================================
    # SL
    # =====================================================

    sl = calculate_sl(
        entry,
        direction,
        sweep_extreme
    )

    risk = abs(
        entry - sl
    )

    # =====================================================
    # INVALID RISK
    # =====================================================

    if risk <= 0:

        result["stage"] = "INVALID"

        result["status"] = "WAIT"

        result["score"] = 65

        result["reason"] = (
            "Некорректный риск."
        )

        return result

    if risk < 0.05:

        result["stage"] = "INVALID"

        result["status"] = "WAIT"

        result["score"] = 65

        result["reason"] = (
            "Слишком маленькая дистанция SL."
        )

        return result

    # =====================================================
    # TP
    # =====================================================

    (
        tp,
        actual_rr,
        tp_reason
    ) = calculate_tp(
        entry,
        sl,
        direction,
        major_levels or []
    )

    if tp is None:

        result["stage"] = "INVALID"

        result["status"] = "WAIT"

        result["score"] = 65

        result["reason"] = (
            "Не удалось рассчитать TP."
        )

        return result

    # =====================================================
    # FINAL READY
    # =====================================================

    result.update({

        "status": direction,

        "stage": "READY",

        "score": score,

        "entry": round(
            entry,
            4
        ),

        "sl": round(
            sl,
            4
        ),

        "tp": round(
            tp,
            4
        ),

        "rr": round(
            actual_rr,
            2
        ),

        "one_tp": True,

        "tp_reason": tp_reason,

        "reason": (
            "Major liquidity → "
            "Sweep 2.0 → "
            "15M confirmation → "
            "CHoCH/BOS bonus → "
            "5M trigger → "
            "SL за экстремумом sweep → "
            "TP по ближайшей крупной ликвидности."
        )
    })

    return result