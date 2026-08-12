"""
4th-Candle Strategy — live/paper engine (Equity Strategy #2).

Buys ATM CE/PE of F&O stocks on a 4th-candle breakout (see research.fourth_candle
for the exact rules), positional (NRML), with target/SL on the option premium.
Paper-mode default. Real orders only when paper_trade is off AND the global
trading gate is on (settings.PAPER_TRADE False / TRADING_ENABLED True) — the same
fencing as the other live strategies.

Portfolio caps: at most ``max_positions`` open, split ``max_calls`` : ``max_puts``
(default 7:3). Driven by the in-process background loop (``check()`` per tick).
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
from core.models import FourthCandlePosition
from research.prev_period_vwap import _candle_dt
from research.pmvwap_straddle.universe import Universe
from research.fourth_candle import calculations as calc
from research.fourth_candle.config import DEFAULT_CONFIG, sanitize, TIMEFRAME

logger = get_logger("strategy.fourth_candle")

_STATE_FILE = settings.DATA_DIR / "strategy_configs" / "fourth_candle_state.json"
MARKET_OPEN = dtime(9, 15)
ENTRY_READY = dtime(9, 35)          # 4th candle complete
MARKET_CLOSE = dtime(15, 30)


class FourthCandleStrategy:
    def __init__(self, broker: Broker, config: Optional[dict] = None, user_id: int = 0):
        self.broker = broker
        self.user_id = user_id
        self.universe = Universe(broker)
        self.cfg = sanitize(config or {})
        self._lock = threading.RLock()
        self.is_active = False
        self._day = None
        self._entered_today: set = set()      # symbols entered today (one per day)
        self._auto_started_date = None
        self._manual_stop_date = None
        self._candle_bucket = None             # current 5-min bucket for the candle cache
        self._candle_cache: dict = {}          # token → today's 5-min candles (this bucket)
        self._load_runtime()

    # ── config / control ──
    def apply_config(self, partial: dict):
        with self._lock:
            self.cfg = sanitize({**self.cfg, **(partial or {})})
        return self.cfg

    def config_dict(self):
        return dict(self.cfg)

    def start(self, partial: Optional[dict] = None):
        with self._lock:
            if partial:
                self.apply_config(partial)
            self.is_active = True
            self._manual_stop_date = None
            self._save_runtime()

    def stop(self):
        with self._lock:
            self.is_active = False
            self._manual_stop_date = date.today().isoformat()
            self._save_runtime()

    def _maybe_autostart(self, now: datetime):
        if (self.cfg.get("auto_start") and not self.is_active
                and self._auto_started_date != now.date().isoformat()
                and self._manual_stop_date != now.date().isoformat()):
            self.is_active = True
            self._auto_started_date = now.date().isoformat()
            self._save_runtime()
            logger.info("4th-candle auto-started for %s", now.date())

    # ── main tick ──
    def check(self) -> dict:
        if not self._lock.acquire(blocking=False):
            return self.get_status()
        try:
            now = datetime.now()
            self._day_reset(now)
            self._maybe_autostart(now)
            if now.weekday() >= 5 or not (MARKET_OPEN <= now.time() <= MARKET_CLOSE):
                return self.get_status()
            self._manage_open(now)
            if self.is_active and now.time() >= ENTRY_READY and now.time() <= calc._parse_hhmm(self.cfg["entry_cutoff"]):
                self._scan_entries(now)
            return self.get_status()
        except Exception as exc:
            logger.error("4th-candle check failed: %s", exc)
            return self.get_status()
        finally:
            self._lock.release()

    def _day_reset(self, now: datetime):
        d = now.date().isoformat()
        if self._day != d:
            self._day = d
            self._entered_today = {p.underlying for p in self._open_positions()}

    # ── entries ──
    def _scan_entries(self, now: datetime):
        opens = self._open_positions()
        if len(opens) >= int(self.cfg["max_positions"]):
            return
        calls = sum(1 for p in opens if p.opt_type == "CE")
        puts = sum(1 for p in opens if p.opt_type == "PE")
        for sym in self.cfg.get("symbols", []):
            sym = (sym or "").strip().upper()
            if not sym or sym in self._entered_today:
                continue
            if len(opens) + 0 >= int(self.cfg["max_positions"]):
                break
            try:
                self._maybe_enter(sym, now, calls, puts)
                opens = self._open_positions()
                calls = sum(1 for p in opens if p.opt_type == "CE")
                puts = sum(1 for p in opens if p.opt_type == "PE")
            except Exception as exc:
                logger.debug("4th-candle entry %s failed: %s", sym, exc)

    def _maybe_enter(self, sym, now, calls, puts):
        token = self.universe.nse_token(sym)
        if not token:
            return
        candles = self._today_5m(token, now)
        dc = calc.day_candles(candles, now.date())
        if len(dc) < 5:
            return
        an = calc.analyze_day(dc)
        if not an or not an["bias"]:
            self._entered_today.add(sym)      # decided (mixed) → skip for the day
            return
        bo = calc.find_breakout(dc, an, entry_cutoff=self.cfg["entry_cutoff"])
        if not bo:
            return                            # bias set but no break yet — keep watching
        side = bo["opt_type"]
        if side == "CE" and calls >= int(self.cfg["max_calls"]):
            return
        if side == "PE" and puts >= int(self.cfg["max_puts"]):
            return
        expiry = self.universe.expiry_for(sym, self.cfg["expiry_type"], now.date())
        if not expiry:
            return
        atm = self.universe.atm_strike(sym, expiry, bo["underlying"])
        opt = self.universe.resolve(sym, expiry, atm, side) if atm is not None else None
        if not opt:
            return
        lot = int(self.universe.lot_size(sym) or opt.get("lot_size", 0) or 0)
        qty = lot * int(self.cfg["lots"])
        if qty <= 0:
            return
        ltp = self._opt_ltp(opt["tradingsymbol"])
        if not ltp:
            return
        if not self._place(opt["tradingsymbol"], qty, OrderSide.BUY, ltp):
            return
        target, stop = calc.resolve_target_sl(ltp, self.cfg)
        db = get_db_session()
        try:
            db.add(FourthCandlePosition(
                user_id=self.user_id, trade_date=now.date(), underlying=sym, opt_type=side,
                symbol=opt["tradingsymbol"], strike=atm, expiry=expiry.isoformat(),
                token=int(opt["token"]), qty=qty, lot=lot, entry_price=round(ltp, 2),
                entry_time=now.strftime("%H:%M:%S"), target=target, sl=stop, ltp=round(ltp, 2),
                status="OPEN", product=self.cfg["product"], paper=bool(self.cfg["paper_trade"])))
            db.commit()
            self._entered_today.add(sym)
            logger.info("4th-candle ENTER %s %s %s qty=%d @ %.2f (paper=%s)", sym, side,
                        opt["tradingsymbol"], qty, ltp, self.cfg["paper_trade"])
            self._notify(
                f"🟢 <b>4th-CANDLE {'PAPER' if self.cfg['paper_trade'] else 'LIVE'}</b>\n\n"
                f"Stock: <b>{sym}</b>  ·  {'CALL' if side == 'CE' else 'PUT'}\n"
                f"Order: BUY {qty} {opt['tradingsymbol']} ({self.cfg['product']})\n"
                f"Entry: ₹{round(ltp, 2)}  ·  Target ₹{target}  ·  SL ₹{stop}\n"
                f"Trigger: broke 4th-candle {'high' if side == 'CE' else 'low'}\n"
                f"Time: {now.strftime('%H:%M')}")
        except Exception as exc:
            db.rollback()
            logger.error("4th-candle position save failed: %s", exc)
        finally:
            db.close()

    # ── manage open positions ──
    def _manage_open(self, now: datetime):
        opens = self._open_positions()
        if not opens:
            return
        squareoff = calc._parse_hhmm(self.cfg["square_off"])
        db = get_db_session()
        try:
            for p in opens:
                row = db.query(FourthCandlePosition).filter(FourthCandlePosition.id == p.id).first()
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
                elif (row.expiry and date.fromisoformat(row.expiry) <= now.date()
                      and now.time() >= squareoff):
                    reason = "SQUAREOFF"
                if reason:
                    self._place(row.symbol, qty, OrderSide.SELL, ltp)
                    row.status = reason
                    row.exit_price = round(ltp, 2)
                    row.exit_reason = reason
                    row.exit_time = now.strftime("%H:%M:%S")
                    row.hold_days = (now.date() - row.trade_date).days
                    logger.info("4th-candle EXIT %s %s @ %.2f mtm=%.2f", row.symbol, reason, ltp, mtm)
                    emoji = {"TARGET": "🎯", "STOP": "🛑"}.get(reason, "⚪")
                    self._notify(
                        f"{emoji} <b>4th-CANDLE EXIT</b> · {'PAPER' if row.paper else 'LIVE'}\n\n"
                        f"Stock: <b>{row.underlying}</b> ({row.opt_type})\n"
                        f"Exit: ₹{round(ltp, 2)} ({reason})  ·  Entry ₹{float(row.entry_price)}\n"
                        f"P&L: ₹{mtm}")
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error("4th-candle manage failed: %s", exc)
        finally:
            db.close()

    # ── telegram (uses the selected universal bot) ──
    def _notify(self, text: str):
        if not self.cfg.get("telegram_alerts"):
            return
        try:
            from core import notify
            bot = self.cfg.get("telegram_bot", "a")
            if notify.enabled(bot):
                notify.send(text, bot=bot)
        except Exception as exc:
            logger.debug("4th-candle notify failed: %s", exc)

    # ── broker helpers (paper short-circuits) ──
    def _place(self, tradingsymbol, qty, side, ref_price) -> bool:
        if self.cfg.get("paper_trade"):
            return True                        # paper — no order
        try:
            req = OrderRequest(
                tradingsymbol=tradingsymbol, exchange=Exchange.NFO,
                side=side, quantity=int(qty),
                order_type=OrderType.MARKET,
                product=ProductType.NRML if self.cfg["product"] == "NRML" else ProductType.MIS)
            resp = self.broker.place_order(req)
            return bool(resp and getattr(resp, "order_id", None))
        except Exception as exc:
            logger.error("4th-candle order failed (%s %s): %s", tradingsymbol, side, exc)
            return False

    def _opt_ltp(self, tradingsymbol) -> Optional[float]:
        try:
            d = self.broker.get_ltp([f"NFO:{tradingsymbol}"]) or {}
            v = d.get(f"NFO:{tradingsymbol}")
            return float(v) if v else None
        except Exception:
            return None

    def _today_5m(self, token, now):
        # Cache per 5-min bucket: with a large live watchlist this method would
        # otherwise re-fetch every stock on every tick and saturate Zerodha's
        # ~3 req/s historical limit — starving other strategies and the research
        # modules (they'd get throttled and return "no stocks"). One fetch per
        # stock per 5-min candle is enough for a 5-min breakout strategy.
        bucket = now.replace(second=0, microsecond=0, minute=(now.minute // 5) * 5)
        if bucket != self._candle_bucket:
            self._candle_bucket = bucket
            self._candle_cache = {}
        if token in self._candle_cache:
            return self._candle_cache[token]
        frm = datetime.combine(now.date(), MARKET_OPEN)
        try:
            raw = self.broker.get_historical_data(token, frm, now, TIMEFRAME) or []
        except Exception:
            raw = []
        for c in raw:
            c["_dt"] = _candle_dt(c)
        self._candle_cache[token] = raw
        return raw

    def _open_positions(self):
        db = get_db_session()
        try:
            return (db.query(FourthCandlePosition)
                      .filter(FourthCandlePosition.user_id == self.user_id,
                              FourthCandlePosition.status == "OPEN").all())
        finally:
            db.close()

    # ── status / positions ──
    def get_status(self) -> dict:
        opens = self._open_positions()
        return {"is_active": self.is_active, "paper_trade": bool(self.cfg["paper_trade"]),
                "auto_start": bool(self.cfg["auto_start"]),
                "open_positions": len(opens),
                "calls": sum(1 for p in opens if p.opt_type == "CE"),
                "puts": sum(1 for p in opens if p.opt_type == "PE"),
                "max_positions": int(self.cfg["max_positions"]),
                "max_calls": int(self.cfg["max_calls"]), "max_puts": int(self.cfg["max_puts"]),
                "config": self.config_dict()}

    def positions(self, trade_date: Optional[str] = None) -> list[dict]:
        db = get_db_session()
        try:
            q = db.query(FourthCandlePosition).filter(FourthCandlePosition.user_id == self.user_id)
            if trade_date:
                try:
                    q = q.filter(FourthCandlePosition.trade_date == date.fromisoformat(trade_date))
                except ValueError:
                    pass
            else:
                q = q.filter(FourthCandlePosition.trade_date == date.today())
            rows = q.order_by(FourthCandlePosition.id.desc()).all()
            return [self._pos_dict(r) for r in rows]
        finally:
            db.close()

    @staticmethod
    def _pos_dict(r):
        f = lambda v: float(v) if v is not None else None
        return {"id": r.id, "date": r.trade_date.isoformat() if r.trade_date else None,
                "underlying": r.underlying, "opt_type": r.opt_type, "symbol": r.symbol,
                "strike": f(r.strike), "expiry": r.expiry, "qty": r.qty, "lot": r.lot,
                "entry_price": f(r.entry_price), "entry_time": r.entry_time,
                "target": f(r.target), "sl": f(r.sl), "ltp": f(r.ltp), "mtm": f(r.mtm),
                "mfe": f(r.mfe), "mae": f(r.mae), "status": r.status,
                "exit_price": f(r.exit_price), "exit_time": r.exit_time, "exit_reason": r.exit_reason,
                "paper": r.paper, "product": r.product, "hold_days": r.hold_days}

    # ── runtime persistence ──
    def _save_runtime(self):
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(json.dumps({
                "is_active": self.is_active, "auto_started_date": self._auto_started_date,
                "manual_stop_date": self._manual_stop_date}))
        except Exception as exc:
            logger.debug("4th-candle runtime save failed: %s", exc)

    def _load_runtime(self):
        try:
            if _STATE_FILE.exists():
                d = json.loads(_STATE_FILE.read_text())
                self.is_active = bool(d.get("is_active"))
                self._auto_started_date = d.get("auto_started_date")
                self._manual_stop_date = d.get("manual_stop_date")
        except Exception:
            pass


DEFAULT_STRATEGY_CONFIG = dict(DEFAULT_CONFIG, symbols=[])
