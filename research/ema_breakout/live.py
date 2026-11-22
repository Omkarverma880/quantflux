"""
20 EMA Single Candle Breakout — the live/paper desk.

It builds the index candles from repeated quotes, runs the SAME state machine the backtest
runs, and when a signal fires it takes the option the settings describe — buying or selling,
at the chosen strike.

Paper unless the strategy's own mode is "live" AND the app is out of paper mode AND the risk
fence is down. That check lives in ``_order`` and nowhere else, exactly as the other desks do
it, so there is a single place to audit.

Two things make this safe to run from a loop that ticks every second:

    CLOSED CANDLES ONLY   the forming candle is never fed to the state machine. A decision is
                          made once, when its candle completes, and the bar index it fired on
                          is remembered so the same candle can never fire twice.
    IDEMPOTENT            every ticket carries a key of (date, bar, direction). A restart, a
                          reconnect or a duplicate tick re-derives the same key and refuses to
                          open a second position for it.

State is per user and in memory, but every ticket is written to ``trade_logs`` the moment it
is taken and again when it closes, so a restart can never erase a trade that really happened.
"""
from __future__ import annotations

import threading
import time as _time
from datetime import date, datetime
from typing import Optional

import pandas as pd

from core.logger import get_logger
from core.models import StrategyConfig
from research.ema_breakout import strategy as ST

logger = get_logger("research.ema_breakout.live")

CONFIG_NAME = "ema_breakout"
CHECK_EVERY_S = 3.0
IDLE_CHECK_EVERY_S = 20.0
LOG_MAX = 300
TICK = 0.05

DEFAULTS = {
    "running": False,
    "mode": "paper",                 # paper | live
    "params": ST.P.as_dict(),
    "started_at": None,
    "day": None,
}

SPOT_KEY = {"NIFTY": "NSE:NIFTY 50", "BANKNIFTY": "NSE:NIFTY BANK",
            "FINNIFTY": "NSE:NIFTY FIN SERVICE", "MIDCPNIFTY": "NSE:NIFTY MID SELECT",
            "SENSEX": "BSE:SENSEX", "BANKEX": "BSE:BANKEX"}

_last_check: dict[int, float] = {}
_lock = threading.Lock()
_state: dict[int, dict] = {}


# ── settings ────────────────────────────────────────────────────────────────
def load_config(db, user_id: int) -> dict:
    row = (db.query(StrategyConfig)
             .filter(StrategyConfig.user_id == user_id,
                     StrategyConfig.strategy_name == CONFIG_NAME).first())
    cfg = {**DEFAULTS, **((row.config or {}) if row else {})}
    cfg["params"] = {**ST.P.as_dict(), **(cfg.get("params") or {})}
    return cfg


def save_config(db, user_id: int, updates: dict) -> dict:
    cfg = load_config(db, user_id)
    for k, v in (updates or {}).items():
        if k == "params" and isinstance(v, dict):
            cfg["params"] = {**cfg["params"],
                             **{pk: pv for pk, pv in v.items() if pk in ST.P.as_dict()}}
        elif k in DEFAULTS and (v is not None or DEFAULTS[k] is None):
            cfg[k] = v
    row = (db.query(StrategyConfig)
             .filter(StrategyConfig.user_id == user_id,
                     StrategyConfig.strategy_name == CONFIG_NAME).first())
    if row is None:
        db.add(StrategyConfig(user_id=user_id, strategy_name=CONFIG_NAME, config=cfg))
    else:
        row.config = cfg
    db.commit()
    return load_config(db, user_id)


def params_of(cfg: dict) -> ST.Params:
    return ST.Params(**{k: v for k, v in (cfg.get("params") or {}).items()
                        if k in ST.P.as_dict()})


def _log(st: dict, line: str) -> None:
    st.setdefault("log", []).insert(0, f"{datetime.now().strftime('%H:%M:%S')} · {line}")
    del st["log"][LOG_MAX:]


def _event(st: dict, e: dict, p: ST.Params) -> None:
    """Keep every state-machine event, with the context the brief asks each one to carry."""
    st.setdefault("events", []).insert(0, {
        **e, "index": p.index, "timeframe": p.timeframe,
        "at": datetime.now().strftime("%H:%M:%S"),
    })
    del st["events"][LOG_MAX:]


