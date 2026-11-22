"""
20 EMA Single Candle Breakout — the rule itself, translated from the Pine script.

The source is a state machine, and the order its blocks run in is the strategy. Reordering
them changes which bar a setup starts on and whether an exit bar can open a new one, so this
follows the original block for block rather than tidying it:

    1  exits            a held position closes when the candle closes the wrong side of EMA
    2  reset            the bar AFTER a position closes, every piece of setup state clears
    3  crossovers       bullCross = close > ema and close[1] < ema[1]
    4  start setup      only when flat
    5  confirmation     bars crossBar+1 and crossBar+2, and no others
    6  expiry           on crossBar+3 with nothing confirmed, the setup dies
    7  entry            close crosses the trigger level

The single most important detail is what ``strategy.position_size`` means inside a Pine bar.
It is the position as it stood when the bar BEGAN — orders placed on this bar fill at its
close and are not visible to the script until the next one. So the bar that exits still reads
as "in a position" and cannot start a fresh setup, and the reset fires one bar later. ``pos``
below is that same pre-order value, and ``pos_prev`` is ``strategy.position_size[1]``.

The confirmation candle is counter-coloured on purpose:

    LONG    the first RED candle (close < open) that still closes ABOVE the EMA.
            Its HIGH becomes the trigger — a pullback that failed to break the trend.
    SHORT   the first GREEN candle (close > open) that still closes BELOW the EMA.
            Its LOW becomes the trigger.

Entry is ``ta.crossover(close, triggerLevel)``, which needs the PREVIOUS close to have been at
or below the PREVIOUS bar's trigger. On the confirmation bar itself the trigger has no prior
value, so no entry can fire there — the earliest possible entry is the bar after confirmation.

This module decides and explains. It touches no broker, no database and no market store.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

import numpy as np
import pandas as pd

LONG, SHORT = 1, -1
CE, PE = "CE", "PE"
BUY, SELL = "BUY", "SELL"

# Strike grids. Lot sizes are NOT kept here: they come from the broker's instrument master
# live, and from the stored contract in a backtest, because exchanges revise them.
STRIKE_STEP = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50,
               "MIDCPNIFTY": 25, "SENSEX": 100, "BANKEX": 100}
TIMEFRAMES = (1, 3, 5, 15, 30, 60)
SESSION_OPEN = 9 * 60 + 15
SESSION_CLOSE = 15 * 60 + 30


@dataclass(frozen=True)
class Params:
    index: str = "NIFTY"
    ema_length: int = 20
    timeframe: int = 5                 # candle size, minutes

    allow_long: bool = True
    allow_short: bool = True

    # ── how the index signal becomes an option trade ──
    # BUY  → a long signal buys a CALL, a short signal buys a PUT
    # SELL → a long signal SELLS a PUT, a short signal SELLS a CALL
    option_action: str = BUY           # BUY | SELL
    strike_offset: int = 0             # steps OUT of the money; negative is in the money
    lots: int = 1

    # ── session guards, applied on top of the Pine rules ──
    entry_from: int = 9 * 60 + 20      # no entry before the opening settles
    entry_until: int = 15 * 60 + 0     # no new entry near the close
    squareoff: int = 15 * 60 + 15      # anything still open is closed here

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def step(self) -> float:
        return float(STRIKE_STEP.get(self.index.upper(), 50))


P = Params()


def hhmm(m: int) -> str:
    return f"{int(m) // 60:02d}:{int(m) % 60:02d}"


def ema(series: pd.Series, length: int) -> pd.Series:
    """ta.ema — Pine seeds with an SMA of the first ``length`` values, then recurses."""
    return series.ewm(span=max(1, int(length)), adjust=False,
                      min_periods=max(1, int(length))).mean()


def prepare(bars: pd.DataFrame, p: Params = P) -> pd.DataFrame:
    """Add the EMA and the four candle classifications the script uses."""
    d = bars.reset_index(drop=True).copy()
    d["ema"] = ema(d["close"], p.ema_length)
    above = d["close"] > d["ema"]
    below = d["close"] < d["ema"]
    green = d["close"] > d["open"]
    red = d["close"] < d["open"]
    d["red_above"] = red & above          # the LONG confirmation candle
    d["green_below"] = green & below      # the SHORT confirmation candle
    prev_close, prev_ema = d["close"].shift(1), d["ema"].shift(1)
    d["bull_cross"] = above & (prev_close < prev_ema)
    d["bear_cross"] = below & (prev_close > prev_ema)
    return d


def option_for(side: int, action: str, spot: float, p: Params) -> dict:
    """Which contract a signal implies.

    A long view is expressed either by buying a call or by selling a put; a short view by
    buying a put or selling a call. ``strike_offset`` counts steps OUT of the money for the
    contract actually traded, so one setting means the same distance from spot whichever of
    the four combinations is in force.
    """
    long_view = side == LONG
    if action == BUY:
        opt = CE if long_view else PE
    else:
        opt = PE if long_view else CE
    atm = round(float(spot) / p.step) * p.step
    away = p.strike_offset * p.step
    strike = atm + away if opt == CE else atm - away
    return {"option_type": opt, "action": action, "strike": float(strike),
            "atm": float(atm),
            "why": (f"{'long' if long_view else 'short'} view, "
                    f"{'buying' if action == BUY else 'selling'} the {opt}")}


# ── the state machine ───────────────────────────────────────────────────────
def run(bars: pd.DataFrame, p: Params = P, on_event=None) -> dict:
    """Walk the bars exactly as Pine does, returning every event and every trade.

    ``bars`` must be closed candles in time order with open/high/low/close, and ``minute``
    and ``date`` columns when session guards are wanted. Nothing here looks at a bar beyond
    the one being decided, which is what makes the backtest and the live desk agree.
    """
    d = prepare(bars, p)
    n = len(d)
    events: list[dict] = []
    trades: list[dict] = []

    setup_active = False
    setup_direction = 0
    cross_bar: Optional[int] = None
    confirmation_found = False
    trigger: Optional[float] = None
    trigger_prev: Optional[float] = None       # triggerLevel[1], for ta.crossover

    pos = 0                 # strategy.position_size as the bar BEGINS
    pos_prev = 0            # strategy.position_size[1]
    open_trade: Optional[dict] = None

    cols = {c: d[c].to_numpy() for c in
            ("open", "high", "low", "close", "ema", "red_above", "green_below",
             "bull_cross", "bear_cross")}
    minutes = d["minute"].to_numpy() if "minute" in d.columns else np.full(n, -1)
    dates = d["date"].tolist() if "date" in d.columns else [None] * n

    def emit(kind: str, i: int, **extra) -> None:
        e = {"kind": kind, "bar": i,
             "date": str(dates[i]) if dates[i] is not None else None,
             "minute": int(minutes[i]) if minutes[i] >= 0 else None,
             "time": hhmm(minutes[i]) if minutes[i] >= 0 else None,
             "close": round(float(cols["close"][i]), 2),
             "ema": (round(float(cols["ema"][i]), 2)
                     if np.isfinite(cols["ema"][i]) else None),
             **extra}
        events.append(e)
        if on_event:
            on_event(e)

    def clear_setup() -> None:
        nonlocal setup_active, setup_direction, cross_bar, confirmation_found, trigger
        setup_active = False
        setup_direction = 0
        cross_bar = None
        confirmation_found = False
        trigger = None

    for i in range(n):
        c = float(cols["close"][i])
        e = float(cols["ema"][i])
        # triggerLevel[1]: the trigger as it stood at the END of the previous bar. It is
        # only ever written in this loop's tail, so reading it here cannot see this bar's
        # own confirmation — which is what stops an entry firing on the confirmation candle.
        prev_trigger = trigger_prev
        pending_close = False
        pending_entry: Optional[int] = None

        if not np.isfinite(e):
            pos_prev = pos
            trigger_prev = trigger
            continue

        in_session = minutes[i] < 0 or (SESSION_OPEN <= minutes[i] <= SESSION_CLOSE)
        # Only true when a LATER bar is visible and belongs to a different date. The live
        # desk feeds only the candles closed so far, so its newest bar is always the last
        # row of the frame — testing "is this the final row" there marked every candle as
        # the end of the session and suppressed every entry. The square-off minute, not the
        # frame's edge, is what ends the day.
        last_of_day = (minutes[i] >= 0 and i + 1 < n and dates[i + 1] != dates[i])

        # 1 ── exits. A close on the wrong side of the EMA ends the trade, and so does the
        #      square-off time; holding an intraday option overnight is a different strategy.
        if pos != 0 and open_trade is not None:
            why = None
            if pos > 0 and c < e:
                why = "EMA_EXIT"
            elif pos < 0 and c > e:
                why = "EMA_EXIT"
            elif minutes[i] >= 0 and (minutes[i] >= p.squareoff or last_of_day):
                why = "SQUAREOFF"
            if why:
                pending_close = True
                open_trade.update(exit_bar=i, exit_close=round(c, 2), exit_reason=why,
                                  exit_minute=int(minutes[i]) if minutes[i] >= 0 else None,
                                  exit_date=str(dates[i]) if dates[i] is not None else None)
                emit("exit_long" if pos > 0 else "exit_short", i, reason=why,
                     direction="LONG" if pos > 0 else "SHORT")

        # 2 ── reset, one bar after the position actually closed
        if pos == 0 and pos_prev != 0:
            clear_setup()
            emit("state_reset", i, reason="flat again — setup state cleared")

        # 3 ── the crossovers
        bull = bool(cols["bull_cross"][i])
        bear = bool(cols["bear_cross"][i])

        # 4 ── start a setup, only when flat
        if bull and pos == 0:
            setup_active, setup_direction, cross_bar = True, LONG, i
            confirmation_found, trigger = False, None
            emit("bull_cross", i, direction="LONG",
                 reason="close crossed above the EMA — watching the next two candles")
        if bear and pos == 0:
            setup_active, setup_direction, cross_bar = True, SHORT, i
            confirmation_found, trigger = False, None
            emit("bear_cross", i, direction="SHORT",
                 reason="close crossed below the EMA — watching the next two candles")

        # 5 ── confirmation, on bars crossBar+1 and crossBar+2 only
        if setup_active and cross_bar is not None and cross_bar < i <= cross_bar + 2:
            if setup_direction == LONG and cols["red_above"][i] and not confirmation_found:
                confirmation_found = True
                trigger = float(cols["high"][i])
                emit("long_confirmation", i, direction="LONG", trigger=round(trigger, 2),
                     candle=f"candle {i - cross_bar} of 2",
                     reason="red candle closing above the EMA — its HIGH is the trigger")
            if setup_direction == SHORT and cols["green_below"][i] and not confirmation_found:
                confirmation_found = True
                trigger = float(cols["low"][i])
                emit("short_confirmation", i, direction="SHORT", trigger=round(trigger, 2),
                     candle=f"candle {i - cross_bar} of 2",
                     reason="green candle closing below the EMA — its LOW is the trigger")

        # 6 ── expiry
        if setup_active and cross_bar is not None and i > cross_bar + 2 and not confirmation_found:
            direction = "LONG" if setup_direction == LONG else "SHORT"
            clear_setup()
            emit("setup_expired", i, direction=direction,
                 reason="no confirmation candle inside the two-candle window")

        # 7 ── entry. ta.crossover needs the PREVIOUS close under the PREVIOUS trigger.
        if (setup_active and confirmation_found and trigger is not None and pos == 0
                and i > 0 and prev_trigger is not None and np.isfinite(prev_trigger)):
            prev_c = float(cols["close"][i - 1])
            allowed = (p.allow_long if setup_direction == LONG else p.allow_short)
            in_window = (minutes[i] < 0 or
                         (p.entry_from <= minutes[i] <= p.entry_until))
            crossed_up = c > trigger and prev_c <= prev_trigger
            crossed_dn = c < trigger and prev_c >= prev_trigger
            fire = (crossed_up if setup_direction == LONG else crossed_dn)
            if fire and not allowed:
                emit("signal_suppressed", i,
                     direction="LONG" if setup_direction == LONG else "SHORT",
                     reason=f"{'long' if setup_direction == LONG else 'short'} signals are "
                            f"switched off in the settings")
                setup_active = False
            elif fire and not in_window:
                emit("signal_suppressed", i,
                     direction="LONG" if setup_direction == LONG else "SHORT",
                     reason=f"outside the entry window "
                            f"{hhmm(p.entry_from)}–{hhmm(p.entry_until)}")
                setup_active = False
            elif fire and not in_session:
                emit("signal_suppressed", i, reason="outside market hours")
                setup_active = False
            elif fire:
                pending_entry = setup_direction
                setup_active = False          # the Pine does this on entry
                emit("buy_entry" if setup_direction == LONG else "sell_entry", i,
                     direction="LONG" if setup_direction == LONG else "SHORT",
                     trigger=round(trigger, 2),
                     reason=(f"close {c:.2f} crossed "
                             f"{'above' if setup_direction == LONG else 'below'} the trigger "
                             f"{trigger:.2f}"))

        # ── apply this bar's orders, so the NEXT bar sees the new position ──
        # One tail, no early exits: the trigger carry-forward below must run on every bar,
        # and an earlier version that returned early from here silently broke the crossover.
        new_pos = pos
        if pending_close and open_trade is not None:
            open_trade["closed"] = True
            trades.append(open_trade)
            open_trade = None
            new_pos = 0
        elif pending_entry is not None:
            open_trade = {
                "direction": "LONG" if pending_entry == LONG else "SHORT",
                "entry_bar": i, "entry_close": round(c, 2),
                "entry_minute": int(minutes[i]) if minutes[i] >= 0 else None,
                "entry_date": str(dates[i]) if dates[i] is not None else None,
                "trigger": round(float(trigger), 2) if trigger is not None else None,
                "ema_at_entry": round(e, 2), "closed": False,
            }
            new_pos = pending_entry
        pos_prev, pos = pos, new_pos
        trigger_prev = trigger

    if open_trade is not None:
        open_trade["exit_reason"] = "STILL_OPEN"
        trades.append(open_trade)

    return {"bars": d, "events": events, "trades": trades,
            "open_position": bool(open_trade is not None and not open_trade.get("closed"))}


def describe(p: Params = P) -> str:
    sides = ("long and short" if p.allow_long and p.allow_short
             else "long only" if p.allow_long else "short only" if p.allow_short else "nothing")
    act = "buying" if p.option_action == BUY else "selling"
    where = ("at the money" if p.strike_offset == 0
             else f"{abs(p.strike_offset)} strike(s) "
                  f"{'out of' if p.strike_offset > 0 else 'in'} the money")
    return (f"{p.index} on {p.timeframe}-minute candles, {p.ema_length} EMA. A close through "
            f"the EMA arms a setup; the first counter-coloured candle inside the next two "
            f"candles sets the trigger at its high (long) or low (short); entry is a close "
            f"through that trigger. Exit when a candle closes back across the EMA. Taking "
            f"{sides}, {act} options {where}.")
