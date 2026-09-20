"""
Flux Lab — replay and live paper trading.

Both modes exist to answer one question: does the strategy behave the same when time moves
forward one bar at a time as it did in the backtest? They therefore call
``engine.decide`` — the same function, on the same feature frame, with the same strategy object.
Nothing in this file knows how to make a trading decision; it only sources bars and, in paper
mode, prices a contract and keeps the position.

**Paper mode cannot place a real order.** There is no import of the order path in this module:
no ``place_order``, no ``OrderRequest``, no execution client. It reads quotes and candles and
writes rows to ``flux_lab_trades`` with ``mode='PAPER'``. That is the whole safety argument, and
it is enforced by an acceptance test that greps this module.

The forming candle is never used. A decision is only taken on a bar whose interval has closed —
the same discipline the OI Lab's signal hub needed, for the same reason: mid-bar values move,
and a backtest never sees them.
"""
from __future__ import annotations

import threading
import time as _time
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

from core.logger import get_logger
from core.models import FluxLabSignal, FluxLabTrade, StrategyConfig
from research.flux_lab import engine as EN
from research.flux_lab import features as FE
from research.flux_lab import rules as RU
from research.options_lab.execution import STEP

logger = get_logger("research.flux_lab.live")

CONFIG_NAME = "flux_lab_paper"
CHECK_EVERY_S = 20.0
BAR_SETTLE_S = 5           # give the exchange a moment after the bar closes
SESSION_OPEN = 9 * 60 + 15
SESSION_LAST_ENTRY = 15 * 60
SQUAREOFF = 15 * 60 + 15

DEFAULT_PAPER = {
    "enabled": False,
    "config": {},                # the same payload a backtest takes
    "max_trades_per_day": 3,
    "lots": 1,
}

_last_check: dict[int, float] = {}
_lock = threading.Lock()


# ── configuration ────────────────────────────────────────────────────
def load_config(db, user_id: int) -> dict:
    row = (db.query(StrategyConfig)
             .filter(StrategyConfig.user_id == user_id,
                     StrategyConfig.strategy_name == CONFIG_NAME).first())
    return {**DEFAULT_PAPER, **((row.config or {}) if row else {})}


def save_config(db, user_id: int, updates: dict) -> dict:
    cfg = {**load_config(db, user_id), **{k: v for k, v in (updates or {}).items() if k in DEFAULT_PAPER}}
    row = (db.query(StrategyConfig)
             .filter(StrategyConfig.user_id == user_id,
                     StrategyConfig.strategy_name == CONFIG_NAME).first())
    if row is None:
        db.add(StrategyConfig(user_id=user_id, strategy_name=CONFIG_NAME, config=cfg))
    else:
        row.config = cfg
    db.commit()
    return load_config(db, user_id)


# ── bars from the live session ───────────────────────────────────────
def _spot_token(broker, underlying: str) -> Optional[int]:
    key = {"NIFTY": ("NSE", "NIFTY 50"), "BANKNIFTY": ("NSE", "NIFTY BANK"),
           "SENSEX": ("BSE", "SENSEX")}.get(underlying, ("NSE", "NIFTY 50"))
    for inst in broker.get_instruments(key[0]) or []:
        if inst.get("tradingsymbol") == key[1]:
            return int(inst["instrument_token"])
    return None


def session_bars(broker, underlying: str, day: Optional[date] = None) -> pd.DataFrame:
    """Today's 1-minute index bars, straight from the broker's historical endpoint."""
    day = day or date.today()
    token = _spot_token(broker, underlying)
    if not token:
        return pd.DataFrame()
    start = datetime.combine(day, datetime.min.time()) + timedelta(hours=9, minutes=15)
    rows = broker.get_historical_data(token, start, datetime.now(), "minute") or []
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    ts = pd.to_datetime(df["date"], errors="coerce", utc=True)
    df["timestamp"] = ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return df[["timestamp", "open", "high", "low", "close", "volume"]].sort_values("timestamp")


