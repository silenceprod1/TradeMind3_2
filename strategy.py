"""
TradeMind 5.4

D1 -> W1 fallback -> 1H synchronization
-> Major Liquidity -> Fresh Sweep
-> 15M confirmation AFTER sweep
-> 5M V/L reversal
-> >=1/3 recovery
-> 5M FVG inversion AFTER reversal
-> Entry -> SL behind sweep -> Structural TP.

No exchange/network code.
"""

from dataclasses import dataclass, asdict
from typing import Any, Optional


STRATEGY_VERSION = "5.4"

MIN_SCORE_READY = 80

# Максимальное расстояние входа от swept liquidity.
MAX_ENTRY_DISTANCE_PCT = 0.75

# Минимальное восстановление после манипуляции.
MIN_RECOVERY_RATIO = 1.0 / 3.0

# Буфер за экстремумом sweep.
SL_BUFFER_PCT = 0.10

# Допустимая дистанция структурного TP.
MIN_TP_DISTANCE_PCT = 0.15
MAX_TP_DISTANCE_PCT = 8.0

STRUCTURE_LOOKBACK = 60
LIQUIDITY_LOOKBACK = 60

SWING_RADIUS = 2

LEVEL_CLUSTER_PCT = 0.35
MIN_MAJOR_TOUCHES = 2
MAX_MAJOR_LEVELS = 6

# Sweep должен быть свежим.
MAX_SWEEP_AGE_5M = 6

# Минимальная глубина прокола liquidity.
MIN_SWEEP_DEPTH_PCT = 0.10

# Минимальный body ratio последней reversal candle.
MIN_REVERSAL_BODY_RATIO = 0.35

# Сколько 5M свечей разрешаем после sweep
# для формирования V/L reversal.
MAX_REVERSAL_CANDLES = 3

REQUIRE_5M_IMBALANCE_INVERSION = True


# ============================================================
# BASIC HELPERS
# ============================================================

def _v(c, key):
    if isinstance(c, dict):
        x = c.get(key)

        if x is None:
            x = c.get({
                "open": "o",
                "high": "h",
                "low": "l",
                "close": "c",
                "open_time": "time",
                "timestamp": "time",
            }.get(key))

    else:
        x = None

    try:
        return float(x)
    except Exception:
        return None


def _time(c):
    if not isinstance(c, dict):
        return None

    return c.get(
        "open_time",
        c.get(
            "time",
            c.get("timestamp")
        )
    )


def _pct(a, b):
    if not b:
        return 999

    return abs(
        float(a) - float(b)
    ) / float(b) * 100


def _body_ratio(c):
    o = _v(c, "open")
    h = _v(c, "high")
    l = _v(c, "low")
    cl = _v(c, "close")

    if None in (o, h, l, cl):
        return 0

    if h == l:
        return 0

    return abs(cl - o) / (h - l)


def _bull(c):
    o = _v(c, "open")
    cl = _v(c, "close")

    if o is None or cl is None:
        return False

    return cl > o


def _bear(c):
    o = _v(c, "open")
    cl = _v(c, "close")

    if o is None or cl is None:
        return False

    return cl < o


def _time_after(a, b):
    """
    True если timestamp a строго позже b.
    Поддерживает числовые timestamps.
    """
    if a is None or b is None:
        return False

    try:
        return float(a) > float(b)
    except Exception:
        return False


# ============================================================
# SWINGS
# ============================================================

def _swings(candles, radius=SWING_RADIUS):

    out = []

    if not candles:
        return out

    n = len(candles)

    for i in range(
        radius,
        n - radius
    ):

        h = _v(
            candles[i],
            "high"
        )

        l = _v(
            candles[i],
            "low"
        )

        if h is None or l is None:
            continue

        highs = [
            _v(
                candles[j],
                "high"
            )
            for j in range(
                i - radius,
                i + radius + 1
            )
        ]

        lows = [
            _v(
                candles[j],
                "low"
            )
            for j in range(
                i - radius,
                i + radius + 1
            )
        ]

        if all(
            x is not None
            for x in highs
        ):

            if h == max(highs):

                out.append({
                    "type": "HIGH",
                    "price": h,
                    "index": i,
                    "time": _time(
                        candles[i]
                    ),
                })

        if all(
            x is not None
            for x in lows
        ):

            if l == min(lows):

                out.append({
                    "type": "LOW",
                    "price": l,
                    "index": i,
                    "time": _time(
                        candles[i]
                    ),
                })

    return out


