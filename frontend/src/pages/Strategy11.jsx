import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  ReferenceDot, ReferenceLine, Legend,
} from 'recharts';
import {
  Play, Square, Settings2, ChevronDown, ChevronUp, AlertCircle, CheckCircle2,
  Activity, Layers, Zap, ShieldCheck, FlaskConical, Info, Lock, X,
} from 'lucide-react';
import { api } from '../api';

const REFRESH_MS = 2_000;

const INR = (v, d = 2) =>
  (v ?? 0).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });

const LEG_STYLE = {
  OPEN:       { bg: 'bg-blue-600/20', text: 'text-blue-400', label: 'Open' },
  TARGET:     { bg: 'bg-emerald-600/20', text: 'text-emerald-400', label: 'Target Hit' },
  LEG2_EXIT:  { bg: 'bg-amber-600/20', text: 'text-amber-400', label: 'Leg-2 Exit' },
  EXPIRY:     { bg: 'bg-violet-600/20', text: 'text-violet-400', label: 'Expiry Exit' },
  MANUAL_EXIT:{ bg: 'bg-gray-600/20', text: 'text-gray-300', label: 'Manual' },
};

const DEFAULT_CONFIG = {
  paper_trade: true,
  expiry_type: 'monthly',
  strike_mode: 'ITM',
  target_mode: 'points',
  target_points: 300,
  target_percent: 150,
  lots: 3,
  manage_second_leg: true,
  leg2_exit_mode: 'fraction',
  leg2_exit_value: 2,
  max_open_pairs: 5,
};

