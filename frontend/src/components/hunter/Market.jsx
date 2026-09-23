import React, { useCallback, useEffect, useState } from 'react';
import { Search, ArrowUp, ArrowDown, Download, Loader2 } from 'lucide-react';
import { api } from '../../api';
import { N, N0, PCT, RS_, RsPill, STAGE_STYLE, tone, Note } from './ui';

/**
 * The whole market as the scan saw it after the close — every stock it measured, setup or not,
 * in one sortable table. Click any row to open its chart tile.
 */
const COLS = [
  ['symbol', 'Stock', 'left'],
  ['close', 'Price', 'right'],
  ['change_pct', 'Day', 'right'],
  ['rs_rating', 'RS', 'right'],
  ['from_52w_high_pct', 'From high', 'right'],
  ['above_50dma_pct', 'vs 50-day', 'right'],
  ['atr_pct', 'Daily range', 'right'],
  ['up_down_volume', 'Up/down vol', 'right'],
  ['volume_x', 'Volume today', 'right'],
  ['turnover_cr', 'Turnover', 'right'],
  ['stage', 'Stage', 'left'],
  ['industry', 'Industry', 'left'],
];
const CELL = {
  close: (r) => RS_(r.close),
  change_pct: (r) => PCT(r.change_pct),
  rs_rating: (r) => r.rs_rating ?? '—',
  from_52w_high_pct: (r) => PCT(r.from_52w_high_pct),
  above_50dma_pct: (r) => PCT(r.above_50dma_pct),
  atr_pct: (r) => (r.atr_pct != null ? `${r.atr_pct}%` : '—'),
  up_down_volume: (r) => (r.up_down_volume != null ? r.up_down_volume.toFixed(2) : '—'),
  volume_x: (r) => (r.volume_x != null ? `${r.volume_x}×` : '—'),
  turnover_cr: (r) => (r.turnover_cr != null ? `₹${N0(r.turnover_cr)} cr` : '—'),
};
const PAGE = 100;

