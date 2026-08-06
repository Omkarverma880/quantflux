import React, { useState, useEffect, useCallback } from 'react';
import {
  Radar, Loader2, AlertCircle, Play, Download, Info, TrendingUp, TrendingDown,
  ShieldAlert, X, ChevronRight, FlaskConical, History, Activity,
} from 'lucide-react';
import { api } from '../../api';

const sel = 'bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';
const NUM = (v, d = 2) => (v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
const HORIZONS = [['intraday', 'Intraday'], ['swing', 'Swing'], ['positional', 'Positional'], ['monthly', 'Monthly']];
const TOPN = [2, 5, 10, 20, 50, 100];
const gradeColor = (g) => ({ Low: '#22c55e', Moderate: '#eab308', High: '#f97316', Severe: '#ef4444' }[g] || '#6b7280');
const scoreColor = (s) => (s >= 85 ? '#22c55e' : s >= 75 ? '#4ade80' : s >= 65 ? '#a3e635' : s >= 50 ? '#eab308' : '#6b7280');

function DirBadge({ d }) {
  if (d === 'long') return <span className="inline-flex items-center gap-1 text-emerald-400"><TrendingUp className="w-3.5 h-3.5" />Long</span>;
  if (d === 'short') return <span className="inline-flex items-center gap-1 text-red-400"><TrendingDown className="w-3.5 h-3.5" />Short</span>;
  return <span className="text-gray-500">{d}</span>;
}

function Detail({ c, onClose }) {
  if (!c) return null;
  return (
    <div className="fixed inset-y-0 right-0 w-full max-w-md bg-surface-1 border-l border-surface-3 shadow-2xl z-50 overflow-y-auto">
      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-3 sticky top-0 bg-surface-1">
        <div className="flex items-center gap-2">
          <span className="text-lg font-bold text-gray-100">#{c.rank} {c.symbol}</span>
          <DirBadge d={c.direction} />
        </div>
        <button onClick={onClose} className="text-gray-400 hover:text-white"><X className="w-5 h-5" /></button>
      </div>
      <div className="p-4 space-y-4">
        <div className="grid grid-cols-3 gap-2 text-center">
          <div className="bg-surface-2 rounded-lg py-2"><div className="text-2xl font-extrabold" style={{ color: scoreColor(c.score) }}>{c.score}</div><div className="text-[10px] text-gray-500 uppercase">Score · {c.band}</div></div>
          <div className="bg-surface-2 rounded-lg py-2"><div className="text-2xl font-extrabold text-gray-100">{c.confidence}</div><div className="text-[10px] text-gray-500 uppercase">Confidence</div></div>
          <div className="bg-surface-2 rounded-lg py-2"><div className="text-lg font-bold" style={{ color: gradeColor(c.risk_grade) }}>{c.risk_grade}</div><div className="text-[10px] text-gray-500 uppercase">Analytical Risk</div></div>
        </div>

        <div className="bg-surface-2 rounded-lg p-3">
          <div className="text-xs font-semibold text-gray-300 mb-2">Research Plan <span className="text-gray-600">(indicative hypothesis — not an order)</span></div>
          <div className="grid grid-cols-2 gap-y-1.5 text-sm">
            <span className="text-gray-500">Indicative Entry</span><span className="text-right text-gray-100">₹{NUM(c.indicative_entry)}</span>
            <span className="text-gray-500">Thesis Invalidation</span><span className="text-right text-red-400">₹{NUM(c.invalidation)}</span>
            <span className="text-gray-500">First Target</span><span className="text-right text-emerald-400">₹{NUM(c.first_target)}</span>
            <span className="text-gray-500">Reward : Risk</span><span className="text-right text-gray-100">{c.reward_to_risk}</span>
            <span className="text-gray-500">ATR ({c.vol_regime})</span><span className="text-right text-gray-300">₹{NUM(c.atr)} · {c.atr_pct}%</span>
          </div>
        </div>

        <div>
          <div className="text-xs font-semibold text-emerald-400 mb-1.5">Supporting evidence</div>
          <div className="space-y-1">{(c.supporting || []).map((s, i) => <div key={i} className="text-xs text-gray-300">✅ {s}</div>)}
            {!(c.supporting || []).length && <div className="text-xs text-gray-600">—</div>}</div>
        </div>
        <div>
          <div className="text-xs font-semibold text-red-400 mb-1.5">Opposing evidence</div>
          <div className="space-y-1">{(c.opposing || []).map((s, i) => <div key={i} className="text-xs text-gray-400">⚠ {s}</div>)}
            {!(c.opposing || []).length && <div className="text-xs text-gray-600">—</div>}</div>
        </div>

        <div className="bg-surface-2 rounded-lg p-3">
          <div className="text-xs font-semibold text-gray-300 mb-2">Component scores</div>
          {Object.entries(c.components || {}).map(([k, v]) => (
            <div key={k} className="flex items-center gap-2 mb-1">
              <span className="text-[10px] text-gray-500 w-28 capitalize">{k.replace('_', ' ')}</span>
              <div className="flex-1 h-2 rounded bg-surface-3 overflow-hidden"><div className="h-full" style={{ width: `${v}%`, background: scoreColor(v) }} /></div>
              <span className="text-[10px] text-gray-400 w-7 text-right">{Math.round(v)}</span>
            </div>
          ))}
        </div>

        <p className="text-xs text-gray-400 leading-relaxed border-t border-surface-3 pt-3">{c.explanation}</p>
      </div>
    </div>
  );
}

function Stat({ label, value, color }) {
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-lg px-3 py-2 text-center">
      <div className="text-xl font-extrabold" style={{ color: color || '#e5e7eb' }}>{value}</div>
      <div className="text-[10px] text-gray-500 uppercase tracking-wide">{label}</div>
    </div>
  );
}

