"""
Live signal hub for the OI Lab signal desk (NIFTY and SENSEX).

Once per 5-minute close per index — shared by every user — it builds the same ``Ctx`` the
backtest builds (the 5-min spot bar that just closed from Kite's candle, OI walls / fresh-writing
balance / ATM straddle from the live snapshot, wall-hold odds from the tested models), fires
``signals.evaluate`` and saves each signal. The paper engine and the UI read from here, so
"12:20 analysis → buy 74600 CE at 12:21" is decided in exactly one place.
"""
from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta
from typing import Optional

from core.logger import get_logger
from research.oi_lab import gann
from research.oi_lab import indices as IX
from research.oi_lab import signal_backtest as SB
from research.oi_lab import signals as SG
from research.oi_lab.live import SERVICE, _market_open

logger = get_logger("research.oi_lab.signal_hub")

BAR_SETTLE_S = 6          # give Kite a few seconds to finalise the 5-min candle
RETRY_S = 10


def _f(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


class SignalHub:
    def __init__(self):
        self._lock = threading.Lock()
        self._index_locks: dict[str, threading.Lock] = {}
        self._evaluated: dict[str, tuple] = {}        # index -> (day, cp)
        self._last_try: dict[str, float] = {}
        self._state: dict[str, dict] = {}             # index -> last evaluation (ctx, blocked, day)

    def _ilock(self, idx: str) -> threading.Lock:
        with self._lock:
            return self._index_locks.setdefault(idx, threading.Lock())

    @staticmethod
    def due_cp(now: datetime) -> Optional[int]:
        """The latest 5-min close that is settled and inside the session."""
        m = now.hour * 60 + now.minute
        cp = m - m % 5
        if m == cp and now.second < BAR_SETTLE_S:
            cp -= 5
        if cp < IX.SESSION_OPEN_MIN + 5 or cp > IX.SESSION_CLOSE_MIN:
            return None
        return cp

    # context --------------------------------------------------------------
    def build_ctx(self, broker, idx: str, cp: int, snap: Optional[dict] = None, force_bar: bool = True):
        snap = snap or SERVICE.snapshot(broker, idx)
        if snap.get("status") != "ok":
            return None, snap, snap.get("message") or "no snapshot"
        if not snap.get("live_session"):
            # a market holiday passes the weekday/time check, but the tape then holds the PREVIOUS
            # session's candles — evaluating them would fire yesterday's signals as today's
            return None, snap, "no live session (market closed or holiday)"
        token = SERVICE._spot_token(broker, idx)
        day = date.fromisoformat(snap["session"])
        closed_at = datetime.combine(day, datetime.min.time()) + timedelta(minutes=cp, seconds=BAR_SETTLE_S - 1)

        def settled(rec) -> bool:
            # Kite also returns the candle still forming; a bar only counts once fetched after it closed
            return bool(rec) and rec.get("fetched_at") is not None and rec["fetched_at"] >= closed_at

        rec = SERVICE.tape.get(token) if token else None
        have = [b for b in (rec or {}).get("bars", []) if b["cp"] <= cp]
        if force_bar and token and (not have or have[-1]["cp"] < cp or not settled(rec)):
            rec = SERVICE.tape.fetch_now(broker, token, with_oi=False)
            have = [b for b in (rec or {}).get("bars", []) if b["cp"] <= cp]
        if len(have) < 2 or have[-1]["cp"] != cp or not settled(rec):
            return None, snap, f"the {IX.hhmm(cp)} candle is not settled yet"
        b = have[-1]
        model = snap.get("model") or {}
        feat, walls = model.get("features") or {}, model.get("walls") or {}
        probs = ((model.get("prediction") or {}).get("probabilities")) or {}
        ctx = SG.Ctx(
            underlying=idx, day=date.fromisoformat(snap["session"]), cp=cp,
            bar=SG.Bar(cp, b.get("open") or b["close"], b.get("high") or b["close"], b.get("low") or b["close"], b["close"]),
            prev_close=have[-2]["close"], straddle=_f(snap.get("straddle")) or float("nan"),
            step=float(snap.get("step") or 50), dte=int(snap.get("dte") or 0),
            ce_wall=_f(walls.get("ce_wall")), pe_wall=_f(walls.get("pe_wall")), doi_bal=_f(feat.get("doi_bal")),
            p_ce_held=_f((probs.get("ce_held") or {}).get("p")), p_pe_held=_f((probs.get("pe_held") or {}).get("p")),
        )
        return ctx, snap, None

    # evaluation -----------------------------------------------------------
    def tick(self, broker, index: str) -> list[dict]:
        """Evaluate the latest settled 5-min close once. Returns the signals that fired (saved)."""
        idx = (index or "").upper()
        if idx not in SG.TRADE_UNDERLYINGS:
            return []
        now = IX.now_ist()
        if not _market_open(now):
            return []
        cp = self.due_cp(now)
        if cp is None:
            return []
        with self._ilock(idx):
            if self._evaluated.get(idx) == (now.date(), cp):
                return []
            if time.monotonic() - self._last_try.get(idx, 0) < RETRY_S and self._state.get(idx, {}).get("pending_cp") == cp:
                return []
            self._last_try[idx] = time.monotonic()
            try:
                ctx, snap, why = self.build_ctx(broker, idx, cp)
            except Exception as exc:
                logger.warning("signal hub %s %s: context failed: %s", idx, IX.hhmm(cp), exc)
                self._state.setdefault(idx, {})["pending_cp"] = cp
                return []
            if ctx is None:
                self._state.setdefault(idx, {}).update(pending_cp=cp, waiting=why)
                return []
            self._evaluated[idx] = (now.date(), cp)
            fired, blocked = SG.evaluate(ctx)
            saved = [self._save(self._enrich(sig, snap)) for sig in fired]
            self._state[idx] = {"day": now.date().isoformat(), "cp": cp, "ctx": SG.ctx_dict(ctx), "blocked": blocked,
                                "fired": [s["id"] for s in saved if s.get("id")], "evaluated_at": now.strftime("%H:%M:%S")}
            if saved:
                logger.info("signal hub %s %s: %s", idx, IX.hhmm(cp), ", ".join(f"{s['setup']} {s['side']} {s['strike']:g}" for s in saved))
            return saved

    def last_ctx(self, index: str) -> Optional[dict]:
        return (self._state.get(index.upper()) or {}).get("ctx")

    @staticmethod
    def _enrich(sig: dict, snap: dict) -> dict:
        row = next((r for r in snap.get("rows", []) if abs(r["strike"] - sig["strike"]) < 0.5), None)
        cell = (row or {}).get(sig["side"].lower()) or {}
        ltp = _f(cell.get("ltp"))
        summary = SB._summaries.get(sig["underlying"])
        return {
            **sig, "symbol": cell.get("symbol"), "token": cell.get("token"), "exchange": snap.get("exchange"),
            "expiry": snap.get("expiry"), "lot_size": cell.get("lot_size") or snap.get("lot_size"),
            "premium": ltp, "delta": cell.get("delta"), "theta": cell.get("theta"), "iv": cell.get("iv"),
            "plans": SG.plans(sig, ltp) if ltp else {},
            "backtest": SB.stats_for(summary, sig["setup"]),
        }

    @staticmethod
    def _save(sig: dict) -> dict:
        try:
            from core.database import get_db_session
            from core.models import OILabSignal
            db = get_db_session()
            try:
                day = date.fromisoformat(sig["day"])
                row = (db.query(OILabSignal).filter(OILabSignal.trade_date == day, OILabSignal.underlying == sig["underlying"],
                                                    OILabSignal.bar_time == sig["time"], OILabSignal.setup == sig["setup"],
                                                    OILabSignal.level == sig["level"]).first())
                if row is None:
                    row = OILabSignal(
                        trade_date=day, underlying=sig["underlying"], bar_time=sig["time"], setup=sig["setup"],
                        side=sig["side"], level=sig["level"], spot=sig["spot"], spot_target=sig["spot_target"],
                        invalidation=sig["invalidation"], strike=sig["strike"], tradingsymbol=sig.get("symbol"),
                        token=sig.get("token"), exchange=sig.get("exchange"),
                        expiry=date.fromisoformat(sig["expiry"]) if sig.get("expiry") else None,
                        lot_size=sig.get("lot_size"), premium=sig.get("premium"), plans=sig.get("plans") or {},
                        reasons=sig.get("reasons") or [], context=sig.get("context") or {}, backtest=sig.get("backtest") or {})
                    db.add(row)
                    db.commit()
                sig["id"] = row.id
            finally:
                db.close()
        except Exception as exc:
            logger.error("signal save failed: %s", exc)
        return sig

    # desk -----------------------------------------------------------------
    def desk(self, broker, index: str) -> dict:
        idx = index.upper()
        tradeable = idx in SG.TRADE_UNDERLYINGS
        if tradeable:
            self.tick(broker, idx)
        snap = SERVICE.snapshot(broker, idx)
        if snap.get("status") != "ok":
            return snap
        now = IX.now_ist()
        spot = float(snap["spot"])
        # "watch" reads the latest completed bar we already hold — no extra Kite call
        ctx = None
        token = SERVICE._spot_token(broker, idx)
        bars = (SERVICE.tape.get(token) or {}).get("bars", []) if token else []
        if len(bars) >= 2:
            state = self._state.get(idx) or {}
            b, prev = bars[-1], bars[-2]
            model = snap.get("model") or {}
            feat, walls = model.get("features") or {}, model.get("walls") or {}
            probs = ((model.get("prediction") or {}).get("probabilities")) or {}
            ctx = SG.Ctx(underlying=idx, day=date.fromisoformat(snap["session"]), cp=b["cp"],
                         bar=SG.Bar(b["cp"], b.get("open") or b["close"], b.get("high") or b["close"], b.get("low") or b["close"], spot),
                         prev_close=prev["close"], straddle=_f(snap.get("straddle")) or float("nan"),
                         step=float(snap.get("step") or 50), dte=int(snap.get("dte") or 0),
                         ce_wall=_f(walls.get("ce_wall")), pe_wall=_f(walls.get("pe_wall")), doi_bal=_f(feat.get("doi_bal")),
                         p_ce_held=_f((probs.get("ce_held") or {}).get("p")), p_pe_held=_f((probs.get("pe_held") or {}).get("p")))
        rows = {r["strike"]: r for r in snap.get("rows", [])}
        ladders = {}
        for side in ("CE", "PE"):
            k = SG.itm_strike(side, spot, float(snap.get("step") or 50), SG.DEFAULTS["strike"])
            cell = (rows.get(k) or {}).get(side.lower()) or {}
            if cell.get("ltp"):
                ladders[side] = {"strike": k, "symbol": cell.get("symbol"), "ltp": cell["ltp"],
                                 "plan": gann.premium_plan(float(cell["ltp"])), "ladder": gann.ladder(float(cell["ltp"]), 3)}
        state = self._state.get(idx) or {}
        summary = SB.get(idx) if tradeable else None
        return {
            "status": "ok", "index": idx, "tradeable": tradeable, "trade_underlyings": list(SG.TRADE_UNDERLYINGS),
            "spot": spot, "session": snap["session"], "live_session": snap["live_session"],
            "now": now.strftime("%H:%M:%S"),
            "evaluated_bar": IX.hhmm(state["cp"]) if state.get("cp") else None,
            "evaluated_at": state.get("evaluated_at"), "waiting": state.get("waiting") if state.get("pending_cp") else None,
            "next_bar": IX.hhmm(((now.hour * 60 + now.minute) // 5 + 1) * 5),
            "blocked": state.get("blocked") or [],
            "signals": signals_for(idx, date.fromisoformat(snap["session"]), rows),
            "watch": SG.watch(ctx) if ctx else [],
            "gann": {"spot_floor": gann.floor(spot), "spot_ceil": gann.ceil(spot), "spot_ladder": gann.ladder(spot, 3),
                     "premium": ladders},
            "walls": (snap.get("model") or {}).get("walls"),
            "backtest": _slim(summary), "backtest_status": SB.status(idx),
            "rules": {"version": SG.RULES_VERSION, "params": SG.DEFAULTS, "entry_window": [IX.hhmm(SG.ENTRY_START), IX.hhmm(SG.ENTRY_END)],
                      "expiry_last_entry": IX.hhmm(SG.EXPIRY_DAY_LAST_ENTRY), "squareoff": IX.hhmm(SG.SQUAREOFF)},
        }


def _slim(summary: Optional[dict]) -> Optional[dict]:
    if not summary:
        return None
    return {k: v for k, v in summary.items() if not k.startswith("_")}


def signals_for(idx: str, day: date, rows: Optional[dict] = None) -> list[dict]:
    try:
        from core.database import get_db_session
        from core.models import OILabSignal as S
        db = get_db_session()
        try:
            out = []
            for r in (db.query(S).filter(S.underlying == idx, S.trade_date == day).order_by(S.id.desc()).limit(50).all()):
                live = ((rows or {}).get(float(r.strike)) or {}).get((r.side or "").lower()) or {}
                out.append({
                    "id": r.id, "time": r.bar_time, "setup": r.setup, "label": SG.SETUPS.get(r.setup, {}).get("label", r.setup),
                    "side": r.side, "level": _f(r.level), "spot": _f(r.spot), "spot_target": _f(r.spot_target),
                    "invalidation": _f(r.invalidation), "strike": _f(r.strike), "symbol": r.tradingsymbol,
                    "expiry": r.expiry.isoformat() if r.expiry else None, "lot_size": r.lot_size,
                    "premium": _f(r.premium), "premium_now": _f(live.get("ltp")), "plans": r.plans or {},
                    "reasons": r.reasons or [], "context": r.context or {}, "backtest": r.backtest or {},
                })
            return out
        finally:
            db.close()
    except Exception as exc:
        logger.debug("signals_for failed: %s", exc)
        return []


HUB = SignalHub()
