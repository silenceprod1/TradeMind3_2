import asyncio
import io
import json
import os
import struct
import zlib

from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)

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


SUBSCRIBERS_FILE = "subscribers.json"


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


def format_price(
    price,
):
    if price is None:
        return "N/A"

    price = float(price)

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

    return f"1:{float(value):.2f}"


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


def levels_text(
    levels,
    current_price,
):

    if not levels:

        return "нет крупных уровней"

    lines = []

    for level in levels:

        distance = (
            abs(
                float(level["price"])
                - current_price
            )
            / current_price
            * 100
        )

        if level["side"] == "SHORT":

            icon = "🔴"

        else:

            icon = "🟢"

        lines.append(
            f"{icon} "
            f"{level['type']} "
            f"{format_price(level['price'])} "
            f"• {distance:.2f}% "
            f"• S{level.get('strength', 0):.0f}"
        )

    return "\n".join(lines)


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
        "symbol": symbol,
        "price": price,
        "major_levels": levels,
        "sweep": sweep,
        "candles_5m": market["candles_5m"],
        "candles_1h": market["candles_1h"],
    })

    return result


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


def market_message(
    results,
):

    lines = [
        "📊 <b>TRADEMIND 6.3 — РЫНОК</b>",
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
        f"📈 <b>TRADEMIND 6.3 — {coin}</b>",
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
                f"{format_price(result['entry'])}"
                "</b>"
            ),

            (
                "SL: <b>"
                f"{format_price(result['sl'])}"
                "</b>"
            ),

            (
                "TP: <b>"
                f"{format_price(result['tp'])}"
                "</b>"
            ),

            (
                "RR: <b>"
                f"{format_rr(result['rr'])}"
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

    lines.extend([
        "",
        "Причина: "
        + str(
            result.get(
                "reason",
                "",
            )
        ),
    ])

    return "\n".join(lines)


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


def chart_png(
    result,
):

    candles = result[
        "candles_5m"
    ][-90:]

    levels = result.get(
        "major_levels",
        [],
    )

    values = []

    for candle in candles:

        values.append(
            candle["high"]
        )

        values.append(
            candle["low"]
        )

    for level in levels:

        values.append(
            float(level["price"])
        )

    for key in (
        "price",
        "entry",
        "sl",
        "tp",
    ):

        if result.get(key) is not None:

            values.append(
                float(result[key])
            )

    if not values:

        values = [
            float(result["price"]) - 1,
            float(result["price"]) + 1,
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

    for level in levels:

        color = (
            235,
            80,
            90,
        )

        if level["side"] == "LONG":

            color = (
                50,
                210,
                130,
            )

        draw_line(
            pixels,
            left,
            y(
                float(
                    level["price"]
                )
            ),
            width - right,
            y(
                float(
                    level["price"]
                )
            ),
            color,
            2,
        )

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

        x = int(
            left
            + (
                i + 0.5
            )
            * spacing
        )

        open_price = candle[
            "open"
        ]

        high_price = candle[
            "high"
        ]

        low_price = candle[
            "low"
        ]

        close_price = candle[
            "close"
        ]

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

        draw_line(
            pixels,
            x,
            y(high_price),
            x,
            y(low_price),
            color,
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
                    color,
                )

    current_price = float(
        result["price"]
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

    sweep = result.get(
        "sweep"
    )

    if sweep:

        draw_line(
            pixels,
            left,
            y(
                float(
                    sweep["level"]
                )
            ),
            width - right,
            y(
                float(
                    sweep["level"]
                )
            ),
            (
                255,
                170,
                40,
            ),
            3,
        )

    special_lines = [
        (
            "entry",
            (
                255,
                215,
                70,
            ),
        ),

        (
            "sl",
            (
                235,
                80,
                90,
            ),
        ),

        (
            "tp",
            (
                80,
                220,
                150,
            ),
        ),
    ]

    for key, color in special_lines:

        if result.get(key) is None:
            continue

        draw_line(
            pixels,
            left,
            y(
                float(
                    result[key]
                )
            ),
            width - right,
            y(
                float(
                    result[key]
                )
            ),
            color,
            3,
        )

    image_bytes = make_png(
        width,
        height,
        pixels,
    )

    return io.BytesIO(
        image_bytes
    )


async def send_chart(
    message,
    coin,
):

    try:

        result = await asyncio.to_thread(
            build_analysis,
            COINS[coin],
        )

        image = chart_png(
            result
        )

        image.seek(0)

        await message.reply_photo(
            photo=InputFile(
                image,
                filename=(
                    f"{coin.lower()}_5m.png"
                ),
            ),

            caption=coin_message(
                coin,
                result,
            ),

            parse_mode="HTML",

            reply_markup=chart_keyboard(),
        )

    except Exception as exc:

        await message.reply_text(
            f"❌ График {coin}: {exc}"
        )


async def broadcast(
    application,
    text,
):

    ids = subscribers()

    if not ids:
        return

    async def send_one(
        chat_id,
    ):

        try:

            await application.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
            )

        except Exception as exc:

            print(
                "BROADCAST ERROR",
                chat_id,
                exc,
            )

    await asyncio.gather(
        *(
            send_one(chat_id)
            for chat_id in ids
        ),
        return_exceptions=True,
    )


async def start(
    update,
    context,
):

    await update.message.reply_text(

        (
            f"🤖 <b>TRADEMIND "
            f"{STRATEGY_VERSION}</b>\n\n"

            "9 монет • Binance Spot\n\n"

            "<b>"
            "1H → Major Liquidity → "
            "Sweep → 15M → 5M ILM → Entry"
            "</b>\n\n"

            "D1/W1: отключены\n"

            "Лимит сделок в день: отключён\n"

            "BingX auto: OFF"
        ),

        parse_mode="HTML",

        reply_markup=main_keyboard(),
    )


async def market_command(
    update,
    context,
):

    results = await asyncio.to_thread(
        scan_all
    )

    await update.message.reply_text(
        market_message(results),
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


async def search_command(
    update,
    context,
):

    results = await asyncio.to_thread(
        scan_all
    )

    ready = []

    for coin, result in results.items():

        if result.get("stage") != "READY":
            continue

        if (
            result.get("score", 0)
            >= MIN_SCORE_READY
        ):

            ready.append(
                (
                    result.get("score", 0),
                    coin,
                    result,
                )
            )

    if ready:

        _, coin, result = max(
            ready,
            key=lambda x: x[0],
        )

        await update.message.reply_text(
            coin_message(
                coin,
                result,
            ),
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )

    else:

        await update.message.reply_text(

            (
                "🔎 <b>ГОТОВОГО СЕТАПА НЕТ</b>\n\n"

                "Ждём:\n"
                "1H → Major Liquidity → "
                "Sweep → 15M → 5M ILM."
            ),

            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )


async def chart_command(
    update,
    context,
):

    coin = "SOL"

    if (
        context.args
        and context.args[0].upper()
        in COINS
    ):

        coin = (
            context.args[0]
            .upper()
        )

    await send_chart(
        update.message,
        coin,
    )


async def subscribe_command(
    update,
    context,
):

    ids = subscribers()

    chat_id = update.effective_chat.id

    if chat_id not in ids:

        ids.append(chat_id)

        save_subscribers(
            ids
        )

    await update.message.reply_text(
        "🔔 Уведомления включены.",
        reply_markup=main_keyboard(),
    )


async def unsubscribe_command(
    update,
    context,
):

    chat_id = update.effective_chat.id

    ids = [
        x
        for x in subscribers()
        if x != chat_id
    ]

    save_subscribers(
        ids
    )

    await update.message.reply_text(
        "🔕 Уведомления выключены.",
        reply_markup=main_keyboard(),
    )


async def status_command(
    update,
    context,
):

    await update.message.reply_text(

        (
            f"📊 <b>TRADEMIND "
            f"{STRATEGY_VERSION}</b>\n\n"

            f"Сканирование: "
            f"{CHECK_INTERVAL} сек.\n"

            f"Монет: {len(COINS)}\n"

            "D1/W1: OFF\n"

            "Дневной лимит: OFF\n"

            "BingX auto: OFF"
        ),

        parse_mode="HTML",

        reply_markup=main_keyboard(),
    )


async def monitor(
    application,
):

    print(
        "TradeMind monitor started."
    )

    print(
        f"Scan interval: "
        f"{CHECK_INTERVAL}s"
    )

    last_state = {}

    while True:

        started = (
            asyncio.get_running_loop()
            .time()
        )

        try:

            results = await asyncio.to_thread(
                scan_all
            )

            for coin, result in results.items():

                if result.get("error"):
                    continue

                stage = result.get(
                    "stage"
                )

                sweep = result.get(
                    "sweep"
                ) or {}

                key = (
                    f"{coin}:"
                    f"{stage}:"
                    f"{sweep.get('open_time')}:"
                    f"{result.get('confirmation_15m_time')}"
                )

                if (
                    last_state.get(coin)
                    == key
                ):
                    continue

                last_state[coin] = key

                if stage == "SWEPT":

                    await broadcast(
                        application,

                        (
                            "🔎 <b>"
                            "TRADEMIND — SWEEP"
                            "</b>\n\n"

                            f"💠 {coin}\n"

                            f"📐 "
                            f"{result.get('direction')}\n\n"

                            "💧 Крупная "
                            "ликвидность снята.\n"

                            "⏳ Ждём "
                            "15M confirmation.\n"

                            "❌ Вход запрещён."
                        ),
                    )

                elif stage == "15M_CONFIRMED":

                    await broadcast(
                        application,

                        (
                            "🟡 <b>"
                            "TRADEMIND — "
                            "15M CONFIRMATION"
                            "</b>\n\n"

                            f"💠 {coin}\n"

                            f"📐 "
                            f"{result.get('direction')}\n\n"

                            "✅ Sweep\n"
                            "✅ 15M confirmation\n"

                            "⏳ Ждём "
                            "5M ILM.\n"

                            "❌ Вход запрещён."
                        ),
                    )

                elif stage == "READY":

                    await broadcast(
                        application,

                        (
                            "🚨 <b>"
                            "TRADEMIND — "
                            "МОЖНО ВХОДИТЬ"
                            "</b>\n\n"

                            f"💠 {coin}\n"

                            f"📐 "
                            f"{result.get('direction')}\n"

                            f"⭐ "
                            f"{result.get('score')}/100\n\n"

                            "Entry: <b>"
                            f"{format_price(result.get('entry'))}"
                            "</b>\n"

                            "SL: <b>"
                            f"{format_price(result.get('sl'))}"
                            "</b>\n"

                            "TP: <b>"
                            f"{format_price(result.get('tp'))}"
                            "</b>\n"

                            "RR: <b>"
                            f"{format_rr(result.get('rr'))}"
                            "</b>\n\n"

                            "🎯 TP = следующая "
                            "свежая major liquidity."
                        ),
                    )

        except Exception as exc:

            print(
                "MONITOR ERROR:",
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


async def callbacks(
    update,
    context,
):

    query = update.callback_query

    await query.answer()

    data = query.data

    if data == "start":

        await query.edit_message_text(

            (
                f"🤖 <b>TRADEMIND "
                f"{STRATEGY_VERSION}</b>\n\n"

                "1H → Major Liquidity → "
                "Sweep → 15M → 5M ILM"
            ),

            parse_mode="HTML",

            reply_markup=main_keyboard(),
        )

        return

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

    if data == "search":

        results = await asyncio.to_thread(
            scan_all
        )

        ready = [
            (
                r.get("score", 0),
                c,
                r,
            )
            for c, r in results.items()
            if (
                r.get("stage")
                == "READY"
                and r.get("score", 0)
                >= MIN_SCORE_READY
            )
        ]

        if ready:

            _, coin, result = max(
                ready,
                key=lambda x: x[0],
            )

            await query.edit_message_text(

                coin_message(
                    coin,
                    result,
                ),

                parse_mode="HTML",

                reply_markup=main_keyboard(),
            )

        else:

            await query.edit_message_text(

                (
                    "🔎 <b>"
                    "ГОТОВОГО СЕТАПА НЕТ"
                    "</b>\n\n"

                    "Ждём:\n"
                    "1H → Major Liquidity → "
                    "Sweep → 15M → 5M ILM."
                ),

                parse_mode="HTML",

                reply_markup=main_keyboard(),
            )

        return

    if data == "charts":

        await query.edit_message_text(

            "📈 <b>Выбери монету:</b>",

            parse_mode="HTML",

            reply_markup=chart_keyboard(),
        )

        return

    if data.startswith("chart_"):

        coin = data.split(
            "_",
            1,
        )[1]

        if coin not in COINS:

            await query.answer(
                "Неизвестная монета",
                show_alert=True,
            )

            return

        await send_chart(
            query.message,
            coin,
        )

        return

    if data == "subscribe":

        ids = subscribers()

        chat_id = query.message.chat_id

        if chat_id not in ids:

            ids.append(chat_id)

            save_subscribers(
                ids
            )

        await query.edit_message_text(

            "🔔 <b>Уведомления включены.</b>",

            parse_mode="HTML",

            reply_markup=main_keyboard(),
        )

        return

    if data == "unsubscribe":

        chat_id = query.message.chat_id

        ids = [
            x
            for x in subscribers()
            if x != chat_id
        ]

        save_subscribers(
            ids
        )

        await query.edit_message_text(

            "🔕 <b>Уведомления выключены.</b>",

            parse_mode="HTML",

            reply_markup=main_keyboard(),
        )

        return


async def post_init(
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
            "search",
            "Поиск сетапа",
        ),

        BotCommand(
            "chart",
            "График",
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
    ]

    await application.bot.set_my_commands(
        commands
    )

    application.create_task(
        monitor(application)
    )


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
            market_command,
        ),

        (
            "search",
            search_command,
        ),

        (
            "chart",
            chart_command,
        ),

        (
            "status",
            status_command,
        ),

        (
            "subscribe",
            subscribe_command,
        ),

        (
            "unsubscribe",
            unsubscribe_command,
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
        f"TradeMind "
        f"{STRATEGY_VERSION} started"
    )

    print(
        "Coins:",
        ", ".join(
            COINS.keys()
        ),
    )

    application.run_polling()


if __name__ == "__main__":
    main()