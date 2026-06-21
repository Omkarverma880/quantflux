import React, { useState, useCallback } from 'react';
import {
  LineChart, Line, BarChart, Bar, Cell, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine, ReferenceDot, Legend,
} from 'recharts';
import {
  FlaskConical, Play, Loader2, AlertCircle, TrendingUp, TrendingDown,
  Activity, BarChart3, LineChart as LineIcon, Info,
} from 'lucide-react';
import { api } from '../../api';

const INR = (v, d = 0) =>
  (v ?? 0).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });

const GRID = 'rgba(120,130,150,0.15)';
const AXIS = '#94a3b8';
const POS = '#34d399';
const NEG = '#f87171';

function Card({ title, icon: Icon, children, right = null, className = '' }) {
  return (
    <div className={`bg-surface-2 border border-surface-3 rounded-xl p-4 ${className}`}>
      {(title || right) && (
        <div className="flex items-center justify-between mb-3">
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

function Stat({ label, value, color = 'text-gray-100' }) {
  return (
    <div className="bg-surface-3/40 rounded-lg px-3 py-3 text-center">
      <div className={`text-lg font-bold ${color}`}>{value}</div>
      <div className="text-gray-500 text-[11px] mt-0.5">{label}</div>
    </div>
  );
}

const EXIT_COLORS = {
  TARGET: POS, SL: NEG, OPPOSITE_SIGNAL: '#fbbf24', TIME_EXIT: '#60a5fa',
};

export default function VwapPvwapResearch() {
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const [active, setActive] = useState(null); // active variant key

  // Signal overlay
  const [sigDate, setSigDate] = useState('');
  const [sigLoading, setSigLoading] = useState(false);
  const [signals, setSignals] = useState(null);

  const run = useCallback(async () => {
    setLoading(true); setError(''); setResult(null);
    try {
      const res = await api.researchVwapPvwapRun(days);
      if (res.status === 'ok') {
        setResult(res);
        const first = Object.keys(res.variants)[0];
        setActive(first);
      } else setError(res.message || 'Run failed');
    } catch (e) { setError(e.message || 'Run failed'); }
    finally { setLoading(false); }
  }, [days]);

  const loadSignals = useCallback(async () => {
    setSigLoading(true);
    try {
      const res = await api.researchVwapPvwapSignals(sigDate || null);
      if (res.status === 'ok') setSignals(res);
      else setError(res.message || 'Signal load failed');
    } catch (e) { setError(e.message || 'Signal load failed'); }
    finally { setSigLoading(false); }
  }, [sigDate]);

  const variant = result && active ? result.variants[active] : null;

  // Derived chart data
  const equityData = variant ? variant.equity_curve.map((v, i) => ({ i: i + 1, v })) : [];
  const ddData = variant ? variant.drawdown_curve.map((v, i) => ({ i: i + 1, v })) : [];
  const dailyData = variant ? variant.daily_pnl : [];
  const exitDist = React.useMemo(() => {
    if (!variant) return [];
    const counts = {};
    variant.trades.forEach((t) => { counts[t.exit_reason] = (counts[t.exit_reason] || 0) + 1; });
    return Object.entries(counts).map(([reason, count]) => ({ reason, count }));
  }, [variant]);

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold text-gray-100">VWAP vs Previous-Day VWAP</h1>
            <span className="px-2 py-0.5 rounded-full bg-brand-600/15 text-brand-400 text-xs font-semibold border border-brand-500/20">
              Research · NIFTY Options
            </span>
          </div>
          <p className="text-gray-500 text-sm mt-0.5">
            At each VWAP × previous-day-VWAP crossover, buy both CALL &amp; PUT. SL 100 / Target 300
            premium pts · {3} lots (×65) · max 3 trades/day · square-off 15:20. Read-only backtest.
          </p>
        </div>
        <div className="flex items-end gap-2">
          <div>
            <label className="block text-xs text-gray-400 mb-1">Lookback (days)</label>
            <input type="number" min="1" max="60" value={days}
              onChange={(e) => setDays(Math.max(1, Math.min(60, parseInt(e.target.value) || 1)))}
              className="w-24 bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60" />
          </div>
          <button onClick={run} disabled={loading}
            className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50 transition">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />} Run Backtest
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-red-400 text-sm">
          <AlertCircle className="w-4 h-4 shrink-0" /> {error}
        </div>
      )}

      {/* Data-availability note */}
      <div className="flex items-start gap-2 bg-blue-500/10 border border-blue-500/30 rounded-lg px-4 py-2.5 text-blue-300 text-xs">
        <Info className="w-4 h-4 shrink-0 mt-0.5" />
        <div>
          Zerodha only lists currently-tradable contracts, so options that already expired in the
          window have no premium history and those signals are skipped (see “skipped” counts).
          Coverage is richest for recent days &amp; the live monthly contract.
          {result && <> VWAP basis: <strong>{result.vwap_basis}</strong>.</>}
        </div>
      </div>

      {loading && (
        <Card><div className="flex items-center justify-center gap-2 py-10 text-gray-400 text-sm">
          <Loader2 className="w-5 h-5 animate-spin" /> Running backtest across 4 variants…
        </div></Card>
      )}

      {!result && !loading && (
        <Card><div className="text-center py-12 text-gray-500 text-sm">
          Set a lookback window and click <strong>Run Backtest</strong> to evaluate all four variants.
        </div></Card>
      )}

      {result && variant && (
        <>
          {/* Variant tabs */}
          <div className="flex flex-wrap gap-2">
            {Object.values(result.variants).map((v) => (
              <button key={v.key} onClick={() => setActive(v.key)}
                className={`px-3 py-1.5 rounded-lg text-sm font-medium border transition ${
                  active === v.key
                    ? 'bg-brand-600/20 text-brand-400 border-brand-500/40'
                    : 'bg-surface-3 text-gray-400 border-surface-4 hover:text-gray-200'
                }`}>
                {v.label}
                <span className={`ml-2 text-xs ${v.net_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {v.net_pnl >= 0 ? '+' : ''}₹{INR(v.net_pnl)}
                </span>
              </button>
            ))}
          </div>

          {/* Section 1: Summary cards */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
            <Stat label="Total Trades" value={variant.total_trades} />
            <Stat label="Winning" value={variant.wins} color="text-emerald-400" />
            <Stat label="Losing" value={variant.losses} color="text-red-400" />
            <Stat label="Win Rate" value={`${variant.win_rate}%`}
              color={variant.win_rate >= 50 ? 'text-emerald-400' : 'text-gray-200'} />
            <Stat label="Net P&L" value={`₹${INR(variant.net_pnl)}`}
              color={variant.net_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'} />
            <Stat label="Avg Profit" value={`₹${INR(variant.avg_profit)}`} color="text-emerald-400" />
            <Stat label="Avg Loss" value={`₹${INR(variant.avg_loss)}`} color="text-red-400" />
            <Stat label="Max Drawdown" value={`₹${INR(variant.max_drawdown)}`} color="text-red-400" />
            <Stat label="Profit Factor" value={variant.profit_factor ?? '∞'} />
            <Stat label="Expectancy" value={`₹${INR(variant.expectancy)}`}
              color={variant.expectancy >= 0 ? 'text-emerald-400' : 'text-red-400'} />
          </div>

          {/* Section 2: Variant comparison table */}
          <Card title="Variant Comparison" icon={BarChart3}>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-surface-3 text-gray-500 text-xs">
                    {['Variant', 'Trades', 'Win Rate', 'PnL', 'Max DD', 'Sharpe', 'Profit Factor'].map((h) => (
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
                      <td className={`py-2 pr-4 font-semibold ${c.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {c.pnl >= 0 ? '+' : ''}₹{INR(c.pnl)}
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

          {/* Charts row 1 */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card title="Equity Curve" icon={LineIcon}>
              <ResponsiveContainer width="100%" height={220}>
                <LineChart data={equityData}>
                  <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
                  <XAxis dataKey="i" tick={{ fill: AXIS, fontSize: 11 }} />
                  <YAxis tick={{ fill: AXIS, fontSize: 11 }} width={56} />
                  <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }} />
                  <ReferenceLine y={0} stroke={AXIS} strokeOpacity={0.4} />
                  <Line type="monotone" dataKey="v" stroke="#818cf8" strokeWidth={2} dot={false} name="Equity ₹" />
                </LineChart>
              </ResponsiveContainer>
            </Card>
            <Card title="Drawdown" icon={TrendingDown}>
              <ResponsiveContainer width="100%" height={220}>
                <LineChart data={ddData}>
                  <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
                  <XAxis dataKey="i" tick={{ fill: AXIS, fontSize: 11 }} />
                  <YAxis tick={{ fill: AXIS, fontSize: 11 }} width={56} />
                  <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }} />
                  <Line type="monotone" dataKey="v" stroke={NEG} strokeWidth={2} dot={false} name="Drawdown ₹" />
                </LineChart>
              </ResponsiveContainer>
            </Card>
          </div>

          {/* Charts row 2 */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card title="Daily P&L" icon={BarChart3}>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={dailyData}>
                  <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
                  <XAxis dataKey="date" tick={{ fill: AXIS, fontSize: 10 }} />
                  <YAxis tick={{ fill: AXIS, fontSize: 11 }} width={56} />
                  <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }} />
                  <ReferenceLine y={0} stroke={AXIS} strokeOpacity={0.4} />
                  <Bar dataKey="pnl" name="Day P&L ₹">
                    {dailyData.map((d, i) => <Cell key={i} fill={d.pnl >= 0 ? POS : NEG} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </Card>
            <Card title="Exit-Reason Distribution" icon={Activity}>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={exitDist}>
                  <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
                  <XAxis dataKey="reason" tick={{ fill: AXIS, fontSize: 10 }} />
                  <YAxis tick={{ fill: AXIS, fontSize: 11 }} width={40} allowDecimals={false} />
                  <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }} />
                  <Bar dataKey="count" name="Trades">
                    {exitDist.map((d, i) => <Cell key={i} fill={EXIT_COLORS[d.reason] || '#94a3b8'} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </Card>
          </div>

          {/* Variant comparison chart */}
          <Card title="Net P&L by Variant" icon={BarChart3}>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={result.comparison}>
                <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
                <XAxis dataKey="label" tick={{ fill: AXIS, fontSize: 11 }} />
                <YAxis tick={{ fill: AXIS, fontSize: 11 }} width={64} />
                <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }} />
                <ReferenceLine y={0} stroke={AXIS} strokeOpacity={0.4} />
                <Bar dataKey="pnl" name="Net P&L ₹">
                  {result.comparison.map((d, i) => <Cell key={i} fill={d.pnl >= 0 ? POS : NEG} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </Card>

          {/* Section 3: Trade log */}
          <Card title={`Trade Log — ${variant.label} (${variant.total_trades})`} icon={Activity}
            right={variant.skipped?.length > 0 && (
              <span className="text-xs text-amber-400">{variant.skipped.length} signals skipped (no data)</span>
            )}>
            {variant.trades.length === 0 ? (
              <p className="text-gray-500 text-sm py-6 text-center">
                No tradable signals for this variant in the window (likely all contracts expired —
                see the data-availability note).
              </p>
            ) : (
              <div className="overflow-x-auto max-h-96 overflow-y-auto">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-surface-2">
                    <tr className="border-b border-surface-3 text-gray-500">
                      {['Date', 'Entry', 'Exit', 'Direction', 'Expiry', 'Strike', 'Buy', 'Sell', 'P&L', 'Reason'].map((h) => (
                        <th key={h} className="text-left font-medium pb-2 pr-3 whitespace-nowrap">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {variant.trades.map((t, i) => (
                      <tr key={i} className="border-b border-surface-3/30 hover:bg-surface-3/20">
                        <td className="py-1.5 pr-3 text-gray-400">{t.date}</td>
                        <td className="py-1.5 pr-3 text-gray-300">{t.entry_time}</td>
                        <td className="py-1.5 pr-3 text-gray-300">{t.exit_time}</td>
                        <td className="py-1.5 pr-3">
                          <span className={t.direction === 'CALL' ? 'text-emerald-400' : 'text-red-400'}>
                            {t.direction}
                          </span>
                        </td>
                        <td className="py-1.5 pr-3 text-gray-400">{t.expiry_type}</td>
                        <td className="py-1.5 pr-3 text-gray-300">{t.strike}</td>
                        <td className="py-1.5 pr-3 text-gray-300">₹{INR(t.premium_buy, 2)}</td>
                        <td className="py-1.5 pr-3 text-gray-300">₹{INR(t.premium_sell, 2)}</td>
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

      {/* Signal visualization (independent of run) */}
      <Card title="Signal Visualization (single day)" icon={LineIcon}
        right={
          <div className="flex items-end gap-2">
            <input type="date" value={sigDate} onChange={(e) => setSigDate(e.target.value)}
              className="bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-brand-500/60" />
            <button onClick={loadSignals} disabled={sigLoading}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-surface-3 hover:bg-surface-4 text-gray-200 border border-surface-4 disabled:opacity-50">
              {sigLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <LineIcon className="w-3.5 h-3.5" />}
              Load Day
            </button>
          </div>
        }>
        {!signals ? (
          <p className="text-gray-500 text-sm py-6 text-center">
            Pick a date (blank = latest trading day) to overlay NIFTY, running VWAP, previous-day VWAP, and crossover markers.
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
