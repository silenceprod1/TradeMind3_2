# -*- coding: utf-8 -*-
"""
TradeMind strategy v9.41.
v9.41 fixes (по модели ILM из видео):
- Инверсия FVG как триггер подтверждения (альтернатива BOS)
- V-образный trigger: body_ratio >= 0.55, close > max(prev 2 highs)
- ОТТ-фильтр: блок азиатской сессии 2:00-7:00 UTC (для крипты)
"""

from typing import Any, Dict, List, Optional, Tuple

STRATEGY_VERSION = "9.41"
ALLOW_SHORT = True
MAX_ILM_AGE_FOR_ENTRY = 3

ATR_SL_MULT_SOFT = 0.8
SL_BUFFER_PCT = 0.20
ATR_SL_MAX_MULT = 3.5

# v9.41: ОТТ — оптимальное торговое время
ENABLE_SESSION_FILTER = True       # было False
SESSION_BLOCK_START_HOUR = 2       # блок с 2:00 UTC
SESSION_BLOCK_END_HOUR = 7         # до 7:00 UTC (Лондон открытие)
SESSION_FILTER_EXEMPT = {"BTCUSDT", "ETHUSDT"}

RETEST_OFFSET_PCT = 0.0

MIN_SCORE_READY = 78
REQUIRE_BOS_FOR_READY = False
STRUCTURAL_SL_LOOKBACK_15M = 50
ENTRY_TOLERANCE_PCT = 0.5
FIXED_RR = 2.0

MIN_SWEEP_DEPTH_PCT = 0.12
MAX_SWEEP_AGE_1H = 6

USE_ATR_SCALING = True
ATR_SL_MULT = 1.4
MIN_SL_DISTANCE_PCT = 0.35
MAX_SL_DISTANCE_PCT = 4.5

ENABLE_SIDEWAYS_FILTER = False
ENABLE_POSITION_FILTER = False
ENABLE_D1_BLOCK = False

SL_USE_1H_SWINGS = True
SL_USE_SWEEP_EXTREME = True
MIN_SL_ATR_MULT = 1.2

# v9.41: V-образный trigger
MIN_BODY_RATIO_TRIGGER_5M = 0.55   # было 0.40

VOLATILITY_ATR_SPIKE_MULT = 2.5
ENABLE_VOLATILITY_FILTER = False

VOLUME_CONFIRMATION_ENABLED = False
VOLUME_CONFIRMATION_MULT = 1.2
VOLUME_CONFIRMATION_LOOKBACK = 20

MIN_BODY_RATIO = 0.35
MAX_5M_ILM_CANDLES = 60
MAX_15M_CONFIRM_CANDLES = 24
MAX_ILM_AGE_CANDLES_5M = 48

MIN_5M_RECOVERY_RATIO = 0.30
MAX_5M_RECOVERY_RATIO = 1.30
MIN_5M_ILM_SWEEP_DISTANCE_PCT = 1.0

MIN_TREND_ACTIVITY_READY = 0.45
COUNTER_TREND_MIN_SCORE = 78

FVG_TOLERANCE_PCT = 0.10
FVG_SWEEP_BONUS = 10
FVG_ENTRY_BONUS = 5
FVG_MAX_BONUS = 15

ILM_TRIGGER_WINDOW = 5

READY_PROMOTE_TIERS = (
    (88, 0.30, False),
    (84, 0.32, False),
    (80, 0.35, False),
)

ENABLE_ANTI_FOMO = True
RSI_PERIOD = 14
STOCH_PERIOD = 14
STOCH_SMOOTH_K = 3
STOCH_SMOOTH_D = 3
RSI_OVERBOUGHT_LONG = 70.0
RSI_OVERSOLD_SHORT = 30.0
STOCH_OVERBOUGHT_LONG = 80.0
STOCH_OVERSOLD_SHORT = 20.0
ANTI_FOMO_RSI_COOL_LONG = 65.0
ANTI_FOMO_RSI_COOL_SHORT = 35.0
ANTI_FOMO_STOCH_COOL_LONG = 80.0
ANTI_FOMO_STOCH_COOL_SHORT = 20.0
EMA_PULLBACK_PERIOD = 21
ATR_EXTENSION_MULT = 1.0
ATR_PULLBACK_TOL_MULT = 0.30
ANTI_FOMO_HARD_BLOCK = False

ENABLE_D1_TREND_FILTER = False
D1_EMA_PERIOD = 50
D1_TREND_BAND_PCT = 1.0
ENABLE_ATR_REGIME_FILTER = False
ATR_REGIME_MIN = 1.08
ENABLE_SPACE_FILTER = False
MIN_RR_SPACE_MULT = 1.3

MIN_LEVEL_STRENGTH = 70.0


# ============================================================
# 10 COINS
# ============================================================

COIN_CONFIGS = {
    "XRPUSDT": {"ATR_SL_MULT": 1.5, "MIN_SWEEP_DEPTH_PCT": 0.15,
                "MAX_SL_DISTANCE_PCT": 3.5, "VOLATILITY_ATR_SPIKE_MULT": 2.0,
                "MIN_SCORE_READY": 80, "MIN_LEVEL_STRENGTH": 70},
    "BCHUSDT": {"ATR_SL_MULT": 1.4, "MIN_SWEEP_DEPTH_PCT": 0.12,
                "MAX_SL_DISTANCE_PCT": 4.0, "VOLATILITY_ATR_SPIKE_MULT": 2.2,
                "MIN_SCORE_READY": 78, "MIN_LEVEL_STRENGTH": 70},
    "APTUSDT": {"ATR_SL_MULT": 1.7, "MIN_SWEEP_DEPTH_PCT": 0.15,
                "MAX_SL_DISTANCE_PCT": 5.0, "VOLATILITY_ATR_SPIKE_MULT": 2.5,
                "MIN_SCORE_READY": 82, "MIN_LEVEL_STRENGTH": 75},
    "SUIUSDT": {"ATR_SL_MULT": 1.8, "MIN_SWEEP_DEPTH_PCT": 0.15,
                "MAX_SL_DISTANCE_PCT": 5.5, "VOLATILITY_ATR_SPIKE_MULT": 3.0,
                "MIN_SCORE_READY": 82, "MIN_LEVEL_STRENGTH": 75},
    "INJUSDT": {"ATR_SL_MULT": 1.6, "MIN_SWEEP_DEPTH_PCT": 0.14,
                "MAX_SL_DISTANCE_PCT": 5.0, "VOLATILITY_ATR_SPIKE_MULT": 2.5,
                "MIN_SCORE_READY": 81, "MIN_LEVEL_STRENGTH": 70},
    "SOLUSDT": {"ATR_SL_MULT": 1.5, "MIN_SWEEP_DEPTH_PCT": 0.14,
                "MAX_SL_DISTANCE_PCT": 5.0, "VOLATILITY_ATR_SPIKE_MULT": 2.8,
                "MIN_SCORE_READY": 80, "MIN_LEVEL_STRENGTH": 70},
    "ADAUSDT": {"ATR_SL_MULT": 1.3, "MIN_SWEEP_DEPTH_PCT": 0.14,
                "MAX_SL_DISTANCE_PCT": 3.5, "VOLATILITY_ATR_SPIKE_MULT": 2.3,
                "MIN_SCORE_READY": 78, "MIN_LEVEL_STRENGTH": 65},
    "AVAXUSDT": {"ATR_SL_MULT": 1.5, "MIN_SWEEP_DEPTH_PCT": 0.15,
                 "MAX_SL_DISTANCE_PCT": 5.0, "VOLATILITY_ATR_SPIKE_MULT": 2.5,
                 "MIN_SCORE_READY": 80, "MIN_LEVEL_STRENGTH": 70},
    "LINKUSDT": {"ATR_SL_MULT": 1.4, "MIN_SWEEP_DEPTH_PCT": 0.13,
                 "MAX_SL_DISTANCE_PCT": 4.5, "VOLATILITY_ATR_SPIKE_MULT": 2.3,
                 "MIN_SCORE_READY": 78, "MIN_LEVEL_STRENGTH": 65},
    "ARBUSDT": {"ATR_SL_MULT": 1.4, "MIN_SWEEP_DEPTH_PCT": 0.15,
                "MAX_SL_DISTANCE_PCT": 5.0, "VOLATILITY_ATR_SPIKE_MULT": 2.5,
                "MIN_SCORE_READY": 80, "MIN_LEVEL_STRENGTH": 70},
}


