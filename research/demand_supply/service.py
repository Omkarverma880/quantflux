"""
Demand-Supply Equity Scanner — service layer (read-only research).

Reuses the existing per-user Broker (Kite ``quote`` gives 5-level depth, total
buy/sell quantity, day volume, OHLC and the day VWAP in one batched call) and the
straddle module's F&O ``Universe`` for the default equity list. It NEVER places,
modifies or simulates orders and imports no execution client — a failure here
cannot touch live trading.

Per scan it fetches quotes in batches, derives the demand/supply metrics, keeps a
short rolling history per symbol (for buy/sell-qty change, persistence and the
score timeline) and ranks stocks by the composite Demand Score.
"""
from __future__ import annotations

import threading
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from core.broker import Broker
from core.logger import get_logger
from research.pmvwap_straddle.universe import Universe
from research.demand_supply import calculations as calc
from research.demand_supply.config import load_config, save_config, sanitize

logger = get_logger("research.demand_supply")

IST = timezone(timedelta(hours=5, minutes=30))
_QUOTE_BATCH = 200          # Kite quote() accepts up to ~500 instruments/call


class DemandSupplyScanner:
    def __init__(self, broker: Broker, user_id: Optional[int] = None):
        self.broker = broker
        self.user_id = user_id
        self.universe = Universe(broker)
        self._lock = threading.Lock()
        self._hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=500))
        self._avgvol: dict[str, tuple[date, float]] = {}   # symbol → (day, avg daily vol)

    # ── config ──
    def load_config(self):
        return load_config()

    def save_config(self, partial):
        return save_config(partial)

    # ── market status (IST, TZ-safe regardless of server clock) ──
    @staticmethod
    def market_status() -> dict:
        now = datetime.now(IST)
        weekday = now.weekday() < 5
        hm = now.hour * 60 + now.minute
        if not weekday:
            state = "CLOSED"
        elif hm < 9 * 60:
            state = "CLOSED"
        elif hm < 9 * 60 + 15:
            state = "PRE-OPEN"
        elif hm <= 15 * 60 + 30:
            state = "OPEN"
        elif hm <= 16 * 60:
            state = "POST-MARKET"
        else:
            state = "CLOSED"
        return {"state": state, "is_open": state == "OPEN", "time": now.strftime("%H:%M:%S")}

    # ── universe resolution ──
    def _resolve_names(self, mode: str, symbols, cfg) -> tuple[list[str], str]:
        if symbols:
            names = [str(s).strip().upper() for s in symbols if str(s).strip()]
            return (list(dict.fromkeys(names)), mode or "selected")
        names = [x["name"] for x in self.universe.equities()]
        if int(cfg["max_stocks"]) > 0:
            names = names[: int(cfg["max_stocks"])]
        return (names, "all")

    # ── quote fetch (batched) ──
    def _quotes(self, names: list[str]) -> dict:
        out: dict = {}
        keys = [f"NSE:{n}" for n in names]
        for i in range(0, len(keys), _QUOTE_BATCH):
            chunk = keys[i:i + _QUOTE_BATCH]
            try:
                out.update(self.broker.get_quote(chunk) or {})
            except Exception as exc:
                logger.warning("demand_supply quote batch failed (%d): %s", len(chunk), exc)
        return out

    # ── average daily volume for RVOL (lazy, day-cached, rate-limit capped) ──
    def _ensure_avgvol(self, names: list[str], cfg: dict):
        today = datetime.now(IST).date()
        need = [n for n in names if self._avgvol.get(n, (None,))[0] != today]
        cap = int(cfg["rvol_fetch_cap"])
        if cap:
            need = need[:cap]
        lookback = int(cfg["rvol_lookback"])
        frm = datetime.combine(today - timedelta(days=lookback * 2 + 10), datetime.min.time())
        to = datetime.combine(today, datetime.min.time())
        for nm in need:
            try:
                token = self.universe.nse_token(nm)
                if not token:
                    continue
                candles = self.broker.get_historical_data(token, frm, to, "day") or []
                vols = [float(c.get("volume", 0) or 0) for c in candles
                        if c.get("date") and str(c["date"])[:10] != today.isoformat()]
                vols = [v for v in vols[-lookback:] if v > 0]
                if vols:
                    self._avgvol[nm] = (today, sum(vols) / len(vols))
            except Exception as exc:
                logger.debug("avgvol %s failed: %s", nm, exc)

    # ── one stock → a scanner row ──
    def _row(self, name: str, q: dict, cfg: dict, ts: datetime) -> Optional[dict]:
        if not q:
            return None
        ltp = float(q.get("last_price", 0) or 0)
        ohlc = q.get("ohlc") or {}
        prev_close = float(ohlc.get("close", 0) or 0)
        volume = float(q.get("volume", q.get("volume_traded", 0)) or 0)
        buy_qty = float(q.get("buy_quantity", 0) or 0)
        sell_qty = float(q.get("sell_quantity", 0) or 0)
        vwap = float(q.get("average_price", 0) or 0) or None
        depth = q.get("depth") or {}
        bids, asks = depth.get("buy") or [], depth.get("sell") or []
        buy_depth = calc.depth_totals(bids)
        sell_depth = calc.depth_totals(asks)
        has_depth = bool(bids or asks)

        ratio = calc.safe_ratio(buy_qty, sell_qty)
        imb = calc.depth_imbalance(buy_depth, sell_depth)
        change_pct = calc.pct_change(ltp, prev_close)

        avg = self._avgvol.get(name)
        rvol = round(volume / avg[1], 2) if (avg and avg[1] > 0 and volume > 0) else None

        prev = self._hist[name][-1] if self._hist[name] else None
        buy_change_pct = calc.pct_change(buy_qty, prev["buy"]) if prev else None
        sell_change_pct = calc.pct_change(sell_qty, prev["sell"]) if prev else None

        hist_with_current = list(self._hist[name]) + [{"ratio": ratio, "imbalance": imb, "score": None}]
        result = calc.compose_score({
            "ratio": ratio, "imbalance": imb, "change_pct": change_pct, "rvol": rvol,
            "ltp": ltp, "vwap": vwap, "buy_change_pct": buy_change_pct,
            "sell_change_pct": sell_change_pct, "history": hist_with_current,
            "availability": {"depth": has_depth},
        }, cfg)

        self._hist[name].append({"ts": ts.strftime("%H:%M:%S"), "ratio": ratio, "imbalance": imb,
                                 "buy": buy_qty, "sell": sell_qty, "price": ltp,
                                 "score": result["score"]})
        # trim to configured cap
        while len(self._hist[name]) > int(cfg["history_cap"]):
            self._hist[name].popleft()

        imb_pct = round(imb * 100, 1) if imb is not None else None
        ratio_disp = None if ratio is None else ("∞" if ratio == float("inf") else round(ratio, 2))
        return {
            "symbol": name, "ltp": round(ltp, 2), "prev_close": round(prev_close, 2),
            "change": round(ltp - prev_close, 2) if prev_close else None, "change_pct": change_pct,
            "buy_qty": int(buy_qty), "sell_qty": int(sell_qty),
            "ratio": ratio_disp, "ratio_raw": None if ratio in (None, float("inf")) else round(ratio, 4),
            "ratio_label": calc.classify_ratio(ratio, cfg),
            "buy_depth": int(buy_depth), "sell_depth": int(sell_depth),
            "imbalance": imb, "imbalance_pct": imb_pct,
            "imbalance_label": calc.classify_imbalance_pct(imb_pct, cfg),
            "volume": int(volume), "rvol": rvol, "rvol_label": calc.classify_rvol(rvol, cfg),
            "vwap": round(vwap, 2) if vwap else None,
            "vwap_dist": round((ltp - vwap) / vwap * 100, 2) if (vwap and ltp) else None,
            "vwap_status": "N/A" if not vwap else ("ABOVE VWAP" if ltp >= vwap else "BELOW VWAP"),
            "buy_change_pct": buy_change_pct, "sell_change_pct": sell_change_pct,
            "score": result["score"], "signal": result["signal"], "emoji": result["emoji"],
            "confidence": result["confidence"], "status": result["status"], "trend": result["trend"],
            "persistence": result["persistence"], "breakdown": result["breakdown"],
            "has_depth": has_depth,
        }

    def _passes(self, r: dict, cfg: dict) -> bool:
        if cfg["min_price"] and (r["ltp"] or 0) < cfg["min_price"]:
            return False
        if cfg["min_volume"] and (r["volume"] or 0) < cfg["min_volume"]:
            return False
        if cfg["require_vwap_above"] and r["vwap"] and r["ltp"] < r["vwap"]:
            return False
        return True

    @staticmethod
    def _summary(rows: list[dict]) -> dict:
        def band(lo, hi):
            return sum(1 for r in rows if r["score"] is not None and lo <= r["score"] < hi)
        return {
            "scanned": len(rows),
            "strong_demand": sum(1 for r in rows if (r["score"] or 0) >= 70),
            "moderate_demand": band(60, 70),
            "neutral": band(45, 60),
            "strong_supply": sum(1 for r in rows if r["score"] is not None and r["score"] < 30),
        }

    # ── main scan ──
    def scan(self, overrides=None, *, mode="all", symbols=None) -> dict:
        with self._lock:
            cfg = sanitize({**self.load_config(), **(overrides or {})})
            names, mode_label = self._resolve_names(mode, symbols, cfg)
            if not names:
                return {"status": "error", "message": "No stocks to scan"}
            quotes = self._quotes(names)
            self._ensure_avgvol(names, cfg)
            ts = datetime.now(IST)
            rows, missing = [], []
            for nm in names:
                q = quotes.get(f"NSE:{nm}")
                if not q:
                    missing.append(nm)
                    continue
                try:
                    row = self._row(nm, q, cfg, ts)
                    if row and self._passes(row, cfg):
                        rows.append(row)
                except Exception as exc:
                    logger.debug("demand_supply row %s failed: %s", nm, exc)
            rows.sort(key=lambda r: (r["score"] is not None, r["score"] or 0), reverse=True)
            for i, r in enumerate(rows, 1):
                r["rank"] = i
            top_n = int(cfg["top_n"])
            top_supply = sorted(rows, key=lambda r: (r["score"] if r["score"] is not None else 999))[:top_n]
            market = self.market_status()
            return {
                "status": "ok", "mode": mode_label, "market": market,
                "connected": bool(quotes), "last_update": ts.strftime("%H:%M:%S"),
                "summary": self._summary(rows), "rows": rows,
                "top_demand": rows[:top_n], "top_supply": top_supply,
                "missing": missing, "config": cfg,
                "generated_at": ts.strftime("%Y-%m-%d %H:%M:%S"),
            }

    # ── single-stock detailed research view ──
    def detail(self, symbol: str, overrides=None) -> dict:
        with self._lock:
            cfg = sanitize({**self.load_config(), **(overrides or {})})
            name = (symbol or "").strip().upper()
            if not name:
                return {"status": "error", "message": "No symbol"}
            key = f"NSE:{name}"
            try:
                q = (self.broker.get_quote([key]) or {}).get(key)
            except Exception as exc:
                return {"status": "error", "message": f"Quote failed: {exc}"}
            if not q:
                return {"status": "error", "message": f"{name}: not found / no quote"}
            self._ensure_avgvol([name], cfg)
            ts = datetime.now(IST)
            row = self._row(name, q, cfg, ts)
            depth = q.get("depth") or {}
            bids = [{"price": float(l.get("price", 0) or 0), "qty": int(l.get("quantity", 0) or 0),
                     "orders": int(l.get("orders", 0) or 0)} for l in (depth.get("buy") or [])[:5]]
            asks = [{"price": float(l.get("price", 0) or 0), "qty": int(l.get("quantity", 0) or 0),
                     "orders": int(l.get("orders", 0) or 0)} for l in (depth.get("sell") or [])[:5]]
            best_bid = bids[0]["price"] if bids else None
            best_ask = asks[0]["price"] if asks else None
            spread = round(best_ask - best_bid, 2) if (best_bid and best_ask) else None
            spread_pct = round(spread / best_bid * 100, 3) if (spread is not None and best_bid) else None
            timeline = [{"t": h["ts"], "score": h["score"], "ratio": (None if h["ratio"] in (None, float("inf")) else round(h["ratio"], 2)),
                         "imbalance": (round(h["imbalance"] * 100, 1) if h["imbalance"] is not None else None),
                         "price": h["price"]} for h in list(self._hist[name])]
            return {
                "status": "ok", "market": self.market_status(), "row": row,
                "order_book": {"bids": bids, "asks": asks, "best_bid": best_bid, "best_ask": best_ask,
                               "spread": spread, "spread_pct": spread_pct,
                               "total_bid": row["buy_depth"], "total_ask": row["sell_depth"],
                               "imbalance_pct": row["imbalance_pct"]},
                "timeline": timeline, "aggressive": {"available": False,
                    "note": "Aggressive buy/sell (tick-side) is not derivable from the Kite quote feed — excluded from scoring."},
                "config": cfg, "generated_at": ts.strftime("%Y-%m-%d %H:%M:%S"),
            }
