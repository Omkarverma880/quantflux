"""
Risk Fence + Auto-Squareoff API routes.

Endpoints (all require app login):
  GET  /api/risk/config              → current fence config + live snapshot
  PUT  /api/risk/pnl_fence           → update advanced P&L fence
  PUT  /api/risk/loss_control        → update day-loss control
  POST /api/risk/reset               → clear triggered flags
  POST /api/risk/squareoff_now       → manual auto-squareoff trigger (MIS opts)
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.auth import login_required
from core.database import get_db
from core import risk_fence, auto_squareoff
from core.logger import get_logger

router = APIRouter()
logger = get_logger("api.risk")


class PnlFencePayload(BaseModel):
    enabled: bool = False
    lock_profit: float = 0.0
    max_loss: float = 0.0


class LossControlPayload(BaseModel):
    enabled: bool = False
    max_day_loss: float = 0.0


class ResetPayload(BaseModel):
    section: str | None = None  # "pnl_fence" | "loss_control" | None=both


@router.get("/config")
async def get_config(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    cfg = risk_fence.load_config(user_id)
    # also include a live PnL snapshot so the UI can show current state
    live = risk_fence._aggregate_pnl_for_user(user_id)
    return {"config": cfg, "live_pnl": live,
            "auto_squareoff_at": auto_squareoff.CUTOFF.strftime("%H:%M")}


@router.put("/pnl_fence")
async def update_pnl_fence(payload: PnlFencePayload,
                           user_id: int = Depends(login_required)):
    new_section = {
        "enabled": bool(payload.enabled),
        "lock_profit": max(0.0, float(payload.lock_profit or 0)),
        "max_loss":    max(0.0, float(payload.max_loss or 0)),
    }
    cfg = risk_fence.save_config(user_id, {"pnl_fence": new_section})
    logger.info("user=%s pnl_fence updated: %s", user_id, new_section)
    return {"status": "ok", "config": cfg}


@router.put("/loss_control")
async def update_loss_control(payload: LossControlPayload,
                              user_id: int = Depends(login_required)):
    new_section = {
        "enabled": bool(payload.enabled),
        "max_day_loss": max(0.0, float(payload.max_day_loss or 0)),
    }
    cfg = risk_fence.save_config(user_id, {"loss_control": new_section})
    logger.info("user=%s loss_control updated: %s", user_id, new_section)
    return {"status": "ok", "config": cfg}


@router.post("/reset")
async def reset(payload: ResetPayload,
                user_id: int = Depends(login_required)):
    cfg = risk_fence.reset_triggers(user_id, payload.section)
    return {"status": "ok", "config": cfg}


@router.post("/squareoff_now")
async def squareoff_now(user_id: int = Depends(login_required)):
    """Manually run the 15:15 auto-squareoff for this user immediately."""
    summary = auto_squareoff.run_now_for_user(user_id)
    return {"status": "ok", "summary": summary}
