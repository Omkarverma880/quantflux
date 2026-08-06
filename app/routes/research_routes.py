"""
API routes for the Research modules (read-only backtest / analytics).

These endpoints never place orders or mutate strategy state — they only read
historical data via the existing per-user Broker. Auth + broker resolution
follow the same pattern as the strategy routes.
"""
from datetime import date as _date

from fastapi import APIRouter, Depends, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.auth import login_required
from core.database import get_db
from core.broker import Broker, get_user_broker
from core.logger import get_logger
from research.vwap_pvwap import VwapPvwapResearch
from research.option_chain import OptionChain
from research.hl_vwap_lab import HlVwapLab
from research.sentiment_engine import SentimentEngine
from research.nifty_sentiment import NiftySentiment
from research.market_pulse import MarketPulse
from research.news_sentiment import NewsSentiment
from research.nifty_signal_generator import NiftySignalGenerator
from research.pmvwap_straddle import PMVwapStraddleResearch
from research.pmvwap_straddle import log as pmvwap_log
from research.pmvwap_equity import PMVwapEquityResearch
from research.pmvwap_equity import log as pmveq_log
from research import pmvwap_report
from research import research_watchlist as rwl
from research.opei import OPEIEngine
from research.opei import log as opei_log
from research.opei import telegram as opei_tg
from research.qmie import QMIEEngine
from research.qmie import store as qmie_store

router = APIRouter()
logger = get_logger("api.research")

# Per-user engine instances (reused so the daily instrument/spot caches persist)
_engines: dict[int, VwapPvwapResearch] = {}
_chains: dict[int, OptionChain] = {}
_labs: dict[int, HlVwapLab] = {}
_sentiments: dict[int, SentimentEngine] = {}
_nifty_sentiments: dict[int, NiftySentiment] = {}
_market_pulses: dict[int, MarketPulse] = {}
_signal_generators: dict[int, NiftySignalGenerator] = {}
_pmvwap_straddles: dict[int, PMVwapStraddleResearch] = {}
_pmvwap_equities: dict[int, PMVwapEquityResearch] = {}
_opei_engines: dict[int, OPEIEngine] = {}
_qmie_engines: dict[int, QMIEEngine] = {}
_news_sentiment = NewsSentiment()   # shared (public RSS — not per-user)


def _get_lab(broker: Broker, user_id: int) -> HlVwapLab:
    lab = _labs.get(user_id)
    if lab is None:
        lab = HlVwapLab(broker)
        _labs[user_id] = lab
    else:
        lab.broker = broker
    return lab


def _get_sentiment(broker: Broker, user_id: int) -> SentimentEngine:
    s = _sentiments.get(user_id)
    if s is None:
        s = SentimentEngine(broker)
        _sentiments[user_id] = s
    else:
        s.broker = broker
        s._chain.broker = broker
    s.user_id = user_id          # bind for durable DB-backed config
    return s


def _get_chain(broker: Broker, user_id: int) -> OptionChain:
    ch = _chains.get(user_id)
    if ch is None:
        ch = OptionChain(broker)
        _chains[user_id] = ch
    else:
        ch.broker = broker
    return ch


def _get_nifty_sentiment(broker: Broker, user_id: int) -> NiftySentiment:
    eng = _nifty_sentiments.get(user_id)
    if eng is None:
        eng = NiftySentiment(broker)
        _nifty_sentiments[user_id] = eng
    else:
        eng.broker = broker
    return eng


def _get_market_pulse(broker: Broker, user_id: int) -> MarketPulse:
    eng = _market_pulses.get(user_id)
    if eng is None:
        eng = MarketPulse(broker)
        _market_pulses[user_id] = eng
    else:
        eng.broker = broker
    return eng


def _get_signal_generator(broker: Broker, user_id: int) -> NiftySignalGenerator:
    eng = _signal_generators.get(user_id)
    if eng is None:
        eng = NiftySignalGenerator(broker, user_id=user_id)
        _signal_generators[user_id] = eng
    else:
        eng.broker = broker
    eng.user_id = user_id
    return eng


def _get_pmvwap_straddle(broker: Broker, user_id: int) -> PMVwapStraddleResearch:
    eng = _pmvwap_straddles.get(user_id)
    if eng is None:
        eng = PMVwapStraddleResearch(broker, user_id=user_id)
        _pmvwap_straddles[user_id] = eng
    else:
        eng.broker = broker
        eng.universe.broker = broker
    eng.user_id = user_id
    return eng


def _get_qmie(broker: Broker, user_id: int) -> QMIEEngine:
    eng = _qmie_engines.get(user_id)
    if eng is None:
        eng = QMIEEngine(broker, user_id=user_id)
        _qmie_engines[user_id] = eng
    else:
        eng.broker = broker
        eng.universe.broker = broker
    eng.user_id = user_id
    return eng


def _get_opei(broker: Broker, user_id: int) -> OPEIEngine:
    eng = _opei_engines.get(user_id)
    if eng is None:
        eng = OPEIEngine(broker, user_id=user_id)
        _opei_engines[user_id] = eng
    else:
        eng.broker = broker
        eng._chain.broker = broker
    eng.user_id = user_id
    return eng


