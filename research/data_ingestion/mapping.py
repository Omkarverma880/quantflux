"""
Column recognition and contract parsing for uploaded market data.

Files come from many places — Kite/Data Downloader exports, broker terminals, vendors,
hand-made CSVs — so column names vary ("Date Time", "strike_price", "Open Interest",
"tradingsymbol" …). This module maps whatever arrives onto the Market Store's field
names, and when strike / option type / expiry are missing it reads them out of the
trading symbol. The result is a frame ``store.normalize_options`` / ``normalize_spot``
accept unchanged.
"""
from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

KNOWN_UNDERLYINGS = ["MIDCPNIFTY", "BANKNIFTY", "FINNIFTY", "NIFTYNXT50", "BANKEX", "SENSEX50", "SENSEX",
                     "NIFTY", "INDIAVIX"]           # longest names first: BANKNIFTY before NIFTY
SPOT_ALIASES = {"NIFTY 50": "NIFTY", "NIFTY50": "NIFTY", "NIFTY BANK": "BANKNIFTY", "NIFTY FIN SERVICE": "FINNIFTY",
                "NIFTY MID SELECT": "MIDCPNIFTY", "INDIA VIX": "INDIAVIX", "BSE SENSEX": "SENSEX"}

FIELDS = {
    "timestamp": ["timestamp", "datetime", "date_time", "time_stamp", "candle_time", "ts", "date", "time"],
    "open": ["open", "o", "open_price", "opening_price"],
    "high": ["high", "h", "high_price"],
    "low": ["low", "l", "low_price"],
    "close": ["close", "c", "close_price", "ltp", "last", "last_price", "closing_price"],
    "volume": ["volume", "vol", "v", "traded_volume", "qty", "traded_qty", "volume_traded"],
    "oi": ["oi", "open_interest", "openinterest", "open_int"],
    "iv": ["iv", "implied_volatility", "impl_vol"],
    "spot": ["spot", "spot_price", "underlying_price", "underlying_value", "index_price", "underlying_ltp"],
    "strike": ["strike", "strike_price", "strikeprice", "strk", "strike_pr"],
    "option_type": ["option_type", "optiontype", "opt_type", "type", "right", "call_put", "cp", "instrument_type", "option"],
    "expiry_date": ["expiry_date", "expiry", "expirydate", "expiry_dt", "exp_date", "exp"],
    "symbol": ["tradingsymbol", "trading_symbol", "symbol", "contract", "ticker", "instrument", "scrip"],
    "underlying": ["underlying", "name", "index", "underlying_symbol"],
}
REQUIRED = {"spot": ["timestamp", "open", "high", "low", "close"],
            "options": ["timestamp", "open", "high", "low", "close", "strike", "option_type", "expiry_date"]}
OPTION_FIELDS = ["timestamp", "open", "high", "low", "close", "volume", "oi", "iv", "spot",
                 "strike", "option_type", "expiry_date", "symbol", "underlying"]
SPOT_FIELDS = ["timestamp", "open", "high", "low", "close", "volume"]

_MONTHS = {m.upper(): i for i, m in enumerate(calendar.month_abbr) if m}
_WEEKLY_MONTH = {**{str(i): i for i in range(1, 10)}, "O": 10, "N": 11, "D": 12}

# NIFTY2591825000CE (weekly: YY, month code 1-9/O/N/D, DD) · NIFTY25SEP25000CE (monthly)
_RE_WEEKLY = re.compile(r"^(?P<und>[A-Z]+?)(?P<yy>\d{2})(?P<m>[1-9OND])(?P<dd>\d{2})(?P<strike>\d+(?:\.\d+)?)(?P<typ>CE|PE)$")
_RE_MONTHLY = re.compile(r"^(?P<und>[A-Z]+?)(?P<yy>\d{2})(?P<mon>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)"
                         r"(?P<strike>\d+(?:\.\d+)?)(?P<typ>CE|PE)$")
# NIFTY 04-Aug-2026 24300 CE · NIFTY_18SEP25_25000_PE · SENSEX 18 Sep 2025 82000 CALL
_RE_SPACED = re.compile(r"^(?P<und>[A-Z]+)[\s_\-]+(?P<d>\d{1,2})[\s_\-]?(?P<mon>[A-Z]{3})[A-Z]*[\s_\-]?(?P<y>\d{2,4})"
                        r"[\s_\-]+(?P<strike>\d+(?:\.\d+)?)[\s_\-]*(?P<typ>CE|PE|CALL|PUT|C|P)$")


