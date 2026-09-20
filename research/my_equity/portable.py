"""
Take the workspace in and out as a spreadsheet.

Someone arriving with eighty names in Excel should be researching in a minute, not typing for
an hour — and the research you have built up should never be locked inside one app. Export
writes every editable field plus a few read-only ones for context; import reads the editable
ones back and ignores the rest, so an exported file can be edited in Excel and re-imported.

Levels, targets and stops travel as pipe-separated columns that line up by position:

    symbol , levels        , targets       , stops
    BSE    , 3100|2900     , 3400|3250     , 2950|2800

A dry run reports exactly what would happen to every line before anything is written.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date
from typing import Optional

from core.logger import get_logger
from research.my_equity import levels as LV
from research.my_equity import store as ST

logger = get_logger("research.my_equity.portable")

COLUMNS = ["symbol", "exchange", "category", "sector", "researched_on", "levels", "targets",
           "stops", "tracked", "touch_pct", "alerts", "note"]
EXTRA = ["ltp", "status", "pnl_pct", "triggered_on", "company", "industry"]
MAX_ROWS = 500
_ALIASES = {
    "stock": "symbol", "tradingsymbol": "symbol", "ticker": "symbol", "scrip": "symbol",
    "type": "category", "trade_category": "category", "trade category": "category",
    "added_on": "researched_on", "research_date": "researched_on", "date": "researched_on",
    "level": "levels", "entry": "levels", "entry_levels": "levels", "research_levels": "levels",
    "target": "targets", "sl": "stops", "stop": "stops", "stoploss": "stops", "stop_loss": "stops",
    "notes": "note", "remark": "note", "remarks": "note", "comment": "note",
    "tolerance": "touch_pct", "touch": "touch_pct", "alert": "alerts", "alerts_on": "alerts",
}


def _split(value: str) -> list[str]:
    return [p.strip() for p in str(value or "").replace(";", "|").replace(",", "|").split("|") if p.strip()]


def _canon(header: Optional[str]) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", str(header or "").strip().lower()).strip("_")
    return _ALIASES.get(key, key)


def _bools(value, default: bool = True) -> bool:
    s = str(value).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    return default


# ── export ───────────────────────────────────────────────────────────
def export_csv(db, user_id: int, rows_payload: Optional[dict] = None) -> str:
    """The whole workspace as CSV text, ready to open in Excel."""
    live = {r.get("id"): r for r in (rows_payload or {}).get("rows", [])}
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS + EXTRA, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for s in ST.list_stocks(db, user_id):
        lv = LV.normalise(s.levels)
        r = live.get(s.id) or {}
        watch = r.get("watch") or {}
        primary = watch.get("primary") or {}
        w.writerow({
            "symbol": s.symbol, "exchange": s.exchange,
            "category": ST.clean_category(s.category), "sector": s.sector or "",
            "researched_on": s.added_on.isoformat() if s.added_on else "",
            "levels": "|".join(f"{x['price']:g}" for x in lv),
            "targets": "|".join(f"{x.get('target', ''):g}" if x.get("target") else "" for x in lv),
            "stops": "|".join(f"{x.get('stop', ''):g}" if x.get("stop") else "" for x in lv),
            "tracked": "|".join("yes" if x["track"] else "no" for x in lv),
            "touch_pct": s.touch_pct, "alerts": "yes" if getattr(s, "alerts_on", True) else "no",
            "note": (s.note or "").replace("\n", " "),
            "ltp": r.get("ltp", ""), "status": watch.get("status", ""),
            "pnl_pct": primary.get("pnl_pct", ""), "triggered_on": primary.get("triggered_on", ""),
            "company": s.company or "", "industry": s.industry or "",
        })
    return buf.getvalue()


def template_csv() -> str:
    """A two-line example so nobody has to guess the format."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerow({"symbol": "BSE", "exchange": "NSE", "category": "SWING", "sector": "",
                "researched_on": date.today().isoformat(), "levels": "3100|2900",
                "targets": "3400|3250", "stops": "2950|2800", "tracked": "yes|yes",
                "touch_pct": "0.25", "alerts": "yes", "note": "breakout retest"})
    w.writerow({"symbol": "RELIANCE", "exchange": "NSE", "category": "INVESTMENT", "sector": "",
                "researched_on": date.today().isoformat(), "levels": "2850", "targets": "3300",
                "stops": "2700", "tracked": "yes", "touch_pct": "0.5", "alerts": "yes",
                "note": "accumulate on dips"})
    return buf.getvalue()


