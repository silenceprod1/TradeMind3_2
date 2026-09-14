"""
TradeMind 5.3
Telegram scanner / monitor.

D1 -> W1 fallback -> 1H
-> Major Liquidity
-> Sweep
-> 15M confirmation
-> 5M V/L reversal
-> Recovery >= 1/3
-> 5M FVG inversion
-> Entry / SL / Structural TP
"""

import os
import asyncio
import logging
from datetime import datetime, date

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

import bingx

from market import get_market_data

from strategy import (
    analyze,
    get_major_liquidity,
    detect_fresh_sweep,
    STRATEGY_VERSION,
    MIN_SCORE_READY,
)


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")


SYMBOLS = [
    "SOLUSDT",
    "ETHUSDT",
]

MAX_TRADES_PER_DAY = 2


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("TradeMind")


# =========================================================
# STATE
# =========================================================

trades_today = 0
daily_stop = False
active_trade = None

last_setups = {}
last_signal_time = {}

_state_date = date.today()


# =========================================================
# DAILY RESET
# =========================================================

def reset_daily_state():

    global trades_today
    global daily_stop
    global _state_date

    today = date.today()

    if today != _state_date:

        trades_today = 0
        daily_stop = False
        active_trade = None
        _state_date = today

        logger.info("Daily state reset.")


# =========================================================
# FORMAT HELPERS
# =========================================================

def fmt_price(value):

    if value is None:
        return "—"

    try:
        return f"${float(value):.4f}"
    except Exception:
        return "—"


def fmt_pct(value):

    if value is None:
        return "—"

    try:
        return f"{float(value):.2f}%"
    except Exception:
        return "—"


def fmt_rr(value):

    if value is None:
        return "—"

    try:
        return f"1:{float(value):.2f}"
    except Exception:
        return "—"


def trend_emoji(trend):

    if trend == "bullish":
        return "🟢"

    if trend == "bearish":
        return "🔴"

    return "⚪"


# =========================================================
# BUILD ANALYSIS
# =========================================================

def build_analysis(symbol):

    data = get_market_data(symbol)

    if not data:
        raise RuntimeError(
            f"Нет данных для {symbol}"
        )

    price = data["price"]

    candles_d1 = data["candles_d1"]
    candles_w1 = data["candles_w1"]
    candles_1h = data["candles_1h"]
    candles_15m = data["candles_15m"]
    candles_5m = data["candles_5m"]

    major_levels = get_major_liquidity(
        candles_1h=candles_1h,
        current_price=price,
    )

    sweep = detect_fresh_sweep(
        candles_1h=candles_1h,
        candles_5m=candles_5m,
        current_price=price,
    )

    result = analyze(
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,
        price=price,
        major_levels=major_levels,
        sweep=sweep,
        candles_d1=candles_d1,
        candles_w1=candles_w1,
    )

    result.update({
        "symbol": symbol,
        "price": price,
        "major_levels": major_levels,
        "sweep": sweep,
        "candles_d1": candles_d1,
        "candles_w1": candles_w1,
        "candles_1h": candles_1h,
        "candles_15m": candles_15m,
        "candles_5m": candles_5m,
        "strategy_version": STRATEGY_VERSION,
    })

    return result


# =========================================================
# FIND READY SETUP
# =========================================================

def find_first_ready():

    reset_daily_state()

    if daily_stop:
        return None

    if trades_today >= MAX_TRADES_PER_DAY:
        return None

    for symbol in SYMBOLS:

        try:

            result = build_analysis(symbol)

            last_setups[symbol] = result

            if (
                result.get("stage") == "READY"
                and result.get("score", 0) >= MIN_SCORE_READY
            ):
                return result

        except Exception as e:

            logger.exception(
                "Analysis error %s: %s",
                symbol,
                e,
            )

    return None


# =========================================================
# FORMAT READY SETUP
# =========================================================

