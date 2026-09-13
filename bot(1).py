# -*- coding: utf-8 -*-
import asyncio, os
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from market import get_market_data, find_major_liquidity, detect_sweep
from strategy import analyze

BOT_TOKEN=os.getenv("BOT_TOKEN")
bot=Bot(token=BOT_TOKEN)
dp=Dispatcher()

def build_analysis():
    d=get_market_data("SOLUSDT")
    levels=find_major_liquidity(d["1h"],d["price"])
    sweep=detect_sweep(d["5m"],levels)
    return analyze(d["1h"],d["15m"],d["5m"],d["price"],sweep=sweep,order_flow=None,major_levels=levels),d["price"]

def format_result(r,p):
    lines=["TradeMind 3.2",f"Цена SOL: ${p:.2f}",""]
    for x in r.get("major_levels",[]):
        lines.append(("🟢" if x["direction"]=="LONG" else "🔴")+f" {x['type']}: ${x['price']:.2f}")
    lines+=["",f"Статус: {r['status']}",f"Score: {r['score']}/100",f"Причина: {r['reason']}"]
    if r["status"] in ("LONG","SHORT"):
        lines+=["",f"Entry: {r['entry']}",f"SL: {r['sl']}",f"TP: {r['tp']}","RR: 1:2 | TP: один",f"5M: {r['confirmation']}"]
    return "\n".join(lines)

@dp.message(Command("start"))
async def start(m): await m.answer("TradeMind 3.2 запущен.\n\n/sol — текущий анализ SOL\n/setup — поиск нового сетапа\n/status — статус\n/journal — журнал")

@dp.message(Command("sol"))
async def sol(m):
    try:
        r,p=await asyncio.to_thread(build_analysis); await m.answer(format_result(r,p))
    except Exception as e: await m.answer(f"Ошибка получения данных: {e}")

@dp.message(Command("setup"))
async def setup(m):
    await m.answer("🔎 Ищу новый сетап TradeMind 3.2...")
    try:
        r,p=await asyncio.to_thread(build_analysis)
        if r["status"]=="WAIT": await m.answer(f"🟡 НОВОГО СЕТАПА НЕТ\n\nЦена SOL: ${p:.2f}\n{r['reason']}")
        else: await m.answer("🚨 НАЙДЕН СЕТАП\n\n"+format_result(r,p))
    except Exception as e: await m.answer(f"Ошибка поиска: {e}")

@dp.message(Command("status"))
async def status(m): await m.answer("🟢 TradeMind 3.2 работает.")

@dp.message(Command("journal"))
async def journal(m): await m.answer("📒 Журнал подключён.")

async def main(): await dp.start_polling(bot)
if __name__=="__main__": asyncio.run(main())
