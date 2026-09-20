"""
TradeMind 9.10 — bot(1).py

Изменения vs 8.6.1:
- COINS: убраны SOL/SUI/BTC (watch-only, v9.10)
- BREAKEVEN_TRIGGER_R 1.0 → 0.5
- PARTIAL_TP_TRIGGER_R 1.0 → 0.7, PARTIAL_TP_PERCENT 50
- Второй partial: PARTIAL_TP_2_TRIGGER_R 1.3, PARTIAL_TP_2_PERCENT 25
- TRAILING_TRIGGER_R 1.5 → 1.3, TRAILING_DISTANCE_R 1.0 → 0.8
- COOLDOWN_AFTER_SL_HOURS 6 → 3
- apply_trailing(): два partial уровня
- Сообщения: отображение partial #1 + #2
"""

import asyncio
import io
import json
import os
import struct
import threading
import time
import uuid
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import escape

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
    ALLOW_SHORT,
)


# ============================================================
# CONFIG v9.10
# ============================================================

TOKEN = os.getenv("BOT_TOKEN")

CHECK_INTERVAL = 15
SCAN_WORKERS = 12

MIN_SCORE_READY = 88
MIN_RR = 2.0

SCAN_CACHE_TTL = 5.0

RUN_BACKTEST_ON_START = True
BACKTEST_SYMBOL = "ETHUSDT"
BACKTEST_MULTI = True
BACKTEST_MAX_HOURS = 24

# ─── v9.10: trailing (плотнее) ───
TRAILING_ENABLED = True
TRAILING_TRIGGER_R = 1.3       # было 1.5
TRAILING_DISTANCE_R = 0.8      # было 1.0

# ─── v9.10: BE раньше ───
BREAKEVEN_TRIGGER_R = 0.5      # было 1.0

# ─── v9.10: два partial ───
PARTIAL_TP_ENABLED = True
PARTIAL_TP_TRIGGER_R = 0.7     # было 1.0
PARTIAL_TP_PERCENT = 50

PARTIAL_TP_2_ENABLED = True
PARTIAL_TP_2_TRIGGER_R = 1.3   # второй уровень
PARTIAL_TP_2_PERCENT = 25

BREAKEVEN_TRIGGER_PCT = 1.0
TRAILING_TRIGGER_PCT = 4.0
TRAILING_DISTANCE_PCT = 2.0

BLOCK_CONFLICTING_TRADES = True

# ─── v9.10: cooldown ───
COOLDOWN_AFTER_SL_ENABLED = True
COOLDOWN_AFTER_SL_HOURS = 3    # было 6
COOLDOWN_AFTER_TP_HOURS = 0

NOTIFICATION_DEDUP_HOURS = 3
NOTIFICATION_ENTRY_TOLERANCE_PCT = 0.5


# ─── v9.10: только «здоровые» пары ───
COINS = {
    "ETH": "ETHUSDT",
    "XRP": "XRPUSDT",
    "LINK": "LINKUSDT",
    "DOT": "DOTUSDT",
    "BCH": "BCHUSDT",
    "APT": "APTUSDT",
    "INJ": "INJUSDT",
    # ─── watch-only (не торгуем) ───
    # "BTC": "BTCUSDT",
    # "SOL": "SOLUSDT",
    # "SUI": "SUIUSDT",
    # "HYPE": "HYPEUSDT",
}


SUBSCRIBERS_FILE = "subscribers.json"
TRADE_JOURNAL_FILE = "trade_journal.json"
ACTIVE_TRADES_FILE = "active_trades.json"
PENDING_SETUPS_FILE = "pending_setups.json"
NOTIFICATION_STATE_FILE = "notification_state.json"

_storage_lock = threading.RLock()


def load_json(filename, default):
    try:
        with open(filename, "r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return default


def save_json(filename, data):
    tmp = filename + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, filename)
    except Exception as exc:
        print("SAVE ERROR:", filename, exc)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def now_ms():
    return int(time.time() * 1000)


def subscribers():
    data = load_json(SUBSCRIBERS_FILE, [])
    return data if isinstance(data, list) else []


def save_subscribers(data):
    with _storage_lock:
        save_json(SUBSCRIBERS_FILE, data)


def is_subscribed(chat_id):
    return chat_id in subscribers()


def load_active_trades():
    data = load_json(ACTIVE_TRADES_FILE, [])
    return data if isinstance(data, list) else []


def save_active_trades(trades):
    with _storage_lock:
        save_json(ACTIVE_TRADES_FILE, trades)


def user_active_trades(chat_id):
    return [
        trade for trade in load_active_trades()
        if trade.get("status") == "OPEN"
        and trade.get("chat_id") == chat_id
    ]


def load_journal():
    data = load_json(TRADE_JOURNAL_FILE, [])
    return data if isinstance(data, list) else []


def save_journal(journal):
    with _storage_lock:
        by_user = {}
        for entry in journal:
            cid = entry.get("chat_id", 0)
            by_user.setdefault(cid, []).append(entry)
        trimmed = []
        for cid, entries in by_user.items():
            trimmed.extend(entries[-500:])
        save_json(TRADE_JOURNAL_FILE, trimmed)


def user_journal(chat_id):
    return [t for t in load_journal() if t.get("chat_id") == chat_id]


def load_pending_setups():
    data = load_json(PENDING_SETUPS_FILE, {})
    return data if isinstance(data, dict) else {}


def save_pending_setups(data):
    with _storage_lock:
        save_json(PENDING_SETUPS_FILE, data)


def load_notification_state():
    data = load_json(NOTIFICATION_STATE_FILE, {})
    return data if isinstance(data, dict) else {}


def save_notification_state(data):
    with _storage_lock:
        save_json(NOTIFICATION_STATE_FILE, data)


# ============================================================
# BASE FORMATTERS
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


def _risk_pct(setup):
    try:
        e = float(setup.get("entry"))
        s = float(setup.get("sl"))
        if e <= 0:
            return "N/A"
        return f"{abs(e - s) / e * 100:.2f}%"
    except Exception:
        return "N/A"


