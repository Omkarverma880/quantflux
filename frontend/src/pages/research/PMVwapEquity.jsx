import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Loader2, AlertCircle, Save, TrendingUp, Download, Play, Info, Radio, BarChart3, FlaskConical,
} from 'lucide-react';
import { api } from '../../api';
import PMVwapReport from '../../components/PMVwapReport';

const selCls =
  'bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';
const lbl = 'block text-[10px] text-gray-500 uppercase tracking-wide mb-1';
const TIMEFRAMES = [['5m', '5 Min'], ['15m', '15 Min'], ['30m', '30 Min'], ['1h', '1 Hour'], ['1d', '1 Day']];

const INT = (v) => (v == null ? '—' : Number(v).toLocaleString('en-IN'));
const NUM = (v, d = 2) => (v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));

const COLS = [
  ['date', 'Date'], ['time', 'Time'], ['underlying', 'Underlying'], ['entry_price', 'Entry'],
  ['prev_month_vwap', 'Prev-M VWAP'], ['prev_week_vwap', 'Prev-W VWAP'], ['direction', 'Dir'],
  ['qty', 'Qty'], ['capital', 'Capital'], ['target_price', 'Target'], ['stop_price', 'Stop'],
  ['exit_price', 'Exit'], ['exit_date', 'Exit Date'], ['exit_reason', 'Reason'], ['hold_days', 'Hold(d)'],
  ['return_pct', 'Return %'], ['mtm', 'MTM'], ['status', 'Status'], ['notes', 'Notes'],
];

function downloadCSV(filename, rows) {
  const esc = (v) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const lines = [COLS.map(([, l]) => esc(l)).join(',')];
  rows.forEach((r) => lines.push(COLS.map(([k]) => esc(r[k])).join(',')));
  const blob = new Blob([lines.join('\n') + '\n'], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
}

function Stat({ label, value, tone }) {
  const c = tone === 'up' ? 'text-emerald-400' : tone === 'down' ? 'text-red-400' : 'text-gray-100';
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl px-3 py-2">
      <div className="text-[10px] text-gray-500 uppercase tracking-wide">{label}</div>
      <div className={`text-lg font-bold ${c}`}>{value}</div>
    </div>
  );
}

