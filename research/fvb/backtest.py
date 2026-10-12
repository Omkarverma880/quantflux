"""
The backtest engine, and the variant comparison that is the whole point of it.

Five variants, each adding one thing to the last, so the comparison answers a question rather
than producing a number:

    A   opening-range breakout, nothing else
    B   A + the participation filter
    C   B + the retest requirement
    D   C + five-minute trend agreement
    E   D + the optional regime filters

Every variant is also run with the firewall off, because a filter that merely trades less is
not a filter that works. A variant is only better if it keeps more of the profit than it keeps
of the trades, and the report puts both numbers side by side so that cannot be fudged.

Determinism and auditability: the same inputs produce the same trades, every declined signal is
counted under the reason that declined it, and every trade carries the measurements that let it
through. Nothing is sampled, shuffled or seeded.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Callable, Optional

import numpy as np
import pandas as pd

from core.logger import get_logger
from research.fvb import data as D
from research.fvb import execution as EX
from research.fvb import signals as SG
from research.fvb import structure as ST
from research.fvb.firewall import Firewall, FirewallParams

logger = get_logger("research.fvb.backtest")

VARIANTS = {
    "A": "opening-range breakout only",
    "B": "A plus the participation filter",
    "C": "B plus a retest requirement",
    "D": "C plus five-minute trend agreement",
    "E": "D plus the regime filters",
}


def variant_params(name: str, base_p: ST.Params, base_ep: SG.EntryParams,
                   participation: str) -> tuple[ST.Params, SG.EntryParams]:
    """Turn a variant letter into the two parameter objects it means."""
    p, ep = base_p, base_ep
    if name == "A":
        p = replace(p, participation=D.NONE, choppy_filter=False, require_htf_trend=False)
        ep = replace(ep, mode="confirmation", require_futures_agreement=False)
    elif name == "B":
        p = replace(p, participation=participation, choppy_filter=False, require_htf_trend=False)
        ep = replace(ep, mode="confirmation", require_futures_agreement=False)
    elif name == "C":
        p = replace(p, participation=participation, choppy_filter=False, require_htf_trend=False)
        ep = replace(ep, mode="retest", require_futures_agreement=False)
    elif name == "D":
        p = replace(p, participation=participation, choppy_filter=False, require_htf_trend=True)
        ep = replace(ep, mode="retest", require_futures_agreement=True)
    elif name == "E":
        p = replace(p, participation=participation, choppy_filter=True, require_htf_trend=True)
        ep = replace(ep, mode="retest", require_futures_agreement=True)
    return p, ep


def _chain_for_day(opt_day: pd.DataFrame) -> Optional[dict]:
    """One session's option chain, pivoted to minute-indexed frames the executor can read."""
    if opt_day.empty:
        return None
    piv = {}
    for field_ in ("open", "high", "low", "close", "volume"):
        piv[field_] = opt_day.pivot_table(index="minute", columns=["option_type", "strike"],
                                          values=field_, aggfunc="last")
    cl = piv["close"]
    printed = cl.notna()
    cl = cl.ffill()
    return {"op": piv["open"].where(piv["open"].notna()).ffill().fillna(cl),
            "hi": piv["high"].ffill(), "lo": piv["low"].ffill(),
            "cl": cl, "vol": piv["volume"], "printed": printed,
            "expiry": opt_day["expiry_date"].iloc[0]}


def _option_volume_series(opt_day: pd.DataFrame, spot_day: pd.DataFrame) -> pd.Series:
    """Traded option volume near the money, as its own participation source.

    This is real exchange volume on real contracts. It is NOT futures volume and is never
    presented as such — it measures option participation, which is a different thing that
    happens to be available.
    """
    if opt_day.empty or spot_day.empty:
        return pd.Series(dtype=float)
    ref = spot_day.set_index("minute")["close"]
    atm = (ref / D.STRIKE_STEP).round() * D.STRIKE_STEP
    near = opt_day.merge(atm.rename("atm"), left_on="minute", right_index=True, how="left")
    near = near[(near["strike"] - near["atm"]).abs() <= 2 * D.STRIKE_STEP]
    if near.empty:
        return pd.Series(dtype=float)
    return near.groupby("minute")["volume"].sum()


