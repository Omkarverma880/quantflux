"""
Settings API — per-user settings stored in PostgreSQL user_settings table.
No more .env file read/write for user-specific settings.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.database import get_db
from core.auth import login_required
from core.encryption import encrypt_value, decrypt_value
from core.logger import get_logger

router = APIRouter()
logger = get_logger("settings")


# ── Pydantic models ──────────────────────────────────

class UserSettingsResponse(BaseModel):
    kite_api_key: str = ""
    kite_api_secret: str = ""
    kite_redirect_url: str = ""
    trading_enabled: bool = False
    paper_trade: bool = True
    max_loss_per_day: float = 5000
    max_trades_per_day: int = 20
    max_position_size: float = 100000
    max_single_order_value: float = 50000
    active_strategies: str = ""


class UserSettingsUpdate(BaseModel):
    kite_api_key: Optional[str] = None
    kite_api_secret: Optional[str] = None
    trading_enabled: Optional[bool] = None
    paper_trade: Optional[bool] = None
    max_loss_per_day: Optional[float] = None
    max_trades_per_day: Optional[int] = None
    max_position_size: Optional[float] = None
    max_single_order_value: Optional[float] = None
    active_strategies: Optional[str] = None


# ── Routes ────────────────────────────────────────────

@router.get("/", response_model=UserSettingsResponse)
def get_settings(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Return current user's settings from DB."""
    from core.models import UserSettings

    row = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    if not row:
        raise HTTPException(404, "Settings not found for this user")

    # Decrypt the secret before returning (masked for display)
    raw_secret = decrypt_value(row.kite_api_secret) if row.kite_api_secret else ""
    masked_secret = raw_secret[:4] + "****" + raw_secret[-4:] if len(raw_secret) > 8 else "****" if raw_secret else ""

    return UserSettingsResponse(
        kite_api_key=row.kite_api_key or "",
        kite_api_secret=masked_secret,
        kite_redirect_url=row.kite_redirect_url or "",
        trading_enabled=row.trading_enabled or False,
        paper_trade=row.paper_trade if row.paper_trade is not None else True,
        max_loss_per_day=float(row.max_loss_per_day or 5000),
        max_trades_per_day=row.max_trades_per_day or 20,
        max_position_size=float(row.max_position_size or 100000),
        max_single_order_value=float(row.max_single_order_value or 50000),
        active_strategies=row.active_strategies or "",
    )


@router.put("/")
def update_settings(
    body: UserSettingsUpdate,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """Update current user's settings in DB."""
    from core.models import UserSettings

    row = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    if not row:
        raise HTTPException(404, "Settings not found for this user")

    updated_fields = []
    for field in [
        "kite_api_key", "kite_api_secret",
        "trading_enabled", "paper_trade",
        "max_loss_per_day", "max_trades_per_day",
        "max_position_size", "max_single_order_value",
        "active_strategies",
    ]:
        value = getattr(body, field, None)
        if value is not None:
            # Encrypt sensitive fields before storing
            if field == "kite_api_secret":
                value = encrypt_value(value)
            setattr(row, field, value)
            updated_fields.append(field)

    db.commit()
    logger.info(f"Settings updated for user {user_id}: {updated_fields}")
    return {"status": "ok", "updated": updated_fields}
