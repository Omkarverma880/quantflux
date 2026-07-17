import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  LineChart, Line, XAxis, YAxis, ResponsiveContainer,
  Tooltip, CartesianGrid, ReferenceDot, Legend,
} from 'recharts';
import {
  Play, Square, Settings2, ChevronDown, ChevronUp,
  Shield, Target, TrendingUp, Zap, Activity,
  CheckCircle2, Clock, Info, Download, LineChart as LineChartIcon,
  ArrowUpCircle, ArrowDownCircle, RefreshCw,
} from 'lucide-react';
import { api } from '../api';

const REFRESH_MS = 1_500;

const STATE_STYLE = {
  IDLE:          { bg: 'bg-gray-600/20',   text: 'text-gray-400',   label: 'Idle' },
  ORDER_PLACED:  { bg: 'bg-yellow-600/20', text: 'text-yellow-400', label: 'Order Placed' },
  POSITION_OPEN: { bg: 'bg-blue-600/20',   text: 'text-blue-400',   label: 'Position Open' },
  COMPLETED:     { bg: 'bg-green-600/20',  text: 'text-green-400',  label: 'Completed' },
};

const TIMEFRAMES = [
  ['1 Minute', '1minute'], ['3 Minute', '3minute'], ['5 Minute', '5minute'],
  ['10 Minute', '10minute'], ['15 Minute', '15minute'], ['30 Minute', '30minute'],
  ['1 Hour', '1hour'], ['2 Hour', '2hour'], ['4 Hour', '4hour'],
  ['Daily', 'day'], ['Weekly', 'week'], ['Monthly', 'month'],
];

const OPTION_MODES = [
  ['ATM', 'ATM'], ['100 ITM', 'ITM_100'], ['200 ITM', 'ITM_200'], ['300 ITM', 'ITM_300'],
  ['100 OTM', 'OTM_100'], ['200 OTM', 'OTM_200'], ['300 OTM', 'OTM_300'],
];

const ATR_UPDATE = [['1 Minute', 1], ['3 Minutes', 3], ['5 Minutes', 5], ['10 Minutes', 10], ['15 Minutes', 15]];

function fmt(n, d = 2) {
  const v = Number(n);
  if (!Number.isFinite(v)) return '—';
  return v.toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
}

function signalColor(sig) {
  if (sig === 'BUY_CALL') return 'text-green-400';
  if (sig === 'BUY_PUT') return 'text-red-400';
  return 'text-yellow-400';
}
function signalBg(sig) {
  if (sig === 'BUY_CALL') return 'bg-green-500/15 border-green-500/30';
  if (sig === 'BUY_PUT') return 'bg-red-500/15 border-red-500/30';
  return 'bg-yellow-500/15 border-yellow-500/30';
}

