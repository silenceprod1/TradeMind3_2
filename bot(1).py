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
    InlineKeyboardMarkup, InlineKeyboardButton,
    BotCommand, InputFile,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
)

from market import get_market_data, find_major_liquidity, detect_sweep
from strategy import analyze, STRATEGY_VERSION, get_1h_direction


TOKEN = os.getenv("BOT_TOKEN")

CHECK_INTERVAL = 15
SCAN_WORKERS = 12
MIN_SCORE_READY = 80
MIN_RR = 2.0
SCAN_CACHE_TTL = 5.0


COINS = {
    "BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT",
    "BNB": "BNBUSDT", "XRP": "XRPUSDT", "DOGE": "DOGEUSDT",
    "ADA": "ADAUSDT", "AVAX": "AVAXUSDT", "LINK": "LINKUSDT",
    "HYPE": "HYPEUSDT", "SUI": "SUIUSDT", "TRX": "TRXUSDT",
    "DOT": "DOTUSDT", "LTC": "LTCUSDT", "BCH": "BCHUSDT",
    "NEAR": "NEARUSDT", "APT": "APTUSDT", "ARB": "ARBUSDT",
    "OP": "OPUSDT",
}


SUBSCRIBERS_FILE = "subscribers.json"
TRADE_JOURNAL_FILE = "trade_journal.json"
ACTIVE_TRADES_FILE = "active_trades.json"
PENDING_SETUPS_FILE = "pending_setups.json"
NOTIFICATION_STATE_FILE = "notification_state.json"

_storage_lock = threading.RLock()


def load_json(filename, default):
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(filename, data):
    tmp = filename + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
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
    return [t for t in load_active_trades()
            if t.get("status") == "OPEN" and t.get("chat_id") == chat_id]


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


def format_price(price):
    if price is None: return "N/A"
    try: price = float(price)
    except: return "N/A"
    if price >= 1000: return f"${price:,.2f}"
    if price >= 1: return f"${price:,.4f}"
    return f"${price:,.6f}"


def format_rr(value):
    if value is None: return "N/A"
    try: return f"1:{float(value):.2f}"
    except: return "N/A"


def calculate_pnl_percent(entry, exit_price, direction):
    try:
        entry = float(entry); exit_price = float(exit_price)
        if entry <= 0: return None
        if direction == "LONG": return (exit_price - entry) / entry * 100
        if direction == "SHORT": return (entry - exit_price) / entry * 100
    except: return None
    return None


def direction_icon(direction):
    if direction == "LONG": return "🟢"
    if direction == "SHORT": return "🔴"
    return "⚪"


STAGE_ICONS = {"READY": "🟢", "SWEPT": "🟠", "15M_CONFIRMED": "🟡", "WAIT": "⏳"}
STAGE_TEXTS = {
    "READY": "🟢 МОЖНО ВХОДИТЬ",
    "SWEPT": "🟠 SWEEP",
    "15M_CONFIRMED": "🟡 15M CONFIRMED",
    "WAIT": "⏳ ОЖИДАНИЕ",
}


def stage_icon(stage): return STAGE_ICONS.get(stage, "⏳")
def stage_text(stage): return STAGE_TEXTS.get(stage, "⏳ ОЖИДАНИЕ")


def tp_source_label(source):
    return {"d1": " (D1)", "major": " (major)", "local": " (local)"}.get(source, "")


def strong_levels(levels):
    if not levels: return []
    strong = [l for l in levels if float(l.get("strength", 0)) >= 65]
    return strong[:8] if strong else levels[:6]


def levels_text(levels, current_price):
    levels = strong_levels(levels)
    if not levels: return "нет сильной major liquidity"
    lines = []
    for level in levels:
        try:
            lp = float(level["price"])
            d = abs(lp - current_price) / current_price * 100
        except: continue
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
            top = float(fvg["top"]); bottom = float(fvg["bottom"])
        except: continue
        icon = "🟢" if fvg["type"] == "bullish" else "🔴"
        tf = fvg.get("tf", "").upper()
        middle = (top + bottom) / 2
        d = abs(middle - current_price) / current_price * 100
        lines.append(
            f"{icon} <b>{tf}</b> {format_price(bottom)} – {format_price(top)} "
            f"• {d:.2f}%"
        )
    return "\n".join(lines) if lines else "— нет незакрытых зон"


