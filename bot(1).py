# ============================================================
# TRADEMIND 6.2.1
# Binance Spot
# 1H -> Major Liquidity -> Sweep -> 15M -> 5M ILM
# D1/W1 REMOVED
# NO MATPLOTLIB
# ============================================================

import os
import json
import asyncio
import traceback

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
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

from strategy import (
    analyze,
    STRATEGY_VERSION,
)


# ============================================================
# CONFIG
# ============================================================

BOT_VERSION = "6.2.1"

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не найден в Environment Variables"
    )

CHECK_INTERVAL = 15
SCAN_WORKERS = 9
MIN_SCORE_READY = 80


# ============================================================
# COINS
# ============================================================

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

STATE_FILE = "monitor_state.json"
JOURNAL_FILE = "trade_journal.json"


# ============================================================
# JSON
# ============================================================

def load_json(filename, default):
    try:
        if not os.path.exists(filename):
            return default

        with open(
            filename,
            "r",
            encoding="utf-8",
        ) as f:
            return json.load(f)

    except Exception:
        return default


def save_json(filename, data):
    try:
        tmp = filename + ".tmp"

        with open(
            tmp,
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(tmp, filename)

    except Exception:
        traceback.print_exc()


# ============================================================
# STATE
# ============================================================

def default_state():
    return {
        "last_signal_key": {},
        "last_sweep_keys": {},
        "last_15m_keys": {},
        "last_ready_keys": {},
        "subscribers": [],
    }


def load_state():
    state = load_json(
        STATE_FILE,
        default_state(),
    )

    if not isinstance(state, dict):
        state = default_state()

    # Удаляем старые ограничения
    state.pop("daily_date", None)
    state.pop("daily_trades", None)
    state.pop("daily_stop", None)

    for key in (
        "last_signal_key",
        "last_sweep_keys",
        "last_15m_keys",
        "last_ready_keys",
    ):
        if not isinstance(
            state.get(key),
            dict,
        ):
            state[key] = {}

    if not isinstance(
        state.get("subscribers"),
        list,
    ):
        state["subscribers"] = []

    return state


STATE = load_state()


def save_state():
    save_json(
        STATE_FILE,
        STATE,
    )


# ============================================================
# SUBSCRIBERS
# ============================================================

def get_subscribers():
    return STATE.get(
        "subscribers",
        [],
    )


def add_subscriber(chat_id):
    subscribers = get_subscribers()

    if chat_id not in subscribers:
        subscribers.append(chat_id)

        STATE["subscribers"] = subscribers

        save_state()


def remove_subscriber(chat_id):
    subscribers = get_subscribers()

    if chat_id in subscribers:
        subscribers.remove(chat_id)

        STATE["subscribers"] = subscribers

        save_state()


# ============================================================
# FORMATTING
# ============================================================

def fmt_price(value):
    if value is None:
        return "—"

    try:
        value = float(value)

        if value >= 1000:
            return f"{value:,.2f}"

        if value >= 100:
            return f"{value:.2f}"

        if value >= 1:
            return f"{value:.4f}"

        return f"{value:.6f}"

    except Exception:
        return str(value)


def pct_distance(level, price):
    try:
        return (
            (float(level) / float(price) - 1)
            * 100
        )
    except Exception:
        return 0.0


def stage_icon(stage):
    stage = str(
        stage or ""
    ).upper()

    mapping = {
        "READY": "🟢",
        "5M": "🟢",
        "15M_CONFIRMED": "🟡",
        "15M": "🟡",
        "SWEEP": "🟠",
        "SWEPT": "🟠",
        "1H": "🔵",
        "WAIT": "⏳",
        "NO_TRADE": "❌",
    }

    return mapping.get(
        stage,
        "⏳",
    )


def direction_icon(direction):
    if direction == "LONG":
        return "🟢"

    if direction == "SHORT":
        return "🔴"

    return "⚪"


# ============================================================
# LIQUIDITY HELPERS
# ============================================================

def level_price(level):
    if not isinstance(level, dict):
        return None

    for key in (
        "price",
        "level",
        "value",
    ):
        if level.get(key) is not None:
            try:
                return float(
                    level[key]
                )
            except Exception:
                pass

    return None


def level_position(
    level,
    price=None,
):
    if not isinstance(level, dict):
        return None

    position = str(
        level.get(
            "position",
            "",
        )
    ).upper()

    if position in (
        "ABOVE",
        "BELOW",
    ):
        return position

    side = str(
        level.get(
            "side",
            "",
        )
    ).upper()

    if side == "SHORT":
        return "ABOVE"

    if side == "LONG":
        return "BELOW"

    value = level_price(level)

    if (
        value is not None
        and price is not None
    ):
        return (
            "ABOVE"
            if value > float(price)
            else "BELOW"
        )

    return None


def split_liquidity(
    levels,
    price,
):
    above = []
    below = []

    for level in levels or []:

        p = level_price(level)

        if p is None:
            continue

        position = level_position(
            level,
            price,
        )

        if position == "ABOVE":
            above.append(level)

        elif position == "BELOW":
            below.append(level)

    above.sort(
        key=lambda x:
        level_price(x) or 10**30
    )

    below.sort(
        key=lambda x:
        level_price(x) or -10**30,
        reverse=True,
    )

    return above, below


def nearest_bsl(
    levels,
    price,
):
    above, _ = split_liquidity(
        levels,
        price,
    )

    return (
        above[0]
        if above
        else None
    )


def nearest_ssl(
    levels,
    price,
):
    _, below = split_liquidity(
        levels,
        price,
    )

    return (
        below[0]
        if below
        else None
    )


# ============================================================
# ANALYSIS
# ============================================================

def build_analysis(symbol):
    data = get_market_data(symbol)

    price = float(
        data["price"]
    )

    candles_1h = data.get(
        "candles_1h",
        [],
    )

    candles_15m = data.get(
        "candles_15m",
        [],
    )

    candles_5m = data.get(
        "candles_5m",
        [],
    )

    candles_1m = data.get(
        "candles_1m",
        [],
    )

    # --------------------------------------------------------
    # MAJOR LIQUIDITY
    # 1H base + optional local clusters
    # --------------------------------------------------------

    major_levels = find_major_liquidity(
        candles_1h,
        price,
        max_levels=20,
        include_swept=True,
        candles_15m=candles_15m,
        candles_5m=candles_5m,
        candles_1m=candles_1m,
    )

    # --------------------------------------------------------
    # FIRST PASS
    # Только 1H direction
    # --------------------------------------------------------

    context = analyze(
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,
        candles_1m=candles_1m,
        current_price=price,
        major_levels=major_levels,
        sweep=None,
    )

    direction = context.get(
        "direction"
    )

    sweep = None

    # --------------------------------------------------------
    # SWEEP
    # LONG -> SSL
    # SHORT -> BSL
    # --------------------------------------------------------

    if direction in (
        "LONG",
        "SHORT",
    ):

        sweep = detect_sweep(
            candles_1h,
            price,
            direction,
        )

    # --------------------------------------------------------
    # SECOND PASS
    # Full strategy
    # --------------------------------------------------------

    result = analyze(
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,
        candles_1m=candles_1m,
        current_price=price,
        major_levels=major_levels,
        sweep=sweep,
    )

    if not isinstance(
        result,
        dict,
    ):
        result = {}

    result.update({
        "symbol": symbol,
        "price": price,
        "major_levels": major_levels,
        "sweep": sweep,
        "candles_1m": candles_1m,
        "candles_5m": candles_5m,
        "candles_15m": candles_15m,
        "candles_1h": candles_1h,
        "strategy_version": STRATEGY_VERSION,
    })

    if not result.get(
        "direction"
    ):
        result["direction"] = direction

    return result


# ============================================================
# STAGE
# ============================================================

def get_result_stage(result):
    stage = result.get(
        "stage"
    )

    if stage:
        return str(
            stage
        ).upper()

    if result.get(
        "ready"
    ) is True:
        return "READY"

    if result.get(
        "trigger_5m"
    ):
        return "5M"

    if result.get(
        "confirmation_15m"
    ):
        return "15M_CONFIRMED"

    if result.get(
        "sweep"
    ):
        return "SWEEP"

    return "WAIT"


# ============================================================
# SOL / COIN MESSAGE
# ============================================================

def build_coin_message(result):
    symbol = result.get(
        "symbol",
        "SOL",
    )

    price = result.get(
        "price",
        0,
    )

    direction = result.get(
        "direction"
    )

    score = result.get(
        "score",
        0,
    )

    stage = get_result_stage(
        result
    )

    levels = result.get(
        "major_levels",
        [],
    )

    bsl = nearest_bsl(
        levels,
        price,
    )

    ssl = nearest_ssl(
        levels,
        price,
    )

    sweep = result.get(
        "sweep"
    )

    lines = [
        f"📈 <b>TRADEMIND {BOT_VERSION} — {symbol}</b>",
        "",
        f"💰 Цена: <b>${fmt_price(price)}</b>",
        "",
    ]

    if direction:
        lines.append(
            f"{direction_icon(direction)} "
            f"<b>1H DIRECTION: {direction}</b>"
        )
    else:
        lines.append(
            "⚪ <b>1H DIRECTION: NEUTRAL</b>"
        )

    lines.append(
        f"{stage_icon(stage)} "
        f"Stage: <b>{stage}</b>"
    )

    lines.append(
        f"⭐ Score: <b>{score}/100</b>"
    )

    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
        "💧 <b>MAJOR LIQUIDITY</b>",
        "━━━━━━━━━━━━━━━━━━",
    ])

    if bsl:
        p = level_price(bsl)

        if p:
            dist = pct_distance(
                p,
                price,
            )

            lines.append(
                f"🔴 <b>BSL</b> "
                f"${fmt_price(p)} "
                f"— +{dist:.2f}%"
            )

            lines.append(
                f"   touches: "
                f"{bsl.get('touches', 0)}"
            )

            lines.append(
                f"   strength: "
                f"{bsl.get('strength', 0)}/100"
            )

    else:
        lines.append(
            "🔴 BSL — нет major liquidity"
        )

    lines.append("")

    if ssl:
        p = level_price(ssl)

        if p:
            dist = abs(
                pct_distance(
                    p,
                    price,
                )
            )

            lines.append(
                f"🟢 <b>SSL</b> "
                f"${fmt_price(p)} "
                f"— -{dist:.2f}%"
            )

            lines.append(
                f"   touches: "
                f"{ssl.get('touches', 0)}"
            )

            lines.append(
                f"   strength: "
                f"{ssl.get('strength', 0)}/100"
            )

    else:
        lines.append(
            "🟢 SSL — нет major liquidity"
        )

    # --------------------------------------------------------
    # SWEEP
    # --------------------------------------------------------

    if sweep:

        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━",
            "🟠 <b>SWEEP</b>",
            "━━━━━━━━━━━━━━━━━━",
        ])

        sweep_level = (
            sweep.get("level")
            or sweep.get("price")
            or sweep.get(
                "liquidity_level"
            )
        )

        extreme = (
            sweep.get("extreme")
            or sweep.get(
                "sweep_extreme"
            )
            or sweep.get("high")
            or sweep.get("low")
        )

        if sweep_level:
            lines.append(
                f"💧 Level: "
                f"${fmt_price(sweep_level)}"
            )

        if extreme:
            lines.append(
                f"📍 Extreme: "
                f"${fmt_price(extreme)}"
            )

        if stage in (
            "SWEEP",
            "SWEPT",
        ):

            lines.append("")

            lines.append(
                "⏳ <b>ЖДЁМ 15M CONFIRMATION</b>"
            )

        elif stage in (
            "15M",
            "15M_CONFIRMED",
        ):

            lines.append("")

            lines.append(
                "🟡 <b>15M CONFIRMED</b>"
            )

            lines.append(
                "⏳ Ждём 5M ILM"
            )

        elif stage in (
            "5M",
            "READY",
        ):

            lines.append("")

            lines.append(
                "🟢 <b>5M ILM CONFIRMED</b>"
            )

    # --------------------------------------------------------
    # READY
    # --------------------------------------------------------

    if stage == "READY":

        entry = (
            result.get("entry")
            or result.get(
                "entry_price"
            )
        )

        sl = (
            result.get("sl")
            or result.get(
                "stop_loss"
            )
        )

        tp = (
            result.get("tp")
            or result.get(
                "take_profit"
            )
        )

        rr = result.get(
            "rr"
        )

        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━",
            "🟢 <b>READY</b>",
            "━━━━━━━━━━━━━━━━━━",
        ])

        if entry:
            lines.append(
                f"🎯 Entry: "
                f"<b>${fmt_price(entry)}</b>"
            )

        if sl:
            lines.append(
                f"🛑 SL: "
                f"<b>${fmt_price(sl)}</b>"
            )

        if tp:
            lines.append(
                f"💰 TP: "
                f"<b>${fmt_price(tp)}</b>"
            )

        if rr is not None:
            try:
                lines.append(
                    f"📐 RR: "
                    f"<b>1:{float(rr):.2f}</b>"
                )
            except Exception:
                pass

        lines.append("")

        lines.append(
            "🟢 <b>МОЖНО ВХОДИТЬ</b>"
        )

    elif stage in (
        "WAIT",
        "NO_TRADE",
    ):

        reason = result.get(
            "reason"
        )

        if reason:
            lines.append("")

            lines.append(
                "Причина:"
            )

            lines.append(
                str(reason)
            )

    return "\n".join(lines)