def calculate_pnl_percent(entry, exit_price, direction):
    try:
        entry = float(entry)
        exit_price = float(exit_price)
        if entry <= 0:
            return None
        if direction == "LONG":
            return (exit_price - entry) / entry * 100
        if direction == "SHORT":
            return (entry - exit_price) / entry * 100
    except Exception:
        return None
    return None


def direction_icon(direction):
    if direction == "LONG":
        return "🟢"
    if direction == "SHORT":
        return "🔴"
    return "⚪"


STAGE_ICONS = {
    "READY": "🟢", "SWEPT": "🟠",
    "15M_CONFIRMED": "🟡", "WAIT": "⏳",
}

STAGE_TEXTS = {
    "READY": "🟢 МОЖНО ВХОДИТЬ", "SWEPT": "🟠 SWEEP",
    "15M_CONFIRMED": "🟡 15M CONFIRMED", "WAIT": "⏳ ОЖИДАНИЕ",
}


def stage_icon(stage): return STAGE_ICONS.get(stage, "⏳")
def stage_text(stage): return STAGE_TEXTS.get(stage, "⏳ ОЖИДАНИЕ")


def tp_source_label(source):
    return {"d1": " (D1)", "major": " (major)",
            "local": " (local)",
            "fixed_rr": " (RR 1:2)"}.get(source, "")


def _scenario_rank(stage):
    return {"READY": 4, "15M_CONFIRMED": 3,
            "SWEPT": 2, "WAIT": 1}.get(stage, 0)


def strong_levels(levels):
    if not levels:
        return []
    strong = [l for l in levels if float(l.get("strength", 0)) >= 65]
    return strong[:8] if strong else levels[:6]


def levels_text(levels, current_price):
    levels = strong_levels(levels)
    if not levels:
        return "нет сильной major liquidity"
    lines = []
    for level in levels:
        try:
            lp = float(level["price"])
            d = abs(lp - current_price) / current_price * 100
        except Exception:
            continue
        lt = level.get("type", "LEVEL")
        src = level.get("source", "")
        icon = "🔴" if lt == "BSL" else "🟢"
        tag = f" [{src}]" if src else ""
        lines.append(
            f"{icon} <b>{lt}</b> {format_price(lp)} "
            f"• {d:.2f}% • S{float(level.get('strength', 0)):.0f}{tag}"
        )
    return "\n".join(lines) if lines else "нет сильной major liquidity"


def fvgs_text(fvgs, current_price, limit=4):
    if not fvgs:
        return "— нет незакрытых зон"
    lines = []
    for fvg in fvgs[:limit]:
        try:
            top = float(fvg["top"])
            bottom = float(fvg["bottom"])
        except Exception:
            continue
        icon = "🟢" if fvg["type"] == "bullish" else "🔴"
        tf = fvg.get("tf", "").upper()
        middle = (top + bottom) / 2
        d = abs(middle - current_price) / current_price * 100
        lines.append(
            f"{icon} <b>{tf}</b> "
            f"{format_price(bottom)} – {format_price(top)} "
            f"• {d:.2f}%"
        )
    return "\n".join(lines) if lines else "— нет незакрытых зон"


# ============================================================
# NOTIFICATION DEDUP
# ============================================================

def _entry_bucket(entry, tolerance_pct=NOTIFICATION_ENTRY_TOLERANCE_PCT):
    try:
        e = float(entry)
        if e <= 0:
            return 0
        bucket_size = e * tolerance_pct / 100.0
        if bucket_size <= 0:
            return 0
        return int(round(e / bucket_size))
    except Exception:
        return 0


def _notification_is_duplicate(notification_state, coin,
                                direction, entry):
    prev = notification_state.get(coin)
    if not isinstance(prev, dict):
        return False
    prev_dir = prev.get("direction")
    prev_bucket = prev.get("entry_bucket")
    prev_ts = prev.get("ts", 0)
    if prev_dir != direction:
        return False
    now_ts = time.time()
    elapsed_h = (now_ts - prev_ts) / 3600.0
    if elapsed_h >= NOTIFICATION_DEDUP_HOURS:
        return False
    return prev_bucket == _entry_bucket(entry)


def _notification_mark(notification_state, coin, direction,
                        entry, setup_id):
    notification_state[coin] = {
        "direction": direction,
        "entry_bucket": _entry_bucket(entry),
        "setup_id": setup_id,
        "entry": float(entry) if entry is not None else None,
        "ts": time.time(),
    }


# ============================================================
# COOLDOWN (v9.10: 3h)
# ============================================================

def _recent_result_ms(coin, result_type):
    journal = load_journal()
    latest = None
    for trade in reversed(journal):
        if trade.get("coin") != coin:
            continue
        if trade.get("result") != result_type:
            continue
        closed_ms = trade.get("closed_at_ms")
        if closed_ms is None:
            continue
        if latest is None or closed_ms > latest:
            latest = closed_ms
    return latest


def coin_in_cooldown(coin):
    if not COOLDOWN_AFTER_SL_ENABLED:
        return False, None
    current_ms = now_ms()
    if COOLDOWN_AFTER_SL_HOURS > 0:
        last_sl_ms = _recent_result_ms(coin, "SL")
        if last_sl_ms is not None:
            elapsed_h = (current_ms - last_sl_ms) / 3600000
            if elapsed_h < COOLDOWN_AFTER_SL_HOURS:
                remaining = COOLDOWN_AFTER_SL_HOURS - elapsed_h
                return True, f"SL {elapsed_h:.1f}h ago (осталось {remaining:.1f}h)"
    if COOLDOWN_AFTER_TP_HOURS > 0:
        last_tp_ms = _recent_result_ms(coin, "TP")
        if last_tp_ms is not None:
            elapsed_h = (current_ms - last_tp_ms) / 3600000
            if elapsed_h < COOLDOWN_AFTER_TP_HOURS:
                remaining = COOLDOWN_AFTER_TP_HOURS - elapsed_h
                return True, f"TP {elapsed_h:.1f}h ago (осталось {remaining:.1f}h)"
    return False, None


