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
# CONFIG
# ============================================================

TOKEN = os.getenv("BOT_TOKEN")

CHECK_INTERVAL = 15
SCAN_WORKERS = 12

MIN_SCORE_READY = 88
MIN_RR = 2.0

SCAN_CACHE_TTL = 5.0

RUN_BACKTEST_ON_START = True
BACKTEST_SYMBOL = "SOLUSDT"
BACKTEST_MULTI = True
BACKTEST_MAX_HOURS = 24

# === v8.3.2 Position Management (R-based) ===
TRAILING_ENABLED = True
BREAKEVEN_TRIGGER_R = 1.0
PARTIAL_TP_ENABLED = True
PARTIAL_TP_TRIGGER_R = 1.0
PARTIAL_TP_PERCENT = 50
TRAILING_TRIGGER_R = 1.5
TRAILING_DISTANCE_R = 1.0

BREAKEVEN_TRIGGER_PCT = 1.0
TRAILING_TRIGGER_PCT = 4.0
TRAILING_DISTANCE_PCT = 2.0

BLOCK_CONFLICTING_TRADES = True

# === Cooldown after SL ===
COOLDOWN_AFTER_SL_ENABLED = True
COOLDOWN_AFTER_SL_HOURS = 6
COOLDOWN_AFTER_TP_HOURS = 0

# === v8.3.2 Notification dedup ===
NOTIFICATION_DEDUP_HOURS = 3
NOTIFICATION_ENTRY_TOLERANCE_PCT = 0.5


COINS = {
    "BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT",
    "BNB": "BNBUSDT", "XRP": "XRPUSDT", "ADA": "ADAUSDT",
    "LINK": "LINKUSDT", "DOT": "DOTUSDT",
    "LTC": "LTCUSDT", "BCH": "BCHUSDT",
    "APT": "APTUSDT", "SUI": "SUIUSDT", "HYPE": "HYPEUSDT",
    "INJ": "INJUSDT",
    "TIA": "TIAUSDT",
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
    if not isinstance(data, list):
        return []
    return data


def save_subscribers(data):
    with _storage_lock:
        save_json(SUBSCRIBERS_FILE, data)


def is_subscribed(chat_id):
    return chat_id in subscribers()


def load_active_trades():
    data = load_json(ACTIVE_TRADES_FILE, [])
    if not isinstance(data, list):
        return []
    return data


def save_active_trades(trades):
    with _storage_lock:
        save_json(ACTIVE_TRADES_FILE, trades)


def user_active_trades(chat_id):
    return [
        trade
        for trade in load_active_trades()
        if trade.get("status") == "OPEN"
        and trade.get("chat_id") == chat_id
    ]


def load_journal():
    data = load_json(TRADE_JOURNAL_FILE, [])
    if not isinstance(data, list):
        return []
    return data


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
    if not isinstance(data, dict):
        return {}
    return data


def save_pending_setups(data):
    with _storage_lock:
        save_json(PENDING_SETUPS_FILE, data)


def load_notification_state():
    data = load_json(NOTIFICATION_STATE_FILE, {})
    if not isinstance(data, dict):
        return {}
    return data


def save_notification_state(data):
    with _storage_lock:
        save_json(NOTIFICATION_STATE_FILE, data)


# ============================================================
# v8.3.2 NOTIFICATION DEDUP
# ============================================================

def _entry_bucket(entry, tolerance_pct=NOTIFICATION_ENTRY_TOLERANCE_PCT):
    """Округляет entry до bucket (0.5% от цены)."""
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
    """
    True если сигнал по этой монете + направлению + близкой цене
    уже отправлялся в последние NOTIFICATION_DEDUP_HOURS часов.
    """
    prev = notification_state.get(coin)
    if not isinstance(prev, dict):
        # старый формат (строка) или пусто — не дубликат
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
# COOLDOWN
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
        lines.append(
            f"{stage_icon(stage)} <b>{coin}</b> {price} "
            f"{direction_icon(direction)} {direction} "
            f"<code>{score}/100</code>{fvg_tag}{cd_tag}"
        )

    trailing_label = "ON" if TRAILING_ENABLED else "OFF"
    short_label = "ON" if ALLOW_SHORT else "OFF"

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "🧭 <b>СТРАТЕГИЯ 8.3</b>",
        "",
        "Entry = ILM trigger (retest)",
        "SL = ATR scaling + structural",
        "TP = RR 1:2 (fixed)",
        "",
        "⚡ Trend ≥ 0.40",
        "🎯 BOS обязателен",
        f"🎯 Trailing: <b>{trailing_label}</b>",
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
        lines.extend([
            f"💠 <b>{coin}</b>{cd_tag}",
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
            "entry_source": "TradeMind 8.3 ILM trigger",
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

    extra = ""
    if trade.get("partial_tp_done"):
        extra += (f"\n💰 Partial TP: <b>50%</b> @ "
                  f"{format_price(trade.get('partial_tp_price'))}")
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
            f"📐 {direction_icon(d)} <b>{d}</b>", "",
            f"💰 Entry: <b>{format_price(entry)}</b>",
            f"📍 Price: <b>{format_price(curr)}</b>",
            f"🛑 SL: <b>{format_price(trade.get('sl'))}</b>",
            f"🎯 TP: <b>{format_price(trade.get('tp'))}</b>",
            f"📊 RR: <b>{format_rr(trade.get('rr'))}</b>",
            f"📈 PnL: <b>{pnl_text}</b>",
        ])

        if trade.get("partial_tp_done"):
            lines.append(
                f"💰 Partial TP: <b>50%</b> @ "
                f"{format_price(trade.get('partial_tp_price'))}"
            )
        if trade.get("trailing_active"):
            lines.append("🎯 Trailing: <b>ON</b>")

        lines.extend(["", "━━━━━━━━━━━━━━━━━━━━", ""])

    return "\n".join(lines)


def active_keyboard(chat_id):
    trades = user_active_trades(chat_id)
    rows = []
    for trade in trades:
        tid = trade.get("id")
        rows.append([InlineKeyboardButton(
            f"📈 {trade.get('coin')} {trade.get('direction')}",
            callback_data=f"active_trade_{tid}")])
    rows.extend([
        [InlineKeyboardButton("🔄 Обновить", callback_data="active")],
        [InlineKeyboardButton("📒 Журнал", callback_data="journal"),
         InlineKeyboardButton("🧠 Dashboard", callback_data="dashboard")],
    ])
    return InlineKeyboardMarkup(rows)


def journal_message(chat_id):
    journal = user_journal(chat_id)
    if not journal:
        return "📒 <b>ЖУРНАЛ ПУСТ</b>"

    total = len(journal)
    tp = len([x for x in journal if x.get("result") == "TP"])
    sl = len([x for x in journal if x.get("result") == "SL"])
    ambiguous = len([x for x in journal if x.get("result") == "AMBIGUOUS"])
    resolved = tp + sl
    win_rate = (tp / resolved * 100) if resolved else 0
    pnl_values = [float(x["pnl_percent"]) for x in journal
                  if x.get("pnl_percent") is not None]
    total_pnl = sum(pnl_values)

    lines = [
        "📒 <b>TRADEMIND JOURNAL</b>", "",
        "━━━━━━━━━━━━━━━━━━━━", "",
        f"📊 Сделок: <b>{total}</b>",
        f"✅ TP: <b>{tp}</b>",
        f"❌ SL: <b>{sl}</b>",
        f"⚪ Ambiguous: <b>{ambiguous}</b>",
        f"🎯 Win rate: <b>{win_rate:.1f}%</b>",
        f"📈 Sum PnL: <b>{total_pnl:+.2f}%</b>",
        "", "━━━━━━━━━━━━━━━━━━━━", "",
        "<b>Последние сделки:</b>", "",
    ]

    for trade in reversed(journal[-10:]):
        rt = trade.get("result", "?")
        icon = {"TP": "✅", "SL": "❌", "AMBIGUOUS": "⚪"}.get(rt, "❔")
        pnl = trade.get("pnl_percent")
        pnl_text = f"{float(pnl):+.2f}%" if pnl is not None else "N/A"
        lines.append(
            f"{icon} <b>{escape(str(trade.get('coin')))}</b> "
            f"{trade.get('direction')} • {rt} • {pnl_text}")
    return "\n".join(lines)


def journal_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data="journal")],
        [InlineKeyboardButton("📌 Active", callback_data="active"),
         InlineKeyboardButton("🧠 Dashboard", callback_data="dashboard")],
    ])


