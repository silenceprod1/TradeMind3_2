"""TradeMind 4.4.4 strategy engine.

1H context -> major liquidity -> sweep -> 15M confirmation
-> FIRST 3 REAL 5M candles -> Entry / SL / one TP exactly 1:2.

Compatibility:
- analyze(..., current_price=...)
- analyze(..., price=...)
Both are accepted so older scanner code does not crash.
"""

from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, List

STRATEGY_VERSION = "4.4.4"

RR_TARGET = 2.0
MAX_ENTRY_DISTANCE_PCT = 0.50
SL_BUFFER_PCT = 0.10
MIN_SCORE_READY = 80
MAX_5M_CANDLES_AFTER_CONFIRM = 3
MAX_SWEEP_AGE_5M = 6


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

    def to_dict(self):
        d = asdict(self)
        d["strategy_version"] = STRATEGY_VERSION
        return d


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def val(c, key, idx=None):
    if isinstance(c, dict):
        return f(c.get(key, c.get(key[0], None)))

    if idx is not None and isinstance(c, (list, tuple)) and len(c) > idx:
        return f(c[idx])

    return None


def timestamp(c):
    """Return candle open timestamp in milliseconds when available."""
    if isinstance(c, dict):
        for key in (
            "timestamp",
            "time",
            "open_time",
            "openTime",
            "ts",
        ):
            value = c.get(key)

            if value is not None:
                try:
                    return int(float(value))
                except (TypeError, ValueError):
                    pass

        return None

    if isinstance(c, (list, tuple)) and len(c) > 0:
        try:
            return int(float(c[0]))
        except (TypeError, ValueError):
            return None

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


def confirm_15m(candles, direction, sweep_level):
    """
    Mandatory 15M confirmation after sweep.
    Returns:
        ok, reason, confirmation_timestamp
    """

    d = last(candles, 6)

    if len(d) < 4 or sweep_level is None:
        return False, None, None

    oo = [o(x) for x in d]
    hh = [h(x) for x in d]
    ll = [l(x) for x in d]
    cc = [cl(x) for x in d]

    if any(x is None for x in oo + hh + ll + cc):
        return False, None, None

    O = oo[-1]
    H = hh[-1]
    L = ll[-1]
    C = cc[-1]

    prev_high = max(hh[:-1])
    prev_low = min(ll[:-1])

    rng = max(H - L, 1e-9)
    body = abs(C - O)

    conf_ts = timestamp(d[-1])

    if direction == "SHORT":

        bearish_reclaim = (
            C < sweep_level
            and C < O
        )

        bearish_break = (
            C < prev_low
            and C < O
            and body / rng >= 0.35
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
                conf_ts,
            )

        if bearish_reclaim and rejection:
            return (
                True,
                "15M bearish rejection/reclaim after upside sweep",
                conf_ts,
            )

        if bearish_reclaim:
            return (
                True,
                "15M close back below swept liquidity",
                conf_ts,
            )

    else:

        bullish_reclaim = (
            C > sweep_level
            and C > O
        )

        bullish_break = (
            C > prev_high
            and C > O
            and body / rng >= 0.35
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
                conf_ts,
            )

        if bullish_reclaim and rejection:
            return (
                True,
                "15M bullish rejection/reclaim after downside sweep",
                conf_ts,
            )

        if bullish_reclaim:
            return (
                True,
                "15M close back above swept liquidity",
                conf_ts,
            )

    return False, None, None