def _get_pmvwap_equity(broker: Broker, user_id: int) -> PMVwapEquityResearch:
    eng = _pmvwap_equities.get(user_id)
    if eng is None:
        eng = PMVwapEquityResearch(broker, user_id=user_id)
        _pmvwap_equities[user_id] = eng
    else:
        eng.broker = broker
        eng.universe.broker = broker
    eng.user_id = user_id
    return eng


def _is_authed(db, user_id: int) -> bool:
    try:
        from core.auth import UserZerodhaAuth
        return UserZerodhaAuth.is_authenticated(db, user_id)
    except Exception:
        return False


def _get_engine(broker: Broker, user_id: int) -> VwapPvwapResearch:
    eng = _engines.get(user_id)
    if eng is None:
        eng = VwapPvwapResearch(broker)
        _engines[user_id] = eng
    else:
        eng.broker = broker  # keep the freshest authenticated broker
    return eng


class RunRequest(BaseModel):
    days: int = 30
    variants: list[str] | None = None
    date: str | None = None  # if set, backtest only this single day (YYYY-MM-DD)
    lots: int | None = None              # qty = 65 × lots
    target_mode: str | None = None       # "points" | "percent" | "double"
    target_points: float | None = None   # used when mode = points
    target_percent: float | None = None  # used when mode = percent
    manage_second_leg: bool | None = None         # control losing-leg loss after 1st target
    leg2_exit_mode: str | None = None             # "points" | "percent"
    leg2_exit_value: float | None = None          # buffer below entry for the 2nd-leg exit


class SignalsRequest(BaseModel):
    date: str | None = None


class ExportRequest(BaseModel):
    start: str | None = None   # YYYY-MM-DD
    end: str | None = None     # YYYY-MM-DD (defaults to start)


class ChainRequest(BaseModel):
    expiry_type: str = "weekly"     # weekly | monthly
    count: int = 15                 # strikes per side
    interval: int = 50              # strike step
    expiry: str | None = None       # optional explicit expiry (YYYY-MM-DD)


class ChainDownloadRequest(BaseModel):
    token: int
    symbol: str
    date: str | None = None         # YYYY-MM-DD (defaults to today)


