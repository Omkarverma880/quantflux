"""
Execution, position sizing and the risk firewall.

The gap between a signal and a realised rupee is where most backtests quietly lie, so the
assumptions are stated here rather than buried:

  * A stop does not fill at its trigger. It fills at the next printed price, and on a gap that
    is worse than the trigger. Modelled, not assumed away.
  * Underlying invalidation and option-premium risk are SEPARATE rules. A 30-point move against
    the index and a 40% fall in the premium are different events — implied volatility, decay,
    strike and liquidity all sit between them — and neither can stand in for the other.
  * Only minute OHLC exists, not quotes. When a bar touches both the stop and the target the
    order is unknowable, so the stop is taken. That is pessimistic by construction and it is
    disclosed on every result rather than hidden in a footnote.
  * Costs come from the shared ``CostModel`` so they match every other lab in this repo, and
    ``round_trip`` returns CHARGES — not profit. Treating it as profit is the single easiest
    way to produce an impossible backtest, so the subtraction is explicit everywhere below.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from research.fvb.data import LOT, STRIKE_STEP
from research.options_lab.execution import CostModel

# why a position left
EXIT_UNDERLYING = "UNDERLYING_STOP"      # the index invalidated the setup
EXIT_PREMIUM = "PREMIUM_STOP"            # the option lost more than its own budget
EXIT_TARGET = "TARGET"
EXIT_TIME = "MAX_HOLD"
EXIT_EOD = "SQUARE_OFF"

# why a signal never became a position
SKIP_NO_CONTRACT = "the required strike is not in the stored chain"
SKIP_NOT_TRADED = "the contract had not traded in the minutes before the fill"
SKIP_NO_PRICE = "no usable option price at the fill minute"
SKIP_SPREAD = "the spread proxy exceeded the configured limit"
SKIP_LIQUIDITY = "the contract was below the liquidity floor"
SKIP_PREMIUM_BAND = "the premium was outside the configured band"
SKIP_RISK = "the trade's risk exceeds the permitted budget"
SKIP_SIZE = "the risk budget does not cover one lot"


@dataclass(frozen=True)
class ExecParams:
    # ── buy the move, or sell against it ──
    # BUY  pays a premium and needs the move to arrive before decay eats it
    # SELL collects a premium on the OPPOSITE side and needs the move merely not to reverse,
    #      with hedge_offset strikes bought further out so the loss stops at the strike width
    action: str = "BUY"                  # BUY | SELL
    hedge_offset: int = 4                # SELL only; 0 is naked and has no defined loss
    # ── which contract ──
    moneyness: int = 0                   # 0 ATM, +1 one strike OTM, -1 one strike ITM
    expiry: str = "nearest"              # nearest | next
    min_premium: float = 0.0             # 0 = off
    max_premium: float = 0.0             # 0 = off
    min_volume: int = 0                  # liquidity floor on the fill bar, 0 = off
    max_spread_pct: float = 0.0          # proxy from the bar's own range, 0 = off
    # ── size ──
    lots: int = 1
    sizing: str = "fixed_lots"           # fixed_lots | risk_based
    max_risk_rs: float = 5000.0          # risk_based only
    # ── exits ──
    use_underlying_stop: bool = True
    premium_stop_pct: float = 35.0       # the option's own budget, 0 = off
    premium_target_pct: float = 0.0      # 0 = run to the underlying target or the close
    use_underlying_target: bool = True
    max_hold_min: int = 0                # 0 = to square-off
    squareoff: int = 15 * 60 + 15
    # ── friction ──
    slippage_pts: float = 1.0            # per fill, both ways
    # ── daily protection ──
    max_daily_loss_rs: float = 0.0       # 0 = off
    max_consecutive_losses: int = 0      # 0 = off
    cooldown_after_loss_min: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


XP = ExecParams()
COST = CostModel(slippage_pts=0.0)       # slippage applied explicitly below, never twice


@dataclass
class Fill:
    ok: bool
    reason: Optional[str] = None
    contract: Optional[str] = None
    strike: Optional[float] = None
    option_type: Optional[str] = None
    expiry: Optional[str] = None
    dte: Optional[int] = None
    entry: Optional[float] = None
    qty: Optional[int] = None
    lots: Optional[int] = None


def _col(frame: pd.DataFrame, typ: str, strike: float):
    for k in ((typ, float(strike)), (typ, int(strike)), (typ, strike)):
        if k in frame.columns:
            return k
    return None


def pick_contract(direction: str, spot: float, xp: ExecParams) -> tuple[str, float]:
    """The option a direction implies, which depends on whether we are paying or collecting.

    Buying a bullish view buys a call. SELLING a bullish view sells a PUT — the other side of
    the same opinion — so the type inverts with the action while the strike logic does not.
    """
    if str(xp.action).upper().startswith("S"):
        typ = "PE" if direction == "BULL" else "CE"
    else:
        typ = "CE" if direction == "BULL" else "PE"
    atm = round(spot / STRIKE_STEP) * STRIKE_STEP
    away = 1 if typ == "CE" else -1
    return typ, float(atm + away * xp.moneyness * STRIKE_STEP)


def size_position(entry: float, stop_premium: Optional[float], xp: ExecParams) -> tuple[int, int, Optional[str]]:
    """Lots and quantity. Risk-based sizing rounds DOWN to a whole lot, never up."""
    if xp.sizing == "fixed_lots":
        return xp.lots, xp.lots * LOT, None
    if stop_premium is None or stop_premium >= entry:
        return 0, 0, SKIP_SIZE
    risk_per_unit = entry - stop_premium
    if risk_per_unit <= 0:
        return 0, 0, SKIP_SIZE
    units = xp.max_risk_rs / risk_per_unit
    lots = int(units // LOT)
    if lots < 1:
        return 0, 0, SKIP_SIZE
    return lots, lots * LOT, None


def open_position(day_chain: dict, d: date, sig, xp: ExecParams) -> Fill:
    """Buy the option the signal implies, at the next minute's real printed open."""
    typ, strike = pick_contract(sig.direction, sig.spot_close, xp)
    op, cl, printed = day_chain["op"], day_chain["cl"], day_chain["printed"]
    vol = day_chain.get("vol")
    col = _col(cl, typ, strike)
    if col is None:
        return Fill(False, SKIP_NO_CONTRACT)
    fill = sig.fill_minute
    if fill not in printed.index:
        return Fill(False, SKIP_NO_PRICE)
    # the contract must have actually traded just before we claim a fill on it
    window = printed[col].loc[max(printed.index.min(), fill - 6):fill]
    if not window.any():
        return Fill(False, SKIP_NOT_TRADED)
    raw = float(op.at[fill, col])
    if not np.isfinite(raw) or raw <= 0:
        return Fill(False, SKIP_NO_PRICE)
    if xp.min_premium and raw < xp.min_premium:
        return Fill(False, SKIP_PREMIUM_BAND)
    if xp.max_premium and raw > xp.max_premium:
        return Fill(False, SKIP_PREMIUM_BAND)
    if xp.min_volume and vol is not None:
        try:
            if float(vol.at[fill, col]) < xp.min_volume:
                return Fill(False, SKIP_LIQUIDITY)
        except (KeyError, TypeError):
            pass
    if xp.max_spread_pct:
        hi, lo = day_chain["hi"], day_chain["lo"]
        try:
            rng = float(hi.at[fill, col]) - float(lo.at[fill, col])
            if raw > 0 and (rng / raw) * 100 > xp.max_spread_pct:
                return Fill(False, SKIP_SPREAD)
        except (KeyError, TypeError):
            pass

    entry = raw + xp.slippage_pts                     # buying costs more than the print
    prem_stop = entry * (1 - xp.premium_stop_pct / 100) if xp.premium_stop_pct else None
    lots, qty, why = size_position(entry, prem_stop, xp)
    if why:
        return Fill(False, why)
    return Fill(True, None, f"NIFTY{strike:.0f}{typ}", float(strike), typ,
                str(day_chain["expiry"]), (day_chain["expiry"] - d).days, entry, qty, lots)