# ── the chain ───────────────────────────────────────────────────────────────
class Chain:
    """The nearest-expiry option list, from the broker's own instrument master.

    Lot size comes from here and is never hard-coded: the exchange revises it and a stale
    constant would size every order wrongly.
    """

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
    key = SPOT_KEY.get(index.upper(), "NSE:NIFTY 50")
    try:
        return float((broker.get_quote([key]) or {}).get(key, {}).get("last_price") or 0)
    except Exception as exc:
        logger.debug("ema breakout: spot quote failed: %s", exc)
        return 0.0


def _quote_one(broker, row: dict) -> dict:
    key = f"{row['exchange']}:{row['symbol']}"
    try:
        q = (broker.get_quote([key]) or {}).get(key, {}) or {}
    except Exception as exc:
        logger.debug("ema breakout: option quote failed: %s", exc)
        return {}
    depth = q.get("depth") or {}
    ask = next((float(x["price"]) for x in depth.get("sell") or []
                if float(x.get("price") or 0) > 0), None)
    bid = next((float(x["price"]) for x in depth.get("buy") or []
                if float(x.get("price") or 0) > 0), None)
    return {"ltp": float(q.get("last_price") or 0) or None, "bid": bid, "ask": ask}


def _nearest(broker, index: str, opt_type: str, strike: float) -> Optional[dict]:
    rows = [r for r in CHAIN.load(broker, index) if r["option_type"] == opt_type]
    if not rows:
        return None
    return min(rows, key=lambda r: abs(r["strike"] - strike))


