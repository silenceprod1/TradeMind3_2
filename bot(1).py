import asyncio
import io
import json
import os
import struct
import time
import uuid
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import escape

from telegram import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
    InputFile,
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
)

from market import (
    get_market_data,
    find_major_liquidity,
    detect_sweep,
)

from strategy import (
    analyze,
    STRATEGY_VERSION,
    get_1h_direction,
)


# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("BOT_TOKEN")

CHECK_INTERVAL = 15

# 19 монет → немного больше параллельных workers
SCAN_WORKERS = 12

MIN_SCORE_READY = 80
MIN_RR = 2.0


# ============================================================
# COINS
# ============================================================
#
# ВСЕ 19 МОНЕТ
#
# Стратегия для всех одинаковая:
#
# 1H
# ↓
# Major Liquidity
# ↓
# Sweep
# ↓
# 15M Confirmation
# ↓
# 5M ILM
# ↓
# Entry
#
# D1/W1 НЕ ИСПОЛЬЗУЮТСЯ.
# ============================================================

COINS = {
    # ========================================================
    # CORE
    # ========================================================

    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "BNB": "BNBUSDT",
    "XRP": "XRPUSDT",

    # ========================================================
    # ALT
    # ========================================================

    "DOGE": "DOGEUSDT",
    "ADA": "ADAUSDT",
    "AVAX": "AVAXUSDT",
    "LINK": "LINKUSDT",

    # ========================================================
    # EXTENDED
    # ========================================================

    "HYPE": "HYPEUSDT",
    "SUI": "SUIUSDT",
    "TRX": "TRXUSDT",
    "DOT": "DOTUSDT",
    "LTC": "LTCUSDT",
    "BCH": "BCHUSDT",
    "NEAR": "NEARUSDT",
    "APT": "APTUSDT",
    "ARB": "ARBUSDT",
    "OP": "OPUSDT",
}


# ============================================================
# FILES
# ============================================================

SUBSCRIBERS_FILE = "subscribers.json"
TRADE_JOURNAL_FILE = "trade_journal.json"
ACTIVE_TRADES_FILE = "active_trades.json"
PENDING_SETUPS_FILE = "pending_setups.json"

NOTIFICATION_STATE_FILE = "notification_state.json"


# ============================================================
# JSON
# ============================================================

def load_json(filename, default):
    try:
        with open(filename, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return default


def save_json(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
            )
    except Exception as exc:
        print("SAVE ERROR:", filename, exc)


# ============================================================
# TIME
# ============================================================

def now_iso():
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(),
    )


def now_ms():
    return int(time.time() * 1000)


# ============================================================
# SUBSCRIBERS
# ============================================================

def subscribers():
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


def is_subscribed(chat_id):
    return chat_id in subscribers()


# ============================================================
# ACTIVE TRADES
# ============================================================

def load_active_trades():
    data = load_json(
        ACTIVE_TRADES_FILE,
        [],
    )

    if not isinstance(data, list):
        return []

    return data


def save_active_trades(trades):
    save_json(
        ACTIVE_TRADES_FILE,
        trades,
    )


def user_active_trades(chat_id):
    return [
        trade
        for trade in load_active_trades()
        if trade.get("status") == "OPEN"
        and trade.get("chat_id") == chat_id
    ]


# ============================================================
# JOURNAL
# ============================================================

def load_journal():
    data = load_json(
        TRADE_JOURNAL_FILE,
        [],
    )

    if not isinstance(data, list):
        return []

    return data


def save_journal(journal):
    save_json(
        TRADE_JOURNAL_FILE,
        journal[-500:],
    )


def user_journal(chat_id):
    return [
        trade
        for trade in load_journal()
        if trade.get("chat_id") == chat_id
    ]


# ============================================================
# PENDING SETUPS
# ============================================================

def load_pending_setups():
    data = load_json(
        PENDING_SETUPS_FILE,
        {},
    )

    if not isinstance(data, dict):
        return {}

    return data


def save_pending_setups(data):
    save_json(
        PENDING_SETUPS_FILE,
        data,
    )


# ============================================================
# NOTIFICATION STATE
# ============================================================

def load_notification_state():
    data = load_json(
        NOTIFICATION_STATE_FILE,
        {},
    )

    if not isinstance(data, dict):
        return {}

    return data


def save_notification_state(data):
    save_json(
        NOTIFICATION_STATE_FILE,
        data,
    )


# ============================================================
# FORMAT
# ============================================================

def format_price(price):
    if price is None:
        return "N/A"

    try:
        price = float(price)
    except Exception:
        return "N/A"

    if price >= 1000:
        return f"${price:,.2f}"

    if price >= 1:
        return f"${price:,.4f}"

    return f"${price:,.6f}"


def format_rr(value):
    if value is None:
        return "N/A"

    try:
        return f"1:{float(value):.2f}"
    except Exception:
        return "N/A"


def calculate_pnl_percent(
    entry,
    exit_price,
    direction,
):
    try:
        entry = float(entry)
        exit_price = float(exit_price)

        if entry <= 0:
            return None

        if direction == "LONG":
            return (
                (exit_price - entry)
                / entry
                * 100
            )

        if direction == "SHORT":
            return (
                (entry - exit_price)
                / entry
                * 100
            )

    except Exception:
        return None

    return None


def direction_icon(direction):
    if direction == "LONG":
        return "🟢"

    if direction == "SHORT":
        return "🔴"

    return "⚪"


def stage_icon(stage):
    return {
        "READY": "🟢",
        "SWEPT": "🟠",
        "15M_CONFIRMED": "🟡",
        "WAIT": "⏳",
    }.get(
        stage,
        "⏳",
    )


def stage_text(stage):
    return {
        "READY": "🟢 МОЖНО ВХОДИТЬ",
        "SWEPT": "🟠 SWEEP",
        "15M_CONFIRMED": "🟡 15M CONFIRMED",
        "WAIT": "⏳ ОЖИДАНИЕ",
    }.get(
        stage,
        "⏳ ОЖИДАНИЕ",
    )


# ============================================================
# LIQUIDITY
# ============================================================

def strong_levels(levels):
    if not levels:
        return []

    strong = [
        level
        for level in levels
        if float(level.get("strength", 0)) >= 65
    ]

    if strong:
        return strong[:8]

    return levels[:6]


def levels_text(
    levels,
    current_price,
):
    levels = strong_levels(levels)

    if not levels:
        return "нет сильной major liquidity"

    lines = []

    for level in levels:
        try:
            level_price = float(level["price"])

            distance = (
                abs(level_price - current_price)
                / current_price
                * 100
            )

        except Exception:
            continue

        level_type = level.get(
            "type",
            "LEVEL",
        )

        icon = (
            "🔴"
            if level_type == "BSL"
            else "🟢"
        )

        lines.append(
            f"{icon} <b>{level_type}</b> "
            f"{format_price(level_price)} "
            f"• {distance:.2f}% "
            f"• S{float(level.get('strength', 0)):.0f}"
        )

    return (
        "\n".join(lines)
        if lines
        else "нет сильной major liquidity"
    )


# ============================================================
# BUILD ANALYSIS
# ============================================================

def build_analysis(symbol):
    market = get_market_data(symbol)

    price = market["price"]

    levels = find_major_liquidity(
        market["candles_1h"],
        price,
        12,
        market["candles_15m"],
        market["candles_5m"],
        market["candles_1m"],
    )

    direction = get_1h_direction(
        market["candles_1h"]
    )

    sweep = None

    if direction != "NEUTRAL":
        sweep = detect_sweep(
            market["candles_1h"],
            price,
            direction,
            levels,
        )

    result = analyze(
        market["candles_1h"],
        market["candles_15m"],
        market["candles_5m"],
        price,
        levels,
        sweep,
        candles_1m=market["candles_1m"],
    )

    result.update({
        "symbol": symbol,
        "price": price,
        "major_levels": levels,
        "sweep": sweep,
        "candles_1h": market["candles_1h"],
        "candles_15m": market["candles_15m"],
        "candles_5m": market["candles_5m"],
        "candles_1m": market["candles_1m"],
    })

    return result


# ============================================================
# SCANNER
# ============================================================

def scan_one(item):
    coin, symbol = item

    try:
        return (
            coin,
            build_analysis(symbol),
        )

    except Exception as exc:
        return (
            coin,
            {
                "error": str(exc),
                "symbol": symbol,
            },
        )


def scan_all():
    results = {}

    with ThreadPoolExecutor(
        max_workers=SCAN_WORKERS
    ) as executor:

        futures = [
            executor.submit(
                scan_one,
                item,
            )
            for item in COINS.items()
        ]

        for future in as_completed(futures):
            coin, result = future.result()
            results[coin] = result

    return {
        coin: results.get(
            coin,
            {"error": "нет данных"},
        )
        for coin in COINS
    }


# ============================================================
# DASHBOARD
# ============================================================

