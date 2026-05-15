"""
Portfolio Analytics — independent module.

Strictly read-only with respect to existing trading flows. This router
exposes:
  • GET  /holdings              — live holdings from Kite + computed metrics
  • GET  /watchlists            — list user's watchlists (with quotes)
  • POST /watchlists            — create a watchlist
  • DELETE /watchlists/{id}     — delete a watchlist
  • POST /watchlists/{id}/items — add a stock to a watchlist
  • DELETE /watchlists/items/{item_id}
  • GET  /research              — research entries (with proximity flags)
  • POST /research              — create research entry
  • PUT  /research/{id}         — update
  • DELETE /research/{id}
  • GET  /holdings/exit-levels  — get all exit-level overlays
  • PUT  /holdings/exit-levels  — upsert one exit level
  • DELETE /holdings/exit-levels/{symbol}

Holdings themselves are never persisted; they are always pulled fresh
from Zerodha. This guarantees the Portfolio Analytics module cannot
drift from the broker's state and never interferes with intraday or
strategy logic.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.auth import login_required
from core.broker import Broker, get_user_broker
from core.database import get_db
from core.logger import get_logger
from core.models import (
    HoldingExitLevel,
    ResearchEntry,
    SectorOverride,
    Watchlist,
    WatchlistItem,
)

router = APIRouter()
logger = get_logger("api.portfolio")


# ─── Static sector lookup ─────────────────────────────────────────────
# Lightweight, dependency-free mapping for the most common NSE blue-chip
# tickers. Anything not in the map is bucketed as "Others" — this keeps
# the module standalone (no extra data files / network calls).
SECTOR_MAP: dict[str, str] = {
    # Banks
    "HDFCBANK": "Banking", "ICICIBANK": "Banking", "AXISBANK": "Banking",
    "KOTAKBANK": "Banking", "SBIN": "Banking", "INDUSINDBK": "Banking",
    "BANKBARODA": "Banking", "PNB": "Banking", "FEDERALBNK": "Banking",
    "IDFCFIRSTB": "Banking", "AUBANK": "Banking",
    # NBFC / Financials
    "BAJFINANCE": "Financials", "BAJAJFINSV": "Financials",
    "HDFCLIFE": "Financials", "SBILIFE": "Financials",
    "ICICIPRULI": "Financials", "ICICIGI": "Financials",
    "CHOLAFIN": "Financials", "MUTHOOTFIN": "Financials",
    "MFSL": "Financials", "LICI": "Financials", "PFC": "Financials",
    "RECLTD": "Financials",
    # IT
    "TCS": "IT", "INFY": "IT", "WIPRO": "IT", "HCLTECH": "IT",
    "TECHM": "IT", "LTIM": "IT", "MPHASIS": "IT", "PERSISTENT": "IT",
    "COFORGE": "IT", "OFSS": "IT",
    # Energy / Oil & Gas
    "RELIANCE": "Energy", "ONGC": "Energy", "BPCL": "Energy",
    "IOC": "Energy", "GAIL": "Energy", "HINDPETRO": "Energy",
    "ADANIGREEN": "Energy", "TATAPOWER": "Energy", "NTPC": "Energy",
    "POWERGRID": "Energy", "ADANIPOWER": "Energy",
    # FMCG
    "HINDUNILVR": "FMCG", "ITC": "FMCG", "NESTLEIND": "FMCG",
    "BRITANNIA": "FMCG", "DABUR": "FMCG", "MARICO": "FMCG",
    "GODREJCP": "FMCG", "COLPAL": "FMCG", "VBL": "FMCG",
    "TATACONSUM": "FMCG",
    # Auto
    "MARUTI": "Auto", "TATAMOTORS": "Auto", "M&M": "Auto",
    "BAJAJ-AUTO": "Auto", "HEROMOTOCO": "Auto", "EICHERMOT": "Auto",
    "TVSMOTOR": "Auto", "ASHOKLEY": "Auto",
    # Pharma
    "SUNPHARMA": "Pharma", "DRREDDY": "Pharma", "CIPLA": "Pharma",
    "DIVISLAB": "Pharma", "LUPIN": "Pharma", "TORNTPHARM": "Pharma",
    "AUROPHARMA": "Pharma", "ZYDUSLIFE": "Pharma", "APOLLOHOSP": "Pharma",
    # Metals
    "TATASTEEL": "Metals", "JSWSTEEL": "Metals", "HINDALCO": "Metals",
    "VEDL": "Metals", "COALINDIA": "Metals", "NMDC": "Metals",
    "SAIL": "Metals", "JINDALSTEL": "Metals",
    # Cement
    "ULTRACEMCO": "Cement", "SHREECEM": "Cement", "ACC": "Cement",
    "AMBUJACEM": "Cement", "DALBHARAT": "Cement",
    # Telecom
    "BHARTIARTL": "Telecom", "IDEA": "Telecom",
    # Infra / Construction
    "LT": "Infrastructure", "ADANIPORTS": "Infrastructure",
    "ADANIENT": "Infrastructure", "DLF": "Realty", "GODREJPROP": "Realty",
    "OBEROIRLTY": "Realty",
    # Consumer / Retail
    "TITAN": "Consumer", "ASIANPAINT": "Consumer", "PIDILITIND": "Consumer",
    "BERGEPAINT": "Consumer", "TRENT": "Consumer", "DMART": "Retail",
    "PAGEIND": "Consumer",
    # Chemicals / Fertilizers
    "UPL": "Chemicals", "PIIND": "Chemicals", "SRF": "Chemicals",
    "DEEPAKNTR": "Chemicals", "AARTIIND": "Chemicals",
    # Capital markets / Fintech / Exchanges
    "GROWW": "Capital Markets", "NSDL": "Capital Markets",
    "CDSL": "Capital Markets", "BSE": "Capital Markets",
    "MCX": "Capital Markets", "ANGELONE": "Capital Markets",
    "CAMS": "Capital Markets", "KFINTECH": "Capital Markets",
    "POLICYBZR": "Fintech", "PAYTM": "Fintech",
    # New-age consumer / Internet
    "ZOMATO": "Consumer Internet", "ETERNAL": "Consumer Internet",
    "SWIGGY": "Consumer Internet", "NYKAA": "Consumer Internet",
    "FSNECOM": "Consumer Internet",
    # Pharma / Healthcare additions
    "SAILIFE": "Pharma", "MANKIND": "Pharma", "GLENMARK": "Pharma",
    "BIOCON": "Pharma", "LAURUSLABS": "Pharma", "ALKEM": "Pharma",
    # Defence / EMS
    "HAL": "Defence", "BEL": "Defence", "MAZDOCK": "Defence",
    "BDL": "Defence", "DYNAMATECH": "Defence",
    "DIXON": "Electronics", "KAYNES": "Electronics", "CYIENTDLM": "Electronics",
    "SYRMA": "Electronics", "AMBER": "Electronics",
    # Logistics / Hospitality misc
    "IRCTC": "Travel", "INDIGO": "Aviation", "INDHOTEL": "Hospitality",
    # Misc additions seen in user holdings
    "ELLEN": "Industrials",
}


def _sector_for(symbol: str, overrides: Optional[dict[str, str]] = None) -> str:
    """Resolve sector: user override → static map → 'Others'."""
    if not symbol:
        return "Others"
    s = symbol.upper().strip()
    if overrides and s in overrides:
        return overrides[s]
    return SECTOR_MAP.get(s, "Others")


def _load_sector_overrides(db: Session, user_id: int) -> dict[str, str]:
    rows = db.execute(
        select(SectorOverride).where(SectorOverride.user_id == user_id)
    ).scalars().all()
    return {r.tradingsymbol.upper(): r.sector for r in rows}


def _decimal(v) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


# ─── Pydantic schemas ─────────────────────────────────────────────────

class WatchlistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class WatchlistItemCreate(BaseModel):
    tradingsymbol: str = Field(min_length=1, max_length=60)
    exchange: str = Field(default="NSE", max_length=10)
    note: str = Field(default="", max_length=255)


class ResearchEntryIn(BaseModel):
    tradingsymbol: str = Field(min_length=1, max_length=60)
    exchange: str = Field(default="NSE", max_length=10)
    entry_level: float = Field(gt=0)
    target_level: float = Field(gt=0)
    stop_level: Optional[float] = None
    proximity_pct: float = Field(default=1.0, ge=0.05, le=20)
    note: str = Field(default="", max_length=500)


class ExitLevelIn(BaseModel):
    tradingsymbol: str = Field(min_length=1, max_length=60)
    exchange: str = Field(default="NSE", max_length=10)
    exit_level: float = Field(gt=0)
    proximity_pct: float = Field(default=1.0, ge=0.05, le=20)
    note: str = Field(default="", max_length=255)


class SectorOverrideIn(BaseModel):
    tradingsymbol: str = Field(min_length=1, max_length=60)
    sector: str = Field(min_length=1, max_length=60)


# ─── Helpers ──────────────────────────────────────────────────────────

def _bulk_ltp(broker: Broker, instruments: list[str]) -> dict[str, float]:
    """LTP fetch with graceful failure — never raise back to the caller."""
    if not instruments:
        return {}
    try:
        return broker.get_ltp(instruments)
    except Exception as e:
        logger.warning(f"portfolio: bulk LTP fetch failed: {e}")
        return {}


def _instrument_key(exchange: str, tradingsymbol: str) -> str:
    return f"{(exchange or 'NSE').upper()}:{tradingsymbol.upper()}"


# ─── Endpoints: Holdings ──────────────────────────────────────────────

@router.get("/holdings")
async def get_holdings(
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """Return the user's Zerodha holdings + analytics overlay.

    Holdings are pulled fresh from Kite every call. Exit-level overlays
    come from `holding_exit_levels` and proximity is computed server-side.
    """
    try:
        broker = get_user_broker(db, user_id)
        holdings_raw = broker.get_holdings()
    except Exception as e:
        logger.warning(f"portfolio: get_holdings failed for user {user_id}: {e}")
        return {
            "holdings": [], "summary": _empty_summary(),
            "sector_allocation": [], "top_gainer": None, "top_loser": None,
            "error": str(e),
        }

    # Fetch user's exit-level overlays in one shot
    exit_rows = db.execute(
        select(HoldingExitLevel).where(HoldingExitLevel.user_id == user_id)
    ).scalars().all()
    exit_map: dict[str, HoldingExitLevel] = {
        f"{(r.exchange or 'NSE').upper()}:{r.tradingsymbol.upper()}": r
        for r in exit_rows
    }
    sector_overrides = _load_sector_overrides(db, user_id)

    # Kite's holdings endpoint returns `last_price` that can lag by minutes
    # (and stays stuck at previous-day close until the first tick). Refresh
    # with a single batched LTP call so the Portfolio Analytics page shows
    # the same live price as the broker terminal.
    live_ltp = _bulk_ltp(
        broker,
        [_instrument_key(getattr(h, "exchange", "NSE") or "NSE", h.tradingsymbol)
         for h in holdings_raw],
    )

    holdings: list[dict] = []
    total_invested = 0.0
    total_current = 0.0

    for h in holdings_raw:
        sym = h.tradingsymbol
        exch = getattr(h, "exchange", "NSE") or "NSE"
        qty = float(h.quantity or 0)
        avg = float(h.average_price or 0)
        ltp = float(live_ltp.get(_instrument_key(exch, sym)) or h.last_price or 0)
        invested = qty * avg
        current = qty * ltp
        pnl = current - invested
        pnl_pct = (pnl / invested * 100) if invested > 0 else 0.0

        total_invested += invested
        total_current += current

        key = _instrument_key(exch, sym)
        ex_row = exit_map.get(key)
        exit_level = float(ex_row.exit_level) if ex_row else None
        exit_prox_pct = float(ex_row.proximity_pct) if ex_row else None
        near_exit = False
        if exit_level and exit_prox_pct and ltp > 0:
            band = exit_level * (exit_prox_pct / 100.0)
            near_exit = abs(ltp - exit_level) <= band

        holdings.append({
            "tradingsymbol": sym,
            "exchange": exch,
            "sector": _sector_for(sym, sector_overrides),
            "quantity": qty,
            "average_price": avg,
            "last_price": ltp,
            "invested": round(invested, 2),
            "current_value": round(current, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 3),
            "exit_level": exit_level,
            "exit_proximity_pct": exit_prox_pct,
            "near_exit": near_exit,
        })

    # Allocation %
    for h in holdings:
        h["allocation_pct"] = (
            round(h["current_value"] / total_current * 100, 2)
            if total_current > 0 else 0.0
        )

    # Top gainer / loser by absolute P&L
    top_gainer = max(holdings, key=lambda x: x["pnl"], default=None)
    top_loser = min(holdings, key=lambda x: x["pnl"], default=None)
    # If both are the same (all flat) suppress
    if top_gainer and top_loser and top_gainer is top_loser:
        top_loser = None

    # Sector allocation aggregate
    sector_agg: dict[str, dict] = {}
    for h in holdings:
        s = h["sector"]
        a = sector_agg.setdefault(s, {"sector": s, "current_value": 0.0, "invested": 0.0, "pnl": 0.0, "count": 0})
        a["current_value"] += h["current_value"]
        a["invested"] += h["invested"]
        a["pnl"] += h["pnl"]
        a["count"] += 1
    sector_allocation = []
    for a in sector_agg.values():
        a["allocation_pct"] = round(a["current_value"] / total_current * 100, 2) if total_current > 0 else 0.0
        a["current_value"] = round(a["current_value"], 2)
        a["invested"] = round(a["invested"], 2)
        a["pnl"] = round(a["pnl"], 2)
        sector_allocation.append(a)
    sector_allocation.sort(key=lambda x: x["current_value"], reverse=True)

    summary = {
        "total_invested": round(total_invested, 2),
        "total_current": round(total_current, 2),
        "total_pnl": round(total_current - total_invested, 2),
        "total_pnl_pct": round(
            ((total_current - total_invested) / total_invested * 100) if total_invested > 0 else 0.0,
            3,
        ),
        "holdings_count": len(holdings),
    }

    return {
        "holdings": holdings,
        "summary": summary,
        "sector_allocation": sector_allocation,
        "top_gainer": top_gainer,
        "top_loser": top_loser,
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _empty_summary() -> dict:
    return {
        "total_invested": 0, "total_current": 0,
        "total_pnl": 0, "total_pnl_pct": 0, "holdings_count": 0,
    }


# ─── Endpoints: Holding exit levels ───────────────────────────────────

@router.get("/holdings/exit-levels")
async def list_exit_levels(
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(HoldingExitLevel).where(HoldingExitLevel.user_id == user_id)
    ).scalars().all()
    return [
        {
            "id": r.id,
            "tradingsymbol": r.tradingsymbol,
            "exchange": r.exchange,
            "exit_level": _decimal(r.exit_level),
            "proximity_pct": _decimal(r.proximity_pct),
            "note": r.note or "",
        }
        for r in rows
    ]


@router.put("/holdings/exit-levels")
async def upsert_exit_level(
    body: ExitLevelIn,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    sym = body.tradingsymbol.upper().strip()
    exch = (body.exchange or "NSE").upper().strip()
    row = db.execute(
        select(HoldingExitLevel).where(
            HoldingExitLevel.user_id == user_id,
            HoldingExitLevel.tradingsymbol == sym,
            HoldingExitLevel.exchange == exch,
        )
    ).scalar_one_or_none()
    if row is None:
        row = HoldingExitLevel(
            user_id=user_id, tradingsymbol=sym, exchange=exch,
            exit_level=body.exit_level, proximity_pct=body.proximity_pct,
            note=body.note,
        )
        db.add(row)
    else:
        row.exit_level = body.exit_level
        row.proximity_pct = body.proximity_pct
        row.note = body.note
    db.commit()
    return {"status": "ok", "id": row.id}


@router.delete("/holdings/exit-levels/{symbol}")
async def delete_exit_level(
    symbol: str,
    exchange: str = "NSE",
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    sym = symbol.upper().strip()
    exch = (exchange or "NSE").upper().strip()
    row = db.execute(
        select(HoldingExitLevel).where(
            HoldingExitLevel.user_id == user_id,
            HoldingExitLevel.tradingsymbol == sym,
            HoldingExitLevel.exchange == exch,
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(row)
    db.commit()
    return {"status": "deleted"}


# ─── Endpoints: Watchlists ────────────────────────────────────────────

@router.get("/watchlists")
async def list_watchlists(
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """Return all watchlists for the user with live LTPs."""
    wls = db.execute(
        select(Watchlist).where(Watchlist.user_id == user_id).order_by(Watchlist.created_at.asc())
    ).scalars().all()

    # Bulk LTP fetch across every item in every watchlist
    instruments: list[str] = []
    for wl in wls:
        for it in wl.items:
            instruments.append(_instrument_key(it.exchange, it.tradingsymbol))
    instruments = list(dict.fromkeys(instruments))  # dedupe, preserve order

    ltp_map: dict[str, float] = {}
    if instruments:
        try:
            broker = get_user_broker(db, user_id)
            ltp_map = _bulk_ltp(broker, instruments)
        except Exception as e:
            logger.debug(f"watchlist LTP fetch skipped: {e}")

    sector_overrides = _load_sector_overrides(db, user_id)
    out = []
    for wl in wls:
        items = []
        for it in wl.items:
            key = _instrument_key(it.exchange, it.tradingsymbol)
            ltp = float(ltp_map.get(key, 0) or 0)
            items.append({
                "id": it.id,
                "tradingsymbol": it.tradingsymbol,
                "exchange": it.exchange,
                "note": it.note or "",
                "last_price": ltp,
                "sector": _sector_for(it.tradingsymbol, sector_overrides),
            })
        out.append({
            "id": wl.id,
            "name": wl.name,
            "items": items,
            "created_at": wl.created_at.isoformat() if wl.created_at else None,
        })
    return out


@router.post("/watchlists")
async def create_watchlist(
    body: WatchlistCreate,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    existing = db.execute(
        select(Watchlist).where(Watchlist.user_id == user_id, Watchlist.name == name)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Watchlist with this name already exists")
    wl = Watchlist(user_id=user_id, name=name)
    db.add(wl)
    db.commit()
    db.refresh(wl)
    return {"id": wl.id, "name": wl.name, "items": []}


@router.delete("/watchlists/{wl_id}")
async def delete_watchlist(
    wl_id: int,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    wl = db.execute(
        select(Watchlist).where(Watchlist.id == wl_id, Watchlist.user_id == user_id)
    ).scalar_one_or_none()
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    db.delete(wl)
    db.commit()
    return {"status": "deleted"}


@router.post("/watchlists/{wl_id}/items")
async def add_watchlist_item(
    wl_id: int,
    body: WatchlistItemCreate,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    wl = db.execute(
        select(Watchlist).where(Watchlist.id == wl_id, Watchlist.user_id == user_id)
    ).scalar_one_or_none()
    if not wl:
        raise HTTPException(status_code=404, detail="Watchlist not found")

    sym = body.tradingsymbol.upper().strip()
    exch = (body.exchange or "NSE").upper().strip()
    existing = db.execute(
        select(WatchlistItem).where(
            WatchlistItem.watchlist_id == wl_id,
            WatchlistItem.tradingsymbol == sym,
            WatchlistItem.exchange == exch,
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Symbol already in watchlist")

    item = WatchlistItem(
        watchlist_id=wl_id, tradingsymbol=sym, exchange=exch, note=body.note,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return {
        "id": item.id, "tradingsymbol": item.tradingsymbol,
        "exchange": item.exchange, "note": item.note or "",
    }


@router.delete("/watchlists/items/{item_id}")
async def delete_watchlist_item(
    item_id: int,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    # Join through Watchlist to ensure the item belongs to the user
    item = db.execute(
        select(WatchlistItem).join(Watchlist, WatchlistItem.watchlist_id == Watchlist.id)
        .where(WatchlistItem.id == item_id, Watchlist.user_id == user_id)
    ).scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(item)
    db.commit()
    return {"status": "deleted"}


# ─── Endpoints: Research entries ──────────────────────────────────────

@router.get("/research")
async def list_research(
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(ResearchEntry).where(ResearchEntry.user_id == user_id)
        .order_by(ResearchEntry.created_at.desc())
    ).scalars().all()

    # Fetch live LTPs in bulk for proximity calculation
    instruments = list({_instrument_key(r.exchange, r.tradingsymbol) for r in rows})
    ltp_map: dict[str, float] = {}
    if instruments:
        try:
            broker = get_user_broker(db, user_id)
            ltp_map = _bulk_ltp(broker, instruments)
        except Exception as e:
            logger.debug(f"research LTP fetch skipped: {e}")

    sector_overrides = _load_sector_overrides(db, user_id)
    out = []
    for r in rows:
        key = _instrument_key(r.exchange, r.tradingsymbol)
        ltp = float(ltp_map.get(key, 0) or 0)
        entry = float(r.entry_level or 0)
        target = float(r.target_level or 0)
        stop = float(r.stop_level) if r.stop_level is not None else None
        prox_pct = float(r.proximity_pct or 1.0)

        near_entry = False
        near_target = False
        near_stop = False
        if ltp > 0:
            if entry > 0 and abs(ltp - entry) <= entry * (prox_pct / 100.0):
                near_entry = True
            if target > 0 and abs(ltp - target) <= target * (prox_pct / 100.0):
                near_target = True
            if stop and stop > 0 and abs(ltp - stop) <= stop * (prox_pct / 100.0):
                near_stop = True

        out.append({
            "id": r.id,
            "tradingsymbol": r.tradingsymbol,
            "exchange": r.exchange,
            "entry_level": entry,
            "target_level": target,
            "stop_level": stop,
            "proximity_pct": prox_pct,
            "note": r.note or "",
            "last_price": ltp,
            "near_entry": near_entry,
            "near_target": near_target,
            "near_stop": near_stop,
            "sector": _sector_for(r.tradingsymbol, sector_overrides),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return out


@router.post("/research")
async def create_research(
    body: ResearchEntryIn,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    row = ResearchEntry(
        user_id=user_id,
        tradingsymbol=body.tradingsymbol.upper().strip(),
        exchange=(body.exchange or "NSE").upper().strip(),
        entry_level=body.entry_level,
        target_level=body.target_level,
        stop_level=body.stop_level,
        proximity_pct=body.proximity_pct,
        note=body.note,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id}


@router.put("/research/{entry_id}")
async def update_research(
    entry_id: int,
    body: ResearchEntryIn,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    row = db.execute(
        select(ResearchEntry).where(
            ResearchEntry.id == entry_id, ResearchEntry.user_id == user_id
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    row.tradingsymbol = body.tradingsymbol.upper().strip()
    row.exchange = (body.exchange or "NSE").upper().strip()
    row.entry_level = body.entry_level
    row.target_level = body.target_level
    row.stop_level = body.stop_level
    row.proximity_pct = body.proximity_pct
    row.note = body.note
    db.commit()
    return {"status": "ok"}


@router.delete("/research/{entry_id}")
async def delete_research(
    entry_id: int,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    row = db.execute(
        select(ResearchEntry).where(
            ResearchEntry.id == entry_id, ResearchEntry.user_id == user_id
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(row)
    db.commit()
    return {"status": "deleted"}


# ─── Quick LTP probe (used for inline "Add to watchlist" UX) ──────────

@router.get("/quote")
async def quote_symbol(
    symbol: str,
    exchange: str = "NSE",
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    sym = symbol.upper().strip()
    exch = (exchange or "NSE").upper().strip()
    try:
        broker = get_user_broker(db, user_id)
        ltp = _bulk_ltp(broker, [_instrument_key(exch, sym)])
        price = float(next(iter(ltp.values()), 0) or 0)
        return {"tradingsymbol": sym, "exchange": exch, "last_price": price}
    except Exception as e:
        return {"tradingsymbol": sym, "exchange": exch, "last_price": 0, "error": str(e)}


# ─── Endpoints: Sector overrides ─────────────────────────────────

# Suggested sector list shown to the UI when classifying a stock.
SUGGESTED_SECTORS: list[str] = [
    "Banking", "Financials", "IT", "Energy", "FMCG", "Auto", "Pharma",
    "Metals", "Cement", "Telecom", "Infrastructure", "Realty",
    "Consumer", "Retail", "Chemicals", "Capital Goods", "Media",
    "Defence", "Logistics", "Insurance", "Textiles", "Agriculture",
    "Hospitality", "Healthcare", "Others",
]


@router.get("/sectors")
async def list_sector_overrides(
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """Return all user sector overrides + suggestion list + the static map."""
    rows = db.execute(
        select(SectorOverride).where(SectorOverride.user_id == user_id)
    ).scalars().all()
    return {
        "overrides": [
            {"tradingsymbol": r.tradingsymbol, "sector": r.sector}
            for r in rows
        ],
        "suggestions": SUGGESTED_SECTORS,
    }


@router.put("/sectors")
async def upsert_sector_override(
    body: SectorOverrideIn,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    sym = body.tradingsymbol.upper().strip()
    sector = body.sector.strip() or "Others"
    row = db.execute(
        select(SectorOverride).where(
            SectorOverride.user_id == user_id,
            SectorOverride.tradingsymbol == sym,
        )
    ).scalar_one_or_none()
    if row is None:
        row = SectorOverride(user_id=user_id, tradingsymbol=sym, sector=sector)
        db.add(row)
    else:
        row.sector = sector
    db.commit()
    return {"status": "ok", "tradingsymbol": sym, "sector": sector}


@router.delete("/sectors/{symbol}")
async def delete_sector_override(
    symbol: str,
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    sym = symbol.upper().strip()
    row = db.execute(
        select(SectorOverride).where(
            SectorOverride.user_id == user_id,
            SectorOverride.tradingsymbol == sym,
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(row)
    db.commit()
    return {"status": "deleted"}


# ─── Analytics World ─────────────────────────────────────────────────
#
# A real-data swing/positional screener. Pulls live quotes for a
# curated NSE universe and tags each stock with derived signals
# (breakout, momentum, swing setup, recent IPO). The frontend
# `AnalyticsWorld` page consumes this single endpoint.
#
# Notes:
#   • Kite has no native screener / market-cap / IPO endpoint, so
#     the stock universe is curated. Each entry carries `tags` that
#     map it to one or more of the four UI sections.
#   • Live quote (LTP, day OHLC, day volume, circuit limits, prev
#     close) comes from a single batched `kite.quote()` call.
#   • 20-day high/low and 5-day momentum come from `kite.historical`
#     fetched in parallel and cached per trading day.
#   • All responses are cached for 60s per user.

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as _date, timedelta

# Curated NSE universe. `tags` drives section placement; static data
# (sector, listing_year, mcap_cr) is best-effort and only used as
# display/filter hints — never as a source of truth for prices.
_AW_UNIVERSE: list[dict] = [
    # Recent IPOs (listed in the last ~24 months)
    {"sym": "TATATECH",   "sector": "IT",            "listing_year": 2023, "mcap_cr": 36000,  "tags": ["ipo"]},
    {"sym": "IREDA",      "sector": "Financials",    "listing_year": 2023, "mcap_cr": 45000,  "tags": ["ipo", "momentum"]},
    {"sym": "JIOFIN",     "sector": "Financials",    "listing_year": 2023, "mcap_cr": 180000, "tags": ["ipo", "swing"]},
    {"sym": "MANKIND",    "sector": "Pharma",        "listing_year": 2023, "mcap_cr": 95000,  "tags": ["ipo"]},
    {"sym": "CELLO",      "sector": "Consumer",      "listing_year": 2023, "mcap_cr": 16000,  "tags": ["ipo"]},
    {"sym": "BAJAJHFL",   "sector": "Financials",    "listing_year": 2024, "mcap_cr": 75000,  "tags": ["ipo", "swing"]},
    {"sym": "OLAELEC",    "sector": "Auto",          "listing_year": 2024, "mcap_cr": 30000,  "tags": ["ipo"]},
    {"sym": "FIRSTCRY",   "sector": "Retail",        "listing_year": 2024, "mcap_cr": 27000,  "tags": ["ipo"]},
    {"sym": "PREMIERENE", "sector": "Energy",        "listing_year": 2024, "mcap_cr": 45000,  "tags": ["ipo", "momentum"]},
    {"sym": "HYUNDAI",    "sector": "Auto",          "listing_year": 2024, "mcap_cr": 150000, "tags": ["ipo"]},
    {"sym": "NTPCGREEN",  "sector": "Energy",        "listing_year": 2024, "mcap_cr": 90000,  "tags": ["ipo", "swing"]},
    {"sym": "SWIGGY",     "sector": "Consumer",      "listing_year": 2024, "mcap_cr": 110000, "tags": ["ipo"]},

    # Swing / positional candidates
    {"sym": "SUZLON",     "sector": "Energy",        "mcap_cr": 70000,  "tags": ["swing", "momentum"]},
    {"sym": "IDEA",       "sector": "Telecom",       "mcap_cr": 50000,  "tags": ["swing"]},
    {"sym": "YESBANK",    "sector": "Banking",       "mcap_cr": 60000,  "tags": ["swing"]},
    {"sym": "IDFCFIRSTB", "sector": "Banking",       "mcap_cr": 50000,  "tags": ["swing"]},
    {"sym": "TATAPOWER",  "sector": "Energy",        "mcap_cr": 120000, "tags": ["swing", "breakout"]},
    {"sym": "PFC",        "sector": "Financials",    "mcap_cr": 150000, "tags": ["swing", "momentum"]},
    {"sym": "RECLTD",     "sector": "Financials",    "mcap_cr": 140000, "tags": ["swing", "momentum"]},
    {"sym": "IRFC",       "sector": "Financials",    "mcap_cr": 180000, "tags": ["swing"]},
    {"sym": "BEL",        "sector": "Defence",       "mcap_cr": 220000, "tags": ["swing", "momentum"]},
    {"sym": "HAL",        "sector": "Defence",       "mcap_cr": 280000, "tags": ["swing", "momentum"]},

    # Momentum / breakout candidates
    {"sym": "ADANIPOWER", "sector": "Energy",        "mcap_cr": 220000, "tags": ["momentum", "breakout"]},
    {"sym": "ADANIENT",   "sector": "Infrastructure","mcap_cr": 300000, "tags": ["breakout"]},
    {"sym": "TRENT",      "sector": "Retail",        "mcap_cr": 220000, "tags": ["momentum", "breakout"]},
    {"sym": "DIXON",      "sector": "Consumer",      "mcap_cr": 90000,  "tags": ["momentum", "breakout"]},
    {"sym": "POLYCAB",    "sector": "Capital Goods", "mcap_cr": 100000, "tags": ["breakout"]},
    {"sym": "CDSL",       "sector": "Financials",    "mcap_cr": 35000,  "tags": ["breakout", "momentum"]},
    {"sym": "BSE",        "sector": "Financials",    "mcap_cr": 65000,  "tags": ["momentum", "breakout"]},
    {"sym": "MAZDOCK",    "sector": "Defence",       "mcap_cr": 110000, "tags": ["momentum", "breakout"]},
    {"sym": "COCHINSHIP", "sector": "Defence",       "mcap_cr": 50000,  "tags": ["momentum", "breakout"]},
    {"sym": "RVNL",       "sector": "Infrastructure","mcap_cr": 100000, "tags": ["momentum"]},
    {"sym": "IRCON",      "sector": "Infrastructure","mcap_cr": 25000,  "tags": ["swing", "momentum"]},
    {"sym": "ZOMATO",     "sector": "Consumer",      "mcap_cr": 250000, "tags": ["breakout", "momentum"]},
]

# Module-level caches
_AW_RESPONSE_CACHE: dict[int, tuple[float, dict]] = {}
_AW_HIST_CACHE: dict[str, tuple[str, dict]] = {}
_AW_TOKEN_CACHE: dict[str, tuple[str, dict]] = {"_": ("", {})}
_AW_RESPONSE_TTL = 60  # seconds


def _aw_load_tokens(broker: Broker) -> dict[str, int]:
    today = _date.today().isoformat()
    cached_day, cached_map = _AW_TOKEN_CACHE["_"]
    if cached_day == today and cached_map:
        return cached_map
    try:
        rows = broker.get_instruments("NSE")
    except Exception as e:
        logger.warning(f"analytics-world: instruments fetch failed: {e}")
        return cached_map or {}
    wanted = {u["sym"] for u in _AW_UNIVERSE}
    token_map: dict[str, int] = {}
    for r in rows:
        ts = r.get("tradingsymbol")
        if ts in wanted and r.get("instrument_type") == "EQ":
            token_map[ts] = int(r["instrument_token"])
    _AW_TOKEN_CACHE["_"] = (today, token_map)
    return token_map


def _aw_compute_hist_metrics(candles: list[dict]) -> dict:
    if not candles or len(candles) < 5:
        return {}
    last20 = candles[-20:] if len(candles) >= 20 else candles
    h20 = max(float(c["high"]) for c in last20)
    l20 = min(float(c["low"]) for c in last20)
    avgvol20 = sum(float(c["volume"]) for c in last20) / max(1, len(last20))

    last5 = candles[-5:]
    up_days = 0
    for i in range(1, len(last5)):
        if float(last5[i]["close"]) > float(last5[i - 1]["close"]):
            up_days += 1

    closes = [float(c["close"]) for c in candles]
    ret5 = 0.0
    if len(closes) >= 6:
        ret5 = (closes[-1] - closes[-6]) / closes[-6] * 100.0

    range_high = max(float(c["high"]) for c in candles)
    range_low = min(float(c["low"]) for c in candles)

    # Sparkline data — last 25 closes, rounded for payload size
    spark = [round(c, 2) for c in closes[-25:]]

    return {
        "high_20d": round(h20, 2),
        "low_20d": round(l20, 2),
        "avg_vol_20d": round(avgvol20, 0),
        "up_days_5d": up_days,
        "return_5d_pct": round(ret5, 2),
        "range_high": round(range_high, 2),
        "range_low": round(range_low, 2),
        "closes": spark,
    }


def _aw_fetch_hist_for(broker: Broker, symbol: str, token: int) -> dict:
    today = _date.today().isoformat()
    cached_day, cached = _AW_HIST_CACHE.get(symbol, ("", {}))
    if cached_day == today and cached:
        return cached
    try:
        to_dt = _date.today()
        from_dt = to_dt - timedelta(days=60)
        candles = broker.get_historical_data(token, from_dt, to_dt, "day")
        metrics = _aw_compute_hist_metrics(candles or [])
        _AW_HIST_CACHE[symbol] = (today, metrics)
        return metrics
    except Exception as e:
        logger.debug(f"analytics-world: hist fetch failed for {symbol}: {e}")
        return cached or {}


def _aw_score_with_reasons(item: dict) -> tuple[int, list[dict]]:
    """Compute verdict score + human-readable reasons.

    Each reason: {label, delta, sign('+'|'-')}.
    """
    score = 0
    reasons: list[dict] = []

    def add(delta: int, label: str):
        nonlocal score
        score += delta
        reasons.append({"label": label, "delta": delta, "sign": "+" if delta > 0 else "-"})

    if item.get("breakout"):
        add(2, "Breakout above 20-day high")
    if item.get("near_breakout"):
        add(1, "Within 2% of 20-day high")
    if item.get("volume_surge"):
        add(1, "Volume ≥1.5× 20-day average")

    ud = item.get("up_days_5d") or 0
    if ud >= 4:
        add(2, f"{ud} of last 5 sessions up")
    elif ud == 3:
        add(1, "3 of last 5 sessions up")

    chg = item.get("change_pct") or 0
    if chg > 2:
        add(1, f"Intraday change +{chg:.2f}%")
    elif chg < -2:
        add(-2, f"Intraday change {chg:.2f}%")

    r5 = item.get("return_5d_pct") or 0
    if r5 > 5:
        add(1, f"5-day return +{r5:.2f}%")
    elif r5 < -5:
        add(-1, f"5-day return {r5:.2f}%")

    return score, reasons


def _aw_verdict_from_score(score: int) -> str:
    if score >= 5:
        return "Strong Buy"
    if score >= 3:
        return "Buy"
    if score >= 1:
        return "Watch"
    return "Avoid"


def _aw_build_item(u: dict, quote: dict, hist: dict) -> dict:
    last = float(quote.get("last_price") or 0)
    ohlc = quote.get("ohlc") or {}
    prev_close = float(ohlc.get("close") or 0)
    day_open = float(ohlc.get("open") or 0)
    day_high = float(ohlc.get("high") or 0)
    day_low = float(ohlc.get("low") or 0)
    volume = float(quote.get("volume") or quote.get("volume_traded") or 0)
    circuit = quote.get("circuit_limits") or {}

    change_pct = ((last - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0

    high_20d = hist.get("high_20d") or day_high or last
    low_20d = hist.get("low_20d") or day_low or last
    avg_vol_20d = hist.get("avg_vol_20d") or 0.0
    up_days_5d = hist.get("up_days_5d") or 0
    return_5d_pct = hist.get("return_5d_pct") or 0.0

    support = round(low_20d, 2)
    resistance = round(high_20d, 2)

    breakout = bool(last > 0 and high_20d > 0 and last >= high_20d * 0.999)
    near_breakout = bool(
        last > 0 and high_20d > 0 and last >= high_20d * 0.98 and not breakout
    )
    volume_surge = bool(
        volume > 0 and avg_vol_20d > 0 and volume >= avg_vol_20d * 1.5
    )

    if breakout:
        entry = round(last, 2)
        stop = round(max(support, last * 0.96), 2)
        target = round(last + (last - stop) * 2.0, 2)
    elif near_breakout:
        entry = round(resistance * 1.002, 2)
        stop = round(max(support, last * 0.96), 2)
        target = round(resistance + (resistance - stop) * 1.5, 2)
    else:
        entry = round(last, 2)
        stop = round(max(support, last * 0.95), 2)
        target = round(last + (last - stop) * 1.8, 2)

    rr_denom = max(0.01, entry - stop)

    item = {
        "symbol": u["sym"],
        "exchange": "NSE",
        "sector": u.get("sector") or "Others",
        "tags": u.get("tags") or [],
        "listing_year": u.get("listing_year"),
        "market_cap_cr": u.get("mcap_cr"),
        "last_price": round(last, 2),
        "prev_close": round(prev_close, 2),
        "day_open": round(day_open, 2),
        "day_high": round(day_high, 2),
        "day_low": round(day_low, 2),
        "change_pct": round(change_pct, 2),
        "volume": int(volume),
        "avg_vol_20d": int(avg_vol_20d) if avg_vol_20d else 0,
        "support": support,
        "resistance": resistance,
        "high_20d": resistance,
        "low_20d": support,
        "range_high": hist.get("range_high"),
        "range_low": hist.get("range_low"),
        "up_days_5d": up_days_5d,
        "return_5d_pct": return_5d_pct,
        "breakout": breakout,
        "near_breakout": near_breakout,
        "volume_surge": volume_surge,
        "circuit_lower": float(circuit.get("lower") or 0) or None,
        "circuit_upper": float(circuit.get("upper") or 0) or None,
        "entry": entry,
        "stop": stop,
        "target": target,
        "risk_reward": round((target - entry) / rr_denom, 2),
        "closes": hist.get("closes") or [],
    }
    score, reasons = _aw_score_with_reasons(item)
    item["verdict"] = _aw_verdict_from_score(score)
    item["verdict_score"] = score
    item["reasons"] = reasons
    return item


def _aw_classify(items: list[dict]) -> dict:
    ipos: list[dict] = []
    swing: list[dict] = []
    momentum: list[dict] = []
    breakouts: list[dict] = []

    for it in items:
        tags = set(it.get("tags") or [])
        if "ipo" in tags and it["last_price"] and it["last_price"] <= 1500:
            ipos.append(it)
        if (
            "swing" in tags
            and 50 <= it["last_price"] <= 2500
            and (it["return_5d_pct"] or 0) >= -3
        ):
            swing.append(it)
        if (
            (it["up_days_5d"] or 0) >= 4
            or (it["return_5d_pct"] or 0) >= 5
            or ("momentum" in tags and (it["change_pct"] or 0) > 0)
        ):
            momentum.append(it)
        if it["breakout"] or (it["near_breakout"] and it["volume_surge"]):
            breakouts.append(it)

    ipos.sort(key=lambda x: (x["change_pct"], x["return_5d_pct"]), reverse=True)
    swing.sort(key=lambda x: (x["return_5d_pct"], x["up_days_5d"]), reverse=True)
    momentum.sort(key=lambda x: (x["up_days_5d"], x["return_5d_pct"]), reverse=True)
    breakouts.sort(key=lambda x: (x["breakout"], x["change_pct"]), reverse=True)

    return {
        "ipos": ipos[:12],
        "swing": swing[:12],
        "momentum": momentum[:12],
        "breakouts": breakouts[:12],
    }


def _aw_market_status() -> dict:
    """Best-effort NSE session status using server clock (assumed IST)."""
    now = datetime.now()
    weekday = now.weekday()
    minutes = now.hour * 60 + now.minute
    if weekday >= 5:
        status = "closed"
    elif minutes < 9 * 60:
        status = "closed"
    elif minutes < 9 * 60 + 15:
        status = "pre_open"
    elif minutes <= 15 * 60 + 30:
        status = "open"
    elif minutes <= 16 * 60:
        status = "post_close"
    else:
        status = "closed"
    return {"status": status, "server_time": now.strftime("%Y-%m-%d %H:%M:%S")}


def _aw_sector_heat(items: list[dict]) -> list[dict]:
    """Average % change per sector across the universe (today)."""
    agg: dict[str, dict] = {}
    for it in items:
        sec = it.get("sector") or "Others"
        a = agg.setdefault(sec, {"sum": 0.0, "count": 0, "positives": 0})
        a["sum"] += float(it.get("change_pct") or 0)
        a["count"] += 1
        if (it.get("change_pct") or 0) >= 0:
            a["positives"] += 1
    out: list[dict] = []
    for sec, a in agg.items():
        avg = a["sum"] / max(1, a["count"])
        out.append({
            "sector": sec,
            "avg_change_pct": round(avg, 2),
            "count": a["count"],
            "breadth_pct": round(a["positives"] / max(1, a["count"]) * 100, 0),
        })
    out.sort(key=lambda x: x["avg_change_pct"], reverse=True)
    return out


@router.get("/analytics-world")
async def get_analytics_world(
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """Real-data screener feeding the Analytics World page."""
    now = time.time()
    cached = _AW_RESPONSE_CACHE.get(user_id)
    if cached and (now - cached[0] < _AW_RESPONSE_TTL):
        return cached[1]

    try:
        broker = get_user_broker(db, user_id)
    except Exception as e:
        logger.warning(f"analytics-world: broker init failed for user {user_id}: {e}")
        return {
            "sections": {"ipos": [], "swing": [], "momentum": [], "breakouts": []},
            "summary": {},
            "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "error": "broker_unavailable",
        }

    symbols = [_instrument_key("NSE", u["sym"]) for u in _AW_UNIVERSE]
    try:
        quote_map = broker.get_quote(symbols) or {}
    except Exception as e:
        logger.warning(f"analytics-world: quote fetch failed for user {user_id}: {e}")
        quote_map = {}

    token_map = _aw_load_tokens(broker)
    hist_map: dict[str, dict] = {}

    def _job(sym: str) -> tuple[str, dict]:
        tok = token_map.get(sym)
        if not tok:
            return sym, {}
        return sym, _aw_fetch_hist_for(broker, sym, tok)

    needed = [u["sym"] for u in _AW_UNIVERSE if token_map.get(u["sym"])]
    if needed:
        try:
            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = [pool.submit(_job, s) for s in needed]
                for f in as_completed(futures):
                    sym, metrics = f.result()
                    hist_map[sym] = metrics
        except Exception as e:
            logger.warning(f"analytics-world: hist pool failed: {e}")

    items: list[dict] = []
    for u in _AW_UNIVERSE:
        key = _instrument_key("NSE", u["sym"])
        q = quote_map.get(key) or {}
        h = hist_map.get(u["sym"], {})
        if not q.get("last_price"):
            continue
        items.append(_aw_build_item(u, q, h))

    sections = _aw_classify(items)
    summary = {
        "universe_size": len(_AW_UNIVERSE),
        "live_count": len(items),
        "breakouts": len(sections["breakouts"]),
        "momentum": len(sections["momentum"]),
        "swing": len(sections["swing"]),
        "ipos": len(sections["ipos"]),
    }
    payload = {
        "sections": sections,
        "summary": summary,
        "sector_heat": _aw_sector_heat(items),
        "market_status": _aw_market_status(),
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _AW_RESPONSE_CACHE[user_id] = (now, payload)
    return payload


# ─── Holdings Insights ─────────────────────────────────────────
#
# Per-holding signal + risk analysis. Reuses Analytics World
# historical helpers so each Zerodha holding is scored on the
# same 20-day high/low framework.

_HI_RESPONSE_CACHE: dict[int, tuple[float, dict]] = {}
_HI_RESPONSE_TTL = 60  # seconds


def _hi_signal_for(last: float, hist: dict, change_pct: float) -> tuple[str, list[str]]:
    """Classify a holding's technical state.

    Returns (signal, notes). Signal values:
      strong_uptrend / breakout / neutral / weak / breakdown
    """
    notes: list[str] = []
    if not hist or not last:
        return "neutral", notes
    h20 = hist.get("high_20d") or 0
    l20 = hist.get("low_20d") or 0
    r5 = hist.get("return_5d_pct") or 0
    ud = hist.get("up_days_5d") or 0

    if h20 and last >= h20 * 0.999:
        notes.append("Trading at/above 20-day high")
        sig = "breakout"
    elif l20 and last <= l20 * 1.005:
        notes.append("Trading at/near 20-day low")
        sig = "breakdown"
    elif r5 >= 5 and ud >= 3:
        notes.append(f"5-day return +{r5:.1f}% with {ud}/4 up days")
        sig = "strong_uptrend"
    elif r5 <= -5:
        notes.append(f"5-day return {r5:.1f}%")
        sig = "weak"
    else:
        sig = "neutral"
    if change_pct >= 3:
        notes.append(f"Intraday +{change_pct:.2f}%")
    elif change_pct <= -3:
        notes.append(f"Intraday {change_pct:.2f}%")
    return sig, notes


def _hi_action_for(signal: str, pnl_pct: float, near_exit: bool) -> str:
    """Translate a signal + P&L into a portfolio action verdict."""
    if near_exit:
        return "Trim / Exit"
    if signal == "breakdown":
        return "Trim / Exit"
    if signal == "weak" and pnl_pct < -5:
        return "Trim"
    if signal == "breakout" and pnl_pct >= 0:
        return "Buy More"
    if signal == "strong_uptrend" and pnl_pct >= 0:
        return "Buy More"
    if signal in ("breakout", "strong_uptrend"):
        return "Hold"
    if signal == "weak":
        return "Hold"
    return "Hold"


@router.get("/holdings/insights")
async def get_holdings_insights(
    user_id: int = Depends(login_required),
    db: Session = Depends(get_db),
):
    """Per-holding signal + risk analysis + concentration warnings.

    Reuses Analytics World historical cache so this is cheap on
    repeat calls.
    """
    now = time.time()
    cached = _HI_RESPONSE_CACHE.get(user_id)
    if cached and (now - cached[0] < _HI_RESPONSE_TTL):
        return cached[1]

    try:
        broker = get_user_broker(db, user_id)
        raw = broker.get_holdings()
    except Exception as e:
        logger.warning(f"holdings/insights: failed for user {user_id}: {e}")
        return {
            "holdings": [], "warnings": [], "portfolio": {},
            "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "error": str(e),
        }

    # User-defined exit levels (used for risk floor)
    exit_rows = db.execute(
        select(HoldingExitLevel).where(HoldingExitLevel.user_id == user_id)
    ).scalars().all()
    exit_map: dict[str, HoldingExitLevel] = {
        f"{(r.exchange or 'NSE').upper()}:{r.tradingsymbol.upper()}": r
        for r in exit_rows
    }
    sector_overrides = _load_sector_overrides(db, user_id)

    # Refresh LTPs
    live_ltp = _bulk_ltp(
        broker,
        [_instrument_key(getattr(h, "exchange", "NSE") or "NSE", h.tradingsymbol)
         for h in raw],
    )

    # Day change: ask quote() for the holdings universe in one batch
    quote_map: dict = {}
    try:
        quote_map = broker.get_quote(
            [_instrument_key(getattr(h, "exchange", "NSE") or "NSE", h.tradingsymbol)
             for h in raw]
        ) or {}
    except Exception as e:
        logger.debug(f"holdings/insights: quote fetch failed: {e}")

    # Token map for historical (one-shot cached)
    token_map: dict[str, int] = {}
    try:
        rows = broker.get_instruments("NSE")
        wanted = {h.tradingsymbol for h in raw}
        for r in rows:
            ts = r.get("tradingsymbol")
            if ts in wanted and r.get("instrument_type") == "EQ":
                token_map[ts] = int(r["instrument_token"])
    except Exception as e:
        logger.debug(f"holdings/insights: instruments fetch failed: {e}")

    # Parallel historical fetch (cached per day)
    hist_map: dict[str, dict] = {}

    def _job(sym: str) -> tuple[str, dict]:
        tok = token_map.get(sym)
        if not tok:
            return sym, {}
        return sym, _aw_fetch_hist_for(broker, sym, tok)

    needed = [h.tradingsymbol for h in raw if token_map.get(h.tradingsymbol)]
    if needed:
        try:
            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = [pool.submit(_job, s) for s in needed]
                for f in as_completed(futures):
                    sym, metrics = f.result()
                    hist_map[sym] = metrics
        except Exception as e:
            logger.warning(f"holdings/insights: hist pool failed: {e}")

    holdings_out: list[dict] = []
    total_invested = 0.0
    total_current = 0.0
    total_risk = 0.0
    sector_value: dict[str, float] = {}

    for h in raw:
        sym = h.tradingsymbol
        exch = getattr(h, "exchange", "NSE") or "NSE"
        qty = float(h.quantity or 0)
        avg = float(h.average_price or 0)
        key = _instrument_key(exch, sym)
        ltp = float(live_ltp.get(key) or h.last_price or 0)
        invested = qty * avg
        current = qty * ltp
        pnl = current - invested
        pnl_pct = (pnl / invested * 100) if invested > 0 else 0.0
        total_invested += invested
        total_current += current

        # Day change
        q = quote_map.get(key) or {}
        prev_close = float((q.get("ohlc") or {}).get("close") or 0)
        change_pct = ((ltp - prev_close) / prev_close * 100.0) if prev_close > 0 else 0.0

        hist = hist_map.get(sym, {})
        signal, notes = _hi_signal_for(ltp, hist, change_pct)

        # Risk: user stop if set, else default 5% below LTP, but capped
        # at 20-day low to avoid unrealistic floors
        ex_row = exit_map.get(key)
        exit_level = float(ex_row.exit_level) if ex_row else None
        near_exit = False
        if ex_row and exit_level and ltp > 0:
            band = exit_level * (float(ex_row.proximity_pct) / 100.0)
            near_exit = abs(ltp - exit_level) <= band

        if exit_level and exit_level < ltp:
            stop_used = exit_level
        else:
            default_stop = ltp * 0.95
            low20 = hist.get("low_20d") or 0
            stop_used = max(default_stop, low20 * 0.99) if low20 else default_stop

        risk_per_share = max(0.0, ltp - stop_used)
        position_risk = risk_per_share * qty
        # Risk capped at downside only — never exceeds current value
        position_risk = min(position_risk, current)
        total_risk += position_risk

        action = _hi_action_for(signal, pnl_pct, near_exit)

        sector = _sector_for(sym, sector_overrides)
        sector_value[sector] = sector_value.get(sector, 0.0) + current

        holdings_out.append({
            "tradingsymbol": sym,
            "exchange": exch,
            "sector": sector,
            "quantity": qty,
            "average_price": avg,
            "last_price": round(ltp, 2),
            "change_pct": round(change_pct, 2),
            "invested": round(invested, 2),
            "current_value": round(current, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 3),
            "high_20d": hist.get("high_20d"),
            "low_20d": hist.get("low_20d"),
            "return_5d_pct": hist.get("return_5d_pct"),
            "up_days_5d": hist.get("up_days_5d"),
            "closes": hist.get("closes") or [],
            "signal": signal,
            "signal_notes": notes,
            "action": action,
            "stop_used": round(stop_used, 2),
            "risk_per_share": round(risk_per_share, 2),
            "position_risk": round(position_risk, 2),
            "exit_level": exit_level,
            "near_exit": near_exit,
        })

    # Allocation + concentration warnings
    warnings_out: list[dict] = []
    for h in holdings_out:
        h["allocation_pct"] = (
            round(h["current_value"] / total_current * 100, 2)
            if total_current > 0 else 0.0
        )
        h["risk_pct_of_position"] = (
            round(h["position_risk"] / h["current_value"] * 100, 2)
            if h["current_value"] > 0 else 0.0
        )
        h["risk_pct_of_portfolio"] = (
            round(h["position_risk"] / total_current * 100, 2)
            if total_current > 0 else 0.0
        )
        if h["allocation_pct"] >= 25:
            warnings_out.append({
                "level": "warning",
                "symbol": h["tradingsymbol"],
                "message": f"{h['tradingsymbol']} is {h['allocation_pct']:.1f}% of portfolio — high single-name concentration.",
            })
        if h["near_exit"]:
            warnings_out.append({
                "level": "alert",
                "symbol": h["tradingsymbol"],
                "message": f"{h['tradingsymbol']} is near your exit level (₹{h['exit_level']}).",
            })
        if h["signal"] == "breakdown":
            warnings_out.append({
                "level": "alert",
                "symbol": h["tradingsymbol"],
                "message": f"{h['tradingsymbol']} broke down through the 20-day low — review stop.",
            })

    # Sector concentration warning (>40% in one sector)
    for sec, val in sector_value.items():
        pct = (val / total_current * 100) if total_current > 0 else 0
        if pct >= 40 and total_current > 0:
            warnings_out.append({
                "level": "warning",
                "symbol": None,
                "message": f"Sector '{sec}' is {pct:.1f}% of portfolio — diversification risk.",
            })

    portfolio = {
        "total_invested": round(total_invested, 2),
        "total_current": round(total_current, 2),
        "total_pnl": round(total_current - total_invested, 2),
        "total_pnl_pct": round(
            ((total_current - total_invested) / total_invested * 100) if total_invested > 0 else 0.0, 3
        ),
        "total_risk": round(total_risk, 2),
        "total_risk_pct": round(
            (total_risk / total_current * 100) if total_current > 0 else 0.0, 2
        ),
        "holdings_count": len(holdings_out),
    }

    payload = {
        "holdings": holdings_out,
        "warnings": warnings_out,
        "portfolio": portfolio,
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _HI_RESPONSE_CACHE[user_id] = (now, payload)
    return payload
