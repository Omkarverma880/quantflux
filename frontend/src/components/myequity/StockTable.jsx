import React, { useMemo, useState } from 'react';
import { Check, Loader2, Pencil, Trash2, X, ArrowUpDown, Maximize2 } from 'lucide-react';
import { api } from '../../api';
import { N, PCT, COMPACT, signTone, RSI_TONE, VolumeSparkline, SensitivityMeter, LevelChips, AlertPills } from './ui';

/** The workspace table. A row blinks while price is sitting on one of its research levels. */

const COLS = [
  ['symbol', 'Stock', 'left'],
  ['ltp', 'LTP', 'right'],
  ['high52', '52w high', 'right'],
  ['rsi', 'RSI', 'right'],
  ['life', 'Lifetime high / low', 'right'],
  ['volume', 'Volume · latest vs older', 'left'],
  ['sensitivity', 'Sensitivity', 'left'],
  ['added_on', 'Added', 'right'],
  ['levels', 'Research levels', 'left'],
  ['alerts', 'Watch', 'left'],
  ['actions', '', 'right'],
];

const value = (r, key) => ({
  symbol: r.symbol,
  ltp: r.ltp,
  high52: r.extremes?.from_52w_high,
  rsi: r.rsi,
  life: r.extremes?.from_life_high,
  volume: r.volume?.vs_avg20,
  sensitivity: r.sensitivity?.score,
  added_on: r.added_on,
  levels: r.watch?.nearest?.distance_pct == null ? null : Math.abs(r.watch.nearest.distance_pct),
  alerts: (r.alerts || []).length,
}[key]);

function EditLevels({ row, onSaved, onCancel }) {
  const [text, setText] = useState((row.levels || []).join(', '));
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      const r = await api.meUpdate(row.id, { levels: text });
      if (r.status === 'ok') onSaved(r.stock);
    } finally { setBusy(false); }
  };
  return (
    <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
      <input autoFocus value={text} onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') save(); if (e.key === 'Escape') onCancel(); }}
        placeholder="3100, 2900" className="input-field !py-1 !px-2 text-[12px] mono w-36" />
      <button onClick={save} disabled={busy} className="text-emerald-400 hover:text-emerald-300 p-1">
        {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
      </button>
      <button onClick={onCancel} className="text-gray-500 hover:text-gray-300 p-1"><X className="w-3.5 h-3.5" /></button>
    </div>
  );
}

