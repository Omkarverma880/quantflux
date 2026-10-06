import React, { useMemo, useState } from 'react';
import { X, Loader2, AlertTriangle, Plus, Trash2 } from 'lucide-react';
import { api } from '../../api';
import { N } from './ui';

/**
 * Change anything about a stock already in the workspace.
 *
 * The research date matters most: it decides which session a level may first trigger on, so
 * getting it wrong distorts every P&L figure behind the row. It was previously only settable
 * when the stock was added, which made a typo permanent.
 */

const CATS = [
  ['SWING', 'Swing trade', 'bg-sky-500/20 text-sky-200 border-sky-500/50', 'judged over 10 sessions'],
  ['INVESTMENT', 'Investment', 'bg-violet-500/20 text-violet-200 border-violet-500/50', 'judged over 60 sessions'],
  ['FNO', 'F&O', 'bg-amber-500/20 text-amber-200 border-amber-500/50', 'options can be bought on a level'],
];

export default function EditStock({ row, onClose, onSaved }) {
  const [form, setForm] = useState({
    added_on: row.added_on || '',
    levels: (row.levels || []).map((l) => ({
      price: String(l.price), side: l.side || 'LONG',
      target: l.target == null ? '' : String(l.target),
      stop: l.stop == null ? '' : String(l.stop),
      booked: (l.booked || []).length,
    })),
    touch_pct: row.touch_pct ?? 0.25,
    category: row.category || 'SWING',
    note: row.note || '',
    alerts_on: row.alerts_on !== false,
    auto_fno: !!row.auto_fno,
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const setLevel = (i, patch) => setForm((f) => ({
    ...f, levels: f.levels.map((l, j) => (j === i ? { ...l, ...patch } : l)),
  }));
  const addLevel = () => setForm((f) => ({
    ...f, levels: [...f.levels, { price: '', side: 'LONG', target: '', stop: '', booked: 0 }],
  }));
  const removeLevel = (i) => setForm((f) => ({ ...f, levels: f.levels.filter((_, j) => j !== i) }));

  const changedDate = form.added_on !== (row.added_on || '');
  const cat = useMemo(() => CATS.find((c) => c[0] === form.category) || CATS[0], [form.category]);

  const save = async () => {
    setBusy(true); setErr('');
    try {
      const r = await api.meUpdate(row.id, {
        added_on: form.added_on || null,
        levels: form.levels
          .filter((l) => String(l.price).trim() !== '' && Number(l.price) > 0)
          .map((l) => ({
            price: Number(l.price), side: l.side,
            target: l.target === '' ? null : Number(l.target),
            stop: l.stop === '' ? null : Number(l.stop),
          })),
        touch_pct: Number(form.touch_pct) || 0.25,
        category: form.category,
        note: form.note,
        alerts_on: form.alerts_on,
        auto_fno: form.auto_fno,
      });
      if (r.status !== 'ok') throw new Error(r.message || 'could not save it');
      onSaved?.(r);
      onClose?.();
    } catch (e) {
      setErr(String(e.message || e));
    } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="w-full max-w-lg card !p-4 space-y-3 max-h-[92vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-[15px] font-bold text-white flex items-center gap-2">
              Edit {row.symbol}
              <span className="text-[10px] text-gray-500 border border-surface-3 rounded px-1">{row.exchange}</span>
              {row.fno && <span className="px-1 py-px rounded text-[9px] font-bold bg-violet-500/15 border border-violet-500/40 text-violet-300">F&amp;O</span>}
            </div>
            <div className="text-[11.5px] text-gray-500 mt-0.5">{row.company} · last trade {N(row.ltp)}</div>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-200"><X className="w-4 h-4" /></button>
        </div>

        <Field label="When did you research it?"
          hint="decides the first session a level may trigger on">
          <input type="date" value={form.added_on} onChange={(e) => set({ added_on: e.target.value })}
            className="input-field !py-1.5 mt-1 w-full mono" />
        </Field>
        {changedDate && (
          <div className="text-[11.5px] text-amber-400 flex items-start gap-1.5">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" />
            Moving the date re-reads every level against stored history, so the triggers and the
            P&amp;L behind this row will change.
          </div>
        )}

        <div>
          <div className="flex h-4 items-baseline justify-between gap-2">
            <span className="text-[10px] uppercase tracking-wider text-gray-500">Research entry levels</span>
            <span className="text-[10px] text-gray-600">
              {row.fno ? 'each level says which way, and so which option it buys' : 'each level is tracked on its own'}
            </span>
          </div>
          <div className="space-y-1.5 mt-1.5">
            {form.levels.map((lv, i) => (
              <div key={i} className="flex items-center gap-1.5">
                <input type="number" step="0.05" value={lv.price} placeholder="3000"
                  onChange={(e) => setLevel(i, { price: e.target.value })}
                  className="input-field !py-1.5 w-28 mono" />
                <select value={lv.side} onChange={(e) => setLevel(i, { side: e.target.value })}
                  className={`input-field !py-1.5 flex-1 ${lv.side === 'SHORT' ? 'text-red-300' : 'text-emerald-300'}`}>
                  <option value="LONG">{row.fno ? 'Long — buy the ATM call' : 'Long — expecting it up'}</option>
                  <option value="SHORT">{row.fno ? 'Short — buy the ATM put' : 'Short — expecting it down'}</option>
                </select>
                <input type="number" step="0.05" value={lv.target} placeholder="target"
                  onChange={(e) => setLevel(i, { target: e.target.value })}
                  className="input-field !py-1.5 w-20 mono" />
                <input type="number" step="0.05" value={lv.stop} placeholder="stop"
                  onChange={(e) => setLevel(i, { stop: e.target.value })}
                  className="input-field !py-1.5 w-20 mono" />
                <button onClick={() => removeLevel(i)} title="remove this level"
                  className="text-gray-600 hover:text-red-400 p-1 shrink-0">
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
            {!form.levels.length && (
              <div className="text-[11.5px] text-gray-500">No levels yet.</div>
            )}
            <button onClick={addLevel}
              className="btn-secondary !py-1 !px-2 text-[11.5px] flex items-center gap-1">
              <Plus className="w-3 h-3" />Add a level
            </button>
          </div>
          <div className="text-[10.5px] text-gray-500 mt-1.5">
            {form.levels.some((l) => l.booked > 0)
              ? 'A level you keep keeps its bookings and its re-arm date; the direction, target and stop are whatever you set here.'
              : 'A target must sit the way the level is meant to go, and the stop the other way — anything else is dropped.'}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Touch tolerance" hint="how close counts as at the level">
            <div className="flex items-center gap-1 mt-1">
              <input type="number" step="0.05" min="0.01" value={form.touch_pct}
                onChange={(e) => set({ touch_pct: e.target.value })}
                className="input-field !py-1.5 w-full mono" />
              <span className="text-[11px] text-gray-500">%</span>
            </div>
          </Field>
          <Field label="Alerts" hint="Telegram, while the market is open">
            <label className="flex items-center gap-2 mt-2 text-[12.5px] text-gray-300 cursor-pointer">
              <input type="checkbox" checked={form.alerts_on}
                onChange={(e) => set({ alerts_on: e.target.checked })} className="accent-brand-500" />
              send alerts for this stock
            </label>
          </Field>
        </div>

        <div>
          <div className="text-[10px] uppercase tracking-wider text-gray-500">Trade category</div>
          <div className="flex items-center gap-2 mt-1.5">
            {CATS.map(([c, label, on]) => (
              <button key={c} onClick={() => set({ category: c })}
                className={`px-2.5 py-1 rounded border text-[11.5px] font-semibold ${form.category === c
                  ? on : 'border-surface-3 text-gray-400 hover:text-gray-200'}`}>
                {label}
              </button>
            ))}
          </div>
          <div className="text-[10.5px] text-gray-500 mt-1">{cat[3]}</div>
        </div>

        {row.fno && (
          <label className="flex items-start gap-2 text-[12.5px] cursor-pointer rounded-lg border border-surface-3 px-3 py-2">
            <input type="checkbox" checked={form.auto_fno}
              onChange={(e) => set({ auto_fno: e.target.checked })} className="accent-amber-500 mt-0.5" />
            <span>
              <span className="text-gray-200">Buy an option automatically when a level triggers</span>
              <span className="block text-[11px] text-gray-500">
                One lot at the money — a call on a LONG level, a put on a SHORT one. The master
                “auto option buy” switch must also be on, and you close the position yourself.
              </span>
            </span>
          </label>
        )}

        <Field label="Why you are watching it" hint="optional">
          <input value={form.note} onChange={(e) => set({ note: e.target.value })}
            placeholder="breakout retest above the monthly range" className="input-field !py-1.5 mt-1 w-full" />
        </Field>

        {err && <div className="text-[12px] text-red-400 flex items-start gap-1.5">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-px" />{err}</div>}

        <div className="flex gap-2">
          <button onClick={save} disabled={busy}
            className="flex-1 !py-2 text-[13px] font-semibold rounded-lg bg-brand-600 hover:bg-brand-700 text-white disabled:opacity-50">
            {busy ? <Loader2 className="w-4 h-4 animate-spin mx-auto" /> : 'Save changes'}
          </button>
          <button onClick={onClose} className="btn-secondary !py-2 !px-3 text-[12.5px]">Cancel</button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, hint, children }) {
  return (
    <label className="block">
      <div className="flex h-4 items-baseline justify-between gap-2 overflow-hidden whitespace-nowrap">
        <span className="text-[10px] uppercase tracking-wider text-gray-500">{label}</span>
        {hint && <span className="text-[10px] text-gray-600 truncate">{hint}</span>}
      </div>
      {children}
    </label>
  );
}
