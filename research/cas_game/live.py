"""
CAS Game Play — the live desk.

Start it whenever you like, even in the morning: outside the window it only watches and shows the
₹1 contracts it would buy, so you can see the chain tighten as the auction approaches. Inside the
window it takes the tickets, then follows them to the target or the square-off.

Two modes, and the difference is the only thing that matters here:
  PAPER  prices come from the live book and the tickets are recorded — nothing is sent anywhere.
  LIVE   the same decisions go to the broker as real orders. This is off unless you turn it on,
         and it still refuses to act when the app is in paper mode or the risk fence is up.

The rule itself lives in ``strategy.py`` — this file only sources prices, keeps state and, in live
mode, hands an order to the broker the desk already uses.
"""
from __future__ import annotations

import threading
import time as _time
from datetime import date, datetime
from typing import Optional

import pandas as pd

from core.logger import get_logger
from core.models import StrategyConfig
from research.cas_game import strategy as ST

logger = get_logger("research.cas_game.live")

CONFIG_NAME = "cas_game"
CHECK_EVERY_S = 8.0
QUOTE_TTL_S = 5.0
SCAN_STRIKES = 25            # strikes either side of the spot — a ₹1 option can sit a long way out

DEFAULTS = {
    "running": False,          # the Start button
    "mode": "paper",           # paper | live
    "params": ST.P.as_dict(),
    "started_at": None,
    "day": None,               # the session the current state belongs to
}

_last_check: dict[int, float] = {}
_quotes: dict[str, tuple] = {}
_lock = threading.Lock()
_state: dict[int, dict] = {}   # per user: today's tickets and the last scan


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
        elif k in DEFAULTS and v is not None:
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


# ── the chain, live ──────────────────────────────────────────────────
class Chain:
    """Today's option instruments for one index, and their quotes."""

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


def _spot(broker, index: str) -> float:
    key = {"NIFTY": "NSE:NIFTY 50", "BANKNIFTY": "NSE:NIFTY BANK", "FINNIFTY": "NSE:NIFTY FIN SERVICE",
           "MIDCPNIFTY": "NSE:NIFTY MID SELECT", "SENSEX": "BSE:SENSEX"}.get(index.upper(), "NSE:NIFTY 50")
    try:
        return float((broker.get_quote([key]) or {}).get(key, {}).get("last_price") or 0)
    except Exception as exc:
        logger.debug("spot quote failed: %s", exc)
        return 0.0


def scan(broker, p: ST.Params) -> dict:
    """What the chain looks like right now, and what the rule would buy from it."""
    rows = CHAIN.load(broker, p.index)
    if not rows:
        return {"error": "no option instruments found for that index"}
    spot = _spot(broker, p.index)
    if spot <= 0:
        return {"error": "no spot price"}
    step = 50 if p.index.upper() in ("NIFTY", "FINNIFTY") else 100
    near = [r for r in rows if abs(r["strike"] - spot) <= SCAN_STRIKES * step]
    keys = [f"{r['exchange']}:{r['symbol']}" for r in near]
    quotes = {}
    for i in range(0, len(keys), 200):                     # the broker takes a few hundred at a time
        try:
            quotes.update(broker.get_quote(keys[i:i + 200]) or {})
        except Exception as exc:
            logger.warning("cas game: quote batch failed: %s", exc)
    chain = []
    for r in near:
        q = quotes.get(f"{r['exchange']}:{r['symbol']}") or {}
        depth = (q.get("depth") or {})
        ask = next((float(x["price"]) for x in depth.get("sell") or [] if float(x.get("price") or 0) > 0), None)
        bid = next((float(x["price"]) for x in depth.get("buy") or [] if float(x.get("price") or 0) > 0), None)
        ltp = float(q.get("last_price") or 0) or None
        chain.append({**r, "price": ask or ltp, "bid": bid, "ask": ask, "ltp": ltp,
                      "volume": float(q.get("volume") or q.get("volume_traded") or 0)})
    decision = ST.choose(chain, spot, p)
    # the full chain goes back too: a ticket that works leaves the ₹1 band immediately, and it
    # still has to be priced and sold
    return {"spot": spot, "expiry": str(CHAIN.expiry), "scanned": len(chain), "chain": chain,
            "picks": ST.size(decision["picks"], p), "considered": decision["considered"],
            "why": decision["why"], "at": datetime.now().strftime("%H:%M:%S")}


