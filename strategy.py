"""
TradeMind 5.8.2 — Strategy Engine

D1 → 1H → Major Liquidity → Sweep → 15M → 5M ILM → Entry → 1:2 TP

Важно:
- D1 задаёт направление.
- W1 используется только как fallback, если D1 нейтрален.
- 1H обязан совпадать с направлением.
- Используем только major liquidity.
- Sweep обязателен.
- 15M confirmation обязателен.
- 5M ILM trigger обязателен.
- TP всегда ровно 1:2.
- Уже swept liquidity не используется как TP.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


STRATEGY_VERSION = "5.8.2"

MIN_SCORE_READY = 80

REQUIRED_RR = 2.0

MAX_ENTRY_DISTANCE_PCT = 0.75

MIN_RECOVERY_RATIO = 1 / 3

MIN_REVERSAL_BODY_RATIO = 0.35

MIN_15M_BODY_RATIO = 0.35

MIN_5M_BODY_RATIO = 0.40

STRUCTURE_LOOKBACK = 40

SL_BUFFER_PCT = 0.10

MIN_TP_DISTANCE_PCT = 0.10

MAX_15M_CONFIRM_AGE = 8

MAX_5M_TRIGGER_AGE = 3


# ============================================================
# BASIC HELPERS
# ============================================================

def _safe_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _last(candles):
    if not candles:
        return None
    return candles[-1]


def _body(candle):
    return abs(
        candle["close"] - candle["open"]
    )


def _range(candle):
    return max(
        candle["high"] - candle["low"],
        0.00000001,
    )


def _body_ratio(candle):
    return _body(candle) / _range(candle)


def _bullish(candle):
    return candle["close"] > candle["open"]


def _bearish(candle):
    return candle["close"] < candle["open"]


def _pct_distance(a, b):
    if b in (None, 0):
        return 999.0

    return abs(a - b) / abs(b) * 100.0


def _same_price(a, b, tolerance_pct=0.15):
    if a is None or b is None:
        return False

    return _pct_distance(a, b) <= tolerance_pct


def _candle_time(candle):
    if not candle:
        return 0
    return candle.get("open_time", 0)


def _age(candles, candle):
    if not candles or not candle:
        return 999

    try:
        index = candles.index(candle)
        return len(candles) - 1 - index
    except ValueError:
        return 999


# ============================================================
# STRUCTURE
# ============================================================

def _swing_high(candles, index):
    if index <= 0:
        return False

    if index >= len(candles) - 1:
        return False

    return (
        candles[index]["high"]
        > candles[index - 1]["high"]
        and
        candles[index]["high"]
        >= candles[index + 1]["high"]
    )


def _swing_low(candles, index):
    if index <= 0:
        return False

    if index >= len(candles) - 1:
        return False

    return (
        candles[index]["low"]
        < candles[index - 1]["low"]
        and
        candles[index]["low"]
        <= candles[index + 1]["low"]
    )


def structure_context(
    candles,
    lookback=STRUCTURE_LOOKBACK,
):
    """
    Определяет структуру:
    BULLISH / BEARISH / NEUTRAL
    """

    if not candles or len(candles) < 8:
        return {
            "trend": "NEUTRAL",
            "reason": "Недостаточно свечей.",
            "highs": [],
            "lows": [],
        }

    data = candles[-lookback:]

    highs = []
    lows = []

    for i in range(1, len(data) - 1):

        if _swing_high(data, i):
            highs.append({
                "price": data[i]["high"],
                "index": i,
                "time": data[i]["open_time"],
            })

        if _swing_low(data, i):
            lows.append({
                "price": data[i]["low"],
                "index": i,
                "time": data[i]["open_time"],
            })

    if len(highs) < 2 or len(lows) < 2:
        return {
            "trend": "NEUTRAL",
            "reason": "Недостаточно swing points.",
            "highs": highs,
            "lows": lows,
        }

    h1 = highs[-2]["price"]
    h2 = highs[-1]["price"]

    l1 = lows[-2]["price"]
    l2 = lows[-1]["price"]

    higher_high = h2 > h1
    higher_low = l2 > l1

    lower_high = h2 < h1
    lower_low = l2 < l1

    if higher_high and higher_low:
        trend = "BULLISH"

    elif lower_high and lower_low:
        trend = "BEARISH"

    else:
        trend = "NEUTRAL"

    return {
        "trend": trend,
        "reason": (
            f"HH={higher_high} "
            f"HL={higher_low} "
            f"LH={lower_high} "
            f"LL={lower_low}"
        ),
        "highs": highs,
        "lows": lows,
    }


def context_d1(candles_d1):
    return structure_context(candles_d1)


def context_w1(candles_w1):
    return structure_context(
        candles_w1,
        lookback=min(30, len(candles_w1))
        if candles_w1
        else 30,
    )


def context_1h(candles_1h):
    return structure_context(candles_1h)


# ============================================================
# DIRECTION
# ============================================================

def direction_from_context(
    candles_d1,
    candles_w1,
    candles_1h,
):
    """
    D1 — главный фильтр.

    Если D1 нейтрален:
        W1 используется fallback.

    1H обязан совпасть.
    """

    d1 = context_d1(candles_d1)
    w1 = context_w1(candles_w1)
    h1 = context_1h(candles_1h)

    d1_trend = d1["trend"]
    w1_trend = w1["trend"]
    h1_trend = h1["trend"]

    if d1_trend in ("BULLISH", "BEARISH"):

        context_trend = d1_trend
        source = "D1"

    elif w1_trend in ("BULLISH", "BEARISH"):

        context_trend = w1_trend
        source = "W1 FALLBACK"

    else:

        return {
            "direction": None,
            "d1": d1_trend,
            "w1": w1_trend,
            "h1": h1_trend,
            "source": None,
            "reason": (
                f"D1={d1_trend}, "
                f"W1={w1_trend}, "
                f"1H={h1_trend} → "
                "нет ясного направления."
            ),
        }

    if h1_trend != context_trend:

        return {
            "direction": None,
            "d1": d1_trend,
            "w1": w1_trend,
            "h1": h1_trend,
            "source": source,
            "reason": (
                f"{source}={context_trend}, "
                f"1H={h1_trend} → "
                "1H НЕ СИНХРОНИЗИРОВАН."
            ),
        }

    direction = (
        "LONG"
        if context_trend == "BULLISH"
        else "SHORT"
    )

    return {
        "direction": direction,
        "d1": d1_trend,
        "w1": w1_trend,
        "h1": h1_trend,
        "source": source,
        "reason": (
            f"{source}={context_trend}, "
            f"1H={h1_trend} → "
            f"{direction} разрешён."
        ),
    }


# ============================================================
# MAJOR LIQUIDITY
# ============================================================

def _parse_liquidity(
    major_levels,
    current_price,
):
    """
    market.py:
        HIGH → side SHORT → BSL
        LOW  → side LONG  → SSL
    """

    bsl = []
    ssl = []

    for level in major_levels or []:

        price = _safe_float(
            level.get("price", level.get("level"))
        )

        if price is None:
            continue

        swept = bool(
            level.get("swept", False)
        )

        side = str(
            level.get("side", "")
        ).upper()

        level_type = str(
            level.get("type", "")
        ).upper()

        if side in ("SHORT", "SELL", "BSL"):
            pool = "BSL"

        elif side in ("LONG", "BUY", "SSL"):
            pool = "SSL"

        elif level_type == "HIGH":
            pool = "BSL"

        elif level_type == "LOW":
            pool = "SSL"

        else:
            if price > current_price:
                pool = "BSL"
            else:
                pool = "SSL"

        item = dict(level)

        item["price"] = price
        item["pool"] = pool
        item["swept"] = swept

        if pool == "BSL":
            bsl.append(item)
        else:
            ssl.append(item)

    bsl.sort(
        key=lambda x: x["price"]
    )

    ssl.sort(
        key=lambda x: x["price"],
        reverse=True,
    )

    return bsl, ssl


def _fresh_levels(levels):
    return [
        x for x in levels
        if not x.get("swept", False)
    ]


def find_directional_liquidity(
    major_levels,
    direction,
    current_price,
):
    bsl, ssl = _parse_liquidity(
        major_levels,
        current_price,
    )

    if direction == "LONG":

        available = [
            x for x in ssl
            if x["price"] < current_price
        ]

    else:

        available = [
            x for x in bsl
            if x["price"] > current_price
        ]

    return available


def find_structural_target(
    major_levels,
    direction,
    entry,
):
    """
    Target = следующая major liquidity
    по направлению сделки.

    Уже swept liquidity исключается.
    """

    bsl, ssl = _parse_liquidity(
        major_levels,
        entry,
    )

    if direction == "LONG":

        targets = [
            x for x in bsl
            if x["price"] > entry
            and not x.get("swept", False)
        ]

        targets.sort(
            key=lambda x: x["price"]
        )

    else:

        targets = [
            x for x in ssl
            if x["price"] < entry
            and not x.get("swept", False)
        ]

        targets.sort(
            key=lambda x: x["price"],
            reverse=True,
        )

    if not targets:
        return None

    return targets[0]


# ============================================================
# FVG / IMBALANCE
# ============================================================

def find_fvgs(candles):
    """
    Простая 3-candle FVG модель.

    Bullish FVG:
        candle[i].low > candle[i-2].high

    Bearish FVG:
        candle[i].high < candle[i-2].low
    """

    result = []

    if not candles or len(candles) < 3:
        return result

    for i in range(2, len(candles)):

        a = candles[i - 2]
        c = candles[i]

        if c["low"] > a["high"]:

            result.append({
                "type": "BULLISH",
                "low": a["high"],
                "high": c["low"],
                "index": i,
                "time": c["open_time"],
            })

        elif c["high"] < a["low"]:

            result.append({
                "type": "BEARISH",
                "low": c["high"],
                "high": a["low"],
                "index": i,
                "time": c["open_time"],
            })

    return result


def imbalance_context(candles_1h):
    fvgs = find_fvgs(candles_1h)

    if not fvgs:
        return {
            "status": "NONE",
            "type": None,
            "distance_pct": None,
        }

    recent = fvgs[-1]

    last_price = candles_1h[-1]["close"]

    distance = _pct_distance(
        last_price,
        (
            recent["high"]
            + recent["low"]
        ) / 2,
    )

    return {
        "status": "FOUND",
        "type": recent["type"],
        "distance_pct": round(
            distance,
            4,
        ),
        "zone": recent,
    }


# ============================================================
# 15M CONFIRMATION
# ============================================================

def confirm_15m(
    candles_15m,
    direction,
    sweep,
):
    if not candles_15m:
        return None

    if not sweep:
        return None

    recent = candles_15m[
        -MAX_15M_CONFIRM_AGE:
    ]

    sweep_level = _safe_float(
        sweep.get("level")
    )

    if sweep_level is None:
        return None

    for candle in reversed(recent):

        body_ratio = _body_ratio(candle)

        if body_ratio < MIN_15M_BODY_RATIO:
            continue

        if direction == "LONG":

            confirmed = (
                _bullish(candle)
                and
                candle["close"] > sweep_level
            )

        else:

            confirmed = (
                _bearish(candle)
                and
                candle["close"] < sweep_level
            )

        if confirmed:

            return {
                "confirmed": True,
                "direction": direction,
                "time": candle["open_time"],
                "close": candle["close"],
                "body_ratio": round(
                    body_ratio,
                    3,
                ),
                "reason": (
                    "15M body закрыт "
                    "за уровнем sweep."
                ),
            }

    return None


# ============================================================
# 5M ILM
# ============================================================

def calculate_recovery(
    candle,
    direction,
    sweep,
):
    if not candle or not sweep:
        return 0.0

    level = _safe_float(
        sweep.get("level")
    )

    extreme = _safe_float(
        sweep.get("extreme")
    )

    if level is None or extreme is None:
        return 0.0

    if direction == "LONG":

        manipulation = level - extreme

        if manipulation <= 0:
            return 0.0

        recovery = (
            candle["close"] - extreme
        )

    else:

        manipulation = extreme - level

        if manipulation <= 0:
            return 0.0

        recovery = (
            extreme - candle["close"]
        )

    return max(
        0.0,
        recovery / manipulation,
    )


def confirm_5m_ilm(
    candles_5m,
    direction,
    sweep,
):
    if not candles_5m or not sweep:
        return None

    if len(candles_5m) < 3:
        return None

    recent = candles_5m[
        -MAX_5M_TRIGGER_AGE:
    ]

    sweep_level = _safe_float(
        sweep.get("level")
    )

    if sweep_level is None:
        return None

    for i in range(
        len(recent) - 1,
        0,
        -1,
    ):

        current = recent[i]
        previous = recent[i - 1]

        current_body_ratio = _body_ratio(
            current
        )

        if (
            current_body_ratio
            < MIN_5M_BODY_RATIO
        ):
            continue

        recovery = calculate_recovery(
            current,
            direction,
            sweep,
        )

        if recovery < MIN_RECOVERY_RATIO:
            continue

        if direction == "LONG":

            reversal = (
                _bullish(current)
                and
                current["close"]
                > sweep_level
                and
                previous["close"]
                <= sweep_level
            )

        else:

            reversal = (
                _bearish(current)
                and
                current["close"]
                < sweep_level
                and
                previous["close"]
                >= sweep_level
            )

        if not reversal:
            continue

        return {
            "confirmed": True,
            "direction": direction,
            "time": current["open_time"],
            "entry": current["close"],
            "recovery_ratio": round(
                recovery,
                3,
            ),
            "body_ratio": round(
                current_body_ratio,
                3,
            ),
            "model": (
                "V-REVERSAL"
                if direction == "LONG"
                else "L-REVERSAL"
            ),
            "reason": (
                "5M ILM trigger подтверждён."
            ),
        }

    return None


# ============================================================
# STOP / TARGET
# ============================================================

def build_trade(
    direction,
    entry,
    sweep,
    structural_target=None,
):
    if not sweep:
        return None

    sweep_level = _safe_float(
        sweep.get("level")
    )

    if sweep_level is None:
        return None

    if direction == "LONG":

        sl = (
            sweep_level
            * (1 - SL_BUFFER_PCT / 100)
        )

        risk = entry - sl

        if risk <= 0:
            return None

        tp = entry + (
            risk * REQUIRED_RR
        )

    else:

        sl = (
            sweep_level
            * (1 + SL_BUFFER_PCT / 100)
        )

        risk = sl - entry

        if risk <= 0:
            return None

        tp = entry - (
            risk * REQUIRED_RR
        )

    tp_distance_pct = _pct_distance(
        tp,
        entry,
    )

    if tp_distance_pct < MIN_TP_DISTANCE_PCT:
        return None

    # --------------------------------------------------------
    # Structural target must support at least 2R.
    # --------------------------------------------------------

    if structural_target:

        target_price = _safe_float(
            structural_target.get("price")
        )

        if target_price is None:
            return None

        if _same_price(
            target_price,
            sweep_level,
        ):
            return None

        if direction == "LONG":

            if target_price <= entry:
                return None

            if target_price < tp:
                return None

        else:

            if target_price >= entry:
                return None

            if target_price > tp:
                return None

    return {
        "direction": direction,
        "entry": round(entry, 8),
        "sl": round(sl, 8),
        "tp": round(tp, 8),
        "rr": REQUIRED_RR,
        "risk": round(risk, 8),
        "tp_distance_pct": round(
            tp_distance_pct,
            4,
        ),
        "structural_target": (
            structural_target
            if structural_target
            else None
        ),
    }


# ============================================================
# SCORE
# ============================================================

def _score_setup(
    direction,
    sweep,
    confirmation_15m,
    trigger_5m,
    imbalance,
    trade,
):
    score = 80
    bonuses = []

    if sweep:

        strength = _safe_float(
            sweep.get("strength"),
            0.0,
        )

        touches = int(
            sweep.get("touches", 0)
            or 0
        )

        depth = _safe_float(
            sweep.get("depth_pct"),
            0.0,
        )

        if strength >= 0.75:
            score += 7
            bonuses.append(
                "сильная liquidity"
            )

        elif strength >= 0.60:
            score += 5
            bonuses.append(
                "хорошая liquidity"
            )

        if touches >= 3:
            score += 3
            bonuses.append(
                "stacked liquidity"
            )

        if depth >= 0.20:
            score += 2
            bonuses.append(
                "глубокий sweep"
            )

    if confirmation_15m:
        score += 3
        bonuses.append("15M confirmation")

    if trigger_5m:

        recovery = _safe_float(
            trigger_5m.get(
                "recovery_ratio"
            ),
            0.0,
        )

        if recovery >= 0.60:
            score += 5
            bonuses.append(
                "сильный 5M recovery"
            )

        else:
            score += 2
            bonuses.append(
                "5M recovery"
            )

    if imbalance:

        if (
            imbalance.get("status")
            == "FOUND"
        ):

            imbalance_type = imbalance.get(
                "type"
            )

            if (
                direction == "LONG"
                and
                imbalance_type
                == "BULLISH"
            ):
                score += 5
                bonuses.append(
                    "bullish 1H imbalance"
                )

            elif (
                direction == "SHORT"
                and
                imbalance_type
                == "BEARISH"
            ):
                score += 5
                bonuses.append(
                    "bearish 1H imbalance"
                )

            else:
                score -= 3
                bonuses.append(
                    "imbalance caution"
                )

    score = max(
        0,
        min(
            100,
            score,
        ),
    )

    return score, bonuses


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
    """
    Совместимый интерфейс с текущим bot(1).py.

    bot вызывает:

    analyze(
        candles_1h=...,
        candles_15m=...,
        candles_5m=...,
        current_price=...,
        major_levels=...,
        sweep=...,
        candles_d1=...,
        candles_w1=...,
    )
    """

    current_price = _safe_float(
        current_price
    )

    if current_price is None:
        return {
            "strategy_version": STRATEGY_VERSION,
            "stage": "ERROR",
            "score": 0,
            "reason": "Некорректная цена.",
            "direction": None,
        }

    # --------------------------------------------------------
    # 1. D1 / W1 / 1H
    # --------------------------------------------------------

    context = direction_from_context(
        candles_d1,
        candles_w1,
        candles_1h,
    )

    direction = context["direction"]

    base_result = {
        "strategy_version": STRATEGY_VERSION,

        "direction": direction,

        "d1_trend": context["d1"],
        "w1_trend": context["w1"],
        "h1_trend": context["h1"],

        "direction_source": context[
            "source"
        ],

        "major_levels": major_levels or [],

        "sweep": sweep,

        "confirmation_15m": None,

        "confirmation_5m": None,

        "confirmation": None,

        "trigger_5m": None,

        "entry": None,

        "sl": None,

        "tp": None,

        "rr": None,

        "structural_target": None,

        "diagnostics": {},
    }

    if direction is None:

        base_result.update({
            "stage": "1H",
            "score": 35,
            "reason": context["reason"],
        })

        base_result["diagnostics"] = {
            "direction": context,
            "message": (
                "D1 → W1 fallback → 1H "
                "не дают синхронизированного "
                "направления."
            ),
        }

        return base_result

    # --------------------------------------------------------
    # 2. Major liquidity
    # --------------------------------------------------------

    directional_levels = (
        find_directional_liquidity(
            major_levels,
            direction,
            current_price,
        )
    )

    if not directional_levels:

        base_result.update({
            "stage": "SWEEP",
            "score": 45,
            "reason": (
                f"{direction} разрешён, "
                "но подходящей major liquidity "
                "для sweep сейчас нет."
            ),
        })

        return base_result

    # --------------------------------------------------------
    # 3. Sweep
    # --------------------------------------------------------

    if not sweep:

        base_result.update({
            "stage": "SWEEP",
            "score": 45,
            "reason": (
                f"{direction} разрешён. "
                "Ждём major liquidity sweep."
            ),
        })

        return base_result

    sweep_direction = str(
        sweep.get("direction", "")
    ).upper()

    if sweep_direction != direction:

        base_result.update({
            "stage": "SWEEP",
            "score": 40,
            "reason": (
                f"Sweep={sweep_direction}, "
                f"но направление={direction}. "
                "Sweep не подходит."
            ),
        })

        return base_result

    sweep_level = _safe_float(
        sweep.get("level")
    )

    if sweep_level is None:

        base_result.update({
            "stage": "SWEEP",
            "score": 40,
            "reason": (
                "Sweep найден, "
                "но отсутствует уровень."
            ),
        })

        return base_result

    # --------------------------------------------------------
    # Anti-chase
    # --------------------------------------------------------

    distance_from_sweep = _pct_distance(
        current_price,
        sweep_level,
    )

    if (
        distance_from_sweep
        > MAX_ENTRY_DISTANCE_PCT
    ):

        base_result.update({
            "stage": "SWEEP",
            "score": 55,
            "reason": (
                f"Цена уже на "
                f"{distance_from_sweep:.2f}% "
                "от sweep. "
                "Не гонимся за движением."
            ),
        })

        return base_result

    # --------------------------------------------------------
    # 4. 15M
    # --------------------------------------------------------

    confirmation_15m = confirm_15m(
        candles_15m,
        direction,
        sweep,
    )

    base_result[
        "confirmation_15m"
    ] = confirmation_15m

    if not confirmation_15m:

        base_result.update({
            "stage": "15M",
            "score": 65,
            "reason": (
                f"{direction} + sweep подтверждены. "
                "Ждём 15M confirmation."
            ),
        })

        return base_result

    # --------------------------------------------------------
    # 5. 5M ILM
    # --------------------------------------------------------

    trigger_5m = confirm_5m_ilm(
        candles_5m,
        direction,
        sweep,
    )

    base_result[
        "confirmation_5m"
    ] = trigger_5m

    base_result[
        "trigger_5m"
    ] = trigger_5m

    # Alias для текущего bot(1).py
    base_result[
        "confirmation"
    ] = trigger_5m

    if not trigger_5m:

        base_result.update({
            "stage": "5M",
            "score": 72,
            "reason": (
                f"{direction} + sweep + "
                "15M confirmation. "
                "Ждём 5M ILM trigger."
            ),
        })

        return base_result

    # --------------------------------------------------------
    # Entry
    # --------------------------------------------------------

    entry = _safe_float(
        trigger_5m.get("entry"),
        current_price,
    )

    # --------------------------------------------------------
    # 6. Structural target
    # --------------------------------------------------------

    structural_target = find_structural_target(
        major_levels,
        direction,
        entry,
    )

    base_result[
        "structural_target"
    ] = structural_target

    if not structural_target:

        base_result.update({
            "stage": "TARGET",
            "score": 70,
            "reason": (
                "5M trigger есть, "
                "но следующая fresh major "
                "liquidity для TP не найдена."
            ),
        })

        return base_result

    target_price = _safe_float(
        structural_target.get("price")
    )

    if target_price is None:

        base_result.update({
            "stage": "TARGET",
            "score": 70,
            "reason": (
                "Major target найден, "
                "но его цена некорректна."
            ),
        })

        return base_result

    # Никогда не используем sweep pool как TP.
    if _same_price(
        target_price,
        sweep_level,
    ):

        base_result.update({
            "stage": "TARGET",
            "score": 70,
            "reason": (
                "Target совпадает "
                "со swept liquidity. "
                "Пропускаем."
            ),
        })

        return base_result

    # --------------------------------------------------------
    # 7. Exact 1:2
    # --------------------------------------------------------

    trade = build_trade(
        direction=direction,
        entry=entry,
        sweep=sweep,
        structural_target=structural_target,
    )

    if not trade:

        base_result.update({
            "stage": "TARGET",
            "score": 70,
            "reason": (
                "До следующей major liquidity "
                "не помещается полный 1:2. "
                "Ждём лучший вход или пропускаем."
            ),
        })

        return base_result

    # --------------------------------------------------------
    # 8. Optional imbalance
    # --------------------------------------------------------

    imbalance = imbalance_context(
        candles_1h
    )

    # --------------------------------------------------------
    # 9. Final score
    # --------------------------------------------------------

    score, bonuses = _score_setup(
        direction=direction,
        sweep=sweep,
        confirmation_15m=confirmation_15m,
        trigger_5m=trigger_5m,
        imbalance=imbalance,
        trade=trade,
    )

    if score < MIN_SCORE_READY:

        base_result.update({
            "stage": "WAIT",
            "score": score,
            "reason": (
                "Все основные условия есть, "
                f"но score={score} < "
                f"{MIN_SCORE_READY}."
            ),
        })

        base_result["diagnostics"] = {
            "imbalance": imbalance,
            "bonuses": bonuses,
            "distance_from_sweep_pct":
                round(
                    distance_from_sweep,
                    4,
                ),
        }

        return base_result

    # --------------------------------------------------------
    # READY
    # --------------------------------------------------------

    base_result.update({
        "stage": "READY",
        "score": score,

        "entry": trade["entry"],
        "sl": trade["sl"],
        "tp": trade["tp"],
        "rr": trade["rr"],

        "risk": trade["risk"],

        "tp_distance_pct":
            trade["tp_distance_pct"],

        "reason": (
            f"{direction} READY. "
            "D1 → 1H → Sweep → "
            "15M → 5M ILM → "
            "1:2 подтверждено."
        ),
    })

    base_result["diagnostics"] = {
        "imbalance": imbalance,

        "bonuses": bonuses,

        "distance_from_sweep_pct":
            round(
                distance_from_sweep,
                4,
            ),

        "sweep_strength":
            sweep.get("strength"),

        "sweep_touches":
            sweep.get("touches"),

        "sweep_depth_pct":
            sweep.get("depth_pct"),

        "confirmation_15m":
            confirmation_15m,

        "trigger_5m":
            trigger_5m,

        "target":
            structural_target,
    }

    return base_result


# ============================================================
# COMPATIBILITY HELPERS
# ============================================================

def analyze_symbol(
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
    return analyze(
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,
        current_price=current_price,
        major_levels=major_levels,
        sweep=sweep,
        candles_d1=candles_d1,
        candles_w1=candles_w1,
        order_flow=order_flow,
    )


def analyze_sol(
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
    return analyze(
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,
        current_price=current_price,
        major_levels=major_levels,
        sweep=sweep,
        candles_d1=candles_d1,
        candles_w1=candles_w1,
        order_flow=order_flow,
    )


__all__ = [
    "STRATEGY_VERSION",
    "analyze",
    "analyze_symbol",
    "analyze_sol",
    "structure_context",
    "context_d1",
    "context_w1",
    "context_1h",
    "direction_from_context",
    "find_fvgs",
    "imbalance_context",
    "confirm_15m",
    "calculate_recovery",
    "confirm_5m_ilm",
    "find_structural_target",
    "build_trade",
]