def format_setup(setup):

    symbol = setup.get(
        "symbol",
        "UNKNOWN"
    )

    direction = setup.get(
        "direction"
    )

    score = setup.get(
        "score",
        0
    )

    entry = setup.get(
        "entry"
    )

    sl = setup.get(
        "sl"
    )

    tp = setup.get(
        "tp"
    )

    rr = setup.get(
        "rr"
    )

    sweep = setup.get(
        "sweep"
    )

    d1 = setup.get(
        "d1_context",
        "neutral"
    )

    w1 = setup.get(
        "w1_context",
        "neutral"
    )

    h1 = setup.get(
        "h1_context",
        "neutral"
    )

    sweep_depth = setup.get(
        "sweep_depth"
    )

    sweep_age = setup.get(
        "sweep_age"
    )

    recovery_ratio = setup.get(
        "recovery_ratio"
    )

    tp_reason = setup.get(
        "tp_reason"
    )

    # Направление
    if direction == "LONG":
        direction_text = "🟢 LONG"

    elif direction == "SHORT":
        direction_text = "🔴 SHORT"

    else:
        direction_text = "⚪ —"

    # Recovery
    if recovery_ratio is not None:

        try:
            recovery_text = (
                f"{float(recovery_ratio):.2f}x"
            )
        except Exception:
            recovery_text = "—"

    else:
        recovery_text = "—"

    # Sweep
    if sweep:
        sweep_text = "YES"
    else:
        sweep_text = "NO"

    text = (
        f"🚨 TRADEMIND {STRATEGY_VERSION}"
        f" — ГОТОВЫЙ СЕТАП\n\n"

        f"💠 Монета: {symbol}\n"

        f"📐 Направление: "
        f"{direction_text}\n"

        f"⭐ Score: {score}/100\n\n"

        f"🟢 МОЖНО ВХОДИТЬ\n\n"

        f"💰 Entry: "
        f"{fmt_price(entry)}\n"

        f"🛑 SL: "
        f"{fmt_price(sl)}\n"

        f"🎯 TP: "
        f"{fmt_price(tp)}\n"

        f"📊 RR: "
        f"{fmt_rr(rr)}\n\n"

        f"📍 D1: "
        f"{trend_emoji(d1)} {d1}\n"

        f"📍 W1: "
        f"{trend_emoji(w1)} {w1}\n"

        f"📍 1H: "
        f"{trend_emoji(h1)} {h1}\n\n"

        f"💧 Sweep: "
        f"{sweep_text}\n"

        f"📏 Sweep depth: "
        f"{fmt_pct(sweep_depth)}\n"

        f"⏱ Sweep age: "
        f"{sweep_age if sweep_age is not None else '—'} × 5M\n\n"

        f"🔄 Recovery: "
        f"{recovery_text}\n\n"

        f"🎯 TP logic: "
        f"{tp_reason or 'Structural target'}\n\n"

        f"⚠️ Не входить в середине.\n"

        f"⚠️ Сигнал действует только "
        f"при сохранении структуры."
    )

    return text


# =========================================================
# FORMAT WAIT
# =========================================================

def format_wait(setup):

    symbol = setup.get(
        "symbol",
        "UNKNOWN"
    )

    price = setup.get(
        "price"
    )

    stage = setup.get(
        "stage",
        "WAIT"
    )

    score = setup.get(
        "score",
        0
    )

    direction = setup.get(
        "direction"
    )

    reason = setup.get(
        "reason",
        "No setup."
    )

    if direction == "LONG":
        direction_text = "🟢 LONG"

    elif direction == "SHORT":
        direction_text = "🔴 SHORT"

    else:
        direction_text = "⚪ NEUTRAL"

    return (
        f"🔎 TRADEMIND {STRATEGY_VERSION}"
        f" — STATUS\n\n"

        f"💠 {symbol}\n"

        f"💵 Price: "
        f"{fmt_price(price)}\n"

        f"📐 Bias: "
        f"{direction_text}\n"

        f"📊 Stage: "
        f"{stage}\n"

        f"⭐ Score: "
        f"{score}/100\n\n"

        f"⏳ WAIT\n\n"

        f"{reason}"
    )


# =========================================================
# /START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await track_chat(
        update,
        context
    )

    text = (
        f"🤖 TRADEMIND {STRATEGY_VERSION}\n\n"

        f"Стратегия:\n"

        f"D1/W1 → 1H → Major Liquidity\n"
        f"→ Sweep → 15M Confirmation\n"
        f"→ 5M V/L → Recovery\n"
        f"→ 5M FVG inversion\n"
        f"→ Entry → SL → Structural TP\n\n"

        f"📊 BingX mode: "
        f"{bingx.mode()}\n\n"

        f"Команды:\n"

        f"/status — статус\n"
        f"/sol — анализ SOL\n"
        f"/eth — анализ ETH\n"
        f"/scan — поиск сетапа\n"
        f"/levels — крупная ликвидность\n"
        f"/help — помощь"
    )

    await update.message.reply_text(
        text
    )


