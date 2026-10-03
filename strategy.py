# -*- coding: utf-8 -*-
"""
TradeMind strategy v9.30.2 (SMC Multi-Timeframe Strategy)
- 1H: Поиск снятия ликвидности (SSL/BSL Sweeps) и определение глобального контекста.
- 15M: Подтверждение структуры (BOS/Engulfing), фильтры волатильности, ATR и Anti-FOMO.
- 5M: Поиск локальной манипуляции V/L-ILM и подтверждение объемом.
- Доработки: Динамический TP (Space Filter), защита от микро-стопов, учет комиссий.
"""

from typing import Any, Dict, List, Optional, Tuple
import time


STRATEGY_VERSION = "9.30.3"

ALLOW_SHORT = True

MAX_ILM_AGE_FOR_ENTRY = 6

ATR_SL_MULT_SOFT = 0.8
SL_BUFFER_PCT = 0.20
ATR_SL_MAX_MULT = 3.0
MIN_SL_ATR_RATIO = 0.5  # Защита от слишком узких стопов (< 0.5 ATR)

ENABLE_SESSION_FILTER = True
SESSION_BLOCK_START_HOUR = 2
SESSION_BLOCK_END_HOUR = 7
SESSION_FILTER_EXEMPT = {"BTCUSDT", "ETHUSDT"}

RETEST_OFFSET_PCT = 0.0

MIN_SCORE_READY = 85
REQUIRE_BOS_FOR_READY = True
STRUCTURAL_SL_LOOKBACK_15M = 50
ENTRY_TOLERANCE_PCT = 1.0
FIXED_RR = 2.0
FEE_SLIPPAGE_PCT = 0.12  # 0.12% поправка на комиссии и проскальзывание

MIN_SWEEP_DEPTH_PCT = 0.12
MAX_SWEEP_AGE_1H = 24

USE_ATR_SCALING = True
MIN_SL_DISTANCE_PCT = 0.35
MAX_SL_DISTANCE_PCT = 4.0

ENABLE_SIDEWAYS_FILTER = True
ENABLE_POSITION_FILTER = False
ENABLE_D1_BLOCK = True

SL_USE_1H_SWINGS = True
SL_USE_SWEEP_EXTREME = True
MIN_SL_ATR_MULT = 1.2
MIN_BODY_RATIO_TRIGGER_5M = 0.40
VOLATILITY_ATR_SPIKE_MULT = 2.5
ENABLE_VOLATILITY_FILTER = True

VOLUME_CONFIRMATION_ENABLED = True  # Включена обязательная проверка объемов
VOLUME_CONFIRMATION_MULT = 1.2
VOLUME_CONFIRMATION_LOOKBACK = 20

MIN_BODY_RATIO = 0.35
MAX_5M_ILM_CANDLES = 60
MAX_15M_CONFIRM_CANDLES = 24
MAX_ILM_AGE_CANDLES_5M = 48

MIN_5M_RECOVERY_RATIO = 0.15
# Было 5.0 (~6 USDT на SOL) — фильтр «ILM рядом со sweep» фактически не работал.
# 0.5% — стартовое значение, подбирается бэктестом (0.3–0.5).
MIN_5M_ILM_SWEEP_DISTANCE_PCT = 0.5

# Длительность свечи в мс: нужна, чтобы отсчитывать этапы цепочки
# sweep(1H) -> подтверждение(15M) -> ILM(5M) от ЗАКРЫТИЯ предыдущей свечи.
INTERVAL_MS_1H = 3_600_000
INTERVAL_MS_15M = 900_000

MIN_TREND_ACTIVITY_READY = 0.45
COUNTER_TREND_MIN_SCORE = 88

FVG_TOLERANCE_PCT = 0.10
FVG_SWEEP_BONUS = 10
FVG_ENTRY_BONUS = 5
FVG_MAX_BONUS = 15

ILM_TRIGGER_WINDOW = 5

READY_PROMOTE_TIERS = (
    (95, 0.20, False),
    (90, 0.25, True),
    (88, 0.30, True),
)

ENABLE_ANTI_FOMO = True

RSI_PERIOD = 14
STOCH_PERIOD = 14
STOCH_SMOOTH_K = 3
STOCH_SMOOTH_D = 3

RSI_OVERBOUGHT_LONG = 75.0
RSI_OVERSOLD_SHORT = 25.0
STOCH_OVERBOUGHT_LONG = 85.0
STOCH_OVERSOLD_SHORT = 15.0

ANTI_FOMO_RSI_COOL_LONG = 65.0
ANTI_FOMO_RSI_COOL_SHORT = 35.0
ANTI_FOMO_STOCH_COOL_LONG = 80.0
ANTI_FOMO_STOCH_COOL_SHORT = 20.0

EMA_PULLBACK_PERIOD = 21
ATR_EXTENSION_MULT = 1.2
ATR_PULLBACK_TOL_MULT = 0.30

ANTI_FOMO_HARD_BLOCK = True

ENABLE_D1_TREND_FILTER = True
D1_EMA_PERIOD = 50
D1_TREND_BAND_PCT = 0.5

ENABLE_ATR_REGIME_FILTER = True
ATR_REGIME_MIN = 1.08

ENABLE_SPACE_FILTER = True  # Включен фильтр пространства до уровней
MIN_RR_SPACE_MULT = 1.2


# -------------------------------------------------------------------------
# ВПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ПРЕОБРАЗОВАНИЯ И ДОСТУПА К ДАННЫМ
# -------------------------------------------------------------------------

def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _v(candle, key, default=None):
    if not isinstance(candle, dict):
        return default
    value = candle.get(key)
    if value is None:
        aliases = {
            "open": "o", "high": "h", "low": "l",
            "close": "c", "open_time": "time",
            "volume": "v",
        }
        alias = aliases.get(key)
        if alias:
            value = candle.get(alias)
    if value is None:
        return default
    conv = _f(value)
    if conv is not None:
        return conv
    return default


def _o(c):
    return _v(c, "open")


def _h(c):
    return _v(c, "high")


def _l(c):
    return _v(c, "low")


def _c(c):
    return _v(c, "close")


def _t(c):
    return _v(c, "open_time")


def _vol(c):
    return _v(c, "volume") or 0.0


def _body(c):
    o = _o(c)
    cl = _c(c)
    if o is None or cl is None:
        return 0.0
    return abs(cl - o)


def _range(c):
    h = _h(c)
    l = _l(c)
    if h is None or l is None:
        return 0.0
    return max(0.0, h - l)


def _body_ratio(c):
    r = _range(c)
    if r > 0:
        return _body(c) / r
    return 0.0


def _bull(c):
    o = _o(c)
    cl = _c(c)
    if o is None or cl is None:
        return False
    return cl > o


def _bear(c):
    o = _o(c)
    cl = _c(c)
    if o is None or cl is None:
        return False
    return cl < o


def _dist_pct(a, b):
    a = _f(a)
    b = _f(b)
    if a is None or b is None or b == 0:
        return None
    return abs(a - b) / abs(b) * 100


# -------------------------------------------------------------------------
# ИНДИКАТОРЫ И ТЕХНИЧЕСКИЕ РАСЧЕТЫ
# -------------------------------------------------------------------------

