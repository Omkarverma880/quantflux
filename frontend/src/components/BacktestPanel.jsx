import React, { useState, useMemo } from 'react';
import {
  LineChart, Line, XAxis, YAxis, ResponsiveContainer,
  ReferenceLine, Tooltip, CartesianGrid,
} from 'recharts';
import {
  Beaker, Play, Pause, RotateCcw, ChevronRight,
  TrendingUp, TrendingDown, Award, AlertTriangle,
} from 'lucide-react';

function fmt(n, d = 2) {
  const v = Number(n);
  if (!Number.isFinite(v)) return '—';
  return v.toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
}

/**
 * Reusable backtest panel for Strategy 7 / 8 / 9.
 *
 * Props:
 *   strategy        : "S7" | "S8" | "S9"
 *   strikes         : { ce_strike, ce_token, pe_strike, pe_token }
 *   lines           : { call_line, put_line }                    (S7/S8)
 *   s9Lines         : { ce: {buy,target,sl}, pe: {buy,target,sl} } (S9)
 *   reverseTokens   : { reverse_ce_token, reverse_pe_token } (S8 only)
 *   manualReverse   : { manual_pe_strike, manual_ce_strike }   (S8 only)
 *   reverseOffset   : int (S8 only)
 *   defaultConfig   : { sl_points, target_points, lot_size, lots, max_trades }
 *   runBacktest     : async (payload) => result
 */
