# ============================================================
# TRADEMIND 6.2.1
# Binance Spot | 1H -> Major Liquidity -> Sweep -> 15M -> 5M
# D1/W1 REMOVED
# Hybrid liquidity | Telegram charts | Auto monitor
# ============================================================

import os
import io
import json
import asyncio
import traceback

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

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

MAX_LOCAL_ZONE_PCT = 0.0040


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

SUBSCRIBERS_FILE = "subscribers.json"
STATE_FILE = "monitor_state.json"
JOURNAL_FILE = "trade_journal.json"


# ============================================================
# JSON
# ============================================================

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
        tmp = filename + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
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

    # Старые ограничения полностью удаляем
    state.pop("daily_date", None)
    state.pop("daily_trades", None)
    state.pop("daily_stop", None)

    for key in (
        "last_signal_key",
        "last_sweep_keys",
        "last_15m_keys",
        "last_ready_keys",
        "subscribers",
    ):
        if key not in state:
            state[key] = (
                []
                if key == "subscribers"
                else {}
            )

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
            (
                float(level)
                / float(price)
            ) - 1.0
        ) * 100.0

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


def level_price(level):
    if not isinstance(level, dict):
        return None

    for key in (
        "price",
        "level",
        "value",
    ):
        value = level.get(key)

        if value is not None:
            try:
                return float(value)
            except Exception:
                pass

    return None


def level_position(level, price=None):
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


