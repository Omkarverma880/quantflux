# Index Straddle Engine

Intraday short (or long) straddle / strangle on NIFTY, selected by distance to
expiry. Backtest, paper and live share one config and one strike resolver, so a
backtested day and a live day pick the same contracts from the same spot.

**Route prefix** `/api/index-strategy/straddle` · **UI** `/index-strategy/straddle`

---

## Where it came from

This is the surviving result of a study that set out to find a profitable
intraday **option-buying** entry on NIFTY. Across 4.7 years of 1-minute data
(162,573 entry points × 2 directions) every buying variant lost, because:

| | |
|---|---|
| Best option structure, random entry | **−1.61% / trade** |
| Strongest measurable directional signal (OR30 break, t = 9.3) | 1.8 index points ≈ **+1.0% of premium** |

The edge is smaller than the friction. Turning the same measurements around —
same instrument, same window, opposite side — is this strategy.

## The result on research defaults

`SHORT ATM straddle · 0–1 DTE · 10:00→15:20 · stop −35% · skip prior-day range > 1.3σ · 3 lots × 65`

| Metric | Value |
|---|---|
| Trades (4.7y) | 421 |
| Win rate | 72.9% |
| Mean / trade | +16.3% of credit |
| t-stat | +10.0 |
| P&L / year | ≈ ₹4.8L on 3 lots |
| Max drawdown | ≈ ₹65k |
| Discovery → holdout | +14.28% (t 7.12) → **+20.70% (t 7.48)** |

The holdout is *stronger* than discovery — the opposite of an overfitting
signature.

### Why only 0–1 DTE

Theta is not spread evenly across the week. This single filter is the strategy:

| DTE | Mean / trade |
|---|---|
| 0–1 | **+14.80%** |
| 2–3 | +0.03% |
| 4–6 | **−2.36%** |

## ⚠ The assumption that could make it wrong

The premium model is calibrated to **one trading day** of a real chain
(2026-09-11, r = 0.988, MAE 8.4 pts) — but that day was **4 DTE**, and all the
profit sits at **0–1 DTE**, where the dataset has no observed option prices.
The 0-DTE anchor comes from outside the data.

Break-even is IV/realized ≈ **1.10** against the **1.30** used. In premium terms:
**the 0-DTE ATM straddle at 09:35 must exceed ~0.42% of spot** (≈ 98 points at
NIFTY 23,400).

> **Verify before sizing.** On any expiry morning at 09:35, take the ATM straddle
> ÷ spot. 110–125 points confirms the edge. Near 95 or below means the strategy
> loses money and the tables are an artefact of the assumption.

## Layout

| File | Purpose |
|---|---|
| `config.py` | Every strategy variable. Defaults == the research configuration. |
| `optmodel.py` | The calibrated premium model (`prem_frac = 0.0050·de^0.372·IV/0.106`). |
| `engine.py` | `scan` (pure price/premium) + `account` (money). Same-bar rule, causal filters. |
| `options.py` | Strike selection (ATM offset / target premium), expiry, contract resolution, real-candle source. |
| `metrics.py` | Summary, by-period, by-DTE, integrity checks. |
| `service.py` | Orchestration + the research parity comparison. |
| `parity_check.py` | CLI: does the engine still reproduce the research? |

## Verifying the engine

```bash
python -m research.index_straddle.parity_check  path/to/NIFTY50_1minute.csv
```

Expected: 421 trades, 72.9% win, every delta inside 2%. The app reports P&L on
the cash basis (credit actually received after the spread) where the research
script used the raw mid, so figures run ~1% lower by design.

Run this after any change to `engine.py` or `optmodel.py` — if it drifts, the
documentation no longer describes the code.

## Premium sources

| Source | Use |
|---|---|
| `model` | Reproduces the research over the full index history. The only way to study 4.7 years — no broker serves that much per-strike option history. |
| `broker` | The contract's own minute candles. Honest, but limited to the option history Zerodha serves. **Days without contract history are skipped, never modelled** — mixing two pricing sources silently would be worse than trading fewer days. |

## Safety

- **Paper by default.** Real orders need `paper_trade = False` *and* the global
  trading gate — the same fencing as every other live strategy here.
- **Never half-legged.** If the second leg fails to place, the first is unwound
  immediately.
- **Flat by 15:20**, every day. Nothing is carried overnight.
- Short volatility earns steadily and loses abruptly. The −35% stop is an
  intention, not a guaranteed fill: on a gap or circuit event both legs reprice
  at once. Size against a stop that does not fill.

The largest move in the whole sample — 4 June 2024, NIFTY 23,180 → 21,281, an
8.19% range — landed on a 2-DTE day and was never traded. That is luck, not
design.
