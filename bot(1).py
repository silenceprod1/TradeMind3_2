# ============================================================
# TradeMind 5.3
# main.py
#
# D1/W1 → 1H → Major Liquidity → Sweep
# → 15M Confirmation → 5M V/L
# → Recovery → 5M FVG Inversion
# → Entry → SL → Structural TP
#
# Binance Spot = источник анализа
# BingX Futures = исполнение, сейчас OFF
# ============================================================

import os
import asyncio
import logging
from datetime import datetime

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
    detect_fresh_sweep,
    STRATEGY_VERSION,
    MIN_SCORE_READY,
)


# ============================================================
# НАСТРОЙКИ
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден в переменных окружения")


# ------------------------------------------------------------
# BingX
# ------------------------------------------------------------

BINGX_MODE = os.getenv("BINGX_MODE", "OFF").upper()

try:
    import bingx
    BINGX_AVAILABLE = True
except Exception:
    bingx = None
    BINGX_AVAILABLE = False


# ------------------------------------------------------------
# Монеты для автоматического сканирования
# ------------------------------------------------------------

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


# ------------------------------------------------------------
# Ограничения
# ------------------------------------------------------------

MAX_TRADES_PER_DAY = 2

MONITOR_INTERVAL = 30

SIGNAL_COOLDOWN_MINUTES = 5

# Максимальное расстояние от текущей цены до Entry,
# после которого не преследуем движение.
MAX_ENTRY_DISTANCE_PCT = 0.75


# ============================================================
# СОСТОЯНИЕ
# ============================================================

trades_today = 0
daily_stop = False

active_trade = None

last_setups = {}

last_signal_time = {}

state_date = None


# ============================================================
# ЛОГИ
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("TradeMind")


# ============================================================
# СБРОС ДНЕВНОГО СОСТОЯНИЯ
# ============================================================

def reset_daily_state():
    global trades_today
    global daily_stop
    global active_trade
    global state_date

    today = datetime.utcnow().date()

    if state_date != today:
        state_date = today

        trades_today = 0
        daily_stop = False
        active_trade = None

        logger.info("Daily state reset")


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def now():
    return datetime.utcnow()


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


def fmt_pct(value):
    if value is None:
        return "—"

    try:
        return f"{float(value):.2f}%"
    except Exception:
        return str(value)


def get_value(data, *keys, default=None):
    """
    Безопасно достаёт значение из словаря.
    """

    if not isinstance(data, dict):
        return default

    for key in keys:
        if key in data:
            return data[key]

    return default


def direction_emoji(direction):
    if direction == "LONG":
        return "🟢"

    if direction == "SHORT":
        return "🔴"

    return "⚪"


def stage_text(stage):
    mapping = {
        "D1": "D1 TREND",
        "W1": "W1 TREND",
        "1H": "1H CONTEXT",
        "LIQUIDITY": "MAJOR LIQUIDITY",
        "WAIT": "WAIT",
        "SWEPT": "SWEEP",
        "15M_CONFIRMED": "15M CONFIRMATION",
        "CONFIRMED": "5M CONFIRMED",
        "READY": "READY",
    }

    return mapping.get(stage, str(stage))


# ============================================================
# АНАЛИЗ РЫНКА
# ============================================================

def build_analysis(symbol):
    """
    Получает Binance Spot данные и запускает TradeMind 5.3.
    """

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
        candles_d1,
        candles_w1,
        candles_1h,
        candles_15m,
        candles_5m,
        price,
    )

    if isinstance(result, dict):
        result["symbol"] = symbol
        result["price"] = price

    return result


# ============================================================
# ПОИСК ГОТОВОГО СЕТАПА
# ============================================================

def find_first_ready():
    """
    Сканирует монеты.
    Возвращает первый полноценный READY-сетап.
    """

    reset_daily_state()

    for symbol in SYMBOLS:

        try:
            result = build_analysis(symbol)

            if not isinstance(result, dict):
                continue

            stage = result.get("stage")

            score = result.get(
                "score",
                result.get("total_score", 0)
            )

            try:
                score = int(score)
            except Exception:
                score = 0

            last_setups[symbol] = result

            logger.info(
                "%s | stage=%s | score=%s",
                symbol,
                stage,
                score,
            )

            if stage == "READY" and score >= MIN_SCORE_READY:
                return result

        except Exception as e:
            logger.exception(
                "Analysis error %s: %s",
                symbol,
                e,
            )

    return None


