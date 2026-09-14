import asyncio
import io
import json
import os
import struct
import zlib
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

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

from market import get_market_data
from strategy import (
    analyze,
    get_major_liquidity,
    detect_fresh_sweep,
    STRATEGY_VERSION,
)
import bingx


TOKEN = os.getenv("BOT_TOKEN")
CHECK_INTERVAL = 15
SCAN_WORKERS = 9

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
PENDING_FILE = "bingx_pending.json"


def load_json(filename, default):
    try:
        if not os.path.exists(filename):
            return default
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"JSON LOAD ERROR {filename}: {exc}")
        return default


def save_json(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"JSON SAVE ERROR {filename}: {exc}")


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
        "active_rr": None,
        "active_tp_reason": None,
        "active_sweep_extreme": None,
        "active_score": None,
        "active_stage": None,
        "last_alert": None,
        "daily_date": None,
        "daily_trades": 0,
        "daily_stop": False,
        "last_signal_key": None,
        "coins": {},
    }


def load_state():
    state = load_json(STATE_FILE, default_state())
    if not isinstance(state, dict):
        state = default_state()
    state.setdefault("coins", {})
    return state


def save_state(state):
    save_json(STATE_FILE, state)


def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Рынок", callback_data="market"),
            InlineKeyboardButton("💧 Уровни", callback_data="levels"),
        ],
        [InlineKeyboardButton("🔎 Поиск сетапа", callback_data="search")],
        [InlineKeyboardButton("📈 График", callback_data="chart")],
        [
            InlineKeyboardButton("🔔 Включить", callback_data="subscribe"),
            InlineKeyboardButton("🔕 Выключить", callback_data="unsubscribe"),
        ],
        [
            InlineKeyboardButton("📈 SOL", callback_data="sol"),
            InlineKeyboardButton("📊 Статус", callback_data="status"),
        ],
        [InlineKeyboardButton("🟠 BingX", callback_data="bingx")],
        [InlineKeyboardButton("📒 Журнал", callback_data="journal")],
    ])


def chart_keyboard():
    rows = []
    coins = list(COINS.keys())
    icons = {
        "BTC": "₿", "ETH": "Ξ", "SOL": "◎", "BNB": "🟡",
        "XRP": "💠", "HYPE": "🔥", "DOGE": "🐶", "LINK": "🔗", "SUI": "💧",
    }
    for i in range(0, len(coins), 3):
        rows.append([
            InlineKeyboardButton(
                f"{icons[c]} {c}", callback_data=f"chart_{c}"
            )
            for c in coins[i:i + 3]
        ])
    rows.append([InlineKeyboardButton("⬅️ Главное меню", callback_data="start")])
    return InlineKeyboardMarkup(rows)


def back_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Главное меню", callback_data="start")]
    ])


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
        return f"1:{float(rr):.2f}"
    except Exception:
        return "N/A"


def format_levels(levels, price):
    if not levels:
        return "💧 Крупные уровни не найдены."

    try:
        current = float(price)
    except Exception:
        current = 0

    lines = []
    for level in levels:
        try:
            lp = float(level.get("price"))
        except Exception:
            continue

        kind = level.get("type", "")
        distance = abs(lp - current) / current * 100 if current else 0
        icon = "🔴" if "HIGH" in kind else "🟢"

        lines.append(
            f"{icon} {kind}: {format_price(lp)} ({distance:.2f}%)"
        )

    return "\n".join(lines) if lines else "💧 Крупные уровни не найдены."


def stage_icon(stage):
    return {
        "READY": "🟢",
        "CONFIRMED": "🟡",
        "15M_CONFIRMED": "🟡",
        "SWEPT": "🟠",
        "WAIT": "⏳",
    }.get(stage, "⚪")


def stage_text(stage):
    return {
        "READY": "МОЖНО ВХОДИТЬ",
        "CONFIRMED": "15M подтверждение",
        "15M_CONFIRMED": "15M подтверждение",
        "SWEPT": "Sweep обнаружен",
        "WAIT": "Ожидание",
    }.get(stage, "Ожидание")


def build_analysis(symbol):
    """Единая точка анализа TradeMind 5.2."""
    data = get_market_data(symbol)
    if not data:
        raise Exception(f"Нет данных для {symbol}")

    price = data["price"]
    candles_1h = data["candles_1h"]
    candles_15m = data["candles_15m"]
    candles_5m = data["candles_5m"]

    # Major Liquidity рассчитывается только strategy.py по 1H.
    major_levels = get_major_liquidity(
        candles_1h=candles_1h,
        current_price=price,
    )

    # Только свежий sweep Major Liquidity.
    sweep = detect_fresh_sweep(
        candles_1h=candles_1h,
        candles_5m=candles_5m,
        current_price=price,
    )

    result = analyze(
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,
        price=price,
        major_levels=major_levels,
        sweep=sweep,
    )

    result.update({
        "symbol": symbol,
        "price": price,
        "major_levels": major_levels,
        "sweep": sweep,
        "candles_5m": candles_5m,
        "strategy_version": STRATEGY_VERSION,
    })

    return result

