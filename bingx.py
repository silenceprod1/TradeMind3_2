"""
TradeMind 4.1 — BingX Futures integration

РЕЖИМЫ:

    BINGX_MODE=OFF
        Только анализ/сигналы.
        Реальные ордера НЕ отправляются.

    BINGX_MODE=PAPER
        Виртуальные сделки.
        Реальные ордера НЕ отправляются.

    BINGX_MODE=CONFIRM
        Сетап сохраняется.
        Реальный ордер отправляется только после /execute.

    BINGX_MODE=AUTO
        Реальные сделки.
        ВКЛЮЧАТЬ ТОЛЬКО ПОСЛЕ ОТДЕЛЬНОЙ ПРОВЕРКИ.

TradeMind:
    1H context
    -> Major Liquidity
    -> 5M Liquidity Sweep
    -> 15M Confirmation
    -> 5M Trigger
    -> Entry
    -> SL
    -> TP = 2R

Риск:
    BINGX_RISK_USDT
    По умолчанию $5.

Важно:
    Leverage не меняет риск по SL.
    Риск считается:
        quantity = risk_usdt / abs(entry - sl)

Никогда не выдавай API-ключу право на withdrawals.
"""

import hashlib
import hmac
import json
import os
import time
from decimal import Decimal, ROUND_DOWN
from urllib.parse import urlencode

import requests


# ============================================================
# CONFIG
# ============================================================

PRIMARY = "https://open-api.bingx.com"
FALLBACK = "https://open-api.bingx.pro"

ENV = os.getenv("BINGX_ENV", "prod-live").strip()

MODE = os.getenv("BINGX_MODE", "OFF").strip().upper()

API_KEY = os.getenv("BINGX_API_KEY", "").strip()
SECRET_KEY = os.getenv("BINGX_SECRET_KEY", "").strip()

LEVERAGE = int(os.getenv("BINGX_LEVERAGE", "5"))

RISK_USDT = float(
    os.getenv("BINGX_RISK_USDT", "5")
)

WORKING_TYPE = os.getenv(
    "BINGX_WORKING_TYPE",
    "MARK_PRICE",
).strip().upper()

RECV_WINDOW = 5000
TIMEOUT = 10

# Безопасность:
# AUTO не должен случайно включиться из-за неправильного значения.
ALLOWED_MODES = {
    "OFF",
    "PAPER",
    "CONFIRM",
    "AUTO",
}

if MODE not in ALLOWED_MODES:
    MODE = "OFF"


if ENV == "prod-vst":
    BASE_URLS = [
        "https://open-api-vst.bingx.com",
        "https://open-api-vst.bingx.pro",
    ]
else:
    BASE_URLS = [
        PRIMARY,
        FALLBACK,
    ]


# ============================================================
# ERRORS
# ============================================================

class BingXError(Exception):
    pass


# ============================================================
# BASIC STATUS
# ============================================================

def enabled():
    return bool(API_KEY and SECRET_KEY)


def is_enabled():
    return enabled()


def is_live():
    return MODE == "AUTO" and enabled()


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


# ============================================================
# VALIDATION
# ============================================================

def _validate_params(params):
    forbidden = "&=?#\r\n"

    for key, value in params.items():
        if any(
            ch in str(value)
            for ch in forbidden
        ):
            raise BingXError(
                f'Invalid character in parameter "{key}"'
            )


def _validate_positive_number(
    name,
    value,
):
    try:
        number = float(value)
    except Exception as exc:
        raise BingXError(
            f"{name} должен быть числом."
        ) from exc

    if number <= 0:
        raise BingXError(
            f"{name} должен быть > 0."
        )

    return number


# ============================================================
# HTTP / SIGNATURE
# ============================================================

