"""
Shared research-watchlist data-access layer.

Named, per-user symbol lists used by the research modules (Straddle #7 and
Equity #8) so a backtest can target a curated set of stocks instead of the whole
F&O universe. Editable from the UI or via file upload/download, stored durably in
``ResearchWatchlist``. Pure DB access — no broker calls.
"""
from __future__ import annotations

import re

from core.logger import get_logger
from core.models import ResearchWatchlist

logger = get_logger("research.watchlist")

_MAX_SYMBOLS = 1000
_SYMBOL_RE = re.compile(r"^[A-Z0-9&\-\.]{1,30}$")


def clean_symbols(raw) -> list[str]:
    """Uppercase, strip, validate and de-duplicate (order-preserving)."""
    out: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        s = str(item).strip().upper()
        if not s or s.startswith("#"):
            continue
        if not _SYMBOL_RE.match(s):
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)
        if len(out) >= _MAX_SYMBOLS:
            break
    return out


def parse_symbols_text(text: str) -> list[str]:
    """Parse an uploaded/downloaded file body — newline, comma or whitespace
    separated (CSV/TXT). Ignores a leading 'symbol' header and blank lines."""
    tokens = re.split(r"[\s,;]+", text or "")
    tokens = [t for t in tokens if t and t.lower() not in ("symbol", "symbols", "tradingsymbol")]
    return clean_symbols(tokens)


def _summary(wl: ResearchWatchlist) -> dict:
    return {"id": wl.id, "name": wl.name, "count": len(wl.symbols or []),
            "updated_at": wl.updated_at.strftime("%Y-%m-%d %H:%M") if wl.updated_at else None}


def list_watchlists(db, user_id: int) -> list[dict]:
    rows = (db.query(ResearchWatchlist)
              .filter(ResearchWatchlist.user_id == user_id)
              .order_by(ResearchWatchlist.name).all())
    return [_summary(w) for w in rows]


def get_watchlist(db, user_id: int, wid: int) -> dict | None:
    w = (db.query(ResearchWatchlist)
           .filter(ResearchWatchlist.user_id == user_id, ResearchWatchlist.id == wid).first())
    if not w:
        return None
    return {"id": w.id, "name": w.name, "symbols": list(w.symbols or []), **_summary(w)}


def _find_by_name(db, user_id: int, name: str) -> ResearchWatchlist | None:
    return (db.query(ResearchWatchlist)
              .filter(ResearchWatchlist.user_id == user_id, ResearchWatchlist.name == name).first())


def create_watchlist(db, user_id: int, name: str, symbols=None) -> dict:
    name = (name or "").strip()[:80] or "Untitled"
    if _find_by_name(db, user_id, name):
        raise ValueError(f"A watchlist named '{name}' already exists")
    w = ResearchWatchlist(user_id=user_id, name=name, symbols=clean_symbols(symbols))
    db.add(w)
    db.commit()
    db.refresh(w)
    return get_watchlist(db, user_id, w.id)


def update_watchlist(db, user_id: int, wid: int, name=None, symbols=None) -> dict | None:
    w = (db.query(ResearchWatchlist)
           .filter(ResearchWatchlist.user_id == user_id, ResearchWatchlist.id == wid).first())
    if not w:
        return None
    if name is not None:
        new_name = str(name).strip()[:80]
        if new_name and new_name != w.name:
            clash = _find_by_name(db, user_id, new_name)
            if clash and clash.id != w.id:
                raise ValueError(f"A watchlist named '{new_name}' already exists")
            w.name = new_name
    if symbols is not None:
        w.symbols = clean_symbols(symbols)
    db.commit()
    return get_watchlist(db, user_id, w.id)


def modify_symbols(db, user_id: int, wid: int, add=None, remove=None) -> dict | None:
    """Add and/or remove symbols from an existing watchlist."""
    w = (db.query(ResearchWatchlist)
           .filter(ResearchWatchlist.user_id == user_id, ResearchWatchlist.id == wid).first())
    if not w:
        return None
    current = list(w.symbols or [])
    if remove:
        rm = set(clean_symbols(remove))
        current = [s for s in current if s not in rm]
    if add:
        existing = set(current)
        for s in clean_symbols(add):
            if s not in existing:
                current.append(s)
                existing.add(s)
    w.symbols = current[:_MAX_SYMBOLS]
    db.commit()
    return get_watchlist(db, user_id, w.id)


def upsert_from_upload(db, user_id: int, name: str, text: str) -> dict:
    """Create a watchlist from an uploaded file, or REPLACE its symbols if a
    watchlist with that name already exists (the download→edit→upload flow)."""
    symbols = parse_symbols_text(text)
    existing = _find_by_name(db, user_id, (name or "").strip()[:80])
    if existing:
        return update_watchlist(db, user_id, existing.id, symbols=symbols)
    return create_watchlist(db, user_id, name, symbols)


def delete_watchlist(db, user_id: int, wid: int) -> bool:
    w = (db.query(ResearchWatchlist)
           .filter(ResearchWatchlist.user_id == user_id, ResearchWatchlist.id == wid).first())
    if not w:
        return False
    db.delete(w)
    db.commit()
    return True
