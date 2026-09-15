"""
TradeMind 5.8.2 — Strategy
D1 → 1H → Major Liquidity → Sweep → 15M → 5M ILM → Entry / SL / TP

Reference market:
Binance Spot

Execution:
BingX Futures (signals only)

Core doctrine:
- D1 defines direction.
- W1 fallback when D1 is neutral.
- 1H must synchronize with higher timeframe.
- Major liquidity only.
- Sweep is mandatory.
- 15M confirmation is mandatory.
- 5M ILM trigger is mandatory.
- Exactly one TP.
- Required RR = 1:2.
- No entry in the middle.
"""

from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, List


# ============================================================
# VERSION
# ============================================================

STRATEGY_VERSION = "5.8.2"


# ============================================================
# SETTINGS
# ============================================================

MIN_SCORE_READY = 80

MAX_ENTRY_DISTANCE_PCT = 0.75

MIN_RECOVERY_RATIO = 1 / 3

MIN_REVERSAL_BODY_RATIO = 0.35

MAX_SWEEP_AGE_1H = 3

MIN_SWEEP_DEPTH_PCT = 0.08

MAX_15M_CONFIRM_AGE = 8

MIN_15M_BODY_RATIO = 0.35

MAX_5M_TRIGGER_AGE = 3

MIN_5M_BODY_RATIO = 0.40

STRUCTURE_LOOKBACK = 40

SL_BUFFER_PCT = 0.10

REQUIRED_RR = 2.0

MIN_TP_DISTANCE_PCT = 0.10


# ============================================================
# DATA STRUCTURE
# ============================================================

@dataclass
class Setup:
    symbol: str

    direction: Optional[str] = None

    stage: str = "NONE"

    score: int = 0

    reason: str = ""

    d1_trend: str = "NEUTRAL"
    w1_trend: str = "NEUTRAL"
    h1_trend: str = "NEUTRAL"

    major_bsl: Optional[float] = None
    major_ssl: Optional[float] = None

    sweep: Optional[Dict[str, Any]] = None

    confirmation_15m: Optional[Dict[str, Any]] = None

    trigger_5m: Optional[Dict[str, Any]] = None

    entry: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None

    rr: Optional[float] = None

    imbalance: Optional[Dict[str, Any]] = None

    diagnostics: Optional[Dict[str, Any]] = None

    ready: bool = False

    def to_dict(self):
        return asdict(self)


# ============================================================
# GENERIC HELPERS
# ============================================================

