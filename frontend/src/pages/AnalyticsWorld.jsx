/**
 * Analytics World — real-data swing/positional screener.
 *
 * Strictly read-only screener: pulls a single payload from
 * `/portfolio/analytics-world` (live Kite quotes + cached daily
 * historicals) and surfaces four sections — IPOs, Swing, Momentum,
 * Breakouts — plus market status, sector heat, sparkline, "why"
 * reasoning, Trade prefill, and Add-to-Watchlist actions.
 */
import React, {
  useCallback, useEffect, useMemo, useRef, useState,
} from 'react';
import { Link } from 'react-router-dom';
import {
  LineChart, Line, ResponsiveContainer, YAxis,
} from 'recharts';
import { api } from '../api';
import { useToast } from '../ToastContext';
import {
  TrendingUp, TrendingDown, RefreshCw, Activity, Rocket, Flame,
  Crosshair, BarChart3, ShieldCheck, IndianRupee, ArrowLeft,
  Info, Bookmark, ExternalLink, Filter, ArrowUpDown, X, Clock,
} from 'lucide-react';

/* ── helpers ───────────────────────────────────── */
const INR = (v, d = 2) =>
  (Number(v) || 0).toLocaleString('en-IN', {
    minimumFractionDigits: d, maximumFractionDigits: d,
  });

const COMPACT_INR = (v) => {
  const n = Number(v) || 0;
  if (n >= 1e7) return `₹${(n / 1e7).toFixed(2)} Cr`;
  if (n >= 1e5) return `₹${(n / 1e5).toFixed(2)} L`;
  if (n >= 1e3) return `₹${(n / 1e3).toFixed(1)} K`;
  return `₹${n.toFixed(0)}`;
};

const COMPACT_NUM = (v) => {
  const n = Number(v) || 0;
  if (n >= 1e7) return `${(n / 1e7).toFixed(2)}Cr`;
  if (n >= 1e5) return `${(n / 1e5).toFixed(2)}L`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return `${n}`;
};

const VERDICT_META = {
  'Strong Buy': { text: 'text-green-400', bg: 'bg-green-500/15', ring: 'border-green-500/30' },
  Buy:          { text: 'text-emerald-400', bg: 'bg-emerald-500/15', ring: 'border-emerald-500/30' },
  Watch:        { text: 'text-yellow-400', bg: 'bg-yellow-500/15', ring: 'border-yellow-500/30' },
  Avoid:        { text: 'text-red-400', bg: 'bg-red-500/15', ring: 'border-red-500/30' },
};

const MARKET_META = {
  open:        { label: 'Market Open',  dot: 'bg-green-400', text: 'text-green-400', bg: 'bg-green-500/10', ring: 'border-green-500/30' },
  pre_open:    { label: 'Pre-Open',     dot: 'bg-amber-400', text: 'text-amber-400', bg: 'bg-amber-500/10', ring: 'border-amber-500/30' },
  post_close:  { label: 'Post-Close',   dot: 'bg-amber-400', text: 'text-amber-400', bg: 'bg-amber-500/10', ring: 'border-amber-500/30' },
  closed:      { label: 'Market Closed',dot: 'bg-red-400',   text: 'text-red-400',   bg: 'bg-red-500/10',   ring: 'border-red-500/30' },
};

const SECTION_DEFS = [
  { id: 'ipos',       label: 'IPOs',       title: 'Undervalued / Recent IPOs', subtitle: 'Recently listed, affordable price points — entry only when structure aligns', icon: Rocket,       accent: 'bg-blue-500/80',    empty: 'No IPO candidates passing the filter right now.' },
  { id: 'swing',      label: 'Swing',      title: 'Swing Trade Candidates',    subtitle: 'Mid-priced names with constructive 5-day structure',                        icon: ShieldCheck,  accent: 'bg-emerald-500/80', empty: 'No swing setups matching the criteria today.' },
  { id: 'momentum',   label: 'Momentum',   title: 'Momentum & Up-Days',        subtitle: '≥4 of last 5 sessions green or ≥5% week return — riding strength',           icon: Flame,        accent: 'bg-orange-500/80',  empty: 'No high-momentum names firing today.' },
  { id: 'breakouts',  label: 'Breakouts',  title: 'Resistance Breakouts',      subtitle: 'Price at or near 20-day high with volume confirmation',                       icon: Crosshair,    accent: 'bg-brand-500',      empty: 'No fresh breakouts on the radar.' },
];

