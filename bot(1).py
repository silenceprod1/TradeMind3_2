# ============================================================
# TRADEMIND 5.7 — MAIN
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

from market import (
    get_market_data,
    find_major_liquidity,
    detect_sweep,
)

from strategy import (
    analyze,
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

BINGX_MODE = os.getenv(
    "BINGX_MODE",
    "OFF"
).upper()

try:
    import bingx
except Exception:
    bingx = None


# Только нужные монеты.
COINS = {
    "SOL": "SOLUSDT",
    "ETH": "ETHUSDT",
    "BTC": "BTCUSDT",
    "BNB": "BNBUSDT",
    "XRP": "XRPUSDT",
    "DOGE": "DOGEUSDT",
    "ADA": "ADAUSDT",
    "AVAX": "AVAXUSDT",
    "LINK": "LINKUSDT",
}

SYMBOLS = list(COINS.values())

MAX_TRADES_PER_DAY = 2

CHECK_INTERVAL = 15

SIGNAL_COOLDOWN_MINUTES = 5


# ============================================================
# GLOBAL STATE
# ============================================================

trades_today = 0

daily_stop = False

active_trade = None

last_setups = {}

last_signal_time = {}

last_sweep = {}

last_confirmation = {}

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

        logger.info(
            "Daily state reset: %s",
            today,
        )


# ============================================================
# HELPERS
# ============================================================

def short_symbol(symbol):

    return str(symbol).replace(
        "USDT",
        "",
    )


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
        "15M_CONFIRMED": "15M CONFIRMED",
        "CONFIRMED": "CONFIRMED",
        "5M": "5M",
        "READY": "READY",
    }

    return mapping.get(
        stage,
        str(stage),
    )


def get_score(result):

    if not result:
        return 0

    return result.get(
        "score",
        result.get(
            "total_score",
            0,
        ),
    )


def get_direction(result):

    if not result:
        return None

    return result.get(
        "direction",
        result.get(
            "side",
        ),
    )


def get_entry(result):

    return result.get(
        "entry",
        result.get(
            "entry_price",
        ),
    )


def get_sl(result):

    return result.get(
        "sl",
        result.get(
            "stop_loss",
        ),
    )


def get_tp(result):

    return result.get(
        "tp",
        result.get(
            "take_profit",
        ),
    )


# ============================================================
# LIQUIDITY HELPERS
# ============================================================

def level_price(level):

    if not isinstance(level, dict):
        return None

    value = level.get(
        "price",
        level.get(
            "level",
        ),
    )

    try:
        return float(value)
    except Exception:
        return None


def nearest_liquidity(
    levels,
    price,
):

    above = []

    below = []

    try:
        price = float(price)
    except Exception:
        return None, None

    for level in levels or []:

        p = level_price(level)

        if p is None:
            continue

        if p > price:
            above.append(level)

        elif p < price:
            below.append(level)

    above.sort(
        key=lambda x: level_price(x)
    )

    below.sort(
        key=lambda x: level_price(x),
        reverse=True,
    )

    nearest_short = (
        above[0]
        if above
        else None
    )

    nearest_long = (
        below[0]
        if below
        else None
    )

    return (
        nearest_short,
        nearest_long,
    )


