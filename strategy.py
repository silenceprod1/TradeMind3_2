from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional


# ============================================================
# TRADEMIND 6.2.2
#
# 1H -> MAJOR LIQUIDITY -> SWEEP
# -> 15M CONFIRMATION -> 5M ILM -> ENTRY
#
# D1 / W1 НЕ ИСПОЛЬЗУЮТСЯ
# ============================================================

STRATEGY_VERSION = "6.2.2"

MIN_SCORE_READY = 80
MIN_RR = 2.0

SL_BUFFER_PCT = 0.20

MIN_5M_MANIPULATION_PCT = 0.08
MIN_5M_RECOVERY_RATIO = 0.33
MIN_DISPLACEMENT_BODY_RATIO = 0.35

MIN_FVG_PCT = 0.03
MIN_TP_DISTANCE_PCT = 0.15

MAX_ILM_SWEEP_DISTANCE_PCT = 0.75

H1_LOOKBACK = 80
M15_LOOKBACK = 12
M5_LOOKBACK = 30

MIN_CANDLES_STRUCTURE = 15


# ============================================================
# HELPERS
# ============================================================

def _safe_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _pct(a, b):
    a = _safe_float(a)
    b = _safe_float(b)

    if a is None or b is None or b == 0:
        return 0.0

    return abs(a - b) / abs(b) * 100.0


def _open(c):
    return _safe_float(c.get("open"))


def _high(c):
    return _safe_float(c.get("high"))


def _low(c):
    return _safe_float(c.get("low"))


def _close(c):
    return _safe_float(c.get("close"))


def _body(c):
    o = _open(c)
    cl = _close(c)

    if o is None or cl is None:
        return 0.0

    return abs(cl - o)


def _range(c):
    h = _high(c)
    l = _low(c)

    if h is None or l is None:
        return 0.0

    return max(0.0, h - l)


def _body_ratio(c):
    r = _range(c)

    if r <= 0:
        return 0.0

    return _body(c) / r


def _bullish(c):
    o = _open(c)
    cl = _close(c)

    return (
        o is not None
        and cl is not None
        and cl > o
    )


def _bearish(c):
    o = _open(c)
    cl = _close(c)

    return (
        o is not None
        and cl is not None
        and cl < o
    )


# ============================================================
# SWINGS
# ============================================================

def _swing_highs(candles):
    result = []

    if not candles:
        return result

    data = candles[-H1_LOOKBACK:]

    for i in range(1, len(data) - 1):
        left = _high(data[i - 1])
        current = _high(data[i])
        right = _high(data[i + 1])

        if None in (left, current, right):
            continue

        if current > left and current >= right:
            result.append({
                "price": current,
                "index": i,
                "time": data[i].get("open_time"),
            })

    return result


def _swing_lows(candles):
    result = []

    if not candles:
        return result

    data = candles[-H1_LOOKBACK:]

    for i in range(1, len(data) - 1):
        left = _low(data[i - 1])
        current = _low(data[i])
        right = _low(data[i + 1])

        if None in (left, current, right):
            continue

        if current < left and current <= right:
            result.append({
                "price": current,
                "index": i,
                "time": data[i].get("open_time"),
            })

    return result


# ============================================================
# 1H STRUCTURE
# ============================================================

def structure_context(candles):
    if not candles or len(candles) < MIN_CANDLES_STRUCTURE:
        return {
            "direction": "NEUTRAL",
            "bos": False,
            "impulse": 0.0,
            "structure_score": 0,
            "reason": "Недостаточно 1H данных",
        }

    highs = _swing_highs(candles)
    lows = _swing_lows(candles)

    if len(highs) < 2 or len(lows) < 2:
        return {
            "direction": "NEUTRAL",
            "bos": False,
            "impulse": 0.0,
            "structure_score": 0,
            "reason": "Недостаточно swing-структуры",
        }

    last_high = highs[-1]["price"]
    prev_high = highs[-2]["price"]

    last_low = lows[-1]["price"]
    prev_low = lows[-2]["price"]

    bullish_score = 0
    bearish_score = 0

    if last_high > prev_high:
        bullish_score += 2
    elif last_high < prev_high:
        bearish_score += 2

    if last_low > prev_low:
        bullish_score += 2
    elif last_low < prev_low:
        bearish_score += 2

    recent = candles[-12:]

    start = _open(recent[0])
    end = _close(recent[-1])

    impulse = 0.0

    if start is not None and start != 0 and end is not None:
        impulse = (end - start) / start * 100.0

    if impulse >= 0.80:
        bullish_score += 1
    elif impulse <= -0.80:
        bearish_score += 1

    recent_structure = candles[-8:]
    latest = recent_structure[-1]
    latest_close = _close(latest)

    highs_recent = [
        _high(c)
        for c in recent_structure[:-1]
        if _high(c) is not None
    ]

    lows_recent = [
        _low(c)
        for c in recent_structure[:-1]
        if _low(c) is not None
    ]

    bullish_bos = False
    bearish_bos = False

    if latest_close is not None and highs_recent:
        bullish_bos = latest_close > max(highs_recent)

    if latest_close is not None and lows_recent:
        bearish_bos = latest_close < min(lows_recent)

    if bullish_bos:
        bullish_score += 2

    if bearish_bos:
        bearish_score += 2

    if bullish_score > bearish_score:
        direction = "BULLISH"
        reason = "1H бычья структура"
        score = bullish_score
        bos = bullish_bos

    elif bearish_score > bullish_score:
        direction = "BEARISH"
        reason = "1H медвежья структура"
        score = bearish_score
        bos = bearish_bos

    else:
        direction = "NEUTRAL"
        reason = "1H структура нейтральна"
        score = 0
        bos = False

    return {
        "direction": direction,
        "bos": bos,
        "impulse": round(impulse, 3),
        "structure_score": score,
        "last_high": last_high,
        "previous_high": prev_high,
        "last_low": last_low,
        "previous_low": prev_low,
        "reason": reason,
    }


