import asyncio
import io
import json
import os
import struct
import time
import uuid
import zlib

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

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
SCAN_WORKERS = 9
MIN_SCORE_READY = 80

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


# ============================================================
# FILES
# ============================================================

SUBSCRIBERS_FILE = "subscribers.json"
TRADE_JOURNAL_FILE = "trade_journal.json"
ACTIVE_TRADES_FILE = "active_trades.json"
PENDING_SETUPS_FILE = "pending_setups.json"


# ============================================================
# JSON
# ============================================================

def load_json(filename, default):
    try:
        with open(
            filename,
            "r",
            encoding="utf-8",
        ) as file:
            return json.load(file)

    except Exception:
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
            "SAVE ERROR",
            filename,
            exc,
        )


# ============================================================
# TIME
# ============================================================

def now_iso():
    return datetime.utcnow().isoformat() + "Z"


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
    journal = journal[-500:]

    save_json(
        TRADE_JOURNAL_FILE,
        journal,
    )


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
# PRICE FORMAT
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


def format_percent(value):
    if value is None:
        return "N/A"

    try:
        value = float(value)

        sign = "+" if value > 0 else ""

        return f"{sign}{value:.2f}%"

    except Exception:
        return "N/A"


# ============================================================
# STAGE
# ============================================================

def stage_text(stage):
    stages = {
        "READY":
            "🟢 МОЖНО ВХОДИТЬ",

        "SWEPT":
            "🟠 SWEEP — ждём 15M",

        "15M_CONFIRMED":
            "🟡 15M — ждём 5M ILM",

        "WAIT":
            "⏳ ОЖИДАНИЕ",
    }

    return stages.get(
        stage,
        "⏳ ОЖИДАНИЕ",
    )


# ============================================================
# LEVELS
# ============================================================

def levels_text(
    levels,
    current_price,
):
    if not levels:
        return "нет крупных уровней"

    lines = []

    for level in levels:

        try:
            level_price = float(
                level["price"]
            )

            distance = (
                abs(
                    level_price
                    - current_price
                )
                / current_price
                * 100
            )

        except Exception:
            continue

        if level.get("side") == "SHORT":
            icon = "🔴"
        else:
            icon = "🟢"

        lines.append(
            f"{icon} "
            f"{level.get('type', 'LEVEL')} "
            f"{format_price(level_price)} "
            f"• {distance:.2f}% "
            f"• S{float(level.get('strength', 0)):.0f}"
        )

    if not lines:
        return "нет крупных уровней"

    return "\n".join(lines)


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
        "candles_5m": market["candles_5m"],
        "candles_1h": market["candles_1h"],
        "candles_1m": market["candles_1m"],
    })

    return result


# ============================================================
# SCAN
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

        for future in as_completed(
            futures
        ):

            coin, result = (
                future.result()
            )

            results[coin] = result

    return results


# ============================================================
# MARKET MESSAGE
# ============================================================

def market_message(results):

    lines = [
        f"📊 <b>TRADEMIND "
        f"{STRATEGY_VERSION} — РЫНОК</b>",
        "",
    ]

    for coin in COINS:

        result = results.get(
            coin,
            {},
        )

        if result.get("error"):

            lines.extend([
                f"❌ <b>{coin}</b>: "
                f"{result['error']}",
                "",
            ])

            continue

        lines.extend([
            f"💠 <b>{coin}</b> "
            f"{format_price(result['price'])}",

            (
                f"{stage_text(result.get('stage'))}"
                f" • Score "
                f"{result.get('score', 0)}/100"
            ),

            (
                "📐 1H: <b>"
                f"{result.get('direction') or 'NEUTRAL'}"
                "</b>"
            ),

            "",
        ])

    lines.extend([
        "1H → Major Liquidity → "
        "Sweep → 15M → 5M ILM",

        "❌ В середине движения не входим.",
    ])

    return "\n".join(lines)


# ============================================================
# COIN MESSAGE
# ============================================================