# ============================================================
# RADAR
# ============================================================

def radar_line(
    symbol,
    result,
):
    score = result.get(
        "score",
        0,
    )

    direction = (
        result.get(
            "direction"
        )
        or "NEUTRAL"
    )

    stage = get_result_stage(
        result
    )

    icon = stage_icon(
        stage
    )

    if stage in (
        "SWEEP",
        "SWEPT",
    ):

        text = (
            "SWEEP СНЯТ — "
            "ЖДЁМ 15M"
        )

    elif stage in (
        "15M",
        "15M_CONFIRMED",
    ):

        text = (
            "15M CONFIRMED — "
            "ЖДЁМ 5M"
        )

    elif stage == "READY":

        text = "READY"

    else:

        text = "ОЖИДАНИЕ"

    return (
        f"{icon} <b>{symbol}</b> — "
        f"{text} · "
        f"{direction} · "
        f"{score}/100"
    )


async def build_radar():
    results = []

    async def scan(symbol):

        try:
            result = await asyncio.to_thread(
                build_analysis,
                COINS[symbol],
            )

            return symbol, result

        except Exception as exc:

            return symbol, {
                "score": 0,
                "direction": "NEUTRAL",
                "stage": "WAIT",
                "reason": (
                    f"Ошибка анализа: "
                    f"{exc}"
                ),
            }

    scanned = await asyncio.gather(
        *[
            scan(symbol)
            for symbol in COINS
        ]
    )

    results.extend(
        scanned
    )

    lines = [
        f"🧠 <b>TRADEMIND {BOT_VERSION}</b>",
        "",
        "📡 Monitor: <b>ONLINE</b>",
        "📊 Market: <b>Binance Spot</b>",
        "",
        "━━━━━━━━━━━━━━━━━━",
        "📡 <b>TRADE RADAR</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]

    ready_count = 0

    for symbol, result in results:

        lines.append(
            radar_line(
                symbol,
                result,
            )
        )

        if (
            get_result_stage(
                result
            ) == "READY"
            and float(
                result.get(
                    "score",
                    0,
                )
            ) >= MIN_SCORE_READY
        ):
            ready_count += 1

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
    ])

    if ready_count:

        lines.append(
            f"🟢 <b>ГОТОВЫХ СЕТАПОВ: "
            f"{ready_count}</b>"
        )

    else:

        lines.append(
            "🎯 <b>ГОТОВОГО СЕТАПА НЕТ</b>"
        )

    lines.extend([
        "",
        "⏳ Ждём:",
        "1H → Major Liquidity → Sweep",
        "→ 15M → 5M ILM",
        "",
        "❌ Нет полного подтверждения",
        "→ нет входа.",
    ])

    return "\n".join(lines)


