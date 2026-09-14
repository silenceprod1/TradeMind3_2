# ============================================================
# TRADEMIND 5.0 — BOT.PY
# Binance Spot market data → TradeMind 5.0 strategy
# Telegram bot
#
# FLOW:
# D1 → W1 fallback → 1H → Major Liquidity → Sweep
# → 15M confirmation → 5M V/L → Entry → SL → structural TP
#
# BingX AUTO intentionally remains OFF by default.
# ============================================================

import asyncio
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

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

from strategy import analyze


# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("BOT_TOKEN")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))
SCAN_WORKERS = int(os.getenv("SCAN_WORKERS", "9"))

BINANCE_BASE = "https://api.binance.com"

# BingX is NOT used for opening trades in this version.
BINGX_MODE = os.getenv("BINGX_MODE", "OFF").upper()

MIN_SCORE = 80

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

SUBSCRIBERS_FILE = "subscribers.json"
STATE_FILE = "monitor_state.json"


# ============================================================
# JSON STATE
# ============================================================

def load_json(filename, default):
    try:
        if not os.path.exists(filename):
            return default

        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)

        return data

    except Exception as exc:
        print(f"JSON LOAD ERROR {filename}: {exc}")
        return default


def save_json(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
            )

    except Exception as exc:
        print(f"JSON SAVE ERROR {filename}: {exc}")


def load_subscribers():
    return load_json(SUBSCRIBERS_FILE, [])


def save_subscribers(data):
    save_json(SUBSCRIBERS_FILE, data)


def default_state():
    return {
        "daily_date": None,
        "daily_trades": 0,
        "daily_stop": False,

        "active_coin": None,
        "active_symbol": None,
        "active_direction": None,
        "active_entry": None,
        "active_sl": None,
        "active_tp": None,
        "active_rr": None,
        "active_score": None,
        "active_stage": None,

        "last_signal_key": None,

        "coins": {},

        "last_scan": None,
    }


def load_state():
    state = load_json(
        STATE_FILE,
        default_state(),
    )

    if not isinstance(state, dict):
        state = default_state()

    state.setdefault("coins", {})

    return state


def save_state(state):
    save_json(
        STATE_FILE,
        state,
    )


# ============================================================
# BINANCE API
# ============================================================

def binance_request(path, params=None):
    if params is None:
        params = {}

    query = urllib.parse.urlencode(params)

    url = BINANCE_BASE + path

    if query:
        url += "?" + query

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "TradeMind/5.0",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=15,
    ) as response:

        raw = response.read()

    return json.loads(raw.decode("utf-8"))


def get_binance_price(symbol):
    data = binance_request(
        "/api/v3/ticker/price",
        {
            "symbol": symbol,
        },
    )

    return float(data["price"])


def get_binance_klines(
    symbol,
    interval,
    limit=300,
):
    data = binance_request(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        },
    )

    candles = []

    for row in data:

        if len(row) < 6:
            continue

        try:

            candles.append({
                "open_time": int(row[0]),

                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),

                "volume": float(row[5]),

                "close_time": int(row[6]),
            })

        except Exception:
            continue

    return candles


# ============================================================
# DATA NORMALIZATION
# ============================================================

def normalize_candles(candles):

    if not candles:
        return []

    result = []

    for candle in candles:

        if isinstance(candle, dict):

            try:

                result.append({
                    "open_time": candle.get(
                        "open_time",
                        candle.get(
                            "timestamp",
                            candle.get(
                                "time",
                                0,
                            ),
                        ),
                    ),

                    "open": float(
                        candle.get(
                            "open",
                            candle.get("o"),
                        )
                    ),

                    "high": float(
                        candle.get(
                            "high",
                            candle.get("h"),
                        )
                    ),

                    "low": float(
                        candle.get(
                            "low",
                            candle.get("l"),
                        )
                    ),

                    "close": float(
                        candle.get(
                            "close",
                            candle.get("c"),
                        )
                    ),

                    "volume": float(
                        candle.get(
                            "volume",
                            candle.get("v", 0),
                        )
                    ),

                })

            except Exception:
                continue

        elif isinstance(candle, (list, tuple)):

            if len(candle) < 6:
                continue

            try:

                result.append({
                    "open_time": int(candle[0]),
                    "open": float(candle[1]),
                    "high": float(candle[2]),
                    "low": float(candle[3]),
                    "close": float(candle[4]),
                    "volume": float(candle[5]),
                })

            except Exception:
                continue

    return result


