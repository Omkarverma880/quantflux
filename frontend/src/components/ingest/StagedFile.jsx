import React, { useEffect, useRef, useState } from 'react';
import { FileText, CheckCircle2, AlertTriangle, Trash2, ShieldCheck, Database, ChevronDown, Loader2 } from 'lucide-react';
import { api } from '../../api';

/**
 * One staged file: what was detected, the column mapping (editable), a dry-run validation and
 * the commit into the Market Store. Validate/commit run server-side as jobs and are polled.
 */

const N = (v) => (v === null || v === undefined ? '—' : Number(v).toLocaleString('en-IN'));
const MB = (b) => (b ? `${(b / 1e6).toFixed(1)} MB` : '');
const LABEL = {
  timestamp: 'Timestamp', time_part: 'Time (if separate)', open: 'Open', high: 'High', low: 'Low', close: 'Close',
  volume: 'Volume', oi: 'Open interest', iv: 'IV', spot: 'Spot / underlying price', strike: 'Strike',
  option_type: 'CE / PE', expiry_date: 'Expiry', symbol: 'Trading symbol', underlying: 'Underlying name',
};

function useJob(onDone) {
  const [job, setJob] = useState(null);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  const start = async (promise) => {
    const r = await promise;
    if (r.status !== 'ok') { setJob({ status: 'error', error: r.message }); return; }
    const poll = async (id) => {
      const j = await api.diJob(id);
      if (j.status !== 'ok') { setJob({ status: 'error', error: j.message }); return; }
      setJob(j.job);
      if (j.job.status === 'running') timer.current = setTimeout(() => poll(id), 1200);
      else onDone?.(j.job);
    };
    setJob(r.job);
    poll(r.job.id);
  };
  return [job, start];
}

function Report({ v }) {
  const r = v.report || {};
  const dropped = r.rows_in - r.rows_out;
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[12px]">
        {[
          ['Rows read', N(r.rows_in)], ['Rows kept', N(r.rows_out)],
          ['New to the store', N(v.rows_new)], ['Already stored', N(v.rows_already_stored)],
          ['Sessions', N(v.sessions)], ['Bar size', v.bar_minutes ? `${v.bar_minutes} min` : '—'],
          [v.kind === 'options' ? 'Contracts' : 'Kind', v.kind === 'options' ? N(v.contracts) : 'index / spot'],
          [v.kind === 'options' ? 'Expiries' : 'Range', v.kind === 'options' ? N(v.expiries) : `${(v.first || '').slice(0, 10)} → ${(v.last || '').slice(0, 10)}`],
        ].map(([k, val]) => (
          <div key={k} className="rounded-lg bg-surface-2 border border-surface-3 px-2.5 py-1.5">
            <div className="text-[10px] uppercase tracking-wider text-gray-500">{k}</div>
            <div className="mono text-gray-100">{val}</div>
          </div>
        ))}
      </div>
      <div className="text-[11.5px] text-gray-400">
        {v.first} → {v.last}
        {v.strike_range && <> · strikes {N(v.strike_range[0])}–{N(v.strike_range[1])}</>}
        {v.nearest_expiries?.length > 0 && <> · expiries {v.nearest_expiries.join(', ')}{v.expiries > v.nearest_expiries.length ? '…' : ''}</>}
      </div>
      {dropped > 0 && (
        <div className="text-[11.5px] text-gray-400">
          Dropped {N(dropped)}: {N(r.bad_ohlc)} bad OHLC · {N(r.outside_session)} outside 09:15–15:30 · {N(r.duplicates_merged)} duplicate bars merged
          {r.conflicting_duplicates ? ` (${N(r.conflicting_duplicates)} with different prices — the last one wins)` : ''}
        </div>
      )}
      {v.months?.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {v.months.map((m) => <span key={m.month} className="text-[10.5px] px-1.5 py-0.5 rounded bg-surface-3/60 text-gray-300 mono">{m.month}: {N(m.rows)}</span>)}
        </div>
      )}
      {v.rows_already_stored > 0 && <div className="text-[11.5px] text-gray-500">Rows already stored are replaced by this file's values (same timestamp and contract); nothing is duplicated.</div>}
      {(v.notes || []).map((n, i) => <div key={i} className="text-[11.5px] text-gray-400 flex gap-1.5"><CheckCircle2 className="w-3.5 h-3.5 text-brand-400 shrink-0 mt-0.5" />{n}</div>)}
      {(v.warnings || []).map((n, i) => <div key={i} className="text-[11.5px] text-amber-500 flex gap-1.5"><AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />{n}</div>)}
    </div>
  );
}

