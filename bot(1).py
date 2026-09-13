import asyncio
import json
import os
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
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

from strategy import analyze


# =========================================================
# CONFIG
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")

CHECK_INTERVAL = 30

COINS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
}

SUBSCRIBERS_FILE = "subscribers.json"
STATE_FILE = "monitor_state.json"


# =========================================================
# FILE HELPERS
# =========================================================

def load_json(filename, default):
    try:
        if not os.path.exists(filename):
            return default

        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        return default


def save_json(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    except Exception as e:
        print(f"JSON SAVE ERROR: {e}")


def load_subscribers():
    return load_json(SUBSCRIBERS_FILE, [])


def save_subscribers(data):
    save_json(SUBSCRIBERS_FILE, data)


def default_state():
    return {
        "active_coin": None,
        "active_symbol": None,
        "active_direction": None,
        "active_setup_key": None,

        "active_entry": None,
        "active_sl": None,
        "active_tp": None,
        "active_score": None,

        "active_stage": None,
        "last_alert": None,

        "coins": {}
    }


def load_state():
    return load_json(STATE_FILE, default_state())


def save_state(state):
    save_json(STATE_FILE, state)


# =========================================================
# TELEGRAM UI
# =========================================================

def main_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "📊 Рынок",
                callback_data="market"
            ),

            InlineKeyboardButton(
                "💧 Ключевые уровни",
                callback_data="levels"
            ),
        ],

        [
            InlineKeyboardButton(
                "🔎 Поиск сетапа",
                callback_data="search"
            ),
        ],

        [
            InlineKeyboardButton(
                "🔔 Уведомления",
                callback_data="subscribe"
            ),

            InlineKeyboardButton(
                "🔕 Выключить",
                callback_data="unsubscribe"
            ),
        ],

        [
            InlineKeyboardButton(
                "📈 SOL",
                callback_data="sol"
            ),

            InlineKeyboardButton(
                "📊 Статус",
                callback_data="status"
            ),
        ],

        [
            InlineKeyboardButton(
                "📒 Журнал",
                callback_data="journal"
            ),
        ],

    ])


def back_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⬅️ Главное меню",
                callback_data="start"
            )
        ]
    ])


# =========================================================
# FORMATTING
# =========================================================

def format_price(price):

    if price is None:
        return "N/A"

    if price >= 1000:
        return f"${price:,.2f}"

    if price >= 1:
        return f"${price:,.4f}"

    return f"${price:,.6f}"


def format_levels(levels, price):

    if not levels:
        return "💧 Крупные уровни не найдены."

    lines = []

    for level in levels:

        level_price = level.get("price")

        if level_price is None:
            continue

        level_type = level.get("type", "")

        distance = abs(level_price - price) / price * 100

        if "HIGH" in level_type:
            icon = "🔴"
        else:
            icon = "🟢"

        lines.append(
            f"{icon} {level_type}: "
            f"{format_price(level_price)} "
            f"({distance:.2f}%)"
        )

    if not lines:
        return "💧 Крупные уровни не найдены."

    return "\n".join(lines)


def stage_icon(stage):

    if stage == "READY":
        return "🟢"

    if stage == "CONFIRMED":
        return "🟡"

    if stage == "SWEPT":
        return "🟠"

    if stage == "WAIT":
        return "⏳"

    return "⚪"


def stage_text(stage):

    if stage == "READY":
        return "МОЖНО ВХОДИТЬ"

    if stage == "CONFIRMED":
        return "15M подтверждение"

    if stage == "SWEPT":
        return "Sweep обнаружен"

    if stage == "WAIT":
        return "Ожидание"

    return "Ожидание"


# =========================================================
# MARKET ANALYSIS
# =========================================================

def build_analysis(symbol):

    data = get_market_data(symbol)

    if not data:
        raise Exception(
            f"Нет данных для {symbol}"
        )

    price = data["price"]

    candles_1h = data["candles_1h"]
    candles_15m = data["candles_15m"]
    candles_5m = data["candles_5m"]

    major_levels = find_major_liquidity(
        candles_1h,
        price,
        max_levels=6
    )

    sweep = detect_sweep(
        candles_5m,
        major_levels
    )

    result = analyze(
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,
        current_price=price,
        major_levels=major_levels,
        sweep=sweep,
    )

    result["symbol"] = symbol
    result["price"] = price
    result["major_levels"] = major_levels
    result["sweep"] = sweep

    return result