def dashboard_message(
    results,
    chat_id=None,
):
    ready = 0
    swept = 0
    confirmed = 0
    waiting = 0

    active = 0

    if chat_id is not None:
        active = len(
            user_active_trades(chat_id)
        )

    for result in results.values():
        stage = result.get("stage")

        if stage == "READY":
            ready += 1

        elif stage == "SWEPT":
            swept += 1

        elif stage == "15M_CONFIRMED":
            confirmed += 1

        else:
            waiting += 1

    lines = [
        "🧠 <b>TRADEMIND CONTROL CENTER</b>",
        f"<code>v{escape(str(STRATEGY_VERSION))}</code>",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"🟢 READY: <b>{ready}</b>",
        f"🟠 SWEEP: <b>{swept}</b>",
        f"🟡 15M: <b>{confirmed}</b>",
        f"⏳ WAIT: <b>{waiting}</b>",
        f"📌 ACTIVE: <b>{active}</b>",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"💠 Мониторинг: <b>{len(COINS)} монет</b>",
        "",
    ]

    for coin in COINS:
        result = results.get(
            coin,
            {},
        )

        if result.get("error"):
            lines.append(
                f"⚫ <b>{coin}</b> — ERROR"
            )
            continue

        price = format_price(
            result.get("price")
        )

        direction = result.get(
            "direction",
            "NEUTRAL",
        )

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
            f"<b>{coin}</b> "
            f"{price} "
            f"{direction_icon(direction)} "
            f"{direction} "
            f"<code>{score}/100</code>"
        )

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "🧭 <b>СТРАТЕГИЯ</b>",
        "",
        "1H → Major Liquidity",
        "→ Sweep → 15M → 5M ILM",
        "",
        "❌ D1/W1 OFF",
        "❌ Daily limit OFF",
        "⚙️ BingX OFF",
        "",
        "🔔 <b>Автоуведомление:</b>",
        "только полностью готовый READY.",
    ])

    return "\n".join(lines)


def dashboard_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔄 Обновить",
                callback_data="dashboard",
            ),
        ],
        [
            InlineKeyboardButton(
                "🟢 READY",
                callback_data="filter_ready",
            ),
            InlineKeyboardButton(
                "📌 ACTIVE",
                callback_data="active",
            ),
        ],
        [
            InlineKeyboardButton(
                "📈 Графики",
                callback_data="charts",
            ),
            InlineKeyboardButton(
                "📊 Рынок",
                callback_data="market",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔎 Сканер",
                callback_data="search",
            ),
            InlineKeyboardButton(
                "📒 Журнал",
                callback_data="journal",
            ),
        ],
        [
            InlineKeyboardButton(
                "🔔 Уведомления",
                callback_data="notifications",
            ),
            InlineKeyboardButton(
                "⚙️ Статус",
                callback_data="status",
            ),
        ],
    ])


# ============================================================
# READY FILTER
# ============================================================

def ready_filter_message(results):
    ready = []

    for coin, result in results.items():
        if result.get("error"):
            continue

        if (
            result.get("stage") == "READY"
            and result.get("score", 0)
            >= MIN_SCORE_READY
            and result.get("rr") is not None
            and float(result.get("rr")) >= MIN_RR
        ):
            ready.append(
                (
                    int(result.get("score", 0)),
                    coin,
                    result,
                )
            )

    if not ready:
        return (
            "🟢 <b>READY СЕТАПОВ НЕТ</b>\n\n"
            "Сейчас нет полного торгового сетапа.\n\n"
            "TradeMind продолжает мониторинг "
            "в фоне."
        )

    ready.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    lines = [
        "🟢 <b>READY SETUPS</b>",
        "",
    ]

    for score, coin, result in ready:
        lines.extend([
            f"💠 <b>{coin}</b>",
            f"📐 {direction_icon(result.get('direction'))} "
            f"{result.get('direction')}",
            f"⭐ Score: <b>{score}/100</b>",
            f"💰 Entry: <b>{format_price(result.get('entry'))}</b>",
            f"🛑 SL: <b>{format_price(result.get('sl'))}</b>",
            f"🎯 TP: <b>{format_price(result.get('tp'))}</b>",
            f"📊 RR: <b>{format_rr(result.get('rr'))}</b>",
            "",
        ])

    return "\n".join(lines)


def ready_filter_keyboard(results=None):
    rows = []

    if results:
        for coin, result in results.items():
            if (
                result.get("stage") == "READY"
                and result.get("score", 0) >= MIN_SCORE_READY
            ):
                rows.append([
                    InlineKeyboardButton(
                        f"💠 {coin}",
                        callback_data=f"coin_{coin}",
                    ),
                ])

    rows.extend([
        [
            InlineKeyboardButton(
                "🔄 Обновить",
                callback_data="filter_ready",
            ),
        ],
        [
            InlineKeyboardButton(
                "⬅️ Dashboard",
                callback_data="dashboard",
            ),
        ],
    ])

    return InlineKeyboardMarkup(rows)


# ============================================================
# COIN MESSAGE
# ============================================================

def coin_message(
    coin,
    result,
):
    if result.get("error"):
        return (
            f"❌ <b>{escape(coin)}</b>\n\n"
            f"{escape(str(result.get('error')))}"
        )

    stage = result.get(
        "stage",
        "WAIT",
    )

    direction = result.get(
        "direction",
        "NEUTRAL",
    )

    lines = [
        f"💠 <b>{escape(coin)}</b>",
        "",
        f"💰 Цена: <b>{format_price(result.get('price'))}</b>",
        (
            f"📐 1H: <b>"
            f"{direction_icon(direction)} "
            f"{direction}"
            f"</b>"
        ),
        f"⭐ Score: <b>{result.get('score', 0)}/100</b>",
        "",
        f"<b>{stage_text(stage)}</b>",
        "",
        "💧 <b>MAJOR LIQUIDITY</b>",
        levels_text(
            result.get("major_levels"),
            result.get("price"),
        ),
    ]

    sweep = result.get("sweep")

    if sweep:
        lines.extend([
            "",
            "💧 <b>ВНУТРЕННИЙ SWEEP</b>",
            (
                f"Level: <b>"
                f"{format_price(sweep.get('level'))}"
                f"</b>"
            ),
            (
                f"Extreme: <b>"
                f"{format_price(sweep.get('extreme'))}"
                f"</b>"
            ),
        ])

    if stage == "WAIT":
        lines.extend([
            "",
            "⏳ TradeMind продолжает поиск.",
            "❌ В середине движения не входим.",
        ])

    elif stage == "SWEPT":
        lines.extend([
            "",
            "✅ Major liquidity снята.",
            "⏳ Внутри системы ждём 15M.",
            "❌ Автоуведомление не отправляется.",
            "❌ Вход пока запрещён.",
        ])

    elif stage == "15M_CONFIRMED":
        lines.extend([
            "",
            "✅ Sweep",
            "✅ 15M confirmation",
            "⏳ Внутри системы ждём 5M ILM.",
            "❌ Автоуведомление не отправляется.",
            "❌ Вход пока запрещён.",
        ])

    elif stage == "READY":
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
            "🎯 <b>READY SETUP</b>",
            "",
            f"Entry: <b>{format_price(result.get('entry'))}</b>",
            f"SL: <b>{format_price(result.get('sl'))}</b>",
            f"TP: <b>{format_price(result.get('tp'))}</b>",
            f"RR: <b>{format_rr(result.get('rr'))}</b>",
            "",
            "🟢 <b>СЕТАП ГОТОВ</b>",
            "🟢 ВХОД РАЗРЕШЁН",
        ])

    reason = result.get("reason")

    if reason:
        lines.extend([
            "",
            f"ℹ️ {escape(str(reason))}",
        ])

    return "\n".join(lines)


# ============================================================
# COIN KEYBOARD
# ============================================================

def coin_keyboard(
    coin,
    result,
    setup=None,
):
    rows = [
        [
            InlineKeyboardButton(
                "📈 График",
                callback_data=f"chart_{coin}",
            ),
        ],
    ]

    if (
        result
        and result.get("stage") == "READY"
        and setup
    ):
        rows.append([
            InlineKeyboardButton(
                "🟢 Я ЗАШЁЛ",
                callback_data=f"enter_{setup['id']}",
            ),
        ])

    rows.extend([
        [
            InlineKeyboardButton(
                "🔄 Обновить",
                callback_data=f"coin_{coin}",
            ),
        ],
        [
            InlineKeyboardButton(
                "⬅️ Dashboard",
                callback_data="dashboard",
            ),
        ],
    ])

    return InlineKeyboardMarkup(rows)


# ============================================================
# CHART KEYBOARD
# ============================================================

def chart_keyboard():
    rows = []

    coins = list(COINS.keys())

    for i in range(
        0,
        len(coins),
        3,
    ):
        rows.append([
            InlineKeyboardButton(
                coin,
                callback_data=f"chart_{coin}",
            )
            for coin in coins[i:i + 3]
        ])

    rows.append([
        InlineKeyboardButton(
            "⬅️ Dashboard",
            callback_data="dashboard",
        ),
    ])

    return InlineKeyboardMarkup(rows)