# ── the desk ─────────────────────────────────────────────────────────
class Desk:
    """Watches, buys inside the window, and closes out. Paper unless told otherwise."""

    def check(self, db, user_id: int, broker) -> dict:
        now = datetime.now()
        with _lock:
            if _time.time() - _last_check.get(user_id, 0) < CHECK_EVERY_S:
                return {"skipped": "too soon"}
            _last_check[user_id] = _time.time()

        cfg = load_config(db, user_id)
        if not cfg.get("running"):
            return {"skipped": "not started"}
        p = params_of(cfg)
        today = str(now.date())
        st = _state.setdefault(user_id, {"day": today, "tickets": [], "log": []})
        if st["day"] != today:                                   # a new session wipes yesterday
            st.update(day=today, tickets=[], log=[])

        minute = now.hour * 60 + now.minute
        out = {"minute": ST.hhmm(minute), "mode": cfg["mode"], "tickets": st["tickets"]}

        scanned = scan(broker, p)
        if "error" in scanned:
            return {**out, "error": scanned["error"]}
        st["last_scan"] = scanned
        out["scan"] = scanned

        # manage anything already open, from the whole chain — a winning ticket is no longer a
        # ₹1 option, so looking for it among the candidates would lose it exactly when it matters
        book = {(c["option_type"], float(c["strike"])): c for c in scanned.get("chain", [])}
        for t in st["tickets"]:
            if t.get("closed"):
                continue
            live = book.get((t["option_type"], float(t["strike"]))) or {}
            price = live.get("bid") or live.get("ltp") or t.get("ltp")
            if price is not None:
                t["ltp"] = price
                t["unrealised"] = round((float(price) - t["entry"]) * t["qty"], 2)
            if price is None:
                if minute >= p.squareoff:            # no quote at the close: book it at the entry
                    self._close(db, user_id, broker, cfg, t, t["entry"], "SQUAREOFF-NO-QUOTE")
                continue
            why = ST.exit_reason(float(price), t, minute, p)
            if why:
                self._close(db, user_id, broker, cfg, t, float(price), why)

        if minute < p.entry_from:
            return {**out, "state": f"watching — the window opens at {ST.hhmm(p.entry_from)}"}
        if st["tickets"]:
            open_n = sum(1 for t in st["tickets"] if not t.get("closed"))
            return {**out, "state": (f"holding {open_n} ticket(s)" if open_n
                                     else "done for the day — tickets closed")}
        if minute > p.entry_to:
            return {**out, "state": f"window closed at {ST.hhmm(p.entry_to)}, nothing taken"}

        for pick in scanned["picks"]:
            if not pick.get("lots"):
                st["log"].append(f"{ST.hhmm(minute)} skipped {pick['option_type']} {pick['strike']:g}: {pick.get('skipped')}")
                continue
            self._buy(db, user_id, broker, cfg, pick, minute)
        return {**out, "state": "tickets taken" if st["tickets"] else "nothing priced in the band yet"}

    # ── entries and exits ──
    def _buy(self, db, user_id: int, broker, cfg: dict, pick: dict, minute: int) -> None:
        st = _state[user_id]
        ticket = {"option_type": pick["option_type"], "strike": pick["strike"], "symbol": pick.get("symbol"),
                  "exchange": pick.get("exchange"), "entry": pick["entry"], "qty": pick["qty"],
                  "lots": pick["lots"], "cost": pick["cost"], "target": pick["target"],
                  "stop": pick.get("stop"), "taken_at": ST.hhmm(minute), "mode": cfg["mode"],
                  "closed": False}
        if cfg["mode"] == "live":
            placed = self._order(broker, user_id, ticket, "BUY")
            if not placed.get("ok"):
                st["log"].append(f"{ST.hhmm(minute)} live buy refused: {placed.get('error')}")
                return
            ticket["order_id"] = placed.get("order_id")
        st["tickets"].append(ticket)
        st["log"].append(f"{ST.hhmm(minute)} bought {ticket['option_type']} {ticket['strike']:g} "
                         f"× {ticket['lots']} lots at {ticket['entry']} ({cfg['mode']})")
        logger.info("cas game: %s ticket %s %s at %s (user %s)", cfg["mode"], ticket["option_type"],
                    ticket["strike"], ticket["entry"], user_id)

    def _close(self, db, user_id: int, broker, cfg: dict, ticket: dict, price: float, why: str) -> None:
        st = _state[user_id]
        if cfg["mode"] == "live" and ticket.get("order_id"):
            self._order(broker, user_id, ticket, "SELL")
        ticket.update(closed=True, exit=round(price, 2), exit_reason=why,
                      pnl=round((price - ticket["entry"]) * ticket["qty"], 2),
                      closed_at=datetime.now().strftime("%H:%M"))
        st["log"].append(f"{ticket['closed_at']} closed {ticket['option_type']} {ticket['strike']:g} "
                         f"at {ticket['exit']} ({why}) · {ticket['pnl']:+,.0f}")

    def _order(self, broker, user_id: int, ticket: dict, side: str) -> dict:
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
                tradingsymbol=ticket["symbol"], exchange=Exchange(ticket.get("exchange", "NFO")),
                side=OrderSide(side), quantity=int(ticket["qty"]), order_type=OrderType.MARKET,
                product=ProductType.NRML, tag="CASGAME")
            res = broker.place_order(req)
            return {"ok": True, "order_id": getattr(res, "order_id", None)}
        except Exception as exc:
            logger.error("cas game: order failed: %s", exc)
            return {"ok": False, "error": str(exc)[:200]}


ENGINE = Desk()


def dashboard(db, user_id: int, broker=None) -> dict:
    """What the page shows: settings, the live scan, the tickets and the running tally."""
    cfg = load_config(db, user_id)
    p = params_of(cfg)
    now = datetime.now()
    minute = now.hour * 60 + now.minute
    st = _state.get(user_id, {})
    tickets = st.get("tickets", [])
    closed = [t for t in tickets if t.get("closed")]
    scanned = st.get("last_scan")
    if broker is not None and cfg.get("running") and not scanned:
        scanned = scan(broker, p)
    return {
        "status": "ok", "running": bool(cfg.get("running")), "mode": cfg.get("mode", "paper"),
        "params": p.as_dict(), "rules": ST.describe(p), "connected": broker is not None,
        "now": now.strftime("%H:%M:%S"),
        "window": {"from": ST.hhmm(p.entry_from), "to": ST.hhmm(p.entry_to),
                   "squareoff": ST.hhmm(p.squareoff),
                   "state": ("before the window" if minute < p.entry_from
                             else "in the window" if minute <= p.entry_to
                             else "after the window")},
        "scan": scanned, "tickets": tickets, "log": st.get("log", [])[-40:],
        "totals": {"tickets": len(tickets), "spent": round(sum(t.get("cost", 0) for t in tickets), 2),
                   "realised": round(sum(t.get("pnl", 0) for t in closed), 2),
                   "unrealised": round(sum(t.get("unrealised", 0) for t in tickets if not t.get("closed")), 2)},
    }
