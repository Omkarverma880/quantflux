"""Manual trading API routes.

Uses the existing Zerodha authentication - no separate JWT login needed.
All manual trade actions are logged to date-based JSON files under
data/trade_history/manual/ to keep them separate from strategy logs.
"""

import asyncio
import json
import math
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta, time as dtime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.auth import login_required
from core.database import get_db
from core.broker import Exchange, OrderRequest, OrderSide, OrderType, ProductType, get_user_broker
from core.logger import get_logger

router = APIRouter()
logger = get_logger("api.manual_trading")

MANUAL_LOG_DIR = Path("data") / "trade_history" / "manual"
MANUAL_LOG_DIR.mkdir(parents=True, exist_ok=True)

INDEX_EXCHANGE_MAP = {
    "NIFTY": "NFO",
    "BANKNIFTY": "NFO",
    "FINNIFTY": "NFO",
    "MIDCPNIFTY": "NFO",
    "SENSEX": "BFO",
    "BANKEX": "BFO",
}
INDEX_SPOT_MAP = {
    "NIFTY": "NSE:NIFTY 50",
    "SENSEX": "BSE:SENSEX",
}


# -- Auth dependency - per-user Zerodha session -------------------------

def require_zerodha_auth(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """FastAPI dependency: ensures this user has an active Zerodha session."""
    from core.auth import UserZerodhaAuth
    if not UserZerodhaAuth.is_authenticated(db, user_id):
        raise HTTPException(
            status_code=401,
            detail="Zerodha not authenticated. Please login via the sidebar first.",
        )
    return {"user_id": user_id, "db": db}


# -- Pydantic models ------------------------------------------------

class ManualOrder(BaseModel):
    index_name: str = ""
    tradingsymbol: str = ""
    exchange: str = "NSE"
    side: str = "BUY"
    quantity: int = 1
    order_type: str = "MARKET"
    product: str = "MIS"
    price: float = 0.0
    trigger_price: float = 0.0
    tag: str = "manual"
    mode: str = "LIVE"
    auto_atm: bool = False
    option_type: str = "CE"
    strike_price: float = 0.0
    entry_price: float = 0.0
    sl_type: str = "POINTS"
    stop_loss: float = 0.0
    target_type: str = "POINTS"
    target: float = 0.0
    trailing_type: str = "POINTS"
    trailing: float = 0.0
    move_sl_to_cost: bool = False
    re_entry: bool = False
    iceberg_legs: int = 1


class SquareoffRequest(BaseModel):
    tradingsymbol: str


class ModifyOrderRequest(BaseModel):
    order_id: str
    price: float


class CancelOrderRequest(BaseModel):
    order_id: str


class OptionSetupResponse(BaseModel):
    index_name: str
    exchange: str
    spot_instrument: str
    spot_price: float
    nearest_expiry: str
    atm_strike: float
    strike_options: list[float]
    lot_size: int = 1


# -- Helpers ---------------------------------------------------------

def _parse_expiry(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
    return None


def _serialize(value):
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, dict):
        return value
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return value


def _today_log_file() -> Path:
    return MANUAL_LOG_DIR / f"{date.today().isoformat()}.json"


def _read_today_logs() -> list[dict]:
    path = _today_log_file()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _read_all_logs() -> list[dict]:
    all_logs = []
    if not MANUAL_LOG_DIR.exists():
        return all_logs
    for path in sorted(MANUAL_LOG_DIR.glob("*.json"), reverse=True):
        try:
            entries = json.loads(path.read_text())
            all_logs.extend(entries)
        except (json.JSONDecodeError, OSError):
            continue
    return all_logs


def _append_manual_log(entry: dict):
    logs = _read_today_logs()
    logs.append(entry)
    try:
        _today_log_file().write_text(json.dumps(logs, indent=2, default=str))
    except OSError as exc:
        logger.error("Failed to write manual trade log: %s", exc)


def _split_quantities(quantity: int, legs: int) -> list[int]:
    legs = max(1, legs)
    base = quantity // legs
    remainder = quantity % legs
    chunks = [base] * legs
    for index in range(remainder):
        chunks[index] += 1
    return [chunk for chunk in chunks if chunk > 0]


def _get_option_candidates(index_name: str, option_type: str, broker):
    exchange = INDEX_EXCHANGE_MAP.get(index_name, "NFO")
    instruments = broker.get_instruments(exchange)
    today = date.today()
    candidates = []

    for instrument in instruments:
        if instrument.get("name") != index_name:
            continue
        if instrument.get("instrument_type") != option_type:
            continue

        expiry = _parse_expiry(instrument.get("expiry"))
        strike = float(instrument.get("strike", 0) or 0)
        if not expiry or expiry < today or strike <= 0:
            continue

        candidates.append((expiry, strike, instrument))

    return candidates, exchange


def _build_option_setup(index_name: str, option_type: str, broker) -> OptionSetupResponse:
    index_name = index_name.strip().upper()
    option_type = option_type.strip().upper()
    if index_name not in INDEX_SPOT_MAP:
        raise HTTPException(status_code=400, detail=f"Unsupported index: {index_name}")
    if option_type not in {"CE", "PE"}:
        raise HTTPException(status_code=400, detail=f"Unsupported option type: {option_type}")

    candidates, exchange = _get_option_candidates(index_name, option_type, broker)
    if not candidates:
        raise HTTPException(status_code=404, detail=f"No active {index_name} {option_type} contracts found")

    candidates.sort(key=lambda item: (item[0], item[1]))
    nearest_expiry = candidates[0][0]
    expiry_candidates = [item for item in candidates if item[0] == nearest_expiry]
    available_strikes = sorted({item[1] for item in expiry_candidates})

    spot_instrument = INDEX_SPOT_MAP[index_name]
    try:
        spot_price = float(broker.get_ltp([spot_instrument]).get(spot_instrument, 0) or 0)
    except Exception as error:
        logger.warning("Falling back to strike midpoint for %s spot: %s", index_name, error)
        spot_price = 0.0

    if spot_price > 0:
        atm_strike = min(available_strikes, key=lambda strike: abs(strike - spot_price))
    else:
        atm_index = len(available_strikes) // 2
        atm_strike = available_strikes[atm_index]

    center_index = available_strikes.index(atm_strike)
    start_index = max(0, center_index - 6)
    end_index = min(len(available_strikes), center_index + 7)
    strike_options = available_strikes[start_index:end_index]

    if len(strike_options) < 13 and available_strikes:
        if start_index == 0:
            strike_options = available_strikes[: min(13, len(available_strikes))]
        elif end_index == len(available_strikes):
            strike_options = available_strikes[max(0, len(available_strikes) - 13):]

    # Extract lot_size from instrument data
    lot_size = int(expiry_candidates[0][2].get("lot_size", 1) or 1)

    return OptionSetupResponse(
        index_name=index_name,
        exchange=exchange,
        spot_instrument=spot_instrument,
        spot_price=spot_price,
        nearest_expiry=nearest_expiry.isoformat(),
        atm_strike=atm_strike,
        strike_options=strike_options,
        lot_size=lot_size,
    )


def _resolve_option_contract(order: ManualOrder, broker) -> tuple[str, date | None, str]:
    manual_symbol = (order.tradingsymbol or "").strip()
    if manual_symbol:
        return manual_symbol, None, order.exchange

    index_name = (order.index_name or "").strip().upper()
    if not index_name or not order.strike_price or not order.option_type:
        raise HTTPException(
            status_code=400,
            detail="Provide a manual trading symbol or select index, strike and option type",
        )

    candidates, exchange = _get_option_candidates(index_name, order.option_type, broker)
    candidates = [item for item in candidates if float(item[1]) == float(order.strike_price)]

    if not candidates:
        raise HTTPException(
            status_code=404,
            detail=f"No active {index_name} {int(order.strike_price)} {order.option_type} contract found for a valid expiry",
        )

    candidates.sort(key=lambda item: item[0])
    selected_expiry, _, selected_instrument = candidates[0]
    resolved_exchange = selected_instrument.get("exchange") or exchange
    return selected_instrument["tradingsymbol"], selected_expiry, resolved_exchange


# -- Routes ----------------------------------------------------------

@router.get("/auth_status")
async def manual_auth_status(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Check Zerodha auth for the current user."""
    from core.auth import UserZerodhaAuth
    is_auth = UserZerodhaAuth.is_authenticated(db, user_id)
    profile = None
    if is_auth:
        try:
            kite = UserZerodhaAuth.get_kite_for_user(db, user_id)
            p = kite.profile()
            profile = {
                "name": p.get("user_name", ""),
                "user_id": p.get("user_id", ""),
            }
        except Exception as exc:
            logger.debug("Profile fetch failed (auth still valid): %s", exc)
    return {"authenticated": is_auth, "profile": profile}


@router.get("/option_setup", response_model=OptionSetupResponse)
async def get_option_setup(
    index_name: str,
    option_type: str = "CE",
    _auth=Depends(require_zerodha_auth),
):
    broker = get_user_broker(_auth["db"], _auth["user_id"])
    return _build_option_setup(index_name, option_type, broker)


@router.post("/order")
async def place_manual_order(order: ManualOrder, _auth=Depends(require_zerodha_auth)):
    broker = get_user_broker(_auth["db"], _auth["user_id"])
    tradingsymbol, resolved_expiry, resolved_exchange = _resolve_option_contract(order, broker)

    order_ids = []
    quantities = _split_quantities(order.quantity, order.iceberg_legs)
    if not quantities:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero")

    for leg_index, leg_quantity in enumerate(quantities, start=1):
        request = OrderRequest(
            tradingsymbol=tradingsymbol,
            exchange=Exchange(resolved_exchange),
            side=OrderSide(order.side),
            quantity=leg_quantity,
            order_type=OrderType(order.order_type),
            product=ProductType(order.product),
            price=order.price,
            trigger_price=order.trigger_price,
            tag=order.tag if len(quantities) == 1 else f"{order.tag}-leg{leg_index}",
        )
        try:
            response = broker.place_order(request)
            oid = response.order_id if hasattr(response, "order_id") else response
            order_ids.append(oid)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except Exception as error:
            logger.exception("Manual order error on leg %s", leg_index)
            raise HTTPException(status_code=500, detail=str(error)) from error

    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "date": date.today().isoformat(),
        "tradingsymbol": tradingsymbol,
        "resolved_expiry": resolved_expiry.isoformat() if resolved_expiry else None,
        "exchange": resolved_exchange,
        "side": order.side,
        "quantity": order.quantity,
        "order_type": order.order_type,
        "product": order.product,
        "price": order.price,
        "trigger_price": order.trigger_price,
        "stop_loss": order.stop_loss,
        "target": order.target,
        "order_ids": order_ids,
        "tag": order.tag,
        "mode": order.mode,
        "index_name": order.index_name,
        "option_type": order.option_type,
        "strike_price": order.strike_price,
        "iceberg_legs": order.iceberg_legs,
        "status": "PLACED",
    }
    _append_manual_log(log_entry)
    logger.info("Manual order placed: %s -> %s", tradingsymbol, order_ids)

    # Register with SL/target monitor if SL or target is set
    if order.stop_loss > 0 or order.target > 0:
        # Determine entry price: use user-supplied price, or fetch LTP
        entry_price = order.entry_price or order.price
        if not entry_price or entry_price <= 0:
            try:
                instrument_key = f"{resolved_exchange}:{tradingsymbol}"
                ltp_data = broker.kite.ltp([instrument_key])
                entry_price = ltp_data[instrument_key]["last_price"]
            except Exception:
                entry_price = 0
        if entry_price > 0:
            _monitor.register(
                tradingsymbol=tradingsymbol,
                exchange=resolved_exchange,
                side=order.side,
                quantity=order.quantity,
                entry_price=entry_price,
                product=order.product,
                sl_type=order.sl_type,
                stop_loss=order.stop_loss,
                target_type=order.target_type,
                target=order.target,
                trailing_type=order.trailing_type,
                trailing=order.trailing,
                move_sl_to_cost=order.move_sl_to_cost,
                re_entry=order.re_entry,
                user_id=_auth["user_id"],
            )

    return {
        "status": "success",
        "order_ids": order_ids,
        "resolved_tradingsymbol": tradingsymbol,
        "resolved_expiry": resolved_expiry.isoformat() if resolved_expiry else None,
        "resolved_exchange": resolved_exchange,
    }


@router.get("/positions")
async def get_manual_positions(_auth=Depends(require_zerodha_auth)):
    broker = get_user_broker(_auth["db"], _auth["user_id"])
    try:
        positions = broker.get_positions()
        active_positions = [p for p in positions if getattr(p, "quantity", 0) != 0]

        # Fetch fresh LTP for all active positions to compute real-time P&L
        if active_positions:
            instruments = [
                f"{p.exchange}:{p.tradingsymbol}" for p in active_positions
            ]
            try:
                ltp_data = broker.kite.ltp(instruments)
                for p in active_positions:
                    key = f"{p.exchange}:{p.tradingsymbol}"
                    if key in ltp_data:
                        p.last_price = ltp_data[key]["last_price"]
                        # Recalculate unrealised P&L from live LTP
                        p.pnl = round((p.last_price - p.average_price) * p.quantity, 2)
            except Exception as ltp_err:
                logger.warning("LTP fetch for positions failed: %s", ltp_err)

        return {"positions": [_serialize(p) for p in active_positions]}
    except Exception as error:
        logger.exception("Get manual positions error")
        raise HTTPException(status_code=500, detail=str(error)) from error


@router.post("/squareoff")
async def manual_squareoff(payload: SquareoffRequest, _auth=Depends(require_zerodha_auth)):
    broker = get_user_broker(_auth["db"], _auth["user_id"])
    try:
        positions = broker.get_positions()
        position = next((item for item in positions if item.tradingsymbol == payload.tradingsymbol), None)
        if not position or position.quantity == 0:
            raise HTTPException(status_code=404, detail="No open position found")

        req = OrderRequest(
            tradingsymbol=position.tradingsymbol,
            exchange=Exchange(position.exchange),
            side=OrderSide.SELL if position.quantity > 0 else OrderSide.BUY,
            quantity=abs(position.quantity),
            order_type=OrderType.MARKET,
            product=ProductType(position.product),
            tag="manual-squareoff",
        )
        response = broker.place_order(req)
        order_id = response.order_id if hasattr(response, "order_id") else response

        _append_manual_log({
            "timestamp": datetime.now().isoformat(),
            "date": date.today().isoformat(),
            "tradingsymbol": position.tradingsymbol,
            "exchange": position.exchange,
            "side": "SELL" if position.quantity > 0 else "BUY",
            "quantity": abs(position.quantity),
            "order_type": "MARKET",
            "order_ids": [order_id],
            "tag": "manual-squareoff",
            "status": "SQUAREOFF",
        })

        logger.info("Manual square-off: %s -> %s", payload.tradingsymbol, order_id)
        _monitor.unregister(payload.tradingsymbol)
        return {"status": "success", "order_id": order_id}
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Manual square-off error")
        raise HTTPException(status_code=500, detail=str(error)) from error


@router.get("/open_orders")
async def get_manual_open_orders(_auth=Depends(require_zerodha_auth)):
    broker = get_user_broker(_auth["db"], _auth["user_id"])
    try:
        orders = broker.get_orders()
        open_orders = [order for order in orders if order.get("status") in {"OPEN", "TRIGGER PENDING"}]
        return {"open_orders": [_serialize(order) for order in open_orders]}
    except Exception as error:
        logger.exception("Get open orders error")
        raise HTTPException(status_code=500, detail=str(error)) from error


@router.get("/orders")
async def get_all_orders(_auth=Depends(require_zerodha_auth)):
    broker = get_user_broker(_auth["db"], _auth["user_id"])
    try:
        orders = broker.get_orders()
        return {"orders": [_serialize(order) for order in orders]}
    except Exception as error:
        logger.exception("Get orders error")
        raise HTTPException(status_code=500, detail=str(error)) from error


@router.post("/order/modify")
async def modify_order(payload: ModifyOrderRequest, _auth=Depends(require_zerodha_auth)):
    broker = get_user_broker(_auth["db"], _auth["user_id"])
    try:
        result = broker.modify_order(payload.order_id, price=payload.price)
        _append_manual_log({
            "timestamp": datetime.now().isoformat(),
            "date": date.today().isoformat(),
            "order_id": payload.order_id,
            "new_price": payload.price,
            "status": "MODIFIED",
        })
        logger.info("Order %s modified to %s", payload.order_id, payload.price)
        return {"status": "success", "result": result}
    except Exception as error:
        logger.exception("Modify order error")
        raise HTTPException(status_code=500, detail=str(error)) from error


@router.post("/order/cancel")
async def cancel_order(payload: CancelOrderRequest, _auth=Depends(require_zerodha_auth)):
    broker = get_user_broker(_auth["db"], _auth["user_id"])
    try:
        result = broker.cancel_order(payload.order_id)
        _append_manual_log({
            "timestamp": datetime.now().isoformat(),
            "date": date.today().isoformat(),
            "order_id": payload.order_id,
            "status": "CANCELLED",
        })
        logger.info("Order %s cancelled", payload.order_id)
        return {"status": "success", "result": result}
    except Exception as error:
        logger.exception("Cancel order error")
        raise HTTPException(status_code=500, detail=str(error)) from error


@router.get("/trade_logs")
async def get_trade_logs(log_date: str = ""):
    if log_date == "all":
        return {"logs": _read_all_logs()}
    return {"logs": _read_today_logs()}


@router.get("/pnl")
async def get_manual_pnl(_auth=Depends(require_zerodha_auth)):
    broker = get_user_broker(_auth["db"], _auth["user_id"])
    try:
        positions = broker.get_positions()
        manual_pnl = 0.0
        trade_count = 0
        for p in positions:
            manual_pnl += getattr(p, "pnl", 0) or 0
            if getattr(p, "quantity", 0) != 0:
                trade_count += 1
        logs = _read_today_logs()
        return {
            "pnl": manual_pnl,
            "trade_count": trade_count,
            "log_count": len(logs),
        }
    except Exception as error:
        logger.exception("Get manual PnL error")
        raise HTTPException(status_code=500, detail=str(error)) from error


@router.get("/margins")
async def get_manual_margins(_auth=Depends(require_zerodha_auth)):
    """Return available margin from Zerodha — same data as Dashboard."""
    broker = get_user_broker(_auth["db"], _auth["user_id"])
    try:
        margins = broker.get_margins()
        equity = margins.get("equity", {})
        available = equity.get("available", {})
        utilised = equity.get("utilised", {})
        return {
            "available": available.get("live_balance", 0),
            "used": utilised.get("debits", 0),
        }
    except Exception as error:
        logger.exception("Get margins error")
        raise HTTPException(status_code=500, detail=str(error)) from error


# ── SL / Target Background Monitor ─────────────────────────────────

TICK = 0.05


def _round_tick(price: float) -> float:
    """Round price to nearest tick size (0.05)."""
    return round(round(price / TICK) * TICK, 2)


class _ManualTradeMonitor:
    """Watches LTP and exits positions when SL/target is breached."""

    def __init__(self):
        # key = tradingsymbol, value = dict with trade config
        self._trades: dict[str, dict] = {}
        self._task: asyncio.Task | None = None
        self._running = False

    # ── public API ──────────────────────────────

    def register(self, tradingsymbol: str, exchange: str, side: str,
                 quantity: int, entry_price: float, product: str,
                 sl_type: str, stop_loss: float,
                 target_type: str, target: float,
                 trailing_type: str, trailing: float,
                 move_sl_to_cost: bool, re_entry: bool,
                 user_id: int = None):
        """Register an active trade for SL/target monitoring."""
        if stop_loss <= 0 and target <= 0:
            return  # nothing to monitor

        # Compute absolute SL and target prices
        if side == "BUY":
            sl_price = self._calc_exit(entry_price, sl_type, stop_loss, "below")
            tgt_price = self._calc_exit(entry_price, target_type, target, "above") if target > 0 else 0
        else:
            sl_price = self._calc_exit(entry_price, sl_type, stop_loss, "above")
            tgt_price = self._calc_exit(entry_price, target_type, target, "below") if target > 0 else 0

        trailing_points = 0
        if trailing > 0:
            if trailing_type == "PERCENT":
                trailing_points = entry_price * trailing / 100.0
            else:
                trailing_points = trailing

        self._trades[tradingsymbol] = {
            "user_id": user_id,
            "exchange": exchange,
            "side": side,
            "quantity": quantity,
            "entry_price": entry_price,
            "product": product,
            "sl_price": _round_tick(sl_price) if sl_price > 0 else 0,
            "tgt_price": _round_tick(tgt_price) if tgt_price > 0 else 0,
            "initial_sl": _round_tick(sl_price) if sl_price > 0 else 0,
            "trailing_points": trailing_points,
            "move_sl_to_cost": move_sl_to_cost,
            "sl_moved_to_cost": False,
            "best_price": entry_price,
            "registered_at": datetime.now().isoformat(),
            "status": "WATCHING",
        }
        logger.info(
            "SL/Target monitor registered: %s | side=%s entry=%.2f SL=%.2f TGT=%.2f trailing=%.2f",
            tradingsymbol, side, entry_price,
            self._trades[tradingsymbol]["sl_price"],
            self._trades[tradingsymbol]["tgt_price"],
            trailing_points,
        )
        self._ensure_running()

    def unregister(self, tradingsymbol: str):
        self._trades.pop(tradingsymbol, None)

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "active_trades": {k: {**v} for k, v in self._trades.items()},
        }

    # ── internal ────────────────────────────────

    @staticmethod
    def _calc_exit(entry: float, calc_type: str, value: float, direction: str) -> float:
        if value <= 0:
            return 0
        if calc_type == "PERCENT":
            offset = entry * value / 100.0
        else:  # POINTS
            offset = value
        if direction == "below":
            return max(entry - offset, TICK)
        return entry + offset

    def _ensure_running(self):
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._monitor_loop())

    async def _monitor_loop(self):
        logger.info("Manual SL/target monitor loop started")
        try:
            while self._trades:
                now = datetime.now().time()
                if now < dtime(9, 15) or now > dtime(15, 30):
                    await asyncio.sleep(5)
                    continue

                try:
                    await self._check_once()
                except Exception as exc:
                    logger.error("Monitor check error: %s", exc)

                await asyncio.sleep(2)
        finally:
            self._running = False
            logger.info("Manual SL/target monitor loop stopped (no active trades)")

    async def _check_once(self):
        if not self._trades:
            return
        from core.database import get_db_session
        from collections import defaultdict

        # Group trades by user_id so we fetch LTP per-user
        user_trades = defaultdict(dict)
        for sym, t in self._trades.items():
            uid = t.get("user_id")
            user_trades[uid][sym] = t

        for uid, trades in user_trades.items():
            if uid is None:
                continue
            db = get_db_session()
            try:
                broker = get_user_broker(db, uid)
                instruments = [f"{t['exchange']}:{sym}" for sym, t in trades.items()]
                try:
                    ltp_data = broker.kite.ltp(instruments)
                except Exception as exc:
                    logger.warning("Monitor LTP fetch failed for user %s: %s", uid, exc)
                    continue
                self._process_ltp(trades, ltp_data, broker)
            finally:
                db.close()

    def _process_ltp(self, trades: dict, ltp_data: dict, broker):
        to_remove = []
        for sym, trade in list(trades.items()):
            key = f"{trade['exchange']}:{sym}"
            if key not in ltp_data:
                continue
            ltp = ltp_data[key]["last_price"]
            is_buy = trade["side"] == "BUY"

            # Update best price for trailing SL
            if is_buy:
                if ltp > trade["best_price"]:
                    trade["best_price"] = ltp
                    if trade["trailing_points"] > 0 and trade["sl_price"] > 0:
                        new_sl = _round_tick(ltp - trade["trailing_points"])
                        if new_sl > trade["sl_price"]:
                            logger.info("Trailing SL updated %s: %.2f → %.2f (LTP=%.2f)", sym, trade["sl_price"], new_sl, ltp)
                            trade["sl_price"] = new_sl
                # Move SL to cost
                if trade["move_sl_to_cost"] and not trade["sl_moved_to_cost"]:
                    if trade["tgt_price"] > 0 and ltp >= trade["entry_price"] + (trade["tgt_price"] - trade["entry_price"]) * 0.5:
                        trade["sl_price"] = _round_tick(trade["entry_price"])
                        trade["sl_moved_to_cost"] = True
                        logger.info("SL moved to cost for %s: SL=%.2f", sym, trade["sl_price"])
            else:  # SELL side
                if ltp < trade["best_price"]:
                    trade["best_price"] = ltp
                    if trade["trailing_points"] > 0 and trade["sl_price"] > 0:
                        new_sl = _round_tick(ltp + trade["trailing_points"])
                        if new_sl < trade["sl_price"]:
                            logger.info("Trailing SL updated %s: %.2f → %.2f (LTP=%.2f)", sym, trade["sl_price"], new_sl, ltp)
                            trade["sl_price"] = new_sl
                if trade["move_sl_to_cost"] and not trade["sl_moved_to_cost"]:
                    if trade["tgt_price"] > 0 and ltp <= trade["entry_price"] - (trade["entry_price"] - trade["tgt_price"]) * 0.5:
                        trade["sl_price"] = _round_tick(trade["entry_price"])
                        trade["sl_moved_to_cost"] = True
                        logger.info("SL moved to cost for %s: SL=%.2f", sym, trade["sl_price"])

            # Check SL hit
            sl_hit = False
            if trade["sl_price"] > 0:
                if is_buy and ltp <= trade["sl_price"]:
                    sl_hit = True
                elif not is_buy and ltp >= trade["sl_price"]:
                    sl_hit = True

            # Check target hit
            tgt_hit = False
            if trade["tgt_price"] > 0:
                if is_buy and ltp >= trade["tgt_price"]:
                    tgt_hit = True
                elif not is_buy and ltp <= trade["tgt_price"]:
                    tgt_hit = True

            if sl_hit or tgt_hit:
                reason = "SL" if sl_hit else "TARGET"
                logger.info("Manual %s hit for %s at LTP=%.2f | SL=%.2f TGT=%.2f",
                            reason, sym, ltp, trade["sl_price"], trade["tgt_price"])
                try:
                    exit_side = OrderSide.SELL if is_buy else OrderSide.BUY
                    req = OrderRequest(
                        tradingsymbol=sym,
                        exchange=Exchange(trade["exchange"]),
                        side=exit_side,
                        quantity=trade["quantity"],
                        order_type=OrderType.MARKET,
                        product=ProductType(trade["product"]),
                        tag=f"manual-{reason.lower()}",
                    )
                    resp = broker.place_order(req)
                    oid = resp.order_id if hasattr(resp, "order_id") else resp
                    _append_manual_log({
                        "timestamp": datetime.now().isoformat(),
                        "date": date.today().isoformat(),
                        "tradingsymbol": sym,
                        "exchange": trade["exchange"],
                        "side": exit_side.value,
                        "quantity": trade["quantity"],
                        "order_type": "MARKET",
                        "order_ids": [oid],
                        "tag": f"manual-{reason.lower()}",
                        "status": reason,
                        "exit_ltp": ltp,
                        "entry_price": trade["entry_price"],
                        "pnl": round((ltp - trade["entry_price"]) * trade["quantity"] * (1 if is_buy else -1), 2),
                    })
                    trade["status"] = reason
                    to_remove.append(sym)
                    logger.info("Manual %s exit placed: %s → %s", reason, sym, oid)
                except Exception as exc:
                    logger.error("Failed to place %s exit for %s: %s", reason, sym, exc)

        for sym in to_remove:
            self._trades.pop(sym, None)


# Singleton monitor
_monitor = _ManualTradeMonitor()


@router.get("/monitor/status")
async def get_monitor_status():
    return _monitor.get_status()


@router.post("/monitor/unregister")
async def unregister_monitor(payload: SquareoffRequest):
    _monitor.unregister(payload.tradingsymbol)
    return {"status": "ok"}