@router.post("/vwap-pvwap/run")
def run_vwap_pvwap(
    payload: RunRequest | None = None,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """Run the VWAP / previous-day-VWAP backtest across the 4 variants."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error",
                "message": "Zerodha not authenticated — research needs historical data access"}
    payload = payload or RunRequest()
    target = None
    if payload.date:
        try:
            target = _date.fromisoformat(payload.date)
        except Exception:
            return {"status": "error", "message": "Invalid date (use YYYY-MM-DD)"}
    eng = _get_engine(broker, user_id)
    try:
        return eng.run(
            days=payload.days, variant_keys=payload.variants, target_date=target,
            lots=payload.lots, target_mode=payload.target_mode,
            target_points=payload.target_points, target_percent=payload.target_percent,
            manage_second_leg=payload.manage_second_leg,
            leg2_exit_mode=payload.leg2_exit_mode, leg2_exit_value=payload.leg2_exit_value,
        )
    except Exception as exc:
        logger.error("VWAP/PVWAP research run failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/vwap-pvwap/signals")
def vwap_pvwap_signals(
    payload: SignalsRequest | None = None,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """Single-day signal overlay (NIFTY close, running VWAP, prev VWAP, markers)."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    target = None
    if payload and payload.date:
        try:
            target = _date.fromisoformat(payload.date)
        except Exception:
            return {"status": "error", "message": "Invalid date (use YYYY-MM-DD)"}
    eng = _get_engine(broker, user_id)
    try:
        return eng.signals(target)
    except Exception as exc:
        logger.error("VWAP/PVWAP signals failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/vwap-pvwap/export")
def export_vwap_pvwap(
    payload: ExportRequest | None = None,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """Per-minute VWAP / prev-day VWAP / crossover rows for a date range (CSV-able)."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    payload = payload or ExportRequest()
    try:
        start = _date.fromisoformat(payload.start) if payload.start else None
        end = _date.fromisoformat(payload.end) if payload.end else (start)
    except Exception:
        return {"status": "error", "message": "Invalid date (use YYYY-MM-DD)"}
    eng = _get_engine(broker, user_id)
    if start is None:
        start = end = eng._trading_days(1)[-1]
    try:
        return eng.export_vwap(start, end or start)
    except Exception as exc:
        logger.error("VWAP/PVWAP export failed: %s", exc)
        return {"status": "error", "message": str(exc)}


# ──────────────── Option-Chain data + downloader ────────────────

@router.get("/option-chain/expiries")
def option_chain_expiries(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    try:
        return _get_chain(broker, user_id).list_expiries()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/option-chain/snapshot")
def option_chain_snapshot(
    payload: ChainRequest | None = None,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """Live NIFTY option chain around ATM with LTP/OHLC/Vol/OI/VWAP/IV/Greeks."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    payload = payload or ChainRequest()
    count = max(1, min(int(payload.count or 15), 40))
    interval = int(payload.interval or 50)
    try:
        return _get_chain(broker, user_id).snapshot(
            expiry_type=payload.expiry_type, count=count, interval=interval, expiry=payload.expiry)
    except Exception as exc:
        logger.error("Option chain snapshot failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/option-chain/download")
def option_chain_download(
    payload: ChainDownloadRequest,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """1-minute candles (OHLCV + OI + running VWAP) for one strike/side."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    try:
        return _get_chain(broker, user_id).download(payload.token, payload.symbol, payload.date)
    except Exception as exc:
        logger.error("Option chain download failed: %s", exc)
        return {"status": "error", "message": str(exc)}


# ──────────────── HL + VWAP Research Lab ────────────────

@router.get("/hl-vwap/meta")
def hl_vwap_meta(index: str = "NIFTY", user_id: int = Depends(login_required),
                       db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    try:
        return _get_lab(broker, user_id).meta(index)
    except Exception as exc:
        logger.error("HLVWAP meta failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/hl-vwap/run")
def hl_vwap_run(payload: dict, user_id: int = Depends(login_required),
                      db: Session = Depends(get_db)):
    """Run the HL+VWAP research. payload = full params dict (mode zerodha/csv)."""
    broker = get_user_broker(db, user_id)
    if (payload or {}).get("mode") != "csv" and not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated — or use CSV mode"}
    try:
        return _get_lab(broker, user_id).run(payload or {})
    except Exception as exc:
        logger.error("HLVWAP run failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/hl-vwap/chart")
def hl_vwap_chart(payload: dict | None = None, user_id: int = Depends(login_required),
                        db: Session = Depends(get_db)):
    """Switch the chart to a specific day (reuses the last run — no re-fetch)."""
    broker = get_user_broker(db, user_id)
    day = (payload or {}).get("day")
    try:
        return _get_lab(broker, user_id).chart_for_day(day)
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/hl-vwap/upload")
async def hl_vwap_upload(file: UploadFile = File(...), kind: str = Form("spot"),
                         user_id: int = Depends(login_required)):
    """Validate an uploaded CSV (spot/option) and return parsed rows."""
    raw = await file.read()
    try:
        return HlVwapLab.validate_csv(raw, "option" if kind == "option" else "spot")
    except Exception as exc:
        logger.error("HLVWAP upload failed: %s", exc)
        return {"status": "error", "message": str(exc)}


# ──────────────── Sentiment Analyzer ────────────────

@router.post("/sentiment/snapshot")
def sentiment_snapshot(payload: dict | None = None, user_id: int = Depends(login_required),
                             db: Session = Depends(get_db)):
    """Overall market sentiment (macro + derivative + technical)."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    force = bool((payload or {}).get("force"))
    try:
        return _get_sentiment(broker, user_id).snapshot(force=force)
    except Exception as exc:
        logger.error("Sentiment snapshot failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.get("/sentiment/config")
def sentiment_config(user_id: int = Depends(login_required),
                           db: Session = Depends(get_db)):
    """Return the current effective sentiment config (DB-backed, file seed)."""
    try:
        eng = _get_sentiment(get_user_broker(db, user_id), user_id)
        return {"status": "ok", "config": eng.load_config()}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/sentiment/config")
def sentiment_config_save(payload: dict | None = None,
                                user_id: int = Depends(login_required),
                                db: Session = Depends(get_db)):
    """Deep-merge edits into the durable per-user config (Postgres on Railway,
    JSON file locally) and re-run the snapshot. No redeploy required."""
    try:
        eng = _get_sentiment(get_user_broker(db, user_id), user_id)
        cfg = eng.save_config(payload or {})
        snap = eng.snapshot(force=True) if _is_authed(db, user_id) else None
        return {"status": "ok", "config": cfg, "snapshot": snap}
    except Exception as exc:
        logger.error("Sentiment config save failed: %s", exc)
        return {"status": "error", "message": str(exc)}


# ──────────────── NIFTY Sentiment Analyzer (constituents + sectors) ────────────────

class NiftySentimentRequest(BaseModel):
    top_n: int | None = None


@router.post("/nifty-sentiment/snapshot")
def nifty_sentiment_snapshot(
    payload: NiftySentimentRequest | None = None,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """Fast batched-quote market bias: top-stock + sector sentiment cards & tables."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    payload = payload or NiftySentimentRequest()
    try:
        return _get_nifty_sentiment(broker, user_id).snapshot(top_n=payload.top_n)
    except Exception as exc:
        logger.error("NIFTY sentiment snapshot failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/nifty-sentiment/analytics")
def nifty_sentiment_analytics(
    payload: NiftySentimentRequest | None = None,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """Per-stock technicals (5-min vol, 20/200 EMA, VWAP, prev-VWAP, trend).
    Time-boxed + TTL-cached so it never hangs or hammers the rate limit."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    payload = payload or NiftySentimentRequest()
    try:
        return _get_nifty_sentiment(broker, user_id).analytics(top_n=payload.top_n)
    except Exception as exc:
        logger.error("NIFTY sentiment analytics failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.get("/nifty-sentiment/config")
def nifty_sentiment_config(user_id: int = Depends(login_required),
                                 db: Session = Depends(get_db)):
    try:
        eng = _get_nifty_sentiment(get_user_broker(db, user_id), user_id)
        return {"status": "ok", "config": eng.load_config()}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/nifty-sentiment/config")
def nifty_sentiment_config_save(payload: dict | None = None,
                                      user_id: int = Depends(login_required),
                                      db: Session = Depends(get_db)):
    try:
        eng = _get_nifty_sentiment(get_user_broker(db, user_id), user_id)
        return {"status": "ok", "config": eng.save_config(payload or {})}
    except Exception as exc:
        logger.error("NIFTY sentiment config save failed: %s", exc)
        return {"status": "error", "message": str(exc)}


# ──────────────── Market Pulse (dashboard confirmations) ────────────────

@router.post("/market-pulse/snapshot")
def market_pulse_snapshot(user_id: int = Depends(login_required),
                                db: Session = Depends(get_db)):
    """Cumulative-volume, 20/200 DMA, VWAP/P-VWAP, psychological & Gann level
    confirmations for the Market Dashboard."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    try:
        return _get_market_pulse(broker, user_id).snapshot(authenticated=True)
    except Exception as exc:
        logger.error("Market pulse snapshot failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/news-sentiment/snapshot")
def news_sentiment_snapshot(user_id: int = Depends(login_required)):
    """Indian market news sentiment from trusted free RSS feeds (no API key)."""
    try:
        return _news_sentiment.snapshot()
    except Exception as exc:
        logger.error("News sentiment snapshot failed: %s", exc)
        return {"status": "error", "message": str(exc), "bias": "Neutral", "available": False}


# ──────────────── NIFTY Signal Generator (PCR + VWAP, research-only) ────────────────

class SignalGeneratorRequest(BaseModel):
    # Optional per-request overrides (UI can preview without persisting config).
    timeframe: str | None = None
    strike_interval: int | None = None
    strike_count: int | None = None
    market: str | None = None
    expiry_type: str | None = None
    date: str | None = None            # backfill a specific session (YYYY-MM-DD)


@router.post("/nifty-signal-generator/snapshot")
def nifty_signal_generator_snapshot(
    payload: SignalGeneratorRequest | None = None,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """One row per completed candle: summed CE/PE OI → PCR signal + VWAP signal."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated — research needs historical data access"}
    payload = payload or SignalGeneratorRequest()
    overrides = {
        k: v for k, v in {
            "timeframe": payload.timeframe, "strike_interval": payload.strike_interval,
            "strike_count": payload.strike_count, "market": payload.market,
            "expiry_type": payload.expiry_type,
        }.items() if v is not None
    }
    try:
        return _get_signal_generator(broker, user_id).snapshot(overrides, payload.date)
    except Exception as exc:
        logger.error("NIFTY signal generator snapshot failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.get("/nifty-signal-generator/config")
def nifty_signal_generator_config(user_id: int = Depends(login_required),
                                        db: Session = Depends(get_db)):
    try:
        eng = _get_signal_generator(get_user_broker(db, user_id), user_id)
        return {"status": "ok", "config": eng.load_config()}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/nifty-signal-generator/config")
def nifty_signal_generator_config_save(payload: dict | None = None,
                                             user_id: int = Depends(login_required),
                                             db: Session = Depends(get_db)):
    try:
        eng = _get_signal_generator(get_user_broker(db, user_id), user_id)
        return {"status": "ok", "config": eng.save_config(payload or {})}
    except Exception as exc:
        logger.error("NIFTY signal generator config save failed: %s", exc)
        return {"status": "error", "message": str(exc)}


# ──────────────── Prev-Month-VWAP Straddle Research (options, read-only) ────────────────

class PMVwapBacktestRequest(BaseModel):
    overrides: dict | None = None       # per-run config overrides (not persisted)
    symbol: str | None = None           # set → single-stock
    symbols: list[str] | None = None    # set → watchlist (subset of universe)
    start: str | None = None            # YYYY-MM-DD (defaults to latest trading day)
    end: str | None = None              # YYYY-MM-DD (defaults to start)
    persist: bool = True                # append rows to the durable research log


@router.get("/pmvwap-straddle/config")
def pmvwap_straddle_config(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    try:
        return {"status": "ok", "config": _get_pmvwap_straddle(get_user_broker(db, user_id), user_id).load_config()}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/pmvwap-straddle/config")
def pmvwap_straddle_config_save(payload: dict | None = None,
                                      user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    try:
        return {"status": "ok", "config": _get_pmvwap_straddle(get_user_broker(db, user_id), user_id).save_config(payload or {})}
    except Exception as exc:
        logger.error("PMVWAP straddle config save failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.get("/pmvwap-straddle/universe")
def pmvwap_straddle_universe(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    try:
        return _get_pmvwap_straddle(broker, user_id).list_universe()
    except Exception as exc:
        logger.error("PMVWAP straddle universe failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/pmvwap-straddle/backtest")
def pmvwap_straddle_backtest(payload: PMVwapBacktestRequest | None = None,
                                   user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Run the straddle backtest (single stock or whole F&O universe) and append
    the resulting research-log rows to the durable, append-only log."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated — research needs historical data"}
    payload = payload or PMVwapBacktestRequest()
    try:
        eng = _get_pmvwap_straddle(broker, user_id)
        res = eng.backtest(payload.overrides, symbol=payload.symbol, symbols=payload.symbols,
                           start=payload.start, end=payload.end)
        if res.get("status") == "ok" and payload.persist and res.get("rows"):
            run_id = pmvwap_log.new_run_id()
            stored = pmvwap_log.persist_rows(db, user_id, run_id, res["mode"], res["rows"])
            res["run_id"] = run_id
            res["stored"] = stored
        return res
    except Exception as exc:
        logger.error("PMVWAP straddle backtest failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.get("/pmvwap-straddle/runs")
def pmvwap_straddle_runs(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    try:
        return {"status": "ok", "runs": pmvwap_log.list_runs(db, user_id)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/pmvwap-straddle/log")
def pmvwap_straddle_log_rows(run_id: str | None = None, date: str | None = None,
                                   user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    try:
        return {"status": "ok", "rows": pmvwap_log.fetch_rows(db, user_id, run_id, date)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/pmvwap-straddle/scan")
def pmvwap_straddle_scan(payload: dict | None = None,
                               user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Live-day scan: run today's backtest and APPEND only new signals to a
    stable per-day live run (rows keep accumulating; no duplicates)."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    today = _date.today().isoformat()
    run_id = f"live_{today}"
    try:
        eng = _get_pmvwap_straddle(broker, user_id)
        eng._opt_cache.clear()          # today's premiums move — refetch fresh
        res = eng.backtest((payload or {}).get("overrides"),
                           symbol=(payload or {}).get("symbol"), symbols=(payload or {}).get("symbols"),
                           start=today, end=today)
        if res.get("status") == "ok":
            res["stored_new"] = pmvwap_log.persist_new(db, user_id, run_id, "live", res.get("rows") or [])
            res["run_id"] = run_id
        return res
    except Exception as exc:
        logger.error("PMVWAP straddle scan failed: %s", exc)
        return {"status": "error", "message": str(exc)}


# ──────────────── Prev-Month-VWAP Equity-Holding Research (cash, read-only) ────────────────

class PMVEqBacktestRequest(BaseModel):
    overrides: dict | None = None
    symbol: str | None = None
    symbols: list[str] | None = None
    start: str | None = None
    end: str | None = None
    persist: bool = True


@router.get("/pmvwap-equity/config")
def pmvwap_equity_config(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    try:
        return {"status": "ok", "config": _get_pmvwap_equity(get_user_broker(db, user_id), user_id).load_config()}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/pmvwap-equity/config")
def pmvwap_equity_config_save(payload: dict | None = None,
                                    user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    try:
        return {"status": "ok", "config": _get_pmvwap_equity(get_user_broker(db, user_id), user_id).save_config(payload or {})}
    except Exception as exc:
        logger.error("PMVWAP equity config save failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.get("/pmvwap-equity/universe")
def pmvwap_equity_universe(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    try:
        return _get_pmvwap_equity(broker, user_id).list_universe()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/pmvwap-equity/backtest")
def pmvwap_equity_backtest(payload: PMVEqBacktestRequest | None = None,
                                 user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated — research needs historical data"}
    payload = payload or PMVEqBacktestRequest()
    try:
        eng = _get_pmvwap_equity(broker, user_id)
        res = eng.backtest(payload.overrides, symbol=payload.symbol, symbols=payload.symbols,
                           start=payload.start, end=payload.end)
        if res.get("status") == "ok" and payload.persist and res.get("rows"):
            run_id = pmveq_log.new_run_id()
            res["run_id"] = run_id
            res["stored"] = pmveq_log.persist_rows(db, user_id, run_id, res["mode"], res["rows"])
        return res
    except Exception as exc:
        logger.error("PMVWAP equity backtest failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.get("/pmvwap-equity/runs")
def pmvwap_equity_runs(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    try:
        return {"status": "ok", "runs": pmveq_log.list_runs(db, user_id)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/pmvwap-equity/log")
def pmvwap_equity_log_rows(run_id: str | None = None, date: str | None = None,
                                 user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    try:
        return {"status": "ok", "rows": pmveq_log.fetch_rows(db, user_id, run_id, date)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/pmvwap-equity/scan")
def pmvwap_equity_scan(payload: dict | None = None,
                             user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Live-day scan: run today's backtest and APPEND only new holding signals."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    today = _date.today().isoformat()
    run_id = f"live_{today}"
    try:
        eng = _get_pmvwap_equity(broker, user_id)
        eng._ul_cache.clear()           # today's candles grow — refetch fresh
        res = eng.backtest((payload or {}).get("overrides"),
                           symbol=(payload or {}).get("symbol"), symbols=(payload or {}).get("symbols"),
                           start=today, end=today)
        if res.get("status") == "ok":
            fresh = pmveq_log.persist_new(db, user_id, run_id, "live", res.get("rows") or [])
            res["stored_new"] = len(fresh)
            res["run_id"] = run_id
            # Telegram: one message per genuinely-new live signal.
            cfg = res.get("config") or {}
            if fresh and cfg.get("telegram_alerts"):
                from core import notify as _notify
                if _notify.enabled():
                    for r in fresh:
                        _notify.send(
                            "📄 <b>RESEARCH · Prev-Month VWAP Equity</b> (Live)\n\n"
                            f"Stock: <b>{r.get('underlying')}</b>\n"
                            f"Signal: {r.get('time')}\n"
                            f"Entry: ₹{r.get('entry_price')} · Qty {r.get('qty')}\n"
                            f"Target: ₹{r.get('target_price')}\n"
                            f"Prev-Month VWAP (purple): ₹{r.get('prev_month_vwap')}\n"
                            f"Prev-Week VWAP (green): ₹{r.get('prev_week_vwap')}")
        return res
    except Exception as exc:
        logger.error("PMVWAP equity scan failed: %s", exc)
        return {"status": "error", "message": str(exc)}


# ──────────────── Research Summary Report + run comparison ────────────────

def _sector_map(db, user_id: int) -> dict:
    """Best-effort underlying → sector map (large-cap constituents + per-user
    overrides). Unmapped names fall back to 'Unknown' inside the report."""
    smap: dict = {}
    try:
        from research.nifty_sentiment import DEFAULT_CONSTITUENTS
        for c in DEFAULT_CONSTITUENTS:
            smap[c["symbol"]] = c.get("sector", "Unknown")
    except Exception:
        pass
    try:
        from core.models import SectorOverride
        for o in db.query(SectorOverride).filter(SectorOverride.user_id == user_id).all():
            if getattr(o, "tradingsymbol", None) and getattr(o, "sector", None):
                smap[o.tradingsymbol] = o.sector
    except Exception:
        pass
    return smap


_nifty_token_cache: dict = {}


def _nifty_benchmark(broker, rows: list[dict]):
    """NIFTY 50 buy-and-hold return over the run's date span (for the report)."""
    dates = [r.get("exit_date") or r.get("date") for r in rows if (r.get("exit_date") or r.get("date"))]
    if not dates:
        return None
    start, end = min(dates), max(dates)
    try:
        from datetime import datetime as _dt
        tok = _nifty_token_cache.get("t")
        if not tok:
            for inst in broker.get_instruments("NSE"):
                if inst.get("tradingsymbol") == "NIFTY 50":
                    tok = int(inst["instrument_token"])
                    _nifty_token_cache["t"] = tok
                    break
        if not tok:
            return None
        frm = _dt.fromisoformat(f"{start}T09:15:00")
        to = _dt.fromisoformat(f"{end}T15:30:00")
        candles = broker.get_historical_data(tok, frm, to, "day") or []
        if len(candles) < 2:
            return None
        first = float(candles[0]["close"])
        last = float(candles[-1]["close"])
        if first <= 0:
            return None
        return {"label": "NIFTY 50 (buy & hold)", "return_pct": round((last - first) / first * 100.0, 2),
                "start": start, "end": end, "start_close": round(first, 2), "end_close": round(last, 2)}
    except Exception as exc:
        logger.warning("benchmark failed: %s", exc)
        return None


class PMVReportRequest(BaseModel):
    run_id: str | None = None
    date: str | None = None


class PMVCompareRequest(BaseModel):
    run_a: str
    run_b: str


@router.post("/pmvwap-straddle/report")
def pmvwap_straddle_report(payload: PMVReportRequest, user_id: int = Depends(login_required),
                                 db: Session = Depends(get_db)):
    try:
        rows = pmvwap_log.fetch_rows(db, user_id, payload.run_id, payload.date)
        report = pmvwap_report.build_report(rows, mtm_key="combined_mtm", sector_map=_sector_map(db, user_id))
        if _is_authed(db, user_id):
            report["benchmark"] = _nifty_benchmark(get_user_broker(db, user_id), rows)
        return {"status": "ok", "count": len(rows), "report": report}
    except Exception as exc:
        logger.error("PMVWAP straddle report failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/pmvwap-straddle/compare")
def pmvwap_straddle_compare(payload: PMVCompareRequest, user_id: int = Depends(login_required),
                                  db: Session = Depends(get_db)):
    try:
        a = pmvwap_log.fetch_rows(db, user_id, payload.run_a)
        b = pmvwap_log.fetch_rows(db, user_id, payload.run_b)
        return {"status": "ok", "comparison": pmvwap_report.compare(
            a, b, mtm_key="combined_mtm", label_a=payload.run_a[:8], label_b=payload.run_b[:8])}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/pmvwap-equity/report")
def pmvwap_equity_report(payload: PMVReportRequest, user_id: int = Depends(login_required),
                               db: Session = Depends(get_db)):
    try:
        rows = pmveq_log.fetch_rows(db, user_id, payload.run_id, payload.date)
        report = pmvwap_report.build_report(rows, mtm_key="mtm", sector_map=_sector_map(db, user_id))
        if _is_authed(db, user_id):
            report["benchmark"] = _nifty_benchmark(get_user_broker(db, user_id), rows)
        return {"status": "ok", "count": len(rows), "report": report}
    except Exception as exc:
        logger.error("PMVWAP equity report failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/pmvwap-equity/compare")
def pmvwap_equity_compare(payload: PMVCompareRequest, user_id: int = Depends(login_required),
                                db: Session = Depends(get_db)):
    try:
        a = pmveq_log.fetch_rows(db, user_id, payload.run_a)
        b = pmveq_log.fetch_rows(db, user_id, payload.run_b)
        return {"status": "ok", "comparison": pmvwap_report.compare(
            a, b, mtm_key="mtm", label_a=payload.run_a[:8], label_b=payload.run_b[:8])}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


# ──────────────── Research Watchlists (shared by Straddle #7 & Equity #8) ────────────────

class WatchlistCreate(BaseModel):
    name: str
    symbols: list[str] | None = None


class WatchlistUpdate(BaseModel):
    name: str | None = None
    symbols: list[str] | None = None


class WatchlistSymbols(BaseModel):
    add: list[str] | None = None
    remove: list[str] | None = None


@router.get("/symbol-search")
def symbol_search(q: str = "", exchange: str = "ALL", limit: int = 20,
                        user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Type-ahead over the full NSE + BSE cash-equity universe (single-stock
    search — not limited to F&O or watchlists)."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    try:
        uni = _get_pmvwap_straddle(broker, user_id).universe
        return {"status": "ok",
                "results": uni.search_equities(q, (exchange or "ALL").upper(), min(50, max(1, int(limit))))}
    except Exception as exc:
        logger.error("symbol search failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.get("/watchlists")
def watchlists_list(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    try:
        return {"status": "ok", "watchlists": rwl.list_watchlists(db, user_id)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/watchlists")
def watchlists_create(payload: WatchlistCreate, user_id: int = Depends(login_required),
                            db: Session = Depends(get_db)):
    try:
        return {"status": "ok", "watchlist": rwl.create_watchlist(db, user_id, payload.name, payload.symbols)}
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    except Exception as exc:
        logger.error("watchlist create failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.get("/watchlists/{wid}")
def watchlists_get(wid: int, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    w = rwl.get_watchlist(db, user_id, wid)
    return {"status": "ok", "watchlist": w} if w else {"status": "error", "message": "Watchlist not found"}


@router.put("/watchlists/{wid}")
def watchlists_update(wid: int, payload: WatchlistUpdate, user_id: int = Depends(login_required),
                            db: Session = Depends(get_db)):
    try:
        w = rwl.update_watchlist(db, user_id, wid, payload.name, payload.symbols)
        return {"status": "ok", "watchlist": w} if w else {"status": "error", "message": "Watchlist not found"}
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/watchlists/{wid}/symbols")
def watchlists_modify(wid: int, payload: WatchlistSymbols, user_id: int = Depends(login_required),
                            db: Session = Depends(get_db)):
    w = rwl.modify_symbols(db, user_id, wid, payload.add, payload.remove)
    return {"status": "ok", "watchlist": w} if w else {"status": "error", "message": "Watchlist not found"}


@router.delete("/watchlists/{wid}")
def watchlists_delete(wid: int, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    return {"status": "ok", "deleted": rwl.delete_watchlist(db, user_id, wid)}


@router.post("/watchlists/upload")
async def watchlists_upload(file: UploadFile = File(...), name: str = Form(...),
                            user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Create a watchlist from an uploaded TXT/CSV, or replace an existing
    same-named list's symbols (the download → edit → upload flow)."""
    try:
        raw = await file.read()
        text = raw.decode("utf-8", errors="ignore")
        return {"status": "ok", "watchlist": rwl.upsert_from_upload(db, user_id, name, text)}
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    except Exception as exc:
        logger.error("watchlist upload failed: %s", exc)
        return {"status": "error", "message": str(exc)}


# ──────────────── Option Premium Entry Intelligence Engine (OPEI) ────────────────

class OPEISnapshotRequest(BaseModel):
    strike: str | None = None
    timeframe: str | None = None
    expiry_type: str | None = None


@router.post("/opei/snapshot")
def opei_snapshot(payload: OPEISnapshotRequest | None = None,
                        user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Live premium-expansion scores + top entry levels for the selected CE/PE."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    payload = payload or OPEISnapshotRequest()
    overrides = {k: v for k, v in {
        "strike": payload.strike, "timeframe": payload.timeframe, "expiry_type": payload.expiry_type,
    }.items() if v is not None}
    try:
        eng = _get_opei(broker, user_id)
        snap = eng.snapshot(overrides)
        if snap.get("status") == "ok":
            from datetime import datetime as _dt
            _now = _dt.now()
            # Track how already-logged levels are actually performing (points/MFE/MAE).
            for _side in ("CE", "PE"):
                _sd = snap["sides"].get(_side) or {}
                opei_log.update_outcomes(db, user_id, _side, _sd.get("premium"), _now)
            recs = eng.institutional_recs(snap)
            if recs:
                # log the qualifying best levels; collect only the NEWLY-logged
                # ones so Telegram fires once per level (not every 3s refresh).
                new_recs = []
                for side in ("CE", "PE"):
                    sd = snap["sides"].get(side) or {}
                    side_recs = [r for r in recs if r["side"] == side]
                    if side_recs:
                        new_recs += opei_log.log_recommendations(
                            db, user_id, side, sd.get("symbol"), sd.get("strike"),
                            sd.get("premium"), side_recs, snap["fetched_at"])
                # Telegram: universal creds if enabled, else OPEI's own (fallback).
                cfg = snap["config"]
                if new_recs and cfg.get("alert_on_institutional"):
                    from core import notify as _notify
                    msgs = [opei_tg.format_entry({**r, "time": snap["fetched_at"]}) for r in new_recs]
                    if _notify.enabled():
                        for m in msgs:
                            _notify.send(m)
                    elif cfg.get("telegram_enabled") and cfg.get("telegram_bot_token") and cfg.get("telegram_chat_id"):
                        for m in msgs:
                            opei_tg.send_message(cfg["telegram_bot_token"], cfg["telegram_chat_id"], m)
        return snap
    except Exception as exc:
        logger.error("OPEI snapshot failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.get("/opei/config")
def opei_config(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    try:
        return {"status": "ok", "config": _get_opei(get_user_broker(db, user_id), user_id).load_config()}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/opei/config")
def opei_config_save(payload: dict | None = None,
                           user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    try:
        cfg = _get_opei(get_user_broker(db, user_id), user_id).save_config(payload or {})
        # Mirror OPEI's Telegram creds into the UNIVERSAL config so every module
        # (Research-8 live, Equity strategy, OPEI) shares them — wherever set.
        if any(k in (payload or {}) for k in ("telegram_enabled", "telegram_bot_token", "telegram_chat_id")):
            from core import notify as _n
            _n.save_config({"enabled": cfg.get("telegram_enabled"),
                            "bot_token": cfg.get("telegram_bot_token"),
                            "chat_id": cfg.get("telegram_chat_id")})
        return {"status": "ok", "config": cfg}
    except Exception as exc:
        logger.error("OPEI config save failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/opei/telegram-test")
def opei_telegram_test(payload: dict | None = None, user_id: int = Depends(login_required)):
    p = payload or {}
    return opei_tg.test_connection(p.get("bot_token", ""), p.get("chat_id", ""))


@router.get("/opei/log")
def opei_log_rows(date: str | None = None, user_id: int = Depends(login_required),
                        db: Session = Depends(get_db)):
    try:
        return {"status": "ok", "rows": opei_log.fetch_log(db, user_id, date)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


# ──────────── Research-10 · Quantum Market Intelligence Engine (QMIE) ────────────
# READ-ONLY opportunity ranking. These endpoints never place, modify, cancel, or
# simulate an order; the engine imports no execution client.

class QMIEScanRequest(BaseModel):
    overrides: dict | None = None
    symbols: list[str] | None = None       # optional explicit universe (e.g. a watchlist)


@router.post("/qmie/scan")
def qmie_scan(payload: QMIEScanRequest | None = None,
              user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Ranked research candidates for the selected horizon (read-only)."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated — research needs historical data access"}
    payload = payload or QMIEScanRequest()
    try:
        snap = _get_qmie(broker, user_id).scan(payload.overrides, symbols=payload.symbols)
        if snap.get("status") == "ok":
            qmie_store.save_snapshot(db, user_id, snap)   # persist for reproducibility
        return snap
    except Exception as exc:
        logger.error("QMIE scan failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.post("/qmie/backtest")
def qmie_backtest(payload: QMIEScanRequest | None = None,
                  user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """Leakage-safe point-in-time backtest + calibration (read-only)."""
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    payload = payload or QMIEScanRequest()
    try:
        return _get_qmie(broker, user_id).backtest(payload.overrides, symbols=payload.symbols)
    except Exception as exc:
        logger.error("QMIE backtest failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.get("/qmie/snapshots")
def qmie_snapshots(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    try:
        return {"status": "ok", "snapshots": qmie_store.list_snapshots(db, user_id)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.get("/qmie/snapshot/{snapshot_id}")
def qmie_snapshot(snapshot_id: str, user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    snap = qmie_store.get_snapshot(db, user_id, snapshot_id)
    if not snap:
        return {"status": "error", "message": "Snapshot not found"}
    return snap


@router.get("/qmie/config")
def qmie_config(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    try:
        return {"status": "ok", "config": _get_qmie(get_user_broker(db, user_id), user_id).load_config()}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/qmie/config")
def qmie_config_save(payload: dict | None = None,
                     user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    try:
        return {"status": "ok", "config": _get_qmie(get_user_broker(db, user_id), user_id).save_config(payload or {})}
    except Exception as exc:
        logger.error("QMIE config save failed: %s", exc)
        return {"status": "error", "message": str(exc)}


@router.get("/qmie/universe")
def qmie_universe(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    """F&O underlyings available for the default universe (read-only, for display)."""
    try:
        eng = _get_qmie(get_user_broker(db, user_id), user_id)
        return {"status": "ok", "symbols": [e["name"] for e in eng.universe.equities()]}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