def coin_message(
    coin,
    result,
    pending_id=None,
):

    if result.get("error"):

        return (
            f"❌ {coin}\n"
            f"{result['error']}"
        )

    stage = result.get(
        "stage",
        "WAIT",
    )

    lines = [

        f"📈 <b>TRADEMIND "
        f"{STRATEGY_VERSION} — {coin}</b>",

        "",

        (
            "💰 Цена: <b>"
            f"{format_price(result['price'])}"
            "</b>"
        ),

        stage_text(stage),

        (
            "⭐ Score: <b>"
            f"{result.get('score', 0)}/100"
            "</b>"
        ),

        (
            "📐 1H: <b>"
            f"{result.get('direction') or 'NEUTRAL'}"
            "</b>"
        ),

        "",

        "💧 <b>MAJOR LIQUIDITY</b>",

        levels_text(
            result.get("major_levels"),
            result["price"],
        ),
    ]

    sweep = result.get("sweep")

    if sweep:

        lines.extend([

            "",

            (
                "💧 Sweep: <b>"
                f"{format_price(sweep.get('level'))}"
                "</b>"
            ),

            (
                "Extreme: <b>"
                f"{format_price(sweep.get('extreme'))}"
                "</b>"
            ),
        ])

    if stage == "SWEPT":

        lines.extend([
            "",
            "⏳ <b>ЖДЁМ 15M CONFIRMATION</b>",
            "❌ Вход запрещён.",
        ])

    elif stage == "15M_CONFIRMED":

        lines.extend([
            "",
            "✅ 15M подтверждение",
            "⏳ <b>ЖДЁМ 5M ILM</b>",
            "❌ Вход запрещён.",
        ])

    elif stage == "READY":

        lines.extend([

            "",

            "🎯 <b>SETUP</b>",

            (
                "Entry: <b>"
                f"{format_price(result.get('entry'))}"
                "</b>"
            ),

            (
                "SL: <b>"
                f"{format_price(result.get('sl'))}"
                "</b>"
            ),

            (
                "TP: <b>"
                f"{format_price(result.get('tp'))}"
                "</b>"
            ),

            (
                "RR: <b>"
                f"{format_rr(result.get('rr'))}"
                "</b>"
            ),

            "",

            "🟢 <b>МОЖНО ВХОДИТЬ</b>",
        ])

    else:

        lines.extend([
            "",
            "⏳ Ждём Major Liquidity → Sweep",
            "❌ В середине движения не входим.",
        ])

    reason = result.get(
        "reason",
        "",
    )

    if reason:

        lines.extend([
            "",
            "Причина: " + str(reason),
        ])

    return "\n".join(lines)


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
                "📈 Графики",
                callback_data="charts",
            ),
        ],

        [
            InlineKeyboardButton(
                "🔎 Поиск",
                callback_data="search",
            ),
        ],

        [
            InlineKeyboardButton(
                "📒 Журнал",
                callback_data="journal",
            ),

            InlineKeyboardButton(
                "📌 Активные",
                callback_data="active",
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


def chart_keyboard():

    rows = []

    coins = list(
        COINS.keys()
    )

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
            "⬅️ Меню",
            callback_data="start",
        )
    ])

    return InlineKeyboardMarkup(
        rows
    )


def ready_keyboard(
    setup_id,
):

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🟢 Я ЗАШЁЛ",
                callback_data=f"enter_{setup_id}",
            ),
        ],

        [
            InlineKeyboardButton(
                "📈 Открыть график",
                callback_data=f"chart_{setup_id}",
            ),
        ],

    ])


def active_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "📈 Графики",
                callback_data="charts",
            ),
        ],

        [
            InlineKeyboardButton(
                "📒 Журнал",
                callback_data="journal",
            ),
        ],

    ])


# ============================================================
# SETUP ID
# ============================================================

def make_setup_id(
    coin,
    result,
):

    raw = "|".join([
        str(coin),
        str(result.get("direction")),
        str(result.get("entry")),
        str(result.get("sl")),
        str(result.get("tp")),
        str(result.get("rr")),
        str(
            (result.get("sweep") or {}).get(
                "open_time"
            )
        ),
        str(
            result.get(
                "confirmation_15m_time"
            )
        ),
        str(
            (result.get("ilm") or {}).get(
                "trigger_time"
            )
        ),
    ])

    return (
        uuid.uuid5(
            uuid.NAMESPACE_DNS,
            raw,
        )
        .hex[:16]
    )


# ============================================================
# SAVE READY SETUP
# ============================================================

def save_ready_setup(
    coin,
    result,
):

    setup_id = make_setup_id(
        coin,
        result,
    )

    pending = load_pending_setups()

    pending[setup_id] = {
        "id": setup_id,
        "coin": coin,
        "symbol": result.get(
            "symbol",
            COINS.get(coin),
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
            "score",
            0,
        ),
        "created_at": now_iso(),

        "sweep": result.get(
            "sweep"
        ),

        "confirmation_15m_time":
            result.get(
                "confirmation_15m_time"
            ),

        "ilm":
            result.get(
                "ilm"
            ),
    }

    save_pending_setups(
        pending
    )

    return setup_id