export default function Market({ onOpen }) {
  const [d, setD] = useState(null);
  const [f, setF] = useState({ q: '', industry: '', stage: '', sort: 'rs_rating', desc: true });
  const [shown, setShown] = useState(PAGE);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (filters, limit) => {
    setBusy(true);
    try {
      const r = await api.huMarket({ ...filters, limit });
      if (r.status === 'ok') setD(r);
    } finally { setBusy(false); }
  }, []);

  useEffect(() => { load(f, shown); }, [f, shown, load]);

  const sortBy = (key) => {
    setShown(PAGE);
    setF((p) => ({ ...p, sort: key, desc: p.sort === key ? !p.desc : true }));
  };
  const csv = () => {
    const head = COLS.map(([, l]) => l).join(',');
    const lines = (d?.rows || []).map((r) => COLS.map(([k]) => {
      const v = CELL[k] ? String(CELL[k](r)).replace(/[₹,×%]/g, '') : (r[k] ?? '');
      return typeof v === 'string' && v.includes(',') ? `"${v}"` : v;
    }).join(','));
    const b = new Blob([[head, ...lines].join('\n')], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(b);
    a.download = `hunter-market-${d?.scan_date || 'latest'}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  if (d?.empty) return <Note>{d.message}</Note>;

  return (
    <div className="card !p-4">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <div className="mr-auto">
          <h3 className="text-[15px] font-bold text-white">Market</h3>
          <div className="text-[11.5px] text-gray-500">
            {d ? `${d.scanned} stocks measured after the close on ${d.scan_date} · showing ${Math.min(shown, d.total)} of ${d.total}` : 'loading…'}
          </div>
        </div>
        <div className="relative">
          <Search className="w-3.5 h-3.5 text-gray-500 absolute left-2 top-1/2 -translate-y-1/2" />
          <input value={f.q} onChange={(e) => { setShown(PAGE); setF({ ...f, q: e.target.value }); }}
            placeholder="Search any stock" className="input-field !py-1.5 !pl-7 text-[12px] w-[170px]" />
        </div>
        <select value={f.stage} onChange={(e) => { setShown(PAGE); setF({ ...f, stage: e.target.value }); }}
          className="bg-surface-2 border border-surface-3 rounded px-2 py-1.5 text-[12px] text-gray-300">
          <option value="">Every stage</option>
          {['FORMING', 'BREAKOUT', 'CLIMBING', 'PLAYED_OUT', 'NONE'].map((s) => (
            <option key={s} value={s}>{s === 'NONE' ? 'No setup' : s.replace('_', ' ').toLowerCase()}
              {d?.counts?.[s] != null ? ` (${d.counts[s]})` : ''}</option>
          ))}
        </select>
        <select value={f.industry} onChange={(e) => { setShown(PAGE); setF({ ...f, industry: e.target.value }); }}
          className="bg-surface-2 border border-surface-3 rounded px-2 py-1.5 text-[12px] text-gray-300 max-w-[190px]">
          <option value="">All industries</option>
          {(d?.industries || []).map((i) => <option key={i} value={i}>{i}</option>)}
        </select>
        <button onClick={csv} className="btn-secondary !py-1.5 !px-2 text-[11.5px] flex items-center gap-1">
          <Download className="w-3 h-3" />CSV
        </button>
      </div>

      <div className="overflow-x-auto max-h-[70vh] overflow-y-auto">
        <table className="w-full text-[12px] min-w-[1040px]">
          <thead className="sticky top-0 bg-surface-1 z-10">
            <tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
              {COLS.map(([k, label, align]) => (
                <th key={k} onClick={() => sortBy(k)}
                  className={`px-2 py-1.5 font-medium cursor-pointer select-none hover:text-gray-300 text-${align}`}>
                  <span className="inline-flex items-center gap-0.5">
                    {label}
                    {f.sort === k && (f.desc ? <ArrowDown className="w-3 h-3" /> : <ArrowUp className="w-3 h-3" />)}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(d?.rows || []).map((r) => (
              <tr key={r.symbol} onClick={() => onOpen?.(r.symbol)}
                className="border-b border-surface-3/40 hover:bg-surface-2/60 cursor-pointer">
                <td className="px-2 py-1.5">
                  <div className="flex items-center gap-1.5">
                    <span className="font-semibold text-gray-100">{r.symbol}</span>
                    {r.blue_sky && <span className="text-[9px] px-1 rounded bg-emerald-500/15 text-emerald-400">blue sky</span>}
                  </div>
                  <div className="text-[10px] text-gray-500 truncate max-w-[210px]">{r.name}</div>
                </td>
                <td className="px-2 py-1.5 mono text-right text-gray-200">{CELL.close(r)}</td>
                <td className={`px-2 py-1.5 mono text-right ${tone(r.change_pct)}`}>{CELL.change_pct(r)}</td>
                <td className="px-2 py-1.5 text-right"><RsPill value={r.rs_rating} /></td>
                <td className={`px-2 py-1.5 mono text-right ${tone(r.from_52w_high_pct)}`}>{CELL.from_52w_high_pct(r)}</td>
                <td className={`px-2 py-1.5 mono text-right ${tone(r.above_50dma_pct)}`}>{CELL.above_50dma_pct(r)}</td>
                <td className="px-2 py-1.5 mono text-right text-gray-400">{CELL.atr_pct(r)}</td>
                <td className={`px-2 py-1.5 mono text-right ${tone(r.up_down_volume)}`}>{CELL.up_down_volume(r)}</td>
                <td className="px-2 py-1.5 mono text-right text-gray-400">{CELL.volume_x(r)}</td>
                <td className="px-2 py-1.5 mono text-right text-gray-400">{CELL.turnover_cr(r)}</td>
                <td className="px-2 py-1.5">
                  {r.stage && r.stage !== 'NONE' ? (
                    <span className={`px-1.5 py-px rounded text-[10px] font-semibold ${(STAGE_STYLE[r.stage] || {}).ring} ${(STAGE_STYLE[r.stage] || {}).text}`}>
                      {r.stage.replace('_', ' ').toLowerCase()}
                    </span>
                  ) : <span className="text-[10.5px] text-gray-600">—</span>}
                </td>
                <td className="px-2 py-1.5 text-[11px] text-gray-500 truncate max-w-[170px]">{r.industry}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {d && d.total > shown && (
        <div className="text-center mt-3">
          <button onClick={() => setShown(shown + PAGE)} disabled={busy}
            className="btn-secondary !py-1.5 !px-3 text-[12px] flex items-center gap-1.5 mx-auto">
            {busy && <Loader2 className="w-3.5 h-3.5 animate-spin" />}Show {PAGE} more · {d.total - shown} left
          </button>
        </div>
      )}
      <div className="text-[11px] text-gray-500 mt-2">
        Click a column to sort by it, and any row to open that stock's chart. Every measure here is the one
        the cards use, computed on the same daily candles.
      </div>
    </div>
  );
}
