import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Play, Square, Settings2, ChevronDown, ChevronUp, RefreshCw,
  TrendingUp, CheckCircle2, XCircle, AlertCircle, Activity,
  Upload, List, Zap, ShoppingCart, Pencil, LogOut, FileText, Loader2, FlaskConical,
} from 'lucide-react';
import { api } from '../api';

const REFRESH_MS = 2_000;

const GLOBAL_STATE_STYLE = {
  IDLE:      { bg: 'bg-gray-600/20',  text: 'text-gray-400',  label: 'Idle' },
  RUNNING:   { bg: 'bg-blue-600/20',  text: 'text-blue-400',  label: 'Running' },
  COMPLETED: { bg: 'bg-green-600/20', text: 'text-green-400', label: 'Completed' },
};

const STOCK_STATE_STYLE = {
  WATCHING:      { bg: 'bg-gray-700/40',   text: 'text-gray-400',   label: 'Watching' },
  ARMED:         { bg: 'bg-amber-600/20',  text: 'text-amber-400',  label: 'Armed (vol)' },
  ORDER_PLACED:  { bg: 'bg-yellow-600/20', text: 'text-yellow-400', label: 'Order Placed' },
  POSITION_OPEN: { bg: 'bg-blue-600/20',   text: 'text-blue-400',   label: 'In Position' },
  SQUARED_OFF:   { bg: 'bg-green-600/20',  text: 'text-green-400',  label: 'Squared Off' },
  TARGET_HIT:    { bg: 'bg-emerald-600/20',text: 'text-emerald-400',label: 'Target Hit' },
  SL_HIT:        { bg: 'bg-red-600/20',    text: 'text-red-400',    label: 'SL Hit' },
  MANUAL_EXIT:   { bg: 'bg-purple-600/20', text: 'text-purple-400', label: 'Manual Exit' },
  SKIP:          { bg: 'bg-gray-700/20',   text: 'text-gray-500',   label: 'Skipped' },
  ENTRY_FAILED:  { bg: 'bg-orange-600/20', text: 'text-orange-400', label: 'Entry Failed' },
};

const INR = (v, d = 2) =>
  (v ?? 0).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });

const fmtVol = (v) => {
  if (!v) return '—';
  if (v >= 1e7) return `${(v / 1e7).toFixed(2)}Cr`;
  if (v >= 1e5) return `${(v / 1e5).toFixed(2)}L`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return `${v}`;
};

function Card({ title, icon: Icon, children, className = '', right = null }) {
  return (
    <div className={`bg-surface-2 border border-surface-3 rounded-xl p-4 ${className}`}>
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2 text-gray-400 text-xs font-medium uppercase tracking-wider">
          {Icon && <Icon className="w-3.5 h-3.5" />}
          {title}
        </div>
        {right}
      </div>
      {children}
    </div>
  );
}

function StatChip({ label, value, color = 'text-gray-200' }) {
  return (
    <div className="bg-surface-3/40 rounded-lg px-3 py-2 text-center">
      <div className={`text-base font-bold ${color}`}>{value}</div>
      <div className="text-gray-500 text-xs mt-0.5">{label}</div>
    </div>
  );
}

function ConfigField({ label, type, value, onChange, hint, step }) {
  return (
    <div>
      <label className="block text-xs text-gray-400 mb-1">{label}</label>
      <input
        type={type}
        step={step}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-blue-500/60"
      />
      {hint && <p className="text-gray-600 text-[10px] mt-0.5">{hint}</p>}
    </div>
  );
}

const DEFAULT_CONFIG = {
  capital_per_stock: 20000,
  target_points: 10,
  sl_points: 10,
  volume_filter: false,
  max_positions: 5,
  lookback_days: 5,
  entry_cutoff: '15:00',
  squareoff_time: '15:15',
  exchange: 'NSE',
  allow_reentry: false,
  max_reentries: 1,
};