# ============================================================
# FULL MARKET DATA FOR TRADEMIND 5.0
# ============================================================

def get_full_market_data(symbol):

    print(
        f"[DATA] Loading "
        f"{symbol}: D1 / W1 / 1H / 15M / 5M"
    )

    price = get_binance_price(symbol)

    candles_d1 = normalize_candles(
        get_binance_klines(
            symbol,
            "1d",
            250,
        )
    )

    candles_w1 = normalize_candles(
        get_binance_klines(
            symbol,
            "1w",
            100,
        )
    )

    candles_1h = normalize_candles(
        get_binance_klines(
            symbol,
            "1h",
            300,
        )
    )

    candles_15m = normalize_candles(
        get_binance_klines(
            symbol,
            "15m",
            300,
        )
    )

    candles_5m = normalize_candles(
        get_binance_klines(
            symbol,
            "5m",
            300,
        )
    )

    if not candles_d1:
        raise RuntimeError(
            f"{symbol}: D1 data unavailable"
        )

    if not candles_1h:
        raise RuntimeError(
            f"{symbol}: 1H data unavailable"
        )

    if not candles_15m:
        raise RuntimeError(
            f"{symbol}: 15M data unavailable"
        )

    if not candles_5m:
        raise RuntimeError(
            f"{symbol}: 5M data unavailable"
        )

    return {
        "symbol": symbol,
        "price": price,

        "candles_d1": candles_d1,
        "candles_w1": candles_w1,

        "candles_1h": candles_1h,
        "candles_15m": candles_15m,
        "candles_5m": candles_5m,
    }


# ============================================================
# STRATEGY
# ============================================================

def run_strategy(symbol):

    data = get_full_market_data(symbol)

    price = data["price"]

    candles_d1 = data["candles_d1"]
    candles_w1 = data["candles_w1"]

    candles_1h = data["candles_1h"]
    candles_15m = data["candles_15m"]
    candles_5m = data["candles_5m"]

    # --------------------------------------------------------
    # IMPORTANT:
    # TradeMind 5.0 receives all required timeframes.
    # --------------------------------------------------------

    try:

        result = analyze(
            candles_d1=candles_d1,

            candles_w1=candles_w1,

            candles_1h=candles_1h,

            candles_15m=candles_15m,

            candles_5m=candles_5m,

            current_price=price,

            price=price,
        )

    except TypeError:

        # Compatibility with versions of strategy.py
        # that use only price= instead of current_price=.

        result = analyze(
            candles_d1=candles_d1,

            candles_w1=candles_w1,

            candles_1h=candles_1h,

            candles_15m=candles_15m,

            candles_5m=candles_5m,

            price=price,
        )

    if not isinstance(result, dict):
        result = {}

    result.update({
        "symbol": symbol,
        "price": price,

        "candles_d1": candles_d1,
        "candles_w1": candles_w1,

        "candles_1h": candles_1h,
        "candles_15m": candles_15m,
        "candles_5m": candles_5m,
    })

    return result


# ============================================================
# SCANNING
# ============================================================

