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
    # Entering at 15:00 beat entering at 15:15 in every stop/target combination tested on the
    # stored sessions — by 15:15 the move the auction is pricing is already under way.
    entry_from: int = 15 * 60 + 0
    entry_to: int = 15 * 60 + 5
    squareoff: int = 15 * 60 + 39       # one minute before the 15:40 derivative close
    # The auction prints in a second or two at 15:30. An order sent into that print is an order
    # sent into a vanishing book, so the position has to be ON well before it. This is the
    # margin that buys: the gap between the last acceptable entry and the auction itself.
    auction_at: int = 15 * 60 + 30
    min_lead_min: int = 20              # refuse to open with less than this much time left
    # ── what to buy ─────────────────────────────────────────────────
    # "strangle" buys a call AND a put together and lets the stop decide which one was wrong.
    # "cheap" is the original ₹1-lottery rule, kept so old runs still reproduce.
    structure: str = "strangle"
    moneyness: int = 0                  # strike steps from ATM: 0 = at the money,
                                        # +1 = one out, -1 = one in the money
    price_min: float = 0.90             # the "cheap" structure's band, ignored by a strangle
    price_max: float = 1.40
    sides: str = "both"                 # both | call | put
    per_side: int = 1                   # how many contracts per side
    budget: float = 20000.0             # total money at risk for the day, across both legs
    max_premium: float = 0.0            # skip the day if one leg costs more than this (0 = off)
    # ── when to let go ──────────────────────────────────────────────
    # The losing leg is cut at a fixed fraction of what it cost; the winner is left to run to
    # the target or to square-off. That asymmetry IS the trade: a defined loss against an
    # undefined gain.
    # A tighter stop beat a looser one on both axes in testing: more money overall AND a
    # smaller worst day, because the cost of being wrong is what you are really choosing here.
    stop_pct: float = 20.0              # cut a leg once it has lost this much of its premium
    # No target by default. The point of the trade is the one day the move keeps going; capping
    # the winner caps exactly the day you are there for. 0 = let it run to square-off.
    target_points: float = 0.0
    stop_points: float = 0.0            # an absolute points stop instead, when set
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


def lead_minutes(minute: int, p: Params = P) -> int:
    """How long before the auction print this minute is. Negative once it has gone."""
    return int(p.auction_at) - int(minute)


def too_late(minute: int, p: Params = P) -> bool:
    """Is there still enough room to get the position on before the print?

    Entering at 15:29 is not the same trade as entering at 15:00: the premium has already moved
    and the book thins out into the auction. Below the lead time the desk simply does not open.
    """
    return lead_minutes(minute, p) < int(p.min_lead_min)


def is_pair(p: Params = P) -> bool:
    """Is this one trade with two legs, rather than two independent bets?"""
    return str(p.structure or "strangle").lower() == "strangle"


def leg_stop(entry: float, p: Params = P) -> Optional[float]:
    """Where a leg is cut, in premium terms. Points win over percent when both are set."""
    if p.stop_points and p.stop_points > 0:
        return round(max(0.05, entry - p.stop_points), 2)
    if p.stop_pct and p.stop_pct > 0:
        return round(max(0.05, entry * (1.0 - p.stop_pct / 100.0)), 2)
    return None


def pair_outcome(legs: list[dict]) -> dict:
    """Read a finished pair the way the trade is actually meant to work.

    One leg is supposed to be stopped out for a known amount while the other runs. Saying so
    explicitly stops a profitable pair from being filed as "one win and one loss".
    """
    if not legs:
        return {}
    won = max(legs, key=lambda t: t.get("pnl", 0.0))
    lost = min(legs, key=lambda t: t.get("pnl", 0.0))
    net = sum(t.get("pnl", 0.0) for t in legs)
    return {"net": round(net, 2),
            "winner": won.get("option_type"), "winner_pnl": round(won.get("pnl", 0.0), 2),
            "loser": lost.get("option_type"), "loser_pnl": round(lost.get("pnl", 0.0), 2),
            "stopped": sum(1 for t in legs if t.get("exit_reason") == "STOP"),
            "legs": len(legs)}


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
    if str(p.structure or "strangle").lower() == "strangle":
        return _strangle(chain, spot, p)

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