# ============================================================
# FIND ACTIVE TRADE
# ============================================================

def find_active_trade(
    coin,
):

    trades = load_active_trades()

    for trade in trades:

        if (
            trade.get("status")
            == "OPEN"
            and trade.get("coin")
            == coin
        ):

            return trade

    return None


# ============================================================
# OPEN TRADE
# ============================================================

def open_trade_from_setup(
    setup,
    chat_id,
):

    trades = load_active_trades()

    existing = find_active_trade(
        setup["coin"]
    )

    if existing:
        return existing, False

    trade_id = (
        uuid.uuid4()
        .hex[:16]
    )

    trade = {

        "id": trade_id,

        "setup_id":
            setup["id"],

        "coin":
            setup["coin"],

        "symbol":
            setup["symbol"],

        "direction":
            setup["direction"],

        "entry":
            float(setup["entry"]),

        "sl":
            float(setup["sl"]),

        "tp":
            float(setup["tp"]),

        "rr":
            float(setup["rr"])
            if setup.get("rr") is not None
            else None,

        "score":
            setup.get("score", 0),

        "status":
            "OPEN",

        "opened_at":
            now_iso(),

        "chat_id":
            chat_id,

        "signal_source":
            "TradeMind READY",

        "signal_created_at":
            setup.get("created_at"),

        "last_price":
            None,

        "exit_price":
            None,

        "closed_at":
            None,

        "result":
            None,

        "pnl_pct":
            None,
    }

    trades.append(
        trade
    )

    save_active_trades(
        trades
    )

    return trade, True


# ============================================================
# PNL
# ============================================================

def calculate_pnl_pct(
    trade,
    exit_price,
):

    try:

        entry = float(
            trade["entry"]
        )

        exit_price = float(
            exit_price
        )

        direction = trade.get(
            "direction"
        )

        if direction == "LONG":

            return (
                (
                    exit_price
                    - entry
                )
                / entry
                * 100
            )

        if direction == "SHORT":

            return (
                (
                    entry
                    - exit_price
                )
                / entry
                * 100
            )

    except Exception:
        pass

    return None


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade_id,
    result,
    exit_price,
):

    trades = load_active_trades()

    closed_trade = None

    updated = []

    for trade in trades:

        if trade.get("id") != trade_id:

            updated.append(
                trade
            )

            continue

        if trade.get("status") != "OPEN":

            updated.append(
                trade
            )

            continue

        trade["status"] = "CLOSED"

        trade["result"] = result

        trade["exit_price"] = float(
            exit_price
        )

        trade["closed_at"] = now_iso()

        trade["pnl_pct"] = (
            calculate_pnl_pct(
                trade,
                exit_price,
            )
        )

        closed_trade = dict(
            trade
        )

        # Closed trades are not kept
        # in active_trades.json.
        continue

    save_active_trades(
        updated
    )

    if closed_trade:

        journal = load_journal()

        journal.append(
            closed_trade
        )

        save_journal(
            journal
        )

    return closed_trade


# ============================================================
# JOURNAL TEXT
# ============================================================

def journal_message():

    journal = load_journal()

    if not journal:

        return (
            "📒 <b>ЖУРНАЛ</b>\n\n"
            "Сделок пока нет."
        )

    lines = [
        "📒 <b>TRADEMIND — ЖУРНАЛ</b>",
        "",
    ]

    for trade in journal[-10:][::-1]:

        result = trade.get(
            "result",
            "?",
        )

        icon = (
            "✅"
            if result == "TP"
            else "❌"
            if result == "SL"
            else "⚠️"
        )

        lines.extend([

            (
                f"{icon} <b>"
                f"{trade.get('coin')}"
                f" {trade.get('direction')}"
                f"</b>"
            ),

            (
                f"Entry: "
                f"{format_price(trade.get('entry'))}"
            ),

            (
                f"Exit: "
                f"{format_price(trade.get('exit_price'))}"
            ),

            (
                f"Результат: "
                f"<b>{result}</b>"
            ),

            (
                f"PnL: "
                f"<b>{format_percent(trade.get('pnl_pct'))}</b>"
            ),

            "",
        ])

    return "\n".join(lines)


