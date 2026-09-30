"""
CAS Game Play — jobs and stored backtests.

Runs are kept as JSON files in the throwaway cache, not in PostgreSQL: they are re-creatable from
the market store at any time, and the database has enough to carry already.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Callable, Optional

from core.logger import get_logger
from research.cas_game import backtest as BT
from research.cas_game import strategy as ST

logger = get_logger("research.cas_game.service")

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
KEEP_RUNS = 30


def _root() -> Path:
    try:
        from config import settings
        p = Path(settings.CACHE_DIR) / "cas_game"
    except Exception:
        p = Path("data/cache/cas_game")
    p.mkdir(parents=True, exist_ok=True)
    return p


def start_job(payload: dict, user_id: int, runner: Callable) -> dict:
    jid = uuid.uuid4().hex[:12]
    job = {"id": jid, "user_id": user_id, "status": "running", "progress": "starting",
           "started": time.time(), "result": None, "error": None}
    with _lock:
        _jobs[jid] = job
        for old in [k for k, v in _jobs.items()
                    if v["status"] != "running" and time.time() - v["started"] > 6 * 3600]:
            _jobs.pop(old, None)

    def work():
        try:
            job["result"] = runner(payload, lambda m: job.__setitem__("progress", m))
            job["status"] = "done"
        except Exception as exc:
            logger.error("cas game backtest failed: %s | %s", exc, traceback.format_exc())
            job["status"], job["error"] = "error", str(exc)[:400]
        finally:
            job["seconds"] = round(time.time() - job["started"], 1)

    threading.Thread(target=work, daemon=True, name=f"casgame-{jid}").start()
    return job


def get_job(job_id: str, user_id: int) -> Optional[dict]:
    j = _jobs.get(job_id)
    return j if j and j["user_id"] == user_id else None


def run_backtest(cfg: dict, progress: Optional[Callable[[str], None]] = None) -> dict:
    p = ST.Params(**{k: v for k, v in (cfg.get("params") or {}).items() if k in ST.P.as_dict()})
    res = BT.run(cfg["start"], cfg["end"], p, progress)
    res["id"] = f"{int(time.time())}"
    res["label"] = cfg.get("label") or ""
    save(res)
    return {"id": res["id"], "summary": res["summary"], "params": res["params"],
            "start": res["start"], "end": res["end"], "trades": res["trades"][:500]}


def save(res: dict) -> None:
    path = _root() / f"run_{res['id']}.json"
    path.write_text(json.dumps(res, default=str))
    for old in sorted(_root().glob("run_*.json"))[:-KEEP_RUNS]:
        old.unlink(missing_ok=True)


def list_runs(limit: int = 30) -> list[dict]:
    out = []
    for f in sorted(_root().glob("run_*.json"), reverse=True)[:limit]:
        try:
            d = json.loads(f.read_text())
            s = d.get("summary", {})
            out.append({"id": d.get("id"), "label": d.get("label"), "start": d.get("start"),
                        "end": d.get("end"), "sessions": s.get("sessions"), "tickets": s.get("tickets"),
                        "net": s.get("net"), "spent": s.get("spent"), "hit_target": s.get("hit_target")})
        except Exception as exc:
            logger.debug("unreadable run %s: %s", f.name, exc)
    return out


def load_run(run_id: str) -> Optional[dict]:
    f = _root() / f"run_{run_id}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def delete_run(run_id: str) -> bool:
    f = _root() / f"run_{run_id}.json"
    if f.exists():
        f.unlink()
        return True
    return False
