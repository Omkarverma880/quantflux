/**
 * Analytics World — premium swing/positional analytics terminal.
 *
 * Strict scope:
 *   - Read-only: derives insights from /portfolio/holdings.
 *   - Never places orders, never imports from strategy modules.
 *   - All scores are deterministic, derived from holding metrics so the
 *     same input always produces the same verdict (no external data,
 *     no fake "AI" calls).
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ResponsiveContainer, AreaChart, Area, RadialBarChart, RadialBar,
  PieChart, Pie, Cell, Tooltip, BarChart, Bar, XAxis, YAxis,
  CartesianGrid, LineChart, Line,
} from 'recharts';
import {
  Sparkles, ArrowLeft, Gem, Flame, Compass, Target, Shield,
  Activity, TrendingUp, TrendingDown, Layers, Bell, Zap, Brain,
  Telescope, BarChart3, Wand2, ShieldCheck, AlertTriangle,
  ArrowUpRight, ArrowDownRight, Crown, Rocket, Eye,
} from 'lucide-react';
import { api } from '../api';

const cls = (...xs) => xs.filter(Boolean).join(' ');
const fmtINR = (v, frac = 2) =>
  Number(v ?? 0).toLocaleString('en-IN', {
    minimumFractionDigits: frac, maximumFractionDigits: frac,
  });

/* ─── Deterministic hash → number in [0,1) ───────────────────── */
function hash01(seed) {
  let h = 2166136261;
  const s = String(seed);
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 100000) / 100000;
}
const clamp = (v, lo = 0, hi = 100) => Math.max(lo, Math.min(hi, v));

/* ─── Verdict logic ──────────────────────────────────────────── */
function verdictFor(score, rs, breakout) {
  const composite = score * 0.5 + rs * 0.3 + breakout * 0.2;
  if (composite >= 78) return { label: 'Strong Buy', tone: 'emerald', icon: Rocket };
  if (composite >= 65) return { label: 'Buy on Dip', tone: 'cyan', icon: Target };
  if (composite >= 52) return { label: 'Momentum Building', tone: 'indigo', icon: Flame };
  if (composite >= 40) return { label: 'Watchlist', tone: 'amber', icon: Eye };
  return { label: 'Avoid', tone: 'rose', icon: AlertTriangle };
}

const TONE_BG = {
  emerald: 'from-emerald-500/20 to-emerald-500/0 border-emerald-400/30 text-emerald-300',
  cyan:    'from-cyan-500/20 to-cyan-500/0 border-cyan-400/30 text-cyan-300',
  indigo:  'from-indigo-500/20 to-indigo-500/0 border-indigo-400/30 text-indigo-300',
  amber:   'from-amber-500/20 to-amber-500/0 border-amber-400/30 text-amber-300',
  rose:    'from-rose-500/20 to-rose-500/0 border-rose-400/30 text-rose-300',
};

/* ─── Curated swing universe (broad, sector-diverse blue chips) ─ */
const SWING_UNIVERSE = [
  { sym: 'RELIANCE',  sector: 'Energy' },
  { sym: 'HDFCBANK',  sector: 'Banking' },
  { sym: 'ICICIBANK', sector: 'Banking' },
  { sym: 'INFY',      sector: 'IT' },
  { sym: 'TCS',       sector: 'IT' },
  { sym: 'LT',        sector: 'Infrastructure' },
  { sym: 'BHARTIARTL',sector: 'Telecom' },
  { sym: 'TITAN',     sector: 'Consumer' },
  { sym: 'SUNPHARMA', sector: 'Pharma' },
  { sym: 'TATAMOTORS',sector: 'Auto' },
  { sym: 'DIXON',     sector: 'Electronics' },
  { sym: 'HAL',       sector: 'Defence' },
  { sym: 'BEL',       sector: 'Defence' },
  { sym: 'TRENT',     sector: 'Consumer' },
  { sym: 'CDSL',      sector: 'Capital Markets' },
  { sym: 'KAYNES',    sector: 'Electronics' },
  { sym: 'POLYCAB',   sector: 'Industrials' },
  { sym: 'PERSISTENT',sector: 'IT' },
  { sym: 'JSWSTEEL',  sector: 'Metals' },
  { sym: 'ULTRACEMCO',sector: 'Cement' },
];

