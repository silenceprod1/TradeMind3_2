import asyncio
import json
import os
from datetime import datetime
from io import BytesIO

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

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

CHECK_INTERVAL = 30

COINS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
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
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"JSON SAVE ERROR: {e}")


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
        "active_score": None,
        "active_stage": None,
        "last_alert": None,
        "coins": {}
    }


def load_state():
    return load_json(STATE_FILE, default_state())


def save_state(state):
    save_json(STATE_FILE, state)


# =========================================================
# KEYBOARDS
# =========================================================

def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Рынок", callback_data="market"),
            InlineKeyboardButton("💧 Уровни", callback_data="levels"),
        ],
        [
            InlineKeyboardButton("🔎 Поиск сетапа", callback_data="search"),
        ],
        [
            InlineKeyboardButton("📈 График", callback_data="chart"),
        ],
        [
            InlineKeyboardButton("🔔 Уведомления", callback_data="subscribe"),
            InlineKeyboardButton("🔕 Выключить", callback_data="unsubscribe"),
        ],
        [
            InlineKeyboardButton("📈 SOL", callback_data="sol"),
            InlineKeyboardButton("📊 Статус", callback_data="status"),
        ],
        [
            InlineKeyboardButton("📒 Журнал", callback_data="journal"),
        ],
    ])


def chart_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("₿ BTC", callback_data="chart_BTC"),
            InlineKeyboardButton("Ξ ETH", callback_data="chart_ETH"),
            InlineKeyboardButton("◎ SOL", callback_data="chart_SOL"),
        ],
        [
            InlineKeyboardButton("⬅️ Главное меню", callback_data="start"),
        ],
    ])