# =========================================================
# ALL COINS
# =========================================================

def scan_all_coins():

    results = {}

    for coin, symbol in COINS.items():

        try:

            result = build_analysis(symbol)

            results[coin] = result

        except Exception as e:

            print(
                f"{coin} ERROR: {e}"
            )

            results[coin] = {
                "error": str(e),
                "symbol": symbol,
            }

    return results


# =========================================================
# READY SIGNAL
# =========================================================

def find_first_ready(results):

    ready = []

    for coin, result in results.items():

        if result.get("error"):
            continue

        stage = result.get("stage")
        score = result.get("score", 0)

        if stage == "READY" and score >= 80:

            ready.append(
                (
                    score,
                    coin,
                    result
                )
            )

    if not ready:
        return None

    ready.sort(
        key=lambda x: x[0],
        reverse=True
    )

    return ready[0]


# =========================================================
# MARKET MESSAGE
# =========================================================

def build_market_message(results):

    lines = [
        "📊 <b>TRADEMIND — РЫНОК</b>",
        "",
    ]

    for coin in ["BTC", "ETH", "SOL"]:

        result = results.get(coin)

        if not result:
            continue

        if result.get("error"):

            lines.extend([
                f"❌ <b>{coin}</b>",
                "Ошибка получения данных",
                "",
            ])

            continue

        price = result.get("price")

        score = result.get(
            "score",
            0
        )

        stage = result.get(
            "stage",
            "WAIT"
        )

        lines.extend([
            f"🟠 <b>{coin}</b> "
            f"{format_price(price)}",
            "",
            f"{stage_icon(stage)} "
            f"{stage_text(stage)}",
            f"Оценка: <b>{score}/100</b>",
            "",
            "💧 <b>Ключевые уровни:</b>",
            format_levels(
                result.get(
                    "major_levels",
                    []
                ),
                price
            ),
            "",
        ])

    lines.extend([
        "━━━━━━━━━━━━",
        "1H → 15M → Sweep → 15M → 5M",
        "Вход только после полного подтверждения.",
    ])

    return "\n".join(lines)


# =========================================================
# LEVELS MESSAGE
# =========================================================

def build_levels_message(results):

    lines = [
        "💧 <b>TRADEMIND — КЛЮЧЕВЫЕ УРОВНИ</b>",
        "",
        "Только крупная ликвидность 1H.",
        "Мелкие локальные уровни не учитываются.",
        "",
    ]

    for coin in ["BTC", "ETH", "SOL"]:

        result = results.get(coin)

        if not result:
            continue

        if result.get("error"):

            lines.extend([
                f"❌ <b>{coin}</b>",
                "Нет данных",
                "",
            ])

            continue

        price = result.get("price")

        levels = result.get(
            "major_levels",
            []
        )

        lines.extend([
            f"💠 <b>{coin}</b> "
            f"{format_price(price)}",
            "",
            format_levels(
                levels,
                price
            ),
            "",
            "────────────",
            "",
        ])

    return "\n".join(lines)


# =========================================================
# SOL MESSAGE
# =========================================================

def build_sol_message(result):

    if result.get("error"):

        return (
            "❌ <b>SOL</b>\n\n"
            "Ошибка получения данных."
        )

    price = result.get("price")

    score = result.get(
        "score",
        0
    )

    stage = result.get(
        "stage",
        "WAIT"
    )

    direction = result.get(
        "direction"
    )

    lines = [
        "📈 <b>TRADEMIND — SOL</b>",
        "",
        f"💰 Цена: <b>{format_price(price)}</b>",
        "",
        f"{stage_icon(stage)} "
        f"{stage_text(stage)}",
        f"Score: <b>{score}/100</b>",
    ]

    if direction:

        lines.extend([
            "",
            f"Направление: "
            f"<b>{direction}</b>",
        ])

    lines.extend([
        "",
        "💧 <b>Ключевые уровни:</b>",
        format_levels(
            result.get(
                "major_levels",
                []
            ),
            price
        ),
    ])

    if result.get("entry"):

        lines.extend([
            "",
            "🎯 <b>СЕТАП</b>",
            "",
            f"Entry: "
            f"<b>{format_price(result['entry'])}</b>",
            f"SL: "
            f"<b>{format_price(result['sl'])}</b>",
            f"TP: "
            f"<b>{format_price(result['tp'])}</b>",
            "",
            "RR: <b>1:2</b>",
        ])

    reason = result.get(
        "reason"
    )

    if reason:

        lines.extend([
            "",
            "Причина:",
            reason,
        ])

    return "\n".join(lines)


