import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  LineChart, Line, XAxis, YAxis, ResponsiveContainer,
  ReferenceLine, Tooltip, CartesianGrid, ReferenceDot,
} from 'recharts';
import {
  Play, Square, Settings2, ChevronDown, ChevronUp,
  Shield, Target, TrendingUp, TrendingDown, Zap,
  CheckCircle2, XCircle, Clock, AlertCircle, RefreshCw, Info, FlaskConical,
} from 'lucide-react';
import { api } from '../api';

const REFRESH_MS = 2_000;
const SPOT_HISTORY_LIMIT = 500; // full intraday session (~375 minute candles + live ticks)

const STATE_STYLE = {
  IDLE:             { bg: 'bg-gray-600/20',   text: 'text-gray-400',   label: 'Idle' },
  BREAKOUT_WATCH:   { bg: 'bg-emerald-600/20', text: 'text-emerald-400', label: 'Breakout Watch' },
  BREAKDOWN_WATCH:  { bg: 'bg-rose-600/20',    text: 'text-rose-400',   label: 'Breakdown Watch' },
  ORDER_PLACED:     { bg: 'bg-yellow-600/20', text: 'text-yellow-400', label: 'Order Placed' },
  POSITION_OPEN:    { bg: 'bg-blue-600/20',   text: 'text-blue-400',   label: 'Position Open' },
  COMPLETED:        { bg: 'bg-green-600/20',  text: 'text-green-400',  label: 'Completed' },
};

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

function ScenarioMiniChart({ title, data, high, low, accent, entryDot }) {
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-3">
      <h4 className={`text-sm font-semibold mb-2 ${accent}`}>{title}</h4>
      <ResponsiveContainer width="100%" height={140}>
        <LineChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
          <XAxis dataKey="x" hide />
          <YAxis domain={['auto', 'auto']} stroke="#475569" fontSize={10} width={45} />
          <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 6 }} />
          <ReferenceLine y={high} stroke="#ec4899" strokeDasharray="4 4" />
          <ReferenceLine y={low} stroke="#f59e0b" strokeDasharray="4 4" />
          <Line type="monotone" dataKey="y" stroke="#3b82f6" strokeWidth={2} dot={{ r: 3 }} />
          {entryDot && <ReferenceDot x={entryDot.x} y={entryDot.y} r={5} fill={entryDot.fill} stroke="#fff" />}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

const SCENARIO_SAMPLES = (high, low) => ([
  {
    title: 'Breakdown → Retest → PUT', accent: 'text-red-400',
    data: [19350, 19280, 19300, 19270, 19220].map((y, i) => ({ x: i + 1, y })),
    entry: { x: 3, y: 19300, fill: '#ef4444' },
  },
  {
    title: 'Breakout → Retest → CALL', accent: 'text-green-400',
    data: [19480, 19520, 19500, 19540, 19600].map((y, i) => ({ x: i + 1, y })),
    entry: { x: 3, y: 19500, fill: '#22c55e' },
  },
  {
    title: 'Fake Breakdown → CALL', accent: 'text-green-400',
    data: [19320, 19280, 19320, 19400, 19480].map((y, i) => ({ x: i + 1, y })),
    entry: { x: 3, y: 19320, fill: '#22c55e' },
  },
  {
    title: 'Fake Breakout → PUT', accent: 'text-red-400',
    data: [19480, 19520, 19480, 19420, 19380].map((y, i) => ({ x: i + 1, y })),
    entry: { x: 3, y: 19480, fill: '#ef4444' },
  },
  {
    title: 'Sideways → NO TRADE', accent: 'text-yellow-400',
    data: [19380, 19420, 19390, 19410, 19400].map((y, i) => ({ x: i + 1, y })),
    entry: null,
  },
]);

