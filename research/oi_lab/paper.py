"""
OI Lab paper engine — trades the signal desk on paper so it can be judged on live prices.

PAPER ONLY. There is no order path in this module; it reads quotes and writes rows.

Called every second for each logged-in user by the server's strategy loop (and throttled here):
  • keeps the signal hub evaluating each 5-min close for the indices the user trades
  • auto-paper ON: opens a position on each fresh signal that passes the user's filters
  • manages every open position — its own or opened by hand from the desk:
        SCALP  premium Gann target / stop, 45-minute limit
        SWING  spot target, premium catastrophe stop moved to breakeven after the first
               premium Gann level, exit alerts at each 5-min close
        both   square-off at 15:15; a position left open by a server restart is closed on the
               next check with reason MISSED_SQUAREOFF
Fills are realistic: buy at the best ask, sell at the best bid (LTP when the book is empty),
with Zerodha statutory charges deducted from P&L.
"""
from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta
from typing import Optional

from core.logger import get_logger
from research.oi_lab import indices as IX
from research.oi_lab import signal_backtest as SB
from research.oi_lab import signals as SG
from research.oi_lab.live import SERVICE
from research.oi_lab.signal_hub import HUB
from research.options_lab.execution import CostModel

logger = get_logger("research.oi_lab.paper")

CONFIG_NAME = "oi_lab_paper"
DEFAULT_CONFIG = {
    "auto": False,
    "indices": ["NIFTY", "SENSEX"],
    "mode": "SWING",                  # SWING | SCALP
    "lots": 1,
    "max_trades_per_day": 3,          # per index
    "setups": list(SG.SETUPS),
    "skip_losing_setups": False,      # skip setups whose unseen-data backtest lost money
    "signal_max_age_s": 120,          # never chase a signal older than this
}
CHECK_EVERY_S = 2.0
_COSTS = CostModel(slippage_pts=0.0)


def _num(x) -> Optional[float]:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def load_config(db, user_id: int) -> dict:
    from core.models import StrategyConfig
    row = db.query(StrategyConfig).filter(StrategyConfig.user_id == user_id,
                                          StrategyConfig.strategy_name == CONFIG_NAME).first()
    cfg = {**DEFAULT_CONFIG, **((row.config or {}) if row else {})}
    cfg["indices"] = [i for i in cfg.get("indices", []) if i in SG.TRADE_UNDERLYINGS]
    cfg["setups"] = [s for s in cfg.get("setups", []) if s in SG.SETUPS]
    cfg["mode"] = cfg.get("mode") if cfg.get("mode") in SG.MODES else "SWING"
    cfg["lots"] = max(1, min(int(cfg.get("lots") or 1), 50))
    cfg["max_trades_per_day"] = max(1, min(int(cfg.get("max_trades_per_day") or 3), 20))
    return cfg


def save_config(db, user_id: int, updates: dict) -> dict:
    from core.models import StrategyConfig
    cfg = {**load_config(db, user_id), **{k: v for k, v in (updates or {}).items() if k in DEFAULT_CONFIG}}
    row = db.query(StrategyConfig).filter(StrategyConfig.user_id == user_id,
                                          StrategyConfig.strategy_name == CONFIG_NAME).first()
    if row is None:
        row = StrategyConfig(user_id=user_id, strategy_name=CONFIG_NAME, config=cfg)
        db.add(row)
    else:
        row.config = cfg
    db.commit()
    ENGINE.forget_config(user_id)
    return load_config(db, user_id)


def _book(q: dict) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """(ltp, best bid, best ask) from a Kite quote."""
    ltp = _num(q.get("last_price"))
    depth = q.get("depth") or {}
    bid = next((_num(d.get("price")) for d in depth.get("buy") or [] if _num(d.get("price"))), None)
    ask = next((_num(d.get("price")) for d in depth.get("sell") or [] if _num(d.get("price"))), None)
    return ltp, bid or ltp, ask or ltp