def _closed_only(candles, now_ms=None):
    """Убирает с конца списка ещё не закрытые (формирующиеся) свечи.

    Идемпотентна: в бэктесте, где в списке уже только закрытые свечи,
    ничего не удаляется. Свечи без close_time остаются как есть.
    В live Binance отдаёт формирующуюся свечу последней — её close_time
    ещё в будущем. Небольшой сдвиг часов безопасен: в худшем случае
    свеча считается закрытой на пару секунд позже.
    """
    if not candles:
        return candles
    if now_ms is None:
        now_ms = time.time() * 1000.0
    out = list(candles)
    while out:
        last = out[-1]
        ct = _f(last.get("close_time")) if isinstance(last, dict) else None
        if ct is not None and ct > now_ms:
            out.pop()
        else:
            break
    return out


def calculate_atr(candles, period=14):
    if not candles or len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = _h(candles[i])
        l = _l(candles[i])
        pc = _c(candles[i - 1])
        if h is None or l is None or pc is None:
            continue
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def calculate_ema(candles, period=21):
    if not candles or period <= 0:
        return None
    closes = [_c(c) for c in candles]
    closes = [x for x in closes if x is not None]
    if len(closes) < period:
        return None
    k = 2.0 / (period + 1.0)
    ema = sum(closes[:period]) / period
    for i in range(period, len(closes)):
        ema = closes[i] * k + ema * (1.0 - k)
    return ema


def calculate_rsi(candles, period=14):
    if not candles or period <= 0:
        return None
    closes = [_c(c) for c in candles]
    closes = [x for x in closes if x is not None]
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains.append(d)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(-d)
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100.0 - (100.0 / (1.0 + rs))


def calculate_stochastic(candles, k_period=14, smooth_k=3, smooth_d=3):
    if not candles or len(candles) < k_period + smooth_k:
        return None, None
    raw_k = []
    for i in range(k_period - 1, len(candles)):
        window = candles[i - k_period + 1:i + 1]
        highs = [_h(c) for c in window if _h(c) is not None]
        lows = [_l(c) for c in window if _l(c) is not None]
        if not highs or not lows:
            continue
        hh = max(highs)
        ll = min(lows)
        cl = _c(candles[i])
        if cl is None:
            continue
        if hh == ll:
            raw_k.append(50.0)
        else:
            raw_k.append((cl - ll) / (hh - ll) * 100.0)
    if len(raw_k) < smooth_k:
        return None, None
    ks = []
    for i in range(smooth_k - 1, len(raw_k)):
        ks.append(sum(raw_k[i - smooth_k + 1:i + 1]) / smooth_k)
    if not ks:
        return None, None
    k_val = ks[-1]
    d_val = sum(ks[-smooth_d:]) / smooth_d if len(ks) >= smooth_d else None
    return k_val, d_val


def _avg_atr(candles, fast=14, slow=50):
    if not candles or len(candles) < slow + 5:
        return None, None
    fv = calculate_atr(candles, fast)
    sv = calculate_atr(candles, slow)
    return fv, sv


def get_d1_trend_ema(candles_d1, price, period=None, band_pct=None):
    if period is None:
        period = D1_EMA_PERIOD
    if band_pct is None:
        band_pct = D1_TREND_BAND_PCT

    if not candles_d1 or len(candles_d1) < period + 2:
        return "NEUTRAL", None

    confirmed = candles_d1[:-1]
    if len(confirmed) < period:
        return "NEUTRAL", None

    ema = calculate_ema(confirmed, period)
    p = _f(price)
    if ema is None or ema <= 0 or p is None:
        return "NEUTRAL", None

    d = (p - ema) / ema * 100.0
    if d > band_pct:
        return "LONG", ema
    if d < -band_pct:
        return "SHORT", ema
    return "NEUTRAL", ema


# -------------------------------------------------------------------------
# АНАЛИЗ СВИНГОВ И ДИРЕКЦИИ (1H / 15M)
# -------------------------------------------------------------------------

def _swing_high(c, i):
    if i < 2 or i >= len(c) - 2:
        return False
    cur = _h(c[i])
    l1, l2 = _h(c[i - 1]), _h(c[i - 2])
    r1, r2 = _h(c[i + 1]), _h(c[i + 2])
    if any(x is None for x in (cur, l1, l2, r1, r2)):
        return False
    return cur > l1 and cur >= l2 and cur >= r1 and cur > r2


def _swing_low(c, i):
    if i < 2 or i >= len(c) - 2:
        return False
    cur = _l(c[i])
    l1, l2 = _l(c[i - 1]), _l(c[i - 2])
    r1, r2 = _l(c[i + 1]), _l(c[i + 2])
    if any(x is None for x in (cur, l1, l2, r1, r2)):
        return False
    return cur < l1 and cur <= l2 and cur <= r1 and cur < r2


def _swing_highs(c):
    r = []
    if not c:
        return r
    for i in range(len(c)):
        if _swing_high(c, i):
            p = _h(c[i])
            if p is not None:
                r.append((i, p))
    return r


def _swing_lows(c):
    r = []
    if not c:
        return r
    for i in range(len(c)):
        if _swing_low(c, i):
            p = _l(c[i])
            if p is not None:
                r.append((i, p))
    return r


def get_1h_direction(candles):
    if not candles or len(candles) < 15:
        return "NEUTRAL"
    candles = candles[-60:]
    highs = _swing_highs(candles)
    lows = _swing_lows(candles)
    bull, bear = False, False
    if len(highs) >= 2 and len(lows) >= 2:
        bull = (highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1])
        bear = (highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1])
    if not bull and not bear:
        recent = candles[-8:]
        bb = sum(_body(c) for c in recent if _bull(c))
        sb = sum(_body(c) for c in recent if _bear(c))
        if bb > 0 and bb > sb * 1.4:
            bull = True
        elif sb > 0 and sb > bb * 1.4:
            bear = True
    if bull and not bear:
        return "LONG"
    if bear and not bull:
        return "SHORT"
    return "NEUTRAL"


def get_higher_tf_direction(c1h, c1d=None, c1w=None):
    return get_1h_direction(c1h)


# -------------------------------------------------------------------------
# АНАЛИЗ УРОВНЕЙ И FVG (FAIR VALUE GAP)
# -------------------------------------------------------------------------

def _level_price(l):
    if isinstance(l, dict):
        return _f(l.get("price"))
    return _f(l)


def _level_side(l):
    if not isinstance(l, dict):
        return None
    side = l.get("side") or l.get("direction") or ""
    return str(side).upper()


def _level_type(l):
    if not isinstance(l, dict):
        return ""
    return str(l.get("type") or "").upper()


def _level_strength(l):
    if not isinstance(l, dict):
        return 0
    v = l.get("strength") or l.get("touches") or 0
    try:
        return float(v)
    except Exception:
        return 0


def _is_swept_level(l):
    if not isinstance(l, dict):
        return False
    return bool(l.get("swept") or l.get("taken") or l.get("used") or l.get("consumed"))


def _levels_for_dir(levels, direction):
    res = []
    exp = "SSL" if direction == "LONG" else "BSL"
    for lv in levels or []:
        price = _level_price(lv)
        if price is None:
            continue
        side = _level_side(lv)
        lt = _level_type(lv)
        if side == direction or lt == exp or lt.startswith(exp + "_"):
            res.append(lv)
    return res


def _opposite_levels(levels, direction):
    res = []
    exp = "BSL" if direction == "LONG" else "SSL"
    for lv in levels or []:
        price = _level_price(lv)
        if price is None:
            continue
        lt = _level_type(lv)
        side = _level_side(lv)
        if lt == exp or lt.startswith(exp + "_") or side == exp:
            res.append(lv)
    return res


