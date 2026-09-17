"""
The chain features — ONE definition, used by the 3-year history build and by the
live X-ray, so a live reading is always compared with history measured the same way.

Input: one row per (key, contract) with columns

    key       checkpoint id (anything hashable: a session+minute, or "now")
    cp        checkpoint minute of day (state AFTER that minute, e.g. 560 = 09:20)
    strike, typ ("CE"/"PE"), close, oi, oi_open, vol_cum, iv (vol points), spot, dte

``oi_open`` is the contract's OI at the 09:20 reference (else its first print that
day). ``vol_cum`` is volume traded so far today. Only strikes within ±WINDOW steps of
the money are used, because that is all the stored history contains.

Every feature is scale-free (ratios, or distances in ATM-straddle units), so a model
fitted on NIFTY can be read — with care — on other indices.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.oi_lab.indices import REF_MIN, SESSION_OPEN_MIN, SESSION_MINUTES

WINDOW = 5

FEATURES = [
    "pcr_log",        # log(put OI / call OI) in the window — writers' positioning
    "doi_bal",        # (put OI added − call OI added) / total added, since 09:20
    "vol_log",        # log(put volume / call volume) today
    "d_ce_wall",      # distance to biggest call-OI strike above, in straddles
    "d_pe_wall",      # distance to biggest put-OI strike below, in straddles
    "ce_wall_share",  # that call wall's share of window call OI (dominance)
    "pe_wall_share",
    "ret_open",       # spot move since 09:20, in opening straddles
    "decay",          # ATM straddle now / at 09:20 − 1
    "skew",           # IV(put 2 steps OTM) − IV(call 2 steps OTM), vol points
    "tod",            # fraction of session elapsed
    "dte",            # calendar days to expiry (capped)
]
LABELS = {
    "pcr_log": "Put/Call OI ratio", "doi_bal": "Fresh writing balance (PE − CE)",
    "vol_log": "Put/Call volume ratio", "d_ce_wall": "Room to call wall",
    "d_pe_wall": "Room to put wall", "ce_wall_share": "Call wall dominance",
    "pe_wall_share": "Put wall dominance", "ret_open": "Move since 09:20",
    "decay": "Straddle decay since 09:20", "skew": "Put − call IV skew",
    "tod": "Time of day", "dte": "Days to expiry",
}


def _pick(c: pd.DataFrame, mask, col: str, name: str) -> pd.Series:
    return c.loc[mask].drop_duplicates("key").set_index("key")[col].rename(name)


def chain_state(c: pd.DataFrame, step: float, window: int = WINDOW) -> pd.DataFrame:
    """Raw per-checkpoint chain state: totals, walls, ATM straddle, skew IVs."""
    c = c.copy()
    c["atm"] = (c["spot"] / step).round() * step
    c["k"] = ((c["strike"] - c["atm"]) / step).round().astype(int)
    c = c[c["k"].abs() <= window]
    if c.empty:
        return pd.DataFrame()
    c["doi"] = c["oi"] - c["oi_open"]
    ce, pe = c["typ"] == "CE", c["typ"] == "PE"
    base = c.groupby("key").agg(cp=("cp", "first"), spot=("spot", "first"),
                                atm=("atm", "first"), dte=("dte", "first"))
    for mask, p in ((ce, "ce"), (pe, "pe")):
        a = c[mask].groupby("key").agg(oi=("oi", "sum"), doi=("doi", "sum"), vol=("vol_cum", "sum"))
        base = base.join(a.add_prefix(f"{p}_"))
    c = c.sort_values(["key", "oi"], ascending=[True, False])
    base = base.join(_pick(c, ce & (c["k"] >= 0), "strike", "ce_wall"))
    base = base.join(_pick(c, ce & (c["k"] >= 0), "oi", "ce_wall_oi"))
    base = base.join(_pick(c, pe & (c["k"] <= 0), "strike", "pe_wall"))
    base = base.join(_pick(c, pe & (c["k"] <= 0), "oi", "pe_wall_oi"))
    base = base.join(_pick(c, ce & (c["k"] == 0), "close", "atm_ce"))
    base = base.join(_pick(c, pe & (c["k"] == 0), "close", "atm_pe"))
    iv = c["iv"].where(c["iv"] > 0.5)
    c = c.assign(iv=iv)
    base = base.join(_pick(c, ce & (c["k"] == 0), "iv", "iv_atm_ce"))
    base = base.join(_pick(c, pe & (c["k"] == 0), "iv", "iv_atm_pe"))
    base = base.join(_pick(c, ce & (c["k"] == 2), "iv", "iv_ce2"))
    base = base.join(_pick(c, pe & (c["k"] == -2), "iv", "iv_pe2"))
    base["straddle"] = base["atm_ce"] + base["atm_pe"]
    base["atm_iv"] = base[["iv_atm_ce", "iv_atm_pe"]].mean(axis=1)
    return base


def features_from_state(st: pd.DataFrame, spot_open: pd.Series | float,
                        straddle_open: pd.Series | float) -> pd.DataFrame:
    """Scale-free features from ``chain_state`` plus the 09:20 references."""
    f = pd.DataFrame(index=st.index)
    eps = 1.0
    f["pcr_log"] = np.log((st["pe_oi"] + eps) / (st["ce_oi"] + eps)).clip(-3, 3)
    f["doi_bal"] = ((st["pe_doi"] - st["ce_doi"])
                    / (st["pe_doi"].abs() + st["ce_doi"].abs() + eps)).clip(-1, 1)
    f["vol_log"] = np.log((st["pe_vol"] + eps) / (st["ce_vol"] + eps)).clip(-3, 3)
    strad = st["straddle"].where(st["straddle"] > 0)
    f["d_ce_wall"] = ((st["ce_wall"] - st["spot"]) / strad).clip(-1, 6)
    f["d_pe_wall"] = ((st["spot"] - st["pe_wall"]) / strad).clip(-1, 6)
    f["ce_wall_share"] = (st["ce_wall_oi"] / st["ce_oi"].where(st["ce_oi"] > 0)).clip(0, 1)
    f["pe_wall_share"] = (st["pe_wall_oi"] / st["pe_oi"].where(st["pe_oi"] > 0)).clip(0, 1)
    so = straddle_open if np.isscalar(straddle_open) else straddle_open.reindex(st.index)
    sp = spot_open if np.isscalar(spot_open) else spot_open.reindex(st.index)
    so = pd.Series(so, index=st.index).where(lambda s: s > 0)
    f["ret_open"] = ((st["spot"] - sp) / so).clip(-5, 5)
    f["decay"] = (st["straddle"] / so - 1).clip(-1, 3)
    f["skew"] = (st["iv_pe2"] - st["iv_ce2"]).clip(-15, 15)
    f["tod"] = ((st["cp"] - SESSION_OPEN_MIN) / SESSION_MINUTES).clip(0, 1)
    f["dte"] = st["dte"].clip(0, 7)
    return f


def opening_refs(st: pd.DataFrame, session: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Spot and ATM straddle at the 09:20 reference (else the first checkpoint) per session.

    ``session`` maps each state row (same index) to its session id."""
    s = st.assign(_session=session.reindex(st.index)).sort_values("cp")
    at_ref = s[s["cp"] >= REF_MIN].dropna(subset=["straddle"]).drop_duplicates("_session")
    spot_o = at_ref.set_index("_session")["spot"]
    strad_o = at_ref.set_index("_session")["straddle"]
    return (session.reindex(st.index).map(spot_o), session.reindex(st.index).map(strad_o))