def settled_frame(bars: pd.DataFrame, timeframe: int, params: dict,
                  now: Optional[datetime] = None) -> pd.DataFrame:
    """Feature frame with the still-forming bar removed.

    A 5-minute bar stamped 10:15 is only decided on once 10:20 has passed; until then its high,
    low and close are still moving and any signal read from it would be one a backtest could
    never have produced.
    """
    if bars.empty:
        return bars
    frame = FE.build(FE.resample(bars, timeframe), params)
    if frame.empty:
        return frame
    now = now or datetime.now()
    cutoff = now - timedelta(minutes=timeframe, seconds=BAR_SETTLE_S)
    return frame[frame["timestamp"] <= cutoff].reset_index(drop=True)


# ── replay: the backtest engine, stepped ─────────────────────────────
def replay_trace(bars: pd.DataFrame, options: pd.DataFrame, cfg: EN.RunConfig,
                 vix=None, futures=None) -> dict:
    """Every bar of one session with its decision, for the step-through view.

    This is the backtest engine's own output — the trades come from ``engine.run`` on exactly
    the data the replay shows, so what you step through is what was measured.
    """
    result = EN.run(bars, options, cfg, vix=vix, futures=futures)
    strat = RU.from_config(cfg.strategy)
    params = {**FE.DEFAULT_PARAMS, **(strat.params or {})}
    frame = FE.build(FE.resample(bars, cfg.timeframe), params, vix=vix, futures=futures)
    steps = []
    for i in range(len(frame)):
        row = frame.iloc[i]
        d = EN.decide(row, strat)
        steps.append({
            "i": i, "time": f"{int(row['minute']) // 60:02d}:{int(row['minute']) % 60:02d}",
            "timestamp": str(row["timestamp"]),
            "open": _f(row["open"]), "high": _f(row["high"]), "low": _f(row["low"]),
            "close": _f(row["close"]), "volume": _f(row.get("volume")),
            "vwap": _f(row.get("vwap")), "ema_fast": _f(row.get("ema_fast")),
            "ema_slow": _f(row.get("ema_slow")), "ema_trend": _f(row.get("ema_trend")),
            "bb_upper": _f(row.get("bb_upper")), "bb_lower": _f(row.get("bb_lower")),
            "rsi": _f(row.get("rsi")), "atr": _f(row.get("atr")),
            "fired": d.fired, "blocked_by": d.blocked_by,
            "passed": sum(1 for r in d.reasons if r["passed"]), "total": len(d.reasons),
            "reasons": d.reasons,
        })
    return {"status": "ok", "steps": steps, "trades": result.get("trades", []),
            "signals": [s for s in result.get("signals", []) if s.get("outcome") == "skipped"],
            "strategy": strat.name, "conditions": strat.describe()}


def _f(v):
    try:
        x = float(v)
        return round(x, 2) if x == x else None
    except (TypeError, ValueError):
        return None


