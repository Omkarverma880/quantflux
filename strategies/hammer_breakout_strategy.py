"""
Hammer at 3/6-Month Low — Breakout: live/paper engine (Equity Strategy #4).

Daily positional swing, LONG only. Once a day (first tick after the open) each
watchlist stock's last COMPLETED daily candle is tested for the hammer setup;
qualifying stocks are "armed" with the trigger = that candle's high. During the
session the LTP is polled and the first print above the trigger buys the stock
(CNC delivery by default, MIS if configured). Target/SL ride on the stock
price — the stop defaults to the hammer's own low — and the position squares
off after ``max_hold_days``.

Paper-mode default. Real orders only when paper_trade is off AND the global
trading gate is on — the same fencing as the other live strategies.
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

from config import settings
from core.broker import Broker, OrderRequest, OrderType, OrderSide, ProductType, Exchange
from core.database import get_db_session
from core.logger import get_logger
from core.models import HammerBreakoutPosition
from research.prev_period_vwap import _candle_dt
from research.pmvwap_straddle.universe import Universe
from research.hammer_breakout import calculations as calc
from research.hammer_breakout.config import sanitize, TIMEFRAME
from research.hammer_breakout.service import warmup_days

logger = get_logger("strategy.hammer_breakout")

_STATE_FILE = settings.DATA_DIR / "strategy_configs" / "hammer_breakout_state.json"
MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)


class HammerBreakoutStrategy:
    def __init__(self, broker: Broker, config: Optional[dict] = None, user_id: int = 0):
        self.broker = broker
        self.user_id = user_id
        self.universe = Universe(broker)
        self.cfg = sanitize(config or {})
        self._lock = threading.RLock()
        self.is_active = False
        self._day = None
        self._setups: dict[str, dict] = {}      # symbol → armed setup for today
        self._entered_today: set = set()
        self._auto_started_date = None
        self._manual_stop_date = None
        self._load_runtime()

    # ── config / control ──
    def apply_config(self, partial: dict):
        with self._lock:
            self.cfg = sanitize({**self.cfg, **(partial or {})})
            self._setups = {}                   # thresholds changed — re-arm
            self._day = None
        return self.cfg

    def config_dict(self):
        return dict(self.cfg)

    def start(self, partial=None):
        with self._lock:
            if partial:
                self.cfg = sanitize({**self.cfg, **partial})
            self.is_active = True
            self._manual_stop_date = None
            self._day = None                    # force a fresh arm on the next tick
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
            if self.is_active and MARKET_OPEN <= now.time() <= MARKET_CLOSE:
                self._arm_setups(now)
                self._scan_entries(now)
            self._manage_open(now)
            return self.get_status()
        finally:
            self._lock.release()

    def _day_reset(self, now):
        d = now.date().isoformat()
        if self._day != d:
            self._day = d
            self._setups = {}
            self._entered_today = {p.underlying for p in self._open_positions()}

    # ── arm: hammer setups on the last COMPLETED daily candle ──
    def _arm_setups(self, now):
        if self._setups:
            return
        today = now.date()
        armed: dict[str, dict] = {}
        for sym in self.cfg.get("symbols", []):
            sym = (sym or "").strip().upper()
            if not sym:
                continue
            try:
                token, exch = self.universe.resolve_equity_token(sym)
                if not token:
                    continue
                candles = [c for c in self._daily(token, today) if c["_d"] < today]
                if len(candles) < 2:
                    continue
                setup = calc.hammer_at(candles, len(candles) - 1, self.cfg)
                if setup and setup["ok"]:
                    armed[sym] = {"token": int(token), "exchange": exch,
                                  "signal_date": candles[-1]["_d"],
                                  "trigger": round(setup["high"], 2),
                                  "sig_low": round(setup["low"], 2)}
                    logger.info("hammer ARMED %s trigger=%.2f (signal %s)", sym,
                                setup["high"], candles[-1]["_d"])
            except Exception as exc:
                logger.debug("hammer arm %s failed: %s", sym, exc)
        self._setups = armed or {"__none__": {}}    # sentinel: computed, nothing armed

    def _armed(self) -> dict:
        return {k: v for k, v in self._setups.items() if k != "__none__"}

    # ── entries ──
    def _scan_entries(self, now):
        armed = self._armed()
        if not armed:
            return
        if now.time() > calc._parse_hhmm(self.cfg["entry_cutoff"]):
            return
        opens = self._open_positions()
        cap = min(int(self.cfg["max_positions"]), int(self.cfg["max_long"]))
        for sym, s in armed.items():
            if sym in self._entered_today:
                continue
            if len(opens) >= cap:
                break
            try:
                if self._maybe_enter(sym, s, now):
                    opens = self._open_positions()
            except Exception as exc:
                logger.debug("hammer entry %s failed: %s", sym, exc)

    def _maybe_enter(self, sym, s, now) -> bool:
        ltp = self._eq_ltp(sym, s["exchange"])
        if not ltp or ltp <= s["trigger"]:
            return False
        entry = round(ltp, 2)
        qty = calc.position_qty(self.cfg, entry)
        if qty <= 0:
            self._entered_today.add(sym)
            return False
        if not self._place(sym, s["exchange"], qty, OrderSide.BUY, entry):
            return False
        target, stop = calc.resolve_target_sl(entry, self.cfg, s["sig_low"])
        db = get_db_session()
        try:
            db.add(HammerBreakoutPosition(
                user_id=self.user_id, trade_date=now.date(), signal_date=s["signal_date"],
                underlying=sym, direction="LONG", symbol=sym, exchange=s["exchange"],
                token=int(s["token"]), qty=qty, trigger=s["trigger"], signal_low=s["sig_low"],
                entry_price=entry, entry_time=now.strftime("%H:%M:%S"), target=target, sl=stop,
                ltp=entry, status="OPEN", product=self.cfg["product"],
                paper=bool(self.cfg["paper_trade"])))
            db.commit()
            self._entered_today.add(sym)
            logger.info("hammer ENTER %s qty=%d @ %.2f (trigger %.2f, paper=%s)", sym, qty, entry,
                        s["trigger"], self.cfg["paper_trade"])
            self._notify(
                f"🔨 <b>HAMMER BREAKOUT {'PAPER' if self.cfg['paper_trade'] else 'LIVE'}</b>\n\n"
                f"Stock: <b>{sym}</b>  ·  LONG\n"
                f"Order: BUY {qty} {sym} ({self.cfg['product']})\n"
                f"Entry: ₹{entry}  ·  Target ₹{target}  ·  SL ₹{stop}\n"
                f"Trigger: broke {s['trigger']} — hammer at a {self.cfg['low_lookback']}-bar low "
                f"on {s['signal_date']}\n"
                f"Time: {now.strftime('%H:%M')}")
            return True
        except Exception as exc:
            db.rollback()
            logger.error("hammer position save failed: %s", exc)
            return False
        finally:
            db.close()

    # ── manage open positions (they carry across days) ──
    def _manage_open(self, now):
        opens = self._open_positions()
        if not opens:
            return
        squareoff = calc._parse_hhmm(self.cfg["square_off"])
        db = get_db_session()
        try:
            for p in opens:
                row = db.query(HammerBreakoutPosition).filter(
                    HammerBreakoutPosition.id == p.id).first()
                if not row or row.status != "OPEN":
                    continue
                ltp = self._eq_ltp(row.underlying, row.exchange)
                if ltp is None:
                    continue
                qty = int(row.qty or 0)
                mtm = round((ltp - float(row.entry_price)) * qty, 2)
                row.ltp = round(ltp, 2)
                row.mtm = mtm
                row.mfe = round(max(float(row.mfe or 0), mtm), 2)
                row.mae = round(min(float(row.mae or 0), mtm), 2)
                tgt, stp = float(row.target or 0), float(row.sl or 0)
                reason = None
                if tgt and ltp >= tgt:
                    reason = "TARGET"
                elif stp and ltp <= stp:
                    reason = "STOP"
                else:
                    hold = (now.date() - row.trade_date).days
                    if row.product == "MIS" and now.time() >= squareoff:
                        reason = "SQUAREOFF"
                    elif (row.product == "CNC" and hold >= int(self.cfg["max_hold_days"])
                          and now.time() >= squareoff):
                        reason = "SQUAREOFF"
                if reason:
                    self._place(row.underlying, row.exchange, qty, OrderSide.SELL, ltp)
                    row.status = reason
                    row.exit_price = round(ltp, 2)
                    row.exit_reason = reason
                    row.exit_time = now.strftime("%H:%M:%S")
                    row.exit_date = now.date()
                    row.hold_days = (now.date() - row.trade_date).days
                    logger.info("hammer EXIT %s %s @ %.2f mtm=%.2f", row.underlying, reason, ltp, mtm)
                    emoji = {"TARGET": "🎯", "STOP": "🛑"}.get(reason, "⚪")
                    self._notify(
                        f"{emoji} <b>HAMMER BREAKOUT EXIT</b> · {'PAPER' if row.paper else 'LIVE'}\n\n"
                        f"Stock: <b>{row.underlying}</b> (LONG)\n"
                        f"Exit: ₹{round(ltp, 2)} ({reason})  ·  Entry ₹{float(row.entry_price)}\n"
                        f"Held: {row.hold_days}d  ·  P&L: ₹{mtm}")
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error("hammer manage failed: %s", exc)
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
            logger.debug("hammer notify failed: %s", exc)

    # ── broker helpers (paper short-circuits) ──
    def _place(self, tradingsymbol, exch, qty, side, ref_price) -> bool:
        if self.cfg.get("paper_trade"):
            return True
        try:
            req = OrderRequest(
                tradingsymbol=tradingsymbol,
                exchange=Exchange.BSE if exch == "BSE" else Exchange.NSE,
                side=side, quantity=int(qty), order_type=OrderType.MARKET,
                product=ProductType.MIS if self.cfg["product"] == "MIS" else ProductType.CNC)
            resp = self.broker.place_order(req)
            return bool(resp and getattr(resp, "order_id", None))
        except Exception as exc:
            logger.error("hammer order failed (%s %s): %s", tradingsymbol, side, exc)
            return False

    def _eq_ltp(self, tradingsymbol, exch) -> Optional[float]:
        try:
            key = f"{'BSE' if exch == 'BSE' else 'NSE'}:{tradingsymbol}"
            d = self.broker.get_ltp([key]) or {}
            v = d.get(key)
            return float(v) if v else None
        except Exception:
            return None

    def _daily(self, token, today) -> list[dict]:
        frm = datetime.combine(today - timedelta(days=warmup_days(self.cfg)), MARKET_OPEN)
        to = datetime.combine(today, MARKET_CLOSE)
        try:
            raw = self.broker.get_historical_data(token, frm, min(to, datetime.now()), TIMEFRAME) or []
        except Exception as exc:
            logger.debug("hammer daily candles failed (%s): %s", token, exc)
            raw = []
        out = []
        for c in raw:
            dt = _candle_dt(c)
            if dt is None:
                continue
            c["_dt"] = dt
            c["_d"] = dt.date()
            out.append(c)
        out.sort(key=lambda c: c["_d"])
        return out

    def _open_positions(self):
        db = get_db_session()
        try:
            return (db.query(HammerBreakoutPosition)
                      .filter(HammerBreakoutPosition.user_id == self.user_id,
                              HammerBreakoutPosition.status == "OPEN").all())
        finally:
            db.close()

    @property
    def has_open_positions(self) -> bool:
        return bool(self._open_positions())

    # ── status / positions ──
    def get_status(self) -> dict:
        opens = self._open_positions()
        armed = self._armed()
        return {"is_active": self.is_active, "paper_trade": bool(self.cfg["paper_trade"]),
                "auto_start": bool(self.cfg["auto_start"]), "open_positions": len(opens),
                "max_positions": int(self.cfg["max_positions"]),
                "max_long": int(self.cfg["max_long"]),
                "armed": [{"underlying": k, "trigger": v.get("trigger"),
                           "sig_low": v.get("sig_low"),
                           "signal_date": v["signal_date"].isoformat() if v.get("signal_date") else None,
                           "entered": k in self._entered_today}
                          for k, v in sorted(armed.items())],
                "armed_count": len(armed), "scanned_day": self._day,
                "config": self.config_dict()}

    def positions(self, trade_date=None, all_open=True) -> list[dict]:
        """Positions for ``trade_date`` (default today). Because this is a swing
        strategy, every still-OPEN position is always included."""
        db = get_db_session()
        try:
            q = db.query(HammerBreakoutPosition).filter(
                HammerBreakoutPosition.user_id == self.user_id)
            rows = q.order_by(HammerBreakoutPosition.id.desc()).all()
            try:
                d = date.fromisoformat(trade_date) if trade_date else date.today()
            except ValueError:
                d = date.today()
            keep = [r for r in rows if r.trade_date == d
                    or (r.exit_date == d)
                    or (all_open and r.status == "OPEN")]
            return [self._pos_dict(r) for r in keep]
        finally:
            db.close()

    @staticmethod
    def _pos_dict(r):
        f = lambda v: float(v) if v is not None else None
        return {"id": r.id, "date": r.trade_date.isoformat() if r.trade_date else None,
                "signal_date": r.signal_date.isoformat() if r.signal_date else None,
                "underlying": r.underlying, "direction": r.direction, "symbol": r.symbol,
                "exchange": r.exchange, "qty": r.qty, "trigger": f(r.trigger),
                "signal_low": f(r.signal_low), "entry_price": f(r.entry_price),
                "entry_time": r.entry_time, "target": f(r.target), "sl": f(r.sl), "ltp": f(r.ltp),
                "mtm": f(r.mtm), "mfe": f(r.mfe), "mae": f(r.mae), "status": r.status,
                "exit_price": f(r.exit_price), "exit_time": r.exit_time,
                "exit_date": r.exit_date.isoformat() if r.exit_date else None,
                "exit_reason": r.exit_reason, "paper": r.paper, "product": r.product,
                "hold_days": r.hold_days}

    # ── runtime persistence ──
    def _save_runtime(self):
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(json.dumps({
                "is_active": self.is_active, "auto_started_date": self._auto_started_date,
                "manual_stop_date": self._manual_stop_date}))
        except Exception as exc:
            logger.debug("hammer runtime save failed: %s", exc)

    def _load_runtime(self):
        try:
            if _STATE_FILE.exists():
                st = json.loads(_STATE_FILE.read_text()) or {}
                self.is_active = bool(st.get("is_active"))
                self._auto_started_date = st.get("auto_started_date")
                self._manual_stop_date = st.get("manual_stop_date")
        except Exception as exc:
            logger.debug("hammer runtime load failed: %s", exc)
