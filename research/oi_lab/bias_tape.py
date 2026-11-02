"""
A recording of the OI Lab's "who is in control" score, minute by minute.

The X-Ray computes that score from the live chain and then discards it: refresh and the
previous reading is gone, so there is no way to ask whether the score led price, lagged it,
or simply tracked it. This module keeps the tape.

How it relates to the OI Lab: it READS. It calls ``OILabLive.snapshot`` — the same public,
cached, lock-protected entry point the X-Ray screen uses — and stores the number that comes
back. It computes no score of its own, on purpose: a second implementation would drift from
the one on screen and the tape would quietly stop describing the thing it is named after.
Nothing in ``oi_lab`` is modified by this file.

Resolution. One row per index per minute, and nothing else. The 5, 15, 30 and 60-minute views
are aggregated from those rows when they are asked for. Writing each timeframe separately
would be four more chances for the coarse series to disagree with the fine one, in exchange
for nothing — a bucket is derivable, so it is derived.

Autosaving. ``record`` is called from the background loop that already runs during market
hours. It rate-limits itself to one write per index per minute and upserts on
``(index, date, minute)``, so a loop ticking every second costs one write a minute and
repeating a minute corrects it rather than duplicating it.
"""
from __future__ import annotations

import threading
from datetime import date, datetime
from typing import Iterable, Optional

from core.logger import get_logger
from research.oi_lab import indices as IX

logger = get_logger("research.oi_lab.bias_tape")

# Recording every listed index would mean a chain build per index per minute. These two are
# the ones that are actually traded here; the rest can be added per call when wanted.
DEFAULT_INDICES = ("NIFTY", "SENSEX")

TIMEFRAMES = (1, 5, 15, 30, 60)
SESSION_OPEN = IX.SESSION_OPEN_MIN          # 09:15
SESSION_CLOSE = IX.SESSION_CLOSE_MIN        # 15:30

_last_write: dict[tuple[str, str], int] = {}
_lock = threading.Lock()


def _f(v) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return f if f == f and abs(f) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _should_write(index_key: str, day: date, minute: int) -> bool:
    """One write per index per minute, decided in memory before touching the database."""
    key = (index_key, day.isoformat())
    with _lock:
        if _last_write.get(key) == minute:
            return False
        _last_write[key] = minute
        return True


def record(db, broker, service, indices: Iterable[str] = DEFAULT_INDICES,
           at: Optional[datetime] = None) -> dict:
    """Capture the current score for each index. Safe to call every second.

    Returns what it did rather than raising: this runs inside the shared background loop and
    must never be able to take the loop down with it.
    """
    from core.models import OIBiasTick

    now = at or IX.now_ist()
    day, minute = now.date(), now.hour * 60 + now.minute
    out = {"written": [], "skipped": [], "errors": []}

    if broker is None or service is None:
        out["skipped"].append("no broker")
        return out
    if not (SESSION_OPEN <= minute <= SESSION_CLOSE) or now.weekday() >= 5:
        out["skipped"].append("outside the session")
        return out

    for key in indices:
        try:
            k = IX.get(key)["key"]
        except ValueError:
            out["errors"].append(f"{key}: not a known index")
            continue
        if not _should_write(k, day, minute):
            out["skipped"].append(f"{k}: already have {minute // 60:02d}:{minute % 60:02d}")
            continue
        try:
            snap = service.snapshot(broker, k)
            if snap.get("status") != "ok":
                out["errors"].append(f"{k}: {snap.get('message') or 'no snapshot'}")
                continue
            bias = ((snap.get("analysis") or {}).get("bias") or {})
            score = _f(bias.get("score"))
            if score is None:
                out["errors"].append(f"{k}: snapshot carried no score")
                continue
            tot = (snap.get("analysis") or {}).get("totals") or {}
            exp = snap.get("expiry")
            row = (db.query(OIBiasTick)
                     .filter(OIBiasTick.index_key == k,
                             OIBiasTick.trading_date == day,
                             OIBiasTick.minute == minute).first())
            if row is None:
                row = OIBiasTick(index_key=k, trading_date=day, minute=minute)
                db.add(row)
            row.captured_at = datetime.utcnow()
            row.score = score
            row.label = bias.get("label")
            row.spot = _f(snap.get("spot"))
            row.spot_change_pct = _f(snap.get("spot_change_pct"))
            row.pcr_oi = _f(tot.get("pcr_oi"))
            row.max_pain = _f(snap.get("analysis", {}).get("max_pain"))
            row.straddle = _f(snap.get("straddle"))
            row.atm_iv = _f(snap.get("atm_iv"))
            row.expiry = date.fromisoformat(exp) if exp else None
            row.dte = snap.get("dte")
            row.components = bias.get("components") or []
            db.commit()
            out["written"].append({"index": k, "minute": minute, "score": score})
        except Exception as exc:
            db.rollback()
            # a failed minute is a gap in the tape, never a broken loop
            with _lock:
                _last_write.pop((k, day.isoformat()), None)
            out["errors"].append(f"{k}: {type(exc).__name__}: {str(exc)[:120]}")
            logger.debug("bias tape write failed for %s: %s", k, exc)
    return out


