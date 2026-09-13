from market import get_price,get_klines
def candles(rows): return [{"o":float(x[1]),"h":float(x[2]),"l":float(x[3]),"c":float(x[4]),"v":float(x[5])} for x in rows]
def analyze_sol():
    price=get_price()
    h=candles(get_klines(interval="1h")); m=candles(get_klines(interval="15m")); f=candles(get_klines(interval="5m"))
    low=min(x["l"] for x in h[-80:]); high=max(x["h"] for x in h[-80:])
    lowz=(low-0.20,low+0.20); highz=(high-0.20,high+0.20)
    status="WAIT"; reason="Цена между крупными зонами — в середине не входим."
    if lowz[0]<=price<=lowz[1]: reason="Цена в нижней крупной зоне. Ждём sweep + реакцию + 5M подтверждение → LONG."
    elif highz[0]<=price<=highz[1]: reason="Цена в верхней крупной зоне. Ждём sweep + реакцию + 5M подтверждение → SHORT."
    return f"TradeMind 3.2\nЦена SOL: ${price:.2f}\n\n🟢 LONG зона: {lowz[0]:.2f}–{lowz[1]:.2f}\n🔴 SHORT зона: {highz[0]:.2f}–{highz[1]:.2f}\n\nСтатус: {status}\n{reason}\n\nRR: 1:2 | TP: один"
