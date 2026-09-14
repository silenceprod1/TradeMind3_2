# ============================================================
# TradeMind 5.3
# main.py
#
# D1/W1 → 1H → Major Liquidity → Sweep
# → 15M Confirmation → 5M V/L
# → Recovery → 5M FVG inversion
# → Entry → SL → Structural TP
#
# Анализ: Binance Spot
# Исполнение: BingX Futures
# Сейчас BingX MODE = OFF
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
    STRATEGY_VERSION,
    MIN_SCORE_READY,
)


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не найден в переменных окружения"
    )


BINGX_MODE = os.getenv(
    "BINGX_MODE",
    "OFF"
).upper()


try:
    import bingx

    BINGX_AVAILABLE = True

except Exception:

    bingx = None

    BINGX_AVAILABLE = False


# ============================================================
# MONITORING COINS
# ============================================================

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


# ============================================================
# LIMITS
# ============================================================

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

state_date = None


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(
    "TradeMind"
)


# ============================================================
# DAILY STATE
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

        logger.info(
            "Daily state reset"
        )


# ============================================================
# HELPERS
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


def direction_emoji(direction):

    if direction == "LONG":
        return "🟢"

    if direction == "SHORT":
        return "🔴"

    return "⚪"


def stage_text(stage):

    stages = {

        "D1": "D1 TREND",

        "W1": "W1 TREND",

        "1H": "1H CONTEXT",

        "LIQUIDITY": "MAJOR LIQUIDITY",

        "WAIT": "WAIT",

        "SWEPT": "SWEEP",

        "15M_CONFIRMED":
            "15M CONFIRMATION",

        "CONFIRMED":
            "5M CONFIRMED",

        "READY":
            "READY",
    }

    return stages.get(
        stage,
        str(stage)
    )


# ============================================================
# BUILD ANALYSIS
# ============================================================

def build_analysis(symbol):

    market = get_market_data(
        symbol
    )

    price = market["price"]


    candles_d1 = market.get(
        "candles_d1",
        market.get(
            "d1",
            []
        )
    )


    candles_w1 = market.get(
        "candles_w1",
        market.get(
            "w1",
            []
        )
    )


    candles_1h = market.get(
        "candles_1h",
        market.get(
            "1h",
            []
        )
    )


    candles_15m = market.get(
        "candles_15m",
        market.get(
            "15m",
            []
        )
    )


    candles_5m = market.get(
        "candles_5m",
        market.get(
            "5m",
            []
        )
    )


    # ========================================================
    # MAIN STRATEGY
    #
    # D1/W1
    # ↓
    # 1H
    # ↓
    # Major Liquidity
    # ↓
    # Sweep
    # ↓
    # 15M
    # ↓
    # 5M
    # ========================================================

    result = analyze(

        candles_1h=candles_1h,

        candles_15m=candles_15m,

        candles_5m=candles_5m,

        price=price,

        candles_d1=candles_d1,

        candles_w1=candles_w1,
    )


    if isinstance(
        result,
        dict
    ):

        result["symbol"] = symbol

        result["price"] = price


    return result


# ============================================================
# FIND READY SETUP
# ============================================================

def find_first_ready():

    reset_daily_state()


    for symbol in SYMBOLS:

        try:

            result = build_analysis(
                symbol
            )


            if not isinstance(
                result,
                dict
            ):
                continue


            stage = result.get(
                "stage"
            )


            score = result.get(
                "score",
                result.get(
                    "total_score",
                    0
                )
            )


            try:

                score = int(
                    score
                )

            except Exception:

                score = 0


            last_setups[
                symbol
            ] = result


            logger.info(
                "%s | stage=%s | score=%s",
                symbol,
                stage,
                score,
            )


            if (
                stage == "READY"
                and
                score >= MIN_SCORE_READY
            ):

                return result


        except Exception as e:

            logger.exception(
                "Analysis error %s: %s",
                symbol,
                e,
            )


    return None


# ============================================================
# FORMAT SETUP
# ============================================================

