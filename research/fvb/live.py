"""
The live desk — four modes, and the gates between them.

    HISTORICAL_BACKTEST   stored data only; this file is not involved
    LIVE_SIGNALS_ONLY     watches the session, runs the firewall, raises alerts, takes nothing
    PAPER_TRADING         the whole position lifecycle, recorded, with no broker order
    LIVE_TRADING          real orders

The default is LIVE_SIGNALS_ONLY, and moving off it is a deliberate act. Even in LIVE_TRADING a
real order needs two more things that this module does not control: the app out of paper mode,
and the risk fence down. Both are checked inside ``_order`` at the moment of every order, not
cached at start-up, because a fence that was down when the desk started is not the question.

A signal is not an order, an order is not a fill, and a cancel request is not a cancellation.
Each is tracked separately and the dashboard shows all three, because conflating them is how a
desk comes to believe it is flat when it is not.

The candle builder is the other thing worth reading. Ticks arrive irregularly; a one-minute
candle is only complete once a tick from the NEXT minute has been seen, and until then the
engine is not shown the bar at all. That is the live equivalent of the backtest's rule that a
decision is taken on the last minute of a closed candle.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from core.logger import get_logger
from core.models import StrategyConfig
from research.fvb import data as D
from research.fvb import execution as EX
from research.fvb import signals as SG
from research.fvb import structure as ST
from research.fvb.firewall import Firewall, FirewallParams

logger = get_logger("research.fvb.live")

CONFIG_NAME = "fvb_desk"
CHECK_EVERY_S = 3.0
IDLE_CHECK_EVERY_S = 20.0
LOG_MAX = 300

MODE_SIGNALS = "LIVE_SIGNALS_ONLY"
MODE_PAPER = "PAPER_TRADING"
MODE_LIVE = "LIVE_TRADING"
MODES = (MODE_SIGNALS, MODE_PAPER, MODE_LIVE)

DEFAULTS = {
    "running": False,
    "mode": MODE_SIGNALS,
    "variant": "B",
    "participation": D.OPTIONS,
    "firewall": True,
    "params": ST.P.as_dict(),
    "entry": SG.EP.as_dict(),
    "execution": EX.XP.as_dict(),
    "firewall_params": FirewallParams().as_dict(),
    "log": [],
    "alerts": [],
}

_last_check: dict[int, float] = {}


def _now() -> datetime:
    return datetime.now()


def load_config(db, user_id: int) -> StrategyConfig:
    row = (db.query(StrategyConfig)
             .filter(StrategyConfig.user_id == user_id,
                     StrategyConfig.strategy_name == CONFIG_NAME).first())
    if row is None:
        row = StrategyConfig(user_id=user_id, strategy_name=CONFIG_NAME,
                             config=dict(DEFAULTS))
        db.add(row)
        db.commit()
        db.refresh(row)
    if not isinstance(row.config, dict):
        row.config = dict(DEFAULTS)
    return row


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


def _push_alert(db, row: StrategyConfig, alert: dict) -> None:
    """Alerts are kept on the config so the page can show a timeline after a restart."""
    data = dict(row.config or {})
    al = list(data.get("alerts") or [])
    if any(a.get("event_id") == alert.get("event_id")
           and a.get("kind") == alert.get("kind") for a in al):
        return                                    # the firewall already de-duplicates; belt and braces
    al.append({**alert, "at": _now().isoformat(timespec="seconds")})
    _save(db, row, alerts=al[-LOG_MAX:])


class CandleBuilder:
    """Ticks in, completed one-minute bars out. A bar is withheld until its minute has passed."""

    def __init__(self) -> None:
        self.day: Optional[date] = None
        self.bars: list[dict] = []
        self.cur: Optional[dict] = None
        self.last_tick: Optional[datetime] = None

    def reset(self, d: date) -> None:
        self.day, self.bars, self.cur = d, [], None

    def add(self, ts: datetime, price: float) -> Optional[dict]:
        """Returns the bar that just COMPLETED, if this tick closed one."""
        if self.day != ts.date():
            self.reset(ts.date())
        self.last_tick = ts
        m = ts.hour * 60 + ts.minute
        if self.cur is None:
            self.cur = {"minute": m, "open": price, "high": price, "low": price,
                        "close": price, "date": ts.date()}
            return None
        if m == self.cur["minute"]:
            self.cur["high"] = max(self.cur["high"], price)
            self.cur["low"] = min(self.cur["low"], price)
            self.cur["close"] = price
            return None
        done = self.cur
        self.bars.append(done)
        self.cur = {"minute": m, "open": price, "high": price, "low": price,
                    "close": price, "date": ts.date()}
        return done

    def frame(self) -> pd.DataFrame:
        """Completed bars only. The bar in progress is never handed to the engine."""
        if not self.bars:
            return pd.DataFrame(columns=["minute", "open", "high", "low", "close", "date"])
        return pd.DataFrame(self.bars)

    def stale_for(self, now: datetime) -> Optional[float]:
        if self.last_tick is None:
            return None
        return (now - self.last_tick).total_seconds()


class Desk:
    """One desk per user, driven by the background loop."""

    STALE_AFTER_S = 90.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[int, dict] = {}
        self._builders: dict[int, CandleBuilder] = {}

    def state(self, user_id: int) -> dict:
        st = dict(self._state.get(user_id) or {})
        st.pop("firewall_obj", None)
        return st

    def _day(self, user_id: int, d: date) -> dict:
        st = self._state.get(user_id)
        if st is None or st.get("day") != d:
            st = {"day": d, "position": None, "taken": 0, "busy_until": -1,
                  "firewall_obj": None, "risk": None, "last_decision": None,
                  "levels": None, "rvol": None, "fw_state": "IDLE"}
            self._state[user_id] = st
            self._builders[user_id] = CandleBuilder()
        return st

    # ── the loop's entry point ────────────────────────────────────────
    def check(self, db, user_id: int, broker) -> None:
        import time as _t
        now = _t.time()
        row = load_config(db, user_id)
        cfg = row.config or {}
        if not cfg.get("running"):
            _last_check[user_id] = now
            return
        st = self._state.get(user_id) or {}
        gap = CHECK_EVERY_S if st.get("position") else IDLE_CHECK_EVERY_S
        if now - _last_check.get(user_id, 0.0) < gap:
            return
        _last_check[user_id] = now
        if broker is None:
            return
        try:
            self._tick(db, row, user_id, broker)
        except Exception as exc:
            logger.error("fvb desk: %s", exc)

    def _tick(self, db, row: StrategyConfig, user_id: int, broker) -> None:
        cfg = row.config or {}
        mode = cfg.get("mode") or MODE_SIGNALS
        p = ST.Params(**{**ST.P.as_dict(), **(cfg.get("params") or {})})
        ep = SG.EntryParams(**{**SG.EP.as_dict(), **(cfg.get("entry") or {})})
        xp = EX.ExecParams(**{**EX.XP.as_dict(), **(cfg.get("execution") or {})})
        fp = FirewallParams(**{**FirewallParams().as_dict(),
                               **(cfg.get("firewall_params") or {})})
        p = ST.Params(**{**p.as_dict(), "participation": cfg.get("participation") or D.OPTIONS})

        now = _now()
        d = now.date()
        st = self._day(user_id, d)
        builder = self._builders[user_id]

        try:
            q = broker.get_ltp(["NSE:NIFTY 50"]) or {}
            spot = float(list(q.values())[0]) if q else 0.0
        except Exception as exc:
            _log(db, row, f"feed error: {exc}")
            return
        if spot <= 0:
            return

        completed = builder.add(now, spot)
        stale = builder.stale_for(now)
        if stale is not None and stale > self.STALE_AFTER_S:
            _log(db, row, f"feed stale for {stale:.0f}s — no decision taken")
            return
        if completed is None:
            if st.get("position"):
                self._manage_live(db, row, user_id, broker, xp, st, now)
            return

        bars = builder.frame()
        if len(bars) < 6:
            return
        if st["firewall_obj"] is None:
            st["firewall_obj"] = Firewall(d, p, fp)
            st["risk"] = EX.DayRisk(xp)
        fw, risk = st["firewall_obj"], st["risk"]

        decision = int(completed["minute"])
        st["last_decision"] = f"{decision // 60:02d}:{decision % 60:02d}"
        atr_s = ST.atr(bars, p.atr_length)
        st["levels"] = ST.levels_for(bars, bars, d, decision, p)
        st["rvol"] = ST.relative_volume(bars, decision, p)

        if st.get("position"):
            self._manage_live(db, row, user_id, broker, xp, st, now)
            return

        ok, why = risk.may_trade(decision)
        if not ok:
            st["fw_state"] = "ENTRY_BLOCKED"
            return

        sig, rec = SG.evaluate(bars, bars, d, decision, p, ep, fw,
                               taken_today=st["taken"],
                               position_open=False, atr_series=atr_s)
        for att in fw.attempts:
            for al in att.alerts:
                _push_alert(db, row, al)
        st["fw_state"] = fw.attempts[-1].state if fw.attempts else "IDLE"

        if sig is None:
            return
        _log(db, row, f"BREAKOUT CONFIRMED — ENTRY ELIGIBLE · {sig.direction} "
                      f"{sig.level_name} {sig.level:.1f} · rvol "
                      f"{sig.volume.get('rvol')} · stop {sig.stop_level:.1f}")
        _push_alert(db, row, {
            "kind": "BREAKOUT CONFIRMED — ENTRY ELIGIBLE", "event_id": sig.event_id,
            "direction": sig.direction, "level": round(sig.level, 2),
            "level_name": sig.level_name, "spot_close": round(sig.spot_close, 2),
            "relative_volume": sig.volume.get("rvol"),
            "volume_state": sig.volume.get("state"),
            "stop": round(sig.stop_level, 2),
            "target": round(sig.target_level, 2) if sig.target_level else None,
            "rr": sig.rr, "retest": sig.retest,
            "note": "An eligible signal is not a fill and not a profit."})

        if mode == MODE_SIGNALS:
            _log(db, row, "signals-only mode — no position taken")
            return
        self._enter_live(db, row, user_id, broker, sig, xp, st, mode)

    # ── taking and managing a position ────────────────────────────────
    def _enter_live(self, db, row, user_id, broker, sig, xp: EX.ExecParams,
                    st: dict, mode: str) -> None:
        typ, strike = EX.pick_contract(sig.direction, sig.spot_close, xp)
        inst = self._resolve(broker, strike, typ)
        if not inst:
            _log(db, row, f"{typ} {strike:.0f} is not listed — no entry")
            return
        px = self._price(broker, inst["tradingsymbol"])
        if px is None:
            _log(db, row, "no quote on the contract — refusing to enter blind")
            return
        entry = px + xp.slippage_pts
        prem_stop = entry * (1 - xp.premium_stop_pct / 100) if xp.premium_stop_pct else None
        lots, qty, why = EX.size_position(entry, prem_stop, xp)
        if why:
            _log(db, row, f"not sized: {why}")
            return

        ticket = {"symbol": inst["tradingsymbol"], "strike": float(strike), "type": typ,
                  "qty": qty, "lots": lots, "entry": entry}
        order = {"ok": False, "order_id": None, "error": "paper mode"}
        if mode == MODE_LIVE:
            order = self._order(broker, user_id, ticket, "BUY")
            if not order["ok"]:
                _log(db, row, f"order not placed: {order['error']}")
        st["position"] = {
            "opened": _now().isoformat(timespec="seconds"),
            "mode": mode, "signal": sig.as_dict(), "ticket": ticket,
            "order": order, "filled": bool(order["ok"]),
            "premium_stop": prem_stop,
            "underlying_stop": sig.stop_level, "underlying_target": sig.target_level,
        }
        st["taken"] += 1
        _log(db, row, f"{'LIVE' if mode == MODE_LIVE else 'PAPER'} entry · {typ} "
                      f"{strike:.0f} x{lots} at {entry:.2f}"
                      + (f" · order {order['order_id']}" if order.get("order_id") else ""))

    def _manage_live(self, db, row, user_id, broker, xp: EX.ExecParams,
                     st: dict, now: datetime) -> None:
        pos = st["position"]
        if not pos:
            return
        t = pos["ticket"]
        px = self._price(broker, t["symbol"])
        if px is None:
            return
        spot = None
        try:
            q = broker.get_ltp(["NSE:NIFTY 50"]) or {}
            spot = float(list(q.values())[0]) if q else None
        except Exception:
            spot = None
        minute = now.hour * 60 + now.minute
        bullish = pos["signal"]["direction"] == "BULL"
        reason = None
        if spot is not None and xp.use_underlying_stop:
            hit = (spot <= pos["underlying_stop"]) if bullish else (spot >= pos["underlying_stop"])
            if hit:
                reason = EX.EXIT_UNDERLYING
        if reason is None and pos["premium_stop"] and px <= pos["premium_stop"]:
            reason = EX.EXIT_PREMIUM
        if reason is None and spot is not None and pos.get("underlying_target"):
            got = (spot >= pos["underlying_target"]) if bullish else (spot <= pos["underlying_target"])
            if got:
                reason = EX.EXIT_TARGET
        if reason is None and minute >= xp.squareoff:
            reason = EX.EXIT_EOD
        if reason is None:
            return

        exit_px = max(0.0, px - xp.slippage_pts)
        order = {"ok": False, "order_id": None, "error": "paper mode"}
        if pos["mode"] == MODE_LIVE and pos.get("filled"):
            order = self._order(broker, user_id, t, "SELL")
            if not order["ok"]:
                _log(db, row, f"EXIT ORDER FAILED — position may still be open: "
                              f"{order['error']}")
        gross = (exit_px - t["entry"]) * t["qty"]
        charges = EX.COST.round_trip(buy_px=t["entry"], sell_px=exit_px, qty=t["qty"])
        pnl = gross - charges
        st["risk"].record(pnl, minute)
        _log(db, row, f"exit {reason} at {exit_px:.2f} · "
                      f"{'+' if pnl >= 0 else ''}{pnl:,.0f}")
        self._record(db, user_id, pos, exit_px, reason, pnl)
        st["position"] = None
        st["busy_until"] = minute

    # ── broker plumbing ───────────────────────────────────────────────
    def _resolve(self, broker, strike: float, typ: str) -> Optional[dict]:
        try:
            from research.pmvwap_straddle.universe import Universe
            u = getattr(self, "_uni", None)
            if u is None or getattr(u, "broker", None) is not broker:
                u = self._uni = Universe(broker)
            today = _now().date()
            exps = sorted({e for e in u.expiries("NIFTY")} if hasattr(u, "expiries") else set())
            exp = next((e for e in exps if e >= today), None)
            if exp is None:
                from research.index_straddle.options import weekly_expiry
                exp = weekly_expiry(today)
            return u.resolve("NIFTY", exp, float(strike), typ)
        except Exception as exc:
            logger.debug("fvb desk: contract lookup failed: %s", exc)
            return None

    def _price(self, broker, tradingsymbol: str) -> Optional[float]:
        try:
            q = broker.get_ltp([f"NFO:{tradingsymbol}"]) or {}
            v = float(list(q.values())[0]) if q else 0.0
            return v if v > 0 else None
        except Exception:
            return None

    def _order(self, broker, user_id: int, t: dict, side: str) -> dict:
        """The only place this strategy can reach the broker. Three gates, checked here."""
        try:
            from config import settings
            if not settings.TRADING_ENABLED or settings.PAPER_TRADE:
                return {"ok": False, "order_id": None,
                        "error": "the app is in paper mode (TRADING_ENABLED / PAPER_TRADE)"}
            from core.risk_fence import is_trading_blocked
            blocked, why = is_trading_blocked(user_id)
            if blocked:
                return {"ok": False, "order_id": None, "error": f"risk fence: {why}"}
            from core.broker import Exchange, OrderRequest, OrderSide, OrderType, ProductType
            req = OrderRequest(tradingsymbol=t["symbol"], exchange=Exchange.NFO,
                               side=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
                               quantity=int(t["qty"]), order_type=OrderType.MARKET,
                               product=ProductType.MIS)
            res = broker.place_order(req)
            oid = getattr(res, "order_id", None) or (res or {}).get("order_id") if res else None
            return {"ok": bool(oid), "order_id": oid,
                    "error": None if oid else "no order id returned"}
        except Exception as exc:
            logger.error("fvb desk: order failed: %s", exc)
            return {"ok": False, "order_id": None, "error": str(exc)[:200]}

    def _record(self, db, user_id: int, pos: dict, exit_px: float,
                reason: str, pnl: float) -> None:
        try:
            from core.models import TradeLog
            t = pos["ticket"]
            today = _now().date()
            row = (db.query(TradeLog)
                     .filter(TradeLog.user_id == user_id,
                             TradeLog.strategy_name == CONFIG_NAME,
                             TradeLog.trade_date == today,
                             TradeLog.option_symbol == t["symbol"]).first())
            if row is None:
                row = TradeLog(user_id=user_id, strategy_name=CONFIG_NAME, trade_date=today)
                db.add(row)
            row.signal = pos["signal"]["direction"]
            row.option_symbol = t["symbol"]
            row.atm_strike = int(t["strike"])
            row.entry_price = t["entry"]
            row.exit_price = exit_px
            row.exit_type = reason
            row.exit_time = _now().strftime("%H:%M:%S")
            row.lot_size = int(t["qty"])
            row.pnl = pnl
            row.extra = {"mode": pos["mode"], "filled": pos.get("filled"),
                         "order": pos.get("order"), "level": pos["signal"]["level"],
                         "level_name": pos["signal"]["level_name"],
                         "rvol": (pos["signal"].get("volume") or {}).get("rvol"),
                         "event_id": pos["signal"]["event_id"]}
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.debug("fvb desk: trade log failed: %s", exc)


ENGINE = Desk()


def dashboard(db, user_id: int) -> dict:
    """What the page shows for the live desk."""
    row = load_config(db, user_id)
    cfg = row.config or {}
    st = ENGINE.state(user_id)
    return {"running": bool(cfg.get("running")), "mode": cfg.get("mode") or MODE_SIGNALS,
            "variant": cfg.get("variant"), "participation": cfg.get("participation"),
            "state": st.get("fw_state", "IDLE"),
            "last_decision": st.get("last_decision"),
            "levels": st.get("levels"), "rvol": st.get("rvol"),
            "position": st.get("position"), "taken_today": st.get("taken", 0),
            "log": (cfg.get("log") or [])[-60:],
            "alerts": (cfg.get("alerts") or [])[-40:]}
