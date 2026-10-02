"""
5 & 60 Minute Range — the live/paper desk.

It builds the day's index bars from repeated quotes, forms the two opening ranges, watches for a
cross and buys the configured strike. Paper unless the strategy's own mode is "live" AND the app
is out of paper mode AND the risk fence is down — that check lives in ``_order`` and nowhere else.

State is per user and in memory, but every ticket is written to ``trade_logs`` as soon as it is
taken and again when it closes, so a restart can never erase a trade that really happened.
"""
from __future__ import annotations

import threading
import time as _time
from datetime import date, datetime
from typing import Optional

import pandas as pd

from core.logger import get_logger
from core.models import StrategyConfig
from research.range_lab import strategy as ST

logger = get_logger("research.range_lab.live")

CONFIG_NAME = "range_lab"
CHECK_EVERY_S = 5.0
IDLE_CHECK_EVERY_S = 30.0
LOG_MAX = 200
SCAN_STRIKES = 30

DEFAULTS = {
    "running": False,
    "mode": "paper",                 # paper | live
    "params": ST.P.as_dict(),
    "started_at": None,
    "day": None,
}

_last_check: dict[int, float] = {}
_lock = threading.Lock()
_state: dict[int, dict] = {}


# ── settings ─────────────────────────────────────────────────────────
def load_config(db, user_id: int) -> dict:
    row = (db.query(StrategyConfig)
             .filter(StrategyConfig.user_id == user_id, StrategyConfig.strategy_name == CONFIG_NAME).first())
    cfg = {**DEFAULTS, **((row.config or {}) if row else {})}
    cfg["params"] = {**ST.P.as_dict(), **(cfg.get("params") or {})}
    return cfg


def save_config(db, user_id: int, updates: dict) -> dict:
    cfg = load_config(db, user_id)
    for k, v in (updates or {}).items():
        if k == "params" and isinstance(v, dict):
            cfg["params"] = {**cfg["params"], **{pk: pv for pk, pv in v.items() if pk in ST.P.as_dict()}}
        elif k in DEFAULTS and (v is not None or DEFAULTS[k] is None):
            cfg[k] = v
    row = (db.query(StrategyConfig)
             .filter(StrategyConfig.user_id == user_id, StrategyConfig.strategy_name == CONFIG_NAME).first())
    if row is None:
        db.add(StrategyConfig(user_id=user_id, strategy_name=CONFIG_NAME, config=cfg))
    else:
        row.config = cfg
    db.commit()
    return load_config(db, user_id)


def params_of(cfg: dict) -> ST.Params:
    return ST.Params(**{k: v for k, v in (cfg.get("params") or {}).items() if k in ST.P.as_dict()})


def _log(st: dict, line: str) -> None:
    log = st.setdefault("log", [])
    if log and log[-1][6:] == line[6:]:
        log[-1] = line
        return
    log.append(line)
    if len(log) > LOG_MAX:
        del log[:-LOG_MAX]


