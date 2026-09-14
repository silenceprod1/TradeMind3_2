# ============================================================
# TRADEMIND 5.3 — MAIN
# ============================================================

import os
import asyncio
import logging
from datetime import datetime, timezone

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from market import get_market_data

from strategy import (
    analyze,
    get_major_liquidity,
    STRATEGY_VERSION,
    MIN_SCORE_READY,
)


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("TradeMind")


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

BINGX_MODE = os.getenv("BINGX_MODE", "OFF").upper()

try:
    import bingx
except Exception:
    bingx = None


SYMBOLS = [
    "SOLUSDT",
    "ETHUSDT",
    "BTCUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "LINKUSDT",
]

MAX_TRADES_PER_DAY = 2
MONITOR_INTERVAL = 30
SIGNAL_COOLDOWN_MINUTES = 5


# ============================================================
# GLOBAL STATE
# ============================================================

trades_today = 0
daily_stop = False
active_trade = None

last_setups = {}
last_signal_time = {}

subscribers = set()

state_date = None


# ============================================================
# TIME / STATE
# ============================================================

def now():
    return datetime.now(timezone.utc)


def reset_daily_state():
    global state_date
    global trades_today
    global daily_stop
    global active_trade

    today = now().date()

    if state_date != today:
        state_date = today
        trades_today = 0
        daily_stop = False
        active_trade = None

        logger.info("Daily state reset: %s", today)


# ============================================================
# HELPERS
# ============================================================

def fmt_price(value):
    if value is None:
        return "—"

    try:
        value = float(value)

        if value >= 1000:
            return f"${value:,.2f}"

        if value >= 100:
            return f"${value:.2f}"

        if value >= 1:
            return f"${value:.4f}"

        return f"${value:.6f}"

    except Exception:
        return str(value)


def direction_emoji(direction):
    if direction == "LONG":
        return "🟢"

    if direction == "SHORT":
        return "🔴"

    return "⚪"


def stage_text(stage):
    if not stage:
        return "—"

    mapping = {
        "D1": "D1",
        "W1": "W1",
        "1H": "1H",
        "WAIT": "WAIT",
        "SWEEP": "SWEEP",
        "15M": "15M",
        "5M": "5M",
        "READY": "READY",
    }

    return mapping.get(stage, str(stage))


def get_score(result):
    if not result:
        return 0

    return result.get(
        "score",
        result.get(
            "total_score",
            0
        )
    )


def get_direction(result):
    if not result:
        return None

    return result.get(
        "direction",
        result.get("side")
    )


# ============================================================
# ANALYSIS
# ============================================================

def build_analysis(symbol):

    market = get_market_data(symbol)

    price = market["price"]

    candles_d1 = market.get(
        "candles_d1",
        market.get("d1", [])
    )

    candles_w1 = market.get(
        "candles_w1",
        market.get("w1", [])
    )

    candles_1h = market.get(
        "candles_1h",
        market.get("1h", [])
    )

    candles_15m = market.get(
        "candles_15m",
        market.get("15m", [])
    )

    candles_5m = market.get(
        "candles_5m",
        market.get("5m", [])
    )

    result = analyze(
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,
        price=price,
        candles_d1=candles_d1,
        candles_w1=candles_w1,
    )

    if result is None:
        result = {}

    result["symbol"] = symbol
    result["price"] = price

    return result


# ============================================================
# READY SEARCH
# ============================================================

def find_first_ready():

    for symbol in SYMBOLS:

        try:
            result = build_analysis(symbol)

            score = get_score(result)
            direction = get_direction(result)
            stage = result.get("stage")

            logger.info(
                "%s | price=%s | direction=%s | stage=%s | score=%s",
                symbol,
                result.get("price"),
                direction,
                stage,
                score,
            )

            if (
                stage == "READY"
                and direction in ("LONG", "SHORT")
                and score >= MIN_SCORE_READY
            ):
                return symbol, result

        except Exception as e:

            logger.exception(
                "Analysis error for %s: %s",
                symbol,
                e,
            )

    return None, None


