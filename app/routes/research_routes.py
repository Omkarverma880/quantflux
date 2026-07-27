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
async def run_vwap_pvwap(
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
async def vwap_pvwap_signals(
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
async def export_vwap_pvwap(
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
async def option_chain_expiries(user_id: int = Depends(login_required), db: Session = Depends(get_db)):
    broker = get_user_broker(db, user_id)
    if not _is_authed(db, user_id):
        return {"status": "error", "message": "Zerodha not authenticated"}
    try:
        return _get_chain(broker, user_id).list_expiries()
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/option-chain/snapshot")
async def option_chain_snapshot(
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
async def option_chain_download(
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
async def hl_vwap_meta(index: str = "NIFTY", user_id: int = Depends(login_required),
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
async def hl_vwap_run(payload: dict, user_id: int = Depends(login_required),
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
async def hl_vwap_chart(payload: dict | None = None, user_id: int = Depends(login_required),
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
async def sentiment_snapshot(payload: dict | None = None, user_id: int = Depends(login_required),
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
async def sentiment_config(user_id: int = Depends(login_required),
                           db: Session = Depends(get_db)):
    """Return the current effective sentiment config (DB-backed, file seed)."""
    try:
        eng = _get_sentiment(get_user_broker(db, user_id), user_id)
        return {"status": "ok", "config": eng.load_config()}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/sentiment/config")
async def sentiment_config_save(payload: dict | None = None,
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
async def nifty_sentiment_snapshot(
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
async def nifty_sentiment_analytics(
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
async def nifty_sentiment_config(user_id: int = Depends(login_required),
                                 db: Session = Depends(get_db)):
    try:
        eng = _get_nifty_sentiment(get_user_broker(db, user_id), user_id)
        return {"status": "ok", "config": eng.load_config()}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/nifty-sentiment/config")
async def nifty_sentiment_config_save(payload: dict | None = None,
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
async def market_pulse_snapshot(user_id: int = Depends(login_required),
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
async def news_sentiment_snapshot(user_id: int = Depends(login_required)):
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
async def nifty_signal_generator_snapshot(
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
async def nifty_signal_generator_config(user_id: int = Depends(login_required),
                                        db: Session = Depends(get_db)):
    try:
        eng = _get_signal_generator(get_user_broker(db, user_id), user_id)
        return {"status": "ok", "config": eng.load_config()}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/nifty-signal-generator/config")
async def nifty_signal_generator_config_save(payload: dict | None = None,
                                             user_id: int = Depends(login_required),
                                             db: Session = Depends(get_db)):
    try:
        eng = _get_signal_generator(get_user_broker(db, user_id), user_id)
        return {"status": "ok", "config": eng.save_config(payload or {})}
    except Exception as exc:
        logger.error("NIFTY signal generator config save failed: %s", exc)
        return {"status": "error", "message": str(exc)}
