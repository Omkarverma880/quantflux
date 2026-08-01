"""
Realistic transaction-cost model for the research modules.

Turns *gross* backtest P&L into *net* — approximating brokerage, statutory
charges (STT / exchange / SEBI / stamp / GST) and slippage for an Indian
round-trip trade. Everything is configurable (rates change over time); the
defaults are sensible ballparks for cash-equity delivery and index/stock
options. Pure and unit-testable — no I/O.

A round-trip cost is modelled as three additive components:
  • slippage   = slippage_bps × (entry_turnover + exit_turnover)
  • brokerage  = 2 × brokerage_per_order   (one buy + one sell order)
  • charges    = charges_pct × (entry_turnover + exit_turnover)   (STT + fees)

where turnover = price × quantity for that side.
"""
from __future__ import annotations


def roundtrip_cost(entry: float, exit_: float, qty: float, *,
                   slippage_bps: float, brokerage_per_order: float,
                   charges_pct: float) -> float:
    """Total round-trip cost (₹, positive) for buying then selling ``qty`` units.

    ``slippage_bps`` is basis points applied to each side (you buy a touch
    higher and sell a touch lower). ``charges_pct`` is a percentage of the
    two-sided turnover that folds STT + exchange + SEBI + stamp + GST into one
    tunable number. ``brokerage_per_order`` is a flat ₹ amount per order.
    """
    if qty <= 0:
        return 0.0
    entry_val = abs(entry) * qty
    exit_val = abs(exit_) * qty
    turnover = entry_val + exit_val
    slippage = (float(slippage_bps) / 1e4) * turnover
    brokerage = 2.0 * float(brokerage_per_order)
    charges = (float(charges_pct) / 100.0) * turnover
    return round(slippage + brokerage + charges, 2)


def cost_config(cfg: dict, kind: str) -> dict:
    """Extract the cost knobs from a module config with kind-appropriate
    fallbacks (``kind`` = 'equity' | 'option')."""
    if kind == "option":
        d_slip, d_brok, d_chg = 20.0, 20.0, 0.10
    else:
        d_slip, d_brok, d_chg = 5.0, 0.0, 0.12
    return {
        "slippage_bps": float(cfg.get("slippage_bps", d_slip) or 0),
        "brokerage_per_order": float(cfg.get("brokerage_per_order", d_brok) or 0),
        "charges_pct": float(cfg.get("charges_pct", d_chg) or 0),
    }