def is_inside_fvg(price, fvgs, direction, tol=FVG_TOLERANCE_PCT):
    p = _f(price)
    if p is None or not fvgs:
        return False
    exp = "bullish" if direction == "LONG" else "bearish"
    for fvg in fvgs:
        if fvg.get("type") != exp:
            continue
        top = _f(fvg.get("top"))
        bottom = _f(fvg.get("bottom"))
        if top is None or bottom is None:
            continue
        t = top * tol / 100
        if (bottom - t) <= p <= (top + t):
            return True
    return False


def compute_fvg_bonus(sweep, entry, fvgs, direction):
    if not fvgs:
        return 0, False, False
    se = _f((sweep or {}).get("extreme"))
    ep = _f(entry)
    si = is_inside_fvg(se, fvgs, direction) if se is not None else False
    ei = is_inside_fvg(ep, fvgs, direction) if ep is not None else False
    b = 0
    if si:
        b += FVG_SWEEP_BONUS
    if ei:
        b += FVG_ENTRY_BONUS
    return min(b, FVG_MAX_BONUS), si, ei


def check_space_to_target(entry, sl, direction, levels):
    if not ENABLE_SPACE_FILTER:
        return True, None, None

    e = _f(entry)
    s = _f(sl)
    if e is None or s is None:
        return True, None, None
    risk = abs(e - s)
    if risk <= 0:
        return True, None, None

    targets = _opposite_levels(levels or [], direction)
    if not targets:
        return True, None, None

    candidates = []
    for t in targets:
        tp = _level_price(t)
        if tp is None:
            continue
        if direction == "LONG" and tp > e:
            candidates.append(tp)
        elif direction == "SHORT" and tp < e:
            candidates.append(tp)

    if not candidates:
        return True, None, None

    if direction == "LONG":
        nearest = min(candidates)
        dist = (nearest - e) / risk
    else:
        nearest = max(candidates)
        dist = (e - nearest) / risk

    if dist < MIN_RR_SPACE_MULT:
        return False, round(dist, 3), nearest
    return True, round(dist, 3), nearest


# -------------------------------------------------------------------------
# ПОИСК СНЯТИЯ ЛИКВИДНОСТИ (1H SWEEP) И ОБЪЕМЫ
# -------------------------------------------------------------------------

def _sweep_cand_score(candle, level, depth):
    strength = _level_strength(level)
    touches = level.get("touches", 1) if isinstance(level, dict) else 1
    return depth * 4.0 + strength / 20.0 + min(touches, 5) * 3.0


def _has_vol_conf(candles, idx, lookback=VOLUME_CONFIRMATION_LOOKBACK, mult=VOLUME_CONFIRMATION_MULT):
    if not candles or idx < 0 or idx >= len(candles) or idx < lookback:
        return True
    start = idx - lookback
    vols = [_vol(candles[i]) for i in range(start, idx) if _vol(candles[i]) > 0]
    if not vols:
        return True
    avg = sum(vols) / len(vols)
    if avg <= 0:
        return True
    cur = _vol(candles[idx])
    return cur >= avg * mult if cur > 0 else True


def find_sweep(candles_1h, major_levels, direction):
    if direction not in ("LONG", "SHORT") or not candles_1h or len(candles_1h) < 3:
        return None

    levels = _levels_for_dir(major_levels, direction)
    if not levels:
        return None

    recent = candles_1h[-MAX_SWEEP_AGE_1H:]
    candidates = []
    total = len(candles_1h)

    for idx in range(len(recent)):
        c = recent[len(recent) - 1 - idx]
        cidx = total - 1 - idx

        if VOLUME_CONFIRMATION_ENABLED and not _has_vol_conf(candles_1h, cidx):
            continue

        for level in levels:
            if _is_swept_level(level):
                continue
            price = _level_price(level)
            if price is None:
                continue

            if direction == "LONG":
                low, close = _l(c), _c(c)
                if low is None or close is None:
                    continue
                depth = (price - low) / price * 100
                if depth < MIN_SWEEP_DEPTH_PCT or not (low < price and close > price):
                    continue
                op = _o(c) or close
                body = abs(close - op)
                wick = min(op, close) - low
                if not (wick > body or _bull(c)):
                    continue

                inv, consec = False, 0
                for k in range(cidx + 1, total):
                    cc = _c(candles_1h[k])
                    if cc is not None and cc < low:
                        consec += 1
                        if consec >= 2:
                            inv = True
                            break
                    else:
                        consec = 0
                if inv:
                    continue

                candidates.append({
                    "swept": True, "direction": "LONG", "level": price, "extreme": low,
                    "open_time": _t(c), "price": low, "liquidity_type": "SSL",
                    "touches": level.get("touches", 1), "strength": level.get("strength", 0),
                    "depth_pct": depth, "_score": _sweep_cand_score(c, level, depth) - idx * 2.0,
                })
            else:
                high, close = _h(c), _c(c)
                if high is None or close is None:
                    continue
                depth = (high - price) / price * 100
                if depth < MIN_SWEEP_DEPTH_PCT or not (high > price and close < price):
                    continue
                op = _o(c) or close
                body = abs(close - op)
                wick = high - max(op, close)
                if not (wick > body or _bear(c)):
                    continue

                inv, consec = False, 0
                for k in range(cidx + 1, total):
                    cc = _c(candles_1h[k])
                    if cc is not None and cc > high:
                        consec += 1
                        if consec >= 2:
                            inv = True
                            break
                    else:
                        consec = 0
                if inv:
                    continue

                candidates.append({
                    "swept": True, "direction": "SHORT", "level": price, "extreme": high,
                    "open_time": _t(c), "price": high, "liquidity_type": "BSL",
                    "touches": level.get("touches", 1), "strength": level.get("strength", 0),
                    "depth_pct": depth, "_score": _sweep_cand_score(c, level, depth) - idx * 2.0,
                })

    if not candidates:
        return None
    best = max(candidates, key=lambda x: x["_score"])
    best.pop("_score", None)
    return best


def measure_trend_activity(candles_1h, direction):
    if not candles_1h or len(candles_1h) < 15:
        return 0.0
    recent = candles_1h[-20:]
    total = sum(_body(c) for c in recent)
    if total <= 0:
        return 0.0
    if direction == "LONG":
        direc = sum(_body(c) for c in recent if _bull(c))
    else:
        direc = sum(_body(c) for c in recent if _bear(c))
    return direc / total


# -------------------------------------------------------------------------
# ПОДТВЕРЖДЕНИЕ 15M И МОДЕЛЬ V/L-ILM НА 5M
# -------------------------------------------------------------------------

def _is_local_high_15m(c, i):
    if i < 1 or i >= len(c) - 1:
        return False
    cur, l, r = _h(c[i]), _h(c[i - 1]), _h(c[i + 1])
    return False if any(x is None for x in (cur, l, r)) else (cur >= l and cur > r)


def _is_local_low_15m(c, i):
    if i < 1 or i >= len(c) - 1:
        return False
    cur, l, r = _l(c[i]), _l(c[i - 1]), _l(c[i + 1])
    return False if any(x is None for x in (cur, l, r)) else (cur <= l and cur < r)


