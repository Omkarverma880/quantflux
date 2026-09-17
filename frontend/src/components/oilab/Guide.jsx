import React from 'react';
import { Section } from './ui';

/**
 * How to read the OI Lab — the trading logic behind every panel, and its tested limits.
 */

const BLOCKS = [
  {
    title: '1. Why writers matter more than buyers',
    body: [
      'Most index options are written (sold) by well-capitalised desks and expire worthless. Buyers are many and small. So open interest is mostly a map of where sellers have committed capital.',
      'Heavy, growing PUT writing at a strike = sellers betting spot stays ABOVE it → support. Heavy CALL writing = sellers betting spot stays BELOW it → resistance.',
      'When writers are under water they cover (buy back), which pushes price further through their strike. That is why a broken wall often runs.',
    ],
  },
  {
    title: '2. Reading a strike',
    body: [
      'Buildup combines price change and OI change: Short Buildup (price ↓, OI ↑) = fresh writing; Long Buildup (price ↑, OI ↑) = fresh buying; Short Covering (price ↑, OI ↓) = writers exiting at a loss; Long Unwinding (price ↓, OI ↓) = buyers exiting.',
      'Writer conviction (0–100) scores size of OI, fresh additions today, whether today\'s writers are in profit (LTP below the day\'s average price) and whether OI has fallen from its intraday peak (covering).',
      'Winner = the side with clearly higher conviction at that strike. "Calls below spot" / "Puts above spot" flag writers on the wrong side of price — their covering becomes fuel if price holds.',
    ],
  },
  {
    title: '3. Zones, not lines',
    body: [
      'An accumulation zone is the strongest wall plus neighbouring strikes that carry at least 70% of its score. Price usually reacts near the zone, not at the exact strike.',
      'A wall losing OI from its day high while price approaches it is the classic early warning of a break.',
      'Wall odds are measured in ATM straddles (the market\'s own expected move): 100 points is far on expiry afternoon and close on a volatile Monday.',
    ],
  },
  {
    title: '4. Buyer vs seller fight',
    body: [
      'The order book shows pending bid vs ask quantity and the top 5 levels. Bid-heavy calls with ask-heavy puts = upside demand. Pending orders can be pulled, so treat it as a hint.',
      'Premium vs VWAP is harder to fake: calls trading above today\'s average while puts trade below means upside buyers are winning the day.',
    ],
  },
  {
    title: '5. Greeks in one line each',
    body: [
      'Delta — premium change per 1-point spot move (≈ probability of expiring in the money). Gamma — how fast delta changes; the "gamma magnet" strike is where price tends to pin, especially on expiry. Theta — premium lost per day from time alone; shown per lot. Vega — premium change per 1% IV. IV — implied volatility backed out of the live premium (same method as the stored history).',
    ],
  },
  {
    title: '6. What was tested, and what it showed',
    body: [
      'Everything in the History tab is trained on NIFTY sessions before Dec 2025 and scored only on sessions after it.',
      'Strong: whether the call wall / put wall holds until the close, and whether it is a range day (AUC ≈ 0.81–0.85, calibrated). How far spot still travels before the close (correlation ≈ 0.76 for the total range).',
      'No edge: whether spot closes higher or is higher in an hour (AUC ≈ 0.49–0.53). The "who is in control" bias describes positioning; it is not a forecast.',
      'Setups: replaying the full engine on 70 unseen sessions, its target/stop odds were close to reality, but its edge score did not pick winners. Use setups to plan entries, stops and size — not as signals on their own.',
    ],
  },
  {
    title: '7. A practical routine',
    body: [
      '09:20–09:45 — let OI settle; note the first walls and whether writers add on puts or calls.',
      'Check the tested wall odds and the range-day probability. High range odds → fade the walls with stops beyond them; low → plan for the weaker wall to break.',
      'Only act in an entry zone, size to the stop, and respect the time-decay warning: on expiry afternoon an option buyer needs a move fast.',
      'Re-check the timeline: walls stepping in your direction confirm; a wall losing OI against you is the exit signal.',
    ],
  },
];

export default function Guide() {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {BLOCKS.map((b) => (
        <Section key={b.title} title={b.title}>
          <div className="space-y-2">
            {b.body.map((t, i) => <p key={i} className="text-[12.5px] leading-relaxed text-gray-300">{t}</p>)}
          </div>
        </Section>
      ))}
      <div className="lg:col-span-2 text-[11.5px] text-gray-500">
        Research and education only — nothing here places orders or is investment advice. Live data comes from your Zerodha session;
        history comes from the Market Store (NIFTY, 3 years, 1-minute option bars).
      </div>
    </div>
  );
}