def get_config(symbol: str) -> dict:
    base = {
        "ATR_SL_MULT": ATR_SL_MULT,
        "MIN_SWEEP_DEPTH_PCT": MIN_SWEEP_DEPTH_PCT,
        "MAX_SL_DISTANCE_PCT": MAX_SL_DISTANCE_PCT,
        "VOLATILITY_ATR_SPIKE_MULT": VOLATILITY_ATR_SPIKE_MULT,
        "MIN_SCORE_READY": MIN_SCORE_READY,
        "MIN_LEVEL_STRENGTH": MIN_LEVEL_STRENGTH,
    }
    if symbol and symbol in COIN_CONFIGS:
        base.update(COIN_CONFIGS[symbol])
    return base


# ============================================================
# CANDLE HELPERS
# ============================================================

def _f(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def _v(candle, key, default=None):
    if not isinstance(candle, dict): return default
    value = candle.get(key)
    if value is None:
        aliases = {"open": "o", "high": "h", "low": "l",
                   "close": "c", "open_time": "time", "volume": "v"}
        alias = aliases.get(key)
        if alias: value = candle.get(alias)
    if value is None: return default
    conv = _f(value)
    return conv if conv is not None else default


def _o(c): return _v(c, "open")
def _h(c): return _v(c, "high")
def _l(c): return _v(c, "low")
def _c(c): return _v(c, "close")
def _t(c): return _v(c, "open_time")
def _vol(c): return _v(c, "volume") or 0.0


def _body(c):
    o, cl = _o(c), _c(c)
    return abs(cl - o) if o is not None and cl is not None else 0.0


def _range(c):
    h, l = _h(c), _l(c)
    return max(0.0, h - l) if h is not None and l is not None else 0.0


def _body_ratio(c):
    r = _range(c)
    return _body(c) / r if r > 0 else 0.0


def _bull(c):
    o, cl = _o(c), _c(c)
    return cl > o if o is not None and cl is not None else False


def _bear(c):
    o, cl = _o(c), _c(c)
    return cl < o if o is not None and cl is not None else False


def _dist_pct(a, b):
    a, b = _f(a), _f(b)
    if a is None or b is None or b == 0: return None
    return abs(a - b) / abs(b) * 100


# ============================================================
# INDICATORS
# ============================================================

def calculate_atr(candles, period=14):
    if not candles or len(candles) < period + 1: return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = _h(candles[i]), _l(candles[i]), _c(candles[i-1])
        if h is None or l is None or pc is None: continue
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period if len(trs) >= period else None


def calculate_ema(candles, period=21):
    if not candles or period <= 0: return None
    closes = [x for x in [_c(c) for c in candles] if x is not None]
    if len(closes) < period: return None
    k = 2.0 / (period + 1.0)
    ema = sum(closes[:period]) / period
    for i in range(period, len(closes)): ema = closes[i] * k + ema * (1.0 - k)
    return ema


def calculate_rsi(candles, period=14):
    if not candles or period <= 0: return None
    closes = [x for x in [_c(c) for c in candles] if x is not None]
    if len(closes) < period + 1: return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0.0)); losses.append(max(-d, 0.0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0: return 100.0
    return 100.0 - (100.0 / (1.0 + avg_g / avg_l))


def calculate_stochastic(candles, k_period=14, smooth_k=3, smooth_d=3):
    if not candles or len(candles) < k_period + smooth_k: return None, None
    raw_k = []
    for i in range(k_period - 1, len(candles)):
        window = candles[i - k_period + 1:i + 1]
        highs = [_h(c) for c in window if _h(c) is not None]
        lows = [_l(c) for c in window if _l(c) is not None]
        if not highs or not lows: continue
        hh, ll, cl = max(highs), min(lows), _c(candles[i])
        if cl is None: continue
        raw_k.append(50.0 if hh == ll else (cl - ll) / (hh - ll) * 100.0)
    if len(raw_k) < smooth_k: return None, None
    ks = [sum(raw_k[i - smooth_k + 1:i + 1]) / smooth_k
          for i in range(smooth_k - 1, len(raw_k))]
    if not ks: return None, None
    k_val = ks[-1]
    d_val = sum(ks[-smooth_d:]) / smooth_d if len(ks) >= smooth_d else None
    return k_val, d_val


def _avg_atr(candles, fast=14, slow=50):
    if not candles or len(candles) < slow + 5: return None, None
    return calculate_atr(candles, fast), calculate_atr(candles, slow)


def get_d1_trend_ema(candles_d1, price, period=None, band_pct=None):
    if period is None: period = D1_EMA_PERIOD
    if band_pct is None: band_pct = D1_TREND_BAND_PCT
    if not candles_d1 or len(candles_d1) < period + 2: return "NEUTRAL", None
    confirmed = candles_d1[:-1]
    if len(confirmed) < period: return "NEUTRAL", None
    ema = calculate_ema(confirmed, period)
    p = _f(price)
    if ema is None or ema <= 0 or p is None: return "NEUTRAL", None
    d = (p - ema) / ema * 100.0
    if d > band_pct: return "LONG", ema
    if d < -band_pct: return "SHORT", ema
    return "NEUTRAL", ema


# ============================================================
# SWINGS
# ============================================================

def _swing_high(c, i):
    if i < 2 or i >= len(c) - 2: return False
    cur = _h(c[i]); l1 = _h(c[i-1]); l2 = _h(c[i-2])
    r1 = _h(c[i+1]); r2 = _h(c[i+2])
    if any(x is None for x in (cur, l1, l2, r1, r2)): return False
    return cur > l1 and cur >= l2 and cur >= r1 and cur > r2


def _swing_low(c, i):
    if i < 2 or i >= len(c) - 2: return False
    cur = _l(c[i]); l1 = _l(c[i-1]); l2 = _l(c[i-2])
    r1 = _l(c[i+1]); r2 = _l(c[i+2])
    if any(x is None for x in (cur, l1, l2, r1, r2)): return False
    return cur < l1 and cur <= l2 and cur <= r1 and cur < r2


def _swing_highs(c):
    return [(i, _h(c[i])) for i in range(len(c))
            if _swing_high(c, i) and _h(c[i]) is not None]


def _swing_lows(c):
    return [(i, _l(c[i])) for i in range(len(c))
            if _swing_low(c, i) and _l(c[i]) is not None]


def get_1h_direction(candles):
    if not candles or len(candles) < 15: return "NEUTRAL"
    candles = candles[-60:]
    highs = _swing_highs(candles); lows = _swing_lows(candles)
    bull = False; bear = False
    if len(highs) >= 2 and len(lows) >= 2:
        bull = (highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1])
        bear = (highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1])
    if not bull and not bear:
        recent = candles[-8:]
        bb = sum(_body(c) for c in recent if _bull(c))
        sb = sum(_body(c) for c in recent if _bear(c))
        if bb > 0 and bb > sb * 1.4: bull = True
        elif sb > 0 and sb > bb * 1.4: bear = True
    if bull and not bear: return "LONG"
    if bear and not bull: return "SHORT"
    return "NEUTRAL"


def get_higher_tf_direction(c1h, c1d=None, c1w=None):
    return get_1h_direction(c1h)


# ============================================================
# LEVELS
# ============================================================

def _level_price(l):
    return _f(l.get("price")) if isinstance(l, dict) else _f(l)


def _level_side(l):
    if not isinstance(l, dict): return None
    return str(l.get("side") or l.get("direction") or "").upper()


def _level_type(l):
    if not isinstance(l, dict): return ""
    return str(l.get("type") or "").upper()


def _level_strength(l):
    if not isinstance(l, dict): return 0
    v = l.get("strength") or l.get("touches") or 0
    try: return float(v)
    except Exception: return 0


def _is_swept_level(l):
    if not isinstance(l, dict): return False
    return bool(l.get("swept") or l.get("taken")
                or l.get("used") or l.get("consumed"))


def _levels_for_dir(levels, direction):
    res = []
    exp = "SSL" if direction == "LONG" else "BSL"
    for lv in levels or []:
        price = _level_price(lv)
        if price is None: continue
        side = _level_side(lv); lt = _level_type(lv)
        if side == direction: res.append(lv)
        elif lt == exp: res.append(lv)
        elif lt.startswith(exp + "_"): res.append(lv)
    return res


