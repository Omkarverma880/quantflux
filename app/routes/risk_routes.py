"""
Risk Fence + Auto-Squareoff API routes.

Endpoints (all require app login):
  GET  /api/risk/config              → current fence config + live snapshot
  PUT  /api/risk/pnl_fence           → update advanced P&L fence
  PUT  /api/risk/loss_control        → update day-loss control
  POST /api/risk/reset               → clear triggered flags
  POST /api/risk/squareoff_now       → manual auto-squareoff trigger (MIS opts)
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
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
    # New signed schema. None = rule disabled.
    profit_target: Optional[float] = None  # exit when pnl >= this
    exit_floor:    Optional[float] = None  # exit when pnl <= this  (any sign)
    trail_amount:  Optional[float] = None  # exit when pnl <= peak - this  (>0)
    # Legacy positive-only fields (kept for older clients).
    lock_profit:   Optional[float] = None
    max_loss:      Optional[float] = None


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


def _coerce_optional_float(v) -> Optional[float]:
    """Treat None / '' / non-numeric as None; else cast to float."""
    if v is None:
        return None
    if isinstance(v, str) and not v.strip():
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@router.put("/pnl_fence")
async def update_pnl_fence(payload: PnlFencePayload,
                           user_id: int = Depends(login_required)):
    # ── Resolve the three rule fields with back-compat fallback ──────
    pt = _coerce_optional_float(payload.profit_target)
    ef = _coerce_optional_float(payload.exit_floor)
    ta = _coerce_optional_float(payload.trail_amount)

    if pt is None:
        lp = _coerce_optional_float(payload.lock_profit)
        if lp is not None and lp > 0:
            pt = lp
    if ef is None:
        ml = _coerce_optional_float(payload.max_loss)
        if ml is not None and ml > 0:
            ef = -abs(ml)

    # Normalize: a non-positive trail amount means "off".
    if ta is not None and ta <= 0:
        ta = None

    # ── Sanity guards (server is source of truth) ────────────────────
    if pt is not None and ef is not None and ef >= pt:
        raise HTTPException(
            status_code=400,
            detail=f"Exit floor (₹{ef:.0f}) must be strictly less than "
                   f"profit target (₹{pt:.0f}).",
        )
    if ta is not None and ta > 1_000_000:
        raise HTTPException(status_code=400, detail="Trail amount unrealistic (>10L).")

    new_section = {
        "enabled":       bool(payload.enabled),
        "profit_target": pt,
        "exit_floor":    ef,
        "trail_amount":  ta,
        # Clear legacy keys so they don't shadow the new signed fields.
        "lock_profit":   0.0,
        "max_loss":      0.0,
        # Wipe peak so trailing starts fresh from the new config.
        "peak_pnl":      None,
        "peak_at":       None,
    }
    cfg = risk_fence.save_config(user_id, {"pnl_fence": new_section})
    logger.info("user=%s pnl_fence updated: target=%s floor=%s trail=%s enabled=%s",
                user_id, pt, ef, ta, payload.enabled)
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
