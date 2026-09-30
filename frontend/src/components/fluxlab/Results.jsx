import React, { useState } from 'react';
import { ChevronDown, ChevronRight, Download } from 'lucide-react';
import { Checks, EquityCurve, MonthTiles, N, N0, PCT, PTS, RS, Reason, Section, SideTag, Stat, Note, tone } from './ui';

/** What the run produced: the headline, the months, and every trade with the candle behind it. */
export default function Results({ run, trades = [], onExport }) {
  const [open, setOpen] = useState(null);
  const s = run?.summary || {};
  if (!s.trades) {
    return <Note>{s.message || 'No trades in this run.'}{s.signals ? ` ${s.signals} signals fired but none could be traded.` : ''}</Note>;
  }
  return (
    <div className="space-y-4">
      <Section title={`Run #${run.id} · ${run.start} → ${run.end} · ${run.timeframe}-minute candles`} right={
        <div className="flex items-center gap-1">
          {['trades', 'monthly'].map((w) => (
            <button key={w} onClick={() => onExport?.(w)} className="btn-secondary !py-1 !px-2 text-[11px] flex items-center gap-1">
              <Download className="w-3 h-3" />{w}
            </button>
          ))}
        </div>}>
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3 mb-3">
          <Stat label="Net P&L" value={RS(s.net)} tone={tone(s.net)} sub={`after ${RS(-s.charges)} charges`} />
          <Stat label="Trades" value={N0(s.trades)} sub={`${s.signals} signals · ${s.sessions} sessions`} />
          <Stat label="Win rate" value={PCT(s.win_rate, 0)} />
          <Stat label="Avg win / loss" value={`${RS(s.avg_win)} / ${RS(s.avg_loss)}`} />
          <Stat label="Profit factor" value={N(s.profit_factor)} sub="₹ won per ₹ lost" />
          <Stat label="Green months" value={s.months ? `${s.green_months}/${s.months}` : '—'} sub={`avg ${RS(s.avg_month)}`} />
          <Stat label="Max drawdown" value={RS(s.max_drawdown)} tone="text-red-400" />
          <Stat label="Avg hold" value={`${s.avg_hold_min} min`} sub={`index ${PTS(s.avg_index_move)}`} />
        </div>
        <div className="text-[11.5px] text-gray-400 mb-3">
          Exits: {Object.entries(s.exits || {}).map(([k, v]) => `${k} ${v}`).join(' · ')}
          {s.skipped ? ` · ${s.skipped} signals skipped` : ''}
          {Object.keys(s.skip_reasons || {}).length
            ? ` (${Object.entries(s.skip_reasons).map(([k, v]) => `${v}× ${k}`).join(', ')})` : ''}
        </div>
        <EquityCurve trades={trades} />
        <div className="mt-3"><MonthTiles months={s.monthly || []} /></div>
        <div className="flex flex-wrap gap-4 mt-3">
          {(s.by_pattern || []).map((b) => (
            <div key={b.pattern} className="text-[12px] text-gray-400">
              {b.pattern}: <span className={`mono font-semibold ${tone(b.net)}`}>{RS(b.net)}</span>
              {' '}({b.trades} trades, {PCT(b.win_rate, 0)} won)
            </div>
          ))}
        </div>
      </Section>

      <Section title={`Trades · ${trades.length}`}>
        <div className="overflow-x-auto max-h-[560px] overflow-y-auto">
          <table className="w-full text-[12px] min-w-[900px]">
            <thead className="sticky top-0 bg-surface-1">
              <tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                {['', 'Date', 'Pattern', 'Signal', 'Bought', 'Entry', 'Exit', 'At', 'Why', 'Index', 'Net P&L'].map((h) => (
                  <th key={h} className={`px-2 py-1 font-medium ${['Date', 'Pattern', 'Signal', 'Bought', 'Why', ''].includes(h) ? 'text-left' : 'text-right'}`}>{h}</th>))}
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => {
                const isOpen = open === t.id;
                return (
                  <React.Fragment key={t.id}>
                    <tr onClick={() => setOpen(isOpen ? null : t.id)} className="border-b border-surface-3/40 cursor-pointer hover:bg-surface-2/60">
                      <td className="px-2 py-1 text-gray-500">{isOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}</td>
                      <td className="px-2 py-1 mono text-gray-300">{t.date}</td>
                      <td className="px-2 py-1"><SideTag side={t.side} /></td>
                      <td className="px-2 py-1 mono text-gray-400">{t.signal_time}</td>
                      <td className="px-2 py-1 mono text-gray-300">{t.type} {N0(t.strike)}</td>
                      <td className="px-2 py-1 mono text-right text-gray-300">{N(t.option_entry)}</td>
                      <td className="px-2 py-1 mono text-right text-gray-300">{N(t.option_exit)}</td>
                      <td className="px-2 py-1 mono text-right text-gray-500">{t.exit_time}</td>
                      <td className="px-2 py-1"><Reason r={t.exit_reason} /></td>
                      <td className={`px-2 py-1 mono text-right ${tone(t.spot_move_pts)}`}>{N(t.spot_move_pts, 1)}</td>
                      <td className={`px-2 py-1 mono text-right font-semibold ${tone(t.pnl)}`}>{RS(t.pnl)}</td>
                    </tr>
                    {isOpen && (
                      <tr className="bg-surface-2/30"><td colSpan={11} className="px-3 py-2">
                        <div className="text-[11px] text-gray-500 mb-2">
                          The candle: O {N(t.candle?.open)} H {N(t.candle?.high)} L {N(t.candle?.low)} C {N(t.candle?.close)} ·
                          {' '}NIFTY {N(t.spot_entry)} at the signal → {N(t.spot_exit)} at the exit ·
                          {' '}expiry {t.expiry} ({t.dte}d) · {t.qty} qty · charges {RS(-(t.charges || 0))} ·
                          {' '}rejection {t.rejection_ok ? 'passed' : 'failed'}, shape {t.shape_ok ? 'passed' : 'failed'}
                        </div>
                        <Checks checks={t.checks} />
                      </td></tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="text-[11px] text-gray-500 mt-2">
          Click a row to see every condition with the value it had on that candle. Index points and option
          rupees are separate columns — they measure different things.
        </div>
      </Section>
    </div>
  );
}
