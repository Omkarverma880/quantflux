import React from 'react';

/**
 * The "How it works" panel for the NIFTY open-reversion desk.
 *
 * Written for someone opening the page for the first time: what the strategy
 * is, the exact rules, how sizing works, what the P&L does and does NOT model,
 * how to run a backtest, and how to read every number it produces.
 */

const INR = (v) => `₹${Number(v || 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`;

function Sec({ title, children }) {
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-2">
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
        <div key={k} className="py-1.5 grid grid-cols-1 sm:grid-cols-[190px_1fr] gap-x-3">
          <div className="text-[13px] font-semibold text-gray-200">{k}</div>
          <div className="text-[13px] text-gray-400">{v}</div>
        </div>
      ))}
    </div>
  );
}

export default function NiftyOpenReversionInfo({ cfg }) {
  const c = cfg || {};
  const off = Number(c.entry_offset ?? 50);
  const sl = Number(c.stop_loss ?? 50);
  const tp = Number(c.target ?? 50);
  const lot = Number(c.lot_size ?? 65);

  return (
    <div className="space-y-3 max-w-5xl text-sm text-gray-300 leading-relaxed">
      <Sec title="What this strategy is">
        <p>
          A <strong>mean-reversion fade of the opening print</strong> on the NIFTY index. Each morning the
          09:15 one-minute candle&apos;s open becomes the day&apos;s anchor. Two levels sit a fixed distance
          either side of it: if price falls to the lower one we buy, expecting a drift back toward the open;
          if it rises to the upper one we sell, for the same reason.
        </p>
        <p>
          Stop and target are the same size, so the edge — if there is one — has to come from the{' '}
          <em>hit rate</em>, not from reward-to-risk. It is purely intraday: everything is flat before the
          close, every single day.
        </p>
      </Sec>

      <Sec title="The exact rules">
        <Rule n="1" t="Daily anchor">
          The open of the <strong>09:15 one-minute candle specifically</strong> — not the previous close, not a
          daily bar, and not &quot;the first row that happens to exist&quot;. If a session has no 09:15 bar the
          run reports it in the integrity checks instead of quietly substituting something else.
        </Rule>
        <Rule n="2" t="The two levels">
          BUY at <code>open − {off}</code>, SELL at <code>open + {off}</code>. In percent mode the distance is
          that percentage of the open instead of a fixed number of points.
        </Rule>
        <Rule n="3" t="Entry">
          Fires when a candle&apos;s <strong>low reaches the BUY level</strong> (or its{' '}
          <strong>high reaches the SELL level</strong>). The fill is the level itself, never the candle&apos;s
          close — you had a resting order sitting there.
        </Rule>
        <Rule n="4" t="Entry window">
          New positions only between <code>{c.entry_start || '09:15'}</code> and{' '}
          <code>{c.entry_cutoff || '10:30'}</code>. After the cutoff nothing new opens; anything already open
          keeps running to its stop, target or the close.
        </Rule>
        <Rule n="5" t="Stop and target">
          {sl} against you, {tp} in your favour. So a BUY stops at <code>open − {off + sl}</code> and targets{' '}
          <code>the open itself</code>; the SELL mirrors it exactly.
        </Rule>
        <Rule n="6" t="Same-candle rule">
          When one candle touches both the stop and the target, minute OHLC cannot tell us which came first — so
          the <strong>stop is always taken</strong>. On the <em>entry</em> candle only the stop is eligible at
          all: crediting a target inside the same minute you entered would flatter the result on evidence that
          does not exist.
        </Rule>
        <Rule n="7" t="End of day">
          If neither level is reached, the position closes at the last candle&apos;s close, marked{' '}
          <code>EOD</code>. Nothing is ever carried overnight.
        </Rule>
        <Rule n="8" t="Two independent sides">
          BUY and SELL are separate strategies sharing one chart. A day can produce neither, one, or both —
          capped at <strong>{c.max_per_side_per_day ?? 1} per side</strong> and{' '}
          <strong>{c.max_trades_per_day ?? 2} per day</strong>. When both fire, they are processed in the order
          they actually triggered.
        </Rule>
      </Sec>

      <Sec title="Position sizing">
        <p>
          Size steps up on <strong>cumulative realised profit</strong> — never on account value — so a run that
          gives profit back does not keep compounding into the drawdown.
        </p>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 my-2">
          {[['1 lot', 'from the start'],
            ['2 lots', `profit > ${INR(c.profit_threshold_2)}`],
            ['3 lots', `profit > ${INR(c.profit_threshold_3)}`],
            ['4 lots', `profit > ${INR(c.profit_threshold_4)}`]].map(([a, b]) => (
              <div key={a} className="bg-surface-3/40 border border-surface-3 rounded-lg px-3 py-2">
                <div className="text-brand-300 font-semibold text-xs">{a}</div>
                <div className="text-[11px] text-gray-500">{b}</div>
              </div>
          ))}
        </div>
        <p>
          <strong className="text-gray-100">The step applies to the NEXT trade.</strong> A 1-lot trade that
          carries profit from ₹2,99,000 to ₹3,02,250 was still a 1-lot trade; the trade after it uses 2 lots.
          Sizes are never rewritten after the fact, and they never step back down.
        </p>
        <p className="text-gray-400">
          Quantity = lots × {lot}. So {tp} points on 1 lot is {INR(tp * lot)}, and on 4 lots {INR(tp * lot * 4)}.
        </p>
      </Sec>

      <Sec title="What the P&L actually models">
        <p className="text-amber-300/90">
          <strong>This is an index-points backtest.</strong> P&amp;L is <code>NIFTY points × quantity</code>. It
          does <strong>not</strong> model option premium, delta, gamma, theta, implied volatility, the bid/ask
          spread, strike selection, expiry or option slippage.
        </p>
        <p>
          Read it as a measurement of <em>signal quality</em>, not as the return of any particular instrument.
          Pick an option mode with a live broker session and the run adds a second block that prices the real
          contract — premium in, premium out, with no delta approximation. That block is bounded by whatever
          option history your broker actually serves, and legs it could not price are listed rather than
          silently dropped.
        </p>
      </Sec>

      <Sec title="How to run a backtest">
        <Rule n="1" t="Give it data">
          Upload a 1-minute OHLC CSV, or leave the dropdown on <strong>Pull from Zerodha</strong> and let it
          fetch (needs a live session). A CSV needs a timestamp column plus{' '}
          <code>open, high, low, close</code>; the Zerodha export shape works as-is. Volume and OI are ignored —
          this strategy does not use them.
        </Rule>
        <Rule n="2" t="It cleans before it tests">
          Timestamps are converted to IST, sorted and de-duplicated; bars outside 09:15–15:30 are dropped, as is
          any bar failing an OHLC sanity check (high below low, non-positive prices). The counts of dropped and
          duplicate rows appear above the results.
        </Rule>
        <Rule n="3" t="Set the parameters">
          Everything on the Backtest tab is a parameter — distance, stop, target, the window, capital, lot size,
          the scaling thresholds, which sides to trade. Costs are separate and default to zero, so the base run
          is the pure theoretical figure.
        </Rule>
        <Rule n="4" t="Read the checks first">
          Before the P&amp;L, look at the integrity block. If any check fails, the numbers above it are not
          trustworthy and the failure tells you why.
        </Rule>
      </Sec>

      <Sec title="Reading the results">
        <Grid rows={[
          ['Win rate', 'With an equal stop and target, anything below ~50% loses money before costs. This one number is what the whole strategy rests on.'],
          ['NIFTY points', 'Signal quality, independent of sizing. Points ÷ trades is your true edge per trade — a fraction of a point is noise.'],
          ['Net P&L / Return', 'Points converted at your lot size, after costs. Return is measured against starting capital, not against a growing balance.'],
          ['Max drawdown', 'The worst fall from the running peak, in rupees. Weigh it against your capital: a strategy you cannot sit through is not tradeable, whatever it returns.'],
          ['Profit factor', 'Gross wins ÷ gross losses. Below 1.0 loses money. Anything around 1.05–1.10 is fragile — costs alone can erase it.'],
          ['Exits T/SL/EOD', 'How trades ended. A large EOD share means the ± distance rarely resolves inside the day, and the strategy is really a close-out bet.'],
          ['Streaks W/L', 'Longest consecutive runs. The losing streak is the one you have to survive, financially and otherwise.'],
          ['Position scaling', 'When each size started, what it traded and what it earned. If nearly all the profit comes from the largest size, the result is a story about one lucky stretch.'],
          ['Year by year', 'The walk-forward view. One strong year can carry a decade — check each regime on its own before believing the headline.'],
        ]}
        />
      </Sec>

      <Sec title="The integrity checks">
        <p>
          Every run re-verifies itself and shows the outcome, because a backtest that quietly cheats is worse
          than no backtest at all:
        </p>
        <ul className="list-disc pl-5 space-y-1 text-gray-400 text-[13px]">
          <li><strong className="text-gray-300">Equity identity</strong> — final equity equals capital plus the sum of every trade.</li>
          <li><strong className="text-gray-300">Return %</strong> — the headline percentage matches the arithmetic.</li>
          <li><strong className="text-gray-300">No overnight positions</strong> — no trade spans a date boundary.</li>
          <li><strong className="text-gray-300">Per-day caps</strong> and <strong className="text-gray-300">entry cutoff</strong> — the rules were actually obeyed, not just intended.</li>
          <li><strong className="text-gray-300">Position size never decreases</strong> — the ratchet held.</li>
          <li><strong className="text-gray-300">No look-ahead in sizing</strong> — every trade&apos;s size follows from profit realised strictly before it.</li>
          <li><strong className="text-gray-300">Chronological sequencing</strong> — trades run in entry-time order, so a same-day BUY and SELL scale correctly.</li>
          <li><strong className="text-gray-300">09:15 open used</strong> — how many sessions had the exact opening candle.</li>
        </ul>
      </Sec>

      <Sec title="Artefacts and the command line">
        <p>
          Every run writes <code>trade_log.csv</code>, daily / monthly / yearly summaries, five charts and{' '}
          <code>final_report.txt</code> — download them from the <strong>Report</strong> tab. The same engine
          runs headless for parameter sweeps:
        </p>
        <pre className="bg-surface-3/50 border border-surface-3 rounded-lg p-3 text-[11px] text-gray-300 overflow-x-auto">
{`python run_nifty_backtest.py --csv nifty_1min.csv
python run_nifty_backtest.py --csv nifty_1min.csv --offset 40 --sl 30 --tp 60
python run_nifty_backtest.py --csv nifty_1min.csv --sweep offset=30,40,50,60,70
python run_nifty_backtest.py --csv nifty_1min.csv --sweep cutoff=10:00,10:30,11:00
python run_nifty_backtest.py --csv nifty_1min.csv --costs --brokerage 20 --slippage 0.5`}
        </pre>
        <p className="text-gray-400 text-[12px]">
          A sweep writes a full result folder per value and prints a comparison table, so you can see how the
          edge behaves across the parameter rather than trusting one setting.
        </p>
      </Sec>

      <Sec title="Live and paper trading">
        <p>
          The live engine runs the identical rules on live prices: it reads the 09:15 candle, places the two
          levels, watches the index and fires when a level is touched inside the window. Sizing uses the same
          ladder, driven by realised profit in your own position table.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 my-2">
          <div className="bg-surface-3/40 border border-surface-3 rounded-lg px-3 py-2">
            <div className="text-xs font-semibold text-emerald-400 mb-1">Option buying</div>
            <div className="text-[12px] text-gray-400">
              BUY signal → <strong>buy CALL</strong><br />SELL signal → <strong>buy PUT</strong>
            </div>
          </div>
          <div className="bg-surface-3/40 border border-surface-3 rounded-lg px-3 py-2">
            <div className="text-xs font-semibold text-red-400 mb-1">Option selling</div>
            <div className="text-[12px] text-gray-400">
              BUY signal → <strong>sell PUT</strong><br />SELL signal → <strong>sell CALL</strong>
            </div>
          </div>
        </div>
        <p>
          Strike is ATM plus your chosen offset in the out-of-the-money direction, on the weekly or monthly
          expiry. The stop and target stay on the <strong>index</strong> — that is the strategy; the option is
          only how you express it.
        </p>
        <p className="text-amber-300/90">
          <strong>Paper is the default.</strong> Real orders need paper mode off <em>and</em> the
          application&apos;s global trading gate on. Index-points mode cannot place orders at all (you cannot
          trade an index) — it tracks the signal only.
        </p>
      </Sec>

      <Sec title="Honest limitations">
        <ul className="list-disc pl-5 space-y-1 text-gray-400 text-[13px]">
          <li>Fills are assumed at the level. Through a fast gap a real fill would be worse — model that with the slippage knob.</li>
          <li>Intrabar order is unknowable from OHLC. The stop-first rule is deliberately pessimistic; reality sits somewhere between it and the optimistic assumption.</li>
          <li>Costs default to zero. Switch them on before drawing any conclusion — at a profit factor near 1.05, costs decide the outcome entirely.</li>
          <li>Option legs assume you can transact at the printed premium, with no spread and no impact.</li>
          <li>A backtest measures the past under stated assumptions. It is evidence, not a forecast.</li>
        </ul>
      </Sec>
    </div>
  );
}
