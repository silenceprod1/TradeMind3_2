"""Market data layer for TradeMind 3.2.
Binance Spot is the structure/reference source.
"""
import requests

BASE_URL = "https://api.binance.com/api/v3"
SYMBOL = "SOLUSDT"

def _get(path, params):
    r = requests.get(BASE_URL + path, params=params, timeout=10)
    r.raise_for_status()
    return r.json()

def get_price(symbol=SYMBOL):
    data = _get("/ticker/price", {"symbol": symbol})
    return float(data["price"])

def get_klines(symbol=SYMBOL, interval="1h", limit=200):
    raw = _get("/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    return [
        {"open_time": x[0], "open": float(x[1]), "high": float(x[2]),
         "low": float(x[3]), "close": float(x[4]), "volume": float(x[5]),
         "close_time": x[6]}
        for x in raw
    ]

def get_market_data(symbol=SYMBOL):
    return {
        "symbol": symbol,
        "price": get_price(symbol),
        "1h": get_klines(symbol, "1h", 200),
        "15m": get_klines(symbol, "15m", 200),
        "5m": get_klines(symbol, "5m", 200),
    }

def _local_swing_high(candles, i, left=2, right=2):
    h=candles[i]["high"]
    return h > max(x["high"] for x in candles[i-left:i]) and h >= max(x["high"] for x in candles[i+1:i+right+1])

def _local_swing_low(candles, i, left=2, right=2):
    l=candles[i]["low"]
    return l < min(x["low"] for x in candles[i-left:i]) and l <= min(x["low"] for x in candles[i+1:i+right+1])

def find_major_liquidity(candles_1h, current_price, max_levels=6):
    """Find meaningful 1H swing liquidity, filtering out tiny noise."""
    c=candles_1h
    if len(c)<15:
        return []
    highs=[]; lows=[]
    for i in range(2,len(c)-2):
        if _local_swing_high(c,i):
            highs.append((c[i]["high"], i))
        if _local_swing_low(c,i):
            lows.append((c[i]["low"], i))

    # Cluster nearby swing points; repeated tests make a level more meaningful.
    def cluster(items):
        items=sorted(items)
        out=[]
        for level,i in items:
            if not out or abs(level-out[-1]["level"])>0.35:
                out.append({"level":level,"touches":1,"index":i})
            else:
                old=out[-1]
                old["level"]=(old["level"]*old["touches"]+level)/(old["touches"]+1)
                old["touches"]+=1
                old["index"]=max(old["index"],i)
        return out

    candidates=[]
    for x in cluster(highs):
        if x["level"] > current_price:
            candidates.append({**x,"side":"SHORT","type":"1H major swing high"})
    for x in cluster(lows):
        if x["level"] < current_price:
            candidates.append({**x,"side":"LONG","type":"1H major swing low"})

    # Prefer repeated levels, then nearest meaningful level.
    candidates.sort(key=lambda x:(-x["touches"], abs(x["level"]-current_price)))
    return candidates[:max_levels]

def detect_sweep(candles_1h, current_price, direction):
    """Confirm that recent price pierced a major 1H level and rejected it."""
    levels=find_major_liquidity(candles_1h,current_price,10)
    wanted="LONG" if direction=="LONG" else "SHORT"
    for z in levels:
        if z["side"]!=wanted:
            continue
        level=z["level"]
        recent=candles_1h[-3:]
        if direction=="LONG":
            swept=any(x["low"] < level for x in recent)
            rejection=recent[-1]["close"] > level or recent[-1]["close"] > recent[-1]["open"]
        else:
            swept=any(x["high"] > level for x in recent)
            rejection=recent[-1]["close"] < level or recent[-1]["close"] < recent[-1]["open"]
        if swept and rejection:
            return {"swept":True,"direction":direction,"level":level,
                    "strength":min(1.0,0.6+0.1*z["touches"]),
                    "liquidity_type":z["type"]}
    return None
