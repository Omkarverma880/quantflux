import React, { useMemo, useState } from 'react';
import { X, Loader2, AlertTriangle } from 'lucide-react';
import { api } from '../../api';
import { N } from './ui';

/**
 * Close out a trade on one research level.
 *
 * Booking does two things: it records what the level actually paid, and it re-arms the level
 * from the exit date so the same price can trigger again without the old trade being counted
 * twice. The level is never changed here — edit it separately if the next idea is at a different
 * price.
 */

const today = () => new Date().toISOString().slice(0, 10);

export default function BookTrade({ row, level, onClose, onBooked }) {
  const lv = (row.watch?.rows || []).find((r) => Number(r.level) === Number(level)) || { level };
  const [qty, setQty] = useState('');
  const [entry, setEntry] = useState(String(lv.level));
  const [exitPx, setExitPx] = useState(row.ltp ? Number(row.ltp).toFixed(2) : '');
  const [enteredOn, setEnteredOn] = useState(lv.triggered_on || row.added_on || today());
  const [exitedOn, setExitedOn] = useState(today());
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const preview = useMemo(() => {
    const e = Number(entry) || 0; const x = Number(exitPx) || 0; const q = Number(qty) || 0;
    if (!e || !x) return null;
    const per = x - e;
    const days = Math.max(0, Math.round((new Date(exitedOn) - new Date(enteredOn)) / 864e5));
    return { kind: per >= 0 ? 'PROFIT' : 'LOSS', per, pnl: q ? per * q : null,
             pct: (per / e) * 100, days };
  }, [entry, exitPx, qty, enteredOn, exitedOn]);

  const save = async () => {
    setBusy(true); setErr('');
    try {
      const r = await api.meBook(row.id, {
        level: Number(lv.level), qty: Number(qty) || 0, entry: Number(entry) || 0,
        exit: Number(exitPx), entered_on: enteredOn, exited_on: exitedOn, note,
        triggered_on: lv.triggered_on || null,
      });
      if (r.status !== 'ok') throw new Error(r.message || 'could not book it');
      onBooked?.(r);
      onClose?.();
    } catch (e) {
      setErr(String(e.message || e));
    } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="w-full max-w-md card !p-4 space-y-3" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-[15px] font-bold text-white">Book {row.symbol} · level {N(lv.level)}</div>
            <div className="text-[11.5px] text-gray-500 mt-0.5">
              {lv.triggered_on ? `triggered ${lv.triggered_on}` : 'not triggered yet'} · last trade {N(row.ltp)}
            </div>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-200"><X className="w-4 h-4" /></button>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Quantity" hint="optional — for the rupee figure">
            <input type="number" min="0" value={qty} onChange={(e) => setQty(e.target.value)}
              placeholder="25" className="input-field !py-1.5 mt-1 w-full mono" />
          </Field>
          <Field label="Bought at" hint="defaults to the level">
            <input type="number" step="0.05" value={entry} onChange={(e) => setEntry(e.target.value)}
              className="input-field !py-1.5 mt-1 w-full mono" />
          </Field>
          <Field label="Sold at">
            <input type="number" step="0.05" value={exitPx} onChange={(e) => setExitPx(e.target.value)}
              className="input-field !py-1.5 mt-1 w-full mono" />
          </Field>
          <Field label="Note" hint="optional">
            <input value={note} onChange={(e) => setNote(e.target.value)}
              placeholder="sold Monday" className="input-field !py-1.5 mt-1 w-full" />
          </Field>
          <Field label="Bought on">
            <input type="date" value={enteredOn} onChange={(e) => setEnteredOn(e.target.value)}
              className="input-field !py-1.5 mt-1 w-full mono" />
          </Field>
          <Field label="Sold on">
            <input type="date" value={exitedOn} onChange={(e) => setExitedOn(e.target.value)}
              className="input-field !py-1.5 mt-1 w-full mono" />
          </Field>
        </div>

        {preview && (
          <div className={`text-[12.5px] rounded-lg px-3 py-2 border ${preview.kind === 'PROFIT'
            ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
            : 'bg-red-500/10 border-red-500/30 text-red-300'}`}>
            {preview.kind === 'PROFIT' ? 'Profit' : 'Loss'} booked
            {preview.pnl != null && <> · <span className="mono">₹{N(Math.abs(preview.pnl), 0)}</span></>}
            {' '}· <span className="mono">{preview.pct >= 0 ? '+' : '−'}{Math.abs(preview.pct).toFixed(2)}%</span>
            {' '}· held <span className="mono">{preview.days}</span> day{preview.days === 1 ? '' : 's'}
          </div>
        )}

        <div className="text-[11px] text-gray-500">
          The level stays at {N(lv.level)} and starts hunting again from the sold date. Edit the
          level separately if your next idea is at a different price.
        </div>

        {err && <div className="text-[12px] text-red-400 flex items-start gap-1.5">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-px" />{err}</div>}

        <div className="flex gap-2">
          <button onClick={save} disabled={busy || !exitPx}
            className="flex-1 !py-2 text-[13px] font-semibold rounded-lg bg-brand-600 hover:bg-brand-700 text-white disabled:opacity-50">
            {busy ? <Loader2 className="w-4 h-4 animate-spin mx-auto" /> : 'Book it and re-arm the level'}
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
