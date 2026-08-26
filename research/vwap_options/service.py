"""
VWAP Options Engine — orchestration (read-only market data; paper by default).

Fetches the underlying path (NIFTY futures or index) at the chosen timeframe,
builds every VWAP line, finds rule hits, resolves the ATM-relative contract,
prices it (REAL → MODELLED) and simulates the trade. The same pieces power the
chart, the single-day simulate and the date-range backtest, so the three cannot
disagree.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

from core.broker import Broker
from core.logger import get_logger
from research.prev_period_vwap import _candle_dt
from research.vwap_options import signals as sig_mod
from research.vwap_options import simulate as sim_mod
from research.vwap_options.chain import NiftyChain, ladder, offset_for, strike_for_offset
from research.vwap_options.config import ROLLING_DAYS, VWAP_LINES, sanitize
from research.vwap_options.pricing import PremiumSource
from research.vwap_options.vwap_engine import build_series

logger = get_logger("research.vwap_options")

INDEX_SYMBOL = "NIFTY 50"
INDEX_NAME = "NIFTY"
MKT_OPEN = dtime(9, 15)
MKT_CLOSE = dtime(15, 30)
LOT_SIZE_FALLBACK = 75

# Kite per-request history limits (days) by interval — used to chunk fetches.
_CHUNK_DAYS = {"minute": 55, "3minute": 90, "5minute": 90, "15minute": 180,
               "30minute": 180, "60minute": 180, "day": 1800}


class VwapOptionsService:
    def __init__(self, broker: Broker, user_id: Optional[int] = None):
        self.broker = broker
        self.user_id = user_id
        self.chain = NiftyChain(broker)
        self.pricing = PremiumSource(broker)
        self._lock = threading.Lock()
        self._index_token: Optional[int] = None
        self._fut_cache: dict = {}
        self._bar_cache: dict = {}

    # ── instrument resolution ──
    def index_token(self) -> Optional[int]:
        if self._index_token:
            return self._index_token
        try:
            for inst in self.broker.get_instruments("NSE") or []:
                if inst.get("tradingsymbol") == INDEX_SYMBOL:
                    self._index_token = int(inst["instrument_token"])
                    return self._index_token
        except Exception as exc:
            logger.error("index token lookup failed: %s", exc)
        return None

    def futures_contracts(self) -> list[dict]:
        """Listed NIFTY futures, nearest expiry first (used for the volume path)."""
        if self._fut_cache.get("day") == date.today():
            return self._fut_cache["rows"]
        rows = []
        try:
            for inst in self.broker.get_instruments("NFO") or []:
                if inst.get("name") == INDEX_NAME and inst.get("instrument_type") == "FUT":
                    exp = inst.get("expiry")
                    if isinstance(exp, datetime):
                        exp = exp.date()
                    elif isinstance(exp, str):
                        exp = date.fromisoformat(exp[:10])
                    if exp:
                        rows.append({"token": int(inst["instrument_token"]),
                                     "tradingsymbol": inst.get("tradingsymbol"), "expiry": exp})
        except Exception as exc:
            logger.error("futures lookup failed: %s", exc)
        rows.sort(key=lambda r: r["expiry"])
        self._fut_cache = {"day": date.today(), "rows": rows}
        return rows

    def lot_size(self) -> int:
        try:
            for inst in self.broker.get_instruments("NFO") or []:
                if inst.get("name") == INDEX_NAME and inst.get("instrument_type") in ("CE", "PE"):
                    return int(inst.get("lot_size") or LOT_SIZE_FALLBACK)
        except Exception:
            pass
        return LOT_SIZE_FALLBACK

    # ── candle fetch (chunked, cached) ──
    def _fetch(self, token: int, frm: datetime, to: datetime, tf: str) -> list[dict]:
        key = (token, frm, to, tf)
        if key in self._bar_cache:
            return self._bar_cache[key]
        span = max(1, _CHUNK_DAYS.get(tf, 90))
        rows: list[dict] = []
        cur = frm
        while cur < to:
            nxt = min(to, cur + timedelta(days=span))
            try:
                rows.extend(self.broker.get_historical_data(token, cur, nxt, tf) or [])
            except Exception as exc:
                logger.debug("history chunk failed (%s %s→%s): %s", token, cur, nxt, exc)
            cur = nxt
        for r in rows:
            r["_dt"] = _candle_dt(r)
        rows = [r for r in rows if r.get("_dt")]
        rows.sort(key=lambda r: r["_dt"])
        # de-duplicate overlapping chunk boundaries
        seen, out = set(), []
        for r in rows:
            if r["_dt"] in seen:
                continue
            seen.add(r["_dt"])
            out.append(r)
        self._bar_cache[key] = out
        return out

    def _warmup_days(self, cfg: dict) -> int:
        """Calendar days of history needed before the window so every enabled
        VWAP line is warm (rolling lines need N completed sessions)."""
        need = 35                                    # covers prev-day / week / month
        for r in cfg.get("rules") or []:
            n = ROLLING_DAYS.get(r.get("line"))
            if r.get("enabled") and n:
                need = max(need, int(n * 1.5) + 10)  # trading→calendar days
        return need

    def underlying_bars(self, start: date, end: date, cfg: dict) -> tuple[list[dict], dict]:
        """(bars, meta). Futures path (real volume) or index path (HLC3 average)."""
        tf = cfg["timeframe"]
        warm = self._warmup_days(cfg)
        frm = datetime.combine(start - timedelta(days=warm), MKT_OPEN)
        to = min(datetime.combine(end, MKT_CLOSE), datetime.now())
        if cfg.get("vwap_source") == "index":
            tok = self.index_token()
            if not tok:
                return [], {"source": "index", "error": "NIFTY 50 index token not resolvable"}
            bars = self._fetch(tok, frm, to, tf)
            return bars, {"source": "index", "volume": False,
                          "note": "Index has no traded volume — this line is an HLC3 average price, not a true VWAP."}
        # futures: stitch the nearest non-expired contract across the window
        futs = self.futures_contracts()
        if not futs:
            return [], {"source": "futures", "error": "no NIFTY futures listed"}
        bars: list[dict] = []
        rolls = []
        cursor = frm
        end_day = to.date()
        for f in futs:
            if f["expiry"] < cursor.date():          # contract already expired — skip
                continue
            f_end = min(to, datetime.combine(f["expiry"], MKT_CLOSE))
            if f_end <= cursor:
                continue
            part = [b for b in self._fetch(f["token"], cursor, f_end, tf) if b["_dt"] >= cursor]
            if part:
                if bars:
                    rolls.append({"at": part[0]["_dt"].isoformat(), "into": f["tradingsymbol"]})
                bars.extend(part)
                cursor = part[-1]["_dt"] + timedelta(minutes=1)
            # Roll ONLY when this contract has actually expired inside the window.
            # Breaking on "data hasn't reached `to`" would splice the NEXT month's
            # bars (which trade at a different price) onto the front contract
            # intraday, because the latest candle always lags the clock.
            if f["expiry"] >= end_day:
                break
        return bars, {"source": "futures", "volume": True, "rolls": rolls,
                      "note": "True volume-weighted VWAP from NIFTY futures; contract rolls are stitched."}

    def index_map(self, start: date, end: date, cfg: dict) -> dict:
        """{bar datetime -> NIFTY index close} for the window.

        NIFTY options are struck on the INDEX, but in futures mode the signal
        bars are futures candles which carry a basis (premium/discount) to spot.
        Deriving ATM from the futures price would pick a strike 1-2 steps off, so
        the strike is always anchored to the index close at the same timestamp.
        """
        tok = self.index_token()
        if not tok:
            return {}
        frm = datetime.combine(start, MKT_OPEN)
        to = min(datetime.combine(end, MKT_CLOSE), datetime.now())
        return {b["_dt"]: float(b["close"]) for b in self._fetch(tok, frm, to, cfg["timeframe"])}

    @staticmethod
    def audit_bars(bars: list[dict], tf: str) -> dict:
        """Flag suspicious candles instead of silently plotting them.

        Catches the three things that actually go wrong with stitched/vendor
        intraday data: timestamps that do not sit on the timeframe grid, repeated
        timestamps, and price jumps far larger than the session's own range
        (the signature of another contract's bars being spliced in).
        """
        mins = {"minute": 1, "3minute": 3, "5minute": 5, "15minute": 15,
                "30minute": 30, "60minute": 60}.get(tf)
        warnings, off_grid, dupes, jumps = [], [], [], []
        seen = set()
        closes = [float(b["close"]) for b in bars if b.get("close")]
        med = sorted(closes)[len(closes) // 2] if closes else 0
        for i, b in enumerate(bars):
            dt = b.get("_dt")
            if dt is None:
                continue
            if mins and ((dt.hour * 60 + dt.minute) % mins) != 0:
                off_grid.append(dt.strftime("%H:%M"))
            if dt in seen:
                dupes.append(dt.strftime("%H:%M"))
            seen.add(dt)
            if med and abs(float(b["close"]) - med) / med > 0.02:      # >2% from the day's median
                jumps.append({"t": dt.strftime("%H:%M"), "close": round(float(b["close"]), 2)})
        if off_grid:
            warnings.append(f"{len(off_grid)} candle(s) not aligned to the {tf} grid "
                            f"({', '.join(off_grid[:5])}) — likely a partial or mis-stamped bar.")
        if dupes:
            warnings.append(f"{len(dupes)} duplicate timestamp(s) ({', '.join(dupes[:5])}).")
        if jumps:
            # build the sample outside the f-string: nested same-quote f-strings
            # are Python 3.12+ only (PEP 701) and the deploy image runs 3.11
            sample = ", ".join("{}@{}".format(j["t"], j["close"]) for j in jumps[:5])
            warnings.append(f"{len(jumps)} candle(s) >2% away from the session median "
                            f"({sample}) — check for a spliced contract or a bad tick.")
        return {"ok": not warnings, "warnings": warnings,
                "off_grid": off_grid[:20], "duplicates": dupes[:20], "outliers": jumps[:20]}

    # ── chart payload (candles + every VWAP line + rule hits) ──
    def chart(self, cfg: dict, day: Optional[str] = None) -> dict:
        with self._lock:
            cfg = sanitize(cfg)
            d = date.fromisoformat(day) if day else self._latest_session()
            bars, meta = self.underlying_bars(d, d, cfg)
            if not bars:
                return {"status": "error", "message": meta.get("error") or f"No candles for {d}",
                        "meta": meta}
            series = build_series(bars, cfg["vwap_source"])
            day_idx = [i for i, b in enumerate(bars) if b["_dt"].date() == d]
            if not day_idx:
                return {"status": "error", "message": f"No candles on {d} (market holiday?)", "meta": meta}
            lo, hi = day_idx[0], day_idx[-1] + 1
            hits = sig_mod.find_signals(bars, series, cfg, day=d)

            # Companion INDEX panel: options are struck on the index, so when the
            # signal path is futures we also return the index candles (with their
            # own HLC3 series) and the live basis between the two.
            idx_candles, idx_series, basis = [], [], None
            if cfg.get("vwap_source") == "futures":
                tok = self.index_token()
                if tok:
                    # Fetch the SAME warmup window as the futures path, otherwise
                    # previous-day / week / month and rolling lines have no prior
                    # period to accumulate from and would render empty.
                    warm = self._warmup_days(cfg)
                    all_ib = self._fetch(tok, datetime.combine(d - timedelta(days=warm), MKT_OPEN),
                                         min(datetime.combine(d, MKT_CLOSE), datetime.now()),
                                         cfg["timeframe"])
                    full_idx = build_series(all_ib, "index") if all_ib else []
                    iday = [i for i, b in enumerate(all_ib) if b["_dt"].date() == d]
                    if iday:
                        ilo, ihi = iday[0], iday[-1] + 1
                        idx_series = full_idx[ilo:ihi]
                        idx_candles = [{"t": b["_dt"].strftime("%H:%M"),
                                        "open": round(float(b["open"]), 2), "high": round(float(b["high"]), 2),
                                        "low": round(float(b["low"]), 2), "close": round(float(b["close"]), 2),
                                        "volume": 0} for b in all_ib[ilo:ihi]]
                        basis = round(float(bars[hi - 1]["close"]) - float(all_ib[ihi - 1]["close"]), 2)

            return {
                "status": "ok", "date": d.isoformat(), "meta": meta,
                "quality": self.audit_bars(bars[lo:hi], cfg["timeframe"]),
                "index_candles": idx_candles, "index_series": idx_series, "basis": basis,
                "timeframe": cfg["timeframe"], "lines": VWAP_LINES,
                "candles": [{"t": b["_dt"].strftime("%H:%M"), "dt": b["_dt"].isoformat(),
                             "open": round(float(b["open"]), 2), "high": round(float(b["high"]), 2),
                             "low": round(float(b["low"]), 2), "close": round(float(b["close"]), 2),
                             "volume": int(float(b.get("volume", 0) or 0))} for b in bars[lo:hi]],
                "series": series[lo:hi],
                "signals": [{**s, "dt": s["dt"].isoformat(), "date": s["date"].isoformat()} for s in hits],
            }

    # ── strike ladder / expiries for the UI ──
    def ladder(self, cfg: dict) -> dict:
        cfg = sanitize(cfg)
        spot = self.chain.spot()
        if not spot:
            return {"status": "error", "message": "Spot price unavailable"}
        exp = self.chain.expiry_for(cfg["expiry_type"], date.today(), cfg["min_days_to_expiry"])
        return {"status": "ok", "spot": round(spot, 2), "lot_size": self.lot_size(),
                "expiry": exp.isoformat() if exp else None,
                "expiries": [e.isoformat() for e in self.chain.expiries()[:12]],
                "strikes": ladder(spot)}

    # ── one signal → a fully simulated trade row ──
    def _trade_for_signal(self, s: dict, bars: list[dict], cfg: dict,
                          start: date, end: date, qty: int,
                          idx_map: Optional[dict] = None) -> tuple[Optional[dict], Optional[dict]]:
        """(trade, skip). Exactly one of the two is populated."""
        d = s["date"]
        signal_px = s["index_price"]                     # price on the VWAP source path
        # strike is anchored to the INDEX, not the futures price (basis would skew it)
        spot = (idx_map or {}).get(s["dt"]) or signal_px
        basis = round(signal_px - spot, 2)
        offset = offset_for(cfg, s["opt_type"])          # moneyness-aware in auto mode
        contract, reason = self.chain.resolve(spot, offset, s["opt_type"],
                                              cfg["expiry_type"], d, cfg["min_days_to_expiry"])
        if not contract:
            contract = self.chain.synthetic_contract(spot, offset, s["opt_type"],
                                                     cfg["expiry_type"], d, cfg["min_days_to_expiry"])
            if not contract:
                return None, {**_sig_brief(s), "reason": reason or "contract could not be derived"}

        day_index = [b for b in bars if b["_dt"].date() == d]
        prem, source, note = self.pricing.bars_for(contract, day_index, d, cfg, start, end)
        if not prem or source == "NONE":
            return None, {**_sig_brief(s), "strike": contract.get("strike"),
                          "reason": note or reason or "no premium data"}

        fill = sim_mod.pick_entry(prem, s["dt"], cfg)
        if not fill:
            return None, {**_sig_brief(s), "strike": contract.get("strike"),
                          "reason": "no bar available to fill on (signal on the last bar)"}

        expiry_reached = contract["expiry"] < date.today()
        res = sim_mod.simulate_trade(prem, fill["i"], fill["price"], qty, cfg,
                                     expiry_reached=expiry_reached, index_bars=day_index,
                                     index_entry=spot)
        return {
            "date": d.isoformat(), "signal_time": s["time"], "rule": s["rule"],
            "line": s["line"], "event": s["event"], "action": s["action"],
            "index_price": round(spot, 2), "signal_price": signal_px,
            "basis": basis, "spot_source": "index" if (idx_map or {}).get(s["dt"]) else "signal-path",
            "vwap_level": s["level"],
            "opt_type": s["opt_type"], "offset_steps": offset,
            "strike_mode": cfg.get("strike_mode", "fixed"),
            "strike": contract["strike"], "symbol": contract["tradingsymbol"],
            "expiry": contract["expiry"].isoformat(), "premium_source": source,
            "source_note": note, "fill_basis": fill["basis"],
            "entry_time": fill["dt"].strftime("%H:%M"), "qty": qty,
            "entry": res["entry"], "target": res["target"], "sl": res["sl"],
            "exit": res["exit"], "exit_time": res["exit_dt"].strftime("%H:%M") if res["exit_dt"] else None,
            "exit_reason": res["exit_reason"], "mtm": res["mtm"], "gross_mtm": res["gross_mtm"],
            "cost": res["cost"], "mfe": res["mfe"], "mae": res["mae"], "open": res["open"],
        }, None

    # ── backtest over a date or range ──
    def backtest(self, cfg: dict, start: Optional[str] = None, end: Optional[str] = None) -> dict:
        with self._lock:
            cfg = sanitize(cfg)
            s = date.fromisoformat(start) if start else self._latest_session()
            e = date.fromisoformat(end) if end else s
            if e < s:
                s, e = e, s
            bars, meta = self.underlying_bars(s, e, cfg)
            if not bars:
                return {"status": "error", "message": meta.get("error") or "No underlying candles",
                        "meta": meta}
            series = build_series(bars, cfg["vwap_source"])
            qty = self.lot_size() * int(cfg["lots"])

            idx_map = self.index_map(s, e, cfg) if cfg.get("vwap_source") == "futures" else {}
            days = sorted({b["_dt"].date() for b in bars if s <= b["_dt"].date() <= e})
            trades, skips = [], []
            for d in days:
                for sg in sig_mod.find_signals(bars, series, cfg, day=d):
                    try:
                        t, skip = self._trade_for_signal(sg, bars, cfg, s, e, qty, idx_map)
                    except Exception as exc:
                        logger.debug("trade sim failed %s: %s", d, exc)
                        t, skip = None, {**_sig_brief(sg), "reason": f"error: {exc}"}
                    (trades if t else skips).append(t or skip)
            trades.sort(key=lambda t: (t["date"], t["signal_time"]))
            return {
                "status": "ok", "start": s.isoformat(), "end": e.isoformat(),
                "sessions": len(days), "meta": meta, "config": cfg, "lot_size": self.lot_size(),
                "trades": trades, "skipped": skips,
                "stats": _stats(trades), "by_source": {
                    "REAL": _stats([t for t in trades if t["premium_source"] == "REAL"]),
                    "MODELLED": _stats([t for t in trades if t["premium_source"] == "MODELLED"]),
                },
                "equity_curve": _equity(trades),
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }

    # ── helpers ──
    def _latest_session(self) -> date:
        d = date.today()
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d


def _sig_brief(s: dict) -> dict:
    return {"date": s["date"].isoformat() if hasattr(s["date"], "isoformat") else s["date"],
            "signal_time": s["time"], "rule": s["rule"], "opt_type": s["opt_type"],
            "index_price": s["index_price"], "vwap_level": s["level"]}


def _stats(trades: list[dict]) -> dict:
    closed = [t for t in trades if not t.get("open")]
    n = len(closed)
    if not n:
        return {"trades": 0, "wins": 0, "win_rate": 0.0, "total_mtm": 0.0, "avg": 0.0,
                "profit_factor": 0.0, "expectancy": 0.0, "best": 0.0, "worst": 0.0,
                "max_drawdown": 0.0, "total_cost": 0.0}
    m = [t["mtm"] for t in closed]
    wins = [x for x in m if x > 0]
    losses = [x for x in m if x < 0]
    gw, gl = sum(wins), -sum(losses)
    wr = len(wins) / n
    eq = peak = dd = 0.0
    for x in m:
        eq += x
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return {"trades": n, "wins": len(wins), "win_rate": round(wr * 100, 1),
            "total_mtm": round(sum(m), 2), "avg": round(sum(m) / n, 2),
            "profit_factor": round(gw / gl, 2) if gl else (999.0 if gw else 0.0),
            "expectancy": round(wr * (gw / len(wins) if wins else 0)
                                - (1 - wr) * (gl / len(losses) if losses else 0), 2),
            "best": round(max(m), 2), "worst": round(min(m), 2),
            "max_drawdown": round(dd, 2),
            "total_cost": round(sum(t.get("cost") or 0 for t in closed), 2)}


def _equity(trades: list[dict]) -> list[dict]:
    eq = 0.0
    out = []
    for t in trades:
        if t.get("open"):
            continue
        eq += t["mtm"]
        out.append({"t": f"{t['date']} {t.get('exit_time') or t['signal_time']}",
                    "equity": round(eq, 2), "mtm": t["mtm"]})
    return out