# ============================================================
# ANALYSIS
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
            [],
        ),
    )

    candles_w1 = market.get(
        "candles_w1",
        market.get(
            "w1",
            [],
        ),
    )

    candles_1h = market.get(
        "candles_1h",
        market.get(
            "1h",
            [],
        ),
    )

    candles_15m = market.get(
        "candles_15m",
        market.get(
            "15m",
            [],
        ),
    )

    candles_5m = market.get(
        "candles_5m",
        market.get(
            "5m",
            [],
        ),
    )

    # --------------------------------------------------------
    # Крупная ликвидность на 1H
    # --------------------------------------------------------

    try:

        major_levels = find_major_liquidity(
            candles_1h,
            price,
            max_levels=6,
        )

    except TypeError:

        major_levels = find_major_liquidity(
            candles_1h,
            price,
        )

    except Exception:

        major_levels = []

    # --------------------------------------------------------
    # ПЕРВЫЙ ПРОХОД
    #
    # Определяем направление через D1/W1/1H.
    # Sweep пока не передаём.
    # --------------------------------------------------------

    first_result = analyze(
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,
        current_price=price,
        price=price,
        major_levels=major_levels,
        candles_d1=candles_d1,
        candles_w1=candles_w1,
        sweep=None,
    )

    if first_result is None:
        first_result = {}

    direction = get_direction(
        first_result
    )

    # --------------------------------------------------------
    # SWEEP
    #
    # LONG:
    # цена должна снять крупную ликвидность снизу.
    #
    # SHORT:
    # цена должна снять крупную ликвидность сверху.
    # --------------------------------------------------------

    sweep = None

    if direction in (
        "LONG",
        "SHORT",
    ):

        try:

            sweep = detect_sweep(
                candles_1h,
                price,
                direction,
            )

        except Exception as e:

            logger.warning(
                "%s sweep detection error: %s",
                symbol,
                e,
            )

    # --------------------------------------------------------
    # ВТОРОЙ ПРОХОД
    #
    # Передаём найденный sweep обратно
    # в стратегию для полного анализа.
    # --------------------------------------------------------

    result = analyze(
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,
        current_price=price,
        price=price,
        major_levels=major_levels,
        candles_d1=candles_d1,
        candles_w1=candles_w1,
        sweep=sweep,
    )

    if result is None:
        result = {}

    # --------------------------------------------------------
    # Дополнительные данные
    # --------------------------------------------------------

    result["symbol"] = symbol

    result["price"] = price

    result["major_levels"] = (
        major_levels
    )

    result["sweep"] = sweep

    result["candles_5m"] = (
        candles_5m
    )

    result["candles_15m"] = (
        candles_15m
    )

    result["candles_1h"] = (
        candles_1h
    )

    result["candles_d1"] = (
        candles_d1
    )

    result["candles_w1"] = (
        candles_w1
    )

    result["strategy_version"] = (
        STRATEGY_VERSION
    )

    return result


# ============================================================
# FORMAT LEVEL
# ============================================================

def format_level_line(
    level,
    index,
    direction,
):

    p = level_price(level)

    if p is None:
        return None

    touches = level.get(
        "touches",
        "—",
    )

    strength = level.get(
        "strength",
        "—",
    )

    emoji = (
        "🔴"
        if direction == "SHORT"
        else "🟢"
    )

    return (
        f"{index}. {emoji} "
        f"{fmt_price(p)}\n"
        f"   {direction} | "
        f"touches: {touches} | "
        f"strength: {strength}"
    )


# ============================================================
# FORMAT SETUP
# ============================================================