def norm_col(c) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(c).strip().lower()).strip("_")


def guess_mapping(columns: list[str]) -> dict:
    """Store field → source column, by exact alias match on normalised names (first alias wins)."""
    normed = {norm_col(c): c for c in columns}
    out, used = {}, set()
    for field, aliases in FIELDS.items():
        for a in aliases:
            src = normed.get(a)
            if src is not None and src not in used:
                out[field] = src
                used.add(src)
                break
    # a lone 'date' plus a lone 'time' column are one timestamp, joined at apply time
    if "date" in normed and "time" in normed and out.get("timestamp") in (normed["date"], normed["time"]):
        out["timestamp"] = normed["date"]
        out["time_part"] = normed["time"]
    return out


def underlying_from_text(text: str) -> Optional[str]:
    t = re.sub(r"[^A-Z0-9 ]", " ", str(text or "").upper())
    for alias, und in SPOT_ALIASES.items():
        if alias in t:
            return und
    compact = t.replace(" ", "")
    for u in KNOWN_UNDERLYINGS:
        if compact.startswith(u) or re.search(rf"(^|\s){u}(\s|\d|$)", t):
            return u
    for u in KNOWN_UNDERLYINGS:
        if u in compact:
            return u
    return None


def parse_symbol(sym: str) -> Optional[dict]:
    """Underlying, strike, CE/PE and expiry out of one trading symbol (expiry None for monthly codes)."""
    s = str(sym or "").strip().upper()
    if not s:
        return None
    m = _RE_WEEKLY.match(s)
    if m:
        try:
            exp = date(2000 + int(m["yy"]), _WEEKLY_MONTH[m["m"]], int(m["dd"]))
            return {"underlying": m["und"], "strike": float(m["strike"]), "option_type": m["typ"],
                    "expiry_date": exp, "expiry_kind": "weekly"}
        except ValueError:
            pass
    m = _RE_MONTHLY.match(s)
    if m:
        return {"underlying": m["und"], "strike": float(m["strike"]), "option_type": m["typ"],
                "expiry_date": None, "expiry_month": (2000 + int(m["yy"]), _MONTHS[m["mon"]]), "expiry_kind": "monthly"}
    m = _RE_SPACED.match(s)
    if m and m["mon"] in _MONTHS:
        y = int(m["y"]) + (2000 if len(m["y"]) == 2 else 0)
        try:
            exp = date(y, _MONTHS[m["mon"]], int(m["d"]))
        except ValueError:
            return None
        typ = {"CALL": "CE", "C": "CE", "PUT": "PE", "P": "PE"}.get(m["typ"], m["typ"])
        return {"underlying": m["und"], "strike": float(m["strike"]), "option_type": typ,
                "expiry_date": exp, "expiry_kind": "dated"}
    return None


def detect_kind(df: pd.DataFrame, mapping: dict) -> str:
    if "strike" in mapping and "option_type" in mapping:
        return "options"
    if "symbol" in mapping:
        sample = df[mapping["symbol"]].dropna().astype(str).head(50)
        if len(sample) and sample.map(lambda x: parse_symbol(x) is not None).mean() > 0.6:
            return "options"
    return "spot"


def detect_underlying(df: pd.DataFrame, mapping: dict, filename: str = "") -> Optional[str]:
    if "underlying" in mapping:
        v = df[mapping["underlying"]].dropna().astype(str).head(20)
        for x in v:
            u = underlying_from_text(x)
            if u:
                return u
    if "symbol" in mapping:
        for x in df[mapping["symbol"]].dropna().astype(str).head(50):
            p = parse_symbol(x)
            u = underlying_from_text(p["underlying"] if p else x)
            if u:
                return u
    return underlying_from_text(filename)


def _timestamps(df: pd.DataFrame, mapping: dict) -> pd.Series:
    ts = df[mapping["timestamp"]]
    if mapping.get("time_part"):
        ts = ts.astype(str).str.strip() + " " + df[mapping["time_part"]].astype(str).str.strip()
    if pd.api.types.is_numeric_dtype(ts):
        # epoch seconds or milliseconds
        unit = "ms" if ts.dropna().abs().median() > 1e11 else "s"
        t = pd.to_datetime(ts, unit=unit, errors="coerce", utc=True)
        return t.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return pd.to_datetime(ts, errors="coerce", dayfirst=False)