# ============================================================
# LIQUIDITY
# ============================================================

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
        key=lambda x: (
            level_price(x)
            if level_price(x) is not None
            else 10**30
        )
    )

    below.sort(
        key=lambda x: (
            level_price(x)
            if level_price(x) is not None
            else -10**30
        ),
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


def liquidity_strength(level):
    if not isinstance(level, dict):
        return 0.0

    try:
        strength = float(
            level.get(
                "strength",
                0,
            )
        )
    except Exception:
        strength = 0.0

    try:
        touches = int(
            level.get(
                "touches",
                0,
            )
        )
    except Exception:
        touches = 0

    # Поддерживаем как 0-1, так и 0-100
    if 0 < strength <= 1:
        score = strength * 70.0
    else:
        score = strength

    score += min(
        touches,
        8,
    ) * 3.75

    return min(
        100.0,
        round(score, 1),
    )


def build_liquidity_zone(
    levels,
    center_level,
    price,
):
    center = level_price(
        center_level
    )

    if center is None:
        return None

    position = level_position(
        center_level,
        price,
    )

    nearby = []

    for level in levels or []:
        p = level_price(level)

        if p is None:
            continue

        if level_position(
            level,
            price,
        ) != position:
            continue

        distance = (
            abs(p - center)
            / center
        )

        if distance <= MAX_LOCAL_ZONE_PCT:
            nearby.append(p)

    points = [
        center,
        *nearby,
    ]

    low = min(points)
    high = max(points)

    max_width = (
        center
        * MAX_LOCAL_ZONE_PCT
    )

    low = max(
        low,
        center - max_width,
    )

    high = min(
        high,
        center + max_width,
    )

    return {
        "low": low,
        "high": high,
        "center": center,
        "position": position,
        "strength": liquidity_strength(
            center_level
        ),
        "major": center_level,
        "local_count": len(
            nearby
        ),
    }


# ============================================================
# ANALYSIS
# ============================================================

def build_analysis(symbol):
    data = get_market_data(
        symbol
    )

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

    # ========================================================
    # MAJOR + HYBRID LIQUIDITY
    # ========================================================

    major_levels = find_major_liquidity(
        candles_1h,
        price,
        max_levels=20,
        include_swept=True,
        candles_15m=candles_15m,
        candles_5m=candles_5m,
        candles_1m=candles_1m,
    )

    # ========================================================
    # FIRST PASS
    # Получаем направление только от 1H.
    # D1/W1 НЕ ИСПОЛЬЗУЮТСЯ.
    # ========================================================

    context = analyze(
        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,
        candles_1m=candles_1m,
        current_price=price,
        major_levels=major_levels,
        sweep=None,
    )

    if not isinstance(
        context,
        dict,
    ):
        context = {}

    direction = context.get(
        "direction"
    )

    sweep = None

    if direction in (
        "LONG",
        "SHORT",
    ):
        try:
            sweep = detect_sweep(
                candles_1h,
                price,
                direction,
            )
        except Exception:
            sweep = None

    # ========================================================
    # SECOND PASS
    # Полный pipeline.
    # ========================================================

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
        "candles_1h": candles_1h,
        "candles_15m": candles_15m,
        "candles_5m": candles_5m,
        "candles_1m": candles_1m,
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
# TEXT
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
        (
            f"{direction_icon(direction)} "
            f"<b>1H DIRECTION: "
            f"{direction or 'NEUTRAL'}</b>"
        ),
        "",
        (
            f"{stage_icon(stage)} "
            f"Stage: <b>{stage}</b>"
        ),
        f"⭐ Score: <b>{score}/100</b>",
        "",
        "━━━━━━━━━━━━━━━━━━",
        "💧 <b>MAJOR LIQUIDITY</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]

    # ========================================================
    # BSL
    # ========================================================

    if bsl:
        p = level_price(bsl)

        zone = build_liquidity_zone(
            levels,
            bsl,
            price,
        )

        if zone:
            lines.append(
                f"🔴 <b>BSL ZONE</b> "
                f"${fmt_price(zone['low'])}"
                f" — "
                f"${fmt_price(zone['high'])}"
            )
        else:
            lines.append(
                f"🔴 <b>BSL</b> "
                f"${fmt_price(p)}"
            )

        dist = pct_distance(
            p,
            price,
        )

        lines.append(
            f"   +{dist:.2f}% | "
            f"touches: "
            f"{bsl.get('touches', 0)} | "
            f"strength: "
            f"{bsl.get('strength', 0)}"
        )

    else:
        lines.append(
            "🔴 BSL — нет major liquidity"
        )

    lines.append("")

    # ========================================================
    # SSL
    # ========================================================

    if ssl:
        p = level_price(ssl)

        zone = build_liquidity_zone(
            levels,
            ssl,
            price,
        )

        if zone:
            lines.append(
                f"🟢 <b>SSL ZONE</b> "
                f"${fmt_price(zone['low'])}"
                f" — "
                f"${fmt_price(zone['high'])}"
            )
        else:
            lines.append(
                f"🟢 <b>SSL</b> "
                f"${fmt_price(p)}"
            )

        dist = abs(
            pct_distance(
                p,
                price,
            )
        )

        lines.append(
            f"   -{dist:.2f}% | "
            f"touches: "
            f"{ssl.get('touches', 0)} | "
            f"strength: "
            f"{ssl.get('strength', 0)}"
        )

    else:
        lines.append(
            "🟢 SSL — нет major liquidity"
        )

    # ========================================================
    # SWEEP
    # ========================================================

    if sweep:
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━",
            "🟠 <b>SWEEP</b>",
            "━━━━━━━━━━━━━━━━━━",
        ])

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

        if level is not None:
            lines.append(
                f"💧 Level: "
                f"${fmt_price(level)}"
            )

        if extreme is not None:
            lines.append(
                f"📍 Extreme: "
                f"${fmt_price(extreme)}"
            )

        if stage in (
            "SWEEP",
            "SWEPT",
        ):
            lines.extend([
                "",
                "⏳ <b>ЖДЁМ "
                "15M CONFIRMATION</b>",
                "❌ Вход запрещён.",
            ])

        elif stage in (
            "15M",
            "15M_CONFIRMED",
        ):
            lines.extend([
                "",
                "🟡 <b>15M CONFIRMED</b>",
                "⏳ <b>ЖДЁМ 5M ILM</b>",
                "❌ Вход запрещён.",
            ])

        elif stage == "5M":
            lines.extend([
                "",
                "🟢 <b>5M ILM CONFIRMED</b>",
                "⏳ Ждём финальный trigger",
            ])

    # ========================================================
    # READY
    # ========================================================

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

        if entry is not None:
            lines.append(
                f"🎯 Entry: "
                f"<b>${fmt_price(entry)}</b>"
            )

        if sl is not None:
            lines.append(
                f"🛑 SL: "
                f"<b>${fmt_price(sl)}</b>"
            )

        if tp is not None:
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

        lines.extend([
            "",
            "🟢 <b>МОЖНО ВХОДИТЬ</b>",
        ])

    elif stage in (
        "WAIT",
        "NO_TRADE",
    ):
        reason = result.get(
            "reason"
        )

        if reason:
            lines.extend([
                "",
                "Причина:",
                str(reason),
            ])

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

    elif stage == "5M":
        text = (
            "5M ILM — "
            "ЖДЁМ TRIGGER"
        )

    elif stage == "READY":
        text = "READY"

    elif stage == "NO_TRADE":
        text = "NO TRADE"

    else:
        text = "ОЖИДАНИЕ"

    return (
        f"{icon} <b>{symbol}</b> — "
        f"{text} · "
        f"{direction} · "
        f"{score}/100"
    )