# ============================================================
# KEYBOARD
# ============================================================

def main_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔄 Обновить",
                callback_data="refresh",
            ),
            InlineKeyboardButton(
                "🔥 Активный сетап",
                callback_data="active",
            ),
        ],
        [
            InlineKeyboardButton(
                "📡 Radar",
                callback_data="radar",
            ),
            InlineKeyboardButton(
                "💧 Liquidity",
                callback_data="liquidity",
            ),
        ],
        [
            InlineKeyboardButton(
                "📈 SOL",
                callback_data="sol",
            ),
        ],
        [
            InlineKeyboardButton(
                "📓 Journal",
                callback_data="journal",
            ),
            InlineKeyboardButton(
                "ℹ️ Status",
                callback_data="status",
            ),
        ],
    ])


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = update.effective_chat.id

    add_subscriber(
        chat_id
    )

    text = await build_radar()

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


# ============================================================
# SUBSCRIBE
# ============================================================

async def subscribe(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = update.effective_chat.id

    add_subscriber(
        chat_id
    )

    await update.message.reply_text(
        "📡 <b>TradeMind monitor включён.</b>",
        parse_mode=ParseMode.HTML,
    )


# ============================================================
# UNSUBSCRIBE
# ============================================================

async def unsubscribe(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = update.effective_chat.id

    remove_subscriber(
        chat_id
    )

    await update.message.reply_text(
        "🔕 Мониторинг выключен."
    )


# ============================================================
# RADAR COMMAND
# ============================================================

async def radar_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    text = await build_radar()

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


# ============================================================
# SOL
# ============================================================

async def sol_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    try:

        result = await asyncio.to_thread(
            build_analysis,
            COINS["SOL"],
        )

        text = build_coin_message(
            result
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔄 Обновить",
                    callback_data="sol",
                )
            ],
            [
                InlineKeyboardButton(
                    "📡 Radar",
                    callback_data="radar",
                )
            ],
        ])

        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )

    except Exception as exc:

        traceback.print_exc()

        await update.message.reply_text(
            f"❌ Ошибка SOL:\n"
            f"<code>{exc}</code>",
            parse_mode=ParseMode.HTML,
        )


