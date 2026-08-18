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


class Strategy11Leg(Base):
    """One row per option leg for Strategy 11 (positional VWAP/prev-VWAP).

    Authoritative, browseable store of every CE/PE leg — open and closed — so
    positions survive app restarts/redeploys and are fully visible in pgAdmin
    (e.g. ``SELECT * FROM strategy11_legs WHERE state='OPEN'``). A CE+PE entry
    shares one ``pair_id``.
    """
    __tablename__ = "strategy11_legs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    pair_id = Column(String(40), nullable=False)         # groups the CE + PE of one entry
    trade_date = Column(Date, nullable=False)
    entry_time = Column(String(10))
    signal = Column(String(10))                          # BULL / BEAR
    expiry_type = Column(String(10))                     # weekly / monthly
    expiry = Column(Date)
    spot = Column(Numeric(12, 2))
    option_type = Column(String(2), nullable=False)      # CE / PE
    strike = Column(Integer)
    symbol = Column(String(100), nullable=False)
    token = Column(Integer)
    qty = Column(Integer)
    entry_price = Column(Numeric(12, 2))
    target_price = Column(Numeric(12, 2))
    ltp = Column(Numeric(12, 2))
    state = Column(String(20), default="OPEN")           # OPEN/TARGET/LEG2_EXIT/EXPIRY/MANUAL_EXIT
    leg2_armed = Column(Boolean, default=False)
    broke_out = Column(Boolean, default=False)
    leg2_level = Column(Numeric(12, 2))
    target_gtt = Column(String(40))
    leg2_gtt = Column(String(40))
    exit_price = Column(Numeric(12, 2))
    exit_time = Column(String(10))
    exit_date = Column(Date)
    pnl = Column(Numeric(12, 2))
    exit_reason = Column(String(20))
    paper = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("user_id", "pair_id", "option_type", name="uq_s11_leg"),
        Index("idx_s11_user_state", "user_id", "state"),
    )


class ResearchConfig(Base):
    """Durable per-user config for research modules (e.g. the Sentiment
    Analyzer). UI edits land here so they survive restarts/redeploys — the
    JSON file in the repo is only the seed/default."""
    __tablename__ = "research_config"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(50), nullable=False)          # e.g. "sentiment"
    config = Column(JSONB, nullable=False, default={})
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_research_config"),)


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


class SectorOverride(Base):
    """User-assigned sector for a symbol (overrides the static SECTOR_MAP).

    Zerodha does not return sector data, so users can manually classify
    each ticker. Scoped per-user so different users can have their own
    classification preferences.
    """
    __tablename__ = "sector_overrides"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tradingsymbol = Column(String(60), nullable=False)
    sector = Column(String(60), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("user_id", "tradingsymbol", name="uq_sector_override_user_sym"),
    )


class Strategy10StockList(Base):
    """Uploaded equity stock list for Strategy 10 (Equity Intraday).

    Global / shared across the platform — the most recently uploaded row is
    the *active* list. Keeping every upload gives an audit trail and the
    "if not re-uploaded today, use the previous list" behaviour for free
    (the strategy always loads the latest row).

    `symbols` is a JSON list of {"symbol": str, "exchange": str} objects.
    """
    __tablename__ = "strategy10_stock_lists"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255))
    symbols = Column(JSONB, nullable=False, default=list)
    stock_count = Column(Integer, default=0)
    uploaded_by = Column(Integer)  # user_id (audit only; list is global)
    uploaded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class PMVwapStraddleTrade(Base):
    """Append-only research log for the Prev-Month-VWAP Straddle Research.

    One row per simulated straddle signal (never overwritten). Grouped by
    ``run_id`` so backtest runs can be compared side-by-side and the live-day
    scan simply keeps appending. Purely virtual — no order is ever placed.
    Auto-created at startup via ``Base.metadata.create_all`` (no migration).
    """
    __tablename__ = "pmvwap_straddle_trades"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    run_id = Column(String(40), nullable=False)          # groups one backtest/live run
    mode = Column(String(10))                            # single | multi | live
    trade_date = Column(Date, nullable=False)
    signal_time = Column(String(10))
    underlying = Column(String(40), nullable=False)
    atm_strike = Column(Numeric(12, 2))
    ce_symbol = Column(String(100))
    pe_symbol = Column(String(100))
    lot_size = Column(Integer)
    combined_premium = Column(Numeric(12, 2))
    target_premium = Column(Numeric(12, 2))
    combined_mtm = Column(Numeric(14, 2))
    targets_hit = Column(Integer, default=0)
    status = Column(String(20), default="FULL EXIT")
    data = Column(JSONB, nullable=False, default=dict)   # full research-log row
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_pmvwap_user_run", "user_id", "run_id"),
        Index("idx_pmvwap_user_date", "user_id", "trade_date"),
    )


