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

from market import (
    get_market_data,
    find_major_liquidity,
    detect_sweep,
)
from strategy import analyze
import bingx


# ============================================================
# TRADEMIND 4.1
# ============================================================

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


# ============================================================
# JSON / STATE
# ============================================================

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
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
            )

    except Exception as exc:
        print(f"JSON SAVE ERROR {filename}: {exc}")


def load_subscribers():
    return load_json(
        SUBSCRIBERS_FILE,
        [],
    )


def save_subscribers(data):
    save_json(
        SUBSCRIBERS_FILE,
        data,
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
        "last_signal_key": None,

        "daily_date": None,
        "daily_trades": 0,
        "daily_stop": False,

        "coins": {},
    }


def load_state():
    state = load_json(
        STATE_FILE,
        default_state(),
    )

    if not isinstance(state, dict):
        state = default_state()

    defaults = default_state()

    for key, value in defaults.items():
        state.setdefault(key, value)

    if not isinstance(state.get("coins"), dict):
        state["coins"] = {}

    return state


def save_state(state):
    save_json(
        STATE_FILE,
        state,
    )


def reset_daily_if_needed(state):
    today = datetime.utcnow().date().isoformat()

    if state.get("daily_date") != today:
        state["daily_date"] = today
        state["daily_trades"] = 0
        state["daily_stop"] = False

        # ВАЖНО:
        # активная позиция не должна исчезать просто
        # из-за смены даты.
        #
        # Поэтому active_coin НЕ сбрасываем.

        save_state(state)


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
                "📈 График",
                callback_data="chart",
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
                "🟠 BingX",
                callback_data="bingx",
            ),
        ],

        [
            InlineKeyboardButton(
                "📒 Журнал",
                callback_data="journal",
            ),
        ],
    ])


def chart_keyboard():
    icons = {
        "BTC": "₿",
        "ETH": "Ξ",
        "SOL": "◎",
        "BNB": "🟡",
        "XRP": "💠",
        "HYPE": "🔥",
        "DOGE": "🐶",
        "LINK": "🔗",
        "SUI": "💧",
    }

    coins = list(COINS.keys())

    rows = []

    for i in range(0, len(coins), 3):
        row = []

        for coin in coins[i:i + 3]:
            row.append(
                InlineKeyboardButton(
                    f"{icons.get(coin, '💠')} {coin}",
                    callback_data=f"chart_{coin}",
                )
            )

        rows.append(row)

    rows.append([
        InlineKeyboardButton(
            "⬅️ Главное меню",
            callback_data="start",
        )
    ])

    return InlineKeyboardMarkup(rows)


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
# FORMAT
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
        return f"1:{float(rr):.2f}"

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
        "CONFIRMED": "15M подтверждение",
        "15M_CONFIRMED": "15M подтверждение",
        "SWEPT": "Sweep обнаружен",
        "WAIT": "Ожидание",
    }.get(
        stage,
        "Ожидание",
    )


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
            level_price = float(
                level.get("price")
            )
        except Exception:
            continue

        kind = level.get(
            "type",
            "",
        )

        distance = (
            abs(level_price - current)
            / current
            * 100
            if current
            else 0
        )

        icon = (
            "🔴"
            if "HIGH" in kind
            else "🟢"
        )

        lines.append(
            f"{icon} {kind}: "
            f"{format_price(level_price)} "
            f"({distance:.2f}%)"
        )

    if not lines:
        return "💧 Крупные уровни не найдены."

    return "\n".join(lines)


# ============================================================
# MARKET ANALYSIS
# ============================================================

def build_analysis(symbol):
    data = get_market_data(symbol)

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

    major_levels = find_major_liquidity(
        candles_1h,
        price,
        max_levels=6,
    )

    sweep = detect_sweep(
        candles_5m,
        major_levels,
    )

    result = analyze(
        price=price,
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,
        major_levels=major_levels,
        sweep=sweep,
    )

    if not isinstance(result, dict):
        result = {}

    result.update({
        "symbol": symbol,
        "price": price,
        "major_levels": major_levels,
        "sweep": sweep,
        "candles_5m": candles_5m,
    })

    return result


def scan_one_coin(coin, symbol):
    try:
        return (
            coin,
            build_analysis(symbol),
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
            for coin, symbol in COINS.items()
        }

        for future in as_completed(
            futures
        ):
            coin = futures[future]

            try:
                result_coin, result = (
                    future.result()
                )

                results[result_coin] = result

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
    return await asyncio.to_thread(
        scan_all_coins
    )


def find_first_ready(results):
    ready = []

    for coin, result in results.items():
        if not isinstance(result, dict):
            continue

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


# ============================================================
# MESSAGES
# ============================================================

def build_market_message(results):
    lines = [
        "📊 <b>TRADEMIND 4.1 — РЫНОК</b>",
        "",
    ]

    for coin in COINS:
        result = results.get(coin)

        if not result:
            continue

        if result.get("error"):
            lines += [
                f"❌ <b>{coin}</b>",
                "Ошибка данных",
                "",
                "────────────",
                "",
            ]
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

        lines += [
            f"💠 <b>{coin}</b> "
            f"{format_price(price)}",

            f"{stage_icon(stage)} "
            f"{stage_text(stage)}",

            f"Score: <b>{score}/100</b>",

            "💧 <b>Крупная ликвидность:</b>",

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
        ]

    lines += [
        "1H → Major Liquidity → Sweep → 15M → 5M",
        "❌ В середине движения не входим.",
    ]

    return "\n".join(lines)


