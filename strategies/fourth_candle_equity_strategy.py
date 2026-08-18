"""
4th-Candle CASH-EQUITY Strategy — live/paper engine (Equity Strategy #3).

Trades the STOCK directly on a 4th-candle breakout: LONG (buy) on a CALL bias,
SHORT (sell) on a PUT bias, as MIS intraday or CNC holding, with target/SL on the
stock price. Paper-mode default. Real orders only when paper_trade is off AND the
global trading gate is on — the same fencing as the other live strategies.
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
from core.models import FourthCandleEquityPosition
from research.prev_period_vwap import _candle_dt
from research.pmvwap_straddle.universe import Universe
from research.fourth_candle_equity import calculations as calc
from research.fourth_candle_equity.config import DEFAULT_CONFIG, sanitize, TIMEFRAME

logger = get_logger("strategy.fourth_candle_equity")

_STATE_FILE = settings.DATA_DIR / "strategy_configs" / "fourth_candle_equity_state.json"
MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)


class FourthCandleEquityStrategy:
    def __init__(self, broker: Broker, config: Optional[dict] = None, user_id: int = 0):
        self.broker = broker
        self.user_id = user_id
        self.universe = Universe(broker)
        self.cfg = sanitize(config or {})
        self._lock = threading.RLock()
        self.is_active = False
        self._day = None
        self._entered_today: set = set()
        self._auto_started_date = None
        self._manual_stop_date = None
        self._candle_bucket = None
        self._candle_cache: dict = {}
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
            if self.is_active and MARKET_OPEN <= now.time() <= MARKET_CLOSE:
                self._scan_entries(now)
            self._manage_open(now)
            return self.get_status()
        finally:
            self._lock.release()

    def _day_reset(self, now):
        d = now.date().isoformat()
        if self._day != d:
            self._day = d
            self._entered_today = {p.underlying for p in self._open_positions()}

    # ── entries ──
    def _scan_entries(self, now):
        opens = self._open_positions()
        if len(opens) >= int(self.cfg["max_positions"]):
            return
        longs = sum(1 for p in opens if p.direction == "LONG")
        shorts = sum(1 for p in opens if p.direction == "SHORT")
        for sym in self.cfg.get("symbols", []):
            sym = (sym or "").strip().upper()
            if not sym or sym in self._entered_today:
                continue
            if len(opens) >= int(self.cfg["max_positions"]):
                break
            try:
                self._maybe_enter(sym, now, longs, shorts)
                opens = self._open_positions()
                longs = sum(1 for p in opens if p.direction == "LONG")
                shorts = sum(1 for p in opens if p.direction == "SHORT")
            except Exception as exc:
                logger.debug("equity entry %s failed: %s", sym, exc)

    def _maybe_enter(self, sym, now, longs, shorts):
        token, exch = self.universe.resolve_equity_token(sym)
        if not token:
            return
        candles = self._today_5m(token, now)
        dc = calc.day_candles(candles, now.date())
        if len(dc) < 5:
            return
        an = calc.analyze_day(dc, reverse=bool(self.cfg.get("reverse_signal")))
        if not an or not an["bias"]:
            self._entered_today.add(sym)
            return
        bo = calc.find_breakout(dc, an, entry_cutoff=self.cfg["entry_cutoff"])
        if not bo:
            return
        direction = calc.direction_for(an["bias"])
        side = "LONG" if direction == "long" else "SHORT"
        if side == "LONG" and longs >= int(self.cfg["max_long"]):
            return
        if side == "SHORT" and shorts >= int(self.cfg["max_short"]):
            return
        ltp = self._eq_ltp(sym, exch)
        if not ltp:
            return
        qty = calc.position_qty(self.cfg, ltp)
        if qty <= 0:
            return
        order_side = OrderSide.BUY if direction == "long" else OrderSide.SELL
        if not self._place(sym, exch, qty, order_side, ltp):
            return
        target, stop = calc.resolve_target_sl(ltp, direction, self.cfg)
        db = get_db_session()
        try:
            db.add(FourthCandleEquityPosition(
                user_id=self.user_id, trade_date=now.date(), underlying=sym, direction=side,
                symbol=sym, exchange=exch, token=int(token), qty=qty, entry_price=round(ltp, 2),
                entry_time=now.strftime("%H:%M:%S"), target=target, sl=stop, ltp=round(ltp, 2),
                status="OPEN", product=self.cfg["product"], paper=bool(self.cfg["paper_trade"])))
            db.commit()
            self._entered_today.add(sym)
            logger.info("4th-candle-EQ ENTER %s %s qty=%d @ %.2f (paper=%s)", sym, side, qty, ltp,
                        self.cfg["paper_trade"])
            self._notify(
                f"🟢 <b>4th-CANDLE CASH {'PAPER' if self.cfg['paper_trade'] else 'LIVE'}</b>\n\n"
                f"Stock: <b>{sym}</b>  ·  {side}\n"
                f"Order: {'BUY' if side == 'LONG' else 'SELL'} {qty} {sym} ({self.cfg['product']})\n"
                f"Entry: ₹{round(ltp, 2)}  ·  Target ₹{target}  ·  SL ₹{stop}\n"
                f"Trigger: broke 4th-candle {'high' if an['bias'] == 'call' else 'low'}\n"
                f"Time: {now.strftime('%H:%M')}")
        except Exception as exc:
            db.rollback()
            logger.error("4th-candle-EQ save failed: %s", exc)
        finally:
            db.close()

    # ── manage open positions ──
    def _manage_open(self, now):
        opens = self._open_positions()
        if not opens:
            return
        squareoff = calc._parse_hhmm(self.cfg["square_off"])
        db = get_db_session()
        try:
            for p in opens:
                row = db.query(FourthCandleEquityPosition).filter(
                    FourthCandleEquityPosition.id == p.id).first()
                if not row or row.status != "OPEN":
                    continue
                ltp = self._eq_ltp(row.underlying, row.exchange)
                if ltp is None:
                    continue
                qty = int(row.qty or 0)
                is_long = row.direction == "LONG"
                sign = 1 if is_long else -1
                mtm = round((ltp - float(row.entry_price)) * qty * sign, 2)
                row.ltp = round(ltp, 2)
                row.mtm = mtm
                row.mfe = round(max(float(row.mfe or 0), mtm), 2)
                row.mae = round(min(float(row.mae or 0), mtm), 2)
                reason = None
                tgt, stp = float(row.target or 0), float(row.sl or 0)
                if is_long:
                    if tgt and ltp >= tgt:
                        reason = "TARGET"
                    elif stp and ltp <= stp:
                        reason = "STOP"
                else:
                    if tgt and ltp <= tgt:
                        reason = "TARGET"
                    elif stp and ltp >= stp:
                        reason = "STOP"
                if reason is None:
                    # MIS squares off intraday; CNC squares off after max_hold_days
                    hold = (now.date() - row.trade_date).days
                    if row.product == "MIS" and now.time() >= squareoff:
                        reason = "SQUAREOFF"
                    elif row.product == "CNC" and hold >= int(self.cfg["max_hold_days"]) and now.time() >= squareoff:
                        reason = "SQUAREOFF"
                if reason:
                    exit_side = OrderSide.SELL if is_long else OrderSide.BUY
                    self._place(row.underlying, row.exchange, qty, exit_side, ltp)
                    row.status = reason
                    row.exit_price = round(ltp, 2)
                    row.exit_reason = reason
                    row.exit_time = now.strftime("%H:%M:%S")
                    row.hold_days = (now.date() - row.trade_date).days
                    logger.info("4th-candle-EQ EXIT %s %s @ %.2f mtm=%.2f", row.underlying, reason, ltp, mtm)
                    emoji = {"TARGET": "🎯", "STOP": "🛑"}.get(reason, "⚪")
                    self._notify(
                        f"{emoji} <b>4th-CANDLE CASH EXIT</b> · {'PAPER' if row.paper else 'LIVE'}\n\n"
                        f"Stock: <b>{row.underlying}</b> ({row.direction})\n"
                        f"Exit: ₹{round(ltp, 2)} ({reason})  ·  Entry ₹{float(row.entry_price)}\n"
                        f"P&L: ₹{mtm}")
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error("4th-candle-EQ manage failed: %s", exc)
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
            logger.debug("4th-candle-EQ notify failed: %s", exc)

    # ── broker helpers (paper short-circuits) ──
    def _place(self, tradingsymbol, exch, qty, side, ref_price) -> bool:
        if self.cfg.get("paper_trade"):
            return True
        try:
            req = OrderRequest(
                tradingsymbol=tradingsymbol,
                exchange=Exchange.BSE if exch == "BSE" else Exchange.NSE,
                side=side, quantity=int(qty), order_type=OrderType.MARKET,
                product=ProductType.CNC if self.cfg["product"] == "CNC" else ProductType.MIS)
            resp = self.broker.place_order(req)
            return bool(resp and getattr(resp, "order_id", None))
        except Exception as exc:
            logger.error("4th-candle-EQ order failed (%s %s): %s", tradingsymbol, side, exc)
            return False

    def _eq_ltp(self, tradingsymbol, exch) -> Optional[float]:
        try:
            key = f"{'BSE' if exch == 'BSE' else 'NSE'}:{tradingsymbol}"
            d = self.broker.get_ltp([key]) or {}
            v = d.get(key)
            return float(v) if v else None
        except Exception:
            return None

    def _today_5m(self, token, now):
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
            return (db.query(FourthCandleEquityPosition)
                      .filter(FourthCandleEquityPosition.user_id == self.user_id,
                              FourthCandleEquityPosition.status == "OPEN").all())
        finally:
            db.close()

    # ── status / positions ──
    def get_status(self) -> dict:
        opens = self._open_positions()
        return {"is_active": self.is_active, "paper_trade": bool(self.cfg["paper_trade"]),
                "auto_start": bool(self.cfg["auto_start"]), "open_positions": len(opens),
                "long": sum(1 for p in opens if p.direction == "LONG"),
                "short": sum(1 for p in opens if p.direction == "SHORT"),
                "max_positions": int(self.cfg["max_positions"]),
                "max_long": int(self.cfg["max_long"]), "max_short": int(self.cfg["max_short"]),
                "config": self.config_dict()}

    def positions(self, trade_date=None) -> list[dict]:
        db = get_db_session()
        try:
            q = db.query(FourthCandleEquityPosition).filter(
                FourthCandleEquityPosition.user_id == self.user_id)
            if trade_date:
                try:
                    q = q.filter(FourthCandleEquityPosition.trade_date == date.fromisoformat(trade_date))
                except ValueError:
                    pass
            else:
                q = q.filter(FourthCandleEquityPosition.trade_date == date.today())
            return [self._pos_dict(r) for r in q.order_by(FourthCandleEquityPosition.id.desc()).all()]
        finally:
            db.close()

    @staticmethod
    def _pos_dict(r):
        f = lambda v: float(v) if v is not None else None
        return {"id": r.id, "date": r.trade_date.isoformat() if r.trade_date else None,
                "underlying": r.underlying, "direction": r.direction, "symbol": r.symbol,
                "exchange": r.exchange, "qty": r.qty, "entry_price": f(r.entry_price),
                "entry_time": r.entry_time, "target": f(r.target), "sl": f(r.sl), "ltp": f(r.ltp),
                "mtm": f(r.mtm), "mfe": f(r.mfe), "mae": f(r.mae), "status": r.status,
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
            logger.debug("4th-candle-EQ runtime save failed: %s", exc)

    def _load_runtime(self):
        try:
            if _STATE_FILE.exists():
                st = json.loads(_STATE_FILE.read_text()) or {}
                self.is_active = bool(st.get("is_active"))
                self._auto_started_date = st.get("auto_started_date")
                self._manual_stop_date = st.get("manual_stop_date")
        except Exception as exc:
            logger.debug("4th-candle-EQ runtime load failed: %s", exc)
