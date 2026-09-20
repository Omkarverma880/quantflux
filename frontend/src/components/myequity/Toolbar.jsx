import React from 'react';
import { Search, X, LayoutList, Rows3 } from 'lucide-react';

/**
 * Finding one stock among a hundred: a search box, one-click filters for the states you
 * actually look for, a sector picker, sorting, and grouping for when the list gets long.
 */

export const FILTERS = [
  ['all', 'All'],
  ['at_level', 'At a level now'],
  ['triggered', 'Triggered'],
  ['waiting', 'Waiting'],
  ['profit', 'In profit'],
  ['loss', 'In loss'],
  ['oversold', 'RSI ≤ 30'],
  ['overbought', 'RSI ≥ 80'],
  ['investment', 'Investment'],
  ['swing', 'Swing'],
];

export const SORTS = [
  ['added_on', 'Research date'],
  ['pnl', 'P&L since trigger'],
  ['distance', 'Closest to a level'],
  ['rsi', 'RSI'],
  ['change_pct', "Today's move"],
  ['from_52w_high', 'Below 52w high'],
  ['symbol', 'Name'],
];

export const GROUPS = [['none', 'No grouping'], ['status', 'Status'], ['sector', 'Sector'], ['category', 'Type']];

const sel = 'bg-surface-2 border border-surface-3 rounded-lg px-2 py-1.5 text-[12px] text-gray-200 outline-none focus:border-brand-500/60';

export function Summary({ summary }) {
  if (!summary) return null;
  const item = (label, value, tone = 'text-gray-100') => (
    <div key={label} className="px-3 py-1.5">
      <div className="text-[9.5px] uppercase tracking-wider text-gray-500">{label}</div>
      <div className={`text-[14px] font-semibold mono ${tone}`}>{value}</div>
    </div>
  );
  const pnl = summary.avg_pnl_pct;
  return (
    <div className="card !p-0 flex flex-wrap divide-x divide-surface-3 overflow-hidden">
      {item('Stocks', summary.stocks)}
      {item('Triggered', summary.triggered, 'text-brand-300')}
      {item('Waiting', summary.waiting)}
      {item('At a level now', summary.at_level_now, summary.at_level_now ? 'text-amber-500' : 'text-gray-100')}
      {item('In profit', summary.in_profit, 'text-emerald-400')}
      {item('In loss', summary.in_loss, 'text-red-400')}
      {item('Average P&L', pnl == null ? '—' : `${pnl > 0 ? '+' : ''}${pnl}%`,
        pnl > 0 ? 'text-emerald-400' : pnl < 0 ? 'text-red-400' : 'text-gray-100')}
      {summary.best && item('Best', `${summary.best[0]} ${summary.best[1] > 0 ? '+' : ''}${summary.best[1]}%`, 'text-emerald-400')}
      {summary.worst && item('Worst', `${summary.worst[0]} ${summary.worst[1]}%`, 'text-red-400')}
    </div>
  );
}

export default function Toolbar({ view, setView, sectors = [], counts = {}, shown, total }) {
  const set = (patch) => setView({ ...view, ...patch });
  return (
    <div className="card !p-3 space-y-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[180px]">
          <Search className="w-3.5 h-3.5 text-gray-500 absolute left-2.5 top-1/2 -translate-y-1/2" />
          <input value={view.q} onChange={(e) => set({ q: e.target.value })}
            placeholder="Search symbol, company, sector or note"
            className="input-field !py-1.5 !pl-8 w-full text-[12.5px]" />
          {view.q && (
            <button onClick={() => set({ q: '' })} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300">
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
        <select value={view.sector} onChange={(e) => set({ sector: e.target.value })} className={sel}>
          <option value="">All sectors</option>
          {sectors.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select value={view.sort} onChange={(e) => set({ sort: e.target.value })} className={sel}>
          {SORTS.map(([k, l]) => <option key={k} value={k}>Sort: {l}</option>)}
        </select>
        <button onClick={() => set({ dir: view.dir === 'desc' ? 'asc' : 'desc' })} className={`${sel} w-16`}>
          {view.dir === 'desc' ? '↓ desc' : '↑ asc'}
        </button>
        <select value={view.group} onChange={(e) => set({ group: e.target.value })} className={sel}>
          {GROUPS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <button onClick={() => set({ dense: !view.dense })} title={view.dense ? 'comfortable rows' : 'compact rows'}
          className={`${sel} flex items-center gap-1`}>
          {view.dense ? <Rows3 className="w-3.5 h-3.5" /> : <LayoutList className="w-3.5 h-3.5" />}
          {view.dense ? 'Compact' : 'Roomy'}
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-1">
        {FILTERS.map(([k, l]) => {
          const n = counts[k];
          const on = view.filter === k;
          if (k !== 'all' && !n) return null;          // never offer a filter that finds nothing
          return (
            <button key={k} onClick={() => set({ filter: k })}
              className={`px-2 py-1 rounded-full border text-[11.5px] font-medium transition ${on
                ? 'border-brand-500 bg-brand-500 text-white'
                : 'border-surface-3 text-gray-400 hover:text-gray-200 hover:border-surface-4'}`}>
              {l}{n != null && <span className={`ml-1 ${on ? 'text-white/70' : 'text-gray-600'}`}>{n}</span>}
            </button>
          );
        })}
        <span className="ml-auto text-[11px] text-gray-500">
          showing {shown} of {total}
        </span>
      </div>
    </div>
  );
}