def build_levels_message(results):
    lines = [
        "💧 <b>TRADEMIND 4.1 — "
        "КЛЮЧЕВЫЕ УРОВНИ</b>",
        "",
        "Используем только крупную "
        "ликвидность 1H.",
        "",
    ]

    for coin in COINS:
        result = results.get(coin)

        if not result:
            continue

        if result.get("error"):
            continue

        price = result.get(
            "price"
        )

        lines += [
            f"💠 <b>{coin}</b> "
            f"{format_price(price)}",

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
        ]

    return "\n".join(lines)


def build_search_message(results):
    ready = find_first_ready(
        results
    )

    if ready:
        score, coin, result = ready

        return "\n".join([
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
            f"RR: "
            f"<b>{format_rr(result.get('rr'))}</b>",
            "",
            "🔥 Полное подтверждение получено.",
            "🎯 Один TP.",
            "📐 RR = 1:2.",
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

        if result.get("error"):
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

        lines.append(
            f"{stage_icon(stage)} "
            f"{coin}: "
            f"{stage_text(stage)} "
            f"— {score}/100"
        )

    lines += [
        "",
        "Ждём → Sweep → 15M → 5M.",
        "❌ В середине движения не входим.",
        "❌ Нет подтверждения → нет входа.",
    ]

    return "\n".join(lines)


def build_sol_message(result):
    if result.get("error"):
        return (
            "❌ <b>SOL</b>\n\n"
            f"{result.get('error')}"
        )

    stage = result.get(
        "stage",
        "WAIT",
    )

    lines = [
        "📈 <b>TRADEMIND 4.1 — SOL</b>",
        "",
        f"💰 Цена: "
        f"<b>{format_price(result.get('price'))}</b>",
        f"{stage_icon(stage)} "
        f"{stage_text(stage)}",
        f"Score: "
        f"<b>{result.get('score', 0)}/100</b>",
    ]

    if result.get("direction"):
        lines.append(
            f"Направление: "
            f"<b>{result.get('direction')}</b>"
        )

    lines += [
        "",
        "💧 <b>КРУПНАЯ ЛИКВИДНОСТЬ:</b>",
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

    if stage == "READY":
        lines += [
            "",
            "🎯 <b>СЕТАП</b>",
            f"Entry: "
            f"<b>{format_price(result.get('entry'))}</b>",
            f"SL: "
            f"<b>{format_price(result.get('sl'))}</b>",
            f"TP: "
            f"<b>{format_price(result.get('tp'))}</b>",
            f"RR: "
            f"<b>{format_rr(result.get('rr'))}</b>",
        ]

    if result.get("liquidity_warning"):
        lines += [
            "",
            f"⚠️ "
            f"{result.get('liquidity_warning')}",
        ]

    if result.get("reason"):
        lines += [
            "",
            f"Причина: "
            f"{result.get('reason')}",
        ]

    return "\n".join(lines)


# ============================================================
# SETUP
# ============================================================

def make_setup(coin, result):
    return {
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
            "score"
        ),
        "stage": result.get(
            "stage"
        ),
        "tp_reason": result.get(
            "tp_reason"
        ),
        "sweep_extreme": result.get(
            "sweep_extreme"
        ),
        "liquidity_warning": result.get(
            "liquidity_warning"
        ),
        "risk_usdt": getattr(
            bingx,
            "RISK_USDT",
            10,
        ),
        "created_at": datetime.utcnow().isoformat(),
    }


def setup_key(setup):
    return (
        f"{setup.get('coin')}_"
        f"{setup.get('direction')}_"
        f"{setup.get('entry')}_"
        f"{setup.get('sl')}_"
        f"{setup.get('tp')}"
    )


# ============================================================
# BINGX
# ============================================================

def bingx_mode_text():
    mode = bingx.mode()

    return {
        "OFF":
            "⚪ OFF — только сигнал, "
            "сделка НЕ открывается",

        "PAPER":
            "🟡 PAPER — виртуальная сделка",

        "CONFIRM":
            "🟠 CONFIRM — setup сохранён, "
            "используй /execute",

        "AUTO":
            "🟢 AUTO — реальный ордер",
    }.get(
        mode,
        f"⚪ {mode}",
    )


def build_bingx_message():
    try:
        cfg = bingx.config_status()
    except Exception:
        cfg = {
            "mode": bingx.mode(),
            "configured": False,
            "leverage": 5,
            "risk_usdt": 10,
        }

    return "\n".join([
        "🟠 <b>TRADEMIND 4.1 — BINGX</b>",
        "",
        f"Режим: "
        f"<b>{bingx_mode_text()}</b>",
        "",
        (
            "API: ✅"
            if cfg.get("configured")
            else "API: ❌"
        ),
        f"Leverage: "
        f"<b>{cfg.get('leverage', 5)}x</b>",
        f"Risk: "
        f"<b>${float(cfg.get('risk_usdt', 10)):.2f}</b>",
        "",
        "⚠️ AUTO сейчас не используем.",
        "Сначала тестируем сигналы.",
    ])


def load_pending():
    return load_json(
        PENDING_FILE,
        None,
    )


def save_pending(data):
    save_json(
        PENDING_FILE,
        data,
    )


def clear_pending():
    try:
        if os.path.exists(
            PENDING_FILE
        ):
            os.remove(
                PENDING_FILE
            )
    except Exception as exc:
        print(
            f"PENDING DELETE ERROR: {exc}"
        )


def execution_success(data):
    if not isinstance(
        data,
        dict,
    ):
        return False

    if data.get("ok") is True:
        return True

    status = data.get(
        "status"
    )

    return status in {
        "submitted",
        "simulated",
        "success",
        "filled",
    }


# ============================================================
# PNG CHART
# ============================================================

def png_chunk(kind, data):
    return (
        struct.pack(
            ">I",
            len(data),
        )
        + kind
        + data
        + struct.pack(
            ">I",
            zlib.crc32(
                kind + data
            ) & 0xffffffff,
        )
    )


def make_png(
    width,
    height,
    pixels,
):
    raw = bytearray()

    for row in pixels:
        raw.append(0)
        raw.extend(row)

    png = bytearray(
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
                0,
            ),
        )
    )

    png.extend(
        png_chunk(
            b"IDAT",
            zlib.compress(
                bytes(raw),
                6,
            ),
        )
    )

    png.extend(
        png_chunk(
            b"IEND",
            b"",
        )
    )

    return bytes(png)