class PMVwapEquityTrade(Base):
    """Append-only research log for the Prev-Month-VWAP Equity-Holding Research.

    One row per simulated equity holding (never overwritten), grouped by
    ``run_id`` for run comparison and live-day appends. Purely virtual — no
    order is ever placed. Auto-created at startup via ``create_all``.
    """
    __tablename__ = "pmvwap_equity_trades"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    run_id = Column(String(40), nullable=False)
    mode = Column(String(10))                            # single | multi | live
    trade_date = Column(Date, nullable=False)
    signal_time = Column(String(10))
    underlying = Column(String(40), nullable=False)
    entry_price = Column(Numeric(12, 2))
    exit_price = Column(Numeric(12, 2))
    mtm = Column(Numeric(14, 2))
    return_pct = Column(Numeric(8, 2))
    status = Column(String(20), default="CLOSED")
    data = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_pmveq_user_run", "user_id", "run_id"),
        Index("idx_pmveq_user_date", "user_id", "trade_date"),
    )


class ResearchWatchlist(Base):
    """User-defined watchlist for the research modules (shared across them).

    A named list of equity symbols the user can backtest instead of the whole
    F&O universe. Editable from the UI or via file upload/download, and stored
    durably so it survives restarts. Auto-created at startup via ``create_all``.
    """
    __tablename__ = "research_watchlists"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(80), nullable=False)
    symbols = Column(JSONB, nullable=False, default=list)     # list[str] of tradingsymbols
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_research_watchlist"),
        Index("idx_research_watchlist_user", "user_id"),
    )


class EquityHoldingPosition(Base):
    """Live/paper position for the Equity Prev-Month-VWAP Holding strategy.

    Authoritative, browseable store of every holding — open and closed — so the
    strategy survives restarts / daily token refresh (positions carry across
    days). One row per stock entry. GTT ids link to the server-side exit
    triggers. Auto-created at startup via ``create_all``.
    """
    __tablename__ = "equity_holding_positions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    trade_date = Column(Date, nullable=False)
    entry_time = Column(String(10))
    underlying = Column(String(40), nullable=False)
    exchange = Column(String(6), default="NSE")
    token = Column(Integer)
    qty = Column(Integer)
    entry_price = Column(Numeric(12, 2))
    capital = Column(Numeric(14, 2))
    target_price = Column(Numeric(12, 2))
    stop_price = Column(Numeric(12, 2))
    prev_month_vwap = Column(Numeric(12, 2))
    prev_week_vwap = Column(Numeric(12, 2))
    ltp = Column(Numeric(12, 2))
    state = Column(String(20), default="OPEN")          # OPEN | CLOSED
    target_gtt = Column(String(40))
    stop_gtt = Column(String(40))
    exit_price = Column(Numeric(12, 2))
    exit_time = Column(String(10))
    exit_date = Column(Date)
    hold_days = Column(Integer)
    pnl = Column(Numeric(14, 2))
    exit_reason = Column(String(30))
    paper = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index("idx_eqhold_user_state", "user_id", "state"),
        Index("idx_eqhold_user_date", "user_id", "trade_date"),
    )