class PaperEngine:
    def __init__(self):
        self._last_check: dict[int, float] = {}
        self._cfg: dict[int, tuple[float, dict]] = {}
        self._alert_cp: dict[int, int] = {}          # position id -> last 5-min close its alerts were read on
        self._lock = threading.Lock()

    def forget_config(self, user_id: int) -> None:
        self._cfg.pop(user_id, None)

    def _config(self, db, user_id: int) -> dict:
        hit = self._cfg.get(user_id)
        if hit and time.monotonic() - hit[0] < 20:
            return hit[1]
        cfg = load_config(db, user_id)
        self._cfg[user_id] = (time.monotonic(), cfg)
        return cfg

    # the loop ---------------------------------------------------------------
    def check(self, user_id: int, broker) -> None:
        now = IX.now_ist()
        if now.weekday() >= 5:
            return
        if time.monotonic() - self._last_check.get(user_id, 0) < CHECK_EVERY_S:
            return
        self._last_check[user_id] = time.monotonic()
        from core.database import get_db_session
        from core.models import OILabPaperPosition as P
        db = get_db_session()
        try:
            cfg = self._config(db, user_id)
            open_rows = db.query(P).filter(P.user_id == user_id, P.status == "OPEN").all()
            indices = (set(cfg["indices"]) if cfg["auto"] else set()) | {p.underlying for p in open_rows}
            for idx in indices & set(SG.TRADE_UNDERLYINGS):
                try:
                    HUB.tick(broker, idx)
                except Exception as exc:
                    logger.warning("paper: hub tick %s failed: %s", idx, exc)
            if cfg["auto"]:
                self._auto_enter(db, user_id, broker, cfg, now)
                open_rows = db.query(P).filter(P.user_id == user_id, P.status == "OPEN").all()
            if open_rows:
                self._manage(db, broker, open_rows, now)
        finally:
            db.close()

    # entries ------------------------------------------------------------------
    def _auto_enter(self, db, user_id: int, broker, cfg: dict, now: datetime) -> None:
        from core.models import OILabPaperPosition as P, OILabSignal as S
        today = now.date()
        for idx in cfg["indices"]:
            if db.query(P).filter(P.user_id == user_id, P.underlying == idx, P.status == "OPEN").count():
                continue
            if db.query(P).filter(P.user_id == user_id, P.underlying == idx, P.trade_date == today).count() >= cfg["max_trades_per_day"]:
                continue
            fresh = (db.query(S).filter(S.underlying == idx, S.trade_date == today)
                     .order_by(S.id.desc()).limit(5).all())
            for sig in fresh:
                # bar_time is the 5-min CLOSE the decision was made on; never chase an old signal
                age = (now - datetime.combine(today, datetime.min.time()) - timedelta(minutes=_minutes(sig.bar_time))).total_seconds()
                if age > cfg["signal_max_age_s"]:
                    continue
                if sig.setup not in cfg["setups"]:
                    continue
                if db.query(P).filter(P.user_id == user_id, P.signal_id == sig.id).count():
                    continue
                if cfg["skip_losing_setups"]:
                    rec = (sig.backtest or {}).get(cfg["mode"]) or {}
                    if (rec.get("verdict") or {}).get("grade") == "losing":
                        continue
                try:
                    open_from_signal(db, user_id, broker, sig, cfg["mode"], cfg["lots"], "AUTO")
                except Exception as exc:
                    logger.warning("paper: auto entry %s #%s failed: %s", idx, sig.id, exc)
                break

    # management ---------------------------------------------------------------
    def _manage(self, db, broker, rows: list, now: datetime) -> None:
        stale = [p for p in rows if p.trade_date < now.date()]
        for p in stale:
            _close(p, _num(p.ltp) or _num(p.entry_price), _num(p.spot_ltp), "MISSED_SQUAREOFF", "15:30")
        rows = [p for p in rows if p.trade_date == now.date()]
        if not rows:
            db.commit()
            return
        keys = {f"{p.exchange}:{p.tradingsymbol}" for p in rows}
        spot_keys = {IX.get(p.underlying)["spot"] for p in rows}
        try:
            quotes = broker.get_quote(list(keys | spot_keys)) or {}
        except Exception as exc:
            logger.warning("paper: quote failed: %s", exc)
            return
        m_now = now.hour * 60 + now.minute
        for p in rows:
            q = quotes.get(f"{p.exchange}:{p.tradingsymbol}") or {}
            ltp, bid, _ask = _book(q)
            spot = _num((quotes.get(IX.get(p.underlying)["spot"]) or {}).get("last_price"))
            if ltp is None:
                continue
            entry = float(p.entry_price)
            p.ltp, p.spot_ltp = ltp, spot
            p.mtm = round((ltp - entry) * p.qty, 2)
            p.max_favourable = max(float(p.max_favourable or 0), ltp - entry)
            p.max_adverse = min(float(p.max_adverse or 0), ltp - entry)
            stop = _num(p.premium_stop)
            reason = None
            if m_now >= SG.SQUAREOFF:
                reason = "SQUAREOFF"
            elif stop is not None and ltp <= stop:
                reason = "STOP" if stop < entry else "BREAKEVEN"
            elif p.mode == "SCALP":
                tgt = _num(p.premium_target)
                if tgt is not None and ltp >= tgt:
                    reason = "TARGET"
                elif m_now >= _minutes(p.entry_time) + SG.SCALP_MAX_HOLD:
                    reason = "TIME"
            else:
                tgt = _num(p.spot_target)
                if spot is not None and tgt is not None and ((p.side == "CE" and spot >= tgt) or (p.side == "PE" and spot <= tgt)):
                    reason = "TARGET"
                elif _num(p.trail_after) is not None and ltp >= float(p.trail_after) and (stop or 0) < entry:
                    p.premium_stop = entry
                    p.alerts = (p.alerts or []) + [{"time": now.strftime("%H:%M:%S"), "code": "TRAILED",
                                                    "text": "first premium Gann level reached — stop moved to entry"}]
                if reason is None:
                    reason = self._alerts(p, now)
            if reason:
                _close(p, bid, spot, reason, now.strftime("%H:%M:%S"))
        db.commit()

    def _alerts(self, p, now: datetime) -> Optional[str]:
        """Read the hub's latest 5-min context once per bar; a swing trade exits on any alert."""
        ctx = HUB.last_ctx(p.underlying)
        if not ctx or ctx.get("day") != now.date().isoformat():
            return None
        cp = int(ctx["cp"])
        if cp <= _minutes(p.entry_time) or self._alert_cp.get(p.id) == cp:
            return None
        self._alert_cp[p.id] = cp
        c = SG.Ctx(**{**ctx, "day": date.fromisoformat(ctx["day"]), "bar": SG.Bar(**ctx["bar"])})
        pos = {"side": p.side, "invalidation": _num(p.invalidation), "spot_entry": _num(p.spot_entry),
               "spot_target": _num(p.spot_target), "entry_doi_bal": p.entry_doi_bal, "adverse_streak": p.adverse_streak or 0}
        alerts, p.adverse_streak = SG.exit_alerts(pos, c, SG.DEFAULTS["alerts"])
        if not alerts:
            return None
        stamp = IX.hhmm(cp)
        p.alerts = (p.alerts or []) + [{"time": stamp, **a} for a in alerts]
        return f"ALERT:{alerts[0]['code']}"