# ============================================================
# FORMAT ANALYSIS / SETUP
# ============================================================

def format_setup(symbol, setup):

    direction = get_direction(setup)

    score = get_score(setup)

    stage = setup.get("stage", "—")

    entry = setup.get(
        "entry",
        setup.get("entry_price")
    )

    sl = setup.get(
        "sl",
        setup.get("stop_loss")
    )

    tp = setup.get(
        "tp",
        setup.get("take_profit")
    )

    rr = setup.get("rr")

    sweep = setup.get("sweep")

    confirmation = setup.get(
        "confirmation_15m",
        setup.get("confirm_15m")
    )

    recovery = setup.get(
        "recovery",
        setup.get("recovery_ratio")
    )

    fvg = setup.get(
        "fvg_inversion_5m",
        setup.get("fvg_inversion")
    )

    # ========================================================
    # READY STATUS
    # ========================================================

    is_ready = (
        stage == "READY"
        and direction in ("LONG", "SHORT")
        and score >= MIN_SCORE_READY
        and entry is not None
        and sl is not None
        and tp is not None
    )

    # ========================================================
    # HEADER
    # ========================================================

    if is_ready:

        text = (
            "🚨 TRADEMIND 5.3 — ГОТОВЫЙ СЕТАП\n\n"
            f"💠 {symbol}\n"
            f"📐 {direction_emoji(direction)} "
            f"{direction}\n"
            f"⭐ Score: {score}/100\n"
            f"📍 Stage: READY\n\n"
        )

    else:

        text = (
            "🔎 TRADEMIND 5.3 — АНАЛИЗ\n\n"
            f"💠 {symbol}\n"
            f"📐 {direction_emoji(direction)} "
            f"{direction or '—'}\n"
            f"⭐ Score: {score}/100\n"
            f"📍 Stage: {stage_text(stage)}\n\n"
        )

    # ========================================================
    # ANALYSIS DETAILS
    # ========================================================

    if sweep is not None:
        text += f"💧 Sweep: {sweep}\n"

    if confirmation is not None:

        if confirmation is True:
            text += "✅ 15M confirmation: True\n"
        else:
            text += "❌ 15M confirmation: False\n"

    if recovery is not None:
        text += f"↩️ Recovery: {recovery}\n"

    if fvg is not None:

        if fvg is True:
            text += "✅ 5M FVG inversion: True\n"
        else:
            text += "❌ 5M FVG inversion: False\n"

    # ========================================================
    # ENTRY DATA
    # ========================================================

    text += (
        "\n"
        f"💰 Entry: {fmt_price(entry)}\n"
        f"🛑 SL: {fmt_price(sl)}\n"
        f"🏁 TP: {fmt_price(tp)}\n"
        f"⚖️ RR: {rr if rr is not None else '—'}\n\n"
    )

    # ========================================================
    # FINAL STATUS
    # ========================================================

    if is_ready:

        text += (
            "🟢 МОЖНО ВХОДИТЬ\n"
            "Все условия TradeMind выполнены."
        )

    else:

        text += (
            "⏳ ВХОДА НЕТ\n"
            "Ждём полного подтверждения TradeMind.\n\n"
            "D1 → 1H → крупная ликвидность → Sweep\n"
            "→ 15M confirmation → 5M trigger"
        )

    return text


# ============================================================
# TRACK CHAT
# ============================================================

def track_chat(update: Update):

    if update.effective_chat:

        subscribers.add(
            update.effective_chat.id
        )