def _opposite_levels(levels, direction):
    res = []
    exp = "BSL" if direction == "LONG" else "SSL"
    for lv in levels or []:
        if _level_price(lv) is None: continue
        lt = _level_type(lv); side = _level_side(lv)
        if lt == exp or lt.startswith(exp + "_") or side == exp:
            res.append(lv)
    return res


# ============================================================
# FVG
# ============================================================

def is_inside_fvg(price, fvgs, direction, tol=FVG_TOLERANCE_PCT):
    p = _f(price)
    if p is None or not fvgs: return False
    exp = "bullish" if direction == "LONG" else "bearish"
    for fvg in fvgs:
        if fvg.get("type") != exp: continue
        top = _f(fvg.get("top")); bottom = _f(fvg.get("bottom"))
        if top is None or bottom is None: continue
        t = top * tol / 100
        if (bottom - t) <= p <= (top + t): return True
    return False


def compute_fvg_bonus(sweep, entry, fvgs, direction):
    if not fvgs: return 0, False, False
    si = is_inside_fvg((sweep or {}).get("extreme"), fvgs, direction)
    ei = is_inside_fvg(entry, fvgs, direction)
    b = 0
    if si: b += FVG_SWEEP_BONUS
    if ei: b += FVG_ENTRY_BONUS
    return min(b, FVG_MAX_BONUS), si, ei


def check_space_to_target(entry, sl, direction, levels):
    if not ENABLE_SPACE_FILTER: return True, None, None
    e = _f(entry); s = _f(sl)
    if e is None or s is None: return True, None, None
    risk = abs(e - s)
    if risk <= 0: return True, None, None
    targets = _opposite_levels(levels or [], direction)
    if not targets: return True, None, None
    candidates = []
    for t in targets:
        tp = _level_price(t)
        if tp is None: continue
        if direction == "LONG" and tp > e: candidates.append(tp)
        elif direction == "SHORT" and tp < e: candidates.append(tp)
    if not candidates: return True, None, None
    if direction == "LONG":
        nearest = min(candidates); dist = (nearest - e) / risk
    else:
        nearest = max(candidates); dist = (e - nearest) / risk
    if dist < MIN_RR_SPACE_MULT:
        return False, round(dist, 3), nearest
    return True, round(dist, 3), nearest


# ============================================================
# SWEEP
# ============================================================

def _sweep_cand_score(candle, level, depth):
    strength = _level_strength(level)
    touches = 1
    if isinstance(level, dict): touches = level.get("touches", 1)
    return depth * 4.0 + strength / 20.0 + min(touches, 5) * 3.0


def _has_vol_conf(candles, idx, lookback=VOLUME_CONFIRMATION_LOOKBACK,
                  mult=VOLUME_CONFIRMATION_MULT):
    if not candles: return True
    if idx < 0 or idx >= len(candles): return True
    if idx < lookback: return True
    start = idx - lookback
    vols = [_vol(candles[i]) for i in range(start, idx)]
    vols = [v for v in vols if v > 0]
    if not vols: return True
    avg = sum(vols) / len(vols)
    if avg <= 0: return True
    cur = _vol(candles[idx])
    if cur <= 0: return True
    return cur >= avg * mult


def find_sweep(candles_1h, major_levels, direction, config=None):
    if config is None: config = {}
    min_depth = config.get("MIN_SWEEP_DEPTH_PCT", MIN_SWEEP_DEPTH_PCT)
    if direction not in ("LONG", "SHORT"): return None
    if not candles_1h or len(candles_1h) < 3: return None
    levels = _levels_for_dir(major_levels, direction)
    if not levels: return None
    recent = candles_1h[-MAX_SWEEP_AGE_1H:]
    candidates = []
    total = len(candles_1h)

    for idx in range(len(recent)):
        c = recent[len(recent) - 1 - idx]
        cidx = total - 1 - idx
        if VOLUME_CONFIRMATION_ENABLED:
            if not _has_vol_conf(candles_1h, cidx): continue
        for level in levels:
            if _is_swept_level(level): continue
            price = _level_price(level)
            if price is None: continue
            if direction == "LONG":
                low = _l(c); close = _c(c)
                if low is None or close is None: continue
                depth = (price - low) / price * 100
                if depth < min_depth: continue
                if not (low < price and close > price): continue
                op = _o(c) or close
                body = abs(close - op)
                wick = min(op, close) - low
                if not (wick > body or _bull(c)): continue
                inv = False; consec = 0
                for k in range(cidx + 1, total):
                    ca = candles_1h[k]; cc = _c(ca)
                    if cc is not None and cc < low:
                        consec += 1
                        if consec >= 2: inv = True; break
                    else: consec = 0
                if inv: continue
                t = level.get("touches", 1); s = level.get("strength", 0)
                candidates.append({
                    "swept": True, "direction": "LONG",
                    "level": price, "extreme": low,
                    "open_time": _t(c), "price": low,
                    "liquidity_type": "SSL",
                    "touches": t, "strength": s, "depth_pct": depth,
                    "_score": _sweep_cand_score(c, level, depth) - idx * 2.0,
                })
            else:
                high = _h(c); close = _c(c)
                if high is None or close is None: continue
                depth = (high - price) / price * 100
                if depth < min_depth: continue
                if not (high > price and close < price): continue
                op = _o(c) or close
                body = abs(close - op)
                wick = high - max(op, close)
                if not (wick > body or _bear(c)): continue
                inv = False; consec = 0
                for k in range(cidx + 1, total):
                    ca = candles_1h[k]; cc = _c(ca)
                    if cc is not None and cc > high:
                        consec += 1
                        if consec >= 2: inv = True; break
                    else: consec = 0
                if inv: continue
                t = level.get("touches", 1); s = level.get("strength", 0)
                candidates.append({
                    "swept": True, "direction": "SHORT",
                    "level": price, "extreme": high,
                    "open_time": _t(c), "price": high,
                    "liquidity_type": "BSL",
                    "touches": t, "strength": s, "depth_pct": depth,
                    "_score": _sweep_cand_score(c, level, depth) - idx * 2.0,
                })
    if not candidates: return None
    best = None; best_score = -1e9
    for cand in candidates:
        sc = cand["_score"]
        if sc > best_score: best_score = sc; best = cand
    if best is not None: best.pop("_score", None)
    return best


# ============================================================
# TREND ACTIVITY
# ============================================================

def measure_trend_activity(candles_1h, direction):
    if not candles_1h or len(candles_1h) < 15: return 0.0
    recent = candles_1h[-20:]
    total = 0.0; direc = 0.0
    for c in recent:
        b = _body(c); total += b
        if direction == "LONG" and _bull(c): direc += b
        elif direction == "SHORT" and _bear(c): direc += b
    return direc / total if total > 0 else 0.0


# ============================================================
# v9.41: ИНВЕРСИЯ FVG
# ============================================================

def _check_fvg_inversion_15m(c15, direction, from_idx):
    """
    v9.41: Инверсия FVG.
    Для LONG: цена закрылась ТЕЛОМ ВЫШЕ верхней границы bearish FVG.
    Для SHORT: цена закрылась ТЕЛОМ НИЖЕ нижней границы bullish FVG.
    """
    if not c15 or len(c15) < 5: return False, None
    lookback = c15[max(0, from_idx - 5):from_idx + 1]
    if len(lookback) < 3: return False, None
    for i in range(1, len(lookback) - 1):
        c1 = lookback[i - 1]
        c3 = lookback[i + 1]
        h1 = _h(c1); l1 = _l(c1)
        h3 = _h(c3); l3 = _l(c3)
        if h1 is None or l1 is None or h3 is None or l3 is None: continue
        if l3 > h1:
            # bullish FVG -> ищем инверсию вниз (для SHORT)
            if direction == "SHORT":
                # ищем свечу после c3, которая закрылась телом НИЖЕ h1
                for j in range(i + 2, len(lookback)):
                    cj = lookback[j]
                    cl = _c(cj)
                    if cl is not None and cl < h1:
                        return True, "FVG inv (bear)"
        if h3 < l1:
            # bearish FVG -> ищем инверсию вверх (для LONG)
            if direction == "LONG":
                for j in range(i + 2, len(lookback)):
                    cj = lookback[j]
                    cl = _c(cj)
                    if cl is not None and cl > l3:
                        return True, "FVG inv (bull)"
    return False, None


