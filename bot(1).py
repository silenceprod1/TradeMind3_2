import asyncio
import os
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

from market import get_klines
from strategy import analyze


BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


async def build_analysis():
    # Binance Spot reference data
    candles_1h = get_klines("SOLUSDT", "1h", 120)
    candles_15m = get_klines("SOLUSDT", "15m", 120)
    candles_5m = get_klines("SOLUSDT", "5m", 120)

    if not candles_1h or not candles_15m or not candles_5m:
        return None

    price = float(candles_5m[-1]["close"])

    # Major liquidity is derived from meaningful 1H extremes.
    highs = [float(c["high"]) for c in candles_1h[-48:]]
    lows = [float(c["low"]) for c in candles_1h[-48:]]
    major_high = max(highs)
    major_low = min(lows)

    # Detect a fresh sweep on the latest 5M candles.
    # We require the level to be a meaningful 1H extreme and price to reject it.
    sweep = None

    recent5 = candles_5m[-6:]
    last = recent5[-1]
    prev = recent5[:-1]

    last_high = float(last["high"])
    last_low = float(last["low"])
    last_close = float(last["close"])
    prev_high = max(float(c["high"]) for c in prev)
    prev_low = min(float(c["low"]) for c in prev)

    # A sweep must take the major level and show rejection.
    if last_low < major_low and last_close > major_low:
        sweep = {
            "direction": "LONG",
            "level": major_low,
            "swept": True,
            "strength": 0.75,
            "liquidity_type": "1H major low",
        }
    elif last_high > major_high and last_close < major_high:
        sweep = {
            "direction": "SHORT",
            "level": major_high,
            "swept": True,
            "strength": 0.75,
            "liquidity_type": "1H major high",
        }

    return analyze(
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,
        current_price=price,
        sweep=sweep,
        order_flow=None,
    )


def format_result(result):
    if not result:
        return "â ÐÐµ ÑÐ´Ð°Ð»Ð¾ÑÑ Ð¿Ð¾Ð»ÑÑÐ¸ÑÑ Ð´Ð°Ð½Ð½ÑÐµ Binance."

    status = result.get("status", "WAIT")
    emoji = "ð¢" if status == "LONG" else "ð´" if status == "SHORT" else "ð¡"

    text = [
        "TradeMind 3.2",
        f"Ð¦ÐµÐ½Ð° SOL: ${result.get('entry') or 'â'}" if status != "WAIT"
        else "Ð¦ÐµÐ½Ð° SOL: Ð°ÐºÑÑÐ°Ð»ÑÐ½Ð°Ñ",
        "",
    ]

    if result.get("zone_low") is not None:
        text.append(f"ð¢ ÐÑÑÐ¿Ð½Ð°Ñ LONG Ð»Ð¸ÐºÐ²Ð¸Ð´Ð½Ð¾ÑÑÑ: Ð¾ÐºÐ¾Ð»Ð¾ ${result['zone_low']:.2f}")
    if result.get("zone_high") is not None:
        text.append(f"ð´ ÐÑÑÐ¿Ð½Ð°Ñ SHORT Ð»Ð¸ÐºÐ²Ð¸Ð´Ð½Ð¾ÑÑÑ: Ð¾ÐºÐ¾Ð»Ð¾ ${result['zone_high']:.2f}")

    text += [
        "",
        f"{emoji} Ð¡ÑÐ°ÑÑÑ: {status}",
        f"Score: {result.get('score', 0)}/100",
        f"ÐÑÐ¸ÑÐ¸Ð½Ð°: {result.get('reason', '')}",
    ]

    if status in ("LONG", "SHORT"):
        text += [
            "",
            f"Entry: {result['entry']}",
            f"SL: {result['sl']}",
            f"TP: {result['tp']}",
            "RR: 1:2 | TP: Ð¾Ð´Ð¸Ð½",
            f"5M: {result.get('confirmation', 'â')}",
        ]

    return "\n".join(text)


@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "TradeMind 3.2 Ð·Ð°Ð¿ÑÑÐµÐ½.\n\n"
        "/sol â ÑÐµÐºÑÑÐ¸Ð¹ Ð°Ð½Ð°Ð»Ð¸Ð· SOL\n"
        "/setup â Ð¿Ð¾Ð¸ÑÐº Ð½Ð¾Ð²Ð¾Ð³Ð¾ ÑÐµÑÐ°Ð¿Ð°\n"
        "/status â ÑÑÐ°ÑÑÑ\n"
        "/journal â Ð¶ÑÑÐ½Ð°Ð»"
    )


@dp.message(Command("sol"))
async def sol(message: Message):
    result = await asyncio.to_thread(build_analysis)
    await message.answer(format_result(result))


@dp.message(Command("setup"))
async def setup(message: Message):
    await message.answer("ð ÐÑÑ Ð½Ð¾Ð²ÑÐ¹ ÑÐµÑÐ°Ð¿ TradeMind 3.2...")
    result = await asyncio.to_thread(build_analysis)

    if not result:
        await message.answer("â ÐÐµ ÑÐ´Ð°Ð»Ð¾ÑÑ Ð¿Ð¾Ð»ÑÑÐ¸ÑÑ ÑÑÐ½Ð¾ÑÐ½ÑÐµ Ð´Ð°Ð½Ð½ÑÐµ.")
        return

    if result.get("status") == "WAIT":
        await message.answer(
            "ð¡ ÐÐÐÐÐÐ Ð¡ÐÐ¢ÐÐÐ ÐÐÐ¢\n\n"
            + result.get("reason", "ÐÐ´ÑÐ¼ ÐºÑÑÐ¿Ð½ÑÑ Ð»Ð¸ÐºÐ²Ð¸Ð´Ð½Ð¾ÑÑÑ Ð¸ Ð¿Ð¾Ð´ÑÐ²ÐµÑÐ¶Ð´ÐµÐ½Ð¸Ðµ 5M.")
        )
    else:
        await message.answer("ð¨ ÐÐÐÐÐÐ Ð¡ÐÐ¢ÐÐ\n\n" + format_result(result))


@dp.message(Command("status"))
async def status(message: Message):
    await message.answer("ð¢ TradeMind 3.2 ÑÐ°Ð±Ð¾ÑÐ°ÐµÑ.")


@dp.message(Command("journal"))
async def journal(message: Message):
    await message.answer("ð ÐÑÑÐ½Ð°Ð» Ð¿Ð¾Ð´ÐºÐ»ÑÑÑÐ½. ÐÐµÑÐ°Ð»ÑÐ½ÑÑ ÑÑÐ°ÑÐ¸ÑÑÐ¸ÐºÑ Ð´Ð¾Ð±Ð°Ð²Ð¸Ð¼ ÑÐ»ÐµÐ´ÑÑÑÐ¸Ð¼ ÑÑÐ°Ð¿Ð¾Ð¼.")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
