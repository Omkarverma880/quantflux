"""
Live chain dissection — pure functions over one snapshot, no broker calls.

Input rows: ``[{"strike": float, "ce": cell | None, "pe": cell | None}, ...]`` where a
cell carries ltp, vwap, oi, oi_chg (vs previous close, may be None), oi_since_open,
oi_day_high, volume, buy_qty, sell_qty, iv, delta, gamma, theta, vega, buildup.

The writer view used throughout: option WRITERS are the patient, well-capitalised
side. Heavy, growing put writing at a strike means sellers are betting spot stays
above it (support); heavy call writing means they bet it stays below (resistance).
Everything here DESCRIBES who is in control now. The 3-year study found that OI
positioning does not forecast direction to the close (see history.py), but it does
forecast which walls hold — so zones matter far more than the bias score.
"""
from __future__ import annotations

from math import sqrt, tanh, log
from typing import Optional

from research.oi_lab.indices import SESSION_MINUTES


def fmt_qty(v: Optional[float]) -> str:
    if v is None:
        return "—"
    a, s = abs(v), "-" if v < 0 else ""
    if a >= 1e7:
        return f"{s}{a / 1e7:.2f} Cr"
    if a >= 1e5:
        return f"{s}{a / 1e5:.1f} L"
    if a >= 1e3:
        return f"{s}{a / 1e3:.1f} K"
    return f"{s}{a:.0f}"


def fmt_signed_qty(v: Optional[float]) -> str:
    return "—" if v is None else ("+" if v >= 0 else "") + fmt_qty(v)


def _num(x, d=0.0) -> float:
    try:
        return float(x) if x is not None else d
    except (TypeError, ValueError):
        return d


def _chg(cell: Optional[dict]) -> Optional[float]:
    """OI change today: vs previous close when known, else since 09:20."""
    if not cell:
        return None
    if cell.get("oi_chg") is not None:
        return float(cell["oi_chg"])
    if cell.get("oi_since_open") is not None:
        return float(cell["oi_since_open"])
    return None


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


# ── totals, PCR, max pain ────────────────────────────────────────────
def totals(rows: list[dict]) -> dict:
    t = {"ce_oi": 0.0, "pe_oi": 0.0, "ce_oi_chg": 0.0, "pe_oi_chg": 0.0, "ce_vol": 0.0, "pe_vol": 0.0}
    known = {"ce": False, "pe": False}
    for r in rows:
        for side in ("ce", "pe"):
            c = r.get(side)
            if not c:
                continue
            t[f"{side}_oi"] += _num(c.get("oi"))
            t[f"{side}_vol"] += _num(c.get("volume"))
            ch = _chg(c)
            if ch is not None:
                t[f"{side}_oi_chg"] += ch
                known[side] = True
    t["pcr_oi"] = round(t["pe_oi"] / t["ce_oi"], 2) if t["ce_oi"] else None
    t["pcr_vol"] = round(t["pe_vol"] / t["ce_vol"], 2) if t["ce_vol"] else None
    t["pcr_chg"] = (round(t["pe_oi_chg"] / t["ce_oi_chg"], 2)
                    if known["ce"] and known["pe"] and t["ce_oi_chg"] > 0 and t["pe_oi_chg"] > 0 else None)
    t["oi_chg_known"] = known["ce"] and known["pe"]
    return t


def max_pain(rows: list[dict]) -> Optional[float]:
    strikes = [r["strike"] for r in rows]
    if not strikes:
        return None
    best, at = None, None
    for E in strikes:
        pain = sum(_num((r.get("ce") or {}).get("oi")) * max(0.0, E - r["strike"])
                   + _num((r.get("pe") or {}).get("oi")) * max(0.0, r["strike"] - E) for r in rows)
        if best is None or pain < best:
            best, at = pain, E
    return at


# ── per-strike writer battle ─────────────────────────────────────────
def _side_scales(rows: list[dict], side: str) -> tuple[float, float, float]:
    ois = [_num((r.get(side) or {}).get("oi")) for r in rows]
    chs = [_chg(r.get(side)) or 0.0 for r in rows]
    return (max(ois) if ois else 0.0) or 1.0, max([c for c in chs if c > 0], default=0.0) or 1.0, \
        -min([c for c in chs if c < 0], default=0.0) or 1.0