def format_setup(
    symbol,
    setup,
):

    direction = get_direction(
        setup
    )

    score = get_score(
        setup
    )

    stage = setup.get(
        "stage",
        "—",
    )

    entry = get_entry(
        setup
    )

    sl = get_sl(
        setup
    )

    tp = get_tp(
        setup
    )

    rr = setup.get(
        "rr"
    )

    sweep = setup.get(
        "sweep"
    )

    confirmation = setup.get(
        "confirmation_15m",
        setup.get(
            "confirm_15m"
        ),
    )

    recovery = setup.get(
        "recovery",
        setup.get(
            "recovery_ratio"
        ),
    )

    fvg = setup.get(
        "fvg_inversion_5m",
        setup.get(
            "fvg_inversion"
        ),
    )

    is_ready = (
        stage == "READY"
        and direction in (
            "LONG",
            "SHORT",
        )
        and score >= MIN_SCORE_READY
        and entry is not None
        and sl is not None
        and tp is not None
    )

    if is_ready:

        text = (
            "🚨 TRADEMIND 5.7 — "
            "ГОТОВЫЙ СЕТАП\n\n"
            f"💠 {short_symbol(symbol)}\n"
            f"📐 {direction_emoji(direction)} "
            f"{direction}\n"
            f"⭐ Score: {score}/100\n"
            f"📍 Stage: READY\n\n"
        )

    else:

        text = (
            "🔎 TRADEMIND 5.7 — "
            "АНАЛИЗ\n\n"
            f"💠 {short_symbol(symbol)}\n"
            f"📐 {direction_emoji(direction)} "
            f"{direction or '—'}\n"
            f"⭐ Score: {score}/100\n"
            f"📍 Stage: "
            f"{stage_text(stage)}\n\n"
        )

    # --------------------------------------------------------
    # SWEEP
    # --------------------------------------------------------

    if sweep:

        sweep_p = level_price(
            sweep
        )

        if sweep_p is not None:

            text += (
                f"💧 Sweep: "
                f"{fmt_price(sweep_p)}\n"
            )

        else:

            text += (
                f"💧 Sweep: {sweep}\n"
            )

    # --------------------------------------------------------
    # 15M
    # --------------------------------------------------------

    if confirmation is not None:

        if confirmation is True:

            text += (
                "✅ 15M confirmation: True\n"
            )

        else:

            text += (
                "❌ 15M confirmation: False\n"
            )

    # --------------------------------------------------------
    # RECOVERY
    # --------------------------------------------------------

    if recovery is not None:

        text += (
            f"↩️ Recovery: "
            f"{recovery}\n"
        )

    # --------------------------------------------------------
    # FVG
    # --------------------------------------------------------

    if fvg is not None:

        if fvg is True:

            text += (
                "✅ 5M FVG inversion: True\n"
            )

        else:

            text += (
                "❌ 5M FVG inversion: False\n"
            )

    # --------------------------------------------------------
    # ENTRY
    # --------------------------------------------------------

    text += (
        "\n"
        f"💰 Entry: {fmt_price(entry)}\n"
        f"🛑 SL: {fmt_price(sl)}\n"
        f"🏁 TP: {fmt_price(tp)}\n"
        f"⚖️ RR: "
        f"{rr if rr is not None else '—'}\n\n"
    )

    # --------------------------------------------------------
    # FINAL
    # --------------------------------------------------------

    if is_ready:

        text += (
            "🟢 МОЖНО ВХОДИТЬ\n"
            "Все условия TradeMind выполнены."
        )

    else:

        text += (
            "⏳ ВХОДА НЕТ\n"
            "Ждём полного подтверждения "
            "TradeMind.\n\n"
            "D1 → 1H → крупная ликвидность "
            "→ Sweep\n"
            "→ 15M confirmation → "
            "5M trigger"
        )

    return text


# ============================================================
# TRACK CHAT
# ============================================================

def track_chat(update):

    if update.effective_chat:

        subscribers.add(
            update.effective_chat.id
        )


# ============================================================
# /START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    track_chat(update)

    text = (
        "🤖 TRADEMIND 5.7\n\n"
        "Автоматический мониторинг "
        "сетапов.\n\n"
        "Основная логика:\n"
        "D1 → 1H → крупная ликвидность "
        "→ Sweep → 15M → 5M → Entry\n\n"
        "Мониторинг:\n"
        "SOL • ETH • BTC • BNB • XRP\n"
        "DOGE • ADA • AVAX • LINK\n\n"
        "Команды:\n"
        "/sol — анализ SOL\n"
        "/eth — анализ ETH\n"
        "/market — весь рынок\n"
        "/levels — ликвидность всех монет\n"
        "/levels SOL — ликвидность SOL\n"
        "/search — поиск нового сетапа\n"
        "/status — статус бота\n"
        "/subscribe — уведомления\n"
        "/unsubscribe — отключить\n"
        "/journal — журнал\n"
        "/bingx_status — BingX\n"
        "/balance — баланс BingX\n"
        "/position — позиция BingX"
    )

    await update.message.reply_text(
        text
    )