export default function Strategy5() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [configOpen, setConfigOpen] = useState(false);
  const [docOpen, setDocOpen] = useState(false);
  const [config, setConfig] = useState({
    sl_points: 30,
    target_points: 60,
    lot_size: 65,
    lots: 1,
    strike_interval: 50,
    sl_proximity: 5,
    target_proximity: 5,
    retest_buffer: 8,
    max_breakout_extension: 60,
    max_trades_per_day: 1,
    allow_reentry: false,
    retest_only: true,
    itm_offset: 100,
    max_entry_slippage: 8,
    index_name: 'NIFTY',
  });
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [spotHistory, setSpotHistory] = useState([]);
  const [backtest, setBacktest] = useState(null);
  const [btLoading, setBtLoading] = useState(false);
  const [btDate, setBtDate] = useState('');
  const [multiBt, setMultiBt] = useState(null);
  const [multiBtLoading, setMultiBtLoading] = useState(false);
  const tickRef = useRef(0);
  const timerRef = useRef(null);
  // Track whether the local config has been seeded from the server.
  // After seeding once, polls must NOT overwrite the user's in-progress
  // edits. Re-seeding only happens when the user explicitly clicks Save.
  const configSeededRef = useRef(false);

  /* ── Data fetching ─────────────────────────── */
  const fetchStatus = useCallback(async () => {
    try {
      setLoading(true);
      const res = await api.getStrategy5TradeStatus();
      setStatus(res);
      // Seed config from server only on first load to avoid clobbering
      // the user's in-progress edits during 2s polls.
      if (res?.config && !configSeededRef.current) {
        setConfig((c) => ({ ...c, ...res.config }));
        configSeededRef.current = true;
      }
      const s = res?.spot?.price ?? 0;
      if (s > 0) {
        tickRef.current += 1;
        const tStr = new Date().toLocaleTimeString('en-IN', { hour12: false });
        setSpotHistory((h) => {
          const next = [...h, { x: tickRef.current, y: s, t: tStr }];
          return next.slice(-SPOT_HISTORY_LIMIT);
        });
      }
    } catch (e) {
      console.error('s5 status', e);
    } finally {
      setLoading(false);
    }
  }, []);

  const triggerCheck = useCallback(async () => {
    try {
      const res = await api.strategy5TradeCheck();
      setStatus(res);
      const s = res?.spot?.price ?? 0;
      if (s > 0) {
        tickRef.current += 1;
        const tStr = new Date().toLocaleTimeString('en-IN', { hour12: false });
        setSpotHistory((h) => {
          const next = [...h, { x: tickRef.current, y: s, t: tStr }];
          return next.slice(-SPOT_HISTORY_LIMIT);
        });
      }
    } catch (e) {
      console.error('s5 check', e);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    // Force-fetch the active Gann pair immediately on mount so the user
    // always sees the dynamic levels — even before they click Start.
    api.getStrategy5Levels().then(() => fetchStatus()).catch(() => {});
    timerRef.current = setInterval(() => {
      if (status?.is_active) triggerCheck();
      else fetchStatus();
    }, REFRESH_MS);
    // Slow Gann-pair refresh while idle (every 15s) so the floating range
    // tracks live spot even when the strategy is not active.
    const lvlTimer = setInterval(() => {
      if (!status?.is_active) {
        api.getStrategy5Levels().catch(() => {});
      }
    }, 15_000);
    return () => { clearInterval(timerRef.current); clearInterval(lvlTimer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.is_active]);

  // One-time seed of spotHistory with today's intraday minute candles
  // so the live chart shows the full session shape (like backtest).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getStrategy5Intraday();
        if (cancelled) return;
        const series = Array.isArray(res?.series) ? res.series : [];
        if (!series.length) return;
        setSpotHistory((h) => {
          const seeded = series.map((p, i) => ({ x: i + 1, y: p.y, t: p.t }));
          const tail = h.slice(-Math.max(0, SPOT_HISTORY_LIMIT - seeded.length));
          const renumbered = tail.map((p, i) => ({ ...p, x: seeded.length + i + 1 }));
          tickRef.current = seeded.length + renumbered.length;
          return [...seeded, ...renumbered].slice(-SPOT_HISTORY_LIMIT);
        });
      } catch (e) {
        console.warn('s5 intraday seed failed', e);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  /* ── Controls ──────────────────────────────── */
  const start = async () => {
    if (starting) return;
    setStarting(true);
    try { await api.strategy5TradeStart(config); await fetchStatus(); }
    catch (e) { alert(e.message || 'Start failed'); }
    finally { setStarting(false); }
  };
  const stop = async () => {
    if (stopping) return;
    setStopping(true);
    try { await api.strategy5TradeStop(); await fetchStatus(); }
    catch (e) { alert(e.message || 'Stop failed'); }
    finally { setStopping(false); }
  };
  const saveConfig = async () => {
    try {
      await api.strategy5TradeUpdateConfig(config);
      // Force re-seed from server after save so values reflect the
      // canonical persisted state.
      configSeededRef.current = false;
      await fetchStatus();
      alert('Config saved');
    } catch (e) {
      alert(e.message || 'Config save failed');
    }
  };
  const refreshLevels = async () => {
    try { await api.getStrategy5Levels(); await fetchStatus(); }
    catch (e) { alert(e.message || 'Levels refresh failed'); }
  };
  const runBacktest = async () => {
    if (btLoading) return;
    setBtLoading(true);
    try {
      const res = await api.strategy5TradeBacktest(btDate || null);
      setBacktest(res);
      if (res?.status === 'error') alert(res.message);
    } catch (e) {
      alert(e.message || 'Backtest failed');
    } finally {
      setBtLoading(false);
    }
  };
  const runMultiBacktest = async () => {
    if (multiBtLoading) return;
    setMultiBtLoading(true);
    try {
      const res = await api.strategy5TradeBacktestMulti(30);
      setMultiBt(res);
      if (res?.status === 'error') alert(res.message);
    } catch (e) {
      alert(e.message || 'Multi backtest failed');
    } finally {
      setMultiBtLoading(false);
    }
  };

  /* ── Derived ───────────────────────────────── */
  const stateMeta = STATE_STYLE[status?.state] || STATE_STYLE.IDLE;
  const high = status?.levels?.gann_upper || 0;
  const low = status?.levels?.gann_lower || 0;
  const spot = status?.spot?.price || 0;
  const signal = status?.signal || 'NO_TRADE';
  const scenario = status?.scenario || '—';

  const entryDot = useMemo(() => {
    if (!status?.trade?.fill_price || !spotHistory.length) return null;
    const last = spotHistory[spotHistory.length - 1];
    return { x: last.t, y: spot, fill: status.trade.signal_type === 'CE' ? '#22c55e' : '#ef4444' };
  }, [status?.trade?.fill_price, status?.trade?.signal_type, spot, spotHistory]);

  const samples = useMemo(() => SCENARIO_SAMPLES(high || 19500, low || 19300), [high, low]);

  /* ── Render ────────────────────────────────── */
  return (
    <div className="p-4 sm:p-6 max-w-[1400px] mx-auto space-y-4 sm:space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold text-white flex items-center gap-2">
            <TrendingUp className="w-5 h-5 text-brand-400" />
            Strategy 5 . Dynamic Gann Level Range
          </h1>
          <p className="text-xs sm:text-sm text-gray-500 mt-0.5">
            Floating Gann pair around spot . breakout / breakdown retest entries on NIFTY ITM
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`px-3 py-1.5 rounded-lg text-xs font-medium ${stateMeta.bg} ${stateMeta.text}`}>
            {stateMeta.label}
          </span>
          <HeartbeatPill lastCheckAt={status?.last_check_at} isActive={status?.is_active} />
          {status?.is_active ? (
            <button onClick={stop} disabled={stopping}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-red-600/20 text-red-400 border border-red-500/30 hover:bg-red-600/30 disabled:opacity-50">
              <Square className="w-3 h-3" /> Stop
            </button>
          ) : (
            <button onClick={start} disabled={starting}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-green-600/20 text-green-400 border border-green-500/30 hover:bg-green-600/30 disabled:opacity-50">
              <Play className="w-3 h-3" /> Start
            </button>
          )}
          <button onClick={refreshLevels}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-surface-3 text-gray-300 hover:bg-surface-4">
            <RefreshCw className="w-3 h-3" /> Refresh Levels
          </button>
          <button onClick={runBacktest} disabled={btLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-purple-600/20 text-purple-300 border border-purple-500/30 hover:bg-purple-600/30 disabled:opacity-50">
            <FlaskConical className="w-3 h-3" /> {btLoading ? 'Backtesting…' : 'Backtest'}
          </button>
          <button onClick={runMultiBacktest} disabled={multiBtLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-purple-600/20 text-purple-300 border border-purple-500/30 hover:bg-purple-600/30 disabled:opacity-50">
            <FlaskConical className="w-3 h-3" /> {multiBtLoading ? 'Running 30d…' : 'Backtest 30d'}
          </button>
          <button onClick={() => setConfigOpen((v) => !v)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-surface-3 text-gray-300 hover:bg-surface-4">
            <Settings2 className="w-3 h-3" /> Config {configOpen ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          </button>
          <button onClick={() => setDocOpen((v) => !v)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-surface-3 text-gray-300 hover:bg-surface-4">
            <Info className="w-3 h-3" /> How it works
          </button>
        </div>
      </div>

      {docOpen && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 text-sm text-gray-300 space-y-2">
          <p>• <strong>Levels</strong>: Active Gann pair from gann_levels.csv — lower = largest level ≤ spot, upper = smallest level &gt; spot. The pair floats with spot until a cross.</p>
          <p>• <strong>Breakout Retest → CALL</strong>: spot breaks above gann_upper, pulls back to it without losing it → BUY ITM CE.</p>
          <p>• <strong>Breakdown Retest → PUT</strong>: spot breaks below gann_lower, pulls back to it without reclaiming → BUY ITM PE.</p>
          <p>• <strong>Fake Breakdown → CALL</strong>: spot dips below gann_lower then reclaims above it → BUY ITM CE.</p>
          <p>• <strong>Fake Breakout → PUT</strong>: spot pops above gann_upper then loses it → BUY ITM PE.</p>
          <p>• <strong>Sideways</strong>: spot inside range → NO TRADE.</p>
          <p className="text-xs text-gray-500">Entries can fire any time after market open. Active Gann pair recomputes every tick from spot until a cross locks it. Auto square-off at 15:15. SL hit deactivates the strategy until restart.</p>
        </div>
      )}

      {configOpen && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
            {[
              ['sl_points', 'SL (option pts)'],
              ['target_points', 'Target (option pts)'],
              ['lots', 'Lots'],
              ['strike_interval', 'Strike Interval'],
              ['retest_buffer', 'Retest Buffer (idx pts)'],
              ['max_breakout_extension', 'Max Extension (idx pts)'],
              ['sl_proximity', 'SL Proximity'],
              ['target_proximity', 'Target Proximity'],
              ['max_trades_per_day', 'Max Trades / Day'],
              ['itm_offset', 'ITM Offset (idx pts)'],
              ['max_entry_slippage', 'Max Entry Slippage (₹)'],
            ].map(([k, label]) => (
              <label key={k} className="text-xs text-gray-400">
                {label}
                <input type="number"
                  className="mt-1 w-full bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5 text-white text-sm"
                  value={config[k] ?? 0}
                  onChange={(e) => setConfig((c) => ({ ...c, [k]: Number(e.target.value) }))} />
              </label>
            ))}
            <label className="text-xs text-gray-400 flex items-center gap-2 mt-5">
              <input type="checkbox" checked={!!config.allow_reentry}
                onChange={(e) => setConfig((c) => ({ ...c, allow_reentry: e.target.checked }))} />
              Allow re-entry after target
            </label>
            <label className="text-xs text-gray-400 flex items-center gap-2 mt-5">
              <input type="checkbox" checked={config.retest_only !== false}
                onChange={(e) => setConfig((c) => ({ ...c, retest_only: e.target.checked }))} />
              Retest entries only (skip fake-outs)
            </label>
          </div>
          <div className="mt-3 flex items-center justify-between">
            <p className="text-[11px] text-gray-500">
              Tip: Re-entry needs <span className="text-gray-300">Max Trades / Day</span> &gt; 1 to take effect. NIFTY lot size = <span className="text-gray-300">{config.lot_size || 65}</span>; order qty = <span className="text-gray-300">{(Number(config.lots) || 1) * (Number(config.lot_size) || 65)}</span>.
            </p>
            <button onClick={saveConfig}
              className="px-3 py-1.5 rounded-lg text-xs font-medium bg-brand-600 hover:bg-brand-700 text-white">
              Save Config
            </button>
          </div>
        </div>
      )}

      {/* Backtest Panel */}
      {backtest && backtest.status === 'ok' && (
        <BacktestPanel data={backtest} btDate={btDate} setBtDate={setBtDate} onRun={runBacktest} btLoading={btLoading} onClose={() => setBacktest(null)} />
      )}

      {/* Multi-day Backtest Panel */}
      {multiBt && multiBt.status === 'ok' && (
        <MultiBacktestPanel data={multiBt} onClose={() => setMultiBt(null)} onRerun={runMultiBacktest} loading={multiBtLoading} />
      )}

      {/* Live Signal Panel */}
      <div className={`rounded-xl border p-4 ${signalBg(signal)}`}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Zap className={`w-5 h-5 ${signalColor(signal)}`} />
            <div>
              <p className={`text-lg font-bold ${signalColor(signal)}`}>{signal.replace('_', ' ')}</p>
              <p className="text-xs text-gray-400">{scenario}</p>
            </div>
          </div>
          <div className="flex items-center gap-6 text-xs">
            <div>
              <p className="text-gray-500">Spot</p>
              <p className="font-mono text-white text-sm">{spot ? spot.toFixed(2) : '—'}</p>
            </div>
            <div>
              <p className="text-green-500">Gann Upper</p>
              <p className="font-mono text-green-300 text-sm">{high ? high.toFixed(2) : '—'}</p>
            </div>
            <div>
              <p className="text-red-500">Gann Lower</p>
              <p className="font-mono text-red-300 text-sm">{low ? low.toFixed(2) : '—'}</p>
            </div>
            <div>
              <p className="text-gray-500">Range</p>
              <p className={`font-mono text-sm ${status?.levels?.locked ? 'text-amber-300' : 'text-gray-300'}`}>
                {status?.levels?.locked ? 'LOCKED' : 'FLOATING'}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Main Live Chart */}
      <Card title="Live Spot vs Active Gann Range" icon={TrendingUp}>
        {high > 0 && low > 0 ? (
          <>
          <ResponsiveContainer width="100%" height={340}>
            <LineChart data={spotHistory} margin={{ top: 10, right: 70, left: 0, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="t" stroke="#475569" fontSize={10} minTickGap={40} />
              <YAxis
                domain={[
                  (dataMin) => {
                    const lo = Math.min(dataMin ?? low, low, high);
                    return Math.floor(lo - Math.max(15, lo * 0.0015));
                  },
                  (dataMax) => {
                    const hi = Math.max(dataMax ?? high, low, high);
                    return Math.ceil(hi + Math.max(15, hi * 0.0015));
                  },
                ]}
                stroke="#475569" fontSize={10} width={60}
                tickFormatter={(v) => Number(v).toFixed(0)}
              />
              <Tooltip
                contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 6 }}
                labelStyle={{ color: '#94a3b8', fontSize: 11 }}
                formatter={(val) => [`₹ ${Number(val).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`, 'Spot']}
                labelFormatter={(t) => `Time: ${t}`}
              />
              <ReferenceLine y={high} stroke="#22c55e" strokeDasharray="6 4" label={{ value: `Gann Up ${high.toFixed(2)}`, fill: '#22c55e', fontSize: 11, position: 'right' }} />
              <ReferenceLine y={low} stroke="#ef4444" strokeDasharray="6 4" label={{ value: `Gann Lo ${low.toFixed(2)}`, fill: '#ef4444', fontSize: 11, position: 'right' }} />
              <Line type="monotone" dataKey="y" stroke="#3b82f6" strokeWidth={2} dot={false} isAnimationActive={false} />
              {spotHistory.length > 0 && (
                <ReferenceDot
                  x={spotHistory[spotHistory.length - 1].t}
                  y={spotHistory[spotHistory.length - 1].y}
                  r={5} fill="#3b82f6" stroke="#fff" strokeWidth={1.5}
                  label={{
                    value: `LTP ${Number(spotHistory[spotHistory.length - 1].y).toFixed(2)}`,
                    position: 'right', fill: '#60a5fa', fontSize: 11,
                  }}
                />
              )}
              {entryDot && <ReferenceDot x={entryDot.x} y={entryDot.y} r={6} fill={entryDot.fill} stroke="#fff" />}
            </LineChart>
          </ResponsiveContainer>
          {/* Live snapshot footer: LTP, range distance, ITM CE/PE preview */}
          {spot > 0 && (() => {
            const atm = Math.round(spot / (config.strike_interval || 50)) * (config.strike_interval || 50);
            const itm = Number(config.itm_offset ?? 100);
            const ceStrike = atm - itm;
            const peStrike = atm + itm;
            const inRange = spot >= low && spot <= high;
            const distHigh = spot - high;
            const distLow = spot - low;
            const locked = !!status?.levels?.locked;
            return (
              <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px]">
                <div className="bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5">
                  <div className="text-gray-500">LTP</div>
                  <div className="text-white font-semibold">₹ {spot.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
                </div>
                <div className="bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5">
                  <div className="text-gray-500">Position vs Gann {locked ? '(LOCKED)' : '(FLOATING)'}</div>
                  <div className={`font-semibold ${inRange ? 'text-yellow-400' : distHigh > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                    {inRange
                      ? `INSIDE  (+${distLow.toFixed(1)} / ${distHigh.toFixed(1)})`
                      : distHigh > 0
                        ? `ABOVE UP +${distHigh.toFixed(1)}`
                        : `BELOW LO ${distLow.toFixed(1)}`}
                  </div>
                </div>
                <div className="bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5">
                  <div className="text-gray-500">ITM CE (if BUY CALL)</div>
                  <div className="text-emerald-400 font-semibold">{ceStrike} CE  <span className="text-gray-500 text-[10px]">(ATM {atm} − {itm})</span></div>
                </div>
                <div className="bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5">
                  <div className="text-gray-500">ITM PE (if BUY PUT)</div>
                  <div className="text-rose-400 font-semibold">{peStrike} PE  <span className="text-gray-500 text-[10px]">(ATM {atm} + {itm})</span></div>
                </div>
              </div>
            );
          })()}
          </>
        ) : (
          <div className="h-[320px] flex items-center justify-center text-gray-500 text-sm">
            <AlertCircle className="w-4 h-4 mr-2" /> Levels not yet loaded — click "Refresh Levels".
          </div>
        )}
      </Card>

      {/* Trade & Orders */}
      {status?.trade?.option_symbol && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <Card title="Entry" icon={CheckCircle2}>
            <div className="space-y-1 text-sm">
              <Row label="Option" value={status.trade.option_symbol} />
              <Row label="Strike" value={status.trade.strike || status.trade.atm_strike} />
              <Row label="ATM (ref)" value={status.trade.atm_strike} />
              <Row label="Fill" value={status.trade.fill_price?.toFixed(2)} />
              <Row label="Reason" value={status.trade.entry_reason || '—'} />
            </div>
          </Card>
          <Card title="Risk" icon={Shield}>
            <div className="space-y-1 text-sm">
              <Row label="SL" value={status.trade.sl_price?.toFixed(2)} color="text-red-400" />
              <Row label="Target" value={status.trade.target_price?.toFixed(2)} color="text-green-400" />
              <Row label="LTP" value={status.trade.current_ltp?.toFixed(2)} />
              <Row label="Unrealized PnL" value={`₹${(status.trade.unrealized_pnl ?? 0).toFixed(2)}`}
                   color={(status.trade.unrealized_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'} />
            </div>
          </Card>
          <Card title="Orders" icon={Target}>
            <OrderRow label="Entry" order={status.orders?.entry} icon={CheckCircle2} color="text-blue-400" />
            <OrderRow label="SL" order={status.orders?.sl} icon={Shield} color="text-red-400" />
            <OrderRow label="Target" order={status.orders?.target} icon={Target} color="text-green-400" />
          </Card>
        </div>
      )}

      {/* Scenario gallery (educational) */}
      <div>
        <h3 className="text-sm font-medium text-gray-300 mb-2">Scenario Reference</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {samples.map((s) => (
            <ScenarioMiniChart key={s.title}
              title={s.title}
              data={s.data}
              high={high || 19500}
              low={low || 19300}
              accent={s.accent}
              entryDot={s.entry} />
          ))}
        </div>
      </div>

      {/* Trade log */}
      {status?.trade_log?.length > 0 && (
        <Card title="Today's Trade Log" icon={Clock}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-gray-500 uppercase">
                <tr>
                  <th className="text-left px-2 py-1">Time</th>
                  <th className="text-left px-2 py-1">Scenario</th>
                  <th className="text-left px-2 py-1">Option</th>
                  <th className="text-right px-2 py-1">Entry</th>
                  <th className="text-right px-2 py-1">Exit</th>
                  <th className="text-left px-2 py-1">Result</th>
                  <th className="text-right px-2 py-1">PnL</th>
                </tr>
              </thead>
              <tbody className="text-gray-300">
                {status.trade_log.slice().reverse().map((t, i) => (
                  <tr key={i} className="border-t border-surface-3">
                    <td className="px-2 py-1">{t.exit_time}</td>
                    <td className="px-2 py-1">{t.scenario}</td>
                    <td className="px-2 py-1 font-mono">{t.option}</td>
                    <td className="px-2 py-1 text-right font-mono">{Number(t.entry_price).toFixed(2)}</td>
                    <td className="px-2 py-1 text-right font-mono">{Number(t.exit_price).toFixed(2)}</td>
                    <td className={`px-2 py-1 ${t.exit_type === 'TARGET_HIT' ? 'text-green-400' : t.exit_type === 'SL_HIT' ? 'text-red-400' : 'text-yellow-400'}`}>
                      {t.exit_type}
                    </td>
                    <td className={`px-2 py-1 text-right font-mono ${t.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      ₹{Number(t.pnl).toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

function Row({ label, value, color = 'text-white' }) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-gray-500">{label}</span>
      <span className={`font-mono ${color}`}>{value ?? '—'}</span>
    </div>
  );
}

function OrderRow({ label, order, icon: Icon, color }) {
  if (!order) return <div className="text-xs text-gray-600 py-1">{label}: —</div>;
  const st = order.status || '—';
  const isFilled = st === 'COMPLETE';
  const isFailed = st === 'CANCELLED' || st === 'REJECTED';
  const isShadow = st === 'SHADOW';
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-surface-3 last:border-0">
      <div className="flex items-center gap-2">
        <Icon className={`w-4 h-4 ${color}`} />
        <span className="text-sm text-gray-300">{label}</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-sm font-mono text-white">{order.price?.toFixed?.(2) ?? '—'}</span>
        {isFilled && <CheckCircle2 className="w-4 h-4 text-green-400" />}
        {isFailed && <XCircle className="w-4 h-4 text-red-400" />}
        {isShadow && <Shield className="w-4 h-4 text-purple-400" />}
        {!isFilled && !isFailed && !isShadow && <Clock className="w-4 h-4 text-yellow-400 animate-pulse" />}
        <span className={`text-xs ${isFilled ? 'text-green-400' : isFailed ? 'text-red-400' : isShadow ? 'text-purple-400' : 'text-yellow-400'}`}>
          {isShadow ? 'HIDDEN' : st}
        </span>
      </div>
    </div>
  );
}

function BacktestPanel({ data, btDate, setBtDate, onRun, btLoading, onClose }) {
  // Merge spot + dynamic Gann band by time so we can plot 3 lines on one chart.
  const bandByTime = new Map((data.gann_band_series || []).map((b) => [b.t, b]));
  const series = (data.spot_series || []).map((s, i) => {
    const b = bandByTime.get(s.t);
    return {
      i, t: s.t, c: s.c,
      up: b ? b.up : null,
      lo: b ? b.lo : null,
      locked: b ? b.locked : false,
    };
  });
  const entries = (data.events || []).filter((e) => e.kind === 'ENTRY');
  const exits = (data.events || []).filter((e) => e.kind === 'SL' || e.kind === 'TGT' || e.kind === 'EXIT');
  const flips = (data.events || []).filter((e) => e.kind === 'FLIP');
  const timeToClose = new Map(series.map((p) => [p.t, p.c]));
  const totalPnl = data.summary?.total_pnl ?? 0;
  // Y domain spans price + every Gann level the band ever touched, so the
  // dynamic dotted bands stay visible even when price barely approaches them.
  const yValues = series.flatMap((p) => [p.c, p.up, p.lo]).filter((v) => Number.isFinite(v));
  const yLo = yValues.length ? Math.min(...yValues) - 20 : 'auto';
  const yHi = yValues.length ? Math.max(...yValues) + 20 : 'auto';

  return (
    <div className="bg-surface-2 border border-purple-500/40 rounded-xl p-4 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <FlaskConical className="w-4 h-4 text-purple-400" />
          <span className="text-sm font-semibold text-purple-300">
            Backtest — {data.sim_date} (dynamic Gann range)
          </span>
          {data.itm_offset !== undefined && (
            <span className="text-[10px] text-gray-400 bg-surface-3 px-1.5 py-0.5 rounded">
              ITM offset: {data.itm_offset} pts
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <input type="date" value={btDate} onChange={(e) => setBtDate(e.target.value)}
            className="bg-surface-3 border border-surface-4 rounded-md px-2 py-1 text-xs text-white" />
          <button onClick={onRun} disabled={btLoading}
            className="px-2 py-1 rounded-md text-xs font-medium bg-purple-600/20 text-purple-300 border border-purple-500/30 hover:bg-purple-600/30 disabled:opacity-50">
            {btLoading ? 'Running…' : 'Re-run'}
          </button>
          <button onClick={onClose}
            className="px-2 py-1 rounded-md text-xs font-medium bg-surface-3 text-gray-300 hover:bg-surface-4">
            Close
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-xs">
        <Stat label="Open Gann (Lo / Up)"
              value={`${Number(data.gann_lower || 0).toFixed(0)} / ${Number(data.gann_upper || 0).toFixed(0)}`}
              color="text-purple-300" />
        <Stat label="Final Gann (Lo / Up)"
              value={`${Number(data.final_gann_lower || 0).toFixed(0)} / ${Number(data.final_gann_upper || 0).toFixed(0)}`}
              color="text-purple-300" />
        <Stat label="Trades" value={data.summary?.total_trades ?? 0} />
        <Stat label="Wins / Losses" value={`${data.summary?.wins ?? 0} / ${data.summary?.losses ?? 0}`} />
        <Stat label="Total PnL"
              value={`₹${totalPnl.toFixed(2)}`}
              color={totalPnl >= 0 ? 'text-green-400' : 'text-red-400'} />
      </div>

      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={series} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
          <XAxis dataKey="t" stroke="#475569" fontSize={10} interval={Math.floor(series.length / 8) || 0} />
          <YAxis domain={[yLo, yHi]} stroke="#475569" fontSize={10} width={60} />
          <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 6 }} />
          {/* Dynamic Gann band — steps because the active pair only changes when spot crosses a level */}
          <Line type="stepAfter" dataKey="up" stroke="#22c55e" strokeWidth={1.5}
                strokeDasharray="4 4" dot={false} isAnimationActive={false}
                connectNulls name="Gann Upper" />
          <Line type="stepAfter" dataKey="lo" stroke="#ef4444" strokeWidth={1.5}
                strokeDasharray="4 4" dot={false} isAnimationActive={false}
                connectNulls name="Gann Lower" />
          <Line type="monotone" dataKey="c" stroke="#3b82f6" strokeWidth={2} dot={false} isAnimationActive={false} name="Spot" />
          {entries.map((e, i) => (
            <ReferenceDot key={`en${i}`} x={e.t} y={timeToClose.get(e.t)}
              r={5} fill={e.label.includes('CALL') ? '#22c55e' : '#ef4444'} stroke="#fff" />
          ))}
          {exits.map((e, i) => (
            <ReferenceDot key={`ex${i}`} x={e.t} y={timeToClose.get(e.t)}
              r={4} fill={e.kind === 'TGT' ? '#22c55e' : e.kind === 'SL' ? '#ef4444' : '#f59e0b'} stroke="#000" />
          ))}
          {flips.map((e, i) => (
            <ReferenceDot key={`fl${i}`} x={e.t} y={timeToClose.get(e.t)} r={3} fill="#a855f7" stroke="#fff" />
          ))}
        </LineChart>
      </ResponsiveContainer>

      {data.trades?.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-gray-500 uppercase">
              <tr>
                <th className="text-left px-2 py-1">Time</th>
                <th className="text-left px-2 py-1">Side</th>
                <th className="text-left px-2 py-1">Option</th>
                <th className="text-right px-2 py-1">Entry</th>
                <th className="text-right px-2 py-1">Exit</th>
                <th className="text-left px-2 py-1">Result</th>
                <th className="text-right px-2 py-1">PnL</th>
              </tr>
            </thead>
            <tbody className="text-gray-300">
              {data.trades.map((t, i) => (
                <tr key={i} className="border-t border-surface-3">
                  <td className="px-2 py-1">{t.time}</td>
                  <td className={`px-2 py-1 ${t.side === 'CE' ? 'text-green-400' : 'text-red-400'}`}>
                    {t.side === 'CE' ? 'BUY CALL' : 'BUY PUT'}
                  </td>
                  <td className="px-2 py-1 font-mono text-gray-300">
                    {t.option_symbol || (t.strike ? `NIFTY ${t.strike} ${t.side}` : '—')}
                  </td>
                  <td className="px-2 py-1 text-right font-mono">{Number(t.entry).toFixed(2)}</td>
                  <td className="px-2 py-1 text-right font-mono">{Number(t.exit).toFixed(2)}</td>
                  <td className={`px-2 py-1 ${t.exit_type === 'TARGET_HIT' ? 'text-green-400' : t.exit_type === 'SL_HIT' ? 'text-red-400' : 'text-yellow-400'}`}>
                    {t.exit_type}
                  </td>
                  <td className={`px-2 py-1 text-right font-mono ${t.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    ₹{Number(t.pnl).toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-[11px] text-gray-500 italic">
        ⓘ {data.note}
      </p>
    </div>
  );
}

function Stat({ label, value, color = 'text-white' }) {
  return (
    <div className="bg-surface-3 rounded-md px-2 py-1.5">
      <p className="text-gray-500 text-[10px] uppercase tracking-wider">{label}</p>
      <p className={`font-mono ${color}`}>{value ?? '—'}</p>
    </div>
  );
}

/* ── Heartbeat (P9) ──────────────────────────── */
function HeartbeatPill({ lastCheckAt, isActive }) {
  const [, force] = useState(0);
  useEffect(() => {
    const id = setInterval(() => force((n) => n + 1), 2000);
    return () => clearInterval(id);
  }, []);
  if (!isActive) return null;
  if (!lastCheckAt) {
    return (
      <span className="px-2 py-1 rounded-md text-[10px] font-medium bg-amber-600/15 text-amber-400 border border-amber-500/30">
        Awaiting tick…
      </span>
    );
  }
  const ts = new Date(lastCheckAt).getTime();
  const ageSec = Math.max(0, (Date.now() - ts) / 1000);
  // Only flag stale during market hours (09:15-15:30 IST). The browser
  // may not be in IST, so derive IST time via offset.
  const now = new Date();
  const istNow = new Date(now.getTime() + (now.getTimezoneOffset() + 330) * 60000);
  const istMin = istNow.getHours() * 60 + istNow.getMinutes();
  const inMarket = istMin >= 9 * 60 + 15 && istMin <= 15 * 60 + 30;
  const stale = inMarket && ageSec > 10;
  return (
    <span
      title={`Last check: ${new Date(lastCheckAt).toLocaleTimeString()}`}
      className={`px-2 py-1 rounded-md text-[10px] font-medium border ${
        stale
          ? 'bg-red-600/15 text-red-400 border-red-500/30 animate-pulse'
          : 'bg-green-600/10 text-green-400 border-green-500/30'
      }`}>
      {stale ? `Stale ${Math.round(ageSec)}s` : `Live · ${Math.round(ageSec)}s`}
    </span>
  );
}

/* ── Multi-day backtest panel (P7) ───────────── */
function MultiBacktestPanel({ data, onClose, onRerun, loading }) {
  const s = data.summary || {};
  const daily = data.daily || [];
  const maxAbs = daily.reduce((m, d) => Math.max(m, Math.abs(d.pnl || 0)), 1);
  return (
    <div className="bg-surface-2 border border-purple-500/40 rounded-xl p-4 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <FlaskConical className="w-4 h-4 text-purple-400" />
          <span className="text-sm font-semibold text-purple-300">
            Multi-day Backtest — last {data.covered_days}/{data.requested_days} sessions
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={onRerun} disabled={loading}
            className="px-2 py-1 rounded-md text-xs font-medium bg-purple-600/20 text-purple-300 border border-purple-500/30 hover:bg-purple-600/30 disabled:opacity-50">
            {loading ? 'Running…' : 'Re-run'}
          </button>
          <button onClick={onClose}
            className="px-2 py-1 rounded-md text-xs font-medium bg-surface-3 text-gray-300 hover:bg-surface-4">
            Close
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3 text-xs">
        <Stat label="Total Trades" value={s.total_trades ?? 0} />
        <Stat label="Win rate" value={`${(s.win_rate ?? 0).toFixed(1)}%`}
              color={(s.win_rate ?? 0) >= 50 ? 'text-green-400' : 'text-amber-400'} />
        <Stat label="Total PnL" value={`₹${Number(s.total_pnl ?? 0).toFixed(2)}`}
              color={(s.total_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'} />
        <Stat label="Avg PnL/Day" value={`₹${Number(s.avg_pnl_per_day ?? 0).toFixed(2)}`}
              color={(s.avg_pnl_per_day ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'} />
        <Stat label="Max DD" value={`₹${Number(s.max_drawdown ?? 0).toFixed(2)}`}
              color="text-red-400" />
        <Stat label="Max Consec Loss" value={s.max_consecutive_losses ?? 0}
              color="text-amber-400" />
      </div>

      {/* Daily PnL bar visualization */}
      {daily.length > 0 && (
        <div className="bg-surface-3 rounded-md p-2">
          <p className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">Daily PnL</p>
          <div className="flex items-end gap-0.5 h-20">
            {daily.map((d, i) => {
              const h = Math.round((Math.abs(d.pnl) / maxAbs) * 70);
              return (
                <div key={i} className="flex-1 flex flex-col items-center justify-end" title={`${d.date} · ₹${Number(d.pnl).toFixed(2)} · ${d.trades} trades`}>
                  <div
                    style={{ height: `${h}px`, minHeight: d.pnl ? '2px' : '0px' }}
                    className={`w-full ${d.pnl >= 0 ? 'bg-green-500/70' : 'bg-red-500/70'}`}
                  />
                </div>
              );
            })}
          </div>
        </div>
      )}

      {daily.length > 0 && (
        <div className="overflow-x-auto max-h-72">
          <table className="w-full text-xs">
            <thead className="text-gray-500 uppercase sticky top-0 bg-surface-2">
              <tr>
                <th className="text-left px-2 py-1">Date</th>
                <th className="text-right px-2 py-1">Trades</th>
                <th className="text-right px-2 py-1">W / L</th>
                <th className="text-right px-2 py-1">PnL</th>
              </tr>
            </thead>
            <tbody className="text-gray-300">
              {daily.map((d, i) => (
                <tr key={i} className="border-t border-surface-3">
                  <td className="px-2 py-1 font-mono">{d.date}</td>
                  <td className="px-2 py-1 text-right font-mono">{d.trades}</td>
                  <td className="px-2 py-1 text-right font-mono text-gray-400">
                    {d.wins} / {d.losses}
                  </td>
                  <td className={`px-2 py-1 text-right font-mono ${d.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                    ₹{Number(d.pnl).toFixed(2)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-[11px] text-gray-500 italic">
        ⓘ {data.note}
      </p>
    </div>
  );
}
