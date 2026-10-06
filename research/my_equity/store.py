"""
The workspace list itself — add, edit and remove the stocks you are researching.

Pure database access: no broker calls, no market data, so the list always loads even when
Zerodha is disconnected (prices simply arrive empty).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Optional

from core.logger import get_logger
from sqlalchemy.orm.attributes import flag_modified

from core.models import MyEquityStock
from research.my_equity import levels as LV

logger = get_logger("research.my_equity.store")

MAX_STOCKS = 500
MAX_LEVELS = LV.MAX_LEVELS
CATEGORIES = ("INVESTMENT", "SWING")
_SYMBOL_RE = re.compile(r"^[A-Z0-9&\-\.]{1,32}$")


def clean_category(raw) -> str:
    c = str(raw or "SWING").strip().upper().replace(" ", "_")
    if c.startswith("INVEST"):
        return "INVESTMENT"
    return "SWING"


def clean_symbol(raw: str) -> str:
    s = (raw or "").strip().upper()
    if not _SYMBOL_RE.match(s):
        raise ValueError(f"'{raw}' is not a valid trading symbol")
    return s


def parse_levels(raw) -> list[dict]:
    """Accept a list of numbers or level objects, or text like '3100, 2900' — however you type it.

    Stored shape is always ``[{"price": 3100.0, "track": True}, …]``; ``track`` says whether the
    level counts towards the P&L, so you can keep a level on the chart without following it.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [p for p in re.split(r"[\s,;|]+", raw) if p]
    return LV.normalise(raw)


def to_dict(row: MyEquityStock) -> dict:
    return {
        "id": row.id, "symbol": row.symbol, "exchange": row.exchange, "company": row.company,
        "token": row.token, "added_on": row.added_on.isoformat() if row.added_on else None,
        "levels": LV.normalise(row.levels), "level_prices": LV.prices(row.levels),
        "category": clean_category(row.category), "sector": row.sector, "industry": row.industry,
        "sector_source": row.sector_source, "note": row.note or "",
        "touch_pct": float(row.touch_pct if row.touch_pct is not None else 0.25),
        "archived": bool(row.archived), "alerts_on": bool(getattr(row, "alerts_on", True)),
        "auto_fno": bool(getattr(row, "auto_fno", False)),
        "last_touch_at": row.last_touch_at.strftime("%Y-%m-%d %H:%M") if row.last_touch_at else None,
        "last_touch_level": float(row.last_touch_level) if row.last_touch_level is not None else None,
    }


def list_stocks(db, user_id: int, include_archived: bool = False) -> list[MyEquityStock]:
    q = db.query(MyEquityStock).filter(MyEquityStock.user_id == user_id)
    if not include_archived:
        q = q.filter(MyEquityStock.archived.is_(False))
    return q.order_by(MyEquityStock.added_on.desc().nullslast(), MyEquityStock.symbol).all()


def get(db, user_id: int, stock_id: int) -> Optional[MyEquityStock]:
    return (db.query(MyEquityStock)
              .filter(MyEquityStock.user_id == user_id, MyEquityStock.id == stock_id).first())


def find(db, user_id: int, symbol: str, exchange: str) -> Optional[MyEquityStock]:
    return (db.query(MyEquityStock)
              .filter(MyEquityStock.user_id == user_id,
                      MyEquityStock.symbol == symbol.upper(),
                      MyEquityStock.exchange == exchange.upper()).first())