# ============================================================
# 1H DIRECTION
# ============================================================

def get_1h_direction(candles_1h):
    structure = structure_context(candles_1h)

    if structure["direction"] == "BULLISH":
        direction = "LONG"
    elif structure["direction"] == "BEARISH":
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    return {
        "direction": direction,
        "source": "1H",
        "structure": structure,
    }


def get_higher_timeframe_direction(
    candles_1h=None,
    candles_d1=None,
    candles_w1=None,
):
    return get_1h_direction(candles_1h)


# ============================================================
# LIQUIDITY
# ============================================================

def _normalize_liquidity_levels(major_levels):
    result = []

    if not major_levels:
        return result

    for level in major_levels:
        if not isinstance(level, dict):
            continue

        price = _safe_float(
            level.get("price")
            if level.get("price") is not None
            else level.get("level")
        )

        if price is None or price <= 0:
            continue

        item = dict(level)
        item["price"] = price

        if item.get("swept") is None:
            item["swept"] = False

        position = item.get("position")
        side = item.get("side")

        if position == "ABOVE":
            item["side"] = "SHORT"
        elif position == "BELOW":
            item["side"] = "LONG"
        elif side == "ABOVE":
            item["side"] = "SHORT"
        elif side == "BELOW":
            item["side"] = "LONG"

        result.append(item)

    return result


def get_liquidity_sides(
    major_levels,
    current_price,
    max_levels_each_side=6,
):
    current_price = _safe_float(current_price)

    if current_price is None:
        return {
            "above": [],
            "below": [],
        }

    levels = _normalize_liquidity_levels(major_levels)

    above = []
    below = []

    for level in levels:
        if level.get("swept") is True:
            continue

        price = level["price"]

        item = dict(level)

        item["distance_pct"] = round(
            _pct(price, current_price),
            4,
        )

        if price > current_price:
            item["position"] = "ABOVE"
            item["side"] = "SHORT"
            item["liquidity_type"] = "BSL"
            above.append(item)

        elif price < current_price:
            item["position"] = "BELOW"
            item["side"] = "LONG"
            item["liquidity_type"] = "SSL"
            below.append(item)

    above.sort(
        key=lambda x: (
            x["distance_pct"],
            -_safe_float(x.get("touches"), 0),
            -_safe_float(x.get("strength"), 0),
        )
    )

    below.sort(
        key=lambda x: (
            x["distance_pct"],
            -_safe_float(x.get("touches"), 0),
            -_safe_float(x.get("strength"), 0),
        )
    )

    return {
        "above": above[:max_levels_each_side],
        "below": below[:max_levels_each_side],
    }


def get_directional_liquidity(
    major_levels,
    current_price,
    direction,
):
    sides = get_liquidity_sides(
        major_levels,
        current_price,
        max_levels_each_side=6,
    )

    if direction == "LONG":
        return sides["below"][0] if sides["below"] else None

    if direction == "SHORT":
        return sides["above"][0] if sides["above"] else None

    return None


# ============================================================
# TARGET
# ============================================================

def get_next_major_target(
    major_levels,
    entry,
    direction,
    excluded_price=None,
):
    entry = _safe_float(entry)
    excluded_price = _safe_float(excluded_price)

    if entry is None:
        return None

    levels = _normalize_liquidity_levels(major_levels)

    candidates = []

    for level in levels:
        price = level["price"]

        if level.get("swept") is True:
            continue

        if (
            excluded_price is not None
            and _pct(price, excluded_price) < 0.10
        ):
            continue

        if direction == "LONG" and price > entry:
            item = dict(level)
            item["liquidity_type"] = "BSL"
            candidates.append(item)

        elif direction == "SHORT" and price < entry:
            item = dict(level)
            item["liquidity_type"] = "SSL"
            candidates.append(item)

    if not candidates:
        return None

    if direction == "LONG":
        candidates.sort(key=lambda x: x["price"])
    else:
        candidates.sort(
            key=lambda x: x["price"],
            reverse=True,
        )

    return candidates[0]