def confirmation_15m(candles_15m, sweep, direction):
    if not sweep or not candles_15m or direction not in ("LONG", "SHORT"):
        return False, None, None, False

    sweep_t = _f(sweep.get("open_time"))
    # open_time sweep-свечи 1H -> подтверждение ищем только в 15M-свечах,
    # открывшихся ПОСЛЕ её закрытия (раньше брались свечи внутри самой sweep-свечи).
    confirm_from = sweep_t + INTERVAL_MS_1H if sweep_t is not None else None
    candidates = [c for c in candles_15m if confirm_from is None or (_t(c) is not None and _t(c) >= confirm_from)]
    candidates = candidates[-MAX_15M_CONFIRM_CANDLES:]
    if len(candidates) < 3:
        return False, None, None, False

    fallback = None
    for i in range(1, len(candidates)):
        c = candidates[i]
        if _body_ratio(c) < MIN_BODY_RATIO:
            continue
        close = _c(c)
        if close is None:
            continue
        prev = candidates[i - 1]
        prev_h, prev_l, prev_b = _h(prev), _l(prev), _body(prev)

        if direction == "LONG":
            if not _bull(c):
                continue
            highs = [_h(candidates[j]) for j in range(i - 1) if _is_local_high_15m(candidates, j) and _h(candidates[j]) is not None]
            if not highs:
                continue
            ref = max(highs[-3:])
            if close > ref:
                return True, "15M BOS", _t(c), True
            if prev_h is not None and close > prev_h and _body(c) > prev_b and fallback is None:
                fallback = (True, "15M engulf", _t(c), False)
        else:
            if not _bear(c):
                continue
            lows = [_l(candidates[j]) for j in range(i - 1) if _is_local_low_15m(candidates, j) and _l(candidates[j]) is not None]
            if not lows:
                continue
            ref = min(lows[-3:])
            if close < ref:
                return True, "15M BOS", _t(c), True
            if prev_l is not None and close < prev_l and _body(c) > prev_b and fallback is None:
                fallback = (True, "15M engulf", _t(c), False)

    return fallback if fallback is not None else (False, None, None, False)


def _is_local_high(c, i):
    if i < 1 or i >= len(c) - 1:
        return False
    cur, l, r = _h(c[i]), _h(c[i - 1]), _h(c[i + 1])
    return False if any(x is None for x in (cur, l, r)) else (cur >= l and cur > r)


def _is_local_low(c, i):
    if i < 1 or i >= len(c) - 1:
        return False
    cur, l, r = _l(c[i]), _l(c[i - 1]), _l(c[i + 1])
    return False if any(x is None for x in (cur, l, r)) else (cur <= l and cur < r)


def _ilm_long(candles, i, sweep_lvl, sweep_ext):
    m = candles[i]
    if not _is_local_low(candles, i):
        return None
    ml, mh = _l(m), _h(m)
    if ml is None or mh is None:
        return None
    before = candles[max(0, i - 2):i]
    bl = [_l(x) for x in before if _l(x) is not None]
    if not bl:
        return None
    left_ref = min(bl)
    if left_ref <= ml:
        return None
    m_range = left_ref - ml
    if m_range <= 0:
        return None
    m_pct = m_range / left_ref * 100
    if m_pct < MIN_SWEEP_DEPTH_PCT:
        return None

    trig_idx = None
    end = min(len(candles), i + 1 + ILM_TRIGGER_WINDOW)
    for j in range(i + 1, end):
        trig = candles[j]
        tc = _c(trig)
        if tc is not None and _bull(trig) and _body_ratio(trig) >= MIN_BODY_RATIO_TRIGGER_5M and tc > mh:
            # Объемное подтверждение триггера
            if VOLUME_CONFIRMATION_ENABLED and not _has_vol_conf(candles, j):
                continue
            trig_idx = j
            break
    if trig_idx is None:
        return None

    trig = candles[trig_idx]
    tc = _c(trig)
    if tc is None:
        return None
    rec = (tc - ml) / m_range
    if rec < MIN_5M_RECOVERY_RATIO:
        return None
    if sweep_lvl is not None and abs(ml - sweep_lvl) / sweep_lvl * 100 > MIN_5M_ILM_SWEEP_DISTANCE_PCT:
        return None
    if sweep_ext is not None and ml > sweep_ext * (1 + MIN_5M_ILM_SWEEP_DISTANCE_PCT / 100):
        return None

    return {
        "direction": "LONG", "extreme": ml, "trigger_time": _t(trig),
        "trigger_price": tc, "reason": "5M V-ILM", "recovery_ratio": rec,
        "manipulation_pct": m_pct, "age_candles": len(candles) - 1 - trig_idx,
        "_score": rec * 30 + _body_ratio(trig) * 20 + m_pct * 5,
    }


def _ilm_short(candles, i, sweep_lvl, sweep_ext):
    m = candles[i]
    if not _is_local_high(candles, i):
        return None
    mh, ml = _h(m), _l(m)
    if mh is None or ml is None:
        return None
    before = candles[max(0, i - 2):i]
    bh = [_h(x) for x in before if _h(x) is not None]
    if not bh:
        return None
    left_ref = max(bh)
    if mh <= left_ref:
        return None
    m_range = mh - left_ref
    if m_range <= 0:
        return None
    m_pct = m_range / left_ref * 100
    if m_pct < MIN_SWEEP_DEPTH_PCT:
        return None

    trig_idx = None
    end = min(len(candles), i + 1 + ILM_TRIGGER_WINDOW)
    for j in range(i + 1, end):
        trig = candles[j]
        tc = _c(trig)
        if tc is not None and _bear(trig) and _body_ratio(trig) >= MIN_BODY_RATIO_TRIGGER_5M and tc < ml:
            if VOLUME_CONFIRMATION_ENABLED and not _has_vol_conf(candles, j):
                continue
            trig_idx = j
            break
    if trig_idx is None:
        return None

    trig = candles[trig_idx]
    tc = _c(trig)
    if tc is None:
        return None
    rec = (mh - tc) / m_range
    if rec < MIN_5M_RECOVERY_RATIO:
        return None
    if sweep_lvl is not None and abs(mh - sweep_lvl) / sweep_lvl * 100 > MIN_5M_ILM_SWEEP_DISTANCE_PCT:
        return None
    if sweep_ext is not None and mh < sweep_ext * (1 - MIN_5M_ILM_SWEEP_DISTANCE_PCT / 100):
        return None

    return {
        "direction": "SHORT", "extreme": mh, "trigger_time": _t(trig),
        "trigger_price": tc, "reason": "5M L-ILM", "recovery_ratio": rec,
        "manipulation_pct": m_pct, "age_candles": len(candles) - 1 - trig_idx,
        "_score": rec * 30 + _body_ratio(trig) * 20 + m_pct * 5,
    }


def detect_5m_ilm(candles_5m, sweep, direction, conf_time=None):
    if not sweep or direction not in ("LONG", "SHORT"):
        return False, None

    # conf_time — open_time подтверждающей 15M-свечи; ILM ищем только в 5M-свечах,
    # открывшихся после её закрытия (раньше — внутри самой подтверждающей свечи).
    conf_t = _f(conf_time)
    if conf_t is not None:
        start = conf_t + INTERVAL_MS_15M
    else:
        sw_t = _f(sweep.get("open_time"))
        start = sw_t + INTERVAL_MS_1H if sw_t is not None else None
    candles = [c for c in (candles_5m or []) if start is None or (_t(c) is not None and _t(c) >= start)]
    candles = candles[-MAX_5M_ILM_CANDLES:]
    if len(candles) < 5:
        return False, None

    sl, se = _f(sweep.get("level")), _f(sweep.get("extreme"))
    cands = []
    for i in range(2, len(candles) - 2):
        ilm = _ilm_long(candles, i, sl, se) if direction == "LONG" else _ilm_short(candles, i, sl, se)
        if ilm:
            cands.append(ilm)

    if not cands:
        return False, None
    best = max(cands, key=lambda x: x["_score"])
    best.pop("_score", None)
    return True, best