def _minutes(hhmm: Optional[str]) -> int:
    try:
        h, m = str(hhmm).split(":")[:2]
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return 0


def _close(p, price: Optional[float], spot: Optional[float], reason: str, at: str) -> None:
    exit_px = float(price if price is not None else (p.ltp or p.entry_price))
    entry = float(p.entry_price)
    charges = _COSTS.round_trip(entry, exit_px, int(p.qty))
    p.exit_price, p.spot_exit, p.exit_reason, p.exit_time = exit_px, spot, reason[:32], at
    p.pnl_points = round(exit_px - entry, 2)
    p.charges = round(charges, 2)
    p.pnl = round((exit_px - entry) * int(p.qty) - charges, 2)
    p.mtm = p.pnl
    p.ltp = exit_px
    p.status = "CLOSED"
    logger.info("paper: closed #%s %s %s at %.2f (%s) pnl %.0f", p.id, p.underlying, p.tradingsymbol, exit_px, reason, p.pnl)


def open_from_signal(db, user_id: int, broker, sig, mode: str, lots: int, source: str):
    """Open a paper position on a saved signal at the live ask. Raises ValueError when it cannot."""
    from core.models import OILabPaperPosition as P
    if mode not in SG.MODES:
        raise ValueError("mode must be SWING or SCALP")
    now = IX.now_ist()
    if sig.trade_date != now.date():
        raise ValueError("this signal is from an earlier session")
    m_now = now.hour * 60 + now.minute
    if m_now >= SG.SQUAREOFF:
        raise ValueError("too late — positions are squared off at 15:15")
    if not sig.tradingsymbol:
        raise ValueError("the signal's contract was not in the live chain")
    key = f"{sig.exchange}:{sig.tradingsymbol}"
    spot_key = IX.get(sig.underlying)["spot"]
    quotes = broker.get_quote([key, spot_key]) or {}
    ltp, _bid, ask = _book(quotes.get(key) or {})
    if not ask:
        raise ValueError(f"no live price for {sig.tradingsymbol}")
    spot = _num((quotes.get(spot_key) or {}).get("last_price"))
    lot = int(sig.lot_size or SB.lot_for(sig.underlying))
    lots = max(1, min(int(lots or 1), 50))
    plan = SG.plans({"spot_target": _num(sig.spot_target), "invalidation": _num(sig.invalidation)}, ask)[mode]
    row = P(
        user_id=user_id, trade_date=now.date(), signal_id=sig.id, source=source, mode=mode,
        underlying=sig.underlying, setup=sig.setup, side=sig.side, tradingsymbol=sig.tradingsymbol, token=sig.token,
        exchange=sig.exchange, strike=sig.strike, expiry=sig.expiry, lots=lots, qty=lots * lot,
        entry_time=now.strftime("%H:%M:%S"), entry_price=ask, spot_entry=spot, level=sig.level,
        spot_target=sig.spot_target, invalidation=sig.invalidation,
        premium_stop=plan.get("stop") if mode == "SCALP" else plan.get("premium_stop"),
        premium_target=plan.get("target1") if mode == "SCALP" else None,
        trail_after=plan.get("trail_after"), entry_doi_bal=(sig.context or {}).get("doi_bal"),
        ltp=ltp, spot_ltp=spot, mtm=0, alerts=[],
    )
    db.add(row)
    db.commit()
    logger.info("paper: opened #%s %s %s %s %s x%d at %.2f (%s)", row.id, source, mode, sig.underlying,
                sig.tradingsymbol, row.qty, ask, sig.setup)
    return row