# ============================================================
# ФОРМАТ СЕТАПА
# ============================================================

def format_setup(setup):
    symbol = setup.get("symbol", "UNKNOWN")

    price = setup.get("price")

    direction = setup.get(
        "direction",
        setup.get("side")
    )

    score = setup.get(
        "score",
        setup.get("total_score", 0)
    )

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

    stage = setup.get("stage", "UNKNOWN")

    sweep = setup.get(
        "sweep",
        setup.get("fresh_sweep")
    )

    confirmation = setup.get(
        "confirmation_15m",
        setup.get("confirm_15m")
    )

    recovery = setup.get(
        "recovery_ratio",
        setup.get("recovery")
    )

    fvg = setup.get(
        "fvg_inversion",
        setup.get("fvg")
    )

    emoji = direction_emoji(direction)

    # --------------------------------------------------------
    # Recovery
    # --------------------------------------------------------

    if recovery is None:
        recovery_text = "—"
    else:
        try:
            recovery_pct = float(recovery) * 100
            recovery_text = f"{recovery_pct:.0f}%"
        except Exception:
            recovery_text = str(recovery)

    # --------------------------------------------------------
    # Sweep
    # --------------------------------------------------------

    if sweep:
        sweep_level = get_value(
            sweep,
            "level",
            "price"
        )

        sweep_text = fmt_price(sweep_level)

    else:
        sweep_text = "—"

    # --------------------------------------------------------
    # FVG
    # --------------------------------------------------------

    if fvg is True:
        fvg_text = "YES"

    elif fvg is False:
        fvg_text = "NO"

    else:
        fvg_text = "—"

    # --------------------------------------------------------
    # Confirmation
    # --------------------------------------------------------

    if confirmation is True:
        confirmation_text = "YES"

    elif confirmation is False:
        confirmation_text = "NO"

    else:
        confirmation_text = "—"

    # --------------------------------------------------------
    # RR
    # --------------------------------------------------------

    rr = setup.get("rr")

    if rr is None and entry is not None and sl is not None and tp is not None:

        try:
            risk = abs(float(entry) - float(sl))
            reward = abs(float(tp) - float(entry))

            if risk > 0:
                rr = reward / risk

        except Exception:
            rr = None

    rr_text = "—"

    if rr is not None:

        try:
            rr_text = f"1:{float(rr):.2f}"

        except Exception:
            rr_text = str(rr)

    # --------------------------------------------------------
    # READY
    # --------------------------------------------------------

    ready_text = (
        "🟢 МОЖНО РАССМАТРИВАТЬ ВХОД"
        if stage == "READY"
        else "⏳ ВХОД ПОКА ЗАПРЕЩЁН"
    )

    return (
        f"🔎 TRADEMIND {STRATEGY_VERSION}\n\n"

        f"💠 {symbol}\n"
        f"📐 {emoji} {direction or '—'}\n"
        f"⭐ Score: {score}/100\n"
        f"📊 Stage: {stage_text(stage)}\n\n"

        f"💰 Цена: {fmt_price(price)}\n\n"

        f"💧 Sweep: {sweep_text}\n"
        f"🕐 15M confirmation: {confirmation_text}\n"
        f"↩️ Recovery: {recovery_text}\n"
        f"🔄 5M FVG inversion: {fvg_text}\n\n"

        f"🎯 Entry: {fmt_price(entry)}\n"
        f"🛑 SL: {fmt_price(sl)}\n"
        f"🏁 TP: {fmt_price(tp)}\n"
        f"⚖️ RR: {rr_text}\n\n"

        f"{ready_text}"
    )


