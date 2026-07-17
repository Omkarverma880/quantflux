import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  RefreshCw, Loader2, AlertCircle, Pause, Play, Settings2, TrendingUp,
  TrendingDown, Minus, ArrowUp, ArrowDown, Gauge, Layers, BarChart3, ChevronsUpDown,
} from 'lucide-react';
import { api } from '../../api';

const INR = (v, d = 2) => (v ?? 0).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtVol = (v) => {
  if (!v) return '—';
  if (v >= 1e7) return `${(v / 1e7).toFixed(2)}Cr`;
  if (v >= 1e5) return `${(v / 1e5).toFixed(2)}L`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return `${v}`;
};

const selCls = 'bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';

const BIAS = {
  Bullish: { c: 'text-emerald-400', bg: 'bg-emerald-500/10', bd: 'border-emerald-500/40', ring: 'ring-emerald-500/30', Icon: TrendingUp },
  Bearish: { c: 'text-red-400', bg: 'bg-red-500/10', bd: 'border-red-500/40', ring: 'ring-red-500/30', Icon: TrendingDown },
  Neutral: { c: 'text-amber-400', bg: 'bg-amber-500/10', bd: 'border-amber-500/40', ring: 'ring-amber-500/30', Icon: Minus },
};

function ChangePct({ v }) {
  const n = Number(v) || 0;
  return <span className={n > 0 ? 'text-emerald-400' : n < 0 ? 'text-red-400' : 'text-gray-400'}>{n > 0 ? '+' : ''}{n.toFixed(2)}%</span>;
}

function TrendBadge({ t }) {
  if (t === 'Strong Bullish') return <span className="inline-flex items-center gap-1 text-emerald-400 font-semibold"><ArrowUp className="w-3.5 h-3.5" /> Strong Bullish</span>;
  if (t === 'Strong Bearish') return <span className="inline-flex items-center gap-1 text-red-400 font-semibold"><ArrowDown className="w-3.5 h-3.5" /> Strong Bearish</span>;
  if (t === 'Bullish') return <span className="text-emerald-400">▲ Bullish</span>;
  if (t === 'Bearish') return <span className="text-red-400">▼ Bearish</span>;
  return <span className="inline-flex items-center gap-1 text-amber-400"><Minus className="w-3.5 h-3.5" /> Neutral</span>;
}

/* Sentiment gauge card */
function SentimentCard({ title, icon: Icon, card }) {
  const bias = card?.bias || 'Neutral';
  const s = BIAS[bias] || BIAS.Neutral;
  const gauge = card?.gauge ?? 50;
  return (
    <div className={`rounded-xl p-4 border ${s.bd} ${s.bg} ring-1 ${s.ring}`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-gray-400">
          {Icon && <Icon className="w-3.5 h-3.5" />} {title}
        </div>
        <s.Icon className={`w-5 h-5 ${s.c}`} />
      </div>
      <div className={`text-2xl font-bold ${s.c}`}>{bias}</div>
      <div className="mt-2 h-2 rounded-full bg-surface-4 overflow-hidden">
        <div className={`h-full ${bias === 'Bullish' ? 'bg-emerald-500' : bias === 'Bearish' ? 'bg-red-500' : 'bg-amber-500'}`}
          style={{ width: `${gauge}%` }} />
      </div>
      <div className="flex items-center justify-between mt-2 text-[11px] text-gray-400">
        <span>Score <strong className={s.c}>{card?.score >= 0 ? '+' : ''}{(card?.score ?? 0).toFixed(3)}</strong></span>
        <span>Gauge <strong className="text-gray-200">{gauge}/100</strong></span>
      </div>
      {(card?.positive_score != null) && (
        <div className="flex items-center justify-between mt-1 text-[11px]">
          <span className="text-emerald-400">+{(card.positive_score).toFixed(3)}</span>
          <span className="text-red-400">{(card.negative_score).toFixed(3)}</span>
        </div>
      )}
      {card?.advancers != null && (
        <div className="mt-1 text-[11px] text-gray-500">{card.advancers} adv · {card.decliners} dec · {card.universe} stocks ({card.coverage_pct}% of index)</div>
      )}
      {card?.sectors != null && <div className="mt-1 text-[11px] text-gray-500">{card.sectors} sectors</div>}
    </div>
  );
}

/* Sortable table header */
function useSort(defaultKey, defaultDir = 'desc') {
  const [sort, setSort] = useState({ key: defaultKey, dir: defaultDir });
  const onSort = (key) => setSort((s) => s.key === key ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'desc' });
  const apply = (rows) => {
    const { key, dir } = sort;
    const m = dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = a[key], bv = b[key];
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * m;
      return String(av ?? '').localeCompare(String(bv ?? '')) * m;
    });
  };
  return { sort, onSort, apply };
}
function Th({ label, k, sort, onSort, align = 'right' }) {
  const active = sort.key === k;
  return (
    <th onClick={() => onSort(k)}
      className={`px-2 py-2 font-medium cursor-pointer select-none whitespace-nowrap hover:text-gray-200 text-${align} ${active ? 'text-brand-400' : 'text-gray-500'}`}>
      <span className="inline-flex items-center gap-0.5">{label}{active ? (sort.dir === 'asc' ? '▲' : '▼') : <ChevronsUpDown className="w-3 h-3 opacity-40" />}</span>
    </th>
  );
}

