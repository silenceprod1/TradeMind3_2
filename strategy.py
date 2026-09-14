"""
TradeMind 4.4.3 strategy engine.

1H context
    ↓
Major Liquidity
    ↓
Fresh 5M Major Liquidity Sweep
    ↓
15M Confirmation AFTER Sweep
    ↓
FIRST 3 REAL 5M CANDLES AFTER 15M CONFIRMATION
    ↓
5M Trigger
    ↓
Entry MUST remain close to Sweep
    ↓
SL behind actual Sweep extreme
    ↓
ONE TP at EXACTLY 1:2

TradeMind 4.4.3 FIXES:
- 5M trigger is allowed ONLY inside the first 3 real 5M candles
  after the confirmed 15M candle.
- We do NOT filter candles first and then count them.
- Previous candle for trigger validation is the ACTUAL previous 5M candle.
- Late trigger = NO TRADE.
- Old sweep = NO TRADE when timestamp is available.
- Entry too far from sweep = NO TRADE.
- TP is ALWAYS calculated from actual Entry/SL risk.
- Exactly ONE TP.
- If exact 1:2 TP hits major opposing liquidity, setup is rejected.
"""

from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone


# ============================================================
# VERSION
# ============================================================

STRATEGY_VERSION = "4.4.3"


# ============================================================
# CORE SETTINGS
# ============================================================

RR_TARGET = 2.0

MIN_SCORE_READY = 80

# Entry / trigger must stay close to sweep.
MAX_ENTRY_DISTANCE_PCT = 0.50

# Sweep freshness:
# maximum age = 6 x 5M candles = 30 minutes.
MAX_SWEEP_AGE_CANDLES = 6

# CRITICAL:
# trigger allowed only in FIRST 3 REAL 5M candles
# after 15M confirmation.
MAX_TRIGGER_CANDLES_AFTER_CONFIRMATION = 3

# SL buffer behind actual sweep extreme.
SL_BUFFER_PCT = 0.10


# ============================================================
# RESULT OBJECT
# ============================================================

@dataclass
class Setup:
    status: str = "WAIT"
    stage: str = "WAIT"

    direction: Optional[str] = None
    score: int = 0
    reason: str = ""

    zone_low: Optional[float] = None
    zone_high: Optional[float] = None

    entry: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None

    rr: float = RR_TARGET
    one_tp: bool = True

    liquidity_type: Optional[str] = None

    confirmation_15m: Optional[str] = None
    confirmation: Optional[str] = None

    order_flow: Optional[str] = None

    sweep_extreme: Optional[float] = None

    sweep_time: Optional[Any] = None
    confirmation_15m_time: Optional[Any] = None
    trigger_5m_time: Optional[Any] = None

    trigger_age_5m: Optional[int] = None
    sweep_age_5m: Optional[int] = None

    entry_distance_from_sweep_pct: Optional[float] = None

    def to_dict(self):
        return asdict(self)


# ============================================================
# BASIC HELPERS
# ============================================================

def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def val(c, key, idx=None):
    if isinstance(c, dict):
        return f(c.get(key, c.get(key[0], None)))

    if idx is not None and isinstance(c, (list, tuple)):
        if len(c) > idx:
            return f(c[idx])

    return None


def o(c):
    return val(c, "open", 1)


def h(c):
    return val(c, "high", 2)


def l(c):
    return val(c, "low", 3)


def cl(c):
    return val(c, "close", 4)


def last(c, n):
    return c[-n:] if len(c) >= n else c


# ============================================================
# TIMESTAMP HELPERS
# ============================================================

def candle_time(c):
    """
    Robust timestamp extraction.

    Supports common dict keys:
    timestamp, time, open_time, openTime, ts, t

    Also supports Binance-style list:
    [open_time, open, high, low, close, ...]
    """

    if c is None:
        return None

    raw = None

    if isinstance(c, dict):
        for key in (
            "timestamp",
            "time",
            "open_time",
            "openTime",
            "ts",
            "t",
        ):
            if key in c:
                raw = c.get(key)
                break

    elif isinstance(c, (list, tuple)):
        if len(c) > 0:
            raw = c[0]

    if raw is None:
        return None

    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = None

    if value is not None:
        # milliseconds
        if value > 10_000_000_000:
            return value / 1000.0

        # seconds
        return value

    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            raw = raw.replace(tzinfo=timezone.utc)

        return raw.timestamp()

    if isinstance(raw, str):
        text = raw.strip()

        try:
            dt = datetime.fromisoformat(
                text.replace("Z", "+00:00")
            )

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

            return dt.timestamp()

        except Exception:
            return None

    return None


