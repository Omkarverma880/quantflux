"""
VWAP Options Strategy — live/paper engine (NIFTY index options).

Uses the SAME signal detection (research.vwap_options.signals) and exit logic
(research.vwap_options.simulate) as the backtest, so live behaviour cannot drift
from tested behaviour.

Paper-mode default. A real order is placed ONLY when paper_trade is off AND the
global gate is on (settings.PAPER_TRADE False / TRADING_ENABLED True) — the same
fencing as the other live strategies.
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, time as dtime
from typing import Optional

from config import settings
from core.broker import Broker, Exchange, OrderRequest, OrderSide, OrderType, ProductType
from core.database import get_db_session
from core.logger import get_logger
from core.models import VWAPOptionsPosition
from research.vwap_options import signals as sig_mod
from research.vwap_options import simulate as sim_mod
from research.vwap_options.chain import NiftyChain
from research.vwap_options.config import sanitize
from research.vwap_options.vwap_engine import build_series

logger = get_logger("strategy.vwap_options")

_STATE_FILE = settings.DATA_DIR / "strategy_configs" / "vwap_options_state.json"
MKT_OPEN = dtime(9, 15)
MKT_CLOSE = dtime(15, 30)


class VwapOptionsStrategy:
    def __init__(self, broker: Broker, config: Optional[dict] = None, user_id: int = 0):
        self.broker = broker
        self.user_id = user_id
        self.cfg = sanitize(config or {})
        self.chain = NiftyChain(broker)
        self._lock = threading.RLock()
        self.is_active = False
        self._day = None
        self._fired_today: set = set()
        self._auto_started_date = None
        self._manual_stop_date = None
        self._bucket = None
        self._bars_cache: list = []
        self._service = None
        self._load_runtime()

    # ── config / control ──
    def apply_config(self, partial: dict):
        with self._lock:
            self.cfg = sanitize({**self.cfg, **(partial or {})})
        return self.cfg

    def config_dict(self):
        return dict(self.cfg)

    def start(self, partial=None):
        with self._lock:
            if partial:
                self.cfg = sanitize({**self.cfg, **partial})
            self.is_active = True
            self._manual_stop_date = None
            self._save_runtime()
        return self.get_status()

    def stop(self):
        with self._lock:
            self.is_active = False
            self._manual_stop_date = date.today().isoformat()
            self._save_runtime()
        return self.get_status()

    def _maybe_autostart(self, now):
        if self.cfg.get("auto_start") and not self.is_active:
            if self._manual_stop_date == now.date().isoformat():
                return
            if self._auto_started_date != now.date().isoformat():
                self.is_active = True
                self._auto_started_date = now.date().isoformat()
                self._save_runtime()

    def check(self):
        if not self._lock.acquire(blocking=False):
            return self.get_status()
        try:
            now = datetime.now()
            self._maybe_autostart(now)
            self._day_reset(now)
            if self.is_active and MKT_OPEN <= now.time() <= MKT_CLOSE:
                self._scan(now)
            self._manage_open(now)
            return self.get_status()
        finally:
            self._lock.release()

    def _day_reset(self, now):
        d = now.date().isoformat()
        if self._day != d:
            self._day = d
            self._fired_today = set()
            self._bars_cache = []

    # ── shared service (data fetch) ──
    def _svc(self):
        if self._service is None:
            from research.vwap_options.service import VwapOptionsService
            self._service = VwapOptionsService(self.broker, user_id=self.user_id)
        self._service.broker = self.broker
        return self._service

    def _today_bars(self, now):
        """Underlying path for today, refreshed at most once per 5-min bucket."""
        bucket = now.replace(second=0, microsecond=0, minute=(now.minute // 5) * 5)
        if bucket == self._bucket and self._bars_cache:
            return self._bars_cache
        bars, _meta = self._svc().underlying_bars(now.date(), now.date(), self.cfg)
        self._bucket = bucket
        self._bars_cache = bars
        return bars

    # ── entries ──
    def _scan(self, now):
        if len(self._open_positions()) >= int(self.cfg["max_positions"]):
            return
        bars = self._today_bars(now)
        if len(bars) < 2:
            return
        series = build_series(bars, self.cfg["vwap_source"])
        for s in sig_mod.find_signals(bars, series, self.cfg, day=now.date()):
            key = (s["line"], s["event"], s["action"], s["time"])
            if key in self._fired_today:
                continue
            self._fired_today.add(key)
            try:
                self._enter(s, now)
            except Exception as exc:
                logger.error("vwap-options entry failed: %s", exc)
            if len(self._open_positions()) >= int(self.cfg["max_positions"]):
                break

    def _enter(self, s, now):
        spot = s["index_price"]
        contract, reason = self.chain.resolve(spot, self.cfg["strike_offset_steps"], s["opt_type"],
                                              self.cfg["expiry_type"], now.date(),
                                              self.cfg["min_days_to_expiry"])
        if not contract:
            logger.info("vwap-options: no contract (%s)", reason)
            return
        lot = self._svc().lot_size()
        qty = lot * int(self.cfg["lots"])
        ltp = self._opt_ltp(contract["tradingsymbol"])
        if not ltp or qty <= 0:
            return
        if not self._place(contract["tradingsymbol"], qty, OrderSide.BUY):
            return
        entry = sim_mod.apply_slippage(ltp, "buy", self.cfg)
        target, stop = sim_mod.resolve_target_sl(entry, self.cfg)
        db = get_db_session()
        try:
            db.add(VWAPOptionsPosition(
                user_id=self.user_id, trade_date=now.date(), rule=s["rule"], line=s["line"],
                event=s["event"], opt_type=s["opt_type"], symbol=contract["tradingsymbol"],
                strike=contract["strike"], expiry=contract["expiry"].isoformat(),
                offset_steps=int(self.cfg["strike_offset_steps"]),
                token=int(contract["token"]) if contract.get("token") else None,
                qty=qty, lot=lot, index_price=spot, vwap_level=s["level"],
                entry_price=entry, entry_time=now.strftime("%H:%M:%S"),
                target=target, sl=stop, ltp=entry, status="OPEN",
                paper=bool(self.cfg["paper_trade"]),
                engine_version=self.cfg.get("engine_version")))
            db.commit()
            logger.info("vwap-options ENTER %s qty=%d @ %.2f (paper=%s)",
                        contract["tradingsymbol"], qty, entry, self.cfg["paper_trade"])
            mode = "PAPER" if self.cfg["paper_trade"] else "LIVE"
            self._notify(
                f"🟢 <b>VWAP OPTIONS {mode}</b>\n\n"
                f"Rule: {s['rule']}\n"
                f"NIFTY {spot} vs {s['line']} {s['level']}\n"
                f"BUY {qty} {contract['tradingsymbol']}\n"
                f"Entry ₹{entry} · Target ₹{target} · SL ₹{stop}\n"
                f"Time: {now.strftime('%H:%M')}")
        except Exception as exc:
            db.rollback()
            logger.error("vwap-options save failed: %s", exc)
        finally:
            db.close()

    # ── manage open positions ──
    def _manage_open(self, now):
        opens = self._open_positions()
        if not opens:
            return
        sq = sim_mod._hhmm(self.cfg.get("square_off_time", "15:15"))
        db = get_db_session()
        try:
            for p in opens:
                row = db.query(VWAPOptionsPosition).filter(VWAPOptionsPosition.id == p.id).first()
                if not row or row.status != "OPEN":
                    continue
                ltp = self._opt_ltp(row.symbol)
                if ltp is None:
                    continue
                qty = int(row.qty or 0)
                mtm = round((ltp - float(row.entry_price)) * qty, 2)
                row.ltp = round(ltp, 2)
                row.mtm = mtm
                row.mfe = round(max(float(row.mfe or 0), mtm), 2)
                row.mae = round(min(float(row.mae or 0), mtm), 2)
                reason = None
                if row.target and ltp >= float(row.target):
                    reason = "TARGET"
                elif row.sl and ltp <= float(row.sl):
                    reason = "STOP"
                elif not self.cfg.get("hold_to_expiry") and now.time() >= sq:
                    reason = "SQUAREOFF"
                if reason:
                    self._place(row.symbol, qty, OrderSide.SELL)
                    row.status = reason
                    row.exit_price = round(ltp, 2)
                    row.exit_reason = reason
                    row.exit_time = now.strftime("%H:%M:%S")
                    logger.info("vwap-options EXIT %s %s @ %.2f mtm=%.2f",
                                row.symbol, reason, ltp, mtm)
                    emoji = {"TARGET": "🎯", "STOP": "🛑"}.get(reason, "⚪")
                    mode = "PAPER" if row.paper else "LIVE"
                    self._notify(
                        f"{emoji} <b>VWAP OPTIONS EXIT</b> · {mode}\n\n"
                        f"{row.symbol}\n"
                        f"Exit ₹{round(ltp, 2)} ({reason}) · Entry ₹{float(row.entry_price)}\n"
                        f"P&L: ₹{mtm}")
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error("vwap-options manage failed: %s", exc)
        finally:
            db.close()

    # ── telegram ──
    def _notify(self, text):
        if not self.cfg.get("telegram_alerts"):
            return
        try:
            from core import notify
            bot = self.cfg.get("telegram_bot", "a")
            if notify.enabled(bot):
                notify.send(text, bot=bot)
        except Exception as exc:
            logger.debug("vwap-options notify failed: %s", exc)

    # ── broker (paper short-circuits before any order call) ──
    def _place(self, tradingsymbol, qty, side) -> bool:
        if self.cfg.get("paper_trade"):
            return True
        if getattr(settings, "PAPER_TRADE", True) or not getattr(settings, "TRADING_ENABLED", False):
            logger.warning("vwap-options: global trading gate blocks real orders")
            return False
        try:
            req = OrderRequest(tradingsymbol=tradingsymbol, exchange=Exchange.NFO, side=side,
                               quantity=int(qty), order_type=OrderType.MARKET,
                               product=ProductType.NRML)
            resp = self.broker.place_order(req)
            return bool(resp and getattr(resp, "order_id", None))
        except Exception as exc:
            logger.error("vwap-options order failed (%s): %s", tradingsymbol, exc)
            return False

    def _opt_ltp(self, tradingsymbol) -> Optional[float]:
        try:
            key = f"NFO:{tradingsymbol}"
            d = self.broker.get_ltp([key]) or {}
            v = d.get(key)
            return float(v) if v else None
        except Exception:
            return None

    def _open_positions(self):
        db = get_db_session()
        try:
            return (db.query(VWAPOptionsPosition)
                      .filter(VWAPOptionsPosition.user_id == self.user_id,
                              VWAPOptionsPosition.status == "OPEN").all())
        finally:
            db.close()

    # ── status / positions ──
    def get_status(self) -> dict:
        opens = self._open_positions()
        return {"is_active": self.is_active, "paper_trade": bool(self.cfg["paper_trade"]),
                "auto_start": bool(self.cfg["auto_start"]), "open_positions": len(opens),
                "max_positions": int(self.cfg["max_positions"]),
                "engine_version": self.cfg.get("engine_version"),
                "config": self.config_dict()}

    def positions(self, trade_date: Optional[str] = None) -> list[dict]:
        db = get_db_session()
        try:
            q = db.query(VWAPOptionsPosition).filter(VWAPOptionsPosition.user_id == self.user_id)
            if trade_date:
                try:
                    q = q.filter(VWAPOptionsPosition.trade_date == date.fromisoformat(trade_date))
                except ValueError:
                    pass
            else:
                q = q.filter(VWAPOptionsPosition.trade_date == date.today())
            return [self._pos_dict(r) for r in q.order_by(VWAPOptionsPosition.id.desc()).all()]
        finally:
            db.close()

    @staticmethod
    def _pos_dict(r):
        def f(v):
            return float(v) if v is not None else None
        return {"id": r.id, "date": r.trade_date.isoformat() if r.trade_date else None,
                "rule": r.rule, "line": r.line, "event": r.event, "opt_type": r.opt_type,
                "symbol": r.symbol, "strike": f(r.strike), "expiry": r.expiry,
                "offset_steps": r.offset_steps, "qty": r.qty, "lot": r.lot,
                "index_price": f(r.index_price), "vwap_level": f(r.vwap_level),
                "entry_price": f(r.entry_price), "entry_time": r.entry_time,
                "target": f(r.target), "sl": f(r.sl), "ltp": f(r.ltp), "mtm": f(r.mtm),
                "mfe": f(r.mfe), "mae": f(r.mae), "status": r.status,
                "exit_price": f(r.exit_price), "exit_time": r.exit_time,
                "exit_reason": r.exit_reason, "paper": r.paper,
                "engine_version": r.engine_version}

    # ── runtime persistence ──
    def _save_runtime(self):
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(json.dumps({
                "is_active": self.is_active, "auto_started_date": self._auto_started_date,
                "manual_stop_date": self._manual_stop_date}))
        except Exception as exc:
            logger.debug("vwap-options runtime save failed: %s", exc)

    def _load_runtime(self):
        try:
            if _STATE_FILE.exists():
                st = json.loads(_STATE_FILE.read_text()) or {}
                self.is_active = bool(st.get("is_active"))
                self._auto_started_date = st.get("auto_started_date")
                self._manual_stop_date = st.get("manual_stop_date")
        except Exception as exc:
            logger.debug("vwap-options runtime load failed: %s", exc)