# -------------------------------------------------------------------------
# РАСЧЕТ ВХОДА, СТОП-ЛОССА, ТЕЙК-ПРОФИТА И ANTI-FOMO
# -------------------------------------------------------------------------

def calculate_entry(ilm, price, direction):
    if not ilm:
        return None
    trig = _f(ilm.get("trigger_price"))
    if trig is None or trig <= 0:
        return None
    offset = RETEST_OFFSET_PCT / 100.0
    if offset <= 0:
        return trig
    return trig * (1 - offset) if direction == "LONG" else trig * (1 + offset)


def find_structural_sl(candles_15m, direction, entry, ilm_ext, sweep_ext=None, candles_1h=None):
    ef = _f(entry)
    if ef is None:
        return ilm_ext
    cands = []

    if candles_15m and len(candles_15m) >= 10:
        w = candles_15m[-STRUCTURAL_SL_LOOKBACK_15M:]
        if direction == "LONG":
            cands.extend([p for _, p in _swing_lows(w)])
        else:
            cands.extend([p for _, p in _swing_highs(w)])

    if SL_USE_1H_SWINGS and candles_1h and len(candles_1h) >= 20:
        w1 = candles_1h[-60:]
        if direction == "LONG":
            cands.extend([p for _, p in _swing_lows(w1)])
        else:
            cands.extend([p for _, p in _swing_highs(w1)])

    se = _f(sweep_ext) if SL_USE_SWEEP_EXTREME else None
    if se is not None:
        cands.append(se)
    ie = _f(ilm_ext)
    if ie is not None:
        cands.append(ie)

    cands = [c for c in cands if c is not None]
    if not cands:
        return ilm_ext

    if direction == "LONG":
        if se is not None and se < ef:
            return se
        below = [c for c in cands if c < ef]
        return max(below) if below else ilm_ext
    else:
        if se is not None and se > ef:
            return se
        above = [c for c in cands if c > ef]
        return min(above) if above else ilm_ext


def calculate_stop(entry, struct_level, direction, atr=None):
    entry, level = _f(entry), _f(struct_level)
    if entry is None or level is None:
        return None

    if USE_ATR_SCALING and atr is not None and atr > 0:
        min_d = atr * ATR_SL_MULT_SOFT
        max_d = atr * ATR_SL_MAX_MULT
        min_noise_d = atr * MIN_SL_ATR_RATIO
    else:
        min_d = entry * MIN_SL_DISTANCE_PCT / 100
        max_d = entry * MAX_SL_DISTANCE_PCT / 100
        min_noise_d = min_d * 0.5

    if direction == "LONG":
        sl = level * (1 - SL_BUFFER_PCT / 100)
        dist = entry - sl
        if dist < min_noise_d:
            return None  # Отбраковка слишком близких стопов
        if dist < min_d:
            sl = entry - min_d
        if (entry - sl) > max_d:
            sl = entry - max_d
        return sl if sl < entry else None
    else:
        sl = level * (1 + SL_BUFFER_PCT / 100)
        dist = sl - entry
        if dist < min_noise_d:
            return None  # Отбраковка слишком близких стопов
        if dist < min_d:
            sl = entry + min_d
        if (sl - entry) > max_d:
            sl = entry + max_d
        return sl if sl > entry else None


def calculate_tp_by_rr(entry, sl, direction, rr=FIXED_RR, levels=None):
    entry, sl = _f(entry), _f(sl)
    if entry is None or sl is None:
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None

    standard_tp = entry + rr * risk if direction == "LONG" else entry - rr * risk

    if ENABLE_SPACE_FILTER and levels:
        space_ok, space_r, space_target = check_space_to_target(entry, sl, direction, levels)
        if space_target is not None:
            if direction == "LONG" and space_target < standard_tp:
                return space_target * 0.998  # Ставим TP за 0.2% до уровня
            elif direction == "SHORT" and space_target > standard_tp:
                return space_target * 1.002

    return standard_tp


def calculate_rr(entry, sl, tp):
    entry, sl, tp = _f(entry), _f(sl), _f(tp)
    if entry is None or sl is None or tp is None:
        return None
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk <= 0:
        return None

    # Поправка на комиссию и спред
    net_risk = risk + (entry * FEE_SLIPPAGE_PCT / 100.0)
    net_reward = max(0.0, reward - (entry * FEE_SLIPPAGE_PCT / 100.0))
    return net_reward / net_risk if net_risk > 0 else 0.0


def validate_geometry(entry, sl, tp, direction):
    entry, sl, tp = _f(entry), _f(sl), _f(tp)
    if entry is None or sl is None or tp is None:
        return False
    return sl < entry < tp if direction == "LONG" else tp < entry < sl


def check_anti_fomo(candles_15m, direction, price):
    if not ENABLE_ANTI_FOMO:
        return True, "anti_fomo disabled", {}

    if not candles_15m or len(candles_15m) < max(RSI_PERIOD, STOCH_PERIOD + 10, EMA_PULLBACK_PERIOD) + 5:
        return True, "anti_fomo: not enough data", {}

    rsi = calculate_rsi(candles_15m, RSI_PERIOD)
    k_val, d_val = calculate_stochastic(candles_15m, STOCH_PERIOD, STOCH_SMOOTH_K, STOCH_SMOOTH_D)
    ema = calculate_ema(candles_15m, EMA_PULLBACK_PERIOD)
    atr = calculate_atr(candles_15m, 14)
    p = _f(price)

    meta = {
        "rsi_15m": round(rsi, 2) if rsi is not None else None,
        "stoch_k_15m": round(k_val, 2) if k_val is not None else None,
        "stoch_d_15m": round(d_val, 2) if d_val is not None else None,
        "ema21_15m": round(ema, 8) if ema is not None else None,
        "atr_15m_fomo": round(atr, 8) if atr is not None else None,
    }

    if rsi is None or k_val is None or ema is None or atr is None or atr <= 0 or p is None:
        return True, "anti_fomo: insufficient indicators", meta

    if direction == "LONG":
        ob = (rsi >= RSI_OVERBOUGHT_LONG and k_val >= STOCH_OVERBOUGHT_LONG)
        extended = p > ema + ATR_EXTENSION_MULT * atr
        pulled = p <= ema + ATR_PULLBACK_TOL_MULT * atr
        cooled = (rsi < ANTI_FOMO_RSI_COOL_LONG or k_val < ANTI_FOMO_STOCH_COOL_LONG)

        meta.update({"ob": ob, "extended": extended, "pulled_back": pulled, "cooled": cooled})

        if ob and extended:
            return (True, "anti_fomo LONG ok (pullback)", meta) if (pulled or cooled) else (False, f"Anti-FOMO LONG: RSI {rsi:.1f} >= {RSI_OVERBOUGHT_LONG}, перекупленность.", meta)
        return True, "anti_fomo LONG passed", meta

    if direction == "SHORT":
        os_ = (rsi <= RSI_OVERSOLD_SHORT and k_val <= STOCH_OVERSOLD_SHORT)
        extended = p < ema - ATR_EXTENSION_MULT * atr
        pulled = p >= ema - ATR_PULLBACK_TOL_MULT * atr
        cooled = (rsi > ANTI_FOMO_RSI_COOL_SHORT or k_val > ANTI_FOMO_STOCH_COOL_SHORT)

        meta.update({"os": os_, "extended": extended, "pulled_back": pulled, "cooled": cooled})

        if os_ and extended:
            return (True, "anti_fomo SHORT ok (pullback)", meta) if (pulled or cooled) else (False, f"Anti-FOMO SHORT: RSI {rsi:.1f} <= {RSI_OVERSOLD_SHORT}, перепроданность.", meta)
        return True, "anti_fomo SHORT passed", meta

    return True, "anti_fomo: no direction", meta


