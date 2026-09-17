import React, { useCallback, useEffect, useState } from 'react';
import { Zap, Eye, Layers, FlaskConical, ShieldAlert, CheckCircle2, XCircle, RefreshCw, Clock, Play } from 'lucide-react';
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine, Legend } from 'recharts';
import { api } from '../../api';
import { usePalette, fmtNum, fmtInt, fmtRupee, fmtSignedPct, isNum, Section, Tip, Empty } from './ui';

/**
 * Signal Desk — timed Gann × OI entries for NIFTY and SENSEX, with their tested record and
 * one-click paper trades. Decisions happen server-side on each 5-min close (signal_hub).
 */

const VERDICT = {
  proven: ['badge-green', 'proven on unseen data'],
  promising: ['badge-blue', 'positive on unseen data, not yet proven'],
  losing: ['badge-red', 'lost on unseen data'],
  thin: ['badge-yellow', 'too few unseen trades'],
};
const ALERT_TEXT = [
  ['INVALIDATED', '5-min close back through the entry level'],
  ['WRITERS_FLIP', 'writers swing hard against the trade'],
  ['WALL_HARDENING', 'the wall in the target path becomes very likely to hold'],
  ['MOMENTUM_FADE', 'two adverse 5-min closes back past entry'],
];
const plusMin = (hhmm, add) => {
  const [h, m] = String(hhmm || '0:0').split(':').map(Number);
  const t = h * 60 + m + add;
  return `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`;
};
const minutesOf = (hhmm) => { const [h, m] = String(hhmm || '0:0').split(':').map(Number); return h * 60 + m; };

function VerdictBadge({ v }) {
  const [cls, label] = VERDICT[v?.grade] || ['badge', 'not backtested yet'];
  return <span className={`${cls} !text-[10px] !px-1.5`} title={v?.text}>{label}</span>;
}

function Record({ rec }) {
  const h = rec?.holdout;
  if (!h) return <div className="text-[11px] text-gray-500">Backtest still running…</div>;
  if (!h.trades) return <div className="text-[11px] text-gray-500">No trades of this setup after the holdout date.</div>;
  return (
    <div className="text-[11px] text-gray-400 space-y-0.5">
      <div className="flex items-center gap-1.5"><VerdictBadge v={rec.verdict} /></div>
      <div className="mono">unseen: {h.trades} trades · win {fmtNum(h.win_rate, 0)}% · avg <span className={h.avg_rupees >= 0 ? 'text-green-400' : 'text-red-400'}>{fmtRupee(h.avg_rupees)}</span>/lot</div>
    </div>
  );
}