def build_analysis(symbol):
    market = get_market_data(symbol)
    price = market["price"]

    levels = find_major_liquidity(
        market["candles_1h"], price, 12,
        market["candles_15m"], market["candles_5m"], market["candles_1m"],
    )

    direction = get_1h_direction(market["candles_1h"])
    sweep = None
    if direction != "NEUTRAL":
        sweep = detect_sweep(market["candles_1h"], price, direction, levels)

    result = analyze(
        market["candles_1h"], market["candles_15m"], market["candles_5m"],
        price, levels, sweep,
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

    final = {coin: results.get(coin, {"error": "нет данных"}) for coin in COINS}
    _scan_cache["results"] = final
    _scan_cache["timestamp"] = now
    return final


def dashboard_message(results, chat_id=None):
    ready = swept = confirmed = waiting = 0
    active = len(user_active_trades(chat_id)) if chat_id is not None else 0

    for result in results.values():
        s = result.get("stage")
        if s == "READY": ready += 1
        elif s == "SWEPT": swept += 1
        elif s == "15M_CONFIRMED": confirmed += 1
        else: waiting += 1

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
        price = format_price(result.get("price"))
        direction = result.get("direction", "NEUTRAL")
        stage = result.get("stage", "WAIT")
        score = result.get("score", 0)
        fvg_tag = " ⚡" if result.get("fvg_bonus", 0) > 0 else ""
        lines.append(
            f"{stage_icon(stage)} <b>{coin}</b> {price} "
            f"{direction_icon(direction)} {direction} "
            f"<code>{score}/100</code>{fvg_tag}"
        )

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        "🧭 <b>СТРАТЕГИЯ</b>",
        "",
        "D1 → 1H Major → Sweep",
        "→ 15M → 5M ILM → Entry",
        "",
        "⚡ Активный тренд ≥ 0.45",
        "🔺 V-Recovery ≥ 0.45",
        "💠 FVG bonus +15 max",
        "",
        "🔔 Автоуведомление: только READY.",
    ])
    return "\n".join(lines)


def dashboard_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data="dashboard")],
        [
            InlineKeyboardButton("🟢 READY", callback_data="filter_ready"),
            InlineKeyboardButton("📌 ACTIVE", callback_data="active"),
        ],
        [
            InlineKeyboardButton("📈 Графики", callback_data="charts"),
            InlineKeyboardButton("📊 Рынок", callback_data="market"),
        ],
        [
            InlineKeyboardButton("🔎 Сканер", callback_data="search"),
            InlineKeyboardButton("📒 Журнал", callback_data="journal"),
        ],
        [
            InlineKeyboardButton("🔔 Уведомления", callback_data="notifications"),
            InlineKeyboardButton("⚙️ Статус", callback_data="status"),
        ],
    ])


def ready_filter_message(results):
    ready = []
    for coin, result in results.items():
        if result.get("error"): continue
        if (result.get("stage") == "READY"
                and result.get("score", 0) >= MIN_SCORE_READY
                and result.get("rr") is not None
                and float(result.get("rr")) >= MIN_RR):
            ready.append((int(result.get("score", 0)), coin, result))

    if not ready:
        return ("🟢 <b>READY СЕТАПОВ НЕТ</b>\n\n"
                "TradeMind продолжает мониторинг в фоне.")

    ready.sort(key=lambda x: x[0], reverse=True)
    lines = ["🟢 <b>READY SETUPS</b>", ""]
    for score, coin, result in ready:
        lines.extend([
            f"💠 <b>{coin}</b>",
            f"📐 {direction_icon(result.get('direction'))} {result.get('direction')}",
            f"⭐ Score: <b>{score}/100</b>",
            f"💰 Entry: <b>{format_price(result.get('entry'))}</b>",
            f"🛑 SL: <b>{format_price(result.get('sl'))}</b>",
            f"🎯 TP: <b>{format_price(result.get('tp'))}</b>{tp_source_label(result.get('tp_source'))}",
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


def coin_message(coin, result):
    if result.get("error"):
        return f"❌ <b>{escape(coin)}</b>\n\n{escape(str(result.get('error')))}"

    stage = result.get("stage", "WAIT")
    direction = result.get("direction", "NEUTRAL")
    d1_trend = result.get("d1_trend", "NEUTRAL")
    d1_a = result.get("d1_point_a")
    d1_b = result.get("d1_point_b")
    trend_activity = result.get("trend_activity", 0.0)
    fvg_bonus = result.get("fvg_bonus", 0)

    lines = [
        f"💠 <b>{escape(coin)}</b>",
        "",
        f"💰 Цена: <b>{format_price(result.get('price'))}</b>",
        f"📐 1H: <b>{direction_icon(direction)} {direction}</b>",
        f"📅 D1: <b>{direction_icon(d1_trend)} {d1_trend}</b>",
        f"⭐ Score: <b>{result.get('score', 0)}/100</b>"
        + (f" <i>(+{fvg_bonus} FVG)</i>" if fvg_bonus else ""),
        f"⚡ Trend activity: <b>{trend_activity:.2f}</b>",
    ]

    if d1_a is not None or d1_b is not None:
        lines.append("")
        lines.append("🎯 <b>D1 CONTEXT</b>")
        if d1_a is not None: lines.append(f"A: <b>{format_price(d1_a)}</b>")
        if d1_b is not None: lines.append(f"B: <b>{format_price(d1_b)}</b>")

    fvgs = result.get("fvgs") or []
    if fvgs:
        lines.append("")
        lines.append("💠 <b>FVG / IMBALANCE</b>")
        lines.append(fvgs_text(fvgs, result.get("price")))

    lines.extend([
        "",
        f"<b>{stage_text(stage)}</b>",
        "",
        "💧 <b>MAJOR LIQUIDITY</b>",
        levels_text(result.get("major_levels"), result.get("price")),
    ])

    sweep = result.get("sweep")
    if sweep:
        lines.extend([
            "",
            "💧 <b>ВНУТРЕННИЙ SWEEP</b>",
            f"Level: <b>{format_price(sweep.get('level'))}</b>",
            f"Extreme: <b>{format_price(sweep.get('extreme'))}</b>",
        ])

    if stage == "WAIT":
        lines.extend(["", "⏳ TradeMind продолжает поиск.",
                      "❌ В середине движения не входим."])
    elif stage == "SWEPT":
        lines.extend(["", "✅ Major liquidity снята.",
                      "⏳ Внутри системы ждём 15M."])
    elif stage == "15M_CONFIRMED":
        lines.extend(["", "✅ Sweep", "✅ 15M confirmation",
                      "⏳ Ждём 5M ILM или разблокировки READY."])
    elif stage == "READY":
        lines.extend([
            "", "━━━━━━━━━━━━━━━━━━━━", "",
            "🎯 <b>READY SETUP</b>", "",
            f"Entry: <b>{format_price(result.get('entry'))}</b>",
            f"SL: <b>{format_price(result.get('sl'))}</b>",
            f"TP: <b>{format_price(result.get('tp'))}</b>{tp_source_label(result.get('tp_source'))}",
            f"RR: <b>{format_rr(result.get('rr'))}</b>",
            "", "🟢 <b>СЕТАП ГОТОВ</b>", "🟢 ВХОД РАЗРЕШЁН",
        ])

    reason = result.get("reason")
    if reason:
        lines.extend(["", f"ℹ️ {escape(str(reason))}"])

    return "\n".join(lines)


def coin_keyboard(coin, result, setup=None):
    rows = [[InlineKeyboardButton("📈 График", callback_data=f"chart_{coin}")]]
    if result and result.get("stage") == "READY" and setup:
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
                     for c in coins[i:i+3]])
    rows.append([InlineKeyboardButton("⬅️ Dashboard", callback_data="dashboard")])
    return InlineKeyboardMarkup(rows)


