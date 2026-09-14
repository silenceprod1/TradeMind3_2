# ============================================================
# TRADEMIND 5.8 — FAST MAIN
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

BINGX_MODE = os.getenv("BINGX_MODE", "OFF").upper()

try:
    import bingx
except Exception:
    bingx = None


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

# Фоновое обновление.
CHECK_INTERVAL = 15

# Сколько минут нельзя повторять один и тот же READY.
SIGNAL_COOLDOWN_MINUTES = 5

# Максимальный возраст кэша для команд.
CACHE_MAX_AGE = 45


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

# Самое главное изменение 5.8:
# здесь постоянно лежит последний анализ каждой монеты.
market_cache = {}

# Lock защищает кэш.
cache_lock = asyncio.Lock()


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
    return str(symbol).replace("USDT", "")


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
        str(stage or "—"),
    )


def get_score(result):

    if not result:
        return 0

    return result.get(
        "score",
        result.get("total_score", 0),
    )


def get_direction(result):

    if not result:
        return None

    return result.get(
        "direction",
        result.get("side"),
    )


def get_entry(result):
    return result.get(
        "entry",
        result.get("entry_price"),
    )


def get_sl(result):
    return result.get(
        "sl",
        result.get("stop_loss"),
    )


def get_tp(result):
    return result.get(
        "tp",
        result.get("take_profit"),
    )


# ============================================================
# LIQUIDITY
# ============================================================

def level_price(level):

    if not isinstance(level, dict):
        return None

    value = level.get(
        "price",
        level.get("level"),
    )

    try:
        return float(value)
    except Exception:
        return None


def nearest_liquidity(levels, price):

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

    above.sort(key=lambda x: level_price(x))
    below.sort(
        key=lambda x: level_price(x),
        reverse=True,
    )

    return (
        above[0] if above else None,
        below[0] if below else None,
    )


# ============================================================
# BUILD ANALYSIS
# ============================================================

def build_analysis(symbol):

    market = get_market_data(symbol)

    price = market["price"]

    candles_d1 = market.get(
        "candles_d1",
        market.get("d1", []),
    )

    candles_w1 = market.get(
        "candles_w1",
        market.get("w1", []),
    )

    candles_1h = market.get(
        "candles_1h",
        market.get("1h", []),
    )

    candles_15m = market.get(
        "candles_15m",
        market.get("15m", []),
    )

    candles_5m = market.get(
        "candles_5m",
        market.get("5m", []),
    )

    # --------------------------------------------------------
    # MAJOR LIQUIDITY
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

    except Exception as e:

        logger.warning(
            "%s liquidity error: %s",
            symbol,
            e,
        )

        major_levels = []

    # --------------------------------------------------------
    # FIRST PASS
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
    ) or {}

    direction = get_direction(first_result)

    # --------------------------------------------------------
    # SWEEP
    # --------------------------------------------------------

    sweep = None

    if direction in ("LONG", "SHORT"):

        try:

            sweep = detect_sweep(
                candles_1h,
                price,
                direction,
            )

        except Exception as e:

            logger.warning(
                "%s sweep error: %s",
                symbol,
                e,
            )

    # --------------------------------------------------------
    # SECOND PASS
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
    ) or {}

    # --------------------------------------------------------
    # EXTRA DATA
    # --------------------------------------------------------

    result["symbol"] = symbol
    result["price"] = price
    result["major_levels"] = major_levels
    result["sweep"] = sweep

    result["candles_5m"] = candles_5m
    result["candles_15m"] = candles_15m
    result["candles_1h"] = candles_1h
    result["candles_d1"] = candles_d1
    result["candles_w1"] = candles_w1

    result["strategy_version"] = STRATEGY_VERSION
    result["_updated_at"] = now()

    return result


# ============================================================
# ASYNC ANALYSIS WRAPPER
# ============================================================

async def async_build_analysis(symbol):

    return await asyncio.to_thread(
        build_analysis,
        symbol,
    )


# ============================================================
# CACHE
# ============================================================