def _signed_request(
    method,
    path,
    params=None,
):
    if not enabled():
        raise BingXError(
            "BingX API не настроен: "
            "BINGX_API_KEY/BINGX_SECRET_KEY."
        )

    params = dict(params or {})

    params["timestamp"] = int(
        time.time() * 1000
    )

    params.setdefault(
        "recvWindow",
        RECV_WINDOW,
    )

    _validate_params(params)

    query = urlencode(
        sorted(params.items()),
        doseq=False,
        safe="",
    )

    signature = hmac.new(
        SECRET_KEY.encode(),
        query.encode(),
        hashlib.sha256,
    ).hexdigest()

    signed = (
        f"{query}&signature={signature}"
    )

    last_error = None

    for base in BASE_URLS:
        try:
            url = (
                f"{base}{path}?{signed}"
            )

            headers = {
                "X-BX-APIKEY": API_KEY,
                "X-SOURCE-KEY": "BX-AI-SKILL",
            }

            method_upper = method.upper()

            if method_upper == "GET":
                response = requests.get(
                    url,
                    headers=headers,
                    timeout=TIMEOUT,
                )

            elif method_upper == "POST":
                headers["Content-Type"] = (
                    "application/x-www-form-urlencoded"
                )

                response = requests.post(
                    url,
                    headers=headers,
                    timeout=TIMEOUT,
                )

            elif method_upper == "DELETE":
                response = requests.delete(
                    url,
                    headers=headers,
                    timeout=TIMEOUT,
                )

            else:
                raise BingXError(
                    f"Unsupported method: {method}"
                )

            response.raise_for_status()

            payload = response.json()

            if payload.get("code") != 0:
                raise BingXError(
                    f"BingX error "
                    f"{payload.get('code')}: "
                    f"{payload.get('msg', 'unknown error')}"
                )

            return payload.get("data")

        except BingXError:
            raise

        except (
            requests.Timeout,
            requests.ConnectionError,
        ) as exc:
            last_error = exc
            continue

        except requests.RequestException as exc:
            raise BingXError(
                str(exc)
            ) from exc

        except ValueError as exc:
            raise BingXError(
                f"Invalid BingX JSON: {exc}"
            ) from exc

    raise BingXError(
        f"BingX network error: {last_error}"
    )


def _public_request(
    path,
    params=None,
):
    last_error = None

    for base in BASE_URLS:
        try:
            response = requests.get(
                f"{base}{path}",
                params=params or {},
                headers={
                    "X-SOURCE-KEY": "BX-AI-SKILL"
                },
                timeout=TIMEOUT,
            )

            response.raise_for_status()

            payload = response.json()

            if payload.get("code") != 0:
                raise BingXError(
                    f"BingX error "
                    f"{payload.get('code')}: "
                    f"{payload.get('msg', 'unknown error')}"
                )

            return payload.get("data")

        except BingXError:
            raise

        except (
            requests.Timeout,
            requests.ConnectionError,
        ) as exc:
            last_error = exc
            continue

        except requests.RequestException as exc:
            raise BingXError(
                str(exc)
            ) from exc

    raise BingXError(
        f"BingX network error: {last_error}"
    )


# ============================================================
# SYMBOL
# ============================================================

def normalize_symbol(symbol):
    symbol = str(
        symbol
    ).upper().strip()

    if "-" in symbol:
        return symbol

    if symbol.endswith("USDT"):
        return (
            f"{symbol[:-4]}-USDT"
        )

    raise BingXError(
        f"Неподдерживаемый символ: {symbol}"
    )


# ============================================================
# BALANCE
# ============================================================

def get_balance():
    data = _signed_request(
        "GET",
        "/openApi/swap/v3/user/balance",
    )

    if isinstance(data, list):
        for item in data:
            if item.get("asset") == "USDT":
                return item

    if isinstance(data, dict):

        if data.get("asset") == "USDT":
            return data

        balance = data.get(
            "balance"
        )

        if isinstance(balance, list):
            for item in balance:
                if item.get("asset") == "USDT":
                    return item

    return data


# ============================================================
# POSITIONS
# ============================================================

def get_positions(symbol=None):
    params = {}

    if symbol:
        params["symbol"] = normalize_symbol(
            symbol
        )

    data = _signed_request(
        "GET",
        "/openApi/swap/v2/user/positions",
        params,
    )

    if not data:
        return []

    if isinstance(data, dict):

        if isinstance(
            data.get("positions"),
            list,
        ):
            return data["positions"]

        return [data]

    return data


