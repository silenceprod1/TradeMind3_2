"""
TradeMind — BingX Futures integration
v1.0

Safety:
    BINGX_MODE=OFF       -> no orders
    BINGX_MODE=PAPER     -> simulated orders
    BINGX_MODE=CONFIRM   -> setup waits for /execute
    BINGX_MODE=AUTO      -> real orders

Required:
    BINGX_API_KEY
    BINGX_SECRET_KEY

Optional:
    BINGX_ENV=prod-live
    BINGX_LEVERAGE=5
    BINGX_RISK_USDT=5
    BINGX_WORKING_TYPE=MARK_PRICE

Use an API key with trading permission only. Never enable withdrawals.
"""

import hashlib
import hmac
import json
import os
import time
from decimal import Decimal, ROUND_DOWN
from urllib.parse import urlencode

import requests

PRIMARY = "https://open-api.bingx.com"
FALLBACK = "https://open-api.bingx.pro"
ENV = os.getenv("BINGX_ENV", "prod-live").strip()
MODE = os.getenv("BINGX_MODE", "OFF").strip().upper()
API_KEY = os.getenv("BINGX_API_KEY", "").strip()
SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "").strip()

LEVERAGE = int(os.getenv("BINGX_LEVERAGE", "5"))
RISK_USDT = float(os.getenv("BINGX_RISK_USDT", "5"))
WORKING_TYPE = os.getenv("BINGX_WORKING_TYPE", "MARK_PRICE").upper()
RECV_WINDOW = 5000
TIMEOUT = 10

if MODE not in {"OFF", "PAPER", "CONFIRM", "AUTO"}:
    MODE = "OFF"

BASE_URLS = (
    [PRIMARY, FALLBACK]
    if ENV != "prod-vst"
    else [
        "https://open-api-vst.bingx.com",
        "https://open-api-vst.bingx.pro",
    ]
)


class BingXError(Exception):
    pass


def enabled():
    return bool(API_KEY and SECRET_KEY)


def mode():
    return MODE


def config_status():
    return {
        "mode": MODE,
        "env": ENV,
        "configured": enabled(),
        "leverage": LEVERAGE,
        "risk_usdt": RISK_USDT,
        "working_type": WORKING_TYPE,
    }


def _validate_params(params):
    forbidden = "&=?#\r\n"
    for key, value in params.items():
        if any(ch in str(value) for ch in forbidden):
            raise BingXError(f'Invalid character in parameter "{key}"')


def _signed_request(method, path, params=None):
    if not enabled():
        raise BingXError(
            "BingX API не настроен: BINGX_API_KEY/BINGX_SECRET_KEY."
        )

    params = dict(params or {})
    params["timestamp"] = int(time.time() * 1000)
    params.setdefault("recvWindow", RECV_WINDOW)
    _validate_params(params)

    query = urlencode(sorted(params.items()), doseq=False, safe="")
    signature = hmac.new(
        SECRET_KEY.encode(),
        query.encode(),
        hashlib.sha256,
    ).hexdigest()
    signed = f"{query}&signature={signature}"

    last_error = None

    for base in BASE_URLS:
        try:
            url = f"{base}{path}?{signed}"
            headers = {
                "X-BX-APIKEY": API_KEY,
                "X-SOURCE-KEY": "BX-AI-SKILL",
            }

            if method.upper() == "GET":
                response = requests.get(
                    url, headers=headers, timeout=TIMEOUT
                )
            elif method.upper() == "POST":
                headers["Content-Type"] = (
                    "application/x-www-form-urlencoded"
                )
                response = requests.post(
                    url, headers=headers, timeout=TIMEOUT
                )
            elif method.upper() == "DELETE":
                response = requests.delete(
                    url, headers=headers, timeout=TIMEOUT
                )
            else:
                raise BingXError(f"Unsupported method: {method}")

            response.raise_for_status()
            payload = response.json()

            if payload.get("code") != 0:
                raise BingXError(
                    f"BingX error {payload.get('code')}: "
                    f"{payload.get('msg', 'unknown error')}"
                )

            return payload.get("data")

        except BingXError:
            raise
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            continue
        except requests.RequestException as exc:
            raise BingXError(str(exc)) from exc
        except ValueError as exc:
            raise BingXError(f"Invalid BingX JSON: {exc}") from exc

    raise BingXError(f"BingX network error: {last_error}")


