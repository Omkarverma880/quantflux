import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  RefreshCw, Loader2, AlertCircle, Save, FlaskConical, Download, Play, Info, Radio, BarChart3,
} from 'lucide-react';
import { api } from '../../api';
import PMVwapReport from '../../components/PMVwapReport';
import WatchlistBar from '../../components/WatchlistBar';

const selCls =
  'bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';
const lbl = 'block text-[10px] text-gray-500 uppercase tracking-wide mb-1';

const TIMEFRAMES = [['1m', '1 Min'], ['3m', '3 Min'], ['5m', '5 Min'], ['10m', '10 Min'],
  ['15m', '15 Min'], ['30m', '30 Min'], ['1h', '1 Hour'], ['1d', '1 Day']];

const INT = (v) => (v == null ? '—' : Number(v).toLocaleString('en-IN'));
const NUM = (v, d = 2) => (v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));

const COLS = [
  ['date', 'Date'], ['time', 'Time'], ['underlying', 'Underlying'], ['underlying_ltp', 'LTP'],
  ['prev_month_vwap', 'Prev-M VWAP'], ['direction', 'Dir'], ['atm_strike', 'ATM'],
  ['ce_symbol', 'CE Symbol'], ['pe_symbol', 'PE Symbol'], ['lot_size', 'Lot'],
  ['entry_ce', 'Entry CE'], ['exit_ce', 'Exit CE'], ['ce_exit_time', 'CE Exit@'],
  ['entry_pe', 'Entry PE'], ['exit_pe', 'Exit PE'], ['pe_exit_time', 'PE Exit@'],
  ['combined_premium', 'Combined'], ['target_premium', 'Target'], ['sl_premium', 'SL'],
  ['ce_mtm', 'CE MTM'], ['pe_mtm', 'PE MTM'], ['combined_mtm', 'Comb MTM'],
  ['max_profit', 'Max Profit'], ['max_loss', 'Max Loss'],
  ['status', 'Status'], ['signal_age', 'Age(m)'], ['notes', 'Notes'],
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

export default function PMVwapStraddle() {
  const [cfg, setCfg] = useState(null);
  const [sel, setSel] = useState({ mode: 'all', symbol: null, symbols: null });
  const [universe, setUniverse] = useState([]);
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [data, setData] = useState(() => { try { return JSON.parse(sessionStorage.getItem('pmvst_data') || 'null'); } catch { return null; } });
  const [dateFilter, setDateFilter] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [live, setLive] = useState(() => localStorage.getItem('pmvst_live') === '1');
  const [tab, setTab] = useState('backtest');   // backtest | report
  const [error, setError] = useState('');
  const [savedMsg, setSavedMsg] = useState('');
  const timer = useRef(null);
  const liveTargetRef = useRef((() => { try { return JSON.parse(localStorage.getItem('pmvst_target') || 'null'); } catch { return null; } })());
  useEffect(() => { localStorage.setItem('pmvst_live', live ? '1' : '0'); }, [live]);
  useEffect(() => { try { if (data) sessionStorage.setItem('pmvst_data', JSON.stringify(data)); } catch { /* quota */ } }, [data]);

  const showErr = (m) => { setError(m); setTimeout(() => setError(''), 6000); };
  const patch = (k, v) => setCfg((c) => ({ ...c, [k]: v }));

  useEffect(() => {
    api.researchPMVwapStraddleConfig().then((r) => { if (r.status === 'ok') setCfg(r.config); }).catch(() => setCfg({}));
    api.researchPMVwapStraddleUniverse()
      .then((r) => { if (r.status === 'ok') setUniverse(r.stocks || []); })
      .catch(() => {});
  }, []);

  const runBacktest = useCallback(async () => {
    if (!cfg) return;
    if (sel.mode === 'single' && !sel.symbol) { showErr('Type a stock symbol'); return; }
    if (sel.mode === 'watchlist' && !(sel.symbols?.length)) { showErr('Selected watchlist is empty'); return; }
    setLoading(true); setData(null);
    try {
      const res = await api.researchPMVwapStraddleBacktest({
        overrides: cfg, symbol: sel.mode === 'single' ? sel.symbol : null,
        symbols: sel.mode === 'watchlist' ? sel.symbols : null,
        start: start || null, end: end || start || null, persist: true,
      });
      if (res.status === 'ok') setData(res);
      else showErr(res.message || 'Backtest failed');
    } catch (e) { showErr(e.message || 'Backtest failed'); }
    finally { setLoading(false); }
  }, [cfg, sel, start, end]);

  useEffect(() => {
    const t = sel.mode === 'single' ? { symbol: sel.symbol, symbols: null }
      : sel.mode === 'watchlist' ? { symbol: null, symbols: sel.symbols }
        : { symbol: null, symbols: null };
    liveTargetRef.current = t;
    localStorage.setItem('pmvst_target', JSON.stringify(t));
  }, [sel]);

  const liveScan = useCallback(async () => {
    if (!cfg) return;
    const t = liveTargetRef.current || { symbol: null, symbols: null };
    try {
      const res = await api.researchPMVwapStraddleScan({ overrides: cfg, symbol: t.symbol, symbols: t.symbols });
      if (res.status === 'ok') setData(res);
    } catch { /* keep prior data on transient errors */ }
  }, [cfg]);

  useEffect(() => {
    if (timer.current) clearInterval(timer.current);
    if (live) { liveScan(); timer.current = setInterval(liveScan, Math.max(10, cfg?.scan_interval || 60) * 1000); }
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [live, liveScan, cfg?.scan_interval]);

  const saveDefaults = async () => {
    setSaving(true);
    try {
      const r = await api.researchPMVwapStraddleConfigSave(cfg);
      if (r.status === 'ok') { setCfg(r.config); setSavedMsg('Saved'); setTimeout(() => setSavedMsg(''), 2500); }
      else showErr(r.message || 'Save failed');
    } catch (e) { showErr(e.message || 'Save failed'); }
    finally { setSaving(false); }
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
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <FlaskConical className="w-5 h-5 text-brand-400" />
            <h1 className="text-xl font-bold text-gray-100 tracking-wide">Previous Month VWAP Straddle</h1>
            <span className="px-2 py-0.5 rounded-full bg-brand-600/15 text-brand-400 text-xs font-semibold border border-brand-500/20">Research Only</span>
          </div>
          <p className="text-gray-500 text-sm mt-0.5">
            Cross Prev-Month VWAP from below → simulate ATM CE+PE straddle, exit each leg at a combined-premium target. No orders placed.
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

      {tab === 'report' ? <PMVwapReport kind="straddle" /> : (<>
      {/* Run controls */}
      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <WatchlistBar universe={universe} count={universe.length} onChange={setSel} />
          <div><label className={lbl}>From</label><input type="date" value={start} onChange={(e) => setStart(e.target.value)} className={selCls} /></div>
          <div><label className={lbl}>To</label><input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className={selCls} /></div>
          <button onClick={runBacktest} disabled={loading}
            className="ml-auto flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50 transition">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />} Run Backtest
          </button>
        </div>

        {/* Config panel */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 pt-2 border-t border-surface-3">
          <div><label className={lbl}>Timeframe</label>
            <select value={cfg.timeframe} onChange={(e) => patch('timeframe', e.target.value)} className={`w-full ${selCls}`}>
              {TIMEFRAMES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </div>
          <div><label className={lbl}>Exit Mode</label>
            <select value={cfg.exit_mode || 'combined_pnl'} onChange={(e) => patch('exit_mode', e.target.value)} className={`w-full ${selCls}`}>
              <option value="combined_pnl">Combined P&amp;L (₹ Target / SL)</option>
              <option value="leg_target">Leg Target % (legacy)</option>
            </select>
          </div>
          {cfg.exit_mode === 'leg_target'
            ? <div><label className={lbl}>Target %</label>{num('target_pct', 0, 1)}</div>
            : <>
                <div><label className={lbl}>Target ₹ (combined)</label>{num('target_amount', 0, 500)}</div>
                <div><label className={lbl}>SL ₹ (combined)</label>{num('sl_amount', 0, 500)}</div>
              </>}
          <div><label className={lbl}>VWAP Buffer (pts)</label>{num('vwap_buffer', 0, 0.05)}</div>
          <div><label className={lbl}>Expiry</label>
            <select value={cfg.expiry_type} onChange={(e) => patch('expiry_type', e.target.value)} className={`w-full ${selCls}`}>
              <option value="monthly">Monthly</option><option value="weekly">Weekly</option>
            </select>
          </div>
          <div><label className={lbl}>Entry Start</label><input value={cfg.entry_start} onChange={(e) => patch('entry_start', e.target.value)} className={`w-full ${selCls}`} /></div>
          <div><label className={lbl}>Signal Cutoff</label><input value={cfg.signal_cutoff} onChange={(e) => patch('signal_cutoff', e.target.value)} className={`w-full ${selCls}`} /></div>
          <div><label className={lbl}>Square-off</label><input value={cfg.square_off} onChange={(e) => patch('square_off', e.target.value)} className={`w-full ${selCls}`} /></div>
          <div><label className={lbl}>History Days</label>{num('history_days', 35)}</div>
          <div><label className={lbl}>Min Price</label>{num('min_price', 0)}</div>
          <div><label className={lbl}>Max Price</label>{num('max_price', 0)}</div>
          <div><label className={lbl}>Min Volume</label>{num('min_volume', 0)}</div>
          <div><label className={lbl}>Max Stocks (0=all)</label>{num('max_stocks', 0)}</div>
          <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer">
            <input type="checkbox" checked={cfg.one_signal_per_day} onChange={(e) => patch('one_signal_per_day', e.target.checked)} className="accent-brand-500" /> One signal/day
          </label>
          <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer">
            <input type="checkbox" checked={cfg.high_vol_only} onChange={(e) => patch('high_vol_only', e.target.checked)} className="accent-brand-500" /> High-vol only
          </label>
          <div><label className={lbl}>High-vol % thr</label>{num('high_vol_threshold', 0, 0.5)}</div>
          <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer">
            <input type="checkbox" checked={cfg.ignore_ban} onChange={(e) => patch('ignore_ban', e.target.checked)} className="accent-brand-500" /> Ignore ban stocks
          </label>
        </div>

        {/* Realistic costs (per option leg: brokerage + STT + slippage) */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 pt-2 border-t border-surface-3 items-end">
          <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
            <input type="checkbox" checked={cfg.apply_costs} onChange={(e) => patch('apply_costs', e.target.checked)} className="accent-brand-500" /> Net of costs
          </label>
          <div><label className={lbl}>Slippage (bps)</label>{num('slippage_bps', 0, 1)}</div>
          <div><label className={lbl}>Brokerage/order</label>{num('brokerage_per_order', 0, 1)}</div>
          <div><label className={lbl}>Charges %</label>{num('charges_pct', 0, 0.01)}</div>
        </div>
      </div>

      {/* Stats */}
      {s && (
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-2">
          <Stat label="Signals" value={INT(s.total_signals)} />
          <Stat label="Win %" value={`${s.win_rate}%`} tone={s.win_rate >= 50 ? 'up' : 'down'} />
          <Stat label={cfg.apply_costs ? 'Total MTM (net)' : 'Total MTM'} value={NUM(s.total_mtm)} tone={s.total_mtm >= 0 ? 'up' : 'down'} />
          {cfg.apply_costs && <Stat label="Total Costs" value={NUM(s.total_cost)} tone="down" />}
          <Stat label="Avg Combined" value={NUM(s.avg_combined_premium)} />
          <Stat label="Avg Time→Tgt" value={s.avg_time_to_target == null ? '—' : `${s.avg_time_to_target}m`} />
          <Stat label="Best Winner" value={NUM(s.highest_winner)} tone="up" />
          <Stat label="Largest DD" value={NUM(s.largest_drawdown)} tone="down" />
          <Stat label="Stocks" value={INT(data.stocks_scanned)} />
        </div>
      )}

      {/* Log table */}
      {data && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
          <div className="flex flex-wrap items-center gap-3 px-3 py-2 border-b border-surface-3">
            <span className="text-sm font-semibold text-gray-200">Research Log <span className="text-gray-500">({shownRows.length})</span></span>
            {dates.length > 1 && (
              <select value={dateFilter} onChange={(e) => setDateFilter(e.target.value)} className={selCls}>
                <option value="">All dates</option>
                {dates.map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
            )}
            <span className="ml-auto flex items-center gap-2">
              {dateFilter && (
                <button onClick={() => downloadCSV(`pmvwap_straddle_${dateFilter}.csv`, shownRows)}
                  className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white transition">
                  <Download className="w-3.5 h-3.5" /> This date
                </button>
              )}
              <button onClick={() => downloadCSV(`pmvwap_straddle_${data.start}_${data.end}.csv`, rows)} disabled={!rows.length}
                className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-40 transition">
                <Download className="w-3.5 h-3.5" /> All ({rows.length})
              </button>
            </span>
          </div>
          {shownRows.length === 0 ? (
            <div className="px-4 py-10 text-center text-gray-500 text-sm">No signals for the selection.</div>
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
                      <td className="px-2.5 py-1 text-gray-300">{NUM(r.underlying_ltp)}</td>
                      <td className="px-2.5 py-1 text-purple-300">{NUM(r.prev_month_vwap)}</td>
                      <td className="px-2.5 py-1 text-gray-400">{r.direction}</td>
                      <td className="px-2.5 py-1 text-gray-200">{INT(r.atm_strike)}</td>
                      <td className="px-2.5 py-1 text-gray-500">{r.ce_symbol}</td>
                      <td className="px-2.5 py-1 text-gray-500">{r.pe_symbol}</td>
                      <td className="px-2.5 py-1 text-gray-400">{INT(r.lot_size)}</td>
                      <td className="px-2.5 py-1 text-gray-300">{NUM(r.entry_ce)}</td>
                      <td className="px-2.5 py-1 text-gray-300">{NUM(r.exit_ce)}<div className="text-[9px] text-gray-600">{r.ce_exit_reason}</div></td>
                      <td className="px-2.5 py-1 text-gray-400">{r.ce_exit_time || '—'}</td>
                      <td className="px-2.5 py-1 text-gray-300">{NUM(r.entry_pe)}</td>
                      <td className="px-2.5 py-1 text-gray-300">{NUM(r.exit_pe)}<div className="text-[9px] text-gray-600">{r.pe_exit_reason}</div></td>
                      <td className="px-2.5 py-1 text-gray-400">{r.pe_exit_time || '—'}</td>
                      <td className="px-2.5 py-1 text-gray-200">{NUM(r.combined_premium)}</td>
                      <td className="px-2.5 py-1 text-amber-300">{NUM(r.target_premium)}</td>
                      <td className="px-2.5 py-1 text-red-300">{r.sl_premium == null ? '—' : NUM(r.sl_premium)}</td>
                      <td className={`px-2.5 py-1 ${r.ce_mtm >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{NUM(r.ce_mtm)}</td>
                      <td className={`px-2.5 py-1 ${r.pe_mtm >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{NUM(r.pe_mtm)}</td>
                      <td className={`px-2.5 py-1 font-semibold ${r.combined_mtm >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{NUM(r.combined_mtm)}</td>
                      <td className="px-2.5 py-1 text-emerald-400">{r.max_profit == null ? '—' : NUM(r.max_profit)}</td>
                      <td className="px-2.5 py-1 text-red-400">{r.max_loss == null ? '—' : NUM(r.max_loss)}</td>
                      <td className="px-2.5 py-1 text-gray-400">{r.status}</td>
                      <td className="px-2.5 py-1 text-gray-400">{r.signal_age ?? '—'}</td>
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
          <Info className="w-4 h-4" /> Pick a stock (or All F&O), a date range, and click Run Backtest — or open the Summary Report tab.
        </div>
      )}
      </>)}
    </div>
  );
}