# ============================================================
# 15M CONFIRMATION (v9.41)
# ============================================================

def _is_local_high_15m(c, i):
    if i < 1 or i >= len(c) - 1: return False
    cur, l, r = _h(c[i]), _h(c[i-1]), _h(c[i+1])
    if cur is None or l is None or r is None: return False
    return cur >= l and cur > r


def _is_local_low_15m(c, i):
    if i < 1 or i >= len(c) - 1: return False
    cur, l, r = _l(c[i]), _l(c[i-1]), _l(c[i+1])
    if cur is None or l is None or r is None: return False
    return cur <= l and cur < r


def confirmation_15m(candles_15m, sweep, direction):
    """
    v9.41:
    - BOS (пробой референса)
    - ИЛИ инверсия FVG
    """
    if not sweep or not candles_15m:
        return False, None, None, False, 0.0
    if direction not in ("LONG", "SHORT"):
        return False, None, None, False, 0.0
    sweep_t = _f(sweep.get("open_time"))
    candidates = []
    for c in candles_15m:
        t = _t(c)
        if sweep_t is None: candidates.append(c)
        elif t is not None and t > sweep_t: candidates.append(c)
    candidates = candidates[-MAX_15M_CONFIRM_CANDLES:]
    if len(candidates) < 3:
        return False, None, None, False, 0.0

    # v9.41: сначала проверяем BOS
    for i in range(1, len(candidates)):
        c = candidates[i]
        br = _body_ratio(c)
        if br < 0.50: continue
        close = _c(c)
        if close is None: continue
        if direction == "LONG":
            if not _bull(c): continue
            highs = [_h(candidates[j]) for j in range(i-1)
                     if _is_local_high_15m(candidates, j)
                     and _h(candidates[j]) is not None]
            if not highs: continue
            ref = max(highs[-3:])
            if close > ref:
                strength = min(1.0, br * 1.2)
                return True, "15M BOS", _t(c), True, strength
        else:
            if not _bear(c): continue
            lows = [_l(candidates[j]) for j in range(i-1)
                    if _is_local_low_15m(candidates, j)
                    and _l(candidates[j]) is not None]
            if not lows: continue
            ref = min(lows[-3:])
            if close < ref:
                strength = min(1.0, br * 1.2)
                return True, "15M BOS", _t(c), True, strength

    # v9.41: если BOS не найден — ищем инверсию FVG
    inv_ok, inv_reason = _check_fvg_inversion_15m(
        candidates, direction, len(candidates) - 1)
    if inv_ok:
        last_c = candidates[-1]
        return True, inv_reason or "15M FVG inv", _t(last_c), False, 0.85

    return False, None, None, False, 0.0


# ============================================================
# 5M ILM (v9.41 — V-образный trigger)
# ============================================================

def _is_local_high(c, i):
    if i < 1 or i >= len(c) - 1: return False
    cur, l, r = _h(c[i]), _h(c[i-1]), _h(c[i+1])
    if cur is None or l is None or r is None: return False
    return cur >= l and cur > r


def _is_local_low(c, i):
    if i < 1 or i >= len(c) - 1: return False
    cur, l, r = _l(c[i]), _l(c[i-1]), _l(c[i+1])
    if cur is None or l is None or r is None: return False
    return cur <= l and cur < r


def _ilm_long(candles, i, sweep_lvl, sweep_ext, min_depth):
    m = candles[i]
    if not _is_local_low(candles, i): return None
    ml = _l(m); mh = _h(m)
    if ml is None or mh is None: return None
    before = candles[max(0, i-2):i]
    if not before: return None
    bl = [_l(x) for x in before if _l(x) is not None]
    if not bl: return None
    left_ref = min(bl)
    if left_ref <= ml: return None
    target = left_ref
    if target <= ml: return None
    m_range = target - ml
    if m_range <= 0: return None
    m_pct = m_range / target * 100
    if m_pct < min_depth: return None
    trig_idx = None
    end = min(len(candles), i + 1 + ILM_TRIGGER_WINDOW)
    for j in range(i + 1, end):
        trig = candles[j]; tc = _c(trig)
        if tc is None or not _bull(trig): continue
        if _body_ratio(trig) < MIN_BODY_RATIO_TRIGGER_5M: continue
        # v9.41: V-образный trigger — close выше max(prev 2 highs)
        if j >= 2:
            h1 = _h(candles[j-1]); h2 = _h(candles[j-2])
            if h1 is not None and h2 is not None:
                if tc <= max(h1, h2): continue
        if tc > mh:
            trig_idx = j; break
    if trig_idx is None: return None
    trig = candles[trig_idx]; tc = _c(trig)
    if tc is None: return None
    rec = (tc - ml) / m_range
    if rec < MIN_5M_RECOVERY_RATIO: return None
    if rec > MAX_5M_RECOVERY_RATIO: return None
    if sweep_lvl is not None:
        d = abs(ml - sweep_lvl) / sweep_lvl * 100
        if d > MIN_5M_ILM_SWEEP_DISTANCE_PCT: return None
    if sweep_ext is not None:
        lim = sweep_ext * (1 + MIN_5M_ILM_SWEEP_DISTANCE_PCT / 100)
        if ml > lim: return None
    return {"direction": "LONG", "extreme": ml,
            "trigger_time": _t(trig), "trigger_price": tc,
            "reason": "5M V-ILM", "recovery_ratio": rec,
            "manipulation_pct": m_pct,
            "age_candles": len(candles) - 1 - trig_idx,
            "_score": rec * 30 + _body_ratio(trig) * 20 + m_pct * 5}


def _ilm_short(candles, i, sweep_lvl, sweep_ext, min_depth):
    m = candles[i]
    if not _is_local_high(candles, i): return None
    mh = _h(m); ml = _l(m)
    if mh is None or ml is None: return None
    before = candles[max(0, i-2):i]
    if not before: return None
    bh = [_h(x) for x in before if _h(x) is not None]
    if not bh: return None
    left_ref = max(bh)
    if mh <= left_ref: return None
    target = left_ref
    if mh <= target: return None
    m_range = mh - target
    if m_range <= 0: return None
    m_pct = m_range / mh * 100
    if m_pct < min_depth: return None
    trig_idx = None
    end = min(len(candles), i + 1 + ILM_TRIGGER_WINDOW)
    for j in range(i + 1, end):
        trig = candles[j]; tc = _c(trig)
        if tc is None or not _bear(trig): continue
        if _body_ratio(trig) < MIN_BODY_RATIO_TRIGGER_5M: continue
        # v9.41: V-образный trigger — close ниже min(prev 2 lows)
        if j >= 2:
            l1 = _l(candles[j-1]); l2 = _l(candles[j-2])
            if l1 is not None and l2 is not None:
                if tc >= min(l1, l2): continue
        if tc < ml:
            trig_idx = j; break
    if trig_idx is None: return None
    trig = candles[trig_idx]; tc = _c(trig)
    if tc is None: return None
    rec = (mh - tc) / m_range
    if rec < MIN_5M_RECOVERY_RATIO: return None
    if rec > MAX_5M_RECOVERY_RATIO: return None
    if sweep_lvl is not None:
        d = abs(mh - sweep_lvl) / sweep_lvl * 100
        if d > MIN_5M_ILM_SWEEP_DISTANCE_PCT: return None
    if sweep_ext is not None:
        lim = sweep_ext * (1 - MIN_5M_ILM_SWEEP_DISTANCE_PCT / 100)
        if mh < lim: return None
    return {"direction": "SHORT", "extreme": mh,
            "trigger_time": _t(trig), "trigger_price": tc,
            "reason": "5M L-ILM", "recovery_ratio": rec,
            "manipulation_pct": m_pct,
            "age_candles": len(candles) - 1 - trig_idx,
            "_score": rec * 30 + _body_ratio(trig) * 20 + m_pct * 5}