# =========================================================
# /HELP
# =========================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await track_chat(
        update,
        context
    )

    text = (
        f"🤖 TRADEMIND {STRATEGY_VERSION}\n\n"

        f"/status — статус бота\n"
        f"/sol — полный анализ SOL\n"
        f"/eth — полный анализ ETH\n"
        f"/scan — поиск готового сетапа\n"
        f"/levels — крупные уровни\n"
        f"/help — помощь\n\n"

        f"TradeMind НЕ входит без:\n\n"

        f"1️⃣ D1/W1 bias\n"
        f"2️⃣ 1H sync\n"
        f"3️⃣ Major liquidity sweep\n"
        f"4️⃣ 15M confirmation\n"
        f"5️⃣ 5M V/L reversal\n"
        f"6️⃣ Recovery >= 1/3\n"
        f"7️⃣ 5M FVG inversion"
    )

    await update.message.reply_text(
        text
    )


# =========================================================
# /STATUS
# =========================================================

async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    global trades_today

    await track_chat(
        update,
        context
    )

    reset_daily_state()

    mode = bingx.mode()

    if active_trade:

        active_text = (
            f"🟢 {active_trade.get('symbol')} "
            f"{active_trade.get('direction')}"
        )

    else:

        active_text = (
            "🟢 Активной сделки нет."
        )

    text = (
        f"📊 TRADEMIND {STRATEGY_VERSION}"
        f" — СТАТУС\n\n"

        f"Strategy version: "
        f"{STRATEGY_VERSION}\n"

        f"BingX mode: "
        f"{mode}\n"

        f"Сделок сегодня: "
        f"{trades_today}/{MAX_TRADES_PER_DAY}\n"

        f"Daily stop: "
        f"{'YES' if daily_stop else 'NO'}\n\n"

        f"{active_text}"
    )

    await update.message.reply_text(
        text
    )


# =========================================================
# SINGLE SYMBOL ANALYSIS
# =========================================================

async def analyze_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    symbol
):

    await track_chat(
        update,
        context
    )

    try:

        result = build_analysis(
            symbol
        )

        last_setups[
            symbol
        ] = result

        if result.get("stage") == "READY":

            text = format_setup(
                result
            )

        else:

            text = format_wait(
                result
            )

        await update.message.reply_text(
            text
        )

    except Exception as e:

        logger.exception(
            "Command analysis error"
        )

        await update.message.reply_text(
            f"❌ Ошибка анализа {symbol}:\n"
            f"{e}"
        )


# =========================================================
# /SOL
# =========================================================

async def sol_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await analyze_command(
        update,
        context,
        "SOLUSDT"
    )


# =========================================================
# /ETH
# =========================================================

async def eth_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await analyze_command(
        update,
        context,
        "ETHUSDT"
    )


# =========================================================
# /SCAN
# =========================================================

async def scan_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await track_chat(
        update,
        context
    )

    reset_daily_state()

    if daily_stop:

        await update.message.reply_text(
            "🛑 Daily stop активирован."
        )

        return

    if trades_today >= MAX_TRADES_PER_DAY:

        await update.message.reply_text(
            "⛔ Лимит 2 сделки "
            "на сегодня достигнут."
        )

        return

    ready = find_first_ready()

    if ready:

        await update.message.reply_text(
            format_setup(
                ready
            )
        )

    else:

        await update.message.reply_text(
            f"🔎 TRADEMIND {STRATEGY_VERSION}\n\n"

            f"❌ Готового сетапа "
            f"не найдено.\n\n"

            f"Проверены:\n"
            f"• SOLUSDT\n"
            f"• ETHUSDT\n\n"

            f"Правило:\n"
            f"нет подтверждения → "
            f"нет входа."
        )


# =========================================================
# /LEVELS
# =========================================================

async def levels_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await track_chat(
        update,
        context
    )

    try:

        result = build_analysis(
            "SOLUSDT"
        )

        price = result["price"]

        levels = result[
            "major_levels"
        ]

        if not levels:

            await update.message.reply_text(
                "❌ Крупные уровни не найдены."
            )

            return

        lines = [
            f"💧 TRADEMIND {STRATEGY_VERSION}",
            "",
            "💠 SOLUSDT",
            f"💵 Price: {fmt_price(price)}",
            "",
            "КРУПНАЯ ЛИКВИДНОСТЬ:",
            "",
        ]

        for i, level in enumerate(
            levels,
            1
        ):

            side = level.get(
                "side",
                "?"
            )

            level_price = level.get(
                "level",
                level.get(
                    "price"
                )
            )

            touches = level.get(
                "touches",
                1
            )

            if side == "LONG":
                icon = "🟢"

            elif side == "SHORT":
                icon = "🔴"

            else:
                icon = "⚪"

            try:

                distance = (
                    abs(
                        float(level_price)
                        - float(price)
                    )
                    / float(price)
                    * 100
                )

            except Exception:

                distance = 0

            lines.append(
                f"{i}. {icon} "
                f"{fmt_price(level_price)} "
                f"({side})\n"
                f"   Touches: {touches} | "
                f"Distance: {distance:.2f}%"
            )

        await update.message.reply_text(
            "\n".join(lines)
        )

    except Exception as e:

        logger.exception(
            "Levels error"
        )

        await update.message.reply_text(
            f"❌ Ошибка:\n{e}"
        )