# ── live paper trading ───────────────────────────────────────────────
class PaperEngine:
    """Signals → paper positions → paper exits. Never an order."""

    def __init__(self):
        self._chain_day: dict = {}

    # option chain from the live instrument dump, chosen at decision time
    def _contracts(self, broker, underlying: str) -> list[dict]:
        today = date.today()
        if self._chain_day.get("day") == today and self._chain_day.get("u") == underlying:
            return self._chain_day["rows"]
        exch = "BFO" if underlying in ("SENSEX", "BANKEX") else "NFO"
        rows = []
        for i in broker.get_instruments(exch) or []:
            if i.get("name") != underlying or i.get("instrument_type") not in ("CE", "PE"):
                continue
            exp = i.get("expiry")
            exp = exp if isinstance(exp, date) else pd.Timestamp(str(exp)).date() if exp else None
            if not exp or exp < today:
                continue
            rows.append({"tradingsymbol": i["tradingsymbol"], "token": int(i["instrument_token"]),
                         "strike": float(i.get("strike") or 0), "type": i["instrument_type"],
                         "expiry": exp, "lot_size": int(i.get("lot_size") or 0), "exchange": exch})
        self._chain_day = {"day": today, "u": underlying, "rows": rows}
        return rows

    def pick(self, broker, underlying: str, side: str, spot: float, sel: EN.Selection) -> Optional[dict]:
        rows = self._contracts(broker, underlying)
        if not rows:
            return None
        today = date.today()
        expiries = sorted({r["expiry"] for r in rows if (r["expiry"] - today).days >= sel.min_dte})
        if not expiries:
            return None
        if sel.expiry_rule == "next" and len(expiries) > 1:
            expiry = expiries[1]
        elif sel.expiry_rule == "monthly":
            month = (expiries[0].year, expiries[0].month)
            same = [e for e in expiries if (e.year, e.month) == month]
            expiry = same[-1]
        else:
            expiry = expiries[0]
        step = STEP.get(underlying, 50)
        atm = round(spot / step) * step
        strike = atm + (1 if side == "CE" else -1) * sel.moneyness * step
        for r in rows:
            if r["type"] == side and r["expiry"] == expiry and abs(r["strike"] - strike) < 0.01:
                return r
        return None

    # ── the tick ──
    def check(self, db, user_id: int, broker) -> dict:
        now = datetime.now()
        if now.weekday() >= 5:
            return {"skipped": "weekend"}
        minute = now.hour * 60 + now.minute
        if minute < SESSION_OPEN or minute > SQUAREOFF + 5:
            return {"skipped": "outside market hours"}
        with _lock:
            if _time.time() - _last_check.get(user_id, 0) < CHECK_EVERY_S:
                return {"skipped": "too soon"}
            _last_check[user_id] = _time.time()

        paper = load_config(db, user_id)
        if not paper.get("enabled"):
            return {"skipped": "paper trading is off"}
        cfg = EN.config_from(paper.get("config") or {})
        strat = RU.from_config(cfg.strategy)
        if not strat.conditions:
            return {"skipped": "no strategy configured"}

        bars = session_bars(broker, cfg.underlying)
        frame = settled_frame(bars, cfg.timeframe, {**FE.DEFAULT_PARAMS, **(strat.params or {})}, now)
        if frame.empty:
            return {"skipped": "no settled bar yet"}
        row = frame.iloc[-1]
        spot = float(row["close"])

        out = {"bar": str(row["timestamp"]), "spot": spot, "managed": [], "opened": None}
        out["managed"] = self._manage(db, user_id, broker, cfg, minute)

        open_rows = self._open_positions(db, user_id)
        if open_rows and cfg.risk.one_position_at_a_time:
            return {**out, "skipped": "a paper position is already open"}
        taken = self._today_count(db, user_id)
        if paper.get("max_trades_per_day") and taken >= int(paper["max_trades_per_day"]):
            return {**out, "skipped": "daily paper trade limit reached"}
        if minute > SESSION_LAST_ENTRY:
            return {**out, "skipped": "past the last entry time"}

        decision = EN.decide(row, strat)                 # ← the backtest's own decision function
        self._log_signal(db, user_id, row, strat, decision, acted=False)
        if not decision.fired:
            return {**out, "signal": False}

        pick = self.pick(broker, cfg.underlying, strat.side, spot, cfg.selection)
        if pick is None:
            return {**out, "signal": True, "skipped": "no contract matched the selection rule"}
        quote = broker.get_quote([f"{pick['exchange']}:{pick['tradingsymbol']}"]) or {}
        q = quote.get(f"{pick['exchange']}:{pick['tradingsymbol']}") or {}
        depth = (q.get("depth") or {}).get("sell") or []
        ask = float(depth[0]["price"]) if depth else float(q.get("last_price") or 0)
        if ask <= 0:
            return {**out, "signal": True, "skipped": "no live price for that contract"}

        lots = int(paper.get("lots") or cfg.risk.lots or 1)
        lot_size = pick["lot_size"] or EN.LOT_SIZE.get(cfg.underlying, 65)
        ex = cfg.execution
        target = ask + ex.target_pts if ex.target_pts > 0 else (ask * (1 + ex.target_pct / 100) if ex.target_pct else None)
        stop = ask - ex.stop_pts if ex.stop_pts > 0 else (ask * (1 - ex.stop_pct / 100) if ex.stop_pct else None)
        trade = FluxLabTrade(
            run_id=None, user_id=user_id, mode="PAPER", trade_no=taken + 1,
            trade_date=date.today(), signal_time=_hhmm(int(row["minute"])),
            entry_time=now.strftime("%H:%M"), side=strat.side,
            contract=pick["tradingsymbol"], strike=pick["strike"], expiry=pick["expiry"],
            dte=(pick["expiry"] - date.today()).days, lots=lots, qty=lots * lot_size,
            option_entry=round(ask, 2), spot_entry=round(spot, 2),
            bucket=str(row.get("bucket")), dow=int(row.get("dow", 0)),
            month=str(row.get("month")), year=int(row.get("year", 0)),
            regime=FE.regime(row), reasons=decision.reasons, indicators=EN._snapshot(row),
            ladder={"target": round(target, 2) if target else None,
                    "stop": round(stop, 2) if stop else None,
                    "max_hold_min": ex.max_hold_min},
            status="OPEN",
        )
        db.add(trade)
        db.commit()
        self._log_signal(db, user_id, row, strat, decision, acted=True, trade_id=trade.id)
        logger.info("flux paper: opened %s %s at %.2f (user %s)", strat.side, pick["tradingsymbol"], ask, user_id)
        return {**out, "signal": True, "opened": {"contract": pick["tradingsymbol"], "entry": round(ask, 2)}}

    # ── position management ──
    def _manage(self, db, user_id: int, broker, cfg: EN.RunConfig, minute: int) -> list[dict]:
        done = []
        for t in self._open_positions(db, user_id):
            exch = "BFO" if cfg.underlying in ("SENSEX", "BANKEX") else "NFO"
            key = f"{exch}:{t.contract}"
            q = (broker.get_quote([key]) or {}).get(key) or {}
            bid_depth = (q.get("depth") or {}).get("buy") or []
            bid = float(bid_depth[0]["price"]) if bid_depth else float(q.get("last_price") or 0)
            if bid <= 0:
                continue
            plan = t.ladder or {}
            entry = float(t.option_entry or 0)
            reason = None
            if plan.get("stop") and bid <= float(plan["stop"]):
                reason = "SL"
            elif plan.get("target") and bid >= float(plan["target"]):
                reason = "TARGET"
            elif minute >= SQUAREOFF:
                reason = "EOD"
            elif plan.get("max_hold_min"):
                held = _minutes_since(t.entry_time)
                if held is not None and held >= int(plan["max_hold_min"]):
                    reason = "TIME"
            if reason is None:
                continue
            spot_now = float((broker.get_quote([_spot_key(cfg.underlying)]) or {})
                             .get(_spot_key(cfg.underlying), {}).get("last_price") or 0)
            from research.options_lab.execution import CostModel
            charges = CostModel(slippage_pts=0.0).round_trip(entry, bid, int(t.qty or 0))
            t.option_exit = round(bid, 2)
            t.exit_time = datetime.now().strftime("%H:%M")
            t.spot_exit = round(spot_now, 2) if spot_now else None
            if spot_now and t.spot_entry:
                move = (spot_now - float(t.spot_entry)) if t.side == "CE" else (float(t.spot_entry) - spot_now)
                t.spot_move_pts = round(move, 2)
            t.gross_pts = round(bid - entry, 2)
            t.charges = round(charges, 2)
            t.pnl = round((bid - entry) * int(t.qty or 0) - charges, 2)
            t.exit_reason = reason
            t.held_min = _minutes_since(t.entry_time)
            t.status = "CLOSED"
            db.commit()
            done.append({"contract": t.contract, "reason": reason, "pnl": float(t.pnl)})
            logger.info("flux paper: closed %s (%s) pnl %.2f", t.contract, reason, float(t.pnl))
        return done

    def _open_positions(self, db, user_id: int):
        return (db.query(FluxLabTrade)
                  .filter(FluxLabTrade.user_id == user_id, FluxLabTrade.mode == "PAPER",
                          FluxLabTrade.status == "OPEN").all())

    def _today_count(self, db, user_id: int) -> int:
        return (db.query(FluxLabTrade)
                  .filter(FluxLabTrade.user_id == user_id, FluxLabTrade.mode == "PAPER",
                          FluxLabTrade.trade_date == date.today()).count())

    def _log_signal(self, db, user_id: int, row, strat, decision, acted: bool,
                    trade_id: Optional[int] = None) -> None:
        try:
            db.add(FluxLabSignal(
                user_id=user_id, mode="PAPER", trade_date=date.today(),
                bar_time=_hhmm(int(row["minute"])), strategy_name=strat.name, side=strat.side,
                spot=round(float(row["close"]), 2), fired=bool(decision.fired), acted=bool(acted),
                skip_reason=decision.blocked_by, reasons=decision.reasons,
                indicators=EN._snapshot(row), regime=FE.regime(row), trade_id=trade_id))
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.debug("signal log failed: %s", exc)