# -------------------------------------------------------------------------
# ОСНОВНОЙ СКОРИНГ И СЦЕНАРНЫЙ АНАЛИЗ
# -------------------------------------------------------------------------

def _score(direction, ctx_dir, sweep, conf_str, bos, ilm, rr, maj_str, fvg_bonus):
    score = 0
    if direction == ctx_dir:
        score += 15
    elif ctx_dir == "NEUTRAL":
        score += 8
    else:
        score += 5

    if sweep:
        depth = sweep.get("depth_pct", 0)
        if depth >= 0.40:
            score += 20
        elif depth >= 0.25:
            score += 15
        elif depth >= 0.15:
            score += 10
        else:
            score += 6

    if conf_str >= 0.75:
        score += 15
    elif conf_str >= 0.50:
        score += 11
    elif conf_str > 0:
        score += 7

    if bos:
        score += 10

    if ilm:
        rec = ilm.get("recovery_ratio", 0)
        if rec >= 0.66:
            score += 15
        elif rec >= 0.50:
            score += 11
        else:
            score += 7

    if rr is not None and rr >= FIXED_RR - 1e-6:  # допуск на float: 2R может дать 1.9999999
        score += 15

    score += min(10, maj_str / 10.0)
    score += fvg_bonus
    return int(min(100, max(0, round(score))))


def _apply_ready_promote(result):
    if result.get("stage") != "15M_CONFIRMED":
        return result
    reason = result.get("reason", "")
    if "READY заблокирован" not in reason or any(result.get(k) is None for k in ("entry", "sl", "tp", "rr")):
        return result

    score = int(result.get("score", 0))
    trend = float(result.get("trend_activity", 0.0))
    bos = bool(result.get("bos", False))

    for sc_min, tr_min, need_bos in READY_PROMOTE_TIERS:
        if score < sc_min or trend < tr_min or (need_bos and not bos):
            continue
        result["stage"] = "READY"
        result["reason"] = f"v9.30.2 promote: score={score} trend={trend:.2f} bos={bos}"
        result["_v910_promoted"] = True
        return result
    return result