def new_canvas(
    width,
    height,
    color,
):
    row = bytearray(
        color * width
    )

    return [
        bytearray(row)
        for _ in range(height)
    ]


def set_pixel(
    pixels,
    x,
    y,
    color,
):
    if not pixels:
        return

    if not pixels[0]:
        return

    height = len(pixels)
    width = len(
        pixels[0]
    ) // 3

    if (
        0 <= x < width
        and
        0 <= y < height
    ):
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
    dx = x2 - x1
    dy = y2 - y1

    steps = max(
        abs(dx),
        abs(dy),
        1,
    )

    for i in range(
        steps + 1
    ):
        x = int(
            x1
            + dx * i / steps
        )

        y = int(
            y1
            + dy * i / steps
        )

        radius = max(
            0,
            thickness // 2,
        )

        for xx in range(
            x - radius,
            x + radius + 1,
        ):
            for yy in range(
                y - radius,
                y + radius + 1,
            ):
                set_pixel(
                    pixels,
                    xx,
                    yy,
                    color,
                )


def draw_rect(
    pixels,
    x1,
    y1,
    x2,
    y2,
    color,
):
    if x1 > x2:
        x1, x2 = x2, x1

    if y1 > y2:
        y1, y2 = y2, y1

    height = len(
        pixels
    )

    width = len(
        pixels[0]
    ) // 3

    for y in range(
        max(0, y1),
        min(
            height,
            y2 + 1,
        ),
    ):
        for x in range(
            max(0, x1),
            min(
                width,
                x2 + 1,
            ),
        ):
            set_pixel(
                pixels,
                x,
                y,
                color,
            )


def candle_value(
    candle,
    key,
):
    if not isinstance(
        candle,
        dict,
    ):
        return None

    value = candle.get(
        key
    )

    aliases = {
        "open": ["o"],
        "high": ["h"],
        "low": ["l"],
        "close": ["c"],
        "open_time": [
            "time",
            "timestamp",
        ],
    }

    if value is None:
        for alias in aliases.get(
            key,
            [],
        ):
            if alias in candle:
                value = candle[
                    alias
                ]
                break

    try:
        return float(value)
    except Exception:
        return None