async def scan_coin(
    symbol,
):
    try:
        return (
            symbol,
            await asyncio.to_thread(
                build_analysis,
                COINS[symbol],
            ),
        )

    except Exception as exc:
        traceback.print_exc()

        return (
            symbol,
            {
                "score": 0,
                "direction": "NEUTRAL",
                "stage": "WAIT",
                "reason": (
                    f"Ошибка анализа: "
                    f"{exc}"
                ),
            },
        )


async def build_radar():
    results = await asyncio.gather(
        *[
            scan_coin(symbol)
            for symbol in COINS
        ]
    )

    lines = [
        f"🧠 <b>TRADEMIND {BOT_VERSION}</b>",
        "",
        "📡 Monitor: <b>ONLINE</b>",
        "📊 Market: <b>Binance Spot</b>",
        f"🕐 Scan: <b>{CHECK_INTERVAL}s</b>",
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
            f"🟢 <b>ГОТОВЫХ "
            f"СЕТАПОВ: "
            f"{ready_count}</b>"
        )
    else:
        lines.append(
            "🎯 <b>ГОТОВОГО "
            "СЕТАПА НЕТ</b>"
        )

    lines.extend([
        "",
        "⏳ Pipeline:",
        "1H → Major Liquidity",
        "→ Sweep → 15M",
        "→ 5M ILM → Entry",
        "",
        "❌ Нет полного "
        "подтверждения",
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
            InlineKeyboardButton(
                "📊 График SOL",
                callback_data="chart_SOL",
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
# CHART
# ============================================================

def candle_to_ohlc(
    candle,
):
    if isinstance(
        candle,
        dict,
    ):
        try:
            return {
                "open": float(
                    candle["open"]
                ),
                "high": float(
                    candle["high"]
                ),
                "low": float(
                    candle["low"]
                ),
                "close": float(
                    candle["close"]
                ),
            }
        except Exception:
            return None

    if (
        isinstance(
            candle,
            (list, tuple),
        )
        and len(candle) >= 5
    ):
        try:
            return {
                "open": float(
                    candle[1]
                ),
                "high": float(
                    candle[2]
                ),
                "low": float(
                    candle[3]
                ),
                "close": float(
                    candle[4]
                ),
            }
        except Exception:
            return None

    return None


def create_chart(
    result,
    timeframe="15M",
):
    if timeframe == "1H":
        raw = result.get(
            "candles_1h",
            [],
        )

    elif timeframe == "5M":
        raw = result.get(
            "candles_5m",
            [],
        )

    else:
        raw = result.get(
            "candles_15m",
            [],
        )

    candles = []

    for candle in raw[-80:]:
        parsed = candle_to_ohlc(
            candle
        )

        if parsed:
            candles.append(
                parsed
            )

    if not candles:
        return None

    price = float(
        result.get(
            "price",
            candles[-1]["close"],
        )
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

    stage = get_result_stage(
        result
    )

    direction = (
        result.get(
            "direction"
        )
        or "NEUTRAL"
    )

    sweep = result.get(
        "sweep"
    )

    fig, ax = plt.subplots(
        figsize=(13, 7),
        dpi=140,
    )

    # ========================================================
    # CANDLES
    # ========================================================

    for i, candle in enumerate(
        candles
    ):
        o = candle["open"]
        h = candle["high"]
        l = candle["low"]
        c = candle["close"]

        ax.plot(
            [i, i],
            [l, h],
            linewidth=1,
        )

        bottom = min(
            o,
            c,
        )

        height = abs(
            c - o
        )

        if height == 0:
            height = max(
                (h - l) * 0.02,
                price * 0.00005,
            )

        face = (
            "white"
            if c >= o
            else "black"
        )

        rect = Rectangle(
            (
                i - 0.32,
                bottom,
            ),
            0.64,
            height,
            facecolor=face,
            edgecolor="black",
            linewidth=0.8,
        )

        ax.add_patch(
            rect
        )

    # ========================================================
    # LIQUIDITY
    # ========================================================

    def draw_liquidity(
        level,
        label,
    ):
        if not level:
            return

        p = level_price(
            level
        )

        if p is None:
            return

        zone = build_liquidity_zone(
            levels,
            level,
            price,
        )

        if zone:
            ax.axhspan(
                zone["low"],
                zone["high"],
                alpha=0.10,
            )

            ax.axhline(
                zone["center"],
                linestyle="--",
                linewidth=1.3,
            )

            ax.text(
                len(candles) - 1,
                zone["center"],
                (
                    f" {label} "
                    f"${fmt_price(zone['center'])}"
                ),
                va="center",
                fontsize=9,
            )

        else:
            ax.axhline(
                p,
                linestyle="--",
                linewidth=1.2,
            )

            ax.text(
                len(candles) - 1,
                p,
                (
                    f" {label} "
                    f"${fmt_price(p)}"
                ),
                va="center",
                fontsize=9,
            )

    draw_liquidity(
        bsl,
        "BSL",
    )

    draw_liquidity(
        ssl,
        "SSL",
    )

    # ========================================================
    # CURRENT PRICE
    # ========================================================

    ax.axhline(
        price,
        linewidth=1.5,
    )

    ax.text(
        len(candles) - 1,
        price,
        (
            f" NOW "
            f"${fmt_price(price)}"
        ),
        va="bottom",
        fontsize=10,
        fontweight="bold",
    )

    # ========================================================
    # SWEEP
    # ========================================================

    if sweep:
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

        if sweep_level is not None:
            try:
                sweep_level = float(
                    sweep_level
                )

                ax.axhline(
                    sweep_level,
                    linestyle=":",
                    linewidth=2,
                )

                ax.text(
                    1,
                    sweep_level,
                    " SWEEP",
                    va="bottom",
                    fontsize=10,
                    fontweight="bold",
                )

            except Exception:
                pass

        if extreme is not None:
            try:
                extreme = float(
                    extreme
                )

                ax.axhline(
                    extreme,
                    linestyle=":",
                    linewidth=1,
                )

                ax.text(
                    1,
                    extreme,
                    " EXTREME",
                    va="bottom",
                    fontsize=9,
                )

            except Exception:
                pass

    # ========================================================
    # ENTRY / SL / TP
    # ========================================================

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

    def draw_trade_line(
        value,
        label,
    ):
        if value is None:
            return

        try:
            value = float(value)

            ax.axhline(
                value,
                linestyle="-.",
                linewidth=1.5,
            )

            ax.text(
                2,
                value,
                f" {label}",
                va="bottom",
                fontsize=9,
                fontweight="bold",
            )

        except Exception:
            pass

    draw_trade_line(
        entry,
        "ENTRY",
    )

    draw_trade_line(
        sl,
        "SL",
    )

    draw_trade_line(
        tp,
        "TP",
    )

    # ========================================================
    # WAITING STAGE
    # ========================================================

    if stage in (
        "SWEEP",
        "SWEPT",
    ):
        waiting = (
            "🟠 ЖДЁМ 15M CONFIRMATION"
        )

    elif stage in (
        "15M",
        "15M_CONFIRMED",
    ):
        waiting = (
            "🟡 15M CONFIRMED "
            "→ ЖДЁМ 5M ILM"
        )

    elif stage == "5M":
        waiting = (
            "🟢 5M ILM "
            "→ ЖДЁМ TRIGGER"
        )

    elif stage == "READY":
        waiting = (
            "🟢 READY — "
            "МОЖНО ВХОДИТЬ"
        )

    elif stage == "NO_TRADE":
        waiting = (
            "❌ NO TRADE"
        )

    else:
        if direction == "SHORT":
            waiting = (
                "🔴 ЖДЁМ SHORT SWEEP"
            )

        elif direction == "LONG":
            waiting = (
                "🟢 ЖДЁМ LONG SWEEP"
            )

        else:
            waiting = (
                "⚪ ЖДЁМ 1H DIRECTION"
            )

    ax.text(
        0.01,
        0.97,
        waiting,
        transform=ax.transAxes,
        fontsize=12,
        fontweight="bold",
        va="top",
    )

    # ========================================================
    # TITLE
    # ========================================================

    symbol = result.get(
        "symbol",
        "SOL",
    )

    ax.set_title(
        (
            f"TradeMind "
            f"{BOT_VERSION} — "
            f"{symbol} — "
            f"{timeframe}\n"
            f"1H: {direction} | "
            f"Stage: {stage}"
        ),
        fontsize=14,
        fontweight="bold",
    )

    ax.set_ylabel(
        "Price"
    )

    ax.set_xlabel(
        "Candles"
    )

    ax.grid(
        alpha=0.18
    )

    ax.set_xlim(
        -2,
        len(candles) + 2,
    )

    # ========================================================
    # RANGE
    # ========================================================

    all_prices = []

    for candle in candles:
        all_prices.extend([
            candle["high"],
            candle["low"],
        ])

    for value in (
        level_price(bsl)
        if bsl
        else None,
        level_price(ssl)
        if ssl
        else None,
        entry,
        sl,
        tp,
    ):
        if value is not None:
            try:
                all_prices.append(
                    float(value)
                )
            except Exception:
                pass

    if all_prices:
        low = min(
            all_prices
        )

        high = max(
            all_prices
        )

        margin = max(
            (high - low) * 0.08,
            price * 0.002,
        )

        ax.set_ylim(
            low - margin,
            high + margin,
        )

    fig.tight_layout()

    output = io.BytesIO()

    fig.savefig(
        output,
        format="png",
        bbox_inches="tight",
    )

    plt.close(
        fig
    )

    output.seek(0)

    return output


# ============================================================
# DASHBOARD
# ============================================================

async def build_dashboard_message():
    return await build_radar()


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
        if (
            "Message is not modified"
            in str(exc)
        ):
            return False

        raise


# ============================================================
# COMMANDS
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = (
        update.effective_chat.id
    )

    add_subscriber(
        chat_id
    )

    text = await build_dashboard_message()

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


async def subscribe(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = (
        update.effective_chat.id
    )

    add_subscriber(
        chat_id
    )

    await update.message.reply_text(
        "📡 <b>TradeMind monitor включён.</b>",
        parse_mode=ParseMode.HTML,
    )


async def unsubscribe(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = (
        update.effective_chat.id
    )

    remove_subscriber(
        chat_id
    )

    await update.message.reply_text(
        "🔕 Мониторинг выключен."
    )


async def radar_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    text = await build_dashboard_message()

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


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
                    "📈 График 15M",
                    callback_data="chart_SOL",
                )
            ],
            [
                InlineKeyboardButton(
                    "📊 График 1H",
                    callback_data="chart1h_SOL",
                ),
                InlineKeyboardButton(
                    "⚡ График 5M",
                    callback_data="chart5m_SOL",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔄 Обновить",
                    callback_data="sol",
                ),
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
            (
                f"❌ Ошибка SOL:\n"
                f"<code>{exc}</code>"
            ),
            parse_mode=ParseMode.HTML,
        )


async def chart_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    try:
        result = await asyncio.to_thread(
            build_analysis,
            COINS["SOL"],
        )

        chart = await asyncio.to_thread(
            create_chart,
            result,
            "15M",
        )

        if chart is None:
            await update.message.reply_text(
                "❌ Не удалось построить график."
            )
            return

        stage = get_result_stage(
            result
        )

        if stage in (
            "SWEEP",
            "SWEPT",
        ):
            caption = (
                "🟠 <b>SOL — SWEEP</b>\n"
                "⏳ Ждём 15M confirmation"
            )

        elif stage in (
            "15M",
            "15M_CONFIRMED",
        ):
            caption = (
                "🟡 <b>SOL — 15M CONFIRMED</b>\n"
                "⏳ Ждём 5M ILM"
            )

        elif stage == "5M":
            caption = (
                "🟢 <b>SOL — 5M ILM</b>\n"
                "⏳ Ждём trigger"
            )

        elif stage == "READY":
            caption = (
                "🟢 <b>SOL — READY</b>\n"
                "Entry / SL / TP показаны"
            )

        else:
            caption = (
                "⏳ <b>SOL — WAIT</b>\n"
                "График актуальный"
            )

        await update.message.reply_photo(
            photo=chart,
            caption=caption,
            parse_mode=ParseMode.HTML,
        )

    except Exception as exc:
        traceback.print_exc()

        await update.message.reply_text(
            (
                f"❌ Ошибка графика:\n"
                f"<code>{exc}</code>"
            ),
            parse_mode=ParseMode.HTML,
        )


# ============================================================
# CALLBACKS
# ============================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    data = query.data or ""

    # ========================================================
    # RADAR
    # ========================================================

    if data in (
        "radar",
        "refresh",
    ):
        text = await build_dashboard_message()

        await safe_edit_message(
            query,
            text,
            main_keyboard(),
        )

        return

    # ========================================================
    # SOL
    # ========================================================

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
                    "📈 График 15M",
                    callback_data="chart_SOL",
                )
            ],
            [
                InlineKeyboardButton(
                    "📊 График 1H",
                    callback_data="chart1h_SOL",
                ),
                InlineKeyboardButton(
                    "⚡ График 5M",
                    callback_data="chart5m_SOL",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔄 Обновить",
                    callback_data="sol",
                ),
            ],
        ])

        await safe_edit_message(
            query,
            text,
            keyboard,
        )

        return

    # ========================================================
    # CHART 15M
    # ========================================================

    if data == "chart_SOL":
        result = await asyncio.to_thread(
            build_analysis,
            COINS["SOL"],
        )

        chart = await asyncio.to_thread(
            create_chart,
            result,
            "15M",
        )

        if chart is None:
            await query.message.reply_text(
                "❌ Нет данных для графика."
            )
            return

        stage = get_result_stage(
            result
        )

        if stage in (
            "SWEEP",
            "SWEPT",
        ):
            caption = (
                "🟠 <b>SOL — SWEEP СНЯТ</b>\n"
                "⏳ ЖДЁМ 15M CONFIRMATION"
            )

        elif stage in (
            "15M",
            "15M_CONFIRMED",
        ):
            caption = (
                "🟡 <b>SOL — 15M CONFIRMED</b>\n"
                "⏳ ЖДЁМ 5M ILM"
            )

        elif stage == "5M":
            caption = (
                "🟢 <b>SOL — 5M ILM</b>\n"
                "⏳ ЖДЁМ TRIGGER"
            )

        elif stage == "READY":
            caption = (
                "🟢 <b>SOL — READY</b>\n"
                "ENTRY / SL / TP показаны"
            )

        else:
            direction = (
                result.get(
                    "direction"
                )
                or "NEUTRAL"
            )

            caption = (
                f"⏳ <b>SOL — WAIT</b>\n"
                f"1H: {direction}"
            )

        await query.message.reply_photo(
            photo=chart,
            caption=caption,
            parse_mode=ParseMode.HTML,
        )

        return

    # ========================================================
    # CHART 1H
    # ========================================================

    if data == "chart1h_SOL":
        result = await asyncio.to_thread(
            build_analysis,
            COINS["SOL"],
        )

        chart = await asyncio.to_thread(
            create_chart,
            result,
            "1H",
        )

        if chart:
            await query.message.reply_photo(
                photo=chart,
                caption=(
                    "📈 <b>SOL — 1H</b>\n"
                    "💧 Major Liquidity + "
                    "1H structure"
                ),
                parse_mode=ParseMode.HTML,
            )

        return

    # ========================================================
    # CHART 5M
    # ========================================================

    if data == "chart5m_SOL":
        result = await asyncio.to_thread(
            build_analysis,
            COINS["SOL"],
        )

        chart = await asyncio.to_thread(
            create_chart,
            result,
            "5M",
        )

        if chart:
            await query.message.reply_photo(
                photo=chart,
                caption=(
                    "⚡ <b>SOL — 5M</b>\n"
                    "ILM / trigger / stage"
                ),
                parse_mode=ParseMode.HTML,
            )

        return

    # ========================================================
    # ACTIVE
    # ========================================================

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
                traceback.print_exc()

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
                result["symbol"] = symbol

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

    # ========================================================
    # LIQUIDITY
    # ========================================================

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
                zone = build_liquidity_zone(
                    levels,
                    bsl,
                    price,
                )

                if zone:
                    lines.append(
                        "🔴 BSL ZONE: "
                        f"${fmt_price(zone['low'])}"
                        " — "
                        f"${fmt_price(zone['high'])}"
                    )

                    lines.append(
                        "🔥 Strength: "
                        f"{zone['strength']:.0f}/100"
                    )

            else:
                lines.append(
                    "🔴 BSL: нет"
                )

            lines.append("")

            if ssl:
                zone = build_liquidity_zone(
                    levels,
                    ssl,
                    price,
                )

                if zone:
                    lines.append(
                        "🟢 SSL ZONE: "
                        f"${fmt_price(zone['low'])}"
                        " — "
                        f"${fmt_price(zone['high'])}"
                    )

                    lines.append(
                        "🔥 Strength: "
                        f"{zone['strength']:.0f}/100"
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
                (
                    f"❌ Liquidity error: "
                    f"{exc}"
                ),
                main_keyboard(),
            )

        return

    # ========================================================
    # JOURNAL
    # ========================================================

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

    # ========================================================
    # STATUS
    # ========================================================

    if data == "status":
        text = (
            f"🧠 <b>TRADEMIND "
            f"{BOT_VERSION}</b>\n\n"
            f"📡 Monitor: ONLINE\n"
            f"📊 Market: Binance Spot\n"
            f"🕐 Scan: "
            f"{CHECK_INTERVAL}s\n"
            f"🧵 Workers: "
            f"{SCAN_WORKERS}\n"
            f"⭐ READY score: "
            f"{MIN_SCORE_READY}+\n\n"
            f"📐 Strategy: "
            f"{STRATEGY_VERSION}\n\n"
            f"1H → Major Liquidity\n"
            f"→ Sweep → 15M\n"
            f"→ 5M ILM → Entry\n\n"
            f"❌ D1: REMOVED\n"
            f"❌ W1: REMOVED\n"
            f"💧 Hybrid liquidity: ON\n"
            f"📈 Telegram charts: ON\n"
            f"🔢 Daily trade limit: OFF"
        )

        await safe_edit_message(
            query,
            text,
            main_keyboard(),
        )

        return


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
    confirmation = (
        result.get(
            "confirmation_15m"
        )
    )

    return (
        f"{symbol}:"
        f"{result.get('direction')}:"
        f"{confirmation}"
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
# MONITOR SEND
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
        f"⏳ <b>ЖДЁМ "
        f"15M CONFIRMATION</b>\n"
        f"❌ Вход пока запрещён."
    )

    await send_monitor_message(
        application,
        text,
    )


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
        f"🎯 Entry: "
        f"${fmt_price(entry)}\n"
        f"🛑 SL: "
        f"${fmt_price(sl)}\n"
        f"💰 TP: "
        f"${fmt_price(tp)}\n"
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
    try:
        result = await asyncio.to_thread(
            build_analysis,
            COINS[symbol],
        )

        stage = get_result_stage(
            result
        )

        # ====================================================
        # SWEEP
        # ====================================================

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
                and STATE[
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

        # ====================================================
        # 15M
        # ====================================================

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
                and STATE[
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

        # ====================================================
        # READY
        # ====================================================

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
                and STATE[
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

    async def worker(
        symbol,
    ):
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

        except asyncio.CancelledError:
            raise

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
        and not task.done()
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
        .post_init(
            post_init
        )
        .post_shutdown(
            post_shutdown
        )
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
        CommandHandler(
            "chart",
            chart_command,
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


if __name__ == "__main__":
    main()