import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  LineChart, Play, Loader2, AlertCircle, RefreshCw, Save, Plus, Trash2, Crosshair,
  SlidersHorizontal, ChevronDown, ChevronUp, Maximize2, Minimize2, TrendingUp, Layers,
} from 'lucide-react';
import { api } from '../../api';
import ChartCanvas, { OVERLAYS, fmtNum, compact } from '../../components/ChartCanvas';

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
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(''); const [msg, setMsg] = useState('');
  const [sugg, setSugg] = useState([]);
  const [addMode, setAddMode] = useState(false);
  const [hover, setHover] = useState(null);
  const [showInd, setShowInd] = useState(true);
  const [showLevels, setShowLevels] = useState(true);
  const [full, setFull] = useState(false);
  const [newLevel, setNewLevel] = useState({ price: '', label: '', color: LEVEL_COLORS[0] });
  const suggTimer = useRef(null);
  const timerRef = useRef(null);

  const showErr = (m) => { setErr(m); setTimeout(() => setErr(''), 7000); };
  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(''), 2500); };
  const patch = (k, v) => setCfg((c) => ({ ...c, [k]: v }));

  useEffect(() => {
    api.simMeta().then((r) => { if (r.status === 'ok') { setMeta(r); setCfg(r.config); setIndex(r.config.index_name || 'NIFTY'); } })
      .catch(() => showErr('Could not load the simulation desk'));
  }, []);

  // symbol type-ahead
  useEffect(() => {
    if (suggTimer.current) clearTimeout(suggTimer.current);
    if (!symbol || symbol.trim().length < 2 || kind === 'index' || kind === 'index_option') { setSugg([]); return; }
    suggTimer.current = setTimeout(async () => {
      try { const r = await api.researchSymbolSearch(symbol.trim()); setSugg(r.status === 'ok' ? (r.results || []).slice(0, 8) : []); }
      catch { setSugg([]); }
    }, 250);
    return () => suggTimer.current && clearTimeout(suggTimer.current);
  }, [symbol, kind]);

  const chainName = kind === 'index_option' ? index : symbol.trim().toUpperCase();
  const loadChain = useCallback(async (name) => {
    if (!name) return;
    try {
      const r = await api.simChain(name);
      if (r.status === 'ok') {
        setChain(r);
        const e = r.expiries?.[0] || '';
        setExpiry(e);
        const ks = r.strikes?.[e] || [];
        setStrike(ks.length ? String(ks[Math.floor(ks.length / 2)]) : '');
      } else { setChain(null); showErr(r.message); }
    } catch (e) { showErr(e.message); }
  }, []);

  useEffect(() => {
    if (kind === 'index_option') loadChain(index);
    else setChain(null);
  }, [kind, index, loadChain]);

  const selection = useCallback(() => {
    if (kind === 'equity') return { kind, symbol: symbol.trim().toUpperCase() };
    if (kind === 'index') return { kind, index };
    if (kind === 'fno_option') {
      return { kind, symbol: symbol.trim().toUpperCase(), expiry, opt_type: optType,
        strike: optType === 'FUT' ? null : Number(strike) };
    }
    return { kind, index, expiry, opt_type: optType, strike: optType === 'FUT' ? null : Number(strike) };
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

  // auto-refresh
  useEffect(() => {
    if (!cfg?.auto_refresh || !data) return undefined;
    timerRef.current = setInterval(() => load(true), Math.max(5, Number(cfg.refresh_secs) || 15) * 1000);
    return () => clearInterval(timerRef.current);
  }, [cfg?.auto_refresh, cfg?.refresh_secs, data, load]);

  const toggleInd = (key) => {
    const cur = new Set(cfg.indicators || []);
    if (cur.has(key)) cur.delete(key); else cur.add(key);
    patch('indicators', (meta.indicators || []).map((i) => i.key).filter((k) => cur.has(k)));
  };

  const instKey = data?.instrument?.quote_key;
  const addLevel = async (price, label) => {
    if (!instKey) { showErr('Load a chart first'); return; }
    try {
      const r = await api.simAddLevel({
        instrument_key: instKey, symbol: data.instrument.label, kind,
        price: Number(price), label: label || `L${(data.levels?.length || 0) + 1}`,
        color: newLevel.color,
      });
      if (r.status === 'ok') { flash(`Level ${fmtNum(price)} saved`); setNewLevel((s) => ({ ...s, price: '', label: '' })); load(true); }
      else showErr(r.message);
    } catch (e) { showErr(e.message); }
  };
  const delLevel = async (id) => {
    try { const r = await api.simDeleteLevel(id); if (r.status === 'ok') { flash('Level removed'); load(true); } else showErr(r.message); }
    catch (e) { showErr(e.message); }
  };
  const saveDefaults = async () => {
    try { const r = await api.simConfigSave(cfg); if (r.status === 'ok') { setCfg(r.config); flash('Defaults saved'); } }
    catch (e) { showErr(e.message); }
  };

  if (!cfg || !meta) return <div className="p-6 text-gray-500 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Loading simulation desk…</div>;

  const groups = (meta.indicators || []).reduce((a, i) => { (a[i.group] = a[i.group] || []).push(i); return a; }, {});
  const levels = data?.levels || [];
  const hot = levels.filter((l) => l.state === 'TOUCHED' || l.state === 'NEAR');
  const chartH = full ? Math.max(480, window.innerHeight - 190) : 620;
  const strikes = chain?.strikes?.[expiry] || [];

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

      {/* ── toolbar ── */}
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
              <select value={expiry} onChange={(e) => { setExpiry(e.target.value); const ks = chain?.strikes?.[e.target.value] || []; setStrike(ks.length ? String(ks[Math.floor(ks.length / 2)]) : ''); }} className={sel}>
                {(chain?.expiries || []).map((e) => <option key={e} value={e}>{e}</option>)}
              </select>
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
        <div>
          <label className={lbl}>History (days)</label>
          <input type="number" min={0} step={30} value={cfg.history_days}
            onChange={(e) => patch('history_days', e.target.value)} placeholder="auto" className={`${sel} w-24`} />
        </div>
        <div>
          <label className={lbl}>Bars shown</label>
          <input type="number" min={50} step={50} value={cfg.bars} onChange={(e) => patch('bars', e.target.value)} className={`${sel} w-24`} />
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
          <button onClick={() => setAddMode((v) => !v)} title="Click the chart to drop a level"
            className={`flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-lg border ${addMode ? 'bg-brand-600 text-white border-brand-500' : 'bg-surface-3 text-gray-300 border-surface-4 hover:text-white'}`}>
            <Crosshair className="w-3.5 h-3.5" /> {addMode ? 'Click chart…' : 'Add level'}
          </button>
          <button onClick={saveDefaults} className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white"><Save className="w-3.5 h-3.5" /> Save</button>
          <button onClick={() => load(true)} className="text-gray-400 hover:text-white"><RefreshCw className="w-4 h-4" /></button>
          <button onClick={() => setFull((v) => !v)} className="text-gray-400 hover:text-white">{full ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}</button>
        </div>
      </div>

      {/* ── level alerts ── */}
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
        {/* ── chart ── */}
        <div className="flex-1 min-w-0 space-y-2">
          <div className="bg-surface-2 border border-surface-3 rounded-xl p-2">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-2 py-1 text-xs">
              <span className="text-brand-300 font-bold">{data?.instrument?.label || '—'}</span>
              <span className="text-gray-500">{TF_LABEL[data?.timeframe] || ''}</span>
              {data && <span className="text-gray-200 font-semibold">₹{fmtNum(data.ltp)}</span>}
              {hover && (
                <span className="text-gray-400">
                  {hover.t} · O {fmtNum(hover.o)} H {fmtNum(hover.h)} L {fmtNum(hover.l)} C <strong className={hover.c >= hover.o ? 'text-emerald-400' : 'text-red-400'}>{fmtNum(hover.c)}</strong> · V {compact(hover.v)}
                  {hover.oi ? ` · OI ${compact(hover.oi)}` : ''}
                </span>
              )}
              {data && <span className="ml-auto text-gray-600">{data.bars_shown} of {data.bars_total} bars · from {data.first_bar} · {data.generated_at}</span>}
            </div>
            <ChartCanvas
              candles={data?.candles || []} series={data?.series || {}} enabled={cfg.indicators || []}
              levels={levels} height={chartH} addMode={addMode} ltp={data?.ltp}
              onAddLevel={(p) => { setAddMode(false); addLevel(p); }}
              onCrosshair={(c) => setHover(c)}
            />
            <div className="flex flex-wrap gap-x-3 gap-y-1 px-2 pt-1 text-[10px]">
              {(cfg.indicators || []).map((k) => {
                const o = OVERLAYS[k];
                const m = (meta.indicators || []).find((x) => x.key === k);
                return (
                  <span key={k} className="flex items-center gap-1 text-gray-500" title={m?.note}>
                    <span className="w-2.5 h-0.5 rounded" style={{ background: o?.color || '#64748b' }} />
                    {m?.name || k}
                  </span>
                );
              })}
              <span className="ml-auto text-gray-600">drag to pan · wheel to zoom</span>
            </div>
          </div>
        </div>

        {/* ── right rack ── */}
        <div className="w-[300px] shrink-0 space-y-3">
          <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
            <button onClick={() => setShowInd((v) => !v)} className="w-full px-3 py-2 flex items-center justify-between text-sm font-semibold text-gray-200 hover:text-white">
              <span className="flex items-center gap-1.5"><SlidersHorizontal className="w-4 h-4 text-brand-400" /> Indicators <span className="text-gray-600 font-normal">({(cfg.indicators || []).length})</span></span>
              {showInd ? <ChevronUp className="w-4 h-4 text-gray-500" /> : <ChevronDown className="w-4 h-4 text-gray-500" />}
            </button>
            {showInd && (
              <div className="border-t border-surface-3 max-h-[420px] overflow-y-auto">
                {Object.entries(groups).map(([g, items]) => (
                  <div key={g} className="border-b border-surface-3/50 last:border-0">
                    <div className="px-3 pt-2 pb-1 text-[10px] uppercase tracking-wide text-gray-600">{g}</div>
                    {items.map((i) => (
                      <label key={i.key} title={i.note} className="flex items-start gap-2 px-3 py-1.5 hover:bg-surface-3/20 cursor-pointer">
                        <input type="checkbox" checked={(cfg.indicators || []).includes(i.key)} onChange={() => toggleInd(i.key)} className="accent-brand-500 mt-0.5" />
                        <span className="min-w-0">
                          <span className="text-xs text-gray-200 flex items-center gap-1.5">
                            {OVERLAYS[i.key] && <span className="w-2.5 h-0.5 rounded shrink-0" style={{ background: OVERLAYS[i.key].color }} />}
                            {i.name}
                          </span>
                          <span className="block text-[10px] text-gray-600 leading-snug">{i.note}</span>
                        </span>
                      </label>
                    ))}
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
                  <div><label className={lbl}>Opening window (min)</label><input type="number" value={cfg.first_hour_minutes} onChange={(e) => patch('first_hour_minutes', e.target.value)} className={`${sel} w-full py-1`} /></div>
                  <div><label className={lbl}>Near level %</label><input type="number" step="0.1" value={cfg.level_near_pct} onChange={(e) => patch('level_near_pct', e.target.value)} className={`${sel} w-full py-1`} /></div>
                </div>
              </div>
            )}
          </div>

          <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
            <button onClick={() => setShowLevels((v) => !v)} className="w-full px-3 py-2 flex items-center justify-between text-sm font-semibold text-gray-200 hover:text-white">
              <span>My levels <span className="text-gray-600 font-normal">({levels.length})</span></span>
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
                    <button onClick={() => newLevel.price && addLevel(newLevel.price, newLevel.label)} disabled={!instKey}
                      className="px-2 rounded-lg bg-brand-600 hover:bg-brand-700 text-white disabled:opacity-40"><Plus className="w-3.5 h-3.5" /></button>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {LEVEL_COLORS.map((c) => (
                      <button key={c} onClick={() => setNewLevel((s) => ({ ...s, color: c }))}
                        className={`w-5 h-5 rounded ${newLevel.color === c ? 'ring-2 ring-white/70' : ''}`} style={{ background: c }} />
                    ))}
                    <span className="text-[10px] text-gray-600 ml-auto">or click the chart</span>
                  </div>
                </div>
                {!levels.length ? (
                  <div className="px-3 py-6 text-center text-gray-600 text-xs">No levels saved for this instrument yet.</div>
                ) : (
                  <div className="max-h-[300px] overflow-y-auto divide-y divide-surface-3/50">
                    {levels.map((l) => (
                      <div key={l.id} className="px-3 py-2">
                        <div className="flex items-center gap-2">
                          <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: l.color }} />
                          <span className="text-xs text-gray-200 font-semibold">{fmtNum(l.price)}</span>
                          <span className="text-[11px] text-gray-500 truncate">{l.label}</span>
                          <span className={`ml-auto px-1.5 py-0.5 rounded border text-[9px] font-bold ${stateTone(l.state)}`}>{l.state}</span>
                          <button onClick={() => delLevel(l.id)} className="text-gray-600 hover:text-red-400"><Trash2 className="w-3.5 h-3.5" /></button>
                        </div>
                        <div className="text-[10px] text-gray-500 mt-0.5">
                          {l.distance_pct > 0 ? `price ${l.distance_pct}% above` : l.distance_pct < 0 ? `price ${Math.abs(l.distance_pct)}% below` : 'at the level'} · {l.approach}
                        </div>
                        <div className="text-[10px] text-gray-600">
                          {l.touches ? `${l.touches} touch(es) · last ${l.last_touch}` : 'never touched in this range'}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

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
    </div>
  );
}