export default function Strategy10() {
  const [status, setStatus] = useState(null);
  const [stocks, setStocks] = useState([]);
  const [listMeta, setListMeta] = useState({ filename: null, uploaded_at: null });
  const [config, setConfig] = useState(DEFAULT_CONFIG);
  const [configOpen, setConfigOpen] = useState(true);
  const [history, setHistory] = useState([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const [manual, setManual] = useState({ symbol: '', capital: '', sl_points: '', target_points: '' });
  const [editSym, setEditSym] = useState(null);
  const [editVals, setEditVals] = useState({ sl_price: '', target_price: '' });

  // Backtest
  const [btDate, setBtDate] = useState('');
  const [btDays, setBtDays] = useState(5);
  const [btLoading, setBtLoading] = useState(false);
  const [btResult, setBtResult] = useState(null);

  const fileRef = useRef(null);
  // Seed the config form from the server only ONCE. After that the form is
  // user-owned until they Save — otherwise the 2s status poll would clobber
  // every keystroke with the server's saved values.
  const configInit = useRef(false);

  const showMsg = (msg, isError = false) => {
    if (isError) { setError(msg); setTimeout(() => setError(''), 4500); }
    else { setSuccess(msg); setTimeout(() => setSuccess(''), 3000); }
  };

  const fetchAll = useCallback(async () => {
    try {
      const [st, sk] = await Promise.all([
        api.getStrategy10Status().catch(() => null),
        api.getStrategy10Stocks().catch(() => null),
      ]);
      if (st) {
        setStatus(st);
        if (st.config && !configInit.current) {
          setConfig((p) => ({ ...DEFAULT_CONFIG, ...p, ...st.config }));
          configInit.current = true;
        }
      }
      if (sk) {
        setStocks(sk.stocks || []);
        setListMeta({ filename: sk.list_filename, uploaded_at: sk.list_uploaded_at });
      }
    } catch {}
  }, []);

  const fetchHistory = useCallback(async () => {
    try {
      const data = await api.strategy10History();
      setHistory((data?.trades || []).slice().reverse());
    } catch {}
  }, []);

  useEffect(() => {
    fetchAll();
    fetchHistory();
    const id = setInterval(fetchAll, REFRESH_MS);
    return () => clearInterval(id);
  }, [fetchAll, fetchHistory]);

  const handleUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    try {
      const res = await api.strategy10UploadStocks(fd);
      if (res.status === 'ok') {
        showMsg(`Uploaded ${res.filename} — ${res.stock_count} stocks loaded.`);
        fetchAll();
      } else showMsg(res.message || 'Upload failed', true);
    } catch (err) {
      showMsg(err.message || 'Upload failed', true);
    } finally {
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const handleStart = async () => {
    setLoading(true);
    try {
      const data = await api.strategy10Start(config);
      setStatus(data);
      showMsg('Strategy 10 started — computing levels…');
    } catch (e) { showMsg(e.message || 'Start failed', true); }
    finally { setLoading(false); }
  };

  const handleStop = async () => {
    setLoading(true);
    try {
      const data = await api.strategy10Stop();
      setStatus(data);
      showMsg('Strategy 10 stopped.');
    } catch (e) { showMsg(e.message || 'Stop failed', true); }
    finally { setLoading(false); }
  };

  const handleSaveConfig = async () => {
    try {
      await api.strategy10UpdateConfig(config);
      showMsg('Config saved.');
    } catch (e) { showMsg(e.message || 'Config update failed', true); }
  };

  const handleRefreshStocks = async () => {
    setRefreshing(true);
    try {
      await api.strategy10RefreshStocks();
      showMsg('Levels recomputing in background.');
      setTimeout(fetchAll, 2000);
    } catch (e) { showMsg(e.message || 'Refresh failed', true); }
    finally { setRefreshing(false); }
  };

  const handleManualOrder = async () => {
    if (!manual.symbol.trim()) { showMsg('Enter / pick a symbol', true); return; }
    try {
      const res = await api.strategy10ManualOrder({
        symbol: manual.symbol.trim().toUpperCase(),
        capital: manual.capital ? parseFloat(manual.capital) : null,
        sl_points: manual.sl_points ? parseFloat(manual.sl_points) : null,
        target_points: manual.target_points ? parseFloat(manual.target_points) : null,
      });
      if (res.status === 'ok') {
        showMsg(`Manual BUY placed for ${res.symbol}.`);
        setManual({ symbol: '', capital: '', sl_points: '', target_points: '' });
        fetchAll();
      } else showMsg(res.message || 'Order failed', true);
    } catch (e) { showMsg(e.message || 'Order failed', true); }
  };

  const handleExit = async (symbol) => {
    try {
      const res = await api.strategy10ManualExit(symbol);
      if (res.status === 'ok') { showMsg(`Exit placed for ${symbol}.`); fetchAll(); }
      else showMsg(res.message || 'Exit failed', true);
    } catch (e) { showMsg(e.message || 'Exit failed', true); }
  };

  const runBacktest = async () => {
    setBtLoading(true);
    setBtResult(null);
    try {
      const res = await api.strategy10Backtest(btDate || null);
      if (res.status === 'ok') setBtResult({ ...res, mode: 'single' });
      else showMsg(res.message || 'Backtest failed', true);
    } catch (e) { showMsg(e.message || 'Backtest failed', true); }
    finally { setBtLoading(false); }
  };

  const runBacktestMulti = async (daysArg) => {
    const d = typeof daysArg === 'number' ? daysArg : btDays;
    setBtLoading(true);
    setBtResult(null);
    try {
      const res = await api.strategy10BacktestMulti(d);
      if (res.status === 'ok') setBtResult({ ...res, mode: 'multi' });
      else showMsg(res.message || 'Backtest failed', true);
    } catch (e) { showMsg(e.message || 'Backtest failed', true); }
    finally { setBtLoading(false); }
  };

  const openEdit = (s) => {
    setEditSym(s.symbol);
    setEditVals({ sl_price: s.sl_price ?? '', target_price: s.target_price ?? '' });
  };

  const handleSaveEdit = async () => {
    try {
      const res = await api.strategy10ManualModify({
        symbol: editSym,
        sl_price: editVals.sl_price ? parseFloat(editVals.sl_price) : null,
        target_price: editVals.target_price ? parseFloat(editVals.target_price) : null,
      });
      if (res.status === 'ok') { showMsg(`Updated SL/Target for ${editSym}.`); setEditSym(null); fetchAll(); }
      else showMsg(res.message || 'Modify failed', true);
    } catch (e) { showMsg(e.message || 'Modify failed', true); }
  };

  const isActive = status?.is_active || false;
  const globalState = status?.state || 'IDLE';
  const gStyle = GLOBAL_STATE_STYLE[globalState] || GLOBAL_STATE_STYLE.IDLE;
  const posOpen = status?.positions_open ?? 0;
  const totalPnl = status?.total_pnl ?? 0;
  const levelsReady = status?.levels_ready;
  const watchingCount = stocks.filter((s) => ['WATCHING', 'ARMED'].includes(s.state)).length;
  const closedCount = stocks.filter((s) => ['SQUARED_OFF', 'TARGET_HIT', 'SL_HIT', 'MANUAL_EXIT'].includes(s.state)).length;
  const candidates = stocks.filter((s) => s.open_above_level === true).length;

  // Surface the interesting rows first: live positions → armed → breakout
  // candidates → watching → closed → skipped.
  const SORT_RANK = { POSITION_OPEN: 0, ORDER_PLACED: 0, ARMED: 1, WATCHING: 3 };
  const rankOf = (s) =>
    s.state === 'WATCHING' && s.open_above_level ? 2 : (SORT_RANK[s.state] ?? 4);
  const sortedStocks = [...stocks].sort((a, b) => rankOf(a) - rankOf(b));

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-7xl mx-auto">
      {/* ── Header ── */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold text-gray-100">Strategy 10</h1>
            <span className="px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 text-xs font-semibold border border-emerald-500/30">
              NSE · Equity · Intraday
            </span>
          </div>
          <p className="text-gray-500 text-sm mt-0.5">
            Buy when today&apos;s open &gt; max first-hour high of last {config.lookback_days} days
            {config.volume_filter ? ', confirmed by volume' : ''}. Auto square-off at {config.squareoff_time}.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input ref={fileRef} type="file" accept=".csv" onChange={handleUpload} className="hidden" />
          <button onClick={() => fileRef.current?.click()}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-surface-3 text-gray-300 hover:bg-surface-3/80 border border-surface-4 transition">
            <Upload className="w-3.5 h-3.5" /> Upload CSV
          </button>
          <button onClick={handleRefreshStocks} disabled={refreshing}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-surface-3 text-gray-300 hover:bg-surface-3/80 border border-surface-4 disabled:opacity-40 transition">
            <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} /> Refresh Levels
          </button>
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

      {error && (
        <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-red-400 text-sm">
          <AlertCircle className="w-4 h-4 shrink-0" /> {error}
        </div>
      )}
      {success && (
        <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-4 py-2 text-emerald-400 text-sm">
          <CheckCircle2 className="w-4 h-4 shrink-0" /> {success}
        </div>
      )}
      {isActive && levelsReady === false && (
        <div className="flex items-center gap-2 bg-blue-500/10 border border-blue-500/30 rounded-lg px-4 py-2 text-blue-400 text-sm">
          <Loader2 className="w-4 h-4 shrink-0 animate-spin" /> Computing 5-day first-hour levels…
        </div>
      )}

      {/* ── Summary ── */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        <StatChip label="Status" value={gStyle.label} color={`${gStyle.text} font-semibold`} />
        <StatChip label="Positions Open" value={posOpen} color={posOpen > 0 ? 'text-blue-400' : 'text-gray-400'} />
        <StatChip label="Breakout Candidates" value={candidates} color={candidates > 0 ? 'text-emerald-400' : 'text-gray-400'} />
        <StatChip label="Total P&L" value={`₹${INR(totalPnl)}`}
          color={totalPnl > 0 ? 'text-emerald-400' : totalPnl < 0 ? 'text-red-400' : 'text-gray-400'} />
        <StatChip label="Stocks Loaded" value={stocks.length} />
      </div>

      {/* ── Configuration (top, like other strategies) ── */}
      <Card title="Configuration" icon={Settings2}
        right={
          <button onClick={() => setConfigOpen((o) => !o)} className="text-gray-500 hover:text-gray-300">
            {configOpen ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          </button>
        }
      >
        {configOpen && (
          <div className="space-y-4 mt-1">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
              <ConfigField label="Capital per Stock (₹)" type="number" value={config.capital_per_stock}
                onChange={(v) => setConfig((c) => ({ ...c, capital_per_stock: parseFloat(v) || 0 }))}
                hint="Qty = floor(capital / entry price)" />
              <ConfigField label="Target (points)" type="number" value={config.target_points}
                onChange={(v) => setConfig((c) => ({ ...c, target_points: parseFloat(v) || 0 }))} />
              <ConfigField label="Stop-Loss (points)" type="number" value={config.sl_points}
                onChange={(v) => setConfig((c) => ({ ...c, sl_points: parseFloat(v) || 0 }))} />
              <ConfigField label="Max Concurrent Positions" type="number" value={config.max_positions}
                onChange={(v) => setConfig((c) => ({ ...c, max_positions: parseInt(v) || 1 }))} />
              <ConfigField label="Lookback Days" type="number" value={config.lookback_days}
                onChange={(v) => setConfig((c) => ({ ...c, lookback_days: parseInt(v) || 5 }))}
                hint="Days of first-hour highs/volumes" />
              <ConfigField label="Entry Cutoff (HH:MM)" type="text" value={config.entry_cutoff}
                onChange={(v) => setConfig((c) => ({ ...c, entry_cutoff: v }))}
                hint="Latest time to take a fresh entry (set early, e.g. 09:30, to only catch the open)" />
              <ConfigField label="Auto Square-Off (HH:MM)" type="text" value={config.squareoff_time}
                onChange={(v) => setConfig((c) => ({ ...c, squareoff_time: v }))} />
              <ConfigField label="Exchange" type="text" value={config.exchange}
                onChange={(v) => setConfig((c) => ({ ...c, exchange: v.toUpperCase() }))} />
              <div className="flex items-center gap-2 pt-5">
                <button
                  onClick={() => setConfig((c) => ({ ...c, volume_filter: !c.volume_filter }))}
                  className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium border transition ${
                    config.volume_filter
                      ? 'bg-amber-600/20 text-amber-400 border-amber-500/40'
                      : 'bg-surface-3 text-gray-400 border-surface-4'
                  }`}
                >
                  <Zap className="w-3.5 h-3.5" />
                  Volume Filter: {config.volume_filter ? 'ON' : 'OFF'}
                </button>
              </div>
              <div className="flex items-center gap-2 pt-5">
                <button
                  onClick={() => setConfig((c) => ({ ...c, allow_reentry: !c.allow_reentry }))}
                  className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium border transition ${
                    config.allow_reentry
                      ? 'bg-purple-600/20 text-purple-400 border-purple-500/40'
                      : 'bg-surface-3 text-gray-400 border-surface-4'
                  }`}
                >
                  <RefreshCw className="w-3.5 h-3.5" />
                  Re-entry: {config.allow_reentry ? 'ON' : 'OFF'}
                </button>
              </div>
              {config.allow_reentry && (
                <ConfigField label="Max Re-entries" type="number" value={config.max_reentries}
                  onChange={(v) => setConfig((c) => ({ ...c, max_reentries: Math.max(0, parseInt(v) || 0) }))}
                  hint="Re-entries allowed per stock after SL/Target" />
              )}
            </div>
            <p className="text-gray-600 text-[11px]">
              When the volume filter is ON, an armed stock only buys once today&apos;s live cumulative
              volume exceeds the <strong>average</strong> of the last {config.lookback_days} days&apos;
              first-hour volumes. Max <strong>{config.max_positions}</strong> positions can be open at once;
              extra breakout candidates wait for a free slot.
              {config.allow_reentry
                ? ` Re-entry is ON — a stock can re-enter up to ${config.max_reentries}× after its SL/Target is hit.`
                : ' Re-entry is OFF — a stock trades once per day.'}
            </p>
            <div className="flex justify-end pt-1">
              <button onClick={handleSaveConfig}
                className="px-4 py-1.5 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition font-medium">
                Save Config
              </button>
            </div>
          </div>
        )}
      </Card>

      {/* ── Manual equity desk ── */}
      <Card title="Manual Equity Desk" icon={ShoppingCart}>
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 items-end">
          <div className="col-span-2 sm:col-span-1">
            <label className="block text-xs text-gray-400 mb-1">Symbol</label>
            <input value={manual.symbol}
              onChange={(e) => setManual((m) => ({ ...m, symbol: e.target.value.toUpperCase() }))}
              placeholder="RELIANCE"
              className="w-full bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-blue-500/60" />
          </div>
          <ConfigField label={`Capital (₹) — def ${config.capital_per_stock}`} type="number"
            value={manual.capital} onChange={(v) => setManual((m) => ({ ...m, capital: v }))} />
          <ConfigField label={`SL pts — def ${config.sl_points}`} type="number"
            value={manual.sl_points} onChange={(v) => setManual((m) => ({ ...m, sl_points: v }))} />
          <ConfigField label={`Target pts — def ${config.target_points}`} type="number"
            value={manual.target_points} onChange={(v) => setManual((m) => ({ ...m, target_points: v }))} />
          <button onClick={handleManualOrder}
            className="flex items-center justify-center gap-1.5 px-4 py-2 text-sm rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-semibold transition">
            <ShoppingCart className="w-4 h-4" /> Buy (MIS)
          </button>
        </div>
        <p className="text-gray-600 text-[11px] mt-2">
          Manual buys use hidden SL/Target (same as automated) and appear in the same trade history.
          Click <strong>Buy</strong> on any row below to pre-fill the symbol here.
        </p>
      </Card>

      {/* ── Columnar stock view ── */}
      <Card title="Stock List — Live" icon={List}
        right={
          <div className="flex items-center gap-3 text-xs text-gray-500">
            {listMeta.filename && (
              <span className="flex items-center gap-1"><FileText className="w-3 h-3" /> {listMeta.filename}</span>
            )}
            <span>{watchingCount} watching · {posOpen} open · {closedCount} closed</span>
          </div>
        }
      >
        {stocks.length === 0 ? (
          <div className="text-center text-gray-500 text-sm py-8">
            No stocks loaded. Click <strong>Upload CSV</strong> to load your equity list
            (columns: <code>symbol,exchange</code>).
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-surface-3">
                  {['Symbol', '5-Day Level', 'Avg 1st-hr Vol', 'Cur Vol', 'Open', 'Open>Lvl', 'LTP', 'State', 'Qty', 'Entry', 'SL', 'Target', 'P&L', ''].map((h) => (
                    <th key={h} className="text-gray-500 font-medium text-left pb-2 pr-3 whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sortedStocks.map((s) => {
                  const sStyle = STOCK_STATE_STYLE[s.state] || STOCK_STATE_STYLE.WATCHING;
                  const isOpen = s.state === 'POSITION_OPEN';
                  const pnl = s.pnl;
                  const isCandidate = s.open_above_level === true && ['WATCHING', 'ARMED'].includes(s.state);
                  const volPct = s.avg_volume > 0 && s.live_volume > 0
                    ? Math.round((s.live_volume / s.avg_volume) * 100) : null;
                  const volMet = volPct != null && volPct >= 100;
                  return (
                    <tr key={s.symbol}
                      className={`border-b border-surface-3/40 hover:bg-surface-3/20 ${isCandidate ? 'bg-emerald-500/5' : ''}`}>
                      <td className="py-2 pr-3 font-semibold text-gray-200 whitespace-nowrap">
                        {s.symbol}
                        {s.manual && <span className="ml-1 text-[9px] text-purple-400 align-top">M</span>}
                      </td>
                      <td className="py-2 pr-3 text-gray-300">{s.level ? `₹${INR(s.level)}` : '—'}</td>
                      <td className="py-2 pr-3 text-gray-400">{fmtVol(s.avg_volume)}</td>
                      <td className="py-2 pr-3">
                        <span className={volMet ? 'text-emerald-400 font-medium' : 'text-gray-300'}>
                          {fmtVol(s.live_volume)}
                        </span>
                        {volPct != null && (
                          <span className={`ml-1 text-[9px] ${volMet ? 'text-emerald-500' : 'text-gray-600'}`}>
                            {volPct}%
                          </span>
                        )}
                      </td>
                      <td className="py-2 pr-3 text-gray-300">{s.today_open ? `₹${INR(s.today_open)}` : '—'}</td>
                      <td className="py-2 pr-3">
                        {s.open_above_level == null ? <span className="text-gray-600">—</span>
                          : s.open_above_level
                            ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                            : <XCircle className="w-3.5 h-3.5 text-gray-500" />}
                      </td>
                      <td className="py-2 pr-3 font-medium text-gray-100">{s.ltp ? `₹${INR(s.ltp)}` : '—'}</td>
                      <td className="py-2 pr-3">
                        <span className={`px-1.5 py-0.5 rounded text-[11px] font-medium ${sStyle.bg} ${sStyle.text}`}>
                          {s.skip_reason && s.state === 'SKIP' ? s.skip_reason : sStyle.label}
                        </span>
                      </td>
                      <td className="py-2 pr-3 text-gray-400">{s.quantity || '—'}</td>
                      <td className="py-2 pr-3 text-gray-300">{s.entry_price ? `₹${INR(s.entry_price)}` : '—'}</td>
                      <td className="py-2 pr-3 text-red-400/80">{s.sl_price ? `₹${INR(s.sl_price)}` : '—'}</td>
                      <td className="py-2 pr-3 text-emerald-400/80">{s.target_price ? `₹${INR(s.target_price)}` : '—'}</td>
                      <td className="py-2 pr-3">
                        {pnl != null ? (
                          <span className={pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                            {pnl >= 0 ? '+' : ''}₹{INR(pnl)}
                          </span>
                        ) : <span className="text-gray-600">—</span>}
                      </td>
                      <td className="py-2 pr-1 whitespace-nowrap">
                        {isOpen ? (
                          <div className="flex items-center gap-1">
                            <button onClick={() => openEdit(s)} title="Modify SL/Target"
                              className="p-1 rounded bg-surface-3 hover:bg-surface-4 text-gray-300">
                              <Pencil className="w-3 h-3" />
                            </button>
                            <button onClick={() => handleExit(s.symbol)} title="Exit now"
                              className="p-1 rounded bg-red-600/80 hover:bg-red-500 text-white">
                              <LogOut className="w-3 h-3" />
                            </button>
                          </div>
                        ) : (
                          <button onClick={() => setManual((m) => ({ ...m, symbol: s.symbol }))} title="Manual buy"
                            className="p-1 rounded bg-blue-600/80 hover:bg-blue-500 text-white">
                            <ShoppingCart className="w-3 h-3" />
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Inline SL/Target editor */}
        {editSym && (
          <div className="mt-3 p-3 rounded-lg bg-surface-3/40 border border-surface-3 flex flex-wrap items-end gap-3">
            <div className="text-sm text-gray-300 font-medium">Modify {editSym}</div>
            <ConfigField label="SL Price (₹)" type="number" step="0.05" value={editVals.sl_price}
              onChange={(v) => setEditVals((e) => ({ ...e, sl_price: v }))} />
            <ConfigField label="Target Price (₹)" type="number" step="0.05" value={editVals.target_price}
              onChange={(v) => setEditVals((e) => ({ ...e, target_price: v }))} />
            <button onClick={handleSaveEdit}
              className="px-3 py-1.5 text-sm bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg">Save</button>
            <button onClick={() => setEditSym(null)}
              className="px-3 py-1.5 text-sm bg-surface-4 hover:bg-surface-3 text-gray-300 rounded-lg">Cancel</button>
          </div>
        )}
      </Card>

      {/* ── Backtest ── */}
      <Card title="Backtest" icon={FlaskConical}>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs text-gray-400 mb-1">Date (blank = latest)</label>
            <input type="date" value={btDate} onChange={(e) => setBtDate(e.target.value)}
              className="bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-blue-500/60" />
          </div>
          <button onClick={runBacktest} disabled={btLoading}
            className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-semibold disabled:opacity-50 transition">
            {btLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <FlaskConical className="w-4 h-4" />} Run 1 Day
          </button>
          <div className="h-8 w-px bg-surface-3 mx-1 hidden sm:block" />
          <div>
            <label className="block text-xs text-gray-400 mb-1">Days (max 30)</label>
            <input type="number" min="1" max="30" value={btDays}
              onChange={(e) => setBtDays(Math.max(1, Math.min(30, parseInt(e.target.value) || 1)))}
              className="w-20 bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-blue-500/60" />
          </div>
          <button onClick={() => runBacktestMulti()} disabled={btLoading}
            className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-surface-3 hover:bg-surface-4 text-gray-200 border border-surface-4 font-semibold disabled:opacity-50 transition">
            {btLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <FlaskConical className="w-4 h-4" />} Run Multi-Day
          </button>
          <button onClick={() => { setBtDays(30); runBacktestMulti(30); }} disabled={btLoading}
            className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-blue-600/15 hover:bg-blue-600/25 text-blue-400 border border-blue-500/30 font-semibold disabled:opacity-50 transition">
            <FlaskConical className="w-4 h-4" /> Run 30 Days
          </button>
          <p className="text-gray-600 text-[11px] basis-full">
            Replays real minute data: enters at open when Open &gt; level{config.volume_filter ? ' (and live volume beats the 5-day average)' : ''},
            then exits on hidden Target/SL or {config.squareoff_time} square-off. SL is checked before Target within a candle (conservative).
            Each signal is evaluated independently (the live {config.max_positions}-position cap isn&apos;t applied in backtest). Large lists × 30 days can take a while.
          </p>
        </div>

        {btResult && (
          <div className="mt-4 space-y-3">
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
              <StatChip label={btResult.mode === 'multi' ? `${btResult.days} Days` : btResult.date}
                value={btResult.mode === 'multi' ? 'Window' : 'Day'} />
              <StatChip label="Trades Taken" value={btResult.summary.trades_taken} />
              <StatChip label="Wins / Losses" value={`${btResult.summary.wins}/${btResult.summary.losses}`} />
              <StatChip label="Win Rate" value={`${btResult.summary.win_rate}%`}
                color={btResult.summary.win_rate >= 50 ? 'text-emerald-400' : 'text-gray-300'} />
              <StatChip label="Total P&L" value={`₹${INR(btResult.summary.total_pnl)}`}
                color={btResult.summary.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'} />
            </div>

            {btResult.mode === 'multi' && btResult.daily?.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {btResult.daily.map((d) => (
                  <div key={d.date} className="px-2.5 py-1.5 rounded-lg bg-surface-3/40 text-xs">
                    <span className="text-gray-400">{d.date}</span>{' '}
                    <span className={d.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                      {d.total_pnl >= 0 ? '+' : ''}₹{INR(d.total_pnl, 0)}
                    </span>
                    <span className="text-gray-600"> · {d.trades_taken}t</span>
                  </div>
                ))}
              </div>
            )}

            <div className="overflow-x-auto max-h-80 overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-surface-2">
                  <tr className="border-b border-surface-3">
                    {[...(btResult.mode === 'multi' ? ['Date'] : []), 'Symbol', 'Level', 'Open', 'Entry', 'Exit', 'Qty', 'Result', 'P&L'].map((h) => (
                      <th key={h} className="text-gray-500 font-medium text-left pb-2 pr-3 whitespace-nowrap">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {btResult.trades.filter((t) => t.result !== 'NO_DATA').map((t, i) => {
                    const pnl = t.pnl ?? 0;
                    const noTrade = t.result === 'NO_TRADE';
                    return (
                      <tr key={i} className="border-b border-surface-3/30">
                        {btResult.mode === 'multi' && <td className="py-1.5 pr-3 text-gray-500">{t.date}</td>}
                        <td className="py-1.5 pr-3 font-medium text-gray-200">{t.symbol}</td>
                        <td className="py-1.5 pr-3 text-gray-400">{t.level ? `₹${INR(t.level)}` : '—'}</td>
                        <td className="py-1.5 pr-3 text-gray-400">{t.day_open ? `₹${INR(t.day_open)}` : '—'}</td>
                        <td className="py-1.5 pr-3 text-gray-300">{t.entry ? `₹${INR(t.entry)}` : '—'}</td>
                        <td className="py-1.5 pr-3 text-gray-300">{t.exit ? `₹${INR(t.exit)}` : '—'}</td>
                        <td className="py-1.5 pr-3 text-gray-400">{t.qty ?? '—'}</td>
                        <td className="py-1.5 pr-3">
                          <span className={
                            noTrade ? 'text-gray-500'
                            : t.result === 'TARGET_HIT' ? 'text-emerald-400'
                            : t.result === 'SL_HIT' ? 'text-red-400' : 'text-gray-300'
                          }>{noTrade ? (t.reason || 'no trade') : t.result}</span>
                        </td>
                        <td className={`py-1.5 pr-3 font-medium ${noTrade ? 'text-gray-600' : pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {noTrade ? '—' : `${pnl >= 0 ? '+' : ''}₹${INR(pnl)}`}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Card>

      {/* ── Trade history ── */}
      <Card title={`Trade History (${history.length})`} icon={Activity}
        right={
          <button onClick={() => { setHistoryOpen((o) => !o); if (!historyOpen) fetchHistory(); }} className="text-gray-500 hover:text-gray-300">
            {historyOpen ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          </button>
        }
      >
        {historyOpen && (
          history.length === 0 ? (
            <p className="text-gray-500 text-sm py-4 text-center">No trades yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-surface-3">
                    {['Date', 'Symbol', 'Type', 'Entry', 'Exit', 'Qty', 'P&L', 'Reason'].map((h) => (
                      <th key={h} className="text-gray-500 font-medium text-left pb-2 pr-3 whitespace-nowrap">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {history.map((t, i) => {
                    const pnl = t.pnl ?? 0;
                    return (
                      <tr key={i} className="border-b border-surface-3/30 hover:bg-surface-3/20">
                        <td className="py-2 pr-3 text-gray-400">{t.date}</td>
                        <td className="py-2 pr-3 font-semibold text-gray-200">{t.symbol}</td>
                        <td className="py-2 pr-3 text-gray-400">{t.type || 'AUTO'}</td>
                        <td className="py-2 pr-3 text-gray-300">{t.entry_price ? `₹${INR(t.entry_price)}` : '—'}</td>
                        <td className="py-2 pr-3 text-gray-300">{t.exit_price ? `₹${INR(t.exit_price)}` : '—'}</td>
                        <td className="py-2 pr-3 text-gray-400">{t.quantity ?? '—'}</td>
                        <td className={`py-2 pr-3 font-semibold ${pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {pnl >= 0 ? '+' : ''}₹{INR(pnl)}
                        </td>
                        <td className="py-2 pr-3 text-gray-500">{t.exit_reason || '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        )}
      </Card>

      {/* ── How it works ── */}
      <Card title="How It Works" icon={TrendingUp}>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs text-gray-400">
          <div className="bg-surface-3/30 rounded-lg p-3 space-y-1">
            <div className="text-gray-200 font-semibold mb-1.5 flex items-center gap-1.5">
              <span className="w-5 h-5 rounded-full bg-blue-600/30 text-blue-400 flex items-center justify-center text-[10px] font-bold">1</span>
              Upload &amp; Levels
            </div>
            <p>Upload your equity list (saved in the database). For each stock, the
              <strong className="text-gray-300"> max of the last {config.lookback_days} days&apos; first-hour (9:15-10:15) highs</strong> becomes the breakout level.</p>
          </div>
          <div className="bg-surface-3/30 rounded-lg p-3 space-y-1">
            <div className="text-gray-200 font-semibold mb-1.5 flex items-center gap-1.5">
              <span className="w-5 h-5 rounded-full bg-blue-600/30 text-blue-400 flex items-center justify-center text-[10px] font-bold">2</span>
              Entry
            </div>
            <p>At open, if <strong className="text-emerald-400">Open &gt; Level</strong> → BUY MARKET (MIS).
              {config.volume_filter && <> With the volume filter ON, it waits for live volume to beat the 5-day average first.</>}</p>
            <p className="text-gray-500">Qty = capital ÷ price. No entry after {config.entry_cutoff}.</p>
          </div>
          <div className="bg-surface-3/30 rounded-lg p-3 space-y-1">
            <div className="text-gray-200 font-semibold mb-1.5 flex items-center gap-1.5">
              <span className="w-5 h-5 rounded-full bg-blue-600/30 text-blue-400 flex items-center justify-center text-[10px] font-bold">3</span>
              Exit
            </div>
            <p>Hidden <strong className="text-emerald-400">Target +{config.target_points}</strong> /
              <strong className="text-red-400"> SL -{config.sl_points}</strong> points, monitored on LTP.</p>
            <p className="text-gray-500">All positions auto square-off at {config.squareoff_time}.</p>
          </div>
        </div>
      </Card>
    </div>
  );
}