# ============================================================
# ANALYSIS
# ============================================================

def build_analysis(symbol):
    market = get_market_data(symbol)
    price = market["price"]

    levels = find_major_liquidity(
        market["candles_1h"], price, 12,
        market["candles_15m"], market["candles_5m"],
        market["candles_1m"],
    )

    direction = get_1h_direction(market["candles_1h"])

    sweep = None
    if direction != "NEUTRAL":
        sweep = detect_sweep(market["candles_1h"], price,
                             direction, levels)

    result = analyze(
        market["candles_1h"], market["candles_15m"],
        market["candles_5m"], price, levels, sweep,
        candles_1m=market["candles_1m"],
        d1_context=market.get("d1_context"),
        fvgs=market.get("fvgs"),
        symbol=symbol,
    )

    result.update({
        "symbol": symbol,
        "price": price,
        "major_levels": levels,
        "sweep": sweep,
        "fvgs": market.get("fvgs", []),
        "candles_d1": market["candles_d1"],
        "candles_1h": market["candles_1h"],
        "candles_15m": market["candles_15m"],
        "candles_5m": market["candles_5m"],
        "candles_1m": market["candles_1m"],
        "d1_context": market.get("d1_context"),
    })
    return result


_scan_cache = {"results": None, "timestamp": 0.0}


def scan_one(item):
    coin, symbol = item
    try:
        return coin, build_analysis(symbol)
    except Exception as exc:
        return coin, {"error": str(exc), "symbol": symbol}


def scan_all():
    now = time.time()
    if (_scan_cache["results"] is not None
            and now - _scan_cache["timestamp"] < SCAN_CACHE_TTL):
        return _scan_cache["results"]

    results = {}
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as ex:
        futures = [ex.submit(scan_one, item) for item in COINS.items()]
        for future in as_completed(futures):
            coin, result = future.result()
            results[coin] = result

    final = {coin: results.get(coin, {"error": "нет данных"})
             for coin in COINS}
    _scan_cache["results"] = final
    _scan_cache["timestamp"] = now
    return final


# ============================================================
# MESSAGES
# ============================================================

def dashboard_message(results, chat_id=None):
    ready = swept = confirmed = waiting = 0
    active = len(user_active_trades(chat_id)) if chat_id is not None else 0

    for result in results.values():
        s = result.get("stage")
        if s == "READY":
            ready += 1
        elif s == "SWEPT":
            swept += 1
        elif s == "15M_CONFIRMED":
            confirmed += 1
        else:
            waiting += 1

    cooldown_label = "ON" if COOLDOWN_AFTER_SL_ENABLED else "OFF"
    cd_hours = COOLDOWN_AFTER_SL_HOURS

    lines = [
        "🧠 <b>TRADEMIND CONTROL CENTER</b>",
        f"<code>v{escape(str(STRATEGY_VERSION))}</code>",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"🟢 READY: <b>{ready}</b>",
        f"🟠 SWEEP: <b>{swept}</b>",
        f"🟡 15M: <b>{confirmed}</b>",
        f"⏳ WAIT: <b>{waiting}</b>",
        f"📌 ACTIVE: <b>{active}</b>",
        "",
        f"❄️ Cooldown: <b>{cooldown_label} {cd_hours}h</b>",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"💠 Мониторинг: <b>{len(COINS)} монет</b>",
        "",
    ]

    for coin in COINS:
        result = results.get(coin, {})
        if result.get("error"):
            lines.append(f"⚫ <b>{coin}</b> — ERROR")
            continue

        in_cd, cd_reason = coin_in_cooldown(coin)
        cd_tag = " ❄️" if in_cd else ""

        price = format_price(result.get("price"))
        direction = result.get("direction", "NEUTRAL")
        stage = result.get("stage", "WAIT")
        score = result.get("score", 0)
        fvg_tag = " ⚡" if result.get("fvg_bonus", 0) > 0 else ""
        promoted = " 🔥" if result.get("_v910_promoted") else ""
        lines.append(
            f"{stage_icon(stage)} <b>{coin}</b> {price} "
            f"{direction_icon(direction)} {direction} "
            f"<code>{score}/100</code>{fvg_tag}{promoted}{cd_tag}"
        )

    trailing_label = "ON" if TRAILING_ENABLED else "OFF"
    short_label = "ON" if ALLOW_SHORT else "OFF"

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "🧭 <b>СТРАТЕГИЯ 9.10</b>",
        "",
        "Entry = ILM trigger (retest)",
        "SL = structural + ATR floor",
        "TP = RR 1:2 (fixed)",
        "",
        f"💰 Partial 1: {PARTIAL_TP_TRIGGER_R}R / {PARTIAL_TP_PERCENT}%",
        f"💰 Partial 2: {PARTIAL_TP_2_TRIGGER_R}R / {PARTIAL_TP_2_PERCENT}%",
        f"🛡 BE: {BREAKEVEN_TRIGGER_R}R",
        "",
        f"🎯 Trailing: <b>{trailing_label}</b> ({TRAILING_TRIGGER_R}R/{TRAILING_DISTANCE_R}R)",
        f"📈 SHORT: <b>{short_label}</b>",
        f"❄️ Cooldown after SL: <b>{cd_hours}h</b>",
        "",
        "🔔 Автоуведомление: только READY.",
    ])
    return "\n".join(lines)


def dashboard_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data="dashboard")],
        [InlineKeyboardButton("🟢 READY", callback_data="filter_ready"),
         InlineKeyboardButton("📌 ACTIVE", callback_data="active")],
        [InlineKeyboardButton("📈 Графики", callback_data="charts"),
         InlineKeyboardButton("📊 Рынок", callback_data="market")],
        [InlineKeyboardButton("🔎 Сканер", callback_data="search"),
         InlineKeyboardButton("📒 Журнал", callback_data="journal")],
        [InlineKeyboardButton("🔔 Уведомления", callback_data="notifications"),
         InlineKeyboardButton("⚙️ Статус", callback_data="status")],
    ])