def detect_5m_ilm(candles_5m, sweep, direction, conf_time=None, config=None):
    if config is None: config = {}
    min_depth = config.get("MIN_SWEEP_DEPTH_PCT", MIN_SWEEP_DEPTH_PCT)
    if not sweep: return False, None
    if direction not in ("LONG", "SHORT"): return False, None
    start = _f(conf_time) or _f(sweep.get("open_time"))
    candles = []
    for c in candles_5m or []:
        t = _t(c)
        if start is None: candles.append(c)
        elif t is not None and t > start: candles.append(c)
    candles = candles[-MAX_5M_ILM_CANDLES:]
    if len(candles) < 5: return False, None
    sl = _f(sweep.get("level")); se = _f(sweep.get("extreme"))
    cands = []
    for i in range(2, len(candles) - 2):
        if direction == "LONG": ilm = _ilm_long(candles, i, sl, se, min_depth)
        else: ilm = _ilm_short(candles, i, sl, se, min_depth)
        if ilm: cands.append(ilm)
    if not cands: return False, None
    best = None; best_score = -1e9
    for cand in cands:
        sc = cand["_score"]
        if sc > best_score: best_score = sc; best = cand
    if best is not None: best.pop("_score", None)
    return True, best


# ============================================================
# ENTRY / SL / TP
# ============================================================

def calculate_entry(ilm, price, direction):
    if not ilm: return None
    trig = _f(ilm.get("trigger_price"))
    if trig is None or trig <= 0: return None
    offset = RETEST_OFFSET_PCT / 100.0
    if offset <= 0: return trig
    if direction == "LONG": return trig * (1 - offset)
    if direction == "SHORT": return trig * (1 + offset)
    return trig


def find_structural_sl(candles_15m, direction, entry, ilm_ext,
                       sweep_ext=None, candles_1h=None):
    ef = _f(entry)
    if ef is None: return ilm_ext
    cands = []
    if candles_15m and len(candles_15m) >= 10:
        w = candles_15m[-STRUCTURAL_SL_LOOKBACK_15M:]
        if direction == "LONG":
            for _, p in _swing_lows(w): cands.append(p)
        elif direction == "SHORT":
            for _, p in _swing_highs(w): cands.append(p)
    if SL_USE_1H_SWINGS and candles_1h and len(candles_1h) >= 20:
        w1 = candles_1h[-60:]
        if direction == "LONG":
            for _, p in _swing_lows(w1): cands.append(p)
        elif direction == "SHORT":
            for _, p in _swing_highs(w1): cands.append(p)
    se = None
    if SL_USE_SWEEP_EXTREME:
        se = _f(sweep_ext)
    ie = _f(ilm_ext)
    cands = [c for c in cands if c is not None]
    if ie is not None: cands.append(ie)

    if direction == "LONG":
        below = [c for c in cands if c < ef]
        nearest_swing = max(below) if below else None
        if se is not None and se < ef:
            if nearest_swing is not None and nearest_swing > se:
                return nearest_swing
            return se
        if nearest_swing is not None:
            return nearest_swing
        return ilm_ext
    if direction == "SHORT":
        above = [c for c in cands if c > ef]
        nearest_swing = min(above) if above else None
        if se is not None and se > ef:
            if nearest_swing is not None and nearest_swing < se:
                return nearest_swing
            return se
        if nearest_swing is not None:
            return nearest_swing
        return ilm_ext
    return ilm_ext


def calculate_stop(entry, struct_level, direction, atr=None, config=None):
    if config is None: config = {}
    entry = _f(entry); level = _f(struct_level)
    if entry is None or level is None: return None
    max_d_pct = config.get("MAX_SL_DISTANCE_PCT", MAX_SL_DISTANCE_PCT)
    if USE_ATR_SCALING and atr is not None and atr > 0:
        min_d = atr * ATR_SL_MULT_SOFT; max_d = atr * ATR_SL_MAX_MULT
    else:
        min_d = entry * MIN_SL_DISTANCE_PCT / 100
        max_d = entry * max_d_pct / 100
    if direction == "LONG":
        sl = level * (1 - SL_BUFFER_PCT / 100)
        if (entry - sl) < min_d: sl = entry - min_d
        if (entry - sl) > max_d: sl = entry - max_d
        return sl if sl < entry else None
    if direction == "SHORT":
        sl = level * (1 + SL_BUFFER_PCT / 100)
        if (sl - entry) < min_d: sl = entry + min_d
        if (sl - entry) > max_d: sl = entry + max_d
        return sl if sl > entry else None
    return None


def calculate_tp_by_rr(entry, sl, direction, rr=FIXED_RR):
    entry = _f(entry); sl = _f(sl)
    if entry is None or sl is None: return None
    risk = abs(entry - sl)
    if risk <= 0: return None
    if direction == "LONG": return entry + rr * risk
    if direction == "SHORT": return entry - rr * risk
    return None


def calculate_rr(entry, sl, tp):
    entry = _f(entry); sl = _f(sl); tp = _f(tp)
    if entry is None or sl is None or tp is None: return None
    risk = abs(entry - sl); reward = abs(tp - entry)
    return reward / risk if risk > 0 else None


def validate_geometry(entry, sl, tp, direction):
    entry = _f(entry); sl = _f(sl); tp = _f(tp)
    if entry is None or sl is None or tp is None: return False
    if direction == "LONG": return sl < entry < tp
    if direction == "SHORT": return tp < entry < sl
    return False


# ============================================================
# ANTI-FOMO
# ============================================================

def check_anti_fomo(candles_15m, direction, price):
    if not ENABLE_ANTI_FOMO: return True, "anti_fomo disabled", {}
    if not candles_15m or len(candles_15m) < max(
            RSI_PERIOD, STOCH_PERIOD + 10, EMA_PULLBACK_PERIOD) + 5:
        return True, "anti_fomo: not enough data", {}
    rsi = calculate_rsi(candles_15m, RSI_PERIOD)
    k_val, d_val = calculate_stochastic(candles_15m, STOCH_PERIOD,
                                        STOCH_SMOOTH_K, STOCH_SMOOTH_D)
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
    if (rsi is None or k_val is None or ema is None
            or atr is None or atr <= 0 or p is None):
        return True, "anti_fomo: insufficient indicators", meta

    if direction == "LONG":
        ob = (rsi >= RSI_OVERBOUGHT_LONG and k_val >= STOCH_OVERBOUGHT_LONG)
        extended = p > ema + ATR_EXTENSION_MULT * atr
        pulled = p <= ema + ATR_PULLBACK_TOL_MULT * atr
        cooled = (rsi < ANTI_FOMO_RSI_COOL_LONG
                  or k_val < ANTI_FOMO_STOCH_COOL_LONG)
        meta.update({"ob": ob, "extended": extended,
                     "pulled_back": pulled, "cooled": cooled})
        if ob and extended:
            if pulled and cooled:
                return True, "anti_fomo LONG ok", meta
            return False, (f"Anti-FOMO LONG: RSI {rsi:.1f} ob, "
                           f"цена >EMA21+{ATR_EXTENSION_MULT}*ATR, "
                           f"нет отката+остывания."), meta
        if ob:
            if pulled or cooled:
                return True, "anti_fomo LONG ok (ob)", meta
            return False, (f"Anti-FOMO LONG: RSI {rsi:.1f}/Stoch "
                           f"{k_val:.1f} без остывания."), meta
        if extended:
            if pulled or cooled:
                return True, "anti_fomo LONG ok (ext)", meta
            return False, (f"Anti-FOMO LONG: цена растянута > "
                           f"{ATR_EXTENSION_MULT}*ATR без остывания."), meta
        return True, "anti_fomo LONG passed", meta

    if direction == "SHORT":
        os_ = (rsi <= RSI_OVERSOLD_SHORT and k_val <= STOCH_OVERSOLD_SHORT)
        extended = p < ema - ATR_EXTENSION_MULT * atr
        pulled = p >= ema - ATR_PULLBACK_TOL_MULT * atr
        cooled = (rsi > ANTI_FOMO_RSI_COOL_SHORT
                  or k_val > ANTI_FOMO_STOCH_COOL_SHORT)
        meta.update({"os": os_, "extended": extended,
                     "pulled_back": pulled, "cooled": cooled})
        if os_ and extended:
            if pulled and cooled:
                return True, "anti_fomo SHORT ok", meta
            return False, (f"Anti-FOMO SHORT: RSI {rsi:.1f} os, "
                           f"цена <EMA21-{ATR_EXTENSION_MULT}*ATR, "
                           f"нет отката+остывания."), meta
        if os_:
            if pulled or cooled:
                return True, "anti_fomo SHORT ok (os)", meta
            return False, (f"Anti-FOMO SHORT: RSI {rsi:.1f}/Stoch "
                           f"{k_val:.1f} без остывания."), meta
        if extended:
            if pulled or cooled:
                return True, "anti_fomo SHORT ok (ext)", meta
            return False, (f"Anti-FOMO SHORT: price extended > "
                           f"{ATR_EXTENSION_MULT}*ATR no cool."), meta
        return True, "anti_fomo SHORT passed", meta
    return True, "anti_fomo: no direction", meta