def _option_type(v: pd.Series) -> pd.Series:
    s = v.astype(str).str.upper().str.strip()
    return s.replace({"CALL": "CE", "C": "CE", "CALLS": "CE", "PUT": "PE", "P": "PE", "PUTS": "PE"})


def apply_mapping(df: pd.DataFrame, kind: str, mapping: dict,
                  symbol_last_day: Optional[pd.Series] = None) -> tuple[pd.DataFrame, list[str]]:
    """Rename/derive columns so the store normaliser accepts the frame. Returns (frame, notes).

    ``symbol_last_day`` (last traded day per symbol across the whole file) resolves monthly
    symbols' expiry correctly when a big file is processed in chunks."""
    notes: list[str] = []
    fields = OPTION_FIELDS if kind == "options" else SPOT_FIELDS
    out = pd.DataFrame(index=df.index)
    out["timestamp"] = _timestamps(df, mapping) if "timestamp" in mapping else pd.NaT
    for f in fields:
        if f == "timestamp" or f not in mapping:
            continue
        out[f] = df[mapping[f]]
    if kind == "spot":
        return out, notes

    if "option_type" in out:
        out["option_type"] = _option_type(out["option_type"])
    need = [f for f in ("strike", "option_type", "expiry_date") if f not in out or out[f].isna().all()]
    if need and "symbol" in out:
        uniq = out["symbol"].dropna().astype(str).unique()
        parsed = {s: parse_symbol(s) for s in uniq}
        ok = sum(1 for p in parsed.values() if p)
        notes.append(f"Read {', '.join(need)} from {ok:,} of {len(uniq):,} trading symbols.")
        sym = out["symbol"].astype(str)
        if "strike" in need:
            out["strike"] = sym.map(lambda s: (parsed.get(s) or {}).get("strike"))
        if "option_type" in need:
            out["option_type"] = sym.map(lambda s: (parsed.get(s) or {}).get("option_type"))
        if "expiry_date" in need:
            out["expiry_date"] = sym.map(lambda s: (parsed.get(s) or {}).get("expiry_date"))
            monthly = [s for s, p in parsed.items() if p and p.get("expiry_kind") == "monthly"]
            if monthly:
                # a monthly code names only the month; the contract's last traded day in the file is its expiry
                # when the file runs to expiry — otherwise the month's last weekday of the dominant weekly expiry
                last_day = (symbol_last_day if symbol_last_day is not None
                            else out.assign(_d=out["timestamp"].dt.date).groupby("symbol")["_d"].max())
                filled = 0
                for s in monthly:
                    y, mo = parsed[s]["expiry_month"]
                    ld = last_day.get(s)
                    if ld is not None and ld.year == y and ld.month == mo and ld.day >= 20:
                        exp = ld
                    else:
                        exp = _last_weekday(y, mo, 3 if date(y, mo, 1) < date(2025, 9, 1) else 1)
                    out.loc[out["symbol"].astype(str) == s, "expiry_date"] = exp
                    filled += 1
                notes.append(f"{filled} monthly symbol(s) carry no expiry day — used each contract's last traded "
                             "day in the file (or the month's last expiry weekday). Include an expiry column to be exact.")
    if "expiry_date" in out and out["expiry_date"].isna().any() and "strike" in out:
        missing = int(out["expiry_date"].isna().sum())
        if missing:
            notes.append(f"{missing:,} rows have no readable expiry and will be dropped.")
    return out, notes


def _last_weekday(y: int, m: int, weekday: int) -> date:
    d = date(y, m, calendar.monthrange(y, m)[1])
    while d.weekday() != weekday:
        d = d.replace(day=d.day - 1)
    return d


def missing_required(kind: str, mapping: dict, frame: Optional[pd.DataFrame] = None) -> list[str]:
    have = set(mapping)
    if frame is not None:
        have |= {c for c in frame.columns if frame[c].notna().any()}
    return [f for f in REQUIRED[kind] if f not in have]


def bar_minutes(ts: pd.Series) -> Optional[int]:
    t = pd.Series(pd.to_datetime(ts, errors="coerce").dropna().unique()).sort_values()
    d = t.diff().dt.total_seconds().div(60)
    d = d[(d > 0) & (d <= 1440)].round()
    return int(d.mode().iloc[0]) if len(d) else None


def jsonable(v):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v) if np.isfinite(v) else None
    if isinstance(v, (pd.Timestamp, date)):
        return v.isoformat()
    if pd.isna(v) if not isinstance(v, (list, dict, str)) else False:
        return None
    return v