# ============================================================
# CALLBACK
# ============================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    data = query.data or ""

    # --------------------------------------------------------
    # RADAR
    # --------------------------------------------------------

    if data in (
        "radar",
        "refresh",
    ):

        text = await build_radar()

        await safe_edit_message(
            query,
            text,
            main_keyboard(),
        )

        return

    # --------------------------------------------------------
    # SOL
    # --------------------------------------------------------

    if data == "sol":

        result = await asyncio.to_thread(
            build_analysis,
            COINS["SOL"],
        )

        text = build_coin_message(
            result
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔄 Обновить",
                    callback_data="sol",
                )
            ],
            [
                InlineKeyboardButton(
                    "📡 Radar",
                    callback_data="radar",
                )
            ],
        ])

        await safe_edit_message(
            query,
            text,
            keyboard,
        )

        return

    # --------------------------------------------------------
    # ACTIVE
    # --------------------------------------------------------

    if data == "active":

        found = []

        for symbol, pair in COINS.items():

            try:

                result = await asyncio.to_thread(
                    build_analysis,
                    pair,
                )

                stage = get_result_stage(
                    result
                )

                score = float(
                    result.get(
                        "score",
                        0,
                    )
                )

                if (
                    stage == "READY"
                    and score >= MIN_SCORE_READY
                ):
                    found.append(
                        (
                            symbol,
                            result,
                        )
                    )

            except Exception:
                continue

        if not found:

            text = (
                "🔥 <b>ACTIVE SETUP</b>\n\n"
                "🎯 Готового сетапа нет.\n\n"
                f"Минимальный score: "
                f"{MIN_SCORE_READY}/100"
            )

        else:

            lines = [
                "🔥 <b>ACTIVE SETUPS</b>",
                "",
            ]

            for symbol, result in found:

                lines.append(
                    build_coin_message(
                        result
                    )
                )

                lines.append("")

            text = "\n".join(
                lines
            )

        await safe_edit_message(
            query,
            text,
            main_keyboard(),
        )

        return

    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    if data == "liquidity":

        try:

            result = await asyncio.to_thread(
                build_analysis,
                COINS["SOL"],
            )

            price = result.get(
                "price",
                0,
            )

            levels = result.get(
                "major_levels",
                [],
            )

            bsl = nearest_bsl(
                levels,
                price,
            )

            ssl = nearest_ssl(
                levels,
                price,
            )

            lines = [
                "💧 <b>SOL LIQUIDITY</b>",
                "",
            ]

            if bsl:

                p = level_price(
                    bsl
                )

                lines.append(
                    f"🔴 BSL: "
                    f"<b>${fmt_price(p)}</b>"
                )

                lines.append(
                    f"Touches: "
                    f"{bsl.get('touches', 0)}"
                )

                lines.append(
                    f"Strength: "
                    f"{bsl.get('strength', 0)}/100"
                )

            else:

                lines.append(
                    "🔴 BSL: нет"
                )

            lines.append("")

            if ssl:

                p = level_price(
                    ssl
                )

                lines.append(
                    f"🟢 SSL: "
                    f"<b>${fmt_price(p)}</b>"
                )

                lines.append(
                    f"Touches: "
                    f"{ssl.get('touches', 0)}"
                )

                lines.append(
                    f"Strength: "
                    f"{ssl.get('strength', 0)}/100"
                )

            else:

                lines.append(
                    "🟢 SSL: нет"
                )

            await safe_edit_message(
                query,
                "\n".join(lines),
                main_keyboard(),
            )

        except Exception as exc:

            await safe_edit_message(
                query,
                f"❌ Liquidity error:\n"
                f"{exc}",
                main_keyboard(),
            )

        return

    # --------------------------------------------------------
    # JOURNAL
    # --------------------------------------------------------

    if data == "journal":

        journal = load_json(
            JOURNAL_FILE,
            [],
        )

        if not journal:

            text = (
                "📓 <b>TRADE JOURNAL</b>\n\n"
                "Пока сделок нет."
            )

        else:

            lines = [
                "📓 <b>TRADE JOURNAL</b>",
                "",
            ]

            for trade in journal[-10:]:

                lines.append(
                    f"• "
                    f"{trade.get('symbol', '—')} "
                    f"{trade.get('direction', '—')} "
                    f"{trade.get('result', '—')}"
                )

            text = "\n".join(
                lines
            )

        await safe_edit_message(
            query,
            text,
            main_keyboard(),
        )

        return

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    if data == "status":

        text = (
            f"🧠 <b>TRADEMIND {BOT_VERSION}</b>\n\n"
            f"📡 Monitor: ONLINE\n"
            f"📊 Market: Binance Spot\n"
            f"🕐 Scan: {CHECK_INTERVAL}s\n"
            f"🧵 Workers: {SCAN_WORKERS}\n"
            f"⭐ READY score: {MIN_SCORE_READY}+\n\n"
            f"📐 Strategy: {STRATEGY_VERSION}\n\n"
            f"1H → Major Liquidity\n"
            f"→ Sweep → 15M → 5M ILM\n\n"
            f"❌ D1: REMOVED\n"
            f"❌ W1: REMOVED\n"
            f"💧 Hybrid liquidity: ON\n"
            f"📊 Chart engine: OFF\n"
            f"🔢 Daily trade limit: OFF"
        )

        await safe_edit_message(
            query,
            text,
            main_keyboard(),
        )

        return


