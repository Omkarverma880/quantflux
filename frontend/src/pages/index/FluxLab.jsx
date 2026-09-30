import React, { useCallback, useEffect, useRef, useState } from 'react';
import { FlaskConical, Loader2, Settings2, BarChart3, BookOpen, History, Trash2 } from 'lucide-react';
import { api, API_BASE } from '../../api';
import Setup from '../../components/fluxlab/Setup';
import Results from '../../components/fluxlab/Results';
import Rules from '../../components/fluxlab/Rules';
import { Note, PCT, RS, Section, tone } from '../../components/fluxlab/ui';

/**
 * Flux Strategy Test Lab — the hammer and inverted hammer, combining two TradingView indicators,
 * backtested on stored NIFTY candles and traded in the option chain. Backtest only; no orders.
 */
const TABS = [
  ['setup', 'Setup', Settings2],
  ['results', 'Results', BarChart3],
  ['rules', 'The rules', BookOpen],
  ['runs', 'Saved runs', History],
];

export default function FluxLab() {
  const [tab, setTab] = useState('setup');
  const [meta, setMeta] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [run, setRun] = useState(null);
  const [trades, setTrades] = useState([]);
  const [runs, setRuns] = useState([]);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState('');
  const [err, setErr] = useState('');
  const poll = useRef(null);

  useEffect(() => {
    api.flMeta().then((m) => {
      if (m.status !== 'ok') { setErr(m.message || 'could not load the lab'); return; }
      setMeta(m);
      const cov = m.coverage?.options || {};
      setCfg({
        start: cov.last ? `${cov.last.slice(0, 4)}-01-01` : '', end: cov.last || '',
        timeframe: m.defaults.timeframe, params: { ...m.defaults.params },
        execution: { ...m.defaults.execution },
      });
    }).catch((e) => setErr(String(e.message || e)));
    return () => poll.current && clearInterval(poll.current);
  }, []);

  const loadRuns = useCallback(async () => {
    const r = await api.flRuns();
    if (r.status === 'ok') setRuns(r.runs || []);
  }, []);
  useEffect(() => { loadRuns(); }, [loadRuns]);

  const openRun = useCallback(async (id) => {
    const [r, t] = await Promise.all([api.flRun(id), api.flRunTrades(id)]);
    if (r.status !== 'ok') { setErr(r.message || 'run not found'); return; }
    setRun(r.run);
    setTrades(t.trades || []);
    setTab('results');
  }, []);

  const go = async () => {
    setErr(''); setBusy(true); setProgress('starting'); setTab('setup');
    try {
      const r = await api.flBacktest({ config: cfg, store: true });
      if (r.status !== 'ok') throw new Error(r.message);
      poll.current = setInterval(async () => {
        const j = await api.flJob(r.job.id);
        const job = j.job || {};
        setProgress(job.progress || '');
        if (job.status === 'done') {
          clearInterval(poll.current); setBusy(false);
          await loadRuns();
          if (job.result?.run_id) await openRun(job.result.run_id);
        } else if (job.status === 'error' || j.status === 'error') {
          clearInterval(poll.current); setBusy(false);
          setErr(job.error || j.message || 'backtest failed');
        }
      }, 1500);
    } catch (e) { setBusy(false); setErr(String(e.message || e)); }
  };

  const remove = async (id) => {
    await api.flDeleteRun(id);
    if (run?.id === id) { setRun(null); setTrades([]); }
    loadRuns();
  };

  const exportCsv = (what) => {
    if (!run) return;
    const token = localStorage.getItem('app_token');
    fetch(`${API_BASE}/index-strategy/flux-lab/runs/${run.id}/export.csv?what=${what}`,
      { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then((r) => r.blob()).then((b) => {
        const url = URL.createObjectURL(b);
        const a = document.createElement('a');
        a.href = url; a.download = `flux-hammer-run-${run.id}-${what}.csv`; a.click();
        URL.revokeObjectURL(url);
      }).catch((e) => setErr(String(e.message || e)));
  };

  if (!cfg) {
    return <div className="p-6 text-center text-gray-500 text-sm">
      {err ? <span className="text-red-400">{err}</span> : <><Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />Loading the lab…</>}
    </div>;
  }

  return (
    <div className="p-4 sm:p-6 space-y-4 max-w-[1600px] mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl sm:text-2xl font-bold text-white flex items-center gap-2">
            <FlaskConical className="w-5 h-5 text-brand-400" />Flux Strategy Test Lab
          </h1>
          <p className="text-xs sm:text-sm text-gray-500 mt-0.5">
            {meta?.strategy} — two TradingView hammer indicators combined, read on NIFTY candles and traded in
            the option chain. Backtest only: no orders are placed.
          </p>
        </div>
        {run?.summary?.trades > 0 && (
          <div className="flex items-center gap-4 text-right">
            <div>
              <div className="text-[10px] uppercase tracking-wider text-gray-500">Net P&L</div>
              <div className={`text-[16px] font-bold mono ${tone(run.summary.net)}`}>{RS(run.summary.net)}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-gray-500">Trades</div>
              <div className="text-[16px] font-bold mono text-gray-100">{run.summary.trades}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-gray-500">Win rate</div>
              <div className="text-[16px] font-bold mono text-gray-100">{PCT(run.summary.win_rate, 0)}</div>
            </div>
          </div>
        )}
      </div>

      <div className="flex gap-1 border-b border-surface-3 overflow-x-auto">
        {TABS.map(([id, label, Icon]) => (
          <button key={id} onClick={() => setTab(id)}
            className={`flex items-center gap-1.5 px-3.5 py-2 text-sm font-semibold border-b-2 -mb-px transition whitespace-nowrap ${tab === id
              ? 'border-brand-500 text-brand-400' : 'border-transparent text-gray-400 hover:text-gray-200'}`}>
            <Icon className="w-4 h-4" />{label}
          </button>
        ))}
      </div>

      {tab === 'setup' && (
        <Setup cfg={cfg} setCfg={setCfg} meta={meta} onRun={go} busy={busy} progress={progress} error={err} />
      )}
      {tab === 'results' && (run ? <Results run={run} trades={trades} onExport={exportCsv} />
        : <Note>Run a backtest from the Setup tab, or open one from Saved runs.</Note>)}
      {tab === 'rules' && <Rules meta={meta} params={cfg.params} />}
      {tab === 'runs' && (
        <Section title={`Saved runs · ${runs.length}`}>
          {!runs.length ? <Note>No runs stored yet.</Note> : (
            <div className="overflow-x-auto">
              <table className="w-full text-[12px] min-w-[760px]">
                <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                  {['Run', 'When', 'Range', 'Candle', 'Signals', 'Trades', 'Win %', 'Green months', 'Net P&L', ''].map((h) => (
                    <th key={h} className="text-left px-2 py-1 font-medium">{h}</th>))}
                </tr></thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={r.id} onClick={() => openRun(r.id)}
                      className={`border-b border-surface-3/40 cursor-pointer hover:bg-surface-2/60 ${run?.id === r.id ? 'bg-surface-2/60' : ''}`}>
                      <td className="px-2 py-1 mono text-gray-300">#{r.id}</td>
                      <td className="px-2 py-1 text-gray-400">{r.created ? new Date(r.created).toLocaleString('en-IN') : '—'}</td>
                      <td className="px-2 py-1 mono text-gray-300">{r.start} → {r.end}</td>
                      <td className="px-2 py-1 mono text-gray-400">{r.timeframe}m</td>
                      <td className="px-2 py-1 mono text-gray-400">{r.signals}</td>
                      <td className="px-2 py-1 mono text-gray-300">{r.trades}</td>
                      <td className="px-2 py-1 mono text-gray-300">{PCT(r.win_rate, 0)}</td>
                      <td className="px-2 py-1 mono text-gray-300">{r.months ? `${r.green_months}/${r.months}` : '—'}</td>
                      <td className={`px-2 py-1 mono font-semibold ${tone(r.net)}`}>{RS(r.net)}</td>
                      <td className="px-2 py-1 text-right">
                        <button onClick={(e) => { e.stopPropagation(); remove(r.id); }} className="text-gray-500 hover:text-red-400">
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      )}
    </div>
  );
}
