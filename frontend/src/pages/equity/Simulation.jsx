import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  LineChart, Play, Loader2, AlertCircle, RefreshCw, Save, Plus, Trash2, Crosshair,
  SlidersHorizontal, ChevronDown, ChevronUp, Maximize2, Minimize2, TrendingUp, Layers,
  Type, Move, Keyboard,
} from 'lucide-react';
import { api } from '../../api';
import ChartCanvas, { LINE_GROUPS, lineColor, fmtNum, compact } from '../../components/ChartCanvas';

const sel = 'bg-surface-3 border border-surface-4 rounded-lg px-2.5 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';
const lbl = 'block text-[10px] text-gray-500 uppercase tracking-wide mb-1';
const TF_LABEL = {
  minute: '1m', '3minute': '3m', '5minute': '5m', '10minute': '10m', '15minute': '15m',
  '30minute': '30m', '60minute': '1h', day: '1D', week: '1W', month: '1M',
};
const KINDS = [
  ['equity', 'Equity', TrendingUp],
  ['fno_option', 'Equity F&O', Layers],
  ['index', 'NIFTY', LineChart],
  ['index_option', 'NIFTY Options', Layers],
];
const REFRESH_OPTS = [[5, '5 s'], [15, '15 s'], [30, '30 s'], [60, '1 min'], [300, '5 min']];
const LEVEL_COLORS = ['#f59e0b', '#38bdf8', '#a78bfa', '#22c55e', '#ef4444', '#e2e8f0'];
// changing any of these needs fresh maths from the server
const SERVER_PARAMS = ['timeframe', 'history_days', 'bars', 'volume_ma', 'ema_fast', 'ema_slow',
  'pivot_basis', 'first_hour_minutes', 'first_hour_days', 'hammer_lookback', 'hammer_red_before',
  'hammer_lower_wick', 'hammer_body_max', 'hammer_upper_wick', 'level_near_pct'];

const stateTone = (s) => ({
  TOUCHED: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40 animate-pulse',
  NEAR: 'bg-amber-500/15 text-amber-300 border-amber-500/40 animate-pulse',
  ABOVE: 'bg-surface-3 text-gray-400 border-surface-4',
  BELOW: 'bg-surface-3 text-gray-400 border-surface-4',
}[s] || 'bg-surface-3 text-gray-500 border-surface-4');