function CalibTable({ title, seg }) {
  const keys = Object.keys(seg || {});
  if (!keys.length) return null;
  return (
    <div>
      <div className="text-xs font-semibold text-gray-300 mb-1.5">{title}</div>
      <div className="overflow-x-auto"><table className="w-full text-xs whitespace-nowrap">
        <thead className="text-gray-500"><tr>{['Bucket', 'n', 'Target%', 'Win%', 'Avg R'].map((h) => <th key={h} className="px-2 py-1 text-right first:text-left font-medium">{h}</th>)}</tr></thead>
        <tbody>{keys.map((k) => (
          <tr key={k} className="border-t border-surface-3/40">
            <td className="px-2 py-1 text-left text-gray-300">{k}</td>
            <td className="px-2 py-1 text-right text-gray-400">{seg[k].n}</td>
            <td className="px-2 py-1 text-right" style={{ color: scoreColor(seg[k].target_rate) }}>{seg[k].target_rate}%</td>
            <td className="px-2 py-1 text-right text-gray-200">{seg[k].win_rate == null ? '—' : `${seg[k].win_rate}%`}</td>
            <td className="px-2 py-1 text-right" style={{ color: seg[k].avg_r >= 0 ? '#4ade80' : '#f87171' }}>{seg[k].avg_r}</td>
          </tr>
        ))}</tbody>
      </table></div>
    </div>
  );
}