def create_pending_setup(coin, result):
    if result.get("stage") != "READY": return None
    if result.get("score", 0) < MIN_SCORE_READY: return None
    required = ("entry", "sl", "tp", "rr", "direction")
    if any(result.get(k) is None for k in required): return None
    try:
        entry = float(result["entry"]); sl = float(result["sl"])
        tp = float(result["tp"]); rr = float(result["rr"])
    except: return None
    if rr < MIN_RR: return None

    sweep = result.get("sweep") or {}
    ilm = result.get("ilm") or {}
    raw_id = (f"{coin}|{result['direction']}|{entry:.10f}|{sl:.10f}|"
              f"{tp:.10f}|{sweep.get('open_time')}|{ilm.get('trigger_time')}")
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
    if setup is None: return None
    with _storage_lock:
        pending = load_pending_setups()
        pending[setup["id"]] = setup
        if len(pending) > 300:
            items = sorted(pending.items(), key=lambda i: i[1].get("created_at", ""))
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
            "id": trade_id, "setup_id": setup["id"], "chat_id": chat_id,
            "coin": setup["coin"], "symbol": setup["symbol"],
            "direction": setup["direction"],
            "entry": float(setup["entry"]), "sl": float(setup["sl"]),
            "tp": float(setup["tp"]), "rr": float(setup["rr"]),
            "score": int(setup.get("score", 0)),
            "tp_source": setup.get("tp_source"),
            "opened_at": now_iso(), "opened_at_ms": now_ms(),
            "status": "OPEN", "last_price": current,
            "last_check_ms": now_ms(),
            "entry_source": "TradeMind READY snapshot",
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
        if target is None: return None

        target["status"] = result_type
        target["result"] = result_type
        target["exit_price"] = float(exit_price)
        target["closed_at"] = now_iso()
        target["pnl_percent"] = calculate_pnl_percent(
            target.get("entry"), exit_price, target.get("direction"))

        save_active_trades(active)

        journal = load_journal()
        je = dict(target); je["result"] = result_type
        journal.append(je)
        save_journal(journal)
    return target


def level_between(prev, curr, level):
    try:
        low = min(float(prev), float(curr)); high = max(float(prev), float(curr))
        level = float(level)
        return low <= level <= high
    except: return False


def check_trade_price(trade, prev, curr):
    try:
        curr = float(curr); prev = float(prev)
        sl = float(trade["sl"]); tp = float(trade["tp"])
        d = trade["direction"]
    except: return None

    if d == "LONG":
        tpc = curr >= tp or level_between(prev, curr, tp)
        slc = curr <= sl or level_between(prev, curr, sl)
        if tpc and slc: return ("AMBIGUOUS", curr)
        if tpc: return ("TP", curr)
        if slc: return ("SL", curr)
    elif d == "SHORT":
        tpc = curr <= tp or level_between(prev, curr, tp)
        slc = curr >= sl or level_between(prev, curr, sl)
        if tpc and slc: return ("AMBIGUOUS", curr)
        if tpc: return ("TP", curr)
        if slc: return ("SL", curr)
    return None


