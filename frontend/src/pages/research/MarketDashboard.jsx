import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  RefreshCw, Loader2, AlertCircle, Pause, Play, Gauge, BarChart3, Layers,
  Activity, Globe, Newspaper, ArrowRightLeft, TrendingUp, Hash, Grid3x3,
  ArrowUp, ArrowDown, Minus, Building2, Zap, Clock, ExternalLink,
} from 'lucide-react';
import { api } from '../../api';

const INR = (v, d = 2) => (v ?? 0).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });

/* Normalise every source to a common {bias, value, detail} shape */
const BIAS = {
  Bullish: { c: 'text-emerald-400', bg: 'bg-emerald-500/10', bd: 'border-emerald-500/40', dot: 'bg-emerald-500' },
  Bearish: { c: 'text-red-400', bg: 'bg-red-500/10', bd: 'border-red-500/40', dot: 'bg-red-500' },
  Neutral: { c: 'text-amber-400', bg: 'bg-amber-500/10', bd: 'border-amber-500/40', dot: 'bg-amber-500' },
};
function normBias(b) {
  if (!b) return 'Neutral';
  const s = String(b).toLowerCase();
  if (s.includes('bull')) return 'Bullish';
  if (s.includes('bear')) return 'Bearish';
  return 'Neutral';
}
function Arrow({ bias, className = 'w-4 h-4' }) {
  const b = normBias(bias);
  if (b === 'Bullish') return <ArrowUp className={`${className} text-emerald-400`} />;
  if (b === 'Bearish') return <ArrowDown className={`${className} text-red-400`} />;
  return <Minus className={`${className} text-amber-400`} />;
}

