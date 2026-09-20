import React, { useState } from 'react';
import { Check, Loader2, Pencil, Trash2, X, Maximize2, Plus } from 'lucide-react';
import { api } from '../../api';
import {
  N, PCT, signTone, rowTone, ROW_CLASS, LevelChips, PnLCell, NotePills, CategoryPicker, Empty,
  AlertBell,
} from './ui';

/**
 * The workspace table.
 *
 * Row colour says one thing at a time: amber and blinking while price sits on a research level,
 * green when RSI is 30 or below, red when it is 80 or above, plain otherwise. Everything inside
 * a row keeps its own dark chip background, so the level arrows stay readable whatever the row
 * is doing — the tint never swallows the data.
 */

const COLS = [
  ['stock', 'Stock', 'left'],
  ['ltp', 'LTP', 'right'],
  ['high52', '52w high', 'right'],
  ['rsi', 'RSI', 'right'],
  ['levels', 'Research levels', 'left'],
  ['pnl', 'P&L since trigger', 'left'],
  ['added', 'Researched', 'right'],
  ['notes', 'What is happening', 'left'],
  ['actions', '', 'right'],
];
const ALIGN = { left: 'text-left', right: 'text-right' };

function EditLevels({ row, onSaved, onCancel }) {
  const [text, setText] = useState((row.levels || []).map((l) => l.price).join(', '));
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

function SectorTag({ row, onChanged }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(row.sector || '');
  const save = async (e) => {
    e.stopPropagation();
    const r = await api.meUpdate(row.id, { sector: text || ' ' });
    if (r.status === 'ok') { setEditing(false); onChanged?.(); }
  };
  if (editing) {
    return (
      <span className="inline-flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
        <input autoFocus value={text} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') save(e); if (e.key === 'Escape') setEditing(false); }}
          placeholder="Banking" className="input-field !py-0.5 !px-1.5 text-[10.5px] w-28" />
        <button onClick={save} className="text-emerald-400"><Check className="w-3 h-3" /></button>
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 group/sector">
      <span className={`text-[10px] ${row.sector ? 'text-gray-400' : 'text-gray-600 italic'}`}>
        {row.sector || 'sector?'}
      </span>
      <button onClick={(e) => { e.stopPropagation(); setEditing(true); }}
        className="text-gray-600 hover:text-brand-400 transition"
        title="set the sector by hand">
        <Pencil className="w-2.5 h-2.5" />
      </button>
    </span>
  );
}

export default function StockTable({ rows = [], onOpen, onChanged, onTrade, loading, dense }) {
  const [editing, setEditing] = useState(null);
  const [removing, setRemoving] = useState(null);
  const pad = dense ? 'px-2.5 py-1' : 'px-2.5 py-2';

  const remove = async (row, e) => {
    e.stopPropagation();
    if (!window.confirm(`Remove ${row.symbol} from your workspace? Your levels, research date and note go with it.`)) return;
    setRemoving(row.id);
    try {
      const r = await api.meRemove(row.id);
      if (r.status === 'ok') onChanged?.();
    } finally { setRemoving(null); }
  };

  const setCategory = async (row, next) => {
    const r = await api.meUpdate(row.id, { category: next });
    if (r.status === 'ok') onChanged?.();
  };

  const toggleAlerts = async (row, on) => {
    const r = await api.meUpdate(row.id, { alerts_on: on });
    if (r.status === 'ok') onChanged?.();
  };

  const toggleLevel = async (row, price) => {
    const levels = (row.levels || []).map((l) => (l.price === price ? { ...l, track: !l.track } : l));
    const r = await api.meUpdate(row.id, { levels });
    if (r.status === 'ok') onChanged?.();
  };

  if (!rows.length) {
    return (
      <div className="card">
        <Empty icon={Plus} title="Nothing to show here"
          hint="Add the stocks you research with the levels you are waiting for, or clear the filters above." />
      </div>
    );
  }

  const card = (r) => {
    const tone = rowTone(r);
    return (
      <div key={r.id} onClick={() => onOpen?.(r)}
        className={`card !p-3 cursor-pointer space-y-2 ${tone === 'blink' ? 'row-blink border-amber-500/30'
          : tone === 'oversold' ? 'border-emerald-500/30 bg-emerald-500/5'
            : tone === 'overbought' ? 'border-red-500/30 bg-red-500/5' : ''}`}>
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 flex-wrap">
              {tone === 'blink' && <span className="w-1.5 h-1.5 rounded-full bg-amber-500 dot-blink shrink-0" />}
              <span className="text-[14px] font-bold text-gray-100">{r.symbol}</span>
              <span className="text-[9.5px] text-gray-500">{r.exchange}</span>
              <CategoryPicker category={r.category} small onChange={(v) => setCategory(r, v)} />
            </div>
            <div className="text-[11px] text-gray-500 truncate">{r.company || '—'}{r.sector ? ` · ${r.sector}` : ''}</div>
          </div>
          <div className="text-right shrink-0">
            <div className="text-[15px] font-bold mono text-gray-100">{N(r.ltp)}</div>
            <div className={`text-[11px] mono ${signTone(r.change_pct)}`}>{PCT(r.change_pct)}</div>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-2 text-[11px]">
          <div>
            <div className="text-[9.5px] uppercase tracking-wider text-gray-500">RSI</div>
            <div className={`mono font-semibold ${r.rsi == null ? 'text-gray-600'
              : r.rsi <= 30 ? 'text-emerald-400' : r.rsi >= 80 ? 'text-red-400' : 'text-gray-300'}`}>
              {r.rsi == null ? '—' : r.rsi.toFixed(1)}
            </div>
          </div>
          <div>
            <div className="text-[9.5px] uppercase tracking-wider text-gray-500">52w high</div>
            <div className="mono text-gray-300">{N(r.high_52w, 0)}<span className={`ml-1 ${signTone(r.from_52w_high)}`}>{PCT(r.from_52w_high, 0)}</span></div>
          </div>
          <div>
            <div className="text-[9.5px] uppercase tracking-wider text-gray-500">Researched</div>
            <div className="mono text-gray-300">{r.added_on || '—'}</div>
          </div>
        </div>

        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-[9.5px] uppercase tracking-wider text-gray-500 mb-1">Levels</div>
            <LevelChips watch={r.watch} onEdit={() => setEditing(r.id)} onToggle={(price) => toggleLevel(r, price)} />
          </div>
          <div className="text-right">
            <div className="text-[9.5px] uppercase tracking-wider text-gray-500 mb-1">P&L since trigger</div>
            <PnLCell watch={r.watch} />
          </div>
        </div>

        {!!(r.notes || []).length && <NotePills notes={r.notes} max={2} />}

        <div className="flex items-center gap-2 pt-1 border-t border-surface-3">
          <button onClick={(e) => { e.stopPropagation(); onTrade?.(r, 'BUY'); }}
            className="flex-1 py-1 rounded border border-emerald-500/40 text-emerald-400 text-[11.5px] font-bold">BUY</button>
          <button onClick={(e) => { e.stopPropagation(); onTrade?.(r, 'SELL'); }}
            className="flex-1 py-1 rounded border border-red-500/40 text-red-400 text-[11.5px] font-bold">SELL</button>
          <button onClick={(e) => { e.stopPropagation(); setEditing(r.id); }}
            className="p-1 text-gray-500" title="edit levels"><Pencil className="w-3.5 h-3.5" /></button>
          <AlertBell on={r.alerts_on !== false} onToggle={(v) => toggleAlerts(r, v)} />
          <button onClick={(e) => remove(r, e)} className="p-1 text-gray-600"><Trash2 className="w-3.5 h-3.5" /></button>
        </div>

        {editing === r.id && (
          <div className="pt-1" onClick={(e) => e.stopPropagation()}>
            <EditLevels row={r} onSaved={() => { setEditing(null); onChanged?.(); }} onCancel={() => setEditing(null)} />
          </div>
        )}
      </div>
    );
  };

  return (
    <>
      {/* phones get cards: the same information without a sideways scroll */}
      <div className="grid gap-2 md:hidden">{rows.map(card)}</div>

      <div className="card !p-0 overflow-hidden hidden md:block">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[1150px] text-[12px]">
          <thead>
            <tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
              {COLS.map(([k, l, a]) => <th key={k} className={`${pad} font-medium ${ALIGN[a]}`}>{l}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const tone = rowTone(r);
              return (
                <tr key={r.id} onClick={() => onOpen?.(r)}
                  className={`border-b border-surface-3/40 cursor-pointer transition-colors ${ROW_CLASS[tone]}`}>
                  <td className={pad}>
                    <div className="flex items-center gap-1.5">
                      {tone === 'blink' && <span className="w-1.5 h-1.5 rounded-full bg-amber-500 dot-blink shrink-0" />}
                      <div className="min-w-0">
                        <div className="flex items-center gap-1.5">
                          <span className="font-semibold text-gray-100 truncate">{r.symbol}</span>
                          <span className="text-[9.5px] text-gray-500">{r.exchange}</span>
                          <CategoryPicker category={r.category} small onChange={(v) => setCategory(r, v)} />
                        </div>
                        <div className="flex items-center gap-1.5 min-w-0">
                          <span className="text-[10.5px] text-gray-500 truncate max-w-[150px]">{r.company || '—'}</span>
                          <SectorTag row={r} onChanged={onChanged} />
                        </div>
                      </div>
                    </div>
                  </td>

                  <td className={`${pad} text-right mono whitespace-nowrap`}>
                    {r.history === false && !r.ltp ? <span className="text-gray-600">—</span> : (
                      <>
                        <div className="text-gray-100 font-semibold">{N(r.ltp)}</div>
                        <div className={`text-[10.5px] ${signTone(r.change_pct)}`}>{PCT(r.change_pct)}</div>
                      </>
                    )}
                  </td>

                  <td className={`${pad} text-right mono whitespace-nowrap`}>
                    <div className="text-gray-200">{N(r.high_52w)}</div>
                    <div className={`text-[10.5px] ${signTone(r.from_52w_high)}`}>{PCT(r.from_52w_high, 1)}</div>
                  </td>

                  <td className={`${pad} text-right mono whitespace-nowrap`}>
                    <span className={`text-[13px] font-semibold ${r.rsi == null ? 'text-gray-600'
                      : r.rsi <= 30 ? 'text-emerald-400' : r.rsi >= 80 ? 'text-red-400' : 'text-gray-300'}`}>
                      {r.rsi == null ? '—' : r.rsi.toFixed(1)}
                    </span>
                  </td>

                  <td className={pad}>
                    {editing === r.id
                      ? <EditLevels row={r} onSaved={() => { setEditing(null); onChanged?.(); }} onCancel={() => setEditing(null)} />
                      : (
                        <div className="flex items-start gap-1.5">
                          <LevelChips watch={r.watch} onEdit={() => setEditing(r.id)}
                            onToggle={(price) => toggleLevel(r, price)} />
                          <button onClick={(e) => { e.stopPropagation(); setEditing(r.id); }}
                            className="text-gray-600 hover:text-brand-400 p-0.5 mt-0.5" title="edit levels">
                            <Pencil className="w-3 h-3" />
                          </button>
                        </div>
                      )}
                  </td>

                  <td className={pad}><PnLCell watch={r.watch} /></td>

                  <td className={`${pad} text-right whitespace-nowrap`}>
                    <div className="text-gray-300 mono text-[11.5px]">{r.added_on || '—'}</div>
                    <div className="text-[10.5px] text-gray-500">
                      {r.days_since_added == null ? '' : r.days_since_added === 0 ? 'today' : `${r.days_since_added}d ago`}
                    </div>
                  </td>

                  <td className={`${pad} max-w-[260px]`}>
                    {r.error ? <span className="text-[10.5px] text-red-400">{r.error}</span>
                      : r.history === false ? <span className="text-[10.5px] text-gray-500">{r.message}</span>
                        : <NotePills notes={r.notes} />}
                  </td>

                  <td className={`${pad} text-right whitespace-nowrap`}>
                    <div className="inline-flex items-center gap-1">
                      <button onClick={(e) => { e.stopPropagation(); onTrade?.(r, 'BUY'); }}
                        className="px-1.5 py-0.5 rounded border border-emerald-500/40 text-emerald-400 hover:bg-emerald-500/15 text-[10.5px] font-bold"
                        title="buy this stock">BUY</button>
                      <button onClick={(e) => { e.stopPropagation(); onTrade?.(r, 'SELL'); }}
                        className="px-1.5 py-0.5 rounded border border-red-500/40 text-red-400 hover:bg-red-500/15 text-[10.5px] font-bold"
                        title="sell this stock">SELL</button>
                      <button onClick={(e) => { e.stopPropagation(); onOpen?.(r); }}
                        className="text-gray-500 hover:text-brand-400 p-1" title="open the X-ray">
                        <Maximize2 className="w-3.5 h-3.5" />
                      </button>
                      <AlertBell on={r.alerts_on !== false} onToggle={(v) => toggleAlerts(r, v)} />
                      <button onClick={(e) => remove(r, e)} disabled={removing === r.id}
                        className="text-gray-600 hover:text-red-400 p-1" title="remove from workspace">
                        {removing === r.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                      </button>
                    </div>
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
    </>
  );
}