def _strangle(chain: list[dict], spot: float, p: Params = P) -> dict:
    """A call and a put around the money, taken as one trade.

    A ₹1 option needs the index to travel a long way before it is worth anything. An at-the-money
    option is already moving with it, so the auction's push shows up in the premium immediately —
    which is the whole point of being there for it. The cost of being wrong is capped by the
    stop on whichever leg goes against you.
    """
    step = _step(chain, spot)
    atm = round(float(spot) / step) * step
    # negative is in the money: the call moves DOWN a strike and the put moves UP, which is
    # the mirror of going out. Clamping at zero used to throw that choice away.
    shift = int(p.moneyness) * step
    want = {"CE": atm + shift, "PE": atm - shift}   # shift < 0 puts both legs in the money

    picks, looked_at = [], {}
    for typ in wanted_types(p):
        cands = [c for c in chain
                 if str(c.get("option_type")).upper() == typ
                 and c.get("price") is not None and float(c["price"]) > 0
                 and float(c.get("volume") or 0) >= p.min_volume]
        target = want[typ]
        ranked = sorted(cands, key=lambda c: (abs(float(c["strike"]) - target),
                                              -float(c.get("volume") or 0)))
        looked_at[typ] = [{**c, "gap": round(abs(float(c["strike"]) - target), 2)} for c in ranked[:6]]
        if ranked:
            picks.append({**ranked[0], "gap": round(abs(float(ranked[0]["strike"]) - target), 2)})

    if p.max_premium and any(float(c["price"]) > p.max_premium for c in picks):
        return {"picks": [], "considered": looked_at,
                "why": f"a leg costs more than ₹{p.max_premium:g} — sat this one out"}

    where = ("at the money" if not shift
             else f"{abs(shift):g} points {'out of' if shift > 0 else 'in'} the money")
    return {"picks": picks, "considered": looked_at,
            "why": (f"a call and a put {where} around {atm:g}, each cut at "
                    + (f"−{p.stop_points:g} points" if p.stop_points
                       else f"−{p.stop_pct:g}% of its premium"))}


def _step(chain: list[dict], spot: float) -> float:
    """The strike grid this chain actually uses, read from the chain rather than assumed."""
    strikes = sorted({float(c["strike"]) for c in chain if float(c.get("strike") or 0) > 0})
    gaps = [b - a for a, b in zip(strikes, strikes[1:]) if b > a]
    if gaps:
        return min(gaps)
    return 100.0 if spot > 40000 else 50.0


def size(picks: list[dict], p: Params = P) -> list[dict]:
    """Split the budget across the picks, in whole lots.

    The budget is the whole risk: what it buys is however many lots the premium allows, and the
    most that can be lost is what was paid.
    """
    if not picks:
        return []
    pair = is_pair(p) and len(picks) > 1
    if pair:
        # equal lots on both legs: the pair is one position, not two sized separately
        per_lot = sum(float(c["price"]) + p.slippage_ticks * TICK for c in picks) * p.lot
        lots_each = int(p.budget // per_lot) if per_lot > 0 else 0
    share = p.budget / len(picks)
    out = []
    for c in picks:
        # the exchange's own lot size for this contract wins: LOT_SIZE is only a fallback, and a
        # stale constant would size a live order wrong the day an exchange revises its lot
        lot = int(c.get("lot_size") or 0) or p.lot
        entry = float(c["price"]) + p.slippage_ticks * TICK
        per_lot = entry * lot
        lots = lots_each if pair else (int(share // per_lot) if per_lot > 0 else 0)
        if lots < 1:
            out.append({**c, "lots": 0, "skipped": f"one lot costs ₹{per_lot:,.0f}, more than the ₹{share:,.0f} share"})
            continue
        out.append({**c, "entry": round(entry, 2), "lots": lots, "qty": lots * lot, "lot": lot,
                    "cost": round(entry * lots * lot, 2),
                    "target": (round(entry + p.target_points, 2)
                               if p.target_points and p.target_points > 0 else None),
                    "stop": leg_stop(entry, p)})
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
    if str(p.structure or "strangle").lower() != "strangle":
        return [
            f"Watch {p.index} options between {hhmm(p.entry_from)} and {hhmm(p.entry_to)}.",
            f"Buy the ones priced ₹{p.price_min:g}–₹{p.price_max:g}, {p.per_side} per side, "
            "nearest strike to the spot first.",
            f"Spend at most ₹{p.budget:,.0f}, in whole lots of {lot}. That is the entire risk.",
            f"Sell at {p.target_points:g} points of gain, and square off anything left at "
            f"{hhmm(p.squareoff)}.",
        ]
    where = ("at the money" if not p.moneyness
             else f"{abs(p.moneyness)} strike(s) {'out of' if p.moneyness > 0 else 'in'} the money")
    cut = (f"{p.stop_points:g} points" if p.stop_points else f"{p.stop_pct:g}% of what it cost")
    return [
        f"Between {hhmm(p.entry_from)} and {hhmm(p.entry_to)}, buy a {p.index} call AND a put "
        f"{where} — one trade, two legs.",
        "The closing auction pushes the index one way. Whichever leg is on the wrong side is cut; "
        "the other is left alone to run.",
        f"Each leg is cut once it has lost {cut}. That is the known cost of being wrong.",
        (f"The leg that works is sold at {p.target_points:g} points of gain, or at "
         f"{hhmm(p.squareoff)}, whichever comes first."
         if p.target_points and p.target_points > 0 else
         f"The leg that works is left uncapped and sold at {hhmm(p.squareoff)} — capping it "
         "would cap exactly the day this trade exists for."),
        f"At most ₹{p.budget:,.0f} across both legs, in whole lots of {lot}.",
        f"Nothing is opened with less than {p.min_lead_min} minutes to the {hhmm(p.auction_at)} "
        "auction — it prints in a second or two, and an order sent into it meets no book.",
        ("The day is skipped when a leg costs more than ₹%g — too expensive to be worth the move."
         % p.max_premium) if p.max_premium else
        "A pair is only worth taking when the move can beat both premiums plus the stop.",
    ]