# ============================================================
# SAFE EDIT
# ============================================================

async def safe_edit_message(
    query,
    text,
    reply_markup=None,
):

    try:

        await query.edit_message_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )

        return True

    except BadRequest as exc:

        if "Message is not modified" in str(
            exc
        ):
            return False

        raise


# ============================================================
# MONITOR KEYS
# ============================================================

def make_sweep_key(
    symbol,
    result,
):

    sweep = result.get(
        "sweep"
    )

    if not sweep:
        return None

    level = (
        sweep.get("level")
        or sweep.get("price")
        or sweep.get(
            "liquidity_level"
        )
    )

    extreme = (
        sweep.get("extreme")
        or sweep.get(
            "sweep_extreme"
        )
        or sweep.get("high")
        or sweep.get("low")
    )

    return (
        f"{symbol}:"
        f"{result.get('direction')}:"
        f"{level}:"
        f"{extreme}"
    )


def make_15m_key(
    symbol,
    result,
):

    return (
        f"{symbol}:"
        f"{result.get('direction')}:"
        f"{result.get('stage')}:"
        f"{result.get('confirmation_15m')}"
    )


def make_ready_key(
    symbol,
    result,
):

    return (
        f"{symbol}:"
        f"{result.get('direction')}:"
        f"{result.get('entry')}:"
        f"{result.get('sl')}:"
        f"{result.get('tp')}"
    )


