import React, { useState } from 'react';
import { Loader2, FlaskConical } from 'lucide-react';
import { Section, StatTable, Stat, Note, N, N0, PCT, RS, tone } from './ui';

/**
 * The validation tools. None of them nominates a winner — they show whether a result survives
 * being pushed: different parameters, unseen periods, harsher assumptions, a different trade order.
 */

const MODES = [
  ['splits', 'Train / validation / out-of-sample', 'Chronological thirds. Read the out-of-sample column first — it is the only one that was never used to choose anything.'],
  ['walkforward', 'Walk-forward', 'Roll a train window and a test window through the history. Consistency across windows beats one big total.'],
  ['robustness', 'Robustness', 'The same strategy under double slippage, later fills, wider stops, higher costs. Fragile strategies break here.'],
  ['sweep', 'Parameter sweep', 'A small declared grid. A result that only appears at one exact value is a spike, not an edge.'],
  ['montecarlo', 'Trade shuffle', 'Reorder the trades that actually happened to see how much of the equity curve was sequence luck.'],
];

const SWEEPABLE = [
  ['execution.target_pct', 'Target %', [20, 25, 30, 35, 40]],
  ['execution.stop_pct', 'Stop %', [10, 15, 20, 25]],
  ['execution.max_hold_min', 'Max hold (min)', [15, 30, 45, 60]],
  ['risk.cooldown_min', 'Cooldown (min)', [0, 10, 20, 30]],
  ['risk.max_trades_per_day', 'Max trades/day', [1, 2, 3, 5]],
  ['selection.moneyness', 'Strike offset', [-1, 0, 1, 2]],
  ['timeframe', 'Timeframe', [3, 5, 15]],
];

