import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  CandlestickChart, Play, Loader2, AlertCircle, Download, Info, TrendingUp, TrendingDown,
  FlaskConical, Wallet, Radio, RefreshCw, Save,
} from 'lucide-react';
import { api } from '../../api';
import WatchlistBar from '../../components/WatchlistBar';

const sel = 'bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';
const lbl = 'block text-[11px] text-gray-500 uppercase tracking-wide mb-1';
const NUM = (v, d = 2) => (v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
const INT = (v) => (v == null ? '—' : Number(v).toLocaleString('en-IN'));
const RUNNING = new Set(['queued', 'running']);

function OptBadge({ t }) {
  if (t === 'CE') return <span className="inline-flex items-center gap-1 text-emerald-400"><TrendingUp className="w-3.5 h-3.5" />CALL</span>;
  if (t === 'PE') return <span className="inline-flex items-center gap-1 text-red-400"><TrendingDown className="w-3.5 h-3.5" />PUT</span>;
  return <span className="text-gray-500">—</span>;
}
const stColor = (s) => ({ TARGET: 'text-emerald-400', STOP: 'text-red-400', OPEN: 'text-amber-400', SQUAREOFF: 'text-gray-400' }[s] || 'text-gray-400');

function Tile({ label, children }) {
  return (
    <div className="bg-surface-3/40 border border-surface-3 rounded-lg px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-0.5">{label}</div>
      <div className="text-sm">{children}</div>
    </div>
  );
}

export default function FourthCandle() {
  const [cfg, setCfg] = useState(null);
  const [tab, setTab] = useState('backtest');
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const showErr = (m) => { setErr(m); setTimeout(() => setErr(''), 6000); };
  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(''), 2500); };
  const patch = (k, v) => setCfg((c) => ({ ...c, [k]: v }));

  const [universe, setUniverse] = useState([]);
  const [sel2, setSel2] = useState({ mode: 'all', symbol: null, symbols: null });
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [showNonTrades, setShowNonTrades] = useState(false);

  // simulate
  const [simSym, setSimSym] = useState('');
  const [simDate, setSimDate] = useState('');
  const [sim, setSim] = useState(null);
  const [simLoading, setSimLoading] = useState(false);
  const [simSugg, setSimSugg] = useState([]);
  const [simSearching, setSimSearching] = useState(false);
  const simTimer = useRef(null);
  useEffect(() => {
    if (simTimer.current) clearTimeout(simTimer.current);
    if (!simSym || simSym.trim().length < 2) { setSimSugg([]); return; }
    setSimSearching(true);
    simTimer.current = setTimeout(async () => {
      try { const r = await api.researchSymbolSearch(simSym.trim()); setSimSugg(r.status === 'ok' ? (r.results || []) : []); }
      catch { setSimSugg([]); } finally { setSimSearching(false); }
    }, 250);
    return () => simTimer.current && clearTimeout(simTimer.current);
  }, [simSym]);

  // strategy
  const [status, setStatus] = useState(null);
  const [symbolsText, setSymbolsText] = useState('');
  const [positions, setPositions] = useState([]);
  const pollRef = useRef(null);

  useEffect(() => {
    api.fcConfig().then((r) => { if (r.status === 'ok') { setCfg(r.config); setSymbolsText((r.config.symbols || []).join(', ')); } }).catch(() => setCfg({}));
    api.researchPMVwapEquityUniverse?.().then((r) => { if (r?.status === 'ok') setUniverse(r.stocks || []); }).catch(() => {});
    const t = new Date(); const y = new Date(); y.setDate(t.getDate() - 20);
    setEnd(t.toISOString().slice(0, 10)); setStart(y.toISOString().slice(0, 10));
  }, []);

  const runBacktest = useCallback(async () => {
    if (!cfg) return;
    setLoading(true); setErr('');
    try {
      const body = {
        overrides: cfg, start, end, include_non_trades: showNonTrades,
        symbol: sel2.mode === 'single' ? sel2.symbol : null,
        symbols: sel2.mode === 'watchlist' ? sel2.symbols : null,
      };
      const r = await api.fcBacktest(body);
      if (r.status === 'ok') setData(r); else showErr(r.message || 'Backtest failed');
    } catch (e) { showErr(e.message); } finally { setLoading(false); }
  }, [cfg, sel2, start, end, showNonTrades]);

  const runSimulate = async () => {
    if (!simSym.trim()) return showErr('Enter a stock symbol');
    setSimLoading(true); setErr('');
    try {
      const r = await api.fcSimulate({ symbol: simSym.trim().toUpperCase(), overrides: cfg, date: simDate || null });
      if (r.status === 'ok') setSim(r); else showErr(r.message || 'Simulate failed');
    } catch (e) { showErr(e.message); } finally { setSimLoading(false); }
  };

  const saveCfg = async () => {
    const syms = symbolsText.split(/[\s,;\n]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
    const r = await api.fcConfigSave({ ...cfg, symbols: syms });
    if (r.status === 'ok') { setCfg(r.config); flash('Config saved'); } else showErr(r.message);
  };

  // strategy control
  const loadStatus = useCallback(async () => { try { const r = await api.fcStatus(); if (r.status === 'ok') setStatus(r); } catch { /* */ } }, []);
  const loadPositions = useCallback(async () => { try { const r = await api.fcPositions(); if (r.status === 'ok') setPositions(r.positions || []); } catch { /* */ } }, []);
  useEffect(() => { if (tab === 'positions') { loadStatus(); loadPositions(); pollRef.current = setInterval(() => { loadStatus(); loadPositions(); }, 5000); return () => clearInterval(pollRef.current); } }, [tab, loadStatus, loadPositions]);

  const startStrat = async () => {
    const syms = symbolsText.split(/[\s,;\n]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
    const r = await api.fcStart({ ...cfg, symbols: syms });
    if (r.status === 'ok') { setStatus(r); flash('Strategy started'); loadPositions(); } else showErr(r.message);
  };
  const stopStrat = async () => { const r = await api.fcStop(); if (r.status === 'ok') { setStatus(r); flash('Stopped'); } else showErr(r.message); };
  const saveStratCfg = async () => {
    const syms = symbolsText.split(/[\s,;\n]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
    const r = await api.fcUpdateConfig({ ...cfg, symbols: syms });
    if (r.status === 'ok') { flash('Saved'); } else showErr(r.message);
  };

  const rows = data?.rows || [];
  const downloadCSV = () => {
    const cols = ['date', 'underlying', 'bias', 'fourth_high', 'fourth_low', 'breakout_time', 'entry_time', 'opt_type', 'symbol', 'strike', 'lot', 'qty', 'entry', 'target', 'sl', 'exit', 'exit_time', 'exit_reason', 'mtm', 'max_profit', 'max_loss', 'hold_days', 'hold_label', 'status', 'notes'];
    const esc = (v) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const lines = [cols.join(',')].concat(rows.map((r) => cols.map((c) => esc(r[c])).join(',')));
    const blob = new Blob([lines.join('\n') + '\n'], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = '4th_candle_backtest.csv'; a.click();
  };

  if (!cfg) return <div className="p-6 text-gray-500 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>;

  // keep the raw typed text so the field can be cleared and leading zeros removed;
  // the backend sanitize() coerces to number (blank → default) on backtest/save.
  const num = (k, min = 0, step = 1) => <input type="number" min={min} step={step} value={cfg[k] ?? ''} onChange={(e) => patch(k, e.target.value)} className={`w-full ${sel}`} />;

  const ConfigGrid = () => (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
      <div><label className={lbl}>Target By</label><select value={cfg.target_mode} onChange={(e) => patch('target_mode', e.target.value)} className={`w-full ${sel}`}><option value="percent">Percent</option><option value="points">Points</option></select></div>
      <div><label className={lbl}>Target {cfg.target_mode === 'points' ? '(pts)' : '%'}</label>{num('target_value', 0, 1)}</div>
      <div><label className={lbl}>SL By</label><select value={cfg.sl_mode} onChange={(e) => patch('sl_mode', e.target.value)} className={`w-full ${sel}`}><option value="percent">Percent</option><option value="points">Points</option></select></div>
      <div><label className={lbl}>SL {cfg.sl_mode === 'points' ? '(pts)' : '%'}</label>{num('sl_value', 0, 1)}</div>
      <div><label className={lbl}>Entry Cutoff</label><input value={cfg.entry_cutoff} onChange={(e) => patch('entry_cutoff', e.target.value)} className={`w-full ${sel}`} /></div>
      <div><label className={lbl}>Expiry</label><select value={cfg.expiry_type} onChange={(e) => patch('expiry_type', e.target.value)} className={`w-full ${sel}`}><option value="monthly">Monthly</option><option value="weekly">Weekly</option></select></div>
      <div><label className={lbl}>Lots</label>{num('lots', 1, 1)}</div>
      <div><label className={lbl}>Max Positions</label>{num('max_positions', 1, 1)}</div>
      <div><label className={lbl}>Max Calls</label>{num('max_calls', 0, 1)}</div>
      <div><label className={lbl}>Max Puts</label>{num('max_puts', 0, 1)}</div>
      <div><label className={lbl}>Product</label><select value={cfg.product} onChange={(e) => patch('product', e.target.value)} className={`w-full ${sel}`}><option value="NRML">NRML (normal)</option><option value="MIS">MIS (intraday)</option></select></div>
      <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer self-end"><input type="checkbox" checked={cfg.apply_costs} onChange={(e) => patch('apply_costs', e.target.checked)} className="accent-brand-500" /> Net of costs</label>
    </div>
  );

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-[1600px] mx-auto">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <CandlestickChart className="w-5 h-5 text-brand-400" />
            <h1 className="text-xl font-bold text-gray-100 tracking-wide">4th Candle Strategy</h1>
            <span className="px-2 py-0.5 rounded-full bg-brand-600/15 text-brand-400 text-xs font-semibold border border-brand-500/20">Equity Strategy 2</span>
          </div>
          <p className="text-gray-500 text-sm mt-0.5">First 3 red → break above 4th-candle high → BUY CALL. First 3 green → break below 4th-candle low → BUY PUT. Positional (NRML) options; target/SL on premium.</p>
        </div>
        {status && <div className={`flex items-center gap-1.5 text-xs ${status.is_active ? 'text-emerald-400' : 'text-gray-500'}`}><Radio className={`w-4 h-4 ${status.is_active ? 'animate-pulse' : ''}`} /> {status.is_active ? 'LIVE ON' : 'LIVE OFF'} · {status.paper_trade ? 'Paper' : 'REAL'}</div>}
      </div>

      <div className="flex gap-1 border-b border-surface-3">
        {[['backtest', 'Backtest', FlaskConical], ['simulate', 'Simulate', CandlestickChart], ['positions', 'Positions', Wallet], ['info', 'Info', Info]].map(([id, label, Icon]) => (
          <button key={id} onClick={() => setTab(id)} className={`flex items-center gap-1.5 px-4 py-2 text-sm font-semibold border-b-2 -mb-px transition ${tab === id ? 'border-brand-500 text-brand-400' : 'border-transparent text-gray-400 hover:text-gray-200'}`}><Icon className="w-4 h-4" /> {label}</button>
        ))}
      </div>

      {err && <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-red-400 text-sm"><AlertCircle className="w-4 h-4" /> {err}</div>}
      {msg && <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-4 py-2 text-emerald-400 text-sm">{msg}</div>}

      {tab === 'backtest' && (
        <div className="space-y-4">
          <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
            <div className="flex flex-wrap items-end gap-3">
              <div className="flex-1 min-w-[280px]"><WatchlistBar universe={universe} count={universe.length} onChange={setSel2} /></div>
              <div><label className={lbl}>From</label><input type="date" value={start} onChange={(e) => setStart(e.target.value)} className={sel} /></div>
              <div><label className={lbl}>To</label><input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className={sel} /></div>
              <button onClick={runBacktest} disabled={loading} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">{loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />} Run Backtest</button>
              <button onClick={saveCfg} className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white"><Save className="w-3.5 h-3.5" /> Save default</button>
              <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer ml-1" title="Also list stocks that had a bias/breakout but produced no trade (no breakout by cutoff, mixed candles, or missing option data), with the reason.">
                <input type="checkbox" checked={showNonTrades} onChange={(e) => setShowNonTrades(e.target.checked)} className="accent-brand-500" /> Show non-trade days
              </label>
            </div>
            {ConfigGrid()}
          </div>

          {data?.note && (
            <div className="text-xs text-amber-300/90 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2">{data.note}</div>
          )}
          {data && (
            <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm bg-surface-2 border border-surface-3 rounded-xl px-4 py-2.5">
              <span className="text-gray-400">Trades <strong className="text-gray-100">{data.stats.total}</strong></span>
              <span className="text-gray-400">Win% <strong className="text-emerald-400">{data.stats.win_rate}%</strong></span>
              <span className="text-gray-400">Total MTM <strong className={data.stats.total_mtm >= 0 ? 'text-emerald-400' : 'text-red-400'}>{NUM(data.stats.total_mtm)}</strong></span>
              <span className="text-gray-400">Calls <strong className="text-emerald-400">{data.stats.calls}</strong> · Puts <strong className="text-red-400">{data.stats.puts}</strong></span>
              <span className="text-gray-400">Best <strong className="text-emerald-400">{NUM(data.stats.best)}</strong> · Worst <strong className="text-red-400">{NUM(data.stats.worst)}</strong></span>
              <button onClick={downloadCSV} disabled={!rows.length} className="ml-auto flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-40"><Download className="w-3.5 h-3.5" /> CSV</button>
            </div>
          )}

          {data && (
            <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
              <div className="overflow-x-auto"><table className="w-full text-xs whitespace-nowrap">
                <thead className="bg-surface-3 text-gray-300"><tr>{['Date', 'Stock', 'Bias', '4th High', '4th Low', 'Breakout', 'Opt', 'Symbol', 'Strike', 'Lot', 'Qty', 'Entry', 'Target', 'SL', 'Exit', 'Reason', 'MTM', 'Max Profit', 'Max Loss', 'Hold'].map((h) => <th key={h} className="px-2.5 py-2 font-semibold text-right first:text-left">{h}</th>)}</tr></thead>
                <tbody>{rows.map((r, i) => (r.qty ? (
                  <tr key={i} className="border-t border-surface-3/40 hover:bg-surface-3/10">
                    <td className="px-2.5 py-1.5 text-left text-gray-400">{r.date}</td>
                    <td className="px-2.5 py-1.5 text-left text-brand-300 font-semibold">{r.underlying}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-400">{r.bias === 'call' ? '3 RED' : '3 GREEN'}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-300">{NUM(r.fourth_high)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-300">{NUM(r.fourth_low)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-400">{r.breakout_time}</td>
                    <td className="px-2.5 py-1.5 text-right"><OptBadge t={r.opt_type} /></td>
                    <td className="px-2.5 py-1.5 text-right text-gray-500">{r.symbol}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-300">{INT(r.strike)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-400">{INT(r.lot)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-200 font-medium">{INT(r.qty)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-200">₹{NUM(r.entry)}</td>
                    <td className="px-2.5 py-1.5 text-right text-emerald-400">₹{NUM(r.target)}</td>
                    <td className="px-2.5 py-1.5 text-right text-red-400">₹{NUM(r.sl)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-300">{r.exit == null ? '—' : `₹${NUM(r.exit)}`}</td>
                    <td className={`px-2.5 py-1.5 text-right font-semibold ${stColor(r.exit_reason)}`}>{r.exit_reason}</td>
                    <td className={`px-2.5 py-1.5 text-right font-semibold ${r.mtm >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{NUM(r.mtm)}</td>
                    <td className="px-2.5 py-1.5 text-right text-emerald-400">{NUM(r.max_profit)}</td>
                    <td className="px-2.5 py-1.5 text-right text-red-400">{NUM(r.max_loss)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-400" title={r.entry_time && r.exit_time ? `${r.entry_time} → ${r.exit_time}` : ''}>{r.hold_label ?? (r.open ? 'open' : '—')}</td>
                  </tr>
                ) : (
                  <tr key={i} className="border-t border-surface-3/40 bg-surface-3/10 text-gray-500">
                    <td className="px-2.5 py-1.5 text-left text-gray-500">{r.date}</td>
                    <td className="px-2.5 py-1.5 text-left text-gray-400 font-semibold">{r.underlying}</td>
                    <td className="px-2.5 py-1.5 text-right">{r.bias ? (r.bias === 'call' ? '3 RED' : '3 GREEN') : 'MIXED'}</td>
                    <td className="px-2.5 py-1.5 text-right">{NUM(r.fourth_high)}</td>
                    <td className="px-2.5 py-1.5 text-right">{NUM(r.fourth_low)}</td>
                    <td className="px-2.5 py-1.5 text-right">{r.breakout_time || '—'}</td>
                    <td className="px-2.5 py-1.5 text-right"><OptBadge t={r.opt_type} /></td>
                    <td className="px-2.5 py-1.5 text-left text-amber-400/80 italic" colSpan={13}>{r.status} — {r.notes}</td>
                  </tr>
                )))}
                {!rows.length && <tr><td colSpan={20} className="px-4 py-8 text-center text-gray-500">No breakout trades in range.{!showNonTrades && ' Tick “Show non-trade days” to see why stocks were skipped.'}</td></tr>}
                </tbody>
              </table></div>
            </div>
          )}
        </div>
      )}

      {tab === 'simulate' && (
        <div className="space-y-4">
          <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 flex flex-wrap items-end gap-3">
            <div className="relative">
              <label className={lbl}>Stock (search NSE/BSE)</label>
              <div className="flex items-center gap-2 bg-surface-3 border border-surface-4 rounded-lg px-3">
                <input value={simSym} onChange={(e) => setSimSym(e.target.value.toUpperCase())} placeholder="Type e.g. RELIANCE, BSE, TCS…" className="w-64 bg-transparent py-1.5 text-sm text-gray-200 focus:outline-none" />
                {simSearching && <Loader2 className="w-4 h-4 animate-spin text-gray-500" />}
              </div>
              {simSugg.length > 0 && (
                <div className="absolute z-20 mt-1 w-full max-h-64 overflow-auto bg-surface-2 border border-surface-3 rounded-lg shadow-2xl">
                  {simSugg.map((s) => (
                    <button key={`${s.symbol}:${s.exchange}`} onClick={() => { setSimSym(s.symbol); setSimSugg([]); }} className="w-full text-left px-3 py-2 hover:bg-surface-3/40 flex items-center gap-2 border-b border-surface-3/40 last:border-0">
                      <span className="text-sm text-gray-100 font-medium">{s.symbol}</span>
                      <span className="text-[11px] text-gray-500 ml-auto">{s.exchange}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div><label className={lbl}>Date</label><input type="date" value={simDate} onChange={(e) => setSimDate(e.target.value)} className={sel} /></div>
            <button onClick={runSimulate} disabled={simLoading} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">{simLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <CandlestickChart className="w-4 h-4" />} Simulate</button>
            <span className="text-[11px] text-gray-600">Note: options-based — the stock must be F&O for a trade to simulate.</span>
          </div>
          {sim && <SimulateView sim={sim} />}
          {!sim && !simLoading && <div className="bg-surface-2 border border-surface-3 rounded-xl p-10 text-center text-gray-500 text-sm">Pick a stock &amp; date to see the first-3-candle colours, the 4th-candle lines, the breakout, and the resulting trade with max profit/loss & MTM.</div>}
        </div>
      )}

      {tab === 'positions' && (
        <div className="space-y-4">
          <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer"><input type="checkbox" checked={cfg.paper_trade} onChange={(e) => patch('paper_trade', e.target.checked)} className="accent-brand-500" /> Paper mode</label>
              <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer"><input type="checkbox" checked={cfg.auto_start} onChange={(e) => patch('auto_start', e.target.checked)} className="accent-brand-500" /> Auto-start on login</label>
              {status && <span className="text-xs text-gray-500">Open {status.open_positions}/{status.max_positions} · Calls {status.calls}/{status.max_calls} · Puts {status.puts}/{status.max_puts}</span>}
              <div className="ml-auto flex items-center gap-2">
                {status?.is_active
                  ? <button onClick={stopStrat} className="px-3 py-1.5 text-sm rounded-lg bg-red-600/80 hover:bg-red-600 text-white font-semibold">Stop</button>
                  : <button onClick={startStrat} className="px-3 py-1.5 text-sm rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white font-semibold">Start</button>}
                <button onClick={saveStratCfg} className="px-3 py-1.5 text-sm rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white flex items-center gap-1"><Save className="w-3.5 h-3.5" /> Save</button>
                <button onClick={() => { loadStatus(); loadPositions(); }} className="text-gray-400 hover:text-white"><RefreshCw className="w-4 h-4" /></button>
              </div>
            </div>
            <div><label className={lbl}>Watchlist (comma / space separated F&O stocks)</label>
              <textarea value={symbolsText} onChange={(e) => setSymbolsText(e.target.value)} rows={2} placeholder="RELIANCE, TCS, HDFCBANK, INFY …" className={`w-full ${sel}`} /></div>
            {ConfigGrid()}
            <div className="flex flex-wrap items-center gap-4 pt-2 border-t border-surface-3">
              <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer"><input type="checkbox" checked={cfg.telegram_alerts} onChange={(e) => patch('telegram_alerts', e.target.checked)} className="accent-brand-500" /> Telegram alerts</label>
              <div className="flex items-center gap-2 text-xs text-gray-400">Bot
                <select value={cfg.telegram_bot || 'a'} onChange={(e) => patch('telegram_bot', e.target.value)} className={`${sel} py-1`}><option value="a">Bot A</option><option value="b">Bot B</option></select>
              </div>
              <span className="text-[11px] text-gray-600">Configure bots in Settings → Telegram, then Save to apply. Alerts fire on each entry/exit.</span>
            </div>
            {!cfg.paper_trade && <p className="text-[11px] text-amber-400">⚠ REAL mode also needs global PAPER_TRADE=False + TRADING_ENABLED=True. Orders are {cfg.product} (positional).</p>}
          </div>

          <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
            <div className="px-3 py-2 border-b border-surface-3 text-sm font-semibold text-gray-200">Positions (today) <span className="text-gray-500">({positions.length})</span></div>
            {!positions.length ? <div className="px-4 py-8 text-center text-gray-500 text-sm">No positions yet — the strategy opens them on 4th-candle breakouts while Live is on.</div> : (
              <div className="overflow-x-auto"><table className="w-full text-xs whitespace-nowrap">
                <thead className="bg-surface-3 text-gray-300"><tr>{['Stock', 'Opt', 'Symbol', 'Strike', 'Entry@', 'Entry', 'Target', 'SL', 'LTP', 'MTM', 'Max Profit', 'Max Loss', 'Status', 'Exit@'].map((h) => <th key={h} className="px-2.5 py-2 font-semibold text-right first:text-left">{h}</th>)}</tr></thead>
                <tbody>{positions.map((p) => (
                  <tr key={p.id} className="border-t border-surface-3/40">
                    <td className="px-2.5 py-1.5 text-left text-brand-300 font-semibold">{p.underlying}{p.paper && <span className="ml-1 text-[9px] px-1 rounded bg-surface-3 text-gray-500 border border-surface-4">paper</span>}</td>
                    <td className="px-2.5 py-1.5 text-right"><OptBadge t={p.opt_type} /></td>
                    <td className="px-2.5 py-1.5 text-right text-gray-500">{p.symbol}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-300">{INT(p.strike)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-500">{p.entry_time}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-200">₹{NUM(p.entry_price)}</td>
                    <td className="px-2.5 py-1.5 text-right text-emerald-400">₹{NUM(p.target)}</td>
                    <td className="px-2.5 py-1.5 text-right text-red-400">₹{NUM(p.sl)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-200">₹{NUM(p.ltp)}</td>
                    <td className={`px-2.5 py-1.5 text-right font-semibold ${p.mtm >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{NUM(p.mtm, 0)}</td>
                    <td className="px-2.5 py-1.5 text-right text-emerald-400">{NUM(p.mfe, 0)}</td>
                    <td className="px-2.5 py-1.5 text-right text-red-400">{NUM(p.mae, 0)}</td>
                    <td className={`px-2.5 py-1.5 text-right font-semibold ${stColor(p.status)}`}>{p.status}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-500">{p.exit_time || '—'}</td>
                  </tr>
                ))}</tbody>
              </table></div>
            )}
          </div>
        </div>
      )}

      {tab === 'info' && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-5 space-y-3 text-sm text-gray-300 max-w-3xl">
          <h3 className="font-semibold text-gray-100">How the 4th Candle Strategy works</h3>
          <p>On a <strong>5-minute chart</strong>, the first 4 candles cover 09:15–09:35. The <strong>4th candle</strong> (09:30–09:35) high &amp; low become reference lines that extend through the day.</p>
          <ul className="list-disc pl-5 space-y-1 text-gray-400">
            <li>If the <strong className="text-red-400">first 3 candles are all RED</strong> → wait for a candle that <strong>breaks above the 4th-candle HIGH</strong> → <strong className="text-emerald-400">buy ATM CALL</strong>.</li>
            <li>If the <strong className="text-emerald-400">first 3 candles are all GREEN</strong> → wait for a candle that <strong>breaks below the 4th-candle LOW</strong> → <strong className="text-red-400">buy ATM PUT</strong>.</li>
            <li>Mixed first 3 candles → <strong>no trade</strong>.</li>
          </ul>
          <p>Exit on <strong>target/SL on the option premium</strong> (default 30% target, 25% stop — configurable by % or points), else square off on expiry. Positional (<strong>NRML</strong>) orders — no intraday square-off, no single-day wipeout worry.</p>
          <p><strong>Portfolio:</strong> up to <strong>{cfg.max_positions}</strong> open positions split <strong>{cfg.max_calls}:{cfg.max_puts}</strong> CALL:PUT (configurable). <strong>Backtest</strong> and <strong>Simulate</strong> are read-only; <strong>Positions</strong> runs paper by default — real orders need the global trading gate.</p>
          <p className="text-[11px] text-gray-600 flex items-center gap-1"><Info className="w-3 h-3" /> Max Profit / Max Loss use the option's intraday high/low after entry; MTM is the current premium P&L.</p>
        </div>
      )}
    </div>
  );
}

const GREEN = '#10b981';
const RED = '#ef4444';

// Hand-rolled SVG candlestick chart (TradingView-lite) — draws the day's 5-min
// candles, the 4th-candle high/low reference lines, and marks the breakout candle.
function CandleChart({ timeline, fourthHigh, fourthLow, breakoutHHMM, exitHHMM, bias }) {
  const n = (timeline || []).length;
  if (!n) return null;
  const step = 16, cw = 9, padL = 6, padR = 66, padT = 14, padB = 26, plotH = 340;
  const width = padL + n * step + padR;
  const height = padT + plotH + padB;
  let lo = Math.min(fourthLow ?? Infinity, ...timeline.map((c) => c.low));
  let hi = Math.max(fourthHigh ?? -Infinity, ...timeline.map((c) => c.high));
  const pad = (hi - lo) * 0.04 || 1;
  lo -= pad; hi += pad;
  const y = (p) => padT + (hi - p) / (hi - lo) * plotH;
  const xc = (i) => padL + i * step + step / 2;
  const ticks = Array.from({ length: 5 }, (_, k) => lo + (hi - lo) * k / 4);
  const boIdx = timeline.findIndex((c) => c.time === breakoutHHMM);
  const exIdx = exitHHMM ? timeline.findIndex((c) => c.time === exitHHMM) : -1;
  const biasColor = bias === 'call' ? GREEN : bias === 'put' ? RED : '#9ca3af';
  return (
    <div className="overflow-x-auto">
      <svg width={width} height={height} className="block" style={{ minWidth: '100%' }}>
        {/* price gridlines + labels */}
        {ticks.map((p, k) => (
          <g key={k}>
            <line x1={padL} x2={padL + n * step} y1={y(p)} y2={y(p)} stroke="#ffffff10" />
            <text x={padL + n * step + 6} y={y(p) + 3} fontSize="10" fill="#6b7280">{NUM(p, 1)}</text>
          </g>
        ))}
        {/* 4th-candle high line */}
        <line x1={padL} x2={padL + n * step} y1={y(fourthHigh)} y2={y(fourthHigh)} stroke={RED} strokeWidth="1.2" strokeDasharray="5 3" />
        <rect x={padL + n * step + 2} y={y(fourthHigh) - 8} width={62} height={15} rx={3} fill={RED} />
        <text x={padL + n * step + 6} y={y(fourthHigh) + 3} fontSize="9.5" fill="#fff" fontWeight="600">H {NUM(fourthHigh, 1)}</text>
        {/* 4th-candle low line */}
        <line x1={padL} x2={padL + n * step} y1={y(fourthLow)} y2={y(fourthLow)} stroke={GREEN} strokeWidth="1.2" strokeDasharray="5 3" />
        <rect x={padL + n * step + 2} y={y(fourthLow) - 8} width={62} height={15} rx={3} fill={GREEN} />
        <text x={padL + n * step + 6} y={y(fourthLow) + 3} fontSize="9.5" fill="#052e1b" fontWeight="600">L {NUM(fourthLow, 1)}</text>
        {/* 4th-candle column highlight */}
        <rect x={xc(3) - step / 2} y={padT} width={step} height={plotH} fill="#3b82f611" />
        {/* breakout marker */}
        {boIdx >= 0 && (
          <g>
            <line x1={xc(boIdx)} x2={xc(boIdx)} y1={padT} y2={padT + plotH} stroke={biasColor} strokeWidth="1" strokeDasharray="3 3" opacity="0.7" />
            <polygon points={`${xc(boIdx) - 5},${padT + plotH + 2} ${xc(boIdx) + 5},${padT + plotH + 2} ${xc(boIdx)},${padT + plotH - 6}`} fill={biasColor} />
          </g>
        )}
        {/* exit marker */}
        {exIdx >= 0 && (
          <line x1={xc(exIdx)} x2={xc(exIdx)} y1={padT} y2={padT + plotH} stroke="#f59e0b" strokeWidth="1" strokeDasharray="2 4" opacity="0.7" />
        )}
        {/* candles */}
        {timeline.map((c, i) => {
          const up = c.close >= c.open;
          const col = c.color === 'green' ? GREEN : c.color === 'red' ? RED : '#9ca3af';
          const yO = y(c.open), yC = y(c.close);
          const top = Math.min(yO, yC);
          const h = Math.max(Math.abs(yO - yC), 1);
          return (
            <g key={i}>
              <line x1={xc(i)} x2={xc(i)} y1={y(c.high)} y2={y(c.low)} stroke={col} strokeWidth="1" />
              <rect x={xc(i) - cw / 2} y={top} width={cw} height={h} fill={col} rx={0.5} />
              <rect x={xc(i) - step / 2} y={padT} width={step} height={plotH} fill="transparent">
                <title>{`${c.time}  O ${NUM(c.open)}  H ${NUM(c.high)}  L ${NUM(c.low)}  C ${NUM(c.close)}`}</title>
              </rect>
              {i % 6 === 0 && <text x={xc(i)} y={height - 8} fontSize="9" fill="#6b7280" textAnchor="middle">{c.time}</text>}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function SimulateView({ sim }) {
  const [showTable, setShowTable] = useState(false);
  const an = sim.analysis;
  const t = sim.trade;
  const hhmm = (s) => (s ? String(s).slice(-5) : null);   // "11-Aug 09:35" → "09:35"
  const boHHMM = hhmm(t?.breakout_time);
  const exHHMM = hhmm(t?.exit_time);
  return (
    <div className="space-y-4">
      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
          <span className="text-gray-100 font-semibold">{sim.symbol} · {sim.date}</span>
          {an && <>
            <span className="text-gray-400">First 3: {an.colors.map((c, i) => <span key={i} className={c === 'green' ? 'text-emerald-400' : c === 'red' ? 'text-red-400' : 'text-gray-500'}>{c === 'green' ? '🟢' : c === 'red' ? '🔴' : '⚪'}</span>)}</span>
            <span className="text-gray-400">Bias <strong className={an.bias === 'call' ? 'text-emerald-400' : an.bias === 'put' ? 'text-red-400' : 'text-gray-500'}>{an.bias ? an.bias.toUpperCase() : 'NO TRADE (mixed)'}</strong></span>
            <span className="text-gray-400">4th High <strong className="text-gray-200">{NUM(an.fourth_high)}</strong></span>
            <span className="text-gray-400">4th Low <strong className="text-gray-200">{NUM(an.fourth_low)}</strong></span>
          </>}
        </div>
      </div>

      {/* candlestick chart */}
      {an && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-3">
          <div className="flex items-center justify-between mb-2 px-1">
            <div className="text-sm font-semibold text-gray-200">{sim.symbol} · 5m</div>
            <div className="flex items-center gap-4 text-[11px]">
              <span className="flex items-center gap-1 text-gray-400"><span className="inline-block w-3 border-t border-dashed" style={{ borderColor: RED }} /> 4th High {NUM(an.fourth_high, 1)}</span>
              <span className="flex items-center gap-1 text-gray-400"><span className="inline-block w-3 border-t border-dashed" style={{ borderColor: GREEN }} /> 4th Low {NUM(an.fourth_low, 1)}</span>
              {boHHMM && <span className="text-gray-400">▲ Breakout <strong className={an.bias === 'call' ? 'text-emerald-400' : 'text-red-400'}>{boHHMM}</strong></span>}
            </div>
          </div>
          <CandleChart timeline={sim.timeline} fourthHigh={an.fourth_high} fourthLow={an.fourth_low}
                       breakoutHHMM={boHHMM} exitHHMM={exHHMM} bias={an.bias} />
        </div>
      )}

      {t && t.opt_type && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
          <div className="text-sm font-semibold text-gray-200 mb-3">Trade</div>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            <Tile label="Signal"><OptBadge t={t.opt_type} /> <span className="text-gray-500 text-xs">@ {t.breakout_time}</span></Tile>
            <Tile label="Option"><span className="text-gray-200">{t.symbol}</span></Tile>
            <Tile label="Entry"><span className="text-gray-100">₹{NUM(t.entry)}</span></Tile>
            <Tile label="Target / SL"><span className="text-emerald-400">₹{NUM(t.target)}</span> <span className="text-gray-600">/</span> <span className="text-red-400">₹{NUM(t.sl)}</span></Tile>
            <Tile label="Exit">{t.exit == null ? <span className="text-gray-400">—</span> : <span className="text-gray-100">₹{NUM(t.exit)}</span>} <span className={`text-xs ${stColor(t.exit_reason)}`}>({t.exit_reason})</span>{t.exit_time ? <span className="text-gray-500 text-xs"> @ {t.exit_time}</span> : null}</Tile>
            <Tile label="Hold"><span className="text-gray-200">{t.hold_label ?? (t.open ? 'open' : '—')}</span></Tile>
            <Tile label="MTM"><span className={`font-semibold ${t.mtm >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>₹{NUM(t.mtm, 0)}</span></Tile>
            <Tile label="Max Profit / Loss"><span className="text-emerald-400">₹{NUM(t.max_profit, 0)}</span> <span className="text-gray-600">/</span> <span className="text-red-400">₹{NUM(t.max_loss, 0)}</span></Tile>
          </div>
        </div>
      )}
      {(!t || !t.opt_type) && <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 text-sm text-gray-400">{t?.status === 'NO OPTION DATA' ? `Breakout at ${boHHMM} but no option price — ${t.notes}` : an && !an.bias ? 'No trade — first 3 candles were mixed.' : 'No breakout of the 4th-candle level during the day.'}</div>}

      <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
        <button onClick={() => setShowTable((v) => !v)} className="w-full px-3 py-2 flex items-center justify-between text-sm font-semibold text-gray-300 hover:text-white">
          <span>5-min candle table</span><span className="text-xs text-gray-500">{showTable ? 'Hide ▲' : 'Show ▼'}</span>
        </button>
        {showTable && (
          <div className="overflow-x-auto max-h-[360px] border-t border-surface-3"><table className="w-full text-xs whitespace-nowrap">
            <thead className="bg-surface-3 text-gray-400 sticky top-0"><tr>{['#', 'Time', 'Open', 'High', 'Low', 'Close', 'Color'].map((h) => <th key={h} className="px-2.5 py-1.5 text-right first:text-left font-semibold">{h}</th>)}</tr></thead>
            <tbody>{(sim.timeline || []).map((c, i) => (
              <tr key={i} className={`border-t border-surface-3/30 ${i === 3 ? 'bg-brand-600/10' : ''} ${c.time === boHHMM ? 'bg-emerald-600/10' : ''}`}>
                <td className="px-2.5 py-1 text-left text-gray-500">{i + 1}{i === 3 ? ' ◄4th' : ''}{c.time === boHHMM ? ' ◄break' : ''}</td>
                <td className="px-2.5 py-1 text-left text-gray-300">{c.time}</td>
                <td className="px-2.5 py-1 text-right text-gray-400">{NUM(c.open)}</td>
                <td className="px-2.5 py-1 text-right text-gray-400">{NUM(c.high)}</td>
                <td className="px-2.5 py-1 text-right text-gray-400">{NUM(c.low)}</td>
                <td className="px-2.5 py-1 text-right text-gray-200">{NUM(c.close)}</td>
                <td className={`px-2.5 py-1 text-right font-semibold ${c.color === 'green' ? 'text-emerald-400' : c.color === 'red' ? 'text-red-400' : 'text-gray-500'}`}>{c.color}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </div>
    </div>
  );
}
