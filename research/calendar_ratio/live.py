"""
Today's calendar ratio structure, from the broker — NIFTY or SENSEX.

This is the part that works regardless of what the store holds: Zerodha lists the full chain
for every live expiry, so the monthly leg and the deep-in-the-money legs that history cannot
price are all quotable right now.

It reads instruments and quotes and nothing else. There is no order path in this module at
all — not a disabled one, not a guarded one. Sizing a short-premium structure is a decision
that belongs to the person holding the account.

The index registry is imported for its exchange and strike-step constants only, so SENSEX
resolves to BFO and NIFTY to NFO without this module knowing anything about either. The OI
Lab is not touched.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import numpy as np
import pandas as pd

from core.logger import get_logger
from research.calendar_ratio import structure as ST
from research.oi_lab import indices as IX

logger = get_logger("research.calendar_ratio.live")

# How far either side of spot to quote. A leg asking for a 450 premium sits well inside the
# money, so a narrow window would simply fail to find it — the usual +/- 10 strikes is not
# enough for this structure.
DEFAULT_WINDOW = 40
MAX_QUOTE = 450              # Kite accepts a few hundred instruments per quote call


def _instruments(broker, index: str) -> list[dict]:
    cfg = IX.get(index)
    out = []
    for inst in broker.get_instruments(cfg["exch"]) or []:
        if inst.get("name") != cfg["key"] or inst.get("instrument_type") not in ("CE", "PE"):
            continue
        exp = inst.get("expiry")
        if isinstance(exp, str):
            try:
                exp = datetime.fromisoformat(exp[:10]).date()
            except Exception:
                exp = None
        elif isinstance(exp, datetime):
            exp = exp.date()
        if not isinstance(exp, date):
            continue
        out.append({"tradingsymbol": inst.get("tradingsymbol"),
                    "token": int(inst["instrument_token"]),
                    "strike": float(inst.get("strike") or 0),
                    "option_type": inst["instrument_type"],
                    "expiry_date": exp,
                    "lot_size": int(inst.get("lot_size") or 0),
                    "exch": cfg["exch"]})
    return out


def _spot(broker, index: str) -> Optional[float]:
    cfg = IX.get(index)
    try:
        q = broker.get_ltp([cfg["spot"]]) or {}
        if q:
            v = list(q.values())[0]
            return float(v.get("last_price") if isinstance(v, dict) else v)
    except Exception as exc:
        logger.debug("spot quote failed for %s: %s", index, exc)
    return None


def _quote(broker, rows: list[dict]) -> dict:
    """Last price per tradingsymbol, in batches the broker will accept."""
    out: dict[str, float] = {}
    keys = [f"{r['exch']}:{r['tradingsymbol']}" for r in rows]
    for i in range(0, len(keys), MAX_QUOTE):
        chunk = keys[i:i + MAX_QUOTE]
        try:
            q = broker.get_ltp(chunk) or {}
        except Exception as exc:
            logger.debug("quote batch failed: %s", exc)
            continue
        for k, v in q.items():
            px = v.get("last_price") if isinstance(v, dict) else v
            try:
                out[str(k).split(":", 1)[-1]] = float(px)
            except Exception:
                continue
    return out


def chain_now(broker, p: ST.Params, window: int = DEFAULT_WINDOW) -> dict:
    """The live chain for the two expiries this structure needs."""
    cfg = IX.get(p.index)
    insts = _instruments(broker, p.index)
    if not insts:
        return {"ok": False,
                "message": (f"No {cfg['label']} options are listed on {cfg['exch']}. If this "
                            f"is SENSEX, check that your Zerodha account has BSE F&O enabled.")}

    today = IX.now_ist().date()
    avail = sorted({i["expiry_date"] for i in insts if i["expiry_date"] >= today})
    exp = ST.resolve_expiries(avail, today, p.same_expiry_policy)
    if not exp["weekly"]:
        return {"ok": False, "message": exp.get("reason") or "no expiry listed", "expiries": exp}

    spot = _spot(broker, p.index)
    if not spot:
        return {"ok": False, "message": f"No quote for {cfg['spot']} right now.",
                "expiries": exp}

    step = float(cfg["step"])
    lo, hi = spot - window * step, spot + window * step
    want = [i for i in insts
            if i["expiry_date"] in (exp["weekly"], exp["monthly"]) and lo <= i["strike"] <= hi]
    if not want:
        return {"ok": False, "message": "no strikes listed in the window around spot",
                "expiries": exp}

    px = _quote(broker, want)
    rows = []
    for i in want:
        v = px.get(i["tradingsymbol"])
        if v is None or v <= 0:
            continue
        rows.append({**i, "contract": i["tradingsymbol"], "premium": float(v)})
    if not rows:
        return {"ok": False, "message": "the broker returned no prices for those contracts",
                "expiries": exp}

    return {"ok": True, "chain": pd.DataFrame(rows), "spot": spot, "expiries": exp,
            "today": today, "cfg": cfg,
            "lot_size": int(pd.DataFrame(rows)["lot_size"].mode().iloc[0] or 0),
            "quoted": len(rows), "window": window}


def desk(broker, p: Optional[ST.Params] = None, window: int = DEFAULT_WINDOW) -> dict:
    """The structure as it stands right now, leg by leg."""
    p = p or ST.Params()
    if broker is None:
        return {"available": False,
                "message": "Connect Zerodha — the live chain comes from the broker."}
    try:
        cfg = IX.get(p.index)
    except ValueError as exc:
        return {"available": False, "message": str(exc)}

    c = chain_now(broker, p, window)
    if not c.get("ok"):
        return {"available": False, "message": c["message"],
                "expiries": _exp_out(c.get("expiries")), "index": p.index.upper()}

    built = ST.build(c["chain"], c["today"], p)
    lot = c["lot_size"] or 0
    now = IX.now_ist()
    now_min = now.hour * 60 + now.minute

    legs = []
    for l in built["legs"]:
        n = l["lots"] * lot
        legs.append({**l, "lot_size": lot, "qty": n,
                     "value_rs": round((l["premium"] or 0) * n, 0) if l["premium"] else None})

    out = {
        "available": True,
        "index": p.index.upper(), "label": cfg["label"], "exchange": cfg["exch"],
        "spot": round(c["spot"], 2),
        "as_of": now.strftime("%H:%M"),
        "date": str(c["today"]),
        "lot_size": lot,
        "expiries": _exp_out(c["expiries"]),
        "legs": legs,
        "tradable": built["tradable"],
        "blockers": built["blockers"],
        "entry_window": _entry_window(now_min, p),
        "rules": ST.describe(p),
        "quoted_contracts": c["quoted"],
        "note": ("Premiums are live quotes, not fills. This page places no orders — the "
                 "structure is short two near-dated options and long three far-dated ones on "
                 "each side, and sizing it is your decision."),
    }
    if built["tradable"]:
        s = ST.summarize(built["legs"], lot)
        out.update({k: v for k, v in s.items()})
        out["net_premium_rs"] = round(s["net_premium"] * lot, 0) if lot else None
    return out


def _exp_out(e: Optional[dict]) -> Optional[dict]:
    if not e:
        return None
    return {k: (str(v) if isinstance(v, date) else v) for k, v in e.items()}


def _entry_window(now_min: int, p: ST.Params) -> dict:
    want = p.entry_minute
    hhmm = f"{want // 60:02d}:{want % 60:02d}"
    if now_min < want:
        return {"state": "before", "entry_at": hhmm,
                "message": f"The structure is specified for {hhmm}. These are the legs it "
                           f"would pick on the prices showing now."}
    if now_min <= want + 5:
        return {"state": "now", "entry_at": hhmm, "message": f"Entry window, {hhmm}."}
    return {"state": "after", "entry_at": hhmm,
            "message": f"Past {hhmm}. These legs reflect prices now, which are not the prices "
                       f"the {hhmm} entry would have seen."}
