"""
NIFTY opening-price mean-reversion — live / paper engine.

The same rules the backtest runs, driven by live prices:

    daily open  = the 09:15 one-minute candle's open
    BUY level   = open − offset      SELL level = open + offset
    SL / TP     = offset points against / in favour, on the INDEX
    entries     only inside the entry window; flat by the square-off time

In ``spot`` mode the leg is tracked on the index itself — you cannot trade the
index, so that mode is signal-tracking and stays paper. In ``option_buy`` /
``option_sell`` the signal is expressed as a real contract:

    option_buy   BUY → BUY CALL      SELL → BUY PUT
    option_sell  BUY → SELL PUT      SELL → SELL CALL

Paper by default. Real orders require paper_trade off AND the global trading
gate on — the same fencing as every other live strategy here.
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
from core.models import NiftyOpenReversionPosition
from research.pmvwap_straddle.universe import Universe
from research.nifty_open_reversion import engine as E
from research.nifty_open_reversion import options as O
from research.nifty_open_reversion.config import Config, load_config

logger = get_logger("strategy.nifty_open_reversion")

_STATE_FILE = settings.DATA_DIR / "strategy_configs" / "nifty_open_reversion_state.json"
INDEX_NAME = "NIFTY"
INDEX_SPOT = "NIFTY 50"
MARKET_OPEN = dtime(9, 15)
MARKET_END = dtime(15, 30)


class NiftyOpenReversionStrategy:
    def __init__(self, broker: Broker, cfg: Optional[Config] = None, user_id: int = 0):
        self.broker = broker
        self.user_id = user_id
        self.cfg = (cfg or load_config()).sanitized()
        self.universe = Universe(broker)
        self._lock = threading.RLock()
        self.is_active = False
        self._day = None
        self.daily_open: Optional[float] = None
        self.buy_level: Optional[float] = None
        self.sell_level: Optional[float] = None
        self._taken: dict[str, int] = {"BUY": 0, "SELL": 0}
        self._index_token: Optional[int] = None
        self._auto_started_date = None
        self._manual_stop_date = None
        self._last_error = ""
        self._load_runtime()

    # ── control ──
    def apply_config(self, partial: dict):
        with self._lock:
            merged = {**self.cfg.to_dict(), **(partial or {})}
            self.cfg = Config.from_dict(merged)
            self._day = None                      # re-arm with the new levels
        return self.cfg

    def config_dict(self) -> dict:
        return self.cfg.to_dict()

    def start(self, partial: Optional[dict] = None):
        with self._lock:
            if partial:
                self.apply_config(partial)
            self.is_active = True
            self._manual_stop_date = None
            self._day = None
            self._save_runtime()
        return self.get_status()

    def stop(self):
        with self._lock:
            self.is_active = False
            self._manual_stop_date = date.today().isoformat()
            self._save_runtime()
        return self.get_status()

    def _maybe_autostart(self, now: datetime):
        if not self.cfg.to_dict().get("auto_start"):
            return
        if self.is_active or self._manual_stop_date == now.date().isoformat():
            return
        if self._auto_started_date != now.date().isoformat():
            self.is_active = True
            self._auto_started_date = now.date().isoformat()
            self._save_runtime()

    # ── the tick ──
    def check(self) -> dict:
        if not self._lock.acquire(blocking=False):
            return self.get_status()
        try:
            now = datetime.now()
            self._maybe_autostart(now)
            self._day_reset(now)
            if self.is_active and MARKET_OPEN <= now.time() <= MARKET_END:
                self._arm(now)
                self._scan(now)
            self._manage(now)
            return self.get_status()
        except Exception as exc:
            self._last_error = str(exc)[:200]
            logger.error("open-reversion check failed: %s", exc)
            return self.get_status()
        finally:
            self._lock.release()

    def _day_reset(self, now: datetime):
        d = now.date().isoformat()
        if self._day != d:
            self._day = d
            self.daily_open = self.buy_level = self.sell_level = None
            self._taken = {"BUY": 0, "SELL": 0}
            for p in self._open_positions():
                self._taken[p.side] = self._taken.get(p.side, 0) + 1

    def _resolve_index(self) -> Optional[int]:
        if self._index_token:
            return self._index_token
        try:
            for inst in self.broker.get_instruments("NSE") or []:
                if inst.get("tradingsymbol") == INDEX_SPOT or inst.get("name") == INDEX_SPOT:
                    self._index_token = int(inst["instrument_token"])
                    break
        except Exception as exc:
            logger.debug("index token failed: %s", exc)
        return self._index_token

    def _arm(self, now: datetime):
        """Read the 09:15 candle once a day and place the two levels."""
        if self.daily_open is not None:
            return
        token = self._resolve_index()
        if not token:
            self._last_error = "Could not resolve NIFTY 50"
            return
        session_open = E._hhmm(self.cfg.entry_start, MARKET_OPEN)
        frm = datetime.combine(now.date(), session_open)
        try:
            raw = self.broker.get_historical_data(token, frm, now, "minute") or []
        except Exception as exc:
            logger.debug("open candle fetch failed: %s", exc)
            return
        first = None
        for c in raw:
            dt = c.get("date")
            if isinstance(dt, str):
                try:
                    dt = datetime.fromisoformat(dt)
                except Exception:
                    continue
            if isinstance(dt, datetime) and dt.replace(tzinfo=None).time() == session_open:
                first = c
                break
        if first is None:
            return                                  # the 09:15 bar has not printed yet
        self.daily_open = round(float(first["open"]), 2)
        off = self.cfg.offset_for(self.daily_open)
        self.buy_level = round(self.daily_open - off, 2)
        self.sell_level = round(self.daily_open + off, 2)
        logger.info("open-reversion armed: open %.2f → BUY %.2f / SELL %.2f",
                    self.daily_open, self.buy_level, self.sell_level)

    def _scan(self, now: datetime):
        if self.daily_open is None:
            return
        if now.time() > E._hhmm(self.cfg.entry_cutoff, dtime(10, 30)):
            return
        opens = self._open_positions()
        if len(opens) >= self.cfg.max_trades_per_day:
            return
        ltp = self._index_ltp()
        if not ltp:
            return
        for side, level, hit in (("BUY", self.buy_level, ltp <= (self.buy_level or 0)),
                                 ("SELL", self.sell_level, ltp >= (self.sell_level or 1e18))):
            if side == "BUY" and not self.cfg.trade_buy:
                continue
            if side == "SELL" and not self.cfg.trade_sell:
                continue
            if self._taken.get(side, 0) >= self.cfg.max_per_side_per_day or not hit:
                continue
            if any(p.side == side and p.status == "OPEN" for p in opens):
                continue
            try:
                self._enter(side, float(level), ltp, now)
            except Exception as exc:
                logger.error("open-reversion entry failed (%s): %s", side, exc)

    # ── entry ──
    def _lots_now(self) -> int:
        """Size from realised profit so far — the same ladder as the backtest."""
        realised = self._realised_profit()
        return E.lots_for(realised, self.cfg)

    def _enter(self, side: str, level: float, index_ltp: float, now: datetime):
        cfg = self.cfg
        sl_pts = cfg.sl_for(self.daily_open)
        tp_pts = cfg.tp_for(self.daily_open)
        long_ = side == "BUY"
        stop = round(level - sl_pts, 2) if long_ else round(level + sl_pts, 2)
        target = round(level + tp_pts, 2) if long_ else round(level - tp_pts, 2)
        lots = self._lots_now()
        qty = lots * cfg.lot_size

        leg = {"instrument_mode": cfg.instrument_mode, "tradingsymbol": INDEX_SPOT,
               "exchange": "NSE", "token": self._index_token, "opt_type": None,
               "strike": None, "expiry": None, "action": side, "entry_price": level}

        if cfg.instrument_mode != "spot":
            opt_type, action = O.map_signal(side, cfg.instrument_mode)
            strike = O.strike_for(self.daily_open, opt_type, cfg.strike_offset,
                                  O.STRIKE_STEP.get(INDEX_NAME, 50))
            exp = self.universe.expiry_for(INDEX_NAME, cfg.expiry_type, now.date())
            rec = self.universe.resolve(INDEX_NAME, exp, strike, opt_type) if exp else None
            if not rec:
                self._last_error = f"No {int(strike)} {opt_type} for {cfg.expiry_type} expiry"
                logger.warning(self._last_error)
                return
            prem = self._ltp(f"NFO:{rec['tradingsymbol']}")
            if not prem:
                self._last_error = f"No premium for {rec['tradingsymbol']}"
                return
            leg = {"instrument_mode": cfg.instrument_mode, "tradingsymbol": rec["tradingsymbol"],
                   "exchange": "NFO", "token": int(rec["token"]), "opt_type": opt_type,
                   "strike": float(strike), "expiry": rec["expiry"], "action": action,
                   "entry_price": round(float(prem), 2)}
            if not self._place(rec["tradingsymbol"], "NFO", qty,
                               OrderSide.BUY if action == "BUY" else OrderSide.SELL):
                return

        db = get_db_session()
        try:
            db.add(NiftyOpenReversionPosition(
                user_id=self.user_id, trade_date=now.date(), side=side,
                daily_open=self.daily_open, trigger_level=level,
                lots=lots, qty=qty, index_entry=round(index_ltp, 2),
                stop_loss=stop, target=target, entry_time=now.strftime("%H:%M:%S"),
                ltp=leg["entry_price"], status="OPEN", paper=bool(self._paper()),
                **leg))
            db.commit()
            self._taken[side] = self._taken.get(side, 0) + 1
            logger.info("open-reversion ENTER %s %s lots=%d @ %.2f (index %.2f)",
                        side, leg["tradingsymbol"], lots, leg["entry_price"], index_ltp)
            self._notify(
                f"🎯 <b>NIFTY OPEN ±{self.cfg.entry_offset:g} {'PAPER' if self._paper() else 'LIVE'}</b>\n\n"
                f"Signal: <b>{side}</b> at {level}  ·  open {self.daily_open}\n"
                f"Leg: {leg['action']} {leg['tradingsymbol']} × {qty} ({lots} lot)\n"
                f"Entry: ₹{leg['entry_price']}  ·  index SL {stop} / TP {target}\n"
                f"Time: {now.strftime('%H:%M')}")
        except Exception as exc:
            db.rollback()
            logger.error("open-reversion save failed: %s", exc)
        finally:
            db.close()

    # ── manage ──
    def _manage(self, now: datetime):
        opens = self._open_positions()
        if not opens:
            return
        index_ltp = self._index_ltp()
        close_t = E._hhmm(self.cfg.market_close, dtime(15, 30))
        db = get_db_session()
        try:
            for p in opens:
                row = db.query(NiftyOpenReversionPosition).filter(
                    NiftyOpenReversionPosition.id == p.id).first()
                if not row or row.status != "OPEN":
                    continue
                leg_ltp = (self._ltp(f"NFO:{row.tradingsymbol}")
                           if row.instrument_mode != "spot" else index_ltp)
                if leg_ltp:
                    row.ltp = round(float(leg_ltp), 2)
                    sign = 1 if (row.action or "BUY") == "BUY" else -1
                    mtm = round((float(leg_ltp) - float(row.entry_price)) * int(row.qty or 0) * sign, 2)
                    row.mtm = mtm
                    row.mfe = round(max(float(row.mfe or 0), mtm), 2)
                    row.mae = round(min(float(row.mae or 0), mtm), 2)

                reason = None
                if index_ltp:
                    long_ = row.side == "BUY"
                    sl, tgt = float(row.stop_loss or 0), float(row.target or 0)
                    if long_:
                        if index_ltp <= sl:
                            reason = "SL"
                        elif index_ltp >= tgt:
                            reason = "TARGET"
                    else:
                        if index_ltp >= sl:
                            reason = "SL"
                        elif index_ltp <= tgt:
                            reason = "TARGET"
                if reason is None and now.time() >= close_t:
                    reason = "EOD"
                if reason:
                    self._close(row, reason, leg_ltp, index_ltp, now)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error("open-reversion manage failed: %s", exc)
        finally:
            db.close()

    def _close(self, row, reason: str, leg_ltp, index_ltp, now: datetime):
        if row.instrument_mode != "spot":
            self._place(row.tradingsymbol, "NFO", int(row.qty or 0),
                        OrderSide.SELL if (row.action or "BUY") == "BUY" else OrderSide.BUY)
        exit_px = float(leg_ltp or row.entry_price)
        sign = 1 if (row.action or "BUY") == "BUY" else -1
        pts = round((exit_px - float(row.entry_price)) * sign, 2)
        row.status = reason
        row.exit_reason = reason
        row.exit_price = round(exit_px, 2)
        row.exit_time = now.strftime("%H:%M:%S")
        row.index_exit = round(float(index_ltp), 2) if index_ltp else None
        row.points = pts
        row.mtm = round(pts * int(row.qty or 0), 2)
        logger.info("open-reversion EXIT %s %s @ %.2f (%s) pnl=%.2f",
                    row.side, row.tradingsymbol, exit_px, reason, float(row.mtm))
        emoji = {"TARGET": "🎯", "SL": "🛑"}.get(reason, "⚪")
        self._notify(
            f"{emoji} <b>NIFTY OPEN ±{self.cfg.entry_offset:g} EXIT</b> · "
            f"{'PAPER' if row.paper else 'LIVE'}\n\n"
            f"{row.side} {row.tradingsymbol}\n"
            f"Exit ₹{round(exit_px, 2)} ({reason})  ·  entry ₹{float(row.entry_price)}\n"
            f"P&L: ₹{float(row.mtm):,.0f}")

    # ── helpers ──
    def _paper(self) -> bool:
        return bool(self.cfg.to_dict().get("paper_trade", True))

    def _place(self, tradingsymbol: str, exch: str, qty: int, side) -> bool:
        if self._paper() or qty <= 0:
            return True
        try:
            req = OrderRequest(
                tradingsymbol=tradingsymbol,
                exchange=Exchange.NFO if exch == "NFO" else Exchange.NSE,
                side=side, quantity=int(qty), order_type=OrderType.MARKET,
                product=ProductType.NRML)
            resp = self.broker.place_order(req)
            return bool(resp and getattr(resp, "order_id", None))
        except Exception as exc:
            logger.error("open-reversion order failed (%s): %s", tradingsymbol, exc)
            self._last_error = str(exc)[:200]
            return False

    def _ltp(self, key: str) -> Optional[float]:
        try:
            d = self.broker.get_ltp([key]) or {}
            v = d.get(key)
            return float(v) if v else None
        except Exception:
            return None

    def _index_ltp(self) -> Optional[float]:
        return self._ltp(f"NSE:{INDEX_SPOT}")

    def _notify(self, text: str):
        if not self.cfg.to_dict().get("telegram_alerts"):
            return
        try:
            from core import notify
            bot = self.cfg.to_dict().get("telegram_bot", "a")
            if notify.enabled(bot):
                notify.send(text, bot=bot)
        except Exception as exc:
            logger.debug("open-reversion notify failed: %s", exc)

    def _open_positions(self):
        db = get_db_session()
        try:
            return (db.query(NiftyOpenReversionPosition)
                      .filter(NiftyOpenReversionPosition.user_id == self.user_id,
                              NiftyOpenReversionPosition.status == "OPEN").all())
        finally:
            db.close()

    @property
    def has_open_positions(self) -> bool:
        return bool(self._open_positions())

    def _realised_profit(self) -> float:
        db = get_db_session()
        try:
            rows = (db.query(NiftyOpenReversionPosition)
                      .filter(NiftyOpenReversionPosition.user_id == self.user_id,
                              NiftyOpenReversionPosition.status != "OPEN").all())
            return float(sum(float(r.mtm or 0) for r in rows))
        finally:
            db.close()

    # ── status / positions ──
    def get_status(self) -> dict:
        opens = self._open_positions()
        realised = self._realised_profit()
        return {
            "is_active": self.is_active, "paper_trade": self._paper(),
            "instrument_mode": self.cfg.instrument_mode,
            "daily_open": self.daily_open, "buy_level": self.buy_level,
            "sell_level": self.sell_level, "index_ltp": self._index_ltp(),
            "taken_today": dict(self._taken), "open_positions": len(opens),
            "realised_profit": round(realised, 2),
            "equity": round(self.cfg.starting_capital + realised, 2),
            "lots_next": self._lots_now(), "armed": self.daily_open is not None,
            "last_error": self._last_error, "config": self.config_dict(),
        }

    def positions(self, trade_date: Optional[str] = None) -> list[dict]:
        db = get_db_session()
        try:
            q = db.query(NiftyOpenReversionPosition).filter(
                NiftyOpenReversionPosition.user_id == self.user_id)
            try:
                d = date.fromisoformat(trade_date) if trade_date else date.today()
            except ValueError:
                d = date.today()
            rows = q.order_by(NiftyOpenReversionPosition.id.desc()).all()
            keep = [r for r in rows if r.trade_date == d or r.status == "OPEN"]
            return [self._pos_dict(r) for r in keep]
        finally:
            db.close()

    @staticmethod
    def _pos_dict(r) -> dict:
        f = lambda v: float(v) if v is not None else None
        return {"id": r.id, "date": r.trade_date.isoformat() if r.trade_date else None,
                "side": r.side, "daily_open": f(r.daily_open), "trigger_level": f(r.trigger_level),
                "instrument_mode": r.instrument_mode, "tradingsymbol": r.tradingsymbol,
                "opt_type": r.opt_type, "strike": f(r.strike), "action": r.action,
                "expiry": r.expiry.isoformat() if r.expiry else None,
                "lots": r.lots, "qty": r.qty, "entry_price": f(r.entry_price),
                "entry_time": r.entry_time, "index_entry": f(r.index_entry),
                "stop_loss": f(r.stop_loss), "target": f(r.target), "ltp": f(r.ltp),
                "exit_price": f(r.exit_price), "exit_time": r.exit_time,
                "index_exit": f(r.index_exit), "exit_reason": r.exit_reason,
                "points": f(r.points), "mtm": f(r.mtm), "mfe": f(r.mfe), "mae": f(r.mae),
                "status": r.status, "paper": r.paper}

    # ── runtime persistence ──
    def _save_runtime(self):
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(json.dumps({
                "is_active": self.is_active, "auto_started_date": self._auto_started_date,
                "manual_stop_date": self._manual_stop_date}))
        except Exception as exc:
            logger.debug("open-reversion runtime save failed: %s", exc)

    def _load_runtime(self):
        try:
            if _STATE_FILE.exists():
                st = json.loads(_STATE_FILE.read_text()) or {}
                self.is_active = bool(st.get("is_active"))
                self._auto_started_date = st.get("auto_started_date")
                self._manual_stop_date = st.get("manual_stop_date")
        except Exception as exc:
            logger.debug("open-reversion runtime load failed: %s", exc)