export default function BacktestPanel({
  strategy = 'S7',
  strikes = {},
  lines = {},
  s9Lines = null,
  reverseTokens = {},
  manualReverse = {},
  reverseOffset = 200,
  defaultConfig = {},
  runBacktest,
}) {
  const today = new Date().toISOString().slice(0, 10);
  const [date, setDate] = useState(today);
  const [sl, setSl] = useState(defaultConfig.sl_points ?? 30);
  const [tgt, setTgt] = useState(defaultConfig.target_points ?? 60);
  const [lots, setLots] = useState(defaultConfig.lots ?? 1);
  const [lot_size, setLotSize] = useState(defaultConfig.lot_size ?? 65);
  const [maxTrades, setMaxTrades] = useState(defaultConfig.max_trades_per_day ?? 3);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [err, setErr] = useState('');

  // Replay state
  const [replayIdx, setReplayIdx] = useState(0);
  const [replaying, setReplaying] = useState(false);
  const [speed, setSpeed] = useState(8); // candles per second
  const replayRef = React.useRef(null);

  const ceSeries = result?.ce_series || [];
  const peSeries = result?.pe_series || [];
  const equity = result?.equity_curve || [];
  const trades = result?.trades || [];
  const stats = result?.stats || {};

  const handleRun = async () => {
    if (!strikes.ce_token || !strikes.pe_token) {
      setErr('Pick both CE and PE strikes before backtesting.');
      return;
    }
    if (strategy === 'S9') {
      const has = (s9Lines?.ce?.buy > 0) || (s9Lines?.pe?.buy > 0);
      if (!has) { setErr('Set at least one BUY line (CE or PE) before backtesting.'); return; }
    } else if (!(lines.call_line > 0) && !(lines.put_line > 0)) {
      setErr('Set at least one CALL or PUT line before backtesting.');
      return;
    }
    setRunning(true); setErr('');
    try {
      let payload;
      if (strategy === 'S9') {
        payload = {
          trade_date: date,
          ce_token: strikes.ce_token, pe_token: strikes.pe_token,
          ce_strike: strikes.ce_strike, pe_strike: strikes.pe_strike,
          ce_buy_line:    Number(s9Lines?.ce?.buy)    || 0,
          ce_target_line: Number(s9Lines?.ce?.target) || 0,
          ce_sl_line:     Number(s9Lines?.ce?.sl)     || 0,
          pe_buy_line:    Number(s9Lines?.pe?.buy)    || 0,
          pe_target_line: Number(s9Lines?.pe?.target) || 0,
          pe_sl_line:     Number(s9Lines?.pe?.sl)     || 0,
          sl_points: Number(sl), target_points: Number(tgt),
          lot_size: Number(lot_size), lots: Number(lots),
          max_trades: Number(maxTrades),
        };
      } else {
        payload = {
          trade_date: date,
          ce_token: strikes.ce_token, pe_token: strikes.pe_token,
          ce_strike: strikes.ce_strike, pe_strike: strikes.pe_strike,
          call_line: Number(lines.call_line) || 0,
          put_line: Number(lines.put_line) || 0,
          sl_points: Number(sl), target_points: Number(tgt),
          lot_size: Number(lot_size), lots: Number(lots),
          max_trades: Number(maxTrades),
        };
      }
      if (strategy === 'S8') {
        payload.reverse_offset = Number(reverseOffset) || 200;
        payload.manual_pe_strike = Number(manualReverse.manual_pe_strike) || 0;
        payload.manual_ce_strike = Number(manualReverse.manual_ce_strike) || 0;
        payload.reverse_ce_token = Number(reverseTokens.reverse_ce_token) || 0;
        payload.reverse_pe_token = Number(reverseTokens.reverse_pe_token) || 0;
      }
      const res = await runBacktest(payload);
      if (res?.status === 'error') {
        setErr(res.message || 'Backtest failed');
      } else {
        setResult(res);
        setReplayIdx(0);
      }
    } catch (e) {
      setErr(e.message || String(e));
    } finally {
      setRunning(false);
    }
  };

  // Replay tick
  React.useEffect(() => {
    if (!replaying) return;
    const total = ceSeries.length;
    if (!total) { setReplaying(false); return; }
    replayRef.current = setInterval(() => {
      setReplayIdx((i) => {
        if (i >= total - 1) { setReplaying(false); return total - 1; }
        return i + 1;
      });
    }, Math.max(50, 1000 / Math.max(1, speed)));
    return () => clearInterval(replayRef.current);
  }, [replaying, speed, ceSeries.length]);

  const slicedCe = useMemo(
    () => (replaying || replayIdx > 0 ? ceSeries.slice(0, replayIdx + 1) : ceSeries),
    [ceSeries, replaying, replayIdx],
  );
  const slicedPe = useMemo(
    () => (replaying || replayIdx > 0 ? peSeries.slice(0, replayIdx + 1) : peSeries),
    [peSeries, replaying, replayIdx],
  );

  const tradesUpTo = useMemo(() => {
    if (!replaying && replayIdx === 0) return trades;
    const cutoff = ceSeries[replayIdx]?.t || '99:99:99';
    return trades.filter((t) => (t.entry_time || '00:00:00') <= cutoff);
  }, [trades, replaying, replayIdx, ceSeries]);

  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <Beaker className="w-4 h-4 text-violet-400" />
          <h3 className="text-sm font-semibold text-white">
            Backtest — {strategy === 'S8' ? 'Reverse Strike Lines' : strategy === 'S9' ? 'Line Of Control' : 'Strike Line Touch'}
          </h3>
          <span className="text-[10px] text-gray-500 ml-1">
            replays historical minute candles for the selected strikes
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleRun}
            disabled={running}
            className="text-xs px-3 py-1.5 rounded bg-violet-600/20 border border-violet-500/40 text-violet-300 hover:bg-violet-600/30 disabled:opacity-50"
          >
            {running ? 'Running…' : 'Run Backtest'}
          </button>
        </div>
      </div>

      {/* Inputs */}
      <div className="grid grid-cols-2 md:grid-cols-7 gap-2 text-xs">
        <label className="flex flex-col gap-0.5">
          <span className="text-gray-400">Date</span>
          <input
            type="date" value={date} onChange={(e) => setDate(e.target.value)}
            className="bg-surface-3 border border-surface-3 rounded px-2 py-1 text-white"
          />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-gray-400">SL pts</span>
          <input type="number" value={sl} onChange={(e) => setSl(e.target.value)}
            className="bg-surface-3 border border-surface-3 rounded px-2 py-1 text-white" />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-gray-400">TGT pts</span>
          <input type="number" value={tgt} onChange={(e) => setTgt(e.target.value)}
            className="bg-surface-3 border border-surface-3 rounded px-2 py-1 text-white" />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-gray-400">Lots</span>
          <input type="number" value={lots} onChange={(e) => setLots(e.target.value)}
            className="bg-surface-3 border border-surface-3 rounded px-2 py-1 text-white" />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-gray-400">Lot size</span>
          <input type="number" value={lot_size} onChange={(e) => setLotSize(e.target.value)}
            className="bg-surface-3 border border-surface-3 rounded px-2 py-1 text-white" />
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-gray-400">Max trades</span>
          <input type="number" value={maxTrades} onChange={(e) => setMaxTrades(e.target.value)}
            className="bg-surface-3 border border-surface-3 rounded px-2 py-1 text-white" />
        </label>
        <div className="flex flex-col gap-0.5 text-[11px] text-gray-500 justify-end">
          {strategy === 'S9' ? (
            <>
              <div>CE: <span className="text-emerald-400">B {fmt(s9Lines?.ce?.buy)}</span> · <span className="text-emerald-300">T {fmt(s9Lines?.ce?.target)}</span> · <span className="text-rose-400">SL {fmt(s9Lines?.ce?.sl)}</span></div>
              <div>PE: <span className="text-rose-400">B {fmt(s9Lines?.pe?.buy)}</span> · <span className="text-emerald-300">T {fmt(s9Lines?.pe?.target)}</span> · <span className="text-rose-400">SL {fmt(s9Lines?.pe?.sl)}</span></div>
            </>
          ) : (
            <>
              <div>CALL line: <span className="text-emerald-400">{fmt(lines.call_line)}</span></div>
              <div>PUT line: <span className="text-rose-400">{fmt(lines.put_line)}</span></div>
            </>
          )}
        </div>
      </div>

      {err && (
        <div className="text-xs text-rose-400 flex items-center gap-1">
          <AlertTriangle className="w-3.5 h-3.5" /> {err}
        </div>
      )}

      {result && (
        <>
          {/* Stats strip */}
          <div className="grid grid-cols-2 md:grid-cols-6 gap-2 text-xs">
            {[
              ['Trades', stats.total_trades, ''],
              ['Win rate', `${stats.win_rate ?? 0}%`, stats.win_rate >= 50 ? 'text-emerald-400' : 'text-rose-400'],
              ['Net P&L', `₹ ${fmt(stats.total_pnl, 2)}`, (stats.total_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'],
              ['Best', `₹ ${fmt(stats.best, 2)}`, 'text-emerald-400'],
              ['Worst', `₹ ${fmt(stats.worst, 2)}`, 'text-rose-400'],
              ['Max DD', `₹ ${fmt(stats.max_drawdown, 2)}`, 'text-amber-400'],
            ].map(([label, val, cls]) => (
              <div key={label} className="bg-surface-3 rounded px-2 py-1.5">
                <div className="text-[10px] text-gray-500 uppercase tracking-wider">{label}</div>
                <div className={`font-semibold text-white ${cls || ''}`}>{val ?? '—'}</div>
              </div>
            ))}
          </div>

          {/* Replay controls */}
          {ceSeries.length > 0 && (
            <div className="flex items-center gap-2 text-xs">
              <button
                onClick={() => setReplaying((r) => !r)}
                className="px-2 py-1 rounded border border-surface-3 text-gray-300 hover:text-white"
              >
                {replaying ? <Pause className="w-3 h-3 inline mr-1" /> : <Play className="w-3 h-3 inline mr-1" />}
                {replaying ? 'Pause' : 'Replay'}
              </button>
              <button
                onClick={() => { setReplayIdx(0); setReplaying(false); }}
                className="px-2 py-1 rounded border border-surface-3 text-gray-300 hover:text-white"
              >
                <RotateCcw className="w-3 h-3 inline mr-1" /> Reset
              </button>
              <input
                type="range" min={0} max={Math.max(0, ceSeries.length - 1)}
                value={replayIdx} onChange={(e) => setReplayIdx(Number(e.target.value))}
                className="flex-1"
              />
              <select
                value={speed} onChange={(e) => setSpeed(Number(e.target.value))}
                className="bg-surface-3 border border-surface-3 rounded px-1.5 py-1 text-white"
              >
                {[2, 5, 8, 15, 30, 60].map((s) => (
                  <option key={s} value={s}>{s}x</option>
                ))}
              </select>
              <span className="text-gray-400 w-20 text-right">
                {ceSeries[replayIdx]?.t || ceSeries[ceSeries.length - 1]?.t || '—'}
              </span>
            </div>
          )}

          {/* Charts */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <ReplayChart
              title="CE replay" data={slicedCe} line={lines.call_line}
              lineColor="#22c55e" trades={tradesUpTo.filter((t) => t.trigger_side === 'CALL')}
            />
            <ReplayChart
              title="PE replay" data={slicedPe} line={lines.put_line}
              lineColor="#ef4444" trades={tradesUpTo.filter((t) => t.trigger_side === 'PUT')}
            />
          </div>

          {/* Equity curve */}
          {equity.length > 0 && (
            <div className="h-40 bg-surface-3 rounded p-2">
              <div className="text-[10px] text-gray-500 uppercase mb-1">Cumulative P&L</div>
              <ResponsiveContainer width="100%" height="90%">
                <LineChart data={equity} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
                  <XAxis dataKey="t" hide />
                  <YAxis tick={{ fontSize: 10, fill: '#9ca3af' }} width={50} />
                  <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #1f2937', fontSize: 11 }} />
                  <ReferenceLine y={0} stroke="#475569" strokeDasharray="2 2" />
                  <Line type="monotone" dataKey="y" stroke="#a78bfa" strokeWidth={1.6} dot={false} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Trades table */}
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-gray-500 border-b border-surface-3">
                  <th className="py-1">#</th>
                  <th>Trigger</th>
                  <th>Side</th>
                  <th>Strike</th>
                  <th>Entry @</th>
                  <th>Exit @</th>
                  <th>Type</th>
                  <th>Time</th>
                  <th className="text-right">P&L</th>
                </tr>
              </thead>
              <tbody>
                {tradesUpTo.map((t, i) => (
                  <tr key={i} className="border-b border-surface-3/50">
                    <td className="py-1 text-gray-500">{i + 1}</td>
                    <td className={t.trigger_side === 'CALL' ? 'text-emerald-400' : 'text-rose-400'}>{t.trigger_side}</td>
                    <td className={t.side === 'CE' ? 'text-emerald-400' : 'text-rose-400'}>{t.side}</td>
                    <td className="text-white">{t.strike}</td>
                    <td>{fmt(t.entry_price)} <span className="text-gray-500">({t.entry_time})</span></td>
                    <td>{fmt(t.exit_price)} <span className="text-gray-500">({t.exit_time})</span></td>
                    <td className="text-gray-300">{t.exit_type}</td>
                    <td className="text-gray-500">SL {fmt(t.sl)} / TGT {fmt(t.tgt)}</td>
                    <td className={`text-right font-semibold ${t.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                      ₹ {fmt(t.pnl, 2)}
                    </td>
                  </tr>
                ))}
                {tradesUpTo.length === 0 && (
                  <tr><td colSpan={9} className="py-2 text-center text-gray-500">No trades in this window</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}

function ReplayChart({ title, data, line, lineColor, trades }) {
  const series = (data || []).map((c, i) => ({ x: i, t: c.t, y: c.c, h: c.h, l: c.l }));
  const ys = series.map((s) => s.y).filter((v) => v > 0);
  if (line > 0) ys.push(line);
  const lo = ys.length ? Math.min(...ys) * 0.95 : 0;
  const hi = ys.length ? Math.max(...ys) * 1.05 : 1;
  return (
    <div className="bg-surface-3 rounded p-2 h-56">
      <div className="text-[10px] text-gray-500 uppercase mb-1">{title}</div>
      <ResponsiveContainer width="100%" height="90%">
        <LineChart data={series} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis dataKey="t" hide />
          <YAxis domain={[lo, hi]} tick={{ fontSize: 10, fill: '#9ca3af' }} width={50} />
          <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #1f2937', fontSize: 11 }} />
          <Line type="monotone" dataKey="y" stroke={lineColor} strokeWidth={1.4} dot={false} isAnimationActive={false} />
          {line > 0 && (
            <ReferenceLine y={line} stroke={lineColor} strokeDasharray="6 4" strokeWidth={1.2} />
          )}
          {(trades || []).map((t, i) => (
            <ReferenceLine
              key={i} y={t.entry_price}
              stroke="#60a5fa" strokeDasharray="2 2" strokeWidth={1}
              label={{ value: '★', position: 'left', fill: '#60a5fa', fontSize: 12 }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