def get_position(
    symbol,
    direction=None,
):
    wanted_symbol = normalize_symbol(
        symbol
    )

    wanted_direction = (
        direction.upper()
        if direction
        else None
    )

    for position in get_positions(
        symbol
    ):

        if position.get(
            "symbol"
        ) != wanted_symbol:
            continue

        side = str(
            position.get(
                "positionSide",
                "",
            )
        ).upper()

        try:
            amount = abs(
                float(
                    position.get(
                        "positionAmt",
                        0,
                    )
                )
            )
        except Exception:
            amount = 0

        if amount <= 0:
            continue

        if (
            wanted_direction
            and side != wanted_direction
        ):
            continue

        return position

    return None


def has_open_position(
    symbol,
    direction=None,
):
    return (
        get_position(
            symbol,
            direction,
        )
        is not None
    )


# ============================================================
# POSITION MODE
# ============================================================

def get_position_mode():
    data = _signed_request(
        "GET",
        "/openApi/swap/v1/positionSide/dual",
    )

    if isinstance(data, dict):
        return bool(
            data.get(
                "dualSidePosition"
            )
        )

    return False


# ============================================================
# LEVERAGE
# ============================================================

def get_leverage(symbol):
    return _signed_request(
        "GET",
        "/openApi/swap/v2/trade/leverage",
        {
            "symbol": normalize_symbol(
                symbol
            )
        },
    )


def set_leverage(
    symbol,
    direction,
    leverage=None,
):
    direction = direction.upper()

    if direction not in {
        "LONG",
        "SHORT",
    }:
        raise BingXError(
            "Direction must be LONG or SHORT."
        )

    value = int(
        leverage
        if leverage is not None
        else LEVERAGE
    )

    if value < 1:
        raise BingXError(
            "Leverage must be >= 1."
        )

    return _signed_request(
        "POST",
        "/openApi/swap/v2/trade/leverage",
        {
            "symbol": normalize_symbol(
                symbol
            ),
            "side": direction,
            "leverage": value,
        },
    )


# ============================================================
# CONTRACT INFO
# ============================================================

def get_contract(symbol):
    symbol = normalize_symbol(
        symbol
    )

    data = _public_request(
        "/openApi/swap/v2/quote/contracts",
        {
            "symbol": symbol
        },
    )

    if isinstance(data, dict):
        return data

    if isinstance(data, list):
        for item in data:
            if item.get(
                "symbol"
            ) == symbol:
                return item

    return None


# ============================================================
# DECIMAL HELPERS
# ============================================================

def _dec(
    value,
    default="0",
):
    try:
        return Decimal(
            str(value)
        )
    except Exception:
        return Decimal(default)


def _floor_step(
    value,
    step,
):
    value = _dec(value)
    step = _dec(step)

    if step <= 0:
        return value

    units = (
        value / step
    ).to_integral_value(
        rounding=ROUND_DOWN
    )

    return units * step


def _fmt(value):
    text = format(
        _dec(value),
        "f",
    )

    return (
        text.rstrip("0")
        .rstrip(".")
        or "0"
    )


# ============================================================
# QUANTITY
# ============================================================

def normalize_quantity(
    symbol,
    quantity,
):
    contract = (
        get_contract(symbol)
        or {}
    )

    step = (
        contract.get(
            "tradeMinQuantity"
        )
        or contract.get(
            "stepSize"
        )
    )

    min_qty = (
        contract.get(
            "tradeMinQuantity"
        )
        or contract.get(
            "minQty"
        )
        or "0"
    )

    if (
        not step
        and contract.get(
            "quantityPrecision"
        )
        is not None
    ):
        try:
            precision = int(
                contract[
                    "quantityPrecision"
                ]
            )

            step = Decimal(
                "1"
            ).scaleb(
                -precision
            )

        except Exception:
            step = "0"

    if not step:
        step = "0"

    quantity = _floor_step(
        quantity,
        step,
    )

    if (
        quantity <= 0
        or quantity < _dec(min_qty)
    ):
        raise BingXError(
            f"Количество {quantity} "
            f"меньше минимального "
            f"для {normalize_symbol(symbol)}: "
            f"{min_qty}"
        )

    return _fmt(quantity)


# ============================================================
# RISK / QUANTITY
# ============================================================

