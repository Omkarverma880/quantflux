import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, Play, Trash2, Download, History } from 'lucide-react';
import { api, API_BASE } from '../../api';
import {
  Section, Stat, Field, Note, TradeTable, MonthTiles, EquityCurve, N, N0, RS, PCT, tone, input,
} from './ui';

/** Backtest the frozen rule on the stored history, and keep every run for comparison. */
export default function Backtest({ meta }) {
  const cov = meta?.coverage?.options || {};
  const [start, setStart] = useState(cov.first || '');
  const [end, setEnd] = useState(cov.last || '');
  const [lots, setLots] = useState(1);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState('');
  const [err, setErr] = useState('');
  const [run, setRun] = useState(null);         // { id, summary, ... }
  const [trades, setTrades] = useState([]);
  const [runs, setRuns] = useState([]);
  const poll = useRef(null);

  const loadRuns = useCallback(async () => {
    const r = await api.flRuns();
    if (r.status === 'ok') setRuns(r.runs || []);
  }, []);

  const openRun = useCallback(async (id) => {
    const [r, t] = await Promise.all([api.flRun(id), api.flRunTrades(id)]);
    if (r.status !== 'ok') { setErr(r.message || 'run not found'); return; }
    setRun(r.run);
    setTrades(t.trades || []);
  }, []);

  useEffect(() => {
    loadRuns().then(() => {});
    return () => poll.current && clearInterval(poll.current);
  }, [loadRuns]);

  useEffect(() => {                                // open the latest stored run on first load
    if (!run && runs.length) openRun(runs[0].id);
  }, [runs, run, openRun]);

  const go = async () => {
    setErr(''); setBusy(true); setProgress('starting');
    try {
      const r = await api.flBacktest({ config: { start, end, lots: Number(lots) || 1 }, store: true });
      if (r.status !== 'ok') throw new Error(r.message);
      const jobId = r.job.id;
      poll.current = setInterval(async () => {
        const j = await api.flJob(jobId);
        const job = j.job || {};
        setProgress(job.progress || '');
        if (job.status === 'done') {
          clearInterval(poll.current);
          setBusy(false);
          await loadRuns();
          if (job.result?.run_id) await openRun(job.result.run_id);
        } else if (job.status === 'error' || j.status === 'error') {
          clearInterval(poll.current);
          setBusy(false);
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
        a.href = url; a.download = `flux-ironfly-run-${run.id}-${what}.csv`; a.click();
        URL.revokeObjectURL(url);
      }).catch((e) => setErr(String(e.message || e)));
  };

  const s = run?.summary || {};
  return (
    <div className="space-y-4">
      <Section title="Run a backtest">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 items-end">
          <Field label="From"><input type="date" value={start} onChange={(e) => setStart(e.target.value)} className={input} /></Field>
          <Field label="To"><input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className={input} /></Field>
          <Field label="Lots"><input type="number" min={1} max={50} value={lots} onChange={(e) => setLots(e.target.value)} className={input} /></Field>
          <button disabled={busy || !start || !end} onClick={go} className="btn-primary !py-1.5 text-[12.5px] flex items-center justify-center gap-1.5">
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}{busy ? 'Running…' : 'Run backtest'}
          </button>
        </div>
        {busy && <div className="text-[11.5px] text-gray-500 mt-2">{progress}</div>}
        {err && <div className="text-[12px] text-red-400 mt-2">{err}</div>}
        <div className="text-[11px] text-gray-500 mt-2">
          Stored data: {cov.first || '—'} → {cov.last || '—'} ({N0(cov.sessions)} sessions). Real option prices, next-minute fills,
          {' '}{meta?.slippage_pts ?? 0.5} pt slippage per leg per side, Zerodha charges on every leg, lot size {meta?.lot_size ?? 65} throughout.
        </div>
      </Section>

      {run && (
        <>
          <Section title={`Run #${run.id} · ${run.start} → ${run.end} · ${run.lots} lot`} right={
            <div className="flex items-center gap-1">
              {['trades', 'monthly'].map((w) => (
                <button key={w} onClick={() => exportCsv(w)} className="btn-secondary !py-1 !px-2 text-[11px] flex items-center gap-1">
                  <Download className="w-3 h-3" />{w}
                </button>
              ))}
            </div>}>
            {!s.trades ? <Note>{s.message || 'No trades in this range.'}</Note> : (
              <>
                <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3 mb-3">
                  <Stat label="Net P&L" value={RS(s.net)} tone={tone(s.net)} sub={`after ${RS(-s.charges)} charges`} />
                  <Stat label="Green months" value={`${s.green_months}/${s.months}`} sub={PCT((s.green_months / s.months) * 100, 0)} />
                  <Stat label="Avg month" value={RS(s.avg_month)} tone={tone(s.avg_month)} sub={`worst ${RS(s.worst_month)}`} />
                  <Stat label="Trades" value={N0(s.trades)} sub={`on ${PCT(s.trade_days_pct, 0)} of ${s.sessions} days`} />
                  <Stat label="Win rate" value={PCT(s.win_rate, 0)} />
                  <Stat label="Avg win / loss" value={`${RS(s.avg_win)} / ${RS(s.avg_loss)}`} />
                  <Stat label="Profit factor" value={N(s.profit_factor)} sub="₹ won per ₹ lost" />
                  <Stat label="Max drawdown" value={RS(s.max_drawdown)} tone="text-red-400" />
                </div>
                <div className="text-[11.5px] text-gray-400 mb-3">
                  Exits: {Object.entries(s.exits || {}).map(([k, v]) => `${k} ${v}`).join(' · ')} ·
                  {' '}average credit {N(s.avg_credit_pts)} pts · best trade {RS(s.best)} · worst trade {RS(s.worst)}
                  {s.skipped ? ` · ${s.skipped} signal days skipped (no tradable price)` : ''}
                </div>
                <EquityCurve trades={trades} />
                <div className="mt-3"><MonthTiles months={s.monthly || []} /></div>
                <div className="flex flex-wrap gap-3 mt-3">
                  {(s.yearly || []).map((y) => (
                    <div key={y.year} className="text-[12px] text-gray-400">
                      {y.year}: <span className={`mono font-semibold ${tone(y.net)}`}>{RS(y.net)}</span> ({y.trades} trades)
                    </div>
                  ))}
                </div>
              </>
            )}
          </Section>
          <Section title={`Trades · ${trades.length}`}>
            <TradeTable trades={trades} />
          </Section>
        </>
      )}

      <Section title={`Saved runs · ${runs.length}`}>
        {!runs.length ? <Note>No backtests stored yet.</Note> : (
          <div className="overflow-x-auto">
            <table className="w-full text-[12px] min-w-[640px]">
              <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                {['Run', 'When', 'Range', 'Lots', 'Trades', 'Win %', 'Green months', 'Net P&L', ''].map((h) => (
                  <th key={h} className="text-left px-2 py-1 font-medium">{h}</th>))}
              </tr></thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id} className={`border-b border-surface-3/40 cursor-pointer hover:bg-surface-2/60 ${run?.id === r.id ? 'bg-surface-2/60' : ''}`}
                    onClick={() => openRun(r.id)}>
                    <td className="px-2 py-1 mono text-gray-300 flex items-center gap-1"><History className="w-3 h-3 text-gray-500" />#{r.id}</td>
                    <td className="px-2 py-1 text-gray-400">{r.created ? new Date(r.created).toLocaleString('en-IN') : '—'}</td>
                    <td className="px-2 py-1 mono text-gray-300">{r.start} → {r.end}</td>
                    <td className="px-2 py-1 mono text-gray-300">{r.lots}</td>
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
    </div>
  );
}