# ============================================================
# SEND MONITOR MESSAGE
# ============================================================

async def send_monitor_message(
    application,
    text,
):

    subscribers = list(
        get_subscribers()
    )

    for chat_id in subscribers:

        try:

            await application.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )

        except Exception:

            traceback.print_exc()


# ============================================================
# SWEEP ALERT
# ============================================================

async def send_sweep_alert(
    application,
    symbol,
    result,
):

    direction = result.get(
        "direction"
    )

    sweep = result.get(
        "sweep"
    )

    level = (
        sweep.get("level")
        or sweep.get("price")
        or sweep.get(
            "liquidity_level"
        )
    )

    extreme = (
        sweep.get("extreme")
        or sweep.get(
            "sweep_extreme"
        )
        or sweep.get("high")
        or sweep.get("low")
    )

    text = (
        f"🟠 <b>{symbol} — "
        f"{direction} SWEEP</b>\n\n"
        f"💧 Liquidity снята\n"
        f"Level: ${fmt_price(level)}\n"
        f"Extreme: ${fmt_price(extreme)}\n\n"
        f"⏳ <b>ЖДЁМ 15M CONFIRMATION</b>\n"
        f"❌ Вход пока запрещён."
    )

    await send_monitor_message(
        application,
        text,
    )


# ============================================================
# 15M ALERT
# ============================================================

