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
from core.models import MyEquityStock

logger = get_logger("research.my_equity.store")

MAX_STOCKS = 200
MAX_LEVELS = 12
_SYMBOL_RE = re.compile(r"^[A-Z0-9&\-\.]{1,32}$")


def clean_symbol(raw: str) -> str:
    s = (raw or "").strip().upper()
    if not _SYMBOL_RE.match(s):
        raise ValueError(f"'{raw}' is not a valid trading symbol")
    return s


def parse_levels(raw) -> list[float]:
    """Accept a list, or text like '3100, 2900' / '3100 2900' — the way you would type it."""
    if raw is None:
        return []
    items = raw if isinstance(raw, (list, tuple)) else re.split(r"[\s,;|]+", str(raw))
    out: list[float] = []
    for it in items:
        try:
            v = round(float(str(it).replace(",", "").strip()), 2)
        except (TypeError, ValueError):
            continue
        if v > 0 and v not in out:
            out.append(v)
    return sorted(out)[:MAX_LEVELS]


def to_dict(row: MyEquityStock) -> dict:
    return {
        "id": row.id, "symbol": row.symbol, "exchange": row.exchange, "company": row.company,
        "token": row.token, "added_on": row.added_on.isoformat() if row.added_on else None,
        "levels": [float(x) for x in (row.levels or [])], "note": row.note or "",
        "touch_pct": float(row.touch_pct if row.touch_pct is not None else 0.25),
        "archived": bool(row.archived),
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
        added_on: Optional[date] = None, touch_pct: float = 0.25) -> MyEquityStock:
    symbol = clean_symbol(symbol)
    exchange = (exchange or "NSE").upper()
    existing = find(db, user_id, symbol, exchange)
    if existing:
        # adding the same stock again means "update my research", not an error
        return update(db, user_id, existing.id, levels=levels, note=note or existing.note,
                      added_on=added_on, touch_pct=touch_pct, archived=False)
    if db.query(MyEquityStock).filter(MyEquityStock.user_id == user_id).count() >= MAX_STOCKS:
        raise ValueError(f"the workspace holds at most {MAX_STOCKS} stocks")
    row = MyEquityStock(
        user_id=user_id, symbol=symbol, exchange=exchange, token=token, company=company,
        added_on=added_on or date.today(), levels=parse_levels(levels), note=(note or "").strip()[:2000],
        touch_pct=max(0.01, min(float(touch_pct or 0.25), 10.0)), archived=False,
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


def mark_touch(db, row: MyEquityStock, level: float) -> None:
    """Remember that price reached one of the research levels (for the 'last touched' badge)."""
    try:
        row.last_touch_at = datetime.now(timezone.utc)
        row.last_touch_level = level
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.debug("touch not saved for %s: %s", row.symbol, exc)
