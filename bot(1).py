"""
TradeMind 6.1.1 — Telegram Bot
Binance Spot
BingX optional / OFF by default

Strategy:
D1/W1 -> 1H -> Major Liquidity -> Sweep
-> 15M -> 5M ILM -> Entry -> SL -> next major liquidity

Rules:
- D1 direction mandatory, W1 fallback
- 1H must sync with direction
- Only major liquidity
- Sweep -> 15M confirmation -> 5M ILM
- One TP only
- TP = next major unswept liquidity
- RR >= 1:2
- No artificial TP
- No daily trade limit
- Duplicate alerts blocked
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

from telegram.error import BadRequest

from market import (
    get_market_data,
    find_major_liquidity,
    detect_sweep,
)

from strategy import (
    analyze,
    STRATEGY_VERSION,
)


# =========================================================
# OPTIONAL BINGX
# =========================================================

try:
    import bingx

    BINGX_AVAILABLE = True

except Exception:
    bingx = None
    BINGX_AVAILABLE = False


# =========================================================
# CONFIG
# =========================================================

TOKEN = os.getenv("BOT_TOKEN")

CHECK_INTERVAL = 15
SCAN_WORKERS = 9
MIN_SCORE_READY = 80


# =========================================================
# COINS
# =========================================================

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


# =========================================================
# FILES
# =========================================================

SUBSCRIBERS_FILE = "subscribers.json"
STATE_FILE = "monitor_state.json"
JOURNAL_FILE = "trade_journal.json"
RESULTS_CACHE_FILE = "trademind_results_cache.json"


# =========================================================
# JSON
# =========================================================

def load_json(filename, default):
    try:
        if not os.path.exists(filename):
            return default

        with open(filename, "r", encoding="utf-8") as file:
            return json.load(file)

    except Exception as exc:
        print(f"JSON LOAD ERROR {filename}: {exc}")
        return default


def save_json(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as file:
            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
            )

    except Exception as exc:
        print(f"JSON SAVE ERROR {filename}: {exc}")


# =========================================================
# CACHE
# =========================================================

def load_results_cache():
    return load_json(
        RESULTS_CACHE_FILE,
        {
            "updated_at": None,
            "results": {},
        },
    )


def save_results_cache(results):
    save_json(
        RESULTS_CACHE_FILE,
        {
            "updated_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "results": results,
        },
    )


def get_cached_results():
    cache = load_results_cache()

    results = cache.get("results", {})
    updated_at = cache.get("updated_at")

    if not isinstance(results, dict):
        results = {}

    return results, updated_at


def cache_age_text(updated_at):
    if not updated_at:
        return "нет данных"

    try:
        dt = datetime.fromisoformat(updated_at)
        now = datetime.now(timezone.utc)

        seconds = max(
            0,
            int((now - dt).total_seconds()),
        )

        if seconds < 60:
            return f"{seconds} сек. назад"

        minutes = seconds // 60

        if minutes < 60:
            return f"{minutes} мин. назад"

        hours = minutes // 60

        return f"{hours} ч. назад"

    except Exception:
        return "неизвестно"


# =========================================================
# STATE
# =========================================================

def default_state():
    return {
        "last_signal_key": None,
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

    state.setdefault("last_signal_key", None)
    state.setdefault("last_sweep_keys", {})
    state.setdefault("last_15m_keys", {})
    state.setdefault("last_ready_keys", {})
    state.setdefault("subscribers", [])

    # Удаляем старые ограничения.
    state.pop("daily_date", None)
    state.pop("daily_trades", None)
    state.pop("daily_stop", None)

    return state


def save_state(state):
    save_json(
        STATE_FILE,
        state,
    )


# =========================================================
# SUBSCRIBERS
# =========================================================

def load_subscribers():
    data = load_json(
        SUBSCRIBERS_FILE,
        [],
    )

    return data if isinstance(data, list) else []


def save_subscribers(subscribers):
    save_json(
        SUBSCRIBERS_FILE,
        subscribers,
    )


# =========================================================
# FORMAT
# =========================================================

def format_price(value):
    if value is None:
        return "N/A"

    try:
        value = float(value)
    except Exception:
        return "N/A"

    if value >= 1000:
        return f"${value:,.2f}"

    if value >= 1:
        return f"${value:,.4f}"

    return f"${value:,.6f}"


def format_rr(value):
    if value is None:
        return "N/A"

    try:
        return f"1:{float(value):.2f}"
    except Exception:
        return "N/A"


def stage_icon(stage):
    return {
        "READY": "🟢",
        "5M": "🔵",
        "15M": "🟡",
        "SWEEP": "🟠",
        "SWEPT": "🟠",
        "1H": "🟣",
        "D1": "⚪",
        "TP": "🔴",
        "WAIT": "⏳",
        "NO_TRADE": "⛔",
    }.get(stage, "⚪")


def stage_text(stage):
    return {
        "READY": "МОЖНО ВХОДИТЬ",
        "5M": "ЖДЁМ 5M ILM",
        "15M": "ЖДЁМ 15M",
        "SWEEP": "ЖДЁМ SWEEP",
        "SWEPT": "SWEEP СНЯТ — ЖДЁМ 15M",
        "1H": "1H НЕ СИНХРОНИЗИРОВАН",
        "D1": "D1/W1 НЕЯСНЫЙ",
        "TP": "RR / TP НЕ ПРОХОДИТ",
        "WAIT": "ОЖИДАНИЕ",
        "NO_TRADE": "НЕТ СДЕЛКИ",
    }.get(stage, "ОЖИДАНИЕ")


# =========================================================
# STAGE
# =========================================================

def get_result_stage(result):

    if not result:
        return "NO_TRADE"

    if result.get("error"):
        return "NO_TRADE"

    stage = result.get("stage")

    if stage:
        return stage

    if (
        result.get("entry") is not None
        and result.get("sl") is not None
        and result.get("tp") is not None
    ):
        try:
            rr = float(result.get("rr", 0))

            if rr >= 2:
                return "READY"

        except Exception:
            pass

    if (
        result.get("confirmation_5m")
        or result.get("trigger_5m")
        or result.get("confirmation")
    ):
        return "5M"

    if result.get("confirmation_15m"):
        return "15M"

    if result.get("sweep"):
        return "SWEEP"

    if result.get("direction"):
        return "1H"

    return "WAIT"


# =========================================================
# LIQUIDITY
# =========================================================

def _level_price(level):
    try:
        return float(
            level.get(
                "price",
                level.get("level"),
            )
        )
    except Exception:
        return None


def format_levels(levels, price):

    if not levels:
        return "💧 Крупная ликвидность не найдена."

    try:
        current = float(price)
    except Exception:
        current = 0.0

    above = []
    below = []

    for level in levels:

        lp = _level_price(level)

        if lp is None:
            continue

        position = str(
            level.get(
                "position",
                "",
            )
        ).upper()

        side = str(
            level.get(
                "side",
                "",
            )
        ).upper()

        # -------------------------------------------------
        # Position имеет приоритет.
        # -------------------------------------------------

        if position == "ABOVE":
            above.append((lp, level))
            continue

        if position == "BELOW":
            below.append((lp, level))
            continue

        # -------------------------------------------------
        # Совместимость со старой market.py.
        # -------------------------------------------------

        if current and lp > current:
            above.append((lp, level))

        elif current and lp < current:
            below.append((lp, level))

        elif side == "SHORT":
            above.append((lp, level))

        elif side == "LONG":
            below.append((lp, level))

    above.sort(
        key=lambda x: x[0]
    )

    below.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    lines = []

    # =====================================================
    # BSL
    # =====================================================

    lines.append(
        "🔴 <b>BSL — ЛИКВИДНОСТЬ СВЕРХУ</b>"
    )

    if above:

        for lp, level in above[:6]:

            touches = level.get(
                "touches",
                1,
            )

            strength = level.get(
                "strength",
                0,
            )

            distance = (
                abs(lp - current)
                / current
                * 100
                if current
                else 0
            )

            lines.append(
                f"🔴 <b>{format_price(lp)}</b> "
                f"— +{distance:.2f}%"
            )

            lines.append(
                f"   touches: {touches} | "
                f"strength: {strength}"
            )

    else:

        lines.append(
            "   — нет свежего major BSL"
        )

    lines.append("")

    # =====================================================
    # SSL
    # =====================================================

    lines.append(
        "🟢 <b>SSL — ЛИКВИДНОСТЬ СНИЗУ</b>"
    )

    if below:

        for lp, level in below[:6]:

            touches = level.get(
                "touches",
                1,
            )

            strength = level.get(
                "strength",
                0,
            )

            distance = (
                abs(lp - current)
                / current
                * 100
                if current
                else 0
            )

            lines.append(
                f"🟢 <b>{format_price(lp)}</b> "
                f"— -{distance:.2f}%"
            )

            lines.append(
                f"   touches: {touches} | "
                f"strength: {strength}"
            )

    else:

        lines.append(
            "   — нет свежего major SSL"
        )

    return "\n".join(lines)


# =========================================================
# ANALYSIS
# =========================================================

def build_analysis(symbol):

    data = get_market_data(symbol)

    if not data:
        raise Exception(
            f"Нет данных для {symbol}"
        )

    price = data["price"]

    candles_d1 = data["candles_d1"]
    candles_w1 = data["candles_w1"]
    candles_1h = data["candles_1h"]
    candles_15m = data["candles_15m"]
    candles_5m = data["candles_5m"]

    # =====================================================
    # MAJOR LIQUIDITY
    # =====================================================

    major_levels = find_major_liquidity(
        candles_1h,
        price,
        max_levels=20,
        include_swept=True,
    )

    # =====================================================
    # FIRST PASS
    # =====================================================

    context = analyze(

        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,

        current_price=price,

        major_levels=major_levels,

        sweep=None,

        candles_d1=candles_d1,
        candles_w1=candles_w1,

    )

    direction = context.get(
        "direction"
    )

    # =====================================================
    # SWEEP
    # =====================================================

    sweep = None

    if direction in {
        "LONG",
        "SHORT",
    }:

        sweep = detect_sweep(
            candles_1h,
            price,
            direction,
        )

    # =====================================================
    # SECOND PASS
    # =====================================================

    result = analyze(

        candles_1h=candles_1h,
        candles_15m=candles_15m,
        candles_5m=candles_5m,

        current_price=price,

        major_levels=major_levels,

        sweep=sweep,

        candles_d1=candles_d1,
        candles_w1=candles_w1,

    )

    # =====================================================
    # COMPATIBILITY
    # =====================================================

    if (
        result.get("confirmation_5m")
        and not result.get("confirmation")
    ):

        result["confirmation"] = (
            result["confirmation_5m"]
        )

    if (
        result.get("trigger_5m")
        and not result.get("confirmation")
    ):

        result["confirmation"] = (
            result["trigger_5m"]
        )

    # =====================================================
    # ATTACH
    # =====================================================

    result.update({

        "symbol": symbol,

        "price": price,

        "major_levels": major_levels,

        "sweep": sweep,

        "candles_5m": candles_5m,

        "candles_15m": candles_15m,

        "candles_1h": candles_1h,

        "candles_d1": candles_d1,

        "candles_w1": candles_w1,

        "strategy_version":
            STRATEGY_VERSION,

    })

    return result


# =========================================================
# SCANNING
# =========================================================

def scan_one_coin(
    coin,
    symbol,
):

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
            for coin, symbol
            in COINS.items()
        }

        for future in as_completed(
            futures
        ):

            coin = futures[future]

            try:

                c, result = (
                    future.result()
                )

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

    return await asyncio.to_thread(
        scan_all_coins
    )


# =========================================================
# READY
# =========================================================

def find_ready_setups(results):

    ready = []

    for coin, result in results.items():

        if result.get("error"):
            continue

        stage = get_result_stage(
            result
        )

        if stage != "READY":
            continue

        try:

            score = float(
                result.get(
                    "score",
                    0,
                )
            )

        except Exception:

            score = 0

        if score < MIN_SCORE_READY:
            continue

        ready.append(
            (
                score,
                coin,
                result,
            )
        )

    ready.sort(
        key=lambda x: x[0],
        reverse=True,
    )

    return ready


def find_first_ready(results):

    ready = find_ready_setups(
        results
    )

    return ready[0] if ready else None


# =========================================================
# SAFE EDIT
# =========================================================

async def safe_edit_message(
    query,
    text,
    reply_markup=None,
):

    try:

        await query.edit_message_text(
            text=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
        )

        return True

    except BadRequest as exc:

        if "Message is not modified" in str(exc):
            return False

        raise


# =========================================================
# KEYBOARD
# =========================================================

def dashboard_keyboard():

    return InlineKeyboardMarkup([

        [
            InlineKeyboardButton(
                "🔄 Обновить",
                callback_data="refresh",
            ),
            InlineKeyboardButton(
                "🎯 Активный сетап",
                callback_data="active",
            ),
        ],

        [
            InlineKeyboardButton(
                "📡 Радар",
                callback_data="radar",
            ),
            InlineKeyboardButton(
                "❓ Почему WAIT?",
                callback_data="why",
            ),
        ],

        [
            InlineKeyboardButton(
                "💧 Ликвидность",
                callback_data="levels",
            ),
            InlineKeyboardButton(
                "📒 Журнал",
                callback_data="journal",
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

    ])


# =========================================================
# DASHBOARD
# =========================================================

def build_dashboard_message():

    results, updated_at = (
        get_cached_results()
    )

    if not results:

        return (
            "🧠 <b>TRADEMIND 6.1.1</b>\n\n"
            "📡 Monitor: <b>ONLINE</b>\n"
            "📊 Market: <b>Binance Spot</b>\n\n"
            "⏳ Данные ещё собираются.\n\n"
            "Нажми 🔄 <b>Обновить</b>."
        )

    lines = [

        "🧠 <b>TRADEMIND 6.1.1</b>",

        "",

        "📡 Monitor: <b>ONLINE</b>",

        "📊 Market: <b>Binance Spot</b>",

        f"🕐 Обновлено: "
        f"<b>{cache_age_text(updated_at)}</b>",

        "",

        "━━━━━━━━━━━━━━━━━━",

        "📡 <b>TRADE RADAR</b>",

        "━━━━━━━━━━━━━━━━━━",

    ]

    best = None

    for coin in COINS:

        result = results.get(
            coin,
            {},
        )

        if result.get("error"):

            lines.append(
                f"❌ <b>{coin}</b> — ERROR"
            )

            continue

        stage = get_result_stage(
            result
        )

        score = result.get(
            "score",
            0,
        )

        direction = result.get(
            "direction"
        )

        direction_text = (
            f" · {direction}"
            if direction
            else ""
        )

        lines.append(
            f"{stage_icon(stage)} "
            f"<b>{coin}</b> — "
            f"{stage_text(stage)}"
            f"{direction_text} · "
            f"{score}/100"
        )

        if stage == "READY":

            try:
                score_num = float(score)
            except Exception:
                score_num = 0

            if (
                best is None
                or score_num > best[0]
            ):

                best = (
                    score_num,
                    coin,
                    result,
                )

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━",
    ]

    if best:

        score, coin, result = best

        lines += [

            "🎯 <b>BEST SETUP</b>",

            "",

            f"💠 <b>{coin}</b>",

            f"📐 Direction: "
            f"<b>{result.get('direction')}</b>",

            f"⭐ Score: "
            f"<b>{score:.0f}/100</b>",

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

        ]

    else:

        lines += [

            "🎯 <b>ГОТОВОГО СЕТАПА НЕТ</b>",

            "",

            "⏳ Ждём:",

            "D1/W1 → 1H → Major Liquidity",

            "→ Sweep → 15M → 5M ILM",

            "",

            "❌ Нет полного подтверждения",

            "→ <b>нет входа.</b>",

        ]

    return "\n".join(lines)


# =========================================================
# ACTIVE SETUP
# =========================================================

def build_active_setup_message():

    results, updated_at = (
        get_cached_results()
    )

    if not results:

        return (
            "🎯 <b>АКТИВНЫЙ СЕТАП</b>\n\n"
            "⏳ Данных пока нет."
        )

    candidates = []

    stage_priority = {
        "READY": 6,
        "5M": 5,
        "15M": 4,
        "SWEEP": 3,
        "SWEPT": 3,
        "1H": 2,
        "D1": 1,
        "WAIT": 0,
        "NO_TRADE": 0,
    }

    for coin, result in results.items():

        if result.get("error"):
            continue

        stage = get_result_stage(
            result
        )

        try:

            score = float(
                result.get(
                    "score",
                    0,
                )
            )

        except Exception:

            score = 0

        candidates.append(
            (
                stage_priority.get(
                    stage,
                    0,
                ),
                score,
                coin,
                result,
            )
        )

    if not candidates:

        return (
            "🎯 <b>АКТИВНЫЙ СЕТАП</b>\n\n"
            "⏳ Активных сетапов нет."
        )

    candidates.sort(
        reverse=True
    )

    _, _, coin, result = (
        candidates[0]
    )

    stage = get_result_stage(
        result
    )

    direction = (
        result.get("direction")
        or "—"
    )

    lines = [

        f"🎯 <b>АКТИВНЫЙ СЕТАП — {coin}</b>",

        "",

        f"📐 Direction: "
        f"<b>{direction}</b>",

        f"⭐ Score: "
        f"<b>{result.get('score', 0)}/100</b>",

        f"📡 Данные: "
        f"<b>{cache_age_text(updated_at)}</b>",

        "",

        "━━━━━━━━━━━━━━━━━━",

        "📍 <b>SETUP PROGRESS</b>",

        "━━━━━━━━━━━━━━━━━━",

    ]

    progress = [

        (
            "D1 / W1",
            stage in {
                "1H",
                "SWEEP",
                "SWEPT",
                "15M",
                "5M",
                "READY",
            },
        ),

        (
            "1H",
            stage in {
                "SWEEP",
                "SWEPT",
                "15M",
                "5M",
                "READY",
            },
        ),

        (
            "💧 Major Sweep",
            stage in {
                "SWEPT",
                "15M",
                "5M",
                "READY",
            },
        ),

        (
            "15M",
            stage in {
                "5M",
                "READY",
            },
        ),

        (
            "5M ILM",
            stage == "READY",
        ),

        (
            "ENTRY",
            stage == "READY",
        ),

    ]

    for name, completed in progress:

        icon = (
            "✅"
            if completed
            else "⏳"
        )

        lines.append(
            f"{icon} {name}"
        )

    lines += [

        "",

        f"📌 Текущий этап: "
        f"<b>{stage_text(stage)}</b>",

    ]

    if stage == "READY":

        lines += [

            "",

            "━━━━━━━━━━━━━━━━━━",

            "🎯 <b>ENTRY</b>",

            "━━━━━━━━━━━━━━━━━━",

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

        ]

    else:

        lines += [

            "",

            "❌ <b>ВХОД ЗАПРЕЩЁН</b>",

            "Ждём следующее подтверждение.",

        ]

    return "\n".join(lines)


# =========================================================
# WHY WAIT
# =========================================================

def build_why_wait_message():

    results, updated_at = (
        get_cached_results()
    )

    if not results:

        return (
            "❓ <b>ПОЧЕМУ WAIT?</b>\n\n"
            "⏳ Нет данных."
        )

    lines = [

        "❓ <b>ПОЧЕМУ WAIT?</b>",

        "",

        f"📡 Данные: "
        f"<b>{cache_age_text(updated_at)}</b>",

        "",

    ]

    reasons = {

        "D1":
            "D1/W1 не дают чёткого направления.",

        "1H":
            "1H не синхронизирован с направлением.",

        "SWEEP":
            "Major liquidity найдена. Ждём sweep.",

        "SWEPT":
            "Sweep снял major liquidity. Ждём 15M.",

        "15M":
            "Sweep есть. Ждём 15M confirmation.",

        "5M":
            "15M подтверждён. Ждём 5M ILM.",

        "TP":
            "Следующая liquidity не даёт RR ≥ 1:2.",

        "WAIT":
            "Нет полного подтверждённого сетапа.",

        "NO_TRADE":
            "Сделка не соответствует правилам.",

    }

    for coin in COINS:

        result = results.get(
            coin,
            {},
        )

        if result.get("error"):
            continue

        stage = get_result_stage(
            result
        )

        if stage == "READY":

            lines += [

                f"🟢 <b>{coin}</b> — READY",

                "Все обязательные условия выполнены.",

                "",

            ]

            continue

        reason = (
            result.get("reason")
            or reasons.get(
                stage,
                "Нет полного подтверждения.",
            )
        )

        lines += [

            f"{stage_icon(stage)} "
            f"<b>{coin}</b>",

            f"Этап: "
            f"<b>{stage_text(stage)}</b>",

            f"Причина: {reason}",

            "",

        ]

    lines += [

        "━━━━━━━━━━━━━━━━━━",

        "🧠 <b>TradeMind правила</b>",

        "",

        "D1/W1 → 1H → liquidity → sweep",

        "→ 15M → 5M ILM → Entry.",

        "",

        "Нет подтверждения → нет входа.",

        "Не входим в середине движения.",

        "Только крупная ликвидность.",

        "TP = следующая unswept liquidity.",

        "RR должен быть ≥ 1:2.",

        "Один TP.",

    ]

    return "\n".join(lines)


# =========================================================
# MARKET
# =========================================================

def build_market_message(results):

    lines = [

        "📊 <b>TRADEMIND 6.1.1 — РЫНОК</b>",

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

            ]

            continue

        price = result.get("price")

        stage = get_result_stage(
            result
        )

        score = result.get(
            "score",
            0,
        )

        direction = result.get(
            "direction"
        )

        lines += [

            f"💠 <b>{coin}</b> "
            f"{format_price(price)}",

            f"{stage_icon(stage)} "
            f"{stage_text(stage)} "
            f"— {score}/100",

        ]

        if direction:

            lines.append(
                f"📐 Direction: "
                f"<b>{direction}</b>"
            )

        levels = result.get(
            "major_levels",
            [],
        )

        if levels:

            try:

                current_price = float(
                    price
                )

                above = []
                below = []

                for level in levels:

                    lp = _level_price(
                        level
                    )

                    if lp is None:
                        continue

                    position = str(
                        level.get(
                            "position",
                            "",
                        )
                    ).upper()

                    if position == "ABOVE":
                        above.append(lp)

                    elif position == "BELOW":
                        below.append(lp)

                    elif lp > current_price:
                        above.append(lp)

                    elif lp < current_price:
                        below.append(lp)

                if above:

                    above.sort()

                    lines.append(
                        "🔴 BSL: "
                        f"<b>{format_price(above[0])}</b>"
                    )

                if below:

                    below.sort(
                        reverse=True
                    )

                    lines.append(
                        "🟢 SSL: "
                        f"<b>{format_price(below[0])}</b>"
                    )

            except Exception:
                pass

        lines += [
            "",
            "────────────",
            "",
        ]

    lines += [

        "D1/W1 → 1H → Major Liquidity",

        "→ Sweep → 15M → 5M ILM",

        "→ Entry → SL → next liquidity",

        "",

        "RR < 1:2 → NO TRADE.",

        "❌ В середине движения не входим.",

    ]

    return "\n".join(lines)


# =========================================================
# LEVELS
# =========================================================

def build_levels_message(results):

    lines = [

        "💧 <b>TRADEMIND 6.1.1 — MAJOR LIQUIDITY</b>",

        "",

        "Только крупные 1H уровни.",

        "🔴 BSL — сверху.",

        "🟢 SSL — снизу.",

        "Снятая ликвидность не является "
        "свежей целью.",

        "",

    ]

    for coin in COINS:

        result = results.get(
            coin
        )

        if (
            not result
            or result.get("error")
        ):
            continue

        lines += [

            f"💠 <b>{coin}</b> "
            f"{format_price(result.get('price'))}",

            "",

            format_levels(

                result.get(
                    "major_levels",
                    [],
                ),

                result.get("price"),

            ),

            "",

            "────────────",

            "",

        ]

    return "\n".join(lines)


# =========================================================
# SEARCH
# =========================================================

def build_search_message(results):

    ready = find_ready_setups(
        results
    )

    if ready:

        lines = [

            "🟢 <b>TRADEMIND — READY SETUPS</b>",

            "",

        ]

        for score, coin, result in ready:

            lines += [

                f"💠 <b>{coin}</b>",

                f"📐 Направление: "
                f"<b>{result.get('direction')}</b>",

                f"⭐ Score: "
                f"<b>{score:.0f}/100</b>",

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

                "✅ D1 / W1",

                "✅ 1H",

                "✅ Major Liquidity",

                "✅ Sweep",

                "✅ 15M Confirmation",

                "✅ 5M ILM",

                "✅ RR ≥ 1:2",

                "",

                "🟢 <b>МОЖНО ВХОДИТЬ</b>",

                "",

                "━━━━━━━━━━━━━━━━━━",

                "",

            ]

        return "\n".join(lines)

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
            continue

        stage = get_result_stage(
            result
        )

        score = result.get(
            "score",
            0,
        )

        direction = result.get(
            "direction"
        )

        direction_text = (
            f" | {direction}"
            if direction
            else ""
        )

        lines.append(

            f"{stage_icon(stage)} "
            f"<b>{coin}</b>: "
            f"{stage_text(stage)}"
            f"{direction_text} "
            f"— {score}/100"

        )

    lines += [

        "",

        "Ждём:",

        "D1/W1 → 1H → Major Liquidity",

        "→ Sweep → 15M → 5M ILM.",

        "",

        "TP = следующая major liquidity.",

        "RR < 1:2 → пропускаем.",

        "",

        "❌ Нет полного подтверждения "
        "→ нет входа.",

    ]

    return "\n".join(lines)


# =========================================================
# SOL
# =========================================================

def build_sol_message(result):

    if not result:

        return (
            "📈 <b>SOL</b>\n\n"
            "⏳ Нет данных.\n"
            "Нажми 🔄 Обновить."
        )

    if result.get("error"):

        return (
            "❌ <b>SOL</b>\n\n"
            "Ошибка получения данных."
        )

    stage = get_result_stage(
        result
    )

    lines = [

        "📈 <b>TRADEMIND 6.1.1 — SOL</b>",

        "",

        f"💰 Цена: "
        f"<b>{format_price(result.get('price'))}</b>",

        f"{stage_icon(stage)} "
        f"{stage_text(stage)}",

        f"⭐ Score: "
        f"<b>{result.get('score', 0)}/100</b>",

    ]

    if result.get("direction"):

        lines.append(
            f"📐 Direction: "
            f"<b>{result.get('direction')}</b>"
        )

    lines += [

        "",

        "💧 <b>MAJOR LIQUIDITY</b>",

        format_levels(

            result.get(
                "major_levels",
                [],
            ),

            result.get("price"),

        ),

    ]

    # =====================================================
    # SWEEP
    # =====================================================

    sweep = result.get("sweep")

    if sweep:

        lines += [

            "",

            "🚨 <b>SWEEP</b>",

            f"Direction: "
            f"<b>{sweep.get('direction')}</b>",

            f"Level: "
            f"<b>{format_price(sweep.get('level'))}</b>",

            f"Extreme: "
            f"<b>{format_price(sweep.get('extreme'))}</b>",

        ]

    # =====================================================
    # 15M
    # =====================================================

    if result.get(
        "confirmation_15m"
    ):

        lines += [

            "",

            "🟡 <b>15M CONFIRMATION</b>",

            str(
                result.get(
                    "confirmation_15m"
                )
            ),

        ]

    # =====================================================
    # 5M
    # =====================================================

    confirmation_5m = (
        result.get("confirmation_5m")
        or result.get("trigger_5m")
        or result.get("confirmation")
    )

    if confirmation_5m:

        lines += [

            "",

            "🔵 <b>5M ILM</b>",

            str(
                confirmation_5m
            ),

        ]

    # =====================================================
    # SETUP
    # =====================================================

    if result.get("entry") is not None:

        lines += [

            "",

            "🎯 <b>SETUP</b>",

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

            "🎯 TP = next major unswept liquidity",

            "🛡 SL = за sweep extreme + buffer",

            "📊 RR ≥ 1:2",

            "",

            "🟢 <b>МОЖНО ВХОДИТЬ</b>",

        ]

    reason = result.get(
        "reason"
    )

    if reason:

        lines += [

            "",

            "Причина:",

            str(reason),

        ]

    return "\n".join(lines)


# =========================================================
# SEND
# =========================================================

async def send_to_subscribers(
    application,
    text,
):

    subscribers = load_subscribers()

    if not subscribers:
        return

    for chat_id in subscribers:

        try:

            await application.bot.send_message(

                chat_id=chat_id,

                text=text,

                parse_mode="HTML",

            )

        except Exception as exc:

            print(
                f"TELEGRAM SEND ERROR "
                f"{chat_id}: {exc}"
            )


# =========================================================
# MONITOR KEYS
# =========================================================

def sweep_key(coin, sweep):

    if not sweep:
        return None

    return (
        f"{coin}:"
        f"{sweep.get('direction')}:"
        f"{sweep.get('level')}:"
        f"{sweep.get('extreme')}:"
        f"{sweep.get('time')}:"
        f"{sweep.get('sweep_index')}"
    )


def ready_key(coin, result):

    return (
        f"{coin}:"
        f"{result.get('direction')}:"
        f"{result.get('entry')}:"
        f"{result.get('sl')}:"
        f"{result.get('tp')}:"
        f"{result.get('rr')}"
    )


def confirmation_key(coin, result):

    confirmation = result.get(
        "confirmation_15m"
    )

    if not confirmation:
        return None

    timestamp = (
        result.get(
            "confirmation_15m_time"
        )
        or result.get(
            "m15_confirmation_time"
        )
        or result.get(
            "confirmation_time_15m"
        )
    )

    return (
        f"{coin}:"
        f"{result.get('direction')}:"
        f"{timestamp}:"
        f"{str(confirmation)[:150]}"
    )


# =========================================================
# MONITOR
# =========================================================

async def monitor_job(application):

    state = load_state()

    # =====================================================
    # SCAN
    # =====================================================

    try:

        results = (
            await scan_all_coins_async()
        )

        save_results_cache(
            results
        )

    except Exception as exc:

        print(
            f"MONITOR ERROR: {exc}"
        )

        return

    # =====================================================
    # SWEEP ALERT
    # =====================================================

    for coin, result in results.items():

        if result.get("error"):
            continue

        sweep = result.get("sweep")

        if not sweep:
            continue

        key = sweep_key(
            coin,
            sweep
        )

        previous = state[
            "last_sweep_keys"
        ].get(coin)

        if key == previous:
            continue

        state[
            "last_sweep_keys"
        ][coin] = key

        text = "\n".join([

            "🚨 <b>TRADEMIND 6.1.1 — SWEEP</b>",

            "",

            f"💠 <b>{coin}</b>",

            f"📐 <b>{sweep.get('direction')}</b>",

            "",

            f"💧 Level: "
            f"<b>{format_price(sweep.get('level'))}</b>",

            f"Extreme: "
            f"<b>{format_price(sweep.get('extreme'))}</b>",

            "",

            "✅ Major liquidity снята.",

            "⏳ Переходим к 15M confirmation.",

            "❌ Вход пока запрещён.",

        ])

        await send_to_subscribers(
            application,
            text,
        )

    # =====================================================
    # 15M ALERT
    # =====================================================

    for coin, result in results.items():

        if result.get("error"):
            continue

        confirmation = result.get(
            "confirmation_15m"
        )

        if not confirmation:
            continue

        key = confirmation_key(
            coin,
            result
        )

        if not key:
            continue

        previous = state[
            "last_15m_keys"
        ].get(coin)

        if key == previous:
            continue

        state[
            "last_15m_keys"
        ][coin] = key

        text = "\n".join([

            "🟡 <b>TRADEMIND 6.1.1 — 15M CONFIRMATION</b>",

            "",

            f"💠 <b>{coin}</b>",

            f"📐 Direction: "
            f"<b>{result.get('direction')}</b>",

            "",

            "✅ Sweep confirmed.",

            "✅ 15M confirmation.",

            "⏳ Ждём 5M ILM.",

            "❌ Вход пока запрещён.",

        ])

        await send_to_subscribers(
            application,
            text,
        )

    # =====================================================
    # READY
    # =====================================================

    ready_setups = find_ready_setups(
        results
    )

    if not ready_setups:

        save_state(
            state
        )

        return

    for score, coin, result in ready_setups:

        key = ready_key(
            coin,
            result
        )

        previous = state[
            "last_ready_keys"
        ].get(coin)

        if key == previous:
            continue

        state[
            "last_ready_keys"
        ][coin] = key

        state[
            "last_signal_key"
        ] = key

        save_state(
            state
        )

        text = "\n".join([

            "🟢 <b>TRADEMIND 6.1.1 — READY</b>",

            "",

            f"💠 Монета: "
            f"<b>{coin}</b>",

            f"📐 Направление: "
            f"<b>{result.get('direction')}</b>",

            f"⭐ Score: "
            f"<b>{score:.0f}/100</b>",

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

            "✅ D1 / W1",

            "✅ 1H",

            "✅ Major Liquidity",

            "✅ Sweep",

            "✅ 15M Confirmation",

            "✅ 5M ILM",

            "✅ RR ≥ 1:2",

            "",

            "🎯 TP = next major unswept liquidity",

            "🛡 SL = за sweep extreme + buffer",

            "",

            "🟢 <b>МОЖНО ВХОДИТЬ</b>",

        ])

        await send_to_subscribers(
            application,
            text,
        )


# =========================================================
# MONITOR LOOP
# =========================================================

async def monitor_loop(application):

    print(
        "TradeMind 6.1.1 background monitor started. "
        f"Interval: {CHECK_INTERVAL}s"
    )

    while True:

        try:

            await monitor_job(
                application
            )

        except asyncio.CancelledError:

            print(
                "TradeMind monitor stopped."
            )

            raise

        except Exception as exc:

            print(
                f"MONITOR LOOP ERROR: {exc}"
            )

        await asyncio.sleep(
            CHECK_INTERVAL
        )


# =========================================================
# COMMANDS
# =========================================================

async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(

        build_dashboard_message(),

        parse_mode="HTML",

        reply_markup=dashboard_keyboard(),

    )


async def dashboard_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(

        build_dashboard_message(),

        parse_mode="HTML",

        reply_markup=dashboard_keyboard(),

    )


async def market_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    results, _ = get_cached_results()

    if not results:

        results = (
            await scan_all_coins_async()
        )

        save_results_cache(
            results
        )

    await update.message.reply_text(

        build_market_message(
            results
        ),

        parse_mode="HTML",

        reply_markup=dashboard_keyboard(),

    )


async def levels_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    results, _ = get_cached_results()

    if not results:

        results = (
            await scan_all_coins_async()
        )

        save_results_cache(
            results
        )

    await update.message.reply_text(

        build_levels_message(
            results
        ),

        parse_mode="HTML",

        reply_markup=dashboard_keyboard(),

    )


async def search_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    results, _ = get_cached_results()

    if not results:

        results = (
            await scan_all_coins_async()
        )

        save_results_cache(
            results
        )

    await update.message.reply_text(

        build_search_message(
            results
        ),

        parse_mode="HTML",

        reply_markup=dashboard_keyboard(),

    )


async def sol_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    results, _ = get_cached_results()

    result = results.get(
        "SOL"
    )

    if not result:

        result = await asyncio.to_thread(
            build_analysis,
            COINS["SOL"],
        )

        results["SOL"] = result

        save_results_cache(
            results
        )

    await update.message.reply_text(

        build_sol_message(
            result
        ),

        parse_mode="HTML",

        reply_markup=dashboard_keyboard(),

    )


async def subscribe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = (
        update.effective_chat.id
    )

    subscribers = (
        load_subscribers()
    )

    if chat_id not in subscribers:

        subscribers.append(
            chat_id
        )

        save_subscribers(
            subscribers
        )

    await update.message.reply_text(

        "🔔 <b>Мониторинг включён.</b>\n\n"

        "Важные события:\n"

        "💧 Sweep\n"

        "🟡 15M confirmation\n"

        "🟢 READY",

        parse_mode="HTML",

        reply_markup=dashboard_keyboard(),

    )


async def unsubscribe_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    chat_id = (
        update.effective_chat.id
    )

    subscribers = (
        load_subscribers()
    )

    if chat_id in subscribers:

        subscribers.remove(
            chat_id
        )

        save_subscribers(
            subscribers
        )

    await update.message.reply_text(

        "🔕 <b>Мониторинг выключен.</b>",

        parse_mode="HTML",

        reply_markup=dashboard_keyboard(),

    )


# =========================================================
# STATUS
# =========================================================

def build_status_message():

    _, updated_at = (
        get_cached_results()
    )

    return "\n".join([

        "📊 <b>TRADEMIND 6.1.1 — STATUS</b>",

        "",

        f"Strategy: "
        f"<b>{STRATEGY_VERSION}</b>",

        "Market: "
        "<b>Binance Spot</b>",

        "BingX: "
        f"<b>{'AVAILABLE' if BINGX_AVAILABLE else 'OFF'}</b>",

        "",

        "📡 Monitor: <b>ONLINE</b>",

        f"Interval: "
        f"<b>{CHECK_INTERVAL}s</b>",

        f"Cache: "
        f"<b>{cache_age_text(updated_at)}</b>",

        "",

        "Daily trade limit: <b>OFF</b>",

        "Daily stop: <b>OFF</b>",

        "",

        f"Coins: <b>{len(COINS)}/9</b>",

        "Liquidity: <b>BSL + SSL</b>",

        "TP: <b>ONE</b>",

        "Target: <b>NEXT MAJOR LIQUIDITY</b>",

        "SL: <b>SWEEP + 0.20%</b>",

        "RR: <b>MINIMUM 1:2</b>",

    ])


async def status_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(

        build_status_message(),

        parse_mode="HTML",

        reply_markup=dashboard_keyboard(),

    )


# =========================================================
# JOURNAL
# =========================================================

def build_journal_message():

    journal = load_json(
        JOURNAL_FILE,
        [],
    )

    if not journal:

        return (
            "📒 <b>TRADEMIND JOURNAL</b>\n\n"
            "Журнал пока пуст."
        )

    lines = [

        "📒 <b>TRADEMIND JOURNAL</b>",

        "",

        f"Всего сделок: "
        f"<b>{len(journal)}</b>",

        "",

    ]

    for trade in reversed(
        journal[-10:]
    ):

        lines += [

            f"💠 {trade.get('coin', '?')}",

            f"📐 {trade.get('direction', '?')}",

            f"📊 Result: "
            f"<b>{trade.get('result', '?')}</b>",

            f"Entry: "
            f"{trade.get('entry', '?')}",

            f"SL: "
            f"{trade.get('sl', '?')}",

            f"TP: "
            f"{trade.get('tp', '?')}",

            "",

        ]

    return "\n".join(lines)


async def journal_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    await update.message.reply_text(

        build_journal_message(),

        parse_mode="HTML",

        reply_markup=dashboard_keyboard(),

    )


# =========================================================
# CALLBACKS
# =========================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    action = query.data

    # -----------------------------------------------------
    # START
    # -----------------------------------------------------

    if action == "start":

        await safe_edit_message(

            query,

            build_dashboard_message(),

            dashboard_keyboard(),

        )

        return

    # -----------------------------------------------------
    # REFRESH
    # -----------------------------------------------------

    if action == "refresh":

        await safe_edit_message(

            query,

            "🔄 <b>Обновляю рынок...</b>\n\n"
            "⏳ Проверяю 9 монет.",

        )

        try:

            results = (
                await scan_all_coins_async()
            )

            save_results_cache(
                results
            )

            await safe_edit_message(

                query,

                build_dashboard_message(),

                dashboard_keyboard(),

            )

        except Exception as exc:

            await safe_edit_message(

                query,

                f"❌ Ошибка обновления:\n{exc}",

                dashboard_keyboard(),

            )

        return

    # -----------------------------------------------------
    # RADAR
    # -----------------------------------------------------

    if action == "radar":

        await safe_edit_message(

            query,

            build_dashboard_message(),

            dashboard_keyboard(),

        )

        return

    # -----------------------------------------------------
    # ACTIVE
    # -----------------------------------------------------

    if action == "active":

        await safe_edit_message(

            query,

            build_active_setup_message(),

            dashboard_keyboard(),

        )

        return

    # -----------------------------------------------------
    # WHY
    # -----------------------------------------------------

    if action == "why":

        await safe_edit_message(

            query,

            build_why_wait_message(),

            dashboard_keyboard(),

        )

        return

    # -----------------------------------------------------
    # MARKET
    # -----------------------------------------------------

    if action == "market":

        results, _ = (
            get_cached_results()
        )

        if not results:

            results = (
                await scan_all_coins_async()
            )

            save_results_cache(
                results
            )

        await safe_edit_message(

            query,

            build_market_message(
                results
            ),

            dashboard_keyboard(),

        )

        return

    # -----------------------------------------------------
    # LEVELS
    # -----------------------------------------------------

    if action == "levels":

        results, _ = (
            get_cached_results()
        )

        if not results:

            results = (
                await scan_all_coins_async()
            )

            save_results_cache(
                results
            )

        await safe_edit_message(

            query,

            build_levels_message(
                results
            ),

            dashboard_keyboard(),

        )

        return

    # -----------------------------------------------------
    # SEARCH
    # -----------------------------------------------------

    if action == "search":

        results, _ = (
            get_cached_results()
        )

        if not results:

            results = (
                await scan_all_coins_async()
            )

            save_results_cache(
                results
            )

        await safe_edit_message(

            query,

            build_search_message(
                results
            ),

            dashboard_keyboard(),

        )

        return

    # -----------------------------------------------------
    # SOL
    # -----------------------------------------------------

    if action == "sol":

        results, _ = (
            get_cached_results()
        )

        result = results.get(
            "SOL"
        )

        if not result:

            await safe_edit_message(

                query,

                "📈 <b>SOL</b>\n\n"
                "⏳ Нет данных.",

                dashboard_keyboard(),

            )

            return

        await safe_edit_message(

            query,

            build_sol_message(
                result
            ),

            dashboard_keyboard(),

        )

        return

    # -----------------------------------------------------
    # STATUS
    # -----------------------------------------------------

    if action == "status":

        await safe_edit_message(

            query,

            build_status_message(),

            dashboard_keyboard(),

        )

        return

    # -----------------------------------------------------
    # SUBSCRIBE
    # -----------------------------------------------------

    if action == "subscribe":

        chat_id = (
            query.message.chat_id
        )

        subscribers = (
            load_subscribers()
        )

        if chat_id not in subscribers:

            subscribers.append(
                chat_id
            )

            save_subscribers(
                subscribers
            )

        await safe_edit_message(

            query,

            "🔔 <b>Мониторинг включён.</b>\n\n"

            "Важные события:\n"

            "💧 Sweep\n"

            "🟡 15M confirmation\n"

            "🟢 READY",

            dashboard_keyboard(),

        )

        return

    # -----------------------------------------------------
    # UNSUBSCRIBE
    # -----------------------------------------------------

    if action == "unsubscribe":

        chat_id = (
            query.message.chat_id
        )

        subscribers = (
            load_subscribers()
        )

        if chat_id in subscribers:

            subscribers.remove(
                chat_id
            )

            save_subscribers(
                subscribers
            )

        await safe_edit_message(

            query,

            "🔕 <b>Мониторинг выключен.</b>",

            dashboard_keyboard(),

        )

        return

    # -----------------------------------------------------
    # JOURNAL
    # -----------------------------------------------------

    if action == "journal":

        await safe_edit_message(

            query,

            build_journal_message(),

            dashboard_keyboard(),

        )

        return


# =========================================================
# POST INIT
# =========================================================

async def post_init(application):

    application.create_task(

        monitor_loop(
            application
        )

    )

    print(
        "TradeMind 6.1.1 background monitor launched."
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not TOKEN:

        raise RuntimeError(
            "BOT_TOKEN environment variable "
            "is not set."
        )

    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    # =====================================================
    # COMMANDS
    # =====================================================

    application.add_handler(
        CommandHandler(
            "start",
            start_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "dashboard",
            dashboard_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "market",
            market_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "levels",
            levels_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "search",
            search_command,
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
            "subscribe",
            subscribe_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "unsubscribe",
            unsubscribe_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "status",
            status_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "journal",
            journal_command,
        )
    )

    # =====================================================
    # CALLBACKS
    # =====================================================

    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    # =====================================================
    # START
    # =====================================================

    print(
        "TradeMind 6.1.1 started."
    )

    print(
        f"Strategy: {STRATEGY_VERSION}"
    )

    print(
        "Market: Binance Spot"
    )

    print(
        "BingX available:",
        BINGX_AVAILABLE,
    )

    print(
        f"Monitoring interval: "
        f"{CHECK_INTERVAL}s"
    )

    print(
        f"Coins: {len(COINS)}/9"
    )

    print(
        "Daily trade limit: OFF"
    )

    print(
        "Liquidity: BSL + SSL"
    )

    print(
        "TP: next major unswept liquidity"
    )

    print(
        "SL: sweep extreme + 0.20%"
    )

    print(
        "Minimum RR: 1:2"
    )

    application.run_polling()


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":
    main()