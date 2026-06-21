import React, { useState, useCallback } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceDot, Legend,
} from 'recharts';
import {
  Play, Loader2, AlertCircle, Activity, Info, Download,
  LineChart as LineIcon, Settings2, Table,
} from 'lucide-react';
import { api } from '../../api';

const INR = (v, d = 0) =>
  (v ?? 0).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });

const GRID = 'rgba(120,130,150,0.15)';
const AXIS = '#94a3b8';
const POS = '#34d399';
const NEG = '#f87171';

const EXIT_COLORS = {
  TARGET: POS, EXPIRY: '#60a5fa', LEG2_EXIT: '#fbbf24',
  SL: NEG, OPPOSITE_SIGNAL: '#fbbf24', TIME_EXIT: '#60a5fa',
};

function Card({ title, icon: Icon, children, right = null, className = '' }) {
  return (
    <div className={`bg-surface-2 border border-surface-3 rounded-xl p-4 ${className}`}>
      {(title || right) && (
        <div className="flex items-center justify-between mb-3 gap-2">
          <div className="flex items-center gap-2 text-gray-400 text-xs font-medium uppercase tracking-wider">
            {Icon && <Icon className="w-3.5 h-3.5" />} {title}
          </div>
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

/* Build + trigger a CSV download from an array of objects. */
function downloadCSV(filename, headers, rows) {
  const esc = (v) => {
    const s = v == null ? '' : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [headers.map((h) => esc(h.label)).join(',')];
  rows.forEach((r) => lines.push(headers.map((h) => esc(r[h.key])).join(',')));
  const blob = new Blob([lines.join('\n') + '\n'], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

const TRADE_HEADERS = [
  { key: 'date', label: 'Entry Date' },
  { key: 'entry_time', label: 'Entry Time' },
  { key: 'exit_date', label: 'Exit Date' },
  { key: 'exit_time', label: 'Exit Time' },
  { key: 'held_days', label: 'Held Days' },
  { key: 'direction', label: 'Direction' },
  { key: 'expiry_type', label: 'Expiry Type' },
  { key: 'expiry', label: 'Expiry Date' },
  { key: 'strike', label: 'Strike' },
  { key: 'symbol', label: 'Option Symbol' },
  { key: 'premium_buy', label: 'Premium Buy' },
  { key: 'target_premium', label: 'Target Premium' },
  { key: 'premium_sell', label: 'Premium Sell' },
  { key: 'qty', label: 'Qty' },
  { key: 'pnl', label: 'PnL' },
  { key: 'exit_reason', label: 'Exit Reason' },
];

function ConfigItem({ label, value }) {
  return (
    <div className="bg-surface-3/40 rounded-lg px-3 py-2">
      <div className="text-[10px] text-gray-500 uppercase tracking-wide">{label}</div>
      <div className="text-sm font-semibold text-gray-200 mt-0.5">{value}</div>
    </div>
  );
}

function VariantCard({ v, active, onSelect, onDownload }) {
  const pos = v.net_pnl >= 0;
  return (
    <div
      onClick={() => onSelect(v.key)}
      className={`cursor-pointer rounded-xl border p-4 transition ${
        active ? 'bg-brand-600/10 border-brand-500/50' : 'bg-surface-2 border-surface-3 hover:border-surface-4'
      }`}
    >
      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold text-gray-100">{v.label}</div>
        <button
          onClick={(e) => { e.stopPropagation(); onDownload(v); }}
          title="Download trades CSV"
          className="p-1 rounded bg-surface-3 hover:bg-surface-4 text-gray-300"
        >
          <Download className="w-3.5 h-3.5" />
        </button>
      </div>
      <div className={`text-2xl font-bold mt-1 ${pos ? 'text-emerald-400' : 'text-red-400'}`}>
        {pos ? '+' : ''}₹{INR(v.net_pnl)}
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 mt-3 text-xs">
        <Row k="Trades" val={v.total_trades} />
        <Row k="Win Rate" val={`${v.win_rate}%`} c={v.win_rate >= 50 ? 'text-emerald-400' : 'text-gray-300'} />
        <Row k="Wins / Losses" val={`${v.wins}/${v.losses}`} />
        <Row k="Profit Factor" val={v.profit_factor ?? '∞'} />
        <Row k="Avg Profit" val={`₹${INR(v.avg_profit)}`} c="text-emerald-400" />
        <Row k="Avg Loss" val={`₹${INR(v.avg_loss)}`} c="text-red-400" />
        <Row k="Max DD" val={`₹${INR(v.max_drawdown)}`} c="text-red-400" />
        <Row k="Expectancy" val={`₹${INR(v.expectancy)}`} c={v.expectancy >= 0 ? 'text-emerald-400' : 'text-red-400'} />
        <Row k="Capital" val={`₹${INR(v.capital_deployed)}`} />
        <Row k="Peak Capital" val={`₹${INR(v.peak_capital)}`} />
        <Row k="Return %" val={`${v.return_pct}%`} c={v.return_pct >= 0 ? 'text-emerald-400' : 'text-red-400'} />
      </div>
      {v.skipped?.length > 0 && (
        <div className="text-[11px] text-amber-400 mt-2">{v.skipped.length} signal(s) skipped (no contract data)</div>
      )}
    </div>
  );
}

function Row({ k, val, c = 'text-gray-200' }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-gray-500">{k}</span>
      <span className={`font-medium ${c}`}>{val}</span>
    </div>
  );
}

export default function VwapPvwapResearch() {
  const [days, setDays] = useState(30);
  const [runDate, setRunDate] = useState('');
  // Editable strategy config (only Lots + Target are configurable; no SL)
  const [cfgLots, setCfgLots] = useState(3);
  const [cfgTgtMode, setCfgTgtMode] = useState('points'); // points | percent | double
  const [cfgTgtPoints, setCfgTgtPoints] = useState(300);
  const [cfgTgtPercent, setCfgTgtPercent] = useState(150);
  // 2nd-leg loss control (activates after the first leg's target hits)
  const [cfgManage2, setCfgManage2] = useState(true);
  const [cfgLeg2Mode, setCfgLeg2Mode] = useState('points'); // points | percent
  const [cfgLeg2Value, setCfgLeg2Value] = useState(15);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const [active, setActive] = useState(null);

  // Signal overlay
  const [sigDate, setSigDate] = useState('');
  const [sigLoading, setSigLoading] = useState(false);
  const [signals, setSignals] = useState(null);

  // VWAP export
  const [expStart, setExpStart] = useState('');
  const [expEnd, setExpEnd] = useState('');
  const [expLoading, setExpLoading] = useState(false);

  const run = useCallback(async (date = null) => {
    setLoading(true); setError(''); setResult(null);
    try {
      const cfg = {
        lots: cfgLots, target_mode: cfgTgtMode,
        target_points: cfgTgtPoints, target_percent: cfgTgtPercent,
        manage_second_leg: cfgManage2,
        leg2_exit_mode: cfgLeg2Mode, leg2_exit_value: cfgLeg2Value,
      };
      const res = await api.researchVwapPvwapRun(days, null, date, cfg);
      if (res.status === 'ok') {
        setResult(res);
        setActive(Object.keys(res.variants)[0]);
      } else setError(res.message || 'Run failed');
    } catch (e) { setError(e.message || 'Run failed'); }
    finally { setLoading(false); }
  }, [days, cfgLots, cfgTgtMode, cfgTgtPoints, cfgTgtPercent, cfgManage2, cfgLeg2Mode, cfgLeg2Value]);

  const loadSignals = useCallback(async () => {
    setSigLoading(true);
    try {
      const res = await api.researchVwapPvwapSignals(sigDate || null);
      if (res.status === 'ok') setSignals(res);
      else setError(res.message || 'Signal load failed');
    } catch (e) { setError(e.message || 'Signal load failed'); }
    finally { setSigLoading(false); }
  }, [sigDate]);

  const downloadVariant = (v) => {
    if (!v.trades?.length) { setError(`No trades to download for ${v.label}`); return; }
    const tag = result?.params?.target_date || `${result?.params?.days_requested}d`;
    downloadCSV(`vwap_pvwap_${v.key}_${tag}.csv`, TRADE_HEADERS, v.trades);
  };

  const downloadAll = () => {
    if (!result) return;
    const all = [];
    Object.values(result.variants).forEach((v) =>
      v.trades.forEach((t) => all.push({ variant: v.label, ...t })));
    if (!all.length) { setError('No trades to download'); return; }
    const tag = result?.params?.target_date || `${result?.params?.days_requested}d`;
    downloadCSV(`vwap_pvwap_all_${tag}.csv`, [{ key: 'variant', label: 'Variant' }, ...TRADE_HEADERS], all);
  };

  const exportVwap = useCallback(async () => {
    setExpLoading(true); setError('');
    try {
      const res = await api.researchVwapPvwapExport(expStart || null, expEnd || expStart || null);
      if (res.status === 'ok') {
        if (!res.rows?.length) { setError('No VWAP rows in that range'); return; }
        downloadCSV(
          `vwap_pvwap_series_${res.from}_to_${res.to}.csv`,
          [
            { key: 'date', label: 'Date' }, { key: 'time', label: 'Time' },
            { key: 'close', label: 'NIFTY Close' }, { key: 'vwap', label: 'VWAP' },
            { key: 'prev_vwap', label: 'Prev Day VWAP' }, { key: 'crossover', label: 'Crossover' },
          ],
          res.rows,
        );
      } else setError(res.message || 'Export failed');
    } catch (e) { setError(e.message || 'Export failed'); }
    finally { setExpLoading(false); }
  }, [expStart, expEnd]);

  const variant = result && active ? result.variants[active] : null;
  const p = result?.params;

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-7xl mx-auto">
      {/* Header + run controls */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold text-gray-100">VWAP vs Previous-Day VWAP</h1>
            <span className="px-2 py-0.5 rounded-full bg-brand-600/15 text-brand-400 text-xs font-semibold border border-brand-500/20">
              Research · NIFTY Options
            </span>
          </div>
          <p className="text-gray-500 text-sm mt-0.5">
            One entry per day on the first VWAP crossover — buy CALL &amp; PUT, <strong>target-only (no SL)</strong>,
            held to target or 15:15 on the expiry day. Read-only backtest.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <div>
            <label className="block text-xs text-gray-400 mb-1">Lookback (days)</label>
            <input type="number" min="1" max="60" value={days}
              onChange={(e) => setDays(Math.max(1, Math.min(60, parseInt(e.target.value) || 1)))}
              className="w-24 bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60" />
          </div>
          <button onClick={() => run(null)} disabled={loading}
            className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50 transition">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />} Run {days}-Day
          </button>
          <div className="h-8 w-px bg-surface-3 mx-1 hidden sm:block" />
          <div>
            <label className="block text-xs text-gray-400 mb-1">Single day</label>
            <input type="date" value={runDate} onChange={(e) => setRunDate(e.target.value)}
              className="bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60" />
          </div>
          <button onClick={() => run(runDate)} disabled={loading || !runDate}
            className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-surface-3 hover:bg-surface-4 text-gray-200 border border-surface-4 font-semibold disabled:opacity-40 transition">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />} Run This Day
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-red-400 text-sm">
          <AlertCircle className="w-4 h-4 shrink-0" /> {error}
        </div>
      )}

      {/* Config — only Lots + Target are configurable (no stop-loss) */}
      <Card title="Strategy Config & Rules" icon={Settings2}>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          <div>
            <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">Lots</label>
            <input type="number" min="1" value={cfgLots}
              onChange={(e) => setCfgLots(Math.max(1, parseInt(e.target.value) || 1))}
              className="w-full bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60" />
          </div>
          <div>
            <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">Target Type</label>
            <select value={cfgTgtMode} onChange={(e) => setCfgTgtMode(e.target.value)}
              className="w-full bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60">
              <option value="points">Points</option>
              <option value="percent">Percent</option>
              <option value="double">Double premium</option>
            </select>
          </div>
          {cfgTgtMode === 'points' && (
            <div>
              <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">Target (pts)</label>
              <input type="number" min="1" value={cfgTgtPoints}
                onChange={(e) => setCfgTgtPoints(Math.max(1, parseFloat(e.target.value) || 1))}
                className="w-full bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60" />
            </div>
          )}
          {cfgTgtMode === 'percent' && (
            <div>
              <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">Target (% gain)</label>
              <input type="number" min="1" value={cfgTgtPercent}
                onChange={(e) => setCfgTgtPercent(Math.max(1, parseFloat(e.target.value) || 1))}
                className="w-full bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60" />
            </div>
          )}
          <ConfigItem label="Quantity" value={`${cfgLots} × 65 = ${cfgLots * 65}`} />
          <ConfigItem label="Stop Loss" value="None" />
          <ConfigItem label="Exit (expiry day)" value={p?.expiry_exit ?? '15:15'} />
        </div>

        {/* 2nd-leg loss control */}
        <div className="mt-3 pt-3 border-t border-surface-3">
          <div className="flex items-center gap-2 mb-2">
            <button
              onClick={() => setCfgManage2((v) => !v)}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium border transition ${
                cfgManage2 ? 'bg-amber-600/20 text-amber-400 border-amber-500/40' : 'bg-surface-3 text-gray-400 border-surface-4'
              }`}
            >
              <Settings2 className="w-3.5 h-3.5" />
              2nd-Leg Loss Control: {cfgManage2 ? 'ON' : 'OFF'}
            </button>
          </div>
          {cfgManage2 && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <div>
                <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">Exit Type</label>
                <select value={cfgLeg2Mode}
                  onChange={(e) => {
                    const m = e.target.value;
                    setCfgLeg2Mode(m);
                    // sensible default value per mode
                    setCfgLeg2Value(m === 'fraction' ? 2 : m === 'percent' ? 15 : 15);
                  }}
                  className="w-full bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60">
                  <option value="points">Points below entry</option>
                  <option value="percent">Percent below entry</option>
                  <option value="fraction">Fraction of premium</option>
                </select>
              </div>
              <div>
                {cfgLeg2Mode === 'fraction' ? (
                  <>
                    <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">Cut at</label>
                    <select value={cfgLeg2Value} onChange={(e) => setCfgLeg2Value(parseInt(e.target.value))}
                      className="w-full bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60">
                      <option value={2}>Half (÷2)</option>
                      <option value={3}>One-third (÷3)</option>
                      <option value={4}>One-quarter (÷4)</option>
                    </select>
                  </>
                ) : (
                  <>
                    <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">
                      Buffer ({cfgLeg2Mode === 'percent' ? '% of entry' : 'pts below entry'})
                    </label>
                    <input type="number" min="1" value={cfgLeg2Value}
                      onChange={(e) => setCfgLeg2Value(Math.max(1, parseFloat(e.target.value) || 1))}
                      className="w-full bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60" />
                  </>
                )}
              </div>
            </div>
          )}
          <p className="text-gray-600 text-[11px] mt-2">
            After the <strong>first</strong> leg books its target, the other leg is watched. If it breaks back
            <strong> above its entry</strong> it&apos;s held (ride the recovery). Otherwise it&apos;s cut at{' '}
            {cfgLeg2Mode === 'fraction'
              ? <strong>entry ÷ {cfgLeg2Value} ({cfgLeg2Value === 2 ? 'half' : cfgLeg2Value === 3 ? 'one-third' : 'one-quarter'} of premium)</strong>
              : <strong>entry − {cfgLeg2Value}{cfgLeg2Mode === 'percent' ? '%' : ' pts'}</strong>}.
            {cfgLeg2Mode === 'fraction'
              ? ' (a hard stop as the losing leg decays). '
              : ' (a small cut when it crawls back near entry). '}
            If neither triggers, it rides to the 15:15 expiry exit. Toggle OFF to compare against plain hold-to-expiry.
          </p>
        </div>
        <p className="text-gray-600 text-[11px] mt-2">
          <strong>Target only, no stop-loss.</strong> Target ={' '}
          {cfgTgtMode === 'double' ? '2 × entry premium'
            : cfgTgtMode === 'percent' ? `entry × (1 + ${cfgTgtPercent}%) = ${(1 + cfgTgtPercent / 100).toFixed(2)}× entry`
            : `entry + ${cfgTgtPoints} pts`}.
          Lot size fixed at <strong>65</strong> → qty = 65 × lots ({cfgLots} → {cfgLots * 65}).
          {p && <> Active run: <strong>{p.lots} lots ({p.qty} qty), target = {p.target_mode === 'double' ? '2× premium' : p.target_mode === 'percent' ? `+${p.target_percent}%` : `+${p.target_points} pts`}</strong>.</>}
        </p>
        <p className="text-gray-600 text-[11px] mt-1">
          <strong>One entry per day</strong> on that day&apos;s first crossover (09:30–15:15): buy one CALL and one
          PUT, each held until the target is hit, otherwise squared off at market at <strong>15:15 on the expiry
          day</strong>. No re-entry, no opposite-signal exit — every trading day with a crossover = 1 CE + 1 PE.
        </p>
      </Card>

      {/* Data note */}
      <div className="flex items-start gap-2 bg-blue-500/10 border border-blue-500/30 rounded-lg px-4 py-2.5 text-blue-300 text-xs">
        <Info className="w-4 h-4 shrink-0 mt-0.5" />
        <div>
          Zerodha only lists currently-tradable contracts, so options that already expired have no premium
          history and those signals are skipped (counted per variant). Coverage is richest for recent days
          &amp; the live monthly contract.
          {p && <> · VWAP basis: <strong>{result.vwap_basis}</strong>
            {p.mode === 'single_day' ? ` · Single day: ${p.target_date}` : ` · ${p.days_with_data} day(s) with data`}.</>}
        </div>
      </div>

      {loading && (
        <Card><div className="flex items-center justify-center gap-2 py-10 text-gray-400 text-sm">
          <Loader2 className="w-5 h-5 animate-spin" /> Running backtest across 4 variants…
        </div></Card>
      )}

      {!result && !loading && (
        <Card><div className="text-center py-12 text-gray-500 text-sm">
          Click <strong>Run {days}-Day</strong> for a rolling window, or pick a date and
          <strong> Run This Day</strong> for a single session (uses that day&apos;s own previous-day VWAP).
        </div></Card>
      )}

      {result && variant && (
        <>
          {/* Headline for the selected variant */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="bg-surface-2 border border-surface-3 rounded-xl px-4 py-3">
              <div className="text-[11px] text-gray-500 uppercase tracking-wide">Capital Used (peak)</div>
              <div className="text-xl font-bold text-gray-100 mt-0.5">₹{INR(variant.peak_capital)}</div>
              <div className="text-[10px] text-gray-500 mt-0.5">total deployed ₹{INR(variant.capital_deployed)}</div>
            </div>
            <div className="bg-surface-2 border border-surface-3 rounded-xl px-4 py-3">
              <div className="text-[11px] text-gray-500 uppercase tracking-wide">Net P&amp;L</div>
              <div className={`text-xl font-bold mt-0.5 ${variant.net_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {variant.net_pnl >= 0 ? '+' : ''}₹{INR(variant.net_pnl)}
              </div>
              <div className="text-[10px] text-gray-500 mt-0.5">{variant.total_trades} legs · {variant.win_rate}% win</div>
            </div>
            <div className="bg-surface-2 border border-surface-3 rounded-xl px-4 py-3">
              <div className="text-[11px] text-gray-500 uppercase tracking-wide">Return % (on peak)</div>
              <div className={`text-xl font-bold mt-0.5 ${variant.return_on_peak_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {variant.return_on_peak_pct}%
              </div>
              <div className="text-[10px] text-gray-500 mt-0.5">on deployed {variant.return_pct}%</div>
            </div>
            <div className="bg-surface-2 border border-surface-3 rounded-xl px-4 py-3">
              <div className="text-[11px] text-gray-500 uppercase tracking-wide">Selected Variant</div>
              <div className="text-base font-bold text-brand-400 mt-0.5">{variant.label}</div>
              <div className="text-[10px] text-gray-500 mt-0.5">{p?.mode === 'single_day' ? p.target_date : `${p?.days_with_data} days`}</div>
            </div>
          </div>

          {/* Variant cards — weekly/monthly × ITM/OTM, each its own calculation */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            {Object.values(result.variants).map((v) => (
              <VariantCard key={v.key} v={v} active={active === v.key}
                onSelect={setActive} onDownload={downloadVariant} />
            ))}
          </div>

          {/* Side-by-side comparison table */}
          <Card title="Variant Comparison" icon={Table}
            right={
              <button onClick={downloadAll}
                className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg bg-surface-3 hover:bg-surface-4 text-gray-200 border border-surface-4">
                <Download className="w-3.5 h-3.5" /> All trades CSV
              </button>
            }>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-surface-3 text-gray-500 text-xs">
                    {['Variant', 'Trades', 'Win Rate', 'Capital', 'Net PnL', 'Return %', 'Max DD', 'Sharpe', 'Profit Factor'].map((h) => (
                      <th key={h} className="text-left font-medium pb-2 pr-4 whitespace-nowrap">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.comparison.map((c) => (
                    <tr key={c.key}
                      className={`border-b border-surface-3/40 cursor-pointer hover:bg-surface-3/20 ${active === c.key ? 'bg-surface-3/20' : ''}`}
                      onClick={() => setActive(c.key)}>
                      <td className="py-2 pr-4 font-medium text-gray-200">{c.label}</td>
                      <td className="py-2 pr-4 text-gray-300">{c.trades}</td>
                      <td className="py-2 pr-4 text-gray-300">{c.win_rate}%</td>
                      <td className="py-2 pr-4 text-gray-300">₹{INR(c.capital_deployed)}</td>
                      <td className={`py-2 pr-4 font-semibold ${c.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {c.pnl >= 0 ? '+' : ''}₹{INR(c.pnl)}
                      </td>
                      <td className={`py-2 pr-4 font-semibold ${c.return_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {c.return_pct}%
                      </td>
                      <td className="py-2 pr-4 text-red-400">₹{INR(c.max_dd)}</td>
                      <td className="py-2 pr-4 text-gray-300">{c.sharpe}</td>
                      <td className="py-2 pr-4 text-gray-300">{c.profit_factor ?? '∞'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          {/* Trade log for the selected variant */}
          <Card title={`Trade Log — ${variant.label} (${variant.total_trades})`} icon={Activity}
            right={
              <button onClick={() => downloadVariant(variant)}
                className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg bg-surface-3 hover:bg-surface-4 text-gray-200 border border-surface-4">
                <Download className="w-3.5 h-3.5" /> Download CSV
              </button>
            }>
            {variant.trades.length === 0 ? (
              <p className="text-gray-500 text-sm py-6 text-center">
                No tradable signals for this variant in the window (contracts likely expired — see the note above).
              </p>
            ) : (
              <div className="overflow-x-auto max-h-[28rem] overflow-y-auto">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-surface-2">
                    <tr className="border-b border-surface-3 text-gray-500">
                      {['Entry', 'Exit', 'Held', 'Dir', 'Expiry', 'Strike', 'Option', 'Buy', 'Target', 'Sell', 'Qty', 'P&L', 'Reason'].map((h) => (
                        <th key={h} className="text-left font-medium pb-2 pr-3 whitespace-nowrap">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {variant.trades.map((t, i) => (
                      <tr key={i} className="border-b border-surface-3/30 hover:bg-surface-3/20">
                        <td className="py-1.5 pr-3 text-gray-300 whitespace-nowrap">
                          {t.date} <span className="text-gray-500">{t.entry_time}</span>
                        </td>
                        <td className="py-1.5 pr-3 text-gray-300 whitespace-nowrap">
                          {t.exit_date && t.exit_date !== t.date && (
                            <span className="text-amber-400">{t.exit_date} </span>
                          )}
                          <span className="text-gray-500">{t.exit_time}</span>
                        </td>
                        <td className="py-1.5 pr-3 text-gray-400 text-center">{t.held_days ?? 0}</td>
                        <td className="py-1.5 pr-3">
                          <span className={t.direction === 'CALL' ? 'text-emerald-400' : 'text-red-400'}>{t.direction}</span>
                        </td>
                        <td className="py-1.5 pr-3 text-gray-400">{t.expiry} <span className="text-gray-600">({t.expiry_type})</span></td>
                        <td className="py-1.5 pr-3 text-gray-300">{t.strike}</td>
                        <td className="py-1.5 pr-3 text-gray-400 font-mono text-[11px]">{t.symbol}</td>
                        <td className="py-1.5 pr-3 text-gray-300">₹{INR(t.premium_buy, 2)}</td>
                        <td className="py-1.5 pr-3 text-emerald-400/80">₹{INR(t.target_premium, 2)}</td>
                        <td className="py-1.5 pr-3 text-gray-300">₹{INR(t.premium_sell, 2)}</td>
                        <td className="py-1.5 pr-3 text-gray-500">{t.qty}</td>
                        <td className={`py-1.5 pr-3 font-medium ${t.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {t.pnl >= 0 ? '+' : ''}₹{INR(t.pnl)}
                        </td>
                        <td className="py-1.5 pr-3">
                          <span style={{ color: EXIT_COLORS[t.exit_reason] || '#94a3b8' }}>{t.exit_reason}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}

      {/* VWAP / PVWAP / crossover export for independent verification */}
      <Card title="Export VWAP / Prev-VWAP / Crossovers" icon={Download}>
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs text-gray-400 mb-1">From</label>
            <input type="date" value={expStart} onChange={(e) => setExpStart(e.target.value)}
              className="bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60" />
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">To (blank = same day)</label>
            <input type="date" value={expEnd} onChange={(e) => setExpEnd(e.target.value)}
              className="bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60" />
          </div>
          <button onClick={exportVwap} disabled={expLoading}
            className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50 transition">
            {expLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />} Download CSV
          </button>
          <p className="text-gray-600 text-[11px] basis-full">
            Per-minute rows: NIFTY close, running VWAP, previous-day VWAP, and a crossover flag
            (BULL/BEAR within the 09:30–15:15 window) — open in Excel to verify the signals yourself.
            Blank dates export the latest trading day.
          </p>
        </div>
      </Card>

      {/* Signal visualization (the one chart kept — shows the actual cross) */}
      <Card title="Signal Visualization (single day)" icon={LineIcon}
        right={
          <div className="flex items-end gap-2">
            <input type="date" value={sigDate} onChange={(e) => setSigDate(e.target.value)}
              className="bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-brand-500/60" />
            <button onClick={loadSignals} disabled={sigLoading}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-surface-3 hover:bg-surface-4 text-gray-200 border border-surface-4 disabled:opacity-50">
              {sigLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <LineIcon className="w-3.5 h-3.5" />} Load Day
            </button>
          </div>
        }>
        {!signals ? (
          <p className="text-gray-500 text-sm py-6 text-center">
            Pick a date (blank = latest trading day) to overlay NIFTY, running VWAP, previous-day VWAP and crossover markers.
          </p>
        ) : (
          <>
            <div className="text-xs text-gray-500 mb-2">
              {signals.date} · Prev-day VWAP <strong className="text-gray-300">{signals.prev_vwap}</strong> ·
              {' '}{signals.markers.length} crossover(s)
            </div>
            <ResponsiveContainer width="100%" height={320}>
              <LineChart data={signals.series}>
                <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
                <XAxis dataKey="t" tick={{ fill: AXIS, fontSize: 10 }} minTickGap={40} />
                <YAxis domain={['auto', 'auto']} tick={{ fill: AXIS, fontSize: 11 }} width={64} />
                <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line type="monotone" dataKey="close" stroke="#cbd5e1" strokeWidth={1.5} dot={false} name="NIFTY" />
                <Line type="monotone" dataKey="vwap" stroke="#818cf8" strokeWidth={1.5} dot={false} name="VWAP" />
                <Line type="monotone" dataKey="prev_vwap" stroke="#fbbf24" strokeWidth={1.5} strokeDasharray="5 4" dot={false} name="Prev VWAP" />
                {signals.markers.map((m, i) => (
                  <ReferenceDot key={i} x={m.t} y={m.spot} r={5}
                    fill={m.direction === 'BULL' ? POS : NEG} stroke="#0f172a" />
                ))}
              </LineChart>
            </ResponsiveContainer>
            <div className="flex items-center gap-4 mt-2 text-xs text-gray-500">
              <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full inline-block" style={{ background: POS }} /> Bullish cross</span>
              <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-full inline-block" style={{ background: NEG }} /> Bearish cross</span>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