# ============================================================
# FVG
# ============================================================

def find_fvg(candles, direction=None):
    if not candles or len(candles) < 3:
        return None

    data = candles[-20:]
    found = []

    for i in range(2, len(data)):
        c1 = data[i - 2]
        c2 = data[i - 1]
        c3 = data[i]

        h1 = _high(c1)
        l1 = _low(c1)

        h3 = _high(c3)
        l3 = _low(c3)

        if None in (h1, l1, h3, l3):
            continue

        # Bullish FVG
        if l3 > h1:
            size = _pct(l3, h1)

            if size >= MIN_FVG_PCT:
                found.append({
                    "direction": "LONG",
                    "low": h1,
                    "high": l3,
                    "mid": (h1 + l3) / 2.0,
                    "size_pct": round(size, 4),
                    "time": c3.get("open_time"),
                    "index": i,
                    "body_high": max(
                        _open(c2) or h1,
                        _close(c2) or h1,
                    ),
                    "body_low": min(
                        _open(c2) or l1,
                        _close(c2) or l1,
                    ),
                })

        # Bearish FVG
        if h3 < l1:
            size = _pct(l1, h3)

            if size >= MIN_FVG_PCT:
                found.append({
                    "direction": "SHORT",
                    "low": h3,
                    "high": l1,
                    "mid": (h3 + l1) / 2.0,
                    "size_pct": round(size, 4),
                    "time": c3.get("open_time"),
                    "index": i,
                    "body_high": max(
                        _open(c2) or h3,
                        _close(c2) or h3,
                    ),
                    "body_low": min(
                        _open(c2) or l1,
                        _close(c2) or l1,
                    ),
                })

    if not found:
        return None

    if direction:
        directional = [
            x for x in found
            if x["direction"] == direction
        ]

        if directional:
            return directional[-1]

    return found[-1]


def fvg_supports_direction(
    candles,
    fvg,
    direction,
):
    if not candles or not fvg:
        return False

    if fvg.get("direction") != direction:
        return False

    close = _close(candles[-1])

    low = _safe_float(fvg.get("low"))
    high = _safe_float(fvg.get("high"))

    if None in (close, low, high):
        return False

    if direction == "LONG":
        return close >= low

    if direction == "SHORT":
        return close <= high

    return False


# ============================================================
# 15M CONFIRMATION
#
# FIX:
# We don't require ONLY the absolute last candle to be BOS.
# We search for the latest valid closed confirmation after sweep.
# ============================================================

def confirmation_15m(
    candles_15m,
    direction,
    sweep=None,
):
    if not candles_15m or len(candles_15m) < 8:
        return None

    data = candles_15m[-M15_LOOKBACK:]

    sweep_time = None

    if sweep:
        sweep_time = sweep.get("time")

    if sweep_time is not None:
        filtered = [
            c for c in data
            if c.get("open_time") is not None
            and c.get("open_time") >= sweep_time
        ]

        if len(filtered) >= 2:
            data = filtered

    if len(data) < 2:
        return None

    # Search from newest backwards.
    for i in range(len(data) - 1, 0, -1):
        current = data[i]

        close = _close(current)
        body_ratio = _body_ratio(current)

        if close is None:
            continue

        previous = data[:i]

        highs = [
            _high(c)
            for c in previous
            if _high(c) is not None
        ]

        lows = [
            _low(c)
            for c in previous
            if _low(c) is not None
        ]

        if not highs or not lows:
            continue

        previous_high = max(highs)
        previous_low = min(lows)

        # LONG
        if direction == "LONG":
            if (
                close > previous_high
                and _bullish(current)
                and body_ratio >= MIN_DISPLACEMENT_BODY_RATIO
            ):
                return {
                    "confirmed": True,
                    "direction": "LONG",
                    "price": close,
                    "bos": True,
                    "body_ratio": round(body_ratio, 3),
                    "body_close": close,
                    "broken_level": previous_high,
                    "reason": "15M bullish BOS body close",
                    "time": current.get("open_time"),
                }

        # SHORT
        if direction == "SHORT":
            if (
                close < previous_low
                and _bearish(current)
                and body_ratio >= MIN_DISPLACEMENT_BODY_RATIO
            ):
                return {
                    "confirmed": True,
                    "direction": "SHORT",
                    "price": close,
                    "bos": True,
                    "body_ratio": round(body_ratio, 3),
                    "body_close": close,
                    "broken_level": previous_low,
                    "reason": "15M bearish BOS body close",
                    "time": current.get("open_time"),
                }

    return None


# ============================================================
# 5M ILM
#
# FIX:
# Search recent local manipulations.
# Do not blindly use the single highest/lowest candle.
# ============================================================

def _find_local_lows(data):
    result = []

    for i in range(1, len(data) - 1):
        left = _low(data[i - 1])
        cur = _low(data[i])
        right = _low(data[i + 1])

        if None in (left, cur, right):
            continue

        if cur <= left and cur < right:
            result.append(i)

    return result


