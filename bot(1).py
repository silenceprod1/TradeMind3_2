import asyncio
import json
import os
import io
import struct
import zlib
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BotCommand,
    InputFile,
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

# Сканирование каждые 15 секунд
CHECK_INTERVAL = 15

# =========================================================
# 9 МОНЕТ
# =========================================================

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


# =========================================================
# JSON
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
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:
        print(f"JSON SAVE ERROR: {e}")


def load_subscribers():
    return load_json(
        SUBSCRIBERS_FILE,
        []
    )


def save_subscribers(data):
    save_json(
        SUBSCRIBERS_FILE,
        data
    )


def default_state():
    return {
        "active_coin": None,
        "active_symbol": None,
        "active_direction": None,
        "active_setup_key": None,
        "active_entry": None,
        "active_sl": None,
        "active_tp": None,
        "active_rr": None,
        "active_tp_reason": None,
        "active_sweep_extreme": None,
        "active_score": None,
        "active_stage": None,
        "last_alert": None,
        "coins": {}
    }


def load_state():
    state = load_json(
        STATE_FILE,
        default_state()
    )

    if not isinstance(state, dict):
        state = default_state()

    state.setdefault("coins", {})

    return state


def save_state(state):
    save_json(
        STATE_FILE,
        state
    )


# =========================================================
# KEYBOARDS
# =========================================================

def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📊 Рынок",
                callback_data="market"
            ),
            InlineKeyboardButton(
                "💧 Уровни",
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
                "📈 График",
                callback_data="chart"
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


def chart_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "₿ BTC",
                callback_data="chart_BTC"
            ),
            InlineKeyboardButton(
                "Ξ ETH",
                callback_data="chart_ETH"
            ),
            InlineKeyboardButton(
                "◎ SOL",
                callback_data="chart_SOL"
            ),
        ],
        [
            InlineKeyboardButton(
                "🟡 BNB",
                callback_data="chart_BNB"
            ),
            InlineKeyboardButton(
                "💠 XRP",
                callback_data="chart_XRP"
            ),
            InlineKeyboardButton(
                "🔥 HYPE",
                callback_data="chart_HYPE"
            ),
        ],
        [
            InlineKeyboardButton(
                "🐶 DOGE",
                callback_data="chart_DOGE"
            ),
            InlineKeyboardButton(
                "🔗 LINK",
                callback_data="chart_LINK"
            ),
            InlineKeyboardButton(
                "💧 SUI",
                callback_data="chart_SUI"
            ),
        ],
        [
            InlineKeyboardButton(
                "⬅️ Главное меню",
                callback_data="start"
            ),
        ],
    ])