def writer_conviction(cell: Optional[dict], scales: tuple[float, float, float]) -> dict:
    """0–100: how firmly writers hold this strike (size, fresh adds, in-profit, not covering)."""
    if not cell:
        return {"score": 0.0, "fresh": 0.0, "profit": 0.0, "off_peak_pct": None}
    max_oi, max_add, max_cut = scales
    oi = _num(cell.get("oi"))
    ch = _chg(cell)
    share = oi / max_oi
    fresh = max(ch or 0.0, 0.0) / max_add
    cut = max(-(ch or 0.0), 0.0) / max_cut
    vwap, ltp = _num(cell.get("vwap")), _num(cell.get("ltp"))
    profit = _clip((vwap - ltp) / vwap / 0.25) if vwap > 0 and ltp > 0 else 0.0   # writers of today in profit
    peak = _num(cell.get("oi_day_high"))
    off_peak = (peak - oi) / peak if peak > 0 else 0.0
    score = 100 * _clip(0.50 * share + 0.30 * fresh - 0.20 * cut + 0.12 * profit - 0.25 * max(off_peak - 0.05, 0), 0, 1)
    return {"score": round(score, 1), "fresh": round(fresh, 3), "profit": round(profit, 3),
            "off_peak_pct": round(off_peak * 100, 1) if peak > 0 else None}


def battle(rows: list[dict], spot: float, step: float) -> list[dict]:
    ce_sc, pe_sc = _side_scales(rows, "ce"), _side_scales(rows, "pe")
    out = []
    for r in rows:
        k = r["strike"]
        ce, pe = r.get("ce"), r.get("pe")
        cw, pw = writer_conviction(ce, ce_sc), writer_conviction(pe, pe_sc)
        diff = pw["score"] - cw["score"]
        winner = "PUT_WRITERS" if diff > 10 else "CALL_WRITERS" if diff < -10 else "BALANCED"
        above, below = k > spot + step * 0.25, k < spot - step * 0.25
        if not above and not below:
            role, note = "BATTLEGROUND", "At the money — whoever wins this strike sets the next move."
        elif above and winner == "CALL_WRITERS":
            role, note = "RESISTANCE", "Call writers defending — they profit while spot stays below."
        elif below and winner == "PUT_WRITERS":
            role, note = "SUPPORT", "Put writers defending — they profit while spot stays above."
        elif above and winner == "PUT_WRITERS":
            role, note = "BULLISH_WRITERS", ("Put writers sit above spot — they are betting spot rises past this strike. "
                                             "If spot fails, their covering adds selling.")
        elif below and winner == "CALL_WRITERS":
            role, note = "BEARISH_WRITERS", ("Call writers sit below spot and are under water — they need spot back below. "
                                             "If spot holds, their covering adds buying.")
        else:
            role, note = "CONTESTED", "Neither side dominates."
        ce_oi, pe_oi = _num((ce or {}).get("oi")), _num((pe or {}).get("oi"))
        out.append({
            "strike": k, "winner": winner, "edge": round(abs(diff), 1), "role": role, "note": note,
            "ce_conviction": cw, "pe_conviction": pw,
            "pe_ce_ratio": round(pe_oi / ce_oi, 2) if ce_oi else None,
        })
    return out


# ── support / resistance zones ───────────────────────────────────────
def _zone_scores(rows: list[dict], side: str, spot: float, step: float) -> list[tuple[float, dict]]:
    other = "ce" if side == "pe" else "pe"
    max_oi, max_add, _ = _side_scales(rows, side)
    out = []
    for r in rows:
        k = r["strike"]
        if side == "pe" and k > spot + step * 0.5:
            continue
        if side == "ce" and k < spot - step * 0.5:
            continue
        c = r.get(side)
        if not c:
            continue
        oi, oo = _num(c.get("oi")), _num((r.get(other) or {}).get("oi"))
        add = max(_chg(c) or 0.0, 0.0)
        score = 0.55 * oi / max_oi + 0.30 * add / max_add + 0.15 * (oi / (oi + oo) if oi + oo else 0.5)
        peak = _num(c.get("oi_day_high"))
        if peak > 0 and oi < peak:
            score *= 1 - min(0.5, (peak - oi) / peak)        # writers already covering from today's peak
        out.append((score, r))
    return out