class OPEIRecommendation(Base):
    """Logged premium entry recommendation from the OPEI research engine.

    Every institutional-grade (or activated) recommendation is stored with its
    outcome fields (triggered / target / SL / MFE / MAE) for later win-rate and
    CSV analysis. Research-only — no orders. Auto-created via ``create_all``.
    """
    __tablename__ = "opei_recommendations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    trade_date = Column(Date, nullable=False)
    signal_time = Column(String(12))
    side = Column(String(2))                    # CE | PE
    symbol = Column(String(100))
    strike = Column(Integer)
    premium = Column(Numeric(12, 2))
    level = Column(Numeric(12, 2))
    confidence = Column(Numeric(6, 2))
    band = Column(String(30))
    sl = Column(Numeric(12, 2))
    target1 = Column(Numeric(12, 2))
    reasons = Column(JSONB, default=list)
    # outcome (updated by the tracker)
    triggered = Column(Boolean, default=False)
    succeeded = Column(Boolean)
    target_hit = Column(Boolean, default=False)
    sl_hit = Column(Boolean, default=False)
    duration_min = Column(Integer)
    mfe = Column(Numeric(12, 2))                 # max favourable excursion
    mae = Column(Numeric(12, 2))                 # max adverse excursion
    data = Column(JSONB, default=dict)

    __table_args__ = (
        Index("idx_opei_user_date", "user_id", "trade_date"),
    )


class QMIESnapshot(Base):
    """Immutable ranked-scan snapshot for Research-10 (QMIE) reproducibility.

    Append-only: every scan persists its full ranked result + provenance
    (as-of, config/ruleset versions, benchmark, counts, market context). Research
    only — no orders. Auto-created via ``create_all``.
    """
    __tablename__ = "qmie_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    snapshot_id = Column(String(40), index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    as_of = Column(String(40))
    horizon = Column(String(20))
    config_version = Column(String(60))
    ruleset_version = Column(String(60))
    benchmark = Column(String(40))
    counts = Column(JSONB, default=dict)
    config = Column(JSONB, default=dict)
    results = Column(JSONB, default=list)
    restricted = Column(JSONB, default=list)
    market_context = Column(JSONB, default=dict)

    __table_args__ = (
        Index("idx_qmie_user_created", "user_id", "created_at"),
    )


class DataDataset(Base):
    """Research → Data Downloader dataset metadata + download-job state.

    One row per download request. Historical candles are stored on disk
    (Parquet/CSV under the data directory); PostgreSQL holds only metadata,
    status, per-chunk resume state, quality and the file path. Read-only w.r.t.
    the market — never places orders. Auto-created via ``create_all``.
    """
    __tablename__ = "data_datasets"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))
    # instrument
    symbol = Column(String(120))
    exchange = Column(String(12))
    segment = Column(String(20))
    instrument_type = Column(String(20))
    instrument_token = Column(Integer)
    expiry = Column(String(12))
    strike = Column(Numeric(12, 2))
    option_type = Column(String(2))
    # request
    interval = Column(String(12))
    from_date = Column(Date)
    to_date = Column(Date)
    timezone = Column(String(32), default="Asia/Kolkata")
    include_oi = Column(Boolean, default=True)
    fmt = Column(String(10), default="parquet")
    normalize = Column(Boolean, default=True)
    # job / status
    status = Column(String(16), default="queued", index=True)
    progress = Column(Integer, default=0)
    rows = Column(Integer, default=0)
    chunks_total = Column(Integer, default=0)
    chunks_completed = Column(Integer, default=0)
    chunks = Column(JSONB, default=list)
    error = Column(Text)
    # file
    file_path = Column(String(500))
    file_format = Column(String(10))
    checksum = Column(String(64))
    size_bytes = Column(Integer)
    quality = Column(JSONB, default=dict)

    __table_args__ = (
        Index("idx_dataset_user_created", "user_id", "created_at"),
    )


class OPEIPaperPosition(Base):
    """OPEI (Research-9) paper position for testing the first recommended label.

    Two sections per side per day: Section 1 (no SL/target, 15:15 square-off) and
    Section 2 (highest target + SL, optional re-entry). PAPER ONLY — never places
    an order. Auto-created via ``create_all``.
    """
    __tablename__ = "opei_paper_positions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))
    trade_date = Column(Date, nullable=False)
    section = Column(Integer)                 # 1 = no-SL/TGT square-off, 2 = TGT+SL(+reentry)
    side = Column(String(2))                  # CE | PE
    symbol = Column(String(100))
    strike = Column(Integer)
    qty = Column(Integer)
    entry_price = Column(Numeric(12, 2))
    entry_time = Column(String(12))
    target = Column(Numeric(12, 2))
    sl = Column(Numeric(12, 2))
    ltp = Column(Numeric(12, 2))
    mfe = Column(Numeric(12, 2), default=0)
    mae = Column(Numeric(12, 2), default=0)
    pnl = Column(Numeric(12, 2), default=0)
    status = Column(String(12), default="OPEN")   # OPEN | TARGET | SL | SQUAREOFF
    exit_price = Column(Numeric(12, 2))
    exit_time = Column(String(12))
    exit_reason = Column(String(12))

    __table_args__ = (
        Index("idx_opei_pos_user_date", "user_id", "trade_date"),
    )


