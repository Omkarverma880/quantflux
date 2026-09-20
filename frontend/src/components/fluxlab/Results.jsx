import React from 'react';
import { Section, Stat, StatTable, EquityCurve, MfeMaeScatter, Note, N, N0, PCT, RS, PTS, tone } from './ui';

/**
 * What the run found — reported as counts and distributions, never as a verdict. The index-point
 * ladder and the MFE/MAE plot are the two panels that answer "is this target realistic?" with
 * data instead of hope.
 */

export default function Results({ run }) {
  if (!run) return null;
  const h = run.headline || {};
  const eq = run.equity || {};
  if (!h.trades) {
    return (
      <Section title="Result">
        <div className="text-[13px] text-gray-200">No trade was taken.</div>
        <div className="text-[12px] text-gray-500 mt-1">{run.message}</div>
        {!!Object.keys(run.skips || {}).length && (
          <div className="mt-3">
            <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Why signals were blocked</div>
            {Object.entries(run.skips).sort((a, b) => b[1] - a[1]).slice(0, 10).map(([k, v]) => (
              <div key={k} className="flex justify-between text-[12px] text-gray-400 border-b border-surface-3/40 py-1">
                <span>{k}</span><span className="mono">{v}</span>
              </div>
            ))}
          </div>
        )}
      </Section>
    );
  }
  const insufficient = run.insufficient_data;

  return (
    <div className="space-y-4">
      {insufficient && (
        <Note tone="warn">
          {h.trades} trades is a small sample. Treat every number below as a description of what
          happened, not as an estimate of what will happen.
        </Note>
      )}

      <Section title="Headline" right={<span className="text-[10.5px] text-gray-500">
        {run.range?.first} → {run.range?.last} · {h.sessions} sessions · run {run.config_hash}</span>}>
        <div className="grid gap-3 grid-cols-2 sm:grid-cols-4 lg:grid-cols-6">
          <Stat label="Net P&L" value={RS(h.net_pnl)} tone={tone(h.net_pnl)} sub={`after ${RS(h.costs)} costs`} />
          <Stat label="Trades" value={N0(h.trades)} sub={`${h.trades_per_day}/day · from ${N0(h.signals)} signals`} />
          <Stat label="Win rate" value={PCT(h.win_rate)} sub={`${h.wins}W / ${h.losses}L`} />
          <Stat label="Profit factor" value={h.profit_factor == null ? '—' : N(h.profit_factor, 2)}
            sub={`gross ${RS(h.gross_profit)} vs ${RS(h.gross_loss)}`} />
          <Stat label="Expectancy" value={RS(h.expectancy)} sub="per trade, after costs" tone={tone(h.expectancy)} />
          <Stat label="Max drawdown" value={RS(eq.max_drawdown)} tone="text-red-400"
            sub={eq.recovery_factor ? `recovery ${N(eq.recovery_factor, 2)}×` : ''} />
          <Stat label="Average trade" value={RS(h.avg_trade)} sub={`median ${RS(h.median_trade)}`} />
          <Stat label="Best / worst" value={`${RS(h.best_trade)} / ${RS(h.worst_trade)}`} />
          <Stat label="Win / loss size" value={`${RS(h.avg_win)} / ${RS(h.avg_loss)}`} />
          <Stat label="Streaks" value={`${h.max_consecutive_wins}W · ${h.max_consecutive_losses}L`} />
          <Stat label="Hold time" value={`${Math.round(h.avg_hold_min || 0)} min`} sub={`max ${Math.round(h.max_hold_min || 0)} min`} />
          <Stat label="Flat stretch" value={`${eq.longest_flat_trades ?? '—'} trades`} sub="without a new equity high" />
        </div>
        <div className="grid gap-3 grid-cols-2 sm:grid-cols-4 mt-3 pt-3 border-t border-surface-3">
          <Stat label="Index move per trade" value={PTS(h.avg_spot_move)} sub="NIFTY points — not P&L" />
          <Stat label="Index MFE" value={PTS(h.avg_spot_mfe)} sub="best the index offered" tone="text-emerald-400" />
          <Stat label="Index MAE" value={PTS(h.avg_spot_mae)} sub="worst before the exit" tone="text-red-400" />
          <Stat label="Option MFE / MAE" value={`${PCT(h.avg_option_mfe_pct)} / ${PCT(h.avg_option_mae_pct)}`}
            sub="premium, what you actually traded" />
        </div>
      </Section>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="Equity and drawdown"
          right={<span className="text-[10.5px] text-gray-500">{RS(eq.starting_capital)} → {RS(eq.ending_equity)}</span>}>
          <EquityCurve curve={eq.curve || []} />
        </Section>

        <Section title="How far the index actually travelled"
          right={<span className="text-[10.5px] text-gray-500">measured on each trade's own path</span>}>
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                {['Move', 'Trades reaching it', 'Share', 'Median bars'].map((x) => <th key={x} className="text-left px-2 py-1 font-medium">{x}</th>)}
              </tr></thead>
              <tbody>
                {(run.ladder?.levels || []).map((l) => (
                  <tr key={l.points} className="border-b border-surface-3/40">
                    <td className="px-2 py-1.5 mono text-gray-200">+{l.points} pts</td>
                    <td className="px-2 py-1.5 mono text-gray-300">{l.trades_reaching}</td>
                    <td className="px-2 py-1.5">
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 rounded-full bg-brand-500/70" style={{ width: `${Math.max(2, l.pct_of_trades || 0)}%` }} />
                        <span className="mono text-gray-400 text-[11px]">{PCT(l.pct_of_trades, 0)}</span>
                      </div>
                    </td>
                    <td className="px-2 py-1.5 mono text-gray-400">{l.median_bars_to_reach ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Note>An opportunity is not a profit: this is how often the index went that far in the
            trade's favour before it closed, whatever the exit rules then captured.</Note>
        </Section>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="MFE against MAE" right={<span className="text-[10.5px] text-gray-500">one dot per trade</span>}>
          <MfeMaeScatter scatter={run.mfe_mae?.scatter || []} />
          <div className="grid grid-cols-2 gap-4 mt-2">
            {[['Favourable (index pts)', run.mfe_mae?.spot_mfe_percentiles],
              ['Adverse (index pts)', run.mfe_mae?.spot_mae_percentiles]].map(([label, pct]) => (
                <div key={label}>
                  <div className="text-[10px] uppercase tracking-wider text-gray-500">{label}</div>
                  <div className="flex flex-wrap gap-x-3 text-[11.5px] mono text-gray-300">
                    {Object.entries(pct || {}).map(([k, v]) => (
                      <span key={k}><span className="text-gray-500">p{k}</span> {N(v, 1)}</span>
                    ))}
                  </div>
                </div>
              ))}
          </div>
        </Section>

        <Section title="What kind of trades these were">
          <div className="space-y-1.5">
            {(run.quality?.classes || []).map((c) => (
              <div key={c.kind} className="flex items-center gap-2">
                <span className="text-[12px] text-gray-300 w-44 shrink-0">{c.kind}</span>
                <div className="flex-1 h-2 rounded-full bg-surface-4 overflow-hidden min-w-0">
                  <div className="h-full bg-brand-500/70" style={{ width: `${c.pct}%` }} />
                </div>
                <span className="text-[11.5px] mono text-gray-400 w-20 text-right">{c.trades} · {PCT(c.pct, 0)}</span>
              </div>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-3 mt-3">
            <Stat label="Median time to target" value={run.quality?.time_to_target_median_min == null ? '—' : `${run.quality.time_to_target_median_min} min`} />
            <Stat label="Median time to stop" value={run.quality?.time_to_stop_median_min == null ? '—' : `${run.quality.time_to_stop_median_min} min`} />
          </div>
          <div className="mt-3">
            <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Exits</div>
            <StatTable rows={run.by_exit || []} keyLabel="Reason"
              columns={[['trades', 'Trades'], ['win_rate', 'Win %', PCT], ['net_pnl', 'Net P&L', RS],
                        ['avg_hold_min', 'Hold', (v) => (v == null ? '—' : `${Math.round(v)}m`)]]} />
          </div>
        </Section>
      </div>

      <Section title="How often the setup appeared">
        <div className="grid gap-3 grid-cols-2 sm:grid-cols-4 lg:grid-cols-6">
          <Stat label="Sessions with a signal" value={N0(run.opportunity?.sessions_with_signals)} />
          <Stat label="Sessions traded" value={N0(run.opportunity?.sessions_traded)} />
          <Stat label="2+ signals in a day" value={N0(run.opportunity?.days_with_2plus_signals)} />
          <Stat label="Signals per day" value={N(run.opportunity?.avg_signals_per_day, 2)} />
          {[25, 30, 40].map((p) => (
            <Stat key={p} label={`Days with ${p}+ pts`}
              value={N0(run.opportunity?.[`days_with_${p}pt_opportunity`])}
              sub={PCT(run.opportunity?.[`pct_days_with_${p}pt`], 0)} />
          ))}
        </div>
        {!!Object.keys(run.skips || {}).length && (
          <div className="mt-3">
            <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Signals that did not become trades</div>
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(run.skips).sort((a, b) => b[1] - a[1]).slice(0, 12).map(([k, v]) => (
                <span key={k} className="px-1.5 py-0.5 rounded border border-surface-3 bg-surface-2/60 text-[11px] text-gray-400">
                  {k} <span className="mono text-gray-300">{v}</span>
                </span>
              ))}
            </div>
          </div>
        )}
      </Section>
    </div>
  );
}