function Card({ title, icon: Icon, children, className = '', right = null }) {
  return (
    <div className={`bg-surface-2 border border-surface-3 rounded-xl p-4 ${className}`}>
      <div className="flex items-center justify-between mb-2">
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

/* ─────────────────────────────────────────────────────
   Strategy Visualization Panel — NIFTY index + EMAs + arrows
   ───────────────────────────────────────────────────── */
function IndexChart({ series, markers }) {
  const data = useMemo(
    () => (series || []).map((p, i) => ({
      x: i, t: p.t, c: Number(p.c),
      ema_fast: Number(p.ema_fast), ema_slow: Number(p.ema_slow),
    })),
    [series],
  );

  const tIndex = useMemo(() => {
    const m = {};
    data.forEach((d) => { if (d.t) m[d.t.slice(0, 5)] = d.x; });
    return m;
  }, [data]);

  const [yMin, yMax] = useMemo(() => {
    const vals = [];
    data.forEach((d) => {
      [d.c, d.ema_fast, d.ema_slow].forEach((v) => { if (Number.isFinite(v) && v > 0) vals.push(v); });
    });
    if (!vals.length) return [0, 1];
    let lo = Math.min(...vals);
    let hi = Math.max(...vals);
    const pad = Math.max(2, (hi - lo) * 0.08);
    return [lo - pad, hi + pad];
  }, [data]);

  const dots = useMemo(() => {
    return (markers || []).map((m, i) => {
      const key = (m.time || '').slice(0, 5);
      const x = tIndex[key];
      if (x == null) return null;
      const isEntry = m.type === 'ENTRY';
      const isCall = m.side === 'CE';
      const color = isEntry ? (isCall ? '#22c55e' : '#ef4444') : '#a855f7';
      return { key: `${m.type}-${i}`, x, y: Number(m.spot), color, isEntry, isCall, m };
    }).filter(Boolean);
  }, [markers, tIndex]);

  if (!data.length) {
    return (
      <div className="h-80 flex items-center justify-center text-xs text-gray-500">
        Waiting for NIFTY index data (connect Zerodha & start the strategy)…
      </div>
    );
  }

  return (
    <div className="h-80">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, left: 4, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis dataKey="t" tick={{ fontSize: 10, fill: '#9ca3af' }} minTickGap={40} />
          <YAxis domain={[yMin, yMax]} tick={{ fontSize: 10, fill: '#9ca3af' }} width={56}
                 tickFormatter={(v) => fmt(v, 0)} />
          <Tooltip
            contentStyle={{ background: '#0f172a', border: '1px solid #1f2937', fontSize: 11 }}
            formatter={(v, name) => [fmt(v), name]}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Line type="monotone" dataKey="c" name="NIFTY" stroke="#e5e7eb" strokeWidth={1.4} dot={false} isAnimationActive={false} />
          <Line type="monotone" dataKey="ema_fast" name="Fast EMA" stroke="#22c55e" strokeWidth={1.6} dot={false} isAnimationActive={false} />
          <Line type="monotone" dataKey="ema_slow" name="Slow EMA" stroke="#3b82f6" strokeWidth={1.6} dot={false} isAnimationActive={false} />
          {dots.map((d) => (
            <ReferenceDot
              key={d.key} x={d.x} y={d.y} r={6}
              fill={d.color} stroke="#0f172a" strokeWidth={1.5} isFront
              label={{
                value: d.isEntry ? (d.isCall ? '▲' : '▼') : '✕',
                position: d.isEntry ? (d.isCall ? 'top' : 'bottom') : 'top',
                fill: d.color, fontSize: 12,
              }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ─────────────────────────────────────────────────────
   Backtest panel — single date / last-N-days + CSV export
   ───────────────────────────────────────────────────── */
const CSV_COLUMNS = [
  ['trade_no', 'Trade #'], ['date', 'Date'], ['time', 'Time'],
  ['index_price', 'Index Price'], ['ema_fast', 'EMA20'], ['ema_slow', 'EMA200'],
  ['signal', 'Signal'], ['strike', 'Strike'], ['option_name', 'Option Name'],
  ['entry_price', 'Entry'], ['exit_price', 'Exit'], ['qty', 'Qty'], ['lots', 'Lots'],
  ['stoploss', 'SL'], ['target', 'Target'], ['exit_reason', 'Exit Reason'],
  ['holding_time', 'Holding'], ['pnl', 'P&L'], ['running_equity', 'Equity'],
];

function BacktestPanel({ config }) {
  const [mode, setMode] = useState('single');
  const [tradeDate, setTradeDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [days, setDays] = useState(30);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState('');

  const run = useCallback(async () => {
    setRunning(true); setErr(''); setResult(null);
    try {
      const payload = { mode, config };
      if (mode === 'single') payload.trade_date = tradeDate;
      else payload.days = Number(days);
      const res = await api.strategy12Backtest(payload);
      if (res?.status === 'error') setErr(res.message || 'Backtest failed');
      else setResult(res);
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setRunning(false);
    }
  }, [mode, tradeDate, days, config]);

  const exportCsv = useCallback(() => {
    if (!result?.trades?.length) return;
    const header = CSV_COLUMNS.map(([, label]) => label).join(',');
    const lines = result.trades.map((t) =>
      CSV_COLUMNS.map(([k]) => {
        const v = t[k] ?? '';
        const s = String(v).replace(/"/g, '""');
        return /[",\n]/.test(s) ? `"${s}"` : s;
      }).join(','),
    );
    const csv = [header, ...lines].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `strategy12_backtest_${result.date_from || 'report'}_${result.date_to || ''}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }, [result]);

  const stats = result?.stats || {};

  return (
    <Card title="Backtest — 200 EMA Pull-Back" icon={LineChartIcon}
      right={
        result?.trades?.length ? (
          <button onClick={exportCsv}
            className="text-[11px] px-2 py-1 rounded border border-emerald-500/40 text-emerald-300 hover:bg-emerald-600/20">
            <Download className="w-3 h-3 inline mr-1" /> Export CSV
          </button>
        ) : null
      }
    >
      <div className="flex flex-wrap items-end gap-3 mb-3 text-xs">
        <label className="flex flex-col gap-1">
          <span className="text-gray-400">Mode</span>
          <select value={mode} onChange={(e) => setMode(e.target.value)}
            className="bg-surface-3 border border-surface-3 rounded px-2 py-1 text-white">
            <option value="single">Single date</option>
            <option value="multi">Last N days</option>
          </select>
        </label>
        {mode === 'single' ? (
          <label className="flex flex-col gap-1">
            <span className="text-gray-400">Date</span>
            <input type="date" value={tradeDate} onChange={(e) => setTradeDate(e.target.value)}
              className="bg-surface-3 border border-surface-3 rounded px-2 py-1 text-white" />
          </label>
        ) : (
          <label className="flex flex-col gap-1">
            <span className="text-gray-400">Days</span>
            <select value={days} onChange={(e) => setDays(Number(e.target.value))}
              className="bg-surface-3 border border-surface-3 rounded px-2 py-1 text-white">
              {[7, 15, 30, 60, 90, 180, 365].map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </label>
        )}
        <button onClick={run} disabled={running}
          className="text-xs px-3 py-1.5 rounded bg-brand-600/20 border border-brand-500/40 text-brand-300 hover:bg-brand-600/30 disabled:opacity-50">
          {running ? 'Running…' : 'Run Backtest'}
        </button>
      </div>

      {err && <div className="text-xs text-rose-400 mb-2">{err}</div>}

      {result && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-6 gap-2 mb-3 text-xs">
            {[
              ['Trades', stats.total_trades, 'text-white'],
              ['Win rate', `${fmt(stats.win_rate, 1)}%`, 'text-white'],
              ['Total P&L', `₹ ${fmt(stats.total_pnl)}`, (stats.total_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400')],
              ['Avg win', `₹ ${fmt(stats.avg_win)}`, 'text-emerald-400'],
              ['Avg loss', `₹ ${fmt(stats.avg_loss)}`, 'text-rose-400'],
              ['Max DD', `₹ ${fmt(stats.max_drawdown)}`, 'text-amber-400'],
            ].map(([label, val, cls]) => (
              <div key={label} className="bg-surface-3 rounded-lg px-2 py-1.5">
                <div className="text-[10px] text-gray-500 uppercase">{label}</div>
                <div className={`font-semibold ${cls}`}>{val ?? '—'}</div>
              </div>
            ))}
          </div>

          <div className="overflow-x-auto max-h-96">
            <table className="w-full text-[11px]">
              <thead className="sticky top-0 bg-surface-2">
                <tr className="text-left text-gray-500 border-b border-surface-3">
                  {CSV_COLUMNS.map(([, label]) => (
                    <th key={label} className="py-1 px-1 whitespace-nowrap">{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.trades.map((t) => (
                  <tr key={t.trade_no} className="border-b border-surface-3/50">
                    <td className="px-1">{t.trade_no}</td>
                    <td className="px-1 text-gray-400 whitespace-nowrap">{t.date}</td>
                    <td className="px-1 text-gray-400">{t.time}</td>
                    <td className="px-1">{fmt(t.index_price, 1)}</td>
                    <td className="px-1 text-green-400">{fmt(t.ema_fast, 1)}</td>
                    <td className="px-1 text-blue-400">{fmt(t.ema_slow, 1)}</td>
                    <td className={`px-1 font-semibold ${t.signal === 'CALL' ? 'text-green-400' : 'text-red-400'}`}>{t.signal}</td>
                    <td className="px-1">{t.strike}</td>
                    <td className="px-1 text-white whitespace-nowrap truncate max-w-[150px]">{t.option_name}</td>
                    <td className="px-1">{fmt(t.entry_price)}</td>
                    <td className="px-1">{fmt(t.exit_price)}</td>
                    <td className="px-1">{t.qty}</td>
                    <td className="px-1">{t.lots}</td>
                    <td className="px-1 text-rose-300">{fmt(t.stoploss)}</td>
                    <td className="px-1 text-emerald-300">{fmt(t.target)}</td>
                    <td className="px-1 text-gray-300">{t.exit_reason}</td>
                    <td className="px-1 text-gray-400">{t.holding_time}</td>
                    <td className={`px-1 text-right font-semibold ${t.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>{fmt(t.pnl)}</td>
                    <td className="px-1 text-right text-gray-300">{fmt(t.running_equity)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}

/* ─────────────────────────────────────────────────────
   Main page
   ───────────────────────────────────────────────────── */
export default function Strategy12() {
  const [status, setStatus] = useState(null);
  const [series, setSeries] = useState([]);
  const [markers, setMarkers] = useState([]);
  const [trades, setTrades] = useState([]);
  const [configOpen, setConfigOpen] = useState(false);
  const [docOpen, setDocOpen] = useState(false);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [config, setConfig] = useState({
    fast_ema: 20, slow_ema: 200, timeframe: '1minute', option_selection: 'ITM_100',
    lot_size: 65, lots: 1, atr_period: 14, sl_mult: 3, target_mode: 'atr',
    tgt_mult: 9, target_points: 30, atr_update_minutes: 1, exit_proximity: 5,
    touch_buffer: 2, enable_reentry: false, strike_interval: 50, index_name: 'NIFTY',
    start_time: '09:20', end_time: '15:10', max_trades_per_day: 5, max_daily_loss: 0,
  });
  const configSeededRef = useRef(false);
  const timerRef = useRef(null);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await api.getStrategy12Status();
      setStatus(res);
      if (res?.config && !configSeededRef.current) {
        setConfig((c) => ({ ...c, ...res.config }));
        configSeededRef.current = true;
      }
    } catch (e) { console.error('s12 status', e); }
  }, []);

  const fetchSeries = useCallback(async () => {
    try {
      const r = await api.strategy12IndexSeries();
      if (r?.status === 'ok') { setSeries(r.series || []); setMarkers(r.markers || []); }
    } catch (e) { console.error('s12 series', e); }
  }, []);

  const fetchHistory = useCallback(async () => {
    try {
      const r = await api.strategy12History();
      setTrades(r?.trades || []);
    } catch (e) { console.error('s12 history', e); }
  }, []);

  useEffect(() => { fetchStatus(); fetchSeries(); fetchHistory(); }, [fetchStatus, fetchSeries, fetchHistory]);

  useEffect(() => {
    timerRef.current = setInterval(() => {
      fetchStatus();
      if ((Date.now() / 1000) % 6 < 1.5) fetchSeries();
      if ((Date.now() / 1000) % 30 < 1.5) fetchHistory();
    }, REFRESH_MS);
    return () => clearInterval(timerRef.current);
  }, [fetchStatus, fetchSeries, fetchHistory]);

  const onStart = useCallback(async () => {
    setStarting(true);
    try {
      await api.strategy12Start(config);
      await fetchStatus();
    } catch (e) { alert(`Start failed: ${e.message || e}`); }
    finally { setStarting(false); }
  }, [config, fetchStatus]);

  const onStop = useCallback(async () => {
    setStopping(true);
    try { await api.strategy12Stop(); await fetchStatus(); }
    catch (e) { alert(`Stop failed: ${e.message || e}`); }
    finally { setStopping(false); }
  }, [fetchStatus]);

  const saveConfig = useCallback(async () => {
    try { await api.strategy12UpdateConfig(config); await fetchStatus(); setConfigOpen(false); }
    catch (e) { alert(`Config save failed: ${e.message || e}`); }
  }, [config, fetchStatus]);

  const resetRisk = useCallback(async () => {
    try { await api.strategy12RiskReset(); await fetchStatus(); }
    catch (e) { alert(`Reset failed: ${e.message || e}`); }
  }, [fetchStatus]);

  const setCfg = (k, v) => setConfig((c) => ({ ...c, [k]: v }));

  const stateKey = status?.state || 'IDLE';
  const stStyle = STATE_STYLE[stateKey] || STATE_STYLE.IDLE;
  const sig = status?.signal || 'NO_TRADE';
  const idx = status?.index || {};
  const trade = status?.trade || {};
  const trend = idx.trend || 'UP';

  return (
    <div className="p-4 space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">
            <Zap className="w-5 h-5 text-amber-400" />
            Strategy 12 — 200 EMA Pull-Back
          </h1>
          <p className="text-xs text-gray-400 mt-0.5">
            NIFTY touches the 200 EMA with the trend (20 EMA vs 200 EMA) → immediate
            MARKET buy of a CALL/PUT. Hidden option-ATR SL &amp; Target. No candle confirmation.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setDocOpen((v) => !v)}
            className="text-xs px-3 py-1.5 rounded border border-surface-3 text-gray-300 hover:text-white">
            <Info className="w-3.5 h-3.5 inline mr-1" /> How it works
          </button>
          <button onClick={() => setConfigOpen((v) => !v)}
            className="text-xs px-3 py-1.5 rounded border border-surface-3 text-gray-300 hover:text-white">
            <Settings2 className="w-3.5 h-3.5 inline mr-1" /> Config
            {configOpen ? <ChevronUp className="w-3 h-3 inline ml-1" /> : <ChevronDown className="w-3 h-3 inline ml-1" />}
          </button>
          {status?.is_active ? (
            <button onClick={onStop} disabled={stopping}
              className="text-xs px-3 py-1.5 rounded bg-rose-600/20 border border-rose-500/40 text-rose-300 hover:bg-rose-600/30 disabled:opacity-50">
              <Square className="w-3.5 h-3.5 inline mr-1" /> {stopping ? 'Stopping…' : 'Stop'}
            </button>
          ) : (
            <button onClick={onStart} disabled={starting}
              className="text-xs px-3 py-1.5 rounded bg-emerald-600/20 border border-emerald-500/40 text-emerald-300 hover:bg-emerald-600/30 disabled:opacity-50">
              <Play className="w-3.5 h-3.5 inline mr-1" /> {starting ? 'Starting…' : 'Start'}
            </button>
          )}
        </div>
      </div>

      {docOpen && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 text-xs text-gray-300 leading-relaxed space-y-2">
          <p><span className="text-amber-400 font-semibold">Entry:</span> When 20&nbsp;EMA &gt; 200&nbsp;EMA and NIFTY LTP touches the 200&nbsp;EMA → buy a CALL. When 20&nbsp;EMA &lt; 200&nbsp;EMA and price touches the 200&nbsp;EMA → buy a PUT. Immediate MARKET order, no confirmation candle.</p>
          <p><span className="text-amber-400 font-semibold">Option:</span> Strike is auto-selected from the current NIFTY price (ATM / 100–300 ITM / 100–300 OTM) on the nearest expiry.</p>
          <p><span className="text-amber-400 font-semibold">Hidden SL / Target:</span> Derived from the <em>option</em>'s ATR — SL = entry − ATR×{config.sl_mult}, Target {config.target_mode === 'points' ? `= entry + ${config.target_points} pts` : config.target_mode === 'none' ? '= none (trailing SL only)' : `= entry + ATR×${config.tgt_mult}`}. Recomputed every {config.atr_update_minutes} min; SL trails up. The broker only ever sees the entry and the exit — never a stop order.</p>
          <p><span className="text-amber-400 font-semibold">Exit:</span> When the option LTP comes within {config.exit_proximity} points of the hidden SL or Target, the position is flattened with a MARKET order. Hard square-off at 15:15.</p>
          <p><span className="text-amber-400 font-semibold">Re-entry:</span> {config.enable_reentry ? 'Enabled — a fresh 200-EMA touch can trade again (bounded by daily caps).' : 'Disabled — one trade per day.'}</p>
        </div>
      )}

      {configOpen && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-4 text-xs">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              ['Fast EMA Period', 'fast_ema', 'number'],
              ['Slow EMA Period', 'slow_ema', 'number'],
              ['ATR Period', 'atr_period', 'number'],
              ['ATR SL Multiplier', 'sl_mult', 'number'],
              ['ATR Target Multiplier', 'tgt_mult', 'number'],
              ['Target Points (points mode)', 'target_points', 'number'],
              ['Lot Size', 'lot_size', 'number'],
              ['Number of Lots', 'lots', 'number'],
              ['Exit Proximity (pts)', 'exit_proximity', 'number'],
              ['Touch Buffer (pts)', 'touch_buffer', 'number'],
              ['Max Daily Trades', 'max_trades_per_day', 'number'],
              ['Max Daily Loss (₹, 0=off)', 'max_daily_loss', 'number'],
            ].map(([label, key]) => (
              <label key={key} className="flex flex-col gap-1">
                <span className="text-gray-400">{label}</span>
                <input type="number" step="any" value={config[key]}
                  onChange={(e) => setCfg(key, e.target.value === '' ? '' : Number(e.target.value))}
                  className="bg-surface-3 border border-surface-3 rounded px-2 py-1 text-white" />
              </label>
            ))}

            <label className="flex flex-col gap-1">
              <span className="text-gray-400">Chart Timeframe</span>
              <select value={config.timeframe} onChange={(e) => setCfg('timeframe', e.target.value)}
                className="bg-surface-3 border border-surface-3 rounded px-2 py-1 text-white">
                {TIMEFRAMES.map(([label, val]) => <option key={val} value={val}>{label}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-gray-400">Option Selection</span>
              <select value={config.option_selection} onChange={(e) => setCfg('option_selection', e.target.value)}
                className="bg-surface-3 border border-surface-3 rounded px-2 py-1 text-white">
                {OPTION_MODES.map(([label, val]) => <option key={val} value={val}>{label}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-gray-400">Target Mode</span>
              <select value={config.target_mode} onChange={(e) => setCfg('target_mode', e.target.value)}
                className="bg-surface-3 border border-surface-3 rounded px-2 py-1 text-white">
                <option value="atr">ATR × multiplier</option>
                <option value="points">Fixed points</option>
                <option value="none">None (trail SL only)</option>
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-gray-400">ATR Update Frequency</span>
              <select value={config.atr_update_minutes} onChange={(e) => setCfg('atr_update_minutes', Number(e.target.value))}
                className="bg-surface-3 border border-surface-3 rounded px-2 py-1 text-white">
                {ATR_UPDATE.map(([label, val]) => <option key={val} value={val}>{label}</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-gray-400">Trading Start</span>
              <input type="time" value={config.start_time} onChange={(e) => setCfg('start_time', e.target.value)}
                className="bg-surface-3 border border-surface-3 rounded px-2 py-1 text-white" />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-gray-400">Trading End</span>
              <input type="time" value={config.end_time} onChange={(e) => setCfg('end_time', e.target.value)}
                className="bg-surface-3 border border-surface-3 rounded px-2 py-1 text-white" />
            </label>
            <label className="flex items-center gap-2 mt-5">
              <input type="checkbox" checked={!!config.enable_reentry}
                onChange={(e) => setCfg('enable_reentry', e.target.checked)}
                className="w-4 h-4 accent-emerald-500" />
              <span className="text-gray-300">Enable Re-entry</span>
            </label>
          </div>
          <div className="flex justify-end">
            <button onClick={saveConfig}
              className="text-xs px-3 py-1.5 rounded bg-emerald-600/20 border border-emerald-500/40 text-emerald-300 hover:bg-emerald-600/30">
              Save config
            </button>
          </div>
        </div>
      )}

      {/* Stat strip */}
      <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
        <Card title="State" icon={Clock}>
          <span className={`px-2 py-1 rounded text-xs font-semibold ${stStyle.bg} ${stStyle.text}`}>{stStyle.label}</span>
        </Card>
        <Card title="Signal" icon={Zap}>
          <span className={`px-2 py-1 rounded text-xs font-semibold border ${signalBg(sig)} ${signalColor(sig)}`}>{sig}</span>
        </Card>
        <Card title="NIFTY Spot" icon={Activity}>
          <div className="text-lg font-bold text-white">{fmt(idx.spot, 1)}</div>
        </Card>
        <Card title={`Fast EMA (${config.fast_ema})`} icon={TrendingUp}>
          <div className="text-lg font-bold text-green-400">{fmt(idx.ema_fast, 1)}</div>
        </Card>
        <Card title={`Slow EMA (${config.slow_ema})`} icon={Shield}>
          <div className="text-lg font-bold text-blue-400">{fmt(idx.ema_slow, 1)}</div>
        </Card>
        <Card title="Trend" icon={trend === 'UP' ? ArrowUpCircle : ArrowDownCircle}>
          <div className={`text-lg font-bold ${trend === 'UP' ? 'text-emerald-400' : 'text-rose-400'}`}>{trend}</div>
          <div className="text-[10px] text-gray-500 truncate">{status?.scenario || '—'}</div>
        </Card>
      </div>

      {/* Visualization panel */}
      <Card title="Strategy Visualization — NIFTY Index + EMAs" icon={LineChartIcon}
        right={
          <div className="flex items-center gap-3 text-[10px]">
            <span className="text-green-400">━ {config.fast_ema} EMA</span>
            <span className="text-blue-400">━ {config.slow_ema} EMA</span>
            <span className="text-emerald-400">▲ CALL</span>
            <span className="text-rose-400">▼ PUT</span>
            <span className="text-purple-400">✕ Exit</span>
          </div>
        }
      >
        <IndexChart series={series} markers={markers} />
      </Card>

      {/* Active trade */}
      <Card title="Active Trade" icon={Target}>
        {stateKey === 'POSITION_OPEN' || stateKey === 'ORDER_PLACED' ? (
          <div className="grid grid-cols-2 md:grid-cols-7 gap-3 text-xs">
            <div>
              <div className="text-gray-500">Side</div>
              <div className={`font-semibold ${trade.signal_type === 'CE' ? 'text-green-400' : 'text-red-400'}`}>
                {trade.signal_type === 'CE' ? 'CALL' : trade.signal_type === 'PE' ? 'PUT' : '—'}
              </div>
            </div>
            <div>
              <div className="text-gray-500">Option</div>
              <div className="text-white font-medium truncate">{trade.option_symbol || '—'}</div>
            </div>
            <div>
              <div className="text-gray-500">Fill</div>
              <div className="text-white font-medium">{fmt(trade.fill_price)}</div>
            </div>
            <div>
              <div className="text-gray-500">LTP</div>
              <div className="text-white font-medium">{fmt(trade.current_ltp)}</div>
            </div>
            <div>
              <div className="text-gray-500">Option ATR</div>
              <div className="text-amber-300 font-medium">{fmt(trade.entry_atr)}</div>
            </div>
            <div>
              <div className="text-gray-500">Hidden SL / Target</div>
              <div className="text-white font-medium">
                <span className="text-rose-400">{fmt(trade.sl_price)}</span> / <span className="text-emerald-400">{trade.target_price > 0 ? fmt(trade.target_price) : '—'}</span>
              </div>
            </div>
            <div>
              <div className="text-gray-500">Unrealized P&L</div>
              <div className={`font-bold ${(trade.unrealized_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                ₹ {fmt(trade.unrealized_pnl)}
              </div>
            </div>
          </div>
        ) : (
          <div className="text-xs text-gray-400">{status?.scenario || 'Idle — waiting for a 200-EMA pull-back.'}</div>
        )}
      </Card>

      {/* Today summary */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Card title="Trades Today" icon={CheckCircle2}>
          <div className="text-lg font-bold text-white">{status?.trades_today ?? 0} / {config.max_trades_per_day}</div>
        </Card>
        <Card title="Realized P&L (today)" icon={Target}>
          <div className={`text-lg font-bold ${(status?.realized_today ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
            ₹ {fmt(status?.realized_today)}
          </div>
        </Card>
        <Card title="Re-entry" icon={RefreshCw}>
          <div className="text-lg font-bold text-white">{config.enable_reentry ? 'Enabled' : 'Disabled'}</div>
        </Card>
        <Card title="Risk" icon={Shield} right={
          <button onClick={resetRisk} className="text-[10px] px-2 py-0.5 rounded border border-surface-3 text-gray-400 hover:text-white">Reset</button>
        }>
          <div className="text-xs text-gray-300">{status?.risk?.mode || 'ACTIVE'}</div>
          <div className="text-[10px] text-gray-500">SL hits {status?.risk?.sl_hits_today ?? 0} · re-entries {status?.risk?.reentries_today ?? 0}</div>
        </Card>
      </div>

      {/* Trade history */}
      <Card title="Trade History" icon={CheckCircle2}>
        {trades.length === 0 ? (
          <div className="text-xs text-gray-500">No trades yet.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-gray-500 border-b border-surface-3">
                  <th className="py-1">Date</th><th>Side</th><th>Strike</th><th>Option</th>
                  <th>Entry</th><th>Exit</th><th>SL</th><th>Target</th><th>Reason</th>
                  <th>Time</th><th className="text-right">P&L</th>
                </tr>
              </thead>
              <tbody>
                {trades.slice(0, 50).map((t, i) => (
                  <tr key={i} className="border-b border-surface-3/50">
                    <td className="py-1 text-gray-400">{t.date}</td>
                    <td className={t.direction === 'CALL' ? 'text-green-400' : 'text-red-400'}>{t.direction}</td>
                    <td>{t.strike}</td>
                    <td className="text-white truncate max-w-[160px]">{t.option}</td>
                    <td>{fmt(t.entry_price)}</td>
                    <td>{fmt(t.exit_price)}</td>
                    <td className="text-rose-300">{fmt(t.sl_price)}</td>
                    <td className="text-emerald-300">{t.target_price > 0 ? fmt(t.target_price) : '—'}</td>
                    <td className="text-gray-300">{t.exit_type}</td>
                    <td className="text-gray-500">{t.exit_time}</td>
                    <td className={`text-right font-semibold ${t.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>₹ {fmt(t.pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Backtest */}
      <BacktestPanel config={config} />
    </div>
  );
}
