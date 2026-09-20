import React, { useCallback, useEffect, useRef, useState } from 'react';
import { RefreshCw, Loader2, CheckCircle2, AlertTriangle, Info } from 'lucide-react';
import { api } from '../../api';

/**
 * One click pulls everything Zerodha still lists — index bars, India VIX, futures and the option
 * chain around ATM with OI — straight into the Market Store the OI Lab reads. Expired contracts
 * are no longer listed, so those still come from file uploads.
 */

const INDICES = ['NIFTY', 'SENSEX', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'BANKEX'];
const KEY = 'qf.ingest.pull.cfg';
const DEFAULT_CFG = { indices: ['NIFTY', 'SENSEX'], days: 5, strikes: 10, expiries: 1, include_index: true, include_vix: true, include_futures: true };

const num = (n) => Number(n || 0).toLocaleString('en-IN');

function readCfg() {
  try { return { ...DEFAULT_CFG, ...JSON.parse(localStorage.getItem(KEY) || '{}') }; } catch { return { ...DEFAULT_CFG }; }
}

function ago(iso) {
  if (!iso) return null;
  const t = new Date(String(iso).replace(' ', 'T'));
  const mins = Math.round((Date.now() - t.getTime()) / 60000);
  if (!Number.isFinite(mins)) return String(iso);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  if (mins < 1440) return `${Math.round(mins / 60)} h ago`;
  return `${Math.round(mins / 1440)} d ago`;
}

function Field({ label, hint, children }) {
  return (
    <label className="block">
      <div className="text-[10px] uppercase tracking-wider text-gray-500">{label}</div>
      {children}
      {hint && <div className="text-[10.5px] text-gray-500 mt-0.5">{hint}</div>}
    </label>
  );
}

