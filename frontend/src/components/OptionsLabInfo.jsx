import React from 'react';

/**
 * Options Lab — How it works, and the full research record on real option prices.
 *
 * Written so the next person (or the next idea) starts from what was already
 * measured, instead of re-discovering the same traps.
 */

function Sec({ title, tone = '', children }) {
  const ring = tone === 'bad' ? 'border-red-500/25' : tone === 'good' ? 'border-green-500/25'
    : tone === 'warn' ? 'border-amber-500/30' : 'border-surface-3';
  return (
    <div className={`bg-surface-2 border ${ring} rounded-xl p-4 space-y-2`}>
      <h3 className="text-sm font-semibold text-gray-100">{title}</h3>
      {children}
    </div>
  );
}

function T({ head, rows, note }) {
  return (
    <div className="space-y-1.5">
      <div className="overflow-x-auto rounded-lg border border-surface-3">
        <table className="w-full text-[12.5px]">
          <thead>
            <tr className="bg-surface-3/60 text-gray-400">
              {head.map((h, i) => <th key={h} className={`px-3 py-2 font-semibold ${i ? 'text-right' : 'text-left'}`}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, ri) => (
              <tr key={ri} className={`border-t border-surface-3/50 ${r.hl ? 'bg-brand-500/10' : ''}`}>
                {r.c.map((c, ci) => (
                  <td key={ci} className={`px-3 py-1.5 ${ci ? 'text-right mono' : 'text-gray-200'} ${
                    r.tone?.[ci] === 'g' ? 'text-green-400' : r.tone?.[ci] === 'r' ? 'text-red-400' : 'text-gray-300'}`}>{c}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {note && <p className="text-[12px] text-gray-500">{note}</p>}
    </div>
  );
}

function Rule({ n, t, children }) {
  return (
    <div className="flex gap-3">
      <span className="shrink-0 w-5 h-5 mt-0.5 rounded bg-brand-500/15 text-brand-300 border border-brand-500/25 text-[10px] font-bold flex items-center justify-center">{n}</span>
      <p className="text-[13px]"><strong className="text-gray-100">{t}.</strong> {children}</p>
    </div>
  );
}

export default function OptionsLabInfo() {
  return (
    <div className="space-y-3 max-w-5xl text-sm text-gray-300 leading-relaxed">
      <Sec title="The short version" tone="bad">
        <p>
          Three years of real, expired NIFTY weekly contracts (Sep-2023 → Sep-2026, 6.08 million option bars,
          11 strikes either side of the money, with OI, IV and volume) were tested for a profitable intraday
          setup — buying and selling. Every candidate that looked profitable was then attacked: a sealed holdout
          period it had never seen, realistic Zerodha costs and margin, and the data's own blind spots.
        </p>
        <p className="text-[13px] bg-red-500/10 border border-red-500/25 rounded-lg p-3">
          <strong className="text-red-200">No setup survived.</strong> Nothing in this lab is a validated money-making
          strategy, so no live or paper engine is armed from it. What it gives you is a backtester you can trust
          on real prices — including to reject ideas quickly, which is most of what it is for.
        </p>
      </Sec>

      <Sec title="What was tested, and what happened">
        <T
          head={['Approach', 'Best discovery', 'Sealed holdout', 'Why it failed']}
          rows={[
            { c: ['Random option buying (every exit, strike, DTE, hour)', 't ≤ −10', '—', 'Negative in every slice: −1.3% to −7.7% per trade'], tone: [null, 'r', null, null] },
            { c: ['ML ranking model, 70 features incl. option chain', 't = 2.39', 't = 0.57', 'Top 5 of 39 trades = 334% of the return; median trade −16%'], tone: [null, null, 'r', null] },
            { c: ['Pure direction skill (call vs put, same minute)', '51.7% right', '50.9% right', 'A coin flip at 60 minutes'], tone: [null, null, 'r', null] },
            { c: ['Option-chain events: short covering, OI walls, skew, flow', 't = 1.46', '—', '90 cells tested, none cleared t > 2 even before the holdout'], tone: [null, 'r', null, null] },
            { c: ['Long straddle when implied vol looks cheap', 'negative', '—', 'Loses in every IV and regime bucket'], tone: [null, 'r', null, null] },
            { c: ['Short straddle, model-timed', 't = 3.02', 't = 1.03', 'No better than selling blindly; needs ~₹1.9L margin/lot'], tone: [null, null, 'r', null] },
            { c: ['Iron fly, 200-pt wings, non-expiry days', 't = 5.09', 't = 2.35', 'Artifact: 48% of trades priced on a leg with no real trade at exit'], tone: [null, 'g', 'g', null], hl: true },
            { c: ['Iron fly, 50–150-pt wings (legs observed)', 't ≤ 1.3', 't ≤ 0.8', 'Where prices are real it loses or is flat'], tone: [null, null, 'r', null] },
            { c: ['Short straddle from the Index Straddle Engine', 't ≤ 1.9', '—', 'Modelled ₹4.8L/yr became −₹14k to +₹66k/yr'], tone: [null, null, null, null] },
          ]}
          note="Gann levels were tested separately against the same grid shifted to random places: reversal rate 52.6% vs 52.2% for random grids, and follow-through after a cross was worse than every control. No detectable effect."
        />
      </Sec>

      <Sec title="The trap that nearly produced a fake strategy" tone="warn">
        <p>
          The iron fly looked like the answer: discovery t = 5.09, and it held on the sealed holdout at t = 2.35.
          It was wrong, and the way it was wrong is the most important thing to know about this dataset.
        </p>
        <p>
          The files contain strikes within ±5 of the money <em>at each minute</em>. When NIFTY moves 150 points,
          a wing 200 points away drifts outside that window and simply stops appearing. The last price it traded at
          is then carried forward for hours — and at 15:15 the backtest "sells" that wing at a stale price far above
          what it was really worth. On 5-Dec-2025 one put wing had no trade for <strong>261 minutes</strong>.
        </p>
        <T
          head={['Wing width', 'Legs unobserved at exit', 'Holdout t (as carried)']}
          rows={[
            { c: ['±50 pts', '8.6%', '−3.48'], tone: [null, null, 'r'] },
            { c: ['±100 pts', '16.5%', '−0.76'], tone: [null, null, 'r'] },
            { c: ['±150 pts', '32.7%', '+0.35'] },
            { c: ['±200 pts', '48.1%', '+2.35'], tone: [null, null, 'g'], hl: true },
          ]}
          note="The apparent profit rises exactly in step with the share of unobserved prices. That is the signature of an artifact, not an edge. This is why the backtester prices unobserved legs adversely by default."
        />
      </Sec>

      <Sec title="How the backtester keeps results honest">
        <Rule n="1" t="Real contracts, never a rolling series">
          The downloaded "ATM" files switch contracts intraday — up to 34 times on one day. A signal names a side
          and moneyness; the strike is fixed from spot at the decision minute, and that exact contract is held by
          name until exit.
        </Rule>
        <Rule n="2" t="Next-minute fills">
          Decisions use the close of minute t; the fill is the open of minute t+1. Nothing trades on a price it
          could not have seen.
        </Rule>
        <Rule n="3" t="Adverse inside the bar, and through gaps">
          Within a minute the stop is tested before the target. A price that exactly touches the stop is a stop.
          If a bar opens beyond the stop, the fill is that open.
        </Rule>
        <Rule n="4" t="Unobserved legs priced against you">
          A long leg with no trade at exit is valued at intrinsic; a short leg at its carried price +25%. Toggle it
          off to see how much a result depends on unobserved prices — if the answer is "all of it", the result is
          not real.
        </Rule>
        <Rule n="5" t="Same-instant marking for multi-leg structures">
          Legs are valued on their closes in the same minute. Adding each leg's own worst tick overstates losses,
          because a call and a put do not peak in the same minute.
        </Rule>
        <Rule n="6" t="Real costs and real margin">
          Zerodha statutory charges on every leg both ways, plus slippage. Short structures are sized by margin per
          lot, not premium — with ₹1 lakh you cannot sell even one naked NIFTY straddle.
        </Rule>
        <Rule n="7" t="Two engines, cross-checked">
          The lab engine was verified against an independently written research engine on 1,000 random trades:
          99.9–100% agree within 0.1 percentage point.
        </Rule>
      </Sec>

      <Sec title="Reading a result before believing it">
        <Rule n="1" t="t-stat above 2, with enough trades">
          Below that, a positive number is indistinguishable from luck. Test many variants and some will clear 2 by
          chance alone — roughly 1 in 40.
        </Rule>
        <Rule n="2" t="It must hold after the holdout date">
          The before/after table splits at 1-Dec-2025. If the right-hand side loses its sign or most of its size,
          the left-hand side was fitted.
        </Rule>
        <Rule n="3" t="Check the unobserved-leg count">
          If a large share of trades has unobserved legs, turn adverse pricing off and on. A result that flips is
          measuring the data window, not the market.
        </Rule>
        <Rule n="4" t="Look at the median trade, not just the mean">
          A strategy whose median trade loses and whose total comes from a handful of days is a lottery ticket.
        </Rule>
        <Rule n="5" t="Size for the worst trade you have not seen yet">
          The largest move in the sample is not the largest move possible.
        </Rule>
      </Sec>

      <Sec title="About the daily ₹5k–₹40k target on ₹1 lakh" tone="warn">
        <p>
          That is 5–40% of capital per day. Compounded over a year even the low end turns ₹1 lakh into an
          astronomical number, which is why no strategy — here or anywhere — sustains it. Chasing that target is
          what pushes position sizes to where a single bad day ends the account. A realistic, validated edge in
          liquid index options is measured in a few percent a <em>month</em> at best, with drawdowns larger than
          any single month's gain.
        </p>
      </Sec>

      <Sec title="What would change these conclusions">
        <Rule n="1" t="A wider strike window">
          Downloading ±10 or ±15 strikes instead of ±5 would make wide-wing structures testable without guessing at
          unobserved prices.
        </Rule>
        <Rule n="2" t="Next-week expiry contracts">
          Only the current weekly expiry is in the data, so calendar and cross-expiry ideas cannot be tested yet.
        </Rule>
        <Rule n="3" t="Bid/ask or depth">
          One-minute OHLC hides the spread. Real quotes would replace the slippage assumption with measurement.
        </Rule>
        <Rule n="4" t="Longer history">
          Three years includes one strong down-leg (2026). More regimes would make any future candidate far more
          trustworthy.
        </Rule>
      </Sec>

      <p className="text-[12px] text-gray-500 pt-1">Backtested results are not predictive of future returns. Nothing here is investment advice.</p>
    </div>
  );
}
