"""
MIH scanner registry — PURE predicates over a per-stock day snapshot.

Every scanner is data-driven and declares what it needs, so a scanner whose data
is unavailable is reported as such instead of silently returning nothing. Adding
a new screen = adding one entry here; no engine changes.

Snapshot row keys: symbol, ltp, open, high, low, prev_close, change_pct, volume,
vwap, rvol, high_52w, low_52w, high_20d, low_20d, sector.
"""
from __future__ import annotations


def _pct(a, b):
    return ((a - b) / b * 100.0) if (a is not None and b) else None


def _near(a, b, tol_pct):
    return a is not None and b not in (None, 0) and abs(a - b) / b * 100.0 <= tol_pct


# each: key → (label, description, direction, needs[], predicate(row, cfg))
SCANNERS: dict[str, tuple] = {
    "open_eq_low": (
        "Open Equal to Low", "Opening price equals the day's low — buyers in control from the bell.",
        "bullish", [],
        lambda r, c: _near(r.get("open"), r.get("low"), c["open_eq_tol_pct"]) and (r.get("change_pct") or 0) > 0),
    "open_eq_high": (
        "Open Equal to High", "Opening price equals the day's high — sellers in control from the bell.",
        "bearish", [],
        lambda r, c: _near(r.get("open"), r.get("high"), c["open_eq_tol_pct"]) and (r.get("change_pct") or 0) < 0),
    "gap_up": (
        "Gap Up", "Opened above the previous close by a meaningful margin.", "bullish", [],
        lambda r, c: (_pct(r.get("open"), r.get("prev_close")) or 0) >= c["gap_pct"]),
    "gap_down": (
        "Gap Down", "Opened below the previous close by a meaningful margin.", "bearish", [],
        lambda r, c: (_pct(r.get("open"), r.get("prev_close")) or 0) <= -c["gap_pct"]),
    "vol_shocker": (
        "Volume Shockers", "Trading far above its own average volume for the day so far.",
        "neutral", ["rvol"],
        lambda r, c: (r.get("rvol") or 0) >= c["vol_shocker_rvol"]),
    "price_vol_breakout": (
        "Price & Volume Breakout", "Up strongly on heavy volume and clearing the recent 20-day high.",
        "bullish", ["rvol", "high_20d"],
        lambda r, c: (r.get("change_pct") or 0) >= c["breakout_change_pct"]
        and (r.get("rvol") or 0) >= c["breakout_rvol"]
        and r.get("high_20d") and r.get("ltp", 0) >= r["high_20d"]),
    "high_52w": (
        "52-Week High", "Trading at or near its highest price of the last year.", "bullish", ["high_52w"],
        lambda r, c: r.get("high_52w") and r.get("ltp", 0) >= r["high_52w"] * (1 - c["near_52w_pct"] / 100.0)),
    "low_52w": (
        "52-Week Low", "Trading at or near its lowest price of the last year.", "bearish", ["low_52w"],
        lambda r, c: r.get("low_52w") and r.get("ltp", 0) <= r["low_52w"] * (1 + c["near_52w_pct"] / 100.0)),
    "above_vwap": (
        "Above VWAP", "Holding above the day's volume-weighted average price.", "bullish", ["vwap"],
        lambda r, c: r.get("vwap") and r.get("ltp", 0) >= r["vwap"]),
    "below_vwap": (
        "Below VWAP", "Trading under the day's volume-weighted average price.", "bearish", ["vwap"],
        lambda r, c: r.get("vwap") and r.get("ltp", 0) < r["vwap"]),
}

GROUPS = {
    "Intraday Scans": ["open_eq_low", "open_eq_high", "gap_up", "gap_down", "above_vwap", "below_vwap"],
    "Price & Volume Breakouts": ["vol_shocker", "price_vol_breakout", "high_52w", "low_52w"],
}


def run_scanner(key: str, rows: list[dict], cfg: dict, limit: int = 0) -> dict:
    """Run one scanner. Reports data coverage so a screen that needs enrichment
    (52w / RVOL) is never silently empty."""
    spec = SCANNERS.get(key)
    if not spec:
        return {"status": "error", "message": f"Unknown scanner '{key}'"}
    label, desc, direction, needs, pred = spec
    eligible = [r for r in rows if all(r.get(n) is not None for n in needs)]
    hits = []
    for r in eligible:
        try:
            if pred(r, cfg):
                hits.append(r)
        except Exception:
            continue
    hits.sort(key=lambda r: abs(r.get("change_pct") or 0), reverse=True)
    return {
        "key": key, "label": label, "description": desc, "direction": direction,
        "count": len(hits), "rows": hits[:limit] if limit else hits,
        "coverage": {"eligible": len(eligible), "total": len(rows), "needs": needs},
        "data_note": (None if len(eligible) == len(rows)
                      else f"{len(eligible)}/{len(rows)} stocks have the data this screen needs "
                           f"({', '.join(needs)}) — the rest are excluded, not assumed."),
    }


def run_all(rows: list[dict], cfg: dict, limit: int = 0) -> dict:
    return {k: run_scanner(k, rows, cfg, limit) for k in SCANNERS}
