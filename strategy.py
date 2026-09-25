# -*- coding: utf-8 -*-
"""
TradeMind strategy v9.34.
v9.34 fixes:
- _score() пересчитан: 92 достижимо
- find_structural_sl: max(se, swing) для LONG / min(se, swing) для SHORT
- MIN_5M_ILM_SWEEP_DISTANCE_PCT: 5.0 -> 1.0
- ANTI_FOMO_HARD_BLOCK = False (warning, не блок)
- ENABLE_VOLATILITY_FILTER = False
- REQUIRE_BOS_FOR_READY = False
- conf_str реальный (не 0.8)
- переданный sweep используется, а не пересчитывается
- MIN_SCORE_READY снижен для SUI/APT
- Anti-FOMO в жёстких случаях требует pulled AND cooled
"""

from typing import Any, Dict, List, Optional, Tuple

STRATEGY_VERSION = "9.34"
ALLOW_SHORT = True
MAX_ILM_AGE_FOR_ENTRY = 6

ATR_SL_MULT_SOFT = 0.8
SL_BUFFER_PCT = 0.20
ATR_SL_MAX_MULT = 3.5

ENABLE_SESSION_FILTER = False
SESSION_BLOCK_START_HOUR = 2
SESSION_BLOCK_END_HOUR = 7
SESSION_FILTER_EXEMPT = {"BTCUSDT", "ETHUSDT"}

RETEST_OFFSET_PCT = 0.0

MIN_SCORE_READY = 90              # v9.34: было 92
REQUIRE_BOS_FOR_READY = False     # v9.34: было True
STRUCTURAL_SL_LOOKBACK_15M = 50
ENTRY_TOLERANCE_PCT = 1.0
FIXED_RR = 2.0

MIN_SWEEP_DEPTH_PCT = 0.12
MAX_SWEEP_AGE_1H = 24

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
MIN_BODY_RATIO_TRIGGER_5M = 0.40
VOLATILITY_ATR_SPIKE_MULT = 2.5
ENABLE_VOLATILITY_FILTER = False   # v9.34: было True

VOLUME_CONFIRMATION_ENABLED = False
VOLUME_CONFIRMATION_MULT = 1.2
VOLUME_CONFIRMATION_LOOKBACK = 20

MIN_BODY_RATIO = 0.35
MAX_5M_ILM_CANDLES = 60
MAX_15M_CONFIRM_CANDLES = 24
MAX_ILM_AGE_CANDLES_5M = 48

MIN_5M_RECOVERY_RATIO = 0.15
MIN_5M_ILM_SWEEP_DISTANCE_PCT = 1.0   # v9.34: было 5.0

MIN_TREND_ACTIVITY_READY = 0.35
COUNTER_TREND_MIN_SCORE = 90

FVG_TOLERANCE_PCT = 0.10
FVG_SWEEP_BONUS = 10
FVG_ENTRY_BONUS = 5
FVG_MAX_BONUS = 15

ILM_TRIGGER_WINDOW = 5

