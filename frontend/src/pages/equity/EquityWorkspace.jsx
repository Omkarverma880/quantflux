import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  LayoutGrid, Play, Loader2, AlertCircle, Download, RefreshCw, Save, Send,
  Check, Minus, BookOpen, SlidersHorizontal, ChevronDown, ChevronUp,
} from 'lucide-react';
import { api } from '../../api';
import WatchlistBar from '../../components/WatchlistBar';

const sel = 'bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';
const lbl = 'block text-[11px] text-gray-500 uppercase tracking-wide mb-1';
const NUM = (v, d = 2) => (v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
const COMPACT = (v) => {
  if (v == null) return '—';
  const n = Number(v);
  if (n >= 1e7) return `${(n / 1e7).toFixed(2)}Cr`;
  if (n >= 1e5) return `${(n / 1e5).toFixed(2)}L`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return n.toLocaleString('en-IN');
};
const TF_LABEL = {
  minute: '1 min', '3minute': '3 min', '5minute': '5 min', '10minute': '10 min',
  '15minute': '15 min', '30minute': '30 min', '60minute': '1 hour',
  day: 'Daily', week: 'Weekly', month: 'Monthly',
};

const sortRows = (arr, s) => {
  if (!s || !s.key) return arr;
  const d = s.dir === 'asc' ? 1 : -1;
  const val = (r) => (s.strategy ? ({ true: 2, false: 1, null: 0 }[String(r.signals?.[s.key]?.ok)] ?? 0) : r[s.key]);
  return [...arr].sort((a, b) => {
    const av = val(a); const bv = val(b);
    if (av == null && bv == null) return 0; if (av == null) return 1; if (bv == null) return -1;
    if (typeof av === 'string' && typeof bv === 'string') return av < bv ? -d : av > bv ? d : 0;
    return (Number(av) - Number(bv)) * d;
  });
};
const nextSort = (s, k, strategy) => (s.key === k
  ? { key: k, dir: s.dir === 'asc' ? 'desc' : 'asc', strategy }
  : { key: k, dir: 'desc', strategy });

function Cell({ s }) {
  if (!s) return <span className="text-gray-700">·</span>;
  if (s.ok === true) {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 font-semibold text-[10px] border border-emerald-500/25">
        <Check className="w-3 h-3" />{s.label}
      </span>
    );
  }
  if (s.ok === false) return <Minus className="w-3.5 h-3.5 text-gray-600 inline" />;
  return <span className="text-[10px] text-gray-700">n/a</span>;
}

function ScoreBar({ score, applicable }) {
  const pctFill = applicable ? (score / applicable) * 100 : 0;
  const tone = pctFill >= 75 ? 'bg-emerald-500' : pctFill >= 50 ? 'bg-brand-500' : pctFill >= 25 ? 'bg-amber-500' : 'bg-gray-600';
  const text = pctFill >= 75 ? 'text-emerald-400' : pctFill >= 50 ? 'text-brand-300' : pctFill >= 25 ? 'text-amber-400' : 'text-gray-500';
  return (
    <div className="flex items-center justify-end gap-2">
      <div className="w-14 h-1.5 rounded-full bg-surface-4 overflow-hidden"><div className={`h-full ${tone}`} style={{ width: `${pctFill}%` }} /></div>
      <span className={`font-bold tabular-nums ${text}`}>{score}<span className="text-gray-600 font-normal">/{applicable}</span></span>
    </div>
  );
}

