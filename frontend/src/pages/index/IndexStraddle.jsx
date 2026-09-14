import React, { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../../api';
import {
  FlaskConical, Wallet, Info, Play, Square, RefreshCw, Upload, Download,
  TrendingUp, TrendingDown, AlertTriangle, CheckCircle2, XCircle, Eye, Target, Save,
} from 'lucide-react';
import IndexStraddleInfo from './IndexStraddleInfo';

const INR = (v, d = 0) => `₹${Number(v || 0).toLocaleString('en-IN', { maximumFractionDigits: d })}`;
const PCT = (v, d = 2) => `${Number(v || 0) >= 0 ? '' : ''}${Number(v || 0).toFixed(d)}%`;
const tone = (v) => (Number(v) > 0 ? 'text-green-400' : Number(v) < 0 ? 'text-red-400' : 'text-gray-300');

function Stat({ label, value, sub, tone: t = '' }) {
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-lg px-3 py-2.5">
      <div className="text-[10.5px] uppercase tracking-wider text-gray-500">{label}</div>
      <div className={`mono text-lg font-semibold ${t || 'text-gray-100'}`}>{value}</div>
      {sub && <div className="text-[11px] text-gray-500 mt-0.5">{sub}</div>}
    </div>
  );
}

function Field({ label, hint, children }) {
  return (
    <label className="block">
      <span className="block text-[11.5px] font-medium text-gray-400 mb-1">{label}</span>
      {children}
      {hint && <span className="block text-[10.5px] text-gray-600 mt-0.5">{hint}</span>}
    </label>
  );
}

const inp = 'w-full bg-surface-3 border border-surface-3 rounded-lg px-2.5 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none';

