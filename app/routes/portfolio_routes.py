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

    holdings: list[dict] = []
    total_invested = 0.0
    total_current = 0.0

    for h in holdings_raw:
        sym = h.tradingsymbol
        exch = getattr(h, "exchange", "NSE") or "NSE"
        qty = float(h.quantity or 0)
        avg = float(h.average_price or 0)
        ltp = float(h.last_price or 0)
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
