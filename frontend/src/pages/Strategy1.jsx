import React, { useState, useEffect, useRef, useCallback } from 'react';
import { api } from '../api';
import {
  Play,
  Square,
  Settings2,
  ChevronDown,
  ChevronUp,
  Crosshair,
  Shield,
  Info,
  X,
  Lock,
  Target,
  TrendingUp,
  TrendingDown,
  Zap,
  Clock,
  CheckCircle2,
  XCircle,
  AlertCircle,
  RefreshCw,
  FlaskConical,
} from 'lucide-react';

const REFRESH_MS = 2_000;  // 2s — fast check cycle

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

export default function Strategy1() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [configOpen, setConfigOpen] = useState(false);
  const [config, setConfig] = useState({
    sl_points: 45,
    target_points: 55,
    lot_size: 65,
    cv_threshold: 150000,
    strike_interval: 50,
    sl_proximity: 5,
    target_proximity: 5,
    gann_target: false,
    re_entry: false,
  });
  const [countdown, setCountdown] = useState(2);
  const [backtest, setBacktest] = useState(null);
  const [backtesting, setBacktesting] = useState(false);
  const [pinOpen, setPinOpen] = useState(false);
  const [docOpen, setDocOpen] = useState(false);
  const timerRef = useRef(null);
  const countdownRef = useRef(null);

  /* ── Data fetching ─────────────────────────── */

  const fetchStatus = useCallback(async () => {
    try {
      setLoading(true);
      const res = await api.getStrategy1TradeStatus();
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
      const res = await api.strategy1TradeCheck();
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
      const res = await api.strategy1TradeStart(config);
      setStatus(res);
    } catch (e) {
      console.error('start', e);
    }
  };

  const handleStop = async () => {
    try {
      const res = await api.strategy1TradeStop();
      setStatus(res);
    } catch (e) {
      console.error('stop', e);
    }
  };

  const handleSaveConfig = async () => {
    try {
      await api.strategy1TradeUpdateConfig(config);
      setConfigOpen(false);
      fetchStatus();
    } catch (e) {
      console.error('config update', e);
    }
  };

  const handleBacktest = async () => {
    try {
      setBacktesting(true);
      setBacktest(null);
      const res = await api.strategy1TradeBacktest(config);
      setBacktest(res);
    } catch (e) {
      console.error('backtest', e);
      setBacktest({ status: 'error', message: e.message });
    } finally {
      setBacktesting(false);
    }
  };

  // Auto-refresh backtest when trade is OPEN (live P&L)
  const backtestRefreshRef = useRef(null);
  useEffect(() => {
    if (backtest?.status === 'signal_found' && backtest?.trade?.exit_type === 'OPEN') {
      backtestRefreshRef.current = setInterval(async () => {
        try {
          const res = await api.strategy1TradeBacktest(config);
          setBacktest(res);
        } catch (e) {
          console.error('backtest refresh', e);
        }
      }, REFRESH_MS);
    }
    return () => {
      if (backtestRefreshRef.current) clearInterval(backtestRefreshRef.current);
    };
  }, [backtest?.status, backtest?.trade?.exit_type, config]);

  /* ── Derived ───────────────────────────────── */

  const isActive = status?.is_active;
  const state = status?.state || 'IDLE';
  const stateStyle = STATE_STYLE[state] || STATE_STYLE.IDLE;
  const trade = status?.trade || {};
  const orders = status?.orders || {};
  const signalType = status?.signal_type;
  const cvValue = status?.cv_value ?? null;
  const spotPrice = status?.spot_price ?? trade?.atm_strike ?? 0;
  const tradeLog = status?.trade_log || [];
  const unrealizedPnl = trade?.unrealized_pnl ?? 0;
  const checklist = status?.entry_checklist || {};

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

  return (
    <div className="p-4 sm:p-6 space-y-4 sm:space-y-6 max-w-6xl mx-auto">
      {/* ── Header ─────────────────────────────── */}
      <div className="space-y-3">
        <div>
          <h1 className="text-lg sm:text-2xl font-bold text-white">Strategy 1 — Gann CV</h1>
          <p className="text-xs sm:text-sm text-gray-400 mt-1">
            ATM option entry at floor Gann level when cumulative volume breaches threshold
          </p>
        </div>
        <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
          <Badge className={stateStyle.bg + ' ' + stateStyle.text}>{stateStyle.label}</Badge>

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

          <button
            onClick={handleBacktest}
            disabled={backtesting}
            className="flex items-center gap-2 px-3 sm:px-4 py-2 rounded-lg bg-purple-600/20 text-purple-400 border border-purple-500/30 hover:bg-purple-600/30 transition text-sm font-medium disabled:opacity-50"
          >
            <FlaskConical className="w-4 h-4" /> {backtesting ? 'Running…' : 'Backtest'}
          </button>

          {/* Info icon → PIN → Documentation */}
          <button
            onClick={() => setPinOpen(true)}
            className="p-2 rounded-lg border border-surface-3 text-gray-400 hover:text-brand-400 hover:bg-surface-3 transition"
            title="Strategy Documentation"
          >
            <Info className="w-4 h-4" />
          </button>

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
            {configField('Lot Size', 'lot_size')}
            {configField('CV Threshold', 'cv_threshold', 10000)}
            {configField('Strike Interval', 'strike_interval')}
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4 mt-4">
            {configField('SL Proximity (pts)', 'sl_proximity')}
            {configField('Target Proximity (pts)', 'target_proximity')}
          </div>
          <div className="flex items-center gap-3 mt-4">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={config.gann_target ?? false}
                onChange={(e) => setConfig((c) => ({ ...c, gann_target: e.target.checked }))}
                className="w-4 h-4 rounded border-gray-600 bg-surface-1 text-brand-600 focus:ring-brand-500"
              />
              <span className="text-sm text-gray-300">Gann Target</span>
            </label>
            <span className="text-xs text-gray-500">
              {config.gann_target ? 'Target = next Gann level (ceiling)' : `Target = entry + ${config.target_points} pts`}
            </span>
          </div>
          <div className="flex items-center gap-3 mt-3">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={config.re_entry ?? false}
                onChange={(e) => setConfig((c) => ({ ...c, re_entry: e.target.checked }))}
                className="w-4 h-4 rounded border-gray-600 bg-surface-1 text-brand-600 focus:ring-brand-500"
              />
              <span className="text-sm text-gray-300">Re-entry on Target</span>
            </label>
            <span className="text-xs text-gray-500">
              {config.re_entry ? 'Auto re-enter same trade when target is hit' : 'No re-entry — stops after target or SL'}
            </span>
          </div>
          <p className="text-xs text-gray-500 mt-2">
            Proximity: SL/Target orders stay hidden in memory and are only placed on the exchange when LTP comes within this many points of the level.
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

      {/* ── Summary cards ──────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card title="Signal" icon={Zap}>
          {signalType ? (
            <div className="flex items-center gap-2">
              {signalType === 'CE' ? (
                <TrendingUp className="w-5 h-5 text-green-400" />
              ) : (
                <TrendingDown className="w-5 h-5 text-red-400" />
              )}
              <span className={`text-xl font-bold ${signalType === 'CE' ? 'text-green-400' : 'text-red-400'}`}>
                {signalType}
              </span>
            </div>
          ) : (
            <span className="text-xl font-bold text-gray-500">—</span>
          )}
        </Card>

        <Card title="Gann Entry" icon={Crosshair}>
          <span className="text-xl font-bold font-mono text-white">
            {trade.gann_entry_price ? trade.gann_entry_price.toFixed(2) : '—'}
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
          {spotPrice > 0 && spotPrice !== trade.atm_strike && (
            <span className="text-xs text-gray-400 ml-2">{spotPrice.toFixed?.(2) ?? spotPrice}</span>
          )}
        </Card>
      </div>

      {/* ── Trade setup ────────────────────────── */}
      {trade.option_symbol && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-5">
          <h3 className="text-sm font-semibold text-white mb-3">Trade Setup</h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
            <div>
              <span className="text-gray-400">Option</span>
              <p className="font-mono text-white">{trade.option_symbol}</p>
            </div>
            <div>
              <span className="text-gray-400">Entry (Gann)</span>
              <p className="font-mono text-white">{trade.gann_entry_price?.toFixed(2)}</p>
            </div>
            <div>
              <span className="text-gray-400">SL Level</span>
              <p className="font-mono text-red-400">{trade.sl_price?.toFixed(2)}</p>
            </div>
            <div>
              <span className="text-gray-400">Target Level</span>
              <p className="font-mono text-green-400">{trade.target_price?.toFixed(2)}</p>
            </div>
          </div>
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

      {/* ── CV indicator ───────────────────────── */}
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
            {/* Threshold markers */}
            <div className="absolute top-0 h-full w-px bg-yellow-400/40" style={{ left: `${50 + (config.cv_threshold / (Math.max(Math.abs(cvValue), config.cv_threshold * 1.5) * 2)) * 100}%` }} />
            <div className="absolute top-0 h-full w-px bg-yellow-400/40" style={{ left: `${50 - (config.cv_threshold / (Math.max(Math.abs(cvValue), config.cv_threshold * 1.5) * 2)) * 100}%` }} />
            <div className="absolute top-0 h-full w-px bg-gray-500" style={{ left: '50%' }} />
          </div>
          <div className="flex justify-between text-[10px] text-gray-500 mt-1">
            <span>−{config.cv_threshold.toLocaleString()}</span>
            <span>0</span>
            <span>+{config.cv_threshold.toLocaleString()}</span>
          </div>
        </div>
      )}

      {/* ── Entry Monitor (checklist) ──────────────── */}
      {isActive && state === 'IDLE' && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-5">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-yellow-400" />
              <h3 className="text-sm font-semibold text-white">Entry Monitor</h3>
              <Badge className="bg-gray-600/20 text-gray-400">Scanning</Badge>
            </div>
            {spotPrice > 0 && (
              <span className="text-xs text-gray-500">Spot: <span className="text-white font-mono">{spotPrice}</span></span>
            )}
          </div>

          {Object.keys(checklist).length > 0 ? (
            <div className="space-y-2">
              {/* CV progress toward threshold */}
              <div className="flex items-center justify-between text-sm">
                <div className="flex items-center gap-2">
                  {(checklist.cv_bullish || checklist.cv_bearish) ? (
                    <CheckCircle2 className="w-4 h-4 text-green-400" />
                  ) : (
                    <XCircle className="w-4 h-4 text-red-400/60" />
                  )}
                  <span className={`${(checklist.cv_bullish || checklist.cv_bearish) ? 'text-green-400' : 'text-gray-400'}`}>
                    CV: {checklist.cv_value?.toLocaleString()} / ±{checklist.cv_threshold?.toLocaleString()}
                  </span>
                </div>
                <span className="text-xs text-gray-500">
                  {checklist.cv_pct}% of threshold
                </span>
              </div>

              {/* CV direction */}
              <div className="flex items-center gap-2 text-sm">
                {checklist.cv_direction === 'Bullish' ? (
                  <TrendingUp className="w-4 h-4 text-green-400" />
                ) : checklist.cv_direction === 'Bearish' ? (
                  <TrendingDown className="w-4 h-4 text-red-400" />
                ) : (
                  <AlertCircle className="w-4 h-4 text-gray-400" />
                )}
                <span className={`${
                  checklist.cv_direction === 'Bullish' ? 'text-green-400' :
                  checklist.cv_direction === 'Bearish' ? 'text-red-400' : 'text-gray-400'
                }`}>
                  Direction: {checklist.cv_direction || '—'}
                  {checklist.signal && ` → Will BUY ${checklist.signal}`}
                </span>
              </div>

              {/* Progress bar for CV toward threshold */}
              <div className="mt-2">
                <div className="h-2 rounded-full bg-surface-1 overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${
                      checklist.cv_pct >= 100 ? 'bg-green-500' :
                      checklist.cv_pct >= 70 ? 'bg-yellow-500' : 'bg-gray-500'
                    }`}
                    style={{ width: `${Math.min(checklist.cv_pct, 100)}%` }}
                  />
                </div>
                <div className="flex justify-between text-[10px] text-gray-500 mt-0.5">
                  <span>0</span>
                  <span className="text-yellow-400/60">Threshold</span>
                  <span>100%</span>
                </div>
              </div>

              {/* Reason why not entered */}
              {!checklist.signal && (
                <p className="text-xs text-yellow-300 mt-2">
                  Waiting: CV magnitude ({Math.abs(checklist.cv_value ?? 0)?.toLocaleString()}) needs to reach ±{checklist.cv_threshold?.toLocaleString()} for entry signal
                </p>
              )}
            </div>
          ) : (
            <p className="text-sm text-gray-500">Waiting for first check cycle...</p>
          )}
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
                  <th className="px-4 py-2 text-right">Exit Time</th>
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
                      <Badge className={t.exit_type === 'TARGET_HIT' ? 'bg-green-600/20 text-green-400' : 'bg-red-600/20 text-red-400'}>
                        {t.exit_type === 'TARGET_HIT' ? 'Target' : 'SL'}
                      </Badge>
                    </td>
                    <td className="px-4 py-2 text-right font-mono text-white">{t.exit_price?.toFixed(2)}</td>
                    <td className="px-4 py-2 text-right font-mono text-gray-300">{t.exit_time || '—'}</td>
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

      {/* ── Empty state ────────────────────────── */}
      {!isActive && state === 'IDLE' && !trade.option_symbol && !backtest && (
        <div className="text-center py-16 text-gray-500">
          <Zap className="w-12 h-12 mx-auto mb-4 opacity-30" />
          <p className="text-lg font-medium">Strategy is inactive</p>
          <p className="text-sm mt-1">Configure parameters and press Start to begin monitoring, or run a Backtest</p>
        </div>
      )}

      {/* ── Backtest results ───────────────────── */}
      {backtest && (
        <div className="bg-surface-2 border border-purple-500/30 rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-surface-3 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <FlaskConical className="w-4 h-4 text-purple-400" />
              <h3 className="text-sm font-semibold text-white">
                Backtest Result — {backtest.data_date || 'N/A'}
              </h3>
              {backtest.is_simulated && (
                <Badge className="bg-yellow-600/20 text-yellow-400">Simulated Premiums</Badge>
              )}
            </div>
            <button onClick={() => setBacktest(null)} className="text-gray-500 hover:text-white text-xs">✕ Close</button>
          </div>

          <div className="p-5 space-y-4">
            {backtest.status === 'no_signal' && (
              <div className="text-center py-8">
                <AlertCircle className="w-10 h-10 mx-auto mb-3 text-yellow-400 opacity-60" />
                <p className="text-white font-medium">{backtest.message}</p>
                <div className="flex justify-center gap-6 mt-3 text-sm text-gray-400">
                  <span>Max CV: <span className="text-green-400 font-mono">{backtest.max_cv?.toLocaleString()}</span></span>
                  <span>Min CV: <span className="text-red-400 font-mono">{backtest.min_cv?.toLocaleString()}</span></span>
                  <span>Candles: {backtest.candle_count}</span>
                </div>
              </div>
            )}

            {backtest.status === 'signal_found' && (
              <>
                {/* Signal + Trade summary */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <Card title="Signal" icon={Zap}>
                    <div className="flex items-center gap-2">
                      {backtest.signal.type === 'CE' ? (
                        <TrendingUp className="w-5 h-5 text-green-400" />
                      ) : (
                        <TrendingDown className="w-5 h-5 text-red-400" />
                      )}
                      <span className={`text-lg font-bold ${backtest.signal.type === 'CE' ? 'text-green-400' : 'text-red-400'}`}>
                        {backtest.signal.type}
                      </span>
                      <span className="text-xs text-gray-400">@ {backtest.signal.time}</span>
                    </div>
                    <p className="text-xs text-gray-400 mt-1">CV: {backtest.signal.cv_value?.toLocaleString()}</p>
                  </Card>

                  <Card title="Entry" icon={Crosshair}>
                    <span className="text-lg font-bold font-mono text-white">
                      {backtest.trade.entry_price?.toFixed(2)}
                    </span>
                    <p className="text-xs text-gray-400 mt-1">
                      Premium: {backtest.option.premium_at_signal?.toFixed(2)} → Gann: {backtest.option.gann_entry_price?.toFixed(2)}
                    </p>
                  </Card>

                  <Card title="Exit" icon={Target}>
                    <div className="flex items-center gap-2">
                      {backtest.trade.exit_type === 'TARGET_HIT' ? (
                        <CheckCircle2 className="w-5 h-5 text-green-400" />
                      ) : backtest.trade.exit_type === 'SL_HIT' ? (
                        <XCircle className="w-5 h-5 text-red-400" />
                      ) : (
                        <Clock className="w-5 h-5 text-yellow-400" />
                      )}
                      <span className={`text-lg font-bold font-mono ${
                        backtest.trade.exit_type === 'TARGET_HIT' ? 'text-green-400' :
                        backtest.trade.exit_type === 'SL_HIT' ? 'text-red-400' : 'text-yellow-400'
                      }`}>
                        {backtest.trade.exit_price?.toFixed(2)}
                      </span>
                    </div>
                    <p className="text-xs text-gray-400 mt-1">
                      {backtest.trade.exit_type === 'TARGET_HIT' ? 'Target Hit' :
                       backtest.trade.exit_type === 'SL_HIT' ? 'Stop Loss Hit' : 'Still Open'}
                      {backtest.trade.exit_time && ` @ ${backtest.trade.exit_time}`}
                    </p>
                  </Card>

                  <Card title="P&L" icon={TrendingUp}>
                    <span className={`text-2xl font-bold font-mono ${backtest.trade.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {backtest.trade.pnl >= 0 ? '+' : ''}₹{backtest.trade.pnl?.toLocaleString()}
                    </span>
                    <p className="text-xs text-gray-400 mt-1">Lot: {backtest.trade.lot_size}</p>
                  </Card>
                </div>

                {/* Trade details row */}
                <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 text-sm">
                  <div className="bg-surface-1 rounded-lg p-3">
                    <span className="text-gray-400 text-xs">ATM Strike</span>
                    <p className="font-mono text-white">{backtest.signal.atm_strike}</p>
                  </div>
                  <div className="bg-surface-1 rounded-lg p-3">
                    <span className="text-gray-400 text-xs">Option</span>
                    <p className="font-mono text-white text-xs">{backtest.option.symbol}</p>
                  </div>
                  <div className="bg-surface-1 rounded-lg p-3">
                    <span className="text-gray-400 text-xs">SL Level</span>
                    <p className="font-mono text-red-400">{backtest.trade.sl_price?.toFixed(2)}</p>
                  </div>
                  <div className="bg-surface-1 rounded-lg p-3">
                    <span className="text-gray-400 text-xs">Target Level</span>
                    <p className="font-mono text-green-400">{backtest.trade.target_price?.toFixed(2)}</p>
                  </div>
                  <div className="bg-surface-1 rounded-lg p-3">
                    <span className="text-gray-400 text-xs">Spot @ Signal</span>
                    <p className="font-mono text-white">{backtest.signal.spot_price?.toFixed(2)}</p>
                  </div>
                </div>

                {/* Price trail */}
                {backtest.price_trail?.length > 0 && (
                  <details className="group">
                    <summary className="cursor-pointer text-sm text-gray-400 hover:text-white transition flex items-center gap-1">
                      <ChevronDown className="w-4 h-4 group-open:rotate-180 transition-transform" />
                      Price trail ({backtest.price_trail.length} candles after entry)
                    </summary>
                    <div className="mt-2 overflow-x-auto max-h-60 overflow-y-auto">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="text-gray-500 uppercase tracking-wider border-b border-surface-3">
                            <th className="px-3 py-1 text-left">Time</th>
                            {backtest.is_simulated ? (
                              <>
                                <th className="px-3 py-1 text-right">Sim. Price</th>
                                <th className="px-3 py-1 text-right">Futures</th>
                              </>
                            ) : (
                              <>
                                <th className="px-3 py-1 text-right">Open</th>
                                <th className="px-3 py-1 text-right">High</th>
                                <th className="px-3 py-1 text-right">Low</th>
                                <th className="px-3 py-1 text-right">Close</th>
                              </>
                            )}
                          </tr>
                        </thead>
                        <tbody>
                          {backtest.price_trail.map((p, i) => (
                            <tr key={i} className="border-b border-surface-3/50 hover:bg-surface-3/30">
                              <td className="px-3 py-1 text-gray-300">{p.time}</td>
                              {backtest.is_simulated ? (
                                <>
                                  <td className="px-3 py-1 text-right font-mono text-white">{p.simulated_price?.toFixed(2)}</td>
                                  <td className="px-3 py-1 text-right font-mono text-gray-400">{p.futures_close?.toFixed(2)}</td>
                                </>
                              ) : (
                                <>
                                  <td className="px-3 py-1 text-right font-mono text-gray-400">{p.open?.toFixed(2)}</td>
                                  <td className="px-3 py-1 text-right font-mono text-green-400">{p.high?.toFixed(2)}</td>
                                  <td className="px-3 py-1 text-right font-mono text-red-400">{p.low?.toFixed(2)}</td>
                                  <td className="px-3 py-1 text-right font-mono text-white">{p.close?.toFixed(2)}</td>
                                </>
                              )}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </details>
                )}
              </>
            )}

            {backtest.status === 'error' && (
              <div className="text-center py-8">
                <XCircle className="w-10 h-10 mx-auto mb-3 text-red-400 opacity-60" />
                <p className="text-red-400 font-medium">{backtest.message}</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Modals ─────────────────────────────── */}
      <PinModal
        open={pinOpen}
        onClose={() => setPinOpen(false)}
        onSuccess={() => { setPinOpen(false); setDocOpen(true); }}
      />
      <Strategy1DocModal open={docOpen} onClose={() => setDocOpen(false)} />
    </div>
  );
}

/* ── PIN Modal ──────────────────────────────────── */
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

/* ── Strategy 1 Documentation Modal ─────────────── */
function Strategy1DocModal({ open, onClose }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div
        className="bg-surface-1 border border-surface-3 rounded-2xl w-full max-w-2xl max-h-[85vh] overflow-y-auto shadow-2xl mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 bg-surface-1 border-b border-surface-3 px-6 py-4 flex items-center justify-between">
          <h2 className="text-lg font-bold text-white">Strategy 1 — Documentation</h2>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-surface-3 text-gray-400 hover:text-white transition">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="px-6 py-5 space-y-6 text-sm">
          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Strategy Name</h3>
            <p className="text-white">Strategy 1 — Gann + Cumulative Volume Entry (Option Buying)</p>
          </section>
          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Strategy Type</h3>
            <p className="text-gray-300">Intraday Option Buying — NIFTY ATM CE / PE (MIS)</p>
          </section>

          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Indicators</h3>
            <ul className="space-y-1.5 text-gray-300 list-disc list-inside">
              <li><span className="text-white font-medium">Cumulative Volume (CV)</span> — Aggregated buy/sell volume differential</li>
              <li><span className="text-white font-medium">Gann Levels</span> — Static support/resistance from gann_levels.csv</li>
            </ul>
          </section>

          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Entry Logic</h3>
            <div className="space-y-3 text-gray-300">
              <div>
                <p className="text-yellow-400 font-medium text-xs uppercase mb-1">Step 1 — CV Signal Detection</p>
                <ul className="list-disc list-inside ml-3 space-y-0.5">
                  <li><span className="text-green-400">Bullish:</span> CV &gt; +cv_threshold → Buy CE (Call)</li>
                  <li><span className="text-red-400">Bearish:</span> CV &lt; −cv_threshold → Buy PE (Put)</li>
                </ul>
              </div>
              <div>
                <p className="text-yellow-400 font-medium text-xs uppercase mb-1">Step 2 — Strike & Option Selection</p>
                <ul className="list-disc list-inside ml-3 space-y-0.5">
                  <li>ATM strike = round(spot / strike_interval) × strike_interval</li>
                  <li>Nearest expiry NIFTY option at that strike</li>
                </ul>
              </div>
              <div>
                <p className="text-yellow-400 font-medium text-xs uppercase mb-1">Step 3 — Gann Entry Price</p>
                <ul className="list-disc list-inside ml-3 space-y-0.5">
                  <li>Entry price = <span className="text-white font-medium">Floor Gann level</span> of option LTP (buy low)</li>
                  <li>LIMIT BUY order placed at the Gann entry price</li>
                </ul>
              </div>
            </div>
          </section>

          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Exit Logic</h3>
            <ul className="list-disc list-inside text-gray-300 space-y-0.5">
              <li><span className="text-white font-medium">Stop Loss</span> — Entry − SL points (default 45 pts)</li>
              <li><span className="text-white font-medium">Target (Fixed)</span> — Entry + Target points (default 55 pts)</li>
              <li><span className="text-white font-medium">Target (Gann)</span> — Ceiling Gann level above entry (when gann_target enabled)</li>
              <li><span className="text-white font-medium">Auto Square-off</span> — At 15:15 (force exit regardless of P&L)</li>
            </ul>
          </section>

          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Risk Management</h3>
            <ul className="list-disc list-inside text-gray-300 space-y-0.5">
              <li>Maximum 1 trade per day (unless re-entry enabled)</li>
              <li>Configurable SL and target in absolute points</li>
              <li>Entry diagnostic checklist for debugging signal issues</li>
              <li>Orphan trade detection on day reset</li>
            </ul>
          </section>

          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Execution Logic</h3>
            <ul className="list-disc list-inside text-gray-300 space-y-0.5">
              <li>Shadow (hidden) SL/Target orders — placed on exchange only when LTP approaches</li>
              <li>SL proximity trigger → real SL-M (Stop Loss Market) SELL order</li>
              <li>Target proximity trigger → real LIMIT SELL order</li>
              <li>Conflict avoidance: cancels opposing order before placing new one</li>
              <li>Aggressive exit if LTP already breached level (LIMIT SELL at LTP × 0.90)</li>
              <li>Paper trade mode for testing without real orders</li>
            </ul>
          </section>

          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Special Features</h3>
            <ul className="list-disc list-inside text-gray-300 space-y-0.5">
              <li><span className="text-white font-medium">Re-entry Mode</span> — After TARGET_HIT, auto re-enters same trade without new CV signal</li>
              <li><span className="text-white font-medium">Gann Target Mode</span> — Uses next Gann level above entry as profit target</li>
              <li><span className="text-white font-medium">Backtest</span> — Built-in historical simulation using candle data</li>
              <li>State persists across server restarts (strategy1_state.json)</li>
              <li>Trade history logged to strategy1_trades.json</li>
            </ul>
          </section>

          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Time Rules</h3>
            <ul className="list-disc list-inside text-gray-300 space-y-0.5">
              <li>Trading window: 9:15 AM — 3:30 PM</li>
              <li>Auto square-off: 3:15 PM</li>
              <li>Polling interval: ~60 seconds</li>
              <li>Day reset: automatic on new calendar day</li>
            </ul>
          </section>
        </div>
      </div>
    </div>
  );
}
