import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  Activity, Radio, Loader2, Download, Info, TrendingUp, TrendingDown, ArrowUp, ArrowDown,
  Minus, RefreshCw, Search, Save, X, SlidersHorizontal, Gauge,
} from 'lucide-react';
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import { api } from '../../api';
import WatchlistBar from '../../components/WatchlistBar';

const sel = 'bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';
const lbl = 'block text-[11px] text-gray-500 uppercase tracking-wide mb-1';
const NUM = (v, d = 2) => (v == null ? 'N/A' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
const INT = (v) => (v == null ? 'N/A' : Number(v).toLocaleString('en-IN'));
const PCT = (v, d = 2) => (v == null ? 'N/A' : `${v >= 0 ? '+' : ''}${Number(v).toFixed(d)}%`);

// score → colour (single demand/supply gradient used everywhere for consistency)
const scoreColor = (s) => (s == null ? 'text-gray-500' : s >= 80 ? 'text-emerald-400' : s >= 60 ? 'text-emerald-300'
  : s >= 45 ? 'text-gray-300' : s >= 30 ? 'text-orange-400' : 'text-red-400');
const scoreBg = (s) => (s == null ? '#6b7280' : s >= 80 ? '#10b981' : s >= 60 ? '#34d399'
  : s >= 45 ? '#9ca3af' : s >= 30 ? '#f59e0b' : '#ef4444');
const pctColor = (v) => (v == null ? 'text-gray-500' : v >= 0 ? 'text-emerald-400' : 'text-red-400');

function TrendIcon({ trend }) {
  if (trend === 'DEMAND BUILDING') return <span className="inline-flex items-center gap-0.5 text-emerald-400"><ArrowUp className="w-3.5 h-3.5" /></span>;
  if (trend === 'DEMAND WEAKENING') return <span className="inline-flex items-center gap-0.5 text-red-400"><ArrowDown className="w-3.5 h-3.5" /></span>;
  return <span className="inline-flex items-center text-gray-500"><Minus className="w-3.5 h-3.5" /></span>;
}

const COLS = [
  ['rank', 'Rank', 'l'], ['symbol', 'Stock', 'l'], ['ltp', 'LTP', 'r'], ['change_pct', 'Chg %', 'r'],
  ['buy_qty', 'Buy Qty', 'r'], ['sell_qty', 'Sell Qty', 'r'], ['ratio', 'B/S Ratio', 'r'],
  ['imbalance_pct', 'Depth Imb', 'r'], ['volume', 'Volume', 'r'], ['rvol', 'RVOL', 'r'],
  ['vwap', 'VWAP', 'r'], ['vwap_status', 'VWAP', 'l'], ['score', 'Demand', 'r'],
  ['confidence', 'Conf', 'r'], ['signal', 'Signal', 'l'], ['status', 'Status', 'l'],
];

export default function DemandSupply() {
  const [tab, setTab] = useState('live');
  const [cfg, setCfg] = useState(null);
  const [universe, setUniverse] = useState([]);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const showErr = (m) => { setErr(m); setTimeout(() => setErr(''), 6000); };
  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(''), 2000); };

  const [liveSel, setLiveSel] = useState({ mode: 'all', symbol: null, symbols: null });
  const [simSel, setSimSel] = useState({ mode: 'single', symbol: null, symbols: null });
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [autoLive, setAutoLive] = useState(true);
  const [sortKey, setSortKey] = useState('score');
  const [sortDir, setSortDir] = useState('desc');
  const [search, setSearch] = useState('');
  const [filters, setFilters] = useState({ minScore: '', minRatio: '', minImb: '', minChg: '', minRvol: '', vwap: 'any' });
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const pollRef = useRef(null);

  useEffect(() => {
    api.dsConfig().then((r) => { if (r.status === 'ok') setCfg(r.config); }).catch(() => setCfg({}));
    api.dsUniverse().then((r) => { if (r.status === 'ok') setUniverse(r.symbols || []); }).catch(() => {});
  }, []);

  const activeSel = tab === 'live' ? liveSel : simSel;
  const runScan = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const s = tab === 'live' ? liveSel : simSel;
      const body = {
        overrides: cfg || undefined,
        mode: s.mode === 'single' ? 'single' : s.mode === 'watchlist' ? 'watchlist' : s.mode === 'all' ? 'all' : 'selected',
        symbols: s.mode === 'single' ? (s.symbol ? [s.symbol] : null) : s.mode === 'watchlist' ? (s.symbols || null) : null,
      };
      const r = await api.dsScan(body);
      if (r.status === 'ok') setData(r); else if (!silent) showErr(r.message || 'Scan failed');
    } catch (e) { if (!silent) showErr(e.message); } finally { if (!silent) setLoading(false); }
  }, [tab, liveSel, simSel, cfg]);

  // live auto-poll (only on the Live tab, when enabled)
  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (tab !== 'live' || !autoLive) return undefined;
    runScan(false);
    const secs = Math.max(3, Number(cfg?.update_interval) || 15);
    pollRef.current = setInterval(() => runScan(true), secs * 1000);
    return () => pollRef.current && clearInterval(pollRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, autoLive, liveSel, cfg?.update_interval]);

  const openDetail = async (symbol) => {
    setDetail({ symbol, loading: true }); setDetailLoading(true);
    try {
      const r = await api.dsDetail(symbol, cfg || undefined);
      if (r.status === 'ok') setDetail({ symbol, ...r }); else { showErr(r.message); setDetail(null); }
    } catch (e) { showErr(e.message); setDetail(null); } finally { setDetailLoading(false); }
  };

  const rows = data?.rows || [];
  const filtered = useMemo(() => {
    let r = rows;
    const f = filters;
    if (search.trim()) { const q = search.trim().toUpperCase(); r = r.filter((x) => x.symbol.includes(q)); }
    const num = (v) => (v === '' || v == null ? null : Number(v));
    if (num(f.minScore) != null) r = r.filter((x) => (x.score ?? -1) >= num(f.minScore));
    if (num(f.minRatio) != null) r = r.filter((x) => (x.ratio_raw ?? -1) >= num(f.minRatio));
    if (num(f.minImb) != null) r = r.filter((x) => (x.imbalance_pct ?? -999) >= num(f.minImb));
    if (num(f.minChg) != null) r = r.filter((x) => (x.change_pct ?? -999) >= num(f.minChg));
    if (num(f.minRvol) != null) r = r.filter((x) => (x.rvol ?? -1) >= num(f.minRvol));
    if (f.vwap === 'above') r = r.filter((x) => x.vwap_status === 'ABOVE VWAP');
    if (f.vwap === 'below') r = r.filter((x) => x.vwap_status === 'BELOW VWAP');
    const dir = sortDir === 'asc' ? 1 : -1;
    const keyOf = (x) => {
      const v = sortKey === 'ratio' ? x.ratio_raw : sortKey === 'imbalance_pct' ? x.imbalance_pct : x[sortKey];
      return v == null ? -Infinity : (typeof v === 'string' ? v : Number(v));
    };
    return [...r].sort((a, b) => { const av = keyOf(a), bv = keyOf(b); return av < bv ? -dir : av > bv ? dir : 0; });
  }, [rows, filters, search, sortKey, sortDir]);

  const toggleSort = (k) => { if (sortKey === k) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc')); else { setSortKey(k); setSortDir('desc'); } };

  const exportCSV = () => {
    if (!filtered.length) return;
    const cols = ['rank', 'symbol', 'ltp', 'change_pct', 'buy_qty', 'sell_qty', 'ratio_raw', 'buy_depth', 'sell_depth',
      'imbalance_pct', 'volume', 'rvol', 'vwap', 'vwap_dist', 'score', 'confidence', 'signal', 'status'];
    const esc = (v) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const ts = new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-');
    const lines = [['timestamp', ...cols].join(',')].concat(
      filtered.map((r) => [data?.last_update || '', ...cols.map((c) => esc(r[c]))].join(',')));
    const blob = new Blob([lines.join('\n') + '\n'], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = `demand_supply_${ts}.csv`; a.click();
  };

  const patch = (k, v) => setCfg((c) => ({ ...c, [k]: v }));
  const patchW = (k, v) => setCfg((c) => ({ ...c, weights: { ...(c.weights || {}), [k]: v } }));
  const saveCfg = async () => { const r = await api.dsConfigSave(cfg); if (r.status === 'ok') { setCfg(r.config); flash('Config saved'); } else showErr(r.message); };

  const market = data?.market;
  const marketColor = market?.state === 'OPEN' ? 'text-emerald-400' : market?.state === 'PRE-OPEN' ? 'text-amber-400' : 'text-gray-500';
  const s = data?.summary || {};

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-[1700px] mx-auto">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Activity className="w-6 h-6 text-brand-400" />
            <h1 className="text-xl font-bold text-gray-100">Demand Supply Equity Scanner</h1>
            <span className="text-[11px] px-2 py-0.5 rounded-full bg-brand-500/15 text-brand-300 border border-brand-500/25">Research 12</span>
          </div>
          <p className="text-sm text-gray-500 mt-0.5">Live Market Demand vs Supply Analysis — visible order-book pressure, ranked.</p>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <span className={`flex items-center gap-1 font-semibold ${tab === 'live' ? 'text-emerald-400' : 'text-brand-300'}`}><Radio className="w-3.5 h-3.5" /> {tab === 'live' ? 'LIVE' : 'SIMULATION'}</span>
          {market && <span className={`font-semibold ${marketColor}`}>Market {market.state}</span>}
          <span className={data?.connected ? 'text-emerald-400' : 'text-gray-500'}>● {data?.connected ? 'CONNECTED' : 'DISCONNECTED'}</span>
          {data?.last_update && <span className="text-gray-500">Last {data.last_update}</span>}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-surface-3">
        {[['live', 'Live Market', Radio], ['sim', 'Simulation', Search], ['config', 'Config', SlidersHorizontal], ['info', 'Info', Info]].map(([id, label, Icon]) => (
          <button key={id} onClick={() => setTab(id)} className={`flex items-center gap-1.5 px-3 py-2 text-sm font-medium border-b-2 -mb-px ${tab === id ? 'border-brand-500 text-brand-300' : 'border-transparent text-gray-400 hover:text-gray-200'}`}>
            <Icon className="w-4 h-4" /> {label}
          </button>
        ))}
      </div>

      {err && <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2">{err}</div>}
      {msg && <div className="text-sm text-emerald-400 bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-3 py-2">{msg}</div>}

      {(tab === 'live' || tab === 'sim') && (
        <div className="space-y-4">
          {/* Universe + controls */}
          <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
            <WatchlistBar universe={universe} count={universe.length}
              onChange={tab === 'live' ? setLiveSel : setSimSel} />
            <div className="flex flex-wrap items-center gap-3">
              <button onClick={() => runScan(false)} disabled={loading} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">
                {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />} Scan now
              </button>
              {tab === 'live' && (
                <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer"><input type="checkbox" checked={autoLive} onChange={(e) => setAutoLive(e.target.checked)} className="accent-brand-500" /> Auto-refresh ({cfg?.update_interval || 15}s)</label>
              )}
              <div className="flex items-center gap-1.5 bg-surface-3 border border-surface-4 rounded-lg px-2">
                <Search className="w-3.5 h-3.5 text-gray-500" />
                <input value={search} onChange={(e) => setSearch(e.target.value.toUpperCase())} placeholder="Filter symbol…" className="bg-transparent py-1.5 text-sm text-gray-200 focus:outline-none w-32" />
              </div>
              <button onClick={exportCSV} disabled={!filtered.length} className="ml-auto flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-40"><Download className="w-3.5 h-3.5" /> CSV</button>
            </div>
            {/* Filters */}
            <div className="flex flex-wrap items-end gap-2 pt-2 border-t border-surface-3">
              {[['minScore', 'Min Score'], ['minRatio', 'Min Ratio'], ['minImb', 'Min Imb %'], ['minChg', 'Min Chg %'], ['minRvol', 'Min RVOL']].map(([k, label]) => (
                <div key={k}><label className={lbl}>{label}</label><input type="number" value={filters[k]} onChange={(e) => setFilters((f) => ({ ...f, [k]: e.target.value }))} className={`w-24 ${sel}`} /></div>
              ))}
              <div><label className={lbl}>VWAP</label>
                <select value={filters.vwap} onChange={(e) => setFilters((f) => ({ ...f, vwap: e.target.value }))} className={sel}><option value="any">Any</option><option value="above">Above</option><option value="below">Below</option></select>
              </div>
              <button onClick={() => { setFilters({ minScore: '', minRatio: '', minImb: '', minChg: '', minRvol: '', vwap: 'any' }); setSearch(''); }} className="text-xs px-2 py-1.5 rounded-lg border bg-surface-3 text-gray-400 border-surface-4 hover:text-white">Reset filters</button>
            </div>
          </div>

          {/* Summary cards */}
          {data && (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
              {[['Scanned', s.scanned, 'text-gray-100'], ['Strong Demand', s.strong_demand, 'text-emerald-400'],
                ['Moderate', s.moderate_demand, 'text-emerald-300'], ['Neutral', s.neutral, 'text-gray-300'],
                ['Strong Supply', s.strong_supply, 'text-red-400'], ['Shown', filtered.length, 'text-brand-300']].map(([label, val, cls]) => (
                <div key={label} className="bg-surface-2 border border-surface-3 rounded-xl px-3 py-2">
                  <div className="text-[10px] uppercase tracking-wide text-gray-500">{label}</div>
                  <div className={`text-lg font-bold ${cls}`}>{val ?? 0}</div>
                </div>
              ))}
            </div>
          )}

          {/* Top demand / supply */}
          {data && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
              <TopList title="Top Demand Stocks" rows={data.top_demand} onPick={openDetail} demand />
              <TopList title="Top Supply Stocks" rows={data.top_supply} onPick={openDetail} />
            </div>
          )}

          {/* Main table */}
          <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
            <div className="overflow-x-auto"><table className="w-full text-xs whitespace-nowrap">
              <thead className="bg-surface-3 text-gray-300"><tr>{COLS.map(([k, h, a]) => (
                <th key={k} onClick={() => toggleSort(k)} className={`px-2.5 py-2 font-semibold cursor-pointer select-none ${a === 'l' ? 'text-left' : 'text-right'} ${sortKey === k ? 'text-brand-300' : ''}`}>
                  {h}{sortKey === k ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ''}
                </th>
              ))}</tr></thead>
              <tbody>{filtered.map((r) => (
                <tr key={r.symbol} onClick={() => openDetail(r.symbol)} className="border-t border-surface-3/40 hover:bg-surface-3/20 cursor-pointer">
                  <td className="px-2.5 py-1.5 text-left text-gray-500">{r.rank}</td>
                  <td className="px-2.5 py-1.5 text-left text-brand-300 font-semibold">{r.symbol}</td>
                  <td className="px-2.5 py-1.5 text-right text-gray-200">₹{NUM(r.ltp)}</td>
                  <td className={`px-2.5 py-1.5 text-right ${pctColor(r.change_pct)}`}>{PCT(r.change_pct)}</td>
                  <td className="px-2.5 py-1.5 text-right text-gray-300">{INT(r.buy_qty)}</td>
                  <td className="px-2.5 py-1.5 text-right text-gray-300">{INT(r.sell_qty)}</td>
                  <td className="px-2.5 py-1.5 text-right text-gray-100 font-medium">{r.ratio ?? 'N/A'}</td>
                  <td className={`px-2.5 py-1.5 text-right ${pctColor(r.imbalance_pct)}`}>{r.imbalance_pct == null ? 'N/A' : PCT(r.imbalance_pct, 1)}</td>
                  <td className="px-2.5 py-1.5 text-right text-gray-400">{INT(r.volume)}</td>
                  <td className="px-2.5 py-1.5 text-right text-gray-300">{r.rvol == null ? 'N/A' : `${r.rvol}x`}</td>
                  <td className="px-2.5 py-1.5 text-right text-gray-400">{r.vwap == null ? 'N/A' : `₹${NUM(r.vwap)}`}</td>
                  <td className={`px-2.5 py-1.5 text-left ${r.vwap_status === 'ABOVE VWAP' ? 'text-emerald-400' : r.vwap_status === 'BELOW VWAP' ? 'text-red-400' : 'text-gray-500'}`}>{r.vwap_status === 'ABOVE VWAP' ? '▲' : r.vwap_status === 'BELOW VWAP' ? '▼' : '—'}</td>
                  <td className="px-2.5 py-1.5 text-right"><span className={`font-bold ${scoreColor(r.score)}`}>{r.score == null ? 'N/A' : r.score}</span></td>
                  <td className="px-2.5 py-1.5 text-right text-gray-400">{r.confidence}%</td>
                  <td className="px-2.5 py-1.5 text-left"><span className={scoreColor(r.score)}>{r.emoji} {r.signal}</span></td>
                  <td className="px-2.5 py-1.5 text-left text-gray-400"><TrendIcon trend={r.trend} /> {r.status}</td>
                </tr>
              ))}
              {!filtered.length && <tr><td colSpan={COLS.length} className="px-4 py-10 text-center text-gray-500">{loading ? 'Scanning…' : data ? 'No stocks match the current filters.' : 'Run a scan to see live demand/supply ranking.'}</td></tr>}
              </tbody>
            </table></div>
          </div>

          <MicroWarning />
        </div>
      )}

      {tab === 'config' && cfg && <ConfigPanel cfg={cfg} patch={patch} patchW={patchW} onSave={saveCfg} />}
      {tab === 'info' && <InfoPanel />}

      {detail && <DetailPanel detail={detail} loading={detailLoading} onClose={() => setDetail(null)} />}
    </div>
  );
}