export default function StagedFile({ file, onRemove, onCommitted }) {
  const [kind, setKind] = useState(file.guess?.kind || 'spot');
  const [und, setUnd] = useState(file.guess?.underlying || '');
  const [mapping, setMapping] = useState(file.guess?.mapping || {});
  const [showMap, setShowMap] = useState(!!file.missing_required?.length);
  const [validated, setValidated] = useState(null);
  const [vJob, startValidate] = useJob((j) => { if (j.status === 'done') setValidated(j.result); });
  const [cJob, startCommit] = useJob((j) => { if (j.status === 'done') onCommitted?.(j.result); });

  const fields = (file.fields?.[kind] || []).concat(mapping.time_part ? ['time_part'] : []);
  const required = file.required?.[kind] || [];
  const viaSymbol = kind === 'options' && mapping.symbol;
  const missing = required.filter((f) => !mapping[f] && !(viaSymbol && ['strike', 'option_type', 'expiry_date'].includes(f)));
  const body = { stage_id: file.stage_id, kind, underlying: und.trim().toUpperCase(), mapping };
  const setMap = (f, col) => { setMapping((m) => { const n = { ...m }; if (col) n[f] = col; else delete n[f]; return n; }); setValidated(null); };
  const running = vJob?.status === 'running' || cJob?.status === 'running';
  const committed = cJob?.status === 'done';

  return (
    <div className={`card !p-4 space-y-3 ${committed ? 'border-green-500/40' : ''}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex items-start gap-2 min-w-0">
          <FileText className="w-5 h-5 text-brand-400 shrink-0 mt-0.5" />
          <div className="min-w-0">
            <div className="text-[13.5px] font-semibold text-gray-100 truncate">{file.filename}</div>
            <div className="text-[11.5px] text-gray-500">
              {MB(file.bytes)}{file.rows_estimate ? ` · ≈${N(file.rows_estimate)} rows` : ''} · {file.bar_minutes ? `${file.bar_minutes}-min bars` : 'bar size unknown'} · {file.first?.slice(0, 16)} → {file.last?.slice(0, 16)} (first rows)
            </div>
          </div>
        </div>
        {!committed && <button onClick={() => onRemove(file.stage_id)} className="btn-ghost !p-1.5" title="Discard" aria-label="Discard"><Trash2 className="w-4 h-4" /></button>}
      </div>

      <div className="grid sm:grid-cols-[auto_auto_1fr] gap-3 items-end">
        <label className="block">
          <span className="text-[10.5px] uppercase tracking-wider text-gray-500">What is it</span>
          <select value={kind} onChange={(e) => { setKind(e.target.value); setValidated(null); }} className="input-field !py-1.5 block mt-0.5" disabled={committed}>
            <option value="spot">Index / spot bars</option><option value="options">Option contract bars</option>
          </select>
        </label>
        <label className="block">
          <span className="text-[10.5px] uppercase tracking-wider text-gray-500">Underlying</span>
          <input list={`und-${file.stage_id}`} value={und} onChange={(e) => { setUnd(e.target.value.toUpperCase()); setValidated(null); }}
            placeholder="NIFTY, SENSEX…" className="input-field !py-1.5 block mt-0.5 w-40" disabled={committed} />
          <datalist id={`und-${file.stage_id}`}>{(file.known_underlyings || []).map((u) => <option key={u} value={u} />)}</datalist>
        </label>
        <div className="text-[11.5px] text-gray-400">
          {missing.length ? <span className="text-amber-500">Map required: {missing.map((f) => LABEL[f] || f).join(', ')}</span>
            : <span className="text-green-400">All required fields mapped{viaSymbol && !mapping.strike ? ' (strike / type / expiry read from the symbol)' : ''}.</span>}
        </div>
      </div>

      {(file.warnings || []).map((w, i) => <div key={i} className="text-[11.5px] text-amber-500 flex gap-1.5"><AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />{w}</div>)}

      <button onClick={() => setShowMap((s) => !s)} className="text-[12px] text-gray-300 flex items-center gap-1.5">
        <ChevronDown className={`w-4 h-4 transition ${showMap ? 'rotate-180' : ''}`} />Column mapping and sample rows
      </button>
      {showMap && (
        <div className="grid lg:grid-cols-[minmax(0,340px)_1fr] gap-3">
          <div className="space-y-1">
            {fields.map((f) => (
              <div key={f} className="grid grid-cols-[130px_1fr] items-center gap-2">
                <span className={`text-[11.5px] ${required.includes(f) ? 'text-gray-200' : 'text-gray-500'}`}>{LABEL[f] || f}{required.includes(f) ? ' *' : ''}</span>
                <select value={mapping[f] || ''} onChange={(e) => setMap(f, e.target.value)} className="input-field !py-1 !text-[12px]" disabled={committed}>
                  <option value="">— not in file —</option>
                  {file.columns.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
                </select>
              </div>
            ))}
          </div>
          <div className="overflow-x-auto rounded-lg border border-surface-3">
            <table className="text-[11px] whitespace-nowrap">
              <thead><tr className="bg-surface-2">{file.columns.map((c) => <th key={c.name} className="px-2 py-1 text-left font-medium text-gray-400">{c.name}<div className="text-[9.5px] text-gray-600">{c.dtype}</div></th>)}</tr></thead>
              <tbody>{(file.sample || []).slice(0, 8).map((row, i) => (
                <tr key={i} className="border-t border-surface-3/50">{file.columns.map((c) => <td key={c.name} className="px-2 py-0.5 mono text-gray-300">{String(row[c.name] ?? '')}</td>)}</tr>
              ))}</tbody>
            </table>
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <button disabled={running || committed || missing.length > 0 || !body.underlying}
          onClick={() => { setValidated(null); startValidate(api.diValidate(body)); }}
          className="btn-secondary !py-1.5 text-[12.5px] flex items-center gap-1.5 disabled:opacity-50">
          {vJob?.status === 'running' ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldCheck className="w-4 h-4" />}Validate (dry run)
        </button>
        <button disabled={running || committed || !validated || validated.report?.rows_out === 0}
          onClick={() => startCommit(api.diCommit(body))}
          className="btn-primary !py-1.5 text-[12.5px] flex items-center gap-1.5 disabled:opacity-50">
          {cJob?.status === 'running' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Database className="w-4 h-4" />}Merge into Market Store
        </button>
        {running && <span className="text-[11.5px] text-gray-400">{(cJob?.status === 'running' ? cJob : vJob).progress}…</span>}
        {!validated && !running && !committed && <span className="text-[11.5px] text-gray-500">Validate first — nothing is written until you merge.</span>}
      </div>
      {vJob?.status === 'error' && <div className="text-[12px] text-red-400">{vJob.error}</div>}
      {validated && !committed && <Report v={validated} />}
      {cJob?.status === 'error' && <div className="text-[12px] text-red-400">Merge failed: {cJob.error}</div>}
      {committed && (
        <div className="rounded-lg border border-green-500/30 bg-green-500/5 px-3 py-2 text-[12.5px] text-gray-200 space-y-1">
          <div className="flex items-center gap-1.5 text-green-400 font-semibold"><CheckCircle2 className="w-4 h-4" />Merged {N(cJob.result.rows_stored)} rows into {cJob.result.underlying} {cJob.result.kind} ({N(cJob.result.rows_added)} new) · {cJob.result.months.join(', ')}</div>
          {cJob.result.oi_study && <div className="text-gray-400">{cJob.result.oi_study}</div>}
          {cJob.result.saved_to_database === false && <div className="text-amber-500">Saved on this server's disk only — the database copy failed, so it would not survive a redeploy.</div>}
          {(cJob.result.warnings || []).map((w, i) => <div key={i} className="text-amber-500">{w}</div>)}
        </div>
      )}
    </div>
  );
}
