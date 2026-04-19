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
    from core.database import get_db_session
    from core.models import ZerodhaSession
    from core.broker import get_user_broker
    from core.auth import UserZerodhaAuth
    from app.routes.strategy1_routes import _get_strategy as _get_s1, _get_cv_data
    from app.routes.strategy2_routes import _get_strategy as _get_s2
    from app.routes.strategy3_routes import _get_strategy as _get_s3
    from datetime import date

    logger.info("Strategy background monitor started")
    while True:
        try:
            await asyncio.sleep(STRATEGY_CHECK_INTERVAL)

            now = datetime.now().time()
            # Only run during market hours (9:15 - 15:30)
            if now < dtime(9, 15) or now > dtime(15, 30):
                continue

            # Find all users with active Zerodha sessions today
            db = get_db_session()
            try:
                active_sessions = db.query(ZerodhaSession).filter(
                    ZerodhaSession.login_date == date.today()
                ).all()
                active_user_ids = [s.user_id for s in active_sessions]
            finally:
                db.close()

            if not active_user_ids:
                continue

            for uid in active_user_ids:
                try:
                    db = get_db_session()
                    try:
                        broker = get_user_broker(db, uid)
                        if not broker.is_kite_connected:
                            continue

                        authenticated = UserZerodhaAuth.is_authenticated(db, uid)
                        cv_data = None
                        spot_price = 0

                        # ── Strategy 1 ──
                        strategy = _get_s1(broker, uid)
                        if strategy.is_active:
                            if cv_data is None:
                                cv_data = _get_cv_data(broker, authenticated)
                                spot_price = cv_data.get("spot_price", 0)
                            strategy.check(cv_data, spot_price)
                            logger.debug(f"BG S1 user={uid}: state={strategy.state.value}")

                        # ── Strategy 2 ──
                        s2 = _get_s2(broker, uid)
                        if s2.is_active and s2.state.value in ("POSITION_OPEN", "ORDER_PLACED"):
                            if cv_data is None:
                                cv_data = _get_cv_data(broker, authenticated)
                                spot_price = cv_data.get("spot_price", 0)
                            s2.check(cv_data, spot_price)
                            logger.debug(f"BG S2 user={uid}: state={s2.state.value}")

                        # ── Strategy 3 ──
                        s3 = _get_s3(broker, uid)
                        if s3.is_active:
                            if cv_data is None:
                                cv_data = _get_cv_data(broker, authenticated)
                                spot_price = cv_data.get("spot_price", 0)
                            s3.check(cv_data, spot_price)
                            logger.debug(f"BG S3 user={uid}: state={s3.state.value}")
                    finally:
                        db.close()
                except Exception as e:
                    logger.error(f"BG strategy check error for user {uid}: {e}")

            # ── Broadcast strategy state via WebSocket ──
            try:
                # Broadcast aggregate state (first active user's state for now)
                payload = {}
                for label, getter in [("s1", _get_s1), ("s2", _get_s2), ("s3", _get_s3)]:
                    if active_user_ids:
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
            logger.info("Strategy background monitor stopped")
            break
        except Exception as e:
            logger.error(f"Background strategy check error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Trading server starting up …")
    task = asyncio.create_task(_strategy_background_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("Trading server shutting down …")


app = FastAPI(
    title="QuantFlux",
    description="Multi-User Automated Trading System",
    version="2.0.0",
    lifespan=lifespan,
)

# Rate-limiting error handler
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
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
app.include_router(manual_trading_router, prefix="/api/manual", tags=["ManualTrading"])
app.include_router(settings_router, prefix="/api/settings", tags=["Settings"])

# ── Boot ID — changes every server restart → frontend forces re-login ──
_BOOT_ID = uuid.uuid4().hex

@app.get("/api/boot_id")
def get_boot_id():
    return {"boot_id": _BOOT_ID}


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