def format_setup(setup):

    symbol = setup.get(
        "symbol",
        "UNKNOWN"
    )

    price = setup.get(
        "price"
    )

    direction = setup.get(
        "direction",
        setup.get(
            "side"
        )
    )

    score = setup.get(
        "score",
        setup.get(
            "total_score",
            0
        )
    )

    entry = setup.get(
        "entry",
        setup.get(
            "entry_price"
        )
    )

    sl = setup.get(
        "sl",
        setup.get(
            "stop_loss"
        )
    )

    tp = setup.get(
        "tp",
        setup.get(
            "take_profit"
        )
    )

    stage = setup.get(
        "stage",
        "UNKNOWN"
    )

    sweep = setup.get(
        "sweep"
    )

    if sweep is None:

        sweep = setup.get(
            "manipulation"
        )

    confirmation = setup.get(
        "confirmation_15m"
    )

    recovery = setup.get(
        "recovery_ratio"
    )

    fvg = setup.get(
        "imbalance_5m"
    )


    # ========================================================
    # DIRECTION
    # ========================================================

    emoji = direction_emoji(
        direction
    )


    # ========================================================
    # RECOVERY
    # ========================================================

    if recovery is None:

        recovery_text = "—"

    else:

        try:

            recovery_text = (
                f"{float(recovery) * 100:.0f}%"
            )

        except Exception:

            recovery_text = str(
                recovery
            )


    # ========================================================
    # SWEEP
    # ========================================================

    if isinstance(
        sweep,
        dict
    ):

        sweep_level = sweep.get(
            "level",
            sweep.get(
                "price"
            )
        )

        sweep_text = fmt_price(
            sweep_level
        )

    else:

        sweep_text = "—"


    # ========================================================
    # 15M
    # ========================================================

    if confirmation is True:

        confirmation_text = "YES"

    elif confirmation is False:

        confirmation_text = "NO"

    else:

        confirmation_text = "—"


    # ========================================================
    # FVG
    # ========================================================

    if isinstance(
        fvg,
        dict
    ):

        fvg_text = "YES"

    elif fvg is True:

        fvg_text = "YES"

    elif fvg is False:

        fvg_text = "NO"

    else:

        fvg_text = "—"


    # ========================================================
    # RR
    # ========================================================

    rr = setup.get(
        "rr"
    )


    if (
        rr is None
        and
        entry is not None
        and
        sl is not None
        and
        tp is not None
    ):

        try:

            risk = abs(
                float(entry)
                -
                float(sl)
            )

            reward = abs(
                float(tp)
                -
                float(entry)
            )

            if risk > 0:

                rr = (
                    reward
                    /
                    risk
                )

        except Exception:

            rr = None


    if rr is None:

        rr_text = "—"

    else:

        try:

            rr_text = (
                f"1:{float(rr):.2f}"
            )

        except Exception:

            rr_text = str(
                rr
            )


    # ========================================================
    # READY STATUS
    # ========================================================

    if stage == "READY":

        ready_text = (
            "🟢 ГОТОВЫЙ СЕТАП"
        )

    else:

        ready_text = (
            "⏳ ВХОД ПОКА ЗАПРЕЩЁН"
        )


    # ========================================================
    # FINAL MESSAGE
    # ========================================================

    return (

        f"🔎 TRADEMIND "
        f"{STRATEGY_VERSION}\n\n"

        f"💠 {symbol}\n"

        f"📐 {emoji} "
        f"{direction or '—'}\n"

        f"⭐ Score: "
        f"{score}/100\n"

        f"📊 Stage: "
        f"{stage_text(stage)}\n\n"

        f"💰 Цена: "
        f"{fmt_price(price)}\n\n"

        f"💧 Sweep: "
        f"{sweep_text}\n"

        f"🕐 15M confirmation: "
        f"{confirmation_text}\n"

        f"↩️ Recovery: "
        f"{recovery_text}\n"

        f"🔄 5M FVG inversion: "
        f"{fvg_text}\n\n"

        f"🎯 Entry: "
        f"{fmt_price(entry)}\n"

        f"🛑 SL: "
        f"{fmt_price(sl)}\n"

        f"🏁 TP: "
        f"{fmt_price(tp)}\n"

        f"⚖️ RR: "
        f"{rr_text}\n\n"

        f"{ready_text}"
    )


# ============================================================
# TRACK CHAT
# ============================================================

