import React from 'react';
import { Loader2, Play } from 'lucide-react';
import { Field, Note, Section, input } from './ui';

/**
 * Every knob both indicators expose, grouped the way the two scripts are written, with a line
 * under each saying what it does. Nothing here is hidden: what you set is what the engine runs.
 */
const MODE_LABEL = {
  both: 'Both indicators must agree',
  either: 'Either indicator is enough',
  rejection: 'Long-tail rejection only',
  shape: 'Body & filters only',
};

export default function Setup({ cfg, setCfg, meta, onRun, busy, progress, error }) {
  const p = cfg.params, e = cfg.execution;
  const setP = (k, v) => setCfg({ ...cfg, params: { ...p, [k]: v } });
  const setE = (k, v) => setCfg({ ...cfg, execution: { ...e, [k]: v } });
  const num = (v) => (v === '' ? '' : Number(v));
  const short = String(e.action || 'BUY').toUpperCase() === 'SELL';
  const cov = meta?.coverage?.options || {};

  return (
    <div className="space-y-4">
      <Section title="Range and speed">
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 items-end">
          <Field label="From"><input type="date" value={cfg.start} onChange={(ev) => setCfg({ ...cfg, start: ev.target.value })} className={input} /></Field>
          <Field label="To"><input type="date" value={cfg.end} onChange={(ev) => setCfg({ ...cfg, end: ev.target.value })} className={input} /></Field>
          <Field label="Candle" hint="The timeframe the pattern is read on">
            <select value={cfg.timeframe} onChange={(ev) => setCfg({ ...cfg, timeframe: Number(ev.target.value) })} className={input}>
              {[1, 3, 5, 15, 30, 60].map((t) => <option key={t} value={t}>{t} minute</option>)}
            </select>
          </Field>
          <Field label="Which side" hint="Hammers are bought as calls, inverted hammers as puts">
            <select value={p.sides} onChange={(ev) => setP('sides', ev.target.value)} className={input}>
              <option value="both">Both</option>
              <option value="bullish">Hammers only</option>
              <option value="bearish">Inverted hammers only</option>
            </select>
          </Field>
          <button disabled={busy || !cfg.start || !cfg.end} onClick={onRun}
            className="btn-primary !py-1.5 text-[12.5px] flex items-center justify-center gap-1.5">
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            {busy ? 'Running…' : 'Run backtest'}
          </button>
        </div>
        <div className="text-[11px] text-gray-500 mt-2">
          Stored data: {cov.first || '—'} → {cov.last || '—'} ({cov.sessions || 0} sessions). Signals are read on
          the index candle and traded in the option chain, one lot = {meta?.lot_size ?? 65}.
        </div>
        {busy && <div className="text-[11.5px] text-gray-500 mt-1">{progress}</div>}
        {error && <div className="text-[12px] text-red-400 mt-1">{error}</div>}
      </Section>

      <Section title="How the two indicators combine">
        <div className="grid sm:grid-cols-4 gap-2">
          {Object.entries(MODE_LABEL).map(([k, label]) => (
            <button key={k} onClick={() => setP('mode', k)}
              className={`text-left rounded-lg border px-2.5 py-2 ${p.mode === k
                ? 'border-brand-500/60 bg-brand-500/15' : 'border-surface-3 bg-surface-2/40 hover:bg-surface-2'}`}>
              <div className={`text-[12.5px] font-semibold ${p.mode === k ? 'text-brand-300' : 'text-gray-200'}`}>{label}</div>
              <div className="text-[10.5px] text-gray-500">{meta?.modes?.[k]}</div>
            </button>
          ))}
        </div>
      </Section>

      <div className="grid lg:grid-cols-2 gap-4">
        <Section title="Long-tail rejection · script one">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Minimum tail (%)" hint="How much of the candle's range the tail must be">
              <input type="number" min={1} max={100} value={p.tail_pct} onChange={(ev) => setP('tail_pct', num(ev.target.value))} className={input} />
            </Field>
            <Field label="Consecutive bars" hint="Bars in a row that must show the tail">
              <input type="number" min={1} max={5} value={p.consecutive} onChange={(ev) => setP('consecutive', num(ev.target.value))} className={input} />
            </Field>
            <Field label="Bars for the old extreme" hint="The low must undercut the lowest low of this many previous bars">
              <input type="number" min={1} max={50} value={p.lookback} onChange={(ev) => setP('lookback', num(ev.target.value))} className={input} />
            </Field>
            <Field label="Beyond that extreme (%)" hint="How much of the candle must sit past the old low or high">
              <input type="number" min={0} max={100} value={p.beyond_pct} onChange={(ev) => setP('beyond_pct', num(ev.target.value))} className={input} />
            </Field>
          </div>
        </Section>

        <Section title="Body & filters · script two">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Fib level" hint="0.40 keeps the body in the far 60% of the range">
              <input type="number" step="0.05" min={0.05} max={0.95} value={p.fib_level} onChange={(ev) => setP('fib_level', num(ev.target.value))} className={input} />
            </Field>
            <Field label="ATR filter" hint="Range must be at least ATR × this — drops doji-sized bars">
              <input type="number" step="0.05" min={0} max={3} value={p.atr_filter} onChange={(ev) => setP('atr_filter', num(ev.target.value))} className={input} />
            </Field>
            <Field label="Swing look-back" hint="The bar (or the one before) must be the extreme of this many bars">
              <input type="number" min={2} max={50} value={p.swing_lookback} onChange={(ev) => setP('swing_lookback', num(ev.target.value))} className={input} />
            </Field>
            <Field label="EMA length" hint="Used only when the EMA filter is on">
              <input type="number" min={5} max={200} value={p.ema_length} onChange={(ev) => setP('ema_length', num(ev.target.value))} className={input} />
            </Field>
            <label className="flex items-center gap-2 text-[12px] text-gray-300 cursor-pointer">
              <input type="checkbox" checked={!!p.colour_filter} onChange={(ev) => setP('colour_filter', ev.target.checked)} />
              Candle must close in the trade's direction
            </label>
            <label className="flex items-center gap-2 text-[12px] text-gray-300 cursor-pointer">
              <input type="checkbox" checked={!!p.use_ema_filter} onChange={(ev) => setP('use_ema_filter', ev.target.checked)} />
              Only trade with the EMA
            </label>
          </div>
        </Section>
      </div>

      <Section title="The trade">
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-4 gap-3 mb-3">
          <Field label="Side" hint="Buy the signal, or sell the opposite option">
            <select value={e.action || 'BUY'} onChange={(ev) => setE('action', ev.target.value)} className={input}>
              <option value="BUY">Buy the option</option>
              <option value="SELL">Sell the opposite option</option>
            </select>
          </Field>
          <Field label="Hedge (strikes)" hint={short ? 'Width of the protective wing · 0 = naked' : 'Selling only'}>
            <input type="number" min={0} max={10} disabled={!short}
              value={e.hedge_offset ?? 0}
              onChange={(ev) => setE('hedge_offset', num(ev.target.value))}
              className={`${input} ${short ? '' : 'opacity-40'}`} />
          </Field>
          <Field label="Min days to expiry" hint="1 skips expiry day">
            <input type="number" min={0} max={10} value={e.min_dte ?? 0}
              onChange={(ev) => setE('min_dte', num(ev.target.value))} className={input} />
          </Field>
          <Field label="Max days to expiry" hint="0 = no limit">
            <input type="number" min={0} max={30} value={e.max_dte ?? 0}
              onChange={(ev) => setE('max_dte', num(ev.target.value))} className={input} />
          </Field>
        </div>
        {short && (
          <div className="mb-3">
            <Note>
              Selling flips every rule with the position. A bullish hammer sells <b>puts</b> and a bearish
              star sells <b>calls</b> — the bet is that the move fails to arrive rather than that it does.
              Target is the credit decaying by that percentage; stop is the credit expanding by it. With a
              hedge width the loss stops at the strikes instead of running with the index, and both legs
              are measured together, because managing the short leg alone would unwind the protection at
              the worst moment. A hedge of 0 is naked: the loss has no defined limit.
            </Note>
          </div>
        )}
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
          <Field label="Target (%)" hint={short ? 'Credit decays this far' : "Of the option's premium"}><input type="number" value={e.target_pct} onChange={(ev) => setE('target_pct', num(ev.target.value))} className={input} /></Field>
          <Field label="Stop (%)" hint={short ? 'Credit expands this far · 0 = none' : "Of the option's premium"}><input type="number" value={e.stop_pct} onChange={(ev) => setE('stop_pct', num(ev.target.value))} className={input} /></Field>
          <Field label="Max hold (min)" hint="0 = hold to square-off"><input type="number" value={e.max_hold_min} onChange={(ev) => setE('max_hold_min', num(ev.target.value))} className={input} /></Field>
          <Field label="Trades a day"><input type="number" min={1} max={10} value={e.max_trades_per_day} onChange={(ev) => setE('max_trades_per_day', num(ev.target.value))} className={input} /></Field>
          <Field label="Strike" hint={short ? 'Steps out of the money, on the side sold' : '0 = at the money, 1 = one strike out'}>
            <select value={e.moneyness} onChange={(ev) => setE('moneyness', Number(ev.target.value))} className={input}>
              <option value={0}>At the money</option>
              <option value={1}>1 strike out</option>
              <option value={2}>2 strikes out</option>
              <option value={3}>3 strikes out</option>
              <option value={-1}>1 strike in</option>
            </select>
          </Field>
          <Field label="Lots"><input type="number" min={1} max={50} value={e.lots} onChange={(ev) => setE('lots', num(ev.target.value))} className={input} /></Field>
          <Field label="Slippage (pts)" hint="Charged on every fill, both ways"><input type="number" step="0.25" value={e.slippage_pts} onChange={(ev) => setE('slippage_pts', num(ev.target.value))} className={input} /></Field>
        </div>
        <div className="mt-2">
          <Note>
            The decision is taken on the candle's last minute and filled at the next minute's real option open —
            never inside the candle. Exits are decided on one-minute closes and filled the same way, with
            Zerodha's charges on every trade.
            {short && ' Slippage is charged on four legs here, not two, and a credit spread is '
              + 'unusually sensitive to it — worth raising to see what the edge survives.'}
          </Note>
        </div>
      </Section>
    </div>
  );
}