export default function PullPanel({ startSignal = 0, onDone }) {
  const [cfg, setCfg] = useState(readCfg);
  const [plan, setPlan] = useState(null);
  const [planning, setPlanning] = useState(false);
  const [job, setJob] = useState(null);
  const [result, setResult] = useState(null);
  const [last, setLast] = useState(null);
  const [err, setErr] = useState('');
  const timer = useRef(null);
  const running = job?.status === 'running';

  const set = (patch) => setCfg((c) => {
    const next = { ...c, ...patch };
    try { localStorage.setItem(KEY, JSON.stringify(next)); } catch { /* private window */ }
    return next;
  });

  const loadPlan = useCallback(async (c) => {
    setPlanning(true); setErr('');
    try {
      const r = await api.diPullPlan({ indices: c.indices.join(','), days: c.days, strikes: c.strikes, expiries: c.expiries });
      setPlan(r);
      if (r.last_pull) setLast(r.last_pull);
      if (r.status !== 'ok') setErr(r.message || '');
    } catch (e) { setErr(String(e.message || e)); } finally { setPlanning(false); }
  }, []);

  useEffect(() => { loadPlan(cfg); }, [cfg, loadPlan]);
  useEffect(() => {
    api.diPullLast().then((r) => { if (r.last_pull) setLast(r.last_pull); }).catch(() => {});
    return () => clearTimeout(timer.current);
  }, []);

  const poll = useCallback((id) => {
    timer.current = setTimeout(async () => {
      try {
        const r = await api.diJob(id);
        if (r.status !== 'ok') { setErr(r.message); return; }
        setJob(r.job);
        if (r.job.status === 'running') poll(id);
        else {
          setResult(r.job.result || null);
          if (r.job.error) setErr(r.job.error);
          if (r.job.result?.status === 'ok') { setLast(r.job.result); onDone?.(); }
        }
      } catch (e) { setErr(String(e.message || e)); }
    }, 1500);
  }, [onDone]);

  const start = useCallback(async () => {
    setErr(''); setResult(null);
    setJob({ status: 'running', progress: 'starting' });
    try {
      const r = await api.diPull(cfg);
      if (r.status !== 'ok') { setErr(r.message || 'could not start'); setJob(null); return; }
      setJob(r.job);
      if (r.job.status === 'running') poll(r.job.id); else setResult(r.job.result || null);
    } catch (e) { setErr(String(e.message || e)); setJob(null); }
  }, [cfg, poll]);

  const started = useRef(0);
  useEffect(() => {
    if (startSignal && startSignal !== started.current && !running) { started.current = startSignal; start(); }
  }, [startSignal, running, start]);

  const notConnected = plan?.code === 'not_connected';

  return (
    <div className="space-y-4">
      <div className="card !p-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-[14px] font-semibold text-gray-100">Refresh from Zerodha</div>
            <div className="text-[12px] text-gray-400 mt-0.5">
              Pulls the live option chains around ATM (with OI), the index, India VIX and futures into the Market Store — the same store the OI Lab, Signal Desk and Options Lab read.
            </div>
          </div>
          <button onClick={start} disabled={running || notConnected}
            className="btn-primary !py-2 !px-4 text-[13px] flex items-center gap-2 disabled:opacity-50">
            {running ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            {running ? 'Pulling…' : 'Pull now'}
          </button>
        </div>

        {last && (
          <div className="text-[12px] text-gray-400 flex flex-wrap items-center gap-x-4 gap-y-1">
            <span className="text-gray-300">Last pull {ago(last.at)}</span>
            <span className="mono text-gray-500">{last.at}</span>
            <span>{last.from} → {last.to}</span>
            <span>{num(last.series_count)} series · {num(last.total_rows)} bars · <span className="text-emerald-400">{num(last.total_added)} new</span></span>
          </div>
        )}

        {notConnected ? (
          <div className="text-[12px] text-amber-500 flex items-start gap-1.5">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-px" />
            <span>{plan.message} Log in to Zerodha from the dashboard, then come back and press Pull now.</span>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Indices" hint="Option chains and index bars">
              <div className="flex flex-wrap gap-1 mt-1">
                {INDICES.map((u) => {
                  const on = cfg.indices.includes(u);
                  return (
                    <button key={u} disabled={running}
                      onClick={() => set({ indices: on ? cfg.indices.filter((x) => x !== u) : [...cfg.indices, u] })}
                      className={`px-2 py-1 rounded text-[11.5px] font-semibold border transition disabled:opacity-50 ${on ? 'border-brand-500 bg-brand-500 text-white' : 'border-surface-3 text-gray-400 hover:text-gray-200'}`}>
                      {u}
                    </button>
                  );
                })}
              </div>
            </Field>
            <Field label="Days back" hint="Calendar days, 1-minute bars (max 60)">
              <input type="number" min={1} max={60} value={cfg.days} disabled={running}
                onChange={(e) => set({ days: Math.max(1, Math.min(60, Number(e.target.value) || 1)) })}
                className="input-field !py-1.5 mt-1 w-full" />
            </Field>
            <Field label="Strikes around ATM" hint={`±${cfg.strikes} strikes, CE and PE`}>
              <input type="number" min={1} max={25} value={cfg.strikes} disabled={running}
                onChange={(e) => set({ strikes: Math.max(1, Math.min(25, Number(e.target.value) || 1)) })}
                className="input-field !py-1.5 mt-1 w-full" />
            </Field>
            <Field label="Expiries" hint="Nearest first">
              <input type="number" min={1} max={4} value={cfg.expiries} disabled={running}
                onChange={(e) => set({ expiries: Math.max(1, Math.min(4, Number(e.target.value) || 1)) })}
                className="input-field !py-1.5 mt-1 w-full" />
            </Field>
            <div className="sm:col-span-2 lg:col-span-4 flex flex-wrap gap-4 text-[12px] text-gray-300">
              {[['include_index', 'Index bars'], ['include_vix', 'India VIX'], ['include_futures', 'Futures']].map(([k, label]) => (
                <label key={k} className="flex items-center gap-1.5 cursor-pointer">
                  <input type="checkbox" checked={!!cfg[k]} disabled={running} onChange={(e) => set({ [k]: e.target.checked })} className="accent-brand-500" />
                  {label}
                </label>
              ))}
            </div>
          </div>
        )}

        {plan?.status === 'ok' && (
          <div className="border-t border-surface-3 pt-3 space-y-2">
            <div className="text-[12px] text-gray-400 flex flex-wrap items-center gap-x-4 gap-y-1">
              {planning ? <span className="flex items-center gap-1.5"><Loader2 className="w-3.5 h-3.5 animate-spin" />checking what is listed…</span>
                : <span><span className="text-gray-200">{num(plan.tasks)}</span> series · {plan.from} → {plan.to} · about {plan.seconds_estimate}s</span>}
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[520px] text-[12px] whitespace-nowrap">
                <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                  {['Underlying', 'Kind', 'Series', 'Expiries'].map((h) => <th key={h} className="text-left px-2 py-1 font-medium">{h}</th>)}
                </tr></thead>
                <tbody>{plan.by_series.map((r) => (
                  <tr key={`${r.underlying}-${r.kind}`} className="border-b border-surface-3/40">
                    <td className="px-2 py-1.5 text-gray-200">{r.underlying}</td>
                    <td className="px-2 py-1.5 text-gray-400">{r.kind === 'options' ? 'option chain' : 'bars'}</td>
                    <td className="px-2 py-1.5 mono">{num(r.series)}</td>
                    <td className="px-2 py-1.5 mono text-gray-400">{r.expiries.join(', ') || '—'}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            {plan.notes?.map((n, i) => (
              <div key={i} className="text-[11.5px] text-amber-500 flex items-start gap-1.5"><AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" />{n}</div>
            ))}
          </div>
        )}

        {err && <div className="text-[12px] text-red-400">{err}</div>}
      </div>

      {running && (
        <div className="card !p-4 flex items-center gap-2 text-[12.5px] text-gray-300">
          <Loader2 className="w-4 h-4 text-brand-400 animate-spin" />
          <span>{job.progress || 'working…'}</span>
          <span className="text-gray-500">— it keeps running if you leave this page</span>
        </div>
      )}

      {result?.status === 'ok' && !running && (
        <div className="card !p-4 space-y-2">
          <div className="flex items-center gap-2 text-[13px] font-semibold text-gray-100">
            <CheckCircle2 className="w-4 h-4 text-emerald-400" />
            Pulled {num(result.series_count)} series · {num(result.total_rows)} bars · {num(result.total_added)} new
            <span className="text-[12px] font-normal text-gray-500">{result.from} → {result.to}</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-[12px] whitespace-nowrap">
              <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                {['Underlying', 'Kind', 'Series', 'Bars', 'New'].map((h) => <th key={h} className="text-left px-2 py-1 font-medium">{h}</th>)}
              </tr></thead>
              <tbody>{result.series.map((r) => (
                <tr key={`${r.underlying}-${r.kind}`} className="border-b border-surface-3/40">
                  <td className="px-2 py-1.5 text-gray-200">{r.underlying}</td>
                  <td className="px-2 py-1.5 text-gray-400">{r.kind === 'options' ? 'option chain' : 'bars'}</td>
                  <td className="px-2 py-1.5 mono">{num(r.series)}</td>
                  <td className="px-2 py-1.5 mono">{num(r.rows)}</td>
                  <td className={`px-2 py-1.5 mono ${r.added ? 'text-emerald-400' : 'text-gray-500'}`}>{num(r.added)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          {result.failures?.map((f, i) => (
            <div key={i} className="text-[11.5px] text-red-400 flex items-start gap-1.5"><AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" />{f}</div>
          ))}
          <div className="text-[11.5px] text-gray-500">
            Anything already stored is merged, not duplicated — “new” counts only bars the store did not have. The OI study of every index that changed rebuilds on its own.
          </div>
        </div>
      )}

      <div className="card !p-4 text-[12px] text-gray-400 space-y-1.5">
        <div className="flex items-center gap-1.5 text-gray-200 font-semibold"><Info className="w-4 h-4 text-brand-400" />How to use this</div>
        <div>• Press <span className="text-gray-200">Pull now</span> once a day, ideally after 15:30, and the whole session — chain, index, VIX, futures — is captured while Zerodha still lists it.</div>
        <div>• A contract that has already expired is dropped from Zerodha’s instrument list and cannot be pulled. That history comes from a file upload.</div>
        <div>• Zerodha serves at most 60 days of 1-minute data per request, and requests are spaced to stay inside its rate limit — a ±10-strike pull of two indices takes a couple of minutes.</div>
        <div>• Futures are stored under <span className="mono">NIFTYFUT</span> / <span className="mono">SENSEXFUT</span>, India VIX under <span className="mono">INDIAVIX</span>; all three show up in Coverage and the Explorer.</div>
        <div>• Read-only: this fetches market data only and never places an order.</div>
      </div>
    </div>
  );
}
