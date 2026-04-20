import React, { useState, useEffect, useRef, useCallback } from 'react';
import { api } from '../api';
import {
  Play,
  Square,
  Settings2,
  Crosshair,
  Shield,
  Target,
  TrendingUp,
  TrendingDown,
  Zap,
  Clock,
  CheckCircle2,
  XCircle,
  AlertCircle,
  RefreshCw,
  Info,
  X,
  Activity,
  BarChart3,
  Lock,
  Eye,
} from 'lucide-react';

const REFRESH_MS = 2_000;  // 2s — match backend for fast momentum entries

const STATE_STYLE = {
  IDLE:          { bg: 'bg-gray-600/20',   text: 'text-gray-400',   label: 'Idle' },
  ORDER_PLACED:  { bg: 'bg-yellow-600/20', text: 'text-yellow-400', label: 'Order Placed' },
  POSITION_OPEN: { bg: 'bg-blue-600/20',   text: 'text-blue-400',   label: 'Position Open' },
  COMPLETED:     { bg: 'bg-green-600/20',  text: 'text-green-400',  label: 'Completed' },
};

function Badge({ children, className = '' }) {
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${className}`}>
      {children}
    </span>
  );
}

function Card({ title, icon: Icon, children, className = '' }) {
  return (
    <div className={`bg-surface-2 border border-surface-3 rounded-xl p-4 ${className}`}>
      <div className="flex items-center gap-2 mb-2 text-gray-400 text-xs font-medium uppercase tracking-wider">
        {Icon && <Icon className="w-3.5 h-3.5" />}
        {title}
      </div>
      {children}
    </div>
  );
}

function OrderRow({ label, order, icon: Icon, color }) {
  if (!order) return null;
  const st = order.status || '—';
  const isFilled = st === 'COMPLETE';
  const isFailed = st === 'CANCELLED' || st === 'REJECTED';
  const isShadow = st === 'SHADOW';
  return (
    <div className="flex items-center justify-between py-2 border-b border-surface-3 last:border-0">
      <div className="flex items-center gap-2">
        <Icon className={`w-4 h-4 ${color}`} />
        <span className="text-sm text-gray-300">{label}</span>
      </div>
      <div className="flex items-center gap-3">
        <span className="text-sm font-mono text-white">{order.price?.toFixed(2) ?? '—'}</span>
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

/* ── Check Item (diagnostics) ────────────────── */

function CheckItem({ ok, label }) {
  return (
    <div className={`flex items-center gap-2 ${ok ? 'text-green-400' : 'text-gray-500'}`}>
      {ok ? <CheckCircle2 className="w-3.5 h-3.5 shrink-0" /> : <XCircle className="w-3.5 h-3.5 shrink-0 text-red-400/60" />}
      <span>{label}</span>
    </div>
  );
}

/* ── Strategy Documentation Modal ────────────── */

function StrategyDocModal({ open, onClose }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div
        className="bg-surface-1 border border-surface-3 rounded-2xl w-full max-w-2xl max-h-[85vh] overflow-y-auto shadow-2xl mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 bg-surface-1 border-b border-surface-3 px-6 py-4 flex items-center justify-between">
          <h2 className="text-lg font-bold text-white">Strategy 3 — Documentation</h2>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-surface-3 text-gray-400 hover:text-white transition">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="px-6 py-5 space-y-6 text-sm">
          {/* 1 & 2 */}
          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Strategy Name</h3>
            <p className="text-white">Strategy 3 — CV + VWAP + EMA200 + ADX Momentum Strategy</p>
          </section>
          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Strategy Type</h3>
            <p className="text-gray-300">Trend + Order Flow + Momentum</p>
          </section>

          {/* 3 */}
          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Indicators</h3>
            <ul className="space-y-1.5 text-gray-300 list-disc list-inside">
              <li><span className="text-white font-medium">EMA 200</span> — Long-term trend direction</li>
              <li><span className="text-white font-medium">EMA 20</span> — Pullback detection</li>
              <li><span className="text-white font-medium">VWAP</span> — Institutional reference level</li>
              <li><span className="text-white font-medium">ADX</span> — Trend strength filter (&ge;25 = strong trend)</li>
              <li><span className="text-white font-medium">Cumulative Volume</span> — Order flow confirmation</li>
            </ul>
          </section>

          {/* 4 */}
          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Entry Logic (3-Phase Setup)</h3>
            <div className="space-y-3 text-gray-300">
              <div>
                <p className="text-yellow-400 font-medium text-xs uppercase mb-1">Phase 1 — Trend Alignment</p>
                <ul className="list-disc list-inside ml-3 space-y-0.5">
                  <li>Spot vs EMA 200 (uptrend/downtrend confirmation)</li>
                  <li>Spot vs VWAP (institutional bias)</li>
                  <li>ADX &ge; threshold (strong trend)</li>
                  <li>CV &gt; threshold + CV slope alignment</li>
                </ul>
              </div>
              <div>
                <p className="text-yellow-400 font-medium text-xs uppercase mb-1">Phase 2 — Pullback Detection</p>
                <ul className="list-disc list-inside ml-3 space-y-0.5">
                  <li><span className="text-green-400">Bullish:</span> Price dips to or below EMA 20 (touches pullback zone)</li>
                  <li><span className="text-red-400">Bearish:</span> Price rallies to or above EMA 20 (touches pullback zone)</li>
                </ul>
              </div>
              <div>
                <p className="text-yellow-400 font-medium text-xs uppercase mb-1">Phase 3 — Breakout Confirmation</p>
                <ul className="list-disc list-inside ml-3 space-y-0.5">
                  <li><span className="text-green-400">Bullish:</span> Green candle (close &gt; open) + price recovers above EMA 20</li>
                  <li><span className="text-red-400">Bearish:</span> Red candle (close &lt; open) + price drops below EMA 20</li>
                  <li>Entry at floor Gann level of option premium (LIMIT order)</li>
                </ul>
              </div>
            </div>
          </section>

          {/* 5 */}
          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Exit Logic</h3>
            <ul className="list-disc list-inside text-gray-300 space-y-0.5">
              <li><span className="text-white font-medium">Stop Loss</span> — Entry − SL points (or previous Gann level)</li>
              <li><span className="text-white font-medium">Target</span> — Entry + Target points</li>
              <li><span className="text-white font-medium">Trailing SL</span> — When profit &ge; trailing amount, SL trails up</li>
              <li><span className="text-white font-medium">Trend Exit</span> — Spot crosses EMA 200 against trade direction (CE: spot drops below EMA200, PE: spot rises above EMA200)</li>
              <li><span className="text-white font-medium">Auto Square-off</span> — At configured time (default 15:15, from env)</li>
            </ul>
          </section>

          {/* 6 */}
          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Risk Management</h3>
            <ul className="list-disc list-inside text-gray-300 space-y-0.5">
              <li>Capital-based position sizing (lot size × entry price)</li>
              <li>Max trades per day limit (configurable)</li>
              <li>Max daily loss limit (configurable)</li>
              <li>New entries blocked once limits are hit</li>
            </ul>
          </section>

          {/* 7 */}
          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Execution Logic</h3>
            <ul className="list-disc list-inside text-gray-300 space-y-0.5">
              <li>Shadow (hidden) SL/Target orders — placed on exchange only when LTP approaches</li>
              <li>Single active sell order rule (cancels opposing before placing new)</li>
              <li>Manual exit supported via Stop button</li>
              <li>Paper trade mode for testing without real orders</li>
            </ul>
          </section>

          {/* 8 */}
          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Time Rules</h3>
            <ul className="list-disc list-inside text-gray-300 space-y-0.5">
              <li>Trading window: 9:15 AM — 3:30 PM</li>
              <li>Auto square-off: configurable from settings (default 3:15 PM)</li>
              <li>Background monitor runs every 10 seconds during market hours</li>
              <li>State persists across server restarts</li>
            </ul>
          </section>
        </div>
      </div>
    </div>
  );
}

/* ── PIN Modal ─────────────────────────────────── */

function PinModal({ open, onClose, onSuccess }) {
  const [pin, setPin] = useState('');
  const [error, setError] = useState('');
  const inputRef = useRef(null);

  useEffect(() => {
    if (open) {
      setPin('');
      setError('');
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [open]);

  if (!open) return null;

  const handleSubmit = (e) => {
    e.preventDefault();
    if (pin === '1605') {
      onSuccess();
      setPin('');
      setError('');
    } else {
      setError('Invalid PIN');
      setPin('');
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div
        className="bg-surface-1 border border-surface-3 rounded-2xl w-full max-w-sm shadow-2xl mx-4 p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 mb-4">
          <div className="w-10 h-10 rounded-lg bg-brand-600/20 flex items-center justify-center">
            <Lock className="w-5 h-5 text-brand-400" />
          </div>
          <div>
            <h3 className="text-white font-semibold">Strategy Documentation</h3>
            <p className="text-xs text-gray-400">Enter PIN to view</p>
          </div>
        </div>
        <form onSubmit={handleSubmit}>
          <input
            ref={inputRef}
            type="password"
            maxLength={4}
            value={pin}
            onChange={(e) => { setPin(e.target.value); setError(''); }}
            placeholder="Enter PIN"
            className="w-full bg-surface-2 border border-surface-3 rounded-lg px-4 py-3 text-white text-center text-lg tracking-[0.5em] font-mono focus:outline-none focus:border-brand-500 transition"
          />
          {error && (
            <p className="mt-2 text-red-400 text-sm text-center flex items-center justify-center gap-1">
              <AlertCircle className="w-3.5 h-3.5" /> {error}
            </p>
          )}
          <div className="flex gap-3 mt-4">
            <button type="button" onClick={onClose} className="flex-1 px-4 py-2 rounded-lg bg-surface-3 text-gray-400 hover:text-white text-sm transition">
              Cancel
            </button>
            <button type="submit" className="flex-1 px-4 py-2 rounded-lg bg-brand-600 hover:bg-brand-700 text-white text-sm font-medium transition">
              Unlock
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/* ── Main Component ──────────────────────────────── */

export default function Strategy3() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [configOpen, setConfigOpen] = useState(false);
  const [config, setConfig] = useState({
    sl_points: 40,
    target_points: 60,
    trailing_sl: 20,
    lot_size: 65,
    cv_threshold: 100000,
    adx_threshold: 25,
    strike_interval: 50,
    sl_proximity: 5,
    target_proximity: 5,
    max_trades_per_day: 5,
    max_loss_per_day: 5000,
    use_cv_filter: false,
  });
  const [countdown, setCountdown] = useState(10);
  const [pinOpen, setPinOpen] = useState(false);
  const [docOpen, setDocOpen] = useState(false);
  const timerRef = useRef(null);
  const countdownRef = useRef(null);

  /* ── Data fetching ─────────────────────────── */

  const fetchStatus = useCallback(async () => {
    try {
      setLoading(true);
      const res = await api.getStrategy3TradeStatus();
      setStatus(res);
      if (res?.config) setConfig(res.config);
    } catch (e) {
      console.error('status fetch', e);
    } finally {
      setLoading(false);
      setCountdown(2);
    }
  }, []);

  const triggerCheck = useCallback(async () => {
    try {
      const res = await api.strategy3TradeCheck();
      setStatus(res);
    } catch (e) {
      console.error('check', e);
    }
    setCountdown(2);
  }, []);

  useEffect(() => {
    fetchStatus();
    timerRef.current = setInterval(triggerCheck, REFRESH_MS);
    countdownRef.current = setInterval(() => setCountdown((c) => Math.max(0, c - 1)), 1000);
    const onConnected = () => fetchStatus();
    const onDisconnected = () => fetchStatus();
    window.addEventListener('zerodha_connected', onConnected);
    window.addEventListener('zerodha_disconnected', onDisconnected);
    return () => {
      clearInterval(timerRef.current);
      clearInterval(countdownRef.current);
      window.removeEventListener('zerodha_connected', onConnected);
      window.removeEventListener('zerodha_disconnected', onDisconnected);
    };
  }, [fetchStatus, triggerCheck]);

  /* ── Actions ───────────────────────────────── */

  const handleStart = async () => {
    try {
      const res = await api.strategy3TradeStart(config);
      setStatus(res);
    } catch (e) {
      console.error('start', e);
    }
  };

  const handleStop = async () => {
    try {
      const res = await api.strategy3TradeStop();
      setStatus(res);
    } catch (e) {
      console.error('stop', e);
    }
  };

  const handleSaveConfig = async () => {
    try {
      await api.strategy3TradeUpdateConfig(config);
      setConfigOpen(false);
      fetchStatus();
    } catch (e) {
      console.error('config update', e);
    }
  };

  /* ── Derived ───────────────────────────────── */

  const isActive = status?.is_active;
  const state = status?.state || 'IDLE';
  const stateStyle = STATE_STYLE[state] || STATE_STYLE.IDLE;
  const trade = status?.trade || {};
  const orders = status?.orders || {};
  const signalType = status?.signal_type;
  const indicators = status?.indicators || {};
  const risk = status?.risk || {};
  const cvValue = status?.cv_value ?? null;
  const tradeLog = status?.trade_log || [];
  const unrealizedPnl = trade?.unrealized_pnl ?? 0;
  const setup = status?.setup || {};
  const checklist = status?.entry_checklist || {};
  const spotPrice = status?.spot_price ?? checklist?.spot_price ?? 0;
  const tradingDate = status?.trading_date || '';

  // Compute the single most important "why not entered" reason
  const whyNotEntered = (() => {
    if (!isActive) return null;
    if (state !== 'IDLE') return null;
    if (!checklist || !Object.keys(checklist).length) return 'Waiting for first check cycle...';
    if (!checklist.risk_ok) return checklist.risk_reason;
    // Phase 1 blockers — direction from EMA200 + VWAP, strength from ADX + CV magnitude
    const trendOk = checklist.ema200_trend === 'Bullish' || checklist.ema200_trend === 'Bearish';
    if (!trendOk) return `No trend: Spot ${checklist.spot_price} near EMA200 ${checklist.ema200_val}`;
    const vwapAligned = checklist.vwap_aligned === true;
    if (!vwapAligned) return `VWAP not aligned with EMA200 trend (Spot vs VWAP ${checklist.vwap_val})`;
    if (!checklist.adx_strong) return `ADX too weak: ${checklist.adx_value} (need ≥${config.adx_threshold})`;
    if (!checklist.cv_active) return `CV not strong enough: ${Math.abs(checklist.cv_value ?? 0)?.toLocaleString()} (need ≥${config.cv_threshold?.toLocaleString()})`;
    // Phase 2
    if (!checklist.pullback_touched) return 'Waiting for price to pull back to EMA 20';
    // Phase 3
    if (checklist.setup_phase !== 'ARMED') return 'Waiting for breakout candle confirmation';
    return null;
  })();

  // Strategy mode label for the unified panel
  const modeInfo = (() => {
    if (!isActive) return { label: 'Inactive', color: 'text-gray-500', bg: 'border-surface-3' };
    if (state === 'IDLE') {
      if (setup.phase === 'ARMED') return { label: 'Signal Armed!', color: 'text-green-400', bg: 'border-green-500/30' };
      if (setup.phase === 'PULLBACK_SEEN') return { label: 'Pullback Detected', color: 'text-yellow-400', bg: 'border-yellow-500/30' };
      if (setup.phase === 'TREND_ALIGNED') return { label: 'Monitoring', color: 'text-brand-400', bg: 'border-brand-500/30' };
      return { label: 'Scanning', color: 'text-gray-400', bg: 'border-surface-3' };
    }
    if (state === 'ORDER_PLACED') return { label: 'Order Active', color: 'text-yellow-400', bg: 'border-yellow-500/30' };
    if (state === 'POSITION_OPEN') return { label: 'Trade Active', color: 'text-blue-400', bg: 'border-blue-500/30' };
    if (state === 'COMPLETED') return { label: 'Completed', color: 'text-green-400', bg: 'border-green-500/30' };
    return { label: state, color: 'text-gray-400', bg: 'border-surface-3' };
  })();

  /* ── Config field helper ───────────────────── */

  const configField = (label, key, step = 1) => (
    <div>
      <label className="block text-xs text-gray-400 mb-1">{label}</label>
      <input
        type="number"
        step={step}
        value={config[key] ?? ''}
        onChange={(e) => setConfig((c) => ({ ...c, [key]: Number(e.target.value) }))}
        disabled={isActive}
        className="w-full bg-surface-1 border border-surface-3 rounded-lg px-3 py-2 text-sm text-white disabled:opacity-50"
      />
    </div>
  );

  /* ── Render ────────────────────────────────── */

  if (loading && !status) {
    return (
      <div className="p-4 sm:p-6 space-y-4 sm:space-y-6 max-w-6xl mx-auto">
        <div>
          <div className="h-8 w-64 sm:w-80 bg-surface-3 rounded animate-pulse" />
          <div className="h-4 w-48 sm:w-60 bg-surface-3 rounded animate-pulse mt-2" />
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-7 gap-3">
          {Array.from({ length: 7 }).map((_, i) => (
            <div key={i} className="bg-surface-2 border border-surface-3 rounded-xl p-4">
              <div className="h-3 w-16 bg-surface-3 rounded animate-pulse mb-3" />
              <div className="h-6 w-12 bg-surface-3 rounded animate-pulse" />
            </div>
          ))}
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="bg-surface-2 border border-surface-3 rounded-xl p-4">
              <div className="h-3 w-16 bg-surface-3 rounded animate-pulse mb-3" />
              <div className="h-7 w-20 bg-surface-3 rounded animate-pulse" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="p-4 sm:p-6 space-y-4 sm:space-y-6 max-w-6xl mx-auto">
      {/* ── Header ─────────────────────────────── */}
      <div className="space-y-3">
        <div>
          <h1 className="text-lg sm:text-2xl font-bold text-white">Strategy 3 — CV + VWAP + EMA + ADX</h1>
          <p className="text-xs sm:text-sm text-gray-400 mt-1">
            Momentum entry using EMA 200/20, VWAP, ADX strength, and cumulative volume confirmation
          </p>
        </div>
        <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
          <Badge className={stateStyle.bg + ' ' + stateStyle.text}>{stateStyle.label}</Badge>

          {/* Info icon → PIN → Documentation */}
          <button
            onClick={() => setPinOpen(true)}
            className="p-2 rounded-lg border border-surface-3 text-gray-400 hover:text-brand-400 hover:bg-surface-3 transition"
            title="Strategy Documentation"
          >
            <Info className="w-4 h-4" />
          </button>

          <button
            onClick={() => setConfigOpen((o) => !o)}
            className="p-2 rounded-lg border border-surface-3 text-gray-400 hover:text-white hover:bg-surface-3 transition"
          >
            <Settings2 className="w-4 h-4" />
          </button>

          {isActive ? (
            <button
              onClick={handleStop}
              className="flex items-center gap-2 px-3 sm:px-4 py-2 rounded-lg bg-red-600/20 text-red-400 border border-red-500/30 hover:bg-red-600/30 transition text-sm font-medium"
            >
              <Square className="w-4 h-4" /> Stop
            </button>
          ) : (
            <button
              onClick={handleStart}
              className="flex items-center gap-2 px-3 sm:px-4 py-2 rounded-lg bg-green-600/20 text-green-400 border border-green-500/30 hover:bg-green-600/30 transition text-sm font-medium"
            >
              <Play className="w-4 h-4" /> Start
            </button>
          )}

          <span className="text-xs text-gray-500 tabular-nums w-8 text-right">{countdown}s</span>
        </div>
      </div>

      {/* ── Config panel ───────────────────────── */}
      {configOpen && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-5">
          <h3 className="text-sm font-semibold text-white mb-4">Configuration</h3>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4">
            {configField('SL Points', 'sl_points')}
            {configField('Target Points', 'target_points')}
            {configField('Trailing SL', 'trailing_sl')}
            {configField('Lot Size', 'lot_size')}
            {configField('CV Threshold', 'cv_threshold', 10000)}
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4 mt-4">
            {configField('ADX Threshold', 'adx_threshold')}
            {configField('Strike Interval', 'strike_interval')}
            {configField('SL Proximity', 'sl_proximity')}
            {configField('Target Proximity', 'target_proximity')}
            {configField('Max Trades/Day', 'max_trades_per_day')}
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4 mt-4">
            {configField('Max Loss/Day (₹)', 'max_loss_per_day', 500)}
          </div>
          {/* CV Filter toggle */}
          <div className="flex items-center gap-3 mt-4 p-3 rounded-lg bg-surface-1 border border-surface-3">
            <input
              type="checkbox"
              id="use_cv_filter"
              checked={config.use_cv_filter ?? false}
              onChange={(e) => setConfig((c) => ({ ...c, use_cv_filter: e.target.checked }))}
              disabled={isActive}
              className="w-4 h-4 rounded border-surface-3 bg-surface-2 text-brand-600 focus:ring-brand-500 disabled:opacity-50"
            />
            <label htmlFor="use_cv_filter" className="text-sm text-gray-300 select-none">
              Require CV Participation for entry
            </label>
            <span className="text-xs text-gray-500 ml-auto">
              {config.use_cv_filter ? 'ON — |CV| must ≥ threshold' : 'OFF — CV decoupled from entry'}
            </span>
          </div>
          <p className="text-xs text-gray-500 mt-3">
            Shadow orders: SL/Target stay hidden until LTP is within proximity. Trailing SL activates once profit exceeds the trailing amount.
          </p>
          {!isActive && (
            <button
              onClick={handleSaveConfig}
              className="mt-4 px-4 py-2 rounded-lg bg-brand-600 hover:bg-brand-700 text-white text-sm font-medium transition"
            >
              Save Config
            </button>
          )}
        </div>
      )}

      {/* ── Indicator cards ────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
        <Card title="Signal" icon={Zap}>
          {signalType ? (
            <div className="flex items-center gap-2">
              {signalType === 'CE' ? (
                <TrendingUp className="w-5 h-5 text-green-400" />
              ) : (
                <TrendingDown className="w-5 h-5 text-red-400" />
              )}
              <span className={`text-lg font-bold ${signalType === 'CE' ? 'text-green-400' : 'text-red-400'}`}>
                {signalType}
              </span>
            </div>
          ) : (
            <span className="text-lg font-bold text-gray-500">—</span>
          )}
        </Card>

        <Card title="Trend Bias" icon={TrendingUp}>
          <span className={`text-lg font-bold ${
            indicators.spot_vs_ema200 === 'Above' ? 'text-green-400' :
            indicators.spot_vs_ema200 === 'Below' ? 'text-red-400' : 'text-gray-400'
          }`}>
            {indicators.spot_vs_ema200 === 'Above' ? '▲ Bullish' :
             indicators.spot_vs_ema200 === 'Below' ? '▼ Bearish' : '—'}
          </span>
          {indicators.spot_vs_ema200 !== '—' && (
            <p className={`text-[10px] mt-0.5 font-medium ${indicators.spot_vs_ema200 === 'Above' ? 'text-green-500/80' : 'text-red-500/80'}`}>
              {indicators.spot_vs_ema200 === 'Above' ? 'Looking: BUY CE' : 'Looking: BUY PE'}
            </p>
          )}
          {indicators.spot_vs_vwap && indicators.spot_vs_vwap !== '—' && (
            <p className={`text-[10px] mt-0.5 ${indicators.spot_vs_vwap === 'Above' ? 'text-green-500/70' : 'text-red-500/70'}`}>
              VWAP: {indicators.spot_vs_vwap}
            </p>
          )}
        </Card>

        <Card title="EMA 200" icon={Activity}>
          <span className="text-lg font-bold font-mono text-white">
            {indicators.ema200 ? indicators.ema200.toFixed(1) : '—'}
          </span>
          <p className="text-[10px] text-gray-500 mt-0.5">NIFTY FUT 1m</p>
        </Card>

        <Card title="EMA 20" icon={Activity}>
          <span className="text-lg font-bold font-mono text-white">
            {indicators.ema20 ? indicators.ema20.toFixed(1) : '—'}
          </span>
          <p className="text-[10px] text-gray-500 mt-0.5">NIFTY FUT 1m</p>
        </Card>

        <Card title="VWAP" icon={BarChart3}>
          <span className="text-lg font-bold font-mono text-white">
            {indicators.vwap ? indicators.vwap.toFixed(1) : '—'}
          </span>
          <p className="text-[10px] text-gray-500 mt-0.5">NIFTY FUT 1m</p>
        </Card>

        <Card title="ADX" icon={Zap}>
          <span className={`text-lg font-bold font-mono ${
            indicators.adx >= (config.adx_threshold || 25) ? 'text-green-400' : 'text-yellow-400'
          }`}>
            {indicators.adx ? indicators.adx.toFixed(1) : '—'}
          </span>
          <p className="text-[10px] text-gray-500 mt-0.5">NIFTY FUT 1m · ADX(14)</p>
        </Card>

        <Card title="CV Trend" icon={BarChart3}>
          <span className={`text-lg font-bold ${
            indicators.cv_trend === 'Bullish' ? 'text-green-400' :
            indicators.cv_trend === 'Bearish' ? 'text-red-400' : 'text-gray-400'
          }`}>
            {indicators.cv_trend || '—'}
          </span>
          {checklist.cv_slope && checklist.cv_slope !== '—' && (
            <p className={`text-[10px] mt-0.5 ${checklist.cv_slope === 'Rising' ? 'text-green-500/70' : checklist.cv_slope === 'Falling' ? 'text-red-500/70' : 'text-gray-500'}`}>
              Slope: {checklist.cv_slope}
            </p>
          )}
        </Card>
      </div>

      {/* ── Summary cards ──────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card title="Entry" icon={Crosshair}>
          <span className="text-xl font-bold font-mono text-white">
            {trade.entry_price ? trade.entry_price.toFixed(2) : '—'}
          </span>
          {trade.option_ltp > 0 && (
            <span className="text-xs text-gray-400 ml-2">LTP {trade.option_ltp.toFixed(2)}</span>
          )}
        </Card>

        <Card title="Current LTP" icon={RefreshCw}>
          <span className="text-xl font-bold font-mono text-white">
            {trade.current_ltp > 0 ? trade.current_ltp.toFixed(2) : '—'}
          </span>
          {state === 'POSITION_OPEN' && (
            <span className={`text-xs ml-2 ${unrealizedPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {unrealizedPnl >= 0 ? '+' : ''}{unrealizedPnl.toFixed(2)}
            </span>
          )}
        </Card>

        <Card title="Spot / ATM" icon={Target}>
          <span className="text-xl font-bold font-mono text-white">
            {trade.atm_strike || '—'}
          </span>
        </Card>

        <Card title="Risk" icon={Shield}>
          <div className="text-sm">
            <span className="text-gray-400">Trades: </span>
            <span className="text-white font-mono">{risk.trades_today ?? 0}/{risk.max_trades_per_day ?? config.max_trades_per_day}</span>
            <br />
            <span className="text-gray-400">Day P&L: </span>
            <span className={`font-mono ${(risk.daily_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {(risk.daily_pnl ?? 0) >= 0 ? '+' : ''}₹{(risk.daily_pnl ?? 0).toFixed(0)}
            </span>
          </div>
        </Card>
      </div>

      {/* ── Trade setup ────────────────────────── */}
      {trade.option_symbol && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-5">
          <h3 className="text-sm font-semibold text-white mb-3">Trade Setup</h3>
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-4 text-sm">
            <div>
              <span className="text-gray-400">Option</span>
              <p className="font-mono text-white">{trade.option_symbol}</p>
            </div>
            <div>
              <span className="text-gray-400">Entry</span>
              <p className="font-mono text-white">{trade.entry_price?.toFixed(2)}</p>
            </div>
            <div>
              <span className="text-gray-400">SL Level</span>
              <p className="font-mono text-red-400">{trade.sl_price?.toFixed(2)}</p>
            </div>
            <div>
              <span className="text-gray-400">Target Level</span>
              <p className="font-mono text-green-400">{trade.target_price?.toFixed(2)}</p>
            </div>
            <div>
              <span className="text-gray-400">Trailing SL</span>
              <p className={`font-mono ${trade.trailing_active ? 'text-purple-400' : 'text-gray-500'}`}>
                {trade.trailing_active ? 'Active' : 'Inactive'}
              </p>
            </div>
          </div>
          {status?.signal_reason && (
            <p className="mt-3 text-xs text-gray-400 bg-surface-1 px-3 py-2 rounded-lg">
              <span className="text-gray-500 mr-1">Signal:</span>
              {status.signal_reason}
            </p>
          )}
        </div>
      )}

      {/* ── Orders ─────────────────────────────── */}
      {(orders.entry || orders.sl || orders.target) && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-5">
          <h3 className="text-sm font-semibold text-white mb-3">Orders</h3>
          <OrderRow label="Entry (LIMIT BUY)" order={orders.entry} icon={Crosshair} color="text-brand-400" />
          <OrderRow label="Stop Loss (SL-M)" order={orders.sl} icon={Shield} color="text-red-400" />
          <OrderRow label="Target (LIMIT SELL)" order={orders.target} icon={Target} color="text-green-400" />
        </div>
      )}

      {/* ── Unified Strategy Status Panel (always visible) ── */}
      <div className={`bg-surface-2 border ${modeInfo.bg} rounded-xl p-5 transition-colors duration-300`}>
        {/* Live status strip */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <div className="flex items-center gap-3">
            <Eye className="w-4 h-4 text-brand-400" />
            <h3 className="text-sm font-semibold text-white">Strategy Monitor</h3>
            <Badge className={`${stateStyle.bg} ${modeInfo.color}`}>{modeInfo.label}</Badge>
            {setup.direction && (
              <Badge className={setup.direction === 'CE' ? 'bg-green-600/20 text-green-400' : 'bg-red-600/20 text-red-400'}>
                {setup.direction === 'CE' ? '▲ CE' : '▼ PE'}
              </Badge>
            )}
          </div>
          <div className="flex items-center gap-4 text-[11px] text-gray-500">
            {spotPrice > 0 && (
              <span>Spot: <span className="text-white font-mono">{spotPrice.toLocaleString()}</span></span>
            )}
            {tradingDate && <span>Date: {tradingDate}</span>}
            <span className="flex items-center gap-1">
              <RefreshCw className={`w-3 h-3 ${isActive ? 'text-green-500 animate-spin' : 'text-gray-600'}`} style={isActive ? { animationDuration: '3s' } : {}} />
              {isActive ? `${countdown}s` : 'Off'}
            </span>
          </div>
        </div>

        {/* Why-not-entered headline */}
        {whyNotEntered && (
          <div className="flex items-center gap-2 mb-4 px-3 py-2 rounded-lg bg-surface-1 border border-surface-3">
            <AlertCircle className="w-4 h-4 text-yellow-400 shrink-0" />
            <span className="text-sm text-yellow-300">{whyNotEntered}</span>
          </div>
        )}

        {/* Setup Phase Progress — always visible */}
        <div className={`${!isActive ? 'opacity-40' : ''} transition-opacity`}>
          <div className="flex items-center gap-2 mb-2">
            {['TREND_ALIGNED', 'PULLBACK_SEEN', 'ARMED'].map((phase, i) => {
              const phaseOrder = { NONE: -1, TREND_ALIGNED: 0, PULLBACK_SEEN: 1, ARMED: 2 };
              // When in trade states, show all phases as complete
              const isTradeState = state === 'ORDER_PLACED' || state === 'POSITION_OPEN' || state === 'COMPLETED';
              const current = isTradeState ? 2 : (phaseOrder[setup.phase] ?? -1);
              const done = current >= i;
              const active = current === i && isActive && state === 'IDLE';
              return (
                <React.Fragment key={phase}>
                  {i > 0 && <div className={`flex-1 h-0.5 transition-colors duration-500 ${done ? 'bg-brand-500' : 'bg-surface-3'}`} />}
                  <div className="flex flex-col items-center min-w-[80px]">
                    <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold mb-1 transition-all duration-300
                      ${done ? 'bg-brand-600 text-white' : 'bg-surface-3 text-gray-500'}
                      ${active ? 'ring-2 ring-brand-400/50 animate-pulse' : ''}`}>
                      {done ? <CheckCircle2 className="w-4 h-4" /> : i + 1}
                    </div>
                    <span className={`text-[10px] text-center ${done ? 'text-brand-400' : 'text-gray-500'}`}>
                      {phase === 'TREND_ALIGNED' ? 'Trend' : phase === 'PULLBACK_SEEN' ? 'Pullback' : 'Breakout'}
                    </span>
                  </div>
                </React.Fragment>
              );
            })}
          </div>

          {/* Phase description or trade-active message */}
          {!isActive && (
            <p className="text-xs text-gray-500 mt-2">Start the strategy to begin monitoring market conditions</p>
          )}
          {isActive && state === 'IDLE' && setup.phase === 'NONE' && (
            <p className="text-xs text-gray-500 mt-2">Scanning for trend alignment (EMA200 + VWAP + ADX + CV)...</p>
          )}
          {isActive && state === 'IDLE' && setup.phase === 'TREND_ALIGNED' && (
            <p className="text-xs text-gray-400 mt-2">
              {setup.direction === 'CE' ? '▲ Bullish' : setup.direction === 'PE' ? '▼ Bearish' : ''} trend aligned ({setup.direction === 'CE' ? 'BUY CE' : 'BUY PE'}). Waiting for price to pull back to EMA 20...
            </p>
          )}
          {isActive && state === 'IDLE' && setup.phase === 'PULLBACK_SEEN' && (
            <p className="text-xs text-yellow-400 mt-2">
              Pullback detected ({setup.direction === 'CE' ? 'BUY CE' : 'BUY PE'})! Waiting for {setup.direction === 'CE' ? 'green' : 'red'} breakout candle...
            </p>
          )}
          {isActive && state === 'IDLE' && setup.phase === 'ARMED' && (
            <p className="text-xs text-green-400 mt-2 font-medium">Entry ARMED — placing {setup.direction === 'CE' ? 'CALL' : 'PUT'} order now!</p>
          )}
          {isActive && state === 'ORDER_PLACED' && (
            <p className="text-xs text-yellow-400 mt-2">All phases passed. Entry order placed — waiting for fill...</p>
          )}
          {isActive && state === 'POSITION_OPEN' && (
            <p className="text-xs text-blue-400 mt-2">Position open. Monitoring SL/Target/Trailing/Trend exit...</p>
          )}
          {isActive && state === 'COMPLETED' && (
            <p className="text-xs text-green-400 mt-2">Trade completed. Waiting for next setup cycle...</p>
          )}
        </div>

        {/* Entry Condition Checklist — collapsible */}
        {isActive && state === 'IDLE' && Object.keys(checklist).length > 0 && (
          <details className="mt-4 group">
            <summary className="flex items-center gap-2 cursor-pointer text-xs text-gray-400 hover:text-gray-300 transition select-none">
              <Activity className="w-3.5 h-3.5" />
              <span>Entry Condition Details</span>
              <span className="text-[10px] text-gray-600 ml-1">(click to expand)</span>
            </summary>
            <div className="mt-3 pt-3 border-t border-surface-3 space-y-1.5 text-xs">
              {!checklist.risk_ok && (
                <div className="flex items-center gap-2 text-red-400">
                  <XCircle className="w-3.5 h-3.5 shrink-0" />
                  <span>Risk: {checklist.risk_reason}</span>
                </div>
              )}
              {checklist.risk_ok && (
                <>
                  {/* Direction (from price action) */}
                  <CheckItem ok={checklist.ema200_trend === 'Bullish' || checklist.ema200_trend === 'Bearish'}
                    label={`EMA200 Trend: ${checklist.ema200_trend} → ${checklist.ema200_trend === 'Bullish' ? 'BUY CE' : checklist.ema200_trend === 'Bearish' ? 'BUY PE' : '—'} (Spot ${checklist.spot_price} vs EMA ${checklist.ema200_val})`} />
                  <CheckItem ok={checklist.vwap_aligned === true}
                    label={`VWAP: Spot ${checklist.above_vwap ? 'above' : checklist.below_vwap ? 'below' : '—'} (${checklist.vwap_val}) ${checklist.vwap_aligned ? '— aligned ✓' : '— diverging'}`} />
                  {/* Strength confirmation */}
                  <CheckItem ok={checklist.adx_strong}
                    label={`ADX Strength: ${checklist.adx_value} (need ≥${config.adx_threshold})`} />
                  {checklist.use_cv_filter ? (
                    <CheckItem ok={checklist.cv_active}
                      label={`CV Participation: ${checklist.cv_value?.toLocaleString()} (|${Math.abs(checklist.cv_value ?? 0)?.toLocaleString()}| ≥ ${config.cv_threshold?.toLocaleString()})`} />
                  ) : (
                    <div className="flex items-center gap-2 text-xs text-gray-500">
                      <Info className="w-3.5 h-3.5 shrink-0" />
                      <span>CV Participation: Disabled (decoupled)</span>
                    </div>
                  )}
                  {/* CV trend & slope (informational) */}
                  <div className={`flex items-center gap-2 text-xs ${checklist.use_cv_filter ? (checklist.cv_aligned ? 'text-green-400' : 'text-yellow-400') : 'text-gray-500'}`}>
                    <Info className="w-3.5 h-3.5 shrink-0" />
                    <span>CV: {checklist.cv_trend} ({checklist.cv_slope || '—'}) {checklist.use_cv_filter ? (checklist.cv_aligned ? '— aligned with trend ✓' : `— diverging from ${checklist.ema200_trend} trend`) : '— informational only'}</span>
                  </div>
                  <div className="border-t border-surface-3 my-1.5" />
                  <CheckItem ok={checklist.pullback_touched}
                    label={`Pullback: ${checklist.pullback_touched ? 'Touched EMA20' : 'Waiting for EMA20 touch'}`} />
                  <CheckItem ok={checklist.setup_phase === 'ARMED'}
                    label={`Breakout: ${checklist.last_candle_bullish === true ? 'Green candle ✓' : checklist.last_candle_bullish === false ? 'Red candle ✓' : 'Waiting...'}`} />
                </>
              )}
            </div>
          </details>
        )}
      </div>

      {/* ── CV indicator bar ───────────────────── */}
      {cvValue !== null && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-5">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-sm font-semibold text-white">Cumulative Volume</h3>
            <Badge className={cvValue > 0 ? 'bg-green-600/20 text-green-400' : cvValue < 0 ? 'bg-red-600/20 text-red-400' : 'bg-gray-600/20 text-gray-400'}>
              {cvValue >= 0 ? '+' : ''}{cvValue.toLocaleString()}
            </Badge>
          </div>
          <div className="h-3 rounded-full bg-surface-1 overflow-hidden relative">
            {(() => {
              const absMax = Math.max(Math.abs(cvValue), config.cv_threshold * 1.5);
              const pct = Math.min(Math.abs(cvValue) / absMax * 50, 50);
              const isPos = cvValue >= 0;
              return (
                <div
                  className={`absolute top-0 h-full rounded-full transition-all duration-500 ${isPos ? 'bg-green-500' : 'bg-red-500'}`}
                  style={{
                    width: `${pct}%`,
                    left: isPos ? '50%' : `${50 - pct}%`,
                  }}
                />
              );
            })()}
            <div className="absolute top-0 h-full w-px bg-yellow-400/40" style={{ left: `${50 + (config.cv_threshold / (Math.max(Math.abs(cvValue || 1), config.cv_threshold * 1.5) * 2)) * 100}%` }} />
            <div className="absolute top-0 h-full w-px bg-yellow-400/40" style={{ left: `${50 - (config.cv_threshold / (Math.max(Math.abs(cvValue || 1), config.cv_threshold * 1.5) * 2)) * 100}%` }} />
            <div className="absolute top-0 h-full w-px bg-gray-500" style={{ left: '50%' }} />
          </div>
          <div className="flex justify-between text-[10px] text-gray-500 mt-1">
            <span>−{config.cv_threshold.toLocaleString()}</span>
            <span>0</span>
            <span>+{config.cv_threshold.toLocaleString()}</span>
          </div>
        </div>
      )}

      {/* ── Trade log ──────────────────────────── */}
      {tradeLog.length > 0 && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-surface-3">
            <h3 className="text-sm font-semibold text-white">Trade Log</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-400 text-xs uppercase tracking-wider border-b border-surface-3">
                  <th className="px-4 py-2 text-left">Date</th>
                  <th className="px-4 py-2 text-left">Signal</th>
                  <th className="px-4 py-2 text-left">Option</th>
                  <th className="px-4 py-2 text-right">Entry</th>
                  <th className="px-4 py-2 text-left">Exit</th>
                  <th className="px-4 py-2 text-right">Exit Price</th>
                  <th className="px-4 py-2 text-right">ADX</th>
                  <th className="px-4 py-2 text-left">CV</th>
                  <th className="px-4 py-2 text-right">PnL</th>
                </tr>
              </thead>
              <tbody>
                {[...tradeLog].reverse().map((t, i) => (
                  <tr key={i} className="border-b border-surface-3 last:border-0 hover:bg-surface-3/50">
                    <td className="px-4 py-2 text-gray-300">{t.date}</td>
                    <td className="px-4 py-2">
                      <Badge className={t.signal === 'CE' ? 'bg-green-600/20 text-green-400' : 'bg-red-600/20 text-red-400'}>
                        {t.signal}
                      </Badge>
                    </td>
                    <td className="px-4 py-2 font-mono text-gray-300 text-xs">{t.option}</td>
                    <td className="px-4 py-2 text-right font-mono text-white">{t.entry_price?.toFixed(2)}</td>
                    <td className="px-4 py-2">
                      <Badge className={
                        t.exit_type === 'TARGET_HIT' ? 'bg-green-600/20 text-green-400' :
                        t.exit_type === 'TREND_EXIT' ? 'bg-yellow-600/20 text-yellow-400' :
                        'bg-red-600/20 text-red-400'
                      }>
                        {t.exit_type === 'TARGET_HIT' ? 'Target' : t.exit_type === 'SL_HIT' ? 'SL' : t.exit_type === 'TREND_EXIT' ? 'Trend' : t.exit_type}
                      </Badge>
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-white">{t.exit_price?.toFixed(2)}</td>
                    <td className="px-4 py-2 text-right font-mono text-gray-400 text-xs">{t.indicators?.adx?.toFixed(1) ?? '—'}</td>
                    <td className="px-4 py-2 text-xs">
                      <span className={
                        t.indicators?.cv_trend === 'Bullish' ? 'text-green-400' :
                        t.indicators?.cv_trend === 'Bearish' ? 'text-red-400' : 'text-gray-500'
                      }>
                        {t.indicators?.cv_trend ?? '—'}
                      </span>
                    </td>
                    <td className={`px-4 py-2 text-right font-bold font-mono ${t.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {t.pnl >= 0 ? '+' : ''}{t.pnl?.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Modals ─────────────────────────────── */}
      <PinModal
        open={pinOpen}
        onClose={() => setPinOpen(false)}
        onSuccess={() => { setPinOpen(false); setDocOpen(true); }}
      />
      <StrategyDocModal open={docOpen} onClose={() => setDocOpen(false)} />
    </div>
  );
}