def _num(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _get(candle, key, default=0.0):
    if isinstance(candle, dict):
        return candle.get(key, default)

    try:
        return getattr(candle, key)
    except Exception:
        return default


def candle_open(c):
    return _num(_get(c, "open"))


def candle_high(c):
    return _num(_get(c, "high"))


def candle_low(c):
    return _num(_get(c, "low"))


def candle_close(c):
    return _num(_get(c, "close"))


def candle_body(c):
    return abs(candle_close(c) - candle_open(c))


def candle_range(c):
    return max(candle_high(c) - candle_low(c), 0.00000001)


def candle_body_ratio(c):
    return candle_body(c) / candle_range(c)


def bullish(c):
    return candle_close(c) > candle_open(c)


def bearish(c):
    return candle_close(c) < candle_open(c)


def pct_distance(a, b):
    if not a or not b:
        return 999.0

    return abs(a - b) / b * 100.0


# ============================================================
# STRUCTURE
# ============================================================

def _local_highs(candles):
    highs = []

    if len(candles) < 3:
        return highs

    for i in range(1, len(candles) - 1):
        left = candle_high(candles[i - 1])
        mid = candle_high(candles[i])
        right = candle_high(candles[i + 1])

        if mid > left and mid > right:
            highs.append({
                "index": i,
                "price": mid
            })

    return highs


def _local_lows(candles):
    lows = []

    if len(candles) < 3:
        return lows

    for i in range(1, len(candles) - 1):
        left = candle_low(candles[i - 1])
        mid = candle_low(candles[i])
        right = candle_low(candles[i + 1])

        if mid < left and mid < right:
            lows.append({
                "index": i,
                "price": mid
            })

    return lows


def structure_context(candles) -> str:
    """
    Determine market structure using recent swing highs/lows.

    HH + HL = BULLISH
    LH + LL = BEARISH
    Otherwise = NEUTRAL
    """

    if not candles:
        return "NEUTRAL"

    candles = list(candles)[-STRUCTURE_LOOKBACK:]

    highs = _local_highs(candles)
    lows = _local_lows(candles)

    if len(highs) < 2 or len(lows) < 2:
        return "NEUTRAL"

    h1 = highs[-2]["price"]
    h2 = highs[-1]["price"]

    l1 = lows[-2]["price"]
    l2 = lows[-1]["price"]

    higher_high = h2 > h1
    higher_low = l2 > l1

    lower_high = h2 < h1
    lower_low = l2 < l1

    if higher_high and higher_low:
        return "BULLISH"

    if lower_high and lower_low:
        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# HIGHER TIMEFRAME CONTEXT
# ============================================================

def context_d1(candles) -> str:
    return structure_context(candles)


def context_w1(candles) -> str:
    return structure_context(candles)


def context_1h(candles) -> str:
    return structure_context(candles)


def direction_from_context(
    d1: str,
    h1: str,
    w1: str = "NEUTRAL"
) -> Optional[str]:

    # D1 has priority.
    if d1 == "BULLISH":
        if h1 == "BULLISH":
            return "LONG"
        return None

    if d1 == "BEARISH":
        if h1 == "BEARISH":
            return "SHORT"
        return None

    # W1 fallback only when D1 is neutral.
    if d1 == "NEUTRAL":

        if w1 == "BULLISH" and h1 == "BULLISH":
            return "LONG"

        if w1 == "BEARISH" and h1 == "BEARISH":
            return "SHORT"

    return None


# ============================================================
# FVG / IMBALANCE
# ============================================================

def find_fvgs(candles) -> List[Dict[str, Any]]:
    """
    Basic 3-candle FVG detection.

    Bullish FVG:
        candle[i].high < candle[i+2].low

    Bearish FVG:
        candle[i].low > candle[i+2].high
    """

    fvgs = []

    if len(candles) < 3:
        return fvgs

    for i in range(len(candles) - 2):

        c1 = candles[i]
        c3 = candles[i + 2]

        c1_high = candle_high(c1)
        c1_low = candle_low(c1)

        c3_high = candle_high(c3)
        c3_low = candle_low(c3)

        if c1_high < c3_low:
            fvgs.append({
                "type": "BULLISH",
                "index": i + 1,
                "low": c1_high,
                "high": c3_low
            })

        elif c1_low > c3_high:
            fvgs.append({
                "type": "BEARISH",
                "index": i + 1,
                "low": c3_high,
                "high": c1_low
            })

    return fvgs


def imbalance_context(candles, direction):
    fvgs = find_fvgs(candles)

    if not fvgs:
        return {
            "found": False,
            "status": "NONE",
            "fvg": None
        }

    recent = fvgs[-1]

    if direction == "LONG":

        if recent["type"] == "BULLISH":
            return {
                "found": True,
                "status": "RESPECTED",
                "fvg": recent
            }

        if recent["type"] == "BEARISH":
            return {
                "found": True,
                "status": "CAUTION",
                "fvg": recent
            }

    if direction == "SHORT":

        if recent["type"] == "BEARISH":
            return {
                "found": True,
                "status": "RESPECTED",
                "fvg": recent
            }

        if recent["type"] == "BULLISH":
            return {
                "found": True,
                "status": "CAUTION",
                "fvg": recent
            }

    return {
        "found": False,
        "status": "NONE",
        "fvg": None
    }


# ============================================================
# 15M CONFIRMATION
# ============================================================

def confirm_15m(
    candles,
    direction,
    sweep=None
) -> Dict[str, Any]:

    if not candles:
        return {
            "confirmed": False,
            "reason": "Нет 15M свечей."
        }

    candles = list(candles)

    recent = candles[-1]

    body_ratio = candle_body_ratio(recent)

    if body_ratio < MIN_15M_BODY_RATIO:
        return {
            "confirmed": False,
            "reason": "15M candle body слишком слабый.",
            "body_ratio": round(body_ratio, 3)
        }

    if direction == "LONG":

        if bullish(recent):
            return {
                "confirmed": True,
                "direction": "LONG",
                "index": len(candles) - 1,
                "body_ratio": round(body_ratio, 3),
                "reason": "15M bullish confirmation."
            }

        return {
            "confirmed": False,
            "direction": "LONG",
            "body_ratio": round(body_ratio, 3),
            "reason": "Нет 15M bullish confirmation."
        }

    if direction == "SHORT":

        if bearish(recent):
            return {
                "confirmed": True,
                "direction": "SHORT",
                "index": len(candles) - 1,
                "body_ratio": round(body_ratio, 3),
                "reason": "15M bearish confirmation."
            }

        return {
            "confirmed": False,
            "direction": "SHORT",
            "body_ratio": round(body_ratio, 3),
            "reason": "Нет 15M bearish confirmation."
        }

    return {
        "confirmed": False,
        "reason": "Direction undefined."
    }


# ============================================================
# RECOVERY
# ============================================================

def calculate_recovery(
    candles,
    direction
) -> float:

    if not candles:
        return 0.0

    candles = list(candles)

    if len(candles) < 2:
        return 0.0

    c = candles[-1]
    prev = candles[-2]

    if direction == "LONG":

        manipulation_low = candle_low(prev)

        recovery_high = candle_high(c)

        total_range = candle_range(prev)

        if total_range <= 0:
            return 0.0

        recovery = recovery_high - manipulation_low

        return max(0.0, recovery / total_range)

    if direction == "SHORT":

        manipulation_high = candle_high(prev)

        recovery_low = candle_low(c)

        total_range = candle_range(prev)

        if total_range <= 0:
            return 0.0

        recovery = manipulation_high - recovery_low

        return max(0.0, recovery / total_range)

    return 0.0


# ============================================================
# 5M ILM
# ============================================================

def confirm_5m_ilm(
    candles,
    direction
) -> Dict[str, Any]:

    if not candles or len(candles) < 3:
        return {
            "confirmed": False,
            "reason": "Недостаточно 5M свечей."
        }

    candles = list(candles)

    prev = candles[-2]
    current = candles[-1]

    recovery = calculate_recovery(candles, direction)

    body_ratio = candle_body_ratio(current)

    if recovery < MIN_RECOVERY_RATIO:
        return {
            "confirmed": False,
            "recovery": round(recovery, 3),
            "body_ratio": round(body_ratio, 3),
            "reason": "Недостаточное восстановление после manipulation."
        }

    if body_ratio < MIN_5M_BODY_RATIO:
        return {
            "confirmed": False,
            "recovery": round(recovery, 3),
            "body_ratio": round(body_ratio, 3),
            "reason": "5M body слишком слабый."
        }

    if direction == "LONG":

        if bullish(current) and candle_close(current) > candle_open(prev):
            return {
                "confirmed": True,
                "direction": "LONG",
                "pattern": "V",
                "recovery": round(recovery, 3),
                "body_ratio": round(body_ratio, 3),
                "index": len(candles) - 1,
                "reason": "5M bullish ILM trigger."
            }

    if direction == "SHORT":

        if bearish(current) and candle_close(current) < candle_open(prev):
            return {
                "confirmed": True,
                "direction": "SHORT",
                "pattern": "L",
                "recovery": round(recovery, 3),
                "body_ratio": round(body_ratio, 3),
                "index": len(candles) - 1,
                "reason": "5M bearish ILM trigger."
            }

    return {
        "confirmed": False,
        "direction": direction,
        "recovery": round(recovery, 3),
        "body_ratio": round(body_ratio, 3),
        "reason": "Нет чистого 5M ILM trigger."
    }


# ============================================================
# STRUCTURAL TARGET
# ============================================================

def find_structural_target(
    candles,
    direction,
    entry
) -> Optional[float]:

    if not candles:
        return None

    candles = list(candles)

    highs = _local_highs(candles)
    lows = _local_lows(candles)

    if direction == "LONG":

        candidates = [
            x["price"]
            for x in highs
            if x["price"] > entry
        ]

        if not candidates:
            return None

        return min(candidates)

    if direction == "SHORT":

        candidates = [
            x["price"]
            for x in lows
            if x["price"] < entry
        ]

        if not candidates:
            return None

        return max(candidates)

    return None


# ============================================================
# TRADE BUILD
# ============================================================

def build_trade(
    direction,
    entry,
    sweep_level,
    structural_target=None
):

    entry = _num(entry)
    sweep_level = _num(sweep_level)

    if entry <= 0 or sweep_level <= 0:
        return None

    if direction == "LONG":

        sl = sweep_level * (1 - SL_BUFFER_PCT / 100)

        risk = entry - sl

        if risk <= 0:
            return None

        tp = entry + risk * REQUIRED_RR

        if structural_target is not None:

            if structural_target <= entry:
                return None

            # Structural target must allow at least 1:2.
            if structural_target < tp:
                return None

            # Exactly 1:2.
            tp = entry + risk * REQUIRED_RR

        if pct_distance(tp, entry) < MIN_TP_DISTANCE_PCT:
            return None

        rr = (tp - entry) / risk

        return {
            "direction": "LONG",
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "rr": rr
        }

    if direction == "SHORT":

        sl = sweep_level * (1 + SL_BUFFER_PCT / 100)

        risk = sl - entry

        if risk <= 0:
            return None

        tp = entry - risk * REQUIRED_RR

        if structural_target is not None:

            if structural_target >= entry:
                return None

            # Structural target must allow at least 1:2.
            if structural_target > tp:
                return None

            # Exactly 1:2.
            tp = entry - risk * REQUIRED_RR

        if pct_distance(tp, entry) < MIN_TP_DISTANCE_PCT:
            return None

        rr = (entry - tp) / risk

        return {
            "direction": "SHORT",
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "rr": rr
        }

    return None


# ============================================================
# DIAGNOSTIC INFORMATION
# ============================================================

def build_diagnostics(
    d1,
    w1,
    h1,
    direction,
    major_bsl=None,
    major_ssl=None,
    sweep=None,
    confirmation_15m=None,
    trigger_5m=None
):

    return {
        "d1": d1,
        "w1": w1,
        "h1": h1,
        "direction": direction,

        "major_liquidity": {
            "BSL": major_bsl,
            "SSL": major_ssl
        },

        "sweep": sweep,

        "15m_confirmation": confirmation_15m,

        "5m_trigger": trigger_5m,

        "pipeline": [
            "D1",
            "1H",
            "Major Liquidity",
            "Sweep",
            "15M Confirmation",
            "5M ILM",
            "Entry",
            "SL",
            "TP 1:2"
        ]
    }


# ============================================================
# MAIN ANALYSIS
# ============================================================

def analyze(
    symbol: str,
    d1_candles,
    h1_candles,
    m15_candles,
    m5_candles,
    price: Optional[float] = None,
    major_bsl: Optional[float] = None,
    major_ssl: Optional[float] = None,
    sweep: Optional[Dict[str, Any]] = None,
    order_flow: Optional[Dict[str, Any]] = None
):

    result = Setup(symbol=symbol)

    # --------------------------------------------------------
    # PRICE
    # --------------------------------------------------------

    if price is None:

        if m5_candles:
            price = candle_close(m5_candles[-1])

        elif m15_candles:
            price = candle_close(m15_candles[-1])

        elif h1_candles:
            price = candle_close(h1_candles[-1])

        else:
            price = 0.0

    price = _num(price)

    # --------------------------------------------------------
    # D1 / W1 / 1H
    # --------------------------------------------------------

    d1 = context_d1(d1_candles)

    # W1 is only fallback context.
    w1 = "NEUTRAL"

    h1 = context_1h(h1_candles)

    result.d1_trend = d1
    result.w1_trend = w1
    result.h1_trend = h1

    direction = direction_from_context(
        d1=d1,
        h1=h1,
        w1=w1
    )

    result.direction = direction

    # --------------------------------------------------------
    # DIAGNOSTIC
    # --------------------------------------------------------

    result.diagnostics = build_diagnostics(
        d1=d1,
        w1=w1,
        h1=h1,
        direction=direction,
        major_bsl=major_bsl,
        major_ssl=major_ssl,
        sweep=sweep
    )

    # --------------------------------------------------------
    # NO DIRECTION
    # --------------------------------------------------------

    if direction is None:

        result.stage = "1H"
        result.score = 35

        result.reason = (
            f"D1={d1}, 1H={h1} → "
            "тренды не синхронизированы."
        )

        result.ready = False

        return result.to_dict()

    # --------------------------------------------------------
    # MAJOR LIQUIDITY
    # --------------------------------------------------------

    result.major_bsl = major_bsl
    result.major_ssl = major_ssl

    if direction == "LONG":

        if major_ssl is None:

            result.stage = "LIQUIDITY"
            result.score = 45

            result.reason = (
                "LONG: ждём Major SSL ниже цены "
                "для sweep."
            )

            return result.to_dict()

    if direction == "SHORT":

        if major_bsl is None:

            result.stage = "LIQUIDITY"
            result.score = 45

            result.reason = (
                "SHORT: ждём Major BSL выше цены "
                "для sweep."
            )

            return result.to_dict()

    # --------------------------------------------------------
    # SWEEP
    # --------------------------------------------------------

    if not sweep:

        result.stage = "SWEEP"
        result.score = 45

        result.reason = (
            f"{direction}: ждём Major Liquidity Sweep."
        )

        return result.to_dict()

    result.sweep = sweep

    sweep_direction = sweep.get("direction")

    # Expected sweep:
    # LONG  → SSL
    # SHORT → BSL

    expected_sweep = "SSL" if direction == "LONG" else "BSL"

    if sweep_direction and sweep_direction != expected_sweep:

        result.stage = "SWEEP"
        result.score = 40

        result.reason = (
            f"Неверное направление sweep: "
            f"{sweep_direction}, ожидался {expected_sweep}."
        )

        return result.to_dict()

    # --------------------------------------------------------
    # SWEEP DEPTH
    # --------------------------------------------------------

    sweep_price = _num(
        sweep.get("level")
        or sweep.get("price")
        or sweep.get("sweep_level")
    )

    if sweep_price > 0 and price > 0:

        depth = pct_distance(price, sweep_price)

        if depth > MAX_ENTRY_DISTANCE_PCT:

            result.stage = "SWEEP"
            result.score = 55

            result.reason = (
                f"Цена слишком далеко от sweep "
                f"({depth:.2f}%). Не догоняем."
            )

            return result.to_dict()

    # --------------------------------------------------------
    # 15M
    # --------------------------------------------------------

    confirmation = confirm_15m(
        m15_candles,
        direction,
        sweep
    )

    result.confirmation_15m = confirmation

    result.diagnostics["15m_confirmation"] = confirmation

    if not confirmation.get("confirmed"):

        result.stage = "15M"
        result.score = 65

        result.reason = (
            "Sweep есть → ждём 15M confirmation."
        )

        return result.to_dict()

    # --------------------------------------------------------
    # 5M ILM
    # --------------------------------------------------------

    trigger = confirm_5m_ilm(
        m5_candles,
        direction
    )

    result.trigger_5m = trigger

    result.diagnostics["5m_trigger"] = trigger

    if not trigger.get("confirmed"):

        result.stage = "5M"
        result.score = 72

        result.reason = (
            "15M подтверждение есть → "
            "ждём 5M ILM trigger."
        )

        return result.to_dict()

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = 80

    # --------------------------------------------------------
    # IMBALANCE
    # --------------------------------------------------------

    imbalance = imbalance_context(
        h1_candles,
        direction
    )

    result.imbalance = imbalance

    if imbalance.get("status") == "RESPECTED":
        score += 8

    elif imbalance.get("status") == "CAUTION":
        score -= 5

    # --------------------------------------------------------
    # SWEEP QUALITY
    # --------------------------------------------------------

    sweep_depth = _num(
        sweep.get("depth_pct")
        or sweep.get("depth")
    )

    if sweep_depth >= 0.30:
        score += 7

    elif sweep_depth >= MIN_SWEEP_DEPTH_PCT:
        score += 5

    else:
        score -= 4

    # --------------------------------------------------------
    # RECOVERY
    # --------------------------------------------------------

    recovery = _num(
        trigger.get("recovery")
    )

    if recovery >= 0.66:
        score += 5

    elif recovery >= MIN_RECOVERY_RATIO:
        score += 2

    # --------------------------------------------------------
    # ORDER FLOW
    # --------------------------------------------------------

    if order_flow:

        of_confirmed = order_flow.get("confirmed")

        if of_confirmed:
            score += 5

    score = max(0, min(100, int(score)))

    result.score = score

    # --------------------------------------------------------
    # ENTRY
    # --------------------------------------------------------

    entry = price

    if entry <= 0:

        result.stage = "ENTRY"
        result.reason = "Не удалось определить цену entry."
        return result.to_dict()

    # --------------------------------------------------------
    # SWEEP LEVEL FOR SL
    # --------------------------------------------------------

    if direction == "LONG":

        sl_level = major_ssl

    else:

        sl_level = major_bsl

    if sl_level is None:

        result.stage = "SL"
        result.score = 70

        result.reason = (
            "Нет валидного liquidity level для SL."
        )

        return result.to_dict()

    # --------------------------------------------------------
    # STRUCTURAL TARGET
    # --------------------------------------------------------

    structural_target = find_structural_target(
        h1_candles,
        direction,
        entry
    )

    if structural_target is None:

        structural_target = find_structural_target(
            m15_candles,
            direction,
            entry
        )

    if structural_target is None:

        result.stage = "TARGET"
        result.score = 70

        result.reason = (
            "Нет валидной unswept structural target."
        )

        return result.to_dict()

    # --------------------------------------------------------
    # BUILD TRADE
    # --------------------------------------------------------

    trade = build_trade(
        direction=direction,
        entry=entry,
        sweep_level=sl_level,
        structural_target=structural_target
    )

    if trade is None:

        result.stage = "RR"
        result.score = 70

        result.reason = (
            "Не выполняется обязательная геометрия 1:2 "
            "до structural target."
        )

        return result.to_dict()

    # --------------------------------------------------------
    # FINAL SETUP
    # --------------------------------------------------------

    result.entry = trade["entry"]
    result.sl = trade["sl"]
    result.tp = trade["tp"]
    result.rr = trade["rr"]

    # --------------------------------------------------------
    # FINAL SCORE / READY
    # --------------------------------------------------------

    result.ready = score >= MIN_SCORE_READY

    if result.ready:

        result.stage = "READY"

        result.reason = (
            f"{direction}: полный TradeMind setup. "
            f"D1 → 1H → Sweep → 15M → 5M ILM → 1:2."
        )

    else:

        result.stage = "WAIT"

        result.reason = (
            "Setup сформирован, но score ниже "
            "минимального порога."
        )

    return result.to_dict()


# ============================================================
# SOL SHORTCUT
# ============================================================

def analyze_sol(
    d1_candles,
    h1_candles,
    m15_candles,
    m5_candles,
    price=None,
    major_bsl=None,
    major_ssl=None,
    sweep=None,
    order_flow=None
):

    return analyze(
        symbol="SOL",
        d1_candles=d1_candles,
        h1_candles=h1_candles,
        m15_candles=m15_candles,
        m5_candles=m5_candles,
        price=price,
        major_bsl=major_bsl,
        major_ssl=major_ssl,
        sweep=sweep,
        order_flow=order_flow
    )


# ============================================================
# GENERIC SYMBOL SHORTCUT
# ============================================================

def analyze_symbol(
    symbol,
    d1_candles,
    h1_candles,
    m15_candles,
    m5_candles,
    price=None,
    major_bsl=None,
    major_ssl=None,
    sweep=None,
    order_flow=None
):

    return analyze(
        symbol=symbol,
        d1_candles=d1_candles,
        h1_candles=h1_candles,
        m15_candles=m15_candles,
        m5_candles=m5_candles,
        price=price,
        major_bsl=major_bsl,
        major_ssl=major_ssl,
        sweep=sweep,
        order_flow=order_flow
    )


# ============================================================
# SCORE LABEL
# ============================================================

def score_label(score: int) -> str:

    score = int(score)

    if score >= 90:
        return "A+"

    if score >= 80:
        return "TRADE"

    if score >= 70:
        return "WAIT"

    return "NO TRADE"


# ============================================================
# PUBLIC STATUS
# ============================================================

def strategy_status(result: Dict[str, Any]) -> Dict[str, Any]:

    score = int(result.get("score", 0))

    return {
        "strategy_version": STRATEGY_VERSION,
        "symbol": result.get("symbol"),
        "direction": result.get("direction"),
        "stage": result.get("stage"),
        "score": score,
        "label": score_label(score),
        "ready": bool(result.get("ready")),
        "entry": result.get("entry"),
        "sl": result.get("sl"),
        "tp": result.get("tp"),
        "rr": result.get("rr"),
        "reason": result.get("reason"),
        "diagnostics": result.get("diagnostics")
    }


# ============================================================
# END
# ============================================================