# ============================================================
# ГЛАВНОЕ МЕНЮ
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    track_chat(update)

    text = (
        "🤖 TRADEMIND 5.3\n\n"

        "Стратегия:\n"
        "D1/W1 → 1H → Major Liquidity\n"
        "→ Sweep → 15M Confirmation\n"
        "→ 5M V/L → Recovery\n"
        "→ 5M FVG inversion\n"
        "→ Entry → SL → Structural TP\n\n"

        f"📊 BingX mode: {BINGX_MODE}\n\n"

        "Команды:\n"
        "/status — статус бота\n"
        "/sol — анализ SOL\n"
        "/eth — анализ ETH\n"
        "/market — рынок монет\n"
        "/search — поиск сетапа\n"
        "/levels — крупная ликвидность\n"
        "/chart — график/цена\n"
        "/journal — журнал\n"
        "/subscribe — включить уведомления\n"
        "/unsubscribe — выключить уведомления\n"
        "/bingx — статус BingX\n"
        "/balance — баланс BingX\n"
        "/position — позиции BingX\n"
        "/help — помощь"
    )

    await update.message.reply_text(text)


# ============================================================
# HELP
# ============================================================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    track_chat(update)

    await update.message.reply_text(
        "📚 TRADEMIND 5.3\n\n"

        "Основная логика:\n"
        "D1/W1 → 1H → крупная ликвидность\n"
        "→ Sweep → 15M confirmation\n"
        "→ 5M V/L → Recovery ≥ 1/3\n"
        "→ 5M FVG inversion\n"
        "→ Entry → SL → Structural TP\n\n"

        "Главное правило:\n"
        "❌ Нет подтверждения → нет входа.\n"
        "❌ Не входить в середине движения.\n"
        "❌ Не преследовать цену.\n"
        "❌ Не брать мелкую ликвидность.\n\n"

        "Команды:\n"
        "/sol\n"
        "/eth\n"
        "/market\n"
        "/search\n"
        "/levels\n"
        "/chart\n"
        "/status\n"
        "/journal\n"
        "/subscribe\n"
        "/unsubscribe"
    )


# ============================================================
# STATUS
# ============================================================

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):

    track_chat(update)
    reset_daily_state()

    active_text = "🟢 Нет активной сделки"

    if active_trade:
        active_symbol = active_trade.get("symbol", "UNKNOWN")
        active_direction = active_trade.get("direction", "—")

        active_text = (
            f"🔴 Активная сделка: "
            f"{active_symbol} {active_direction}"
        )

    await update.message.reply_text(
        f"📊 TRADEMIND {STRATEGY_VERSION} — СТАТУС\n\n"

        f"Стратегия: {STRATEGY_VERSION}\n"
        f"BingX mode: {BINGX_MODE}\n"
        f"BingX доступен: "
        f"{'YES' if BINGX_AVAILABLE else 'NO'}\n\n"

        f"Сделок сегодня: "
        f"{trades_today}/{MAX_TRADES_PER_DAY}\n"

        f"Daily stop: "
        f"{'YES' if daily_stop else 'NO'}\n\n"

        f"{active_text}"
    )


# ============================================================
# ANALYSIS SOL
# ============================================================

async def sol(update: Update, context: ContextTypes.DEFAULT_TYPE):

    track_chat(update)

    await update.message.reply_text(
        "🔎 Анализирую SOL по Binance Spot...\n"
        "D1/W1 → 1H → 15M → 5M"
    )

    try:
        result = build_analysis("SOLUSDT")

        await update.message.reply_text(
            format_setup(result)
        )

    except Exception as e:

        logger.exception("SOL analysis error")

        await update.message.reply_text(
            f"❌ Ошибка анализа SOL:\n{e}"
        )


# ============================================================
# ANALYSIS ETH
# ============================================================

async def eth(update: Update, context: ContextTypes.DEFAULT_TYPE):

    track_chat(update)

    await update.message.reply_text(
        "🔎 Анализирую ETH по Binance Spot..."
    )

    try:
        result = build_analysis("ETHUSDT")

        await update.message.reply_text(
            format_setup(result)
        )

    except Exception as e:

        logger.exception("ETH analysis error")

        await update.message.reply_text(
            f"❌ Ошибка анализа ETH:\n{e}"
        )


# ============================================================
# MARKET
# ============================================================

