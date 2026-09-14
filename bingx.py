# bingx.py
# TradeMind — BingX integration v1.0
#
# Режимы:
# OFF     — BingX не используется
# PAPER   — имитация сделки без реального ордера
# CONFIRM — подготовка сделки, реальный ордер только после подтверждения
# AUTO    — автоматическое открытие сделки
#
# Переменные окружения:
# BINGX_MODE=OFF
# BINGX_API_KEY=...
# BINGX_SECRET_KEY=...
# BINGX_ENV=prod-live
# BINGX_LEVERAGE=5
# BINGX_RISK_USDT=10
# BINGX_WORKING_TYPE=MARK_PRICE

import os
import time
import hmac
import hashlib
import logging
from urllib.parse import urlencode

import requests


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://open-api.bingx.com"

API_KEY = os.getenv("BINGX_API_KEY", "").strip()
SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "").strip()

MODE = os.getenv("BINGX_MODE", "OFF").strip().upper()

ENVIRONMENT = os.getenv("BINGX_ENV", "prod-live").strip()

try:
    LEVERAGE = int(os.getenv("BINGX_LEVERAGE", "5"))
except Exception:
    LEVERAGE = 5

try:
    RISK_USDT = float(os.getenv("BINGX_RISK_USDT", "10"))
except Exception:
    RISK_USDT = 10.0

WORKING_TYPE = os.getenv(
    "BINGX_WORKING_TYPE",
    "MARK_PRICE"
).strip().upper()

RECV_WINDOW = 5000

REQUEST_TIMEOUT = 10


# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger("TradeMind.BingX")


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "TradeMind/1.0",
    "Content-Type": "application/json",
})


# ============================================================
# HELPERS
# ============================================================

def mode():
    """
    Current BingX mode.
    """
    return MODE


def is_enabled():
    return MODE in {
        "PAPER",
        "CONFIRM",
        "AUTO",
    }


def is_live():
    return MODE == "AUTO"


def config_status():
    """
    Human-readable BingX configuration status.
    """

    key_ok = bool(API_KEY)
    secret_ok = bool(SECRET_KEY)

    return {
        "mode": MODE,
        "api_key": key_ok,
        "secret_key": secret_ok,
        "environment": ENVIRONMENT,
        "leverage": LEVERAGE,
        "risk_usdt": RISK_USDT,
        "working_type": WORKING_TYPE,
        "ready": (
            MODE == "OFF"
            or (
                key_ok
                and secret_ok
            )
        ),
    }


def _timestamp():
    return int(time.time() * 1000)