function EquityChart({ curve }) {
  if (!curve?.length) return null;
  const W = 760, H = 200, P = 28;
  const eq = curve.map((c) => Number(c.equity));
  const lo = Math.min(...eq), hi = Math.max(...eq);
  const span = hi - lo || 1;
  const pts = eq.map((v, i) => {
    const x = P + (i / Math.max(eq.length - 1, 1)) * (W - P * 2);
    const y = H - P - ((v - lo) / span) * (H - P * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const up = eq[eq.length - 1] >= eq[0];
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-3">
      <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-1">Equity curve</div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto">
        <line x1={P} y1={H - P} x2={W - P} y2={H - P} stroke="currentColor" className="text-surface-3" />
        <polyline points={pts} fill="none" strokeWidth="2"
          className={up ? 'text-green-400' : 'text-red-400'} stroke="currentColor" />
        <text x={P} y={16} className="fill-gray-500" fontSize="11">{INR(hi)}</text>
        <text x={P} y={H - 8} className="fill-gray-500" fontSize="11">{INR(lo)}</text>
      </svg>
    </div>
  );
}

function ParityBlock({ parity }) {
  if (!parity) return null;
  const v = parity.verdict || (parity.matches ? 'match' : 'drift');
  const skin = v === 'match' ? 'bg-green-500/5 border-green-500/25'
    : v === 'different_window' ? 'bg-blue-500/5 border-blue-500/25'
    : 'bg-amber-500/5 border-amber-500/30';
  const Icon = v === 'match' ? CheckCircle2 : v === 'different_window' ? Info : AlertTriangle;
  const iconTone = v === 'match' ? 'text-green-400'
    : v === 'different_window' ? 'text-blue-400' : 'text-amber-400';
  const title = v === 'match' ? 'Parity OK — this run reproduces the research'
    : v === 'different_window' ? 'Different data window — totals are not comparable'
    : 'Parity drift — investigate before trusting these numbers';
  return (
    <div className={`rounded-xl p-4 border ${skin}`}>
      <div className="flex items-center gap-2 mb-2">
        <Icon className={`w-4 h-4 ${iconTone}`} />
        <h3 className="text-sm font-semibold text-gray-100">{title}</h3>
      </div>
      {parity.scope && <p className="text-[12.5px] text-gray-400 mb-2">{parity.scope}</p>}
      <p className="text-[12px] text-gray-500 mb-2">{parity.reference?.config}</p>
      <div className="overflow-x-auto">
        <table className="w-full text-[12.5px]">
          <thead>
            <tr className="text-gray-500">
              <th className="text-left px-2 py-1 font-semibold">Metric</th>
              <th className="text-right px-2 py-1 font-semibold">This run</th>
              <th className="text-right px-2 py-1 font-semibold">Research</th>
              <th className="text-right px-2 py-1 font-semibold">Delta</th>
            </tr>
          </thead>
          <tbody>
            {(parity.items || []).map((r) => {
              const muted = v === 'different_window' && r.span_sensitive;
              return (
                <tr key={r.metric} className="border-t border-surface-3/50">
                  <td className="px-2 py-1 text-gray-300">
                    {r.metric}
                    {muted && <span className="ml-1.5 text-[10px] text-gray-600">depends on window length</span>}
                  </td>
                  <td className="px-2 py-1 text-right mono text-gray-100">{Number(r.engine).toLocaleString('en-IN')}</td>
                  <td className="px-2 py-1 text-right mono text-gray-400">{Number(r.research).toLocaleString('en-IN')}</td>
                  <td className={`px-2 py-1 text-right mono ${
                    muted ? 'text-gray-600'
                    : Math.abs(r.delta_pct ?? 0) <= 2 ? 'text-gray-400' : 'text-amber-400'}`}>
                    {r.delta_pct === null ? '—' : `${r.delta_pct > 0 ? '+' : ''}${r.delta_pct}%`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="text-[11.5px] text-gray-500 mt-2">{parity.reference?.note}</p>
    </div>
  );
}

function FunnelBlock({ funnel }) {
  if (!funnel) return null;
  const f = funnel;
  return (
    <div className="card">
      <h3 className="text-sm font-semibold text-gray-100 mb-1">Session funnel — every trading day accounted for</h3>
      <p className="text-[11.5px] text-gray-500 mb-3">{f.first_session} → {f.last_session}</p>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
        <Stat label="Trading days in data" value={f.sessions_in_data} />
        <Stat label="Days in your window" value={f.sessions_in_window} sub={f.warmup_sessions ? `+${f.warmup_sessions} warm-up loaded before it` : ''} />
        <Stat label="Days we traded" value={f.sessions_traded} tone="text-green-400" sub={`${f.trade_rate_pct}% of sessions`} />
        <Stat label="Days skipped" value={f.sessions_skipped} tone="text-gray-400" />
      </div>
      {f.reasons?.length > 0 && (
        <div className="space-y-1">
          <div className="text-[11px] uppercase tracking-wider text-gray-500">Why the rest were skipped</div>
          {f.reasons.map((r) => {
            const pct = f.sessions_in_window ? (r.sessions / f.sessions_in_window) * 100 : 0;
            return (
              <div key={r.reason} className="flex items-center gap-2">
                <div className="w-56 shrink-0 text-[12px] text-gray-400">{r.label}</div>
                <div className="flex-1 h-2 rounded bg-surface-3 overflow-hidden">
                  <div className="h-full bg-gray-600" style={{ width: `${Math.min(100, pct)}%` }} />
                </div>
                <div className="w-20 text-right mono text-[12px] text-gray-300">{r.sessions}</div>
                <div className="w-14 text-right mono text-[11px] text-gray-600">{pct.toFixed(0)}%</div>
              </div>
            );
          })}
        </div>
      )}
      <p className="text-[11.5px] text-gray-500 mt-3">
        Most skips are simply days outside the expiry window — that is the strategy working as
        designed, not data missing. With a 0–1 DTE filter roughly two sessions a week qualify.
      </p>
    </div>
  );
}

export default function IndexStraddle() {
  const [meta, setMeta] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [tab, setTab] = useState('backtest');
  const [res, setRes] = useState(null);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState(''); const [msg, setMsg] = useState('');
  const [status, setStatus] = useState(null);
  const [positions, setPositions] = useState([]);
  const [preview, setPreview] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [openRow, setOpenRow] = useState(null);
  const [saved, setSaved] = useState(null);   // last persisted config, for the dirty flag
  const [saving, setSaving] = useState(false);
  const fileRef = useRef(null);

  const set = (k, v) => setCfg((c) => ({ ...c, [k]: v }));

  useEffect(() => {
    api.isMeta().then((m) => {
      if (m?.status === 'error') { setErr(m.message); return; }
      setMeta(m); setCfg(m.config); setSaved(JSON.stringify(m.config));
    }).catch((e) => setErr(String(e)));
    api.isLast().then((r) => { if (r?.status === 'ok') setRes(r); }).catch(() => {});
  }, []);

  const refreshLive = useCallback(async () => {
    try {
      const s = await api.isStatus();
      if (s?.status !== 'error') setStatus(s);
      const p = await api.isPositions();
      setPositions(p?.positions || []);
    } catch { /* transient */ }
  }, []);

  useEffect(() => {
    if (tab !== 'live') return undefined;
    refreshLive();
    const id = setInterval(refreshLive, 15000);
    return () => clearInterval(id);
  }, [tab, refreshLive]);

  const runBacktest = async () => {
    setRunning(true); setErr(''); setMsg('');
    try {
      const r = await api.isBacktest(cfg, true);
      if (r?.status === 'error') setErr(r.message);
      else { setRes(r); setMsg(`Done — ${r.trade_count} trades`); }
    } catch (e) { setErr(String(e)); } finally { setRunning(false); }
  };

  const doUpload = async (f) => {
    if (!f) return;
    setUploading(true); setErr('');
    try {
      const r = await api.isUpload(f);
      if (r?.status === 'error') setErr(r.message);
      else { set('csv_path', r.path); setMsg(`Uploaded ${r.name}`); const m = await api.isMeta(); setMeta(m); }
    } catch (e) { setErr(String(e)); } finally { setUploading(false); }
  };

  const dirty = saved !== null && cfg !== null && JSON.stringify(cfg) !== saved;

  const saveCfg = async () => {
    setSaving(true); setErr(''); setMsg('');
    try {
      const r = await api.isConfigSave(cfg);
      if (r?.status === 'error') setErr(r.message);
      else { setSaved(JSON.stringify(r.config)); setCfg(r.config); setMsg('Configuration saved'); }
    } catch (e) { setErr(String(e)); } finally { setSaving(false); }
  };

  const doPreview = async () => {
    try {
      const p = await api.isPreview(cfg);
      setPreview(p);
      if (p?.message) setMsg(p.message);
    } catch (e) { setErr(String(e)); }
  };

  const startLive = async () => {
    if (!cfg?.paper_trade) {
      const go = window.confirm(
        'PAPER TRADE IS OFF — this will place REAL orders on Zerodha.\n\n' +
        'Short volatility loses fast on a trend day and a stop is not a guarantee of a fill.\n\nContinue?');
      if (!go) return;
    }
    setErr(''); setMsg('');
    const r = await api.isStart(cfg);
    if (r?.status === 'error') setErr(r.message); else { setStatus(r); setMsg('Strategy armed'); }
    refreshLive();
  };

  const stopLive = async () => {
    const r = await api.isStop();
    if (r?.status === 'error') setErr(r.message); else { setStatus(r); setMsg('Strategy stopped'); }
    refreshLive();
  };

  const squareOff = async () => {
    if (!window.confirm('Close every open leg now at market?')) return;
    const r = await api.isSquareOff();
    if (r?.status === 'error') setErr(r.message); else setMsg('Square-off sent');
    refreshLive();
  };

  const s = res?.summary;
  const isShort = (cfg?.direction || 'short') === 'short';

  if (!cfg) {
    return (
      <div className="p-6 max-w-[1400px] mx-auto">
        <div className="card">{err ? <p className="text-red-400 text-sm">{err}</p> : <p className="text-gray-500 text-sm">Loading…</p>}</div>
      </div>
    );
  }

  return (
    <div className="p-4 sm:p-6 space-y-4 max-w-[1400px] mx-auto">
      <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-2">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold text-white">Index Straddle Engine</h1>
          <p className="text-xs sm:text-sm text-gray-500 mt-0.5">
            Intraday {isShort ? 'short' : 'long'} straddle on NIFTY, selected by distance to expiry ·{' '}
            <span className={isShort ? 'text-green-400' : 'text-amber-400'}>
              {isShort ? 'the researched edge' : 'documented as losing — see ⓘ'}
            </span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`px-2.5 py-1 rounded-lg text-[11px] font-semibold border ${
            cfg.paper_trade ? 'bg-blue-500/10 text-blue-300 border-blue-500/25'
                            : 'bg-red-500/10 text-red-300 border-red-500/30'}`}>
            {cfg.paper_trade ? 'PAPER' : 'LIVE ORDERS'}
          </span>
          {status?.is_active && <span className="px-2.5 py-1 rounded-lg text-[11px] font-semibold bg-green-500/10 text-green-300 border border-green-500/25">ARMED</span>}
        </div>
      </div>

      <div className="rounded-xl bg-red-500/10 border border-red-500/30 px-4 py-3 text-[13px] text-red-100 flex items-start gap-2.5">
        <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0 text-red-300" />
        <div>
          <strong className="text-red-200">Re-tested on real option prices — the modelled result did not hold.</strong>{' '}
          On actual expired NIFTY contracts (Sep-2023 → Sep-2026), this exact configuration returned between
          {' '}<strong>−₹14k and +₹66k per year</strong> on 3 lots (t = 0.4 to 1.9, depending on how the stop is
          monitored), with a max drawdown of ₹1.2L–₹1.8L and stops on 33–39% of days — not the ₹4.8L the premium
          model produced. The premium <em>level</em> assumption was right (real 0-DTE straddle median 0.41% of spot);
          the intraday path was not. Backtests below still use the model. Keep this on paper; real-premium
          backtests live in <strong>Options Lab</strong>.
        </div>
      </div>

      {err && <div className="rounded-lg bg-red-500/10 border border-red-500/25 px-3 py-2 text-sm text-red-300 flex items-start gap-2"><XCircle className="w-4 h-4 mt-0.5 shrink-0" />{err}</div>}
      {msg && <div className="rounded-lg bg-green-500/10 border border-green-500/25 px-3 py-2 text-sm text-green-300">{msg}</div>}

      <div className="flex gap-1 border-b border-surface-3 overflow-x-auto">
        {[['backtest', 'Backtest', FlaskConical], ['live', 'Live / Paper', Wallet], ['info', 'How it works', Info]].map(([id, label, Icon]) => (
          <button key={id} onClick={() => setTab(id)}
            className={`flex items-center gap-1.5 px-4 py-2 text-sm font-semibold border-b-2 -mb-px transition whitespace-nowrap ${
              tab === id ? 'border-brand-500 text-brand-400' : 'border-transparent text-gray-400 hover:text-gray-200'}`}>
            <Icon className="w-4 h-4" />{label}
          </button>
        ))}
      </div>

      {/* ── configuration ── */}
      {(tab === 'backtest' || tab === 'live') && (
        <div className="card space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-gray-100">Configuration</h2>
            <span className="text-[11.5px] mono text-gray-500">{res?.describe || meta?.research?.config?.slice(0, 60)}</span>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Field label="Direction" hint={isShort ? 'sell premium' : 'buy premium — loses'}>
              <select className={inp} value={cfg.direction} onChange={(e) => set('direction', e.target.value)}>
                {(meta?.directions || []).map((d) => <option key={d.key} value={d.key}>{d.name}</option>)}
              </select>
            </Field>
            <Field label="Structure">
              <select className={inp} value={cfg.structure} onChange={(e) => set('structure', e.target.value)}>
                {(meta?.structures || []).map((d) => <option key={d.key} value={d.key}>{d.name}</option>)}
              </select>
            </Field>
            {cfg.structure === 'strangle' ? (
              <Field label="Wing width (points)" hint="either side of ATM">
                <input type="number" step="50" className={inp} value={cfg.wing_offset}
                  onChange={(e) => set('wing_offset', Number(e.target.value))} />
              </Field>
            ) : (
              <Field label="Strike selection">
                <select className={inp} value={cfg.strike_mode} onChange={(e) => set('strike_mode', e.target.value)}>
                  {(meta?.strike_modes || []).map((d) => <option key={d.key} value={d.key}>{d.name}</option>)}
                </select>
              </Field>
            )}
            {cfg.structure !== 'strangle' && cfg.strike_mode === 'premium' ? (
              <Field label="Target premium (₹)" hint="strike that prices nearest">
                <input type="number" step="5" className={inp} value={cfg.target_premium}
                  onChange={(e) => set('target_premium', Number(e.target.value))} />
              </Field>
            ) : cfg.structure !== 'strangle' ? (
              <Field label="Distance from ATM" hint="− = ITM, + = OTM">
                <select className={inp} value={cfg.strike_offset} onChange={(e) => set('strike_offset', Number(e.target.value))}>
                  {(meta?.strike_offsets || []).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </Field>
            ) : <div />}
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Field label="Entry time" hint="10:00 measured best"><input className={inp} value={cfg.entry_time} onChange={(e) => set('entry_time', e.target.value)} /></Field>
            <Field label="Exit time" hint="flat by 15:20"><input className={inp} value={cfg.exit_time} onChange={(e) => set('exit_time', e.target.value)} /></Field>
            <Field label="Stop %" hint="of combined premium"><input type="number" step="5" className={inp} value={cfg.stop_pct} onChange={(e) => set('stop_pct', Number(e.target.value))} /></Field>
            <Field label="Target %" hint="0 = time exit only"><input type="number" step="5" className={inp} value={cfg.target_pct} onChange={(e) => set('target_pct', Number(e.target.value))} /></Field>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Field label="DTE from" hint="0 = expiry day"><input type="number" className={inp} value={cfg.dte_min} onChange={(e) => set('dte_min', Number(e.target.value))} /></Field>
            <Field label="DTE to" hint="0–1 is the edge"><input type="number" className={inp} value={cfg.dte_max} onChange={(e) => set('dte_max', Number(e.target.value))} /></Field>
            <Field label="Lots" hint={`× ${cfg.lot_size} = ${cfg.lots * cfg.lot_size} qty/leg`}><input type="number" className={inp} value={cfg.lots} onChange={(e) => set('lots', Number(e.target.value))} /></Field>
            <Field label="Lot size"><input type="number" className={inp} value={cfg.lot_size} onChange={(e) => set('lot_size', Number(e.target.value))} /></Field>
            <Field label="Margin per lot (₹)" hint="short only — blocked, not spent"><input type="number" step="10000" className={inp} value={cfg.margin_per_lot} onChange={(e) => set('margin_per_lot', Number(e.target.value))} /></Field>
            <Field label="Starting capital (₹)"><input type="number" step="10000" className={inp} value={cfg.starting_capital} onChange={(e) => set('starting_capital', Number(e.target.value))} /></Field>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <Field label="Skip if prior-day range >" hint="σ · 0 = off"><input type="number" step="0.1" className={inp} value={cfg.skip_prior_range_sigma} onChange={(e) => set('skip_prior_range_sigma', Number(e.target.value))} /></Field>
            <Field label="Skip if |gap| >" hint="σ · 0 = off"><input type="number" step="0.1" className={inp} value={cfg.skip_gap_sigma} onChange={(e) => set('skip_gap_sigma', Number(e.target.value))} /></Field>
            <Field label="Skip if open range >" hint="σ · 0 = off"><input type="number" step="0.05" className={inp} value={cfg.skip_open_range_sigma} onChange={(e) => set('skip_open_range_sigma', Number(e.target.value))} /></Field>
            <Field label="Premium source" hint="model reproduces the research">
              <select className={inp} value={cfg.premium_source} onChange={(e) => set('premium_source', e.target.value)}>
                {(meta?.premium_sources || []).map((d) => <option key={d.key} value={d.key}>{d.name}</option>)}
              </select>
            </Field>
          </div>

          {tab === 'backtest' && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <Field label="Dataset" hint="blank → pull from Zerodha">
                <select className={inp} value={cfg.csv_path} onChange={(e) => set('csv_path', e.target.value)}>
                  <option value="">— pull from broker —</option>
                  {(meta?.datasets || []).map((d) => <option key={d.path} value={d.path}>{d.name}</option>)}
                </select>
              </Field>
              <Field label="Start date"><input type="date" className={inp} value={cfg.start_date} onChange={(e) => set('start_date', e.target.value)} /></Field>
              <Field label="End date"><input type="date" className={inp} value={cfg.end_date} onChange={(e) => set('end_date', e.target.value)} /></Field>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2 pt-1">
            <label className="flex items-center gap-2 text-[12.5px] text-gray-300 cursor-pointer">
              <input type="checkbox" checked={!!cfg.paper_trade} onChange={(e) => set('paper_trade', e.target.checked)} />
              Paper trade
            </label>
            <label className="flex items-center gap-2 text-[12.5px] text-gray-300 cursor-pointer">
              <input type="checkbox" checked={!!cfg.auto_start} onChange={(e) => set('auto_start', e.target.checked)} />
              Auto-start at 09:15
            </label>
            <label className="flex items-center gap-2 text-[12.5px] text-gray-300 cursor-pointer">
              <input type="checkbox" checked={!!cfg.telegram_alerts} onChange={(e) => set('telegram_alerts', e.target.checked)} />
              Telegram alerts
            </label>
            <div className="flex-1" />
            {dirty && (
              <span className="px-2 py-1 rounded-lg text-[10.5px] font-semibold bg-amber-500/10 text-amber-300 border border-amber-500/25">
                unsaved changes
              </span>
            )}
            <button onClick={() => setCfg(meta?.defaults)} className="px-3 py-1.5 rounded-lg text-xs font-medium bg-surface-3 text-gray-300 hover:text-white">
              Reset to research defaults
            </button>
            <button onClick={saveCfg} disabled={saving || !dirty}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-surface-3 text-gray-200 hover:text-white disabled:opacity-40">
              <Save className="w-3.5 h-3.5" />{saving ? 'Saving…' : 'Save config'}
            </button>
            {tab === 'backtest' && (
              <>
                <input ref={fileRef} type="file" accept=".csv" className="hidden" onChange={(e) => doUpload(e.target.files?.[0])} />
                <button onClick={() => fileRef.current?.click()} disabled={uploading}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-surface-3 text-gray-300 hover:text-white disabled:opacity-50">
                  <Upload className="w-3.5 h-3.5" />{uploading ? 'Uploading…' : 'Upload CSV'}
                </button>
                <button onClick={runBacktest} disabled={running}
                  className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-semibold bg-brand-600 text-white hover:bg-brand-500 disabled:opacity-50">
                  {running ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <FlaskConical className="w-3.5 h-3.5" />}
                  {running ? 'Running…' : 'Run backtest'}
                </button>
              </>
            )}
          </div>
          <p className="text-[11px] text-gray-600 -mt-1">
            Backtests always use what is on screen — Save is only needed to make this config the
            one the live engine and the next page load start from. Arming the strategy saves it too.
          </p>
        </div>
      )}

      {/* ── backtest results ── */}
      {tab === 'backtest' && res?.status === 'ok' && s && (
        <div className="space-y-4">
          {res.data_warning && (
            <div className="rounded-lg bg-amber-500/10 border border-amber-500/25 px-3 py-2 text-[12.5px] text-amber-200 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              <span><strong>Incomplete history.</strong> {res.data_warning}</span>
            </div>
          )}

          {res.warmup_note && (
            <div className="rounded-lg bg-amber-500/10 border border-amber-500/25 px-3 py-2 text-[12.5px] text-amber-200 flex items-start gap-2">
              <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
              <span><strong>Warm-up too short.</strong> {res.warmup_note}</span>
            </div>
          )}

          <div className="rounded-lg bg-surface-2 border border-surface-3 px-3 py-2 text-[12px] text-gray-400">
            <span className="text-gray-500">Data used: </span>
            <span className="mono text-gray-200">{res.source}</span>
            <span className="text-gray-600"> · </span>
            <span className="mono text-gray-200">{res.sessions_loaded}</span>
            <span className="text-gray-500"> sessions · </span>
            <span className="text-gray-500">premiums from </span>
            <span className="mono text-gray-200">{res.premium_source === 'model' ? 'calibrated model (computed from index bars)' : 'real Zerodha option candles'}</span>
            {res.window_start && (
              <>
                <span className="text-gray-600"> · </span>
                <span className="text-gray-500">window </span>
                <span className="mono text-gray-200">{res.window_start} → {res.window_end || res.last_day}</span>
                <span className="text-gray-600"> · </span>
                <span className="mono text-gray-200">{res.warmup_sessions}</span>
                <span className="text-gray-500"> warm-up sessions loaded before it</span>
              </>
            )}
          </div>

          <ParityBlock parity={res.parity} />

          <FunnelBlock funnel={res.funnel} />

          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-2">
            <Stat label="Trades" value={s.total_trades} sub={`${s.trades_per_week}/week`} />
            <Stat label="Win rate" value={PCT(s.win_rate, 1)} tone={tone(s.win_rate - 50)} />
            <Stat label="Mean / trade" value={PCT(s.mean_return_pct)} tone={tone(s.mean_return_pct)} />
            <Stat label="t-stat" value={Number(s.t_stat).toFixed(2)} tone={tone(s.t_stat)} />
            <Stat label="P&L / year" value={INR(s.pnl_per_year)} tone={tone(s.pnl_per_year)} />
            <Stat label="Max drawdown" value={INR(s.max_drawdown)} tone="text-red-400" sub={`${s.max_drawdown_pct}%`} />
            <Stat label="Total P&L" value={INR(s.total_pnl)} tone={tone(s.total_pnl)} />
            <Stat label="Profit factor" value={s.profit_factor ?? '—'} />
            <Stat label="Stop-outs" value={`${s.stop_exits} (${s.stop_rate}%)`} tone="text-amber-400" />
            <Stat label="Worst day" value={INR(s.largest_loss)} tone="text-red-400" />
            <Stat label="Avg credit" value={INR(s.avg_credit)} />
            <Stat label="Return / DD" value={s.return_over_dd} />
          </div>

          <div className="card">
            <h3 className="text-sm font-semibold text-gray-100 mb-2">Capital &amp; margin</h3>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
              <Stat label="Starting capital" value={INR(s.starting_capital)} />
              <Stat label="Capital tied up / trade" value={INR(s.capital_used)} sub={s.capital_basis} />
              <Stat label="Avg return on capital" value={PCT(s.avg_return_on_capital_pct, 2)} sub="per trade" tone={tone(s.avg_return_on_capital_pct)} />
              <Stat label="Annual return on capital" value={PCT(s.annual_return_on_capital_pct, 1)} tone={tone(s.annual_return_on_capital_pct)} />
              <Stat label="Final equity" value={INR(s.final_equity)} sub={`${s.total_return_pct}% total`} tone={tone(s.total_return_pct)} />
            </div>
            <p className="text-[11.5px] text-gray-500 mt-2">
              {cfg.direction === 'short'
                ? `Selling options blocks margin, it does not cost premium. ${cfg.lots} lot(s) x ${INR(cfg.margin_per_lot)} = ${INR(cfg.margin_per_lot * cfg.lots)} is blocked for the session and released at square-off. The credit collected is cash in, not capital out. Change "margin per lot" in the config if your broker differs.`
                : `Buying options costs the premium itself - there is no margin. Capital used per trade is the debit paid, which varies with the premium on the day.`}
            </p>
          </div>

          <EquityChart curve={res.equity_curve} />

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div className="card">
              <h3 className="text-sm font-semibold text-gray-100 mb-2">By days to expiry</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-[12.5px]">
                  <thead><tr className="text-gray-500"><th className="text-left px-2 py-1">DTE</th><th className="text-right px-2 py-1">Trades</th><th className="text-right px-2 py-1">Win</th><th className="text-right px-2 py-1">Mean</th><th className="text-right px-2 py-1">P&L</th></tr></thead>
                  <tbody>{(res.by_dte || []).map((r) => (
                    <tr key={r.dte} className="border-t border-surface-3/50">
                      <td className="px-2 py-1 text-gray-200">{r.dte}</td>
                      <td className="px-2 py-1 text-right mono">{r.trades}</td>
                      <td className="px-2 py-1 text-right mono">{r.win_rate}%</td>
                      <td className={`px-2 py-1 text-right mono ${tone(r.mean_return_pct)}`}>{PCT(r.mean_return_pct)}</td>
                      <td className={`px-2 py-1 text-right mono ${tone(r.pnl)}`}>{INR(r.pnl)}</td>
                    </tr>))}
                  </tbody>
                </table>
              </div>
            </div>
            <div className="card">
              <h3 className="text-sm font-semibold text-gray-100 mb-2">Year by year</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-[12.5px]">
                  <thead><tr className="text-gray-500"><th className="text-left px-2 py-1">Year</th><th className="text-right px-2 py-1">Trades</th><th className="text-right px-2 py-1">Win</th><th className="text-right px-2 py-1">Stops</th><th className="text-right px-2 py-1">P&L</th></tr></thead>
                  <tbody>{(res.yearly || []).map((r) => (
                    <tr key={r.period} className="border-t border-surface-3/50">
                      <td className="px-2 py-1 text-gray-200">{r.period}</td>
                      <td className="px-2 py-1 text-right mono">{r.trades}</td>
                      <td className="px-2 py-1 text-right mono">{r.win_rate}%</td>
                      <td className="px-2 py-1 text-right mono text-amber-400">{r.stops}</td>
                      <td className={`px-2 py-1 text-right mono ${tone(r.pnl)}`}>{INR(r.pnl)}</td>
                    </tr>))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-sm font-semibold text-gray-100">Integrity checks</h3>
              <span className={`text-[11px] px-2 py-0.5 rounded ${res.checks?.all_passed ? 'bg-green-500/15 text-green-300' : 'bg-red-500/15 text-red-300'}`}>
                {res.checks?.all_passed ? 'all passed' : 'review'}
              </span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
              {Object.entries(res.checks || {}).filter(([k]) => k !== 'all_passed').map(([k, v]) => (
                <div key={k} className="flex items-center gap-1.5 text-[12px]">
                  {v ? <CheckCircle2 className="w-3.5 h-3.5 text-green-400 shrink-0" /> : <XCircle className="w-3.5 h-3.5 text-red-400 shrink-0" />}
                  <span className="text-gray-400">{k.replace(/_/g, ' ')}</span>
                </div>
              ))}
            </div>
            <p className="text-[11.5px] text-gray-500 mt-2">
              {res.bars?.toLocaleString('en-IN')} bars · {res.first_day} → {res.last_day} ·
              source {res.source} · premium {res.premium_source} ·
              {res.coverage?.in_dte_window} of {res.coverage?.sessions} sessions inside the DTE window ·
              {res.partial_sessions_dropped} partial session(s) dropped
            </p>
          </div>

          <div className="card">
            <div className="flex items-center justify-between mb-2">
              <div>
                <h3 className="text-sm font-semibold text-gray-100">Trade log ({res.trade_count})</h3>
                <p className="text-[11.5px] text-gray-500">Click any row for the plain-English version of that trade.</p>
              </div>
              {res.files?.trades && (
                <button onClick={() => api.isFile(res.files.trades)}
                  className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] bg-surface-3 text-gray-300 hover:text-white">
                  <Download className="w-3 h-3" />CSV
                </button>
              )}
            </div>
            <div className="overflow-x-auto max-h-[520px]">
              <table className="w-full text-[12px]">
                <thead className="sticky top-0 bg-surface-2 z-10">
                  <tr className="text-gray-500">
                    <th className="px-2 py-1.5 text-left font-semibold">Date</th>
                    <th className="px-2 py-1.5 text-center font-semibold">DTE</th>
                    <th className="px-2 py-1.5 text-left font-semibold">What was traded</th>
                    <th className="px-2 py-1.5 text-right font-semibold">Spot</th>
                    <th className="px-2 py-1.5 text-right font-semibold">CE in&rarr;out</th>
                    <th className="px-2 py-1.5 text-right font-semibold">PE in&rarr;out</th>
                    <th className="px-2 py-1.5 text-right font-semibold">{cfg.direction === 'short' ? 'Credit in' : 'Debit paid'}</th>
                    <th className="px-2 py-1.5 text-right font-semibold">{cfg.direction === 'short' ? 'Cost to close' : 'Sold for'}</th>
                    <th className="px-2 py-1.5 text-center font-semibold">Why it ended</th>
                    <th className="px-2 py-1.5 text-right font-semibold">Ret</th>
                    <th className="px-2 py-1.5 text-right font-semibold">P&amp;L</th>
                    <th className="px-2 py-1.5 text-right font-semibold">Equity</th>
                  </tr>
                </thead>
                <tbody>
                  {(res.trades || []).slice().reverse().map((t, i) => {
                    const rowKey = `${t.date}-${i}`;
                    const open = openRow === rowKey;
                    const why = t.exit_reason === 'SL' ? 'Stop hit'
                      : t.exit_reason === 'TARGET' ? 'Target hit' : 'Time exit 15:20';
                    return (
                      <React.Fragment key={rowKey}>
                        <tr onClick={() => setOpenRow(open ? null : rowKey)}
                          className={`border-t border-surface-3/40 cursor-pointer hover:bg-surface-3/40 ${open ? 'bg-surface-3/50' : ''}`}>
                          <td className="px-2 py-1 text-gray-300 whitespace-nowrap">{t.date}</td>
                          <td className="px-2 py-1 text-center">
                            <span className={`text-[10px] px-1.5 py-0.5 rounded ${t.dte === 0 ? 'bg-amber-500/15 text-amber-300' : 'bg-surface-3 text-gray-400'}`}>
                              {t.dte === 0 ? 'expiry' : `${t.dte}d`}
                            </span>
                          </td>
                          <td className="px-2 py-1 whitespace-nowrap">
                            <span className={t.action === 'SELL' ? 'text-red-300' : 'text-green-300'}>{t.action}</span>
                            <span className="text-gray-300"> {Number(t.call_strike).toFixed(0)} CE</span>
                            <span className="text-gray-600"> + </span>
                            <span className={t.action === 'SELL' ? 'text-red-300' : 'text-green-300'}>{t.action}</span>
                            <span className="text-gray-300"> {Number(t.put_strike).toFixed(0)} PE</span>
                            <span className="text-gray-600"> &times;{t.qty}</span>
                          </td>
                          <td className="px-2 py-1 text-right mono text-gray-400">{Number(t.spot_entry).toFixed(0)}</td>
                          <td className="px-2 py-1 text-right mono whitespace-nowrap">
                            {Number(t.call_entry).toFixed(2)}<span className="text-gray-600">&rarr;</span>{Number(t.call_exit).toFixed(2)}
                          </td>
                          <td className="px-2 py-1 text-right mono whitespace-nowrap">
                            {Number(t.put_entry).toFixed(2)}<span className="text-gray-600">&rarr;</span>{Number(t.put_exit).toFixed(2)}
                          </td>
                          <td className="px-2 py-1 text-right mono text-gray-200">{INR(t.basis_value)}</td>
                          <td className="px-2 py-1 text-right mono text-gray-400">{INR(t.exit_value)}</td>
                          <td className="px-2 py-1 text-center">
                            <span className={`text-[10px] px-1.5 py-0.5 rounded whitespace-nowrap ${
                              t.exit_reason === 'SL' ? 'bg-red-500/15 text-red-300'
                              : t.exit_reason === 'TARGET' ? 'bg-green-500/15 text-green-300'
                              : 'bg-surface-3 text-gray-400'}`}>{why}</span>
                          </td>
                          <td className={`px-2 py-1 text-right mono ${tone(t.return_pct)}`}>{PCT(t.return_pct, 1)}</td>
                          <td className={`px-2 py-1 text-right mono font-semibold ${tone(t.pnl)}`}>{INR(t.pnl)}</td>
                          <td className="px-2 py-1 text-right mono text-gray-500">{INR(t.equity)}</td>
                        </tr>
                        {open && (
                          <tr className="bg-surface-3/30 border-t border-surface-3/40">
                            <td colSpan={12} className="px-4 py-3">
                              <p className="text-[13px] text-gray-200 leading-relaxed">{t.story}</p>
                              <div className="mt-2 grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-x-5 gap-y-1.5 text-[11.5px]">
                                <div><span className="text-gray-500">Entry &rarr; exit</span><div className="mono text-gray-300">{t.entry_time} &rarr; {t.exit_time}</div></div>
                                <div><span className="text-gray-500">Held</span><div className="mono text-gray-300">{t.bars_held} min</div></div>
                                <div><span className="text-gray-500">Spot moved</span><div className="mono text-gray-300">{Number(t.spot_entry).toFixed(0)} &rarr; {Number(t.spot_exit).toFixed(0)} ({(t.spot_exit - t.spot_entry) >= 0 ? '+' : ''}{(t.spot_exit - t.spot_entry).toFixed(0)})</div></div>
                                <div><span className="text-gray-500">Capital tied up</span><div className="mono text-gray-300">{INR(t.capital_used)}</div></div>
                                <div><span className="text-gray-500">Return on capital</span><div className={`mono ${tone(t.return_on_capital_pct)}`}>{PCT(t.return_on_capital_pct, 2)}</div></div>
                                <div><span className="text-gray-500">Worst point in trade</span><div className="mono text-red-400">{PCT((t.mae || 0) * 100, 1)}</div></div>
                                <div><span className="text-gray-500">CE leg value in</span><div className="mono text-gray-300">{INR(t.call_entry_value)}</div></div>
                                <div><span className="text-gray-500">PE leg value in</span><div className="mono text-gray-300">{INR(t.put_entry_value)}</div></div>
                                <div><span className="text-gray-500">Prior-day range</span><div className="mono text-gray-300">{Number(t.prior_range_sigma).toFixed(2)}&sigma;</div></div>
                                <div><span className="text-gray-500">Overnight gap</span><div className="mono text-gray-300">{Number(t.gap_sigma) >= 0 ? '+' : ''}{Number(t.gap_sigma).toFixed(2)}&sigma;</div></div>
                                <div><span className="text-gray-500">Implied vol used</span><div className="mono text-gray-300">{(Number(t.iv_regime) * 100).toFixed(1)}%</div></div>
                                <div><span className="text-gray-500">Effective days to expiry</span><div className="mono text-gray-300">{Number(t.de0).toFixed(2)}</div></div>
                              </div>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {tab === 'backtest' && !res && (
        <div className="card text-center py-10">
          <FlaskConical className="w-10 h-10 mx-auto text-gray-600 mb-3" />
          <p className="text-sm text-gray-400">Upload a NIFTY 1-minute CSV (or connect Zerodha), then run the backtest.</p>
          <p className="text-[12px] text-gray-600 mt-1">On untouched defaults this should reproduce the research: 421 trades, 72.9% win rate.</p>
        </div>
      )}

      {/* ── live / paper ── */}
      {tab === 'live' && (
        <div className="space-y-4">
          <div className="card">
            <div className="flex flex-wrap items-center gap-2">
              {!status?.is_active ? (
                <button onClick={startLive} className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold bg-green-600 text-white hover:bg-green-500">
                  <Play className="w-4 h-4" />Arm strategy
                </button>
              ) : (
                <button onClick={stopLive} className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-semibold bg-red-600 text-white hover:bg-red-500">
                  <Square className="w-4 h-4" />Stop
                </button>
              )}
              <button onClick={doPreview} className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm bg-surface-3 text-gray-300 hover:text-white">
                <Eye className="w-4 h-4" />Preview strikes
              </button>
              <button onClick={refreshLive} className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm bg-surface-3 text-gray-300 hover:text-white">
                <RefreshCw className="w-4 h-4" />Refresh
              </button>
              <div className="flex-1" />
              <button onClick={squareOff} className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm bg-amber-600/20 text-amber-300 border border-amber-500/30 hover:bg-amber-600/30">
                <AlertTriangle className="w-4 h-4" />Square off all
              </button>
            </div>
            {!cfg.paper_trade && (
              <p className="mt-3 text-[12.5px] text-red-300 bg-red-500/10 border border-red-500/25 rounded-lg px-3 py-2">
                Paper trade is OFF — this will place real orders. Short volatility loses fast on a trend day and a stop is an intention, not a guaranteed fill.
              </p>
            )}
          </div>

          {preview?.legs && (
            <div className="card">
              <h3 className="text-sm font-semibold text-gray-100 mb-2 flex items-center gap-1.5"><Target className="w-4 h-4" />What it would trade right now</h3>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
                <Stat label="Spot" value={Number(preview.spot).toFixed(1)} />
                <Stat label="Structure" value={preview.legs.label} />
                <Stat label="Call strike" value={preview.legs.call_strike} sub={`${preview.legs.action} CE`} />
                <Stat label="Put strike" value={preview.legs.put_strike} sub={`${preview.legs.action} PE`} />
                <Stat label="Expiry" value={preview.expiry} sub={`${preview.dte} DTE`} />
              </div>
            </div>
          )}

          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-2">
            <Stat label="Status" value={status?.is_active ? 'ARMED' : 'IDLE'} tone={status?.is_active ? 'text-green-400' : 'text-gray-400'} />
            <Stat label="Mode" value={status?.paper_trade ? 'PAPER' : 'LIVE'} tone={status?.paper_trade ? 'text-blue-300' : 'text-red-300'} />
            <Stat label="NIFTY" value={status?.index_ltp ? Number(status.index_ltp).toFixed(1) : '—'} />
            <Stat label="DTE today" value={status?.dte_today ?? '—'} />
            <Stat label="Eligible" value={status?.eligible_today ? 'YES' : 'NO'} tone={status?.eligible_today ? 'text-green-400' : 'text-gray-500'} sub={status?.skip_reason || ''} />
            <Stat label="Open legs" value={status?.open_positions ?? 0} />
            <Stat label="Combined entry" value={status?.combined_entry ? `₹${Number(status.combined_entry).toFixed(2)}` : '—'} />
            <Stat label="Stop level" value={status?.stop_level ? `₹${Number(status.stop_level).toFixed(2)}` : '—'} tone="text-red-400" />
            <Stat label="Realised" value={INR(status?.realised_profit)} tone={tone(status?.realised_profit)} />
            <Stat label="Equity" value={INR(status?.equity)} />
          </div>

          <div className="card">
            <h3 className="text-sm font-semibold text-gray-100 mb-2">Positions</h3>
            {positions.length === 0 ? (
              <p className="text-sm text-gray-500">No legs today.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-[12px]">
                  <thead><tr className="text-gray-500">
                    {['Date', 'Symbol', 'Type', 'Strike', 'Action', 'Qty', 'Entry', 'LTP', 'Exit', 'Why', 'P&L', 'Status'].map((h, i) => (
                      <th key={h} className={`px-2 py-1.5 font-semibold ${i ? 'text-right' : 'text-left'}`}>{h}</th>))}
                  </tr></thead>
                  <tbody>
                    {positions.map((p) => (
                      <tr key={p.id} className="border-t border-surface-3/40">
                        <td className="px-2 py-1 text-gray-300">{p.date}</td>
                        <td className="px-2 py-1 text-gray-300">{p.tradingsymbol}</td>
                        <td className="px-2 py-1 text-right">{p.opt_type}</td>
                        <td className="px-2 py-1 text-right mono">{p.strike}</td>
                        <td className={`px-2 py-1 text-right ${p.action === 'SELL' ? 'text-red-300' : 'text-green-300'}`}>{p.action}</td>
                        <td className="px-2 py-1 text-right mono">{p.qty}</td>
                        <td className="px-2 py-1 text-right mono">{p.entry_price}</td>
                        <td className="px-2 py-1 text-right mono">{p.ltp ?? '—'}</td>
                        <td className="px-2 py-1 text-right mono">{p.exit_price ?? '—'}</td>
                        <td className="px-2 py-1 text-right text-gray-500">{p.exit_reason ?? '—'}</td>
                        <td className={`px-2 py-1 text-right mono font-semibold ${tone(p.mtm)}`}>{p.mtm != null ? INR(p.mtm) : '—'}</td>
                        <td className="px-2 py-1 text-right">
                          <span className={`text-[10px] px-1.5 py-0.5 rounded ${p.status === 'OPEN' ? 'bg-blue-500/15 text-blue-300' : 'bg-surface-3 text-gray-400'}`}>{p.status}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {tab === 'info' && <IndexStraddleInfo cfg={cfg} research={meta?.research} />}
    </div>
  );
}