function BacktestPanel({ bt, loading }) {
  if (loading) return <div className="bg-surface-2 border border-surface-3 rounded-xl p-12 text-center text-gray-500 text-sm flex items-center justify-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Running leakage-safe point-in-time backtest… (this scans full history read-only)</div>;
  if (!bt) return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-12 text-center text-gray-500 text-sm">
      <FlaskConical className="w-8 h-8 mx-auto mb-3 text-gray-600" />
      Runs each ranking decision <strong className="text-gray-300">point-in-time</strong> (only prior bars) and measures target-before-stop on forward bars. Set a horizon/universe and <strong className="text-gray-300">Run Backtest</strong>.
    </div>
  );
  const r = bt.report || {};
  if (!r.count) return <div className="bg-surface-2 border border-surface-3 rounded-xl p-8 text-center text-gray-500 text-sm">No directional decisions were generated over the tested window.</div>;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-xs text-gray-500 bg-surface-2 border border-surface-3 rounded-xl px-4 py-2">
        <span>Horizon <strong className="text-brand-400 capitalize">{bt.horizon}</strong></span>
        <span>Instruments <strong className="text-gray-200">{bt.instruments_tested}</strong></span>
        <span>Forward window <strong className="text-gray-200">{bt.window_bars} bars</strong></span>
        <span>Decisions <strong className="text-gray-200">{r.count}</strong></span>
        {r.insufficient && <span className="text-amber-400">⚠ small sample (&lt;30) — exploratory only</span>}
      </div>
      <div className="grid grid-cols-2 md:grid-cols-6 gap-2">
        <Stat label="Win rate" value={r.win_rate == null ? '—' : `${r.win_rate}%`} color={scoreColor(r.win_rate)} />
        <Stat label="Expectancy (R)" value={r.expectancy} color={r.expectancy >= 0 ? '#4ade80' : '#f87171'} />
        <Stat label="Target%" value={`${r.target_rate}%`} color="#4ade80" />
        <Stat label="Stop%" value={`${r.stop_rate}%`} color="#f87171" />
        <Stat label="Open/none%" value={`${Math.round(r.none / r.count * 100)}%`} />
        <Stat label="Avg hold" value={r.avg_hold == null ? '—' : `${r.avg_hold}b`} />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 bg-surface-2 border border-surface-3 rounded-xl p-4">
        <CalibTable title="Calibration by score band" seg={r.by_band} />
        <CalibTable title="By confidence bucket" seg={r.by_confidence} />
        <CalibTable title="By direction" seg={r.by_direction} />
      </div>
      <p className="text-[11px] text-gray-600 flex items-center gap-1"><Info className="w-3 h-3" /> {bt.note}</p>
    </div>
  );
}