# ============================================================
# ACTIVE TEXT
# ============================================================

def active_message():

    trades = load_active_trades()

    open_trades = [
        x
        for x in trades
        if x.get("status") == "OPEN"
    ]

    if not open_trades:

        return (
            "📌 <b>АКТИВНЫЕ СДЕЛКИ</b>\n\n"
            "Нет активных сделок."
        )

    lines = [
        "📌 <b>TRADEMIND — АКТИВНЫЕ СДЕЛКИ</b>",
        "",
    ]

    for trade in open_trades:

        lines.extend([

            (
                f"💠 <b>{trade.get('coin')}</b> "
                f"{trade.get('direction')}"
            ),

            (
                f"Entry: "
                f"{format_price(trade.get('entry'))}"
            ),

            (
                f"SL: "
                f"{format_price(trade.get('sl'))}"
            ),

            (
                f"TP: "
                f"{format_price(trade.get('tp'))}"
            ),

            (
                f"RR: "
                f"{format_rr(trade.get('rr'))}"
            ),

            (
                f"Цена: "
                f"{format_price(trade.get('last_price'))}"
            ),

            "",
        ])

    return "\n".join(lines)


# ============================================================
# PNG HELPERS
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
            )
            & 0xffffffff,
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

        for _ in range(
            height
        )
    ]


