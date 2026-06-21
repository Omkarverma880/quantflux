import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, useWebSocket } from '../api';
import { useToast } from '../ToastContext';
import { DashboardSkeleton } from '../components/ErrorBoundary';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts';
import {
  TrendingUp,
  TrendingDown,
  Wallet,
  BarChart3,
  ShieldCheck,
  ShieldAlert,
  Play,
  Square,
  RefreshCw,
  Zap,
  Clock,
  Target,
  CircleDot,
  ArrowUpCircle,
  ArrowDownCircle,
  IndianRupee,
  AlertTriangle,
  Activity,
  ChevronRight,
  Radio,
  AlertOctagon,
} from 'lucide-react';

/* ── Helpers ───────────────────────────────────── */

const INR = (v, decimals = 0) =>
  (v || 0).toLocaleString('en-IN', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });

const STATE_META = {
  IDLE:          { dot: 'bg-gray-500',   label: 'Idle',     text: 'text-gray-500', bg: 'bg-gray-500/10' },
  RUNNING:       { dot: 'bg-brand-400 animate-pulse', label: 'Running', text: 'text-brand-400', bg: 'bg-brand-400/10' },
  ORDER_PLACED:  { dot: 'bg-yellow-400 animate-pulse', label: 'Order Placed', text: 'text-yellow-400', bg: 'bg-yellow-400/10' },
  POSITION_OPEN: { dot: 'bg-blue-400 animate-pulse',  label: 'In Position', text: 'text-blue-400', bg: 'bg-blue-400/10' },
  COMPLETED:     { dot: 'bg-green-400',  label: 'Completed', text: 'text-green-400', bg: 'bg-green-400/10' },
};

/* ── Strategy Card ───────────────────────────── */

