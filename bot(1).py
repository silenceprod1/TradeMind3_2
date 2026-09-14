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

from strategy import analyze

import bingx


# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("BOT_TOKEN")

CHECK_INTERVAL = 15
SCAN_WORKERS = 9

# IMPORTANT:
# OFF    = signals only
# PAPER  = simulated execution
# CONFIRM = requires /execute
# AUTO   = real BingX orders
#
# Keep OFF until everything is tested.
BINGX_MODE = bingx.mode()


COINS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "BNB": "BNBUSDT",
    "XRP": "XRPUSDT",
    "HYPE": "HYPEUSDT",
    "DOGE": "DOGEUSDT",
    "LINK": "LINKUSDT",
    "SUI": "SUIUSDT",
}


# ============================================================
# FILES
# ============================================================

SUBSCRIBERS_FILE = "subscribers.json"
STATE_FILE = "monitor_state.json"
PENDING_FILE = "bingx_pending.json"
JOURNAL_FILE = "trade_journal.json"


# ============================================================
# JSON HELPERS
# ============================================================

def load_json(filename, default):
    try:
        if not os.path.exists(filename):
            return default

        with open(
            filename,
            "r",
            encoding="utf-8",
        ) as f:
            return json.load(f)

    except Exception as exc:
        print(
            f"[JSON LOAD ERROR] "
            f"{filename}: {exc}"
        )
        return default


def save_json(filename, data):
    try:
        tmp = f"{filename}.tmp"

        with open(
            tmp,
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(
            tmp,
            filename,
        )

    except Exception as exc:
        print(
            f"[JSON SAVE ERROR] "
            f"{filename}: {exc}"
        )


# ============================================================
# SUBSCRIBERS
# ============================================================

def load_subscribers():
    data = load_json(
        SUBSCRIBERS_FILE,
        [],
    )

    if not isinstance(data, list):
        return []

    return data


def save_subscribers(data):
    save_json(
        SUBSCRIBERS_FILE,
        data,
    )


# ============================================================
# STATE
# ============================================================

def default_state():
    return {
        # Current active setup
        "active_coin": None,
        "active_symbol": None,
        "active_direction": None,
        "active_setup_key": None,

        "active_entry": None,
        "active_sl": None,
        "active_tp": None,
        "active_rr": None,

        "active_score": None,
        "active_stage": None,

        "active_tp_reason": None,
        "active_sweep_extreme": None,

        # Daily risk control
        "daily_date": None,
        "daily_trades": 0,
        "daily_stop": False,

        # Per-coin alert state
        "coins": {},

        # Last successful READY signal
        "last_ready_key": None,
    }


def load_state():

    state = load_json(
        STATE_FILE,
        default_state(),
    )

    if not isinstance(state, dict):
        state = default_state()

    base = default_state()

    for key, value in base.items():

        if key not in state:
            state[key] = value

    if not isinstance(
        state.get("coins"),
        dict,
    ):
        state["coins"] = {}

    return state


def save_state(state):
    save_json(
        STATE_FILE,
        state,
    )


def reset_daily_state_if_needed(state):

    today = (
        datetime.now(
            timezone.utc
        )
        .date()
        .isoformat()
    )

    if state.get(
        "daily_date"
    ) != today:

        state["daily_date"] = today
        state["daily_trades"] = 0
        state["daily_stop"] = False

        # We do not automatically erase
        # the active setup here.
        #
        # It is safer to preserve it until
        # the monitor decides it is finished.

    return state


# ============================================================
# PENDING BINGX SETUP
# ============================================================

def load_pending():

    data = load_json(
        PENDING_FILE,
        None,
    )

    return data


def save_pending(data):
    save_json(
        PENDING_FILE,
        data,
    )


# ============================================================
# JOURNAL
# ============================================================

def load_journal():

    data = load_json(
        JOURNAL_FILE,
        [],
    )

    if not isinstance(data, list):
        return []

    return data


def save_journal(data):
    save_json(
        JOURNAL_FILE,
        data,
    )


def add_journal_entry(entry):

    journal = load_journal()

    journal.append(entry)

    # Keep the journal manageable.
    journal = journal[-500:]

    save_journal(
        journal
    )


# ============================================================
# FORMATTING
# ============================================================

def format_price(value):

    if value is None:
        return "N/A"

    try:
        value = float(value)

    except Exception:
        return "N/A"

    if value >= 10000:
        return f"${value:,.2f}"

    if value >= 1000:
        return f"${value:,.2f}"

    if value >= 1:
        return f"${value:,.4f}"

    if value >= 0.01:
        return f"${value:,.6f}"

    return f"${value:,.8f}"


def format_rr(value):

    if value is None:
        return "N/A"

    try:
        return f"1:{float(value):.2f}"

    except Exception:
        return "N/A"


def stage_icon(stage):

    return {
        "READY": "🟢",
        "CONFIRMED": "🟡",
        "15M_CONFIRMED": "🟡",
        "SWEPT": "🟠",
        "WAIT": "⏳",
    }.get(
        stage,
        "⚪",
    )


def stage_text(stage):

    return {
        "READY": "МОЖНО ВХОДИТЬ",
        "CONFIRMED": "15M CONFIRMATION",
        "15M_CONFIRMED": "15M CONFIRMATION",
        "SWEPT": "SWEEP",
        "WAIT": "ОЖИДАНИЕ",
    }.get(
        stage,
        "ОЖИДАНИЕ",
    )


def format_levels(
    levels,
    current_price,
):

    if not levels:
        return (
            "💧 Крупные уровни "
            "не найдены."
        )

    try:
        current = float(
            current_price
        )

    except Exception:
        current = 0

    result = []

    for level in levels:

        try:
            price = float(
                level.get("price")
            )

        except Exception:
            continue

        level_type = str(
            level.get(
                "type",
                "",
            )
        )

        if "HIGH" in level_type:
            icon = "🔴"
        else:
            icon = "🟢"

        if current:
            distance = (
                abs(price - current)
                / current
                * 100
            )
        else:
            distance = 0

        result.append(
            f"{icon} "
            f"{level_type}: "
            f"<b>{format_price(price)}</b> "
            f"({distance:.2f}%)"
        )

    if not result:
        return (
            "💧 Крупные уровни "
            "не найдены."
        )

    return "\n".join(
        result
    )


# ============================================================
# KEYBOARDS
# ============================================================

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
            ),
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
                "🟠 BingX",
                callback_data="bingx",
            ),
            InlineKeyboardButton(
                "📒 Журнал",
                callback_data="journal",
            ),
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