def _sign(params):
    """
    BingX HMAC SHA256 signature.
    """

    query_string = urlencode(
        sorted(params.items()),
        doseq=True
    )

    signature = hmac.new(
        SECRET_KEY.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    return signature


def _headers():
    return {
        "X-BX-APIKEY": API_KEY,
        "Content-Type": "application/json",
    }


def _public_get(path, params=None):
    """
    Public GET request.
    """

    url = BASE_URL + path

    try:
        response = session.get(
            url,
            params=params or {},
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        return response.json()

    except Exception as exc:
        logger.exception(
            "BingX public request failed: %s",
            exc
        )

        return {
            "code": -1,
            "msg": str(exc),
            "data": None,
        }


def _signed_get(path, params=None):
    """
    Signed GET request.
    """

    if not API_KEY or not SECRET_KEY:
        return {
            "code": -1,
            "msg": "BingX API keys are not configured",
            "data": None,
        }

    params = dict(params or {})

    params["timestamp"] = _timestamp()
    params["recvWindow"] = RECV_WINDOW

    params["signature"] = _sign(params)

    url = BASE_URL + path

    try:
        response = session.get(
            url,
            params=params,
            headers=_headers(),
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        return response.json()

    except Exception as exc:
        logger.exception(
            "BingX signed GET failed: %s",
            exc
        )

        return {
            "code": -1,
            "msg": str(exc),
            "data": None,
        }


def _signed_post(path, params=None):
    """
    Signed POST request.
    """

    if not API_KEY or not SECRET_KEY:
        return {
            "code": -1,
            "msg": "BingX API keys are not configured",
            "data": None,
        }

    params = dict(params or {})

    params["timestamp"] = _timestamp()
    params["recvWindow"] = RECV_WINDOW

    params["signature"] = _sign(params)

    url = BASE_URL + path

    try:
        response = session.post(
            url,
            params=params,
            headers=_headers(),
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        return response.json()

    except Exception as exc:
        logger.exception(
            "BingX signed POST failed: %s",
            exc
        )

        return {
            "code": -1,
            "msg": str(exc),
            "data": None,
        }


# ============================================================
# SYMBOL
# ============================================================

def normalize_symbol(symbol):
    """
    Converts:
        SOL
        SOLUSDT
        SOL-USDT
        SOL/USDT
    into:
        SOL-USDT
    """

    if not symbol:
        return ""

    symbol = str(symbol).upper().strip()

    symbol = symbol.replace("/", "-")
    symbol = symbol.replace("_", "-")

    if symbol.endswith("USDT") and "-" not in symbol:
        symbol = symbol[:-4] + "-USDT"

    if symbol.endswith("-USDT"):
        return symbol

    return symbol


# ============================================================
# MARKET DATA
# ============================================================

def get_ticker(symbol):
    """
    Get BingX perpetual ticker.
    """

    symbol = normalize_symbol(symbol)

    return _public_get(
        "/openApi/swap/v2/quote/ticker",
        {
            "symbol": symbol,
        }
    )


def get_contracts():
    """
    Get perpetual contract information.
    """

    return _public_get(
        "/openApi/swap/v2/quote/contracts"
    )


def get_contract(symbol):
    """
    Find contract metadata.
    """

    symbol = normalize_symbol(symbol)

    result = get_contracts()

    if result.get("code") != 0:
        return None

    data = result.get("data") or []

    if isinstance(data, dict):
        data = [data]

    for contract in data:
        contract_symbol = normalize_symbol(
            contract.get("symbol", "")
        )

        if contract_symbol == symbol:
            return contract

    return None


# ============================================================
# ACCOUNT
# ============================================================

def get_balance():
    """
    Get USDT futures balance.
    """

    result = _signed_get(
        "/openApi/swap/v2/user/balance"
    )

    if result.get("code") != 0:
        return result

    data = result.get("data")

    return {
        "code": result.get("code"),
        "msg": result.get("msg"),
        "data": data,
    }


def get_positions(symbol=None):
    """
    Get current futures positions.
    """

    params = {}

    if symbol:
        params["symbol"] = normalize_symbol(symbol)

    return _signed_get(
        "/openApi/swap/v2/user/positions",
        params
    )


# ============================================================
# HEDGE MODE
# ============================================================

def get_position_mode():
    """
    Check position mode.
    """

    return _signed_get(
        "/openApi/swap/v1/position/side/dual"
    )


def set_position_mode(dual_side=True):
    """
    Set hedge mode.

    dual_side=True:
        Hedge Mode

    dual_side=False:
        One-way Mode
    """

    return _signed_post(
        "/openApi/swap/v1/position/side/dual",
        {
            "dualSidePosition": str(
                bool(dual_side)
            ).lower()
        }
    )


# ============================================================
# LEVERAGE
# ============================================================

def set_leverage(symbol, leverage=None):
    """
    Set leverage for a contract.
    """

    symbol = normalize_symbol(symbol)

    leverage = leverage or LEVERAGE

    try:
        leverage = int(leverage)
    except Exception:
        leverage = LEVERAGE

    return _signed_post(
        "/openApi/swap/v2/trade/leverage",
        {
            "symbol": symbol,
            "leverage": leverage,
        }
    )


# ============================================================
# QUANTITY
# ============================================================

def _to_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def _round_step(value, step):
    """
    Round quantity according to contract step.
    """

    value = _to_float(value)
    step = _to_float(step)

    if step <= 0:
        return value

    decimals = 0

    text = f"{step:.12f}".rstrip("0")

    if "." in text:
        decimals = len(
            text.split(".")[1]
        )

    rounded = round(
        value / step
    ) * step

    return round(
        rounded,
        decimals
    )


def get_quantity_rules(symbol):
    """
    Extract quantity rules from BingX contract metadata.
    """

    contract = get_contract(symbol)

    if not contract:
        return {
            "min_qty": 0.0,
            "max_qty": 0.0,
            "step_size": 0.0,
        }

    min_qty = (
        contract.get("tradeMinQuantity")
        or contract.get("minQty")
        or contract.get("min_quantity")
        or 0
    )

    max_qty = (
        contract.get("tradeMaxQuantity")
        or contract.get("maxQty")
        or contract.get("max_quantity")
        or 0
    )

    step_size = (
        contract.get("tradeQuantityPrecision")
        or contract.get("quantityPrecision")
        or 0
    )

    # Some BingX responses expose quantity precision
    # as an integer instead of a step.
    if isinstance(step_size, int):
        precision = step_size

        if precision >= 0 and precision <= 12:
            step_size = 10 ** (-precision)

    try:
        step_size = float(step_size)
    except Exception:
        step_size = 0.0

    return {
        "min_qty": _to_float(min_qty),
        "max_qty": _to_float(max_qty),
        "step_size": step_size,
    }


def calculate_quantity(
    symbol,
    entry,
    stop_loss,
    risk_usdt=None
):
    """
    Calculate quantity based on fixed USDT risk.

    Example:

        risk = $10
        entry = 100
        SL = 98

        distance = $2
        quantity = 10 / 2
                 = 5 SOL
    """

    entry = _to_float(entry)
    stop_loss = _to_float(stop_loss)

    if entry <= 0:
        return 0.0

    if stop_loss <= 0:
        return 0.0

    risk_usdt = (
        RISK_USDT
        if risk_usdt is None
        else _to_float(
            risk_usdt,
            RISK_USDT
        )
    )

    distance = abs(
        entry - stop_loss
    )

    if distance <= 0:
        return 0.0

    quantity = risk_usdt / distance

    rules = get_quantity_rules(symbol)

    min_qty = rules["min_qty"]
    max_qty = rules["max_qty"]
    step_size = rules["step_size"]

    if step_size > 0:
        quantity = _round_step(
            quantity,
            step_size
        )

    if min_qty > 0 and quantity < min_qty:
        quantity = min_qty

    if max_qty > 0 and quantity > max_qty:
        quantity = max_qty

    return quantity


# ============================================================
# ORDER
# ============================================================

def place_order(
    symbol,
    side,
    quantity,
    position_side=None,
    order_type="MARKET",
):
    """
    Place BingX perpetual order.

    side:
        BUY
        SELL

    position_side:
        LONG
        SHORT
    """

    symbol = normalize_symbol(symbol)

    side = str(side).upper().strip()

    quantity = _to_float(quantity)

    if not symbol:
        return {
            "code": -1,
            "msg": "Invalid symbol",
            "data": None,
        }

    if side not in {
        "BUY",
        "SELL",
    }:
        return {
            "code": -1,
            "msg": "Invalid order side",
            "data": None,
        }

    if quantity <= 0:
        return {
            "code": -1,
            "msg": "Quantity must be greater than zero",
            "data": None,
        }

    params = {
        "symbol": symbol,
        "side": side,
        "positionSide": (
            position_side
            if position_side
            else "BOTH"
        ),
        "type": order_type,
        "quantity": quantity,
    }

    return _signed_post(
        "/openApi/swap/v2/trade/order",
        params
    )


# ============================================================
# STOP LOSS / TAKE PROFIT
# ============================================================

def place_stop_loss(
    symbol,
    side,
    quantity,
    stop_price,
    position_side=None,
):
    """
    Place stop-loss order.
    """

    symbol = normalize_symbol(symbol)

    if side.upper() == "BUY":
        working_side = "SELL"
    else:
        working_side = "BUY"

    params = {
        "symbol": symbol,
        "side": working_side,
        "positionSide": (
            position_side
            if position_side
            else "BOTH"
        ),
        "type": "STOP_MARKET",
        "stopPrice": stop_price,
        "closePosition": "true",
        "workingType": WORKING_TYPE,
    }

    return _signed_post(
        "/openApi/swap/v2/trade/order",
        params
    )


def place_take_profit(
    symbol,
    side,
    quantity,
    take_profit,
    position_side=None,
):
    """
    Place take-profit order.
    """

    symbol = normalize_symbol(symbol)

    if side.upper() == "BUY":
        working_side = "SELL"
    else:
        working_side = "BUY"

    params = {
        "symbol": symbol,
        "side": working_side,
        "positionSide": (
            position_side
            if position_side
            else "BOTH"
        ),
        "type": "TAKE_PROFIT_MARKET",
        "stopPrice": take_profit,
        "closePosition": "true",
        "workingType": WORKING_TYPE,
    }

    return _signed_post(
        "/openApi/swap/v2/trade/order",
        params
    )


# ============================================================
# SETUP PARSING
# ============================================================

def _setup_value(setup, *keys):
    """
    Read setup field using several possible names.
    """

    if not isinstance(setup, dict):
        return None

    for key in keys:
        if key in setup:
            value = setup[key]

            if value is not None:
                return value

    return None


def _setup_symbol(setup):
    return _setup_value(
        setup,
        "symbol",
        "coin",
        "ticker"
    )


def _setup_direction(setup):
    value = _setup_value(
        setup,
        "direction",
        "side"
    )

    if value is None:
        return None

    value = str(value).upper()

    if value in {
        "LONG",
        "BUY",
    }:
        return "LONG"

    if value in {
        "SHORT",
        "SELL",
    }:
        return "SHORT"

    return None


def _setup_entry(setup):
    return _to_float(
        _setup_value(
            setup,
            "entry",
            "entry_price"
        )
    )


def _setup_sl(setup):
    return _to_float(
        _setup_value(
            setup,
            "sl",
            "stop_loss",
            "stop"
        )
    )


def _setup_tp(setup):
    return _to_float(
        _setup_value(
            setup,
            "tp",
            "take_profit",
            "target"
        )
    )


# ============================================================
# OPEN TRADE
# ============================================================

def open_trade(setup):
    """
    Main function used by TradeMind.

    OFF:
        Does nothing.

    PAPER:
        Simulates opening.

    CONFIRM:
        Returns prepared order information.

    AUTO:
        Sends real market order.
    """

    symbol = _setup_symbol(setup)
    direction = _setup_direction(setup)

    entry = _setup_entry(setup)
    stop_loss = _setup_sl(setup)
    take_profit = _setup_tp(setup)

    if not symbol:
        return {
            "ok": False,
            "status": "ERROR",
            "message": "Setup has no symbol",
        }

    if direction not in {
        "LONG",
        "SHORT",
    }:
        return {
            "ok": False,
            "status": "ERROR",
            "message": "Setup has invalid direction",
        }

    if entry <= 0:
        return {
            "ok": False,
            "status": "ERROR",
            "message": "Invalid entry",
        }

    if stop_loss <= 0:
        return {
            "ok": False,
            "status": "ERROR",
            "message": "Invalid stop loss",
        }

    if take_profit <= 0:
        return {
            "ok": False,
            "status": "ERROR",
            "message": "Invalid take profit",
        }

    quantity = calculate_quantity(
        symbol,
        entry,
        stop_loss,
        RISK_USDT
    )

    if quantity <= 0:
        return {
            "ok": False,
            "status": "ERROR",
            "message": "Could not calculate quantity",
        }

    if direction == "LONG":
        order_side = "BUY"
        position_side = "LONG"
    else:
        order_side = "SELL"
        position_side = "SHORT"

    result = {
        "ok": False,
        "status": MODE,
        "symbol": normalize_symbol(symbol),
        "direction": direction,
        "side": order_side,
        "position_side": position_side,
        "entry": entry,
        "sl": stop_loss,
        "tp": take_profit,
        "quantity": quantity,
        "risk_usdt": RISK_USDT,
        "leverage": LEVERAGE,
    }

    # --------------------------------------------------------
    # OFF
    # --------------------------------------------------------

    if MODE == "OFF":
        result.update({
            "ok": True,
            "status": "OFF",
            "message": "BingX is OFF",
        })

        return result

    # --------------------------------------------------------
    # PAPER
    # --------------------------------------------------------

    if MODE == "PAPER":
        result.update({
            "ok": True,
            "status": "PAPER",
            "message": "Paper trade created",
            "order": None,
        })

        logger.info(
            "PAPER TRADE: %s %s qty=%s entry=%s SL=%s TP=%s",
            direction,
            symbol,
            quantity,
            entry,
            stop_loss,
            take_profit,
        )

        return result

    # --------------------------------------------------------
    # CONFIRM
    # --------------------------------------------------------

    if MODE == "CONFIRM":
        result.update({
            "ok": True,
            "status": "CONFIRM",
            "message": "Trade prepared; confirmation required",
            "order": None,
        })

        return result

    # --------------------------------------------------------
    # AUTO
    # --------------------------------------------------------

    if MODE != "AUTO":
        result.update({
            "ok": False,
            "status": "ERROR",
            "message": f"Unknown BINGX_MODE: {MODE}",
        })

        return result

    if not API_KEY or not SECRET_KEY:
        result.update({
            "ok": False,
            "status": "ERROR",
            "message": "BingX API keys are missing",
        })

        return result

    # Set leverage before opening.
    leverage_result = set_leverage(
        symbol,
        LEVERAGE
    )

    if leverage_result.get("code") != 0:
        logger.warning(
            "Could not set leverage: %s",
            leverage_result
        )

    # Open position.
    order_result = place_order(
        symbol=symbol,
        side=order_side,
        quantity=quantity,
        position_side=position_side,
        order_type="MARKET",
    )

    result["order"] = order_result

    if order_result.get("code") != 0:
        result.update({
            "ok": False,
            "status": "ERROR",
            "message": (
                order_result.get("msg")
                or "BingX order failed"
            ),
        })

        return result

    result.update({
        "ok": True,
        "status": "OPENED",
        "message": "BingX position opened",
    })

    logger.warning(
        "LIVE TRADE OPENED: %s %s qty=%s",
        direction,
        symbol,
        quantity,
    )

    # --------------------------------------------------------
    # Attach SL
    # --------------------------------------------------------

    sl_result = place_stop_loss(
        symbol=symbol,
        side=order_side,
        quantity=quantity,
        stop_price=stop_loss,
        position_side=position_side,
    )

    result["sl_order"] = sl_result

    # --------------------------------------------------------
    # Attach TP
    # --------------------------------------------------------

    tp_result = place_take_profit(
        symbol=symbol,
        side=order_side,
        quantity=quantity,
        take_profit=take_profit,
        position_side=position_side,
    )

    result["tp_order"] = tp_result

    return result


# ============================================================
# CONFIRM EXECUTION
# ============================================================

def execute_confirmed(setup):
    """
    Used by TradeMind when BINGX_MODE=CONFIRM
    and user confirms a prepared setup.

    Temporarily executes the same logic as AUTO.
    """

    if MODE == "OFF":
        return {
            "ok": False,
            "status": "OFF",
            "message": "BingX is OFF",
        }

    if MODE == "PAPER":
        return open_trade(setup)

    # Validate setup first.
    symbol = _setup_symbol(setup)
    direction = _setup_direction(setup)

    entry = _setup_entry(setup)
    stop_loss = _setup_sl(setup)
    take_profit = _setup_tp(setup)

    if not symbol or not direction:
        return {
            "ok": False,
            "status": "ERROR",
            "message": "Invalid setup",
        }

    quantity = calculate_quantity(
        symbol,
        entry,
        stop_loss,
        RISK_USDT
    )

    if quantity <= 0:
        return {
            "ok": False,
            "status": "ERROR",
            "message": "Invalid quantity",
        }

    if direction == "LONG":
        order_side = "BUY"
        position_side = "LONG"
    else:
        order_side = "SELL"
        position_side = "SHORT"

    if not API_KEY or not SECRET_KEY:
        return {
            "ok": False,
            "status": "ERROR",
            "message": "BingX API keys are missing",
        }

    set_leverage(
        symbol,
        LEVERAGE
    )

    order_result = place_order(
        symbol=symbol,
        side=order_side,
        quantity=quantity,
        position_side=position_side,
        order_type="MARKET",
    )

    if order_result.get("code") != 0:
        return {
            "ok": False,
            "status": "ERROR",
            "message": (
                order_result.get("msg")
                or "Order failed"
            ),
            "order": order_result,
        }

    sl_result = place_stop_loss(
        symbol=symbol,
        side=order_side,
        quantity=quantity,
        stop_price=stop_loss,
        position_side=position_side,
    )

    tp_result = place_take_profit(
        symbol=symbol,
        side=order_side,
        quantity=quantity,
        take_profit=take_profit,
        position_side=position_side,
    )

    return {
        "ok": True,
        "status": "OPENED",
        "message": "Confirmed BingX trade opened",
        "symbol": normalize_symbol(symbol),
        "direction": direction,
        "quantity": quantity,
        "entry": entry,
        "sl": stop_loss,
        "tp": take_profit,
        "order": order_result,
        "sl_order": sl_result,
        "tp_order": tp_result,
    }


# ============================================================
# CLOSE POSITION
# ============================================================

def close_position(symbol=None):
    """
    Close current position.

    If symbol is None:
        tries to close all active positions.
    """

    positions_result = get_positions(
        symbol
    )

    if positions_result.get("code") != 0:
        return positions_result

    data = positions_result.get("data") or []

    if isinstance(data, dict):
        data = [data]

    results = []

    for position in data:
        position_amt = _to_float(
            position.get("positionAmt")
            or position.get("positionAmount")
            or position.get("amount")
            or position.get("availableAmt")
        )

        if abs(position_amt) <= 0:
            continue

        pos_symbol = normalize_symbol(
            position.get("symbol")
            or symbol
            or ""
        )

        if not pos_symbol:
            continue

        position_side = str(
            position.get("positionSide")
            or "BOTH"
        ).upper()

        if position_amt > 0:
            side = "SELL"
        else:
            side = "BUY"

        quantity = abs(position_amt)

        close_params = {
            "symbol": pos_symbol,
            "side": side,
            "positionSide": position_side,
            "type": "MARKET",
            "quantity": quantity,
        }

        result = _signed_post(
            "/openApi/swap/v2/trade/order",
            close_params
        )

        results.append({
            "symbol": pos_symbol,
            "quantity": quantity,
            "side": side,
            "result": result,
        })

    return {
        "code": 0,
        "msg": "Close operation completed",
        "data": results,
    }


# ============================================================
# CANCEL ALL ORDERS
# ============================================================

def cancel_all_orders(symbol):
    """
    Cancel all open orders for a symbol.
    """

    symbol = normalize_symbol(symbol)

    return _signed_post(
        "/openApi/swap/v2/trade/allOpenOrders",
        {
            "symbol": symbol,
        }
    )


# ============================================================
# OPEN ORDERS
# ============================================================

def get_open_orders(symbol=None):
    """
    Get open orders.
    """

    params = {}

    if symbol:
        params["symbol"] = normalize_symbol(symbol)

    return _signed_get(
        "/openApi/swap/v2/trade/openOrders",
        params
    )


# ============================================================
# HEALTH CHECK
# ============================================================

def ping():
    """
    Simple BingX API test.
    """

    result = _public_get(
        "/openApi/swap/v2/server/time"
    )

    return result


def health():
    """
    Full integration health status.
    """

    status = config_status()

    result = {
        "mode": status["mode"],
        "configured": status["ready"],
        "api_reachable": False,
        "account_reachable": False,
    }

    ping_result = ping()

    if ping_result.get("code") == 0:
        result["api_reachable"] = True

    if (
        status["ready"]
        and MODE != "OFF"
    ):
        balance = get_balance()

        if balance.get("code") == 0:
            result["account_reachable"] = True

    return result


# ============================================================
# MODULE INFO
# ============================================================

__version__ = "1.0"

__all__ = [
    "MODE",
    "RISK_USDT",
    "LEVERAGE",
    "mode",
    "is_enabled",
    "is_live",
    "config_status",
    "get_ticker",
    "get_contracts",
    "get_contract",
    "get_balance",
    "get_positions",
    "get_position_mode",
    "set_position_mode",
    "set_leverage",
    "calculate_quantity",
    "place_order",
    "place_stop_loss",
    "place_take_profit",
    "open_trade",
    "execute_confirmed",
    "close_position",
    "cancel_all_orders",
    "get_open_orders",
    "ping",
    "health",
]