def _find_local_highs(data):
    result = []

    for i in range(1, len(data) - 1):
        left = _high(data[i - 1])
        cur = _high(data[i])
        right = _high(data[i + 1])

        if None in (left, cur, right):
            continue

        if cur >= left and cur > right:
            result.append(i)

    return result


def detect_5m_ilm(
    candles_5m,
    direction,
    sweep=None,
    confirmation=None,
):
    if not candles_5m or len(candles_5m) < 12:
        return None

    data = candles_5m[-M5_LOOKBACK:]

    sweep_extreme = None
    sweep_time = None

    if sweep:
        sweep_extreme = _safe_float(
            sweep.get("extreme")
        )
        sweep_time = sweep.get("time")

    confirmation_time = None

    if confirmation:
        confirmation_time = confirmation.get("time")

    # --------------------------------------------------------
    # Only candles after 15M confirmation.
    # --------------------------------------------------------

    if confirmation_time is not None:
        filtered = [
            c for c in data
            if c.get("open_time") is not None
            and c.get("open_time") >= confirmation_time
        ]

        if len(filtered) >= 5:
            data = filtered

    elif sweep_time is not None:
        filtered = [
            c for c in data
            if c.get("open_time") is not None
            and c.get("open_time") >= sweep_time
        ]

        if len(filtered) >= 5:
            data = filtered

    if len(data) < 5:
        return None

    trigger = data[-1]
    trigger_close = _close(trigger)

    if trigger_close is None:
        return None

    search = data[:-1]

    if len(search) < 4:
        return None

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        local_lows = _find_local_lows(search)

        # If no perfect 3-candle low, allow the lowest recent
        # candle only as a fallback.
        if not local_lows:
            candidates = [
                i for i in range(len(search))
                if _low(search[i]) is not None
            ]

            local_lows = candidates[-5:]

        # Newest candidates first.
        local_lows = sorted(
            local_lows,
            reverse=True,
        )

        for extreme_index in local_lows:

            manipulation_low = _low(
                search[extreme_index]
            )

            if manipulation_low is None:
                continue

            recovery_candles = search[
                extreme_index + 1:
            ]

            if not recovery_candles:
                continue

            recovery_highs = [
                _high(c)
                for c in recovery_candles
                if _high(c) is not None
            ]

            if not recovery_highs:
                continue

            recovery_high = max(recovery_highs)

            manipulation_size = (
                recovery_high
                - manipulation_low
            )

            if manipulation_size <= 0:
                continue

            manipulation_pct = (
                manipulation_size
                / manipulation_low
                * 100.0
            )

            if manipulation_pct < MIN_5M_MANIPULATION_PCT:
                continue

            recovery = (
                trigger_close
                - manipulation_low
            )

            recovery_ratio = (
                recovery
                / manipulation_size
            )

            if recovery_ratio < MIN_5M_RECOVERY_RATIO:
                continue

            if sweep_extreme is not None:
                distance = _pct(
                    manipulation_low,
                    sweep_extreme,
                )

                if distance > MAX_ILM_SWEEP_DISTANCE_PCT:
                    continue

            body_ratio = _body_ratio(trigger)

            if (
                not _bullish(trigger)
                or body_ratio < MIN_DISPLACEMENT_BODY_RATIO
            ):
                continue

            # Find bearish body before manipulation.
            start_index = max(
                0,
                extreme_index - 5,
            )

            bearish_bodies = [
                c
                for c in search[start_index:extreme_index + 1]
                if _bearish(c)
            ]

            inversion_level = None

            for c in reversed(bearish_bodies):
                o = _open(c)
                cl = _close(c)

                if o is not None and cl is not None:
                    inversion_level = max(o, cl)
                    break

            if inversion_level is None:
                highs = [
                    _high(c)
                    for c in search[:extreme_index + 1]
                    if _high(c) is not None
                ]

                if highs:
                    inversion_level = max(highs)

            if inversion_level is None:
                continue

            # BODY close, not wick.
            if trigger_close <= inversion_level:
                continue

            fvg = find_fvg(
                data,
                "LONG",
            )

            fvg_ok = bool(
                fvg
                and trigger_close >= fvg["low"]
            )

            return {
                "confirmed": True,
                "direction": "LONG",
                "pattern": "V",
                "extreme": manipulation_low,
                "price": trigger_close,
                "trigger_price": trigger_close,
                "manipulation_pct": round(
                    manipulation_pct,
                    4,
                ),
                "recovery_ratio": round(
                    recovery_ratio,
                    3,
                ),
                "body_ratio": round(
                    body_ratio,
                    3,
                ),
                "inversion_level": inversion_level,
                "body_close": trigger_close,
                "fvg": fvg,
                "fvg_ok": fvg_ok,
                "reason": (
                    "5M V-ILM + bullish body inversion"
                ),
                "time": trigger.get("open_time"),
            }

        return None

    # ========================================================
    # SHORT
    # ========================================================

    if direction == "SHORT":

        local_highs = _find_local_highs(search)

        if not local_highs:
            candidates = [
                i for i in range(len(search))
                if _high(search[i]) is not None
            ]

            local_highs = candidates[-5:]

        local_highs = sorted(
            local_highs,
            reverse=True,
        )

        for extreme_index in local_highs:

            manipulation_high = _high(
                search[extreme_index]
            )

            if manipulation_high is None:
                continue

            recovery_candles = search[
                extreme_index + 1:
            ]

            if not recovery_candles:
                continue

            recovery_lows = [
                _low(c)
                for c in recovery_candles
                if _low(c) is not None
            ]

            if not recovery_lows:
                continue

            recovery_low = min(recovery_lows)

            manipulation_size = (
                manipulation_high
                - recovery_low
            )

            if manipulation_size <= 0:
                continue

            manipulation_pct = (
                manipulation_size
                / manipulation_high
                * 100.0
            )

            if manipulation_pct < MIN_5M_MANIPULATION_PCT:
                continue

            recovery = (
                manipulation_high
                - trigger_close
            )

            recovery_ratio = (
                recovery
                / manipulation_size
            )

            if recovery_ratio < MIN_5M_RECOVERY_RATIO:
                continue

            if sweep_extreme is not None:
                distance = _pct(
                    manipulation_high,
                    sweep_extreme,
                )

                if distance > MAX_ILM_SWEEP_DISTANCE_PCT:
                    continue

            body_ratio = _body_ratio(trigger)

            if (
                not _bearish(trigger)
                or body_ratio < MIN_DISPLACEMENT_BODY_RATIO
            ):
                continue

            start_index = max(
                0,
                extreme_index - 5,
            )

            bullish_bodies = [
                c
                for c in search[start_index:extreme_index + 1]
                if _bullish(c)
            ]

            inversion_level = None

            for c in reversed(bullish_bodies):
                o = _open(c)
                cl = _close(c)

                if o is not None and cl is not None:
                    inversion_level = min(o, cl)
                    break

            if inversion_level is None:
                lows = [
                    _low(c)
                    for c in search[:extreme_index + 1]
                    if _low(c) is not None
                ]

                if lows:
                    inversion_level = min(lows)

            if inversion_level is None:
                continue

            # BODY close, not wick.
            if trigger_close >= inversion_level:
                continue

            fvg = find_fvg(
                data,
                "SHORT",
            )

            fvg_ok = bool(
                fvg
                and trigger_close <= fvg["high"]
            )

            return {
                "confirmed": True,
                "direction": "SHORT",
                "pattern": "L",
                "extreme": manipulation_high,
                "price": trigger_close,
                "trigger_price": trigger_close,
                "manipulation_pct": round(
                    manipulation_pct,
                    4,
                ),
                "recovery_ratio": round(
                    recovery_ratio,
                    3,
                ),
                "body_ratio": round(
                    body_ratio,
                    3,
                ),
                "inversion_level": inversion_level,
                "body_close": trigger_close,
                "fvg": fvg,
                "fvg_ok": fvg_ok,
                "reason": (
                    "5M L-ILM + bearish body inversion"
                ),
                "time": trigger.get("open_time"),
            }

        return None

    return None


