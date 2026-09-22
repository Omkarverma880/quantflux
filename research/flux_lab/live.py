"""
Flux Lab — live PAPER trading of the failed-breakout iron fly.

**This module cannot place an order.** It imports no order path: no ``place_order``, no
``OrderRequest``, no execution client. It reads NIFTY candles and option quotes from the broker
and writes rows to ``flux_lab_trades`` with ``mode='PAPER'``. That is the whole safety argument.

The decision is ``strategy.scan`` — the same function the backtest calls — run on today's
COMPLETED 1-minute NIFTY closes (the forming candle is dropped). Fills are priced honestly from the
live order book: sold legs at the best bid, bought legs at the best ask, and exits the other way.
Because the backtest fills on the next minute, an entry is only taken within 3 minutes of the
signal; a signal found later (engine off, Zerodha not connected) is logged as missed, not chased.
"""
from __future__ import annotations

import threading
import time as _time
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

from core.logger import get_logger
from core.models import FluxLabSignal, FluxLabTrade, StrategyConfig
from research.flux_lab import strategy as ST
from research.options_lab.execution import CostModel

logger = get_logger("research.flux_lab.live")

CONFIG_NAME = "flux_lab_ironfly_paper"
CHECK_EVERY_S = 20.0
BARS_TTL_S = 20.0
ENTRY_GRACE_MIN = 3
SESSION_OPEN = 9 * 60 + 15
SESSION_END = 15 * 60 + 30
DEFAULT_PAPER = {"enabled": False, "lots": 1}
COST = CostModel(slippage_pts=0.0)

_last_check: dict[int, float] = {}
_bars_cache: dict[str, tuple[float, pd.DataFrame]] = {}
_logged: set = set()
_lock = threading.Lock()


# ── configuration ────────────────────────────────────────────────────
def load_config(db, user_id: int) -> dict:
    row = (db.query(StrategyConfig)
             .filter(StrategyConfig.user_id == user_id, StrategyConfig.strategy_name == CONFIG_NAME).first())
    return {**DEFAULT_PAPER, **((row.config or {}) if row else {})}


def save_config(db, user_id: int, updates: dict) -> dict:
    clean = {k: v for k, v in (updates or {}).items() if k in DEFAULT_PAPER and v is not None}
    if "lots" in clean:
        clean["lots"] = max(1, min(50, int(clean["lots"])))
    cfg = {**load_config(db, user_id), **clean}
    row = (db.query(StrategyConfig)
             .filter(StrategyConfig.user_id == user_id, StrategyConfig.strategy_name == CONFIG_NAME).first())
    if row is None:
        db.add(StrategyConfig(user_id=user_id, strategy_name=CONFIG_NAME, config=cfg))
    else:
        row.config = cfg
    db.commit()
    return load_config(db, user_id)


# ── market data (read-only) ──────────────────────────────────────────
def _now_min(now: Optional[datetime] = None) -> int:
    now = now or datetime.now()
    return now.hour * 60 + now.minute