def _record(db, user_id: int, t: dict, cfg: dict) -> None:
    """One trade_logs row per ticket, written on entry and updated on exit."""
    if db is None:
        return
    try:
        from core.models import TradeLog
        key = f"{t.get('symbol')}@{t.get('taken_at')}"
        row = (db.query(TradeLog)
                 .filter(TradeLog.user_id == user_id, TradeLog.strategy_name == CONFIG_NAME,
                         TradeLog.trade_date == date.today())
                 .filter(TradeLog.extra["key"].astext == key).first())
        if row is None:
            row = TradeLog(user_id=user_id, strategy_name=CONFIG_NAME, trade_date=date.today())
            db.add(row)
        row.signal = t.get("side")
        row.option_symbol = t.get("symbol")
        row.atm_strike = int(float(t.get("strike") or 0))
        row.entry_price = t.get("entry")
        row.exit_price = t.get("exit")
        row.exit_type = t.get("exit_reason")
        row.exit_time = t.get("closed_at")
        row.lot_size = t.get("lot")
        row.pnl = t.get("pnl")
        row.extra = {"key": key, "mode": t.get("mode"), "lots": t.get("lots"), "qty": t.get("qty"),
                     "range": t.get("range"), "level": t.get("level"), "why": t.get("why"),
                     "target": t.get("target"), "stop": t.get("stop"),
                     "index": (cfg.get("params") or {}).get("index"),
                     "order_id": t.get("order_id"), "exit_error": t.get("exit_error"),
                     "needs_attention": bool(t.get("needs_attention"))}
        db.commit()
    except Exception as exc:
        logger.warning("range lab: could not record ticket: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass


# ── the chain ────────────────────────────────────────────────────────
class Chain:
    def __init__(self):
        self.day = None
        self.index = None
        self.rows: list[dict] = []
        self.expiry = None

    def load(self, broker, index: str) -> list[dict]:
        today = date.today()
        if self.day == today and self.index == index and self.rows:
            return self.rows
        exch = "BFO" if index.upper() in ("SENSEX", "BANKEX") else "NFO"
        rows = []
        for i in broker.get_instruments(exch) or []:
            if i.get("name") != index.upper() or i.get("instrument_type") not in ("CE", "PE"):
                continue
            exp = i.get("expiry")
            exp = exp if isinstance(exp, date) else (pd.Timestamp(str(exp)).date() if exp else None)
            if not exp or exp < today:
                continue
            rows.append({"symbol": i["tradingsymbol"], "option_type": i["instrument_type"],
                         "strike": float(i.get("strike") or 0), "expiry": exp,
                         "lot_size": int(i.get("lot_size") or 0), "exchange": exch})
        self.expiry = min((r["expiry"] for r in rows), default=None)
        self.rows = [r for r in rows if r["expiry"] == self.expiry]
        self.day, self.index = today, index
        return self.rows


CHAIN = Chain()

SPOT_KEY = {"NIFTY": "NSE:NIFTY 50", "BANKNIFTY": "NSE:NIFTY BANK",
            "FINNIFTY": "NSE:NIFTY FIN SERVICE", "MIDCPNIFTY": "NSE:NIFTY MID SELECT",
            "SENSEX": "BSE:SENSEX"}


def _spot(broker, index: str) -> float:
    key = SPOT_KEY.get(index.upper(), "NSE:NIFTY 50")
    try:
        return float((broker.get_quote([key]) or {}).get(key, {}).get("last_price") or 0)
    except Exception as exc:
        logger.debug("range lab: spot quote failed: %s", exc)
        return 0.0


def _quote_one(broker, row: dict) -> dict:
    key = f"{row['exchange']}:{row['symbol']}"
    try:
        q = (broker.get_quote([key]) or {}).get(key, {}) or {}
    except Exception as exc:
        logger.debug("range lab: option quote failed: %s", exc)
        return {}
    depth = q.get("depth") or {}
    ask = next((float(x["price"]) for x in depth.get("sell") or [] if float(x.get("price") or 0) > 0), None)
    bid = next((float(x["price"]) for x in depth.get("buy") or [] if float(x.get("price") or 0) > 0), None)
    return {"ltp": float(q.get("last_price") or 0) or None, "bid": bid, "ask": ask}


def _pick(broker, index: str, side: str, strike: float) -> Optional[dict]:
    for r in CHAIN.load(broker, index):
        if r["option_type"] == side and float(r["strike"]) == float(strike):
            return r
    return None


# ── the day's index bars, built from quotes ──────────────────────────
def _tick_bar(st: dict, minute: int, price: float) -> None:
    """Fold this quote into the minute bar it belongs to."""
    bars = st.setdefault("bars", {})
    b = bars.get(minute)
    if b is None:
        bars[minute] = {"minute": minute, "open": price, "high": price, "low": price, "close": price}
        return
    b["high"] = max(b["high"], price)
    b["low"] = min(b["low"], price)
    b["close"] = price


def _closed_bars(st: dict, now_minute: int) -> list[dict]:
    """Only bars that have finished. The current minute is still forming and must not be read."""
    return [b for m, b in sorted(st.get("bars", {}).items()) if m < now_minute]


class Desk:
    """Forms the ranges, waits for a cross, buys, and manages the exit."""

    def check(self, db, user_id: int, broker) -> dict:
        now = datetime.now()
        cfg = load_config(db, user_id)
        if not cfg.get("running"):
            return {"skipped": "not started"}
        p = params_of(cfg)
        today = str(now.date())
        minute = now.hour * 60 + now.minute

        st = _state.setdefault(user_id, {"day": today, "tickets": [], "log": [], "bars": {}})
        if st["day"] != today:
            st.update(day=today, tickets=[], log=[], bars={}, levels=None, used=[])

        holding = any(not t.get("closed") for t in st["tickets"])
        every = CHECK_EVERY_S if (holding or minute >= p.first_entry - 1) else IDLE_CHECK_EVERY_S
        with _lock:
            if _time.time() - _last_check.get(user_id, 0) < every:
                return {"skipped": "too soon"}
            _last_check[user_id] = _time.time()

        spot = _spot(broker, p.index)
        if spot <= 0:
            return {"state": "no spot price", "tickets": st["tickets"]}
        _tick_bar(st, minute, spot)

        bars = _closed_bars(st, minute)
        lv = ST.levels(bars, p)
        st["levels"] = lv
        out = {"minute": ST.hhmm(minute), "mode": cfg["mode"], "spot": spot,
               "levels": lv, "tickets": st["tickets"], "in_force": ST.range_in_force(minute, p)}

        # manage anything open first — an exit always outranks a new entry
        for t in st["tickets"]:
            if t.get("closed"):
                continue
            q = _quote_one(broker, {"exchange": t["exchange"], "symbol": t["symbol"]})
            price = q.get("bid") or q.get("ltp")
            if price is not None:
                t["ltp"] = price
                t["unrealised"] = round((float(price) - t["entry"]) * t["qty"], 2)
            if minute >= p.squareoff:
                self._close(db, user_id, broker, cfg, t, float(price or t["entry"]), "SQUAREOFF")
            elif price is not None and float(price) <= t["stop"]:
                self._close(db, user_id, broker, cfg, t, float(price), "STOP")
            elif price is not None and float(price) >= t["target"]:
                self._close(db, user_id, broker, cfg, t, float(price), "TARGET")

        if not lv.get("A"):
            return {**out, "state": f"forming the {p.range_a_min}-minute range"}
        if minute < p.first_entry:
            return {**out, "state": f"waiting for {ST.hhmm(p.first_entry)}"}
        if any(not t.get("closed") for t in st["tickets"]):
            return {**out, "state": "holding"}
        if len(st["tickets"]) >= p.max_trades_per_day:
            return {**out, "state": f"done — {p.max_trades_per_day} trade(s) taken"}
        if minute > p.last_entry:
            return {**out, "state": f"no new entry after {ST.hhmm(p.last_entry)}"}

        which = ST.range_in_force(minute, p)
        level = lv.get(which)
        if not level:
            return {**out, "state": f"the {p.range_b_min}-minute range is not complete yet"}
        if len(bars) < 2:
            return {**out, "state": "waiting for two closed bars"}
        sig = ST.signal(float(bars[-2]["close"]), float(bars[-1]["close"]), level, p)
        out["signal"] = sig
        if not sig:
            return {**out, "state": f"watching the {which} range "
                                    f"{level['low']:,.1f}–{level['high']:,.1f}"}
        key = f"{which}:{sig['level']}:{sig['side']}"
        if p.one_trade_per_level and key in st.setdefault("used", []):
            return {**out, "state": f"{key} already taken today"}

        self._buy(db, user_id, broker, cfg, p, sig, spot, minute, which, key)
        return {**out, "state": "trade taken" if st["tickets"] else "could not price the strike",
                "tickets": st["tickets"]}

    # ── entries and exits ──
    def _buy(self, db, user_id: int, broker, cfg: dict, p: ST.Params, sig: dict,
             spot: float, minute: int, which: str, key: str) -> None:
        st = _state[user_id]
        strike = ST.strike_for(spot, sig["side"], p)
        row = _pick(broker, p.index, sig["side"], strike)
        if row is None:
            _log(st, f"{ST.hhmm(minute)} {sig['side']} {strike:g} is not listed")
            return
        q = _quote_one(broker, row)
        price = q.get("ask") or q.get("ltp")
        if not price or float(price) < p.min_premium:
            _log(st, f"{ST.hhmm(minute)} {sig['side']} {strike:g} has no usable price")
            return
        entry = round(float(price) + p.slippage_ticks * ST.TICK, 2)
        lot = int(row.get("lot_size") or 0) or p.lot
        ex = ST.exits(entry, p)
        t = {"side": sig["side"], "strike": strike, "symbol": row["symbol"], "exchange": row["exchange"],
             "entry": entry, "lot": lot, "lots": p.lots, "qty": p.lots * lot,
             "cost": round(entry * p.lots * lot, 2), "target": ex["target"], "stop": ex["stop"],
             "range": which, "level": sig["level"], "why": sig["why"],
             "taken_at": ST.hhmm(minute), "mode": cfg["mode"], "closed": False}
        if cfg["mode"] == "live":
            placed = self._order(broker, user_id, t, "BUY")
            if not placed.get("ok"):
                _log(st, f"{ST.hhmm(minute)} live buy refused: {placed.get('error')}")
                return
            t["order_id"] = placed.get("order_id")
        st["tickets"].append(t)
        st.setdefault("used", []).append(key)
        _record(db, user_id, t, cfg)
        _log(st, f"{ST.hhmm(minute)} bought {t['side']} {strike:g} × {p.lots} lot(s) at {entry} "
                 f"({sig['why']}) [{cfg['mode']}]")
        logger.info("range lab: %s %s %s at %s (user %s)", cfg["mode"], t["side"], strike, entry, user_id)

    def _close(self, db, user_id: int, broker, cfg: dict, t: dict, price: float, why: str) -> None:
        st = _state[user_id]
        if cfg["mode"] == "live" and t.get("order_id"):
            sold = self._order(broker, user_id, t, "SELL")
            if not sold.get("ok"):
                t["exit_error"] = str(sold.get("error"))[:200]
                t["needs_attention"] = True
                _log(st, f"SELL REFUSED for {t['side']} {t['strike']:g} — STILL OPEN: {t['exit_error']}")
                logger.error("range lab: sell refused, position still open: %s", t["exit_error"])
                _record(db, user_id, t, cfg)
                return
            t.pop("exit_error", None)
            t["needs_attention"] = False
        t.update(closed=True, exit=round(float(price), 2), exit_reason=why,
                 pnl=round((float(price) - t["entry"]) * t["qty"], 2),
                 closed_at=datetime.now().strftime("%H:%M"))
        _record(db, user_id, t, cfg)
        _log(st, f"{t['closed_at']} closed {t['side']} {t['strike']:g} at {t['exit']} "
                 f"({why}) · {t['pnl']:+,.0f}")

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
                tradingsymbol=t["symbol"],
                exchange=Exchange.BFO if t["exchange"] == "BFO" else Exchange.NFO,
                transaction_type=OrderSide.BUY if side == "BUY" else OrderSide.SELL,
                quantity=int(t["qty"]), order_type=OrderType.MARKET, product=ProductType.MIS)
            res = broker.place_order(req)
            oid = getattr(res, "order_id", None) or (res or {}).get("order_id") if res else None
            return {"ok": bool(oid), "order_id": oid, "error": None if oid else "no order id returned"}
        except Exception as exc:
            logger.error("range lab: order failed: %s", exc)
            return {"ok": False, "error": str(exc)[:200]}


