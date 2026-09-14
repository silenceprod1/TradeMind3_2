"""
TradeMind 5.8 — Telegram Bot

Market reference:
Binance Spot.

Execution:
BingX optional.

Strategy:
D1/W1 -> 1H -> Major Liquidity -> Sweep
-> 15M -> 5M ILM -> Entry -> SL -> exact 1:2 TP.
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
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
)


# =========================================================
# OPTIONAL BINGX
# =========================================================

try:
    import bingx
    BINGX_AVAILABLE = True
except Exception:
    bingx = None
    BINGX_AVAILABLE = False


# =========================================================
# CONFIG
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")

CHECK_INTERVAL = 15

SCAN_WORKERS = 9

MIN_SCORE_READY = 80

# New doctrine:
# one trade / signal per day.
MAX_TRADES_PER_DAY = 1

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

SUBSCRIBERS_FILE = "subscribers.json"

STATE_FILE = "monitor_state.json"

JOURNAL_FILE = "trade_journal.json"


# =========================================================
# JSON
# =========================================================

def load_json(
    filename,
    default,
):
    try:

        if not os.path.exists(
            filename
        ):
            return default

        with open(
            filename,
            "r",
            encoding="utf-8",
        ) as file:

            return json.load(file)

    except Exception as exc:

        print(
            f"JSON LOAD ERROR "
            f"{filename}: {exc}"
        )

        return default


def save_json(
    filename,
    data,
):
    try:

        with open(
            filename,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
            )

    except Exception as exc:

        print(
            f"JSON SAVE ERROR "
            f"{filename}: {exc}"
        )


# =========================================================
# STATE
# =========================================================

def default_state():

    return {
        "daily_date": None,

        "daily_trades": 0,

        "daily_stop": False,

        "last_signal_key": None,

        "last_sweep_keys": {},

        "last_15m_keys": {},

        "subscribers": [],
    }


def load_state():

    state = load_json(
        STATE_FILE,
        default_state(),
    )

    if not isinstance(
        state,
        dict,
    ):
        state = default_state()

    return state


def save_state(state):

    save_json(
        STATE_FILE,
        state,
    )


def reset_daily_state_if_needed():

    state = load_state()

    today = datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d")

    if state.get(
        "daily_date"
    ) != today:

        state["daily_date"] = today

        state["daily_trades"] = 0

        state["daily_stop"] = False

        state["last_signal_key"] = None

        state["last_sweep_keys"] = {}

        state["last_15m_keys"] = {}

        save_state(state)

    return state


# =========================================================
# SUBSCRIBERS
# =========================================================

def load_subscribers():

    data = load_json(
        SUBSCRIBERS_FILE,
        [],
    )

    if not isinstance(
        data,
        list,
    ):
        return []

    return data


def save_subscribers(
    subscribers,
):

    save_json(
        SUBSCRIBERS_FILE,
        subscribers,
    )


# =========================================================
# FORMATTING
# =========================================================

def format_price(
    value,
):

    if value is None:
        return "N/A"

    try:
        value = float(value)
    except Exception:
        return "N/A"

    if value >= 1000:
        return f"${value:,.2f}"

    if value >= 1:
        return f"${value:,.4f}"

    return f"${value:,.6f}"


def format_rr(
    value,
):

    if value is None:
        return "N/A"

    try:
        return f"1:{float(value):.2f}"
    except Exception:
        return "N/A"


def stage_icon(
    stage,
):

    return {
        "READY": "🟢",
        "5M": "🔵",
        "15M": "🟡",
        "SWEEP": "🟠",
        "1H": "🟣",
        "D1": "⚪",
        "TP": "🔴",
        "WAIT": "⏳",
    }.get(
        stage,
        "⚪",
    )


def stage_text(
    stage,
):

    return {
        "READY": "МОЖНО ВХОДИТЬ",
        "5M": "ЖДЁМ 5M ILM",
        "15M": "ЖДЁМ 15M",
        "SWEEP": "ЖДЁМ SWEEP",
        "1H": "1H НЕ СИНХРОНИЗИРОВАН",
        "D1": "D1 НЕЯСНЫЙ",
        "TP": "TP НЕ ПРОХОДИТ",
        "WAIT": "ОЖИДАНИЕ",
    }.get(
        stage,
        "ОЖИДАНИЕ",
    )


# =========================================================
# LIQUIDITY FORMAT
# =========================================================

def format_levels(
    levels,
    price,
):

    if not levels:
        return (
            "💧 Крупная ликвидность "
            "не найдена."
        )

    try:
        current = float(price)
    except Exception:
        current = 0.0

    lines = []

    for level in levels:

        try:
            lp = float(
                level.get(
                    "price",
                    level.get(
                        "level"
                    ),
                )
            )
        except Exception:
            continue

        side = str(
            level.get(
                "side",
                "",
            )
        ).upper()

        touches = level.get(
            "touches",
            1,
        )

        strength = level.get(
            "strength",
            0,
        )

        distance = (
            abs(lp - current)
            / current
            * 100
            if current
            else 0
        )

        if side == "SHORT":

            icon = "🔴"

            label = "BSL / SHORT SWEEP"

        else:

            icon = "🟢"

            label = "SSL / LONG SWEEP"

        lines.append(
            f"{icon} "
            f"<b>{format_price(lp)}</b> "
            f"— {label}\n"
            f"   touches: {touches} | "
            f"strength: {strength} | "
            f"distance: {distance:.2f}%"
        )

    return "\n".join(lines)


# =========================================================
# MARKET ANALYSIS
# =========================================================

def build_analysis(
    symbol,
):

    data = get_market_data(
        symbol
    )

    if not data:
        raise Exception(
            f"Нет данных для {symbol}"
        )

    price = data["price"]

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

    # -----------------------------------------------------
    # Major liquidity
    # -----------------------------------------------------

    major_levels = (
        find_major_liquidity(
            candles_1h,
            price,
            max_levels=6,
        )
    )

    # -----------------------------------------------------
    # FIRST PASS
    # Get D1/1H direction.
    # -----------------------------------------------------

    context = analyze(
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,

        current_price=price,

        major_levels=major_levels,

        sweep=None,

        candles_d1=candles_d1,

        candles_w1=candles_w1,
    )

    direction = context.get(
        "direction"
    )

    # -----------------------------------------------------
    # Directional sweep
    # -----------------------------------------------------

    sweep = None

    if direction in {
        "LONG",
        "SHORT",
    }:

        sweep = detect_sweep(
            candles_1h,
            price,
            direction,
        )

    # -----------------------------------------------------
    # SECOND PASS
    # Full strategy.
    # -----------------------------------------------------

    result = analyze(
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,

        current_price=price,

        major_levels=major_levels,

        sweep=sweep,

        candles_d1=candles_d1,

        candles_w1=candles_w1,
    )

    result.update({
        "symbol": symbol,

        "price": price,

        "major_levels":
            major_levels,

        "sweep":
            sweep,

        "candles_5m":
            candles_5m,

        "candles_15m":
            candles_15m,

        "candles_1h":
            candles_1h,

        "candles_d1":
            candles_d1,

        "candles_w1":
            candles_w1,

        "strategy_version":
            STRATEGY_VERSION,
    })

    return result


def scan_one_coin(
    coin,
    symbol,
):

    try:

        return (
            coin,
            build_analysis(
                symbol
            ),
        )

    except Exception as exc:

        print(
            f"{coin} ERROR: {exc}"
        )

        return (
            coin,
            {
                "error": str(exc),

                "symbol": symbol,
            },
        )


def scan_all_coins():

    results = {}

    with ThreadPoolExecutor(
        max_workers=SCAN_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                scan_one_coin,
                coin,
                symbol,
            ): coin

            for coin, symbol
            in COINS.items()
        }

        for future in as_completed(
            futures
        ):

            coin = futures[
                future
            ]

            try:

                c, result = (
                    future.result()
                )

                results[c] = result

            except Exception as exc:

                results[coin] = {
                    "error": str(exc),

                    "symbol":
                        COINS[coin],
                }

    return {
        coin: results[coin]

        for coin in COINS

        if coin in results
    }


async def scan_all_coins_async():

    return await asyncio.to_thread(
        scan_all_coins
    )


# =========================================================
# READY
# =========================================================

def find_first_ready(
    results,
):

    ready = []

    for coin, result in results.items():

        if result.get("error"):
            continue

        if (
            result.get("stage")
            == "READY"
            and result.get("score", 0)
            >= MIN_SCORE_READY
        ):

            ready.append(
                (
                    result.get(
                        "score",
                        0,
                    ),
                    coin,
                    result,
                )
            )

    if not ready:
        return None

    ready.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    return ready[0]


# =========================================================
# TELEGRAM KEYBOARDS
# =========================================================

def main_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📊 Рынок",
                callback_data="market",
            ),

            InlineKeyboardButton(
                "💧 Уровни",
                callback_data="levels",
            ),
        ],

        [
            InlineKeyboardButton(
                "🔎 Поиск сетапа",
                callback_data="search",
            )
        ],

        [
            InlineKeyboardButton(
                "📈 SOL",
                callback_data="sol",
            ),

            InlineKeyboardButton(
                "📊 Статус",
                callback_data="status",
            ),
        ],

        [
            InlineKeyboardButton(
                "🔔 Включить",
                callback_data="subscribe",
            ),

            InlineKeyboardButton(
                "🔕 Выключить",
                callback_data="unsubscribe",
            ),
        ],

        [
            InlineKeyboardButton(
                "📒 Журнал",
                callback_data="journal",
            )
        ],
    ])


def back_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⬅️ Главное меню",
                callback_data="start",
            )
        ]
    ])


# =========================================================
# MESSAGES
# =========================================================

def build_market_message(
    results,
):

    lines = [
        "📊 <b>TRADEMIND 5.8 — РЫНОК</b>",
        "",
    ]

    for coin in COINS:

        result = results.get(
            coin
        )

        if not result:
            continue

        if result.get("error"):

            lines += [
                f"❌ <b>{coin}</b>",
                "Ошибка данных",
                "",
            ]

            continue

        price = result.get(
            "price"
        )

        stage = result.get(
            "stage",
            "WAIT",
        )

        score = result.get(
            "score",
            0,
        )

        direction = result.get(
            "direction"
        )

        lines += [
            f"💠 <b>{coin}</b> "
            f"{format_price(price)}",

            f"{stage_icon(stage)} "
            f"{stage_text(stage)} "
            f"— {score}/100",
        ]

        if direction:

            lines.append(
                f"📐 Direction: "
                f"<b>{direction}</b>"
            )

        levels = result.get(
            "major_levels",
            [],
        )

        if levels:

            nearest = min(
                levels,
                key=lambda x:
                    abs(
                        float(
                            x["price"]
                        )
                        - float(price)
                    ),
            )

            lines.append(
                "💧 Ближайшая major liquidity: "
                f"<b>{format_price(nearest['price'])}</b>"
            )

        lines += [
            "",
            "────────────",
            "",
        ]

    lines += [
        "D1 → 1H → Major Liquidity",
        "→ Sweep → 15M → 5M ILM",
        "→ Entry → SL → EXACT 1:2 TP",
        "",
        "❌ В середине движения не входим.",
    ]

    return "\n".join(lines)


def build_levels_message(
    results,
):

    lines = [
        "💧 <b>TRADEMIND 5.8 — MAJOR LIQUIDITY</b>",
        "",
        "Только крупные 1H уровни.",
        "Снятая ликвидность не возвращается как свежая цель.",
        "",
    ]

    for coin in COINS:

        result = results.get(
            coin
        )

        if (
            not result
            or result.get("error")
        ):
            continue

        lines += [
            f"💠 <b>{coin}</b> "
            f"{format_price(result.get('price'))}",

            format_levels(
                result.get(
                    "major_levels",
                    [],
                ),
                result.get(
                    "price"
                ),
            ),

            "",
            "────────────",
            "",
        ]

    return "\n".join(lines)


def build_search_message(
    results,
):

    state = reset_daily_state_if_needed()

    if (
        state.get(
            "daily_stop"
        )
        or state.get(
            "daily_trades",
            0,
        ) >= MAX_TRADES_PER_DAY
    ):

        return (
            "🔎 <b>ПОИСК СЕТАПА</b>\n\n"
            "🛑 Дневной лимит уже закрыт.\n"
            "Сегодня новых входов нет."
        )

    ready = find_first_ready(
        results
    )

    if ready:

        score, coin, result = ready

        return "\n".join([
            "🟢 <b>TRADEMIND — READY</b>",
            "",
            f"💠 Монета: <b>{coin}</b>",
            f"📐 Направление: <b>{result.get('direction')}</b>",
            f"⭐ Score: <b>{score}/100</b>",
            "",
            f"Entry: <b>{format_price(result.get('entry'))}</b>",
            f"SL: <b>{format_price(result.get('sl'))}</b>",
            f"TP: <b>{format_price(result.get('tp'))}</b>",
            f"RR: <b>{format_rr(result.get('rr'))}</b>",
            "",
            "✅ D1",
            "✅ 1H",
            "✅ Major Liquidity Sweep",
            "✅ 15M Confirmation",
            "✅ 5M ILM",
            "✅ EXACT 1:2",
            "",
            "🟢 МОЖНО ВХОДИТЬ",
        ])

    lines = [
        "🔎 <b>ПОИСК СЕТАПА</b>",
        "",
        "❌ Готового входа сейчас нет.",
        "",
    ]

    for coin in COINS:

        result = results.get(
            coin
        )

        if not result:
            continue

        if result.get("error"):
            continue

        stage = result.get(
            "stage",
            "WAIT",
        )

        score = result.get(
            "score",
            0,
        )

        lines.append(
            f"{stage_icon(stage)} "
            f"<b>{coin}</b>: "
            f"{stage_text(stage)} "
            f"— {score}/100"
        )

    lines += [
        "",
        "Ждём:",
        "Major Liquidity → Sweep → 15M → 5M ILM.",
        "",
        "❌ Нет confirmation → нет входа.",
    ]

    return "\n".join(lines)


def build_sol_message(
    result,
):

    if result.get("error"):

        return (
            "❌ <b>SOL</b>\n\n"
            "Ошибка получения данных."
        )

    stage = result.get(
        "stage",
        "WAIT",
    )

    lines = [
        "📈 <b>TRADEMIND 5.8 — SOL</b>",
        "",
        f"💰 Цена: "
        f"<b>{format_price(result.get('price'))}</b>",

        f"{stage_icon(stage)} "
        f"{stage_text(stage)}",

        f"⭐ Score: "
        f"<b>{result.get('score', 0)}/100</b>",
    ]

    if result.get(
        "direction"
    ):

        lines.append(
            f"📐 Direction: "
            f"<b>{result.get('direction')}</b>"
        )

    lines += [
        "",
        "💧 <b>MAJOR LIQUIDITY</b>",
        format_levels(
            result.get(
                "major_levels",
                [],
            ),
            result.get(
                "price"
            ),
        ),
    ]

    sweep = result.get(
        "sweep"
    )

    if sweep:

        lines += [
            "",
            "🚨 <b>SWEEP</b>",
            f"Level: "
            f"<b>{format_price(sweep.get('level'))}</b>",
            f"Extreme: "
            f"<b>{format_price(sweep.get('extreme'))}</b>",
        ]

    if result.get(
        "confirmation_15m"
    ):

        lines += [
            "",
            "15M:",
            result.get(
                "confirmation_15m"
            ),
        ]

    if result.get(
        "confirmation"
    ):

        lines += [
            "",
            "5M ILM:",
            result.get(
                "confirmation"
            ),
        ]

    if result.get(
        "entry"
    ) is not None:

        lines += [
            "",
            "🎯 <b>SETUP</b>",
            "",
            f"Entry: <b>{format_price(result.get('entry'))}</b>",
            f"SL: <b>{format_price(result.get('sl'))}</b>",
            f"TP: <b>{format_price(result.get('tp'))}</b>",
            f"RR: <b>{format_rr(result.get('rr'))}</b>",
            "",
            "🟢 <b>МОЖНО ВХОДИТЬ</b>",
        ]

    lines += [
        "",
        "Причина:",
        str(
            result.get(
                "reason",
                "",
            )
        ),
    ]

    return "\n".join(lines)


# =========================================================
# COMMANDS
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "🧠 <b>TRADEMIND 5.8</b>\n\n"
        "D1 → 1H → Major Liquidity → "
        "Sweep → 15M → 5M ILM → Entry → SL → 1:2\n\n"
        "BingX execution: "
        f"{'AVAILABLE' if BINGX_AVAILABLE else 'OFF'}",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


async def market_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    try:

        results = await asyncio.to_thread(
            scan_all_coins
        )

        await update.message.reply_text(
            build_market_message(
                results
            ),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

    except Exception as exc:

        await update.message.reply_text(
            f"❌ Ошибка рынка:\n{exc}"
        )


async def levels_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    try:

        results = await asyncio.to_thread(
            scan_all_coins
        )

        await update.message.reply_text(
            build_levels_message(
                results
            ),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

    except Exception as exc:

        await update.message.reply_text(
            f"❌ Ошибка уровней:\n{exc}"
        )


async def search_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    try:

        results = await asyncio.to_thread(
            scan_all_coins
        )

        await update.message.reply_text(
            build_search_message(
                results
            ),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

    except Exception as exc:

        await update.message.reply_text(
            f"❌ Ошибка поиска:\n{exc}"
        )


async def sol_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    try:

        result = await asyncio.to_thread(
            build_analysis,
            COINS["SOL"],
        )

        await update.message.reply_text(
            build_sol_message(
                result
            ),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

    except Exception as exc:

        await update.message.reply_text(
            f"❌ SOL ERROR:\n{exc}"
        )


async def subscribe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = update.effective_chat.id

    subscribers = load_subscribers()

    if chat_id not in subscribers:

        subscribers.append(
            chat_id
        )

        save_subscribers(
            subscribers
        )

    await update.message.reply_text(
        "🔔 Мониторинг включён.\n\n"
        "Я буду искать:\n"
        "Major Liquidity → Sweep → "
        "15M → 5M ILM → READY."
    )


async def unsubscribe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = update.effective_chat.id

    subscribers = load_subscribers()

    if chat_id in subscribers:

        subscribers.remove(
            chat_id
        )

        save_subscribers(
            subscribers
        )

    await update.message.reply_text(
        "🔕 Мониторинг выключен."
    )


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    state = reset_daily_state_if_needed()

    bingx_status = (
        "AVAILABLE"
        if BINGX_AVAILABLE
        else "OFF"
    )

    text = "\n".join([
        "📊 <b>TRADEMIND 5.8 — STATUS</b>",
        "",
        f"Strategy: <b>{STRATEGY_VERSION}</b>",
        "Market: <b>Binance Spot</b>",
        f"BingX module: <b>{bingx_status}</b>",
        "",
        f"Сделок сегодня: "
        f"<b>{state.get('daily_trades', 0)}/1</b>",
        f"Daily stop: "
        f"<b>{'YES' if state.get('daily_stop') else 'NO'}</b>",
        "",
        "1 trade/day",
        "One TP",
        "RR exactly 1:2",
    ])

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


# =========================================================
# JOURNAL
# =========================================================

async def journal_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    journal = load_json(
        JOURNAL_FILE,
        [],
    )

    if not journal:

        await update.message.reply_text(
            "📒 Журнал пока пуст."
        )

        return

    last_trades = journal[-10:]

    lines = [
        "📒 <b>TRADEMIND — JOURNAL</b>",
        "",
    ]

    for trade in reversed(
        last_trades
    ):

        lines += [
            f"💠 {trade.get('coin', '?')}",
            f"Direction: {trade.get('direction', '?')}",
            f"Result: {trade.get('result', '?')}",
            f"Entry: {trade.get('entry', '?')}",
            f"SL: {trade.get('sl', '?')}",
            f"TP: {trade.get('tp', '?')}",
            "",
        ]

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


# =========================================================
# MONITOR
# =========================================================

def sweep_key(
    coin,
    sweep,
):

    if not sweep:
        return None

    return (
        f"{coin}:"
        f"{sweep.get('direction')}:"
        f"{sweep.get('level')}:"
        f"{sweep.get('time')}"
    )


def ready_key(
    coin,
    result,
):

    return (
        f"{coin}:"
        f"{result.get('direction')}:"
        f"{result.get('entry')}:"
        f"{result.get('sl')}:"
        f"{result.get('tp')}"
    )


async def send_to_subscribers(
    application,
    text,
):

    subscribers = load_subscribers()

    for chat_id in subscribers:

        try:

            await application.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
            )

        except Exception as exc:

            print(
                f"TELEGRAM SEND ERROR "
                f"{chat_id}: {exc}"
            )


async def monitor_job(
    context: ContextTypes.DEFAULT_TYPE,
):

    state = reset_daily_state_if_needed()

    # One trade per day.
    if (
        state.get(
            "daily_stop"
        )
        or state.get(
            "daily_trades",
            0,
        ) >= MAX_TRADES_PER_DAY
    ):
        return

    try:

        results = await asyncio.to_thread(
            scan_all_coins
        )

    except Exception as exc:

        print(
            f"MONITOR ERROR: {exc}"
        )

        return

    # -----------------------------------------------------
    # SWEEP ALERT
    # -----------------------------------------------------

    for coin, result in results.items():

        if result.get("error"):
            continue

        sweep = result.get(
            "sweep"
        )

        if not sweep:
            continue

        key = sweep_key(
            coin,
            sweep,
        )

        previous = state[
            "last_sweep_keys"
        ].get(coin)

        if key == previous:
            continue

        state[
            "last_sweep_keys"
        ][coin] = key

        text = "\n".join([
            "🚨 <b>TRADEMIND 5.8 — SWEEP</b>",
            "",
            f"💠 {coin}",
            f"📐 {sweep.get('direction')}",
            "",
            f"💧 Level: "
            f"<b>{format_price(sweep.get('level'))}</b>",
            f"Extreme: "
            f"<b>{format_price(sweep.get('extreme'))}</b>",
            "",
            "✅ Major liquidity taken.",
            "⏳ Ждём 15M confirmation.",
            "❌ Вход пока запрещён.",
        ])

        await send_to_subscribers(
            context.application,
            text,
        )

    # -----------------------------------------------------
    # READY
    # -----------------------------------------------------

    ready = find_first_ready(
        results
    )

    if not ready:
        save_state(state)
        return

    score, coin, result = ready

    key = ready_key(
        coin,
        result,
    )

    if (
        state.get(
            "last_signal_key"
        )
        == key
    ):
        save_state(state)
        return

    # -----------------------------------------------------
    # DAILY LIMIT
    # -----------------------------------------------------

    if (
        state.get(
            "daily_trades",
            0,
        )
        >= MAX_TRADES_PER_DAY
    ):

        save_state(state)

        return

    # Mark one signal/trade.
    state[
        "daily_trades"
    ] = (
        state.get(
            "daily_trades",
            0,
        )
        + 1
    )

    state[
        "last_signal_key"
    ] = key

    # After one setup:
    # no more setups today.
    state[
        "daily_stop"
    ] = True

    save_state(state)

    text = "\n".join([
        "🟢 <b>TRADEMIND 5.8 — READY</b>",
        "",
        f"💠 Монета: <b>{coin}</b>",
        f"📐 Направление: "
        f"<b>{result.get('direction')}</b>",
        f"⭐ Score: <b>{score}/100</b>",
        "",
        f"Entry: "
        f"<b>{format_price(result.get('entry'))}</b>",
        f"SL: "
        f"<b>{format_price(result.get('sl'))}</b>",
        f"TP: "
        f"<b>{format_price(result.get('tp'))}</b>",
        f"RR: "
        f"<b>{format_rr(result.get('rr'))}</b>",
        "",
        "✅ D1",
        "✅ 1H",
        "✅ Major Liquidity Sweep",
        "✅ 15M Confirmation",
        "✅ 5M ILM",
        "✅ EXACT 1:2",
        "",
        "🟢 <b>МОЖНО ВХОДИТЬ</b>",
        "",
        "🛑 Сегодня новых сетапов больше нет.",
    ])

    await send_to_subscribers(
        context.application,
        text,
    )


# =========================================================
# CALLBACKS
# =========================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    action = query.data

    if action == "start":

        await query.edit_message_text(
            "🧠 <b>TRADEMIND 5.8</b>\n\n"
            "D1 → 1H → Major Liquidity → "
            "Sweep → 15M → 5M ILM → "
            "Entry → SL → EXACT 1:2",
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )

        return

    if action == "market":

        results = await asyncio.to_thread(
            scan_all_coins
        )

        await query.edit_message_text(
            build_market_message(
                results
            ),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

        return

    if action == "levels":

        results = await asyncio.to_thread(
            scan_all_coins
        )

        await query.edit_message_text(
            build_levels_message(
                results
            ),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

        return

    if action == "search":

        results = await asyncio.to_thread(
            scan_all_coins
        )

        await query.edit_message_text(
            build_search_message(
                results
            ),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

        return

    if action == "sol":

        result = await asyncio.to_thread(
            build_analysis,
            COINS["SOL"],
        )

        await query.edit_message_text(
            build_sol_message(
                result
            ),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

        return

    if action == "status":

        state = reset_daily_state_if_needed()

        await query.edit_message_text(
            "\n".join([
                "📊 <b>TRADEMIND 5.8</b>",
                "",
                f"Strategy: <b>{STRATEGY_VERSION}</b>",
                "Market: <b>Binance Spot</b>",
                f"BingX: <b>{'AVAILABLE' if BINGX_AVAILABLE else 'OFF'}</b>",
                "",
                f"Trades today: "
                f"<b>{state.get('daily_trades', 0)}/1</b>",
                f"Daily stop: "
                f"<b>{'YES' if state.get('daily_stop') else 'NO'}</b>",
                "",
                "RR: <b>1:2</b>",
                "TP: <b>ONE</b>",
            ]),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

        return

    if action == "subscribe":

        chat_id = query.message.chat_id

        subscribers = load_subscribers()

        if chat_id not in subscribers:

            subscribers.append(
                chat_id
            )

            save_subscribers(
                subscribers
            )

        await query.edit_message_text(
            "🔔 Мониторинг включён.",
            reply_markup=back_keyboard(),
        )

        return

    if action == "unsubscribe":

        chat_id = query.message.chat_id

        subscribers = load_subscribers()

        if chat_id in subscribers:

            subscribers.remove(
                chat_id
            )

            save_subscribers(
                subscribers
            )

        await query.edit_message_text(
            "🔕 Мониторинг выключен.",
            reply_markup=back_keyboard(),
        )

        return

    if action == "journal":

        journal = load_json(
            JOURNAL_FILE,
            [],
        )

        await query.edit_message_text(
            f"📒 <b>Журнал</b>\n\n"
            f"Записей: <b>{len(journal)}</b>",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )


# =========================================================
# START
# =========================================================

async def post_init(
    application,
):

    application.job_queue.run_repeating(
        monitor_job,
        interval=CHECK_INTERVAL,
        first=5,
    )


def main():

    if not TOKEN:

        raise RuntimeError(
            "BOT_TOKEN environment variable is not set."
        )

    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "market",
            market_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "levels",
            levels_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "search",
            search_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "sol",
            sol_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "subscribe",
            subscribe_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "unsubscribe",
            unsubscribe_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            status_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "journal",
            journal_command,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    print(
        f"TradeMind {STRATEGY_VERSION} started."
    )

    print(
        "BingX available:",
        BINGX_AVAILABLE,
    )

    application.run_polling()


if __name__ == "__main__":
    main()