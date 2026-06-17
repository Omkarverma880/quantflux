"""
API routes for Strategy 10 — Equity Intraday Breakout (+ manual equity desk).

The candidate stock list is uploaded by the user as a CSV and persisted in
Postgres (global / shared — latest upload wins). The strategy trades the
uploaded list only; there is no auto gainer/volume scanning.
"""
import csv
import io
import json
from datetime import datetime
from fastapi import APIRouter, Depends, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import settings
from core.logger import get_logger
from core.database import get_db
from core.auth import login_required
from core.broker import Broker, get_user_broker
from core.models import Strategy10StockList
from strategies.strategy10_equity_intraday import Strategy10EquityIntraday

router = APIRouter()
logger = get_logger("api.strategy10")

_user_strategies: dict[int, Strategy10EquityIntraday] = {}

CONFIG_FILE = settings.DATA_DIR / "strategy_configs" / "strategy10_equity_intraday.json"


class Strategy10Config(BaseModel):
    capital_per_stock: float = 20000.0
    target_points: float = 30.0
    sl_points: float = 20.0
    volume_filter: bool = False
    max_positions: int = 5
    lookback_days: int = 5
    entry_cutoff: str = "09:30"
    squareoff_time: str = "15:15"
    exchange: str = "NSE"


class ManualOrder(BaseModel):
    symbol: str
    quantity: int | None = None
    capital: float | None = None
    sl_points: float | None = None
    target_points: float | None = None
    exchange: str | None = None


class ManualModify(BaseModel):
    symbol: str
    sl_price: float | None = None
    target_price: float | None = None
    sl_points: float | None = None
    target_points: float | None = None


class ManualExit(BaseModel):
    symbol: str


# ── Config persistence ──────────────────────────────────────────────


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text()).get("params", {})
        except json.JSONDecodeError:
            pass
    return {}


def _save_config(params: dict):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps({"params": params}, indent=2))


def _is_authed(db, user_id: int) -> bool:
    try:
        from core.auth import UserZerodhaAuth
        return UserZerodhaAuth.is_authenticated(db, user_id)
    except Exception:
        return False


# ── Stock-list DB helpers (global / shared) ─────────────────────────


def _latest_stock_list(db: Session) -> Strategy10StockList | None:
    return (
        db.query(Strategy10StockList)
        .order_by(Strategy10StockList.uploaded_at.desc())
        .first()
    )


def _load_symbols_from_db(db: Session) -> list[dict]:
    row = _latest_stock_list(db)
    return list(row.symbols) if row and row.symbols else []


def _parse_stock_csv(raw: bytes) -> list[dict]:
    """Parse an uploaded CSV into [{'symbol','exchange'}]. Accepts a header
    with a 'symbol' column (and optional 'exchange'), or a plain
    one-symbol-per-line file."""
    text = raw.decode("utf-8-sig", errors="ignore")
    symbols: list[dict] = []
    seen: set[str] = set()
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return symbols

    header = [h.strip().lower() for h in rows[0]]
    has_header = "symbol" in header
    sym_idx = header.index("symbol") if has_header else 0
    exch_idx = header.index("exchange") if "exchange" in header else None
    data_rows = rows[1:] if has_header else rows

    for row in data_rows:
        if not row:
            continue
        sym = (row[sym_idx] if len(row) > sym_idx else "").strip().upper()
        if not sym or sym.startswith("#") or sym in seen:
            continue
        exch = "NSE"
        if exch_idx is not None and len(row) > exch_idx and row[exch_idx].strip():
            exch = row[exch_idx].strip().upper()
        seen.add(sym)
        symbols.append({"symbol": sym, "exchange": exch})
    return symbols


# ── Strategy instance management ────────────────────────────────────


def _get_strategy(broker: Broker = None, user_id: int = 0) -> Strategy10EquityIntraday:
    if user_id in _user_strategies:
        strat = _user_strategies[user_id]
        if broker and broker._kite is not None:
            strat.broker = broker
        return strat
    if broker is None:
        broker = Broker()
    strat = Strategy10EquityIntraday(broker, _load_config())
    if strat.restore_state():
        logger.info("S10 state restored for user %s: %s", user_id, strat.state.value)
    _user_strategies[user_id] = strat
    return strat


# ── Endpoints ──────────────────────────────────────────────────────


