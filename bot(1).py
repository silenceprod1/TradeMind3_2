import asyncio, os
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message
from market import get_price
from strategy import analyze_sol
from journal import init_db, list_trades

TOKEN=os.getenv("TELEGRAM_BOT_TOKEN")
router=Router()

@router.message(Command("start"))
async def start(m:Message):
    await m.answer("🤖 TradeMind 3.2\n/sol — анализ SOL\n/setup — поиск сетапа\n/journal — журнал\n/status — статус")

@router.message(Command("sol"))
async def sol(m:Message):
    a=analyze_sol()
    await m.answer(a)

@router.message(Command("setup"))
async def setup(m:Message):
    await m.answer(analyze_sol())

@router.message(Command("journal"))
async def journal(m:Message):
    rows=list_trades(10)
    if not rows:
        await m.answer("📒 Журнал пока пуст.")
    else:
        await m.answer("\n".join(f"#{r['id']} {r['symbol']} {r['direction']} | {r['result'] or 'OPEN'}" for r in rows))

@router.message(Command("status"))
async def status(m:Message):
    await m.answer("🟢 ONLINE\nTradeMind 3.2\nBinance Spot\n1H → 15M → 5M\nRR 1:2\nАвтоторговля: ВЫКЛ")

async def main():
    if not TOKEN: raise RuntimeError("TELEGRAM_BOT_TOKEN не задан")
    init_db()
    dp=Dispatcher(); dp.include_router(router)
    await dp.start_polling(Bot(TOKEN))

if __name__=="__main__": asyncio.run(main())
