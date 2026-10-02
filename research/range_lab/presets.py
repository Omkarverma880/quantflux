"""
5 & 60 Minute Range — what each filter does, and the configurations that were measured.

Every row in PRESETS was actually run against the market store over one window. Applying a preset
sets the parameters AND the dates, so re-running it reproduces the stated numbers exactly. If a
number here ever stops matching a fresh run, the data changed underneath it — say so rather than
quietly trusting the table.

Results below are NET of real Zerodha charges and one tick of slippage per side.
"""
from __future__ import annotations

# the window every measured result below was produced on
WINDOW = {"start": "2025-10-02", "end": "2026-09-11", "index": "NIFTY",
          "sessions": 232, "note": "NIFTY, nearest expiry, stored 1-minute premiums"}

# the settings shared by every preset — only what a preset overrides is listed in its own "params"
COMMON = {
    "index": "NIFTY", "mode": "reversal", "moneyness": "ITM", "strike_offset": 200.0,
    "sides": "both", "target_points": 25.0, "stop_points": 20.0, "exit_on_index": False,
    "max_trades_per_day": 3, "range_a_min": 5, "range_b_min": 60, "switch_at": 615,
    "first_entry": 560, "last_entry": 870, "squareoff": 915,
    "cross_buffer": 0.0, "min_range": 0.0, "max_range": 0.0, "lots": 1,
}


FILTERS = [
    {
        "key": "cross_buffer",
        "name": "Cross buffer",
        "unit": "index points",
        "what": "The close must clear the level by this many points before the cross counts. "
                "At 0 a one-paise poke through the level is a signal.",
        "why": "Most crosses are noise. Price grazes a level, you pay ₹78 of brokerage, and it "
               "goes nowhere. The buffer asks the market to commit before you do.",
        "measured": "At 3 lots: 0 → 421 trades and +₹30,133. 12 → 95 trades and +₹80,646, with "
                    "the drawdown falling from ₹1,19,073 to ₹19,507.",
        "suggested": "8 to 12. All three work, so this is a plateau rather than one fitted value.",
    },
    {
        "key": "max_range",
        "name": "Max range width",
        "unit": "index points",
        "what": "Skip the day when the opening range is wider than this. 0 turns it off.",
        "why": "A very wide opening range means the move already happened. The reclaim you are "
               "buying has less room left before the day runs out.",
        "measured": "At 3 lots with no buffer: capping at 150 took 351 trades instead of 421 and "
                    "lifted net from +₹30,133 to +₹76,435.",
        "suggested": "150, or 0 while you are still judging the buffer on its own.",
    },
    {
        "key": "min_range",
        "name": "Min range width",
        "unit": "index points",
        "what": "Skip the day when the opening range is narrower than this. 0 turns it off.",
        "why": "A very tight range sits inside the spread and the noise. Crossing it means little.",
        "measured": "Weak on its own: a 50-point floor moved net from +₹30,133 to +₹36,428. "
                    "A 30-point floor changed nothing — no stored session was that quiet.",
        "suggested": "0. The buffer does this job better.",
    },
    {
        "key": "lots",
        "name": "Lots",
        "unit": "lots",
        "what": "Position size. It does not change a single signal — only what each one earns.",
        "why": "₹40 of every round trip is flat brokerage, so per-lot cost falls from ₹76 at one "
               "lot to ₹38 at five. Size is the only lever that improves the cost drag without "
               "touching the rule.",
        "measured": "Same 421 trades: 1 lot −₹3,203 · 2 lots +₹13,465 · 3 lots +₹30,133 · "
                    "5 lots +₹63,468 · 10 lots +₹1,46,808.",
        "suggested": "Size to the drawdown you can sit through — it scales exactly as fast as "
                     "the profit. At 3 lots the worst stretch was ₹1,19,073 without a buffer.",
    },
    {
        "key": "sides",
        "name": "Sides",
        "unit": "call / put / both",
        "what": "Take only calls, only puts, or both.",
        "why": "Restricting a side is a directional bet on the market, not a property of the rule.",
        "measured": "At 3 lots: calls +₹32,243 from 215 trades, puts +₹7,394 from 208.",
        "suggested": "both. The call/put gap is this window trending up; it is the most likely "
                     "thing here to fall apart out of sample.",
    },
    {
        "key": "max_trades_per_day",
        "name": "Max trades a day",
        "unit": "trades",
        "what": "Stop after this many entries, whatever else fires.",
        "why": "Caps the damage on a day that whipsaws through a level repeatedly.",
        "measured": "Cutting to 1 a day made things worse in every variant tested — it keeps "
                    "the first signal of the day rather than the best one.",
        "suggested": "3. Use the buffer to cut trade count instead.",
    },
    {
        "key": "moneyness",
        "name": "Strike",
        "unit": "ITM / ATM / OTM",
        "what": "Where the bought option sits relative to spot, by Strike offset points.",
        "why": "An in-the-money option carries more delta and less time value, so a given index "
               "move shows up more fully in the premium.",
        "measured": "At 1 lot with no buffer, ITM beat ATM which beat OTM, in both readings.",
        "suggested": "ITM at 200 points. The ordering was the same in both readings.",
    },
    {
        "key": "mode",
        "name": "Reading",
        "unit": "reversal / breakout",
        "what": "reversal buys a call when price crosses back up through the range low, and a put "
                "when it crosses back down through the high. breakout does the opposite.",
        "why": "Your spec describes the reversal. The breakout is here so the alternative is "
               "visible rather than assumed.",
        "measured": "At 1 lot, 200 ITM over this window: reversal −₹3,203 · breakout −₹1,01,755.",
        "suggested": "reversal.",
    },
]