def render_chart_png(
    coin,
    result,
):
    candles = result.get(
        "candles_5m",
        [],
    )

    levels = result.get(
        "major_levels",
        [],
    )

    price = result.get(
        "price"
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

    valid = []

    for candle in candles:
        values = {
            key: candle_value(
                candle,
                key,
            )
            for key in (
                "open",
                "high",
                "low",
                "close",
            )
        }

        if all(
            value is not None
            for value in values.values()
        ):
            valid.append(
                values
            )

    valid = valid[-80:]

    if not valid:
        raise Exception(
            "Нет 5M свечей."
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
        24,
    )

    grid = (
        45,
        52,
        62,
    )

    bullish = (
        50,
        210,
        130,
    )

    bearish = (
        235,
        80,
        90,
    )

    blue = (
        80,
        170,
        255,
    )

    high_color = (
        240,
        80,
        90,
    )

    low_color = (
        50,
        210,
        130,
    )

    entry_color = (
        255,
        215,
        70,
    )

    tp_color = (
        80,
        220,
        150,
    )

    pixels = new_canvas(
        width,
        height,
        background,
    )

    values = []

    for candle in valid:
        values += [
            candle["high"],
            candle["low"],
        ]

    for level in levels:
        try:
            values.append(
                float(
                    level["price"]
                )
            )
        except Exception:
            pass

    for value in (
        price,
        entry,
        sl,
        tp,
    ):
        try:
            if value is not None:
                values.append(
                    float(value)
                )
        except Exception:
            pass

    if not values:
        raise Exception(
            "Нет данных для графика."
        )

    low = min(values)
    high = max(values)

    if high == low:
        high += 1
        low -= 1

    padding = (
        high - low
    ) * 0.08

    high += padding
    low -= padding

    def y_of(value):
        ratio = (
            high - value
        ) / (
            high - low
        )

        return int(
            top
            + ratio * chart_height
        )

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
        )

    for level in levels:
        try:
            level_price = float(
                level["price"]
            )
        except Exception:
            continue

        color = (
            high_color
            if "HIGH"
            in level.get(
                "type",
                "",
            )
            else low_color
        )

        y = y_of(
            level_price
        )

        draw_line(
            pixels,
            left,
            y,
            width - right,
            y,
            color,
            2,
        )

    count = len(valid)

    space = (
        chart_width
        / count
    )

    candle_width = max(
        3,
        int(
            space * 0.55
        ),
    )

    for i, candle in enumerate(
        valid
    ):
        x = int(
            left
            + (
                i + 0.5
            )
            * space
        )

        open_y = y_of(
            candle["open"]
        )

        high_y = y_of(
            candle["high"]
        )

        low_y = y_of(
            candle["low"]
        )

        close_y = y_of(
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
            x,
            high_y,
            x,
            low_y,
            color,
        )

        top_y = min(
            open_y,
            close_y,
        )

        bottom_y = max(
            open_y,
            close_y,
        )

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
            y = y_of(
                float(price)
            )

            draw_line(
                pixels,
                left,
                y,
                width - right,
                y,
                blue,
                2,
            )
        except Exception:
            pass

    for value, color in (
        (
            entry,
            entry_color,
        ),
        (
            sl,
            high_color,
        ),
        (
            tp,
            tp_color,
        ),
    ):
        if value is None:
            continue

        try:
            y = y_of(
                float(value)
            )

            draw_line(
                pixels,
                left,
                y,
                width - right,
                y,
                color,
                3,
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


def build_chart_caption(
    coin,
    result,
):
    stage = result.get(
        "stage",
        "WAIT",
    )

    lines = [
        f"📈 <b>TRADEMIND 4.1 — {coin}</b>",
        "",
        f"💰 Цена: "
        f"<b>{format_price(result.get('price'))}</b>",
        f"{stage_icon(stage)} "
        f"{stage_text(stage)}",
        f"⭐ Score: "
        f"<b>{result.get('score', 0)}/100</b>",
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

    if result.get(
        "direction"
    ):
        lines += [
            "",
            f"📐 Направление: "
            f"<b>{result.get('direction')}</b>",
        ]

    if stage == "READY":
        lines += [
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
            "🎯 Один TP.",
            "📐 RR = 1:2.",
        ]

    elif stage in (
        "CONFIRMED",
        "15M_CONFIRMED",
    ):
        lines += [
            "",
            "✅ Sweep",
            "✅ 15M confirmation",
            "⏳ Ждём 5M trigger",
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

    if result.get(
        "liquidity_warning"
    ):
        lines += [
            "",
            f"⚠️ "
            f"{result.get('liquidity_warning')}",
        ]

    return "\n".join(lines)


async def send_chart(
    message,
    coin,
):
    try:
        result = await asyncio.to_thread(
            build_analysis,
            COINS[coin],
        )

        image = render_chart_png(
            coin,
            result,
        )

        image.seek(0)

        await message.reply_photo(
            photo=InputFile(
                image,
                filename=(
                    f"{coin.lower()}_5m.png"
                ),
            ),
            caption=build_chart_caption(
                coin,
                result,
            ),
            parse_mode="HTML",
            reply_markup=chart_keyboard(),
        )

    except Exception as exc:
        await message.reply_text(
            f"❌ Ошибка графика "
            f"{coin}: {exc}",
            reply_markup=back_keyboard(),
        )


# ============================================================
# TELEGRAM HELPERS
# ============================================================

async def broadcast(
    application,
    text,
    keyboard=None,
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
                reply_markup=keyboard,
            )

        except Exception as exc:
            print(
                f"BROADCAST ERROR "
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
# /start
# ============================================================

async def start(
    update,
    context,
):
    chat_id = (
        update.effective_chat.id
    )

    subscribers = load_subscribers()

    if chat_id not in subscribers:
        subscribers.append(
            chat_id
        )

        save_subscribers(
            subscribers
        )

    await update.message.reply_text(
        "\n".join([
            "🤖 <b>TRADEMIND 4.1</b>",
            "",
            "Мониторинг:",
            "BTC • ETH • SOL • BNB • XRP",
            "HYPE • DOGE • LINK • SUI",
            "",
            f"⏱ Сканирование: "
            f"<b>{CHECK_INTERVAL} сек.</b>",
            "",
            "Стратегия:",
            "1H → Major Liquidity → Sweep → 15M → 5M",
            "",
            "Правила:",
            "💧 Только крупная ликвидность",
            "🎯 Sweep → 15M → 5M",
            "❌ Не входить в середине",
            "❌ Нет подтверждения → нет входа",
            "🎯 Только один TP",
            "📐 RR = 1:2",
            "",
            f"{bingx_mode_text()}",
        ]),
        parse_mode="HTML",
        reply_markup=main_keyboard(),
    )


# ============================================================
# BASIC COMMANDS
# ============================================================

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
            f"❌ Ошибка рынка:\n{exc}",
            reply_markup=back_keyboard(),
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
            f"❌ Ошибка уровней:\n{exc}",
            reply_markup=back_keyboard(),
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
            f"❌ Ошибка поиска:\n{exc}",
            reply_markup=back_keyboard(),
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
            build_sol_message(
                result
            ),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

    except Exception as exc:
        await update.message.reply_text(
            f"❌ Ошибка SOL:\n{exc}",
            reply_markup=back_keyboard(),
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
        coin = context.args[0].upper()

    await send_chart(
        update.message,
        coin,
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
# STATUS
# ============================================================

async def status_command(
    update,
    context,
):
    state = load_state()

    lines = [
        "📊 <b>TRADEMIND 4.1 — СТАТУС</b>",
        "",
        f"BingX mode: "
        f"<b>{bingx.mode()}</b>",
        f"Сделок сегодня: "
        f"<b>{state.get('daily_trades', 0)}/2</b>",
        f"Daily stop: "
        f"<b>{'YES' if state.get('daily_stop') else 'NO'}</b>",
    ]

    if state.get(
        "active_coin"
    ):
        lines += [
            "",
            "🔥 <b>АКТИВНЫЙ SETUP</b>",
            f"Монета: "
            f"<b>{state.get('active_coin')}</b>",
            f"Направление: "
            f"<b>{state.get('active_direction')}</b>",
            f"Stage: "
            f"<b>{state.get('active_stage')}</b>",
            f"Score: "
            f"<b>{state.get('active_score')}/100</b>",
            "",
            f"Entry: "
            f"<b>{format_price(state.get('active_entry'))}</b>",
            f"SL: "
            f"<b>{format_price(state.get('active_sl'))}</b>",
            f"TP: "
            f"<b>{format_price(state.get('active_tp'))}</b>",
            f"RR: "
            f"<b>{format_rr(state.get('active_rr'))}</b>",
        ]

    else:
        lines += [
            "",
            "🟢 Активной сделки нет.",
        ]

    await update.message.reply_text(
        "\n".join(lines),
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
            f"❌ BingX balance error:\n{exc}",
            reply_markup=back_keyboard(),
        )


async def position_command(
    update,
    context,
):
    try:
        positions = await asyncio.to_thread(
            bingx.get_positions
        )

        active = []

        for position in positions or []:
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
                "📌 <b>BingX — ПОЗИЦИИ</b>",
                "",
            ]

            for position in active:
                lines += [
                    f"💠 <b>{position.get('symbol')}</b>",
                    f"Side: "
                    f"<b>{position.get('positionSide')}</b>",
                    f"Qty: "
                    f"<b>{position.get('positionAmt')}</b>",
                    f"Entry: "
                    f"<b>{position.get('avgPrice')}</b>",
                    f"PnL: "
                    f"<b>{position.get('unrealizedProfit')}</b>",
                    "",
                ]

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
            f"❌ BingX position error:\n{exc}",
            reply_markup=back_keyboard(),
        )


# ============================================================
# EXECUTE
# ============================================================

async def execute_command(
    update,
    context,
):
    if bingx.mode() != "CONFIRM":
        await update.message.reply_text(
            "❌ /execute работает "
            "только при "
            "<b>BINGX_MODE=CONFIRM</b>.",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        return

    state = load_state()

    reset_daily_if_needed(
        state
    )

    if state.get(
        "daily_stop"
    ):
        await update.message.reply_text(
            "🛑 Торговля на сегодня остановлена.",
            reply_markup=back_keyboard(),
        )
        return

    if state.get(
        "daily_trades",
        0,
    ) >= 2:
        state["daily_stop"] = True
        save_state(state)

        await update.message.reply_text(
            "🛑 Достигнут лимит "
            "<b>2 сделки/день</b>.",
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )
        return

    pending = load_pending()

    if not pending:
        await update.message.reply_text(
            "📭 Pending setup отсутствует.",
            reply_markup=back_keyboard(),
        )
        return

    try:
        data = await asyncio.to_thread(
            bingx.execute_confirmed,
            pending,
        )

        if not execution_success(
            data
        ):
            error = (
                data.get("error")
                or data.get("message")
                or "BingX не подтвердил выполнение."
            )

            await update.message.reply_text(
                "❌ <b>Сделка НЕ выполнена</b>\n\n"
                f"Причина: {error}",
                parse_mode="HTML",
                reply_markup=back_keyboard(),
            )

            # Pending НЕ удаляем.
            return

        setup = pending

        state.update({
            "active_coin": setup.get(
                "coin"
            ),
            "active_symbol": setup.get(
                "symbol"
            ),
            "active_direction": setup.get(
                "direction"
            ),
            "active_setup_key": setup_key(
                setup
            ),
            "active_entry": setup.get(
                "entry"
            ),
            "active_sl": setup.get(
                "sl"
            ),
            "active_tp": setup.get(
                "tp"
            ),
            "active_rr": setup.get(
                "rr"
            ),
            "active_tp_reason": setup.get(
                "tp_reason"
            ),
            "active_sweep_extreme": setup.get(
                "sweep_extreme"
            ),
            "active_score": setup.get(
                "score"
            ),
            "active_stage": "READY",
            "last_alert": datetime.utcnow().isoformat(),
        })

        state["daily_trades"] = (
            int(
                state.get(
                    "daily_trades",
                    0,
                )
            )
            + 1
        )

        if state["daily_trades"] >= 2:
            state["daily_stop"] = True

        save_state(
            state
        )

        clear_pending()

        await update.message.reply_text(
            "\n".join([
                "🟢 <b>BingX order отправлен.</b>",
                "",
                f"Coin: "
                f"<b>{setup.get('coin')}</b>",
                f"Direction: "
                f"<b>{setup.get('direction')}</b>",
                "",
                f"Entry: "
                f"<b>{format_price(setup.get('entry'))}</b>",
                f"SL: "
                f"<b>{format_price(setup.get('sl'))}</b>",
                f"TP: "
                f"<b>{format_price(setup.get('tp'))}</b>",
                f"RR: "
                f"<b>{format_rr(setup.get('rr'))}</b>",
                "",
                f"Сделок сегодня: "
                f"<b>{state['daily_trades']}/2</b>",
            ]),
            parse_mode="HTML",
            reply_markup=back_keyboard(),
        )

    except Exception as exc:
        await update.message.reply_text(
            f"❌ BingX execute error:\n{exc}",
            reply_markup=back_keyboard(),
        )


# ============================================================
# CLOSE
# ============================================================

async def close_command(
    update,
    context,
):
    confirm = (
        bool(context.args)
        and
        context.args[0].upper()
        == "CONFIRM"
    )

    if not confirm:
        await update.message.reply_text(
            "⚠️ Команда закрывает "
            "позицию рыночным ордером.\n\n"
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
        state["active_tp_reason"] = None
        state["active_sweep_extreme"] = None
        state["active_score"] = None
        state["active_stage"] = None

        save_state(
            state
        )

        await update.message.reply_text(
            f"🟢 BingX close отправлен.\n"
            f"<code>{data}</code>",
            parse_mode="HTML",
        )

    except Exception as exc:
        await update.message.reply_text(
            f"❌ BingX close error:\n{exc}"
        )


# ============================================================
# JOURNAL
# ============================================================

async def journal_command(
    update,
    context,
):
    await update.message.reply_text(
        "\n".join([
            "📒 <b>TRADEMIND 4.1 — ЖУРНАЛ</b>",
            "",
            "Мониторинг сигналов подключён.",
            "",
            "Следующий этап:",
            "• фактический fill",
            "• TP/SL outcome",
            "• PnL",
            "• R",
            "• Win Rate",
            "• Total R",
            "• остановка после SL",
        ]),
        parse_mode="HTML",
        reply_markup=back_keyboard(),
    )


# ============================================================
# MONITOR
# ============================================================

async def monitor(
    application,
):
    print(
        "TradeMind 4.1 monitor started."
    )

    print(
        f"Scan interval: "
        f"{CHECK_INTERVAL}s"
    )

    print(
        f"BingX mode: "
        f"{bingx.mode()}"
    )

    while True:
        started = (
            asyncio.get_running_loop()
            .time()
        )

        try:
            state = load_state()

            reset_daily_if_needed(
                state
            )

            # Не создаём новые сделки после
            # двух сделок за день.
            if state.get(
                "daily_trades",
                0,
            ) >= 2:
                state["daily_stop"] = True

            results = (
                await scan_all_coins_async()
            )

            active_coin = state.get(
                "active_coin"
            )

            # ==================================================
            # READY SETUP
            # ==================================================

            if (
                not active_coin
                and
                not state.get(
                    "daily_stop"
                )
            ):
                ready = find_first_ready(
                    results
                )

                if ready:
                    score, coin, result = ready

                    setup = make_setup(
                        coin,
                        result,
                    )

                    current_key = setup_key(
                        setup
                    )

                    # Не шлём один и тот же
                    # сигнал бесконечно.
                    if (
                        current_key
                        != state.get(
                            "last_signal_key"
                        )
                    ):
                        mode = bingx.mode()

                        # ======================================
                        # OFF
                        # ======================================

                        if mode == "OFF":
                            state[
                                "last_signal_key"
                            ] = current_key

                            state[
                                "last_alert"
                            ] = datetime.utcnow().isoformat()

                            save_state(
                                state
                            )

                            text = "\n".join([
                                "🚨 <b>TRADEMIND 4.1 — СЕТАП</b>",
                                "",
                                f"💠 Монета: "
                                f"<b>{coin}</b>",
                                f"📐 Направление: "
                                f"<b>{setup.get('direction')}</b>",
                                f"⭐ Score: "
                                f"<b>{score}/100</b>",
                                "",
                                f"Entry: "
                                f"<b>{format_price(setup.get('entry'))}</b>",
                                f"SL: "
                                f"<b>{format_price(setup.get('sl'))}</b>",
                                f"TP: "
                                f"<b>{format_price(setup.get('tp'))}</b>",
                                f"RR: "
                                f"<b>{format_rr(setup.get('rr'))}</b>",
                                "",
                                "⚪ <b>OFF</b> — "
                                "только сигнал, "
                                "сделка НЕ открыта.",
                                "",
                                "1H → Major Liquidity "
                                "→ Sweep → 15M → 5M",
                                "🎯 Один TP.",
                                "📐 RR = 1:2.",
                            ])

                            if setup.get(
                                "liquidity_warning"
                            ):
                                text += (
                                    "\n⚠️ "
                                    f"{setup.get('liquidity_warning')}"
                                )

                            await broadcast(
                                application,
                                text,
                            )

                        # ======================================
                        # CONFIRM
                        # ======================================

                        elif mode == "CONFIRM":
                            try:
                                execution = (
                                    await asyncio.to_thread(
                                        bingx.open_trade,
                                        setup,
                                    )
                                )

                            except Exception as exc:
                                print(
                                    "BINGX CONFIRM ERROR:",
                                    exc,
                                )

                                execution = {
                                    "ok": False,
                                    "status": "error",
                                    "error": str(exc),
                                }

                            if (
                                execution.get(
                                    "status"
                                )
                                == "pending_confirmation"
                            ):
                                save_pending(
                                    setup
                                )

                                state[
                                    "last_signal_key"
                                ] = current_key

                                state[
                                    "last_alert"
                                ] = datetime.utcnow().isoformat()

                                save_state(
                                    state
                                )

                                text = "\n".join([
                                    "🚨 <b>TRADEMIND 4.1 — СЕТАП</b>",
                                    "",
                                    f"💠 Монета: "
                                    f"<b>{coin}</b>",
                                    f"📐 Направление: "
                                    f"<b>{setup.get('direction')}</b>",
                                    f"⭐ Score: "
                                    f"<b>{score}/100</b>",
                                    "",
                                    f"Entry: "
                                    f"<b>{format_price(setup.get('entry'))}</b>",
                                    f"SL: "
                                    f"<b>{format_price(setup.get('sl'))}</b>",
                                    f"TP: "
                                    f"<b>{format_price(setup.get('tp'))}</b>",
                                    f"RR: "
                                    f"<b>{format_rr(setup.get('rr'))}</b>",
                                    "",
                                    "🟠 <b>CONFIRM</b>",
                                    "",
                                    "Setup сохранён.",
                                    "Для исполнения:",
                                    "<code>/execute</code>",
                                    "",
                                    "❌ Автоматически "
                                    "сделка не открывается.",
                                ])

                                if setup.get(
                                    "liquidity_warning"
                                ):
                                    text += (
                                        "\n⚠️ "
                                        f"{setup.get('liquidity_warning')}"
                                    )

                                await broadcast(
                                    application,
                                    text,
                                )

                        # ======================================
                        # PAPER / AUTO
                        # ======================================

                        else:
                            try:
                                execution = (
                                    await asyncio.to_thread(
                                        bingx.open_trade,
                                        setup,
                                    )
                                )

                            except Exception as exc:
                                print(
                                    "BINGX OPEN ERROR:",
                                    exc,
                                )

                                execution = {
                                    "ok": False,
                                    "status": "error",
                                    "error": str(exc),
                                }

                            status = execution.get(
                                "status"
                            )

                            if status in {
                                "simulated",
                                "submitted",
                            }:
                                state.update({
                                    "active_coin": coin,
                                    "active_symbol": setup.get(
                                        "symbol"
                                    ),
                                    "active_direction": setup.get(
                                        "direction"
                                    ),
                                    "active_setup_key": current_key,
                                    "active_entry": setup.get(
                                        "entry"
                                    ),
                                    "active_sl": setup.get(
                                        "sl"
                                    ),
                                    "active_tp": setup.get(
                                        "tp"
                                    ),
                                    "active_rr": setup.get(
                                        "rr"
                                    ),
                                    "active_tp_reason": setup.get(
                                        "tp_reason"
                                    ),
                                    "active_sweep_extreme": setup.get(
                                        "sweep_extreme"
                                    ),
                                    "active_score": score,
                                    "active_stage": "READY",
                                    "last_signal_key": current_key,
                                    "last_alert": datetime.utcnow().isoformat(),
                                })

                                state[
                                    "daily_trades"
                                ] = (
                                    int(
                                        state.get(
                                            "daily_trades",
                                            0,
                                        )
                                    )
                                    + 1
                                )

                                if (
                                    state[
                                        "daily_trades"
                                    ]
                                    >= 2
                                ):
                                    state[
                                        "daily_stop"
                                    ] = True

                                save_state(
                                    state
                                )

                                text = "\n".join([
                                    "🚨 <b>TRADEMIND 4.1 — СЕТАП</b>",
                                    "",
                                    f"💠 Монета: "
                                    f"<b>{coin}</b>",
                                    f"📐 Направление: "
                                    f"<b>{setup.get('direction')}</b>",
                                    f"⭐ Score: "
                                    f"<b>{score}/100</b>",
                                    "",
                                    f"Entry: "
                                    f"<b>{format_price(setup.get('entry'))}</b>",
                                    f"SL: "
                                    f"<b>{format_price(setup.get('sl'))}</b>",
                                    f"TP: "
                                    f"<b>{format_price(setup.get('tp'))}</b>",
                                    f"RR: "
                                    f"<b>{format_rr(setup.get('rr'))}</b>",
                                    "",
                                    bingx_mode_text(),
                                ])

                                await broadcast(
                                    application,
                                    text,
                                )

            # ==================================================
            # EARLY STAGES
            # ==================================================

            for coin, result in results.items():
                if not result:
                    continue

                if result.get(
                    "error"
                ):
                    continue

                direction = result.get(
                    "direction"
                )

                stage = result.get(
                    "stage"
                )

                sweep = result.get(
                    "sweep"
                )

                coin_state = (
                    state[
                        "coins"
                    ].setdefault(
                        coin,
                        {},
                    )
                )

                # ----------------------------------------------
                # SWEEP
                # ----------------------------------------------

                if sweep:
                    sweep_price = (
                        sweep.get(
                            "price"
                        )
                        or
                        sweep.get(
                            "level"
                        )
                    )

                    sweep_time = sweep.get(
                        "open_time"
                    )

                    sweep_key = (
                        f"{coin}_"
                        f"{direction}_"
                        f"{sweep_price}_"
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
                                "🔎 <b>TRADEMIND 4.1 — SWEEP</b>",
                                "",
                                f"💠 {coin}",
                                f"📐 {direction}",
                                "",
                                "💧 Крупная ликвидность снята.",
                                "⏳ Ждём 15M confirmation.",
                                "❌ Вход пока запрещён.",
                            ]),
                        )

                # ----------------------------------------------
                # 15M CONFIRMATION
                # ----------------------------------------------

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

                        await broadcast(
                            application,
                            "\n".join([
                                "🟡 <b>TRADEMIND 4.1 — "
                                "15M CONFIRMATION</b>",
                                "",
                                f"💠 {coin}",
                                f"📐 {direction}",
                                "",
                                "✅ Sweep",
                                "✅ 15M confirmation",
                                "⏳ Ждём 5M trigger.",
                                "❌ Вход пока запрещён.",
                            ]),
                        )

            save_state(
                state
            )

        except Exception as exc:
            print(
                f"MONITOR ERROR: {exc}"
            )

        elapsed = (
            asyncio.get_running_loop()
            .time()
            - started
        )

        await asyncio.sleep(
            max(
                1,
                CHECK_INTERVAL
                - elapsed,
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

    try:

        # ------------------------------------------
        # HOME
        # ------------------------------------------

        if data == "start":
            await query.edit_message_text(
                "\n".join([
                    "🤖 <b>TRADEMIND 4.1</b>",
                    "",
                    "9 монет • сканирование "
                    f"{CHECK_INTERVAL} секунд",
                    "",
                    "1H → Major Liquidity "
                    "→ Sweep → 15M → 5M",
                    "",
                    f"{bingx_mode_text()}",
                ]),
                parse_mode="HTML",
                reply_markup=main_keyboard(),
            )
            return

        # ------------------------------------------
        # MARKET
        # ------------------------------------------

        if data == "market":
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

        # ------------------------------------------
        # LEVELS
        # ------------------------------------------

        if data == "levels":
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

        # ------------------------------------------
        # SEARCH
        # ------------------------------------------

        if data == "search":
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

        # ------------------------------------------
        # CHART
        # ------------------------------------------

        if data == "chart":
            await query.edit_message_text(
                "📈 <b>TRADEMIND 4.1 — ГРАФИК</b>\n\n"
                "Выбери монету:",
                parse_mode="HTML",
                reply_markup=chart_keyboard(),
            )
            return

        if data.startswith(
            "chart_"
        ):
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

        # ------------------------------------------
        # SOL
        # ------------------------------------------

        if data == "sol":
            try:
                result = await asyncio.to_thread(
                    build_analysis,
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
                    f"❌ Ошибка SOL:\n{exc}",
                    reply_markup=back_keyboard(),
                )

            return

        # ------------------------------------------
        # STATUS
        # ------------------------------------------

        if data == "status":
            state = load_state()

            text = "\n".join([
                "📊 <b>TRADEMIND 4.1 — STATUS</b>",
                "",
                f"Active: "
                f"<b>{state.get('active_coin') or 'нет'}</b>",
                f"Direction: "
                f"<b>{state.get('active_direction') or '—'}</b>",
                f"Score: "
                f"<b>{state.get('active_score') or '—'}</b>",
                "",
                f"BingX: "
                f"<b>{bingx.mode()}</b>",
                f"Daily trades: "
                f"<b>{state.get('daily_trades', 0)}/2</b>",
                f"Daily stop: "
                f"<b>{'YES' if state.get('daily_stop') else 'NO'}</b>",
            ])

            if state.get(
                "active_coin"
            ):
                text += "\n\n" + "\n".join([
                    f"Entry: "
                    f"<b>{format_price(state.get('active_entry'))}</b>",
                    f"SL: "
                    f"<b>{format_price(state.get('active_sl'))}</b>",
                    f"TP: "
                    f"<b>{format_price(state.get('active_tp'))}</b>",
                    f"RR: "
                    f"<b>{format_rr(state.get('active_rr'))}</b>",
                ])

            await query.edit_message_text(
                text,
                parse_mode="HTML",
                reply_markup=back_keyboard(),
            )

            return

        # ------------------------------------------
        # BINGX
        # ------------------------------------------

        if data == "bingx":
            await query.edit_message_text(
                build_bingx_message(),
                parse_mode="HTML",
                reply_markup=back_keyboard(),
            )
            return

        # ------------------------------------------
        # SUBSCRIBE
        # ------------------------------------------

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

        # ------------------------------------------
        # UNSUBSCRIBE
        # ------------------------------------------

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

        # ------------------------------------------
        # JOURNAL
        # ------------------------------------------

        if data == "journal":
            await query.edit_message_text(
                "\n".join([
                    "📒 <b>TRADEMIND 4.1 — ЖУРНАЛ</b>",
                    "",
                    "Система журнала подключена.",
                    "",
                    "Следующий этап:",
                    "PnL / R / Win Rate / Total R",
                    "по фактическим TP/SL.",
                ]),
                parse_mode="HTML",
                reply_markup=back_keyboard(),
            )

            return

    except Exception as exc:
        print(
            f"CALLBACK ERROR: {exc}"
        )

        try:
            await query.edit_message_text(
                f"❌ Ошибка:\n{exc}",
                reply_markup=back_keyboard(),
            )
        except Exception:
            pass


# ============================================================
# COMMANDS
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
            "chart",
            "График",
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
            "Закрыть позицию",
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
            "sol",
            "Анализ SOL",
        ),
        BotCommand(
            "journal",
            "Журнал",
        ),
    ]

    await application.bot.set_my_commands(
        commands
    )


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
            "BOT_TOKEN не найден."
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
            "levels",
            levels_command,
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
            "bingx",
            bingx_command,
        ),
        (
            "balance",
            balance_command,
        ),
        (
            "position",
            position_command,
        ),
        (
            "execute",
            execute_command,
        ),
        (
            "close",
            close_command,
        ),
        (
            "subscribe",
            subscribe_command,
        ),
        (
            "unsubscribe",
            unsubscribe_command,
        ),
        (
            "sol",
            sol_command,
        ),
        (
            "journal",
            journal_command,
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
        "TradeMind 4.1 started."
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
        "Coins:",
        ", ".join(
            COINS.keys()
        ),
    )

    print(
        "BingX mode:",
        bingx.mode(),
    )

    application.run_polling()


if __name__ == "__main__":
    main()