# ============================================================
# SCORE
# ============================================================

def _score(direction, ctx_dir, sweep, conf_str, bos, ilm,
           rr, maj_str, fvg_bonus, conf_text=""):
    score = 0

    if direction == ctx_dir:
        score += 12
    elif ctx_dir == "NEUTRAL":
        score += 6
    else:
        score += 3

    if sweep:
        depth = sweep.get("depth_pct", 0)
        if depth >= 0.35: score += 20
        elif depth >= 0.22: score += 16
        elif depth >= 0.15: score += 12
        else: score += 8

    # v9.41: BOS +3, FVG inv +3
    if conf_text == "15M BOS":
        score += 20
    elif "FVG inv" in (conf_text or ""):
        score += 18
    elif conf_str >= 0.75: score += 14
    elif conf_str >= 0.55: score += 11
    elif conf_str >= 0.40: score += 8
    else: score += 0

    if ilm:
        rec = ilm.get("recovery_ratio", 0)
        age = ilm.get("age_candles", 99)
        if 0.50 <= rec <= 1.10: base = 20
        elif 0.30 <= rec < 0.50: base = 15
        else: base = 10
        if age > 2: base -= 3
        elif age > 1: base -= 1
        score += max(0, base)

    if rr is not None and rr >= FIXED_RR:
        score += 12

    score += min(10, maj_str / 6.0)

    score += fvg_bonus

    return int(min(100, max(0, round(score))))


def _apply_ready_promote(result):
    if result.get("stage") != "15M_CONFIRMED": return result
    reason = result.get("reason", "")
    if "READY заблокирован" not in reason: return result
    if any(result.get(k) is None for k in ("entry", "sl", "tp", "rr")):
        return result
    score = int(result.get("score", 0))
    trend = float(result.get("trend_activity", 0.0))
    bos = bool(result.get("bos", False))
    for sc_min, tr_min, need_bos in READY_PROMOTE_TIERS:
        if score < sc_min: continue
        if trend < tr_min: continue
        if need_bos and not bos: continue
        result["stage"] = "READY"
        result["reason"] = (f"v9.41 promote: score={score} "
                            f"trend={trend:.2f} bos={bos}")
        result["_v941_promoted"] = True
        return result
    return result


# ============================================================
# SCENARIO
# ============================================================

def _analyze_scenario(c1h, c15, c5, price, levels, direction,
                      ctx_dir, d1_context=None, fvgs=None,
                      symbol=None, config=None, provided_sweep=None):
    if config is None: config = {}
    result = {
        "stage": "WAIT", "direction": direction, "score": 0,
        "reason": "", "entry": None, "sl": None, "tp": None,
        "tp_source": "fixed_rr", "rr": None, "sweep": None,
        "major_levels": levels or [], "confirmation_15m": False,
        "confirmation_15m_time": None, "confirmation": None,
        "bos": False, "ilm": None, "sweep_extreme": None,
        "tp_reason": None, "geometry_valid": False,
        "trend_activity": 0.0, "fvg_bonus": 0,
        "fvg_sweep": False, "fvg_entry": False,
        "sl_distance_pct": None, "atr_15m": None,
        "sl_source": None, "anti_fomo": {},
        "anti_fomo_reason": "", "anti_fomo_ok": True,
        "d1_trend_ema": "NEUTRAL", "d1_ema_value": None,
        "atr_regime": None, "space_ok": True,
        "space_r": None, "space_target": None,
    }
    if direction == "SHORT" and not ALLOW_SHORT:
        result["reason"] = "SHORT disabled"; return result
    price = _f(price)
    if price is None or not c1h or not c15 or not c5:
        result["reason"] = "Недостаточно данных."; return result

    # v9.41: ОТТ-фильтр (сессия)
    if ENABLE_SESSION_FILTER:
        _sym = symbol or ""
        if _sym not in SESSION_FILTER_EXEMPT:
            last_c = c1h[-1] if c1h else None
            t_ms = _t(last_c) if last_c else None
            if t_ms is not None:
                try:
                    import time as _time_mod
                    hour_utc = _time_mod.gmtime(t_ms / 1000.0).tm_hour
                    if SESSION_BLOCK_START_HOUR <= hour_utc < SESSION_BLOCK_END_HOUR:
                        result["score"] = 0
                        result["reason"] = f"OTT block ({hour_utc}h UTC)"
                        return result
                except Exception:
                    pass

    if ENABLE_ATR_REGIME_FILTER:
        atr_fast = calculate_atr(c1h, 14); atr_slow = calculate_atr(c1h, 50)
        if (atr_fast is not None and atr_slow is not None and atr_slow > 0):
            ratio = atr_fast / atr_slow
            result["atr_regime"] = round(ratio, 3)
            if ratio < ATR_REGIME_MIN:
                result["stage"] = "WAIT"; result["score"] = 10
                result["reason"] = (f"ATR-regime: {ratio:.2f} < "
                                    f"{ATR_REGIME_MIN} (боковик)")
                return result

    if ENABLE_D1_TREND_FILTER:
        candles_d1 = None
        if isinstance(d1_context, dict):
            candles_d1 = d1_context.get("candles_d1")
        d1_trend, d1_ema = get_d1_trend_ema(candles_d1 or [], price)
        result["d1_trend_ema"] = d1_trend
        if d1_ema is not None: result["d1_ema_value"] = round(d1_ema, 8)
        if d1_trend != "NEUTRAL" and d1_trend != direction:
            result["stage"] = "WAIT"; result["score"] = 5
            result["reason"] = (f"D1 EMA{D1_EMA_PERIOD}: {d1_trend} "
                                f"vs {direction} — contra")
            return result

    trend = measure_trend_activity(c1h, direction)
    result["trend_activity"] = round(trend, 3)

    lv = _levels_for_dir(levels, direction)
    if not lv:
        result["score"] = 20
        t = "SSL" if direction == "LONG" else "BSL"
        result["reason"] = f"Нет Major {t}."; return result

    min_str = config.get("MIN_LEVEL_STRENGTH", MIN_LEVEL_STRENGTH)
    strong_lv = [x for x in lv if _level_strength(x) >= min_str]
    if not strong_lv:
        result["score"] = 25
        result["reason"] = f"Уровни слабые (strength < {min_str})."
        return result
    lv = strong_lv

    sweep = provided_sweep
    if sweep is None or not isinstance(sweep, dict):
        sweep = find_sweep(c1h, lv, direction, config=config)
    else:
        if sweep.get("direction") != direction:
            sweep = find_sweep(c1h, lv, direction, config=config)

    result["sweep"] = sweep
    if sweep is None:
        result["score"] = 25
        t = "SSL sweep" if direction == "LONG" else "BSL sweep"
        result["reason"] = f"Ждём {t}."; return result

    result["stage"] = "SWEPT"
    result["sweep_extreme"] = sweep.get("extreme")

    conf_ok, conf_text, conf_t, bos, conf_str = confirmation_15m(
        c15, sweep, direction)
    result["confirmation_15m"] = conf_ok
    result["confirmation_15m_time"] = conf_t
    result["confirmation"] = conf_text
    result["bos"] = bos
    result["confirmation_strength"] = conf_str
    if not conf_ok:
        result["score"] = 50; result["reason"] = "Ждём 15M."; return result

    result["stage"] = "15M_CONFIRMED"

    ilm_ok, ilm = detect_5m_ilm(c5, sweep, direction, conf_t, config=config)
    result["ilm"] = ilm
    if not ilm_ok:
        result["score"] = 65; result["reason"] = "Ждём 5M ILM."; return result

    age = int(ilm.get("age_candles", 0))
    if age > MAX_ILM_AGE_CANDLES_5M:
        result["score"] = 65; result["reason"] = f"ILM устарел ({age})"; return result
    if age > MAX_ILM_AGE_FOR_ENTRY:
        result["score"] = 68
        result["reason"] = f"ILM старый ({age} > {MAX_ILM_AGE_FOR_ENTRY})"
        return result

    entry = calculate_entry(ilm, price, direction)
    if entry is None:
        result["score"] = 68; result["reason"] = "Нет Entry."; return result

    dist = _dist_pct(price, entry)
    if dist is None or dist > ENTRY_TOLERANCE_PCT:
        result["score"] = 68
        result["reason"] = f"Цена ушла на {dist:.2f}%"; return result

    ilm_ext = _f(ilm.get("extreme"))

    if ENABLE_VOLATILITY_FILTER:
        fa, sa = _avg_atr(c15, fast=14, slow=50)
        vol_mult = config.get("VOLATILITY_ATR_SPIKE_MULT",
                              VOLATILITY_ATR_SPIKE_MULT)
        if (fa is not None and sa is not None and sa > 0
                and fa > sa * vol_mult):
            result["score"] = 70; result["reason"] = "Volatility spike"
            return result

    atr_15m = calculate_atr(c15, 14)
    struct_lvl = find_structural_sl(
        c15, direction, entry, ilm_ext,
        sweep_ext=sweep.get("extreme") if sweep else None, candles_1h=c1h)

    sl = calculate_stop(entry, struct_lvl, direction,
                        atr=atr_15m, config=config)
    if sl is None:
        result["score"] = 68; result["reason"] = "Нет SL."; return result

    space_ok, space_r, space_target = check_space_to_target(
        entry, sl, direction, levels)
    result["space_ok"] = space_ok
    result["space_r"] = space_r
    result["space_target"] = (round(space_target, 8)
                              if space_target is not None else None)
    if not space_ok:
        result["stage"] = "WAIT"; result["score"] = 30
        result["reason"] = (f"Space filter: RR до сопротивления "
                            f"{space_r} < {MIN_RR_SPACE_MULT}")
        return result

    tp = calculate_tp_by_rr(entry, sl, direction, FIXED_RR)
    if tp is None:
        result["score"] = 70; result["reason"] = "Нет TP."; return result
    if not validate_geometry(entry, sl, tp, direction):
        result["score"] = 68; result["reason"] = "Геометрия сломана."
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
        "entry": round(entry, 8), "sl": round(sl, 8),
        "tp": round(tp, 8),
        "rr": round(calculate_rr(entry, sl, tp) or 0.0, 3),
        "tp_reason": f"Fixed RR 1:{FIXED_RR}",
    })
    rr = _f(result.get("rr"))
    try:
        sdp = abs(entry - sl) / entry * 100
        result["sl_distance_pct"] = round(sdp, 3)
        if atr_15m:
            result["atr_15m"] = round(atr_15m, 6)
            if abs(entry - sl) < atr_15m * ATR_SL_MULT_SOFT:
                result["sl_source"] = "atr_floor"
            else:
                result["sl_source"] = "structural"
    except Exception: pass

    try:
        ms = max([_level_strength(l) for l in lv] or [0])
    except Exception: ms = 0

    fb, fs, fe = compute_fvg_bonus(sweep, entry, fvgs or [], direction)
    result["fvg_bonus"] = fb; result["fvg_sweep"] = fs; result["fvg_entry"] = fe

    score = _score(direction, ctx_dir, sweep, conf_str, bos,
                   ilm, rr, ms, fb, conf_text=conf_text or "")
    result["score"] = score

    try:
        sweep_depth = sweep.get("depth_pct", 0) if sweep else 0
        ilm_rec = ilm.get("recovery_ratio", 0) if ilm else 0
        ilm_age = ilm.get("age_candles", 0) if ilm else 0
        ilm_manip = ilm.get("manipulation_pct", 0) if ilm else 0
        risk_pct_v = abs(entry - sl) / entry * 100 if entry else 0
        print(
            f"[SETUP] {symbol} {direction} score={score} "
            f"sweep_depth={sweep_depth:.2f} "
            f"conf={conf_text} conf_br={conf_str:.2f} bos={bos} "
            f"ilm_rec={ilm_rec:.2f} ilm_age={ilm_age} "
            f"ilm_manip={ilm_manip:.2f} "
            f"trend={trend:.2f} "
            f"entry={entry:.6f} sl={sl:.6f} tp={tp:.6f} "
            f"risk_pct={risk_pct_v:.2f} "
            f"maj_str={ms:.0f} fvg={fb}",
            flush=True,
        )
    except Exception:
        pass

    min_score = config.get("MIN_SCORE_READY", MIN_SCORE_READY)
    trend_ok = trend >= MIN_TREND_ACTIVITY_READY
    bos_ok = (not REQUIRE_BOS_FOR_READY) or bos
    ready_ok = (score >= min_score and trend_ok and bos_ok)
    if ready_ok:
        result["stage"] = "READY"
        result["reason"] = (f"Sweep→15M→5M ILM. Trend {trend:.2f}. "
                            f"RR {FIXED_RR}. BOS={bos}.")
        return result

    result["stage"] = "15M_CONFIRMED"
    blocks = []
    if score < min_score: blocks.append(f"score {score}")
    if not trend_ok: blocks.append(f"trend {trend:.2f}")
    if not bos_ok: blocks.append("no_bos")
    result["reason"] = "READY заблокирован: " + ", ".join(blocks)
    result = _apply_ready_promote(result)
    return result


