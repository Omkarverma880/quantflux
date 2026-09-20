import React, { useState } from 'react';
import { X, Check, Minus, Download } from 'lucide-react';
import { Section, Stat, Note, N, PCT, RS, PTS, tone } from './ui';

/**
 * Every trade, and — when you click one — exactly why it happened: the conditions that were
 * true on the decision bar with the values they had, the contract that was chosen, the fill,
 * the path and the exit. No aggregate in this lab is unexplainable down to this card.
 */

function TradeCard({ t, onClose }) {
  if (!t) return null;
  const groups = {};
  (t.reasons || []).forEach((r) => { (groups[r.group] = groups[r.group] || []).push(r); });
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="w-full max-w-2xl card !p-4 space-y-3 max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between">
          <div>
            <div className="text-[15px] font-bold text-white">
              Trade #{t.trade_no ?? t.trade_id} · {t.side} {t.contract}
            </div>
            <div className="text-[11.5px] text-gray-500">
              {t.date} · signal {t.time} · filled {t.entry_time} · exited {t.exit_time} ·
              {' '}expiry {t.expiry} ({t.dte}d)
            </div>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-200"><X className="w-4 h-4" /></button>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Stat label="Option entry" value={N(t.option_entry)} sub={`${t.qty} qty · ${t.lots} lot(s)`} />
          <Stat label="Option exit" value={N(t.option_exit)} sub={t.exit_reason} />
          <Stat label="Net P&L" value={RS(t.pnl)} tone={tone(t.pnl)} sub={`after ${RS(t.charges)} costs`} />
          <Stat label="Held" value={`${t.held_min} min`} />
          <Stat label="NIFTY at entry" value={N(t.spot_entry)} />
          <Stat label="NIFTY at exit" value={N(t.spot_exit)} sub={PTS(t.spot_move_pts)} />
          <Stat label="Index MFE / MAE" value={`${PTS(t.spot_mfe_pts, 0)} / ${PTS(t.spot_mae_pts, 0)}`}
            sub="how far it went, either way" />
          <Stat label="Option MFE / MAE" value={`${PCT(t.option_mfe_pct)} / ${PCT(t.option_mae_pct)}`} />
        </div>

        <div>
          <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Why this fired</div>
          <div className="space-y-2">
            {Object.entries(groups).map(([group, rows]) => (
              <div key={group}>
                <div className="text-[10.5px] text-gray-500 capitalize">{group}</div>
                {rows.map((r) => (
                  <div key={r.key} className="flex items-start gap-2 py-0.5">
                    {r.passed ? <Check className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-px" />
                      : <Minus className="w-3.5 h-3.5 text-gray-600 shrink-0 mt-px" />}
                    <span className="text-[12px] text-gray-300">{r.label}</span>
                    {r.detail && <span className="text-[11px] text-gray-500 mono ml-auto">{r.detail}</span>}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>

        {!!Object.keys(t.indicators || {}).length && (
          <div>
            <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">The bar it decided on</div>
            <div className="grid grid-cols-3 sm:grid-cols-5 gap-x-4 gap-y-1">
              {Object.entries(t.indicators).filter(([, v]) => v != null).map(([k, v]) => (
                <div key={k} className="text-[11px]">
                  <span className="text-gray-500">{k}</span> <span className="mono text-gray-300">{N(v)}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {!!Object.keys(t.regime || {}).length && (
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(t.regime).filter(([, v]) => v).map(([k, v]) => (
              <span key={k} className="px-1.5 py-0.5 rounded border border-surface-3 bg-surface-2/60 text-[11px] text-gray-400">
                {k}: <span className="text-gray-200">{v}</span>
              </span>
            ))}
          </div>
        )}

        {!!Object.keys(t.ladder || {}).length && (
          <div className="text-[11.5px] text-gray-400">
            Index milestones reached (bars after entry):{' '}
            {Object.entries(t.ladder).map(([p, b]) => `+${p}pt in ${b}`).join(' · ')}
          </div>
        )}
      </div>
    </div>
  );
}

export default function Ledger({ trades = [], runId, onExport }) {
  const [open, setOpen] = useState(null);
  const [filter, setFilter] = useState('all');
  const shown = trades.filter((t) => (filter === 'all' ? true
    : filter === 'wins' ? (t.pnl || 0) > 0
      : filter === 'losses' ? (t.pnl || 0) < 0
        : t.exit_reason === filter));
  if (!trades.length) return <Note>No trades in this run.</Note>;
  return (
    <Section title={`Trade ledger · ${trades.length}`} right={
      <div className="flex items-center gap-2">
        <select value={filter} onChange={(e) => setFilter(e.target.value)}
          className="bg-surface-2 border border-surface-3 rounded px-2 py-1 text-[11.5px] text-gray-300">
          <option value="all">all trades</option>
          <option value="wins">winners</option>
          <option value="losses">losers</option>
          <option value="TARGET">target</option>
          <option value="SL">stopped</option>
          <option value="TIME">time exit</option>
          <option value="EOD">end of day</option>
          <option value="TRAIL">trailed</option>
        </select>
        {runId && (
          <button onClick={() => onExport?.('trades')} className="btn-secondary !py-1 !px-2 text-[11.5px] flex items-center gap-1">
            <Download className="w-3 h-3" />CSV
          </button>
        )}
      </div>}>
      <div className="overflow-x-auto max-h-[560px] overflow-y-auto">
        <table className="w-full text-[12px] min-w-[980px]">
          <thead className="sticky top-0 bg-surface-1">
            <tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
              {['#', 'Date', 'Signal', 'Side', 'Strike', 'Entry', 'Exit', 'Exit at', 'Reason',
                'Index move', 'Index MFE', 'Index MAE', 'Costs', 'Net P&L'].map((h) => (
                  <th key={h} className="px-2 py-1 font-medium text-right first:text-left">{h}</th>))}
            </tr>
          </thead>
          <tbody>
            {shown.map((t) => (
              <tr key={t.trade_no ?? t.trade_id ?? t.id} onClick={() => setOpen(t)}
                className="border-b border-surface-3/40 cursor-pointer hover:bg-surface-2/60">
                <td className="px-2 py-1 text-gray-500 mono">{t.trade_no ?? t.trade_id}</td>
                <td className="px-2 py-1 mono text-gray-300 text-right">{t.date}</td>
                <td className="px-2 py-1 mono text-gray-400 text-right">{t.time}</td>
                <td className="px-2 py-1 text-right">
                  <span className={`px-1 rounded text-[10.5px] font-bold ${t.side === 'CE'
                    ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'}`}>{t.side}</span>
                </td>
                <td className="px-2 py-1 mono text-gray-300 text-right">{N(t.strike, 0)}</td>
                <td className="px-2 py-1 mono text-gray-300 text-right">{N(t.option_entry)}</td>
                <td className="px-2 py-1 mono text-gray-300 text-right">{N(t.option_exit)}</td>
                <td className="px-2 py-1 mono text-gray-500 text-right">{t.exit_time}</td>
                <td className="px-2 py-1 text-right text-[11px] text-gray-400">{t.exit_reason}</td>
                <td className={`px-2 py-1 mono text-right ${tone(t.spot_move_pts)}`}>{N(t.spot_move_pts, 1)}</td>
                <td className="px-2 py-1 mono text-right text-emerald-400/70">{N(t.spot_mfe_pts, 1)}</td>
                <td className="px-2 py-1 mono text-right text-red-400/70">{N(t.spot_mae_pts, 1)}</td>
                <td className="px-2 py-1 mono text-right text-gray-500">{RS(t.charges)}</td>
                <td className={`px-2 py-1 mono text-right font-semibold ${tone(t.pnl)}`}>{RS(t.pnl)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="text-[11px] text-gray-500 mt-2">
        Click any row for the conditions and indicator values behind it. Index points and option
        rupees are separate columns — they are different measurements of the same trade.
      </div>
      <TradeCard t={open} onClose={() => setOpen(null)} />
    </Section>
  );
}