ENGINE = Desk()


def dashboard(db, user_id: int, broker=None) -> dict:
    cfg = load_config(db, user_id)
    p = params_of(cfg)
    now = datetime.now()
    minute = now.hour * 60 + now.minute
    st = _state.get(user_id, {})
    tickets = st.get("tickets", [])
    closed = [t for t in tickets if t.get("closed")]
    lv = st.get("levels") or {}
    return {
        "status": "ok", "running": bool(cfg.get("running")), "mode": cfg.get("mode", "paper"),
        "params": p.as_dict(), "rules": ST.describe(p), "connected": broker is not None,
        "now": now.strftime("%H:%M:%S"), "in_force": ST.range_in_force(minute, p),
        "levels": lv, "bars": len(st.get("bars", {})),
        "window": {"first_entry": ST.hhmm(p.first_entry), "switch_at": ST.hhmm(p.switch_at),
                   "last_entry": ST.hhmm(p.last_entry), "squareoff": ST.hhmm(p.squareoff)},
        "tickets": tickets, "log": st.get("log", [])[-40:],
        "totals": {"trades": len(tickets),
                   "spent": round(sum(t.get("cost", 0) for t in tickets), 2),
                   "realised": round(sum(t.get("pnl", 0) for t in closed), 2),
                   "unrealised": round(sum(t.get("unrealised", 0) for t in tickets
                                           if not t.get("closed")), 2)},
    }
