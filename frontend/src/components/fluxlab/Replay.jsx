import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Play, Pause, SkipForward, SkipBack, Loader2, Check, Minus } from 'lucide-react';
import CandleChart from '../CandleChart';
import { Section, Stat, Note, N, PCT, RS, tone, input } from './ui';

/**
 * Bar-by-bar replay of one session, fed through the same engine as the backtest.
 *
 * What you step through is what was measured: the trades shown are the engine's own output for
 * this date and configuration, and the condition panel is the decision it made on that exact bar.
 */

const SPEEDS = [['slow', 900], ['normal', 420], ['fast', 160], ['very fast', 60]];

export default function Replay({ cfg, onLoad, data, busy, date, setDate }) {
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(420);
  const timer = useRef(null);
  const steps = data?.steps || [];

  useEffect(() => { setI(0); setPlaying(false); }, [data]);
  useEffect(() => {
    clearTimeout(timer.current);
    if (!playing || !steps.length) return undefined;
    if (i >= steps.length - 1) { setPlaying(false); return undefined; }
    timer.current = setTimeout(() => setI((n) => Math.min(n + 1, steps.length - 1)), speed);
    return () => clearTimeout(timer.current);
  }, [playing, i, speed, steps.length]);

  const step = steps[i];
  const shown = useMemo(() => steps.slice(0, i + 1), [steps, i]);
  const candles = shown.map((s) => ({ t: s.time, open: s.open, high: s.high, low: s.low, close: s.close, volume: s.volume }));
  const overlays = [
    { key: 'vwap', label: 'VWAP', color: '#34d399', values: shown.map((s) => s.vwap) },
    { key: 'ema_fast', label: 'fast EMA', color: '#f59e0b', values: shown.map((s) => s.ema_fast) },
    { key: 'ema_slow', label: 'slow EMA', color: '#a78bfa', values: shown.map((s) => s.ema_slow) },
    { key: 'bb_upper', label: 'BB', color: '#64748b', values: shown.map((s) => s.bb_upper) },
    { key: 'bb_lower', label: '', color: '#64748b', values: shown.map((s) => s.bb_lower) },
  ];
  // trades that have already happened by this bar
  const done = (data?.trades || []).filter((t) => t.time <= (step?.time || '00:00'));
  const markers = done.map((t) => ({
    index: steps.findIndex((s) => s.time === t.time),
    label: t.side, color: t.side === 'CE' ? '#10b981' : '#ef4444',
    side: t.side === 'CE' ? 'up' : 'down',
  })).filter((m) => m.index >= 0 && m.index <= i);
  const live = done.find((t) => t.entry_time <= (step?.time || '') && (step?.time || '') <= t.exit_time);
  const rails = live ? [
    { price: live.spot_entry, color: '#22d3ee', label: 'entry' },
  ] : [];

  return (
    <div className="space-y-4">
      <Section title="Replay one session" right={<span className="text-[10.5px] text-gray-500">
        same engine, stepped forward</span>}>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-gray-500">Date</div>
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} className={`${input} !w-40`} />
          </div>
          <button onClick={onLoad} disabled={busy || !date}
            className="btn-primary !py-1.5 !px-4 text-[12.5px] flex items-center gap-2 disabled:opacity-50">
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}Load session
          </button>
          {!!steps.length && (
            <>
              <div className="flex items-center gap-1">
                <button onClick={() => setI((n) => Math.max(0, n - 1))} className="btn-secondary !py-1.5 !px-2"><SkipBack className="w-3.5 h-3.5" /></button>
                <button onClick={() => setPlaying((p) => !p)} className="btn-secondary !py-1.5 !px-2">
                  {playing ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
                </button>
                <button onClick={() => setI((n) => Math.min(steps.length - 1, n + 1))} className="btn-secondary !py-1.5 !px-2"><SkipForward className="w-3.5 h-3.5" /></button>
              </div>
              <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}
                className="bg-surface-2 border border-surface-3 rounded px-2 py-1.5 text-[12px] text-gray-300">
                {SPEEDS.map(([l, v]) => <option key={v} value={v}>{l}</option>)}
              </select>
              <input type="range" min={0} max={steps.length - 1} value={i}
                onChange={(e) => { setPlaying(false); setI(Number(e.target.value)); }} className="flex-1 min-w-[160px] accent-brand-500" />
              <span className="text-[12px] mono text-gray-300">{step?.time} · bar {i + 1}/{steps.length}</span>
            </>
          )}
        </div>
      </Section>

      {!steps.length ? (
        <Note>Pick a date the store covers and load it. The replay shows every bar, the decision
          taken on it, and the trades the engine produced for that day.</Note>
      ) : (
        <>
          <div className="grid gap-4 lg:grid-cols-[2fr,1fr]">
            <Section title={`NIFTY · ${date}`}>
              <CandleChart candles={candles} overlays={overlays} markers={markers} rails={rails} height={300} />
            </Section>

            <Section title="This bar">
              <div className="grid grid-cols-2 gap-3">
                <Stat label="Time" value={step?.time} />
                <Stat label="Close" value={N(step?.close)} />
                <Stat label="VWAP" value={N(step?.vwap)} sub={step?.close > step?.vwap ? 'above' : 'below'} />
                <Stat label="RSI" value={N(step?.rsi, 1)} />
                <Stat label="Conditions met" value={`${step?.passed}/${step?.total}`}
                  tone={step?.fired ? 'text-emerald-400' : 'text-gray-100'} />
                <Stat label="Signal" value={step?.fired ? 'FIRED' : '—'}
                  tone={step?.fired ? 'text-emerald-400' : 'text-gray-500'} sub={step?.blocked_by || ''} />
              </div>
              <div className="mt-3 space-y-1">
                {(step?.reasons || []).map((r) => (
                  <div key={r.key} className="flex items-start gap-2">
                    {r.passed ? <Check className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-px" />
                      : <Minus className="w-3.5 h-3.5 text-gray-600 shrink-0 mt-px" />}
                    <span className={`text-[11.5px] ${r.passed ? 'text-gray-300' : 'text-gray-500'}`}>{r.label}</span>
                    {r.detail && <span className="text-[10.5px] text-gray-500 mono ml-auto">{r.detail}</span>}
                  </div>
                ))}
              </div>
              {live && (
                <div className="mt-3 rounded-lg border border-brand-500/40 bg-brand-500/5 p-2">
                  <div className="text-[11px] text-brand-300 font-semibold">In a position</div>
                  <div className="text-[11.5px] text-gray-300 mono">
                    {live.side} {live.contract} · entry {N(live.option_entry)} · exit planned {live.exit_time}
                  </div>
                </div>
              )}
            </Section>
          </div>

          <Section title={`Trades on this day · ${(data.trades || []).length}`}>
            {!(data.trades || []).length ? <Note>No trade was taken on this date.</Note> : (
              <div className="overflow-x-auto">
                <table className="w-full text-[12px] min-w-[720px]">
                  <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                    {['Signal', 'Side', 'Contract', 'Entry', 'Exit', 'Reason', 'Index move', 'Net P&L'].map((h) => (
                      <th key={h} className="text-left px-2 py-1 font-medium">{h}</th>))}
                  </tr></thead>
                  <tbody>
                    {(data.trades || []).map((t) => (
                      <tr key={t.trade_id} className="border-b border-surface-3/40">
                        <td className="px-2 py-1 mono text-gray-400">{t.time}</td>
                        <td className="px-2 py-1">{t.side}</td>
                        <td className="px-2 py-1 mono text-gray-300">{t.contract}</td>
                        <td className="px-2 py-1 mono text-gray-300">{N(t.option_entry)} @ {t.entry_time}</td>
                        <td className="px-2 py-1 mono text-gray-300">{N(t.option_exit)} @ {t.exit_time}</td>
                        <td className="px-2 py-1 text-gray-400">{t.exit_reason}</td>
                        <td className={`px-2 py-1 mono ${tone(t.spot_move_pts)}`}>{N(t.spot_move_pts, 1)} pts</td>
                        <td className={`px-2 py-1 mono font-semibold ${tone(t.pnl)}`}>{RS(t.pnl)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {!!(data.signals || []).length && (
              <div className="text-[11px] text-gray-500 mt-2">
                {data.signals.length} signal(s) on this day were blocked:{' '}
                {[...new Set(data.signals.map((s) => s.skip_reason))].slice(0, 4).join(' · ')}
              </div>
            )}
          </Section>
        </>
      )}
    </div>
  );
}