# =========================================================
# SEARCH MESSAGE
# =========================================================

def build_search_message(results):

    lines = [
        "🔎 <b>ПОИСК СЕТАПА</b>",
        "",
    ]

    ready = find_first_ready(results)

    if ready:

        score, coin, result = ready

        lines.extend([
            "🟢 <b>НАЙДЕН СЕТАП</b>",
            "",
            f"Монета: <b>{coin}</b>",
            f"Направление: "
            f"<b>{result.get('direction', 'N/A')}</b>",
            f"Score: <b>{score}/100</b>",
            "",
            f"Entry: "
            f"<b>{format_price(result.get('entry'))}</b>",
            f"SL: "
            f"<b>{format_price(result.get('sl'))}</b>",
            f"TP: "
            f"<b>{format_price(result.get('tp'))}</b>",
            "",
            "RR: <b>1:2</b>",
            "",
            "🔥 Полное подтверждение получено.",
        ])

        return "\n".join(lines)

    lines.extend([
        "❌ Готового входа сейчас нет.",
        "",
    ])

    for coin in ["BTC", "ETH", "SOL"]:

        result = results.get(coin)

        if not result:
            continue

        if result.get("error"):

            lines.append(
                f"❌ {coin}: ошибка"
            )

            continue

        score = result.get(
            "score",
            0
        )

        stage = result.get(
            "stage",
            "WAIT"
        )

        lines.append(
            f"{stage_icon(stage)} "
            f"{coin}: "
            f"{stage_text(stage)} "
            f"— {score}/100"
        )

    lines.extend([
        "",
        "Ждём Sweep → 15M → 5M.",
        "В середине движения не входим.",
    ])

    return "\n".join(lines)


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        "🤖 <b>TRADEMIND</b>\n\n"
        "Мониторинг:\n"
        "• BTC\n"
        "• ETH\n"
        "• SOL\n\n"
        "Стратегия:\n"
        "1H → 15M → Sweep → 15M → 5M\n\n"
        "Вход только после полного "
        "подтверждения.\n"
        "RR: <b>1:2</b>"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


# =========================================================
# MARKET COMMAND
# =========================================================

async def send_market(
    message
):

    results = scan_all_coins()

    await message.reply_text(
        build_market_message(results),
        parse_mode="HTML",
        reply_markup=back_keyboard()
    )


async def market_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await send_market(
        update.message
    )


# =========================================================
# LEVELS COMMAND
# =========================================================

async def send_levels(
    message
):

    results = scan_all_coins()

    await message.reply_text(
        build_levels_message(results),
        parse_mode="HTML",
        reply_markup=back_keyboard()
    )


async def levels_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await send_levels(
        update.message
    )


# =========================================================
# SOL COMMAND
# =========================================================

async def send_sol(
    message
):

    try:

        result = build_analysis(
            "SOLUSDT"
        )

        await message.reply_text(
            build_sol_message(result),
            parse_mode="HTML",
            reply_markup=back_keyboard()
        )

    except Exception as e:

        await message.reply_text(
            f"❌ Ошибка SOL:\n{e}",
            reply_markup=back_keyboard()
        )


async def sol_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await send_sol(
        update.message
    )


# =========================================================
# SEARCH COMMAND
# =========================================================

async def send_search(
    message
):

    results = scan_all_coins()

    await message.reply_text(
        build_search_message(results),
        parse_mode="HTML",
        reply_markup=back_keyboard()
    )


async def search_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await send_search(
        update.message
    )


# =========================================================
# SUBSCRIBE
# =========================================================