def ready_message(coin, result, setup):
    fvg_bonus = setup.get("fvg_bonus", 0)
    fvg_tag = f"\n⚡ FVG bonus: <b>+{fvg_bonus}</b>" if fvg_bonus else ""
    bos_tag = " ✅" if result.get("bos") else " —"

    return (
        "🚨 <b>TRADEMIND — READY</b>\n\n"
        f"💠 <b>{escape(str(coin))}</b>\n"
        f"📐 {direction_icon(result.get('direction'))} "
        f"<b>{result.get('direction')}</b>\n"
        f"⭐ Score: <b>{result.get('score', 0)}/100</b>{fvg_tag}\n"
        f"📅 D1: <b>{result.get('d1_trend', 'NEUTRAL')}</b>\n"
        f"🎯 BOS: <b>{bos_tag}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "💰 <b>ТОЧКА ВХОДА (лимит)</b>\n\n"
        f"Entry: <b>{format_price(setup.get('entry'))}</b>\n"
        f"SL: <b>{format_price(setup.get('sl'))}</b>\n"
        f"🎯 Risk: <b>{_risk_pct(setup)}</b>\n"
        f"TP: <b>{format_price(setup.get('tp'))}</b>"
        f"{tp_source_label(setup.get('tp_source'))}\n"
        f"RR: <b>{format_rr(setup.get('rr'))}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🟢 <b>СТАВЬ ЛИМИТКУ НА ENTRY</b>\n\n"
        "Если зашёл — нажми «🟢 Я ЗАШЁЛ»."
    )


def ready_keyboard(setup):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Я ЗАШЁЛ",
                              callback_data=f"enter_{setup['id']}")],
        [InlineKeyboardButton("📈 График",
                              callback_data=f"chart_{setup['coin']}"),
         InlineKeyboardButton("💠 Карточка",
                              callback_data=f"coin_{setup['coin']}")],
    ])


async def send_ready_chart(app, chat_id, coin, result, setup):
    caption = ready_message(coin, result, setup)
    try:
        image = await asyncio.to_thread(chart_png, result, None)
        image.seek(0)
        await safe_send_photo(
            app, chat_id,
            InputFile(image, filename=f"{coin.lower()}_ready.png"),
            caption=caption, parse_mode="HTML",
            reply_markup=ready_keyboard(setup))
    except Exception as exc:
        print("READY CHART ERROR:", exc)
        try:
            await safe_send_message(app, chat_id, caption,
                                    parse_mode="HTML",
                                    reply_markup=ready_keyboard(setup))
        except Exception:
            pass