# ============================================================
# MARKET ANALYSIS
# ============================================================

def build_analysis(symbol):

    data = get_market_data(
        symbol
    )

    if not data:
        raise RuntimeError(
            f"Нет данных для {symbol}"
        )

    price = data.get(
        "price"
    )

    candles_1h = data.get(
        "candles_1h",
        [],
    )

    candles_15m = data.get(
        "candles_15m",
        [],
    )

    candles_5m = data.get(
        "candles_5m",
        [],
    )

    if not candles_1h:
        raise RuntimeError(
            f"{symbol}: нет 1H свечей"
        )

    if not candles_15m:
        raise RuntimeError(
            f"{symbol}: нет 15M свечей"
        )

    if not candles_5m:
        raise RuntimeError(
            f"{symbol}: нет 5M свечей"
        )

    # IMPORTANT:
    # Only major 1H liquidity is passed
    # to sweep detection.
    major_levels = (
        find_major_liquidity(
            candles_1h,
            price,
            max_levels=6,
        )
    )

    sweep = detect_sweep(
        candles_5m,
        major_levels,
    )

    result = analyze(
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,
        current_price=price,
        major_levels=major_levels,
        sweep=sweep,
    )

    if not isinstance(
        result,
        dict,
    ):
        result = {}

    result.update({
        "symbol": symbol,
        "price": price,
        "major_levels": major_levels,
        "sweep": sweep,
        "candles_1h": candles_1h,
        "candles_15m": candles_15m,
        "candles_5m": candles_5m,
    })

    return result


# ============================================================
# PARALLEL SCANNER
# ============================================================

