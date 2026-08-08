import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Database, Search, Download, Loader2, CheckCircle, XCircle, RefreshCw, Trash2,
  Eye, X, AlertCircle, Wifi, FileDown, Play, Info,
} from 'lucide-react';
import { api } from '../../api';

const sel = 'bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';
const lbl = 'block text-[11px] text-gray-500 uppercase tracking-wide mb-1';
const INTERVALS = [['1minute', '1 Minute'], ['3minute', '3 Minute'], ['5minute', '5 Minute'], ['10minute', '10 Minute'], ['15minute', '15 Minute'], ['30minute', '30 Minute'], ['60minute', '60 Minute'], ['day', 'Daily']];
const TYPES = [['all', 'All'], ['index', 'Index'], ['equity', 'Equity'], ['futures', 'Futures'], ['options', 'Options']];
const NUM = (v) => (v == null ? '—' : Number(v).toLocaleString('en-IN'));
const RUNNING = new Set(['queued', 'running']);
const statusColor = (s) => ({ completed: 'text-emerald-400', running: 'text-brand-400', queued: 'text-gray-400', partial: 'text-amber-400', failed: 'text-red-400', cancelled: 'text-gray-500' }[s] || 'text-gray-400');

function typeBadge(t) {
  const c = { index: 'bg-blue-500/15 text-blue-300 border-blue-500/30', equity: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30', futures: 'bg-amber-500/15 text-amber-300 border-amber-500/30', options: 'bg-purple-500/15 text-purple-300 border-purple-500/30' }[t] || 'bg-surface-3 text-gray-400 border-surface-4';
  return <span className={`text-[9px] px-1.5 py-0.5 rounded border ${c}`}>{t}</span>;
}

export default function DataDownloader() {
  const [tab, setTab] = useState('download');
  const [cfg, setCfg] = useState(null);
  const [err, setErr] = useState('');
  const showErr = (m) => { setErr(m); setTimeout(() => setErr(''), 6000); };

  // instrument selection
  const [itype, setItype] = useState('all');
  const [q, setQ] = useState('');
  const [sugg, setSugg] = useState([]);
  const [searching, setSearching] = useState(false);
  const [selected, setSelected] = useState(null);
  const searchTimer = useRef(null);

  // config
  const [interval, setInterval2] = useState('day');
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');
  const [includeOi, setIncludeOi] = useState(true);
  const [fmt, setFmt] = useState('parquet');

  // job + datasets
  const [job, setJob] = useState(null);
  const [datasets, setDatasets] = useState([]);
  const [viewing, setViewing] = useState(null);
  const pollRef = useRef(null);

  useEffect(() => {
    api.ddConfig().then((r) => { if (r.status === 'ok') { setCfg(r.config); setInterval2(r.config.default_interval); setFmt(r.config.default_format); setIncludeOi(r.config.include_oi_default); } }).catch(() => setCfg({}));
    const t = new Date(); const y = new Date(); y.setFullYear(t.getFullYear() - 1);
    setToDate(t.toISOString().slice(0, 10)); setFromDate(y.toISOString().slice(0, 10));
  }, []);

  // debounced instrument search
  useEffect(() => {
    if (searchTimer.current) clearTimeout(searchTimer.current);
    if (!q || q.trim().length < 2) { setSugg([]); return; }
    setSearching(true);
    searchTimer.current = setTimeout(async () => {
      try { const r = await api.ddSearch(q.trim(), itype, null); setSugg(r.status === 'ok' ? r.results : []); }
      catch { setSugg([]); } finally { setSearching(false); }
    }, 300);
    return () => searchTimer.current && clearTimeout(searchTimer.current);
  }, [q, itype]);

  const pickInstrument = (r) => { setSelected(r); setSugg([]); setQ(r.tradingsymbol || r.symbol || ''); };

  const startDownload = async () => {
    if (!selected) return showErr('Select an instrument first');
    if (!fromDate || !toDate) return showErr('Pick a date range');
    const spec = {
      instrument_token: selected.instrument_token, symbol: selected.tradingsymbol || selected.symbol,
      exchange: selected.exchange, segment: selected.segment, instrument_type: selected.instrument_type,
      expiry: selected.expiry || null, strike: selected.strike || null, option_type: selected.option_type || null,
      interval, from_date: fromDate, to_date: toDate, include_oi: includeOi, fmt, normalize: true,
    };
    try {
      const r = await api.ddDownload(spec);
      if (r.status === 'ok') { setJob(r.dataset); pollJob(r.job_id); }
      else showErr(r.message || 'Download failed to start');
    } catch (e) { showErr(e.message); }
  };

  const pollJob = useCallback((id) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = window.setInterval(async () => {
      try {
        const r = await api.ddJob(id);
        if (r.status === 'ok') {
          setJob(r);
          if (!RUNNING.has(r.status)) { clearInterval(pollRef.current); pollRef.current = null; loadDatasets(); }
        }
      } catch { /* keep polling */ }
    }, 1500);
  }, []);
  useEffect(() => () => pollRef.current && clearInterval(pollRef.current), []);

  const loadDatasets = useCallback(async () => {
    try { const r = await api.ddDatasets(); if (r.status === 'ok') setDatasets(r.datasets || []); } catch { /* noop */ }
  }, []);
  useEffect(() => { if (tab === 'datasets') loadDatasets(); }, [tab, loadDatasets]);
  // auto-refresh datasets while any is running
  useEffect(() => {
    if (tab !== 'datasets') return;
    if (!datasets.some((d) => RUNNING.has(d.status))) return;
    const t = window.setInterval(loadDatasets, 2500);
    return () => clearInterval(t);
  }, [tab, datasets, loadDatasets]);

  const resume = async (id) => { const r = await api.ddResume(id); if (r.status === 'ok') { loadDatasets(); if (tab === 'download' && job?.id === id) pollJob(id); } else showErr(r.message); };
  const cancel = async (id) => { await api.ddCancel(id); loadDatasets(); };
  const del = async (id) => { if (!window.confirm('Delete this dataset and its file?')) return; const r = await api.ddDatasetDelete(id); if (r.status === 'ok') { loadDatasets(); if (job?.id === id) setJob(null); } else showErr(r.message); };
  const view = async (id) => { const r = await api.ddDataset(id); if (r.status === 'ok') setViewing(r); else showErr(r.message); };
  const dl = async (d, fmtSel) => { try { await api.ddDownloadFile(d.id, fmtSel, `${(d.symbol || 'data').replace(/\s+/g, '')}_${d.interval}_${d.from_date}_${d.to_date}.${fmtSel === 'csv' ? 'csv' : d.file_format}`); } catch (e) { showErr(e.message); } };

  if (!cfg) return <div className="p-6 text-gray-500 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>;

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-[1400px] mx-auto">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Database className="w-5 h-5 text-brand-400" />
            <h1 className="text-xl font-bold text-gray-100 tracking-wide">Data Downloader</h1>
            <span className="px-2 py-0.5 rounded-full bg-brand-600/15 text-brand-400 text-xs font-semibold border border-brand-500/20">Read-only · No orders</span>
          </div>
          <p className="text-gray-500 text-sm mt-0.5">Download &amp; manage historical OHLCV / OI datasets via your existing Zerodha connection — for ML &amp; backtesting research.</p>
        </div>
        <div className="flex items-center gap-1.5 text-xs text-emerald-400"><Wifi className="w-4 h-4" /> Zerodha / Kite · Connected</div>
      </div>

      <div className="flex gap-1 border-b border-surface-3">
        {[['download', 'Download', Download], ['datasets', 'My Datasets', Database]].map(([id, label, Icon]) => (
          <button key={id} onClick={() => setTab(id)} className={`flex items-center gap-1.5 px-4 py-2 text-sm font-semibold border-b-2 -mb-px transition ${tab === id ? 'border-brand-500 text-brand-400' : 'border-transparent text-gray-400 hover:text-gray-200'}`}><Icon className="w-4 h-4" /> {label}</button>
        ))}
      </div>

      {err && <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-red-400 text-sm"><AlertCircle className="w-4 h-4" /> {err}</div>}

      {tab === 'download' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Instrument selection */}
          <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
            <div className="text-sm font-semibold text-gray-200">Instrument Selection</div>
            <div className="flex gap-2 flex-wrap">
              {TYPES.map(([v, l]) => <button key={v} onClick={() => setItype(v)} className={`px-2.5 py-1 text-xs rounded-lg border ${itype === v ? 'bg-brand-600/20 text-brand-400 border-brand-500/40' : 'bg-surface-3 text-gray-400 border-surface-4'}`}>{l}</button>)}
            </div>
            <div className="relative">
              <div className="flex items-center gap-2 bg-surface-3 border border-surface-4 rounded-lg px-3">
                <Search className="w-4 h-4 text-gray-500" />
                <input value={q} onChange={(e) => { setQ(e.target.value); setSelected(null); }} placeholder="Search NIFTY / RELIANCE / HDFCBANK / RELIANCE 2900 CE …" className="flex-1 bg-transparent py-2 text-sm text-gray-200 focus:outline-none" />
                {searching && <Loader2 className="w-4 h-4 animate-spin text-gray-500" />}
              </div>
              {sugg.length > 0 && (
                <div className="absolute z-20 mt-1 w-full max-h-72 overflow-auto bg-surface-2 border border-surface-3 rounded-lg shadow-2xl">
                  {sugg.map((r) => (
                    <button key={r.instrument_token} onClick={() => pickInstrument(r)} className="w-full text-left px-3 py-2 hover:bg-surface-3/40 flex items-center gap-2 border-b border-surface-3/40 last:border-0">
                      <span className="text-sm text-gray-100 font-medium">{r.tradingsymbol}</span>
                      {typeBadge(r.instrument_type)}
                      <span className="text-[11px] text-gray-500 ml-auto">{r.exchange} · {r.instrument_token}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            {selected && (
              <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs bg-surface-3/40 rounded-lg p-3">
                {[['Symbol', selected.tradingsymbol], ['Exchange', selected.exchange], ['Token', selected.instrument_token], ['Segment', selected.segment], ['Type', selected.instrument_type], ['Expiry', selected.expiry || '—'], ['Strike', selected.strike || '—'], ['Option', selected.option_type || '—']].map(([k, v]) => (
                  <React.Fragment key={k}><span className="text-gray-500">{k}</span><span className="text-right text-gray-200">{v ?? '—'}</span></React.Fragment>
                ))}
              </div>
            )}
          </div>

          {/* Data configuration */}
          <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
            <div className="text-sm font-semibold text-gray-200">Data Configuration</div>
            <div className="grid grid-cols-2 gap-3">
              <div><label className={lbl}>Timeframe</label><select value={interval} onChange={(e) => setInterval2(e.target.value)} className={`w-full ${sel}`}>{INTERVALS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
              <div><label className={lbl}>Timezone</label><input value="Asia/Kolkata" disabled className={`w-full ${sel} opacity-60`} /></div>
              <div><label className={lbl}>From</label><input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} className={`w-full ${sel}`} /></div>
              <div><label className={lbl}>To</label><input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} className={`w-full ${sel}`} /></div>
            </div>
            <div className="flex flex-wrap gap-4 text-xs text-gray-300 pt-1">
              <label className="flex items-center gap-1.5 cursor-pointer"><input type="checkbox" checked={includeOi} onChange={(e) => setIncludeOi(e.target.checked)} className="accent-brand-500" /> Include Open Interest</label>
              <label className="flex items-center gap-1.5 cursor-pointer"><input type="radio" name="fmt" checked={fmt === 'parquet'} onChange={() => setFmt('parquet')} className="accent-brand-500" /> Parquet</label>
              <label className="flex items-center gap-1.5 cursor-pointer"><input type="radio" name="fmt" checked={fmt === 'csv'} onChange={() => setFmt('csv')} className="accent-brand-500" /> CSV</label>
            </div>
            <button onClick={startDownload} disabled={!selected || (job && RUNNING.has(job.status))} className="w-full flex items-center justify-center gap-2 px-4 py-2 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">
              {job && RUNNING.has(job.status) ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />} Download Data
            </button>
            <p className="text-[11px] text-gray-600 flex items-center gap-1"><Info className="w-3 h-3" /> Large ranges are auto-chunked to respect Kite limits; a failed chunk can be resumed from My Datasets.</p>
          </div>

          {/* Progress / result */}
          {job && (
            <div className="lg:col-span-2 bg-surface-2 border border-surface-3 rounded-xl p-4">
              {RUNNING.has(job.status) ? (
                <>
                  <div className="flex items-center justify-between mb-2 text-sm">
                    <span className="text-gray-300 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin text-brand-400" /> Downloading {job.symbol} · {job.interval}</span>
                    <button onClick={() => cancel(job.id)} className="text-xs text-gray-400 hover:text-red-400">Cancel</button>
                  </div>
                  <div className="h-3 rounded-full bg-surface-3 overflow-hidden"><div className="h-full bg-brand-500 transition-all" style={{ width: `${job.progress || 0}%` }} /></div>
                  <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs text-gray-500 mt-2">
                    <span>{job.progress || 0}%</span>
                    <span>Rows <strong className="text-gray-300">{NUM(job.rows)}</strong></span>
                    <span>Chunks <strong className="text-gray-300">{job.chunks_completed} / {job.chunks_total}</strong></span>
                    <span className={statusColor(job.status)}>{job.status}</span>
                  </div>
                </>
              ) : (
                <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
                  {job.status === 'completed' ? <CheckCircle className="w-5 h-5 text-emerald-400" /> : job.status === 'partial' ? <AlertCircle className="w-5 h-5 text-amber-400" /> : <XCircle className="w-5 h-5 text-red-400" />}
                  <span className={`font-semibold ${statusColor(job.status)}`}>{job.status === 'completed' ? 'Download completed' : job.status === 'partial' ? 'Partial — resume to finish' : job.status === 'cancelled' ? 'Cancelled' : 'Failed'}</span>
                  <span className="text-gray-400">{job.symbol} · {job.interval} · {job.from_date} → {job.to_date}</span>
                  <span className="text-gray-400">Rows <strong className="text-gray-200">{NUM(job.rows)}</strong></span>
                  {job.quality?.status && <span className={job.quality.status === 'valid' ? 'text-emerald-400' : 'text-amber-400'}>{job.quality.status === 'valid' ? '✓ dataset valid' : '⚠ has warnings'}</span>}
                  {job.error && <span className="text-red-400 text-xs">{job.error}</span>}
                  <div className="ml-auto flex items-center gap-2">
                    {job.has_file && <button onClick={() => view(job.id)} className="text-xs text-brand-400 hover:text-brand-300 flex items-center gap-1"><Eye className="w-3.5 h-3.5" /> View</button>}
                    {job.has_file && <button onClick={() => dl(job, 'native')} className="text-xs text-gray-300 hover:text-white flex items-center gap-1"><FileDown className="w-3.5 h-3.5" /> File</button>}
                    {job.has_file && <button onClick={() => dl(job, 'csv')} className="text-xs text-gray-300 hover:text-white flex items-center gap-1"><FileDown className="w-3.5 h-3.5" /> CSV</button>}
                    {(job.status === 'partial' || job.status === 'failed') && <button onClick={() => resume(job.id)} className="text-xs text-amber-400 hover:text-amber-300 flex items-center gap-1"><Play className="w-3.5 h-3.5" /> Resume</button>}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {tab === 'datasets' && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
          <div className="flex items-center gap-3 px-3 py-2 border-b border-surface-3">
            <span className="text-sm font-semibold text-gray-200">My Datasets <span className="text-gray-500">({datasets.length})</span></span>
            <button onClick={loadDatasets} className="text-xs text-gray-400 hover:text-white flex items-center gap-1"><RefreshCw className="w-3.5 h-3.5" /> Refresh</button>
          </div>
          {!datasets.length ? <div className="px-4 py-10 text-center text-gray-500 text-sm">No datasets yet — download one from the Download tab.</div> : (
            <div className="overflow-x-auto"><table className="w-full text-xs whitespace-nowrap">
              <thead className="bg-surface-3 text-gray-300"><tr>{['Symbol', 'Type', 'Interval', 'From', 'To', 'Rows', 'Progress', 'Status', 'Actions'].map((h) => <th key={h} className="px-2.5 py-2 font-semibold text-left">{h}</th>)}</tr></thead>
              <tbody>{datasets.map((d) => (
                <tr key={d.id} className="border-t border-surface-3/40 hover:bg-surface-3/10">
                  <td className="px-2.5 py-1.5 text-gray-100 font-medium">{d.symbol}</td>
                  <td className="px-2.5 py-1.5">{typeBadge(d.instrument_type)}</td>
                  <td className="px-2.5 py-1.5 text-gray-300">{d.interval}</td>
                  <td className="px-2.5 py-1.5 text-gray-400">{d.from_date}</td>
                  <td className="px-2.5 py-1.5 text-gray-400">{d.to_date}</td>
                  <td className="px-2.5 py-1.5 text-gray-200">{NUM(d.rows)}</td>
                  <td className="px-2.5 py-1.5 text-gray-400">{d.chunks_completed}/{d.chunks_total}</td>
                  <td className={`px-2.5 py-1.5 font-semibold ${statusColor(d.status)}`}>{d.status}</td>
                  <td className="px-2.5 py-1.5">
                    <div className="flex items-center gap-2">
                      {d.has_file && <button onClick={() => view(d.id)} title="View" className="text-brand-400 hover:text-brand-300"><Eye className="w-4 h-4" /></button>}
                      {d.has_file && <button onClick={() => dl(d, 'native')} title="Download file" className="text-gray-400 hover:text-white"><FileDown className="w-4 h-4" /></button>}
                      {d.has_file && <button onClick={() => dl(d, 'csv')} title="Download CSV" className="text-gray-400 hover:text-white text-[10px] font-bold">CSV</button>}
                      {(d.status === 'partial' || d.status === 'failed') && <button onClick={() => resume(d.id)} title="Resume" className="text-amber-400 hover:text-amber-300"><Play className="w-4 h-4" /></button>}
                      {RUNNING.has(d.status) && <button onClick={() => cancel(d.id)} title="Cancel" className="text-gray-400 hover:text-red-400"><X className="w-4 h-4" /></button>}
                      <button onClick={() => del(d.id)} title="Delete" className="text-gray-500 hover:text-red-400"><Trash2 className="w-4 h-4" /></button>
                    </div>
                  </td>
                </tr>
              ))}</tbody>
            </table></div>
          )}
        </div>
      )}

      {viewing && <ViewModal d={viewing} onClose={() => setViewing(null)} />}

      <p className="text-[11px] text-gray-600 flex items-center gap-1"><Info className="w-3 h-3" /> All Zerodha calls run on the backend (no secrets in the browser). Datasets are stored under the app data directory; on Railway the filesystem is ephemeral, so re-download or export to keep long-term copies.</p>
    </div>
  );
}

function ViewModal({ d, onClose }) {
  const rows = d.sample || [];
  const cols = rows.length ? Object.keys(rows[0]) : [];
  const q = d.quality || {};
  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-surface-1 border border-surface-3 rounded-xl w-full max-w-5xl max-h-[85vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-surface-3">
          <div className="text-sm font-bold text-gray-100">{d.symbol} · {d.interval} · {d.from_date} → {d.to_date}</div>
          <button onClick={onClose} className="text-gray-400 hover:text-white"><X className="w-5 h-5" /></button>
        </div>
        <div className="px-4 py-2 border-b border-surface-3 flex flex-wrap gap-x-6 gap-y-1 text-xs">
          <span className="text-gray-500">Rows <strong className="text-gray-200">{NUM(q.rows ?? d.rows)}</strong></span>
          <span className="text-gray-500">Duplicates <strong className="text-gray-200">{q.duplicates ?? 0}</strong></span>
          <span className="text-gray-500">Missing OHLC <strong className="text-gray-200">{q.missing_ohlc ?? 0}</strong></span>
          <span className="text-gray-500">Invalid <strong className="text-gray-200">{q.invalid ?? 0}</strong></span>
          <span className="text-gray-500">Missing intervals <strong className="text-gray-200">{q.missing_intervals ?? 0}</strong></span>
          <span className={q.status === 'valid' ? 'text-emerald-400 font-semibold' : 'text-amber-400 font-semibold'}>{q.status === 'valid' ? '✓ DATASET VALID' : '⚠ WARNINGS'}</span>
          <span className="text-gray-600 ml-auto">{d.file_format} · {d.checksum ? d.checksum.slice(0, 10) : ''}</span>
        </div>
        <div className="overflow-auto flex-1">
          {rows.length ? (
            <table className="w-full text-[11px] whitespace-nowrap">
              <thead className="bg-surface-3 text-gray-400 sticky top-0"><tr>{cols.map((c) => <th key={c} className="px-2 py-1.5 text-right first:text-left font-semibold">{c}</th>)}</tr></thead>
              <tbody>{rows.map((r, i) => <tr key={i} className="border-t border-surface-3/30">{cols.map((c) => <td key={c} className="px-2 py-1 text-right first:text-left text-gray-300">{r[c] == null ? '' : String(r[c])}</td>)}</tr>)}</tbody>
            </table>
          ) : <div className="px-4 py-10 text-center text-gray-500 text-sm">No preview rows.</div>}
        </div>
      </div>
    </div>
  );
}
