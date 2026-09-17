import React, { useCallback, useEffect, useRef, useState } from 'react';
import { UploadCloud, LayoutGrid, CandlestickChart, Download, Info, Loader2 } from 'lucide-react';
import { api } from '../api';
import StagedFile from '../components/ingest/StagedFile';
import Coverage from '../components/ingest/Coverage';
import Explorer from '../components/ingest/Explorer';

/**
 * Data Ingestion Lab — get index and option history into the shared Market Store, check it,
 * and see it. Everything merged here feeds the OI Lab studies, the Signal Desk backtests and
 * the Options Lab. Uploads are merged, never overwritten: re-uploading a day adds nothing twice.
 */

const TABS = [
  ['upload', 'Upload & validate', UploadCloud],
  ['coverage', 'Coverage', LayoutGrid],
  ['explore', 'Explorer', CandlestickChart],
  ['imports', 'From Data Downloader', Download],
];

function Imports({ onStaged }) {
  const [rows, setRows] = useState(null);
  const [busy, setBusy] = useState(null);
  const [msg, setMsg] = useState('');
  useEffect(() => { api.diDownloader().then((r) => setRows(r.datasets || [])).catch((e) => setMsg(String(e.message || e))); }, []);
  const stage = async (id) => {
    setBusy(id); setMsg('');
    try {
      const r = await api.diDownloaderStage(id);
      if (r.status === 'ok') onStaged(r.files); else setMsg(r.message);
    } finally { setBusy(null); }
  };
  if (!rows) return <div className="text-[12.5px] text-gray-500 py-6 text-center">Loading your downloads…</div>;
  return (
    <div className="card !p-4 space-y-2">
      <div className="text-[12.5px] text-gray-400">Datasets you downloaded with Research → Data Downloader. Stage one to map, validate and merge it like an upload.</div>
      {msg && <div className="text-[12px] text-red-400">{msg}</div>}
      {!rows.length ? <div className="text-[12.5px] text-gray-500 py-4">No downloaded datasets yet.</div> : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-[12px] whitespace-nowrap">
            <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
              {['Symbol', 'Type', 'Interval', 'From → to', 'Rows', 'Suggested', ''].map((h) => <th key={h} className="text-left px-2 py-1 font-medium">{h}</th>)}
            </tr></thead>
            <tbody>{rows.map((d) => (
              <tr key={d.id} className="border-b border-surface-3/40">
                <td className="px-2 py-1.5 text-gray-200">{d.symbol} <span className="text-gray-500">{d.exchange}</span></td>
                <td className="px-2 py-1.5 text-gray-400">{d.instrument_type}{d.option_type ? ` ${d.option_type}` : ''}</td>
                <td className="px-2 py-1.5 text-gray-400">{d.interval}</td>
                <td className="px-2 py-1.5 mono text-gray-400">{d.from_date} → {d.to_date}</td>
                <td className="px-2 py-1.5 mono">{Number(d.rows || 0).toLocaleString('en-IN')}</td>
                <td className="px-2 py-1.5 text-gray-400">{d.suggested_underlying || '?'} · {d.suggested_kind}</td>
                <td className="px-2 py-1.5 text-right">
                  {d.file_exists ? (
                    <button onClick={() => stage(d.id)} disabled={busy === d.id} className="btn-secondary !py-1 !px-2.5 text-[12px] disabled:opacity-50">{busy === d.id ? 'Staging…' : 'Stage'}</button>
                  ) : <span className="text-[11px] text-gray-500">file not on this server</span>}
                </td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function DataIngestion() {
  const [tab, setTab] = useState('upload');
  const [staged, setStaged] = useState([]);
  const [errors, setErrors] = useState([]);
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const [coverage, setCoverage] = useState(null);
  const fileRef = useRef(null);

  const loadCoverage = useCallback(() => {
    api.diCoverage().then((r) => { if (r.status === 'ok') setCoverage(r); }).catch(() => {});
  }, []);
  useEffect(() => { loadCoverage(); }, [loadCoverage]);

  const addStaged = (files) => {
    setErrors((e) => [...e, ...files.filter((f) => f.status !== 'ok')]);
    setStaged((s) => [...files.filter((f) => f.status === 'ok'), ...s]);
    setTab('upload');
  };

  const upload = async (files) => {
    if (!files?.length) return;
    setBusy(true); setErrors([]);
    try {
      const r = await api.diStage(files);
      if (r.status === 'ok') addStaged(r.files); else setErrors([{ filename: '', message: r.message }]);
    } catch (e) { setErrors([{ filename: '', message: String(e.message || e) }]); } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };
  const remove = async (id) => { await api.diDropStage(id).catch(() => {}); setStaged((s) => s.filter((f) => f.stage_id !== id)); };

  return (
    <div className="p-4 sm:p-6 space-y-4 max-w-[1400px] mx-auto">
      <div>
        <h1 className="text-xl sm:text-2xl font-bold text-white">Data Ingestion Lab</h1>
        <p className="text-xs sm:text-sm text-gray-500 mt-0.5">
          Index and option history for NIFTY, SENSEX and any other underlying → one shared Market Store that feeds the OI Lab, Signal Desk and Options Lab
        </p>
      </div>

      <div className="flex gap-1 border-b border-surface-3 overflow-x-auto">
        {TABS.map(([id, label, Icon]) => (
          <button key={id} onClick={() => { setTab(id); if (id !== 'upload') loadCoverage(); }}
            className={`flex items-center gap-1.5 px-4 py-2 text-sm font-semibold border-b-2 -mb-px transition whitespace-nowrap ${tab === id ? 'border-brand-500 text-brand-400' : 'border-transparent text-gray-400 hover:text-gray-200'}`}>
            <Icon className="w-4 h-4" />{label}
          </button>
        ))}
      </div>

      {tab === 'upload' && (
        <div className="space-y-4">
          <div
            onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files); }}
            onClick={() => fileRef.current?.click()} role="button" tabIndex={0}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') fileRef.current?.click(); }}
            className={`card cursor-pointer text-center py-8 border-2 border-dashed transition ${drag ? 'border-brand-500 bg-brand-600/5' : 'border-surface-3 hover:border-brand-500/40'}`}>
            <input ref={fileRef} type="file" multiple accept=".csv,.gz,.parquet,.pq" className="hidden" onChange={(e) => upload(e.target.files)} />
            {busy ? <Loader2 className="w-8 h-8 text-brand-400 mx-auto animate-spin" /> : <UploadCloud className="w-8 h-8 text-brand-400 mx-auto" />}
            <div className="text-[14px] font-semibold text-gray-100 mt-2">{busy ? 'Uploading…' : 'Drop CSV or Parquet files, or click to choose'}</div>
            <div className="text-[12px] text-gray-500 mt-1">Index bars (timestamp, OHLC) or option bars (timestamp, OHLC, OI + strike/type/expiry or a trading symbol) · 1- or 5-minute · up to 1 GB each</div>
          </div>
          {errors.map((e, i) => <div key={i} className="text-[12px] text-red-400">{e.filename ? `${e.filename}: ` : ''}{e.message}</div>)}
          {staged.map((f) => <StagedFile key={f.stage_id} file={f} onRemove={remove} onCommitted={loadCoverage} />)}
          <div className="card !p-4 text-[12px] text-gray-400 space-y-1.5">
            <div className="flex items-center gap-1.5 text-gray-200 font-semibold"><Info className="w-4 h-4 text-brand-400" />What the OI Lab needs per index</div>
            <div>• <span className="text-gray-200">Option bars</span> for the strikes around ATM (±5 or wider) with <span className="text-gray-200">open interest</span> — nearest weekly expiry at minimum.</div>
            <div>• <span className="text-gray-200">Index bars</span> for the same days (for accurate highs/lows); a spot column inside the option file works as a fallback.</div>
            <div>• IV is optional — it is computed when missing. Symbols like <span className="mono">SENSEX2590874600CE</span> or <span className="mono">NIFTY25SEP25000CE</span> are parsed automatically.</div>
            <div>• Upload daily, weekly or monthly — overlapping uploads merge. The {`index's`} OI study and Signal Desk backtest rebuild on their own after each merge; models need at least 40 sessions.</div>
          </div>
        </div>
      )}
      {tab === 'coverage' && <Coverage data={coverage} reload={loadCoverage} />}
      {tab === 'explore' && <Explorer coverage={coverage} />}
      {tab === 'imports' && <Imports onStaged={addStaged} />}
    </div>
  );
}
