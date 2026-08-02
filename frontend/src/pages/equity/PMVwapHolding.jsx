import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Play, Pause, Loader2, AlertCircle, RefreshCw, Save, Trash2, Radio, Info,
  TrendingUp, ListChecks, ShieldAlert, CheckCircle2,
} from 'lucide-react';
import { api } from '../../api';
import WatchlistBar from '../../components/WatchlistBar';

const selCls = 'bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';
const lbl = 'block text-[10px] text-gray-500 uppercase tracking-wide mb-1';
const TIMEFRAMES = [['5m', '5 Min'], ['15m', '15 Min'], ['30m', '30 Min'], ['1h', '1 Hour'], ['1d', '1 Day']];
const INT = (v) => (v == null ? '—' : Number(v).toLocaleString('en-IN'));
const NUM = (v, d = 2) => (v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));

function Stat({ label, value, tone }) {
  const c = tone === 'up' ? 'text-emerald-400' : tone === 'down' ? 'text-red-400' : 'text-gray-100';
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl px-3 py-2">
      <div className="text-[10px] text-gray-500 uppercase tracking-wide">{label}</div>
      <div className={`text-lg font-bold ${c}`}>{value}</div>
    </div>
  );
}

const selToSymbols = (sel, universe) => {
  if (!sel) return [];
  if (sel.mode === 'single') return sel.symbol ? [sel.symbol] : [];
  if (sel.mode === 'watchlist') return sel.symbols || [];
  return (universe || []).map((u) => (typeof u === 'string' ? u : u.name));
};

