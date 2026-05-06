"""
SQLAlchemy ORM models for all QuantFlux database tables.
Maps to the PostgreSQL tables in quantflux_db.
"""
from datetime import datetime, date, timezone
from sqlalchemy import (
    Column, Integer, String, Boolean, Float, Date, DateTime,
    Text, Numeric, ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from core.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(100))
    is_active = Column(Boolean, default=True)
    is_onboarded = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    settings = relationship("UserSettings", uselist=False, back_populates="user", cascade="all, delete-orphan")
    zerodha_sessions = relationship("ZerodhaSession", back_populates="user", cascade="all, delete-orphan")
    strategy_configs = relationship("StrategyConfig", back_populates="user", cascade="all, delete-orphan")
    strategy_states = relationship("StrategyState", back_populates="user", cascade="all, delete-orphan")
    trade_logs = relationship("TradeLog", back_populates="user", cascade="all, delete-orphan")
    order_history = relationship("OrderHistory", back_populates="user", cascade="all, delete-orphan")


class UserSettings(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    kite_api_key = Column(String(255))
    kite_api_secret = Column(Text)  # Fernet-encrypted
    kite_redirect_url = Column(String(500), default="")
    trading_enabled = Column(Boolean, default=False)
    paper_trade = Column(Boolean, default=True)
    max_loss_per_day = Column(Numeric(12, 2), default=5000)
    max_trades_per_day = Column(Integer, default=20)
    max_position_size = Column(Numeric(12, 2), default=100000)
    max_single_order_value = Column(Numeric(12, 2), default=50000)
    active_strategies = Column(Text, default="strategy1_gann_cv,strategy3_cv_vwap_ema_adx")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="settings")


class ZerodhaSession(Base):
    __tablename__ = "zerodha_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    access_token = Column(Text, nullable=False)  # Fernet-encrypted
    login_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("user_id", "login_date"),)

    user = relationship("User", back_populates="zerodha_sessions")


class StrategyConfig(Base):
    __tablename__ = "strategy_configs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    strategy_name = Column(String(100), nullable=False)
    config = Column(JSONB, nullable=False, default={})
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("user_id", "strategy_name"),)

    user = relationship("User", back_populates="strategy_configs")


class StrategyState(Base):
    __tablename__ = "strategy_states"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    strategy_name = Column(String(100), nullable=False)
    state = Column(JSONB, nullable=False, default={})
    trading_date = Column(Date)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("user_id", "strategy_name"),)

    user = relationship("User", back_populates="strategy_states")


class TradeLog(Base):
    __tablename__ = "trade_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    strategy_name = Column(String(100), nullable=False)
    trade_date = Column(Date, nullable=False)
    signal = Column(String(10))
    option_symbol = Column(String(100))
    atm_strike = Column(Integer)
    entry_price = Column(Numeric(12, 2))
    exit_price = Column(Numeric(12, 2))
    exit_type = Column(String(50))
    exit_time = Column(String(20))
    lot_size = Column(Integer)
    pnl = Column(Numeric(12, 2))
    extra = Column(JSONB, default={})
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (Index("idx_trade_logs_user_date", "user_id", "trade_date"),)

    user = relationship("User", back_populates="trade_logs")


class OrderHistory(Base):
    __tablename__ = "order_history"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    order_date = Column(Date, nullable=False)
    order_time = Column(DateTime)
    tradingsymbol = Column(String(100), nullable=False)
    exchange = Column(String(10), default="NFO")
    transaction_type = Column(String(10), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Numeric(12, 2))
    average_price = Column(Numeric(12, 2))
    status = Column(String(30))
    order_id = Column(String(50))
    tag = Column(String(50))
    order_type = Column(String(20))
    product = Column(String(10))
    extra = Column(JSONB, default={})
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (Index("idx_order_history_user_date", "user_id", "order_date"),)

    user = relationship("User", back_populates="order_history")


# ── Portfolio Analytics (independent module) ─────────────────────────
# These tables back the Portfolio Analytics page. They are completely
# isolated from strategy/intraday execution paths — read & write only
# happens via /api/portfolio/* routes.

class Watchlist(Base):
    __tablename__ = "watchlists"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(80), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    items = relationship("WatchlistItem", back_populates="watchlist", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_watchlist_user_name"),)


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    id = Column(Integer, primary_key=True, index=True)
    watchlist_id = Column(Integer, ForeignKey("watchlists.id", ondelete="CASCADE"), nullable=False, index=True)
    tradingsymbol = Column(String(60), nullable=False)
    exchange = Column(String(10), default="NSE")
    note = Column(String(255), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    watchlist = relationship("Watchlist", back_populates="items")

    __table_args__ = (UniqueConstraint("watchlist_id", "tradingsymbol", "exchange", name="uq_watchlist_item"),)


class ResearchEntry(Base):
    """Manual research idea — purely for tracking, never executed."""
    __tablename__ = "research_entries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tradingsymbol = Column(String(60), nullable=False)
    exchange = Column(String(10), default="NSE")
    entry_level = Column(Numeric(14, 4), nullable=False)
    target_level = Column(Numeric(14, 4), nullable=False)
    stop_level = Column(Numeric(14, 4))            # optional
    proximity_pct = Column(Numeric(6, 3), default=1.0)  # alert window in %
    note = Column(String(500), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))


class HoldingExitLevel(Base):
    """Optional user-defined exit price for a Zerodha holding.

    Holdings themselves are NOT stored in the DB — they are always pulled
    fresh from Kite. This table only stores the user's exit-level overlay.
    """
    __tablename__ = "holding_exit_levels"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tradingsymbol = Column(String(60), nullable=False)
    exchange = Column(String(10), default="NSE")
    exit_level = Column(Numeric(14, 4), nullable=False)
    proximity_pct = Column(Numeric(6, 3), default=1.0)
    note = Column(String(255), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("user_id", "tradingsymbol", "exchange", name="uq_holding_exit_user_sym"),
    )