def ready_filter_message(results):
    ready = []
    for coin, result in results.items():
        if result.get("error"):
            continue
        if (result.get("stage") == "READY"
                and result.get("score", 0) >= MIN_SCORE_READY
                and result.get("rr") is not None
                and float(result.get("rr")) >= MIN_RR):
            in_cd, cd_reason = coin_in_cooldown(coin)
            ready.append((int(result.get("score", 0)), coin, result,
                          in_cd, cd_reason))

    if not ready:
        return ("🟢 <b>READY СЕТАПОВ НЕТ</b>\n\n"
                "TradeMind продолжает мониторинг в фоне.")

    ready.sort(key=lambda x: x[0], reverse=True)
    lines = ["🟢 <b>READY SETUPS</b>", ""]
    for score, coin, result, in_cd, cd_reason in ready:
        cd_tag = f" ❄️ ({cd_reason})" if in_cd else ""
        promoted_tag = " 🔥" if result.get("_v910_promoted") else ""
        lines.extend([
            f"💠 <b>{coin}</b>{cd_tag}{promoted_tag}",
            f"📐 {direction_icon(result.get('direction'))} "
            f"{result.get('direction')}",
            f"⭐ Score: <b>{score}/100</b>",
            f"💰 Entry: <b>{format_price(result.get('entry'))}</b>",
            f"🛑 SL: <b>{format_price(result.get('sl'))}</b> "
            f"(risk {_risk_pct(result)})",
            f"🎯 TP: <b>{format_price(result.get('tp'))}</b>"
            f"{tp_source_label(result.get('tp_source'))}",
            f"📊 RR: <b>{format_rr(result.get('rr'))}</b>",
            "",
        ])
    return "\n".join(lines)


def ready_filter_keyboard(results=None):
    rows = []
    if results:
        for coin, result in results.items():
            if (result.get("stage") == "READY"
                    and result.get("score", 0) >= MIN_SCORE_READY):
                rows.append([InlineKeyboardButton(
                    f"💠 {coin}", callback_data=f"coin_{coin}")])
    rows.extend([
        [InlineKeyboardButton("🔄 Обновить", callback_data="filter_ready")],
        [InlineKeyboardButton("⬅️ Dashboard", callback_data="dashboard")],
    ])
    return InlineKeyboardMarkup(rows)


def checklist_text(result):
    direction = result.get("direction", "NEUTRAL")
    stage = result.get("stage", "WAIT")
    price = result.get("price")

    if direction in ("LONG", "SHORT"):
        step1 = f"✅ 1H Direction: {direction}"
    else:
        step1 = "⬜ 1H Direction: NEUTRAL"

    levels = result.get("major_levels") or []
    expected_type = "SSL" if direction == "LONG" else "BSL"

    target_level = None
    for lvl in levels:
        if lvl.get("type") == expected_type:
            target_level = lvl
            break

    if target_level is not None:
        step2 = (f"✅ Major {expected_type}: "
                 f"{format_price(target_level.get('price'))}")
    else:
        step2 = f"⬜ Major {expected_type}: нет"

    sweep = result.get("sweep")
    ilm = result.get("ilm")
    entry = result.get("entry")
    rr = result.get("rr")
    bos = result.get("bos", False)

    step3 = "✅ Sweep" if sweep else "⬜ Sweep"

    stage_rank = {"WAIT": 0, "SWEPT": 1,
                  "15M_CONFIRMED": 2, "READY": 3}.get(stage, 0)

    bos_tag = " + BOS" if bos else ""
    step4 = (f"✅ 15M Confirmation{bos_tag}" if stage_rank >= 2
             else "⬜ 15M Confirmation")
    step5 = "✅ 5M ILM" if ilm else "⬜ 5M ILM"
    step6 = ("✅ Entry / RR"
             if (entry is not None and rr is not None)
             else "⬜ Entry / RR")

    lines = [
        "📋 <b>ПРОГРЕСС СЕТАПА</b>", "",
        step1, step2, step3, step4, step5, step6,
    ]

    if stage == "WAIT":
        if target_level is not None:
            try:
                level_price = float(target_level.get("price"))
                current_price = float(price)
                dist = abs(level_price - current_price) / current_price * 100
            except Exception:
                dist = 0.0
            lines.extend([
                "",
                f"🎯 Ждём: <b>{expected_type} sweep</b> "
                f"@ {format_price(target_level.get('price'))}",
                f"📏 До уровня: <b>{dist:.2f}%</b>",
            ])
        else:
            lines.extend([
                "",
                f"🎯 Ждём появления <b>{expected_type}</b> уровня.",
            ])
    elif stage == "SWEPT":
        lines.extend(["", "🎯 Ждём: <b>15M confirmation</b>"])
    elif stage == "15M_CONFIRMED":
        if ilm:
            lines.extend(["", "🎯 Сетап сформирован, но READY заблокирован."])
        else:
            lines.extend(["", "🎯 Ждём: <b>5M ILM</b>"])
    elif stage == "READY":
        lines.extend(["", "🎯 <b>READY — можно входить</b>"])

    return "\n".join(lines)


def _scenario_mini_checklist(other_dir, other):
    stage = other.get("stage", "WAIT")
    sweep = other.get("sweep")
    ilm = other.get("ilm")
    entry = other.get("entry")
    rr = other.get("rr")

    stage_rank = {"WAIT": 0, "SWEPT": 1,
                  "15M_CONFIRMED": 2, "READY": 3}.get(stage, 0)

    marks = []
    marks.append("✅ 1H" if other_dir in ("LONG", "SHORT") else "⬜ 1H")
    marks.append("✅ Major" if other.get("major_levels") else "⬜ Major")
    marks.append("✅ Sweep" if sweep else "⬜ Sweep")
    marks.append("✅ 15M" if stage_rank >= 2 else "⬜ 15M")
    marks.append("✅ ILM" if ilm else "⬜ ILM")
    marks.append("✅ Entry" if (entry is not None and rr is not None)
                 else "⬜ Entry")
    return " · ".join(marks)