export default function NiftySentiment() {
  const [cfg, setCfg] = useState({ top_n: 10, refresh_interval: 5, enable_sector: true, enable_trend: true, show_only_top: false, sentiment_threshold: 0.05 });
  const [data, setData] = useState(null);
  const [analytics, setAnalytics] = useState([]);
  const [auto, setAuto] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [cfgOpen, setCfgOpen] = useState(false);
  const timer = useRef(null);
  const aTimer = useRef(null);

  const showErr = (m) => { setError(m); setTimeout(() => setError(''), 4500); };

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const res = await api.researchNiftySentiment(cfg.top_n);
      if (res.status === 'ok') { setData(res); if (res.config) setCfg((c) => ({ ...c, ...res.config })); }
      else if (!silent) showErr(res.message || 'Failed to load sentiment');
    } catch (e) { if (!silent) showErr(e.message || 'Failed to load sentiment'); }
    finally { if (!silent) setLoading(false); }
  }, [cfg.top_n]);

  const loadAnalytics = useCallback(async () => {
    if (!cfg.enable_trend) return;
    try {
      const res = await api.researchNiftySentimentAnalytics(cfg.top_n);
      if (res.status === 'ok') setAnalytics(res.rows || []);
    } catch { /* non-fatal */ }
  }, [cfg.top_n, cfg.enable_trend]);

  useEffect(() => {
    api.researchNiftySentimentConfig().then((r) => { if (r.status === 'ok') setCfg((c) => ({ ...c, ...r.config })); }).catch(() => {});
    load(false); loadAnalytics();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-refresh: fast snapshot on the configured interval; analytics every ~30s.
  useEffect(() => {
    if (timer.current) clearInterval(timer.current);
    if (aTimer.current) clearInterval(aTimer.current);
    if (auto) {
      timer.current = setInterval(() => load(true), Math.max(3, Number(cfg.refresh_interval) || 5) * 1000);
      aTimer.current = setInterval(loadAnalytics, 30000);
    }
    return () => { if (timer.current) clearInterval(timer.current); if (aTimer.current) clearInterval(aTimer.current); };
  }, [auto, cfg.refresh_interval, load, loadAnalytics]);

  const saveCfg = useCallback(async (patch) => {
    const next = { ...cfg, ...patch };
    setCfg(next);
    try { await api.researchNiftySentimentConfigSave(patch); } catch { /* ignore */ }
    load(true); loadAnalytics();
  }, [cfg, load, loadAnalytics]);

  const topSort = useSort('weighted_score');
  const secSort = useSort('contribution');
  const anaSort = useSort('weight');

  const topRows = data?.top_stocks || [];
  const secRows = data?.sectors || [];

  // Highlights for the analytics table
  const anaStats = useMemo(() => {
    if (!analytics.length) return {};
    const chg = analytics.map((r) => r.change_pct ?? 0);
    const vol = analytics.map((r) => r.vol_5min ?? 0);
    const wt = analytics.map((r) => r.weight ?? 0);
    return {
      gain: Math.max(...chg), loss: Math.min(...chg),
      vol: Math.max(...vol), wt: Math.max(...wt),
    };
  }, [analytics]);

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-[1500px] mx-auto">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold text-gray-100">NIFTY Sentiment Analyzer</h1>
            <span className="px-2 py-0.5 rounded-full bg-brand-600/15 text-brand-400 text-xs font-semibold border border-brand-500/20">Research · NIFTY</span>
          </div>
          <p className="text-gray-500 text-sm mt-0.5">
            Real-time market bias from the top-weighted constituents &amp; sectors — weighted, not one-stock-driven. Read-only.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setCfgOpen((v) => !v)} className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border transition ${cfgOpen ? 'bg-brand-600/20 text-brand-300 border-brand-500/40' : 'bg-surface-3 text-gray-400 border-surface-4'}`}>
            <Settings2 className="w-3.5 h-3.5" /> Config
          </button>
          <button onClick={() => load(false)} disabled={loading} className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">
            {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />} Refresh
          </button>
          <button onClick={() => setAuto((a) => !a)} className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border transition ${auto ? 'bg-emerald-600/20 text-emerald-400 border-emerald-500/40' : 'bg-surface-3 text-gray-400 border-surface-4'}`}>
            {auto ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />} Auto {auto ? 'ON' : 'OFF'}
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-red-400 text-sm">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      )}

      {cfgOpen && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 items-end text-xs">
          <label className="flex flex-col gap-1">
            <span className="text-gray-400">Top stocks (universe)</span>
            <input type="number" min="3" max="24" value={cfg.top_n}
              onChange={(e) => saveCfg({ top_n: Math.max(3, Math.min(24, parseInt(e.target.value) || 10)) })} className={selCls} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-gray-400">Refresh interval (s)</span>
            <input type="number" min="3" max="60" value={cfg.refresh_interval}
              onChange={(e) => saveCfg({ refresh_interval: Math.max(3, Math.min(60, parseInt(e.target.value) || 5)) })} className={selCls} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-gray-400">Neutral threshold (%)</span>
            <input type="number" step="0.01" min="0" value={cfg.sentiment_threshold}
              onChange={(e) => saveCfg({ sentiment_threshold: Math.max(0, parseFloat(e.target.value) || 0) })} className={selCls} />
          </label>
          <label className="flex items-center gap-2 mt-4">
            <input type="checkbox" checked={!!cfg.enable_sector} onChange={(e) => saveCfg({ enable_sector: e.target.checked })} className="w-4 h-4 accent-brand-500" />
            <span className="text-gray-300">Sector analysis</span>
          </label>
          <label className="flex items-center gap-2 mt-4">
            <input type="checkbox" checked={!!cfg.enable_trend} onChange={(e) => saveCfg({ enable_trend: e.target.checked })} className="w-4 h-4 accent-brand-500" />
            <span className="text-gray-300">Trend analytics table</span>
          </label>
        </div>
      )}

      {/* Sentiment cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <SentimentCard title="Overall Market Sentiment" icon={Gauge} card={data?.overall} />
        <SentimentCard title="Top Stocks Sentiment" icon={BarChart3} card={data?.top_card} />
        <SentimentCard title="Sector Sentiment" icon={Layers} card={data?.sector_card} />
      </div>
      {data?.fetched_at && <div className="text-[11px] text-gray-500 text-right -mt-1">Updated {data.fetched_at}</div>}

      {/* Top contributors */}
      <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
        <div className="px-4 py-2 text-sm font-semibold text-gray-200 border-b border-surface-3 flex items-center gap-2">
          <BarChart3 className="w-4 h-4 text-brand-400" /> Top Contributors <span className="text-gray-500 font-normal">— ranked by market impact (weight × move)</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs whitespace-nowrap">
            <thead><tr className="border-b border-surface-3">
              <Th label="#" k="impact_rank" sort={topSort.sort} onSort={topSort.onSort} align="left" />
              <Th label="Stock" k="name" sort={topSort.sort} onSort={topSort.onSort} align="left" />
              <Th label="Weight%" k="weight" sort={topSort.sort} onSort={topSort.onSort} />
              <Th label="LTP" k="ltp" sort={topSort.sort} onSort={topSort.onSort} />
              <Th label="% Chg" k="change_pct" sort={topSort.sort} onSort={topSort.onSort} />
              <Th label="Wtd Score" k="weighted_score" sort={topSort.sort} onSort={topSort.onSort} />
              <Th label="Contrib%" k="contribution_pct" sort={topSort.sort} onSort={topSort.onSort} />
              <Th label="Trend" k="trend" sort={topSort.sort} onSort={topSort.onSort} align="center" />
            </tr></thead>
            <tbody>
              {topSort.apply(topRows).map((r) => (
                <tr key={r.symbol} className="border-b border-surface-3/40 hover:bg-surface-3/20">
                  <td className="px-2 py-1.5 text-left text-gray-500">{r.impact_rank}</td>
                  <td className="px-2 py-1.5 text-left"><div className="text-gray-100 font-medium">{r.name}</div><div className="text-[10px] text-gray-500">{r.symbol} · {r.sector}</div></td>
                  <td className="px-2 py-1.5 text-right text-brand-300">{INR(r.weight, 2)}</td>
                  <td className="px-2 py-1.5 text-right text-gray-200">₹{INR(r.ltp)}</td>
                  <td className="px-2 py-1.5 text-right"><ChangePct v={r.change_pct} /></td>
                  <td className={`px-2 py-1.5 text-right font-semibold ${r.weighted_score >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{r.weighted_score >= 0 ? '+' : ''}{(r.weighted_score ?? 0).toFixed(3)}</td>
                  <td className="px-2 py-1.5 text-right text-gray-300">{r.contribution_pct >= 0 ? '+' : ''}{(r.contribution_pct ?? 0).toFixed(3)}</td>
                  <td className="px-2 py-1.5 text-center"><TrendBadge t={r.trend} /></td>
                </tr>
              ))}
              {topRows.length === 0 && <tr><td colSpan={8} className="px-2 py-8 text-center text-gray-500">Connect Zerodha &amp; refresh to load sentiment.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {/* Sector table */}
      {cfg.enable_sector && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
          <div className="px-4 py-2 text-sm font-semibold text-gray-200 border-b border-surface-3 flex items-center gap-2">
            <Layers className="w-4 h-4 text-brand-400" /> Sector Strength
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs whitespace-nowrap">
              <thead><tr className="border-b border-surface-3">
                <Th label="Sector" k="sector" sort={secSort.sort} onSort={secSort.onSort} align="left" />
                <Th label="Weight%" k="weight" sort={secSort.sort} onSort={secSort.onSort} />
                <Th label="Stocks" k="count" sort={secSort.sort} onSort={secSort.onSort} />
                <Th label="Avg %" k="avg_performance" sort={secSort.sort} onSort={secSort.onSort} />
                <Th label="Wtd %" k="weighted_performance" sort={secSort.sort} onSort={secSort.onSort} />
                <Th label="Contrib" k="contribution" sort={secSort.sort} onSort={secSort.onSort} />
                <Th label="Strength" k="strength_score" sort={secSort.sort} onSort={secSort.onSort} />
                <Th label="Bias" k="bias" sort={secSort.sort} onSort={secSort.onSort} align="center" />
              </tr></thead>
              <tbody>
                {secSort.apply(secRows).map((r) => {
                  const s = BIAS[r.bias] || BIAS.Neutral;
                  return (
                    <tr key={r.sector} className="border-b border-surface-3/40 hover:bg-surface-3/20">
                      <td className="px-2 py-1.5 text-left text-gray-100 font-medium">{r.sector} <span className="text-[10px] text-gray-500">({r.advancers}▲/{r.decliners}▼)</span></td>
                      <td className="px-2 py-1.5 text-right text-brand-300">{INR(r.weight, 2)}</td>
                      <td className="px-2 py-1.5 text-right text-gray-400">{r.count}</td>
                      <td className="px-2 py-1.5 text-right"><ChangePct v={r.avg_performance} /></td>
                      <td className="px-2 py-1.5 text-right"><ChangePct v={r.weighted_performance} /></td>
                      <td className={`px-2 py-1.5 text-right font-semibold ${r.contribution >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{r.contribution >= 0 ? '+' : ''}{(r.contribution ?? 0).toFixed(3)}</td>
                      <td className="px-2 py-1.5 text-right text-gray-300">{r.strength_score}</td>
                      <td className="px-2 py-1.5 text-center"><span className={`px-2 py-0.5 rounded text-[11px] border ${s.bd} ${s.bg} ${s.c}`}>{r.bias}</span></td>
                    </tr>
                  );
                })}
                {secRows.length === 0 && <tr><td colSpan={8} className="px-2 py-8 text-center text-gray-500">No sector data.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Live analytics table */}
      {cfg.enable_trend && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
          <div className="px-4 py-2 text-sm font-semibold text-gray-200 border-b border-surface-3 flex items-center gap-2">
            <TrendingUp className="w-4 h-4 text-brand-400" /> Live Analytics — major movers
            <span className="text-gray-500 font-normal text-xs ml-auto">EMA/VWAP refresh ~30s (rate-limit friendly)</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs whitespace-nowrap">
              <thead><tr className="border-b border-surface-3">
                <Th label="Stock" k="name" sort={anaSort.sort} onSort={anaSort.onSort} align="left" />
                <Th label="Weight%" k="weight" sort={anaSort.sort} onSort={anaSort.onSort} />
                <Th label="LTP" k="ltp" sort={anaSort.sort} onSort={anaSort.onSort} />
                <Th label="5m Vol" k="vol_5min" sort={anaSort.sort} onSort={anaSort.onSort} />
                <Th label="20 EMA" k="ema20" sort={anaSort.sort} onSort={anaSort.onSort} />
                <Th label="200 EMA" k="ema200" sort={anaSort.sort} onSort={anaSort.onSort} />
                <Th label="VWAP" k="vwap" sort={anaSort.sort} onSort={anaSort.onSort} />
                <Th label="Prev VWAP" k="prev_vwap" sort={anaSort.sort} onSort={anaSort.onSort} />
                <Th label="% Chg" k="change_pct" sort={anaSort.sort} onSort={anaSort.onSort} />
                <Th label="Trend" k="trend" sort={anaSort.sort} onSort={anaSort.onSort} align="center" />
              </tr></thead>
              <tbody>
                {anaSort.apply(analytics).map((r) => {
                  const topGain = r.change_pct === anaStats.gain && r.change_pct > 0;
                  const topLoss = r.change_pct === anaStats.loss && r.change_pct < 0;
                  const topVol = r.vol_5min === anaStats.vol && r.vol_5min > 0;
                  const topWt = r.weight === anaStats.wt;
                  return (
                    <tr key={r.symbol} className={`border-b border-surface-3/40 hover:bg-surface-3/20 ${topGain ? 'bg-emerald-500/5' : topLoss ? 'bg-red-500/5' : ''}`}>
                      <td className="px-2 py-1.5 text-left"><div className="text-gray-100 font-medium">{r.name} {r.pending && <Loader2 className="w-3 h-3 inline animate-spin text-gray-500" />}</div><div className="text-[10px] text-gray-500">{r.symbol}</div></td>
                      <td className={`px-2 py-1.5 text-right ${topWt ? 'text-brand-300 font-bold' : 'text-brand-300'}`}>{INR(r.weight, 2)}</td>
                      <td className="px-2 py-1.5 text-right text-gray-200">{r.ltp ? `₹${INR(r.ltp)}` : '—'}</td>
                      <td className={`px-2 py-1.5 text-right ${topVol ? 'text-amber-300 font-bold' : 'text-gray-400'}`}>{fmtVol(r.vol_5min)}</td>
                      <td className={`px-2 py-1.5 text-right ${r.ltp > r.ema20 ? 'text-emerald-400' : 'text-red-400'}`}>{r.ema20 ? INR(r.ema20) : '—'}</td>
                      <td className={`px-2 py-1.5 text-right ${r.ltp > r.ema200 ? 'text-emerald-400' : 'text-red-400'}`}>{r.ema200 ? INR(r.ema200) : '—'}</td>
                      <td className={`px-2 py-1.5 text-right ${r.ltp > r.vwap ? 'text-emerald-400' : 'text-red-400'}`}>{r.vwap ? INR(r.vwap) : '—'}</td>
                      <td className="px-2 py-1.5 text-right text-gray-400">{r.prev_vwap ? INR(r.prev_vwap) : '—'}</td>
                      <td className="px-2 py-1.5 text-right"><ChangePct v={r.change_pct} /></td>
                      <td className="px-2 py-1.5 text-center"><TrendBadge t={r.trend} /></td>
                    </tr>
                  );
                })}
                {analytics.length === 0 && <tr><td colSpan={10} className="px-2 py-8 text-center text-gray-500">Loading technicals… (first load fetches EMA/VWAP per stock)</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-[11px] text-gray-500 border-t border-surface-3">
            <span><span className="w-2.5 h-2.5 rounded inline-block bg-emerald-500/30 mr-1" />Top gainer</span>
            <span><span className="w-2.5 h-2.5 rounded inline-block bg-red-500/30 mr-1" />Top loser</span>
            <span>Strong Bullish = LTP &gt; 200 EMA &amp; &gt; VWAP · Strong Bearish = LTP &lt; 200 EMA &amp; &lt; VWAP</span>
          </div>
        </div>
      )}
    </div>
  );
}
