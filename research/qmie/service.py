"""
QMIE service — read-only scan orchestration → ranked snapshot (§8, §11, §12).

Reuses ONLY the existing read-only Broker data methods (historical bars,
instrument dump via the shared Universe adapter). It never imports, references,
or calls any order/execution path. Output is an immutable-style ranked snapshot
with as-of time, provenance and per-candidate evidence.
"""
from __future__ import annotations

import bisect
import hashlib
import threading
from datetime import date, datetime, time as dtime, timedelta
from typing import Optional

from core.broker import Broker
from core.logger import get_logger
from research.pmvwap_straddle.universe import Universe   # read-only instrument adapter
from research.option_chain import OptionChain            # read-only chain (PCR/max-pain)
from research.qmie import ranking
from research.qmie import engines as eng
from research.qmie import backtest as bt
from research.qmie.config import load_config, save_config, sanitize, liquidity_floor, min_rr
from research.qmie.constants import (
    HORIZONS, BENCHMARK_SYMBOL, RULESET_VERSION, SCHEMA_VERSION, DISCLAIMER,
    STATE_ELIGIBLE, STATE_WARNING, STATE_RESTRICTED, STATE_UNAVAILABLE,
)

logger = get_logger("research.qmie")

_RANKABLE = (STATE_ELIGIBLE, STATE_WARNING)


