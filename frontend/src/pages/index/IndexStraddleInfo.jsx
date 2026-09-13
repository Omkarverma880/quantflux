import React from 'react';

/**
 * The ⓘ tab — the full origin story of the Index Straddle Engine.
 *
 * Written for someone opening this page cold: where the strategy came from, what
 * was tested and rejected on the way, the exact entry rules and why each one is
 * there, what the P&L does and does not model, and the one assumption that could
 * make the whole thing wrong.
 */

const INR = (v) => `₹${Number(v || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;

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

function Rule({ n, t, children }) {
  return (
    <div className="flex gap-3">
      <span className="shrink-0 w-5 h-5 mt-0.5 rounded bg-brand-500/15 text-brand-300 border border-brand-500/25 text-[10px] font-bold flex items-center justify-center">{n}</span>
      <p className="text-[13px]"><strong className="text-gray-100">{t}.</strong> {children}</p>
    </div>
  );
}

function Grid({ rows }) {
  return (
    <div className="divide-y divide-surface-3/50">
      {rows.map(([k, v]) => (
        <div key={k} className="py-1.5 grid grid-cols-1 sm:grid-cols-[200px_1fr] gap-x-3">
          <div className="text-[13px] font-semibold text-gray-200">{k}</div>
          <div className="text-[13px] text-gray-400">{v}</div>
        </div>
      ))}
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
              {head.map((h, i) => (
                <th key={h} className={`px-3 py-2 font-semibold ${i ? 'text-right' : 'text-left'}`}>{h}</th>
              ))}
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

export default function IndexStraddleInfo({ cfg, research }) {
  const c = cfg || {};
  const isShort = (c.direction || 'short') === 'short';
  const lots = Number(c.lots ?? 3);
  const lot = Number(c.lot_size ?? 65);
  const stop = Number(c.stop_pct ?? 35);

  return (
    <div className="space-y-3 max-w-5xl text-sm text-gray-300 leading-relaxed">

      <Sec title="Where this strategy came from">
        <p>
          It is the surviving result of a study that set out to do the opposite. The brief was to find
          a profitable intraday <strong>option-buying</strong> entry on NIFTY. Across 4.7 years of
          one-minute data — <strong>162,573 entry points × 2 directions</strong> — every buying variant
          lost money, and not because the timing was wrong:
        </p>
        <p className="text-[13px] bg-surface-3/40 rounded-lg p-3 border border-surface-3">
          The <strong className="text-gray-100">best</strong> option structure available returns
          <strong className="text-red-400"> −1.61% per trade on random entry</strong>. The
          <strong className="text-gray-100"> strongest</strong> directional signal measurable in the
          data (opening-range break, t = 9.3 over 42,300 samples) is worth
          <strong className="text-gray-100"> 1.8 index points ≈ +1.0% of an ATM premium</strong>.
          The edge is smaller than the friction, so no entry pattern can close the gap.
        </p>
        <p>
          A buyer losing ~3% per trade means somebody collects it. Turning the same measurements around
          — same instrument, same intraday window, opposite side — is the strategy on this page.
        </p>
      </Sec>

      <Sec title="The entry logic, and why each rule is there" tone="good">
        <Rule n="1" t="Sell the straddle, do not buy it">
          Both legs at one strike, {isShort ? 'sold' : 'bought'} together. The position is non-directional
          at entry: it makes money from time passing, not from guessing which way NIFTY goes. That matters
          because the study found direction is genuinely unpredictable — a 77-feature model under purged
          walk-forward validation reached a rank correlation of <strong>0.036</strong>, which is noise.
        </Rule>
        <Rule n="2" t="Only 0–1 days to expiry — this single filter IS the strategy">
          Theta is not spread evenly across the week. Measured per trade: <strong>0–1 DTE +14.80%</strong>,
          <strong> 2–3 DTE +0.03%</strong>, <strong className="text-red-400">4–6 DTE −2.36%</strong>.
          Outside the final two sessions there is nothing to harvest. It is also the precise mirror of the
          buy-side finding that expiry-day <em>buying</em> is the worst cell in the entire study at −8.12%.
        </Rule>
        <Rule n="3" t="Enter at 10:00, not at the open">
          The opening auction's noise widens spreads and clusters the early stop-outs. Entering at 09:20
          returns +11.71% per trade; 10:00 returns <strong>+15.39%</strong>. Anywhere between 09:45 and
          11:15 performs the same within noise — the rule is &quot;after the open settles&quot;, not a
          magic minute.
        </Rule>
        <Rule n="4" t="Skip the day after a wide prior-day range">
          The one filter that is close to free. Skipping sessions that follow a prior-day range above
          1.3σ removes only 42 of 463 trades while improving both the mean return and the drawdown.
          Volatility clusters, so yesterday's big range is a real warning about today.
        </Rule>
        <Rule n="5" t="Stop on the COMBINED premium, not per leg">
          The risk is the structure, not either option. When the two legs together move {stop}% against
          the position, both are closed at once. Results are flat for stops between −25% and −50%; a
          tighter stop gives near-identical return with roughly half the drawdown.
        </Rule>
        <Rule n="6" t="Flat by 15:20, every day, no exceptions">
          The 1-minute ATM bar range widens from 3.2% of premium to <strong>5.3%</strong> after 15:15 —
          exit liquidity is worst exactly when most intraday traders need it. Nothing is ever carried
          overnight.
        </Rule>
      </Sec>

      <Sec title="What the numbers were, on the research defaults">
        <T
          head={['Metric', 'Value']}
          rows={[
            { c: ['Trades (4.7 years)', '421'] },
            { c: ['Win rate', '72.9%'], tone: [null, 'g'] },
            { c: ['Mean return per trade', '+16.31% of credit'], tone: [null, 'g'] },
            { c: ['t-statistic', '+10.00'], tone: [null, 'g'] },
            { c: ['P&L per year (3 lots × 65)', INR(483431)], tone: [null, 'g'], hl: true },
            { c: ['Max drawdown', INR(-65297)], tone: [null, 'r'] },
            { c: ['Worst single day', INR(-31136)], tone: [null, 'r'] },
            { c: ['Stop-out rate', '13.8% of days'] },
            { c: ['Frequency', '1.9 trades per week'] },
          ]}
          note="Discovery (to Mar-2025) +14.28% at t = 7.12 → holdout (Apr-2025 on) +20.70% at t = 7.48. The holdout is stronger than the discovery period, which is the opposite of an overfitting signature."
        />
        <p className="text-[12.5px] text-gray-500">
          Your backtest reports P&amp;L on the cash basis — the credit actually received after the spread —
          where the research script used the raw mid. That makes app figures run about <strong>1% lower</strong>.
          Everything else reproduces exactly; the <strong>Parity</strong> block on the Backtest tab shows the
          comparison line by line.
        </p>
      </Sec>

      <Sec title="Why the long straddle is offered but not recommended" tone="bad">
        <p>
          The mirror structure was tested on the same data and it loses, decisively:
        </p>
        <T
          head={['Long straddle variant', 'Win', 'Mean/trade', 't', '₹/year']}
          rows={[
            { c: ['All days pooled', '21.8%', '−7.35%', '−8.95', INR(-686408)], tone: [null, null, 'r', 'r', 'r'] },
            { c: ['0–1 DTE', '~22%', '−14.9%', '−12.6', INR(-497000)], tone: [null, null, 'r', 'r', 'r'] },
            { c: ['With a +20% target', '29.6%', '−15.65%', '−13.02', INR(-522942)], tone: [null, null, 'r', 'r', 'r'] },
            { c: ['Strangle, 150 wide', '13.0%', '−20.99%', '−6.08', INR(-250788)], tone: [null, null, 'r', 'r', 'r'] },
            { c: ['High-expansion days only', '38.3%', '+2.95%', '+0.92', INR(40852)], hl: true },
          ]}
          note="Note the target row: adding a +20% target RAISES the win rate to 29.6% and WORSENS expectancy — it caps the rare large winner that is the only reason to own convexity."
        />
        <p>
          It is in the engine so the claim can be re-checked rather than taken on trust. Only the last row
          is not clearly negative, and at t = +0.92 it is not distinguishable from zero.
        </p>
      </Sec>

      <Sec title="Win rate is a dial, not an edge" tone="warn">
        <p>
          The most common intuition about option buying is that a 60–70% win rate would fix it. It would
          not. Win rate is set by where the stop sits relative to the target, so it can be tuned to any
          number — and on a negative-edge trade, raising it makes things worse:
        </p>
        <T
          head={['Target / Stop', 'Win rate', 'Expectancy', '₹/trade (3 lots)']}
          rows={[
            { c: ['+15% / −15%', '38.8%', '−3.05%', '−610'], tone: [null, null, 'r', 'r'] },
            { c: ['+8% / −30%', '53.5%', '−3.28%', '−649'], tone: [null, null, 'r', 'r'] },
            { c: ['+5% / −50%', '66.0%', '−3.54%', '−699'], tone: [null, null, 'r', 'r'] },
            { c: ['+3% / −75%', '79.5%', '−4.13%', '−796'], tone: [null, null, 'r', 'r'], hl: true },
          ]}
          note="A 79.5% win rate is available immediately. It loses more per trade than the 38.8% version. This is the shape of every high-win-rate option-buying system sold to retail traders."
        />
      </Sec>

      <Sec title="The assumption that could make all of this wrong" tone="warn">
        <p>
          Read this before sizing anything. The premium model is calibrated to <strong>one trading
          day</strong> of a real NIFTY chain — 11 September 2026, at <strong>4 DTE</strong>. It validated
          well there (r = 0.988 against traded premiums, mean error 8.4 points). But every rupee of this
          strategy&apos;s profit sits at <strong>0–1 DTE</strong>, where the dataset contains no observed
          option prices at all. The 0-DTE premium level is an anchor brought in from outside the data.
        </p>
        <p>
          Break-even sits at implied/realized ≈ <strong>1.10</strong> against the <strong>1.30</strong> the
          model uses. In premium terms: <strong className="text-amber-300">the 0-DTE ATM straddle at 09:35
          must be worth more than about 0.42% of spot.</strong>
        </p>
        <div className="text-[13px] bg-amber-500/10 border border-amber-500/25 rounded-lg p-3">
          <strong className="text-amber-300">The one-minute live test.</strong> On any expiry morning at
          09:35, take the ATM straddle and divide by spot. At NIFTY 23,400 break-even is about
          <strong> 98 points</strong>. If it consistently prints 110–125, the edge is real and these
          numbers roughly hold. If it prints near 95 or below, this strategy loses money and the tables
          above are an artefact of the assumption rather than a finding. Check it over three or more
          expiries, and run the Backtest tab on <em>Real option candles</em> once you have the history.
        </div>
      </Sec>

      <Sec title="Tail risk — the part that decides business or blow-up" tone="bad">
        <ul className="list-disc pl-5 space-y-1 text-[13px]">
          <li>Stopped out on <strong>13.8%</strong> of days; worst single day <strong>{INR(-31136)}</strong> on 3 lots.</li>
          <li>Deepest drawdown <strong>{INR(-65297)}</strong>, about 11% of the assumed {INR(570000)} margin, recovered within weeks. Worst losing streak: four days.</li>
          <li>
            <strong>The largest move in the sample was never traded.</strong> 4 June 2024 — NIFTY opened
            23,180 and fell to 21,281, an 8.19% range — landed on a 2-DTE day, outside the window. That is
            luck, not design. The all-expiry version was stopped at −50% that day.
          </li>
          <li>
            Stress test: if every stopped trade exits at <strong>twice</strong> the modelled loss, the annual
            return falls from {INR(488003)} to <strong>{INR(298452)}</strong>. The edge survives bad fills.
            It would not survive a session where no fill is available at all — and that is precisely the risk
            being paid for here.
          </li>
        </ul>
        <p className="text-[13px]">
          Short volatility earns steadily and loses abruptly. Size against the possibility of a stop that
          does not fill, not against the historical drawdown.
        </p>
      </Sec>

      <Sec title="What the backtest models, and what it does not">
        <Grid rows={[
          ['Friction applied', '0.8% of premium per side (measured from real 1-minute ATM bar ranges) plus 0.4% round-trip brokerage and taxes. Sell at the bid, buy at the ask.'],
          ['Same-bar rule', 'Within one minute we cannot know whether the high or the low came first, so the position is always marked against the WORSE extreme. A stop that could have been hit is always taken.'],
          ['Causality', 'σ, the implied-vol regime and every day filter are computed from data strictly before the session they gate. A day never sees its own volatility.'],
          ['One trade per day', 'At most one structure per session. Verified by an integrity check on every run.'],
          ['NOT modelled — slippage on stops', 'The stop assumes a fill at the modelled price. On a gap or circuit event both legs reprice at once and the fill can be far worse.'],
          ['NOT modelled — margin calls', 'Span margin is assumed available and static. A volatility spike raises margin exactly when the position is losing.'],
          ['NOT modelled — liquidity', 'Deep strikes on a quiet expiry can be far wider than 0.8% per side.'],
        ]} />
      </Sec>

      <Sec title="Configuration guide">
        <Grid rows={[
          ['Direction', 'short sells the structure (the researched edge), long buys it (documented as losing).'],
          ['Structure', 'straddle uses one strike for both legs; strangle places wings either side of ATM.'],
          ['Strike — distance from ATM', 'Signed points. 0 = ATM, −200 = 200 ITM, +200 = 200 OTM. Resolved per option type, so −200 means 200 ITM for the call and the put alike.'],
          ['Strike — target premium', 'Instead of a distance, pick the strike whose premium prices nearest your target. "Sell the ₹100 option" rather than "sell the 200-OTM option" — the strike that delivers it moves with volatility.'],
          ['DTE window', '0–1 is the researched setting. Widening it will lower expectancy; the by-DTE table on the Backtest tab shows exactly how much.'],
          ['Premium source', 'model reproduces the research over the full history. broker uses the contract\'s own candles — honest but limited to the option history Zerodha serves, and days without it are skipped rather than modelled.'],
          ['Paper trade', 'ON by default and stays on until you turn it off. Real orders additionally require the global trading gate.'],
        ]} />
      </Sec>

      <Sec title="How to run this properly">
        <Rule n="1" t="Reproduce the research">
          Leave every default alone, upload your NIFTY 1-minute CSV, run the backtest, and check the
          <strong> Parity</strong> block. It should show 421 trades at a 72.9% win rate with every delta
          inside 2%. If it does not, something changed and the numbers should not be trusted.
        </Rule>
        <Rule n="2" t="Verify the premium assumption">
          Run the 09:35 straddle test above on three expiry mornings. This is the single most important
          check, and no amount of backtesting substitutes for it.
        </Rule>
        <Rule n="3" t="Re-run on real option candles">
          Switch the premium source to broker over whatever option history you have. Expect fewer trades
          and different numbers — that is the point of the comparison.
        </Rule>
        <Rule n="4" t="Paper trade a full expiry cycle">
          At least 8–10 live paper trades before risking capital, watching whether real fills match the
          modelled ones on stop days in particular.
        </Rule>
        <Rule n="5" t="Size for the tail, then go live">
          {lots} lot{lots === 1 ? '' : 's'} × {lot} is the researched size against roughly {INR(570000)} of
          margin. Keep several times the historical drawdown in reserve, because the worst day in the
          sample is not the worst day possible.
        </Rule>
      </Sec>

      <p className="text-[12px] text-gray-500 pt-1">
        Backtested results are not predictive of future returns. Nothing here is investment advice.
      </p>
    </div>
  );
}