# ============================================================
# MARKET STRUCTURE
# ============================================================

def market_structure(candles):

    if not candles:
        return "neutral"

    candles = candles[
        -STRUCTURE_LOOKBACK:
    ]

    swings = _swings(candles)

    highs = [
        x for x in swings
        if x["type"] == "HIGH"
    ]

    lows = [
        x for x in swings
        if x["type"] == "LOW"
    ]

    if len(highs) < 2 or len(lows) < 2:
        return "neutral"

    last_high = highs[-1]["price"]
    prev_high = highs[-2]["price"]

    last_low = lows[-1]["price"]
    prev_low = lows[-2]["price"]

    # HH + HL
    if (
        last_high > prev_high
        and
        last_low > prev_low
    ):
        return "bullish"

    # LH + LL
    if (
        last_high < prev_high
        and
        last_low < prev_low
    ):
        return "bearish"

    return "neutral"


def context_d1(candles):

    if not candles:
        return "neutral"

    return market_structure(candles)


def context_w1(candles):

    if not candles:
        return "neutral"

    return market_structure(candles)


def context_1h(candles):

    if not candles:
        return "neutral"

    return market_structure(candles)


# ============================================================
# MAJOR LIQUIDITY
# ============================================================

_CURRENT_CANDLES = []


def _cluster_levels(raw):

    groups = []

    for x in sorted(
        raw,
        key=lambda z: z["price"]
    ):

        placed = False

        for g in groups:

            if _pct(
                x["price"],
                g["price"]
            ) <= LEVEL_CLUSTER_PCT:

                g["members"].append(x)

                g["price"] = (
                    sum(
                        m["price"]
                        for m in g["members"]
                    )
                    /
                    len(
                        g["members"]
                    )
                )

                placed = True
                break

        if not placed:

            groups.append({
                "type": x["type"],
                "price": x["price"],
                "members": [x],
            })

    result = []

    for g in groups:

        members = g["members"]

        touches = len(members)

        last_idx = max(
            m["index"]
            for m in members
        )

        if _CURRENT_CANDLES:

            recency = max(
                0,
                1 -
                (
                    len(_CURRENT_CANDLES)
                    - 1
                    - last_idx
                )
                /
                LIQUIDITY_LOOKBACK
            )

        else:

            recency = 0.5

        strength = min(
            1.0,
            0.45
            +
            0.12 * max(
                0,
                touches - 1
            )
            +
            0.10 * recency
        )

        result.append({

            "type": g["type"],

            "price": g["price"],

            "touches": touches,

            "strength": round(
                strength,
                3
            ),

            "indices": [
                m["index"]
                for m in members
            ],
        })

    return result


def get_major_liquidity(
    candles_1h,
    current_price=None
):

    global _CURRENT_CANDLES

    if not candles_1h:
        return []

    candles = candles_1h[
        -LIQUIDITY_LOOKBACK:
    ]

    _CURRENT_CANDLES = candles

    raw = _swings(candles)

    if not raw:
        return []

    candidates = _cluster_levels(raw)

    repeated = [
        x for x in candidates
        if x["touches"]
        >= MIN_MAJOR_TOUCHES
    ]

    singles = [
        x for x in candidates
        if x["touches"]
        < MIN_MAJOR_TOUCHES
    ]

    if repeated:

        candidates = repeated

        # Разрешаем несколько сильных одиночных
        # уровней, только если повторных мало.
        if len(candidates) < 3:

            singles.sort(
                key=lambda x: x["strength"],
                reverse=True
            )

            candidates += singles[
                :3 - len(candidates)
            ]

    else:

        # Если повторных уровней вообще нет,
        # не возвращаем весь шум.
        candidates = sorted(
            candidates,
            key=lambda x: x["strength"],
            reverse=True
        )[:4]

    if current_price:

        candidates.sort(
            key=lambda x: (
                x["strength"],
                -_pct(
                    x["price"],
                    current_price
                )
            ),
            reverse=True
        )

    else:

        candidates.sort(
            key=lambda x: x["strength"],
            reverse=True
        )

    return candidates[
        :MAX_MAJOR_LEVELS
    ]


