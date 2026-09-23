"""
Hunter — one pass over the universe.

Every stock is measured the same way (``patterns.classify``), then relative strength is ranked
across everything scanned that day, so an RS rating means "better than this % of the NIFTY 500
today" rather than an abstract number. Illiquid names are dropped by turnover, not by opinion.

The result is a board — how many stocks sit in each stage — the rows behind it, and a diff against
the previous scan: what broke out, what came onto the watch list, what lost its stop.
"""
from __future__ import annotations

from datetime import date
from typing import Callable, Optional

import pandas as pd

from core.logger import get_logger
from research.hunter import data as DATA
from research.hunter import patterns as PT
from research.hunter import store as STORE
from research.hunter import universe as UNIV

logger = get_logger("research.hunter.scan")

STAGES = ["FORMING", "BREAKOUT", "CLIMBING", "PLAYED_OUT"]
STAGE_LABEL = {"FORMING": "Forming", "BREAKOUT": "Fresh breakout",
               "CLIMBING": "Climbing", "PLAYED_OUT": "Played out"}
STAGE_BLURB = {
    "FORMING": "resting in a tight range under a ceiling",
    "BREAKOUT": "cleared the ceiling in the last 5 sessions",
    "CLIMBING": "broke out earlier, still above its stop",
    "PLAYED_OUT": "broke out and has since lost its stop",
}
MIN_RS = 65.0                   # a forming base is only interesting in a leading stock


def run(broker, progress: Optional[Callable[[str], None]] = None, refresh_universe: bool = False,
        min_rs: float = MIN_RS, params: PT.Params = PT.P, universe: str = "nifty500") -> dict:
    say = progress or (lambda _m: None)
    say("every liquid NSE stock" if universe == "nse_liquid" else "loading the NIFTY 500 list")
    stocks, missing = UNIV.with_tokens(broker, refresh_universe, universe)
    meta_u = UNIV.load()
    if not stocks:
        return {"status": "error", "message": meta_u.get("note") or "could not build the universe"}
    say(f"{len(stocks)} stocks · fetching daily candles")
    bars, skipped = DATA.load_all(broker, stocks, say)

    say("measuring")
    rows = []
    for s in stocks:
        df = bars.get(s["symbol"])
        if df is None:
            continue
        try:
            d = PT.indicators(df)
            c = PT.classify(d, params)
        except Exception as exc:                      # one odd stock can never end the whole scan
            logger.warning("hunter: %s could not be measured: %s", s["symbol"], exc)
            skipped.append({"symbol": s["symbol"], "reason": f"{type(exc).__name__}: {exc}"[:120]})
            continue
        rows.append({**{k: s[k] for k in ("symbol", "name", "industry", "token", "exchange")}, **c,
                     "date": str(pd.Timestamp(d["date"].iloc[-1]).date())})

    # relative strength as a percentile of everything measured today
    raw = pd.Series([r.get("rs_raw") for r in rows], dtype="float64")
    pct = raw.rank(pct=True) * 100
    for r, p in zip(rows, pct):
        r["rs_rating"] = int(round(p)) if p == p else None

    for r in rows:
        if r.get("turnover_cr") is not None and r["turnover_cr"] < params.min_turnover_cr:
            r["stage"], r["why"] = "NONE", f"too thin to trade ({r['turnover_cr']:.1f} cr a day)"
        elif r["stage"] == "FORMING" and (r.get("rs_rating") or 0) < min_rs:
            r["stage"] = "NONE"
            r["why"] = f"base is fine but relative strength is only {r.get('rs_rating')} — not a leader"

    for r in rows:
        sc = r.get("screens") or {}
        sc["base_break"] = {"hit": r["stage"] in ("FORMING", "BREAKOUT"), "why": r.get("why", "")}
        r["screens"] = sc
        r["screen_hits"] = sorted(k for k, v in sc.items() if v.get("hit"))

    scan_date = max((r["date"] for r in rows), default=date.today().isoformat())
    prev_rows, prev_meta = STORE.previous(scan_date)
    changes = diff(rows, prev_rows)
    meta = {
        "scan_date": scan_date, "ran_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "universe": {"mode": universe,
                     "source": "every NSE equity (instrument dump)" if universe == "nse_liquid" else meta_u.get("source"),
                     "fetched": meta_u.get("fetched"),
                     "count": len(stocks), "note": meta_u.get("note"), "unresolved": missing[:20]},
        "scanned": len(rows), "skipped": len(skipped),
        "skipped_detail": skipped[:20], "min_rs": min_rs, "params": params.as_dict(),
        "board": board(rows), "compared_with": prev_meta.get("scan_date"),
        "screens": {k: {**v, "count": sum(1 for r in rows if k in (r.get("screen_hits") or []))}
                    for k, v in PT.SCREENS.items()},
    }
    STORE.save(rows, {**meta, "changes": changes})
    return {"status": "ok", "meta": meta, "rows": rows, "changes": changes}


def board(rows: list[dict]) -> list[dict]:
    counts = {s: 0 for s in STAGES}
    for r in rows:
        if r["stage"] in counts:
            counts[r["stage"]] += 1
    return [{"stage": s, "label": STAGE_LABEL[s], "blurb": STAGE_BLURB[s], "count": counts[s]} for s in STAGES]


def diff(rows: list[dict], prev: list[dict]) -> list[dict]:
    """What changed since the previous scan, in the order that matters."""
    before = {r["symbol"]: r for r in prev}
    out = []
    for r in rows:
        was = (before.get(r["symbol"]) or {}).get("stage")
        if r["stage"] == was:
            continue
        if r["stage"] == "BREAKOUT":
            bo = r.get("breakout") or {}
            out.append({"kind": "broke_out", "symbol": r["symbol"], "name": r["name"], "close": r["close"],
                        "change_pct": r.get("extended_pct"), "rs": r.get("rs_rating"),
                        "text": (f"broke out. Closed ₹{r['close']:,.1f} — {r.get('extended_pct', 0):.1f}% above the "
                                 f"₹{bo.get('pivot', 0):,.1f} ceiling, on {bo.get('volume_x', 0)}× its usual trading.")})
        elif r["stage"] == "FORMING" and was != "FORMING":
            b = r.get("base") or {}
            out.append({"kind": "now_forming", "symbol": r["symbol"], "name": r["name"], "close": r["close"],
                        "rs": r.get("rs_rating"),
                        "text": (f"came onto the watch list — a {b.get('length')}-session base, "
                                 f"{abs(b.get('from_pivot_pct', 0)):.1f}% under ₹{b.get('pivot', 0):,.1f}.")})
        elif r["stage"] == "PLAYED_OUT" and was in ("BREAKOUT", "CLIMBING"):
            bo = r.get("breakout") or {}
            out.append({"kind": "played_out", "symbol": r["symbol"], "name": r["name"], "close": r["close"],
                        "rs": r.get("rs_rating"),
                        "text": (f"lost its stop at ₹{bo.get('stop', 0):,.1f} after {bo.get('gain_pct', 0):+.1f}% "
                                 f"from the breakout.")})
    order = {"broke_out": 0, "now_forming": 1, "played_out": 2}
    return sorted(out, key=lambda x: (order.get(x["kind"], 9), -(x.get("rs") or 0)))
