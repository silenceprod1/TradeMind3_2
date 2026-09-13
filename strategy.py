# -*- coding: utf-8 -*-
"""TradeMind 3.2: major liquidity -> sweep -> 5M confirmation -> one TP 1:2."""

def _ctx(c):
    if len(c) < 8: return "neutral"
    a,b=c[-8:-4],c[-4:]
    ah,al=max(x["high"] for x in a),min(x["low"] for x in a)
    bh,bl=max(x["high"] for x in b),min(x["low"] for x in b)
    if bh>ah and bl>al: return "bullish"
    if bh<ah and bl<al: return "bearish"
    return "neutral"

def _confirm(c, direction):
    if len(c)<5: return False,"Недостаточно 5M данных"
    last,prev=c[-1],c[-5:-1]
    body=abs(last["close"]-last["open"]); rng=max(last["high"]-last["low"],1e-9)
    if direction=="LONG":
        if last["close"]>last["open"] and last["close"]>max(x["high"] for x in prev) and body/rng>=.45:
            return True,"5M bullish displacement"
        if last["close"]>last["open"] and last["low"]<min(x["low"] for x in prev) and last["close"]>last["low"]+rng*.55:
            return True,"5M bullish rejection"
    else:
        if last["close"]<last["open"] and last["close"]<min(x["low"] for x in prev) and body/rng>=.45:
            return True,"5M bearish displacement"
        if last["close"]<last["open"] and last["high"]>max(x["high"] for x in prev) and last["close"]<last["high"]-rng*.55:
            return True,"5M bearish rejection"
    return False,"Нет подтверждения на 5M"

def _flow(flow,direction):
    if not flow: return None,"order flow пока не подключён"
    a=str(flow.get("absorption","")).lower()
    d=flow.get("delta")
    if direction=="LONG":
        if a in ("buyer","buyers","buy"): return True,"buyer absorption"
        if d is not None and float(d)>0: return True,"positive delta"
    else:
        if a in ("seller","sellers","sell"): return True,"seller absorption"
        if d is not None and float(d)<0: return True,"negative delta"
    return False,"order flow против направления"

def analyze(candles_1h,candles_15m,candles_5m,current_price,sweep=None,order_flow=None,major_levels=None):
    price=float(current_price)
    r={"status":"WAIT","direction":None,"score":0,"reason":"",
       "entry":None,"sl":None,"tp":None,"rr":2.0,"one_tp":True,
       "liquidity_type":None,"confirmation":None,"order_flow":None,
       "context_1h":_ctx(candles_1h),"context_15m":_ctx(candles_15m),
       "major_levels":major_levels or []}
    if not sweep or not sweep.get("swept"):
        r["score"]=30; r["reason"]="Нет sweep крупной ликвидности — в середине не входим."; return r
    direction=sweep["direction"]; level=float(sweep["level"])
    ok,conf=_confirm(candles_5m,direction)
    r.update(direction=direction,liquidity_type=sweep.get("liquidity_type"),confirmation=conf)
    if not ok:
        r["score"]=55; r["reason"]="Sweep есть, но нет подтверждения → нет входа."; return r
    flow_ok,flow_text=_flow(order_flow,direction); r["order_flow"]=flow_text
    if flow_ok is False:
        r["score"]=60; r["reason"]="5M подтверждение есть, но order flow против направления."; return r
    score=82
    if (direction=="LONG" and r["context_1h"]=="bullish") or (direction=="SHORT" and r["context_1h"]=="bearish"): score+=6
    if (direction=="LONG" and r["context_15m"]=="bullish") or (direction=="SHORT" and r["context_15m"]=="bearish"): score+=5
    if flow_ok is True: score+=7
    r["score"]=min(score,100)
    buffer=max(price*.0008,.08)
    sl=level-buffer if direction=="LONG" else level+buffer
    risk=abs(price-sl)
    if risk<=0 or abs(price-level)>1.5:
        r["score"]=65; r["reason"]="Движение уже ушло от sweep — не догоняем."; return r
    tp=price+2*risk if direction=="LONG" else price-2*risk
    r.update(status=direction,entry=round(price,4),sl=round(sl,4),tp=round(tp,4),
             reason="Крупная ликвидность → sweep → 5M confirmation → вход.")
    return r

def analyze_sol(candles_1h,candles_15m,candles_5m,current_price,order_flow=None,sweep=None):
    return analyze(candles_1h,candles_15m,candles_5m,current_price,sweep=sweep,order_flow=order_flow)