def sweep_timestamp(sweep):
    if not isinstance(sweep, dict):
        return None

    for key in (
        "timestamp",
        "time",
        "open_time",
        "openTime",
        "ts",
        "t",
        "sweep_time",
        "sweep_timestamp",
    ):
        if key in sweep:
            value = candle_time({key: sweep.get(key)})
            if value is not None:
                return value

    return None


def timestamp_index(candles):
    """
    Return indexes with valid timestamps.
    """
    result = []

    for i, c in enumerate(candles):
        t = candle_time(c)

        if t is not None:
            result.append((i, t))

    return result


# ============================================================
# CONTEXT
# ============================================================

def context_1h(candles):
    d = last(candles, 24)

    if len(d) < 8:
        return "neutral"

    hs = [h(x) for x in d]
    ls = [l(x) for x in d]

    if any(x is None for x in hs + ls):
        return "neutral"

    mid = len(d) // 2

    if (
        max(hs[mid:]) > max(hs[:mid])
        and min(ls[mid:]) > min(ls[:mid])
    ):
        return "bullish"

    if (
        max(hs[mid:]) < max(hs[:mid])
        and min(ls[mid:]) < min(ls[:mid])
    ):
        return "bearish"

    return "neutral"


def context_15m(candles):
    d = last(candles, 8)

    if len(d) < 4:
        return "neutral"

    hs = [h(x) for x in d]
    ls = [l(x) for x in d]

    if any(x is None for x in hs + ls):
        return "neutral"

    m = len(d) // 2

    if (
        max(hs[m:]) > max(hs[:m])
        and min(ls[m:]) > min(ls[:m])
    ):
        return "bullish"

    if (
        max(hs[m:]) < max(hs[:m])
        and min(ls[m:]) < min(ls[:m])
    ):
        return "bearish"

    return "neutral"


# ============================================================
# 15M CONFIRMATION
# ============================================================

def confirm_15m(
    candles,
    direction,
    sweep_level,
    sweep_time=None,
):
    """
    Find the FIRST valid 15M confirmation AFTER the sweep.

    Returns:
        ok,
        confirmation_text,
        confirmation_candle,
        confirmation_index
    """

    if not candles or sweep_level is None:
        return False, None, None, None

    candidates = []

    for i, candle in enumerate(candles):

        t = candle_time(candle)

        # If both timestamps exist, confirmation MUST be after sweep.
        if sweep_time is not None and t is not None:
            if t <= sweep_time:
                continue

        candidates.append((i, candle))

    if not candidates:
        return False, None, None, None

    # We only need the latest 8 candidates.
    candidates = candidates[-8:]

    for i, candle in candidates:

        O = o(candle)
        H = h(candle)
        L = l(candle)
        C = cl(candle)

        if None in (O, H, L, C):
            continue

        rng = max(H - L, 1e-9)
        body = abs(C - O)

        # Previous actual 15M candle.
        if i <= 0:
            continue

        prev = candles[i - 1]

        prev_high = h(prev)
        prev_low = l(prev)

        if prev_high is None or prev_low is None:
            continue

        if direction == "SHORT":

            bearish_break = (
                C < prev_low
                and C < O
                and body / rng >= 0.35
            )

            bearish_reclaim = (
                C < sweep_level
                and C < O
            )

            rejection = (
                H > sweep_level
                and C < sweep_level
                and C < H - rng * 0.55
            )

            if bearish_break:
                return (
                    True,
                    "15M bearish structure break after upside sweep",
                    candle,
                    i,
                )

            if bearish_reclaim and rejection:
                return (
                    True,
                    "15M bearish rejection/reclaim after upside sweep",
                    candle,
                    i,
                )

            if bearish_reclaim:
                return (
                    True,
                    "15M close back below swept liquidity",
                    candle,
                    i,
                )

        else:

            bullish_break = (
                C > prev_high
                and C > O
                and body / rng >= 0.35
            )

            bullish_reclaim = (
                C > sweep_level
                and C > O
            )

            rejection = (
                L < sweep_level
                and C > sweep_level
                and C > L + rng * 0.55
            )

            if bullish_break:
                return (
                    True,
                    "15M bullish structure break after downside sweep",
                    candle,
                    i,
                )

            if bullish_reclaim and rejection:
                return (
                    True,
                    "15M bullish rejection/reclaim after downside sweep",
                    candle,
                    i,
                )

            if bullish_reclaim:
                return (
                    True,
                    "15M close back above swept liquidity",
                    candle,
                    i,
                )

    return False, None, None, None