def resolve_crossed_levels_1m(trade, candles_1m, from_ms, to_ms):
    relevant = []
    for candle in candles_1m or []:
        try:
            ot = int(candle["open_time"]); ct = int(candle["close_time"])
            if ct >= from_ms and ot <= to_ms:
                relevant.append(candle)
        except: continue

    if not relevant: return "NO_DATA"
    relevant.sort(key=lambda x: int(x["open_time"]))

    direction = trade.get("direction")
    try:
        sl = float(trade["sl"]); tp = float(trade["tp"])
    except: return "NO_DATA"

    for candle in relevant:
        try:
            h = float(candle["high"]); l = float(candle["low"])
        except: continue
        if direction == "LONG":
            hit_sl = l <= sl; hit_tp = h >= tp
        else:
            hit_sl = h >= sl; hit_tp = l <= tp
        if hit_sl and hit_tp: return "AMBIGUOUS"
        if hit_tp: return "TP"
        if hit_sl: return "SL"
    return "NO_DATA"


def trade_close_message(trade):
    rt = trade.get("result", trade.get("status"))
    if rt == "TP": icon, title = "✅", "TP ДОСТИГНУТ"
    elif rt == "SL": icon, title = "❌", "SL ДОСТИГНУТ"
    else: icon, title = "⚪", "РЕЗУЛЬТАТ НЕОПРЕДЕЛЁН"

    pnl = trade.get("pnl_percent")
    pnl_text = f"{float(pnl):+.2f}%" if pnl is not None else "N/A"

    return (
        f"{icon} <b>TRADEMIND — {title}</b>\n\n"
        f"💠 <b>{escape(str(trade.get('coin')))}</b>\n"
        f"📐 {escape(str(trade.get('direction')))}\n\n"
        f"Entry: <b>{format_price(trade.get('entry'))}</b>\n"
        f"Exit: <b>{format_price(trade.get('exit_price'))}</b>\n"
        f"SL: <b>{format_price(trade.get('sl'))}</b>\n"
        f"TP: <b>{format_price(trade.get('tp'))}</b>{tp_source_label(trade.get('tp_source'))}\n\n"
        f"📊 RR: <b>{format_rr(trade.get('rr'))}</b>\n"
        f"📈 PnL: <b>{pnl_text}</b>"
    )


async def safe_send_message(app, chat_id, text, **kwargs):
    for attempt in range(3):
        try:
            return await app.bot.send_message(chat_id=chat_id, text=text, **kwargs)
        except Exception as exc:
            if attempt == 2:
                print("SEND FAILED:", exc); raise
            await asyncio.sleep(1.5 * (attempt + 1))


async def safe_send_photo(app, chat_id, photo, **kwargs):
    for attempt in range(3):
        try:
            return await app.bot.send_photo(chat_id=chat_id, photo=photo, **kwargs)
        except Exception as exc:
            if attempt == 2:
                print("SEND PHOTO FAILED:", exc); raise
            await asyncio.sleep(1.5 * (attempt + 1))


async def monitor_active_trades(app, results):
    active = load_active_trades()
    if not active: return

    snapshot = [dict(t) for t in active]
    to_close = []

    for trade in snapshot:
        if trade.get("status") != "OPEN": continue
        coin = trade.get("coin")
        result = results.get(coin)
        if not result or result.get("error"): continue

        curr = result.get("price")
        if curr is None: continue
        try: curr = float(curr)
        except: continue

        prev = float(trade.get("last_price", trade.get("entry")))
        prev_ms = int(trade.get("last_check_ms",
                       trade.get("opened_at_ms", now_ms())))
        curr_ms = now_ms()

        check = check_trade_price(trade, prev, curr)
        if check is None:
            trade["last_price"] = curr
            trade["last_check_ms"] = curr_ms
            continue

        rt, exit_price = check
        if rt == "AMBIGUOUS":
            resolved = resolve_crossed_levels_1m(
                trade, result.get("candles_1m", []), prev_ms, curr_ms)
            if resolved == "NO_DATA":
                trade["last_price"] = curr
                trade["last_check_ms"] = curr_ms
                continue
            rt = resolved

        to_close.append((trade, rt, exit_price))

    save_active_trades([t for t in snapshot if t.get("status") == "OPEN"])

    for trade, rt, exit_price in to_close:
        closed = close_trade(trade, exit_price, rt)
        if closed is None: continue
        chat_id = closed.get("chat_id")
        if not chat_id: continue
        coin = closed.get("coin")
        result = results.get(coin)
        try:
            await safe_send_message(
                app, chat_id, trade_close_message(closed),
                parse_mode="HTML", reply_markup=trade_close_keyboard(closed))
        except Exception as exc:
            print("TRADE CLOSE MSG ERROR:", exc)
        try:
            if result:
                await send_chart_to_chat(app, chat_id, coin, result=result, trade=closed)
        except Exception as exc:
            print("TRADE CLOSE CHART ERROR:", exc)


def trade_close_keyboard(trade):
    tid = trade.get("id", "")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 График", callback_data=f"active_trade_{tid}")],
        [
            InlineKeyboardButton("📒 Журнал", callback_data="journal"),
            InlineKeyboardButton("🧠 Dashboard", callback_data="dashboard"),
        ],
    ])


def active_message(chat_id):
    trades = user_active_trades(chat_id)
    if not trades:
        return "📌 <b>АКТИВНЫХ СДЕЛОК НЕТ</b>"

    lines = ["📌 <b>ACTIVE TRADES</b>", ""]
    for trade in trades:
        coin = trade.get("coin"); d = trade.get("direction")
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
            "", "━━━━━━━━━━━━━━━━━━━━", "",
        ])
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
        [
            InlineKeyboardButton("📒 Журнал", callback_data="journal"),
            InlineKeyboardButton("🧠 Dashboard", callback_data="dashboard"),
        ],
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
            f"{trade.get('direction')} • {rt} • {pnl_text}"
        )
    return "\n".join(lines)