# ============================================================
# MAIN ANALYZE
# ============================================================

def analyze(candles_1h, candles_15m, candles_5m,
            current_price, major_levels=None,
            sweep=None, order_flow=None,
            candles_1m=None, d1_context=None,
            fvgs=None, symbol=None):
    price = _f(current_price)
    ctx_dir = get_1h_direction(candles_1h)
    config = get_config(symbol)
    base = {
        "stage": "WAIT", "direction": ctx_dir,
        "context_direction": ctx_dir,
        "d1_trend": (d1_context or {}).get("trend", "NEUTRAL"),
        "d1_point_a": (d1_context or {}).get("point_a"),
        "d1_point_b": (d1_context or {}).get("point_b"),
        "score": 0, "reason": "", "entry": None, "sl": None,
        "tp": None, "tp_source": "fixed_rr", "rr": None,
        "sweep": None, "major_levels": major_levels or [],
        "confirmation_15m": False, "confirmation_15m_time": None,
        "confirmation": None, "bos": False, "ilm": None,
        "sweep_extreme": None, "tp_reason": None,
        "geometry_valid": False, "trend_activity": 0.0,
        "fvg_bonus": 0, "fvg_sweep": False, "fvg_entry": False,
        "long": None, "short": None,
        "anti_fomo": {}, "anti_fomo_reason": "", "anti_fomo_ok": True,
        "d1_trend_ema": "NEUTRAL", "d1_ema_value": None,
    }
    if price is None:
        base["reason"] = "Недостаточно данных."; return base
    if not candles_1h or not candles_15m or not candles_5m:
        base["reason"] = "Недостаточно данных."; return base

    lr = _analyze_scenario(candles_1h, candles_15m, candles_5m, price,
                           major_levels, "LONG", ctx_dir,
                           d1_context=d1_context, fvgs=fvgs,
                           symbol=symbol, config=config,
                           provided_sweep=sweep)
    sr = _analyze_scenario(candles_1h, candles_15m, candles_5m, price,
                           major_levels, "SHORT", ctx_dir,
                           d1_context=d1_context, fvgs=fvgs,
                           symbol=symbol, config=config,
                           provided_sweep=sweep)
    base["long"] = lr
    base["short"] = sr
    min_score = config.get("MIN_SCORE_READY", MIN_SCORE_READY)

    if ctx_dir == "NEUTRAL":
        best = lr if lr.get("score", 0) >= sr.get("score", 0) else sr
        base["score"] = best.get("score", 0)
        base["reason"] = "1H NEUTRAL — блок"
        base["stage"] = "WAIT"
        return base

    ready = []
    if lr.get("stage") == "READY" and lr.get("score", 0) >= min_score:
        ready.append(lr)
    if sr.get("stage") == "READY" and sr.get("score", 0) >= min_score:
        ready.append(sr)

    if ready:
        aligned = [x for x in ready if x["direction"] == ctx_dir]
        counter = [x for x in ready if x["direction"] != ctx_dir]
        chosen = None
        if aligned:
            best_sc = -1
            for a in aligned:
                if a["score"] > best_sc: best_sc = a["score"]; chosen = a
        elif counter:
            cr = [x for x in counter if x["score"] >= COUNTER_TREND_MIN_SCORE]
            if not cr:
                base["score"] = max(lr["score"], sr["score"])
                base["reason"] = "Counter-тренд слаб."; return base
            best_sc = -1
            for x in cr:
                if x["score"] > best_sc: best_sc = x["score"]; chosen = x
        if chosen:
            base.update(chosen)
            base["context_direction"] = ctx_dir
            base["long"] = lr; base["short"] = sr
            return base

    candidates = [lr, sr]

    def stage_wt(r):
        m = {"READY": 5, "15M_CONFIRMED": 4, "WAIT_PULLBACK": 4,
             "SWEPT": 3, "WAIT": 1}
        return m.get(r.get("stage"), 0)

    aligned_c = [x for x in candidates if x["direction"] == ctx_dir]
    pool = aligned_c if aligned_c else candidates

    chosen = None; best_k = None
    for x in pool:
        k = (stage_wt(x), x.get("score", 0))
        if best_k is None or k > best_k: best_k = k; chosen = x

    if chosen is not None:
        base.update({
            "stage": chosen.get("stage", "WAIT"),
            "direction": chosen.get("direction", ctx_dir),
            "score": chosen.get("score", 0),
            "reason": chosen.get("reason", ""),
            "entry": chosen.get("entry"), "sl": chosen.get("sl"),
            "tp": chosen.get("tp"), "tp_source": chosen.get("tp_source"),
            "rr": chosen.get("rr"), "sweep": chosen.get("sweep"),
            "confirmation_15m": chosen.get("confirmation_15m", False),
            "confirmation_15m_time": chosen.get("confirmation_15m_time"),
            "confirmation": chosen.get("confirmation"),
            "bos": chosen.get("bos", False), "ilm": chosen.get("ilm"),
            "sweep_extreme": chosen.get("sweep_extreme"),
            "tp_reason": chosen.get("tp_reason"),
            "geometry_valid": chosen.get("geometry_valid", False),
            "trend_activity": chosen.get("trend_activity", 0.0),
            "fvg_bonus": chosen.get("fvg_bonus", 0),
            "fvg_sweep": chosen.get("fvg_sweep", False),
            "fvg_entry": chosen.get("fvg_entry", False),
            "anti_fomo": chosen.get("anti_fomo", {}),
            "anti_fomo_reason": chosen.get("anti_fomo_reason", ""),
            "anti_fomo_ok": chosen.get("anti_fomo_ok", True),
            "d1_trend_ema": chosen.get("d1_trend_ema", "NEUTRAL"),
            "d1_ema_value": chosen.get("d1_ema_value"),
        })
    base["context_direction"] = ctx_dir
    return base