def _spot_key(underlying: str) -> str:
    return {"NIFTY": "NSE:NIFTY 50", "BANKNIFTY": "NSE:NIFTY BANK",
            "SENSEX": "BSE:SENSEX"}.get(underlying, "NSE:NIFTY 50")


def _hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _minutes_since(hhmm: Optional[str]) -> Optional[int]:
    if not hhmm:
        return None
    try:
        h, m = [int(x) for x in hhmm.split(":")[:2]]
    except Exception:
        return None
    now = datetime.now()
    return max(0, (now.hour * 60 + now.minute) - (h * 60 + m))


ENGINE = PaperEngine()


def dashboard(db, user_id: int, broker=None) -> dict:
    """What the paper desk shows: position, today's trades and the running tally."""
    cfg = load_config(db, user_id)
    rc = EN.config_from(cfg.get("config") or {})
    strat = RU.from_config(rc.strategy)
    today = date.today()
    rows = (db.query(FluxLabTrade)
              .filter(FluxLabTrade.user_id == user_id, FluxLabTrade.mode == "PAPER")
              .order_by(FluxLabTrade.id.desc()).limit(200).all())
    from research.flux_lab.service import _trade_dict
    trades = [_trade_dict(t) for t in rows]
    todays = [t for t in trades if t["date"] == str(today)]
    closed = [t for t in todays if t["status"] == "CLOSED"]
    open_rows = [t for t in trades if t["status"] == "OPEN"]
    live_spot = None
    if broker is not None:
        try:
            q = broker.get_quote([_spot_key(rc.underlying)]) or {}
            live_spot = float(q.get(_spot_key(rc.underlying), {}).get("last_price") or 0) or None
            for t in open_rows:                       # mark the open position to market
                exch = "BFO" if rc.underlying in ("SENSEX", "BANKEX") else "NFO"
                k = f"{exch}:{t['contract']}"
                oq = (broker.get_quote([k]) or {}).get(k) or {}
                ltp = float(oq.get("last_price") or 0)
                if ltp and t.get("option_entry"):
                    t["ltp"] = round(ltp, 2)
                    t["unrealised"] = round((ltp - t["option_entry"]) * (t["qty"] or 0), 2)
        except Exception as exc:
            logger.debug("paper dashboard quote failed: %s", exc)
    realised = sum(t["pnl"] or 0 for t in closed)
    wins = sum(1 for t in closed if (t["pnl"] or 0) > 0)
    now = datetime.now()
    minute = now.hour * 60 + now.minute
    return {
        "status": "ok", "enabled": bool(cfg.get("enabled")), "mode": "PAPER",
        "strategy": strat.name, "conditions": strat.describe(), "side": strat.side,
        "underlying": rc.underlying, "timeframe": rc.timeframe,
        "market_open": now.weekday() < 5 and SESSION_OPEN <= minute <= 15 * 60 + 30,
        "spot": live_spot,
        "max_trades_per_day": cfg.get("max_trades_per_day"), "lots": cfg.get("lots"),
        "open_positions": open_rows, "today": todays, "recent": trades[:50],
        "today_stats": {"trades": len(todays), "closed": len(closed), "wins": wins,
                        "win_rate": round(wins / len(closed) * 100, 1) if closed else None,
                        "realised": round(realised, 2),
                        "unrealised": round(sum(t.get("unrealised") or 0 for t in open_rows), 2)},
        "config": cfg,
    }
