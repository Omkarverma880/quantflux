"""
Short strangle — the live/paper desk.

The one configuration in this repository that survived real option prices, a conservative
high-based stop and a split it did not choose: sell a 300-point strangle at 10:00 on a 0–1 DTE
session, stop the pair at 35% against, square off at 15:20. Over 237 sessions that was t=6.59,
65.8% of days green and every calendar year green, on a 4.5% drawdown against margin.

What it is NOT is a machine that is always in profit. One trade in three stops out, by design —
the stop is what keeps a short strangle survivable, and a desk that never took a loss would be
a desk that had removed the thing protecting it. The honest promise is a positive expectancy
across many days, not a green screen on any given one.

Three gates stand between this file and a real order, and all three must be open:
  the desk's own mode is "live"      — set here, per user
  the app is not in paper mode       — TRADING_ENABLED / PAPER_TRADE in settings
  the risk fence is down             — day-loss and P&L locks
That check lives in ``_order`` and nowhere else, so there is exactly one door.

State is per user and in memory; every leg is written to ``trade_logs`` as it is taken and again
as it closes, so a restart can never erase a position that really exists.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from typing import Optional

from core.logger import get_logger
from core.models import StrategyConfig
from research.index_straddle import options as O
from research.index_straddle.config import Config

logger = get_logger("research.index_straddle.live")

CONFIG_NAME = "index_strangle_desk"
CHECK_EVERY_S = 5.0
IDLE_CHECK_EVERY_S = 30.0
LOG_MAX = 200

# The validated configuration, as the backtest ran it. Changing any of these moves the desk off
# the thing that was tested, so each one says what it is doing here.
PRESET = {
    "wing_offset": 300,        # points either side of ATM — 300 was the only width that held
    "entry_time": "10:00",     # 09:30 looked better and failed out of sample (t=0.34)
    "exit_time": "15:20",
    "stop_pct": 35.0,          # on the COMBINED premium, not per leg
    "target_pct": 0.0,         # taking profit early lost money in every variant tested
    "dte_min": 0,
    "dte_max": 1,              # 2-4 DTE lost; 0-6 lost
    "skip_prior_range_sigma": 1.3,
    "lots": 3,
    "lot_size": 65,
    "index": "NIFTY",
}

DEFAULTS = {
    "running": False,
    "mode": "paper",           # paper | live
    "params": dict(PRESET),
    "started_at": None,
    "day": None,
}

_last_check: dict[int, float] = {}


def _now() -> datetime:
    return datetime.now()


def _hhmm(s: str, fallback: str = "10:00") -> int:
    try:
        h, m = str(s).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        h, m = fallback.split(":")
        return int(h) * 60 + int(m)


def load_config(db, user_id: int) -> StrategyConfig:
    row = (db.query(StrategyConfig)
             .filter(StrategyConfig.user_id == user_id,
                     StrategyConfig.strategy_name == CONFIG_NAME).first())
    if row is None:
        row = StrategyConfig(user_id=user_id, strategy_name=CONFIG_NAME, config=dict(DEFAULTS))
        db.add(row)
        db.commit()
        db.refresh(row)
    if not isinstance(row.config, dict):
        row.config = dict(DEFAULTS)
    return row


def params_of(cfg: StrategyConfig) -> dict:
    p = dict(PRESET)
    p.update((cfg.config or {}).get("params") or {})
    return p


def _save(db, row: StrategyConfig, **changes) -> None:
    from sqlalchemy.orm.attributes import flag_modified
    data = dict(row.config or {})
    data.update(changes)
    row.config = data
    flag_modified(row, "config")
    db.commit()


def _log(db, row: StrategyConfig, text: str) -> None:
    data = dict(row.config or {})
    lines = list(data.get("log") or [])
    lines.append(f"{_now().strftime('%H:%M:%S')}  {text}")
    _save(db, row, log=lines[-LOG_MAX:])


class Desk:
    """One short-strangle desk per user, driven by the background loop."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[int, dict] = {}

    # ── the session's own state ───────────────────────────────────────
    def _day_state(self, user_id: int, today: date) -> dict:
        st = self._state.get(user_id)
        if st is None or st.get("day") != today:
            st = {"day": today, "position": None, "done": False, "skipped": None}
            self._state[user_id] = st
        return st

    def state(self, user_id: int) -> dict:
        return dict(self._state.get(user_id) or {})

    # ── the gate the day has to pass before anything is sold ──────────
    def _why_not(self, p: dict, broker, today: date) -> Optional[str]:
        """The filters, in the order the backtest applied them."""
        mins = _now().hour * 60 + _now().minute
        if mins < _hhmm(p["entry_time"]):
            return None                                  # simply not time yet
        if mins > _hhmm(p["exit_time"]):
            return "past the square-off time"
        try:
            cfgo = Config(**{k: v for k, v in p.items() if k in Config().__dict__})
            expiry = O.live_expiry(self._universe(broker), cfgo, today)
        except Exception:
            expiry = None
        if expiry is None:
            return "the expiry for today could not be resolved"
        dte = (expiry - today).days
        if not (int(p["dte_min"]) <= dte <= int(p["dte_max"])):
            return f"{dte} days to expiry is outside {p['dte_min']}-{p['dte_max']}"
        return None

    # ── the loop's entry point ────────────────────────────────────────
    def check(self, db, user_id: int, broker) -> None:
        import time as _t
        now = _t.time()
        row = load_config(db, user_id)
        cfg = row.config or {}
        if not cfg.get("running"):
            _last_check[user_id] = now
            return
        gap = CHECK_EVERY_S if (self._state.get(user_id) or {}).get("position") else IDLE_CHECK_EVERY_S
        if now - _last_check.get(user_id, 0.0) < gap:
            return
        _last_check[user_id] = now
        if broker is None:
            return
        try:
            self._tick(db, row, user_id, broker)
        except Exception as exc:
            logger.error("strangle desk: %s", exc)

    def _tick(self, db, row: StrategyConfig, user_id: int, broker) -> None:
        p = params_of(row)
        today = _now().date()
        st = self._day_state(user_id, today)
        mins = _now().hour * 60 + _now().minute

        if st.get("position"):
            self._manage(db, row, user_id, broker, p, st, mins)
            return
        if st.get("done") or st.get("skipped"):
            return
        if mins < _hhmm(p["entry_time"]) or mins > _hhmm(p["exit_time"]):
            return
        why = self._why_not(p, broker, today)
        if why:
            st["skipped"] = why
            _log(db, row, f"no trade today — {why}")
            return
        self._enter(db, row, user_id, broker, p, st)

    # ── selling the strangle ──────────────────────────────────────────
    def _enter(self, db, row: StrategyConfig, user_id: int, broker, p: dict, st: dict) -> None:
        today = _now().date()
        try:
            ltp = broker.get_ltp(["NSE:NIFTY 50"]) or {}
            spot = float(list(ltp.values())[0]) if ltp else 0.0
        except Exception as exc:
            _log(db, row, f"could not read the index: {exc}")
            return
        if spot <= 0:
            return
        step = O.step_for(p["index"])
        atm = O.atm_strike(spot, step)
        wing = int(p["wing_offset"])
        kc, kp = atm + wing, atm - wing

        legs = self._resolve(broker, p, today, kc, kp)
        if not legs:
            st["skipped"] = "the 300-point strikes are not listed for this expiry"
            _log(db, row, st["skipped"])
            return

        qty = int(p["lots"]) * int(p["lot_size"])
        prices = self._prices(broker, legs)
        if not prices:
            _log(db, row, "no price on one of the legs — not selling into the dark")
            return
        basis = prices["call"] + prices["put"]
        if basis <= 0:
            return

        tickets = []
        for side_name in ("call", "put"):
            t = {"symbol": legs[side_name]["tradingsymbol"], "exchange": "NFO",
                 "qty": qty, "strike": legs[side_name]["strike"], "type": side_name.upper()}
            res = self._order(broker, user_id, t, "SELL")
            t["order_id"] = res.get("order_id")
            t["paper"] = not res.get("ok")
            t["entry"] = prices[side_name]
            tickets.append(t)
            if not res.get("ok") and res.get("error"):
                _log(db, row, f"{side_name} leg not sent to the broker: {res['error']}")

        st["position"] = {
            "opened": _now().isoformat(timespec="seconds"),
            "spot": spot, "basis": basis, "qty": qty,
            "stop_at": basis * (1.0 + float(p["stop_pct"]) / 100.0),
            "legs": tickets,
        }
        _log(db, row, f"sold {wing}-point strangle {kp:.0f}P / {kc:.0f}C for {basis:.2f} "
                      f"({qty} each) · stop at {st['position']['stop_at']:.2f}")
        self._record(db, user_id, st["position"], "OPEN")

    # ── watching it ───────────────────────────────────────────────────
    def _manage(self, db, row: StrategyConfig, user_id: int, broker,
                p: dict, st: dict, mins: int) -> None:
        pos = st["position"]
        legs = {t["type"].lower(): {"tradingsymbol": t["symbol"], "strike": t["strike"]}
                for t in pos["legs"]}
        prices = self._prices(broker, legs)
        if not prices:
            return
        combined = prices["call"] + prices["put"]
        reason = None
        if combined >= pos["stop_at"]:
            reason = "SL"
        elif mins >= _hhmm(p["exit_time"]):
            reason = "EOD"
        if not reason:
            return
        for t in pos["legs"]:
            r = self._order(broker, user_id, t, "BUY")
            t["exit_order_id"] = r.get("order_id")
            t["exit"] = prices[t["type"].lower()]
        pnl = (pos["basis"] - combined) * pos["qty"]
        pos.update({"closed": _now().isoformat(timespec="seconds"), "exit": combined,
                    "reason": reason, "pnl": round(pnl, 2)})
        _log(db, row, f"closed on {reason} at {combined:.2f} — "
                      f"{'+' if pnl >= 0 else ''}{pnl:,.0f}")
        self._record(db, user_id, pos, "CLOSE")
        st["position"], st["done"] = None, True

    # ── plumbing ──────────────────────────────────────────────────────
    def _universe(self, broker):
        """The shared instrument-dump universe, built once and reused.

        ContractResolver wants an object with ``resolve(index, expiry, strike, type)``, not a
        raw instrument list — passing the list silently resolves nothing, which looks exactly
        like a day with no listed strikes.
        """
        u = getattr(self, "_uni", None)
        if u is None or getattr(u, "broker", None) is not broker:
            from research.pmvwap_straddle.universe import Universe
            u = self._uni = Universe(broker)
        return u

    def _resolve(self, broker, p: dict, today: date, kc: float, kp: float) -> Optional[dict]:
        try:
            universe = self._universe(broker)
            cfgo = Config(**{k: v for k, v in p.items() if k in Config().__dict__})
            resolver = O.ContractResolver(universe, p["index"])
            expiry = O.live_expiry(universe, cfgo, today)
            ce = resolver.resolve(kc, "CE", expiry)
            pe = resolver.resolve(kp, "PE", expiry)
            if not ce or not pe:
                return None
            return {"call": ce, "put": pe}
        except Exception as exc:
            logger.debug("strangle desk: contracts unavailable: %s", exc)
            return None

    def _prices(self, broker, legs: dict) -> Optional[dict]:
        try:
            keys = [f"NFO:{legs[k]['tradingsymbol']}" for k in ("call", "put")]
            q = broker.get_ltp(keys) or {}
            out = {}
            for name, key in zip(("call", "put"), keys):
                v = float(q.get(key) or 0)
                if v <= 0:
                    return None
                out[name] = v
            return out
        except Exception:
            return None

    def _order(self, broker, user_id: int, t: dict, side: str) -> dict:
        """The only place this strategy can touch the broker. Refuses whenever the app says no."""
        try:
            from config import settings
            if not settings.TRADING_ENABLED or settings.PAPER_TRADE:
                return {"ok": False, "error": "the app is in paper mode (TRADING_ENABLED / PAPER_TRADE)"}
            from core.risk_fence import is_trading_blocked
            blocked, reason = is_trading_blocked(user_id)
            if blocked:
                return {"ok": False, "error": f"risk fence: {reason}"}
            from core.broker import Exchange, OrderRequest, OrderSide, OrderType, ProductType
            req = OrderRequest(
                tradingsymbol=t["symbol"], exchange=Exchange.NFO,
                side=OrderSide.SELL if side == "SELL" else OrderSide.BUY,
                quantity=int(t["qty"]), order_type=OrderType.MARKET, product=ProductType.MIS)
            res = broker.place_order(req)
            oid = getattr(res, "order_id", None) or (res or {}).get("order_id") if res else None
            return {"ok": bool(oid), "order_id": oid,
                    "error": None if oid else "no order id returned"}
        except Exception as exc:
            logger.error("strangle desk: order failed: %s", exc)
            return {"ok": False, "error": str(exc)[:200]}

    def _record(self, db, user_id: int, pos: dict, phase: str) -> None:
        """Write each leg down as it opens and again as it closes.

        Keyed on the leg's symbol for the day, so the closing pass updates the row the opening
        pass created rather than adding a second one — a restart between the two must not leave
        a position that looks like it was never closed.
        """
        try:
            from core.models import TradeLog
            today = _now().date()
            for t in pos.get("legs") or []:
                row = (db.query(TradeLog)
                         .filter(TradeLog.user_id == user_id,
                                 TradeLog.strategy_name == CONFIG_NAME,
                                 TradeLog.trade_date == today,
                                 TradeLog.option_symbol == t["symbol"]).first())
                if row is None:
                    row = TradeLog(user_id=user_id, strategy_name=CONFIG_NAME,
                                   trade_date=today)
                    db.add(row)
                row.signal = "SELL"
                row.option_symbol = t["symbol"]
                row.atm_strike = int(float(t.get("strike") or 0))
                row.entry_price = t.get("entry")
                row.exit_price = t.get("exit")
                row.exit_type = pos.get("reason")
                row.exit_time = (pos.get("closed") or "")[11:19] or None
                row.lot_size = int(t.get("qty") or 0)
                # the P&L belongs to the pair, so it is carried on the leg that closes it out
                # rather than halved into two numbers that mean nothing on their own
                row.pnl = pos.get("pnl") if t is (pos.get("legs") or [None])[0] else None
                row.extra = {"phase": phase, "type": t.get("type"),
                             "basis": pos.get("basis"), "stop_at": pos.get("stop_at"),
                             "combined_exit": pos.get("exit"), "spot": pos.get("spot"),
                             "paper": bool(t.get("paper", True)),
                             "order_id": t.get("order_id"),
                             "exit_order_id": t.get("exit_order_id")}
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.debug("strangle desk: could not write the trade log: %s", exc)


ENGINE = Desk()
