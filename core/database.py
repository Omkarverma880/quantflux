"""
PostgreSQL database connection layer using SQLAlchemy.
Provides sync engine + session factory for all DB operations.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from config import settings
from core.logger import get_logger

logger = get_logger("database")

engine = create_engine(
    settings.DATABASE_URL,
    pool_size=10,
    max_overflow=20,
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
