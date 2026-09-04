"""
Wyckoff Method — the three desks.

  • ``analyze_equity``  — a cash stock on any timeframe.
  • ``analyze_options`` — the NIFTY (or any F&O) index, read structurally, then
    translated into a CALL / PUT stance. Optionally a second read on an actual
    option premium, shown *beside* the index read: a premium series is decaying,
    thinly traded and one-sided, so it confirms — it never leads.
  • ``analyze_fno``     — a stock plus its front-month future, where open
    interest turns Wyckoff's effort-vs-result law into a directly measurable
    thing (long build-up vs short covering).

Read-only. Fetches candles, runs ``core.analyze``, returns the read. Never
places an order, never touches strategy state.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from typing import Optional

from core.broker import Broker
from core.logger import get_logger
from research.pmvwap_straddle.universe import Universe
from research.wyckoff import core
from research.wyckoff.config import TF_HISTORY_DAYS, load_config, save_config, sanitize

logger = get_logger("research.wyckoff")

MARKET_OPEN = datetime.min.time().replace(hour=9, minute=15)
MARKET_CLOSE = datetime.min.time().replace(hour=15, minute=30)

INDEX_SPOT = {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK",
              "FINNIFTY": "NIFTY FIN SERVICE", "MIDCPNIFTY": "NIFTY MID SELECT"}
STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50, "MIDCPNIFTY": 25}


class WyckoffService:
    def __init__(self, broker: Broker, user_id: Optional[int] = None):
        self.broker = broker
        self.user_id = user_id
        self.universe = Universe(broker)
        self._lock = threading.Lock()
        self._index_tokens: dict = {}

    def load_config(self):
        return load_config()

    def save_config(self, partial):
        return save_config(partial)

    # ── data ─────────────────────────────────────────────────────────
    def _candles(self, token: int, tf: str, *, oi: bool = False, days: Optional[int] = None) -> list[dict]:
        d = days or TF_HISTORY_DAYS.get(tf, 200)
        frm = datetime.combine(date.today() - timedelta(days=d), MARKET_OPEN)
        to = min(datetime.combine(date.today(), MARKET_CLOSE), datetime.now())
        try:
            raw = self.broker.get_historical_data(token, frm, to, tf, oi=oi) or []
        except TypeError:                       # brokers without the oi kwarg
            raw = self.broker.get_historical_data(token, frm, to, tf) or []
        except Exception as exc:
            logger.debug("wyckoff candles failed (%s %s): %s", token, tf, exc)
            return []
        out = []
        for c in raw:
            dt = core._dt(c)
            if dt is None:
                continue
            c["_dt"] = dt
            out.append(c)
        out.sort(key=lambda c: c["_dt"])
        return out

    def _index_token(self, spot_symbol: str) -> Optional[int]:
        if spot_symbol in self._index_tokens:
            return self._index_tokens[spot_symbol]
        tok = None
        try:
            for inst in self.broker.get_instruments("NSE") or []:
                if inst.get("tradingsymbol") == spot_symbol or inst.get("name") == spot_symbol:
                    tok = int(inst["instrument_token"])
                    break
        except Exception as exc:
            logger.debug("wyckoff index token failed: %s", exc)
        self._index_tokens[spot_symbol] = tok
        return tok

    def _ltp(self, key: str) -> Optional[float]:
        try:
            d = self.broker.get_ltp([key]) or {}
            v = d.get(key)
            return float(v) if v else None
        except Exception:
            return None

    # ── 1. equity ────────────────────────────────────────────────────
    def analyze_equity(self, symbol: str, overrides=None) -> dict:
        cfg = sanitize({**self.load_config(), **(overrides or {})})
        name = (symbol or "").strip().upper()
        if not name:
            return {"status": "error", "message": "Enter a stock symbol"}
        token, exch = self.universe.resolve_equity_token(name)
        if not token:
            return {"status": "error", "message": f"{name}: no NSE/BSE equity token"}
        candles = self._candles(token, cfg["timeframe"])
        if not candles:
            return {"status": "error", "message": f"{name}: no candles for {cfg['timeframe']}"}
        read = core.analyze(candles, cfg, instrument=name, kind="equity")
        ltp = self._ltp(f"{exch}:{name}")
        return {"status": "ok", "kind": "equity", "symbol": name, "exchange": exch,
                "timeframe": cfg["timeframe"], "ltp": ltp or read.get("ltp"),
                "config": cfg, "read": read,
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    # ── 2. index options ─────────────────────────────────────────────
    def analyze_options(self, overrides=None, *, index: Optional[str] = None,
                        mode: Optional[str] = None, strike: Optional[float] = None,
                        opt_type: str = "CE", expiry_type: str = "weekly") -> dict:
        cfg = sanitize({**self.load_config(), **(overrides or {})})
        idx = (index or cfg["index_name"]).strip().upper()
        mode = mode or cfg["option_mode"]
        spot_symbol = INDEX_SPOT.get(idx, idx)
        token = self._index_token(spot_symbol)
        if not token:
            return {"status": "error", "message": f"Could not resolve {spot_symbol}"}
        candles = self._candles(token, cfg["timeframe"])
        if not candles:
            return {"status": "error", "message": f"{idx}: no candles for {cfg['timeframe']}"}
        index_read = core.analyze(candles, cfg, instrument=idx, kind="index")
        spot = self._ltp(f"NSE:{spot_symbol}") or index_read.get("ltp")

        out = {"status": "ok", "kind": "options", "index": idx, "spot_symbol": spot_symbol,
               "spot": spot, "timeframe": cfg["timeframe"], "mode": mode, "config": cfg,
               "read": index_read, "option": None, "confirmation": None,
               "stance": _option_stance(index_read, idx),
               "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

        if mode != "premium":
            return out

        # optional second read on a real option premium
        try:
            exp = self.universe.expiry_for(idx, expiry_type, date.today())
            if not exp:
                out["option_error"] = f"No {expiry_type} expiry found for {idx}"
                return out
            step = STRIKE_STEP.get(idx, 50)
            k = float(strike) if strike else round((spot or 0) / step) * step
            inst = self.universe.resolve(idx, exp, k, opt_type)
            if not inst:
                out["option_error"] = f"No {idx} {int(k)} {opt_type} for {exp}"
                return out
            opt_candles = self._candles(inst["token"], cfg["timeframe"], oi=True)
            if not opt_candles:
                out["option_error"] = f"{inst['tradingsymbol']}: no candles"
                return out
            oi_series = [float(c.get("oi", 0) or 0) for c in opt_candles]
            opt_read = core.analyze(opt_candles, cfg, instrument=inst["tradingsymbol"],
                                    kind="option",
                                    oi_series=oi_series if any(oi_series) else None)
            out["option"] = {"tradingsymbol": inst["tradingsymbol"], "strike": k,
                             "opt_type": opt_type, "expiry": exp.isoformat(),
                             "ltp": self._ltp(f"NFO:{inst['tradingsymbol']}"), "read": opt_read}
            out["confirmation"] = _premium_confirmation(index_read, opt_read, opt_type)
        except Exception as exc:
            logger.debug("wyckoff option leg failed: %s", exc)
            out["option_error"] = str(exc)
        return out

    # ── 3. equity F&O (stock + future OI) ────────────────────────────
    def analyze_fno(self, symbol: str, overrides=None) -> dict:
        cfg = sanitize({**self.load_config(), **(overrides or {})})
        name = (symbol or "").strip().upper()
        token, exch = self.universe.resolve_equity_token(name)
        if not token:
            return {"status": "error", "message": f"{name}: no NSE/BSE equity token"}
        fut = self._front_future(name)
        if not fut:
            return {"status": "error",
                    "message": f"{name} is not in the F&O universe — use the Equity tab"}
        cash = self._candles(token, cfg["timeframe"])
        if not cash:
            return {"status": "error", "message": f"{name}: no candles for {cfg['timeframe']}"}
        fut_candles = self._candles(fut["token"], cfg["timeframe"], oi=True)
        oi_series = [float(c.get("oi", 0) or 0) for c in fut_candles]
        has_oi = any(oi_series)
        cash_read = core.analyze(cash, cfg, instrument=name, kind="equity",
                                 oi_series=oi_series if has_oi and len(oi_series) >= len(cash) // 2 else None)
        fut_read = core.analyze(fut_candles, cfg, instrument=fut["tradingsymbol"], kind="future",
                                oi_series=oi_series if has_oi else None) if fut_candles else None
        return {"status": "ok", "kind": "fno", "symbol": name, "exchange": exch,
                "future": {"tradingsymbol": fut["tradingsymbol"], "expiry": fut["expiry"].isoformat(),
                           "lot_size": fut.get("lot_size"),
                           "ltp": self._ltp(f"NFO:{fut['tradingsymbol']}")},
                "timeframe": cfg["timeframe"], "config": cfg,
                "ltp": self._ltp(f"{exch}:{name}") or cash_read.get("ltp"),
                "read": cash_read, "future_read": fut_read,
                "oi": (fut_read or {}).get("oi") if fut_read else None,
                "confirmation": _cash_future_confirmation(cash_read, fut_read),
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    def _front_future(self, name: str) -> Optional[dict]:
        self.universe._ensure_nfo()                    # read-only warm-up
        today = date.today()
        best = None
        for rec in getattr(self.universe, "_nfo", []):
            if rec.get("type") != "FUT" or rec.get("name") != name or rec.get("expiry") < today:
                continue
            if best is None or rec["expiry"] < best["expiry"]:
                best = rec
        return best

    # ── batch scan (workspace / telegram) ────────────────────────────
    def scan(self, symbols: list[str], overrides=None) -> dict:
        cfg = sanitize({**self.load_config(), **(overrides or {})})
        rows = []
        for s in symbols or []:
            name = (s or "").strip().upper()
            if not name:
                continue
            try:
                token, _exch = self.universe.resolve_equity_token(name)
                if not token:
                    continue
                candles = self._candles(token, cfg["timeframe"])
                if not candles:
                    continue
                r = core.analyze(candles, cfg, instrument=name, kind="equity")
                g = r["guidance"]
                rows.append({"underlying": name, "bias": r["bias"], "phase": r["phase"],
                             "action": g["action"], "confidence": g["confidence"],
                             "tests": f"{g['tests_passed']}/{g['tests_total']}",
                             "headline": g["headline"], "ltp": r.get("ltp"),
                             "range_low": r["range"].get("low"), "range_high": r["range"].get("high"),
                             "trigger": g["trigger"], "invalidation": g["invalidation"],
                             "events": [e["type"] for e in r["events"]]})
            except Exception as exc:
                logger.debug("wyckoff scan %s failed: %s", name, exc)
        order = {"ENTER_LONG": 0, "ENTER_SHORT": 1, "PREPARE_LONG": 2, "PREPARE_SHORT": 3,
                 "WAIT": 4, "AVOID": 5}
        rows.sort(key=lambda r: (order.get(r["action"], 9), -r["confidence"], r["underlying"]))
        return {"status": "ok", "timeframe": cfg["timeframe"], "rows": rows,
                "scanned": len(rows), "config": cfg,
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


# ── translations ─────────────────────────────────────────────────────
def _option_stance(read: dict, idx: str) -> dict:
    """Turn a structural read on the index into an option stance. Buying premium
    only makes sense where the Wyckoff setup is complete — Phase C or D."""
    g = read.get("guidance") or {}
    action = g.get("action")
    phase = read.get("phase")
    if action == "ENTER_LONG":
        return {"stance": "BUY CALL", "tone": "bull",
                "note": f"{idx} is in Phase {phase} accumulation with the setup complete — buy calls (or sell puts).",
                "confidence": g.get("confidence", 0)}
    if action == "ENTER_SHORT":
        return {"stance": "BUY PUT", "tone": "bear",
                "note": f"{idx} is in Phase {phase} distribution with the setup complete — buy puts (or sell calls).",
                "confidence": g.get("confidence", 0)}
    if action == "PREPARE_LONG":
        return {"stance": "PREPARE CALL", "tone": "bull",
                "note": "Long setup forming — do not pay for premium until the test confirms.",
                "confidence": g.get("confidence", 0)}
    if action == "PREPARE_SHORT":
        return {"stance": "PREPARE PUT", "tone": "bear",
                "note": "Short setup forming — wait for the confirmation before paying for premium.",
                "confidence": g.get("confidence", 0)}
    return {"stance": "NO TRADE", "tone": "flat",
            "note": ("Phase B or no structure: direction is unresolved and time decay is certain. "
                     "This is exactly where option buyers bleed."),
            "confidence": g.get("confidence", 0)}


def _premium_confirmation(index_read: dict, opt_read: dict, opt_type: str) -> dict:
    """Does the premium's own structure agree with the index read?"""
    i_side = index_read.get("side")
    o_side = opt_read.get("side")
    if not i_side or not o_side:
        return {"agree": None, "note": "One of the two series has no readable structure."}
    # a CALL premium should accumulate when the index accumulates; a PUT mirrors it
    expected = i_side if opt_type == "CE" else ("distrib" if i_side == "accum" else "accum")
    agree = (o_side == expected)
    return {"agree": agree,
            "note": ("The premium's own structure agrees with the index read — the move is being paid for."
                     if agree else
                     "The premium's structure disagrees with the index. Premium series decay and trade thin: "
                     "trust the index, and treat this as a warning that the option is badly priced or illiquid."),
            "index_side": i_side, "option_side": o_side}


