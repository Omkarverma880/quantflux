"""
Equity Previous-Month-VWAP Holding — LIVE strategy (paper or real).

Production port of Research #8. The *signal* logic is identical and reused
verbatim (``research.prev_period_vwap`` + ``research.pmvwap_equity.calculations``)
— only the execution layer is real here:

  • Entry  : price meets the Prev-Month VWAP while the Prev-Week VWAP is above it
             → BUY the equity (CNC / delivery).
  • Exit   : a server-side GTT for the target (and optional stop) so exits fire
             even if the app is offline; plus a max-hold-days square-off and an
             optional "close back below Prev-Month VWAP" exit managed by check().

Runs off the existing in-process background loop (no OS cron): the user logs in
each morning → their session is picked up → ``check()`` is ticked during market
hours. Positions persist in ``EquityHoldingPosition`` and are reloaded on
restart, so they carry across days and the daily token refresh.

Paper mode (default) fully simulates fills/exits from live LTP so the strategy
can be forward-tested before any real capital is deployed.
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

from config import settings
from core.broker import (
    Broker, OrderRequest, OrderSide, OrderType, ProductType, Exchange,
)
from core.logger import get_logger
from research.prev_period_vwap import compute_prev_period_vwaps, _candle_dt
from research.pmvwap_equity import calculations as calc
from research.pmvwap_equity.constants import TIMEFRAME_MAP, MARKET_OPEN, MARKET_CLOSE
from research.pmvwap_straddle.universe import Universe

logger = get_logger("strategy.equity_pmvwap_holding")

_INTERVAL_MIN = {"minute": 1, "3minute": 3, "5minute": 5, "10minute": 10,
                 "15minute": 15, "30minute": 30, "60minute": 60, "day": 375}

STATE_IDLE = "IDLE"
STATE_RUNNING = "RUNNING"

_STATE_FILE = settings.DATA_DIR / "strategy_configs" / "equity_pmvwap_holding_state.json"

DEFAULT_CONFIG: dict = {
    "paper_trade": True,                 # SAFETY: simulate fills until you flip this off
    "auto_start": False,                 # auto-start the strategy when you log in each morning
    "timeframe": "15m",
    "entry_mode": "cross_up",            # cross_up | touch
    "vwap_buffer": 0.0,
    "require_pw_above_pm": True,          # green above purple
    "one_signal_per_day": True,
    "entry_start": "09:20",
    "signal_cutoff": "15:00",
    "target_pct": 10.0,
    "stop_pct": 0.0,                     # 0 = no stop
    "max_hold_days": 20,
    "exit_on": "high_low",               # high_low → GTT tick exit (offline-safe) | close → strategy-managed on candle close
    "allow_reentry": False,              # off → skip stocks already held (strategy or demat)
    "exit_on_vwap_cross": False,         # exit when price closes back below Prev-Month VWAP
    "portfolio_mode": True,
    "portfolio_capital": 1000000,
    "max_open_positions": 10,
    "capital_per_trade": 25000,          # used when portfolio_mode is off
    "fixed_qty": 0,
    "history_days": 90,
    "symbols": [],                       # universe to trade (from a watchlist)
}


def _hhmm(s: str) -> dtime:
    try:
        h, m = str(s).split(":")
        return dtime(int(h), int(m))
    except Exception:
        return dtime(9, 20)


class EquityPMVwapHolding:
    def __init__(self, broker: Broker, config: dict, user_id: Optional[int] = None):
        self.broker = broker
        self.user_id = user_id
        self._lock = threading.RLock()
        self.universe = Universe(broker)

        self.cfg = dict(DEFAULT_CONFIG)
        self.apply_config(config or {}, save=False)

        self.is_active = False
        self.state = STATE_IDLE
        self.positions: list[dict] = []          # open + recently closed (session view)
        self._entered_today: set[str] = set()
        self._trading_date: Optional[date] = None
        self._auto_started_date: Optional[date] = None   # day auto-start last fired
        self._manual_stop_date: Optional[date] = None    # day the user manually stopped
        self._candle_cache: dict[tuple, tuple[float, list[dict]]] = {}
        self.last_error: Optional[str] = None
        self.last_check: Optional[str] = None

    # ── config ──
    def apply_config(self, config: dict, save: bool = True):
        for k, v in (config or {}).items():
            if k in DEFAULT_CONFIG and v is not None:
                self.cfg[k] = v
        # coerce / clamp
        self.cfg["symbols"] = [str(x).strip().upper() for x in (self.cfg.get("symbols") or []) if str(x).strip()]
        for b in ("paper_trade", "auto_start", "require_pw_above_pm", "one_signal_per_day",
                  "exit_on_vwap_cross", "portfolio_mode", "allow_reentry"):
            self.cfg[b] = bool(self.cfg[b])
        self.cfg["max_open_positions"] = max(1, int(self.cfg["max_open_positions"]))
        if self.cfg["timeframe"] not in TIMEFRAME_MAP:
            self.cfg["timeframe"] = "15m"
        if self.cfg.get("exit_on") not in ("close", "high_low"):
            self.cfg["exit_on"] = "high_low"

    def config_dict(self) -> dict:
        return dict(self.cfg)

    def start(self, config: dict):
        self.apply_config(config or {})
        self.is_active = True
        self.state = STATE_RUNNING
        self._manual_stop_date = None          # a manual start clears any stop-block
        self._day_reset(datetime.now())
        self._save_runtime()
        logger.info("EquityPMVwapHolding started (paper=%s, %d symbols)",
                    self.cfg["paper_trade"], len(self.cfg["symbols"]))

    def stop(self):
        self.is_active = False
        self.state = STATE_IDLE
        self._manual_stop_date = date.today()  # don't auto-restart for the rest of today
        self._save_runtime()
        logger.info("EquityPMVwapHolding stopped (open positions keep being managed)")

    def _maybe_autostart(self, now: datetime):
        """Auto-start on the morning tick if enabled and not manually stopped
        today. The background loop only ticks authenticated (logged-in) users,
        so this fires right after the daily Zerodha login."""
        if not self.cfg.get("auto_start") or self.is_active:
            return
        today = now.date()
        if self._manual_stop_date == today:
            return
        self.is_active = True
        self.state = STATE_RUNNING
        self._auto_started_date = today
        self._day_reset(now)
        self._save_runtime()
        logger.info("EquityPMVwapHolding auto-started on login (%s)", today.isoformat())

    # ── run-state persistence (survives restarts within the day) ──
    def _save_runtime(self):
        if not self.user_id:
            return
        try:
            data = {}
            if _STATE_FILE.exists():
                data = json.loads(_STATE_FILE.read_text()) or {}
            data[str(self.user_id)] = {
                "is_active": self.is_active,
                "auto_started_date": self._auto_started_date.isoformat() if self._auto_started_date else None,
                "manual_stop_date": self._manual_stop_date.isoformat() if self._manual_stop_date else None,
            }
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(json.dumps(data, indent=2))
        except Exception as exc:
            logger.debug("save runtime failed: %s", exc)

    def _load_runtime(self):
        if not self.user_id:
            return
        try:
            if not _STATE_FILE.exists():
                return
            d = (json.loads(_STATE_FILE.read_text()) or {}).get(str(self.user_id))
            if not d:
                return
            self.is_active = bool(d.get("is_active"))
            self.state = STATE_RUNNING if self.is_active else STATE_IDLE
            self._auto_started_date = date.fromisoformat(d["auto_started_date"]) if d.get("auto_started_date") else None
            self._manual_stop_date = date.fromisoformat(d["manual_stop_date"]) if d.get("manual_stop_date") else None
        except Exception as exc:
            logger.debug("load runtime failed: %s", exc)

    # ── helpers ──
    @property
    def _open(self) -> list[dict]:
        return [p for p in self.positions if p.get("state") == "OPEN"]

    def _interval(self) -> str:
        return TIMEFRAME_MAP[self.cfg["timeframe"]]

    def _exch_enum(self, exch: str) -> Exchange:
        return Exchange.BSE if exch == "BSE" else Exchange.NSE

    def _ltp(self, symbols: list[tuple]) -> dict:
        """symbols = [(tradingsymbol, exchange)]. Returns {tradingsymbol: ltp}."""
        keys = [f"{ex}:{s}" for s, ex in symbols]
        out: dict = {}
        try:
            raw = self.broker.get_ltp(keys) or {}
            for (s, ex) in symbols:
                out[s] = float(raw.get(f"{ex}:{s}", 0) or 0)
        except Exception as exc:
            logger.debug("LTP fetch failed: %s", exc)
        return out

    def _candles(self, token: int) -> list[dict]:
        key = (token, self.cfg["timeframe"], date.today())
        import time as _t
        cached = self._candle_cache.get(key)
        now = _t.monotonic()
        if cached and now - cached[0] < 60:
            return cached[1]
        frm = datetime.combine(date.today() - timedelta(days=int(self.cfg["history_days"])), MARKET_OPEN)
        to = datetime.combine(date.today(), MARKET_CLOSE)
        try:
            raw = self.broker.get_historical_data(token, frm, to, self._interval()) or []
        except Exception as exc:
            logger.debug("candles failed token=%s: %s", token, exc)
            raw = cached[1] if cached else []
        for c in raw:
            c["_dt"] = _candle_dt(c)
        self._candle_cache[key] = (now, raw)
        return raw

    # ── day reset + restore ──
    def _day_reset(self, now: datetime):
        today = now.date()
        if self._trading_date == today:
            return
        self._trading_date = today
        # Only stocks entered *today* block a same-day duplicate; older holdings
        # are governed by the re-entry rule, not this set.
        self._entered_today = {p["underlying"] for p in self._open if p.get("trade_date") == today.isoformat()}
        self._candle_cache.clear()

    # ── main tick ──
    def check(self) -> dict:
        with self._lock:
            now = datetime.now()
            self.last_check = now.strftime("%H:%M:%S")
            try:
                self._day_reset(now)
                self._maybe_autostart(now)
                self._manage_open(now)
                if self.is_active and self._in_entry_window(now):
                    self._try_entries(now)
                self.last_error = None
            except Exception as exc:
                self.last_error = str(exc)
                logger.error("Equity holding check failed: %s", exc)
            return self.get_status()

    def _in_entry_window(self, now: datetime) -> bool:
        return _hhmm(self.cfg["entry_start"]) <= now.time() <= _hhmm(self.cfg["signal_cutoff"])

    def _last_completed_close(self, token: int, now: datetime) -> Optional[float]:
        """Close of the most recently *completed* candle (for Exit-On = Close)."""
        candles = self._candles(token)
        mins = _INTERVAL_MIN.get(self._interval(), 15)
        last = None
        for c in candles:
            dt = c.get("_dt")
            if dt is not None and dt + timedelta(minutes=mins) <= now:
                last = c
        try:
            return float(last["close"]) if last else None
        except Exception:
            return None

    # ── manage open positions (reconcile + exits) ──
    def _manage_open(self, now: datetime):
        openp = self._open
        if not openp:
            return
        paper = self.cfg["paper_trade"]
        exit_on = self.cfg.get("exit_on", "high_low")
        ltp_map = self._ltp([(p["underlying"], p.get("exchange", "NSE")) for p in openp])

        # Live High/Low exits fire via GTT — a triggered GTT leaves the list.
        active_gtts = None
        if not paper and exit_on == "high_low":
            try:
                active_gtts = {str(g.get("id")) for g in (self.broker.get_gtts() or [])
                               if isinstance(g, dict) and g.get("id") is not None}
            except Exception:
                active_gtts = None      # unknown → skip GTT reconciliation this tick

        for p in openp:
            ltp = ltp_map.get(p["underlying"]) or float(p.get("ltp") or p["entry_price"])
            p["ltp"] = round(ltp, 2)
            p["pnl"] = round((ltp - p["entry_price"]) * p["qty"], 2)
            held = (now.date() - date.fromisoformat(p["trade_date"])).days
            p["hold_days"] = held

            # ── target / stop, per Exit-On semantics ──
            if exit_on == "high_low":
                if paper:                                   # tick touch (simulated)
                    if ltp >= p["target_price"]:
                        self._close(p, p["target_price"], "TARGET", now); continue
                    if p.get("stop_price") and ltp <= p["stop_price"]:
                        self._close(p, p["stop_price"], "STOP", now); continue
                elif active_gtts is not None:               # GTT reconciliation (OCO or single)
                    gid = p.get("target_gtt")
                    if not gid or str(gid).startswith("PAPER"):
                        # No real GTT (placement failed, or global paper mode) →
                        # protect the position ourselves via tick management.
                        if ltp >= p["target_price"]:
                            self._close(p, p["target_price"], "TARGET", now); continue
                        if p.get("stop_price") and ltp <= p["stop_price"]:
                            self._close(p, p["stop_price"], "STOP", now); continue
                    elif str(gid) not in active_gtts:       # a GTT leg fired → one exit only
                        if p.get("stop_price") and ltp <= float(p["stop_price"]) * 1.003:
                            self._close(p, p["stop_price"], "STOP", now)
                        else:
                            self._close(p, p["target_price"], "TARGET", now)
                        continue
            else:                                           # exit_on == "close"
                c = self._last_completed_close(int(p["token"]), now)
                if c is not None:
                    if c >= p["target_price"]:
                        self._close(p, c, "TARGET", now); continue
                    if p.get("stop_price") and c <= p["stop_price"]:
                        self._close(p, c, "STOP", now); continue

            # ── max-hold-days square off ──
            if held >= int(self.cfg["max_hold_days"]):
                self._close(p, ltp, "MAXHOLD", now); continue

            # ── optional exit when price closes back below Prev-Month VWAP ──
            if self.cfg["exit_on_vwap_cross"]:
                pm = self._current_pm_vwap(p)
                if pm and ltp < pm:
                    self._close(p, ltp, "VWAP_EXIT", now); continue

            self._persist(p)

    def _current_pm_vwap(self, p: dict) -> Optional[float]:
        try:
            candles = self._candles(int(p["token"]))
            if not candles:
                return None
            return compute_prev_period_vwaps(candles)[-1].get("prev_month_vwap")
        except Exception:
            return None

    def _demat_symbols(self) -> set:
        """Tradingsymbols already in the user's real demat holdings (upper-cased).
        Used to avoid double-buying something you already own when re-entry is
        off. Empty on any error / no session."""
        try:
            return {str(h.tradingsymbol).strip().upper() for h in (self.broker.get_holdings() or [])}
        except Exception:
            return set()

    # ── entries ──
    def _try_entries(self, now: datetime):
        symbols = self.cfg["symbols"]
        if not symbols:
            return
        held = {p["underlying"] for p in self._open}
        # Re-entry rule: when off, skip anything already held by the strategy OR
        # already in your real demat holdings. When on, only same-day duplicates
        # (``_entered_today``) are blocked.
        blocked = set() if self.cfg.get("allow_reentry") else (held | self._demat_symbols())
        if self.cfg["portfolio_mode"] and len(held) >= int(self.cfg["max_open_positions"]):
            return
        for sym in symbols:
            if self.cfg["portfolio_mode"] and len(self._open) >= int(self.cfg["max_open_positions"]):
                break
            if sym in blocked or sym in self._entered_today:
                continue
            try:
                self._maybe_enter(sym, now)
            except Exception as exc:
                logger.debug("entry eval failed %s: %s", sym, exc)

    def _maybe_enter(self, sym: str, now: datetime):
        token, exch = self.universe.resolve_equity_token(sym)
        if not token:
            return
        candles = self._candles(int(token))
        if not candles:
            return
        vwaps = compute_prev_period_vwaps(candles)
        signals = calc.find_holding_signals(
            candles, vwaps, entry_mode=self.cfg["entry_mode"], buffer=float(self.cfg["vwap_buffer"]),
            require_pw_above=bool(self.cfg["require_pw_above_pm"]), entry_start=self.cfg["entry_start"],
            signal_cutoff=self.cfg["signal_cutoff"], one_per_day=False, day=now.date())
        # only completed candles (bar closed) qualify for a live entry
        mins = _INTERVAL_MIN.get(self._interval(), 15)
        done = [s for s in signals if s["dt"] + timedelta(minutes=mins) <= now]
        if not done:
            return
        sig = done[-1]

        ltp = self._ltp([(sym, exch)]).get(sym) or sig["close"]
        if ltp <= 0:
            return
        qty = self._size(ltp)
        if qty <= 0:
            return
        if not self._place_buy(sym, exch, qty, ltp):
            return

        target = round(ltp * (1.0 + float(self.cfg["target_pct"]) / 100.0), 2)
        stop = round(ltp * (1.0 - float(self.cfg["stop_pct"]) / 100.0), 2) if float(self.cfg["stop_pct"]) > 0 else None
        pos = {
            "trade_date": now.date().isoformat(), "entry_time": now.strftime("%H:%M"),
            "underlying": sym, "exchange": exch, "token": int(token), "qty": qty,
            "entry_price": round(ltp, 2), "capital": round(ltp * qty, 2),
            "target_price": target, "stop_price": stop,
            "prev_month_vwap": sig["prev_month_vwap"], "prev_week_vwap": sig["prev_week_vwap"],
            "ltp": round(ltp, 2), "state": "OPEN",
            "target_gtt": None, "stop_gtt": None, "oco": False,
            "exit_price": None, "exit_time": None, "exit_date": None,
            "hold_days": 0, "pnl": 0.0, "exit_reason": None,
            "paper": bool(self.cfg["paper_trade"]),
        }
        # Live High/Low exits → server-side GTT. With a stop, use a single OCO
        # GTT so exactly one leg can ever fire (no double-sell). Close-mode exits
        # are managed by check() (no resting GTT).
        if not self.cfg["paper_trade"] and self.cfg.get("exit_on", "high_low") == "high_low":
            if stop:
                oco = self._place_oco(sym, exch, stop, target, ltp, qty)
                pos["target_gtt"] = oco
                pos["stop_gtt"] = oco
                pos["oco"] = bool(oco)
            else:
                pos["target_gtt"] = self._place_gtt(sym, exch, target, ltp, qty, "TARGET")
            if not pos["target_gtt"]:
                # GTT didn't place — the position is protected by tick-management
                # fallback in _manage_open, but surface it so you can act.
                self.last_error = f"{sym}: exit GTT not placed — managing exit in-app (check connectivity)"
                logger.warning(self.last_error)

        self.positions.append(pos)
        self._entered_today.add(sym)
        self._persist(pos)
        logger.info("ENTRY %s qty=%d @ %.2f (target %.2f%s, paper=%s)", sym, qty, ltp, target,
                    f", stop {stop}" if stop else "", self.cfg["paper_trade"])

    def _size(self, price: float) -> int:
        if self.cfg["portfolio_mode"]:
            alloc = float(self.cfg["portfolio_capital"]) / max(1, int(self.cfg["max_open_positions"]))
            return int(alloc // price) if price > 0 else 0
        return calc.position_qty(int(self.cfg["capital_per_trade"]), int(self.cfg["fixed_qty"]), price)

    # ── order execution (paper short-circuits) ──
    def _place_buy(self, sym: str, exch: str, qty: int, ref: float) -> bool:
        if self.cfg["paper_trade"]:
            return True
        try:
            self.broker.place_order(OrderRequest(
                tradingsymbol=sym, exchange=self._exch_enum(exch), side=OrderSide.BUY,
                quantity=qty, order_type=OrderType.MARKET, product=ProductType.CNC, tag="EQHOLD"))
            return True
        except Exception as exc:
            logger.error("BUY failed %s: %s", sym, exc)
            self.last_error = f"BUY {sym}: {exc}"
            return False

    def _place_sell(self, sym: str, exch: str, qty: int, tag: str) -> bool:
        if self.cfg["paper_trade"]:
            return True
        try:
            self.broker.place_order(OrderRequest(
                tradingsymbol=sym, exchange=self._exch_enum(exch), side=OrderSide.SELL,
                quantity=qty, order_type=OrderType.MARKET, product=ProductType.CNC, tag=tag[:20]))
            return True
        except Exception as exc:
            logger.error("SELL failed %s: %s", sym, exc)
            self.last_error = f"SELL {sym}: {exc}"
            return False

    def _place_gtt(self, sym: str, exch: str, trigger: float, ltp: float, qty: int, tag: str):
        try:
            return self.broker.place_gtt(
                tradingsymbol=sym, exchange=exch, trigger_price=float(trigger),
                last_price=float(ltp), quantity=int(qty), side="SELL", product="CNC",
                order_type="LIMIT" if tag == "TARGET" else "MARKET",
                price=float(trigger) if tag == "TARGET" else None)
        except Exception as exc:
            logger.error("GTT %s failed %s: %s", tag, sym, exc)
            return None

    def _place_oco(self, sym: str, exch: str, stop: float, target: float, ltp: float, qty: int):
        try:
            return self.broker.place_oco_gtt(
                tradingsymbol=sym, exchange=exch, stop_trigger=float(stop),
                target_trigger=float(target), last_price=float(ltp), quantity=int(qty),
                side="SELL", product="CNC")
        except Exception as exc:
            logger.error("OCO GTT failed %s: %s", sym, exc)
            return None

    def _cancel_gtt(self, gid):
        if gid:
            try:
                self.broker.delete_gtt(gid)
            except Exception:
                pass

    def _close(self, p: dict, price: float, reason: str, now: datetime):
        if not self.cfg["paper_trade"]:
            exit_on = self.cfg.get("exit_on", "high_low")
            # Only treat it as GTT-filled if a GTT actually exists for this leg;
            # otherwise (Close-mode, or a failed GTT) we must exit at market.
            gtt_filled = (exit_on == "high_low" and reason in ("TARGET", "STOP") and bool(p.get("target_gtt")))
            if gtt_filled:
                # A resting GTT fired server-side. For OCO the broker already
                # cancelled the other leg; for a single GTT there's no sibling.
                pass
            else:
                # We must exit at market (Close-mode target/stop, max-hold, VWAP
                # re-cross). Cancel any resting GTT(s) first, then market-sell.
                self._cancel_gtt(p.get("target_gtt"))
                if not p.get("oco") and p.get("stop_gtt"):
                    self._cancel_gtt(p.get("stop_gtt"))
                self._place_sell(p["underlying"], p.get("exchange", "NSE"), p["qty"], reason)
        p["state"] = "CLOSED"
        p["exit_price"] = round(float(price), 2)
        p["exit_time"] = now.strftime("%H:%M")
        p["exit_date"] = now.date().isoformat()
        p["hold_days"] = (now.date() - date.fromisoformat(p["trade_date"])).days
        p["pnl"] = round((p["exit_price"] - p["entry_price"]) * p["qty"], 2)
        p["exit_reason"] = reason
        self._persist(p)
        logger.info("EXIT %s @ %.2f (%s) pnl=%.2f", p["underlying"], p["exit_price"], reason, p["pnl"])

    # ── persistence ──
    def _persist(self, p: dict):
        if not self.user_id:
            return
        from core.database import get_db_session
        from core.models import EquityHoldingPosition
        db = get_db_session()
        try:
            row = (db.query(EquityHoldingPosition)
                     .filter_by(user_id=self.user_id, underlying=p["underlying"],
                                trade_date=date.fromisoformat(p["trade_date"]),
                                entry_time=p["entry_time"]).first())
            vals = dict(
                user_id=self.user_id, trade_date=date.fromisoformat(p["trade_date"]),
                entry_time=p["entry_time"], underlying=p["underlying"], exchange=p.get("exchange", "NSE"),
                token=p.get("token"), qty=p["qty"], entry_price=p["entry_price"], capital=p.get("capital"),
                target_price=p.get("target_price"), stop_price=p.get("stop_price"),
                prev_month_vwap=p.get("prev_month_vwap"), prev_week_vwap=p.get("prev_week_vwap"),
                ltp=p.get("ltp"), state=p["state"], target_gtt=str(p["target_gtt"]) if p.get("target_gtt") else None,
                stop_gtt=str(p["stop_gtt"]) if p.get("stop_gtt") else None,
                exit_price=p.get("exit_price"), exit_time=p.get("exit_time"),
                exit_date=date.fromisoformat(p["exit_date"]) if p.get("exit_date") else None,
                hold_days=p.get("hold_days"), pnl=p.get("pnl"), exit_reason=p.get("exit_reason"),
                paper=bool(p.get("paper", True)))
            if row:
                for k, v in vals.items():
                    setattr(row, k, v)
            else:
                db.add(EquityHoldingPosition(**vals))
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.error("persist position failed: %s", exc)
        finally:
            db.close()

    def restore_state(self) -> bool:
        if not self.user_id:
            return False
        from core.database import get_db_session
        from core.models import EquityHoldingPosition
        db = get_db_session()
        try:
            cutoff = date.today() - timedelta(days=120)
            rows = (db.query(EquityHoldingPosition)
                      .filter(EquityHoldingPosition.user_id == self.user_id,
                              EquityHoldingPosition.trade_date >= cutoff)
                      .order_by(EquityHoldingPosition.trade_date, EquityHoldingPosition.entry_time).all())
            self.positions = [self._row_to_pos(r) for r in rows]
            self._load_runtime()          # resume active/stopped state after a restart
            return bool(self.positions)
        except Exception as exc:
            logger.error("restore state failed: %s", exc)
            return False
        finally:
            db.close()

    @staticmethod
    def _row_to_pos(r) -> dict:
        f = lambda v: float(v) if v is not None else None
        return {
            "trade_date": r.trade_date.isoformat() if r.trade_date else None,
            "entry_time": r.entry_time, "underlying": r.underlying, "exchange": r.exchange or "NSE",
            "token": r.token, "qty": r.qty, "entry_price": f(r.entry_price), "capital": f(r.capital),
            "target_price": f(r.target_price), "stop_price": f(r.stop_price),
            "prev_month_vwap": f(r.prev_month_vwap), "prev_week_vwap": f(r.prev_week_vwap),
            "ltp": f(r.ltp), "state": r.state, "target_gtt": r.target_gtt, "stop_gtt": r.stop_gtt,
            "oco": bool(r.target_gtt and r.stop_gtt and r.target_gtt == r.stop_gtt),
            "exit_price": f(r.exit_price), "exit_time": r.exit_time,
            "exit_date": r.exit_date.isoformat() if r.exit_date else None,
            "hold_days": r.hold_days, "pnl": f(r.pnl), "exit_reason": r.exit_reason,
            "paper": bool(r.paper),
        }

    def reset(self):
        """Clear session positions (does not touch broker holdings/GTTs)."""
        if self.user_id:
            from core.database import get_db_session
            from core.models import EquityHoldingPosition
            db = get_db_session()
            try:
                db.query(EquityHoldingPosition).filter_by(user_id=self.user_id).delete()
                db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()
        self.positions = []
        self._entered_today = set()

    # ── status ──
    def get_status(self) -> dict:
        openp = self._open
        closed = [p for p in self.positions if p.get("state") == "CLOSED"]
        open_mtm = round(sum(float(p.get("pnl") or 0) for p in openp), 2)
        realised = round(sum(float(p.get("pnl") or 0) for p in closed), 2)
        deployed = round(sum(float(p.get("capital") or 0) for p in openp), 2)
        return {
            "is_active": self.is_active, "state": self.state,
            "paper_trade": self.cfg["paper_trade"], "config": self.config_dict(),
            "open_count": len(openp), "closed_count": len(closed),
            "open_mtm": open_mtm, "realised_pnl": realised, "capital_deployed": deployed,
            "positions": sorted(openp, key=lambda p: (p["trade_date"], p["entry_time"])),
            "closed": sorted(closed, key=lambda p: (p.get("exit_date") or "", p.get("exit_time") or ""), reverse=True)[:200],
            "last_check": self.last_check, "last_error": self.last_error,
        }
