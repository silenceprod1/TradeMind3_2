"""TradeMind 3.2 strategy engine: 1H context -> major liquidity -> 15M zone -> sweep -> 5M confirmation -> one TP at 1:2."""
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, List

@dataclass
class Setup:
    status: str = "WAIT"
    direction: Optional[str] = None
    score: int = 0
    reason: str = ""
    zone_low: Optional[float] = None
    zone_high: Optional[float] = None
    entry: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    rr: float = 2.0
    one_tp: bool = True
    liquidity_type: Optional[str] = None
    confirmation: Optional[str] = None
    order_flow: Optional[str] = None
    def to_dict(self): return asdict(self)

def f(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def val(c, key, idx):
    if isinstance(c, dict): return f(c.get(key, c.get(key[0], None)))
    return f(c[idx]) if isinstance(c, (list, tuple)) and len(c)>idx else None

def o(c): return val(c,"open",1)
def h(c): return val(c,"high",2)
def l(c): return val(c,"low",3)
def cl(c): return val(c,"close",4)

def last(c,n): return c[-n:] if len(c)>=n else c

def major_levels(candles, lookback=48):
    d=last(candles,lookback); hs=[h(x) for x in d if h(x) is not None]; ls=[l(x) for x in d if l(x) is not None]
    return (max(hs),min(ls)) if hs and ls else (None,None)

def context_15m(candles):
    d=last(candles,8)
    if len(d)<4:return "neutral"
    hs=[h(x) for x in d]; ls=[l(x) for x in d]
    if any(x is None for x in hs+ls):return "neutral"
    m=len(d)//2
    if max(hs[m:])>max(hs[:m]) and min(ls[m:])>min(ls[:m]):return "bullish"
    if max(hs[m:])<max(hs[:m]) and min(ls[m:])<min(ls[:m]):return "bearish"
    return "neutral"

def confirm_5m(candles,direction):
    d=last(candles,6)
    if len(d)<4:return False,None
    oo=[o(x) for x in d]; hh=[h(x) for x in d]; ll=[l(x) for x in d]; cc=[cl(x) for x in d]
    if any(x is None for x in oo+hh+ll+cc):return False,None
    O,H,L,C=oo[-1],hh[-1],ll[-1],cc[-1]; ph=max(hh[:-1]); pl=min(ll[:-1]); rng=max(H-L,1e-9); body=abs(C-O)
    if direction=="LONG":
        if C>O and C>ph and body/rng>=.45:return True,"5M bullish displacement / micro-structure break"
        if C>O and L<pl and C>L+rng*.55:return True,"5M bullish rejection after downside sweep"
    else:
        if C<O and C<pl and body/rng>=.45:return True,"5M bearish displacement / micro-structure break"
        if C<O and H>ph and C<H-rng*.55:return True,"5M bearish rejection after upside sweep"
    return False,None

def flow_check(flow,direction):
    if not flow:return None,None
    a=str(flow.get("absorption","")).lower(); d=f(flow.get("delta")); cv=f(flow.get("cvd_change"))
    if direction=="LONG":
        if a in {"buyers","buyer","buy"}:return True,"buyer absorption"
        if d is not None and d>0:return True,"positive delta"
        if cv is not None and cv>0:return True,"rising CVD"
    else:
        if a in {"sellers","seller","sell"}:return True,"seller absorption"
        if d is not None and d<0:return True,"negative delta"
        if cv is not None and cv<0:return True,"falling CVD"
    return False,"order flow does not support direction"

def zone(level,width=.30):
    width=min(max(f(width) or .30,.20),.60); return level-width,level+width

def trade(direction,entry,sl):
    risk=abs(entry-sl)
    if risk<=0:return None,None
    return round(entry,4),round(entry+(2*risk if direction=="LONG" else -2*risk),4)

def analyze(candles_1h:List[Any],candles_15m:List[Any],candles_5m:List[Any],current_price:float,order_flow:Optional[Dict[str,Any]]=None,sweep:Optional[Dict[str,Any]]=None):
    p=f(current_price); r=Setup()
    if p is None or not candles_1h or not candles_15m or not candles_5m:
        r.reason="ÐÐµÐ´Ð¾ÑÑÐ°ÑÐ¾ÑÐ½Ð¾ ÑÑÐ½Ð¾ÑÐ½ÑÑ Ð´Ð°Ð½Ð½ÑÑ."; return r.to_dict()
    hi,lo=major_levels(candles_1h); ctx=context_15m(candles_15m)
    if lo is not None: r.zone_low=round(lo-.30,4)
    if hi is not None: r.zone_high=round(hi+.30,4)
    # Critical anti-chasing rule: no explicit sweep = no trade.
    if not sweep or not sweep.get("swept"):
        r.score=25 if ctx=="neutral" else 35
        r.reason="ÐÐµÑ Ð¿Ð¾Ð´ÑÐ²ÐµÑÐ¶Ð´ÐµÐ½Ð½Ð¾Ð³Ð¾ sweep ÐºÑÑÐ¿Ð½Ð¾Ð¹ Ð»Ð¸ÐºÐ²Ð¸Ð´Ð½Ð¾ÑÑÐ¸. Ð ÑÐµÑÐµÐ´Ð¸Ð½Ðµ Ð´Ð¸Ð°Ð¿Ð°Ð·Ð¾Ð½Ð° Ð½Ðµ Ð²ÑÐ¾Ð´Ð¸Ð¼."
        return r.to_dict()
    direction=str(sweep.get("direction","")).upper(); level=f(sweep.get("level")); strength=f(sweep.get("strength"))
    if direction not in {"LONG","SHORT"} or level is None:
        r.reason="Sweep Ð½Ðµ ÑÐ¾Ð´ÐµÑÐ¶Ð¸Ñ ÐºÐ¾ÑÑÐµÐºÑÐ½Ð¾Ð³Ð¾ Ð½Ð°Ð¿ÑÐ°Ð²Ð»ÐµÐ½Ð¸Ñ/ÑÑÐ¾Ð²Ð½Ñ."; return r.to_dict()
    if strength is not None and strength<.60:
        r.score=40; r.reason="Sweep ÐµÑÑÑ, Ð½Ð¾ Ð¾Ð½ Ð½ÐµÐ´Ð¾ÑÑÐ°ÑÐ¾ÑÐ½Ð¾ ÑÐ¸Ð»ÑÐ½ÑÐ¹."; return r.to_dict()
    if abs(p-level)>1.50:
        r.score=35; r.reason="ÐÐ¾ÑÐ»Ðµ sweep ÑÐµÐ½Ð° ÑÐ¶Ðµ ÑÐ»Ð¸ÑÐºÐ¾Ð¼ Ð´Ð°Ð»ÐµÐºÐ¾ â Ð´Ð²Ð¸Ð¶ÐµÐ½Ð¸Ðµ Ð½Ðµ Ð´Ð¾Ð³Ð¾Ð½ÑÐµÐ¼."; return r.to_dict()
    ok,conf=confirm_5m(candles_5m,direction)
    r.direction=direction; r.liquidity_type=sweep.get("liquidity_type","major liquidity"); r.confirmation=conf
    if not ok:
        r.score=55; r.reason="Sweep ÐµÑÑÑ, Ð½Ð¾ Ð½ÐµÑ Ð¿Ð¾Ð´ÑÐ²ÐµÑÐ¶Ð´ÐµÐ½Ð¸Ñ Ð½Ð° 5M â Ð½ÐµÑ Ð²ÑÐ¾Ð´Ð°."; return r.to_dict()
    fok,ft=flow_check(order_flow,direction); r.order_flow=ft if ft else "Ð½ÐµÑ Ð´Ð°Ð½Ð½ÑÑ"
    if fok is False:
        r.score=60; r.reason="5M Ð¿Ð¾Ð´ÑÐ²ÐµÑÐ¶Ð´ÐµÐ½Ð¸Ðµ ÐµÑÑÑ, Ð½Ð¾ order flow Ð½Ðµ Ð¿Ð¾Ð´Ð´ÐµÑÐ¶Ð¸Ð²Ð°ÐµÑ Ð½Ð°Ð¿ÑÐ°Ð²Ð»ÐµÐ½Ð¸Ðµ."; return r.to_dict()
    score=78
    if (direction=="LONG" and ctx=="bullish") or (direction=="SHORT" and ctx=="bearish"):score+=7
    elif ctx not in {"neutral",direction.lower()}:score-=4
    if fok is True:score+=8
    entry=p; sl=level-.10 if direction=="LONG" else level+.10; entry,tp=trade(direction,entry,sl)
    r.status=direction; r.score=min(score,100); r.entry=entry; r.sl=round(sl,4); r.tp=tp; r.reason="ÐÑÑÐ¿Ð½Ð°Ñ Ð»Ð¸ÐºÐ²Ð¸Ð´Ð½Ð¾ÑÑÑ â sweep â 5M confirmation â Ð²ÑÐ¾Ð´."
    return r.to_dict()

def analyze_sol(candles_1h,candles_15m,candles_5m,current_price,order_flow=None,sweep=None):
    return analyze(candles_1h,candles_15m,candles_5m,current_price,order_flow,sweep)