def journal_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Обновить", callback_data="journal")],
        [
            InlineKeyboardButton("📌 Active", callback_data="active"),
            InlineKeyboardButton("🧠 Dashboard", callback_data="dashboard"),
        ],
    ])


def ready_message(coin, result, setup):
    fvg_bonus = setup.get("fvg_bonus", 0)
    fvg_tag = f"\n⚡ FVG bonus: <b>+{fvg_bonus}</b>" if fvg_bonus else ""
    return (
        "🚨 <b>TRADEMIND — READY</b>\n\n"
        f"💠 <b>{escape(str(coin))}</b>\n"
        f"📐 {direction_icon(result.get('direction'))} "
        f"<b>{result.get('direction')}</b>\n"
        f"⭐ Score: <b>{result.get('score', 0)}/100</b>{fvg_tag}\n"
        f"📅 D1: <b>{result.get('d1_trend', 'NEUTRAL')}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "💰 <b>ТОЧКА ВХОДА</b>\n\n"
        f"Entry: <b>{format_price(setup.get('entry'))}</b>\n"
        f"SL: <b>{format_price(setup.get('sl'))}</b>\n"
        f"TP: <b>{format_price(setup.get('tp'))}</b>"
        f"{tp_source_label(setup.get('tp_source'))}\n"
        f"RR: <b>{format_rr(setup.get('rr'))}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🟢 <b>МОЖНО ВХОДИТЬ</b>"
    )


def ready_keyboard(setup):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Я ЗАШЁЛ", callback_data=f"enter_{setup['id']}")],
        [
            InlineKeyboardButton("📈 График", callback_data=f"chart_{setup['coin']}"),
            InlineKeyboardButton("💠 Карточка", callback_data=f"coin_{setup['coin']}"),
        ],
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
            reply_markup=ready_keyboard(setup),
        )
    except Exception as exc:
        print("READY CHART ERROR:", exc)
        try:
            await safe_send_message(app, chat_id, caption,
                                    parse_mode="HTML",
                                    reply_markup=ready_keyboard(setup))
        except: pass


async def send_photo_to_chat(app, chat_id, coin, result, trade=None, reply_markup=None):
    image = await asyncio.to_thread(chart_png, result, trade)
    image.seek(0)

    if trade:
        curr = (trade.get("last_price") if trade.get("status") == "OPEN"
                else trade.get("exit_price"))
        pnl = calculate_pnl_percent(trade.get("entry"), curr, trade.get("direction"))
        caption = (
            f"💠 <b>{escape(str(coin))}</b>\n\n"
            f"📐 {direction_icon(trade.get('direction'))} <b>{trade.get('direction')}</b>\n\n"
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
        caption=caption, parse_mode="HTML", reply_markup=reply_markup,
    )


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
            curr = (trade.get("last_price") if trade.get("status") == "OPEN"
                    else trade.get("exit_price"))
            pnl = calculate_pnl_percent(trade.get("entry"), curr, trade.get("direction"))
            caption = (
                f"💠 <b>{escape(str(coin))}</b>\n\n"
                f"📐 {direction_icon(trade.get('direction'))} <b>{trade.get('direction')}</b>\n\n"
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
            photo=InputFile(image, filename=f"{coin.lower()}_trademind.png"),
            caption=caption, parse_mode="HTML", reply_markup=chart_keyboard(),
        )
    except Exception as exc:
        await message.reply_text(f"❌ Ошибка графика: {escape(str(exc))}")


async def broadcast_ready(app, coin, result, setup):
    ids = subscribers()
    if not ids: return
    async def send(chat_id):
        try:
            await send_ready_chart(app, chat_id, coin, result, setup)
        except Exception as exc:
            print("BROADCAST ERROR:", chat_id, exc)
    await asyncio.gather(*(send(cid) for cid in ids), return_exceptions=True)


# ============================================================
# PNG
# ============================================================

def png_chunk(chunk_type, data):
    return (struct.pack(">I", len(data)) + chunk_type + data
            + struct.pack(">I", zlib.crc32(chunk_type + data) & 0xffffffff))


def make_png(width, height, pixels):
    raw = b"".join(b"\0" + bytes(row) for row in pixels)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + png_chunk(b"IDAT", zlib.compress(raw, 6))
        + png_chunk(b"IEND", b"")
    )


def create_canvas(width, height):
    bg = (14, 18, 24)
    return [bytearray(bg * width) for _ in range(height)]