def _analyze_scenario(c1h, c15, c5, price, levels, direction, ctx_dir, d1_context=None, fvgs=None, symbol=None):
    result = {
        "stage": "WAIT", "direction": direction, "score": 0, "reason": "",
        "entry": None, "sl": None, "tp": None, "tp_source": "fixed_rr", "rr": None,
        "sweep": None, "major_levels": levels or [], "confirmation_15m": False,
        "confirmation_15m_time": None, "confirmation": None, "bos": False,
        "ilm": None, "sweep_extreme": None, "tp_reason": None, "geometry_valid": False,
        "trend_activity": 0.0, "fvg_bonus": 0, "fvg_sweep": False, "fvg_entry": False,
        "sl_distance_pct": None, "atr_15m": None, "sl_source": None, "anti_fomo": {},
        "anti_fomo_reason": "", "anti_fomo_ok": True, "d1_trend_ema": "NEUTRAL",
        "d1_ema_value": None, "atr_regime": None, "space_ok": True, "space_r": None,
        "space_target": None,
    }

    if direction == "SHORT" and not ALLOW_SHORT:
        result["reason"] = "SHORT disabled"
        return result

    price = _f(price)
    if price is None or not c1h or not c15 or not c5:
        result["reason"] = "Недостаточно данных."
        return result

    if ENABLE_SESSION_FILTER:
        _sym = symbol or ""
        if _sym not in SESSION_FILTER_EXEMPT:
            last_c = c1h[-1] if c1h else None
            t_ms = _t(last_c) if last_c else None
            if t_ms is not None:
                try:
                    hour_utc = time.gmtime(t_ms / 1000.0).tm_hour
                    if SESSION_BLOCK_START_HOUR <= hour_utc < SESSION_BLOCK_END_HOUR:
                        result["score"] = 0
                        result["reason"] = f"OTT block ({hour_utc}h UTC)"
                        return result
                except Exception:
                    pass

    if ENABLE_ATR_REGIME_FILTER:
        atr_fast, atr_slow = calculate_atr(c1h, 14), calculate_atr(c1h, 50)
        if atr_fast is not None and atr_slow is not None and atr_slow > 0:
            ratio = atr_fast / atr_slow
            result["atr_regime"] = round(ratio, 3)
            if ratio < ATR_REGIME_MIN:
                result["stage"] = "WAIT"
                result["score"] = 10
                result["reason"] = f"ATR-regime: {ratio:.2f} < {ATR_REGIME_MIN} (боковик)"
                return result

    if ENABLE_D1_TREND_FILTER:
        candles_d1 = (d1_context or {}).get("candles_d1") if isinstance(d1_context, dict) else None
        d1_trend, d1_ema = get_d1_trend_ema(candles_d1 or [], price)
        result["d1_trend_ema"] = d1_trend
        if d1_ema is not None:
            result["d1_ema_value"] = round(d1_ema, 8)

        if d1_trend != "NEUTRAL" and d1_trend != direction:
            result["stage"] = "WAIT"
            result["score"] = 5
            result["reason"] = f"D1 EMA{D1_EMA_PERIOD}: {d1_trend} vs {direction} — contra"
            return result

    trend = measure_trend_activity(c1h, direction)
    result["trend_activity"] = round(trend, 3)

    lv = _levels_for_dir(levels, direction)
    if not lv:
        result["score"] = 20
        result["reason"] = f"Нет Major {'SSL' if direction == 'LONG' else 'BSL'}."
        return result

    sweep = find_sweep(c1h, lv, direction)
    result["sweep"] = sweep

    if sweep is None:
        result["score"] = 25
        result["reason"] = f"Ждём {'SSL sweep' if direction == 'LONG' else 'BSL sweep'}."
        return result

    result["stage"] = "SWEPT"
    result["sweep_extreme"] = sweep.get("extreme")

    conf_ok, conf_text, conf_t, bos = confirmation_15m(c15, sweep, direction)
    result.update({"confirmation_15m": conf_ok, "confirmation_15m_time": conf_t, "confirmation": conf_text, "bos": bos})

    if not conf_ok:
        result["score"] = 50
        result["reason"] = "Ждём 15M."
        return result

    result["stage"] = "15M_CONFIRMED"

    ilm_ok, ilm = detect_5m_ilm(c5, sweep, direction, conf_t)
    result["ilm"] = ilm

    if not ilm_ok:
        result["score"] = 65
        result["reason"] = "Ждём 5M ILM."
        return result

    age = int(ilm.get("age_candles", 0))
    if age > MAX_ILM_AGE_CANDLES_5M:
        result["score"] = 65
        result["reason"] = f"ILM устарел ({age})"
        return result

    if age > MAX_ILM_AGE_FOR_ENTRY:
        result["score"] = 68
        result["reason"] = f"ILM старый ({age} > {MAX_ILM_AGE_FOR_ENTRY})"
        return result

    entry = calculate_entry(ilm, price, direction)
    if entry is None:
        result["score"] = 68
        result["reason"] = "Нет Entry."
        return result

    dist = _dist_pct(price, entry)
    if dist is None or dist > ENTRY_TOLERANCE_PCT:
        result["score"] = 68
        result["reason"] = f"Цена ушла на {dist:.2f}%"
        return result

    ilm_ext = _f(ilm.get("extreme"))

    if ENABLE_VOLATILITY_FILTER:
        fa, sa = _avg_atr(c15, fast=14, slow=50)
        if fa is not None and sa is not None and sa > 0 and fa > sa * VOLATILITY_ATR_SPIKE_MULT:
            result["score"] = 70
            result["reason"] = "Volatility spike"
            return result

    atr_15m = calculate_atr(c15, 14)
    struct_lvl = find_structural_sl(c15, direction, entry, ilm_ext, sweep_ext=sweep.get("extreme") if sweep else None, candles_1h=c1h)

    sl = calculate_stop(entry, struct_lvl, direction, atr=atr_15m)
    if sl is None:
        result["score"] = 68
        result["reason"] = "Нет SL (слишком близко или невалиден)."
        return result

    space_ok, space_r, space_target = check_space_to_target(entry, sl, direction, levels)
    result["space_ok"] = space_ok
    result["space_r"] = space_r
    result["space_target"] = round(space_target, 8) if space_target is not None else None

    if not space_ok:
        result["stage"] = "WAIT"
        result["score"] = 30
        result["reason"] = f"Space filter: RR до сопротивления {space_r} < {MIN_RR_SPACE_MULT}"
        return result

    tp = calculate_tp_by_rr(entry, sl, direction, FIXED_RR, levels=levels)
    if tp is None:
        result["score"] = 70
        result["reason"] = "Нет TP."
        return result

    if not validate_geometry(entry, sl, tp, direction):
        result["score"] = 68
        result["reason"] = "Геометрия сломана."
        return result

    result["geometry_valid"] = True

    fomo_ok, fomo_reason, fomo_meta = check_anti_fomo(c15, direction, price)
    result["anti_fomo_ok"] = fomo_ok
    result["anti_fomo_reason"] = fomo_reason
    result["anti_fomo"] = fomo_meta

    if not fomo_ok and ANTI_FOMO_HARD_BLOCK:
        result["stage"] = "WAIT_PULLBACK"
        result["reason"] = fomo_reason
        return result

    result.update({
        "entry": round(entry, 8),
        "sl": round(sl, 8),
        "tp": round(tp, 8),
        "rr": round(calculate_rr(entry, sl, tp) or 0.0, 3),
        "tp_reason": f"Dynamic TP / Space Filter (Target {round(tp, 4)})",
    })

    # Для скоринга берём RR ДО комиссий. result["rr"] — net-значение
    # (calculate_rr), и при TP ровно на FIXED_RR оно математически < FIXED_RR
    # (1.2–1.9 в зависимости от стопа), поэтому +15 баллов за RR никогда не
    # начислялись: максимум 85 при порогах 88–93 -> ни одной сделки в бэктесте.
    # Если Space Filter сократил TP, gross-RR < FIXED_RR и баллы не даются.
    _risk_g = abs(entry - sl)
    rr = (abs(tp - entry) / _risk_g) if _risk_g > 0 else None

    try:
        sdp = abs(entry - sl) / entry * 100
        result["sl_distance_pct"] = round(sdp, 3)
        if atr_15m:
            result["atr_15m"] = round(atr_15m, 6)
            result["sl_source"] = "atr_floor" if abs(entry - sl) < atr_15m * ATR_SL_MULT_SOFT else "structural"
    except Exception:
        pass

    try:
        ms = max([_level_strength(l) for l in lv] or [0])
    except Exception:
        ms = 0

    fb, fs, fe = compute_fvg_bonus(sweep, entry, fvgs or [], direction)
    result["fvg_bonus"] = fb
    result["fvg_sweep"] = fs
    result["fvg_entry"] = fe

    conf_str = 0.8 if conf_ok else 0.6

    score = _score(
        direction=direction, ctx_dir=ctx_dir, sweep=sweep,
        conf_str=conf_str, bos=bos, ilm=ilm, rr=rr,
        maj_str=ms, fvg_bonus=fb,
    )
    result["score"] = score

    trend_ok = trend >= MIN_TREND_ACTIVITY_READY
    bos_ok = (not REQUIRE_BOS_FOR_READY) or bos
    ready_ok = (score >= MIN_SCORE_READY and trend_ok and bos_ok)

    if ready_ok:
        result["stage"] = "READY"
        result["reason"] = f"Sweep→15M→5M ILM. Trend {trend:.2f}. RR {result['rr']}. BOS."
        return result

    result["stage"] = "15M_CONFIRMED"
    blocks = []
    if score < MIN_SCORE_READY:
        blocks.append(f"score {score}")
    if not trend_ok:
        blocks.append(f"trend {trend:.2f}")
    if not bos_ok:
        blocks.append("no_bos")
    result["reason"] = "READY заблокирован: " + ", ".join(blocks)
    result = _apply_ready_promote(result)
    return result


# -------------------------------------------------------------------------
# ГЛАВНАЯ ТОЧКА ВХОДА СТРАТЕГИИ
# -------------------------------------------------------------------------