def zones(rows: list[dict], spot: float, step: float) -> dict:
    res = {}
    for side, name in (("pe", "support"), ("ce", "resistance")):
        scored = sorted(_zone_scores(rows, side, spot, step), key=lambda t: -t[0])
        if not scored:
            res[name] = []
            continue
        by_strike = {r["strike"]: s for s, r in scored}
        picked, used = [], set()
        for s, r in scored:
            if len(picked) == 2:
                break
            k = r["strike"]
            if k in used:
                continue
            band = [k] + [n for n in (k - step, k + step) if by_strike.get(n, 0) >= 0.7 * s and n not in used]
            used.update(band)
            used.update({k - step, k + step})
            c = r[side]
            oi, ch = _num(c.get("oi")), _chg(c)
            peak = _num(c.get("oi_day_high"))
            reasons = [f"{'Put' if side == 'pe' else 'Call'} OI {fmt_qty(oi)}"
                       + (f", {fmt_signed_qty(ch)} today" if ch is not None else "")]
            if peak > 0 and oi < peak * 0.93:
                reasons.append(f"{(peak - oi) / peak * 100:.0f}% below today's OI peak — writers covering, wall weakening")
            vwap, ltp = _num(c.get("vwap")), _num(c.get("ltp"))
            if vwap > 0 and ltp > 0:
                reasons.append(f"writers of today {'in profit' if ltp < vwap else 'under water'} "
                               f"(LTP {ltp:.1f} vs avg {vwap:.1f})")
            if c.get("buildup"):
                reasons.append(c["buildup"])
            picked.append({
                "anchor": k, "low": min(band), "high": max(band), "score": round(s, 3),
                "strength": round(min(100.0, 100 * s / 0.8), 1),     # 0.8 ≈ the biggest OI that is also the biggest add
                "label": "Strong" if s >= 0.7 else "Moderate" if s >= 0.45 else "Weak",
                "oi": oi, "oi_chg": ch, "distance_pts": round(abs(spot - k), 1), "reasons": reasons,
            })
        res[name] = picked
    return res


# ── order flow, gamma, expected move ────────────────────────────────
def flow(rows: list[dict], spot: float, step: float) -> dict:
    near = [r for r in rows if abs(r["strike"] - spot) <= step * 2.01]
    agg = {}
    for side in ("ce", "pe"):
        b = sum(_num((r.get(side) or {}).get("buy_qty")) for r in near)
        s = sum(_num((r.get(side) or {}).get("sell_qty")) for r in near)
        ltp = sum(_num((r.get(side) or {}).get("ltp")) for r in near)
        vwap = sum(_num((r.get(side) or {}).get("vwap")) for r in near)
        agg[side] = {"bid_qty": b, "ask_qty": s, "imbalance": round((b - s) / (b + s), 3) if b + s else 0.0,
                     "vs_vwap_pct": round((ltp - vwap) / vwap * 100, 2) if vwap else 0.0}
    ce, pe = agg["ce"], agg["pe"]
    call_side = "call buyers" if ce["imbalance"] > 0.08 else "call sellers" if ce["imbalance"] < -0.08 else None
    put_side = "put buyers" if pe["imbalance"] > 0.08 else "put sellers" if pe["imbalance"] < -0.08 else None
    parts = []
    if call_side:
        parts.append(f"{call_side} are heavier in the order book near the money ({ce['imbalance']:+.0%})")
    if put_side:
        parts.append(f"{put_side} are heavier ({pe['imbalance']:+.0%})")
    mom = ce["vs_vwap_pct"] - pe["vs_vwap_pct"]
    if abs(mom) >= 3:
        parts.append(("Calls trade above and puts below today's average price — buyers of upside winning the day"
                      if mom > 0 else "Puts trade above and calls below today's average price — buyers of downside winning the day"))
    return {"ce": ce, "pe": pe, "momentum_gap_pct": round(mom, 2),
            "text": "; ".join(parts) if parts else "Order book and premiums are balanced near the money."}


def gamma_profile(rows: list[dict], spot: float) -> dict:
    prof = []
    for r in rows:
        g = sum(_num((r.get(s) or {}).get("gamma")) * _num((r.get(s) or {}).get("oi")) for s in ("ce", "pe"))
        net = (_num((r.get("ce") or {}).get("gamma")) * _num((r.get("ce") or {}).get("oi"))
               - _num((r.get("pe") or {}).get("gamma")) * _num((r.get("pe") or {}).get("oi")))
        prof.append({"strike": r["strike"], "gex": g * spot * spot * 0.01, "net": net * spot * spot * 0.01})
    top = max((p["gex"] for p in prof), default=0.0) or 1.0
    for p in prof:
        p["share"] = round(p["gex"] / top, 3)
    magnet = max(prof, key=lambda p: p["gex"])["strike"] if prof else None
    return {"magnet": magnet, "profile": [{"strike": p["strike"], "share": p["share"],
                                           "net_sign": 1 if p["net"] > 0 else -1 if p["net"] < 0 else 0} for p in prof]}