# ============================================================
# READY SETUP
# ============================================================

def create_pending_setup(
    coin,
    result,
):
    if result.get("stage") != "READY":
        return None

    if result.get("score", 0) < MIN_SCORE_READY:
        return None

    required = (
        "entry",
        "sl",
        "tp",
        "rr",
        "direction",
    )

    if any(
        result.get(key) is None
        for key in required
    ):
        return None

    try:
        entry = float(result["entry"])
        sl = float(result["sl"])
        tp = float(result["tp"])
        rr = float(result["rr"])
    except Exception:
        return None

    if rr < MIN_RR:
        return None

    sweep = result.get("sweep") or {}
    ilm = result.get("ilm") or {}

    raw_id = (
        f"{coin}|"
        f"{result['direction']}|"
        f"{entry:.10f}|"
        f"{sl:.10f}|"
        f"{tp:.10f}|"
        f"{sweep.get('open_time')}|"
        f"{ilm.get('trigger_time')}"
    )

    setup_id = uuid.uuid5(
        uuid.NAMESPACE_DNS,
        raw_id,
    ).hex[:12]

    pending = load_pending_setups()

    old = pending.get(setup_id)

    created_at = (
        old.get("created_at")
        if old
        else now_iso()
    )

    return {
        "id": setup_id,
        "coin": coin,
        "symbol": result.get("symbol"),
        "direction": result.get("direction"),
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "score": int(result.get("score", 0)),
        "created_at": created_at,
        "sweep": sweep,
        "ilm": ilm,
        "status": "PENDING",
    }


def save_ready_setup(
    coin,
    result,
):
    setup = create_pending_setup(
        coin,
        result,
    )

    if setup is None:
        return None

    pending = load_pending_setups()

    pending[setup["id"]] = setup

    if len(pending) > 300:
        items = sorted(
            pending.items(),
            key=lambda item: item[1].get(
                "created_at",
                "",
            ),
        )

        pending = dict(
            items[-300:]
        )

    save_pending_setups(
        pending
    )

    return setup


# ============================================================
# ACTIVATE TRADE
# ============================================================

def activate_trade(
    setup,
    chat_id,
):
    active = load_active_trades()

    for trade in active:
        if (
            trade.get("setup_id") == setup["id"]
            and trade.get("chat_id") == chat_id
            and trade.get("status") == "OPEN"
        ):
            return trade, False

    trade_id = uuid.uuid4().hex[:12]

    current = float(setup["entry"])

    trade = {
        "id": trade_id,
        "setup_id": setup["id"],
        "chat_id": chat_id,
        "coin": setup["coin"],
        "symbol": setup["symbol"],
        "direction": setup["direction"],
        "entry": float(setup["entry"]),
        "sl": float(setup["sl"]),
        "tp": float(setup["tp"]),
        "rr": float(setup["rr"]),
        "score": int(setup.get("score", 0)),
        "opened_at": now_iso(),
        "opened_at_ms": now_ms(),
        "status": "OPEN",
        "last_price": current,
        "last_check_ms": now_ms(),
        "entry_source": "TradeMind READY snapshot",
    }

    active.append(trade)

    save_active_trades(active)

    return trade, True


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade,
    exit_price,
    result_type,
):
    active = load_active_trades()

    target = None

    for item in active:
        if item.get("id") == trade.get("id"):
            target = item
            break

    if target is None:
        return None

    target["status"] = result_type
    target["result"] = result_type
    target["exit_price"] = float(exit_price)
    target["closed_at"] = now_iso()

    target["pnl_percent"] = calculate_pnl_percent(
        target.get("entry"),
        exit_price,
        target.get("direction"),
    )

    save_active_trades(active)

    journal = load_journal()

    journal_entry = dict(target)
    journal_entry["result"] = result_type

    journal.append(journal_entry)

    save_journal(journal)

    return target


# ============================================================
# PRICE CROSSING
# ============================================================

def level_between(
    previous_price,
    current_price,
    level,
):
    try:
        low = min(
            float(previous_price),
            float(current_price),
        )

        high = max(
            float(previous_price),
            float(current_price),
        )

        level = float(level)

        return low <= level <= high

    except Exception:
        return False


def check_trade_price(
    trade,
    previous_price,
    current_price,
):
    try:
        current_price = float(current_price)
        previous_price = float(previous_price)

        sl = float(trade["sl"])
        tp = float(trade["tp"])
        direction = trade["direction"]

    except Exception:
        return None

    if direction == "LONG":

        tp_crossed = (
            current_price >= tp
            or level_between(
                previous_price,
                current_price,
                tp,
            )
        )

        sl_crossed = (
            current_price <= sl
            or level_between(
                previous_price,
                current_price,
                sl,
            )
        )

        if tp_crossed and sl_crossed:
            return ("AMBIGUOUS", current_price)

        if tp_crossed:
            return ("TP", current_price)

        if sl_crossed:
            return ("SL", current_price)

    elif direction == "SHORT":

        tp_crossed = (
            current_price <= tp
            or level_between(
                previous_price,
                current_price,
                tp,
            )
        )

        sl_crossed = (
            current_price >= sl
            or level_between(
                previous_price,
                current_price,
                sl,
            )
        )

        if tp_crossed and sl_crossed:
            return ("AMBIGUOUS", current_price)

        if tp_crossed:
            return ("TP", current_price)

        if sl_crossed:
            return ("SL", current_price)

    return None


# ============================================================
# RESOLVE 1M CROSS
# ============================================================

def resolve_crossed_levels_1m(
    trade,
    candles_1m,
    from_ms,
    to_ms,
):
    relevant = []

    for candle in candles_1m or []:
        try:
            open_time = int(
                candle["open_time"]
            )

            close_time = int(
                candle["close_time"]
            )

            if (
                close_time >= from_ms
                and open_time <= to_ms
            ):
                relevant.append(candle)

        except Exception:
            continue

    relevant.sort(
        key=lambda x: int(
            x["open_time"]
        )
    )

    if not relevant:
        return "AMBIGUOUS"

    direction = trade.get("direction")

    try:
        sl = float(trade["sl"])
        tp = float(trade["tp"])
    except Exception:
        return "AMBIGUOUS"

    for candle in relevant:

        try:
            high = float(candle["high"])
            low = float(candle["low"])
        except Exception:
            continue

        if direction == "LONG":
            hit_sl = low <= sl
            hit_tp = high >= tp
        else:
            hit_sl = high >= sl
            hit_tp = low <= tp

        if hit_sl and hit_tp:
            return "AMBIGUOUS"

        if hit_tp:
            return "TP"

        if hit_sl:
            return "SL"

    return "AMBIGUOUS"


# ============================================================
# TRADE CLOSE MESSAGE
# ============================================================

def trade_close_message(trade):
    result_type = trade.get(
        "result",
        trade.get("status"),
    )

    if result_type == "TP":
        icon = "✅"
        title = "TP ДОСТИГНУТ"

    elif result_type == "SL":
        icon = "❌"
        title = "SL ДОСТИГНУТ"

    else:
        icon = "⚪"
        title = "РЕЗУЛЬТАТ НЕОПРЕДЕЛЁН"

    pnl = trade.get("pnl_percent")

    pnl_text = (
        f"{float(pnl):+.2f}%"
        if pnl is not None
        else "N/A"
    )

    return (
        f"{icon} <b>TRADEMIND — {title}</b>\n\n"
        f"💠 <b>{escape(str(trade.get('coin')))}</b>\n"
        f"📐 {escape(str(trade.get('direction')))}\n\n"
        f"Entry: <b>{format_price(trade.get('entry'))}</b>\n"
        f"Exit: <b>{format_price(trade.get('exit_price'))}</b>\n"
        f"SL: <b>{format_price(trade.get('sl'))}</b>\n"
        f"TP: <b>{format_price(trade.get('tp'))}</b>\n\n"
        f"📊 RR: <b>{format_rr(trade.get('rr'))}</b>\n"
        f"📈 PnL: <b>{pnl_text}</b>"
    )


# ============================================================
# ACTIVE MONITOR
# ============================================================

