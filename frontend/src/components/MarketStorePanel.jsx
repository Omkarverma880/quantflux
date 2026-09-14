import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { Database, Upload, RefreshCw, CheckCircle2, XCircle, HardDrive } from 'lucide-react';

/**
 * Market Store — the shared history every Options Lab backtest reads from.
 *
 * Upload spot or option files (parquet or csv). Each upload is merged, never
 * overwritten: rows already stored are skipped, rolling "ATM" series are
 * exploded back into the real contracts they were built from.
 */

const MB = (b) => `${(Number(b || 0) / 1e6).toFixed(1)} MB`;
const N = (v) => Number(v || 0).toLocaleString('en-IN');

function Tile({ label, value, sub }) {
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-lg px-3 py-2.5">
      <div className="text-[10.5px] uppercase tracking-wider text-gray-500">{label}</div>
      <div className="mono text-lg font-semibold text-gray-100">{value}</div>
      {sub && <div className="text-[11px] text-gray-500 mt-0.5">{sub}</div>}
    </div>
  );
}

export default function MarketStorePanel() {
  const [summary, setSummary] = useState(null);
  const [ingests, setIngests] = useState([]);
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState(null);
  const [err, setErr] = useState('');
  const fileRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const s = await api.msSummary();
      if (s?.status === 'error') setErr(s.message); else setSummary(s.store);
      const i = await api.msIngests();
      if (i?.status !== 'error') setIngests(i.ingests || []);
    } catch (e) { setErr(String(e)); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const upload = async (files) => {
    if (!files?.length) return;
    setBusy(true); setErr(''); setResults(null);
    try {
      const r = await api.msUpload(files);
      if (r?.status === 'error') setErr(r.message);
      else { setResults(r.results || []); setSummary(r.store); }
      await load();
    } catch (e) { setErr(String(e)); } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const sp = summary?.spot || {};
  const op = summary?.options || {};

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <div className="flex items-center gap-2">
            <Database className="w-4 h-4 text-brand-400" />
            <h2 className="text-sm font-semibold text-gray-100">Market Store</h2>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={load} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs bg-surface-3 text-gray-300 hover:text-white">
              <RefreshCw className="w-3.5 h-3.5" />Refresh
            </button>
            <input ref={fileRef} type="file" multiple accept=".parquet,.csv" className="hidden"
              onChange={(e) => upload(e.target.files)} />
            <button onClick={() => fileRef.current?.click()} disabled={busy}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-brand-600 text-white hover:bg-brand-500 disabled:opacity-50">
              <Upload className="w-3.5 h-3.5" />{busy ? 'Merging…' : 'Upload files'}
            </button>
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <Tile label="Option bars" value={N(op.rows)} sub={op.first_month ? `${op.first_month} → ${op.last_month}` : 'empty'} />
          <Tile label="Option months" value={op.months || 0} sub={MB(op.bytes)} />
          <Tile label="Spot bars" value={N(sp.rows)} sub={sp.first_month ? `${sp.first_month} → ${sp.last_month}` : 'empty'} />
          <Tile label="Spot months" value={sp.months || 0} sub={MB(sp.bytes)} />
        </div>

        <p className="text-[11.5px] text-gray-500 mt-3 flex items-start gap-1.5">
          <HardDrive className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>
            Candles are stored as compressed monthly Parquet; the database keeps the catalog. Uploading
            overlapping files is safe — only rows not already stored are added. Option files need
            timestamp, strike, option_type, expiry_date and OHLC; a <code className="text-[11px]">contract</code> column is
            used when present. Files are detected as spot or options from their columns.
          </span>
        </p>
      </div>

      {err && (
        <div className="rounded-lg bg-red-500/10 border border-red-500/25 px-3 py-2 text-sm text-red-300 flex items-start gap-2">
          <XCircle className="w-4 h-4 mt-0.5 shrink-0" />{err}
        </div>
      )}

      {results && (
        <div className="card">
          <h3 className="text-sm font-semibold text-gray-100 mb-2">This upload</h3>
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-gray-500">
                  {['File', 'Kind', 'Rows in', 'New rows', 'Bad OHLC', 'Outside session', 'Duplicates merged', 'Months'].map((h, i) => (
                    <th key={h} className={`px-2 py-1.5 font-semibold ${i ? 'text-right' : 'text-left'}`}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {results.map((r, i) => (
                  <tr key={i} className="border-t border-surface-3/40">
                    <td className="px-2 py-1 text-gray-300">
                      <span className="inline-flex items-center gap-1.5">
                        {r.status === 'ok' ? <CheckCircle2 className="w-3.5 h-3.5 text-green-400" /> : <XCircle className="w-3.5 h-3.5 text-red-400" />}
                        {r.filename}
                      </span>
                      {r.status !== 'ok' && <div className="text-[11px] text-red-300">{r.message}</div>}
                    </td>
                    <td className="px-2 py-1 text-right">{r.kind || '—'}</td>
                    <td className="px-2 py-1 text-right mono">{N(r.report?.rows_in)}</td>
                    <td className="px-2 py-1 text-right mono text-green-400">{N(r.rows_added)}</td>
                    <td className="px-2 py-1 text-right mono">{N(r.report?.bad_ohlc)}</td>
                    <td className="px-2 py-1 text-right mono">{N(r.report?.outside_session)}</td>
                    <td className="px-2 py-1 text-right mono">{N(r.report?.duplicates_merged)}</td>
                    <td className="px-2 py-1 text-right text-gray-400">{(r.months || []).length}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="card">
        <h3 className="text-sm font-semibold text-gray-100 mb-2">Upload history</h3>
        {ingests.length === 0 ? (
          <p className="text-sm text-gray-500">No uploads recorded yet.</p>
        ) : (
          <div className="overflow-x-auto max-h-[320px]">
            <table className="w-full text-[12px]">
              <thead className="sticky top-0 bg-surface-2">
                <tr className="text-gray-500">
                  {['When', 'File', 'Kind', 'Status', 'Rows in', 'New rows'].map((h, i) => (
                    <th key={h} className={`px-2 py-1.5 font-semibold ${i > 3 ? 'text-right' : 'text-left'}`}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ingests.map((g) => (
                  <tr key={g.id} className="border-t border-surface-3/40">
                    <td className="px-2 py-1 text-gray-400 whitespace-nowrap">{g.at?.replace('T', ' ').slice(0, 19)}</td>
                    <td className="px-2 py-1 text-gray-300">{g.filename}</td>
                    <td className="px-2 py-1">{g.kind}</td>
                    <td className={`px-2 py-1 ${g.status === 'completed' ? 'text-green-400' : 'text-red-400'}`}>{g.status}</td>
                    <td className="px-2 py-1 text-right mono">{N(g.rows_in)}</td>
                    <td className="px-2 py-1 text-right mono">{N(g.rows_added)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
