import React from 'react';

/** Shared display pieces for My Equity Workspace — formatting, the volume sparkline,
 *  the sensitivity meter and the research-level chips. */

export const N = (v, d = 2) => (v == null || Number.isNaN(Number(v)) ? '—'
  : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));

export const COMPACT = (v) => {
  if (v == null) return '—';
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  if (Math.abs(n) >= 1e7) return `${(n / 1e7).toFixed(2)} Cr`;
  if (Math.abs(n) >= 1e5) return `${(n / 1e5).toFixed(2)} L`;
  if (Math.abs(n) >= 1e3) return `${(n / 1e3).toFixed(1)} K`;
  return n.toLocaleString('en-IN');
};

export const PCT = (v, d = 2) => (v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(d)}%`);
export const signTone = (v) => (v == null ? 'text-gray-400' : v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-gray-400');

export const RSI_TONE = {
  oversold: 'text-red-400 font-bold',
  weak: 'text-amber-400',
  neutral: 'text-gray-300',
  strong: 'text-emerald-300',
  overbought: 'text-emerald-400 font-bold',
  none: 'text-gray-500',
};

export const TONE_CLASS = {
  alert: 'bg-amber-500/15 text-amber-500 border-amber-500/30',
  warn: 'bg-amber-500/10 text-amber-500 border-amber-500/20',
  good: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
  info: 'bg-brand-500/10 text-brand-400 border-brand-500/20',
};

export function Section({ title, right, children, className = '' }) {
  return (
    <div className={`card !p-4 ${className}`}>
      {(title || right) && (
        <div className="flex items-center justify-between gap-2 mb-2.5">
          <div className="text-[12px] font-semibold uppercase tracking-wider text-gray-400">{title}</div>
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

export function Stat({ label, value, sub, tone = 'text-gray-100', title }) {
  return (
    <div className="min-w-0" title={title}>
      <div className="text-[10px] uppercase tracking-wider text-gray-500">{label}</div>
      <div className={`text-[15px] font-semibold mono truncate ${tone}`}>{value}</div>
      {sub && <div className="text-[11px] text-gray-500 truncate">{sub}</div>}
    </div>
  );
}

/** Recent sessions as bars, newest on the right and highlighted — the "latest vs older"
 *  volume picture in one cell. */
export function VolumeSparkline({ bars = [], width = 84, height = 26 }) {
  if (!bars.length) return <span className="text-gray-600">—</span>;
  const vals = bars.map((b) => Number(b.volume) || 0);
  const max = Math.max(...vals, 1);
  const bw = width / bars.length;
  return (
    <svg width={width} height={height} className="block">
      {bars.map((b, i) => {
        const h = Math.max(1.5, (vals[i] / max) * (height - 2));
        const last = i === bars.length - 1;
        return (
          <rect key={b.date || i} x={i * bw} y={height - h} width={Math.max(1.5, bw - 1.5)} height={h}
            rx={1} className={last ? 'fill-brand-400' : 'fill-gray-600/70'}>
            <title>{`${b.date}: ${COMPACT(b.volume)}`}</title>
          </rect>
        );
      })}
    </svg>
  );
}

/** −100 … +100 meter with the zero line marked. */
export function SensitivityMeter({ sens, width = 92 }) {
  if (!sens || sens.score == null) return <span className="text-gray-600">—</span>;
  const s = Math.max(-100, Math.min(100, Number(sens.score)));
  const mid = width / 2;
  const w = Math.abs(s) / 100 * mid;
  const tone = s >= 20 ? 'fill-emerald-500' : s <= -20 ? 'fill-red-500' : 'fill-gray-500';
  const text = s >= 20 ? 'text-emerald-400' : s <= -20 ? 'text-red-400' : 'text-gray-400';
  return (
    <div className="flex items-center gap-2">
      <svg width={width} height={12} className="shrink-0">
        <rect x={0} y={5} width={width} height={2} className="fill-surface-4" rx={1} />
        <rect x={s >= 0 ? mid : mid - w} y={2} width={Math.max(1.5, w)} height={8} rx={2} className={tone} />
        <rect x={mid - 0.5} y={0} width={1} height={12} className="fill-gray-500" />
      </svg>
      <div className="min-w-0">
        <div className={`text-[11.5px] font-semibold leading-tight ${text}`}>{sens.label}</div>
        <div className="text-[10px] text-gray-500 mono leading-tight">{s > 0 ? '+' : ''}{s.toFixed(0)}</div>
      </div>
    </div>
  );
}

/** The levels written down when the stock was added; the nearest one is highlighted. */
export function LevelChips({ watch, onEdit }) {
  const rows = watch?.rows || (watch?.levels || []).map((l) => ({ level: l, distance_pct: null }));
  if (!rows.length) {
    return (
      <button onClick={onEdit} className="text-[11px] text-gray-500 hover:text-brand-400 underline decoration-dotted">
        add a level
      </button>
    );
  }
  const nearest = watch?.nearest?.level;
  return (
    <div className="flex flex-wrap items-center gap-1">
      {rows.map((r) => {
        const near = r.level === nearest;
        const hit = near && watch?.touched;
        return (
          <span key={r.level}
            title={r.distance_pct == null ? '' : `price is ${PCT(r.distance_pct)} from this level`}
            className={`px-1.5 py-0.5 rounded border text-[11px] mono whitespace-nowrap ${hit
              ? 'bg-amber-500/20 text-amber-500 border-amber-500/40 font-semibold'
              : near ? 'bg-surface-3 text-gray-200 border-surface-4' : 'bg-surface-2 text-gray-400 border-surface-3'}`}>
            {N(r.level, 2)}
            {r.distance_pct != null && <span className="ml-1 text-[10px] opacity-70">{PCT(r.distance_pct, 1)}</span>}
          </span>
        );
      })}
    </div>
  );
}

export function AlertPills({ alerts = [], max = 3 }) {
  if (!alerts.length) return <span className="text-gray-700">—</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {alerts.slice(0, max).map((a) => (
        <span key={a.key} className={`px-1.5 py-0.5 rounded border text-[10.5px] whitespace-nowrap ${TONE_CLASS[a.tone] || TONE_CLASS.info}`}>
          {a.text}
        </span>
      ))}
      {alerts.length > max && <span className="text-[10.5px] text-gray-500">+{alerts.length - max}</span>}
    </div>
  );
}