def secondary_scenario_text(result):
    direction = result.get("direction", "NEUTRAL")
    long_r = result.get("long") or {}
    short_r = result.get("short") or {}

    if direction == "LONG":
        other_dir, other = "SHORT", short_r
    elif direction == "SHORT":
        other_dir, other = "LONG", long_r
    else:
        if long_r.get("score", 0) >= short_r.get("score", 0):
            other_dir, other = "SHORT", short_r
        else:
            other_dir, other = "LONG", long_r

    if not other:
        return ""

    other_score = other.get("score", 0)
    other_stage = other.get("stage", "WAIT")

    lines = [
        f"🔄 <b>ВТОРОЙ СЦЕНАРИЙ ({other_dir})</b>", "",
        f"Score: <b>{other_score}/100</b>",
        _scenario_mini_checklist(other_dir, other),
    ]

    levels = result.get("major_levels") or []
    expected_type = "SSL" if other_dir == "LONG" else "BSL"
    target = None
    for lvl in levels:
        if lvl.get("type") == expected_type:
            target = lvl
            break

    if other_stage == "WAIT" and target is not None:
        try:
            level_price = float(target.get("price"))
            current_price = float(result.get("price", level_price))
            dist = abs(level_price - current_price) / current_price * 100
        except Exception:
            dist = 0.0
        lines.append(
            f"🎯 Ждём {expected_type} sweep "
            f"@ {format_price(target.get('price'))} ({dist:.2f}%)"
        )
    elif other_stage == "SWEPT":
        lines.append("🎯 Sweep есть. Ждём 15M confirmation.")
    elif other_stage == "15M_CONFIRMED":
        lines.append("🎯 15M есть. Ждём 5M ILM.")
    elif other_stage == "READY":
        lines.append("🎯 READY.")
    else:
        lines.append("🎯 Ожидание.")

    return "\n".join(lines)


def coin_message(coin, result):
    if result.get("error"):
        return (f"❌ <b>{escape(coin)}</b>\n\n"
                f"{escape(str(result.get('error')))}")

    stage = result.get("stage", "WAIT")
    direction = result.get("direction", "NEUTRAL")
    d1_trend = result.get("d1_trend", "NEUTRAL")
    trend_activity = result.get("trend_activity", 0.0)
    fvg_bonus = result.get("fvg_bonus", 0)
    score = result.get("score", 0)
    bos = result.get("bos", False)
    promoted = result.get("_v910_promoted", False)

    in_cd, cd_reason = coin_in_cooldown(coin)

    long_r = result.get("long") or {}
    short_r = result.get("short") or {}

    if direction == "LONG":
        other, other_dir = short_r, "SHORT"
    elif direction == "SHORT":
        other, other_dir = long_r, "LONG"
    else:
        other, other_dir = None, None

    warning_line = None
    if other is not None:
        other_stage = other.get("stage", "WAIT")
        if _scenario_rank(other_stage) > _scenario_rank(stage):
            warning_line = (f"⚠️ <b>{other_dir}-сценарий активнее: "
                            f"{other_stage}</b>")

    lines = [f"💠 <b>{escape(coin)}</b>", "━━━━━━━━━━━━━━━━━━━━"]

    if in_cd:
        lines.extend(["", f"❄️ <b>COOLDOWN:</b> {cd_reason}"])

    if warning_line:
        lines.extend(["", warning_line])

    if promoted:
        lines.extend(["", "🔥 <b>v9.10 PROMOTE</b> — READY по override"])

    bos_tag = " ✅" if bos else " —"

    lines.extend([
        "",
        f"💰 Цена: <b>{format_price(result.get('price'))}</b>",
        f"📐 1H: <b>{direction_icon(direction)} {direction}</b>",
        f"📅 D1: <b>{direction_icon(d1_trend)} {d1_trend}</b>",
        f"⚡ Trend activity: <b>{trend_activity:.2f}</b>",
        f"🎯 BOS: <b>{bos_tag}</b>",
        f"⭐ Score: <b>{score}/100</b>"
        + (f" <i>(+{fvg_bonus} FVG)</i>" if fvg_bonus else ""),
        "",
        f"<b>{stage_text(stage)}</b>",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        checklist_text(result),
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "💧 <b>MAJOR LIQUIDITY</b>",
        "",
        levels_text(result.get("major_levels"), result.get("price")),
    ])

    fvgs = result.get("fvgs") or []
    if fvgs:
        lines.append("")
        lines.append("💠 <b>FVG / IMBALANCE</b>")
        lines.append(fvgs_text(fvgs, result.get("price")))

    sweep = result.get("sweep")
    if sweep:
        lines.extend([
            "",
            "💧 <b>SWEEP</b>",
            f"Level: <b>{format_price(sweep.get('level'))}</b>",
            f"Extreme: <b>{format_price(sweep.get('extreme'))}</b>",
        ])

    if stage == "READY":
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "🎯 <b>READY SETUP</b>",
            "",
            f"💰 Entry (лимит): <b>{format_price(result.get('entry'))}</b>",
            f"🛑 SL: <b>{format_price(result.get('sl'))}</b>",
            f"🎯 Risk: <b>{_risk_pct(result)}</b>",
            f"🎯 TP: <b>{format_price(result.get('tp'))}</b>"
            f"{tp_source_label(result.get('tp_source'))}",
            f"📊 RR: <b>{format_rr(result.get('rr'))}</b>",
            "",
            f"💰 Partial 1: <b>{PARTIAL_TP_TRIGGER_R}R</b> "
            f"({PARTIAL_TP_PERCENT}%)",
            f"💰 Partial 2: <b>{PARTIAL_TP_2_TRIGGER_R}R</b> "
            f"({PARTIAL_TP_2_PERCENT}%)",
            f"🛡 BE: <b>{BREAKEVEN_TRIGGER_R}R</b>",
        ])
        if in_cd:
            lines.extend([
                "",
                f"❄️ <b>ВХОД ЗАБЛОКИРОВАН COOLDOWN</b>",
                f"<i>{cd_reason}</i>",
            ])
        else:
            lines.extend([
                "",
                "🟢 <b>СТАВЬ ЛИМИТКУ НА ENTRY</b>",
            ])

    secondary = secondary_scenario_text(result)
    if secondary:
        lines.extend(["", "━━━━━━━━━━━━━━━━━━━━", secondary])

    reason = result.get("reason")
    if reason:
        reason_str = str(reason)
        is_dup = False
        if stage == "WAIT" and "Ждём" in reason_str:
            is_dup = True
        elif stage == "SWEPT" and "15M" in reason_str and "Ждём" in reason_str:
            is_dup = True
        elif stage == "15M_CONFIRMED" and "Ждём 5M ILM" in reason_str:
            is_dup = True
        if not is_dup:
            lines.extend(["", f"ℹ️ {escape(reason_str)}"])

    return "\n".join(lines)