def chart_coin_keyboard(coin):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("₿ BTC", callback_data="chart_BTC"),
            InlineKeyboardButton("Ξ ETH", callback_data="chart_ETH"),
            InlineKeyboardButton("◎ SOL", callback_data="chart_SOL"),
        ],
        [
            InlineKeyboardButton(
                f"🔄 Обновить {coin}",
                callback_data=f"chart_{coin}"
            ),
        ],
        [
            InlineKeyboardButton("⬅️ Главное меню", callback_data="start"),
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

    price = float(price)

    if price >= 1000:
        return f"${price:,.2f}"

    if price >= 1:
        return f"${price:,.4f}"

    return f"${price:,.6f}"


def format_levels(levels, price):
    if not levels:
        return "💧 Крупные уровни не найдены."

    lines = []

    for level in levels:
        level_price = level.get("price")

        if level_price is None:
            continue

        level_type = level.get("type", "")
        distance = abs(level_price - price) / price * 100

        icon = "🔴" if "HIGH" in level_type else "🟢"

        lines.append(
            f"{icon} {level_type}: "
            f"{format_price(level_price)} "
            f"({distance:.2f}%)"
        )

    return "\n".join(lines) if lines else "💧 Крупные уровни не найдены."


def stage_icon(stage):
    if stage == "READY":
        return "🟢"

    if stage in ("CONFIRMED", "15M_CONFIRMED"):
        return "🟡"

    if stage == "SWEPT":
        return "🟠"

    if stage == "WAIT":
        return "⏳"

    return "⚪"


def stage_text(stage):
    if stage == "READY":
        return "МОЖНО ВХОДИТЬ"

    if stage in ("CONFIRMED", "15M_CONFIRMED"):
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
    data = get_market_data(symbol)

    if not data:
        raise Exception(f"Нет данных для {symbol}")

    price = data["price"]

    candles_1h = data["candles_1h"]
    candles_15m = data["candles_15m"]
    candles_5m = data["candles_5m"]

    major_levels = find_major_liquidity(
        candles_1h,
        price,
        max_levels=6
    )

    sweep = detect_sweep(
        candles_5m,
        major_levels
    )

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

    return result


def scan_all_coins():
    results = {}

    for coin, symbol in COINS.items():
        try:
            results[coin] = build_analysis(symbol)

        except Exception as e:
            print(f"{coin} ERROR: {e}")

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
                    result.get("score", 0),
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
# CHART
# =========================================================

def candle_datetime(candle):
    try:
        timestamp = float(candle.get("open_time"))

        if timestamp > 10_000_000_000:
            timestamp /= 1000

        return datetime.fromtimestamp(timestamp)

    except Exception:
        return None


def get_chart_status(result):
    stage = result.get("stage", "WAIT")
    direction = result.get("direction")

    if stage == "READY":
        return "🟢 МОЖНО ВХОДИТЬ", direction

    if stage in ("CONFIRMED", "15M_CONFIRMED"):
        return "🟡 ЖДЁМ 5M TRIGGER", direction

    if stage == "SWEPT":
        return "🟠 ЖДЁМ 15M CONFIRMATION", direction

    return "⏳ ЖДЁМ MAJOR LIQUIDITY SWEEP", None


def build_chart(coin, result):
    symbol = result["symbol"]

    # Получаем максимально свежие данные
    data = get_market_data(symbol)

    candles = data["candles_5m"]
    price = float(data["price"])

    levels = result.get("major_levels", [])
    sweep = result.get("sweep") or {}

    candles = candles[-80:]

    if len(candles) < 5:
        raise Exception("Недостаточно 5M свечей.")

    fig, ax = plt.subplots(
        figsize=(13, 7)
    )

    width = 0.62

    opens = []
    highs = []
    lows = []
    closes = []

    # -----------------------------------------------------
    # CANDLES
    # -----------------------------------------------------

    for i, candle in enumerate(candles):

        o = float(candle["open"])
        h = float(candle["high"])
        l = float(candle["low"])
        c = float(candle["close"])

        opens.append(o)
        highs.append(h)
        lows.append(l)
        closes.append(c)

        bullish = c >= o

        candle_color = "#16a34a" if bullish else "#dc2626"

        # wick
        ax.vlines(
            i,
            l,
            h,
            color=candle_color,
            linewidth=1.0,
            zorder=2
        )

        body_low = min(o, c)
        body_height = abs(c - o)

        if body_height == 0:
            body_height = price * 0.00003

        rect = Rectangle(
            (
                i - width / 2,
                body_low
            ),
            width,
            body_height,
            facecolor=candle_color,
            edgecolor=candle_color,
            linewidth=0.7,
            zorder=3
        )

        ax.add_patch(rect)

    # -----------------------------------------------------
    # MAJOR LIQUIDITY
    # -----------------------------------------------------

    for level in levels:

        level_price = float(
            level["price"]
        )

        level_type = level.get(
            "type",
            ""
        )

        if "HIGH" in level_type:
            color = "#dc2626"
        else:
            color = "#16a34a"

        ax.axhline(
            level_price,
            color=color,
            linestyle="--",
            linewidth=1.2,
            alpha=0.75,
            zorder=1
        )

        ax.text(
            len(candles) - 1,
            level_price,
            f"  {level_type} "
            f"{format_price(level_price)}",
            color=color,
            fontsize=8,
            va="center",
            fontweight="bold"
        )

    # -----------------------------------------------------
    # CURRENT PRICE
    # -----------------------------------------------------

    ax.axhline(
        price,
        color="#2563eb",
        linestyle="-",
        linewidth=1.5,
        alpha=0.9
    )

    ax.text(
        len(candles) - 1,
        price,
        f"  PRICE {format_price(price)}",
        color="#2563eb",
        fontsize=9,
        va="bottom",
        fontweight="bold"
    )

    # -----------------------------------------------------
    # SWEEP
    # -----------------------------------------------------

    if sweep:

        sweep_level = (
            sweep.get("level")
            or sweep.get("price")
        )

        if sweep_level is not None:

            sweep_level = float(
                sweep_level
            )

            sweep_direction = sweep.get(
                "direction",
                result.get("direction", "")
            )

            idx = len(candles) - 1

            ax.scatter(
                [idx],
                [sweep_level],
                s=100,
                color="#f59e0b",
                zorder=5
            )

            ax.annotate(
                f"SWEEP {sweep_direction}\n"
                f"{format_price(sweep_level)}",
                xy=(idx, sweep_level),
                xytext=(
                    max(0, idx - 18),
                    sweep_level
                ),
                arrowprops={
                    "arrowstyle": "->",
                    "color": "#f59e0b",
                    "lw": 1.5
                },
                fontsize=9,
                fontweight="bold",
                color="#b45309"
            )

    # -----------------------------------------------------
    # ENTRY / SL / TP
    # -----------------------------------------------------

    if result.get("stage") == "READY":

        entry = result.get("entry")
        sl = result.get("sl")
        tp = result.get("tp")

        if entry is not None:

            entry = float(entry)

            ax.axhline(
                entry,
                color="#2563eb",
                linestyle="-.",
                linewidth=1.4
            )

            ax.text(
                len(candles) - 1,
                entry,
                f"  ENTRY {format_price(entry)}",
                fontsize=9,
                color="#2563eb",
                va="bottom",
                fontweight="bold"
            )

        if sl is not None:

            sl = float(sl)

            ax.axhline(
                sl,
                color="#dc2626",
                linestyle=":",
                linewidth=1.5
            )

            ax.text(
                len(candles) - 1,
                sl,
                f"  SL {format_price(sl)}",
                fontsize=9,
                color="#dc2626",
                va="bottom",
                fontweight="bold"
            )

        if tp is not None:

            tp = float(tp)

            ax.axhline(
                tp,
                color="#16a34a",
                linestyle=":",
                linewidth=1.5
            )

            ax.text(
                len(candles) - 1,
                tp,
                f"  TP {format_price(tp)}",
                fontsize=9,
                color="#16a34a",
                va="bottom",
                fontweight="bold"
            )

    # -----------------------------------------------------
    # WAITING SCENARIO
    # -----------------------------------------------------

    stage = result.get(
        "stage",
        "WAIT"
    )

    if stage == "WAIT":

        # Показываем ближайшие уровни,
        # от которых ждём sweep.
        above = []
        below = []

        for level in levels:

            level_price = float(
                level["price"]
            )

            if level_price > price:
                above.append(level)
            elif level_price < price:
                below.append(level)

        above.sort(
            key=lambda x: float(x["price"])
        )

        below.sort(
            key=lambda x: float(x["price"]),
            reverse=True
        )

        if below:

            target = below[0]

            ax.annotate(
                "ЖДЁМ LONG SWEEP",
                xy=(
                    len(candles) - 1,
                    float(target["price"])
                ),
                xytext=(
                    max(0, len(candles) - 25),
                    float(target["price"])
                    - (max(highs) - min(lows)) * 0.12
                ),
                arrowprops={
                    "arrowstyle": "->",
                    "color": "#16a34a",
                    "lw": 1.5
                },
                fontsize=9,
                color="#16a34a",
                fontweight="bold"
            )

        if above:

            target = above[0]

            ax.annotate(
                "ЖДЁМ SHORT SWEEP",
                xy=(
                    len(candles) - 1,
                    float(target["price"])
                ),
                xytext=(
                    max(0, len(candles) - 25),
                    float(target["price"])
                    + (max(highs) - min(lows)) * 0.12
                ),
                arrowprops={
                    "arrowstyle": "->",
                    "color": "#dc2626",
                    "lw": 1.5
                },
                fontsize=9,
                color="#dc2626",
                fontweight="bold"
            )

    # -----------------------------------------------------
    # TITLE
    # -----------------------------------------------------

    status, direction = get_chart_status(
        result
    )

    title = (
        f"TRADEMIND 3.6 — {coin} / 5M\n"
        f"{status}"
    )

    if direction:
        title += f"  |  {direction}"

    ax.set_title(
        title,
        fontsize=15,
        fontweight="bold"
    )

    ax.set_ylabel("Price")
    ax.set_xlabel(
        "Последние закрытые 5M свечи"
    )

    ax.grid(
        alpha=0.18
    )

    # -----------------------------------------------------
    # Y RANGE
    # -----------------------------------------------------

    values = (
        highs
        + lows
        + [price]
    )

    for level in levels:
        values.append(
            float(level["price"])
        )

    for key in [
        "entry",
        "sl",
        "tp"
    ]:
        if result.get(key) is not None:
            values.append(
                float(result[key])
            )

    low = min(values)
    high = max(values)

    padding = max(
        (high - low) * 0.08,
        price * 0.001
    )

    ax.set_ylim(
        low - padding,
        high + padding
    )

    # -----------------------------------------------------
    # X LABELS
    # -----------------------------------------------------

    labels = []

    for candle in candles:

        dt = candle_datetime(
            candle
        )

        if dt:
            labels.append(
                dt.strftime("%H:%M")
            )
        else:
            labels.append("")

    step = max(
        1,
        len(labels) // 8
    )

    positions = list(
        range(
            0,
            len(labels),
            step
        )
    )

    ax.set_xticks(
        positions
    )

    ax.set_xticklabels(
        [
            labels[i]
            for i in positions
        ]
    )

    plt.tight_layout()

    # -----------------------------------------------------
    # PNG IN MEMORY
    # -----------------------------------------------------

    buffer = BytesIO()

    fig.savefig(
        buffer,
        format="png",
        dpi=150,
        bbox_inches="tight"
    )

    plt.close(fig)

    buffer.seek(0)

    return buffer


def chart_caption(
    coin,
    result
):

    price = result.get(
        "price"
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
    ]

    if direction:
        lines.append(
            f"📐 Направление: <b>{direction}</b>"
        )

    lines.append("")

    if stage == "WAIT":

        lines.extend([
            "⏳ <b>СЕЙЧАС ЖДЁМ:</b>",
            "",
            "1️⃣ Major liquidity",
            "2️⃣ Sweep",
            "3️⃣ 15M confirmation",
            "4️⃣ 5M trigger",
            "",
            "❌ В середине движения не входим."
        ])

    elif stage == "SWEPT":

        lines.extend([
            "💧 <b>SWEEP ЕСТЬ</b>",
            "",
            "✅ Major liquidity снята",
            "⏳ Ждём 15M confirmation",
            "❌ Вход пока запрещён"
        ])

    elif stage in (
        "CONFIRMED",
        "15M_CONFIRMED"
    ):

        lines.extend([
            "✅ Sweep",
            "✅ 15M confirmation",
            "⏳ Ждём 5M trigger",
            "❌ Вход пока запрещён"
        ])

    elif stage == "READY":

        lines.extend([
            "🔥 <b>ПОЛНОЕ ПОДТВЕРЖДЕНИЕ</b>",
            "",
            f"Entry: <b>{format_price(result.get('entry'))}</b>",
            f"SL: <b>{format_price(result.get('sl'))}</b>",
            f"TP: <b>{format_price(result.get('tp'))}</b>",
            "",
            "RR: <b>1:2</b>"
        ])

    return "\n".join(lines)


async def send_chart(
    message,
    coin
):

    try:

        result = build_analysis(
            COINS[coin]
        )

        image = build_chart(
            coin,
            result
        )

        caption = chart_caption(
            coin,
            result
        )

        await message.reply_photo(
            photo=image,
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

def build_market_message(
    results
):

    lines = [
        "📊 <b>TRADEMIND — РЫНОК</b>",
        ""
    ]

    for coin in [
        "BTC",
        "ETH",
        "SOL"
    ]:

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


def build_levels_message(
    results
):

    lines = [
        "💧 <b>TRADEMIND — КЛЮЧЕВЫЕ УРОВНИ</b>",
        "",
        "Используем только крупную ликвидность 1H.",
        "Мелкие локальные уровни не учитываются.",
        ""
    ]

    for coin in [
        "BTC",
        "ETH",
        "SOL"
    ]:

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


def build_sol_message(
    result
):

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
            f"Направление: <b>{direction}</b>"
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

    if result.get("entry"):

        lines.extend([
            "",
            "🎯 <b>СЕТАП</b>",
            "",
            f"Entry: <b>{format_price(result['entry'])}</b>",
            f"SL: <b>{format_price(result['sl'])}</b>",
            f"TP: <b>{format_price(result['tp'])}</b>",
            "",
            "RR: <b>1:2</b>"
        ])

    reason = result.get(
        "reason"
    )

    if reason:

        lines.extend([
            "",
            "Причина:",
            reason
        ])

    return "\n".join(lines)


def build_search_message(
    results
):

    lines = [
        "🔎 <b>ПОИСК СЕТАПА</b>",
        ""
    ]

    ready = find_first_ready(
        results
    )

    if ready:

        score, coin, result = ready

        lines.extend([
            "🟢 <b>НАЙДЕН СЕТАП</b>",
            "",
            f"Монета: <b>{coin}</b>",
            f"Направление: <b>{result.get('direction')}</b>",
            f"Score: <b>{score}/100</b>",
            "",
            f"Entry: <b>{format_price(result.get('entry'))}</b>",
            f"SL: <b>{format_price(result.get('sl'))}</b>",
            f"TP: <b>{format_price(result.get('tp'))}</b>",
            "",
            "RR: <b>1:2</b>",
            "",
            "🔥 Полное подтверждение получено."
        ])

        return "\n".join(lines)

    lines.extend([
        "❌ Готового входа сейчас нет.",
        ""
    ])

    for coin in [
        "BTC",
        "ETH",
        "SOL"
    ]:

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
        "🤖 <b>TRADEMIND 3.6</b>\n\n"
        "Мониторинг:\n"
        "• BTC\n"
        "• ETH\n"
        "• SOL\n\n"
        "Стратегия:\n"
        "1H → Major Liquidity → Sweep → 15M → 5M\n\n"
        "RR: <b>1:2</b>\n"
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
        build_market_message(results),
        parse_mode="HTML",
        reply_markup=back_keyboard()
    )


async def levels_command(
    update,
    context
):

    results = scan_all_coins()

    await update.message.reply_text(
        build_levels_message(results),
        parse_mode="HTML",
        reply_markup=back_keyboard()
    )


async def search_command(
    update,
    context
):

    results = scan_all_coins()

    await update.message.reply_text(
        build_search_message(results),
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
            build_sol_message(result),
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
            "Отслеживаю BTC, ETH и SOL."
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
            f"<b>{format_price(state.get('active_tp'))}</b>"
        )

    else:

        text = (
            "📊 <b>TRADEMIND — СТАТУС</b>\n\n"
            "🟢 Активного входа нет.\n\n"
            "Мониторинг:\n"
            "BTC • ETH • SOL"
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
        "• Entry\n"
        "• SL\n"
        "• TP\n"
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

    while True:

        try:

            state = load_state()

            # Пока есть активный сетап,
            # новые монеты не запускаем.
            if state.get("active_coin"):

                await asyncio.sleep(
                    CHECK_INTERVAL
                )

                continue

            results = scan_all_coins()

            # -------------------------------------------------
            # READY
            # -------------------------------------------------

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

                        "active_coin": coin,

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
                        f"TP: <b>{format_price(tp)}</b>\n\n"
                        "RR: <b>1:2</b>\n\n"
                        "🔥 Полное подтверждение:\n"
                        "1H → Sweep → 15M → 5M"
                    )

                    await broadcast(
                        application,
                        text
                    )

                await asyncio.sleep(
                    CHECK_INTERVAL
                )

                continue

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
                            f"Направление: <b>{direction}</b>\n\n"
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

        await asyncio.sleep(
            CHECK_INTERVAL
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
            "🤖 <b>TRADEMIND 3.6</b>\n\n"
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
                f"<b>{format_price(state.get('active_tp'))}</b>"
            )

        else:

            text = (
                "📊 <b>СТАТУС</b>\n\n"
                "🟢 Активного сетапа нет.\n\n"
                "BTC • ETH • SOL"
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
                "🔔 <b>Уведомления включены.</b>"
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
            "Рынок BTC ETH SOL"
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
        "TradeMind 3.6 started."
    )

    application.run_polling()


if __name__ == "__main__":
    main()