# ============================================================
# /HELP
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    track_chat(update)

    await update.message.reply_text(
        "📖 TRADEMIND 5.7\n\n"
        "/sol — SOL\n"
        "/eth — ETH\n"
        "/market — рынок\n"
        "/levels — крупная ликвидность\n"
        "/levels SOL — ликвидность SOL\n"
        "/search — поиск сетапа\n"
        "/status — статус\n"
        "/subscribe — уведомления\n"
        "/unsubscribe — отключить\n"
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
    context: ContextTypes.DEFAULT_TYPE,
):

    track_chat(update)

    reset_daily_state()

    if active_trade is None:

        active_text = (
            "🟢 Активной сделки нет."
        )

    else:

        active_text = (
            f"🔴 Активная сделка:\n"
            f"{active_trade}"
        )

    text = (
        "📊 TRADEMIND 5.7 — СТАТУС\n\n"
        "Bot version: 5.7\n"
        f"Strategy version: "
        f"{STRATEGY_VERSION}\n"
        f"BingX mode: {BINGX_MODE}\n"
        f"Сделок сегодня: "
        f"{trades_today}/"
        f"{MAX_TRADES_PER_DAY}\n"
        f"Daily stop: "
        f"{'YES' if daily_stop else 'NO'}\n\n"
        f"{active_text}"
    )

    await update.message.reply_text(
        text
    )


# ============================================================
# ANALYZE ONE COIN
# ============================================================

async def analyze_coin_command(
    update,
    symbol,
):

    track_chat(update)

    try:

        setup = build_analysis(
            symbol
        )

        await update.message.reply_text(
            format_setup(
                symbol,
                setup,
            )
        )

    except Exception as e:

        logger.exception(
            "%s analysis error",
            symbol,
        )

        await update.message.reply_text(
            f"❌ Ошибка анализа "
            f"{short_symbol(symbol)}:\n"
            f"{e}"
        )


# ============================================================
# /SOL
# ============================================================