async def send_15m_alert(
    application,
    symbol,
    result,
):

    direction = result.get(
        "direction"
    )

    text = (
        f"🟡 <b>{symbol} — "
        f"15M CONFIRMED</b>\n\n"
        f"Direction: {direction}\n\n"
        f"⏳ <b>ЖДЁМ 5M ILM</b>\n"
        f"❌ Вход пока запрещён."
    )

    await send_monitor_message(
        application,
        text,
    )


# ============================================================
# READY ALERT
# ============================================================

async def send_ready_alert(
    application,
    symbol,
    result,
):

    direction = result.get(
        "direction"
    )

    entry = (
        result.get("entry")
        or result.get(
            "entry_price"
        )
    )

    sl = (
        result.get("sl")
        or result.get(
            "stop_loss"
        )
    )

    tp = (
        result.get("tp")
        or result.get(
            "take_profit"
        )
    )

    rr = result.get(
        "rr"
    )

    score = result.get(
        "score",
        0,
    )

    try:
        rr_text = (
            f"1:{float(rr):.2f}"
        )
    except Exception:
        rr_text = "—"

    text = (
        f"🟢 <b>{symbol} — "
        f"READY {direction}</b>\n\n"
        f"⭐ Score: {score}/100\n\n"
        f"🎯 Entry: ${fmt_price(entry)}\n"
        f"🛑 SL: ${fmt_price(sl)}\n"
        f"💰 TP: ${fmt_price(tp)}\n"
        f"📐 RR: {rr_text}\n\n"
        f"🟢 <b>МОЖНО ВХОДИТЬ</b>"
    )

    await send_monitor_message(
        application,
        text,
    )


