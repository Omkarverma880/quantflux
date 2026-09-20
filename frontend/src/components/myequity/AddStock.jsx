import React, { useEffect, useRef, useState } from 'react';
import { Plus, Search, Loader2, X, CalendarDays } from 'lucide-react';
import { api } from '../../api';
import { CategoryChip } from './ui';

/**
 * Add a stock you have researched.
 *
 * The research date is asked outright and is not assumed to be today: you may be adding work you
 * did months ago, and every level trigger and P&L in the workspace is measured from that date.
 */

const today = () => new Date().toISOString().slice(0, 10);
const EMPTY = {
  symbol: '', exchange: null, company: '', levels: '', note: '',
  added_on: today(), touch_pct: 0.25, category: 'SWING',
};

export default function AddStock({ onAdded, connected }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [hits, setHits] = useState([]);
  const [searching, setSearching] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const timer = useRef(null);

  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const backdated = form.added_on && form.added_on !== today();

  useEffect(() => {
    if (!open || !connected) return undefined;
    const q = form.symbol.trim();
    if (q.length < 2 || form.exchange) { setHits([]); return undefined; }
    clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      setSearching(true);
      try {
        const r = await api.meSearch(q);
        setHits(r.status === 'ok' ? r.results : []);
      } catch { setHits([]); } finally { setSearching(false); }
    }, 220);
    return () => clearTimeout(timer.current);
  }, [form.symbol, form.exchange, open, connected]);

  const choose = (h) => { set({ symbol: h.symbol, exchange: h.exchange, company: h.company }); setHits([]); };

  const submit = async () => {
    if (!form.symbol.trim()) { setErr('Pick a stock first'); return; }
    if (!form.added_on) { setErr('Set the date you did this research — the P&L is measured from it'); return; }
    setBusy(true); setErr('');
    try {
      const r = await api.meAdd({
        symbol: form.symbol.trim().toUpperCase(), exchange: form.exchange,
        levels: form.levels, note: form.note, added_on: form.added_on,
        touch_pct: Number(form.touch_pct) || 0.25, category: form.category,
      });
      if (r.status !== 'ok') { setErr(r.message || 'could not add'); return; }
      setForm(EMPTY); setOpen(false);
      onAdded?.(r.stock);
    } catch (e) { setErr(String(e.message || e)); } finally { setBusy(false); }
  };

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className="btn-primary !py-2 !px-4 text-[13px] flex items-center gap-2 shrink-0">
        <Plus className="w-4 h-4" />Add stock
      </button>
    );
  }

  return (
    <div className="card !p-4 space-y-3 w-full">
      <div className="flex items-center justify-between">
        <div className="text-[13px] font-semibold text-gray-100">Add a researched stock</div>
        <button onClick={() => { setOpen(false); setErr(''); }} className="text-gray-500 hover:text-gray-200"><X className="w-4 h-4" /></button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div className="relative">
          <div className="text-[10px] uppercase tracking-wider text-gray-500">Stock</div>
          <div className="relative mt-1">
            <Search className="w-3.5 h-3.5 text-gray-500 absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input autoFocus value={form.symbol} placeholder={connected ? 'RELIANCE, BSE, TCS…' : 'connect Zerodha to search'}
              onChange={(e) => set({ symbol: e.target.value.toUpperCase(), exchange: null, company: '' })}
              onKeyDown={(e) => { if (e.key === 'Enter' && hits.length) choose(hits[0]); }}
              className="input-field !py-1.5 !pl-8 w-full" />
            {searching && <Loader2 className="w-3.5 h-3.5 animate-spin text-gray-500 absolute right-2.5 top-1/2 -translate-y-1/2" />}
          </div>
          {form.company && <div className="text-[11px] text-gray-500 mt-1 truncate">{form.company} · {form.exchange}</div>}
          {!!hits.length && (
            <div className="absolute z-20 mt-1 w-full max-h-56 overflow-auto rounded-lg border border-surface-3 bg-surface-1 shadow-xl">
              {hits.map((h) => (
                <button key={`${h.exchange}-${h.symbol}`} onClick={() => choose(h)}
                  className="w-full text-left px-3 py-1.5 hover:bg-surface-2 border-b border-surface-3/40 last:border-0">
                  <div className="text-[12.5px] text-gray-100 font-semibold">{h.symbol} <span className="text-[10px] text-gray-500">{h.exchange}</span></div>
                  <div className="text-[11px] text-gray-500 truncate">{h.company}</div>
                </button>
              ))}
            </div>
          )}
        </div>

        <label className="block">
          <div className="text-[10px] uppercase tracking-wider text-gray-500">Research entry levels</div>
          <input value={form.levels} placeholder="3100, 2900" onChange={(e) => set({ levels: e.target.value })}
            className="input-field !py-1.5 mt-1 w-full mono" />
          <div className="text-[10.5px] text-gray-500 mt-0.5">One or many — each is tracked on its own</div>
        </label>

        <label className="block">
          <div className="text-[10px] uppercase tracking-wider text-gray-500 flex items-center gap-1">
            <CalendarDays className="w-3 h-3" />When did you research it?
          </div>
          <input type="date" value={form.added_on} max={today()} onChange={(e) => set({ added_on: e.target.value })}
            className={`input-field !py-1.5 mt-1 w-full ${backdated ? 'border-brand-500/60' : ''}`} />
          <div className="text-[10.5px] text-gray-500 mt-0.5">
            {backdated ? 'Back-dated — triggers before today will be found in the history'
              : 'Researched earlier? Set the real date so the P&L is honest'}
          </div>
        </label>

        <div>
          <div className="text-[10px] uppercase tracking-wider text-gray-500">Trade category</div>
          <div className="flex items-center gap-2 mt-1.5">
            {['SWING', 'INVESTMENT'].map((c) => (
              <button key={c} onClick={() => set({ category: c })}
                className={`px-2.5 py-1 rounded border text-[11.5px] font-semibold ${form.category === c
                  ? (c === 'INVESTMENT' ? 'bg-violet-500/20 text-violet-200 border-violet-500/50' : 'bg-sky-500/20 text-sky-200 border-sky-500/50')
                  : 'border-surface-3 text-gray-400 hover:text-gray-200'}`}>
                {c === 'INVESTMENT' ? 'Investment' : 'Swing trade'}
              </button>
            ))}
          </div>
          <div className="text-[10.5px] text-gray-500 mt-1">
            {form.category === 'INVESTMENT' ? 'Judged over 60 sessions' : 'Judged over 10 sessions'}
          </div>
        </div>

        <label className="block">
          <div className="text-[10px] uppercase tracking-wider text-gray-500">Touch tolerance</div>
          <div className="flex items-center gap-2 mt-1">
            <input type="number" step="0.05" min="0.01" max="10" value={form.touch_pct}
              onChange={(e) => set({ touch_pct: e.target.value })} className="input-field !py-1.5 w-full" />
            <span className="text-[12px] text-gray-500">%</span>
          </div>
          <div className="text-[10.5px] text-gray-500 mt-0.5">How close counts as being at the level</div>
        </label>

        <label className="block sm:col-span-2 lg:col-span-3">
          <div className="text-[10px] uppercase tracking-wider text-gray-500">Why you are watching it</div>
          <input value={form.note} placeholder="breakout retest above the monthly range, results on the 28th…"
            onChange={(e) => set({ note: e.target.value })} className="input-field !py-1.5 mt-1 w-full" />
        </label>
      </div>

      {err && <div className="text-[12px] text-red-400">{err}</div>}
      <div className="flex items-center gap-2">
        <button onClick={submit} disabled={busy} className="btn-primary !py-1.5 !px-4 text-[12.5px] flex items-center gap-2 disabled:opacity-50">
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}Add to workspace
        </button>
        <button onClick={() => { setOpen(false); setErr(''); }} className="btn-secondary !py-1.5 !px-3 text-[12.5px]">Cancel</button>
        <span className="text-[11px] text-gray-500 flex items-center gap-1.5">
          adding as <CategoryChip category={form.category} small />
        </span>
      </div>
    </div>
  );
}