# ── reading ─────────────────────────────────────────────────────────────────
def _bucket(minute: int, tf: int) -> int:
    """Anchor buckets on the session open, so a 15-minute bar is 09:15-09:29.

    Anchoring on midnight instead would put the first bar at 09:15-09:29 only by accident and
    would break entirely for 30 and 60 minutes.
    """
    if tf <= 1:
        return minute
    return SESSION_OPEN + ((minute - SESSION_OPEN) // tf) * tf


def sessions(db, index_key: str = "NIFTY", limit: int = 60) -> dict:
    """Which dates have a tape, newest first — what the date picker offers."""
    from sqlalchemy import func
    from core.models import OIBiasTick
    try:
        q = (db.query(OIBiasTick.trading_date,
                      func.count(OIBiasTick.id),
                      func.min(OIBiasTick.minute),
                      func.max(OIBiasTick.minute))
               .filter(OIBiasTick.index_key == index_key.upper())
               .group_by(OIBiasTick.trading_date)
               .order_by(OIBiasTick.trading_date.desc())
               .limit(max(1, min(int(limit), 400))))
        rows = [{"trading_date": d.isoformat(), "minutes": int(n),
                 "from": f"{int(a) // 60:02d}:{int(a) % 60:02d}",
                 "to": f"{int(b) // 60:02d}:{int(b) % 60:02d}"}
                for d, n, a, b in q.all()]
    except Exception as exc:
        logger.debug("bias tape sessions failed: %s", exc)
        return {"available": False, "sessions": [], "message": str(exc)[:160]}
    return {"available": True, "sessions": rows, "count": len(rows),
            "index": index_key.upper()}


def series(db, index_key: str = "NIFTY", day: Optional[str] = None,
           timeframe: int = 1) -> dict:
    """The tape for one session at one resolution, with the index price beside it.

    Each bucket reports the score the way a candle reports price — first, high, low, last —
    because an average alone hides the swing that makes a bucket interesting. ``spot`` is the
    last price in the bucket, so the two series line up on the same instant.
    """
    from core.models import OIBiasTick
    tf = int(timeframe) if int(timeframe) in TIMEFRAMES else 1
    idx = (index_key or "NIFTY").upper()

    try:
        if day:
            d = date.fromisoformat(day)
        else:
            latest = (db.query(OIBiasTick.trading_date)
                        .filter(OIBiasTick.index_key == idx)
                        .order_by(OIBiasTick.trading_date.desc()).first())
            if not latest:
                return {"available": False, "index": idx, "timeframe": tf, "bars": [],
                        "message": (f"Nothing recorded for {idx} yet. The tape fills while "
                                    f"the market is open and Zerodha is connected — it "
                                    f"cannot be backfilled, because the score is computed "
                                    f"from the live chain and that chain is gone once the "
                                    f"minute has passed.")}
            d = latest[0]
        rows = (db.query(OIBiasTick)
                  .filter(OIBiasTick.index_key == idx, OIBiasTick.trading_date == d)
                  .order_by(OIBiasTick.minute.asc()).all())
    except Exception as exc:
        logger.debug("bias tape series failed: %s", exc)
        return {"available": False, "index": idx, "timeframe": tf, "bars": [],
                "message": str(exc)[:160]}

    if not rows:
        return {"available": False, "index": idx, "timeframe": tf, "bars": [],
                "trading_date": d.isoformat(),
                "message": f"No {idx} tape recorded for {d.isoformat()}."}

    buckets: dict[int, list] = {}
    for r in rows:
        buckets.setdefault(_bucket(int(r.minute), tf), []).append(r)

    bars = []
    for m in sorted(buckets):
        grp = buckets[m]
        scores = [float(x.score) for x in grp if x.score is not None]
        spots = [float(x.spot) for x in grp if x.spot is not None]
        if not scores:
            continue
        last = grp[-1]
        bars.append({
            "minute": m, "time": f"{m // 60:02d}:{m % 60:02d}",
            "ticks": len(grp),
            "score_open": round(scores[0], 1), "score_close": round(scores[-1], 1),
            "score_high": round(max(scores), 1), "score_low": round(min(scores), 1),
            "score_avg": round(sum(scores) / len(scores), 1),
            "label": last.label,
            "spot": round(spots[-1], 2) if spots else None,
            "spot_open": round(spots[0], 2) if spots else None,
            "spot_change_pct": _f(last.spot_change_pct),
            "pcr_oi": _f(last.pcr_oi), "max_pain": _f(last.max_pain),
            "straddle": _f(last.straddle), "atm_iv": _f(last.atm_iv),
            "dte": last.dte,
        })

    for i, b in enumerate(bars):
        prev = bars[i - 1] if i else None
        b["score_change"] = (round(b["score_close"] - prev["score_close"], 1)
                             if prev else None)
        b["spot_change"] = (round(b["spot"] - prev["spot"], 2)
                            if prev and b["spot"] is not None and prev["spot"] is not None
                            else None)

    closes = [b["score_close"] for b in bars]
    spots = [b["spot"] for b in bars if b["spot"] is not None]
    hi = max(bars, key=lambda b: b["score_close"])
    lo = min(bars, key=lambda b: b["score_close"])
    flips = sum(1 for i in range(1, len(closes))
                if (closes[i] > 0) != (closes[i - 1] > 0))

    return {
        "available": True, "index": idx, "timeframe": tf,
        "trading_date": d.isoformat(),
        "bars": bars, "count": len(bars), "ticks": len(rows),
        "summary": {
            "open": closes[0], "close": closes[-1],
            "high": hi["score_close"], "high_at": hi["time"],
            "low": lo["score_close"], "low_at": lo["time"],
            "avg": round(sum(closes) / len(closes), 1),
            "sign_flips": flips,
            "spot_open": spots[0] if spots else None,
            "spot_close": spots[-1] if spots else None,
            "spot_move_pct": (round((spots[-1] - spots[0]) / spots[0] * 100, 2)
                              if len(spots) >= 2 and spots[0] else None),
            "correlation": _correlation(bars),
        },
        "note": ("The score is read from the OI Lab's own X-Ray, not recomputed here. Each "
                 "row is one minute as it was seen live; coarser timeframes are grouped from "
                 "those same rows, so they cannot disagree with the one-minute series."),
    }


def _correlation(bars: list[dict]) -> Optional[dict]:
    """How the score moved against the index, bar to bar.

    Deliberately on CHANGES rather than levels: two series that both drift upward correlate
    on levels whatever they do in between, which would flatter the score. It is also only
    a description of the session on screen — not evidence the score predicts anything.
    """
    pairs = [(b["score_change"], b["spot_change"]) for b in bars
             if b.get("score_change") is not None and b.get("spot_change") is not None]
    if len(pairs) < 5:
        return None
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    n = len(pairs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in pairs)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    r = sxy / ((sxx ** 0.5) * (syy ** 0.5))
    same = sum(1 for x, y in pairs if (x > 0) == (y > 0))
    return {"r": round(r, 3), "bars": n,
            "same_direction_pct": round(100.0 * same / n, 1),
            "note": ("Correlation of bar-to-bar CHANGES in the score against changes in the "
                     "index, for this session only. It describes what happened; it is not "
                     "evidence that the score leads price.")}


def purge(db, index_key: Optional[str] = None, before: Optional[str] = None) -> dict:
    """Delete recorded sessions. Used only when explicitly asked from the UI."""
    from core.models import OIBiasTick
    try:
        q = db.query(OIBiasTick)
        if index_key:
            q = q.filter(OIBiasTick.index_key == index_key.upper())
        if before:
            q = q.filter(OIBiasTick.trading_date < date.fromisoformat(before))
        n = q.delete(synchronize_session=False)
        db.commit()
        return {"deleted": int(n)}
    except Exception as exc:
        db.rollback()
        return {"deleted": 0, "error": str(exc)[:160]}
