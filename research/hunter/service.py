"""
Hunter — jobs, settings, filtering and the daily automatic scan.

The scan is one background job (the same polling contract the other labs use) because a first run
fetches a year of candles for 500 stocks. Afterwards it is incremental and quick, and the daily
run happens by itself after the close, so the board is ready when you open the page.
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from datetime import date, datetime
from typing import Callable, Optional

from core.logger import get_logger
from core.models import StrategyConfig
from research.hunter import scan as SCAN
from research.hunter import store as STORE

logger = get_logger("research.hunter.service")

CONFIG_NAME = "hunter"
DEFAULTS = {"auto_scan": True, "min_rs": 65, "scan_after_min": 16 * 60,   # 16:00 IST
            "universe": "nse_liquid",        # every ordinary NSE equity; "nifty500" for the index only
            "base_max_depth": 35.0, "near_pivot_pct": 20.0, "base_min": 10, "base_max": 60,
            "strict_base": False, "min_turnover_cr": 2.0}
_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_last_auto: dict[int, str] = {}


# ── settings ──
def load_config(db, user_id: int) -> dict:
    row = (db.query(StrategyConfig)
             .filter(StrategyConfig.user_id == user_id, StrategyConfig.strategy_name == CONFIG_NAME).first())
    return {**DEFAULTS, **((row.config or {}) if row else {})}


def save_config(db, user_id: int, updates: dict) -> dict:
    clean = {k: v for k, v in (updates or {}).items() if k in DEFAULTS and v is not None}
    cfg = {**load_config(db, user_id), **clean}
    row = (db.query(StrategyConfig)
             .filter(StrategyConfig.user_id == user_id, StrategyConfig.strategy_name == CONFIG_NAME).first())
    if row is None:
        db.add(StrategyConfig(user_id=user_id, strategy_name=CONFIG_NAME, config=cfg))
    else:
        row.config = cfg
    db.commit()
    return load_config(db, user_id)


# ── jobs ──
def params_from(cfg: dict):
    """The tuning the user has set, as the parameter object the scan understands."""
    from research.hunter import patterns as PT
    keys = ("base_max_depth", "near_pivot_pct", "base_min", "base_max", "strict_base", "min_turnover_cr")
    return PT.Params(**{k: type(getattr(PT.P, k))(cfg[k]) for k in keys if cfg.get(k) is not None})


def start_scan(broker, user_id: int, min_rs: float = SCAN.MIN_RS, refresh_universe: bool = False,
               cfg: Optional[dict] = None) -> dict:
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "user_id": user_id, "status": "running", "progress": "starting",
           "started": time.time(), "result": None, "error": None}
    with _lock:
        _jobs[jid] = job
        for old in [k for k, v in _jobs.items() if v["status"] != "running" and time.time() - v["started"] > 6 * 3600]:
            _jobs.pop(old, None)

    def work():
        try:
            c = cfg or {}
            res = SCAN.run(broker, lambda m: job.__setitem__("progress", m), refresh_universe, min_rs,
                           params_from(c), c.get("universe", "nifty500"))
            job["result"] = {"status": res.get("status"), "meta": res.get("meta"), "message": res.get("message")}
            job["status"] = "done" if res.get("status") == "ok" else "error"
            job["error"] = res.get("message")
        except Exception as exc:
            logger.error("hunter scan failed: %s | %s", exc, traceback.format_exc())
            job["status"], job["error"] = "error", str(exc)[:400]
        finally:
            job["seconds"] = round(time.time() - job["started"], 1)

    threading.Thread(target=work, daemon=True, name=f"hunter-scan-{jid}").start()
    return job


def start_evidence(user_id: int, years: int = 5) -> dict:
    """Backtest every screen over the cached history — no broker needed, so it can run any time."""
    from research.hunter import evidence as EVID
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "user_id": user_id, "status": "running", "progress": "starting",
           "started": time.time(), "result": None, "error": None}
    with _lock:
        _jobs[jid] = job

    def work():
        try:
            res = EVID.run(lambda m: job.__setitem__("progress", m), years=years)
            job["result"] = {"status": res.get("status"), "message": res.get("message")}
            job["status"] = "done" if res.get("status") == "ok" else "error"
            job["error"] = res.get("message") if res.get("status") != "ok" else None
        except Exception as exc:
            logger.error("hunter evidence failed: %s | %s", exc, traceback.format_exc())
            job["status"], job["error"] = "error", str(exc)[:400]
        finally:
            job["seconds"] = round(time.time() - job["started"], 1)

    threading.Thread(target=work, daemon=True, name=f"hunter-evidence-{jid}").start()
    return job


def get_job(job_id: str, user_id: int) -> Optional[dict]:
    j = _jobs.get(job_id)
    return j if j and j["user_id"] == user_id else None


def scanning() -> bool:
    return any(j["status"] == "running" for j in _jobs.values())


# ── reading the board ──
def latest(stage: Optional[str] = None, industry: Optional[str] = None, q: Optional[str] = None,
           sort: str = "rs_rating", limit: int = 400, scan_date: Optional[str] = None,
           screens: Optional[list[str]] = None, combine: str = "any") -> dict:
    rows, meta = STORE.load(scan_date)
    if not rows:
        return {"status": "ok", "empty": True, "board": [], "rows": [], "meta": {},
                "message": "No scan yet — run one to fill the board."}
    picked = [s for s in (screens or []) if s]
    if picked:
        def matches(r):
            hits = set(r.get("screen_hits") or [])
            return hits.issuperset(picked) if combine == "all" else bool(hits & set(picked))
        shown = [r for r in rows if matches(r)]
    else:
        shown = [r for r in rows if r.get("stage") != "NONE"]
    if stage and not picked:
        shown = [r for r in shown if r["stage"] == stage.upper()]
    if industry and industry != "All industries":
        shown = [r for r in shown if (r.get("industry") or "Unclassified") == industry]
    if q:
        s = q.strip().upper()
        shown = [r for r in shown if s in r["symbol"] or s in (r.get("name") or "").upper()]
    keys = {"rs_rating": lambda r: -(r.get("rs_rating") or 0),
            "symbol": lambda r: r["symbol"],
            "from_pivot": lambda r: abs((r.get("base") or {}).get("from_pivot_pct") or 99),
            "turnover": lambda r: -(r.get("turnover_cr") or 0),
            "gain": lambda r: -((r.get("breakout") or {}).get("gain_pct") or -999)}
    shown.sort(key=keys.get(sort, keys["rs_rating"]))
    return {"status": "ok", "board": meta.get("board", []), "changes": meta.get("changes", [])[:40],
            "screens": meta.get("screens", {}),
            "industries": sorted({(r.get("industry") or "Unclassified") for r in rows if r.get("stage") != "NONE"}),
            "rows": shown[:limit], "total": len(shown), "meta": {k: v for k, v in meta.items() if k != "changes"},
            "dates": STORE.dates()[-30:]}


MARKET_COLS = ("symbol", "name", "industry", "stage", "close", "rs_rating", "from_52w_high_pct",
               "above_50dma_pct", "atr_pct", "turnover_cr", "volume_x", "up_down_volume",
               "blue_sky", "screen_hits", "bases_this_year")


def market(q: Optional[str] = None, industry: Optional[str] = None, stage: Optional[str] = None,
           sort: str = "rs_rating", desc: bool = True, limit: int = 100, offset: int = 0,
           scan_date: Optional[str] = None) -> dict:
    """Every stock the scan measured, in one table — setup or not.

    This is the whole market as the scan saw it after the close: the same measures as the cards,
    for names that are merely strong, merely liquid, or nothing in particular.
    """
    rows, meta = STORE.load(scan_date)
    if not rows:
        return {"status": "ok", "empty": True, "rows": [], "total": 0,
                "message": "No scan yet — run one to fill the market table."}
    out = rows
    if stage:
        out = [r for r in out if r.get("stage") == stage.upper()]
    if industry:
        out = [r for r in out if (r.get("industry") or "Unclassified") == industry]
    if q:
        needle = q.strip().upper()
        out = [r for r in out if needle in r["symbol"] or needle in (r.get("name") or "").upper()]

    def key(r):
        if sort in ("change", "change_pct"):          # the day's move lives inside the day block
            return (r.get("day") or {}).get("change_pct")
        if sort in ("symbol", "name", "industry", "stage"):
            return (r.get(sort) or "")
        return r.get(sort)
    with_val = [r for r in out if key(r) is not None]
    without = [r for r in out if key(r) is None]
    with_val.sort(key=key, reverse=bool(desc))
    ordered = with_val + without
    trimmed = []
    for r in ordered[offset:offset + limit]:
        row = {k: r.get(k) for k in MARKET_COLS}
        row["change_pct"] = (r.get("day") or {}).get("change_pct")
        trimmed.append(row)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.get("stage") or "NONE"] = counts.get(r.get("stage") or "NONE", 0) + 1
    return {"status": "ok", "rows": trimmed, "total": len(ordered), "scanned": len(rows),
            "counts": counts, "scan_date": meta.get("scan_date"),
            "industries": sorted({(r.get("industry") or "Unclassified") for r in rows})}


def one(symbol: str, scan_date: Optional[str] = None) -> Optional[dict]:
    rows, _ = STORE.load(scan_date)
    return next((r for r in rows if r["symbol"] == symbol.upper()), None)


def chart(symbol: str, bars: int = 140, timeframe: str = "day") -> Optional[dict]:
    """Candles for one card, straight from the cached daily file — no broker call, no refetch."""
    from research.hunter import patterns as PT
    from research.my_equity import cache as CACHE
    row = one(symbol)
    if not row:
        return None
    df = CACHE.daily(None, row["symbol"], row.get("token") or 0, row.get("exchange", "NSE"), refresh=False)
    if df is None or df.empty:
        return None
    d = PT.indicators(df)
    if timeframe == "week":
        wk = PT.to_weekly(df)
        return {"symbol": row["symbol"], "timeframe": "week",
                **PT.chart_payload(wk, None, None, max(60, bars // 5))}
    return {"symbol": row["symbol"], "timeframe": "day",
            **PT.chart_payload(d, row.get("base"), row.get("breakout"), bars)}


# ── the automatic daily scan ──
def tick(db, user_id: int, broker) -> Optional[str]:
    """Called by the background loop; scans once, after the close, on a trading day."""
    cfg = load_config(db, user_id)
    if not cfg.get("auto_scan") or broker is None or scanning():
        return None
    now = datetime.now()
    today = now.date().isoformat()
    if now.weekday() >= 5 or now.hour * 60 + now.minute < int(cfg.get("scan_after_min") or 960):
        return None
    if _last_auto.get(user_id) == today:
        return None
    done = STORE.dates()
    if done and done[-1] >= today:
        _last_auto[user_id] = today
        return None
    _last_auto[user_id] = today
    logger.info("hunter: starting the daily scan for user %s", user_id)
    start_scan(broker, user_id, float(cfg.get("min_rs") or SCAN.MIN_RS), cfg=cfg)
    return "started"