export default function StockTable({ rows = [], onOpen, onChanged, loading }) {
  const [sort, setSort] = useState({ key: 'added_on', dir: 'desc' });
  const [editing, setEditing] = useState(null);
  const [removing, setRemoving] = useState(null);

  const sorted = useMemo(() => {
    const d = sort.dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = value(a, sort.key); const bv = value(b, sort.key);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === 'string') return av < bv ? -d : av > bv ? d : 0;
      return (Number(av) - Number(bv)) * d;
    });
  }, [rows, sort]);

  // Tailwind only ships classes it can see in the source, so map instead of interpolating.
  const ALIGN = { left: 'text-left', right: 'text-right' };
  const head = (key, label, align) => (
    <th key={key} className={`px-2.5 py-2 font-medium ${ALIGN[align]} ${key === 'actions' ? '' : 'cursor-pointer select-none hover:text-gray-300'}`}
      onClick={() => key !== 'actions' && setSort((s) => ({ key, dir: s.key === key && s.dir === 'desc' ? 'asc' : 'desc' }))}>
      <span className="inline-flex items-center gap-1">{label}
        {sort.key === key && key !== 'actions' && <ArrowUpDown className="w-3 h-3 text-brand-400" />}</span>
    </th>
  );

  const remove = async (row, e) => {
    e.stopPropagation();
    if (!window.confirm(`Remove ${row.symbol} from your workspace? Your levels and note go with it.`)) return;
    setRemoving(row.id);
    try {
      const r = await api.meRemove(row.id);
      if (r.status === 'ok') onChanged?.();
    } finally { setRemoving(null); }
  };

  if (!rows.length) {
    return (
      <div className="card text-center py-12">
        <div className="text-[14px] text-gray-300 font-semibold">Your workspace is empty</div>
        <div className="text-[12.5px] text-gray-500 mt-1">
          Add the stocks you research, with the levels you are waiting for. Prices, RSI, extremes,
          volume flow and the level watch fill in automatically.
        </div>
      </div>
    );
  }

  return (
    <div className="card !p-0 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[1180px] text-[12px]">
          <thead>
            <tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
              {COLS.map(([k, l, a]) => head(k, l, a))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => {
              const blink = !!r.watch?.touched;
              const ext = r.extremes || {};
              return (
                <tr key={r.id} onClick={() => onOpen?.(r)}
                  className={`border-b border-surface-3/40 cursor-pointer transition-colors ${blink ? 'row-blink' : 'hover:bg-surface-2/60'}`}>
                  <td className="px-2.5 py-2">
                    <div className="flex items-center gap-1.5">
                      {blink && <span className="w-1.5 h-1.5 rounded-full bg-amber-500 dot-blink shrink-0" />}
                      <div className="min-w-0">
                        <div className="font-semibold text-gray-100 truncate">{r.symbol}
                          <span className="ml-1 text-[9.5px] text-gray-500">{r.exchange}</span></div>
                        <div className="text-[10.5px] text-gray-500 truncate max-w-[170px]">{r.company || '—'}</div>
                      </div>
                    </div>
                  </td>

                  <td className="px-2.5 py-2 text-right mono whitespace-nowrap">
                    {r.history === false && !r.ltp ? <span className="text-gray-600">—</span> : (
                      <>
                        <div className="text-gray-100 font-semibold">{N(r.ltp)}</div>
                        <div className={`text-[10.5px] ${signTone(r.change_pct)}`}>{PCT(r.change_pct)}</div>
                      </>
                    )}
                  </td>

                  <td className="px-2.5 py-2 text-right mono whitespace-nowrap">
                    <div className="text-gray-200">{N(ext.high_52w)}</div>
                    <div className={`text-[10.5px] ${signTone(ext.from_52w_high)}`}>{PCT(ext.from_52w_high, 1)}</div>
                  </td>

                  <td className="px-2.5 py-2 text-right mono whitespace-nowrap">
                    <span className={`${RSI_TONE[r.rsi_state] || 'text-gray-300'} ${r.rsi_state === 'oversold' ? 'dot-blink' : ''}`}>
                      {r.rsi == null ? '—' : r.rsi.toFixed(1)}
                    </span>
                  </td>

                  <td className="px-2.5 py-2 text-right mono whitespace-nowrap"
                    title={ext.high_life_on ? `high on ${ext.high_life_on} · low on ${ext.low_life_on} · ${ext.sessions} sessions from ${ext.first_session}` : ''}>
                    <div className="text-gray-200">{N(ext.high_life)}</div>
                    <div className="text-[10.5px] text-gray-500">{N(ext.low_life)}</div>
                  </td>

                  <td className="px-2.5 py-2">
                    <div className="flex items-center gap-2">
                      <VolumeSparkline bars={r.volume?.bars || []} />
                      <div className="leading-tight whitespace-nowrap">
                        <div className="text-gray-200 mono text-[11.5px]">{COMPACT(r.volume?.latest)}</div>
                        <div className={`text-[10.5px] ${(r.volume?.vs_avg20 || 0) >= 1.5 ? 'text-emerald-400' : 'text-gray-500'}`}>
                          {r.volume?.vs_avg20 ? `${r.volume.vs_avg20}× 20d` : '—'}
                        </div>
                      </div>
                    </div>
                  </td>

                  <td className="px-2.5 py-2"><SensitivityMeter sens={r.sensitivity} /></td>

                  <td className="px-2.5 py-2 text-right whitespace-nowrap">
                    <div className="text-gray-300 mono text-[11.5px]">{r.added_on || '—'}</div>
                    <div className="text-[10.5px] text-gray-500">
                      {r.days_since_added == null ? '' : r.days_since_added === 0 ? 'today' : `${r.days_since_added}d ago`}
                    </div>
                  </td>

                  <td className="px-2.5 py-2" onClick={(e) => { if (editing === r.id) e.stopPropagation(); }}>
                    {editing === r.id
                      ? <EditLevels row={r} onSaved={() => { setEditing(null); onChanged?.(); }} onCancel={() => setEditing(null)} />
                      : (
                        <div className="flex items-center gap-1.5">
                          <LevelChips watch={r.watch} onEdit={() => setEditing(r.id)} />
                          <button onClick={(e) => { e.stopPropagation(); setEditing(r.id); }}
                            className="text-gray-600 hover:text-brand-400 p-0.5" title="edit levels">
                            <Pencil className="w-3 h-3" />
                          </button>
                        </div>
                      )}
                  </td>

                  <td className="px-2.5 py-2 max-w-[230px]">
                    {r.error ? <span className="text-[10.5px] text-red-400">{r.error}</span>
                      : r.history === false ? <span className="text-[10.5px] text-gray-500">{r.message}</span>
                        : <AlertPills alerts={r.alerts} />}
                  </td>

                  <td className="px-2.5 py-2 text-right whitespace-nowrap">
                    <button onClick={(e) => { e.stopPropagation(); onOpen?.(r); }}
                      className="text-gray-500 hover:text-brand-400 p-1" title="open the X-ray">
                      <Maximize2 className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={(e) => remove(r, e)} disabled={removing === r.id}
                      className="text-gray-600 hover:text-red-400 p-1" title="remove from workspace">
                      {removing === r.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {loading && (
        <div className="px-3 py-1.5 text-[11px] text-gray-500 flex items-center gap-1.5 border-t border-surface-3">
          <Loader2 className="w-3 h-3 animate-spin" />refreshing prices…
        </div>
      )}
    </div>
  );
}