class QMIEEngine:
    def __init__(self, broker: Broker, user_id: Optional[int] = None):
        self.broker = broker
        self.user_id = user_id
        self.universe = Universe(broker)
        self._chain = OptionChain(broker)          # read-only option-chain adapter
        self._lock = threading.Lock()
        self._bench_token: Optional[int] = None
        self._bench_day: Optional[date] = None

    # ── config passthrough ──
    def load_config(self) -> dict:
        return load_config()

    def save_config(self, partial: dict) -> dict:
        return save_config(partial)

    # ── helpers ──
    def _benchmark_token(self) -> Optional[int]:
        today = date.today()
        if self._bench_token and self._bench_day == today:
            return self._bench_token
        try:
            for inst in self.broker.get_instruments("NSE"):
                if (inst.get("tradingsymbol") or "").upper() == BENCHMARK_SYMBOL:
                    self._bench_token = int(inst["instrument_token"])
                    self._bench_day = today
                    return self._bench_token
        except Exception as exc:
            logger.error("QMIE benchmark token lookup failed: %s", exc)
        return None

    def _bars(self, token: int, profile: dict, days: Optional[int] = None) -> list[dict]:
        to = datetime.now()
        frm = to - timedelta(days=days or profile["history_days"])
        try:
            return self.broker.get_historical_data(int(token), frm, to, profile["interval"]) or []
        except Exception as exc:
            logger.debug("QMIE bars failed for %s: %s", token, exc)
            return []

    def _resolve_universe(self, cfg: dict, symbols: Optional[list[str]]) -> list[str]:
        if symbols:
            syms = symbols
        elif cfg["universe"] == "custom":
            syms = cfg["custom_symbols"]
        else:                                   # fno
            syms = [e["name"] for e in self.universe.equities()]
        # de-dupe, keep order, cap
        seen, out = set(), []
        for s in syms:
            s = (s or "").strip().upper()
            if s and s not in seen:
                seen.add(s); out.append(s)
        return out[: cfg["max_instruments"]]

    def _market_context(self, all_bars: list[list[dict]], now) -> dict:
        breadth = eng.breadth_engine(all_bars)
        pcr = max_pain = spot = None
        chain_bias = "neutral"
        try:
            snap = self._chain.snapshot(expiry_type="weekly", count=10)
            if snap.get("status") == "ok":
                pcr = snap.get("pcr")
                max_pain = snap.get("max_pain")
                spot = snap.get("spot")
                if pcr is not None:
                    chain_bias = "bullish" if pcr >= 1.15 else ("bearish" if pcr <= 0.85 else "neutral")
        except Exception as exc:
            logger.debug("QMIE market OI/PCR unavailable: %s", exc)
        # Composite market bias: breadth is primary; PCR is a tie-breaker only.
        bias = breadth.get("bias", "neutral")
        if bias == "neutral" and chain_bias != "neutral":
            bias = chain_bias
        return {"breadth": breadth, "pcr": pcr, "max_pain": max_pain, "nifty_spot": spot,
                "chain_bias": chain_bias, "bias": bias, "regime": breadth.get("regime", "unavailable")}

    # ── scan ──
    def scan(self, overrides: Optional[dict] = None, symbols: Optional[list[str]] = None) -> dict:
        with self._lock:
            cfg = sanitize({**self.load_config(), **(overrides or {})})
            profile = HORIZONS[cfg["horizon"]]
            now = datetime.now()
            floor = liquidity_floor(cfg)
            mrr = min_rr(cfg)

            bench_token = self._benchmark_token()
            bench = self._bars(bench_token, profile) if bench_token else []
            if len(bench) < 10:
                return {"status": "error", "message": "Benchmark (NIFTY 50) history unavailable — is Zerodha connected?"}

            uni = self._resolve_universe(cfg, symbols)
            if not uni:
                return {"status": "error", "message": "Empty universe — add symbols or pick F&O."}

            # ── Pass 1: fetch bars once (read-only) into memory ──
            fetched: list[tuple] = []      # (sym, exch, token, bars)
            unavailable = 0
            for sym in uni:
                token, exch = self.universe.resolve_equity_token(sym)
                if not token:
                    unavailable += 1
                    continue
                bars = self._bars(token, profile)
                if len(bars) < 10:
                    unavailable += 1
                    continue
                fetched.append((sym, exch, token, bars))

            # ── Market context: breadth (from the universe) + NIFTY OI/PCR ──
            market_context = self._market_context([f[3] for f in fetched], now)

            # ── Pass 2: evaluate each candidate with market context ──
            results = []
            for sym, exch, token, bars in fetched:
                try:
                    cand = ranking.evaluate(sym, exch, token, bars, bench, cfg, profile,
                                            floor, mrr, now, mctx=market_context)
                    results.append(cand)
                except Exception as exc:                 # one bad instrument never blocks others
                    logger.debug("QMIE evaluate failed for %s: %s", sym, exc)
                    unavailable += 1

            # ── rank the eligible + warning candidates deterministically ──
            rankable = [r for r in results if r.get("state") in _RANKABLE]
            rankable.sort(key=lambda r: (
                -float(r.get("score", 0)), -float(r.get("confidence", 0)),
                -float(r.get("median_value") or 0), -float(r.get("rel_strength_excess") or 0),
                r["symbol"]))
            for i, r in enumerate(rankable, 1):
                r["rank"] = i

            restricted = [r for r in results if r.get("state") == STATE_RESTRICTED]
            unavail = [r for r in results if r.get("state") == STATE_UNAVAILABLE]

            sig = f"{now:%Y%m%d%H%M}|{cfg['horizon']}|{cfg['config_version']}|{len(uni)}"
            snapshot_id = "qmie-" + hashlib.sha1(sig.encode()).hexdigest()[:12]

            return {
                "status": "ok", "snapshot_id": snapshot_id, "as_of": now.isoformat(),
                "as_of_display": now.strftime("%Y-%m-%d %H:%M:%S"),
                "horizon": cfg["horizon"], "config": cfg,
                "ruleset_version": RULESET_VERSION, "schema_version": SCHEMA_VERSION,
                "benchmark": BENCHMARK_SYMBOL, "disclaimer": DISCLAIMER,
                "counts": {"scanned": len(uni), "eligible": sum(1 for r in rankable if r["state"] == STATE_ELIGIBLE),
                           "warning": sum(1 for r in rankable if r["state"] == STATE_WARNING),
                           "restricted": len(restricted), "unavailable": len(unavail) + unavailable},
                "market_context": market_context,
                "results": rankable,
                "restricted": restricted[:50],
            }

    # ── leakage-safe backtest + calibration (§35/§46) ──
    def backtest(self, overrides: Optional[dict] = None, symbols: Optional[list[str]] = None) -> dict:
        with self._lock:
            cfg = sanitize({**self.load_config(), **(overrides or {})})
            profile = HORIZONS[cfg["horizon"]]
            btp = bt.BT_PROFILE[cfg["horizon"]]
            max_hold, cadence = btp["max_hold"], btp["cadence"]
            floor, mrr = liquidity_floor(cfg), min_rr(cfg)
            bt_days = min(profile["history_days"] * 2 + 200, 2000)

            bench_token = self._benchmark_token()
            bench = self._bars(bench_token, profile, days=bt_days) if bench_token else []
            if len(bench) < profile["min_bars"]:
                return {"status": "error", "message": "Benchmark history insufficient for backtest"}
            bench_dates = [ranking._bar_date(b) for b in bench]

            # cap the universe for a manual research backtest (compute bound)
            uni = self._resolve_universe(cfg, symbols)[: min(cfg["max_instruments"], 25)]
            records: list[dict] = []
            tested = 0
            for sym in uni:
                token, exch = self.universe.resolve_equity_token(sym)
                if not token:
                    continue
                bars = self._bars(token, profile, days=bt_days)
                dates = [ranking._bar_date(b) for b in bars]
                start = profile["min_bars"]
                end = len(bars) - max_hold - 1
                if end <= start:
                    continue
                tested += 1
                decisions = 0
                i = start
                while i <= end and decisions < bt.MAX_DECISIONS_PER_INSTRUMENT:
                    d = dates[i]
                    if not d:
                        i += cadence; continue
                    k = bisect.bisect_right(bench_dates, d)
                    bench_sub = bench[:k]
                    if len(bench_sub) < 10:
                        i += cadence; continue
                    asof = datetime.combine(d, dtime(15, 30))
                    try:
                        cand = ranking.evaluate(sym, exch, token, bars[:i + 1], bench_sub,
                                                cfg, profile, floor, mrr, asof, mctx=None)
                    except Exception:
                        i += cadence; continue
                    if cand.get("state") in _RANKABLE and cand.get("direction") in ("long", "short"):
                        out = bt.simulate_outcome(cand["direction"], cand["indicative_entry"],
                                                  cand["first_target"], cand["invalidation"],
                                                  bars[i + 1:i + 1 + max_hold])
                        records.append({"symbol": sym, "date": d.isoformat(),
                                        "direction": cand["direction"], "score": cand["score"],
                                        "band": cand["band"], "confidence": cand["confidence"], **out})
                        decisions += 1
                    i += cadence

            report = bt.aggregate(records)
            return {
                "status": "ok", "horizon": cfg["horizon"], "benchmark": BENCHMARK_SYMBOL,
                "ruleset_version": RULESET_VERSION, "instruments_tested": tested,
                "window_bars": max_hold, "cadence": cadence, "disclaimer": DISCLAIMER,
                "report": report, "sample": records[-200:],
                "note": ("Leakage-safe point-in-time: each decision uses only prior bars; "
                         "outcomes use only forward bars. Same-bar target+stop = conservative loss. "
                         "Simulated research outcomes — no fills, costs, or orders."),
            }