def scan_one_coin(coin, symbol):
    try:
        return coin, build_analysis(symbol)
    except Exception as exc:
        print(f"{coin} ERROR: {exc}")
        return coin, {"error": str(exc), "symbol": symbol}


def scan_all_coins():
    results = {}
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as executor:
        futures = {
            executor.submit(scan_one_coin, coin, symbol): coin
            for coin, symbol in COINS.items()
        }

        for future in as_completed(futures):
            coin = futures[future]
            try:
                c, result = future.result()
                results[c] = result
            except Exception as exc:
                results[coin] = {
                    "error": str(exc),
                    "symbol": COINS[coin],
                }

    return {
        coin: results[coin]
        for coin in COINS
        if coin in results
    }


async def scan_all_coins_async():
    return await asyncio.to_thread(scan_all_coins)


def find_first_ready(results):
    ready = []
    for coin, result in results.items():
        if result.get("error"):
            continue
        if result.get("stage") == "READY" and result.get("score", 0) >= 80:
            ready.append((result.get("score", 0), coin, result))

    if not ready:
        return None

    ready.sort(key=lambda item: item[0], reverse=True)
    return ready[0]


def build_market_message(results):
    lines = ["📊 <b>TRADEMIND — РЫНОК</b>", ""]

    for coin in COINS:
        result = results.get(coin)
        if not result:
            continue

        if result.get("error"):
            lines += [f"❌ <b>{coin}</b>", "Ошибка данных", "", "────────────", ""]
            continue

        price = result.get("price")
        stage = result.get("stage", "WAIT")
        score = result.get("score", 0)

        lines += [
            f"💠 <b>{coin}</b> {format_price(price)}",
            f"{stage_icon(stage)} {stage_text(stage)}",
            f"Score: <b>{score}/100</b>",
            "💧 <b>Крупная ликвидность:</b>",
            format_levels(result.get("major_levels", []), price),
            "",
            "────────────",
            "",
        ]

    lines += [
        "1H → Major Liquidity → Sweep → 15M → 5M",
        "❌ В середине движения не входим.",
    ]
    return "\n".join(lines)


def build_levels_message(results):
    lines = [
        "💧 <b>TRADEMIND — КЛЮЧЕВЫЕ УРОВНИ</b>",
        "",
        "Используем только крупную ликвидность 1H.",
        "",
    ]

    for coin in COINS:
        result = results.get(coin)
        if not result or result.get("error"):
            continue

        price = result.get("price")
        lines += [
            f"💠 <b>{coin}</b> {format_price(price)}",
            format_levels(result.get("major_levels", []), price),
            "",
            "────────────",
            "",
        ]

    return "\n".join(lines)


def build_search_message(results):
    ready = find_first_ready(results)

    if ready:
        score, coin, result = ready
        return "\n".join([
            "🟢 <b>НАЙДЕН СЕТАП</b>",
            "",
            f"Монета: <b>{coin}</b>",
            f"Направление: <b>{result.get('direction')}</b>",
            f"Score: <b>{score}/100</b>",
            "",
            f"Entry: <b>{format_price(result.get('entry'))}</b>",
            f"SL: <b>{format_price(result.get('sl'))}</b>",
            f"TP: <b>{format_price(result.get('tp'))}</b>",
            f"RR: <b>{format_rr(result.get('rr'))}</b>",
            "",
            "🔥 Полное подтверждение получено.",
        ])

    lines = [
        "🔎 <b>ПОИСК СЕТАПА</b>",
        "",
        "❌ Готового входа сейчас нет.",
        "",
    ]

    for coin in COINS:
        result = results.get(coin)
        if not result:
            continue
        if result.get("error"):
            lines.append(f"❌ {coin}: ошибка")
            continue
        lines.append(
            f"{stage_icon(result.get('stage', 'WAIT'))} "
            f"{coin}: {stage_text(result.get('stage', 'WAIT'))} — "
            f"{result.get('score', 0)}/100"
        )

    lines += [
        "",
        "Ждём → Sweep → 15M → 5M.",
        "❌ В середине движения не входим.",
    ]
    return "\n".join(lines)


def build_sol_message(result):
    if result.get("error"):
        return "❌ <b>SOL</b>\n\nОшибка получения данных."

    stage = result.get("stage", "WAIT")
    lines = [
        "📈 <b>TRADEMIND — SOL</b>",
        "",
        f"💰 Цена: <b>{format_price(result.get('price'))}</b>",
        f"{stage_icon(stage)} {stage_text(stage)}",
        f"Score: <b>{result.get('score', 0)}/100</b>",
    ]

    if result.get("direction"):
        lines.append(
            f"Направление: <b>{result.get('direction')}</b>"
        )

    lines += [
        "",
        "💧 <b>КРУПНАЯ ЛИКВИДНОСТЬ:</b>",
        format_levels(
            result.get("major_levels", []),
            result.get("price"),
        ),
    ]

    if result.get("entry") is not None:
        lines += [
            "",
            "🎯 <b>СЕТАП</b>",
            f"Entry: <b>{format_price(result.get('entry'))}</b>",
            f"SL: <b>{format_price(result.get('sl'))}</b>",
            f"TP: <b>{format_price(result.get('tp'))}</b>",
            f"RR: <b>{format_rr(result.get('rr'))}</b>",
        ]

    if result.get("reason"):
        lines += ["", "Причина:", str(result["reason"])]

    return "\n".join(lines)