def trigger_on_candle(c, previous_candle, direction):
    """
    Evaluate ONE specific 5M candle.

    Important:
    We do not filter candidate candles first.
    This prevents a late trigger from being treated as an early trigger.
    """

    if previous_candle is None:
        return False, None

    O = o(c)
    H = h(c)
    L = l(c)
    C = cl(c)

    pH = h(previous_candle)
    pL = l(previous_candle)

    if any(
        x is None
        for x in (O, H, L, C, pH, pL)
    ):
        return False, None

    rng = max(H - L, 1e-9)
    body = abs(C - O)

    if direction == "LONG":

        if (
            C > O
            and C > pH
            and body / rng >= 0.45
        ):
            return (
                True,
                "5M bullish displacement / micro-structure break",
            )

        if (
            C > O
            and L < pL
            and C > L + rng * 0.55
        ):
            return (
                True,
                "5M bullish rejection after downside sweep",
            )

    else:

        if (
            C < O
            and C < pL
            and body / rng >= 0.45
        ):
            return (
                True,
                "5M bearish displacement / micro-structure break",
            )

        if (
            C < O
            and H > pH
            and C < H - rng * 0.55
        ):
            return (
                True,
                "5M bearish rejection after upside sweep",
            )

    return False, None


def confirm_5m_after_15m(
    candles_5m,
    direction,
    confirmation_ts,
):
    """
    Only the first 3 REAL 5M candles after
    the 15M confirmation.

    No late trigger.
    """

    if not candles_5m or confirmation_ts is None:
        return False, None

    after = []

    for candle in candles_5m:

        ts = timestamp(candle)

        if ts is not None and ts > confirmation_ts:
            after.append(candle)

    if len(after) < 1:
        return False, None

    # First 3 actual candles.
    after = after[:MAX_5M_CANDLES_AFTER_CONFIRM]

    all_ts = [
        timestamp(c)
        for c in candles_5m
    ]

    for candle in after:

        ts = timestamp(candle)

        try:
            idx = all_ts.index(ts)
        except ValueError:
            continue

        if idx <= 0:
            continue

        previous = candles_5m[idx - 1]

        ok, reason = trigger_on_candle(
            candle,
            previous,
            direction,
        )

        if ok:
            return True, reason

    return False, None


def flow_check(flow, direction):

    if not flow:
        return None, None

    absorption = str(
        flow.get("absorption", "")
    ).lower()

    delta = f(flow.get("delta"))
    cvd = f(flow.get("cvd_change"))

    if direction == "LONG":

        if absorption in {
            "buyers",
            "buyer",
            "buy",
        }:
            return True, "buyer absorption"

        if delta is not None and delta > 0:
            return True, "positive delta"

        if cvd is not None and cvd > 0:
            return True, "rising CVD"

    else:

        if absorption in {
            "sellers",
            "seller",
            "sell",
        }:
            return True, "seller absorption"

        if delta is not None and delta < 0:
            return True, "negative delta"

        if cvd is not None and cvd < 0:
            return True, "falling CVD"

    return False, "order flow does not support direction"


