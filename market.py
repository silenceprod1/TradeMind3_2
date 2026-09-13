# -*- coding: utf-8 -*-
"""TradeMind 3.2 market data: Binance Spot reference for SOLUSDT."""
import requests

BASE_URL = "https://api.binance.com/api/v3"
SYMBOL = "SOLUSDT"

def _get(path, params):
    r = requests.get(BASE_URL + path, params=params, timeout=10)
    r.raise_for_status()
    return r.json()

def get_price(symbol=SYMBOL):
    return float(_get("/ticker/price", {"symbol": symbol})["price"])

def get_klines(symbol=SYMBOL, interval="1h", limit=200):
    raw = _get("/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    return [{"open_time": x[0], "open": float(x[1]), "high": float(x[2]),
             "low": float(x[3]), "close": float(x[4]), "volume": float(x[5]),
             "close_time": x[6]} for x in raw]

def get_market_data(symbol=SYMBOL):
    return {"symbol": symbol, "price": get_price(symbol),
            "1h": get_klines(symbol, "1h", 200),
            "15m": get_klines(symbol, "15m", 200),
            "5m": get_klines(symbol, "5m", 200)}

def find_swing_levels(candles, left=2, right=2):
    highs, lows = [], []
    for i in range(left, len(candles)-right):
        h, l = candles[i]["high"], candles[i]["low"]
        if h >= max(x["high"] for x in candles[i-left:i]) and h > max(x["high"] for x in candles[i+1:i+right+1]):
            highs.append((h, i))
        if l <= min(x["low"] for x in candles[i-left:i]) and l < min(x["low"] for x in candles[i+1:i+right+1]):
            lows.append((l, i))
    return highs, lows

def find_major_liquidity(candles_1h, current_price, max_levels=4):
    """Keep meaningful 1H swing liquidity and suppress tiny nearby noise."""
    sh, sl = find_swing_levels(candles_1h[-120:])
    candidates = []
    for price, idx in sh:
        if price > current_price * 1.002:
            candidates.append({"price": price, "direction": "SHORT", "type": "1H swing high", "index": idx})
    for price, idx in sl:
        if price < current_price * 0.998:
            candidates.append({"price": price, "direction": "LONG", "type": "1H swing low", "index": idx})
    candidates.sort(key=lambda x: abs(x["price"]-current_price))
    selected, min_gap = [], max(current_price * 0.003, 0.25)
    for c in candidates:
        if all(abs(c["price"]-s["price"]) >= min_gap for s in selected):
            selected.append(c)
        if len(selected) >= max_levels:
            break
    return selected

def detect_sweep(candles_5m, major_levels, lookback=6):
    if len(candles_5m) < 3:
        return None
    last, prev = candles_5m[-1], candles_5m[-lookback:-1]
    if not prev:
        return None
    for level in major_levels:
        lv = level["price"]
        if level["direction"] == "LONG" and last["low"] < lv and last["close"] > lv and last["close"] > last["open"]:
            return {"direction":"LONG","level":lv,"swept":True,"strength":0.8,"liquidity_type":level["type"]}
        if level["direction"] == "SHORT" and last["high"] > lv and last["close"] < lv and last["close"] < last["open"]:
            return {"direction":"SHORT","level":lv,"swept":True,"strength":0.8,"liquidity_type":level["type"]}
    return None