export default function PMVwapEquity() {
  const [cfg, setCfg] = useState(null);
  const [mode, setMode] = useState('single');
  const [symbol, setSymbol] = useState('');
  const [universe, setUniverse] = useState([]);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [data, setData] = useState(null);
  const [dateFilter, setDateFilter] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [live, setLive] = useState(false);
  const [tab, setTab] = useState('backtest');
  const [error, setError] = useState('');
  const [savedMsg, setSavedMsg] = useState('');
  const timer = useRef(null);

  const showErr = (m) => { setError(m); setTimeout(() => setError(''), 6000); };
  const patch = (k, v) => setCfg((c) => ({ ...c, [k]: v }));

  useEffect(() => {
    api.researchPMVwapEquityConfig().then((r) => { if (r.status === 'ok') setCfg(r.config); }).catch(() => setCfg({}));
    api.researchPMVwapEquityUniverse()
      .then((r) => { if (r.status === 'ok') { setUniverse(r.stocks || []); if (r.stocks?.[0]) setSymbol(r.stocks[0].name); } })
      .catch(() => {});
  }, []);

  const runBacktest = useCallback(async () => {
    if (!cfg) return;
    if (mode === 'single' && !symbol) { showErr('Pick a stock'); return; }
    setLoading(true); setData(null);
    try {
      const res = await api.researchPMVwapEquityBacktest({
        overrides: cfg, symbol: mode === 'single' ? symbol : null,
        start: start || null, end: end || start || null, persist: true,
      });
      if (res.status === 'ok') setData(res); else showErr(res.message || 'Backtest failed');
    } catch (e) { showErr(e.message || 'Backtest failed'); }
    finally { setLoading(false); }
  }, [cfg, mode, symbol, start, end]);

  const liveScan = useCallback(async () => {
    if (!cfg) return;
    try {
      const res = await api.researchPMVwapEquityScan({ overrides: cfg, symbol: mode === 'single' ? symbol : null });
      if (res.status === 'ok') setData(res);
    } catch { /* keep prior data on transient errors */ }
  }, [cfg, mode, symbol]);

  useEffect(() => {
    if (timer.current) clearInterval(timer.current);
    if (live) { liveScan(); timer.current = setInterval(liveScan, Math.max(10, cfg?.scan_interval || 60) * 1000); }
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [live, liveScan, cfg?.scan_interval]);

  const saveDefaults = async () => {
    setSaving(true);
    try {
      const r = await api.researchPMVwapEquityConfigSave(cfg);
      if (r.status === 'ok') { setCfg(r.config); setSavedMsg('Saved'); setTimeout(() => setSavedMsg(''), 2500); }
    } catch (e) { showErr(e.message || 'Save failed'); } finally { setSaving(false); }
  };

  const rows = data?.rows || [];
  const shownRows = dateFilter ? rows.filter((r) => r.date === dateFilter) : rows;
  const dates = [...new Set(rows.map((r) => r.date))];
  const s = data?.stats;
  if (!cfg) return <div className="p-6 text-gray-500 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>;
  const num = (k, min, step = 1) => (
    <input type="number" min={min} step={step} value={cfg[k] ?? ''} onChange={(e) => patch(k, e.target.value === '' ? 0 : parseFloat(e.target.value))} className={`w-full ${selCls}`} />
  );

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-[1500px] mx-auto">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <TrendingUp className="w-5 h-5 text-brand-400" />
            <h1 className="text-xl font-bold text-gray-100 tracking-wide">Previous Month VWAP Equity Holding</h1>
            <span className="px-2 py-0.5 rounded-full bg-brand-600/15 text-brand-400 text-xs font-semibold border border-brand-500/20">Research Only</span>
          </div>
          <p className="text-gray-500 text-sm mt-0.5">
            Buy equity as a holding when price meets Prev-Month VWAP (purple) while Prev-Week VWAP (green) is above it. No orders placed.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setLive((v) => !v)}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border transition ${
              live ? 'bg-emerald-600/20 text-emerald-400 border-emerald-500/40' : 'bg-surface-3 text-gray-400 border-surface-4'}`}>
            {live ? <Radio className="w-3.5 h-3.5 animate-pulse" /> : <Play className="w-3.5 h-3.5" />} Live {live ? 'ON' : 'OFF'}
          </button>
          <button onClick={saveDefaults} disabled={saving}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-50 transition">
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />} {savedMsg || 'Save as default'}
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-red-400 text-sm">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 border-b border-surface-3">
        {[['backtest', 'Backtest', FlaskConical], ['report', 'Summary Report', BarChart3]].map(([id, label, Icon]) => (
          <button key={id} onClick={() => setTab(id)}
            className={`flex items-center gap-1.5 px-4 py-2 text-sm font-semibold border-b-2 -mb-px transition ${
              tab === id ? 'border-brand-500 text-brand-400' : 'border-transparent text-gray-400 hover:text-gray-200'}`}>
            <Icon className="w-4 h-4" /> {label}
          </button>
        ))}
      </div>

      {tab === 'report' ? <PMVwapReport kind="equity" /> : (<>
      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex rounded-lg overflow-hidden border border-surface-4">
            {['single', 'multi'].map((m) => (
              <button key={m} onClick={() => setMode(m)}
                className={`px-3 py-1.5 text-xs font-semibold ${mode === m ? 'bg-brand-600 text-white' : 'bg-surface-3 text-gray-400'}`}>
                {m === 'single' ? 'Single Stock' : `All F&O (${universe.length})`}
              </button>
            ))}
          </div>
          {mode === 'single' && (
            <select value={symbol} onChange={(e) => setSymbol(e.target.value)} className={selCls}>
              {universe.map((u) => <option key={u.name} value={u.name}>{u.name}</option>)}
            </select>
          )}
          <div><label className={lbl}>From</label><input type="date" value={start} onChange={(e) => setStart(e.target.value)} className={selCls} /></div>
          <div><label className={lbl}>To</label><input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className={selCls} /></div>
          <button onClick={runBacktest} disabled={loading || live}
            className="ml-auto flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50 transition">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />} Run Backtest
          </button>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 pt-2 border-t border-surface-3">
          <div><label className={lbl}>Timeframe</label>
            <select value={cfg.timeframe} onChange={(e) => patch('timeframe', e.target.value)} className={`w-full ${selCls}`}>
              {TIMEFRAMES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </div>
          <div><label className={lbl}>Entry Mode</label>
            <select value={cfg.entry_mode} onChange={(e) => patch('entry_mode', e.target.value)} className={`w-full ${selCls}`}>
              <option value="cross_up">Cross-up</option><option value="touch">Touch</option>
            </select>
          </div>
          <div><label className={lbl}>VWAP Buffer</label>{num('vwap_buffer', 0, 0.05)}</div>
          <div><label className={lbl}>Target %</label>{num('target_pct', 0, 0.5)}</div>
          <div><label className={lbl}>Stop % (0=off)</label>{num('stop_pct', 0, 0.5)}</div>
          <div><label className={lbl}>Max Hold Days</label>{num('max_hold_days', 1)}</div>
          <div><label className={lbl}>Exit On</label>
            <select value={cfg.exit_on} onChange={(e) => patch('exit_on', e.target.value)} className={`w-full ${selCls}`}>
              <option value="close">Close</option><option value="high_low">High/Low</option>
            </select>
          </div>
          <div><label className={lbl}>Capital / Trade</label>{num('capital_per_trade', 0, 1000)}</div>
          <div><label className={lbl}>Fixed Qty (0=cap)</label>{num('fixed_qty', 0)}</div>
          <div><label className={lbl}>History Days</label>{num('history_days', 35)}</div>
          <div><label className={lbl}>Min Price</label>{num('min_price', 0)}</div>
          <div><label className={lbl}>Max Stocks (0=all)</label>{num('max_stocks', 0)}</div>
          <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer">
            <input type="checkbox" checked={cfg.require_pw_above_pm} onChange={(e) => patch('require_pw_above_pm', e.target.checked)} className="accent-brand-500" /> Green above purple
          </label>
          <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer">
            <input type="checkbox" checked={cfg.one_signal_per_day} onChange={(e) => patch('one_signal_per_day', e.target.checked)} className="accent-brand-500" /> One signal/day
          </label>
        </div>
      </div>

      {s && (
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-5 gap-2">
          <Stat label="Signals" value={INT(s.total_signals)} />
          <Stat label="Win %" value={`${s.win_rate}%`} tone={s.win_rate >= 50 ? 'up' : 'down'} />
          <Stat label="Capital Used" value={`₹${NUM(s.total_capital, 0)}`} />
          <Stat label="Total MTM" value={NUM(s.total_mtm)} tone={s.total_mtm >= 0 ? 'up' : 'down'} />
          <Stat label="ROI on Capital" value={`${s.roi_pct}%`} tone={s.roi_pct >= 0 ? 'up' : 'down'} />
          <Stat label="Avg Return / Trade" value={`${s.avg_return_pct}%`} tone={s.avg_return_pct >= 0 ? 'up' : 'down'} />
          <Stat label="Avg Capital / Trade" value={`₹${NUM(s.avg_capital, 0)}`} />
          <Stat label="Avg Hold" value={`${s.avg_hold_days}d`} />
          <Stat label="Best" value={NUM(s.highest_winner)} tone="up" />
          <Stat label="Worst" value={NUM(s.largest_drawdown)} tone="down" />
          <Stat label="Still Open" value={INT(s.open)} />
        </div>
      )}

      {data && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
          <div className="flex flex-wrap items-center gap-3 px-3 py-2 border-b border-surface-3">
            <span className="text-sm font-semibold text-gray-200">Research Log <span className="text-gray-500">({shownRows.length})</span></span>
            {live && <span className="text-[11px] text-emerald-400 flex items-center gap-1"><Radio className="w-3 h-3 animate-pulse" /> live · {data.generated_at}</span>}
            {dates.length > 1 && (
              <select value={dateFilter} onChange={(e) => setDateFilter(e.target.value)} className={selCls}>
                <option value="">All dates</option>
                {dates.map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
            )}
            <span className="ml-auto flex items-center gap-2">
              {dateFilter && (
                <button onClick={() => downloadCSV(`pmvwap_equity_${dateFilter}.csv`, shownRows)}
                  className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white transition">
                  <Download className="w-3.5 h-3.5" /> This date
                </button>
              )}
              <button onClick={() => downloadCSV(`pmvwap_equity_${data.start}_${data.end}.csv`, rows)} disabled={!rows.length}
                className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-40 transition">
                <Download className="w-3.5 h-3.5" /> All ({rows.length})
              </button>
            </span>
          </div>
          {shownRows.length === 0 ? (
            <div className="px-4 py-10 text-center text-gray-500 text-sm">No holding signals for the selection.</div>
          ) : (
            <div className="overflow-auto max-h-[520px]">
              <table className="w-full text-xs whitespace-nowrap">
                <thead className="sticky top-0 z-10">
                  <tr className="text-gray-300 bg-surface-3">
                    {COLS.map(([k, l]) => <th key={k} className="px-2.5 py-2 font-semibold text-center border-r border-surface-2 last:border-r-0">{l}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {shownRows.map((r, i) => (
                    <tr key={i} className="border-b border-surface-3/40 text-center hover:bg-surface-3/10">
                      <td className="px-2.5 py-1 text-gray-400">{r.date}</td>
                      <td className="px-2.5 py-1 text-gray-200 font-semibold">{r.time}</td>
                      <td className="px-2.5 py-1 text-brand-300 font-semibold">{r.underlying}</td>
                      <td className="px-2.5 py-1 text-gray-200">{NUM(r.entry_price)}</td>
                      <td className="px-2.5 py-1 text-purple-300">{NUM(r.prev_month_vwap)}</td>
                      <td className="px-2.5 py-1 text-emerald-300">{NUM(r.prev_week_vwap)}</td>
                      <td className="px-2.5 py-1 text-gray-400">{r.direction}</td>
                      <td className="px-2.5 py-1 text-gray-300">{INT(r.qty)}</td>
                      <td className="px-2.5 py-1 text-gray-400">{NUM(r.capital)}</td>
                      <td className="px-2.5 py-1 text-amber-300">{NUM(r.target_price)}</td>
                      <td className="px-2.5 py-1 text-gray-500">{r.stop_price == null ? '—' : NUM(r.stop_price)}</td>
                      <td className="px-2.5 py-1 text-gray-200">{NUM(r.exit_price)}</td>
                      <td className="px-2.5 py-1 text-gray-500">{r.exit_date || '—'}</td>
                      <td className="px-2.5 py-1 text-gray-400">{r.exit_reason}</td>
                      <td className="px-2.5 py-1 text-gray-400">{INT(r.hold_days)}</td>
                      <td className={`px-2.5 py-1 ${r.return_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{NUM(r.return_pct)}%</td>
                      <td className={`px-2.5 py-1 font-semibold ${r.mtm >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{NUM(r.mtm)}</td>
                      <td className="px-2.5 py-1 text-gray-400">{r.status}</td>
                      <td className="px-2.5 py-1 text-gray-500">{r.notes}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {!data && !loading && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-10 text-center text-gray-500 text-sm flex items-center justify-center gap-2">
          <Info className="w-4 h-4" /> Pick a stock (or All F&O), a date range, and Run Backtest — or turn Live ON to append today's signals.
        </div>
      )}
      </>)}
    </div>
  );
}
