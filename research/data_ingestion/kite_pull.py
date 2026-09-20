"""
One-click pull of everything currently listed from Zerodha into the Market Store.

Uploading files covers expired contracts; this covers the live ones. For each chosen index it
fetches, at 1-minute resolution and straight into the same store the OI Lab reads:

  index spot      NIFTY 50 / SENSEX / … (kind=spot, underlying=NIFTY, SENSEX, …)
  India VIX       kind=spot, underlying=INDIAVIX
  futures         current-month contract (kind=spot, underlying=NIFTYFUT, SENSEXFUT, …)
  option chain    the nearest expiry (optionally more), ATM ± N strikes, CE and PE, WITH open
                  interest, and with the index price of the same minute filled into `spot`

What it cannot do: fetch a contract that has already expired — Zerodha drops it from the
instrument list, so its token is gone. That is what the file upload is for. Pull daily (or at
least weekly) and every expiry is captured while it is still listed.

Read-only against the market: it only calls quote/historical, never places an order. Requests are
spaced to stay inside Kite's ~3 historical calls per second, and merges are idempotent, so
pulling the same days again adds nothing twice.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from typing import Callable, Optional

import pandas as pd

from core.logger import get_logger
from research.data_downloader.chunker import build_chunks
from research.market_store import store as MS
from research.oi_lab import indices as IX

logger = get_logger("research.data_ingestion.kite_pull")

CALL_SPACING_S = 0.35            # Kite allows ~3 historical requests per second
MAX_DAYS = 60                    # Kite serves at most 60 days of 1-minute data per request
STATE_FILE = ".last_kite_pull.json"

DEFAULTS = {
    "indices": ["NIFTY", "SENSEX"],
    "days": 5,
    "strikes": 10,
    "expiries": 1,
    "include_index": True,
    "include_vix": True,
    "include_futures": True,
}


def config(overrides: Optional[dict] = None) -> dict:
    c = {**DEFAULTS, **{k: v for k, v in (overrides or {}).items() if k in DEFAULTS and v is not None}}
    c["indices"] = [i for i in c["indices"] if i in IX.INDICES] or ["NIFTY"]
    c["days"] = max(1, min(int(c["days"]), MAX_DAYS))
    c["strikes"] = max(1, min(int(c["strikes"]), 25))
    c["expiries"] = max(1, min(int(c["expiries"]), 4))
    for k in ("include_index", "include_vix", "include_futures"):
        c[k] = bool(c[k])
    return c


# ── state (kept beside the store, so it lives on the same volume) ────
def _state_path():
    return MS.ROOT / STATE_FILE


def last_pull() -> Optional[dict]:
    try:
        return json.loads(_state_path().read_text())
    except Exception:
        return None


def _save_state(result: dict) -> None:
    try:
        MS.ROOT.mkdir(parents=True, exist_ok=True)
        _state_path().write_text(json.dumps(result, default=str))
    except Exception as exc:
        logger.debug("could not save pull state: %s", exc)


# ── planning ─────────────────────────────────────────────────────────
def _instruments(broker, exch: str) -> list[dict]:
    try:
        return broker.get_instruments(exch) or []
    except Exception as exc:
        logger.warning("instrument dump %s failed: %s", exch, exc)
        return []


def _as_date(v) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return pd.Timestamp(str(v)).date()
    except Exception:
        return None


def _spot_token(broker, key: str) -> Optional[int]:
    exch, sym = key.split(":", 1)
    for inst in _instruments(broker, exch):
        if inst.get("tradingsymbol") == sym:
            return int(inst["instrument_token"])
    return None


def build_tasks(broker, c: dict) -> tuple[list[dict], list[str]]:
    """Every series to fetch, and anything that could not be resolved. No market data yet."""
    today = IX.now_ist().date()
    tasks: list[dict] = []
    notes: list[str] = []
    if c["include_vix"]:
        tok = _spot_token(broker, IX.VIX_KEY)
        if tok:
            tasks.append({"kind": "spot", "underlying": "INDIAVIX", "token": tok, "label": "India VIX", "oi": False})
        else:
            notes.append("India VIX is not in the instrument list")
    for u in c["indices"]:
        cfg_u = IX.get(u)
        if c["include_index"]:
            tok = _spot_token(broker, cfg_u["spot"])
            if tok:
                tasks.append({"kind": "spot", "underlying": u, "token": tok,
                              "label": f"{cfg_u['label']} index", "oi": False, "is_index": True})
            else:
                notes.append(f"{u}: index not found ({cfg_u['spot']})")
        dump = _instruments(broker, cfg_u["exch"])
        mine = [i for i in dump if i.get("name") == u]
        if c["include_futures"]:
            futs = sorted(((_as_date(i.get("expiry")), i) for i in mine if i.get("instrument_type") == "FUT"),
                          key=lambda t: (t[0] or date.max))
            fut = next((i for d, i in futs if d and d >= today), None)
            if fut:
                # futures go in the spot schema, which has no OI column, so OI is not requested
                tasks.append({"kind": "spot", "underlying": f"{u}FUT", "token": int(fut["instrument_token"]),
                              "label": f"{u} futures ({fut.get('tradingsymbol')})", "oi": False})
            else:
                notes.append(f"{u}: no futures contract is listed")
        opts = [i for i in mine if i.get("instrument_type") in ("CE", "PE") and _as_date(i.get("expiry"))]
        exps = sorted({_as_date(i["expiry"]) for i in opts if _as_date(i["expiry"]) >= today})[: c["expiries"]]
        if not exps:
            notes.append(f"{u}: no option expiry is listed")
            continue
        spot = 0.0
        try:
            spot = float((broker.get_quote([cfg_u["spot"]]) or {}).get(cfg_u["spot"], {}).get("last_price") or 0)
        except Exception as exc:
            notes.append(f"{u}: live price unavailable, using the middle of the strike list ({exc})"[:140])
        for exp in exps:
            listed = sorted({float(i["strike"]) for i in opts
                             if _as_date(i["expiry"]) == exp and float(i.get("strike") or 0) > 0})
            if not listed:
                continue
            atm = min(listed, key=lambda s: abs(s - spot)) if spot else listed[len(listed) // 2]
            i0 = listed.index(atm)
            want = set(listed[max(0, i0 - c["strikes"]): i0 + c["strikes"] + 1])
            for i in opts:
                if _as_date(i["expiry"]) == exp and float(i.get("strike") or 0) in want:
                    tasks.append({"kind": "options", "underlying": u, "token": int(i["instrument_token"]),
                                  "label": i.get("tradingsymbol"), "oi": True, "expiry": exp.isoformat(),
                                  "strike": float(i["strike"]), "option_type": i["instrument_type"]})
    return tasks, notes


def _summarise(tasks: list[dict]) -> list[dict]:
    out: dict[tuple, dict] = {}
    for t in tasks:
        row = out.setdefault((t["underlying"], t["kind"]),
                             {"underlying": t["underlying"], "kind": t["kind"], "series": 0, "expiries": set()})
        row["series"] += 1
        if t.get("expiry"):
            row["expiries"].add(t["expiry"])
    return [{**r, "expiries": sorted(r["expiries"])} for r in sorted(out.values(), key=lambda r: (r["underlying"], r["kind"]))]


def window(c: dict) -> tuple[date, date]:
    today = IX.now_ist().date()
    return today - timedelta(days=c["days"] - 1), today


def plan(broker, cfg: Optional[dict] = None) -> dict:
    """What a pull would fetch right now — nothing is downloaded here."""
    c = config(cfg)
    tasks, notes = build_tasks(broker, c)
    start, end = window(c)
    return {
        "status": "ok", "config": c, "from": start.isoformat(), "to": end.isoformat(),
        "tasks": len(tasks), "seconds_estimate": round(len(tasks) * CALL_SPACING_S + 5),
        "by_series": _summarise(tasks), "notes": notes, "last_pull": last_pull(),
        "limits": {"max_days": MAX_DAYS},
        "note": "Only contracts Zerodha still lists can be pulled; expired ones come from file uploads.",
    }


# ── fetching ─────────────────────────────────────────────────────────
def _fetch(broker, token: int, start: date, end: date, oi: bool) -> pd.DataFrame:
    rows = []
    for ch in build_chunks(start, end, "minute"):
        frm = datetime.combine(date.fromisoformat(ch["start"]), datetime.min.time()) + timedelta(hours=9, minutes=15)
        to = datetime.combine(date.fromisoformat(ch["end"]), datetime.min.time()) + timedelta(hours=15, minutes=30)
        t0 = time.monotonic()
        try:
            rows.extend(broker.get_historical_data(token, frm, to, "minute", oi=oi) or [])
        finally:
            time.sleep(max(0.0, CALL_SPACING_S - (time.monotonic() - t0)))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    ts = pd.to_datetime(df["date"], errors="coerce", utc=True)
    df["timestamp"] = ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return df.drop(columns=["date"])


def run(broker, cfg: Optional[dict] = None, progress: Optional[Callable[[str], None]] = None,
        user_id: int = 0) -> dict:
    """Fetch every listed series and merge it into the Market Store."""
    from research.market_store import service as SV
    say = progress or (lambda _m: None)
    c = config(cfg)
    tasks, notes = build_tasks(broker, c)
    start, end = window(c)
    if not tasks:
        return {"status": "error", "message": "nothing to pull — is Zerodha connected?", "notes": notes}
    say(f"{len(tasks)} series · {start} → {end}")
    index_bars: dict[str, pd.DataFrame] = {}
    stored: list[dict] = []
    failures: list[str] = []
    # index series first: their prices fill the `spot` column of the option bars
    ordered = sorted(tasks, key=lambda t: (not t.get("is_index"), t["kind"] != "spot"))
    for n, t in enumerate(ordered, 1):
        say(f"{n}/{len(ordered)} · {t['label']}")
        try:
            df = _fetch(broker, t["token"], start, end, t["oi"])
        except Exception as exc:
            failures.append(f"{t['label']}: {str(exc)[:90]}")
            continue
        if df.empty:
            continue
        if t["kind"] == "spot":
            cols = ["timestamp", "open", "high", "low", "close"] + (["volume"] if "volume" in df else [])
            frame = df[cols]
            if t.get("is_index"):
                index_bars[t["underlying"]] = df[["timestamp", "close"]].rename(columns={"close": "spot"})
        else:
            frame = df.assign(strike=t["strike"], option_type=t["option_type"], expiry_date=t["expiry"])
            ref = index_bars.get(t["underlying"])
            if ref is not None:
                frame = frame.merge(ref, on="timestamp", how="left")
        res = SV.ingest_frame(frame, filename=f"zerodha-pull · {t['label']}", kind=t["kind"],
                              underlying=t["underlying"], user_id=user_id)
        if res.get("status") != "ok":
            failures.append(f"{t['label']}: {res.get('message')}"[:140])
            continue
        stored.append({"underlying": t["underlying"], "kind": t["kind"],
                       "rows": int((res.get("report") or {}).get("rows_out", 0)), "added": int(res.get("rows_added", 0))})
    merged: dict[tuple, dict] = {}
    for r in stored:
        m = merged.setdefault((r["underlying"], r["kind"]),
                              {"underlying": r["underlying"], "kind": r["kind"], "series": 0, "rows": 0, "added": 0})
        m["series"] += 1
        m["rows"] += r["rows"]
        m["added"] += r["added"]
    if not any(t.get("is_index") for t in ordered):
        notes.append("Index bars were not pulled, so option rows carry no spot price; the OI study then "
                     "falls back to whatever index file is already stored.")
    _refresh_studies({r["underlying"] for r in stored})
    out = {"status": "ok", "at": IX.now_ist().isoformat(sep=" ", timespec="seconds"),
           "from": start.isoformat(), "to": end.isoformat(), "config": c,
           "series": sorted(merged.values(), key=lambda r: (r["underlying"], r["kind"])),
           "series_count": len(stored), "total_rows": sum(m["rows"] for m in merged.values()),
           "total_added": sum(m["added"] for m in merged.values()),
           "failures": failures, "notes": notes}
    _save_state(out)
    logger.info("zerodha pull: %d series, %d rows stored (%d new)%s", len(stored), out["total_rows"],
                out["total_added"], f", {len(failures)} failed" if failures else "")
    return out


def _refresh_studies(underlyings) -> None:
    """Rebuild the OI study of every underlying whose data just changed."""
    try:
        from research.oi_lab import history as HS
        available = set(HS.available_underlyings())
        for u in underlyings:
            if u in available:
                HS.ensure_started(u, force=True)
    except Exception as exc:
        logger.debug("study refresh skipped: %s", exc)