def put_pixel(pixels, x, y, color):
    if y < 0 or y >= len(pixels): return
    w = len(pixels[0]) // 3
    if x < 0 or x >= w: return
    i = x * 3
    pixels[y][i:i+3] = bytes(color)


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
    x1 = max(0, int(x1)); x2 = min(len(pixels[0]) // 3 - 1, int(x2))
    y1 = max(0, int(y1)); y2 = min(len(pixels) - 1, int(y2))
    for y in range(min(y1, y2), max(y1, y2) + 1):
        row = pixels[y]
        for x in range(min(x1, x2), max(x1, x2) + 1):
            i = x * 3
            row[i:i+3] = bytes(color)


def chart_png(result, trade=None):
    candles = result.get("candles_5m", [])[-100:]
    levels = strong_levels(result.get("major_levels", []))
    fvgs = result.get("fvgs", [])

    values = []
    for c in candles:
        try: values.extend([float(c["high"]), float(c["low"])])
        except: continue
    for lvl in levels:
        try: values.append(float(lvl["price"]))
        except: pass
    sweep = result.get("sweep")
    if sweep:
        for k in ("level", "extreme"):
            try: values.append(float(sweep[k]))
            except: pass
    for k in ("price", "entry", "sl", "tp", "exit_price"):
        if result.get(k) is not None:
            try: values.append(float(result[k]))
            except: pass
    d1_context = result.get("d1_context") or {}
    for k in ("point_a", "point_b"):
        v = d1_context.get(k)
        if v is not None:
            try: values.append(float(v))
            except: pass
    for f in fvgs:
        try: values.extend([float(f["top"]), float(f["bottom"])])
        except: pass
    if trade:
        for k in ("entry", "sl", "tp", "exit_price", "last_price"):
            if trade.get(k) is not None:
                try: values.append(float(trade[k]))
                except: pass

    if not values:
        cur = float(result.get("price", 1))
        values = [cur - 1, cur + 1]

    low = min(values); high = max(values)
    padding = (high - low) * 0.08 or 1
    low -= padding; high += padding

    width = 1200; height = 680
    left = 55; right = 35; top = 65; bottom = 45
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
    fill_rect(pixels, 0, 0, width, 10, stage_colors.get(stage, (90, 100, 115)))

    grid_color = (42, 48, 58)
    for i in range(1, 9):
        yy = top + chart_height * i // 9
        draw_line(pixels, left, yy, width - right, yy, grid_color, 1)

    # FVG зоны — под свечами, тонкая заливка
    for fvg in fvgs:
        try:
            t = float(fvg["top"]); b = float(fvg["bottom"])
        except: continue
        if fvg["type"] == "bullish":
            zone_color = (20, 45, 32)
        else:
            zone_color = (45, 22, 28)
        fill_rect(pixels, left, y(t), width - right, y(b), zone_color)

    # Major liquidity
    for level in levels:
        try:
            lp = float(level["price"])
            zl = float(level.get("zone_low", lp * 0.998))
            zh = float(level.get("zone_high", lp * 1.002))
        except: continue
        if level.get("type") == "BSL":
            zone_color = (65, 30, 38); line_color = (235, 80, 90)
        else:
            zone_color = (25, 60, 42); line_color = (50, 210, 130)
        fill_rect(pixels, left, y(zh), width - right, y(zl), zone_color)
        draw_line(pixels, left, y(lp), width - right, y(lp), line_color, 2)

    # Candles
    if candles:
        spacing = chart_width / max(len(candles), 1)
        candle_width = max(3, int(spacing * 0.58))
        for i, candle in enumerate(candles):
            try:
                o = float(candle["open"]); h = float(candle["high"])
                l = float(candle["low"]); c = float(candle["close"])
            except: continue
            x = int(left + (i + 0.5) * spacing)
            color = (55, 205, 125) if c >= o else (230, 80, 90)
            draw_line(pixels, x, y(h), x, y(l), color, 1)
            bt = min(y(o), y(c)); bb = max(y(o), y(c))
            if bb <= bt: bb = bt + 1
            for xx in range(x - candle_width // 2, x + candle_width // 2 + 1):
                for yy in range(bt, bb + 1):
                    put_pixel(pixels, xx, yy, color)

    cur_price = result.get("price")
    if cur_price is not None:
        try:
            cur_price = float(cur_price)
            draw_line(pixels, left, y(cur_price), width - right, y(cur_price),
                      (80, 170, 255), 2)
            draw_marker(pixels, width - right - 8, y(cur_price), (80, 170, 255), 8)
        except: pass

    if sweep:
        for k in ("level", "extreme"):
            try:
                v = float(sweep[k])
                draw_line(pixels, left, y(v), width - right, y(v), (255, 165, 40), 3)
                draw_marker(pixels, left + 15, y(v), (255, 165, 40), 6)
            except: continue

    for k, color in (("point_a", (200, 130, 255)), ("point_b", (130, 200, 255))):
        v = d1_context.get(k)
        if v is None: continue
        try:
            v = float(v)
            draw_line(pixels, left, y(v), width - right, y(v), color, 2)
        except: continue

    trade_source = trade or result
    trade_colors = {
        "entry": (255, 215, 70), "sl": (235, 80, 90),
        "tp": (80, 220, 150), "exit_price": (180, 100, 255),
    }
    for k, color in trade_colors.items():
        v = trade_source.get(k)
        if v is None: continue
        try:
            v = float(v)
            draw_line(pixels, left, y(v), width - right, y(v), color, 3)
            draw_marker(pixels, width - right - 10, y(v), color, 8)
        except: continue

    return io.BytesIO(make_png(width, height, pixels))


# ============================================================
# HANDLERS
# ============================================================

async def start(update, context):
    results = await asyncio.to_thread(scan_all)
    await update.message.reply_text(
        dashboard_message(results, update.effective_chat.id),
        parse_mode="HTML", reply_markup=dashboard_keyboard())


async def dashboard_cmd(update, context):
    results = await asyncio.to_thread(scan_all)
    await update.message.reply_text(
        dashboard_message(results, update.effective_chat.id),
        parse_mode="HTML", reply_markup=dashboard_keyboard())


async def market_cmd(update, context):
    await dashboard_cmd(update, context)


async def search_cmd(update, context):
    results = await asyncio.to_thread(scan_all)
    ready_items = []
    for coin, result in results.items():
        if result.get("error"): continue
        if (result.get("stage") == "READY"
                and result.get("score", 0) >= MIN_SCORE_READY
                and result.get("rr") is not None
                and float(result.get("rr")) >= MIN_RR):
            setup = save_ready_setup(coin, result)
            if setup:
                ready_items.append((result.get("score", 0), coin, result, setup))

    if not ready_items:
        await update.message.reply_text(
            "🔎 <b>READY СЕТАПОВ НЕТ</b>", parse_mode="HTML",
            reply_markup=dashboard_keyboard())
        return

    ready_items.sort(key=lambda x: x[0], reverse=True)
    for _, coin, result, setup in ready_items[:5]:
        await update.message.reply_text(
            ready_message(coin, result, setup),
            parse_mode="HTML", reply_markup=ready_keyboard(setup))


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
            data.append(chat_id); save_subscribers(data)
    await update.message.reply_text(
        "🔔 <b>УВЕДОМЛЕНИЯ ВКЛЮЧЕНЫ</b>\n\n"
        "Только READY-сетапы.", parse_mode="HTML",
        reply_markup=dashboard_keyboard())


async def unsub_cmd(update, context):
    chat_id = update.effective_chat.id
    with _storage_lock:
        save_subscribers([x for x in subscribers() if x != chat_id])
    await update.message.reply_text(
        "🔕 <b>УВЕДОМЛЕНИЯ ВЫКЛЮЧЕНЫ</b>", parse_mode="HTML",
        reply_markup=dashboard_keyboard())


async def active_cmd(update, context):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        active_message(chat_id), parse_mode="HTML",
        reply_markup=active_keyboard(chat_id))


async def journal_cmd(update, context):
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        journal_message(chat_id), parse_mode="HTML",
        reply_markup=journal_keyboard())