def manage(day: pd.DataFrame, day_chain: dict, sig, fill: Fill, xp: ExecParams) -> dict:
    """Walk the position forward on completed one-minute bars and decide how it ended.

    Both exit rules are live at once and checked in a fixed order: the adverse ones first, and
    among those the underlying's invalidation before the premium budget, because a backtest
    that resolves ties in its own favour is worth nothing.
    """
    col = _col(day_chain["cl"], fill.option_type, fill.strike)
    op, cl = day_chain["op"], day_chain["cl"]
    start = sig.fill_minute
    last = min(xp.squareoff, int(day_chain["cl"].index.max()))
    end = min(last, start + xp.max_hold_min) if xp.max_hold_min else last

    prem_stop = fill.entry * (1 - xp.premium_stop_pct / 100) if xp.premium_stop_pct else None
    prem_target = fill.entry * (1 + xp.premium_target_pct / 100) if xp.premium_target_pct else None
    bullish = sig.direction == "BULL"

    spot_by_min = day.set_index("minute")
    reason, exit_min = EXIT_EOD, end
    for m in range(start, end):
        # ── the underlying's own invalidation, on the adverse extreme of the bar ──
        if xp.use_underlying_stop and m in spot_by_min.index:
            bar = spot_by_min.loc[m]
            adverse = float(bar["low"]) if bullish else float(bar["high"])
            hit = (adverse <= sig.stop_level) if bullish else (adverse >= sig.stop_level)
            if hit:
                reason, exit_min = EXIT_UNDERLYING, m + 1
                break
        # ── the option's own budget ──
        px = float(cl.at[m, col]) if m in cl.index else np.nan
        if np.isfinite(px):
            if prem_stop is not None and px <= prem_stop:
                reason, exit_min = EXIT_PREMIUM, m + 1
                break
            if prem_target is not None and px >= prem_target:
                reason, exit_min = EXIT_TARGET, m + 1
                break
        if xp.use_underlying_target and sig.target_level and m in spot_by_min.index:
            bar = spot_by_min.loc[m]
            favour = float(bar["high"]) if bullish else float(bar["low"])
            got = (favour >= sig.target_level) if bullish else (favour <= sig.target_level)
            if got:
                reason, exit_min = EXIT_TARGET, m + 1
                break
    if reason == EXIT_EOD and xp.max_hold_min and end < last:
        reason = EXIT_TIME

    exit_min = min(exit_min, end)
    raw_out = float(op.at[exit_min, col]) if (exit_min in op.index
                                              and np.isfinite(op.at[exit_min, col])) \
        else float(cl.at[min(exit_min, end), col])
    exit_px = max(0.0, raw_out - xp.slippage_pts)     # selling gets less than the print

    gross = (exit_px - fill.entry) * fill.qty
    charges = COST.round_trip(buy_px=fill.entry, sell_px=exit_px, qty=fill.qty)
    # the option's realised risk, which is NOT the underlying's
    return {
        "exit_reason": reason, "exit_minute": int(exit_min),
        "exit_time": f"{int(exit_min) // 60:02d}:{int(exit_min) % 60:02d}",
        "option_entry": round(fill.entry, 2), "option_exit": round(exit_px, 2),
        "held_min": int(exit_min - start),
        "gross": round(gross, 2), "charges": round(charges, 2),
        "pnl": round(gross - charges, 2),
        "stop_was_gapped": bool(reason in (EXIT_UNDERLYING, EXIT_PREMIUM)
                                and prem_stop is not None and exit_px < prem_stop - 0.01),
        "premium_stop": round(prem_stop, 2) if prem_stop else None,
        "ambiguous_bar": reason in (EXIT_UNDERLYING, EXIT_PREMIUM),
    }


