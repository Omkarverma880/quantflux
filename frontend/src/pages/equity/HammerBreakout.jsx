import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Hammer, Play, Loader2, AlertCircle, Download, Info, TrendingUp, Check, X,
  FlaskConical, Wallet, Radio, RefreshCw, Save, Search, Send, Target,
} from 'lucide-react';
import { api } from '../../api';
import WatchlistBar from '../../components/WatchlistBar';

const sel = 'bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';
const lbl = 'block text-[11px] text-gray-500 uppercase tracking-wide mb-1';
const NUM = (v, d = 2) => (v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
const INT = (v) => (v == null ? '—' : Number(v).toLocaleString('en-IN'));
const GREEN = '#10b981'; const RED = '#ef4444';

const stColor = (s) => ({ TARGET: 'text-emerald-400', STOP: 'text-red-400', OPEN: 'text-amber-400', SQUAREOFF: 'text-gray-400' }[s] || 'text-gray-400');
const sortRows = (arr, s) => {
  if (!s || !s.key) return arr;
  const d = s.dir === 'asc' ? 1 : -1;
  const norm = (v) => (v == null || v === '' ? null : v);
  return [...arr].sort((a, b) => {
    const av = norm(a[s.key]); const bv = norm(b[s.key]);
    if (av == null && bv == null) return 0; if (av == null) return 1; if (bv == null) return -1;
    if (typeof av === 'string' && typeof bv === 'string') return av < bv ? -d : av > bv ? d : 0;
    return (Number(av) - Number(bv)) * d;
  });
};
const nextSort = (s, k) => (s.key === k ? { key: k, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key: k, dir: 'desc' });
const EVERY_PRESETS = [[15, '15 sec'], [30, '30 sec'], [60, '1 min'], [300, '5 min'],
  [900, '15 min'], [1800, '30 min'], [3600, '1 hour']];
const everyLabel = (n) => (n % 3600 === 0 ? `${n / 3600} hour` : n % 60 === 0 ? `${n / 60} min` : `${n} sec`);
function SortTh({ label, k, align, sort, onSort }) {
  const active = sort.key === k;
  return <th onClick={() => onSort(k)} className={`px-2.5 py-2 font-semibold cursor-pointer select-none ${align === 'l' ? 'text-left' : 'text-right'} ${active ? 'text-brand-300' : ''}`}>{label}{active ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}</th>;
}

const BT_COLS = [['Entry Date', 'date', 'l'], ['Stock', 'underlying', 'l'], ['Hammer On', 'signal_date', 'l'],
  ['Wick %', 'lower_wick_pct', 'r'], ['Body %', 'body_pct', 'r'], ['Up Wick %', 'upper_wick_pct', 'r'],
  ['Trigger', 'sig_high', 'r'], ['Hammer Low', 'sig_low', 'r'], ['Qty', 'qty', 'r'], ['Entry', 'entry', 'r'],
  ['Target', 'target', 'r'], ['SL', 'sl', 'r'], ['Exit', 'exit', 'r'], ['Exit Date', 'exit_date', 'r'],
  ['Reason', 'status', 'r'], ['MTM', 'mtm', 'r'], ['Max Profit', 'max_profit', 'r'], ['Max Loss', 'max_loss', 'r'],
  ['Hold', 'hold_days', 'r']];
const POS_COLS = [['Stock', 'underlying', 'l'], ['Entry Date', 'date', 'l'], ['Hammer On', 'signal_date', 'l'],
  ['Trigger', 'trigger', 'r'], ['Entry@', 'entry_time', 'r'], ['Entry', 'entry_price', 'r'], ['Target', 'target', 'r'],
  ['SL', 'sl', 'r'], ['LTP', 'ltp', 'r'], ['MTM', 'mtm', 'r'], ['Max Profit', 'mfe', 'r'], ['Max Loss', 'mae', 'r'],
  ['Held', 'hold_days', 'r'], ['Status', 'status', 'r']];

export default function HammerBreakout() {
  const [cfg, setCfg] = useState(null);
  const [tab, setTab] = useState('backtest');
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const showErr = (m) => { setErr(m); setTimeout(() => setErr(''), 6000); };
  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(''), 2500); };
  const patch = (k, v) => setCfg((c) => ({ ...c, [k]: v }));

  const [universe, setUniverse] = useState([]);
  const [sel2, setSel2] = useState({ mode: 'all', symbol: null, symbols: null });
  const [start, setStart] = useState(''); const [end, setEnd] = useState('');
  const [data, setData] = useState(null); const [loading, setLoading] = useState(false);
  const [showNonTrades, setShowNonTrades] = useState(false);
  const [applyCaps, setApplyCaps] = useState(true);
  const [btSort, setBtSort] = useState({ key: null, dir: 'desc' });
  const [posSort, setPosSort] = useState({ key: null, dir: 'desc' });

  const [simSym, setSimSym] = useState(''); const [simDate, setSimDate] = useState('');
  const [sim, setSim] = useState(null); const [simLoading, setSimLoading] = useState(false);
  const [simSugg, setSimSugg] = useState([]); const [simSearching, setSimSearching] = useState(false);
  const simTimer = useRef(null);
  useEffect(() => {
    if (simTimer.current) clearTimeout(simTimer.current);
    if (!simSym || simSym.trim().length < 2) { setSimSugg([]); return; }
    setSimSearching(true);
    simTimer.current = setTimeout(async () => {
      try { const r = await api.researchSymbolSearch(simSym.trim()); setSimSugg(r.status === 'ok' ? (r.results || []) : []); }
      catch { setSimSugg([]); } finally { setSimSearching(false); }
    }, 250);
    return () => simTimer.current && clearTimeout(simTimer.current);
  }, [simSym]);

  const [status, setStatus] = useState(null);
  const [symbolsText, setSymbolsText] = useState('');
  const [positions, setPositions] = useState([]);
  const [savedWls, setSavedWls] = useState([]);
  const pollRef = useRef(null);

  // ── Today tab: live setup list ──
  const [today, setToday] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [sending, setSending] = useState(false);
  const [autoTick, setAutoTick] = useState(true);
  const [useStratList, setUseStratList] = useState(false);   // default = the F&O universe
  const [todaySel, setTodaySel] = useState({ mode: 'all', symbol: null, symbols: null });
  const [todaySort, setTodaySort] = useState({ key: null, dir: 'desc' });
  const [customEvery, setCustomEvery] = useState(false);
  const [customVal, setCustomVal] = useState(2);
  const [customUnit, setCustomUnit] = useState(60);
  const ltpRef = useRef(null);

  const symList = () => symbolsText.split(/[\s,;\n]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
  const applySymbols = (syms, append) => setSymbolsText((prev) => {
    if (!append) return syms.join(', ');
    const have = new Set(prev.split(/[\s,;\n]+/).map((s) => s.trim().toUpperCase()).filter(Boolean));
    syms.forEach((s) => have.add(String(s).toUpperCase())); return [...have].join(', ');
  });
  const loadWl = async (id, append = false) => {
    if (!id) return;
    if (id === '__ALL_FNO__') { applySymbols(universe.map((u) => (typeof u === 'string' ? u : u.name)).filter(Boolean), append); return; }
    try { const r = await api.researchWatchlistGet(id); if (r.status === 'ok') applySymbols(r.watchlist.symbols || [], append); } catch { /* */ }
  };

  useEffect(() => {
    api.hbConfig().then((r) => { if (r.status === 'ok') { setCfg(r.config); setSymbolsText((r.config.symbols || []).join(', ')); } }).catch(() => setCfg({}));
    api.researchPMVwapEquityUniverse?.().then((r) => { if (r?.status === 'ok') setUniverse(r.stocks || []); }).catch(() => {});
    api.researchWatchlists?.().then((r) => { if (r?.status === 'ok') setSavedWls(r.watchlists || []); }).catch(() => {});
    const t = new Date(); const y = new Date(); y.setFullYear(t.getFullYear() - 1);
    setEnd(t.toISOString().slice(0, 10)); setStart(y.toISOString().slice(0, 10));
  }, []);

  const runBacktest = useCallback(async () => {
    if (!cfg) return;
    setLoading(true); setErr('');
    try {
      const body = {
        overrides: cfg, start, end, include_non_trades: showNonTrades, apply_caps: applyCaps,
        symbol: sel2.mode === 'single' ? sel2.symbol : null,
        symbols: sel2.mode === 'watchlist' ? sel2.symbols : null,
      };
      const r = await api.hbBacktest(body);
      if (r.status === 'ok') setData(r); else showErr(r.message || 'Backtest failed');
    } catch (e) { showErr(e.message); } finally { setLoading(false); }
  }, [cfg, sel2, start, end, showNonTrades, applyCaps]);

  const runSimulate = async () => {
    if (!simSym.trim()) return showErr('Enter a stock symbol');
    setSimLoading(true); setErr('');
    try {
      const r = await api.hbSimulate({ symbol: simSym.trim().toUpperCase(), overrides: cfg, date: simDate || null });
      if (r.status === 'ok') setSim(r); else showErr(r.message || 'Simulate failed');
    } catch (e) { showErr(e.message); } finally { setSimLoading(false); }
  };

  const todaySymbols = useCallback(() => {
    if (useStratList) return symList();
    if (todaySel.mode === 'single') return todaySel.symbol ? [todaySel.symbol] : [];
    if (todaySel.mode === 'watchlist') return todaySel.symbols || [];
    return universe.map((u) => (typeof u === 'string' ? u : u.name)).filter(Boolean);
  }, [useStratList, todaySel, universe, symbolsText]);

  const runScan = useCallback(async () => {
    const syms = todaySymbols();
    if (!syms.length) { showErr('No stocks to scan — add some to the watchlist'); return; }
    setScanning(true); setErr('');
    try {
      const r = await api.hbScan({ symbols: syms, overrides: cfg });
      if (r.status === 'ok') setToday(r); else showErr(r.message || 'Scan failed');
    } catch (e) { showErr(e.message); } finally { setScanning(false); }
  }, [cfg, todaySymbols]);

  // The live tick. Hammers only change at the day boundary, so the server just
  // re-prices the cached setups (one batched quote call) — and, when Telegram
  // alerts are on, pushes every stock that has just taken out its trigger. The
  // dedupe ledger lives on the server, so a stock is announced exactly once a
  // day no matter how many tabs are open.
  const tickRef = useRef(null);
  const runTick = useCallback(async () => {
    const syms = todaySymbols();
    if (!syms.length) return;
    try {
      const call = cfg?.telegram_alerts ? api.hbTodayAutoAlert : api.hbTodayRefresh;
      const r = await call({ symbols: syms, overrides: cfg });
      if (r.status !== 'ok') return;
      setToday(r);
      if (r.sent) flash(`${r.sent} breakout(s) sent to Telegram: ${(r.alerted || []).join(', ')}`);
    } catch { /* keep the last good snapshot */ }
  }, [cfg, todaySymbols]);
  const tickFn = useRef(runTick);
  useEffect(() => { tickFn.current = runTick; }, [runTick]);

  const everySecs = Math.max(5, Number(cfg?.today_refresh_secs) || 15);
  const applyEvery = async (n) => {
    const secs = Math.max(5, Math.min(86400, Math.round(Number(n) || 15)));
    setCfg((c) => ({ ...c, today_refresh_secs: secs }));
    try { await api.hbConfigSave({ today_refresh_secs: secs }); } catch { /* stays local */ }
  };
  useEffect(() => {
    if (tab !== 'today' || !autoTick) return undefined;
    tickRef.current = setInterval(() => tickFn.current(), everySecs * 1000);
    return () => clearInterval(tickRef.current);
  }, [tab, autoTick, everySecs]);

  // first visit to the tab → scan once, so the list is there without a click
  const firstScan = useRef(false);
  useEffect(() => {
    if (tab === 'today' && cfg && !firstScan.current) { firstScan.current = true; runScan(); }
  }, [tab, cfg, runScan]);

  const sendTelegram = async () => {
    const syms = todaySymbols();
    if (!syms.length) { showErr('No stocks to scan'); return; }
    setSending(true); setErr('');
    try {
      const r = await api.hbTodayTelegram({ symbols: syms, overrides: cfg });
      if (r.status === 'ok') { setToday(r); flash(`Sent ${r.sent} setup(s) to Telegram`); }
      else showErr(r.message || 'Telegram send failed');
    } catch (e) { showErr(e.message); } finally { setSending(false); }
  };

  const saveCfg = async () => {
    const r = await api.hbConfigSave({ ...cfg, symbols: symList() });
    if (r.status === 'ok') { setCfg(r.config); flash('Config saved'); } else showErr(r.message);
  };

  const loadStatus = useCallback(async () => { try { const r = await api.hbStatus(); if (r.status === 'ok') setStatus(r); } catch { /* */ } }, []);
  const loadPositions = useCallback(async () => { try { const r = await api.hbPositions(); if (r.status === 'ok') setPositions(r.positions || []); } catch { /* */ } }, []);
  useEffect(() => { if (tab === 'positions') { loadStatus(); loadPositions(); pollRef.current = setInterval(() => { loadStatus(); loadPositions(); }, 5000); return () => clearInterval(pollRef.current); } }, [tab, loadStatus, loadPositions]);

  const startStrat = async () => {
    const r = await api.hbStart({ ...cfg, symbols: symList() });
    if (r.status === 'ok') { setStatus(r); flash('Strategy started'); loadPositions(); } else showErr(r.message);
  };
  const stopStrat = async () => { const r = await api.hbStop(); if (r.status === 'ok') { setStatus(r); flash('Stopped'); } else showErr(r.message); };
  const saveStratCfg = async () => {
    const r = await api.hbUpdateConfig({ ...cfg, symbols: symList() });
    if (r.status === 'ok') flash('Saved'); else showErr(r.message);
  };

  const rows = data?.rows || [];
  const downloadCSV = () => {
    const cols = ['date', 'underlying', 'signal_date', 'lower_wick_pct', 'body_pct', 'upper_wick_pct', 'lowest_low',
      'sig_high', 'sig_low', 'qty', 'entry', 'target', 'sl', 'exit', 'exit_date', 'exit_reason', 'mtm', 'cost',
      'max_profit', 'max_loss', 'hold_days', 'product', 'status', 'notes'];
    const esc = (v) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const lines = [cols.join(',')].concat(rows.map((r) => cols.map((c) => esc(r[c])).join(',')));
    const blob = new Blob([lines.join('\n') + '\n'], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'hammer_breakout_backtest.csv'; a.click();
  };
  const downloadPositionsCSV = () => {
    if (!positions.length) return;
    const cols = ['date', 'signal_date', 'underlying', 'qty', 'trigger', 'signal_low', 'entry_time', 'entry_price',
      'target', 'sl', 'ltp', 'mtm', 'mfe', 'mae', 'status', 'exit_date', 'exit_time', 'exit_price', 'exit_reason', 'paper', 'hold_days'];
    const esc = (v) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const lines = [cols.join(',')].concat(positions.map((r) => cols.map((c) => esc(r[c])).join(',')));
    const blob = new Blob([lines.join('\n') + '\n'], { type: 'text/csv;charset=utf-8' });
    const mode = positions.every((p) => p.paper) ? 'paper' : positions.some((p) => p.paper) ? 'mixed' : 'live';
    const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-');
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `hammer_breakout_positions_${mode}_${stamp}.csv`; a.click();
  };

  if (!cfg) return <div className="p-6 text-gray-500 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>;
  const num = (k, min = 0, step = 1) => <input type="number" min={min} step={step} value={cfg[k] ?? ''} onChange={(e) => patch(k, e.target.value)} className={`w-full ${sel}`} />;

  const ConfigGrid = () => (
    <div className="space-y-3">
      <div>
        <div className="text-[11px] uppercase tracking-wide text-gray-500 mb-1.5">The hammer (previous daily candle)</div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          <div><label className={lbl}>Min Lower Wick %</label>{num('lower_wick_min', 0, 0.1)}</div>
          <div><label className={lbl}>Max Body %</label>{num('body_max', 0, 0.1)}</div>
          <div><label className={lbl}>Max Upper Wick %</label>{num('upper_wick_max', 0, 0.1)}</div>
          <div>
            <label className={lbl}>Low Lookback (bars)</label>
            <select value={cfg.low_lookback} onChange={(e) => patch('low_lookback', e.target.value)} className={`w-full ${sel}`}>
              <option value={63}>63 — 3 months</option>
              <option value={126}>126 — 6 months</option>
              <option value={252}>252 — 12 months</option>
              {![63, 126, 252].includes(Number(cfg.low_lookback)) && <option value={cfg.low_lookback}>{cfg.low_lookback} bars</option>}
            </select>
          </div>
          <div><label className={lbl}>Red Candles Before</label>{num('red_before', 0, 1)}</div>
        </div>
      </div>
      <div>
        <div className="text-[11px] uppercase tracking-wide text-gray-500 mb-1.5">Trade management</div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          <div><label className={lbl}>Target By</label><select value={cfg.target_mode} onChange={(e) => patch('target_mode', e.target.value)} className={`w-full ${sel}`}><option value="percent">Percent</option><option value="points">Points</option></select></div>
          <div><label className={lbl}>Target {cfg.target_mode === 'points' ? '(pts)' : '%'}</label>{num('target_value', 0, 0.5)}</div>
          <div><label className={lbl}>SL By</label><select value={cfg.sl_mode} onChange={(e) => patch('sl_mode', e.target.value)} className={`w-full ${sel}`}><option value="signal_low">Hammer Low</option><option value="percent">Percent</option><option value="points">Points</option></select></div>
          {cfg.sl_mode !== 'signal_low' && <div><label className={lbl}>SL {cfg.sl_mode === 'points' ? '(pts)' : '%'}</label>{num('sl_value', 0, 0.5)}</div>}
          <div><label className={lbl}>Product</label><select value={cfg.product} onChange={(e) => patch('product', e.target.value)} className={`w-full ${sel}`}><option value="CNC">CNC (delivery)</option><option value="MIS">MIS (intraday)</option></select></div>
          <div><label className={lbl}>Max Hold (days)</label>{num('max_hold_days', 1, 1)}</div>
          <div><label className={lbl}>Entry Cutoff</label><input value={cfg.entry_cutoff} onChange={(e) => patch('entry_cutoff', e.target.value)} className={`w-full ${sel}`} /></div>
          <div><label className={lbl}>Square-off</label><input value={cfg.square_off} onChange={(e) => patch('square_off', e.target.value)} className={`w-full ${sel}`} /></div>
          <div><label className={lbl}>Qty By</label><select value={cfg.qty_mode} onChange={(e) => patch('qty_mode', e.target.value)} className={`w-full ${sel}`}><option value="capital">Capital</option><option value="fixed">Fixed Qty</option></select></div>
          {cfg.qty_mode === 'capital'
            ? <div><label className={lbl}>Capital / Trade ₹</label>{num('capital_per_trade', 0, 1000)}</div>
            : <div><label className={lbl}>Fixed Qty</label>{num('fixed_qty', 1, 1)}</div>}
          <div><label className={lbl}>Max Positions</label>{num('max_positions', 1, 1)}</div>
          <div><label className={lbl}>Max Long</label>{num('max_long', 1, 1)}</div>
          <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer self-end"><input type="checkbox" checked={cfg.apply_costs} onChange={(e) => patch('apply_costs', e.target.checked)} className="accent-brand-500" /> Net of costs</label>
        </div>
      </div>
    </div>
  );

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-[1600px] mx-auto">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Hammer className="w-6 h-6 text-brand-400" />
            <h1 className="text-xl font-bold text-gray-100">Hammer at 3/6-Month Low — Breakout</h1>
            <span className="text-[11px] px-2 py-0.5 rounded-full bg-brand-500/15 text-brand-300 border border-brand-500/25">Equity Strategy 4</span>
          </div>
          <p className="text-sm text-gray-500 mt-0.5">Daily positional swing. Yesterday prints a hammer at a {cfg.low_lookback}-bar low after {cfg.red_before} red candles → today trades above its high → <b>BUY</b>.</p>
        </div>
        {status && <div className={`flex items-center gap-1.5 text-xs ${status.is_active ? 'text-emerald-400' : 'text-gray-500'}`}><Radio className={`w-4 h-4 ${status.is_active ? 'animate-pulse' : ''}`} /> {status.is_active ? 'LIVE ON' : 'LIVE OFF'} · {status.paper_trade ? 'Paper' : 'REAL'}</div>}
      </div>

      <div className="flex gap-1 border-b border-surface-3">
        {[['backtest', 'Backtest', FlaskConical], ['simulate', 'Simulate', Hammer], ['positions', 'Positions', Wallet], ['today', "Today's Stocks", Target], ['info', 'Info', Info]].map(([id, label, Icon]) => (
          <button key={id} onClick={() => setTab(id)} className={`flex items-center gap-1.5 px-4 py-2 text-sm font-semibold border-b-2 -mb-px transition ${tab === id ? 'border-brand-500 text-brand-400' : 'border-transparent text-gray-400 hover:text-gray-200'}`}><Icon className="w-4 h-4" /> {label}</button>
        ))}
      </div>

      {err && <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-red-400 text-sm"><AlertCircle className="w-4 h-4" /> {err}</div>}
      {msg && <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-4 py-2 text-emerald-400 text-sm">{msg}</div>}

      {tab === 'backtest' && (
        <div className="space-y-4">
          <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
            <div className="flex flex-wrap items-end gap-3">
              <div className="flex-1 min-w-[280px]"><WatchlistBar universe={universe} count={universe.length} onChange={setSel2} /></div>
              <div><label className={lbl}>From</label><input type="date" value={start} onChange={(e) => setStart(e.target.value)} className={sel} /></div>
              <div><label className={lbl}>To</label><input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className={sel} /></div>
              <button onClick={runBacktest} disabled={loading} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">{loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />} Run Backtest</button>
              <button onClick={saveCfg} className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white"><Save className="w-3.5 h-3.5" /> Save default</button>
              <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer ml-1" title="Show hammers that never broke out, and signals skipped for size/caps."><input type="checkbox" checked={showNonTrades} onChange={(e) => setShowNonTrades(e.target.checked)} className="accent-brand-500" /> Show non-trade setups</label>
              <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer" title="Apply live portfolio caps (Max Positions / Long) so the backtest matches Live/Paper."><input type="checkbox" checked={applyCaps} onChange={(e) => setApplyCaps(e.target.checked)} className="accent-brand-500" /> Apply caps (match live)</label>
            </div>
            {ConfigGrid()}
            <p className="text-[11px] text-gray-600">Daily candles — the setup needs {cfg.low_lookback} bars of history before each signal, so the fetch reaches well past the “From” date.</p>
          </div>

          {data?.note && <div className="text-xs text-amber-300/90 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2">{data.note}</div>}
          {data?.caps_note && <div className="text-xs text-sky-300/90 bg-sky-500/10 border border-sky-500/30 rounded-lg px-3 py-2">{data.caps_note}</div>}
          {data && (
            <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm bg-surface-2 border border-surface-3 rounded-xl px-4 py-2.5">
              <span className="text-gray-400">Trades <strong className="text-gray-100">{data.stats.total}</strong></span>
              <span className="text-gray-400">Win% <strong className="text-emerald-400">{data.stats.win_rate}%</strong></span>
              <span className="text-gray-400">Total MTM <strong className={data.stats.total_mtm >= 0 ? 'text-emerald-400' : 'text-red-400'}>{NUM(data.stats.total_mtm)}</strong></span>
              <span className="text-gray-400">Avg hold <strong className="text-gray-200">{data.stats.avg_hold}d</strong></span>
              <span className="text-gray-400">Still open <strong className="text-amber-400">{data.stats.open}</strong></span>
              <span className="text-gray-400">Best <strong className="text-emerald-400">{NUM(data.stats.best)}</strong> · Worst <strong className="text-red-400">{NUM(data.stats.worst)}</strong></span>
              <span className="text-gray-500 text-xs">{data.stocks_scanned} stocks scanned</span>
              <button onClick={downloadCSV} disabled={!rows.length} className="ml-auto flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-40"><Download className="w-3.5 h-3.5" /> CSV</button>
            </div>
          )}

          {data && (
            <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
              <div className="overflow-x-auto"><table className="w-full text-xs whitespace-nowrap">
                <thead className="bg-surface-3 text-gray-300"><tr>{BT_COLS.map(([label, k, a]) => <SortTh key={k} label={label} k={k} align={a} sort={btSort} onSort={(kk) => setBtSort((s) => nextSort(s, kk))} />)}</tr></thead>
                <tbody>{sortRows(rows, btSort).map((r, i) => (r.qty ? (
                  <tr key={i} className="border-t border-surface-3/40 hover:bg-surface-3/10">
                    <td className="px-2.5 py-1.5 text-left text-gray-400">{r.date}</td>
                    <td className="px-2.5 py-1.5 text-left text-brand-300 font-semibold">{r.underlying}</td>
                    <td className="px-2.5 py-1.5 text-left text-gray-500">{r.signal_date}</td>
                    <td className="px-2.5 py-1.5 text-right text-emerald-400">{NUM(r.lower_wick_pct)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-300">{NUM(r.body_pct)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-300">{NUM(r.upper_wick_pct)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-300">{NUM(r.sig_high)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-400">{NUM(r.sig_low)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-200 font-medium">{INT(r.qty)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-200">₹{NUM(r.entry)}</td>
                    <td className="px-2.5 py-1.5 text-right text-emerald-400">₹{NUM(r.target)}</td>
                    <td className="px-2.5 py-1.5 text-right text-red-400">₹{NUM(r.sl)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-300">{r.exit == null ? '—' : `₹${NUM(r.exit)}`}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-500">{r.exit_date || '—'}</td>
                    <td className={`px-2.5 py-1.5 text-right font-semibold ${stColor(r.exit_reason)}`}>{r.exit_reason}</td>
                    <td className={`px-2.5 py-1.5 text-right font-semibold ${r.mtm >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{NUM(r.mtm)}</td>
                    <td className="px-2.5 py-1.5 text-right text-emerald-400">{NUM(r.max_profit)}</td>
                    <td className="px-2.5 py-1.5 text-right text-red-400">{NUM(r.max_loss)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-400">{r.hold_label ?? (r.open ? 'open' : '—')}</td>
                  </tr>
                ) : (
                  <tr key={i} className="border-t border-surface-3/40 bg-surface-3/10 text-gray-500">
                    <td className="px-2.5 py-1.5 text-left text-gray-500">{r.date}</td>
                    <td className="px-2.5 py-1.5 text-left text-gray-400 font-semibold">{r.underlying}</td>
                    <td className="px-2.5 py-1.5 text-left text-gray-500">{r.signal_date}</td>
                    <td className="px-2.5 py-1.5 text-right">{NUM(r.lower_wick_pct)}</td>
                    <td className="px-2.5 py-1.5 text-right">{NUM(r.body_pct)}</td>
                    <td className="px-2.5 py-1.5 text-right">{NUM(r.upper_wick_pct)}</td>
                    <td className="px-2.5 py-1.5 text-right">{NUM(r.sig_high)}</td>
                    <td className="px-2.5 py-1.5 text-left text-amber-400/80 italic" colSpan={12}>{r.status} — {r.notes}</td>
                  </tr>
                )))}
                {!rows.length && <tr><td colSpan={BT_COLS.length} className="px-4 py-8 text-center text-gray-500">No breakouts in range.{!showNonTrades && ' Tick “Show non-trade setups” to see hammers that never broke out.'}</td></tr>}
                </tbody>
              </table></div>
            </div>
          )}
        </div>
      )}

      {tab === 'simulate' && (
        <div className="space-y-4">
          <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 flex flex-wrap items-end gap-3">
            <div className="relative">
              <label className={lbl}>Stock (search NSE/BSE)</label>
              <div className="flex items-center gap-2 bg-surface-3 border border-surface-4 rounded-lg px-3">
                <input value={simSym} onChange={(e) => setSimSym(e.target.value.toUpperCase())} placeholder="Type e.g. RELIANCE, TCS…" className="w-64 bg-transparent py-1.5 text-sm text-gray-200 focus:outline-none" />
                {simSearching && <Loader2 className="w-4 h-4 animate-spin text-gray-500" />}
              </div>
              {simSugg.length > 0 && (
                <div className="absolute z-20 mt-1 w-full max-h-64 overflow-auto bg-surface-2 border border-surface-3 rounded-lg shadow-2xl">
                  {simSugg.map((s) => (
                    <button key={`${s.symbol}:${s.exchange}`} onClick={() => { setSimSym(s.symbol); setSimSugg([]); }} className="w-full text-left px-3 py-2 hover:bg-surface-3/40 flex items-center gap-2 border-b border-surface-3/40 last:border-0">
                      <span className="text-sm text-gray-100 font-medium">{s.symbol}</span><span className="text-[11px] text-gray-500 ml-auto">{s.exchange}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div><label className={lbl}>Trade Date (the breakout day)</label><input type="date" value={simDate} onChange={(e) => setSimDate(e.target.value)} className={sel} /></div>
            <button onClick={runSimulate} disabled={simLoading} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">{simLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Hammer className="w-4 h-4" />} Check Setup</button>
            <span className="text-[11px] text-gray-600">Checks the previous day's candle against all 5 conditions, then the breakout.</span>
          </div>
          {sim && <SimulateView sim={sim} />}
          {!sim && !simLoading && <div className="bg-surface-2 border border-surface-3 rounded-xl p-10 text-center text-gray-500 text-sm">Pick a stock &amp; date to see each condition pass or fail, the hammer on the daily chart, and the resulting trade.</div>}
        </div>
      )}

      {tab === 'positions' && (
        <div className="space-y-4">
          <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer"><input type="checkbox" checked={cfg.paper_trade} onChange={(e) => patch('paper_trade', e.target.checked)} className="accent-brand-500" /> Paper mode</label>
              <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer"><input type="checkbox" checked={cfg.auto_start} onChange={(e) => patch('auto_start', e.target.checked)} className="accent-brand-500" /> Auto-start on login</label>
              {status && <span className="text-xs text-gray-500">Open {status.open_positions}/{status.max_positions} · Armed today {status.armed_count}</span>}
              <div className="ml-auto flex items-center gap-2">
                {status?.is_active
                  ? <button onClick={stopStrat} className="px-3 py-1.5 text-sm rounded-lg bg-red-600/80 hover:bg-red-600 text-white font-semibold">Stop</button>
                  : <button onClick={startStrat} className="px-3 py-1.5 text-sm rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white font-semibold">Start</button>}
                <button onClick={saveStratCfg} className="px-3 py-1.5 text-sm rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white flex items-center gap-1"><Save className="w-3.5 h-3.5" /> Save</button>
                <button onClick={() => { loadStatus(); loadPositions(); }} className="text-gray-400 hover:text-white"><RefreshCw className="w-4 h-4" /></button>
              </div>
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2 mb-1">
                <label className={`${lbl} !mb-0`}>Watchlist (stocks the strategy scans)</label>
                {(savedWls.length > 0 || universe.length > 0) && (
                  <div className="flex items-center gap-1.5 ml-auto">
                    <select defaultValue="" onChange={(e) => { loadWl(e.target.value, false); e.target.value = ''; }} className={`${sel} py-1 text-xs`}><option value="">Load saved watchlist…</option>{universe.length > 0 && <option value="__ALL_FNO__">All F&O ({universe.length})</option>}{savedWls.map((w) => <option key={w.id} value={w.id}>{w.name} ({w.count})</option>)}</select>
                    <select defaultValue="" onChange={(e) => { loadWl(e.target.value, true); e.target.value = ''; }} className={`${sel} py-1 text-xs`}><option value="">+ Add from…</option>{universe.length > 0 && <option value="__ALL_FNO__">All F&O ({universe.length})</option>}{savedWls.map((w) => <option key={w.id} value={w.id}>{w.name} ({w.count})</option>)}</select>
                    <button onClick={() => setSymbolsText('')} className="text-xs px-2 py-1 rounded-lg border bg-surface-3 text-gray-400 border-surface-4 hover:text-white">Clear</button>
                  </div>
                )}
              </div>
              <textarea value={symbolsText} onChange={(e) => setSymbolsText(e.target.value)} rows={2} placeholder="RELIANCE, TCS, HDFCBANK …" className={`w-full ${sel}`} />
              <div className="text-[11px] text-gray-600 mt-0.5">{symList().length} stocks</div>
            </div>
            {ConfigGrid()}
            <div className="flex flex-wrap items-center gap-4 pt-2 border-t border-surface-3">
              <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer"><input type="checkbox" checked={cfg.telegram_alerts} onChange={(e) => patch('telegram_alerts', e.target.checked)} className="accent-brand-500" /> Telegram alerts</label>
              <div className="flex items-center gap-2 text-xs text-gray-400">Bot<select value={cfg.telegram_bot || 'a'} onChange={(e) => patch('telegram_bot', e.target.value)} className={`${sel} py-1`}><option value="a">Bot A</option><option value="b">Bot B</option></select></div>
            </div>
            {!cfg.paper_trade && <p className="text-[11px] text-amber-400">⚠ REAL mode also needs global PAPER_TRADE=False + TRADING_ENABLED=True. Orders are {cfg.product} BUY on the breakout, sold back on target / SL / max-hold.</p>}
          </div>

          {positions.length > 0 && (() => {
            const s = positions.reduce((a, p) => { a.mtm += p.mtm || 0; a.mfe += p.mfe || 0; a.mae += p.mae || 0; if (p.status === 'OPEN') a.open += 1; else { a.closed += 1; a.realized += p.mtm || 0; } return a; }, { mtm: 0, mfe: 0, mae: 0, open: 0, closed: 0, realized: 0 });
            return (
              <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm bg-surface-2 border border-surface-3 rounded-xl px-4 py-2.5">
                <span className="text-gray-400">Positions <strong className="text-gray-100">{positions.length}</strong> <span className="text-gray-600">({s.open} open · {s.closed} closed)</span></span>
                <span className="text-gray-400">Total MTM <strong className={s.mtm >= 0 ? 'text-emerald-400' : 'text-red-400'}>₹{NUM(s.mtm, 0)}</strong></span>
                <span className="text-gray-400">Realized <strong className={s.realized >= 0 ? 'text-emerald-400' : 'text-red-400'}>₹{NUM(s.realized, 0)}</strong></span>
                <span className="text-gray-400">Σ Max Profit <strong className="text-emerald-400">₹{NUM(s.mfe, 0)}</strong></span>
                <span className="text-gray-400">Σ Max Loss <strong className="text-red-400">₹{NUM(s.mae, 0)}</strong></span>
              </div>
            );
          })()}

          <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
            <div className="px-3 py-2 border-b border-surface-3 flex items-center justify-between">
              <span className="text-sm font-semibold text-gray-200">Positions <span className="text-gray-500">(today + everything still open — swings carry across days)</span></span>
              <button onClick={downloadPositionsCSV} disabled={!positions.length} className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-40"><Download className="w-3.5 h-3.5" /> CSV</button>
            </div>
            {!positions.length ? <div className="px-4 py-8 text-center text-gray-500 text-sm">No positions yet — one opens when an armed stock trades above its trigger while Live is on.</div> : (
              <div className="overflow-x-auto"><table className="w-full text-xs whitespace-nowrap">
                <thead className="bg-surface-3 text-gray-300"><tr>{POS_COLS.map(([label, k, a]) => <SortTh key={k} label={label} k={k} align={a} sort={posSort} onSort={(kk) => setPosSort((s) => nextSort(s, kk))} />)}</tr></thead>
                <tbody>{sortRows(positions, posSort).map((p) => (
                  <tr key={p.id} className="border-t border-surface-3/40">
                    <td className="px-2.5 py-1.5 text-left text-brand-300 font-semibold">{p.underlying}{p.paper && <span className="ml-1 text-[9px] px-1 rounded bg-surface-3 text-gray-500 border border-surface-4">paper</span>}</td>
                    <td className="px-2.5 py-1.5 text-left text-gray-400">{p.date}</td>
                    <td className="px-2.5 py-1.5 text-left text-gray-500">{p.signal_date || '—'}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-400">₹{NUM(p.trigger)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-500">{p.entry_time}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-200">₹{NUM(p.entry_price)}</td>
                    <td className="px-2.5 py-1.5 text-right text-emerald-400">₹{NUM(p.target)}</td>
                    <td className="px-2.5 py-1.5 text-right text-red-400">₹{NUM(p.sl)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-200">₹{NUM(p.ltp)}</td>
                    <td className={`px-2.5 py-1.5 text-right font-semibold ${p.mtm >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{NUM(p.mtm, 0)}</td>
                    <td className="px-2.5 py-1.5 text-right text-emerald-400">{NUM(p.mfe, 0)}</td>
                    <td className="px-2.5 py-1.5 text-right text-red-400">{NUM(p.mae, 0)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-400">{p.hold_days == null ? '—' : `${p.hold_days}d`}</td>
                    <td className={`px-2.5 py-1.5 text-right font-semibold ${stColor(p.status)}`}>{p.status}</td>
                  </tr>
                ))}</tbody>
              </table></div>
            )}
          </div>
        </div>
      )}

      {tab === 'today' && (() => {
        const rows = today?.setups || [];
        const broke = rows.filter((r) => r.broke);
        const downloadTodayCSV = () => {
          if (!rows.length) return;
          const cols = ['underlying', 'signal_date', 'trigger', 'ltp', 'to_trigger_pct', 'day_open', 'day_high',
            'sig_low', 'prev_close', 'lower_wick_pct', 'body_pct', 'upper_wick_pct', 'lowest_low', 'status'];
          const esc = (v) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
          const lines = [cols.join(',')].concat(rows.map((r) => cols.map((c) => esc(r[c])).join(',')));
          const blob = new Blob([lines.join('\n') + '\n'], { type: 'text/csv;charset=utf-8' });
          const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
          a.download = `hammer_today_${today?.date || ''}.csv`; a.click();
        };
        return (
          <div className="space-y-4">
            <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
              <div className="flex flex-wrap items-end gap-3">
                <div>
                  <label className={lbl}>Scan</label>
                  <div className="flex rounded-lg bg-surface-3 p-1">
                    {[[true, `Strategy watchlist (${symList().length})`], [false, 'Pick another list']].map(([v, label]) => (
                      <button key={String(v)} onClick={() => setUseStratList(v)} className={`px-3 py-1 text-xs rounded-md font-semibold transition ${useStratList === v ? 'bg-brand-600 text-white' : 'text-gray-400 hover:text-gray-200'}`}>{label}</button>
                    ))}
                  </div>
                </div>
                {!useStratList && <div className="flex-1 min-w-[280px]"><WatchlistBar universe={universe} count={universe.length} onChange={setTodaySel} /></div>}
                <button onClick={runScan} disabled={scanning} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">{scanning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />} Scan now</button>
                <button onClick={sendTelegram} disabled={sending} className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-50">{sending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-3.5 h-3.5" />} Send to Telegram</button>
                <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer" title="Re-prices the listed setups on this cadence. The hammer list itself only changes once a day.">
                  <input type="checkbox" checked={autoTick} onChange={(e) => setAutoTick(e.target.checked)} className="accent-brand-500" /> Auto-refresh
                </label>
                <div>
                  <label className={lbl}>Every</label>
                  <div className="flex items-center gap-1.5">
                    <select
                      value={customEvery ? 'custom' : String(everySecs)}
                      onChange={(e) => {
                        if (e.target.value === 'custom') { setCustomEvery(true); return; }
                        setCustomEvery(false); applyEvery(e.target.value);
                      }}
                      className={`${sel} py-1`}
                    >
                      {EVERY_PRESETS.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
                      {!customEvery && !EVERY_PRESETS.some(([v]) => v === everySecs) && <option value={everySecs}>{everyLabel(everySecs)}</option>}
                      <option value="custom">Custom…</option>
                    </select>
                    {customEvery && (
                      <>
                        <input
                          type="number" min={1} value={customVal}
                          onChange={(e) => { const n = Math.max(1, Number(e.target.value) || 1); setCustomVal(n); patch('today_refresh_secs', n * customUnit); }}
                          onBlur={() => applyEvery(customVal * customUnit)}
                          className={`${sel} py-1 w-20`}
                        />
                        <select
                          value={customUnit}
                          onChange={(e) => { const u = Number(e.target.value); setCustomUnit(u); applyEvery(customVal * u); }}
                          className={`${sel} py-1`}
                        >
                          <option value={1}>sec</option><option value={60}>min</option><option value={3600}>hour</option>
                        </select>
                      </>
                    )}
                  </div>
                </div>
                <button onClick={runTick} disabled={!rows.length} title="Refresh now" className="text-gray-400 hover:text-white disabled:opacity-40"><RefreshCw className="w-4 h-4" /></button>
              </div>
              <p className="text-[11px] text-gray-600">
                Yesterday's candle is the signal; these stocks are one print above the trigger away from a BUY.
                {today && <> Scanned {today.scanned} stocks at {today.generated_at?.slice(-8)}{today.priced_at ? ` · prices ${today.priced_at}` : ''}.</>}
                {' '}
                {cfg.telegram_alerts
                  ? <span className="text-emerald-400/90">Auto-alert is ON — every stock that takes out its trigger is sent to Telegram once, the moment it happens (also server-side while the strategy runs).</span>
                  : <span className="text-amber-400/80">Turn on “Telegram alerts” in Positions to have breakouts pushed automatically.</span>}
                {today?.alerted_today?.length ? <> Already alerted today: {today.alerted_today.join(', ')}.</> : null}
              </p>
            </div>

            {today && (
              <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm bg-surface-2 border border-surface-3 rounded-xl px-4 py-2.5">
                <span className="text-gray-400">Setups today <strong className="text-gray-100">{rows.length}</strong></span>
                <span className="text-gray-400">Broken out <strong className="text-emerald-400">{broke.length}</strong></span>
                <span className="text-gray-400">Waiting <strong className="text-amber-400">{rows.length - broke.length}</strong></span>
                <span className="text-gray-500 text-xs">{today.date} · {today.scanned} scanned</span>
                <button onClick={downloadTodayCSV} disabled={!rows.length} className="ml-auto flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-40"><Download className="w-3.5 h-3.5" /> CSV</button>
              </div>
            )}

            <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
              {!rows.length ? (
                <div className="px-4 py-10 text-center text-gray-500 text-sm">{scanning ? <span className="inline-flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Scanning…</span> : today ? 'No stock qualifies today — no hammer at the lookback low on yesterday’s candle.' : 'Run a scan to see today’s qualifying stocks.'}</div>
              ) : (
                <div className="overflow-x-auto"><table className="w-full text-xs whitespace-nowrap">
                  <thead className="bg-surface-3 text-gray-300"><tr>{[['Stock', 'underlying', 'l'], ['Hammer On', 'signal_date', 'l'], ['Trigger (buy above)', 'trigger', 'r'],
                    ['LTP', 'ltp', 'r'], ['% to Trigger', 'to_trigger_pct', 'r'], ['Day High', 'day_high', 'r'], ['Hammer Low (SL)', 'sig_low', 'r'],
                    ['Wick %', 'lower_wick_pct', 'r'], ['Body %', 'body_pct', 'r'], ['Up Wick %', 'upper_wick_pct', 'r'], ['Status', 'status', 'r']]
                    .map(([label, k, a]) => <SortTh key={k} label={label} k={k} align={a} sort={todaySort} onSort={(kk) => setTodaySort((s) => nextSort(s, kk))} />)}</tr></thead>
                  <tbody>{sortRows(rows, todaySort).map((r, i) => (
                    <tr key={i} className={`border-t border-surface-3/40 ${r.broke ? 'bg-emerald-500/5' : 'hover:bg-surface-3/10'}`}>
                      <td className="px-2.5 py-1.5 text-left text-brand-300 font-semibold">{r.underlying}</td>
                      <td className="px-2.5 py-1.5 text-left text-gray-500">{r.signal_date}</td>
                      <td className="px-2.5 py-1.5 text-right text-gray-100 font-medium">₹{NUM(r.trigger)}</td>
                      <td className="px-2.5 py-1.5 text-right text-gray-200">{r.ltp == null ? '—' : `₹${NUM(r.ltp)}`}</td>
                      <td className={`px-2.5 py-1.5 text-right font-semibold ${r.to_trigger_pct == null ? 'text-gray-500' : r.to_trigger_pct <= 0 ? 'text-emerald-400' : r.to_trigger_pct < 1 ? 'text-amber-400' : 'text-gray-400'}`}>{r.to_trigger_pct == null ? '—' : `${NUM(r.to_trigger_pct)}%`}</td>
                      <td className="px-2.5 py-1.5 text-right text-gray-400">{r.day_high == null ? '—' : NUM(r.day_high)}</td>
                      <td className="px-2.5 py-1.5 text-right text-red-400">₹{NUM(r.sig_low)}</td>
                      <td className="px-2.5 py-1.5 text-right text-emerald-400">{NUM(r.lower_wick_pct)}</td>
                      <td className="px-2.5 py-1.5 text-right text-gray-400">{NUM(r.body_pct)}</td>
                      <td className="px-2.5 py-1.5 text-right text-gray-400">{NUM(r.upper_wick_pct)}</td>
                      <td className="px-2.5 py-1.5 text-right">{r.broke
                        ? <span className="inline-flex items-center gap-1 text-emerald-400 font-semibold"><TrendingUp className="w-3.5 h-3.5" />BREAKOUT</span>
                        : <span className="text-amber-400/80">ARMED</span>}</td>
                    </tr>
                  ))}</tbody>
                </table></div>
              )}
            </div>
          </div>
        );
      })()}

      {tab === 'info' && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-5 space-y-3 text-sm text-gray-300 max-w-3xl">
          <h3 className="font-semibold text-gray-100">How the Hammer-at-3/6-Month-Low Breakout works</h3>
          <p>A daily positional swing. Everything in the setup is measured on the <strong>previous day's daily candle</strong> (the “signal candle”), and every percentage is taken against that candle's <strong>low</strong> — exactly like the Pine study.</p>
          <ol className="list-decimal pl-5 space-y-1 text-gray-400">
            <li><strong className="text-emerald-400">Lower wick ≥ {cfg.lower_wick_min}%</strong> — sellers pushed price down and got rejected.</li>
            <li><strong>Body &lt; {cfg.body_max}%</strong> — indecision, not a big directional bar.</li>
            <li><strong>Upper wick &lt; {cfg.upper_wick_max}%</strong> — the close is near the high; buyers held the gains.</li>
            <li><strong>Its low is the lowest low of the last {cfg.low_lookback} bars</strong> ({cfg.low_lookback === 126 ? '≈ 6 months' : cfg.low_lookback === 63 ? '≈ 3 months' : 'custom window'}).</li>
            <li><strong className="text-red-400">The {cfg.red_before} candles before it were all red</strong> — a sustained slide into the low, not a one-day spike.</li>
          </ol>
          <p><strong className="text-emerald-400">Trigger:</strong> the current day trades <strong>above the signal candle's high</strong> → <strong>BUY</strong> (long only — the study has no short leg). A gap-up fills at the open.</p>
          <p><strong>Exit:</strong> target {cfg.target_mode === 'points' ? `${cfg.target_value} pts` : `${cfg.target_value}%`} above entry; stop {cfg.sl_mode === 'signal_low' ? 'at the hammer’s own low' : cfg.sl_mode === 'points' ? `${cfg.sl_value} pts below entry` : `${cfg.sl_value}% below entry`}; otherwise squared off after {cfg.max_hold_days} days at {cfg.square_off}. Sized by capital-per-trade or a fixed quantity, capped by Max Positions.</p>
          <p className="text-[12px] text-gray-500">Backtest note: exits are resolved on <strong>daily</strong> bars <em>after</em> the breakout day. A daily candle has no intraday path, so the breakout day itself is never used to decide target vs. stop — the day's low may well have printed before the trigger. Max profit / max loss come from the daily highs and lows over the hold.</p>
          <p><strong className="text-gray-100">Today's Stocks</strong> scans the F&amp;O universe by default (or any watchlist you pick) and lists every stock whose <em>yesterday</em> candle qualifies, with the trigger, the live LTP and how far it still is from the break. It re-prices on your chosen cadence — 15 s up to an hour, or a custom interval — and a stock flips to <strong className="text-emerald-400">BREAKOUT</strong> the moment it trades above the trigger.</p>
          <p>With <strong>Telegram alerts</strong> on, each breakout is pushed <strong>automatically</strong>, once per stock per day, as it happens — from the browser tick and, so it keeps working with the tab closed, from the live engine too (it announces every trigger break, including ones it can't buy because the position cap is full or the entry cutoff has passed). The dedupe ledger is on the server, so several open tabs never double-send. The morning digest of the day's armed list is sent once when the engine arms, and the <strong>Send to Telegram</strong> button re-sends the full list on demand.</p>
          <p className="text-[12px] text-gray-500">Backtest &amp; Simulate are read-only. Positions run <strong>paper</strong> by default; real orders need paper mode off plus the global trading gate on.</p>
        </div>
      )}
    </div>
  );
}

// ── daily candle chart: the lookback window, the hammer, the trigger + trade levels ──
function DailyChart({ timeline, setup, trade }) {
  const n = (timeline || []).length;
  if (!n) return null;
  const step = 22, cw = 12, padL = 6, padR = 78, padT = 14, padB = 34, plotH = 320;
  const width = padL + n * step + padR, height = padT + plotH + padB;
  const extra = [setup?.high, setup?.low, trade?.entry, trade?.target, trade?.sl].filter((v) => v != null);
  let lo = Math.min(...timeline.map((c) => c.low), ...extra);
  let hi = Math.max(...timeline.map((c) => c.high), ...extra);
  const pad = (hi - lo) * 0.04 || 1; lo -= pad; hi += pad;
  const y = (p) => padT + (hi - p) / (hi - lo) * plotH;
  const xc = (i) => padL + i * step + step / 2;
  const sigIdx = timeline.findIndex((c) => c.signal);
  const line = (val, color, label, dash) => (
    <g key={label}>
      <line x1={padL} x2={padL + n * step} y1={y(val)} y2={y(val)} stroke={color} strokeWidth="1.2" strokeDasharray={dash} />
      <rect x={padL + n * step + 2} y={y(val) - 8} width={74} height={15} rx={3} fill={color} />
      <text x={padL + n * step + 5} y={y(val) + 3} fontSize="9" fill="#04121f" fontWeight="700">{label} {NUM(val, 1)}</text>
    </g>
  );
  return (
    <div className="overflow-x-auto">
      <svg width={width} height={height} className="block" style={{ minWidth: '100%' }}>
        {sigIdx >= 0 && <rect x={xc(sigIdx) - step / 2} y={padT} width={step} height={plotH} fill="#f59e0b18" />}
        {setup?.high != null && line(setup.high, '#22c55e', 'Trigger', '5 3')}
        {setup?.low != null && line(setup.low, '#f87171', 'H-Low', '5 3')}
        {trade?.entry != null && line(trade.entry, '#9ca3af', 'Entry', '2 2')}
        {trade?.target != null && line(trade.target, '#22c55e', 'Target', '1 3')}
        {trade?.sl != null && line(trade.sl, '#f87171', 'SL', '1 3')}
        {timeline.map((c, i) => {
          const col = c.red ? RED : GREEN;
          const top = Math.min(y(c.open), y(c.close)); const h = Math.max(Math.abs(y(c.open) - y(c.close)), 1);
          return (
            <g key={i}>
              <line x1={xc(i)} x2={xc(i)} y1={y(c.high)} y2={y(c.low)} stroke={col} strokeWidth="1" />
              <rect x={xc(i) - cw / 2} y={top} width={cw} height={h} fill={col} rx={0.5} />
              <rect x={xc(i) - step / 2} y={padT} width={step} height={plotH} fill="transparent"><title>{`${c.date}  O ${NUM(c.open)}  H ${NUM(c.high)}  L ${NUM(c.low)}  C ${NUM(c.close)}`}</title></rect>
              {(i % 3 === 0 || c.signal) && <text x={xc(i)} y={height - 20} fontSize="8" fill={c.signal ? '#fbbf24' : '#6b7280'} textAnchor="middle" transform={`rotate(-45 ${xc(i)} ${height - 20})`}>{c.date.slice(5)}</text>}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function SimulateView({ sim }) {
  const [showTable, setShowTable] = useState(false);
  const s = sim.setup; const t = sim.trade;
  const traded = t && t.qty;
  return (
    <div className="space-y-4">
      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
          <span className="text-gray-100 font-semibold">{sim.symbol} · {sim.date}</span>
          <span className="text-gray-400">Hammer candle <strong className="text-gray-200">{sim.signal_date}</strong></span>
          <span className="text-gray-400">Setup <strong className={sim.hammer ? 'text-emerald-400' : 'text-red-400'}>{sim.hammer ? 'VALID HAMMER' : 'NOT A HAMMER'}</strong></span>
          <span className="text-gray-400">Breakout <strong className={sim.broke ? 'text-emerald-400' : 'text-gray-500'}>{sim.broke ? 'YES' : 'NO'}</strong></span>
          {sim.hammer && sim.broke && <span className="inline-flex items-center gap-1 text-emerald-400 font-semibold"><TrendingUp className="w-4 h-4" /> BUY</span>}
        </div>
      </div>

      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
        <div className="text-sm font-semibold text-gray-200 mb-3">Conditions</div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
          {(sim.checks || []).map((c, i) => (
            <div key={i} className={`flex items-center gap-2 rounded-lg px-3 py-2 border ${c.ok ? 'bg-emerald-500/5 border-emerald-500/25' : 'bg-red-500/5 border-red-500/25'}`}>
              {c.ok ? <Check className="w-4 h-4 text-emerald-400 shrink-0" /> : <X className="w-4 h-4 text-red-400 shrink-0" />}
              <div className="min-w-0">
                <div className="text-xs text-gray-200 truncate">{c.label}</div>
                <div className="text-[11px] text-gray-500 truncate">{c.value}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="bg-surface-2 border border-surface-3 rounded-xl p-3">
        <div className="flex items-center justify-between mb-2 px-1">
          <div className="text-sm font-semibold text-gray-200">{sim.symbol} · daily</div>
          <div className="flex items-center gap-4 text-[11px] text-gray-400">
            <span className="text-amber-400">▮ hammer candle</span>
            <span>--- Trigger {NUM(s?.high, 1)}</span><span>--- Hammer low {NUM(s?.low, 1)}</span>
          </div>
        </div>
        <DailyChart timeline={sim.timeline} setup={s} trade={traded ? t : null} />
      </div>

      {traded ? (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
          <div className="text-sm font-semibold text-gray-200 mb-3">Trade</div>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            {[['Signal', <span className="inline-flex items-center gap-1 text-emerald-400"><TrendingUp className="w-3.5 h-3.5" />LONG</span>],
              ['Product', <span className="text-gray-200">{t.product}</span>],
              ['Qty', <span className="text-gray-100">{INT(t.qty)}</span>],
              ['Entry', <span className="text-gray-100">₹{NUM(t.entry)}</span>],
              ['Target / SL', <><span className="text-emerald-400">₹{NUM(t.target)}</span> <span className="text-gray-600">/</span> <span className="text-red-400">₹{NUM(t.sl)}</span></>],
              ['Exit', t.exit == null ? <span className="text-gray-400">—</span> : <><span className="text-gray-100">₹{NUM(t.exit)}</span> <span className={`text-xs ${stColor(t.exit_reason)}`}>({t.exit_reason})</span>{t.exit_date ? <span className="text-gray-500 text-xs"> on {t.exit_date}</span> : null}</>],
              ['Hold', <span className="text-gray-200">{t.hold_label ?? (t.open ? 'open' : '—')}</span>],
              ['MTM', <span className={`font-semibold ${t.mtm >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>₹{NUM(t.mtm, 0)}</span>],
              ['Max Profit / Loss', <><span className="text-emerald-400">₹{NUM(t.max_profit, 0)}</span> <span className="text-gray-600">/</span> <span className="text-red-400">₹{NUM(t.max_loss, 0)}</span></>]].map(([k, v]) => (
              <div key={k} className="bg-surface-3/40 border border-surface-3 rounded-lg px-3 py-2"><div className="text-[10px] uppercase tracking-wide text-gray-500 mb-0.5">{k}</div><div className="text-sm">{v}</div></div>
            ))}
          </div>
        </div>
      ) : (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 text-sm text-gray-400">
          {!sim.hammer ? 'No trade — the previous daily candle is not a hammer at the lookback low.'
            : !sim.broke ? `No trade — the hammer held but ${NUM(s?.high)} was never taken out on ${sim.date}.`
              : t?.notes || 'No trade.'}
        </div>
      )}

      <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
        <button onClick={() => setShowTable((v) => !v)} className="w-full px-3 py-2 flex items-center justify-between text-sm font-semibold text-gray-300 hover:text-white"><span>Daily candle table</span><span className="text-xs text-gray-500">{showTable ? 'Hide ▲' : 'Show ▼'}</span></button>
        {showTable && (
          <div className="overflow-x-auto max-h-[360px] border-t border-surface-3"><table className="w-full text-xs whitespace-nowrap">
            <thead className="bg-surface-3 text-gray-400 sticky top-0"><tr>{['Date', 'Open', 'High', 'Low', 'Close', 'Colour'].map((h) => <th key={h} className="px-2.5 py-1.5 text-right first:text-left font-semibold">{h}</th>)}</tr></thead>
            <tbody>{(sim.timeline || []).map((c, i) => (
              <tr key={i} className={`border-t border-surface-3/30 ${c.signal ? 'bg-amber-500/10' : ''}`}>
                <td className="px-2.5 py-1 text-left text-gray-300">{c.date}{c.signal ? ' ◄ hammer' : ''}</td>
                <td className="px-2.5 py-1 text-right text-gray-400">{NUM(c.open)}</td>
                <td className="px-2.5 py-1 text-right text-gray-400">{NUM(c.high)}</td>
                <td className="px-2.5 py-1 text-right text-gray-400">{NUM(c.low)}</td>
                <td className="px-2.5 py-1 text-right text-gray-200">{NUM(c.close)}</td>
                <td className={`px-2.5 py-1 text-right font-semibold ${c.red ? 'text-red-400' : 'text-emerald-400'}`}>{c.red ? 'red' : 'green'}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </div>
    </div>
  );
}
