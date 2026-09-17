import asyncio
import io
import json
import os
import struct
import time
import uuid
import zlib

from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)

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

def load_json(
    filename,
    default,
):
    try:
        with open(
            filename,
            "r",
            encoding="utf-8",
        ) as file:

            return json.load(file)

    except Exception:
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
            "SAVE ERROR",
            filename,
            exc,
        )


# ============================================================
# SUBSCRIBERS
# ============================================================

def subscribers():

    return load_json(
        SUBSCRIBERS_FILE,
        [],
    )


def save_subscribers(
    data,
):

    save_json(
        SUBSCRIBERS_FILE,
        data,
    )


# ============================================================
# TRADES
# ============================================================

def load_active_trades():

    data = load_json(
        ACTIVE_TRADES_FILE,
        [],
    )

    if not isinstance(data, list):
        return []

    return data


def save_active_trades(
    trades,
):

    save_json(
        ACTIVE_TRADES_FILE,
        trades,
    )


def load_journal():

    data = load_json(
        TRADE_JOURNAL_FILE,
        [],
    )

    if not isinstance(data, list):
        return []

    return data


def save_journal(
    journal,
):

    # Храним последние 500 сделок.
    journal = journal[-500:]

    save_json(
        TRADE_JOURNAL_FILE,
        journal,
    )


def load_pending_setups():

    data = load_json(
        PENDING_SETUPS_FILE,
        {},
    )

    if not isinstance(data, dict):
        return {}

    return data


def save_pending_setups(
    data,
):

    save_json(
        PENDING_SETUPS_FILE,
        data,
    )


# ============================================================
# PRICE FORMAT
# ============================================================

def format_price(
    price,
):

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


def format_rr(
    value,
):

    if value is None:
        return "N/A"

    try:
        return f"1:{float(value):.2f}"
    except Exception:
        return "N/A"


# ============================================================
# STAGE
# ============================================================

def stage_text(
    stage,
):

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

        if level["side"] == "SHORT":

            icon = "🔴"

        else:

            icon = "🟢"

        lines.append(
            f"{icon} "
            f"{level.get('type', 'LEVEL')} "
            f"{format_price(level_price)} "
            f"• {distance:.2f}% "
            f"• S{level.get('strength', 0):.0f}"
        )

    if not lines:
        return "нет крупных уровней"

    return "\n".join(lines)


# ============================================================
# BUILD ANALYSIS
# ============================================================

def build_analysis(
    symbol,
):

    market = get_market_data(
        symbol
    )

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

        "symbol":
            symbol,

        "price":
            price,

        "major_levels":
            levels,

        "sweep":
            sweep,

        "candles_5m":
            market["candles_5m"],

        "candles_1h":
            market["candles_1h"],

        "candles_1m":
            market["candles_1m"],
    })

    return result


# ============================================================
# SCAN
# ============================================================

def scan_one(
    item,
):

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
                "error":
                    str(exc),

                "symbol":
                    symbol,
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

def market_message(
    results,
):

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
                f"{result['error']",

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

    sweep = result.get(
        "sweep"
    )

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
                "📌 Активная",
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
    trade_id,
):

    return InlineKeyboardMarkup([

        [

            InlineKeyboardButton(
                "🟢 Я ЗАШЁЛ",
                callback_data=f"enter_{trade_id}",
            ),

        ],

        [

            InlineKeyboardButton(
                "📈 Открыть график",
                callback_data="charts",
            ),

        ],

    ])


def active_keyboard():

    return InlineKeyboardMarkup([

        [

            InlineKeyboardButton(
                "📈 График сделки",
                callback_data="active_chart",
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
# PNG
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

    for candle in candles:

        try:

            values.append(
                float(candle["high"])
            )

            values.append(
                float(candle["low"])
            )

        except Exception:
            pass

    for level in levels:

        try:

            values.append(
                float(level["price"])
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
            result["price"]
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
            + chart_height * i // 8
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

        draw_line(
            pixels,
            left,
            y(level_price),
            width - right,
            y(level_price),
            color,
            2,
        )

    # --------------------------------------------------------
    # CANDLES
    # --------------------------------------------------------

    spacing = (
        chart_width
        / max(len(candles), 1)
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