async def do_subscribe(
    chat_id,
    message
):

    subscribers = load_subscribers()

    if chat_id not in subscribers:

        subscribers.append(
            chat_id
        )

        save_subscribers(
            subscribers
        )

        text = (
            "🔔 <b>Уведомления включены.</b>\n\n"
            "Я буду отслеживать BTC, ETH и SOL."
        )

    else:

        text = (
            "🔔 Уведомления уже включены."
        )

    await message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=back_keyboard()
    )


async def subscribe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await do_subscribe(
        update.effective_chat.id,
        update.message
    )


# =========================================================
# UNSUBSCRIBE
# =========================================================

async def do_unsubscribe(
    chat_id,
    message
):

    subscribers = load_subscribers()

    if chat_id in subscribers:

        subscribers.remove(
            chat_id
        )

        save_subscribers(
            subscribers
        )

        text = (
            "🔕 <b>Уведомления выключены.</b>"
        )

    else:

        text = (
            "🔕 Уведомления уже выключены."
        )

    await message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=back_keyboard()
    )


async def unsubscribe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await do_unsubscribe(
        update.effective_chat.id,
        update.message
    )


# =========================================================
# STATUS
# =========================================================

async def send_status(
    message
):

    state = load_state()

    active_coin = state.get(
        "active_coin"
    )

    if active_coin:

        text = (
            "📊 <b>TRADEMIND — СТАТУС</b>\n\n"
            f"🟢 Активная монета: "
            f"<b>{active_coin}</b>\n"
            f"Направление: "
            f"<b>{state.get('active_direction')}</b>\n"
            f"Stage: "
            f"<b>{state.get('active_stage')}</b>\n"
            f"Score: "
            f"<b>{state.get('active_score')}/100</b>\n\n"
            f"Entry: "
            f"<b>{format_price(state.get('active_entry'))}</b>\n"
            f"SL: "
            f"<b>{format_price(state.get('active_sl'))}</b>\n"
            f"TP: "
            f"<b>{format_price(state.get('active_tp'))}</b>"
        )

    else:

        text = (
            "📊 <b>TRADEMIND — СТАТУС</b>\n\n"
            "🟢 Активного входа нет.\n\n"
            "Сканируются:\n"
            "BTC • ETH • SOL"
        )

    await message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=back_keyboard()
    )


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await send_status(
        update.message
    )


# =========================================================
# JOURNAL
# =========================================================

async def send_journal(
    message
):

    state = load_state()

    text = (
        "📒 <b>TRADEMIND — ЖУРНАЛ</b>\n\n"
        "Автоматический журнал сделок "
        "будет подключён следующим этапом.\n\n"
        "План:\n"
        "• Entry\n"
        "• SL\n"
        "• TP\n"
        "• Win/Loss\n"
        "• R\n"
        "• Winrate\n"
        "• серия из 10 сделок"
    )

    await message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=back_keyboard()
    )


async def journal_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await send_journal(
        update.message
    )


# =========================================================
# BROADCAST
# =========================================================

async def broadcast(
    application,
    text
):

    subscribers = load_subscribers()

    for chat_id in subscribers:

        try:

            await application.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML"
            )

        except Exception as e:

            print(
                f"BROADCAST ERROR {chat_id}: {e}"
            )


# =========================================================
# MONITOR
# =========================================================

monitor_lock = asyncio.Lock()