@router.get("/status")
async def get_status(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    return strat.get_status()


@router.get("/stocks")
async def get_stocks(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Current loaded stock list with live LTP for the columnar view.

    If the in-memory strategy has no stocks yet (e.g. fresh server / not
    started today), seed it from the latest uploaded list in Postgres so
    the columnar view shows the watchlist before Start.
    """
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    if not strat.stock_states:
        symbols = _load_symbols_from_db(db)
        if symbols:
            strat.set_symbols(symbols)
    if _is_authed(db, user_id):
        try:
            strat.refresh_quotes()  # LTP + live cumulative volume
        except Exception as exc:
            logger.debug("S10 /stocks quote refresh failed: %s", exc)
    status = strat.get_status()
    row = _latest_stock_list(db)
    return {
        "status": "ok",
        "stocks": status.get("stocks", []),
        "total_stocks": len(strat.stock_states),
        "positions_open": status.get("positions_open", 0),
        "list_filename": row.filename if row else None,
        "list_uploaded_at": row.uploaded_at.isoformat() if row else None,
    }


@router.post("/upload-stocks")
async def upload_stocks(
    file: UploadFile = File(...),
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """Upload the equity stock list CSV (global/shared, latest wins)."""
    raw = await file.read()
    symbols = _parse_stock_csv(raw)
    if not symbols:
        return {"status": "error", "message": "No valid symbols found in CSV"}

    row = Strategy10StockList(
        filename=file.filename,
        symbols=symbols,
        stock_count=len(symbols),
        uploaded_by=user_id,
        uploaded_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()

    # Push into the in-memory strategy immediately
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    strat.set_symbols(symbols)
    if _is_authed(db, user_id):
        try:
            strat.refresh_stocks(symbols)  # recompute levels in background
        except Exception as exc:
            logger.debug("S10 post-upload refresh failed: %s", exc)

    logger.info("S10 stock list uploaded: %s (%d symbols) by user %s",
                file.filename, len(symbols), user_id)
    return {
        "status": "ok",
        "filename": file.filename,
        "stock_count": len(symbols),
        "symbols": symbols,
    }


@router.post("/start")
async def start_strategy(
    config: Strategy10Config,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    params = config.model_dump()
    _save_config(params)
    symbols = _load_symbols_from_db(db)
    broker = get_user_broker(db, user_id)
    strat = Strategy10EquityIntraday(broker, params)
    strat.start(params, symbols=symbols)
    _user_strategies[user_id] = strat
    return strat.get_status()


@router.post("/stop")
async def stop_strategy(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    _get_strategy(broker, user_id).stop()
    return _get_strategy(broker, user_id).get_status()


@router.post("/check")
async def check_strategy(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    if not strat.is_active and not strat.has_open_positions:
        return strat.get_status()
    try:
        return strat.check()
    except Exception as exc:
        logger.error("S10 check failed: %s", exc)
        status = strat.get_status()
        status["error"] = str(exc)
        return status


@router.put("/config")
async def update_config(
    config: Strategy10Config,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    params = config.model_dump()
    _save_config(params)
    strat = _user_strategies.get(user_id)
    if strat is not None:
        strat.apply_config(params)
    return {"status": "updated", "config": params}


@router.post("/refresh-stocks")
async def refresh_stocks(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Reload the latest uploaded list from DB and recompute levels."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated — needs market data access"}
    symbols = _load_symbols_from_db(db)
    strat = _get_strategy(broker, user_id)
    result = strat.refresh_stocks(symbols)
    return {"status": "ok", **result}


# ── Manual equity desk ──────────────────────────────────────────────


@router.post("/manual/order")
async def manual_order(
    order: ManualOrder,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    return strat.add_manual_trade(
        symbol=order.symbol,
        quantity=order.quantity,
        capital=order.capital,
        sl_points=order.sl_points,
        target_points=order.target_points,
        exchange=order.exchange,
    )


@router.post("/manual/modify")
async def manual_modify(
    payload: ManualModify,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    return strat.modify_manual(
        symbol=payload.symbol,
        sl_price=payload.sl_price,
        target_price=payload.target_price,
        sl_points=payload.sl_points,
        target_points=payload.target_points,
    )


@router.post("/manual/exit")
async def manual_exit(
    payload: ManualExit,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    broker = get_user_broker(db, user_id)
    strat = _get_strategy(broker, user_id)
    return strat.exit_manual(payload.symbol)


@router.post("/backtest")
async def run_backtest(
    payload: dict | None = None,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """Backtest the uploaded list on one day (default latest trading day).

    Body (optional): {"date": "YYYY-MM-DD"}
    """
    from datetime import date as _date
    target = None
    if payload and payload.get("date"):
        try:
            target = _date.fromisoformat(payload["date"])
        except Exception:
            return {"status": "error", "message": "Invalid date format (use YYYY-MM-DD)"}

    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated — backtest needs historical data access"}

    symbols = _load_symbols_from_db(db)
    sym_list = [s["symbol"] if isinstance(s, dict) else s for s in symbols]
    # Fresh instance — pass symbols directly so we never touch the live state file
    strat = Strategy10EquityIntraday(broker, _load_config())
    try:
        return strat.backtest(target, symbols=sym_list)
    except Exception as exc:
        logger.error("S10 backtest failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/backtest-multi")
async def run_backtest_multi(
    payload: dict | None = None,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """Aggregated multi-day backtest. Body: {"days": 5} (max 10)."""
    days = 5
    if payload and payload.get("days"):
        try:
            days = max(1, min(int(payload["days"]), 10))
        except Exception:
            return {"status": "error", "message": "Invalid days value"}

    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated — backtest needs historical data access"}

    symbols = _load_symbols_from_db(db)
    sym_list = [s["symbol"] if isinstance(s, dict) else s for s in symbols]
    strat = Strategy10EquityIntraday(broker, _load_config())
    try:
        return strat.backtest_multi(days, symbols=sym_list)
    except Exception as exc:
        logger.error("S10 multi backtest failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.get("/history")
async def get_trade_history(user_id: int = Depends(login_required)):
    file = settings.DATA_DIR / "trade_history" / "strategy10_trades.json"
    if not file.exists():
        return {"trades": []}
    try:
        return {"trades": json.loads(file.read_text())}
    except Exception:
        return {"trades": []}