export default function ResearchPanel({ cfg, runId, onRun, busy, progress, result, mode, setMode }) {
  const [grid, setGrid] = useState({});
  const active = MODES.find((m) => m[0] === mode) || MODES[0];

  const toggle = (path, values) => {
    setGrid((g) => {
      const next = { ...g };
      if (next[path]) delete next[path]; else next[path] = values;
      return next;
    });
  };

  const combos = Object.values(grid).reduce((a, v) => a * v.length, 1) * (Object.keys(grid).length ? 1 : 0);

  return (
    <div className="space-y-4">
      <Section title="Validation" right={<span className="text-[10.5px] text-gray-500">runs on the configuration in Setup</span>}>
        <div className="flex flex-wrap gap-1.5">
          {MODES.map(([k, label]) => (
            <button key={k} onClick={() => setMode(k)}
              className={`px-2.5 py-1 rounded-full border text-[11.5px] font-medium ${mode === k
                ? 'border-brand-500 bg-brand-500 text-white' : 'border-surface-3 text-gray-400 hover:text-gray-200'}`}>
              {label}
            </button>
          ))}
        </div>
        <Note>{active[2]}</Note>

        {mode === 'sweep' && (
          <div className="mt-3 space-y-2">
            <div className="text-[10px] uppercase tracking-wider text-gray-500">Parameters to vary</div>
            <div className="flex flex-wrap gap-1.5">
              {SWEEPABLE.map(([path, label, values]) => (
                <button key={path} onClick={() => toggle(path, values)}
                  className={`px-2 py-1 rounded border text-[11.5px] ${grid[path]
                    ? 'border-brand-500 bg-brand-500/10 text-brand-300' : 'border-surface-3 text-gray-400'}`}>
                  {label} <span className="text-[10px] text-gray-500">{values.join(', ')}</span>
                </button>
              ))}
            </div>
            <div className="text-[11.5px] text-gray-500">
              {combos ? `${combos} combinations` : 'pick at least one parameter'}
              {combos > 60 && <span className="text-amber-500"> — over the 60-combination ceiling; drop one</span>}
            </div>
          </div>
        )}

        {mode === 'montecarlo' && !runId && (
          <Note tone="warn">Run and store a backtest first — the shuffle needs its trades.</Note>
        )}

        <button onClick={() => onRun(mode, { grid, run_id: runId })} disabled={busy || (mode === 'sweep' && (!combos || combos > 60))}
          className="btn-primary !py-1.5 !px-4 text-[12.5px] mt-3 flex items-center gap-2 disabled:opacity-50">
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <FlaskConical className="w-4 h-4" />}
          {busy ? (progress || 'working…') : `Run ${active[1].toLowerCase()}`}
        </button>
      </Section>

      {result?.kind === 'splits' && (
        <Section title="Train / validation / out-of-sample">
          {result.insufficient_data ? <Note tone="warn">{result.message}</Note> : (
            <>
              <div className="grid gap-3 sm:grid-cols-3">
                {['train', 'validation', 'test'].map((k) => {
                  const p = result.periods?.[k] || {};
                  return (
                    <div key={k} className={`rounded-lg border p-3 ${k === 'test' ? 'border-brand-500/40 bg-brand-500/5' : 'border-surface-3'}`}>
                      <div className="text-[11px] uppercase tracking-wider text-gray-500">
                        {k === 'test' ? 'out-of-sample' : k}
                      </div>
                      <div className="text-[10.5px] text-gray-500 mb-1.5">{p.window?.start} → {p.window?.end}</div>
                      <div className={`text-[16px] font-bold mono ${tone(p.net_pnl)}`}>{RS(p.net_pnl)}</div>
                      <div className="text-[11.5px] text-gray-400">
                        {N0(p.trades)} trades · {PCT(p.win_rate)} · PF {p.profit_factor == null ? '—' : N(p.profit_factor, 2)}
                      </div>
                      <div className="text-[11px] text-gray-500">max DD {RS(p.max_drawdown)}</div>
                    </div>
                  );
                })}
              </div>
              <Note>{result.note}</Note>
            </>
          )}
        </Section>
      )}

      {result?.kind === 'walk_forward' && (
        <Section title="Walk-forward" right={<span className="text-[10.5px] text-gray-500">
          {result.train_days}-day train → {result.test_days}-day test</span>}>
          {result.insufficient_data ? <Note tone="warn">{result.message}</Note> : (
            <>
              <div className="grid gap-3 sm:grid-cols-4 mb-3">
                <Stat label="Windows" value={N0(result.summary?.windows)} />
                <Stat label="With trades" value={N0(result.summary?.windows_with_trades)} />
                <Stat label="Profitable windows" value={N0(result.summary?.profitable_windows)} />
                <Stat label="Total across windows" value={RS(result.summary?.total_net_pnl)}
                  tone={tone(result.summary?.total_net_pnl)} />
              </div>
              <StatTable rows={(result.windows || []).map((w) => ({ ...w, key: `${w.window}. ${w.test}` }))}
                keyLabel="Test window" />
              <Note>{result.summary?.reading}</Note>
            </>
          )}
        </Section>
      )}

      {result?.kind === 'robustness' && (
        <Section title="Robustness" right={<span className="text-[10.5px] text-gray-500">
          {result.summary?.still_profitable} of {result.summary?.variants} variants still positive</span>}>
          <StatTable rows={(result.rows || []).map((r) => ({ ...r, key: r.variant }))} keyLabel="Variant"
            columns={[['trades', 'Trades'], ['win_rate', 'Win %', PCT], ['net_pnl', 'Net P&L', RS],
                      ['vs_baseline_pct', 'vs baseline', (v) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v}%`)],
                      ['max_drawdown', 'Max DD', RS], ['profit_factor', 'PF', (v) => N(v, 2)]]} />
          <Note>{result.summary?.reading}</Note>
        </Section>
      )}

      {result?.kind === 'sweep' && (
        <Section title="Parameter sweep" right={<span className="text-[10.5px] text-gray-500">
          {result.combinations} combinations</span>}>
          <div className="overflow-x-auto max-h-[460px] overflow-y-auto">
            <table className="w-full text-[12px] min-w-[720px]">
              <thead className="sticky top-0 bg-surface-1">
                <tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                  {(result.keys || []).map((k) => <th key={k} className="text-left px-2 py-1 font-medium">{k.split('.').pop()}</th>)}
                  {['Trades', 'Win %', 'Net P&L', 'PF', 'Max DD', 'Expectancy'].map((h) => (
                    <th key={h} className="text-right px-2 py-1 font-medium">{h}</th>))}
                </tr>
              </thead>
              <tbody>
                {(result.rows || []).map((r, i) => (
                  <tr key={i} className="border-b border-surface-3/40">
                    {(result.keys || []).map((k) => (
                      <td key={k} className="px-2 py-1 mono text-gray-300">{String(r.params?.[k])}</td>))}
                    {r.error ? <td colSpan={6} className="px-2 py-1 text-red-400">{r.error}</td> : (
                      <>
                        <td className="px-2 py-1 text-right mono text-gray-300">{r.trades}</td>
                        <td className="px-2 py-1 text-right mono text-gray-300">{PCT(r.win_rate)}</td>
                        <td className={`px-2 py-1 text-right mono font-semibold ${tone(r.net_pnl)}`}>{RS(r.net_pnl)}</td>
                        <td className="px-2 py-1 text-right mono text-gray-300">{N(r.profit_factor, 2)}</td>
                        <td className="px-2 py-1 text-right mono text-red-400/70">{RS(r.max_drawdown)}</td>
                        <td className="px-2 py-1 text-right mono text-gray-300">{RS(r.expectancy)}</td>
                      </>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {result.stability && (
            <div className="mt-2 grid gap-3 sm:grid-cols-4">
              <Stat label="Best" value={RS(result.stability.best_net_pnl)} />
              <Stat label="Median" value={RS(result.stability.median_net_pnl)} />
              <Stat label="Profitable combos" value={`${result.stability.profitable_combinations} / ${result.stability.total}`} />
              <Stat label="Best ÷ median" value={result.stability.best_to_median == null ? '—' : `${result.stability.best_to_median}×`} />
            </div>
          )}
          <Note tone="warn">{result.stability?.reading || result.note}</Note>
        </Section>
      )}

      {result?.kind === 'monte_carlo' && (
        <Section title="Trade shuffle" right={<span className="text-[10.5px] text-gray-500">
          {result.runs} shuffles · seed {result.seed}</span>}>
          {result.insufficient_data ? <Note tone="warn">Only {result.trades} trades — too few to shuffle meaningfully.</Note> : (
            <>
              <div className="grid gap-3 sm:grid-cols-4">
                <Stat label="This run actually made" value={RS(result.actual_net_pnl)} tone={tone(result.actual_net_pnl)} />
                <Stat label="Resampled runs below zero" value={PCT(result.worse_than_zero_pct, 0)}
                  tone={result.worse_than_zero_pct > 30 ? 'text-red-400' : 'text-gray-100'} />
                <Stat label="Median resampled P&L" value={RS(result.final_pnl_percentiles?.['50'])} />
                <Stat label="Median drawdown, reordered" value={RS(result.shuffle_drawdown_percentiles?.['50'])} tone="text-red-400" />
              </div>
              <div className="grid gap-4 sm:grid-cols-3 mt-3">
                {[['P&L if the trades were resampled', result.final_pnl_percentiles],
                  ['Drawdown, same trades reordered', result.shuffle_drawdown_percentiles],
                  ['Drawdown when resampled', result.max_drawdown_percentiles]].map(([label, pct]) => (
                    <div key={label}>
                      <div className="text-[10px] uppercase tracking-wider text-gray-500">{label}</div>
                      <div className="flex flex-wrap gap-x-3 text-[11.5px] mono text-gray-300">
                        {Object.entries(pct || {}).map(([k, v]) => (
                          <span key={k}><span className="text-gray-500">p{k}</span> {RS(v)}</span>))}
                      </div>
                    </div>
                  ))}
              </div>
              <Note>{result.note}</Note>
            </>
          )}
        </Section>
      )}
    </div>
  );
}