# ============================================================
# 5M TRIGGER
# ============================================================

def trigger_on_candle(candles, index, direction, sweep_level):
    """
    Validate ONE exact 5M candle.

    IMPORTANT:
    Previous candle = actual previous 5M candle,
    not previous FILTERED candidate.
    """

    if index <= 0 or index >= len(candles):
        return False, None

    current = candles[index]
    previous = candles[index - 1]

    O = o(current)
    H = h(current)
    L = l(current)
    C = cl(current)

    pH = h(previous)
    pL = l(previous)
    pC = cl(previous)

    if None in (O, H, L, C, pH, pL, pC):
        return False, None

    rng = max(H - L, 1e-9)
    body = abs(C - O)

    if direction == "LONG":

        displacement = (
            C > O
            and C > pC
            and C > pH
            and C > sweep_level
            and body / rng >= 0.45
        )

        rejection = (
            C > O
            and L < pL
            and C > sweep_level
            and C > L + rng * 0.55
        )

        if displacement:
            return True, "5M bullish displacement / micro-structure break"

        if rejection:
            return True, "5M bullish rejection after downside sweep"

    else:

        displacement = (
            C < O
            and C < pC
            and C < pL
            and C < sweep_level
            and body / rng >= 0.45
        )

        rejection = (
            C < O
            and H > pH
            and C < sweep_level
            and C < H - rng * 0.55
        )

        if displacement:
            return True, "5M bearish displacement / micro-structure break"

        if rejection:
            return True, "5M bearish rejection after upside sweep"

    return False, None


def confirm_5m_after_15m(
    candles_5m,
    direction,
    sweep_level,
    confirmation_time,
):
    """
    CRITICAL 4.4.3 FIX.

    We identify the FIRST REAL 5M candle after the 15M
    confirmation candle.

    Then we inspect ONLY:
        candle #1
        candle #2
        candle #3

    No filtering before counting.

    Therefore:
        4th candle = NO TRADE
        5th candle = NO TRADE
        10th candle = NO TRADE

    This prevents late/chasing entries.
    """

    if not candles_5m:
        return False, None, None, None, None

    # --------------------------------------------------------
    # Timestamp mode
    # --------------------------------------------------------

    if confirmation_time is not None:

        after = []

        for i, candle in enumerate(candles_5m):

            t = candle_time(candle)

            if t is None:
                continue

            if t > confirmation_time:
                after.append((i, candle, t))

        # Sort chronologically.
        after.sort(key=lambda x: x[2])

        # ONLY FIRST 3 REAL CANDLES.
        first_three = after[:MAX_TRIGGER_CANDLES_AFTER_CONFIRMATION]

        for age, (i, candle, t) in enumerate(first_three, start=1):

            # Entry/trigger must remain close to sweep.
            close_price = cl(candle)

            if close_price is None:
                continue

            distance_pct = (
                abs(close_price - sweep_level)
                / max(abs(sweep_level), 1e-9)
                * 100
            )

            if distance_pct > MAX_ENTRY_DISTANCE_PCT:
                # Do NOT skip to a later candle and pretend it is candle #1.
                # It remains candle #1/#2/#3 and therefore the window stays strict.
                continue

            ok, reason = trigger_on_candle(
                candles_5m,
                i,
                direction,
                sweep_level,
            )

            if ok:
                return True, reason, candle, i, age

        return False, None, None, None, None

    # --------------------------------------------------------
    # SAFE FALLBACK
    # --------------------------------------------------------
    #
    # If timestamp information is unavailable we do NOT want
    # to accept an unknown-age trigger.
    #
    # Instead of generating a potentially late trade, reject.
    #

    return (
        False,
        "Нет timestamp 15M/5M → возраст trigger невозможно проверить.",
        None,
        None,
        None,
    )


# ============================================================
# ORDER FLOW
# ============================================================

def flow_check(flow, direction):

    if not flow:
        return None, None

    a = str(flow.get("absorption", "")).lower()

    d = f(flow.get("delta"))
    cv = f(flow.get("cvd_change"))

    if direction == "LONG":

        if a in {"buyers", "buyer", "buy"}:
            return True, "buyer absorption"

        if d is not None and d > 0:
            return True, "positive delta"

        if cv is not None and cv > 0:
            return True, "rising CVD"

    else:

        if a in {"sellers", "seller", "sell"}:
            return True, "seller absorption"

        if d is not None and d < 0:
            return True, "negative delta"

        if cv is not None and cv < 0:
            return True, "falling CVD"

    return False, "order flow does not support direction"


