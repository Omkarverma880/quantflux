"""
FastAPI application — main server setup.
Mounts API routes, WebSocket, serves React frontend.
"""
import os
import sys
import uuid
import asyncio
from pathlib import Path
from datetime import time as dtime, datetime

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.websocket_manager import ws_manager
from app.routes.auth_routes import router as auth_router
from app.routes.trading_routes import router as trading_router
from app.routes.strategy_routes import router as strategy_router
from app.routes.dashboard_routes import router as dashboard_router
from app.routes.cumulative_volume_routes import router as cv_router
from app.routes.strategy1_routes import router as s1_router
from app.routes.strategy2_routes import router as s2_router
from app.routes.strategy3_routes import router as s3_router
from app.routes.strategy4_routes import router as s4_router
from app.routes.manual_trading_routes import router as manual_trading_router
from app.routes.settings_routes import router as settings_router
from core.logger import get_logger

logger = get_logger("server")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"
STRATEGY_CHECK_INTERVAL = 2  # seconds — fast enough for momentum entries


async def _strategy_background_loop():
    """
    Background task: runs strategy check every STRATEGY_CHECK_INTERVAL seconds
    for ALL users with active Zerodha sessions.
    """
    # Brief delay to let server finish startup
    await asyncio.sleep(5)
    print("[BG] Strategy loop: starting checks.", flush=True)

    while True:
        try:
            await asyncio.sleep(STRATEGY_CHECK_INTERVAL)

            now = datetime.now()
            # Only run during market hours on weekdays (Mon-Fri 9:15 - 15:30)
            if now.weekday() >= 5:  # Saturday / Sunday
                continue
            if now.time() < dtime(9, 15) or now.time() > dtime(15, 30):
                continue

            # Run DB query in a thread to avoid blocking the event loop
            loop = asyncio.get_running_loop()
            active_user_ids = await loop.run_in_executor(None, _get_active_user_ids)

            if not active_user_ids:
                continue

            for uid in active_user_ids:
                try:
                    await loop.run_in_executor(None, _run_strategies_for_user, uid)
                except Exception as e:
                    print(f"[BG] Strategy check error user {uid}: {e}", flush=True)

            # ── Broadcast strategy state via WebSocket ──
            try:
                from app.routes.strategy1_routes import _get_strategy as _get_s1
                from app.routes.strategy2_routes import _get_strategy as _get_s2
                from app.routes.strategy3_routes import _get_strategy as _get_s3
                from app.routes.strategy4_routes import _get_strategy as _get_s4
                payload = {}
                for label, getter in [("s1", _get_s1), ("s2", _get_s2), ("s3", _get_s3), ("s4", _get_s4)]:
                    strat = getter(user_id=active_user_ids[0])
                    payload[label] = {
                        "state": strat.state.value if hasattr(strat.state, "value") else str(strat.state),
                        "is_active": strat.is_active,
                        "ltp": getattr(strat, "current_ltp", 0),
                    }
                await ws_manager.broadcast("strategy_update", payload)
            except Exception:
                pass  # non-critical
        except asyncio.CancelledError:
            print("[BG] Strategy loop cancelled.", flush=True)
            break
        except Exception as e:
            print(f"[BG] Strategy loop error: {e}", flush=True)
            await asyncio.sleep(10)  # back off on error


def _get_active_user_ids() -> list[int]:
    """Synchronous helper — queries DB for active sessions today. Runs in executor."""
    from core.database import get_db_session
    from core.models import ZerodhaSession
    from datetime import date
    db = get_db_session()
    try:
        sessions = db.query(ZerodhaSession).filter(
            ZerodhaSession.login_date == date.today()
        ).all()
        return [s.user_id for s in sessions]
    except Exception as e:
        print(f"[BG] DB query error: {e}", flush=True)
        return []
    finally:
        db.close()