def expected_move(spot: float, straddle: Optional[float], atm_iv: Optional[float], mins_left: float,
                  years_to_expiry: float) -> dict:
    out = {"straddle": round(straddle, 2) if straddle else None}
    if atm_iv and atm_iv > 0:
        sig = atm_iv / 100
        intraday = spot * sig * sqrt(max(mins_left, 1) / (SESSION_MINUTES * 252))
        out.update(intraday_sigma_pts=round(intraday, 1), upper_1s=round(spot + intraday, 1),
                   lower_1s=round(spot - intraday, 1),
                   expiry_sigma_pts=round(spot * sig * sqrt(max(years_to_expiry, 1e-6)), 1))
    if straddle:
        out.update(straddle_upper=round(spot + straddle, 1), straddle_lower=round(spot - straddle, 1))
    return out


# ── positioning bias ─────────────────────────────────────────────────
def bias(rows: list[dict], spot: float, step: float, tot: dict, zn: dict, fl: dict,
         mp: Optional[float], straddle: Optional[float], dte: int) -> dict:
    comps = []

    def add(name, value, weight, text):
        comps.append({"name": name, "value": round(_clip(value), 3), "weight": weight, "text": text})

    if tot.get("pcr_oi"):
        add("Writers' positioning (PCR)", tanh(log(tot["pcr_oi"]) * 2.5), 0.15,
            f"PCR {tot['pcr_oi']:.2f} — {'more put writing (supportive)' if tot['pcr_oi'] > 1 else 'more call writing (capping)'}")
    if tot.get("oi_chg_known"):
        a, b = tot["pe_oi_chg"], tot["ce_oi_chg"]
        if abs(a) + abs(b) > 0:
            add("Fresh writing today", (a - b) / (abs(a) + abs(b)), 0.25,
                f"Put OI {fmt_signed_qty(a)} vs call OI {fmt_signed_qty(b)} today")
    add("Premium momentum", tanh(fl["momentum_gap_pct"] / 12), 0.20,
        f"Calls {fl['ce']['vs_vwap_pct']:+.1f}% vs puts {fl['pe']['vs_vwap_pct']:+.1f}% against today's average price")
    add("Order book pressure", (fl["ce"]["imbalance"] - fl["pe"]["imbalance"]), 0.10,
        f"Bid-side share: calls {fl['ce']['imbalance']:+.0%}, puts {fl['pe']['imbalance']:+.0%}")
    sup, res = (zn.get("support") or [None])[0], (zn.get("resistance") or [None])[0]
    if sup and res:
        up, dn = max(res["anchor"] - spot, 0.0), max(spot - sup["anchor"], 0.0)
        if up + dn > 0:
            add("Room between walls", (up - dn) / (up + dn), 0.10,
                f"{up:.0f} pts to resistance {res['anchor']:g}, {dn:.0f} pts to support {sup['anchor']:g}")
    bull = bear = 0.0
    for r in rows:
        for side in ("ce", "pe"):
            c = r.get(side) or {}
            w = abs(_chg(c) or 0.0)
            bu = c.get("buildup")
            if not bu or not w:
                continue
            if (side == "pe" and bu in ("Short Buildup", "Long Unwinding")) or (side == "ce" and bu in ("Long Buildup", "Short Covering")):
                bull += w
            else:
                bear += w
    if bull + bear > 0:
        add("Buildup mix", (bull - bear) / (bull + bear), 0.10,
            "Strike-by-strike buildups weighted by OI change")
    if mp and straddle:
        add("Pull to max pain", tanh((mp - spot) / straddle), 0.10 if dte <= 1 else 0.04,
            f"Max pain {mp:g} is {mp - spot:+.0f} pts away")
    wsum = sum(c["weight"] for c in comps) or 1.0
    score = round(100 * sum(c["value"] * c["weight"] for c in comps) / wsum, 1)
    label = ("Strong bullish" if score >= 35 else "Bullish" if score >= 12 else "Neutral" if score > -12
             else "Bearish" if score > -35 else "Strong bearish")
    return {"score": score, "label": label, "components": comps}


def regime(zn: dict, spot: float, em: dict, probs: Optional[dict]) -> dict:
    """Range vs breakout read. Uses the tested wall models when history is loaded."""
    sup, res = (zn.get("support") or [None])[0], (zn.get("resistance") or [None])[0]
    if probs and probs.get("inside"):
        p = probs["inside"]["p"]
        if p >= 0.45:
            return {"label": "Range day likely", "p_range": p,
                    "text": f"Historically {p:.0%} of sessions like this stayed between both walls until the close. "
                            "Favour fading the walls over chasing breakouts."}
        if p <= 0.15:
            return {"label": "Breakout risk high", "p_range": p,
                    "text": f"Only {p:.0%} of similar sessions kept both walls intact — expect at least one wall to be taken out."}
        return {"label": "Mixed — one wall likely to give", "p_range": p,
                "text": f"{p:.0%} of similar sessions held both walls. Trade the wall with the better odds, not both."}
    if sup and res and em.get("intraday_sigma_pts"):
        width = res["anchor"] - sup["anchor"]
        ratio = width / em["intraday_sigma_pts"]
        if ratio >= 2 and sup["label"] != "Weak" and res["label"] != "Weak":
            return {"label": "Range day likely", "p_range": None,
                    "text": f"Walls {width:.0f} pts apart ≈ {ratio:.1f}× the remaining 1σ move, both firm."}
        return {"label": "Breakout risk high" if ratio < 1 else "Mixed", "p_range": None,
                "text": f"Walls {width:.0f} pts apart ≈ {ratio:.1f}× the remaining 1σ move."}
    return {"label": "Unclear", "p_range": None, "text": "Not enough structure to call a regime."}


