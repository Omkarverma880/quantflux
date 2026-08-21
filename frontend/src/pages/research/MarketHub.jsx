import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  Activity, Loader2, RefreshCw, X, Search, Newspaper, Layers, Flame, TrendingUp, TrendingDown,
  Target, Gauge, SlidersHorizontal, Info, Save, ChevronRight, Download,
} from 'lucide-react';
import { api } from '../../api';

const sel = 'bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';
const lbl = 'block text-[11px] text-gray-500 uppercase tracking-wide mb-1';
const NUM = (v, d = 2) => (v == null ? 'N/A' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
const INT = (v) => (v == null ? 'N/A' : Number(v).toLocaleString('en-IN'));
const PCT = (v, d = 2) => (v == null ? 'N/A' : `${v >= 0 ? '+' : ''}${Number(v).toFixed(d)}%`);
const pc = (v) => (v == null ? 'text-gray-500' : v >= 0 ? 'text-emerald-400' : 'text-red-400');
const scoreColor = (s) => (s >= 8 ? '#10b981' : s >= 6.5 ? '#34d399' : s >= 4.5 ? '#9ca3af' : s >= 3 ? '#f59e0b' : '#ef4444');

function Card({ title, icon: Icon, sub, right, children, className = '' }) {
  return (
    <div className={`bg-surface-2 border border-surface-3 rounded-xl overflow-hidden ${className}`}>
      {(title || right) && (
        <div className="px-4 py-2.5 border-b border-surface-3 flex items-center justify-between gap-2">
          <div>
            <div className="flex items-center gap-1.5 text-sm font-semibold text-gray-100">
              {Icon && <Icon className="w-4 h-4 text-brand-400" />} {title}
            </div>
            {sub && <div className="text-[11px] text-gray-500 mt-0.5">{sub}</div>}
          </div>
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

function ScoreRing({ score, size = 44 }) {
  const c = scoreColor(score);
  const pctv = Math.max(0, Math.min(100, (score / 10) * 100));
  const r = (size - 6) / 2;
  const circ = 2 * Math.PI * r;
  return (
    <svg width={size} height={size} className="shrink-0">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#ffffff14" strokeWidth="4" />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={c} strokeWidth="4" strokeLinecap="round"
        strokeDasharray={`${circ * pctv / 100} ${circ}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      <text x="50%" y="52%" dominantBaseline="middle" textAnchor="middle" fontSize={size * 0.32} fontWeight="700" fill={c}>{score}</text>
    </svg>
  );
}

function StockRow({ r, onPick, right }) {
  return (
    <button onClick={() => onPick?.(r.symbol)} className="w-full px-3 py-2 flex items-center gap-3 hover:bg-surface-3/30 border-b border-surface-3/40 last:border-0 text-left">
      <div className="min-w-0 flex-1">
        <div className="text-sm text-gray-100 font-medium truncate">{r.symbol}</div>
        <div className="text-[11px] text-gray-500 truncate">{r.sector}</div>
      </div>
      {right || (
        <div className="text-right shrink-0">
          <div className="text-sm text-gray-200">₹{NUM(r.ltp)}</div>
          <div className={`text-[11px] ${pc(r.change_pct)}`}>{r.change == null ? '' : `${r.change >= 0 ? '+' : ''}${NUM(r.change)} `}({PCT(r.change_pct)})</div>
        </div>
      )}
    </button>
  );
}

/* SL ——●—— Entry ——— Target track, like the reference trade cards */
function IdeaTrack({ idea }) {
  const p = Math.max(2, Math.min(98, idea.progress));
  const entryPos = Math.max(2, Math.min(98, ((idea.entry - idea.sl) / Math.max(0.01, idea.target - idea.sl)) * 100));
  return (
    <div className="relative pt-6 pb-5">
      <div className="absolute left-0 right-0 top-[26px] h-[3px] rounded bg-surface-4" />
      <div className="absolute top-[26px] h-[3px] rounded bg-emerald-500/40" style={{ left: `${entryPos}%`, right: '0%' }} />
      {/* current price marker */}
      <div className="absolute -translate-x-1/2 flex flex-col items-center" style={{ left: `${p}%`, top: 0 }}>
        <span className="text-[9px] px-1.5 py-0.5 rounded bg-surface-4 text-gray-200 whitespace-nowrap">₹{NUM(idea.ltp)}</span>
        <span className="w-[9px] h-[9px] rounded-full bg-gray-100 mt-[6px] ring-2 ring-surface-2" />
      </div>
      {/* entry pin */}
      <div className="absolute -translate-x-1/2" style={{ left: `${entryPos}%`, top: '22px' }}>
        <span className="block w-[3px] h-[11px] rounded bg-sky-400" />
      </div>
      <div className="absolute left-0 top-[38px] text-left"><div className="text-[9px] text-gray-500">STOP LOSS</div><div className="text-[11px] text-red-400 font-semibold">₹{NUM(idea.sl)}</div></div>
      <div className="absolute -translate-x-1/2 top-[38px] text-center" style={{ left: `${entryPos}%` }}><div className="text-[9px] text-gray-500">ENTRY</div><div className="text-[11px] text-gray-200 font-semibold">₹{NUM(idea.entry)}</div></div>
      <div className="absolute right-0 top-[38px] text-right"><div className="text-[9px] text-gray-500">TARGET</div><div className="text-[11px] text-emerald-400 font-semibold">₹{NUM(idea.target)}</div></div>
    </div>
  );
}

const statusStyle = (s) => ({
  'TARGET HIT': 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40',
  'SL HIT': 'bg-red-500/15 text-red-300 border-red-500/40',
  'AWAITING ENTRY': 'bg-amber-500/15 text-amber-300 border-amber-500/40',
  ACTIVE: 'bg-sky-500/15 text-sky-300 border-sky-500/40',
}[s] || 'bg-surface-3 text-gray-400 border-surface-4');

function IdeaCard({ idea, onPick }) {
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
      <div className="flex items-start justify-between gap-2">
        <button onClick={() => onPick?.(idea.symbol)} className="text-left">
          <div className="text-base font-bold text-gray-100 hover:text-brand-300">{idea.symbol}</div>
          <div className="text-[11px] text-gray-500">{idea.sector} · {idea.horizon}</div>
        </button>
        <div className="flex items-center gap-2">
          <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${statusStyle(idea.status)}`}>{idea.status}</span>
          <ScoreRing score={idea.score} size={38} />
        </div>
      </div>
      <IdeaTrack idea={idea} />
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] border-t border-surface-3 pt-2">
        <span className="text-gray-400">Upside to target <strong className="text-emerald-400">{PCT(idea.upside_pct)}</strong></span>
        <span className="text-gray-400">Risk <strong className="text-red-400">{PCT(idea.risk_pct)}</strong></span>
        <span className="text-gray-400">R:R <strong className="text-gray-200">{idea.rr}</strong></span>
        <span className={`ml-auto ${pc(idea.change_pct)}`}>{PCT(idea.change_pct)} today</span>
      </div>
      <div className="text-[11px] text-gray-500 mt-1.5">{idea.rationale}</div>
    </div>
  );
}

function SectorBar({ s }) {
  const total = Math.max(1, s.adv + s.dec + s.unch);
  const a = (s.adv / total) * 100;
  const d = (s.dec / total) * 100;
  return (
    <div className="px-3 py-2 border-b border-surface-3/40 last:border-0">
      <div className="flex items-center justify-between text-xs mb-1">
        <span className="text-gray-200 font-medium">{s.sector}</span>
        <span className={pc(s.avg_change)}>{PCT(s.avg_change)}</span>
      </div>
      <div className="flex items-center gap-2">
        <div className="flex-1 h-3 rounded overflow-hidden bg-surface-3 flex">
          <div className="h-full bg-emerald-500/80" style={{ width: `${a}%` }} />
          <div className="h-full bg-gray-600/60" style={{ width: `${100 - a - d}%` }} />
          <div className="h-full bg-red-500/80" style={{ width: `${d}%` }} />
        </div>
        <span className="text-[11px] text-emerald-400 w-6 text-right">{s.adv}</span>
        <span className="text-[11px] text-gray-600">/</span>
        <span className="text-[11px] text-red-400 w-6">{s.dec}</span>
      </div>
    </div>
  );
}

export default function MarketHub() {
  const [tab, setTab] = useState('overview');
  const [data, setData] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState(''); const [msg, setMsg] = useState('');
  const [moverTab, setMoverTab] = useState('gainers');
  const [group, setGroup] = useState('Intraday Scans');
  const [detail, setDetail] = useState(null);
  const [news, setNews] = useState(null);
  const [q, setQ] = useState('');
  const [failed, setFailed] = useState('');
  const abortRef = useRef(null);
  const showErr = (m) => { setErr(m); setTimeout(() => setErr(''), 6000); };
  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(''), 2500); };

  const load = useCallback(async () => {
    const ac = new AbortController(); abortRef.current = ac; setLoading(true);
    try {
      const r = await api.mihDashboard({}, ac.signal);
      if (r.status === 'ok') { setData(r); setFailed(''); }
      else { setFailed(r.message || 'Failed to load'); showErr(r.message || 'Failed to load'); }
    } catch (e) {
      if (e.name !== 'AbortError') { setFailed(e.message); showErr(e.message); }
    } finally { setLoading(false); }
  }, []);
  const cancel = () => { if (abortRef.current) abortRef.current.abort(); setLoading(false); };

  useEffect(() => {
    load();
    api.mihConfig().then((r) => { if (r.status === 'ok') setCfg(r.config); }).catch(() => {});
    api.mihNews(8).then((r) => setNews(r)).catch(() => {});
  }, [load]);

  const openStock = async (symbol) => {
    setDetail({ symbol, loading: true });
    try {
      const r = await api.mihStock(symbol, {});
      if (r.status === 'ok') setDetail({ symbol, ...r }); else { showErr(r.message); setDetail(null); }
    } catch (e) { showErr(e.message); setDetail(null); }
  };

  const m = data?.market;
  const mColor = m?.state === 'OPEN' ? 'text-emerald-400 border-emerald-500/40 bg-emerald-500/10'
    : m?.state === 'PRE-OPEN' ? 'text-amber-400 border-amber-500/40 bg-amber-500/10'
      : 'text-gray-400 border-surface-4 bg-surface-3';
  const movers = data?.movers || {};
  const moverRows = movers[moverTab === 'gainers' ? 'gainers' : moverTab === 'losers' ? 'losers' : moverTab === 'high52' ? 'high_52w' : 'low_52w'] || [];
  const groupKeys = (data?.scanner_groups || {})[group] || [];

  const filteredScores = useMemo(() => {
    const rows = data?.top_scores || [];
    if (!q.trim()) return rows;
    const s = q.trim().toUpperCase();
    return rows.filter((r) => r.symbol.includes(s) || (r.sector || '').toUpperCase().includes(s));
  }, [data, q]);

  const exportIdeas = () => {
    const rows = data?.ideas || [];
    if (!rows.length) return;
    const cols = ['symbol', 'sector', 'ltp', 'change_pct', 'score', 'grade', 'entry', 'sl', 'target', 'rr', 'upside_pct', 'status', 'rationale'];
    const esc = (v) => { const x = v == null ? '' : String(v); return /[",\n]/.test(x) ? `"${x.replace(/"/g, '""')}"` : x; };
    const lines = [cols.join(',')].concat(rows.map((r) => cols.map((c) => esc(r[c])).join(',')));
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' }));
    a.download = 'market_hub_ideas.csv'; a.click();
  };

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-[1700px] mx-auto">
      {/* header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Activity className="w-6 h-6 text-brand-400" />
            <h1 className="text-xl font-bold text-gray-100">Market Intelligence Hub</h1>
            <span className="text-[11px] px-2 py-0.5 rounded-full bg-brand-500/15 text-brand-300 border border-brand-500/25">Research 14</span>
            {m && <span className={`text-[11px] px-2 py-0.5 rounded-full border font-semibold ${mColor}`}>MARKET {m.state}</span>}
          </div>
          <p className="text-sm text-gray-500 mt-0.5">Movers, sector breadth, ready-made screeners, stock scores and research setups — one place. Read-only research, not investment advice.</p>
        </div>
        <div className="flex items-center gap-2">
          {data && <span className="text-xs text-gray-500">Updated {data.updated_at} · {data.coverage.scanned}/{data.coverage.universe} stocks · {data.coverage.enriched} enriched</span>}
          <button onClick={load} disabled={loading} className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />} Refresh
          </button>
          {loading && <button onClick={cancel} className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg bg-red-600/80 hover:bg-red-600 text-white font-semibold"><X className="w-4 h-4" /> Cancel</button>}
        </div>
      </div>

      <div className="flex items-center gap-1 border-b border-surface-3 overflow-x-auto">
        {[['overview', 'Overview', Layers], ['scanners', 'Scanners', Search], ['ideas', 'Trade Ideas', Target], ['scores', 'Stock Scores', Flame], ['news', 'Market Buzz', Newspaper], ['settings', 'Settings', SlidersHorizontal]].map(([id, label, Icon]) => (
          <button key={id} onClick={() => setTab(id)} className={`flex items-center gap-1.5 px-3 py-2 text-sm font-medium border-b-2 -mb-px whitespace-nowrap ${tab === id ? 'border-brand-500 text-brand-300' : 'border-transparent text-gray-400 hover:text-gray-200'}`}>
            <Icon className="w-4 h-4" /> {label}
          </button>
        ))}
      </div>

      {err && <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2">{err}</div>}
      {msg && <div className="text-sm text-emerald-400 bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-3 py-2">{msg}</div>}
      {!data && loading && <div className="p-10 text-center text-gray-500"><Loader2 className="w-5 h-5 animate-spin inline" /> Loading market intelligence…</div>}
      {!data && !loading && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-10 text-center space-y-3">
          <div className="text-sm text-gray-300">{failed ? 'Could not load market intelligence.' : 'No data loaded yet.'}</div>
          {failed && <div className="text-xs text-red-400 font-mono break-all max-w-xl mx-auto">{failed}</div>}
          <div className="text-xs text-gray-500">Zerodha must be connected for live market data.</div>
          <button onClick={load} className="inline-flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold"><RefreshCw className="w-4 h-4" /> Retry</button>
        </div>
      )}

      {data && tab === 'overview' && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {/* What's Moving */}
            <Card title="What's Moving" icon={TrendingUp}
              right={<div className="flex gap-1">{[['gainers', 'Gainers'], ['losers', 'Losers'], ['high52', '52W High'], ['low52', '52W Low']].map(([k, l]) => (
                <button key={k} onClick={() => setMoverTab(k)} className={`text-[10px] px-2 py-1 rounded-full border ${moverTab === k ? 'bg-brand-600 text-white border-brand-500' : 'bg-surface-3 text-gray-400 border-surface-4'}`}>{l}</button>))}</div>}>
              <div>{moverRows.length ? moverRows.map((r) => <StockRow key={r.symbol} r={r} onPick={openStock} />)
                : <div className="px-4 py-6 text-center text-gray-500 text-sm">{(moverTab === 'high52' || moverTab === 'low52') ? '52-week levels still loading — coverage grows each refresh.' : 'No data.'}</div>}</div>
            </Card>

            {/* Sector breadth */}
            <Card title="Sector Breadth" icon={Layers} sub="Advances vs declines per sector">
              <div className="max-h-[340px] overflow-y-auto">
                {(data.sectors || []).map((s) => <SectorBar key={s.sector} s={s} />)}
                {!(data.sectors || []).length && <div className="px-4 py-6 text-center text-gray-500 text-sm">No sector data.</div>}
              </div>
            </Card>

            {/* Top scores */}
            <Card title="Stock Scores" icon={Flame} sub="Technical strength (0-10) — trend, momentum, volume, VWAP, range"
              right={<button onClick={() => setTab('scores')} className="text-[11px] text-brand-300 hover:underline flex items-center">Show all <ChevronRight className="w-3 h-3" /></button>}>
              <div className="max-h-[340px] overflow-y-auto">
                {(data.top_scores || []).slice(0, 8).map((r) => (
                  <StockRow key={r.symbol} r={r} onPick={openStock}
                    right={<div className="flex items-center gap-2 shrink-0"><div className="text-right"><div className="text-sm text-gray-200">₹{NUM(r.ltp)}</div><div className={`text-[11px] ${pc(r.change_pct)}`}>{PCT(r.change_pct)}</div></div><ScoreRing score={r.score} size={38} /></div>} />
                ))}
              </div>
            </Card>
          </div>

          {/* Scanners preview */}
          <Card title="Scanners" icon={Search} sub="Ready-made screeners over the live universe"
            right={<div className="flex gap-1">{Object.keys(data.scanner_groups || {}).map((g) => (
              <button key={g} onClick={() => setGroup(g)} className={`text-[10px] px-2 py-1 rounded-full border ${group === g ? 'bg-brand-600 text-white border-brand-500' : 'bg-surface-3 text-gray-400 border-surface-4'}`}>{g}</button>))}</div>}>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 p-3">
              {groupKeys.map((k) => {
                const s = data.scanners[k];
                if (!s) return null;
                return (
                  <div key={k} className="bg-surface-3/30 border border-surface-3 rounded-lg overflow-hidden">
                    <div className="px-3 py-2 border-b border-surface-3">
                      <div className="flex items-center gap-1.5 text-sm font-semibold text-gray-100">
                        {s.direction === 'bullish' ? <TrendingUp className="w-3.5 h-3.5 text-emerald-400" /> : s.direction === 'bearish' ? <TrendingDown className="w-3.5 h-3.5 text-red-400" /> : <Activity className="w-3.5 h-3.5 text-gray-400" />}
                        {s.label}<span className="ml-auto text-[11px] text-gray-500">{s.count}</span>
                      </div>
                      <div className="text-[11px] text-gray-500 mt-0.5 line-clamp-2">{s.description}</div>
                    </div>
                    {s.rows.length ? s.rows.slice(0, 3).map((r) => <StockRow key={r.symbol} r={r} onPick={openStock} />)
                      : <div className="px-3 py-4 text-center text-gray-600 text-xs">No stocks match right now.</div>}
                    {s.data_note && <div className="px-3 py-1.5 text-[10px] text-amber-300/80 bg-amber-500/5 border-t border-surface-3">{s.data_note}</div>}
                  </div>
                );
              })}
            </div>
          </Card>

          {/* Ideas preview */}
          <Card title="Research Setups" icon={Target} sub={`${(data.ideas || []).length} qualifying setups · entry / stop / target with live progress`}
            right={<button onClick={() => setTab('ideas')} className="text-[11px] text-brand-300 hover:underline flex items-center">Show all <ChevronRight className="w-3 h-3" /></button>}>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3 p-3">
              {(data.ideas || []).slice(0, 3).map((i) => <IdeaCard key={i.symbol} idea={i} onPick={openStock} />)}
              {!(data.ideas || []).length && <div className="col-span-full py-6 text-center text-gray-500 text-sm">No setup currently clears the score threshold — that is a valid outcome, not an error.</div>}
            </div>
          </Card>
          <Disclaimer />
        </div>
      )}

      {data && tab === 'scanners' && (
        <div className="space-y-4">
          <div className="flex gap-1 flex-wrap">
            {Object.keys(data.scanner_groups || {}).map((g) => (
              <button key={g} onClick={() => setGroup(g)} className={`text-xs px-3 py-1.5 rounded-full border ${group === g ? 'bg-brand-600 text-white border-brand-500' : 'bg-surface-3 text-gray-400 border-surface-4'}`}>{g}</button>))}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {groupKeys.map((k) => {
              const s = data.scanners[k];
              if (!s) return null;
              return (
                <Card key={k} title={s.label} sub={s.description}
                  icon={s.direction === 'bullish' ? TrendingUp : s.direction === 'bearish' ? TrendingDown : Activity}
                  right={<span className="text-xs text-gray-400">{s.count} stocks</span>}>
                  <div className="max-h-[420px] overflow-y-auto">
                    {s.rows.length ? s.rows.map((r) => <StockRow key={r.symbol} r={r} onPick={openStock} />)
                      : <div className="px-4 py-8 text-center text-gray-500 text-sm">No stocks match right now.</div>}
                  </div>
                  {s.data_note && <div className="px-3 py-2 text-[11px] text-amber-300/80 bg-amber-500/5 border-t border-surface-3">{s.data_note}</div>}
                </Card>
              );
            })}
          </div>
          <Disclaimer />
        </div>
      )}

      {data && tab === 'ideas' && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <span className="text-sm text-gray-400">{(data.ideas || []).length} research setups · score ≥ {cfg?.idea_min_score ?? 6.5}</span>
            <button onClick={exportIdeas} disabled={!(data.ideas || []).length} className="ml-auto flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-40"><Download className="w-3.5 h-3.5" /> CSV</button>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {(data.ideas || []).map((i) => <IdeaCard key={i.symbol} idea={i} onPick={openStock} />)}
            {!(data.ideas || []).length && <div className="col-span-full py-10 text-center text-gray-500 text-sm">No setup clears the threshold right now. Lower <b>Idea min score</b> in Settings to see more marginal candidates.</div>}
          </div>
          <Disclaimer />
        </div>
      )}

      {data && tab === 'scores' && (
        <div className="space-y-3">
          <div className="flex items-center gap-2 bg-surface-3 border border-surface-4 rounded-lg px-3 w-72">
            <Search className="w-3.5 h-3.5 text-gray-500" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search symbol or sector…" className="bg-transparent py-1.5 text-sm text-gray-200 focus:outline-none w-full" />
          </div>
          <Card title="Stock Scores" icon={Flame} sub="Technical strength only — the broker feed carries no earnings/valuation data">
            <div className="overflow-x-auto"><table className="w-full text-xs whitespace-nowrap">
              <thead className="bg-surface-3 text-gray-300"><tr>{['Symbol', 'Sector', 'LTP', 'Chg%', 'RVOL', 'VWAP', '52W H', '52W L', 'Score', 'Grade'].map((h, i) => <th key={h} className={`px-2.5 py-2 font-semibold ${i < 2 ? 'text-left' : 'text-right'}`}>{h}</th>)}</tr></thead>
              <tbody>{filteredScores.map((r) => (
                <tr key={r.symbol} onClick={() => openStock(r.symbol)} className="border-t border-surface-3/40 hover:bg-surface-3/20 cursor-pointer">
                  <td className="px-2.5 py-1.5 text-left text-brand-300 font-semibold">{r.symbol}</td>
                  <td className="px-2.5 py-1.5 text-left text-gray-500">{r.sector}</td>
                  <td className="px-2.5 py-1.5 text-right text-gray-200">₹{NUM(r.ltp)}</td>
                  <td className={`px-2.5 py-1.5 text-right ${pc(r.change_pct)}`}>{PCT(r.change_pct)}</td>
                  <td className="px-2.5 py-1.5 text-right text-gray-300">{r.rvol == null ? 'N/A' : `${r.rvol}x`}</td>
                  <td className={`px-2.5 py-1.5 text-right ${r.vwap && r.ltp >= r.vwap ? 'text-emerald-400' : 'text-red-400'}`}>{r.vwap ? NUM(r.vwap, 0) : 'N/A'}</td>
                  <td className="px-2.5 py-1.5 text-right text-gray-400">{r.high_52w ? NUM(r.high_52w, 0) : '—'}</td>
                  <td className="px-2.5 py-1.5 text-right text-gray-400">{r.low_52w ? NUM(r.low_52w, 0) : '—'}</td>
                  <td className="px-2.5 py-1.5 text-right font-bold" style={{ color: scoreColor(r.score) }}>{r.score}</td>
                  <td className="px-2.5 py-1.5 text-right text-gray-300">{r.grade}</td>
                </tr>
              ))}</tbody>
            </table></div>
          </Card>
          <Disclaimer />
        </div>
      )}

      {tab === 'news' && (
        <Card title="Market Buzz" icon={Newspaper} sub={news?.available ? `${(news.sources || []).join(', ')}` : 'Headlines from the existing news module'}>
          <div>
            {(news?.items || []).map((n, i) => (
              <a key={i} href={n.link || n.url || '#'} target="_blank" rel="noreferrer" className="block px-4 py-2.5 border-b border-surface-3/40 last:border-0 hover:bg-surface-3/20">
                <div className="text-sm text-gray-200">{n.title || n.headline || String(n)}</div>
                <div className="text-[11px] text-gray-500 mt-0.5">{n.source || ''}{n.published ? ` · ${n.published}` : ''}</div>
              </a>
            ))}
            {!(news?.items || []).length && <div className="px-4 py-8 text-center text-gray-500 text-sm">{news?.note || 'No headlines available right now.'}</div>}
          </div>
        </Card>
      )}

      {tab === 'settings' && cfg && <SettingsPanel cfg={cfg} setCfg={setCfg} flash={flash} showErr={showErr} reload={load} />}

      {detail && <StockDrawer d={detail} onClose={() => setDetail(null)} />}
    </div>
  );
}

function SettingsPanel({ cfg, setCfg, flash, showErr, reload }) {
  const [local, setLocal] = useState(cfg);
  const patch = (k, v) => setLocal((c) => ({ ...c, [k]: v }));
  const num = (k, step = 1) => <input type="number" step={step} value={local[k] ?? ''} onChange={(e) => patch(k, e.target.value === '' ? '' : Number(e.target.value))} className={`w-full ${sel}`} />;
  const save = async () => {
    const r = await api.mihConfigSave(local);
    if (r.status === 'ok') { setLocal(r.config); setCfg(r.config); flash('Settings saved'); reload(); } else showErr(r.message);
  };
  return (
    <div className="space-y-4 max-w-4xl">
      <Card title="Universe & Screens" icon={SlidersHorizontal}>
        <div className="p-4 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          <div><label className={lbl}>Rows per card</label>{num('top_n')}</div>
          <div><label className={lbl}>Max stocks (0=all)</label>{num('max_stocks')}</div>
          <div><label className={lbl}>Min price ₹</label>{num('min_price', 1)}</div>
          <div><label className={lbl}>Min volume</label>{num('min_volume', 1000)}</div>
          <div><label className={lbl}>Open=Low tol %</label>{num('open_eq_tol_pct', 0.05)}</div>
          <div><label className={lbl}>Gap %</label>{num('gap_pct', 0.1)}</div>
          <div><label className={lbl}>Vol shocker RVOL</label>{num('vol_shocker_rvol', 0.1)}</div>
          <div><label className={lbl}>Breakout RVOL</label>{num('breakout_rvol', 0.1)}</div>
          <div><label className={lbl}>Breakout chg %</label>{num('breakout_change_pct', 0.1)}</div>
          <div><label className={lbl}>Near 52W %</label>{num('near_52w_pct', 0.1)}</div>
          <div><label className={lbl}>Enrich cap / refresh</label>{num('enrich_cap', 5)}</div>
        </div>
      </Card>
      <Card title="Research Setups" icon={Target}>
        <div className="p-4 grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div><label className={lbl}>Idea min score</label>{num('idea_min_score', 0.1)}</div>
          <div><label className={lbl}>Stop (ATR ×)</label>{num('idea_sl_atr', 0.1)}</div>
          <div><label className={lbl}>Target (ATR ×)</label>{num('idea_target_atr', 0.1)}</div>
          <div><label className={lbl}>Max ideas</label>{num('idea_max')}</div>
        </div>
      </Card>
      <button onClick={save} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold"><Save className="w-4 h-4" /> Save Settings</button>
      <Disclaimer />
    </div>
  );
}

function StockDrawer({ d, onClose }) {
  const s = d.snapshot; const sc = d.score;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-3 overflow-y-auto" onClick={onClose}>
      <div className="bg-surface-1 border border-surface-3 rounded-xl w-full max-w-3xl my-6" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-surface-3 sticky top-0 bg-surface-1 rounded-t-xl">
          <div className="flex items-center gap-2">
            <span className="text-lg font-bold text-gray-100">{d.symbol}</span>
            {s && <span className="text-xs text-gray-500">{s.sector}</span>}
            {sc && <ScoreRing score={sc.score} size={36} />}
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white"><X className="w-5 h-5" /></button>
        </div>
        {d.loading || !s ? <div className="p-10 text-center text-gray-500"><Loader2 className="w-5 h-5 animate-spin inline" /> Loading…</div> : (
          <div className="p-4 space-y-4">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {[['LTP', `₹${NUM(s.ltp)}`, ''], ['Change', PCT(s.change_pct), pc(s.change_pct)], ['Open', `₹${NUM(s.open)}`, ''], ['Prev Close', `₹${NUM(s.prev_close)}`, ''],
                ['Day High', `₹${NUM(s.high)}`, ''], ['Day Low', `₹${NUM(s.low)}`, ''], ['VWAP', s.vwap ? `₹${NUM(s.vwap)}` : 'N/A', ''], ['RVOL', s.rvol ? `${s.rvol}x` : 'N/A', ''],
                ['52W High', s.high_52w ? `₹${NUM(s.high_52w)}` : '—', ''], ['52W Low', s.low_52w ? `₹${NUM(s.low_52w)}` : '—', ''], ['Volume', INT(s.volume), ''], ['ATR', s.atr ? NUM(s.atr) : '—', '']].map(([k, v, c]) => (
                <div key={k} className="bg-surface-3/40 border border-surface-3 rounded-lg px-3 py-2"><div className="text-[10px] uppercase text-gray-500">{k}</div><div className={`text-sm ${c || 'text-gray-200'}`}>{v}</div></div>
              ))}
            </div>
            <div className="bg-surface-2 border border-surface-3 rounded-lg p-3">
              <div className="text-sm font-semibold text-gray-200 mb-2">Score breakdown — {sc.score}/10 · {sc.grade}</div>
              <div className="space-y-1.5 text-xs">
                {Object.entries(sc.breakdown).map(([k, b]) => (
                  <div key={k}>
                    <div className="flex justify-between text-gray-400"><span className="capitalize">{k}</span><span className="text-gray-200">{b.pts} / {b.max}</span></div>
                    <div className="h-2 rounded bg-surface-3 overflow-hidden"><div className="h-full rounded" style={{ width: `${b.max ? (b.pts / b.max) * 100 : 0}%`, background: scoreColor(sc.score) }} /></div>
                  </div>
                ))}
              </div>
              <div className="text-[10px] text-gray-600 mt-2">Technical score only — no earnings, balance-sheet or valuation inputs are available from the market feed.</div>
            </div>
            {!!(d.screens || []).length && (
              <div className="flex flex-wrap gap-1.5">
                {d.screens.map((x) => (
                  <span key={x.key} className={`text-[11px] px-2 py-0.5 rounded-full border ${x.direction === 'bullish' ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30' : x.direction === 'bearish' ? 'bg-red-500/10 text-red-300 border-red-500/30' : 'bg-surface-3 text-gray-400 border-surface-4'}`}>{x.label}</span>
                ))}
              </div>
            )}
            {d.idea ? <IdeaCard idea={d.idea} /> : <div className="text-sm text-gray-500">No research setup qualifies for this stock at the current score threshold.</div>}
            <Disclaimer />
          </div>
        )}
      </div>
    </div>
  );
}

function Disclaimer() {
  return (
    <div className="text-[11px] text-gray-500 bg-surface-2/60 border border-surface-3 rounded-lg px-3 py-2 flex items-start gap-2">
      <Info className="w-4 h-4 mt-0.5 shrink-0 text-amber-400" />
      <span><strong className="text-amber-300">Research output — not investment advice.</strong> Levels are derived from price/volume structure (ATR, VWAP, 20-day and 52-week ranges). Scores rank technical strength only; the market feed carries no earnings or valuation data. No orders are placed by this module.</span>
    </div>
  );
}