def analyze_sol(*args, **kwargs):
    return analyze(*args, **kwargs)


def generate_neurobro_report(result: dict, symbol: str,
                              risk_pct: float = 1.0) -> str:
    stage = result.get("stage", "WAIT")
    direction = result.get("direction", "NEUTRAL")
    score = result.get("score", 0)
    reason = result.get("reason", "")
    if stage == "READY" and direction in ("LONG", "SHORT"):
        emoji = "🟢 BUY" if direction == "LONG" else "🔴 SELL"
        entry = result.get("entry"); sl = result.get("sl"); tp = result.get("tp")
        if not all([entry, sl, tp]):
            return f"⚠️ {symbol}: READY но нет entry/sl/tp"
        risk = abs(entry - sl)
        if direction == "LONG":
            tp1 = entry + 1.8 * risk; tp2 = entry + 2.9 * risk
        else:
            tp1 = entry - 1.8 * risk; tp2 = entry - 2.9 * risk
        risk_pct_price = (risk / entry) * 100
        lines = [
            f"По ${symbol} сетап: {emoji}", "",
            f"📐 Направление: <b>{direction}</b>",
            f"⭐ Уверенность: <b>{score}/100</b>", "",
            f"<b>Вход:</b>  <code>{entry:.6f}</code>",
            f"<b>Стоп:</b>  <code>{sl:.6f}</code>  "
            f"({risk_pct_price:.2f}% риска)",
            f"<b>TP1:</b>   <code>{tp1:.6f}</code>  (1.8R)",
            f"<b>TP2:</b>   <code>{tp2:.6f}</code>  (2.9R)",
            f"<b>Таймфрейм:</b> Скальп/Интрадей", "",
            f"<b>Логика:</b> {reason}", "",
            f"💰 При риске {risk_pct:.1f}% депозита размер позиции = "
            f"<code>{risk_pct / risk_pct_price * 100:.1f}%</code> от депо.",
            "", "ℹ️ <i>При TP1 — закрой 50% и переведи стоп в БУ.</i>",
        ]
        return "\n".join(lines)
    if stage == "WAIT_PULLBACK":
        meta = result.get("anti_fomo") or {}
        lines = [f"🧲 ${symbol}: <b>СИГНАЛ ГОТОВ — ЖДЁМ ОТКАТ</b>", "",
                 f"📐 {direction}", f"⭐ Score: {score}/100"]
        if meta.get("rsi_15m") is not None:
            lines.append(f"RSI 15M: <b>{meta['rsi_15m']:.1f}</b>")
        if meta.get("stoch_k_15m") is not None:
            lines.append(f"Stoch 15M: <b>{meta['stoch_k_15m']:.1f}</b>")
        if meta.get("ema21_15m") is not None:
            lines.append(f"EMA21 15M: <code>{meta['ema21_15m']:.6f}</code>")
        if reason: lines.extend(["", f"ℹ️ {reason}"])
        lines.extend(["", "⏳ <b>НЕ ВХОДИМ СЕЙЧАС</b>",
                      "Ждём откат к EMA21 / остывание RSI/Stoch."])
        return "\n".join(lines)
    return f"⚪ ${symbol}: <b>{stage}</b> — {reason}"


__all__ = [
    "STRATEGY_VERSION", "ALLOW_SHORT", "MIN_SCORE_READY",
    "REQUIRE_BOS_FOR_READY", "FIXED_RR", "SL_BUFFER_PCT",
    "MIN_SWEEP_DEPTH_PCT", "USE_ATR_SCALING",
    "MAX_ILM_AGE_FOR_ENTRY", "ATR_SL_MULT_SOFT",
    "ATR_SL_MAX_MULT", "RETEST_OFFSET_PCT",
    "ENABLE_ANTI_FOMO", "RSI_OVERBOUGHT_LONG",
    "RSI_OVERSOLD_SHORT", "STOCH_OVERBOUGHT_LONG",
    "STOCH_OVERSOLD_SHORT", "EMA_PULLBACK_PERIOD",
    "ATR_EXTENSION_MULT", "ATR_PULLBACK_TOL_MULT",
    "ANTI_FOMO_HARD_BLOCK", "ENABLE_D1_TREND_FILTER",
    "D1_EMA_PERIOD", "D1_TREND_BAND_PCT",
    "ENABLE_ATR_REGIME_FILTER", "ATR_REGIME_MIN",
    "VOLUME_CONFIRMATION_ENABLED", "ENABLE_SPACE_FILTER",
    "MIN_RR_SPACE_MULT", "ENABLE_VOLATILITY_FILTER",
    "MIN_LEVEL_STRENGTH", "MAX_5M_RECOVERY_RATIO",
    "ENABLE_SESSION_FILTER", "SESSION_BLOCK_START_HOUR",
    "SESSION_BLOCK_END_HOUR", "SESSION_FILTER_EXEMPT",
    "COIN_CONFIGS", "get_config",
    "calculate_atr", "calculate_ema", "calculate_rsi",
    "calculate_stochastic", "get_1h_direction",
    "get_higher_tf_direction", "get_d1_trend_ema",
    "measure_trend_activity", "find_sweep",
    "confirmation_15m", "detect_5m_ilm",
    "calculate_entry", "calculate_stop",
    "calculate_tp_by_rr", "calculate_rr",
    "find_structural_sl", "validate_geometry",
    "check_anti_fomo", "check_space_to_target",
    "analyze", "analyze_sol", "generate_neurobro_report",
]
