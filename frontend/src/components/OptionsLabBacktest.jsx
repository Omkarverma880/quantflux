import React, { useEffect, useState } from 'react';
import { api } from '../api';
import { FlaskConical, RefreshCw, AlertTriangle, XCircle } from 'lucide-react';

/**
 * Options Lab — Backtest tab. Runs any structure on REAL traded option prices from
 * the Market Store. Results are always split at the research holdout date so a
 * configuration that only looks good on the data it was chosen from is visible.
 */

const INR = (v) => (v === null || v === undefined ? '—' : `₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`);
const tone = (v) => (Number(v) > 0 ? 'text-green-400' : Number(v) < 0 ? 'text-red-400' : 'text-gray-300');
const HHMM = (m) => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
const toMin = (s) => { const [h, m] = String(s || '').split(':').map(Number); return Number.isFinite(h) ? h * 60 + (m || 0) : 585; };
const inp = 'w-full bg-surface-3 border border-surface-3 rounded-lg px-2.5 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none';

function Field({ label, hint, children }) {
  return (
    <label className="block">
      <span className="block text-[11.5px] font-medium text-gray-400 mb-1">{label}</span>
      {children}
      {hint && <span className="block text-[10.5px] text-gray-600 mt-0.5">{hint}</span>}
    </label>
  );
}

function Stat({ label, value, sub, t = '' }) {
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-lg px-3 py-2.5">
      <div className="text-[10.5px] uppercase tracking-wider text-gray-500">{label}</div>
      <div className={`mono text-lg font-semibold ${t || 'text-gray-100'}`}>{value}</div>
      {sub && <div className="text-[11px] text-gray-500 mt-0.5">{sub}</div>}
    </div>
  );
}

function SplitRow({ label, s }) {
  if (!s || !s.trades) return (
    <tr className="border-t border-surface-3/50">
      <td className="px-2 py-1.5 text-gray-400">{label}</td>
      <td colSpan={7} className="px-2 py-1.5 text-gray-600">
        {s?.outside_range ? 'not in your selected dates — widen Start/End to include it' : 'no trades in this period'}
      </td>
    </tr>
  );
  return (
    <tr className="border-t border-surface-3/50">
      <td className="px-2 py-1.5 text-gray-200">{label}</td>
      <td className="px-2 py-1.5 text-right mono">{s.trades}</td>
      <td className="px-2 py-1.5 text-right mono">{s.win_rate}%</td>
      <td className={`px-2 py-1.5 text-right mono ${tone(s.pnl_per_trade)}`}>{INR(s.pnl_per_trade)}</td>
      <td className="px-2 py-1.5 text-right mono">{s.t_stat ?? '—'}</td>
      <td className={`px-2 py-1.5 text-right mono ${tone(s.pnl_per_year)}`}>{INR(s.pnl_per_year)}</td>
      <td className="px-2 py-1.5 text-right mono text-red-400">{INR(s.max_drawdown)}</td>
      <td className="px-2 py-1.5 text-right mono text-amber-400">{s.stale_leg_trades}</td>
    </tr>
  );
}