def coin_keyboard(coin, result, setup=None):
    rows = [[InlineKeyboardButton("📈 График",
                                  callback_data=f"chart_{coin}")]]
    if (result and result.get("stage") == "READY" and setup):
        in_cd, _ = coin_in_cooldown(coin)
        if not in_cd:
            rows.append([InlineKeyboardButton(
                "🟢 Я ЗАШЁЛ", callback_data=f"enter_{setup['id']}")])
    rows.extend([
        [InlineKeyboardButton("🔄 Обновить", callback_data=f"coin_{coin}")],
        [InlineKeyboardButton("⬅️ Dashboard", callback_data="dashboard")],
    ])
    return InlineKeyboardMarkup(rows)


def chart_keyboard():
    rows = []
    coins = list(COINS.keys())
    for i in range(0, len(coins), 3):
        rows.append([InlineKeyboardButton(c, callback_data=f"chart_{c}")
                     for c in coins[i:i + 3]])
    rows.append([InlineKeyboardButton("⬅️ Dashboard",
                                      callback_data="dashboard")])
    return InlineKeyboardMarkup(rows)


def create_pending_setup(coin, result):
    if result.get("stage") != "READY":
        return None
    if result.get("score", 0) < MIN_SCORE_READY:
        return None

    required = ("entry", "sl", "tp", "rr", "direction")
    if any(result.get(k) is None for k in required):
        return None

    try:
        entry = float(result["entry"])
        sl = float(result["sl"])
        tp = float(result["tp"])
        rr = float(result["rr"])
    except Exception:
        return None

    if rr < MIN_RR:
        return None

    sweep = result.get("sweep") or {}
    ilm = result.get("ilm") or {}

    raw_id = (
        f"{coin}|{result['direction']}|"
        f"{sweep.get('open_time')}|"
        f"{sweep.get('level')}|"
        f"{ilm.get('trigger_time')}"
    )
    setup_id = uuid.uuid5(uuid.NAMESPACE_DNS, raw_id).hex[:12]

    pending = load_pending_setups()
    old = pending.get(setup_id)
    created_at = old.get("created_at") if old else now_iso()

    return {
        "id": setup_id, "coin": coin, "symbol": result.get("symbol"),
        "direction": result.get("direction"),
        "entry": entry, "sl": sl, "tp": tp, "rr": rr,
        "score": int(result.get("score", 0)),
        "tp_source": result.get("tp_source"),
        "fvg_bonus": result.get("fvg_bonus", 0),
        "v910_promoted": bool(result.get("_v910_promoted")),
        "created_at": created_at,
        "sweep": sweep, "ilm": ilm, "status": "PENDING",
    }


def save_ready_setup(coin, result):
    setup = create_pending_setup(coin, result)
    if setup is None:
        return None
    with _storage_lock:
        pending = load_pending_setups()
        pending[setup["id"]] = setup
        if len(pending) > 300:
            items = sorted(pending.items(),
                           key=lambda i: i[1].get("created_at", ""))
            pending = dict(items[-300:])
        save_pending_setups(pending)
    return setup


def activate_trade(setup, chat_id):
    with _storage_lock:
        active = load_active_trades()
        for trade in active:
            if (trade.get("setup_id") == setup["id"]
                    and trade.get("chat_id") == chat_id
                    and trade.get("status") == "OPEN"):
                return trade, False

        trade_id = uuid.uuid4().hex[:12]
        current = float(setup["entry"])
        trade = {
            "id": trade_id, "setup_id": setup["id"],
            "chat_id": chat_id,
            "coin": setup["coin"], "symbol": setup["symbol"],
            "direction": setup["direction"],
            "entry": float(setup["entry"]),
            "sl": float(setup["sl"]),
            "sl_initial": float(setup["sl"]),
            "tp": float(setup["tp"]),
            "rr": float(setup["rr"]),
            "score": int(setup.get("score", 0)),
            "tp_source": setup.get("tp_source"),
            "opened_at": now_iso(), "opened_at_ms": now_ms(),
            "status": "OPEN", "last_price": current,
            "best_price": current,
            "last_check_ms": now_ms(),
            "trailing_active": False,
            "partial_tp_done": False,
            "partial_tp_price": None,
            "partial_tp_2_done": False,
            "partial_tp_2_price": None,
            "entry_source": f"TradeMind {STRATEGY_VERSION} ILM trigger",
        }
        active.append(trade)
        save_active_trades(active)
    return trade, True


def close_trade(trade, exit_price, result_type):
    with _storage_lock:
        active = load_active_trades()
        target = None
        for item in active:
            if item.get("id") == trade.get("id"):
                target = item
                break
        if target is None:
            return None

        target["status"] = result_type
        target["result"] = result_type
        target["exit_price"] = float(exit_price)
        target["closed_at"] = now_iso()
        target["closed_at_ms"] = now_ms()
        target["pnl_percent"] = calculate_pnl_percent(
            target.get("entry"), exit_price, target.get("direction"))

        save_active_trades(active)

        journal = load_journal()
        je = dict(target)
        je["result"] = result_type
        journal.append(je)
        save_journal(journal)
    return target