@dataclass
class Config:
    start: str = "2023-09-18"
    end: str = "2026-09-11"
    timeframe: int = 5
    participation: str = D.OPTIONS
    firewall: bool = True
    variant: str = "B"
    params: ST.Params = field(default_factory=lambda: ST.P)
    entry: SG.EntryParams = field(default_factory=lambda: SG.EP)
    execution: EX.ExecParams = field(default_factory=lambda: EX.XP)
    firewall_params: FirewallParams = field(default_factory=lambda: FirewallParams())

    def as_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "timeframe": self.timeframe,
                "participation": self.participation, "firewall": self.firewall,
                "variant": self.variant, "params": self.params.as_dict(),
                "entry": self.entry.as_dict(), "execution": self.execution.as_dict(),
                "firewall_params": self.firewall_params.as_dict()}


def run(cfg: Config, progress: Optional[Callable[[str], None]] = None) -> dict:
    """Walk the range month by month, session by session, bar by bar."""
    say = progress or (lambda _m: None)

    # A range outside the stored data must say so. Reporting "no trade passed every rule"
    # when nothing was ever evaluated blames the strategy for a gap in the data, and sends
    # whoever reads it hunting a bug that is not there.
    inv = D.inventory()
    have = inv.get("spot") or {}
    if not have.get("available"):
        return {"status": "error", "trades": [], "declines": [], "quality": [],
                "firewall": [], "sessions": 0,
                "summary": {"trades": 0, "sessions": 0, "declined": 0,
                            "decline_reasons": {}, "net": 0.0,
                            "message": "No NIFTY spot data is stored at all."},
                "message": "No NIFTY spot data is stored at all.",
                "coverage": have}
    first, last = have.get("first"), have.get("last")
    if cfg.end < first or cfg.start > last:
        msg = (f"No data for {cfg.start} to {cfg.end}. The store holds "
               f"{first} to {last}, so nothing in your range exists yet — this is a data "
               f"gap, not a result. Pull fresh candles in the Data Ingestion Lab, or pick a "
               f"range inside {first} to {last}.")
        return {"status": "no_data", "trades": [], "declines": [], "quality": [],
                "firewall": [], "sessions": 0,
                "summary": {"trades": 0, "sessions": 0, "declined": 0,
                            "decline_reasons": {}, "net": 0.0, "message": msg},
                "message": msg, "coverage": {"first": first, "last": last},
                "requested": {"start": cfg.start, "end": cfg.end}}
    clipped = None
    if cfg.start < first or cfg.end > last:
        clipped = (f"Requested {cfg.start} to {cfg.end}; the store holds {first} to {last}, "
                   f"so only the overlap was tested.")

    p, ep = variant_params(cfg.variant, cfg.params, cfg.entry, cfg.participation)
    fp = cfg.firewall_params if cfg.firewall else FirewallParams(enabled=False,
                                                                 block_on_warning=False)
    trades, declines, quality, fw_summaries = [], [], [], []
    sessions = 0
    futures_note = None

    for a, b in D.months(cfg.start, cfg.end):
        say(f"{a[:7]} · {len(trades)} trades so far")
        spot = D.load_spot(a, b)
        if spot.empty:
            continue
        fut, why = D.load_futures(a, b)
        if why and futures_note is None:
            futures_note = why
        opt = D.load_options(a, b)
        vix = D.load_vix(a, b)

        for d, sg_day in spot.groupby("date", sort=True):
            sessions += 1
            q = D.session_quality(d, spot, fut, opt, vix)
            quality.append(q.as_dict())
            if not q.usable:
                declines.append({"date": str(d), "reason": SG.R_DATA,
                                 "detail": "; ".join(q.reasons)})
                continue
            opt_day = opt[opt["date"] == d] if not opt.empty else pd.DataFrame()
            chain = _chain_for_day(opt_day)
            if chain is None:
                declines.append({"date": str(d), "reason": "no option chain for this session"})
                continue

            fut_day = fut[fut["date"] == d] if not fut.empty else pd.DataFrame()
            joined, basis = D.align(sg_day, fut_day)
            if p.participation == D.OPTIONS:
                ov = _option_volume_series(opt_day, sg_day)
                joined = joined.merge(ov.rename("opt_volume"), left_on="minute",
                                      right_index=True, how="left")

            bars = D.resample(joined, cfg.timeframe) if cfg.timeframe > 1 else joined
            if p.participation == D.OPTIONS and cfg.timeframe > 1 and "opt_volume" in joined:
                agg = (joined.groupby(D.SESSION_OPEN +
                                      ((joined["minute"] - D.SESSION_OPEN) // cfg.timeframe)
                                      * cfg.timeframe)["opt_volume"].sum())
                bars = bars.merge(agg.rename("opt_volume"), left_on="minute",
                                  right_index=True, how="left", suffixes=("_drop", ""))
                bars = bars[[c for c in bars.columns if not c.endswith("_drop")]]
            bars = bars.reset_index(drop=True)
            atr_s = ST.atr(bars, p.atr_length)

            fw = Firewall(d, p, fp)
            risk = EX.DayRisk(cfg.execution)
            taken, busy_until = 0, -1

            for row in bars.itertuples():
                decision = int(row.minute) + cfg.timeframe - 1
                ok, why_risk = risk.may_trade(decision)
                if not ok:
                    continue
                sig, rec = SG.evaluate(bars, spot, d, decision, p, ep, fw,
                                       taken_today=taken,
                                       position_open=decision <= busy_until,
                                       atr_series=atr_s)
                if sig is None:
                    if rec.get("reason") not in (SG.R_SESSION, SG.R_NOT_BEYOND, SG.R_NO_LEVEL):
                        declines.append({"date": str(d), **rec})
                    continue
                if p.require_htf_trend and not _htf_agrees(joined, d, decision, sig.direction,
                                                           p.htf_minutes):
                    declines.append({"date": str(d), "reason": "five-minute trend disagrees",
                                     "minute": decision})
                    continue

                selling = str(cfg.execution.action).upper().startswith("S")
                fill = (EX.open_short if selling else EX.open_position)(
                    chain, d, sig, cfg.execution)
                if not fill.ok:
                    declines.append({"date": str(d), "reason": fill.reason,
                                     "minute": decision})
                    continue
                res = (EX.manage_short if selling else EX.manage)(
                    bars, chain, sig, fill, cfg.execution)
                risk.record(res["pnl"], res["exit_minute"])
                trades.append({
                    "date": str(d), "month": str(d)[:7], "variant": cfg.variant,
                    "direction": sig.direction,
                    "option_type": fill.option_type, "strike": fill.strike,
                    "expiry": fill.expiry, "dte": fill.dte,
                    "lots": fill.lots, "qty": fill.qty,
                    "level": round(sig.level, 2), "level_name": sig.level_name,
                    "signal_time": f"{decision // 60:02d}:{decision % 60:02d}",
                    "spot_entry": round(sig.spot_close, 2),
                    "underlying_stop": round(sig.stop_level, 2),
                    "underlying_target": round(sig.target_level, 2) if sig.target_level else None,
                    "rvol": sig.volume.get("rvol"),
                    "volume_state": sig.volume.get("state"),
                    "participation_source": sig.volume.get("source"),
                    "character": sig.character,
                    "futures_checked": sig.futures.get("checked"),
                    "firewall_state": sig.firewall_state, "event_id": sig.event_id,
                    "mode": sig.mode, **res})
                taken += 1
                busy_until = res["exit_minute"]

            # resolve whatever the firewall was still watching when the bell went
            for att in fw.attempts:
                if att.state in ("BREAKOUT_CANDIDATE", "WAITING_FOR_CONFIRMATION",
                                 "WAITING_FOR_RETEST"):
                    fw.follow(att, bars, bars["minute"].max())
            fw_summaries.append(fw.summary())

    say(f"done · {len(trades)} trades across {sessions} sessions")
    summary = summarise(trades, declines, sessions, cfg)
    if not trades and sessions == 0:
        summary["message"] = (
            f"No session in {cfg.start} to {cfg.end} could be loaded, so nothing was "
            f"evaluated. The store holds {first} to {last}.")
    if clipped:
        summary["coverage_note"] = clipped
    return {"status": "ok", "config": cfg.as_dict(), "trades": trades,
            "declines": declines, "quality": quality, "firewall": fw_summaries,
            "sessions": sessions, "futures_note": futures_note,
            "coverage": {"first": first, "last": last}, "coverage_note": clipped,
            "summary": summary}


def _htf_agrees(joined: pd.DataFrame, d: date, minute: int, direction: str,
                htf: int) -> bool:
    """Does the higher timeframe lean the same way, on candles that have closed?"""
    big = D.resample(joined, htf)
    done = big[big["minute"] + htf - 1 <= minute]
    if len(done) < 2:
        return False
    last = done.iloc[-1]
    up = float(last["close"]) > float(last["open"])
    return up if direction == SG.BULL else (not up)


def summarise(trades: list, declines: list, sessions: int, cfg: Config) -> dict:
    t = pd.DataFrame(trades)
    reasons = (pd.Series([x.get("reason") for x in declines]).value_counts().to_dict()
               if declines else {})
    if t.empty:
        # A configuration that took nothing is a real result, not an absent one, so it reports
        # the same keys as any other run. Returning a short dict here made "no trades"
        # indistinguishable from "this field does not exist" everywhere downstream.
        return {"trades": 0, "sessions": sessions, "declined": len(declines),
                "decline_reasons": reasons, "net": 0.0, "gross_profit": 0.0,
                "gross_loss": 0.0, "charges": 0.0, "win_rate": None, "avg_win": 0.0,
                "avg_loss": 0.0, "expectancy": 0.0, "profit_factor": None, "t_stat": None,
                "max_drawdown": 0.0, "longest_losing_streak": 0, "avg_hold_min": 0,
                "exits": {}, "by_direction": {}, "by_option_type": {}, "monthly": [],
                "green_months": 0, "months": 0, "ambiguous_bars": 0, "by_hour": [],
                "participation_source": cfg.participation,
                "message": ("No trade passed every rule in this configuration."
                            if sessions else
                            "No session could be loaded for this range, so no rule was "
                            "applied — this is a data gap, not a result.")}
    pnl = t["pnl"]
    w, l = pnl[pnl > 0], pnl[pnl <= 0]
    eq = pnl.cumsum()
    dd = eq - eq.cummax()
    streak = cur = 0
    for v in pnl:
        cur = cur + 1 if v <= 0 else 0
        streak = max(streak, cur)
    sd = float(pnl.std(ddof=1)) if len(pnl) > 1 else 0.0
    tstat = float(pnl.mean() / (sd / np.sqrt(len(pnl)))) if sd > 0 else None
    by_month = t.groupby("month")["pnl"].agg(["size", "sum"])
    out = {
        "trades": int(len(t)), "sessions": sessions, "declined": len(declines),
        "decline_reasons": reasons,
        "net": round(float(pnl.sum()), 2),
        "gross_profit": round(float(w.sum()), 2), "gross_loss": round(float(l.sum()), 2),
        "charges": round(float(t["charges"].sum()), 2),
        "win_rate": round(float((pnl > 0).mean() * 100), 1),
        "avg_win": round(float(w.mean()), 2) if len(w) else 0.0,
        "avg_loss": round(float(l.mean()), 2) if len(l) else 0.0,
        "expectancy": round(float(pnl.mean()), 2),
        "profit_factor": round(float(w.sum() / -l.sum()), 2) if l.sum() < 0 else None,
        "t_stat": round(tstat, 2) if tstat is not None else None,
        "max_drawdown": round(float(dd.min()), 2),
        "longest_losing_streak": int(streak),
        "avg_hold_min": int(t["held_min"].mean()),
        "exits": {k: int(v) for k, v in t["exit_reason"].value_counts().items()},
        "by_direction": {k: {"trades": int(g["pnl"].size), "net": round(float(g["pnl"].sum()), 2),
                             "win_rate": round(float((g["pnl"] > 0).mean() * 100), 1)}
                         for k, g in t.groupby("direction")},
        "by_option_type": {k: {"trades": int(g["pnl"].size),
                               "net": round(float(g["pnl"].sum()), 2)}
                           for k, g in t.groupby("option_type")},
        "monthly": [{"month": m, "trades": int(r["size"]), "net": round(float(r["sum"]), 2)}
                    for m, r in by_month.iterrows()],
        "green_months": int((by_month["sum"] > 0).sum()), "months": int(len(by_month)),
        "ambiguous_bars": int(t["ambiguous_bar"].sum()) if "ambiguous_bar" in t else 0,
        "participation_source": cfg.participation,
        "caveats": [
            "Only minute OHLC exists, so an intrabar fill cannot be reconstructed. Where a bar "
            "touched both the stop and the target, the stop was taken.",
            "Costs are Zerodha's published charges plus explicit per-fill slippage; "
            "round_trip returns charges and is subtracted, never added.",
        ],
    }
    if "time_bucket" not in t:
        t["time_bucket"] = t["signal_time"].str.slice(0, 2) + ":00"
    out["by_hour"] = [{"hour": h, "trades": int(g["pnl"].size),
                       "net": round(float(g["pnl"].sum()), 2)}
                      for h, g in t.groupby("time_bucket")]
    return out


def _verdict(net_off, net_on, kept_trades, kept_profit) -> str:
    """What the firewall actually did, stated so a losing variant cannot look like a win."""
    if net_on is None or net_off is None:
        return "not comparable — one side produced no result"
    if net_on == net_off:
        return "the firewall changed nothing"
    better = net_on > net_off
    if net_off <= 0 and net_on <= 0:
        return ("the loss shrank, but the variant still loses money"
                if better else "the loss grew; the firewall removed better-than-average trades")
    if net_off <= 0 < net_on:
        return "the firewall turned a losing variant profitable"
    if net_on <= 0 < net_off:
        return "the firewall turned a profitable variant into a loss"
    if kept_profit is not None and kept_profit > kept_trades:
        return ("the firewall kept more profit than trades — it removed worse-than-average "
                "trades")
    return "the firewall removed trades without improving what was left"


def compare(cfg: Config, variants: tuple = ("A", "B", "C", "D", "E"),
            with_and_without_firewall: bool = True,
            progress: Optional[Callable[[str], None]] = None) -> dict:
    """Run every variant, with the firewall on and off, and lay the results side by side."""
    say = progress or (lambda _m: None)
    rows = []
    for v in variants:
        for fw_on in ((True, False) if with_and_without_firewall else (cfg.firewall,)):
            say(f"variant {v}, firewall {'on' if fw_on else 'off'}")
            r = run(replace(cfg, variant=v, firewall=fw_on))
            s = r["summary"]
            rows.append({"variant": v, "label": VARIANTS[v], "firewall": fw_on,
                         "trades": s.get("trades", 0), "net": s.get("net"),
                         "win_rate": s.get("win_rate"), "profit_factor": s.get("profit_factor"),
                         "t_stat": s.get("t_stat"), "max_drawdown": s.get("max_drawdown"),
                         "expectancy": s.get("expectancy"),
                         "declined": s.get("declined")})
    # what the firewall actually did, rather than how many trades it removed
    effect = []
    for v in variants:
        on = next((r for r in rows if r["variant"] == v and r["firewall"]), None)
        off = next((r for r in rows if r["variant"] == v and not r["firewall"]), None)
        if not on or not off or not off["trades"]:
            continue
        kept_trades = on["trades"] / off["trades"] if off["trades"] else 0
        net_on, net_off = on.get("net"), off.get("net")
        # A ratio of two negatives is meaningless here: -588 over -339 is 173%, which reads as
        # "kept more profit" when the loss actually grew. Improvement is the CHANGE in net, and
        # the share language only applies when there was profit to keep.
        improved = (net_on - net_off) if (net_on is not None and net_off is not None) else None
        kept_profit = ((net_on / net_off) if (net_on is not None and net_off and net_off > 0)
                       else None)
        effect.append({
            "variant": v, "trades_off": off["trades"], "trades_on": on["trades"],
            "net_off": net_off, "net_on": net_on,
            "kept_trades_pct": round(kept_trades * 100, 1),
            "kept_profit_pct": round(kept_profit * 100, 1) if kept_profit is not None else None,
            "improved_by": round(improved, 2) if improved is not None else None,
            "verdict": _verdict(net_off, net_on, kept_trades, kept_profit),
        })
    return {"status": "ok", "rows": rows, "firewall_effect": effect,
            "config": cfg.as_dict()}