# ============================================================
# ENTRY
# ============================================================

def calculate_entry(
    current_price,
    confirmation_5m=None,
):
    if confirmation_5m:
        price = _safe_float(
            confirmation_5m.get(
                "trigger_price"
            )
            if confirmation_5m.get(
                "trigger_price"
            ) is not None
            else confirmation_5m.get(
                "price"
            )
        )

        if price is not None:
            return price

    return _safe_float(current_price)


# ============================================================
# SL
# ============================================================

def calculate_stop(
    sweep,
    direction,
    trigger_5m=None,
):
    if not sweep:
        return None

    sweep_extreme = _safe_float(
        sweep.get("extreme")
    )

    if sweep_extreme is None:
        return None

    ilm_extreme = None

    if trigger_5m:
        ilm_extreme = _safe_float(
            trigger_5m.get("extreme")
        )

    extreme = sweep_extreme

    if direction == "LONG":
        if (
            ilm_extreme is not None
            and ilm_extreme < extreme
        ):
            extreme = ilm_extreme

    elif direction == "SHORT":
        if (
            ilm_extreme is not None
            and ilm_extreme > extreme
        ):
            extreme = ilm_extreme

    buffer = SL_BUFFER_PCT / 100.0

    if direction == "LONG":
        return extreme * (1.0 - buffer)

    if direction == "SHORT":
        return extreme * (1.0 + buffer)

    return None


# ============================================================
# TP
# ============================================================

def calculate_take_profit(
    major_levels,
    entry,
    direction,
    sweep=None,
):
    excluded_price = None

    if sweep:
        excluded_price = _safe_float(
            sweep.get("level")
        )

    target = get_next_major_target(
        major_levels,
        entry,
        direction,
        excluded_price=excluded_price,
    )

    if not target:
        return None

    tp = _safe_float(
        target.get("price")
    )

    if tp is None:
        return None

    if _pct(tp, entry) < MIN_TP_DISTANCE_PCT:
        return None

    return tp


