import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity, AlertTriangle, CheckCircle2, Database, Download, Info, Loader2, Play,
  RefreshCw, Shield, ShieldAlert, TrendingDown, TrendingUp,
} from 'lucide-react';
import { api } from '../../api';

/**
 * NIFTY Futures Volume Breakout Lab.
 *
 * The page is arranged around what the data can actually support, which is why the inventory
 * sits at the top rather than in a settings drawer: futures volume has never been ingested, and
 * a page that let you pick it without saying so would waste a three-year backtest to tell you
 * nothing.
 *
 * Nothing here is mocked. Where a number is unavailable the panel says so and names the series
 * that is missing.
 */

const N = (v, d = 0) => (v == null || Number.isNaN(Number(v)) ? '—'
  : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
const RS = (v) => (v == null ? '—' : `${Number(v) < 0 ? '−' : '+'}₹${Math.abs(Number(v)).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`);
const RSA = (v) => (v == null ? '—' : `₹${Math.abs(Number(v)).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`);
const PCT = (v, d = 1) => (v == null ? '—' : `${Number(v).toFixed(d)}%`);
const tone = (v) => (v == null ? 'text-gray-400' : v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-gray-300');
const SEL = 'input-field !py-1 !px-2 text-[12px]';

const STATE_TONE = {
  IDLE: 'text-gray-400', BREAKOUT_CANDIDATE: 'text-brand-300',
  WAITING_FOR_CONFIRMATION: 'text-brand-300', WAITING_FOR_RETEST: 'text-brand-300',
  VALID_BREAKOUT: 'text-emerald-400', POTENTIAL_FALSE_BREAKOUT: 'text-amber-300',
  FAILED_BREAKOUT_CONFIRMED: 'text-red-400', ENTRY_BLOCKED: 'text-red-400',
  COOLDOWN: 'text-gray-500',
};

export default function FvbLab() {
  const [meta, setMeta] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [tab, setTab] = useState('today');
  const [job, setJob] = useState(null);
  const [result, setResult] = useState(null);
  const [compare, setCompare] = useState(null);
  const [desk, setDesk] = useState(null);
  const [today, setToday] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const poll = useRef(null);
  const deskTimer = useRef(null);

  useEffect(() => {
    api.fvbMeta().then((m) => {
      setMeta(m);
      if (m?.defaults) setCfg({ ...m.defaults, compare_variants: false });
    }).catch((e) => setErr(String(e.message || e)));
  }, []);

  const loadDesk = useCallback(() => {
    api.fvbDesk().then(setDesk).catch(() => {});
  }, []);
  useEffect(() => { loadDesk(); }, [loadDesk]);

  // Today is never in the stored history, so this asks the broker for the live session instead
  // of the store. It is the answer to "where are the levels right now", which a backtest dated
  // today can never give.
  const loadToday = useCallback(() => {
    setToday(null);
    api.fvbToday({ variant: cfg?.variant || 'B', participation: cfg?.participation || 'options' })
      .then(setToday).catch((e) => setErr(String(e.message || e)));
  }, [cfg?.variant, cfg?.participation]);
  useEffect(() => { if (cfg) loadToday(); }, [cfg?.variant, cfg?.participation]);  // eslint-disable-line
  useEffect(() => {
    clearInterval(deskTimer.current);
    if (tab !== 'live' || !desk?.running) return undefined;
    deskTimer.current = setInterval(loadDesk, 8000);
    return () => clearInterval(deskTimer.current);
  }, [tab, desk?.running, loadDesk]);

  const set = (k, v) => setCfg((c) => ({ ...c, [k]: v }));
  const setIn = (group, k, v) => setCfg((c) => ({ ...c, [group]: { ...(c[group] || {}), [k]: v } }));

  const run = async (comparing) => {
    setBusy(true); setErr(''); setMsg(''); setResult(null); setCompare(null);
    try {
      const r = await api.fvbBacktest({ ...cfg, compare_variants: !!comparing });
      if (r.status !== 'ok') throw new Error(r.message);
      setJob({ id: r.job_id, comparing: r.comparing, progress: 'starting' });
      clearInterval(poll.current);
      poll.current = setInterval(async () => {
        const j = await api.fvbJob(r.job_id);
        setJob((p) => ({ ...p, ...j }));
        if (j.state === 'done') {
          clearInterval(poll.current);
          setBusy(false);
          if (r.comparing) { setCompare(j.result); setTab('variants'); }
          else { setResult(j.result); setTab('results'); }
        } else if (j.state === 'error') {
          clearInterval(poll.current); setBusy(false); setErr(j.error || 'the run failed');
        }
      }, 1500);
    } catch (e) { setErr(String(e.message || e)); setBusy(false); }
  };

  const pullFutures = async () => {
    setBusy(true); setMsg(''); setErr('');
    try {
      const r = await api.fvbIngestFutures(5);
      if (r.status !== 'ok') throw new Error(r.message);
      setMsg(`Futures pull started (${r.job?.id}). ${r.note}`);
    } catch (e) { setErr(String(e.message || e)); } finally { setBusy(false); }
  };

  const updateDesk = async (changes) => {
    setBusy(true); setErr('');
    try {
      const d = await api.fvbDeskUpdate(changes);
      if (d.status !== 'ok') throw new Error(d.message);
      setDesk(d);
    } catch (e) { setErr(String(e.message || e)); } finally { setBusy(false); }
  };

  const inv = meta?.inventory;
  const futuresOut = inv && !inv.futures?.available;
  // the single most confusing failure this lab can produce is a silent zero-trade run on a
  // range the store does not cover, so it is caught before the button is pressed
  const outOfRange = !!(inv?.spot?.last && cfg?.start && cfg?.end
    && (cfg.end < inv.spot.first || cfg.start > inv.spot.last));
  const staleDays = inv?.spot?.last
    ? Math.round((Date.now() - new Date(`${inv.spot.last}T00:00:00`).getTime()) / 86400000)
    : 0;
  const staleData = staleDays > 7;

  if (!meta || !cfg) {
    return <div className="text-[13px] text-gray-500 flex items-center gap-2">
      <Loader2 className="w-4 h-4 animate-spin" />Loading the lab…</div>;
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold text-white flex items-center gap-2">
            <Activity className="w-5 h-5 text-brand-400" />NIFTY Futures Volume Breakout Lab
          </h1>
          <p className="text-xs sm:text-sm text-gray-500 mt-0.5 max-w-4xl">
            Opening-range and structural breakouts, confirmed by participation and policed by an
            independent false-breakout firewall. The lab is built to be able to say no trade.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {['today', 'setup', 'results', 'variants', 'firewall', 'live', 'data'].map((t) => (
            <button key={t} onClick={() => setTab(t)}
              className={`px-2.5 py-1 text-[12px] rounded-lg border ${tab === t
                ? 'bg-brand-500 text-white border-brand-500'
                : 'border-surface-3 text-gray-400 hover:text-white'}`}>
              {{ today: 'Today', setup: 'Setup', results: 'Results',
                 variants: 'Variants', firewall: 'Firewall', live: 'Live desk',
                 data: 'Data' }[t]}
            </button>
          ))}
        </div>
      </div>

      {err && <Note tone="bad"><AlertTriangle className="w-3.5 h-3.5 inline mr-1.5 -mt-0.5" />{err}</Note>}
      {msg && <Note tone="ok"><CheckCircle2 className="w-3.5 h-3.5 inline mr-1.5 -mt-0.5" />{msg}</Note>}

      {outOfRange && (
        <Note tone="bad">
          <AlertTriangle className="w-3.5 h-3.5 inline mr-1.5 -mt-0.5" />
          <b>There is no data for {cfg.start} to {cfg.end}.</b> The store holds{' '}
          <span className="mono">{inv?.spot?.first}</span> to{' '}
          <span className="mono">{inv?.spot?.last}</span>. A backtest over this range returns
          nothing because nothing was loaded — that is a data gap, not a result. Pull fresh
          candles in the Data Ingestion Lab, or
          <button onClick={() => { set('start', inv.spot.first); set('end', inv.spot.last); }}
            className="ml-1 underline hover:text-white">use the full stored range</button>.
        </Note>
      )}

      {staleData && !outOfRange && (
        <Note tone="warn">
          The newest stored session is <span className="mono">{inv?.spot?.last}</span>, which is
          {' '}{staleDays} days old. Anything after that cannot be tested until the store is
          refreshed.
        </Note>
      )}

      {futuresOut && (
        <Note tone="warn">
          <b>Futures volume has never been ingested.</b> The strategy was designed around NIFTY
          futures participation, and without <span className="mono">spot/NIFTYFUT</span> that
          filter reports <span className="mono">UNAVAILABLE</span> and every signal needing it is
          declined — which is the honest outcome, not a bug. Option volume near the money is real
          traded volume and is offered as its own source under its own name; it is a different
          measure and is never presented as futures participation.
          <button onClick={pullFutures} disabled={busy}
            className="ml-2 btn-secondary !py-0.5 !px-2 text-[11.5px]">Pull futures now</button>
        </Note>
      )}

      {tab === 'today' && <TodayView t={today} onReload={loadToday} />}
      {tab === 'setup' && (
        <Setup cfg={cfg} set={set} setIn={setIn} meta={meta} onRun={run} busy={busy}
          job={job} outOfRange={outOfRange} />
      )}
      {tab === 'results' && <Results res={result} />}
      {tab === 'variants' && <Variants cmp={compare} onRun={() => run(true)} busy={busy} job={job} />}
      {tab === 'firewall' && <FirewallView res={result} />}
      {tab === 'live' && <LiveDesk desk={desk} meta={meta} onChange={updateDesk} busy={busy} />}
      {tab === 'data' && <DataTab inv={inv} meta={meta} onPull={pullFutures} busy={busy} />}
    </div>
  );
}

/* ── setup ─────────────────────────────────────────────────────────────── */
function Setup({ cfg, set, setIn, meta, onRun, busy, job, outOfRange }) {
  const p = cfg.params || {}, e = cfg.entry || {}, x = cfg.execution || {}, f = cfg.firewall_params || {};
  return (
    <div className="space-y-4">
      <Card title="What to run">
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          <Field label="From" hint={meta.inventory?.spot?.first ? `data starts ${meta.inventory.spot.first}` : undefined}>
            <input type="date" value={cfg.start || ''} className={SEL}
              min={meta.inventory?.spot?.first} max={meta.inventory?.spot?.last}
              onChange={(ev) => set('start', ev.target.value)} /></Field>
          <Field label="To" hint={meta.inventory?.spot?.last ? `data ends ${meta.inventory.spot.last}` : undefined}>
            <input type="date" value={cfg.end || ''} className={SEL}
              min={meta.inventory?.spot?.first} max={meta.inventory?.spot?.last}
              onChange={(ev) => set('end', ev.target.value)} /></Field>
          <Field label="Candle" hint="minutes">
            <select value={cfg.timeframe} className={SEL}
              onChange={(ev) => set('timeframe', Number(ev.target.value))}>
              {[1, 3, 5, 15].map((v) => <option key={v} value={v}>{v} minute</option>)}
            </select>
          </Field>
          <Field label="Variant">
            <select value={cfg.variant} className={SEL} onChange={(ev) => set('variant', ev.target.value)}>
              {(meta.variants || []).map((v) => <option key={v.key} value={v.key}>{v.key} — {v.label}</option>)}
            </select>
          </Field>
          <Field label="Participation" hint="what counts as taking part">
            <select value={cfg.participation} className={SEL}
              onChange={(ev) => set('participation', ev.target.value)}>
              {(meta.participation_sources || []).map((s) => (
                <option key={s.key} value={s.key} disabled={!s.available}>
                  {s.label}{s.available ? '' : ' — not ingested'}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Firewall">
            <select value={cfg.firewall ? '1' : '0'} className={SEL}
              onChange={(ev) => set('firewall', ev.target.value === '1')}>
              <option value="1">On</option><option value="0">Off</option>
            </select>
          </Field>
        </div>
      </Card>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Card title="Levels, buffer and participation">
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <Field label="Opening range" hint="minutes from 09:15">
              <input type="number" value={p.or_minutes ?? 15} className={SEL}
                onChange={(ev) => setIn('params', 'or_minutes', Number(ev.target.value))} /></Field>
            <Field label="Buffer mode">
              <select value={p.buffer_mode || 'atr'} className={SEL}
                onChange={(ev) => setIn('params', 'buffer_mode', ev.target.value)}>
                <option value="atr">ATR multiple</option><option value="points">Points</option>
                <option value="percent">Percent</option>
              </select></Field>
            <Field label="Buffer size" hint={p.buffer_mode === 'points' ? 'index points' : p.buffer_mode === 'percent' ? '% of price' : '× ATR'}>
              <input type="number" step="0.05" className={SEL}
                value={p.buffer_mode === 'points' ? (p.buffer_points ?? 10)
                  : p.buffer_mode === 'percent' ? (p.buffer_percent ?? 0.05) : (p.buffer_atr_mult ?? 0.15)}
                onChange={(ev) => setIn('params', p.buffer_mode === 'points' ? 'buffer_points'
                  : p.buffer_mode === 'percent' ? 'buffer_percent' : 'buffer_atr_mult',
                  Number(ev.target.value))} /></Field>
            <Field label="RVOL lookback" hint="completed candles">
              <input type="number" value={p.rvol_lookback ?? 20} className={SEL}
                onChange={(ev) => setIn('params', 'rvol_lookback', Number(ev.target.value))} /></Field>
            <Field label="RVOL threshold">
              <select value={p.rvol_threshold ?? 1.5} className={SEL}
                onChange={(ev) => setIn('params', 'rvol_threshold', Number(ev.target.value))}>
                {(meta.rvol_threshold_experiments || [1.5]).map((v) => (
                  <option key={v} value={v}>{v}×</option>))}
              </select></Field>
            <Field label="Normalise by time" hint="same minute on earlier days">
              <select value={p.rvol_time_normalised ? '1' : '0'} className={SEL}
                onChange={(ev) => setIn('params', 'rvol_time_normalised', ev.target.value === '1')}>
                <option value="0">No</option><option value="1">Yes</option>
              </select></Field>
          </div>
        </Card>

        <Card title="Entry">
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <Field label="Mode">
              <select value={e.mode || 'confirmation'} className={SEL}
                onChange={(ev) => setIn('entry', 'mode', ev.target.value)}>
                <option value="confirmation">Breakout confirmation</option>
                <option value="retest">Breakout then retest</option>
              </select></Field>
            <Field label="Trades a day">
              <input type="number" value={e.max_trades_per_day ?? 2} className={SEL}
                onChange={(ev) => setIn('entry', 'max_trades_per_day', Number(ev.target.value))} /></Field>
            <Field label="Minimum reward:risk">
              <input type="number" step="0.25" value={e.min_rr ?? 1} className={SEL}
                onChange={(ev) => setIn('entry', 'min_rr', Number(ev.target.value))} /></Field>
            <Field label="Retest within" hint="candles">
              <input type="number" value={e.retest_within ?? 15} className={SEL}
                onChange={(ev) => setIn('entry', 'retest_within', Number(ev.target.value))} /></Field>
            <Field label="Allow CE">
              <select value={e.allow_bull === false ? '0' : '1'} className={SEL}
                onChange={(ev) => setIn('entry', 'allow_bull', ev.target.value === '1')}>
                <option value="1">Yes</option><option value="0">No</option></select></Field>
            <Field label="Allow PE">
              <select value={e.allow_bear === false ? '0' : '1'} className={SEL}
                onChange={(ev) => setIn('entry', 'allow_bear', ev.target.value === '1')}>
                <option value="1">Yes</option><option value="0">No</option></select></Field>
          </div>
        </Card>

        <Card title="Firewall">
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <Field label="Failure window" hint="candles to close back through">
              <input type="number" value={f.failure_window ?? 3} className={SEL}
                onChange={(ev) => setIn('firewall_params', 'failure_window', Number(ev.target.value))} /></Field>
            <Field label="Cooldown" hint="bars after a failure">
              <input type="number" value={f.cooldown_bars ?? 15} className={SEL}
                onChange={(ev) => setIn('firewall_params', 'cooldown_bars', Number(ev.target.value))} /></Field>
            <Field label="Warnings to block">
              <input type="number" min={1} value={f.warnings_to_block ?? 1} className={SEL}
                onChange={(ev) => setIn('firewall_params', 'warnings_to_block', Number(ev.target.value))} /></Field>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 mt-3">
            {[['warn_on_wick_reject', 'Rejection wick or weak close'],
              ['warn_on_no_participation', 'Participation missing or below threshold'],
              ['warn_on_futures_disagree', 'Futures disagree with the index'],
              ['warn_on_immediate_reclaim', 'The level is immediately reclaimed'],
              ['warn_on_choppy', 'The session is choppy'],
              ['block_on_warning', 'A warning blocks the entry']].map(([k, label]) => (
              <label key={k} className="flex items-center gap-2 text-[12px] text-gray-300 cursor-pointer">
                <input type="checkbox" checked={f[k] !== false}
                  onChange={(ev) => setIn('firewall_params', k, ev.target.checked)} />
                {label}
              </label>
            ))}
          </div>
        </Card>

        <Card title="Execution and risk">
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <Field label="Strike">
              <select value={x.moneyness ?? 0} className={SEL}
                onChange={(ev) => setIn('execution', 'moneyness', Number(ev.target.value))}>
                <option value={-1}>1 in the money</option><option value={0}>At the money</option>
                <option value={1}>1 out</option><option value={2}>2 out</option>
              </select></Field>
            <Field label="Sizing">
              <select value={x.sizing || 'fixed_lots'} className={SEL}
                onChange={(ev) => setIn('execution', 'sizing', ev.target.value)}>
                <option value="fixed_lots">Fixed lots</option>
                <option value="risk_based">By risk budget</option></select></Field>
            <Field label={x.sizing === 'risk_based' ? 'Risk per trade (₹)' : 'Lots'}>
              <input type="number" className={SEL}
                value={x.sizing === 'risk_based' ? (x.max_risk_rs ?? 5000) : (x.lots ?? 1)}
                onChange={(ev) => setIn('execution', x.sizing === 'risk_based' ? 'max_risk_rs' : 'lots',
                  Number(ev.target.value))} /></Field>
            <Field label="Premium stop (%)" hint="the option's own budget">
              <input type="number" value={x.premium_stop_pct ?? 35} className={SEL}
                onChange={(ev) => setIn('execution', 'premium_stop_pct', Number(ev.target.value))} /></Field>
            <Field label="Slippage (pts)" hint="each fill, both ways">
              <input type="number" step="0.25" value={x.slippage_pts ?? 1} className={SEL}
                onChange={(ev) => setIn('execution', 'slippage_pts', Number(ev.target.value))} /></Field>
            <Field label="Daily loss cap (₹)" hint="0 = off">
              <input type="number" value={x.max_daily_loss_rs ?? 0} className={SEL}
                onChange={(ev) => setIn('execution', 'max_daily_loss_rs', Number(ev.target.value))} /></Field>
          </div>
          <Note tone="info" className="mt-3">
            The underlying's invalidation and the option's premium budget are separate rules and
            are checked separately. Where a bar touched both the stop and the target, the stop is
            taken — only minute OHLC exists, so the order inside the bar is unknowable.
          </Note>
        </Card>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button onClick={() => onRun(false)} disabled={busy || outOfRange}
          className="btn-primary !py-2 !px-4 text-[13px] font-semibold flex items-center gap-2 disabled:opacity-50">
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
          Run backtest
        </button>
        <button onClick={() => onRun(true)} disabled={busy || outOfRange}
          className="btn-secondary !py-2 !px-4 text-[13px] flex items-center gap-2 disabled:opacity-50">
          Compare variants A–E, firewall on and off
        </button>
        {job?.progress && busy && (
          <span className="text-[12px] text-gray-400 mono">{job.progress}</span>
        )}
      </div>
    </div>
  );
}

/* ── results ───────────────────────────────────────────────────────────── */
function Results({ res }) {
  if (!res) return <Note tone="info">Run a backtest to see results here.</Note>;
  const s = res.summary || {};
  if (!s.trades) {
    return (
      <Card title="No trades">
        <Note tone="warn">{s.message || 'Nothing passed every rule.'}</Note>
        <DeclineTable reasons={s.decline_reasons} />
      </Card>
    );
  }
  const stats = [
    ['Net', RS(s.net), tone(s.net)], ['Trades', N(s.trades)],
    ['Win rate', PCT(s.win_rate)], ['Expectancy', RS(s.expectancy), tone(s.expectancy)],
    ['Profit factor', s.profit_factor ?? '—'], ['t-statistic', s.t_stat ?? '—'],
    ['Max drawdown', RS(s.max_drawdown), 'text-red-400'],
    ['Longest losing streak', N(s.longest_losing_streak)],
    ['Gross profit', RSA(s.gross_profit), 'text-emerald-400'],
    ['Gross loss', RSA(s.gross_loss), 'text-red-400'],
    ['Charges', RSA(s.charges), 'text-gray-300'],
    ['Avg hold', `${N(s.avg_hold_min)} min`],
  ];
  return (
    <div className="space-y-4">
      <Card title="Performance" right={<span className="text-[10.5px] text-gray-500">
        {s.sessions} sessions · participation: {s.participation_source}</span>}>
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
          {stats.map(([k, v, t]) => (
            <div key={k} className="rounded-lg border border-surface-3 bg-surface-2/40 p-2.5">
              <div className="text-[10px] uppercase tracking-wider text-gray-500">{k}</div>
              <div className={`text-[15px] font-bold mono mt-0.5 ${t || 'text-gray-100'}`}>{v}</div>
            </div>
          ))}
        </div>
        {(s.t_stat != null && Math.abs(s.t_stat) < 2) && (
          <Note tone="warn" className="mt-3">
            A t-statistic of {s.t_stat} does not clear 2. On this sample the result is not
            statistically distinguishable from chance, whatever the net says.
          </Note>
        )}
      </Card>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Card title="CE versus PE">
          <Table cols={['Side', 'Trades', 'Net', 'Win rate']} align={['left', 'right', 'right', 'right']}>
            {Object.entries(s.by_direction || {}).map(([k, v]) => (
              <tr key={k} className="border-b border-surface-3/40">
                <td className="px-2 py-1.5">{k === 'BULL' ? 'CE (bullish)' : 'PE (bearish)'}</td>
                <td className="px-2 py-1.5 text-right mono">{N(v.trades)}</td>
                <td className={`px-2 py-1.5 text-right mono ${tone(v.net)}`}>{RS(v.net)}</td>
                <td className="px-2 py-1.5 text-right mono">{PCT(v.win_rate)}</td>
              </tr>
            ))}
          </Table>
        </Card>
        <Card title="How positions ended">
          <Table cols={['Reason', 'Count']} align={['left', 'right']}>
            {Object.entries(s.exits || {}).map(([k, v]) => (
              <tr key={k} className="border-b border-surface-3/40">
                <td className="px-2 py-1.5 text-[11.5px]">{k.replace(/_/g, ' ').toLowerCase()}</td>
                <td className="px-2 py-1.5 text-right mono">{N(v)}</td>
              </tr>
            ))}
          </Table>
        </Card>
      </div>

      <Card title="Month by month">
        <Table cols={['Month', 'Trades', 'Net']} align={['left', 'right', 'right']}>
          {(s.monthly || []).map((m) => (
            <tr key={m.month} className="border-b border-surface-3/40">
              <td className="px-2 py-1.5 mono">{m.month}</td>
              <td className="px-2 py-1.5 text-right mono">{N(m.trades)}</td>
              <td className={`px-2 py-1.5 text-right mono ${tone(m.net)}`}>{RS(m.net)}</td>
            </tr>
          ))}
        </Table>
        <div className="text-[11px] text-gray-500 mt-2">
          {s.green_months} of {s.months} months green.
        </div>
      </Card>

      <Card title="Why entries were declined"
        right={<CsvButton rows={res.trades} name="fvb-trades" />}>
        <DeclineTable reasons={s.decline_reasons} total={s.declined} />
      </Card>

      {!!(s.caveats || []).length && (
        <Card title="What these numbers assume">
          <ul className="space-y-1.5 text-[12px] text-gray-400 list-disc list-inside">
            {s.caveats.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </Card>
      )}
    </div>
  );
}

function DeclineTable({ reasons, total }) {
  const rows = Object.entries(reasons || {});
  if (!rows.length) return <Note tone="info">Nothing was declined.</Note>;
  return (
    <>
      {total != null && <div className="text-[11px] text-gray-500 mb-2">{N(total)} declined in total.</div>}
      <Table cols={['Reason', 'Count']} align={['left', 'right']}>
        {rows.sort((a, b) => b[1] - a[1]).map(([k, v]) => (
          <tr key={k} className="border-b border-surface-3/40">
            <td className="px-2 py-1.5 text-[11.5px] text-gray-300">{k}</td>
            <td className="px-2 py-1.5 text-right mono text-gray-400">{N(v)}</td>
          </tr>
        ))}
      </Table>
    </>
  );
}

/* ── variants ──────────────────────────────────────────────────────────── */
function Variants({ cmp, onRun, busy, job }) {
  if (!cmp) {
    return (
      <Card title="Variant comparison">
        <Note tone="info">
          Runs A–E with the firewall on and off — ten backtests. The question it answers is
          whether each filter keeps more of the profit than it keeps of the trades. A filter that
          only trades less is not a filter that works.
        </Note>
        <button onClick={onRun} disabled={busy}
          className="btn-primary !py-2 !px-4 text-[13px] mt-3 flex items-center gap-2 disabled:opacity-50">
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
          Run the comparison
        </button>
        {busy && job?.progress && <div className="text-[12px] text-gray-400 mono mt-2">{job.progress}</div>}
      </Card>
    );
  }
  return (
    <div className="space-y-4">
      <Card title="Every variant, firewall on and off">
        <Table cols={['Variant', 'Firewall', 'Trades', 'Net', 'Win %', 'PF', 't', 'Drawdown', 'Expectancy']}
          align={['left', 'left', 'right', 'right', 'right', 'right', 'right', 'right', 'right']}>
          {(cmp.rows || []).map((r, i) => (
            <tr key={i} className="border-b border-surface-3/40">
              <td className="px-2 py-1.5"><b>{r.variant}</b>
                <span className="text-[10.5px] text-gray-500 ml-1.5">{r.label}</span></td>
              <td className="px-2 py-1.5 text-[11.5px]">{r.firewall ? 'on' : 'off'}</td>
              <td className="px-2 py-1.5 text-right mono">{N(r.trades)}</td>
              <td className={`px-2 py-1.5 text-right mono ${tone(r.net)}`}>{RS(r.net)}</td>
              <td className="px-2 py-1.5 text-right mono">{PCT(r.win_rate)}</td>
              <td className="px-2 py-1.5 text-right mono">{r.profit_factor ?? '—'}</td>
              <td className={`px-2 py-1.5 text-right mono ${Math.abs(r.t_stat || 0) >= 2 ? 'text-emerald-400' : 'text-gray-400'}`}>
                {r.t_stat ?? '—'}</td>
              <td className="px-2 py-1.5 text-right mono text-red-400">{RS(r.max_drawdown)}</td>
              <td className={`px-2 py-1.5 text-right mono ${tone(r.expectancy)}`}>{RS(r.expectancy)}</td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card title="Did the firewall improve what was left, or only trade less?">
        {(cmp.firewall_effect || []).map((e) => (
          <div key={e.variant} className="border-b border-surface-3/40 py-2">
            <div className="text-[12.5px] text-gray-200">
              <b>{e.variant}</b> · trades {N(e.trades_off)} → {N(e.trades_on)}
              <span className="text-gray-500"> ({e.kept_trades_pct}% kept)</span>
              {' · '}net {RS(e.net_off)} → {RS(e.net_on)}
              <span className="text-gray-500"> ({e.kept_profit_pct ?? '—'}% kept)</span>
            </div>
            <div className={`text-[11.5px] mt-0.5 ${
              String(e.verdict).startsWith('the firewall kept more') ? 'text-emerald-400' : 'text-amber-300'}`}>
              {e.verdict}
            </div>
          </div>
        ))}
      </Card>
    </div>
  );
}

/* ── firewall timeline ─────────────────────────────────────────────────── */
function FirewallView({ res }) {
  const sessions = res?.firewall || [];
  const alerts = useMemo(() => sessions.flatMap((s) => s.alerts || []), [sessions]);
  if (!res) return <Note tone="info">Run a backtest to populate the firewall timeline.</Note>;
  const byState = sessions.reduce((acc, s) => {
    Object.entries(s.by_state || {}).forEach(([k, v]) => { acc[k] = (acc[k] || 0) + v; });
    return acc;
  }, {});
  return (
    <div className="space-y-4">
      <Card title="Breakout attempts by final state">
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          {Object.entries(byState).map(([k, v]) => (
            <div key={k} className="rounded-lg border border-surface-3 bg-surface-2/40 p-2.5">
              <div className={`text-[10px] uppercase tracking-wider ${STATE_TONE[k] || 'text-gray-500'}`}>
                {k.replace(/_/g, ' ')}</div>
              <div className="text-[18px] font-bold mono text-gray-100 mt-0.5">{N(v)}</div>
            </div>
          ))}
        </div>
      </Card>
      <Card title="Alerts" right={<span className="text-[10.5px] text-gray-500">{alerts.length} raised</span>}>
        {alerts.length ? (
          <div className="overflow-y-auto max-h-[520px] space-y-1.5">
            {alerts.slice(0, 300).map((a, i) => (
              <div key={i} className={`rounded-lg border px-3 py-2 text-[12px] ${
                a.kind.startsWith('FALSE BREAKOUT CONFIRMED') ? 'border-red-500/40 bg-red-500/5'
                  : a.kind.startsWith('POTENTIAL') ? 'border-amber-500/40 bg-amber-500/5'
                    : 'border-emerald-500/40 bg-emerald-500/5'}`}>
                <div className="flex flex-wrap items-center gap-2">
                  {a.kind.startsWith('FALSE BREAKOUT CONFIRMED') ? <ShieldAlert className="w-3.5 h-3.5 text-red-400" />
                    : a.kind.startsWith('POTENTIAL') ? <Shield className="w-3.5 h-3.5 text-amber-300" />
                      : <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />}
                  <b className="text-gray-100">{a.kind}</b>
                  <span className="mono text-[11px] text-gray-500">{a.session} {a.time}</span>
                  <span className="text-[11px] text-gray-400">
                    {a.direction} · {a.level_name} {N(a.level, 2)}</span>
                  <span className="mono text-[10.5px] text-gray-600 ml-auto">{a.event_id}</span>
                </div>
                {!!(a.conditions || []).length && (
                  <ul className="mt-1 ml-5 list-disc text-[11.5px] text-gray-400">
                    {a.conditions.map((c, j) => <li key={j}>{c}</li>)}
                  </ul>
                )}
                {a.action && <div className="text-[11.5px] text-gray-400 mt-1">{a.action}</div>}
                {a.note && <div className="text-[11px] text-gray-600 mt-0.5">{a.note}</div>}
              </div>
            ))}
          </div>
        ) : <Note tone="info">No alerts in this run.</Note>}
      </Card>
    </div>
  );
}

/* ── live desk ─────────────────────────────────────────────────────────── */
function LiveDesk({ desk, meta, onChange, busy }) {
  if (!desk) return <Note tone="info">Loading the desk…</Note>;
  const live = desk.mode === 'LIVE_TRADING';
  return (
    <div className="space-y-4">
      <Card title="Operating mode"
        right={
          <div className="flex items-center gap-2">
            <span className={`text-[11px] px-2 py-0.5 rounded-full border ${desk.running
              ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
              : 'border-surface-4 bg-surface-3 text-gray-400'}`}>
              {desk.running ? 'running' : 'stopped'}
            </span>
            <button onClick={() => onChange({ running: !desk.running })} disabled={busy}
              className="btn-secondary !py-1 !px-3 text-[12px]">
              {desk.running ? 'Stop' : 'Start'}
            </button>
          </div>
        }>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          {(meta.modes || []).filter((m) => m !== 'HISTORICAL_BACKTEST').map((m) => (
            <button key={m} onClick={() => onChange({ mode: m })} disabled={busy}
              className={`rounded-lg border px-3 py-2 text-left ${desk.mode === m
                ? 'border-brand-500 bg-brand-500/10' : 'border-surface-3 hover:border-surface-4'}`}>
              <div className="text-[12.5px] font-semibold text-gray-100">{m.replace(/_/g, ' ')}</div>
              <div className="text-[11px] text-gray-500 mt-0.5">
                {m === 'LIVE_SIGNALS_ONLY' && 'Alerts only. Takes nothing.'}
                {m === 'PAPER_TRADING' && 'Full lifecycle, recorded, no broker order.'}
                {m === 'LIVE_TRADING' && 'Real orders, behind two further gates.'}
              </div>
            </button>
          ))}
        </div>
        {live && (
          <Note tone="warn" className="mt-3">
            <b>LIVE_TRADING is selected.</b> That alone places nothing: the app must also be out
            of paper mode and the risk fence must be down, and both are checked again at the
            moment of every order.
          </Note>
        )}
      </Card>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Card title="Market state">
          <div className="grid grid-cols-2 gap-3">
            <Stat label="Firewall state" value={desk.state?.replace(/_/g, ' ') || 'idle'}
              tone={STATE_TONE[desk.state] || 'text-gray-300'} />
            <Stat label="Last decision" value={desk.last_decision || '—'} />
            <Stat label="Relative volume"
              value={desk.rvol?.rvol != null ? `${N(desk.rvol.rvol, 2)}×` : (desk.rvol?.state || '—')}
              tone={desk.rvol?.state === 'CONFIRMED' ? 'text-emerald-400'
                : desk.rvol?.state === 'UNAVAILABLE' ? 'text-amber-300' : 'text-gray-300'} />
            <Stat label="Trades today" value={N(desk.taken_today)} />
          </div>
          {desk.rvol?.reason && (
            <div className="text-[11px] text-amber-300 mt-2">{desk.rvol.reason}</div>
          )}
          {!!desk.levels && (
            <div className="mt-3 text-[11.5px] text-gray-400 space-y-0.5">
              {(desk.levels.resistance || []).map((l, i) => (
                <div key={`r${i}`}>▲ {l.name}: <span className="mono text-gray-200">{N(l.price, 2)}</span></div>))}
              {(desk.levels.support || []).map((l, i) => (
                <div key={`s${i}`}>▼ {l.name}: <span className="mono text-gray-200">{N(l.price, 2)}</span></div>))}
            </div>
          )}
        </Card>

        <Card title="Position">
          {desk.position ? (
            <div className="text-[12.5px] text-gray-200 space-y-1">
              <div><b>{desk.position.ticket?.type} {N(desk.position.ticket?.strike)}</b>
                {' × '}{desk.position.ticket?.lots} lots at {N(desk.position.ticket?.entry, 2)}</div>
              <div className="text-[11.5px] text-gray-400">
                underlying stop {N(desk.position.underlying_stop, 1)} ·
                premium stop {N(desk.position.premium_stop, 2)}</div>
              <div className="text-[11.5px]">
                <span className={desk.position.filled ? 'text-emerald-400' : 'text-amber-300'}>
                  {desk.position.filled ? 'order filled' : 'not sent to the broker'}
                </span>
                {desk.position.order?.error && (
                  <span className="text-gray-500"> — {desk.position.order.error}</span>)}
              </div>
            </div>
          ) : <Note tone="info">Flat.</Note>}
        </Card>
      </div>

      <Card title="Alerts">
        {(desk.alerts || []).length ? (
          <div className="space-y-1 max-h-[320px] overflow-y-auto">
            {desk.alerts.slice().reverse().map((a, i) => (
              <div key={i} className="text-[11.5px] text-gray-300 border-b border-surface-3/40 py-1">
                <span className="mono text-gray-600">{String(a.at || '').slice(11, 19)}</span>{' '}
                <b>{a.kind}</b> · {a.direction} {a.level_name} {N(a.level, 2)}
              </div>
            ))}
          </div>
        ) : <Note tone="info">No alerts yet.</Note>}
      </Card>

      <Card title="Log">
        <pre className="text-[11px] text-gray-400 max-h-[260px] overflow-y-auto whitespace-pre-wrap">
          {(desk.log || []).slice().reverse().join('\n') || 'nothing yet'}
        </pre>
      </Card>
    </div>
  );
}

/* ── data ──────────────────────────────────────────────────────────────── */
function DataTab({ inv, meta, onPull, busy }) {
  if (!inv) return null;
  const rows = [['NIFTY spot 1-min', inv.spot], ['NIFTY futures 1-min', inv.futures],
    ['NIFTY options 1-min', inv.options], ['India VIX 1-min', inv.vix]];
  return (
    <div className="space-y-4">
      <Card title="What the store actually holds"
        right={<button onClick={onPull} disabled={busy}
          className="btn-secondary !py-1 !px-3 text-[12px] flex items-center gap-1.5">
          <Database className="w-3.5 h-3.5" />Pull futures</button>}>
        <Table cols={['Series', 'Available', 'Sessions', 'Rows', 'From', 'To']}
          align={['left', 'left', 'right', 'right', 'left', 'left']}>
          {rows.map(([label, v]) => (
            <tr key={label} className="border-b border-surface-3/40">
              <td className="px-2 py-1.5 text-gray-200">{label}</td>
              <td className={`px-2 py-1.5 text-[11.5px] ${v?.available ? 'text-emerald-400' : 'text-red-400'}`}>
                {v?.available ? 'yes' : 'never ingested'}</td>
              <td className="px-2 py-1.5 text-right mono">{N(v?.sessions)}</td>
              <td className="px-2 py-1.5 text-right mono text-gray-400">{N(v?.rows)}</td>
              <td className="px-2 py-1.5 mono text-gray-400">{v?.first || '—'}</td>
              <td className="px-2 py-1.5 mono text-gray-400">{v?.last || '—'}</td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card title="What that means for this strategy">
        <ul className="space-y-1.5 text-[12.5px] text-gray-300 list-disc list-inside">
          {(inv.notes || []).map((n, i) => <li key={i}>{n}</li>)}
          {(meta.caveats || []).map((c, i) => <li key={`c${i}`}>{c}</li>)}
        </ul>
      </Card>
    </div>
  );
}

/* ── small pieces ──────────────────────────────────────────────────────── */
function Card({ title, right, children }) {
  return (
    <div className="card !p-4">
      <div className="flex flex-wrap items-start justify-between gap-2 mb-2.5">
        <div className="text-[12px] font-semibold uppercase tracking-wider text-gray-400">{title}</div>
        {right}
      </div>
      {children}
    </div>
  );
}

function Field({ label, hint, children }) {
  return (
    <label className="block">
      <div className="text-[10px] uppercase tracking-wider text-gray-500">{label}</div>
      {children}
      {hint && <div className="text-[10px] text-gray-600 mt-0.5">{hint}</div>}
    </label>
  );
}

function Stat({ label, value, tone: t }) {
  return (
    <div className="rounded-lg border border-surface-3 bg-surface-2/40 p-2.5">
      <div className="text-[10px] uppercase tracking-wider text-gray-500">{label}</div>
      <div className={`text-[14px] font-bold mono mt-0.5 ${t || 'text-gray-100'}`}>{value}</div>
    </div>
  );
}

function Note({ tone: t = 'info', children, className = '' }) {
  const cls = {
    info: 'border-surface-3 text-gray-400',
    warn: 'border-amber-500/40 text-amber-300 bg-amber-500/5',
    ok: 'border-emerald-500/40 text-emerald-300 bg-emerald-500/5',
    bad: 'border-red-500/40 text-red-300 bg-red-500/5',
  }[t];
  return (
    <div className={`text-[12px] rounded-lg border px-3 py-2 leading-snug ${cls} ${className}`}>
      {t === 'info' && <Info className="w-3.5 h-3.5 inline mr-1.5 -mt-0.5 text-gray-500" />}
      {children}
    </div>
  );
}

function Table({ cols, align = [], children }) {
  return (
    <div className="overflow-x-auto max-h-[460px]">
      <table className="w-full text-[12px]">
        <thead className="sticky top-0 z-10">
          <tr className="bg-surface-3/90 backdrop-blur-sm text-[10px] uppercase tracking-[0.12em]
                         text-gray-300 border-b-2 border-surface-4">
            {cols.map((c, i) => (
              <th key={c} className={`px-2 py-1.5 font-semibold whitespace-nowrap
                ${align[i] === 'right' ? 'text-right' : 'text-left'}`}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function CsvButton({ rows, name }) {
  if (!rows?.length) return null;
  const download = () => {
    const keys = Object.keys(rows[0]);
    const body = rows.map((r) => keys.map((k) => {
      const v = r[k];
      const s = v == null ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v);
      return `"${s.replace(/"/g, '""')}"`;
    }).join(','));
    const blob = new Blob([[keys.join(','), ...body].join('\n')], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${name}-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  return (
    <button onClick={download} className="btn-secondary !py-1 !px-2 text-[11.5px] flex items-center gap-1">
      <Download className="w-3 h-3" />CSV
    </button>
  );
}

/* -- today's levels, straight from the broker -------------------------- */
function TodayView({ t, onReload }) {
  if (!t) {
    return (
      <div className="text-[13px] text-gray-500 flex items-center gap-2">
        <Loader2 className="w-4 h-4 animate-spin" />
        Reading the live session from your broker&hellip;
      </div>
    );
  }
  if (!t.available) {
    return (
      <Card title="Today">
        <Note tone="warn">{t.message}</Note>
        <p className="text-[11.5px] text-gray-500 mt-2">
          Today is never in the stored history &mdash; the store is a snapshot refreshed by the
          Data Ingestion Lab. This view asks the broker for the live session instead, so it
          needs a connection rather than an ingest.
        </p>
      </Card>
    );
  }
  const Row = ({ one, bullish }) => (
    <tr className="border-b border-surface-3/40">
      <td className="px-2 py-1.5">
        <span className={bullish ? 'text-emerald-400' : 'text-red-400'}>{bullish ? '\u25B2' : '\u25BC'}</span>
        <span className="ml-1.5 text-gray-200">{one.name}</span>
      </td>
      <td className="px-2 py-1.5 text-right mono text-gray-100">{N(one.price, 2)}</td>
      <td className="px-2 py-1.5 text-right mono font-semibold text-brand-300">
        {N(one.trigger.close_beyond, 2)}
      </td>
      <td className={`px-2 py-1.5 text-right mono ${one.trigger.already_beyond
        ? 'text-emerald-400' : 'text-gray-400'}`}>
        {one.trigger.already_beyond
          ? 'already beyond'
          : `${N(Math.abs(one.trigger.distance), 1)} away`}
      </td>
    </tr>
  );
  return (
    <div className="space-y-4">
      <Card title={`Session ${t.session}${t.is_today ? '' : ' (last completed)'}`}
        right={(
          <div className="flex items-center gap-2">
            <span className="text-[10.5px] text-gray-500 mono">
              as of {t.as_of} &middot; {t.bars} bars
            </span>
            <button onClick={onReload}
              className="btn-secondary !py-1 !px-2 text-[11.5px] flex items-center gap-1">
              <RefreshCw className="w-3 h-3" />Refresh
            </button>
          </div>
        )}>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Stat label="NIFTY" value={N(t.spot, 2)} />
          <Stat label="ATR" value={N(t.atr, 1)} />
          <Stat label="Breakout buffer" value={`${N(t.buffer, 1)} pts`} />
          <Stat label="Relative volume"
            value={t.relative_volume?.rvol != null
              ? `${N(t.relative_volume.rvol, 2)}x`
              : (t.relative_volume?.state || '\u2014')}
            tone={t.relative_volume?.state === 'CONFIRMED' ? 'text-emerald-400'
              : t.relative_volume?.state === 'UNAVAILABLE' ? 'text-amber-300' : 'text-gray-300'} />
        </div>
        {t.relative_volume?.reason && (
          <div className="text-[11px] text-amber-300 mt-2">{t.relative_volume.reason}</div>
        )}
      </Card>

      <Card title="Levels, and what would trigger them">
        <Table cols={['Level', 'Price', 'A close beyond', 'Distance']}
          align={['left', 'right', 'right', 'right']}>
          {(t.resistance || []).map((o, i) => <Row key={`r${i}`} one={o} bullish />)}
          {(t.support || []).map((o, i) => <Row key={`s${i}`} one={o} bullish={false} />)}
        </Table>
        <p className="text-[11px] text-gray-600 mt-2">{t.note}</p>
      </Card>

      <Card title="Can a trade trigger right now?">
        {t.tradeable_now ? (
          <Note tone="ok">
            <CheckCircle2 className="w-3.5 h-3.5 inline mr-1.5 -mt-0.5" />
            Nothing is blocking an entry. A completed candle closing beyond one of the trigger
            prices above would qualify, subject to the firewall&apos;s verdict on that candle.
          </Note>
        ) : (
          <>
            <Note tone="warn">No entry can trigger at this moment:</Note>
            <ul className="mt-2 space-y-1 text-[12.5px] text-gray-300 list-disc list-inside">
              {(t.blockers || []).map((b, i) => <li key={i}>{b}</li>)}
            </ul>
          </>
        )}
      </Card>

      <Card title="Breakout attempts this session"
        right={(
          <span className="text-[10.5px] text-gray-500">
            {t.firewall?.attempts || 0} seen &middot; {t.firewall?.blocked || 0} blocked
          </span>
        )}>
        {(t.attempts || []).length ? (
          <Table cols={['Time', 'Direction', 'Level', 'State', 'Why']}
            align={['left', 'left', 'right', 'left', 'left']}>
            {t.attempts.slice().reverse().map((a, i) => (
              <tr key={i} className="border-b border-surface-3/40">
                <td className="px-2 py-1.5 mono text-gray-400">
                  {String(a.transitions?.[0]?.time || '')}
                </td>
                <td className="px-2 py-1.5 text-[11.5px]">
                  {a.direction === 'BULL' ? 'upside' : 'downside'}
                </td>
                <td className="px-2 py-1.5 text-right mono">{N(a.level, 2)}</td>
                <td className={`px-2 py-1.5 text-[11.5px] ${STATE_TONE[a.state] || 'text-gray-400'}`}>
                  {String(a.state).replace(/_/g, ' ').toLowerCase()}
                </td>
                <td className="px-2 py-1.5 text-[11.5px] text-gray-400">
                  {a.block_reason || a.transitions?.[a.transitions.length - 1]?.why || ''}
                </td>
              </tr>
            ))}
          </Table>
        ) : <Note tone="info">No level has been broken yet this session.</Note>}
      </Card>
    </div>
  );
}
