import React, { useCallback, useEffect, useState } from 'react';
import {
  Loader2, RefreshCw, AlertTriangle, Layers, BarChart3, BookOpen, Database, Play,
} from 'lucide-react';
import { api } from '../../api';
import { Note, Section, Stat, Field, input, N, N0, RS, PCT, tone } from '../../components/fluxlab/ui';

/**
 * Calendar Ratio Spread.
 *
 * Sells two near-dated options and buys three far-dated ones on each side, with every leg
 * chosen by PREMIUM rather than by strike. Two things the UI has to make impossible to miss:
 * the gap between the premium asked for and the premium found (a leg 300 away from target is
 * not that leg), and whether the two expiries are actually different (in the last week of a
 * month they are not, and the structure is then something else entirely).
 */

const TABS = [
  { id: 'live', label: 'Live structure', icon: Layers },
  { id: 'backtest', label: 'Backtest', icon: BarChart3 },
  { id: 'data', label: 'Data check', icon: Database },
  { id: 'rules', label: 'The structure', icon: BookOpen },
];

const RSA = (v) => (v == null ? '—' : `₹${Math.abs(Number(v)).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`);
const today = () => new Date().toISOString().slice(0, 10);
const daysAgo = (n) => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);

const VERDICT_CLS = {
  ok: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
  weak: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
  thin: 'border-sky-500/40 bg-sky-500/10 text-sky-300',
  bad: 'border-red-500/40 bg-red-500/10 text-red-300',
  none: 'border-surface-3 bg-surface-2/40 text-gray-300',
};

