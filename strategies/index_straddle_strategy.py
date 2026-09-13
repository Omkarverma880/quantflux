"""
Index Straddle Engine — live / paper execution.

Runs the same rules the backtest runs, driven by live prices:

    eligible day?   distance to expiry inside the configured window, and the
                    day filters (prior-day range, gap, opening range) pass
    entry           at the configured time, both legs at once on one strike
    stop            the COMBINED premium moves against the position by stop_pct
    exit            stop, optional target, or the square-off time — always flat

Direction decides everything downstream:

    short   SELL call + SELL put   → credit received, stop is premium rising
    long    BUY  call + BUY  put   → debit paid,      stop is premium falling

Paper by default. Real orders require paper_trade off AND the global trading
gate on — the same fencing as every other live strategy here.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import date, datetime, time as dtime
from typing import Optional

from config import settings
from core.broker import (Broker, OrderRequest, OrderType, OrderSide,
                         ProductType, Exchange)
from core.database import get_db_session
from core.logger import get_logger
from core.models import IndexStraddlePosition
from research.pmvwap_straddle.universe import Universe
from research.index_straddle import engine as E
from research.index_straddle import optmodel as M
from research.index_straddle import options as O
from research.index_straddle.config import Config, load_config

logger = get_logger("strategy.index_straddle")

_STATE_FILE = settings.DATA_DIR / "strategy_configs" / "index_straddle_state.json"
INDEX_SPOT = "NIFTY 50"
MARKET_OPEN = dtime(9, 15)
MARKET_END = dtime(15, 30)


def _hhmm(s: str, fallback: dtime) -> dtime:
    try:
        h, m = str(s).split(":")
        return dtime(int(h), int(m))
    except Exception:
        return fallback


class IndexStraddleStrategy:
    def __init__(self, broker: Broker, cfg: Optional[Config] = None, user_id: int = 0):
        self.broker = broker
        self.user_id = user_id
        self.cfg = (cfg or load_config()).sanitized()
        self.universe = Universe(broker)
        self.resolver = O.ContractResolver(self.universe, self.cfg.index)
        self._lock = threading.RLock()
        self.is_active = False
        self._day = None
        self._entered_today = False
        self._skip_reason = ""
        self._group_id = None
        self._index_token: Optional[int] = None
        self._auto_started_date = None
        self._manual_stop_date = None
        self._last_error = ""
        self._day_ctx: dict = {}
        self._load_runtime()

    # ── control ──────────────────────────────────────────────────────
    def apply_config(self, partial: dict):
        with self._lock:
            merged = {**self.cfg.to_dict(), **(partial or {})}
            self.cfg = Config.from_dict(merged)
            self.resolver = O.ContractResolver(self.universe, self.cfg.index)
            self._day = None                       # re-arm with the new rules
        return self.cfg

    def config_dict(self) -> dict:
        return self.cfg.to_dict()

    def start(self, partial: Optional[dict] = None):
        with self._lock:
            if partial:
                self.apply_config(partial)
            self.is_active = True
            self._manual_stop_date = None
            self._save_runtime()
        logger.info("index_straddle started · %s", self.cfg.describe())
        return self.cfg

    def stop(self):
        with self._lock:
            self.is_active = False
            self._manual_stop_date = str(date.today())
            self._save_runtime()
        logger.info("index_straddle stopped")

    # ── the per-tick entry point ─────────────────────────────────────
    def check(self) -> dict:
        """Called on a timer by the engine. Arms the day, enters, manages, exits."""
        now = datetime.now()
        if now.time() < MARKET_OPEN or now.time() > MARKET_END:
            return {"skipped": "outside market hours"}
        self._maybe_autostart(now)
        if not self.is_active:
            return {"skipped": "inactive"}
        with self._lock:
            self._day_reset(now)
            try:
                if not self._entered_today:
                    self._maybe_enter(now)
                self._manage(now)
            except Exception as exc:
                self._last_error = str(exc)[:200]
                logger.error("index_straddle check failed: %s", exc)
        return self.get_status()

    def _maybe_autostart(self, now: datetime):
        if self.is_active or not self.cfg.auto_start:
            return
        today = str(now.date())
        if self._auto_started_date == today or self._manual_stop_date == today:
            return
        if now.time() >= MARKET_OPEN:
            self.is_active = True
            self._auto_started_date = today
            self._save_runtime()
            logger.info("index_straddle auto-started for %s", today)

    def _day_reset(self, now: datetime):
        d = now.date()
        if self._day == d:
            return
        self._day = d
        self._entered_today = False
        self._skip_reason = ""
        self._group_id = None
        self._day_ctx = {}
        # any leg still open from a previous day is not this strategy's to hold
        for r in self._open_positions():
            if r.trade_date != d:
                self._close_leg(r, "EOD", None, now)

    # ── entry ────────────────────────────────────────────────────────
    def _eligible(self, now: datetime) -> tuple[bool, str]:
        d = now.date()
        dte = E.calendar_dte(d)
        if not (self.cfg.dte_min <= dte <= self.cfg.dte_max):
            return False, f"DTE {dte} outside {self.cfg.dte_min}–{self.cfg.dte_max}"
        if self.cfg.weekdays and d.weekday() not in self.cfg.weekdays:
            return False, "weekday not selected"
        ctx = self._day_context(d)
        if not ctx:
            return False, "no context (need index history)"
        if self.cfg.skip_prior_range_sigma > 0:
            pr = ctx.get("prior_range_sigma")
            if pr and pr > self.cfg.skip_prior_range_sigma:
                return False, f"prior-day range {pr:.2f}σ > {self.cfg.skip_prior_range_sigma:g}σ"
        if self.cfg.skip_gap_sigma > 0:
            g = ctx.get("gap_sigma")
            if g is not None and abs(g) > self.cfg.skip_gap_sigma:
                return False, f"gap {g:+.2f}σ beyond ±{self.cfg.skip_gap_sigma:g}σ"
        return True, ""

    def _day_context(self, d: date) -> dict:
        """σ, prior-day range and gap, computed from recent index history.

        Same definitions as the backtest, so a live skip and a backtest skip
        agree on the same day.
        """
        if self._day_ctx:
            return self._day_ctx
        tok = self._resolve_index()
        if not tok:
            return {}
        try:
            import pandas as pd
            from research.nifty_open_reversion import data as D
            start = d - pd.Timedelta(days=60).to_pytimedelta()
            df = D.load_from_broker(self.broker, tok, start, d, "minute")
            if df.empty:
                return {}
            sig = M.daily_sigma_series(df)
            pr = M.prior_day_range_sigma(df)
            gp = M.gap_sigma(df)
            key = pd.Timestamp(d)
            self._day_ctx = {
                "sigma": float(sig.get(key, float("nan"))),
                "prior_range_sigma": float(pr.get(key, float("nan"))),
                "gap_sigma": float(gp.get(key, float("nan"))),
                "iv_regime": float(M.iv_regime_series(df, self.cfg.vrp).get(key, float("nan"))),
            }
        except Exception as exc:
            logger.debug("index_straddle context failed: %s", exc)
            return {}
        return self._day_ctx

    def _maybe_enter(self, now: datetime):
        t_in = _hhmm(self.cfg.entry_time, dtime(10, 0))
        t_out = _hhmm(self.cfg.exit_time, dtime(15, 20))
        if now.time() < t_in or now.time() >= t_out:
            return
        ok, why = self._eligible(now)
        if not ok:
            if why != self._skip_reason:
                self._skip_reason = why
                logger.info("index_straddle skipping %s: %s", now.date(), why)
            self._entered_today = True         # decided for the day
            return

        spot = self._index_ltp()
        if not spot:
            return
        ctx = self._day_ctx or {}
        de_now = M.effective_dte(E.calendar_dte(now.date()),
                                 now.hour * 60 + now.minute)
        legs = O.legs_for(self.cfg, spot, de_now,
                          float(ctx.get("iv_regime") or 0.12))
        exp = O.live_expiry(self.universe, self.cfg, now.date())
        ce = self.resolver.resolve(legs["call_strike"], "CE", exp)
        pe = self.resolver.resolve(legs["put_strike"], "PE", exp)
        if not ce or not pe:
            self._last_error = "could not resolve both contracts"
            return

        side = OrderSide.SELL if self.cfg.is_short else OrderSide.BUY
        action = "SELL" if self.cfg.is_short else "BUY"
        qty = self.cfg.qty
        gid = uuid.uuid4().hex[:12]
        placed = []
        prices = {}
        for con, opt in ((ce, "CE"), (pe, "PE")):
            sym = con.get("tradingsymbol")
            px = self._ltp(f"NFO:{sym}") or 0.0
            if not self._place(sym, "NFO", qty, side):
                # unwind whatever already went through — never sit half-legged
                for done_sym in placed:
                    self._place(done_sym, "NFO", qty,
                                OrderSide.BUY if self.cfg.is_short else OrderSide.SELL)
                self._last_error = f"leg {opt} failed; unwound"
                logger.error("index_straddle %s leg failed, unwound", opt)
                return
            placed.append(sym)
            prices[opt] = px

        combined = round(sum(prices.values()), 2)
        self._group_id = gid
        self._entered_today = True
        db = get_db_session()
        try:
            for con, opt in ((ce, "CE"), (pe, "PE")):
                db.add(IndexStraddlePosition(
                    user_id=self.user_id, trade_date=now.date(), group_id=gid,
                    direction=self.cfg.direction, structure=self.cfg.structure,
                    dte=E.calendar_dte(now.date()),
                    tradingsymbol=con.get("tradingsymbol"), exchange="NFO",
                    token=con.get("instrument_token"), opt_type=opt,
                    strike=legs["call_strike"] if opt == "CE" else legs["put_strike"],
                    expiry=exp, action=action, lots=self.cfg.lots, qty=qty,
                    entry_price=prices[opt], entry_time=now.strftime("%H:%M:%S"),
                    spot_entry=spot, combined_entry=combined, combined_ltp=combined,
                    status="OPEN", paper=self._paper()))
            db.commit()
        finally:
            db.close()

        self._notify(
            f"{'🔴' if self.cfg.is_short else '🟢'} <b>INDEX STRADDLE {action}</b> · "
            f"{'PAPER' if self._paper() else 'LIVE'}\n\n"
            f"{legs['label']} · {self.cfg.lots} lot(s) × {self.cfg.lot_size}\n"
            f"CE {legs['call_strike']:.0f} @ ₹{prices.get('CE', 0):.2f}\n"
            f"PE {legs['put_strike']:.0f} @ ₹{prices.get('PE', 0):.2f}\n"
            f"Combined ₹{combined:.2f}  ·  spot {spot:.1f}\n"
            f"Stop: combined {'above' if self.cfg.is_short else 'below'} "
            f"₹{self._stop_level(combined):.2f}")

    # ── management ───────────────────────────────────────────────────
    def _stop_level(self, combined_entry: float) -> float:
        f = self.cfg.stop_pct / 100.0
        return combined_entry * (1.0 + f) if self.cfg.is_short else combined_entry * (1.0 - f)

    def _target_level(self, combined_entry: float) -> Optional[float]:
        if self.cfg.target_pct <= 0:
            return None
        f = self.cfg.target_pct / 100.0
        return combined_entry * (1.0 - f) if self.cfg.is_short else combined_entry * (1.0 + f)

    def _manage(self, now: datetime):
        rows = self._open_positions()
        if not rows:
            return
        t_out = _hhmm(self.cfg.exit_time, dtime(15, 20))

        by_group: dict = {}
        for r in rows:
            by_group.setdefault(r.group_id, []).append(r)

        for gid, legs in by_group.items():
            ltps = {}
            for r in legs:
                px = self._ltp(f"NFO:{r.tradingsymbol}")
                if px is not None:
                    ltps[r.id] = px
            if len(ltps) < len(legs):
                continue
            combined = sum(ltps.values())
            entry = float(legs[0].combined_entry or 0) or sum(float(r.entry_price or 0) for r in legs)
            if entry <= 0:
                continue

            db = get_db_session()
            try:
                for r in legs:
                    row = db.query(IndexStraddlePosition).get(r.id)
                    if row:
                        row.ltp = ltps[r.id]
                        row.combined_ltp = round(combined, 2)
                db.commit()
            finally:
                db.close()

            reason = None
            if self.cfg.is_short and combined >= self._stop_level(entry):
                reason = "SL"
            elif (not self.cfg.is_short) and combined <= self._stop_level(entry):
                reason = "SL"
            tgt = self._target_level(entry)
            if reason is None and tgt is not None:
                if self.cfg.is_short and combined <= tgt:
                    reason = "TARGET"
                elif (not self.cfg.is_short) and combined >= tgt:
                    reason = "TARGET"
            if reason is None and now.time() >= t_out:
                reason = "EOD"

            if reason:
                for r in legs:
                    self._close_leg(r, reason, ltps.get(r.id), now)
                pnl = self._group_pnl(entry, combined)
                self._notify(
                    f"{'🟢' if pnl >= 0 else '🔴'} <b>INDEX STRADDLE EXIT</b> · "
                    f"{'PAPER' if self._paper() else 'LIVE'}\n\n"
                    f"{reason} · combined ₹{entry:.2f} → ₹{combined:.2f}\n"
                    f"P&L: ₹{pnl:,.0f}")

    def _group_pnl(self, entry: float, exit_: float) -> float:
        per_unit = (entry - exit_) if self.cfg.is_short else (exit_ - entry)
        return per_unit * self.cfg.qty

    def _close_leg(self, row, reason: str, ltp: Optional[float], now: datetime):
        side = OrderSide.BUY if row.action == "SELL" else OrderSide.SELL
        self._place(row.tradingsymbol, row.exchange or "NFO", int(row.qty or 0), side)
        px = ltp if ltp is not None else float(row.ltp or row.entry_price or 0)
        entry = float(row.entry_price or 0)
        per_unit = (entry - px) if row.action == "SELL" else (px - entry)
        db = get_db_session()
        try:
            r = db.query(IndexStraddlePosition).get(row.id)
            if r:
                r.exit_price = px
                r.exit_time = now.strftime("%H:%M:%S")
                r.spot_exit = self._index_ltp()
                r.exit_reason = reason
                r.mtm = round(per_unit * float(r.qty or 0), 2)
                r.status = "CLOSED"
                db.commit()
        finally:
            db.close()

    def square_off_all(self, reason: str = "MANUAL"):
        now = datetime.now()
        for r in self._open_positions():
            self._close_leg(r, reason, self._ltp(f"NFO:{r.tradingsymbol}"), now)

    # ── helpers ──────────────────────────────────────────────────────
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
            logger.error("index_straddle order failed (%s): %s", tradingsymbol, exc)
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

    def _resolve_index(self) -> Optional[int]:
        """NIFTY 50 spot token from the NSE instrument dump.

        Same lookup the other index strategies use — the index is a cash-segment
        instrument, so it is never in the NFO dump the Universe caches.
        """
        if self._index_token:
            return self._index_token
        try:
            for inst in self.broker.get_instruments("NSE") or []:
                if (inst.get("tradingsymbol") == INDEX_SPOT
                        or inst.get("name") == INDEX_SPOT):
                    self._index_token = int(inst["instrument_token"])
                    break
        except Exception as exc:
            logger.debug("index_straddle index token failed: %s", exc)
        if not self._index_token:
            try:
                tok = self.universe.nse_token(INDEX_SPOT)
                if tok:
                    self._index_token = int(tok)
            except Exception as exc:
                logger.debug("index_straddle nse_token fallback failed: %s", exc)
        return self._index_token

    def _notify(self, text: str):
        if not self.cfg.to_dict().get("telegram_alerts"):
            return
        try:
            from core import notify
            bot = self.cfg.to_dict().get("telegram_bot", "a")
            if notify.enabled(bot):
                notify.send(text, bot=bot)
        except Exception as exc:
            logger.debug("index_straddle notify failed: %s", exc)

    def _open_positions(self):
        db = get_db_session()
        try:
            return (db.query(IndexStraddlePosition)
                      .filter(IndexStraddlePosition.user_id == self.user_id,
                              IndexStraddlePosition.status == "OPEN").all())
        finally:
            db.close()

    @property
    def has_open_positions(self) -> bool:
        return bool(self._open_positions())

    def _realised_profit(self) -> float:
        db = get_db_session()
        try:
            rows = (db.query(IndexStraddlePosition)
                      .filter(IndexStraddlePosition.user_id == self.user_id,
                              IndexStraddlePosition.status == "CLOSED").all())
            return float(sum(float(r.mtm or 0) for r in rows))
        finally:
            db.close()

    # ── status / positions ───────────────────────────────────────────
    def get_status(self) -> dict:
        opens = self._open_positions()
        realised = self._realised_profit()
        today = date.today()
        dte = E.calendar_dte(today)
        eligible, why = (True, "") if self.is_active else (False, "inactive")
        if self.is_active:
            eligible, why = self._eligible(datetime.now())
        combined_entry = float(opens[0].combined_entry) if opens else None
        return {
            "is_active": self.is_active, "paper_trade": self._paper(),
            "direction": self.cfg.direction, "structure": self.cfg.structure,
            "describe": self.cfg.describe(),
            "index_ltp": self._index_ltp(),
            "dte_today": dte, "eligible_today": eligible,
            "skip_reason": why or self._skip_reason,
            "entered_today": self._entered_today,
            "open_positions": len(opens),
            "combined_entry": combined_entry,
            "stop_level": self._stop_level(combined_entry) if combined_entry else None,
            "target_level": self._target_level(combined_entry) if combined_entry else None,
            "day_context": self._day_ctx,
            "realised_profit": round(realised, 2),
            "equity": round(self.cfg.starting_capital + realised, 2),
            "last_error": self._last_error, "config": self.config_dict(),
        }

    def positions(self, trade_date: Optional[str] = None) -> list[dict]:
        db = get_db_session()
        try:
            rows = (db.query(IndexStraddlePosition)
                      .filter(IndexStraddlePosition.user_id == self.user_id)
                      .order_by(IndexStraddlePosition.id.desc()).all())
            try:
                d = date.fromisoformat(trade_date) if trade_date else date.today()
            except ValueError:
                d = date.today()
            keep = [r for r in rows if r.trade_date == d or r.status == "OPEN"]
            return [self._pos_dict(r) for r in keep]
        finally:
            db.close()

    @staticmethod
    def _pos_dict(r) -> dict:
        f = lambda v: float(v) if v is not None else None
        return {"id": r.id, "date": r.trade_date.isoformat() if r.trade_date else None,
                "group_id": r.group_id, "direction": r.direction, "structure": r.structure,
                "dte": r.dte, "tradingsymbol": r.tradingsymbol, "opt_type": r.opt_type,
                "strike": f(r.strike), "action": r.action,
                "expiry": r.expiry.isoformat() if r.expiry else None,
                "lots": r.lots, "qty": r.qty, "entry_price": f(r.entry_price),
                "entry_time": r.entry_time, "spot_entry": f(r.spot_entry),
                "ltp": f(r.ltp), "combined_entry": f(r.combined_entry),
                "combined_ltp": f(r.combined_ltp), "exit_price": f(r.exit_price),
                "exit_time": r.exit_time, "spot_exit": f(r.spot_exit),
                "exit_reason": r.exit_reason, "mtm": f(r.mtm),
                "status": r.status, "paper": r.paper}

    # ── runtime persistence ──────────────────────────────────────────
    def _save_runtime(self):
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(json.dumps({
                "is_active": self.is_active,
                "auto_started_date": self._auto_started_date,
                "manual_stop_date": self._manual_stop_date}))
        except Exception as exc:
            logger.debug("index_straddle runtime save failed: %s", exc)

    def _load_runtime(self):
        try:
            if _STATE_FILE.exists():
                d = json.loads(_STATE_FILE.read_text())
                self.is_active = bool(d.get("is_active"))
                self._auto_started_date = d.get("auto_started_date")
                self._manual_stop_date = d.get("manual_stop_date")
        except Exception as exc:
            logger.debug("index_straddle runtime load failed: %s", exc)