def track_chat(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:

        chat_id = (
            update.effective_chat.id
        )

        subscribers = (
            context.application
            .bot_data
            .setdefault(
                "subscribers",
                set()
            )
        )

        subscribers.add(
            chat_id
        )

    except Exception as e:

        logger.error(
            "Track chat error: %s",
            e,
        )


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(
        update,
        context
    )

    text = (

        "🤖 TRADEMIND 5.3\n\n"

        "Стратегия:\n"

        "D1/W1 → 1H → Major Liquidity\n"
        "→ Sweep → 15M Confirmation\n"
        "→ 5M V/L → Recovery\n"
        "→ 5M FVG inversion\n"
        "→ Entry → SL → Structural TP\n\n"

        f"📊 BingX mode: "
        f"{BINGX_MODE}\n\n"

        "Команды:\n"

        "/status — статус\n"
        "/sol — анализ SOL\n"
        "/eth — анализ ETH\n"
        "/market — рынок\n"
        "/search — поиск сетапа\n"
        "/levels — крупная ликвидность\n"
        "/chart — цена\n"
        "/journal — журнал\n"
        "/subscribe — уведомления\n"
        "/unsubscribe — отключить\n"
        "/bingx — BingX статус\n"
        "/balance — BingX баланс\n"
        "/position — позиции\n"
        "/help — помощь"
    )

    await update.message.reply_text(
        text
    )


# ============================================================
# HELP
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(
        update,
        context
    )

    await update.message.reply_text(

        "📚 TRADEMIND 5.3\n\n"

        "Логика:\n"

        "D1/W1 → 1H → Major Liquidity\n"
        "→ Sweep → 15M confirmation\n"
        "→ 5M V/L → Recovery ≥ 1/3\n"
        "→ 5M FVG inversion\n"
        "→ Entry → SL → Structural TP\n\n"

        "Правила:\n"

        "❌ Нет подтверждения → нет входа.\n"
        "❌ Не входить в середине.\n"
        "❌ Не преследовать цену.\n"
        "❌ Не брать мелкую ликвидность.\n"
        "❌ D1/1H конфликт → нет входа.\n\n"

        "Команды:\n"

        "/sol\n"
        "/eth\n"
        "/market\n"
        "/search\n"
        "/levels SOL\n"
        "/levels ETH\n"
        "/levels BTC\n"
        "/chart\n"
        "/status\n"
        "/journal"
    )


# ============================================================
# STATUS
# ============================================================

async def status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(
        update,
        context
    )

    reset_daily_state()

    if active_trade:

        active_text = (
            "🔴 Активная сделка: "
            f"{active_trade.get('symbol', 'UNKNOWN')} "
            f"{active_trade.get('direction', '—')}"
        )

    else:

        active_text = (
            "🟢 Активной сделки нет"
        )

    await update.message.reply_text(

        f"📊 TRADEMIND "
        f"{STRATEGY_VERSION} — СТАТУС\n\n"

        f"Strategy: "
        f"{STRATEGY_VERSION}\n"

        f"BingX mode: "
        f"{BINGX_MODE}\n"

        f"BingX module: "
        f"{'YES' if BINGX_AVAILABLE else 'NO'}\n\n"

        f"Сделок сегодня: "
        f"{trades_today}/"
        f"{MAX_TRADES_PER_DAY}\n"

        f"Daily stop: "
        f"{'YES' if daily_stop else 'NO'}\n\n"

        f"{active_text}"
    )


# ============================================================
# SOL
# ============================================================

async def sol(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(
        update,
        context
    )

    await update.message.reply_text(
        "🔎 Анализирую SOL...\n"
        "Binance Spot\n\n"
        "D1/W1 → 1H → 15M → 5M"
    )

    try:

        result = build_analysis(
            "SOLUSDT"
        )

        await update.message.reply_text(
            format_setup(
                result
            )
        )

    except Exception as e:

        logger.exception(
            "SOL error"
        )

        await update.message.reply_text(
            f"❌ Ошибка SOL:\n{e}"
        )


# ============================================================
# ETH
# ============================================================

async def eth(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(
        update,
        context
    )

    await update.message.reply_text(
        "🔎 Анализирую ETH...\n"
        "Binance Spot\n\n"
        "D1/W1 → 1H → 15M → 5M"
    )

    try:

        result = build_analysis(
            "ETHUSDT"
        )

        await update.message.reply_text(
            format_setup(
                result
            )
        )

    except Exception as e:

        logger.exception(
            "ETH error"
        )

        await update.message.reply_text(
            f"❌ Ошибка ETH:\n{e}"
        )


# ============================================================
# MARKET
# ============================================================

async def market_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(
        update,
        context
    )

    await update.message.reply_text(
        "📊 TRADEMIND — ПРОВЕРЯЮ РЫНОК..."
    )

    lines = [
        "📊 TRADEMIND — РЫНОК",
        "",
    ]

    for symbol in SYMBOLS:

        try:

            result = build_analysis(
                symbol
            )

            price = result.get(
                "price"
            )

            stage = result.get(
                "stage",
                "UNKNOWN"
            )

            score = result.get(
                "score",
                0
            )

            direction = result.get(
                "direction"
            )

            emoji = direction_emoji(
                direction
            )

            short_symbol = (
                symbol.replace(
                    "USDT",
                    ""
                )
            )

            lines.append(

                f"{short_symbol}: "
                f"{fmt_price(price)} | "
                f"{emoji} "
                f"{direction or '—'} | "
                f"{stage} | "
                f"{score}/100"
            )

        except Exception as e:

            logger.exception(
                "Market error %s",
                symbol
            )

            lines.append(

                f"{symbol.replace('USDT', '')}: "
                f"❌ ERROR"
            )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# SEARCH
# ============================================================

async def search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(
        update,
        context
    )

    reset_daily_state()

    if daily_stop:

        await update.message.reply_text(

            "🛑 DAILY STOP\n\n"
            "Новые сетапы сегодня запрещены."
        )

        return

    await update.message.reply_text(

        "🔎 Ищу новый сетап...\n\n"

        "D1/W1\n"
        "↓\n"
        "1H\n"
        "↓\n"
        "Major Liquidity\n"
        "↓\n"
        "Sweep\n"
        "↓\n"
        "15M\n"
        "↓\n"
        "5M"
    )

    try:

        setup = find_first_ready()

        if not setup:

            await update.message.reply_text(

                "❌ READY-сетап не найден.\n\n"
                "Ничего не форсируем."
            )

            return

        symbol = setup.get(
            "symbol"
        )

        last_setups[
            symbol
        ] = setup

        await update.message.reply_text(
            format_setup(
                setup
            )
        )

    except Exception as e:

        logger.exception(
            "Search error"
        )

        await update.message.reply_text(
            f"❌ Ошибка поиска:\n{e}"
        )


# ============================================================
# LEVELS
# ============================================================

async def levels(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(
        update,
        context
    )


    # --------------------------------------------------------
    # DEFAULT = SOL
    # --------------------------------------------------------

    symbol = "SOLUSDT"


    # --------------------------------------------------------
    # /levels ETH
    # /levels BTC
    # /levels SOLUSDT
    # --------------------------------------------------------

    if context.args:

        requested = (
            context.args[0]
            .strip()
            .upper()
        )


        if not requested.endswith(
            "USDT"
        ):

            requested += "USDT"


        if requested in SYMBOLS:

            symbol = requested

        else:

            await update.message.reply_text(

                "❌ Неизвестная монета.\n\n"

                "Доступно:\n"
                "SOL\n"
                "ETH\n"
                "BTC\n"
                "BNB\n"
                "XRP\n"
                "DOGE\n"
                "ADA\n"
                "AVAX\n"
                "LINK"
            )

            return


    try:

        market = get_market_data(
            symbol
        )


        price = float(
            market["price"]
        )


        candles_1h = market.get(
            "candles_1h",
            market.get(
                "1h",
                []
            )
        )


        levels_data = (
            get_major_liquidity(
                candles_1h,
                price
            )
        )


        if not levels_data:

            await update.message.reply_text(

                f"💧 TRADEMIND — "
                f"КРУПНАЯ ЛИКВИДНОСТЬ\n\n"

                f"💠 {symbol}\n"
                f"💰 Цена: {fmt_price(price)}\n\n"

                "Крупная ликвидность "
                "не найдена."
            )

            return


        # ----------------------------------------------------
        # IMPORTANT:
        # Отдельно разделяем уровни ВЫШЕ и НИЖЕ цены.
        #
        # HIGH выше цены = зона возможного SHORT sweep
        # LOW ниже цены  = зона возможного LONG sweep
        # ----------------------------------------------------

        highs = []
        lows = []


        for level in levels_data:

            try:

                level_price = float(
                    level.get(
                        "price"
                    )
                )

            except Exception:

                continue


            level_type = str(
                level.get(
                    "type",
                    ""
                )
            ).upper()


            if (
                level_type == "HIGH"
                and
                level_price > price
            ):

                highs.append(
                    level
                )


            elif (
                level_type == "LOW"
                and
                level_price < price
            ):

                lows.append(
                    level
                )


        # ----------------------------------------------------
        # SORT
        # ----------------------------------------------------

        highs.sort(
            key=lambda x: float(
                x.get("price", 0)
            )
        )


        lows.sort(
            key=lambda x: float(
                x.get("price", 0)
            ),
            reverse=True
        )


        # ----------------------------------------------------
        # MESSAGE
        # ----------------------------------------------------

        lines = [

            "💧 TRADEMIND — "
            "КРУПНАЯ ЛИКВИДНОСТЬ",

            "",

            f"💠 {symbol}",

            f"💰 Цена: "
            f"{fmt_price(price)}",

            "",
        ]


        # ====================================================
        # LIQUIDITY ABOVE
        # ====================================================

        lines.append(
            "🔴 ЛИКВИДНОСТЬ ВЫШЕ ЦЕНЫ"
        )

        lines.append(
            "Потенциальная зона SHORT sweep:"
        )

        lines.append("")


        if highs:

            for i, level in enumerate(
                highs,
                start=1
            ):

                level_price = level.get(
                    "price"
                )

                touches = level.get(
                    "touches",
                    0
                )

                strength = level.get(
                    "strength",
                    0
                )

                lines.append(

                    f"{i}. 🔴 "
                    f"{fmt_price(level_price)}\n"

                    f"   SHORT sweep | "
                    f"touches: {touches} | "
                    f"strength: {strength}"
                )

        else:

            lines.append(
                "— Крупных HIGH выше цены нет."
            )


        lines.append("")


        # ====================================================
        # LIQUIDITY BELOW
        # ====================================================

        lines.append(
            "🟢 ЛИКВИДНОСТЬ НИЖЕ ЦЕНЫ"
        )

        lines.append(
            "Потенциальная зона LONG sweep:"
        )

        lines.append("")


        if lows:

            for i, level in enumerate(
                lows,
                start=1
            ):

                level_price = level.get(
                    "price"
                )

                touches = level.get(
                    "touches",
                    0
                )

                strength = level.get(
                    "strength",
                    0
                )

                lines.append(

                    f"{i}. 🟢 "
                    f"{fmt_price(level_price)}\n"

                    f"   LONG sweep | "
                    f"touches: {touches} | "
                    f"strength: {strength}"
                )

        else:

            lines.append(
                "— Крупных LOW ниже цены нет."
            )


        lines.extend([

            "",

            "⚠️ Это уровни ликвидности, "
            "а не сигнал на вход.",

            "Вход только после:",

            "Sweep → 15M confirmation "
            "→ 5M V/L → Recovery "
            "→ FVG inversion.",

        ])


        await update.message.reply_text(

            "\n".join(
                lines
            )
        )


    except Exception as e:

        logger.exception(
            "Levels error"
        )

        await update.message.reply_text(
            f"❌ Ошибка уровней:\n{e}"
        )


# ============================================================
# CHART
# ============================================================

async def chart(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(
        update,
        context
    )

    symbol = "SOLUSDT"

    if context.args:

        requested = (
            context.args[0]
            .upper()
        )

        if not requested.endswith(
            "USDT"
        ):

            requested += "USDT"

        if requested in SYMBOLS:

            symbol = requested

    try:

        market = get_market_data(
            symbol
        )

        price = market[
            "price"
        ]

        await update.message.reply_text(

            f"📈 {symbol}\n\n"

            f"Источник: Binance Spot\n"

            f"Цена: "
            f"{fmt_price(price)}\n\n"

            "Таймфреймы:\n"

            "D1 → W1 → 1H → 15M → 5M"
        )

    except Exception as e:

        logger.exception(
            "Chart error"
        )

        await update.message.reply_text(
            f"❌ Ошибка графика:\n{e}"
        )


# ============================================================
# SUBSCRIBE
# ============================================================

async def subscribe(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(
        update,
        context
    )

    await update.message.reply_text(

        "🔔 Уведомления включены.\n\n"

        "TradeMind будет искать "
        "READY-сетапы автоматически."
    )


# ============================================================
# UNSUBSCRIBE
# ============================================================

async def unsubscribe(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    subscribers = (
        context.application
        .bot_data
        .setdefault(
            "subscribers",
            set()
        )
    )

    chat_id = (
        update.effective_chat.id
    )

    subscribers.discard(
        chat_id
    )

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

    track_chat(
        update,
        context
    )

    await update.message.reply_text(

        "📒 TRADEMIND — ЖУРНАЛ\n\n"

        "Режим: SIGNAL ONLY\n"

        f"Сделок сегодня: "
        f"{trades_today}/"
        f"{MAX_TRADES_PER_DAY}\n\n"

        "Автоматический учёт "
        "результатов BingX будет "
        "добавлен после подключения "
        "мониторинга закрытых позиций."
    )


# ============================================================
# BINGX STATUS
# ============================================================

async def bingx_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(
        update,
        context
    )

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

    track_chat(
        update,
        context
    )

    if not BINGX_AVAILABLE:

        await update.message.reply_text(
            "❌ BingX модуль недоступен."
        )

        return


    if BINGX_MODE != "ON":

        await update.message.reply_text(

            "📊 BingX mode: OFF\n\n"

            "Баланс сейчас "
            "автоматически не используется."
        )

        return


    try:

        if hasattr(
            bingx,
            "get_balance"
        ):

            result = (
                await asyncio.to_thread(
                    bingx.get_balance
                )
            )

            await update.message.reply_text(

                "💰 BingX баланс:\n\n"
                f"{result}"
            )

        else:

            await update.message.reply_text(

                "⚠️ В bingx.py нет "
                "функции get_balance()."
            )

    except Exception as e:

        logger.exception(
            "Balance error"
        )

        await update.message.reply_text(
            f"❌ Ошибка BingX:\n{e}"
        )


# ============================================================
# BINGX POSITION
# ============================================================

async def position(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    track_chat(
        update,
        context
    )

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

        if hasattr(
            bingx,
            "get_positions"
        ):

            result = (
                await asyncio.to_thread(
                    bingx.get_positions
                )
            )

            await update.message.reply_text(

                "📌 BingX позиции:\n\n"
                f"{result}"
            )

        else:

            await update.message.reply_text(

                "⚠️ В bingx.py нет "
                "функции get_positions()."
            )

    except Exception as e:

        logger.exception(
            "Position error"
        )

        await update.message.reply_text(
            f"❌ Ошибка BingX:\n{e}"
        )


# ============================================================
# AUTOMATIC MONITOR
# ============================================================

async def monitor(
    application
):

    global trades_today
    global daily_stop
    global active_trade


    logger.info(
        "TradeMind monitor started"
    )


    while True:

        try:

            reset_daily_state()


            # ------------------------------------------------
            # DAILY STOP
            # ------------------------------------------------

            if daily_stop:

                await asyncio.sleep(
                    MONITOR_INTERVAL
                )

                continue


            # ------------------------------------------------
            # MAX TRADES
            # ------------------------------------------------

            if (
                trades_today
                >=
                MAX_TRADES_PER_DAY
            ):

                daily_stop = True

                await asyncio.sleep(
                    MONITOR_INTERVAL
                )

                continue


            # ------------------------------------------------
            # SUBSCRIBERS
            # ------------------------------------------------

            subscribers = (
                application
                .bot_data
                .get(
                    "subscribers",
                    set()
                )
            )


            if not subscribers:

                await asyncio.sleep(
                    MONITOR_INTERVAL
                )

                continue


            # ------------------------------------------------
            # SEARCH
            # ------------------------------------------------

            setup = (
                find_first_ready()
            )


            if setup:

                symbol = setup.get(
                    "symbol"
                )

                direction = setup.get(
                    "direction"
                )

                key = (
                    f"{symbol}:"
                    f"{direction}"
                )

                current_time = now()

                previous_time = (
                    last_signal_time.get(
                        key
                    )
                )


                # ------------------------------------------------
                # COOLDOWN
                # ------------------------------------------------

                if previous_time:

                    elapsed = (
                        current_time
                        -
                        previous_time
                    ).total_seconds() / 60

                    if (
                        elapsed
                        <
                        SIGNAL_COOLDOWN_MINUTES
                    ):

                        await asyncio.sleep(
                            MONITOR_INTERVAL
                        )

                        continue


                last_signal_time[
                    key
                ] = current_time


                last_setups[
                    symbol
                ] = setup


                # ------------------------------------------------
                # MESSAGE
                # ------------------------------------------------

                message = (

                    "🚨 TRADEMIND 5.3 — "
                   