function StrategyCard({ label, shortName, data, onClick }) {
  if (!data) return null;
  const state = data.state || 'IDLE';
  const meta = STATE_META[state] || STATE_META.IDLE;
  const trade = data.trade || {};
  const signal = data.signal_type;
  const pnl = trade.unrealized_pnl || 0;
  const lastTrade = (data.trade_log || []).slice(-1)[0];
  const lastPnl = lastTrade?.pnl ?? null;
  const isOpen = state === 'POSITION_OPEN';
  const isActive = data.is_active;
  const tradeCount = data.trade_log?.length || 0;

  // Compute total realized P&L for this strategy
  const totalPnl = (data.trade_log || []).reduce((s, t) => s + (t.pnl || 0), 0) +
    (isOpen ? pnl : 0);

  return (
    <div
      onClick={onClick}
      className="group relative bg-surface-1 border border-surface-3 rounded-xl overflow-hidden
                 hover:border-brand-500/30 hover:shadow-lg hover:shadow-brand-900/10 transition-all cursor-pointer"
    >
      {/* Top accent bar */}
      <div className={`h-0.5 ${isActive ? (isOpen ? 'bg-blue-500' : 'bg-brand-500') : 'bg-surface-3'}`} />

      <div className="p-4">
        {/* Header */}
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2.5">
            <div className={`w-7 h-7 rounded-lg ${meta.bg} flex items-center justify-center`}>
              <Activity className={`w-3.5 h-3.5 ${meta.text}`} />
            </div>
            <div>
              <p className="text-sm font-semibold text-white group-hover:text-brand-400 transition leading-tight">
                {label}
              </p>
              <p className="text-[10px] text-gray-600">{shortName}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {signal && (
              <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                signal === 'CE' ? 'bg-green-600/15 text-green-400 border border-green-500/20'
                              : 'bg-red-600/15 text-red-400 border border-red-500/20'
              }`}>{signal}</span>
            )}
            <span className={`px-2 py-0.5 rounded-md text-[10px] font-semibold ${meta.text} ${meta.bg}`}>
              {meta.label}
            </span>
          </div>
        </div>

        {/* Body: two-row, two-column compact grid so long option symbols
            don't overflow alongside numerics. */}
        {trade.option_symbol ? (
          <div className="grid grid-cols-2 gap-x-3 gap-y-2 pt-3 border-t border-surface-3/60">
            <Stat label="Option" value={trade.option_symbol} mono truncate />
            <Stat
              label={isOpen ? 'Unrealized' : 'Last P&L'}
              value={
                isOpen
                  ? `${pnl >= 0 ? '+' : ''}₹${pnl.toFixed(0)}`
                  : lastPnl !== null ? `${lastPnl >= 0 ? '+' : ''}₹${lastPnl.toFixed(0)}` : '—'
              }
              mono
              color={isOpen ? (pnl >= 0 ? 'text-green-400' : 'text-red-400')
                           : lastPnl !== null ? (lastPnl >= 0 ? 'text-green-400' : 'text-red-400') : 'text-gray-500'}
            />
            <Stat label="Entry" value={`₹${(trade.fill_price || trade.gann_entry_price || trade.entry_price || 0).toFixed(1)}`} mono />
            <Stat label="LTP" value={trade.current_ltp > 0 ? `₹${trade.current_ltp.toFixed(1)}` : '—'} mono />
          </div>
        ) : (
          <div className="pt-3 border-t border-surface-3/60 flex items-center justify-between">
            <p className="text-[11px] text-gray-600">
              {isActive ? (
                <span className="flex items-center gap-1.5">
                  <Radio className="w-3 h-3 text-brand-400 animate-pulse" />
                  Scanning for entry signal…
                </span>
              ) : 'Strategy not running'}
            </p>
            <ChevronRight className="w-3.5 h-3.5 text-gray-600 group-hover:text-brand-400 transition" />
          </div>
        )}

        {/* Footer with P&L bar */}
        {tradeCount > 0 && (
          <div className="flex items-center justify-between mt-3 pt-2.5 border-t border-surface-3/40">
            <span className="text-[10px] text-gray-600">{tradeCount} trade{tradeCount !== 1 ? 's' : ''} today</span>
            <span className={`text-xs font-bold mono ${totalPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {totalPnl >= 0 ? '+' : ''}₹{INR(totalPnl, 0)}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value, mono, truncate, color }) {
  return (
    <div className="min-w-0">
      <p className="text-[10px] text-gray-500 mb-1">{label}</p>
      <p className={`text-xs font-medium ${color || 'text-white'} ${mono ? 'mono' : ''} ${truncate ? 'truncate' : ''}`}>
        {value}
      </p>
    </div>
  );
}

/* ── Gauge (reusable) ────────────────────────── */

function Gauge({ label, value, max, unit = '', warn = false }) {
  const pct = max > 0 ? Math.min((Math.abs(value) / max) * 100, 100) : 0;
  const danger = pct > 80;
  return (
    <div className="space-y-1.5">
      <div className="flex justify-between text-xs">
        <span className="text-gray-400">{label}</span>
        <span className={`mono font-medium ${danger || warn ? 'text-red-400' : 'text-gray-300'}`}>
          {value}{unit} / {max}{unit}
        </span>
      </div>
      <div className="h-1.5 bg-surface-3 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-700 ${
            danger ? 'bg-red-500' : pct > 50 ? 'bg-yellow-500' : 'bg-brand-500'
          }`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

/* ── Metric card for top row ─────────────────── */

function MetricCard({ icon: Icon, label, value, sub, color = 'brand', valueClass }) {
  const gradients = {
    brand: 'from-brand-500/8 to-transparent border-brand-500/12',
    green: 'from-green-500/8 to-transparent border-green-500/12',
    red:   'from-red-500/8 to-transparent border-red-500/12',
    blue:  'from-blue-500/8 to-transparent border-blue-500/12',
  };
  const iconColors = {
    brand: 'text-brand-400',
    green: 'text-green-400',
    red: 'text-red-400',
    blue: 'text-blue-400',
  };

  return (
    <div className={`bg-gradient-to-br ${gradients[color]} bg-surface-1 border rounded-xl p-4`}>
      <div className="flex items-center gap-2 mb-2.5">
        <Icon className={`w-4 h-4 ${iconColors[color]}`} />
        <span className="text-[11px] text-gray-500 uppercase tracking-wider font-medium">{label}</span>
      </div>
      <p className={`text-xl font-bold mono ${valueClass || 'text-white'}`}>{value}</p>
      {sub && <p className="text-[10px] text-gray-600 mt-1">{sub}</p>}
    </div>
  );
}

/* ── Instant kill-switch tile ──────────────────── */

function ExitAllTile() {
  const toast = useToast();
  const [busy, setBusy] = useState(false);

  const handleExit = async () => {
    if (busy) return;
    if (!window.confirm(
      'EXIT ALL POSITIONS?\n\n' +
      'This cancels every open order and squares off every active ' +
      'position at MARKET. This cannot be undone.'
    )) return;
    setBusy(true);
    try {
      const r = await api.exitAllPositions();
      const c = r?.cancelled_orders?.length || 0;
      const s = r?.squared_off?.length || 0;
      const ce = r?.cancel_errors?.length || 0;
      const se = r?.squareoff_errors?.length || 0;
      const sk = (r?.skipped_positions?.length || 0) + (r?.skipped_orders?.length || 0);
      if (ce || se) {
        toast.error(`Exit All: ${s} squared / ${c} cancelled. ${ce + se} error(s) — check logs.`);
      } else if (s === 0 && c === 0 && sk > 0) {
        const sample = (r?.skipped_positions || []).slice(0, 2)
          .map(p => `${p.tradingsymbol} (${p.product}/${p.exchange})`).join(', ');
        toast.error(
          `Exit All scope = MIS options only. Skipped ${sk} item(s)` +
          (sample ? `: ${sample}${sk > 2 ? '…' : ''}` : '') +
          '. Use Manual Trading to exit other positions.'
        );
      } else if (s === 0 && c === 0) {
        toast.info('Nothing to exit — no open orders or positions.');
      } else {
        toast.success(`Exit All complete: ${s} squared off, ${c} order(s) cancelled.`);
      }
      window.dispatchEvent(new Event('positions:refresh'));
      window.dispatchEvent(new Event('orders:refresh'));
    } catch (e) {
      toast.error(`Exit All failed: ${e?.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      onClick={handleExit}
      disabled={busy}
      className="group bg-gradient-to-br from-red-600/15 to-red-500/5 bg-surface-1
                 border border-red-500/30 hover:border-red-400/60
                 rounded-xl p-4 text-left transition-all
                 hover:shadow-lg hover:shadow-red-900/20
                 disabled:opacity-60 disabled:cursor-not-allowed"
    >
      <div className="flex items-center gap-2 mb-2.5">
        <AlertOctagon className={`w-4 h-4 text-red-400 ${busy ? 'animate-pulse' : 'group-hover:animate-pulse'}`} />
        <span className="text-[11px] text-red-300/80 uppercase tracking-wider font-medium">
          Kill Switch
        </span>
      </div>
      <p className="text-xl font-bold mono text-red-400">
        {busy ? 'EXITING…' : 'EXIT ALL'}
      </p>
      <p className="text-[10px] text-gray-500 mt-1">
        Cancel orders & square-off at MARKET
      </p>
    </button>
  );
}

/* ── MAIN ────────────────────────────────────── */

/* ── Risk Fence: P&L lock + Day-loss control + Auto-squareoff status ── */

function RiskFencePanels({ marketOpen }) {
  const toast = useToast();
  const [cfg, setCfg] = useState(null);
  const [livePnl, setLivePnl] = useState(null);
  const [autoTime, setAutoTime] = useState('15:15');
  // local form state
  const [pf, setPf] = useState({
    enabled: false,
    profit_target: '',
    exit_floor: '',
    trail_amount: '',
  });
  const [lc, setLc] = useState({ enabled: false, max_day_loss: '' });
  const [savingPf, setSavingPf] = useState(false);
  const [savingLc, setSavingLc] = useState(false);
  const [savedPf, setSavedPf] = useState(false);
  const [savedLc, setSavedLc] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [squaring, setSquaring] = useState(false);
  // dirty flags — when user is editing, don't let polling clobber inputs
  const pfDirty = useRef(false);
  const lcDirty = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const r = await api.getRiskConfig();
      setCfg(r.config);
      setLivePnl(r.live_pnl);
      setAutoTime(r.auto_squareoff_at || '15:15');
      if (!pfDirty.current) {
        const fence = r.config.pnl_fence || {};
        // Prefer new signed fields; fall back to legacy lock_profit / max_loss
        // for clients that haven't been migrated yet.
        const pt = fence.profit_target ??
          (Number(fence.lock_profit) > 0 ? Number(fence.lock_profit) : null);
        const ef = fence.exit_floor ??
          (Number(fence.max_loss) > 0 ? -Number(fence.max_loss) : null);
        setPf({
          enabled: !!fence.enabled,
          profit_target: pt ?? '',
          exit_floor:    ef ?? '',
          trail_amount:  fence.trail_amount ?? '',
        });
      }
      if (!lcDirty.current) {
        setLc({
          enabled: !!r.config.loss_control.enabled,
          max_day_loss: r.config.loss_control.max_day_loss ?? '',
        });
      }
    } catch (e) { /* ignore */ }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, marketOpen ? 5000 : 30000);
    return () => clearInterval(id);
  }, [refresh, marketOpen]);

  const savePf = async () => {
    // Parse to floats once. Empty / non-numeric = null = rule disabled.
    const parse = (v) => {
      if (v === '' || v === null || v === undefined) return null;
      const n = Number(v);
      return Number.isFinite(n) ? n : null;
    };
    const pt = parse(pf.profit_target);
    const ef = parse(pf.exit_floor);
    const ta = parse(pf.trail_amount);

    // Client-side sanity (server also enforces).
    if (pt !== null && ef !== null && ef >= pt) {
      toast.error(`Exit floor (₹${ef}) must be lower than profit target (₹${pt}).`);
      return;
    }
    if (ta !== null && ta <= 0) {
      toast.error('Trail amount must be greater than 0.');
      return;
    }

    // Warn if the rules would trip immediately against current live PnL.
    if (pf.enabled && livePnl !== null && livePnl !== undefined) {
      const willFire = (
        (pt !== null && livePnl >= pt) ||
        (ef !== null && livePnl <= ef)
      );
      if (willFire) {
        const ok = window.confirm(
          `WARNING: live PnL is ₹${INR(livePnl, 0)} — this config will exit ALL ` +
          `MIS option positions immediately on the next 5-second tick.\n\nContinue?`
        );
        if (!ok) return;
      }
    }

    setSavingPf(true);
    try {
      await api.updatePnlFence({
        enabled: pf.enabled,
        profit_target: pt,
        exit_floor: ef,
        trail_amount: ta,
      });
      toast.success('P&L Fence updated');
      pfDirty.current = false;
      await refresh();
      setSavedPf(true);
      setTimeout(() => setSavedPf(false), 2500);
    } catch (e) { toast.error(e.message); }
    finally { setSavingPf(false); }
  };

  const saveLc = async () => {
    setSavingLc(true);
    try {
      await api.updateLossControl({
        enabled: lc.enabled,
        max_day_loss: Number(lc.max_day_loss) || 0,
      });
      toast.success('Loss Control updated');
      lcDirty.current = false;
      await refresh();
      setSavedLc(true);
      setTimeout(() => setSavedLc(false), 2500);
    } catch (e) { toast.error(e.message); }
    finally { setSavingLc(false); }
  };

  const resetSection = async (section) => {
    if (!window.confirm(`Reset ${section === 'pnl_fence' ? 'P&L Fence' : 'Loss Control'} trigger?`)) return;
    setResetting(true);
    try { await api.resetRiskFence(section); toast.success('Reset done'); await refresh(); }
    catch (e) { toast.error(e.message); }
    finally { setResetting(false); }
  };

  const squareNow = async () => {
    if (!window.confirm(`Run auto-squareoff NOW for all MIS option positions?`)) return;
    setSquaring(true);
    try {
      const r = await api.squareoffNow();
      const n = r?.summary?.exited?.length || 0;
      toast.success(`Squareoff complete — ${n} position(s) exited.`);
      window.dispatchEvent(new Event('positions:refresh'));
    } catch (e) { toast.error(e.message); }
    finally { setSquaring(false); }
  };

  if (!cfg) return null;
  const pfTriggered = cfg.pnl_fence.triggered;
  const lcTriggered = cfg.loss_control.triggered;
  const pnlTxt = livePnl !== null && livePnl !== undefined
    ? `${livePnl >= 0 ? '+' : ''}₹${INR(livePnl, 0)}`
    : '—';

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
      {/* P&L Fence card */}
      <div className={`bg-surface-1 border rounded-xl p-4 ${pfTriggered ? 'border-red-500/40 bg-red-500/5' : 'border-surface-3'}`}>
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <ShieldCheck className={`w-4 h-4 ${pf.enabled ? 'text-yellow-400' : 'text-gray-500'}`} />
            <span className="text-xs uppercase tracking-wider text-gray-400 font-medium">Advanced P&L Fence</span>
          </div>
          <label className="inline-flex items-center gap-1.5 text-xs">
            <input type="checkbox" checked={pf.enabled}
                   onChange={(e) => setPf({ ...pf, enabled: e.target.checked })} />
            <span className="text-gray-400">Enable</span>
          </label>
        </div>
        <p className="text-[10px] text-gray-500 mb-2">
          Smart Exit — three rules. Any rule that's filled in is armed.
          <span className="block mt-0.5">Live P&L: <span className="mono text-gray-300">{pnlTxt}</span>{(() => {
            const peak = cfg.pnl_fence.peak_pnl;
            const trail = Number(cfg.pnl_fence.trail_amount);
            if (peak !== null && peak !== undefined && Number.isFinite(trail) && trail > 0) {
              const exitAt = Number(peak) - trail;
              return (
                <span className="ml-2">
                  · Peak <span className="mono text-emerald-300">+₹{INR(Number(peak), 0)}</span>
                  {' '}· Trail exit at <span className="mono text-amber-300">₹{INR(exitAt, 0)}</span>
                </span>
              );
            }
            return null;
          })()}</span>
        </p>
        <div className="grid grid-cols-3 gap-2 mb-1">
          <input type="number" placeholder="Profit target ₹" value={pf.profit_target}
                 title="Exit when live P&L ≥ this. e.g. 2400 = book at +₹2400."
                 onChange={(e) => { pfDirty.current = true; setPf({ ...pf, profit_target: e.target.value }); }}
                 className="input-field text-xs mono py-1.5" />
          <input type="number" placeholder="Exit floor ₹ (signed)" value={pf.exit_floor}
                 title="Exit when live P&L ≤ this. Can be negative (hard SL), zero (breakeven stop), or positive (lock min profit)."
                 onChange={(e) => { pfDirty.current = true; setPf({ ...pf, exit_floor: e.target.value }); }}
                 className="input-field text-xs mono py-1.5" />
          <input type="number" min="0" placeholder="Trail by ₹" value={pf.trail_amount}
                 title="Trailing stop: exits when P&L gives back this much from its peak. Peak resets each trading day."
                 onChange={(e) => { pfDirty.current = true; setPf({ ...pf, trail_amount: e.target.value }); }}
                 className="input-field text-xs mono py-1.5" />
        </div>
        <p className="text-[9px] text-gray-600 mb-2 leading-tight">
          <b>Target</b> = ceiling · <b>Floor</b> = signed floor (e.g. <span className="mono">-1000</span> hard SL, <span className="mono">0</span> breakeven, <span className="mono">1200</span> min lock) · <b>Trail</b> = give-back from peak. Empty = rule off.
        </p>
        <div className="flex items-center justify-between gap-2">
          <button onClick={savePf} disabled={savingPf}
                  className={`px-3 py-1.5 rounded-lg text-white text-xs font-medium disabled:opacity-50 flex-1 transition-colors ${savedPf ? 'bg-green-600/80 hover:bg-green-600' : 'bg-brand-600/80 hover:bg-brand-600'}`}>
            {savingPf ? 'Saving...' : savedPf ? '✓ Saved' : 'Save'}
          </button>
          {pfTriggered ? (
            <button onClick={() => resetSection('pnl_fence')} disabled={resetting}
                    className="px-3 py-1.5 rounded-lg bg-red-500/15 hover:bg-red-500/25 text-red-300 text-xs font-medium border border-red-500/30">
              Reset Trigger
            </button>
          ) : null}
        </div>
        {pfTriggered ? (
          <div className="mt-2 text-[11px] text-red-300 bg-red-500/10 border border-red-500/20 rounded p-2">
            🚨 Triggered: {cfg.pnl_fence.trigger_reason} (PnL ₹{cfg.pnl_fence.trigger_pnl})
          </div>
        ) : null}
      </div>

      {/* Loss Control card */}
      <div className={`bg-surface-1 border rounded-xl p-4 ${lcTriggered ? 'border-red-500/40 bg-red-500/5' : 'border-surface-3'}`}>
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <ShieldAlert className={`w-4 h-4 ${lc.enabled ? 'text-red-400' : 'text-gray-500'}`} />
            <span className="text-xs uppercase tracking-wider text-gray-400 font-medium">Day-Loss Control</span>
          </div>
          <label className="inline-flex items-center gap-1.5 text-xs">
            <input type="checkbox" checked={lc.enabled}
                   onChange={(e) => setLc({ ...lc, enabled: e.target.checked })} />
            <span className="text-gray-400">Enable</span>
          </label>
        </div>
        <p className="text-[10px] text-gray-500 mb-2">
          Once day P&L ≤ <b>−limit</b>, all new manual & strategy orders are <b>blocked</b> until you disable this.
        </p>
        <input type="number" placeholder="Max day loss ₹" value={lc.max_day_loss}
               onChange={(e) => { lcDirty.current = true; setLc({ ...lc, max_day_loss: e.target.value }); }}
               className="input-field text-xs mono py-1.5 w-full mb-2" />
        <div className="flex items-center justify-between gap-2">
          <button onClick={saveLc} disabled={savingLc}
                  className={`px-3 py-1.5 rounded-lg text-white text-xs font-medium disabled:opacity-50 flex-1 transition-colors ${savedLc ? 'bg-green-600/80 hover:bg-green-600' : 'bg-brand-600/80 hover:bg-brand-600'}`}>
            {savingLc ? 'Saving...' : savedLc ? '✓ Saved' : 'Save'}
          </button>
          {lcTriggered ? (
            <button onClick={() => resetSection('loss_control')} disabled={resetting}
                    className="px-3 py-1.5 rounded-lg bg-red-500/15 hover:bg-red-500/25 text-red-300 text-xs font-medium border border-red-500/30">
              Reset Trigger
            </button>
          ) : null}
        </div>
        {lcTriggered ? (
          <div className="mt-2 text-[11px] text-red-300 bg-red-500/10 border border-red-500/20 rounded p-2">
            🚫 Trading blocked — PnL was ₹{cfg.loss_control.trigger_pnl}
          </div>
        ) : null}
      </div>

      {/* Auto-Squareoff card */}
      <div className="bg-surface-1 border border-surface-3 rounded-xl p-4">
        <div className="flex items-center gap-2 mb-2">
          <Clock className="w-4 h-4 text-blue-400" />
          <span className="text-xs uppercase tracking-wider text-gray-400 font-medium">Auto-Squareoff Fence</span>
        </div>
        <p className="text-[10px] text-gray-500 mb-2">
          Hard fence at <span className="mono text-gray-300">{autoTime}</span> IST — exits all MIS option positions
          (NFO/BFO) regardless of strategy state. Equity holdings (CNC) are never touched.
        </p>
        <button onClick={squareNow} disabled={squaring}
                className="w-full px-3 py-1.5 rounded-lg bg-blue-500/15 hover:bg-blue-500/25 text-blue-300 text-xs font-medium border border-blue-500/30 disabled:opacity-50">
          {squaring ? 'Running...' : 'Run Squareoff Now'}
        </button>
      </div>
    </div>
  );
}