class DayRisk:
    """Daily capital protection. Refuses rather than warns."""

    def __init__(self, xp: ExecParams):
        self.xp = xp
        self.realised = 0.0
        self.losses_in_a_row = 0
        self.blocked_until: Optional[int] = None
        self.halted: Optional[str] = None

    def may_trade(self, minute: int) -> tuple[bool, Optional[str]]:
        if self.halted:
            return False, self.halted
        if self.blocked_until and minute < self.blocked_until:
            return False, (f"cooling off until "
                           f"{self.blocked_until // 60:02d}:{self.blocked_until % 60:02d}")
        return True, None

    def record(self, pnl: float, exit_minute: int) -> None:
        self.realised += pnl
        if pnl < 0:
            self.losses_in_a_row += 1
            if self.xp.cooldown_after_loss_min:
                self.blocked_until = exit_minute + self.xp.cooldown_after_loss_min
        else:
            self.losses_in_a_row = 0
        if self.xp.max_daily_loss_rs and self.realised <= -abs(self.xp.max_daily_loss_rs):
            self.halted = (f"daily loss limit reached "
                           f"(Rs {self.realised:,.0f} against a Rs "
                           f"{self.xp.max_daily_loss_rs:,.0f} budget)")
        if self.xp.max_consecutive_losses and \
                self.losses_in_a_row >= self.xp.max_consecutive_losses:
            self.halted = f"{self.losses_in_a_row} losses in a row"

