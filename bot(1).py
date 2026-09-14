"""
TradeMind 5.3
Telegram scanner / monitor.

Strategy:
D1 -> W1 fallback -> 1H
-> Major Liquidity
-> Sweep
-> 15M confirmation
-> 5M V/L
-> >=1/3 recovery
-> 5M FVG inversion
-> Entry / SL / Structural TP

BingX:
OFF = signals only
ON  = execution through bingx.py
"""

import os
import asyncio
import logging
from datetime import datetime, date

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

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

BOT_TOKEN = os.getenv(
    "BOT_TOKEN"
)

if not BOT_TOKEN:

    raise RuntimeError(
        "BOT_TOKEN is not set"
    )


# Основной инструмент
SYMBOLS = [
    "SOLUSDT",
    "ETHUSDT",
]


# Максимум сигналов/сделок в день
MAX_TRADES_PER_DAY = 2


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format=(
        "%(asctime)s - "
        "%(name)s - "
        "%(levelname)s - "
        "%(message)s"
    ),
    level=logging.INFO,
)

logger = logging.getLogger(
    "TradeMind"
)


# =========================================================
# STATE
# =========================================================

trades_today = 0

daily_stop = False

last_setups = {}

last_signal_time = {}

active_trade = None


# =========================================================
# DAILY RESET
# =========================================================

_state_date = date.today()


def reset_daily_state():

    global trades_today
    global daily_stop
    global _state_date

    today = date.today()

    if today != _state_date:

        trades_today = 0

        daily_stop = False

        _state_date = today

        logger.info(
            "Daily state reset."
        )


# =========================================================
# HELPERS
# =========================================================

def fmt_price(
    value
):

    if value is None:
        return "—"

    return f"${value:.4f}"


def fmt_pct(
    value
):

    if value is None:
        return "—"

    return f"{value:.2f}%"


def fmt_rr(
    value
):

    if value is None:
        return "—"

    return f"1:{value:.2f}"


def trend_emoji(
    trend
):

    if trend == "bullish":
        return "🟢"

    if trend == "bearish":
        return "🔴"

    return "⚪"


# =========================================================
# ANALYSIS
# =========================================================

def build_analysis(
    symbol
):

    data = get_market_data(
        symbol
    )

    if not data:

        raise RuntimeError(
            f"No market data for {symbol}"
        )

    price = data[
        "price"
    ]

    candles_d1 = data[
        "candles_d1"
    ]

    candles_w1 = data[
        "candles_w1"
    ]

    candles_1h = data[
        "candles_1h"
    ]

    candles_15m = data[
        "candles_15m"
    ]

    candles_5m = data[
        "candles_5m"
    ]

    # =====================================================
    # MAJOR LIQUIDITY
    # =====================================================

    major_levels = (
        get_major_liquidity(
            candles_1h,
            price
        )
    )

    # =====================================================
    # FRESH SWEEP
    # =====================================================

    sweep = (
        detect_fresh_sweep(
            candles_1h=candles_1h,
            candles_5m=candles_5m,
            current_price=price,
        )
    )

    # =====================================================
    # STRATEGY
    # =====================================================

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

        "strategy_version":
            STRATEGY_VERSION,
    })

    return result


# =========================================================
# FIND READY
# =========================================================

def find_first_ready():

    reset_daily_state()

    if daily_stop:

        return None

    if trades_today >= MAX_TRADES_PER_DAY:

        return None

    for symbol in SYMBOLS:

        try:

            result = build_analysis(
                symbol
            )

            last_setups[
                symbol
            ] = result

            if (
                result.get("stage")
                == "READY"
                and
                result.get("score", 0)
                >= MIN_SCORE_READY
            ):

                return result

        except Exception as e:

            logger.exception(
                "Analysis error %s: %s",
                symbol,
                e
            )

    return None


# =========================================================
# FORMAT SETUP
# =========================================================

def format_setup(
    setup
):

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

    if direction == "LONG":

        direction_text = "🟢 LONG"

    elif direction == "SHORT":

        direction_text = "🔴 SHORT"

    else:

        direction_text = "⚪ —"

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
        f"{'YES' if sweep else 'NO'}\n"

        f"📏 Sweep depth: "
        f"{fmt_pct(sweep_depth)}\n"

        f"⏱ Sweep age: "
        f"{sweep_age if sweep_age is not None else '—'} "
        f"× 5M\n\n"

        f"🔄 Recovery: "
        f"{(
            f'{recovery_ratio:.2f}x'
            if recovery_ratio is not None
            else '—'
        )}\n\n"

        f"🎯 TP logic: "
        f"{tp_reason or 'Structural target'}\n\n"

        f"⚠️ Не входить в середине.\n"

        f"⚠️ Сигнал действует только "
        f"при сохранении структуры."
    )

    return text


