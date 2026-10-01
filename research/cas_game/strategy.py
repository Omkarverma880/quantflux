"""
CAS Game Play — the rule, in one place.

Since 3 August 2026 the cash market's closing price is discovered in a Closing Auction Session
(15:15–15:35) while index options keep trading until 15:40. In those minutes the options that are
already worth almost nothing — a rupee or so — can be repriced violently when the auction settles
the underlying somewhere the option chain did not expect.

The bet this strategy makes:

    find the options trading around ₹1, put a fixed, small amount of money on them, and sell if
    the premium gains the target (25 points by default). Anything else is squared off before the
    derivative close.

It is a lottery ticket by design: nearly every ticket expires worthless, and the whole stake is
expected to be lost on most days. The point of the fixed budget is that the loss is bounded and
the payoff, when the auction does dislocate a strike, is many times the stake.

Everything here is a pure function of a chain snapshot, so the backtest and the live desk make the
same choice from the same data.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

LOT_SIZE = {"NIFTY": 65, "BANKNIFTY": 30, "FINNIFTY": 65, "MIDCPNIFTY": 120, "SENSEX": 20}
TICK = 0.05


@dataclass(frozen=True)
class Params:
    index: str = "NIFTY"
    # the window, from the exchange's own schedule
    entry_from: int = 15 * 60 + 15      # CAS begins in the cash market
    entry_to: int = 15 * 60 + 35        # CAS closing price is struck by here
    squareoff: int = 15 * 60 + 39       # one minute before the 15:40 derivative close
    # what to buy
    price_min: float = 0.90             # "a rupee and a few paise"
    price_max: float = 1.40
    sides: str = "both"                 # both | call | put
    per_side: int = 1                   # how many contracts per side
    budget: float = 5000.0              # total money at risk for the day
    # when to let go
    target_points: float = 25.0         # premium points gained, not percent
    stop_points: float = 0.0            # 0 = no stop; the budget is the stop
    # execution
    slippage_ticks: int = 1             # a tick is 5 paise — on a ₹1 option that is 5%
    min_volume: int = 0                 # ignore contracts that have not traded at all
    expiry_only: bool = True            # the dislocation is an expiry-day event

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def lot(self) -> int:
        return LOT_SIZE.get(self.index.upper(), 65)


P = Params()


def hhmm(m: int) -> str:
    return f"{int(m) // 60:02d}:{int(m) % 60:02d}"


def wanted_types(p: Params = P) -> list[str]:
    return {"both": ["CE", "PE"], "call": ["CE"], "put": ["PE"]}.get(p.sides, ["CE", "PE"])


def rank(candidates: list[dict], spot: float, p: Params = P) -> list[dict]:
    """Order the ₹1 options by how likely they are to be the one that moves.

    Two things decide it. Distance from the spot, because the auction has to drag the underlying
    that far before the option is worth anything — the nearest strike needs the smallest move. And
    whether the contract is trading at all, because an option nobody has touched cannot be bought
    at the price on the screen. Volume breaks ties; distance decides.
    """
    out = []
    for c in candidates:
        gap = abs(float(c["strike"]) - spot)
        out.append({**c, "gap": round(gap, 2), "volume": float(c.get("volume") or 0)})
    out.sort(key=lambda c: (c["gap"], -c["volume"]))
    return out


def choose(chain: list[dict], spot: float, p: Params = P) -> dict:
    """Pick what to buy from one snapshot of the chain.

    ``chain`` rows need: option_type, strike, price (the ask you would pay), volume.
    Returns the picks and the ones that were considered, so the desk can show its working.
    """
    picks, looked_at = [], {}
    for typ in wanted_types(p):
        cands = [c for c in chain
                 if str(c.get("option_type")).upper() == typ
                 and c.get("price") is not None
                 and p.price_min <= float(c["price"]) <= p.price_max
                 and float(c.get("volume") or 0) >= p.min_volume]
        ranked = rank(cands, spot, p)
        looked_at[typ] = ranked
        picks.extend(ranked[:max(0, int(p.per_side))])
    return {"picks": picks, "considered": looked_at,
            "why": (f"options priced ₹{p.price_min:g}–₹{p.price_max:g}, nearest strike first"
                    + (f", {p.per_side} per side" if p.per_side != 1 else ""))}


def size(picks: list[dict], p: Params = P) -> list[dict]:
    """Split the budget across the picks, in whole lots.

    The budget is the whole risk: what it buys is however many lots the premium allows, and the
    most that can be lost is what was paid.
    """
    if not picks:
        return []
    share = p.budget / len(picks)
    out = []
    for c in picks:
        # the exchange's own lot size for this contract wins: LOT_SIZE is only a fallback, and a
        # stale constant would size a live order wrong the day an exchange revises its lot
        lot = int(c.get("lot_size") or 0) or p.lot
        entry = float(c["price"]) + p.slippage_ticks * TICK
        per_lot = entry * lot
        lots = int(share // per_lot) if per_lot > 0 else 0
        if lots < 1:
            out.append({**c, "lots": 0, "skipped": f"one lot costs ₹{per_lot:,.0f}, more than the ₹{share:,.0f} share"})
            continue
        out.append({**c, "entry": round(entry, 2), "lots": lots, "qty": lots * lot, "lot": lot,
                    "cost": round(entry * lots * lot, 2),
                    "target": round(entry + p.target_points, 2),
                    "stop": round(entry - p.stop_points, 2) if p.stop_points else None})
    return out


def exit_reason(price: float, position: dict, minute: int, p: Params = P) -> Optional[str]:
    """TARGET, STOP or the hard square-off — checked on every price we see."""
    if position.get("target") and price >= position["target"]:
        return "TARGET"
    if position.get("stop") and price <= position["stop"]:
        return "STOP"
    if minute >= p.squareoff:
        return "SQUAREOFF"
    return None


def describe(p: Params = P) -> list[str]:
    lot = p.lot
    return [
        f"Watch {p.index} options between {hhmm(p.entry_from)} and {hhmm(p.entry_to)} — the closing "
        "auction runs 15:15–15:35 in the cash market while index options trade on until 15:40.",
        f"Buy the ones priced ₹{p.price_min:g}–₹{p.price_max:g}"
        + (", calls and puts" if p.sides == "both" else f", {p.sides}s only")
        + f", {p.per_side} per side, nearest strike to the spot first.",
        f"Spend at most ₹{p.budget:,.0f} in total, in whole lots of {lot}. That is the entire risk.",
        f"Sell when the premium gains {p.target_points:g} points"
        + (f", or loses {p.stop_points:g}" if p.stop_points else " (no stop — the stake is the stop)")
        + f", and square off anything left at {hhmm(p.squareoff)}.",
        f"A fill is assumed {p.slippage_ticks} tick worse than the screen price, which on a ₹1 option "
        f"is {p.slippage_ticks * TICK / 1.0 * 100:.0f}% — cheap options have wide spreads.",
    ]