def _run_strategies_for_user(uid: int):
    """Synchronous helper — runs strategy checks for one user. Runs in executor."""
    from core.database import get_db_session
    from core.broker import get_user_broker
    from core.auth import UserZerodhaAuth
    from app.routes.strategy1_routes import _get_strategy as _get_s1, _get_cv_data
    from app.routes.strategy2_routes import _get_strategy as _get_s2
    from app.routes.strategy3_routes import _get_strategy as _get_s3
    from app.routes.strategy4_routes import _get_strategy as _get_s4, _get_spot_price as _get_s4_spot

    db = get_db_session()
    try:
        broker = get_user_broker(db, uid)
        if not broker.is_kite_connected:
            return

        authenticated = UserZerodhaAuth.is_authenticated(db, uid)
        cv_data = None
        spot_price = 0

        strategy = _get_s1(broker, uid)
        if strategy.is_active:
            if cv_data is None:
                cv_data = _get_cv_data(broker, authenticated)
                spot_price = cv_data.get("spot_price", 0)
            strategy.check(cv_data, spot_price)

        s2 = _get_s2(broker, uid)
        if s2.is_active and s2.state.value in ("POSITION_OPEN", "ORDER_PLACED"):
            if cv_data is None:
                cv_data = _get_cv_data(broker, authenticated)
                spot_price = cv_data.get("spot_price", 0)
            s2.check(cv_data, spot_price)

        s3 = _get_s3(broker, uid)
        if s3.is_active:
            if cv_data is None:
                cv_data = _get_cv_data(broker, authenticated)
                spot_price = cv_data.get("spot_price", 0)
            s3.check(cv_data, spot_price)

        s4 = _get_s4(broker, uid)
        if s4.is_active:
            s4_spot = _get_s4_spot(broker, authenticated)
            s4.check(s4_spot)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[LIFESPAN] Trading server starting up …", flush=True)

    # ── Log Railway outbound IP (for Zerodha whitelisting) ──
    # Prints the public egress IP on every deploy. Find it in Railway
    # → Deploy Logs by searching for "OUTBOUND IP".
    try:
        import urllib.request
        for svc in ("https://api.ipify.org", "https://ifconfig.me/ip", "https://checkip.amazonaws.com"):
            try:
                with urllib.request.urlopen(svc, timeout=4) as r:
                    ip = r.read().decode().strip()
                    if ip:
                        print(f"[LIFESPAN] ===== OUTBOUND IP: {ip}  (whitelist this in Zerodha) =====", flush=True)
                        app.state.outbound_ip = ip
                        break
            except Exception:
                continue
        else:
            print("[LIFESPAN] OUTBOUND IP: could not resolve (no egress to ipify/ifconfig/aws).", flush=True)
    except Exception as e:
        print(f"[LIFESPAN] OUTBOUND IP lookup error (non-fatal): {e}", flush=True)

    # Ensure all DB tables exist (safe on fresh Railway Postgres)
    try:
        from core.database import engine, Base
        from core import models  # noqa: F401 — registers all models
        Base.metadata.create_all(bind=engine)
        print("[LIFESPAN] Database tables verified / created.", flush=True)
    except Exception as e:
        print(f"[LIFESPAN] DB init error (non-fatal): {e}", flush=True)

    # Start background loop (it self-delays 30s before first run)
    task = asyncio.create_task(_strategy_background_loop())

    # Resume the manual-trading SL/Target monitor for any persisted trades.
    # Without this, monitored positions are unprotected after a server
    # restart until the next /monitor/status poll — unacceptable on Railway.
    try:
        from app.routes.manual_trading_routes import _monitor as _mt_monitor
        if _mt_monitor._trades:
            _mt_monitor._ensure_running()
            print(
                f"[LIFESPAN] Manual SL/Target monitor resumed for "
                f"{len(_mt_monitor._trades)} persisted trade(s).",
                flush=True,
            )
    except Exception as exc:
        print(f"[LIFESPAN] Manual monitor resume failed: {exc}", flush=True)

    print("[LIFESPAN] Server ready.", flush=True)
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    print("[LIFESPAN] Server shut down.", flush=True)