export default function CalendarRatio() {
  const [tab, setTab] = useState('live');
  const [meta, setMeta] = useState(null);
  const [err, setErr] = useState('');

  const [index, setIndex] = useState('NIFTY');
  const [legs, setLegs] = useState(null);
  const [policy, setPolicy] = useState('next_month');
  const [exitMode, setExitMode] = useState('weekly_expiry');
  const [holdDays, setHoldDays] = useState(1);
  const [tol, setTol] = useState({ pct: 25, abs: 20 });

  const [live, setLive] = useState(null);
  const [loadingLive, setLoadingLive] = useState(false);

  const [range, setRange] = useState({ start: daysAgo(365), end: today() });
  const [slip, setSlip] = useState(0.5);
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);

  const [diag, setDiag] = useState(null);
  const [diagBusy, setDiagBusy] = useState(false);

  useEffect(() => {
    api.crMeta().then((d) => {
      setMeta(d);
      if (d?.defaults?.params?.legs) setLegs(d.defaults.params.legs);
    }).catch((e) => setErr(String(e.message || e)));
  }, []);

  const params = useCallback(() => ({
    index, legs, same_expiry_policy: policy, exit_mode: exitMode, hold_days: holdDays,
    tolerance_pct: tol.pct, tolerance_abs: tol.abs,
  }), [index, legs, policy, exitMode, holdDays, tol]);

  const loadLive = useCallback(async () => {
    if (!legs) return;
    setLoadingLive(true);
    try {
      const d = await api.crLive({ params: params() });
      if (d?.status === 'error') setErr(d.message); else setErr('');
      setLive(d?.live || null);
    } catch (e) { setErr(String(e.message || e)); } finally { setLoadingLive(false); }
  }, [params, legs]);

  useEffect(() => { if (tab === 'live' && legs) loadLive(); }, [tab, loadLive, legs]);
  useEffect(() => {
    if (tab !== 'live') return undefined;
    const t = setInterval(loadLive, 30000);
    return () => clearInterval(t);
  }, [tab, loadLive]);

  const runDiag = useCallback(async () => {
    setDiagBusy(true);
    try { setDiag((await api.crDiagnose({ index, ...range }))?.diagnosis || null); }
    catch (e) { setErr(String(e.message || e)); } finally { setDiagBusy(false); }
  }, [index, range]);

  useEffect(() => { if (tab === 'data') runDiag(); }, [tab, runDiag]);

  const run = async () => {
    setRunning(true); setErr(''); setResult(null);
    try {
      const d = await api.crBacktest({ config: { ...range, params: params(), slippage_pts: slip } });
      if (d?.status === 'error') setErr(d.message);
      setResult(d?.result || null);
    } catch (e) { setErr(String(e.message || e)); } finally { setRunning(false); }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-white">Calendar Ratio Spread</h1>
          <p className="text-[12.5px] text-gray-400 mt-0.5 max-w-3xl">
            Sell two near-dated options, buy three far-dated ones, on each side — every leg
            picked by <b>premium</b>, not by strike. Premium in and premium out are close by
            design: the bet is that the weekly decays faster than the monthly.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-[11px] px-2 py-1 rounded ${meta?.connected
            ? 'bg-emerald-500/15 text-emerald-400' : 'bg-amber-500/15 text-amber-400'}`}>
            {meta?.connected ? 'Zerodha connected' : 'Zerodha not connected'}
          </span>
          {tab === 'live' && (
            <button onClick={loadLive} disabled={loadingLive}
                    className="btn-secondary !py-1.5 !px-3 text-[12.5px] flex items-center gap-1.5">
              {loadingLive ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                : <RefreshCw className="w-3.5 h-3.5" />}Refresh
            </button>
          )}
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

      <Setup meta={meta} index={index} setIndex={setIndex} legs={legs} setLegs={setLegs}
             policy={policy} setPolicy={setPolicy} exitMode={exitMode} setExitMode={setExitMode}
             holdDays={holdDays} setHoldDays={setHoldDays} tol={tol} setTol={setTol}
             showExit={tab === 'backtest'} />

      {tab === 'live' && <LiveTab live={live} loading={loadingLive} />}
      {tab === 'backtest' && (
        <BacktestTab range={range} setRange={setRange} slip={slip} setSlip={setSlip}
                     run={run} running={running} result={result} />
      )}
      {tab === 'data' && <DataTab diag={diag} busy={diagBusy} rerun={runDiag} index={index}
                                  meta={meta} />}
      {tab === 'rules' && <RulesTab meta={meta} />}
    </div>
  );
}

/* ── the structure definition ────────────────────────────────────────────── */
function Setup({ meta, index, setIndex, legs, setLegs, policy, setPolicy, exitMode,
                 setExitMode, holdDays, setHoldDays, tol, setTol, showExit }) {
  const setLeg = (i, patch) => setLegs(legs.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  return (
    <Section title="The structure">
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-3">
        <Field label="Index" hint="NIFTY is NFO, SENSEX is BFO">
          <select className={input} value={index} onChange={(e) => setIndex(e.target.value)}>
            {(meta?.indices || [{ key: 'NIFTY', label: 'NIFTY 50' }]).map((i) => (
              <option key={i.key} value={i.key}>{i.label}</option>
            ))}
          </select>
        </Field>
        <Field label="Tolerance %" hint="how far a premium may miss its target">
          <input type="number" min={0} max={100} className={input} value={tol.pct}
                 onChange={(e) => setTol({ ...tol, pct: Number(e.target.value) })} />
        </Field>
        <Field label="Tolerance pts" hint="the floor, for the small legs">
          <input type="number" min={0} max={500} className={input} value={tol.abs}
                 onChange={(e) => setTol({ ...tol, abs: Number(e.target.value) })} />
        </Field>
        <Field label="Far leg" hint="when the near expiry is also the month's last">
          <select className={input} value={policy} onChange={(e) => setPolicy(e.target.value)}>
            <option value="next_month">Roll the far leg to next month</option>
            <option value="skip">Skip the day</option>
          </select>
        </Field>
        {showExit && (
          <>
            <Field label="Exit" hint="the source gives no exit — this is a choice">
              <select className={input} value={exitMode} onChange={(e) => setExitMode(e.target.value)}>
                {(meta?.exit_modes || []).map((m) => (
                  <option key={m.key} value={m.key}>{m.label}</option>
                ))}
              </select>
            </Field>
            <Field label="Hold days" hint="only for the fixed-days exit">
              <input type="number" min={1} max={40} className={input} value={holdDays}
                     disabled={exitMode !== 'fixed_days'}
                     onChange={(e) => setHoldDays(Number(e.target.value))} />
            </Field>
          </>
        )}
      </div>

      {legs && (
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead className="text-[10px] uppercase tracking-wider text-gray-500">
              <tr className="border-b border-surface-3">
                <th className="text-left px-2 py-1.5">Side</th>
                <th className="text-left px-2 py-1.5">Option</th>
                <th className="text-left px-2 py-1.5">Expiry</th>
                <th className="text-right px-2 py-1.5">Premium near</th>
                <th className="text-right px-2 py-1.5">Lots</th>
              </tr>
            </thead>
            <tbody>
              {legs.map((l, i) => (
                <tr key={i} className="border-b border-surface-3/50">
                  <td className="px-2 py-1">
                    <select className={`${input} !w-24`} value={l.side}
                            onChange={(e) => setLeg(i, { side: e.target.value })}>
                      <option value="SELL">SELL</option><option value="BUY">BUY</option>
                    </select>
                  </td>
                  <td className="px-2 py-1">
                    <select className={`${input} !w-20`} value={l.option_type}
                            onChange={(e) => setLeg(i, { option_type: e.target.value })}>
                      <option value="CE">CE</option><option value="PE">PE</option>
                    </select>
                  </td>
                  <td className="px-2 py-1">
                    <select className={`${input} !w-28`} value={l.expiry}
                            onChange={(e) => setLeg(i, { expiry: e.target.value })}>
                      <option value="weekly">this week</option>
                      <option value="monthly">this month</option>
                    </select>
                  </td>
                  <td className="px-2 py-1 text-right">
                    <input type="number" min={1} className={`${input} !w-24 text-right`}
                           value={l.target_premium}
                           onChange={(e) => setLeg(i, { target_premium: Number(e.target.value) })} />
                  </td>
                  <td className="px-2 py-1 text-right">
                    <input type="number" min={1} max={100} className={`${input} !w-16 text-right`}
                           value={l.lots}
                           onChange={(e) => setLeg(i, { lots: Number(e.target.value) })} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

/* ── live ─────────────────────────────────────────────────────────────────── */
function LiveTab({ live, loading }) {
  if (loading && !live) {
    return <Section><div className="flex items-center gap-2 text-[12.5px] text-gray-400">
      <Loader2 className="w-4 h-4 animate-spin" />Reading the chain…</div></Section>;
  }
  if (!live) return <Section><Note>Nothing loaded yet.</Note></Section>;
  if (!live.available) {
    return <Section title="Live structure"><Note tone="warn">{live.message}</Note></Section>;
  }
  const e = live.expiries || {};
  return (
    <div className="space-y-4">
      <Section title="Where things stand"
        right={<span className="text-[11px] text-gray-500 mono">
          {live.label} · {live.exchange} · {live.date} · {live.as_of}</span>}>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          <Stat label="Spot" value={N(live.spot)} sub={live.label} />
          <Stat label="Near leg" value={e.weekly || '—'} sub={`${e.weekly_dte ?? '—'} days out`} />
          <Stat label="Far leg" value={e.monthly || '—'}
                sub={e.gap_days == null ? `${e.monthly_dte ?? '—'} days out`
                  : `${e.gap_days}d past the near leg`}
                tone={e.collapsed ? 'text-red-400' : 'text-gray-100'} />
          <Stat label="Lot size" value={N0(live.lot_size)} sub="from the broker" />
          <Stat label="Net premium" value={live.net_premium == null ? '—' : N(live.net_premium)}
                sub={live.net_is || '—'} tone={tone(live.net_premium)} />
          <Stat label="Net, in rupees" value={live.net_premium_rs == null ? '—' : RS(live.net_premium_rs)}
                tone={tone(live.net_premium_rs)} sub="one structure" />
        </div>
        <div className="mt-3 space-y-1.5">
          <Note tone={live.entry_window?.state === 'now' ? 'info' : 'warn'}>
            {live.entry_window?.message}
          </Note>
          {e.collapsed && (
            <Note tone="warn">
              <b>{e.only_one ? 'Only one expiry in the data.' : 'The calendar has collapsed.'}</b>
              {' '}{e.reason}
            </Note>
          )}
          {(live.blockers || []).map((b, i) => <Note key={i} tone="warn">{b}</Note>)}
        </div>
      </Section>

      <Section title="The six legs"
        right={live.tradable
          ? <span className="text-[11px] text-emerald-400">all legs within tolerance</span>
          : <span className="text-[11px] text-amber-400">some legs could not be filled</span>}>
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead className="text-[10px] uppercase tracking-wider text-gray-500">
              <tr className="border-b border-surface-3">
                <th className="text-left px-2 py-1.5">Leg</th>
                <th className="text-left px-2 py-1.5">Contract</th>
                <th className="text-right px-2 py-1.5">Strike</th>
                <th className="text-right px-2 py-1.5">Wanted</th>
                <th className="text-right px-2 py-1.5">Got</th>
                <th className="text-right px-2 py-1.5">Off by</th>
                <th className="text-right px-2 py-1.5">Qty</th>
                <th className="text-right px-2 py-1.5">Value</th>
              </tr>
            </thead>
            <tbody>
              {(live.legs || []).map((l, i) => {
                const sell = l.side === 'SELL';
                return (
                  <tr key={i} className="border-b border-surface-3/50">
                    <td className="px-2 py-1.5 whitespace-nowrap">
                      <span className={`px-1.5 py-px rounded text-[10.5px] font-bold ${sell
                        ? 'bg-red-500/15 text-red-400' : 'bg-emerald-500/15 text-emerald-400'}`}>
                        {l.side}
                      </span>
                      <span className="text-gray-400 ml-1.5">
                        {l.lots}× {l.expiry_kind} {l.option_type}
                      </span>
                    </td>
                    <td className="px-2 py-1.5 mono text-gray-400 text-[11px]">
                      {l.tradingsymbol || '—'}
                    </td>
                    <td className="px-2 py-1.5 text-right mono">{N0(l.strike)}</td>
                    <td className="px-2 py-1.5 text-right mono text-gray-500">{N0(l.target_premium)}</td>
                    <td className="px-2 py-1.5 text-right mono">{N(l.premium)}</td>
                    <td className={`px-2 py-1.5 text-right mono ${l.ok ? 'text-gray-500' : 'text-red-400'}`}>
                      {l.gap == null ? '—' : N(l.gap, 1)}
                    </td>
                    <td className="px-2 py-1.5 text-right mono text-gray-400">{N0(l.qty)}</td>
                    <td className="px-2 py-1.5 text-right mono">{RSA(l.value_rs)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {live.tradable && (
          <div className="text-[11.5px] text-gray-500 mt-3">
            Collected {N(live.premium_collected)} against {N(live.premium_paid)} paid —{' '}
            {live.note}
          </div>
        )}
      </Section>
    </div>
  );
}

/* ── backtest ─────────────────────────────────────────────────────────────── */
function BacktestTab({ range, setRange, slip, setSlip, run, running, result }) {
  const r = result;
  return (
    <div className="space-y-4">
      <Section title="Run it over history"
        right={<button onClick={run} disabled={running}
                       className="btn-primary !py-1.5 !px-3 text-[12.5px] flex items-center gap-1.5">
          {running ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
          {running ? 'Running…' : 'Run'}</button>}>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Field label="From"><input type="date" className={input} value={range.start}
                                     onChange={(e) => setRange({ ...range, start: e.target.value })} /></Field>
          <Field label="To"><input type="date" className={input} value={range.end}
                                   onChange={(e) => setRange({ ...range, end: e.target.value })} /></Field>
          <Field label="Slippage" hint="points per leg, per side — twelve fills a cycle">
            <input type="number" step={0.25} min={0} max={20} className={input} value={slip}
                   onChange={(e) => setSlip(Number(e.target.value))} /></Field>
        </div>
      </Section>

      {running && (
        <Section><div className="flex items-center gap-2 text-[12.5px] text-gray-400">
          <Loader2 className="w-4 h-4 animate-spin" />Pricing twelve legs a cycle on real prints…
        </div></Section>
      )}

      {r && r.status === 'bad_request' && <Section><Note tone="warn">{r.message}</Note></Section>}

      {r && (r.status === 'no_data') && (
        <Section title="No result">
          <Note tone="warn">{r.message}</Note>
          {r.coverage && <div className="text-[11.5px] text-gray-500 mt-2">Store holds {r.coverage}.</div>}
          <div className="text-[11.5px] text-gray-500 mt-2">
            The Data check tab says exactly what is missing.
          </div>
        </Section>
      )}

      {r && (r.status === 'ok' || r.status === 'no_trades') && <Results r={r} />}
    </div>
  );
}

function Results({ r }) {
  const o = r.overall || {};
  const v = r.verdict || {};
  return (
    <div className="space-y-4">
      <div className={`rounded-lg border px-3.5 py-3 ${VERDICT_CLS[v.tone] || VERDICT_CLS.none}`}>
        <div className="text-[13.5px] font-semibold">{v.headline}</div>
        <div className="text-[12px] opacity-90 mt-1">{v.detail}</div>
      </div>

      <Section title="What happened"
        right={<span className="text-[11px] text-gray-500 mono">
          {r.span?.start} → {r.span?.end} · {r.span?.days} sessions · {r.index} · lot {r.lot_size}
        </span>}>
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
          <Stat label="Net" value={RS(o.net)} tone={tone(o.net)} sub={`${o.trades} cycles`} />
          <Stat label="Per cycle" value={RS(o.avg)} tone={tone(o.avg)} sub={`median ${RS(o.median)}`} />
          <Stat label="Win rate" value={PCT(o.win_rate)} />
          <Stat label="t-statistic" value={o.t_stat == null ? '—' : N(o.t_stat)}
                tone={o.t_stat == null ? 'text-gray-400' : o.t_stat >= 2 ? 'text-emerald-400' : 'text-amber-400'}
                sub="2 is the usual bar" />
          <Stat label="Worst cycle" value={RS(o.worst)} tone="text-red-400" />
          <Stat label="Max drawdown" value={RSA(o.max_drawdown)} tone="text-red-400" />
          <Stat label="Skipped" value={N0(r.skipped)} sub="days with no trade" />
        </div>
        <div className="text-[11.5px] text-gray-500 mt-3">
          Exit: {r.exit?.mode?.replace(/_/g, ' ')} at {r.exit?.time}. {r.exit?.note}{' '}
          {r.costs?.note}
        </div>
      </Section>

      {(r.skipped_reasons || []).length > 0 && (
        <Section title="Why days were skipped">
          <div className="space-y-1.5">
            {r.skipped_reasons.map(([why, n], i) => (
              <div key={i} className="flex items-start gap-2 text-[12px]">
                <span className="mono text-gray-500 shrink-0 w-12 text-right">{n}×</span>
                <span className="text-gray-400">{why}</span>
              </div>
            ))}
          </div>
        </Section>
      )}

      {(r.trades || []).length > 0 && (
        <Section title="The cycles"
          right={r.trades_truncated && <span className="text-[11px] text-gray-500">
            {r.trades_truncated.shown} of {r.trades_truncated.of} shown</span>}>
          <div className="overflow-x-auto max-h-[26rem]">
            <table className="w-full text-[11.5px]">
              <thead className="text-[10px] uppercase tracking-wider text-gray-500 sticky top-0 bg-surface-1">
                <tr className="border-b border-surface-3">
                  <th className="text-left px-2 py-1.5">In</th>
                  <th className="text-left px-2 py-1.5">Out</th>
                  <th className="text-right px-2 py-1.5">Held</th>
                  <th className="text-left px-2 py-1.5">Weekly</th>
                  <th className="text-left px-2 py-1.5">Monthly</th>
                  <th className="text-right px-2 py-1.5">Gross</th>
                  <th className="text-right px-2 py-1.5">Costs</th>
                  <th className="text-right px-2 py-1.5">Net</th>
                </tr>
              </thead>
              <tbody>
                {[...r.trades].reverse().map((t, i) => (
                  <tr key={i} className="border-b border-surface-3/50">
                    <td className="px-2 py-1 mono text-gray-400">{t.date}</td>
                    <td className="px-2 py-1 mono text-gray-400">{t.exit_date}</td>
                    <td className="px-2 py-1 text-right mono text-gray-500">{t.held_days}d</td>
                    <td className="px-2 py-1 mono text-gray-500">{t.weekly_expiry}</td>
                    <td className="px-2 py-1 mono text-gray-500">{t.monthly_expiry}</td>
                    <td className={`px-2 py-1 text-right mono ${tone(t.gross_rs)}`}>{RS(t.gross_rs)}</td>
                    <td className="px-2 py-1 text-right mono text-gray-500">{RSA(t.costs_rs)}</td>
                    <td className={`px-2 py-1 text-right mono ${tone(t.pnl_rs)}`}>{RS(t.pnl_rs)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}

/* ── data check ───────────────────────────────────────────────────────────── */
function DataTab({ diag, busy, rerun, index, meta }) {
  if (busy && !diag) {
    return <Section><div className="flex items-center gap-2 text-[12.5px] text-gray-400">
      <Loader2 className="w-4 h-4 animate-spin" />Checking the store…</div></Section>;
  }
  return (
    <div className="space-y-4">
      <Section title={`Can the store price this structure? — ${index}`}
        right={<button onClick={rerun} disabled={busy}
                       className="btn-secondary !py-1.5 !px-3 text-[12.5px] flex items-center gap-1.5">
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
          Re-check</button>}>
        {!diag?.ok ? (
          <Note tone="warn">{diag?.message || 'No data for that index and range.'}</Note>
        ) : (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
              <Stat label="Sessions" value={N0(diag.days)} />
              <Stat label="Expiries per day"
                    value={`${diag.expiries_per_day?.min}–${diag.expiries_per_day?.max}`}
                    sub={`median ${diag.expiries_per_day?.median}`}
                    tone={diag.has_two_expiries_on > 0 ? 'text-emerald-400' : 'text-red-400'} />
              <Stat label="Days with 2+" value={N0(diag.has_two_expiries_on)}
                    tone={diag.has_two_expiries_on > 0 ? 'text-emerald-400' : 'text-red-400'}
                    sub="a calendar needs this" />
              <Stat label="Strikes per day" value={N0(diag.strikes_per_day_median)} sub="median" />
              <Stat label="Max CE premium"
                    value={N0(diag.premium_reach_at_0930?.CE?.median_max_premium)}
                    sub="median, at 09:30" />
              <Stat label="Max PE premium"
                    value={N0(diag.premium_reach_at_0930?.PE?.median_max_premium)}
                    sub="median, at 09:30" />
            </div>
            <div className="mt-3">
              <Note tone={diag.has_two_expiries_on > 0 ? 'info' : 'warn'}>{diag.verdict}</Note>
            </div>
            {diag.coverage && (
              <div className="text-[11.5px] text-gray-500 mt-2">Store holds {diag.coverage}.</div>
            )}
          </>
        )}
      </Section>

      <Section title="If the data is not there">
        <div className="text-[12.5px] text-gray-300 space-y-2.5 max-w-3xl">
          <p>
            This structure needs two things from history that a single-expiry capture does not
            provide: a <b>weekly and a monthly expiry on the same day</b>, and strikes deep
            enough that a 450-premium option exists at all — which for a call means several
            hundred points in the money.
          </p>
          <p>
            The Data Ingestion Lab captures the chain daily. Its defaults are{' '}
            <span className="mono text-gray-400">expiries: 1</span> and{' '}
            <span className="mono text-gray-400">strikes: 10</span> either side of ATM; it
            accepts up to <span className="mono text-gray-400">4</span> and{' '}
            <span className="mono text-gray-400">25</span>. Raising both is what makes this
            strategy backtestable going forward.
          </p>
          <p>
            It cannot be back-filled from the broker, and that is a hard limit rather than a
            setting: once a contract expires Zerodha drops it from the instrument list and its
            token is gone. Contracts that are <i>still listed</i> — including this month's
            monthly — do still carry their history, so widening the capture now recovers
            roughly the last two months as well as everything from here on.
          </p>
          <p className="text-[11.5px] text-gray-500">
            {meta?.store?.SENSEX
              ? `SENSEX in the store: ${meta.store.SENSEX}.`
              : 'SENSEX is not in the store at all yet — the live tab works regardless, '
                + 'because it reads the chain from the broker.'}
          </p>
        </div>
      </Section>
    </div>
  );
}

/* ── rules ────────────────────────────────────────────────────────────────── */
function RulesTab({ meta }) {
  return (
    <div className="space-y-4">
      <Section title="What the position is">
        <div className="text-[12.5px] text-gray-300 space-y-2.5 max-w-3xl">
          <p>
            On each side — calls and puts — it sells two options in <b>this week's</b> expiry,
            one near 150 premium and one near 450, and buys three in <b>this month's</b>
            {' '}expiry near 200.
          </p>
          <p>
            <b>The premium nets to roughly zero.</b> 150 + 450 collected against 3 × 200 paid
            is 600 against 600. So this is not a credit trade and there is no premium being
            &ldquo;kept&rdquo;. What it is betting on is <b>differential decay</b>: the
            near-dated options you are short losing value faster than the far-dated ones you
            are long.
          </p>
          <p>
            <b>Legs are chosen by premium, not by strike.</b> That inverts the usual
            resolution — not &ldquo;200 points out&rdquo; but &ldquo;whichever listed contract
            is trading nearest 150&rdquo;. The strike therefore moves with volatility. Every
            leg shows the gap between what was asked for and what was found, and a leg outside
            tolerance is refused rather than substituted, because a contract 300 away from a
            450 target is simply not that leg.
          </p>
          <p>
            <b>One week in four, this is a different trade.</b> In the last week of a month the
            weekly expiry <i>is</i> the monthly expiry. The calendar collapses to a plain ratio
            backspread in a single expiry — two short against three long, no time differential
            at all. Those days are skipped by default; the alternative is to buy the following
            month, which keeps a real calendar but is not the structure as specified.
          </p>
        </div>
      </Section>

      <Section title="What is assumed, and what is measured">
        <div className="text-[12.5px] text-gray-300 space-y-2.5 max-w-3xl">
          <p>
            <b>The exit is an assumption.</b> The specification gives an entry time and no
            exit. Three are offered — hold to the weekly expiry, square off the same day, or
            hold a fixed number of days — and whichever is chosen is stated on the result,
            because the P&amp;L belongs to that choice and not to the strategy alone.
          </p>
          <p>
            <b>Costs are real.</b> Twelve fills a cycle: six in, six out. Zerodha statutory
            charges on the correct side of each leg, plus slippage working against the
            position on every one.
          </p>
          <p>
            <b>Prices are prints, not models.</b> A leg with no print in the exit minute
            carries its last traded price and is counted, never interpolated. A day whose
            chain cannot supply all six legs is skipped with the reason recorded — that count
            is part of the result, not a gap in it.
          </p>
          <p className="text-[11.5px] text-gray-500">
            A short-premium structure can look calm for a long time and give it back on one
            gap. The summary reports the worst cycle and the drawdown next to the total for
            that reason, and nothing here is a recommendation about size.
          </p>
        </div>
      </Section>

      {meta?.rules && (
        <Section title="Current settings">
          <div className="text-[12px] text-gray-400">{meta.rules}</div>
        </Section>
      )}
    </div>
  );
}