# ── import ───────────────────────────────────────────────────────────
def parse(text: str) -> tuple[list[dict], list[str]]:
    """Read a CSV into workspace rows, reporting anything that could not be understood."""
    problems: list[str] = []
    try:
        sample = text[:4096]
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t") if sample.strip() else csv.excel
    except Exception:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        return [], ["the file has no header row — the first line must name the columns"]
    # "Research Date", "research_date" and "RESEARCH-DATE" all mean the same column
    fields = {_canon(f): f for f in reader.fieldnames}
    if "symbol" not in fields:
        return [], [f"no 'symbol' column found (saw: {', '.join(reader.fieldnames[:8])})"]
    out: list[dict] = []
    for n, raw in enumerate(reader, start=2):
        if len(out) >= MAX_ROWS:
            problems.append(f"stopped at {MAX_ROWS} rows")
            break
        get = lambda key, default="": str(raw.get(fields.get(key, ""), default) or "").strip()  # noqa: E731
        symbol = get("symbol").upper()
        if not symbol or symbol.startswith("#"):
            continue
        try:
            ST.clean_symbol(symbol)
        except ValueError:
            problems.append(f"line {n}: '{symbol}' is not a valid symbol")
            continue
        prices, targets, stops = _split(get("levels")), _split(get("targets")), _split(get("stops"))
        tracked = _split(get("tracked"))
        levels = []
        for i, p in enumerate(prices):
            item = {"price": p, "track": _bools(tracked[i], True) if i < len(tracked) else True}
            if i < len(targets) and targets[i]:
                item["target"] = targets[i]
            if i < len(stops) and stops[i]:
                item["stop"] = stops[i]
            levels.append(item)
        row = {
            "line": n, "symbol": symbol, "exchange": (get("exchange") or "").upper() or None,
            "category": ST.clean_category(get("category") or "SWING"),
            "sector": get("sector") or None,
            "levels": LV.normalise(levels), "note": get("note")[:2000],
            "alerts_on": _bools(get("alerts", "yes"), True),
        }
        researched = get("researched_on")
        if researched:
            try:
                row["added_on"] = _as_iso(researched)
            except ValueError:
                problems.append(f"line {n}: could not read the date '{researched}' — using today")
        touch = get("touch_pct")
        if touch:
            try:
                row["touch_pct"] = max(0.01, min(float(touch), 10.0))
            except ValueError:
                pass
        if len(prices) and not row["levels"]:
            problems.append(f"line {n}: {symbol} — no usable level in '{get('levels')}'")
        out.append(row)
    return out, problems


def _as_iso(value: str) -> str:
    """Accept 2026-09-20, 20-09-2026, 20/09/2026 — the ways a spreadsheet writes a date."""
    v = value.strip().replace("/", "-")
    parts = v.split("-")
    if len(parts) == 3:
        if len(parts[0]) == 4:
            return date(int(parts[0]), int(parts[1]), int(parts[2])).isoformat()
        return date(int(parts[2]), int(parts[1]), int(parts[0])).isoformat()
    raise ValueError(value)


def apply(db, user_id: int, rows: list[dict], svc=None, dry_run: bool = True) -> dict:
    """Add or update every parsed row. ``dry_run`` reports without writing anything."""
    results, added, updated, failed = [], 0, 0, 0
    for row in rows:
        symbol, exchange = row["symbol"], row.get("exchange")
        existing = None
        resolved = None
        if svc is not None and svc.broker is not None:
            resolved = svc.resolve(symbol, exchange)
            if resolved is None:
                results.append({**row, "action": "error",
                                "message": f"{symbol} is not listed on NSE or BSE"})
                failed += 1
                continue
            exchange = resolved["exchange"]
        existing = ST.find(db, user_id, symbol, exchange or "NSE")
        action = "update" if existing else "add"
        if dry_run:
            results.append({**row, "action": action, "exchange": exchange or "NSE",
                            "company": (resolved or {}).get("company")})
            added += action == "add"
            updated += action == "update"
            continue
        # a column that could not be read must never erase what is already stored
        levels = row["levels"] or None
        try:
            ST.add(db, user_id, symbol=symbol, exchange=exchange or "NSE",
                   token=(resolved or {}).get("token"), company=(resolved or {}).get("company"),
                   levels=levels, note=row.get("note", ""),
                   added_on=row.get("added_on"), touch_pct=row.get("touch_pct", 0.25),
                   category=row.get("category", "SWING"), sector=row.get("sector"))
            saved = ST.find(db, user_id, symbol, exchange or "NSE")
            if saved is not None:
                fields = {"category": row.get("category")}
                if levels:
                    fields["levels"] = levels
                if row.get("sector"):
                    fields["sector"] = row["sector"]
                if row.get("added_on"):
                    fields["added_on"] = row["added_on"]
                if row.get("touch_pct") is not None:
                    fields["touch_pct"] = row["touch_pct"]
                ST.update(db, user_id, saved.id, **fields)
                saved.alerts_on = bool(row.get("alerts_on", True))
                db.commit()
            results.append({**row, "action": action, "exchange": exchange or "NSE",
                            "kept_levels": not levels})
            added += action == "add"
            updated += action == "update"
        except Exception as exc:
            logger.warning("import failed for %s: %s", symbol, exc)
            results.append({**row, "action": "error", "message": str(exc)[:160]})
            failed += 1
    return {"status": "ok", "dry_run": dry_run, "rows": results,
            "added": added, "updated": updated, "failed": failed}