def calculate_quantity(
    entry,
    sl,
    risk_usdt=None,
):
    entry = float(entry)
    sl = float(sl)

    risk = float(
        RISK_USDT
        if risk_usdt is None
        else risk_usdt
    )

    if entry <= 0 or sl <= 0:
        raise BingXError(
            "Entry/SL должны быть > 0."
        )

    if entry == sl:
        raise BingXError(
            "Entry и SL не могут совпадать."
        )

    if risk <= 0:
        raise BingXError(
            "Risk USDT должен быть > 0."
        )

    return (
        risk
        / abs(entry - sl)
    )


def calculate_actual_risk(
    entry,
    sl,
    quantity,
):
    return (
        abs(
            float(entry)
            - float(sl)
        )
        * float(quantity)
    )


# ============================================================
# SETUP VALIDATION
# ============================================================

def _validate_setup(setup):

    for key in (
        "symbol",
        "direction",
        "entry",
        "sl",
        "tp",
    ):
        if setup.get(key) is None:
            raise BingXError(
                f"В setup отсутствует {key}."
            )

    direction = str(
        setup["direction"]
    ).upper()

    entry = float(
        setup["entry"]
    )

    sl = float(
        setup["sl"]
    )

    tp = float(
        setup["tp"]
    )

    if direction not in {
        "LONG",
        "SHORT",
    }:
        raise BingXError(
            "Direction must be LONG or SHORT."
        )

    if entry <= 0:
        raise BingXError(
            "Entry должен быть > 0."
        )

    if sl <= 0:
        raise BingXError(
            "SL должен быть > 0."
        )

    if tp <= 0:
        raise BingXError(
            "TP должен быть > 0."
        )

    if direction == "LONG":

        if not (
            sl < entry < tp
        ):
            raise BingXError(
                "LONG должен иметь "
                "SL < Entry < TP."
            )

    else:

        if not (
            tp < entry < sl
        ):
            raise BingXError(
                "SHORT должен иметь "
                "TP < Entry < SL."
            )

    # Проверяем RR.
    risk = abs(
        entry - sl
    )

    reward = abs(
        tp - entry
    )

    if risk <= 0:
        raise BingXError(
            "Risk должен быть > 0."
        )

    rr = reward / risk

    # TradeMind требует 2R.
    if abs(rr - 2.0) > 0.03:
        raise BingXError(
            f"TradeMind требует RR 1:2. "
            f"Получено RR={rr:.3f}."
        )


# ============================================================
# ORDER PARAMS
# ============================================================

def _market_params(
    symbol,
    direction,
    quantity,
    sl,
    tp,
    client_order_id=None,
):
    direction = direction.upper()

    if direction == "LONG":
        side = "BUY"
        position_side = "LONG"

    else:
        side = "SELL"
        position_side = "SHORT"

    params = {
        "symbol": normalize_symbol(
            symbol
        ),
        "side": side,
        "positionSide": position_side,
        "type": "MARKET",
        "quantity": quantity,
        "workingType": WORKING_TYPE,
    }

    if client_order_id:
        params[
            "clientOrderId"
        ] = client_order_id

    params["stopLoss"] = json.dumps(
        {
            "type": "STOP_MARKET",
            "stopPrice": float(sl),
            "workingType": WORKING_TYPE,
            "stopGuaranteed": False,
        },
        separators=(
            ",",
            ":",
        ),
    )

    params["takeProfit"] = json.dumps(
        {
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": float(tp),
            "workingType": WORKING_TYPE,
            "stopGuaranteed": False,
        },
        separators=(
            ",",
            ":",
        ),
    )

    return params


# ============================================================
# PLACE MARKET ORDER
# ============================================================

def place_market_order(
    symbol,
    direction,
    quantity,
    sl,
    tp,
    client_order_id=None,
):

    direction = direction.upper()

    if direction not in {
        "LONG",
        "SHORT",
    }:
        raise BingXError(
            "Direction must be LONG or SHORT."
        )

    sl = _validate_positive_number(
        "SL",
        sl,
    )

    tp = _validate_positive_number(
        "TP",
        tp,
    )

    # AUTO/CONFIRM требуют Hedge Mode.
    if not get_position_mode():
        raise BingXError(
            "BingX находится в One-way Mode. "
            "TradeMind использует Hedge Mode "
            "(LONG/SHORT). "
            "Переключи режим на Hedge Mode "
            "перед использованием BingX."
        )

    set_leverage(
        symbol,
        direction,
        LEVERAGE,
    )

    quantity = normalize_quantity(
        symbol,
        quantity,
    )

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