async def monitor_active_trades(
    app,
    results,
):
    active = load_active_trades()

    if not active:
        return

    changed = False

    for trade in list(active):

        if trade.get("status") != "OPEN":
            continue

        coin = trade.get("coin")
        result = results.get(coin)

        if not result or result.get("error"):
            continue

        current_price = result.get("price")

        if current_price is None:
            continue

        try:
            current_price = float(
                current_price
            )
        except Exception:
            continue

        previous_price = float(
            trade.get(
                "last_price",
                trade.get("entry"),
            )
        )

        previous_check_ms = int(
            trade.get(
                "last_check_ms",
                trade.get(
                    "opened_at_ms",
                    now_ms(),
                ),
            )
        )

        current_check_ms = now_ms()

        check = check_trade_price(
            trade,
            previous_price,
            current_price,
        )

        if check is None:
            trade["last_price"] = current_price
            trade["last_check_ms"] = current_check_ms
            changed = True
            continue

        result_type, exit_price = check

        if result_type == "AMBIGUOUS":
            result_type = resolve_crossed_levels_1m(
                trade,
                result.get(
                    "candles_1m",
                    [],
                ),
                previous_check_ms,
                current_check_ms,
            )

        closed = close_trade(
            trade,
            exit_price,
            result_type,
        )

        if closed is None:
            continue

        changed = True

        chat_id = closed.get("chat_id")

        if not chat_id:
            continue

        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=trade_close_message(
                    closed
                ),
                parse_mode="HTML",
                reply_markup=trade_close_keyboard(
                    closed
                ),
            )

            await send_chart_to_chat(
                app,
                chat_id,
                coin,
                result=result,
                trade=closed,
            )

        except Exception as exc:
            print(
                "TRADE CLOSE NOTIFY ERROR:",
                exc,
            )

    if changed:
        active = [
            trade
            for trade in load_active_trades()
            if trade.get("status") == "OPEN"
        ]

        save_active_trades(active)


# ============================================================
# TRADE KEYBOARDS
# ============================================================

def trade_close_keyboard(trade):
    trade_id = trade.get(
        "id",
        "",
    )

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📈 График",
                callback_data=f"active_trade_{trade_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "📒 Журнал",
                callback_data="journal",
            ),
            InlineKeyboardButton(
                "🧠 Dashboard",
                callback_data="dashboard",
            ),
        ],
    ])


# ============================================================
# ACTIVE MESSAGE
# ============================================================

def active_message(
    chat_id,
):
    trades = user_active_trades(
        chat_id
    )

    if not trades:
        return (
            "📌 <b>АКТИВНЫХ СДЕЛОК НЕТ</b>\n\n"
            "Нажми «🟢 Я ЗАШЁЛ» на READY-сигнале."
        )

    lines = [
        "📌 <b>ACTIVE TRADES</b>",
        "",
    ]

    for trade in trades:

        coin = trade.get("coin")
        direction = trade.get("direction")

        current = trade.get(
            "last_price",
            trade.get("entry"),
        )

        entry = float(
            trade.get("entry")
        )

        pnl = calculate_pnl_percent(
            entry,
            current,
            direction,
        )

        pnl_text = (
            f"{pnl:+.2f}%"
            if pnl is not None
            else "N/A"
        )

        lines.extend([
            f"💠 <b>{escape(str(coin))}</b>",
            f"📐 {direction_icon(direction)} "
            f"<b>{direction}</b>",
            "",
            f"💰 Entry: <b>{format_price(entry)}</b>",
            f"📍 Price: <b>{format_price(current)}</b>",
            f"🛑 SL: <b>{format_price(trade.get('sl'))}</b>",
            f"🎯 TP: <b>{format_price(trade.get('tp'))}</b>",
            f"📊 RR: <b>{format_rr(trade.get('rr'))}</b>",
            f"📈 PnL: <b>{pnl_text}</b>",
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
        ])

    return "\n".join(lines)


def active_keyboard(chat_id):
    trades = user_active_trades(
        chat_id
    )

    rows = []

    for trade in trades:
        trade_id = trade.get("id")

        rows.append([
            InlineKeyboardButton(
                f"📈 {trade.get('coin')} "
                f"{trade.get('direction')}",
                callback_data=f"active_trade_{trade_id}",
            ),
        ])

    rows.extend([
        [
            InlineKeyboardButton(
                "🔄 Обновить",
                callback_data="active",
            ),
        ],
        [
            InlineKeyboardButton(
                "📒 Журнал",
                callback_data="journal",
            ),
            InlineKeyboardButton(
                "🧠 Dashboard",
                callback_data="dashboard",
            ),
        ],
    ])

    return InlineKeyboardMarkup(rows)


# ============================================================
# JOURNAL
# ============================================================

def journal_message(chat_id):
    journal = user_journal(
        chat_id
    )

    if not journal:
        return (
            "📒 <b>ЖУРНАЛ ПУСТ</b>\n\n"
            "Закрытые сделки появятся здесь."
        )

    total = len(journal)

    tp = len([
        x
        for x in journal
        if x.get("result") == "TP"
    ])

    sl = len([
        x
        for x in journal
        if x.get("result") == "SL"
    ])

    ambiguous = len([
        x
        for x in journal
        if x.get("result") == "AMBIGUOUS"
    ])

    resolved = tp + sl

    win_rate = (
        tp / resolved * 100
        if resolved
        else 0
    )

    pnl_values = [
        float(x["pnl_percent"])
        for x in journal
        if x.get("pnl_percent") is not None
    ]

    total_pnl = sum(
        pnl_values
    )

    lines = [
        "📒 <b>TRADEMIND JOURNAL</b>",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"📊 Сделок: <b>{total}</b>",
        f"✅ TP: <b>{tp}</b>",
        f"❌ SL: <b>{sl}</b>",
        f"⚪ Ambiguous: <b>{ambiguous}</b>",
        f"🎯 Win rate: <b>{win_rate:.1f}%</b>",
        f"📈 Sum PnL: <b>{total_pnl:+.2f}%</b>",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "<b>Последние сделки:</b>",
        "",
    ]

    for trade in reversed(
        journal[-10:]
    ):
        result_type = trade.get(
            "result",
            "?",
        )

        icon = {
            "TP": "✅",
            "SL": "❌",
            "AMBIGUOUS": "⚪",
        }.get(
            result_type,
            "❔",
        )

        pnl = trade.get(
            "pnl_percent"
        )

        pnl_text = (
            f"{float(pnl):+.2f}%"
            if pnl is not None
            else "N/A"
        )

        lines.append(
            f"{icon} <b>{escape(str(trade.get('coin')))}</b> "
            f"{trade.get('direction')} "
            f"• {result_type} "
            f"• {pnl_text}"
        )

    return "\n".join(lines)


def journal_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔄 Обновить",
                callback_data="journal",
            ),
        ],
        [
            InlineKeyboardButton(
                "📌 Active",
                callback_data="active",
            ),
            InlineKeyboardButton(
                "🧠 Dashboard",
                callback_data="dashboard",
            ),
        ],
    ])


# ============================================================
# READY MESSAGE
# ============================================================

def ready_message(
    coin,
    result,
    setup,
):
    return (
        "🚨 <b>TRADEMIND — READY</b>\n\n"
        f"💠 <b>{escape(str(coin))}</b>\n"
        f"📐 {direction_icon(result.get('direction'))} "
        f"<b>{result.get('direction')}</b>\n"
        f"⭐ Score: <b>{result.get('score', 0)}/100</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "💰 <b>ТОЧКА ВХОДА</b>\n\n"
        f"Entry: <b>{format_price(setup.get('entry'))}</b>\n"
        f"SL: <b>{format_price(setup.get('sl'))}</b>\n"
        f"TP: <b>{format_price(setup.get('tp'))}</b>\n"
        f"RR: <b>{format_rr(setup.get('rr'))}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "💧 TP = следующая свежая major liquidity.\n"
        "🧠 Entry / SL / TP зафиксированы snapshot'ом.\n\n"
        "🟢 <b>МОЖНО ВХОДИТЬ</b>\n\n"
        "Если вошёл — нажми «🟢 Я ЗАШЁЛ»."
    )


def ready_keyboard(setup):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🟢 Я ЗАШЁЛ",
                callback_data=f"enter_{setup['id']}",
            ),
        ],
        [
            InlineKeyboardButton(
                "📈 График",
                callback_data=f"chart_{setup['coin']}",
            ),
            InlineKeyboardButton(
                "💠 Карточка",
                callback_data=f"coin_{setup['coin']}",
            ),
        ],
    ])


# ============================================================
# READY CHART
# ============================================================

async def send_ready_chart(
    app,
    chat_id,
    coin,
    result,
    setup,
):
    try:
        image = chart_png(
            result,
            trade=None,
        )

        image.seek(0)

        caption = ready_message(
            coin,
            result,
            setup,
        )

        await app.bot.send_photo(
            chat_id=chat_id,
            photo=InputFile(
                image,
                filename=f"{coin.lower()}_ready.png",
            ),
            caption=caption,
            parse_mode="HTML",
            reply_markup=ready_keyboard(
                setup
            ),
        )

    except Exception as exc:
        print(
            "READY CHART ERROR:",
            exc,
        )

        try:
            await app.bot.send_message(
                chat_id=chat_id,
                text=caption,
                parse_mode="HTML",
                reply_markup=ready_keyboard(
                    setup
                ),
            )
        except Exception:
            pass


# ============================================================
# SEND PHOTO TO CHAT
# ============================================================