# ============================================================
# TRADE CALCULATION
# ============================================================

def trade(direction, entry, sl):
    """
    ONE TP.
    EXACTLY 1:2.
    """

    entry = f(entry)
    sl = f(sl)

    if entry is None or sl is None:
        return None, None, None

    if direction == "LONG" and sl >= entry:
        return None, None, None

    if direction == "SHORT" and sl <= entry:
        return None, None, None

    risk = abs(entry - sl)

    if risk <= 0:
        return None, None, None

    if direction == "LONG":
        tp = entry + RR_TARGET * risk
    else:
        tp = entry - RR_TARGET * risk

    return (
        round(entry, 6),
        round(sl, 6),
        round(tp, 6),
    )


# ============================================================
# SWEEP EXTREME
# ============================================================

def _sweep_extreme(candles_5m, direction, level):

    if not candles_5m or level is None:
        return None

    recent = last(candles_5m, 12)

    if direction == "SHORT":

        swept = [
            h(x)
            for x in recent
            if h(x) is not None and h(x) > level
        ]

        return max(swept) if swept else level

    swept = [
        l(x)
        for x in recent
        if l(x) is not None and l(x) < level
    ]

    return min(swept) if swept else level


# ============================================================
# OPPOSITE MAJOR LIQUIDITY
# ============================================================

def _nearest_opposite_level(
    major_levels,
    direction,
    entry,
):
    """
    Find nearest untouched major liquidity on TP side.
    """

    if not major_levels or entry is None:
        return None

    candidates = []

    for z in major_levels:

        if not isinstance(z, dict):
            continue

        level = f(
            z.get(
                "price",
                z.get("level")
            )
        )

        if level is None:
            continue

        side = str(
            z.get("side", "")
        ).upper()

        if direction == "LONG":

            if level > entry and side != "LONG":
                candidates.append(level)

        else:

            if level < entry and side != "SHORT":
                candidates.append(level)

    if not candidates:
        return None

    if direction == "LONG":
        return min(candidates)

    return max(candidates)


# ============================================================
# SWEEP FRESHNESS
# ============================================================

def sweep_age_in_5m_candles(
    candles_5m,
    sweep_time,
):
    """
    Determine how many 5M candles old the sweep is.

    Returns None if timestamps are unavailable.
    """

    if sweep_time is None:
        return None

    indexed = timestamp_index(candles_5m)

    if not indexed:
        return None

    after = [
        (i, t)
        for i, t in indexed
        if t > sweep_time
    ]

    # If there are no candles after sweep, current data is stale.
    if not after:
        return None

    return len(after)


# ============================================================
# MAIN ANALYSIS
# ============================================================