# ============================================================
# VERIFY POSITION
# ============================================================

def wait_for_position(
    symbol,
    direction,
    timeout_seconds=5,
    interval=0.5,
):
    started = time.time()

    while (
        time.time()
        - started
        < timeout_seconds
    ):
        position = get_position(
            symbol,
            direction,
        )

        if position:
            return position

        time.sleep(interval)

    return None


# ============================================================
# OPEN TRADE
# ============================================================

def open_trade(
    setup,
    risk_usdt=None,
):
    """
    Главная функция открытия сделки.

    OFF:
        ничего не открывает.

    PAPER:
        симуляция.

    CONFIRM:
        pending setup.

    AUTO:
        реальный ордер.
    """

    _validate_setup(setup)

    risk_value = (
        RISK_USDT
        if risk_usdt is None
        else float(risk_usdt)
    )

    raw_quantity = calculate_quantity(
        setup["entry"],
        setup["sl"],
        risk_value,
    )

    quantity = normalize_quantity(
        setup["symbol"],
        raw_quantity,
    )

    actual_quantity = float(
        quantity
    )

    actual_risk = calculate_actual_risk(
        setup["entry"],
        setup["sl"],
        actual_quantity,
    )

    payload = {
        "symbol": normalize_symbol(
            setup["symbol"]
        ),
        "direction": str(
            setup["direction"]
        ).upper(),
        "entry": float(
            setup["entry"]
        ),
        "sl": float(
            setup["sl"]
        ),
        "tp": float(
            setup["tp"]
        ),
        "quantity": quantity,
        "risk_usdt": float(
            risk_value
        ),
        "actual_risk_usdt": round(
            actual_risk,
            6,
        ),
        "leverage": LEVERAGE,
    }

    # --------------------------------------------------------
    # OFF
    # --------------------------------------------------------

    if MODE == "OFF":
        return {
            "ok": True,
            "mode": MODE,
            "status": "disabled",
            "payload": payload,
        }

    # --------------------------------------------------------
    # PAPER
    # --------------------------------------------------------

    if MODE == "PAPER":
        return {
            "ok": True,
            "mode": MODE,
            "status": "simulated",
            "payload": payload,
        }

    # --------------------------------------------------------
    # CONFIRM
    # --------------------------------------------------------

    if MODE == "CONFIRM":
        return {
            "ok": True,
            "mode": MODE,
            "status": "pending_confirmation",
            "payload": payload,
        }

    # --------------------------------------------------------
    # AUTO
    # --------------------------------------------------------

    if MODE != "AUTO":
        raise BingXError(
            f"Неизвестный режим: {MODE}"
        )

    # В AUTO обязательно наличие API.
    if not enabled():
        raise BingXError(
            "AUTO требует "
            "BINGX_API_KEY и "
            "BINGX_SECRET_KEY."
        )

    # Перед открытием проверяем Hedge Mode.
    if not get_position_mode():
        raise BingXError(
            "AUTO остановлен: "
            "BingX не находится в Hedge Mode."
        )

    # Не открываем вторую позицию того же направления.
    existing = get_position(
        setup["symbol"],
        setup["direction"],
    )

    if existing:
        raise BingXError(
            "Позиция этого направления "
            "уже открыта."
        )

    data = place_market_order(
        symbol=setup["symbol"],
        direction=setup["direction"],
        quantity=quantity,
        sl=setup["sl"],
        tp=setup["tp"],
        client_order_id=setup.get(
            "client_order_id"
        ),
    )

    # После отправки проверяем,
    # появилась ли реальная позиция.
    position = wait_for_position(
        setup["symbol"],
        setup["direction"],
        timeout_seconds=5,
    )

    if not position:

        # Если ордер вроде бы отправлен,
        # но позиция не появилась —
        # НЕ считаем сделку успешной.
        raise BingXError(
            "BingX принял запрос, "
            "но открытая позиция "
            "не была подтверждена."
        )

    return {
        "ok": True,
        "mode": MODE,
        "status": "submitted",
        "payload": payload,
        "data": data,
        "position": position,
    }