# ============================================================
# /START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(update)

    text = (
        "🤖 TRADEMIND 5.3\n\n"
        "Автоматический мониторинг сетапов.\n\n"
        "Основная логика:\n"
        "D1 → 1H → Liquidity Sweep → 15M → 5M → Entry\n\n"
        "Команды:\n"
        "/sol — анализ SOL\n"
        "/eth — анализ ETH\n"
        "/market — весь рынок\n"
        "/levels — крупная ликвидность\n"
        "/search — поиск нового сетапа\n"
        "/status — статус бота\n"
        "/subscribe — включить уведомления\n"
        "/unsubscribe — выключить уведомления\n"
        "/journal — журнал\n"
        "/bingx_status — статус BingX\n"
        "/balance — баланс BingX\n"
        "/position — позиция BingX\n"
    )

    await update.message.reply_text(text)


# ============================================================
# /HELP
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(update)

    await update.message.reply_text(
        "📖 TRADEMIND 5.3\n\n"
        "/sol — SOL\n"
        "/eth — ETH\n"
        "/market — рынок\n"
        "/levels — крупная ликвидность\n"
        "/search — поиск сетапа\n"
        "/status — статус\n"
        "/subscribe — уведомления\n"
        "/unsubscribe — отключить уведомления\n"
        "/journal — журнал\n"
        "/bingx_status — BingX\n"
        "/balance — баланс\n"
        "/position — позиция"
    )


# ============================================================
# /STATUS
# ============================================================

async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(update)
    reset_daily_state()

    if active_trade is None:
        active_text = "🟢 Активной сделки нет."
    else:
        active_text = (
            f"🔴 Активная сделка: {active_trade}"
        )

    text = (
        "📊 TRADEMIND 5.3 — СТАТУС\n\n"
        "Bot version: 5.3\n"
        f"Strategy version: {STRATEGY_VERSION}\n"
        f"BingX mode: {BINGX_MODE}\n"
        f"Сделок сегодня: "
        f"{trades_today}/{MAX_TRADES_PER_DAY}\n"
        f"Daily stop: "
        f"{'YES' if daily_stop else 'NO'}\n\n"
        f"{active_text}"
    )

    await update.message.reply_text(text)


# ============================================================
# /SOL
# ============================================================