def open_short(day_chain: dict, d: date, sig, xp: ExecParams) -> Fill:
    """Sell the opposite option and buy a wing, at the next minute's real printed open."""
    typ, strike = pick_contract(sig.direction, sig.spot_close, xp)
    op, cl, printed = day_chain["op"], day_chain["cl"], day_chain["printed"]
    away = 1 if typ == "CE" else -1
    wing = strike + away * xp.hedge_offset * STRIKE_STEP if xp.hedge_offset else None

    short_col = _col(cl, typ, strike)
    if short_col is None:
        return Fill(False, SKIP_NO_CONTRACT)
    wing_col = _col(cl, typ, wing) if wing is not None else None
    if wing is not None and wing_col is None:
        return Fill(False, SKIP_NO_CONTRACT)

    fill = sig.fill_minute
    if fill not in printed.index:
        return Fill(False, SKIP_NO_PRICE)
    for c in (short_col, wing_col):
        if c is None:
            continue
        if not printed[c].loc[max(printed.index.min(), fill - 6):fill].any():
            return Fill(False, SKIP_NOT_TRADED)

    s_raw = float(op.at[fill, short_col])
    if not np.isfinite(s_raw) or s_raw <= 0:
        return Fill(False, SKIP_NO_PRICE)
    s_in = s_raw - xp.slippage_pts                   # premium received, so slippage reduces it
    w_in = 0.0
    if wing_col is not None:
        w_raw = float(op.at[fill, wing_col])
        if not np.isfinite(w_raw) or w_raw <= 0:
            return Fill(False, SKIP_NO_PRICE)
        w_in = w_raw + xp.slippage_pts               # the wing is bought, so slippage adds
    credit = s_in - w_in
    if credit <= 0:
        return Fill(False, "the wing costs more than the option sold — no credit")

    f = Fill(True, None, f"NIFTY{strike:.0f}{typ}", float(strike), typ,
             str(day_chain["expiry"]), (day_chain["expiry"] - d).days,
             credit, LOT * xp.lots, xp.lots)
    f.wing = float(wing) if wing is not None else None      # type: ignore[attr-defined]
    f.short_in = s_in                                        # type: ignore[attr-defined]
    f.wing_in = w_in                                         # type: ignore[attr-defined]
    return f