async def send_photo_to_chat(
    app,
    chat_id,
    coin,
    result,
    trade=None,
    reply_markup=None,
):
    image = chart_png(
        result,
        trade=trade,
    )

    image.seek(0)

    if trade:
        current = (
            trade.get("last_price")
            if trade.get("status") == "OPEN"
            else trade.get("exit_price")
        )

        pnl = calculate_pnl_percent(
            trade.get("entry"),
            current,
            trade.get("direction"),
        )

        caption = (
            f"💠 <b>{escape(str(coin))}</b>\n\n"
            f"📐 {direction_icon(trade.get('direction'))} "
            f"<b>{trade.get('direction')}</b>\n\n"
            f"Entry: <b>{format_price(trade.get('entry'))}</b>\n"
            f"SL: <b>{format_price(trade.get('sl'))}</b>\n"
            f"TP: <b>{format_price(trade.get('tp'))}</b>\n"
            f"RR: <b>{format_rr(trade.get('rr'))}</b>"
        )

        if trade.get("status") != "OPEN":
            caption += (
                "\n"
                f"Exit: <b>{format_price(trade.get('exit_price'))}</b>"
            )

        if pnl is not None:
            caption += (
                "\n"
                f"PnL: <b>{pnl:+.2f}%</b>"
            )

    else:
        caption = coin_message(
            coin,
            result,
        )

    if reply_markup is None:
        reply_markup = chart_keyboard()

    await app.bot.send_photo(
        chat_id=chat_id,
        photo=InputFile(
            image,
            filename=f"{coin.lower()}_trademind.png",
        ),
        caption=caption,
        parse_mode="HTML",
        reply_markup=reply_markup,
    )


async def send_chart_to_chat(
    app,
    chat_id,
    coin,
    result=None,
    trade=None,
):
    try:
        if result is None:
            result = await asyncio.to_thread(
                build_analysis,
                COINS[coin],
            )

        await send_photo_to_chat(
            app,
            chat_id,
            coin,
            result,
            trade=trade,
        )

    except Exception as exc:
        print(
            "CHART SEND ERROR:",
            exc,
        )


async def send_chart(
    message,
    coin,
    result=None,
    trade=None,
):
    try:
        if result is None:
            result = await asyncio.to_thread(
                build_analysis,
                COINS[coin],
            )

        image = chart_png(
            result,
            trade=trade,
        )

        image.seek(0)

        if trade:
            current = (
                trade.get("last_price")
                if trade.get("status") == "OPEN"
                else trade.get("exit_price")
            )

            pnl = calculate_pnl_percent(
                trade.get("entry"),
                current,
                trade.get("direction"),
            )

            caption = (
                f"💠 <b>{escape(str(coin))}</b>\n\n"
                f"📐 {direction_icon(trade.get('direction'))} "
                f"<b>{trade.get('direction')}</b>\n\n"
                f"Entry: <b>{format_price(trade.get('entry'))}</b>\n"
                f"SL: <b>{format_price(trade.get('sl'))}</b>\n"
                f"TP: <b>{format_price(trade.get('tp'))}</b>\n"
                f"RR: <b>{format_rr(trade.get('rr'))}</b>"
            )

            if pnl is not None:
                caption += (
                    f"\nPnL: <b>{pnl:+.2f}%</b>"
                )

        else:
            caption = coin_message(
                coin,
                result,
            )

        await message.reply_photo(
            photo=InputFile(
                image,
                filename=f"{coin.lower()}_trademind.png",
            ),
            caption=caption,
            parse_mode="HTML",
            reply_markup=chart_keyboard(),
        )

    except Exception as exc:
        await message.reply_text(
            f"❌ Ошибка графика: {escape(str(exc))}"
        )


# ============================================================
# BROADCAST READY ONLY
# ============================================================

async def broadcast_ready(
    app,
    coin,
    result,
    setup,
):
    ids = subscribers()

    if not ids:
        return

    async def send(chat_id):
        await send_ready_chart(
            app,
            chat_id,
            coin,
            result,
            setup,
        )

    await asyncio.gather(
        *(send(chat_id) for chat_id in ids),
        return_exceptions=True,
    )


# ============================================================
# PNG ENGINE
# ============================================================

def png_chunk(
    chunk_type,
    data,
):
    return (
        struct.pack(
            ">I",
            len(data),
        )
        + chunk_type
        + data
        + struct.pack(
            ">I",
            zlib.crc32(
                chunk_type + data
            ) & 0xffffffff,
        )
    )


def make_png(
    width,
    height,
    pixels,
):
    raw = b"".join(
        b"\0" + bytes(row)
        for row in pixels
    )

    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(
            b"IHDR",
            struct.pack(
                ">IIBBBBB",
                width,
                height,
                8,
                2,
                0,
                0,
                0,
            ),
        )
        + png_chunk(
            b"IDAT",
            zlib.compress(
                raw,
                6,
            ),
        )
        + png_chunk(
            b"IEND",
            b"",
        )
    )


def create_canvas(
    width,
    height,
):
    background = (
        14,
        18,
        24,
    )

    return [
        bytearray(
            background * width
        )
        for _ in range(height)
    ]


def put_pixel(
    pixels,
    x,
    y,
    color,
):
    if y < 0 or y >= len(pixels):
        return

    width = len(pixels[0]) // 3

    if x < 0 or x >= width:
        return

    index = x * 3

    pixels[y][index:index + 3] = bytes(
        color
    )


def draw_line(
    pixels,
    x1,
    y1,
    x2,
    y2,
    color,
    thickness=1,
):
    steps = max(
        abs(x2 - x1),
        abs(y2 - y1),
        1,
    )

    for i in range(
        steps + 1
    ):
        x = int(
            x1
            + (x2 - x1)
            * i
            / steps
        )

        y = int(
            y1
            + (y2 - y1)
            * i
            / steps
        )

        for dx in range(
            -thickness // 2,
            thickness // 2 + 1,
        ):
            for dy in range(
                -thickness // 2,
                thickness // 2 + 1,
            ):
                put_pixel(
                    pixels,
                    x + dx,
                    y + dy,
                    color,
                )


def draw_marker(
    pixels,
    x,
    y,
    color,
    radius=7,
):
    for dx in range(
        -radius,
        radius + 1,
    ):
        for dy in range(
            -radius,
            radius + 1,
        ):
            if (
                dx * dx
                + dy * dy
                <= radius * radius
            ):
                put_pixel(
                    pixels,
                    x + dx,
                    y + dy,
                    color,
                )


def fill_rect(
    pixels,
    x1,
    y1,
    x2,
    y2,
    color,
):
    x1 = max(
        0,
        int(x1),
    )

    x2 = min(
        len(pixels[0]) // 3 - 1,
        int(x2),
    )

    y1 = max(
        0,
        int(y1),
    )

    y2 = min(
        len(pixels) - 1,
        int(y2),
    )

    for y in range(
        min(y1, y2),
        max(y1, y2) + 1,
    ):
        row = pixels[y]

        for x in range(
            min(x1, x2),
            max(x1, x2) + 1,
        ):
            index = x * 3

            row[index:index + 3] = bytes(
                color
            )


# ============================================================
# CHART
# ============================================================