# =========================================================
# WAIT FORMAT
# =========================================================

def format_wait(
    setup
):

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

        f"⏳ WAIT\n"

        f"{reason}"
    )


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

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
# HELP
# =========================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (

        f"🤖 TRADEMIND {STRATEGY_VERSION}\n\n"

        f"/status — статус бота\n"
        f"/sol — полный анализ SOL\n"
        f"/eth — полный анализ ETH\n"
        f"/scan — поиск готового сетапа\n"
        f"/levels — крупные уровни ликвидности\n"
        f"/help — помощь\n\n"

        f"TradeMind НЕ входит в сделку без:\n"

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
# STATUS
# =========================================================

async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    reset_daily_state()

    mode = bingx.mode()

    if active_trade:

        active_text = (
            f"🟢 {active_trade.get('symbol')}"
            f" {active_trade.get('direction')}"
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

    try:

        result = build_analysis(
            symbol
        )

        last_setups[
            symbol
        ] = result

        if (
            result.get("stage")
            == "READY"
        ):

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
# SOL
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
# ETH
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
# SCAN
# =========================================================

async def scan_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    reset_daily_state()

    if daily_stop:

        await update.message.reply_text(
            "🛑 Daily stop активирован."
        )

        return

    if (
        trades_today
        >= MAX_TRADES_PER_DAY
    ):

        await update.message.reply_text(
            "⛔ Лимит 2 сделки "
            "на сегодня достигнут."
        )

        return

    ready = (
        find_first_ready()
    )

    if ready:

        await update.message.reply_text(
            format_setup(
                ready
            )
        )

    else:

        await update.message.reply_text(

            f"🔎 TRADEMIND "
            f"{STRATEGY_VERSION}\n\n"

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
# LEVELS
# =========================================================

async def levels_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:

        result = build_analysis(
            "SOLUSDT"
        )

        price = result[
            "price"
        ]

        levels = result[
            "major_levels"
        ]

        if not levels:

            await update.message.reply_text(
                "❌ Крупные уровни не найдены."
            )

            return

        lines = [

            f"💧 TRADEMIND "
            f"{STRATEGY_VERSION}",

            "",

            f"💠 SOLUSDT",

            f"💵 Price: "
            f"{fmt_price(price)}",

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

            distance = (
                abs(
                    level_price
                    - price
                )
                / price
                * 100
            )

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

    data = query.data

    if data == "scan":

        ready = (
            find_first_ready()
        )

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
# MONITOR
# =========================================================

async def monitor(
    app
):

    global trades_today
    global daily_stop
    global active_trade

    logger.info(
        "Monitor started."
    )

    while True:

        try:

            reset_daily_state()

            # =============================================
            # DAILY LIMIT
            # =============================================

            if daily_stop:

                await asyncio.sleep(
                    60
                )

                continue

            if (
                trades_today
                >= MAX_TRADES_PER_DAY
            ):

                await asyncio.sleep(
                    60
                )

                continue

            # =============================================
            # SEARCH
            # =============================================

            setup = (
                find_first_ready()
            )

            if setup:

                symbol = setup.get(
                    "symbol"
                )

                now = datetime.utcnow()

                previous = (
                    last_signal_time.get(
                        symbol
                    )
                )

                # Не спамим одним и тем же
                if (
                    previous
                    and (
                        now - previous
                    ).total_seconds()
                    < 300
                ):

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
                # BINGX MODE
                # =========================================

                mode = bingx.mode()

                if mode == "OFF":

                    execution = {
                        "status":
                            "signal_only"
                    }

                else:

                    execution = (
                        await asyncio.to_thread(
                            bingx.open_trade,
                            setup
                        )
                    )

                # =========================================
                # RESULT
                # =========================================

                if (
                    execution.get(
                        "status"
                    )
                    == "signal_only"
                ):

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
                # SEND TO ALL KNOWN CHATS
                # =========================================

                for chat_id in (
                    getattr(
                        app.bot_data,
                        "subscribers",
                        set()
                    )
                ):

                    try:

                        await app.bot.send_message(
                            chat_id=chat_id,
                            text=text
                        )

                    except Exception:

                        logger.exception(
                            "Send error"
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
# TRACK CHAT
# =========================================================

async def track_chat(
    update,
    context
):

    if update.effective_chat is None:
        return

    chat_id = (
        update.effective_chat.id
    )

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
# POST INIT
# =========================================================

async def post_init(
    application
):

    application.bot_data[
        "subscribers"
    ] = set()

    # Запускаем монитор
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

    # Commands
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


if __name__ == "__main__":

    main()