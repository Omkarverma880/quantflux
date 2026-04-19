"""
Trading control API routes.
Start/stop engine, place manual orders, view positions.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
import threading

from core.broker import get_user_broker, OrderRequest, OrderSide, OrderType, ProductType, Exchange
from core.database import get_db
from core.auth import login_required
from core.risk_manager import get_risk_manager
from engine.trading_engine import TradingEngine
from core.logger import get_logger

router = APIRouter()
logger = get_logger("api.trading")

# Engine singleton
_engine: Optional[TradingEngine] = None
_engine_thread: Optional[threading.Thread] = None


class ManualOrderRequest(BaseModel):
    tradingsymbol: str
    exchange: str = "NSE"
    side: str = "BUY"
    quantity: int = 1
    order_type: str = "MARKET"
    product: str = "MIS"
    price: float = 0.0


@router.post("/engine/start")
async def start_engine():
    """Start the trading engine in a background thread."""
    global _engine, _engine_thread
    if _engine and _engine._running:
        return {"status": "already_running"}

    from config import settings
    from strategies.registry import get_strategy_class
    import json

    _engine = TradingEngine()

    strategy_configs = []
    for name in settings.ACTIVE_STRATEGIES:
        config_file = settings.DATA_DIR / "strategy_configs" / f"{name}.json"
        if config_file.exists():
            with open(config_file) as f:
                cfg = json.load(f)
            cfg["name"] = name
            strategy_configs.append(cfg)
        else:
            strategy_configs.append({"name": name, "instruments": [], "params": {}})

    if strategy_configs:
        _engine.load_strategies(strategy_configs)

    _engine_thread = threading.Thread(target=_engine.start, daemon=True)
    _engine_thread.start()

    logger.info("Engine started via API.")
    return {"status": "started", "strategies_loaded": len(strategy_configs)}


@router.post("/engine/stop")
async def stop_engine():
    """Stop the trading engine."""
    global _engine
    if not _engine or not _engine._running:
        return {"status": "not_running"}
    _engine.stop()
    return {"status": "stopped"}


@router.get("/engine/status")
async def engine_status():
    """Get engine status."""
    global _engine
    if not _engine:
        return {"running": False, "strategies": [], "risk": {}}
    return _engine.status()


@router.post("/order")
async def place_manual_order(order: ManualOrderRequest, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Place a manual order."""
    broker = get_user_broker(db, user_id)
    risk = get_risk_manager()

    approved, reason = risk.pre_order_check(
        order.tradingsymbol, order.quantity, order.price, order.side
    )
    if not approved:
        return {"status": "rejected", "reason": reason}

    req = OrderRequest(
        tradingsymbol=order.tradingsymbol,
        exchange=Exchange(order.exchange),
        side=OrderSide(order.side),
        quantity=order.quantity,
        order_type=OrderType(order.order_type),
        product=ProductType(order.product),
        price=order.price,
    )
    resp = broker.place_order(req)
    risk.record_trade(order.quantity, order.price, order.side)

    return {
        "status": "placed",
        "order_id": resp.order_id,
        "is_paper": resp.is_paper,
    }


@router.get("/positions")
async def get_positions(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Get current positions."""
    try:
        broker = get_user_broker(db, user_id)
        positions = broker.get_positions()
        return {
            "positions": [
                {
                    "tradingsymbol": p.tradingsymbol,
                    "exchange": p.exchange,
                    "quantity": p.quantity,
                    "average_price": p.average_price,
                    "pnl": p.pnl,
                    "product": p.product,
                }
                for p in positions
            ]
        }
    except Exception as e:
        return {"positions": [], "error": str(e)}


@router.get("/holdings")
async def get_holdings(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Get holdings."""
    try:
        broker = get_user_broker(db, user_id)
        holdings = broker.get_holdings()
        return {
            "holdings": [
                {
                    "tradingsymbol": h.tradingsymbol,
                    "exchange": h.exchange,
                    "quantity": h.quantity,
                    "average_price": h.average_price,
                    "last_price": h.last_price,
                    "pnl": h.pnl,
                }
                for h in holdings
            ]
        }
    except Exception as e:
        return {"holdings": [], "error": str(e)}


@router.get("/orders")
async def get_orders(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Get today's orders."""
    try:
        broker = get_user_broker(db, user_id)
        orders = broker.get_orders()
        return {"orders": orders}
    except Exception as e:
        return {"orders": [], "error": str(e)}


@router.get("/margins")
async def get_margins(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Get account margins."""
    try:
        broker = get_user_broker(db, user_id)
        margins = broker.get_margins()
        equity = margins.get("equity", {})
        available = equity.get("available", {})
        utilised = equity.get("utilised", {})
        return {
            "equity": {
                "available": available.get("live_balance", 0),
                "used": utilised.get("debits", 0),
                "net": equity.get("net", 0),
            }
        }
    except Exception as e:
        return {"equity": {"available": 0, "used": 0, "net": 0}, "error": str(e)}


# ── Order Cancel / Modify ──────────────────────────


class ModifyOrderRequest(BaseModel):
    order_id: str
    price: Optional[float] = None
    quantity: Optional[int] = None
    order_type: Optional[str] = None
    trigger_price: Optional[float] = None


@router.post("/order/cancel/{order_id}")
async def cancel_order(order_id: str, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Cancel a pending order."""
    try:
        broker = get_user_broker(db, user_id)
        result = broker.cancel_order(order_id)
        logger.info(f"Order cancelled: {order_id}")
        return {"status": "cancelled", **result}
    except Exception as e:
        logger.error(f"Cancel order failed: {e}")
        return {"status": "error", "error": str(e)}


@router.put("/order/modify")
async def modify_order(body: ModifyOrderRequest, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Modify a pending order."""
    try:
        broker = get_user_broker(db, user_id)
        changes = {}
        if body.price is not None:
            changes["price"] = body.price
        if body.quantity is not None:
            changes["quantity"] = body.quantity
        if body.order_type is not None:
            changes["order_type"] = body.order_type
        if body.trigger_price is not None:
            changes["trigger_price"] = body.trigger_price
        result = broker.modify_order(body.order_id, **changes)
        logger.info(f"Order modified: {body.order_id}")
        return {"status": "modified", **result}
    except Exception as e:
        logger.error(f"Modify order failed: {e}")
        return {"status": "error", "error": str(e)}