# v9.34: тиры подогнаны под пересчитанный _score
READY_PROMOTE_TIERS = (
    (92, 0.22, False),   # было (95, 0.20, False)
    (88, 0.25, False),   # было (92, 0.25, True)
    (85, 0.28, False),   # было (90, 0.30, True)
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
ANTI_FOMO_HARD_BLOCK = False       # v9.34: было True

ENABLE_D1_TREND_FILTER = False
D1_EMA_PERIOD = 50
D1_TREND_BAND_PCT = 1.0
ENABLE_ATR_REGIME_FILTER = False
ATR_REGIME_MIN = 1.08
ENABLE_SPACE_FILTER = False
MIN_RR_SPACE_MULT = 1.3


# ============================================================
# 10 COINS
# ============================================================

COIN_CONFIGS = {
    "XRPUSDT": {"ATR_SL_MULT": 1.5, "MIN_SWEEP_DEPTH_PCT": 0.15,
                "MAX_SL_DISTANCE_PCT": 3.5, "VOLATILITY_ATR_SPIKE_MULT": 2.0,
                "MIN_SCORE_READY": 91},
    "BCHUSDT": {"ATR_SL_MULT": 1.4, "MIN_SWEEP_DEPTH_PCT": 0.12,
                "MAX_SL_DISTANCE_PCT": 4.0, "VOLATILITY_ATR_SPIKE_MULT": 2.2,
                "MIN_SCORE_READY": 90},
    "APTUSDT": {"ATR_SL_MULT": 1.7, "MIN_SWEEP_DEPTH_PCT": 0.15,
                "MAX_SL_DISTANCE_PCT": 5.0, "VOLATILITY_ATR_SPIKE_MULT": 2.5,
                "MIN_SCORE_READY": 94},   # v9.34: было 96
    "SUIUSDT": {"ATR_SL_MULT": 1.8, "MIN_SWEEP_DEPTH_PCT": 0.15,
                "MAX_SL_DISTANCE_PCT": 5.5, "VOLATILITY_ATR_SPIKE_MULT": 3.0,
                "MIN_SCORE_READY": 94},   # v9.34: было 96
    "INJUSDT": {"ATR_SL_MULT": 1.6, "MIN_SWEEP_DEPTH_PCT": 0.14,
                "MAX_SL_DISTANCE_PCT": 5.0, "VOLATILITY_ATR_SPIKE_MULT": 2.5,
                "MIN_SCORE_READY": 92},
    "SOLUSDT": {"ATR_SL_MULT": 1.5, "MIN_SWEEP_DEPTH_PCT": 0.14,
                "MAX_SL_DISTANCE_PCT": 5.0, "VOLATILITY_ATR_SPIKE_MULT": 2.8,
                "MIN_SCORE_READY": 91},
    "ADAUSDT": {"ATR_SL_MULT": 1.3, "MIN_SWEEP_DEPTH_PCT": 0.14,
                "MAX_SL_DISTANCE_PCT": 3.5, "VOLATILITY_ATR_SPIKE_MULT": 2.3,
                "MIN_SCORE_READY": 90},
    "AVAXUSDT": {"ATR_SL_MULT": 1.5, "MIN_SWEEP_DEPTH_PCT": 0.15,
                 "MAX_SL_DISTANCE_PCT": 5.0, "VOLATILITY_ATR_SPIKE_MULT": 2.5,
                 "MIN_SCORE_READY": 91},
    "LINKUSDT": {"ATR_SL_MULT": 1.4, "MIN_SWEEP_DEPTH_PCT": 0.13,
                 "MAX_SL_DISTANCE_PCT": 4.5, "VOLATILITY_ATR_SPIKE_MULT": 2.3,
                 "MIN_SCORE_READY": 90},
    "ARBUSDT": {"ATR_SL_MULT": 1.4, "MIN_SWEEP_DEPTH_PCT": 0.15,
                "MAX_SL_DISTANCE_PCT": 5.0, "VOLATILITY_ATR_SPIKE_MULT": 2.5,
                "MIN_SCORE_READY": 91},
}


def get_config(symbol: str) -> dict:
    base = {
        "ATR_SL_MULT": ATR_SL_MULT,
        "MIN_SWEEP_DEPTH_PCT": MIN_SWEEP_DEPTH_PCT,
        "MAX_SL_DISTANCE_PCT": MAX_SL_DISTANCE_PCT,
        "VOLATILITY_ATR_SPIKE_MULT": VOLATILITY_ATR_SPIKE_MULT,
        "MIN_SCORE_READY": MIN_SCORE_READY,
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
# 15M CONFIRMATION
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
    v9.34: возвращает (ok, text, time, bos, strength), где strength —
    реальная сила подтверждения 0..1 (используется в _score).
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
    fallback = None
    for i in range(1, len(candidates)):
        c = candidates[i]
        br = _body_ratio(c)
        if br < MIN_BODY_RATIO: continue
        close = _c(c)
        if close is None: continue
        prev = candidates[i-1]
        prev_h = _h(prev); prev_l = _l(prev); prev_b = _body(prev)
        if direction == "LONG":
            if not _bull(c): continue
            highs = [_h(candidates[j]) for j in range(i-1)
                     if _is_local_high_15m(candidates, j)
                     and _h(candidates[j]) is not None]
            if not highs: continue
            ref = max(highs[-3:])
            if close > ref:
                strength = min(1.0, br * 1.4)
                return True, "15M BOS", _t(c), True, strength
            engulf = (prev_h is not None and close > prev_h
                      and _body(c) > prev_b)
            if engulf and fallback is None:
                strength = min(1.0, br * 1.0)
                fallback = (True, "15M engulf", _t(c), False, strength)
        else:
            if not _bear(c): continue
            lows = [_l(candidates[j]) for j in range(i-1)
                    if _is_local_low_15m(candidates, j)
                    and _l(candidates[j]) is not None]
            if not lows: continue
            ref = min(lows[-3:])
            if close < ref:
                strength = min(1.0, br * 1.4)
                return True, "15M BOS", _t(c), True, strength
            engulf = (prev_l is not None and close < prev_l
                      and _body(c) > prev_b)
            if engulf and fallback is None:
                strength = min(1.0, br * 1.0)
                fallback = (True, "15M engulf", _t(c), False, strength)
    if fallback