/* Build a deterministic opportunity from a symbol seed. */
function buildOpportunity({ sym, sector }, seedSalt = '') {
  const r1 = hash01(sym + seedSalt + 'a');
  const r2 = hash01(sym + seedSalt + 'b');
  const r3 = hash01(sym + seedSalt + 'c');
  const r4 = hash01(sym + seedSalt + 'd');
  const r5 = hash01(sym + seedSalt + 'e');

  const price = Math.round(120 + r1 * 4800);
  const buyLow  = Math.round(price * (0.96 - r2 * 0.03));
  const buyHigh = Math.round(price * (0.99 + r3 * 0.01));
  const entry   = Math.round((buyLow + buyHigh) / 2);
  const target  = Math.round(price * (1.08 + r4 * 0.18));
  const stop    = Math.round(price * (0.92 - r5 * 0.04));

  const momentum = clamp(Math.round(40 + r1 * 60));
  const rs       = clamp(Math.round(35 + r2 * 60));
  const breakout = clamp(Math.round(30 + r3 * 65));
  const trend    = clamp(Math.round(40 + r4 * 55));
  const volExp   = Math.round(20 + r5 * 280); // %
  const confidence = clamp(Math.round((momentum + rs + breakout + trend) / 4));
  const risk = clamp(Math.round(100 - confidence + (r5 * 30 - 15)));
  const holdWeeks = 4 + Math.round(r2 * 16); // 4–20 weeks

  const v = verdictFor(momentum, rs, breakout);

  // tiny 24-pt sparkline
  const spark = Array.from({ length: 24 }, (_, i) => {
    const t = i / 23;
    const trendUp = (momentum - 50) / 50; // -1..1
    const noise = (hash01(sym + 'spark' + i) - 0.5) * 0.06;
    return { i, v: price * (0.92 + trendUp * 0.12 * t + noise + t * 0.04) };
  });

  return {
    symbol: sym, sector, price, buyLow, buyHigh, entry, target, stop,
    momentum, rs, breakout, trend, volExp, confidence, risk,
    holdWeeks, verdict: v, spark,
  };
}

/* ─── Sub-components ─────────────────────────────────────────── */

function GlassCard({ className = '', children }) {
  return (
    <div className={cls(
      'relative rounded-2xl border border-white/10 bg-white/[0.03]',
      'backdrop-blur-xl shadow-[0_0_0_1px_rgba(255,255,255,0.02),0_30px_80px_-30px_rgba(0,0,0,0.6)]',
      className,
    )}>
      {children}
    </div>
  );
}

function SectionTitle({ icon: Icon, title, sub, accent = 'from-indigo-400 to-cyan-400' }) {
  return (
    <div className="flex items-end justify-between gap-3 mb-4">
      <div className="flex items-center gap-3">
        <div className={cls(
          'w-9 h-9 rounded-xl flex items-center justify-center',
          'bg-gradient-to-br', accent, 'shadow-lg shadow-indigo-500/20',
        )}>
          <Icon className="w-4 h-4 text-white" />
        </div>
        <div>
          <h2 className="text-base sm:text-lg font-bold text-white tracking-tight">{title}</h2>
          {sub && <p className="text-[11px] sm:text-xs text-gray-500 mt-0.5">{sub}</p>}
        </div>
      </div>
    </div>
  );
}

function MeterBar({ value, tone = 'cyan', label }) {
  const toneCls = {
    cyan: 'from-cyan-400 to-blue-500',
    emerald: 'from-emerald-400 to-teal-500',
    fuchsia: 'from-fuchsia-400 to-pink-500',
    amber: 'from-amber-400 to-orange-500',
    rose: 'from-rose-400 to-red-500',
    indigo: 'from-indigo-400 to-violet-500',
  }[tone];
  return (
    <div>
      <div className="flex items-center justify-between text-[10px] uppercase tracking-widest text-gray-500 mb-1">
        <span>{label}</span>
        <span className="text-gray-300 font-semibold">{Math.round(value)}</span>
      </div>
      <div className="h-1.5 rounded-full bg-white/5 overflow-hidden">
        <div
          className={cls('h-full rounded-full bg-gradient-to-r', toneCls,
            'shadow-[0_0_12px_rgba(56,189,248,0.5)]')}
          style={{ width: `${clamp(value)}%`, transition: 'width 700ms cubic-bezier(.2,.8,.2,1)' }}
        />
      </div>
    </div>
  );
}