export default function OptionsLabBacktest() {
  const [meta, setMeta] = useState(null);
  const [rule, setRule] = useState(null);
  const [preset, setPreset] = useState('');
  const [range, setRange] = useState({ start_date: '', end_date: '', slippage_pts: 0.5, lot_size: 65 });
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  useEffect(() => {
    api.olMeta().then((m) => {
      if (m?.status === 'error') { setErr(m.message); return; }
      setMeta(m);
      const start = m.default_preset && m.presets?.[m.default_preset];
      setPreset(start ? m.default_preset : '');
      setRule(start ? { ...m.defaults, ...start.rule } : m.defaults);
    }).catch((e) => setErr(String(e)));
  }, []);

  const set = (k, v) => setRule((r) => ({ ...r, [k]: v }));
  const applyPreset = (key) => {
    setPreset(key);
    if (key && meta?.presets?.[key]) setRule({ ...meta.defaults, ...meta.presets[key].rule });
  };

  const run = async () => {
    setBusy(true); setErr(''); setRes(null);
    try {
      const r = await api.olBacktest({ rule, ...range });
      if (r?.status === 'error') setErr(r.message); else setRes(r);
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };

  if (!rule) return <div className="card text-sm text-gray-500">{err || 'Loading…'}</div>;
  const isLong = ['long_call', 'long_put', 'long_straddle'].includes(rule.structure);
  const hasWing = ['iron_fly', 'short_strangle'].includes(rule.structure);
  const s = res?.summary;
  const pr = preset && meta?.presets?.[preset];

  return (
    <div className="space-y-4">
      <div className="card space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-gray-100">Backtest on real option prices</h2>
          <span className="text-[11px] text-gray-500">Research holdout starts {meta?.holdout_start}</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Field label="Preset">
            <select className={inp} value={preset} onChange={(e) => applyPreset(e.target.value)}>
              <option value="">— custom —</option>
              {Object.entries(meta?.presets || {}).map(([k, p]) => <option key={k} value={k}>{p.label}</option>)}
            </select>
          </Field>
          <Field label="Structure">
            <select className={inp} value={rule.structure} onChange={(e) => set('structure', e.target.value)}>
              {(meta?.structures || []).map((x) => <option key={x} value={x}>{x.replace(/_/g, ' ')}</option>)}
            </select>
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Entry (decision)" hint="fill at next minute">
              <input className={inp} value={HHMM(rule.entry_minute)} onChange={(e) => set('entry_minute', toMin(e.target.value))} />
            </Field>
            <Field label="Square-off">
              <input className={inp} value={HHMM(rule.squareoff)} onChange={(e) => set('squareoff', toMin(e.target.value))} />
            </Field>
          </div>
        </div>

        {pr?.note && (
          <p className="text-[12px] text-amber-200 bg-amber-500/10 border border-amber-500/25 rounded-lg px-3 py-2 flex gap-2">
            <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />{pr.note}
          </p>
        )}

        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
          <Field label="DTE from"><input type="number" className={inp} value={rule.dte_min} onChange={(e) => set('dte_min', Number(e.target.value))} /></Field>
          <Field label="DTE to"><input type="number" className={inp} value={rule.dte_max} onChange={(e) => set('dte_max', Number(e.target.value))} /></Field>
          {hasWing && <Field label="Wing (strikes)" hint="× 50 pts"><input type="number" className={inp} value={rule.wing_steps} onChange={(e) => set('wing_steps', Number(e.target.value))} /></Field>}
          {isLong && <Field label="Moneyness" hint="-1 ITM · 0 ATM · +1 OTM"><input type="number" className={inp} value={rule.moneyness} onChange={(e) => set('moneyness', Number(e.target.value))} /></Field>}
          {isLong
            ? <Field label="Stop %" hint="of debit"><input type="number" className={inp} value={rule.stop_pct} onChange={(e) => set('stop_pct', Number(e.target.value))} /></Field>
            : <Field label="Stop × credit" hint="0 = none"><input type="number" step="0.1" className={inp} value={rule.stop_x_credit} onChange={(e) => set('stop_x_credit', Number(e.target.value))} /></Field>}
          <Field label="Target %" hint="0 = none"><input type="number" className={inp} value={rule.target_pct} onChange={(e) => set('target_pct', Number(e.target.value))} /></Field>
          <Field label="Capital ₹"><input type="number" step="10000" className={inp} value={rule.capital} onChange={(e) => set('capital', Number(e.target.value))} /></Field>
          {!isLong && <Field label="Margin / lot ₹" hint="check broker SPAN"><input type="number" step="5000" className={inp} value={rule.margin_per_lot} onChange={(e) => set('margin_per_lot', Number(e.target.value))} /></Field>}
          <Field label="Max lots"><input type="number" className={inp} value={rule.max_lots} onChange={(e) => set('max_lots', Number(e.target.value))} /></Field>
          <Field label="Slippage pts" hint="per leg, per side"><input type="number" step="0.25" className={inp} value={range.slippage_pts} onChange={(e) => setRange({ ...range, slippage_pts: Number(e.target.value) })} /></Field>
          <Field label="Start"><input type="date" className={inp} value={range.start_date} onChange={(e) => setRange({ ...range, start_date: e.target.value })} /></Field>
          <Field label="End"><input type="date" className={inp} value={range.end_date} onChange={(e) => setRange({ ...range, end_date: e.target.value })} /></Field>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-[12.5px] text-gray-300 cursor-pointer">
            <input type="checkbox" checked={!!rule.stale_adverse} onChange={(e) => set('stale_adverse', e.target.checked)} />
            Price unobserved legs adversely <span className="text-gray-500">(recommended — see How it works)</span>
          </label>
          <div className="flex-1" />
          <button onClick={run} disabled={busy}
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-semibold bg-brand-600 text-white hover:bg-brand-500 disabled:opacity-50">
            {busy ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <FlaskConical className="w-3.5 h-3.5" />}
            {busy ? 'Running…' : 'Run backtest'}
          </button>
        </div>
      </div>

      {err && <div className="rounded-lg bg-red-500/10 border border-red-500/25 px-3 py-2 text-sm text-red-300 flex gap-2"><XCircle className="w-4 h-4 mt-0.5 shrink-0" />{err}</div>}

      {res && s && (
        <>
          {s.note && <div className="rounded-lg bg-amber-500/10 border border-amber-500/25 px-3 py-2 text-sm text-amber-200">{s.note}</div>}

          {s.trades > 0 && (
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-2">
              <Stat label="Trades" value={s.trades} sub={`${res.sessions} sessions in range`} />
              <Stat label="Win rate" value={`${s.win_rate}%`} />
              <Stat label="P&L / trade" value={INR(s.pnl_per_trade)} t={tone(s.pnl_per_trade)} />
              <Stat label="t-stat" value={s.t_stat ?? '—'} sub="> 2 before believing it" />
              <Stat label="P&L / year" value={INR(s.pnl_per_year)} t={tone(s.pnl_per_year)}
                sub={s.pnl_per_year == null ? 'shown for 3+ months of dates' : 'projected from this range'} />
              <Stat label="Max drawdown" value={INR(s.max_drawdown)} t="text-red-400" />
              <Stat label="Total P&L" value={INR(s.pnl_total)} t={tone(s.pnl_total)} />
              <Stat label="Profit factor" value={s.profit_factor ?? '—'} />
              <Stat label="Worst trade" value={INR(s.worst_trade)} t="text-red-400" />
              <Stat label="Stop exits" value={s.stop_exits} />
              <Stat label="Capital / trade" value={INR(s.avg_capital_used)} />
              <Stat label="Unobserved-leg trades" value={s.stale_leg_trades} t={s.stale_leg_trades ? 'text-amber-400' : ''} sub="exit price not traded" />
            </div>
          )}

          {s.trades > 0 && (
            <div className="card">
              <h3 className="text-sm font-semibold text-gray-100 mb-1">Before vs after the research holdout</h3>
              <p className="text-[11.5px] text-gray-500 mb-2">A real effect should keep its sign and roughly its size on the right-hand period.</p>
              <div className="overflow-x-auto">
                <table className="w-full text-[12.5px]">
                  <thead><tr className="text-gray-500">
                    {['Period', 'Trades', 'Win', 'P&L/trade', 't', 'P&L/year', 'Max DD', 'Unobserved legs'].map((h, i) => (
                      <th key={h} className={`px-2 py-1 font-semibold ${i ? 'text-right' : 'text-left'}`}>{h}</th>))}
                  </tr></thead>
                  <tbody>
                    <SplitRow label={`Before ${meta?.holdout_start}`} s={res.split?.before_holdout} />
                    <SplitRow label={`From ${meta?.holdout_start}`} s={res.split?.holdout} />
                    {(res.yearly || []).map((y) => <SplitRow key={y.period} label={y.period} s={y} />)}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {res.funnel?.skips?.length > 0 && (
            <div className="card">
              <h3 className="text-sm font-semibold text-gray-100 mb-2">Why sessions were not traded</h3>
              <div className="space-y-1">
                {res.funnel.skips.map((k) => (
                  <div key={k.reason} className="flex items-center gap-2 text-[12px]">
                    <div className="w-72 text-gray-400">{k.label}</div>
                    <div className="mono text-gray-200">{k.sessions}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {res.trades?.length > 0 && (
            <div className="card">
              <h3 className="text-sm font-semibold text-gray-100">Trade log ({res.trades.length})</h3>
              <p className="text-[11.5px] text-gray-500 mb-2">
                Prices are per unit of the option (1 qty), after slippage. P&L = (exit − entry) × qty − costs.
                For multi-leg trades the price is the net of all legs; each leg is listed underneath.
              </p>
              <div className="overflow-x-auto max-h-[620px]">
                <table className="w-full text-[12px]">
                  <thead className="sticky top-0 bg-surface-2">
                    <tr className="text-gray-500">
                      {['Date', 'DTE', 'Entry', 'Exit', 'Size', 'Costs', 'P&L', 'Result'].map((h, i) => (
                        <th key={h} className={`px-2 py-1.5 font-semibold ${i === 0 ? 'text-left' : 'text-right'}`}>{h}</th>))}
                    </tr>
                  </thead>
                  <tbody>
                    {res.trades.slice().reverse().map((t, i) => {
                      const credit = t.entry_action === 'Received';
                      const result = { SL: 'Stop-loss', TARGET: 'Target', EOD: 'Square-off' }[t.exit_reason] || t.exit_reason;
                      return (
                        <React.Fragment key={i}>
                          <tr className="border-t border-surface-3/40">
                            <td className="px-2 pt-1.5 text-gray-200 whitespace-nowrap">{t.date}</td>
                            <td className="px-2 pt-1.5 text-right mono">{t.dte}</td>
                            <td className="px-2 pt-1.5 text-right whitespace-nowrap">
                              <span className="mono text-gray-500">{t.entry_time}</span>{' '}
                              <span className="text-gray-400">{credit ? 'sold for' : 'bought at'}</span>{' '}
                              <span className="mono text-gray-100">₹{Number(t.premium_in).toFixed(2)}</span>
                            </td>
                            <td className="px-2 pt-1.5 text-right whitespace-nowrap">
                              <span className="mono text-gray-500">{t.exit_time}</span>{' '}
                              <span className="text-gray-400">{credit ? 'bought back at' : 'sold at'}</span>{' '}
                              <span className="mono text-gray-100">₹{Number(t.premium_out).toFixed(2)}</span>
                            </td>
                            <td className="px-2 pt-1.5 text-right mono whitespace-nowrap">{t.lots} lots · {t.qty} qty</td>
                            <td className="px-2 pt-1.5 text-right mono text-gray-500">{INR(t.costs_rs)}</td>
                            <td className={`px-2 pt-1.5 text-right mono font-semibold ${tone(t.pnl_rs)}`}>{INR(t.pnl_rs)}</td>
                            <td className={`px-2 pt-1.5 text-right ${t.exit_reason === 'TARGET' ? 'text-green-400' : t.exit_reason === 'SL' ? 'text-red-400' : 'text-gray-400'}`}>{result}</td>
                          </tr>
                          <tr>
                            <td colSpan={8} className="px-2 pb-1.5 text-[11.5px] text-gray-400">
                              {t.plain || t.legs}
                              {t.stale_legs_at_exit > 0 && (
                                <span className="text-amber-400"> · {t.stale_legs_at_exit} leg(s) had no trade at the exit minute and were priced adversely</span>
                              )}
                            </td>
                          </tr>
                        </React.Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
