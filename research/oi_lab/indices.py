"""
Indices covered by the OI Lab.

The strike step is only a default: the live engine infers it from the strikes
actually listed around spot, so an exchange changing the grid never breaks a chain.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

INDICES = {
    "NIFTY":      {"label": "NIFTY 50",   "spot": "NSE:NIFTY 50",          "exch": "NFO", "step": 50},
    "BANKNIFTY":  {"label": "BANK NIFTY", "spot": "NSE:NIFTY BANK",        "exch": "NFO", "step": 100},
    "SENSEX":     {"label": "SENSEX",     "spot": "BSE:SENSEX",            "exch": "BFO", "step": 100},
    "FINNIFTY":   {"label": "FIN NIFTY",  "spot": "NSE:NIFTY FIN SERVICE", "exch": "NFO", "step": 50},
    "MIDCPNIFTY": {"label": "MIDCAP SEL", "spot": "NSE:NIFTY MID SELECT",  "exch": "NFO", "step": 25},
    "BANKEX":     {"label": "BANKEX",     "spot": "BSE:BANKEX",            "exch": "BFO", "step": 100},
}
VIX_KEY = "NSE:INDIA VIX"

SESSION_OPEN_MIN = 9 * 60 + 15
SESSION_CLOSE_MIN = 15 * 60 + 30
SESSION_MINUTES = SESSION_CLOSE_MIN - SESSION_OPEN_MIN      # 375
REF_MIN = 9 * 60 + 20          # "open" reference checkpoint: state after the first 5 minutes
CHECK_EVERY = 5                # checkpoint grid, minutes


def now_ist() -> datetime:
    """Naive IST wall clock. The server may run in UTC (Railway)."""
    return datetime.now(IST).replace(tzinfo=None)


def get(index: str) -> dict:
    key = (index or "NIFTY").upper()
    if key not in INDICES:
        raise ValueError(f"index must be one of {', '.join(INDICES)}")
    return {"key": key, **INDICES[key]}


def hhmm(minute: int) -> str:
    return f"{int(minute) // 60:02d}:{int(minute) % 60:02d}"