# =========================================================
# CALLBACK
# =========================================================

async def callback_query(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if query.data == "scan":

        ready = find_first_ready()

        if ready:

            await query.message.reply_text(
                format_setup(
                    ready
                )
            )

        else:

            await query.message.reply_text(
                "❌ Готового сетапа нет."
            )


# =========================================================
# TRACK CHAT
# =========================================================

async def track_chat(
    update,
    context
):

    if update.effective_chat is None:
        return

    chat_id = update.effective_chat.id

    subscribers = (
        context.application.bot_data
        .setdefault(
            "subscribers",
            set()
        )
    )

    subscribers.add(
        chat_id
    )


# =========================================================
# MONITOR
# =========================================================

async def monitor(
    application
):

    global trades_today
    global daily_stop
    global active_trade

    logger.info(
        "TradeMind monitor started."
    )

    while True:

        try:

            reset_daily_state()

            if daily_stop:

                await asyncio.sleep(
                    60
                )

                continue

            if trades_today >= MAX_TRADES_PER_DAY:

                await asyncio.sleep(
                    60
                )

                continue

            setup = find_first_ready()

            if setup:

                symbol = setup.get(
                    "symbol"
                )

                now = datetime.utcnow()

                previous = last_signal_time.get(
                    symbol
                )

                # Не отправляем один и тот же
                # сигнал чаще одного раза в 5 минут.
                if previous:

                    elapsed = (
                        now - previous
                    ).total_seconds()

                    if elapsed < 300:

                        await asyncio.sleep(
                            30
                        )

                        continue

                last_signal_time[
                    symbol
                ] = now

                text = format_setup(
                    setup
                )

                # =========================================
                # BINGX
                # =========================================

                mode = bingx.mode()

                if mode == "OFF":

                    execution = {
                        "status": "signal_only"
                    }

                else:

                    execution = (
                        await asyncio.to_thread(
                            bingx.open_trade,
                            setup
                        )
                    )

                # =========================================
                # EXECUTION RESULT
                # =========================================

                if execution.get(
                    "status"
                ) == "signal_only":

                    text += (
                        "\n\n"
                        "📡 BingX mode OFF — "
                        "только сигнал."
                    )

                else:

                    text += (
                        "\n\n"
                        f"⚙️ Execution: "
                        f"{execution}"
                    )

                    active_trade = setup

                    trades_today += 1

                    if (
                        trades_today
                        >= MAX_TRADES_PER_DAY
                    ):

                        daily_stop = True

                # =========================================
                # SEND
                # =========================================

                subscribers = (
                    application.bot_data.get(
                        "subscribers",
                        set()
                    )
                )

                for chat_id in subscribers:

                    try:

                        await application.bot.send_message(
                            chat_id=chat_id,
                            text=text
                        )

                    except Exception:

                        logger.exception(
                            "Telegram send error"
                        )

            await asyncio.sleep(
                30
            )

        except Exception as e:

            logger.exception(
                "Monitor loop error: %s",
                e
            )

            await asyncio.sleep(
                30
            )


# =========================================================
# POST INIT
# =========================================================

async def post_init(
    application
):

    application.bot_data[
        "subscribers"
    ] = set()

    application.create_task(
        monitor(
            application
        )
    )

    logger.info(
        "TradeMind %s started.",
        STRATEGY_VERSION
    )


# =========================================================
# MAIN
# =========================================================

def main():

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(
            post_init
        )
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_command
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            status
        )
    )

    application.add_handler(
        CommandHandler(
            "sol",
            sol_command
        )
    )

    application.add_handler(
        CommandHandler(
            "eth",
            eth_command
        )
    )

    application.add_handler(
        CommandHandler(
            "scan",
            scan_command
        )
    )

    application.add_handler(
        CommandHandler(
            "levels",
            levels_command
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_query
        )
    )

    logger.info(
        "Starting polling..."
    )

    application.run_polling()


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":
    main()