def chart_png(
    result,
    trade=None,
):
    candles = result.get(
        "candles_5m",
        [],
    )[-100:]

    levels = strong_levels(
        result.get(
            "major_levels",
            [],
        )
    )

    values = []

    for candle in candles:
        try:
            values.append(
                float(candle["high"])
            )

            values.append(
                float(candle["low"])
            )

        except Exception:
            continue

    for level in levels:
        try:
            values.append(
                float(level["price"])
            )
        except Exception:
            pass

    sweep = result.get(
        "sweep"
    )

    if sweep:
        for key in (
            "level",
            "extreme",
        ):
            try:
                values.append(
                    float(sweep[key])
                )
            except Exception:
                pass

    for key in (
        "price",
        "entry",
        "sl",
        "tp",
        "exit_price",
    ):
        if result.get(key) is not None:
            try:
                values.append(
                    float(result[key])
                )
            except Exception:
                pass

    if trade:
        for key in (
            "entry",
            "sl",
            "tp",
            "exit_price",
            "last_price",
        ):
            if trade.get(key) is not None:
                try:
                    values.append(
                        float(trade[key])
                    )
                except Exception:
                    pass

    if not values:
        current = float(
            result.get(
                "price",
                1,
            )
        )

        values = [
            current - 1,
            current + 1,
        ]

    low = min(values)
    high = max(values)

    padding = (
        (high - low) * 0.08
        or 1
    )

    low -= padding
    high += padding

    width = 1200
    height = 680

    left = 55
    right = 35
    top = 65
    bottom = 45

    chart_width = (
        width
        - left
        - right
    )

    chart_height = (
        height
        - top
        - bottom
    )

    pixels = create_canvas(
        width,
        height,
    )

    def y(value):
        return int(
            top
            + (
                high - value
            )
            / (
                high - low
            )
            * chart_height
        )

    # ========================================================
    # STAGE STRIPE
    # ========================================================

    stage = result.get(
        "stage",
        "WAIT",
    )

    stage_colors = {
        "READY": (
            60,
            220,
            140,
        ),
        "SWEPT": (
            255,
            165,
            45,
        ),
        "15M_CONFIRMED": (
            245,
            205,
            60,
        ),
        "WAIT": (
            90,
            100,
            115,
        ),
    }

    fill_rect(
        pixels,
        0,
        0,
        width,
        10,
        stage_colors.get(
            stage,
            (90, 100, 115),
        ),
    )

    # ========================================================
    # GRID
    # ========================================================

    grid_color = (
        42,
        48,
        58,
    )

    for i in range(
        1,
        9,
    ):
        yy = (
            top
            + chart_height * i // 9
        )

        draw_line(
            pixels,
            left,
            yy,
            width - right,
            yy,
            grid_color,
            1,
        )

    # ========================================================
    # MAJOR LIQUIDITY ZONES
    # ========================================================

    for level in levels:

        try:
            level_price = float(
                level["price"]
            )

            zone_low = float(
                level.get(
                    "zone_low",
                    level_price * 0.998,
                )
            )

            zone_high = float(
                level.get(
                    "zone_high",
                    level_price * 1.002,
                )
            )

        except Exception:
            continue

        if level.get("type") == "BSL":
            zone_color = (
                65,
                30,
                38,
            )

            line_color = (
                235,
                80,
                90,
            )

        else:
            zone_color = (
                25,
                60,
                42,
            )

            line_color = (
                50,
                210,
                130,
            )

        fill_rect(
            pixels,
            left,
            y(zone_high),
            width - right,
            y(zone_low),
            zone_color,
        )

        draw_line(
            pixels,
            left,
            y(level_price),
            width - right,
            y(level_price),
            line_color,
            2,
        )

    # ========================================================
    # CANDLES
    # ========================================================

    if candles:

        spacing = (
            chart_width
            / max(
                len(candles),
                1,
            )
        )

        candle_width = max(
            3,
            int(
                spacing * 0.58
            ),
        )

        for i, candle in enumerate(
            candles
        ):

            try:
                open_price = float(
                    candle["open"]
                )

                high_price = float(
                    candle["high"]
                )

                low_price = float(
                    candle["low"]
                )

                close_price = float(
                    candle["close"]
                )

            except Exception:
                continue

            x = int(
                left
                + (
                    i + 0.5
                )
                * spacing
            )

            if close_price >= open_price:
                candle_color = (
                    55,
                    205,
                    125,
                )
            else:
                candle_color = (
                    230,
                    80,
                    90,
                )

            draw_line(
                pixels,
                x,
                y(high_price),
                x,
                y(low_price),
                candle_color,
                1,
            )

            body_top = min(
                y(open_price),
                y(close_price),
            )

            body_bottom = max(
                y(open_price),
                y(close_price),
            )

            if body_bottom <= body_top:
                body_bottom = body_top + 1

            for xx in range(
                x - candle_width // 2,
                x + candle_width // 2 + 1,
            ):
                for yy in range(
                    body_top,
                    body_bottom + 1,
                ):
                    put_pixel(
                        pixels,
                        xx,
                        yy,
                        candle_color,
                    )

    # ========================================================
    # CURRENT PRICE
    # ========================================================

    current_price = result.get(
        "price"
    )

    if current_price is not None:

        try:
            current_price = float(
                current_price
            )

            draw_line(
                pixels,
                left,
                y(current_price),
                width - right,
                y(current_price),
                (
                    80,
                    170,
                    255,
                ),
                2,
            )

            draw_marker(
                pixels,
                width - right - 8,
                y(current_price),
                (
                    80,
                    170,
                    255,
                ),
                8,
            )

        except Exception:
            pass

    # ========================================================
    # SWEEP
    # ========================================================

    if sweep:

        for key in (
            "level",
            "extreme",
        ):

            try:
                value = float(
                    sweep[key]
                )

                draw_line(
                    pixels,
                    left,
                    y(value),
                    width - right,
                    y(value),
                    (
                        255,
                        165,
                        40,
                    ),
                    3,
                )

                draw_marker(
                    pixels,
                    left + 15,
                    y(value),
                    (
                        255,
                        165,
                        40,
                    ),
                    6,
                )

            except Exception:
                continue

    # ========================================================
    # TRADE LEVELS
    # ========================================================

    trade_source = (
        trade
        or result
    )

    trade_colors = {
        "entry": (
            255,
            215,
            70,
        ),
        "sl": (
            235,
            80,
            90,
        ),
        "tp": (
            80,
            220,
            150,
        ),
        "exit_price": (
            180,
            100,
            255,
        ),
    }

    for key, color in trade_colors.items():

        value = trade_source.get(key)

        if value is None:
            continue

        try:
            value = float(value)

            draw_line(
                pixels,
                left,
                y(value),
                width - right,
                y(value),
                color,
                3,
            )

            draw_marker(
                pixels,
                width - right - 10,
                y(value),
                color,
                8,
            )

        except Exception:
            continue

    # ========================================================
    # EXIT
    # ========================================================

    if (
        trade
        and trade.get("exit_price") is not None
    ):

        try:
            exit_y = y(
                float(
                    trade["exit_price"]
                )
            )

            exit_x = (
                width
                - right
                - 55
            )

            draw_marker(
                pixels,
                exit_x,
                exit_y,
                (
                    180,
                    100,
                    255,
                ),
                11,
            )

        except Exception:
            pass

    return io.BytesIO(
        make_png(
            width,
            height,
            pixels,
        )
    )


# ============================================================
# START
# ============================================================

async def start(
    update,
    context,
):
    results = await asyncio.to_thread(
        scan_all
    )

    await update.message.reply_text(
        dashboard_message(
            results,
            update.effective_chat.id,
        ),
        parse_mode="HTML",
        reply_markup=dashboard_keyboard(),
    )


# ============================================================
# DASHBOARD
# ============================================================

async def dashboard_cmd(
    update,
    context,
):
    results = await asyncio.to_thread(
        scan_all
    )

    await update.message.reply_text(
        dashboard_message(
            results,
            update.effective_chat.id,
        ),
        parse_mode="HTML",
        reply_markup=dashboard_keyboard(),
    )


# ============================================================
# MARKET
# ============================================================

async def market_cmd(
    update,
    context,
):
    await dashboard_cmd(
        update,
        context,
    )


# ============================================================
# SEARCH
# ============================================================

async def search_cmd(
    update,
    context,
):
    results = await asyncio.to_thread(
        scan_all
    )

    ready_items = []

    for coin, result in results.items():

        if result.get("error"):
            continue

        if (
            result.get("stage") == "READY"
            and result.get("score", 0)
            >= MIN_SCORE_READY
            and result.get("rr") is not None
            and float(result.get("rr")) >= MIN_RR
        ):
            setup = save_ready_setup(
                coin,
                result,
            )

            if setup:
                ready_items.append(
                    (
                        result.get("score", 0),
                        coin,
                        result,
                        setup,
                    )
                )

    if not ready_items:

        await update.message.reply_text(
            (
                "🔎 <b>READY СЕТАПОВ НЕТ</b>\n\n"
                "TradeMind продолжает искать полный цикл:\n\n"
                "1H → Major Liquidity\n"
                "→ Sweep → 15M → 5M ILM\n\n"
                "❌ В середине движения не входим."
            ),
            parse_mode="HTML",
            reply_markup=dashboard_keyboard(),
        )

        return

    ready_items.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    for _, coin, result, setup in ready_items[:5]:

        await update.message.reply_text(
            ready_message(
                coin,
                result,
                setup,
            ),
            parse_mode="HTML",
            reply_markup=ready_keyboard(
                setup
            ),
        )


# ============================================================
# CHART
# ============================================================

async def chart_cmd(
    update,
    context,
):
    coin = "SOL"

    if (
        context.args
        and context.args[0].upper() in COINS
    ):
        coin = context.args[0].upper()

    await send_chart(
        update.message,
        coin,
    )


# ============================================================
# SUBSCRIBE
# ============================================================

async def sub_cmd(
    update,
    context,
):
    chat_id = update.effective_chat.id

    data = subscribers()

    if chat_id not in data:
        data.append(chat_id)
        save_subscribers(data)

    await update.message.reply_text(
        (
            "🔔 <b>УВЕДОМЛЕНИЯ ВКЛЮЧЕНЫ</b>\n\n"
            "Теперь автоматические уведомления "
            "приходят <b>только когда готов полный READY</b>.\n\n"
            "❌ SWEEP — нет\n"
            "❌ 15M — нет\n"
            "❌ WAIT — нет\n"
            "🟢 READY — да"
        ),
        parse_mode="HTML",
        reply_markup=dashboard_keyboard(),
    )