async def market_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(update)

    lines = [
        "📊 TRADEMIND — РЫНОК",
        "",
    ]

    for symbol in SYMBOLS:

        try:
            result = build_analysis(symbol)

            price = result.get("price")

            stage = result.get(
                "stage",
                "UNKNOWN"
            )

            score = result.get(
                "score",
                result.get("total_score", 0)
            )

            direction = result.get(
                "direction",
                result.get("side", "—")
            )

            emoji = direction_emoji(direction)

            lines.append(
                f"{symbol.replace('USDT', '')}: "
                f"{fmt_price(price)} | "
                f"{emoji} {direction} | "
                f"{stage} | "
                f"{score}/100"
            )

        except Exception as e:

            logger.error(
                "Market error %s: %s",
                symbol,
                e,
            )

            lines.append(
                f"{symbol.replace('USDT', '')}: ❌ ERROR"
            )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# SEARCH
# ============================================================

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):

    track_chat(update)
    reset_daily_state()

    if daily_stop:

        await update.message.reply_text(
            "🛑 Daily stop активирован.\n"
            "Новые сетапы сегодня запрещены."
        )

        return

    await update.message.reply_text(
        "🔎 Ищу новый сетап...\n\n"
        "Проверяю:\n"
        "D1/W1 → 1H → Major Liquidity\n"
        "→ Sweep → 15M → 5M"
    )

    try:

        setup = find_first_ready()

        if not setup:

            await update.message.reply_text(
                "❌ READY-сетап не найден.\n\n"
                "Ничего не форсируем."
            )

            return

        symbol = setup.get("symbol")

        last_setups[symbol] = setup

        await update.message.reply_text(
            format_setup(setup)
        )

    except Exception as e:

        logger.exception("Search error")

        await update.message.reply_text(
            f"❌ Ошибка поиска:\n{e}"
        )


# ============================================================
# LEVELS
# ============================================================