def nearest_major_levels(
    levels,
    price
):

    return sorted(
        levels,
        key=lambda x: _pct(
            x["price"],
            price
        )
    )


# ============================================================
# IMBALANCES / FVG
# ============================================================

def find_imbalances(candles):

    out = []

    if not candles:
        return out

    for i in range(
        2,
        len(candles)
    ):

        a = candles[i - 2]
        c = candles[i]

        ah = _v(a, "high")
        al = _v(a, "low")

        ch = _v(c, "high")
        cl = _v(c, "low")

        if None in (
            ah,
            al,
            ch,
            cl
        ):
            continue

        # Bearish FVG
        if al > ch:

            out.append({

                "type": "BEARISH",

                "low": ch,

                "high": al,

                "index": i,

                "time": _time(c),
            })

        # Bullish FVG
        if ah < cl:

            out.append({

                "type": "BULLISH",

                "low": ah,

                "high": cl,

                "index": i,

                "time": _time(c),
            })

    return out


def imbalance_context(
    candles_1h,
    price
):

    if not candles_1h:
        return "none"

    fvgs = find_imbalances(
        candles_1h[-30:]
    )

    if not fvgs:
        return "none"

    nearest = min(
        fvgs,
        key=lambda x: min(
            abs(
                price - x["low"]
            ),
            abs(
                price - x["high"]
            )
        )
    )

    if (
        nearest["low"]
        <= price
        <= nearest["high"]
    ):
        return "inside"

    if (
        _pct(
            price,
            nearest["low"]
        ) < 1.0
        or
        _pct(
            price,
            nearest["high"]
        ) < 1.0
    ):
        return "near"

    return "none"


# ============================================================
# 5M FVG INVERSION
# ============================================================

def fvg_inversion_5m(
    candles_5m,
    direction,
    after_time=None
):

    if not candles_5m:
        return False, None

    fvgs = find_imbalances(
        candles_5m[-20:]
    )

    if not fvgs:
        return False, None

    recent_candles = candles_5m[-12:]

    if direction == "LONG":

        for f in reversed(fvgs):

            if f["type"] != "BEARISH":
                continue

            for c in recent_candles:

                c_time = _time(c)

                if (
                    after_time is not None
                    and
                    not _time_after(
                        c_time,
                        after_time
                    )
                ):
                    continue

                cl = _v(
                    c,
                    "close"
                )

                if (
                    cl is not None
                    and
                    cl > f["high"]
                    and
                    _bull(c)
                ):

                    return True, f

    else:

        for f in reversed(fvgs):

            if f["type"] != "BULLISH":
                continue

            for c in recent_candles:

                c_time = _time(c)

                if (
                    after_time is not None
                    and
                    not _time_after(
                        c_time,
                        after_time
                    )
                ):
                    continue

                cl = _v(
                    c,
                    "close"
                )

                if (
                    cl is not None
                    and
                    cl < f["low"]
                    and
                    _bear(c)
                ):

                    return True, f

    return False, None


# ============================================================
# FRESH LIQUIDITY SWEEP
# ============================================================