const SORT_OPTIONS = [
  { value: 'rr',       label: 'R:R',           pick: (x) => x.risk_reward || 0 },
  { value: 'ret5',     label: '5D Return',     pick: (x) => x.return_5d_pct || 0 },
  { value: 'change',   label: 'Day Change',    pick: (x) => x.change_pct || 0 },
  { value: 'updays',   label: 'Up-Days',       pick: (x) => x.up_days_5d || 0 },
  { value: 'volume',   label: 'Volume',        pick: (x) => x.volume || 0 },
  { value: 'price',    label: 'Price',         pick: (x) => x.last_price || 0 },
];

/* ── small atoms ───────────────────────────────── */
function StatPill({ icon: Icon, label, value, accent = 'text-white' }) {
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-lg px-3 py-2 flex items-center gap-2.5 min-w-[120px]">
      <div className="w-7 h-7 rounded-md bg-brand-500/10 flex items-center justify-center">
        <Icon className="w-3.5 h-3.5 text-brand-400" />
      </div>
      <div className="flex flex-col leading-tight">
        <span className="text-[10px] uppercase tracking-wider text-gray-500">{label}</span>
        <span className={`text-sm font-semibold ${accent}`}>{value}</span>
      </div>
    </div>
  );
}

function VerdictBadge({ verdict, onClick, hasReasons }) {
  const m = VERDICT_META[verdict] || VERDICT_META.Watch;
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!hasReasons}
      title={hasReasons ? 'Why this verdict?' : ''}
      className={`text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded-full border
                  inline-flex items-center gap-1 ${m.bg} ${m.text} ${m.ring}
                  ${hasReasons ? 'hover:brightness-125 cursor-pointer' : 'cursor-default'}`}
    >
      {verdict}
      {hasReasons && <Info className="w-2.5 h-2.5" />}
    </button>
  );
}

