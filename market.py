import requests
BASE="https://api.binance.com"
def get_price(symbol="SOLUSDT"):
    r=requests.get(BASE+"/api/v3/ticker/price",params={"symbol":symbol},timeout=10); r.raise_for_status()
    return float(r.json()["price"])
def get_klines(symbol="SOLUSDT",interval="15m",limit=200):
    r=requests.get(BASE+"/api/v3/klines",params={"symbol":symbol,"interval":interval,"limit":limit},timeout=10); r.raise_for_status()
    return r.json()