def put_pixel(
    pixels,
    x,
    y,
    color,
):

    if y < 0 or y >= len(pixels):
        return

    width = len(
        pixels[0]
    ) // 3

    if x < 0 or x >= width:
        return

    index = x * 3

    pixels[y][
        index:index + 3
    ] = bytes(color)


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
    )[-90:]

    levels = result.get(
        "major_levels",
        [],
    )

    values = []

    # --------------------------------------------------------
    # Candle values
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Liquidity
    # --------------------------------------------------------

    for level in levels:

        try:

            values.append(
                float(level["price"])
            )

            # Include zone boundaries.
            if level.get("zone_low") is not None:
                values.append(
                    float(level["zone_low"])
                )

            if level.get("zone_high") is not None:
                values.append(
                    float(level["zone_high"])
                )

        except Exception:
            continue

    # --------------------------------------------------------
    # Result levels
    # --------------------------------------------------------

    for key in (
        "price",
        "entry",
        "sl",
        "tp",
        "exit_price",
        "sweep_extreme",
    ):

        if result.get(key) is not None:

            try:

                values.append(
                    float(result[key])
                )

            except Exception:
                continue

    # --------------------------------------------------------
    # Trade levels
    # --------------------------------------------------------

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
                        float(
                            trade[key]
                        )
                    )

                except Exception:
                    continue

    if not values:

        try:

            current = float(
                result["price"]
            )

        except Exception:

            current = 1.0

        values = [
            current - 1,
            current + 1,
        ]

    low = min(values)
    high = max(values)

    padding = (
        (high - low)
        * 0.08
        or 1
    )

    low -= padding
    high += padding

    width = 1100
    height = 620

    left = 50
    right = 30
    top = 30
    bottom = 40

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

    # --------------------------------------------------------
    # GRID
    # --------------------------------------------------------

    grid_color = (
        45,
        52,
        62,
    )

    for i in range(
        1,
        8,
    ):

        yy = (
            top
            + chart_height
            * i
            // 8
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

    # --------------------------------------------------------
    # MAJOR LIQUIDITY
    # --------------------------------------------------------

    for level in levels:

        try:

            level_price = float(
                level["price"]
            )

        except Exception:
            continue

        if level.get("side") == "LONG":

            color = (
                50,
                210,
                130,
            )

        else:

            color = (
                235,
                80,
                90,
            )

        # Main level.
        draw_line(
            pixels,
            left,
            y(level_price),
            width - right,
            y(level_price),
            color,
            2,
        )

        # Zone boundaries.
        try:

            zone_low = float(
                level["zone_low"]
            )

            zone_high = float(
                level["zone_high"]
            )

            draw_line(
                pixels,
                left,
                y(zone_low),
                width - right,
                y(zone_low),
                color,
                1,
            )

            draw_line(
                pixels,
                left,
                y(zone_high),
                width - right,
                y(zone_high),
                color,
                1,
            )

        except Exception:
            pass

    # --------------------------------------------------------
    # CANDLES
    # --------------------------------------------------------

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
            spacing * 0.55
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

        xx = int(
            left
            + (
                i + 0.5
            )
            * spacing
        )

        if close_price >= open_price:

            color = (
                50,
                210,
                130,
            )

        else:

            color = (
                235,
                80,
                90,
            )

        # Wick.
        draw_line(
            pixels,
            xx,
            y(high_price),
            xx,
            y(low_price),
            color,
            1,
        )

        # Body.
        body_top = min(
            y(open_price),
            y(close_price),
        )

        body_bottom = max(
            y(open_price),
            y(close_price),
        )

        for X in range(
            xx - candle_width // 2,
            xx + candle_width // 2 + 1,
        ):

            for Y in range(
                body_top,
                body_bottom + 1,
            ):

                put_pixel(
                    pixels,
                    X,
                    Y,
                    color,
                )

    # --------------------------------------------------------
    # CURRENT PRICE
    # --------------------------------------------------------

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
                width - right - 5,
                y(current_price),
                (
                    80,
                    170,
                    255,
                ),
                6,
            )

        except Exception:
            pass

    # --------------------------------------------------------
    # SWEEP
    # --------------------------------------------------------

    sweep = result.get(
        "sweep"
    )

    if sweep:

        try:

            sweep_level = float(
                sweep["level"]
            )

            draw_line(
                pixels,
                left,
                y(sweep_level),
                width - right,
                y(sweep_level),
                (
                    255,
                    170,
                    40,
                ),
                3,
            )

        except Exception:
            pass

        try:

            sweep_extreme = float(
                sweep["extreme"]
            )

            draw_marker(
                pixels,
                width - right - 80,
                y(sweep_extreme),
                (
                    255,
                    170,
                    40,
                ),
                8,
            )

        except Exception:
            pass

    # --------------------------------------------------------
    # RESULT TRADE LEVELS
    # --------------------------------------------------------

    trade_source = trade or result

    level_colors = {
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
            255,
            255,
            255,
        ),
    }

    for key, color in level_colors.items():

        value = trade_source.get(
            key
        )

        if value is None:
            continue

        try:

            value = float(
                value
            )

            draw_line(
                pixels,
                left,
                y(value),
                width - right,
                y(value),
                color,
                3,
            )

        except Exception:
            continue

    # --------------------------------------------------------
    # EXIT MARKER
    # --------------------------------------------------------

    if trade and trade.get(
        "exit_price"
    ) is not None:

        try:

            exit_price = float(
                trade["exit_price"]
            )

            draw_marker(
                pixels,
                width - right - 35,
                y(exit_price),
                (
                    255,
                    255,
                    255,
                ),
                10,
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
# SEND CHART
# ============================================================

async def send_chart(
    message,
    coin,
    trade=None,
):

    try:

        result = await asyncio.to_thread(
            build_analysis,
            COINS[coin],
        )

        image = chart_png(
            result,
            trade,
        )

        image.seek(0)

        caption = coin_message(
            coin,
            result,
        )

        if trade:

            caption += (
                "\n\n📌 <b>АКТИВНАЯ СДЕЛКА</b>"
                "\n"
                f"Entry: <b>{format_price(trade.get('entry'))}</b>"
                "\n"
                f"SL: <b>{format_price(trade.get('sl'))}</b>"
                "\n"
                f"TP: <b>{format_price(trade.get('tp'))}</b>"
            )

        await message.reply_photo(
            photo=InputFile(
                image,
                filename=(
                    f"{coin.lower()}_5m.png"
                ),
            ),
            caption=caption,
            parse_mode="HTML",
            reply_markup=chart_keyboard(),
        )

    except Exception as exc:

        await message.reply_text(
            f"❌ График {coin}: {exc}"
        )


# ============================================================
# BROADCAST
# ============================================================

async def broadcast(
    app,
    text,
    reply_markup=None,
):

    chats = subscribers()

    if not chats:
        return

    await asyncio.gather(

        *(
            app.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )

            for chat_id in chats
        ),

        return_exceptions=True,
    )


# ============================================================
# READY BROADCAST
# ============================================================