function PlanCard({ title, mode, rows, rec, onPaper, busy, disabled }) {
  const [lots, setLots] = useState(1);
  return (
    <div className="rounded-lg border border-surface-3 bg-surface-2 px-3 py-2.5 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-[12.5px] font-semibold text-gray-100">{title}</span>
        <span className="text-[10px] uppercase tracking-wider text-gray-500">{mode}</span>
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11.5px]">
        {rows.map(([k, v, cls]) => (
          <React.Fragment key={k}>
            <span className="text-gray-500">{k}</span>
            <span className={`mono text-right ${cls || 'text-gray-200'}`}>{v}</span>
          </React.Fragment>
        ))}
      </div>
      <Record rec={rec} />
      <div className="flex items-center gap-2 pt-1">
        <input type="number" min={1} max={50} value={lots} onChange={(e) => setLots(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
          className="input-field !py-1 !px-2 w-16 !text-[12px]" aria-label="Lots" />
        <span className="text-[11px] text-gray-500">lots</span>
        <button disabled={busy || disabled} onClick={() => onPaper(mode, lots)}
          className="btn-primary !py-1 !px-3 text-[12px] ml-auto flex items-center gap-1.5 disabled:opacity-50">
          <Play className="w-3.5 h-3.5" />Paper trade
        </button>
      </div>
    </div>
  );
}

function SignalHero({ sig, desk, onPaper, busy }) {
  const age = desk.live_session ? minutesOf(desk.now) - minutesOf(sig.time) : null;
  const stale = age !== null && age > 10;
  const chg = isNum(sig.premium_now) && isNum(sig.premium) && sig.premium ? ((sig.premium_now - sig.premium) / sig.premium) * 100 : null;
  const sw = sig.plans?.SWING || {};
  const sc = sig.plans?.SCALP || {};
  const call = sig.side === 'CE';
  return (
    <div className={`rounded-xl border px-4 py-3 space-y-3 ${stale ? 'border-surface-3' : 'border-brand-500/60 shadow-lg shadow-brand-900/20'}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-[10.5px] uppercase tracking-wider text-brand-400 font-semibold flex items-center gap-1.5">
            <Zap className="w-3.5 h-3.5" />{stale ? `Signal ${age} min ago — don't chase` : 'Latest signal'}
          </div>
          <div className="text-[17px] sm:text-[19px] font-bold text-white leading-tight mt-0.5">
            {sig.time} analysis → BUY {desk.index} {fmtInt(sig.strike)} {sig.side} at {plusMin(sig.time, 1)}
          </div>
          <div className="text-[12px] text-gray-400 mt-0.5">
            {sig.label} · Gann {fmtInt(sig.level)} · spot {fmtNum(sig.spot, 1)} · {sig.symbol || 'contract not in chain'}
          </div>
        </div>
        <div className="text-right">
          <div className="text-[10.5px] text-gray-500">premium at signal → now</div>
          <div className="mono text-[15px] text-gray-100">{fmtNum(sig.premium)} → {fmtNum(sig.premium_now)}</div>
          {chg !== null && <div className={`mono text-[11.5px] ${chg >= 0 ? 'text-green-400' : 'text-red-400'}`}>{fmtSignedPct(chg, 1)}</div>}
        </div>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {(sig.reasons || []).map((r, i) => <span key={i} className="text-[11px] px-2 py-0.5 rounded-full bg-surface-2 border border-surface-3 text-gray-300">{r}</span>)}
      </div>
      <div className="grid md:grid-cols-2 gap-3">
        <PlanCard title="Swing — ride to the next Gann level" mode="SWING" busy={busy} disabled={!desk.live_session || stale}
          rec={sig.backtest?.SWING} onPaper={(m, l) => onPaper(sig, m, l)}
          rows={[
            ['Spot target', fmtNum(sw.spot_target, 1), 'text-green-400'],
            ['Invalidation (5-min close)', `${call ? '<' : '>'} ${fmtNum(sw.invalidation, 1)}`, 'text-red-400'],
            ['Premium catastrophe stop', fmtNum(sw.premium_stop), 'text-red-400'],
            ['Stop → entry after', fmtNum(sw.trail_after)],
            ['Square-off', sw.squareoff],
          ]} />
        <PlanCard title="Scalp — premium Gann grid" mode="SCALP" busy={busy} disabled={!desk.live_session || stale}
          rec={sig.backtest?.SCALP} onPaper={(m, l) => onPaper(sig, m, l)}
          rows={[
            ['Entry ≈', fmtNum(sc.entry)],
            ['Target 1 / 2', `${fmtNum(sc.target1, 0)} / ${fmtNum(sc.target2, 0)}`, 'text-green-400'],
            ['Stop', fmtNum(sc.stop, 0), 'text-red-400'],
            ['Max hold', `${sc.max_hold_min} min`],
            ['Lot size', fmtInt(sig.lot_size)],
          ]} />
      </div>
      <div className="text-[11px] text-gray-500 flex items-start gap-1.5">
        <ShieldAlert className="w-3.5 h-3.5 mt-0.5 shrink-0 text-amber-400" />
        <span>Exit alerts armed on swing positions (checked every 5-min close): {ALERT_TEXT.map(([, t]) => t).join(' · ')}.</span>
      </div>
    </div>
  );
}

function Watch({ desk }) {
  const w = desk.watch || [];
  return (
    <Section title={<span className="flex items-center gap-1.5"><Eye className="w-4 h-4 text-brand-400" />What would trigger next</span>}
      tip="The two Gann levels around spot and what each setup needs on the next 5-min close — plus whether today's OI currently allows it.">
      {!w.length ? <Empty>Waiting for the first 5-min candles.</Empty> : (
        <div className="space-y-1.5">
          {w.map((x) => (
            <div key={x.setup} className="flex items-center gap-2 text-[12px] rounded-lg bg-surface-2 border border-surface-3 px-2.5 py-1.5">
              {x.oi_allows ? <CheckCircle2 className="w-4 h-4 text-green-400 shrink-0" aria-label="OI allows" /> : <XCircle className="w-4 h-4 text-gray-500 shrink-0" aria-label="OI blocks" />}
              <span className={`font-semibold w-8 ${x.side === 'CE' ? 'text-emerald-400' : 'text-orange-400'}`}>{x.side}</span>
              <span className="text-gray-200 flex-1 min-w-0 truncate">{x.trigger}</span>
              <span className="mono text-gray-500 whitespace-nowrap">{fmtInt(x.distance_pts)} pts</span>
              <Tip text={x.note} />
            </div>
          ))}
        </div>
      )}
      {desk.blocked?.length > 0 && (
        <div className="mt-2 text-[11px] text-gray-500">
          Last bar ({desk.evaluated_bar}) — blocked: {desk.blocked.map((b) => `${b.setup.replace('GANN_', '').toLowerCase()} ${b.level ? fmtInt(b.level) : ''}: ${b.why}`).join(' · ')}
        </div>
      )}
    </Section>
  );
}

function Ladder({ desk }) {
  const pal = usePalette();
  const g = desk.gann || {};
  const walls = desk.walls || {};
  const marks = [
    ...(g.spot_ladder || []).map((v) => ({ v, kind: 'gann' })),
    ...(isNum(walls.ce_wall) ? [{ v: walls.ce_wall, kind: 'ce' }] : []),
    ...(isNum(walls.pe_wall) ? [{ v: walls.pe_wall, kind: 'pe' }] : []),
    { v: desk.spot, kind: 'spot' },
  ].sort((a, b) => b.v - a.v);
  return (
    <Section title={<span className="flex items-center gap-1.5"><Layers className="w-4 h-4 text-brand-400" />Gann ladder</span>}
      tip="Spot Gann levels around the index, with the call and put walls marked; and the premium grid of the ATM call and put (scalp targets and stops).">
      <div className="grid grid-cols-[1fr_1fr] gap-3">
        <div className="space-y-0.5">
          <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Spot</div>
          {marks.map((mk) => {
            if (mk.kind === 'gann') {
              return (
                <div key={`g${mk.v}`} className="flex items-center gap-2 text-[12px]">
                  <span className="mono text-gray-200 w-16 text-right">{fmtInt(mk.v)}</span>
                  <div className="flex-1 h-px bg-surface-3" /><span className="text-[10px] text-gray-500">Gann</span>
                </div>
              );
            }
            const color = mk.kind === 'spot' ? pal.spot : mk.kind === 'ce' ? pal.call : pal.put;
            const label = mk.kind === 'spot' ? 'spot' : mk.kind === 'ce' ? 'call wall' : 'put wall';
            return (
              <div key={`${mk.kind}${mk.v}`} className="flex items-center gap-2 text-[11px] my-0.5">
                <span className="mono font-semibold w-16 text-right" style={{ color }}>{mk.kind === 'spot' ? fmtNum(mk.v, 1) : fmtInt(mk.v)}</span>
                <div className={`flex-1 ${mk.kind === 'spot' ? 'h-0.5' : 'border-t border-dashed'} rounded`} style={mk.kind === 'spot' ? { background: color } : { borderColor: color }} />
                <span style={{ color }}>{label}</span>
              </div>
            );
          })}
        </div>
        <div className="space-y-2">
          <div className="text-[10px] uppercase tracking-wider text-gray-500">Premium ({desk.rules?.params?.strike === 'ITM1' ? '1 ITM' : 'ATM'})</div>
          {['CE', 'PE'].map((s) => {
            const p = g.premium?.[s];
            if (!p) return <div key={s} className="text-[11px] text-gray-500">{s}: no price</div>;
            return (
              <div key={s} className="rounded-lg bg-surface-2 border border-surface-3 px-2.5 py-1.5 text-[11.5px]">
                <div className="flex justify-between"><span className={s === 'CE' ? 'text-emerald-400' : 'text-orange-400'}>{fmtInt(p.strike)} {s}</span><span className="mono text-gray-100">{fmtNum(p.ltp)}</span></div>
                <div className="mono text-gray-400">T {fmtInt(p.plan.target1)} / {fmtInt(p.plan.target2)} · SL {fmtInt(p.plan.stop)}</div>
              </div>
            );
          })}
        </div>
      </div>
    </Section>
  );
}

function Backtest({ index, desk }) {
  const pal = usePalette();
  const bt = desk.backtest;
  const st = desk.backtest_status || {};
  const [running, setRunning] = useState(false);
  const rerun = async () => { setRunning(true); try { await api.oiLabSignalBacktestRun(index); } finally { setRunning(false); } };
  if (!bt) {
    return (
      <Section title={<span className="flex items-center gap-1.5"><FlaskConical className="w-4 h-4 text-brand-400" />How these signals did in history</span>}>
        <Empty>{st.status === 'running' ? `Backtesting on ${index} history… ${st.done || 0}/${st.total || '?'} months` : st.status === 'error' ? `Backtest unavailable: ${st.error}` : `Needs the ${index} OI study first — upload ${index} index + option history in the Data Ingestion Lab.`}</Empty>
      </Section>
    );
  }
  const eq = bt.equity || {};
  const byDate = {};
  Object.entries(eq).forEach(([mode, pts]) => pts.forEach((p) => { (byDate[p.date] = byDate[p.date] || {})[mode] = p.equity; }));
  const last = {};
  const series = Object.keys(byDate).sort().map((d) => {
    Object.assign(last, byDate[d]);           // a mode with no trade that day keeps its running total
    return { date: d, ...last };
  });
  const holdoutIdx = series.findIndex((r) => r.date >= bt.holdout);
  return (
    <Section title={<span className="flex items-center gap-1.5"><FlaskConical className="w-4 h-4 text-brand-400" />How these signals did in {index} history</span>}
      tip="The exact same rules replayed on real option premiums from the Market Store: next-bar fills, stop before target inside a bar, Zerodha charges and slippage. Wall-hold odds come from models fitted only on sessions before the holdout date."
      right={<button onClick={rerun} disabled={running || st.status === 'running'} className="btn-ghost !py-1 !px-2 text-[11.5px] flex items-center gap-1"><RefreshCw className={`w-3.5 h-3.5 ${st.status === 'running' ? 'animate-spin' : ''}`} />Re-run</button>}>
      <div className="text-[11.5px] text-gray-500 mb-2">
        {bt.sessions} sessions {bt.first} → {bt.last} · holdout from <span className="mono">{bt.holdout}</span> · 1 lot = {bt.lot} · slippage {bt.slippage_pts} pts/side · rules {bt.rules_version}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-[12px] whitespace-nowrap">
          <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
            <th className="text-left px-2 py-1 font-medium">Mode</th><th className="text-left px-2 py-1 font-medium">Period</th>
            <th className="text-right px-2 py-1 font-medium">Trades</th><th className="text-right px-2 py-1 font-medium">Win</th>
            <th className="text-right px-2 py-1 font-medium">Avg / lot</th><th className="text-right px-2 py-1 font-medium">Total</th>
            <th className="text-right px-2 py-1 font-medium">t</th><th className="text-right px-2 py-1 font-medium">Max DD</th><th className="px-2 py-1" />
          </tr></thead>
          <tbody>
            {(bt.by_mode || []).flatMap((r) => [['Before holdout', r.train, null], ['Unseen', r.holdout, r.verdict]].map(([label, s, v]) => (
              <tr key={`${r.mode}${label}`} className="border-b border-surface-3/40">
                <td className="px-2 py-1.5 text-gray-200">{r.mode}</td><td className="px-2 py-1.5 text-gray-400">{label}</td>
                <td className="px-2 py-1.5 text-right mono">{s.trades}</td>
                <td className="px-2 py-1.5 text-right mono">{isNum(s.win_rate) ? `${fmtNum(s.win_rate, 0)}%` : '—'}</td>
                <td className={`px-2 py-1.5 text-right mono ${s.avg_rupees >= 0 ? 'text-green-400' : 'text-red-400'}`}>{fmtRupee(s.avg_rupees)}</td>
                <td className={`px-2 py-1.5 text-right mono ${s.total_rupees >= 0 ? 'text-green-400' : 'text-red-400'}`}>{fmtRupee(s.total_rupees)}</td>
                <td className="px-2 py-1.5 text-right mono text-gray-400">{s.t_stat ?? '—'}</td>
                <td className="px-2 py-1.5 text-right mono text-red-400">{fmtRupee(s.max_drawdown)}</td>
                <td className="px-2 py-1.5">{v && <VerdictBadge v={v} />}</td>
              </tr>
            )))}
          </tbody>
        </table>
      </div>
      {series.length > 1 && (
        <div className="h-44 mt-3">
          <ResponsiveContainer>
            <LineChart data={series} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={pal.grid} vertical={false} />
              <XAxis dataKey="date" stroke={pal.axis} fontSize={10} tickLine={false} axisLine={false} minTickGap={60} />
              <YAxis stroke={pal.axis} fontSize={10} tickLine={false} axisLine={false} width={56} tickFormatter={(v) => `₹${Math.round(v / 1000)}k`} />
              <Tooltip contentStyle={{ background: 'rgb(var(--surface-1))', border: '1px solid rgb(var(--surface-3))', borderRadius: 8, fontSize: 11 }}
                formatter={(v, n) => [fmtRupee(v), `${n} cumulative`]} />
              <Legend wrapperStyle={{ fontSize: 11 }} iconType="plainline" />
              <ReferenceLine y={0} stroke={pal.axis} strokeOpacity={0.5} />
              {holdoutIdx > 0 && <ReferenceLine x={series[holdoutIdx].date} stroke={pal.axis} strokeDasharray="4 4" label={{ value: 'unseen →', fill: pal.axis, fontSize: 10, position: 'insideTopRight' }} />}
              <Line name="SWING" type="monotone" dataKey="SWING" stroke={pal.spot} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
              <Line name="SCALP" type="monotone" dataKey="SCALP" stroke={pal.call} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
      <details className="mt-2">
        <summary className="text-[11.5px] text-gray-400 cursor-pointer">By setup</summary>
        <div className="overflow-x-auto mt-1">
          <table className="w-full min-w-[560px] text-[11.5px]">
            <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
              <th className="text-left px-2 py-1 font-medium">Setup</th><th className="text-left px-2 py-1 font-medium">Mode</th>
              <th className="text-right px-2 py-1 font-medium">Before: trades / total</th><th className="text-right px-2 py-1 font-medium">Unseen: trades / win / total</th><th className="px-2 py-1" />
            </tr></thead>
            <tbody>{(bt.by_setup || []).map((r) => (
              <tr key={`${r.setup}${r.mode}`} className="border-b border-surface-3/40">
                <td className="px-2 py-1 text-gray-200">{r.label} <span className="text-gray-500">{r.side}</span></td><td className="px-2 py-1 text-gray-400">{r.mode}</td>
                <td className="px-2 py-1 text-right mono">{r.train.trades} / <span className={r.train.total_rupees >= 0 ? 'text-green-400' : 'text-red-400'}>{fmtRupee(r.train.total_rupees)}</span></td>
                <td className="px-2 py-1 text-right mono">{r.holdout.trades} / {isNum(r.holdout.win_rate) ? `${fmtNum(r.holdout.win_rate, 0)}%` : '—'} / <span className={(r.holdout.total_rupees || 0) >= 0 ? 'text-green-400' : 'text-red-400'}>{fmtRupee(r.holdout.total_rupees)}</span></td>
                <td className="px-2 py-1"><VerdictBadge v={r.verdict} /></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </details>
      <div className="mt-2 text-[11.5px] text-amber-500">
        {index === 'NIFTY'
          ? 'Read this before trusting a signal: on 3 years of NIFTY, trading the Gann levels without the OI filters lost 2–3× more, so the filters earn their place — but no rule variant tested was profitable before the holdout date. Paper-trade these signals for a few weeks and compare with this table before risking money.'
          : `Judge ${index} signals by the unseen-data rows above${bt.sessions < 250 ? ` — with only ${bt.sessions} sessions stored the sample is small; keep uploading ${index} history` : ''}, and by your paper results, before risking money.`}
      </div>
    </Section>
  );
}

export default function SignalDesk({ index, active = true, onPaper }) {
  const [desk, setDesk] = useState(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState('');

  const load = useCallback(async () => {
    try {
      const r = await api.oiLabDesk(index);
      if (r.status === 'ok') { setDesk(r); setErr(''); } else setErr(r.message || 'Signal desk unavailable');
    } catch (e) { setErr(String(e.message || e)); }
  }, [index]);

  useEffect(() => { setDesk(null); load(); }, [load]);
  useEffect(() => {
    if (!active || (desk && !desk.live_session)) return undefined;
    const t = setInterval(() => { if (!document.hidden) load(); }, 15000);
    return () => clearInterval(t);
  }, [active, desk, load]);

  const paper = async (sig, mode, lots) => {
    setBusy(true); setToast('');
    try {
      const r = await api.oiLabPaperOpen(sig.id, mode, lots);
      setToast(r.status === 'ok' ? `Paper ${mode} position opened: ${r.position.symbol} × ${r.position.qty} at ${fmtNum(r.position.entry_price)}` : r.message);
      if (r.status === 'ok') onPaper?.();
    } catch (e) { setToast(String(e.message || e)); } finally { setBusy(false); }
  };

  if (err && !desk) return <div className="card text-[12.5px] text-gray-400">{err}</div>;
  if (!desk) return <Empty>Loading the signal desk…</Empty>;
  const latest = desk.signals?.[0];
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Zap className="w-5 h-5 text-brand-400" />
          <span className="text-[15px] font-bold text-white">Signal Desk · {desk.index}</span>
          <span className="badge-yellow !text-[10px]">paper only</span>
        </div>
        <div className="text-[11.5px] text-gray-500 flex items-center gap-1.5">
          <Clock className="w-3.5 h-3.5" />
          {desk.live_session
            ? <>last bar evaluated <span className="mono text-gray-300">{desk.evaluated_bar || '—'}</span> · next <span className="mono text-gray-300">{desk.next_bar}</span>{desk.waiting ? ` · ${desk.waiting}` : ''}</>
            : `market closed · ${desk.session} session`}
        </div>
      </div>
      {toast && <div className="rounded-lg border border-brand-500/30 bg-brand-500/5 px-3 py-2 text-[12.5px] text-gray-200">{toast}</div>}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <div className="space-y-4 min-w-0">
          {latest ? <SignalHero sig={latest} desk={desk} onPaper={paper} busy={busy} /> : (
            <div className="rounded-xl border border-surface-3 bg-surface-2 px-4 py-4">
              <div className="text-[14px] font-semibold text-gray-100">No signal yet today</div>
              <div className="text-[12.5px] text-gray-400 mt-1">
                Signals fire on 5-min closes between {desk.rules.entry_window[0]} and {desk.rules.entry_window[1]}
                (no new entries after {desk.rules.expiry_last_entry} on expiry day). On NIFTY history that is about one signal every three sessions —
                most days the right trade is none.
              </div>
            </div>
          )}
          {desk.signals?.length > 1 && (
            <Section title="Today's signals">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[520px] text-[12px]">
                  <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                    <th className="text-left px-2 py-1 font-medium">Bar</th><th className="text-left px-2 py-1 font-medium">Setup</th>
                    <th className="text-left px-2 py-1 font-medium">Contract</th><th className="text-right px-2 py-1 font-medium">Premium → now</th>
                    <th className="text-right px-2 py-1 font-medium">Target / invalid</th>
                  </tr></thead>
                  <tbody>{desk.signals.map((s) => (
                    <tr key={s.id} className="border-b border-surface-3/40">
                      <td className="px-2 py-1.5 mono text-gray-300">{s.time}</td>
                      <td className="px-2 py-1.5 text-gray-200">{s.label}</td>
                      <td className={`px-2 py-1.5 mono ${s.side === 'CE' ? 'text-emerald-400' : 'text-orange-400'}`}>{fmtInt(s.strike)} {s.side}</td>
                      <td className="px-2 py-1.5 text-right mono">{fmtNum(s.premium)} → {fmtNum(s.premium_now)}</td>
                      <td className="px-2 py-1.5 text-right mono text-gray-400">{fmtInt(s.spot_target)} / {fmtInt(s.invalidation)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </Section>
          )}
          <Backtest index={desk.index} desk={desk} />
        </div>
        <div className="space-y-4 min-w-0">
          <Watch desk={desk} />
          <Ladder desk={desk} />
        </div>
      </div>
    </div>
  );
}
