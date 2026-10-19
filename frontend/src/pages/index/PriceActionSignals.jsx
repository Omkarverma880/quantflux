import React, { useCallback, useEffect, useState } from 'react';
import {
  Loader2, RefreshCw, AlertTriangle, Radio, BarChart3, BookOpen, ArrowUpRight,
  ArrowDownRight, Clock, Target,
} from 'lucide-react';
import { api } from '../../api';
import { Note, Section, Stat, Field, input, N, N0, RS, PCT, tone } from '../../components/fluxlab/ui';

/**
 * Price Action Signals — the Pine indicator, live.
 *
 * The Live tab is the point of this page: the two levels that would trigger, which signals
 * have fired on CLOSED candles today, and the option each one implies. The Backtest tab is
 * what those same signals did on real option prices, and it is allowed to say the answer is
 * no — the verdict is rendered as prominently when it is bad as when it is good.
 */

const TABS = [
  { id: 'live', label: 'Live signals', icon: Radio },
  { id: 'backtest', label: 'Backtest', icon: BarChart3 },
  { id: 'rules', label: 'The rules', icon: BookOpen },
];

// RS() signs its value, which is right for P&L and wrong for a magnitude: a drawdown of
// 45,150 is not "+₹45,150".
const RSA = (v) => (v == null ? '—' : `₹${Math.abs(Number(v)).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`);
const today = () => new Date().toISOString().slice(0, 10);
const daysAgo = (n) => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);

const VERDICT_CLS = {
  ok: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
  mixed: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
  weak: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
  thin: 'border-sky-500/40 bg-sky-500/10 text-sky-300',
  bad: 'border-red-500/40 bg-red-500/10 text-red-300',
  none: 'border-surface-3 bg-surface-2/40 text-gray-300',
};

export default function PriceActionSignals() {
  const [tab, setTab] = useState('live');
  const [meta, setMeta] = useState(null);
  const [err, setErr] = useState('');

  const [params, setParams] = useState({
    ema_len: 20, swing_len: 10, use_vwap: true, use_retest: false,
    allow_bull: true, allow_bear: true,
  });
  const [timeframe, setTimeframe] = useState(5);
  const [offset, setOffset] = useState(0);

  const [live, setLive] = useState(null);
  const [loadingLive, setLoadingLive] = useState(false);

  const [range, setRange] = useState({ start: daysAgo(365), end: today() });
  const [exits, setExits] = useState({ target_pct: 60, stop_pct: 30, max_hold_min: 60, moneyness: 0, slippage_pts: 0.5 });
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    api.pasMeta().then(setMeta).catch((e) => setErr(String(e.message || e)));
  }, []);

  const loadLive = useCallback(async () => {
    setLoadingLive(true);
    try {
      const d = await api.pasLive({ params, timeframe, strike_offset: offset });
      setLive(d?.live || null);
      setErr(d?.status === 'error' ? d.message : '');
    } catch (e) { setErr(String(e.message || e)); } finally { setLoadingLive(false); }
  }, [params, timeframe, offset]);

  useEffect(() => { if (tab === 'live') loadLive(); }, [tab, loadLive]);
  useEffect(() => {
    if (tab !== 'live') return undefined;
    const t = setInterval(loadLive, 30000);
    return () => clearInterval(t);
  }, [tab, loadLive]);

  const run = async () => {
    setRunning(true); setErr(''); setResult(null);
    try {
      const d = await api.pasBacktest({ config: { ...range, timeframe, params, ...exits } });
      if (d?.status === 'error') setErr(d.message);
      setResult(d?.result || null);
    } catch (e) { setErr(String(e.message || e)); } finally { setRunning(false); }
  };

  const set = (patch) => setParams((p) => ({ ...p, ...patch }));
  const cov = meta?.coverage;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-white">Price Action Signals</h1>
          <p className="text-[12.5px] text-gray-400 mt-0.5 max-w-3xl">
            Quantflux Price Action Signals, translated from the Pine script line for line: a
            close through the swing high or low as a <b>fresh cross</b>, filtered by the EMA and
            VWAP, taken on <b>closed candles only</b> — and the option each signal implies.
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

      <Inputs params={params} set={set} timeframe={timeframe} setTimeframe={setTimeframe}
              timeframes={meta?.timeframes} offset={offset} setOffset={setOffset}
              showOffset={tab === 'live'} />

      {tab === 'live' && <LiveTab live={live} loading={loadingLive} connected={meta?.connected} />}

      {tab === 'backtest' && (
        <BacktestTab range={range} setRange={setRange} exits={exits} setExits={setExits}
                     run={run} running={running} result={result} coverage={cov} />
      )}

      {tab === 'rules' && <RulesTab meta={meta} />}
    </div>
  );
}