def scan_one_coin(
    coin,
    symbol,
):

    try:

        result = build_analysis(
            symbol
        )

        return (
            coin,
            result,
        )

    except Exception as exc:

        print(
            f"[SCAN ERROR] "
            f"{coin}: {exc}"
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

                print(
                    f"[FUTURE ERROR] "
                    f"{coin}: {exc}"
                )

                results[coin] = {
                    "error": str(exc),
                    "symbol": COINS[
                        coin
                    ],
                }

    # Keep deterministic order.
    return {
        coin: results[coin]
        for coin in COINS
        if coin in results
    }


async def scan_all_coins_async():

    return await asyncio.to_thread(
        scan_all_coins
    )


# ============================================================
# READY SETUP
# ============================================================

def find_ready_setups(
    results
):

    ready = []

    for coin, result in (
        results.items()
    ):

        if result.get(
            "error"
        ):
            continue

        stage = result.get(
            "stage"
        )

        score = result.get(
            "score",
            0,
        )

        try:
            score = float(
                score
            )
        except Exception:
            score = 0

        if (
            stage == "READY"
            and score >= 80
        ):

            if not result.get(
                "direction"
            ):
                continue

            if result.get(
                "entry"
            ) is None:
                continue

            if result.get(
                "sl"
            ) is None:
                continue

            if result.get(
                "tp"
            ) is None:
                continue

            ready.append(
                (
                    score,
                    coin,
                    result,
                )
            )

    ready.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    return ready


def find_first_ready(
    results
):

    setups = find_ready_setups(
        results
    )

    if not setups:
        return None

    return setups[0]


# ============================================================
# SETUP OBJECT
# ============================================================

def make_setup(
    coin,
    result,
):

    return {
        "coin": coin,
        "symbol": result.get(
            "symbol"
        ),

        "direction": result.get(
            "direction"
        ),

        "entry": result.get(
            "entry"
        ),

        "sl": result.get(
            "sl"
        ),

        "tp": result.get(
            "tp"
        ),

        "rr": result.get(
            "rr"
        ),

        "score": result.get(
            "score"
        ),

        "tp_reason": result.get(
            "tp_reason"
        ),

        "sweep_extreme": result.get(
            "sweep_extreme"
        ),

        "sweep_quality": result.get(
            "sweep_quality"
        ),

        "structure": result.get(
            "structure"
        ),

        "created_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "risk_usdt":
            bingx.RISK_USDT,
    }


def setup_key(
    setup
):

    return (
        f"{setup.get('coin')}_"
        f"{setup.get('direction')}_"
        f"{setup.get('entry')}_"
        f"{setup.get('sl')}_"
        f"{setup.get('tp')}"
    )


# ============================================================
# MESSAGES
# ============================================================

def build_start_message():

    return (
        "🤖 <b>TRADEMIND 4.0</b>\n\n"

        "Мониторинг:\n"
        "BTC • ETH • SOL • BNB • XRP\n"
        "HYPE • DOGE • LINK • SUI\n\n"

        f"⚡ Сканирование: "
        f"<b>{CHECK_INTERVAL} сек.</b>\n\n"

        "Стратегия:\n"
        "<b>"
        "1H → Major Liquidity → "
        "Sweep → 15M → 5M"
        "</b>\n\n"

        "Правила:\n"
        "💧 только крупная ликвидность\n"
        "✅ Sweep\n"
        "✅ 15M confirmation\n"
        "✅ 5M trigger\n"
        "❌ не входить в середине\n"
        "🎯 один TP\n\n"

        f"BingX: <b>{bingx.mode()}</b>"
    )


def build_market_message(
    results
):

    lines = [
        "📊 <b>TRADEMIND — РЫНОК</b>",
        "",
    ]

    for coin in COINS:

        result = results.get(
            coin
        )

        if not result:
            continue

        if result.get(
            "error"
        ):

            lines.extend([
                f"❌ <b>{coin}</b>",
                "Ошибка данных",
                "",
            ])

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

        direction_text = (
            f" • {direction}"
            if direction
            else ""
        )

        lines.extend([
            f"💠 <b>{coin}</b> "
            f"{format_price(price)}",

            f"{stage_icon(stage)} "
            f"{stage_text(stage)}"
            f"{direction_text}",

            f"⭐ Score: "
            f"<b>{score}/100</b>",

            "💧 Major Liquidity:",

            format_levels(
                result.get(
                    "major_levels",
                    [],
                ),
                price,
            ),

            "",
            "────────────",
            "",
        ])

    lines.extend([
        "1H → Liquidity → Sweep → "
        "15M → 5M",

        "",
        "❌ В середине движения "
        "не входим.",
    ])

    return "\n".join(
        lines
    )


def build_levels_message(
    results
):

    lines = [
        "💧 <b>MAJOR LIQUIDITY</b>",
        "",
        "Только крупные 1H уровни.",
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

        lines.extend([
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
        ])

    return "\n".join(
        lines
    )


def build_search_message(
    results
):

    setups = find_ready_setups(
        results
    )

    if setups:

        score, coin, result = (
            setups[0]
        )

        return "\n".join([
            "🚨 <b>НАЙДЕН СЕТАП</b>",
            "",
            f"💠 Монета: "
            f"<b>{coin}</b>",

            f"📐 Направление: "
            f"<b>{result.get('direction')}</b>",

            f"⭐ Score: "
            f"<b>{score:.0f}/100</b>",

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
            "🟢 <b>МОЖНО ВХОДИТЬ</b>",
        ])

    lines = [
        "🔎 <b>ПОИСК СЕТАПА</b>",
        "",
        "❌ Готового входа нет.",
        "",
    ]

    for coin in COINS:

        result = results.get(
            coin
        )

        if not result:
            continue

        if result.get(
            "error"
        ):

            lines.append(
                f"❌ {coin}: ошибка"
            )

            continue

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

        direction_text = (
            f" {direction}"
            if direction
            else ""
        )

        lines.append(
            f"{stage_icon(stage)} "
            f"{coin}{direction_text}: "
            f"{stage_text(stage)} "
            f"({score}/100)"
        )

    lines.extend([
        "",
        "Ждём крупную ликвидность → "
        "Sweep → 15M → 5M.",
    ])

    return "\n".join(
        lines
    )


def build_setup_message(
    coin,
    result,
):

    stage = result.get(
        "stage",
        "WAIT",
    )

    lines = [
        f"📈 <b>TRADEMIND — {coin}</b>",
        "",
        f"💰 Цена: "
        f"<b>{format_price(result.get('price'))}</b>",

        f"{stage_icon(stage)} "
        f"<b>{stage_text(stage)}</b>",

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

    lines.extend([
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
    ])

    if stage in {
        "SWEPT",
        "CONFIRMED",
        "15M_CONFIRMED",
        "READY",
    }:

        lines.extend([
            "",
            "💧 Sweep: "
            "<b>обнаружен</b>",
        ])

    if stage in {
        "CONFIRMED",
        "15M_CONFIRMED",
        "READY",
    }:

        lines.extend([
            "✅ 15M confirmation",
        ])

    if stage == "READY":

        lines.extend([
            "✅ 5M trigger",
            "",
            "🎯 <b>ПЛАН</b>",

            f"Entry: "
            f"<b>{format_price(result.get('entry'))}</b>",

            f"SL: "
            f"<b>{format_price(result.get('sl'))}</b>",

            f"TP: "
            f"<b>{format_price(result.get('tp'))}</b>",

            f"RR: "
            f"<b>{format_rr(result.get('rr'))}</b>",
        ])

        if result.get(
            "tp_reason"
        ):

            lines.append(
                f"🎯 {result.get('tp_reason')}"
            )

        lines.append(
            "\n🟢 <b>МОЖНО ВХОДИТЬ</b>"
        )

    elif stage in {
        "CONFIRMED",
        "15M_CONFIRMED",
    }:

        lines.extend([
            "",
            "⏳ Ждём 5M trigger.",
            "❌ Вход пока запрещён.",
        ])

    elif stage == "SWEPT":

        lines.extend([
            "",
            "⏳ Ждём 15M confirmation.",
            "❌ Вход пока запрещён.",
        ])

    else:

        lines.extend([
            "",
            "⏳ Ждём крупную ликвидность.",
            "❌ Вход в середине запрещён.",
        ])

    return "\n".join(
        lines
    )


# ============================================================
# BINGX
# ============================================================

def build_bingx_message():

    cfg = bingx.config_status()

    mode_text = {
        "OFF":
            "🔴 OFF — только сигналы",

        "PAPER":
            "🟡 PAPER — симуляция",

        "CONFIRM":
            "🟠 CONFIRM — нужен /execute",

        "AUTO":
            "🟢 AUTO — реальные ордера",
    }.get(
        cfg.get("mode"),
        cfg.get("mode"),
    )

    configured = (
        "✅ API настроен"
        if cfg.get("configured")
        else
        "❌ API ключи не настроены"
    )

    return "\n".join([
        "🟠 <b>TRADEMIND — BINGX</b>",
        "",
        f"Режим: <b>{mode_text}</b>",
        configured,
        "",
        f"Leverage: "
        f"<b>{cfg.get('leverage')}x</b>",

        f"Risk: "
        f"<b>${cfg.get('risk_usdt'):.2f}</b>",

        f"Working type: "
        f"<b>{cfg.get('working_type')}</b>",

        "",
        "/balance — баланс",
        "/position — позиции",
        "/execute — исполнить pending",
        "/close CONFIRM — закрыть",
        "",
        "⚠️ AUTO пока не включаем.",
    ])


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        build_start_message(),
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


async def market_command(
    update,
    context,
):

    try:

        results = (
            await scan_all_coins_async()
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
    update,
    context,
):

    try:

        results = (
            await scan_all_coins_async()
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
    update,
    context,
):

    try:

        results = (
            await scan_all_coins_async()
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
    update,
    context,
):

    try:

        result = await asyncio.to_thread(
            build_analysis,
            "SOLUSDT",
        )

        await update.message.reply_text(
            build_setup_message(
                "SOL",
                result,
            ),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

    except Exception as exc:

        await update.message.reply_text(
            f"❌ Ошибка SOL:\n{exc}"
        )


async def status_command(
    update,
    context,
):

    state = load_state()

    active = (
        state.get("active_coin")
        or "нет"
    )

    direction = (
        state.get(
            "active_direction"
        )
        or "—"
    )

    score = (
        state.get(
            "active_score"
        )
        or "—"
    )

    daily = state.get(
        "daily_trades",
        0,
    )

    daily_stop = (
        "🔴 STOP"
        if state.get(
            "daily_stop"
        )
        else "🟢 ACTIVE"
    )

    text = "\n".join([
        "📊 <b>TRADEMIND — СТАТУС</b>",
        "",
        f"Active: <b>{active}</b>",
        f"Direction: <b>{direction}</b>",
        f"Score: <b>{score}</b>",
        "",
        f"Daily trades: "
        f"<b>{daily}/2</b>",

        f"Daily risk: "
        f"<b>{daily_stop}</b>",

        "",
        f"Scan: "
        f"<b>{CHECK_INTERVAL}s</b>",

        f"BingX: "
        f"<b>{bingx.mode()}</b>",
    ])

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


async def subscribe_command(
    update,
    context,
):

    subscribers = load_subscribers()

    chat_id = (
        update.effective_chat.id
    )

    if chat_id not in subscribers:

        subscribers.append(
            chat_id
        )

        save_subscribers(
            subscribers
        )

        text = (
            "🔔 <b>Уведомления включены.</b>"
        )

    else:

        text = (
            "🔔 Уведомления уже включены."
        )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


async def unsubscribe_command(
    update,
    context,
):

    subscribers = load_subscribers()

    chat_id = (
        update.effective_chat.id
    )

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

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


# ============================================================
# BINGX COMMANDS
# ============================================================

async def bingx_command(
    update,
    context,
):

    await update.message.reply_text(
        build_bingx_message(),
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


async def balance_command(
    update,
    context,
):

    try:

        data = await asyncio.to_thread(
            bingx.get_balance
        )

        if not data:

            text = (
                "❌ Баланс не получен."
            )

        else:

            text = "\n".join([
                "💰 <b>BingX Futures</b>",
                "",

                f"Balance: "
                f"<b>{data.get('balance', 'N/A')}</b> USDT",

                f"Equity: "
                f"<b>{data.get('equity', 'N/A')}</b> USDT",

                f"Available: "
                f"<b>{data.get('availableMargin', 'N/A')}</b> USDT",

                f"UPL: "
                f"<b>{data.get('unrealizedProfit', 'N/A')}</b> USDT",
            ])

        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

    except Exception as exc:

        await update.message.reply_text(
            f"❌ BingX balance error:\n{exc}"
        )


async def position_command(
    update,
    context,
):

    try:

        positions = (
            await asyncio.to_thread(
                bingx.get_positions
            )
        )

        active = []

        for position in (
            positions or []
        ):

            try:

                amount = abs(
                    float(
                        position.get(
                            "positionAmt",
                            0,
                        )
                    )
                )

            except Exception:

                amount = 0

            if amount <= 0:
                continue

            active.append(
                position
            )

        if not active:

            text = (
                "📭 <b>BingX</b>\n\n"
                "Открытых позиций нет."
            )

        else:

            lines = [
                "📌 <b>BINGX — ПОЗИЦИИ</b>",
                "",
            ]

            for p in active:

                lines.extend([
                    f"💠 <b>{p.get('symbol')}</b>",
                    f"Side: <b>{p.get('positionSide')}</b>",
                    f"Qty: <b>{p.get('positionAmt')}</b>",
                    f"Entry: <b>{p.get('avgPrice')}</b>",
                    f"PnL: <b>{p.get('unrealizedProfit')}</b>",
                    f"Liquidation: <b>{p.get('liquidationPrice')}</b>",
                    "",
                ])

            text = "\n".join(
                lines
            )

        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

    except Exception as exc:

        await update.message.reply_text(
            f"❌ BingX position error:\n{exc}"
        )


async def execute_command(
    update,
    context,
):

    if bingx.mode() != "CONFIRM":

        await update.message.reply_text(
            "❌ /execute работает "
            "только при BINGX_MODE=CONFIRM."
        )

        return

    pending = load_pending()

    if not pending:

        await update.message.reply_text(
            "📭 Pending setup отсутствует."
        )

        return

    try:

        data = await asyncio.to_thread(
            bingx.execute_confirmed,
            pending,
        )

        save_pending(
            None
        )

        state = load_state()

        state["daily_trades"] = (
            state.get(
                "daily_trades",
                0,
            )
            + 1
        )

        state["active_coin"] = (
            pending.get("coin")
        )

        state["active_symbol"] = (
            pending.get("symbol")
        )

        state["active_direction"] = (
            pending.get("direction")
        )

        state["active_entry"] = (
            pending.get("entry")
        )

        state["active_sl"] = (
            pending.get("sl")
        )

        state["active_tp"] = (
            pending.get("tp")
        )

        state["active_rr"] = (
            pending.get("rr")
        )

        state["active_score"] = (
            pending.get("score")
        )

        state["active_stage"] = (
            "READY"
        )

        state["active_setup_key"] = (
            setup_key(pending)
        )

        save_state(
            state
        )

        add_journal_entry({
            "created_at":
                datetime.now(
                    timezone.utc
                ).isoformat(),

            "type":
                "BINGX_EXECUTED",

            "coin":
                pending.get("coin"),

            "symbol":
                pending.get("symbol"),

            "direction":
                pending.get("direction"),

            "entry":
                pending.get("entry"),

            "sl":
                pending.get("sl"),

            "tp":
                pending.get("tp"),

            "rr":
                pending.get("rr"),

            "score":
                pending.get("score"),

            "risk_usdt":
                pending.get(
                    "risk_usdt"
                ),
        })

        await update.message.reply_text(
            "🟢 <b>BINGX ORDER ОТПРАВЛЕН</b>\n\n"
            f"💠 Coin: "
            f"<b>{pending.get('coin')}</b>\n"

            f"📐 Direction: "
            f"<b>{pending.get('direction')}</b>\n"

            f"Entry: "
            f"<b>{format_price(pending.get('entry'))}</b>\n"

            f"SL: "
            f"<b>{format_price(pending.get('sl'))}</b>\n"

            f"TP: "
            f"<b>{format_price(pending.get('tp'))}</b>\n"

            f"RR: "
            f"<b>{format_rr(pending.get('rr'))}</b>\n\n"

            f"API: "
            f"<code>{str(data)[:1000]}</code>",

            parse_mode="HTML",
        )

    except Exception as exc:

        await update.message.reply_text(
            f"❌ BingX execute error:\n{exc}"
        )


async def close_command(
    update,
    context,
):

    confirmed = (
        bool(context.args)
        and
        context.args[0].upper()
        == "CONFIRM"
    )

    if not confirmed:

        await update.message.reply_text(
            "⚠️ Команда закрывает "
            "позиции.\n\n"
            "Для подтверждения:\n"
            "<code>/close CONFIRM</code>",
            parse_mode="HTML",
        )

        return

    try:

        symbol = (
            context.args[1]
            if len(context.args) > 1
            else None
        )

        data = await asyncio.to_thread(
            bingx.close_position,
            symbol,
        )

        state = load_state()

        state["active_coin"] = None
        state["active_symbol"] = None
        state["active_direction"] = None
        state["active_setup_key"] = None
        state["active_entry"] = None
        state["active_sl"] = None
        state["active_tp"] = None
        state["active_rr"] = None
        state["active_score"] = None
        state["active_stage"] = None

        save_state(
            state
        )

        await update.message.reply_text(
            "🟢 <b>BINGX CLOSE ОТПРАВЛЕН</b>\n\n"
            f"<code>{str(data)[:1500]}</code>",
            parse_mode="HTML",
        )

    except Exception as exc:

        await update.message.reply_text(
            f"❌ BingX close error:\n{exc}"
        )


# ============================================================
# JOURNAL COMMAND
# ============================================================

async def journal_command(
    update,
    context,
):

    journal = load_journal()

    if not journal:

        await update.message.reply_text(
            "📒 <b>ЖУРНАЛ</b>\n\n"
            "Сделок пока нет.",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

        return

    recent = journal[-10:]

    lines = [
        "📒 <b>TRADEMIND — ЖУРНАЛ</b>",
        "",
        f"Записей: <b>{len(journal)}</b>",
        "",
    ]

    for item in reversed(
        recent
    ):

        coin = item.get(
            "coin",
            "?"
        )

        direction = item.get(
            "direction",
            "?"
        )

        score = item.get(
            "score",
            "?"
        )

        entry_type = item.get(
            "type",
            "?"
        )

        lines.append(
            f"• <b>{coin}</b> "
            f"{direction} "
            f"Score {score} "
            f"— {entry_type}"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


# ============================================================
# BROADCAST
# ============================================================

async def broadcast(
    application,
    text,
):

    subscribers = (
        load_subscribers()
    )

    if not subscribers:
        return

    async def send_one(
        chat_id
    ):

        try:

            await application.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
            )

        except Exception as exc:

            print(
                f"[BROADCAST ERROR] "
                f"{chat_id}: {exc}"
            )

    await asyncio.gather(
        *(
            send_one(chat_id)
            for chat_id in subscribers
        ),
        return_exceptions=True,
    )


# ============================================================
# READY ALERT
# ============================================================

def build_ready_alert(
    setup
):

    text = "\n".join([
        "🚨 <b>TRADEMIND — МОЖНО ВХОДИТЬ</b>",
        "",

        f"💠 Монета: "
        f"<b>{setup.get('coin')}</b>",

        f"📐 Направление: "
        f"<b>{setup.get('direction')}</b>",

        f"⭐ Score: "
        f"<b>{setup.get('score')}/100</b>",

        "",
        f"Entry: "
        f"<b>{format_price(setup.get('entry'))}</b>",

        f"SL: "
        f"<b>{format_price(setup.get('sl'))}</b>",

        f"TP: "
        f"<b>{format_price(setup.get('tp'))}</b>",

        f"RR: "
        f"<b>{format_rr(setup.get('rr'))}</b>",
    ])

    if setup.get(
        "tp_reason"
    ):

        text += (
            "\n\n🎯 "
            + str(
                setup.get(
                    "tp_reason"
                )
            )
        )

    text += (
        "\n\n"
        "✅ Major Liquidity\n"
        "✅ Sweep\n"
        "✅ 15M confirmation\n"
        "✅ 5M trigger"
    )

    return text


# ============================================================
# MONITOR
# ============================================================

async def monitor(
    application
):

    print(
        "TradeMind 4.0 monitor started."
    )

    print(
        f"Scan interval: "
        f"{CHECK_INTERVAL}s"
    )

    print(
        f"Workers: "
        f"{SCAN_WORKERS}"
    )

    print(
        "Coins: "
        + ", ".join(
            COINS.keys()
        )
    )

    print(
        "BingX mode: "
        + bingx.mode()
    )

    while True:

        started = (
            asyncio.get_running_loop()
            .time()
        )

        try:

            state = load_state()

            state = (
                reset_daily_state_if_needed(
                    state
                )
            )

            results = (
                await scan_all_coins_async()
            )

            # ------------------------------------------------
            # EARLY STAGE ALERTS
            # ------------------------------------------------

            for coin, result in (
                results.items()
            ):

                if result.get(
                    "error"
                ):
                    continue

                coin_state = (
                    state["coins"]
                    .setdefault(
                        coin,
                        {}
                    )
                )

                direction = result.get(
                    "direction"
                )

                sweep = result.get(
                    "sweep"
                )

                stage = result.get(
                    "stage"
                )

                # --------------------------------------------
                # SWEEP
                # --------------------------------------------

                if sweep:

                    sweep_price = (
                        sweep.get("price")
                        or
                        sweep.get("level")
                        or
                        sweep.get("close")
                    )

                    sweep_time = (
                        sweep.get(
                            "open_time"
                        )
                        or
                        sweep.get(
                            "time"
                        )
                    )

                    sweep_key = (
                        f"{coin}|"
                        f"{direction}|"
                        f"{sweep_price}|"
                        f"{sweep_time}"
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

                        await broadcast(
                            application,

                            "\n".join([
                                "🔎 <b>"
                                "TRADEMIND — SWEEP"
                                "</b>",
                                "",

                                f"💠 "
                                f"<b>{coin}</b>",

                                f"📐 "
                                f"<b>{direction}</b>",

                                "",
                                "💧 Крупная "
                                "ликвидность снята.",

                                "⏳ Ждём "
                                "15M confirmation.",

                                "❌ Вход пока запрещён.",
                            ])
                        )

                # --------------------------------------------
                # 15M CONFIRMATION
                # --------------------------------------------

                if stage in {
                    "CONFIRMED",
                    "15M_CONFIRMED",
                }:

                    confirmation_time = (
                        result.get(
                            "confirmation_15m_time"
                        )
                        or
                        result.get(
                            "confirmation_time"
                        )
                    )

                    confirmation_key = (
                        f"{coin}|"
                        f"{direction}|"
                        f"{confirmation_time}"
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

                        await broadcast(
                            application,

                            "\n".join([
                                "🟡 <b>"
                                "TRADEMIND — "
                                "15M CONFIRMATION"
                                "</b>",
                                "",

                                f"💠 "
                                f"<b>{coin}</b>",

                                f"📐 "
                                f"<b>{direction}</b>",

                                "",
                                "✅ Sweep",
                                "✅ 15M confirmation",
                                "⏳ Ждём 5M trigger.",
                                "❌ Вход пока запрещён.",
                            ])
                        )

            # ------------------------------------------------
            # READY SETUP
            # ------------------------------------------------

            # Do not open another setup if:
            # 1. Daily stop activated.
            # 2. Two trades already happened.
            # 3. Another active setup exists.

            can_find_new = (
                not state.get(
                    "daily_stop"
                )
                and
                state.get(
                    "daily_trades",
                    0,
                ) < 2
                and
                not state.get(
                    "active_coin"
                )
            )

            if can_find_new:

                ready = (
                    find_first_ready(
                        results
                    )
                )

                if ready:

                    score, coin, result = (
                        ready
                    )

                    setup = make_setup(
                        coin,
                        result
                    )

                    current_key = (
                        setup_key(
                            setup
                        )
                    )

                    if (
                        state.get(
                            "last_ready_key"
                        )
                        != current_key
                    ):

                        # ------------------------------------
                        # BINGX EXECUTION MODE
                        # ------------------------------------

                        execution = (
                            await asyncio.to_thread(
                                bingx.open_trade,
                                setup,
                            )
                        )

                        execution_status = (
                            execution.get(
                                "status"
                            )
                        )

                        mode = bingx.mode()

                        accepted = (
                            execution_status
                            in {
                                "submitted",
                                "simulated",
                                "pending_confirmation",
                                "disabled",
                            }
                        )

                        if accepted:

                            state[
                                "last_ready_key"
                            ] = current_key

                            state.update({
                                "active_coin":
                                    coin,

                                "active_symbol":
                                    setup.get(
                                        "symbol"
                                    ),

                                "active_direction":
                                    setup.get(
                                        "direction"
                                    ),

                                "active_setup_key":
                                    current_key,

                                "active_entry":
                                    setup.get(
                                        "entry"
                                    ),

                                "active_sl":
                                    setup.get(
                                        "sl"
                                    ),

                                "active_tp":
                                    setup.get(
                                        "tp"
                                    ),

                                "active_rr":
                                    setup.get(
                                        "rr"
                                    ),

                                "active_score":
                                    setup.get(
                                        "score"
                                    ),

                                "active_stage":
                                    "READY",

                                "active_tp_reason":
                                    setup.get(
                                        "tp_reason"
                                    ),

                                "active_sweep_extreme":
                                    setup.get(
                                        "sweep_extreme"
                                    ),

                            })

                            # --------------------------------
                            # PAPER / AUTO count as trade
                            # --------------------------------

                            if mode in {
                                "PAPER",
                                "AUTO",
                            }:

                                state[
                                    "daily_trades"
                                ] = (
                                    state.get(
                                        "daily_trades",
                                        0,
                                    )
                                    + 1
                                )

                            # --------------------------------
                            # CONFIRM stores pending
                            # --------------------------------

                            if mode == "CONFIRM":

                                save_pending(
                                    setup
                                )

                            # --------------------------------
                            # Journal
                            # --------------------------------

                            add_journal_entry({
                                "created_at":
                                    datetime.now(
                                        timezone.utc
                                    ).isoformat(),

                                "type":
                                    "SIGNAL",

                                "mode":
                                    mode,

                                "coin":
                                    setup.get(
                                        "coin"
                                    ),

                                "symbol":
                                    setup.get(
                                        "symbol"
                                    ),

                                "direction":
                                    setup.get(
                                        "direction"
                                    ),

                                "entry":
                                    setup.get(
                                        "entry"
                                    ),

                                "sl":
                                    setup.get(
                                        "sl"
                                    ),

                                "tp":
                                    setup.get(
                                        "tp"
                                    ),

                                "rr":
                                    setup.get(
                                        "rr"
                                    ),

                                "score":
                                    setup.get(
                                        "score"
                                    ),

                                "risk_usdt":
                                    setup.get(
                                        "risk_usdt"
                                    ),

                                "status":
                                    execution_status,
                            })

                            save_state(
                                state
                            )

                            mode_line = {
                                "OFF":
                                    "⚪ OFF — только сигнал",

                                "PAPER":
                                    "🟡 PAPER — симуляция",

                                "CONFIRM":
                                    "🟠 CONFIRM — "
                                    "используй /execute",

                                "AUTO":
                                    "🟢 AUTO — "
                                    "ордер отправлен",
                            }.get(
                                mode,
                                mode,
                            )

                            alert = (
                                build_ready_alert(
                                    setup
                                )
                                + "\n\n"
                                + mode_line
                            )

                            await broadcast(
                                application,
                                alert,
                            )

            save_state(
                state
            )

        except Exception as exc:

            print(
                f"[MONITOR ERROR] "
                f"{exc}"
            )

        elapsed = (
            asyncio.get_running_loop()
            .time()
            - started
        )

        sleep_for = max(
            1,
            CHECK_INTERVAL
            - elapsed,
        )

        await asyncio.sleep(
            sleep_for
        )


# ============================================================
# CALLBACKS
# ============================================================

async def callbacks(
    update,
    context,
):

    query = (
        update.callback_query
    )

    await query.answer()

    data = query.data

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    if data == "start":

        await query.edit_message_text(
            build_start_message(),
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )

        return

    # --------------------------------------------------------
    # MARKET
    # --------------------------------------------------------

    if data == "market":

        try:

            results = (
                await scan_all_coins_async()
            )

            await query.edit_message_text(
                build_market_message(
                    results
                ),
                parse_mode="HTML",
                reply_markup=back_keyboard(),
            )

        except Exception as exc:

            await query.edit_message_text(
                f"❌ Ошибка:\n{exc}"
            )

        return

    # --------------------------------------------------------
    # LEVELS
    # --------------------------------------------------------

    if data == "levels":

        try:

            results = (
                await scan_all_coins_async()
            )

            await query.edit_message_text(
                build_levels_message(
                    results
                ),
                parse_mode="HTML",
                reply_markup=back_keyboard(),
            )

        except Exception as exc:

            await query.edit_message_text(
                f"❌ Ошибка:\n{exc}"
            )

        return

    # --------------------------------------------------------
    # SEARCH
    # --------------------------------------------------------

    if data == "search":

        try:

            results = (
                await scan_all_coins_async()
            )

            await query.edit_message_text(
                build_search_message(
                    results
                ),
                parse_mode="HTML",
                reply_markup=back_keyboard(),
            )

        except Exception as exc:

            await query.edit_message_text(
                f"❌ Ошибка:\n{exc}"
            )

        return

    # --------------------------------------------------------
    # SOL
    # --------------------------------------------------------

    if data == "sol":

        try:

            result = await asyncio.to_thread(
                build_analysis,
                "SOLUSDT",
            )

            await query.edit_message_text(
                build_setup_message(
                    "SOL",
                    result,
                ),
                parse_mode="HTML",
                reply_markup=back_keyboard(),
            )

        except Exception as exc:

            await query.edit_message_text(
                f"❌ Ошибка SOL:\n{exc}",
                reply_markup=back_keyboard(),
            )

        return

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    if data == "status":

        state = load_state()

        text = "\n".join([
            "📊 <b>СТАТУС</b>",
            "",

            f"Active: "
            f"<b>{state.get('active_coin') or 'нет'}</b>",

            f"Direction: "
            f"<b>{state.get('active_direction') or '—'}</b>",

            f"Stage: "
            f"<b>{state.get('active_stage') or '—'}</b>",

            f"Score: "
            f"<b>{state.get('active_score') or '—'}</b>",

            "",
            f"Entry: "
            f"<b>{format_price(state.get('active_entry'))}</b>",

            f"SL: "
            f"<b>{format_price(state.get('active_sl'))}</b>",

            f"TP: "
            f"<b>{format_price(state.get('active_tp'))}</b>",

            f"RR: "
            f"<b>{format_rr(state.get('active_rr'))}</b>",

            "",
            f"Daily: "
            f"<b>{state.get('daily_trades', 0)}/2</b>",

            f"BingX: "
            f"<b>{bingx.mode()}</b>",
        ])

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

        return

    # --------------------------------------------------------
    # BINGX
    # --------------------------------------------------------

    if data == "bingx":

        await query.edit_message_text(
            build_bingx_message(),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

        return

    # --------------------------------------------------------
    # SUBSCRIBE
    # --------------------------------------------------------

    if data == "subscribe":

        subscribers = (
            load_subscribers()
        )

        chat_id = (
            query.message.chat_id
        )

        if chat_id not in subscribers:

            subscribers.append(
                chat_id
            )

            save_subscribers(
                subscribers
            )

        await query.edit_message_text(
            "🔔 <b>Уведомления включены.</b>",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

        return

    # --------------------------------------------------------
    # UNSUBSCRIBE
    # --------------------------------------------------------

    if data == "unsubscribe":

        subscribers = (
            load_subscribers()
        )

        chat_id = (
            query.message.chat_id
        )

        if chat_id in subscribers:

            subscribers.remove(
                chat_id
            )

            save_subscribers(
                subscribers
            )

        await query.edit_message_text(
            "🔕 <b>Уведомления выключены.</b>",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

        return

    # --------------------------------------------------------
    # JOURNAL
    # --------------------------------------------------------

    if data == "journal":

        journal = load_journal()

        await query.edit_message_text(
            "\n".join([
                "📒 <b>ЖУРНАЛ</b>",
                "",
                f"Всего записей: "
                f"<b>{len(journal)}</b>",
                "",
                "Используй /journal "
                "для последних записей.",
            ]),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

        return


# ============================================================
# TELEGRAM COMMAND SET
# ============================================================

async def set_commands(
    application
):

    commands = [

        CommandHandler(
            "start",
            start,
        ),

    ]

    # Telegram BotCommand objects
    from telegram import BotCommand

    bot_commands = [

        BotCommand(
            "start",
            "Главное меню",
        ),

        BotCommand(
            "market",
            "Рынок 9 монет",
        ),

        BotCommand(
            "levels",
            "Крупные уровни",
        ),

        BotCommand(
            "search",
            "Поиск сетапа",
        ),

        BotCommand(
            "sol",
            "Анализ SOL",
        ),

        BotCommand(
            "status",
            "Статус",
        ),

        BotCommand(
            "bingx",
            "BingX статус",
        ),

        BotCommand(
            "balance",
            "BingX баланс",
        ),

        BotCommand(
            "position",
            "BingX позиции",
        ),

        BotCommand(
            "execute",
            "Исполнить pending",
        ),

        BotCommand(
            "close",
            "Закрыть позиции",
        ),

        BotCommand(
            "subscribe",
            "Включить уведомления",
        ),

        BotCommand(
            "unsubscribe",
            "Выключить уведомления",
        ),

        BotCommand(
            "journal",
            "Торговый журнал",
        ),
    ]

    await application.bot.set_my_commands(
        bot_commands
    )


# ============================================================
# POST INIT
# ============================================================

async def post_init(
    application
):

    await set_commands(
        application
    )

    application.create_task(
        monitor(
            application
        ),
        name="trademind_monitor",
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not TOKEN:

        raise RuntimeError(
            "BOT_TOKEN не найден."
        )

    print(
        "================================"
    )

    print(
        "TradeMind 4.0"
    )

    print(
        "================================"
    )

    print(
        f"Scan interval: "
        f"{CHECK_INTERVAL}s"
    )

    print(
        f"Parallel workers: "
        f"{SCAN_WORKERS}"
    )

    print(
        "Coins: "
        + ", ".join(
            COINS.keys()
        )
    )

    print(
        "BingX mode: "
        + bingx.mode()
    )

    print(
        "================================"
    )

    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    # --------------------------------------------------------
    # Commands
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start,
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
            "status",
            status_command,
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

    # --------------------------------------------------------
    # BingX
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "bingx",
            bingx_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "balance",
            balance_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "position",
            position_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "execute",
            execute_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "close",
            close_command,
        )
    )

    # --------------------------------------------------------
    # Journal
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "journal",
            journal_command,
        )
    )

    # --------------------------------------------------------
    # Buttons
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            callbacks
        )
    )

    # --------------------------------------------------------
    # Start
    # --------------------------------------------------------

    application.run_polling()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()