async def send_photo_to_chat(app, chat_id, coin, result,
                             trade=None, reply_markup=None):
    image = await asyncio.to_thread(chart_png, result, trade)
    image.seek(0)

    if trade:
        curr = (trade.get("last_price")
                if trade.get("status") == "OPEN"
                else trade.get("exit_price"))
        pnl = calculate_pnl_percent(trade.get("entry"), curr,
                                    trade.get("direction"))
        caption = (
            f"💠 <b>{escape(str(coin))}</b>\n\n"
            f"📐 {direction_icon(trade.get('direction'))} "
            f"<b>{trade.get('direction')}</b>\n\n"
            f"Entry: <b>{format_price(trade.get('entry'))}</b>\n"
            f"SL: <b>{format_price(trade.get('sl'))}</b>\n"
            f"TP: <b>{format_price(trade.get('tp'))}</b>\n"
            f"RR: <b>{format_rr(trade.get('rr'))}</b>"
        )
        if trade.get("status") != "OPEN":
            caption += f"\nExit: <b>{format_price(trade.get('exit_price'))}</b>"
        if pnl is not None:
            caption += f"\nPnL: <b>{pnl:+.2f}%</b>"
    else:
        caption = coin_message(coin, result)

    if reply_markup is None:
        reply_markup = chart_keyboard()

    await safe_send_photo(
        app, chat_id,
        InputFile(image, filename=f"{coin.lower()}_trademind.png"),
        caption=caption, parse_mode="HTML",
        reply_markup=reply_markup)


async def send_chart_to_chat(app, chat_id, coin, result=None, trade=None):
    try:
        if result is None:
            result = await asyncio.to_thread(build_analysis, COINS[coin])
        await send_photo_to_chat(app, chat_id, coin, result, trade=trade)
    except Exception as exc:
        print("CHART SEND ERROR:", exc)


async def send_chart(message, coin, result=None, trade=None):
    try:
        if result is None:
            result = await asyncio.to_thread(build_analysis, COINS[coin])
        image = await asyncio.to_thread(chart_png, result, trade)
        image.seek(0)

        if trade:
            curr = (trade.get("last_price")
                    if trade.get("status") == "OPEN"
                    else trade.get("exit_price"))
            pnl = calculate_pnl_percent(trade.get("entry"), curr,
                                        trade.get("direction"))
            caption = (
                f"💠 <b>{escape(str(coin))}</b>\n\n"
                f"📐 {direction_icon(trade.get('direction'))} "
                f"<b>{trade.get('direction')}</b>\n\n"
                f"Entry: <b>{format_price(trade.get('entry'))}</b>\n"
                f"SL: <b>{format_price(trade.get('sl'))}</b>\n"
                f"TP: <b>{format_price(trade.get('tp'))}</b>\n"
                f"RR: <b>{format_rr(trade.get('rr'))}</b>"
            )
            if pnl is not None:
                caption += f"\nPnL: <b>{pnl:+.2f}%</b>"
        else:
            caption = coin_message(coin, result)

        await message.reply_photo(
            photo=InputFile(image,
                            filename=f"{coin.lower()}_trademind.png"),
            caption=caption, parse_mode="HTML",
            reply_markup=chart_keyboard())
    except Exception as exc:
        await message.reply_text(f"❌ Ошибка графика: {escape(str(exc))}")


async def broadcast_ready(app, coin, result, setup):
    ids = subscribers()
    if not ids:
        return

    async def send(chat_id):
        try:
            await send_ready_chart(app, chat_id, coin, result, setup)
        except Exception as exc:
            print("BROADCAST ERROR:", chat_id, exc)

    await asyncio.gather(*(send(cid) for cid in ids),
                         return_exceptions=True)


# ============================================================
# PNG
# ============================================================

def png_chunk(chunk_type, data):
    return (struct.pack(">I", len(data)) + chunk_type + data
            + struct.pack(">I",
                          zlib.crc32(chunk_type + data) & 0xffffffff))


def make_png(width, height, pixels):
    raw = b"".join(b"\0" + bytes(row) for row in pixels)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR",
                    struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(raw, 6))
        + png_chunk(b"IEND", b"")
    )


def create_canvas(width, height):
    bg = (14, 18, 24)
    return [bytearray(bg * width) for _ in range(height)]


def put_pixel(pixels, x, y, color):
    if y < 0 or y >= len(pixels):
        return
    w = len(pixels[0]) // 3
    if x < 0 or x >= w:
        return
    i = x * 3
    pixels[y][i:i + 3] = bytes(color)