/* ── the indicator's own inputs, shared by both tabs ─────────────────────── */
function Inputs({ params, set, timeframe, setTimeframe, timeframes, offset, setOffset, showOffset }) {
  const tfs = timeframes?.length ? timeframes : [1, 3, 5, 15, 30, 60];
  return (
    <Section title="Indicator inputs">
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        <Field label="Candle" hint="the chart timeframe">
          <select className={input} value={timeframe} onChange={(e) => setTimeframe(Number(e.target.value))}>
            {tfs.map((t) => <option key={t} value={t}>{t} min</option>)}
          </select>
        </Field>
        <Field label="Swing length" hint="bars behind the level — swingLen">
          <input type="number" min={2} max={100} className={input} value={params.swing_len}
                 onChange={(e) => set({ swing_len: Number(e.target.value) })} />
        </Field>
        <Field label="EMA length" hint="the trend filter — emaLen">
          <input type="number" min={2} max={200} className={input} value={params.ema_len}
                 onChange={(e) => set({ ema_len: Number(e.target.value) })} />
        </Field>
        <Field label="Entry" hint="the break itself, or a pullback to it">
          <select className={input} value={params.use_retest ? 'retest' : 'break'}
                  onChange={(e) => set({ use_retest: e.target.value === 'retest' })}>
            <option value="break">On the break</option>
            <option value="retest">On the retest</option>
          </select>
        </Field>
        <Field label="VWAP filter" hint="useVWAP — price must also be the right side of VWAP">
          <select className={input} value={params.use_vwap ? 'on' : 'off'}
                  onChange={(e) => set({ use_vwap: e.target.value === 'on' })}>
            <option value="on">On</option><option value="off">Off</option>
          </select>
        </Field>
        {showOffset ? (
          <Field label="Strike" hint="0 is ATM, 1 is one strike out of the money">
            <select className={input} value={offset} onChange={(e) => setOffset(Number(e.target.value))}>
              <option value={-1}>1 ITM</option><option value={0}>ATM</option>
              <option value={1}>1 OTM</option><option value={2}>2 OTM</option>
            </select>
          </Field>
        ) : (
          <Field label="Sides" hint="which signals to take">
            <select className={input}
                    value={params.allow_bull && params.allow_bear ? 'both' : params.allow_bull ? 'bull' : 'bear'}
                    onChange={(e) => set({
                      allow_bull: e.target.value !== 'bear', allow_bear: e.target.value !== 'bull',
                    })}>
              <option value="both">Both</option><option value="bull">Calls only</option>
              <option value="bear">Puts only</option>
            </select>
          </Field>
        )}
      </div>
    </Section>
  );
}

