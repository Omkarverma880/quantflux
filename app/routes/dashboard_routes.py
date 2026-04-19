"""
Dashboard data API routes.
Aggregated data for the main dashboard view.
"""
from datetime import datetime, time as dtime
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from config import settings
from core.database import get_db
from core.auth import login_required
from core.broker import get_user_broker
from core.risk_manager import get_risk_manager
from strategies.registry import list_strategies
from core.logger import get_logger

router = APIRouter()
logger = get_logger("api.dashboard")

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)


@router.get("/summary")
async def dashboard_summary(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Main dashboard data — everything the frontend needs at a glance."""
    risk = get_risk_manager()
    now = datetime.now()
    current_time = now.time()

    # Market status
    is_weekday = now.weekday() < 5
    is_market_hours = MARKET_OPEN <= current_time <= MARKET_CLOSE
    market_status = "OPEN" if (is_weekday and is_market_hours) else "CLOSED"

    # Try to get live data for THIS user
    account = {"available": 0, "used": 0}
    positions_count = 0
    total_pnl = 0
    orders_count = 0

    try:
        broker = get_user_broker(db, user_id)
        margins = broker.get_margins()
        equity = margins.get("equity", {})
        available = equity.get("available", {})
        utilised = equity.get("utilised", {})
        account = {
            "available": available.get("live_balance", 0),
            "used": utilised.get("debits", 0),
        }

        positions = broker.get_positions()
        active_positions = [p for p in positions if p.quantity != 0]
        positions_count = len(active_positions)
        total_pnl = sum(p.pnl for p in positions)

        orders = broker.get_orders()
        orders_count = len(orders) if orders else 0
    except Exception:
        pass

    return {
        "timestamp": now.isoformat(),
        "market_status": market_status,
        "account": account,
        "positions_count": positions_count,
        "total_pnl": round(total_pnl, 2),
        "orders_today": orders_count,
        "paper_trade": settings.PAPER_TRADE,
        "trading_enabled": settings.TRADING_ENABLED,
        "max_position_size": settings.MAX_POSITION_SIZE,
        "risk": {
            "daily_pnl": risk.daily_pnl,
            "trade_count": risk.daily_trade_count,
            "max_loss_limit": settings.MAX_LOSS_PER_DAY,
            "max_trades_limit": settings.MAX_TRADES_PER_DAY,
            "trading_allowed": risk.is_trading_allowed,
        },
        "strategies": {
            "registered": list_strategies(),
            "active": settings.ACTIVE_STRATEGIES,
            "total_registered": len(list_strategies()),
            "total_active": len(settings.ACTIVE_STRATEGIES),
        },
    }


@router.get("/ltp")
async def get_ltp(instruments: str = "", user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Get last traded prices. Query: ?instruments=NSE:RELIANCE,NSE:INFY"""
    if not instruments:
        return {"prices": {}}

    try:
        broker = get_user_broker(db, user_id)
        inst_list = [i.strip() for i in instruments.split(",") if i.strip()]
        prices = broker.get_ltp(inst_list)
        return {"prices": prices}
    except Exception as e:
        return {"prices": {}, "error": str(e)}