def draw_line(pixels, x1, y1, x2, y2, color, thickness=1):
    steps = max(abs(x2 - x1), abs(y2 - y1), 1)
    for i in range(steps + 1):
        x = int(x1 + (x2 - x1) * i / steps)
        y = int(y1 + (y2 - y1) * i / steps)
        for dx in range(-thickness // 2, thickness // 2 + 1):
            for dy in range(-thickness // 2, thickness // 2 + 1):
                put_pixel(pixels, x + dx, y + dy, color)


def draw_marker(pixels, x, y, color, radius=7):
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            if dx * dx + dy * dy <= radius * radius:
                put_pixel(pixels, x + dx, y + dy, color)


def fill_rect(pixels, x1, y1, x2, y2, color):
    x1 = max(0, int(x1))
    x2 = min(len(pixels[0]) // 3 - 1, int(x2))
    y1 = max(0, int(y1))
    y2 = min(len(pixels) - 1, int(y2))
    for y in range(min(y1, y2), max(y1, y2) + 1):
        row = pixels[y]
        for x in range(min(x1, x2), max(x1, x2) + 1):
            i = x * 3
            row[i:i + 3] = bytes(color)


def chart_png(result, trade=None):
    candles = result.get("candles_5m", [])[-100:]
    levels = strong_levels(result.get("major_levels", []))
    fvgs = result.get("fvgs", [])

    values = []
    for c in candles:
        try:
            values.extend([float(c["high"]), float(c["low"])])
        except Exception:
            continue
    for lvl in levels:
        try:
            values.append(float(lvl["price"]))
        except Exception:
            pass
    sweep = result.get("sweep")
    if sweep:
        for k in ("level", "extreme"):
            try:
                values.append(float(sweep[k]))
            except Exception:
                pass
    for k in ("price", "entry", "sl", "tp", "exit_price"):
        if result.get(k) is not None:
            try:
                values.append(float(result[k]))
            except Exception:
                pass
    d1_context = result.get("d1_context") or {}
    for k in ("point_a", "point_b"):
        v = d1_context.get(k)
        if v is not None:
            try:
                values.append(float(v))
            except Exception:
                pass
    for f in fvgs:
        try:
            values.extend([float(f["top"]), float(f["bottom"])])
        except Exception:
            pass
    if trade:
        for k in ("entry", "sl", "tp", "exit_price", "last_price"):
            if trade.get(k) is not None:
                try:
                    values.append(float(trade[k]))
                except Exception:
                    pass

    if not values:
        cur = float(result.get("price", 1))
        values = [cur - 1, cur + 1]

    low = min(values)
    high = max(values)
    padding = (high - low) * 0.08 or 1
    low -= padding
    high += padding

    width = 1200
    height = 680
    left = 55
    right = 35
    top = 65
    bottom = 45
    chart_width = width - left - right
    chart_height = height - top - bottom

    pixels = create_canvas(width, height)

    def y(value):
        return int(top + (high - value) / (high - low) * chart_height)

    stage = result.get("stage", "WAIT")
    stage_colors = {
        "READY": (60, 220, 140), "SWEPT": (255, 165, 45),
        "15M_CONFIRMED": (245, 205, 60), "WAIT": (90, 100, 115),
    }
    fill_rect(pixels, 0, 0, width, 10,
              stage_colors.get(stage, (90, 100, 115)))

    grid_color = (42, 48, 58)
    for i in range(1, 9):
        yy = top + chart_height * i // 9
        draw_line(pixels, left, yy, width - right, yy, grid_color, 1)

    for fvg in fvgs:
        try:
            t = float(fvg["top"])
            b = float(fvg["bottom"])
        except Exception:
            continue
        zone_color = ((20, 45, 32) if fvg["type"] == "bullish"
                      else (45, 22, 28))
        fill_rect(pixels, left, y(t), width - right, y(b), zone_color)

    for level in levels:
        try:
            lp = float(level["price"])
            zl = float(level.get("zone_low", lp * 0.998))
            zh = float(level.get("zone_high", lp * 1.002))
        except Exception:
            continue
        if level.get("type") == "BSL":
            zone_color = (65, 30, 38)
            line_color = (235, 80, 90)
        else:
            zone_color = (25, 60, 42)
            line_color = (50, 210, 130)
        fill_rect(pixels, left, y(zh), width - right, y(zl), zone_color)
        draw_line(pixels, left, y(lp), width - right, y(lp), line_color, 2)

    if candles:
        spacing = chart_width / max(len(candles), 1)
        candle_width = max(3, int(spacing * 0.58))
        for i, candle in enumerate(candles):
            try:
                o = float(candle["open"])
                h = float(candle["high"])
                l = float(candle["low"])
                c = float(candle["close"])
            except Exception:
                continue
            x = int(left + (i + 0.5) * spacing)
            color = (55, 205, 125) if c >= o else (230, 80, 90)
            draw_line(pixels, x, y(h), x, y(l), color, 1)
            bt = min(y(o), y(c))
            bb = max(y(o), y(c))
            if bb <= bt:
                bb = bt + 1
            for xx in range(x - candle_width // 2,
                            x + candle_width // 2 + 1):
                for yy in range(bt, bb + 1):
                    put_pixel(pixels, xx, yy, color)

    cur_price = result.get("price")
    if cur_price is not None:
        try:
            cur_price = float(cur_price)
            draw_line(pixels, left, y(cur_price), width - right,
                      y(cur_price), (80, 170, 255), 2)
            draw_marker(pixels, width - right - 8, y(cur_price),
                        (80, 170, 255), 8)
        except Exception:
            pass

    if sweep:
        for k in ("level", "extreme"):
            try:
                v = float(sweep[k])
                draw_line(pixels, left, y(v), width - right, y(v),
                          (255, 165, 40), 3)
                draw_marker(pixels, left + 15, y(v), (255, 165, 40), 6)
            except Exception:
                continue

    for k, color in (("point_a", (200, 130, 255)),
                     ("point_b", (130, 200, 255))):
        v = d1_context.get(k)
        if v is None:
            continue
        try:
            v = float(v)
            draw_line(pixels, left, y(v), width - right, y(v), color, 2)
        except Exception:
            continue

    trade_source = trade or result
    trade_colors = {
        "entry": (255, 215, 70), "sl": (235, 80, 90),
        "tp": (80, 220, 150), "exit_price": (180, 100, 255),
    }
    for k, color in trade_colors.items():
        v = trade_source.get(k)
        if v is None:
            continue
        try:
            v = float(v)
            draw_line(pixels, left, y(v), width - right, y(v), color, 3)
            draw_marker(pixels, width - right - 10, y(v), color, 8)
        except Exception:
            continue

    return io.BytesIO(make_png(width, height, pixels))


# ============================================================
# HANDLERS
# ============================================================

async def start(update, context):
    results = await asyncio.to_thread(scan_all)
    await update.message.reply_text(
        dashboard_message(results, update.effective_chat.id),
        parse_mode="HTML",
        reply_markup=dashboard_keyboard())


async def dashboard_cmd(update, context):
    results = await asyncio.to_thread(scan_all)
    await update.message.reply_text(
        dashboard_message(results, update.effective_chat.id),
        parse_mode="HTML",
        reply_markup=dashboard_keyboard())


async def market_cmd(update, context):
    await dashboard_cmd(update, context)


async def search_cmd(update, context):
    results = await asyncio.to_thread(scan_all)
    ready_items = []
    for coin, result in results.items():
        if result.get("error"):
            continue
        if (result.get("stage") == "READY"
                and result.get("score", 0) >= MIN_SCORE_READY
                and result.get("rr") is not None
                and float(result.get("rr")) >= MIN_RR):
            in_cd, _ = coin_in_cooldown(coin)
            if in_cd:
                continue
            setup = save_ready_setup(coin, result)
            if setup:
                ready_items.append((result.get("score", 0),
                                    coin, result, setup))

    if not ready_items:
        await update.message.reply_text(
            "🔎 <b>READY СЕТАПОВ НЕТ</b>",
            parse_mode="HTML",
            reply_markup=dashboard_keyboard())
        return

    ready_items.sort(key=lambda x: x[0], reverse=True)
    for _, coin, result, setup in ready_items[:5]:
        await update.message.reply_text(
            ready_message(coin, result, setup),
            parse_mode="HTML",
            reply_markup=ready_keyboard(setup))


async def chart_cmd(update, context):
    coin = "SOL"
    if context.args and context.args[0].upper() in COINS:
        coin = context.args[0].upper()
    await send_chart(update.message, coin)


async def sub_cmd(update, context):
    chat_id = update.effective_chat.id
    with _storage_lock:
        data = subscribers()
        if chat_id not in data:
            data.append(chat_id)
            save_subscribers(data)
    await update.message.reply_text(
        "🔔 <b>УВЕДОМЛЕНИЯ ВКЛЮЧЕНЫ</b>\n\nТолько READY-сетапы.",
        parse_mode="HTML",
        reply_markup=dashboard_keyboard())


async def unsub_cmd(update, context):
    chat_id = update.effective_chat.id
    with _storage_lock:
        save_subscribers([x for x in subscribers() if x != chat_id])
    await update.message.reply_text(
        "🔕 <b>УВЕДОМЛЕНИЯ ВЫКЛЮЧЕНЫ</b>",
        parse_mode="HTML",
        reply_markup=dashboard_keyboard())


async def active_cmd(update, context):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        active_message(chat_id),
        parse_mode="HTML",
        reply_markup=active_keyboard(chat_id))


async def journal_cmd(update, context):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        journal_message(chat_id),
        parse_mode="HTML",
        reply_markup=journal_keyboard())


async def status_cmd(update, context):
    active = load_active_trades()
    active_count = len([x for x in active if x.get("status") == "OPEN"])
    journal = load_journal()

    trailing_label = "ON" if TRAILING_ENABLED else "OFF"
    partial_label = "ON" if PARTIAL_TP_ENABLED else "OFF"
    short_label = "ON" if ALLOW_SHORT else "OFF"
    cd_label = (f"{COOLDOWN_AFTER_SL_HOURS}h"
                if COOLDOWN_AFTER_SL_ENABLED else "OFF")

    await update.message.reply_text(
        (f"⚙️ <b>TRADEMIND STATUS</b>\n\n"
         f"Version: <b>{escape(str(STRATEGY_VERSION))}</b>\n"
         f"Scanner: <b>{CHECK_INTERVAL}s</b>\n"
         f"Workers: <b>{SCAN_WORKERS}</b>\n"
         f"Coins: <b>{len(COINS)}</b>\n"
         f"Active: <b>{active_count}</b>\n"
         f"Journal: <b>{len(journal)}</b>\n\n"
         "━━━━━━━━━━━━━━━━━━━━\n\n"
         "🎯 <b>МОДЕЛЬ 8.3</b>\n"
         "Entry = ILM trigger\n"
         "SL = ATR scaling + structural\n"
         "TP = RR 1:2 (fixed)\n\n"
         "📅 D1 context\n"
         "💧 1H Major + 15M + ROUND + FRESH\n"
         "💠 FVG\n"
         "⚡ Trend ≥ 0.40\n"
         "🎯 BOS обязателен\n"
         f"🎯 Trailing: <b>{trailing_label}</b>\n"
         f"💰 Partial TP: <b>{partial_label}</b>\n"
         f"❄️ Cooldown after SL: <b>{cd_label}</b>\n"
         f"📈 SHORT: <b>{short_label}</b>\n\n"
         "🕐 Работаем 24/7"),
        parse_mode="HTML",
        reply_markup=dashboard_keyboard())


# ============================================================
# MONITOR
# ============================================================

async def _safe_monitor(app):
    try:
        await monitor(app)
    except Exception as exc:
        import traceback
        print("MONITOR FATAL CRASH:", exc, flush=True)
        traceback.print_exc()
        raise


async def monitor(app):
    notification_state = load_notification_state()

    while True:
        started = asyncio.get_running_loop().time()

        try:
            results = await asyncio.to_thread(scan_all)
            await monitor_active_trades(app, results)

            state_changed = False

            open_trades = [
                t for t in load_active_trades()
                if t.get("status") == "OPEN"
            ]

            for coin, result in results.items():
                if result.get("error"):
                    continue

                stage = result.get("stage", "WAIT")
                if stage != "READY":
                    continue

                score = int(result.get("score", 0))
                if score < MIN_SCORE_READY:
                    continue

                rr = result.get("rr")
                if rr is None:
                    continue
                try:
                    rr = float(rr)
                except Exception:
                    continue
                if rr < MIN_RR:
                    continue

                in_cd, cd_reason = coin_in_cooldown(coin)
                if in_cd:
                    print(
                        f"[SKIP-COOLDOWN] {coin} — {cd_reason}",
                        flush=True,
                    )
                    continue

                if BLOCK_CONFLICTING_TRADES:
                    conflicting = [
                        t for t in open_trades
                        if t.get("coin") == coin
                    ]

                    if conflicting:
                        sides = {t.get("direction") for t in conflicting}
                        new_dir = result.get("direction")

                        if new_dir not in sides:
                            print(
                                f"[SKIP-CONFLICT] {coin} "
                                f"уже есть OPEN {sides} — "
                                f"пропускаем {new_dir}",
                                flush=True,
                            )
                            continue
                        print(
                            f"[SKIP-DUP] {coin} "
                            f"уже есть OPEN {new_dir} — "
                            f"пропускаем дубликат",
                            flush=True,
                        )
                        continue

                setup = save_ready_setup(coin, result)
                if setup is None:
                    continue

                # === v8.3.2 DEDUP ===
                if _notification_is_duplicate(
                    notification_state, coin,
                    setup["direction"], setup["entry"]
                ):
                    print(
                        f"[SKIP-DUP-NOTIFY] {coin} "
                        f"{setup['direction']} @{setup['entry']} — "
                        f"уже отправляли <{NOTIFICATION_DEDUP_HOURS}h назад",
                        flush=True,
                    )
                    continue

                _notification_mark(
                    notification_state, coin,
                    setup["direction"], setup["entry"], setup["id"],
                )
                state_changed = True

                risk_info = result.get("sl_distance_pct")
                atr_info = result.get("atr_15m")
                src_info = result.get("sl_source")

                print(
                    f"[READY] {coin} {setup['direction']} "
                    f"entry={setup['entry']} sl={setup['sl']} "
                    f"tp={setup['tp']} "
                    f"risk={risk_info}% atr={atr_info} src={src_info} "
                    f"tp_src={setup.get('tp_source')} "
                    f"fvg={setup.get('fvg_bonus', 0)} "
                    f"rr={setup['rr']} score={setup['score']}",
                    flush=True,
                )

                await broadcast_ready(app, coin, result, setup)

            if state_changed:
                save_notification_state(notification_state)

        except Exception as exc:
            print("MONITOR ERROR:", exc, flush=True)

        elapsed = asyncio.get_running_loop().time() - started
        await asyncio.sleep(max(1, CHECK_INTERVAL - elapsed))


# ============================================================
# CALLBACKS
# ============================================================

async def edit_query(query, text, keyboard=None):
    try:
        await query.edit_message_text(text, parse_mode="HTML",
                                      reply_markup=keyboard)
        return True
    except Exception:
        return False


async def callbacks(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id

    if data in ("start", "dashboard"):
        results = await asyncio.to_thread(scan_all)
        text = dashboard_message(results, chat_id)
        if not await edit_query(query, text, dashboard_keyboard()):
            await query.message.reply_text(
                text, parse_mode="HTML",
                reply_markup=dashboard_keyboard())
        return

    if data == "market":
        results = await asyncio.to_thread(scan_all)
        text = dashboard_message(results, chat_id)
        if not await edit_query(query, text, dashboard_keyboard()):
            await query.message.reply_text(
                text, parse_mode="HTML",
                reply_markup=dashboard_keyboard())
        return

    if data == "filter_ready":
        results = await asyncio.to_thread(scan_all)
        text = ready_filter_message(results)
        await edit_query(query, text, ready_filter_keyboard(results))
        return

    if data == "search":
        results = await asyncio.to_thread(scan_all)
        ready = []
        for coin, result in results.items():
            if result.get("error"):
                continue
            if (result.get("stage") == "READY"
                    and result.get("score", 0) >= MIN_SCORE_READY
                    and result.get("rr") is not None
                    and float(result.get("rr")) >= MIN_RR):
                in_cd, _ = coin_in_cooldown(coin)
                if in_cd:
                    continue
                setup = save_ready_setup(coin, result)
                if setup:
                    ready.append((result.get("score", 0),
                                  coin, result, setup))
        if not ready:
            await edit_query(query, "🔎 <b>READY СЕТАПОВ НЕТ</b>",
                             dashboard_keyboard())
            return
        ready.sort(key=lambda x: x[0], reverse=True)
        _, coin, result, setup = ready[0]
        await edit_query(query, ready_message(coin, result, setup),
                         ready_keyboard(setup))
        return

    if data.startswith("coin_"):
        coin = data.split("_", 1)[1]
        if coin not in COINS:
            return
        result = await asyncio.to_thread(build_analysis, COINS[coin])
        setup = None
        in_cd, _ = coin_in_cooldown(coin)
        if (result.get("stage") == "READY"
                and result.get("score", 0) >= MIN_SCORE_READY
                and not in_cd):
            setup = save_ready_setup(coin, result)
        text = coin_message(coin, result)
        if not await edit_query(query, text,
                                coin_keyboard(coin, result, setup)):
            await query.message.reply_text(
                text, parse_mode="HTML",
                reply_markup=coin_keyboard(coin, result, setup))
        return

    if data.startswith("enter_"):
        setup_id = data.split("_", 1)[1]
        setup = load_pending_setups().get(setup_id)
        if not setup:
            await edit_query(query, "⚠️ <b>СИГНАЛ НЕ НАЙДЕН</b>",
                             dashboard_keyboard())
            return

        coin = setup.get("coin")
        in_cd, cd_reason = coin_in_cooldown(coin)
        if in_cd:
            await edit_query(
                query,
                (f"❄️ <b>COOLDOWN</b>\n\n"
                 f"💠 <b>{coin}</b>\n"
                 f"<i>{cd_reason}</i>\n\n"
                 f"Вход заблокирован после недавнего SL."),
                dashboard_keyboard())
            return

        trade, created = activate_trade(setup, chat_id)
        if not created:
            await edit_query(
                query,
                (f"🟢 <b>СДЕЛКА УЖЕ АКТИВНА</b>\n\n"
                 f"💠 {setup.get('coin')}\n"
                 f"📐 {setup.get('direction')}\n\n"
                 f"Entry: <b>{format_price(setup.get('entry'))}</b>\n"
                 f"SL: <b>{format_price(setup.get('sl'))}</b>\n"
                 f"TP: <b>{format_price(setup.get('tp'))}</b>\n"
                 f"RR: <b>{format_rr(setup.get('rr'))}</b>"),
                active_keyboard(chat_id))
            return
        await edit_query(
            query,
            (f"🟢 <b>СДЕЛКА ПРИНЯТА</b>\n\n"
             f"💠 <b>{trade.get('coin')}</b>\n"
             f"📐 {direction_icon(trade.get('direction'))} "
             f"<b>{trade.get('direction')}</b>\n\n"
             f"Entry: <b>{format_price(trade.get('entry'))}</b>\n"
             f"SL: <b>{format_price(trade.get('sl'))}</b>\n"
             f"TP: <b>{format_price(trade.get('tp'))}</b>\n"
             f"RR: <b>{format_rr(trade.get('rr'))}</b>\n\n"
             "📌 Snapshot сохранён.\n"
             "🎯 Следим до TP или SL\n"
             "💰 BE +1R · Partial 50% +1R · Trail +1.5R"),
            active_keyboard(chat_id))
        return

    if data == "charts":
        await edit_query(query, "📈 <b>ВЫБЕРИ МОНЕТУ</b>",
                         chart_keyboard())
        return

    if data.startswith("chart_"):
        coin = data.split("_", 1)[1]
        if coin not in COINS:
            return
        await send_chart(query.message, coin)
        return

    if data == "active":
        text = active_message(chat_id)
        if not await edit_query(query, text, active_keyboard(chat_id)):
            await query.message.reply_text(
                text, parse_mode="HTML",
                reply_markup=active_keyboard(chat_id))
        return

    if data.startswith("active_trade_"):
        trade_id = data.split("_", 2)[2]
        trades = user_active_trades(chat_id)
        trade = next((x for x in trades if x.get("id") == trade_id), None)
        if not trade:
            await edit_query(query, "⚠️ Сделка больше не активна.",
                             dashboard_keyboard())
            return
        coin = trade.get("coin", "SOL")
        result = await asyncio.to_thread(build_analysis, COINS[coin])
        trade["last_price"] = result.get("price", trade.get("last_price"))
        await send_chart(query.message, coin, result=result, trade=trade)
        return

    if data == "journal":
        text = journal_message(chat_id)
        if not await edit_query(query, text, journal_keyboard()):
            await query.message.reply_text(
                text, parse_mode="HTML",
                reply_markup=journal_keyboard())
        return

    if data == "notifications":
        if is_subscribed(chat_id):
            text = "🔔 <b>УВЕДОМЛЕНИЯ ВКЛЮЧЕНЫ</b>"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔕 Выключить",
                                      callback_data="unsubscribe")],
                [InlineKeyboardButton("⬅️ Dashboard",
                                      callback_data="dashboard")],
            ])
        else:
            text = "🔕 <b>УВЕДОМЛЕНИЯ ВЫКЛЮЧЕНЫ</b>"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔔 Включить",
                                      callback_data="subscribe")],
                [InlineKeyboardButton("⬅️ Dashboard",
                                      callback_data="dashboard")],
            ])
        await edit_query(query, text, kb)
        return

    if data == "subscribe":
        with _storage_lock:
            d = subscribers()
            if chat_id not in d:
                d.append(chat_id)
                save_subscribers(d)
        await edit_query(query,
                         "🔔 <b>READY-УВЕДОМЛЕНИЯ ВКЛЮЧЕНЫ</b>",
                         dashboard_keyboard())
        return

    if data == "unsubscribe":
        with _storage_lock:
            save_subscribers([x for x in subscribers() if x != chat_id])
        await edit_query(query, "🔕 <b>УВЕДОМЛЕНИЯ ВЫКЛЮЧЕНЫ</b>",
                         dashboard_keyboard())
        return

    if data == "status":
        active = load_active_trades()
        active_count = len([x for x in active if x.get("status") == "OPEN"])
        journal = load_journal()

        trailing_label = "ON" if TRAILING_ENABLED else "OFF"
        partial_label = "ON" if PARTIAL_TP_ENABLED else "OFF"
        short_label = "ON" if ALLOW_SHORT else "OFF"
        cd_label = (f"{COOLDOWN_AFTER_SL_HOURS}h"
                    if COOLDOWN_AFTER_SL_ENABLED else "OFF")

        await edit_query(
            query,
            (f"⚙️ <b>TRADEMIND STATUS</b>\n\n"
             f"Version: <b>{escape(str(STRATEGY_VERSION))}</b>\n"
             f"Scanner: <b>{CHECK_INTERVAL}s</b>\n"
             f"Workers: <b>{SCAN_WORKERS}</b>\n"
             f"Coins: <b>{len(COINS)}</b>\n"
             f"Active: <b>{active_count}</b>\n"
             f"Journal: <b>{len(journal)}</b>\n\n"
             "━━━━━━━━━━━━━━━━━━━━\n\n"
             "🎯 <b>МОДЕЛЬ 8.3</b>\n"
             "Entry = ILM trigger\n"
             "SL = ATR scaling + structural\n"
             "TP = RR 1:2 (fixed)\n\n"
             "📅 D1 context\n"
             "💧 1H Major + 15M + ROUND + FRESH\n"
             "💠 FVG\n"
             "⚡ Trend ≥ 0.40\n"
             "🎯 BOS обязателен\n"
             f"🎯 Trailing: <b>{trailing_label}</b>\n"
             f"💰 Partial TP: <b>{partial_label}</b>\n"
             f"❄️ Cooldown: <b>{cd_label}</b>\n"
             f"📈 SHORT: <b>{short_label}</b>\n\n"
             "🕐 Работаем 24/7"),
            dashboard_keyboard())
        return


