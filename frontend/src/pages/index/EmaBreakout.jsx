import React, { useCallback, useEffect, useState } from 'react';
import {
  Loader2, Play, Square, RefreshCw, AlertTriangle, Radio, BarChart3, BookOpen,
  TrendingUp, TrendingDown, Target, ListTree,
} from 'lucide-react';
import { api } from '../../api';
import { Note, Section, Stat, Field, input, N, N0, RS, PCT, tone } from '../../components/fluxlab/ui';

/**
 * 20 EMA Single Candle Breakout — long and short.
 *
 * A close through the EMA arms a setup; the first counter-coloured candle inside the next two
 * candles sets the trigger at its high (long) or low (short); entry is a close through that
 * trigger; exit is a close back across the EMA.
 *
 * The index decides the direction; the trade is taken in the option chain. Buying and selling
 * are genuinely different risks, so the two are never presented as interchangeable — selling
 * carries its own warning wherever it is chosen.
 */

const TABS = [
  { id: 'desk', label: 'Live desk', icon: Radio },
  { id: 'backtest', label: 'Backtest', icon: BarChart3 },
  { id: 'events', label: 'Event log', icon: ListTree },
  { id: 'rules', label: 'The rules', icon: BookOpen },
];

const RSA = (v) => (v == null ? '—' : `₹${Math.abs(Number(v)).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`);
const today = () => new Date().toISOString().slice(0, 10);
const daysAgo = (n) => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);
const HHMM = (m) => (m == null ? '—' : `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`);

const VERDICT_CLS = {
  ok: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
  weak: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
  thin: 'border-sky-500/40 bg-sky-500/10 text-sky-300',
  bad: 'border-red-500/40 bg-red-500/10 text-red-300',
  none: 'border-surface-3 bg-surface-2/40 text-gray-300',
};

const EVENT_CLS = {
  bull_cross: 'text-emerald-400', bear_cross: 'text-red-400',
  long_confirmation: 'text-emerald-300', short_confirmation: 'text-red-300',
  buy_entry: 'text-emerald-400 font-semibold', sell_entry: 'text-red-400 font-semibold',
  exit_long: 'text-amber-400', exit_short: 'text-amber-400',
  setup_expired: 'text-gray-500', state_reset: 'text-gray-600',
  signal_suppressed: 'text-amber-500',
};
const EVENT_LABEL = {
  bull_cross: 'Bullish EMA crossover', bear_cross: 'Bearish EMA crossover',
  long_confirmation: 'Long confirmation', short_confirmation: 'Short confirmation',
  buy_entry: 'BUY entry', sell_entry: 'SELL entry',
  exit_long: 'Exit long', exit_short: 'Exit short',
  setup_expired: 'Setup expired', state_reset: 'State reset',
  signal_suppressed: 'Signal suppressed',
};