def chart_coin_keyboard(coin):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "₿ BTC",
                callback_data="chart_BTC"
            ),
            InlineKeyboardButton(
                "Ξ ETH",
                callback_data="chart_ETH"
            ),
            InlineKeyboardButton(
                "◎ SOL",
                callback_data="chart_SOL"
            ),
        ],
        [
            InlineKeyboardButton(
                "🟡 BNB",
                callback_data="chart_BNB"
            ),
            InlineKeyboardButton(
                "💠 XRP",
                callback_data="chart_XRP"
            ),
            InlineKeyboardButton(
                "🔥 HYPE",
                callback_data="chart_HYPE"
            ),
        ],
        [
            InlineKeyboardButton(
                "🐶 DOGE",
                callback_data="chart_DOGE"
            ),
            InlineKeyboardButton(
                "🔗 LINK",
                callback_data="chart_LINK"
            ),
            InlineKeyboardButton(
                "💧 SUI",
                callback_data="chart_SUI"
            ),
        ],
        [
            InlineKeyboardButton(
                f"🔄 Обновить {coin}",
                callback_data=f"chart_{coin}"
            ),
        ],
        [
            InlineKeyboardButton(
                "⬅️ Главное меню",
                callback_data="start"
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
# FORMAT
# =========================================================

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


def format_rr(rr):
    if rr is None:
        return "N/A"

    try:
        return f"1:{float(rr):.2f}"

    except Exception:
        return "N/A"


def format_levels(levels, price):
    if not levels:
        return "💧 Крупные уровни не найдены."

    lines = []

    try:
        price = float(price)
    except Exception:
        price = 0

    for level in levels:

        try:
            level_price = float(
                level.get("price")
            )

        except Exception:
            continue

        level_type = level.get(
            "type",
            ""
        )

        if price:
            distance = (
                abs(level_price - price)
                / price
                * 100
            )

        else:
            distance = 0

        icon = (
            "🔴"
            if "HIGH" in level_type
            else "🟢"
        )

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

    if stage in (
        "CONFIRMED",
        "15M_CONFIRMED"
    ):
        return "🟡"

    if stage == "SWEPT":
        return "🟠"

    if stage == "WAIT":
        return "⏳"

    return "⚪"


def stage_text(stage):
    if stage == "READY":
        return "МОЖНО ВХОДИТЬ"

    if stage in (
        "CONFIRMED",
        "15M_CONFIRMED"
    ):
        return "15M подтверждение"

    if stage == "SWEPT":
        return "Sweep обнаружен"

    if stage == "WAIT":
        return "Ожидание"

    return "Ожидание"


# =========================================================
# ANALYSIS
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
    # Только крупная 1H ликвидность
    # -----------------------------------------------------

    major_levels = find_major_liquidity(
        candles_1h,
        price,
        max_levels=6
    )

    # -----------------------------------------------------
    # Sweep только относительно major liquidity
    # -----------------------------------------------------

    sweep = detect_sweep(
        candles_5m,
        major_levels
    )

    # -----------------------------------------------------
    # Анализ
    # -----------------------------------------------------

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
    result["candles_5m"] = candles_5m

    return result


def scan_all_coins():

    results = {}

    for coin, symbol in COINS.items():

        try:

            results[coin] = build_analysis(
                symbol
            )

        except Exception as e:

            print(
                f"{coin} ERROR: {e}"
            )

            results[coin] = {
                "error": str(e),
                "symbol": symbol,
            }

    return results


def find_first_ready(results):

    ready = []

    for coin, result in results.items():

        if result.get("error"):
            continue

        if (
            result.get("stage") == "READY"
            and result.get("score", 0) >= 80
        ):

            ready.append(
                (
                    result.get(
                        "score",
                        0
                    ),
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
# SIMPLE PNG ENGINE
# =========================================================

def png_chunk(chunk_type, data):
    return (
        struct.pack(
            ">I",
            len(data)
        )
        + chunk_type
        + data
        + struct.pack(
            ">I",
            zlib.crc32(
                chunk_type + data
            ) & 0xffffffff
        )
    )


def make_png(width, height, pixels):

    raw = bytearray()

    for row in pixels:
        raw.append(0)
        raw.extend(row)

    compressed = zlib.compress(
        bytes(raw),
        6
    )

    png = bytearray()

    png.extend(
        b"\x89PNG\r\n\x1a\n"
    )

    png.extend(
        png_chunk(
            b"IHDR",
            struct.pack(
                ">IIBBBBB",
                width,
                height,
                8,
                2,
                0,
                0,
                0
            )
        )
    )

    png.extend(
        png_chunk(
            b"IDAT",
            compressed
        )
    )

    png.extend(
        png_chunk(
            b"IEND",
            b""
        )
    )

    return bytes(png)


def new_canvas(width, height, color):

    row = bytearray(
        color * width
    )

    return [
        bytearray(row)
        for _ in range(height)
    ]


def set_pixel(pixels, x, y, color):

    height = len(pixels)
    width = len(pixels[0]) // 3

    if (
        x < 0
        or y < 0
        or x >= width
        or y >= height
    ):
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
    thickness=1
):

    dx = x2 - x1
    dy = y2 - y1

    steps = max(
        abs(dx),
        abs(dy),
        1
    )

    for i in range(steps + 1):

        x = int(
            x1 + dx * i / steps
        )

        y = int(
            y1 + dy * i / steps
        )

        radius = max(
            0,
            thickness // 2
        )

        for xx in range(
            x - radius,
            x + radius + 1
        ):

            for yy in range(
                y - radius,
                y + radius + 1
            ):

                set_pixel(
                    pixels,
                    xx,
                    yy,
                    color
                )


def draw_rect(
    pixels,
    x1,
    y1,
    x2,
    y2,
    color
):

    if x1 > x2:
        x1, x2 = x2, x1

    if y1 > y2:
        y1, y2 = y2, y1

    for y in range(
        max(0, y1),
        min(len(pixels), y2 + 1)
    ):

        for x in range(
            max(0, x1),
            min(
                len(pixels[0]) // 3,
                x2 + 1
            )
        ):

            set_pixel(
                pixels,
                x,
                y,
                color
            )


def candle_value(candle, key):

    value = candle.get(key)

    if value is None:

        aliases = {
            "open": ["o"],
            "high": ["h"],
            "low": ["l"],
            "close": ["c"],
            "open_time": [
                "time",
                "timestamp"
            ]
        }

        for alias in aliases.get(
            key,
            []
        ):

            if alias in candle:
                value = candle[alias]
                break

    try:
        return float(value)

    except Exception:
        return None


def render_chart_png(
    coin,
    result
):

    candles = result.get(
        "candles_5m",
        []
    )

    price = result.get(
        "price"
    )

    levels = result.get(
        "major_levels",
        []
    )

    sweep = result.get(
        "sweep"
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

    valid_candles = []

    for candle in candles:

        o = candle_value(
            candle,
            "open"
        )

        h = candle_value(
            candle,
            "high"
        )

        l = candle_value(
            candle,
            "low"
        )

        c = candle_value(
            candle,
            "close"
        )

        if None in (
            o,
            h,
            l,
            c
        ):
            continue

        valid_candles.append({
            "open": o,
            "high": h,
            "low": l,
            "close": c
        })

    valid_candles = valid_candles[-80:]

    if not valid_candles:

        raise Exception(
            "Нет 5M свечей для графика"
        )

    width = 1200
    height = 700

    left = 60
    right = 60
    top = 40
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

    background = (
        14,
        18,
        24
    )

    grid = (
        45,
        52,
        62
    )

    bullish = (
        50,
        210,
        130
    )

    bearish = (
        235,
        80,
        90
    )

    current_color = (
        80,
        170,
        255
    )

    high_color = (
        240,
        80,
        90
    )

    low_color = (
        50,
        210,
        130
    )

    sweep_color = (
        255,
        170,
        50
    )

    entry_color = (
        255,
        215,
        70
    )

    sl_color = (
        240,
        80,
        90
    )

    tp_color = (
        80,
        220,
        150
    )

    pixels = new_canvas(
        width,
        height,
        background
    )

    all_values = []

    for candle in valid_candles:

        all_values.extend([
            candle["high"],
            candle["low"]
        ])

    for level in levels:

        try:
            all_values.append(
                float(
                    level.get("price")
                )
            )

        except Exception:
            pass

    for value in (
        price,
        entry,
        sl,
        tp
    ):

        if value is not None:

            try:
                all_values.append(
                    float(value)
                )

            except Exception:
                pass

    min_price = min(all_values)
    max_price = max(all_values)

    if max_price == min_price:

        max_price += 1
        min_price -= 1

    padding = (
        max_price - min_price
    ) * 0.08

    max_price += padding
    min_price -= padding

    def price_to_y(value):

        ratio = (
            max_price - value
        ) / (
            max_price - min_price
        )

        return int(
            top
            + ratio * chart_height
        )

    # GRID

    for i in range(1, 8):

        y = (
            top
            + int(
                chart_height
                * i
                / 8
            )
        )

        draw_line(
            pixels,
            left,
            y,
            width - right,
            y,
            grid,
            1
        )

    # MAJOR LIQUIDITY

    for level in levels:

        try:

            level_price = float(
                level.get("price")
            )

        except Exception:
            continue

        y = price_to_y(
            level_price
        )

        if "HIGH" in level.get(
            "type",
            ""
        ):

            color = high_color

        else:

            color = low_color

        draw_line(
            pixels,
            left,
            y,
            width - right,
            y,
            color,
            2
        )

    # CANDLES

    count = len(
        valid_candles
    )

    candle_space = (
        chart_width
        / count
    )

    candle_width = max(
        3,
        int(
            candle_space * 0.55
        )
    )

    for i, candle in enumerate(
        valid_candles
    ):

        center_x = int(
            left
            + (
                i + 0.5
            ) * candle_space
        )

        open_y = price_to_y(
            candle["open"]
        )

        high_y = price_to_y(
            candle["high"]
        )

        low_y = price_to_y(
            candle["low"]
        )

        close_y = price_to_y(
            candle["close"]
        )

        color = (
            bullish
            if candle["close"]
            >= candle["open"]
            else bearish
        )

        draw_line(
            pixels,
            center_x,
            high_y,
            center_x,
            low_y,
            color,
            1
        )

        body_top = min(
            open_y,
            close_y
        )

        body_bottom = max(
            open_y,
            close_y
        )

        if body_bottom == body_top:
            body_bottom += 2

        draw_rect(
            pixels,
            center_x
            - candle_width // 2,
            body_top,
            center_x
            + candle_width // 2,
            body_bottom,
            color
        )

    # CURRENT PRICE

    if price is not None:

        try:

            y = price_to_y(
                float(price)
            )

            draw_line(
                pixels,
                left,
                y,
                width - right,
                y,
                current_color,
                2
            )

        except Exception:
            pass

    # SWEEP

    if sweep:

        sweep_price = (
            sweep.get("price")
            or sweep.get("level")
        )

        try:

            sweep_price = float(
                sweep_price
            )

            y = price_to_y(
                sweep_price
            )

            draw_line(
                pixels,
                left,
                y,
                width - right,
                y,
                sweep_color,
                3
            )

        except Exception:
            pass

    # ENTRY / SL / TP

    setup_lines = [
        (
            entry,
            entry_color,
            3
        ),
        (
            sl,
            sl_color,
            3
        ),
        (
            tp,
            tp_color,
            3
        ),
    ]

    for value, color, thickness in setup_lines:

        if value is None:
            continue

        try:

            y = price_to_y(
                float(value)
            )

            draw_line(
                pixels,
                left,
                y,
                width - right,
                y,
                color,
                thickness
            )

        except Exception:
            pass

    png = make_png(
        width,
        height,
        pixels
    )

    return io.BytesIO(
        png
    )


# =========================================================
# CHART STATUS
# =========================================================

def get_chart_status(result):

    stage = result.get(
        "stage",
        "WAIT"
    )

    direction = result.get(
        "direction"
    )

    if stage == "READY":

        return (
            "🟢 МОЖНО ВХОДИТЬ",
            direction
        )

    if stage in (
        "CONFIRMED",
        "15M_CONFIRMED"
    ):

        return (
            "🟡 ЖДЁМ 5M TRIGGER",
            direction
        )

    if stage == "SWEPT":

        return (
            "🟠 ЖДЁМ 15M CONFIRMATION",
            direction
        )

    return (
        "⏳ ЖДЁМ MAJOR LIQUIDITY SWEEP",
        None
    )


def build_chart_caption(
    coin,
    result
):

    price = result.get(
        "price"
    )

    levels = result.get(
        "major_levels",
        []
    )

    sweep = result.get(
        "sweep"
    )

    stage = result.get(
        "stage",
        "WAIT"
    )

    score = result.get(
        "score",
        0
    )

    direction = result.get(
        "direction"
    )

    status, _ = get_chart_status(
        result
    )

    lines = [
        f"📈 <b>TRADEMIND — {coin}</b>",
        "",
        f"💰 Цена: <b>{format_price(price)}</b>",
        f"{status}",
        f"⭐ Score: <b>{score}/100</b>",
        "",
        "💧 <b>MAJOR LIQUIDITY</b>",
        format_levels(
            levels,
            price
        ),
    ]

    if direction:

        lines.extend([
            "",
            f"📐 Направление: "
            f"<b>{direction}</b>"
        ])

    if stage == "WAIT":

        lines.extend([
            "",
            "⏳ <b>ЖДЁМ:</b>",
            "Major Liquidity → Sweep",
            "",
            "❌ В середине движения не входим."
        ])

    elif stage == "SWEPT":

        lines.extend([
            "",
            "💧 <b>SWEEP ОБНАРУЖЕН</b>",
            "✅ Крупная ликвидность снята",
            "⏳ Ждём 15M confirmation",
            "❌ Вход запрещён"
        ])

        if sweep:

            sweep_price = (
                sweep.get("price")
                or sweep.get("level")
            )

            if sweep_price is not None:

                lines.extend([
                    "",
                    f"Sweep: "
                    f"<b>{format_price(sweep_price)}</b>"
                ])

    elif stage in (
        "CONFIRMED",
        "15M_CONFIRMED"
    ):

        lines.extend([
            "",
            "✅ Sweep",
            "✅ 15M confirmation",
            "⏳ <b>ЖДЁМ 5M TRIGGER</b>",
            "❌ Вход запрещён"
        ])

    elif stage == "READY":

        rr = result.get(
            "rr"
        )

        tp_reason = result.get(
            "tp_reason"
        )

        lines.extend([
            "",
            "🔥 <b>ПОЛНОЕ ПОДТВЕРЖДЕНИЕ</b>",
            "",
            f"Entry: <b>{format_price(result.get('entry'))}</b>",
            f"SL: <b>{format_price(result.get('sl'))}</b>",
            f"TP: <b>{format_price(result.get('tp'))}</b>",
            f"RR: <b>{format_rr(rr)}</b>",
        ])

        if tp_reason:

            lines.append(
                f"🎯 TP: {tp_reason}"
            )

        lines.extend([
            "",
            "🟢 <b>МОЖНО ВХОДИТЬ</b>"
        ])

    lines.extend([
        "",
        "5M • последние свечи",
        "1H MAJOR → SWEEP → 15M → 5M"
    ])

    return "\n".join(lines)


# =========================================================
# SEND CHART
# =========================================================

async def send_chart(
    message,
    coin
):

    try:

        result = build_analysis(
            COINS[coin]
        )

        image = render_chart_png(
            coin,
            result
        )

        image.seek(0)

        caption = build_chart_caption(
            coin,
            result
        )

        await message.reply_photo(
            photo=InputFile(
                image,
                filename=f"{coin.lower()}_5m.png"
            ),
            caption=caption,
            parse_mode="HTML",
            reply_markup=chart_coin_keyboard(
                coin
            )
        )

    except Exception as e:

        print(
            f"CHART ERROR {coin}: {e}"
        )

        await message.reply_text(
            f"❌ Ошибка графика {coin}:\n\n{e}",
            reply_markup=back_keyboard()
        )


# =========================================================
# MESSAGES
# =========================================================

def build_market_message(results):

    lines = [
        "📊 <b>TRADEMIND — РЫНОК</b>",
        ""
    ]

    for coin in COINS:

        result = results.get(
            coin
        )

        if not result:
            continue

        if result.get("error"):

            lines.extend([
                f"❌ <b>{coin}</b>",
                "Ошибка получения данных",
                ""
            ])

            continue

        price = result.get(
            "price"
        )

        score = result.get(
            "score",
            0
        )

        stage = result.get(
            "stage",
            "WAIT"
        )

        lines.extend([
            f"💠 <b>{coin}</b> "
            f"{format_price(price)}",
            "",
            f"{stage_icon(stage)} "
            f"{stage_text(stage)}",
            f"Score: <b>{score}/100</b>",
            "",
            "💧 <b>Крупная ликвидность:</b>",
            format_levels(
                result.get(
                    "major_levels",
                    []
                ),
                price
            ),
            "",
            "────────────",
            ""
        ])

    lines.extend([
        "1H → Sweep → 15M → 5M",
        "",
        "❌ В середине движения не входим."
    ])

    return "\n".join(lines)


def build_levels_message(results):

    lines = [
        "💧 <b>TRADEMIND — КЛЮЧЕВЫЕ УРОВНИ</b>",
        "",
        "Используем только крупную ликвидность 1H.",
        "Мелкие локальные уровни не учитываются.",
        ""
    ]

    for coin in COINS:

        result = results.get(
            coin
        )

        if not result:
            continue

        if result.get("error"):
            continue

        price = result.get(
            "price"
        )

        lines.extend([
            f"💠 <b>{coin}</b> "
            f"{format_price(price)}",
            "",
            format_levels(
                result.get(
                    "major_levels",
                    []
                ),
                price
            ),
            "",
            "────────────",
            ""
        ])

    return "\n".join(lines)


def build_sol_message(result):

    if result.get("error"):

        return (
            "❌ <b>SOL</b>\n\n"
            "Ошибка получения данных."
        )

    price = result.get(
        "price"
    )

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
        f"Score: <b>{score}/100</b>"
    ]

    if direction:

        lines.extend([
            "",
            f"Направление: "
            f"<b>{direction}</b>"
        ])

    lines.extend([
        "",
        "💧 <b>КРУПНАЯ ЛИКВИДНОСТЬ:</b>",
        format_levels(
            result.get(
                "major_levels",
                []
            ),
            price
        )
    ])

    if result.get("entry") is not None:

        rr = result.get(
            "rr"
        )

        tp_reason = result.get(
            "tp_reason"
        )

        lines.extend([
            "",
            "🎯 <b>СЕТАП</b>",
            "",
            f"Entry: "
            f"<b>{format_price(result.get('entry'))}</b>",
            f"SL: "
            f"<b>{format_price(result.get('sl'))}</b>",
            f"TP: "
            f"<b>{format_price(result.get('tp'))}</b>",
            "",
            f"RR: <b>{format_rr(rr)}</b>"
        ])

        if result.get("sweep_extreme") is not None:

            lines.extend([
                f"💧 Sweep extreme: "
                f"<b>{format_price(result.get('sweep_extreme'))}</b>"
            ])

        if tp_reason:

            lines.append(
                f"🎯 {tp_reason}"
            )

    reason = result.get(
        "reason"
    )

    if reason:

        lines.extend([
            "",
            "Причина:",
            str(reason)
        ])

    return "\n".join(lines)


def build_search_message(results):

    lines = [
        "🔎 <b>ПОИСК СЕТАПА</b>",
        ""
    ]

    ready = find_first_ready(
        results
    )

    if ready:

        score, coin, result = ready

        rr = result.get(
            "rr"
        )

        tp_reason = result.get(
            "tp_reason"
        )

        lines.extend([
            "🟢 <b>НАЙДЕН СЕТАП</b>",
            "",
            f"Монета: <b>{coin}</b>",
            f"Направление: "
            f"<b>{result.get('direction')}</b>",
            f"Score: <b>{score}/100</b>",
            "",
            f"Entry: "
            f"<b>{format_price(result.get('entry'))}</b>",
            f"SL: "
            f"<b>{format_price(result.get('sl'))}</b>",
            f"TP: "
            f"<b>{format_price(result.get('tp'))}</b>",
            "",
            f"RR: <b>{format_rr(rr)}</b>",
        ])

        if tp_reason:

            lines.append(
                f"🎯 {tp_reason}"
            )

        lines.extend([
            "",
            "🔥 Полное подтверждение получено."
        ])

        return "\n".join(lines)

    lines.extend([
        "❌ Готового входа сейчас нет.",
        ""
    ])

    for coin in COINS:

        result = results.get(
            coin
        )

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
            f"{stage_text(stage)} — "
            f"{score}/100"
        )

    lines.extend([
        "",
        "Ждём → Sweep → 15M → 5M.",
        "❌ В середине движения не входим."
    ])

    return "\n".join(lines)


# =========================================================
# COMMANDS
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    text = (
        "🤖 <b>TRADEMIND 3.9</b>\n\n"
        "Мониторинг:\n"
        "• BTC\n"
        "• ETH\n"
        "• SOL\n"
        "• BNB\n"
        "• XRP\n"
        "• HYPE\n"
        "• DOGE\n"
        "• LINK\n"
        "• SUI\n\n"
        "Сканирование: <b>каждые 15 секунд</b>\n\n"
        "Стратегия:\n"
        "1H → Major Liquidity → Sweep → 15M → 5M\n\n"
        "TP:\n"
        "• ближайшая встречная ликвидность ограничивает TP\n"
        "• если ликвидность дальше 2R — TP = 2R\n\n"
        "Вход только после полного подтверждения."
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=main_keyboard()
    )


async def market_command(
    update,
    context
):

    results = scan_all_coins()

    await update.message.reply_text(
        build_market_message(
            results
        ),
        parse_mode="HTML",
        reply_markup=back_keyboard()
    )


async def levels_command(
    update,
    context
):

    results = scan_all_coins()

    await update.message.reply_text(
        build_levels_message(
            results
        ),
        parse_mode="HTML",
        reply_markup=back_keyboard()
    )


async def search_command(
    update,
    context
):

    results = scan_all_coins()

    await update.message.reply_text(
        build_search_message(
            results
        ),
        parse_mode="HTML",
        reply_markup=back_keyboard()
    )


async def sol_command(
    update,
    context
):

    try:

        result = build_analysis(
            "SOLUSDT"
        )

        await update.message.reply_text(
            build_sol_message(
                result
            ),
            parse_mode="HTML",
            reply_markup=back_keyboard()
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Ошибка SOL:\n{e}",
            reply_markup=back_keyboard()
        )


async def chart_command(
    update,
    context
):

    coin = "SOL"

    if context.args:

        requested = (
            context.args[0]
            .upper()
        )

        if requested in COINS:
            coin = requested

    await send_chart(
        update.message,
        coin
    )


# =========================================================
# SUBSCRIBE
# =========================================================

async def subscribe_command(
    update,
    context
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
            "🔔 <b>Уведомления включены.</b>\n\n"
            "Отслеживаю 9 монет:\n"
            "BTC • ETH • SOL • BNB • XRP\n"
            "HYPE • DOGE • LINK • SUI"
        )

    else:

        text = (
            "🔔 Уведомления уже включены."
        )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=back_keyboard()
    )


async def unsubscribe_command(
    update,
    context
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
        reply_markup=back_keyboard()
    )


# =========================================================
# STATUS
# =========================================================

async def status_command(
    update,
    context
):

    state = load_state()

    if state.get("active_coin"):

        text = (
            "📊 <b>TRADEMIND — СТАТУС</b>\n\n"
            f"🟢 Активная монета: "
            f"<b>{state.get('active_coin')}</b>\n"
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
            f"<b>{format_price(state.get('active_tp'))}</b>\n"
            f"RR: "
            f"<b>{format_rr(state.get('active_rr'))}</b>"
        )

    else:

        text = (
            "📊 <b>TRADEMIND — СТАТУС</b>\n\n"
            "🟢 Активного входа нет.\n\n"
            "Мониторинг:\n"
            "BTC • ETH • SOL • BNB • XRP\n"
            "HYPE • DOGE • LINK • SUI\n\n"
            "Интервал: <b>15 секунд</b>"
        )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=back_keyboard()
    )