function ChangeChip({ pct }) {
  const positive = (pct || 0) >= 0;
  const Icon = positive ? TrendingUp : TrendingDown;
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-semibold ${positive ? 'text-green-400' : 'text-red-400'}`}>
      <Icon className="w-3 h-3" />
      {positive ? '+' : ''}{(pct || 0).toFixed(2)}%
    </span>
  );
}

function SignalDot({ on, label }) {
  return (
    <span
      title={label}
      className={`inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded
        ${on ? 'bg-brand-500/15 text-brand-400 border border-brand-500/25'
            : 'bg-surface-3 text-gray-500 border border-surface-3'}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${on ? 'bg-brand-400' : 'bg-gray-500'}`} />
      {label}
    </span>
  );
}

function MarketStatusPill({ status, serverTime }) {
  const m = MARKET_META[status] || MARKET_META.closed;
  return (
    <span className={`inline-flex items-center gap-2 text-xs font-medium px-2.5 py-1 rounded-full border ${m.bg} ${m.ring} ${m.text}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${m.dot} animate-pulse`} />
      {m.label}
      {serverTime && (
        <span className="text-gray-500 inline-flex items-center gap-1">
          <Clock className="w-3 h-3" />
          {serverTime.split(' ').pop()}
        </span>
      )}
    </span>
  );
}

function Sparkline({ closes, positive }) {
  const data = useMemo(
    () => (closes || []).map((c, i) => ({ i, c: Number(c) || 0 })),
    [closes]
  );
  if (data.length < 2) {
    return <div className="h-[34px] flex items-center justify-center text-[10px] text-gray-500">no chart</div>;
  }
  const stroke = positive ? '#34d399' : '#f87171';
  return (
    <div className="h-[34px] w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 2, right: 0, left: 0, bottom: 2 }}>
          <YAxis hide domain={['dataMin', 'dataMax']} />
          <Line
            type="monotone"
            dataKey="c"
            stroke={stroke}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function ReasonsPopover({ item, onClose }) {
  if (!item) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        className="bg-surface-1 border border-surface-3 rounded-xl max-w-md w-full p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-4">
          <div>
            <div className="text-xs text-gray-500 uppercase tracking-wider">Why</div>
            <h3 className="text-lg font-bold text-white">
              {item.symbol} <span className="text-sm text-gray-500">·</span>
              <span className="ml-1"><VerdictBadge verdict={item.verdict} /></span>
            </h3>
            <div className="text-xs text-gray-500 mt-0.5">
              Score: <span className="text-white font-semibold">{item.verdict_score ?? 0}</span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="w-7 h-7 rounded-md hover:bg-surface-2 flex items-center justify-center text-gray-500 hover:text-white"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {Array.isArray(item.reasons) && item.reasons.length > 0 ? (
          <ul className="space-y-2">
            {item.reasons.map((r, i) => (
              <li key={i} className="flex items-start gap-2 text-sm">
                <span className={`mt-0.5 w-5 h-5 rounded-full flex items-center justify-center text-[10px] font-bold
                  ${r.sign > 0 ? 'bg-green-500/15 text-green-400'
                    : r.sign < 0 ? 'bg-red-500/15 text-red-400'
                    : 'bg-surface-3 text-gray-400'}`}
                >
                  {r.sign > 0 ? '+' : r.sign < 0 ? '–' : '·'}
                </span>
                <div className="flex-1">
                  <div className="text-white">{r.label}</div>
                  {typeof r.delta === 'number' && r.delta !== 0 && (
                    <div className="text-[10px] text-gray-500">
                      contributes {r.delta > 0 ? '+' : ''}{r.delta} to score
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-gray-500">No additional reasoning available.</p>
        )}
      </div>
    </div>
  );
}

/* ── opportunity card ─────────────────────────── */
function OpportunityCard({ item, onShowReasons, onAddWatchlist }) {
  if (!item) return null;
  const positive = (item.change_pct || 0) >= 0;
  const sparkPositive = (item.return_5d_pct || 0) >= 0;
  const range = Math.max(0.0001, (item.resistance || 0) - (item.support || 0));
  const px = Math.max(item.support || 0, Math.min(item.resistance || 0, item.last_price || 0));
  const pctInRange = ((px - (item.support || 0)) / range) * 100;

  const tradeHref = `/manual-trading?symbol=${encodeURIComponent(item.symbol)}`
    + `&exchange=${encodeURIComponent(item.exchange || 'NSE')}`
    + `&entry=${item.entry ?? ''}`
    + `&stop=${item.stop ?? ''}`
    + `&target=${item.target ?? ''}`;

  return (
    <div className="group relative bg-surface-1 border border-surface-3 rounded-xl p-4 hover:border-brand-500/30 transition-colors">
      {/* header */}
      <div className="flex items-start justify-between mb-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-bold text-white truncate">{item.symbol}</h3>
            <span className="text-[10px] uppercase tracking-wider text-gray-500">{item.exchange}</span>
          </div>
          <div className="text-[11px] text-gray-500 truncate">
            {item.sector}{item.listing_year ? ` · IPO ${item.listing_year}` : ''}
          </div>
        </div>
        <VerdictBadge
          verdict={item.verdict}
          hasReasons={Array.isArray(item.reasons) && item.reasons.length > 0}
          onClick={() => onShowReasons(item)}
        />
      </div>

      {/* price + sparkline */}
      <div className="flex items-baseline justify-between mb-2">
        <div className="flex items-baseline gap-2">
          <span className="text-xl font-bold text-white">₹{INR(item.last_price, 2)}</span>
          <ChangeChip pct={item.change_pct} />
        </div>
        {item.market_cap_cr ? (
          <span className="text-[11px] text-gray-500">M-Cap {COMPACT_INR(item.market_cap_cr * 1e7)}</span>
        ) : null}
      </div>
      <div className="mb-3">
        <Sparkline closes={item.closes} positive={sparkPositive} />
      </div>

      {/* support/resistance bar */}
      <div className="mb-3">
        <div className="flex items-center justify-between text-[10px] text-gray-500 mb-1">
          <span>Support ₹{INR(item.support, 2)}</span>
          <span>Resistance ₹{INR(item.resistance, 2)}</span>
        </div>
        <div className="relative h-1.5 rounded-full bg-surface-3 overflow-hidden">
          <div
            className={`absolute inset-y-0 left-0 ${positive ? 'bg-brand-500/40' : 'bg-red-500/40'}`}
            style={{ width: `${Math.max(0, Math.min(100, pctInRange))}%` }}
          />
          <div
            className="absolute top-1/2 -translate-y-1/2 w-2 h-2 rounded-full bg-white shadow"
            style={{ left: `calc(${Math.max(0, Math.min(100, pctInRange))}% - 4px)` }}
          />
        </div>
      </div>

      {/* signals row */}
      <div className="flex flex-wrap gap-1.5 mb-3">
        <SignalDot on={item.breakout} label="Breakout" />
        <SignalDot on={item.near_breakout} label="Near Hi" />
        <SignalDot on={item.volume_surge} label="Vol Surge" />
        <SignalDot on={(item.up_days_5d || 0) >= 4} label={`${item.up_days_5d || 0}/4 up`} />
      </div>

      {/* trade plan */}
      <div className="grid grid-cols-3 gap-2 text-center bg-surface-2 border border-surface-3 rounded-lg p-2 mb-3">
        <div>
          <div className="text-[10px] uppercase tracking-wider text-gray-500">Entry</div>
          <div className="text-xs font-semibold text-white">₹{INR(item.entry, 2)}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-gray-500">Stop</div>
          <div className="text-xs font-semibold text-red-400">₹{INR(item.stop, 2)}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wider text-gray-500">Target</div>
          <div className="text-xs font-semibold text-green-400">₹{INR(item.target, 2)}</div>
        </div>
      </div>

      {/* footer metrics */}
      <div className="grid grid-cols-3 gap-2 text-[10px] mb-3">
        <div className="text-center">
          <div className="text-gray-500 uppercase tracking-wider">5D Ret</div>
          <div className={`font-semibold ${(item.return_5d_pct || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>
            {(item.return_5d_pct || 0) >= 0 ? '+' : ''}{(item.return_5d_pct || 0).toFixed(2)}%
          </div>
        </div>
        <div className="text-center">
          <div className="text-gray-500 uppercase tracking-wider">Volume</div>
          <div className="font-semibold text-white">{COMPACT_NUM(item.volume)}</div>
        </div>
        <div className="text-center">
          <div className="text-gray-500 uppercase tracking-wider">R:R</div>
          <div className="font-semibold text-white">{(item.risk_reward || 0).toFixed(2)}</div>
        </div>
      </div>

      {/* actions */}
      <div className="flex gap-2">
        <Link
          to={tradeHref}
          className="flex-1 inline-flex items-center justify-center gap-1 px-2 py-1.5 rounded-md
                     bg-brand-500/15 border border-brand-500/30 text-xs font-semibold text-brand-400
                     hover:bg-brand-500/25 transition-colors"
        >
          <ExternalLink className="w-3 h-3" />
          Trade
        </Link>
        <button
          type="button"
          onClick={() => onAddWatchlist(item)}
          className="inline-flex items-center justify-center gap-1 px-2 py-1.5 rounded-md
                     bg-surface-2 border border-surface-3 text-xs font-medium text-gray-400
                     hover:text-white hover:border-brand-500/30 transition-colors"
          title="Add to watchlist"
        >
          <Bookmark className="w-3 h-3" />
        </button>
      </div>
    </div>
  );
}

/* ── section block with per-section sort/filter ─ */
function Section({ def, items, sectors, onShowReasons, onAddWatchlist }) {
  const [sortBy, setSortBy] = useState('rr');
  const [sortDesc, setSortDesc] = useState(true);
  const [sectorFilter, setSectorFilter] = useState('all');
  const [maxPrice, setMaxPrice] = useState('');

  const filtered = useMemo(() => {
    const opt = SORT_OPTIONS.find((o) => o.value === sortBy) || SORT_OPTIONS[0];
    let rows = items.slice();
    if (sectorFilter !== 'all') {
      rows = rows.filter((x) => (x.sector || '').toLowerCase() === sectorFilter.toLowerCase());
    }
    const max = Number(maxPrice);
    if (max > 0) rows = rows.filter((x) => (x.last_price || 0) <= max);
    rows.sort((a, b) => (sortDesc ? opt.pick(b) - opt.pick(a) : opt.pick(a) - opt.pick(b)));
    return rows;
  }, [items, sortBy, sortDesc, sectorFilter, maxPrice]);

  const Icon = def.icon;
  return (
    <section id={def.id} className="mb-8 scroll-mt-24">
      <div className="flex items-end justify-between mb-3 flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <div className={`w-9 h-9 rounded-lg ${def.accent} flex items-center justify-center`}>
            <Icon className="w-4 h-4 text-white" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">{def.title}</h2>
            <p className="text-xs text-gray-500">{def.subtitle}</p>
          </div>
        </div>

        {items.length > 0 && (
          <div className="flex items-center gap-2 flex-wrap">
            <div className="inline-flex items-center gap-1 bg-surface-1 border border-surface-3 rounded-md text-xs">
              <ArrowUpDown className="w-3 h-3 text-gray-500 ml-2" />
              <select
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value)}
                className="bg-transparent text-white px-2 py-1 outline-none cursor-pointer"
              >
                {SORT_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value} className="bg-surface-1">{o.label}</option>
                ))}
              </select>
              <button
                onClick={() => setSortDesc((d) => !d)}
                className="px-2 py-1 text-gray-400 hover:text-white border-l border-surface-3"
                title={sortDesc ? 'Descending' : 'Ascending'}
              >
                {sortDesc ? '↓' : '↑'}
              </button>
            </div>

            <div className="inline-flex items-center gap-1 bg-surface-1 border border-surface-3 rounded-md text-xs">
              <Filter className="w-3 h-3 text-gray-500 ml-2" />
              <select
                value={sectorFilter}
                onChange={(e) => setSectorFilter(e.target.value)}
                className="bg-transparent text-white px-2 py-1 outline-none cursor-pointer max-w-[140px]"
              >
                <option value="all" className="bg-surface-1">All Sectors</option>
                {sectors.map((s) => (
                  <option key={s} value={s} className="bg-surface-1">{s}</option>
                ))}
              </select>
            </div>

            <input
              type="number"
              min="0"
              placeholder="Max ₹"
              value={maxPrice}
              onChange={(e) => setMaxPrice(e.target.value)}
              className="w-24 bg-surface-1 border border-surface-3 rounded-md px-2 py-1 text-xs text-white outline-none focus:border-brand-500/40"
            />

            <span className="text-xs text-gray-500">{filtered.length}/{items.length}</span>
          </div>
        )}
      </div>

      {filtered.length === 0 ? (
        <div className="bg-surface-1 border border-dashed border-surface-3 rounded-xl p-6 text-center">
          <p className="text-sm text-gray-500">
            {items.length === 0 ? def.empty : 'No ideas match the current filters.'}
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {filtered.map((it) => (
            <OpportunityCard
              key={`${def.id}-${it.symbol}`}
              item={it}
              onShowReasons={onShowReasons}
              onAddWatchlist={onAddWatchlist}
            />
          ))}
        </div>
      )}
    </section>
  );
}