async def status_cmd(update, context):
    active = load_active_trades()
    active_count = len([x for x in active if x.get("status") == "OPEN"])
    journal = load_journal()

    await update.message.reply_text(
        (
            f"⚙️ <b>TRADEMIND STATUS</b>\n\n"
            f"Version: <b>{escape(str(STRATEGY_VERSION))}</b>\n"
            f"Scanner: <b>{CHECK_INTERVAL}s</b>\n"
            f"Workers: <b>{SCAN_WORKERS}</b>\n"
            f"Coins: <b>{len(COINS)}</b>\n"
            f"Active: <b>{active_count}</b>\n"
            f"Journal: <b>{len(journal)}</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "📅 D1 context\n"
            "💧 1H Major + 15M + ROUND\n"
            "💠 FVG (5M + 15M)\n"
            "🟠 Sweep internal\n"
            "🟡 15M confirmation\n"
            "🎯 5M ILM\n"
            "⚡ Trend ≥ 0.45\n"
            "🔺 V-Recovery ≥ 0.45\n"
            "📊 RR ≥ 1:2\n\n"
            "🕐 Работаем 24/7 (без сессий)"
        ),
        parse_mode="HTML", reply_markup=dashboard_keyboard())


# ============================================================
# MONITOR
# ============================================================

async def monitor(app):
    notification_state = load_notification_state()

    while True:
        started = asyncio.get_running_loop().time()
        try:
            results = await asyncio.to_thread(scan_all)
            await monitor_active_trades(app, results)

            state_changed = False
            for coin, result in results.items():
                if result.get("error"): continue
                stage = result.get("stage", "WAIT")
                if stage != "READY": continue
                score = int(result.get("score", 0))
                if score < MIN_SCORE_READY: continue
                rr = result.get("rr")
                if rr is None: continue
                try: rr = float(rr)
                except: continue
                if rr < MIN_RR: continue

                setup = save_ready_setup(coin, result)
                if setup is None: continue

                signal_key = setup["id"]
                if notification_state.get(coin) == signal_key: continue

                notification_state[coin] = signal_key
                state_changed = True

                print(
                    f"[READY] {coin} {setup['direction']} "
                    f"entry={setup['entry']} sl={setup['sl']} tp={setup['tp']} "
                    f"tp_src={setup.get('tp_source')} "
                    f"fvg={setup.get('fvg_bonus', 0)} "
                    f"rr={setup['rr']} score={setup['score']}"
                )

                await broadcast_ready(app, coin, result, setup)

            if state_changed:
                save_notification_state(notification_state)
        except Exception as exc:
            print("MONITOR ERROR:", exc)

        elapsed = asyncio.get_running_loop().time() - started
        await asyncio.sleep(max(1, CHECK_INTERVAL - elapsed))


# ============================================================
# CALLBACKS
# ============================================================