# ============================================================
# UNSUBSCRIBE
# ============================================================

async def unsub_cmd(
    update,
    context,
):
    chat_id = update.effective_chat.id

    save_subscribers([
        x
        for x in subscribers()
        if x != chat_id
    ])

    await update.message.reply_text(
        (
            "🔕 <b>УВЕДОМЛЕНИЯ ВЫКЛЮЧЕНЫ</b>\n\n"
            "Dashboard и ручной сканер продолжают работать."
        ),
        parse_mode="HTML",
        reply_markup=dashboard_keyboard(),
    )


# ============================================================
# ACTIVE
# ============================================================

async def active_cmd(
    update,
    context,
):
    chat_id = update.effective_chat.id

    await update.message.reply_text(
        active_message(chat_id),
        parse_mode="HTML",
        reply_markup=active_keyboard(chat_id),
    )


# ============================================================
# JOURNAL
# ============================================================

async def journal_cmd(
    update,
    context,
):
    chat_id = update.effective_chat.id

    await update.message.reply_text(
        journal_message(chat_id),
        parse_mode="HTML",
        reply_markup=journal_keyboard(),
    )


# ============================================================
# STATUS
# ============================================================

async def status_cmd(
    update,
    context,
):
    active = load_active_trades()

    active_count = len([
        x
        for x in active
        if x.get("status") == "OPEN"
    ])

    journal = load_journal()

    await update.message.reply_text(
        (
            f"⚙️ <b>TRADEMIND STATUS</b>\n\n"
            f"Version: <b>{escape(str(STRATEGY_VERSION))}</b>\n"
            f"Scanner: <b>{CHECK_INTERVAL}s</b>\n"
            f"Workers: <b>{SCAN_WORKERS}</b>\n"
            f"Coins: <b>{len(COINS)}</b>\n"
            f"Active global: <b>{active_count}</b>\n"
            f"Journal: <b>{len(journal)}</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "📐 Direction: <b>1H</b>\n"
            "💧 Liquidity: <b>1H Major + local cluster</b>\n"
            "🟠 Sweep: <b>internal only</b>\n"
            "🟡 Confirmation: <b>15M internal only</b>\n"
            "🎯 Trigger: <b>5M ILM</b>\n"
            "📊 Minimum RR: <b>1:2</b>\n"
            "🛑 SL buffer: <b>0.20%</b>\n"
            "❌ D1/W1: <b>OFF</b>\n"
            "❌ Daily limit: <b>OFF</b>\n"
            "⚙️ BingX: <b>OFF</b>\n\n"
            "🔔 <b>Уведомления:</b>\n"
            "только READY"
        ),
        parse_mode="HTML",
        reply_markup=dashboard_keyboard(),
    )


# ============================================================
# MONITOR — READY ONLY
# ============================================================

async def monitor(app):

    notification_state = (
        load_notification_state()
    )

    while True:

        started = (
            asyncio.get_running_loop().time()
        )

        try:

            results = await asyncio.to_thread(
                scan_all
            )

            # -----------------------------------------------
            # ACTIVE TRADES
            # -----------------------------------------------

            await monitor_active_trades(
                app,
                results,
            )

            # -----------------------------------------------
            # SIGNAL NOTIFICATIONS
            # -----------------------------------------------

            state_changed = False

            for coin, result in results.items():

                if result.get("error"):
                    continue

                stage = result.get(
                    "stage",
                    "WAIT",
                )

                if stage != "READY":
                    continue

                score = int(
                    result.get(
                        "score",
                        0,
                    )
                )

                if score < MIN_SCORE_READY:
                    continue

                rr = result.get("rr")

                if rr is None:
                    continue

                try:
                    rr = float(rr)
                except Exception:
                    continue

                if rr < MIN_RR:
                    continue

                # -------------------------------------------
                # СОЗДАЁМ SNAPSHOT
                # -------------------------------------------

                setup = save_ready_setup(
                    coin,
                    result,
                )

                if setup is None:
                    continue

                # -------------------------------------------
                # УНИКАЛЬНЫЙ ID
                # -------------------------------------------

                signal_key = (
                    setup["id"]
                )

                previous_key = (
                    notification_state.get(
                        coin
                    )
                )

                if previous_key == signal_key:
                    continue

                # -------------------------------------------
                # НОВЫЙ READY
                # -------------------------------------------

                notification_state[coin] = (
                    signal_key
                )

                state_changed = True

                print(
                    f"[READY] {coin} "
                    f"{setup['direction']} "
                    f"entry={setup['entry']} "
                    f"sl={setup['sl']} "
                    f"tp={setup['tp']} "
                    f"rr={setup['rr']} "
                    f"score={setup['score']}"
                )

                await broadcast_ready(
                    app,
                    coin,
                    result,
                    setup,
                )

            if state_changed:
                save_notification_state(
                    notification_state
                )

        except Exception as exc:
            print(
                "MONITOR ERROR:",
                exc,
            )

        elapsed = (
            asyncio.get_running_loop().time()
            - started
        )

        await asyncio.sleep(
            max(
                1,
                CHECK_INTERVAL - elapsed,
            )
        )


# ============================================================
# CALLBACK HELPER
# ============================================================

async def edit_query(
    query,
    text,
    keyboard=None,
):
    try:
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        return True

    except Exception:
        return False


# ============================================================
# CALLBACKS
# ============================================================