export default function EquityWorkspace() {
  const [meta, setMeta] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [universe, setUniverse] = useState([]);
  const [pick, setPick] = useState({ mode: 'all', symbol: null, symbols: null });
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(''); const [msg, setMsg] = useState('');
  const [sort, setSort] = useState({ key: 'score', dir: 'desc', strategy: false });
  const [showLegend, setShowLegend] = useState(true);
  const [showTuning, setShowTuning] = useState(false);
  const [detail, setDetail] = useState(null);        // {row, key}
  const liveRef = useRef(null);

  const showErr = (m) => { setErr(m); setTimeout(() => setErr(''), 7000); };
  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(''), 3000); };
  const patch = (k, v) => setCfg((c) => ({ ...c, [k]: v }));

  useEffect(() => {
    api.wsMeta().then((r) => { if (r.status === 'ok') { setMeta(r); setCfg(r.config); } }).catch(() => showErr('Could not load the workspace'));
    api.researchPMVwapEquityUniverse?.().then((r) => { if (r?.status === 'ok') setUniverse(r.stocks || []); }).catch(() => {});
  }, []);

  const symbolsFor = useCallback(() => {
    if (pick.mode === 'single') return pick.symbol ? [pick.symbol.trim().toUpperCase()] : [];
    if (pick.mode === 'watchlist') return pick.symbols || [];
    return universe.map((u) => (typeof u === 'string' ? u : u.name)).filter(Boolean);
  }, [pick, universe]);

  const runScan = useCallback(async (withTelegram = false) => {
    const syms = symbolsFor();
    if (!syms.length) { showErr('Pick a watchlist or type a stock (e.g. BSE) first'); return; }
    setLoading(true); setErr('');
    try {
      const r = await api.wsScan({ symbols: syms, overrides: cfg, telegram: withTelegram });
      if (r.status === 'ok') {
        setData(r);
        if (r.telegram_sent) flash('Scan sent to Telegram');
        if (r.telegram_error) showErr(r.telegram_error);
      } else showErr(r.message || 'Scan failed');
    } catch (e) { showErr(e.message); } finally { setLoading(false); }
  }, [cfg, symbolsFor]);

  // Live scan is opt-in — the manual run is the main path.
  useEffect(() => {
    if (!cfg?.live_scan) return undefined;
    liveRef.current = setInterval(() => runScan(false), Math.max(15, Number(cfg.refresh_secs) || 60) * 1000);
    return () => clearInterval(liveRef.current);
  }, [cfg?.live_scan, cfg?.refresh_secs, runScan]);

  const saveCfg = async () => {
    try { const r = await api.wsConfigSave(cfg); if (r.status === 'ok') { setCfg(r.config); flash('Defaults saved'); } }
    catch (e) { showErr(e.message); }
  };

  const strategies = data?.strategies || meta?.strategies || [];
  const rows = data?.rows || [];
  const enabled = cfg?.enabled?.length ? cfg.enabled : (meta?.strategies || []).map((s) => s.key);
  const toggleStrategy = (key) => {
    const all = (meta?.strategies || []).map((s) => s.key);
    const cur = new Set(cfg.enabled?.length ? cfg.enabled : all);
    if (cur.has(key)) cur.delete(key); else cur.add(key);
    const next = all.filter((k) => cur.has(k));
    patch('enabled', next.length === all.length ? [] : next);
  };

  const downloadCSV = () => {
    if (!rows.length) return;
    const keys = data.keys || [];
    const cols = ['underlying', 'ltp', 'change_pct', 'volume', 'oi', 'is_fno', 'score', 'applicable', ...keys];
    const esc = (v) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const val = (r, c) => (keys.includes(c)
      ? (r.signals?.[c]?.ok === true ? r.signals[c].label : r.signals?.[c]?.ok === false ? 'no' : 'n/a')
      : r[c]);
    const lines = [cols.join(',')].concat(sortRows(rows, sort).map((r) => cols.map((c) => esc(val(r, c))).join(',')));
    const blob = new Blob([lines.join('\n') + '\n'], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
    a.download = `equity_workspace_${data.timeframe}_${data.date}.csv`; a.click();
  };

  if (!cfg || !meta) return <div className="p-6 text-gray-500 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Loading workspace…</div>;
  const num = (k, min = 0, step = 1) => <input type="number" min={min} step={step} value={cfg[k] ?? ''} onChange={(e) => patch(k, e.target.value)} className={`w-full ${sel}`} />;

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-[1800px] mx-auto">
      {/* ── header + strategy map ── */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <LayoutGrid className="w-6 h-6 text-brand-400" />
            <h1 className="text-xl font-bold text-gray-100">Equity Strategy Workspace</h1>
            <span className="text-[11px] px-2 py-0.5 rounded-full bg-brand-500/15 text-brand-300 border border-brand-500/25">Smart Scan</span>
          </div>
          <p className="text-sm text-gray-500 mt-0.5">One run, every strategy. See which of your {(meta.strategies || []).length} equity strategies each stock satisfies right now — score, sort, decide. Screener only: no entry, no target, no orders.</p>
        </div>
        <button onClick={() => setShowLegend((v) => !v)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white">
          <BookOpen className="w-4 h-4 text-brand-400" /> Strategy map
          {showLegend ? <ChevronUp className="w-4 h-4 text-gray-500" /> : <ChevronDown className="w-4 h-4 text-gray-500" />}
        </button>
      </div>

      {showLegend && (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-2">
          {(meta.strategies || []).map((s) => {
            const on = enabled.includes(s.key);
            return (
              <div key={s.key}
                className={`bg-surface-2 border rounded-xl px-3 py-2.5 flex gap-2.5 transition ${on ? 'border-surface-3' : 'border-surface-3/50 opacity-45'}`}>
                <span className="mt-0.5 shrink-0 w-10 h-7 flex items-center justify-center text-[11px] font-bold rounded-lg bg-brand-500/15 text-brand-300 border border-brand-500/25">{s.short}</span>
                <div className="min-w-0">
                  <div className="text-xs font-semibold text-gray-200 leading-tight">
                    {s.name}
                    {s.intraday_only && <span className="ml-1.5 text-[9px] px-1 py-0.5 rounded bg-amber-500/10 text-amber-400/90 border border-amber-500/20 align-middle">intraday</span>}
                  </div>
                  <div className="text-[10px] text-gray-600 mt-0.5">{s.source}</div>
                  <div className="text-[11px] text-gray-500 leading-snug mt-1">{s.rule}</div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {err && <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-red-400 text-sm"><AlertCircle className="w-4 h-4" /> {err}</div>}
      {msg && <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-4 py-2 text-emerald-400 text-sm">{msg}</div>}

      {/* ── controls ── */}
      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[300px]"><WatchlistBar universe={universe} count={universe.length} onChange={setPick} /></div>
          <div>
            <label className={lbl}>Candle</label>
            <select value={cfg.timeframe} onChange={(e) => patch('timeframe', e.target.value)} className={sel}>
              {(meta.timeframes || []).map((t) => <option key={t} value={t}>{TF_LABEL[t] || t}</option>)}
            </select>
          </div>
          <button onClick={() => runScan(false)} disabled={loading} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />} Smart Scan
          </button>
          <button onClick={() => runScan(true)} disabled={loading} title="Run the scan and push the result to Telegram now" className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-50">
            <Send className="w-3.5 h-3.5" /> Scan &amp; send
          </button>
          <button onClick={saveCfg} className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white"><Save className="w-3.5 h-3.5" /> Save defaults</button>
          <button onClick={() => setShowTuning((v) => !v)} className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white"><SlidersHorizontal className="w-3.5 h-3.5" /> Tuning</button>
        </div>

        {/* strategy toggles */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] text-gray-500 uppercase tracking-wide">Strategies</span>
          {(meta.strategies || []).map((s) => {
            const on = enabled.includes(s.key);
            return (
              <button key={s.key} onClick={() => toggleStrategy(s.key)} title={s.rule}
                className={`px-2.5 py-1 text-xs rounded-lg border font-semibold transition ${on ? 'bg-brand-600/20 text-brand-300 border-brand-500/40' : 'bg-surface-3 text-gray-500 border-surface-4 hover:text-gray-300'}`}>
                {s.short} <span className="font-normal">{s.name}</span>
              </button>
            );
          })}
        </div>

        {/* telegram + live scan */}
        <div className="flex flex-wrap items-center gap-4 pt-2 border-t border-surface-3">
          <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer" title="Push the scan result to Telegram on every run.">
            <input type="checkbox" checked={cfg.telegram_alerts} onChange={(e) => patch('telegram_alerts', e.target.checked)} className="accent-brand-500" /> Telegram notification
          </label>
          <div className="flex items-center gap-2 text-xs text-gray-400">Bot
            <select value={cfg.telegram_bot} onChange={(e) => patch('telegram_bot', e.target.value)} className={`${sel} py-1`}>
              <option value="a">Bot A</option><option value="b">Bot B</option>
            </select>
          </div>
          <div className="flex items-center gap-2 text-xs text-gray-400">Send top
            <input type="number" min={1} value={cfg.telegram_top_n} onChange={(e) => patch('telegram_top_n', e.target.value)} className={`${sel} py-1 w-16`} />
            scoring ≥
            <input type="number" min={0} value={cfg.telegram_min_score} onChange={(e) => patch('telegram_min_score', e.target.value)} className={`${sel} py-1 w-16`} />
          </div>
          <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer ml-auto" title="Optional — re-runs the whole scan on a timer. The manual run is the main path.">
            <input type="checkbox" checked={cfg.live_scan} onChange={(e) => patch('live_scan', e.target.checked)} className="accent-brand-500" /> Live scan
          </label>
          {cfg.live_scan && (
            <div className="flex items-center gap-2 text-xs text-gray-400">every
              <select value={cfg.refresh_secs} onChange={(e) => patch('refresh_secs', Number(e.target.value))} className={`${sel} py-1`}>
                {[[60, '1 min'], [300, '5 min'], [900, '15 min'], [1800, '30 min'], [3600, '1 hour']].map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </div>
          )}
        </div>

        {showTuning && (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 pt-2 border-t border-surface-3">
            <div><label className={lbl}>Hammer lookback</label>{num('hammer_lookback', 2, 1)}</div>
            <div><label className={lbl}>Red before</label>{num('hammer_red_before', 0, 1)}</div>
            <div><label className={lbl}>EMA fast</label>{num('ema_fast', 2, 1)}</div>
            <div><label className={lbl}>EMA slow</label>{num('ema_slow', 3, 1)}</div>
            <div><label className={lbl}>EMA touch %</label>{num('ema_touch_pct', 0, 0.1)}</div>
            <div><label className={lbl}>ADX period</label>{num('adx_period', 2, 1)}</div>
            <div><label className={lbl}>ADX threshold</label>{num('adx_threshold', 0, 1)}</div>
            <div><label className={lbl}>CV lookback</label>{num('cv_lookback', 2, 1)}</div>
            <div><label className={lbl}>First-hour days</label>{num('first_hour_days', 1, 1)}</div>
            <div><label className={lbl}>PM-VWAP buffer %</label>{num('pmvwap_buffer_pct', 0, 0.05)}</div>
            <div><label className={lbl}>Min score shown</label>{num('min_score', 0, 1)}</div>
            <div><label className={lbl}>Max stocks (0 = all)</label>{num('max_stocks', 0, 10)}</div>
          </div>
        )}
      </div>

      {/* ── summary ── */}
      {data && (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm bg-surface-2 border border-surface-3 rounded-xl px-4 py-2.5">
          <span className="text-gray-400">Scanned <strong className="text-gray-100">{data.scanned}</strong>{data.requested !== data.scanned && <span className="text-gray-600"> of {data.requested}</span>}</span>
          <span className="text-gray-400">Candle <strong className="text-gray-100">{TF_LABEL[data.timeframe] || data.timeframe}</strong></span>
          <span className="text-gray-400">Max score <strong className="text-gray-100">{data.max_score}</strong></span>
          <span className="text-gray-400">Clearing ≥ half <strong className="text-emerald-400">{rows.filter((r) => r.applicable && r.score / r.applicable >= 0.5).length}</strong></span>
          <span className="text-gray-500 text-xs">{data.generated_at?.slice(-8)} · {data.took_secs}s</span>
          {data.unresolved?.length > 0 && <span className="text-amber-400/80 text-xs" title={data.unresolved.join(', ')}>{data.unresolved.length} symbol(s) not found</span>}
          <div className="ml-auto flex items-center gap-2">
            <button onClick={() => runScan(false)} disabled={loading} className="text-gray-400 hover:text-white disabled:opacity-40"><RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /></button>
            <button onClick={downloadCSV} disabled={!rows.length} className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-40"><Download className="w-3.5 h-3.5" /> CSV</button>
          </div>
        </div>
      )}

      {/* ── the grid ── */}
      <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
        {!data ? (
          <div className="px-4 py-14 text-center text-gray-500 text-sm">Pick a watchlist — or switch to <strong className="text-gray-300">Single Stock</strong> and type one, e.g. <strong className="text-brand-300">BSE</strong> — then hit <strong className="text-gray-300">Smart Scan</strong>.</div>
        ) : !rows.length ? (
          <div className="px-4 py-14 text-center text-gray-500 text-sm">Nothing to show. {cfg.min_score > 0 ? `No stock scored ${cfg.min_score} or more — lower “Min score shown” in Tuning.` : 'No stocks resolved from that selection.'}</div>
        ) : (
          <div className="overflow-x-auto"><table className="w-full text-xs whitespace-nowrap">
            <thead className="bg-surface-3 text-gray-300">
              <tr>
                {[['Stock', 'underlying', 'l'], ['LTP', 'ltp', 'r'], ['Chg %', 'change_pct', 'r'], ['Volume', 'volume', 'r'], ['OI', 'oi', 'r']].map(([label, k, a]) => (
                  <th key={k} onClick={() => setSort((s) => nextSort(s, k, false))}
                    className={`px-2.5 py-2 font-semibold cursor-pointer select-none ${a === 'l' ? 'text-left' : 'text-right'} ${sort.key === k ? 'text-brand-300' : ''}`}>
                    {label}{sort.key === k ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                  </th>
                ))}
                {strategies.map((s) => (
                  <th key={s.key} onClick={() => setSort((st) => nextSort(st, s.key, true))} title={`${s.name} — ${s.rule}`}
                    className={`px-2 py-2 font-semibold cursor-pointer select-none text-center ${sort.key === s.key ? 'text-brand-300' : ''}`}>
                    {s.short}{sort.key === s.key ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                  </th>
                ))}
                <th onClick={() => setSort((s) => nextSort(s, 'score', false))}
                  className={`px-2.5 py-2 font-semibold cursor-pointer select-none text-right ${sort.key === 'score' ? 'text-brand-300' : ''}`}>
                  Score{sort.key === 'score' ? (sort.dir === 'asc' ? ' ▲' : ' ▼') : ''}
                </th>
              </tr>
            </thead>
            <tbody>
              {sortRows(rows, sort).map((r) => (
                <tr key={r.underlying} className="border-t border-surface-3/40 hover:bg-surface-3/10">
                  <td className="px-2.5 py-1.5 text-left">
                    <span className="text-brand-300 font-semibold">{r.underlying}</span>
                    {r.is_fno && <span className="ml-1 text-[9px] px-1 rounded bg-surface-3 text-gray-500 border border-surface-4">F&amp;O</span>}
                  </td>
                  <td className="px-2.5 py-1.5 text-right text-gray-200">{r.ltp == null ? '—' : `₹${NUM(r.ltp)}`}</td>
                  <td className={`px-2.5 py-1.5 text-right font-medium ${r.change_pct == null ? 'text-gray-500' : r.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{r.change_pct == null ? '—' : `${r.change_pct > 0 ? '+' : ''}${NUM(r.change_pct)}%`}</td>
                  <td className="px-2.5 py-1.5 text-right text-gray-400" title={r.volume ? Number(r.volume).toLocaleString('en-IN') : ''}>{COMPACT(r.volume)}</td>
                  <td className="px-2.5 py-1.5 text-right text-gray-400" title={r.future || ''}>{r.is_fno ? COMPACT(r.oi) : <span className="text-gray-700">—</span>}</td>
                  {strategies.map((s) => (
                    <td key={s.key} className="px-2 py-1.5 text-center cursor-pointer" title={`${s.name}: ${r.signals?.[s.key]?.detail || ''}`}
                      onClick={() => setDetail({ row: r, key: s.key })}>
                      <Cell s={r.signals?.[s.key]} />
                    </td>
                  ))}
                  <td className="px-2.5 py-1.5 text-right"><ScoreBar score={r.score} applicable={r.applicable} /></td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </div>

      {/* ── why-it-passed drawer ── */}
      {detail && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => setDetail(null)}>
          <div className="bg-surface-1 border border-surface-3 rounded-xl w-full max-w-2xl max-h-[80vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-4 py-3 border-b border-surface-3">
              <div className="flex items-center gap-2 text-gray-100 font-semibold">
                {detail.row.underlying}
                <span className="text-xs text-gray-500">₹{NUM(detail.row.ltp)} · {TF_LABEL[data.timeframe] || data.timeframe} · score {detail.row.score}/{detail.row.applicable}</span>
              </div>
              <button onClick={() => setDetail(null)} className="text-gray-500 hover:text-white text-sm">Close</button>
            </div>
            <div className="overflow-y-auto divide-y divide-surface-3/50">
              {strategies.map((s) => {
                const v = detail.row.signals?.[s.key];
                return (
                  <div key={s.key} className={`px-4 py-2.5 ${s.key === detail.key ? 'bg-brand-600/10' : ''}`}>
                    <div className="flex items-center gap-2">
                      <Cell s={v} />
                      <span className="text-sm text-gray-200 font-medium">{s.name}</span>
                      <span className="text-[11px] text-gray-600">{s.source}</span>
                    </div>
                    <div className="text-[11px] text-gray-500 mt-0.5">{v?.detail || '—'}</div>
                    <div className="text-[11px] text-gray-600 mt-0.5 italic">{s.rule}</div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
