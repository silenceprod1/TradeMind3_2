import asyncio
import io
import json
import os
import struct
import time
import uuid
import zlib

from concurrent.futures import ThreadPoolExecutor, as_completed

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
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(),
    )


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
# PRICE
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
            f"• S{level.get('strength', 0):.0f}"
        )

    if not lines:
        return "нет крупных уровней"

    return "\n".join(lines)


# ============================================================
# BUILD ANALYSIS
# ============================================================

def build_analysis(symbol):

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
                callback_data="chart_ready",
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

        [

            InlineKeyboardButton(
                "⬅️ Меню",
                callback_data="start",
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
            continue

    for level in levels:

        try:

            values.append(
                float(level["price"])
            )

        except Exception:
            continue

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

            x = int(
                left
                + (
                    i + 0.5
                )
                * spacing
            )

            if close_price >= open_price:

                candle_color = (
                    50,
                    210,
                    130,
                )

            else:

                candle_color = (
                    235,
                    80,
                    90,
                )

            # Wick

            draw_line(
                pixels,
                x,
                y(high_price),
                x,
                y(low_price),
                candle_color,
                1,
            )

            # Body

            body_top = min(
                y(open_price),
                y(close_price),
            )

            body_bottom = max(
                y(open_price),
                y(close_price),
            )

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

        except Exception:
            pass

    # --------------------------------------------------------
    # SWEEP
    # --------------------------------------------------------

    sweep = result.get(
        "sweep"
    )

    if sweep:

        for key in (
            "level",
            "extreme",
        ):

            value = sweep.get(
                key
            )

            if value is None:
                continue

            try:

                draw_line(
                    pixels,
                    left,
                    y(float(value)),
                    width - right,
                    y(float(value)),
                    (
                        255,
                        170,
                        40,
                    ),
                    3,
                )

            except Exception:
                pass

    # --------------------------------------------------------
    # TRADE LEVELS
    # --------------------------------------------------------

    trade_source = trade or result

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

        value = trade_source.get(
            key
        )

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

            # Marker справа

            marker_x = (
                width
                - right
                - 10
            )

            draw_marker(
                pixels,
                marker_x,
                y(value),
                color,
                7,
            )

        except Exception:
            pass

    # --------------------------------------------------------
    # EXIT MARKER
    # --------------------------------------------------------

    if trade and trade.get(
        "exit_price"
    ) is not None:

        try:

            exit_y = y(
                float(
                    trade["exit_price"]
                )
            )

            exit_x = (
                width
                - right
                - 40
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
            trade,
        )

        image.seek(0)

        caption = coin_message(
            coin,
            result,
        )

        if trade:

            pnl = calculate_pnl_percent(
                trade.get("entry"),
                trade.get("exit_price"),
                trade.get("direction"),
            )

            caption += (

                "\n\n"
                "📌 <b>СДЕЛКА</b>\n"
                f"Entry: <b>{format_price(trade.get('entry'))}</b>\n"
                f"SL: <b>{format_price(trade.get('sl'))}</b>\n"
                f"TP: <b>{format_price(trade.get('tp'))}</b>\n"
                f"RR: <b>{format_rr(trade.get('rr'))}</b>"
            )

            if trade.get(
                "exit_price"
            ) is not None:

                caption += (

                    "\n"
                    f"Exit: <b>{format_price(trade.get('exit_price'))}</b>"
                )

            if pnl is not None:

                caption += (
                    "\n"
                    f"PnL: <b>{pnl:+.2f}%</b>"
                )

        await message.reply_photo(

            photo=InputFile(
                image,
                filename=f"{coin.lower()}_5m.png",
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

    ids = subscribers()

    if not ids:
        return

    await asyncio.gather(

        *(
            app.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
                reply_markup=reply_markup,
            )

            for chat_id in ids
        ),

        return_exceptions=True,
    )


# ============================================================
# PENDING SETUP CREATION
# ============================================================

def create_pending_setup(
    coin,
    result,
):

    if result.get(
        "stage"
    ) != "READY":

        return None

    entry = result.get(
        "entry"
    )

    sl = result.get(
        "sl"
    )

    tp = result.get(
        "tp"
    )

    rr_value = result.get(
        "rr"
    )

    direction = result.get(
        "direction"
    )

    if any(
        x is None
        for x in (
            entry,
            sl,
            tp,
            rr_value,
            direction,
        )
    ):

        return None

    sweep = result.get(
        "sweep"
    ) or {}

    ilm = result.get(
        "ilm"
    ) or {}

    # Стабильный ID сигнала.
    raw_id = (
        f"{coin}|"
        f"{direction}|"
        f"{entry}|"
        f"{sl}|"
        f"{tp}|"
        f"{sweep.get('open_time')}|"
        f"{ilm.get('trigger_time')}"
    )

    setup_id = uuid.uuid5(
        uuid.NAMESPACE_DNS,
        raw_id,
    ).hex[:12]

    setup = {

        "id":
            setup_id,

        "coin":
            coin,

        "symbol":
            result.get(
                "symbol"
            ),

        "direction":
            direction,

        "entry":
            float(entry),

        "sl":
            float(sl),

        "tp":
            float(tp),

        "rr":
            float(rr_value),

        "score":
            int(
                result.get(
                    "score",
                    0,
                )
            ),

        "created_at":
            now_iso(),

        "sweep":
            sweep,

        "ilm":
            ilm,

        "status":
            "PENDING",

    }

    return setup


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

    # Не держим бесконечный мусор.
    if len(pending) > 200:

        items = sorted(
            pending.items(),
            key=lambda x: x[1].get(
                "created_at",
                "",
            ),
        )

        pending = dict(
            items[-200:]
        )

    save_pending_setups(
        pending
    )

    return setup


# ============================================================
# ENTER TRADE
# ============================================================

def activate_trade(
    setup,
    chat_id,
):

    active = load_active_trades()

    # Уже существует?
    for trade in active:

        if (
            trade.get("setup_id")
            == setup["id"]
            and trade.get("status")
            == "OPEN"
        ):

            return trade, False

    trade_id = uuid.uuid4().hex[:12]

    trade = {

        "id":
            trade_id,

        "setup_id":
            setup["id"],

        "chat_id":
            chat_id,

        "coin":
            setup["coin"],

        "symbol":
            setup["symbol"],

        "direction":
            setup["direction"],

        # ФИКСИРУЕМ сигнал.
        "entry":
            setup["entry"],

        "sl":
            setup["sl"],

        "tp":
            setup["tp"],

        "rr":
            setup["rr"],

        "score":
            setup.get(
                "score",
                0,
            ),

        "opened_at":
            now_iso(),

        "status":
            "OPEN",

        "last_price":
            setup["entry"],

        "entry_source":
            "TradeMind READY signal",

    }

    active.append(
        trade
    )

    save_active_trades(
        active
    )

    pending = load_pending_setups()

    if setup["id"] in pending:

        pending[setup["id"]][
            "status"
        ] = "ENTERED"

        save_pending_setups(
            pending
        )

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

        if item.get(
            "id"
        ) == trade.get(
            "id"
        ):

            target = item
            break

    if target is None:
        return None

    target["status"] = (
        result_type
    )

    target["exit_price"] = float(
        exit_price
    )

    target["closed_at"] = now_iso()

    target["pnl_percent"] = (
        calculate_pnl_percent(
            target.get("entry"),
            exit_price,
            target.get("direction"),
        )
    )

    save_active_trades(
        active
    )

    journal = load_journal()

    journal_entry = dict(
        target
    )

    journal_entry["result"] = (
        result_type
    )

    journal.append(
        journal_entry
    )

    save_journal(
        journal
    )

    return target


# ============================================================
# ACTIVE TRADE CHECK
# ============================================================

def check_trade_price(
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

        direction = trade[
            "direction"
        ]

    except Exception:

        return None

    if direction == "LONG":

        # TP priority only when
        # price is clearly above TP.
        if price >= tp:

            return (
                "TP",
                price,
            )

        if price <= sl:

            return (
                "SL",
                price,
            )

    elif direction == "SHORT":

        if price <= tp:

            return (
                "TP",
                price,
            )

        if price >= sl:

            return (
                "SL",
                price,
            )

    return None


# ============================================================
# MONITOR ACTIVE TRADES
# ============================================================

async def monitor_active_trades(
    app,
    results,
):

    active = load_active_trades()

    if not active:
        return

    changed = False

    for trade in list(
        active
    ):

        if trade.get(
            "status"
        ) != "OPEN":

            continue

        coin = trade.get(
            "coin"
        )

        result = results.get(
            coin
        )

        if not result or result.get(
            "error"
        ):

            continue

        price = result.get(
            "price"
        )

        if price is None:
            continue

        trade["last_price"] = float(
            price
        )

        check = check_trade_price(
            trade,
            price,
        )

        if not check:
            changed = True
            continue

        result_type, exit_price = (
            check
        )

        closed = close_trade(
            trade,
            exit_price,
            result_type,
        )

        if closed is None:
            continue

        changed = True

        pnl = closed.get(
            "pnl_percent"
        )

        if result_type == "TP":

            icon = "✅"

            title = "TP ДОСТИГНУТ"

        else:

            icon = "❌"

            title = "SL ДОСТИГНУТ"

        pnl_text = (
            f"{pnl:+.2f}%"
            if pnl is not None
            else "N/A"
        )

        text = (

            f"{icon} <b>TRADEMIND — "
            f"{title}</b>\n\n"

            f"💠 {closed.get('coin')}\n"

            f"📐 {closed.get('direction')}\n\n"

            f"Entry: <b>"
            f"{format_price(closed.get('entry'))}"
            f"</b>\n"

            f"Exit: <b>"
            f"{format_price(closed.get('exit_price'))}"
            f"</b>\n"

            f"SL: <b>"
            f"{format_price(closed.get('sl'))}"
            f"</b>\n"

            f"TP: <b>"
            f"{format_price(closed.get('tp'))}"
            f"</b>\n\n"

            f"📊 RR: <b>"
            f"{format_rr(closed.get('rr'))}"
            f"</b>\n"

            f"📈 PnL: <b>"
            f"{pnl_text}"
            f"</b>"
        )

        chat_id = closed.get(
            "chat_id"
        )

        if chat_id:

            try:

                await app.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode="HTML",
                )

                await send_chart(
                    app.bot,
                    closed.get("coin"),
                    result=result,
                    trade=closed,
                )

            except Exception as exc:

                print(
                    "TRADE CLOSE MESSAGE ERROR",
                    exc,
                )

        else:

            await broadcast(
                app,
                text,
            )

    if changed:

        # Удаляем закрытые сделки
        # из активного списка.
        active = [
            x
            for x in load_active_trades()
            if x.get("status") == "OPEN"
        ]

        save_active_trades(
            active
        )


# ============================================================
# READY MESSAGE
# ============================================================

def ready_message(
    coin,
    result,
    setup,
):

    return (

        "🚨 <b>TRADEMIND — "
        "МОЖНО ВХОДИТЬ</b>\n\n"

        f"💠 {coin}\n"

        f"📐 <b>{result.get('direction')}</b>\n"

        f"⭐ {result.get('score', 0)}/100\n\n"

        f"💰 Entry: <b>"
        f"{format_price(setup.get('entry'))}"
        f"</b>\n"

        f"🛑 SL: <b>"
        f"{format_price(setup.get('sl'))}"
        f"</b>\n"

        f"🎯 TP: <b>"
        f"{format_price(setup.get('tp'))}"
        f"</b>\n"

        f"📊 RR: <b>"
        f"{format_rr(setup.get('rr'))}"
        f"</b>\n\n"

        "🎯 TP = следующая свежая "
        "major liquidity.\n\n"

        "🟢 <b>Если вошёл по этому сигналу — "
        "нажми кнопку ниже.</b>"
    )


# ============================================================
# START
# ============================================================

async def start(
    update,
    context,
):

    await update.message.reply_text(

        f"🤖 <b>TRADEMIND "
        f"{STRATEGY_VERSION}</b>\n\n"

        "9 монет • Binance Spot\n\n"

        "<b>1H → Major Liquidity → "
        "Sweep → 15M → 5M ILM → Entry</b>\n\n"

        "D1/W1: отключены\n"
        "Дневной лимит: отключён\n"
        "BingX auto: OFF",

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
        ) == "READY":

            if (
                result.get(
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

        await update.message.reply_text(

            "🔎 <b>ГОТОВОГО СЕТАПА НЕТ</b>\n\n"

            "Ждём:\n"
            "1H → Major Liquidity → "
            "Sweep → 15M → 5M ILM.",

            parse_mode="HTML",

            reply_markup=main_keyboard(),
        )

        return

    # Берём READY с максимальным score
    # только для отображения поиска.
    _, coin, result = max(
        ready,
        key=lambda x: x[0],
    )

    setup = save_ready_setup(
        coin,
        result,
    )

    if setup is None:

        await update.message.reply_text(
            "❌ Не удалось сохранить READY setup.",
            reply_markup=main_keyboard(),
        )

        return

    await update.message.reply_text(

        ready_message(
            coin,
            result,
            setup,
        ),

        parse_mode="HTML",

        reply_markup=ready_keyboard(
            setup["id"]
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

        "🔕 <b>Уведомления выключены.</b>",

        parse_mode="HTML",

        reply_markup=main_keyboard(),
    )


# ============================================================
# ACTIVE
# ============================================================

async def active_cmd(
    update,
    context,
):

    active = load_active_trades()

    active = [

        x
        for x in active
        if x.get("status") == "OPEN"

    ]

    if not active:

        await update.message.reply_text(

            "📌 <b>Активных сделок нет.</b>",

            parse_mode="HTML",

            reply_markup=main_keyboard(),
        )

        return

    lines = [
        "📌 <b>АКТИВНЫЕ СДЕЛКИ</b>",
        "",
    ]

    for trade in active:

        lines.extend([

            f"💠 <b>{trade.get('coin')}</b> "
            f"{trade.get('direction')}",

            (
                f"Entry: "
                f"<b>{format_price(trade.get('entry'))}</b>"
            ),

            (
                f"SL: "
                f"<b>{format_price(trade.get('sl'))}</b>"
            ),

            (
                f"TP: "
                f"<b>{format_price(trade.get('tp'))}</b>"
            ),

            (
                f"RR: "
                f"<b>{format_rr(trade.get('rr'))}</b>"
            ),

            (
                f"Цена: "
                f"<b>{format_price(trade.get('last_price'))}</b>"
            ),

            "",
        ])

    await update.message.reply_text(

        "\n".join(lines),

        parse_mode="HTML",

        reply_markup=active_keyboard(),
    )


# ============================================================
# JOURNAL
# ============================================================

async def journal_cmd(
    update,
    context,
):

    journal = load_journal()

    if not journal:

        await update.message.reply_text(

            "📒 <b>Журнал пока пуст.</b>",

            parse_mode="HTML",

            reply_markup=main_keyboard(),
        )

        return

    last = journal[-10:]

    lines = [
        "📒 <b>TRADEMIND — ЖУРНАЛ</b>",
        "",
    ]

    for trade in reversed(
        last
    ):

        result_type = trade.get(
            "result",
            trade.get(
                "status",
                "?",
            ),
        )

        icon = (
            "✅"
            if result_type == "TP"
            else "❌"
            if result_type == "SL"
            else "⚪"
        )

        pnl = trade.get(
            "pnl_percent"
        )

        pnl_text = (
            f"{pnl:+.2f}%"
            if pnl is not None
            else "N/A"
        )

        lines.extend([

            f"{icon} <b>"
            f"{trade.get('coin')}"
            f"</b> "
            f"{trade.get('direction')}",

            f"{result_type} • "
            f"PnL {pnl_text}",

            (
                f"Entry "
                f"{format_price(trade.get('entry'))}"
                f" → "
                f"{format_price(trade.get('exit_price'))}"
            ),

            "",
        ])

    await update.message.reply_text(

        "\n".join(lines),

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

    active_count = len([

        x
        for x in active
        if x.get("status") == "OPEN"

    ])

    journal = load_journal()

    await update.message.reply_text(

        f"📊 <b>TRADEMIND "
        f"{STRATEGY_VERSION}</b>\n\n"

        f"Сканирование: "
        f"{CHECK_INTERVAL} сек.\n"

        f"Монет: "
        f"{len(COINS)}\n"

        f"Активных сделок: "
        f"{active_count}\n"

        f"Сделок в журнале: "
        f"{len(journal)}\n\n"

        "Направление: <b>1H</b>\n"
        "D1/W1: OFF\n"
        "Дневной лимит: OFF\n"
        "BingX auto: OFF",

        parse_mode="HTML",

        reply_markup=main_keyboard(),
    )


# ============================================================
# MONITOR
# ============================================================

async def monitor(
    app,
):

    last_signal = {}

    while True:

        started = (
            asyncio.get_running_loop()
            .time()
        )

        try:

            results = await asyncio.to_thread(
                scan_all
            )

            # ------------------------------------------------
            # ACTIVE TRADES
            # ------------------------------------------------

            await monitor_active_trades(
                app,
                results,
            )

            # ------------------------------------------------
            # SIGNALS
            # ------------------------------------------------

            for coin, result in results.items():

                if result.get(
                    "error"
                ):
                    continue

                stage = result.get(
                    "stage"
                )

                sweep = (
                    result.get(
                        "sweep"
                    )
                    or {}
                )

                ilm = (
                    result.get(
                        "ilm"
                    )
                    or {}
                )

                signal_key = (

                    f"{coin}:"
                    f"{stage}:"
                    f"{result.get('direction')}:"
                    f"{sweep.get('open_time')}:"
                    f"{result.get('confirmation_15m_time')}:"
                    f"{ilm.get('trigger_time')}:"
                    f"{result.get('entry')}:"
                    f"{result.get('sl')}:"
                    f"{result.get('tp')}"
                )

                if (
                    signal_key
                    == last_signal.get(
                        coin
                    )
                ):

                    continue

                last_signal[
                    coin
                ] = signal_key

                # ------------------------------------------------
                # SWEEP
                # ------------------------------------------------

                if stage == "SWEPT":

                    text = (

                        "🔎 <b>TRADEMIND — SWEEP</b>\n\n"

                        f"💠 {coin}\n"

                        f"📐 "
                        f"<b>{result.get('direction')}</b>\n\n"

                        "💧 Крупная ликвидность снята.\n"

                        f"💰 Цена: "
                        f"<b>{format_price(result.get('price'))}</b>\n\n"

                        "⏳ <b>ЖДЁМ 15M CONFIRMATION</b>\n"

                        "❌ Вход запрещён."
                    )

                    await broadcast(
                        app,
                        text,
                    )

                # ------------------------------------------------
                # 15M
                # ------------------------------------------------

                elif stage == "15M_CONFIRMED":

                    text = (

                        "🟡 <b>TRADEMIND — "
                        "15M CONFIRMATION</b>\n\n"

                        f"💠 {coin}\n"

                        f"📐 "
                        f"<b>{result.get('direction')}</b>\n\n"

                        "✅ Sweep\n"
                        "✅ 15M confirmation\n\n"

                        "⏳ <b>ЖДЁМ 5M ILM</b>\n"

                        "❌ Вход запрещён."
                    )

                    await broadcast(
                        app,
                        text,
                    )

                # ------------------------------------------------
                # READY
                # ------------------------------------------------

                elif (
                    stage == "READY"
                    and result.get(
                        "score",
                        0,
                    ) >= MIN_SCORE_READY
                ):

                    setup = save_ready_setup(
                        coin,
                        result,
                    )

                    if setup is None:
                        continue

                    text = ready_message(
                        coin,
                        result,
                        setup,
                    )

                    await broadcast(

                        app,

                        text,

                        reply_markup=ready_keyboard(
                            setup["id"]
                        ),
                    )

        except Exception as exc:

            print(
                "MONITOR ERROR",
                exc,
            )

        elapsed = (
            asyncio.get_running_loop()
            .time()
            - started
        )

        await asyncio.sleep(
            max(
                1,
                CHECK_INTERVAL - elapsed,
            )
        )


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

    # ========================================================
    # MENU
    # ========================================================

    if data == "start":

        await query.edit_message_text(

            f"🤖 <b>TRADEMIND "
            f"{STRATEGY_VERSION}</b>\n\n"

            "1H → Major Liquidity → "
            "Sweep → 15M → 5M ILM\n\n"

            "D1/W1: OFF\n"
            "Дневной лимит: OFF",

            parse_mode="HTML",

            reply_markup=main_keyboard(),
        )

        return

    # ========================================================
    # MARKET
    # ========================================================

    if data == "market":

        results = await asyncio.to_thread(
            scan_all
        )

        await query.edit_message_text(

            market_message(
                results
            ),

            parse_mode="HTML",

            reply_markup=main_keyboard(),
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

            if (
                result.get(
                    "stage"
                ) == "READY"
                and result.get(
                    "score",
                    0,
                ) >= MIN_SCORE_READY
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

            await query.edit_message_text(

                "🔎 <b>ГОТОВОГО СЕТАПА НЕТ</b>\n\n"

                "Ждём:\n"
                "1H → Major Liquidity → "
                "Sweep → 15M → 5M ILM.",

                parse_mode="HTML",

                reply_markup=main_keyboard(),
            )

            return

        _, coin, result = max(
            ready,
            key=lambda x: x[0],
        )

        setup = save_ready_setup(
            coin,
            result,
        )

        if setup is None:

            await query.edit_message_text(
                "❌ Не удалось сохранить setup."
            )

            return

        await query.edit_message_text(

            ready_message(
                coin,
                result,
                setup,
            ),

            parse_mode="HTML",

            reply_markup=ready_keyboard(
                setup["id"]
            ),
        )

        return

    # ========================================================
    # ENTER
    # ========================================================

    if data.startswith(
        "enter_"
    ):

        setup_id = data.split(
            "_",
            1,
        )[1]

        pending = load_pending_setups()

        setup = pending.get(
            setup_id
        )

        if not setup:

            await query.edit_message_text(

                "⚠️ <b>Этот сигнал больше "
                "не найден.</b>\n\n"
                "Возможно, он устарел.",

                parse_mode="HTML",

                reply_markup=main_keyboard(),
            )

            return

        if setup.get(
            "status"
        ) == "ENTERED":

            active = load_active_trades()

            existing = next(

                (
                    x
                    for x in active
                    if x.get(
                        "setup_id"
                    )
                    == setup_id
                    and x.get(
                        "status"
                    )
                    == "OPEN"
                ),

                None,
            )

            if existing:

                await query.edit_message_text(

                    "🟢 <b>Эта сделка уже активна.</b>\n\n"

                    f"💠 {existing.get('coin')}\n"
                    f"📐 {existing.get('direction')}\n\n"

                    f"Entry: <b>"
                    f"{format_price(existing.get('entry'))}"
                    f"</b>\n"

                    f"SL: <b>"
                    f"{format_price(existing.get('sl'))}"
                    f"</b>\n"

                    f"TP: <b>"
                    f"{format_price(existing.get('tp'))}"
                    f"</b>\n"

                    f"RR: <b>"
                    f"{format_rr(existing.get('rr'))}"
                    f"</b>",

                    parse_mode="HTML",

                    reply_markup=active_keyboard(),
                )

                return

        trade, created = activate_trade(
            setup,
            query.message.chat_id,
        )

        if not created:

            await query.edit_message_text(

                "🟢 <b>Сделка уже активна.</b>",

                parse_mode="HTML",

                reply_markup=active_keyboard(),
            )

            return

        await query.edit_message_text(

            "🟢 <b>СДЕЛКА ПРИНЯТА</b>\n\n"

            f"💠 {trade.get('coin')}\n"

            f"📐 <b>{trade.get('direction')}</b>\n\n"

            f"Entry: <b>"
            f"{format_price(trade.get('entry'))}"
            f"</b>\n"

            f"SL: <b>"
            f"{format_price(trade.get('sl'))}"
            f"</b>\n"

            f"TP: <b>"
            f"{format_price(trade.get('tp'))}"
            f"</b>\n"

            f"RR: <b>"
            f"{format_rr(trade.get('rr'))}"
            f"</b>\n\n"

            "⏳ <b>Сделка активна.</b>\n"
            "Binance Spot используется для мониторинга цены.",

            parse_mode="HTML",

            reply_markup=active_keyboard(),
        )

        return

    # ========================================================
    # CHARTS
    # ========================================================

    if data == "charts":

        await query.edit_message_text(

            "📈 <b>Выбери монету:</b>",

            parse_mode="HTML",

            reply_markup=chart_keyboard(),
        )

        return

    # ========================================================
    # READY CHART
    # ========================================================

    if data == "chart_ready":

        await query.message.reply_text(
            "📈 Выбери монету:",
            reply_markup=chart_keyboard(),
        )

        return

    # ========================================================
    # CHART COIN
    # ========================================================

    if data.startswith(
        "chart_"
    ):

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

        active = load_active_trades()

        active = [

            x
            for x in active
            if x.get(
                "status"
            ) == "OPEN"

        ]

        if not active:

            await query.edit_message_text(

                "📌 <b>Активных сделок нет.</b>",

                parse_mode="HTML",

                reply_markup=main_keyboard(),
            )

            return

        lines = [
            "📌 <b>АКТИВНЫЕ СДЕЛКИ</b>",
            "",
        ]

        for trade in active:

            lines.extend([

                f"💠 <b>{trade.get('coin')}</b> "
                f"{trade.get('direction')}",

                f"Entry: "
                f"<b>{format_price(trade.get('entry'))}</b>",

                f"SL: "
                f"<b>{format_price(trade.get('sl'))}</b>",

                f"TP: "
                f"<b>{format_price(trade.get('tp'))}</b>",

                f"RR: "
                f"<b>{format_rr(trade.get('rr'))}</b>",

                f"Цена: "
                f"<b>{format_price(trade.get('last_price'))}</b>",

                "",
            ])

        await query.edit_message_text(

            "\n".join(lines),

            parse_mode="HTML",

            reply_markup=active_keyboard(),
        )

        return

    # ========================================================
    # ACTIVE CHART
    # ========================================================

    if data == "active_chart":

        active = load_active_trades()

        active = [

            x
            for x in active
            if x.get(
                "status"
            ) == "OPEN"

        ]

        if not active:

            await query.edit_message_text(

                "📌 <b>Активных сделок нет.</b>",

                parse_mode="HTML",

                reply_markup=main_keyboard(),
            )

            return

        trade = active[0]

        coin = trade.get(
            "coin",
            "SOL",
        )

        await send_chart(
            query.message,
            coin,
            trade=trade,
        )

        return

    # ========================================================
    # JOURNAL
    # ========================================================

    if data == "journal":

        journal = load_journal()

        if not journal:

            await query.edit_message_text(

                "📒 <b>Журнал пока пуст.</b>",

                parse_mode="HTML",

                reply_markup=main_keyboard(),
            )

            return

        last = journal[-10:]

        lines = [
            "📒 <b>TRADEMIND — ЖУРНАЛ</b>",
            "",
        ]

        for trade in reversed(
            last
        ):

            result_type = trade.get(
                "result",
                trade.get(
                    "status",
                    "?",
                ),
            )

            icon = (

                "✅"
                if result_type == "TP"
                else "❌"
                if result_type == "SL"
                else "⚪"
            )

            pnl = trade.get(
                "pnl_percent"
            )

            pnl_text = (

                f"{pnl:+.2f}%"
                if pnl is not None
                else "N/A"
            )

            lines.extend([

                f"{icon} <b>"
                f"{trade.get('coin')}"
                f"</b> "
                f"{trade.get('direction')}",

                f"{result_type} • "
                f"PnL {pnl_text}",

                (
                    f"Entry "
                    f"{format_price(trade.get('entry'))}"
                    f" → "
                    f"{format_price(trade.get('exit_price'))}"
                ),

                "",
            ])

        await query.edit_message_text(

            "\n".join(lines),

            parse_mode="HTML",

            reply_markup=main_keyboard(),
        )

        return

    # ========================================================
    # SUBSCRIBE
    # ========================================================

    if data == "subscribe":

        chat_id = query.message.chat_id

        data_list = subscribers()

        if chat_id not in data_list:

            data_list.append(
                chat_id
            )

            save_subscribers(
                data_list
            )

        await query.edit_message_text(

            "🔔 <b>Уведомления включены.</b>",

            parse_mode="HTML",

            reply_markup=main_keyboard(),
        )

        return

    # ========================================================
    # UNSUBSCRIBE
    # ========================================================

    if data == "unsubscribe":

        chat_id = query.message.chat_id

        save_subscribers([

            x
            for x in subscribers()
            if x != chat_id

        ])

        await query.edit_message_text(

            "🔕 <b>Уведомления выключены.</b>",

            parse_mode="HTML",

            reply_markup=main_keyboard(),
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
            "Главное меню",
        ),

        (
            "market",
            "Рынок",
        ),

        (
            "search",
            "Поиск сетапа",
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
            "Включить уведомления",
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

        for command, description
        in commands

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

    application.run_polling()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()