def analyze(
    candles_1h: List[Any],
    candles_15m: List[Any],
    candles_5m: List[Any],
    current_price: float,
    major_levels=None,
    order_flow: Optional[Dict[str, Any]] = None,
    sweep: Optional[Dict[str, Any]] = None,
):

    p = f(current_price)

    r = Setup()

    if (
        p is None
        or not candles_1h
        or not candles_15m
        or not candles_5m
    ):
        r.reason = "Недостаточно рыночных данных."
        return r.to_dict()

    # --------------------------------------------------------
    # CONTEXT
    # --------------------------------------------------------

    ctx1h = context_1h(candles_1h)
    ctx15 = context_15m(candles_15m)

    # --------------------------------------------------------
    # SWEEP REQUIRED
    # --------------------------------------------------------

    if not sweep or not sweep.get("swept"):

        r.score = 25 if ctx15 == "neutral" else 35

        r.reason = (
            "Нет подтвержденного sweep крупной ликвидности. "
            "В середине диапазона не входим."
        )

        return r.to_dict()

    direction = str(
        sweep.get("direction", "")
    ).upper()

    level = f(
        sweep.get(
            "level",
            sweep.get("price")
        )
    )

    strength = f(
        sweep.get("strength")
    )

    if direction not in {"LONG", "SHORT"} or level is None:

        r.reason = (
            "Sweep не содержит корректного "
            "направления/уровня."
        )

        return r.to_dict()

    r.direction = direction

    r.liquidity_type = sweep.get(
        "liquidity_type",
        "major liquidity"
    )

    # --------------------------------------------------------
    # SWEEP STRENGTH
    # --------------------------------------------------------

    if strength is not None and strength < 0.60:

        r.score = 40

        r.reason = (
            "Sweep есть, но он недостаточно сильный."
        )

        return r.to_dict()

    # --------------------------------------------------------
    # SWEEP TIME
    # --------------------------------------------------------

    sw_time = sweep_timestamp(sweep)

    r.sweep_time = sw_time

    # --------------------------------------------------------
    # SWEEP AGE
    # --------------------------------------------------------

    if sw_time is not None:

        age = sweep_age_in_5m_candles(
            candles_5m,
            sw_time,
        )

        r.sweep_age_5m = age

        if age is None:

            r.score = 35

            r.reason = (
                "Невозможно подтвердить свежесть sweep "
                "по timestamp → вход запрещён."
            )

            return r.to_dict()

        if age > MAX_SWEEP_AGE_CANDLES:

            r.score = 35
            r.stage = "SWEPT"

            r.reason = (
                f"Sweep слишком старый: {age} x 5M. "
                f"Максимум {MAX_SWEEP_AGE_CANDLES} x 5M. "
                "Не догоняем."
            )

            return r.to_dict()

    # --------------------------------------------------------
    # ANTI-CHASE
    # --------------------------------------------------------

    distance_pct = (
        abs(p - level)
        / max(abs(level), 1e-9)
        * 100
    )

    r.entry_distance_from_sweep_pct = distance_pct

    if distance_pct > MAX_ENTRY_DISTANCE_PCT:

        r.score = 35
        r.stage = "SWEPT"

        r.reason = (
            f"После sweep цена ушла на "
            f"{distance_pct:.2f}% — больше лимита "
            f"{MAX_ENTRY_DISTANCE_PCT:.2f}%. "
            "Не догоняем."
        )

        return r.to_dict()

    # --------------------------------------------------------
    # 15M CONFIRMATION
    # --------------------------------------------------------

    (
        ok15,
        conf15,
        conf15_candle,
        conf15_index,
    ) = confirm_15m(
        candles_15m,
        direction,
        level,
        sw_time,
    )

    r.confirmation_15m = conf15

    if not ok15:

        r.score = 55
        r.stage = "SWEPT"

        r.reason = (
            "Sweep есть, но 15M confirmation "
            "после sweep отсутствует → вход запрещён."
        )

        return r.to_dict()

    conf15_time = candle_time(
        conf15_candle
    )

    r.confirmation_15m_time = conf15_time

    r.stage = "15M_CONFIRMED"

    # --------------------------------------------------------
    # 5M TRIGGER — STRICT FIRST 3 CANDLES
    # --------------------------------------------------------

    (
        ok5,
        conf5,
        trigger_candle,
        trigger_index,
        trigger_age,
    ) = confirm_5m_after_15m(
        candles_5m,
        direction,
        level,
        conf15_time,
    )

    r.confirmation = conf5

    r.trigger_5m_time = (
        candle_time(trigger_candle)
        if trigger_candle is not None
        else None
    )

    r.trigger_age_5m = trigger_age

    if not ok5:

        r.score = 65
        r.stage = "15M_CONFIRMED"

        if conf5 and "timestamp" in conf5.lower():

            r.reason = (
                "15M confirmation есть, но невозможно "
                "проверить строгий 3-свечный 5M trigger "
                "→ вход запрещён."
            )

        else:

            r.reason = (
                "15M confirmation есть, но "
                "5M trigger не появился в первых "
                f"{MAX_TRIGGER_CANDLES_AFTER_CONFIRMATION} "
                "реальных 5M свечах → NO TRADE."
            )

        return r.to_dict()

    # --------------------------------------------------------
    # TRIGGER DISTANCE
    # --------------------------------------------------------

    trigger_close = cl(trigger_candle)

    if trigger_close is None:

        r.score = 60

        r.reason = (
            "Некорректный 5M trigger → вход запрещён."
        )

        return r.to_dict()

    trigger_distance_pct = (
        abs(trigger_close - level)
        / max(abs(level), 1e-9)
        * 100
    )

    if trigger_distance_pct > MAX_ENTRY_DISTANCE_PCT:

        r.score = 60
        r.reason = (
            f"5M trigger слишком далеко от sweep: "
            f"{trigger_distance_pct:.2f}% → NO TRADE."
        )

        return r.to_dict()

    # --------------------------------------------------------
    # ORDER FLOW
    # --------------------------------------------------------

    fok, ft = flow_check(
        order_flow,
        direction,
    )

    r.order_flow = (
        ft if ft else "нет данных"
    )

    if fok is False:

        r.score = 68

        r.reason = (
            "5M trigger есть, но order flow "
            "не поддерживает направление."
        )

        return r.to_dict()

    # --------------------------------------------------------
    # SWEEP EXTREME
    # --------------------------------------------------------

    extreme = f(
        sweep.get(
            "extreme",
            sweep.get("sweep_extreme")
        )
    )

    if extreme is None:

        extreme = _sweep_extreme(
            candles_5m,
            direction,
            level,
        )

    if extreme is None:
        extreme = level

    r.sweep_extreme = extreme

    # --------------------------------------------------------
    # SL BEHIND ACTUAL SWEEP
    # --------------------------------------------------------

    if direction == "SHORT":

        sl = extreme * (
            1 + SL_BUFFER_PCT / 100
        )

    else:

        sl = extreme * (
            1 - SL_BUFFER_PCT / 100
        )

    # --------------------------------------------------------
    # ENTRY / EXACT 2R TP
    # --------------------------------------------------------

    entry = p

    entry, sl, tp = trade(
        direction,
        entry,
        sl,
    )

    if (
        entry is None
        or sl is None
        or tp is None
    ):

        r.score = 60

        r.reason = (
            "Невалидная геометрия Entry/SL "
            "→ вход запрещен."
        )

        return r.to_dict()

    # --------------------------------------------------------
    # FINAL ENTRY DISTANCE CHECK
    # --------------------------------------------------------

    final_entry_distance = (
        abs(entry - level)
        / max(abs(level), 1e-9)
        * 100
    )

    r.entry_distance_from_sweep_pct = (
        final_entry_distance
    )

    if final_entry_distance > MAX_ENTRY_DISTANCE_PCT:

        r.score = 60

        r.reason = (
            f"Entry слишком далеко от sweep: "
            f"{final_entry_distance:.2f}% "
            f"> {MAX_ENTRY_DISTANCE_PCT:.2f}% → NO TRADE."
        )

        return r.to_dict()

    # --------------------------------------------------------
    # OPPOSING MAJOR LIQUIDITY
    # --------------------------------------------------------

    opposite = _nearest_opposite_level(
        major_levels or [],
        direction,
        entry,
    )

    if opposite is not None:

        if direction == "LONG" and tp >= opposite:

            r.score = 70

            r.reason = (
                "1:2 TP упирается в крупную "
                "встречную ликвидность → NO TRADE."
            )

            return r.to_dict()

        if direction == "SHORT" and tp <= opposite:

            r.score = 70

            r.reason = (
                "1:2 TP проходит встречную "
                "крупную ликвидность → NO TRADE."
            )

            return r.to_dict()

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = 80

    # 1H context
    if (
        direction == "LONG"
        and ctx1h == "bullish"
    ):
        score += 7

    elif (
        direction == "SHORT"
        and ctx1h == "bearish"
    ):
        score += 7

    elif ctx1h != "neutral":

        score -= 4

    # 15M context
    if ctx15 == direction.lower():
        score += 5

    # Order flow
    if fok is True:
        score += 8

    score = min(score, 100)

    # --------------------------------------------------------
    # FINAL SCORE GATE
    # --------------------------------------------------------

    if score < MIN_SCORE_READY:

        r.score = score
        r.reason = (
            f"Score {score}/100 ниже минимального "
            f"{MIN_SCORE_READY} → NO TRADE."
        )

        return r.to_dict()

    # --------------------------------------------------------
    # READY
    # --------------------------------------------------------

    r.status = "READY"
    r.stage = "READY"

    r.score = score

    r.entry = entry
    r.sl = sl
    r.tp = tp

    r.rr = RR_TARGET
    r.one_tp = True

    r.reason = (
        "TradeMind 4.4.3 READY: "
        "fresh major sweep → "
        "15M confirmation → "
        "5M trigger ONLY in first 3 candles → "
        "trigger near sweep → "
        "entry near sweep → "
        "SL behind sweep extreme → "
        "strict 1:2 TP."
    )

    return r.to_dict()


# ============================================================
# SOL WRAPPER
# ============================================================

def analyze_sol(
    candles_1h,
    candles_15m,
    candles_5m,
    current_price,
    order_flow=None,
    sweep=None,
    major_levels=None,
):

    return analyze(
        candles_1h,
        candles_15m,
        candles_5m,
        current_price,
        major_levels=major_levels,
        order_flow=order_flow,
        sweep=sweep,
    )