def _public_request(path, params=None):
    last_error = None

    for base in BASE_URLS:
        try:
            response = requests.get(
                f"{base}{path}",
                params=params or {},
                headers={"X-SOURCE-KEY": "BX-AI-SKILL"},
                timeout=TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()

            if payload.get("code") != 0:
                raise BingXError(
                    f"BingX error {payload.get('code')}: "
                    f"{payload.get('msg', 'unknown error')}"
                )

            return payload.get("data")

        except BingXError:
            raise
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            continue
        except requests.RequestException as exc:
            raise BingXError(str(exc)) from exc

    raise BingXError(f"BingX network error: {last_error}")


def normalize_symbol(symbol):
    symbol = str(symbol).upper().strip()
    if "-" in symbol:
        return symbol
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}-USDT"
    raise BingXError(f"Неподдерживаемый символ: {symbol}")


def get_balance():
    data = _signed_request(
        "GET", "/openApi/swap/v3/user/balance"
    )
    if isinstance(data, list):
        for item in data:
            if item.get("asset") == "USDT":
                return item
    if isinstance(data, dict):
        if data.get("asset") == "USDT":
            return data
        if isinstance(data.get("balance"), list):
            for item in data["balance"]:
                if item.get("asset") == "USDT":
                    return item
    return data


def get_positions(symbol=None):
    params = {}
    if symbol:
        params["symbol"] = normalize_symbol(symbol)

    data = _signed_request(
        "GET", "/openApi/swap/v2/user/positions", params
    )

    if not data:
        return []
    if isinstance(data, dict):
        if isinstance(data.get("positions"), list):
            return data["positions"]
        return [data]
    return data


def get_position(symbol, direction=None):
    wanted_symbol = normalize_symbol(symbol)
    wanted_direction = direction.upper() if direction else None

    for position in get_positions(symbol):
        if position.get("symbol") != wanted_symbol:
            continue

        side = str(position.get("positionSide", "")).upper()
        try:
            amount = abs(float(position.get("positionAmt", 0)))
        except Exception:
            amount = 0

        if amount <= 0:
            continue
        if wanted_direction and side != wanted_direction:
            continue
        return position

    return None


def get_position_mode():
    data = _signed_request(
        "GET", "/openApi/swap/v1/positionSide/dual"
    )
    return bool(
        data.get("dualSidePosition")
        if isinstance(data, dict)
        else False
    )


def get_leverage(symbol):
    return _signed_request(
        "GET",
        "/openApi/swap/v2/trade/leverage",
        {"symbol": normalize_symbol(symbol)},
    )


def set_leverage(symbol, direction, leverage=None):
    direction = direction.upper()
    if direction not in {"LONG", "SHORT"}:
        raise BingXError("Direction must be LONG or SHORT.")

    value = int(leverage if leverage is not None else LEVERAGE)
    if value < 1:
        raise BingXError("Leverage must be >= 1.")

    return _signed_request(
        "POST",
        "/openApi/swap/v2/trade/leverage",
        {
            "symbol": normalize_symbol(symbol),
            "side": direction,
            "leverage": value,
        },
    )


def get_contract(symbol):
    symbol = normalize_symbol(symbol)
    data = _public_request(
        "/openApi/swap/v2/quote/contracts",
        {"symbol": symbol},
    )

    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        for item in data:
            if item.get("symbol") == symbol:
                return item
    return None


def _dec(value, default="0"):
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _floor_step(value, step):
    value = _dec(value)
    step = _dec(step)
    if step <= 0:
        return value
    units = (value / step).to_integral_value(
        rounding=ROUND_DOWN
    )
    return units * step


def _fmt(value):
    text = format(_dec(value), "f")
    return text.rstrip("0").rstrip(".") or "0"


def normalize_quantity(symbol, quantity):
    contract = get_contract(symbol) or {}

    # BingX separates minimum quantity from decimal precision.
    # tradeMinQuantity is a minimum, not a step size.
    min_qty = (
        contract.get("tradeMinQuantity")
        or contract.get("minQty")
        or "0"
    )

    precision = contract.get("quantityPrecision")
    if precision is not None:
        try:
            precision = int(precision)
            quantum = Decimal("1").scaleb(-precision)
            quantity = _floor_step(quantity, quantum)
        except Exception:
            pass

    if quantity <= 0 or quantity < _dec(min_qty):
        raise BingXError(
            f"Количество {quantity} меньше минимального "
            f"для {normalize_symbol(symbol)}: {min_qty}"
        )

    return _fmt(quantity)


def calculate_quantity(entry, sl, risk_usdt=None):
    entry = float(entry)
    sl = float(sl)
    risk = float(RISK_USDT if risk_usdt is None else risk_usdt)

    if entry <= 0 or sl <= 0:
        raise BingXError("Entry/SL должны быть > 0.")
    if entry == sl:
        raise BingXError("Entry и SL не могут совпадать.")
    if risk <= 0:
        raise BingXError("Risk USDT должен быть > 0.")

    return risk / abs(entry - sl)


