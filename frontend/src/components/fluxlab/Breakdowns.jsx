import React from 'react';
import { Section, StatTable, Stat, Note, N, N0, PCT, RS, tone } from './ui';

/** The same trades, cut by time, weekday, month, year and market regime. */

export default function Breakdowns({ run }) {
  if (!run?.headline?.trades) return <Note>Run a backtest first — there is nothing to break down yet.</Note>;
  const months = run.by_month || [];
  return (
    <div className="space-y-4">
      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Time of day" right={<span className="text-[10.5px] text-gray-500">exchange session buckets</span>}>
          <StatTable rows={run.by_time || []} keyLabel="Window" />
        </Section>
        <Section title="Day of the week">
          <StatTable rows={run.by_dow || []} keyLabel="Day" />
        </Section>
      </div>

      <Section title="Month by month" right={
        <span className="text-[10.5px] text-gray-500">
          {run.months?.profitable} profitable · {run.months?.losing} losing
        </span>}>
        <StatTable rows={months} keyLabel="Month"
          columns={[['trades', 'Trades'], ['wins', 'W'], ['losses', 'L'], ['win_rate', 'Win %', PCT],
                    ['gross_profit', 'Gross +', RS], ['gross_loss', 'Gross −', RS], ['costs', 'Costs', RS],
                    ['net_pnl', 'Net P&L', RS], ['avg_trade', 'Avg', RS]]} />
        {months.length > 0 && (
          <div className="flex items-end gap-1 h-24 mt-3">
            {months.map((m) => {
              const max = Math.max(...months.map((x) => Math.abs(x.net_pnl || 0)), 1);
              const h = (Math.abs(m.net_pnl || 0) / max) * 44;
              const up = (m.net_pnl || 0) >= 0;
              return (
                <div key={m.key} className="flex-1 h-full flex flex-col items-center justify-center"
                  title={`${m.key}: ${RS(m.net_pnl)} from ${m.trades} trades`}>
                  <div className="flex-1 flex items-end w-full justify-center">
                    {up && <div className="w-full bg-emerald-500/70 rounded-t" style={{ height: `${h}px` }} />}
                  </div>
                  <div className="w-full h-px bg-surface-4" />
                  <div className="flex-1 flex items-start w-full justify-center">
                    {!up && <div className="w-full bg-red-500/70 rounded-b" style={{ height: `${h}px` }} />}
                  </div>
                  <div className="text-[8px] text-gray-500 mt-0.5">{String(m.key).slice(5)}</div>
                </div>
              );
            })}
          </div>
        )}
      </Section>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Year by year" right={<span className="text-[10.5px] text-gray-500">only the periods actually stored</span>}>
          <StatTable rows={run.by_year || []} keyLabel="Year" />
        </Section>
        <Section title="CE against PE">
          <StatTable rows={run.by_side || []} keyLabel="Side" />
          {!!(run.by_expiry_dte || []).length && (
            <div className="mt-3">
              <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">By days to expiry</div>
              <StatTable rows={run.by_expiry_dte} keyLabel="DTE"
                columns={[['trades', 'Trades'], ['win_rate', 'Win %', PCT], ['net_pnl', 'Net P&L', RS]]} />
            </div>
          )}
        </Section>
      </div>

      <Section title="Market regime" right={<span className="text-[10.5px] text-gray-500">derived from the data, not hand-labelled</span>}>
        <div className="grid gap-4 lg:grid-cols-2">
          {Object.entries(run.regimes || {}).map(([dim, rows]) => (
            <div key={dim}>
              <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">{dim.replace('_', ' ')}</div>
              <StatTable rows={rows} keyLabel="Condition"
                columns={[['trades', 'Trades'], ['win_rate', 'Win %', PCT], ['net_pnl', 'Net P&L', RS],
                          ['avg_spot_mfe', 'Avg MFE', (v) => (v == null ? '—' : `${N(v, 1)} pts`)]]} />
            </div>
          ))}
        </div>
      </Section>

      <Section title="Day by day" right={<span className="text-[10.5px] text-gray-500">{(run.daily || []).length} sessions with a signal</span>}>
        <div className="overflow-x-auto max-h-[420px] overflow-y-auto">
          <table className="w-full text-[12px] min-w-[720px]">
            <thead className="sticky top-0 bg-surface-1">
              <tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                {['Date', 'Signals', 'Trades', 'W', 'L', 'Gross', 'Costs', 'Net', 'Max DD', 'Targets', 'Stops', 'Best index MFE']
                  .map((h) => <th key={h} className="text-right px-2 py-1 font-medium first:text-left">{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {(run.daily || []).map((d) => (
                <tr key={d.date} className="border-b border-surface-3/40">
                  <td className="px-2 py-1 mono text-gray-300">{d.date}</td>
                  <td className="px-2 py-1 text-right mono text-gray-400">{d.signals}</td>
                  <td className="px-2 py-1 text-right mono text-gray-200">{d.trades}</td>
                  <td className="px-2 py-1 text-right mono text-emerald-400/80">{d.wins}</td>
                  <td className="px-2 py-1 text-right mono text-red-400/80">{d.losses}</td>
                  <td className="px-2 py-1 text-right mono text-gray-400">{RS(d.gross)}</td>
                  <td className="px-2 py-1 text-right mono text-gray-500">{RS(d.costs)}</td>
                  <td className={`px-2 py-1 text-right mono font-semibold ${tone(d.net_pnl)}`}>{RS(d.net_pnl)}</td>
                  <td className="px-2 py-1 text-right mono text-red-400/70">{RS(d.max_dd)}</td>
                  <td className="px-2 py-1 text-right mono text-gray-400">{d.target_hits}</td>
                  <td className="px-2 py-1 text-right mono text-gray-400">{d.stop_hits}</td>
                  <td className="px-2 py-1 text-right mono text-gray-400">{d.best_spot_mfe == null ? '—' : `${N(d.best_spot_mfe, 1)}`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}