/* One confirmation tile */
function Tile({ icon: Icon, title, bias, value, detail, sub }) {
  const b = normBias(bias);
  const s = BIAS[b];
  return (
    <div className={`rounded-xl border ${s.bd} ${s.bg} p-3 flex flex-col gap-1`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-gray-400">
          {Icon && <Icon className="w-3.5 h-3.5" />} {title}
        </div>
        <Arrow bias={b} />
      </div>
      <div className={`text-lg font-bold ${s.c} leading-tight`}>{value ?? b}</div>
      {detail && <div className="text-[11px] text-gray-400 leading-snug">{detail}</div>}
      {sub && <div className="text-[10px] text-gray-500">{sub}</div>}
    </div>
  );
}

export default function MarketDashboard() {
  const [pulse, setPulse] = useState(null);
  const [nifty, setNifty] = useState(null);
  const [sent, setSent] = useState(null);
  const [news, setNews] = useState(null);
  const [auto, setAuto] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [updated, setUpdated] = useState('');
  const timer = useRef(null);

  const showErr = (m) => { setError(m); setTimeout(() => setError(''), 4500); };

  const loadAll = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    // Fire all three independent sources in parallel — one slow source never blocks the others.
    const [p, n, s, nw] = await Promise.allSettled([
      api.researchMarketPulse(),
      api.researchNiftySentiment(),
      api.researchSentiment(false),
      api.researchNewsSentiment(),
    ]);
    if (p.status === 'fulfilled' && p.value?.status === 'ok') setPulse(p.value);
    if (n.status === 'fulfilled' && n.value?.status === 'ok') setNifty(n.value);
    if (s.status === 'fulfilled' && s.value?.status === 'ok') setSent(s.value);
    if (nw.status === 'fulfilled' && nw.value) setNews(nw.value);
    const anyOk = [p, n, s].some((r) => r.status === 'fulfilled' && r.value?.status === 'ok');
    if (!anyOk && !silent) showErr('Could not load market data — is Zerodha connected?');
    setUpdated(new Date().toLocaleTimeString('en-IN', { hour12: false }));
    if (!silent) setLoading(false);
  }, []);

  useEffect(() => { loadAll(false); }, [loadAll]);
  useEffect(() => {
    if (timer.current) clearInterval(timer.current);
    if (auto) timer.current = setInterval(() => loadAll(true), 30000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [auto, loadAll]);

  const sig = pulse?.signals || {};
  const macroBias = sent ? (sent.macro_score > 0.5 ? 'Bullish' : sent.macro_score < -0.5 ? 'Bearish' : 'Neutral') : 'Neutral';
  const domesticBias = sent ? (((sent.derivative_score + sent.technical_score) / 2) > 0.5 ? 'Bullish'
    : ((sent.derivative_score + sent.technical_score) / 2) < -0.5 ? 'Bearish' : 'Neutral') : 'Neutral';

  // FII/DII from the sentiment engine indicator rows
  const fiiRow = sent?.indicators?.find((r) => String(r.indicator).startsWith('FII net'));
  const diiRow = sent?.indicators?.find((r) => String(r.indicator).startsWith('DII net'));
  const fiiDiiBias = fiiRow ? (fiiRow.score > 0 ? 'Bullish' : fiiRow.score < 0 ? 'Bearish' : 'Neutral') : 'Neutral';
  const topSector = (nifty?.sectors || [])[0];

  // Build the 10 confirmation tiles from the note
  const tiles = [
    { icon: BarChart3, title: 'Cumulative Volume', bias: sig.cumulative_volume?.bias,
      value: sig.cumulative_volume?.trend || '—', detail: sig.cumulative_volume?.detail },
    { icon: Grid3x3, title: '10-Stock Weightage', bias: nifty?.top_card?.bias,
      value: nifty?.top_card?.bias, detail: nifty ? `Score ${nifty.top_card.score >= 0 ? '+' : ''}${nifty.top_card.score} · ${nifty.top_card.advancers}▲/${nifty.top_card.decliners}▼` : '' },
    { icon: Layers, title: 'Major Sector Contributor', bias: topSector?.bias,
      value: topSector?.sector || '—', detail: topSector ? `Wtd ${topSector.weighted_performance >= 0 ? '+' : ''}${topSector.weighted_performance}% · contrib ${topSector.contribution}` : '' },
    { icon: TrendingUp, title: 'NIFTY 20/200 DMA', bias: sig.dma?.bias,
      value: sig.dma?.available ? `${sig.dma.cross === 'golden' ? 'Golden' : sig.dma.cross === 'death' ? 'Death' : ''} ${sig.dma.bias}`.trim() : '—',
      detail: sig.dma?.detail, sub: sig.dma?.available ? `20DMA ${INR(sig.dma.sma20, 0)} · 200DMA ${sig.dma.sma200 ? INR(sig.dma.sma200, 0) : '—'}` : '' },
    { icon: Globe, title: 'Global Sentiment', bias: macroBias,
      value: macroBias, detail: sent ? `Macro score ${sent.macro_score >= 0 ? '+' : ''}${sent.macro_score}` : '' },
    { icon: Newspaper, title: 'India News Sentiment', bias: news?.available ? news.bias : domesticBias,
      value: news?.available ? news.bias : domesticBias,
      detail: news?.available ? `${news.pct_positive}% positive · ${news.total} headlines` : (sent ? 'News feeds loading…' : ''),
      sub: news?.available ? `${(news.sources || []).join(', ')}` : '' },
    { icon: ArrowRightLeft, title: 'FII / DII Net Flow', bias: fiiDiiBias,
      value: fiiRow ? `FII ₹${INR(fiiRow.value, 0)}cr` : '—',
      detail: diiRow ? `DII ₹${INR(diiRow.value, 0)}cr` : (fiiRow ? '' : 'NSE data unavailable') },
    { icon: Activity, title: 'VWAP / P-VWAP', bias: sig.vwap?.bias,
      value: sig.vwap?.available ? sig.vwap.bias : '—', detail: sig.vwap?.detail,
      sub: sig.vwap?.available ? `VWAP ${INR(sig.vwap.vwap, 0)} · P-VWAP ${sig.vwap.prev_vwap ? INR(sig.vwap.prev_vwap, 0) : '—'}` : '' },
    { icon: Hash, title: 'Psychological Level', bias: sig.psychological?.bias,
      value: sig.psychological?.available ? (sig.psychological.breakout !== 'none' ? `Breakout ${sig.psychological.breakout}` : `${sig.psychological.support}–${sig.psychological.resistance}`) : '—',
      detail: sig.psychological?.detail },
    { icon: Building2, title: 'Gann Level (S/R)', bias: sig.gann?.bias,
      value: sig.gann?.available ? (sig.gann.breakout !== 'none' ? `Breakout ${sig.gann.breakout}` : sig.gann.at_level ? `At ${sig.gann.at_level}` : 'In band') : '—',
      detail: sig.gann?.detail,
      sub: sig.gann?.available ? `Sup ${INR(sig.gann.support, 0)} · Res ${INR(sig.gann.resistance, 0)}` : '' },
    { icon: Clock, title: '5-Day First-Hour', bias: sig.five_day_fh?.bias,
      value: sig.five_day_fh?.available ? (sig.five_day_fh.breakout !== 'none' ? `Breakout ${sig.five_day_fh.breakout}` : 'In range') : '—',
      detail: sig.five_day_fh?.detail,
      sub: sig.five_day_fh?.available ? `Low ${INR(sig.five_day_fh.support, 0)} · High ${INR(sig.five_day_fh.resistance, 0)}` : '' },
  ];

  // Overall verdict = blend of the three engines' headline reads
  const overall = useMemo(() => {
    const votes = [];
    if (pulse?.confirmation) votes.push(pulse.confirmation.net);
    if (nifty?.overall) votes.push(nifty.overall.score > 0.05 ? 1 : nifty.overall.score < -0.05 ? -1 : 0);
    if (sent) votes.push(sent.final_score > 0.5 ? 1 : sent.final_score < -0.5 ? -1 : 0);
    const tileVotes = tiles.map((t) => normBias(t.bias)).map((b) => b === 'Bullish' ? 1 : b === 'Bearish' ? -1 : 0);
    const bull = tileVotes.filter((v) => v > 0).length;
    const bear = tileVotes.filter((v) => v < 0).length;
    const net = bull - bear;
    const bias = net > 0 ? 'Bullish' : net < 0 ? 'Bearish' : 'Neutral';
    const conf = Math.round((Math.max(bull, bear) / (tileVotes.length || 1)) * 100);
    return { bias, bull, bear, neutral: tileVotes.length - bull - bear, total: tileVotes.length, conf };
  }, [pulse, nifty, sent]); // eslint-disable-line react-hooks/exhaustive-deps

  const ob = BIAS[normBias(overall.bias)];
  const spot = pulse?.spot ?? sent?.indicators?.find((r) => r.indicator?.includes('NIFTY'))?.value ?? 0;

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-[1500px] mx-auto">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold text-gray-100">Market Dashboard</h1>
            <span className="px-2 py-0.5 rounded-full bg-brand-600/15 text-brand-400 text-xs font-semibold border border-brand-500/20">Research · Live</span>
          </div>
          <p className="text-gray-500 text-sm mt-0.5">One-glance NIFTY bias — every confirmation in one place. Read-only, auto-refresh 30s.</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-gray-500">Updated {updated || '—'}</span>
          <button onClick={() => loadAll(false)} disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">
            {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />} Refresh
          </button>
          <button onClick={() => setAuto((a) => !a)}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border transition ${auto ? 'bg-emerald-600/20 text-emerald-400 border-emerald-500/40' : 'bg-surface-3 text-gray-400 border-surface-4'}`}>
            {auto ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />} Auto {auto ? 'ON' : 'OFF'}
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-red-400 text-sm">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      )}

      {/* Hero — overall bias */}
      <div className={`rounded-2xl border ${ob.bd} ${ob.bg} p-5`}>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            <div className={`w-16 h-16 rounded-2xl ${ob.bg} border ${ob.bd} flex items-center justify-center`}>
              <Arrow bias={overall.bias} className="w-8 h-8" />
            </div>
            <div>
              <div className="text-[11px] uppercase tracking-wide text-gray-400">Overall Market Bias</div>
              <div className={`text-3xl font-bold ${ob.c}`}>{overall.bias}</div>
              <div className="text-xs text-gray-400 mt-0.5">
                {overall.bull} bullish · {overall.bear} bearish · {overall.neutral} neutral of {overall.total} signals
              </div>
            </div>
          </div>
          <div className="flex items-center gap-6">
            <div className="text-center">
              <div className="text-[11px] text-gray-500 uppercase">NIFTY</div>
              <div className="text-2xl font-bold text-gray-100">{spot ? INR(spot, 2) : '—'}</div>
              {pulse?.day_change_pct != null && (
                <div className={`text-xs ${pulse.day_change_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {pulse.day_change_pct >= 0 ? '+' : ''}{pulse.day_change_pct}%
                </div>
              )}
            </div>
            <div className="text-center min-w-[110px]">
              <div className="text-[11px] text-gray-500 uppercase">Agreement</div>
              <div className={`text-2xl font-bold ${ob.c}`}>{overall.conf}%</div>
              <div className="h-2 mt-1 rounded-full bg-surface-4 overflow-hidden">
                <div className={`h-full ${ob.dot}`} style={{ width: `${overall.conf}%` }} />
              </div>
            </div>
            {sent?.action && (
              <div className="text-center">
                <div className="text-[11px] text-gray-500 uppercase">Signal</div>
                <div className={`text-lg font-bold ${sent.action.color === 'green' ? 'text-emerald-400' : sent.action.color === 'red' ? 'text-red-400' : 'text-amber-400'}`}>
                  {sent.action.label}
                </div>
                <div className="text-[10px] text-gray-500">{sent.action.strength} · conf {sent.confidence}%</div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 10 confirmation tiles */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
        {tiles.map((t) => <Tile key={t.title} {...t} />)}
      </div>

      {/* Top contributors + sectors */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
          <div className="px-4 py-2 text-sm font-semibold text-gray-200 border-b border-surface-3 flex items-center gap-2">
            <BarChart3 className="w-4 h-4 text-brand-400" /> Top Contributors (market impact)
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs whitespace-nowrap">
              <thead><tr className="text-gray-500 border-b border-surface-3">
                <th className="px-2 py-1.5 text-left">#</th><th className="px-2 py-1.5 text-left">Stock</th>
                <th className="px-2 py-1.5 text-right">Wt%</th><th className="px-2 py-1.5 text-right">% Chg</th>
                <th className="px-2 py-1.5 text-right">Wtd</th><th className="px-2 py-1.5 text-center">Trend</th>
              </tr></thead>
              <tbody>
                {(nifty?.top_stocks || []).slice(0, 8).map((r) => (
                  <tr key={r.symbol} className="border-b border-surface-3/40">
                    <td className="px-2 py-1.5 text-gray-500">{r.impact_rank}</td>
                    <td className="px-2 py-1.5 text-gray-100">{r.name}</td>
                    <td className="px-2 py-1.5 text-right text-brand-300">{INR(r.weight, 2)}</td>
                    <td className={`px-2 py-1.5 text-right ${r.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{r.change_pct >= 0 ? '+' : ''}{r.change_pct}%</td>
                    <td className={`px-2 py-1.5 text-right font-semibold ${r.weighted_score >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{r.weighted_score >= 0 ? '+' : ''}{r.weighted_score}</td>
                    <td className="px-2 py-1.5 text-center"><Arrow bias={r.trend} className="w-3.5 h-3.5 inline" /></td>
                  </tr>
                ))}
                {!nifty && <tr><td colSpan={6} className="px-2 py-6 text-center text-gray-500">Loading…</td></tr>}
              </tbody>
            </table>
          </div>
        </div>

        <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
          <div className="px-4 py-2 text-sm font-semibold text-gray-200 border-b border-surface-3 flex items-center gap-2">
            <Layers className="w-4 h-4 text-brand-400" /> Sector Strength
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs whitespace-nowrap">
              <thead><tr className="text-gray-500 border-b border-surface-3">
                <th className="px-2 py-1.5 text-left">Sector</th><th className="px-2 py-1.5 text-right">Wt%</th>
                <th className="px-2 py-1.5 text-right">Wtd %</th><th className="px-2 py-1.5 text-right">Contrib</th>
                <th className="px-2 py-1.5 text-center">Bias</th>
              </tr></thead>
              <tbody>
                {(nifty?.sectors || []).slice(0, 8).map((r) => {
                  const s = BIAS[normBias(r.bias)];
                  return (
                    <tr key={r.sector} className="border-b border-surface-3/40">
                      <td className="px-2 py-1.5 text-gray-100">{r.sector}</td>
                      <td className="px-2 py-1.5 text-right text-brand-300">{INR(r.weight, 2)}</td>
                      <td className={`px-2 py-1.5 text-right ${r.weighted_performance >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{r.weighted_performance >= 0 ? '+' : ''}{r.weighted_performance}%</td>
                      <td className={`px-2 py-1.5 text-right font-semibold ${r.contribution >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{r.contribution >= 0 ? '+' : ''}{r.contribution}</td>
                      <td className="px-2 py-1.5 text-center"><span className={`px-1.5 py-0.5 rounded text-[10px] border ${s.bd} ${s.bg} ${s.c}`}>{normBias(r.bias)}</span></td>
                    </tr>
                  );
                })}
                {!nifty && <tr><td colSpan={5} className="px-2 py-6 text-center text-gray-500">Loading…</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Global market sessions + reasons */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
          <div className="px-4 py-2 text-sm font-semibold text-gray-200 border-b border-surface-3 flex items-center gap-2">
            <Globe className="w-4 h-4 text-brand-400" /> Global Markets
          </div>
          <div className="p-3 grid grid-cols-2 sm:grid-cols-3 gap-2">
            {(sent?.markets || []).map((m) => (
              <div key={m.name} className="flex items-center justify-between bg-surface-3/50 rounded-lg px-2.5 py-1.5">
                <span className="text-xs text-gray-300">{m.name}</span>
                <span className={`text-[10px] px-1.5 py-0.5 rounded ${m.status === 'Open' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-surface-4 text-gray-500'}`}>{m.status}</span>
              </div>
            ))}
            {!sent && <div className="col-span-full text-xs text-gray-500 text-center py-3">Loading global sessions…</div>}
          </div>
        </div>

        <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
          <div className="px-4 py-2 text-sm font-semibold text-gray-200 border-b border-surface-3 flex items-center gap-2">
            <Zap className="w-4 h-4 text-brand-400" /> Why — key drivers
          </div>
          <div className="p-3 flex flex-wrap gap-1.5">
            {(sent?.reasons || []).map((r, i) => (
              <span key={i} className="text-[11px] px-2 py-1 rounded-lg bg-surface-3/60 text-gray-300 border border-surface-4">{r}</span>
            ))}
            {(!sent?.reasons?.length) && <div className="text-xs text-gray-500 py-2">No standout drivers right now.</div>}
          </div>
        </div>
      </div>

      {/* News headlines */}
      <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
        <div className="px-4 py-2 text-sm font-semibold text-gray-200 border-b border-surface-3 flex items-center gap-2">
          <Newspaper className="w-4 h-4 text-brand-400" /> India Market News
          {news?.available && (
            <span className={`ml-2 text-[11px] px-2 py-0.5 rounded ${BIAS[normBias(news.bias)].bg} ${BIAS[normBias(news.bias)].c}`}>
              {news.bias} · {news.pct_positive}% positive
            </span>
          )}
          <span className="ml-auto text-[10px] text-gray-500">{news?.available ? (news.sources || []).join(' · ') : ''}</span>
        </div>
        <div className="divide-y divide-surface-3/50 max-h-72 overflow-y-auto">
          {(news?.headlines || []).map((h, i) => (
            <a key={i} href={h.link || '#'} target="_blank" rel="noreferrer"
              className="flex items-start gap-2 px-4 py-2 hover:bg-surface-3/20 group">
              <span className={`mt-1 w-2 h-2 rounded-full shrink-0 ${h.tone === 'pos' ? 'bg-emerald-500' : h.tone === 'neg' ? 'bg-red-500' : 'bg-gray-500'}`} />
              <span className="text-xs text-gray-300 group-hover:text-gray-100 leading-snug flex-1">{h.title}</span>
              <span className="text-[10px] text-gray-500 shrink-0 flex items-center gap-1">{h.source} {h.link && <ExternalLink className="w-3 h-3" />}</span>
            </a>
          ))}
          {(!news?.available) && (
            <div className="px-4 py-6 text-center text-xs text-gray-500">
              {news && news.status === 'error' ? 'News feeds unreachable right now.' : 'Loading Indian market news…'}
            </div>
          )}
        </div>
      </div>

      <p className="text-[11px] text-gray-600 text-center pt-1">
        Research &amp; analysis only — not investment advice. Composed from live Zerodha data + free public news RSS; sources cached independently (Sentiment 60s · Pulse 30s · News 10m).
      </p>
    </div>
  );
}
