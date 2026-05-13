"""
Dashboard data API routes.
Aggregated data for the main dashboard view.
"""
import asyncio
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

    # Try to get live data for THIS user. Track per-section warnings so the
    # frontend can show a banner instead of silently displaying stale zeros.
    account = {"available": 0, "used": 0}
    positions_count = 0
    total_pnl = 0
    orders_count = 0
    warnings: list[str] = []
    broker = None

    try:
        broker = get_user_broker(db, user_id)
    except Exception as exc:
        logger.warning("dashboard: get_user_broker failed: %s", exc)
        warnings.append(f"broker: {exc}")

    if broker is not None:
        try:
            margins = broker.get_margins()
            equity = margins.get("equity", {})
            available = equity.get("available", {})
            utilised = equity.get("utilised", {})
            account = {
                "available": available.get("live_balance", 0),
                "used": utilised.get("debits", 0),
            }
        except Exception as exc:
            logger.warning("dashboard: margins fetch failed: %s", exc)
            warnings.append(f"margins: {exc}")

        # Retry positions up to 2x with cache evict + small backoff on
        # transient failure (matches the resilient pattern used by
        # /api/manual/positions so the two views stay in sync).
        positions = None
        last_pos_err: Exception | None = None
        for attempt in range(2):
            try:
                positions = broker.get_positions()
                break
            except Exception as exc:
                last_pos_err = exc
                logger.warning(
                    "dashboard: positions fetch attempt %d failed: %s",
                    attempt + 1, exc,
                )
                # Force-refresh broker handle (handles stale token cache)
                try:
                    from core.broker import _user_brokers
                    _user_brokers.pop(user_id, None)
                    broker = get_user_broker(db, user_id)
                except Exception as exc2:
                    logger.warning(
                        "dashboard: broker re-init failed: %s", exc2,
                    )
                await asyncio.sleep(0.3)
        if positions is None:
            positions = []
            if last_pos_err is not None:
                warnings.append(f"positions: {last_pos_err}")
        active_positions = [p for p in positions if (p.quantity or 0) != 0]
        positions_count = len(active_positions)
        # Day P&L must include CLOSED legs too (qty==0 but with realised PnL
        # from same-day round-trips). Filtering those out would under-report
        # the day's realised gains and disagree with Trade History.
        total_pnl = sum((p.pnl or 0) for p in positions)

        # Also pick up same-day CNC buys that Kite may report under
        # holdings (t1_quantity > 0) rather than day-positions. This keeps
        # Dashboard Day P&L aligned with the Zerodha web "Total P&L" figure.
        try:
            raw_holdings = broker.kite.holdings() or []
            seen = {(p.tradingsymbol, p.exchange) for p in positions}
            for h in raw_holdings:
                try:
                    t1 = int(h.get("t1_quantity", 0) or 0)
                except Exception:
                    t1 = 0
                if t1 <= 0:
                    continue
                key = (h.get("tradingsymbol"), h.get("exchange"))
                if key in seen:
                    continue  # already counted via day-positions
                last_px = float(h.get("last_price") or 0)
                avg_px = float(h.get("average_price") or 0)
                total_pnl += (last_px - avg_px) * t1
                positions_count += 1
        except Exception as exc:
            logger.warning("dashboard: holdings T+0 sweep failed: %s", exc)
            warnings.append(f"holdings_t1: {exc}")

        try:
            orders = broker.get_orders()
            orders_count = len(orders) if orders else 0
        except Exception as exc:
            logger.warning("dashboard: orders fetch failed: %s", exc)
            warnings.append(f"orders: {exc}")

    return {
        "timestamp": now.isoformat(),
        "market_status": market_status,
        "account": account,
        "positions_count": positions_count,
        "total_pnl": round(total_pnl, 2),
        "orders_today": orders_count,
        "warnings": warnings,
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
