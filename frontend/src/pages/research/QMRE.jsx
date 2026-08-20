import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Activity, Radio, Loader2, Play, Pause, StepForward, RotateCcw, Search, Download,
  Save, X, SlidersHorizontal, Wallet, FlaskConical, Gauge, ShieldCheck, TrendingUp, Info,
} from 'lucide-react';
import { api } from '../../api';
import WatchlistBar from '../../components/WatchlistBar';

const sel = 'bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';
const lbl = 'block text-[11px] text-gray-500 uppercase tracking-wide mb-1';
const NUM = (v, d = 2) => (v == null ? 'N/A' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
const INT = (v) => (v == null ? 'N/A' : Number(v).toLocaleString('en-IN'));
const PCT = (v, d = 2) => (v == null ? 'N/A' : `${v >= 0 ? '+' : ''}${Number(v).toFixed(d)}%`);
const clsColor = (c) => ({ 'A+': 'text-emerald-400', A: 'text-emerald-300', B: 'text-sky-300', WATCH: 'text-amber-400', 'NO TRADE': 'text-gray-500' }[c] || 'text-gray-400');
const clsBg = (c) => ({ 'A+': '#10b981', A: '#34d399', B: '#38bdf8', WATCH: '#f59e0b', 'NO TRADE': '#6b7280' }[c] || '#6b7280');
const pcolor = (v) => (v == null ? 'text-gray-500' : v >= 0 ? 'text-emerald-400' : 'text-red-400');
const COMP_LABELS = { market_regime: 'Market Regime', sector_strength: 'Sector Strength', price_trend: 'Price Trend', relative_strength: 'Relative Strength', volume: 'Volume / RVOL', breakout: 'Breakout', vwap: 'VWAP Structure', volatility: 'Volatility/ATR', liquidity: 'Liquidity', order_book: 'Order Book', risk_reward: 'Risk/Reward' };

function Bar({ value, max, color }) {
  const pct = max ? Math.min(100, Math.max(0, value) / max * 100) : 0;
  return <div className="h-2 rounded bg-surface-3 overflow-hidden"><div className="h-full rounded" style={{ width: `${pct}%`, background: color }} /></div>;
}

function EntryTag({ t }) {
  const m = { NOW: ['ENTER NOW', 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40'], BREAK: ['BUY-STOP ↑', 'bg-sky-500/15 text-sky-300 border-sky-500/40'], PULLBACK: ['PULLBACK ↓', 'bg-amber-500/15 text-amber-300 border-amber-500/40'] };
  const [label, cls] = m[t] || ['—', 'text-gray-500 border-surface-4'];
  return <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded border ${cls}`}>{label}</span>;
}

export default function QMRE() {
  const [tab, setTab] = useState('live');
  const [cfg, setCfg] = useState(null);
  const [universe, setUniverse] = useState([]);
  const [err, setErr] = useState(''); const [msg, setMsg] = useState('');
  const showErr = (m) => { setErr(m); setTimeout(() => setErr(''), 6000); };
  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(''), 2500); };

  useEffect(() => {
    api.qmreConfig().then((r) => { if (r.status === 'ok') setCfg(r.config); }).catch(() => setCfg({}));
    api.qmreUniverse().then((r) => { if (r.status === 'ok') setUniverse(r.symbols || []); }).catch(() => {});
  }, []);

  if (!cfg) return <div className="p-6 text-gray-500 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>;

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-[1700px] mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Activity className="w-6 h-6 text-brand-400" />
            <h1 className="text-xl font-bold text-gray-100">Momentum &amp; Market Replay Engine</h1>
            <span className="text-[11px] px-2 py-0.5 rounded-full bg-brand-500/15 text-brand-300 border border-brand-500/25">Research 13</span>
            <span className="text-[11px] px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/40 flex items-center gap-1"><ShieldCheck className="w-3 h-3" /> PAPER ONLY · NO REAL ORDERS</span>
          </div>
          <p className="text-sm text-gray-500 mt-0.5">Point-in-time momentum scanning, replay &amp; backtest on one look-ahead-safe engine. Signals are probabilistic research outputs — past performance does not guarantee future results.</p>
        </div>
      </div>

      <div className="flex items-center gap-1 border-b border-surface-3 overflow-x-auto">
        {[['live', 'Live', Radio], ['replay', 'Replay', Play], ['single', 'Single Stock', Search], ['backtest', 'Backtest', FlaskConical], ['portfolio', 'Paper Portfolio', Wallet], ['settings', 'Settings', SlidersHorizontal]].map(([id, label, Icon]) => (
          <button key={id} onClick={() => setTab(id)} className={`flex items-center gap-1.5 px-3 py-2 text-sm font-medium border-b-2 -mb-px whitespace-nowrap ${tab === id ? 'border-brand-500 text-brand-300' : 'border-transparent text-gray-400 hover:text-gray-200'}`}><Icon className="w-4 h-4" /> {label}</button>
        ))}
      </div>

      {err && <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2">{err}</div>}
      {msg && <div className="text-sm text-emerald-400 bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-3 py-2">{msg}</div>}

      {(tab === 'live' || tab === 'replay') && <ScanTab mode={tab} cfg={cfg} universe={universe} showErr={showErr} flash={flash} />}
      {tab === 'single' && <SingleTab cfg={cfg} showErr={showErr} />}
      {tab === 'backtest' && <BacktestTab cfg={cfg} universe={universe} showErr={showErr} />}
      {tab === 'portfolio' && <PortfolioTab showErr={showErr} flash={flash} />}
      {tab === 'settings' && <SettingsTab cfg={cfg} setCfg={setCfg} showErr={showErr} flash={flash} />}
    </div>
  );
}

function RegimeBanner({ market }) {
  if (!market) return null;
  const bull = (market.regime_score || 0) > 0.1; const bear = (market.regime_score || 0) < -0.1;
  const c = bull ? 'text-emerald-400 border-emerald-500/40 bg-emerald-500/10' : bear ? 'text-red-400 border-red-500/40 bg-red-500/10' : 'text-amber-400 border-amber-500/40 bg-amber-500/10';
  return (
    <div className={`flex flex-wrap items-center gap-4 rounded-xl border px-4 py-2.5 text-sm ${c}`}>
      <span className="flex items-center gap-1.5 font-bold"><Gauge className="w-4 h-4" /> Market Regime: {market.regime_label || 'NEUTRAL'}</span>
      <span className="text-gray-400">Score <strong>{market.regime_score}</strong></span>
      <span className="text-gray-400">NIFTY today <strong className={pcolor(market.day_change_pct)}>{PCT(market.day_change_pct)}</strong></span>
      <span className="text-gray-400">Benchmark→now <strong className={pcolor(market.bench_ret_pct)}>{PCT(market.bench_ret_pct)}</strong></span>
      {!market.regime_available && <span className="text-[11px] text-gray-500">(regime unavailable in replay)</span>}
    </div>
  );
}

function ScanTab({ mode, cfg, universe, showErr, flash }) {
  const [selU, setSelU] = useState({ mode: 'all', symbol: null, symbols: null });
  const [topN, setTopN] = useState(cfg.top_n || 5);
  const [data, setData] = useState(null); const [loading, setLoading] = useState(false);
  const [auto, setAuto] = useState(mode === 'live');
  const [detail, setDetail] = useState(null);
  const [rdate, setRdate] = useState(''); const [rtime, setRtime] = useState('09:45');
  const pollRef = useRef(null);
  const inFlight = useRef(false);
  const abortRef = useRef(null);

  useEffect(() => { const t = new Date().toISOString().slice(0, 10); setRdate(t); }, []);

  const cancel = () => {
    if (abortRef.current) abortRef.current.abort();
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    setAuto(false); inFlight.current = false; setLoading(false);
  };

  const run = useCallback(async (silent = false) => {
    if (inFlight.current) return;              // never overlap scans (avoids pile-up)
    inFlight.current = true;
    const ac = new AbortController(); abortRef.current = ac;
    if (!silent) setLoading(true);
    try {
      const body = { top_n: topN, symbols: selU.mode === 'single' ? (selU.symbol ? [selU.symbol] : null) : selU.mode === 'watchlist' ? selU.symbols : null };
      if (mode === 'replay') { body.date = rdate; body.at_time = rtime; }
      const r = await api.qmreScan(body, ac.signal);
      if (r.status === 'ok') setData(r); else if (!silent) showErr(r.message || 'Scan failed');
    } catch (e) { if (e.name !== 'AbortError' && !silent) showErr(e.message); } finally { inFlight.current = false; if (!silent) setLoading(false); }
  }, [mode, topN, selU, rdate, rtime]); // eslint-disable-line

  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (mode !== 'live' || !auto) return undefined;
    run(false);
    pollRef.current = setInterval(() => run(true), Math.max(5, cfg.update_interval || 20) * 1000);
    return () => pollRef.current && clearInterval(pollRef.current);
  }, [mode, auto, selU, topN]); // eslint-disable-line

  const step = (deltaMin) => {
    const [h, m] = rtime.split(':').map(Number);
    let t = h * 60 + m + deltaMin; t = Math.max(555, Math.min(930, t));
    setRtime(`${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`);
  };
  useEffect(() => { if (mode === 'replay' && rdate) run(false); }, [rtime]); // eslint-disable-line

  const openPaper = async (c) => {
    const r = await api.qmrePaperOpen({ symbol: c.symbol, qty: c.sizing.qty, entry: c.risk.entry, sl: c.risk.sl, target: c.risk.target1, mode: cfg.mode, note: `${c.class} score ${c.score}` });
    if (r.status === 'ok') flash(`Paper position opened: ${c.symbol}`); else showErr(r.message);
  };

  return (
    <div className="space-y-4">
      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
        <WatchlistBar universe={universe} count={universe.length} onChange={setSelU} />
        <div className="flex flex-wrap items-center gap-3">
          {mode === 'replay' && (<>
            <div><label className={lbl}>Date</label><input type="date" value={rdate} onChange={(e) => setRdate(e.target.value)} className={sel} /></div>
            <div><label className={lbl}>Time (cutoff)</label><input value={rtime} onChange={(e) => setRtime(e.target.value)} className={`${sel} w-24`} /></div>
            <div className="flex items-center gap-1 self-end">
              <button onClick={() => step(-5)} className="p-1.5 rounded-lg bg-surface-3 border border-surface-4 text-gray-300 hover:text-white" title="Step back 5m"><RotateCcw className="w-4 h-4" /></button>
              <button onClick={() => step(5)} className="p-1.5 rounded-lg bg-surface-3 border border-surface-4 text-gray-300 hover:text-white" title="Step forward 5m"><StepForward className="w-4 h-4" /></button>
              <button onClick={() => setRtime('09:45')} className="p-1.5 rounded-lg bg-surface-3 border border-surface-4 text-gray-300 hover:text-white" title="Reset to 09:45"><Pause className="w-4 h-4" /></button>
            </div>
          </>)}
          <div><label className={lbl}>Show</label>
            <select value={topN} onChange={(e) => setTopN(Number(e.target.value))} className={sel}>{[1, 3, 5, 10, 20, 50, 999].map((n) => <option key={n} value={n}>{n === 999 ? 'All' : `Top ${n}`}</option>)}</select>
          </div>
          <button onClick={() => run(false)} disabled={loading} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50 self-end">{loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Radio className="w-4 h-4" />} Scan</button>
          {loading && <button onClick={cancel} className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg bg-red-600/80 hover:bg-red-600 text-white font-semibold self-end"><X className="w-4 h-4" /> Cancel</button>}
          {mode === 'live' && <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer self-end"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} className="accent-brand-500" /> Auto-refresh</label>}
          {data && (
            <span className="text-xs self-end ml-auto flex items-center gap-2">
              {data.stale && <span className="px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/40 font-semibold">LAST SESSION {data.date}</span>}
              <span className="text-gray-500">Scanned {data.scanned} · cutoff {data.cutoff} · v{data.config_version}</span>
            </span>
          )}
        </div>
      </div>

      {data && <RegimeBanner market={data.market} />}

      {data && (
        <div className="flex flex-wrap gap-2 text-xs">
          {['A+', 'A', 'B', 'WATCH', 'NO TRADE'].map((k) => (
            <span key={k} className="px-2 py-1 rounded-lg bg-surface-2 border border-surface-3"><strong className={clsColor(k)}>{k}</strong> <span className="text-gray-400">{data.counts?.[k] || 0}</span></span>
          ))}
          {data.scanned === 0
            ? <span className="px-2 py-1 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300">No candles for this session — the market may not have traded yet. Try Replay with a past trading date.</span>
            : !(data.top || []).some((c) => c.class === 'A+' || c.class === 'A')
              ? <span className="px-2 py-1 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-300">No A+/A setup right now — best candidates shown below.</span>
              : null}
        </div>
      )}

      {data && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
          <div className="overflow-x-auto"><table className="w-full text-xs whitespace-nowrap">
            <thead className="bg-surface-3 text-gray-300"><tr>{['#', 'Symbol', 'LTP', 'Chg%', 'RVOL', 'RS', 'Score', 'Class', 'Type', 'Entry', 'SL', 'Target', 'RR', 'Qty', ''].map((h, i) => <th key={h + i} className={`px-2.5 py-2 font-semibold ${i < 2 ? 'text-left' : i === 8 ? 'text-center' : 'text-right'}`}>{h}</th>)}</tr></thead>
            <tbody>{(data.top || []).map((c) => (
              <tr key={c.symbol} className="border-t border-surface-3/40 hover:bg-surface-3/20">
                <td className="px-2.5 py-1.5 text-left text-gray-500">{c.rank}</td>
                <td className="px-2.5 py-1.5 text-left"><button onClick={() => setDetail(c)} className="text-brand-300 font-semibold hover:underline" title="Full score breakdown & entry plan">{c.symbol}</button></td>
                <td className="px-2.5 py-1.5 text-right text-gray-200">₹{NUM(c.features.ltp)}</td>
                <td className={`px-2.5 py-1.5 text-right ${pcolor(c.features.change_pct)}`}>{PCT(c.features.change_pct)}</td>
                <td className="px-2.5 py-1.5 text-right text-gray-300">{c.features.rvol == null ? 'N/A' : `${c.features.rvol}x`}</td>
                <td className={`px-2.5 py-1.5 text-right ${pcolor(c.features.rs)}`}>{PCT(c.features.rs, 1)}</td>
                <td className="px-2.5 py-1.5 text-right"><span className="font-bold" style={{ color: clsBg(c.class) }}>{c.score}</span></td>
                <td className={`px-2.5 py-1.5 text-right font-bold ${clsColor(c.class)}`}>{c.class}</td>
                <td className="px-2.5 py-1.5 text-center"><EntryTag t={c.risk.entry_type} /></td>
                <td className="px-2.5 py-1.5 text-right text-gray-100 font-medium" title={c.risk.entry_note}>₹{NUM(c.risk.entry)}</td>
                <td className="px-2.5 py-1.5 text-right text-red-400">₹{NUM(c.risk.sl)}</td>
                <td className="px-2.5 py-1.5 text-right text-emerald-400">₹{NUM(c.risk.target1)}</td>
                <td className={`px-2.5 py-1.5 text-right ${c.risk.poor_rr ? 'text-amber-400' : 'text-gray-300'}`}>{c.risk.rr}</td>
                <td className="px-2.5 py-1.5 text-right text-gray-400">{INT(c.sizing.qty)}</td>
                <td className="px-2.5 py-1.5 text-right"><button onClick={() => openPaper(c)} className="px-2 py-0.5 text-[11px] rounded bg-brand-600/80 hover:bg-brand-600 text-white" title="Open paper position (simulation only)">Paper Buy</button></td>
              </tr>
            ))}
            {!(data.top || []).length && <tr><td colSpan={15} className="px-4 py-8 text-center text-gray-500">No candidates.</td></tr>}
            </tbody>
          </table></div>
        </div>
      )}

      {detail && <ForensicDrawer c={detail} onClose={() => setDetail(null)} onPaper={openPaper} />}
      <Disclaimer />
    </div>
  );
}

function ForensicDrawer({ c, onClose, onPaper }) {
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-3 overflow-y-auto" onClick={onClose}>
      <div className="bg-surface-1 border border-surface-3 rounded-xl w-full max-w-3xl my-6" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-surface-3 sticky top-0 bg-surface-1 rounded-t-xl">
          <div className="flex items-center gap-2"><span className="text-lg font-bold text-gray-100">{c.symbol}</span><span className={`font-bold ${clsColor(c.class)}`}>{c.class} · {c.score}/100</span></div>
          <button onClick={onClose} className="text-gray-500 hover:text-white"><X className="w-5 h-5" /></button>
        </div>
        <div className="p-4 space-y-4">
          <div className="text-sm text-gray-400">Why this signal: <span className="text-gray-200">{c.signal_reason}</span></div>
          <div className="bg-surface-2 border border-surface-3 rounded-lg p-3 flex flex-wrap items-center gap-3">
            <EntryTag t={c.risk.entry_type} />
            <span className="text-sm text-gray-200">{c.risk.entry_note}</span>
            <span className="text-xs text-gray-500 ml-auto">Entry zone ₹{NUM(c.risk.entry_low)} – ₹{NUM(c.risk.entry_high)} · {c.risk.ext_atr} ATR from VWAP · entry quality {Math.round((c.risk.entry_quality || 0) * 100)}%</span>
          </div>
          <div className="bg-surface-2 border border-surface-3 rounded-lg p-3">
            <div className="text-sm font-semibold text-gray-200 mb-2">Score Breakdown</div>
            <div className="space-y-1.5 text-xs">
              {Object.entries(c.breakdown || {}).map(([k, b]) => (
                <div key={k}><div className="flex justify-between text-gray-400"><span>{COMP_LABELS[k] || k}</span><span className="text-gray-200">{b.points} / {b.max}</span></div><Bar value={b.points} max={b.max} color={clsBg(c.class)} /></div>
              ))}
              <div className="flex justify-between pt-2 mt-1 border-t border-surface-3 font-bold text-gray-100"><span>TOTAL</span><span style={{ color: clsBg(c.class) }}>{c.score} / 100</span></div>
            </div>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {[['Entry', `₹${NUM(c.risk.entry)}`], ['Stop', `₹${NUM(c.risk.sl)}`], ['Target 1', `₹${NUM(c.risk.target1)}`], ['Target 2', `₹${NUM(c.risk.target2)}`], ['R:R', c.risk.rr], ['Qty', INT(c.sizing.qty)], ['Capital', `₹${NUM(c.sizing.capital_used, 0)}`], ['Risk ₹', `₹${NUM(c.sizing.risk_amount, 0)}`]].map(([k, v]) => (
              <div key={k} className="bg-surface-3/40 border border-surface-3 rounded-lg px-3 py-2"><div className="text-[10px] uppercase text-gray-500">{k}</div><div className="text-sm text-gray-200">{v}</div></div>
            ))}
          </div>
          {c.risk.poor_rr && <div className="text-xs text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded px-3 py-2">⚠ Risk/reward below your minimum — flagged as a poor setup.</div>}
          <button onClick={() => { onPaper(c); onClose(); }} className="px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold">Open Paper Position (simulation)</button>
        </div>
      </div>
    </div>
  );
}

function SingleTab({ cfg, showErr }) {
  const [sym, setSym] = useState(''); const [date, setDate] = useState(''); const [time, setTime] = useState('09:45');
  const [data, setData] = useState(null); const [loading, setLoading] = useState(false);
  const abortRef = useRef(null);
  useEffect(() => { setDate(new Date().toISOString().slice(0, 10)); }, []);
  const cancel = () => { if (abortRef.current) abortRef.current.abort(); setLoading(false); };
  const run = async () => {
    if (!sym.trim()) return showErr('Enter a symbol');
    const ac = new AbortController(); abortRef.current = ac; setLoading(true);
    try { const r = await api.qmreSingle({ symbol: sym.trim().toUpperCase(), date, at_time: time }, ac.signal); if (r.status === 'ok') setData(r); else showErr(r.message); }
    catch (e) { if (e.name !== 'AbortError') showErr(e.message); } finally { setLoading(false); }
  };
  const c = data?.candidate;
  return (
    <div className="space-y-4">
      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 flex flex-wrap items-end gap-3">
        <div><label className={lbl}>Symbol</label><input value={sym} onChange={(e) => setSym(e.target.value.toUpperCase())} placeholder="RELIANCE" className={sel} /></div>
        <div><label className={lbl}>Date</label><input type="date" value={date} onChange={(e) => setDate(e.target.value)} className={sel} /></div>
        <div><label className={lbl}>Time</label><input value={time} onChange={(e) => setTime(e.target.value)} className={`${sel} w-24`} /></div>
        <button onClick={run} disabled={loading} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">{loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />} Analyze</button>
        {loading && <button onClick={cancel} className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg bg-red-600/80 hover:bg-red-600 text-white font-semibold"><X className="w-4 h-4" /> Cancel</button>}
      </div>
      {data && c && (
        <>
          <RegimeBanner market={data.market} />
          <div className="flex items-center gap-3"><span className="text-lg font-bold text-gray-100">{data.symbol}</span><span className={`font-bold ${clsColor(c.class)}`}>{c.class} · {c.score}/100</span><span className="text-sm text-gray-500">{c.signal_reason}</span></div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="bg-surface-2 border border-surface-3 rounded-lg p-3">
              <div className="text-sm font-semibold text-gray-200 mb-2">Score Breakdown — why the system {c.score >= 60 ? 'liked' : 'passed on'} it</div>
              <div className="space-y-1.5 text-xs">{Object.entries(c.breakdown || {}).map(([k, b]) => (<div key={k}><div className="flex justify-between text-gray-400"><span>{COMP_LABELS[k] || k}</span><span className="text-gray-200">{b.points} / {b.max}</span></div><Bar value={b.points} max={b.max} color={clsBg(c.class)} /></div>))}</div>
            </div>
            <div className="bg-surface-2 border border-surface-3 rounded-lg p-3">
              <div className="text-sm font-semibold text-gray-200 mb-2">What happened after {data.at_time}</div>
              <table className="w-full text-xs"><thead className="text-gray-400"><tr>{['Horizon', 'Price', 'Return', 'MFE', 'MAE'].map((h, i) => <th key={h} className={`py-1 ${i === 0 ? 'text-left' : 'text-right'}`}>{h}</th>)}</tr></thead>
                <tbody>{(data.outcomes || []).map((o) => (<tr key={o.t} className="border-t border-surface-3/30"><td className="py-1 text-left text-gray-300">{o.t}</td><td className="py-1 text-right text-gray-200">₹{NUM(o.price)}</td><td className={`py-1 text-right ${pcolor(o.ret_pct)}`}>{PCT(o.ret_pct)}</td><td className="py-1 text-right text-emerald-400">{PCT(o.mfe_pct)}</td><td className="py-1 text-right text-red-400">{PCT(o.mae_pct)}</td></tr>))}</tbody>
              </table>
              <div className="text-[10px] text-gray-600 mt-2">Look-ahead safe: the score used only data up to {data.at_time}; outcomes are shown separately for evaluation.</div>
            </div>
          </div>
        </>
      )}
      <Disclaimer />
    </div>
  );
}

function BacktestTab({ cfg, universe, showErr }) {
  const [selU, setSelU] = useState({ mode: 'watchlist', symbol: null, symbols: null });
  const [start, setStart] = useState(''); const [end, setEnd] = useState('');
  const [data, setData] = useState(null); const [loading, setLoading] = useState(false);
  const abortRef = useRef(null);
  useEffect(() => { const t = new Date().toISOString().slice(0, 10); setStart(t); setEnd(t); }, []);
  const cancel = () => { if (abortRef.current) abortRef.current.abort(); setLoading(false); };
  const run = async () => {
    const ac = new AbortController(); abortRef.current = ac; setLoading(true);
    try {
      const body = { start, end, symbols: selU.mode === 'single' ? (selU.symbol ? [selU.symbol] : null) : selU.mode === 'watchlist' ? selU.symbols : null };
      const r = await api.qmreBacktest(body, ac.signal);
      if (r.status === 'ok') setData(r); else showErr(r.message);
    } catch (e) { if (e.name !== 'AbortError') showErr(e.message); } finally { setLoading(false); }
  };
  const s = data?.stats;
  const exportCSV = () => {
    if (!data?.trades?.length) return;
    const cols = ['date', 'symbol', 'entry_time', 'class', 'score', 'regime', 'entry', 'sl', 'target', 'rr', 'qty', 'exit', 'exit_time', 'exit_reason', 'mtm', 'mfe', 'mae', 'rvol'];
    const esc = (v) => { const x = v == null ? '' : String(v); return /[",\n]/.test(x) ? `"${x.replace(/"/g, '""')}"` : x; };
    const lines = [cols.join(',')].concat(data.trades.map((t) => cols.map((c) => esc(t[c])).join(',')));
    const blob = new Blob([lines.join('\n') + '\n'], { type: 'text/csv' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'qmre_backtest.csv'; a.click();
  };
  return (
    <div className="space-y-4">
      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
        <WatchlistBar universe={universe} count={universe.length} onChange={setSelU} />
        <div className="flex flex-wrap items-end gap-3">
          <div><label className={lbl}>From</label><input type="date" value={start} onChange={(e) => setStart(e.target.value)} className={sel} /></div>
          <div><label className={lbl}>To</label><input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className={sel} /></div>
          <button onClick={run} disabled={loading} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">{loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <FlaskConical className="w-4 h-4" />} Run Backtest</button>
          {loading && <button onClick={cancel} className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg bg-red-600/80 hover:bg-red-600 text-white font-semibold"><X className="w-4 h-4" /> Cancel</button>}
          <span className="text-[11px] text-gray-600">One paper trade per stock per day, entered at the first A+/A cutoff · costs &amp; slippage applied · no look-ahead.</span>
        </div>
      </div>
      {s && (
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
          {[['Trades', s.trades, 'text-gray-100'], ['Win%', `${s.win_rate}%`, 'text-emerald-400'], ['Total P&L', `₹${NUM(s.total_mtm, 0)}`, s.total_mtm >= 0 ? 'text-emerald-400' : 'text-red-400'], ['Profit Factor', s.profit_factor, 'text-sky-300'], ['Expectancy', `₹${NUM(s.expectancy, 0)}`, s.expectancy >= 0 ? 'text-emerald-400' : 'text-red-400'], ['Max DD', `₹${NUM(s.max_drawdown, 0)}`, 'text-red-400'], ['Avg Win', `₹${NUM(s.avg_win, 0)}`, 'text-emerald-400'], ['Avg Loss', `₹${NUM(s.avg_loss, 0)}`, 'text-red-400'], ['Best', `₹${NUM(s.best, 0)}`, 'text-emerald-400'], ['Worst', `₹${NUM(s.worst, 0)}`, 'text-red-400'], ['Return', `${s.return_pct}%`, s.return_pct >= 0 ? 'text-emerald-400' : 'text-red-400'], ['Days', data.days, 'text-gray-300']].map(([k, v, c]) => (
            <div key={k} className="bg-surface-2 border border-surface-3 rounded-xl px-3 py-2"><div className="text-[10px] uppercase tracking-wide text-gray-500">{k}</div><div className={`text-base font-bold ${c}`}>{v}</div></div>
          ))}
        </div>
      )}
      {data && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
          <div className="px-3 py-2 border-b border-surface-3 flex items-center justify-between"><span className="text-sm font-semibold text-gray-200">Trades ({data.trades.length})</span><button onClick={exportCSV} disabled={!data.trades.length} className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-40"><Download className="w-3.5 h-3.5" /> CSV</button></div>
          <div className="overflow-x-auto max-h-[520px]"><table className="w-full text-xs whitespace-nowrap">
            <thead className="bg-surface-3 text-gray-300 sticky top-0"><tr>{['Date', 'Symbol', 'Entry@', 'Class', 'Score', 'Regime', 'Entry', 'SL', 'Target', 'RR', 'Qty', 'Exit', 'Exit@', 'Reason', 'P&L', 'MFE', 'MAE'].map((h, i) => <th key={h} className={`px-2.5 py-1.5 font-semibold ${i < 2 ? 'text-left' : 'text-right'}`}>{h}</th>)}</tr></thead>
            <tbody>{data.trades.map((t, i) => (
              <tr key={i} className="border-t border-surface-3/30">
                <td className="px-2.5 py-1 text-left text-gray-400">{t.date}</td>
                <td className="px-2.5 py-1 text-left text-brand-300 font-semibold">{t.symbol}</td>
                <td className="px-2.5 py-1 text-right text-gray-400">{t.entry_time}</td>
                <td className={`px-2.5 py-1 text-right font-semibold ${clsColor(t.class)}`}>{t.class}</td>
                <td className="px-2.5 py-1 text-right text-gray-300">{t.score}</td>
                <td className="px-2.5 py-1 text-right text-gray-500">{t.regime}</td>
                <td className="px-2.5 py-1 text-right text-gray-200">₹{NUM(t.entry)}</td>
                <td className="px-2.5 py-1 text-right text-red-400">₹{NUM(t.sl)}</td>
                <td className="px-2.5 py-1 text-right text-emerald-400">₹{NUM(t.target)}</td>
                <td className="px-2.5 py-1 text-right text-gray-300">{t.rr}</td>
                <td className="px-2.5 py-1 text-right text-gray-300">{INT(t.qty)}</td>
                <td className="px-2.5 py-1 text-right text-gray-300">{t.exit == null ? '—' : `₹${NUM(t.exit)}`}</td>
                <td className="px-2.5 py-1 text-right text-gray-500">{t.exit_time || '—'}</td>
                <td className="px-2.5 py-1 text-right text-gray-400">{t.exit_reason}</td>
                <td className={`px-2.5 py-1 text-right font-semibold ${(t.mtm || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{NUM(t.mtm, 0)}</td>
                <td className="px-2.5 py-1 text-right text-emerald-400">{NUM(t.mfe, 0)}</td>
                <td className="px-2.5 py-1 text-right text-red-400">{NUM(t.mae, 0)}</td>
              </tr>
            ))}
            {!data.trades.length && <tr><td colSpan={17} className="px-4 py-8 text-center text-gray-500">No qualifying signals in range.</td></tr>}
            </tbody>
          </table></div>
        </div>
      )}
      <Disclaimer />
    </div>
  );
}

function PortfolioTab({ showErr, flash }) {
  const [p, setP] = useState(null); const [loading, setLoading] = useState(false);
  const load = useCallback(async () => { setLoading(true); try { const r = await api.qmrePortfolio(); if (r.status === 'ok') setP(r.portfolio); else showErr(r.message); } catch (e) { showErr(e.message); } finally { setLoading(false); } }, []); // eslint-disable-line
  useEffect(() => { load(); const t = setInterval(load, 8000); return () => clearInterval(t); }, [load]);
  const close = async (id) => { const r = await api.qmrePaperClose(id); if (r.status === 'ok') { flash('Closed'); load(); } else showErr(r.message); };
  if (!p) return <div className="p-6 text-gray-500 flex items-center gap-2">{loading ? <Loader2 className="w-4 h-4 animate-spin" /> : null} Loading paper portfolio…</div>;
  const rows = [...(p.positions_open || []), ...(p.positions_closed || [])];
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
        {[['Total P&L', `₹${NUM(p.total_pnl, 0)}`, p.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'], ['Realized', `₹${NUM(p.realized, 0)}`, p.realized >= 0 ? 'text-emerald-400' : 'text-red-400'], ['Unrealized', `₹${NUM(p.unrealized, 0)}`, p.unrealized >= 0 ? 'text-emerald-400' : 'text-red-400'], ['Return', `${p.return_pct}%`, p.return_pct >= 0 ? 'text-emerald-400' : 'text-red-400'], ['Deployed', `₹${NUM(p.deployed, 0)}`, 'text-gray-200'], ['Available', `₹${NUM(p.available, 0)}`, 'text-gray-200'], ['Open', p.open, 'text-amber-400'], ['Closed', p.closed, 'text-gray-300'], ['Win%', `${p.win_rate}%`, 'text-emerald-400'], ['Profit Factor', p.profit_factor, 'text-sky-300'], ['Avg Win', `₹${NUM(p.avg_win, 0)}`, 'text-emerald-400'], ['Avg Loss', `₹${NUM(p.avg_loss, 0)}`, 'text-red-400']].map(([k, v, c]) => (
          <div key={k} className="bg-surface-2 border border-surface-3 rounded-xl px-3 py-2"><div className="text-[10px] uppercase tracking-wide text-gray-500">{k}</div><div className={`text-base font-bold ${c}`}>{v}</div></div>
        ))}
      </div>
      <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
        <div className="px-3 py-2 border-b border-surface-3 text-sm font-semibold text-gray-200">Paper Positions <span className="text-gray-500">({rows.length})</span></div>
        <div className="overflow-x-auto"><table className="w-full text-xs whitespace-nowrap">
          <thead className="bg-surface-3 text-gray-300"><tr>{['Symbol', 'Dir', 'Mode', 'Qty', 'Entry@', 'Entry', 'SL', 'Target', 'LTP', 'P&L', 'MFE', 'MAE', 'Status', 'Class', ''].map((h, i) => <th key={h + i} className={`px-2.5 py-2 font-semibold ${i === 0 ? 'text-left' : 'text-right'}`}>{h}</th>)}</tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id} className="border-t border-surface-3/40">
              <td className="px-2.5 py-1.5 text-left text-brand-300 font-semibold">{r.symbol}</td>
              <td className="px-2.5 py-1.5 text-right text-gray-300">{r.direction}</td>
              <td className="px-2.5 py-1.5 text-right text-gray-400">{r.mode}</td>
              <td className="px-2.5 py-1.5 text-right text-gray-300">{INT(r.qty)}</td>
              <td className="px-2.5 py-1.5 text-right text-gray-500">{r.entry_time}</td>
              <td className="px-2.5 py-1.5 text-right text-gray-200">₹{NUM(r.entry_price)}</td>
              <td className="px-2.5 py-1.5 text-right text-red-400">₹{NUM(r.sl)}</td>
              <td className="px-2.5 py-1.5 text-right text-emerald-400">₹{NUM(r.target)}</td>
              <td className="px-2.5 py-1.5 text-right text-gray-200">₹{NUM(r.ltp)}</td>
              <td className={`px-2.5 py-1.5 text-right font-semibold ${(r.mtm || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{NUM(r.mtm, 0)}</td>
              <td className="px-2.5 py-1.5 text-right text-emerald-400">{NUM(r.mfe, 0)}</td>
              <td className="px-2.5 py-1.5 text-right text-red-400">{NUM(r.mae, 0)}</td>
              <td className={`px-2.5 py-1.5 text-right font-semibold ${r.status === 'OPEN' ? 'text-amber-400' : r.status === 'TARGET' ? 'text-emerald-400' : r.status === 'STOP' ? 'text-red-400' : 'text-gray-400'}`}>{r.status}</td>
              <td className={`px-2.5 py-1.5 text-right ${clsColor(r.signal_class)}`}>{r.signal_class || '—'}</td>
              <td className="px-2.5 py-1.5 text-right">{r.status === 'OPEN' && <button onClick={() => close(r.id)} className="px-2 py-0.5 text-[11px] rounded bg-surface-3 border border-surface-4 text-gray-300 hover:text-white">Close</button>}</td>
            </tr>
          ))}
          {!rows.length && <tr><td colSpan={15} className="px-4 py-8 text-center text-gray-500">No paper positions yet — open one from Live/Replay or Single Stock.</td></tr>}
          </tbody>
        </table></div>
      </div>
      <Disclaimer />
    </div>
  );
}

function SettingsTab({ cfg, setCfg, showErr, flash }) {
  const [local, setLocal] = useState(cfg);
  const patch = (k, v) => setLocal((c) => ({ ...c, [k]: v }));
  const patchW = (k, v) => setLocal((c) => ({ ...c, weights: { ...(c.weights || {}), [k]: v } }));
  const save = async () => { const r = await api.qmreConfigSave(local); if (r.status === 'ok') { setLocal(r.config); setCfg(r.config); flash('Settings saved (durable)'); } else showErr(r.message); };
  const num = (k, step = 1) => <input type="number" step={step} value={local[k] ?? ''} onChange={(e) => patch(k, e.target.value === '' ? '' : Number(e.target.value))} className={`w-full ${sel}`} />;
  const w = local.weights || {}; const total = Object.values(w).reduce((a, b) => a + Number(b || 0), 0);
  return (
    <div className="space-y-5 max-w-5xl">
      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
        <div className="flex items-center justify-between mb-2"><h3 className="text-sm font-semibold text-gray-200">Scoring Weights (100 pts)</h3><span className={`text-xs ${total === 100 ? 'text-emerald-400' : 'text-amber-400'}`}>Total {total} · normalised at runtime</span></div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          {Object.keys(COMP_LABELS).map((k) => (<div key={k}><label className={lbl}>{COMP_LABELS[k]}</label><input type="number" value={w[k] ?? ''} onChange={(e) => patchW(k, e.target.value === '' ? '' : Number(e.target.value))} className={`w-full ${sel}`} /></div>))}
        </div>
      </div>
      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
        <h3 className="text-sm font-semibold text-gray-200 mb-2">Risk / Targets / Sizing</h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          <div><label className={lbl}>SL Mode</label><select value={local.sl_mode} onChange={(e) => patch('sl_mode', e.target.value)} className={`w-full ${sel}`}><option value="atr">ATR</option><option value="percent">Percent</option><option value="structure">Structure</option></select></div>
          <div><label className={lbl}>SL Value</label>{num('sl_value', 0.1)}</div>
          <div><label className={lbl}>Target Mode</label><select value={local.target_mode} onChange={(e) => patch('target_mode', e.target.value)} className={`w-full ${sel}`}><option value="atr">ATR</option><option value="percent">Percent</option><option value="rr">R multiple</option></select></div>
          <div><label className={lbl}>Target Value</label>{num('target_value', 0.1)}</div>
          <div><label className={lbl}>Min R:R</label>{num('min_rr', 0.1)}</div>
          <div><label className={lbl}>Capital / Stock ₹</label>{num('capital_per_stock', 1000)}</div>
          <div><label className={lbl}>Starting Capital ₹</label>{num('starting_capital', 10000)}</div>
          <div><label className={lbl}>Mode</label><select value={local.mode} onChange={(e) => patch('mode', e.target.value)} className={`w-full ${sel}`}><option value="intraday">Intraday</option><option value="swing">Swing/CNC</option></select></div>
          <div><label className={lbl}>EOD Exit</label><input value={local.eod_exit_time} onChange={(e) => patch('eod_exit_time', e.target.value)} className={`w-full ${sel}`} /></div>
          <div><label className={lbl}>Opening Range (min)</label><select value={local.opening_range_min} onChange={(e) => patch('opening_range_min', Number(e.target.value))} className={`w-full ${sel}`}>{[5, 10, 15, 30].map((n) => <option key={n} value={n}>{n}</option>)}</select></div>
          <div><label className={lbl}>Breakout RVOL min</label>{num('breakout_rvol_min', 0.1)}</div>
          <div><label className={lbl}>Max Trades/Day</label>{num('max_trades_per_day', 1)}</div>
        </div>
      </div>
      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
        <h3 className="text-sm font-semibold text-gray-200 mb-2">Universe · Costs · Telegram</h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          <div><label className={lbl}>Min Price ₹</label>{num('min_price', 1)}</div>
          <div><label className={lbl}>Min Liquidity (₹cr)</label>{num('min_avg_value_cr', 0.5)}</div>
          <div><label className={lbl}>Max Stocks (0=all)</label>{num('max_stocks', 1)}</div>
          <div><label className={lbl}>Top N</label>{num('top_n', 1)}</div>
          <div><label className={lbl}>Slippage (bps)</label>{num('slippage_bps', 1)}</div>
          <div><label className={lbl}>Charges %</label>{num('charges_pct', 0.01)}</div>
          <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer self-end"><input type="checkbox" checked={!!local.apply_costs} onChange={(e) => patch('apply_costs', e.target.checked)} className="accent-brand-500" /> Apply costs</label>
          <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer self-end"><input type="checkbox" checked={!!local.telegram_alerts} onChange={(e) => patch('telegram_alerts', e.target.checked)} className="accent-brand-500" /> Telegram alerts</label>
          <div><label className={lbl}>Telegram Bot</label><select value={local.telegram_bot} onChange={(e) => patch('telegram_bot', e.target.value)} className={`w-full ${sel}`}><option value="a">Bot A</option><option value="b">Bot B</option><option value="both">Both</option></select></div>
          <div><label className={lbl}>Alert Cooldown (min)</label>{num('alert_cooldown_min', 1)}</div>
        </div>
        <p className="text-[11px] text-gray-600 mt-2">Telegram reuses Settings → Telegram (Bot A / Bot B). Configure &amp; test bots there; tokens are never exposed here.</p>
      </div>
      <div className="flex items-center gap-2">
        <button onClick={save} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold"><Save className="w-4 h-4" /> Save Settings</button>
        <span className="text-[11px] text-gray-500">Durable (Postgres) · versioned strategy v{local.strategy_version}</span>
      </div>
      <Disclaimer />
    </div>
  );
}

function Disclaimer() {
  return (
    <div className="text-[11px] text-gray-500 bg-surface-2/60 border border-surface-3 rounded-lg px-3 py-2 flex items-start gap-2">
      <Info className="w-4 h-4 mt-0.5 shrink-0 text-amber-400" />
      <span><strong className="text-amber-300">Paper research only — no real orders are ever placed.</strong> Scores are a ranking of momentum quality, not a probability of profit. Signals are probabilistic research outputs; past performance does not guarantee future results.</span>
    </div>
  );
}