# ============================================================
# RR
# ============================================================

def calculate_rr(
    entry,
    stop_loss,
    take_profit,
    direction,
):
    entry = _safe_float(entry)
    stop_loss = _safe_float(stop_loss)
    take_profit = _safe_float(take_profit)

    if None in (
        entry,
        stop_loss,
        take_profit,
    ):
        return 0.0

    if direction == "LONG":
        risk = entry - stop_loss
        reward = take_profit - entry

    elif direction == "SHORT":
        risk = stop_loss - entry
        reward = entry - take_profit

    else:
        return 0.0

    if risk <= 0 or reward <= 0:
        return 0.0

    return reward / risk


# ============================================================
# TARGET VALIDATION
# ============================================================

def validate_target(
    major_levels,
    entry,
    take_profit,
    direction,
    sweep=None,
):
    if take_profit is None:
        return False

    excluded_price = None

    if sweep:
        excluded_price = _safe_float(
            sweep.get("level")
        )

    target = get_next_major_target(
        major_levels,
        entry,
        direction,
        excluded_price=excluded_price,
    )

    if not target:
        return False

    target_price = _safe_float(
        target.get("price")
    )

    if target_price is None:
        return False

    tolerance = max(
        target_price * 0.001,
        0.000001,
    )

    return (
        abs(target_price - take_profit)
        <= tolerance
    )


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    h1_ok,
    liquidity_ok,
    sweep_ok,
    confirmation_15m_ok,
    confirmation_5m_ok,
    target_ok,
    rr_ok,
    fvg_ok=False,
    strong_liquidity=False,
):
    score = 0

    if h1_ok:
        score += 20

    if liquidity_ok:
        score += 15

    if sweep_ok:
        score += 20

    if confirmation_15m_ok:
        score += 15

    if confirmation_5m_ok:
        score += 15

    if target_ok:
        score += 5

    if rr_ok:
        score += 5

    if fvg_ok:
        score += 3

    if strong_liquidity:
        score += 2

    return min(score, 100)


# ============================================================
# RESULT
# ============================================================

@dataclass
class StrategyResult:

    direction: str = "NEUTRAL"
    stage: str = "WAIT"
    score: int = 0
    reason: str = ""

    liquidity: Optional[Dict[str, Any]] = None
    sweep: Optional[Dict[str, Any]] = None

    confirmation_15m: Optional[
        Dict[str, Any]
    ] = None

    confirmation_5m: Optional[
        Dict[str, Any]
    ] = None

    trigger_5m: Optional[
        Dict[str, Any]
    ] = None

    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    rr: Optional[float] = None

    liquidity_above: Optional[
        List[Dict[str, Any]]
    ] = None

    liquidity_below: Optional[
        List[Dict[str, Any]]
    ] = None

    major_liquidity_above: Optional[
        List[Dict[str, Any]]
    ] = None

    major_liquidity_below: Optional[
        List[Dict[str, Any]]
    ] = None

    htf_source: Optional[str] = None

    h1_structure: Optional[
        Dict[str, Any]
    ] = None

    fvg_1h: Optional[
        Dict[str, Any]
    ] = None

    fvg_support: Optional[bool] = None

    tp_reason: Optional[str] = None

    sweep_extreme: Optional[float] = None

    def to_dict(self):
        data = asdict(self)

        data["confirmation"] = self.confirmation_5m
        data["trigger_5m"] = self.trigger_5m
        data["sl"] = self.stop_loss
        data["tp"] = self.take_profit

        return data


# ============================================================
# MAIN ANALYSIS
# ============================================================

