"""
Normalize raw Kite candles into the consistent dataset schema (pure).
"""
from __future__ import annotations


def _iso(ts):
    return ts.isoformat() if hasattr(ts, "isoformat") else (str(ts) if ts is not None else None)


def normalize_candles(candles: list[dict], meta: dict, interval_label: str) -> list[dict]:
    """Map Kite candles ({date,open,high,low,close,volume,oi?}) → schema rows."""
    exp = meta.get("expiry")
    rows = []
    for c in candles:
        rows.append({
            "timestamp": _iso(c.get("date")),
            "symbol": meta.get("symbol"),
            "exchange": meta.get("exchange"),
            "instrument_token": meta.get("instrument_token"),
            "instrument_type": meta.get("instrument_type"),
            "expiry": exp.isoformat() if hasattr(exp, "isoformat") else exp,
            "strike": meta.get("strike"),
            "option_type": meta.get("option_type"),
            "interval": interval_label,
            "open": _num(c.get("open")), "high": _num(c.get("high")),
            "low": _num(c.get("low")), "close": _num(c.get("close")),
            "volume": _int(c.get("volume")),
            "oi": _int(c.get("oi")) if c.get("oi") is not None else None,
        })
    return rows


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