function Card({ title, icon: Icon, children, right = null }) {
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
      {(title || right) && (
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2 text-gray-400 text-xs font-medium uppercase tracking-wider">
            {Icon && <Icon className="w-3.5 h-3.5" />} {title}
          </div>
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

function Stat({ label, value, color = 'text-gray-100' }) {
  return (
    <div className="bg-surface-3/40 rounded-lg px-3 py-2.5 text-center">
      <div className={`text-base font-bold ${color}`}>{value}</div>
      <div className="text-gray-500 text-[11px] mt-0.5">{label}</div>
    </div>
  );
}

function Field({ label, children, hint }) {
  return (
    <div>
      <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">{label}</label>
      {children}
      {hint && <p className="text-gray-600 text-[10px] mt-0.5">{hint}</p>}
    </div>
  );
}

const inputCls = 'w-full bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';

/* Illustrative VWAP / prev-VWAP crossover (synthetic, for the docs only). */
const DOC_CHART = [
  { t: '09:20', price: 23990, vwap: 23993, prev: 24000 },
  { t: '09:35', price: 23984, vwap: 23990, prev: 24000 },
  { t: '09:50', price: 23996, vwap: 23992, prev: 24000 },
  { t: '10:05', price: 24008, vwap: 23998, prev: 24000 },
  { t: '10:20', price: 24026, vwap: 24007, prev: 24000 },
  { t: '10:35', price: 24018, vwap: 24010, prev: 24000 },
  { t: '11:00', price: 24042, vwap: 24017, prev: 24000 },
  { t: '12:00', price: 24061, vwap: 24027, prev: 24000 },
  { t: '13:00', price: 24080, vwap: 24037, prev: 24000 },
  { t: '14:00', price: 24102, vwap: 24048, prev: 24000 },
];

function DocSection({ n, title, children }) {
  return (
    <div className="bg-surface-3/30 rounded-lg p-4">
      <div className="flex items-center gap-2 mb-2">
        <span className="w-5 h-5 rounded-full bg-violet-600/30 text-violet-300 flex items-center justify-center text-[10px] font-bold">{n}</span>
        <h3 className="text-sm font-semibold text-gray-100">{title}</h3>
      </div>
      <div className="text-xs text-gray-400 space-y-2 leading-relaxed">{children}</div>
    </div>
  );
}

function DocModal({ onClose }) {
  const [unlocked, setUnlocked] = useState(false);
  const [pwd, setPwd] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const unlock = async () => {
    setBusy(true); setErr('');
    try {
      const res = await api.strategy11DocUnlock(pwd);
      if (res.ok) setUnlocked(true);
      else setErr('Incorrect password.');
    } catch { setErr('Could not verify password.'); }
    finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="bg-surface-1 border border-surface-3 rounded-xl w-full max-w-4xl max-h-[88vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-3 border-b border-surface-3">
          <div className="flex items-center gap-2 text-gray-100 font-semibold">
            <Info className="w-4 h-4 text-violet-400" /> Strategy 11 — Core Documentation
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-200"><X className="w-5 h-5" /></button>
        </div>

        {!unlocked ? (
          <div className="p-8 flex flex-col items-center justify-center gap-3 text-center">
            <div className="w-12 h-12 rounded-full bg-surface-3 flex items-center justify-center"><Lock className="w-6 h-6 text-violet-400" /></div>
            <div className="text-sm text-gray-300">This documentation is password-protected.</div>
            <div className="flex items-center gap-2">
              <input type="password" value={pwd} autoFocus
                onChange={(e) => setPwd(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && unlock()}
                placeholder="Enter password"
                className="bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-violet-500/60" />
              <button onClick={unlock} disabled={busy || !pwd}
                className="px-4 py-1.5 text-sm rounded-lg bg-violet-600 hover:bg-violet-500 text-white font-semibold disabled:opacity-50">
                Unlock
              </button>
            </div>
            {err && <div className="text-red-400 text-xs">{err}</div>}
          </div>
        ) : (
          <div className="p-5 overflow-y-auto space-y-3">
            <DocSection n="1" title="Core Overview">
              <p>Strategy 11 is a <strong>positional</strong> NIFTY options strategy. Each trading day, on the
                day&apos;s <strong>first</strong> VWAP × previous-day-VWAP crossover, it buys <strong>one CALL and one PUT</strong>
                (a strangle/straddle depending on strikes) and holds them across days — there is <strong>no stop-loss</strong>,
                only a profit target plus a smart second-leg control to limit the losing side.</p>
              <p>It mirrors the read-only <em>Research → VWAP vs Prev VWAP</em> module, turned into live/paper execution.</p>
            </DocSection>

            <DocSection n="2" title="Entry Condition">
              <p>Every minute the app computes today&apos;s running VWAP and compares it to <strong>yesterday&apos;s full-day VWAP</strong>
                (a flat reference line). The <strong>first time</strong> the running VWAP crosses that line (either direction),
                between <strong>09:30 and 15:15</strong>, is the entry trigger. Direction is ignored — it always buys both a CALL and a PUT.</p>
              <p>One entry per day, NRML (carried overnight). The chart below illustrates a bullish cross around 10:20 — that minute is the entry.</p>
              <div className="bg-surface-2 rounded-lg p-2 mt-1">
                <ResponsiveContainer width="100%" height={220}>
                  <LineChart data={DOC_CHART}>
                    <CartesianGrid stroke="rgba(120,130,150,0.15)" strokeDasharray="3 3" />
                    <XAxis dataKey="t" tick={{ fill: '#94a3b8', fontSize: 10 }} />
                    <YAxis domain={['dataMin-10', 'dataMax+10']} tick={{ fill: '#94a3b8', fontSize: 10 }} width={56} />
                    <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <ReferenceLine y={24000} stroke="#fbbf24" strokeDasharray="5 4" />
                    <Line type="monotone" dataKey="price" name="NIFTY" stroke="#cbd5e1" strokeWidth={1.5} dot={false} />
                    <Line type="monotone" dataKey="vwap" name="VWAP" stroke="#818cf8" strokeWidth={1.5} dot={false} />
                    <Line type="monotone" dataKey="prev" name="Prev VWAP" stroke="#fbbf24" strokeWidth={1} dot={false} />
                    <ReferenceDot x="10:20" y={24026} r={6} fill="#34d399" stroke="#0f172a" />
                  </LineChart>
                </ResponsiveContainer>
                <div className="text-[11px] text-emerald-400 text-center mt-1">● Entry — buy CE + PUT at the first crossover</div>
              </div>
            </DocSection>

            <DocSection n="3" title="Strike Selection (200-point offset)">
              <p>At entry the spot is rounded to the nearest 50 → ATM. Then:</p>
              <table className="w-full text-[11px] mt-1">
                <thead><tr className="text-gray-500 border-b border-surface-3">
                  <th className="text-left py-1">Mode</th><th className="text-left py-1">CALL strike</th><th className="text-left py-1">PUT strike</th></tr></thead>
                <tbody className="text-gray-300">
                  <tr><td className="py-1">200 ITM</td><td>ATM − 200</td><td>ATM + 200</td></tr>
                  <tr><td className="py-1">200 OTM</td><td>ATM + 200</td><td>ATM − 200</td></tr>
                  <tr><td className="py-1">ATM</td><td>ATM</td><td>ATM</td></tr>
                </tbody>
              </table>
              <p>Weekly or monthly expiry is configurable; the engine resolves the live contract for that day.</p>
            </DocSection>

            <DocSection n="4" title="Target — no stop-loss">
              <p>Each leg has only a profit target (premium points). Three target types:</p>
              <ul className="list-disc list-inside space-y-0.5">
                <li><strong>Points</strong> — entry + N (default 300)</li>
                <li><strong>Percent</strong> — entry × (1 + N%) (e.g. 150 → 2.5× entry)</li>
                <li><strong>Double</strong> — 2 × entry premium</li>
              </ul>
              <p>There is <strong>no SL</strong>. A leg is held until its target hits or until the expiry-day square-off.</p>
            </DocSection>

            <DocSection n="5" title="Second-Leg Loss Control">
              <p>One leg usually wins, the other decays. <strong>Once the first leg books its target</strong>, the other (losing)
                leg is managed:</p>
              <ul className="list-disc list-inside space-y-0.5">
                <li>Breaks back <strong>above its entry</strong> → ride it (to its own target / expiry).</li>
                <li><strong>Fraction</strong> — cut at entry ÷ N (½, ⅓, ¼) as it decays (a hard stop).</li>
                <li><strong>Points / Percent</strong> — cut at entry − buffer when it recovers near entry.</li>
                <li>Never triggers → squared off at 15:15 on expiry day.</li>
              </ul>
            </DocSection>

            <DocSection n="6" title="Exit Conditions">
              <p>A leg exits on, in priority: <strong>Target</strong> → <strong>Leg-2 cut</strong> (only on the managed leg) →
                <strong> Expiry-day 15:15</strong> square-off. Positions can span multiple days.</p>
            </DocSection>

            <DocSection n="7" title="Execution Flow — when GTT vs when live observation">
              <table className="w-full text-[11px]">
                <thead><tr className="text-gray-500 border-b border-surface-3">
                  <th className="text-left py-1">Event</th><th className="text-left py-1">Mechanism</th></tr></thead>
                <tbody className="text-gray-300">
                  <tr className="border-b border-surface-3/40"><td className="py-1">Entry detection (first crossover)</td><td className="text-blue-300">Live — app loop watches VWAP each minute</td></tr>
                  <tr className="border-b border-surface-3/40"><td className="py-1">Target exit (each leg)</td><td className="text-emerald-300">GTT placed at entry → fires server-side even if app is offline</td></tr>
                  <tr className="border-b border-surface-3/40"><td className="py-1">Leg-2 fraction cut</td><td className="text-emerald-300">GTT stop placed once leg-2 is armed</td></tr>
                  <tr className="border-b border-surface-3/40"><td className="py-1">Leg-2 points/percent cut</td><td className="text-blue-300">Live — dynamic (ride-above-entry / recover-near-entry)</td></tr>
                  <tr><td className="py-1">Expiry-day 15:15 square-off</td><td className="text-blue-300">Live — app loop places market SELL</td></tr>
                </tbody>
              </table>
              <p className="mt-1">On each morning&apos;s start, the app <strong>reconciles</strong> against the broker&apos;s net positions to learn which GTTs filled while you were away. <strong>Paper mode</strong> simulates every fill against the LTP (no real orders / GTTs).</p>
            </DocSection>

            <DocSection n="8" title="Worked Example (Monthly · 200 ITM · target +300 · 3 lots = 195 qty)">
              <p>Spot at crossover <strong>24,000</strong> → ATM 24,000. Buy <strong>23,800 CE @ ₹350</strong> and <strong>24,200 PE @ ₹330</strong>.</p>
              <p>Targets (+300): CE → <strong>650</strong>, PE → <strong>630</strong>.</p>
              <p>Day 2 — market rises, <strong>CE hits 650</strong> (GTT fires): P&amp;L = (650 − 350) × 195 = <strong className="text-emerald-400">+₹58,500</strong>.</p>
              <p>PE is now the losing leg → leg-2 armed. With <strong>Fraction ½</strong>, cut level = 330 ÷ 2 = <strong>165</strong>.</p>
              <ul className="list-disc list-inside space-y-0.5">
                <li>PE decays to 165 → exit (165 − 330) × 195 = <strong className="text-red-400">−₹32,175</strong>. Pair net = <strong className="text-emerald-400">+₹26,325</strong>.</li>
                <li>OR PE reclaims above 330 → ride it; it may hit its own 630 target (+₹58,500) or run to expiry.</li>
              </ul>
              <p className="text-gray-500">Numbers illustrative — actual fills depend on live premiums &amp; slippage.</p>
            </DocSection>

            <DocSection n="9" title="Daily operation (positional)">
              <p>Zerodha issues a new access token each morning, so <strong>log in and keep the app running</strong> on trading days
                for entries and live leg-2 management. Target / fraction GTTs rest at Zerodha and fire even if the app is offline.
                Open positions are stored in Postgres (<code className="text-violet-300">strategy11_legs</code>) and restored on restart.</p>
            </DocSection>
          </div>
        )}
      </div>
    </div>
  );
}

export default function Strategy11() {
  const [status, setStatus] = useState(null);
  const [config, setConfig] = useState(DEFAULT_CONFIG);
  const [configOpen, setConfigOpen] = useState(true);
  const [history, setHistory] = useState([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [docOpen, setDocOpen] = useState(false);
  const configInit = useRef(false);

  const msg = (m, isErr = false) => {
    if (isErr) { setError(m); setTimeout(() => setError(''), 4500); }
    else { setSuccess(m); setTimeout(() => setSuccess(''), 3000); }
  };

  const fetchAll = useCallback(async () => {
    try {
      const st = await api.getStrategy11Status().catch(() => null);
      if (st) {
        setStatus(st);
        if (st.config && !configInit.current) {
          setConfig((p) => ({ ...DEFAULT_CONFIG, ...p, ...st.config }));
          configInit.current = true;
        }
      }
    } catch {}
  }, []);

  const fetchHistory = useCallback(async () => {
    try {
      const d = await api.strategy11History();
      setHistory(d?.trades || []);   // API returns newest-first
    } catch {}
  }, []);

  const handleSimulate = async () => {
    try {
      const res = await api.strategy11SimulateEntry();
      if (res.status === 'ok' || res.open_pairs != null) { msg('Simulated crossover entry placed (paper).'); fetchAll(); }
      else msg(res.message || 'Simulate failed', true);
    } catch (e) { msg(e.message || 'Simulate failed', true); }
  };

  const handleReset = async () => {
    if (!window.confirm('Clear ALL Strategy 11 paper positions and history rows? This cannot be undone.')) return;
    try {
      const res = await api.strategy11Reset();
      if (res.status === 'ok' || res.open_pairs != null) { msg('Paper positions cleared.'); setStatus(res); fetchHistory(); }
      else msg(res.message || 'Reset failed', true);
    } catch (e) { msg(e.message || 'Reset failed', true); }
  };

  useEffect(() => {
    fetchAll(); fetchHistory();
    const id = setInterval(fetchAll, REFRESH_MS);
    return () => clearInterval(id);
  }, [fetchAll, fetchHistory]);

  const handleStart = async () => {
    setLoading(true);
    try { setStatus(await api.strategy11Start(config)); msg('Strategy 11 started.'); }
    catch (e) { msg(e.message || 'Start failed', true); }
    finally { setLoading(false); }
  };
  const handleStop = async () => {
    setLoading(true);
    try { setStatus(await api.strategy11Stop()); msg('Stopped — open pairs keep being managed.'); }
    catch (e) { msg(e.message || 'Stop failed', true); }
    finally { setLoading(false); }
  };
  const handleSave = async () => {
    try { await api.strategy11UpdateConfig(config); msg('Config saved.'); }
    catch (e) { msg(e.message || 'Save failed', true); }
  };

  const isActive = status?.is_active;
  const paper = config.paper_trade;
  const openLegs = [];
  (status?.trades || []).forEach((p) => {
    if (p.closed) return;
    (p.legs || []).forEach((l) => openLegs.push({ ...l, pair: p }));
  });

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-7xl mx-auto">
      {docOpen && <DocModal onClose={() => setDocOpen(false)} />}
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-start gap-2">
          <button onClick={() => setDocOpen(true)} title="Strategy documentation"
            className="mt-1 w-7 h-7 shrink-0 rounded-full bg-violet-600/15 border border-violet-500/30 text-violet-400 hover:bg-violet-600/25 flex items-center justify-center transition">
            <Info className="w-4 h-4" />
          </button>
          <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold text-gray-100">Strategy 11 — VWAP vs Prev-Day VWAP</h1>
            <span className="px-2 py-0.5 rounded-full bg-violet-500/15 text-violet-400 text-xs font-semibold border border-violet-500/30">
              NIFTY · Options · Positional
            </span>
            <span className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${
              paper ? 'bg-amber-500/15 text-amber-400 border-amber-500/30' : 'bg-red-500/15 text-red-400 border-red-500/30'
            }`}>
              {paper ? 'PAPER' : 'LIVE'}
            </span>
          </div>
          <p className="text-gray-500 text-sm mt-0.5">
            First crossover each day → buy CE + PUT, target-only (no SL), held to target or 15:15 expiry-day exit.
            Hybrid execution: target &amp; fraction-stop via GTT, dynamic leg-2 managed live.
          </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {paper && (
            <button onClick={handleSimulate}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-surface-3 text-amber-400 hover:bg-surface-3/80 border border-amber-500/30 transition"
              title="Paper only — force an entry now to watch the full cycle">
              <Zap className="w-3.5 h-3.5" /> Simulate Crossover
            </button>
          )}
          {!isActive ? (
            <button onClick={handleStart} disabled={loading}
              className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-semibold disabled:opacity-50 transition">
              <Play className="w-4 h-4" /> Start
            </button>
          ) : (
            <button onClick={handleStop} disabled={loading}
              className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-red-600 hover:bg-red-500 text-white font-semibold disabled:opacity-50 transition">
              <Square className="w-4 h-4" /> Stop
            </button>
          )}
        </div>
      </div>

      {error && <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-red-400 text-sm"><AlertCircle className="w-4 h-4" /> {error}</div>}
      {success && <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-4 py-2 text-emerald-400 text-sm"><CheckCircle2 className="w-4 h-4" /> {success}</div>}

      {/* Summary */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        <Stat label="Status" value={isActive ? 'Running' : 'Idle'} color={isActive ? 'text-blue-400 font-semibold' : 'text-gray-400'} />
        <Stat label="Open Pairs" value={status?.open_pairs ?? 0} color={(status?.open_pairs ?? 0) > 0 ? 'text-blue-400' : 'text-gray-400'} />
        <Stat label="Entered Today" value={status?.entered_today ? 'Yes' : 'No'} color={status?.entered_today ? 'text-emerald-400' : 'text-gray-400'} />
        <Stat label="Realized P&L" value={`₹${INR(status?.realized_pnl, 0)}`} color={(status?.realized_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'} />
        <Stat label="Unrealized P&L" value={`₹${INR(status?.unrealized_pnl, 0)}`} color={(status?.unrealized_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'} />
      </div>

      {/* Config */}
      <Card title="Configuration" icon={Settings2}
        right={<button onClick={() => setConfigOpen((o) => !o)} className="text-gray-500 hover:text-gray-300">{configOpen ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}</button>}>
        {configOpen && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
              <Field label="Mode">
                <button onClick={() => setConfig((c) => ({ ...c, paper_trade: !c.paper_trade }))}
                  className={`w-full flex items-center justify-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium border transition ${
                    config.paper_trade ? 'bg-amber-600/20 text-amber-400 border-amber-500/40' : 'bg-red-600/20 text-red-400 border-red-500/40'
                  }`}>
                  <ShieldCheck className="w-3.5 h-3.5" /> {config.paper_trade ? 'Paper' : 'Live'}
                </button>
              </Field>
              <Field label="Expiry">
                <select value={config.expiry_type} onChange={(e) => setConfig((c) => ({ ...c, expiry_type: e.target.value }))} className={inputCls}>
                  <option value="monthly">Monthly</option>
                  <option value="weekly">Weekly</option>
                </select>
              </Field>
              <Field label="Strike">
                <select value={config.strike_mode} onChange={(e) => setConfig((c) => ({ ...c, strike_mode: e.target.value }))} className={inputCls}>
                  <option value="ITM">200 ITM</option>
                  <option value="OTM">200 OTM</option>
                  <option value="ATM">ATM</option>
                </select>
              </Field>
              <Field label="Lots" hint={`qty = ${config.lots} × 65 = ${config.lots * 65}`}>
                <input type="number" min="1" value={config.lots}
                  onChange={(e) => setConfig((c) => ({ ...c, lots: Math.max(1, parseInt(e.target.value) || 1) }))} className={inputCls} />
              </Field>
              <Field label="Target Type">
                <select value={config.target_mode} onChange={(e) => setConfig((c) => ({ ...c, target_mode: e.target.value }))} className={inputCls}>
                  <option value="points">Points</option>
                  <option value="percent">Percent</option>
                  <option value="double">Double premium</option>
                </select>
              </Field>
              {config.target_mode === 'points' && (
                <Field label="Target (pts)">
                  <input type="number" min="1" value={config.target_points}
                    onChange={(e) => setConfig((c) => ({ ...c, target_points: Math.max(1, parseFloat(e.target.value) || 1) }))} className={inputCls} />
                </Field>
              )}
              {config.target_mode === 'percent' && (
                <Field label="Target (% gain)">
                  <input type="number" min="1" value={config.target_percent}
                    onChange={(e) => setConfig((c) => ({ ...c, target_percent: Math.max(1, parseFloat(e.target.value) || 1) }))} className={inputCls} />
                </Field>
              )}
              <Field label="Max Open Pairs">
                <input type="number" min="1" value={config.max_open_pairs}
                  onChange={(e) => setConfig((c) => ({ ...c, max_open_pairs: Math.max(1, parseInt(e.target.value) || 1) }))} className={inputCls} />
              </Field>
            </div>

            {/* 2nd-leg loss control */}
            <div className="pt-3 border-t border-surface-3">
              <button onClick={() => setConfig((c) => ({ ...c, manage_second_leg: !c.manage_second_leg }))}
                className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium border transition mb-2 ${
                  config.manage_second_leg ? 'bg-amber-600/20 text-amber-400 border-amber-500/40' : 'bg-surface-3 text-gray-400 border-surface-4'
                }`}>
                <Zap className="w-3.5 h-3.5" /> 2nd-Leg Loss Control: {config.manage_second_leg ? 'ON' : 'OFF'}
              </button>
              {config.manage_second_leg && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  <Field label="Exit Type">
                    <select value={config.leg2_exit_mode}
                      onChange={(e) => {
                        const m = e.target.value;
                        setConfig((c) => ({ ...c, leg2_exit_mode: m, leg2_exit_value: m === 'fraction' ? 2 : 15 }));
                      }} className={inputCls}>
                      <option value="fraction">Fraction of premium (GTT)</option>
                      <option value="points">Points below entry (live)</option>
                      <option value="percent">Percent below entry (live)</option>
                    </select>
                  </Field>
                  <Field label={config.leg2_exit_mode === 'fraction' ? 'Cut at' : `Buffer (${config.leg2_exit_mode === 'percent' ? '%' : 'pts'})`}>
                    {config.leg2_exit_mode === 'fraction' ? (
                      <select value={config.leg2_exit_value} onChange={(e) => setConfig((c) => ({ ...c, leg2_exit_value: parseInt(e.target.value) }))} className={inputCls}>
                        <option value={2}>Half (÷2)</option>
                        <option value={3}>One-third (÷3)</option>
                        <option value={4}>One-quarter (÷4)</option>
                      </select>
                    ) : (
                      <input type="number" min="1" value={config.leg2_exit_value}
                        onChange={(e) => setConfig((c) => ({ ...c, leg2_exit_value: Math.max(1, parseFloat(e.target.value) || 1) }))} className={inputCls} />
                    )}
                  </Field>
                </div>
              )}
            </div>

            <p className="text-gray-600 text-[11px]">
              <strong>{config.expiry_type} {config.strike_mode}</strong> · one entry/day on the first crossover ·
              target = {config.target_mode === 'double' ? '2× premium' : config.target_mode === 'percent' ? `+${config.target_percent}%` : `+${config.target_points} pts`} ·
              no stop-loss · expiry-day square-off 15:15.
              {config.manage_second_leg && <> Leg-2: {config.leg2_exit_mode === 'fraction' ? `cut at entry ÷ ${config.leg2_exit_value} via GTT` : `cut at entry − ${config.leg2_exit_value}${config.leg2_exit_mode === 'percent' ? '%' : ' pts'} live`}.</>}
              {' '}Changes apply on next <strong>Start</strong> (or Save Config for an open run).
            </p>
            <div className="flex justify-end">
              <button onClick={handleSave} className="px-4 py-1.5 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded-lg font-medium transition">Save Config</button>
            </div>
          </div>
        )}
      </Card>

      {/* Open positions */}
      <Card title={`Open Positions (${openLegs.length} legs)`} icon={Layers}
        right={paper && openLegs.length > 0 && (
          <button onClick={handleReset}
            className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg bg-red-600/15 text-red-400 hover:bg-red-600/25 border border-red-500/30 transition"
            title="Paper only — clear all tracked positions">
            <X className="w-3.5 h-3.5" /> Clear
          </button>
        )}>
        {openLegs.length === 0 ? (
          <p className="text-gray-500 text-sm py-6 text-center">No open positions. An entry is taken on the first VWAP crossover each trading day.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-surface-3 text-gray-500">
                  {['Entry Date', 'Time', 'Dir', 'Expiry', 'Strike', 'Option', 'Entry', 'Target', 'LTP', 'Leg-2', 'State', 'P&L (live)'].map((h) => (
                    <th key={h} className="text-left font-medium pb-2 pr-3 whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {openLegs.map((l, i) => {
                  const st = LEG_STYLE[l.state] || LEG_STYLE.OPEN;
                  const live = l.ltp ? (l.ltp - l.entry_price) * l.qty : 0;
                  return (
                    <tr key={i} className="border-b border-surface-3/30 hover:bg-surface-3/20">
                      <td className="py-1.5 pr-3 text-gray-400">{l.entry_date}</td>
                      <td className="py-1.5 pr-3 text-gray-400">{l.entry_time}</td>
                      <td className="py-1.5 pr-3"><span className={l.option_type === 'CE' ? 'text-emerald-400' : 'text-red-400'}>{l.option_type === 'CE' ? 'CALL' : 'PUT'}</span></td>
                      <td className="py-1.5 pr-3 text-gray-400">{l.pair.expiry} <span className="text-gray-600">({l.pair.expiry_type})</span></td>
                      <td className="py-1.5 pr-3 text-gray-300">{l.strike}</td>
                      <td className="py-1.5 pr-3 text-gray-400 font-mono text-[11px]">{l.symbol}</td>
                      <td className="py-1.5 pr-3 text-gray-300">₹{INR(l.entry_price)}</td>
                      <td className="py-1.5 pr-3 text-emerald-400/80">₹{INR(l.target_price)}</td>
                      <td className="py-1.5 pr-3 font-medium text-gray-100">{l.ltp ? `₹${INR(l.ltp)}` : '—'}</td>
                      <td className="py-1.5 pr-3">{l.leg2_armed ? <span className="text-amber-400">armed @₹{INR(l.leg2_level)}{l.broke_out ? ' · rode' : ''}</span> : <span className="text-gray-600">—</span>}</td>
                      <td className="py-1.5 pr-3"><span className={`px-1.5 py-0.5 rounded text-[11px] ${st.bg} ${st.text}`}>{st.label}</span></td>
                      <td className={`py-1.5 pr-3 font-medium ${live >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{live >= 0 ? '+' : ''}₹{INR(live, 0)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Trade history */}
      <Card title={`Trade History (${history.length})`} icon={Activity}
        right={<button onClick={() => { setHistoryOpen((o) => !o); if (!historyOpen) fetchHistory(); }} className="text-gray-500 hover:text-gray-300">{historyOpen ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}</button>}>
        {historyOpen && (history.length === 0 ? (
          <p className="text-gray-500 text-sm py-4 text-center">No closed trades yet.</p>
        ) : (
          <div className="overflow-x-auto max-h-96 overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-surface-2">
                <tr className="border-b border-surface-3 text-gray-500">
                  {['Entry', 'Exit', 'Held', 'Dir', 'Expiry', 'Strike', 'Option', 'Buy', 'Target', 'Sell', 'P&L', 'Reason', 'Mode'].map((h) => (
                    <th key={h} className="text-left font-medium pb-2 pr-3 whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {history.map((t, i) => {
                  const st = LEG_STYLE[t.exit_reason] || LEG_STYLE.OPEN;
                  return (
                    <tr key={i} className="border-b border-surface-3/30 hover:bg-surface-3/20">
                      <td className="py-1.5 pr-3 text-gray-300 whitespace-nowrap">{t.date} <span className="text-gray-500">{t.entry_time}</span></td>
                      <td className="py-1.5 pr-3 text-gray-300 whitespace-nowrap">{t.exit_date && t.exit_date !== t.date && <span className="text-amber-400">{t.exit_date} </span>}<span className="text-gray-500">{t.exit_time}</span></td>
                      <td className="py-1.5 pr-3 text-gray-400 text-center">{t.held_days ?? 0}</td>
                      <td className="py-1.5 pr-3"><span className={t.direction === 'CALL' ? 'text-emerald-400' : 'text-red-400'}>{t.direction}</span></td>
                      <td className="py-1.5 pr-3 text-gray-400">{t.expiry} <span className="text-gray-600">({t.expiry_type})</span></td>
                      <td className="py-1.5 pr-3 text-gray-300">{t.strike}</td>
                      <td className="py-1.5 pr-3 text-gray-400 font-mono text-[11px]">{t.symbol}</td>
                      <td className="py-1.5 pr-3 text-gray-300">₹{INR(t.premium_buy)}</td>
                      <td className="py-1.5 pr-3 text-emerald-400/80">₹{INR(t.target_premium)}</td>
                      <td className="py-1.5 pr-3 text-gray-300">₹{INR(t.premium_sell)}</td>
                      <td className={`py-1.5 pr-3 font-medium ${(t.pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{(t.pnl ?? 0) >= 0 ? '+' : ''}₹{INR(t.pnl, 0)}</td>
                      <td className="py-1.5 pr-3"><span className={`${st.text}`}>{t.exit_reason}</span></td>
                      <td className="py-1.5 pr-3 text-gray-500">{t.paper ? 'paper' : 'live'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ))}
      </Card>

      {/* How it works */}
      <Card title="How It Works" icon={FlaskConical}>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs text-gray-400">
          <div className="bg-surface-3/30 rounded-lg p-3">
            <div className="text-gray-200 font-semibold mb-1.5">1 · Daily entry</div>
            <p>Each day the app watches NIFTY VWAP vs the previous day&apos;s VWAP. On the <strong>first crossover</strong> (09:30–15:15) it buys one CALL + one PUT (NRML) at the configured {config.expiry_type} {config.strike_mode} strikes, and places a <strong>GTT target</strong> for each.</p>
          </div>
          <div className="bg-surface-3/30 rounded-lg p-3">
            <div className="text-gray-200 font-semibold mb-1.5">2 · Hold to target</div>
            <p>No stop-loss. Each leg is held across days until its target GTT fires. Positions carry overnight — just log into Zerodha &amp; keep the app running each day so it can manage them.</p>
          </div>
          <div className="bg-surface-3/30 rounded-lg p-3">
            <div className="text-gray-200 font-semibold mb-1.5">3 · Leg-2 control &amp; expiry</div>
            <p>When one leg books target, the other is managed (ride above entry, else cut at the configured level — fraction via GTT, points/percent live). Anything still open is squared off at <strong>15:15 on the expiry day</strong>.</p>
          </div>
        </div>
        <p className="text-gray-600 text-[11px] mt-3">
          <strong>Daily login required:</strong> Zerodha issues a fresh access token each morning, so log in and keep the app
          running on trading days for entries &amp; live leg-2 management. The target/fraction GTTs rest at Zerodha and fire
          even if the app is offline; on next start the strategy reconciles what filled while away.
        </p>
      </Card>
    </div>
  );
}