# ============================================================
# POST INIT
# ============================================================

async def post_init(application):
    if RUN_BACKTEST_ON_START:
        try:
            print("=" * 70, flush=True)
            print("BACKTEST: запуск", flush=True)
            print("=" * 70, flush=True)

            import backtest

            if BACKTEST_MULTI:
                print(">>> BACKTEST 40d (v9.4 full) <<<", flush=True)
                backtest.run_multi_backtest_with_hours(
                    BACKTEST_MAX_HOURS,
                    use_breakeven=True,
                    use_partial_tp=True,
                    use_trailing=True,
                )
            else:
                trades, diag = backtest.run_backtest(
                    BACKTEST_SYMBOL, BACKTEST_MAX_HOURS,
                    use_breakeven=True,
                    use_partial_tp=True,
                    use_trailing=True,
                )
                backtest.print_report(
                    BACKTEST_SYMBOL, trades, diag,
                    use_breakeven=True,
                    use_partial_tp=True,
                    use_trailing=True,
                )

            print("=" * 70, flush=True)
            print("BACKTEST: завершён", flush=True)
            print("=" * 70, flush=True)

        except Exception as exc:
            import traceback
            print("BACKTEST ERROR:", exc, flush=True)
            traceback.print_exc()

    commands = [
        ("start", "TradeMind Dashboard"),
        ("market", "Рынок"),
        ("search", "Поиск READY"),
        ("chart", "График"),
        ("active", "Активные сделки"),
        ("journal", "Журнал"),
        ("status", "Статус"),
        ("subscribe", "Включить уведомления"),
        ("unsubscribe", "Выключить уведомления"),
    ]

    await application.bot.set_my_commands([
        BotCommand(cmd, desc) for cmd, desc in commands
    ])

    application.create_task(_safe_monitor(application))


# ============================================================
# MAIN
# ============================================================

def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN не найден")

    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    handlers = [
        ("start", start),
        ("market", market_cmd),
        ("search", search_cmd),
        ("chart", chart_cmd),
        ("active", active_cmd),
        ("journal", journal_cmd),
        ("status", status_cmd),
        ("subscribe", sub_cmd),
        ("unsubscribe", unsub_cmd),
    ]

    for cmd, handler in handlers:
        application.add_handler(CommandHandler(cmd, handler))

    application.add_handler(CallbackQueryHandler(callbacks))

    print(f"TradeMind {STRATEGY_VERSION} started (24/7)", flush=True)
    print(f"Monitoring {len(COINS)} coins", flush=True)
    print(f"Cooldown after SL: {COOLDOWN_AFTER_SL_HOURS}h", flush=True)
    print(f"Notification dedup: {NOTIFICATION_DEDUP_HOURS}h", flush=True)

    application.run_polling()


if __name__ == "__main__":
    main()