async def cache_result(symbol, result):

    async with cache_lock:

        market_cache[symbol] = result


async def get_cached(symbol):

    async with cache_lock:

        result = market_cache.get(symbol)

    if not result:
        return None

    updated = result.get("_updated_at")

    if updated:

        age = (
            now() - updated
        ).total_seconds()

        if age > CACHE_MAX_AGE:
            return None

    return result


async def get_or_build(symbol):

    cached = await get_cached(symbol)

    if cached is not None:
        return cached

    try:

        result = await async_build_analysis(
            symbol
        )

        await cache_result(
            symbol,
            result,
        )

        return result

    except Exception as e:

        logger.exception(
            "Analysis error %s",
            symbol,
        )

        raise e


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
        f"{index}. {emoji} {fmt_price(p)}\n"
        f"   {direction} | "
        f"touches: {touches} | "
        f"strength: {strength}"
    )


# ============================================================
# FORMAT SETUP
# ============================================================

def format_setup(symbol, setup):

    direction = get_direction(setup)
    score = get_score(setup)

    stage = setup.get(
        "stage",
        "—",
    )

    entry = get_entry(setup)
    sl = get_sl(setup)
    tp = get_tp(setup)

    rr = setup.get("rr")

    sweep = setup.get("sweep")

    confirmation = setup.get(
        "confirmation_15m",
        setup.get("confirm_15m"),
    )

    recovery = setup.get(
        "recovery",
        setup.get("recovery_ratio"),
    )

    fvg = setup.get(
        "fvg_inversion_5m",
        setup.get("fvg_inversion"),
    )

    is_ready = (
        stage == "READY"
        and direction in ("LONG", "SHORT")
        and score >= MIN_SCORE_READY
        and entry is not None
        and sl is not None
        and tp is not None
    )

    if is_ready:

        text = (
            "🚨 TRADEMIND 5.8 — ГОТОВЫЙ СЕТАП\n\n"
            f"💠 {short_symbol(symbol)}\n"
            f"📐 {direction_emoji(direction)} {direction}\n"
            f"⭐ Score: {score}/100\n"
            f"📍 Stage: READY\n\n"
        )

    else:

        text = (
            "🔎 TRADEMIND 5.8 — АНАЛИЗ\n\n"
            f"💠 {short_symbol(symbol)}\n"
            f"📐 {direction_emoji(direction)} "
            f"{direction or '—'}\n"
            f"⭐ Score: {score}/100\n"
            f"📍 Stage: {stage_text(stage)}\n\n"
        )

    if sweep:

        sweep_p = level_price(sweep)

        if sweep_p is not None:
            text += (
                f"💧 Sweep: "
                f"{fmt_price(sweep_p)}\n"
            )

    if confirmation is not None:

        text += (
            "✅ 15M confirmation: True\n"
            if confirmation is True
            else "❌ 15M confirmation: False\n"
        )

    if recovery is not None:

        text += (
            f"↩️ Recovery: {recovery}\n"
        )

    if fvg is not None:

        text += (
            "✅ 5M FVG inversion: True\n"
            if fvg is True
            else "❌ 5M FVG inversion: False\n"
        )

    text += (
        "\n"
        f"💰 Entry: {fmt_price(entry)}\n"
        f"🛑 SL: {fmt_price(sl)}\n"
        f"🏁 TP: {fmt_price(tp)}\n"
        f"⚖️ RR: {rr if rr is not None else '—'}\n\n"
    )

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

def track_chat(update):

    if update.effective_chat:
        subscribers.add(
            update.effective_chat.id
        )


# ============================================================
# /START
# ============================================================