def apply_trailing(trade, current_price):
    """
    v9.10:
      - partial 1 на PARTIAL_TP_TRIGGER_R (0.7R / 50%)
      - partial 2 на PARTIAL_TP_2_TRIGGER_R (1.3R / 25%)
      - BE на BREAKEVEN_TRIGGER_R (0.5R)
      - trailing на TRAILING_TRIGGER_R (1.3R), дистанция 0.8R
    """
    try:
        entry = float(trade["entry"])
        current_sl = float(trade["sl"])
        sl_initial = float(trade.get("sl_initial", current_sl))
        direction = trade["direction"]
        best = float(trade.get("best_price", entry))
    except Exception:
        return

    risk = abs(entry - sl_initial)
    if risk <= 0:
        return

    if direction == "LONG":
        if current_price > best:
            best = current_price
        move_r = (best - entry) / risk

        if (PARTIAL_TP_ENABLED
                and not trade.get("partial_tp_done")
                and move_r >= PARTIAL_TP_TRIGGER_R):
            trade["partial_tp_done"] = True
            trade["partial_tp_price"] = round(current_price, 8)

        if (PARTIAL_TP_2_ENABLED
                and not trade.get("partial_tp_2_done")
                and move_r >= PARTIAL_TP_2_TRIGGER_R):
            trade["partial_tp_2_done"] = True
            trade["partial_tp_2_price"] = round(current_price, 8)

        if move_r >= BREAKEVEN_TRIGGER_R:
            if entry > current_sl:
                trade["sl"] = round(entry, 8)
                trade["trailing_active"] = True

        if move_r >= TRAILING_TRIGGER_R:
            new_sl = best - risk * TRAILING_DISTANCE_R
            if new_sl > current_sl:
                trade["sl"] = round(new_sl, 8)
                trade["trailing_active"] = True

    elif direction == "SHORT":
        if current_price < best:
            best = current_price
        move_r = (entry - best) / risk

        if (PARTIAL_TP_ENABLED
                and not trade.get("partial_tp_done")
                and move_r >= PARTIAL_TP_TRIGGER_R):
            trade["partial_tp_done"] = True
            trade["partial_tp_price"] = round(current_price, 8)

        if (PARTIAL_TP_2_ENABLED
                and not trade.get("partial_tp_2_done")
                and move_r >= PARTIAL_TP_2_TRIGGER_R):
            trade["partial_tp_2_done"] = True
            trade["partial_tp_2_price"] = round(current_price, 8)

        if move_r >= BREAKEVEN_TRIGGER_R:
            if entry < current_sl:
                trade["sl"] = round(entry, 8)
                trade["trailing_active"] = True

        if move_r >= TRAILING_TRIGGER_R:
            new_sl = best + risk * TRAILING_DISTANCE_R
            if new_sl < current_sl:
                trade["sl"] = round(new_sl, 8)
                trade["trailing_active"] = True

    trade["best_price"] = round(best, 8)


def level_between(prev, curr, level):
    try:
        low = min(float(prev), float(curr))
        high = max(float(prev), float(curr))
        level = float(level)
        return low <= level <= high
    except Exception:
        return False


def check_trade_price(trade, prev, curr):
    try:
        curr = float(curr)
        prev = float(prev)
        sl = float(trade["sl"])
        tp = float(trade["tp"])
        d = trade["direction"]
    except Exception:
        return None

    if d == "LONG":
        tpc = curr >= tp or level_between(prev, curr, tp)
        slc = curr <= sl or level_between(prev, curr, sl)
        if tpc and slc:
            return ("AMBIGUOUS", curr)
        if tpc:
            return ("TP", curr)
        if slc:
            return ("SL", curr)
    elif d == "SHORT":
        tpc = curr <= tp or level_between(prev, curr, tp)
        slc = curr >= sl or level_between(prev, curr, sl)
        if tpc and slc:
            return ("AMBIGUOUS", curr)
        if tpc:
            return ("TP", curr)
        if slc:
            return ("SL", curr)
    return None


def resolve_crossed_levels_1m(trade, candles_1m, from_ms, to_ms):
    relevant = []
    for candle in candles_1m or []:
        try:
            ot = int(candle["open_time"])
            ct = int(candle["close_time"])
            if ct >= from_ms and ot <= to_ms:
                relevant.append(candle)
        except Exception:
            continue

    if not relevant:
        return "NO_DATA"
    relevant.sort(key=lambda x: int(x["open_time"]))

    direction = trade.get("direction")
    try:
        sl = float(trade["sl"])
        tp = float(trade["tp"])
    except Exception:
        return "NO_DATA"

    for candle in relevant:
        try:
            h = float(candle["high"])
            l = float(candle["low"])
        except Exception:
            continue
        if direction == "LONG":
            hit_sl = l <= sl
            hit_tp = h >= tp
        else:
            hit_sl = h >= sl
            hit_tp = l <= tp
        if hit_sl and hit_tp:
            return "AMBIGUOUS"
        if hit_tp:
            return "TP"
        if hit_sl:
            return "SL"
    return "NO_DATA"


def _partials_text(trade):
    """Строки про partial'ы для сообщений."""
    lines = []
    if trade.get("partial_tp_done"):
        lines.append(
            f"💰 Partial 1: <b>{PARTIAL_TP_PERCENT}%</b> @ "
            f"{format_price(trade.get('partial_tp_price'))}"
        )
    if trade.get("partial_tp_2_done"):
        lines.append(
            f"💰 Partial 2: <b>{PARTIAL_TP_2_PERCENT}%</b> @ "
            f"{format_price(trade.get('partial_tp_2_price'))}"
        )
    return lines


