/**
 * Analytics World — real-data swing/positional screener.
 *
 * Strictly read-only. Pulls a single payload from `/portfolio/analytics-world`
 * containing four sections sourced from live Kite quotes and cached
 * 20-day historical metrics:
 *
 *   1. Undervalued / recent IPOs        (ipos)
 *   2. Swing trade candidates           (swing)
 *   3. Momentum & continuous up-days    (momentum)
 *   4. Breakout setups                  (breakouts)
 *
 * UI is themed with the system surface/brand tokens so it renders
 * correctly in both dark and light themes.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import { useToast } from '../ToastContext';
import {
  TrendingUp, TrendingDown, RefreshCw, Activity, Rocket, Flame,
  Crosshair, BarChart3, ShieldCheck, ChevronRight, IndianRupee,
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

function VerdictBadge({ verdict }) {
  const m = VERDICT_META[verdict] || VERDICT_META.Watch;
  return (
    <span className={`text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded-full border ${m.bg} ${m.text} ${m.ring}`}>
      {verdict}
    </span>
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

/* ── opportunity card ─────────────────────────── */
function OpportunityCard({ item }) {
  if (!item) return null;
  const positive = (item.change_pct || 0) >= 0;

  // proximity to resistance / support (0..1)
  const range = Math.max(0.0001, (item.resistance || 0) - (item.support || 0));
  const px = Math.max(item.support || 0, Math.min(item.resistance || 0, item.last_price || 0));
  const pctInRange = ((px - (item.support || 0)) / range) * 100;

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
        <VerdictBadge verdict={item.verdict} />
      </div>

      {/* price */}
      <div className="flex items-baseline justify-between mb-3">
        <div className="flex items-baseline gap-2">
          <span className="text-xl font-bold text-white">₹{INR(item.last_price, 2)}</span>
          <ChangeChip pct={item.change_pct} />
        </div>
        {item.market_cap_cr ? (
          <span className="text-[11px] text-gray-500">M-Cap {COMPACT_INR(item.market_cap_cr * 1e7)}</span>
        ) : null}
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
      <div className="grid grid-cols-3 gap-2 text-[10px]">
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
    </div>
  );
}

/* ── section block ────────────────────────────── */
function Section({ id, title, subtitle, icon: Icon, accent, items, emptyHint }) {
  return (
    <section id={id} className="mb-8">
      <div className="flex items-end justify-between mb-3">
        <div className="flex items-center gap-3">
          <div className={`w-9 h-9 rounded-lg ${accent} flex items-center justify-center`}>
            <Icon className="w-4 h-4 text-white" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">{title}</h2>
            <p className="text-xs text-gray-500">{subtitle}</p>
          </div>
        </div>
        <span className="text-xs text-gray-500">{items.length} ideas</span>
      </div>

      {items.length === 0 ? (
        <div className="bg-surface-1 border border-dashed border-surface-3 rounded-xl p-6 text-center">
          <p className="text-sm text-gray-500">{emptyHint}</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {items.map((it) => (
            <OpportunityCard key={`${id}-${it.symbol}`} item={it} />
          ))}
        </div>
      )}
    </section>
  );
}

/* ── main page ────────────────────────────────── */
export default function AnalyticsWorld() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const { showToast } = useToast();

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

  const totalIdeas = useMemo(
    () => sections.ipos.length + sections.swing.length + sections.momentum.length + sections.breakouts.length,
    [sections]
  );

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
                  <div key={j} className="h-64 bg-surface-1 border border-surface-3 rounded-xl" />
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
      <header className="mb-6">
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
          <div className="flex items-center gap-2">
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
        <div className="flex flex-wrap gap-2">
          <StatPill icon={Activity} label="Live" value={summary.live_count ?? '—'} />
          <StatPill icon={Rocket}   label="IPOs" value={summary.ipos ?? 0} />
          <StatPill icon={ShieldCheck} label="Swing" value={summary.swing ?? 0} />
          <StatPill icon={Flame}    label="Momentum" value={summary.momentum ?? 0} accent="text-orange-400" />
          <StatPill icon={Crosshair} label="Breakouts" value={summary.breakouts ?? 0} accent="text-brand-400" />
          <StatPill icon={IndianRupee} label="Total Ideas" value={totalIdeas} />
        </div>

        {error && (
          <div className="mt-3 p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-sm text-red-400">
            {error}
          </div>
        )}
      </header>

      {/* ── sections ── */}
      <Section
        id="ipos"
        title="Undervalued / Recent IPOs"
        subtitle="Recently listed, affordable price points — entry only when structure aligns"
        icon={Rocket}
        accent="bg-blue-500/80"
        items={sections.ipos}
        emptyHint="No IPO candidates passing the affordability filter right now."
      />

      <Section
        id="swing"
        title="Swing Trade Candidates"
        subtitle="Mid-priced names with constructive 5-day structure"
        icon={ShieldCheck}
        accent="bg-emerald-500/80"
        items={sections.swing}
        emptyHint="No swing setups matching the criteria today."
      />

      <Section
        id="momentum"
        title="Momentum & Continuous Up-Days"
        subtitle="≥4 of last 5 sessions green or ≥5% week return — riding strength"
        icon={Flame}
        accent="bg-orange-500/80"
        items={sections.momentum}
        emptyHint="No high-momentum names firing today."
      />

      <Section
        id="breakouts"
        title="Resistance Breakouts"
        subtitle="Price at or near 20-day high with volume confirmation"
        icon={Crosshair}
        accent="bg-brand-500"
        items={sections.breakouts}
        emptyHint="No fresh breakouts on the radar."
      />

      <footer className="mt-10 text-center">
        <p className="text-[11px] text-gray-500">
          Data sourced from your Zerodha Kite quotes + daily historicals. Verdicts are
          rule-based heuristics, not financial advice. Always validate with your own analysis.
        </p>
      </footer>
    </div>
  );
}