async def sol(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await analyze_coin_command(
        update,
        "SOLUSDT",
    )


# ============================================================
# /ETH
# ============================================================

async def eth(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await analyze_coin_command(
        update,
        "ETHUSDT",
    )


# ============================================================
# /MARKET
# ============================================================

async def market(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    track_chat(update)

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

            direction = get_direction(
                result
            )

            stage = result.get(
                "stage"
            )

            score = get_score(
                result
            )

            major_levels = result.get(
                "major_levels",
                [],
            )

            nearest_short, nearest_long = (
                nearest_liquidity(
                    major_levels,
                    price,
                )
            )

            nearest = (
                nearest_long
                if direction == "LONG"
                else nearest_short
            )

            distance_text = ""

            if nearest is not None:

                p = level_price(
                    nearest
                )

                if p is not None:

                    distance = abs(
                        p - float(price)
                    ) / float(price) * 100

                    distance_text = (
                        f" | Liq "
                        f"{fmt_price(p)} "
                        f"({distance:.2f}%)"
                    )

            lines.append(
                f"{short_symbol(symbol)}: "
                f"{fmt_price(price)} | "
                f"{direction_emoji(direction)} "
                f"{direction or '—'} | "
                f"{stage_text(stage)} | "
                f"{score}/100"
                f"{distance_text}"
            )

        except Exception as e:

            logger.exception(
                "Market error %s",
                symbol,
            )

            lines.append(
                f"{short_symbol(symbol)}: "
                f"❌ ERROR"
            )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# FIND READY
# ============================================================

def find_first_ready():

    best_symbol = None

    best_result = None

    best_score = -1

    for symbol in SYMBOLS:

        try:

            result = build_analysis(
                symbol
            )

            score = get_score(
                result
            )

            direction = get_direction(
                result
            )

            stage = result.get(
                "stage"
            )

            logger.info(
                "%s | price=%s | "
                "direction=%s | "
                "stage=%s | score=%s",
                symbol,
                result.get(
                    "price"
                ),
                direction,
                stage,
                score,
            )

            if (
                stage == "READY"
                and direction in (
                    "LONG",
                    "SHORT",
                )
                and score >= MIN_SCORE_READY
            ):

                if score > best_score:

                    best_score = score

                    best_symbol = symbol

                    best_result = result

        except Exception as e:

            logger.exception(
                "Ready search error "
                "for %s: %s",
                symbol,
                e,
            )

    return (
        best_symbol,
        best_result,
    )


# ============================================================
# /SEARCH
# ============================================================

async def search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    track_chat(update)

    await update.message.reply_text(
        "🔎 TRADEMIND — "
        "ПОИСК СЕТАПА\n\n"
        "Проверяю SOL • ETH • BTC • "
        "BNB • XRP • DOGE • ADA • "
        "AVAX • LINK..."
    )

    try:

        symbol, setup = (
            find_first_ready()
        )

        if symbol is None:

            await update.message.reply_text(
                "❌ Готового сетапа сейчас нет.\n\n"
                "Нет полного подтверждения → "
                "нет входа."
            )

            return

        await update.message.reply_text(
            format_setup(
                symbol,
                setup,
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
# /LEVELS FORMAT
# ============================================================

def format_levels_for_symbol(
    symbol,
    market_data,
):

    price = market_data["price"]

    candles_1h = market_data.get(
        "candles_1h",
        market_data.get(
            "1h",
            [],
        ),
    )

    try:

        levels_data = (
            find_major_liquidity(
                candles_1h,
                price,
                max_levels=6,
            )
        )

    except TypeError:

        levels_data = (
            find_major_liquidity(
                candles_1h,
                price,
            )
        )

    highs = []

    lows = []

    for level in levels_data or []:

        p = level_price(
            level
        )

        if p is None:
            continue

        level_type = str(
            level.get(
                "type",
                "",
            )
        ).upper()

        # ----------------------------------------------------
        # HIGH ABOVE CURRENT PRICE
        # SHORT SWEEP
        # ----------------------------------------------------

        if (
            level_type == "HIGH"
            and p > float(price)
        ):

            highs.append(
                level
            )

        # ----------------------------------------------------
        # LOW BELOW CURRENT PRICE
        # LONG SWEEP
        # ----------------------------------------------------

        elif (
            level_type == "LOW"
            and p < float(price)
        ):

            lows.append(
                level
            )

    highs.sort(
        key=lambda x: level_price(x)
    )

    lows.sort(
        key=lambda x: level_price(x),
        reverse=True,
    )

    lines = []

    lines.append(
        f"💠 {short_symbol(symbol)}"
    )

    lines.append(
        f"💰 Цена: {fmt_price(price)}"
    )

    lines.append("")

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    lines.append(
        "🔴 ВЫШЕ ЦЕНЫ — SHORT SWEEP"
    )

    if highs:

        for i, level in enumerate(
            highs,
            start=1,
        ):

            line = format_level_line(
                level,
                i,
                "SHORT",
            )

            if line:
                lines.append(
                    line
                )

    else:

        lines.append(
            "Нет крупных уровней."
        )

    lines.append("")

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    lines.append(
        "🟢 НИЖЕ ЦЕНЫ — LONG SWEEP"
    )

    if lows:

        for i, level in enumerate(
            lows,
            start=1,
        ):

            line = format_level_line(
                level,
                i,
                "LONG",
            )

            if line:
                lines.append(
                    line
                )

    else:

        lines.append(
            "Нет крупных уровней."
        )

    return "\n".join(lines)


# ============================================================
# /LEVELS
# ============================================================

async def levels(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    track_chat(update)

    # ========================================================
    # SPECIFIC COIN
    # ========================================================

    if context.args:

        requested = (
            context.args[0]
            .upper()
            .strip()
        )

        if not requested.endswith(
            "USDT"
        ):

            requested += "USDT"

        if requested not in SYMBOLS:

            await update.message.reply_text(
                "❌ Неизвестная монета.\n\n"
                "Доступны:\n"
                + ", ".join(
                    short_symbol(s)
                    for s in SYMBOLS
                )
            )

            return

        try:

            market_data = (
                get_market_data(
                    requested
                )
            )

            text = (
                "💧 TRADEMIND — "
                "КРУПНАЯ ЛИКВИДНОСТЬ\n\n"
                + format_levels_for_symbol(
                    requested,
                    market_data,
                )
                + "\n\n"
                "⚠️ Уровни — НЕ сигнал входа.\n"
                "Ждём:\n"
                "Sweep → 15M confirmation "
                "→ 5M trigger."
            )

            await update.message.reply_text(
                text
            )

        except Exception as e:

            logger.exception(
                "Levels error %s",
                requested,
            )

            await update.message.reply_text(
                f"❌ Ошибка уровней "
                f"{requested}:\n{e}"
            )

        return

    # ========================================================
    # ALL COINS
    # ========================================================

    await update.message.reply_text(
        "💧 TRADEMIND — "
        "КРУПНАЯ ЛИКВИДНОСТЬ\n\n"
        "Сканирую рынок..."
    )

    blocks = []

    for symbol in SYMBOLS:

        try:

            market_data = (
                get_market_data(
                    symbol
                )
            )

            block = (
                format_levels_for_symbol(
                    symbol,
                    market_data,
                )
            )

            blocks.append(
                block
            )

        except Exception as e:

            logger.exception(
                "Levels error %s: %s",
                symbol,
                e,
            )

            blocks.append(
                f"💠 {short_symbol(symbol)}\n"
                f"❌ Ошибка получения уровней"
            )

    text = (
        "💧 TRADEMIND — "
        "КРУПНАЯ ЛИКВИДНОСТЬ\n\n"
        + "\n\n".join(blocks)
        + "\n\n"
        "⚠️ Уровни — НЕ сигнал входа.\n"
        "Sweep → 15M confirmation "
        "→ 5M trigger."
    )

    await update.message.reply_text(
        text
    )


# ============================================================
# /SUBSCRIBE
# ============================================================

async def subscribe(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    track_chat(update)

    chat_id = (
        update.effective_chat.id
    )

    subscribers.add(
        chat_id
    )

    await update.message.reply_text(
        "🔔 Уведомления TradeMind "
        "включены."
    )


# ============================================================
# /UNSUBSCRIBE
# ============================================================

async def unsubscribe(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = (
        update.effective_chat.id
    )

    subscribers.discard(
        chat_id
    )

    await update.message.reply_text(
        "🔕 Уведомления TradeMind "
        "отключены."
    )


# ============================================================
# /JOURNAL
# ============================================================

async def journal(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    track_chat(update)

    reset_daily_state()

    await update.message.reply_text(
        "📒 TRADEMIND — ЖУРНАЛ\n\n"
        f"Сделок сегодня: "
        f"{trades_today}/"
        f"{MAX_TRADES_PER_DAY}\n"
        f"Daily stop: "
        f"{'YES' if daily_stop else 'NO'}\n\n"
        "Исполнение BingX: "
        f"{BINGX_MODE}\n\n"
        "Сделки будут сохраняться "
        "после подключения "
        "исполнения."
    )


# ============================================================
# /BINGX_STATUS
# ============================================================

async def bingx_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
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
    context: ContextTypes.DEFAULT_TYPE,
):

    track_chat(update)

    if bingx is None:

        await update.message.reply_text(
            "❌ BingX module недоступен."
        )

        return

    try:

        if hasattr(
            bingx,
            "get_balance",
        ):

            result = (
                bingx.get_balance()
            )

            await update.message.reply_text(
                "💰 BingX balance:\n"
                f"{result}"
            )

        else:

            await update.message.reply_text(
                "⚠️ get_balance не найден "
                "в модуле BingX."
            )

    except Exception as e:

        logger.exception(
            "Balance error"
        )

        await update.message.reply_text(
            f"❌ Ошибка BingX balance:\n"
            f"{e}"
        )


# ============================================================
# /POSITION
# ============================================================

async def position(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    track_chat(update)

    if bingx is None:

        await update.message.reply_text(
            "❌ BingX module недоступен."
        )

        return

    try:

        if hasattr(
            bingx,
            "get_position",
        ):

            result = (
                bingx.get_position()
            )

            await update.message.reply_text(
                "📌 BingX position:\n"
                f"{result}"
            )

        else:

            await update.message.reply_text(
                "⚠️ get_position не найден "
                "в модуле BingX."
            )

    except Exception as e:

        logger.exception(
            "Position error"
        )

        await update.message.reply_text(
            f"❌ Ошибка BingX position:\n"
            f"{e}"
        )


# ============================================================
# BROADCAST
# ============================================================

async def broadcast(
    application,
    text,
):

    for chat_id in list(
        subscribers
    ):

        try:

            await application.bot.send_message(
                chat_id=chat_id,
                text=text,
            )

        except Exception as e:

            logger.error(
                "Broadcast error %s: %s",
                chat_id,
                e,
            )


# ============================================================
# SWEEP ALERT
# ============================================================

async def process_sweep_alert(
    application,
    symbol,
    result,
):

    sweep = result.get(
        "sweep"
    )

    if not sweep:
        return

    direction = get_direction(
        result
    )

    if direction not in (
        "LONG",
        "SHORT",
    ):
        return

    sweep_price = level_price(
        sweep
    )

    sweep_time = sweep.get(
        "open_time",
        sweep.get(
            "time",
            "",
        ),
    )

    key = (
        f"{symbol}_"
        f"{direction}_"
        f"{sweep_price}_"
        f"{sweep_time}"
    )

    if last_sweep.get(
        symbol
    ) == key:

        return

    last_sweep[
        symbol
    ] = key

    text = (
        "🔎 TRADEMIND 5.7 — SWEEP\n\n"
        f"💠 {short_symbol(symbol)}\n"
        f"📐 {direction_emoji(direction)} "
        f"{direction}\n"
    )

    if sweep_price is not None:

        text += (
            f"💧 Крупная ликвидность снята: "
            f"{fmt_price(sweep_price)}\n"
        )

    text += (
        "\n"
        "⏳ Ждём 15M confirmation.\n"
        "❌ ВХОД ПОКА ЗАПРЕЩЁН."
    )

    await broadcast(
        application,
        text,
    )


# ============================================================
# 15M CONFIRMATION ALERT
# ============================================================

async def process_confirmation_alert(
    application,
    symbol,
    result,
):

    stage = result.get(
        "stage"
    )

    if stage not in (
        "15M_CONFIRMED",
        "CONFIRMED",
        "5M",
        "READY",
    ):

        return

    direction = get_direction(
        result
    )

    if direction not in (
        "LONG",
        "SHORT",
    ):
        return

    confirmation_time = result.get(
        "confirmation_15m_time",
        result.get(
            "confirmation_time",
            "",
        ),
    )

    key = (
        f"{symbol}_"
        f"{direction}_"
        f"{confirmation_time}"
    )

    if last_confirmation.get(
        symbol
    ) == key:

        return

    last_confirmation[
        symbol
    ] = key

    text = (
        "✅ TRADEMIND 5.7 — 15M CONFIRMATION\n\n"
        f"💠 {short_symbol(symbol)}\n"
        f"📐 {direction_emoji(direction)} "
        f"{direction}\n\n"
        "💧 Sweep найден.\n"
        "✅ 15M confirmation получен.\n"
        "⏳ Переходим к 5M trigger.\n"
        "❌ Вход пока запрещён."
    )

    await broadcast(
        application,
        text,
    )


# ============================================================
# READY ALERT
# ============================================================

async def process_ready_alert(
    application,
    symbol,
    setup,
):

    direction = get_direction(
        setup
    )

    score = get_score(
        setup
    )

    stage = setup.get(
        "stage"
    )

    entry = get_entry(
        setup
    )

    sl = get_sl(
        setup
    )

    tp = get_tp(
        setup
    )

    if not (
        stage == "READY"
        and direction in (
            "LONG",
            "SHORT",
        )
        and score >= MIN_SCORE_READY
        and entry is not None
        and sl is not None
        and tp is not None
    ):

        return False

    cooldown_key = (
        symbol,
        direction,
    )

    current_time = now()

    previous_signal = (
        last_signal_time.get(
            cooldown_key
        )
    )

    if previous_signal:

        elapsed = (
            current_time
            - previous_signal
        ).total_seconds() / 60

        if elapsed < (
            SIGNAL_COOLDOWN_MINUTES
        ):

            return False

    last_signal_time[
        cooldown_key
    ] = current_time

    last_setups[
        symbol
    ] = setup

    message = (
        "🚨 TRADEMIND 5.7 — "
        "ГОТОВЫЙ СЕТАП\n\n"

        f"💠 {short_symbol(symbol)}\n"

        f"📐 "
        f"{direction_emoji(direction)} "
        f"{direction}\n"

        f"⭐ Score: "
        f"{score}/100\n\n"

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

    await broadcast(
        application,
        message,
    )

    return True


# ============================================================
# MONITOR
# ============================================================

async def monitor(
    application,
):

    global trades_today
    global daily_stop
    global active_trade

    logger.info(
        "TradeMind 5.7 monitor started."
    )

    while True:

        try:

            reset_daily_state()

            # ------------------------------------------------
            # Дневной лимит
            # ------------------------------------------------

            if daily_stop:

                await asyncio.sleep(
                    CHECK_INTERVAL
                )

                continue

            # ------------------------------------------------
            # Нет подписчиков
            # ------------------------------------------------

            if not subscribers:

                await asyncio.sleep(
                    CHECK_INTERVAL
                )

                continue

            # ------------------------------------------------
            # Сканируем ВСЕ монеты
            # ------------------------------------------------

            for symbol in SYMBOLS:

                try:

                    result = build_analysis(
                        symbol
                    )

                    # ----------------------------------------
                    # SWEEP
                    # ----------------------------------------

                    await process_sweep_alert(
                        application,
                        symbol,
                        result,
                    )

                    # ----------------------------------------
                    # 15M
                    # ----------------------------------------

                    await process_confirmation_alert(
                        application,
                        symbol,
                        result,
                    )

                    # ----------------------------------------
                    # READY
                    # ----------------------------------------

                    if not daily_stop:

                        await process_ready_alert(
                            application,
                            symbol,
                            result,
                        )

                except Exception as e:

                    logger.exception(
                        "Monitor analysis "
                        "error %s: %s",
                        symbol,
                        e,
                    )

            # ------------------------------------------------
            # Sleep
            # ------------------------------------------------

            await asyncio.sleep(
                CHECK_INTERVAL
            )

        except Exception as e:

            logger.exception(
                "Monitor error: %s",
                e,
            )

            await asyncio.sleep(
                CHECK_INTERVAL
            )


# ============================================================
# POST INIT
# ============================================================

async def post_init(
    application,
):

    commands = [

        BotCommand(
            "start",
            "Запустить TradeMind",
        ),

        BotCommand(
            "help",
            "Помощь",
        ),

        BotCommand(
            "sol",
            "Анализ SOL",
        ),

        BotCommand(
            "eth",
            "Анализ ETH",
        ),

        BotCommand(
            "market",
            "Весь рынок",
        ),

        BotCommand(
            "levels",
            "Крупная ликвидность",
        ),

        BotCommand(
            "search",
            "Поиск сетапа",
        ),

        BotCommand(
            "status",
            "Статус",
        ),

        BotCommand(
            "subscribe",
            "Включить уведомления",
        ),

        BotCommand(
            "unsubscribe",
            "Отключить уведомления",
        ),

        BotCommand(
            "journal",
            "Журнал",
        ),

        BotCommand(
            "bingx_status",
            "Статус BingX",
        ),

        BotCommand(
            "balance",
            "Баланс BingX",
        ),

        BotCommand(
            "position",
            "Позиция BingX",
        ),
    ]

    await application.bot.set_my_commands(
        commands
    )

    application.create_task(
        monitor(
            application
        )
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:

        raise RuntimeError(
            "BOT_TOKEN environment variable "
            "is not set."
        )

    application = (
        Application.builder()
        .token(
            BOT_TOKEN
        )
        .post_init(
            post_init
        )
        .build()
    )

    # --------------------------------------------------------
    # COMMANDS
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            status,
        )
    )

    application.add_handler(
        CommandHandler(
            "sol",
            sol,
        )
    )

    application.add_handler(
        CommandHandler(
            "eth",
            eth,
        )
    )

    application.add_handler(
        CommandHandler(
            "market",
            market,
        )
    )

    application.add_handler(
        CommandHandler(
            "search",
            search,
        )
    )

    application.add_handler(
        CommandHandler(
            "levels",
            levels,
        )
    )

    application.add_handler(
        CommandHandler(
            "subscribe",
            subscribe,
        )
    )

    application.add_handler(
        CommandHandler(
            "unsubscribe",
            unsubscribe,
        )
    )

    application.add_handler(
        CommandHandler(
            "journal",
            journal,
        )
    )

    application.add_handler(
        CommandHandler(
            "bingx_status",
            bingx_status,
        )
    )

    application.add_handler(
        CommandHandler(
            "balance",
            balance,
        )
    )

    application.add_handler(
        CommandHandler(
            "position",
            position,
        )
    )

    logger.info(
        "TradeMind 5.7 starting..."
    )

    application.run_polling()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()