export default function PMVwapHolding() {
  const [st, setSt] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [universe, setUniverse] = useState([]);
  const [sel, setSel] = useState({ mode: 'watchlist', symbol: null, symbols: null });
  const [tab, setTab] = useState('strategy');
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const timer = useRef(null);

  const showErr = (m) => { setErr(m); setTimeout(() => setErr(''), 6000); };
  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(''), 2500); };
  const patch = (k, v) => setCfg((c) => ({ ...c, [k]: v }));

  const refresh = useCallback(async () => {
    try {
      const r = await api.eqHoldStatus();
      setSt(r);
      setCfg((c) => c || r.config);
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    refresh();
    api.researchPMVwapEquityUniverse().then((r) => { if (r.status === 'ok') setUniverse(r.stocks || []); }).catch(() => {});
    timer.current = setInterval(refresh, 5000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [refresh]);

  const symbols = selToSymbols(sel, universe);

  const saveConfig = async () => {
    setBusy('save');
    try {
      const r = await api.eqHoldUpdateConfig({ ...cfg, symbols });
      if (r.status === 'ok') { setCfg(r.config); flash('Config saved'); } else showErr(r.message || 'Save failed');
    } catch (e) { showErr(e.message); } finally { setBusy(''); }
  };

  const start = async () => {
    if (!symbols.length) { showErr('Pick a watchlist / stock to trade first'); return; }
    if (!cfg.paper_trade) {
      const ok = window.confirm('LIVE mode will place REAL delivery (CNC) orders with real money. Continue?');
      if (!ok) return;
    }
    setBusy('start');
    try {
      const r = await api.eqHoldStart({ ...cfg, symbols });
      if (r.status === 'ok') { setSt(r); flash('Strategy started'); } else showErr(r.message || 'Start failed');
    } catch (e) { showErr(e.message); } finally { setBusy(''); }
  };
  const stop = async () => { setBusy('stop'); try { const r = await api.eqHoldStop(); if (r.status === 'ok') setSt(r); } catch (e) { showErr(e.message); } finally { setBusy(''); } };
  const checkNow = async () => { setBusy('check'); try { const r = await api.eqHoldCheck(); if (r.status === 'ok') { setSt(r); flash('Checked'); } else showErr(r.message); } catch (e) { showErr(e.message); } finally { setBusy(''); } };
  const reset = async () => { if (!window.confirm('Clear all logged positions for this strategy? (does not touch broker holdings/GTTs)')) return; setBusy('reset'); try { const r = await api.eqHoldReset(); if (r.status === 'ok') setSt(r); } catch (e) { showErr(e.message); } finally { setBusy(''); } };

  if (!cfg || !st) return <div className="p-6 text-gray-500 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>;

  const num = (k, min, step = 1) => (
    <input type="number" min={min} step={step} value={cfg[k] ?? ''} onChange={(e) => patch(k, e.target.value === '' ? 0 : parseFloat(e.target.value))} className={`w-full ${selCls}`} />
  );
  const live = !cfg.paper_trade;
  const open = st.positions || [];
  const closed = st.closed || [];

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-[1500px] mx-auto">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <TrendingUp className="w-5 h-5 text-brand-400" />
            <h1 className="text-xl font-bold text-gray-100 tracking-wide">Prev-Month VWAP Equity Holding</h1>
            <span className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${st.is_active ? 'bg-emerald-600/15 text-emerald-400 border-emerald-500/30' : 'bg-surface-3 text-gray-400 border-surface-4'}`}>
              {st.is_active ? 'RUNNING' : 'STOPPED'}
            </span>
            <span className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${live ? 'bg-red-600/15 text-red-400 border-red-500/30' : 'bg-amber-500/15 text-amber-400 border-amber-500/30'}`}>
              {live ? 'LIVE · REAL ORDERS' : 'PAPER'}
            </span>
          </div>
          <p className="text-gray-500 text-sm mt-0.5">Swing delivery (CNC): buy when price meets Prev-Month VWAP with Prev-Week VWAP above; exit via GTT target/stop, max-hold or VWAP re-cross.</p>
        </div>
        <div className="flex items-center gap-2">
          {st.is_active ? (
            <button onClick={stop} disabled={busy} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-red-600 hover:bg-red-700 text-white font-semibold disabled:opacity-50">
              {busy === 'stop' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Pause className="w-4 h-4" />} Stop
            </button>
          ) : (
            <button onClick={start} disabled={busy} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white font-semibold disabled:opacity-50">
              {busy === 'start' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />} Start
            </button>
          )}
          <button onClick={checkNow} disabled={busy} title="Evaluate now" className="p-2 rounded-lg bg-surface-3 border border-surface-4 text-gray-300 hover:text-white"><RefreshCw className={`w-4 h-4 ${busy === 'check' ? 'animate-spin' : ''}`} /></button>
        </div>
      </div>

      {err && <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-red-400 text-sm"><AlertCircle className="w-4 h-4" /> {err}</div>}
      {msg && <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-4 py-2 text-emerald-400 text-sm"><CheckCircle2 className="w-4 h-4" /> {msg}</div>}
      {live && (
        <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-red-300 text-sm">
          <ShieldAlert className="w-4 h-4" /> LIVE mode places real CNC delivery orders. Test in Paper mode first. The risk-fence and your capital limits still apply.
        </div>
      )}

      {/* Live stat row */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
        <Stat label="Open Holdings" value={INT(st.open_count)} />
        <Stat label="Open MTM" value={NUM(st.open_mtm)} tone={st.open_mtm >= 0 ? 'up' : 'down'} />
        <Stat label="Realised P&L" value={NUM(st.realised_pnl)} tone={st.realised_pnl >= 0 ? 'up' : 'down'} />
        <Stat label="Capital Deployed" value={`₹${NUM(st.capital_deployed, 0)}`} />
        <Stat label="Closed" value={INT(st.closed_count)} />
        <Stat label="Last Check" value={st.last_check || '—'} />
      </div>
      {st.last_error && <div className="text-xs text-red-400">Last error: {st.last_error}</div>}

      {/* Tabs */}
      <div className="flex gap-1 border-b border-surface-3">
        {[['strategy', 'Strategy', TrendingUp], ['positions', `Positions (${open.length})`, ListChecks], ['info', 'Info', Info]].map(([id, label, Icon]) => (
          <button key={id} onClick={() => setTab(id)} className={`flex items-center gap-1.5 px-4 py-2 text-sm font-semibold border-b-2 -mb-px transition ${tab === id ? 'border-brand-500 text-brand-400' : 'border-transparent text-gray-400 hover:text-gray-200'}`}>
            <Icon className="w-4 h-4" /> {label}
          </button>
        ))}
      </div>

      {tab === 'strategy' && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
          {/* Universe + mode */}
          <div className="flex flex-wrap items-center gap-2">
            <WatchlistBar universe={universe} count={universe.length} onChange={setSel} />
            <span className="text-xs text-gray-500">{symbols.length} stock(s) selected</span>
          </div>

          {/* Paper / Live + core config */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 pt-2 border-t border-surface-3 items-end">
            <label className="flex items-center gap-2 text-xs cursor-pointer">
              <input type="checkbox" checked={cfg.paper_trade} onChange={(e) => patch('paper_trade', e.target.checked)} className="accent-brand-500" />
              <span className={cfg.paper_trade ? 'text-amber-400 font-semibold' : 'text-red-400 font-semibold'}>{cfg.paper_trade ? 'Paper mode' : 'LIVE (real orders)'}</span>
            </label>
            <div><label className={lbl}>Timeframe</label>
              <select value={cfg.timeframe} onChange={(e) => patch('timeframe', e.target.value)} className={`w-full ${selCls}`}>{TIMEFRAMES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
            </div>
            <div><label className={lbl}>Entry Mode</label>
              <select value={cfg.entry_mode} onChange={(e) => patch('entry_mode', e.target.value)} className={`w-full ${selCls}`}><option value="cross_up">Cross-up</option><option value="touch">Touch</option></select>
            </div>
            <div><label className={lbl}>Target %</label>{num('target_pct', 0, 0.5)}</div>
            <div><label className={lbl}>Stop % (0=off)</label>{num('stop_pct', 0, 0.5)}</div>
            <div><label className={lbl}>Max Hold Days</label>{num('max_hold_days', 1)}</div>
            <div><label className={lbl}>VWAP Buffer</label>{num('vwap_buffer', 0, 0.05)}</div>
            <div><label className={lbl}>Entry Start</label><input value={cfg.entry_start} onChange={(e) => patch('entry_start', e.target.value)} className={`w-full ${selCls}`} /></div>
            <div><label className={lbl}>Signal Cutoff</label><input value={cfg.signal_cutoff} onChange={(e) => patch('signal_cutoff', e.target.value)} className={`w-full ${selCls}`} /></div>
            <div><label className={lbl}>History Days</label>{num('history_days', 35)}</div>
            <div><label className={lbl}>Pool Capital</label>{num('portfolio_capital', 0, 10000)}</div>
            <div><label className={lbl}>Max Open Positions</label>{num('max_open_positions', 1)}</div>
            <div><label className={lbl}>Capital / Trade (pool off)</label>{num('capital_per_trade', 0, 1000)}</div>
            <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer"><input type="checkbox" checked={cfg.portfolio_mode} onChange={(e) => patch('portfolio_mode', e.target.checked)} className="accent-brand-500" /> Portfolio pool sizing</label>
            <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer"><input type="checkbox" checked={cfg.require_pw_above_pm} onChange={(e) => patch('require_pw_above_pm', e.target.checked)} className="accent-brand-500" /> Green above purple</label>
            <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer"><input type="checkbox" checked={cfg.one_signal_per_day} onChange={(e) => patch('one_signal_per_day', e.target.checked)} className="accent-brand-500" /> One signal/day</label>
            <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer" title="On: buy even if already held. Off: skip stocks already in the strategy or your demat holdings."><input type="checkbox" checked={cfg.allow_reentry} onChange={(e) => patch('allow_reentry', e.target.checked)} className="accent-brand-500" /> Allow re-entry</label>
            <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer"><input type="checkbox" checked={cfg.exit_on_vwap_cross} onChange={(e) => patch('exit_on_vwap_cross', e.target.checked)} className="accent-brand-500" /> Exit on VWAP re-cross</label>
          </div>
          <div className="flex items-center gap-2 pt-2 border-t border-surface-3">
            <button onClick={saveConfig} disabled={busy} className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white">
              {busy === 'save' ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />} Save config
            </button>
            <button onClick={reset} disabled={busy} className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border bg-red-500/10 text-red-400 border-red-500/30 hover:bg-red-500/20 ml-auto">
              <Trash2 className="w-3.5 h-3.5" /> Reset positions log
            </button>
          </div>
        </div>
      )}

      {tab === 'positions' && (
        <div className="space-y-4">
          <PosTable title="Open Holdings" rows={open} kind="open" />
          <PosTable title="Closed" rows={closed} kind="closed" />
        </div>
      )}

      {tab === 'info' && <InfoPanel />}
    </div>
  );
}

function PosTable({ title, rows, kind }) {
  const NUM2 = (v) => (v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const cols = kind === 'open'
    ? ['Date', 'Time', 'Stock', 'Qty', 'Entry', 'LTP', 'Target', 'Stop', 'MTM', 'Hold(d)', 'Prev-M VWAP', 'Mode']
    : ['Date', 'Time', 'Stock', 'Qty', 'Entry', 'Exit', 'Exit Date', 'Reason', 'Hold(d)', 'P&L', 'Mode'];
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
      <div className="px-3 py-2 text-sm font-semibold text-gray-200 border-b border-surface-3">{title} <span className="text-gray-500">({rows.length})</span></div>
      {rows.length === 0 ? (
        <div className="px-4 py-8 text-center text-gray-500 text-sm">None.</div>
      ) : (
        <div className="overflow-auto max-h-[420px]">
          <table className="w-full text-xs whitespace-nowrap">
            <thead className="sticky top-0 z-10"><tr className="text-gray-300 bg-surface-3">{cols.map((c) => <th key={c} className="px-2.5 py-2 font-semibold text-center border-r border-surface-2 last:border-r-0">{c}</th>)}</tr></thead>
            <tbody>
              {rows.map((r, i) => kind === 'open' ? (
                <tr key={i} className="border-b border-surface-3/40 text-center hover:bg-surface-3/10">
                  <td className="px-2.5 py-1 text-gray-400">{r.trade_date}</td>
                  <td className="px-2.5 py-1 text-gray-200 font-semibold">{r.entry_time}</td>
                  <td className="px-2.5 py-1 text-brand-300 font-semibold">{r.underlying}</td>
                  <td className="px-2.5 py-1 text-gray-300">{r.qty}</td>
                  <td className="px-2.5 py-1 text-gray-200">{NUM2(r.entry_price)}</td>
                  <td className="px-2.5 py-1 text-gray-200">{NUM2(r.ltp)}</td>
                  <td className="px-2.5 py-1 text-amber-300">{NUM2(r.target_price)}</td>
                  <td className="px-2.5 py-1 text-gray-500">{r.stop_price == null ? '—' : NUM2(r.stop_price)}</td>
                  <td className={`px-2.5 py-1 font-semibold ${r.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{NUM2(r.pnl)}</td>
                  <td className="px-2.5 py-1 text-gray-400">{r.hold_days}</td>
                  <td className="px-2.5 py-1 text-purple-300">{NUM2(r.prev_month_vwap)}</td>
                  <td className="px-2.5 py-1 text-gray-500">{r.paper ? 'Paper' : 'Live'}</td>
                </tr>
              ) : (
                <tr key={i} className="border-b border-surface-3/40 text-center hover:bg-surface-3/10">
                  <td className="px-2.5 py-1 text-gray-400">{r.trade_date}</td>
                  <td className="px-2.5 py-1 text-gray-200 font-semibold">{r.entry_time}</td>
                  <td className="px-2.5 py-1 text-brand-300 font-semibold">{r.underlying}</td>
                  <td className="px-2.5 py-1 text-gray-300">{r.qty}</td>
                  <td className="px-2.5 py-1 text-gray-200">{NUM2(r.entry_price)}</td>
                  <td className="px-2.5 py-1 text-gray-200">{NUM2(r.exit_price)}</td>
                  <td className="px-2.5 py-1 text-gray-400">{r.exit_date || '—'}</td>
                  <td className="px-2.5 py-1 text-gray-400">{r.exit_reason}</td>
                  <td className="px-2.5 py-1 text-gray-400">{r.hold_days}</td>
                  <td className={`px-2.5 py-1 font-semibold ${r.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{NUM2(r.pnl)}</td>
                  <td className="px-2.5 py-1 text-gray-500">{r.paper ? 'Paper' : 'Live'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function InfoPanel() {
  const Section = ({ title, children }) => (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
      <div className="text-sm font-semibold text-brand-300 mb-2">{title}</div>
      <div className="text-sm text-gray-400 space-y-1.5 leading-relaxed">{children}</div>
    </div>
  );
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <Section title="What this strategy does">
        <p>A live port of Research #8. It buys equities as a <strong>swing / delivery (CNC)</strong> holding when price meets the <strong>Previous-Month VWAP (purple)</strong> while the <strong>Previous-Week VWAP (green)</strong> is above it — the "green above purple" setup. The signal maths is identical to the research module.</p>
      </Section>
      <Section title="How it runs (no cron)">
        <p>You log in to Zerodha each morning as usual. While the app is up and you're logged in, the built-in loop evaluates the strategy during market hours (9:15–15:30) and manages exits — no scheduled job needed. Positions persist in the database and are reloaded on restart, so they carry across days and the daily token refresh.</p>
      </Section>
      <Section title="Entry">
        <p>• On each completed candle of your timeframe, for every configured stock not already held.</p>
        <p>• Condition: price meets Prev-Month VWAP (Cross-up or Touch) with Prev-Week VWAP above it.</p>
        <p>• Sizing: portfolio pool (pool ÷ max open positions) or fixed Capital/Trade. Respects Max Open Positions.</p>
      </Section>
      <Section title="Exit">
        <p>• <strong>Target</strong> (and optional <strong>Stop</strong>) are placed as server-side <strong>GTT</strong> orders — they fire even if the app is offline.</p>
        <p>• <strong>Max-hold-days</strong>: squared off at market when the holding ages out.</p>
        <p>• <strong>VWAP re-cross</strong> (optional): exit when price closes back below the Prev-Month VWAP.</p>
      </Section>
      <Section title="Paper vs Live">
        <p>• <strong>Paper</strong> (default): fully simulates fills/exits from live prices — no real orders. Use this to forward-test first.</p>
        <p>• <strong>Live</strong>: places real CNC delivery orders + GTTs. The existing risk-fence (day-loss / P&L lock) still blocks orders when tripped.</p>
      </Section>
      <Section title="Going live — checklist">
        <p>1. Pick a curated watchlist (not all 208).<br />2. Run in Paper for a few sessions; compare to the research backtest.<br />3. Set Pool Capital + Max Open Positions to your real limits.<br />4. Uncheck Paper, Save, then Start — confirm the LIVE prompt.<br />5. Watch the Positions tab; GTTs also appear in your Zerodha orders.</p>
      </Section>
    </div>
  );
}