def trade(direction, entry, sl):
    """
    Exactly one TP at RR 1:2.
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


def _sweep_extreme(
    candles_5m,
    direction,
    level,
):

    if not candles_5m or level is None:
        return None

    recent = last(
        candles_5m,
        12,
    )

    if direction == "SHORT":

        swept = [
            h(x)
            for x in recent
            if h(x) is not None
            and h(x) > level
        ]

        return max(swept) if swept else level

    swept = [
        l(x)
        for x in recent
        if l(x) is not None
        and l(x) < level
    ]

    return min(swept) if swept else level


def _nearest_opposite_level(
    major_levels,
    direction,
    entry,
):

    if not major_levels or entry is None:
        return None

    candidates = []

    for zone in major_levels:

        if not isinstance(zone, dict):
            continue

        level = f(
            zone.get(
                "price",
                zone.get("level"),
            )
        )

        if level is None:
            continue

        side = str(
            zone.get("side", "")
        ).upper()

        if (
            direction == "LONG"
            and level > entry
            and side != "LONG"
        ):
            candidates.append(level)

        elif (
            direction == "SHORT"
            and level < entry
            and side != "SHORT"
        ):
            candidates.append(level)

    if not candidates:
        return None

    if direction == "LONG":
        return min(candidates)

    return max(candidates)


def _sweep_timestamp(sweep):

    if not sweep:
        return None

    keys = (
        "timestamp",
        "time",
        "sweep_timestamp",
        "sweep_time",
        "ts",
        "open_time",
        "openTime",
    )

    for key in keys:

        if key not in sweep:
            continue

        value = sweep.get(key)

        if value is None:
            continue

        try:
            return int(float(value))
        except (
            TypeError,
            ValueError,
        ):
            pass

    return None


def analyze(
    candles_1h: List[Any],
    candles_15m: List[Any],
    candles_5m: List[Any],
    current_price: float = None,
    major_levels=None,
    order_flow: Optional[
        Dict[str, Any]
    ] = None,
    sweep: Optional[
        Dict[str, Any]
    ] = None,

    # IMPORTANT:
    # Old bot scanner uses price=...
    # We keep this parameter for compatibility.
    price: float = None,
):

    # Support BOTH:
    # current_price=
    # price=

    if current_price is not None:
        p = f(current_price)
    else:
        p = f(price)

    r = Setup()

    if (
        p is None
        or not candles_1h
        or not candles_15m
        or not candles_5m
    ):
        r.reason = (
            "Недостаточно рыночных данных."
        )
        return r.to_dict()

    ctx1h = context_1h(
        candles_1h
    )

    ctx15 = context_15m(
        candles_15m
    )

    # ==========================================
    # MAJOR LIQUIDITY + SWEEP
    # ==========================================

    if (
        not sweep
        or not sweep.get("swept")
    ):

        r.score = (
            25
            if ctx15 == "neutral"
            else 35
        )

        r.reason = (
            "Нет подтвержденного sweep "
            "крупной ликвидности. "
            "В середине диапазона не входим."
        )

        return r.to_dict()

    direction = str(
        sweep.get(
            "direction",
            "",
        )
    ).upper()

    level = f(
        sweep.get(
            "level",
            sweep.get("price"),
        )
    )

    strength = f(
        sweep.get("strength")
    )

    if (
        direction not in {
            "LONG",
            "SHORT",
        }
        or level is None
    ):

        r.reason = (
            "Sweep не содержит "
            "корректного направления/уровня."
        )

        return r.to_dict()

    r.direction = direction

    r.liquidity_type = sweep.get(
        "liquidity_type",
        "major liquidity",
    )

    if (
        strength is not None
        and strength < 0.60
    ):

        r.score = 40

        r.reason = (
            "Sweep есть, "
            "но он недостаточно сильный."
        )

        return r.to_dict()

    # ==========================================
    # ANTI-CHASING
    # ==========================================

    distance_pct = (
        abs(p - level)
        / max(abs(level), 1e-9)
        * 100
    )

    if (
        distance_pct
        > MAX_ENTRY_DISTANCE_PCT
    ):

        r.score = 35
        r.stage = "SWEPT"

        r.reason = (
            f"После sweep цена ушла "
            f"на {distance_pct:.2f}% — "
            f"больше лимита "
            f"{MAX_ENTRY_DISTANCE_PCT:.2f}%. "
            f"Не догоняем."
        )

        return r.to_dict()

    # ==========================================
    # SWEEP FRESHNESS
    # ==========================================

    sweep_ts = _sweep_timestamp(
        sweep
    )

    latest_5m_ts = timestamp(
        candles_5m[-1]
    )

    if (
        sweep_ts is not None
        and latest_5m_ts is not None
    ):

        age_5m = max(
            0,
            (
                latest_5m_ts
                - sweep_ts
            )
            // (
                5 * 60 * 1000
            ),
        )

        if (
            age_5m
            > MAX_SWEEP_AGE_5M
        ):

            r.score = 40
            r.stage = "SWEPT"

            r.reason = (
                "Sweep слишком старый — "
                "сетап протух, "
                "не догоняем."
            )

            return r.to_dict()

    # ==========================================
    # 15M CONFIRMATION
    # ==========================================

    (
        ok15,
        conf15,
        conf_ts,
    ) = confirm_15m(
        candles_15m,
        direction,
        level,
    )

    r.confirmation_15m = conf15

    if not ok15:

        r.score = 55
        r.stage = "SWEPT"

        r.reason = (
            "Sweep есть, "
            "но 15M confirmation "
            "отсутствует → вход запрещен."
        )

        return r.to_dict()

    r.stage = "15M_CONFIRMED"

    # ==========================================
    # STRICT 5M TRIGGER
    # FIRST 3 REAL CANDLES
    # ==========================================

    (
        ok5,
        conf5,
    ) = confirm_5m_after_15m(
        candles_5m,
        direction,
        conf_ts,
    )

    r.confirmation = conf5

    if not ok5:

        r.score = 65

        r.reason = (
            "15M подтверждение есть, "
            "но в первых 3 реальных "
            "5M свечах после confirmation "
            "нет trigger → вход запрещен."
        )

        return r.to_dict()

    # ==========================================
    # ORDER FLOW
    # ==========================================

    fok, ft = flow_check(
        order_flow,
        direction,
    )

    r.order_flow = (
        ft
        if ft
        else "нет данных"
    )

    if fok is False:

        r.score = 68

        r.reason = (
            "5M trigger есть, "
            "но order flow "
            "не поддерживает направление."
        )

        return r.to_dict()

    # ==========================================
    # SL FROM SWEEP EXTREME
    # ==========================================

    extreme = f(
        sweep.get(
            "extreme",
            sweep.get(
                "sweep_extreme"
            ),
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

    if direction == "SHORT":

        sl = (
            extreme
            * (
                1
                + SL_BUFFER_PCT
                / 100
            )
        )

    else:

        sl = (
            extreme
            * (
                1
                - SL_BUFFER_PCT
                / 100
            )
        )

    # ==========================================
    # EXACT 1:2
    # ==========================================

    entry, sl, tp = trade(
        direction,
        p,
        sl,
    )

    if (
        entry is None
        or sl is None
        or tp is None
    ):

        r.score = 60

        r.reason = (
            "Невалидная геометрия "
            "Entry/SL → вход запрещен."
        )

        return r.to_dict()

    # ==========================================
    # OPPOSING MAJOR LIQUIDITY
    # ==========================================

    opposite = (
        _nearest_opposite_level(
            major_levels or [],
            direction,
            entry,
        )
    )

    if opposite is not None:

        if (
            direction == "LONG"
            and tp >= opposite
        ):

            r.score = 70

            r.reason = (
                "1:2 TP упирается "
                "в крупную встречную "
                "ликвидность → NO TRADE."
            )

            return r.to_dict()

        if (
            direction == "SHORT"
            and tp <= opposite
        ):

            r.score = 70

            r.reason = (
                "1:2 TP проходит "
                "встречную крупную "
                "ликвидность → NO TRADE."
            )

            return r.to_dict()

    # ==========================================
    # SCORE
    # ==========================================

    score = 80

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

    if (
        ctx15
        == direction.lower()
    ):

        score += 5

    if fok is True:
        score += 8

    score = min(
        score,
        100,
    )

    if score < MIN_SCORE_READY:

        r.score = score
        r.stage = "WAIT"

        r.reason = (
            f"Score {score} ниже "
            f"минимального "
            f"{MIN_SCORE_READY} "
            f"→ NO TRADE."
        )

        return r.to_dict()

    # ==========================================
    # READY
    # ==========================================

    r.status = "READY"
    r.stage = "READY"
    r.score = score

    r.entry = entry
    r.sl = sl
    r.tp = tp

    r.rr = RR_TARGET
    r.one_tp = True

    r.reason = (
        "1H context → "
        "major liquidity → "
        "sweep → "
        "15M confirmation → "
        "first 3 real 5M candles → "
        "Entry/SL/TP 1:2."
    )

    return r.to_dict()


def analyze_sol(
    candles_1h,
    candles_15m,
    candles_5m,
    current_price=None,
    order_flow=None,
    sweep=None,
    major_levels=None,
    price=None,
):

    return analyze(
        candles_1h,
        candles_15m,
        candles_5m,
        current_price=current_price,
        major_levels=major_levels,
        order_flow=order_flow,
        sweep=sweep,
        price=price,
    )