def close_manual(db, user_id: int, broker, position_id: int):
    from core.models import OILabPaperPosition as P
    p = db.query(P).filter(P.id == int(position_id), P.user_id == user_id).first()
    if p is None:
        raise ValueError("position not found")
    if p.status != "OPEN":
        raise ValueError("position is already closed")
    key, spot_key = f"{p.exchange}:{p.tradingsymbol}", IX.get(p.underlying)["spot"]
    try:
        quotes = broker.get_quote([key, spot_key]) or {}
    except Exception:
        quotes = {}
    _ltp, bid, _ask = _book(quotes.get(key) or {})
    _close(p, bid or _num(p.ltp), _num((quotes.get(spot_key) or {}).get("last_price")), "MANUAL",
           IX.now_ist().strftime("%H:%M:%S"))
    db.commit()
    return p


def serialize(p) -> dict:
    f = _num
    return {
        "id": p.id, "trade_date": p.trade_date.isoformat() if p.trade_date else None, "signal_id": p.signal_id,
        "source": p.source, "mode": p.mode, "underlying": p.underlying, "setup": p.setup,
        "label": SG.SETUPS.get(p.setup, {}).get("label", p.setup), "side": p.side, "symbol": p.tradingsymbol,
        "strike": f(p.strike), "expiry": p.expiry.isoformat() if p.expiry else None, "lots": p.lots, "qty": p.qty,
        "entry_time": p.entry_time, "entry_price": f(p.entry_price), "spot_entry": f(p.spot_entry), "level": f(p.level),
        "spot_target": f(p.spot_target), "invalidation": f(p.invalidation), "premium_stop": f(p.premium_stop),
        "premium_target": f(p.premium_target), "trail_after": f(p.trail_after), "ltp": f(p.ltp), "spot_ltp": f(p.spot_ltp),
        "mtm": f(p.mtm), "max_favourable": f(p.max_favourable), "max_adverse": f(p.max_adverse), "alerts": p.alerts or [],
        "status": p.status, "exit_time": p.exit_time, "exit_price": f(p.exit_price), "spot_exit": f(p.spot_exit),
        "exit_reason": p.exit_reason, "pnl_points": f(p.pnl_points), "charges": f(p.charges), "pnl": f(p.pnl),
    }


def journal(db, user_id: int, days: int = 30) -> dict:
    from core.models import OILabPaperPosition as P
    since = IX.now_ist().date() - timedelta(days=max(1, min(days, 365)))
    rows = (db.query(P).filter(P.user_id == user_id, P.trade_date >= since)
            .order_by(P.trade_date.desc(), P.id.desc()).all())
    items = [serialize(p) for p in rows]
    closed = [i for i in items if i["status"] == "CLOSED" and i["pnl"] is not None]

    def stats(xs):
        if not xs:
            return {"trades": 0}
        pnl = [x["pnl"] for x in xs]
        wins = [v for v in pnl if v > 0]
        losses = [v for v in pnl if v < 0]
        return {"trades": len(xs), "win_rate": round(100 * len(wins) / len(xs), 1), "total_pnl": round(sum(pnl)),
                "avg_pnl": round(sum(pnl) / len(xs)), "best": round(max(pnl)), "worst": round(min(pnl)),
                "profit_factor": round(sum(wins) / -sum(losses), 2) if losses else None}

    by = {}
    for x in closed:
        by.setdefault((x["underlying"], x["setup"], x["mode"]), []).append(x)
    days_ = {}
    for x in closed:
        days_.setdefault(x["trade_date"], []).append(x["pnl"])
    return {
        "status": "ok", "since": since.isoformat(), "positions": items,
        "open": [i for i in items if i["status"] == "OPEN"],
        "stats": stats(closed),
        "by_setup": [{"underlying": k[0], "setup": k[1], "label": SG.SETUPS.get(k[1], {}).get("label", k[1]),
                      "mode": k[2], **stats(v)} for k, v in sorted(by.items())],
        "daily": [{"date": d, "pnl": round(sum(v)), "trades": len(v)} for d, v in sorted(days_.items())],
    }


ENGINE = PaperEngine()