# ---------- PNG chart ----------

def png_chunk(kind, data):
    return (
        struct.pack(">I", len(data))
        + kind + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    )


def make_png(width, height, pixels):
    raw = bytearray()
    for row in pixels:
        raw.append(0)
        raw.extend(row)

    png = bytearray(b"\x89PNG\r\n\x1a\n")
    png.extend(png_chunk(
        b"IHDR",
        struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ))
    png.extend(png_chunk(b"IDAT", zlib.compress(bytes(raw), 6)))
    png.extend(png_chunk(b"IEND", b""))
    return bytes(png)


def new_canvas(width, height, color):
    row = bytearray(color * width)
    return [bytearray(row) for _ in range(height)]


def set_pixel(pixels, x, y, color):
    if not pixels or not pixels[0]:
        return
    height = len(pixels)
    width = len(pixels[0]) // 3

    if 0 <= x < width and 0 <= y < height:
        index = x * 3
        pixels[y][index:index + 3] = bytes(color)


def draw_line(pixels, x1, y1, x2, y2, color, thickness=1):
    dx, dy = x2 - x1, y2 - y1
    steps = max(abs(dx), abs(dy), 1)

    for i in range(steps + 1):
        x = int(x1 + dx * i / steps)
        y = int(y1 + dy * i / steps)
        radius = max(0, thickness // 2)

        for xx in range(x - radius, x + radius + 1):
            for yy in range(y - radius, y + radius + 1):
                set_pixel(pixels, xx, yy, color)


def draw_rect(pixels, x1, y1, x2, y2, color):
    if x1 > x2:
        x1, x2 = x2, x1
    if y1 > y2:
        y1, y2 = y2, y1

    height = len(pixels)
    width = len(pixels[0]) // 3

    for y in range(max(0, y1), min(height, y2 + 1)):
        for x in range(max(0, x1), min(width, x2 + 1)):
            set_pixel(pixels, x, y, color)


def candle_value(candle, key):
    value = candle.get(key)
    aliases = {
        "open": ["o"],
        "high": ["h"],
        "low": ["l"],
        "close": ["c"],
        "open_time": ["time", "timestamp"],
    }

    if value is None:
        for alias in aliases.get(key, []):
            if alias in candle:
                value = candle[alias]
                break

    try:
        return float(value)
    except Exception:
        return None


def render_chart_png(coin, result):
    candles = result.get("candles_5m", [])
    levels = result.get("major_levels", [])
    price = result.get("price")
    entry = result.get("entry")
    sl = result.get("sl")
    tp = result.get("tp")

    valid = []
    for candle in candles:
        values = {
            k: candle_value(candle, k)
            for k in ("open", "high", "low", "close")
        }
        if all(v is not None for v in values.values()):
            valid.append(values)

    valid = valid[-80:]
    if not valid:
        raise Exception("Нет 5M свечей для графика.")

    width, height = 1200, 700
    left, right, top, bottom = 60, 60, 40, 40
    chart_width = width - left - right
    chart_height = height - top - bottom

    background = (14, 18, 24)
    grid = (45, 52, 62)
    bullish = (50, 210, 130)
    bearish = (235, 80, 90)
    blue = (80, 170, 255)
    high_color = (240, 80, 90)
    low_color = (50, 210, 130)
    entry_color = (255, 215, 70)
    tp_color = (80, 220, 150)

    pixels = new_canvas(width, height, background)

    values = []
    for candle in valid:
        values += [candle["high"], candle["low"]]

    for level in levels:
        try:
            values.append(float(level["price"]))
        except Exception:
            pass

    for value in (price, entry, sl, tp):
        try:
            if value is not None:
                values.append(float(value))
        except Exception:
            pass

    lo, hi = min(values), max(values)
    if hi == lo:
        hi += 1
        lo -= 1

    pad = (hi - lo) * 0.08
    hi += pad
    lo -= pad

    def y_of(value):
        ratio = (hi - value) / (hi - lo)
        return int(top + ratio * chart_height)

    for i in range(1, 8):
        y = top + int(chart_height * i / 8)
        draw_line(pixels, left, y, width - right, y, grid)

    for level in levels:
        try:
            lp = float(level["price"])
        except Exception:
            continue
        color = high_color if "HIGH" in level.get("type", "") else low_color
        y = y_of(lp)
        draw_line(pixels, left, y, width - right, y, color, 2)

    count = len(valid)
    space = chart_width / count
    candle_width = max(3, int(space * 0.55))

    for i, candle in enumerate(valid):
        x = int(left + (i + 0.5) * space)
        oy, hy, ly, cy = (
            y_of(candle["open"]),
            y_of(candle["high"]),
            y_of(candle["low"]),
            y_of(candle["close"]),
        )
        color = bullish if candle["close"] >= candle["open"] else bearish

        draw_line(pixels, x, hy, x, ly, color, 1)
        top_y, bottom_y = min(oy, cy), max(oy, cy)
        if top_y == bottom_y:
            bottom_y += 2

        draw_rect(
            pixels,
            x - candle_width // 2,
            top_y,
            x + candle_width // 2,
            bottom_y,
            color,
        )

    if price is not None:
        try:
            y = y_of(float(price))
            draw_line(pixels, left, y, width - right, y, blue, 2)
        except Exception:
            pass

    for value, color in (
        (entry, entry_color),
        (sl, high_color),
        (tp, tp_color),
    ):
        if value is None:
            continue
        try:
            y = y_of(float(value))
            draw_line(pixels, left, y, width - right, y, color, 3)
        except Exception:
            pass

    return io.BytesIO(make_png(width, height, pixels))


def build_chart_caption(coin, result):
    stage = result.get("stage", "WAIT")
    lines = [
        f"📈 <b>TRADEMIND — {coin}</b>",
        "",
        f"💰 Цена: <b>{format_price(result.get('price'))}</b>",
        f"{stage_icon(stage)} {stage_text(stage)}",
        f"⭐ Score: <b>{result.get('score', 0)}/100</b>",
        "",
        "💧 <b>MAJOR LIQUIDITY</b>",
        format_levels(
            result.get("major_levels", []),
            result.get("price"),
        ),
    ]

    if result.get("direction"):
        lines += [
            "",
            f"📐 Направление: <b>{result['direction']}</b>",
        ]

    if stage == "READY":
        lines += [
            "",
            f"Entry: <b>{format_price(result.get('entry'))}</b>",
            f"SL: <b>{format_price(result.get('sl'))}</b>",
            f"TP: <b>{format_price(result.get('tp'))}</b>",
            f"RR: <b>{format_rr(result.get('rr'))}</b>",
            "",
            "🟢 <b>МОЖНО ВХОДИТЬ</b>",
        ]
    elif stage in ("CONFIRMED", "15M_CONFIRMED"):
        lines += [
            "",
            "✅ Sweep",
            "✅ 15M confirmation",
            "⏳ ЖДЁМ 5M trigger",
            "❌ Вход запрещён",
        ]
    elif stage == "SWEPT":
        lines += [
            "",
            "💧 Sweep обнаружен",
            "⏳ Ждём 15M confirmation",
            "❌ Вход запрещён",
        ]
    else:
        lines += [
            "",
            "⏳ Ждём Major Liquidity → Sweep",
            "❌ В середине движения не входим.",
        ]

    return "\n".join(lines)


async def send_chart(message, coin):
    try:
        result = await asyncio.to_thread(build_analysis, COINS[coin])
        image = render_chart_png(coin, result)
        image.seek(0)

        await message.reply_photo(
            photo=InputFile(image, filename=f"{coin.lower()}_5m.png"),
            caption=build_chart_caption(coin, result),
            parse_mode="HTML",
            reply_markup=chart_keyboard(),
        )
    except Exception as exc:
        await message.reply_text(
            f"❌ Ошибка графика {coin}: {exc}",
            reply_markup=back_keyboard(),
        )


# ---------- BingX ----------

def build_bingx_message():
    cfg = bingx.config_status()

    mode_text = {
        "OFF": "🔴 OFF — реальные сделки отключены",
        "PAPER": "🟡 PAPER — только симуляция",
        "CONFIRM": "🟠 CONFIRM — нужен /execute",
        "AUTO": "🟢 AUTO — реальные сделки разрешены",
    }.get(cfg["mode"], cfg["mode"])

    configured = "✅ API ключи есть" if cfg["configured"] else "❌ API ключи не заданы"

    return "\n".join([
        "🟠 <b>TRADEMIND — BINGX</b>",
        "",
        f"Режим: <b>{mode_text}</b>",
        configured,
        f"Leverage: <b>{cfg['leverage']}x</b>",
        f"Risk: <b>${cfg['risk_usdt']:.2f}</b>",
        "",
        "Команды:",
        "/bingx — статус",
        "/balance — баланс Futures",
        "/position — открытые позиции",
        "/execute — исполнить pending setup в CONFIRM",
        "/close CONFIRM — закрыть позиции",
        "",
        "⚠️ Для AUTO сначала проверь PAPER/CONFIRM.",
    ])


def make_setup(coin, result):
    return {
        "symbol": result["symbol"],
        "direction": result["direction"],
        "entry": result["entry"],
        "sl": result["sl"],
        "tp": result["tp"],
        "rr": result.get("rr"),
        "score": result.get("score"),
        "coin": coin,
        "tp_reason": result.get("tp_reason"),
        "sweep_extreme": result.get("sweep_extreme"),
        "risk_usdt": bingx.RISK_USDT,
        "created_at": datetime.utcnow().isoformat(),
    }


def load_pending():
    return load_json(PENDING_FILE, None)


def save_pending(data):
    save_json(PENDING_FILE, data)


async def bingx_command(update, context):
    await update.message.reply_text(
        build_bingx_message(),
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


async def balance_command(update, context):
    try:
        data = await asyncio.to_thread(bingx.get_balance)
        if not data:
            text = "❌ Баланс не получен."
        else:
            text = "\n".join([
                "💰 <b>BingX Futures</b>",
                "",
                f"Balance: <b>{data.get('balance', 'N/A')}</b> USDT",
                f"Equity: <b>{data.get('equity', 'N/A')}</b> USDT",
                f"Available: <b>{data.get('availableMargin', 'N/A')}</b> USDT",
                f"UPL: <b>{data.get('unrealizedProfit', 'N/A')}</b> USDT",
            ])
        await update.message.reply_text(
            text, parse_mode="HTML", reply_markup=back_keyboard()
        )
    except Exception as exc:
        await update.message.reply_text(
            f"❌ BingX balance error:\n{exc}",
            reply_markup=back_keyboard(),
        )


async def position_command(update, context):
    try:
        positions = await asyncio.to_thread(bingx.get_positions)
        if not positions:
            text = "📭 <b>BingX</b>\n\nОткрытых позиций нет."
        else:
            lines = ["📌 <b>BingX — ПОЗИЦИИ</b>", ""]
            for p in positions:
                try:
                    amount = abs(float(p.get("positionAmt", 0)))
                except Exception:
                    amount = 0
                if amount <= 0:
                    continue

                lines += [
                    f"💠 <b>{p.get('symbol')}</b>",
                    f"Side: <b>{p.get('positionSide')}</b>",
                    f"Qty: <b>{p.get('positionAmt')}</b>",
                    f"Entry: <b>{p.get('avgPrice')}</b>",
                    f"PnL: <b>{p.get('unrealizedProfit')}</b>",
                    f"Liquidation: <b>{p.get('liquidationPrice')}</b>",
                    "",
                ]
            text = "\n".join(lines)
        await update.message.reply_text(
            text, parse_mode="HTML", reply_markup=back_keyboard()
        )
    except Exception as exc:
        await update.message.reply_text(
            f"❌ BingX position error:\n{exc}",
            reply_markup=back_keyboard(),
        )


async def execute_command(update, context):
    if bingx.mode() != "CONFIRM":
        await update.message.reply_text(
            "❌ /execute работает только при BINGX_MODE=CONFIRM."
        )
        return

    pending = load_pending()
    if not pending:
        await update.message.reply_text("📭 Pending setup отсутствует.")
        return

    try:
        data = await asyncio.to_thread(
            bingx.execute_confirmed, pending
        )

        # Do not destroy pending state before a successful API response.
        save_pending(None)

        state = load_state()
        state.update({
            "active_coin": pending.get("coin"),
            "active_symbol": pending.get("symbol"),
            "active_direction": pending.get("direction"),
            "active_setup_key": f"{pending.get('coin')}_{pending.get('direction')}_{pending.get('entry')}_{pending.get('sl')}_{pending.get('tp')}",
            "active_entry": pending.get("entry"),
            "active_sl": pending.get("sl"),
            "active_tp": pending.get("tp"),
            "active_rr": pending.get("rr"),
            "active_tp_reason": pending.get("tp_reason"),
            "active_sweep_extreme": pending.get("sweep_extreme"),
            "active_score": pending.get("score"),
            "active_stage": "READY",
            "last_alert": datetime.utcnow().isoformat(),
        })
        state["daily_trades"] = int(state.get("daily_trades", 0)) + 1
        save_state(state)

        await update.message.reply_text(
            "🟢 <b>BingX order отправлен.</b>\n\n"
            f"Coin: <b>{pending.get('coin')}</b>\n"
            f"Direction: <b>{pending.get('direction')}</b>\n"
            f"Entry plan: <b>{format_price(pending.get('entry'))}</b>\n"
            f"SL: <b>{format_price(pending.get('sl'))}</b>\n"
            f"TP: <b>{format_price(pending.get('tp'))}</b>\n\n"
            f"API: <code>{str(data)[:1200]}</code>",
            parse_mode="HTML",
        )
    except Exception as exc:
        await update.message.reply_text(
            f"❌ BingX execute error:\n{exc}"
        )


async def close_command(update, context):
    confirm = (
        bool(context.args)
        and context.args[0].upper() == "CONFIRM"
    )

    if not confirm:
        await update.message.reply_text(
            "⚠️ Команда закрывает позиции рыночным ордером.\n"
            "Для подтверждения: /close CONFIRM"
        )
        return

    try:
        symbol = context.args[1] if len(context.args) > 1 else None
        data = await asyncio.to_thread(
            bingx.close_position, symbol
        )
        await update.message.reply_text(
            f"🟢 BingX close отправлен.\n<code>{data}</code>",
            parse_mode="HTML",
        )
    except Exception as exc:
        await update.message.reply_text(
            f"❌ BingX close error:\n{exc}"
        )


# ---------- Telegram commands ----------

async def start(update, context):
    await update.message.reply_text(
        "🤖 <b>TRADEMIND 4.0</b>\n\n"
        "Мониторинг 9 монет:\n"
        "BTC • ETH • SOL • BNB • XRP\n"
        "HYPE • DOGE • LINK • SUI\n\n"
        f"Сканирование: <b>каждые {CHECK_INTERVAL} секунд</b>\n\n"
        "Стратегия:\n"
        "1H → Major Liquidity → Sweep → 15M → 5M\n\n"
        "BingX: <b>AUTO выключен по умолчанию</b>.",
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


async def market_command(update, context):
    results = await scan_all_coins_async()
    await update.message.reply_text(
        build_market_message(results),
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


async def levels_command(update, context):
    results = await scan_all_coins_async()
    await update.message.reply_text(
        build_levels_message(results),
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


async def search_command(update, context):
    results = await scan_all_coins_async()
    await update.message.reply_text(
        build_search_message(results),
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


async def sol_command(update, context):
    try:
        result = await asyncio.to_thread(build_analysis, "SOLUSDT")
        await update.message.reply_text(
            build_sol_message(result),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
    except Exception as exc:
        await update.message.reply_text(
            f"❌ Ошибка SOL:\n{exc}",
            reply_markup=back_keyboard(),
        )


async def chart_command(update, context):
    coin = "SOL"
    if context.args and context.args[0].upper() in COINS:
        coin = context.args[0].upper()
    await send_chart(update.message, coin)


async def subscribe_command(update, context):
    subscribers = load_subscribers()
    chat_id = update.effective_chat.id

    if chat_id not in subscribers:
        subscribers.append(chat_id)
        save_subscribers(subscribers)
        text = "🔔 <b>Уведомления включены.</b>"
    else:
        text = "🔔 Уведомления уже включены."

    await update.message.reply_text(
        text, parse_mode="HTML", reply_markup=back_keyboard()
    )


async def unsubscribe_command(update, context):
    subscribers = load_subscribers()
    chat_id = update.effective_chat.id

    if chat_id in subscribers:
        subscribers.remove(chat_id)
        save_subscribers(subscribers)
        text = "🔕 <b>Уведомления выключены.</b>"
    else:
        text = "🔕 Уведомления уже выключены."

    await update.message.reply_text(
        text, parse_mode="HTML", reply_markup=back_keyboard()
    )


async def status_command(update, context):
    state = load_state()

    if state.get("active_coin"):
        text = (
            "📊 <b>TRADEMIND — СТАТУС</b>\n\n"
            f"🟢 Активно: <b>{state.get('active_coin')}</b>\n"
            f"Направление: <b>{state.get('active_direction')}</b>\n"
            f"Stage: <b>{state.get('active_stage')}</b>\n"
            f"Score: <b>{state.get('active_score')}/100</b>\n\n"
            f"Entry: <b>{format_price(state.get('active_entry'))}</b>\n"
            f"SL: <b>{format_price(state.get('active_sl'))}</b>\n"
            f"TP: <b>{format_price(state.get('active_tp'))}</b>\n"
            f"RR: <b>{format_rr(state.get('active_rr'))}</b>\n\n"
            f"BingX mode: <b>{bingx.mode()}</b>"
        )
    else:
        text = (
            "📊 <b>TRADEMIND — СТАТУС</b>\n\n"
            "🟢 Активного сетапа нет.\n"
            f"Сканирование: <b>{CHECK_INTERVAL} сек.</b>\n"
            f"BingX: <b>{bingx.mode()}</b>"
        )

    await update.message.reply_text(
        text, parse_mode="HTML", reply_markup=back_keyboard()
    )


async def journal_command(update, context):
    await update.message.reply_text(
        "📒 <b>TRADEMIND — ЖУРНАЛ</b>\n\n"
        "TradeMind 4.1: журнал сигналов и сделок подключён в состоянии monitor. "
        "Следующий этап — автоматический PnL/R по фактическим fills.",
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


async def broadcast(application, text):
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
            print(f"BROADCAST ERROR {chat_id}: {exc}")

    await asyncio.gather(
        *(send_one(chat_id) for chat_id in subscribers),
        return_exceptions=True,
    )


# ---------- Monitor ----------

async def monitor(application):
    print("TradeMind 4.1 monitor started.")
    print(f"Scan interval: {CHECK_INTERVAL}s")
    print(f"BingX mode: {bingx.mode()}")

    while True:
        started = asyncio.get_running_loop().time()

        try:
            state = load_state()
            today = datetime.utcnow().date().isoformat()

            if state.get("daily_date") != today:
                state["daily_date"] = today
                state["daily_trades"] = 0
                state["daily_stop"] = False
                state["active_coin"] = None
                state["active_setup_key"] = None

            if state.get("daily_trades", 0) >= 2:
                state["daily_stop"] = True

            results = await scan_all_coins_async()
            active_coin = state.get("active_coin")

            if not active_coin and not state.get("daily_stop"):
                ready = find_first_ready(results)

                if ready:
                    score, coin, result = ready
                    setup = make_setup(coin, result)

                    setup_key = (
                        f"{coin}_{setup['direction']}_"
                        f"{setup['entry']}_{setup['sl']}_{setup['tp']}"
                    )

                    if state.get("active_setup_key") != setup_key:
                        execution = await asyncio.to_thread(
                            bingx.open_trade, setup
                        )

                        status = execution.get("status")

                        mode = bingx.mode()

                        # OFF = signal only. It must NEVER become an active trade.
                        if mode == "OFF":
                            state["last_signal_key"] = setup_key
                            save_state(state)

                        elif status in {"simulated", "submitted"}:
                            state.update({
                                "active_coin": coin,
                                "active_symbol": result.get("symbol"),
                                "active_direction": result.get("direction"),
                                "active_setup_key": setup_key,
                                "active_entry": result.get("entry"),
                                "active_sl": result.get("sl"),
                                "active_tp": result.get("tp"),
                                "active_rr": result.get("rr"),
                                "active_tp_reason": result.get("tp_reason"),
                                "active_sweep_extreme": result.get("sweep_extreme"),
                                "active_score": score,
                                "active_stage": "READY",
                                "last_alert": datetime.utcnow().isoformat(),
                            })
                            state["daily_trades"] += 1
                            save_state(state)

                        elif mode == "CONFIRM" and status == "pending_confirmation":
                            save_pending(setup)
                            state["last_signal_key"] = setup_key
                            save_state(state)

                            mode_line = {
                            "OFF": "⚪ OFF — только сигнал, сделка НЕ открыта",
                            "PAPER": "🟡 PAPER — виртуальная сделка",
                            "CONFIRM": "🟠 CONFIRM — setup сохранён, используй /execute",
                            "AUTO": "🟢 AUTO — ордер отправлен",
                        }.get(mode, mode)

                        text = (
                            "🚨 <b>TRADEMIND 4.1 — СЕТАП</b>\n\n"
                            f"💠 Монета: <b>{coin}</b>\n"
                            f"📐 Направление: <b>{setup['direction']}</b>\n"
                            f"⭐ Score: <b>{score}/100</b>\n\n"
                            f"Entry: <b>{format_price(setup['entry'])}</b>\n"
                            f"SL: <b>{format_price(setup['sl'])}</b>\n"
                            f"TP: <b>{format_price(setup['tp'])}</b>\n"
                            f"RR: <b>{format_rr(setup['rr'])}</b>\n\n"
                            f"{mode_line}"
                        )

                        if setup.get("tp_reason"):
                            text += f"\n🎯 {setup['tp_reason']}"

                        if result.get("liquidity_warning"):
                            text += f"\n⚠️ {result['liquidity_warning']}"

                        await broadcast(application, text)

            # Early-stage alerts remain independent from READY.
            for coin, result in results.items():
                if result.get("error"):
                    continue

                direction = result.get("direction")
                stage = result.get("stage")
                sweep = result.get("sweep")

                coin_state = state["coins"].setdefault(coin, {})

                if sweep:
                    sweep_price = (
                        sweep.get("price")
                        or sweep.get("level")
                    )
                    sweep_time = sweep.get("open_time")
                    sweep_key = (
                        f"{coin}_{direction}_{sweep_price}_{sweep_time}"
                    )

                    if coin_state.get("last_sweep") != sweep_key:
                        coin_state["last_sweep"] = sweep_key
                        await broadcast(
                            application,
                            "🔎 <b>TRADEMIND — SWEEP</b>\n\n"
                            f"💠 {coin}\n"
                            f"📐 {direction}\n\n"
                            "💧 Крупная ликвидность снята.\n"
                            "⏳ Ждём 15M confirmation.\n"
                            "❌ Вход пока запрещён."
                        )

                if stage in {"CONFIRMED", "15M_CONFIRMED"}:
                    confirmation_time = (
                        result.get("confirmation_15m_time")
                        or result.get("confirmation_time")
                    )
                    key = f"{coin}_{direction}_{confirmation_time}"

                    if coin_state.get("last_confirmation") != key:
                        coin_state["last_confirmation"] = key
                        await broadcast(
                            application,
                            "🟡 <b>TRADEMIND — 15M CONFIRMATION</b>\n\n"
                            f"💠 {coin}\n"
                            f"📐 {direction}\n\n"
                            "✅ Sweep\n"
                            "✅ 15M confirmation\n"
                            "⏳ Ждём 5M trigger.\n"
                            "❌ Вход пока запрещён."
                        )

            save_state(state)

        except Exception as exc:
            print(f"MONITOR ERROR: {exc}")

        elapsed = asyncio.get_running_loop().time() - started
        await asyncio.sleep(max(1, CHECK_INTERVAL - elapsed))


# ---------- callbacks ----------

async def callbacks(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "start":
        await query.edit_message_text(
            "🤖 <b>TRADEMIND 4.0</b>\n\n"
            "9 монет • сканирование 15 секунд\n\n"
            "1H → Major Liquidity → Sweep → 15M → 5M",
            parse_mode="HTML",
            reply_markup=main_keyboard(),
        )
        return

    if data == "market":
        results = await scan_all_coins_async()
        await query.edit_message_text(
            build_market_message(results),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        return

    if data == "levels":
        results = await scan_all_coins_async()
        await query.edit_message_text(
            build_levels_message(results),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        return

    if data == "search":
        results = await scan_all_coins_async()
        await query.edit_message_text(
            build_search_message(results),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        return

    if data == "chart":
        await query.edit_message_text(
            "📈 <b>TRADEMIND — ГРАФИК</b>\n\nВыбери монету:",
            parse_mode="HTML",
            reply_markup=chart_keyboard(),
        )
        return

    if data.startswith("chart_"):
        coin = data.split("_", 1)[1]
        if coin not in COINS:
            await query.answer("Неизвестная монета", show_alert=True)
            return
        await send_chart(query.message, coin)
        return

    if data == "sol":
        try:
            result = await asyncio.to_thread(
                build_analysis, "SOLUSDT"
            )
            await query.edit_message_text(
                build_sol_message(result),
                parse_mode="HTML",
                reply_markup=back_keyboard(),
            )
        except Exception as exc:
            await query.edit_message_text(
                f"❌ Ошибка SOL:\n{exc}",
                reply_markup=back_keyboard(),
            )
        return

    if data == "status":
        state = load_state()
        text = (
            "📊 <b>СТАТУС</b>\n\n"
            f"Active: <b>{state.get('active_coin') or 'нет'}</b>\n"
            f"Direction: <b>{state.get('active_direction') or '—'}</b>\n"
            f"Score: <b>{state.get('active_score') or '—'}</b>\n"
            f"BingX: <b>{bingx.mode()}</b>\n"
            f"Daily trades: <b>{state.get('daily_trades', 0)}/2</b>"
        )
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=back_keyboard()
        )
        return

    if data == "bingx":
        await query.edit_message_text(
            build_bingx_message(),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        return

    if data == "subscribe":
        subscribers = load_subscribers()
        chat_id = query.message.chat_id
        if chat_id not in subscribers:
            subscribers.append(chat_id)
            save_subscribers(subscribers)
        await query.edit_message_text(
            "🔔 <b>Уведомления включены.</b>",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        return

    if data == "unsubscribe":
        subscribers = load_subscribers()
        chat_id = query.message.chat_id
        if chat_id in subscribers:
            subscribers.remove(chat_id)
            save_subscribers(subscribers)
        await query.edit_message_text(
            "🔕 <b>Уведомления выключены.</b>",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        return

    if data == "journal":
        await query.edit_message_text(
            "📒 <b>ЖУРНАЛ</b>\n\n"
            "Фактический BingX PnL-журнал подключим следующим этапом.",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )


async def set_commands(application):
    commands = [
        BotCommand("start", "Главное меню"),
        BotCommand("market", "Рынок 9 монет"),
        BotCommand("levels", "Крупные уровни"),
        BotCommand("search", "Поиск сетапа"),
        BotCommand("chart", "График"),
        BotCommand("status", "Статус"),
        BotCommand("bingx", "BingX статус"),
        BotCommand("balance", "BingX баланс"),
        BotCommand("position", "BingX позиции"),
        BotCommand("execute", "Исполнить pending"),
        BotCommand("close", "Закрыть позиции"),
        BotCommand("subscribe", "Включить уведомления"),
        BotCommand("unsubscribe", "Выключить уведомления"),
        BotCommand("sol", "Анализ SOL"),
        BotCommand("journal", "Журнал"),
    ]
    await application.bot.set_my_commands(commands)


async def post_init(application):
    await set_commands(application)
    application.create_task(
        monitor(application),
        name="trademind_monitor",
    )


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN не найден.")

    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    handlers = [
        ("start", start),
        ("market", market_command),
        ("levels", levels_command),
        ("search", search_command),
        ("chart", chart_command),
        ("status", status_command),
        ("bingx", bingx_command),
        ("balance", balance_command),
        ("position", position_command),
        ("execute", execute_command),
        ("close", close_command),
        ("subscribe", subscribe_command),
        ("unsubscribe", unsubscribe_command),
        ("sol", sol_command),
        ("journal", journal_command),
    ]

    for command, handler in handlers:
        application.add_handler(
            CommandHandler(command, handler)
        )

    application.add_handler(
        CallbackQueryHandler(callbacks)
    )

    print("TradeMind 4.1 started.")
    print(f"Scan interval: {CHECK_INTERVAL}s")
    print(f"Parallel workers: {SCAN_WORKERS}")
    print("Coins:", ", ".join(COINS.keys()))
    print("BingX mode:", bingx.mode())

    application.run_polling()


if __name__ == "__main__":
    main()
