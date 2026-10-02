"""
5 & 60 Minute Range — the rule itself.

Two opening ranges are measured from the index, not from any option:

    5-minute range    09:15–09:20     governs 09:20 → 10:15
    60-minute range   09:15–10:15     governs 10:15 → 14:30

A signal fires when the index crosses a level of whichever range is in force, and the trade is
taken in the option chain at a configurable strike (200 points in-the-money by default).

DIRECTION
---------
The written spec reads "cross above the range LOW → buy call" and "cross below the range HIGH →
buy put". Taken literally that is a reversal: price has to leave the range and come back, so a
failed breakdown buys calls and a failed breakout buys puts. The other common reading is a plain
breakout — above the high buys calls, below the low buys puts. Both are supported and ``mode``
chooses; the literal reading is the default so the spec is honoured as written.

Nothing here touches live data or the market store. It decides, and says why.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

LOT_SIZE = {"NIFTY": 65, "BANKNIFTY": 30, "FINNIFTY": 65, "MIDCPNIFTY": 120, "SENSEX": 20}
STRIKE_STEP = {"NIFTY": 50, "FINNIFTY": 50, "MIDCPNIFTY": 25, "BANKNIFTY": 100, "SENSEX": 100}
TICK = 0.05

SESSION_OPEN = 9 * 60 + 15


@dataclass(frozen=True)
class Params:
    index: str = "NIFTY"

    # ── the two ranges ───────────────────────────────────────────────
    range_a_min: int = 5                 # the opening range that governs the early session
    range_b_min: int = 60                # the wider range that governs the rest of the day
    switch_at: int = 10 * 60 + 15        # range A applies before this, range B after
    first_entry: int = 9 * 60 + 20       # nothing is taken before the 5-minute range exists
    last_entry: int = 14 * 60 + 30       # no new trade after this
    squareoff: int = 15 * 60 + 15        # anything still open is closed here

    # ── which way the cross is read ──────────────────────────────────
    mode: str = "reversal"               # reversal (as written) | breakout
    sides: str = "both"                  # both | call | put

    # ── the option that is bought ────────────────────────────────────
    moneyness: str = "ITM"               # ITM | ATM | OTM
    strike_offset: float = 200.0         # points away from spot; ignored when moneyness is ATM

    # ── exits, in premium points unless exit_on_index is set ─────────
    target_points: float = 25.0
    stop_points: float = 20.0
    exit_on_index: bool = False          # measure the 25/20 on the index instead of the premium
    max_hold_min: int = 0                # 0 = until target, stop or square-off

    # ── sizing and execution ─────────────────────────────────────────
    lots: int = 1
    max_trades_per_day: int = 3
    one_trade_per_level: bool = True     # do not re-enter the same level again and again
    slippage_ticks: int = 1
    min_premium: float = 1.0             # never buy a contract priced below this

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def lot(self) -> int:
        return LOT_SIZE.get(self.index.upper(), 65)

    @property
    def step(self) -> int:
        return STRIKE_STEP.get(self.index.upper(), 50)


P = Params()


def hhmm(minute: int) -> str:
    return f"{int(minute) // 60:02d}:{int(minute) % 60:02d}"


def range_in_force(minute: int, p: Params = P) -> str:
    """Which opening range governs this minute: "A" (the 5-minute) or "B" (the 60-minute)."""
    return "A" if minute < p.switch_at else "B"


def levels(bars, p: Params = P) -> dict:
    """High and low of each opening range, from index bars of one session.

    ``bars`` is any sequence of mappings carrying ``minute``, ``high`` and ``low``. A range is
    only returned once every one of its minutes has actually printed, so a half-formed range can
    never be traded — that would be reading the future.
    """
    out: dict = {"A": None, "B": None}
    for key, span in (("A", p.range_a_min), ("B", p.range_b_min)):
        end = SESSION_OPEN + span                      # exclusive: 09:15 + 5 covers 09:15..09:19
        inside = [b for b in bars if SESSION_OPEN <= int(b["minute"]) < end]
        if len(inside) < span:                         # the range is not complete yet
            continue
        out[key] = {"high": max(float(b["high"]) for b in inside),
                    "low": min(float(b["low"]) for b in inside),
                    "from": SESSION_OPEN, "to": end - 1, "minutes": span}
    return out


def signal(prev_close: float, close: float, lv: dict, p: Params = P) -> Optional[dict]:
    """Does this bar's close cross a level of the range in force?

    A cross needs both sides: the previous close on one side of the level and this close on the
    other. Touching a level is not crossing it.
    """
    if not lv:
        return None
    hi, lo = float(lv["high"]), float(lv["low"])
    up_through_low = prev_close <= lo < close
    down_through_high = prev_close >= hi > close
    up_through_high = prev_close <= hi < close
    down_through_low = prev_close >= lo > close

    if p.mode == "breakout":
        call_on, put_on, call_lv, put_lv = up_through_high, down_through_low, "high", "low"
    else:                                              # as written: reclaim the level
        call_on, put_on, call_lv, put_lv = up_through_low, down_through_high, "low", "high"

    if call_on and p.sides in ("both", "call"):
        at = hi if call_lv == "high" else lo
        return {"side": "CE", "level": call_lv, "level_price": at,
                "why": f"closed above the range {call_lv} {at:,.1f}"}
    if put_on and p.sides in ("both", "put"):
        at = hi if put_lv == "high" else lo
        return {"side": "PE", "level": put_lv, "level_price": at,
                "why": f"closed below the range {put_lv} {at:,.1f}"}
    return None


def strike_for(spot: float, side: str, p: Params = P) -> float:
    """The strike this signal buys.

    In-the-money means below spot for a call and above it for a put; out-of-the-money is the
    mirror. The offset is in index points and is snapped to the index's own strike grid.
    """
    step = p.step
    atm = round(float(spot) / step) * step
    if p.moneyness.upper() == "ATM" or p.strike_offset <= 0:
        return float(atm)
    shift = round(float(p.strike_offset) / step) * step
    if p.moneyness.upper() == "ITM":
        return float(atm - shift) if side == "CE" else float(atm + shift)
    return float(atm + shift) if side == "CE" else float(atm - shift)


def exits(entry: float, p: Params = P) -> dict:
    """Target and stop for a long option, in premium terms."""
    return {"target": round(entry + p.target_points, 2),
            "stop": round(max(0.05, entry - p.stop_points), 2)}


def describe(p: Params = P) -> list[str]:
    where = "in-the-money" if p.moneyness.upper() == "ITM" else (
        "at-the-money" if p.moneyness.upper() == "ATM" else "out-of-the-money")
    offset = "" if p.moneyness.upper() == "ATM" else f" by {p.strike_offset:g} points"
    if p.mode == "breakout":
        rule = ("Buy a call when the index closes above the range high, and a put when it closes "
                "below the range low.")
    else:
        rule = ("Buy a call when the index closes back above the range low, and a put when it "
                "closes back below the range high — the level is reclaimed, not broken.")
    return [
        f"Measure two opening ranges from the {p.index} index: the first {p.range_a_min} minutes "
        f"and the first {p.range_b_min} minutes, both from {hhmm(SESSION_OPEN)}.",
        f"Before {hhmm(p.switch_at)} the {p.range_a_min}-minute range is in force; after it, the "
        f"{p.range_b_min}-minute one.",
        rule,
        f"Trade the option {where}{offset}, {p.lots} lot(s) of {p.lot}.",
        (f"Take {p.target_points:g} points of profit, cut at {p.stop_points:g} — measured on "
         f"{'the index' if p.exit_on_index else 'the option premium'}."),
        f"No new trade before {hhmm(p.first_entry)} or after {hhmm(p.last_entry)}; "
        f"everything is closed by {hhmm(p.squareoff)}.",
        f"At most {p.max_trades_per_day} trade(s) a day"
        + (", and each level is taken only once." if p.one_trade_per_level else "."),
    ]