export default function Dashboard() {
  const navigate = useNavigate();
  const [summary, setSummary] = useState(null);
  const [engine, setEngine] = useState(null);
  const [s1, setS1] = useState(null);
  const [s2, setS2] = useState(null);
  const [s3, setS3] = useState(null);
  const [s4, setS4] = useState(null);
  const [s5, setS5] = useState(null);
  const [s6, setS6] = useState(null);
  const [s7, setS7] = useState(null);
  const [s8, setS8] = useState(null);
  const [s9, setS9] = useState(null);
  const [s10, setS10] = useState(null);
  const [s11, setS11] = useState(null);
  const [loading, setLoading] = useState(true);
  const [engineLoading, setEngineLoading] = useState(false);
  const [time, setTime] = useState(new Date());

  const fetchData = useCallback(async () => {
    try {
      const [sm, en, st1, st2, st3, st4, st5, st6, st7, st8, st9, st10] = await Promise.all([
        api.getSummary().catch(() => null),
        api.getEngineStatus().catch(() => null),
        api.getStrategy1TradeStatus().catch(() => null),
        api.getStrategy2TradeStatus().catch(() => null),
        api.getStrategy3TradeStatus().catch(() => null),
        api.getStrategy4TradeStatus().catch(() => null),
        api.getStrategy5TradeStatus().catch(() => null),
        api.getStrategy6TradeStatus().catch(() => null),
        api.getStrategy7TradeStatus().catch(() => null),
        api.getStrategy8TradeStatus().catch(() => null),
        api.getStrategy9TradeStatus().catch(() => null),
        api.getStrategy10Status().catch(() => null),
      ]);
      if (sm) setSummary(sm);
      if (en) setEngine(en);
      if (st1) setS1(st1);
      if (st2) setS2(st2);
      if (st3) setS3(st3);
      if (st4) setS4(st4);
      if (st5) setS5(st5);
      if (st6) setS6(st6);
      if (st7) setS7(st7);
      if (st8) setS8(st8);
      if (st9) setS9(st9);
      if (st10) setS10(st10);
    } finally {
      setLoading(false);
    }
  }, []);

  // WebSocket for real-time strategy updates
  useWebSocket(useCallback((msg) => {
    if (msg.type === 'strategy_update') {
      const d = msg.data;
      if (d.s1) setS1(d.s1);
      if (d.s2) setS2(d.s2);
      if (d.s3) setS3(d.s3);
      if (d.s4) setS4(d.s4);
      if (d.s5) setS5(d.s5);
      if (d.s6) setS6(d.s6);
      if (d.s7) setS7(d.s7);
      if (d.s8) setS8(d.s8);
      if (d.s9) setS9(d.s9);
      if (d.s10) setS10(d.s10);
      if (d.s11) setS11(d.s11);
    }
  }, []));

  useEffect(() => {
    fetchData();
    // Fast summary poll keeps Day P&L / Positions / Margin in sync with
    // broker state every 5s; strategy details come over WebSocket.
    const summaryPoll = setInterval(async () => {
      try {
        const sm = await api.getSummary();
        if (sm) setSummary(sm);
      } catch { /* ignore transient failures */ }
    }, 5000);
    // Slower full refetch (30s) catches anything we missed.
    const interval = setInterval(fetchData, 30000);
    const clock = setInterval(() => setTime(new Date()), 1000);
    // Re-fetch immediately when Zerodha login completes
    const onConnected = () => fetchData();
    const onDisconnected = () => { setSummary(null); fetchData(); };
    const onPositionsRefresh = () => fetchData();
    window.addEventListener('zerodha_connected', onConnected);
    window.addEventListener('zerodha_disconnected', onDisconnected);
    window.addEventListener('positions:refresh', onPositionsRefresh);
    return () => {
      clearInterval(interval); clearInterval(summaryPoll); clearInterval(clock);
      window.removeEventListener('zerodha_connected', onConnected);
      window.removeEventListener('zerodha_disconnected', onDisconnected);
      window.removeEventListener('positions:refresh', onPositionsRefresh);
    };
  }, [fetchData]);

  // Build equity curve from trade logs
  const equityCurve = useMemo(() => {
    const allTrades = [];
    [s1, s2, s3, s4, s5, s6, s7, s8, s9, s10].forEach((s, idx) => {
      const label = ['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9', 'S10'][idx];
      (s?.trade_log || []).forEach((t) => {
        if (t.pnl !== undefined && t.pnl !== null) {
          allTrades.push({
            time: t.exit_time || t.entry_time || '',
            pnl: t.pnl || 0,
            strategy: label,
          });
        }
      });
    });
    if (allTrades.length === 0) return [];
    // Sort by time
    allTrades.sort((a, b) => (a.time > b.time ? 1 : -1));
    let cumulative = 0;
    return allTrades.map((t) => {
      cumulative += t.pnl;
      return {
        time: t.time ? t.time.split(' ').pop()?.slice(0, 5) || t.time.slice(-5) : '',
        pnl: Math.round(t.pnl),
        cumulative: Math.round(cumulative),
        strategy: t.strategy,
      };
    });
  }, [s1, s2, s3, s4, s5, s6, s7, s8, s9, s10]);

  if (loading) return <DashboardSkeleton />;

  const toggleEngine = async () => {
    setEngineLoading(true);
    try {
      if (engine?.running) {
        await api.stopEngine();
      } else {
        await api.startEngine();
      }
      await fetchData();
    } finally {
      setEngineLoading(false);
    }
  };

  const isRunning = engine?.running;
  const marketOpen = summary?.market_status === 'OPEN';

  /* Compute total strategy P&L from trade logs */
  const stratPnl = (data) => {
    if (!data) return 0;
    const log = data.trade_log || [];
    const realized = log.reduce((s, t) => s + (t.pnl || 0), 0);
    const unrealized = data.state === 'POSITION_OPEN' ? (data.trade?.unrealized_pnl || 0) : 0;
    return realized + unrealized;
  };
  const s1Pnl = stratPnl(s1);
  const s2Pnl = stratPnl(s2);
  const s3Pnl = stratPnl(s3);
  const s4Pnl = stratPnl(s4);
  const s5Pnl = stratPnl(s5);
  const s6Pnl = stratPnl(s6);
  const s7Pnl = stratPnl(s7);
  const s8Pnl = stratPnl(s8);
  const s9Pnl = stratPnl(s9);
  const s10Pnl = s10?.total_pnl ?? 0;
  const totalStratPnl = s1Pnl + s2Pnl + s3Pnl + s4Pnl + s5Pnl + s6Pnl + s7Pnl + s8Pnl + s9Pnl + s10Pnl;

  const totalTrades =
    (s1?.trade_log?.length || 0) +
    (s2?.trade_log?.length || 0) +
    (s3?.trade_log?.length || 0) +
    (s4?.trade_log?.length || 0) +
    (s5?.trade_log?.length || 0) +
    (s6?.trade_log?.length || 0) +
    (s7?.trade_log?.length || 0) +
    (s8?.trade_log?.length || 0) +
    (s9?.trade_log?.length || 0) +
    (s10?.trade_log?.length || 0);

  const openPositions = [s1, s2, s3, s4, s5, s6, s7, s8, s9].filter((s) => s?.state === 'POSITION_OPEN').length
    + (s10?.positions_open ?? 0);

  const riskBlocked = summary?.risk && !summary.risk.trading_allowed;

  return (
    <div className="p-3 sm:p-6 space-y-5 max-w-[1400px] mx-auto">
      {/* ── Header ──────────────────────────────── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold text-white tracking-tight">Dashboard</h1>
          <p className="text-sm text-gray-500 mt-0.5 flex items-center gap-2">
            <Clock className="w-3.5 h-3.5" />
            <span className="mono">{time.toLocaleTimeString('en-IN', { hour12: false })}</span>
            <span className="text-gray-700">·</span>
            {new Date().toLocaleDateString('en-IN', { weekday: 'short', day: 'numeric', month: 'short' })}
          </p>
        </div>
        <div className="flex items-center gap-2 sm:gap-2.5 flex-wrap">
          <div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium ${
            marketOpen
              ? 'border-green-500/30 bg-green-500/10 text-green-400'
              : 'border-gray-600/30 bg-surface-2 text-gray-400'
          }`}>
            <div className={`w-1.5 h-1.5 rounded-full ${marketOpen ? 'bg-green-400 animate-pulse' : 'bg-gray-500'}`} />
            {marketOpen ? 'Market Open' : 'Market Closed'}
          </div>

          <div className={`px-3 py-1.5 rounded-lg text-xs font-bold tracking-wide ${
            summary?.paper_trade
              ? 'bg-yellow-500/10 text-yellow-400 border border-yellow-500/20'
              : 'bg-green-500/10 text-green-400 border border-green-500/20'
          }`}>
            {summary?.paper_trade ? 'PAPER' : 'LIVE'}
          </div>

          <button
            onClick={toggleEngine}
            disabled={engineLoading}
            className={`flex items-center gap-2 px-4 py-1.5 rounded-lg font-semibold text-xs transition-all ${
              isRunning
                ? 'bg-red-500/10 text-red-400 border border-red-500/25 hover:bg-red-500/20'
                : 'bg-green-500/10 text-green-400 border border-green-500/25 hover:bg-green-500/20'
            } ${engineLoading ? 'opacity-60 cursor-wait' : ''}`}
          >
            {engineLoading ? (
              <RefreshCw className="w-3.5 h-3.5 animate-spin" />
            ) : isRunning ? (
              <Square className="w-3.5 h-3.5" />
            ) : (
              <Play className="w-3.5 h-3.5" />
            )}
            {isRunning ? 'Stop' : 'Start'}
          </button>
        </div>
      </div>

      {/* ── Top metrics row ─────────────────────── */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <MetricCard
          icon={Wallet}
          label="Margin"
          value={`₹${INR(summary?.account?.available)}`}
          sub={`Used: ₹${INR(summary?.account?.used)}`}
          color="brand"
        />
        <MetricCard
          icon={BarChart3}
          label="Day P&L"
          value={`${(summary?.total_pnl || 0) >= 0 ? '+' : ''}₹${INR(summary?.total_pnl, 2)}`}
          sub={`${summary?.orders_today || 0} orders placed`}
          color={(summary?.total_pnl || 0) >= 0 ? 'green' : 'red'}
          valueClass={(summary?.total_pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400'}
        />
        <MetricCard
          icon={Zap}
          label="Strategy P&L"
          value={`${totalStratPnl >= 0 ? '+' : ''}₹${INR(totalStratPnl, 0)}`}
          sub={`${totalTrades} trades today`}
          color={totalStratPnl >= 0 ? 'green' : 'red'}
          valueClass={totalStratPnl >= 0 ? 'text-green-400' : 'text-red-400'}
        />
        <MetricCard
          icon={Target}
          label="Positions"
          value={summary?.positions_count || 0}
          sub={`${openPositions} strategy open`}
          color="blue"
        />
        <ExitAllTile />
      </div>

      {/* ── Risk Fence panels ───────────────────── */}
      <RiskFencePanels marketOpen={marketOpen} />

      {/* ── Strategy cards ──────────────────────── */}
      <div>
        <div className="flex items-center gap-2 px-1 mb-3">
          <CircleDot className="w-4 h-4 text-brand-400" />
          <h3 className="text-sm font-semibold text-white">Live Strategies</h3>
          <span className="text-[10px] text-gray-600 ml-auto flex items-center gap-1">
            <Radio className="w-2.5 h-2.5 text-green-400 animate-pulse" /> Live
          </span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
          <StrategyCard
            label="Gann CV"
            shortName="Strategy 1"
            data={s1}
            onClick={() => navigate('/strategy1-trade')}
          />
          <StrategyCard
            label="Option Selling"
            shortName="Strategy 2"
            data={s2}
            onClick={() => navigate('/strategy2-trade')}
          />
          <StrategyCard
            label="CV VWAP EMA ADX"
            shortName="Strategy 3"
            data={s3}
            onClick={() => navigate('/strategy3-trade')}
          />
          <StrategyCard
            label="HL Retest"
            shortName="Strategy 4"
            data={s4}
            onClick={() => navigate('/strategy4-trade')}
          />
          <StrategyCard
            label="Gann Range"
            shortName="Strategy 5"
            data={s5}
            onClick={() => navigate('/strategy5-trade')}
          />
          <StrategyCard
            label="CALL/PUT Lines"
            shortName="Strategy 6"
            data={s6}
            onClick={() => navigate('/strategy6-trade')}
          />
          <StrategyCard
            label="Strike Lines"
            shortName="Strategy 7"
            data={s7}
            onClick={() => navigate('/strategy7-trade')}
          />
          <StrategyCard
            label="Reverse Lines"
            shortName="Strategy 8"
            data={s8}
            onClick={() => navigate('/strategy8-trade')}
          />
          <StrategyCard
            label="Line Of Control"
            shortName="Strategy 9"
            data={s9}
            onClick={() => navigate('/strategy9-trade')}
          />
          <StrategyCard
            label="Equity Intraday"
            shortName="Strategy 10"
            data={s10}
            onClick={() => navigate('/strategy10-trade')}
          />
          <StrategyCard
            label="VWAP vs Prev VWAP"
            shortName="Strategy 11"
            data={s11}
            onClick={() => navigate('/strategy11-trade')}
          />
        </div>
      </div>

      {/* ── Equity curve ──────────────────────── */}
      {equityCurve.length > 0 && (
        <div className="bg-surface-1 border border-surface-3 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-4">
            <TrendingUp className="w-4 h-4 text-brand-400" />
            <h3 className="text-sm font-semibold text-white">Equity Curve</h3>
            <span className="text-[10px] text-gray-600 ml-auto">{equityCurve.length} trades</span>
          </div>
          <div className="h-48">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={equityCurve} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="time" tick={{ fill: '#6b7280', fontSize: 10 }} />
                <YAxis tick={{ fill: '#6b7280', fontSize: 10 }} tickFormatter={(v) => `₹${v}`} />
                <Tooltip
                  contentStyle={{ background: '#111827', border: '1px solid #1f2937', borderRadius: 8,  fontSize: 12 }}
                  labelStyle={{ color: '#9ca3af' }}
                  formatter={(v, name) => [`₹${v}`, name === 'cumulative' ? 'Cumulative P&L' : 'Trade P&L']}
                />
                <ReferenceLine y={0} stroke="rgba(255,255,255,0.1)" />
                <Line type="monotone" dataKey="cumulative" stroke="#1189fc" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="pnl" stroke="#6366f1" strokeWidth={1} dot={{ r: 3, fill: '#6366f1' }} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* ── Strategy Comparison ──────────────── */}
      <div className="bg-surface-1 border border-surface-3 rounded-xl p-4">
        <div className="flex items-center gap-2 mb-4">
          <BarChart3 className="w-4 h-4 text-brand-400" />
          <h3 className="text-sm font-semibold text-white">Strategy Comparison</h3>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          {[
            { label: 'Gann CV', short: 'S1', data: s1, pnl: s1Pnl, color: 'brand' },
            { label: 'Option Sell', short: 'S2', data: s2, pnl: s2Pnl, color: 'blue' },
            { label: 'CV+VWAP', short: 'S3', data: s3, pnl: s3Pnl, color: 'brand' },
            { label: 'HL Retest', short: 'S4', data: s4, pnl: s4Pnl, color: 'brand' },
            { label: 'Gann Range', short: 'S5', data: s5, pnl: s5Pnl, color: 'brand' },
            { label: 'CALL/PUT', short: 'S6', data: s6, pnl: s6Pnl, color: 'brand' },
            { label: 'Strike Lines', short: 'S7', data: s7, pnl: s7Pnl, color: 'brand' },
            { label: 'Reverse Lines', short: 'S8', data: s8, pnl: s8Pnl, color: 'amber' },
            { label: 'Line Of Control', short: 'S9', data: s9, pnl: s9Pnl, color: 'cyan' },
            { label: 'Equity Intraday', short: 'S10', data: s10, pnl: s10Pnl, color: 'emerald' },
          ].map((s) => {
            const trades = s.data?.trade_log?.length || 0;
            const wins = (s.data?.trade_log || []).filter((t) => (t.pnl || 0) > 0).length;
            const winRate = trades > 0 ? ((wins / trades) * 100).toFixed(0) : '—';
            return (
              <div key={s.short} className="bg-surface-2 rounded-lg p-3 text-center">
                <p className="text-[10px] text-gray-500 uppercase tracking-wider">{s.label}</p>
                <p className={`text-lg font-bold mono mt-1 ${s.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {s.pnl >= 0 ? '+' : ''}₹{INR(s.pnl, 0)}
                </p>
                <div className="flex justify-center gap-3 mt-2 text-[10px] text-gray-500">
                  <span>{trades} trades</span>
                  <span>WR {winRate}%</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Bottom row: Account + Risk + System ──
          (P&L Breakdown removed — Strategy Comparison above is the
          single source of truth for per-strategy P&L) */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">

        {/* Account */}
        <div className="bg-surface-1 border border-surface-3 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <IndianRupee className="w-4 h-4 text-brand-400" />
            <h4 className="text-xs text-gray-500 uppercase tracking-wider font-medium">Account</h4>
          </div>
          <div className="space-y-2.5 text-xs">
            <div className="flex justify-between">
              <span className="text-gray-400">Available</span>
              <span className="text-white mono font-semibold">₹{INR(summary?.account?.available)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Used Margin</span>
              <span className="text-gray-300 mono">₹{INR(summary?.account?.used)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-400">Positions P&L</span>
              <span className={`mono font-medium ${(summary?.total_pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                {(summary?.total_pnl || 0) >= 0 ? '+' : ''}₹{INR(summary?.total_pnl, 2)}
              </span>
            </div>
            <div className="border-t border-surface-3 pt-2.5 flex justify-between">
              <span className="text-gray-300 font-medium">Net Value</span>
              <span className="text-white mono font-bold">
                ₹{INR((summary?.account?.available || 0) + (summary?.account?.used || 0))}
              </span>
            </div>
          </div>
        </div>

        {/* Risk Limits */}
        <div className="bg-surface-1 border border-surface-3 rounded-xl p-4 space-y-3">
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-brand-400" />
            <h4 className="text-xs text-gray-500 uppercase tracking-wider font-medium">Risk Limits</h4>
            {riskBlocked && <AlertTriangle className="w-3.5 h-3.5 text-red-400 ml-auto" />}
          </div>
          <Gauge
            label="Daily P&L"
            value={Math.round(summary?.risk?.daily_pnl || 0)}
            max={summary?.risk?.max_loss_limit || 5000}
            unit="₹"
            warn={riskBlocked}
          />
          <Gauge
            label="Trade Count"
            value={summary?.risk?.trade_count || 0}
            max={summary?.risk?.max_trades_limit || 20}
          />
        </div>

        {/* System Status */}
        <div className="bg-surface-1 border border-surface-3 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <Clock className="w-4 h-4 text-brand-400" />
            <h4 className="text-xs text-gray-500 uppercase tracking-wider font-medium">System</h4>
          </div>
          <div className="space-y-3 text-xs">
            <div className="flex justify-between items-center">
              <span className="text-gray-400">Engine</span>
              <span className={`flex items-center gap-1.5 font-semibold ${isRunning ? 'text-green-400' : 'text-gray-500'}`}>
                <div className={`w-1.5 h-1.5 rounded-full ${isRunning ? 'bg-green-400 animate-pulse' : 'bg-gray-600'}`} />
                {isRunning ? 'Running' : 'Stopped'}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-gray-400">Mode</span>
              <span className={`font-semibold ${summary?.paper_trade ? 'text-yellow-400' : 'text-green-400'}`}>
                {summary?.paper_trade ? 'Paper Trade' : 'Live Trading'}
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-gray-400">Square-off</span>
              <span className="text-gray-300 mono font-medium">15:15</span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-gray-400">Strategies</span>
              <span className="text-gray-300 font-medium">
                {[s1, s2, s3, s4, s5, s6, s7, s8, s9, s10, s11].filter((s) => s?.is_active).length} / 11 active
              </span>
            </div>
            <div className="flex justify-between items-center">
              <span className="text-gray-400">Market</span>
              <span className={`font-semibold ${marketOpen ? 'text-green-400' : 'text-gray-500'}`}>
                {marketOpen ? 'Open' : 'Closed'}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
