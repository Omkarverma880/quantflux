"""
Central configuration for the trading system.
All sensitive values are loaded from environment variables or .env file.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# ──────────────── Database ────────────────
_raw_db_url = os.getenv("DATABASE_URL", "postgresql://postgres:1605@localhost:5432/quantflux_db")
# Railway provides postgres:// but SQLAlchemy needs postgresql://
_raw_db_url = _raw_db_url.replace("postgres://", "postgresql://", 1) if _raw_db_url.startswith("postgres://") else _raw_db_url


def _with_driver(url: str) -> str:
    """Name the PostgreSQL driver in the URL instead of relying on SQLAlchemy's default.

    A bare ``postgresql://`` means psycopg2 on SQLAlchemy 2.0 but psycopg (version 3) on 2.1, so
    an unpinned upgrade silently switches drivers and the container dies at import with
    "No module named 'psycopg'". We install psycopg2-binary, so we ask for it by name; an explicit
    ``postgresql+something://`` from the environment is left exactly as it is.
    """
    return url.replace("postgresql://", "postgresql+psycopg2://", 1) if url.startswith("postgresql://") else url


DATABASE_URL = _with_driver(_raw_db_url)

# ──────────────── Token Storage ────────────────
TOKEN_DIR = BASE_DIR / "data" / "tokens"
TOKEN_DIR.mkdir(parents=True, exist_ok=True)
ACCESS_TOKEN_FILE = TOKEN_DIR / "access_token.json"

# ──────────────── Logging ────────────────
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ──────────────── Trading ────────────────
TRADING_ENABLED = os.getenv("TRADING_ENABLED", "false").lower() == "true"
PAPER_TRADE = os.getenv("PAPER_TRADE", "true").lower() == "true"

# ──────────────── Risk Defaults ────────────────
MAX_LOSS_PER_DAY = float(os.getenv("MAX_LOSS_PER_DAY", "5000"))
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "20"))
MAX_POSITION_SIZE = float(os.getenv("MAX_POSITION_SIZE", "100000"))
MAX_SINGLE_ORDER_VALUE = float(os.getenv("MAX_SINGLE_ORDER_VALUE", "50000"))

# ──────────────── Auto Square-off ────────────────
AUTO_SQUARE_OFF_TIME = os.getenv("AUTO_SQUARE_OFF_TIME", "15:15")

# ──────────────── App Auth ────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")

# ──────────────── Server ────────────────
PORT = int(os.getenv("PORT", "8000"))
APP_URL = os.getenv("APP_URL", "http://localhost:8000")  # public URL (Railway sets this)
CORS_ORIGIN_REGEX = os.getenv(
    "CORS_ORIGIN_REGEX",
    r"https?://(localhost|127\.0\.0\.1)(:\d+)?|https://.*\.up\.railway\.app"
)

# ──────────────── Data ────────────────
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ──────────────── Strategy Registry ────────────────
ACTIVE_STRATEGIES = os.getenv("ACTIVE_STRATEGIES", "").split(",")
ACTIVE_STRATEGIES = [s.strip() for s in ACTIVE_STRATEGIES if s.strip()]