def detect_fresh_sweep(
    candles_1h,
    candles_5m,
    current_price
):

    levels = get_major_liquidity(
        candles_1h,
        current_price
    )

    if not levels or not candles_5m:
        return None

    recent = candles_5m[
        -MAX_SWEEP_AGE_5M:
    ]

    if not recent:
        return None

    for i in range(
        len(recent) - 1,
        -1,
        -1
    ):

        c = recent[i]

        hi = _v(c, "high")
        lo = _v(c, "low")

        if hi is None or lo is None:
            continue

        for level in levels:

            p = level["price"]

            # =================================================
            # LONG SWEEP
            # =================================================

            if level["type"] == "LOW":

                depth = (
                    (p - lo)
                    /
                    p
                    *
                    100
                )

                if (
                    lo < p
                    and
                    depth >= MIN_SWEEP_DEPTH_PCT
                    and
                    current_price > p
                ):

                    absolute_index = (
                        len(candles_5m)
                        -
                        len(recent)
                        +
                        i
                    )

                    return {

                        "direction": "LONG",

                        "type": "LOW",

                        "level": p,

                        "price": p,

                        "extreme": lo,

                        "depth_pct": depth,

                        "age":
                            len(recent)
                            - 1
                            - i,

                        "index_5m":
                            absolute_index,

                        "open_time":
                            _time(c),

                        "strength":
                            level.get(
                                "strength",
                                0
                            ),

                        "touches":
                            level.get(
                                "touches",
                                1
                            ),
                    }

            # =================================================
            # SHORT SWEEP
            # =================================================

            if level["type"] == "HIGH":

                depth = (
                    (hi - p)
                    /
                    p
                    *
                    100
                )

                if (
                    hi > p
                    and
                    depth >= MIN_SWEEP_DEPTH_PCT
                    and
                    current_price < p
                ):

                    absolute_index = (
                        len(candles_5m)
                        -
                        len(recent)
                        +
                        i
                    )

                    return {

                        "direction": "SHORT",

                        "type": "HIGH",

                        "level": p,

                        "price": p,

                        "extreme": hi,

                        "depth_pct": depth,

                        "age":
                            len(recent)
                            - 1
                            - i,

                        "index_5m":
                            absolute_index,

                        "open_time":
                            _time(c),

                        "strength":
                            level.get(
                                "strength",
                                0
                            ),

                        "touches":
                            level.get(
                                "touches",
                                1
                            ),
                    }

    return None


# ============================================================
# 15M CONFIRMATION
# ============================================================

def _confirm_15m(
    candles,
    direction,
    level,
    sweep_time=None
):

    if not candles or level is None:
        return False, None

    recent = candles[-12:]

    for c in recent:

        c_time = _time(c)

        # Критически важно:
        # confirmation должна быть ПОСЛЕ sweep.
        if (
            sweep_time is not None
            and
            not _time_after(
                c_time,
                sweep_time
            )
        ):
            continue

        h = _v(c, "high")
        l = _v(c, "low")
        cl = _v(c, "close")

        if None in (
            h,
            l,
            cl
        ):
            continue

        # =====================================================
        # LONG
        # =====================================================

        if direction == "LONG":

            if (
                l < level
                and
                cl > level
                and
                _bull(c)
            ):

                return True, c_time

        # =====================================================
        # SHORT
        # =====================================================

        else:

            if (
                h > level
                and
                cl < level
                and
                _bear(c)
            ):

                return True, c_time

    return False, None


# ============================================================
# 5M V / L REVERSAL
# ============================================================