async def start(update, context):

    track_chat(update)

    await update.message.reply_text(
        "🤖 TRADEMIND 5.8\n\n"
        "Быстрый фоновый мониторинг сетапов.\n\n"
        "D1 → 1H → крупная ликвидность → Sweep\n"
        "→ 15M → 5M → Entry\n\n"
        "Мониторинг:\n"
        "SOL • ETH • BTC • BNB • XRP\n"
        "DOGE • ADA • AVAX • LINK\n\n"
        "/sol — анализ SOL\n"
        "/eth — анализ ETH\n"
        "/market — рынок\n"
        "/levels — ликвидность\n"
        "/search — поиск\n"
        "/status — статус\n"
        "/subscribe — уведомления\n"
        "/unsubscribe — отключить\n"
        "/journal — журнал"
    )


# ============================================================
# /HELP
# ============================================================

async def help_command(update, context):

    track_chat(update)

    await update.message.reply_text(
        "📖 TRADEMIND 5.8\n\n"
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

async def status(update, context):

    track_chat(update)
    reset_daily_state()

    cache_count = len(market_cache)

    if active_trade is None:

        active_text = "🟢 Активной сделки нет."

    else:

        active_text = (
            f"🔴 Активная сделка:\n"
            f"{active_trade}"
        )

    await update.message.reply_text(
        "📊 TRADEMIND 5.8 — СТАТУС\n\n"
        f"Strategy: {STRATEGY_VERSION}\n"
        f"BingX mode: {BINGX_MODE}\n"
        f"Сигналов сегодня: "
        f"{trades_today}/{MAX_TRADES_PER_DAY}\n"
        f"Daily stop: "
        f"{'YES' if daily_stop else 'NO'}\n"
        f"Кэш: {cache_count}/{len(SYMBOLS)} монет\n\n"
        f"{active_text}"
    )


# ============================================================
# /SOL / ETH
# ============================================================

async def analyze_coin_command(
    update,
    symbol,
):

    track_chat(update)

    try:

        result = await get_or_build(symbol)

        await update.message.reply_text(
            format_setup(
                symbol,
                result,
            )
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Ошибка анализа "
            f"{short_symbol(symbol)}:\n{e}"
        )


async def sol(update, context):

    await analyze_coin_command(
        update,
        "SOLUSDT",
    )


async def eth(update, context):

    await analyze_coin_command(
        update,
        "ETHUSDT",
    )


# ============================================================
# /MARKET — FAST
# ============================================================

async def market(update, context):

    track_chat(update)

    # НИКАКОГО НОВОГО СКАНА 9 МОНЕТ ЗДЕСЬ НЕТ.
    # Берём готовый фоновой кэш.

    results = []

    for symbol in SYMBOLS:

        result = await get_cached(symbol)

        if result is not None:

            results.append(
                (symbol, result)
            )

    # Если бот только что запустился,
    # кэш ещё пустой.
    if not results:

        await update.message.reply_text(
            "⏳ TradeMind ещё собирает первый "
            "снимок рынка..."
        )

        return

    lines = [
        "📊 TRADEMIND 5.8 — РЫНОК",
        "",
    ]

    for symbol, result in results:

        price = result.get("price")

        direction = get_direction(
            result
        )

        stage = result.get("stage")

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

            p = level_price(nearest)

            if p is not None:

                try:

                    distance = (
                        abs(
                            p - float(price)
                        )
                        / float(price)
                        * 100
                    )

                    distance_text = (
                        f" | Liq "
                        f"{fmt_price(p)} "
                        f"({distance:.2f}%)"
                    )

                except Exception:
                    pass

        lines.append(
            f"{short_symbol(symbol)}: "
            f"{fmt_price(price)} | "
            f"{direction_emoji(direction)} "
            f"{direction or '—'} | "
            f"{stage_text(stage)} | "
            f"{score}/100"
            f"{distance_text}"
        )

    if len(results) < len(SYMBOLS):

        lines.append("")
        lines.append(
            f"⏳ Кэш обновляется: "
            f"{len(results)}/{len(SYMBOLS)}"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )


# ============================================================
# READY SEARCH — CACHE
# ============================================================

async def find_first_ready_cached():

    best_symbol = None
    best_result = None
    best_score = -1

    for symbol in SYMBOLS:

        result = await get_cached(
            symbol
        )

        if result is None:
            continue

        score = get_score(result)
        direction = get_direction(result)
        stage = result.get("stage")

        if (
            stage == "READY"
            and direction in ("LONG", "SHORT")
            and score >= MIN_SCORE_READY
        ):

            if score > best_score:

                best_score = score
                best_symbol = symbol
                best_result = result

    return (
        best_symbol,
        best_result,
    )


# ============================================================
# /SEARCH
# ============================================================

async def search(update, context):

    track_chat(update)

    symbol, setup = (
        await find_first_ready_cached()
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


# ============================================================
# LEVELS FROM CACHE
# ============================================================

def format_levels_from_result(
    symbol,
    result,
):

    price = result.get("price")

    levels_data = result.get(
        "major_levels",
        [],
    )

    highs = []
    lows = []

    for level in levels_data:

        p = level_price(level)

        if p is None:
            continue

        level_type = str(
            level.get("type", "")
        ).upper()

        if (
            level_type == "HIGH"
            and p > float(price)
        ):

            highs.append(level)

        elif (
            level_type == "LOW"
            and p < float(price)
        ):

            lows.append(level)

    highs.sort(
        key=lambda x: level_price(x)
    )

    lows.sort(
        key=lambda x: level_price(x),
        reverse=True,
    )

    lines = [
        f"💠 {short_symbol(symbol)}",
        f"💰 Цена: {fmt_price(price)}",
        "",
        "🔴 ВЫШЕ ЦЕНЫ — SHORT SWEEP",
    ]

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
                lines.append(line)

    else:

        lines.append(
            "Нет крупных уровней."
        )

    lines += [
        "",
        "🟢 НИЖЕ ЦЕНЫ — LONG SWEEP",
    ]

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
                lines.append(line)

    else:

        lines.append(
            "Нет крупных уровней."
        )

    return "\n".join(lines)


# ============================================================
# /LEVELS
# ============================================================

async def levels(update, context):

    track_chat(update)

    if context.args:

        requested = (
            context.args[0]
            .upper()
            .strip()
        )

        if not requested.endswith("USDT"):
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

        result = await get_or_build(
            requested
        )

        text = (
            "💧 TRADEMIND — "
            "КРУПНАЯ ЛИКВИДНОСТЬ\n\n"
            + format_levels_from_result(
                requested,
                result,
            )
            + "\n\n"
            "⚠️ Уровни — НЕ сигнал входа.\n"
            "Sweep → 15M confirmation → "
            "5M trigger."
        )

        await update.message.reply_text(
            text
        )

        return

    # Все монеты — только из кэша.
    blocks = []

    for symbol in SYMBOLS:

        result = await get_cached(symbol)

        if result is not None:

            blocks.append(
                format_levels_from_result(
                    symbol,
                    result,
                )
            )

    if not blocks:

        await update.message.reply_text(
            "⏳ Кэш рынка ещё заполняется."
        )

        return

    text = (
        "💧 TRADEMIND — "
        "КРУПНАЯ ЛИКВИДНОСТЬ\n\n"
        + "\n\n".join(blocks)
        + "\n\n"
        "⚠️ Уровни — НЕ сигнал входа.\n"
        "Sweep → 15M confirmation → "
        "5M trigger."
    )

    await update.message.reply_text(
        text
    )


# ============================================================
# SUBSCRIBE
# ============================================================

async def subscribe(update, context):

    track_chat(update)

    subscribers.add(
        update.effective_chat.id
    )

    await update.message.reply_text(
        "🔔 Уведомления TradeMind включены."
    )


async def unsubscribe(update, context):

    chat_id = update.effective_chat.id

    subscribers.discard(chat_id)

    await update.message.reply_text(
        "🔕 Уведомления TradeMind отключены."
    )


# ============================================================
# JOURNAL
# ============================================================

async def journal(update, context):

    track_chat(update)
    reset_daily_state()

    await update.message.reply_text(
        "📒 TRADEMIND — ЖУРНАЛ\n\n"
        f"Сигналов сегодня: "
        f"{trades_today}/{MAX_TRADES_PER_DAY}\n"
        f"Daily stop: "
        f"{'YES' if daily_stop else 'NO'}\n\n"
        f"BingX: {BINGX_MODE}\n\n"
        "TradeMind сохраняет последние "
        "сетапы в памяти."
    )


# ============================================================
# BINGX
# ============================================================

async def bingx_status(update, context):

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


async def balance(update, context):

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
                "⚠️ get_balance не найден."
            )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Ошибка BingX:\n{e}"
        )