# =========================================================
# JOURNAL
# =========================================================

async def journal_command(
    update,
    context
):

    text = (
        "📒 <b>TRADEMIND — ЖУРНАЛ</b>\n\n"
        "Автоматический журнал сделок "
        "подключим следующим этапом.\n\n"
        "Будем записывать:\n"
        "• Coin\n"
        "• Direction\n"
        "• Entry\n"
        "• SL\n"
        "• TP\n"
        "• фактический RR\n"
        "• Win / Loss\n"
        "• R\n"
        "• Winrate\n"
        "• 10 тестовых сделок"
    )

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=back_keyboard()
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

    print(
        f"Scan interval: {CHECK_INTERVAL} seconds"
    )

    print(
        "Coins:",
        ", ".join(COINS.keys())
    )

    while True:

        cycle_start = asyncio.get_running_loop().time()

        try:

            state = load_state()

            # -------------------------------------------------
            # ВСЕГДА СКАНИРУЕМ ВСЕ 9 МОНЕТ
            # -------------------------------------------------

            results = scan_all_coins()

            active_coin = state.get(
                "active_coin"
            )

            # -------------------------------------------------
            # READY
            # -------------------------------------------------
            #
            # Если уже есть активная сделка/сетап,
            # новый READY не открываем.
            #
            # Но остальные монеты всё равно продолжают
            # сканироваться каждые 15 секунд.
            # -------------------------------------------------

            if not active_coin:

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

                    rr = result.get(
                        "rr"
                    )

                    tp_reason = result.get(
                        "tp_reason"
                    )

                    sweep_extreme = result.get(
                        "sweep_extreme"
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

                            "active_coin":
                                coin,

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

                            "active_rr":
                                rr,

                            "active_tp_reason":
                                tp_reason,

                            "active_sweep_extreme":
                                sweep_extreme,

                            "active_score":
                                score,

                            "active_stage":
                                "READY",

                            "last_alert":
                                datetime.utcnow().isoformat()
                        })

                        save_state(
                            state
                        )

                        text = (
                            "🚨 <b>TRADEMIND — МОЖНО ВХОДИТЬ</b>\n\n"
                            f"💠 Монета: <b>{coin}</b>\n"
                            f"📐 Направление: <b>{direction}</b>\n"
                            f"⭐ Score: <b>{score}/100</b>\n\n"
                            f"Entry: <b>{format_price(entry)}</b>\n"
                            f"SL: <b>{format_price(sl)}</b>\n"
                            f"TP: <b>{format_price(tp)}</b>\n"
                            f"RR: <b>{format_rr(rr)}</b>\n"
                        )

                        if sweep_extreme is not None:

                            text += (
                                f"💧 Sweep extreme: "
                                f"<b>{format_price(sweep_extreme)}</b>\n"
                            )

                        if tp_reason:

                            text += (
                                f"\n🎯 {tp_reason}\n"
                            )

                        text += (
                            "\n🔥 Полное подтверждение:\n"
                            "1H → Sweep → 15M → 5M"
                        )

                        await broadcast(
                            application,
                            text
                        )

            # -------------------------------------------------
            # SWEEP + 15M CONFIRMATION
            # -------------------------------------------------

            for coin, result in results.items():

                if result.get("error"):
                    continue

                stage = result.get(
                    "stage"
                )

                direction = result.get(
                    "direction"
                )

                sweep = result.get(
                    "sweep"
                )

                coin_state = (
                    state["coins"]
                    .setdefault(
                        coin,
                        {}
                    )
                )

                # -----------------------------
                # SWEEP
                # -----------------------------

                if sweep:

                    sweep_price = (
                        sweep.get("price")
                        or sweep.get("level")
                    )

                    sweep_key = (
                        f"{coin}_"
                        f"{direction}_"
                        f"{sweep_price}"
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
                            "🔎 <b>TRADEMIND — SWEEP</b>\n\n"
                            f"💠 Монета: <b>{coin}</b>\n"
                            f"Направление: <b>{direction}</b>\n\n"
                            "💧 Крупная ликвидность снята.\n\n"
                            "⏳ Ждём подтверждение 15M.\n\n"
                            "❌ Вход пока запрещён."
                        )

                        await broadcast(
                            application,
                            text
                        )

                # -----------------------------
                # 15M CONFIRMATION
                # -----------------------------

                if stage in (
                    "CONFIRMED",
                    "15M_CONFIRMED"
                ):

                    confirmation_time = (
                        result.get(
                            "confirmation_15m_time"
                        )
                        or result.get(
                            "confirmation_time"
                        )
                    )

                    confirmation_key = (
                        f"{coin}_"
                        f"{direction}_"
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
                            "🟡 <b>TRADEMIND — 15M CONFIRMATION</b>\n\n"
                            f"💠 Монета: <b>{coin}</b>\n"
                            f"📐 Направление: <b>{direction}</b>\n\n"
                            "✅ Sweep\n"
                            "✅ 15M confirmation\n\n"
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

        # -----------------------------------------------------
        # РОВНО ПРИМЕРНО КАЖДЫЕ 15 СЕКУНД
        # -----------------------------------------------------

        elapsed = (
            asyncio.get_running_loop().time()
            - cycle_start
        )

        sleep_time = max(
            1,
            CHECK_INTERVAL - elapsed
        )

        await asyncio.sleep(
            sleep_time
        )


# =========================================================
# CALLBACKS
# =========================================================

async def callbacks(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    data = query.data

    # -------------------------------------------------------
    # HOME
    # -------------------------------------------------------

    if data == "start":

        await query.edit_message_text(
            "🤖 <b>TRADEMIND 3.9</b>\n\n"
            "Мониторинг 9 монет:\n"
            "BTC • ETH • SOL • BNB • XRP\n"
            "HYPE • DOGE • LINK • SUI\n\n"
            "Сканирование: <b>каждые 15 секунд</b>\n\n"
            "Выбери действие:",
            parse_mode="HTML",
            reply_markup=main_keyboard()
        )

        return

    # -------------------------------------------------------
    # MARKET
    # -------------------------------------------------------

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

    # -------------------------------------------------------
    # LEVELS
    # -------------------------------------------------------

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

    # -------------------------------------------------------
    # SEARCH
    # -------------------------------------------------------

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

    # -------------------------------------------------------
    # CHART MENU
    # -------------------------------------------------------

    if data == "chart":

        await query.edit_message_text(
            "📈 <b>TRADEMIND — ГРАФИК</b>\n\n"
            "Выбери монету:",
            parse_mode="HTML",
            reply_markup=chart_keyboard()
        )

        return

    # -------------------------------------------------------
    # CHART
    # -------------------------------------------------------

    if data.startswith("chart_"):

        coin = data.split(
            "_",
            1
        )[1]

        if coin not in COINS:

            await query.answer(
                "Неизвестная монета",
                show_alert=True
            )

            return

        await send_chart(
            query.message,
            coin
        )

        return

    # -------------------------------------------------------
    # SOL
    # -------------------------------------------------------

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

    # -------------------------------------------------------
    # STATUS
    # -------------------------------------------------------

    if data == "status":

        state = load_state()

        if state.get(
            "active_coin"
        ):

            text = (
                "📊 <b>СТАТУС</b>\n\n"
                f"🟢 Активно: "
                f"<b>{state.get('active_coin')}</b>\n"
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
                f"<b>{format_price(state.get('active_tp'))}</b>\n"
                f"RR: "
                f"<b>{format_rr(state.get('active_rr'))}</b>\n\n"
                f"🔄 Сканирование: "
                f"<b>каждые {CHECK_INTERVAL} сек.</b>"
            )

        else:

            text = (
                "📊 <b>СТАТУС</b>\n\n"
                "🟢 Активного сетапа нет.\n\n"
                "Мониторинг:\n"
                "BTC • ETH • SOL • BNB • XRP\n"
                "HYPE • DOGE • LINK • SUI\n\n"
                f"🔄 Интервал: <b>{CHECK_INTERVAL} секунд</b>"
            )

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=back_keyboard()
        )

        return

    # -------------------------------------------------------
    # SUBSCRIBE
    # -------------------------------------------------------

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

            text = (
                "🔔 <b>Уведомления включены.</b>\n\n"
                "Отслеживаются 9 монет."
            )

        else:

            text = (
                "🔔 Уведомления уже включены."
            )

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=back_keyboard()
        )

        return

    # -------------------------------------------------------
    # UNSUBSCRIBE
    # -------------------------------------------------------

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

            text = (
                "🔕 <b>Уведомления выключены.</b>"
            )

        else:

            text = (
                "🔕 Уведомления уже выключены."
            )

        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=back_keyboard()
        )

        return

    # -------------------------------------------------------
    # JOURNAL
    # -------------------------------------------------------

    if data == "journal":

        await query.edit_message_text(
            "📒 <b>TRADEMIND — ЖУРНАЛ</b>\n\n"
            "Автоматический журнал подключим "
            "следующим этапом.",
            parse_mode="HTML",
            reply_markup=back_keyboard()
        )

        return


# =========================================================
# COMMANDS
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
            "Рынок 9 монет"
        ),

        BotCommand(
            "levels",
            "Крупные уровни"
        ),

        BotCommand(
            "search",
            "Поиск сетапа"
        ),

        BotCommand(
            "chart",
            "График"
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
# POST INIT
# =========================================================

async def post_init(
    application
):

    await set_commands(
        application
    )

    application.create_task(
        monitor(application),
        name="trademind_monitor"
    )


# =========================================================
# MAIN
# =========================================================

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
            "chart",
            chart_command
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

    application.add_handler(
        CallbackQueryHandler(
            callbacks
        )
    )

    print(
        "TradeMind 3.9 started."
    )

    print(
        f"Scan interval: {CHECK_INTERVAL} seconds"
    )

    print(
        "Coins:",
        ", ".join(COINS.keys())
    )

    application.run_polling()


if __name__ == "__main__":
    main()