def _reversal_5m(
    candles,
    sweep
):

    if not sweep:
        return None

    direction = sweep["direction"]

    level = sweep["level"]

    sweep_index = sweep.get(
        "index_5m"
    )

    if sweep_index is None:
        return None

    if sweep_index >= len(candles) - 1:
        return None

    # ========================================================
    # ТОЛЬКО ПЕРВЫЕ 3 СВЕЧИ ПОСЛЕ SWEEP
    # ========================================================

    after = candles[
        sweep_index + 1:
        sweep_index + 1
        + MAX_REVERSAL_CANDLES
    ]

    if not after:
        return None

    extreme = sweep["extreme"]

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        manipulation = (
            level - extreme
        )

        if manipulation <= 0:
            return None

        recovery_values = []

        for c in after:

            cl = _v(
                c,
                "close"
            )

            if cl is not None:

                recovery_values.append(
                    cl - extreme
                )

        if not recovery_values:
            return None

        recovery = max(
            recovery_values
        )

        ratio = (
            recovery
            /
            manipulation
        )

        # Последняя доступная candle
        # должна дать полноценный trigger.
        trigger = after[-1]

        last_close = _v(
            trigger,
            "close"
        )

        ok = (
            ratio >= MIN_RECOVERY_RATIO
            and
            last_close is not None
            and
            last_close > level
            and
            _bull(trigger)
        )

    # ========================================================
    # SHORT
    # ========================================================

    else:

        manipulation = (
            extreme - level
        )

        if manipulation <= 0:
            return None

        recovery_values = []

        for c in after:

            cl = _v(
                c,
                "close"
            )

            if cl is not None:

                recovery_values.append(
                    extreme - cl
                )

        if not recovery_values:
            return None

        recovery = max(
            recovery_values
        )

        ratio = (
            recovery
            /
            manipulation
        )

        trigger = after[-1]

        last_close = _v(
            trigger,
            "close"
        )

        ok = (
            ratio >= MIN_RECOVERY_RATIO
            and
            last_close is not None
            and
            last_close < level
            and
            _bear(trigger)
        )

    if not ok:
        return None

    if (
        _body_ratio(trigger)
        <
        MIN_REVERSAL_BODY_RATIO
    ):
        return None

    return {

        "ok": True,

        "recovery_ratio": ratio,

        "candle_time":
            _time(trigger),

        "manipulation":
            manipulation,

        "recovery":
            recovery,

        "trigger_index":
            candles.index(trigger),

        "candles_after_sweep":
            len(after),
    }


# ============================================================
# STRUCTURAL TARGET
# ============================================================

def structural_target(
    candles_1h,
    direction,
    entry,
    sweep_index=None
):

    if not candles_1h:
        return None

    candles = candles_1h[
        -STRUCTURE_LOOKBACK:
    ]

    swings = _swings(candles)

    # ========================================================
    # LONG
    #
    # TP = local high, from which the current correction
    # started.
    # ========================================================

    if direction == "LONG":

        highs = [
            x
            for x in swings
            if (
                x["type"] == "HIGH"
                and
                x["price"] > entry
            )
        ]

        if not highs:
            return None

        # Если знаем индекс sweep,
        # ищем structural high ДО sweep.
        if sweep_index is not None:

            # sweep_index относится к 5M,
            # поэтому прямое сравнение с 1H индексом
            # невозможно.
            #
            # Используем последний структурный high
            # перед текущим участком.
            valid = [
                x
                for x in highs
                if x["price"] > entry
            ]

            if valid:
                return valid[-1]["price"]

        return highs[-1]["price"]

    # ========================================================
    # SHORT
    # ========================================================

    lows = [
        x
        for x in swings
        if (
            x["type"] == "LOW"
            and
            x["price"] < entry
        )
    ]

    if not lows:
        return None

    if sweep_index is not None:

        valid = [
            x
            for x in lows
            if x["price"] < entry
        ]

        if valid:
            return valid[-1]["price"]

    return lows[-1]["price"]


# ============================================================
# SETUP
# ============================================================

@dataclass
class Setup:

    status: str = "WAIT"

    stage: str = "WAIT"

    direction: Optional[str] = None

    score: int = 0

    reason: str = ""

    zone: Any = None

    entry: Optional[float] = None

    sl: Optional[float] = None

    tp: Optional[float] = None

    rr: Optional[float] = None

    tp_reason: Optional[str] = None

    liquidity_type: Optional[str] = None

    confirmation_15m: bool = False

    confirmation_15m_time: Any = None

    confirmation: bool = False

    order_flow: Any = None

    sweep_extreme: Optional[float] = None

    sweep_depth: Optional[float] = None

    sweep_age: Optional[int] = None

    structural_target: Optional[float] = None

    d1_context: str = "neutral"

    w1_context: str = "neutral"

    h1_context: str = "neutral"

    imbalance_context: Optional[str] = None

    imbalance_5m: Any = None

    manipulation: Any = None

    recovery: Any = None

    recovery_ratio: Optional[float] = None

    major_levels: Any = None

    score_breakdown: Any = None


def _dict(s):

    return asdict(s)


# ============================================================
# MAIN ANALYSIS
# ============================================================

