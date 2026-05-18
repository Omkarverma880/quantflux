"""
Authentication API routes — multi-user.
  POST /register         — create account
  POST /app-login        — JWT login
  GET  /me               — current user info
  POST /onboard          — save Zerodha API keys (first-time setup)
  GET  /login            — Zerodha login URL (per-user)
  GET  /callback         — Zerodha OAuth callback (per-user)
  GET  /status           — Zerodha auth status (per-user)
  POST /logout           — clear Zerodha session (per-user)
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import timedelta
from typing import Optional
from slowapi import Limiter
from slowapi.util import get_remote_address

from core.database import get_db
from core.auth import (
    authenticate_user, create_user, create_access_token, get_user_by_id,
    Token, login_required, UserZerodhaAuth,
    create_reset_token, verify_reset_token, reset_user_password,
)
from core.logger import get_logger

router = APIRouter()
logger = get_logger("api.auth")
limiter = Limiter(key_func=get_remote_address)


# ── Request models ─────────────────────────────────

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    full_name: str = ""

class AppLoginRequest(BaseModel):
    username: str
    password: str

class OnboardRequest(BaseModel):
    kite_api_key: str
    kite_api_secret: str

class ForgotPasswordRequest(BaseModel):
    username: str
    email: str

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


# ── Register ───────────────────────────────────────

@router.post("/register")
@limiter.limit("5/minute")
def register(request: Request, body: RegisterRequest, db: Session = Depends(get_db)):
    """Create a new user account."""
    from core.models import User

    if len(body.username) < 3:
        raise HTTPException(400, "Username must be at least 3 characters")
    if len(body.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")

    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(409, "Username already taken")
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(409, "Email already registered")

    user = create_user(db, body.username, body.email, body.password, body.full_name)
    token = create_access_token(
        data={"sub": user.username, "user_id": user.id},
        expires_delta=timedelta(hours=24),
    )
    logger.info(f"New user registered: {user.username} (id={user.id})")
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "full_name": user.full_name,
            "is_onboarded": user.is_onboarded,
        },
    }


# ── Login ──────────────────────────────────────────

@router.post("/app-login")
@limiter.limit("10/minute")
def app_login(request: Request, body: AppLoginRequest, db: Session = Depends(get_db)):
    """Authenticate user and return JWT token."""
    user = authenticate_user(db, body.username, body.password)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Invalid username or password"})

    token = create_access_token(
        data={"sub": user.username, "user_id": user.id},
        expires_delta=timedelta(hours=24),
    )
    logger.info(f"App login successful: {user.username}")
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "full_name": user.full_name,
        },
    }


# ── Current user info ──────────────────────────────

@router.get("/me")
def get_me(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Return current user profile."""
    user = get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "is_onboarded": user.is_onboarded,
    }


# ── Onboarding (Zerodha API keys) ─────────────────

@router.post("/onboard")
def onboard(body: OnboardRequest, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Save Zerodha API credentials after registration."""
    from core.models import UserSettings, User

    settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
    if not settings:
        raise HTTPException(404, "User settings not found")

    settings.kite_api_key = body.kite_api_key

    from core.encryption import encrypt_value
    settings.kite_api_secret = encrypt_value(body.kite_api_secret)

    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.is_onboarded = True

    db.commit()
    logger.info(f"User {user_id} onboarded with Zerodha API keys")
    return {"status": "ok", "is_onboarded": True}


# ── Zerodha Login (per-user) ──────────────────────

@router.get("/login")
def zerodha_login(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Return the Zerodha login URL for this user."""
    try:
        login_url = UserZerodhaAuth.get_login_url(db, user_id)
        return JSONResponse({"login_url": login_url})
    except RuntimeError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})