async def monitor(
    application
):

    print(
        "TradeMind monitor started."
    )

    while True:

        try:

            state = load_state()

            # ---------------------------------------------
            # If there is an active setup
            # ---------------------------------------------

            if state.get("active_coin"):

                await asyncio.sleep(
                    CHECK_INTERVAL
                )

                continue

            # ---------------------------------------------
            # Scan all coins
            # ---------------------------------------------

            results = scan_all_coins()

            # ---------------------------------------------
            # Check READY
            # ---------------------------------------------

            ready = find_first_ready(
                results
            )

            if ready:

                score, coin, result = ready

                direction = result.get(
                    "direction"
                )

                entry = result.get(
                    "entry"
                )

                sl = result.get(
                    "sl"
                )

                tp = result.get(
                    "tp"
                )

                setup_key = (
                    f"{coin}_"
                    f"{direction}_"
                    f"{entry}_"
                    f"{sl}_"
                    f"{tp}"
                )

                if (
                    state.get(
                        "active_setup_key"
                    )
                    != setup_key
                ):

                    state.update({

                        "active_coin": coin,

                        "active_symbol":
                            result.get(
                                "symbol"
                            ),

                        "active_direction":
                            direction,

                        "active_setup_key":
                            setup_key,

                        "active_entry":
                            entry,

                        "active_sl":
                            sl,

                        "active_tp":
                            tp,

                        "active_score":
                            score,

                        "active_stage":
                            "READY",

                        "last_alert":
                            datetime.utcnow().isoformat(),
                    })

                    save_state(
                        state
                    )

                    text = (
                        "🚨 <b>TRADEMIND — "
                        "МОЖНО ВХОДИТЬ</b>\n\n"
                        f"💠 Монета: "
                        f"<b>{coin}</b>\n"
                        f"📐 Направление: "
                        f"<b>{direction}</b>\n"
                        f"⭐ Score: "
                        f"<b>{score}/100</b>\n\n"
                        f"Entry: "
                        f"<b>{format_price(entry)}</b>\n"
                        f"SL: "
                        f"<b>{format_price(sl)}</b>\n"
                        f"TP: "
                        f"<b>{format_price(tp)}</b>\n\n"
                        "RR: <b>1:2</b>\n\n"
                        "🔥 Полное подтверждение "
                        "получено:\n"
                        "1H → Sweep → 15M → 5M"
                    )

                    await broadcast(
                        application,
                        text
                    )

                await asyncio.sleep(
                    CHECK_INTERVAL
                )

                continue

            # ---------------------------------------------
            # No ready setup
            # ---------------------------------------------

            for coin, result in results.items():

                if result.get("error"):
                    continue

                stage = result.get(
                    "stage"
                )

                sweep = result.get(
                    "sweep"
                )

                direction = result.get(
                    "direction"
                )

                coin_state = state[
                    "coins"
                ].setdefault(
                    coin,
                    {}
                )

                # -----------------------------------------
                # Sweep alert
                # -----------------------------------------

                if sweep:

                    sweep_key = (
                        f"{coin}_"
                        f"{direction}_"
                        f"{sweep.get('price')}"
                    )

                    if (
                        coin_state.get(
                            "last_sweep"
                        )
                        != sweep_key
                    ):

                        coin_state[
                            "last_sweep"
                        ] = sweep_key

                        coin_state[
                            "sweep_alerted"
                        ] = True

                        text = (
                            "🔎 <b>TRADEMIND — "
                            "SWEEP</b>\n\n"
                            f"💠 Монета: "
                            f"<b>{coin}</b>\n"
                            f"Направление: "
                            f"<b>{direction}</b>\n\n"
                            "💧 Крупная ликвидность "
                            "снята.\n\n"
                            "⏳ Ждём подтверждение "
                            "15M.\n\n"
                            "❌ Вход пока запрещён."
                        )

                        await broadcast(
                            application,
                            text
                        )

                # -----------------------------------------
                # 15M confirmation
                # -----------------------------------------

                if stage == "CONFIRMED":

                    confirmation_key = (
                        f"{coin}_"
                        f"{direction}_"
                        f"{result.get('confirmation_time')}"
                    )

                    if (
                        coin_state.get(
                            "last_confirmation"
                        )
                        != confirmation_key
                    ):

                        coin_state[
                            "last_confirmation"
                        ] = confirmation_key

                        text = (
                            "🟡 <b>TRADEMIND — "
                            "15M CONFIRMATION</b>\n\n"
                            f"💠 Монета: "
                            f"<b>{coin}</b>\n"
                            f"Направление: "
                            f"<b>{direction}</b>\n\n"
                            "15M подтверждение "
                            "получено.\n\n"
                            "⏳ Ждём 5M trigger.\n\n"
                            "❌ Вход пока запрещён."
                        )

                        await broadcast(
                            application,
                            text
                        )

            save_state(
                state
            )

        except Exception as e:

            print(
                f"MONITOR ERROR: {e}"
            )

        await asyncio.sleep(
            CHECK_INTERVAL
        )


