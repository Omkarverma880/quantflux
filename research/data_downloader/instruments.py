"""
Instrument index for the Data Downloader — searchable, day-cached, read-only.

Built from the EXISTING Broker instrument dump (``broker.get_instruments``,
which is itself globally day-cached), so no new Zerodha connection and no
hardcoded symbol lists. Indexes indices, cash equities and futures for free-text
search; options are reached through underlying → expiry → strike selectors
(and free-text within an underlying) to avoid scanning the full ~50k option set.
"""
from __future__ import annotations

import threading
from datetime import date, datetime
from typing import Optional

from core.broker import Broker
from core.logger import get_logger
from research.data_downloader.constants import EXCHANGES

logger = get_logger("research.data_downloader.instruments")


def _parse_expiry(v) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str) and v:
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(v, fmt).date()
            except ValueError:
                continue
    return None


def _classify(inst: dict) -> Optional[str]:
    it = inst.get("instrument_type")
    seg = inst.get("segment") or ""
    if seg == "INDICES":
        return "index"
    if it == "EQ" and seg in ("NSE", "BSE"):
        return "equity"
    if it == "FUT":
        return "futures"
    if it in ("CE", "PE"):
        return "options"
    return None


class InstrumentIndex:
    def __init__(self, broker: Broker):
        self.broker = broker
        self._lock = threading.Lock()
        self._day: Optional[date] = None
        self._search: list[dict] = []          # index+equity+futures (searchable)
        self._token: dict[int, dict] = {}       # token → record (for resolve)
        self._fut_by_name: dict[str, list] = {}
        self._opt_by_name: dict[str, list] = {}

    def _rec(self, inst: dict, itype: str) -> dict:
        exp = _parse_expiry(inst.get("expiry"))
        return {
            "tradingsymbol": inst.get("tradingsymbol"),
            "symbol": inst.get("tradingsymbol"),
            "name": inst.get("name") or inst.get("tradingsymbol"),
            "exchange": inst.get("exchange"),
            "segment": inst.get("segment"),
            "instrument_token": int(inst["instrument_token"]),
            "instrument_type": itype,
            "expiry": exp.isoformat() if exp else None,
            "_expiry": exp,
            "strike": float(inst.get("strike", 0) or 0) or None,
            "option_type": inst.get("instrument_type") if itype == "options" else None,
            "lot_size": int(inst.get("lot_size", 0) or 0),
        }

    def _ensure(self) -> None:
        today = date.today()
        if self._day == today and self._search:
            return
        with self._lock:
            if self._day == today and self._search:
                return
            search, token, fut_by, opt_by = [], {}, {}, {}
            for ex in EXCHANGES:
                try:
                    dump = self.broker.get_instruments(ex)
                except Exception as exc:
                    logger.warning("instrument dump failed (%s): %s", ex, exc)
                    continue
                for inst in dump:
                    itype = _classify(inst)
                    if not itype:
                        continue
                    rec = self._rec(inst, itype)
                    if itype in ("index", "equity", "futures"):
                        search.append(rec)
                        token[rec["instrument_token"]] = rec
                    if itype == "futures":
                        fut_by.setdefault(rec["name"], []).append(rec)
                    elif itype == "options":
                        opt_by.setdefault(rec["name"], []).append(rec)
            self._search = search
            self._token = token
            self._fut_by_name = fut_by
            self._opt_by_name = opt_by
            self._day = today
            logger.info("Data Downloader index: %d searchable, %d fut names, %d opt names",
                        len(search), len(fut_by), len(opt_by))

    # ── search ──
    def search(self, q: str, itype: Optional[str] = None, exchange: Optional[str] = None,
               limit: int = 25) -> list[dict]:
        self._ensure()
        q = (q or "").strip().upper()
        if not q:
            return []
        pool = self._search
        prefix, substr = [], []
        for r in pool:
            if itype and itype != "all" and r["instrument_type"] != itype:
                continue
            if exchange and exchange != "ALL" and r["exchange"] != exchange:
                continue
            ts = (r["tradingsymbol"] or "").upper()
            nm = (r["name"] or "").upper()
            if ts.startswith(q) or nm.startswith(q):
                prefix.append(r)
            elif q in ts or q in nm:
                substr.append(r)
        # free-text option suggestions within a matched underlying (bounded)
        opts = []
        if (not itype or itype in ("all", "options")):
            for name, recs in self._opt_by_name.items():
                if name.upper().startswith(q):
                    opts.extend(sorted(recs, key=lambda r: (r["_expiry"] or date.max, r["strike"] or 0))[:8])
                    if len(opts) >= 16:
                        break
        prefix.sort(key=lambda r: (len(r["tradingsymbol"] or ""), r["tradingsymbol"] or ""))
        substr.sort(key=lambda r: (len(r["tradingsymbol"] or ""), r["tradingsymbol"] or ""))
        out, seen = [], set()
        for r in prefix + substr + opts:
            k = r["instrument_token"]
            if k in seen:
                continue
            seen.add(k)
            out.append({k: v for k, v in r.items() if not k.startswith("_")})
            if len(out) >= limit:
                break
        return out

    def resolve(self, token: int) -> Optional[dict]:
        self._ensure()
        r = self._token.get(int(token))
        return {k: v for k, v in r.items() if not k.startswith("_")} if r else None

    # ── derivatives selectors ──
    def expiries(self, name: str, kind: str = "all") -> list[str]:
        self._ensure()
        name = (name or "").strip().upper()
        recs = []
        if kind in ("all", "futures"):
            recs += self._fut_by_name.get(name, [])
        if kind in ("all", "options"):
            recs += self._opt_by_name.get(name, [])
        exps = sorted({r["_expiry"] for r in recs if r["_expiry"]})
        return [e.isoformat() for e in exps]

    def futures(self, name: str) -> list[dict]:
        self._ensure()
        recs = sorted(self._fut_by_name.get((name or "").strip().upper(), []),
                      key=lambda r: (r["_expiry"] or date.max))
        return [{k: v for k, v in r.items() if not k.startswith("_")} for r in recs]

    def strikes(self, name: str, expiry: str, option_type: Optional[str] = None) -> list[dict]:
        self._ensure()
        name = (name or "").strip().upper()
        exp = _parse_expiry(expiry)
        recs = [r for r in self._opt_by_name.get(name, []) if r["_expiry"] == exp
                and (not option_type or r["option_type"] == option_type)]
        recs.sort(key=lambda r: (r["strike"] or 0, r["option_type"]))
        return [{k: v for k, v in r.items() if not k.startswith("_")} for r in recs]
