import React from 'react';
import { Play, Loader2, Database, CheckCircle2, AlertTriangle } from 'lucide-react';
import { Section, Field, Note, input } from './ui';

/**
 * Step 1–7 of the workflow: what to test, over what data, with which execution and risk
 * assumptions. Everything here is a parameter — nothing about a strategy is hardcoded in the
 * engine, which is what makes a sweep or a robustness run meaningful.
 */

export default function Setup({ meta, cfg, setCfg, onRun, onCheck, busy, progress, quality }) {
  const set = (path, value) => {
    const next = JSON.parse(JSON.stringify(cfg));
    const parts = path.split('.');
    let node = next;
    parts.slice(0, -1).forEach((p) => { node[p] = node[p] || {}; node = node[p]; });
    node[parts[parts.length - 1]] = value;
    setCfg(next);
  };
  const preset = meta?.presets?.find((p) => p.key === cfg.strategy?.preset);
  const cov = meta?.coverage || {};
  const span = cov.options || cov.spot || {};

  return (
    <div className="space-y-4">
      <Section title="1 · Strategy"
        right={<span className="text-[10.5px] text-gray-500">engine {meta?.engine_version}</span>}>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Preset" hint="a starting point — every condition below stays editable">
            <select value={cfg.strategy?.preset || ''} onChange={(e) => set('strategy', { preset: e.target.value })}
              className={input}>
              {(meta?.presets || []).map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}
            </select>
          </Field>
          <Field label="Side" hint="which option the signal buys">
            <div className="flex gap-1 mt-1">
              {['CE', 'PE'].map((s) => (
                <button key={s} onClick={() => set('strategy.side', s)}
                  className={`px-3 py-1 rounded border text-[12px] font-semibold ${(cfg.strategy?.side || preset?.side) === s
                    ? 'border-brand-500 bg-brand-500 text-white' : 'border-surface-3 text-gray-400'}`}>{s}</button>
              ))}
            </div>
          </Field>
          <Field label="Timeframe" hint="decision candles">
            <select value={cfg.timeframe} onChange={(e) => set('timeframe', Number(e.target.value))} className={input}>
              {[1, 3, 5, 15, 30].map((m) => <option key={m} value={m}>{m} minute</option>)}
            </select>
          </Field>
          <Field label="Entry window" hint="no signal outside these times">
            <div className="flex items-center gap-1">
              <input type="number" value={cfg.strategy?.entry_from ?? preset?.entry_from ?? 570}
                onChange={(e) => set('strategy.entry_from', Number(e.target.value))} className={input} />
              <span className="text-gray-500 text-[11px]">to</span>
              <input type="number" value={cfg.strategy?.entry_until ?? preset?.entry_until ?? 900}
                onChange={(e) => set('strategy.entry_until', Number(e.target.value))} className={input} />
            </div>
          </Field>
        </div>
        {preset && (
          <div className="mt-2.5">
            <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Conditions — all must be true on the decision bar</div>
            <div className="flex flex-wrap gap-1.5">
              {(cfg.strategy?.conditions || preset.conditions || []).map((c) => {
                const info = (meta?.conditions || []).find((x) => x.key === c);
                return (
                  <span key={c} className="px-1.5 py-0.5 rounded border border-surface-3 bg-surface-2/60 text-[11px] text-gray-300">
                    {info?.label || c}
                  </span>
                );
              })}
            </div>
            {preset.note && <div className="text-[11.5px] text-gray-500 mt-1.5">{preset.note}</div>}
          </div>
        )}
      </Section>

      <Section title="2 · Data range" right={span.first && (
        <span className="text-[10.5px] text-gray-500 flex items-center gap-1">
          <Database className="w-3 h-3" />stored: {span.first} → {span.last} · {span.sessions} sessions
        </span>)}>
        <div className="grid gap-3 sm:grid-cols-4">
          <Field label="From"><input type="date" value={cfg.start || ''} min={span.first} max={span.last}
            onChange={(e) => set('start', e.target.value)} className={input} /></Field>
          <Field label="To"><input type="date" value={cfg.end || ''} min={span.first} max={span.last}
            onChange={(e) => set('end', e.target.value)} className={input} /></Field>
          <Field label="Underlying">
            <select value={cfg.underlying} onChange={(e) => set('underlying', e.target.value)} className={input}>
              <option value="NIFTY">NIFTY</option>
            </select>
          </Field>
          <Field label="Capital" hint="for the equity curve only">
            <input type="number" value={cfg.capital} onChange={(e) => set('capital', Number(e.target.value))} className={input} />
          </Field>
        </div>
        <button onClick={onCheck} disabled={busy}
          className="btn-secondary !py-1.5 !px-3 text-[12px] mt-2.5 disabled:opacity-50">Check this data first</button>
        {quality && (
          <div className="mt-2 space-y-1">
            <div className={`text-[12px] flex items-center gap-1.5 ${quality.quality?.ok ? 'text-emerald-400' : 'text-red-400'}`}>
              {quality.quality?.ok ? <CheckCircle2 className="w-4 h-4" /> : <AlertTriangle className="w-4 h-4" />}
              {quality.range?.start} → {quality.range?.end} ·
              {' '}{quality.quality?.stats?.sessions} sessions ·
              {' '}{quality.quality?.stats?.option_sessions} with option data ·
              {' '}{quality.quality?.stats?.strikes_per_session} strikes a session
            </div>
            {(quality.quality?.problems || []).map((p, i) => (
              <Note key={i} tone={p.level === 'info' ? 'info' : 'warn'}>{p.text}</Note>
            ))}
          </div>
        )}
      </Section>

      <div className="grid gap-4 lg:grid-cols-3">
        <Section title="3 · Execution">
          <div className="space-y-2.5">
            <Field label="Entry" hint="when the fill happens relative to the signal bar">
              <select value={cfg.execution.entry_model} onChange={(e) => set('execution.entry_model', e.target.value)} className={input}>
                <option value="signal_close">signal candle close</option>
                <option value="next_open">next candle open</option>
                <option value="next_close">next candle close</option>
                <option value="delay">delayed by N candles</option>
              </select>
            </Field>
            {cfg.execution.entry_model === 'delay' && (
              <Field label="Delay (candles)">
                <input type="number" min={1} value={cfg.execution.entry_delay_bars}
                  onChange={(e) => set('execution.entry_delay_bars', Number(e.target.value))} className={input} />
              </Field>
            )}
            <div className="grid grid-cols-2 gap-2">
              <Field label="Target %" hint="of option premium">
                <input type="number" value={cfg.execution.target_pct}
                  onChange={(e) => set('execution.target_pct', Number(e.target.value))} className={input} /></Field>
              <Field label="Stop %">
                <input type="number" value={cfg.execution.stop_pct}
                  onChange={(e) => set('execution.stop_pct', Number(e.target.value))} className={input} /></Field>
              <Field label="Trail after %" hint="0 = off">
                <input type="number" value={cfg.execution.trail_after_pct}
                  onChange={(e) => set('execution.trail_after_pct', Number(e.target.value))} className={input} /></Field>
              <Field label="Give back %">
                <input type="number" value={cfg.execution.trail_giveback_pct}
                  onChange={(e) => set('execution.trail_giveback_pct', Number(e.target.value))} className={input} /></Field>
              <Field label="Max hold (min)" hint="0 = until square-off">
                <input type="number" value={cfg.execution.max_hold_min}
                  onChange={(e) => set('execution.max_hold_min', Number(e.target.value))} className={input} /></Field>
              <Field label="Slippage (pts)" hint="per side, on the option">
                <input type="number" step="0.05" value={cfg.execution.slippage_pts}
                  onChange={(e) => set('execution.slippage_pts', Number(e.target.value))} className={input} /></Field>
            </div>
            <label className="flex items-center gap-2 text-[12px] text-gray-300">
              <input type="checkbox" checked={cfg.execution.costs_on}
                onChange={(e) => set('execution.costs_on', e.target.checked)} className="accent-brand-500" />
              apply brokerage, STT, exchange, GST, stamp duty
            </label>
          </div>
        </Section>

        <Section title="4 · Option selection">
          <div className="space-y-2.5">
            <Field label="Strike" hint="steps from ATM, in the direction of the trade">
              <select value={cfg.selection.moneyness} onChange={(e) => set('selection.moneyness', Number(e.target.value))} className={input}>
                <option value={-3}>3 ITM</option><option value={-2}>2 ITM</option><option value={-1}>1 ITM</option>
                <option value={0}>ATM</option>
                <option value={1}>1 OTM</option><option value={2}>2 OTM</option><option value={3}>3 OTM</option>
                <option value={5}>5 OTM</option>
              </select>
            </Field>
            <Field label="Expiry" hint="chosen from the expiries listed in that session's own data">
              <select value={cfg.selection.expiry_rule} onChange={(e) => set('selection.expiry_rule', e.target.value)} className={input}>
                <option value="nearest">nearest</option>
                <option value="next">next</option>
                <option value="monthly">monthly</option>
              </select>
            </Field>
            <Field label="Skip expiries closer than (days)" hint="1 avoids expiry-day contracts">
              <input type="number" min={0} value={cfg.selection.min_dte}
                onChange={(e) => set('selection.min_dte', Number(e.target.value))} className={input} />
            </Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Min option volume"><input type="number" value={cfg.risk.min_option_volume}
                onChange={(e) => set('risk.min_option_volume', Number(e.target.value))} className={input} /></Field>
              <Field label="Min option OI"><input type="number" value={cfg.risk.min_option_oi}
                onChange={(e) => set('risk.min_option_oi', Number(e.target.value))} className={input} /></Field>
            </div>
          </div>
        </Section>

        <Section title="5 · Risk">
          <div className="space-y-2.5">
            <div className="grid grid-cols-2 gap-2">
              <Field label="Max trades/day" hint="0 = unlimited">
                <input type="number" value={cfg.risk.max_trades_per_day}
                  onChange={(e) => set('risk.max_trades_per_day', Number(e.target.value))} className={input} /></Field>
              <Field label="Cooldown (min)">
                <input type="number" value={cfg.risk.cooldown_min}
                  onChange={(e) => set('risk.cooldown_min', Number(e.target.value))} className={input} /></Field>
              <Field label="Max daily loss ₹" hint="0 = off">
                <input type="number" value={cfg.risk.max_daily_loss}
                  onChange={(e) => set('risk.max_daily_loss', Number(e.target.value))} className={input} /></Field>
              <Field label="Max losses in a row" hint="0 = off">
                <input type="number" value={cfg.risk.max_consecutive_losses}
                  onChange={(e) => set('risk.max_consecutive_losses', Number(e.target.value))} className={input} /></Field>
              <Field label="Lots"><input type="number" min={1} value={cfg.risk.lots}
                onChange={(e) => set('risk.lots', Number(e.target.value))} className={input} /></Field>
              <Field label="Square off at" hint="minutes from midnight">
                <input type="number" value={cfg.execution.squareoff_min}
                  onChange={(e) => set('execution.squareoff_min', Number(e.target.value))} className={input} /></Field>
            </div>
            <label className="flex items-center gap-2 text-[12px] text-gray-300">
              <input type="checkbox" checked={cfg.risk.one_position_at_a_time}
                onChange={(e) => set('risk.one_position_at_a_time', e.target.checked)} className="accent-brand-500" />
              one position at a time
            </label>
          </div>
        </Section>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button onClick={onRun} disabled={busy}
          className="btn-primary !py-2 !px-5 text-[13px] flex items-center gap-2 disabled:opacity-50">
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
          {busy ? 'Running…' : 'Run backtest'}
        </button>
        {busy && progress && <span className="text-[12px] text-gray-400">{progress}</span>}
        <span className="text-[11px] text-gray-500 ml-auto">
          Every run is stored with its configuration and a hash, so it can be repeated exactly.
        </span>
      </div>
    </div>
  );
}