async def broadcast_ready(
    app,
    coin,
    result,
):

    setup_id = save_ready_setup(
        coin,
        result,
    )

    text = (
        "🚨 <b>TRADEMIND — МОЖНО ВХОДИТЬ</b>\n\n"

        f"💠 <b>{coin}</b>\n"

        f"📐 <b>{result.get('direction')}</b>\n\n"

        f"⭐ Score: <b>{result.get('score', 0)}/100</b>\n\n"

        f"💰 Entry: <b>{format_price(result.get('entry'))}</b>\n"

        f"🛑 SL: <b>{format_price(result.get('sl'))}</b>\n"

        f"🎯 TP: <b>{format_price(result.get('tp'))}</b>\n"

        f"📊 RR: <b>{format_rr(result.get('rr'))}</b>\n\n"

        "🎯 TP = следующая свежая major liquidity.\n\n"

        "🟢 <b>Сигнал зафиксирован.</b>\n"
        "Нажми кнопку только если реально вошёл."
    )

    await broadcast(
        app,
        text,
        ready_keyboard(
            setup_id
        ),
    )


# ============================================================
# SWEEP BROADCAST
# ============================================================

async def broadcast_sweep(
    app,
    coin,
    result,
):

    sweep = result.get(
        "sweep"
    ) or {}

    text = (
        "🔎 <b>TRADEMIND — SWEEP</b>\n\n"

        f"💠 <b>{coin}</b>\n"

        f"📐 {result.get('direction')}\n\n"

        "💧 <b>Крупная ликвидность снята.</b>\n\n"

        f"Уровень: <b>{format_price(sweep.get('level'))}</b>\n"

        f"Экстремум: <b>{format_price(sweep.get('extreme'))}</b>\n\n"

        "⏳ <b>ЖДЁМ 15M CONFIRMATION</b>\n"

        "❌ Вход пока запрещён."
    )

    await broadcast(
        app,
        text,
    )


# ============================================================
# 15M BROADCAST
# ============================================================

async def broadcast_15m(
    app,
    coin,
    result,
):

    text = (
        "🟡 <b>TRADEMIND — 15M CONFIRMATION</b>\n\n"

        f"💠 <b>{coin}</b>\n"

        f"📐 {result.get('direction')}\n\n"

        "✅ Sweep\n"
        "✅ 15M confirmation\n\n"

        "⏳ <b>ЖДЁМ 5M ILM</b>\n"

        "❌ Вход пока запрещён."
    )

    await broadcast(
        app,
        text,
    )


# ============================================================
# START
# ============================================================

async def start(
    update,
    context,
):

    await update.message.reply_text(

        f"🤖 <b>TRADEMIND {STRATEGY_VERSION}</b>\n\n"

        "9 монет • Binance Spot\n\n"

        "<b>1H → Major Liquidity → "
        "Sweep → 15M → 5M ILM → Entry</b>\n\n"

        "📐 1H — главное направление\n"

        "D1/W1 — отключены\n"

        "📊 Дневной лимит сделок — отключён\n"

        "⚡ BingX auto — OFF\n\n"

        "Бот даёт сигнал.\n"
        "После 🟢 «Я ЗАШЁЛ» TradeMind "
        "сам отслеживает TP/SL.",

        parse_mode="HTML",

        reply_markup=main_keyboard(),
    )


# ============================================================
# MARKET COMMAND
# ============================================================