async def levels(update: Update, context: ContextTypes.DEFAULT_TYPE):

    track_chat(update)

    symbol = "SOLUSDT"

    if context.args:

        requested = context.args[0].upper()

        if not requested.endswith("USDT"):
            requested += "USDT"

        if requested in SYMBOLS:
            symbol = requested

    try:

        market = get_market_data(symbol)

        price = market["price"]

        candles_1h = market.get(
            "candles_1h",
            market.get("1h", [])
        )

        levels_data = get_major_liquidity(
            candles_1h,
            price,
        )

        if not levels_data:

            await update.message.reply_text(
                f"💧 {symbol}\n\n"
                "Крупная ликвидность не найдена."
            )

            return

        lines = [
            f"💧 TRADEMIND — КРУПНАЯ ЛИКВИДНОСТЬ",
            f"💠 {symbol}",
            f"💰 Цена: {fmt_price(price)}",
            "",
        ]

        for i, level in enumerate(
            levels_data,
            start=1
        ):

            level_price = level.get(
                "level",
                level.get("price")
            )

            side = level.get(
                "side",
                "—"
            )

            touches = level.get(
                "touches",
                0
            )

            liquidity_type = level.get(
                "liquidity_type",
                "Major"
            )

            emoji = (
                "🔴"
                if side == "SHORT"
                else "🟢"
            )

            lines.append(
                f"{i}. {emoji} "
                f"{fmt_price(level_price)}\n"
                f"   {side} | "
                f"touches: {touches}\n"
                f"   {liquidity_type}"
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
# CHART
# ============================================================

async def chart(update: Update, context: ContextTypes.DEFAULT_TYPE):

    track_chat(update)

    symbol = "SOLUSDT"

    if context.args:

        requested = context.args[0].upper()

        if not requested.endswith("USDT"):
            requested += "USDT"

        if requested in SYMBOLS:
            symbol = requested

    try:

        market = get_market_data(symbol)

        price = market["price"]

        await update.message.reply_text(
            f"📈 {symbol}\n\n"
            f"Источник: Binance Spot\n"
            f"Цена: {fmt_price(price)}\n\n"
            f"Таймфреймы:\n"
            f"D1 → W1 → 1H → 15M → 5M"
        )

    except Exception as e:

        logger.exception("Chart error")

        await update.message.reply_text(
            f"❌ Ошибка графика:\n{e}"
        )


# ============================================================
# SUBSCRIBERS
# ============================================================

def track_chat(update: Update):

    try:

        chat_id = update.effective_chat.id

        subscribers = (
            update.get_bot().application.bot_data
            .setdefault("subscribers", set())
        )

        subscribers.add(chat_id)

    except Exception as e:

        logger.error(
            "Could not track subscriber: %s",
            e,
        )


async def subscribe(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(update)

    subscribers = (
        context.application.bot_data
        .setdefault("subscribers", set())
    )

    subscribers.add(
        update.effective_chat.id
    )

    await update.message.reply_text(
        "🔔 Уведомления включены.\n\n"
        "Бот будет искать READY-сетапы "
        "по TradeMind 5.3."
    )


async def unsubscribe(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    subscribers = (
        context.application.bot_data
        .setdefault("subscribers", set())
    )

    chat_id = update.effective_chat.id

    subscribers.discard(chat_id)

    await update.message.reply_text(
        "🔕 Уведомления выключены."
    )


# ============================================================
# JOURNAL
# ============================================================

async def journal(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(update)

    await update.message.reply_text(
        "📒 TRADEMIND — ЖУРНАЛ\n\n"

        "Сейчас журнал работает в режиме "
        "мониторинга сигналов.\n\n"

        "Автоматическая статистика сделок "
        "будет считаться после подключения "
        "учёта фактических результатов BingX."
    )


# ============================================================
# BINGX STATUS
# ============================================================

async def bingx_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(update)

    await update.message.reply_text(
        "📊 BINGX STATUS\n\n"
        f"Mode: {BINGX_MODE}\n"
        f"Module: "
        f"{'AVAILABLE' if BINGX_AVAILABLE else 'NOT AVAILABLE'}"
    )


# ============================================================
# BINGX BALANCE
# ============================================================

async def balance(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(update)

    if not BINGX_AVAILABLE:

        await update.message.reply_text(
            "❌ BingX модуль недоступен."
        )

        return

    if BINGX_MODE != "ON":

        await update.message.reply_text(
            "📊 BingX mode: OFF\n\n"
            "Автоматическое получение баланса "
            "не используется."
        )

        return

    try:

        if hasattr(bingx, "get_balance"):

            result = await asyncio.to_thread(
                bingx.get_balance
            )

            await update.message.reply_text(
                f"💰 BingX баланс:\n\n{result}"
            )

        else:

            await update.message.reply_text(
                "⚠️ В текущем bingx.py "
                "нет функции get_balance()."
            )

    except Exception as e:

        logger.exception("Balance error")

        await update.message.reply_text(
            f"❌ Ошибка BingX:\n{e}"
        )


# ============================================================
# BINGX POSITIONS
# ============================================================

async def position(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(update)

    if not BINGX_AVAILABLE:

        await update.message.reply_text(
            "❌ BingX модуль недоступен."
        )

        return

    if BINGX_MODE != "ON":

        await update.message.reply_text(
            "📊 BingX mode: OFF"
        )

        return

    try:

        if hasattr(bingx, "get_positions"):

            result = await asyncio.to_thread(
                bingx.get_positions
            )

            await update.message.reply_text(
                f"📌 BingX позиции:\n\n{result}"
            )

        else:

            await update.message.reply_text(
                "⚠️ В текущем bingx.py "
                "нет функции get_positions()."
            )

    except Exception as e:

        logger.exception("Position error")

        await update.message.reply_text(
            f"❌ Ошибка BingX:\n{e}"
        )


# ============================================================
# MONITOR
# ============================================================

async def monitor(application):

    global trades_today
    global daily_stop
    global active_trade

    logger.info(
        "TradeMind monitor started"
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

                await asyncio.sleep(
                    MONITOR_INTERVAL
                )

                continue

            subscribers = (
                application.bot_data
                .get("subscribers", set())
            )

            if not subscribers:

                await asyncio.sleep(
                    MONITOR_INTERVAL
                )

                continue

            setup = find_first_ready()

            if setup:

                symbol = setup.get(
                    "symbol",
                    "UNKNOWN"
                )

                direction = setup.get(
                    "direction",
                    setup.get("side")
                )

                # ------------------------------------------------
                # Защита от повторного сигнала
                # ------------------------------------------------

                key = f"{symbol}:{direction}"

                current_time = now()

                previous_time = last_signal_time.get(key)

                if previous_time:

                    elapsed = (
                        current_time - previous_time
                    ).total_seconds() / 60

                    if elapsed < SIGNAL_COOLDOWN_MINUTES:

                        await asyncio.sleep(
                            MONITOR_INTERVAL
                        )

                        continue

                last_signal_time[key] = current_time

                last_setups[symbol] = setup

                message = (
                    "🚨 TRADEMIND 5.3 — READY\n\n"
                    + format_setup(setup)
                )

                # ------------------------------------------------
                # BingX OFF = только сигнал
                # ------------------------------------------------

                if BINGX_MODE != "ON":

                    execution_text = (
                        "\n\n"
                        "📊 BingX mode: OFF\n"
                        "⚠️ Сделка НЕ открыта.\n"
                        "Это только сигнал."
                    )

                    message += execution_text

                # ------------------------------------------------
                # BingX ON
                # ------------------------------------------------

                else:

                    if not BINGX_AVAILABLE:

                        message += (
                            "\n\n"
                            "❌ BingX module unavailable."
                        )

                    else:

                        try:

                            if hasattr(
                                bingx,
                                "open_trade"
                            ):

                                result = await asyncio.to_thread(
                                    bingx.open_trade,
                                    setup
                                )

                                active_trade = setup

                                trades_today += 1

                                message += (
                                    "\n\n"
                                    "🟢 BingX: "
                                    f"{result}"
                                )

                            else:

                                message += (
                                    "\n\n"
                                    "⚠️ bingx.py не содержит "
                                    "open_trade()."
                                )

                        except Exception as e:

                            logger.exception(
                                "BingX execution error"
                            )

                            message += (
                                "\n\n"
                                f"❌ Ошибка BingX:\n{e}"
                            )

                # ------------------------------------------------
                # Отправка подписчикам
                # ------------------------------------------------

                for chat_id in list(subscribers):

                    try:

                        await application.bot.send_message(
                            chat_id=chat_id,
                            text=message,
                        )

                    except Exception as e:

                        logger.error(
                            "Send error %s: %s",
                            chat_id,
                            e,
                        )

        except Exception as e:

            logger.exception(
                "Monitor error: %s",
                e,
            )

        await asyncio.sleep(
            MONITOR_INTERVAL
        )


# ============================================================
# POST INIT
# ============================================================

async def post_init(application):

    await application.bot.set_my_commands(
        [
            BotCommand(
                "start",
                "Главное меню"
            ),
            BotCommand(
                "market",
                "Рынок монет"
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
                "chart",
                "График"
            ),
            BotCommand(
                "status",
                "Статус"
            ),
            BotCommand(
                "bingx",
                "BingX статус"
            ),
            BotCommand(
                "balance",
                "BingX баланс"
            ),
            BotCommand(
                "position",
                "BingX позиции"
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
                "sol",
                "Анализ SOL"
            ),
            BotCommand(
                "eth",
                "Анализ ETH"
            ),
            BotCommand(
                "journal",
                "Журнал"
            ),
            BotCommand(
                "help",
                "Помощь"
            ),
        ]
    )

    application.create_task(
        monitor(application)
    )

    logger.info(
        "TradeMind %s started",
        STRATEGY_VERSION
    )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.exception(
        "Telegram error: %s",
        context.error,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # --------------------------------------------------------
    # Основные команды
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Анализ
    # --------------------------------------------------------

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
            market_command
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
            "chart",
            chart
        )
    )

    # --------------------------------------------------------
    # Уведомления
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Журнал
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "journal",
            journal
        )
    )

    # --------------------------------------------------------
    # BingX
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "bingx",
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

    # --------------------------------------------------------
    # Errors
    # --------------------------------------------------------

    application.add_error_handler(
        error_handler
    )

    logger.info(
        "Starting TradeMind %s",
        STRATEGY_VERSION
    )

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()