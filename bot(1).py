# -*- coding: utf-8 -*-
"""
TradeMind bot v9.19.3.
Fix: BE после P1 + Telegram-уведомления о P1/P2/BE.
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
from concurrent.futures import (
    ThreadPoolExecutor, as_completed
)
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


# --- CONFIG ---

TOKEN = os.getenv("BOT_TOKEN")

CHECK_INTERVAL = 60
SCAN_WORKERS = 12
MIN_RR = 2.0
SCAN_CACHE_TTL = 5.0

RUN_BACKTEST_ON_START = False
BACKTEST_SYMBOL = "INJUSDT"
BACKTEST_MULTI = True
BACKTEST_MAX_HOURS = 24

TRAILING_ENABLED = True
TRAILING_TRIGGER_R = 1.3
TRAILING_DISTANCE_R = 0.8

# v9.19.3: BE срабатывает только после partial_1
BREAKEVEN_TRIGGER_R = 0.7
PARTIAL_TP_ENABLED = True
PARTIAL_TP_TRIGGER_R = 0.7
PARTIAL_TP_PERCENT = 50
PARTIAL_TP_2_ENABLED = True
PARTIAL_TP_2_TRIGGER_R = 1.3
PARTIAL_TP_2_PERCENT = 25

BLOCK_CONFLICTING_TRADES = True

COOLDOWN_AFTER_SL_ENABLED = True
COOLDOWN_AFTER_SL_HOURS = 3
COOLDOWN_AFTER_TP_HOURS = 0

NOTIFICATION_DEDUP_HOURS = 3
NOTIFICATION_ENTRY_TOLERANCE_PCT = 0.5

COINS = {
    "BTC": "BTCUSDT",
    "XRP": "XRPUSDT",
    "LINK": "LINKUSDT",
    "BCH": "BCHUSDT",
    "APT": "APTUSDT",
    "SUI": "SUIUSDT",
    "INJ": "INJUSDT",
}

MIN_SCORE_MAP = {
    "default": 90,
    "INJUSDT": 88,
    "BCHUSDT": 92,
    "APTUSDT": 90,
}

MIN_SCORE_READY = 90

SUBSCRIBERS_FILE = "subscribers.json"
TRADE_JOURNAL_FILE = "trade_journal.json"
ACTIVE_TRADES_FILE = "active_trades.json"
PENDING_SETUPS_FILE = "pending_setups.json"
NOTIFICATION_STATE_FILE = "notification_state.json"

_storage_lock = threading.RLock()


def get_min_score(sym):
    if sym in MIN_SCORE_MAP:
        return MIN_SCORE_MAP[sym]
    return MIN_SCORE_MAP["default"]


def load_json(fn, default):
    try:
        f = open(fn, "r", encoding="utf-8")
        data = json.load(f)
        f.close()
        return data
    except Exception:
        return default


def save_json(fn, data):
    tmp = fn + ".tmp"
    try:
        f = open(tmp, "w", encoding="utf-8")
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
        f.close()
        os.replace(tmp, fn)
    except Exception as exc:
        print("SAVE ERROR:", fn, exc)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ",
                         time.gmtime())


def now_ms():
    return int(time.time() * 1000)


def subscribers():
    data = load_json(SUBSCRIBERS_FILE, [])
    if isinstance(data, list):
        return data
    return []


def save_subscribers(data):
    with _storage_lock:
        save_json(SUBSCRIBERS_FILE, data)


def is_subscribed(chat_id):
    return chat_id in subscribers()


def load_active_trades():
    data = load_json(ACTIVE_TRADES_FILE, [])
    if isinstance(data, list):
        return data
    return []


def save_active_trades(trades):
    with _storage_lock:
        save_json(ACTIVE_TRADES_FILE, trades)


def user_active_trades(chat_id):
    out = []
    for t in load_active_trades():
        if t.get("status") != "OPEN":
            continue
        if t.get("chat_id") != chat_id:
            continue
        out.append(t)
    return out


def load_journal():
    data = load_json(TRADE_JOURNAL_FILE, [])
    if isinstance(data, list):
        return data
    return []


def save_journal(journal):
    with _storage_lock:
        by_user = {}
        for entry in journal:
            cid = entry.get("chat_id", 0)
            if cid not in by_user:
                by_user[cid] = []
            by_user[cid].append(entry)
        trimmed = []
        for cid in by_user:
            entries = by_user[cid]
            trimmed.extend(entries[-500:])
        save_json(TRADE_JOURNAL_FILE, trimmed)


def user_journal(chat_id):
    out = []
    for t in load_journal():
        if t.get("chat_id") == chat_id:
            out.append(t)
    return out


def load_pending_setups():
    data = load_json(PENDING_SETUPS_FILE, {})
    if isinstance(data, dict):
        return data
    return {}


def save_pending_setups(data):
    with _storage_lock:
        save_json(PENDING_SETUPS_FILE, data)


def load_notification_state():
    data = load_json(NOTIFICATION_STATE_FILE, {})
    if isinstance(data, dict):
        return data
    return {}


def save_notification_state(data):
    with _storage_lock:
        save_json(NOTIFICATION_STATE_FILE, data)


# --- FORMATTERS ---

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


def calc_pnl(entry, exit_p, d):
    try:
        entry = float(entry)
        exit_p = float(exit_p)
        if entry <= 0:
            return None
        if d == "LONG":
            return (exit_p - entry) / entry * 100
        if d == "SHORT":
            return (entry - exit_p) / entry * 100
    except Exception:
        return None
    return None


def dir_icon(d):
    if d == "LONG":
        return "🟢"
    if d == "SHORT":
        return "🔴"
    return "⚪"


STAGE_ICONS = {
    "READY": "🟢", "SWEPT": "🟠",
    "15M_CONFIRMED": "🟡", "WAIT": "⏳",
}

STAGE_TEXTS = {
    "READY": "🟢 МОЖНО ВХОДИТЬ",
    "SWEPT": "🟠 SWEEP",
    "15M_CONFIRMED": "🟡 15M CONFIRMED",
    "WAIT": "⏳ ОЖИДАНИЕ",
}


def stage_icon(s):
    return STAGE_ICONS.get(s, "⏳")


def stage_text(s):
    return STAGE_TEXTS.get(s, "⏳ ОЖИДАНИЕ")


def tp_src_label(source):
    m = {
        "d1": " (D1)",
        "major": " (major)",
        "local": " (local)",
        "fixed_rr": " (RR 1:2)",
    }
    return m.get(source, "")


def strong_levels(levels):
    if not levels:
        return []
    strong = []
    for l in levels:
        try:
            if float(l.get("strength", 0)) >= 65:
                strong.append(l)
        except Exception:
            pass
    if strong:
        return strong[:8]
    return levels[:6]


def levels_text(levels, cur):
    levels = strong_levels(levels)
    if not levels:
        return "нет сильной major liquidity"
    lines = []
    for level in levels:
        try:
            lp = float(level["price"])
            d = abs(lp - cur) / cur * 100
        except Exception:
            continue
        lt = level.get("type", "LEVEL")
        src = level.get("source", "")
        icon = "🔴" if lt == "BSL" else "🟢"
        tag = f" [{src}]" if src else ""
        st = float(level.get("strength", 0))
        lines.append(
            f"{icon} <b>{lt}</b> "
            f"{format_price(lp)} • {d:.2f}% "
            f"• S{st:.0f}{tag}"
        )
    if lines:
        return "\n".join(lines)
    return "нет сильной major liquidity"


def fvgs_text(fvgs, cur, limit=4):
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
        mid = (top + bottom) / 2
        d = abs(mid - cur) / cur * 100
        lines.append(
            f"{icon} <b>{tf}</b> "
            f"{format_price(bottom)} – "
            f"{format_price(top)} • {d:.2f}%"
        )
    if lines:
        return "\n".join(lines)
    return "— нет незакрытых зон"


# --- NOTIFICATION DEDUP ---

def _entry_bucket(entry, tol=None):
    if tol is None:
        tol = NOTIFICATION_ENTRY_TOLERANCE_PCT
    try:
        e = float(entry)
        if e <= 0:
            return 0
        bs = e * tol / 100.0
        if bs <= 0:
            return 0
        return int(round(e / bs))
    except Exception:
        return 0


def _notif_is_dup(state, coin, d, entry):
    prev = state.get(coin)
    if not isinstance(prev, dict):
        return False
    if prev.get("direction") != d:
        return False
    ts = prev.get("ts", 0)
    elapsed = (time.time() - ts) / 3600.0
    if elapsed >= NOTIFICATION_DEDUP_HOURS:
        return False
    return prev.get("entry_bucket") == _entry_bucket(entry)


def _notif_mark(state, coin, d, entry, sid):
    state[coin] = {
        "direction": d,
        "entry_bucket": _entry_bucket(entry),
        "setup_id": sid,
        "entry": float(entry) if entry else None,
        "ts": time.time(),
    }


# --- COOLDOWN ---

def _recent_result_ms(coin, rtype):
    journal = load_journal()
    latest = None
    for trade in reversed(journal):
        if trade.get("coin") != coin:
            continue
        if trade.get("result") != rtype:
            continue
        closed = trade.get("closed_at_ms")
        if closed is None:
            continue
        if latest is None or closed > latest:
            latest = closed
    return latest


def coin_in_cooldown(coin):
    if not COOLDOWN_AFTER_SL_ENABLED:
        return False, None
    cur = now_ms()
    if COOLDOWN_AFTER_SL_HOURS > 0:
        last_sl = _recent_result_ms(coin, "SL")
        if last_sl is not None:
            eh = (cur - last_sl) / 3600000
            if eh < COOLDOWN_AFTER_SL_HOURS:
                rem = COOLDOWN_AFTER_SL_HOURS - eh
                return True, f"SL {eh:.1f}h ago ({rem:.1f}h left)"
    if COOLDOWN_AFTER_TP_HOURS > 0:
        last_tp = _recent_result_ms(coin, "TP")
        if last_tp is not None:
            eh = (cur - last_tp) / 3600000
            if eh < COOLDOWN_AFTER_TP_HOURS:
                rem = COOLDOWN_AFTER_TP_HOURS - eh
                return True, f"TP {eh:.1f}h ago ({rem:.1f}h left)"
    return False, None


# --- ANALYSIS ---

def build_analysis(symbol):
    market = get_market_data(symbol)
    price = market["price"]

    levels = find_major_liquidity(
        market["candles_1h"], price, 12,
        market["candles_15m"], market["candles_5m"],
        market["candles_1m"],
    )

    d = get_1h_direction(market["candles_1h"])
    sweep = None
    if d != "NEUTRAL":
        sweep = detect_sweep(
            market["candles_1h"], price, d, levels)

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
        "candles_1h": market["candles_1h"],
        "candles_15m": market["candles_15m"],
        "candles_5m": market["candles_5m"],
        "candles_1m": market["candles_1m"],
    })
    return result


_scan_cache = {"r": None, "t": 0.0}


def scan_one(item):
    coin, symbol = item
    try:
        return coin, build_analysis(symbol)
    except Exception as exc:
        return coin, {"error": str(exc),
                      "symbol": symbol}


def scan_all():
    now = time.time()
    if _scan_cache["r"] is not None:
        if now - _scan_cache["t"] < SCAN_CACHE_TTL:
            return _scan_cache["r"]

    results = {}
    with ThreadPoolExecutor(
            max_workers=SCAN_WORKERS) as ex:
        futs = []
        for item in COINS.items():
            futs.append(ex.submit(scan_one, item))
        for fut in as_completed(futs):
            coin, result = fut.result()
            results[coin] = result

    final = {}
    for coin in COINS:
        if coin in results:
            final[coin] = results[coin]
        else:
            final[coin] = {"error": "нет данных"}
    _scan_cache["r"] = final
    _scan_cache["t"] = now
    return final


# --- MESSAGES ---

def dashboard_message(results, chat_id=None):
    ready = 0
    swept = 0
    confirmed = 0
    waiting = 0
    active = 0
    if chat_id is not None:
        active = len(user_active_trades(chat_id))

    for r in results.values():
        s = r.get("stage")
        if s == "READY":
            ready += 1
        elif s == "SWEPT":
            swept += 1
        elif s == "15M_CONFIRMED":
            confirmed += 1
        else:
            waiting += 1

    cd_label = "ON"
    if not COOLDOWN_AFTER_SL_ENABLED:
        cd_label = "OFF"

    lines = [
        "🧠 <b>TRADEMIND v9.19.3</b>",
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
        f"❄️ Cooldown: <b>{cd_label} "
        f"{COOLDOWN_AFTER_SL_HOURS}h</b>",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"💠 Мониторинг: <b>{len(COINS)} монет</b>",
        "",
    ]

    for coin in COINS:
        r = results.get(coin, {})
        if r.get("error"):
            lines.append(f"⚫ <b>{coin}</b> — ERROR")
            continue

        in_cd, _ = coin_in_cooldown(coin)
        cd_tag = " ❄️" if in_cd else ""

        price = format_price(r.get("price"))
        d = r.get("direction", "NEUTRAL")
        stage = r.get("stage", "WAIT")
        score = r.get("score", 0)
        fvg_tag = ""
        if r.get("fvg_bonus", 0) > 0:
            fvg_tag = " ⚡"
        lines.append(
            f"{stage_icon(stage)} <b>{coin}</b> "
            f"{price} {dir_icon(d)} {d} "
            f"<code>{score}/100</code>{fvg_tag}{cd_tag}"
        )

    tr_label = "ON" if TRAILING_ENABLED else "OFF"
    sh_label = "ON" if ALLOW_SHORT else "OFF"

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "🧭 <b>СТРАТЕГИЯ 9.19.3</b>",
        "",
        "Entry = ILM trigger",
        "SL = structural + ATR",
        "TP = RR 1:2",
        "",
        f"💰 P1: {PARTIAL_TP_TRIGGER_R}R"
        f"/{PARTIAL_TP_PERCENT}%",
        f"💰 P2: {PARTIAL_TP_2_TRIGGER_R}R"
        f"/{PARTIAL_TP_2_PERCENT}%",
        f"🛡 BE: {BREAKEVEN_TRIGGER_R}R (после P1)",
        "",
        f"🎯 Trail: <b>{tr_label}</b>",
        f"📈 SHORT: <b>{sh_label}</b>",
        f"❄️ Cooldown: <b>{cd_label} "
        f"{COOLDOWN_AFTER_SL_HOURS}h</b>",
    ])
    return "\n".join(lines)


def dashboard_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить",
                              callback_data="dashboard")],
        [InlineKeyboardButton("🟢 READY",
                              callback_data="filter_ready"),
         InlineKeyboardButton("📌 ACTIVE",
                              callback_data="active")],
        [InlineKeyboardButton("📈 Графики",
                              callback_data="charts"),
         InlineKeyboardButton("📊 Рынок",
                              callback_data="market")],
        [InlineKeyboardButton("🔎 Сканер",
                              callback_data="search"),
         InlineKeyboardButton("📒 Журнал",
                              callback_data="journal")],
        [InlineKeyboardButton("🔔 Увед",
                              callback_data="notifications"),
         InlineKeyboardButton("⚙️ Статус",
                              callback_data="status")],
    ])


def checklist_text(result):
    d = result.get("direction", "NEUTRAL")
    stage = result.get("stage", "WAIT")
    price = result.get("price")

    if d in ("LONG", "SHORT"):
        step1 = f"✅ 1H: {d}"
    else:
        step1 = "⬜ 1H: NEUTRAL"

    levels = result.get("major_levels") or []
    exp_type = "SSL" if d == "LONG" else "BSL"

    target = None
    for lvl in levels:
        if lvl.get("type") == exp_type:
            target = lvl
            break

    if target is not None:
        step2 = (f"✅ Major {exp_type}: "
                 f"{format_price(target.get('price'))}")
    else:
        step2 = f"⬜ Major {exp_type}: нет"

    sweep = result.get("sweep")
    ilm = result.get("ilm")
    entry = result.get("entry")
    rr = result.get("rr")
    bos = result.get("bos", False)

    step3 = "✅ Sweep" if sweep else "⬜ Sweep"

    stage_rank = {"WAIT": 0, "SWEPT": 1,
                  "15M_CONFIRMED": 2,
                  "READY": 3}.get(stage, 0)

    bos_tag = " + BOS" if bos else ""
    if stage_rank >= 2:
        step4 = f"✅ 15M{bos_tag}"
    else:
        step4 = "⬜ 15M"
    step5 = "✅ 5M ILM" if ilm else "⬜ 5M ILM"
    if entry is not None and rr is not None:
        step6 = "✅ Entry / RR"
    else:
        step6 = "⬜ Entry / RR"

    lines = [
        "📋 <b>ПРОГРЕСС</b>", "",
        step1, step2, step3,
        step4, step5, step6,
    ]

    if stage == "WAIT":
        if target is not None:
            try:
                lp = float(target.get("price"))
                cp = float(price)
                dist = abs(lp - cp) / cp * 100
            except Exception:
                dist = 0.0
            lines.extend([
                "",
                f"🎯 Ждём <b>{exp_type}</b> "
                f"@ {format_price(target.get('price'))}",
                f"📏 До уровня: <b>{dist:.2f}%</b>",
            ])
        else:
            lines.extend([
                "",
                f"🎯 Ждём <b>{exp_type}</b>",
            ])
    elif stage == "SWEPT":
        lines.append("")
        lines.append("🎯 Ждём 15M")
    elif stage == "15M_CONFIRMED":
        if ilm:
            lines.extend([
                "",
                "🎯 Сетап есть, READY заблокирован",
            ])
        else:
            lines.append("")
            lines.append("🎯 Ждём 5M ILM")
    elif stage == "READY":
        lines.append("")
        lines.append("🎯 <b>READY</b>")

    return "\n".join(lines)


def coin_message(coin, result):
    if result.get("error"):
        return (f"❌ <b>{escape(coin)}</b>\n\n"
                f"{escape(str(result.get('error')))}")

    stage = result.get("stage", "WAIT")
    d = result.get("direction", "NEUTRAL")
    d1 = result.get("d1_trend", "NEUTRAL")
    trend = result.get("trend_activity", 0.0)
    score = result.get("score", 0)
    bos = result.get("bos", False)

    in_cd, cd_reason = coin_in_cooldown(coin)
    bos_tag = " ✅" if bos else " —"

    lines = [
        f"💠 <b>{escape(coin)}</b>",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    if in_cd:
        lines.extend(["",
                      f"❄️ <b>COOLDOWN:</b> {cd_reason}"])

    lines.extend([
        "",
        f"💰 Цена: "
        f"<b>{format_price(result.get('price'))}</b>",
        f"📐 1H: <b>{dir_icon(d)} {d}</b>",
        f"📅 D1: <b>{dir_icon(d1)} {d1}</b>",
        f"⚡ Trend: <b>{trend:.2f}</b>",
        f"🎯 BOS: <b>{bos_tag}</b>",
        f"⭐ Score: <b>{score}/100</b>",
        "",
        f"<b>{stage_text(stage)}</b>",
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        checklist_text(result),
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "💧 <b>MAJOR LIQUIDITY</b>",
        "",
        levels_text(result.get("major_levels"),
                    result.get("price")),
    ])

    fvgs = result.get("fvgs") or []
    if fvgs:
        lines.append("")
        lines.append("💠 <b>FVG</b>")
        lines.append(fvgs_text(fvgs,
                               result.get("price")))

    sweep = result.get("sweep")
    if sweep:
        lines.extend([
            "",
            "💧 <b>SWEEP</b>",
            f"Lvl: "
            f"<b>{format_price(sweep.get('level'))}</b>",
            f"Ext: "
            f"<b>{format_price(sweep.get('extreme'))}</b>",
        ])

    if stage == "READY":
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "🎯 <b>READY</b>",
            "",
            f"💰 Entry: "
            f"<b>{format_price(result.get('entry'))}</b>",
            f"🛑 SL: "
            f"<b>{format_price(result.get('sl'))}</b>",
            f"🎯 Risk: <b>{_risk_pct(result)}</b>",
            f"🎯 TP: "
            f"<b>{format_price(result.get('tp'))}</b>",
            f"📊 RR: "
            f"<b>{format_rr(result.get('rr'))}</b>",
            "",
            f"💰 P1: <b>{PARTIAL_TP_TRIGGER_R}R</b>",
            f"💰 P2: <b>{PARTIAL_TP_2_TRIGGER_R}R</b>",
            f"🛡 BE: <b>{BREAKEVEN_TRIGGER_R}R</b>",
        ])
        if in_cd:
            lines.extend([
                "",
                f"❄️ <b>COOLDOWN</b>",
            ])
        else:
            lines.extend([
                "",
                "🟢 <b>СТАВЬ ЛИМИТКУ</b>",
            ])

    reason = result.get("reason")
    if reason:
        lines.extend(["", f"ℹ️ {escape(str(reason))}"])

    return "\n".join(lines)


def coin_keyboard(coin, result, setup=None):
    rows = [[InlineKeyboardButton(
        "📈 График",
        callback_data=f"chart_{coin}")]]
    if result and result.get("stage") == "READY" and setup:
        in_cd, _ = coin_in_cooldown(coin)
        if not in_cd:
            rows.append([InlineKeyboardButton(
                "🟢 Я ЗАШЁЛ",
                callback_data=f"enter_{setup['id']}")])
    rows.extend([
        [InlineKeyboardButton(
            "🔄 Обновить",
            callback_data=f"coin_{coin}")],
        [InlineKeyboardButton(
            "⬅️ Dashboard",
            callback_data="dashboard")],
    ])
    return InlineKeyboardMarkup(rows)


def chart_keyboard():
    rows = []
    coins = list(COINS.keys())
    for i in range(0, len(coins), 3):
        chunk = coins[i:i + 3]
        row = []
        for c in chunk:
            row.append(InlineKeyboardButton(
                c, callback_data=f"chart_{c}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(
        "⬅️ Dashboard",
        callback_data="dashboard")])
    return InlineKeyboardMarkup(rows)


def create_pending_setup(coin, result):
    if result.get("stage") != "READY":
        return None
    score = result.get("score", 0)
    sym = result.get("symbol")
    if score < get_min_score(sym):
        return None

    req = ("entry", "sl", "tp", "rr", "direction")
    for k in req:
        if result.get(k) is None:
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

    raw = (f"{coin}|{result['direction']}|"
           f"{sweep.get('open_time')}|"
           f"{sweep.get('level')}|"
           f"{ilm.get('trigger_time')}")
    sid = uuid.uuid5(uuid.NAMESPACE_DNS, raw).hex[:12]

    pending = load_pending_setups()
    old = pending.get(sid)
    if old:
        created = old.get("created_at")
    else:
        created = now_iso()

    return {
        "id": sid,
        "coin": coin,
        "symbol": result.get("symbol"),
        "direction": result.get("direction"),
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "score": int(score),
        "tp_source": result.get("tp_source"),
        "fvg_bonus": result.get("fvg_bonus", 0),
        "created_at": created,
        "sweep": sweep,
        "ilm": ilm,
        "status": "PENDING",
    }


def save_ready_setup(coin, result):
    setup = create_pending_setup(coin, result)
    if setup is None:
        return None
    with _storage_lock:
        pending = load_pending_setups()
        pending[setup["id"]] = setup
        if len(pending) > 300:
            items = sorted(
                pending.items(),
                key=lambda i: i[1].get("created_at", ""))
            pending = dict(items[-300:])
        save_pending_setups(pending)
    return setup


def activate_trade(setup, chat_id):
    with _storage_lock:
        active = load_active_trades()
        for t in active:
            if t.get("setup_id") != setup["id"]:
                continue
            if t.get("chat_id") != chat_id:
                continue
            if t.get("status") == "OPEN":
                return t, False

        tid = uuid.uuid4().hex[:12]
        cur = float(setup["entry"])
        trade = {
            "id": tid,
            "setup_id": setup["id"],
            "chat_id": chat_id,
            "coin": setup["coin"],
            "symbol": setup["symbol"],
            "direction": setup["direction"],
            "entry": float(setup["entry"]),
            "sl": float(setup["sl"]),
            "sl_initial": float(setup["sl"]),
            "tp": float(setup["tp"]),
            "rr": float(setup["rr"]),
            "score": int(setup.get("score", 0)),
            "tp_source": setup.get("tp_source"),
            "opened_at": now_iso(),
            "opened_at_ms": now_ms(),
            "status": "OPEN",
            "last_price": cur,
            "best_price": cur,
            "last_check_ms": now_ms(),
            "trailing_active": False,
            "partial_tp_done": False,
            "partial_tp_price": None,
            "partial_tp_2_done": False,
            "partial_tp_2_price": None,
            "be_moved": False,
        }
        active.append(trade)
        save_active_trades(active)
    return trade, True


def close_trade(trade, exit_price, rtype):
    with _storage_lock:
        active = load_active_trades()
        target = None
        for t in active:
            if t.get("id") == trade.get("id"):
                target = t
                break
        if target is None:
            return None

        target["status"] = rtype
        target["result"] = rtype
        target["exit_price"] = float(exit_price)
        target["closed_at"] = now_iso()
        target["closed_at_ms"] = now_ms()
        target["pnl_percent"] = calc_pnl(
            target.get("entry"),
            exit_price,
            target.get("direction"))

        save_active_trades(active)

        journal = load_journal()
        je = dict(target)
        je["result"] = rtype
        journal.append(je)
        save_journal(journal)
    return target


def apply_trailing(trade, cur_price):
    """
    Возвращает список событий: [("P1", price), ("P2", price), ("BE", price)]
    v9.19.3: BE срабатывает ТОЛЬКО после P1.
    """
    events = []

    try:
        entry = float(trade["entry"])
        cur_sl = float(trade["sl"])
        sl0 = float(trade.get("sl_initial", cur_sl))
        d = trade["direction"]
        best = float(trade.get("best_price", entry))
    except Exception:
        return events

    risk = abs(entry - sl0)
    if risk <= 0:
        return events

    # --- LONG ---
    if d == "LONG":
        if cur_price > best:
            best = cur_price
        mr = (best - entry) / risk

        # P1
        if (PARTIAL_TP_ENABLED
                and not trade.get("partial_tp_done")
                and mr >= PARTIAL_TP_TRIGGER_R):
            trade["partial_tp_done"] = True
            trade["partial_tp_price"] = round(cur_price, 8)
            events.append(("P1", cur_price))

        # P2
        if (PARTIAL_TP_2_ENABLED
                and not trade.get("partial_tp_2_done")
                and trade.get("partial_tp_done")
                and mr >= PARTIAL_TP_2_TRIGGER_R):
            trade["partial_tp_2_done"] = True
            trade["partial_tp_2_price"] = round(cur_price, 8)
            events.append(("P2", cur_price))

        # BE — только после P1
        be_ready = (not PARTIAL_TP_ENABLED) or trade.get(
            "partial_tp_done")
        if (be_ready
                and not trade.get("be_moved")
                and mr >= BREAKEVEN_TRIGGER_R):
            if entry > cur_sl:
                trade["sl"] = round(entry, 8)
                trade["trailing_active"] = True
                trade["be_moved"] = True
                events.append(("BE", entry))

        # Trailing
        if mr >= TRAILING_TRIGGER_R:
            ns = best - risk * TRAILING_DISTANCE_R
            if ns > cur_sl:
                trade["sl"] = round(ns, 8)
                trade["trailing_active"] = True

    # --- SHORT ---
    elif d == "SHORT":
        if cur_price < best:
            best = cur_price
        mr = (entry - best) / risk

        # P1
        if (PARTIAL_TP_ENABLED
                and not trade.get("partial_tp_done")
                and mr >= PARTIAL_TP_TRIGGER_R):
            trade["partial_tp_done"] = True
            trade["partial_tp_price"] = round(cur_price, 8)
            events.append(("P1", cur_price))

        # P2
        if (PARTIAL_TP_2_ENABLED
                and not trade.get("partial_tp_2_done")
                and trade.get("partial_tp_done")
                and mr >= PARTIAL_TP_2_TRIGGER_R):
            trade["partial_tp_2_done"] = True
            trade["partial_tp_2_price"] = round(cur_price, 8)
            events.append(("P2", cur_price))

        # BE — только после P1
        be_ready = (not PARTIAL_TP_ENABLED) or trade.get(
            "partial_tp_done")
        if (be_ready
                and not trade.get("be_moved")
                and mr >= BREAKEVEN_TRIGGER_R):
            if entry < cur_sl:
                trade["sl"] = round(entry, 8)
                trade["trailing_active"] = True
                trade["be_moved"] = True
                events.append(("BE", entry))

        # Trailing
        if mr >= TRAILING_TRIGGER_R:
            ns = best + risk * TRAILING_DISTANCE_R
            if ns < cur_sl:
                trade["sl"] = round(ns, 8)
                trade["trailing_active"] = True

    trade["best_price"] = round(best, 8)
    return events


def _partials_text(trade):
    lines = []
    if trade.get("partial_tp_done"):
        lines.append(
            f"💰 P1: <b>{PARTIAL_TP_PERCENT}%</b> @ "
            f"{format_price(trade.get('partial_tp_price'))}")
    if trade.get("partial_tp_2_done"):
        lines.append(
            f"💰 P2: <b>{PARTIAL_TP_2_PERCENT}%</b> @ "
            f"{format_price(trade.get('partial_tp_2_price'))}")
    return lines


def event_notify_message(trade, event_type, price):
    coin = trade.get("coin")
    d = trade.get("direction")
    entry = trade.get("entry")
    sl = trade.get("sl")
    pnl = calc_pnl(entry, price, d)
    pnl_text = f"{pnl:+.2f}%" if pnl is not None else "N/A"

    if event_type == "P1":
        return (
            f"💰 <b>P1 — ЗАКРОЙ 50%</b>\n\n"
            f"💠 <b>{escape(str(coin))}</b>\n"
            f"📐 {dir_icon(d)} <b>{d}</b>\n\n"
            f"Текущая цена: "
            f"<b>{format_price(price)}</b>\n"
            f"PnL: <b>{pnl_text}</b>\n\n"
            f"✅ Закрой <b>{PARTIAL_TP_PERCENT}%</b> "
            f"позиции рыночным ордером\n"
            f"✅ Перенеси SL на "
            f"<b>{format_price(entry)}</b>\n"
            f"<i>(BE активен — защита от отката)</i>"
        )
    if event_type == "P2":
        return (
            f"💰 <b>P2 — ЗАКРОЙ ЕЩЁ 25%</b>\n\n"
            f"💠 <b>{escape(str(coin))}</b>\n"
            f"📐 {dir_icon(d)} <b>{d}</b>\n\n"
            f"Текущая цена: "
            f"<b>{format_price(price)}</b>\n"
            f"PnL: <b>{pnl_text}</b>\n\n"
            f"✅ Закрой <b>{PARTIAL_TP_2_PERCENT}%</b> "
            f"позиции\n"
            f"Остаток едет до TP"
        )
    if event_type == "BE":
        return (
            f"🛡 <b>BE — SL В БЕЗУБЫТОК</b>\n\n"
            f"💠 <b>{escape(str(coin))}</b>\n"
            f"📐 {dir_icon(d)} <b>{d}</b>\n\n"
            f"SL перенесён на entry: "
            f"<b>{format_price(sl)}</b>\n"
            f"<i>Теперь при откате — 0% вместо −риск</i>\n\n"
            f"📊 PnL сейчас: <b>{pnl_text}</b>"
        )
    return ""


def trade_close_message(trade):
    rt = trade.get("result", trade.get("status"))
    if rt == "TP":
        icon = "✅"
        title = "TP"
    elif rt == "SL":
        icon = "❌"
        title = "SL"
    else:
        icon = "⚪"
        title = "UNKNOWN"

    pnl = trade.get("pnl_percent")
    if pnl is not None:
        pnl_text = f"{float(pnl):+.2f}%"
    else:
        pnl_text = "N/A"

    pl = _partials_text(trade)
    extra = ""
    if pl:
        extra = "\n" + "\n".join(pl)

    cd_notice = ""
    if rt == "SL" and COOLDOWN_AFTER_SL_ENABLED:
        cd_notice = (
            f"\n\n❄️ <b>{trade.get('coin')} в cooldown "
            f"на {COOLDOWN_AFTER_SL_HOURS}h</b>")

    lines = [
        f"{icon} <b>TRADEMIND — {title}</b>", "",
        f"💠 <b>{escape(str(trade.get('coin')))}</b>",
        f"📐 {escape(str(trade.get('direction')))}", "",
        f"Entry: "
        f"<b>{format_price(trade.get('entry'))}</b>",
        f"Exit: "
        f"<b>{format_price(trade.get('exit_price'))}</b>",
        f"SL: <b>{format_price(trade.get('sl'))}</b>",
        f"TP: <b>{format_price(trade.get('tp'))}</b>",
        "",
        f"📊 RR: "
        f"<b>{format_rr(trade.get('rr'))}</b>",
        f"📈 PnL: <b>{pnl_text}</b>",
    ]
    return "\n".join(lines) + extra + cd_notice


async def safe_send_message(app, chat_id, text, **kwargs):
    for i in range(3):
        try:
            return await app.bot.send_message(
                chat_id=chat_id, text=text, **kwargs)
        except Exception as exc:
            if i == 2:
                print("SEND FAILED:", exc)
                raise
            await asyncio.sleep(1.5 * (i + 1))


async def safe_send_photo(app, chat_id, photo, **kwargs):
    for i in range(3):
        try:
            return await app.bot.send_photo(
                chat_id=chat_id, photo=photo, **kwargs)
        except Exception as exc:
            if i == 2:
                print("SEND PHOTO FAILED:", exc)
                raise
            await asyncio.sleep(1.5 * (i + 1))


async def monitor_active_trades(app, results):
    active = load_active_trades()
    if not active:
        return

    snap = []
    for t in active:
        snap.append(dict(t))

    to_close = []
    to_notify = []   # [(chat_id, event_type, price, trade_snapshot)]

    for trade in snap:
        if trade.get("status") != "OPEN":
            continue
        coin = trade.get("coin")
        result = results.get(coin)
        if not result or result.get("error"):
            continue

        cur = result.get("price")
        if cur is None:
            continue
        try:
            cur = float(cur)
        except Exception:
            continue

        cms = now_ms()

        # Проверка SL/TP
        try:
            sl = float(trade["sl"])
            tp = float(trade["tp"])
            d = trade["direction"]
        except Exception:
            continue

        hit = None
        if d == "LONG":
            if cur >= tp:
                hit = ("TP", cur)
            elif cur <= sl:
                hit = ("SL", cur)
        elif d == "SHORT":
            if cur <= tp:
                hit = ("TP", cur)
            elif cur >= sl:
                hit = ("SL", cur)

        if hit is not None:
            rtype, exp = hit
            to_close.append((trade, rtype, exp))
            continue

        # Trailing + partials + BE
        if TRAILING_ENABLED:
            events = apply_trailing(trade, cur)
            if events:
                chat_id = trade.get("chat_id")
                for ev_type, ev_price in events:
                    to_notify.append(
                        (chat_id, ev_type, ev_price, dict(trade)))

        trade["last_price"] = cur
        trade["last_check_ms"] = cms

    still_open = []
    for t in snap:
        if t.get("status") == "OPEN":
            still_open.append(t)
    save_active_trades(still_open)

    # Отправляем уведомления о P1/P2/BE
    for chat_id, ev_type, ev_price, tr_snap in to_notify:
        if not chat_id:
            continue
        try:
            msg = event_notify_message(tr_snap, ev_type, ev_price)
            if msg:
                await safe_send_message(
                    app, chat_id, msg,
                    parse_mode="HTML",
                    reply_markup=active_keyboard(chat_id))
                print(
                    f"[{ev_type}] {tr_snap.get('coin')} "
                    f"@ {ev_price}", flush=True)
        except Exception as exc:
            print("EVENT NOTIFY ERR:", exc)

    # Закрываем TP/SL
    for trade, rtype, exp in to_close:
        closed = close_trade(trade, exp, rtype)
        if closed is None:
            continue
        chat_id = closed.get("chat_id")
        if not chat_id:
            continue
        coin = closed.get("coin")
        result = results.get(coin)

        try:
            await safe_send_message(
                app, chat_id,
                trade_close_message(closed),
                parse_mode="HTML",
                reply_markup=trade_close_keyboard(closed))
        except Exception as exc:
            print("CLOSE MSG ERR:", exc)

        try:
            if result:
                await send_chart_to_chat(
                    app, chat_id, coin,
                    result=result, trade=closed)
        except Exception as exc:
            print("CLOSE CHART ERR:", exc)


def trade_close_keyboard(trade):
    tid = trade.get("id", "")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "📈 График",
            callback_data=f"active_trade_{tid}")],
        [InlineKeyboardButton(
            "📒 Журнал", callback_data="journal"),
         InlineKeyboardButton(
             "🧠 Dashboard",
             callback_data="dashboard")],
    ])


def active_message(chat_id):
    trades = user_active_trades(chat_id)
    if not trades:
        return ("📌 <b>АКТИВНЫХ НЕТ</b>\n\n"
                "Нажми «🟢 Я ЗАШЁЛ» на READY.")

    lines = ["📌 <b>ACTIVE</b>", ""]
    for t in trades:
        coin = t.get("coin")
        d = t.get("direction")
        cur = t.get("last_price", t.get("entry"))
        entry = float(t.get("entry"))
        pnl = calc_pnl(entry, cur, d)
        if pnl is not None:
            pnl_text = f"{pnl:+.2f}%"
        else:
            pnl_text = "N/A"

        be_tag = ""
        if t.get("be_moved"):
            be_tag = " 🛡 BE"

        lines.extend([
            f"💠 <b>{escape(str(coin))}</b>{be_tag}",
            f"📐 {dir_icon(d)} <b>{d}</b>", "",
            f"💰 Entry: "
            f"<b>{format_price(entry)}</b>",
            f"📍 Now: <b>{format_price(cur)}</b>",
            f"🛑 SL: "
            f"<b>{format_price(t.get('sl'))}</b>",
            f"🎯 TP: "
            f"<b>{format_price(t.get('tp'))}</b>",
            f"📈 PnL: <b>{pnl_text}</b>",
        ])
        for pl in _partials_text(t):
            lines.append(pl)
        lines.extend(["", "━━━━━━━━━━━━━━━━━━━━", ""])
    return "\n".join(lines)


def active_keyboard(chat_id):
    trades = user_active_trades(chat_id)
    rows = []
    for t in trades:
        tid = t.get("id")
        rows.append([InlineKeyboardButton(
            f"📈 {t.get('coin')} {t.get('direction')}",
            callback_data=f"active_trade_{tid}")])
    rows.extend([
        [InlineKeyboardButton(
            "🔄 Обновить", callback_data="active")],
        [InlineKeyboardButton(
            "📒 Журнал", callback_data="journal"),
         InlineKeyboardButton(
             "🧠 Dashboard",
             callback_data="dashboard")],
    ])
    return InlineKeyboardMarkup(rows)


def journal_message(chat_id):
    journal = user_journal(chat_id)
    if not journal:
        return "📒 <b>ЖУРНАЛ ПУСТ</b>"

    total = len(journal)
    tp = 0
    sl = 0
    amb = 0
    for x in journal:
        r = x.get("result")
        if r == "TP":
            tp += 1
        elif r == "SL":
            sl += 1
        elif r == "AMBIGUOUS":
            amb += 1

    resolved = tp + sl
    if resolved:
        wr = tp / resolved * 100
    else:
        wr = 0

    pnls = []
    for x in journal:
        p = x.get("pnl_percent")
        if p is not None:
            pnls.append(float(p))
    total_pnl = sum(pnls)

    lines = [
        "📒 <b>JOURNAL</b>", "",
        f"📊 Сделок: <b>{total}</b>",
        f"✅ TP: <b>{tp}</b>",
        f"❌ SL: <b>{sl}</b>",
        f"⚪ Amb: <b>{amb}</b>",
        f"🎯 WR: <b>{wr:.1f}%</b>",
        f"📈 PnL: <b>{total_pnl:+.2f}%</b>",
        "",
        "<b>Последние:</b>", "",
    ]

    for t in reversed(journal[-10:]):
        rt = t.get("result", "?")
        ic = {"TP": "✅", "SL": "❌",
              "AMBIGUOUS": "⚪"}.get(rt, "❔")
        p = t.get("pnl_percent")
        if p is not None:
            pt = f"{float(p):+.2f}%"
        else:
            pt = "N/A"
        lines.append(
            f"{ic} <b>{escape(str(t.get('coin')))}</b> "
            f"{t.get('direction')} • {rt} • {pt}")
    return "\n".join(lines)


def journal_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🔄 Обновить",
            callback_data="journal")],
        [InlineKeyboardButton(
            "📌 Active", callback_data="active"),
         InlineKeyboardButton(
             "🧠 Dashboard",
             callback_data="dashboard")],
    ])


def ready_message(coin, result, setup):
    fvg = setup.get("fvg_bonus", 0)
    fvg_tag = ""
    if fvg:
        fvg_tag = f"\n⚡ FVG: <b>+{fvg}</b>"
    bos_tag = " ✅" if result.get("bos") else " —"

    lines = [
        "🚨 <b>TRADEMIND READY</b>", "",
        f"💠 <b>{escape(str(coin))}</b>",
        f"📐 {dir_icon(result.get('direction'))} "
        f"<b>{result.get('direction')}</b>",
        f"⭐ Score: <b>{result.get('score', 0)}/100</b>"
        f"{fvg_tag}",
        f"🎯 BOS: <b>{bos_tag}</b>",
        "",
        "━━━━━━━━━━━━━━━━━━━━", "",
        f"Entry: "
        f"<b>{format_price(setup.get('entry'))}</b>",
        f"SL: <b>{format_price(setup.get('sl'))}</b>",
        f"Risk: <b>{_risk_pct(setup)}</b>",
        f"TP: <b>{format_price(setup.get('tp'))}</b>",
        f"RR: <b>{format_rr(setup.get('rr'))}</b>",
        "",
        "━━━━━━━━━━━━━━━━━━━━", "",
        f"💰 P1: {PARTIAL_TP_TRIGGER_R}R "
        f"({PARTIAL_TP_PERCENT}%)",
        f"💰 P2: {PARTIAL_TP_2_TRIGGER_R}R "
        f"({PARTIAL_TP_2_PERCENT}%)",
        f"🛡 BE: {BREAKEVEN_TRIGGER_R}R (после P1)",
        "",
        "🟢 <b>СТАВЬ ЛИМИТКУ</b>",
    ]
    return "\n".join(lines)


def ready_keyboard(setup):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🟢 Я ЗАШЁЛ",
            callback_data=f"enter_{setup['id']}")],
        [InlineKeyboardButton(
            "📈 График",
            callback_data=f"chart_{setup['coin']}"),
         InlineKeyboardButton(
             "💠 Карточка",
             callback_data=f"coin_{setup['coin']}")],
    ])


async def send_ready_chart(app, chat_id, coin,
                           result, setup):
    caption = ready_message(coin, result, setup)
    try:
        image = await asyncio.to_thread(
            chart_png, result, None)
        image.seek(0)
        await safe_send_photo(
            app, chat_id,
            InputFile(image,
                      filename=f"{coin.lower()}_r.png"),
            caption=caption,
            parse_mode="HTML",
            reply_markup=ready_keyboard(setup))
    except Exception as exc:
        print("READY CHART ERR:", exc)
        try:
            await safe_send_message(
                app, chat_id, caption,
                parse_mode="HTML",
                reply_markup=ready_keyboard(setup))
        except Exception:
            pass


async def send_chart_to_chat(app, chat_id, coin,
                              result=None, trade=None):
    try:
        if result is None:
            result = await asyncio.to_thread(
                build_analysis, COINS[coin])
        image = await asyncio.to_thread(
            chart_png, result, trade)
        image.seek(0)

        if trade:
            cur = trade.get("last_price")
            if trade.get("status") != "OPEN":
                cur = trade.get("exit_price")
            pnl = calc_pnl(trade.get("entry"), cur,
                           trade.get("direction"))
            caption = (
                f"💠 <b>{escape(str(coin))}</b>\n\n"
                f"📐 "
                f"{dir_icon(trade.get('direction'))} "
                f"<b>{trade.get('direction')}</b>\n\n"
                f"Entry: "
                f"<b>{format_price(trade.get('entry'))}</b>\n"
                f"SL: "
                f"<b>{format_price(trade.get('sl'))}</b>\n"
                f"TP: "
                f"<b>{format_price(trade.get('tp'))}</b>\n"
                f"RR: "
                f"<b>{format_rr(trade.get('rr'))}</b>"
            )
            if pnl is not None:
                caption += f"\nPnL: <b>{pnl:+.2f}%</b>"
        else:
            caption = coin_message(coin, result)

        await safe_send_photo(
            app, chat_id,
            InputFile(image,
                      filename=f"{coin.lower()}_c.png"),
            caption=caption,
            parse_mode="HTML",
            reply_markup=chart_keyboard())
    except Exception as exc:
        print("CHART ERR:", exc)


async def send_chart(message, coin,
                      result=None, trade=None):
    try:
        if result is None:
            result = await asyncio.to_thread(
                build_analysis, COINS[coin])
        image = await asyncio.to_thread(
            chart_png, result, trade)
        image.seek(0)

        if trade:
            pnl = calc_pnl(trade.get("entry"),
                           trade.get("last_price"),
                           trade.get("direction"))
            caption = (
                f"💠 <b>{escape(str(coin))}</b>\n\n"
                f"📐 "
                f"{dir_icon(trade.get('direction'))} "
                f"<b>{trade.get('direction')}</b>"
            )
            if pnl is not None:
                caption += f"\nPnL: <b>{pnl:+.2f}%</b>"
        else:
            caption = coin_message(coin, result)

        await message.reply_photo(
            photo=InputFile(
                image,
                filename=f"{coin.lower()}_t.png"),
            caption=caption,
            parse_mode="HTML",
            reply_markup=chart_keyboard())
    except Exception as exc:
        msg = f"❌ Ошибка графика: {escape(str(exc))}"
        await message.reply_text(msg)


async def broadcast_ready(app, coin, result, setup):
    ids = subscribers()
    if not ids:
        return
    tasks = []
    for cid in ids:
        tasks.append(send_ready_chart(
            app, cid, coin, result, setup))
    await asyncio.gather(*tasks,
                         return_exceptions=True)


# --- PNG ---

def png_chunk(ctype, data):
    crc = zlib.crc32(ctype + data) & 0xffffffff
    return (struct.pack(">I", len(data))
            + ctype + data
            + struct.pack(">I", crc))


def make_png(width, height, pixels):
    raw = b"".join(b"\0" + bytes(row)
                   for row in pixels)
    ihdr = struct.pack(">IIBBBBB", width, height,
                       8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + png_chunk(b"IHDR", ihdr)
            + png_chunk(b"IDAT",
                        zlib.compress(raw, 6))
            + png_chunk(b"IEND", b""))


def create_canvas(w, h):
    bg = (14, 18, 24)
    return [bytearray(bg * w) for _ in range(h)]


def put_pixel(pix, x, y, color):
    if y < 0 or y >= len(pix):
        return
    w = len(pix[0]) // 3
    if x < 0 or x >= w:
        return
    i = x * 3
    pix[y][i:i + 3] = bytes(color)


def draw_line(pix, x1, y1, x2, y2, color, tk=1):
    steps = max(abs(x2 - x1), abs(y2 - y1), 1)
    for i in range(steps + 1):
        x = int(x1 + (x2 - x1) * i / steps)
        y = int(y1 + (y2 - y1) * i / steps)
        for dx in range(-tk // 2, tk // 2 + 1):
            for dy in range(-tk // 2, tk // 2 + 1):
                put_pixel(pix, x + dx, y + dy, color)


def draw_marker(pix, x, y, color, r=7):
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            if dx * dx + dy * dy <= r * r:
                put_pixel(pix, x + dx, y + dy, color)


def fill_rect(pix, x1, y1, x2, y2, color):
    x1 = max(0, int(x1))
    x2 = min(len(pix[0]) // 3 - 1, int(x2))
    y1 = max(0, int(y1))
    y2 = min(len(pix) - 1, int(y2))
    for y in range(min(y1, y2), max(y1, y2) + 1):
        row = pix[y]
        for x in range(min(x1, x2), max(x1, x2) + 1):
            i = x * 3
            row[i:i + 3] = bytes(color)


def chart_png(result, trade=None):
    candles = result.get("candles_5m", [])[-100:]
    levels = strong_levels(
        result.get("major_levels", []))
    fvgs = result.get("fvgs", [])

    values = []
    for c in candles:
        try:
            values.append(float(c["high"]))
            values.append(float(c["low"]))
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
    for k in ("price", "entry", "sl", "tp"):
        v = result.get(k)
        if v is not None:
            try:
                values.append(float(v))
            except Exception:
                pass
    for f in fvgs:
        try:
            values.append(float(f["top"]))
            values.append(float(f["bottom"]))
        except Exception:
            pass
    if trade:
        for k in ("entry", "sl", "tp",
                  "exit_price", "last_price"):
            v = trade.get(k)
            if v is not None:
                try:
                    values.append(float(v))
                except Exception:
                    pass

    if not values:
        cur = float(result.get("price", 1))
        values = [cur - 1, cur + 1]

    low = min(values)
    high = max(values)
    pad = (high - low) * 0.08
    if pad <= 0:
        pad = 1
    low -= pad
    high += pad

    W = 1200
    H = 680
    L = 55
    R = 35
    T = 65
    B = 45
    cw = W - L - R
    ch = H - T - B

    pix = create_canvas(W, H)

    def y(v):
        return int(T + (high - v) / (high - low) * ch)

    stage = result.get("stage", "WAIT")
    sc = {
        "READY": (60, 220, 140),
        "SWEPT": (255, 165, 45),
        "15M_CONFIRMED": (245, 205, 60),
        "WAIT": (90, 100, 115),
    }
    fill_rect(pix, 0, 0, W, 10,
              sc.get(stage, (90, 100, 115)))

    for i in range(1, 9):
        yy = T + ch * i // 9
        draw_line(pix, L, yy, W - R, yy,
                  (42, 48, 58), 1)

    for fvg in fvgs:
        try:
            t = float(fvg["top"])
            b = float(fvg["bottom"])
        except Exception:
            continue
        if fvg["type"] == "bullish":
            zc = (20, 45, 32)
        else:
            zc = (45, 22, 28)
        fill_rect(pix, L, y(t), W - R, y(b), zc)

    for level in levels:
        try:
            lp = float(level["price"])
            zl = float(level.get("zone_low",
                                 lp * 0.998))
            zh = float(level.get("zone_high",
                                 lp * 1.002))
        except Exception:
            continue
        if level.get("type") == "BSL":
            zc = (65, 30, 38)
            lc = (235, 80, 90)
        else:
            zc = (25, 60, 42)
            lc = (50, 210, 130)
        fill_rect(pix, L, y(zh), W - R, y(zl), zc)
        draw_line(pix, L, y(lp), W - R, y(lp), lc, 2)

    if candles:
        sp = cw / max(len(candles), 1)
        cwd = max(3, int(sp * 0.58))
        for i in range(len(candles)):
            c = candles[i]
            try:
                o = float(c["open"])
                hi = float(c["high"])
                lo = float(c["low"])
                cl = float(c["close"])
            except Exception:
                continue
            x = int(L + (i + 0.5) * sp)
            if cl >= o:
                color = (55, 205, 125)
            else:
                color = (230, 80, 90)
            draw_line(pix, x, y(hi), x, y(lo), color, 1)
            bt = min(y(o), y(cl))
            bb = max(y(o), y(cl))
            if bb <= bt:
                bb = bt + 1
            for xx in range(x - cwd // 2,
                            x + cwd // 2 + 1):
                for yy in range(bt, bb + 1):
                    put_pixel(pix, xx, yy, color)

    cp = result.get("price")
    if cp is not None:
        try:
            cp = float(cp)
            draw_line(pix, L, y(cp), W - R, y(cp),
                      (80, 170, 255), 2)
            draw_marker(pix, W - R - 8, y(cp),
                        (80, 170, 255), 8)
        except Exception:
            pass

    if sweep:
        for k in ("level", "extreme"):
            try:
                v = float(sweep[k])
                draw_line(pix, L, y(v), W - R,
                          y(v), (255, 165, 40), 3)
                draw_marker(pix, L + 15, y(v),
                            (255, 165, 40), 6)
            except Exception:
                continue

    src = trade or result
    tcolors = {
        "entry": (255, 215, 70),
        "sl": (235, 80, 90),
        "tp": (80, 220, 150),
        "exit_price": (180, 100, 255),
    }
    for k in tcolors:
        v = src.get(k)
        if v is None:
            continue
        try:
            v = float(v)
            draw_line(pix, L, y(v), W - R, y(v),
                      tcolors[k], 3)
            draw_marker(pix, W - R - 10, y(v),
                        tcolors[k], 8)
        except Exception:
            continue

    return io.BytesIO(make_png(W, H, pix))


# --- HANDLERS ---

async def start(update, context):
    results = await asyncio.to_thread(scan_all)
    await update.message.reply_text(
        dashboard_message(
            results, update.effective_chat.id),
        parse_mode="HTML",
        reply_markup=dashboard_keyboard())


async def dashboard_cmd(update, context):
    results = await asyncio.to_thread(scan_all)
    await update.message.reply_text(
        dashboard_message(
            results, update.effective_chat.id),
        parse_mode="HTML",
        reply_markup=dashboard_keyboard())


async def market_cmd(update, context):
    await dashboard_cmd(update, context)


async def search_cmd(update, context):
    results = await asyncio.to_thread(scan_all)
    items = []
    for coin, result in results.items():
        if result.get("error"):
            continue
        if result.get("stage") != "READY":
            continue
        sym = result.get("symbol")
        score = result.get("score", 0)
        if score < get_min_score(sym):
            continue
        rr = result.get("rr")
        if rr is None:
            continue
        if float(rr) < MIN_RR:
            continue
        in_cd, _ = coin_in_cooldown(coin)
        if in_cd:
            continue
        setup = save_ready_setup(coin, result)
        if setup:
            items.append((score, coin, result, setup))

    if not items:
        await update.message.reply_text(
            "🔎 <b>READY НЕТ</b>",
            parse_mode="HTML",
            reply_markup=dashboard_keyboard())
        return

    items.sort(key=lambda x: x[0], reverse=True)
    for _, coin, result, setup in items[:5]:
        await update.message.reply_text(
            ready_message(coin, result, setup),
            parse_mode="HTML",
            reply_markup=ready_keyboard(setup))


async def chart_cmd(update, context):
    coin = "INJ"
    if context.args:
        if context.args[0].upper() in COINS:
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
        "🔔 <b>УВЕДОМЛЕНИЯ ON</b>",
        parse_mode="HTML",
        reply_markup=dashboard_keyboard())


async def unsub_cmd(update, context):
    chat_id = update.effective_chat.id
    with _storage_lock:
        data = []
        for x in subscribers():
            if x != chat_id:
                data.append(x)
        save_subscribers(data)
    await update.message.reply_text(
        "🔕 <b>УВЕДОМЛЕНИЯ OFF</b>",
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
    n_active = 0
    for x in active:
        if x.get("status") == "OPEN":
            n_active += 1
    journal = load_journal()

    tr = "ON" if TRAILING_ENABLED else "OFF"
    pt = "ON" if PARTIAL_TP_ENABLED else "OFF"
    sh = "ON" if ALLOW_SHORT else "OFF"
    if COOLDOWN_AFTER_SL_ENABLED:
        cd = f"{COOLDOWN_AFTER_SL_HOURS}h"
    else:
        cd = "OFF"

    text = (
        f"⚙️ <b>TRADEMIND STATUS</b>\n\n"
        f"Version: "
        f"<b>{escape(str(STRATEGY_VERSION))}</b>\n"
        f"Scanner: <b>{CHECK_INTERVAL}s</b>\n"
        f"Coins: <b>{len(COINS)}</b>\n"
        f"Active: <b>{n_active}</b>\n"
        f"Journal: <b>{len(journal)}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 <b>MODEL 9.19.3</b>\n\n"
        f"💰 P1: <b>{PARTIAL_TP_TRIGGER_R}R</b>"
        f" ({PARTIAL_TP_PERCENT}%)\n"
        f"💰 P2: "
        f"<b>{PARTIAL_TP_2_TRIGGER_R}R</b>"
        f" ({PARTIAL_TP_2_PERCENT}%)\n"
        f"🛡 BE: <b>{BREAKEVEN_TRIGGER_R}R</b>"
        f" (после P1)\n\n"
        f"🎯 Trailing: <b>{tr}</b>\n"
        f"💰 Partial: <b>{pt}</b>\n"
        f"❄️ Cooldown: <b>{cd}</b>\n"
        f"📈 SHORT: <b>{sh}</b>\n\n"
        f"🕐 Работаем 24/7"
    )
    await update.message.reply_text(
        text, parse_mode="HTML",
        reply_markup=dashboard_keyboard())


# --- MONITOR ---

async def _safe_monitor(app):
    try:
        await monitor(app)
    except Exception as exc:
        import traceback
        print("MONITOR CRASH:", exc, flush=True)
        traceback.print_exc()
        raise


async def monitor(app):
    notif = load_notification_state()

    while True:
        started = asyncio.get_running_loop().time()

        try:
            results = await asyncio.to_thread(scan_all)
            await monitor_active_trades(app, results)

            state_changed = False
            opens = []
            for t in load_active_trades():
                if t.get("status") == "OPEN":
                    opens.append(t)

            for coin, result in results.items():
                if result.get("error"):
                    continue
                if result.get("stage") != "READY":
                    continue
                sym = result.get("symbol")
                score = int(result.get("score", 0))
                if score < get_min_score(sym):
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

                in_cd, _ = coin_in_cooldown(coin)
                if in_cd:
                    continue

                if BLOCK_CONFLICTING_TRADES:
                    conf = []
                    for t in opens:
                        if t.get("coin") == coin:
                            conf.append(t)
                    if conf:
                        sides = set()
                        for t in conf:
                            sides.add(t.get("direction"))
                        new_d = result.get("direction")
                        if new_d not in sides:
                            continue
                        continue

                setup = save_ready_setup(coin, result)
                if setup is None:
                    continue

                if _notif_is_dup(
                    notif, coin,
                    setup["direction"],
                    setup["entry"]
                ):
                    continue

                _notif_mark(
                    notif, coin,
                    setup["direction"],
                    setup["entry"],
                    setup["id"])
                state_changed = True

                print(
                    f"[READY] {coin} "
                    f"{setup['direction']} "
                    f"entry={setup['entry']} "
                    f"score={setup['score']}",
                    flush=True)

                await broadcast_ready(
                    app, coin, result, setup)

            if state_changed:
                save_notification_state(notif)

        except Exception as exc:
            print("MONITOR ERR:", exc, flush=True)

        elapsed = (asyncio.get_running_loop().time()
                   - started)
        sleep = CHECK_INTERVAL - elapsed
        if sleep < 1:
            sleep = 1
        await asyncio.sleep(sleep)


# --- CALLBACKS ---

async def edit_query(query, text, kb=None):
    try:
        await query.edit_message_text(
            text, parse_mode="HTML",
            reply_markup=kb)
        return True
    except Exception:
        return False


async def callbacks(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id

    if data in ("start", "dashboard", "market"):
        results = await asyncio.to_thread(scan_all)
        text = dashboard_message(results, chat_id)
        if not await edit_query(
                query, text, dashboard_keyboard()):
            await query.message.reply_text(
                text, parse_mode="HTML",
                reply_markup=dashboard_keyboard())
        return

    if data == "filter_ready":
        results = await asyncio.to_thread(scan_all)
        lines = ["🟢 <b>READY SETUPS</b>", ""]
        cnt = 0
        for coin, result in results.items():
            if result.get("error"):
                continue
            if result.get("stage") != "READY":
                continue
            sym = result.get("symbol")
            score = int(result.get("score", 0))
            if score < get_min_score(sym):
                continue
            cnt += 1
            in_cd, cd = coin_in_cooldown(coin)
            cd_tag = ""
            if in_cd:
                cd_tag = f" ❄️ {cd}"
            lines.extend([
                f"💠 <b>{coin}</b>{cd_tag}",
                f"📐 "
                f"{dir_icon(result.get('direction'))} "
                f"{result.get('direction')}",
                f"⭐ Score: <b>{score}</b>",
                f"💰 Entry: "
                f"<b>{format_price(result.get('entry'))}</b>",
                f"🎯 RR: "
                f"<b>{format_rr(result.get('rr'))}</b>",
                "",
            ])
        if cnt == 0:
            lines.append("Нет READY сетапов")
        await edit_query(
            query, "\n".join(lines),
            dashboard_keyboard())
        return

    if data == "search":
        results = await asyncio.to_thread(scan_all)
        items = []
        for coin, result in results.items():
            if result.get("error"):
                continue
            if result.get("stage") != "READY":
                continue
            sym = result.get("symbol")
            score = int(result.get("score", 0))
            if score < get_min_score(sym):
                continue
            rr = result.get("rr")
            if rr is None:
                continue
            if float(rr) < MIN_RR:
                continue
            in_cd, _ = coin_in_cooldown(coin)
            if in_cd:
                continue
            setup = save_ready_setup(coin, result)
            if setup:
                items.append((score, coin, result, setup))

        if not items:
            await edit_query(
                query,
                "🔎 <b>НЕТ READY</b>",
                dashboard_keyboard())
            return
        items.sort(key=lambda x: x[0], reverse=True)
        _, coin, result, setup = items[0]
        await edit_query(
            query,
            ready_message(coin, result, setup),
            ready_keyboard(setup))
        return

    if data.startswith("coin_"):
        coin = data.split("_", 1)[1]
        if coin not in COINS:
            return
        result = await asyncio.to_thread(
            build_analysis, COINS[coin])
        setup = None
        in_cd, _ = coin_in_cooldown(coin)
        if result.get("stage") == "READY":
            if not in_cd:
                setup = save_ready_setup(coin, result)
        text = coin_message(coin, result)
        kb = coin_keyboard(coin, result, setup)
        if not await edit_query(query, text, kb):
            await query.message.reply_text(
                text, parse_mode="HTML",
                reply_markup=kb)
        return

    if data.startswith("enter_"):
        sid = data.split("_", 1)[1]
        setups = load_pending_setups()
        setup = setups.get(sid)
        if not setup:
            await edit_query(
                query,
                "⚠️ СИГНАЛ НЕ НАЙДЕН",
                dashboard_keyboard())
            return

        coin = setup.get("coin")
        in_cd, cd_reason = coin_in_cooldown(coin)
        if in_cd:
            await edit_query(
                query,
                (f"❄️ <b>COOLDOWN</b>\n\n"
                 f"💠 <b>{coin}</b>\n"
                 f"<i>{cd_reason}</i>"),
                dashboard_keyboard())
            return

        trade, created = activate_trade(setup, chat_id)
        if not created:
            await edit_query(
                query,
                f"🟢 <b>УЖЕ АКТИВНА</b>\n\n"
                f"💠 {setup.get('coin')}",
                active_keyboard(chat_id))
            return
        await edit_query(
            query,
            (f"🟢 <b>ПРИНЯТА</b>\n\n"
             f"💠 <b>{trade.get('coin')}</b>\n"
             f"📐 "
             f"{dir_icon(trade.get('direction'))} "
             f"<b>{trade.get('direction')}</b>\n\n"
             f"Entry: "
             f"<b>{format_price(trade.get('entry'))}</b>\n"
             f"SL: "
             f"<b>{format_price(trade.get('sl'))}</b>\n"
             f"TP: "
             f"<b>{format_price(trade.get('tp'))}</b>\n"
             f"RR: "
             f"<b>{format_rr(trade.get('rr'))}</b>\n\n"
             f"📌 Snapshot сохранён\n"
             f"🔔 P1/P2/BE придут в Telegram"),
            active_keyboard(chat_id))
        return

    if data == "charts":
        await edit_query(
            query,
            "📈 <b>ВЫБЕРИ МОНЕТУ</b>",
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
        kb = active_keyboard(chat_id)
        if not await edit_query(query, text, kb):
            await query.message.reply_text(
                text, parse_mode="HTML",
                reply_markup=kb)
        return

    if data.startswith("active_trade_"):
        tid = data.split("_", 2)[2]
        trades = user_active_trades(chat_id)
        trade = None
        for x in trades:
            if x.get("id") == tid:
                trade = x
                break
        if not trade:
            await edit_query(
                query,
                "⚠️ Сделка не активна",
                dashboard_keyboard())
            return
        coin = trade.get("coin", "INJ")
        result = await asyncio.to_thread(
            build_analysis, COINS[coin])
        cur = result.get("price")
        if cur is not None:
            trade["last_price"] = cur
        await send_chart(query.message, coin,
                          result=result, trade=trade)
        return

    if data == "journal":
        text = journal_message(chat_id)
        kb = journal_keyboard()
        if not await edit_query(query, text, kb):
            await query.message.reply_text(
                text, parse_mode="HTML",
                reply_markup=kb)
        return

    if data == "notifications":
        if is_subscribed(chat_id):
            text = "🔔 <b>УВЕД ON</b>"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🔕 Выключить",
                    callback_data="unsubscribe")],
                [InlineKeyboardButton(
                    "⬅️ Dashboard",
                    callback_data="dashboard")],
            ])
        else:
            text = "🔕 <b>УВЕД OFF</b>"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "🔔 Включить",
                    callback_data="subscribe")],
                [InlineKeyboardButton(
                    "⬅️ Dashboard",
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
        await edit_query(
            query,
            "🔔 <b>УВЕД ON</b>",
            dashboard_keyboard())
        return

    if data == "unsubscribe":
        with _storage_lock:
            d = []
            for x in subscribers():
                if x != chat_id:
                    d.append(x)
            save_subscribers(d)
        await edit_query(
            query,
            "🔕 <b>УВЕД OFF</b>",
            dashboard_keyboard())
        return

    if data == "status":
        active = load_active_trades()
        n_active = 0
        for x in active:
            if x.get("status") == "OPEN":
                n_active += 1
        journal = load_journal()
        tr = "ON" if TRAILING_ENABLED else "OFF"
        pt = "ON" if PARTIAL_TP_ENABLED else "OFF"
        sh = "ON" if ALLOW_SHORT else "OFF"
        if COOLDOWN_AFTER_SL_ENABLED:
            cd = f"{COOLDOWN_AFTER_SL_HOURS}h"
        else:
            cd = "OFF"
        text = (
            f"⚙️ <b>STATUS</b>\n\n"
            f"v{escape(str(STRATEGY_VERSION))}\n"
            f"Coins: <b>{len(COINS)}</b>\n"
            f"Active: <b>{n_active}</b>\n"
            f"Journal: <b>{len(journal)}</b>\n\n"
            f"P1: <b>{PARTIAL_TP_TRIGGER_R}R</b>\n"
            f"P2: <b>{PARTIAL_TP_2_TRIGGER_R}R</b>\n"
            f"BE: <b>{BREAKEVEN_TRIGGER_R}R</b>\n"
            f"BE после P1\n"
            f"Trail: <b>{tr}</b>\n"
            f"Partial: <b>{pt}</b>\n"
            f"Cooldown: <b>{cd}</b>\n"
            f"SHORT: <b>{sh}</b>"
        )
        await edit_query(query, text,
                         dashboard_keyboard())
        return


# --- POST INIT ---

async def post_init(application):
    if RUN_BACKTEST_ON_START:
        try:
            print("=" * 70, flush=True)
            print("BACKTEST start", flush=True)
            print("=" * 70, flush=True)
            import backtest
            if BACKTEST_MULTI:
                backtest.run_multi(
                    BACKTEST_MAX_HOURS)
            print("=" * 70, flush=True)
            print("BACKTEST done", flush=True)
            print("=" * 70, flush=True)
        except Exception as exc:
            import traceback
            print("BACKTEST ERR:", exc, flush=True)
            traceback.print_exc()

    cmds = [
        ("start", "TradeMind Dashboard"),
        ("market", "Рынок"),
        ("search", "Поиск READY"),
        ("chart", "График"),
        ("active", "Активные сделки"),
        ("journal", "Журнал"),
        ("status", "Статус"),
        ("subscribe", "Вкл увед"),
        ("unsubscribe", "Выкл увед"),
    ]
    await application.bot.set_my_commands(
        [BotCommand(c, d) for c, d in cmds])

    async def _starter():
        await asyncio.sleep(1)
        try:
            await _safe_monitor(application)
        except Exception as e:
            import traceback
            print("MONITOR DIE:", e, flush=True)
            traceback.print_exc()

    asyncio.create_task(_starter())


# --- MAIN ---

def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN не найден")

    app = (Application.builder()
           .token(TOKEN)
           .post_init(post_init)
           .build())

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
    for cmd, h in handlers:
        app.add_handler(CommandHandler(cmd, h))

    app.add_handler(CallbackQueryHandler(callbacks))

    print(f"TradeMind {STRATEGY_VERSION} started",
          flush=True)
    print(f"Monitoring {len(COINS)} coins", flush=True)
    print(f"Cooldown: {COOLDOWN_AFTER_SL_HOURS}h",
          flush=True)

    app.run_polling()


if __name__ == "__main__":
    main()