function TopList({ title, rows, onPick, demand }) {
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
      <div className="px-3 py-2 border-b border-surface-3 text-sm font-semibold text-gray-200 flex items-center gap-1.5">
        {demand ? <TrendingUp className="w-4 h-4 text-emerald-400" /> : <TrendingDown className="w-4 h-4 text-red-400" />} {title}
      </div>
      <div className="overflow-x-auto"><table className="w-full text-xs whitespace-nowrap">
        <thead className="bg-surface-3/50 text-gray-400"><tr>{['#', 'Stock', 'Score', 'Ratio', 'Depth', 'Chg %', 'RVOL', 'Signal'].map((h, i) => <th key={h} className={`px-2.5 py-1.5 font-semibold ${i < 2 ? 'text-left' : 'text-right'}`}>{h}</th>)}</tr></thead>
        <tbody>{(rows || []).map((r, i) => (
          <tr key={r.symbol} onClick={() => onPick(r.symbol)} className="border-t border-surface-3/30 hover:bg-surface-3/20 cursor-pointer">
            <td className="px-2.5 py-1 text-left text-gray-500">{i + 1}</td>
            <td className="px-2.5 py-1 text-left text-brand-300 font-semibold">{r.symbol}</td>
            <td className={`px-2.5 py-1 text-right font-bold ${scoreColor(r.score)}`}>{r.score ?? 'N/A'}</td>
            <td className="px-2.5 py-1 text-right text-gray-200">{r.ratio ?? 'N/A'}</td>
            <td className={`px-2.5 py-1 text-right ${pctColor(r.imbalance_pct)}`}>{r.imbalance_pct == null ? 'N/A' : PCT(r.imbalance_pct, 0)}</td>
            <td className={`px-2.5 py-1 text-right ${pctColor(r.change_pct)}`}>{PCT(r.change_pct)}</td>
            <td className="px-2.5 py-1 text-right text-gray-300">{r.rvol == null ? 'N/A' : `${r.rvol}x`}</td>
            <td className="px-2.5 py-1 text-right text-gray-400">{r.emoji}</td>
          </tr>
        ))}
        {!(rows || []).length && <tr><td colSpan={8} className="px-3 py-4 text-center text-gray-600">—</td></tr>}
        </tbody>
      </table></div>
    </div>
  );
}

