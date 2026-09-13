# -*- coding: utf-8 -*-

"""
TradeMind 3.4
Стратегия:

1H context
→ 15M key zone
→ Major liquidity sweep
→ 15M confirmation ПОСЛЕ sweep
→ 5M trigger ПОСЛЕ confirmation
→ Entry / SL / TP
→ RR 1:2
→ Один TP
"""

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
    """
    Проверяем только закрытые 15M свечи,
    которые появились ПОСЛЕ sweep.
    """

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
        return False, "После sweep ещё нет новой 15M свечи", None

    # Проверяем от самой новой свечи назад
    for last in reversed(candidates):

        previous = candles[
            max(0, candles.index(last) - 4):
            candles.index(last)
        ]

        if len(previous) < 1:
            continue

        candle_range = max(
            last["high"] - last["low"],
            0.000001
        )

        body = abs(last["close"] - last["open"])
        body_ratio = body / candle_range

        previous_high = max(x["high"] for x in previous)
        previous_low = min(x["low"] for x in previous)

        # LONG
        if direction == "LONG":

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

        # SHORT
        if direction == "SHORT":

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

    return False, "Нет подтверждения на 15M", None


# =========================================================
# 5M TRIGGER
# =========================================================

def check_5m_trigger(candles, direction, after_time=None):
    """
    Проверяем только 5M свечи,
    которые появились ПОСЛЕ 15M confirmation.
    """

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
        return False, "После 15M confirmation ещё нет нового 5M trigger", None

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

        body = abs(last["close"] - last["open"])
        body_ratio = body / candle_range

        previous_high = max(x["high"] for x in previous)
        previous_low = min(x["low"] for x in previous)

        # LONG
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

        # SHORT
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

    return False, "Нет подтверждения на 5M", None


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

    # Не догоняем движение
    if distance > float(price) * 0.015:
        return False

    return True


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
    major_levels=None
):

    price = float(current_price)

    context_1h = get_context(candles_1h)
    context_15m = get_context(candles_15m)

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

        "sweep_time": None,

        "confirmation_15m": False,

        "confirmation_15m_text": None,

        "confirmation_15m_time": None,

        "trigger_5m": False,

        "trigger_5m_text": None,

        "trigger_5m_time": None,

        "order_flow": None,

        "context_1h": context_1h,

        "context_15m": context_15m,

        "major_levels": major_levels or []
    }

    # =====================================================
    # NO SWEEP
    # =====================================================

    if not sweep or not sweep.get("swept"):

        result["stage"] = "WAIT"

        result["score"] = 30

        result["reason"] = (
            "Нет sweep крупной ликвидности — "
            "в середине не входим."
        )

        return result


    # =====================================================
    # SWEEP
    # =====================================================

    direction = sweep["direction"]

    level = float(sweep["level"])

    sweep_time = sweep.get("open_time")

    result["direction"] = direction

    result["sweep_level"] = level

    result["sweep_time"] = sweep_time

    result["liquidity_type"] = sweep.get(
        "liquidity_type"
    )

    # =====================================================
    # 15M CONFIRMATION
    # =====================================================

    confirmation_15m, confirmation_text, confirmation_time = (
        confirm_15m(
            candles_15m,
            direction,
            after_time=sweep_time
        )
    )

    result["confirmation_15m"] = confirmation_15m

    result["confirmation_15m_text"] = (
        confirmation_text
    )

    result["confirmation_15m_time"] = (
        confirmation_time
    )

    # Нет 15M confirmation
    if not confirmation_15m:

        result["stage"] = "SWEPT"

        result["status"] = "WAIT"

        result["score"] = 55

        result["reason"] = (
            "Sweep крупной ликвидности есть, "
            "но нет нового подтверждения на 15M."
        )

        return result

    # =====================================================
    # 15M CONFIRMED
    # =====================================================

    score = 75

    result["stage"] = "15M_CONFIRMED"

    # =====================================================
    # 5M TRIGGER
    # =====================================================

    trigger_ok, trigger_text, trigger_time = (
        check_5m_trigger(
            candles_5m,
            direction,
            after_time=confirmation_time
        )
    )

    result["trigger_5m"] = trigger_ok

    result["trigger_5m_text"] = trigger_text

    result["trigger_5m_time"] = trigger_time

    # Нет 5M trigger
    if not trigger_ok:

        result["status"] = "WAIT"

        result["score"] = score

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

    # 1H context
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

    # 15M context
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
    # SCORE
    # =====================================================

    score = min(score, 100)

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
    # ENTRY / SL / TP
    # =====================================================

    entry = price

    buffer = max(
        price * 0.0008,
        0.08
    )

    if direction == "LONG":

        sl = level - buffer

    else:

        sl = level + buffer

    risk = abs(
        entry - sl
    )

    # Некорректный риск
    if risk <= 0:

        result["stage"] = "INVALID"

        result["status"] = "WAIT"

        result["score"] = 65

        result["reason"] = (
            "Некорректный риск."
        )

        return result

    # Слишком маленький SL
    if risk < 0.05:

        result["stage"] = "INVALID"

        result["status"] = "WAIT"

        result["score"] = 65

        result["reason"] = (
            "Слишком маленькая дистанция SL."
        )

        return result

    # =====================================================
    # EXACT 1:2
    # =====================================================

    if direction == "LONG":

        tp = entry + risk * 2

    else:

        tp = entry - risk * 2

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

        "rr": 2.0,

        "one_tp": True,

        "reason": (
            "Крупная ликвидность → "
            "sweep → "
            "15M confirmation → "
            "5M trigger → "
            "можно входить."
        )
    })

    return result


# =========================================================
# SOL ANALYSIS
# =========================================================

def analyze_sol(
    candles_1h,
    candles_15m,
    candles_5m,
    current_price,
    order_flow=None,
    sweep=None
):

    return analyze(

        candles_1h,

        candles_15m,

        candles_5m,

        current_price,

        sweep=sweep,

        order_flow=order_flow,

        major_levels=None
    )