def analyze(candles_1h, candles_15m, candles_5m, current_price, major_levels=None, sweep=None, order_flow=None, candles_1m=None, d1_context=None, fvgs=None, symbol=None):
    price = _f(current_price)

    # Анализ только по закрытым свечам: live и бэктест должны видеть одно и то же.
    # D1 не трогаем — get_d1_trend_ema сам отбрасывает последнюю свечу.
    candles_1h = _closed_only(candles_1h)
    candles_15m = _closed_only(candles_15m)
    candles_5m = _closed_only(candles_5m)

    ctx_dir = get_1h_direction(candles_1h)

    base = {
        "stage": "WAIT", "direction": ctx_dir, "context_direction": ctx_dir,
        "d1_trend": (d1_context or {}).get("trend", "NEUTRAL"),
        "d1_point_a": (d1_context or {}).get("point_a"),
        "d1_point_b": (d1_context or {}).get("point_b"),
        "score": 0, "reason": "", "entry": None, "sl": None, "tp": None,
        "tp_source": "fixed_rr", "rr": None, "sweep": None, "major_levels": major_levels or [],
        "confirmation_15m": False, "confirmation_15m_time": None, "confirmation": None,
        "bos": False, "ilm": None, "sweep_extreme": None, "tp_reason": None,
        "geometry_valid": False, "trend_activity": 0.0, "fvg_bonus": 0,
        "fvg_sweep": False, "fvg_entry": False, "long": None, "short": None,
        "anti_fomo": {}, "anti_fomo_reason": "", "anti_fomo_ok": True,
        "d1_trend_ema": "NEUTRAL", "d1_ema_value": None,
    }

    if price is None or not candles_1h or not candles_15m or not candles_5m:
        base["reason"] = "Недостаточно данных."
        return base

    lr = _analyze_scenario(candles_1h, candles_15m, candles_5m, price, major_levels, "LONG", ctx_dir, d1_context=d1_context, fvgs=fvgs, symbol=symbol)
    sr = _analyze_scenario(candles_1h, candles_15m, candles_5m, price, major_levels, "SHORT", ctx_dir, d1_context=d1_context, fvgs=fvgs, symbol=symbol)

    base["long"] = lr
    base["short"] = sr

    if ctx_dir == "NEUTRAL":
        best = lr if lr.get("score", 0) >= sr.get("score", 0) else sr
        stage = best.get("stage", "WAIT")
        if stage == "READY":
            stage = "WAIT"
        base.update({
            "stage": stage, "direction": "NEUTRAL", "score": best.get("score", 0),
            "reason": "1H NEUTRAL", "entry": best.get("entry"), "sl": best.get("sl"),
            "tp": best.get("tp"), "tp_source": best.get("tp_source"), "rr": best.get("rr"),
            "sweep": best.get("sweep"), "confirmation_15m": best.get("confirmation_15m", False),
            "confirmation_15m_time": best.get("confirmation_15m_time"),
            "confirmation": best.get("confirmation"), "bos": best.get("bos", False),
            "ilm": best.get("ilm"), "sweep_extreme": best.get("sweep_extreme"),
            "tp_reason": best.get("tp_reason"), "geometry_valid": best.get("geometry_valid", False),
            "trend_activity": best.get("trend_activity", 0.0), "fvg_bonus": best.get("fvg_bonus", 0),
            "fvg_sweep": best.get("fvg_sweep", False), "fvg_entry": best.get("fvg_entry", False),
            "anti_fomo": best.get("anti_fomo", {}), "anti_fomo_reason": best.get("anti_fomo_reason", ""),
            "anti_fomo_ok": best.get("anti_fomo_ok", True), "d1_trend_ema": best.get("d1_trend_ema", "NEUTRAL"),
            "d1_ema_value": best.get("d1_ema_value"),
        })
        return base

    ready = []
    if lr.get("stage") == "READY" and lr.get("score", 0) >= MIN_SCORE_READY:
        ready.append(lr)
    if sr.get("stage") == "READY" and sr.get("score", 0) >= MIN_SCORE_READY:
        ready.append(sr)

    if ready:
        aligned = [x for x in ready if x["direction"] == ctx_dir]
        counter = [x for x in ready if x["direction"] != ctx_dir]

        chosen = None
        if aligned:
            chosen = max(aligned, key=lambda x: x["score"])
        elif counter:
            cr = [x for x in counter if x["score"] >= COUNTER_TREND_MIN_SCORE]
            if not cr:
                base["score"] = max(lr["score"], sr["score"])
                base["reason"] = "Counter-тренд слаб."
                return base
            chosen = max(cr, key=lambda x: x["score"])

        if chosen:
            base.update(chosen)
            base["context_direction"] = ctx_dir
            base["long"], base["short"] = lr, sr
            return base

    candidates = [lr, sr]

    def stage_wt(r):
        m = {"READY": 5, "15M_CONFIRMED": 4, "WAIT_PULLBACK": 4, "SWEPT": 3, "WAIT": 1}
        return m.get(r.get("stage"), 0)

    aligned_c = [x for x in candidates if x["direction"] == ctx_dir]
    pool = aligned_c if aligned_c else candidates

    chosen = max(pool, key=lambda x: (stage_wt(x), x.get("score", 0)))
    if chosen is not None:
        base.update({
            "stage": chosen.get("stage", "WAIT"), "direction": chosen.get("direction", ctx_dir),
            "score": chosen.get("score", 0), "reason": chosen.get("reason", ""),
            "entry": chosen.get("entry"), "sl": chosen.get("sl"), "tp": chosen.get("tp"),
            "tp_source": chosen.get("tp_source"), "rr": chosen.get("rr"),
            "sweep": chosen.get("sweep"), "confirmation_15m": chosen.get("confirmation_15m", False),
            "confirmation_15m_time": chosen.get("confirmation_15m_time"),
            "confirmation": chosen.get("confirmation"), "bos": chosen.get("bos", False),
            "ilm": chosen.get("ilm"), "sweep_extreme": chosen.get("sweep_extreme"),
            "tp_reason": chosen.get("tp_reason"), "geometry_valid": chosen.get("geometry_valid", False),
            "trend_activity": chosen.get("trend_activity", 0.0), "fvg_bonus": chosen.get("fvg_bonus", 0),
            "fvg_sweep": chosen.get("fvg_sweep", False), "fvg_entry": chosen.get("fvg_entry", False),
            "anti_fomo": chosen.get("anti_fomo", {}), "anti_fomo_reason": chosen.get("anti_fomo_reason", ""),
            "anti_fomo_ok": chosen.get("anti_fomo_ok", True), "d1_trend_ema": chosen.get("d1_trend_ema", "NEUTRAL"),
            "d1_ema_value": chosen.get("d1_ema_value"),
        })
    base["context_direction"] = ctx_dir
    return base


def analyze_sol(*args, **kwargs):
    return analyze(*args, **kwargs)


__all__ = [
    "STRATEGY_VERSION", "ALLOW_SHORT", "MIN_SCORE_READY",
    "REQUIRE_BOS_FOR_READY", "FIXED_RR", "SL_BUFFER_PCT",
    "MIN_SWEEP_DEPTH_PCT", "USE_ATR_SCALING",
    "MAX_ILM_AGE_FOR_ENTRY", "ATR_SL_MULT_SOFT",
    "ATR_SL_MAX_MULT", "RETEST_OFFSET_PCT", "ENABLE_ANTI_FOMO",
    "RSI_OVERBOUGHT_LONG", "RSI_OVERSOLD_SHORT",
    "STOCH_OVERBOUGHT_LONG", "STOCH_OVERSOLD_SHORT",
    "EMA_PULLBACK_PERIOD", "ATR_EXTENSION_MULT",
    "ATR_PULLBACK_TOL_MULT", "ANTI_FOMO_HARD_BLOCK",
    "ENABLE_D1_TREND_FILTER", "D1_EMA_PERIOD",
    "D1_TREND_BAND_PCT", "ENABLE_ATR_REGIME_FILTER",
    "ATR_REGIME_MIN", "VOLUME_CONFIRMATION_ENABLED",
    "ENABLE_SPACE_FILTER", "MIN_RR_SPACE_MULT",
    "ENABLE_SESSION_FILTER", "SESSION_BLOCK_START_HOUR",
    "SESSION_BLOCK_END_HOUR", "SESSION_FILTER_EXEMPT",
    "calculate_atr", "calculate_ema", "calculate_rsi",
    "calculate_stochastic", "get_1h_direction",
    "get_higher_tf_direction", "get_d1_trend_ema",
    "measure_trend_activity", "find_sweep",
    "confirmation_15m", "detect_5m_ilm", "calculate_entry",
    "calculate_stop", "calculate_tp_by_rr", "calculate_rr",
    "find_structural_sl", "validate_geometry",
    "check_anti_fomo", "check_space_to_target",
    "analyze", "analyze_sol",
]