@router.get("/callback")
def zerodha_callback(
    request: Request,
    request_token: str = "",
    status: str = "",
    user_id: str = "",
):
    """
    Zerodha redirects here after login.

    We embed `user_id` inside Kite's `redirect_params` when starting the
    login (see `UserZerodhaAuth.get_login_url`). Kite forwards that value
    back in one of two ways depending on app configuration:

      1. As a literal `?redirect_params=user_id%3D<id>` query string
         (current Kite Connect behaviour).
      2. Decoded into individual query params (e.g. `?user_id=<id>`),
         which happens when a redirect URL is configured with the param
         baked into it.

    We accept both, so multi-user logins work regardless of how each
    user's Kite Connect app is configured on the broker side.
    """
    if status != "success" or not request_token:
        return HTMLResponse(
            "<html><body><h2>Login failed.</h2>"
            "<p>No request token received. Please close this tab and try again.</p>"
            "</body></html>",
            status_code=400,
        )

    # Resolve user_id: direct query param → redirect_params → any other
    # query param that may carry it (defensive).
    resolved_uid: Optional[int] = None
    try:
        if user_id:
            resolved_uid = int(user_id)
    except (TypeError, ValueError):
        resolved_uid = None

    if resolved_uid is None:
        try:
            from urllib.parse import parse_qs, unquote
            qp = dict(request.query_params)
            raw_rp = qp.get("redirect_params") or ""
            # Kite may URL-encode or double-encode the value; try both.
            for candidate in (raw_rp, unquote(raw_rp)):
                if not candidate:
                    continue
                parsed = parse_qs(candidate)
                if "user_id" in parsed and parsed["user_id"]:
                    resolved_uid = int(parsed["user_id"][0])
                    break
        except Exception as e:
            logger.warning(f"callback: failed to parse redirect_params: {e}")

    try:
        if not resolved_uid:
            raise RuntimeError(
                "Cannot determine which user is logging in. "
                "Please retry the login from the app."
            )

        from core.database import get_db_session
        db = get_db_session()
        try:
            UserZerodhaAuth.complete_login(db, resolved_uid, request_token)
        finally:
            db.close()

        logger.info(f"Zerodha login successful for user_id={resolved_uid}")
        return HTMLResponse(
            "<html><body>"
            "<h2 style='color:green'>Login successful!</h2>"
            "<p>You can close this tab. The dashboard will update automatically.</p>"
            "<script>"
            "if(window.opener){window.opener.postMessage({type:'zerodha_login_success'},'*');}"
            "window.close();"
            "</script>"
            "</body></html>"
        )
    except Exception as e:
        logger.error(f"Login callback error: {e}")
        return HTMLResponse(
            f"<html><body><h2 style='color:red'>Login Error</h2>"
            f"<p>{e}</p>"
            f"<p>Please close this tab and try again.</p>"
            f"</body></html>",
            status_code=500,
        )


# ── Zerodha Status (per-user) ─────────────────────

@router.get("/status")
def auth_status(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Check if this user is authenticated with Zerodha today."""
    is_valid = UserZerodhaAuth.is_authenticated(db, user_id)
    profile = None
    if is_valid:
        try:
            kite = UserZerodhaAuth.get_kite_for_user(db, user_id)
            p = kite.profile()
            profile = {
                "name": p.get("user_name", ""),
                "user_id": p.get("user_id", ""),
                "email": p.get("email", ""),
                "broker": p.get("broker", ""),
            }
        except Exception:
            pass

    return {"authenticated": is_valid, "profile": profile}


# ── Zerodha Logout (per-user) ─────────────────────

@router.post("/logout")
def zerodha_logout(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Clear today's Zerodha session for this user."""
    UserZerodhaAuth.logout(db, user_id)
    return {"status": "logged_out"}


# ── Password Reset ─────────────────────────────────

@router.post("/forgot-password")
@limiter.limit("5/minute")
def forgot_password(request: Request, body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Verify username + email match, return a short-lived reset token."""
    from core.models import User
    user = db.query(User).filter(
        User.username == body.username,
        User.email == body.email,
        User.is_active == True,
    ).first()
    if not user:
        raise HTTPException(400, "No account found with that username and email combination")
    token = create_reset_token(user.id, user.username)
    logger.info(f"Password reset token issued for user: {user.username}")
    return {"reset_token": token}


@router.post("/reset-password")
@limiter.limit("5/minute")
def reset_password(request: Request, body: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Validate reset token and set new password."""
    if len(body.new_password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    user_id = verify_reset_token(body.token)
    success = reset_user_password(db, user_id, body.new_password)
    if not success:
        raise HTTPException(400, "Could not reset password. Account may be inactive.")
    logger.info(f"Password reset completed for user_id={user_id}")
    return {"status": "ok", "message": "Password has been reset. You can now sign in."}