function MicroWarning() {
  return (
    <div className="text-[11px] text-amber-300/80 bg-amber-500/10 border border-amber-500/25 rounded-lg px-3 py-2 flex items-start gap-2">
      <Info className="w-4 h-4 mt-0.5 shrink-0" />
      <span>Market-depth demand is a real-time indication of <strong>visible order-book pressure</strong>. Orders can be cancelled or modified, so order-book imbalance must be interpreted together with price, volume, VWAP and persistence. This is research/ranking — not a buy signal or price prediction.</span>
    </div>
  );
}

function Bar({ value, max, color }) {
  const pct = max ? Math.min(100, Math.abs(value) / max * 100) : 0;
  return <div className="h-2 rounded bg-surface-3 overflow-hidden"><div className="h-full rounded" style={{ width: `${pct}%`, background: color }} /></div>;
}

function DetailPanel({ detail, loading, onClose }) {
  const r = detail.row;
  const ob = detail.order_book;
  const maxDepth = ob ? Math.max(1, ...(ob.bids || []).map((b) => b.qty), ...(ob.asks || []).map((a) => a.qty)) : 1;
  const timeline = (detail.timeline || []).filter((t) => t.score != null);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-3 overflow-y-auto" onClick={onClose}>
      <div className="bg-surface-1 border border-surface-3 rounded-xl w-full max-w-4xl my-6" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-surface-3 sticky top-0 bg-surface-1 rounded-t-xl">
          <div className="flex items-center gap-2"><Gauge className="w-5 h-5 text-brand-400" /><span className="text-lg font-bold text-gray-100">{detail.symbol}</span>
            {r && <span className={`text-sm font-semibold ${scoreColor(r.score)}`}>{r.emoji} {r.signal}</span>}</div>
          <button onClick={onClose} className="text-gray-500 hover:text-white"><X className="w-5 h-5" /></button>
        </div>
        {loading || !r ? (
          <div className="p-10 text-center text-gray-500"><Loader2 className="w-5 h-5 animate-spin inline" /> Loading order book…</div>
        ) : (
          <div className="p-4 space-y-4">
            {/* metric tiles */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {[['LTP', `₹${NUM(r.ltp)}`, ''], ['Change', PCT(r.change_pct), pctColor(r.change_pct)],
                ['VWAP', r.vwap == null ? 'N/A' : `₹${NUM(r.vwap)}`, ''], ['VWAP Dist', r.vwap_dist == null ? 'N/A' : PCT(r.vwap_dist), pctColor(r.vwap_dist)],
                ['Buy Qty', INT(r.buy_qty), 'text-emerald-400'], ['Sell Qty', INT(r.sell_qty), 'text-red-400'],
                ['B/S Ratio', r.ratio ?? 'N/A', ''], ['Depth Imb', r.imbalance_pct == null ? 'N/A' : PCT(r.imbalance_pct, 1), pctColor(r.imbalance_pct)],
                ['5L Buy Depth', INT(r.buy_depth), 'text-emerald-400'], ['5L Sell Depth', INT(r.sell_depth), 'text-red-400'],
                ['Volume', INT(r.volume), ''], ['RVOL', r.rvol == null ? 'N/A' : `${r.rvol}x`, '']].map(([k, v, c]) => (
                <div key={k} className="bg-surface-3/40 border border-surface-3 rounded-lg px-3 py-2">
                  <div className="text-[10px] uppercase tracking-wide text-gray-500">{k}</div>
                  <div className={`text-sm font-medium ${c || 'text-gray-200'}`}>{v}</div>
                </div>
              ))}
            </div>

            {/* score + confidence + status */}
            <div className="flex flex-wrap items-center gap-4 bg-surface-2 border border-surface-3 rounded-lg px-4 py-3">
              <div><div className="text-[10px] uppercase text-gray-500">Demand Score</div><div className={`text-2xl font-bold ${scoreColor(r.score)}`}>{r.score}<span className="text-sm text-gray-500">/100</span></div></div>
              <div><div className="text-[10px] uppercase text-gray-500">Signal Confidence</div><div className="text-xl font-bold text-gray-200">{r.confidence}%</div></div>
              <div><div className="text-[10px] uppercase text-gray-500">Status</div><div className="text-sm font-semibold text-gray-200 flex items-center gap-1"><TrendIcon trend={r.trend} /> {r.status}</div></div>
              <div><div className="text-[10px] uppercase text-gray-500">Persistence</div><div className="text-sm font-semibold text-gray-200">{r.persistence?.bullish ?? 0}/{r.persistence?.n ?? 0} bullish</div></div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {/* order book ladder */}
              <div className="bg-surface-2 border border-surface-3 rounded-lg p-3">
                <div className="text-sm font-semibold text-gray-200 mb-2">Order Book (5-level)</div>
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div>
                    <div className="text-[10px] text-emerald-400 mb-1 flex justify-between"><span>BID</span><span>Qty</span></div>
                    {(ob.bids || []).map((b, i) => (
                      <div key={i} className="mb-1"><div className="flex justify-between text-gray-300"><span>₹{NUM(b.price)}</span><span>{INT(b.qty)}{b.orders ? <span className="text-gray-600"> ({b.orders})</span> : null}</span></div><Bar value={b.qty} max={maxDepth} color="#10b981" /></div>
                    ))}
                  </div>
                  <div>
                    <div className="text-[10px] text-red-400 mb-1 flex justify-between"><span>ASK</span><span>Qty</span></div>
                    {(ob.asks || []).map((a, i) => (
                      <div key={i} className="mb-1"><div className="flex justify-between text-gray-300"><span>₹{NUM(a.price)}</span><span>{INT(a.qty)}{a.orders ? <span className="text-gray-600"> ({a.orders})</span> : null}</span></div><Bar value={a.qty} max={maxDepth} color="#ef4444" /></div>
                    ))}
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-2 mt-3 pt-2 border-t border-surface-3 text-[11px]">
                  <div><div className="text-gray-500">Spread</div><div className="text-gray-200">{ob.spread == null ? 'N/A' : `₹${NUM(ob.spread)} (${ob.spread_pct ?? 'N/A'}%)`}</div></div>
                  <div><div className="text-gray-500">Total Bid / Ask</div><div className="text-gray-200">{INT(ob.total_bid)} / {INT(ob.total_ask)}</div></div>
                  <div><div className="text-gray-500">Depth Imbalance</div><div className={pctColor(ob.imbalance_pct)}>{ob.imbalance_pct == null ? 'N/A' : PCT(ob.imbalance_pct, 1)}</div></div>
                </div>
              </div>

              {/* score breakdown */}
              <div className="bg-surface-2 border border-surface-3 rounded-lg p-3">
                <div className="text-sm font-semibold text-gray-200 mb-2">Demand Score Breakdown</div>
                <div className="space-y-1.5 text-xs">
                  {Object.entries(r.breakdown || {}).map(([k, b]) => (
                    <div key={k}>
                      <div className="flex justify-between text-gray-400"><span className="capitalize">{k.replace('_', ' ')}</span><span className="text-gray-200">{b.points} / {b.max}</span></div>
                      <Bar value={b.points} max={b.max} color={scoreBg(r.score)} />
                    </div>
                  ))}
                  <div className="flex justify-between pt-2 mt-1 border-t border-surface-3 font-bold text-gray-100"><span>TOTAL</span><span className={scoreColor(r.score)}>{r.score} / 100</span></div>
                </div>
                {detail.aggressive && !detail.aggressive.available && (
                  <div className="text-[10px] text-gray-500 mt-2">{detail.aggressive.note}</div>
                )}
              </div>
            </div>

            {/* score timeline */}
            <div className="bg-surface-2 border border-surface-3 rounded-lg p-3">
              <div className="text-sm font-semibold text-gray-200 mb-2">Demand Score over Time</div>
              {timeline.length < 2 ? (
                <div className="text-xs text-gray-500 py-6 text-center">Timeline builds as the scanner accumulates snapshots (keep the Live tab open).</div>
              ) : (
                <div style={{ height: 180 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={timeline} margin={{ top: 6, right: 10, left: -18, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#ffffff10" />
                      <XAxis dataKey="t" tick={{ fontSize: 10, fill: '#6b7280' }} />
                      <YAxis domain={[0, 100]} tick={{ fontSize: 10, fill: '#6b7280' }} />
                      <Tooltip contentStyle={{ background: '#1e293b', border: '1px solid #334155', fontSize: 12 }} />
                      <Line type="monotone" dataKey="score" stroke={scoreBg(r.score)} strokeWidth={2} dot={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              )}
            </div>

            <MicroWarning />
          </div>
        )}
      </div>
    </div>
  );
}

function ConfigPanel({ cfg, patch, patchW, onSave }) {
  const w = cfg.weights || {};
  const total = Object.values(w).reduce((a, b) => a + Number(b || 0), 0);
  const numField = (k, label, step = 1) => (
    <div><label className={lbl}>{label}</label><input type="number" step={step} value={cfg[k] ?? ''} onChange={(e) => patch(k, e.target.value === '' ? '' : Number(e.target.value))} className={`w-full ${sel}`} /></div>
  );
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-5 max-w-4xl">
      <div>
        <div className="flex items-center justify-between mb-2"><h3 className="text-sm font-semibold text-gray-200">Scoring Weights</h3><span className={`text-xs ${total === 100 ? 'text-emerald-400' : 'text-amber-400'}`}>Total {total} {total !== 100 ? '(normalised at runtime)' : ''}</span></div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[['ratio', 'Buy/Sell Ratio'], ['imbalance', 'Depth Imbalance'], ['momentum', 'Price Momentum'], ['volume', 'Volume Strength'], ['vwap', 'VWAP Position'], ['buy_trend', 'Buy Qty Trend'], ['sell_trend', 'Sell Qty Trend']].map(([k, label]) => (
            <div key={k}><label className={lbl}>{label}</label><input type="number" value={w[k] ?? ''} onChange={(e) => patchW(k, e.target.value === '' ? '' : Number(e.target.value))} className={`w-full ${sel}`} /></div>
          ))}
        </div>
      </div>
      <div>
        <h3 className="text-sm font-semibold text-gray-200 mb-2">Scan & Signal Settings</h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {numField('max_stocks', 'Max Stocks (0=all)')}
          {numField('top_n', 'Top N Lists')}
          {numField('update_interval', 'Live Interval (s)')}
          {numField('persistence_lookback', 'Persistence Window')}
          {numField('persistence_weight', 'Persistence Weight', 0.05)}
          {numField('trend_min_history', 'Trend Min History')}
          {numField('rvol_lookback', 'RVOL Lookback (days)')}
          {numField('rvol_fetch_cap', 'RVOL Fetch Cap / scan')}
          {numField('min_price', 'Min Price', 0.5)}
          {numField('min_volume', 'Min Volume')}
          {numField('history_cap', 'History Cap')}
          <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer self-end"><input type="checkbox" checked={!!cfg.require_vwap_above} onChange={(e) => patch('require_vwap_above', e.target.checked)} className="accent-brand-500" /> Require above VWAP</label>
        </div>
      </div>
      <div className="flex items-center gap-2 pt-2 border-t border-surface-3">
        <button onClick={onSave} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold"><Save className="w-4 h-4" /> Save Config</button>
        <span className="text-[11px] text-gray-500">Thresholds &amp; scoring anchors are data-driven; edit the JSON config for band-level tuning.</span>
      </div>
    </div>
  );
}

function InfoPanel() {
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-5 space-y-3 text-sm text-gray-300 max-w-3xl">
      <h3 className="font-semibold text-gray-100">How the Demand-Supply Scanner works</h3>
      <p>Each stock's live Kite quote gives 5-level market depth, total buy/sell quantity, day volume, OHLC and the day VWAP. From those the scanner derives independent, normalised signals and combines them into a 0-100 <strong>Demand Score</strong> — never a raw bid/ask ratio.</p>
      <ul className="list-disc pl-5 space-y-1 text-gray-400">
        <li><strong>Buy/Sell Ratio</strong> &amp; <strong>5-level Depth Imbalance</strong> — visible order-book pressure.</li>
        <li><strong>Price momentum</strong> &amp; <strong>VWAP position</strong> — is price confirming the demand?</li>
        <li><strong>Relative Volume</strong> — is participation genuine? (avg over the configured lookback)</li>
        <li><strong>Buy/Sell qty trend</strong> — is pressure building or fading between snapshots?</li>
        <li><strong>Persistence</strong> — a single bullish snapshot is damped; sustained pressure scores higher.</li>
      </ul>
      <p>The score yields a <strong>Signal</strong> (Extreme → Neutral → Supply), a separate <strong>Status</strong> (Demand Building / Weakening / Confirmed), and a <strong>Signal Confidence</strong> reflecting data availability and agreement.</p>
      <p className="text-[12px] text-gray-500">Aggressive buying (trade-side classification) requires tick-level data not present in the Kite quote feed, so it is shown as N/A and excluded from scoring — no fabricated values. This module is read-only and never places orders.</p>
      <MicroWarning />
    </div>
  );
}