async def callbacks(
    update,
    context,
):
    query = update.callback_query

    await query.answer()

    data = query.data
    chat_id = query.message.chat_id

    # ========================================================
    # DASHBOARD
    # ========================================================

    if data in (
        "start",
        "dashboard",
    ):

        results = await asyncio.to_thread(
            scan_all
        )

        text = dashboard_message(
            results,
            chat_id,
        )

        if not await edit_query(
            query,
            text,
            dashboard_keyboard(),
        ):
            await query.message.reply_text(
                text,
                parse_mode="HTML",
                reply_markup=dashboard_keyboard(),
            )

        return

    # ========================================================
    # MARKET
    # ========================================================

    if data == "market":

        results = await asyncio.to_thread(
            scan_all
        )

        text = dashboard_message(
            results,
            chat_id,
        )

        if not await edit_query(
            query,
            text,
            dashboard_keyboard(),
        ):
            await query.message.reply_text(
                text,
                parse_mode="HTML",
                reply_markup=dashboard_keyboard(),
            )

        return

    # ========================================================
    # READY FILTER
    # ========================================================

    if data == "filter_ready":

        results = await asyncio.to_thread(
            scan_all
        )

        text = ready_filter_message(
            results
        )

        await edit_query(
            query,
            text,
            ready_filter_keyboard(
                results
            ),
        )

        return

    # ========================================================
    # SEARCH
    # ========================================================

    if data == "search":

        results = await asyncio.to_thread(
            scan_all
        )

        ready = []

        for coin, result in results.items():

            if result.get("error"):
                continue

            if (
                result.get("stage") == "READY"
                and result.get("score", 0)
                >= MIN_SCORE_READY
                and result.get("rr") is not None
                and float(result.get("rr"))
                >= MIN_RR
            ):

                setup = save_ready_setup(
                    coin,
                    result,
                )

                if setup:
                    ready.append(
                        (
                            result.get(
                                "score",
                                0,
                            ),
                            coin,
                            result,
                            setup,
                        )
                    )

        if not ready:

            await edit_query(
                query,
                (
                    "🔎 <b>READY СЕТАПОВ НЕТ</b>\n\n"
                    "TradeMind продолжает мониторинг."
                ),
                dashboard_keyboard(),
            )

            return

        ready.sort(
            key=lambda x: x[0],
            reverse=True,
        )

        _, coin, result, setup = ready[0]

        await edit_query(
            query,
            ready_message(
                coin,
                result,
                setup,
            ),
            ready_keyboard(
                setup
            ),
        )

        return

    # ========================================================
    # COIN
    # ========================================================

    if data.startswith("coin_"):

        coin = data.split(
            "_",
            1,
        )[1]

        if coin not in COINS:
            return

        result = await asyncio.to_thread(
            build_analysis,
            COINS[coin],
        )

        setup = None

        if (
            result.get("stage") == "READY"
            and result.get("score", 0)
            >= MIN_SCORE_READY
        ):
            setup = save_ready_setup(
                coin,
                result,
            )

        text = coin_message(
            coin,
            result,
        )

        if not await edit_query(
            query,
            text,
            coin_keyboard(
                coin,
                result,
                setup,
            ),
        ):
            await query.message.reply_text(
                text,
                parse_mode="HTML",
                reply_markup=coin_keyboard(
                    coin,
                    result,
                    setup,
                ),
            )

        return

    # ========================================================
    # ENTER
    # ========================================================

    if data.startswith("enter_"):

        setup_id = data.split(
            "_",
            1,
        )[1]

        pending = load_pending_setups()

        setup = pending.get(
            setup_id
        )

        if not setup:

            await edit_query(
                query,
                (
                    "⚠️ <b>СИГНАЛ НЕ НАЙДЕН</b>\n\n"
                    "Возможно, старый setup уже удалён."
                ),
                dashboard_keyboard(),
            )

            return

        trade, created = activate_trade(
            setup,
            chat_id,
        )

        if not created:

            await edit_query(
                query,
                (
                    "🟢 <b>СДЕЛКА УЖЕ АКТИВНА</b>\n\n"
                    f"💠 {setup.get('coin')}\n"
                    f"📐 {setup.get('direction')}\n\n"
                    f"Entry: <b>{format_price(setup.get('entry'))}</b>\n"
                    f"SL: <b>{format_price(setup.get('sl'))}</b>\n"
                    f"TP: <b>{format_price(setup.get('tp'))}</b>\n"
                    f"RR: <b>{format_rr(setup.get('rr'))}</b>"
                ),
                active_keyboard(chat_id),
            )

            return

        await edit_query(
            query,
            (
                "🟢 <b>СДЕЛКА ПРИНЯТА</b>\n\n"
                f"💠 <b>{trade.get('coin')}</b>\n"
                f"📐 {direction_icon(trade.get('direction'))} "
                f"<b>{trade.get('direction')}</b>\n\n"
                f"Entry: <b>{format_price(trade.get('entry'))}</b>\n"
                f"SL: <b>{format_price(trade.get('sl'))}</b>\n"
                f"TP: <b>{format_price(trade.get('tp'))}</b>\n"
                f"RR: <b>{format_rr(trade.get('rr'))}</b>\n\n"
                "📌 Snapshot сохранён.\n"
                "TradeMind теперь мониторит сделку."
            ),
            active_keyboard(chat_id),
        )

        return

    # ========================================================
    # CHART MENU
    # ========================================================

    if data == "charts":

        await edit_query(
            query,
            "📈 <b>ВЫБЕРИ МОНЕТУ</b>",
            chart_keyboard(),
        )

        return

    # ========================================================
    # CHART
    # ========================================================

    if data.startswith("chart_"):

        coin = data.split(
            "_",
            1,
        )[1]

        if coin not in COINS:
            return

        await send_chart(
            query.message,
            coin,
        )

        return

    # ========================================================
    # ACTIVE
    # ========================================================

    if data == "active":

        text = active_message(
            chat_id
        )

        if not await edit_query(
            query,
            text,
            active_keyboard(chat_id),
        ):
            await query.message.reply_text(
                text,
                parse_mode="HTML",
                reply_markup=active_keyboard(chat_id),
            )

        return

    # ========================================================
    # ACTIVE TRADE
    # ========================================================

    if data.startswith("active_trade_"):

        trade_id = data.split(
            "_",
            2,
        )[2]

        trades = user_active_trades(
            chat_id
        )

        trade = next(
            (
                x
                for x in trades
                if x.get("id") == trade_id
            ),
            None,
        )

        if not trade:

            await edit_query(
                query,
                "⚠️ Сделка больше не активна.",
                dashboard_keyboard(),
            )

            return

        coin = trade.get(
            "coin",
            "SOL",
        )

        result = await asyncio.to_thread(
            build_analysis,
            COINS[coin],
        )

        trade["last_price"] = result.get(
            "price",
            trade.get("last_price"),
        )

        await send_chart(
            query.message,
            coin,
            result=result,
            trade=trade,
        )

        return

    # ========================================================
    # JOURNAL
    # ========================================================

    if data == "journal":

        text = journal_message(
            chat_id
        )

        if not await edit_query(
            query,
            text,
            journal_keyboard(),
        ):
            await query.message.reply_text(
                text,
                parse_mode="HTML",
                reply_markup=journal_keyboard(),
            )

        return

    # ========================================================
    # NOTIFICATIONS
    # ========================================================

    if data == "notifications":

        if is_subscribed(chat_id):

            text = (
                "🔔 <b>УВЕДОМЛЕНИЯ ВКЛЮЧЕНЫ</b>\n\n"
                "Ты получаешь только:\n\n"
                "🟢 <b>READY</b>\n"
                "полный готовый сетап с Entry / SL / TP / RR.\n\n"
                "Промежуточные стадии:\n"
                "❌ SWEEP\n"
                "❌ 15M\n"
                "❌ WAIT"
            )

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔕 Выключить",
                        callback_data="unsubscribe",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Dashboard",
                        callback_data="dashboard",
                    ),
                ],
            ])

        else:

            text = (
                "🔕 <b>УВЕДОМЛЕНИЯ ВЫКЛЮЧЕНЫ</b>\n\n"
                "Можно включить уведомления "
                "только для готовых READY-сетапов."
            )

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔔 Включить",
                        callback_data="subscribe",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Dashboard",
                        callback_data="dashboard",
                    ),
                ],
            ])

        await edit_query(
            query,
            text,
            keyboard,
        )

        return

    # ========================================================
    # SUBSCRIBE
    # ========================================================

    if data == "subscribe":

        data_list = subscribers()

        if chat_id not in data_list:
            data_list.append(chat_id)
            save_subscribers(data_list)

        await edit_query(
            query,
            (
                "🔔 <b>READY-УВЕДОМЛЕНИЯ ВКЛЮЧЕНЫ</b>\n\n"
                "Буду присылать сообщение "
                "только после полного подтверждения."
            ),
            dashboard_keyboard(),
        )

        return

    # ========================================================
    # UNSUBSCRIBE
    # ========================================================

    if data == "unsubscribe":

        save_subscribers([
            x
            for x in subscribers()
            if x != chat_id
        ])

        await edit_query(
            query,
            (
                "🔕 <b>УВЕДОМЛЕНИЯ ВЫКЛЮЧЕНЫ</b>\n\n"
                "Ручной Dashboard продолжает работать."
            ),
            dashboard_keyboard(),
        )

        return

    # ========================================================
    # STATUS
    # ========================================================

    if data == "status":

        active = load_active_trades()

        active_count = len([
            x
            for x in active
            if x.get("status") == "OPEN"
        ])

        journal = load_journal()

        await edit_query(
            query,
            (
                f"⚙️ <b>TRADEMIND STATUS</b>\n\n"
                f"Version: <b>{escape(str(STRATEGY_VERSION))}</b>\n"
                f"Scanner: <b>{CHECK_INTERVAL}s</b>\n"
                f"Workers: <b>{SCAN_WORKERS}</b>\n"
                f"Coins: <b>{len(COINS)}</b>\n"
                f"Active: <b>{active_count}</b>\n"
                f"Journal: <b>{len(journal)}</b>\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "📐 1H direction\n"
                "💧 1H Major Liquidity\n"
                "🟠 Sweep — internal\n"
                "🟡 15M — internal\n"
                "🎯 5M ILM\n"
                "📊 RR ≥ 1:2\n"
                "🛑 SL buffer 0.20%\n\n"
                "🔔 <b>Уведомления:</b> READY ONLY\n"
                "❌ D1/W1 OFF\n"
                "❌ Daily limit OFF\n"
                "⚙️ BingX OFF"
            ),
            dashboard_keyboard(),
        )

        return


# ============================================================
# POST INIT
# ============================================================

async def post_init(
    application,
):
    commands = [
        (
            "start",
            "TradeMind Dashboard",
        ),
        (
            "market",
            "Рынок",
        ),
        (
            "search",
            "Поиск READY",
        ),
        (
            "chart",
            "График",
        ),
        (
            "active",
            "Активные сделки",
        ),
        (
            "journal",
            "Журнал",
        ),
        (
            "status",
            "Статус",
        ),
        (
            "subscribe",
            "Включить READY уведомления",
        ),
        (
            "unsubscribe",
            "Выключить уведомления",
        ),
    ]

    await application.bot.set_my_commands([
        BotCommand(
            command,
            description,
        )
        for command, description in commands
    ])

    application.create_task(
        monitor(
            application
        )
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN не найден"
        )

    application = (
        Application
        .builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    handlers = [
        (
            "start",
            start,
        ),
        (
            "market",
            market_cmd,
        ),
        (
            "search",
            search_cmd,
        ),
        (
            "chart",
            chart_cmd,
        ),
        (
            "active",
            active_cmd,
        ),
        (
            "journal",
            journal_cmd,
        ),
        (
            "status",
            status_cmd,
        ),
        (
            "subscribe",
            sub_cmd,
        ),
        (
            "unsubscribe",
            unsub_cmd,
        ),
    ]

    for command, handler in handlers:

        application.add_handler(
            CommandHandler(
                command,
                handler,
            )
        )

    application.add_handler(
        CallbackQueryHandler(
            callbacks
        )
    )

    print(
        f"TradeMind {STRATEGY_VERSION} started"
    )

    print(
        f"Monitoring {len(COINS)} coins:"
    )

    print(
        ", ".join(COINS.keys())
    )

    application.run_polling()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()