async def position(update, context):

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
                "⚠️ get_position не найден."
            )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Ошибка BingX:\n{e}"
        )


# ============================================================
# BROADCAST
# ============================================================

async def broadcast(
    application,
    text,
):

    if not subscribers:
        return

    for chat_id in list(subscribers):

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

    sweep = result.get("sweep")

    if not sweep:
        return

    direction = get_direction(result)

    if direction not in ("LONG", "SHORT"):
        return

    sweep_price = level_price(sweep)

    sweep_time = sweep.get(
        "open_time",
        sweep.get("time", ""),
    )

    key = (
        f"{symbol}_{direction}_"
        f"{sweep_price}_{sweep_time}"
    )

    if last_sweep.get(symbol) == key:
        return

    last_sweep[symbol] = key

    text = (
        "🔎 TRADEMIND 5.8 — SWEEP\n\n"
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
# 15M ALERT
# ============================================================

async def process_confirmation_alert(
    application,
    symbol,
    result,
):

    stage = result.get("stage")

    if stage not in (
        "15M_CONFIRMED",
        "CONFIRMED",
        "5M",
        "READY",
    ):
        return

    direction = get_direction(result)

    if direction not in ("LONG", "SHORT"):
        return

    confirmation_time = result.get(
        "confirmation_15m_time",
        result.get(
            "confirmation_time",
            "",
        ),
    )

    # Если стратегия пока не отдаёт отдельное время
    # подтверждения, используем stage + sweep.
    if not confirmation_time:

        sweep = result.get("sweep") or {}

        confirmation_time = sweep.get(
            "open_time",
            sweep.get("time", ""),
        )

    key = (
        f"{symbol}_{direction}_"
        f"{confirmation_time}"
    )

    if last_confirmation.get(symbol) == key:
        return

    last_confirmation[symbol] = key

    await broadcast(
        application,
        "✅ TRADEMIND 5.8 — 15M CONFIRMATION\n\n"
        f"💠 {short_symbol(symbol)}\n"
        f"📐 {direction_emoji(direction)} "
        f"{direction}\n\n"
        "💧 Sweep найден.\n"
        "✅ 15M confirmation получен.\n"
        "⏳ Переходим к 5M trigger.\n"
        "❌ Вход пока запрещён.",
    )


# ============================================================
# READY ALERT
# ============================================================

async def process_ready_alert(
    application,
    symbol,
    setup,
):

    global trades_today
    global daily_stop

    direction = get_direction(setup)
    score = get_score(setup)
    stage = setup.get("stage")

    entry = get_entry(setup)
    sl = get_sl(setup)
    tp = get_tp(setup)

    if not (
        stage == "READY"
        and direction in ("LONG", "SHORT")
        and score >= MIN_SCORE_READY
        and entry is not None
        and sl is not None
        and tp is not None
    ):
        return False

    reset_daily_state()

    if daily_stop:
        return False

    if trades_today >= MAX_TRADES_PER_DAY:

        daily_stop = True

        return False

    cooldown_key = (
        symbol,
        direction,
    )

    current_time = now()

    previous_signal = last_signal_time.get(
        cooldown_key
    )

    if previous_signal:

        elapsed = (
            current_time - previous_signal
        ).total_seconds() / 60

        if elapsed < SIGNAL_COOLDOWN_MINUTES:
            return False

    # Регистрируем сигнал.
    trades_today += 1

    if trades_today >= MAX_TRADES_PER_DAY:
        daily_stop = True

    last_signal_time[
        cooldown_key
    ] = current_time

    last_setups[symbol] = setup

    message = (
        "🚨 TRADEMIND 5.8 — "
        "ГОТОВЫЙ СЕТАП\n\n"
        f"💠 {short_symbol(symbol)}\n"
        f"📐 {direction_emoji(direction)} "
        f"{direction}\n"
        f"⭐ Score: {score}/100\n\n"
        f"💰 Entry: {fmt_price(entry)}\n"
        f"🛑 SL: {fmt_price(sl)}\n"
        f"🏁 TP: {fmt_price(tp)}\n\n"
        f"⚖️ RR: "
        f"{setup.get('rr', '—')}\n\n"
        "🟢 МОЖНО ВХОДИТЬ\n\n"
        f"Лимит: "
        f"{trades_today}/{MAX_TRADES_PER_DAY}"
    )

    await broadcast(
        application,
        message,
    )

    return True


# ============================================================
# BACKGROUND SCAN
# ============================================================

async def scan_all_coins():

    # Параллельно обновляем все 9 монет.
    tasks = [
        async_build_analysis(symbol)
        for symbol in SYMBOLS
    ]

    results = await asyncio.gather(
        *tasks,
        return_exceptions=True,
    )

    successful = 0

    for symbol, result in zip(
        SYMBOLS,
        results,
    ):

        if isinstance(result, Exception):

            logger.warning(
                "Background scan failed %s: %s",
                symbol,
                result,
            )

            continue

        await cache_result(
            symbol,
            result,
        )

        successful += 1

    logger.info(
        "Market cache updated: %s/%s",
        successful,
        len(SYMBOLS),
    )


# ============================================================
# MONITOR
# ============================================================

async def monitor(application):

    logger.info(
        "TradeMind 5.8 background monitor started."
    )

    # Первый скан сразу.
    first_scan = True

    while True:

        try:

            reset_daily_state()

            # ------------------------------------------------
            # ВАЖНО:
            # сканируем рынок независимо от подписчиков.
            # Поэтому /market всегда получает свежий кэш.
            # ------------------------------------------------

            await scan_all_coins()

            # ------------------------------------------------
            # Alerts
            # ------------------------------------------------

            if subscribers:

                for symbol in SYMBOLS:

                    result = await get_cached(
                        symbol
                    )

                    if result is None:
                        continue

                    try:

                        await process_sweep_alert(
                            application,
                            symbol,
                            result,
                        )

                        await process_confirmation_alert(
                            application,
                            symbol,
                            result,
                        )

                        await process_ready_alert(
                            application,
                            symbol,
                            result,
                        )

                    except Exception as e:

                        logger.exception(
                            "Alert error %s: %s",
                            symbol,
                            e,
                        )

            first_scan = False

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

async def post_init(application):

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
        monitor(application)
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
        .token(BOT_TOKEN)

        # Telegram timeout protection.
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)

        .post_init(post_init)
        .build()
    )

    # --------------------------------------------------------
    # COMMANDS
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(
        CommandHandler("status", status)
    )

    application.add_handler(
        CommandHandler("sol", sol)
    )

    application.add_handler(
        CommandHandler("eth", eth)
    )

    application.add_handler(
        CommandHandler("market", market)
    )

    application.add_handler(
        CommandHandler("search", search)
    )

    application.add_handler(
        CommandHandler("levels", levels)
    )

    application.add_handler(
        CommandHandler("subscribe", subscribe)
    )

    application.add_handler(
        CommandHandler("unsubscribe", unsubscribe)
    )

    application.add_handler(
        CommandHandler("journal", journal)
    )

    application.add_handler(
        CommandHandler("bingx_status", bingx_status)
    )

    application.add_handler(
        CommandHandler("balance", balance)
    )

    application.add_handler(
        CommandHandler("position", position)
    )

    logger.info(
        "TradeMind 5.8 starting..."
    )

    application.run_polling()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()