def narrative(spot: float, tot: dict, zn: dict, fl: dict, mp: Optional[float], em: dict, bt: list[dict],
              gm: dict, bs: dict, rg: dict, shifts: list[str]) -> list[str]:
    out = []
    if tot.get("oi_chg_known"):
        a, b = tot["pe_oi_chg"], tot["ce_oi_chg"]
        if a > 0 and (b <= 0 or a > 1.25 * b):
            out.append(f"Put writers are in control today: put OI {fmt_signed_qty(a)} vs call OI {fmt_signed_qty(b)}"
                       + (f" ({a / b:.1f}×)" if b > 0 else "") + ". Sellers are underwriting the downside — supportive.")
        elif b > 0 and (a <= 0 or b > 1.25 * a):
            out.append(f"Call writers are in control today: call OI {fmt_signed_qty(b)} vs put OI {fmt_signed_qty(a)}"
                       + (f" ({b / a:.1f}×)" if a > 0 else "") + ". Sellers are capping the upside — heavy overhead.")
        else:
            out.append(f"Writers are adding on both sides (calls {fmt_signed_qty(b)}, puts {fmt_signed_qty(a)}) — a tug of war, typical of a range.")
    sup, res = (zn.get("support") or [None])[0], (zn.get("resistance") or [None])[0]
    if sup:
        out.append(f"Strongest support {sup['anchor']:g} ({sup['label'].lower()}): " + "; ".join(sup["reasons"]) + ".")
    if res:
        out.append(f"Strongest resistance {res['anchor']:g} ({res['label'].lower()}): " + "; ".join(res["reasons"]) + ".")
    if sup and res:
        out.append(f"Spot {spot:,.1f} is {spot - sup['anchor']:.0f} pts above support and {res['anchor'] - spot:.0f} pts below resistance.")
    out.extend(shifts)
    out.append(rg["text"])
    trapped = [b for b in bt if b["role"] in ("BEARISH_WRITERS", "BULLISH_WRITERS") and b["edge"] >= 20]
    for b in trapped[:2]:
        out.append(f"{b['strike']:g}: {b['note']}")
    out.append(fl["text"])
    if em.get("intraday_sigma_pts"):
        out.append(f"Options price a ±{em['intraday_sigma_pts']:.0f}-pt 1σ move for the rest of the session"
                   + (f"; the ATM straddle ({em['straddle']:.1f}) prices ±{em['straddle']:.0f} to expiry." if em.get("straddle") else "."))
    if gm.get("magnet"):
        out.append(f"Gamma is concentrated at {gm['magnet']:g} — price tends to stick near heavy-gamma strikes, most of all on expiry day.")
    if mp:
        out.append(f"Max pain {mp:g} ({mp - spot:+.0f} pts): where option buyers would collectively lose the most at expiry.")
    return out


def analyze(rows: list[dict], spot: float, step: float, dte: int, mins_left: float, years_to_expiry: float,
            straddle: Optional[float], atm_iv: Optional[float], probs: Optional[dict] = None,
            shifts: Optional[list[str]] = None) -> dict:
    tot = totals(rows)
    mp = max_pain(rows)
    bt = battle(rows, spot, step)
    zn = zones(rows, spot, step)
    fl = flow(rows, spot, step)
    gm = gamma_profile(rows, spot)
    em = expected_move(spot, straddle, atm_iv, mins_left, years_to_expiry)
    bs = bias(rows, spot, step, tot, zn, fl, mp, straddle, dte)
    rg = regime(zn, spot, em, probs)
    return {"totals": tot, "max_pain": mp, "battle": bt, "zones": zn, "flow": fl, "gamma": gm,
            "expected_move": em, "bias": bs, "regime": rg,
            "narrative": narrative(spot, tot, zn, fl, mp, em, bt, gm, bs, rg, shifts or [])}