def _cash_future_confirmation(cash: dict, fut: Optional[dict]) -> dict:
    if not fut:
        return {"agree": None, "note": "No future series available."}
    same = cash.get("side") == fut.get("side") and cash.get("phase") == fut.get("phase")
    oi = fut.get("oi") or {}
    bits = []
    if same:
        bits.append("Cash and futures show the same structure and phase.")
    else:
        bits.append(f"Cash reads Phase {cash.get('phase')} {cash.get('bias')}, "
                    f"futures read Phase {fut.get('phase')} {fut.get('bias')} — wait for them to agree.")
    if oi.get("available"):
        bits.append(oi.get("note", ""))
    return {"agree": same, "note": " ".join(b for b in bits if b), "oi": oi or None}


NL = "\n"


def format_message(payload: dict) -> str:
    """Crisp Telegram note for one Wyckoff read."""
    read = payload.get("read") or {}
    g = read.get("guidance") or {}
    rng = read.get("range") or {}
    who = payload.get("symbol") or payload.get("index") or read.get("instrument") or "—"
    icon = {"ENTER_LONG": "🟢", "ENTER_SHORT": "🔴", "PREPARE_LONG": "🟡",
            "PREPARE_SHORT": "🟡", "AVOID": "⛔", "WAIT": "⚪"}.get(g.get("action"), "⚪")
    lines = [f"{icon} <b>WYCKOFF — {who}</b>",
             f"{payload.get('timeframe')}  ·  Phase {read.get('phase')}  ·  {read.get('bias')}",
             f"<b>{g.get('headline', '')}</b>  ({g.get('confidence', 0)}% · "
             f"{g.get('tests_passed', 0)}/{g.get('tests_total', 9)} tests)"]
    if payload.get("stance"):
        lines.append(f"Stance: <b>{payload['stance']['stance']}</b>")
    if rng:
        lines.append(f"Range {rng.get('low')} – {rng.get('high')} "
                     f"({rng.get('duration')} bars, price {rng.get('position_pct')}% up the range)")
    if g.get("trigger"):
        lines.append(f"Trigger {g['trigger']}  ·  invalidation {g.get('invalidation')}")
    if g.get("reasons"):
        lines += [""] + [f"• {r}" for r in g["reasons"][:4]]
    if g.get("avoid"):
        lines += ["", "<b>Avoid because</b>"] + [f"• {a}" for a in g["avoid"][:4]]
    return NL.join(lines)