def analyze(
    candles_1h,
    candles_15m,
    candles_5m,
    price,
    major_levels=None,
    sweep=None,
    candles_d1=None,
    candles_w1=None,
    order_flow=None,
    **kwargs
):

    # ========================================================
    # HIGHER TIMEFRAME CONTEXT
    # ========================================================

    d1 = context_d1(
        candles_d1
    )

    w1 = context_w1(
        candles_w1
    )

    h1 = context_1h(
        candles_1h
    )

    # ========================================================
    # D1 -> W1 FALLBACK
    # ========================================================

    higher_context = d1
    higher_tf = "D1"

    if d1 == "neutral":

        if w1 == "neutral":

            return _dict(
                Setup(

                    stage="W1",

                    score=25,

                    d1_context=d1,

                    w1_context=w1,

                    h1_context=h1,

                    reason=(
                        "D1 and W1 trend "
                        "are unclear; "
                        "no trade."
                    )
                )
            )

        higher_context = w1
        higher_tf = "W1"

    # ========================================================
    # DIRECTION
    # ========================================================

    if higher_context == "bullish":

        direction = "LONG"

    else:

        direction = "SHORT"

    # ========================================================
    # 1H SYNCHRONIZATION
    # ========================================================

    if h1 != higher_context:

        return _dict(
            Setup(

                stage="1H",

                score=35,

                direction=direction,

                d1_context=d1,

                w1_context=w1,

                h1_context=h1,

                reason=(
                    f"{higher_tf} and 1H "
                    "trend are not synchronized."
                )
            )
        )

    # ========================================================
    # MAJOR LIQUIDITY
    # ========================================================

    levels = (
        major_levels
        or
        get_major_liquidity(
            candles_1h,
            price
        )
    )

    if not levels:

        return _dict(
            Setup(

                stage="LIQUIDITY",

                score=45,

                direction=direction,

                d1_context=d1,

                w1_context=w1,

                h1_context=h1,

                reason=(
                    "No major liquidity."
                )
            )
        )

    # ========================================================
    # SWEEP
    # ========================================================

    if sweep is None:

        sweep = detect_fresh_sweep(
            candles_1h,
            candles_5m,
            price
        )

    if (
        not sweep
        or
        sweep.get("direction")
        != direction
    ):

        return _dict(
            Setup(

                stage="WAIT",

                score=55,

                direction=direction,

                d1_context=d1,

                w1_context=w1,

                h1_context=h1,

                major_levels=levels,

                reason=(
                    "No fresh major-liquidity "
                    "sweep in higher-timeframe "
                    "direction."
                )
            )
        )

    age = sweep.get(
        "age",
        999
    )

    depth = sweep.get(
        "depth_pct",
        0
    )

    # ========================================================
    # SWEEP QUALITY
    # ========================================================

    if (
        age > MAX_SWEEP_AGE_5M
        or
        depth < MIN_SWEEP_DEPTH_PCT
    ):

        return _dict(
            Setup(

                stage="SWEPT",

                score=65,

                direction=direction,

                d1_context=d1,

                w1_context=w1,

                h1_context=h1,

                major_levels=levels,

                sweep_extreme=
                    sweep.get(
                        "extreme"
                    ),

                sweep_age=age,

                sweep_depth=depth,

                reason=(
                    "Sweep is not "
                    "fresh/strong enough."
                )
            )
        )

    level = sweep["level"]

    sweep_time = sweep.get(
        "open_time"
    )

    # ========================================================
    # 15M CONFIRMATION
    # ========================================================

    conf, conf_time = _confirm_15m(
        candles_15m,
        direction,
        level,
        sweep_time=sweep_time
    )

    if not conf:

        return _dict(
            Setup(

                stage="SWEPT",

                score=70,

                direction=direction,

                d1_context=d1,

                w1_context=w1,

                h1_context=h1,

                major_levels=levels,

                sweep_extreme=
                    sweep.get(
                        "extreme"
                    ),

                sweep_age=age,

                sweep_depth=depth,

                reason=(
                    "Fresh sweep found; "
                    "waiting for NEW "
                    "15M confirmation."
                )
            )
        )

    # ========================================================
    # 5M V/L REVERSAL
    # ========================================================

    rev = _reversal_5m(
        candles_5m,
        sweep
    )

    if not rev:

        return _dict(
            Setup(

                stage="15M_CONFIRMED",

                score=75,

                direction=direction,

                d1_context=d1,

                w1_context=w1,

                h1_context=h1,

                major_levels=levels,

                confirmation_15m=True,

                confirmation_15m_time=
                    conf_time,

                sweep_extreme=
                    sweep.get(
                        "extreme"
                    ),

                sweep_age=age,

                sweep_depth=depth,

                reason=(
                    "15M confirmed; "
                    "waiting for fresh "
                    "5M V/L trigger."
                )
            )
        )

    # ========================================================
    # 5M FVG INVERSION
    # ========================================================

    fvg_ok, fvg = (
        fvg_inversion_5m(
            candles_5m,
            direction,
            after_time=
                rev.get(
                    "candle_time"
                )
        )
    )

    if (
        REQUIRE_5M_IMBALANCE_INVERSION
        and
        not fvg_ok
    ):

        return _dict(
            Setup(

                stage="CONFIRMED",

                score=78,

                direction=direction,

                d1_context=d1,

                w1_context=w1,

                h1_context=h1,

                major_levels=levels,

                confirmation_15m=True,

                confirmation_15m_time=
                    conf_time,

                recovery_ratio=
                    rev[
                        "recovery_ratio"
                    ],

                reason=(
                    "5M reversal found; "
                    "waiting for NEW "
                    "FVG inversion."
                )
            )
        )

    # ========================================================
    # ENTRY
    # ========================================================

    entry = float(price)

    extreme = float(
        sweep["extreme"]
    )

    # ========================================================
    # STOP LOSS
    # ========================================================

    if direction == "LONG":

        sl = (
            extreme
            *
            (
                1
                -
                SL_BUFFER_PCT / 100
            )
        )

    else:

        sl = (
            extreme
            *
            (
                1
                +
                SL_BUFFER_PCT / 100
            )
        )

    # ========================================================
    # ANTI-CHASE
    # ========================================================

    entry_distance = _pct(
        entry,
        level
    )

    if (
        entry_distance
        >
        MAX_ENTRY_DISTANCE_PCT
    ):

        return _dict(
            Setup(

                stage="WAIT",

                score=77,

                direction=direction,

                d1_context=d1,

                w1_context=w1,

                h1_context=h1,

                reason=(
                    "Entry too far "
                    "from swept level; "
                    "no chase."
                )
            )
        )

    # ========================================================
    # STRUCTURAL TP
    # ========================================================

    tp = structural_target(
        candles_1h,
        direction,
        entry,
        sweep_index=
            sweep.get(
                "index_5m"
            )
    )

    if tp is None:

        return _dict(
            Setup(

                stage="CONFIRMED",

                score=79,

                direction=direction,

                d1_context=d1,

                w1_context=w1,

                h1_context=h1,

                reason=(
                    "No valid "
                    "structural target."
                )
            )
        )

    # ========================================================
    # STRUCTURE VALIDATION
    # ========================================================

    if direction == "LONG":

        if not (
            tp
            >
            entry
            >
            sl
        ):

            return _dict(
                Setup(

                    stage="WAIT",

                    score=79,

                    direction=direction,

                    d1_context=d1,

                    w1_context=w1,

                    h1_context=h1,

                    reason=(
                        "Invalid LONG "
                        "structure."
                    )
                )
            )

    else:

        if not (
            tp
            <
            entry
            <
            sl
        ):

            return _dict(
                Setup(

                    stage="WAIT",

                    score=79,

                    direction=direction,

                    d1_context=d1,

                    w1_context=w1,

                    h1_context=h1,

                    reason=(
                        "Invalid SHORT "
                        "structure."
                    )
                )
            )

    # ========================================================
    # RR
    # ========================================================

    risk = abs(
        entry - sl
    )

    reward = abs(
        tp - entry
    )

    rr = (
        reward / risk
        if risk
        else 0
    )

    # RR теперь информационный.
    # TP определяется структурой.

    tp_dist = _pct(
        tp,
        entry
    )

    if (
        tp_dist < MIN_TP_DISTANCE_PCT
        or
        tp_dist > MAX_TP_DISTANCE_PCT
    ):

        return _dict(
            Setup(

                stage="WAIT",

                score=79,

                direction=direction,

                d1_context=d1,

                w1_context=w1,

                h1_context=h1,

                reason=(
                    "Structural TP "
                    "is invalid distance."
                )
            )
        )

    # ========================================================
    # SCORE
    # ========================================================

    score = 80

    breakdown = {

        "base": 80,

        "sweep_depth": 0,

        "liquidity_strength": 0,

        "recovery": 0,

        "fvg": 0,

        "order_flow": 0,
    }

    # ========================================================
    # SWEEP DEPTH
    # ========================================================

    if depth >= 0.30:

        score += 5

        breakdown[
            "sweep_depth"
        ] = 5

    # ========================================================
    # LIQUIDITY STRENGTH
    # ========================================================

    if (
        sweep.get(
            "strength",
            0
        )
        >= 0.80
    ):

        score += 3

        breakdown[
            "liquidity_strength"
        ] = 3

    # ========================================================
    # RECOVERY
    # ========================================================

    if (
        rev["recovery_ratio"]
        >= 0.50
    ):

        score += 5

        breakdown[
            "recovery"
        ] = 5

    else:

        score += 2

        breakdown[
            "recovery"
        ] = 2

    # ========================================================
    # FVG
    # ========================================================

    if fvg_ok:

        score += 5

        breakdown[
            "fvg"
        ] = 5

    # ========================================================
    # ORDER FLOW
    # ========================================================

    if order_flow is True:

        score += 5

        breakdown[
            "order_flow"
        ] = 5

    elif order_flow is False:

        score -= 5

        breakdown[
            "order_flow"
        ] = -5

    score = max(
        0,
        min(
            100,
            score
        )
    )

    # ========================================================
    # FINAL SCORE GATE
    # ========================================================

    if score < MIN_SCORE_READY:

        return _dict(
            Setup(

                stage="WAIT",

                score=score,

                direction=direction,

                d1_context=d1,

                w1_context=w1,

                h1_context=h1,

                reason=(
                    "Score below "
                    "READY threshold."
                )
            )
        )

    # ========================================================
    # READY
    # ========================================================

    return _dict(
        Setup(

            status="READY",

            stage="READY",

            direction=direction,

            score=score,

            reason=(
                "Full TradeMind 5.4 "
                "confirmation."
            ),

            zone=level,

            entry=entry,

            sl=sl,

            tp=tp,

            rr=rr,

            tp_reason=(
                "Structural target: "
                "local swing high/low."
            ),

            liquidity_type=
                sweep.get(
                    "type"
                ),

            confirmation_15m=True,

            confirmation_15m_time=
                conf_time,

            confirmation=True,

            order_flow=order_flow,

            sweep_extreme=extreme,

            sweep_depth=depth,

            sweep_age=age,

            structural_target=tp,

            d1_context=d1,

            w1_context=w1,

            h1_context=h1,

            imbalance_context=
                imbalance_context(
                    candles_1h,
                    price
                ),

            imbalance_5m=fvg,

            manipulation=sweep,

            recovery=rev,

            recovery_ratio=
                rev[
                    "recovery_ratio"
                ],

            major_levels=levels,

            score_breakdown=breakdown,
        )
    )


# ============================================================
# WRAPPERS
# ============================================================

def analyze_sol(
    candles_1h,
    candles_15m,
    candles_5m,
    price,
    **kwargs
):

    return analyze(
        candles_1h,
        candles_15m,
        candles_5m,
        price,
        **kwargs
    )


def analyze_symbol(
    candles_1h,
    candles_15m,
    candles_5m,
    price,
    **kwargs
):

    return analyze(
        candles_1h,
        candles_15m,
        candles_5m,
        price,
        **kwargs
    )