def add(db, user_id: int, *, symbol: str, exchange: str = "NSE", token: Optional[int] = None,
        company: Optional[str] = None, levels=None, note: str = "",
        added_on: Optional[date] = None, touch_pct: float = 0.25,
        category: str = "SWING", sector: Optional[str] = None,
        industry: Optional[str] = None) -> MyEquityStock:
    symbol = clean_symbol(symbol)
    exchange = (exchange or "NSE").upper()
    existing = find(db, user_id, symbol, exchange)
    if existing:
        # adding the same stock again means "update my research", not an error
        return update(db, user_id, existing.id, levels=levels, note=note or existing.note,
                      added_on=added_on, touch_pct=touch_pct, archived=False, category=category)
    if db.query(MyEquityStock).filter(MyEquityStock.user_id == user_id).count() >= MAX_STOCKS:
        raise ValueError(f"the workspace holds at most {MAX_STOCKS} stocks")
    row = MyEquityStock(
        user_id=user_id, symbol=symbol, exchange=exchange, token=token, company=company,
        added_on=added_on or date.today(), levels=parse_levels(levels), note=(note or "").strip()[:2000],
        touch_pct=max(0.01, min(float(touch_pct or 0.25), 10.0)), archived=False,
        category=clean_category(category), sector=sector, industry=industry,
        sector_source="auto" if sector else None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info("my-equity: user %s added %s:%s", user_id, exchange, symbol)
    return row


def update(db, user_id: int, stock_id: int, **fields) -> MyEquityStock:
    row = get(db, user_id, stock_id)
    if row is None:
        raise ValueError("that stock is not in your workspace")
    if "levels" in fields and fields["levels"] is not None:
        row.levels = parse_levels(fields["levels"])
    if fields.get("note") is not None:
        row.note = str(fields["note"]).strip()[:2000]
    if fields.get("added_on"):
        v = fields["added_on"]
        row.added_on = v if isinstance(v, date) else date.fromisoformat(str(v)[:10])
    if fields.get("touch_pct") is not None:
        row.touch_pct = max(0.01, min(float(fields["touch_pct"]), 10.0))
    if fields.get("archived") is not None:
        row.archived = bool(fields["archived"])
    if fields.get("category") is not None:
        row.category = clean_category(fields["category"])
    if fields.get("alerts_on") is not None:
        row.alerts_on = bool(fields["alerts_on"])
    if fields.get("auto_fno") is not None:
        row.auto_fno = bool(fields["auto_fno"])
    if fields.get("sector") is not None:
        row.sector = str(fields["sector"]).strip()[:60] or None
        row.industry = (str(fields.get("industry") or "").strip()[:90] or None) or row.industry
        row.sector_source = fields.get("sector_source") or "manual"
    for key in ("token", "company"):
        if fields.get(key) is not None:
            setattr(row, key, fields[key])
    db.commit()
    db.refresh(row)
    return row


def remove(db, user_id: int, stock_id: int) -> bool:
    row = get(db, user_id, stock_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    logger.info("my-equity: user %s removed %s", user_id, row.symbol)
    return True


def book_level(db, user_id: int, stock_id: int, level: float, *, kind: str = "",
               qty: float = 0, entry: float = 0, exit: float = 0,
               entered_on=None, exited_on=None, note: str = "") -> dict:
    """Close out a trade on one research level and re-arm it.

    The booking is appended to that level's history and ``reset_on`` is set to the exit date, so
    the level starts hunting for its next trigger from there. The level itself is untouched — the
    same price can be used again, or edited to a new one, without losing what it did.
    """
    row = get(db, user_id, stock_id)
    if row is None:
        raise ValueError("stock not found")
    level = float(level)
    levels = LV.normalise(row.levels)
    target = next((l for l in levels if abs(l["price"] - level) < 1e-6), None)
    if target is None:
        raise ValueError(f"{level:g} is not a research level on {row.symbol}")

    entry = float(entry or level)
    exit_px = float(exit or 0)
    if exit_px <= 0:
        raise ValueError("an exit price is needed to book a trade")
    qty = float(qty or 0)
    d_in = LV._as_date(entered_on) or LV._as_date(row.added_on) or date.today()
    d_out = LV._as_date(exited_on) or date.today()
    if d_out < d_in:
        raise ValueError("the exit date is before the entry date")

    pnl_per = exit_px - entry
    pnl = round(pnl_per * qty, 2) if qty else None
    kind = (kind or "").strip().upper()
    if kind not in ("PROFIT", "LOSS"):
        kind = "PROFIT" if pnl_per >= 0 else "LOSS"

    booking = {"kind": kind, "qty": qty or None, "entry": round(entry, 2),
               "exit": round(exit_px, 2), "entered_on": d_in.isoformat(),
               "exited_on": d_out.isoformat(), "days": (d_out - d_in).days,
               "pnl": pnl, "pnl_pct": round(pnl_per / entry * 100, 2) if entry else None,
               "note": (note or "").strip()[:200]}
    target["booked"] = [*(target.get("booked") or []), booking][-LV.MAX_BOOKED:]
    # re-arm from the exit date: the trade just closed must not be found again
    target["reset_on"] = d_out.isoformat()

    row.levels = LV.normalise(levels)
    flag_modified(row, "levels")
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    logger.info("my-equity: user %s booked %s on %s %s", user_id, kind, row.symbol, level)
    return {"stock": to_dict(row), "booking": booking}


def clear_booking(db, user_id: int, stock_id: int, level: float, index: int = -1) -> dict:
    """Undo a booking — remove it and, if it was the last one, re-arm from the research date."""
    row = get(db, user_id, stock_id)
    if row is None:
        raise ValueError("stock not found")
    levels = LV.normalise(row.levels)
    target = next((l for l in levels if abs(l["price"] - float(level)) < 1e-6), None)
    if target is None or not target.get("booked"):
        raise ValueError("nothing booked on that level")
    hist = list(target["booked"])
    try:
        hist.pop(index)
    except IndexError:
        raise ValueError("no booking at that position")
    if hist:
        target["booked"] = hist
        target["reset_on"] = hist[-1]["exited_on"]
    else:
        target.pop("booked", None)
        target.pop("reset_on", None)
    row.levels = LV.normalise(levels)
    flag_modified(row, "levels")
    db.commit()
    return {"stock": to_dict(row)}


def mark_touch(db, row: MyEquityStock, level: float) -> None:
    """Remember that price reached one of the research levels (for the 'last touched' badge)."""
    try:
        row.last_touch_at = datetime.now(timezone.utc)
        row.last_touch_level = level
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.debug("touch not saved for %s: %s", row.symbol, exc)