def _p(**over) -> dict:
    return {**COMMON, **over}


PRESETS = [
    {
        "id": "spec",
        "name": "As written",
        "note": "Your specification with nothing added. The signal is real but brokerage is "
                "125.6% of the gross, so it loses.",
        "params": _p(lots=1),
        "result": {"trades": 421, "net": -3203, "per_trade": -8, "win_rate": 46.6,
                   "green_months": 7, "months": 12, "max_drawdown": 45150, "worst_day": -4127},
    },
    {
        "id": "spec3",
        "name": "As written, 3 lots",
        "note": "Identical signals. Only the size changes, which halves the per-lot cost.",
        "params": _p(lots=3),
        "result": {"trades": 421, "net": 30133, "per_trade": 72, "win_rate": 46.6,
                   "green_months": 7, "months": 12, "max_drawdown": 119073, "worst_day": -12096},
    },
    {
        "id": "buffer10",
        "name": "Cross buffer 10",
        "note": "The close must clear the level by 10 points. A quarter of the trades, more money.",
        "params": _p(lots=3, cross_buffer=10),
        "result": {"trades": 121, "net": 72253, "per_trade": 597, "win_rate": 52.9,
                   "green_months": 6, "months": 10, "max_drawdown": 18876, "worst_day": -8090,
                   "p_profit": 96},
    },
    {
        "id": "buffer12",
        "name": "Cross buffer 12",
        "note": "The steadiest of the three buffers — 9 of 10 months green, and it survives "
                "losing its best month (+₹60,380 left).",
        "params": _p(lots=3, cross_buffer=12),
        "result": {"trades": 95, "net": 80646, "per_trade": 849, "win_rate": 55.8,
                   "green_months": 9, "months": 10, "max_drawdown": 19507, "worst_day": -9482,
                   "p_profit": 98},
    },
    {
        "id": "buffer10w150",
        "name": "Buffer 10 + range ≤ 150",
        "note": "The best total measured, but two filters stacked on 88 trades — the easiest "
                "result here to have fitted.",
        "params": _p(lots=3, cross_buffer=10, max_range=150),
        "result": {"trades": 88, "net": 82925, "per_trade": 942, "win_rate": 56.8,
                   "green_months": 7, "months": 10, "max_drawdown": 18812, "worst_day": -8094,
                   "p_profit": 98},
    },
    {
        "id": "buffer10x1",
        "name": "Buffer 10, one lot",
        "note": "What ₹2L of capital can actually carry. Profitable, with a drawdown you can "
                "sit through.",
        "params": _p(lots=1, cross_buffer=10),
        "result": {"trades": 121, "net": 20277, "per_trade": 168, "win_rate": 52.9,
                   "green_months": 6, "months": 10, "max_drawdown": 6723, "worst_day": -2697,
                   "p_profit": 92},
    },
    {
        "id": "breakout",
        "name": "Breakout reading",
        "note": "The opposite direction, for contrast. It is badly negative, which is itself "
                "evidence the reversal reading is the right one.",
        "params": _p(lots=1, mode="breakout"),
        "result": {"trades": 487, "net": -101755, "per_trade": -209, "win_rate": 39.8,
                   "green_months": 1, "months": 12, "max_drawdown": 105559, "worst_day": -4154},
    },
]

CAVEATS = [
    "Every number was produced on one window of one index. One regime is not proof.",
    "The buffer cuts the sample to roughly 95–160 trades across a year. Thinner than it looks.",
    "A preset's result is what the backtest produced when it was recorded. Re-run it — if the "
    "store has since been refreshed or extended, the number will move.",
    "Charges and one tick of slippage per side are already deducted. Real fills on a thin strike "
    "can still be worse.",
    "Drawdown scales with lots exactly as fast as profit does. Size to the drawdown, not the net.",
]