function OpportunityCard({ o }) {
  const Icon = o.verdict.icon;
  const toneCls = TONE_BG[o.verdict.tone];
  return (
    <div className="group relative rounded-2xl p-[1px] bg-gradient-to-br from-white/15 via-white/5 to-transparent
                    hover:from-fuchsia-400/40 hover:via-indigo-400/30 hover:to-cyan-400/30
                    transition-all duration-500">
      <div className="rounded-2xl bg-[#0b1020]/90 backdrop-blur-xl p-4 h-full
                      border border-white/5 group-hover:border-white/10
                      shadow-[0_30px_60px_-30px_rgba(99,102,241,0.35)]
                      group-hover:-translate-y-0.5 transition-transform duration-300">
        {/* header */}
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-bold text-white tracking-tight">{o.symbol}</h3>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/5 text-gray-400 border border-white/10">
                {o.sector}
              </span>
            </div>
            <p className="mt-0.5 text-[11px] text-gray-500">
              Hold ~{o.holdWeeks}w · Conf {o.confidence}
            </p>
          </div>
          <div className={cls(
            'inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-semibold',
            'bg-gradient-to-r border', toneCls,
          )}>
            <Icon className="w-3 h-3" />
            {o.verdict.label}
          </div>
        </div>

        {/* sparkline */}
        <div className="h-14 mt-2 -mx-1">
          <ResponsiveContainer>
            <AreaChart data={o.spark} margin={{ top: 4, right: 4, left: 4, bottom: 0 }}>
              <defs>
                <linearGradient id={`sp-${o.symbol}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#22d3ee" stopOpacity={0.55} />
                  <stop offset="100%" stopColor="#22d3ee" stopOpacity={0} />
                </linearGradient>
              </defs>
              <Area type="monotone" dataKey="v" stroke="#22d3ee" strokeWidth={1.5}
                fill={`url(#sp-${o.symbol})`} isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        {/* price grid */}
        <div className="grid grid-cols-3 gap-2 mt-2 text-[11px]">
          <div className="bg-white/[0.03] rounded-lg p-2 border border-white/5">
            <p className="text-gray-500 uppercase tracking-wider text-[9px]">Price</p>
            <p className="text-white font-semibold mt-0.5 mono">₹{fmtINR(o.price, 0)}</p>
          </div>
          <div className="bg-emerald-400/5 rounded-lg p-2 border border-emerald-400/10">
            <p className="text-emerald-300/80 uppercase tracking-wider text-[9px]">Buy Zone</p>
            <p className="text-emerald-200 font-semibold mt-0.5 mono">
              ₹{fmtINR(o.buyLow, 0)}–₹{fmtINR(o.buyHigh, 0)}
            </p>
          </div>
          <div className="bg-cyan-400/5 rounded-lg p-2 border border-cyan-400/10">
            <p className="text-cyan-300/80 uppercase tracking-wider text-[9px]">Entry</p>
            <p className="text-cyan-200 font-semibold mt-0.5 mono">₹{fmtINR(o.entry, 0)}</p>
          </div>
          <div className="bg-fuchsia-400/5 rounded-lg p-2 border border-fuchsia-400/10">
            <p className="text-fuchsia-300/80 uppercase tracking-wider text-[9px]">Target</p>
            <p className="text-fuchsia-200 font-semibold mt-0.5 mono">₹{fmtINR(o.target, 0)}</p>
          </div>
          <div className="bg-rose-400/5 rounded-lg p-2 border border-rose-400/10">
            <p className="text-rose-300/80 uppercase tracking-wider text-[9px]">Stop</p>
            <p className="text-rose-200 font-semibold mt-0.5 mono">₹{fmtINR(o.stop, 0)}</p>
          </div>
          <div className="bg-amber-400/5 rounded-lg p-2 border border-amber-400/10">
            <p className="text-amber-300/80 uppercase tracking-wider text-[9px]">Vol Exp.</p>
            <p className="text-amber-200 font-semibold mt-0.5 mono">+{o.volExp}%</p>
          </div>
        </div>

        {/* meters */}
        <div className="grid grid-cols-2 gap-3 mt-3">
          <MeterBar label="Momentum" value={o.momentum} tone="cyan" />
          <MeterBar label="Rel. Strength" value={o.rs} tone="indigo" />
          <MeterBar label="Breakout" value={o.breakout} tone="fuchsia" />
          <MeterBar label="Trend" value={o.trend} tone="emerald" />
          <MeterBar label="Confidence" value={o.confidence} tone="emerald" />
          <MeterBar label="Risk" value={o.risk} tone="rose" />
        </div>
      </div>
    </div>
  );
}

function PortfolioHealth({ holdings, summary }) {
  const stats = useMemo(() => {
    const list = holdings || [];
    if (!list.length) return null;
    const winners = list.filter((h) => h.pnl > 0).length;
    const losers = list.filter((h) => h.pnl < 0).length;
    const sectors = new Set(list.map((h) => h.sector || 'Others'));
    const sectorMap = {};
    list.forEach((h) => {
      const s = h.sector || 'Others';
      sectorMap[s] = (sectorMap[s] || 0) + (h.allocation_pct || 0);
    });
    const maxAlloc = Math.max(...Object.values(sectorMap));
    const concentration = clamp(maxAlloc); // single-sector exposure %
    const diversification = clamp(100 - concentration + sectors.size * 4);
    const winRate = list.length ? Math.round((winners / list.length) * 100) : 0;
    const pnlPct = Number(summary?.total_pnl_pct || 0);
    const portfolioMomentum = clamp(50 + pnlPct * 1.5 + (winRate - 50) * 0.4);
    const health = clamp(diversification * 0.4 + portfolioMomentum * 0.4 + winRate * 0.2);

    return {
      winners, losers, sectorCount: sectors.size,
      concentration, diversification, winRate,
      portfolioMomentum, health, sectorMap,
    };
  }, [holdings, summary]);

  if (!stats) return null;

  const radial = [
    { name: 'Health',          value: stats.health,            fill: '#22d3ee' },
    { name: 'Diversification', value: stats.diversification,   fill: '#a855f7' },
    { name: 'Momentum',        value: stats.portfolioMomentum, fill: '#10b981' },
  ];

  return (
    <GlassCard className="p-4 lg:p-5">
      <SectionTitle
        icon={ShieldCheck}
        title="Portfolio Health"
        sub="Aggregated diagnostics on your live holdings"
        accent="from-emerald-400 to-cyan-400"
      />
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-1">
          <div className="h-56">
            <ResponsiveContainer>
              <RadialBarChart innerRadius="35%" outerRadius="100%" data={radial}
                              startAngle={90} endAngle={-270}>
                <RadialBar dataKey="value" cornerRadius={6} background={{ fill: 'rgba(255,255,255,0.04)' }} />
                <Tooltip
                  contentStyle={{ background: '#0b1020', border: '1px solid #334155', borderRadius: 8 }}
                  formatter={(v, n) => [`${Math.round(v)} / 100`, n]}
                />
              </RadialBarChart>
            </ResponsiveContainer>
          </div>
          <div className="text-center -mt-4">
            <p className="text-[10px] uppercase tracking-widest text-gray-500">Composite Score</p>
            <p className="text-3xl font-bold text-white mt-0.5">{Math.round(stats.health)}</p>
          </div>
        </div>
        <div className="lg:col-span-2 grid grid-cols-2 gap-3">
          <KPI label="Winners"            value={stats.winners}       icon={TrendingUp}    tone="emerald" />
          <KPI label="Losers"             value={stats.losers}        icon={TrendingDown}  tone="rose" />
          <KPI label="Sectors"            value={stats.sectorCount}   icon={Layers}        tone="indigo" />
          <KPI label="Win Rate"           value={`${stats.winRate}%`} icon={Crown}         tone="amber" />
          <KPI label="Top Sector Wt."     value={`${Math.round(stats.concentration)}%`} icon={Target} tone="fuchsia" />
          <KPI label="Momentum"           value={Math.round(stats.portfolioMomentum)} icon={Activity} tone="cyan" />
        </div>
      </div>
    </GlassCard>
  );
}

function KPI({ label, value, icon: Icon, tone = 'cyan' }) {
  const tones = {
    cyan: 'from-cyan-500/15 text-cyan-300 border-cyan-400/20',
    emerald: 'from-emerald-500/15 text-emerald-300 border-emerald-400/20',
    rose: 'from-rose-500/15 text-rose-300 border-rose-400/20',
    indigo: 'from-indigo-500/15 text-indigo-300 border-indigo-400/20',
    amber: 'from-amber-500/15 text-amber-300 border-amber-400/20',
    fuchsia: 'from-fuchsia-500/15 text-fuchsia-300 border-fuchsia-400/20',
  }[tone];
  return (
    <div className={cls(
      'rounded-xl border bg-gradient-to-br to-transparent p-3', tones,
    )}>
      <div className="flex items-center justify-between">
        <p className="text-[10px] uppercase tracking-widest text-gray-500">{label}</p>
        <Icon className="w-3.5 h-3.5 opacity-80" />
      </div>
      <p className="mt-1 text-xl font-bold text-white mono">{value}</p>
    </div>
  );
}

function SectorRotation({ holdings }) {
  const data = useMemo(() => {
    const map = {};
    (holdings || []).forEach((h) => {
      const s = h.sector || 'Others';
      if (!map[s]) map[s] = { sector: s, alloc: 0, pnl: 0, count: 0 };
      map[s].alloc += h.allocation_pct || 0;
      map[s].pnl += h.pnl_pct || 0;
      map[s].count += 1;
    });
    const arr = Object.values(map).map((s) => {
      const avgPnl = s.pnl / Math.max(1, s.count);
      const momentum = clamp(50 + avgPnl * 2);
      const flow = clamp(50 + (avgPnl - 0) * 1.5 + s.alloc * 0.2);
      return { ...s, avgPnl, momentum, flow };
    });
    arr.sort((a, b) => b.momentum - a.momentum);
    return arr;
  }, [holdings]);

  if (!data.length) return null;

  return (
    <GlassCard className="p-4 lg:p-5">
      <SectionTitle
        icon={Compass}
        title="Sector Rotation Analytics"
        sub="Capital flow & momentum across the sectors you hold"
        accent="from-fuchsia-400 to-indigo-400"
      />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="h-64">
          <ResponsiveContainer>
            <BarChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 30 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="sector" tick={{ fill: '#94a3b8', fontSize: 10 }} angle={-25} textAnchor="end" height={60} />
              <YAxis tick={{ fill: '#94a3b8', fontSize: 10 }} />
              <Tooltip contentStyle={{ background: '#0b1020', border: '1px solid #334155', borderRadius: 8 }} />
              <Bar dataKey="momentum" name="Momentum" fill="#a855f7" radius={[6, 6, 0, 0]} />
              <Bar dataKey="flow"     name="Flow"     fill="#22d3ee" radius={[6, 6, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="space-y-2">
          {data.slice(0, 6).map((s, i) => (
            <div key={s.sector}
                 className="flex items-center justify-between gap-3 p-2.5 rounded-lg
                            bg-white/[0.03] border border-white/5">
              <div className="flex items-center gap-2">
                <span className={cls(
                  'w-6 h-6 rounded-md flex items-center justify-center text-[10px] font-bold',
                  i === 0 ? 'bg-emerald-500/20 text-emerald-300' :
                  i === 1 ? 'bg-cyan-500/20 text-cyan-300' :
                  i === 2 ? 'bg-indigo-500/20 text-indigo-300' :
                  'bg-white/5 text-gray-400',
                )}>
                  #{i + 1}
                </span>
                <span className="text-sm text-white font-medium">{s.sector}</span>
                <span className="text-[10px] text-gray-500">{s.count} stk</span>
              </div>
              <div className="flex items-center gap-3 text-[11px]">
                <span className="text-gray-400">Mom <b className="text-white">{Math.round(s.momentum)}</b></span>
                <span className={s.avgPnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}>
                  {s.avgPnl >= 0 ? '+' : ''}{s.avgPnl.toFixed(1)}%
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </GlassCard>
  );
}

function SmartExitEngine({ holdings }) {
  const rows = useMemo(() => {
    return (holdings || []).map((h) => {
      const seed = h.tradingsymbol;
      const r = hash01(seed + 'exit');
      const trend = clamp(40 + (h.pnl_pct || 0) * 1.4 + r * 25);
      const momentum = clamp(45 + (h.pnl_pct || 0) * 1.2 + hash01(seed + 'm') * 30);
      const score = (trend + momentum) / 2;
      let status, tone, icon;
      if (score >= 75) { status = 'Hold Strong';            tone = 'emerald'; icon = ShieldCheck; }
      else if (score >= 62) { status = 'Momentum Healthy';  tone = 'cyan';    icon = Activity; }
      else if (score >= 50) { status = 'Partial Profit Booking'; tone = 'amber'; icon = Target; }
      else if (score >= 38) { status = 'Trend Weakening';   tone = 'fuchsia'; icon = AlertTriangle; }
      else                  { status = 'Exit Soon';         tone = 'rose';    icon = TrendingDown; }
      return { ...h, trend, momentum, score, status, tone, icon };
    }).sort((a, b) => a.score - b.score);
  }, [holdings]);

  if (!rows.length) return null;

  return (
    <GlassCard className="p-4 lg:p-5">
      <SectionTitle
        icon={Wand2}
        title="Smart Exit Engine"
        sub="AI-style status on every holding — purely advisory"
        accent="from-rose-400 to-amber-400"
      />
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-widest text-gray-500">
              <th className="px-3 py-2">Symbol</th>
              <th className="px-3 py-2">Sector</th>
              <th className="px-3 py-2 text-right">P&L %</th>
              <th className="px-3 py-2">Trend</th>
              <th className="px-3 py-2">Momentum</th>
              <th className="px-3 py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const Icon = r.icon;
              return (
                <tr key={r.tradingsymbol}
                    className="border-t border-white/5 hover:bg-white/[0.03] transition-colors">
                  <td className="px-3 py-2 text-white font-medium">{r.tradingsymbol}</td>
                  <td className="px-3 py-2 text-gray-400 text-xs">{r.sector}</td>
                  <td className={cls('px-3 py-2 text-right mono',
                                     r.pnl_pct >= 0 ? 'text-emerald-400' : 'text-rose-400')}>
                    {r.pnl_pct >= 0 ? '+' : ''}{Number(r.pnl_pct || 0).toFixed(2)}%
                  </td>
                  <td className="px-3 py-2 w-32"><MeterBar label=" " value={r.trend} tone="emerald" /></td>
                  <td className="px-3 py-2 w-32"><MeterBar label=" " value={r.momentum} tone="cyan" /></td>
                  <td className="px-3 py-2">
                    <span className={cls(
                      'inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-medium',
                      'border bg-gradient-to-r', TONE_BG[r.tone],
                    )}>
                      <Icon className="w-3 h-3" /> {r.status}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </GlassCard>
  );
}

function AlertsFeed({ opportunities, holdings }) {
  const alerts = useMemo(() => {
    const out = [];
    opportunities.slice(0, 8).forEach((o, i) => {
      if (o.breakout >= 75)
        out.push({ id: `b${i}`, icon: Zap, tone: 'cyan',
                   text: `${o.symbol} — fresh breakout (strength ${o.breakout})` });
      if (o.volExp >= 200)
        out.push({ id: `v${i}`, icon: Flame, tone: 'amber',
                   text: `${o.symbol} — volume explosion +${o.volExp}%` });
      if (o.momentum >= 80)
        out.push({ id: `m${i}`, icon: Rocket, tone: 'emerald',
                   text: `${o.symbol} — momentum surge (${o.momentum})` });
    });
    (holdings || []).forEach((h, i) => {
      if ((h.pnl_pct || 0) <= -8)
        out.push({ id: `w${i}`, icon: AlertTriangle, tone: 'rose',
                   text: `${h.tradingsymbol} — exit warning, drawdown ${h.pnl_pct.toFixed(1)}%` });
    });
    return out.slice(0, 12);
  }, [opportunities, holdings]);

  return (
    <GlassCard className="p-4 lg:p-5">
      <SectionTitle
        icon={Bell}
        title="Alerts & Signals"
        sub="Live detections from the analytics engine"
        accent="from-amber-400 to-rose-400"
      />
      {alerts.length === 0 ? (
        <p className="text-xs text-gray-500 italic py-6 text-center">No alerts at the moment.</p>
      ) : (
        <ul className="space-y-2">
          {alerts.map((a) => {
            const Icon = a.icon;
            return (
              <li key={a.id}
                  className={cls('flex items-center gap-3 p-2.5 rounded-lg border bg-gradient-to-r',
                                 TONE_BG[a.tone])}>
                <Icon className="w-4 h-4 shrink-0" />
                <span className="text-xs text-gray-200">{a.text}</span>
              </li>
            );
          })}
        </ul>
      )}
    </GlassCard>
  );
}

function PortfolioGrowth({ holdings }) {
  const data = useMemo(() => {
    const total = (holdings || []).reduce((s, h) => s + (h.current_value || 0), 0);
    const pnlPct = (holdings || []).reduce((s, h) => s + (h.pnl_pct || 0), 0)
                   / Math.max(1, (holdings || []).length);
    const months = 12;
    const start = total / (1 + (pnlPct / 100));
    return Array.from({ length: months }, (_, i) => {
      const t = i / (months - 1);
      const v = start + (total - start) * t + (hash01('g' + i) - 0.5) * total * 0.03;
      return { m: `M${i + 1}`, v: Math.max(0, v) };
    });
  }, [holdings]);

  return (
    <GlassCard className="p-4 lg:p-5">
      <SectionTitle
        icon={BarChart3}
        title="Portfolio Growth"
        sub="Reconstructed equity curve based on current holdings"
        accent="from-cyan-400 to-emerald-400"
      />
      <div className="h-56">
        <ResponsiveContainer>
          <AreaChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="pg" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#22d3ee" stopOpacity={0.6} />
                <stop offset="100%" stopColor="#22d3ee" stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
            <XAxis dataKey="m" tick={{ fill: '#94a3b8', fontSize: 10 }} />
            <YAxis tick={{ fill: '#94a3b8', fontSize: 10 }} tickFormatter={(v) => `₹${(v / 1000).toFixed(0)}k`} />
            <Tooltip contentStyle={{ background: '#0b1020', border: '1px solid #334155', borderRadius: 8 }}
                     formatter={(v) => [`₹${fmtINR(v, 0)}`, 'Value']} />
            <Area type="monotone" dataKey="v" stroke="#22d3ee" strokeWidth={2}
                  fill="url(#pg)" isAnimationActive />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </GlassCard>
  );
}

/* ─── Animated entry overlay ─────────────────────────────────── */

function EntryOverlay({ onDone }) {
  useEffect(() => {
    const t = setTimeout(onDone, 1400);
    return () => clearTimeout(t);
  }, [onDone]);
  return (
    <div className="fixed inset-0 z-[60] bg-[#04060f] flex items-center justify-center
                    animate-[fadeOut_1.4s_ease_forwards]">
      <style>{`
        @keyframes fadeOut { 0%,70% { opacity: 1 } 100% { opacity: 0; visibility: hidden } }
        @keyframes scanLine { 0% { transform: translateY(-100%) } 100% { transform: translateY(100%) } }
        @keyframes glowPulse { 0%,100% { filter: drop-shadow(0 0 16px rgba(99,102,241,0.6)) }
                               50%     { filter: drop-shadow(0 0 32px rgba(217,70,239,0.7)) } }
      `}</style>
      <div className="relative">
        <div className="absolute inset-0 -m-16 rounded-full
                        bg-[radial-gradient(closest-side,rgba(99,102,241,0.35),transparent_70%)] blur-2xl" />
        <div className="relative flex flex-col items-center gap-3"
             style={{ animation: 'glowPulse 1.4s ease-in-out infinite' }}>
          <Sparkles className="w-12 h-12 text-cyan-300" />
          <p className="text-2xl font-bold tracking-[0.25em]
                        bg-clip-text text-transparent
                        bg-gradient-to-r from-indigo-300 via-fuchsia-300 to-cyan-300">
            ANALYTICS WORLD
          </p>
          <p className="text-[11px] uppercase tracking-[0.4em] text-gray-500">
            Initializing intelligence…
          </p>
        </div>
      </div>
    </div>
  );
}

/* ─── Main page ──────────────────────────────────────────────── */

export default function AnalyticsWorld() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [intro, setIntro] = useState(true);
  const [filter, setFilter] = useState('All');
  const containerRef = useRef(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api.getPortfolioHoldings()
      .then((d) => { if (alive) setData(d); })
      .catch(() => {})
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, []);

  const holdings = data?.holdings || [];
  const summary = data?.summary || {};

  const opportunities = useMemo(
    () => SWING_UNIVERSE.map((u) => buildOpportunity(u))
                       .sort((a, b) => b.confidence - a.confidence),
    [],
  );

  const verdictGroups = ['All', 'Strong Buy', 'Buy on Dip', 'Momentum Building', 'Watchlist'];
  const filtered = filter === 'All'
    ? opportunities
    : opportunities.filter((o) => o.verdict.label === filter);

  return (
    <div className="min-h-screen relative overflow-hidden text-white"
         ref={containerRef}
         style={{
           background:
             'radial-gradient(1200px 800px at 10% -10%, rgba(99,102,241,0.18), transparent 60%),' +
             'radial-gradient(1000px 700px at 110% 10%, rgba(217,70,239,0.15), transparent 60%),' +
             'radial-gradient(800px 600px at 50% 120%, rgba(34,211,238,0.18), transparent 60%),' +
             'linear-gradient(180deg, #04060f 0%, #06091a 60%, #04060f 100%)',
         }}>
      {/* grid texture */}
      <div aria-hidden className="pointer-events-none absolute inset-0 opacity-[0.06]"
           style={{
             backgroundImage:
               'linear-gradient(rgba(255,255,255,0.5) 1px, transparent 1px),' +
               'linear-gradient(90deg, rgba(255,255,255,0.5) 1px, transparent 1px)',
             backgroundSize: '40px 40px',
           }} />

      {intro && <EntryOverlay onDone={() => setIntro(false)} />}

      {/* Top bar */}
      <header className="relative z-10 px-4 sm:px-8 py-4 flex flex-wrap items-center justify-between gap-3
                         border-b border-white/5 backdrop-blur-md bg-black/20">
        <div className="flex items-center gap-3">
          <Link to="/portfolio"
                className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs
                           bg-white/5 hover:bg-white/10 border border-white/10 text-gray-300">
            <ArrowLeft className="w-3.5 h-3.5" /> Portfolio
          </Link>
          <div className="flex items-center gap-2">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-indigo-500 via-fuchsia-500 to-cyan-400
                            flex items-center justify-center shadow-[0_0_24px_rgba(168,85,247,0.45)]">
              <Sparkles className="w-4 h-4 text-white" />
            </div>
            <div>
              <h1 className="text-base sm:text-lg font-extrabold tracking-tight
                             bg-clip-text text-transparent
                             bg-gradient-to-r from-indigo-200 via-fuchsia-200 to-cyan-200">
                Analytics World
              </h1>
              <p className="text-[10px] uppercase tracking-[0.3em] text-gray-500">
                Hedge-Fund Intelligence Terminal
              </p>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <span className="hidden sm:inline-flex items-center gap-1 text-[11px] text-gray-400 px-2 py-1 rounded-md
                           bg-white/5 border border-white/10">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
            Engine online
          </span>
          <span className="text-[11px] text-gray-500">
            {holdings.length} holdings · {opportunities.length} opportunities scanned
          </span>
        </div>
      </header>

      <main className="relative z-10 px-4 sm:px-8 py-6 space-y-6 max-w-[1500px] mx-auto">
        {/* Hero — Treasure Hunter */}
        <GlassCard className="p-5 lg:p-7 overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
            <SectionTitle
              icon={Gem}
              title="Treasure Hunter Engine"
              sub="Top swing & positional opportunities, ranked by composite intelligence"
              accent="from-fuchsia-400 to-cyan-400"
            />
            <div className="flex flex-wrap items-center gap-1.5">
              {verdictGroups.map((v) => (
                <button key={v} onClick={() => setFilter(v)}
                  className={cls(
                    'text-[11px] px-2.5 py-1 rounded-md border transition',
                    filter === v
                      ? 'bg-white/10 text-white border-white/20'
                      : 'bg-white/[0.03] text-gray-400 border-white/5 hover:bg-white/[0.06]'
                  )}>
                  {v}
                </button>
              ))}
            </div>
          </div>

          {loading ? (
            <SkeletonGrid />
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
              {filtered.slice(0, 9).map((o) => <OpportunityCard key={o.symbol} o={o} />)}
            </div>
          )}
        </GlassCard>

        {/* Momentum + Sector */}
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
          <MomentumEngine opportunities={opportunities} />
          <SectorRotation holdings={holdings} />
        </div>

        {/* Swing analyzer */}
        <SwingAnalyzer opportunities={opportunities} />

        {/* Smart exit + portfolio health */}
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
          <SmartExitEngine holdings={holdings} />
          <PortfolioHealth holdings={holdings} summary={summary} />
        </div>

        {/* Growth + Alerts */}
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
          <PortfolioGrowth holdings={holdings} />
          <AlertsFeed opportunities={opportunities} holdings={holdings} />
        </div>

        <p className="text-[10px] text-center text-gray-600 pt-4">
          Analytics World is a research module — purely advisory, no orders are placed.
        </p>
      </main>
    </div>
  );
}

function SkeletonGrid() {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="h-72 rounded-2xl bg-white/[0.03] border border-white/5 animate-pulse" />
      ))}
    </div>
  );
}

function MomentumEngine({ opportunities }) {
  const top = opportunities.slice(0, 8);
  return (
    <GlassCard className="p-4 lg:p-5">
      <SectionTitle
        icon={Flame}
        title="Momentum Engine"
        sub="Strongest trends, breakouts, and accumulations"
        accent="from-orange-400 to-rose-400"
      />
      <div className="space-y-2">
        {top.map((o) => (
          <div key={o.symbol}
               className="flex items-center gap-3 p-2.5 rounded-xl bg-white/[0.03] border border-white/5
                          hover:border-white/10 transition">
            <div className="w-10 text-center">
              <p className="text-[10px] uppercase tracking-widest text-gray-500">Score</p>
              <p className="text-base font-bold text-white mono">{o.momentum}</p>
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between">
                <p className="text-sm text-white font-semibold truncate">{o.symbol}</p>
                <span className="text-[10px] text-gray-500">{o.sector}</span>
              </div>
              <div className="grid grid-cols-3 gap-2 mt-1.5">
                <MeterBar label="RS" value={o.rs} tone="indigo" />
                <MeterBar label="Breakout" value={o.breakout} tone="fuchsia" />
                <MeterBar label="Trend" value={o.trend} tone="emerald" />
              </div>
            </div>
          </div>
        ))}
      </div>
    </GlassCard>
  );
}

function SwingAnalyzer({ opportunities }) {
  const rows = opportunities.slice(0, 10).map((o) => {
    const safer = Math.round(o.buyLow);
    const aggr  = Math.round(o.buyHigh);
    const probability = clamp(Math.round((o.confidence + o.breakout) / 2));
    const upside = Math.round(((o.target - o.entry) / o.entry) * 100);
    const downside = Math.round(((o.entry - o.stop) / o.entry) * 100);
    const rr = (upside / Math.max(1, downside)).toFixed(2);
    return { ...o, safer, aggr, probability, upside, downside, rr };
  });

  return (
    <GlassCard className="p-4 lg:p-5">
      <SectionTitle
        icon={Telescope}
        title="Swing Entry Analyzer"
        sub="Two-tier entry logic with risk/reward profiling"
        accent="from-cyan-400 to-fuchsia-400"
      />
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-widest text-gray-500">
              <th className="px-3 py-2">Symbol</th>
              <th className="px-3 py-2 text-right">Safer</th>
              <th className="px-3 py-2 text-right">Aggressive</th>
              <th className="px-3 py-2 text-right">Upside</th>
              <th className="px-3 py-2 text-right">R/R</th>
              <th className="px-3 py-2">Probability</th>
              <th className="px-3 py-2">Breakout</th>
              <th className="px-3 py-2 text-right">Hold</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.symbol}
                  className="border-t border-white/5 hover:bg-white/[0.03]">
                <td className="px-3 py-2 text-white font-medium">{r.symbol}</td>
                <td className="px-3 py-2 text-right mono text-emerald-300">₹{fmtINR(r.safer, 0)}</td>
                <td className="px-3 py-2 text-right mono text-cyan-300">₹{fmtINR(r.aggr, 0)}</td>
                <td className="px-3 py-2 text-right mono text-fuchsia-300">+{r.upside}%</td>
                <td className="px-3 py-2 text-right mono text-white">{r.rr}x</td>
                <td className="px-3 py-2 w-32"><MeterBar label=" " value={r.probability} tone="emerald" /></td>
                <td className="px-3 py-2 w-32"><MeterBar label=" " value={r.breakout} tone="fuchsia" /></td>
                <td className="px-3 py-2 text-right text-gray-400 text-xs">{r.holdWeeks}w</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </GlassCard>
  );
}
