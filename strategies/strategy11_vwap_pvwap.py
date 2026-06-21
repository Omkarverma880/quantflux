"""
Strategy 11 — VWAP vs Previous-Day VWAP (positional NIFTY options).

Live / paper version of the research module ``research/vwap_pvwap.py``.

Concept (identical to the research)
-----------------------------------
Each day, on that day's FIRST VWAP × previous-day-VWAP crossover (09:30-15:15),
BUY one CALL and one PUT (NRML, carried overnight) for the configured variant
(weekly/monthly × ITM/OTM/ATM). Each leg is TARGET-ONLY (no stop-loss) and is
held across days until:

    • its target premium is hit, OR
    • the configured 15:15 force-exit on the contract's EXPIRY day.

Second-leg loss control
-----------------------
Once the FIRST leg of a pair books its target, the other (losing) leg is
managed:
    • breaks back above its entry  → ride to its own target / expiry
    • points / percent  → cut at (entry − buffer) when it recovers near entry
    • fraction          → cut at (entry ÷ N) as it decays (a hard stop)
    • never triggers    → 15:15 expiry exit

Hybrid execution
----------------
    • Target            → resting GTT at Zerodha (fires even if the app is
                          offline / token expired). Reconciled on each tick.
    • Leg-2 fraction    → GTT stop placed once the first leg books target.
    • Leg-2 pts/percent → managed live by the app loop (dynamic).
    • Expiry-day exit   → market SELL by the app loop.
Paper mode simulates every fill against the LTP (no real orders / GTTs).

Multi-day state
---------------
Open pairs persist (DB-backed JSON) and are RESTORED across days and restarts.
Each morning after the user re-logs into Zerodha, ``check()`` reconciles the
persisted open legs against the broker's net positions to learn what filled
while the app was away.
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, time as dtime, timedelta
from enum import Enum
from math import floor
from typing import Optional

from config import settings
from core.broker import (
    Broker, OrderRequest, Exchange, OrderSide, OrderType, ProductType,
)
from core.logger import get_logger
from research.vwap_pvwap import (
    _candle_dt, _parse_expiry,
    STRIKE_INTERVAL, ITM_OFFSET, OTM_OFFSET, LOT_SIZE,
    ENTRY_START, SIGNAL_CUTOFF, EXPIRY_EXIT, MARKET_OPEN, MARKET_CLOSE,
    WEEKLY_MAX_DAYS, MONTHLY_MAX_DAYS, INDEX_NAME, INDEX_SPOT_TRADINGSYMBOL,
)

logger = get_logger("strategy11.vwap_pvwap")

STATE_FILE = settings.DATA_DIR / "strategy_configs" / "strategy11_state.json"
TRADE_HISTORY_FILE = settings.DATA_DIR / "trade_history" / "strategy11_trades.json"

LEG_OPEN = "OPEN"
LEG_TARGET = "TARGET"
LEG_LEG2_EXIT = "LEG2_EXIT"
LEG_EXPIRY = "EXPIRY"
LEG_MANUAL = "MANUAL_EXIT"
LEG_TERMINAL = {LEG_TARGET, LEG_LEG2_EXIT, LEG_EXPIRY, LEG_MANUAL}


class GlobalState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"


def _full_day_vwap(candles: list[dict]) -> Optional[float]:
    cum_pv = cum_v = cum_tp = 0.0
    n = 0
    for c in candles:
        tp = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0
        v = float(c.get("volume", 0) or 0)
        cum_pv += tp * v
        cum_v += v
        cum_tp += tp
        n += 1
    if n == 0:
        return None
    return cum_pv / cum_v if cum_v > 0 else cum_tp / n


def _running_vwap_series(candles: list[dict]) -> list[float]:
    out, cum_pv, cum_v, cum_tp, n = [], 0.0, 0.0, 0.0, 0
    for c in candles:
        tp = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0
        v = float(c.get("volume", 0) or 0)
        cum_pv += tp * v
        cum_v += v
        cum_tp += tp
        n += 1
        out.append(cum_pv / cum_v if cum_v > 0 else cum_tp / n)
    return out


class Strategy11VwapPvwap:
    """Positional VWAP/prev-VWAP options strategy (live or paper)."""

    def __init__(self, broker: Broker, config: dict, user_id: Optional[int] = None):
        self.broker = broker
        self.user_id = user_id          # set by the route → enables DB persistence
        self._lock = threading.Lock()

        # ── Config ──
        self.paper_trade = bool(config.get("paper_trade", True))
        self.expiry_type = str(config.get("expiry_type", "monthly")).lower()  # monthly|weekly
        self.strike_mode = str(config.get("strike_mode", "ITM")).upper()       # ITM|OTM|ATM
        self.target_mode = config.get("target_mode", "points")                 # points|percent|double
        self.target_points = float(config.get("target_points", 300))
        self.target_percent = float(config.get("target_percent", 150))
        self.lots = max(1, int(config.get("lots", 3)))
        self.manage_second_leg = bool(config.get("manage_second_leg", True))
        self.leg2_exit_mode = config.get("leg2_exit_mode", "fraction")         # points|percent|fraction
        self.leg2_exit_value = float(config.get("leg2_exit_value", 2))
        self.max_open_pairs = int(config.get("max_open_pairs", 5))

        # ── State ──
        self.is_active = False
        self.state = GlobalState.IDLE
        self.trades: list[dict] = []            # open + recently-closed pairs
        self.trade_log: list[dict] = []         # closed legs (session view)
        self._entered_dates: list[str] = []     # ISO dates we already entered on
        self._trading_date: Optional[date] = None

        # ── Caches (daily) ──
        self._index_token: Optional[int] = None
        self._nfo_options: Optional[list[dict]] = None
        self._nfo_date: Optional[date] = None
        self._prev_vwap: Optional[float] = None
        self._prev_vwap_date: Optional[date] = None
        self._recon_counter = 0

    @property
    def qty(self) -> int:
        return LOT_SIZE * self.lots

    @property
    def has_open_positions(self) -> bool:
        return any(not p.get("closed") for p in self.trades)

    # ──────────────────── Config ────────────────────────

    def _config_dict(self) -> dict:
        return {
            "paper_trade": self.paper_trade,
            "expiry_type": self.expiry_type,
            "strike_mode": self.strike_mode,
            "target_mode": self.target_mode,
            "target_points": self.target_points,
            "target_percent": self.target_percent,
            "lots": self.lots,
            "qty": self.qty,
            "manage_second_leg": self.manage_second_leg,
            "leg2_exit_mode": self.leg2_exit_mode,
            "leg2_exit_value": self.leg2_exit_value,
            "max_open_pairs": self.max_open_pairs,
            "entry_start": ENTRY_START.strftime("%H:%M"),
            "signal_cutoff": SIGNAL_CUTOFF.strftime("%H:%M"),
            "expiry_exit": EXPIRY_EXIT.strftime("%H:%M"),
        }

    def apply_config(self, config: dict, save: bool = True):
        self.paper_trade = bool(config.get("paper_trade", self.paper_trade))
        self.expiry_type = str(config.get("expiry_type", self.expiry_type)).lower()
        self.strike_mode = str(config.get("strike_mode", self.strike_mode)).upper()
        self.target_mode = config.get("target_mode", self.target_mode)
        self.target_points = float(config.get("target_points", self.target_points))
        self.target_percent = float(config.get("target_percent", self.target_percent))
        self.lots = max(1, int(config.get("lots", self.lots)))
        self.manage_second_leg = bool(config.get("manage_second_leg", self.manage_second_leg))
        self.leg2_exit_mode = config.get("leg2_exit_mode", self.leg2_exit_mode)
        self.leg2_exit_value = float(config.get("leg2_exit_value", self.leg2_exit_value))
        self.max_open_pairs = int(config.get("max_open_pairs", self.max_open_pairs))
        if save:
            self._save_state()

    def start(self, config: dict):
        self.apply_config(config, save=False)
        self.is_active = True
        self.state = GlobalState.RUNNING
        self._check_day_reset()
        self._save_state()
        logger.info("Strategy 11 started (%s, %s %s, paper=%s)",
                    self.target_mode, self.expiry_type, self.strike_mode, self.paper_trade)

    def stop(self):
        self.is_active = False
        self._save_state()
        logger.info("Strategy 11 stopped (open pairs keep being managed)")

    # ──────────────────── Instruments / levels ──────────

    def _instruments(self, exch: str) -> list[dict]:
        return self.broker.get_instruments(exch)

    def _resolve_index_token(self) -> Optional[int]:
        if self._index_token:
            return self._index_token
        try:
            for inst in self._instruments("NSE"):
                if inst.get("tradingsymbol") == INDEX_SPOT_TRADINGSYMBOL:
                    self._index_token = int(inst["instrument_token"])
                    return self._index_token
        except Exception as exc:
            logger.error("S11 index token lookup failed: %s", exc)
        return None

    def _nifty_options(self) -> list[dict]:
        today = date.today()
        if self._nfo_options is not None and self._nfo_date == today:
            return self._nfo_options
        opts = []
        try:
            for inst in self._instruments("NFO"):
                if inst.get("name") != INDEX_NAME or inst.get("instrument_type") not in ("CE", "PE"):
                    continue
                exp = _parse_expiry(inst.get("expiry"))
                if not exp:
                    continue
                opts.append({
                    "tradingsymbol": inst.get("tradingsymbol"),
                    "token": int(inst["instrument_token"]),
                    "strike": float(inst.get("strike", 0) or 0),
                    "type": inst.get("instrument_type"),
                    "expiry": exp,
                })
        except Exception as exc:
            logger.error("S11 NFO instruments fetch failed: %s", exc)
        self._nfo_options, self._nfo_date = opts, today
        return opts

    def _expiry_for(self, day: date) -> Optional[date]:
        exps = sorted({o["expiry"] for o in self._nifty_options()})
        if self.expiry_type == "monthly":
            by_month: dict = {}
            for e in exps:
                k = (e.year, e.month)
                if k not in by_month or e > by_month[k]:
                    by_month[k] = e
            exps = sorted(by_month.values())
        for e in exps:
            if e >= day:
                return e
        return None

    def _resolve_option(self, expiry: date, strike: float, opt_type: str) -> Optional[dict]:
        for o in self._nifty_options():
            if o["expiry"] == expiry and o["type"] == opt_type and abs(o["strike"] - strike) < 0.5:
                return o
        return None

    def _strikes_for(self, spot: float) -> tuple[int, int]:
        atm = int(round(spot / STRIKE_INTERVAL) * STRIKE_INTERVAL)
        if self.strike_mode == "ATM":
            return atm, atm
        if self.strike_mode == "OTM":
            return atm + OTM_OFFSET, atm - OTM_OFFSET
        return atm - ITM_OFFSET, atm + ITM_OFFSET  # ITM

    def _target_for(self, entry: float) -> float:
        if self.target_mode == "double":
            return entry * 2.0
        if self.target_mode == "percent":
            return entry * (1.0 + self.target_percent / 100.0)
        return entry + self.target_points

    def _leg2_level(self, entry: float) -> float:
        if self.leg2_exit_mode == "fraction":
            return entry / max(1.0, self.leg2_exit_value)
        if self.leg2_exit_mode == "percent":
            return entry * (1.0 - self.leg2_exit_value / 100.0)
        return entry - self.leg2_exit_value

    # ──────────────────── VWAP / crossover ──────────────

    def _previous_trading_day(self, d: date) -> date:
        d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d

    def _index_minute(self, day: date, to_dt: Optional[datetime] = None) -> list[dict]:
        token = self._resolve_index_token()
        if not token:
            return []
        frm = datetime.combine(day, MARKET_OPEN)
        to = to_dt or datetime.combine(day, MARKET_CLOSE)
        try:
            return self.broker.get_historical_data(token, frm, to, "minute") or []
        except Exception as exc:
            logger.debug("S11 index minute fetch failed: %s", exc)
            return []

    def _prev_day_vwap(self) -> Optional[float]:
        today = date.today()
        if self._prev_vwap is not None and self._prev_vwap_date == today:
            return self._prev_vwap
        d = today
        for _ in range(8):
            d = self._previous_trading_day(d)
            candles = self._index_minute(d)
            v = _full_day_vwap(candles)
            if v is not None:
                self._prev_vwap, self._prev_vwap_date = v, today
                return v
        return None

    def _first_crossover_today(self, now: datetime) -> Optional[dict]:
        """Return the day's first VWAP×prevVWAP crossover (in the entry window)
        or None."""
        prev = self._prev_day_vwap()
        if prev is None:
            return None
        candles = self._index_minute(date.today(), now)
        if not candles:
            return None
        rv = _running_vwap_series(candles)
        prev_diff = None
        for i, c in enumerate(candles):
            dt = _candle_dt(c)
            if not dt:
                continue
            diff = rv[i] - prev
            if prev_diff is not None and prev_diff != 0 and diff != 0:
                up, dn = prev_diff < 0 < diff, prev_diff > 0 > diff
                if (up or dn) and ENTRY_START <= dt.time() <= SIGNAL_CUTOFF:
                    return {"time": dt.strftime("%H:%M"), "dt": dt,
                            "spot": float(c["close"]), "direction": "BULL" if up else "BEAR"}
            prev_diff = diff
        return None

    # ──────────────────── Order helpers ─────────────────

    def _ltp(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        try:
            return self.broker.get_ltp(symbols)
        except Exception as exc:
            logger.debug("S11 LTP fetch failed: %s", exc)
            return {}

    def _key(self, symbol: str) -> str:
        return f"NFO:{symbol}"

    def _place_buy(self, symbol: str, ref_price: float) -> bool:
        if self.paper_trade:
            return True
        try:
            req = OrderRequest(
                tradingsymbol=symbol, exchange=Exchange.NFO, side=OrderSide.BUY,
                quantity=self.qty, order_type=OrderType.MARKET, product=ProductType.NRML,
                tag="S11ENTRY",
            )
            self.broker.place_order(req)
            return True
        except Exception as exc:
            logger.error("S11 buy failed for %s: %s", symbol, exc)
            return False

    def _place_sell(self, symbol: str, tag: str) -> bool:
        if self.paper_trade:
            return True
        try:
            req = OrderRequest(
                tradingsymbol=symbol, exchange=Exchange.NFO, side=OrderSide.SELL,
                quantity=self.qty, order_type=OrderType.MARKET, product=ProductType.NRML,
                tag=tag,
            )
            self.broker.place_order(req)
            return True
        except Exception as exc:
            logger.error("S11 sell failed for %s: %s", symbol, exc)
            return False

    def _place_target_gtt(self, leg: dict, ltp: float):
        if self.paper_trade:
            return
        try:
            leg["target_gtt"] = self.broker.place_gtt(
                tradingsymbol=leg["symbol"], exchange="NFO",
                trigger_price=leg["target_price"], last_price=ltp or leg["entry_price"],
                quantity=self.qty, side="SELL", product="NRML", order_type="LIMIT",
                price=leg["target_price"],
            )
        except Exception as exc:
            logger.error("S11 target GTT failed for %s: %s", leg["symbol"], exc)

    def _place_leg2_gtt(self, leg: dict, ltp: float):
        if self.paper_trade:
            return
        try:
            leg["leg2_gtt"] = self.broker.place_gtt(
                tradingsymbol=leg["symbol"], exchange="NFO",
                trigger_price=leg["leg2_level"], last_price=ltp or leg["entry_price"],
                quantity=self.qty, side="SELL", product="NRML", order_type="MARKET",
            )
        except Exception as exc:
            logger.error("S11 leg2 GTT failed for %s: %s", leg["symbol"], exc)

    def _cancel_gtt(self, gtt_id):
        if gtt_id:
            try:
                self.broker.delete_gtt(gtt_id)
            except Exception:
                pass

    # ──────────────────── Entry ─────────────────────────

    def _enter(self, ev: dict, mark_entered: bool = True):
        day = date.today()
        expiry = self._expiry_for(day)
        if not expiry:
            logger.warning("S11 no %s expiry resolvable — skip entry", self.expiry_type)
            return
        max_days = WEEKLY_MAX_DAYS if self.expiry_type == "weekly" else MONTHLY_MAX_DAYS
        if (expiry - day).days > max_days:
            logger.warning("S11 nearest %s expiry %s too far — skip entry",
                           self.expiry_type, expiry)
            return

        ce_strike, pe_strike = self._strikes_for(ev["spot"])
        ce = self._resolve_option(expiry, ce_strike, "CE")
        pe = self._resolve_option(expiry, pe_strike, "PE")
        if not ce or not pe:
            logger.warning("S11 contract not listed (CE %s / PE %s) — skip entry",
                           ce_strike, pe_strike)
            return

        ltp_map = self._ltp([self._key(ce["tradingsymbol"]), self._key(pe["tradingsymbol"])])
        legs = []
        for opt, strike in ((ce, ce_strike), (pe, pe_strike)):
            ltp = float(ltp_map.get(self._key(opt["tradingsymbol"]), 0) or 0)
            if ltp <= 0:
                logger.warning("S11 no LTP for %s — skip entry", opt["tradingsymbol"])
                return
            if not self._place_buy(opt["tradingsymbol"], ltp):
                return
            leg = {
                "option_type": opt["type"], "symbol": opt["tradingsymbol"],
                "token": opt["token"], "strike": int(strike), "qty": self.qty,
                "entry_price": round(ltp, 2), "entry_time": ev["time"],
                "entry_date": day.isoformat(),
                "target_price": round(self._target_for(ltp), 2),
                "state": LEG_OPEN, "target_gtt": None,
                "leg2_armed": False, "broke_out": False, "leg2_gtt": None, "leg2_level": None,
                "ltp": round(ltp, 2), "exit_price": None, "exit_time": None,
                "exit_date": None, "pnl": None, "exit_reason": None,
            }
            self._place_target_gtt(leg, ltp)
            legs.append(leg)

        pair = {
            "id": f"{day.isoformat()}-{len([t for t in self.trades]) + 1}",
            "date": day.isoformat(), "entry_time": ev["time"], "signal": ev["direction"],
            "expiry_type": self.expiry_type, "expiry": expiry.isoformat(),
            "spot": round(ev["spot"], 2), "legs": legs, "closed": False,
        }
        self.trades.append(pair)
        if mark_entered and day.isoformat() not in self._entered_dates:
            self._entered_dates.append(day.isoformat())
        for leg in legs:
            self._db_upsert_leg(pair, leg)
        self._save_state()
        logger.info("S11 ENTER%s %s pair: CE %s @%.2f / PE %s @%.2f (expiry %s)",
                    " (sim)" if not mark_entered else "", ev["direction"],
                    legs[0]["symbol"], legs[0]["entry_price"],
                    legs[1]["symbol"], legs[1]["entry_price"], expiry.isoformat())

    def simulate_entry(self) -> dict:
        """Paper-only test trigger: force an entry NOW at the current spot, as
        if a crossover just fired — to watch the full multi-day cycle without
        waiting for a real signal. Does not consume the day's real entry slot."""
        with self._lock:
            if not self.paper_trade:
                return {"status": "error", "message": "Simulate is paper-mode only — switch to Paper first."}
            if len([p for p in self.trades if not p.get("closed")]) >= self.max_open_pairs:
                return {"status": "error", "message": f"Max open pairs ({self.max_open_pairs}) reached."}
            spot = 0.0
            try:
                spot = float((self.broker.get_ltp(["NSE:NIFTY 50"]) or {}).get("NSE:NIFTY 50", 0) or 0)
            except Exception:
                spot = 0.0
            if spot <= 0:
                return {"status": "error", "message": "No live NIFTY spot — is the market open & Zerodha connected?"}
            self.state = GlobalState.RUNNING
            ev = {"time": datetime.now().strftime("%H:%M"), "dt": datetime.now(),
                  "spot": spot, "direction": "BULL"}
            try:
                self._enter(ev, mark_entered=False)
            except Exception as exc:
                logger.error("S11 simulate entry failed: %s", exc)
                return {"status": "error", "message": str(exc)}
            return {"status": "ok", **self.get_status()}

    # ──────────────────── Monitoring / exits ────────────

    def _book_exit(self, pair: dict, leg: dict, exit_price: float, reason: str):
        leg["exit_price"] = round(exit_price, 2)
        leg["exit_time"] = datetime.now().strftime("%H:%M")
        leg["exit_date"] = date.today().isoformat()
        leg["pnl"] = round((exit_price - leg["entry_price"]) * leg["qty"], 2)
        leg["state"] = reason
        self._cancel_gtt(leg.get("target_gtt"))
        self._cancel_gtt(leg.get("leg2_gtt"))
        self._append_trade(leg, pair)
        self._db_upsert_leg(pair, leg)
        logger.info("S11 EXIT %s %s @%.2f pnl=%.2f", leg["symbol"], reason, exit_price, leg["pnl"])

        # Arm the sibling's leg-2 management once the FIRST leg books target.
        if reason == LEG_TARGET and self.manage_second_leg:
            for other in pair["legs"]:
                if other is not leg and other["state"] == LEG_OPEN and not other["leg2_armed"]:
                    other["leg2_armed"] = True
                    other["leg2_level"] = round(self._leg2_level(other["entry_price"]), 2)
                    if self.leg2_exit_mode == "fraction":
                        self._place_leg2_gtt(other, other.get("ltp") or other["entry_price"])
                    self._db_upsert_leg(pair, other)
                    logger.info("S11 leg-2 armed: %s level=%.2f (%s)",
                                other["symbol"], other["leg2_level"], self.leg2_exit_mode)

        if all(l["state"] in LEG_TERMINAL for l in pair["legs"]):
            pair["closed"] = True

    def _is_expiry_exit_time(self, expiry: date, now: datetime) -> bool:
        return now.date() >= expiry and now.time() >= EXPIRY_EXIT

    def check(self) -> dict:
        if not self._lock.acquire(blocking=False):
            return self.get_status()
        try:
            now = datetime.now()
            self._check_day_reset()
            if now.weekday() >= 5 or not (MARKET_OPEN <= now.time() <= dtime(15, 30)):
                return self.get_status()

            # ── Entry: first crossover of the day ──
            if (self.is_active and date.today().isoformat() not in self._entered_dates
                    and ENTRY_START <= now.time() <= SIGNAL_CUTOFF
                    and len([p for p in self.trades if not p.get("closed")]) < self.max_open_pairs):
                try:
                    ev = self._first_crossover_today(now)
                    if ev:
                        self._enter(ev)
                except Exception as exc:
                    logger.error("S11 entry scan failed: %s", exc)

            # ── Monitor open legs ──
            open_legs = [(p, l) for p in self.trades if not p.get("closed")
                         for l in p["legs"] if l["state"] == LEG_OPEN]
            if open_legs:
                ltp_map = self._ltp(list({self._key(l["symbol"]) for _, l in open_legs}))
                # Live reconciliation of GTT fills (throttled)
                held = None
                self._recon_counter += 1
                if not self.paper_trade and self._recon_counter % 5 == 0:
                    held = self._held_symbols()

                for pair, leg in open_legs:
                    ltp = float(ltp_map.get(self._key(leg["symbol"]), 0) or 0)
                    if ltp > 0:
                        leg["ltp"] = round(ltp, 2)
                    expiry = date.fromisoformat(pair["expiry"])

                    # 1) Expiry-day force exit (both modes)
                    if self._is_expiry_exit_time(expiry, now):
                        self._place_sell(leg["symbol"], "S11EXP")
                        self._book_exit(pair, leg, ltp or leg.get("ltp") or leg["entry_price"], LEG_EXPIRY)
                        continue

                    if self.paper_trade:
                        self._paper_manage(pair, leg, ltp)
                    else:
                        self._live_manage(pair, leg, ltp, held)

                # Keep the DB position rows' LTP fresh for pgAdmin (throttled).
                if self.user_id and self._recon_counter % 30 == 0:
                    self._db_sync_open()

            self._save_state()
            return self.get_status()
        finally:
            self._lock.release()

    def _paper_manage(self, pair: dict, leg: dict, ltp: float):
        if ltp <= 0:
            return
        if not leg["leg2_armed"]:
            if ltp >= leg["target_price"]:
                self._book_exit(pair, leg, leg["target_price"], LEG_TARGET)
            return
        # managed (second) leg
        if ltp >= leg["entry_price"]:
            leg["broke_out"] = True
        if ltp >= leg["target_price"]:
            self._book_exit(pair, leg, leg["target_price"], LEG_TARGET)
            return
        if not leg["broke_out"]:
            lvl = leg["leg2_level"]
            if self.leg2_exit_mode == "fraction":
                if ltp <= lvl:
                    self._book_exit(pair, leg, lvl, LEG_LEG2_EXIT)
            else:  # points / percent — cut when it recovers up near entry
                if ltp >= lvl:
                    self._book_exit(pair, leg, lvl, LEG_LEG2_EXIT)

    def _live_manage(self, pair: dict, leg: dict, ltp: float, held: Optional[set]):
        # Target + fraction stop are GTTs → detect fills via net positions.
        if held is not None and leg["symbol"].upper() not in held:
            reason = LEG_LEG2_EXIT if leg["leg2_armed"] and self.leg2_exit_mode == "fraction" else LEG_TARGET
            price = leg["leg2_level"] if reason == LEG_LEG2_EXIT else leg["target_price"]
            self._book_exit(pair, leg, price or ltp or leg["entry_price"], reason)
            return
        # Dynamic leg-2 (points/percent) is managed live by the app loop.
        if leg["leg2_armed"] and self.leg2_exit_mode in ("points", "percent") and ltp > 0:
            if ltp >= leg["entry_price"]:
                leg["broke_out"] = True
            if not leg["broke_out"] and ltp >= leg["leg2_level"]:
                self._place_sell(leg["symbol"], "S11L2")
                self._book_exit(pair, leg, leg["leg2_level"], LEG_LEG2_EXIT)

    def _held_symbols(self) -> set:
        try:
            positions = self.broker.get_positions(kind="net") or []
            return {(p.tradingsymbol or "").upper() for p in positions
                    if int(getattr(p, "quantity", 0) or 0) != 0}
        except Exception as exc:
            logger.debug("S11 net positions fetch failed: %s", exc)
            return set()

    # ──────────────────── Day reset ─────────────────────

    def _check_day_reset(self):
        today = date.today()
        if self._trading_date == today:
            return
        self._trading_date = today
        # New day: drop stale daily caches; KEEP open positions.
        self._prev_vwap = None
        self._prev_vwap_date = None
        self._nfo_options = None
        # prune fully-closed pairs older than a few days to keep state small
        self.trades = [p for p in self.trades if not p.get("closed")
                       or (today - date.fromisoformat(p["date"])).days <= 7]
        logger.info("S11 day reset for %s — %d open pair(s) carried",
                    today.isoformat(), len([p for p in self.trades if not p.get("closed")]))

    # ──────────────────── Persistence ───────────────────

    def _save_state(self):
        data = {
            "is_active": self.is_active,
            "state": self.state.value,
            "trading_date": (self._trading_date or date.today()).isoformat(),
            "entered_dates": self._entered_dates[-30:],
            "trades": self.trades,
            "trade_log": self.trade_log[-100:],
            "config": self._config_dict(),
            "saved_at": datetime.now().isoformat(),
        }
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(data, indent=2, default=str))
        except Exception as exc:
            logger.error("S11 save_state failed: %s", exc)

    def restore_state(self) -> bool:
        """Restore across days/restarts (positional — never date-gated).

        Positions come from the DB (authoritative, survives redeploys); flags &
        config come from the JSON state file.
        """
        loaded = False
        # Flags + config from JSON (lightweight; may be absent on a fresh deploy)
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text())
                self.is_active = bool(data.get("is_active", False))
                self.state = GlobalState(data.get("state", "IDLE"))
                self._entered_dates = list(data.get("entered_dates", []))
                self.trades = list(data.get("trades", []))
                self.trade_log = list(data.get("trade_log", []))
                cfg = data.get("config", {}) or {}
                if cfg:
                    self.apply_config(cfg, save=False)
                loaded = True
            except Exception as exc:
                logger.warning("S11 JSON restore failed: %s", exc)
        # Positions from DB override the JSON copy when available
        try:
            if self._db_reconstruct():
                loaded = True
        except Exception as exc:
            logger.warning("S11 DB restore failed: %s", exc)
        if loaded:
            logger.info("S11 state restored: %d open pair(s)",
                        len([p for p in self.trades if not p.get("closed")]))
        return loaded

    def _append_trade(self, leg: dict, pair: dict):
        rec = {
            "date": pair["date"], "entry_time": leg["entry_time"],
            "exit_date": leg["exit_date"], "exit_time": leg["exit_time"],
            "held_days": ((date.fromisoformat(leg["exit_date"]) - date.fromisoformat(leg["entry_date"])).days
                          if leg.get("exit_date") else 0),
            "direction": "CALL" if leg["option_type"] == "CE" else "PUT",
            "signal": pair["signal"], "expiry_type": pair["expiry_type"],
            "expiry": pair["expiry"], "strike": leg["strike"], "symbol": leg["symbol"],
            "premium_buy": leg["entry_price"], "target_premium": leg["target_price"],
            "premium_sell": leg["exit_price"], "qty": leg["qty"], "pnl": leg["pnl"],
            "exit_reason": leg["state"], "paper": self.paper_trade,
        }
        self.trade_log.append(rec)
        try:
            trades = []
            if TRADE_HISTORY_FILE.exists():
                trades = json.loads(TRADE_HISTORY_FILE.read_text())
            trades.append(rec)
            TRADE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            TRADE_HISTORY_FILE.write_text(json.dumps(trades, indent=2, default=str))
        except Exception as exc:
            logger.error("S11 trade history append failed: %s", exc)

    # ──────────────── DB (pgAdmin-visible, Railway-safe) ────────────

    @staticmethod
    def _as_date(s):
        try:
            return date.fromisoformat(s) if s else None
        except Exception:
            return None

    def _leg_columns(self, pair: dict, leg: dict) -> dict:
        return dict(
            user_id=self.user_id, pair_id=pair["id"],
            trade_date=self._as_date(pair["date"]), entry_time=leg.get("entry_time"),
            signal=pair.get("signal"), expiry_type=pair.get("expiry_type"),
            expiry=self._as_date(pair.get("expiry")), spot=pair.get("spot"),
            option_type=leg["option_type"], strike=leg.get("strike"),
            symbol=leg["symbol"], token=leg.get("token"), qty=leg.get("qty"),
            entry_price=leg.get("entry_price"), target_price=leg.get("target_price"),
            ltp=leg.get("ltp"), state=leg.get("state"),
            leg2_armed=bool(leg.get("leg2_armed")), broke_out=bool(leg.get("broke_out")),
            leg2_level=leg.get("leg2_level"),
            target_gtt=str(leg.get("target_gtt")) if leg.get("target_gtt") else None,
            leg2_gtt=str(leg.get("leg2_gtt")) if leg.get("leg2_gtt") else None,
            exit_price=leg.get("exit_price"), exit_time=leg.get("exit_time"),
            exit_date=self._as_date(leg.get("exit_date")), pnl=leg.get("pnl"),
            exit_reason=leg.get("exit_reason"), paper=self.paper_trade,
        )

    def _db_upsert_leg(self, pair: dict, leg: dict):
        if not self.user_id:
            return
        from core.database import get_db_session
        from core.models import Strategy11Leg
        db = get_db_session()
        try:
            row = db.query(Strategy11Leg).filter_by(
                user_id=self.user_id, pair_id=pair["id"], option_type=leg["option_type"]
            ).first()
            vals = self._leg_columns(pair, leg)
            if row is None:
                db.add(Strategy11Leg(**vals))
            else:
                for k, v in vals.items():
                    setattr(row, k, v)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.debug("S11 db upsert failed: %s", exc)
        finally:
            db.close()

    def _db_sync_open(self):
        for p in self.trades:
            if p.get("closed"):
                continue
            for l in p["legs"]:
                self._db_upsert_leg(p, l)

    def _db_reconstruct(self) -> bool:
        """Rebuild self.trades from the DB (authoritative across redeploys)."""
        if not self.user_id:
            return False
        from core.database import get_db_session
        from core.models import Strategy11Leg
        db = get_db_session()
        try:
            cutoff = date.today() - timedelta(days=7)
            rows = db.query(Strategy11Leg).filter(
                Strategy11Leg.user_id == self.user_id,
                Strategy11Leg.trade_date >= cutoff,
            ).all()
            if not rows:
                return False
            pairs: dict = {}
            for r in rows:
                p = pairs.setdefault(r.pair_id, {
                    "id": r.pair_id,
                    "date": r.trade_date.isoformat() if r.trade_date else None,
                    "entry_time": r.entry_time, "signal": r.signal,
                    "expiry_type": r.expiry_type,
                    "expiry": r.expiry.isoformat() if r.expiry else None,
                    "spot": float(r.spot) if r.spot is not None else 0,
                    "legs": [], "closed": False,
                })
                p["legs"].append({
                    "option_type": r.option_type, "symbol": r.symbol, "token": r.token,
                    "strike": r.strike, "qty": r.qty,
                    "entry_price": float(r.entry_price) if r.entry_price is not None else 0,
                    "entry_time": r.entry_time,
                    "entry_date": r.trade_date.isoformat() if r.trade_date else None,
                    "target_price": float(r.target_price) if r.target_price is not None else 0,
                    "state": r.state, "target_gtt": r.target_gtt,
                    "leg2_armed": bool(r.leg2_armed), "broke_out": bool(r.broke_out),
                    "leg2_gtt": r.leg2_gtt,
                    "leg2_level": float(r.leg2_level) if r.leg2_level is not None else None,
                    "ltp": float(r.ltp) if r.ltp is not None else 0,
                    "exit_price": float(r.exit_price) if r.exit_price is not None else None,
                    "exit_time": r.exit_time,
                    "exit_date": r.exit_date.isoformat() if r.exit_date else None,
                    "pnl": float(r.pnl) if r.pnl is not None else None,
                    "exit_reason": r.exit_reason,
                })
            for p in pairs.values():
                p["closed"] = all(l["state"] in LEG_TERMINAL for l in p["legs"])
            self.trades = list(pairs.values())
            logger.info("S11 reconstructed %d pair(s) from DB", len(self.trades))
            return True
        except Exception as exc:
            logger.warning("S11 db reconstruct failed: %s", exc)
            return False
        finally:
            db.close()

    # ──────────────────── Status ────────────────────────

    def get_status(self) -> dict:
        open_pairs = [p for p in self.trades if not p.get("closed")]
        open_legs = [l for p in open_pairs for l in p["legs"] if l["state"] == LEG_OPEN]
        realized = round(sum(r["pnl"] or 0 for r in self.trade_log), 2)
        unrealized = 0.0
        for l in open_legs:
            if l.get("ltp"):
                unrealized += (l["ltp"] - l["entry_price"]) * l["qty"]
        return {
            "strategy": "strategy11_vwap_pvwap",
            "is_active": self.is_active,
            "state": self.state.value,
            "paper_trade": self.paper_trade,
            "open_pairs": len(open_pairs),
            "open_legs": len(open_legs),
            "entered_today": date.today().isoformat() in self._entered_dates,
            "realized_pnl": realized,
            "unrealized_pnl": round(unrealized, 2),
            "current_ltp": 0,
            "trades": self.trades[-50:],
            "trade_log": self.trade_log[-50:],
            "config": self._config_dict(),
        }
