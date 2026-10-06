import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Loader2, Play, Square, RefreshCw, AlertTriangle, Radio, BarChart3, BookOpen, Ruler, Info, Check, Download,
} from 'lucide-react';
import { api, API_BASE } from '../../api';
import { Note, Section, Stat, Field, input, N, N0, RS, PCT, tone } from '../../components/fluxlab/ui';

const TABS = [
  { id: 'desk', label: 'Live desk', icon: Radio },
  { id: 'backtest', label: 'Backtest', icon: BarChart3 },
  { id: 'rules', label: 'The rules', icon: BookOpen },
  { id: 'guide', label: 'Filters & presets', icon: Info },
];

// RS() signs everything, which is right for a P&L and wrong for a magnitude:
// a drawdown of 45,150 is not "+₹45,150". Costs and drawdowns use this instead.
const RSA = (v) => (v == null ? '—' : `₹${Math.abs(Number(v)).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`);

const today = () => new Date().toISOString().slice(0, 10);
const daysAgo = (n) => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);

export default function RangeLab() {
  const [tab, setTab] = useState('desk');
  const [meta, setMeta] = useState(null);
  const [desk, setDesk] = useState(null);
  const [params, setParams] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  // backtest
  const [range, setRange] = useState({ start: daysAgo(365), end: today() });
  const [job, setJob] = useState(null);
  const [result, setResult] = useState(null);
  const [runs, setRuns] = useState([]);
  const [guide, setGuide] = useState(null);
  const [applied, setApplied] = useState('');
  const poll = useRef(null);

  const loadMeta = useCallback(async () => {
    try {
      const d = await api.rangeMeta();
      setMeta(d);
      setParams(d?.config?.params || d?.defaults || null);
    } catch (e) { setErr(String(e.message || e)); }
  }, []);

  const loadDesk = useCallback(async () => {
    try { setDesk(await api.rangeDesk()); } catch (e) { setErr(String(e.message || e)); }
  }, []);

  useEffect(() => {
    loadMeta(); loadDesk();
    api.rangeRuns().then((d) => setRuns(d?.runs || [])).catch(() => {});
    api.rangeGuide().then(setGuide).catch(() => {});
  }, [loadMeta, loadDesk]);
  useEffect(() => {
    if (tab !== 'desk') return undefined;
    const t = setInterval(loadDesk, 5000);
    return () => clearInterval(t);
  }, [tab, loadDesk]);

  const act = async (fn) => {
    setBusy(true); setErr('');
    try { await fn(); await loadDesk(); await loadMeta(); }
    catch (e) { setErr(String(e.message || e)); }
    finally { setBusy(false); }
  };

  const saveParams = async (patch) => {
    const next = { ...(params || {}), ...patch };
    setParams(next);
    try { await api.rangeConfig({ params: patch }); } catch (e) { setErr(String(e.message || e)); }
  };

  const applyPreset = async (preset) => {
    // the dates matter as much as the parameters: a preset's number belongs to its window
    const w = guide?.window || {};
    setRange({ start: w.start || range.start, end: w.end || range.end });
    setParams({ ...(params || {}), ...preset.params });
    setApplied(preset.id);
    setTab('backtest');
    try { await api.rangeConfig({ params: preset.params }); }
    catch (e) { setErr(String(e.message || e)); }
  };

  const exportCsv = (runId, what) => {
    const token = localStorage.getItem('app_token');
    fetch(`${API_BASE}/index-strategy/range-lab/runs/${runId}/export.csv?what=${what}`,
      { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then((r) => r.blob()).then((b) => {
        const url = URL.createObjectURL(b);
        const a = document.createElement('a');
        a.href = url; a.download = `range-5-60-${runId}-${what}.csv`; a.click();
        URL.revokeObjectURL(url);
      }).catch((e) => setErr(String(e.message || e)));
  };

  const runBacktest = async () => {
    setBusy(true); setErr(''); setResult(null);
    try {
      const d = await api.rangeBacktest({ config: { ...range, params } });
      setJob(d?.job || null);
      clearInterval(poll.current);
      poll.current = setInterval(async () => {
        const j = await api.rangeJob(d.job.id);
        setJob(j.job);
        if (j.job?.status !== 'running') {
          clearInterval(poll.current);
          setBusy(false);
          if (j.job?.status === 'done') {
            setResult(j.job.result);
            api.rangeRuns().then((r) => setRuns(r?.runs || [])).catch(() => {});
          } else setErr(j.job?.error || 'the run failed');
        }
      }, 1500);
    } catch (e) { setErr(String(e.message || e)); setBusy(false); }
  };
  useEffect(() => () => clearInterval(poll.current), []);

  const live = desk?.mode === 'live';
  const s = result?.summary;
  const lv = desk?.levels || {};

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-white flex items-center gap-2">
            <Ruler className="w-5 h-5 text-brand-400" /> 5 &amp; 60 Minute Range
          </h1>
          <p className="text-[13px] text-gray-400 mt-1 max-w-4xl">
            Two opening ranges from the index — the first 5 minutes and the first 60. Before 10:15
            the 5-minute range is in force, after it the 60-minute one. A cross buys an option at
            the strike you choose.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`text-[11px] px-2 py-0.5 rounded-full ${desk?.connected
            ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'}`}>
            {desk?.connected ? 'Zerodha connected' : 'not connected'}
          </span>
          <span className={`text-[11px] px-2 py-0.5 rounded-full ${live
            ? 'bg-red-500/20 text-red-300 font-semibold' : 'bg-surface-3 text-gray-300'}`}>
            {live ? 'LIVE ORDERS' : 'paper'}
          </span>
          <span className="text-[11px] text-gray-500 mono">{desk?.now}</span>
          {desk?.running
            ? <button disabled={busy} onClick={() => act(api.rangeStop)}
                      className="btn-secondary !py-1.5 !px-3 text-[12.5px] flex items-center gap-1.5">
                <Square className="w-3.5 h-3.5" />Stop</button>
            : <button disabled={busy || !desk?.connected} onClick={() => act(api.rangeStart)}
                      className="btn-primary !py-1.5 !px-3 text-[12.5px] flex items-center gap-1.5">
                <Play className="w-3.5 h-3.5" />Start</button>}
        </div>
      </div>

      {err && <div className="text-[12px] text-red-400 flex items-start gap-1.5">
        <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />{err}</div>}

      <div className="flex gap-1 border-b border-surface-3">
        {TABS.map((t) => {
          const Icon = t.icon;
          return (
            <button key={t.id} onClick={() => setTab(t.id)}
                    className={`px-3 py-2 text-[13px] flex items-center gap-1.5 border-b-2 -mb-px transition-colors ${
                      tab === t.id ? 'border-brand-500 text-brand-400' : 'border-transparent text-gray-400 hover:text-white'}`}>
              <Icon className="w-3.5 h-3.5" />{t.label}
            </button>
          );
        })}
      </div>

      {tab === 'desk' && (
        <>
          <Section title="Today" right={
            <select value={desk?.mode || 'paper'} onChange={(e) => act(() => api.rangeConfig({ mode: e.target.value }))}
                    className={input}>
              <option value="paper">paper</option><option value="live">live</option>
            </select>}>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              <Stat label="Range in force" value={desk?.in_force === 'B' ? '60 min' : '5 min'}
                    sub={`switches at ${desk?.window?.switch_at || '10:15'}`} />
              <Stat label="5-min range"
                    value={lv.A ? `${N0(lv.A.low)}–${N0(lv.A.high)}` : '—'}
                    sub={lv.A ? `09:15–09:19` : 'not formed yet'} />
              <Stat label="60-min range"
                    value={lv.B ? `${N0(lv.B.low)}–${N0(lv.B.high)}` : '—'}
                    sub={lv.B ? `09:15–10:14` : 'not formed yet'} />
              <Stat label="Trades" value={desk?.totals?.trades ?? 0}
                    sub={`${RS(desk?.totals?.spent || 0)} deployed`} />
              <Stat label="P&L" value={RS((desk?.totals?.realised || 0) + (desk?.totals?.unrealised || 0))}
                    tone={tone((desk?.totals?.realised || 0) + (desk?.totals?.unrealised || 0))}
                    sub={`${RS(desk?.totals?.realised || 0)} booked`} />
            </div>
            <div className="mt-3"><Note>{desk?.state || desk?.log?.slice(-1)[0] || 'Press Start to arm the desk.'}</Note></div>
          </Section>

          {!!desk?.tickets?.length && (
            <Section title="Trades">
              <div className="overflow-x-auto">
                <table className="w-full text-[12.5px]">
                  <thead className="text-gray-500 text-[11px] uppercase tracking-wide">
                    <tr>{['Taken', 'Contract', 'Why', 'Lots', 'Entry', 'Now', 'Target', 'Stop', 'Exit', 'P&L']
                      .map((h) => <th key={h} className="text-left font-medium py-1.5 pr-3">{h}</th>)}</tr>
                  </thead>
                  <tbody className="mono">
                    {desk.tickets.map((t, i) => (
                      <tr key={i} className="border-t border-surface-3">
                        <td className="py-1.5 pr-3">{t.taken_at}</td>
                        <td className="py-1.5 pr-3">{t.side} {N0(t.strike)}</td>
                        <td className="py-1.5 pr-3 text-gray-400 font-sans">{t.why}</td>
                        <td className="py-1.5 pr-3">{t.lots}</td>
                        <td className="py-1.5 pr-3">{N(t.entry)}</td>
                        <td className="py-1.5 pr-3">{t.ltp != null ? N(t.ltp) : '—'}</td>
                        <td className="py-1.5 pr-3 text-emerald-400">{N(t.target)}</td>
                        <td className="py-1.5 pr-3 text-red-400">{N(t.stop)}</td>
                        <td className="py-1.5 pr-3">{t.closed ? `${N(t.exit)} (${t.exit_reason})` : '—'}</td>
                        <td className={`py-1.5 pr-3 ${tone(t.pnl ?? t.unrealised)}`}>
                          {RS(t.pnl ?? t.unrealised ?? 0)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Section>
          )}

          <Settings params={params} meta={meta} onChange={saveParams} />

          {!!desk?.log?.length && (
            <Section title="What it did">
              <pre className="text-[11.5px] text-gray-400 mono whitespace-pre-wrap leading-relaxed">
                {desk.log.slice().reverse().join('\n')}</pre>
            </Section>
          )}
        </>
      )}

      {tab === 'backtest' && (
        <>
          <Section title="Run it on stored data" right={
            <button disabled={busy} onClick={runBacktest} className="btn-primary !py-1.5 !px-3 text-[12.5px] flex items-center gap-1.5">
              {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              Run backtest</button>}>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <Field label="From"><input type="date" value={range.start}
                onChange={(e) => setRange({ ...range, start: e.target.value })} className={input} /></Field>
              <Field label="To"><input type="date" value={range.end}
                onChange={(e) => setRange({ ...range, end: e.target.value })} className={input} /></Field>
              <Field label="Today only"><button onClick={() => setRange({ start: today(), end: today() })}
                className="btn-secondary w-full !py-2 text-[12.5px]">Set to today</button></Field>
              <Field label="Last 12 months"><button onClick={() => setRange({ start: daysAgo(365), end: today() })}
                className="btn-secondary w-full !py-2 text-[12.5px]">Set</button></Field>
            </div>
            {job?.status === 'running' && <div className="mt-3"><Note>{job.progress}</Note></div>}
          </Section>

          <Settings params={params} meta={meta} onChange={saveParams} />

          {s && (
            <Section title="Result" right={result?.id ? (
              <div className="flex items-center gap-1.5">
                {[['trades', 'Trades'], ['monthly', 'Monthly'], ['summary', 'Summary']].map(([w, lbl]) => (
                  <button key={w} onClick={() => exportCsv(result.id, w)}
                          className="btn-secondary !py-1 !px-2 text-[11.5px] flex items-center gap-1">
                    <Download className="w-3 h-3" />{lbl}
                  </button>
                ))}
              </div>
            ) : null}>
              {s.trades ? (
                <>
                  <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
                    <Stat label="Net" value={RS(s.net)} tone={tone(s.net)} sub={`${s.trades} trades`} />
                    <Stat label="Per trade" value={RS(s.per_trade)} tone={tone(s.per_trade)}
                          sub={`${PCT(s.win_rate)} of trades win · ${RSA(s.charges / Math.max(s.trades, 1))} cost each`} />
                    <Stat label="Gross" value={RS(s.gross)} tone={tone(s.gross)}
                          sub={`less ${RSA(s.charges)} charges`} />
                    <Stat label="Green months" value={`${s.green_months}/${s.months}`}
                          tone={tone(s.median_month)}
                          sub={`median month ${RS(s.median_month)} — not a win rate`} />
                    <Stat label="Worst day" value={RS(s.worst_day)} tone="text-red-400"
                          sub={`best ${RS(s.best_day)}`} />
                    <Stat label="Max drawdown" value={RSA(s.max_drawdown)} tone="text-red-400"
                          sub={`worst peak-to-trough · ${s.traded_days} days traded`} />
                  </div>
                  {s.charges > Math.abs(s.gross) * 0.5 && (
                    <div className="mt-3"><Note>
                      Charges are {PCT(s.charges / Math.abs(s.gross) * 100)} of the gross — at this
                      trade count costs, not the signal, decide the outcome.
                    </Note></div>
                  )}
                  <div className="mt-3 text-[12px] text-gray-400">
                    Exits: {Object.entries(s.by_reason || {}).map(([k, v]) => `${k} ${v}`).join(' · ')}
                    {' · '}Sides: {Object.entries(s.by_side || {}).map(([k, v]) => `${k} ${v}`).join(' · ')}
                  </div>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {Object.entries(s.monthly || {}).map(([m, v]) => (
                      <span key={m} className={`text-[11px] px-2 py-0.5 rounded mono ${v >= 0
                        ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'}`}>
                        {m} {RS(v)}</span>
                    ))}
                  </div>
                </>
              ) : (
                <Note>{s.note || 'No trade was taken.'}
                  {!!s.why_not?.length && <span className="block mt-1 text-gray-500">{s.why_not.join(' · ')}</span>}
                </Note>
              )}
            </Section>
          )}

          {!!result?.trades?.length && (
            <Section title={`Trades (${result.trades.length})`} right={result?.id ? (
              <button onClick={() => exportCsv(result.id, 'trades')}
                      className="btn-secondary !py-1 !px-2 text-[11.5px] flex items-center gap-1">
                <Download className="w-3 h-3" />Download all as CSV
              </button>) : null}>
              <div className="overflow-x-auto max-h-[460px]">
                <table className="w-full text-[12px]">
                  <thead className="text-gray-500 text-[11px] uppercase tracking-wide sticky top-0 bg-surface-1">
                    <tr>{['Date', 'Signal', 'Range', 'Level', 'Side', 'Strike', 'Entry', 'Exit', 'Why out', 'Held', 'P&L']
                      .map((h) => <th key={h} className="text-left font-medium py-1.5 pr-3">{h}</th>)}</tr>
                  </thead>
                  <tbody className="mono">
                    {result.trades.map((t, i) => (
                      <tr key={i} className="border-t border-surface-3">
                        <td className="py-1 pr-3">{t.date}</td>
                        <td className="py-1 pr-3">{t.time}</td>
                        <td className="py-1 pr-3">{t.range === 'A' ? '5m' : '60m'}</td>
                        <td className="py-1 pr-3">{t.level} {N0(t.level_price)}</td>
                        <td className="py-1 pr-3">{t.side}</td>
                        <td className="py-1 pr-3">{N0(t.strike)}</td>
                        <td className="py-1 pr-3">{N(t.entry)}</td>
                        <td className="py-1 pr-3">{N(t.exit)}</td>
                        <td className="py-1 pr-3">{t.exit_reason}</td>
                        <td className="py-1 pr-3">{t.held_min}m</td>
                        <td className={`py-1 pr-3 ${tone(t.pnl)}`}>{RS(t.pnl)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Section>
          )}

          {!!runs.length && (
            <Section title="Saved runs">
              <div className="space-y-1">
                {runs.map((r) => (
                  <div key={r.id} className="flex flex-wrap items-center gap-3 text-[12px] py-1 border-b border-surface-3">
                    <span className="text-gray-500 mono">{r.start} → {r.end}</span>
                    <span className="text-gray-400">{r.mode} · {r.moneyness}</span>
                    <span className="mono">{r.trades} trades</span>
                    <span className={`mono ${tone(r.net)}`}>{RS(r.net)}</span>
                    <span className="text-gray-500">{r.green_months}/{r.months} green months</span>
                    <button onClick={() => exportCsv(r.id, 'trades')}
                            className="ml-auto btn-secondary !py-0.5 !px-2 text-[11px] flex items-center gap-1">
                      <Download className="w-3 h-3" />CSV
                    </button>
                  </div>
                ))}
              </div>
            </Section>
          )}
        </>
      )}

      {tab === 'guide' && (
        <>
          <Section title="How to reproduce a number">
            <ol className="space-y-1.5 text-[13px] text-gray-300 list-decimal list-inside">
              <li>Pick a preset below and press <b>Apply</b>. It sets every setting <i>and</i> the
                  date window the number was measured on.</li>
              <li>You land on the Backtest tab with everything filled in. Press <b>Run backtest</b>.</li>
              <li>The result should match the preset's row. If it does not, the market store has
                  been refreshed or extended since the row was recorded — the fresh run is the
                  truthful one.</li>
            </ol>
            {guide?.window && (
              <div className="mt-3 text-[12px] text-gray-400">
                Every number below was measured on{' '}
                <span className="mono text-gray-300">{guide.window.start} → {guide.window.end}</span>,{' '}
                {guide.window.note}. Net of real brokerage, STT and one tick of slippage per side.
              </div>
            )}
          </Section>

          <Section title="Measured configurations">
            <div className="overflow-x-auto">
              <table className="w-full text-[12.5px]">
                <thead className="text-gray-500 text-[11px] uppercase tracking-wide">
                  <tr>{['', 'Configuration', 'Trades', 'Net', 'Per trade', 'Win', 'Green months', 'Max drawdown', 'Confidence']
                    .map((h) => <th key={h} className="text-left font-medium py-1.5 pr-3">{h}</th>)}</tr>
                </thead>
                <tbody>
                  {(guide?.presets || []).map((ps) => (
                    <tr key={ps.id} className="border-t border-surface-3 align-top">
                      <td className="py-2 pr-3">
                        <button onClick={() => applyPreset(ps)}
                                className="btn-secondary !py-1 !px-2 text-[11.5px] flex items-center gap-1">
                          {applied === ps.id ? <Check className="w-3 h-3" /> : null}Apply
                        </button>
                      </td>
                      <td className="py-2 pr-3">
                        <div className="text-gray-200">{ps.name}</div>
                        <div className="text-[11.5px] text-gray-500 max-w-md">{ps.note}</div>
                      </td>
                      <td className="py-2 pr-3 mono">{ps.result.trades}</td>
                      <td className={`py-2 pr-3 mono ${tone(ps.result.net)}`}>{RS(ps.result.net)}</td>
                      <td className={`py-2 pr-3 mono ${tone(ps.result.per_trade)}`}>{RS(ps.result.per_trade)}</td>
                      <td className="py-2 pr-3 mono">{PCT(ps.result.win_rate)}</td>
                      <td className="py-2 pr-3 mono">{ps.result.green_months}/{ps.result.months}</td>
                      <td className="py-2 pr-3 mono text-red-400">{RSA(ps.result.max_drawdown)}</td>
                      <td className="py-2 pr-3 mono text-gray-400">
                        {ps.result.p_profit ? `${ps.result.p_profit}%` : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-[11.5px] text-gray-500 mt-2">
              Confidence is the share of 5,000 bootstrap resamples of the daily results that stayed
              profitable. Blank where it was not computed.
            </p>
          </Section>

          <Section title="What each filter does">
            <div className="space-y-3">
              {(guide?.filters || []).map((f) => (
                <div key={f.key} className="border-l-2 border-surface-3 pl-3">
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="text-[13px] font-semibold text-gray-100">{f.name}</span>
                    <span className="text-[11px] text-gray-500 mono">{f.key}</span>
                    <span className="text-[11px] text-gray-500">({f.unit})</span>
                  </div>
                  <div className="text-[12.5px] text-gray-300 mt-1">{f.what}</div>
                  <div className="text-[12.5px] text-gray-400 mt-1"><b className="text-gray-500">Why: </b>{f.why}</div>
                  <div className="text-[12.5px] text-gray-400 mt-1"><b className="text-gray-500">Measured: </b>{f.measured}</div>
                  <div className="text-[12.5px] text-emerald-400/90 mt-1"><b className="text-gray-500">Use: </b>{f.suggested}</div>
                </div>
              ))}
            </div>
          </Section>

          <Section title="What these numbers do not prove">
            <ul className="space-y-1.5 text-[12.5px] text-gray-300 list-disc list-inside">
              {(guide?.caveats || []).map((c, i) => <li key={i}>{c}</li>)}
            </ul>
          </Section>
        </>
      )}

      {tab === 'rules' && (
        <Section title="What this does">
          <ol className="space-y-2 text-[13px] text-gray-300 list-decimal list-inside">
            {(meta?.rules || desk?.rules || []).map((r, i) => <li key={i}>{r}</li>)}
          </ol>
          <div className="mt-4"><Note>
            The written spec says “cross above the range low buys a call”. Taken literally that is a
            reversal — price leaves the range and comes back. <b>Reversal</b> mode follows the spec
            as written; <b>breakout</b> mode does the opposite (above the high buys a call). Both
            are backtestable, and on stored NIFTY data the reversal reading is the better of the two.
          </Note></div>
          <div className="mt-2"><Note>
            A range can only be used once every one of its minutes has printed, a cross is judged on
            a closed bar, and the fill is the next bar’s open. The backtest prices the real contract
            from the market store and charges real brokerage, STT and slippage.
          </Note></div>
        </Section>
      )}
    </div>
  );
}

function Settings({ params, meta, onChange }) {
  if (!params) return null;
  const num = (k) => (e) => onChange({ [k]: Number(e.target.value) });
  return (
    <Section title="Settings">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Field label="Index">
          <select value={params.index} onChange={(e) => onChange({ index: e.target.value })} className={input}>
            {(meta?.indices || ['NIFTY']).map((i) => <option key={i} value={i}>{i}</option>)}
          </select>
        </Field>
        <Field label="Reading" hint="how a cross is judged">
          <select value={params.mode} onChange={(e) => onChange({ mode: e.target.value })} className={input}>
            <option value="reversal">reversal (as written)</option>
            <option value="breakout">breakout</option>
          </select>
        </Field>
        <Field label="Strike">
          <select value={params.moneyness} onChange={(e) => onChange({ moneyness: e.target.value })} className={input}>
            <option value="ITM">ITM</option><option value="ATM">ATM</option><option value="OTM">OTM</option>
          </select>
        </Field>
        <Field label="Strike offset" hint="index points from spot">
          <input type="number" step="50" value={params.strike_offset}
                 onChange={num('strike_offset')} className={input}
                 disabled={String(params.moneyness).toUpperCase() === 'ATM'} />
        </Field>
        <Field label="Sides">
          <select value={params.sides} onChange={(e) => onChange({ sides: e.target.value })} className={input}>
            <option value="both">call and put</option><option value="call">calls only</option>
            <option value="put">puts only</option>
          </select>
        </Field>

        <Field label="Cross buffer" hint="points the close must clear the level by">
          <input type="number" step="1" value={params.cross_buffer ?? 0} onChange={num('cross_buffer')} className={input} />
        </Field>
        <Field label="Min range width" hint="skip a narrower range (0 = off)">
          <input type="number" step="10" value={params.min_range ?? 0} onChange={num('min_range')} className={input} />
        </Field>
        <Field label="Max range width" hint="skip a wider range (0 = off)">
          <input type="number" step="10" value={params.max_range ?? 0} onChange={num('max_range')} className={input} />
        </Field>

        <Field label="Target (points)"><input type="number" value={params.target_points} onChange={num('target_points')} className={input} /></Field>
        <Field label="Stop (points)"><input type="number" value={params.stop_points} onChange={num('stop_points')} className={input} /></Field>
        <Field label="Measured on">
          <select value={params.exit_on_index ? 'index' : 'premium'}
                  onChange={(e) => onChange({ exit_on_index: e.target.value === 'index' })} className={input}>
            <option value="premium">the option premium</option><option value="index">the index</option>
          </select>
        </Field>
        <Field label="Lots"><input type="number" min="1" value={params.lots} onChange={num('lots')} className={input} /></Field>
        <Field label="Max trades a day"><input type="number" min="1" value={params.max_trades_per_day} onChange={num('max_trades_per_day')} className={input} /></Field>

        <Field label="Range A (min)"><input type="number" value={params.range_a_min} onChange={num('range_a_min')} className={input} /></Field>
        <Field label="Range B (min)"><input type="number" value={params.range_b_min} onChange={num('range_b_min')} className={input} /></Field>
        <Field label="Switch at"><input type="number" value={params.switch_at} onChange={num('switch_at')} className={input} /></Field>
        <Field label="Last entry"><input type="number" value={params.last_entry} onChange={num('last_entry')} className={input} /></Field>
        <Field label="Square-off"><input type="number" value={params.squareoff} onChange={num('squareoff')} className={input} /></Field>
      </div>
      <p className="text-[11.5px] text-gray-500 mt-2">
        Times are minutes from midnight: 09:20 is 560, 10:15 is 615, 14:30 is 870, 15:15 is 915.
      </p>
    </Section>
  );
}