async def sol(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(update)

    try:

        symbol = "SOLUSDT"

        setup = build_analysis(symbol)

        await update.message.reply_text(
            format_setup(symbol, setup)
        )

    except Exception as e:

        logger.exception("SOL error")

        await update.message.reply_text(
            f"❌ Ошибка анализа SOL:\n{e}"
        )


# ============================================================
# /ETH
# ============================================================

async def eth(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(update)

    try:

        symbol = "ETHUSDT"

        setup = build_analysis(symbol)

        await update.message.reply_text(
            format_setup(symbol, setup)
        )

    except Exception as e:

        logger.exception("ETH error")

        await update.message.reply_text(
            f"❌ Ошибка анализа ETH:\n{e}"
        )


# ============================================================
# /MARKET
# ============================================================

async def market(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(update)

    lines = [
        "📊 TRADEMIND — РЫНОК",
        ""
    ]

    for symbol in SYMBOLS:

        try:

            result = build_analysis(symbol)

            price = result.get("price")

            direction = get_direction(result)

            stage = result.get("stage")

            score = get_score(result)

            emoji = direction_emoji(direction)

            short_symbol = symbol.replace(
                "USDT",
                ""
            )

            lines.append(
                f"{short_symbol}: "
                f"{fmt_price(price)} | "
                f"{emoji} {direction or '—'} | "
                f"{stage_text(stage)} | "
                f"{score}/100"
            )

        except Exception as e:

            logger.error(
                "Market error %s: %s",
                symbol,
                e,
            )

            short_symbol = symbol.replace(
                "USDT",
                ""
            )

            lines.append(
                f"{short_symbol}: ❌ ERROR"
            )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# /SEARCH
# ============================================================

async def search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(update)

    await update.message.reply_text(
        "🔎 TRADEMIND — ПОИСК СЕТАПА\n\n"
        "Проверяю рынок..."
    )

    try:

        symbol, setup = find_first_ready()

        if symbol is None:

            await update.message.reply_text(
                "❌ Готового сетапа сейчас нет.\n\n"
                "Нет полного подтверждения → нет входа."
            )

            return

        await update.message.reply_text(
            format_setup(symbol, setup)
        )

    except Exception as e:

        logger.exception("Search error")

        await update.message.reply_text(
            f"❌ Ошибка поиска:\n{e}"
        )


# ============================================================
# /LEVELS
# ============================================================

async def levels(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(update)

    symbol = "SOLUSDT"

    if context.args:

        requested = context.args[0].upper()

        if not requested.endswith("USDT"):
            requested += "USDT"

        if requested in SYMBOLS:
            symbol = requested

    try:

        market_data = get_market_data(symbol)

        price = market_data["price"]

        candles_1h = market_data.get(
            "candles_1h",
            market_data.get("1h", [])
        )

        levels_data = get_major_liquidity(
            candles_1h,
            price
        )

        highs = []
        lows = []

        for level in levels_data:

            level_price = level.get("price")

            if level_price is None:
                continue

            level_price = float(level_price)

            level_type = level.get(
                "type",
                ""
            ).upper()

            if (
                level_type == "HIGH"
                and level_price > price
            ):
                highs.append(level)

            elif (
                level_type == "LOW"
                and level_price < price
            ):
                lows.append(level)

        highs.sort(
            key=lambda x: float(x["price"])
        )

        lows.sort(
            key=lambda x: float(x["price"]),
            reverse=True
        )

        lines = [
            "💧 TRADEMIND — КРУПНАЯ ЛИКВИДНОСТЬ",
            "",
            f"💠 {symbol}",
            f"💰 Цена: {fmt_price(price)}",
            ""
        ]

        lines.append(
            "🔴 ВЫШЕ ЦЕНЫ — SHORT SWEEP"
        )

        if highs:

            for i, level in enumerate(
                highs,
                start=1
            ):

                lines.append(
                    f"{i}. 🔴 "
                    f"{fmt_price(level['price'])}\n"
                    f"   SHORT | "
                    f"touches: "
                    f"{level.get('touches', '—')} | "
                    f"strength: "
                    f"{level.get('strength', '—')}"
                )

        else:

            lines.append(
                "Нет крупных уровней."
            )

        lines.append("")

        lines.append(
            "🟢 НИЖЕ ЦЕНЫ — LONG SWEEP"
        )

        if lows:

            for i, level in enumerate(
                lows,
                start=1
            ):

                lines.append(
                    f"{i}. 🟢 "
                    f"{fmt_price(level['price'])}\n"
                    f"   LONG | "
                    f"touches: "
                    f"{level.get('touches', '—')} | "
                    f"strength: "
                    f"{level.get('strength', '—')}"
                )

        else:

            lines.append(
                "Нет крупных уровней."
            )

        lines.extend(
            [
                "",
                "⚠️ Уровни — не сигнал входа.",
                "Ждём sweep → 15M → 5M confirmation."
            ]
        )

        await update.message.reply_text(
            "\n".join(lines)
        )

    except Exception as e:

        logger.exception("Levels error")

        await update.message.reply_text(
            f"❌ Ошибка уровней:\n{e}"
        )


# ============================================================
# /SUBSCRIBE
# ============================================================

async def subscribe(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat_id = update.effective_chat.id

    subscribers.add(chat_id)

    await update.message.reply_text(
        "🔔 Уведомления TradeMind включены."
    )


# ============================================================
# /UNSUBSCRIBE
# ============================================================

async def unsubscribe(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    chat_id = update.effective_chat.id

    subscribers.discard(chat_id)

    await update.message.reply_text(
        "🔕 Уведомления TradeMind отключены."
    )


# ============================================================
# /JOURNAL
# ============================================================

async def journal(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(update)

    await update.message.reply_text(
        "📒 TRADEMIND — ЖУРНАЛ\n\n"
        f"Сделок сегодня: "
        f"{trades_today}/{MAX_TRADES_PER_DAY}\n"
        f"Daily stop: "
        f"{'YES' if daily_stop else 'NO'}\n\n"
        "Автоматический журнал сделок "
        "будет использоваться при подключении "
        "исполнения BingX."
    )


# ============================================================
# /BINGX_STATUS
# ============================================================

async def bingx_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(update)

    if bingx is None:

        await update.message.reply_text(
            "🔴 BingX module не подключён.\n"
            f"Mode: {BINGX_MODE}"
        )

        return

    await update.message.reply_text(
        "🟢 BingX module найден.\n"
        f"Mode: {BINGX_MODE}"
    )


# ============================================================
# /BALANCE
# ============================================================

async def balance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(update)

    if bingx is None:

        await update.message.reply_text(
            "❌ BingX module недоступен."
        )

        return

    try:

        if hasattr(bingx, "get_balance"):

            result = bingx.get_balance()

            await update.message.reply_text(
                f"💰 BingX balance:\n{result}"
            )

        else:

            await update.message.reply_text(
                "⚠️ get_balance не найден "
                "в модуле BingX."
            )

    except Exception as e:

        logger.exception("Balance error")

        await update.message.reply_text(
            f"❌ Ошибка BingX balance:\n{e}"
        )


# ============================================================
# /POSITION
# ============================================================

async def position(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(update)

    if bingx is None:

        await update.message.reply_text(
            "❌ BingX module недоступен."
        )

        return

    try:

        if hasattr(bingx, "get_position"):

            result = bingx.get_position()

            await update.message.reply_text(
                f"📌 BingX position:\n{result}"
            )

        else:

            await update.message.reply_text(
                "⚠️ get_position не найден "
                "в модуле BingX."
            )

    except Exception as e:

        logger.exception("Position error")

        await update.message.reply_text(
            f"❌ Ошибка BingX position:\n{e}"
        )


# ============================================================
# MONITOR
# ============================================================

async def monitor(application):

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
                    MONITOR_INTERVAL
                )

                continue

            if trades_today >= MAX_TRADES_PER_DAY:

                daily_stop = True

                logger.info(
                    "Daily trade limit reached."
                )

                await asyncio.sleep(
                    MONITOR_INTERVAL
                )

                continue

            if not subscribers:

                await asyncio.sleep(
                    MONITOR_INTERVAL
                )

                continue

            symbol, setup = find_first_ready()

            if symbol is None:

                await asyncio.sleep(
                    MONITOR_INTERVAL
                )

                continue

            direction = get_direction(setup)

            score = get_score(setup)

            stage = setup.get("stage")

            entry = setup.get(
                "entry",
                setup.get("entry_price")
            )

            sl = setup.get(
                "sl",
                setup.get("stop_loss")
            )

            tp = setup.get(
                "tp",
                setup.get("take_profit")
            )

            # =================================================
            # FINAL SAFETY CHECK
            # =================================================

            if not (
                stage == "READY"
                and direction in ("LONG", "SHORT")
                and score >= MIN_SCORE_READY
                and entry is not None
                and sl is not None
                and tp is not None
            ):

                logger.warning(
                    "Signal rejected by final safety check: "
                    "%s | stage=%s | score=%s | direction=%s",
                    symbol,
                    stage,
                    score,
                    direction,
                )

                await asyncio.sleep(
                    MONITOR_INTERVAL
                )

                continue

            cooldown_key = (
                symbol,
                direction
            )

            current_time = now()

            previous_signal = last_signal_time.get(
                cooldown_key
            )

            if previous_signal is not None:

                elapsed = (
                    current_time -
                    previous_signal
                ).total_seconds() / 60

                if elapsed < SIGNAL_COOLDOWN_MINUTES:

                    await asyncio.sleep(
                        MONITOR_INTERVAL
                    )

                    continue

            last_signal_time[
                cooldown_key
            ] = current_time

            last_setups[symbol] = setup

            message = (

                "🚨 TRADEMIND 5.3 — "
                "ГОТОВЫЙ СЕТАП\n\n"

                f"💠 {symbol}\n"

                f"📐 {direction_emoji(direction)} "
                f"{direction}\n"

                f"⭐ Score: {score}/100\n\n"

                f"💰 Entry: "
                f"{fmt_price(entry)}\n"

                f"🛑 SL: "
                f"{fmt_price(sl)}\n"

                f"🏁 TP: "
                f"{fmt_price(tp)}\n\n"

                f"⚖️ RR: "
                f"{setup.get('rr', '—')}\n\n"

                "🟢 МОЖНО ВХОДИТЬ"
            )

            for chat_id in list(subscribers):

                try:

                    await application.bot.send_message(
                        chat_id=chat_id,
                        text=message
                    )

                except Exception as e:

                    logger.error(
                        "Telegram send error %s: %s",
                        chat_id,
                        e
                    )

            # =================================================
            # BINGX EXECUTION
            # =================================================

            if (
                BINGX_MODE == "ON"
                and bingx is not None
            ):

                try:

                    if hasattr(
                        bingx,
                        "open_trade"
                    ):

                        result = bingx.open_trade(
                            setup
                        )

                        active_trade = result

                        logger.info(
                            "BingX trade opened: %s",
                            result
                        )

                        trades_today += 1

                        if trades_today >= MAX_TRADES_PER_DAY:

                            daily_stop = True

                    else:

                        logger.warning(
                            "bingx.open_trade "
                            "not found."
                        )

                except Exception as e:

                    logger.exception(
                        "BingX execution error: %s",
                        e
                    )

            await asyncio.sleep(
                MONITOR_INTERVAL
            )

        except Exception as e:

            logger.exception(
                "Monitor error: %s",
                e
            )

            await asyncio.sleep(
                MONITOR_INTERVAL
            )


# ============================================================
# POST INIT
# ============================================================

async def post_init(application):

    commands = [

        BotCommand(
            "start",
            "Запустить TradeMind"
        ),

        BotCommand(
            "sol",
            "Анализ SOL"
        ),

        BotCommand(
            "eth",
            "Анализ ETH"
        ),

        BotCommand(
            "market",
            "Весь рынок"
        ),

        BotCommand(
            "levels",
            "Крупная ликвидность"
        ),

        BotCommand(
            "search",
            "Поиск сетапа"
        ),

        BotCommand(
            "status",
            "Статус"
        ),

        BotCommand(
            "subscribe",
            "Включить уведомления"
        ),

        BotCommand(
            "unsubscribe",
            "Выключить уведомления"
        ),

        BotCommand(
            "journal",
            "Журнал"
        ),

        BotCommand(
            "bingx_status",
            "Статус BingX"
        ),

        BotCommand(
            "balance",
            "Баланс BingX"
        ),

        BotCommand(
            "position",
            "Позиция BingX"
        ),
    ]

    await application.bot.set_my_commands(
        commands
    )

    application.create_task(
        monitor(application)
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN environment variable is not set."
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # ========================================================
    # COMMAND HANDLERS
    # ========================================================

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
            sol
        )
    )

    application.add_handler(
        CommandHandler(
            "eth",
            eth
        )
    )

    application.add_handler(
        CommandHandler(
            "market",
            market
        )
    )

    application.add_handler(
        CommandHandler(
            "search",
            search
        )
    )

    application.add_handler(
        CommandHandler(
            "levels",
            levels
        )
    )

    application.add_handler(
        CommandHandler(
            "subscribe",
            subscribe
        )
    )

    application.add_handler(
        CommandHandler(
            "unsubscribe",
            unsubscribe
        )
    )

    application.add_handler(
        CommandHandler(
            "journal",
            journal
        )
    )

    application.add_handler(
        CommandHandler(
            "bingx_status",
            bingx_status
        )
    )

    application.add_handler(
        CommandHandler(
            "balance",
            balance
        )
    )

    application.add_handler(
        CommandHandler(
            "position",
            position
        )
    )

    # ========================================================
    # START
    # ========================================================

    logger.info(
        "TradeMind 5.3 starting..."
    )

    application.run_polling()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()