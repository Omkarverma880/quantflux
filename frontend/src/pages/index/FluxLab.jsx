import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  FlaskConical, Loader2, Settings2, BarChart3, CalendarRange, ListOrdered,
  Beaker, PlayCircle, Radio, History, Trash2, Download,
} from 'lucide-react';
import { api, API_BASE } from '../../api';
import Setup from '../../components/fluxlab/Setup';
import Results from '../../components/fluxlab/Results';
import Breakdowns from '../../components/fluxlab/Breakdowns';
import Ledger from '../../components/fluxlab/Ledger';
import ResearchPanel from '../../components/fluxlab/ResearchPanel';
import Replay from '../../components/fluxlab/Replay';
import Paper from '../../components/fluxlab/Paper';
import { Section, Note, N0, PCT, RS, tone } from '../../components/fluxlab/ui';

/**
 * Flux Strategy Test Lab — research a NIFTY option-buying idea on the history Quantflux already
 * stores, validate it, replay it bar by bar, then run the same logic forward on live data as
 * paper trades. One engine throughout; no order is ever placed from this page.
 */

const TABS = [
  ['setup', 'Setup', Settings2],
  ['results', 'Results', BarChart3],
  ['breakdowns', 'Breakdowns', CalendarRange],
  ['ledger', 'Trade ledger', ListOrdered],
  ['research', 'Validation', Beaker],
  ['replay', 'Replay', PlayCircle],
  ['paper', 'Paper trading', Radio],
  ['runs', 'Saved runs', History],
];

const DEFAULT_CFG = (meta) => ({
  underlying: 'NIFTY',
  start: '', end: '', timeframe: 5, capital: 200000,
  strategy: { preset: 'trend_pullback_ce' },
  execution: meta?.defaults?.execution || {},
  risk: meta?.defaults?.risk || {},
  selection: meta?.defaults?.selection || {},
});

