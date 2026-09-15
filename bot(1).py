"""
TradeMind 5.8.1 — Telegram Bot

Market:
Binance Spot

Execution:
BingX optional / OFF by default

Strategy:
D1/W1 -> 1H -> Major Liquidity -> Sweep
-> 15M -> 5M ILM -> Entry -> SL -> exact 1:2 TP

Interface:
Dashboard
Radar
Active Setup
Why WAIT
Major Liquidity
Journal

Background monitoring:
asyncio loop
No JobQueue required.

IMPORTANT:
The strategy itself is NOT changed here.
This file only handles the bot, monitoring and interface.
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

# One signal / trade per day
MAX_TRADES_PER_DAY = 1


# =========================================================
# COINS
# =========================================================

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


# =========================================================
# FILES
# =========================================================

SUBSCRIBERS_FILE = "subscribers.json"

STATE_FILE = "monitor_state.json"

JOURNAL_FILE = "trade_journal.json"

RESULTS_CACHE_FILE = "trademind_results_cache.json"


# =========================================================
# JSON HELPERS
# =========================================================

def load_json(filename, default):

    try:

        if not os.path.exists(filename):
            return default

        with open(
            filename,
            "r",
            encoding="utf-8",
        ) as file:

            return json.load(file)

    except Exception as exc:

        print(
            f"JSON LOAD ERROR {filename}: {exc}"
        )

        return default


def save_json(filename, data):

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
            f"JSON SAVE ERROR {filename}: {exc}"
        )


# =========================================================
# RESULTS CACHE
# =========================================================

def load_results_cache():

    return load_json(
        RESULTS_CACHE_FILE,
        {
            "updated_at": None,
            "results": {},
        },
    )


def save_results_cache(results):

    save_json(
        RESULTS_CACHE_FILE,
        {
            "updated_at": datetime.now(
                timezone.utc
            ).isoformat(),

            "results": results,
        },
    )


def get_cached_results():

    cache = load_results_cache()

    results = cache.get(
        "results",
        {},
    )

    updated_at = cache.get(
        "updated_at"
    )

    if not isinstance(results, dict):
        results = {}

    return results, updated_at


def cache_age_text(updated_at):

    if not updated_at:
        return "нет данных"

    try:

        dt = datetime.fromisoformat(
            updated_at
        )

        now = datetime.now(
            timezone.utc
        )

        seconds = max(
            0,
            int(
                (
                    now - dt
                ).total_seconds()
            ),
        )

        if seconds < 60:

            return (
                f"{seconds} сек. назад"
            )

        minutes = seconds // 60

        if minutes < 60:

            return (
                f"{minutes} мин. назад"
            )

        hours = minutes // 60

        return (
            f"{hours} ч. назад"
        )

    except Exception:

        return "неизвестно"


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

        "last_ready_keys": {},

        "subscribers": [],

    }


def load_state():

    state = load_json(
        STATE_FILE,
        default_state(),
    )

    if not isinstance(state, dict):

        state = default_state()

    state.setdefault(
        "daily_date",
        None,
    )

    state.setdefault(
        "daily_trades",
        0,
    )

    state.setdefault(
        "daily_stop",
        False,
    )

    state.setdefault(
        "last_signal_key",
        None,
    )

    state.setdefault(
        "last_sweep_keys",
        {},
    )

    state.setdefault(
        "last_15m_keys",
        {},
    )

    state.setdefault(
        "last_ready_keys",
        {},
    )

    state.setdefault(
        "subscribers",
        [],
    )

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

        state["last_ready_keys"] = {}

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

    if not isinstance(data, list):

        return []

    return data


def save_subscribers(subscribers):

    save_json(
        SUBSCRIBERS_FILE,
        subscribers,
    )


# =========================================================
# FORMATTING
# =========================================================

def format_price(value):

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

        "5M": "🔵",

        "15M": "🟡",

        "SWEEP": "🟠",

        "1H": "🟣",

        "D1": "⚪",

        "TP": "🔴",

        "WAIT": "⏳",

        "NO_TRADE": "⛔",

    }.get(
        stage,
        "⚪",
    )


def stage_text(stage):

    return {

        "READY": "МОЖНО ВХОДИТЬ",

        "5M": "ЖДЁМ 5M ILM",

        "15M": "ЖДЁМ 15M",

        "SWEEP": "ЖДЁМ SWEEP",

        "1H": "1H НЕ СИНХРОНИЗИРОВАН",

        "D1": "D1 НЕЯСНЫЙ",

        "TP": "TP НЕ ПРОХОДИТ",

        "WAIT": "ОЖИДАНИЕ",

        "NO_TRADE": "НЕТ СДЕЛКИ",

    }.get(
        stage,
        "ОЖИДАНИЕ",
    )


# =========================================================
# STAGE DETECTION
# =========================================================

def get_result_stage(result):

    if not result:

        return "NO_TRADE"

    if result.get("error"):

        return "NO_TRADE"

    stage = result.get("stage")

    if stage:

        return stage

    if (

        result.get("entry") is not None

        and result.get("sl") is not None

        and result.get("tp") is not None

    ):

        return "READY"

    if result.get(
        "confirmation"
    ):

        return "5M"

    if result.get(
        "confirmation_15m"
    ):

        return "15M"

    if result.get(
        "sweep"
    ):

        return "SWEEP"

    if result.get(
        "direction"
    ):

        return "1H"

    return "WAIT"


# =========================================================
# LIQUIDITY
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

            label = (
                "BSL / SHORT SWEEP"
            )

        else:

            icon = "🟢"

            label = (
                "SSL / LONG SWEEP"
            )

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

def build_analysis(symbol):

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


    # =====================================================
    # MAJOR LIQUIDITY
    # =====================================================

    major_levels = (
        find_major_liquidity(
            candles_1h,
            price,
            max_levels=6,
        )
    )


    # =====================================================
    # FIRST PASS
    # =====================================================

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


    # =====================================================
    # DIRECTIONAL 1H SWEEP
    # =====================================================

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


    # =====================================================
    # SECOND PASS
    # =====================================================

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

        "major_levels": major_levels,

        "sweep": sweep,

        "candles_5m": candles_5m,

        "candles_15m": candles_15m,

        "candles_1h": candles_1h,

        "candles_d1": candles_d1,

        "candles_w1": candles_w1,

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

                    "error":
                        str(exc),

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

        if result.get(
            "error"
        ):

            continue

        if (

            get_result_stage(
                result
            )
            == "READY"

            and result.get(
                "score",
                0,
            )
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
# DASHBOARD KEYBOARD
# =========================================================

def dashboard_keyboard():

    return InlineKeyboardMarkup([

        [

            InlineKeyboardButton(
                "🔄 Обновить",
                callback_data="refresh",
            ),

            InlineKeyboardButton(
                "🎯 Активный сетап",
                callback_data="active",
            ),

        ],

        [

            InlineKeyboardButton(
                "📡 Радар",
                callback_data="radar",
            ),

            InlineKeyboardButton(
                "❓ Почему WAIT?",
                callback_data="why",
            ),

        ],

        [

            InlineKeyboardButton(
                "💧 Ликвидность",
                callback_data="levels",
            ),

            InlineKeyboardButton(
                "📒 Журнал",
                callback_data="journal",
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
# DASHBOARD
# =========================================================

def build_dashboard_message():

    results, updated_at = (
        get_cached_results()
    )

    state = (
        reset_daily_state_if_needed()
    )

    daily_trades = state.get(
        "daily_trades",
        0,
    )

    if not results:

        return (

            "🧠 <b>TRADEMIND 5.8.1</b>\n\n"

            "📡 Monitor: <b>ONLINE</b>\n"

            "📊 Market: <b>Binance Spot</b>\n\n"

            "⏳ Данные ещё собираются.\n\n"

            "Нажми 🔄 <b>Обновить</b>, "
            "чтобы получить первый снимок рынка."

        )


    lines = [

        "🧠 <b>TRADEMIND 5.8.1</b>",

        "",

        "📡 Monitor: <b>ONLINE</b>",

        "📊 Market: <b>Binance Spot</b>",

        f"🕐 Обновлено: "
        f"<b>{cache_age_text(updated_at)}</b>",

        f"🎯 Сделки сегодня: "
        f"<b>{daily_trades}/1</b>",

        "",

        "━━━━━━━━━━━━━━",

        "📡 <b>TRADE RADAR</b>",

        "━━━━━━━━━━━━━━",

    ]


    best = None


    for coin in COINS:

        result = results.get(
            coin,
            {},
        )

        if result.get(
            "error"
        ):

            lines.append(

                f"❌ <b>{coin}</b> — ERROR"

            )

            continue


        stage = get_result_stage(
            result
        )

        score = result.get(
            "score",
            0,
        )

        direction = result.get(
            "direction"
        )

        direction_text = (

            f" · {direction}"

            if direction

            else ""

        )


        lines.append(

            f"{stage_icon(stage)} "
            f"<b>{coin}</b> — "
            f"{stage_text(stage)}"
            f"{direction_text} · "
            f"{score}/100"

        )


        if stage == "READY":

            if (

                best is None

                or score > best[0]

            ):

                best = (

                    score,

                    coin,

                    result,

                )


    lines += [

        "",

        "━━━━━━━━━━━━━━",

    ]


    if best:

        score, coin, result = best

        lines += [

            "🎯 <b>BEST SETUP</b>",

            "",

            f"💠 <b>{coin}</b>",

            f"📐 Direction: "
            f"<b>{result.get('direction')}</b>",

            f"⭐ Score: "
            f"<b>{score}/100</b>",

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

        ]

    else:

        lines += [

            "🎯 <b>ГОТОВОГО СЕТАПА НЕТ</b>",

            "",

            "⏳ Ждём:",

            "D1 → 1H → Major Liquidity",

            "→ Sweep → 15M → 5M ILM",

            "",

            "❌ Нет полного подтверждения",

            "→ <b>нет входа.</b>",

        ]


    return "\n".join(lines)


# =========================================================
# ACTIVE SETUP
# =========================================================

def build_active_setup_message():

    results, updated_at = (
        get_cached_results()
    )

    if not results:

        return (

            "🎯 <b>АКТИВНЫЙ СЕТАП</b>\n\n"

            "⏳ Данных пока нет.\n"

            "Нажми 🔄 Обновить."

        )


    candidates = []


    stage_priority = {

        "READY": 6,

        "5M": 5,

        "15M": 4,

        "SWEEP": 3,

        "1H": 2,

        "D1": 1,

        "WAIT": 0,

        "NO_TRADE": 0,

    }


    for coin, result in results.items():

        if result.get(
            "error"
        ):

            continue

        stage = get_result_stage(
            result
        )

        candidates.append(

            (

                stage_priority.get(
                    stage,
                    0,
                ),

                result.get(
                    "score",
                    0,
                ),

                coin,

                result,

            )

        )


    if not candidates:

        return (

            "🎯 <b>АКТИВНЫЙ СЕТАП</b>\n\n"

            "⏳ Активных сетапов нет."

        )


    candidates.sort(
        reverse=True
    )


    _, _, coin, result = (
        candidates[0]
    )


    stage = get_result_stage(
        result
    )

    direction = (
        result.get(
            "direction"
        )
        or "—"
    )


    lines = [

        f"🎯 <b>АКТИВНЫЙ СЕТАП — {coin}</b>",

        "",

        f"📐 Direction: "
        f"<b>{direction}</b>",

        f"⭐ Score: "
        f"<b>{result.get('score', 0)}/100</b>",

        f"📡 Данные: "
        f"<b>{cache_age_text(updated_at)}</b>",

        "",

        "━━━━━━━━━━━━━━",

        "📍 <b>SETUP PROGRESS</b>",

        "━━━━━━━━━━━━━━",

    ]


    progress = [

        (

            "D1",

            stage in {

                "1H",
                "SWEEP",
                "15M",
                "5M",
                "READY",

            },

        ),

        (

            "1H",

            stage in {

                "SWEEP",
                "15M",
                "5M",
                "READY",

            },

        ),

        (

            "💧 SWEEP",

            stage in {

                "15M",
                "5M",
                "READY",

            },

        ),

        (

            "15M",

            stage in {

                "5M",
                "READY",

            },

        ),

        (

            "5M ILM",

            stage == "READY",

        ),

        (

            "ENTRY",

            stage == "READY",

        ),

    ]


    for name, completed in progress:

        icon = (
            "✅"
            if completed
            else "⏳"
        )

        lines.append(
            f"{icon} {name}"
        )


    lines += [

        "",

        f"📌 Текущий этап: "
        f"<b>{stage_text(stage)}</b>",

    ]


    if stage == "READY":

        lines += [

            "",

            "━━━━━━━━━━━━━━",

            "🎯 <b>ENTRY</b>",

            "━━━━━━━━━━━━━━",

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

        ]

    else:

        lines += [

            "",

            "❌ <b>ВХОД ЗАПРЕЩЁН</b>",

            "Ждём следующее подтверждение.",

        ]


    return "\n".join(lines)


# =========================================================
# WHY WAIT
# =========================================================

def build_why_wait_message():

    results, updated_at = (
        get_cached_results()
    )

    if not results:

        return (

            "❓ <b>ПОЧЕМУ WAIT?</b>\n\n"

            "⏳ Нет данных."

        )


    lines = [

        "❓ <b>ПОЧЕМУ WAIT?</b>",

        "",

        f"📡 Данные: "
        f"<b>{cache_age_text(updated_at)}</b>",

        "",

    ]


    reasons = {

        "D1":
            "D1/W1 не даёт чёткого направления.",

        "1H":
            "1H не синхронизирован с направлением.",

        "SWEEP":
            "Sweep есть. Ждём 15M confirmation.",

        "15M":
            "15M подтверждён. Ждём 5M ILM.",

        "5M":
            "Ждём завершение 5M ILM / вход.",

        "WAIT":
            "Нет полного подтверждённого сетапа.",

        "NO_TRADE":
            "Сделка не соответствует правилам.",

    }


    for coin in COINS:

        result = results.get(
            coin,
            {},
        )

        if result.get(
            "error"
        ):

            continue


        stage = get_result_stage(
            result
        )


        if stage == "READY":

            lines += [

                f"🟢 <b>{coin}</b> — READY",

                "Полное подтверждение получено.",

                "",

            ]

            continue


        reason = reasons.get(

            stage,

            result.get(
                "reason",
                "Нет полного подтверждения.",
            ),

        )


        lines += [

            f"{stage_icon(stage)} "
            f"<b>{coin}</b>",

            f"Этап: "
            f"<b>{stage_text(stage)}</b>",

            f"Причина: {reason}",

            "",

        ]


    lines += [

        "━━━━━━━━━━━━━━",

        "🧠 <b>TradeMind правило</b>",

        "",

        "Нет подтверждения → нет входа.",

        "Не входим в середине движения.",

        "Только крупная ликвидность.",

        "TP только 1:2.",

    ]


    return "\n".join(lines)


# =========================================================
# MARKET MESSAGE
# =========================================================

def build_market_message(
    results,
):

    lines = [

        "📊 <b>TRADEMIND 5.8.1 — РЫНОК</b>",

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

            lines += [

                f"❌ <b>{coin}</b>",

                "Ошибка данных",

                "",

            ]

            continue


        price = result.get(
            "price"
        )

        stage = get_result_stage(
            result
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

            try:

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

                    "💧 Ближайшая major "
                    "liquidity: "
                    f"<b>"
                    f"{format_price(nearest['price'])}"
                    f"</b>"

                )

            except Exception:

                pass


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


# =========================================================
# LEVELS MESSAGE
# =========================================================

def build_levels_message(
    results,
):

    lines = [

        "💧 <b>TRADEMIND 5.8.1 — MAJOR LIQUIDITY</b>",

        "",

        "Только крупные 1H уровни.",

        "Снятая ликвидность не является "
        "свежей целью.",

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


# =========================================================
# SEARCH MESSAGE
# =========================================================

def build_search_message(
    results,
):

    state = (
        reset_daily_state_if_needed()
    )


    if (

        state.get(
            "daily_stop"
        )

        or state.get(
            "daily_trades",
            0,
        )
        >= MAX_TRADES_PER_DAY

    ):

        return (

            "🔎 <b>ПОИСК СЕТАПА</b>\n\n"

            "🛑 Дневной лимит закрыт.\n"

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

            f"📐 Направление: "
            f"<b>{result.get('direction')}</b>",

            f"⭐ Score: "
            f"<b>{score}/100</b>",

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


        if result.get(
            "error"
        ):

            continue


        stage = get_result_stage(
            result
        )

        score = result.get(
            "score",
            0,
        )

        direction = result.get(
            "direction"
        )


        direction_text = (

            f" | {direction}"

            if direction

            else ""

        )


        lines.append(

            f"{stage_icon(stage)} "
            f"<b>{coin}</b>: "
            f"{stage_text(stage)}"
            f"{direction_text} "
            f"— {score}/100"

        )


    lines += [

        "",

        "Ждём:",

        "Major Liquidity → Sweep → "
        "15M → 5M ILM.",

        "",

        "❌ Нет полного подтверждения "
        "→ нет входа.",

    ]


    return "\n".join(lines)


# =========================================================
# SOL MESSAGE
# =========================================================

def build_sol_message(
    result,
):

    if not result:

        return (

            "📈 <b>SOL</b>\n\n"

            "⏳ Нет данных.\n"

            "Нажми 🔄 Обновить."

        )


    if result.get(
        "error"
    ):

        return (

            "❌ <b>SOL</b>\n\n"

            "Ошибка получения данных."

        )


    stage = get_result_stage(
        result
    )


    lines = [

        "📈 <b>TRADEMIND 5.8.1 — SOL</b>",

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

            f"Direction: "
            f"<b>{sweep.get('direction')}</b>",

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

            "🟡 <b>15M CONFIRMATION</b>",

            str(
                result.get(
                    "confirmation_15m"
                )
            ),

        ]


    if result.get(
        "confirmation"
    ):

        lines += [

            "",

            "🔵 <b>5M ILM</b>",

            str(
                result.get(
                    "confirmation"
                )
            ),

        ]


    if result.get(
        "entry"
    ) is not None:

        lines += [

            "",

            "🎯 <b>SETUP</b>",

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

        ]


    reason = result.get(
        "reason"
    )


    if reason:

        lines += [

            "",

            "Причина:",

            str(reason),

        ]


    return "\n".join(lines)


# =========================================================
# SEND
# =========================================================

async def send_to_subscribers(
    application,
    text,
):

    subscribers = (
        load_subscribers()
    )


    if not subscribers:

        return


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


# =========================================================
# MONITOR KEYS
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


def confirmation_key(
    coin,
    result,
):

    confirmation = result.get(
        "confirmation_15m"
    )


    if not confirmation:

        return None


    timestamp = (

        result.get(
            "confirmation_15m_time"
        )

        or result.get(
            "m15_confirmation_time"
        )

        or result.get(
            "confirmation_time_15m"
        )

    )


    return (

        f"{coin}:"
        f"{result.get('direction')}:"
        f"{timestamp}:"
        f"{str(confirmation)[:100]}"

    )


# =========================================================
# MONITOR JOB
# =========================================================

async def monitor_job(
    application
):

    state = (
        reset_daily_state_if_needed()
    )


    # =====================================================
    # ONE TRADE / SIGNAL PER DAY
    # =====================================================

    if (

        state.get(
            "daily_stop"
        )

        or state.get(
            "daily_trades",
            0,
        )
        >= MAX_TRADES_PER_DAY

    ):

        # Still update market cache,
        # but do not create new trade signals.

        try:

            results = (
                await scan_all_coins_async()
            )

            save_results_cache(
                results
            )

        except Exception as exc:

            print(
                f"MONITOR CACHE ERROR: {exc}"
            )

        return


    # =====================================================
    # SCAN
    # =====================================================

    try:

        results = (
            await scan_all_coins_async()
        )

        # IMPORTANT:
        # Save the newest market snapshot.
        save_results_cache(
            results
        )

    except Exception as exc:

        print(
            f"MONITOR ERROR: {exc}"
        )

        return


    # =====================================================
    # SWEEP ALERT
    # =====================================================

    for coin, result in results.items():

        if result.get(
            "error"
        ):

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
        ].get(
            coin
        )


        if key == previous:

            continue


        state[
            "last_sweep_keys"
        ][coin] = key


        text = "\n".join([

            "🚨 <b>TRADEMIND 5.8.1 — SWEEP</b>",

            "",

            f"💠 {coin}",

            f"📐 <b>{sweep.get('direction')}</b>",

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
            application,
            text,
        )


    # =====================================================
    # 15M CONFIRMATION
    # =====================================================

    for coin, result in results.items():

        if result.get(
            "error"
        ):

            continue


        confirmation = result.get(
            "confirmation_15m"
        )


        if not confirmation:

            continue


        key = confirmation_key(
            coin,
            result,
        )


        if not key:

            continue


        previous = state[
            "last_15m_keys"
        ].get(
            coin
        )


        if key == previous:

            continue


        state[
            "last_15m_keys"
        ][coin] = key


        text = "\n".join([

            "🟡 <b>TRADEMIND 5.8.1 — 15M CONFIRMATION</b>",

            "",

            f"💠 {coin}",

            f"📐 Direction: "
            f"<b>{result.get('direction')}</b>",

            "",

            "✅ Sweep confirmed.",

            "✅ 15M confirmation.",

            "⏳ Ждём 5M ILM.",

            "❌ Вход пока запрещён.",

        ])


        await send_to_subscribers(
            application,
            text,
        )


    # =====================================================
    # READY
    # =====================================================

    ready = find_first_ready(
        results
    )


    if not ready:

        save_state(
            state
        )

        return


    score, coin, result = ready


    key = ready_key(
        coin,
        result,
    )


    if state.get(
        "last_signal_key"
    ) == key:

        save_state(
            state
        )

        return


    if state.get(
        "daily_trades",
        0,
    ) >= MAX_TRADES_PER_DAY:

        save_state(
            state
        )

        return


    # =====================================================
    # ONE TRADE / DAY
    # =====================================================

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


    state[
        "last_ready_keys"
    ][coin] = key


    # After READY no more trade signals today.
    state[
        "daily_stop"
    ] = True


    save_state(
        state
    )


    text = "\n".join([

        "🟢 <b>TRADEMIND 5.8.1 — READY</b>",

        "",

        f"💠 Монета: "
        f"<b>{coin}</b>",

        f"📐 Направление: "
        f"<b>{result.get('direction')}</b>",

        f"⭐ Score: "
        f"<b>{score}/100</b>",

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
        application,
        text,
    )


# =========================================================
# ASYNC MONITOR LOOP
# =========================================================

async def monitor_loop(
    application
):

    print(

        "TradeMind background monitor started. "
        f"Interval: {CHECK_INTERVAL}s"

    )


    while True:

        try:

            await monitor_job(
                application
            )

        except asyncio.CancelledError:

            print(
                "TradeMind monitor stopped."
            )

            raise

        except Exception as exc:

            print(
                f"MONITOR LOOP ERROR: {exc}"
            )


        await asyncio.sleep(
            CHECK_INTERVAL
        )


# =========================================================
# /START
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(

        build_dashboard_message(),

        parse_mode="HTML",

        reply_markup=dashboard_keyboard(),

    )


# =========================================================
# /DASHBOARD
# =========================================================

async def dashboard_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(

        build_dashboard_message(),

        parse_mode="HTML",

        reply_markup=dashboard_keyboard(),

    )


# =========================================================
# /MARKET
# =========================================================

async def market_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    results, _ = (
        get_cached_results()
    )


    if not results:

        results = (
            await scan_all_coins_async()
        )

        save_results_cache(
            results
        )


    await update.message.reply_text(

        build_market_message(
            results
        ),

        parse_mode="HTML",

        reply_markup=dashboard_keyboard(),

    )


# =========================================================
# /LEVELS
# =========================================================

async def levels_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    results, _ = (
        get_cached_results()
    )


    if not results:

        results = (
            await scan_all_coins_async()
        )

        save_results_cache(
            results
        )


    await update.message.reply_text(

        build_levels_message(
            results
        ),

        parse_mode="HTML",

        reply_markup=dashboard_keyboard(),

    )


# =========================================================
# /SEARCH
# =========================================================

async def search_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    results, _ = (
        get_cached_results()
    )


    if not results:

        results = (
            await scan_all_coins_async()
        )

        save_results_cache(
            results
        )


    await update.message.reply_text(

        build_search_message(
            results
        ),

        parse_mode="HTML",

        reply_markup=dashboard_keyboard(),

    )


# =========================================================
# /SOL
# =========================================================

async def sol_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    results, _ = (
        get_cached_results()
    )


    result = results.get(
        "SOL"
    )


    if not result:

        result = await asyncio.to_thread(

            build_analysis,

            COINS["SOL"],

        )


        results["SOL"] = result

        save_results_cache(
            results
        )


    await update.message.reply_text(

        build_sol_message(
            result
        ),

        parse_mode="HTML",

        reply_markup=dashboard_keyboard(),

    )


# =========================================================
# /SUBSCRIBE
# =========================================================

async def subscribe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = (
        update.effective_chat.id
    )

    subscribers = (
        load_subscribers()
    )


    if chat_id not in subscribers:

        subscribers.append(
            chat_id
        )

        save_subscribers(
            subscribers
        )


    await update.message.reply_text(

        "🔔 <b>Мониторинг включён.</b>\n\n"

        "Будут приходить только важные события:\n"

        "💧 Sweep\n"

        "🟡 15M confirmation\n"

        "🟢 READY",

        parse_mode="HTML",

        reply_markup=dashboard_keyboard(),

    )


# =========================================================
# /UNSUBSCRIBE
# =========================================================

async def unsubscribe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = (
        update.effective_chat.id
    )

    subscribers = (
        load_subscribers()
    )


    if chat_id in subscribers:

        subscribers.remove(
            chat_id
        )

        save_subscribers(
            subscribers
        )


    await update.message.reply_text(

        "🔕 <b>Мониторинг выключен.</b>",

        parse_mode="HTML",

        reply_markup=dashboard_keyboard(),

    )


# =========================================================
# /STATUS
# =========================================================

async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    state = (
        reset_daily_state_if_needed()
    )

    _, updated_at = (
        get_cached_results()
    )


    bingx_status = (

        "AVAILABLE"

        if BINGX_AVAILABLE

        else "OFF"

    )


    text = "\n".join([

        "📊 <b>TRADEMIND 5.8.1 — STATUS</b>",

        "",

        f"Strategy: "
        f"<b>{STRATEGY_VERSION}</b>",

        "Market: <b>Binance Spot</b>",

        f"BingX: "
        f"<b>{bingx_status}</b>",

        "",

        "📡 Monitor: <b>ONLINE</b>",

        f"Interval: "
        f"<b>{CHECK_INTERVAL}s</b>",

        f"Cache: "
        f"<b>{cache_age_text(updated_at)}</b>",

        "",

        f"Signals today: "
        f"<b>{state.get('daily_trades', 0)}/1</b>",

        f"Daily stop: "
        f"<b>{'YES' if state.get('daily_stop') else 'NO'}</b>",

        "",

        "Coins: <b>9/9</b>",

        "TP: <b>ONE</b>",

        "RR: <b>EXACT 1:2</b>",

    ])


    await update.message.reply_text(

        text,

        parse_mode="HTML",

        reply_markup=dashboard_keyboard(),

    )


# =========================================================
# /JOURNAL
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

            "📒 <b>TRADEMIND JOURNAL</b>\n\n"
            "Журнал пока пуст.",

            parse_mode="HTML",

            reply_markup=dashboard_keyboard(),

        )

        return


    lines = [

        "📒 <b>TRADEMIND JOURNAL</b>",

        "",

        f"Всего сделок: "
        f"<b>{len(journal)}</b>",

        "",

    ]


    for trade in reversed(
        journal[-10:]
    ):

        lines += [

            f"💠 {trade.get('coin', '?')}",

            f"📐 {trade.get('direction', '?')}",

            f"📊 Result: "
            f"<b>{trade.get('result', '?')}</b>",

            f"Entry: "
            f"{trade.get('entry', '?')}",

            f"SL: "
            f"{trade.get('sl', '?')}",

            f"TP: "
            f"{trade.get('tp', '?')}",

            "",

        ]


    await update.message.reply_text(

        "\n".join(lines),

        parse_mode="HTML",

        reply_markup=dashboard_keyboard(),

    )


# =========================================================
# CALLBACK HANDLER
# =========================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    action = query.data


    # =====================================================
    # MAIN DASHBOARD
    # =====================================================

    if action == "start":

        await query.edit_message_text(

            build_dashboard_message(),

            parse_mode="HTML",

            reply_markup=dashboard_keyboard(),

        )

        return


    # =====================================================
    # REFRESH
    #
    # THIS IS THE ONLY NORMAL UI BUTTON
    # THAT FORCES A FULL MARKET SCAN.
    # =====================================================

    if action == "refresh":

        await query.edit_message_text(

            "🔄 <b>Обновляю рынок...</b>\n\n"
            "⏳ Проверяю 9 монет.",

            parse_mode="HTML",

        )


        try:

            results = (
                await scan_all_coins_async()
            )

            save_results_cache(
                results
            )


            await query.edit_message_text(

                build_dashboard_message(),

                parse_mode="HTML",

                reply_markup=dashboard_keyboard(),

            )

        except Exception as exc:

            await query.edit_message_text(

                f"❌ Ошибка обновления:\n{exc}",

                parse_mode="HTML",

                reply_markup=dashboard_keyboard(),

            )

        return


    # =====================================================
    # RADAR
    # =====================================================

    if action == "radar":

        await query.edit_message_text(

            build_dashboard_message(),

            parse_mode="HTML",

            reply_markup=dashboard_keyboard(),

        )

        return


    # =====================================================
    # ACTIVE SETUP
    # =====================================================

    if action == "active":

        await query.edit_message_text(

            build_active_setup_message(),

            parse_mode="HTML",

            reply_markup=dashboard_keyboard(),

        )

        return


    # =====================================================
    # WHY WAIT
    # =====================================================

    if action == "why":

        await query.edit_message_text(

            build_why_wait_message(),

            parse_mode="HTML",

            reply_markup=dashboard_keyboard(),

        )

        return


    # =====================================================
    # MARKET
    # =====================================================

    if action == "market":

        results, _ = (
            get_cached_results()
        )


        if not results:

            results = (
                await scan_all_coins_async()
            )

            save_results_cache(
                results
            )


        await query.edit_message_text(

            build_market_message(
                results
            ),

            parse_mode="HTML",

            reply_markup=dashboard_keyboard(),

        )

        return


    # =====================================================
    # LEVELS
    # =====================================================

    if action == "levels":

        results, _ = (
            get_cached_results()
        )


        if not results:

            results = (
                await scan_all_coins_async()
            )

            save_results_cache(
                results
            )


        await query.edit_message_text(

            build_levels_message(
                results
            ),

            parse_mode="HTML",

            reply_markup=dashboard_keyboard(),

        )

        return


    # =====================================================
    # SEARCH
    # =====================================================

    if action == "search":

        results, _ = (
            get_cached_results()
        )


        if not results:

            results = (
                await scan_all_coins_async()
            )

            save_results_cache(
                results
            )


        await query.edit_message_text(

            build_search_message(
                results
            ),

            parse_mode="HTML",

            reply_markup=dashboard_keyboard(),

        )

        return


    # =====================================================
    # SOL
    # =====================================================

    if action == "sol":

        results, _ = (
            get_cached_results()
        )


        result = results.get(
            "SOL"
        )


        if not result:

            await query.edit_message_text(

                "📈 <b>SOL</b>\n\n"
                "⏳ Нет данных.\n"
                "Нажми 🔄 Обновить.",

                parse_mode="HTML",

                reply_markup=dashboard_keyboard(),

            )

            return


        await query.edit_message_text(

            build_sol_message(
                result
            ),

            parse_mode="HTML",

            reply_markup=dashboard_keyboard(),

        )

        return


    # =====================================================
    # STATUS
    # =====================================================

    if action == "status":

        state = (
            reset_daily_state_if_needed()
        )

        _, updated_at = (
            get_cached_results()
        )


        await query.edit_message_text(

            "\n".join([

                "📊 <b>TRADEMIND 5.8.1 — STATUS</b>",

                "",

                f"Strategy: "
                f"<b>{STRATEGY_VERSION}</b>",

                "Market: "
                "<b>Binance Spot</b>",

                f"BingX: "
                f"<b>{'AVAILABLE' if BINGX_AVAILABLE else 'OFF'}</b>",

                "",

                "📡 Monitor: <b>ONLINE</b>",

                f"Interval: "
                f"<b>{CHECK_INTERVAL}s</b>",

                f"Cache: "
                f"<b>{cache_age_text(updated_at)}</b>",

                "",

                f"Signals today: "
                f"<b>{state.get('daily_trades', 0)}/1</b>",

                f"Daily stop: "
                f"<b>{'YES' if state.get('daily_stop') else 'NO'}</b>",

                "",

                "Coins: <b>9/9</b>",

                "TP: <b>ONE</b>",

                "RR: <b>EXACT 1:2</b>",

            ]),

            parse_mode="HTML",

            reply_markup=dashboard_keyboard(),

        )

        return


    # =====================================================
    # SUBSCRIBE
    # =====================================================

    if action == "subscribe":

        chat_id = (
            query.message.chat_id
        )

        subscribers = (
            load_subscribers()
        )


        if chat_id not in subscribers:

            subscribers.append(
                chat_id
            )

            save_subscribers(
                subscribers
            )


        await query.edit_message_text(

            "🔔 <b>Мониторинг включён.</b>\n\n"

            "Важные события:\n"

            "💧 Sweep\n"

            "🟡 15M confirmation\n"

            "🟢 READY",

            parse_mode="HTML",

            reply_markup=dashboard_keyboard(),

        )

        return


    # =====================================================
    # UNSUBSCRIBE
    # =====================================================

    if action == "unsubscribe":

        chat_id = (
            query.message.chat_id
        )

        subscribers = (
            load_subscribers()
        )


        if chat_id in subscribers:

            subscribers.remove(
                chat_id
            )

            save_subscribers(
                subscribers
            )


        await query.edit_message_text(

            "🔕 <b>Мониторинг выключен.</b>",

            parse_mode="HTML",

            reply_markup=dashboard_keyboard(),

        )

        return


    # =====================================================
    # JOURNAL
    # =====================================================

    if action == "journal":

        journal = load_json(
            JOURNAL_FILE,
            [],
        )


        if not journal:

            text = (

                "📒 <b>TRADEMIND JOURNAL</b>\n\n"

                "Журнал пока пуст."

            )

        else:

            lines = [

                "📒 <b>TRADEMIND JOURNAL</b>",

                "",

                f"Всего сделок: "
                f"<b>{len(journal)}</b>",

                "",

            ]


            for trade in reversed(
                journal[-10:]
            ):

                lines.append(

                    f"💠 {trade.get('coin', '?')} "
                    f"| {trade.get('direction', '?')} "
                    f"| <b>{trade.get('result', '?')}</b>"

                )


            text = "\n".join(
                lines
            )


        await query.edit_message_text(

            text,

            parse_mode="HTML",

            reply_markup=dashboard_keyboard(),

        )

        return


# =========================================================
# POST INIT
# =========================================================

async def post_init(
    application,
):

    application.create_task(

        monitor_loop(
            application
        )

    )

    print(
        "TradeMind background monitor launched."
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not TOKEN:

        raise RuntimeError(

            "BOT_TOKEN environment variable "
            "is not set."

        )


    application = (

        Application.builder()

        .token(TOKEN)

        .post_init(
            post_init
        )

        .build()

    )


    # =====================================================
    # COMMANDS
    # =====================================================

    application.add_handler(

        CommandHandler(
            "start",
            start_command,
        )

    )


    application.add_handler(

        CommandHandler(
            "dashboard",
            dashboard_command,
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


    # =====================================================
    # CALLBACKS
    # =====================================================

    application.add_handler(

        CallbackQueryHandler(
            callback_handler
        )

    )


    # =====================================================
    # START
    # =====================================================

    print(
        "TradeMind 5.8.1 started."
    )

    print(
        f"Strategy: {STRATEGY_VERSION}"
    )

    print(
        "Market: Binance Spot"
    )

    print(
        "BingX available:",
        BINGX_AVAILABLE,
    )

    print(
        f"Monitoring interval: "
        f"{CHECK_INTERVAL}s"
    )

    print(
        f"Coins: {len(COINS)}/9"
    )


    application.run_polling()


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":

    main()