async def edit_query(query, text, keyboard=None):
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        return True
    except: return False


async def callbacks(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id

    if data in ("start", "dashboard"):
        results = await asyncio.to_thread(scan_all)
        text = dashboard_message(results, chat_id)
        if not await edit_query(query, text, dashboard_keyboard()):
            await query.message.reply_text(text, parse_mode="HTML",
                                           reply_markup=dashboard_keyboard())
        return

    if data == "market":
        results = await asyncio.to_thread(scan_all)
        text = dashboard_message(results, chat_id)
        if not await edit_query(query, text, dashboard_keyboard()):
            await query.message.reply_text(text, parse_mode="HTML",
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
            if result.get("error"): continue
            if (result.get("stage") == "READY"
                    and result.get("score", 0) >= MIN_SCORE_READY
                    and result.get("rr") is not None
                    and float(result.get("rr")) >= MIN_RR):
                setup = save_ready_setup(coin, result)
                if setup:
                    ready.append((result.get("score", 0), coin, result, setup))
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
        if coin not in COINS: return
        result = await asyncio.to_thread(build_analysis, COINS[coin])
        setup = None
        if (result.get("stage") == "READY"
                and result.get("score", 0) >= MIN_SCORE_READY):
            setup = save_ready_setup(coin, result)
        text = coin_message(coin, result)
        if not await edit_query(query, text, coin_keyboard(coin, result, setup)):
            await query.message.reply_text(text, parse_mode="HTML",
                                           reply_markup=coin_keyboard(coin, result, setup))
        return

    if data.startswith("enter_"):
        setup_id = data.split("_", 1)[1]
        setup = load_pending_setups().get(setup_id)
        if not setup:
            await edit_query(query, "⚠️ <b>СИГНАЛ НЕ НАЙДЕН</b>",
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
             "📌 Snapshot сохранён."),
            active_keyboard(chat_id))
        return

    if data == "charts":
        await edit_query(query, "📈 <b>ВЫБЕРИ МОНЕТУ</b>", chart_keyboard())
        return

    if data.startswith("chart_"):
        coin = data.split("_", 1)[1]
        if coin not in COINS: return
        await send_chart(query.message, coin)
        return

    if data == "active":
        text = active_message(chat_id)
        if not await edit_query(query, text, active_keyboard(chat_id)):
            await query.message.reply_text(text, parse_mode="HTML",
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
            await query.message.reply_text(text, parse_mode="HTML",
                                           reply_markup=journal_keyboard())
        return

    if data == "notifications":
        if is_subscribed(chat_id):
            text = "🔔 <b>УВЕДОМЛЕНИЯ ВКЛЮЧЕНЫ</b>"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔕 Выключить", callback_data="unsubscribe")],
                [InlineKeyboardButton("⬅️ Dashboard", callback_data="dashboard")],
            ])
        else:
            text = "🔕 <b>УВЕДОМЛЕНИЯ ВЫКЛЮЧЕНЫ</b>"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔔 Включить", callback_data="subscribe")],
                [InlineKeyboardButton("⬅️ Dashboard", callback_data="dashboard")],
            ])
        await edit_query(query, text, kb)
        return

    if data == "subscribe":
        with _storage_lock:
            d = subscribers()
            if chat_id not in d:
                d.append(chat_id); save_subscribers(d)
        await edit_query(query, "🔔 <b>READY-УВЕДОМЛЕНИЯ ВКЛЮЧЕНЫ</b>",
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
             "📅 D1 context\n"
             "💧 1H Major + 15M + ROUND\n"
             "💠 FVG (5M + 15M)\n"
             "🟠 Sweep internal\n"
             "🟡 15M confirmation\n"
             "🎯 5M ILM\n"
             "⚡ Trend ≥ 0.45\n"
             "🔺 V-Recovery ≥ 0.45\n"
             "📊 RR ≥ 1:2\n\n"
             "🕐 Работаем 24/7"),
            dashboard_keyboard())
        return


# ============================================================
# POST INIT
# ============================================================

async def post_init(application):
    commands = [
        ("start", "TradeMind Dashboard"), ("market", "Рынок"),
        ("search", "Поиск READY"), ("chart", "График"),
        ("active", "Активные сделки"), ("journal", "Журнал"),
        ("status", "Статус"), ("subscribe", "Включить уведомления"),
        ("unsubscribe", "Выключить уведомления"),
    ]
    await application.bot.set_my_commands([
        BotCommand(cmd, desc) for cmd, desc in commands
    ])
    application.create_task(monitor(application))


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN не найден")

    application = Application.builder().token(TOKEN).post_init(post_init).build()

    handlers = [
        ("start", start), ("market", market_cmd), ("search", search_cmd),
        ("chart", chart_cmd), ("active", active_cmd), ("journal", journal_cmd),
        ("status", status_cmd), ("subscribe", sub_cmd), ("unsubscribe", unsub_cmd),
    ]
    for cmd, h in handlers:
        application.add_handler(CommandHandler(cmd, h))

    application.add_handler(CallbackQueryHandler(callbacks))

    print(f"TradeMind {STRATEGY_VERSION} started (24/7)")
    print(f"Monitoring {len(COINS)} coins")

    application.run_polling()


if __name__ == "__main__":
    main()