app = FastAPI(
    title="QuantFlux",
    description="Multi-User Automated Trading System",
    version="2.0.0",
    lifespan=lifespan,
)

# Rate-limiting error handler
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.routes.auth_routes import limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — allow React dev server + production domain
from config import settings as app_settings

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=app_settings.CORS_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# ── API Routes ─────────────────────────────────────
app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])
app.include_router(trading_router, prefix="/api/trading", tags=["Trading"])
app.include_router(strategy_router, prefix="/api/strategies", tags=["Strategies"])
app.include_router(dashboard_router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(cv_router, prefix="/api/strategy1", tags=["Strategy1-CumulativeVolume"])
app.include_router(s1_router, prefix="/api/strategy1-trade", tags=["Strategy1-GannCV"])
app.include_router(s2_router, prefix="/api/strategy2-trade", tags=["Strategy2-OptionSell"])
app.include_router(s3_router, prefix="/api/strategy3-trade", tags=["Strategy3-CvVwapEmaAdx"])
app.include_router(s4_router, prefix="/api/strategy4-trade", tags=["Strategy4-HighLowRetest"])
app.include_router(manual_trading_router, prefix="/api/manual", tags=["ManualTrading"])
app.include_router(settings_router, prefix="/api/settings", tags=["Settings"])

# ── Boot ID — changes every server restart → frontend forces re-login ──
_BOOT_ID = uuid.uuid4().hex

@app.get("/api/boot_id")
def get_boot_id():
    return {"boot_id": _BOOT_ID}


@app.get("/api/health")
def healthcheck():
    """Lightweight health check — no DB, no imports."""
    import os
    return {
        "status": "ok",
        "port": os.getenv("PORT", "8000"),
        "boot_id": _BOOT_ID,
    }


@app.get("/api/outbound-ip")
def get_outbound_ip():
    """
    Return this deployment's public outbound IP — the one to whitelist
    in your Zerodha developer profile. Cached at startup; refreshes live
    if the cache is empty.
    """
    import urllib.request
    cached = getattr(app.state, "outbound_ip", None)
    if cached:
        return {"ip": cached, "source": "startup_cache", "boot_id": _BOOT_ID}

    for svc in ("https://api.ipify.org", "https://ifconfig.me/ip", "https://checkip.amazonaws.com"):
        try:
            with urllib.request.urlopen(svc, timeout=4) as r:
                ip = r.read().decode().strip()
                if ip:
                    app.state.outbound_ip = ip
                    return {"ip": ip, "source": svc, "boot_id": _BOOT_ID}
        except Exception:
            continue
    return {"ip": None, "error": "could not resolve outbound IP", "boot_id": _BOOT_ID}


# ── WebSocket ──────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            data = await ws.receive_text()
            # Client can send commands via WS if needed
    except WebSocketDisconnect:
        await ws_manager.disconnect(ws)


# ── Serve React frontend (production build) ───────
from fastapi.responses import FileResponse

@app.get("/api/debug/frontend")
def debug_frontend():
    """Diagnostic: check if frontend/dist exists."""
    exists = FRONTEND_DIR.exists()
    files = sorted(str(f.relative_to(FRONTEND_DIR)) for f in FRONTEND_DIR.rglob("*") if f.is_file()) if exists else []
    return {"frontend_dir": str(FRONTEND_DIR), "exists": exists, "files": files[:50]}

if FRONTEND_DIR.exists():
    # Serve static assets (JS, CSS, images) from dist/assets/
    assets_dir = FRONTEND_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="static-assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """SPA catch-all: serve file if exists, otherwise index.html."""
        file_path = FRONTEND_DIR / full_path
        if full_path and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")