async def market_cmd(
    update,
    context,
):

    results = await asyncio.to_thread(
        scan_all
    )

    await update.message.reply_text(

        market_message(
            results
        ),

        parse_mode="HTML",

        reply_markup=main_keyboard(),
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

    ready = []

    for coin, result in results.items():

        if result.get(
            "stage"
        ) != "READY":

            continue

        if result.get(
            "score",
            0,
        ) < MIN_SCORE_READY:

            continue

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

        await update.message.reply_text(

            "🔎 <b>ГОТОВОГО СЕТАПА НЕТ</b>\n\n"

            "Ждём:\n"
            "1H → Major Liquidity → "
            "Sweep → 15M → 5M ILM.",

            parse_mode="HTML",

            reply_markup=main_keyboard(),
        )

        return

    ready.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    _, coin, result = ready[0]

    setup_id = save_ready_setup(
        coin,
        result,
    )

    await update.message.reply_text(

        coin_message(
            coin,
            result,
        ),

        parse_mode="HTML",

        reply_markup=ready_keyboard(
            setup_id
        ),
    )


# ============================================================
# CHART COMMAND
# ============================================================

async def chart_cmd(
    update,
    context,
):

    coin = "SOL"

    if (
        context.args
        and context.args[0].upper()
        in COINS
    ):

        coin = context.args[0].upper()

    trade = find_active_trade(
        coin
    )

    await send_chart(
        update.message,
        coin,
        trade,
    )


# ============================================================
# ACTIVE COMMAND
# ============================================================

async def active_cmd(
    update,
    context,
):

    await update.message.reply_text(

        active_message(),

        parse_mode="HTML",

        reply_markup=active_keyboard(),
    )


# ============================================================
# JOURNAL COMMAND
# ============================================================

async def journal_cmd(
    update,
    context,
):

    await update.message.reply_text(

        journal_message(),

        parse_mode="HTML",

        reply_markup=main_keyboard(),
    )


# ============================================================
# SUBSCRIBE
# ============================================================

async def subscribe_cmd(
    update,
    context,
):

    data = subscribers()

    chat_id = update.effective_chat.id

    if chat_id not in data:

        data.append(
            chat_id
        )

        save_subscribers(
            data
        )

    await update.message.reply_text(

        "🔔 <b>Уведомления включены.</b>",

        parse_mode="HTML",

        reply_markup=main_keyboard(),
    )


# ============================================================
# UNSUBSCRIBE
# ============================================================

async def unsubscribe_cmd(
    update,
    context,
):

    chat_id = update.effective_chat.id

    data = [
        x
        for x in subscribers()
        if x != chat_id
    ]

    save_subscribers(
        data
    )

    await update.message.reply_text(

        "🔕 <b>Уведомления выключены.</b>",

        parse_mode="HTML",

        reply_markup=main_keyboard(),
    )


# ============================================================
# STATUS
# ============================================================

async def status_cmd(
    update,
    context,
):

    active = load_active_trades()

    open_count = len([
        x
        for x in active
        if x.get("status") == "OPEN"
    ])

    journal = load_journal()

    await update.message.reply_text(

        f"📊 <b>TRADEMIND {STRATEGY_VERSION}</b>\n\n"

        f"⏱ Сканирование: "
        f"{CHECK_INTERVAL} сек.\n"

        f"💠 Монет: {len(COINS)}\n"

        f"📌 Активных сделок: "
        f"{open_count}\n"

        f"📒 В журнале: "
        f"{len(journal)}\n\n"

        "📐 1H — основной timeframe\n"

        "D1/W1 — OFF\n"

        "💰 Binance Spot — ON\n"

        "⚡ BingX auto — OFF\n"

        "📅 Дневной лимит — OFF",

        parse_mode="HTML",

        reply_markup=main_keyboard(),
    )


# ============================================================
# PRICE CHECK FOR ACTIVE TRADES
# ============================================================

def check_trade_hit(
    trade,
    price,
):

    try:

        price = float(price)

        entry = float(
            trade["entry"]
        )

        sl = float(
            trade["sl"]
        )

        tp = float(
            trade["tp"]
        )

        direction = trade.get(
            "direction"
        )

    except Exception:

        return None

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        if price >= tp:
            return "TP"

        if price <= sl:
            return "SL"

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    elif direction == "SHORT":

        if price <= tp:
            return "TP"

        if price >= sl:
            return "SL"

    return None


# ============================================================
# ACTIVE TRADE MONITOR
# ============================================================

async def monitor_active_trades(
    app,
    results,
):

    trades = load_active_trades()

    if not trades:
        return

    changed = False

    for trade in trades:

        if trade.get(
            "status"
        ) != "OPEN":

            continue

        coin = trade.get(
            "coin"
        )

        result = results.get(
            coin,
            {},
        )

        if result.get("error"):
            continue

        price = result.get(
            "price"
        )

        if price is None:
            continue

        trade["last_price"] = float(
            price
        )

        hit = check_trade_hit(
            trade,
            price,
        )

        if hit is None:
            changed = True
            continue

        trade_id = trade.get(
            "id"
        )

        closed = close_trade(
            trade_id,
            hit,
            price,
        )

        if not closed:
            continue

        changed = True

        direction = closed.get(
            "direction"
        )

        pnl = closed.get(
            "pnl_pct"
        )

        if hit == "TP":

            icon = "✅"

            title = "TP ДОСТИГНУТ"

        elif hit == "SL":

            icon = "❌"

            title = "SL ДОСТИГНУТ"

        else:

            icon = "⚠️"

            title = hit

        text = (
            f"{icon} <b>TRADEMIND — {title}</b>\n\n"

            f"💠 <b>{coin}</b>\n"

            f"📐 {direction}\n\n"

            f"Entry: "
            f"<b