# ============================================================
# CONFIRM EXECUTION
# ============================================================

def execute_confirmed(setup):
    """
    Используется командой /execute.

    Работает только в CONFIRM.
    """

    if MODE != "CONFIRM":
        raise BingXError(
            "Нужен BINGX_MODE=CONFIRM."
        )

    _validate_setup(setup)

    risk_usdt = float(
        setup.get(
            "risk_usdt",
            RISK_USDT,
        )
    )

    raw_quantity = calculate_quantity(
        setup["entry"],
        setup["sl"],
        risk_usdt,
    )

    quantity = normalize_quantity(
        setup["symbol"],
        raw_quantity,
    )

    actual_risk = calculate_actual_risk(
        setup["entry"],
        setup["sl"],
        float(quantity),
    )

    data = place_market_order(
        symbol=setup["symbol"],
        direction=setup["direction"],
        quantity=quantity,
        sl=setup["sl"],
        tp=setup["tp"],
        client_order_id=setup.get(
            "client_order_id"
        ),
    )

    position = wait_for_position(
        setup["symbol"],
        setup["direction"],
        timeout_seconds=5,
    )

    if not position:
        raise BingXError(
            "Ордер отправлен, "
            "но позиция не подтверждена."
        )

    return {
        "ok": True,
        "mode": MODE,
        "status": "submitted",
        "symbol": normalize_symbol(
            setup["symbol"]
        ),
        "direction": str(
            setup["direction"]
        ).upper(),
        "entry": float(
            setup["entry"]
        ),
        "sl": float(
            setup["sl"]
        ),
        "tp": float(
            setup["tp"]
        ),
        "quantity": quantity,
        "risk_usdt": risk_usdt,
        "actual_risk_usdt": round(
            actual_risk,
            6,
        ),
        "data": data,
        "position": position,
    }


# ============================================================
# CLOSE POSITION
# ============================================================

def close_position(
    symbol=None,
):
    params = {}

    if symbol:
        params["symbol"] = normalize_symbol(
            symbol
        )

    return _signed_request(
        "POST",
        "/openApi/swap/v2/trade/closeAllPositions",
        params,
    )


# ============================================================
# OPEN ORDERS
# ============================================================

def get_open_orders(
    symbol=None,
):
    params = {}

    if symbol:
        params["symbol"] = normalize_symbol(
            symbol
        )

    return _signed_request(
        "GET",
        "/openApi/swap/v2/trade/openOrders",
        params,
    )


# ============================================================
# CANCEL ALL ORDERS
# ============================================================

def cancel_all_orders(
    symbol=None,
):
    params = {}

    if symbol:
        params["symbol"] = normalize_symbol(
            symbol
        )

    return _signed_request(
        "DELETE",
        "/openApi/swap/v2/trade/allOpenOrders",
        params,
    )


# ============================================================
# PING / HEALTH
# ============================================================

def ping():
    return _public_request(
        "/openApi/swap/v2/server/time"
    )


def health():
    result = {
        "mode": MODE,
        "configured": enabled(),
        "env": ENV,
        "api": False,
        "hedge_mode": None,
        "error": None,
    }

    try:
        ping()
        result["api"] = True

    except Exception as exc:
        result["error"] = str(exc)
        return result

    if enabled():

        try:
            result[
                "hedge_mode"
            ] = get_position_mode()

        except Exception as exc:
            result["error"] = str(exc)

    return result


# ============================================================
# SAFE SUMMARY
# ============================================================

def status_text():
    status = config_status()

    return (
        "BingX\n"
        f"Mode: {status['mode']}\n"
        f"Environment: {status['env']}\n"
        f"API: "
        f"{'OK' if status['configured'] else 'OFF'}\n"
        f"Leverage: {status['leverage']}x\n"
        f"Risk: ${status['risk_usdt']}\n"
        f"Working type: "
        f"{status['working_type']}"
    )