import React, { useState } from 'react';
import { Loader2, AlertTriangle, Info } from 'lucide-react';
import { api } from '../../api';

/**
 * Stock-level institutional activity, and the import that feeds it.
 *
 * No free feed publishes daily per-stock FII/DII attribution. What the published lists actually
 * report is a change in *shareholding*, which is a different measure on a different clock. So a
 * list has to be named by whoever read it, is stored as a shareholding change, and is shown
 * beside today's price and volume from the broker — the only part of this that can be verified.
 *
 * That pairing is the point. A name on an "FII bought" list that then fell on heavy volume has
 * not been confirmed by anything, and the table says so rather than leaving the impression that
 * appearing on the list was itself a signal.
 */

const N = (v, d = 2) => (v == null || Number.isNaN(Number(v)) ? '—'
  : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
const N0 = (v) => (v == null ? '—' : Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 }));
const PCT = (v, d = 2) => (v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(d)}%`);
const tone = (v) => (v == null ? 'text-gray-400' : v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-gray-300');

const COLS = ['Stock', 'Price', 'Move', '% change', 'Volume', 'vs 20d avg', 'Confirmation', 'Reaction'];
const RIGHT = new Set([1, 2, 3, 4, 5]);

export function StockActivity({ data, who }) {
  if (!data) {
    return <div className="text-[12px] text-gray-500 flex items-center gap-1.5">
      <Loader2 className="w-3.5 h-3.5 animate-spin" />Loading…</div>;
  }
  if (!data.available) {
    return (
      <div className="text-[12px] rounded-lg border border-surface-3 text-gray-400 px-3 py-2 leading-snug">
        <Info className="w-3.5 h-3.5 inline mr-1.5 -mt-0.5 text-gray-500" />{data.message}
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <div className="text-[11px] text-gray-500">
        {data.date} · source: <span className="text-gray-300">{data.source}</span> · recorded as{' '}
        <span className="mono">{data.data_type}</span>
        {data.is_latest_available && (
          <span className="ml-2 px-1 py-px rounded text-[10px] bg-amber-500/15
            border border-amber-500/40 text-amber-300">
            latest available — nothing recorded for {data.asked_for} yet
          </span>
        )}
      </div>
      <Group label={`${who} shareholding increase`} rows={data.increased} up />
      <Group label={`${who} shareholding decrease`} rows={data.decreased} />
      <div className="text-[12px] rounded-lg border border-amber-500/40 bg-amber-500/5 text-amber-300 px-3 py-2 leading-snug">
        {data.caveat}
      </div>
    </div>
  );
}

function Group({ label, rows, up }) {
  return (
    <div>
      <div className="flex items-baseline gap-2 mb-1.5">
        <span className={`text-[11px] font-semibold uppercase tracking-wider ${up ? 'text-emerald-400' : 'text-red-400'}`}>
          {label}
        </span>
        <span className="text-[10.5px] text-gray-500">{(rows || []).length} names</span>
      </div>
      {(rows || []).length ? (
        <div className="overflow-x-auto max-h-[360px]">
          <table className="w-full text-[12px]">
            <thead className="sticky top-0 z-10">
              <tr className="bg-surface-3/80 backdrop-blur-sm text-[10px] uppercase tracking-[0.12em]
                             text-gray-300 border-b-2 border-surface-4">
                {COLS.map((c, i) => (
                  <th key={c} className={`px-2 py-1.5 font-semibold whitespace-nowrap ${RIGHT.has(i) ? 'text-right' : 'text-left'}`}>
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.symbol} className="border-b border-surface-3/40">
                  <td className="px-2 py-1.5 font-semibold text-gray-100">{r.symbol}</td>
                  <td className="px-2 py-1.5 text-right mono text-gray-200">{N(r.price)}</td>
                  <td className={`px-2 py-1.5 text-right mono ${tone(r.price_move)}`}>
                    {r.price_move == null ? '—'
                      : `${r.price_move > 0 ? '+' : '−'}₹${Math.abs(r.price_move).toFixed(2)}`}
                  </td>
                  <td className={`px-2 py-1.5 text-right mono ${tone(r.price_change_pct)}`}>{PCT(r.price_change_pct)}</td>
                  <td className="px-2 py-1.5 text-right mono text-gray-400">{N0(r.volume)}</td>
                  <td className={`px-2 py-1.5 text-right mono ${r.relative_volume > 1 ? 'text-amber-300' : 'text-gray-400'}`}>
                    {r.relative_volume == null ? '—' : `${N(r.relative_volume, 2)}x`}
                  </td>
                  <td className={`px-2 py-1.5 text-[11.5px] ${
                    String(r.confirmation).startsWith('Possible positive') ? 'text-emerald-400'
                      : String(r.confirmation).startsWith('Possible negative') ? 'text-red-400'
                        : 'text-gray-500'}`}>{r.confirmation}</td>
                  <td className="px-2 py-1.5 text-[11.5px] text-gray-300">{r.reaction}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <div className="text-[12px] text-gray-500">None recorded.</div>}
    </div>
  );
}

export function ImportList({ onClose, onDone }) {
  const [form, setForm] = useState({
    trading_date: new Date().toISOString().slice(0, 10),
    institution: 'FII', increased: '', decreased: '', source: '',
  });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const set = (p) => setForm((f) => ({ ...f, ...p }));
  const split = (t) => String(t || '').split(/[\s,;|]+/).map((x) => x.trim()).filter(Boolean);

  const save = async () => {
    setBusy(true); setErr('');
    try {
      const r = await api.instImport({
        trading_date: form.trading_date,
        institution: form.institution,
        increased: split(form.increased),
        decreased: split(form.decreased),
        source: form.source,
      });
      if (r.status !== 'ok') throw new Error(r.message || 'could not save the list');
      onDone?.();
    } catch (e) {
      setErr(String(e.message || e));
    } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="w-full max-w-2xl card !p-4 space-y-3 max-h-[92vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}>
        <div>
          <div className="text-[15px] font-bold text-white">Import a stock-level activity list</div>
          <p className="text-[11.5px] text-gray-500 mt-1">
            No free feed publishes daily per-stock FII/DII attribution, so a list has to come from
            somewhere you name. It is stored as a <b>shareholding change</b> — which is what these
            lists actually report — and the app puts today&apos;s price, volume and relative volume
            beside each name, so you can see whether the tape agreed.
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <div className="text-[10px] uppercase tracking-wider text-gray-500">Session date</div>
            <input type="date" value={form.trading_date}
              onChange={(e) => set({ trading_date: e.target.value })}
              className="input-field !py-1.5 mt-1 w-full mono" />
          </label>
          <label className="block">
            <div className="text-[10px] uppercase tracking-wider text-gray-500">Institution</div>
            <select value={form.institution} onChange={(e) => set({ institution: e.target.value })}
              className="input-field !py-1.5 mt-1 w-full">
              <option value="FII">FII / FPI</option>
              <option value="DII">DII</option>
            </select>
          </label>
        </div>

        <label className="block">
          <div className="text-[10px] uppercase tracking-wider text-gray-500">
            Source <span className="text-gray-600">— required</span>
          </div>
          <input value={form.source} onChange={(e) => set({ source: e.target.value })}
            placeholder="Economic Times — FII shareholding change, read 07 Oct 2026"
            className="input-field !py-1.5 mt-1 w-full" />
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <div className="text-[10px] uppercase tracking-wider text-emerald-400">Shareholding increase</div>
            <textarea rows={7} value={form.increased} onChange={(e) => set({ increased: e.target.value })}
              placeholder="SHRIRAMFIN, HFCL, MTARTECH, MCX, WELCORP"
              className="input-field !py-1.5 mt-1 w-full mono text-[12px]" />
          </label>
          <label className="block">
            <div className="text-[10px] uppercase tracking-wider text-red-400">Shareholding decrease</div>
            <textarea rows={7} value={form.decreased} onChange={(e) => set({ decreased: e.target.value })}
              placeholder="RBLBANK, IGL, COFORGE, DELHIVERY"
              className="input-field !py-1.5 mt-1 w-full mono text-[12px]" />
          </label>
        </div>

        <p className="text-[11px] text-gray-600">
          NSE trading symbols, separated by commas, spaces or new lines. Re-importing the same
          date and institution replaces that list rather than adding to it.
        </p>

        {err && (
          <div className="text-[12px] text-red-400 flex items-start gap-1.5">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-px" />{err}
          </div>
        )}

        <div className="flex gap-2">
          <button onClick={save} disabled={busy || !form.source.trim()}
            className="flex-1 !py-2 text-[13px] font-semibold rounded-lg bg-brand-600 hover:bg-brand-700 text-white disabled:opacity-50">
            {busy ? <Loader2 className="w-4 h-4 animate-spin mx-auto" /> : 'Save the list'}
          </button>
          <button onClick={onClose} className="btn-secondary !py-2 !px-3 text-[12.5px]">Cancel</button>
        </div>
      </div>
    </div>
  );
}
