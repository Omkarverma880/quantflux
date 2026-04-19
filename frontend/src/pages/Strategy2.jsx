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
} from 'lucide-react';

const REFRESH_MS = 2_000;  // 2s — match Strategy 1/3 for real-time view

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

export default function Strategy2() {
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
  });
  const [countdown, setCountdown] = useState(2);
  const [pinOpen, setPinOpen] = useState(false);
  const [docOpen, setDocOpen] = useState(false);
  const timerRef = useRef(null);
  const countdownRef = useRef(null);

  /* ── Data fetching ─────────────────────────── */

  const fetchStatus = useCallback(async () => {
    try {
      setLoading(true);
      const res = await api.getStrategy2TradeStatus();
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
      const res = await api.strategy2TradeCheck();
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
      const res = await api.strategy2TradeStart(config);
      setStatus(res);
    } catch (e) {
      console.error('start', e);
    }
  };

  const handleStop = async () => {
    try {
      const res = await api.strategy2TradeStop();
      setStatus(res);
    } catch (e) {
      console.error('stop', e);
    }
  };

  const handleSaveConfig = async () => {
    try {
      await api.strategy2TradeUpdateConfig(config);
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
  const cvValue = status?.cv_value ?? null;
  const spotPrice = status?.spot_price ?? trade?.atm_strike ?? 0;
  const tradeLog = status?.trade_log || [];
  const unrealizedPnl = trade?.unrealized_pnl ?? 0;

  /* ── Signal label helper (selling: flipped) ── */
  // Bullish CV → SELL PUT, Bearish CV → SELL CALL
  const signalLabel = signalType === 'PE' ? 'SELL PUT' : signalType === 'CE' ? 'SELL CALL' : null;

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
    <div className="p-6 space-y-6 max-w-6xl mx-auto">
      {/* ── Header ─────────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white">Strategy 2 — Option Selling</h1>
          <p className="text-sm text-gray-400 mt-1">
            ATM option selling at ceiling Gann level when cumulative volume breaches threshold
          </p>
        </div>
        <div className="flex items-center gap-3">
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
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-red-600/20 text-red-400 border border-red-500/30 hover:bg-red-600/30 transition text-sm font-medium"
            >
              <Square className="w-4 h-4" /> Stop
            </button>
          ) : (
            <button
              onClick={handleStart}
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-green-600/20 text-green-400 border border-green-500/30 hover:bg-green-600/30 transition text-sm font-medium"
            >
              <Play className="w-4 h-4" /> Start
            </button>
          )}

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
              {config.gann_target ? 'Target = prev Gann level (premium decay)' : `Target = entry − ${config.target_points} pts`}
            </span>
          </div>
          <p className="text-xs text-gray-500 mt-2">
            Proximity: SL/Target orders stay hidden in memory and are only placed on the exchange when LTP comes within this many points of the level.
          </p>
          <p className="text-xs text-orange-400/70 mt-1">
            Option Selling: SL triggers when premium RISES above entry + SL pts. Target triggers when premium DECAYS below entry − Target pts.
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
          {signalLabel ? (
            <div className="flex items-center gap-2">
              {signalType === 'PE' ? (
                <TrendingUp className="w-5 h-5 text-green-400" />
              ) : (
                <TrendingDown className="w-5 h-5 text-red-400" />
              )}
              <span className={`text-xl font-bold ${signalType === 'PE' ? 'text-green-400' : 'text-red-400'}`}>
                {signalLabel}
              </span>
            </div>
          ) : (
            <span className="text-xl font-bold text-gray-500">—</span>
          )}
        </Card>

        <Card title="Gann Entry (Ceil)" icon={Crosshair}>
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
          <h3 className="text-sm font-semibold text-white mb-3">Trade Setup (Short)</h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
            <div>
              <span className="text-gray-400">Option</span>
              <p className="font-mono text-white">{trade.option_symbol}</p>
            </div>
            <div>
              <span className="text-gray-400">Entry (Gann Ceil)</span>
              <p className="font-mono text-white">{trade.gann_entry_price?.toFixed(2)}</p>
            </div>
            <div>
              <span className="text-gray-400">SL Level (premium rise)</span>
              <p className="font-mono text-red-400">{trade.sl_price?.toFixed(2)}</p>
            </div>
            <div>
              <span className="text-gray-400">Target Level (premium decay)</span>
              <p className="font-mono text-green-400">{trade.target_price?.toFixed(2)}</p>
            </div>
          </div>
        </div>
      )}

      {/* ── Orders ─────────────────────────────── */}
      {(orders.entry || orders.sl || orders.target) && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-5">
          <h3 className="text-sm font-semibold text-white mb-3">Orders</h3>
          <OrderRow label="Entry (LIMIT SELL)" order={orders.entry} icon={Crosshair} color="text-orange-400" />
          <OrderRow label="Stop Loss (BUY to cover)" order={orders.sl} icon={Shield} color="text-red-400" />
          <OrderRow label="Target (BUY to cover)" order={orders.target} icon={Target} color="text-green-400" />
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

      {/* ── Trade log ──────────────────────────── */}
      {tradeLog.length > 0 && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
          <div className="px-5 py-3 border-b border-surface-3">
            <h3 className="text-sm font-semibold text-white">Trade Log (Sells)</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-gray-400 text-xs uppercase tracking-wider border-b border-surface-3">
                  <th className="px-4 py-2 text-left">Date</th>
                  <th className="px-4 py-2 text-left">Signal</th>
                  <th className="px-4 py-2 text-left">Option</th>
                  <th className="px-4 py-2 text-right">Entry (Sell)</th>
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
                      <Badge className={t.signal === 'PE' ? 'bg-green-600/20 text-green-400' : 'bg-red-600/20 text-red-400'}>
                        {t.signal === 'PE' ? 'SELL PUT' : 'SELL CALL'}
                      </Badge>
                    </td>
                    <td className="px-4 py-2 font-mono text-gray-300 text-xs">{t.option}</td>
                    <td className="px-4 py-2 text-right font-mono text-white">{t.entry_price?.toFixed(2)}</td>
                    <td className="px-4 py-2">
                      <Badge className={t.exit_type === 'TARGET_HIT' ? 'bg-green-600/20 text-green-400' : 'bg-red-600/20 text-red-400'}>
                        {t.exit_type === 'TARGET_HIT' ? 'Target' : t.exit_type === 'SL_HIT' ? 'SL' : t.exit_type}
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
      {!isActive && state === 'IDLE' && !trade.option_symbol && (
        <div className="text-center py-16 text-gray-500">
          <Zap className="w-12 h-12 mx-auto mb-4 opacity-30" />
          <p className="text-lg font-medium">Strategy is inactive</p>
          <p className="text-sm mt-1">Configure parameters and press Start to begin option selling</p>
        </div>
      )}

      {/* ── Modals ─────────────────────────────── */}
      <PinModal
        open={pinOpen}
        onClose={() => setPinOpen(false)}
        onSuccess={() => { setPinOpen(false); setDocOpen(true); }}
      />
      <Strategy2DocModal open={docOpen} onClose={() => setDocOpen(false)} />
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

/* ── Strategy 2 Documentation Modal ─────────────── */
function Strategy2DocModal({ open, onClose }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div
        className="bg-surface-1 border border-surface-3 rounded-2xl w-full max-w-2xl max-h-[85vh] overflow-y-auto shadow-2xl mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 bg-surface-1 border-b border-surface-3 px-6 py-4 flex items-center justify-between">
          <h2 className="text-lg font-bold text-white">Strategy 2 — Documentation</h2>
          <button onClick={onClose} className="p-1 rounded-lg hover:bg-surface-3 text-gray-400 hover:text-white transition">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="px-6 py-5 space-y-6 text-sm">
          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Strategy Name</h3>
            <p className="text-white">Strategy 2 — Gann + Cumulative Volume Option Selling</p>
          </section>
          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Strategy Type</h3>
            <p className="text-gray-300">Intraday Option Selling / Writing — NIFTY ATM CE / PE (MIS)</p>
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
                <p className="text-yellow-400 font-medium text-xs uppercase mb-1">Step 1 — CV Signal Detection (Inverted)</p>
                <ul className="list-disc list-inside ml-3 space-y-0.5">
                  <li><span className="text-green-400">Bullish CV:</span> CV &gt; +cv_threshold → Sell PUT (premium decays as market rises)</li>
                  <li><span className="text-red-400">Bearish CV:</span> CV &lt; −cv_threshold → Sell CALL (premium decays as market falls)</li>
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
                  <li>Entry price = <span className="text-white font-medium">Ceiling Gann level</span> of option LTP (sell high)</li>
                  <li>LIMIT SELL order placed at the Gann entry price</li>
                </ul>
              </div>
            </div>
          </section>

          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Exit Logic</h3>
            <ul className="list-disc list-inside text-gray-300 space-y-0.5">
              <li><span className="text-white font-medium">Stop Loss</span> — Entry + SL points (premium rising = loss for seller, default 45 pts)</li>
              <li><span className="text-white font-medium">Target (Fixed)</span> — Entry − Target points (premium decay = profit, default 55 pts)</li>
              <li><span className="text-white font-medium">Target (Gann)</span> — Previous Gann level below entry (when gann_target enabled)</li>
              <li><span className="text-white font-medium">Auto Square-off</span> — At 15:15 (force buy-back to cover short position)</li>
            </ul>
          </section>

          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Risk Management</h3>
            <ul className="list-disc list-inside text-gray-300 space-y-0.5">
              <li>Maximum 1 trade per day (no re-entry)</li>
              <li>Configurable SL and target in absolute points</li>
              <li>Target price floored at ₹0.05 to prevent invalid prices</li>
              <li>Orphan trade detection on day reset</li>
            </ul>
          </section>

          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Execution Logic</h3>
            <ul className="list-disc list-inside text-gray-300 space-y-0.5">
              <li>Shadow (hidden) SL/Target orders — placed on exchange only when LTP approaches</li>
              <li>SL proximity trigger → real SL-M (Stop Loss Market) BUY order (buy back to cover)</li>
              <li>Target proximity trigger → real LIMIT BUY order (buy back at profit)</li>
              <li>Conflict avoidance: cancels opposing order before placing new one</li>
              <li>Aggressive exit if LTP breached: SL → LIMIT BUY at LTP × 1.10, Target → LIMIT BUY at LTP × 0.90</li>
              <li>Paper trade mode for testing without real orders</li>
            </ul>
          </section>

          <section>
            <h3 className="text-brand-400 font-semibold uppercase tracking-wider text-xs mb-2">Key Differences from Strategy 1</h3>
            <ul className="list-disc list-inside text-gray-300 space-y-0.5">
              <li><span className="text-white font-medium">Selling vs Buying</span> — Profits from premium decay (theta), not directional move</li>
              <li><span className="text-white font-medium">Ceiling Gann</span> — Entry at ceiling level (sell high) vs floor level (buy low)</li>
              <li><span className="text-white font-medium">Inverted SL/Target</span> — SL is above entry, target is below entry</li>
              <li><span className="text-white font-medium">No Re-entry</span> — Always completes after one trade per day</li>
              <li><span className="text-white font-medium">No Backtest</span> — Historical simulation not available</li>
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