class AppSetting(Base):
    """Global (app-wide, not per-user) key-value settings persisted in PostgreSQL.

    Used for values that must survive redeploys on an ephemeral filesystem — e.g.
    the universal Telegram bot token / chat id. Value is JSONB. Auto-created via
    ``create_all``.
    """
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(64), unique=True, index=True, nullable=False)
    value = Column(JSONB, default=dict)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class FourthCandlePosition(Base):
    """Paper/live position for the 4th-Candle Strategy (Equity Strategy #2).

    Buys an ATM CE/PE of an F&O stock on a 4th-candle breakout; positional (NRML)
    with target/SL on the option premium. Real orders only when paper_trade is
    off AND the global trading gate is on. Auto-created via ``create_all``.
    """
    __tablename__ = "fourth_candle_positions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    trade_date = Column(Date, nullable=False)
    underlying = Column(String(40))
    opt_type = Column(String(2))            # CE | PE
    symbol = Column(String(100))
    strike = Column(Numeric(12, 2))
    expiry = Column(String(12))
    token = Column(Integer)
    qty = Column(Integer)
    lot = Column(Integer)
    entry_price = Column(Numeric(12, 2))
    entry_time = Column(String(12))
    target = Column(Numeric(12, 2))
    sl = Column(Numeric(12, 2))
    ltp = Column(Numeric(12, 2))
    mtm = Column(Numeric(14, 2), default=0)
    mfe = Column(Numeric(14, 2), default=0)         # max profit
    mae = Column(Numeric(14, 2), default=0)         # max loss
    status = Column(String(12), default="OPEN")     # OPEN | TARGET | STOP | SQUAREOFF
    exit_price = Column(Numeric(12, 2))
    exit_time = Column(String(12))
    exit_reason = Column(String(12))
    product = Column(String(8), default="NRML")
    paper = Column(Boolean, default=True)
    hold_days = Column(Integer)

    __table_args__ = (
        Index("idx_fourthc_user_date", "user_id", "trade_date"),
    )


class FourthCandleEquityPosition(Base):
    """Paper/live position for the 4th-Candle CASH-EQUITY Strategy (Equity #3).

    Same 4th-candle setup but trades the STOCK directly — LONG (buy) on a CALL
    bias, SHORT (sell) on a PUT bias — as MIS intraday or CNC holding, with
    target/SL on the stock price. Real orders only when paper_trade is off AND
    the global trading gate is on. Auto-created via ``create_all``.
    """
    __tablename__ = "fourth_candle_equity_positions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    trade_date = Column(Date, nullable=False)
    underlying = Column(String(40))
    direction = Column(String(5))           # LONG | SHORT
    symbol = Column(String(60))             # NSE tradingsymbol (same as underlying)
    exchange = Column(String(8), default="NSE")
    token = Column(Integer)
    qty = Column(Integer)
    entry_price = Column(Numeric(12, 2))
    entry_time = Column(String(12))
    target = Column(Numeric(12, 2))
    sl = Column(Numeric(12, 2))
    ltp = Column(Numeric(12, 2))
    mtm = Column(Numeric(14, 2), default=0)
    mfe = Column(Numeric(14, 2), default=0)         # max profit
    mae = Column(Numeric(14, 2), default=0)         # max loss
    status = Column(String(12), default="OPEN")     # OPEN | TARGET | STOP | SQUAREOFF
    exit_price = Column(Numeric(12, 2))
    exit_time = Column(String(12))
    exit_reason = Column(String(12))
    product = Column(String(8), default="MIS")      # MIS | CNC
    paper = Column(Boolean, default=True)
    hold_days = Column(Integer)

    __table_args__ = (
        Index("idx_fourthc_eq_user_date", "user_id", "trade_date"),
    )