export default function Simulation() {
  const [meta, setMeta] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [kind, setKind] = useState('equity');
  const [symbol, setSymbol] = useState('');
  const [index, setIndex] = useState('NIFTY');
  const [chain, setChain] = useState(null);
  const [expiry, setExpiry] = useState('');
  const [strike, setStrike] = useState('');
  const [optType, setOptType] = useState('CE');
  const [expKind, setExpKind] = useState('weekly');   // weekly | monthly
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(''); const [msg, setMsg] = useState('');
  const [sugg, setSugg] = useState([]);
  const [addMode, setAddMode] = useState(null);       // null | 'line' | 'text'
  const [pendingText, setPendingText] = useState(null);
  const [hover, setHover] = useState(null);
  const [showInd, setShowInd] = useState(true);
  const [showLevels, setShowLevels] = useState(true);
  const [full, setFull] = useState(false);
  const [newLevel, setNewLevel] = useState({ price: '', label: '', color: LEVEL_COLORS[0] });
  const [editing, setEditing] = useState({});          // id → price string being typed
  const suggTimer = useRef(null);
  const timerRef = useRef(null);
  const paramTimer = useRef(null);
  const chartRef = useRef(null);
  const instKeyRef = useRef(null);
  const levelsRef = useRef([]);
  const colorRef = useRef(LEVEL_COLORS[0]);

  const showErr = (m) => { setErr(m); setTimeout(() => setErr(''), 7000); };
  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(''), 2500); };
  const patch = (k, v) => setCfg((c) => ({ ...c, [k]: v }));

  useEffect(() => {
    api.simMeta().then((r) => { if (r.status === 'ok') { setMeta(r); setCfg(r.config); setIndex(r.config.index_name || 'NIFTY'); } })
      .catch(() => showErr('Could not load the simulation desk'));
  }, []);

  useEffect(() => {
    if (suggTimer.current) clearTimeout(suggTimer.current);
    if (!symbol || symbol.trim().length < 2 || kind === 'index' || kind === 'index_option') { setSugg([]); return; }
    suggTimer.current = setTimeout(async () => {
      try { const r = await api.researchSymbolSearch(symbol.trim()); setSugg(r.status === 'ok' ? (r.results || []).slice(0, 8) : []); }
      catch { setSugg([]); }
    }, 250);
    return () => suggTimer.current && clearTimeout(suggTimer.current);
  }, [symbol, kind]);

  const loadChain = useCallback(async (name) => {
    if (!name) return;
    try {
      const r = await api.simChain(name);
      if (r.status === 'ok') {
        setChain(r);
        // Index options trade weekly, stock options monthly — open on whichever
        // this underlying actually has.
        const kind = (r.weekly || []).length ? 'weekly' : 'monthly';
        setExpKind(kind);
        const e = (r[kind] || r.expiries || [])[0] || '';
        setExpiry(e);
        const ks = r.strikes?.[e] || [];
        setStrike(ks.length ? String(ks[Math.floor(ks.length / 2)]) : '');
      } else { setChain(null); showErr(r.message); }
    } catch (e) { showErr(e.message); }
  }, []);

  useEffect(() => { if (kind === 'index_option') loadChain(index); else setChain(null); }, [kind, index, loadChain]);

  const selection = useCallback(() => {
    if (kind === 'equity') return { kind, symbol: symbol.trim().toUpperCase() };
    if (kind === 'index') return { kind, index };
    const base = kind === 'fno_option' ? { symbol: symbol.trim().toUpperCase() } : { index };
    return { kind, ...base, expiry, opt_type: optType, strike: optType === 'FUT' ? null : Number(strike) };
  }, [kind, symbol, index, expiry, optType, strike]);

  const load = useCallback(async (quiet = false) => {
    const s = selection();
    if ((kind === 'equity' || kind === 'fno_option') && !s.symbol) { if (!quiet) showErr('Type a stock first'); return; }
    if ((kind === 'fno_option' || kind === 'index_option') && optType !== 'FUT' && !s.strike) { if (!quiet) showErr('Pick a strike'); return; }
    if (!quiet) setLoading(true);
    try {
      const r = await api.simChart({ ...s, overrides: cfg });
      if (r.status === 'ok') setData(r); else if (!quiet) showErr(r.message || 'Chart failed');
    } catch (e) { if (!quiet) showErr(e.message); } finally { if (!quiet) setLoading(false); }
  }, [selection, cfg, kind, optType]);

  // auto-refresh (silent — never interrupts a pan or zoom)
  useEffect(() => {
    if (!cfg?.auto_refresh || !data) return undefined;
    timerRef.current = setInterval(() => load(true), Math.max(5, Number(cfg.refresh_secs) || 15) * 1000);
    return () => clearInterval(timerRef.current);
  }, [cfg?.auto_refresh, cfg?.refresh_secs, data, load]);

  // Indicator PARAMETERS (and the timeframe) need fresh maths; ticking an
  // indicator does not — the server sends every series and the rack only
  // decides what is drawn. Compare what the toolbar says against what the
  // chart on screen was actually built with, so nothing can be missed.
  const sig = (o) => SERVER_PARAMS.map((k) => {
    const v = o?.[k];
    return (typeof v === 'string' && v !== '' && !Number.isNaN(Number(v))) ? Number(v) : v;
  }).join('|');
  const paramSig = cfg ? sig(cfg) : '';
  const loadedSig = data?.config ? sig(data.config) : null;
  useEffect(() => {
    if (!data || loadedSig === paramSig) return undefined;
    if (paramTimer.current) clearTimeout(paramTimer.current);
    paramTimer.current = setTimeout(() => load(true), 450);
    return () => paramTimer.current && clearTimeout(paramTimer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paramSig, loadedSig]);

  // ── keyboard, TradingView style ──
  useEffect(() => {
    const onKey = (e) => {
      if (e.target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
      if (e.altKey && e.key.toLowerCase() === 'h') { e.preventDefault(); quickAdd('line'); }
      else if (e.altKey && e.key.toLowerCase() === 't') { e.preventDefault(); quickAdd('text'); }
      else if (e.altKey && e.key.toLowerCase() === 'f') { e.preventDefault(); chartRef.current?.fit(); }
      else if (e.key === 'Escape') { setAddMode(null); setPendingText(null); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instKey, data, newLevel.color]);

  const toggleInd = (key) => {
    const cur = new Set(cfg.indicators || []);
    if (cur.has(key)) cur.delete(key); else cur.add(key);
    patch('indicators', (meta.indicators || []).map((i) => i.key).filter((k) => cur.has(k)));
  };
  const setColor = (key, value) => patch('colors', { ...(cfg.colors || {}), [key]: value });

  const instKey = data?.instrument?.quote_key;
  const saveLevel = async (body) => {
    if (!instKey) { showErr('Load a chart first'); return; }
    try {
      const r = await api.simAddLevel({ instrument_key: instKey, symbol: data.instrument.label, kind, ...body });
      if (r.status === 'ok') { flash(`${body.type === 'text' ? 'Note' : 'Level'} saved at ${fmtNum(body.price)}`); load(true); }
      else showErr(r.message);
    } catch (e) { showErr(e.message); }
  };
  const onChartAdd = (price, mode, anchor) => {
    setAddMode(null);
    if (mode === 'text') setPendingText({ price, anchor, label: '', color: newLevel.color });
    else saveLevel({ price, label: newLevel.label || `L${(data?.levels?.length || 0) + 1}`, color: newLevel.color, type: 'line' });
  };
  // Alt+H / Alt+T drop straight onto the chart at the crosshair — no second
  // click. With the cursor off the chart we fall back to arming click-to-place.
  const quickAdd = (mode) => {
    if (!instKeyRef.current) { showErr('Load a chart first'); return; }
    const price = chartRef.current?.crosshairPrice();
    if (price == null) { setAddMode(mode); flash('Now click the chart to place it'); return; }
    const at = Math.round(price * 100) / 100;
    const anchor = chartRef.current?.crosshairBar();
    if (mode === 'text') setPendingText({ price: at, anchor, label: '', color: colorRef.current });
    else saveLevel({ price: at, label: `L${(levelsRef.current.length || 0) + 1}`, color: colorRef.current, type: 'line' });
  };

  const setLevelColor = async (id, color) => {
    try {
      const r = await api.simUpdateLevel(id, { color });
      if (r.status === 'ok') load(true); else showErr(r.message);
    } catch (e) { showErr(e.message); }
  };

  const moveLevel = async (id, price) => {
    try {
      const r = await api.simUpdateLevel(id, { price });
      if (r.status === 'ok') { flash(`Moved to ${fmtNum(price)}`); load(true); } else showErr(r.message);
    } catch (e) { showErr(e.message); }
  };
  const delLevel = async (id) => {
    try { const r = await api.simDeleteLevel(id); if (r.status === 'ok') { flash('Removed'); load(true); } else showErr(r.message); }
    catch (e) { showErr(e.message); }
  };
  const saveDefaults = async () => {
    try { const r = await api.simConfigSave(cfg); if (r.status === 'ok') { setCfg(r.config); flash('Defaults saved'); } }
    catch (e) { showErr(e.message); }
  };

  if (!cfg || !meta) return <div className="p-6 text-gray-500 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Loading simulation desk…</div>;

  const groups = (meta.indicators || []).reduce((a, i) => { (a[i.group] = a[i.group] || []).push(i); return a; }, {});
  const levels = data?.levels || [];
  instKeyRef.current = instKey;
  levelsRef.current = levels;
  colorRef.current = newLevel.color;
  const hot = levels.filter((l) => l.type !== 'text' && (l.state === 'TOUCHED' || l.state === 'NEAR'));
  const chartH = full ? Math.max(480, window.innerHeight - 190) : 640;
  const strikes = chain?.strikes?.[expiry] || [];
  const autoDays = meta.history_days?.[cfg.timeframe];

  return (
    <div className={`p-4 md:p-6 space-y-3 mx-auto ${full ? 'max-w-none' : 'max-w-[1800px]'}`}>
      {!full && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <LineChart className="w-6 h-6 text-brand-400" />
              <h1 className="text-xl font-bold text-gray-100">Chart Simulation</h1>
              <span className="text-[11px] px-2 py-0.5 rounded-full bg-brand-500/15 text-brand-300 border border-brand-500/25">Single instrument</span>
            </div>
            <p className="text-sm text-gray-500 mt-0.5">One symbol, full history, every indicator on a switch — and your own levels saved against the instrument, with what price did around them.</p>
          </div>
          <div className="flex items-center gap-3 text-[11px] text-gray-600">
            <span className="flex items-center gap-1"><Keyboard className="w-3.5 h-3.5" /> Alt+H line at cursor · Alt+T note · Alt+F fit · Esc cancel</span>
          </div>
        </div>
      )}

      <div className="flex gap-1 border-b border-surface-3">
        {KINDS.map(([id, label, Icon]) => (
          <button key={id} onClick={() => { setKind(id); setData(null); }}
            className={`flex items-center gap-1.5 px-4 py-2 text-sm font-semibold border-b-2 -mb-px transition ${kind === id ? 'border-brand-500 text-brand-400' : 'border-transparent text-gray-400 hover:text-gray-200'}`}>
            <Icon className="w-4 h-4" /> {label}
          </button>
        ))}
      </div>

      {err && <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-red-400 text-sm"><AlertCircle className="w-4 h-4" /> {err}</div>}
      {msg && <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-4 py-2 text-emerald-400 text-sm">{msg}</div>}

      <div className="bg-surface-2 border border-surface-3 rounded-xl p-3 flex flex-wrap items-end gap-2.5">
        {(kind === 'equity' || kind === 'fno_option') && (
          <div className="relative">
            <label className={lbl}>Stock</label>
            <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              onKeyDown={(e) => { if (e.key === 'Enter') { setSugg([]); if (kind === 'fno_option') loadChain(symbol.trim().toUpperCase()); else load(); } }}
              placeholder="e.g. BSE" className={`${sel} w-40`} />
            {sugg.length > 0 && (
              <div className="absolute z-30 mt-1 w-40 max-h-56 overflow-auto bg-surface-2 border border-surface-3 rounded-lg shadow-2xl">
                {sugg.map((s) => (
                  <button key={`${s.symbol}:${s.exchange}`}
                    onClick={() => { setSymbol(s.symbol); setSugg([]); if (kind === 'fno_option') loadChain(s.symbol); }}
                    className="w-full text-left px-3 py-1.5 hover:bg-surface-3/40 flex items-center gap-2 border-b border-surface-3/40 last:border-0">
                    <span className="text-sm text-gray-100">{s.symbol}</span><span className="text-[10px] text-gray-500 ml-auto">{s.exchange}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        {(kind === 'index' || kind === 'index_option') && (
          <div>
            <label className={lbl}>Index</label>
            <select value={index} onChange={(e) => setIndex(e.target.value)} className={sel}>
              {(meta.indices || ['NIFTY']).map((i) => <option key={i} value={i}>{i}</option>)}
            </select>
          </div>
        )}
        {(kind === 'fno_option' || kind === 'index_option') && (
          <>
            {kind === 'fno_option' && (
              <button onClick={() => loadChain(symbol.trim().toUpperCase())} className="px-2.5 py-1.5 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white">Load chain</button>
            )}
            <div>
              <label className={lbl}>Contract</label>
              <select value={optType} onChange={(e) => setOptType(e.target.value)} className={sel}>
                <option value="CE">CE</option><option value="PE">PE</option><option value="FUT">Future</option>
              </select>
            </div>
            <div>
              <label className={lbl}>Expiry</label>
              <div className="flex items-center gap-1.5">
                <div className="flex rounded-lg bg-surface-3 p-0.5">
                  {['weekly', 'monthly'].map((k) => {
                    const list = chain?.[k] || [];
                    return (
                      <button key={k} disabled={!list.length}
                        onClick={() => {
                          setExpKind(k);
                          const e0 = list[0] || '';
                          setExpiry(e0);
                          const ks = chain?.strikes?.[e0] || [];
                          setStrike(ks.length ? String(ks[Math.floor(ks.length / 2)]) : '');
                        }}
                        className={`px-2 py-1 text-[11px] rounded-md font-semibold capitalize transition disabled:opacity-30 ${expKind === k ? 'bg-brand-600 text-white' : 'text-gray-400 hover:text-gray-200'}`}>
                        {k}
                      </button>
                    );
                  })}
                </div>
                <select value={expiry} onChange={(e) => { setExpiry(e.target.value); const ks = chain?.strikes?.[e.target.value] || []; setStrike(ks.length ? String(ks[Math.floor(ks.length / 2)]) : ''); }} className={sel}>
                  {((chain?.[expKind]?.length ? chain[expKind] : chain?.expiries) || []).map((e) => <option key={e} value={e}>{e}</option>)}
                </select>
              </div>
            </div>
            {optType !== 'FUT' && (
              <div>
                <label className={lbl}>Strike</label>
                <select value={strike} onChange={(e) => setStrike(e.target.value)} className={`${sel} w-28`}>
                  {strikes.map((k) => <option key={k} value={k}>{k}</option>)}
                </select>
              </div>
            )}
          </>
        )}
        <div>
          <label className={lbl}>Candle</label>
          <select value={cfg.timeframe} onChange={(e) => patch('timeframe', e.target.value)} className={sel}>
            {(meta.timeframes || []).map((t) => <option key={t} value={t}>{TF_LABEL[t] || t}</option>)}
          </select>
        </div>
        <div title={`Blank or 0 = automatic: ${autoDays} calendar days for ${TF_LABEL[cfg.timeframe]}. Raise it to reach further back.`}>
          <label className={lbl}>History</label>
          <input type="number" min={0} step={30} value={cfg.history_days || ''}
            onChange={(e) => patch('history_days', e.target.value === '' ? 0 : e.target.value)}
            placeholder={`auto (${autoDays}d)`} className={`${sel} w-28`} />
        </div>
        <div title="How many candles the server sends. Pan left inside them without another fetch.">
          <label className={lbl}>Bars</label>
          <input type="number" min={200} step={500} value={cfg.bars} onChange={(e) => patch('bars', e.target.value)} className={`${sel} w-24`} />
        </div>
        <button onClick={() => load()} disabled={loading} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />} Load
        </button>
        <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer">
          <input type="checkbox" checked={cfg.auto_refresh} onChange={(e) => patch('auto_refresh', e.target.checked)} className="accent-brand-500" /> Auto
        </label>
        <select value={cfg.refresh_secs} onChange={(e) => patch('refresh_secs', Number(e.target.value))} className={`${sel} py-1`}>
          {REFRESH_OPTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <div className="ml-auto flex items-center gap-2">
          <button onClick={() => setAddMode(addMode === 'line' ? null : 'line')} title="Click the chart to place a line — or just press Alt+H with the cursor where you want it"
            className={`flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-lg border ${addMode === 'line' ? 'bg-brand-600 text-white border-brand-500' : 'bg-surface-3 text-gray-300 border-surface-4 hover:text-white'}`}>
            <Crosshair className="w-3.5 h-3.5" /> {addMode === 'line' ? 'Click chart…' : 'Line'}
          </button>
          <button onClick={() => setAddMode(addMode === 'text' ? null : 'text')} title="Alt+T — then click where the note goes"
            className={`flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-lg border ${addMode === 'text' ? 'bg-brand-600 text-white border-brand-500' : 'bg-surface-3 text-gray-300 border-surface-4 hover:text-white'}`}>
            <Type className="w-3.5 h-3.5" /> Text
          </button>
          <button onClick={() => chartRef.current?.fit()} title="Alt+F — refit the scale"
            className="px-2.5 py-1.5 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white">Fit</button>
          <button onClick={saveDefaults} className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white"><Save className="w-3.5 h-3.5" /> Save</button>
          <button onClick={() => load(true)} className="text-gray-400 hover:text-white"><RefreshCw className="w-4 h-4" /></button>
          <button onClick={() => setFull((v) => !v)} className="text-gray-400 hover:text-white">{full ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}</button>
        </div>
      </div>

      {hot.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 bg-surface-2 border border-amber-500/30 rounded-xl px-3 py-2">
          <span className="text-[11px] uppercase tracking-wide text-amber-400/90 font-semibold">Level alerts</span>
          {hot.map((l) => (
            <span key={l.id} className={`px-2.5 py-1 rounded-lg border text-[11px] font-semibold ${stateTone(l.state)}`}>
              {l.label || 'level'} {fmtNum(l.price)} · {l.state === 'TOUCHED' ? 'TOUCHED today' : `${Math.abs(l.distance_pct)}% away, ${l.approach}`}
            </span>
          ))}
        </div>
      )}

      <div className="flex gap-3 items-start">
        <div className="flex-1 min-w-0">
          <div className="bg-surface-2 border border-surface-3 rounded-xl p-2">
            {/* Fixed height + nowrap: the readout must never re-flow the page
                when it appears, or the chart jumps under the cursor. */}
            <div className="flex items-center gap-x-4 px-2 h-6 text-xs whitespace-nowrap overflow-hidden">
              <span className="text-brand-300 font-bold shrink-0">{data?.instrument?.label || '—'}</span>
              <span className="text-gray-500 shrink-0">{TF_LABEL[data?.timeframe] || ''}</span>
              {data && <span className="text-gray-200 font-semibold shrink-0">₹{fmtNum(data.ltp)}</span>}
              {(() => {
                const b = hover || (data?.candles || [])[(data?.candles?.length || 0) - 1];
                if (!b) return null;
                return (
                  <span className="text-gray-400 truncate">
                    {b.t} · O {fmtNum(b.o)} H {fmtNum(b.h)} L {fmtNum(b.l)} C <strong className={b.c >= b.o ? 'text-emerald-400' : 'text-red-400'}>{fmtNum(b.c)}</strong> · V {compact(b.v)}
                    {b.oi ? ` · OI ${compact(b.oi)}` : ''}
                  </span>
                );
              })()}
              {data && <span className="ml-auto text-gray-600 shrink-0 hidden xl:inline">{data.bars_shown} bars · from {data.first_bar} · {data.history_days}d · {data.generated_at}</span>}
            </div>
            <ChartCanvas
              ref={chartRef}
              candles={data?.candles || []} series={data?.series || {}} enabled={cfg.indicators || []}
              levels={levels} colors={cfg.colors || {}} height={chartH} addMode={addMode} ltp={data?.ltp}
              onAddLevel={onChartAdd} onCrosshair={(c) => setHover(c)}
            />
            <div className="flex items-center gap-x-3 px-2 pt-1 h-5 text-[10px] overflow-hidden whitespace-nowrap">
              {(cfg.indicators || []).flatMap((k) => (LINE_GROUPS[k] || []).map((ln) => (
                <span key={ln.k} className="flex items-center gap-1 text-gray-500"
                  title={(meta.indicators || []).find((x) => x.key === k)?.note}>
                  <span className="w-2.5 h-0.5 rounded" style={{ background: lineColor(ln, cfg.colors || {}) }} />
                  {ln.label}
                </span>
              )))}
              <span className="ml-auto text-gray-600 shrink-0 hidden lg:inline">drag to pan · wheel to zoom · drag the axis to squeeze · double-click to fit</span>
            </div>
          </div>
        </div>

        <div className="w-[310px] shrink-0 space-y-3">
          <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
            <button onClick={() => setShowInd((v) => !v)} className="w-full px-3 py-2 flex items-center justify-between text-sm font-semibold text-gray-200 hover:text-white">
              <span className="flex items-center gap-1.5"><SlidersHorizontal className="w-4 h-4 text-brand-400" /> Indicators <span className="text-gray-600 font-normal">({(cfg.indicators || []).length})</span></span>
              {showInd ? <ChevronUp className="w-4 h-4 text-gray-500" /> : <ChevronDown className="w-4 h-4 text-gray-500" />}
            </button>
            {showInd && (
              <div className="border-t border-surface-3 max-h-[440px] overflow-y-auto">
                {Object.entries(groups).map(([g, items]) => (
                  <div key={g} className="border-b border-surface-3/50 last:border-0">
                    <div className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-wide text-gray-600">{g}</div>
                    {items.map((i) => {
                      const lines = LINE_GROUPS[i.key] || [];
                      const on = (cfg.indicators || []).includes(i.key);
                      // OI only exists on derivatives — say so instead of
                      // letting the tick do nothing on a cash chart.
                      const noOi = i.key === 'oi' && data && !data.series?.oi;
                      const noVol = data && data.has_volume === false
                        && ['volume', 'volume_ma', 'cum_volume'].includes(i.key);
                      const twap = data && data.has_volume === false && i.key.includes('vwap');
                      return (
                        <div key={i.key} className={`px-3 py-1.5 hover:bg-surface-3/20 ${noOi ? 'opacity-50' : ''}`}>
                          <div className="flex items-start gap-2">
                            <input type="checkbox" checked={on && !noOi} disabled={noOi}
                              onChange={() => toggleInd(i.key)}
                              className="accent-brand-500 mt-0.5 cursor-pointer disabled:cursor-not-allowed" />
                            <label className="min-w-0 flex-1 cursor-pointer" title={noOi ? 'This instrument carries no open interest' : i.note}
                              onClick={() => !noOi && toggleInd(i.key)}>
                              <span className="text-xs text-gray-200 block">
                                {i.name}
                                {noOi && <span className="ml-1.5 text-[9px] px-1 py-0.5 rounded bg-surface-3 text-amber-400/90 border border-surface-4 align-middle">no OI on {data.instrument?.label}</span>}
                                {twap && <span className="ml-1.5 text-[9px] px-1 py-0.5 rounded bg-surface-3 text-sky-400/90 border border-surface-4 align-middle">TWAP</span>}
                                {noVol && <span className="ml-1.5 text-[9px] px-1 py-0.5 rounded bg-surface-3 text-amber-400/90 border border-surface-4 align-middle">no volume</span>}
                              </span>
                              <span className="block text-[10px] text-gray-600 leading-snug">
                                {noOi ? 'Cash equity and index charts have no open interest — switch to the Equity F&O or NIFTY Options tab to see it.'
                                  : twap ? `${data.instrument?.label} publishes no volume, so this is anchored on the typical price with every bar weighted equally (TWAP). Use the future for a true VWAP.`
                                    : noVol ? `${data.instrument?.label} publishes no volume — nothing to plot here.`
                                      : i.note}
                              </span>
                            </label>
                          </div>
                          {on && lines.length > 0 && (
                            <div className="flex flex-wrap items-center gap-2 mt-1 pl-6">
                              {lines.map((ln) => (
                                <span key={ln.k} className="flex items-center gap-1" title={`Colour of ${ln.label}`}>
                                  <input type="color" value={lineColor(ln, cfg.colors || {})}
                                    onChange={(e) => setColor(ln.k, e.target.value)}
                                    className="w-4 h-4 rounded cursor-pointer bg-transparent border-0 p-0" />
                                  <span className="text-[9px] text-gray-500">{ln.label}</span>
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ))}
                <div className="grid grid-cols-2 gap-2 p-3 border-t border-surface-3">
                  <div><label className={lbl}>Vol MA</label><input type="number" value={cfg.volume_ma} onChange={(e) => patch('volume_ma', e.target.value)} className={`${sel} w-full py-1`} /></div>
                  <div><label className={lbl}>EMA fast</label><input type="number" value={cfg.ema_fast} onChange={(e) => patch('ema_fast', e.target.value)} className={`${sel} w-full py-1`} /></div>
                  <div><label className={lbl}>EMA slow</label><input type="number" value={cfg.ema_slow} onChange={(e) => patch('ema_slow', e.target.value)} className={`${sel} w-full py-1`} /></div>
                  <div><label className={lbl}>Pivot basis</label>
                    <select value={cfg.pivot_basis} onChange={(e) => patch('pivot_basis', e.target.value)} className={`${sel} w-full py-1`}>
                      <option value="day">Daily</option><option value="week">Weekly</option><option value="month">Monthly</option>
                    </select>
                  </div>
                  <div><label className={lbl}>Open window (min)</label><input type="number" value={cfg.first_hour_minutes} onChange={(e) => patch('first_hour_minutes', e.target.value)} className={`${sel} w-full py-1`} /></div>
                  <div><label className={lbl}>FH stats days</label><input type="number" value={cfg.first_hour_days} onChange={(e) => patch('first_hour_days', e.target.value)} className={`${sel} w-full py-1`} /></div>
                  <div><label className={lbl}>Hammer lookback</label><input type="number" value={cfg.hammer_lookback} onChange={(e) => patch('hammer_lookback', e.target.value)} className={`${sel} w-full py-1`} /></div>
                  <div><label className={lbl}>Red before</label><input type="number" value={cfg.hammer_red_before} onChange={(e) => patch('hammer_red_before', e.target.value)} className={`${sel} w-full py-1`} /></div>
                  <div title="Minimum lower wick, as a % of the candle's low"><label className={lbl}>Wick min %</label><input type="number" step="0.1" value={cfg.hammer_lower_wick} onChange={(e) => patch('hammer_lower_wick', e.target.value)} className={`${sel} w-full py-1`} /></div>
                  <div title="Maximum body, as a % of the candle's low"><label className={lbl}>Body max %</label><input type="number" step="0.1" value={cfg.hammer_body_max} onChange={(e) => patch('hammer_body_max', e.target.value)} className={`${sel} w-full py-1`} /></div>
                  <div title="Maximum upper wick, as a % of the candle's low"><label className={lbl}>Up wick max %</label><input type="number" step="0.1" value={cfg.hammer_upper_wick} onChange={(e) => patch('hammer_upper_wick', e.target.value)} className={`${sel} w-full py-1`} /></div>
                  <div><label className={lbl}>Near level %</label><input type="number" step="0.1" value={cfg.level_near_pct} onChange={(e) => patch('level_near_pct', e.target.value)} className={`${sel} w-full py-1`} /></div>
                </div>
              </div>
            )}
          </div>

          <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
            <button onClick={() => setShowLevels((v) => !v)} className="w-full px-3 py-2 flex items-center justify-between text-sm font-semibold text-gray-200 hover:text-white">
              <span>My levels &amp; notes <span className="text-gray-600 font-normal">({levels.length})</span></span>
              {showLevels ? <ChevronUp className="w-4 h-4 text-gray-500" /> : <ChevronDown className="w-4 h-4 text-gray-500" />}
            </button>
            {showLevels && (
              <div className="border-t border-surface-3">
                <div className="p-3 space-y-2 border-b border-surface-3">
                  <div className="flex gap-1.5">
                    <input value={newLevel.price} onChange={(e) => setNewLevel((s) => ({ ...s, price: e.target.value }))}
                      placeholder="price" className={`${sel} w-24 py-1`} />
                    <input value={newLevel.label} onChange={(e) => setNewLevel((s) => ({ ...s, label: e.target.value }))}
                      placeholder="label" className={`${sel} flex-1 py-1`} />
                    <button onClick={() => newLevel.price && saveLevel({ price: Number(newLevel.price), label: newLevel.label || 'level', color: newLevel.color, type: 'line' })}
                      disabled={!instKey} className="px-2 rounded-lg bg-brand-600 hover:bg-brand-700 text-white disabled:opacity-40"><Plus className="w-3.5 h-3.5" /></button>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {LEVEL_COLORS.map((c) => (
                      <button key={c} onClick={() => setNewLevel((s) => ({ ...s, color: c }))}
                        className={`w-5 h-5 rounded ${newLevel.color === c ? 'ring-2 ring-white/70' : ''}`} style={{ background: c }} />
                    ))}
                    <span className="text-[10px] text-gray-600 ml-auto">Alt+H drops a line at the cursor</span>
                  </div>
                </div>
                {!levels.length ? (
                  <div className="px-3 py-6 text-center text-gray-600 text-xs">Nothing saved for this instrument yet.</div>
                ) : (
                  <div className="max-h-[320px] overflow-y-auto divide-y divide-surface-3/50">
                    {levels.map((l) => (
                      <div key={l.id} className="px-3 py-2">
                        <div className="flex items-center gap-2">
                          <input type="color" value={l.color || '#f59e0b'} title="Change this level's colour"
                            onChange={(e) => setLevelColor(l.id, e.target.value)}
                            className="w-4 h-4 rounded cursor-pointer bg-transparent border-0 p-0 shrink-0" />
                          <input
                            value={editing[l.id] ?? fmtNum(l.price)}
                            onChange={(e) => setEditing((s) => ({ ...s, [l.id]: e.target.value }))}
                            onBlur={() => {
                              const v = Number(String(editing[l.id] ?? '').replace(/,/g, ''));
                              setEditing((s) => { const c = { ...s }; delete c[l.id]; return c; });
                              if (v && Math.abs(v - Number(l.price)) > 0.001) moveLevel(l.id, v);
                            }}
                            onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }}
                            className="w-20 bg-transparent border-b border-transparent hover:border-surface-4 focus:border-brand-500 text-xs text-gray-200 font-semibold focus:outline-none"
                          />
                          {l.type === 'text' && <Type className="w-3 h-3 text-gray-500 shrink-0" />}
                          <span className="text-[11px] text-gray-500 truncate flex-1">{l.label}</span>
                          {l.type !== 'text' && <span className={`px-1.5 py-0.5 rounded border text-[9px] font-bold ${stateTone(l.state)}`}>{l.state}</span>}
                          <button onClick={() => delLevel(l.id)} className="text-gray-600 hover:text-red-400"><Trash2 className="w-3.5 h-3.5" /></button>
                        </div>
                        {l.type !== 'text' && (
                          <>
                            <div className="text-[10px] text-gray-500 mt-0.5">
                              {l.distance_pct > 0 ? `price ${l.distance_pct}% above` : l.distance_pct < 0 ? `price ${Math.abs(l.distance_pct)}% below` : 'at the level'} · {l.approach}
                            </div>
                            <div className="text-[10px] text-gray-600">
                              {l.touches ? `${l.touches} touch(es) · last ${l.last_touch}` : 'never touched in this range'}
                            </div>
                          </>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                <div className="px-3 py-2 border-t border-surface-3 text-[10px] text-gray-600 flex items-center gap-1.5">
                  <Move className="w-3 h-3" /> Alt+H drops a line at the cursor. Edit the price or colour here — lines never move by dragging, so panning is always safe.
                </div>
              </div>
            )}
          </div>

          {(cfg.indicators || []).includes('hammer') && data && (
            <div className="bg-surface-2 border border-surface-3 rounded-xl p-3">
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-xs font-semibold text-gray-200">Hammer signals</span>
                <span className="text-[10px] text-gray-500">{(data.series?.hammer?.signals || []).length} found · {cfg.hammer_lookback}-bar low</span>
              </div>
              {!(data.series?.hammer?.signals || []).length ? (
                <div className="text-[11px] text-gray-500 leading-snug">
                  Nothing qualifies in this range. The rule is strict — lower wick ≥ {cfg.hammer_lower_wick}%,
                  body &lt; {cfg.hammer_body_max}%, upper wick &lt; {cfg.hammer_upper_wick}%, at the
                  {' '}{cfg.hammer_lookback}-bar low after {cfg.hammer_red_before} red candles.
                  Shorten the lookback or relax the wick/body limits in Indicators to widen it.
                </div>
              ) : (
                <div className="max-h-40 overflow-y-auto space-y-1">
                  {(data.series.hammer.signals || []).slice(-12).reverse().map((h) => (
                    <div key={h.at} className="text-[11px]">
                      <div className="flex items-center gap-2">
                        <span className="text-gray-400">{h.at}</span>
                        <span className="text-amber-400 font-semibold ml-auto">buy &gt; {fmtNum(h.trigger)}</span>
                      </div>
                      <div className="text-[10px] text-gray-600">
                        low {fmtNum(h.low)} · wick {h.lower_wick_pct}% · body {h.body_pct}% · up {h.upper_wick_pct}%
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {data?.series?.fourth_candle?.days?.length > 0 && (cfg.indicators || []).includes('fourth_candle') && (
            <div className="bg-surface-2 border border-surface-3 rounded-xl p-3">
              <div className="text-xs font-semibold text-gray-200 mb-1.5">4th-candle setups</div>
              <div className="max-h-40 overflow-y-auto space-y-1">
                {data.series.fourth_candle.days.slice(-12).reverse().map((d) => (
                  <div key={d.date} className="flex items-center gap-2 text-[11px]">
                    <span className="text-gray-500">{d.date}</span>
                    <span className={d.bias === 'call' ? 'text-red-400' : 'text-emerald-400'}>{d.bias === 'call' ? '3 RED' : '3 GREEN'}</span>
                    <span className="text-gray-400">{d.bias === 'call' ? `H ${fmtNum(d.high)}` : `L ${fmtNum(d.low)}`}</span>
                    <span className={`ml-auto ${d.broke ? 'text-emerald-400' : 'text-gray-600'}`}>{d.broke ? 'broke' : 'no break'}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* text-note composer */}
      {pendingText && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => setPendingText(null)}>
          <div className="bg-surface-1 border border-surface-3 rounded-xl w-full max-w-md p-4 space-y-3" onClick={(e) => e.stopPropagation()}>
            <div className="text-sm font-semibold text-gray-100">Note at ₹{fmtNum(pendingText.price)}</div>
            <input autoFocus value={pendingText.label} onChange={(e) => setPendingText((s) => ({ ...s, label: e.target.value }))}
              onKeyDown={(e) => { if (e.key === 'Enter' && pendingText.label.trim()) { saveLevel({ ...pendingText, type: 'text' }); setPendingText(null); } }}
              placeholder="e.g. supply zone from Aug high" className={`${sel} w-full`} />
            <div className="flex items-center gap-1.5">
              {LEVEL_COLORS.map((c) => (
                <button key={c} onClick={() => setPendingText((s) => ({ ...s, color: c }))}
                  className={`w-5 h-5 rounded ${pendingText.color === c ? 'ring-2 ring-white/70' : ''}`} style={{ background: c }} />
              ))}
              <div className="ml-auto flex gap-2">
                <button onClick={() => setPendingText(null)} className="px-3 py-1.5 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4">Cancel</button>
                <button onClick={() => { if (pendingText.label.trim()) { saveLevel({ ...pendingText, type: 'text' }); setPendingText(null); } }}
                  className="px-3 py-1.5 text-xs rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold">Add note</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