# =========================================================
# CALLBACKS
# =========================================================

async def callbacks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    data = query.data

    if data == "start":

        text = (
            "🤖 <b>TRADEMIND</b>\n\n"
            "BTC • ETH • SOL\n\n"
            "Выбери действие:"
        )

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )

        return

    if data == "market":

        results = scan_all_coins()

        await query.edit_message_text(
            build_market_message(
                results
            ),
            parse_mode="HTML",
            reply_markup=back_keyboard()
        )

        return

    if data == "levels":

        results = scan_all_coins()

        await query.edit_message_text(
            build_levels_message(
                results
            ),
            parse_mode="HTML",
            reply_markup=back_keyboard()
        )

        return

    if data == "search":

        results = scan_all_coins()

        await query.edit_message_text(
            build_search_message(
                results
            ),
            parse_mode="HTML",
            reply_markup=back_keyboard()
        )

        return

    if data == "sol":

        try:

            result = build_analysis(
                "SOLUSDT"
            )

            await query.edit_message_text(
                build_sol_message(
                    result
                ),
                parse_mode="HTML",
                reply_markup=back_keyboard()
            )

        except Exception as e:

            await query.edit_message_text(
                f"❌ Ошибка SOL:\n{e}",
                reply_markup=back_keyboard()
            )

        return

    if data == "status":

        state = load_state()

        if state.get("active_coin"):

            text = (
                "📊 <b>СТАТУС</b>\n\n"
                f"🟢 Активно: "
                f"<b>{state.get('active_coin')}</b>\n"
                f"Направление: "
                f"<b>{state.get('active_direction')}</b>\n"
                f"Score: "
                f"<b>{state.get('active_score')}/100</b>\n\n"
                f"Entry: "
                f"<b>{format_price(state.get('active_entry'))}</b>\n"
                f"SL: "
                f"<b>{format_price(state.get('active_sl'))}</b>\n"
                f"TP: "
                f"<b>{format_price(state.get('active_tp'))}</b>"
            )

        else:

            text = (
                "📊 <b>СТАТУС</b>\n\n"
                "🟢 Активного сетапа нет.\n\n"
                "Мониторинг:\n"
                "BTC • ETH • SOL"
            )

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=back_keyboard()
        )

        return

    if data == "subscribe":

        await do_subscribe(
            query.message.chat_id,
            query.message
        )

        return

    if data == "unsubscribe":

        await do_unsubscribe(
            query.message.chat_id,
            query.message
        )

        return

    if data == "journal":

        await send_journal(
            query.message
        )

        return


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

async def set_commands(
    application
):

    commands = [

        BotCommand(
            "start",
            "Главное меню"
        ),

        BotCommand(
            "market",
            "Рынок BTC ETH SOL"
        ),

        BotCommand(
            "levels",
            "Ключевые уровни"
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
            "sol",
            "Анализ SOL"
        ),

        BotCommand(
            "journal",
            "Журнал"
        ),
    ]

    await application.bot.set_my_commands(
        commands
    )


# =========================================================
# MAIN
# =========================================================

async def post_init(
    application
):

    await set_commands(
        application
    )

    application.create_task(
        monitor(
            application
        )
    )


def main():

    if not TOKEN:

        raise RuntimeError(
            "BOT_TOKEN не найден."
        )

    application = (
        Application
        .builder()
        .token(TOKEN)
        .post_init(post_init)
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
            "market",
            market_command
        )
    )

    application.add_handler(
        CommandHandler(
            "levels",
            levels_command
        )
    )

    application.add_handler(
        CommandHandler(
            "search",
            search_command
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            status_command
        )
    )

    application.add_handler(
        CommandHandler(
            "subscribe",
            subscribe_command
        )
    )

    application.add_handler(
        CommandHandler(
            "unsubscribe",
            unsubscribe_command
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
            "journal",
            journal_command
        )
    )

    # Buttons

    application.add_handler(
        CallbackQueryHandler(
            callbacks
        )
    )

    print(
        "TradeMind 3.5 started."
    )

    application.run_polling()


if __name__ == "__main__":
    main()