/* ── live ─────────────────────────────────────────────────────────────────── */
function LiveTab({ live, loading, connected }) {
  if (loading && !live) {
    return <Section><div className="flex items-center gap-2 text-[12.5px] text-gray-400">
      <Loader2 className="w-4 h-4 animate-spin" />Reading the session…</div></Section>;
  }
  if (!live) return <Section><Note>Nothing loaded yet.</Note></Section>;
  if (!live.available) {
    return (
      <Section title="Live signals">
        <Note tone="warn">{live.message}</Note>
        {!connected && (
          <div className="text-[11.5px] text-gray-500 mt-2">
            The backtest tab works without the broker — it reads the Market Store.
          </div>
        )}
      </Section>
    );
  }

  const p = live.pending || {};
  return (
    <div className="space-y-4">
      <Section title="Where price is"
        right={<span className="text-[11px] text-gray-500 mono">
          {live.session} · {live.as_of} · {live.candles} closed candles
          {!live.is_today && ' · last session in the data'}
        </span>}>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          <Stat label="NIFTY" value={N(live.spot)} sub="last closed candle" />
          <Stat label={`${live.ema_len} EMA`} value={N(live.ema)}
                sub={live.price_vs_ema === 'above' ? 'price above' : 'price below'}
                tone={live.price_vs_ema === 'above' ? 'text-emerald-400' : 'text-red-400'} />
          <Stat label="VWAP" value={N(live.vwap)}
                sub={live.price_vs_vwap ? `price ${live.price_vs_vwap}` : 'not available'}
                tone={live.price_vs_vwap === 'above' ? 'text-emerald-400'
                  : live.price_vs_vwap === 'below' ? 'text-red-400' : 'text-gray-400'} />
          <Stat label="Swing high" value={N(live.resistance)} sub="the level above" />
          <Stat label="Swing low" value={N(live.support)} sub="the level below" />
          <Stat label="Signals today" value={N0(live.signal_count)} sub={`${live.mode} mode`} />
        </div>
        {live.blockers?.length > 0 && (
          <div className="mt-3 space-y-1.5">
            {live.blockers.map((b, i) => <Note key={i} tone="warn">{b}</Note>)}
          </div>
        )}
      </Section>

      <Section title="The levels to watch, and what each one would buy">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <Trigger one={p.resistance} bullish />
          <Trigger one={p.support} bullish={false} />
        </div>
        {live.waiting_for_retest && (
          <div className="mt-3">
            <Note tone="warn">
              <b>Waiting for a retest</b> of {N(live.waiting_for_retest.level)} on the{' '}
              {live.waiting_for_retest.side === 'BULL' ? 'upside' : 'downside'} — it needs{' '}
              {live.waiting_for_retest.needs}. The break has already happened; in retest mode
              the signal only comes on that pullback.
            </Note>
          </div>
        )}
        <div className="text-[11px] text-gray-500 mt-3">{live.note}</div>
      </Section>

      {live.latest && <LatestSignal s={live.latest} />}

      <Section title="Every signal this session"
        right={<span className="text-[11px] text-gray-500">{live.rules}</span>}>
        {live.signals?.length ? (
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead className="text-[10px] uppercase tracking-wider text-gray-500">
                <tr className="border-b border-surface-3">
                  <th className="text-left px-2 py-1.5">Time</th>
                  <th className="text-left px-2 py-1.5">Signal</th>
                  <th className="text-right px-2 py-1.5">Level</th>
                  <th className="text-right px-2 py-1.5">Close</th>
                  <th className="text-left px-2 py-1.5">What confirmed it</th>
                </tr>
              </thead>
              <tbody>
                {[...live.signals].reverse().map((s, i) => {
                  const bull = s.side === 'BULL';
                  return (
                    <tr key={i} className="border-b border-surface-3/50">
                      <td className="px-2 py-1.5 mono text-gray-400">{s.time}</td>
                      <td className="px-2 py-1.5">
                        <span className={`px-1.5 py-px rounded text-[10.5px] font-bold ${bull
                          ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'}`}>
                          Buy {s.option_type}
                        </span>
                      </td>
                      <td className="px-2 py-1.5 text-right mono">{N(s.level)}</td>
                      <td className="px-2 py-1.5 text-right mono">{N(s.close)}</td>
                      <td className="px-2 py-1.5 text-[11.5px] text-gray-400">
                        {Object.entries(s.checks || {})
                          .filter(([, v]) => v !== null)
                          .map(([k, v]) => `${k.replace(/_/g, ' ')}${v ? '' : ' ✗'}`)
                          .join(' · ')}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <Note>
            No signal has fired yet this session. That is the normal state — the rules ask for
            a fresh cross of the {live.resistance ? 'swing level' : 'level'} with the trend
            filters agreeing, and most candles do not qualify.
          </Note>
        )}
      </Section>
    </div>
  );
}

function Trigger({ one, bullish }) {
  const Icon = bullish ? ArrowUpRight : ArrowDownRight;
  const col = bullish ? 'text-emerald-400' : 'text-red-400';
  if (!one) {
    return (
      <div className="rounded-lg border border-surface-3 bg-surface-2/40 p-3">
        <div className={`text-[10px] uppercase tracking-wider ${col} flex items-center gap-1`}>
          <Icon className="w-3 h-3" />{bullish ? 'Upside trigger' : 'Downside trigger'}
        </div>
        <div className="text-[12px] text-gray-500 mt-1">
          Not formed yet — there are not enough candles behind this level.
        </div>
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-surface-3 bg-surface-2/40 p-3">
      <div className={`text-[10px] uppercase tracking-wider ${col} flex items-center gap-1`}>
        <Icon className="w-3 h-3" />{bullish ? 'Upside trigger' : 'Downside trigger'}
      </div>
      <div className="text-[22px] font-bold mono text-gray-100 mt-1 leading-tight">
        {N(one.price)}
      </div>
      <div className="text-[11.5px] text-gray-500">
        a candle must <b>close {bullish ? 'above' : 'below'}</b> this
        {one.already_beyond
          ? ' — price is already beyond it, so this level is spent until a new one forms'
          : ` · ${N(Math.abs(one.distance), 1)} pts away`}
      </div>
      <div className="mt-2 pt-2 border-t border-surface-3 text-[12px] text-gray-200">
        then buy <b className={col}>{one.would_buy}</b>
      </div>
    </div>
  );
}

function LatestSignal({ s }) {
  const bull = s.side === 'BULL';
  const o = s.option || {};
  return (
    <Section title="The most recent signal, and the contract behind it"
      right={<span className="text-[11px] text-gray-500 mono flex items-center gap-1">
        <Clock className="w-3 h-3" />{s.time}</span>}>
      <div className={`rounded-lg border p-3 ${bull
        ? 'border-emerald-500/40 bg-emerald-500/10' : 'border-red-500/40 bg-red-500/10'}`}>
        <div className="flex flex-wrap items-baseline gap-2">
          <span className={`text-[15px] font-bold ${bull ? 'text-emerald-300' : 'text-red-300'}`}>
            Buy {s.option_type}
          </span>
          <span className="text-[12px] text-gray-400">
            on the {bull ? 'break above' : 'break below'} the {s.level_name} at {N(s.level)},
            closed at {N(s.close)}
          </span>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-3">
          <Stat label="Contract" value={o.tradingsymbol || '—'} sub={o.expiry ? `expiry ${o.expiry}` : ''} />
          <Stat label="Strike" value={N0(o.strike)} sub={o.option_type} />
          <Stat label="Premium" value={o.premium == null ? '—' : N(o.premium)} sub="live quote" />
          <Stat label="One lot" value={o.cost_per_lot == null ? '—' : RSA(o.cost_per_lot)}
                sub={o.lot_size ? `${o.lot_size} qty` : ''} />
        </div>
        {o.note && <div className="text-[11.5px] text-amber-400 mt-2">{o.note}</div>}
      </div>
      <div className="text-[11px] text-gray-500 mt-2">
        The premium is the quote as of this refresh, not a fill. No order is placed from this
        page.
      </div>
    </Section>
  );
}

/* ── backtest ─────────────────────────────────────────────────────────────── */
function BacktestTab({ range, setRange, exits, setExits, run, running, result, coverage }) {
  const setE = (patch) => setExits((e) => ({ ...e, ...patch }));
  const r = result;
  return (
    <div className="space-y-4">
      <Section title="The trade each signal becomes"
        right={<button onClick={run} disabled={running}
                       className="btn-primary !py-1.5 !px-3 text-[12.5px] flex items-center gap-1.5">
          {running ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Target className="w-3.5 h-3.5" />}
          {running ? 'Running…' : 'Run'}</button>}>
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
          <Field label="From"><input type="date" className={input} value={range.start}
                                     onChange={(e) => setRange({ ...range, start: e.target.value })} /></Field>
          <Field label="To"><input type="date" className={input} value={range.end}
                                   onChange={(e) => setRange({ ...range, end: e.target.value })} /></Field>
          <Field label="Strike" hint="steps from ATM">
            <select className={input} value={exits.moneyness}
                    onChange={(e) => setE({ moneyness: Number(e.target.value) })}>
              <option value={-1}>1 ITM</option><option value={0}>ATM</option>
              <option value={1}>1 OTM</option><option value={2}>2 OTM</option>
            </select>
          </Field>
          <Field label="Target %" hint="on the premium">
            <input type="number" min={0} max={500} className={input} value={exits.target_pct}
                   onChange={(e) => setE({ target_pct: Number(e.target.value) })} /></Field>
          <Field label="Stop %" hint="on the premium">
            <input type="number" min={0} max={100} className={input} value={exits.stop_pct}
                   onChange={(e) => setE({ stop_pct: Number(e.target.value) })} /></Field>
          <Field label="Max hold" hint="minutes; 0 holds to square-off">
            <input type="number" min={0} max={375} className={input} value={exits.max_hold_min}
                   onChange={(e) => setE({ max_hold_min: Number(e.target.value) })} /></Field>
          <Field label="Slippage" hint="premium points per side">
            <input type="number" step={0.25} min={0} max={10} className={input} value={exits.slippage_pts}
                   onChange={(e) => setE({ slippage_pts: Number(e.target.value) })} /></Field>
        </div>
        {coverage && <div className="text-[11px] text-gray-500 mt-2.5">Store holds {coverage}.</div>}
      </Section>

      {running && (
        <Section><div className="flex items-center gap-2 text-[12.5px] text-gray-400">
          <Loader2 className="w-4 h-4 animate-spin" />
          Pricing every signal on the real option candles — a year takes a minute or two.
        </div></Section>
      )}

      {r && r.status !== 'ok' && (
        <Section title="No result">
          <Note tone="warn">{r.message}</Note>
          {r.coverage && <div className="text-[11.5px] text-gray-500 mt-2">Store holds {r.coverage}.</div>}
        </Section>
      )}

      {r?.status === 'ok' && <Results r={r} />}
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
          {r.span?.start} → {r.span?.end} · {r.span?.days} sessions · {r.span?.timeframe}m
        </span>}>
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
          <Stat label="Net" value={RS(o.net)} tone={tone(o.net)} sub={`${o.trades} trades`} />
          <Stat label="Per trade" value={RS(o.avg)} tone={tone(o.avg)} sub={`median ${RS(o.median)}`} />
          <Stat label="Win rate" value={PCT(o.win_rate)}
                sub={`${RS(o.avg_win)} / ${RS(o.avg_loss)}`} />
          <Stat label="t-statistic" value={o.t_stat == null ? '—' : N(o.t_stat)}
                tone={o.t_stat == null ? 'text-gray-400' : o.t_stat >= 2 ? 'text-emerald-400' : 'text-amber-400'}
                sub="2 is the usual bar" />
          <Stat label="p-value" value={o.p_value == null ? '—' : N(o.p_value, 3)}
                sub="bootstrap, one-sided" />
          <Stat label="Profit factor" value={o.profit_factor == null ? '—' : N(o.profit_factor)} />
          <Stat label="Max drawdown" value={RSA(o.max_drawdown)} tone="text-red-400"
                sub={`worst trade ${RS(o.worst)}`} />
        </div>
        <div className="text-[11.5px] text-gray-500 mt-3">
          {r.signal_count} signals fired, {r.taken} became trades
          {r.skipped > 0 && ` — ${r.skipped} were skipped because ${r.skipped_why}`}.
          Levels: {r.levels_basis}. {r.costs?.note} Slippage{' '}
          {N(r.costs?.slippage_pts_per_side, 2)} pts a side.
          {r.span?.note && ` ${r.span.note}`}
        </div>
      </Section>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Section title="Split in half" right={<span className="text-[11px] text-gray-500 mono">
          at {r.split_at}</span>}>
          <div className="grid grid-cols-2 gap-3">
            {[['First half', r.in_sample], ['Second half', r.out_of_sample]].map(([lab, s]) => (
              <div key={lab}>
                <div className="text-[10px] uppercase tracking-wider text-gray-500">{lab}</div>
                <div className={`text-[16px] font-semibold mono ${tone(s?.net)}`}>{RS(s?.net)}</div>
                <div className="text-[11px] text-gray-500">
                  {s?.trades || 0} trades · t={s?.t_stat == null ? '—' : N(s.t_stat)}
                </div>
              </div>
            ))}
          </div>
          <div className="text-[11px] text-gray-500 mt-2.5">
            The second half is not a forward test — the rules were chosen knowing the whole
            span existed. It is the weakest useful check, and a split that only works in the
            first half is the clearest warning there is.
          </div>
        </Section>

        <Section title="By side, and by how each trade ended">
          <div className="grid grid-cols-2 gap-3">
            {['BULL', 'BEAR'].map((k) => {
              const s = r.by_side?.[k] || {};
              return (
                <div key={k}>
                  <div className="text-[10px] uppercase tracking-wider text-gray-500">
                    {k === 'BULL' ? 'Calls' : 'Puts'}
                  </div>
                  <div className={`text-[16px] font-semibold mono ${tone(s.net)}`}>{RS(s.net)}</div>
                  <div className="text-[11px] text-gray-500">
                    {s.trades || 0} trades · {PCT(s.win_rate)} · t={s.t_stat == null ? '—' : N(s.t_stat)}
                  </div>
                </div>
              );
            })}
          </div>
          <div className="mt-3 pt-3 border-t border-surface-3 space-y-1">
            {Object.entries(r.exit_reasons || {}).map(([k, b]) => (
              <div key={k} className="flex items-center justify-between text-[12px]">
                <span className="text-gray-400">{k} <span className="text-gray-600">× {b.n}</span></span>
                <span className={`mono ${tone(b.net)}`}>{RS(b.net)}</span>
              </div>
            ))}
          </div>
        </Section>
      </div>

      <Section title="The trades"
        right={r.trades_truncated && <span className="text-[11px] text-gray-500">
          {r.trades_truncated.shown} of {r.trades_truncated.of} shown</span>}>
        <div className="overflow-x-auto max-h-[28rem]">
          <table className="w-full text-[11.5px]">
            <thead className="text-[10px] uppercase tracking-wider text-gray-500 sticky top-0 bg-surface-1">
              <tr className="border-b border-surface-3">
                <th className="text-left px-2 py-1.5">Date</th>
                <th className="text-left px-2 py-1.5">Side</th>
                <th className="text-left px-2 py-1.5">Contract</th>
                <th className="text-right px-2 py-1.5">In</th>
                <th className="text-right px-2 py-1.5">Out</th>
                <th className="text-left px-2 py-1.5">Held</th>
                <th className="text-left px-2 py-1.5">Why out</th>
                <th className="text-right px-2 py-1.5">Net</th>
              </tr>
            </thead>
            <tbody>
              {[...(r.trades || [])].reverse().map((t, i) => (
                <tr key={i} className="border-b border-surface-3/50">
                  <td className="px-2 py-1 mono text-gray-400">{t.date}</td>
                  <td className="px-2 py-1">
                    <span className={t.tag === 'BULL' ? 'text-emerald-400' : 'text-red-400'}>
                      {t.side}
                    </span>
                  </td>
                  <td className="px-2 py-1 mono text-gray-400">{t.contract}</td>
                  <td className="px-2 py-1 text-right mono">{N(t.entry)}</td>
                  <td className="px-2 py-1 text-right mono">{N(t.exit)}</td>
                  <td className="px-2 py-1 mono text-gray-500">{t.held_min}m</td>
                  <td className="px-2 py-1 text-gray-400">{t.exit_reason}</td>
                  <td className={`px-2 py-1 text-right mono ${tone(t.pnl_rs)}`}>{RS(t.pnl_rs)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}

/* ── rules ────────────────────────────────────────────────────────────────── */
function RulesTab({ meta }) {
  return (
    <div className="space-y-4">
      <Section title="The signal, exactly as the script defines it">
        <div className="text-[12.5px] text-gray-300 space-y-2.5 max-w-3xl">
          <p>
            <b>The level.</b> Resistance is the highest high of the previous <i>swingLen</i>
            {' '}candles and support the lowest low — <span className="mono text-gray-400">
            ta.highest(high[1], swingLen)</span>. The <span className="mono text-gray-400">[1]
            </span> matters: the level a candle breaks is built from the candles <i>before</i> it,
            never from its own high.
          </p>
          <p>
            <b>The break is a fresh cross.</b>{' '}
            <span className="mono text-gray-400">close &gt; resistance and close[1] &lt;= resistance[1]</span>.
            The second half is the part most versions drop, and it is what stops one breakout
            becoming forty — the previous candle must have been at or below its own level.
          </p>
          <p>
            <b>The trend filter.</b> A call needs price above the EMA, and above VWAP when the
            VWAP filter is on. A put needs the mirror. Nothing fires against the filter.
          </p>
          <p>
            <b>The retest, when enabled.</b> The break arms a latch; the signal then waits for a
            candle that trades back into the level, closes back through it, and closes in
            colour. One break offers one retest and no more.
          </p>
          <p>
            <b>Closed candles only.</b>{' '}
            <span className="mono text-gray-400">barstate.isconfirmed</span> is the whole
            discipline. The live tab withholds the candle still forming, so nothing you see
            here can un-happen — which is exactly what a repainting indicator does.
          </p>
        </div>
      </Section>

      <Section title="What this page adds, and what it does not claim">
        <div className="text-[12.5px] text-gray-300 space-y-2.5 max-w-3xl">
          <p>
            The indicator draws a label. This page also names the option that label implies,
            with its live premium, so a signal is something you can act on. It places no
            orders.
          </p>
          <p>
            The backtest prices every signal on the <b>real option candles</b> in the Market
            Store, not a model: the decision is made on a closed candle and filled at the next
            candle's open, the stop is tested before the target inside a candle, a candle that
            opens through the stop fills at that open, and real Zerodha charges plus slippage
            come off every round trip.
          </p>
          <p>
            It reports a t-statistic, a bootstrap p-value and a two-half split, and the verdict
            says plainly when the numbers do not support a conclusion. An indicator is a way of
            describing price. Whether buying options on it makes money is a separate question,
            and the backtest tab is where it gets answered rather than assumed.
          </p>
          <p className="text-[11.5px] text-gray-500">
            VWAP note: Pine weights VWAP by the chart's volume. NIFTY is an index, so the
            weight used here is whatever volume column the candles carry; with none usable it
            falls back to a running mean of hlc3 and says so rather than drawing a flat line
            that looks like a working VWAP.
          </p>
        </div>
      </Section>

      {meta?.source && (
        <Section title="Source"><div className="text-[12px] text-gray-400">{meta.source}</div></Section>
      )}
    </div>
  );
}