def completed_closes(broker, now: Optional[datetime] = None) -> tuple[dict, Optional[int]]:
    """Today's NIFTY 1-minute closes by minute, forming candle excluded, and the last complete minute."""
    now = now or datetime.now()
    key = f"{id(broker)}:{now.date()}"
    hit = _bars_cache.get(key)
    if hit and _time.time() - hit[0] < BARS_TTL_S:
        df = hit[1]
    else:
        token = None
        for inst in broker.get_instruments("NSE") or []:
            if inst.get("tradingsymbol") == "NIFTY 50":
                token = int(inst["instrument_token"])
                break
        if not token:
            return {}, None
        start = datetime.combine(now.date(), datetime.min.time()) + timedelta(hours=9, minutes=15)
        rows = broker.get_historical_data(token, start, now, "minute") or []
        df = pd.DataFrame(rows)
        if not df.empty:
            ts = pd.to_datetime(df["date"], errors="coerce", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
            df = df.assign(ts=ts)[["ts", "close"]].sort_values("ts")
        _bars_cache.clear()
        _bars_cache[key] = (_time.time(), df)
    if df.empty:
        return {}, None
    df = df[df["ts"].dt.date == now.date()]
    done = df[df["ts"] + pd.Timedelta(minutes=1) <= pd.Timestamp(now) - pd.Timedelta(seconds=2)]
    closes = {int(t.hour * 60 + t.minute): float(c) for t, c in zip(done["ts"], done["close"])}
    return closes, (max(closes) if closes else None)


class _Chain:
    """Today's NIFTY option instruments, from the instrument dump already used across the app."""

    def __init__(self):
        self.day = None
        self.rows: list[dict] = []

    def get(self, broker) -> list[dict]:
        today = date.today()
        if self.day == today and self.rows:
            return self.rows
        rows = []
        for i in broker.get_instruments("NFO") or []:
            if i.get("name") != "NIFTY" or i.get("instrument_type") not in ("CE", "PE"):
                continue
            exp = i.get("expiry")
            exp = exp if isinstance(exp, date) else (pd.Timestamp(str(exp)).date() if exp else None)
            if exp and exp >= today:
                rows.append({"symbol": i["tradingsymbol"], "type": i["instrument_type"],
                             "strike": float(i.get("strike") or 0), "expiry": exp,
                             "lot_size": int(i.get("lot_size") or 0)})
        self.day, self.rows = today, rows
        return rows

    def resolve(self, broker, legs: list[dict]) -> tuple[Optional[date], list[dict]]:
        rows = self.get(broker)
        if not rows:
            return None, []
        expiry = min(r["expiry"] for r in rows)                     # nearest weekly, as backtested
        out = []
        for l in legs:
            m = next((r for r in rows if r["expiry"] == expiry and r["type"] == l["type"]
                      and abs(r["strike"] - l["strike"]) < 0.01), None)
            if m is None:
                return expiry, []
            out.append({**l, "symbol": m["symbol"], "lot_size": m["lot_size"]})
        return expiry, out


def _book(broker, symbols: list[str]) -> dict:
    keys = [f"NFO:{s}" for s in symbols]
    q = broker.get_quote(keys) or {}
    out = {}
    for s, k in zip(symbols, keys):
        d = q.get(k) or {}
        depth = d.get("depth") or {}
        bid = next((float(x["price"]) for x in depth.get("buy") or [] if float(x.get("price") or 0) > 0), None)
        ask = next((float(x["price"]) for x in depth.get("sell") or [] if float(x.get("price") or 0) > 0), None)
        ltp = float(d.get("last_price") or 0) or None
        out[s] = {"bid": bid or ltp, "ask": ask or ltp, "ltp": ltp}
    return out


def _mark(legs: list[dict], book: dict) -> Optional[float]:
    """Per-unit cost to close now: buy back sold legs at the ask, sell bought legs at the bid."""
    total = 0.0
    for l in legs:
        b = book.get(l["symbol"]) or {}
        px = b.get("ask") if l["q"] < 0 else b.get("bid")
        if not px:
            return None
        total += -l["q"] * px
    return total


# ── the engine ───────────────────────────────────────────────────────
class PaperEngine:
    """Signal → paper iron fly → paper exit. Never an order."""

    def __init__(self):
        self.chain = _Chain()

    def check(self, db, user_id: int, broker) -> dict:
        now = datetime.now()
        if now.weekday() >= 5:
            return {"skipped": "weekend"}
        minute = _now_min(now)
        if minute < SESSION_OPEN or minute > SESSION_END:
            return {"skipped": "outside market hours"}
        with _lock:
            if _time.time() - _last_check.get(user_id, 0) < CHECK_EVERY_S:
                return {"skipped": "too soon"}
            _last_check[user_id] = _time.time()

        out = {"managed": self._manage(db, user_id, broker, minute)}
        cfg = load_config(db, user_id)
        if not cfg.get("enabled"):
            return {**out, "skipped": "paper trading is off"}
        if self._open(db, user_id) or self._traded_today(db, user_id):
            return {**out, "skipped": "one trade a day — already taken"}

        closes, upto = completed_closes(broker, now)
        if not closes:
            return {**out, "skipped": "no NIFTY candles yet today"}
        res = ST.scan(closes, upto=upto)
        out["state"] = res["state"]
        sig = res["signal"]
        if not sig:
            return out
        if minute > sig["minute"] + ENTRY_GRACE_MIN or minute >= ST.RULE.squareoff - 5:
            self._log(db, user_id, sig, acted=False,
                      why=f"signal at {sig['time']} found at {ST.hhmm(minute)} — too late to fill like the backtest")
            return {**out, "skipped": "signal missed"}
        return {**out, "opened": self._enter(db, user_id, broker, cfg, sig, res["events"], now)}

    # ── entry ──
    def _enter(self, db, user_id: int, broker, cfg: dict, sig: dict, events: list, now: datetime) -> Optional[dict]:
        expiry, legs = self.chain.resolve(broker, ST.legs(sig["spot"]))
        if not legs:
            self._log(db, user_id, sig, acted=False, why="a leg's contract was not found in today's instruments")
            return None
        book = _book(broker, [l["symbol"] for l in legs])
        fills = []
        for l in legs:
            b = book.get(l["symbol"]) or {}
            px = b.get("bid") if l["q"] < 0 else b.get("ask")      # sold at the bid, bought at the ask
            if not px:
                self._log(db, user_id, sig, acted=False, why=f"no live price for {l['symbol']}")
                return None
            fills.append({**l, "entry": round(px, 2)})
        credit = -sum(l["q"] * l["entry"] for l in fills)
        if credit <= 5:
            self._log(db, user_id, sig, acted=False, why=f"credit only {credit:.2f} pts")
            return None
        lots = int(cfg.get("lots") or 1)
        lot_size = fills[0].get("lot_size") or 65
        t = {"date": str(date.today()), "signal_time": sig["time"], "entry_time": now.strftime("%H:%M"),
             "atm": fills[0]["strike"], "expiry": str(expiry), "dte": (expiry - date.today()).days,
             "lots": lots, "qty": lots * lot_size, "credit": round(credit, 2),
             "spot_entry": round(sig["spot"], 2), "legs": fills, "signal": sig, "events": events}
        from research.flux_lab.service import trade_row
        row = trade_row(t, user_id, "PAPER", status="OPEN")
        row.ladder = {"last_debit": round(credit, 2), "marked_at": now.strftime("%H:%M")}
        db.add(row)
        db.commit()
        self._log(db, user_id, sig, acted=True, why=None, trade_id=row.id)
        logger.info("flux paper: iron fly opened at %s credit %.2f (user %s)", t["atm"], credit, user_id)
        return {"atm": t["atm"], "credit": round(credit, 2), "legs": [l["symbol"] for l in fills]}

    # ── management ──
    def _manage(self, db, user_id: int, broker, minute: int) -> list[dict]:
        done = []
        for t in self._open(db, user_id):
            legs = (t.indicators or {}).get("legs") or []
            credit = float(t.option_entry or 0)
            if t.trade_date < date.today():            # left open overnight (app was down at 15:15)
                last = float((t.ladder or {}).get("last_debit") or credit)
                self._close(db, t, legs, None, last, "EOD-LATE", "15:15")
                done.append({"id": t.id, "reason": "EOD-LATE"})
                continue
            book = _book(broker, [l["symbol"] for l in legs])
            debit = _mark(legs, book)
            if debit is None:
                continue
            t.ladder = {"last_debit": round(debit, 2), "marked_at": ST.hhmm(minute)}
            reason = ST.exit_reason(credit - debit, credit, minute)
            if reason:
                self._close(db, t, legs, book, debit, reason, ST.hhmm(minute))
                done.append({"id": t.id, "reason": reason, "pnl": float(t.pnl)})
            else:
                db.commit()
        return done

    def _close(self, db, t: FluxLabTrade, legs: list[dict], book: Optional[dict], debit: float,
               reason: str, at: str) -> None:
        qty = int(t.qty or 0)
        new_legs, gross, charges = [], 0.0, 0.0
        for l in legs:
            if book:
                b = book.get(l["symbol"]) or {}
                x = float(b.get("ask") if l["q"] < 0 else b.get("bid"))
            else:                                          # no live book: split the last mark evenly
                x = float(l["entry"])
            e = float(l["entry"])
            gross += -l["q"] * (e - x) * qty
            charges += COST.round_trip(x, e, qty) if l["q"] < 0 else COST.round_trip(e, x, qty)
            new_legs.append({**l, "exit": round(x, 2)})
        if not book:                                       # stale close: P&L from the last recorded mark
            gross = (float(t.option_entry or 0) - debit) * qty
        t.option_exit = round(debit, 2)
        t.gross_pts = round(float(t.option_entry or 0) - debit, 2)
        t.charges = round(charges, 2)
        t.pnl = round(gross - charges, 2)
        t.exit_reason = reason
        t.exit_time = at
        try:
            h, m = [int(x) for x in (t.entry_time or "0:0").split(":")[:2]]
            eh, em = [int(x) for x in at.split(":")[:2]]
            t.held_min = max(0, (eh * 60 + em) - (h * 60 + m))
        except Exception:
            pass
        t.indicators = {**(t.indicators or {}), "legs": new_legs}
        t.status = "CLOSED"
        db.commit()
        logger.info("flux paper: iron fly closed (%s) pnl %.2f", reason, float(t.pnl))

    # ── bookkeeping ──
    def _open(self, db, user_id: int) -> list[FluxLabTrade]:
        return (db.query(FluxLabTrade)
                  .filter(FluxLabTrade.user_id == user_id, FluxLabTrade.mode == "PAPER",
                          FluxLabTrade.side == "IF", FluxLabTrade.status == "OPEN").all())

    def _traded_today(self, db, user_id: int) -> bool:
        return (db.query(FluxLabTrade)
                  .filter(FluxLabTrade.user_id == user_id, FluxLabTrade.mode == "PAPER",
                          FluxLabTrade.side == "IF", FluxLabTrade.trade_date == date.today()).count() > 0)

    def _log(self, db, user_id: int, sig: dict, acted: bool, why: Optional[str], trade_id: Optional[int] = None):
        key = (user_id, str(date.today()), sig["time"], acted, why)
        if key in _logged:
            return
        _logged.add(key)
        try:
            db.add(FluxLabSignal(
                user_id=user_id, mode="PAPER", trade_date=date.today(), bar_time=sig["time"],
                strategy_name=ST.STRATEGY_NAME, side="IF", spot=round(sig["spot"], 2), fired=True,
                acted=acted, skip_reason=why, reasons=[sig["direction"]], indicators=sig, regime={},
                trade_id=trade_id))
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.debug("signal log failed: %s", exc)


ENGINE = PaperEngine()


# ── the desk ─────────────────────────────────────────────────────────
def dashboard(db, user_id: int, broker=None) -> dict:
    from research.flux_lab.service import latest_run, trade_dict
    cfg = load_config(db, user_id)
    now = datetime.now()
    minute = _now_min(now)
    market_open = now.weekday() < 5 and SESSION_OPEN <= minute <= SESSION_END
    rows = (db.query(FluxLabTrade)
              .filter(FluxLabTrade.user_id == user_id, FluxLabTrade.mode == "PAPER", FluxLabTrade.side == "IF")
              .order_by(FluxLabTrade.id.desc()).limit(500).all())
    trades = [trade_dict(t) for t in rows]

    today = {"state": None, "or_high": None, "or_low": None, "events": [], "spot": None, "last_minute": None}
    open_pos = [t for t in trades if t["status"] == "OPEN"]
    if broker is not None and now.weekday() < 5 and minute >= SESSION_OPEN:
        try:
            closes, upto = completed_closes(broker, now)
            if closes:
                res = ST.scan(closes, upto=upto)
                today.update(state=res["state"], events=res["events"], spot=closes[upto],
                             last_minute=ST.hhmm(upto), signal=res["signal"])
                if res["or"]:
                    today.update(or_high=res["or"][0], or_low=res["or"][1])
            else:
                today["state"] = "no NIFTY candles yet today"
        except Exception as exc:
            today["state"] = f"could not read today's candles: {exc}"[:200]
        for t in open_pos:
            try:
                book = _book(broker, [l["symbol"] for l in t["legs"]])
                debit = _mark(t["legs"], book)
                for l in t["legs"]:
                    l.update(book.get(l["symbol"]) or {})
                if debit is not None and t["credit"]:
                    t["debit_now"] = round(debit, 2)
                    t["unrealised"] = round((t["credit"] - debit) * (t["qty"] or 0), 2)
            except Exception as exc:
                t["mark_error"] = str(exc)[:120]
    elif broker is None:
        today["state"] = "Zerodha is not connected — paper trading needs live candles and quotes"
    for t in open_pos:
        if t["credit"] and t["qty"]:
            t["target_rs"] = round(ST.RULE.target_pct / 100 * t["credit"] * t["qty"], 2)
            t["stop_rs"] = round(-ST.RULE.stop_pct / 100 * t["credit"] * t["qty"], 2)

    closed = [t for t in trades if t["status"] == "CLOSED"]
    months: dict[str, dict] = {}
    for t in closed:
        m = months.setdefault(t["date"][:7], {"month": t["date"][:7], "trades": 0, "wins": 0, "net": 0.0})
        m["trades"] += 1
        m["wins"] += 1 if (t["pnl"] or 0) > 0 else 0
        m["net"] = round(m["net"] + (t["pnl"] or 0), 2)
    realised = sum(t["pnl"] or 0 for t in closed)
    return {
        "status": "ok", "mode": "PAPER", "enabled": bool(cfg.get("enabled")), "lots": cfg.get("lots"),
        "strategy": ST.STRATEGY_NAME, "rules": ST.describe(), "rule": ST.RULE.as_dict(),
        "market_open": market_open, "connected": broker is not None, "now": now.strftime("%H:%M:%S"),
        "today": today, "open_positions": open_pos, "trades": trades,
        "monthly": sorted(months.values(), key=lambda m: m["month"], reverse=True),
        "totals": {"trades": len(closed), "wins": sum(1 for t in closed if (t["pnl"] or 0) > 0),
                   "net": round(realised, 2),
                   "green_months": sum(1 for m in months.values() if m["net"] > 0), "months": len(months)},
        "backtest": latest_run(db, user_id),
    }