def trade_close_message(trade):
    rt = trade.get("result", trade.get("status"))
    if rt == "TP":
        icon, title = "✅", "TP ДОСТИГНУТ"
    elif rt == "SL":
        icon, title = "❌", "SL ДОСТИГНУТ"
    else:
        icon, title = "⚪", "РЕЗУЛЬТАТ НЕОПРЕДЕЛЁН"

    pnl = trade.get("pnl_percent")
    pnl_text = f"{float(pnl):+.2f}%" if pnl is not None else "N/A"

    partial_lines = _partials_text(trade)
    extra = ""
    if partial_lines:
        extra += "\n" + "\n".join(partial_lines)
    if trade.get("trailing_active"):
        extra += "\n🎯 Trailing: <b>ON</b>"

    cd_notice = ""
    if rt == "SL" and COOLDOWN_AFTER_SL_ENABLED and COOLDOWN_AFTER_SL_HOURS > 0:
        cd_notice = (f"\n\n❄️ <b>{trade.get('coin')} в cooldown "
                     f"на {COOLDOWN_AFTER_SL_HOURS}h</b>")

    return (
        f"{icon} <b>TRADEMIND — {title}</b>\n\n"
        f"💠 <b>{escape(str(trade.get('coin')))}</b>\n"
        f"📐 {escape(str(trade.get('direction')))}\n\n"
        f"Entry: <b>{format_price(trade.get('entry'))}</b>\n"
        f"Exit: <b>{format_price(trade.get('exit_price'))}</b>\n"
        f"SL: <b>{format_price(trade.get('sl'))}</b>\n"
        f"TP: <b>{format_price(trade.get('tp'))}</b>"
        f"{tp_source_label(trade.get('tp_source'))}\n\n"
        f"📊 RR: <b>{format_rr(trade.get('rr'))}</b>\n"
        f"📈 PnL: <b>{pnl_text}</b>"
        f"{extra}"
        f"{cd_notice}"
    )


async def safe_send_message(app, chat_id, text, **kwargs):
    for attempt in range(3):
        try:
            return await app.bot.send_message(chat_id=chat_id,
                                              text=text, **kwargs)
        except Exception as exc:
            if attempt == 2:
                print("SEND FAILED:", exc)
                raise
            await asyncio.sleep(1.5 * (attempt + 1))


async def safe_send_photo(app, chat_id, photo, **kwargs):
    for attempt in range(3):
        try:
            return await app.bot.send_photo(chat_id=chat_id,
                                            photo=photo, **kwargs)
        except Exception as exc:
            if attempt == 2:
                print("SEND PHOTO FAILED:", exc)
                raise
            await asyncio.sleep(1.5 * (attempt + 1))


async def monitor_active_trades(app, results):
    active = load_active_trades()
    if not active:
        return

    active_snapshot = [dict(t) for t in active]
    to_close = []

    for trade in active_snapshot:
        if trade.get("status") != "OPEN":
            continue
        coin = trade.get("coin")
        result = results.get(coin)
        if not result or result.get("error"):
            continue

        current_price = result.get("price")
        if current_price is None:
            continue
        try:
            current_price = float(current_price)
        except Exception:
            continue

        previous_price = float(trade.get("last_price", trade.get("entry")))
        previous_check_ms = int(
            trade.get("last_check_ms", trade.get("opened_at_ms", now_ms())))
        current_check_ms = now_ms()

        check = check_trade_price(trade, previous_price, current_price)

        if check is None:
            if TRAILING_ENABLED:
                apply_trailing(trade, current_price)
            trade["last_price"] = current_price
            trade["last_check_ms"] = current_check_ms
            continue

        result_type, exit_price = check
        if result_type == "AMBIGUOUS":
            resolved = resolve_crossed_levels_1m(
                trade, result.get("candles_1m", []),
                previous_check_ms, current_check_ms)
            if resolved == "NO_DATA":
                if TRAILING_ENABLED:
                    apply_trailing(trade, current_price)
                trade["last_price"] = current_price
                trade["last_check_ms"] = current_check_ms
                continue
            result_type = resolved

        to_close.append((trade, result_type, exit_price))

    save_active_trades([t for t in active_snapshot
                        if t.get("status") == "OPEN"])

    for trade, result_type, exit_price in to_close:
        closed = close_trade(trade, exit_price, result_type)
        if closed is None:
            continue
        chat_id = closed.get("chat_id")
        if not chat_id:
            continue
        coin = closed.get("coin")
        result = results.get(coin)

        try:
            await safe_send_message(
                app, chat_id, trade_close_message(closed),
                parse_mode="HTML",
                reply_markup=trade_close_keyboard(closed))
        except Exception as exc:
            print("TRADE CLOSE MESSAGE ERROR:", exc)

        try:
            if result:
                await send_chart_to_chat(app, chat_id, coin,
                                          result=result, trade=closed)
        except Exception as exc:
            print("TRADE CLOSE CHART ERROR:", exc)


def trade_close_keyboard(trade):
    tid = trade.get("id", "")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 График",
                              callback_data=f"active_trade_{tid}")],
        [InlineKeyboardButton("📒 Журнал", callback_data="journal"),
         InlineKeyboardButton("🧠 Dashboard", callback_data="dashboard")],
    ])


def active_message(chat_id):
    trades = user_active_trades(chat_id)
    if not trades:
        return ("📌 <b>АКТИВНЫХ СДЕЛОК НЕТ</b>\n\n"
                "Нажми «🟢 Я ЗАШЁЛ» на READY-сигнале.")

    lines = ["📌 <b>ACTIVE TRADES</b>", ""]
    for trade in trades:
        coin = trade.get("coin")
        d = trade.get("direction")
        curr = trade.get("last_price", trade.get("entry"))
        entry = float(trade.get("entry"))
        pnl = calculate_pnl_percent(entry, curr, d)
        pnl_text = f"{pnl:+.2f}%" if pnl is not None else "N/A"

        lines.extend([
            f"💠 <b>{escape(str(coin))}</b>",
            f"📐 {direction_icon(d)} <b>{d}</b>",