# ============================================================
# MONITOR COIN
# ============================================================

async def monitor_coin(
    application,
    symbol,
):

    pair = COINS[
        symbol
    ]

    try:

        result = await asyncio.to_thread(
            build_analysis,
            pair,
        )

        stage = get_result_stage(
            result
        )

        # ----------------------------------------------------
        # SWEEP
        # ----------------------------------------------------

        if stage in (
            "SWEEP",
            "SWEPT",
        ):

            key = make_sweep_key(
                symbol,
                result,
            )

            if (
                key
                and
                STATE[
                    "last_sweep_keys"
                ].get(symbol)
                != key
            ):

                STATE[
                    "last_sweep_keys"
                ][symbol] = key

                save_state()

                await send_sweep_alert(
                    application,
                    symbol,
                    result,
                )

        # ----------------------------------------------------
        # 15M
        # ----------------------------------------------------

        if stage in (
            "15M",
            "15M_CONFIRMED",
        ):

            key = make_15m_key(
                symbol,
                result,
            )

            if (
                key
                and
                STATE[
                    "last_15m_keys"
                ].get(symbol)
                != key
            ):

                STATE[
                    "last_15m_keys"
                ][symbol] = key

                save_state()

                await send_15m_alert(
                    application,
                    symbol,
                    result,
                )

        # ----------------------------------------------------
        # READY
        # ----------------------------------------------------

        score = float(
            result.get(
                "score",
                0,
            )
        )

        if (
            stage == "READY"
            and score >= MIN_SCORE_READY
        ):

            key = make_ready_key(
                symbol,
                result,
            )

            if (
                key
                and
                STATE[
                    "last_ready_keys"
                ].get(symbol)
                != key
            ):

                STATE[
                    "last_ready_keys"
                ][symbol] = key

                save_state()

                await send_ready_alert(
                    application,
                    symbol,
                    result,
                )

    except Exception:

        traceback.print_exc()


# ============================================================
# MONITOR LOOP
# ============================================================

async def monitor_loop(
    application,
):

    semaphore = asyncio.Semaphore(
        SCAN_WORKERS
    )

    async def worker(symbol):

        async with semaphore:

            await monitor_coin(
                application,
                symbol,
            )

    while True:

        try:

            await asyncio.gather(
                *[
                    worker(symbol)
                    for symbol in COINS
                ]
            )

        except Exception:

            traceback.print_exc()

        await asyncio.sleep(
            CHECK_INTERVAL
        )


# ============================================================
# POST INIT
# ============================================================

async def post_init(
    application,
):

    task = asyncio.create_task(
        monitor_loop(
            application
        )
    )

    application.bot_data[
        "monitor_task"
    ] = task

    print(
        f"TradeMind {BOT_VERSION} "
        f"background monitor launched."
    )


# ============================================================
# POST SHUTDOWN
# ============================================================

async def post_shutdown(
    application,
):

    task = application.bot_data.get(
        "monitor_task"
    )

    if (
        task
        and
        not task.done()
    ):

        task.cancel()

        try:

            await task

        except asyncio.CancelledError:
            pass

    print(
        "TradeMind monitor stopped."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "subscribe",
            subscribe,
        )
    )

    application.add_handler(
        CommandHandler(
            "unsubscribe",
            unsubscribe,
        )
    )

    application.add_handler(
        CommandHandler(
            "radar",
            radar_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "sol",
            sol_command,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_handler,
        )
    )

    print(
        f"TradeMind {BOT_VERSION} started."
    )

    application.run_polling(
        drop_pending_updates=True,
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()