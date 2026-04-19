"""
Authentication module for QuantFlux.

Two auth layers:
  1. App auth: JWT-based multi-user login with bcrypt passwords (PostgreSQL)
  2. Zerodha auth: Per-user KiteConnect OAuth (tokens stored in DB, encrypted)
"""
import time
from datetime import datetime, date, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import HTTPException, Depends
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings as _cfg
from core.logger import get_logger

logger = get_logger("auth")

# ──────────────── JWT Config ────────────────
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

# ──────────────── Pydantic Models ────────────────

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    user_id: Optional[int] = None
    username: Optional[str] = None

class AppUser(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    is_onboarded: bool = False

    class Config:
        from_attributes = True

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

# ──────────────── Password Hashing (bcrypt) ────────────────

def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(plain_password: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed.encode("utf-8"))

# ──────────────── JWT Token ────────────────

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, _cfg.SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> TokenData:
    try:
        payload = jwt.decode(token, _cfg.SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")
        username = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return TokenData(user_id=user_id, username=username)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ──────────────── FastAPI Dependencies ────────────────

def get_current_user_id(token: str = Depends(oauth2_scheme)) -> int:
    """Extract user_id from JWT. Use as FastAPI dependency."""
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    data = decode_token(token)
    return data.user_id

def login_required(user_id: int = Depends(get_current_user_id)) -> int:
    """Alias for get_current_user_id — makes route signatures clearer."""
    return user_id

# ──────────────── User CRUD (DB) ────────────────

def create_user(db: Session, username: str, email: str, password: str, full_name: str = None):
    """Create a new user with hashed password + default settings row."""
    from core.models import User, UserSettings

    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        full_name=full_name,
        is_onboarded=True,
    )
    db.add(user)
    db.flush()  # get user.id

    # Create default settings row
    settings_row = UserSettings(user_id=user.id)
    db.add(settings_row)
    db.commit()
    db.refresh(user)
    return user

def authenticate_user(db: Session, username: str, password: str):
    """Verify credentials, return User ORM object or None."""
    from core.models import User
    user = db.query(User).filter(User.username == username, User.is_active == True).first()
    if not user or not verify_password(password, user.password_hash):
        return None
    return user

def get_user_by_id(db: Session, user_id: int):
    from core.models import User
    return db.query(User).filter(User.id == user_id).first()

# ──────────────── Password Reset ────────────────

RESET_TOKEN_EXPIRE_MINUTES = 15

def create_reset_token(user_id: int, username: str) -> str:
    """Create a short-lived JWT specifically for password reset."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {"user_id": user_id, "sub": username, "purpose": "password_reset", "exp": expire},
        _cfg.SECRET_KEY,
        algorithm=ALGORITHM,
    )

def verify_reset_token(token: str) -> int:
    """Validate a password-reset JWT. Returns user_id or raises."""
    try:
        payload = jwt.decode(token, _cfg.SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("purpose") != "password_reset":
            raise HTTPException(status_code=400, detail="Invalid reset token")
        user_id = payload.get("user_id")
        if user_id is None:
            raise HTTPException(status_code=400, detail="Invalid reset token")
        return user_id
    except JWTError:
        raise HTTPException(status_code=400, detail="Reset link has expired or is invalid")

def reset_user_password(db: Session, user_id: int, new_password: str) -> bool:
    """Update the user's password hash. Returns True on success."""
    from core.models import User
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        return False
    user.password_hash = hash_password(new_password)
    db.commit()
    return True

# ──────────────── Password Reset ────────────────

RESET_TOKEN_EXPIRE_MINUTES = 15

def create_reset_token(user_id: int, username: str) -> str:
    """Create a short-lived JWT specifically for password reset."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {"user_id": user_id, "sub": username, "purpose": "password_reset", "exp": expire},
        _cfg.SECRET_KEY,
        algorithm=ALGORITHM,
    )

def verify_reset_token(token: str) -> int:
    """Validate a password-reset JWT. Returns user_id or raises."""
    try:
        payload = jwt.decode(token, _cfg.SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("purpose") != "password_reset":
            raise HTTPException(status_code=400, detail="Invalid reset token")
        user_id = payload.get("user_id")
        if user_id is None:
            raise HTTPException(status_code=400, detail="Invalid reset token")
        return user_id
    except JWTError:
        raise HTTPException(status_code=400, detail="Reset link has expired or is invalid")

def reset_user_password(db: Session, user_id: int, new_password: str) -> bool:
    """Update the user's password hash. Returns True on success."""
    from core.models import User
    user = db.query(User).filter(User.id == user_id, User.is_active == True).first()
    if not user:
        return False
    user.password_hash = hash_password(new_password)
    db.commit()
    return True

# ──────────────── Zerodha Auth (per-user) ────────────────

from kiteconnect import KiteConnect

# Per-user KiteConnect instances cached in memory
_user_kite_instances: dict[int, KiteConnect] = {}


class UserZerodhaAuth:
    """
    Manages Zerodha authentication for a specific user.
    Tokens stored in DB (zerodha_sessions table).
    """

    @staticmethod
    def get_kite_for_user(db: Session, user_id: int) -> KiteConnect:
        """Return an authenticated KiteConnect for this user, or raise."""
        from core.models import UserSettings, ZerodhaSession
        from core.encryption import decrypt_value

        settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        if not settings or not settings.kite_api_key:
            raise RuntimeError("Zerodha API credentials not configured. Go to Settings.")

        # Check for today's access token in DB
        session = (
            db.query(ZerodhaSession)
            .filter(ZerodhaSession.user_id == user_id, ZerodhaSession.login_date == date.today())
            .first()
        )

        if session:
            token = decrypt_value(session.access_token)
            # Reuse cached instance if available
            if user_id in _user_kite_instances:
                kite = _user_kite_instances[user_id]
                kite.set_access_token(token)
                return kite
            # Create new instance with saved token
            kite = KiteConnect(api_key=settings.kite_api_key)
            kite.set_access_token(token)
            _user_kite_instances[user_id] = kite
            return kite

        raise RuntimeError("Not logged in to Zerodha today. Please login first.")

    @staticmethod
    def get_kite_or_none(db: Session, user_id: int) -> Optional[KiteConnect]:
        """Return kite if authenticated today, else None."""
        try:
            return UserZerodhaAuth.get_kite_for_user(db, user_id)
        except RuntimeError:
            return None

    @staticmethod
    def is_authenticated(db: Session, user_id: int) -> bool:
        from core.models import ZerodhaSession
        return (
            db.query(ZerodhaSession)
            .filter(ZerodhaSession.user_id == user_id, ZerodhaSession.login_date == date.today())
            .first()
        ) is not None

    @staticmethod
    def get_login_url(db: Session, user_id: int) -> str:
        from core.models import UserSettings
        settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        if not settings or not settings.kite_api_key:
            raise RuntimeError("Zerodha API credentials not configured. Add your API Key and Secret in Settings first.")
        kite = KiteConnect(api_key=settings.kite_api_key)
        _user_kite_instances[user_id] = kite
        return kite.login_url()

    @staticmethod
    def complete_login(db: Session, user_id: int, request_token: str):
        """Exchange request_token for access_token and save to DB (encrypted)."""
        from core.models import UserSettings, ZerodhaSession
        from core.encryption import encrypt_value, decrypt_value

        settings = db.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        if not settings:
            raise RuntimeError("User settings not found")

        kite = _user_kite_instances.get(user_id)
        if not kite:
            kite = KiteConnect(api_key=settings.kite_api_key)
            _user_kite_instances[user_id] = kite

        api_secret = decrypt_value(settings.kite_api_secret) if settings.kite_api_secret else ""
        session_data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = session_data["access_token"]
        kite.set_access_token(access_token)

        encrypted_token = encrypt_value(access_token)

        # Upsert today's session in DB
        existing = (
            db.query(ZerodhaSession)
            .filter(ZerodhaSession.user_id == user_id, ZerodhaSession.login_date == date.today())
            .first()
        )
        if existing:
            existing.access_token = encrypted_token
            existing.created_at = datetime.now(timezone.utc)
        else:
            db.add(ZerodhaSession(
                user_id=user_id,
                access_token=encrypted_token,
                login_date=date.today(),
            ))
        db.commit()
        logger.info(f"Zerodha login successful for user_id={user_id}")

    @staticmethod
    def logout(db: Session, user_id: int):
        from core.models import ZerodhaSession
        db.query(ZerodhaSession).filter(
            ZerodhaSession.user_id == user_id,
            ZerodhaSession.login_date == date.today(),
        ).delete()
        db.commit()
        _user_kite_instances.pop(user_id, None)


# ──────────────── Legacy helpers ─────────────────
# Bridge for old code (broker.py, strategy routes) that still uses
# get_auth() / get_kite().  Uses the first active Zerodha session found.

class _LegacyAuthProxy:
    """Bridges old get_auth()/get_kite() calls to the new per-user system."""

    @property
    def is_authenticated(self) -> bool:
        from core.database import get_db_session
        from core.models import ZerodhaSession
        db = get_db_session()
        try:
            return db.query(ZerodhaSession).filter(
                ZerodhaSession.login_date == date.today()
            ).first() is not None
        finally:
            db.close()

    @property
    def kite(self) -> KiteConnect:
        return self.get_kite()

    def get_kite(self) -> KiteConnect:
        from core.database import get_db_session
        from core.models import ZerodhaSession, UserSettings
        from core.encryption import decrypt_value
        db = get_db_session()
        try:
            session = db.query(ZerodhaSession).filter(
                ZerodhaSession.login_date == date.today()
            ).first()
            if not session:
                raise RuntimeError("No Zerodha session today.")
            settings = db.query(UserSettings).filter(
                UserSettings.user_id == session.user_id
            ).first()
            kite = KiteConnect(api_key=settings.kite_api_key)
            kite.set_access_token(decrypt_value(session.access_token))
            return kite
        finally:
            db.close()

    def get_kite_or_none(self) -> Optional[KiteConnect]:
        try:
            return self.get_kite()
        except Exception:
            return None

    def login_url(self):
        """For legacy code that calls auth.kite.login_url()."""
        from core.database import get_db_session
        from core.models import ZerodhaSession, UserSettings
        db = get_db_session()
        try:
            # Find any user with kite_api_key set
            settings = db.query(UserSettings).filter(
                UserSettings.kite_api_key.isnot(None)
            ).first()
            if not settings:
                raise RuntimeError("No user has Zerodha API credentials configured.")
            kite = KiteConnect(api_key=settings.kite_api_key)
            return kite.login_url()
        finally:
            db.close()

    def set_access_token(self, request_token: str):
        """Legacy: called by old callback. Find the user who initiated login."""
        from core.database import get_db_session
        from core.models import UserSettings
        db = get_db_session()
        try:
            # Find user with matching KiteConnect in cache
            for uid, kite in _user_kite_instances.items():
                try:
                    settings = db.query(UserSettings).filter(
                        UserSettings.user_id == uid
                    ).first()
                    if settings:
                        UserZerodhaAuth.complete_login(db, uid, request_token)
                        return
                except Exception:
                    continue
            raise RuntimeError("Could not find user for this login callback.")
        finally:
            db.close()


_legacy_auth: Optional[_LegacyAuthProxy] = None


def get_auth() -> _LegacyAuthProxy:
    global _legacy_auth
    if _legacy_auth is None:
        _legacy_auth = _LegacyAuthProxy()
    return _legacy_auth


def get_kite() -> KiteConnect:
    """Legacy helper — used by broker.py."""
    return get_auth().get_kite()