export default function FluxLab() {
  const [tab, setTab] = useState('setup');
  const [meta, setMeta] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [run, setRun] = useState(null);
  const [trades, setTrades] = useState([]);
  const [runId, setRunId] = useState(null);
  const [runs, setRuns] = useState([]);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState('');
  const [err, setErr] = useState('');
  const [quality, setQuality] = useState(null);
  const [mode, setMode] = useState('splits');
  const [research, setResearch] = useState(null);
  const [replayDate, setReplayDate] = useState('');
  const [replayData, setReplayData] = useState(null);
  const poll = useRef(null);

  useEffect(() => {
    api.flMeta().then((m) => {
      if (m.status !== 'ok') { setErr(m.message); return; }
      setMeta(m);
      const span = m.coverage?.options || m.coverage?.spot || {};
      const base = DEFAULT_CFG(m);
      // default to the most recent three months of stored data — a full run can take minutes
      if (span.last) {
        const end = new Date(span.last);
        const start = new Date(end); start.setMonth(start.getMonth() - 3);
        const floor = span.first ? new Date(span.first) : start;
        base.end = span.last;
        base.start = (start < floor ? floor : start).toISOString().slice(0, 10);
        setReplayDate(span.last);
      }
      setCfg(base);
    }).catch((e) => setErr(String(e.message || e)));
    loadRuns();
    return () => clearTimeout(poll.current);
  }, []);

  const loadRuns = useCallback(async () => {
    try {
      const r = await api.flRuns();
      if (r.status === 'ok') setRuns(r.runs || []);
    } catch { /* listing is not critical */ }
  }, []);

  const watch = useCallback((jobId, onDone) => {
    poll.current = setTimeout(async () => {
      try {
        const r = await api.flJob(jobId);
        if (r.status !== 'ok') { setErr(r.message); setBusy(false); return; }
        setProgress(r.job.progress || '');
        if (r.job.status === 'running') { watch(jobId, onDone); return; }
        setBusy(false);
        if (r.job.status === 'error') { setErr(r.job.error || 'run failed'); return; }
        onDone(r.job.result);
      } catch (e) { setErr(String(e.message || e)); setBusy(false); }
    }, 1200);
  }, []);

  const runBacktest = async () => {
    setBusy(true); setErr(''); setProgress('starting'); setResearch(null);
    try {
      const r = await api.flBacktest({ config: cfg, store: true });
      if (r.status !== 'ok') { setErr(r.message); setBusy(false); return; }
      watch(r.job.id, async (result) => {
        if (!result || result.status === 'error') { setErr(result?.message || 'run failed'); return; }
        setRun(result);
        setRunId(result.run_id || null);
        setTrades(result.trades || []);
        setTab('results');
        loadRuns();
      });
    } catch (e) { setErr(String(e.message || e)); setBusy(false); }
  };

  const checkData = async () => {
    setBusy(true); setErr('');
    try {
      const r = await api.flDataCheck({ config: cfg });
      setQuality(r.status === 'ok' ? r : null);
      if (r.status !== 'ok') setErr(r.message);
    } finally { setBusy(false); }
  };

  const runResearch = async (which, extra) => {
    setBusy(true); setErr(''); setProgress('starting'); setResearch(null);
    try {
      const r = await api.flResearch(which, { config: cfg, ...extra });
      if (r.status !== 'ok') { setErr(r.message); setBusy(false); return; }
      watch(r.job.id, (result) => {
        if (!result || result.status === 'error') { setErr(result?.message || 'run failed'); return; }
        setResearch(result);
      });
    } catch (e) { setErr(String(e.message || e)); setBusy(false); }
  };

  const loadReplay = async () => {
    setBusy(true); setErr(''); setReplayData(null);
    try {
      const r = await api.flReplay({ config: { ...cfg, start: replayDate, end: replayDate } });
      if (r.status !== 'ok') { setErr(r.message); return; }
      setReplayData(r);
    } catch (e) { setErr(String(e.message || e)); } finally { setBusy(false); }
  };

  const openRun = async (id) => {
    setBusy(true); setErr('');
    try {
      const [r, t] = await Promise.all([api.flRun(id), api.flRunTrades(id)]);
      if (r.status === 'ok') { setRun(r.run); setRunId(id); setTrades(t.trades || []); setTab('results'); }
      else setErr(r.message);
    } finally { setBusy(false); }
  };

  const removeRun = async (id) => {
    if (!window.confirm('Delete this run and its trades?')) return;
    await api.flDeleteRun(id);
    if (runId === id) { setRun(null); setTrades([]); setRunId(null); }
    loadRuns();
  };

  const exportCsv = (what) => {
    if (!runId) return;
    const token = localStorage.getItem('app_token');
    fetch(`${API_BASE}/index-strategy/flux-lab/runs/${runId}/export.csv?what=${what}`,
      { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then((r) => r.blob()).then((b) => {
        const url = URL.createObjectURL(b);
        const a = document.createElement('a');
        a.href = url; a.download = `flux-run-${runId}-${what}.csv`; a.click();
        URL.revokeObjectURL(url);
      }).catch((e) => setErr(String(e.message || e)));
  };

  if (!cfg) {
    return (
      <div className="p-6 text-center text-gray-500 text-sm">
        {err ? <span className="text-red-400">{err}</span> : <><Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />Loading the lab…</>}
      </div>
    );
  }

  return (
    <div className="p-4 sm:p-6 space-y-4 max-w-[1600px] mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="w-full sm:w-auto sm:flex-1 min-w-0">
          <h1 className="text-xl sm:text-2xl font-bold text-white flex items-center gap-2">
            <FlaskConical className="w-5 h-5 text-brand-400" />Flux Strategy Test Lab
          </h1>
          <p className="text-xs sm:text-sm text-gray-500 mt-0.5">
            Test a NIFTY option-buying idea on stored history, validate it out-of-sample, replay it
            bar by bar, then run the same engine forward on live data as paper trades
          </p>
        </div>
        {run?.headline?.trades > 0 && (
          <div className="flex items-center gap-4 text-right">
            <div>
              <div className="text-[10px] uppercase tracking-wider text-gray-500">Net P&L</div>
              <div className={`text-[16px] font-bold mono ${tone(run.headline.net_pnl)}`}>{RS(run.headline.net_pnl)}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-gray-500">Trades</div>
              <div className="text-[16px] font-bold mono text-gray-100">{N0(run.headline.trades)}</div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wider text-gray-500">Win rate</div>
              <div className="text-[16px] font-bold mono text-gray-100">{PCT(run.headline.win_rate, 0)}</div>
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

      {err && <div className="text-[12px] text-red-400">{err}</div>}

      {tab === 'setup' && (
        <Setup meta={meta} cfg={cfg} setCfg={setCfg} onRun={runBacktest} onCheck={checkData}
          busy={busy} progress={progress} quality={quality} />
      )}
      {tab === 'results' && (run ? <Results run={run} /> : <Note>Run a backtest from the Setup tab.</Note>)}
      {tab === 'breakdowns' && (run ? <Breakdowns run={run} /> : <Note>Run a backtest from the Setup tab.</Note>)}
      {tab === 'ledger' && <Ledger trades={trades} runId={runId} onExport={exportCsv} />}
      {tab === 'research' && (
        <ResearchPanel cfg={cfg} runId={runId} onRun={runResearch} busy={busy} progress={progress}
          result={research} mode={mode} setMode={setMode} />
      )}
      {tab === 'replay' && (
        <Replay cfg={cfg} onLoad={loadReplay} data={replayData} busy={busy}
          date={replayDate} setDate={setReplayDate} />
      )}
      {tab === 'paper' && <Paper cfg={cfg} runId={runId} />}

      {tab === 'runs' && (
        <Section title={`Saved runs · ${runs.length}`} right={runId && (
          <div className="flex items-center gap-1">
            {['trades', 'daily', 'monthly', 'mfe'].map((w) => (
              <button key={w} onClick={() => exportCsv(w)}
                className="btn-secondary !py-1 !px-2 text-[11px] flex items-center gap-1">
                <Download className="w-3 h-3" />{w}
              </button>
            ))}
          </div>)}>
          {!runs.length ? <Note>No stored runs yet.</Note> : (
            <div className="overflow-x-auto">
              <table className="w-full text-[12px] min-w-[880px]">
                <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                  {['When', 'Strategy', 'Range', 'TF', 'Trades', 'Win %', 'Net P&L', 'Hash', ''].map((h) => (
                    <th key={h} className="text-left px-2 py-1 font-medium">{h}</th>))}
                </tr></thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={r.id} onClick={() => openRun(r.id)}
                      className={`border-b border-surface-3/40 cursor-pointer hover:bg-surface-2/60 ${runId === r.id ? 'bg-brand-500/5' : ''}`}>
                      <td className="px-2 py-1.5 mono text-gray-400">{r.created_at}</td>
                      <td className="px-2 py-1.5 text-gray-200">{r.strategy}</td>
                      <td className="px-2 py-1.5 mono text-gray-400">{r.start} → {r.end}</td>
                      <td className="px-2 py-1.5 mono text-gray-400">{r.timeframe}m</td>
                      <td className="px-2 py-1.5 mono text-gray-300">{r.trades}</td>
                      <td className="px-2 py-1.5 mono text-gray-300">{PCT(r.headline?.win_rate, 0)}</td>
                      <td className={`px-2 py-1.5 mono font-semibold ${tone(r.headline?.net_pnl)}`}>{RS(r.headline?.net_pnl)}</td>
                      <td className="px-2 py-1.5 mono text-[10.5px] text-gray-500">{r.hash}</td>
                      <td className="px-2 py-1.5 text-right">
                        <button onClick={(e) => { e.stopPropagation(); removeRun(r.id); }}
                          className="text-gray-600 hover:text-red-400"><Trash2 className="w-3.5 h-3.5" /></button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <Note>Each row stores the exact configuration and a hash of it. Opening a run reloads its
            trades; re-running the same configuration over the same data reproduces it.</Note>
        </Section>
      )}

      <div className="text-[11px] text-gray-500 flex flex-wrap items-center justify-between gap-2">
        <span>Research and paper trading only — this lab has no order path.</span>
        <span>Index points and option P&L are always reported separately.</span>
      </div>
    </div>
  );
}