def _validate_setup(setup):
    for key in ("symbol", "direction", "entry", "sl", "tp"):
        if setup.get(key) is None:
            raise BingXError(f"В setup отсутствует {key}.")

    direction = str(setup["direction"]).upper()
    entry = float(setup["entry"])
    sl = float(setup["sl"])
    tp = float(setup["tp"])

    if direction not in {"LONG", "SHORT"}:
        raise BingXError("Direction must be LONG or SHORT.")

    if direction == "LONG" and not (sl < entry < tp):
        raise BingXError("LONG должен иметь SL < Entry < TP.")

    if direction == "SHORT" and not (tp < entry < sl):
        raise BingXError("SHORT должен иметь TP < Entry < SL.")


def _market_params(
    symbol, direction, quantity, sl, tp, client_order_id=None
):
    direction = direction.upper()

    if direction == "LONG":
        side = "BUY"
        position_side = "LONG"
    else:
        side = "SELL"
        position_side = "SHORT"

    params = {
        "symbol": normalize_symbol(symbol),
        "side": side,
        "positionSide": position_side,
        "type": "MARKET",
        "quantity": quantity,
        "workingType": WORKING_TYPE,
    }

    if client_order_id:
        params["clientOrderId"] = client_order_id

    params["stopLoss"] = json.dumps(
        {
            "type": "STOP_MARKET",
            "stopPrice": float(sl),
            "workingType": WORKING_TYPE,
            "stopGuaranteed": False,
        },
        separators=(",", ":"),
    )

    params["takeProfit"] = json.dumps(
        {
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": float(tp),
            "workingType": WORKING_TYPE,
            "stopGuaranteed": False,
        },
        separators=(",", ":"),
    )

    return params


def place_market_order(
    symbol, direction, quantity, sl, tp, client_order_id=None
):
    direction = direction.upper()

    if direction not in {"LONG", "SHORT"}:
        raise BingXError("Direction must be LONG or SHORT.")

    sl = float(sl)
    tp = float(tp)

    if sl <= 0 or tp <= 0:
        raise BingXError("SL/TP должны быть > 0.")

    if not get_position_mode():
        raise BingXError(
            "BingX находится в One-way Mode. "
            "TradeMind использует Hedge Mode (LONG/SHORT). "
            "Переключи режим на Hedge Mode перед AUTO."
        )

    set_leverage(symbol, direction, LEVERAGE)

    quantity = normalize_quantity(symbol, quantity)

    return _signed_request(
        "POST",
        "/openApi/swap/v2/trade/order",
        _market_params(
            symbol,
            direction,
            quantity,
            sl,
            tp,
            client_order_id,
        ),
    )


def open_trade(setup, risk_usdt=None):
    _validate_setup(setup)

    quantity = normalize_quantity(
        setup["symbol"],
        calculate_quantity(
            setup["entry"],
            setup["sl"],
            risk_usdt,
        ),
    )

    payload = {
        "symbol": normalize_symbol(setup["symbol"]),
        "direction": str(setup["direction"]).upper(),
        "entry": float(setup["entry"]),
        "sl": float(setup["sl"]),
        "tp": float(setup["tp"]),
        "quantity": quantity,
        "risk_usdt": float(
            RISK_USDT if risk_usdt is None else risk_usdt
        ),
        "leverage": LEVERAGE,
    }

    if MODE == "OFF":
        return {"mode": MODE, "status": "disabled", "payload": payload}

    if MODE == "PAPER":
        return {"mode": MODE, "status": "simulated", "payload": payload}

    if MODE == "CONFIRM":
        return {"mode": MODE, "status": "pending_confirmation", "payload": payload}

    data = place_market_order(
        symbol=setup["symbol"],
        direction=setup["direction"],
        quantity=quantity,
        sl=setup["sl"],
        tp=setup["tp"],
        client_order_id=setup.get("client_order_id"),
    )

    return {
        "mode": MODE,
        "status": "submitted",
        "payload": payload,
        "data": data,
    }


def execute_confirmed(setup):
    if MODE != "CONFIRM":
        raise BingXError("Нужен BINGX_MODE=CONFIRM.")

    _validate_setup(setup)

    quantity = normalize_quantity(
        setup["symbol"],
        calculate_quantity(
            setup["entry"],
            setup["sl"],
            setup.get("risk_usdt", RISK_USDT),
        ),
    )

    return place_market_order(
        symbol=setup["symbol"],
        direction=setup["direction"],
        quantity=quantity,
        sl=setup["sl"],
        tp=setup["tp"],
        client_order_id=setup.get("client_order_id"),
    )


def close_position(symbol=None):
    params = {}
    if symbol:
        params["symbol"] = normalize_symbol(symbol)

    return _signed_request(
        "POST",
        "/openApi/swap/v2/trade/closeAllPositions",
        params,
    )
