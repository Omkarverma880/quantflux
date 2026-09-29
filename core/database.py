"""
PostgreSQL database connection layer using SQLAlchemy.
Provides sync engine + session factory for all DB operations.
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from config import settings
from core.logger import get_logger

logger = get_logger("database")

# Every pooled connection is a PostgreSQL backend process using several megabytes on the database
# service, charged by the gigabyte-hour. A dashboard with a handful of users needs a handful of
# connections; raise DB_POOL_SIZE if you ever see "QueuePool limit" in the logs.
_POOL = int(os.getenv("DB_POOL_SIZE", "5"))
_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "5"))

engine = create_engine(
    settings.DATABASE_URL,
    pool_size=_POOL,
    max_overflow=_OVERFLOW,
    pool_recycle=1800,          # drop connections a proxy may have already closed
    pool_pre_ping=True,
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

Base = declarative_base()


def get_db() -> Session:
    """
    FastAPI dependency — yields a DB session per request.
    Usage: db: Session = Depends(get_db)
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_session() -> Session:
    """
    Non-generator version for background tasks / non-FastAPI contexts.
    Caller must close the session manually.
    """
    return SessionLocal()