/* ── sector heat strip ────────────────────────── */
function SectorHeatStrip({ rows }) {
  if (!Array.isArray(rows) || rows.length === 0) return null;
  const maxAbs = Math.max(
    0.1,
    ...rows.map((r) => Math.abs(r.avg_change_pct || 0)),
  );
  return (
    <div className="bg-surface-1 border border-surface-3 rounded-xl p-3 mb-6">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs uppercase tracking-wider text-gray-500 font-semibold">Sector Heat</h3>
        <span className="text-[10px] text-gray-500">avg day change · breadth %</span>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
        {rows.map((s) => {
          const positive = (s.avg_change_pct || 0) >= 0;
          const widthPct = Math.min(100, (Math.abs(s.avg_change_pct || 0) / maxAbs) * 100);
          return (
            <div key={s.sector} className="bg-surface-2 border border-surface-3 rounded-md p-2">
              <div className="flex items-center justify-between mb-1">
                <span className="text-[11px] font-medium text-white truncate">{s.sector}</span>
                <span className={`text-[11px] font-semibold ${positive ? 'text-green-400' : 'text-red-400'}`}>
                  {positive ? '+' : ''}{(s.avg_change_pct || 0).toFixed(2)}%
                </span>
              </div>
              <div className="relative h-1.5 rounded-full bg-surface-3 overflow-hidden mb-1">
                <div
                  className={`absolute inset-y-0 left-0 ${positive ? 'bg-green-500/60' : 'bg-red-500/60'}`}
                  style={{ width: `${widthPct}%` }}
                />
              </div>
              <div className="flex items-center justify-between text-[10px] text-gray-500">
                <span>{s.count} ideas</span>
                <span>{Math.round(s.breadth_pct || 0)}% green</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ── sticky section tabs ──────────────────────── */
function SectionTabs({ counts }) {
  const [active, setActive] = useState('ipos');

  useEffect(() => {
    const observers = SECTION_DEFS.map((def) => {
      const el = document.getElementById(def.id);
      if (!el) return null;
      const obs = new IntersectionObserver(
        ([entry]) => {
          if (entry.isIntersecting) setActive(def.id);
        },
        { rootMargin: '-30% 0px -60% 0px' }
      );
      obs.observe(el);
      return obs;
    }).filter(Boolean);
    return () => observers.forEach((o) => o.disconnect());
  }, []);

  return (
    <div className="sticky top-0 z-30 -mx-6 px-6 py-2 bg-surface-0/80 backdrop-blur border-b border-surface-3 mb-4">
      <div className="flex gap-2 overflow-x-auto">
        {SECTION_DEFS.map((def) => {
          const isActive = active === def.id;
          const Icon = def.icon;
          return (
            <a
              key={def.id}
              href={`#${def.id}`}
              onClick={() => setActive(def.id)}
              className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap transition-colors
                ${isActive
                  ? 'bg-brand-500/15 border border-brand-500/30 text-brand-400'
                  : 'bg-surface-1 border border-surface-3 text-gray-400 hover:text-white'}`}
            >
              <Icon className="w-3.5 h-3.5" />
              {def.label}
              <span className={`text-[10px] px-1.5 py-0.5 rounded-full
                ${isActive ? 'bg-brand-500/20' : 'bg-surface-3 text-gray-500'}`}
              >
                {counts[def.id] ?? 0}
              </span>
            </a>
          );
        })}
      </div>
    </div>
  );
}

/* ── main page ────────────────────────────────── */
export default function AnalyticsWorld() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const [reasonsItem, setReasonsItem] = useState(null);
  const { showToast } = useToast();
  const watchlistIdRef = useRef(null);

  const load = useCallback(async ({ silent = false } = {}) => {
    try {
      if (silent) setRefreshing(true);
      const res = await api.getAnalyticsWorld();
      setData(res || null);
      setError(null);
    } catch (e) {
      setError(e?.message || 'Failed to load analytics');
      if (!silent) showToast('Failed to load analytics', 'error');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [showToast]);

  useEffect(() => {
    load();
    const id = setInterval(() => load({ silent: true }), 60_000);
    return () => clearInterval(id);
  }, [load]);

  const sections = data?.sections || { ipos: [], swing: [], momentum: [], breakouts: [] };
  const summary = data?.summary || {};
  const sectorHeat = data?.sector_heat || [];
  const marketStatus = data?.market_status || {};

  const counts = useMemo(() => ({
    ipos: sections.ipos.length,
    swing: sections.swing.length,
    momentum: sections.momentum.length,
    breakouts: sections.breakouts.length,
  }), [sections]);

  const totalIdeas = counts.ipos + counts.swing + counts.momentum + counts.breakouts;

  const sectors = useMemo(() => {
    const set = new Set();
    Object.values(sections).flat().forEach((x) => { if (x?.sector) set.add(x.sector); });
    return Array.from(set).sort();
  }, [sections]);

  const handleAddWatchlist = useCallback(async (item) => {
    try {
      // Resolve a watchlist id once per session
      if (!watchlistIdRef.current) {
        const wls = await api.getPortfolioWatchlists();
        if (Array.isArray(wls) && wls.length > 0) {
          watchlistIdRef.current = wls[0].id;
        } else {
          const created = await api.createWatchlist('Analytics World');
          watchlistIdRef.current = created?.id;
        }
      }
      if (!watchlistIdRef.current) {
        showToast('Could not create watchlist', 'error');
        return;
      }
      await api.addWatchlistItem(watchlistIdRef.current, {
        tradingsymbol: item.symbol,
        exchange: item.exchange || 'NSE',
      });
      showToast(`${item.symbol} added to watchlist`, 'success');
    } catch (e) {
      showToast(e?.message || 'Add to watchlist failed', 'error');
    }
  }, [showToast]);

  if (loading) {
    return (
      <div className="p-6">
        <div className="animate-pulse">
          <div className="h-8 w-64 bg-surface-3 rounded mb-4" />
          <div className="grid grid-cols-1 md:grid-cols-3 xl:grid-cols-6 gap-3 mb-8">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="h-16 bg-surface-1 border border-surface-3 rounded-lg" />
            ))}
          </div>
          {Array.from({ length: 2 }).map((_, i) => (
            <div key={i} className="mb-8">
              <div className="h-6 w-48 bg-surface-3 rounded mb-3" />
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                {Array.from({ length: 3 }).map((__, j) => (
                  <div key={j} className="h-72 bg-surface-1 border border-surface-3 rounded-xl" />
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-[1600px] mx-auto">
      {/* ── header ── */}
      <header className="mb-4">
        <Link
          to="/portfolio"
          className="inline-flex items-center gap-1.5 text-xs text-gray-500 hover:text-white mb-3 transition-colors"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          Back to Portfolio
        </Link>
        <div className="flex items-start justify-between flex-wrap gap-3 mb-4">
          <div>
            <h1 className="text-2xl font-bold text-white flex items-center gap-2">
              <BarChart3 className="w-6 h-6 text-brand-400" />
              Analytics World
            </h1>
            <p className="text-sm text-gray-500 mt-1">
              Live-quote driven screener · curated NSE universe · refreshed every 60s
            </p>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            {marketStatus.status && (
              <MarketStatusPill status={marketStatus.status} serverTime={marketStatus.server_time} />
            )}
            {data?.as_of && (
              <span className="text-xs text-gray-500">As of {data.as_of}</span>
            )}
            <button
              onClick={() => load({ silent: true })}
              disabled={refreshing}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg
                         bg-surface-1 border border-surface-3 text-sm text-white
                         hover:border-brand-500/40 disabled:opacity-50 transition-colors"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </div>
        </div>

        {/* summary pills */}
        <div className="flex flex-wrap gap-2 mb-4">
          <StatPill icon={Activity} label="Live" value={summary.live_count ?? '—'} />
          <StatPill icon={Rocket}   label="IPOs" value={summary.ipos ?? 0} />
          <StatPill icon={ShieldCheck} label="Swing" value={summary.swing ?? 0} />
          <StatPill icon={Flame}    label="Momentum" value={summary.momentum ?? 0} accent="text-orange-400" />
          <StatPill icon={Crosshair} label="Breakouts" value={summary.breakouts ?? 0} accent="text-brand-400" />
          <StatPill icon={IndianRupee} label="Total Ideas" value={totalIdeas} />
        </div>

        {error && (
          <div className="mb-3 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400">
            {error}
          </div>
        )}
      </header>

      <SectorHeatStrip rows={sectorHeat} />
      <SectionTabs counts={counts} />

      {/* ── sections ── */}
      {SECTION_DEFS.map((def) => (
        <Section
          key={def.id}
          def={def}
          items={sections[def.id] || []}
          sectors={sectors}
          onShowReasons={setReasonsItem}
          onAddWatchlist={handleAddWatchlist}
        />
      ))}

      <footer className="mt-10 text-center">
        <p className="text-[11px] text-gray-500">
          Data sourced from your Zerodha Kite quotes + daily historicals. Verdicts are
          rule-based heuristics, not financial advice. Always validate with your own analysis.
        </p>
      </footer>

      {reasonsItem && (
        <ReasonsPopover item={reasonsItem} onClose={() => setReasonsItem(null)} />
      )}
    </div>
  );
}