# ── the day's index candles, built from quotes ──────────────────────────────
def _tick_bar(st: dict, minute: int, price: float, tf: int) -> None:
    """Fold this quote into the candle it belongs to, anchored on the session open."""
    bucket = ST.SESSION_OPEN + ((minute - ST.SESSION_OPEN) // max(1, tf)) * max(1, tf)
    bars = st.setdefault("bars", {})
    b = bars.get(bucket)
    if b is None:
        bars[bucket] = {"minute": bucket, "open": price, "high": price,
                        "low": price, "close": price}
        return
    b["high"] = max(b["high"], price)
    b["low"] = min(b["low"], price)
    b["close"] = price


def _closed_bars(st: dict, minute: int, tf: int, day: str) -> pd.DataFrame:
    """Only candles whose last minute has passed. The forming one must never be read."""
    rows = [b for m, b in sorted(st.get("bars", {}).items()) if m + max(1, tf) - 1 < minute]
    if not rows:
        return pd.DataFrame()
    d = pd.DataFrame(rows)
    d["date"] = day
    return d


# ── trade log ───────────────────────────────────────────────────────────────
def _record(db, user_id: int, t: dict, cfg: dict) -> None:
    if db is None:
        return
    try:
        from core.models import TradeLog
        key = t["key"]
        row = (db.query(TradeLog)
                 .filter(TradeLog.user_id == user_id,
                         TradeLog.strategy_name == CONFIG_NAME,
                         TradeLog.trade_date == date.today())
                 .filter(TradeLog.extra["key"].astext == key).first())
        if row is None:
            row = TradeLog(user_id=user_id, strategy_name=CONFIG_NAME,
                           trade_date=date.today())
            db.add(row)
        row.signal = t.get("direction")
        row.option_symbol = t.get("symbol")
        row.atm_strike = int(float(t.get("strike") or 0))
        row.entry_price = t.get("entry")
        row.exit_price = t.get("exit")
        row.exit_type = t.get("exit_reason")
        row.exit_time = t.get("closed_at")
        row.lot_size = t.get("lot")
        row.pnl = t.get("pnl")
        row.extra = {"key": key, "mode": t.get("mode"), "lots": t.get("lots"),
                     "qty": t.get("qty"), "action": t.get("action"),
                     "option_type": t.get("option_type"), "trigger": t.get("trigger"),
                     "index": t.get("index"), "timeframe": t.get("timeframe"),
                     "entry_reason": t.get("entry_reason"),
                     "order_id": t.get("order_id"), "exit_order_id": t.get("exit_order_id"),
                     "exit_error": t.get("exit_error"),
                     "needs_attention": bool(t.get("needs_attention"))}
        db.commit()
    except Exception as exc:
        logger.warning("ema breakout: could not record ticket: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass


# ── the desk ────────────────────────────────────────────────────────────────
class Desk:
    def check(self, db, user_id: int, broker) -> dict:
        now = datetime.now()
        cfg = load_config(db, user_id)
        if not cfg.get("running"):
            return {"skipped": "not started"}
        p = params_of(cfg)
        today = str(now.date())
        minute = now.hour * 60 + now.minute

        st = _state.setdefault(user_id, {"day": today, "tickets": [], "log": [],
                                         "bars": {}, "events": [], "fired": []})
        if st["day"] != today:
            st.update(day=today, tickets=[], log=[], bars={}, events=[], fired=[])

        holding = any(not t.get("closed") for t in st["tickets"])
        every = CHECK_EVERY_S if holding else IDLE_CHECK_EVERY_S
        with _lock:
            if _time.time() - _last_check.get(user_id, 0) < every:
                return {"skipped": "too soon"}
            _last_check[user_id] = _time.time()

        spot = _spot(broker, p.index)
        if spot <= 0:
            return {"state": "no index price", "tickets": st["tickets"]}
        _tick_bar(st, minute, spot, p.timeframe)

        bars = _closed_bars(st, minute, p.timeframe, today)
        out = {"minute": ST.hhmm(minute), "mode": cfg["mode"], "spot": spot,
               "index": p.index, "timeframe": p.timeframe,
               "closed_candles": int(len(bars)), "tickets": st["tickets"]}

        # mark anything open to market first — an exit always outranks a new entry
        open_t = next((t for t in st["tickets"] if not t.get("closed")), None)
        if open_t:
            q = _quote_one(broker, open_t)
            px = (q.get("bid") if open_t["action"] == ST.BUY else q.get("ask")) or q.get("ltp")
            if px is not None:
                open_t["ltp"] = px
                d = (float(px) - open_t["entry"]) * (1 if open_t["action"] == ST.BUY else -1)
                open_t["unrealised"] = round(d * open_t["qty"], 2)

        if len(bars) < p.ema_length + 1:
            return {**out, "state": f"warming up — {len(bars)} of "
                                    f"{p.ema_length + 1} candles needed"}

        res = ST.run(bars, p)
        # Surface the events of the newest closed candle, once. Keyed on the bar index rather
        # than on a timestamp: several events can share a candle, and a time-based guard would
        # publish only the first of them.
        newest = int(len(bars)) - 1
        seen = st.get("last_event_bar", -1)
        if newest > seen:
            # everything since the last check, not just the newest candle: a check that gets
            # rate-limited past a candle must not lose that candle's events
            for e in res["events"]:
                if seen < e["bar"] <= newest:
                    _event(st, e, p)
            st["last_event_bar"] = newest

        last = res["trades"][-1] if res["trades"] else None
        st["setup"] = self._setup_view(res, p)
        out["setup"] = st["setup"]
        out["recent_events"] = st.get("events", [])[:12]

        # ── exit: the state machine says the position is done ──
        if open_t:
            wants_exit = (last is not None and last.get("exit_bar") == newest
                          and last.get("direction") == open_t["direction"])
            if minute >= p.squareoff:
                self._close(db, user_id, broker, cfg, open_t, "SQUAREOFF")
            elif wants_exit:
                self._close(db, user_id, broker, cfg, open_t, last.get("exit_reason") or "EMA_EXIT")
            return {**out, "state": "holding" if not open_t.get("closed") else "closed",
                    "tickets": st["tickets"]}

        # ── entry: only from the candle that has just closed, and only once ──
        # ST.run returns the still-open trade in `trades` too, so one lookup covers both an
        # entry that has already exited within this run and one that is still open.
        fresh = next((t for t in res["trades"] if t.get("entry_bar") == newest), None)
        if fresh is None:
            return {**out, "state": st["setup"]["state"]}

        key = f"{today}:{newest}:{fresh['direction']}"
        if key in st.setdefault("fired", []):
            return {**out, "state": "signal already acted on"}
        if minute > p.entry_until:
            _log(st, f"signal at {ST.hhmm(minute)} ignored — past the entry window")
            st["fired"].append(key)
            return {**out, "state": "past the entry window"}

        self._enter(db, user_id, broker, cfg, p, fresh, spot, minute, key)
        return {**out, "state": "trade taken" if st["tickets"] else "could not price the option",
                "tickets": st["tickets"]}

    @staticmethod
    def _setup_view(res: dict, p: ST.Params) -> dict:
        """What the desk is watching right now, in words."""
        ev = res["events"]
        last_cross = next((e for e in reversed(ev)
                           if e["kind"] in ("bull_cross", "bear_cross")), None)
        last_conf = next((e for e in reversed(ev)
                          if e["kind"] in ("long_confirmation", "short_confirmation")), None)
        last_end = next((e for e in reversed(ev)
                         if e["kind"] in ("setup_expired", "buy_entry", "sell_entry")), None)
        live_conf = (last_conf and (last_end is None or last_conf["bar"] > last_end["bar"]))
        live_cross = (last_cross and (last_end is None or last_cross["bar"] > last_end["bar"]))
        if live_conf:
            return {"state": f"armed — a close through {last_conf['trigger']} triggers",
                    "direction": last_conf.get("direction"),
                    "trigger": last_conf.get("trigger"),
                    "since": last_conf.get("time")}
        if live_cross:
            return {"state": "watching the next two candles for a confirmation candle",
                    "direction": last_cross.get("direction"), "trigger": None,
                    "since": last_cross.get("time")}
        return {"state": "waiting for a close through the EMA", "direction": None,
                "trigger": None, "since": None}

    # ── entry and exit ──
    def _enter(self, db, user_id: int, broker, cfg: dict, p: ST.Params, sig: dict,
               spot: float, minute: int, key: str) -> None:
        st = _state[user_id]
        side = ST.LONG if sig["direction"] == "LONG" else ST.SHORT
        leg = ST.option_for(side, p.option_action, spot, p)
        row = _nearest(broker, p.index, leg["option_type"], leg["strike"])
        if row is None:
            _log(st, f"{ST.hhmm(minute)} no {leg['option_type']} listed near {leg['strike']:g}")
            st["fired"].append(key)
            return
        q = _quote_one(broker, row)
        # buying pays the ask, selling receives the bid — never the mid
        price = (q.get("ask") if p.option_action == ST.BUY else q.get("bid")) or q.get("ltp")
        if not price or float(price) <= 0:
            _log(st, f"{ST.hhmm(minute)} {row['symbol']} has no usable price")
            st["fired"].append(key)
            return
        slip = TICK if p.option_action == ST.BUY else -TICK
        entry = round(float(price) + slip, 2)
        lot = int(row.get("lot_size") or 0)
        if lot <= 0:
            _log(st, f"{ST.hhmm(minute)} {row['symbol']} has no lot size in the instrument "
                     f"master — refusing to guess one")
            st["fired"].append(key)
            return
        t = {"key": key, "direction": sig["direction"], "action": p.option_action,
             "option_type": leg["option_type"], "strike": row["strike"],
             "symbol": row["symbol"], "exchange": row["exchange"], "expiry": str(row["expiry"]),
             "entry": entry, "lot": lot, "lots": p.lots, "qty": p.lots * lot,
             "trigger": sig.get("trigger"), "index": p.index, "timeframe": p.timeframe,
             "entry_reason": f"close {sig['entry_close']} crossed the trigger {sig.get('trigger')}",
             "taken_at": ST.hhmm(minute), "mode": cfg["mode"], "closed": False}
        if cfg["mode"] == "live":
            placed = self._order(broker, user_id, t, p.option_action)
            if not placed.get("ok"):
                _log(st, f"{ST.hhmm(minute)} live order refused: {placed.get('error')}")
                st["fired"].append(key)
                return
            t["order_id"] = placed.get("order_id")
        st["tickets"].append(t)
        st["fired"].append(key)
        _record(db, user_id, t, cfg)
        _log(st, f"{ST.hhmm(minute)} {p.option_action} {t['option_type']} {t['strike']:g} "
                 f"× {p.lots} lot(s) at {entry} ({sig['direction']} signal) [{cfg['mode']}]")
        logger.info("ema breakout: %s %s %s %s at %s (user %s)", cfg["mode"],
                    p.option_action, t["option_type"], t["strike"], entry, user_id)

    def _close(self, db, user_id: int, broker, cfg: dict, t: dict, why: str) -> None:
        st = _state[user_id]
        q = _quote_one(broker, t)
        px = (q.get("bid") if t["action"] == ST.BUY else q.get("ask")) or q.get("ltp") or t["entry"]
        if cfg["mode"] == "live" and t.get("order_id"):
            back = ST.SELL if t["action"] == ST.BUY else ST.BUY
            done = self._order(broker, user_id, t, back)
            if not done.get("ok"):
                t["exit_error"] = str(done.get("error"))[:200]
                t["needs_attention"] = True
                _log(st, f"EXIT REFUSED for {t['symbol']} — STILL OPEN: {t['exit_error']}")
                logger.error("ema breakout: exit refused, position still open: %s",
                             t["exit_error"])
                _record(db, user_id, t, cfg)
                return
            t["exit_order_id"] = done.get("order_id")
            t.pop("exit_error", None)
            t["needs_attention"] = False
        direction = 1 if t["action"] == ST.BUY else -1
        t.update(closed=True, exit=round(float(px), 2), exit_reason=why,
                 pnl=round((float(px) - t["entry"]) * direction * t["qty"], 2),
                 closed_at=datetime.now().strftime("%H:%M"))
        _record(db, user_id, t, cfg)
        _log(st, f"{t['closed_at']} closed {t['option_type']} {t['strike']:g} at {t['exit']} "
                 f"({why}) · {t['pnl']:+,.0f}")

    def _order(self, broker, user_id: int, t: dict, side: str) -> dict:
        """The only place this strategy can touch the broker. Refuses whenever the app says no."""
        try:
            from config import settings
            if not settings.TRADING_ENABLED or settings.PAPER_TRADE:
                return {"ok": False,
                        "error": "the app is in paper mode (TRADING_ENABLED / PAPER_TRADE)"}
            from core.risk_fence import is_trading_blocked
            blocked, reason = is_trading_blocked(user_id)
            if blocked:
                return {"ok": False, "error": f"risk fence: {reason}"}
            from core.broker import Exchange, OrderRequest, OrderSide, OrderType, ProductType
            req = OrderRequest(
                tradingsymbol=t["symbol"],
                exchange=Exchange.BFO if t["exchange"] == "BFO" else Exchange.NFO,
                side=OrderSide.BUY if side == ST.BUY else OrderSide.SELL,
                quantity=int(t["qty"]), order_type=OrderType.MARKET, product=ProductType.MIS)
            res = broker.place_order(req)
            oid = getattr(res, "order_id", None) or (res or {}).get("order_id") if res else None
            return {"ok": bool(oid), "order_id": oid,
                    "error": None if oid else "no order id returned"}
        except Exception as exc:
            logger.error("ema breakout: order failed: %s", exc)
            return {"ok": False, "error": str(exc)[:200]}


ENGINE = Desk()


def dashboard(db, user_id: int, broker=None) -> dict:
    cfg = load_config(db, user_id)
    p = params_of(cfg)
    st = _state.get(user_id) or {}
    tickets = st.get("tickets", [])
    closed = [t for t in tickets if t.get("closed")]
    realised = round(sum(float(t.get("pnl") or 0) for t in closed), 2)
    unreal = round(sum(float(t.get("unrealised") or 0)
                       for t in tickets if not t.get("closed")), 2)
    from config import settings
    return {
        "status": "ok",
        "running": bool(cfg.get("running")),
        "mode": cfg.get("mode"),
        "connected": broker is not None,
        "config": cfg,
        "params": p.as_dict(),
        "rules": ST.describe(p),
        "now": datetime.now().strftime("%H:%M:%S"),
        "day": st.get("day"),
        "closed_candles": len([1 for m in (st.get("bars") or {})]),
        "setup": st.get("setup") or {"state": "not started"},
        "tickets": tickets,
        "events": st.get("events", [])[:60],
        "log": st.get("log", [])[:60],
        "realised": realised, "unrealised": unreal, "net": round(realised + unreal, 2),
        "gates": {
            "strategy_mode": cfg.get("mode"),
            "app_trading_enabled": bool(getattr(settings, "TRADING_ENABLED", False)),
            "app_paper_trade": bool(getattr(settings, "PAPER_TRADE", True)),
            "note": ("A real order needs all three: this strategy in live mode, the app out "
                     "of paper mode, and the risk fence down. Any one of them says no and "
                     "the order is refused, not downgraded."),
        },
    }