def scan_one_coin(coin, symbol):

    try:

        result = run_strategy(symbol)

        return (
            coin,
            result,
        )

    except Exception as exc:

        print(
            f"[SCAN ERROR] "
            f"{coin} / {symbol}: {exc}"
        )

        return (
            coin,
            {
                "error": str(exc),
                "symbol": symbol,
                "price": None,
                "stage": "ERROR",
                "score": 0,
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

            for coin, symbol in COINS.items()
        }

        for future in as_completed(futures):

            coin = futures[future]

            try:

                c, result = future.result()

                results[c] = result

            except Exception as exc:

                print(
                    f"[WORKER ERROR] "
                    f"{coin}: {exc}"
                )

                results[coin] = {
                    "error": str(exc),
                    "stage": "ERROR",
                    "score": 0,
                }

    return {
        coin: results.get(
            coin,
            {
                "error": "No result",
                "stage": "ERROR",
                "score": 0,
            },
        )

        for coin in COINS
    }


async def scan_all_coins_async():

    return await asyncio.to_thread(
        scan_all_coins
    )


# ============================================================
# FORMATTING
# ============================================================

def format_price(price):

    if price is None:
        return "N/A"

    try:
        value = float(price)
    except Exception:
        return "N/A"

    if value >= 1000:
        return f"${value:,.2f}"

    if value >= 1:
        return f"${value:,.4f}"

    return f"${value:,.6f}"


def format_rr(rr):

    if rr is None:
        return "N/A"

    try:

        value = float(rr)

        return f"1:{value:.2f}"

    except Exception:
        return "N/A"


def stage_icon(stage):

    return {
        "READY": "🟢",

        "CONFIRMED": "🟡",
        "15M_CONFIRMED": "🟡",

        "SWEPT": "🟠",
        "SWEEP": "🟠",

        "WAIT": "⚪",
        "ERROR": "❌",
    }.get(
        str(stage).upper(),
        "⚪",
    )


def stage_text(stage):

    return {
        "READY": "МОЖНО ВХОДИТЬ",

        "CONFIRMED": "5M подтверждение",
        "15M_CONFIRMED": "15M подтверждение",

        "SWEPT": "SWEEP обнаружен",
        "SWEEP": "SWEEP обнаружен",

        "WAIT": "Ожидание",

        "ERROR": "Ошибка",
    }.get(
        str(stage).upper(),
        "Ожидание",
    )


def direction_icon(direction):

    if direction == "LONG":
        return "🟢"

    if direction == "SHORT":
        return "🔴"

    return "⚪"


# ============================================================
# LIQUIDITY DISPLAY
# ============================================================

def format_levels(levels, price):

    if not levels:
        return (
            "💧 Крупные уровни не найдены."
        )

    try:
        current = float(price)
    except Exception:
        current = 0

    lines = []

    for level in levels:

        try:

            lp = float(
                level.get(
                    "price",
                    level.get(
                        "level",
                        0,
                    ),
                )
            )

        except Exception:
            continue

        level_type = str(
            level.get(
                "type",
                level.get(
                    "liquidity_type",
                    "",
                ),
            )
        ).upper()

        distance = 0

        if current:
            distance = (
                abs(lp - current)
                / current
                * 100
            )

        if (
            "HIGH" in level_type
            or "SELL" in level_type
        ):
            icon = "🔴"

        else:
            icon = "🟢"

        lines.append(
            f"{icon} "
            f"{level_type or 'LEVEL'}: "
            f"{format_price(lp)} "
            f"({distance:.2f}%)"
        )

    if not lines:
        return (
            "💧 Крупные уровни не найдены."
        )

    return "\n".join(lines)


# ============================================================
# RESULT HELPERS
# ============================================================

def get_stage(result):

    stage = result.get(
        "stage",
        "WAIT",
    )

    if stage is None:
        return "WAIT"

    return str(stage).upper()


def is_ready(result):

    if not result:
        return False

    if result.get("error"):
        return False

    stage = get_stage(result)

    score = result.get(
        "score",
        0,
    )

    try:
        score = float(score)
    except Exception:
        score = 0

    return (
        stage == "READY"
        and score >= MIN_SCORE
        and result.get("direction")
        in {"LONG", "SHORT"}
        and result.get("entry") is not None
        and result.get("sl") is not None
        and result.get("tp") is not None
    )


def find_best_ready(results):

    ready = []

    for coin, result in results.items():

        if not is_ready(result):
            continue

        try:
            score = float(
                result.get(
                    "score",
                    0,
                )
            )
        except Exception:
            score = 0

        ready.append(
            (
                score,
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


# ============================================================
# TELEGRAM KEYBOARDS
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
# MARKET MESSAGE
# ============================================================

def build_market_message(results):

    lines = [
        "📊 <b>TRADEMIND 5.0 — РЫНОК</b>",
        "",
    ]

    for coin in COINS:

        result = results.get(
            coin,
            {},
        )

        if result.get("error"):

            lines += [
                f"❌ <b>{coin}</b> — ошибка",
                "",
            ]

            continue

        price = result.get(
            "price"
        )

        stage = get_stage(result)

        score = result.get(
            "score",
            0,
        )

        direction = result.get(
            "direction"
        )

        direction_text = ""

        if direction:
            direction_text = (
                f" {direction_icon(direction)} "
                f"{direction}"
            )

        lines += [
            (
                f"💠 <b>{coin}</b> "
                f"{format_price(price)}"
            ),

            (
                f"{stage_icon(stage)} "
                f"{stage_text(stage)}"
                f"{direction_text}"
            ),

            f"⭐ Score: <b>{score}/100</b>",
            "",
        ]

    lines += [
        "━━━━━━━━━━━━━━",
        "D1 → 1H → Liquidity → Sweep",
        "→ 15M → 5M → Entry",
        "",
        "❌ В середине движения не входим.",
        "❌ Нет подтверждения → нет входа.",
    ]

    return "\n".join(lines)


# ============================================================
# LEVELS MESSAGE
# ============================================================

def build_levels_message(results):

    lines = [
        "💧 <b>TRADEMIND 5.0 — MAJOR LIQUIDITY</b>",
        "",
        "Основные уровни берём с 1H.",
        "",
    ]

    for coin in COINS:

        result = results.get(
            coin,
            {},
        )

        if result.get("error"):
            continue

        levels = result.get(
            "major_levels",
            result.get(
                "liquidity_levels",
                [],
            ),
        )

        lines += [
            f"💠 <b>{coin}</b>",
            format_levels(
                levels,
                result.get("price"),
            ),
            "",
            "────────────",
            "",
        ]

    return "\n".join(lines)


# ============================================================
# SEARCH MESSAGE
# ============================================================

def build_search_message(results):

    ready = find_best_ready(
        results
    )

    if ready:

        score, coin, result = ready

        direction = result.get(
            "direction"
        )

        return "\n".join([
            "🚨 <b>TRADEMIND 5.0 — ГОТОВЫЙ СЕТАП</b>",
            "",

            f"💠 Монета: <b>{coin}</b>",

            (
                f"📐 Направление: "
                f"{direction_icon(direction)} "
                f"<b>{direction}</b>"
            ),

            f"⭐ Score: <b>{score:.0f}/100</b>",

            "",
            "🟢 <b>МОЖНО ВХОДИТЬ</b>",
            "",

            (
                f"Entry: "
                f"<b>{format_price(result.get('entry'))}</b>"
            ),

            (
                f"SL: "
                f"<b>{format_price(result.get('sl'))}</b>"
            ),

            (
                f"TP: "
                f"<b>{format_price(result.get('tp'))}</b>"
            ),

            (
                f"RR: "
                f"<b>{format_rr(result.get('rr'))}</b>"
            ),

            "",
            "🎯 TP — структурный уровень.",
            "❌ Фиксированный 1:2 больше не обязателен.",
        ])

    lines = [
        "🔎 <b>TRADEMIND 5.0 — ПОИСК СЕТАПА</b>",
        "",
        "❌ <b>ГОТОВОГО ВХОДА СЕЙЧАС НЕТ</b>",
        "",
    ]

    for coin in COINS:

        result = results.get(
            coin,
            {},
        )

        if result.get("error"):

            lines.append(
                f"❌ {coin}: ошибка"
            )

            continue

        stage = get_stage(result)

        score = result.get(
            "score",
            0,
        )

        direction = result.get(
            "direction"
        )

        direction_text = ""

        if direction:
            direction_text = (
                f" — {direction}"
            )

        lines.append(
            f"{stage_icon(stage)} "
            f"<b>{coin}</b>: "
            f"{stage_text(stage)} "
            f"— {score}/100"
            f"{direction_text}"
        )

    lines += [
        "",
        "Ждём → D1 → 1H → Major Liquidity",
        "→ Sweep → 15M → 5M.",
        "",
        "❌ В середине движения не входим.",
        "❌ Нет подтверждения → нет входа.",
    ]

    return "\n".join(lines)


# ============================================================
# SOL MESSAGE
# ============================================================

def build_sol_message(result):

    if result.get("error"):

        return (
            "❌ <b>SOL</b>\n\n"
            f"Ошибка: "
            f"{result.get('error')}"
        )

    price = result.get(
        "price"
    )

    stage = get_stage(result)

    score = result.get(
        "score",
        0,
    )

    direction = result.get(
        "direction"
    )

    lines = [
        "📈 <b>TRADEMIND 5.0 — SOL</b>",
        "",
        f"💰 Цена: <b>{format_price(price)}</b>",
        (
            f"{stage_icon(stage)} "
            f"<b>{stage_text(stage)}</b>"
        ),
        f"⭐ Score: <b>{score}/100</b>",
    ]

    if direction:

        lines += [
            "",
            (
                f"📐 Направление: "
                f"{direction_icon(direction)} "
                f"<b>{direction}</b>"
            ),
        ]

    levels = result.get(
        "major_levels",
        result.get(
            "liquidity_levels",
            [],
        ),
    )

    lines += [
        "",
        "💧 <b>MAJOR LIQUIDITY</b>",
        format_levels(
            levels,
            price,
        ),
    ]

    if result.get("entry") is not None:

        lines += [
            "",
            "🎯 <b>СЕТАП</b>",

            (
                f"Entry: "
                f"<b>{format_price(result.get('entry'))}</b>"
            ),

            (
                f"SL: "
                f"<b>{format_price(result.get('sl'))}</b>"
            ),

            (
                f"TP: "
                f"<b>{format_price(result.get('tp'))}</b>"
            ),

            (
                f"RR: "
                f"<b>{format_rr(result.get('rr'))}</b>"
            ),
        ]

        if result.get(
            "tp_reason"
        ):

            lines.append(
                f"🎯 {result.get('tp_reason')}"
            )

    reason = result.get(
        "reason"
    )

    if reason:

        lines += [
            "",
            "🧠 <b>Причина:</b>",
            str(reason),
        ]

    return "\n".join(lines)


# ============================================================
# SETUP KEY
# ============================================================

def make_setup_key(
    coin,
    result,
):

    return "|".join([
        str(coin),
        str(
            result.get(
                "direction"
            )
        ),
        str(
            result.get(
                "entry"
            )
        ),
        str(
            result.get(
                "sl"
            )
        ),
        str(
            result.get(
                "tp"
            )
        ),
    ])


# ============================================================
# SUBSCRIBER BROADCAST
# ============================================================

async def broadcast(
    application,
    text,
):

    subscribers = load_subscribers()

    if not subscribers:
        return

    async def send_one(chat_id):

        try:

            await application.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
            )

        except Exception as exc:

            print(
                f"[TELEGRAM ERROR] "
                f"{chat_id}: {exc}"
            )

    await asyncio.gather(
        *[
            send_one(chat_id)
            for chat_id in subscribers
        ],
        return_exceptions=True,
    )


# ============================================================
# STAGE ALERT
# ============================================================

def build_stage_alert(
    coin,
    result,
):

    stage = get_stage(result)

    direction = result.get(
        "direction"
    )

    price = result.get(
        "price"
    )

    if stage in {
        "SWEPT",
        "SWEEP",
    }:

        return "\n".join([
            "🔎 <b>TRADEMIND 5.0 — SWEEP</b>",
            "",
            f"💠 {coin}",
            (
                f"📐 {direction_icon(direction)} "
                f"<b>{direction}</b>"
            ),
            f"💰 Цена: <b>{format_price(price)}</b>",
            "",
            "💧 Крупная ликвидность снята.",
            "⏳ Ждём 15M confirmation.",
            "❌ ВХОД ПОКА ЗАПРЕЩЁН.",
        ])

    if stage in {
        "CONFIRMED",
        "15M_CONFIRMED",
    }:

        return "\n".join([
            "🟡 <b>TRADEMIND 5.0 — 15M CONFIRMATION</b>",
            "",
            f"💠 {coin}",
            (
                f"📐 {direction_icon(direction)} "
                f"<b>{direction}</b>"
            ),
            "",
            "✅ D1 / 1H направление",
            "✅ Major Liquidity",
            "✅ Sweep",
            "✅ 15M confirmation",
            "",
            "⏳ Ждём 5M trigger.",
            "❌ Вход пока запрещён.",
        ])

    return None


# ============================================================
# READY ALERT
# ============================================================

def build_ready_alert(
    coin,
    result,
):

    direction = result.get(
        "direction"
    )

    score = result.get(
        "score",
        0,
    )

    return "\n".join([
        "🚨 <b>TRADEMIND 5.0 — ГОТОВЫЙ СЕТАП</b>",
        "",
        f"💠 Монета: <b>{coin}</b>",
        (
            f"📐 Направление: "
            f"{direction_icon(direction)} "
            f"<b>{direction}</b>"
        ),
        f"⭐ Score: <b>{score}/100</b>",
        "",
        "🟢 <b>МОЖНО ВХОДИТЬ</b>",
        "",
        (
            f"Entry: "
            f"<b>{format_price(result.get('entry'))}</b>"
        ),
        (
            f"SL: "
            f"<b>{format_price(result.get('sl'))}</b>"
        ),
        (
            f"TP: "
            f"<b>{format_price(result.get('tp'))}</b>"
        ),
        (
            f"RR: "
            f"<b>{format_rr(result.get('rr'))}</b>"
        ),
        "",
        "🎯 TP = структурный уровень.",
        "❌ Не фиксированный 1:2.",
        "",
        "⚠️ BingX AUTO: OFF",
    ])


# ============================================================
# MONITOR
# ============================================================

async def monitor(
    application,
):

    print(
        "===================================="
    )

    print(
        "TradeMind 5.0 monitor started."
    )

    print(
        f"Interval: {CHECK_INTERVAL}s"
    )

    print(
        f"Workers: {SCAN_WORKERS}"
    )

    print(
        f"Coins: {', '.join(COINS.keys())}"
    )

    print(
        f"BingX mode: {BINGX_MODE}"
    )

    print(
        "===================================="
    )

    while True:

        started = time.monotonic()

        try:

            state = load_state()

            today = datetime.now(
                timezone.utc
            ).date().isoformat()

            # ------------------------------------------------
            # NEW DAY
            # ------------------------------------------------

            if state.get(
                "daily_date"
            ) != today:

                state["daily_date"] = today

                state["daily_trades"] = 0

                state["daily_stop"] = False

                state["active_coin"] = None

                state["active_symbol"] = None

                state["active_direction"] = None

                state["active_entry"] = None

                state["active_sl"] = None

                state["active_tp"] = None

                state["active_rr"] = None

                state["active_score"] = None

                state["active_stage"] = None

                state["last_signal_key"] = None

            # ------------------------------------------------
            # DAILY LIMIT
            # ------------------------------------------------

            if state.get(
                "daily_trades",
                0,
            ) >= 2:

                state["daily_stop"] = True

            # ------------------------------------------------
            # SCAN
            # ------------------------------------------------

            results = await scan_all_coins_async()

            state["last_scan"] = (
                datetime.now(
                    timezone.utc
                ).isoformat()
            )

            # ------------------------------------------------
            # READY SETUP
            # ------------------------------------------------

            ready = find_best_ready(
                results
            )

            if (
                ready
                and not state.get(
                    "daily_stop"
                )
            ):

                score, coin, result = ready

                setup_key = make_setup_key(
                    coin,
                    result,
                )

                # Only alert once for exactly
                # the same setup.

                if (
                    state.get(
                        "last_signal_key"
                    )
                    != setup_key
                ):

                    text = build_ready_alert(
                        coin,
                        result,
                    )

                    await broadcast(
                        application,
                        text,
                    )

                    state["last_signal_key"] = (
                        setup_key
                    )

                    state["active_coin"] = coin

                    state["active_symbol"] = (
                        result.get(
                            "symbol"
                        )
                    )

                    state["active_direction"] = (
                        result.get(
                            "direction"
                        )
                    )

                    state["active_entry"] = (
                        result.get(
                            "entry"
                        )
                    )

                    state["active_sl"] = (
                        result.get(
                            "sl"
                        )
                    )

                    state["active_tp"] = (
                        result.get(
                            "tp"
                        )
                    )

                    state["active_rr"] = (
                        result.get(
                            "rr"
                        )
                    )

                    state["active_score"] = (
                        score
                    )

                    state["active_stage"] = (
                        "READY"
                    )

            # ------------------------------------------------
            # EARLY STAGES
            # ------------------------------------------------

            for coin, result in results.items():

                if result.get(
                    "error"
                ):
                    continue

                stage = get_stage(
                    result
                )

                direction = result.get(
                    "direction"
                )

                coin_state = state[
                    "coins"
                ].setdefault(
                    coin,
                    {},
                )

                # --------------------------------------------
                # SWEEP
                # --------------------------------------------

                sweep = result.get(
                    "sweep"
                )

                if sweep:

                    sweep_price = (
                        sweep.get(
                            "price"
                        )
                        or sweep.get(
                            "level"
                        )
                        or sweep.get(
                            "sweep_price"
                        )
                    )

                    sweep_time = (
                        sweep.get(
                            "open_time"
                        )
                        or sweep.get(
                            "timestamp"
                        )
                        or sweep.get(
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

                        text = (
                            build_stage_alert(
                                coin,
                                result,
                            )
                        )

                        if text:

                            await broadcast(
                                application,
                                text,
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
                        or result.get(
                            "confirmation_time"
                        )
                        or result.get(
                            "confirmation_time_15m"
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

                        text = (
                            build_stage_alert(
                                coin,
                                result,
                            )
                        )

                        if text:

                            await broadcast(
                                application,
                                text,
                            )

            save_state(
                state
            )

        except Exception as exc:

            print(
                f"[MONITOR ERROR] {exc}"
            )

        elapsed = (
            time.monotonic()
            - started
        )

        sleep_time = max(
            1,
            CHECK_INTERVAL
            - elapsed,
        )

        await asyncio.sleep(
            sleep_time
        )


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "\n".join([
            "🤖 <b>TRADEMIND 5.0</b>",
            "",
            "Автоматический анализ:",
            "",
            "1️⃣ D1 — направление",
            "2️⃣ 1H — подтверждение",
            "3️⃣ 1H — Major Liquidity",
            "4️⃣ Sweep",
            "5️⃣ 15M confirmation",
            "6️⃣ 5M V/L trigger",
            "7️⃣ Entry",
            "8️⃣ SL за sweep",
            "9️⃣ TP по структуре",
            "",
            f"Монеты: <b>{len(COINS)}</b>",
            f"Сканирование: <b>{CHECK_INTERVAL} сек.</b>",
            "",
            "BingX AUTO: <b>OFF</b>",
        ]),
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


async def market_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "⏳ Сканирую рынок..."
    )

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


async def levels_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "⏳ Получаю Major Liquidity..."
    )

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


async def search_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "🔎 Ищу сетап..."
    )

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


async def sol_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    try:

        result = await asyncio.to_thread(
            run_strategy,
            "SOLUSDT",
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
            (
                "❌ <b>Ошибка SOL</b>\n\n"
                f"{exc}"
            ),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    state = load_state()

    daily_trades = int(
        state.get(
            "daily_trades",
            0,
        )
    )

    daily_stop = state.get(
        "daily_stop",
        False,
    )

    active_coin = state.get(
        "active_coin"
    )

    if active_coin:

        text = "\n".join([
            "📊 <b>TRADEMIND 5.0 — СТАТУС</b>",
            "",
            f"🟢 Активный сетап: <b>{active_coin}</b>",
            (
                f"Направление: "
                f"<b>{state.get('active_direction')}</b>"
            ),
            (
                f"Stage: "
                f"<b>{state.get('active_stage')}</b>"
            ),
            (
                f"Score: "
                f"<b>{state.get('active_score')}/100</b>"
            ),
            "",
            (
                f"Entry: "
                f"<b>{format_price(state.get('active_entry'))}</b>"
            ),
            (
                f"SL: "
                f"<b>{format_price(state.get('active_sl'))}</b>"
            ),
            (
                f"TP: "
                f"<b>{format_price(state.get('active_tp'))}</b>"
            ),
            (
                f"RR: "
                f"<b>{format_rr(state.get('active_rr'))}</b>"
            ),
            "",
            (
                f"Сделок сегодня: "
                f"<b>{daily_trades}/2</b>"
            ),
            (
                f"Daily stop: "
                f"<b>{'YES' if daily_stop else 'NO'}</b>"
            ),
            "",
            "BingX: <b>OFF</b>",
        ])

    else:

        text = "\n".join([
            "📊 <b>TRADEMIND 5.0 — СТАТУС</b>",
            "",
            "🟢 Активного сетапа нет.",
            "",
            (
                f"Сделок сегодня: "
                f"<b>{daily_trades}/2</b>"
            ),
            (
                f"Daily stop: "
                f"<b>{'YES' if daily_stop else 'NO'}</b>"
            ),
            "",
            (
                f"Сканирование: "
                f"<b>{CHECK_INTERVAL} сек.</b>"
            ),
            "",
            "BingX: <b>OFF</b>",
        ])

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


async def subscribe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    subscribers = load_subscribers()

    chat_id = update.effective_chat.id

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
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    subscribers = load_subscribers()

    chat_id = update.effective_chat.id

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


async def journal_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(
        "\n".join([
            "📒 <b>TRADEMIND — ЖУРНАЛ</b>",
            "",
            "Журнал сетапов будет использовать",
            "monitor_state.json.",
            "",
            "Следующий этап — автоматическая",
            "фиксация результата Entry/SL/TP.",
        ]),
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


# ============================================================
# CALLBACKS
# ============================================================

async def callbacks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    data = query.data

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    if data == "start":

        await query.edit_message_text(
            "\n".join([
                "🤖 <b>TRADEMIND 5.0</b>",
                "",
                "D1 → 1H → Liquidity",
                "→ Sweep → 15M → 5M",
                "",
                "BingX AUTO: <b>OFF</b>",
            ]),
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )

        return

    # --------------------------------------------------------
    # MARKET
    # --------------------------------------------------------

    if data == "market":

        await query.edit_message_text(
            "⏳ Сканирую рынок..."
        )

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

        return

    # --------------------------------------------------------
    # LEVELS
    # --------------------------------------------------------

    if data == "levels":

        await query.edit_message_text(
            "⏳ Получаю Major Liquidity..."
        )

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

        return

    # --------------------------------------------------------
    # SEARCH
    # --------------------------------------------------------

    if data == "search":

        await query.edit_message_text(
            "🔎 Ищу сетап..."
        )

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

        return

    # --------------------------------------------------------
    # SOL
    # --------------------------------------------------------

    if data == "sol":

        try:

            result = await asyncio.to_thread(
                run_strategy,
                "SOLUSDT",
            )

            await query.edit_message_text(
                build_sol_message(
                    result
                ),
                parse_mode="HTML",
                reply_markup=back_keyboard(),
            )

        except Exception as exc:

            await query.edit_message_text(
                (
                    "❌ <b>Ошибка SOL</b>\n\n"
                    f"{exc}"
                ),
                parse_mode="HTML",
                reply_markup=back_keyboard(),
            )

        return

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    if data == "status":

        state = load_state()

        text = "\n".join([
            "📊 <b>TRADEMIND 5.0</b>",
            "",
            (
                f"Active: "
                f"<b>{state.get('active_coin') or 'нет'}</b>"
            ),
            (
                f"Direction: "
                f"<b>{state.get('active_direction') or '—'}</b>"
            ),
            (
                f"Stage: "
                f"<b>{state.get('active_stage') or '—'}</b>"
            ),
            (
                f"Score: "
                f"<b>{state.get('active_score') or '—'}</b>"
            ),
            "",
            (
                f"Сегодня: "
                f"<b>{state.get('daily_trades', 0)}/2</b>"
            ),
            (
                f"Daily stop: "
                f"<b>{'YES' if state.get('daily_stop') else 'NO'}</b>"
            ),
            "",
            "BingX: <b>OFF</b>",
        ])

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

        return

    # --------------------------------------------------------
    # SUBSCRIBE
    # --------------------------------------------------------

    if data == "subscribe":

        subscribers = load_subscribers()

        chat_id = query.message.chat_id

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

        subscribers = load_subscribers()

        chat_id = query.message.chat_id

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


# ============================================================
# TELEGRAM COMMANDS
# ============================================================

async def set_commands(
    application,
):

    commands = [
        BotCommand(
            "start",
            "Главное меню",
        ),

        BotCommand(
            "market",
            "Рынок",
        ),

        BotCommand(
            "levels",
            "Major Liquidity",
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
            "subscribe",
            "Включить уведомления",
        ),

        BotCommand(
            "unsubscribe",
            "Выключить уведомления",
        ),

        BotCommand(
            "journal",
            "Журнал",
        ),
    ]

    await application.bot.set_my_commands(
        commands
    )


# ============================================================
# POST INIT
# ============================================================

async def post_init(
    application,
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
            "BOT_TOKEN не найден. "
            "Добавь BOT_TOKEN в Environment Variables."
        )

    print(
        "Starting TradeMind 5.0..."
    )

    print(
        f"Binance source: {BINANCE_BASE}"
    )

    print(
        f"BingX mode: {BINGX_MODE}"
    )

    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
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

    application.add_handler(
        CommandHandler(
            "journal",
            journal_command,
        )
    )

    # --------------------------------------------------------
    # BUTTONS
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            callbacks
        )
    )

    print(
        "===================================="
    )

    print(
        "TRADEMIND 5.0 STARTED"
    )

    print(
        f"Coins: {', '.join(COINS.keys())}"
    )

    print(
        f"Scan interval: {CHECK_INTERVAL}s"
    )

    print(
        "D1 + W1 + 1H + 15M + 5M: ENABLED"
    )

    print(
        "BingX AUTO: OFF"
    )

    print(
        "===================================="
    )

    application.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()