function HistoryPanel({ snaps, onLoad, onRefresh }) {
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
      <div className="flex items-center gap-3 px-3 py-2 border-b border-surface-3">
        <span className="text-sm font-semibold text-gray-200">Stored Snapshots <span className="text-gray-500">({snaps.length})</span></span>
        <button onClick={onRefresh} className="text-xs text-gray-400 hover:text-white">Refresh</button>
        <span className="ml-auto text-[11px] text-gray-600">Immutable · reproducible</span>
      </div>
      {!snaps.length ? <div className="px-4 py-8 text-center text-gray-500 text-sm">No snapshots yet — run a scan to persist one.</div> : (
        <div className="overflow-x-auto max-h-[560px]"><table className="w-full text-xs whitespace-nowrap">
          <thead className="bg-surface-3 text-gray-300 sticky top-0"><tr>{['Snapshot', 'As-of', 'Horizon', 'Eligible', 'Warn', 'Restricted', 'Ruleset', ''].map((h) => <th key={h} className="px-2.5 py-2 font-semibold text-right first:text-left">{h}</th>)}</tr></thead>
          <tbody>{snaps.map((s) => (
            <tr key={s.snapshot_id} className="border-t border-surface-3/40 hover:bg-surface-3/10">
              <td className="px-2.5 py-1.5 text-left font-mono text-gray-300">{s.snapshot_id}</td>
              <td className="px-2.5 py-1.5 text-right text-gray-400">{(s.as_of || '').replace('T', ' ').slice(0, 19)}</td>
              <td className="px-2.5 py-1.5 text-right text-brand-400 capitalize">{s.horizon}</td>
              <td className="px-2.5 py-1.5 text-right text-emerald-400">{s.counts?.eligible ?? '—'}</td>
              <td className="px-2.5 py-1.5 text-right text-amber-400">{s.counts?.warning ?? '—'}</td>
              <td className="px-2.5 py-1.5 text-right text-gray-500">{s.counts?.restricted ?? '—'}</td>
              <td className="px-2.5 py-1.5 text-right text-gray-600">{s.ruleset_version}</td>
              <td className="px-2.5 py-1.5 text-right"><button onClick={() => onLoad(s.snapshot_id)} className="text-brand-400 hover:text-brand-300 font-semibold">Load →</button></td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </div>
  );
}

export default function QMIE() {
  const [cfg, setCfg] = useState(null);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [selected, setSelected] = useState(null);
  const [topN, setTopN] = useState(20);
  const [fDir, setFDir] = useState('all');
  const [fGrade, setFGrade] = useState('all');
  const [customText, setCustomText] = useState('');
  const [tab, setTab] = useState('scan');
  const [bt, setBt] = useState(null);
  const [btLoading, setBtLoading] = useState(false);
  const [snaps, setSnaps] = useState([]);
  const showErr = (m) => { setErr(m); setTimeout(() => setErr(''), 6000); };
  const patch = (k, v) => setCfg((c) => ({ ...c, [k]: v }));
  const scanSymbols = () => (cfg?.universe === 'custom'
    ? customText.split(/[\s,;\n]+/).map((s) => s.trim().toUpperCase()).filter(Boolean) : null);

  useEffect(() => { api.researchQMIEConfig().then((r) => { if (r.status === 'ok') setCfg(r.config); }).catch(() => setCfg({})); }, []);

  const runScan = useCallback(async () => {
    if (!cfg) return;
    setLoading(true); setErr('');
    try {
      const r = await api.researchQMIEScan(cfg, scanSymbols());
      if (r.status === 'ok') setData(r);
      else showErr(r.message || 'Scan failed');
    } catch (e) { showErr(e.message); } finally { setLoading(false); }
  }, [cfg, customText]);

  const runBacktest = useCallback(async () => {
    if (!cfg) return;
    setBtLoading(true); setErr('');
    try {
      const r = await api.researchQMIEBacktest(cfg, scanSymbols());
      if (r.status === 'ok') setBt(r);
      else showErr(r.message || 'Backtest failed');
    } catch (e) { showErr(e.message); } finally { setBtLoading(false); }
  }, [cfg, customText]);

  const loadSnaps = useCallback(async () => {
    try { const r = await api.researchQMIESnapshots(); if (r.status === 'ok') setSnaps(r.snapshots || []); } catch { /* noop */ }
  }, []);
  const loadSnap = async (id) => {
    try { const r = await api.researchQMIESnapshot(id); if (r.status === 'ok') { setData(r); setTab('scan'); } else showErr(r.message); } catch (e) { showErr(e.message); }
  };
  useEffect(() => { if (tab === 'history') loadSnaps(); }, [tab, loadSnaps]);

  const rows = (data?.results || [])
    .filter((r) => fDir === 'all' || r.direction === fDir)
    .filter((r) => fGrade === 'all' || r.risk_grade === fGrade)
    .slice(0, topN);

  const downloadCSV = () => {
    const cols = ['rank', 'symbol', 'direction', 'horizon', 'score', 'confidence', 'risk_grade', 'indicative_entry', 'invalidation', 'first_target', 'reward_to_risk', 'rel_strength_excess', 'median_value', 'state'];
    const esc = (v) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const lines = [cols.join(',')].concat(rows.map((r) => cols.map((c) => esc(r[c])).join(',')));
    const blob = new Blob([`# ${data?.disclaimer || ''}\n`, lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `qmie_${data?.horizon}.csv`; a.click();
  };

  if (!cfg) return <div className="p-6 text-gray-500 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>;

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-[1600px] mx-auto">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Radar className="w-5 h-5 text-brand-400" />
            <h1 className="text-xl font-bold text-gray-100 tracking-wide">Quantum Market Intelligence Engine</h1>
            <span className="px-2 py-0.5 rounded-full bg-brand-600/15 text-brand-400 text-xs font-semibold border border-brand-500/20">Research-10 · Read-only · No orders</span>
          </div>
          <p className="text-gray-500 text-sm mt-0.5">Ranked, explainable research candidates across horizons — evidence-first, with analytical risk & provenance.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select value={cfg.horizon} onChange={(e) => patch('horizon', e.target.value)} className={sel}>{HORIZONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
          <select value={cfg.universe} onChange={(e) => patch('universe', e.target.value)} className={sel}><option value="fno">F&amp;O universe</option><option value="custom">Custom symbols</option></select>
          <select value={cfg.direction} onChange={(e) => patch('direction', e.target.value)} className={sel}><option value="long_only">Long only</option><option value="long_short">Long &amp; Short</option></select>
          <select value={cfg.max_instruments} onChange={(e) => patch('max_instruments', parseInt(e.target.value))} className={sel}>{[20, 40, 80, 150, 250].map((n) => <option key={n} value={n}>{n} scanned</option>)}</select>
          {tab === 'backtest' ? (
            <button onClick={runBacktest} disabled={btLoading} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">{btLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <FlaskConical className="w-4 h-4" />} Run Backtest</button>
          ) : tab === 'scan' ? (
            <button onClick={runScan} disabled={loading} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">{loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />} Run Scan</button>
          ) : null}
        </div>
      </div>

      <div className="flex gap-1 border-b border-surface-3">
        {[['scan', 'Live Scan', Radar], ['backtest', 'Backtest', FlaskConical], ['history', 'Snapshots', History]].map(([id, label, Icon]) => (
          <button key={id} onClick={() => setTab(id)} className={`flex items-center gap-1.5 px-4 py-2 text-sm font-semibold border-b-2 -mb-px transition ${tab === id ? 'border-brand-500 text-brand-400' : 'border-transparent text-gray-400 hover:text-gray-200'}`}><Icon className="w-4 h-4" /> {label}</button>
        ))}
      </div>

      {cfg.universe === 'custom' && (
        <input value={customText} onChange={(e) => setCustomText(e.target.value)} placeholder="Enter symbols (comma / space separated) e.g. RELIANCE, TCS, INFY" className={`w-full ${sel}`} />
      )}

      {err && <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-red-400 text-sm"><AlertCircle className="w-4 h-4" /> {err}</div>}

      {tab === 'scan' && data?.market_context && (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm bg-surface-2 border border-surface-3 rounded-xl px-4 py-2">
          <span className="text-gray-500 flex items-center gap-1"><Activity className="w-3.5 h-3.5" /> Market context</span>
          <span className="text-gray-400">Breadth <strong className={data.market_context.bias === 'bullish' ? 'text-emerald-400' : data.market_context.bias === 'bearish' ? 'text-red-400' : 'text-gray-300'}>{data.market_context.regime}</strong></span>
          {data.market_context.breadth?.pct_above_ema50 != null && <span className="text-gray-400">%&gt;50EMA <strong className="text-gray-200">{data.market_context.breadth.pct_above_ema50}%</strong></span>}
          {data.market_context.breadth?.ad_ratio != null && <span className="text-gray-400">A/D <strong className="text-gray-200">{data.market_context.breadth.ad_ratio}</strong></span>}
          <span className="text-gray-400">NIFTY PCR <strong className="text-gray-200">{data.market_context.pcr ?? '—'}</strong></span>
          <span className="text-gray-400">Max Pain <strong className="text-gray-200">{data.market_context.max_pain ?? '—'}</strong></span>
        </div>
      )}

      {tab === 'scan' && data && (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm bg-surface-2 border border-surface-3 rounded-xl px-4 py-2.5">
          {data.stored && <span className="text-[10px] px-2 py-0.5 rounded-full bg-brand-600/15 text-brand-400 border border-brand-500/20">stored snapshot</span>}
          <span className="text-gray-400">Horizon <strong className="text-brand-400 capitalize">{data.horizon}</strong></span>
          <span className="text-gray-400">Scanned <strong className="text-gray-100">{data.counts.scanned}</strong></span>
          <span className="text-gray-400">Eligible <strong className="text-emerald-400">{data.counts.eligible}</strong></span>
          <span className="text-gray-400">Warning <strong className="text-amber-400">{data.counts.warning}</strong></span>
          <span className="text-gray-400">Restricted <strong className="text-gray-500">{data.counts.restricted}</strong></span>
          <span className="text-gray-400">Benchmark <strong className="text-gray-300">{data.benchmark}</strong></span>
          <span className="text-gray-500 text-xs ml-auto">{data.as_of_display} · {data.snapshot_id}</span>
        </div>
      )}

      {/* controls row */}
      {tab === 'scan' && data && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-gray-500">Top</span>
          {TOPN.map((n) => <button key={n} onClick={() => setTopN(n)} className={`px-2 py-1 rounded border ${topN === n ? 'bg-brand-600/20 text-brand-400 border-brand-500/40' : 'bg-surface-3 text-gray-400 border-surface-4'}`}>{n}</button>)}
          <select value={fDir} onChange={(e) => setFDir(e.target.value)} className={`${sel} ml-2 py-1`}><option value="all">All directions</option><option value="long">Long</option><option value="short">Short</option></select>
          <select value={fGrade} onChange={(e) => setFGrade(e.target.value)} className={`${sel} py-1`}><option value="all">All risk</option>{['Low', 'Moderate', 'High', 'Severe'].map((g) => <option key={g} value={g}>{g}</option>)}</select>
          <button onClick={downloadCSV} disabled={!rows.length} className="ml-auto flex items-center gap-1.5 px-2.5 py-1 rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-40"><Download className="w-3.5 h-3.5" /> CSV</button>
        </div>
      )}

      {tab === 'scan' && !data && !loading && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-12 text-center text-gray-500 text-sm">
          <Radar className="w-8 h-8 mx-auto mb-3 text-gray-600" />
          Pick a horizon and universe, then <strong className="text-gray-300">Run Scan</strong> to rank research opportunities.
        </div>
      )}
      {tab === 'scan' && loading && <div className="bg-surface-2 border border-surface-3 rounded-xl p-12 text-center text-gray-500 text-sm flex items-center justify-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Scanning universe (read-only)…</div>}

      {tab === 'scan' && data && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs whitespace-nowrap">
              <thead className="bg-surface-3 text-gray-300"><tr>
                {['#', 'Instrument', 'Dir', 'Score', 'Conf', 'Risk', 'Entry', 'Invalidation', 'Target', 'R:R', 'Rel.Str', 'Liquidity', 'Fresh', ''].map((h) => <th key={h} className="px-2.5 py-2 font-semibold text-right first:text-center">{h}</th>)}
              </tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.symbol} onClick={() => setSelected(r)} className="border-t border-surface-3/40 hover:bg-surface-3/20 cursor-pointer">
                    <td className="px-2.5 py-1.5 text-center text-gray-500 font-bold">{r.rank}</td>
                    <td className="px-2.5 py-1.5 text-left font-semibold text-gray-100">{r.symbol}{r.state === 'eligible_warning' && <span className="ml-1 text-[9px] px-1 rounded bg-amber-500/15 text-amber-400 border border-amber-500/30">warn</span>}</td>
                    <td className="px-2.5 py-1.5 text-right"><DirBadge d={r.direction} /></td>
                    <td className="px-2.5 py-1.5 text-right font-bold" style={{ color: scoreColor(r.score) }}>{r.score}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-300">{r.confidence}</td>
                    <td className="px-2.5 py-1.5 text-right font-semibold" style={{ color: gradeColor(r.risk_grade) }}>{r.risk_grade}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-200">₹{NUM(r.indicative_entry)}</td>
                    <td className="px-2.5 py-1.5 text-right text-red-400">₹{NUM(r.invalidation)}</td>
                    <td className="px-2.5 py-1.5 text-right text-emerald-400">₹{NUM(r.first_target)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-200">{r.reward_to_risk}</td>
                    <td className="px-2.5 py-1.5 text-right" style={{ color: (r.rel_strength_excess || 0) >= 0 ? '#4ade80' : '#f87171' }}>{r.rel_strength_excess == null ? '—' : `${r.rel_strength_excess}%`}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-400">{r.median_value ? `₹${(r.median_value / 1e7).toFixed(1)}Cr` : '—'}</td>
                    <td className="px-2.5 py-1.5 text-right">{r.fresh ? <span className="text-emerald-500">●</span> : <span className="text-amber-500">●</span>}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-600"><ChevronRight className="w-4 h-4 inline" /></td>
                  </tr>
                ))}
                {!rows.length && <tr><td colSpan={14} className="px-4 py-8 text-center text-gray-500">No candidates match the current filters.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === 'backtest' && <BacktestPanel bt={bt} loading={btLoading} />}
      {tab === 'history' && <HistoryPanel snaps={snaps} onLoad={loadSnap} onRefresh={loadSnaps} />}

      <p className="text-[11px] text-gray-600 flex items-center gap-1"><ShieldAlert className="w-3 h-3" /> {data?.disclaimer || 'QMIE research only — no order is created, transmitted, or executed.'} Entry / invalidation / target are analytical hypotheses. Phase-1 engines: trend, relative strength, volume, volatility, liquidity, market breadth. <Info className="w-3 h-3" /></p>

      <Detail c={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
