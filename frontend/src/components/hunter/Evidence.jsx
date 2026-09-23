import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, Play, AlertTriangle } from 'lucide-react';
import { api } from '../../api';
import { Note, Section, N, PCT, tone } from './ui';

/**
 * What each screen has actually returned. One fixed trade rule for all of them, so the screens are
 * compared like for like — and the caveats are on the page, not buried.
 */
export default function Evidence() {
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState('');
  const [err, setErr] = useState('');
  const [years, setYears] = useState(5);
  const poll = useRef(null);

  const load = useCallback(async () => {
    const r = await api.huEvidence();
    setData(r);
  }, []);
  useEffect(() => { load(); return () => poll.current && clearInterval(poll.current); }, [load]);

  const build = async () => {
    setErr(''); setBusy(true); setProgress('starting');
    try {
      const r = await api.huBuildEvidence(years);
      if (r.status !== 'ok') throw new Error(r.message);
      poll.current = setInterval(async () => {
        const j = await api.huJob(r.job.id);
        const job = j.job || {};
        setProgress(job.progress || '');
        if (job.status === 'done' || job.status === 'error') {
          clearInterval(poll.current);
          setBusy(false);
          if (job.status === 'error') setErr(job.error || 'could not build the evidence');
          else load();
        }
      }, 2000);
    } catch (e) { setBusy(false); setErr(String(e.message || e)); }
  };

  const screens = data?.screens ? Object.entries(data.screens) : [];
  return (
    <div className="space-y-4">
      <Section title="What these screens have returned" right={
        <div className="flex items-center gap-2">
          <select value={years} onChange={(e) => setYears(Number(e.target.value))}
            className="bg-surface-2 border border-surface-3 rounded px-2 py-1 text-[11.5px] text-gray-300">
            {[2, 3, 5, 8].map((y) => <option key={y} value={y}>last {y} years</option>)}
          </select>
          <button onClick={build} disabled={busy} className="btn-primary !py-1 !px-2.5 text-[11.5px] flex items-center gap-1">
            {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
            {busy ? 'Building…' : data?.status === 'ok' ? 'Rebuild' : 'Build evidence'}
          </button>
        </div>}>
        {busy && <Note>Walking every stock's history — {progress}. This uses the candles already cached, so it
          needs no Zerodha connection.</Note>}
        {err && <div className="text-[12px] text-red-400">{err}</div>}
        {data?.status !== 'ok' ? (
          !busy && <div className="py-6 text-center text-[12.5px] text-gray-500">
            {data?.message || 'Nothing built yet.'} Build it once and every screen gets a track record.
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-[12px] min-w-[860px]">
                <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                  {['Screen', 'Trades', 'Win %', 'Avg win', 'Avg loss', 'Payoff', 'Per trade', 'Median',
                    'Avg hold', 'Best', 'Worst'].map((h) => (
                      <th key={h} className={`px-2 py-1 font-medium ${h === 'Screen' ? 'text-left' : 'text-right'}`}>{h}</th>))}
                </tr></thead>
                <tbody>
                  {screens.map(([k, s]) => (
                    <tr key={k} className="border-b border-surface-3/40">
                      <td className="px-2 py-1.5 text-gray-200 font-semibold">{s.screen}</td>
                      <td className="px-2 py-1.5 mono text-right text-gray-300">{s.trades}</td>
                      <td className="px-2 py-1.5 mono text-right text-gray-300">{s.win_rate != null ? `${s.win_rate}%` : '—'}</td>
                      <td className="px-2 py-1.5 mono text-right text-emerald-400">{s.avg_win != null ? `${s.avg_win}%` : '—'}</td>
                      <td className="px-2 py-1.5 mono text-right text-red-400">{s.avg_loss != null ? `${s.avg_loss}%` : '—'}</td>
                      <td className="px-2 py-1.5 mono text-right text-gray-300">{s.payoff ?? '—'}</td>
                      <td className={`px-2 py-1.5 mono text-right font-semibold ${tone(s.expectancy)}`}>
                        {s.expectancy != null ? `${s.expectancy > 0 ? '+' : ''}${s.expectancy}%` : '—'}</td>
                      <td className={`px-2 py-1.5 mono text-right ${tone(s.median)}`}>{s.median != null ? `${s.median}%` : '—'}</td>
                      <td className="px-2 py-1.5 mono text-right text-gray-400">{s.avg_hold != null ? `${s.avg_hold}d` : '—'}</td>
                      <td className="px-2 py-1.5 mono text-right text-emerald-400/80">{s.best != null ? `${s.best}%` : '—'}</td>
                      <td className="px-2 py-1.5 mono text-right text-red-400/80">{s.worst != null ? `${s.worst}%` : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="text-[11.5px] text-gray-500 mt-2">
              Every screen traded the same way: {data.rule?.entry}, a {data.rule?.stop_pct}% stop,
              {' '}out on {data.rule?.trail}, {data.rule?.max_hold} sessions at most,
              {' '}{data.rule?.cost_pct_each_way}% costs each way. Built {data.built?.slice(0, 16)} over the last {data.years} years.
            </div>
          </>
        )}
      </Section>

      {data?.status === 'ok' && (
        <>
          <Section title="Year by year, average return per trade">
            <div className="overflow-x-auto">
              <table className="w-full text-[12px] min-w-[560px]">
                <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                  <th className="text-left px-2 py-1 font-medium">Screen</th>
                  {(screens[0]?.[1]?.by_year || []).map((y) => <th key={y.year} className="text-right px-2 py-1 font-medium">{y.year}</th>)}
                </tr></thead>
                <tbody>
                  {screens.map(([k, s]) => (
                    <tr key={k} className="border-b border-surface-3/40">
                      <td className="px-2 py-1.5 text-gray-200">{s.screen}</td>
                      {(s.by_year || []).map((y) => (
                        <td key={y.year} className={`px-2 py-1.5 mono text-right ${tone(y.avg_pct)}`}
                          title={`${y.trades} trades`}>{y.avg_pct > 0 ? '+' : ''}{y.avg_pct}%</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>

          <Section title="Read this before trusting the table">
            <div className="space-y-1.5">
              {(data.caveats || []).map((c, i) => (
                <div key={i} className="flex items-start gap-1.5">
                  <AlertTriangle className="w-3.5 h-3.5 text-amber-500 mt-0.5 shrink-0" />
                  <span className="text-[12px] text-gray-300">{c}</span>
                </div>
              ))}
            </div>
          </Section>
        </>
      )}
    </div>
  );
}
