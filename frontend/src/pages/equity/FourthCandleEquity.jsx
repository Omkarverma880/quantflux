import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  CandlestickChart, Play, Loader2, AlertCircle, Download, Info, TrendingUp, TrendingDown,
  FlaskConical, Wallet, Radio, RefreshCw, Save,
} from 'lucide-react';
import { api } from '../../api';
import WatchlistBar from '../../components/WatchlistBar';

const sel = 'bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';
const lbl = 'block text-[11px] text-gray-500 uppercase tracking-wide mb-1';
const NUM = (v, d = 2) => (v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
const INT = (v) => (v == null ? '—' : Number(v).toLocaleString('en-IN'));
const GREEN = '#10b981'; const RED = '#ef4444';

function SideBadge({ s }) {
  if (s === 'LONG') return <span className="inline-flex items-center gap-1 text-emerald-400"><TrendingUp className="w-3.5 h-3.5" />LONG</span>;
  if (s === 'SHORT') return <span className="inline-flex items-center gap-1 text-red-400"><TrendingDown className="w-3.5 h-3.5" />SHORT</span>;
  return <span className="text-gray-500">—</span>;
}
const stColor = (s) => ({ TARGET: 'text-emerald-400', STOP: 'text-red-400', OPEN: 'text-amber-400', SQUAREOFF: 'text-gray-400' }[s] || 'text-gray-400');
const biasLabel = (r) => {
  const c = r.colors;
  if (Array.isArray(c) && c.length >= 3) return c.slice(0, 3).every((x) => x === 'red') ? '3 RED' : c.slice(0, 3).every((x) => x === 'green') ? '3 GREEN' : 'MIXED';
  return r.bias === 'call' ? '3 RED' : r.bias === 'put' ? '3 GREEN' : 'MIXED';
};
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
function SortTh({ label, k, align, sort, onSort }) {
  const active = sort.key === k;
  return <th onClick={() => onSort(k)} className={`px-2.5 py-2 font-semibold cursor-pointer select-none ${align === 'l' ? 'text-left' : 'text-right'} ${active ? 'text-brand-300' : ''}`}>{label}{active ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}</th>;
}
const BT_COLS = [['Date', 'date', 'l'], ['Stock', 'underlying', 'l'], ['Bias', 'bias', 'r'], ['4th High', 'fourth_high', 'r'],
  ['4th Low', 'fourth_low', 'r'], ['Breakout', 'breakout_time', 'r'], ['Side', 'side', 'r'], ['Qty', 'qty', 'r'],
  ['Entry', 'entry', 'r'], ['Target', 'target', 'r'], ['SL', 'sl', 'r'], ['Exit', 'exit', 'r'], ['Reason', 'status', 'r'],
  ['MTM', 'mtm', 'r'], ['Max Profit', 'max_profit', 'r'], ['Max Loss', 'max_loss', 'r'], ['Hold', 'hold_days', 'r']];
const POS_COLS = [['Stock', 'underlying', 'l'], ['Side', 'direction', 'r'], ['Product', 'product', 'r'], ['Entry@', 'entry_time', 'r'],
  ['Entry', 'entry_price', 'r'], ['Target', 'target', 'r'], ['SL', 'sl', 'r'], ['LTP', 'ltp', 'r'], ['MTM', 'mtm', 'r'],
  ['Max Profit', 'mfe', 'r'], ['Max Loss', 'mae', 'r'], ['Status', 'status', 'r'], ['Exit@', 'exit_time', 'r']];

export default function FourthCandleEquity() {
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
    api.fceConfig().then((r) => { if (r.status === 'ok') { setCfg(r.config); setSymbolsText((r.config.symbols || []).join(', ')); } }).catch(() => setCfg({}));
    api.researchPMVwapEquityUniverse?.().then((r) => { if (r?.status === 'ok') setUniverse(r.stocks || []); }).catch(() => {});
    api.researchWatchlists?.().then((r) => { if (r?.status === 'ok') setSavedWls(r.watchlists || []); }).catch(() => {});
    const t = new Date(); const y = new Date(); y.setDate(t.getDate() - 20);
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
      const r = await api.fceBacktest(body);
      if (r.status === 'ok') setData(r); else showErr(r.message || 'Backtest failed');
    } catch (e) { showErr(e.message); } finally { setLoading(false); }
  }, [cfg, sel2, start, end, showNonTrades, applyCaps]);

  const runSimulate = async () => {
    if (!simSym.trim()) return showErr('Enter a stock symbol');
    setSimLoading(true); setErr('');
    try {
      const r = await api.fceSimulate({ symbol: simSym.trim().toUpperCase(), overrides: cfg, date: simDate || null });
      if (r.status === 'ok') setSim(r); else showErr(r.message || 'Simulate failed');
    } catch (e) { showErr(e.message); } finally { setSimLoading(false); }
  };

  const saveCfg = async () => {
    const syms = symbolsText.split(/[\s,;\n]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
    const r = await api.fceConfigSave({ ...cfg, symbols: syms });
    if (r.status === 'ok') { setCfg(r.config); flash('Config saved'); } else showErr(r.message);
  };

  const loadStatus = useCallback(async () => { try { const r = await api.fceStatus(); if (r.status === 'ok') setStatus(r); } catch { /* */ } }, []);
  const loadPositions = useCallback(async () => { try { const r = await api.fcePositions(); if (r.status === 'ok') setPositions(r.positions || []); } catch { /* */ } }, []);
  useEffect(() => { if (tab === 'positions') { loadStatus(); loadPositions(); pollRef.current = setInterval(() => { loadStatus(); loadPositions(); }, 5000); return () => clearInterval(pollRef.current); } }, [tab, loadStatus, loadPositions]);

  const startStrat = async () => {
    const syms = symbolsText.split(/[\s,;\n]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
    const r = await api.fceStart({ ...cfg, symbols: syms });
    if (r.status === 'ok') { setStatus(r); flash('Strategy started'); loadPositions(); } else showErr(r.message);
  };
  const stopStrat = async () => { const r = await api.fceStop(); if (r.status === 'ok') { setStatus(r); flash('Stopped'); } else showErr(r.message); };
  const saveStratCfg = async () => {
    const syms = symbolsText.split(/[\s,;\n]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
    const r = await api.fceUpdateConfig({ ...cfg, symbols: syms });
    if (r.status === 'ok') flash('Saved'); else showErr(r.message);
  };

  const rows = data?.rows || [];
  const downloadCSV = () => {
    const cols = ['date', 'underlying', 'bias', 'fourth_high', 'fourth_low', 'breakout_time', 'side', 'qty', 'entry', 'target', 'sl', 'exit', 'exit_time', 'exit_reason', 'mtm', 'max_profit', 'max_loss', 'hold_days', 'hold_label', 'product', 'status', 'notes'];
    const esc = (v) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const lines = [cols.join(',')].concat(rows.map((r) => cols.map((c) => esc(r[c])).join(',')));
    const blob = new Blob([lines.join('\n') + '\n'], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = '4th_candle_cash_backtest.csv'; a.click();
  };
  const downloadPositionsCSV = () => {
    if (!positions.length) return;
    const cols = ['date', 'underlying', 'direction', 'product', 'qty', 'entry_time', 'entry_price', 'target', 'sl', 'ltp', 'mtm', 'mfe', 'mae', 'status', 'exit_time', 'exit_price', 'exit_reason', 'paper', 'hold_days'];
    const esc = (v) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const lines = [cols.join(',')].concat(positions.map((r) => cols.map((c) => esc(r[c])).join(',')));
    const blob = new Blob([lines.join('\n') + '\n'], { type: 'text/csv;charset=utf-8' });
    const mode = positions.every((p) => p.paper) ? 'paper' : positions.some((p) => p.paper) ? 'mixed' : 'live';
    const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-');
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `4th_candle_cash_positions_${mode}_${stamp}.csv`; a.click();
  };

  if (!cfg) return <div className="p-6 text-gray-500 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>;
  const num = (k, min = 0, step = 1) => <input type="number" min={min} step={step} value={cfg[k] ?? ''} onChange={(e) => patch(k, e.target.value)} className={`w-full ${sel}`} />;

  const ConfigGrid = () => (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
      <div><label className={lbl}>Target By</label><select value={cfg.target_mode} onChange={(e) => patch('target_mode', e.target.value)} className={`w-full ${sel}`}><option value="percent">Percent</option><option value="points">Points</option></select></div>
      <div><label className={lbl}>Target {cfg.target_mode === 'points' ? '(pts)' : '%'}</label>{num('target_value', 0, 0.1)}</div>
      <div><label className={lbl}>SL By</label><select value={cfg.sl_mode} onChange={(e) => patch('sl_mode', e.target.value)} className={`w-full ${sel}`}><option value="percent">Percent</option><option value="points">Points</option></select></div>
      <div><label className={lbl}>SL {cfg.sl_mode === 'points' ? '(pts)' : '%'}</label>{num('sl_value', 0, 0.1)}</div>
      <div><label className={lbl}>Entry Cutoff</label><input value={cfg.entry_cutoff} onChange={(e) => patch('entry_cutoff', e.target.value)} className={`w-full ${sel}`} /></div>
      <div><label className={lbl}>Square-off</label><input value={cfg.square_off} onChange={(e) => patch('square_off', e.target.value)} className={`w-full ${sel}`} /></div>
      <div><label className={lbl}>Product</label><select value={cfg.product} onChange={(e) => patch('product', e.target.value)} className={`w-full ${sel}`}><option value="MIS">MIS (intraday)</option><option value="CNC">CNC (holding)</option></select></div>
      {cfg.product === 'CNC' && <div><label className={lbl}>Max Hold (days)</label>{num('max_hold_days', 0, 1)}</div>}
      <div><label className={lbl}>Qty By</label><select value={cfg.qty_mode} onChange={(e) => patch('qty_mode', e.target.value)} className={`w-full ${sel}`}><option value="capital">Capital</option><option value="fixed">Fixed Qty</option></select></div>
      {cfg.qty_mode === 'capital'
        ? <div><label className={lbl}>Capital / Trade ₹</label>{num('capital_per_trade', 0, 1000)}</div>
        : <div><label className={lbl}>Fixed Qty</label>{num('fixed_qty', 1, 1)}</div>}
      <div><label className={lbl}>Max Positions</label>{num('max_positions', 1, 1)}</div>
      <div><label className={lbl}>Max Long</label>{num('max_long', 0, 1)}</div>
      <div><label className={lbl}>Max Short</label>{num('max_short', 0, 1)}</div>
      <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer self-end"><input type="checkbox" checked={cfg.apply_costs} onChange={(e) => patch('apply_costs', e.target.checked)} className="accent-brand-500" /> Net of costs</label>
      <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer self-end" title="Default: 3 red → LONG, 3 green → SHORT. Reversed: 3 red → SHORT (break the 4th LOW), 3 green → LONG (break the 4th HIGH)."><input type="checkbox" checked={!!cfg.reverse_signal} onChange={(e) => patch('reverse_signal', e.target.checked)} className="accent-brand-500" /> Reverse signal</label>
    </div>
  );

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-[1600px] mx-auto">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <CandlestickChart className="w-6 h-6 text-brand-400" />
            <h1 className="text-xl font-bold text-gray-100">4th Candle — Cash Equity</h1>
            <span className="text-[11px] px-2 py-0.5 rounded-full bg-brand-500/15 text-brand-300 border border-brand-500/25">Equity Strategy 3</span>
          </div>
          <p className="text-sm text-gray-500 mt-0.5">3 red → break above 4th high → <b>LONG</b>. 3 green → break below 4th low → <b>SHORT</b>. Trades the stock (MIS/CNC); target/SL on price. Works on deep 5-min history.</p>
        </div>
        {status && <div className={`flex items-center gap-1.5 text-xs ${status.is_active ? 'text-emerald-400' : 'text-gray-500'}`}><Radio className={`w-4 h-4 ${status.is_active ? 'animate-pulse' : ''}`} /> {status.is_active ? 'LIVE ON' : 'LIVE OFF'} · {status.paper_trade ? 'Paper' : 'REAL'}</div>}
      </div>

      <div className="flex gap-1 border-b border-surface-3">
        {[['backtest', 'Backtest', FlaskConical], ['simulate', 'Simulate', CandlestickChart], ['positions', 'Positions', Wallet], ['info', 'Info', Info]].map(([id, label, Icon]) => (
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
              <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer ml-1"><input type="checkbox" checked={showNonTrades} onChange={(e) => setShowNonTrades(e.target.checked)} className="accent-brand-500" /> Show non-trade days</label>
              <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer" title="Apply live portfolio caps (Max Positions / Long / Short) so the backtest matches Live/Paper."><input type="checkbox" checked={applyCaps} onChange={(e) => setApplyCaps(e.target.checked)} className="accent-brand-500" /> Apply caps (match live)</label>
            </div>
            {ConfigGrid()}
          </div>

          {data?.note && <div className="text-xs text-amber-300/90 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2">{data.note}</div>}
          {data?.caps_note && <div className="text-xs text-sky-300/90 bg-sky-500/10 border border-sky-500/30 rounded-lg px-3 py-2">{data.caps_note}</div>}
          {data && (
            <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm bg-surface-2 border border-surface-3 rounded-xl px-4 py-2.5">
              <span className="text-gray-400">Trades <strong className="text-gray-100">{data.stats.total}</strong></span>
              <span className="text-gray-400">Win% <strong className="text-emerald-400">{data.stats.win_rate}%</strong></span>
              <span className="text-gray-400">Total MTM <strong className={data.stats.total_mtm >= 0 ? 'text-emerald-400' : 'text-red-400'}>{NUM(data.stats.total_mtm)}</strong></span>
              <span className="text-gray-400">Long <strong className="text-emerald-400">{data.stats.long}</strong> · Short <strong className="text-red-400">{data.stats.short}</strong></span>
              <span className="text-gray-400">Best <strong className="text-emerald-400">{NUM(data.stats.best)}</strong> · Worst <strong className="text-red-400">{NUM(data.stats.worst)}</strong></span>
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
                    <td className="px-2.5 py-1.5 text-right text-gray-400">{biasLabel(r)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-300">{NUM(r.fourth_high)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-300">{NUM(r.fourth_low)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-400">{r.breakout_time}</td>
                    <td className="px-2.5 py-1.5 text-right"><SideBadge s={r.side} /></td>
                    <td className="px-2.5 py-1.5 text-right text-gray-200 font-medium">{INT(r.qty)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-200">₹{NUM(r.entry)}</td>
                    <td className="px-2.5 py-1.5 text-right text-emerald-400">₹{NUM(r.target)}</td>
                    <td className="px-2.5 py-1.5 text-right text-red-400">₹{NUM(r.sl)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-300">{r.exit == null ? '—' : `₹${NUM(r.exit)}`}</td>
                    <td className={`px-2.5 py-1.5 text-right font-semibold ${stColor(r.exit_reason)}`}>{r.exit_reason}</td>
                    <td className={`px-2.5 py-1.5 text-right font-semibold ${r.mtm >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{NUM(r.mtm)}</td>
                    <td className="px-2.5 py-1.5 text-right text-emerald-400">{NUM(r.max_profit)}</td>
                    <td className="px-2.5 py-1.5 text-right text-red-400">{NUM(r.max_loss)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-400" title={r.entry_time && r.exit_time ? `${r.entry_time} → ${r.exit_time}` : ''}>{r.hold_label ?? (r.open ? 'open' : '—')}</td>
                  </tr>
                ) : (
                  <tr key={i} className="border-t border-surface-3/40 bg-surface-3/10 text-gray-500">
                    <td className="px-2.5 py-1.5 text-left text-gray-500">{r.date}</td>
                    <td className="px-2.5 py-1.5 text-left text-gray-400 font-semibold">{r.underlying}</td>
                    <td className="px-2.5 py-1.5 text-right">{biasLabel(r)}</td>
                    <td className="px-2.5 py-1.5 text-right">{NUM(r.fourth_high)}</td>
                    <td className="px-2.5 py-1.5 text-right">{NUM(r.fourth_low)}</td>
                    <td className="px-2.5 py-1.5 text-right">{r.breakout_time || '—'}</td>
                    <td className="px-2.5 py-1.5 text-right"><SideBadge s={r.side} /></td>
                    <td className="px-2.5 py-1.5 text-left text-amber-400/80 italic" colSpan={10}>{r.status} — {r.notes}</td>
                  </tr>
                )))}
                {!rows.length && <tr><td colSpan={BT_COLS.length} className="px-4 py-8 text-center text-gray-500">No trades in range.{!showNonTrades && ' Tick “Show non-trade days” to see why stocks were skipped.'}</td></tr>}
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
            <div><label className={lbl}>Date</label><input type="date" value={simDate} onChange={(e) => setSimDate(e.target.value)} className={sel} /></div>
            <button onClick={runSimulate} disabled={simLoading} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">{simLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <CandlestickChart className="w-4 h-4" />} Simulate</button>
            <span className="text-[11px] text-gray-600">Cash equity — works for any NSE/BSE stock and long-back dates.</span>
          </div>
          {sim && <SimulateView sim={sim} />}
          {!sim && !simLoading && <div className="bg-surface-2 border border-surface-3 rounded-xl p-10 text-center text-gray-500 text-sm">Pick a stock &amp; date to see the candle colours, 4th-candle lines, breakout, entry/target/SL and the resulting LONG/SHORT trade.</div>}
        </div>
      )}

      {tab === 'positions' && (
        <div className="space-y-4">
          <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer"><input type="checkbox" checked={cfg.paper_trade} onChange={(e) => patch('paper_trade', e.target.checked)} className="accent-brand-500" /> Paper mode</label>
              <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer"><input type="checkbox" checked={cfg.auto_start} onChange={(e) => patch('auto_start', e.target.checked)} className="accent-brand-500" /> Auto-start on login</label>
              {status && <span className="text-xs text-gray-500">Open {status.open_positions}/{status.max_positions} · Long {status.long}/{status.max_long} · Short {status.short}/{status.max_short}</span>}
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
                <label className={`${lbl} !mb-0`}>Watchlist (stocks the strategy trades)</label>
                {(savedWls.length > 0 || universe.length > 0) && (
                  <div className="flex items-center gap-1.5 ml-auto">
                    <select defaultValue="" onChange={(e) => { loadWl(e.target.value, false); e.target.value = ''; }} className={`${sel} py-1 text-xs`}><option value="">Load saved watchlist…</option>{universe.length > 0 && <option value="__ALL_FNO__">All F&O ({universe.length})</option>}{savedWls.map((w) => <option key={w.id} value={w.id}>{w.name} ({w.count})</option>)}</select>
                    <select defaultValue="" onChange={(e) => { loadWl(e.target.value, true); e.target.value = ''; }} className={`${sel} py-1 text-xs`}><option value="">+ Add from…</option>{universe.length > 0 && <option value="__ALL_FNO__">All F&O ({universe.length})</option>}{savedWls.map((w) => <option key={w.id} value={w.id}>{w.name} ({w.count})</option>)}</select>
                    <button onClick={() => setSymbolsText('')} className="text-xs px-2 py-1 rounded-lg border bg-surface-3 text-gray-400 border-surface-4 hover:text-white">Clear</button>
                  </div>
                )}
              </div>
              <textarea value={symbolsText} onChange={(e) => setSymbolsText(e.target.value)} rows={2} placeholder="RELIANCE, TCS, HDFCBANK …" className={`w-full ${sel}`} />
              <div className="text-[11px] text-gray-600 mt-0.5">{symbolsText.split(/[\s,;\n]+/).filter(Boolean).length} stocks</div>
            </div>
            {ConfigGrid()}
            <div className="flex flex-wrap items-center gap-4 pt-2 border-t border-surface-3">
              <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer"><input type="checkbox" checked={cfg.telegram_alerts} onChange={(e) => patch('telegram_alerts', e.target.checked)} className="accent-brand-500" /> Telegram alerts</label>
              <div className="flex items-center gap-2 text-xs text-gray-400">Bot<select value={cfg.telegram_bot || 'a'} onChange={(e) => patch('telegram_bot', e.target.value)} className={`${sel} py-1`}><option value="a">Bot A</option><option value="b">Bot B</option></select></div>
            </div>
            {!cfg.paper_trade && <p className="text-[11px] text-amber-400">⚠ REAL mode also needs global PAPER_TRADE=False + TRADING_ENABLED=True. Orders are {cfg.product}. SHORT under CNC needs delivery holdings — prefer MIS for shorting.</p>}
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
              <span className="text-sm font-semibold text-gray-200">Positions (today) <span className="text-gray-500">({positions.length})</span></span>
              <button onClick={downloadPositionsCSV} disabled={!positions.length} className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-40"><Download className="w-3.5 h-3.5" /> CSV</button>
            </div>
            {!positions.length ? <div className="px-4 py-8 text-center text-gray-500 text-sm">No positions yet — opens on 4th-candle breakouts while Live is on.</div> : (
              <div className="overflow-x-auto"><table className="w-full text-xs whitespace-nowrap">
                <thead className="bg-surface-3 text-gray-300"><tr>{POS_COLS.map(([label, k, a]) => <SortTh key={k} label={label} k={k} align={a} sort={posSort} onSort={(kk) => setPosSort((s) => nextSort(s, kk))} />)}</tr></thead>
                <tbody>{sortRows(positions, posSort).map((p) => (
                  <tr key={p.id} className="border-t border-surface-3/40">
                    <td className="px-2.5 py-1.5 text-left text-brand-300 font-semibold">{p.underlying}{p.paper && <span className="ml-1 text-[9px] px-1 rounded bg-surface-3 text-gray-500 border border-surface-4">paper</span>}</td>
                    <td className="px-2.5 py-1.5 text-right"><SideBadge s={p.direction} /></td>
                    <td className="px-2.5 py-1.5 text-right text-gray-400">{p.product}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-500">{p.entry_time}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-200">₹{NUM(p.entry_price)}</td>
                    <td className="px-2.5 py-1.5 text-right text-emerald-400">₹{NUM(p.target)}</td>
                    <td className="px-2.5 py-1.5 text-right text-red-400">₹{NUM(p.sl)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-200">₹{NUM(p.ltp)}</td>
                    <td className={`px-2.5 py-1.5 text-right font-semibold ${p.mtm >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{NUM(p.mtm, 0)}</td>
                    <td className="px-2.5 py-1.5 text-right text-emerald-400">{NUM(p.mfe, 0)}</td>
                    <td className="px-2.5 py-1.5 text-right text-red-400">{NUM(p.mae, 0)}</td>
                    <td className={`px-2.5 py-1.5 text-right font-semibold ${stColor(p.status)}`}>{p.status}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-500">{p.exit_time || '—'}</td>
                  </tr>
                ))}</tbody>
              </table></div>
            )}
          </div>
        </div>
      )}

      {tab === 'info' && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-5 space-y-3 text-sm text-gray-300 max-w-3xl">
          <h3 className="font-semibold text-gray-100">How the 4th Candle Cash-Equity Strategy works</h3>
          <p>Same 4th-candle setup as the options version, but it trades the <strong>stock itself</strong>:</p>
          <ul className="list-disc pl-5 space-y-1 text-gray-400">
            <li><strong className="text-red-400">3 RED</strong> first candles → break <strong>above</strong> the 4th-candle high → <strong className="text-emerald-400">go LONG (buy)</strong>.</li>
            <li><strong className="text-emerald-400">3 GREEN</strong> first candles → break <strong>below</strong> the 4th-candle low → <strong className="text-red-400">go SHORT (sell)</strong>.</li>
            <li><strong>Reverse signal</strong> flips both (3 red → SHORT, 3 green → LONG).</li>
          </ul>
          <p>Target/SL are on the <strong>stock price</strong> (percent or points). <strong>MIS</strong> squares off intraday; <strong>CNC</strong> holds up to Max Hold days. Size by capital-per-trade or a fixed quantity.</p>
          <p className="text-[12px] text-gray-500">Because it uses cash-equity 5-min candles (available for years), you can backtest far further back than the options strategy. Backtest/Simulate are read-only; Positions run paper by default. Note: shorting delivery (CNC) isn't allowed at most brokers — use MIS to short intraday.</p>
        </div>
      )}
    </div>
  );
}

// ── candle chart with 4th-candle lines + breakout + entry/target/SL ──
function CandleChart({ timeline, an, trade }) {
  const n = (timeline || []).length;
  if (!n) return null;
  const step = 16, cw = 9, padL = 6, padR = 70, padT = 14, padB = 26, plotH = 340;
  const width = padL + n * step + padR, height = padT + plotH + padB;
  const extra = [an.fourth_high, an.fourth_low, trade?.entry, trade?.target, trade?.sl].filter((v) => v != null);
  let lo = Math.min(...timeline.map((c) => c.low), ...extra);
  let hi = Math.max(...timeline.map((c) => c.high), ...extra);
  const pad = (hi - lo) * 0.04 || 1; lo -= pad; hi += pad;
  const y = (p) => padT + (hi - p) / (hi - lo) * plotH;
  const xc = (i) => padL + i * step + step / 2;
  const boHHMM = trade?.breakout_time ? String(trade.breakout_time).slice(-5) : null;
  const boIdx = timeline.findIndex((c) => c.time === boHHMM);
  const line = (val, color, label, dash) => (
    <g key={label}>
      <line x1={padL} x2={padL + n * step} y1={y(val)} y2={y(val)} stroke={color} strokeWidth="1.2" strokeDasharray={dash} />
      <rect x={padL + n * step + 2} y={y(val) - 8} width={66} height={15} rx={3} fill={color} />
      <text x={padL + n * step + 5} y={y(val) + 3} fontSize="9" fill="#04121f" fontWeight="700">{label} {NUM(val, 1)}</text>
    </g>
  );
  return (
    <div className="overflow-x-auto">
      <svg width={width} height={height} className="block" style={{ minWidth: '100%' }}>
        <rect x={xc(3) - step / 2} y={padT} width={step} height={plotH} fill="#3b82f611" />
        {line(an.fourth_high, RED, 'H', '5 3')}
        {line(an.fourth_low, GREEN, 'L', '5 3')}
        {trade?.entry != null && line(trade.entry, '#9ca3af', 'E', '2 2')}
        {trade?.target != null && line(trade.target, '#22c55e', 'T', '1 3')}
        {trade?.sl != null && line(trade.sl, '#f87171', 'S', '1 3')}
        {boIdx >= 0 && <line x1={xc(boIdx)} x2={xc(boIdx)} y1={padT} y2={padT + plotH} stroke={trade?.side === 'LONG' ? GREEN : RED} strokeWidth="1" strokeDasharray="3 3" opacity="0.7" />}
        {timeline.map((c, i) => {
          const col = c.color === 'green' ? GREEN : c.color === 'red' ? RED : '#9ca3af';
          const top = Math.min(y(c.open), y(c.close)); const h = Math.max(Math.abs(y(c.open) - y(c.close)), 1);
          return (
            <g key={i}>
              <line x1={xc(i)} x2={xc(i)} y1={y(c.high)} y2={y(c.low)} stroke={col} strokeWidth="1" />
              <rect x={xc(i) - cw / 2} y={top} width={cw} height={h} fill={col} rx={0.5} />
              <rect x={xc(i) - step / 2} y={padT} width={step} height={plotH} fill="transparent"><title>{`${c.time}  O ${NUM(c.open)}  H ${NUM(c.high)}  L ${NUM(c.low)}  C ${NUM(c.close)}`}</title></rect>
              {i % 6 === 0 && <text x={xc(i)} y={height - 8} fontSize="9" fill="#6b7280" textAnchor="middle">{c.time}</text>}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function SimulateView({ sim }) {
  const [showTable, setShowTable] = useState(false);
  const an = sim.analysis; const t = sim.trade;
  const hhmm = (s) => (s ? String(s).slice(-5) : null);
  return (
    <div className="space-y-4">
      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
          <span className="text-gray-100 font-semibold">{sim.symbol} · {sim.date}</span>
          {an && <>
            <span className="text-gray-400">First 3: {an.colors.map((c, i) => <span key={i} className={c === 'green' ? 'text-emerald-400' : c === 'red' ? 'text-red-400' : 'text-gray-500'}>{c === 'green' ? '🟢' : c === 'red' ? '🔴' : '⚪'}</span>)}</span>
            <span className="text-gray-400">Bias <strong className={an.bias === 'call' ? 'text-emerald-400' : an.bias === 'put' ? 'text-red-400' : 'text-gray-500'}>{an.bias ? (an.bias === 'call' ? 'LONG' : 'SHORT') : 'NO TRADE (mixed)'}</strong></span>
            <span className="text-gray-400">4th High <strong className="text-gray-200">{NUM(an.fourth_high)}</strong></span>
            <span className="text-gray-400">4th Low <strong className="text-gray-200">{NUM(an.fourth_low)}</strong></span>
          </>}
        </div>
      </div>

      {an && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-3">
          <div className="flex items-center justify-between mb-2 px-1">
            <div className="text-sm font-semibold text-gray-200">{sim.symbol} · 5m</div>
            <div className="flex items-center gap-4 text-[11px] text-gray-400">
              <span>--- 4th High {NUM(an.fourth_high, 1)}</span><span>--- 4th Low {NUM(an.fourth_low, 1)}</span>
              {t?.breakout_time && <span>▲ Breakout <strong className={t.side === 'LONG' ? 'text-emerald-400' : 'text-red-400'}>{hhmm(t.breakout_time)}</strong></span>}
            </div>
          </div>
          <CandleChart timeline={sim.timeline} an={an} trade={t && t.side ? t : null} />
        </div>
      )}

      {t && t.side && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
          <div className="text-sm font-semibold text-gray-200 mb-3">Trade</div>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            {[['Signal', <><SideBadge s={t.side} /> <span className="text-gray-500 text-xs">@ {t.breakout_time}</span></>],
              ['Product', <span className="text-gray-200">{t.product}</span>],
              ['Qty', <span className="text-gray-100">{INT(t.qty)}</span>],
              ['Entry', <span className="text-gray-100">₹{NUM(t.entry)}</span>],
              ['Target / SL', <><span className="text-emerald-400">₹{NUM(t.target)}</span> <span className="text-gray-600">/</span> <span className="text-red-400">₹{NUM(t.sl)}</span></>],
              ['Exit', t.exit == null ? <span className="text-gray-400">—</span> : <><span className="text-gray-100">₹{NUM(t.exit)}</span> <span className={`text-xs ${stColor(t.exit_reason)}`}>({t.exit_reason})</span>{t.exit_time ? <span className="text-gray-500 text-xs"> @ {t.exit_time}</span> : null}</>],
              ['Hold', <span className="text-gray-200">{t.hold_label ?? (t.open ? 'open' : '—')}</span>],
              ['MTM', <span className={`font-semibold ${t.mtm >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>₹{NUM(t.mtm, 0)}</span>],
              ['Max Profit / Loss', <><span className="text-emerald-400">₹{NUM(t.max_profit, 0)}</span> <span className="text-gray-600">/</span> <span className="text-red-400">₹{NUM(t.max_loss, 0)}</span></>]].map(([k, v]) => (
              <div key={k} className="bg-surface-3/40 border border-surface-3 rounded-lg px-3 py-2"><div className="text-[10px] uppercase tracking-wide text-gray-500 mb-0.5">{k}</div><div className="text-sm">{v}</div></div>
            ))}
          </div>
        </div>
      )}
      {(!t || !t.side) && <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 text-sm text-gray-400">{an && !an.bias ? 'No trade — first 3 candles were mixed.' : 'No breakout of the 4th-candle level during the day.'}</div>}

      <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
        <button onClick={() => setShowTable((v) => !v)} className="w-full px-3 py-2 flex items-center justify-between text-sm font-semibold text-gray-300 hover:text-white"><span>5-min candle table</span><span className="text-xs text-gray-500">{showTable ? 'Hide ▲' : 'Show ▼'}</span></button>
        {showTable && (
          <div className="overflow-x-auto max-h-[360px] border-t border-surface-3"><table className="w-full text-xs whitespace-nowrap">
            <thead className="bg-surface-3 text-gray-400 sticky top-0"><tr>{['#', 'Time', 'Open', 'High', 'Low', 'Close', 'Color'].map((h) => <th key={h} className="px-2.5 py-1.5 text-right first:text-left font-semibold">{h}</th>)}</tr></thead>
            <tbody>{(sim.timeline || []).map((c, i) => (
              <tr key={i} className={`border-t border-surface-3/30 ${i === 3 ? 'bg-brand-600/10' : ''}`}>
                <td className="px-2.5 py-1 text-left text-gray-500">{i + 1}{i === 3 ? ' ◄4th' : ''}</td>
                <td className="px-2.5 py-1 text-left text-gray-300">{c.time}</td>
                <td className="px-2.5 py-1 text-right text-gray-400">{NUM(c.open)}</td>
                <td className="px-2.5 py-1 text-right text-gray-400">{NUM(c.high)}</td>
                <td className="px-2.5 py-1 text-right text-gray-400">{NUM(c.low)}</td>
                <td className="px-2.5 py-1 text-right text-gray-200">{NUM(c.close)}</td>
                <td className={`px-2.5 py-1 text-right font-semibold ${c.color === 'green' ? 'text-emerald-400' : c.color === 'red' ? 'text-red-400' : 'text-gray-500'}`}>{c.color}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </div>
    </div>
  );
}