def manage_short(day: pd.DataFrame, day_chain: dict, sig, fill: Fill,
                 xp: ExecParams) -> dict:
    """Run the credit spread to its stop, its target, the underlying, or the close.

    Target and stop are measured on the NET credit, because the spread is one position and
    managing the short leg alone would unwind the protection at the worst possible moment.
    """
    cl, op = day_chain["cl"], day_chain["op"]
    short_col = _col(cl, fill.option_type, fill.strike)
    wing = getattr(fill, "wing", None)
    wing_col = _col(cl, fill.option_type, wing) if wing is not None else None
    credit = float(fill.entry)
    start = sig.fill_minute
    last = min(xp.squareoff, int(cl.index.max()))
    end = min(last, start + xp.max_hold_min) if xp.max_hold_min else last

    stop_at = credit * (1 + xp.premium_stop_pct / 100) if xp.premium_stop_pct else None
    target_at = credit * (1 - xp.premium_target_pct / 100) if xp.premium_target_pct else None
    bullish = sig.direction == "BULL"
    spot_by_min = day.set_index("minute")

    def value(m: int, adverse: bool) -> Optional[float]:
        """The spread marked against us when adverse: short at its high, wing at its low."""
        hi, lo = day_chain["hi"], day_chain["lo"]
        try:
            s_ = float((hi if adverse else lo).at[m, short_col])
            if not np.isfinite(s_):
                return None
            if wing_col is None:
                return s_
            w_ = float((lo if adverse else hi).at[m, wing_col])
            return s_ - (w_ if np.isfinite(w_) else 0.0)
        except (KeyError, TypeError):
            return None

    reason, exit_min = EXIT_EOD, end
    for m in range(start, end):
        if xp.use_underlying_stop and m in spot_by_min.index:
            bar = spot_by_min.loc[m]
            adv = float(bar["low"]) if bullish else float(bar["high"])
            if (adv <= sig.stop_level) if bullish else (adv >= sig.stop_level):
                reason, exit_min = EXIT_UNDERLYING, m + 1
                break
        v_bad = value(m, adverse=True)
        if v_bad is not None and stop_at is not None and v_bad >= stop_at:
            reason, exit_min = EXIT_PREMIUM, m + 1
            break
        v_good = value(m, adverse=False)
        if v_good is not None and target_at is not None and v_good <= target_at:
            reason, exit_min = EXIT_TARGET, m + 1
            break
    if reason == EXIT_EOD and xp.max_hold_min and end < last:
        reason = EXIT_TIME

    exit_min = min(exit_min, end)

    def out(c) -> float:
        v = float(op.at[exit_min, c]) if (exit_min in op.index
                                          and np.isfinite(op.at[exit_min, c])) \
            else float(cl.at[min(exit_min, end), c])
        return v

    s_out = out(short_col) + xp.slippage_pts                 # bought back, costs more
    w_out = max(0.0, out(wing_col) - xp.slippage_pts) if wing_col is not None else 0.0
    qty = fill.qty
    gross = (getattr(fill, "short_in", credit) - s_out) * qty
    charges = COST.round_trip(buy_px=s_out, sell_px=getattr(fill, "short_in", credit), qty=qty)
    if wing_col is not None:
        w_in = getattr(fill, "wing_in", 0.0)
        gross += (w_out - w_in) * qty
        charges += COST.round_trip(buy_px=w_in, sell_px=w_out, qty=qty)
    width = abs(float(wing) - float(fill.strike)) if wing is not None else None
    return {
        "exit_reason": reason, "exit_minute": int(exit_min),
        "exit_time": f"{int(exit_min) // 60:02d}:{int(exit_min) % 60:02d}",
        "option_entry": round(credit, 2), "option_exit": round(s_out - w_out, 2),
        "credit": round(credit, 2), "spread_width": width,
        "max_loss": round(width * qty - credit * qty, 2) if width else None,
        "held_min": int(exit_min - start),
        "gross": round(gross, 2), "charges": round(charges, 2),
        "pnl": round(gross - charges, 2),
        "premium_stop": round(stop_at, 2) if stop_at else None,
        "ambiguous_bar": reason in (EXIT_UNDERLYING, EXIT_PREMIUM),
        "stop_was_gapped": False,
    }
