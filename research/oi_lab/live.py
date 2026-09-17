"""
Live OI X-ray: broker snapshot + intraday tape, assembled with analytics, history and setups.

Market data is the same for every user, so snapshots and the tape are shared and cached;
each request passes its own logged-in broker, which is only used to fetch.

The tape
--------
Kite quotes carry today's OI but not yesterday's, and no intraday history. One
``5minute`` historical call per contract (spanning the previous sessions) returns both:
the previous close OI and today's OI/price path. Those calls are rate-limited by Kite
(~3/s), so a background worker fetches them ATM-first and refreshes each contract every
few minutes. The snapshot never blocks on it for more than a few seconds; anything not
yet fetched shows "—" and fills in on the next refresh.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import date, datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from core.logger import get_logger
from research.black_scholes import implied_vol, greeks
from research.oi_lab import analytics as AN
from research.oi_lab import features as FT
from research.oi_lab import history as HS
from research.oi_lab import indices as IX
from research.oi_lab import setups as SU

logger = get_logger("research.oi_lab.live")

RATE = 0.10                    # matches the stored history's IV (verified: BS at r = 10%, 15:30 expiry)
TAPE_INTERVAL = "5minute"
TAPE_REFRESH_S = 240
TAPE_SPACING_S = 0.36          # ≤ 3 historical calls per second
SNAPSHOT_TTL_S = 5
MIN_T = 5 / (365 * 24 * 60)


def _as_date(v) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return pd.Timestamp(str(v)).date()
    except Exception:
        return None


def _naive(dt) -> Optional[datetime]:
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return None
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(IX.IST).replace(tzinfo=None)
    return dt


def _market_open(now: datetime) -> bool:
    m = now.hour * 60 + now.minute
    return now.weekday() < 5 and IX.SESSION_OPEN_MIN <= m <= IX.SESSION_CLOSE_MIN + 5


# ── intraday tape ────────────────────────────────────────────────────
class Tape:
    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict[int, dict] = {}
        self._queue: deque = deque()
        self._queued: set[int] = set()
        self._with_oi: dict[int, bool] = {}
        self._broker = None
        self._thread: Optional[threading.Thread] = None

    def _stale(self, token: int) -> bool:
        rec = self._data.get(token)
        if rec is None:
            return True
        age = time.monotonic() - rec["fetched"]
        if rec.get("error"):
            return age > 60
        now = IX.now_ist()
        if _market_open(now):
            return age > TAPE_REFRESH_S
        # fetched during today's session but the session is now over → one more fetch for the final bars
        closed_at = datetime.combine(now.date(), datetime.min.time()) + timedelta(minutes=IX.SESSION_CLOSE_MIN + 5)
        fetched_at = rec.get("fetched_at")
        return fetched_at is not None and fetched_at.date() == now.date() and fetched_at < closed_at <= now

    def request(self, broker, tokens: list[int], with_oi: bool = True) -> None:
        with self._lock:
            self._broker = broker
            self._prune()
            for t in tokens:
                self._with_oi[t] = with_oi
                if t not in self._queued and self._stale(t):
                    self._queue.append(t)
                    self._queued.add(t)
            if self._queue and (self._thread is None or not self._thread.is_alive()):
                self._thread = threading.Thread(target=self._run, daemon=True, name="oi-lab-tape")
                self._thread.start()

    def _prune(self) -> None:
        """Drop tapes of sessions older than a week (expired contracts accumulate otherwise)."""
        cutoff = IX.now_ist().date() - timedelta(days=7)
        for t in [t for t, r in self._data.items() if r.get("session") and r["session"] < cutoff]:
            self._data.pop(t, None)
            self._with_oi.pop(t, None)

    def get(self, token: int) -> Optional[dict]:
        rec = self._data.get(token)
        return rec if rec and not rec.get("error") else None

    def wait(self, tokens: list[int], timeout: float) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if all(t in self._data for t in tokens):
                return
            time.sleep(0.15)

    def _run(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    return
                token = self._queue.popleft()
                self._queued.discard(token)
                broker, with_oi = self._broker, self._with_oi.get(token, True)
            t0 = time.monotonic()
            self._fetch(broker, token, with_oi)
            time.sleep(max(0.0, TAPE_SPACING_S - (time.monotonic() - t0)))

    def _fetch(self, broker, token: int, with_oi: bool) -> None:
        now = IX.now_ist()
        frm = datetime.combine(now.date() - timedelta(days=7), datetime.min.time()) + timedelta(hours=9, minutes=15)
        try:
            candles = broker.get_historical_data(token, frm, now, TAPE_INTERVAL, oi=with_oi) or []
        except Exception as exc:
            logger.debug("tape fetch %s failed: %s", token, exc)
            self._data[token] = {"fetched": time.monotonic(), "fetched_at": now, "error": str(exc)[:200]}
            return
        rows = []
        for c in candles:
            dt = _naive(c.get("date"))
            if dt is None:
                continue
            rows.append((dt.date(), dt.hour * 60 + dt.minute + 5, float(c.get("close") or 0),
                         float(c.get("volume") or 0), float(c.get("oi") or 0)))
        if not rows:
            self._data[token] = {"fetched": time.monotonic(), "fetched_at": now, "session": None, "prev_oi": None,
                                 "prev_close": None, "bars": []}
            return
        session = rows[-1][0]
        prev = [r for r in rows if r[0] < session]
        bars = [{"cp": r[1], "close": r[2], "volume": r[3], "oi": r[4]} for r in rows if r[0] == session]
        self._data[token] = {
            "fetched": time.monotonic(), "fetched_at": now, "session": session,
            "prev_oi": prev[-1][4] if prev and with_oi else None,
            "prev_close": prev[-1][2] if prev else None, "bars": bars,
        }


# ── live service ─────────────────────────────────────────────────────
class OILabLive:
    def __init__(self):
        self.tape = Tape()
        self._inst: dict[str, tuple[date, list[dict]]] = {}
        self._spot_tokens: dict[str, int] = {}
        self._cache: dict[tuple, tuple[float, dict]] = {}
        self._lock = threading.Lock()                      # guards the dicts only, never held while fetching
        self._key_locks: dict[tuple, threading.Lock] = {}

    # instruments ---------------------------------------------------------
    def _options(self, broker, index: str) -> list[dict]:
        cfg = IX.get(index)
        today = IX.now_ist().date()
        hit = self._inst.get(cfg["key"])
        if hit and hit[0] == today:
            return hit[1]
        out = []
        for inst in broker.get_instruments(cfg["exch"]):
            if inst.get("name") != cfg["key"] or inst.get("instrument_type") not in ("CE", "PE"):
                continue
            exp = _as_date(inst.get("expiry"))
            if not exp:
                continue
            out.append({"symbol": inst.get("tradingsymbol"), "token": int(inst["instrument_token"]),
                        "strike": float(inst.get("strike") or 0), "type": inst["instrument_type"],
                        "expiry": exp, "lot_size": int(inst.get("lot_size") or 0),
                        "exch": cfg["exch"]})
        self._inst[cfg["key"]] = (today, out)
        return out

    def _spot_token(self, broker, index: str) -> Optional[int]:
        cfg = IX.get(index)
        if cfg["key"] in self._spot_tokens:
            return self._spot_tokens[cfg["key"]]
        exch, sym = cfg["spot"].split(":", 1)
        try:
            for inst in broker.get_instruments(exch):
                if inst.get("tradingsymbol") == sym:
                    self._spot_tokens[cfg["key"]] = int(inst["instrument_token"])
                    return self._spot_tokens[cfg["key"]]
        except Exception as exc:
            logger.debug("spot token lookup failed for %s: %s", index, exc)
        return None

    def expiries(self, broker, index: str) -> list[str]:
        today = IX.now_ist().date()
        return [e.isoformat() for e in sorted({o["expiry"] for o in self._options(broker, index) if o["expiry"] >= today})]

    @staticmethod
    def _step(strikes: list[float], spot: float, default: float) -> float:
        near = sorted(s for s in strikes if abs(s - spot) <= spot * 0.03)
        diffs = np.diff(near)
        diffs = diffs[diffs > 0]
        if not len(diffs):
            return default
        vals, counts = np.unique(np.round(diffs, 2), return_counts=True)
        return float(vals[np.argmax(counts)])

    # snapshot ------------------------------------------------------------
    def snapshot(self, broker, index: str, expiry: Optional[str] = None, window: int = 7) -> dict:
        cfg = IX.get(index)
        window = max(3, min(int(window or 7), 15))
        key = (cfg["key"], expiry or "", window)
        with self._lock:
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        # One build per chain at a time. Other indices/windows build in parallel, and
        # concurrent viewers of the same chain wait for, then reuse, the fresh result.
        with key_lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < SNAPSHOT_TTL_S:
                return hit[1]
            out = self._build(broker, cfg, expiry, window)
            if out.get("status") == "ok":
                with self._lock:
                    self._cache[key] = (time.monotonic(), out)
            return out

    def _build(self, broker, cfg: dict, expiry: Optional[str], window: int) -> dict:
        now = IX.now_ist()
        opts = self._options(broker, cfg["key"])
        if not opts:
            return {"status": "error", "message": f"No {cfg['key']} options listed on {cfg['exch']}"}
        exps = sorted({o["expiry"] for o in opts if o["expiry"] >= now.date()})
        exp = _as_date(expiry) if expiry else (exps[0] if exps else None)
        if not exp:
            return {"status": "error", "message": "No live expiry found"}

        q0 = broker.get_quote([cfg["spot"], IX.VIX_KEY]) or {}
        sq = q0.get(cfg["spot"]) or {}
        spot = float(sq.get("last_price") or 0)
        if spot <= 0:
            return {"status": "error", "message": f"No {cfg['label']} price — is Zerodha connected?"}
        s_ohlc = sq.get("ohlc") or {}
        vix = (q0.get(IX.VIX_KEY) or {}).get("last_price")

        listed = [o for o in opts if o["expiry"] == exp]
        strikes_all = sorted({o["strike"] for o in listed})
        if not strikes_all:
            return {"status": "error", "message": f"No {cfg['key']} contracts listed for expiry {exp.isoformat()} — "
                                                  "it may have expired. Pick another expiry.",
                    "expiries": [e.isoformat() for e in exps[:8]]}
        step = self._step(strikes_all, spot, cfg["step"])
        atm = min(strikes_all, key=lambda s: abs(s - spot))
        i = strikes_all.index(atm)
        strikes = strikes_all[max(0, i - window): i + window + 1]
        by = {(o["strike"], o["type"]): o for o in listed if o["strike"] in set(strikes)}
        keys = {f"{o['exch']}:{o['symbol']}": o for o in by.values()}
        quotes = broker.get_quote(list(keys)) or {}

        # tape: ATM-first, wait briefly for the strikes that matter most
        ordered = sorted(by.values(), key=lambda o: abs(o["strike"] - atm))
        spot_token = self._spot_token(broker, cfg["key"])
        if spot_token:
            self.tape.request(broker, [spot_token], with_oi=False)
        self.tape.request(broker, [o["token"] for o in ordered])
        must = [o["token"] for o in ordered if abs(o["strike"] - atm) <= step * 2.01] + ([spot_token] if spot_token else [])
        self.tape.wait(must, timeout=4.0)

        spot_tape = self.tape.get(spot_token) if spot_token else None
        session = (spot_tape or {}).get("session") or next(
            (t["session"] for t in (self.tape.get(o["token"]) for o in ordered) if t and t.get("session")), now.date())
        dte = max((exp - session).days, 0)
        now_min = now.hour * 60 + now.minute
        live_session = session == now.date() and _market_open(now)
        # A closed session's last prices were struck with the time left AT its close, not now.
        session_close = datetime.combine(session, datetime.min.time()) + timedelta(minutes=IX.SESSION_CLOSE_MIN)
        priced_at = now if live_session else min(now, session_close)
        exp_close = datetime.combine(exp, datetime.min.time()) + timedelta(minutes=IX.SESSION_CLOSE_MIN)
        T = max((exp_close - priced_at).total_seconds() / (365 * 24 * 3600), MIN_T)
        cp_now = min(max(now_min, IX.REF_MIN), HS.LAST_CP) if live_session else HS.LAST_CP
        mins_left = max(IX.SESSION_CLOSE_MIN - now_min, 0) if live_session else 0

        rows = []
        for k in strikes:
            row = {"strike": k, "ce": None, "pe": None}
            for typ in ("CE", "PE"):
                o = by.get((k, typ))
                if not o:
                    continue
                q = quotes.get(f"{o['exch']}:{o['symbol']}") or {}
                row[typ.lower()] = self._cell(o, q, spot, T, self.tape.get(o["token"]), session)
            rows.append(row)

        atm_row = next(r for r in rows if r["strike"] == atm)
        straddle = ((atm_row["ce"] or {}).get("ltp") or 0) + ((atm_row["pe"] or {}).get("ltp") or 0) or None
        ivs = [c["iv"] for c in (atm_row["ce"], atm_row["pe"]) if c and c.get("iv")]
        atm_iv = sum(ivs) / len(ivs) if ivs else None

        timeline, state_open = self._timeline(rows, spot_tape, step, dte, T, priced_at)
        feat = self._features_now(rows, spot, step, dte, cp_now, state_open, s_ohlc)
        model_walls = feat.pop("_walls", None) if feat else None
        study, prediction, analogs, probs = HS.get(), None, None, None
        if study is not None and feat is not None:
            prediction = study.predict(feat, straddle or 0)
            probs = prediction["probabilities"]
            analogs = study.analogs(feat, cp_now, dte, straddle or 0, exclude=session)
        shifts = self._shifts(timeline)
        A = AN.analyze(rows, spot, step, dte, mins_left if live_session else IX.SESSION_MINUTES,
                       T, straddle, atm_iv, probs=probs, shifts=shifts)
        reach = (lambda d, pts: study.reach_odds(feat, d, pts, straddle)) if (study is not None and feat and straddle) else None
        lot = next((o["lot_size"] for o in listed if o["lot_size"]), None)
        S = SU.build(spot=spot, step=step, zones=A["zones"], bias=A["bias"], expected_move=A["expected_move"],
                     rows=rows, mins_left=mins_left if live_session else IX.SESSION_MINUTES, dte=dte,
                     reach_odds=reach, probs=probs, lot_size=lot)
        if not live_session:
            S["warnings"].insert(0, f"Market is closed — levels are from the {session:%d %b} session; "
                                    "use them to plan the next open, not to enter now.")
            S["best"] = None
            S["stand_aside"] = ("Market is closed, so no setup is live. Re-check after 09:20 next session, once the "
                                "opening OI has settled"
                                + (" — and switch to the next expiry, this one has settled." if exp <= session else "."))
        curve = None
        if study is not None:
            curve = {"dte_bucket": HS.dte_fine(dte), "history": study.curves.get(HS.dte_fine(dte)),
                     "today": [{"cp": t["cp"], "time": t["time"], "decay": t.get("decay"), "move": t.get("ret_open_abs")}
                               for t in timeline if t.get("decay") is not None]}
        ch = float(s_ohlc.get("close") or 0)
        return {
            "status": "ok", "index": cfg["key"], "label": cfg["label"], "exchange": cfg["exch"],
            "spot": round(spot, 2), "spot_change": round(spot - ch, 2) if ch else None,
            "spot_change_pct": round((spot - ch) / ch * 100, 2) if ch else None,
            "spot_ohlc": {k: s_ohlc.get(k) for k in ("open", "high", "low", "close")},
            "vix": vix, "expiry": exp.isoformat(), "expiries": [e.isoformat() for e in exps[:8]],
            "dte": dte, "session": session.isoformat(), "live_session": live_session, "mins_left": mins_left,
            "atm": atm, "step": step, "window": window, "lot_size": lot,
            "straddle": round(straddle, 2) if straddle else None, "atm_iv": round(atm_iv, 2) if atm_iv else None,
            "fetched_at": now.strftime("%H:%M:%S"),
            "tape": {"ready": sum(1 for o in by.values() if self.tape.get(o["token"])), "total": len(by)},
            "rows": rows, "analysis": A, "setups": S, "timeline": timeline,
            "model": {"features": {k: (round(v, 4) if v is not None and np.isfinite(v) else None) for k, v in (feat or {}).items()},
                      "prediction": prediction, "analogs": analogs, "expiry_curve": curve, "walls": model_walls,
                      "history": HS.status(),
                      "note": (None if cfg["key"] == HS.UNDERLYING else
                               f"Models are trained on {HS.UNDERLYING} history. Features are scale-free, so they read "
                               f"sensibly on {cfg['key']}, but the odds are untested on this index.")},
        }

    @staticmethod
    def _cell(o: dict, q: dict, spot: float, T: float, tape: Optional[dict], session: date) -> dict:
        ltp = float(q.get("last_price") or 0)
        ohlc = q.get("ohlc") or {}
        prev_close = float(ohlc.get("close") or 0)
        oi = float(q.get("oi") or 0)
        prev_oi = tape.get("prev_oi") if tape and tape.get("session") == session else None
        bars = (tape or {}).get("bars") or []
        oi_open = next((b["oi"] for b in bars if b["cp"] >= IX.REF_MIN and b["oi"] > 0), None)
        depth = q.get("depth") or {}
        buy = [{"price": d.get("price"), "qty": d.get("quantity"), "orders": d.get("orders")} for d in depth.get("buy") or []]
        sell = [{"price": d.get("price"), "qty": d.get("quantity"), "orders": d.get("orders")} for d in depth.get("sell") or []]
        bid = next((d["price"] for d in buy if d["price"]), None)
        ask = next((d["price"] for d in sell if d["price"]), None)
        is_call = o["type"] == "CE"
        iv = implied_vol(ltp, spot, o["strike"], T, RATE, is_call) if ltp > 0 else None
        g = greeks(spot, o["strike"], T, RATE, iv, is_call) if iv else {}
        chg = ltp - prev_close if prev_close else None
        oi_chg = oi - prev_oi if prev_oi is not None else None
        vwap = float(q.get("average_price") or 0)
        intrinsic = max(0.0, (spot - o["strike"]) if is_call else (o["strike"] - spot))
        buy_qty, sell_qty = float(q.get("buy_quantity") or 0), float(q.get("sell_quantity") or 0)
        return {
            "symbol": o["symbol"], "token": o["token"], "lot_size": o["lot_size"],
            "ltp": round(ltp, 2), "prev_close": round(prev_close, 2) if prev_close else None,
            "change": round(chg, 2) if chg is not None else None,
            "change_pct": round(chg / prev_close * 100, 2) if chg is not None and prev_close else None,
            "open": ohlc.get("open"), "high": ohlc.get("high"), "low": ohlc.get("low"),
            "volume": int(q.get("volume") or 0), "vwap": round(vwap, 2) if vwap else None,
            "oi": int(oi), "prev_oi": int(prev_oi) if prev_oi is not None else None,
            "oi_chg": int(oi_chg) if oi_chg is not None else None,
            "oi_chg_pct": round(oi_chg / prev_oi * 100, 1) if oi_chg is not None and prev_oi else None,
            "oi_open": int(oi_open) if oi_open else None,
            "oi_since_open": int(oi - oi_open) if oi_open else None,
            "oi_day_high": int(q.get("oi_day_high") or 0) or None, "oi_day_low": int(q.get("oi_day_low") or 0) or None,
            "buildup": _buildup(chg, oi_chg),
            "buy_qty": int(buy_qty), "sell_qty": int(sell_qty),
            "book_imbalance": round((buy_qty - sell_qty) / (buy_qty + sell_qty), 3) if buy_qty + sell_qty else None,
            "depth": {"buy": buy, "sell": sell}, "bid": bid, "ask": ask,
            "spread": round(ask - bid, 2) if bid and ask else None,
            "spread_pct": round((ask - bid) / ltp * 100, 2) if bid and ask and ltp else None,
            "last_qty": q.get("last_quantity"),
            "last_trade_time": str(q.get("last_trade_time")) if q.get("last_trade_time") else None,
            "iv": round(iv * 100, 2) if iv else None, **g,
            "intrinsic": round(intrinsic, 2), "time_value": round(max(ltp - intrinsic, 0.0), 2),
            "writer_pnl_pct": round((vwap - ltp) / vwap * 100, 1) if vwap and ltp else None,
            "writer_pnl_prev_pct": round((prev_close - ltp) / prev_close * 100, 1) if prev_close and ltp else None,
        }

    # timeline + features ---------------------------------------------------
    def _timeline(self, rows, spot_tape, step, dte, T_now, now) -> tuple[list[dict], Optional[pd.Series]]:
        if not spot_tape or not spot_tape.get("bars"):
            return [], None
        spot_by_cp = {b["cp"]: b["close"] for b in spot_tape["bars"]}
        recs = []
        for r in rows:
            for side in ("ce", "pe"):
                c = r.get(side)
                if not c:
                    continue
                tape = self.tape.get(c["token"])
                if not tape or tape.get("session") != spot_tape.get("session"):
                    continue
                oi_open = next((b["oi"] for b in tape["bars"] if b["cp"] >= IX.REF_MIN and b["oi"] > 0), None)
                vol = 0.0
                for b in tape["bars"]:
                    vol += b["volume"]
                    if b["cp"] not in spot_by_cp or b["cp"] > HS.LAST_CP + 5:
                        continue
                    recs.append({"key": b["cp"], "cp": b["cp"], "strike": r["strike"], "typ": side.upper(),
                                 "close": b["close"], "oi": b["oi"], "oi_open": oi_open or b["oi"], "vol_cum": vol,
                                 "iv": np.nan, "spot": spot_by_cp[b["cp"]], "dte": dte,
                                 "prev_oi": tape.get("prev_oi")})
        if not recs:
            return [], None
        c = pd.DataFrame(recs)
        # IV only where features need it (ATM and 2 steps out), priced at that checkpoint's time
        atm = (c["spot"] / step).round() * step
        need = ((c["strike"] - atm) / step).round().abs().isin([0, 2])
        mins_to_now = (now.hour * 60 + now.minute) - c["cp"]
        for idx in c.index[need]:
            T = T_now + max(float(mins_to_now[idx]), 0) / (365 * 24 * 60)
            v = implied_vol(float(c.at[idx, "close"]), float(c.at[idx, "spot"]), float(c.at[idx, "strike"]),
                            T, RATE, c.at[idx, "typ"] == "CE")
            c.at[idx, "iv"] = v * 100 if v else np.nan
        st5 = FT.chain_state(c, step, window=FT.WINDOW)
        if st5.empty:
            return [], None
        session = pd.Series(0, index=st5.index)
        spot_o, strad_o = FT.opening_refs(st5, session)
        f = FT.features_from_state(st5, spot_o, strad_o)
        full = FT.chain_state(c, step, window=len(rows))
        # OI change vs previous close, over the contracts whose previous OI is known
        chg = (c.dropna(subset=["prev_oi"]).assign(d=lambda x: x["oi"] - x["prev_oi"])
               .groupby(["cp", "typ"])["d"].sum().unstack())
        out = []
        for cp in st5.index:
            s5, fu, fe = st5.loc[cp], full.loc[cp] if cp in full.index else None, f.loc[cp]
            out.append({
                "cp": int(cp), "time": IX.hhmm(cp), "spot": round(float(s5["spot"]), 2),
                "straddle": _r(s5["straddle"]), "decay": _r(fe["decay"], 4), "ret_open_abs": _r(abs(fe["ret_open"]), 4)
                if pd.notna(fe["ret_open"]) else None,
                "pcr": _r(fu["pe_oi"] / fu["ce_oi"], 3) if fu is not None and fu["ce_oi"] else None,
                "ce_oi": _r(fu["ce_oi"], 0) if fu is not None else None, "pe_oi": _r(fu["pe_oi"], 0) if fu is not None else None,
                "ce_oi_chg": _r(chg.at[cp, "CE"], 0) if cp in chg.index and "CE" in chg else None,
                "pe_oi_chg": _r(chg.at[cp, "PE"], 0) if cp in chg.index and "PE" in chg else None,
                "ce_wall": _r(fu["ce_wall"]) if fu is not None else None,
                "pe_wall": _r(fu["pe_wall"]) if fu is not None else None,
            })
        ref = st5[st5["cp"] >= IX.REF_MIN].dropna(subset=["straddle"])
        return out, (ref.iloc[0] if len(ref) else None)

    @staticmethod
    def _features_now(rows, spot, step, dte, cp_now, state_open, s_ohlc) -> Optional[dict]:
        recs = []
        for r in rows:
            for side in ("ce", "pe"):
                c = r.get(side)
                if not c or not c.get("ltp"):
                    continue
                recs.append({"key": 0, "cp": cp_now, "strike": r["strike"], "typ": side.upper(), "close": c["ltp"],
                             "oi": c["oi"], "oi_open": c["oi_open"] if c.get("oi_open") else c["oi"],
                             "vol_cum": c["volume"], "iv": c["iv"] if c.get("iv") else np.nan, "spot": spot, "dte": dte})
        if not recs:
            return None
        st = FT.chain_state(pd.DataFrame(recs), step)
        if st.empty:
            return None
        spot_open = float(state_open["spot"]) if state_open is not None else float(s_ohlc.get("open") or np.nan)
        strad_open = float(state_open["straddle"]) if state_open is not None else np.nan
        f = FT.features_from_state(st, spot_open, strad_open)
        out = {k: (float(v) if pd.notna(v) else np.nan) for k, v in f.iloc[0].items()}
        # the walls the tested models score: biggest OI within ±5 strikes, calls at/above ATM, puts at/below
        out["_walls"] = {"ce_wall": _r(st.iloc[0]["ce_wall"]), "pe_wall": _r(st.iloc[0]["pe_wall"])}
        return out

    @staticmethod
    def _shifts(timeline: list[dict]) -> list[str]:
        if len(timeline) < 4:
            return []
        first, last = timeline[0], timeline[-1]
        hour_ago = timeline[max(0, len(timeline) - 13)]
        out = []
        for key, name, up_word, dn_word in (("pe_wall", "Put wall (support)", "bullish", "bearish"),
                                            ("ce_wall", "Call wall (resistance)", "bullish", "bearish")):
            a, b, h = first.get(key), last.get(key), hour_ago.get(key)
            if a and b and a != b:
                out.append(f"{name} moved {'up' if b > a else 'down'} from {a:g} at {first['time']} to {b:g} "
                           f"({up_word if b > a else dn_word} shift)"
                           + (f"; it was {h:g} an hour ago." if h and h not in (a, b) else "."))
        return out


def _r(v, nd: int = 2):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return round(v, nd) if np.isfinite(v) else None


def _buildup(price_chg: Optional[float], oi_chg: Optional[float]) -> Optional[str]:
    if price_chg is None or oi_chg is None or oi_chg == 0:
        return None
    if price_chg >= 0 and oi_chg > 0:
        return "Long Buildup"
    if price_chg < 0 and oi_chg > 0:
        return "Short Buildup"
    if price_chg >= 0 and oi_chg < 0:
        return "Short Covering"
    return "Long Unwinding"


# ── all-index overview (one quote call, no tape) ─────────────────────
_overview_cache: dict = {}


def overview(service: OILabLive, broker) -> dict:
    hit = _overview_cache.get("v")
    if hit and time.monotonic() - hit[0] < 15:
        return hit[1]
    spots = broker.get_quote([c["spot"] for c in IX.INDICES.values()]) or {}
    keys, meta, cards = [], {}, []
    for key, cfg in IX.INDICES.items():
        spot = float((spots.get(cfg["spot"]) or {}).get("last_price") or 0)
        if spot <= 0:
            continue
        try:
            opts = service._options(broker, key)
        except Exception:
            continue
        today = IX.now_ist().date()
        exps = sorted({o["expiry"] for o in opts if o["expiry"] >= today})
        if not exps:
            continue
        listed = [o for o in opts if o["expiry"] == exps[0]]
        strikes = sorted({o["strike"] for o in listed})
        atm = min(strikes, key=lambda s: abs(s - spot))
        i = strikes.index(atm)
        near = set(strikes[max(0, i - 5): i + 6])
        for o in listed:
            if o["strike"] in near:
                k = f"{o['exch']}:{o['symbol']}"
                keys.append(k)
                meta[k] = (key, o)
        ohlc = (spots.get(cfg["spot"]) or {}).get("ohlc") or {}
        cards.append({"index": key, "label": cfg["label"], "spot": round(spot, 2), "atm": atm,
                      "expiry": exps[0].isoformat(), "dte": (exps[0] - today).days,
                      "change_pct": round((spot - ohlc["close"]) / ohlc["close"] * 100, 2) if ohlc.get("close") else None})
    quotes = broker.get_quote(keys) if keys else {}
    for card in cards:
        rows: dict[float, dict] = {}
        for k, (idx, o) in meta.items():
            if idx != card["index"]:
                continue
            q = (quotes or {}).get(k) or {}
            rows.setdefault(o["strike"], {"strike": o["strike"], "ce": None, "pe": None})[o["type"].lower()] = {
                "oi": float(q.get("oi") or 0), "ltp": float(q.get("last_price") or 0)}
        rr = [rows[s] for s in sorted(rows)]
        ce_oi = sum((r["ce"] or {}).get("oi", 0) for r in rr)
        pe_oi = sum((r["pe"] or {}).get("oi", 0) for r in rr)
        above = [r for r in rr if r["strike"] >= card["atm"] and r["ce"]]
        below = [r for r in rr if r["strike"] <= card["atm"] and r["pe"]]
        atm_row = next((r for r in rr if r["strike"] == card["atm"]), None)
        card.update({
            "pcr": round(pe_oi / ce_oi, 2) if ce_oi else None,
            "resistance": max(above, key=lambda r: r["ce"]["oi"])["strike"] if above else None,
            "support": max(below, key=lambda r: r["pe"]["oi"])["strike"] if below else None,
            "max_pain": AN.max_pain(rr),
            "straddle": round((atm_row["ce"] or {}).get("ltp", 0) + (atm_row["pe"] or {}).get("ltp", 0), 2) if atm_row else None,
        })
        p = card["pcr"]
        card["tilt"] = None if p is None else "Put writers heavier" if p >= 1.15 else "Call writers heavier" if p <= 0.85 else "Balanced"
    out = {"status": "ok", "indices": cards, "fetched_at": IX.now_ist().strftime("%H:%M:%S")}
    _overview_cache["v"] = (time.monotonic(), out)
    return out


def contract_series(service: OILabLive, token: int) -> dict:
    tape = service.tape.get(int(token))
    if not tape:
        return {"status": "pending", "bars": []}
    return {"status": "ok", "session": tape["session"].isoformat() if tape.get("session") else None,
            "prev_oi": tape.get("prev_oi"), "prev_close": tape.get("prev_close"),
            "bars": [{"time": IX.hhmm(b["cp"]), **b} for b in tape["bars"]]}


SERVICE = OILabLive()