export default function EmaBreakout() {
  const [tab, setTab] = useState('desk');
  const [meta, setMeta] = useState(null);
  const [deskData, setDesk] = useState(null);
  const [params, setParams] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const [range, setRange] = useState({ start: daysAgo(365), end: today() });
  const [slip, setSlip] = useState(0.5);
  const [result, setResult] = useState(null);
  const [running, setRunning] = useState(false);

  const loadMeta = useCallback(async () => {
    try {
      const d = await api.emaMeta();
      setMeta(d);
      setParams((cur) => cur || d?.config?.params || d?.defaults || null);
    } catch (e) { setErr(String(e.message || e)); }
  }, []);

  const loadDesk = useCallback(async () => {
    try { setDesk(await api.emaDesk()); } catch (e) { setErr(String(e.message || e)); }
  }, []);

  useEffect(() => { loadMeta(); loadDesk(); }, [loadMeta, loadDesk]);
  useEffect(() => {
    if (tab !== 'desk' && tab !== 'events') return undefined;
    const t = setInterval(loadDesk, 5000);
    return () => clearInterval(t);
  }, [tab, loadDesk]);

  const act = async (fn) => {
    setBusy(true); setErr('');
    try { await fn(); await loadDesk(); await loadMeta(); }
    catch (e) { setErr(String(e.message || e)); } finally { setBusy(false); }
  };

  const setP = async (patch) => {
    const next = { ...(params || {}), ...patch };
    setParams(next);
    try { await api.emaConfig({ params: patch }); } catch (e) { setErr(String(e.message || e)); }
  };

  const run = async () => {
    setRunning(true); setErr(''); setResult(null);
    try {
      const d = await api.emaBacktest({ config: { ...range, params, slippage_pts: slip } });
      if (d?.status === 'error') setErr(d.message);
      setResult(d?.result || null);
    } catch (e) { setErr(String(e.message || e)); } finally { setRunning(false); }
  };

  const selling = params?.option_action === 'SELL';

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-white">20 EMA Single Candle Breakout</h1>
          <p className="text-[12.5px] text-gray-400 mt-0.5 max-w-3xl">
            A close through the EMA arms a setup. The first <b>counter-coloured</b> candle in
            the next two candles sets the trigger — its high for a long, its low for a short.
            Entry is a close through that trigger; exit is a close back across the EMA.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-[11px] px-2 py-1 rounded ${deskData?.connected
            ? 'bg-emerald-500/15 text-emerald-400' : 'bg-amber-500/15 text-amber-400'}`}>
            {deskData?.connected ? 'Zerodha connected' : 'Zerodha not connected'}
          </span>
          <span className={`text-[11px] px-2 py-1 rounded ${deskData?.mode === 'live'
            ? 'bg-red-500/15 text-red-400' : 'bg-surface-3 text-gray-300'}`}>
            {deskData?.mode === 'live' ? 'LIVE mode' : 'Paper mode'}
          </span>
          {deskData?.running
            ? <button disabled={busy} onClick={() => act(api.emaStop)}
                className="btn-secondary !py-1.5 !px-3 text-[12.5px] flex items-center gap-1.5">
                <Square className="w-3.5 h-3.5" />Stop</button>
            : <button disabled={busy} onClick={() => act(api.emaStart)}
                className="btn-primary !py-1.5 !px-3 text-[12.5px] flex items-center gap-1.5">
                <Play className="w-3.5 h-3.5" />Start</button>}
          <button onClick={loadDesk}
            className="btn-secondary !py-1.5 !px-2 text-[12.5px]"><RefreshCw className="w-3.5 h-3.5" /></button>
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

      {params && (
        <Setup params={params} setP={setP} meta={meta} desk={deskData} selling={selling} />
      )}

      {tab === 'desk' && <DeskTab d={deskData} selling={selling} />}
      {tab === 'backtest' && (
        <BacktestTab range={range} setRange={setRange} slip={slip} setSlip={setSlip}
          run={run} running={running} result={result} meta={meta} />
      )}
      {tab === 'events' && <EventsTab d={deskData} />}
      {tab === 'rules' && <RulesTab meta={meta} />}
    </div>
  );
}

/* ── settings ─────────────────────────────────────────────────────────────── */
function Setup({ params, setP, meta, desk, selling }) {
  const idx = meta?.indices || [];
  return (
    <Section title="Settings"
      right={<span className="text-[11px] text-gray-500">{desk?.rules}</span>}>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-7 gap-3">
        <Field label="Index" hint="NIFTY/BANKNIFTY are NFO, SENSEX is BFO">
          <select className={input} value={params.index}
            onChange={(e) => setP({ index: e.target.value })}>
            {(idx.length ? idx : [{ key: 'NIFTY' }]).map((i) => (
              <option key={i.key} value={i.key}>{i.key}</option>
            ))}
          </select>
        </Field>
        <Field label="EMA length" hint="the trend line, default 20">
          <input type="number" min={2} max={200} className={input} value={params.ema_length}
            onChange={(e) => setP({ ema_length: Number(e.target.value) })} />
        </Field>
        <Field label="Candle" hint="the timeframe the rule runs on">
          <select className={input} value={params.timeframe}
            onChange={(e) => setP({ timeframe: Number(e.target.value) })}>
            {(meta?.timeframes || [1, 3, 5, 15, 30, 60]).map((t) => (
              <option key={t} value={t}>{t} min</option>
            ))}
          </select>
        </Field>
        <Field label="Options" hint="buy the view, or sell the other side of it">
          <select className={input} value={params.option_action}
            onChange={(e) => setP({ option_action: e.target.value })}>
            <option value="BUY">Buy options</option>
            <option value="SELL">Sell options</option>
          </select>
        </Field>
        <Field label="Strike" hint="steps out of the money; negative is in the money">
          <select className={input} value={params.strike_offset}
            onChange={(e) => setP({ strike_offset: Number(e.target.value) })}>
            {[-2, -1, 0, 1, 2, 3, 4, 5].map((o) => (
              <option key={o} value={o}>
                {o === 0 ? 'ATM' : o > 0 ? `${o} OTM` : `${-o} ITM`}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Lots" hint="lot size comes from the broker, never hard-coded">
          <input type="number" min={1} max={100} className={input} value={params.lots}
            onChange={(e) => setP({ lots: Number(e.target.value) })} />
        </Field>
        <Field label="Sides" hint="which signals to take">
          <select className={input}
            value={params.allow_long && params.allow_short ? 'both' : params.allow_long ? 'long' : 'short'}
            onChange={(e) => setP({
              allow_long: e.target.value !== 'short', allow_short: e.target.value !== 'long',
            })}>
            <option value="both">Long &amp; short</option>
            <option value="long">Long only</option>
            <option value="short">Short only</option>
          </select>
        </Field>
      </div>

      <div className="mt-3 space-y-1.5">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <div className="rounded-lg border border-surface-3 bg-surface-2/40 px-2.5 py-2 text-[11.5px]">
            <span className="text-emerald-400 font-semibold">Long signal</span>
            <span className="text-gray-400"> → {params.option_action === 'BUY'
              ? 'buy a CALL' : 'sell a PUT'}</span>
          </div>
          <div className="rounded-lg border border-surface-3 bg-surface-2/40 px-2.5 py-2 text-[11.5px]">
            <span className="text-red-400 font-semibold">Short signal</span>
            <span className="text-gray-400"> → {params.option_action === 'BUY'
              ? 'buy a PUT' : 'sell a CALL'}</span>
          </div>
        </div>
        {selling && (
          <Note tone="warn">
            <b>Selling options caps the gain and leaves the loss open.</b> The premium you
            receive is the most you can make; an adverse move is not bounded by it. The exit
            is a candle close across the EMA, which does not cap a gap.
          </Note>
        )}
        {meta?.app_paper_mode && (
          <Note>
            The app itself is in paper mode, so no real order can be placed by any strategy —
            including this one in live mode.
          </Note>
        )}
      </div>
    </Section>
  );
}

/* ── live desk ────────────────────────────────────────────────────────────── */
function DeskTab({ d, selling }) {
  if (!d) return <Section><Note>Loading the desk…</Note></Section>;
  const s = d.setup || {};
  const open = (d.tickets || []).filter((t) => !t.closed);
  const closed = (d.tickets || []).filter((t) => t.closed);
  return (
    <div className="space-y-4">
      <Section title="What the desk is watching"
        right={<span className="text-[11px] text-gray-500 mono">{d.now} · {d.closed_candles} candles</span>}>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          <Stat label="State" value={d.running ? 'Running' : 'Stopped'}
            tone={d.running ? 'text-emerald-400' : 'text-gray-400'} sub={d.mode} />
          <Stat label="Setup" value={s.direction || '—'}
            tone={s.direction === 'LONG' ? 'text-emerald-400'
              : s.direction === 'SHORT' ? 'text-red-400' : 'text-gray-400'}
            sub={s.since ? `since ${s.since}` : ''} />
          <Stat label="Trigger" value={s.trigger == null ? '—' : N(s.trigger)}
            sub="a close through this fires" />
          <Stat label="Open" value={N0(open.length)} sub="position(s)" />
          <Stat label="Realised" value={RS(d.realised)} tone={tone(d.realised)} />
          <Stat label="Unrealised" value={RS(d.unrealised)} tone={tone(d.unrealised)} />
        </div>
        <div className="mt-3">
          <Note tone={s.trigger != null ? 'warn' : 'info'}>{s.state}</Note>
        </div>
      </Section>

      <Section title="Order gates"
        right={<span className="text-[11px] text-gray-500">all three must agree</span>}>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          {[
            ['This strategy', d.gates?.strategy_mode === 'live', d.gates?.strategy_mode],
            ['App trading', d.gates?.app_trading_enabled && !d.gates?.app_paper_trade,
              d.gates?.app_paper_trade ? 'paper mode' : d.gates?.app_trading_enabled ? 'enabled' : 'disabled'],
            ['Risk fence', true, 'checked at order time'],
          ].map(([label, ok, detail]) => (
            <div key={label} className={`rounded-lg border px-2.5 py-2 text-[11.5px] ${ok
              ? 'border-emerald-500/30 bg-emerald-500/5' : 'border-surface-3 bg-surface-2/40'}`}>
              <div className="text-[10px] uppercase tracking-wider text-gray-500">{label}</div>
              <div className={ok ? 'text-emerald-400' : 'text-gray-300'}>{detail}</div>
            </div>
          ))}
        </div>
        <div className="text-[11px] text-gray-500 mt-2">{d.gates?.note}</div>
      </Section>

      <Section title="Tickets">
        {(d.tickets || []).length === 0 ? (
          <Note>No trade yet today.</Note>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead className="text-[10px] uppercase tracking-wider text-gray-500">
                <tr className="border-b border-surface-3">
                  <th className="text-left px-2 py-1.5">Time</th>
                  <th className="text-left px-2 py-1.5">Signal</th>
                  <th className="text-left px-2 py-1.5">Contract</th>
                  <th className="text-right px-2 py-1.5">Qty</th>
                  <th className="text-right px-2 py-1.5">In</th>
                  <th className="text-right px-2 py-1.5">Now / out</th>
                  <th className="text-left px-2 py-1.5">Why out</th>
                  <th className="text-right px-2 py-1.5">P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {[...d.tickets].reverse().map((t, i) => (
                  <tr key={i} className="border-b border-surface-3/50">
                    <td className="px-2 py-1.5 mono text-gray-400">{t.taken_at}</td>
                    <td className="px-2 py-1.5 whitespace-nowrap">
                      <span className={`px-1.5 py-px rounded text-[10.5px] font-bold ${t.direction === 'LONG'
                        ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'}`}>
                        {t.direction}
                      </span>
                      <span className="text-gray-400 ml-1.5">{t.action} {t.option_type}</span>
                    </td>
                    <td className="px-2 py-1.5 mono text-gray-400 text-[11px]">{t.symbol}</td>
                    <td className="px-2 py-1.5 text-right mono text-gray-400">{N0(t.qty)}</td>
                    <td className="px-2 py-1.5 text-right mono">{N(t.entry)}</td>
                    <td className="px-2 py-1.5 text-right mono">{N(t.closed ? t.exit : t.ltp)}</td>
                    <td className="px-2 py-1.5 text-gray-400">{t.exit_reason || (t.closed ? '—' : 'open')}</td>
                    <td className={`px-2 py-1.5 text-right mono ${tone(t.closed ? t.pnl : t.unrealised)}`}>
                      {RS(t.closed ? t.pnl : t.unrealised)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {(d.tickets || []).some((t) => t.needs_attention) && (
          <div className="mt-2"><Note tone="warn">
            A live exit was refused and the position is still open — it needs attention in the
            broker terminal.
          </Note></div>
        )}
      </Section>

      {(d.log || []).length > 0 && (
        <Section title="Desk log">
          <div className="space-y-0.5 max-h-56 overflow-auto">
            {d.log.map((l, i) => (
              <div key={i} className="text-[11.5px] text-gray-400 mono">{l}</div>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}

/* ── event log ────────────────────────────────────────────────────────────── */
function EventsTab({ d }) {
  const ev = d?.events || [];
  return (
    <Section title="Every event the rule produced today"
      right={<span className="text-[11px] text-gray-500">{ev.length} event(s)</span>}>
      {ev.length === 0 ? (
        <Note>Nothing yet. Events appear as each candle closes.</Note>
      ) : (
        <div className="overflow-x-auto max-h-[30rem]">
          <table className="w-full text-[11.5px]">
            <thead className="text-[10px] uppercase tracking-wider text-gray-500 sticky top-0 bg-surface-1">
              <tr className="border-b border-surface-3">
                <th className="text-left px-2 py-1.5">Candle</th>
                <th className="text-left px-2 py-1.5">Index</th>
                <th className="text-left px-2 py-1.5">TF</th>
                <th className="text-left px-2 py-1.5">Event</th>
                <th className="text-left px-2 py-1.5">Direction</th>
                <th className="text-right px-2 py-1.5">Close</th>
                <th className="text-right px-2 py-1.5">EMA</th>
                <th className="text-right px-2 py-1.5">Trigger</th>
                <th className="text-left px-2 py-1.5">Reason</th>
              </tr>
            </thead>
            <tbody>
              {ev.map((e, i) => (
                <tr key={i} className="border-b border-surface-3/40">
                  <td className="px-2 py-1 mono text-gray-400">{e.time || '—'}</td>
                  <td className="px-2 py-1 text-gray-500">{e.index}</td>
                  <td className="px-2 py-1 text-gray-500">{e.timeframe}m</td>
                  <td className={`px-2 py-1 ${EVENT_CLS[e.kind] || 'text-gray-300'}`}>
                    {EVENT_LABEL[e.kind] || e.kind}
                  </td>
                  <td className="px-2 py-1 text-gray-400">{e.direction || '—'}</td>
                  <td className="px-2 py-1 text-right mono">{N(e.close)}</td>
                  <td className="px-2 py-1 text-right mono text-gray-500">{N(e.ema)}</td>
                  <td className="px-2 py-1 text-right mono">{e.trigger == null ? '—' : N(e.trigger)}</td>
                  <td className="px-2 py-1 text-gray-500">{e.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

/* ── backtest ─────────────────────────────────────────────────────────────── */
function BacktestTab({ range, setRange, slip, setSlip, run, running, result, meta }) {
  const r = result;
  return (
    <div className="space-y-4">
      <Section title="Run it over history"
        right={<button onClick={run} disabled={running}
          className="btn-primary !py-1.5 !px-3 text-[12.5px] flex items-center gap-1.5">
          {running ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Target className="w-3.5 h-3.5" />}
          {running ? 'Running…' : 'Run'}</button>}>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Field label="From"><input type="date" className={input} value={range.start}
            onChange={(e) => setRange({ ...range, start: e.target.value })} /></Field>
          <Field label="To"><input type="date" className={input} value={range.end}
            onChange={(e) => setRange({ ...range, end: e.target.value })} /></Field>
          <Field label="Slippage" hint="premium points per side">
            <input type="number" step={0.25} min={0} max={20} className={input} value={slip}
              onChange={(e) => setSlip(Number(e.target.value))} /></Field>
        </div>
        {meta?.store?.NIFTY && (
          <div className="text-[11px] text-gray-500 mt-2.5">Store holds {meta.store.NIFTY}.</div>
        )}
      </Section>

      {running && <Section><div className="flex items-center gap-2 text-[12.5px] text-gray-400">
        <Loader2 className="w-4 h-4 animate-spin" />Pricing every signal on real option candles…
      </div></Section>}

      {r && ['bad_request', 'no_data', 'no_option_data'].includes(r.status) && (
        <Section title="No result">
          <Note tone="warn">{r.message}</Note>
          {r.index_points && <IndexPoints ip={r.index_points} />}
        </Section>
      )}

      {r && ['ok', 'no_trades'].includes(r.status) && <Results r={r} />}
    </div>
  );
}

function IndexPoints({ ip }) {
  return (
    <div className="mt-3">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="Signals" value={N0(ip.trades)} />
        <Stat label="Index points" value={ip.net_points == null ? '—' : N(ip.net_points, 1)}
          tone={tone(ip.net_points)} />
        <Stat label="Per signal" value={ip.avg_points == null ? '—' : N(ip.avg_points, 2)}
          tone={tone(ip.avg_points)} sub="points" />
        <Stat label="Win rate" value={PCT(ip.win_rate)} sub="on the index" />
      </div>
      <div className="text-[11px] text-gray-500 mt-2">{ip.note}</div>
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
          {r.span?.start} → {r.span?.end} · {r.span?.days} sessions · {r.index} · {r.span?.timeframe}m · lot {r.lot_size}
        </span>}>
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3">
          <Stat label="Net" value={RS(o.net)} tone={tone(o.net)} sub={`${o.trades} trades`} />
          <Stat label="Per trade" value={RS(o.avg)} tone={tone(o.avg)} />
          <Stat label="Win rate" value={PCT(o.win_rate)} sub={`${o.wins}W / ${o.losses}L`} />
          <Stat label="Profit factor" value={o.profit_factor == null ? '—' : N(o.profit_factor)} />
          <Stat label="Max drawdown" value={RSA(o.max_drawdown)} tone="text-red-400" />
          <Stat label="Worst trade" value={RS(o.worst)} tone="text-red-400" />
          <Stat label="t-statistic" value={o.t_stat == null ? '—' : N(o.t_stat)}
            tone={o.t_stat == null ? 'text-gray-400' : o.t_stat >= 2 ? 'text-emerald-400' : 'text-amber-400'}
            sub="2 is the usual bar" />
          <Stat label="Skipped" value={N0(r.skipped)} sub={`of ${r.signals} signals`} />
        </div>
        <div className="text-[11.5px] text-gray-500 mt-3">
          {r.costs?.note} {r.lot_note}
        </div>
        <IndexPoints ip={r.index_points} />
      </Section>

      {(r.skipped_reasons || []).length > 0 && (
        <Section title="Why signals were skipped">
          <div className="space-y-1.5">
            {r.skipped_reasons.map(([why, n], i) => (
              <div key={i} className="flex items-start gap-2 text-[12px]">
                <span className="mono text-gray-500 shrink-0 w-10 text-right">{n}×</span>
                <span className="text-gray-400">{why}</span>
              </div>
            ))}
          </div>
        </Section>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Section title="By direction">
          <div className="grid grid-cols-2 gap-3">
            {['LONG', 'SHORT'].map((k) => {
              const s = r.by_direction?.[k] || {};
              const Icon = k === 'LONG' ? TrendingUp : TrendingDown;
              return (
                <div key={k}>
                  <div className="text-[10px] uppercase tracking-wider text-gray-500 flex items-center gap-1">
                    <Icon className="w-3 h-3" />{k}
                  </div>
                  <div className={`text-[16px] font-semibold mono ${tone(s.net)}`}>{RS(s.net)}</div>
                  <div className="text-[11px] text-gray-500">
                    {s.trades || 0} trades · {PCT(s.win_rate)} · t={s.t_stat == null ? '—' : N(s.t_stat)}
                  </div>
                </div>
              );
            })}
          </div>
        </Section>
        <Section title="How trades ended">
          <div className="space-y-1">
            {Object.entries(r.exit_reasons || {}).map(([k, b]) => (
              <div key={k} className="flex items-center justify-between text-[12px]">
                <span className="text-gray-400">{k} <span className="text-gray-600">× {b.n}</span></span>
                <span className={`mono ${tone(b.net)}`}>{RS(b.net)}</span>
              </div>
            ))}
          </div>
        </Section>
      </div>

      {(r.trades || []).length > 0 && (
        <Section title="The trades"
          right={r.trades_truncated && <span className="text-[11px] text-gray-500">
            {r.trades_truncated.shown} of {r.trades_truncated.of} shown</span>}>
          <div className="overflow-x-auto max-h-[28rem]">
            <table className="w-full text-[11.5px]">
              <thead className="text-[10px] uppercase tracking-wider text-gray-500 sticky top-0 bg-surface-1">
                <tr className="border-b border-surface-3">
                  <th className="text-left px-2 py-1.5">Date</th>
                  <th className="text-left px-2 py-1.5">Dir</th>
                  <th className="text-left px-2 py-1.5">Contract</th>
                  <th className="text-right px-2 py-1.5">Trigger</th>
                  <th className="text-left px-2 py-1.5">In</th>
                  <th className="text-left px-2 py-1.5">Out</th>
                  <th className="text-right px-2 py-1.5">Prem in</th>
                  <th className="text-right px-2 py-1.5">Prem out</th>
                  <th className="text-right px-2 py-1.5">Idx pts</th>
                  <th className="text-left px-2 py-1.5">Why out</th>
                  <th className="text-right px-2 py-1.5">Net</th>
                </tr>
              </thead>
              <tbody>
                {[...r.trades].reverse().map((t, i) => (
                  <tr key={i} className="border-b border-surface-3/40">
                    <td className="px-2 py-1 mono text-gray-400">{t.entry_date}</td>
                    <td className={`px-2 py-1 ${t.direction === 'LONG' ? 'text-emerald-400' : 'text-red-400'}`}>
                      {t.direction}
                    </td>
                    <td className="px-2 py-1 mono text-gray-400">{t.contract}</td>
                    <td className="px-2 py-1 text-right mono text-gray-500">{N(t.trigger)}</td>
                    <td className="px-2 py-1 mono text-gray-500">{t.entry_time}</td>
                    <td className="px-2 py-1 mono text-gray-500">{t.exit_time}</td>
                    <td className="px-2 py-1 text-right mono">{N(t.entry_premium)}</td>
                    <td className="px-2 py-1 text-right mono">{N(t.exit_premium)}</td>
                    <td className={`px-2 py-1 text-right mono ${tone(t.index_points)}`}>{N(t.index_points, 1)}</td>
                    <td className="px-2 py-1 text-gray-400">{t.exit_reason}</td>
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

/* ── rules ────────────────────────────────────────────────────────────────── */
function RulesTab({ meta }) {
  return (
    <div className="space-y-4">
      <Section title="The rule, exactly">
        <div className="text-[12.5px] text-gray-300 space-y-2.5 max-w-3xl">
          <p><b>1. The crossover arms a setup.</b> A candle that closes above the EMA when the
            previous one closed below it arms a LONG setup; the mirror arms a SHORT. A setup
            can only be armed when flat.</p>
          <p><b>2. Two candles, no more.</b> Only the next two candles are examined — the
            crossover candle itself does not count. A qualifying candle on the third is too
            late and the setup expires.</p>
          <p><b>3. The confirmation candle is counter-coloured.</b> For a LONG it is the first
            <b> red</b> candle (close below open) that still closes <b>above</b> the EMA, and
            its <b>high</b> becomes the trigger. For a SHORT it is the first <b>green</b>
            candle that still closes <b>below</b> the EMA, and its <b>low</b> is the trigger.</p>
          <p><b>4. Entry is a close through the trigger</b> — not a touch, not an intrabar
            spike. The earliest it can happen is the candle after confirmation, because the
            crossover test needs a previous close on the other side of the level.</p>
          <p><b>5. Exit is a close back across the EMA.</b> A completed candle closing below
            the EMA ends a long; above it ends a short. Intrabar movement does not exit.</p>
          <p><b>6. One position at a time, no pyramiding.</b> After an entry the setup is
            switched off, so the same break cannot fire twice; all setup state clears one
            candle after the position closes, which is where the source clears it.</p>
        </div>
      </Section>

      <Section title="How the index signal becomes an option trade">
        <div className="text-[12.5px] text-gray-300 space-y-2.5 max-w-3xl">
          <p>The rule is a view on the index. You choose how to express it:</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[12px]">
            <div className="rounded-lg border border-surface-3 bg-surface-2/40 p-2.5">
              <div className="text-emerald-400 font-semibold mb-1">Buying</div>
              long → buy a CALL · short → buy a PUT<br />
              <span className="text-gray-500">Loss is capped at the premium. Decay works
                against you every day you are in.</span>
            </div>
            <div className="rounded-lg border border-surface-3 bg-surface-2/40 p-2.5">
              <div className="text-red-400 font-semibold mb-1">Selling</div>
              long → sell a PUT · short → sell a CALL<br />
              <span className="text-gray-500">Decay works for you, the gain is capped at the
                premium, and the loss is not capped at all.</span>
            </div>
          </div>
          <p>The strike is chosen in steps from the money, so one setting means the same
            distance from spot whichever of the four combinations is in force. Lot size comes
            from the broker's instrument master live — never a stored constant, because
            exchanges revise them.</p>
        </div>
      </Section>

      <Section title="What is measured, and what is assumed">
        <div className="text-[12.5px] text-gray-300 space-y-2.5 max-w-3xl">
          <p><b>No look-ahead.</b> The state machine sees one candle at a time. A trade opens
            on the close of its entry candle and closes on the close of its exit candle, which
            is what <span className="mono text-gray-400">process_orders_on_close</span> means
            in the source. The live desk withholds the forming candle entirely.</p>
          <p><b>Real prices.</b> The backtest prices every signal on the actual option contract
            from the Market Store, with Zerodha statutory charges and slippage on both sides.
            A minute with no print carries the last trade and is counted, never interpolated.</p>
          <p><b>Two columns, on purpose.</b> "Index points" is what the rule did to the
            underlying; the option column is what the trade returned. They can disagree
            sharply — the option column is the one that was paid.</p>
          <p className="text-[11.5px] text-gray-500">Square-off and the entry window are added
            on top of the source, which has neither: an intraday option position left open
            overnight is a different strategy from the one being tested.</p>
        </div>
      </Section>
    </div>
  );
}