def analyze(
    candles_1h,
    candles_15m,
    candles_5m,
    current_price,
    major_levels,
    sweep=None,
    order_flow=None,
    candles_1m=None,
    **_legacy_kwargs,
):
    current_price = _safe_float(current_price)

    if current_price is None:
        return StrategyResult(
            reason="Нет текущей цены"
        ).to_dict()

    # ========================================================
    # 1H
    # ========================================================

    h1_context = get_1h_direction(
        candles_1h
    )

    direction = h1_context["direction"]
    h1_structure = h1_context["structure"]

    # ========================================================
    # LIQUIDITY
    # ========================================================

    liquidity_sides = get_liquidity_sides(
        major_levels,
        current_price,
        max_levels_each_side=6,
    )

    above = liquidity_sides["above"]
    below = liquidity_sides["below"]

    result = StrategyResult(
        direction=direction,
        htf_source="1H",
        h1_structure=h1_structure,
        liquidity_above=above,
        liquidity_below=below,
        major_liquidity_above=above,
        major_liquidity_below=below,
    )

    # ========================================================
    # NEUTRAL
    # ========================================================

    if direction == "NEUTRAL":
        result.stage = "WAIT"
        result.score = 0
        result.reason = (
            "1H не даёт однозначного направления"
        )

        return result.to_dict()

    # ========================================================
    # H1 SAFETY
    # ========================================================

    expected_h1 = (
        "BULLISH"
        if direction == "LONG"
        else "BEARISH"
    )

    h1_ok = (
        h1_structure.get("direction")
        == expected_h1
    )

    if not h1_ok:
        result.stage = "WAIT"
        result.score = 20
        result.reason = (
            "1H структура не подтверждает направление"
        )

        return result.to_dict()

    # ========================================================
    # 1H FVG
    # ========================================================

    fvg_1h = find_fvg(
        candles_1h,
        direction,
    )

    result.fvg_1h = fvg_1h

    result.fvg_support = (
        fvg_supports_direction(
            candles_1h,
            fvg_1h,
            direction,
        )
        if fvg_1h
        else False
    )

    # ========================================================
    # DIRECTIONAL MAJOR LIQUIDITY
    # ========================================================

    if direction == "LONG":
        liquidity = (
            below[0]
            if below
            else None
        )

        wait_reason = "Ждём sweep SSL"

    else:
        liquidity = (
            above[0]
            if above
            else None
        )

        wait_reason = "Ждём sweep BSL"

    result.liquidity = liquidity

    liquidity_ok = liquidity is not None

    # ========================================================
    # NO LIQUIDITY
    # ========================================================

    if not liquidity_ok:
        result.stage = "WAIT"

        result.score = calculate_score(
            h1_ok=True,
            liquidity_ok=False,
            sweep_ok=False,
            confirmation_15m_ok=False,
            confirmation_5m_ok=False,
            target_ok=False,
            rr_ok=False,
            fvg_ok=bool(result.fvg_support),
        )

        result.reason = (
            "Нет свежей major liquidity "
            "в направлении 1H сценария"
        )

        return result.to_dict()

    # ========================================================
    # WAIT SWEEP
    # ========================================================

    active_sweep = sweep

    if active_sweep is None:

        strong_liquidity = (
            _safe_float(
                liquidity.get("touches"),
                0,
            )
            >= 3
        )

        result.stage = "WAIT"

        result.score = calculate_score(
            h1_ok=True,
            liquidity_ok=True,
            sweep_ok=False,
            confirmation_15m_ok=False,
            confirmation_5m_ok=False,
            target_ok=False,
            rr_ok=False,
            fvg_ok=bool(result.fvg_support),
            strong_liquidity=strong_liquidity,
        )

        result.reason = wait_reason

        return result.to_dict()

    # ========================================================
    # SWEEP DIRECTION
    # ========================================================

    if (
        active_sweep.get("direction")
        != direction
    ):
        result.stage = "WAIT"
        result.score = 40
        result.reason = (
            "Sweep не соответствует направлению 1H"
        )

        return result.to_dict()

    expected_liquidity_type = (
        "SSL"
        if direction == "LONG"
        else "BSL"
    )

    if (
        active_sweep.get("liquidity_type")
        != expected_liquidity_type
    ):
        result.stage = "WAIT"
        result.score = 40
        result.reason = (
            "Снята не та сторона major liquidity"
        )

        return result.to_dict()

    result.sweep = active_sweep

    result.sweep_extreme = _safe_float(
        active_sweep.get("extreme")
    )

    # ========================================================
    # 15M
    # ========================================================

    conf_15m = confirmation_15m(
        candles_15m,
        direction,
        active_sweep,
    )

    result.confirmation_15m = conf_15m

    if not conf_15m:
        result.stage = "SWEPT"

        result.score = calculate_score(
            h1_ok=True,
            liquidity_ok=True,
            sweep_ok=True,
            confirmation_15m_ok=False,
            confirmation_5m_ok=False,
            target_ok=False,
            rr_ok=False,
            fvg_ok=bool(result.fvg_support),
        )

        result.reason = (
            "Major liquidity снята. "
            "Ждём 15M confirmation"
        )

        return result.to_dict()

    # ========================================================
    # 5M ILM
    # ========================================================

    conf_5m = detect_5m_ilm(
        candles_5m,
        direction,
        active_sweep,
        conf_15m,
    )

    result.confirmation_5m = conf_5m
    result.trigger_5m = conf_5m

    if not conf_5m:
        result.stage = "15M_CONFIRMED"

        result.score = calculate_score(
            h1_ok=True,
            liquidity_ok=True,
            sweep_ok=True,
            confirmation_15m_ok=True,
            confirmation_5m_ok=False,
            target_ok=False,
            rr_ok=False,
            fvg_ok=bool(result.fvg_support),
        )

        result.reason = (
            "15M подтверждение есть. "
            "Ждём 5M ILM"
        )

        return result.to_dict()

    # ========================================================
    # ENTRY
    # ========================================================

    entry = calculate_entry(
        current_price,
        conf_5m,
    )

    result.entry = entry

    if entry is None:
        result.stage = "NO_TRADE"
        result.score = 75
        result.reason = (
            "Не удалось определить Entry"
        )

        return result.to_dict()

    # ========================================================
    # SL
    # ========================================================

    stop_loss = calculate_stop(
        active_sweep,
        direction,
        conf_5m,
    )

    result.stop_loss = stop_loss

    if stop_loss is None:
        result.stage = "NO_TRADE"
        result.score = 75
        result.reason = (
            "Не удалось рассчитать SL"
        )

        return result.to_dict()

    if direction == "LONG" and entry <= stop_loss:
        result.stage = "NO_TRADE"
        result.score = 75
        result.reason = (
            "Entry находится ниже или на SL"
        )

        return result.to_dict()

    if direction == "SHORT" and entry >= stop_loss:
        result.stage = "NO_TRADE"
        result.score = 75
        result.reason = (
            "Entry находится выше или на SL"
        )

        return result.to_dict()

    # ========================================================
    # TP
    # ========================================================

    target = get_next_major_target(
        major_levels,
        entry,
        direction,
        excluded_price=active_sweep.get("level"),
    )

    if not target:
        result.stage = "NO_TRADE"
        result.score = 75
        result.reason = (
            "Нет следующей свежей major liquidity для TP"
        )

        return result.to_dict()

    take_profit = _safe_float(
        target.get("price")
    )

    result.take_profit = take_profit

    result.tp_reason = (
        "Следующая свежая major liquidity "
        f"({target.get('liquidity_type', 'LIQUIDITY')})"
    )

    if take_profit is None:
        result.stage = "NO_TRADE"
        result.score = 75
        result.reason = "Некорректный TP"

        return result.to_dict()

    # ========================================================
    # TP DIRECTION
    # ========================================================

    if direction == "LONG" and take_profit <= entry:
        result.stage = "NO_TRADE"
        result.score = 75
        result.reason = (
            "TP находится не выше Entry"
        )

        return result.to_dict()

    if direction == "SHORT" and take_profit >= entry:
        result.stage = "NO_TRADE"
        result.score = 75
        result.reason = (
            "TP находится не ниже Entry"
        )

        return result.to_dict()

    # ========================================================
    # TP DISTANCE
    # ========================================================

    tp_distance_pct = _pct(
        take_profit,
        entry,
    )

    if tp_distance_pct < MIN_TP_DISTANCE_PCT:
        result.stage = "NO_TRADE"
        result.score = 75
        result.reason = "TP слишком близко"

        return result.to_dict()

    # ========================================================
    # RR
    # ========================================================

    rr = calculate_rr(
        entry,
        stop_loss,
        take_profit,
        direction,
    )

    result.rr = round(rr, 2)

    # HARD FILTER
    if rr < MIN_RR:
        result.stage = "NO_TRADE"
        result.score = 70
        result.reason = (
            f"RR {rr:.2f} < 1:2. "
            "Вход запрещён. "
            "TP не отодвигаем искусственно."
        )

        return result.to_dict()

    # ========================================================
    # TARGET VALIDATION
    # ========================================================

    target_ok = validate_target(
        major_levels,
        entry,
        take_profit,
        direction,
        sweep=active_sweep,
    )

    if not target_ok:
        result.stage = "NO_TRADE"
        result.score = 75
        result.reason = (
            "TP не совпадает со следующей "
            "свежей major liquidity"
        )

        return result.to_dict()

    # ========================================================
    # FINAL SCORE
    # ========================================================

    strong_liquidity = (
        _safe_float(
            liquidity.get("touches"),
            0,
        )
        >= 3
    )

    fvg_ok = (
        bool(conf_5m.get("fvg_ok"))
        or bool(result.fvg_support)
    )

    score = calculate_score(
        h1_ok=True,
        liquidity_ok=True,
        sweep_ok=True,
        confirmation_15m_ok=True,
        confirmation_5m_ok=True,
        target_ok=True,
        rr_ok=True,
        fvg_ok=fvg_ok,
        strong_liquidity=strong_liquidity,
    )

    result.score = score

    # ========================================================
    # READY
    # ========================================================

    if score >= MIN_SCORE_READY:
        result.stage = "READY"

        result.reason = (
            "Полный сетап: "
            "1H → Major Liquidity → Sweep → "
            "15M Confirmation → 5M ILM → "
            "Entry → SL → TP → "
            f"RR {rr:.2f}"
        )

        return result.to_dict()

    # ========================================================
    # FINAL SAFETY
    # ========================================================

    result.stage = "NO_TRADE"
    result.reason = (
        "Сетап не достиг минимального score"
    )

    return result.to_dict()


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "STRATEGY_VERSION",
    "MIN_SCORE_READY",
    "MIN_RR",
    "SL_BUFFER_PCT",
    "structure_context",
    "get_1h_direction",
    "get_higher_timeframe_direction",
    "get_directional_liquidity",
    "get_liquidity_sides",
    "get_next_major_target",
    "find_fvg",
    "fvg_supports_direction",
    "confirmation_15m",
    "detect_5m_ilm",
    "calculate_entry",
    "calculate_stop",
    "calculate_take_profit",
    "calculate_rr",
    "validate_target",
    "calculate_score",
    "StrategyResult",
    "analyze",
]