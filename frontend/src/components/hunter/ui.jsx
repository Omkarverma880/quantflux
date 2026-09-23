import React from 'react';
import { AlertTriangle, Info } from 'lucide-react';

/** Shared display pieces for the Hunter. */

export const N = (v, d = 2) => (v == null || Number.isNaN(Number(v)) ? '—'
  : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
export const N0 = (v) => N(v, 0);
export const RS_ = (v) => (v == null ? '—' : `₹${Number(v).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`);
export const PCT = (v, d = 1) => (v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(d)}%`);
export const tone = (v) => (v == null ? 'text-gray-400' : v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-gray-300');

export const STAGE_STYLE = {
  FORMING: { ring: 'border-emerald-500/50 bg-emerald-500/10', dot: 'bg-emerald-400', text: 'text-emerald-400' },
  BREAKOUT: { ring: 'border-sky-500/50 bg-sky-500/10', dot: 'bg-sky-400', text: 'text-sky-400' },
  CLIMBING: { ring: 'border-violet-500/50 bg-violet-500/10', dot: 'bg-violet-400', text: 'text-violet-400' },
  PLAYED_OUT: { ring: 'border-surface-3 bg-surface-2/60', dot: 'bg-gray-500', text: 'text-gray-400' },
};

export function Section({ title, right, children, className = '' }) {
  return (
    <div className={`card !p-4 ${className}`}>
      {(title || right) && (
        <div className="flex flex-wrap items-center justify-between gap-2 mb-2.5">
          <div className="text-[12px] font-semibold uppercase tracking-wider text-gray-400">{title}</div>
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

export function Measure({ label, value, sub, tone: t = 'text-gray-100' }) {
  return (
    <div className="min-w-0">
      <div className="text-[9.5px] uppercase tracking-wider text-gray-500">{label}</div>
      <div className={`text-[13px] font-semibold mono truncate ${t}`}>{value}</div>
      {sub && <div className="text-[10px] text-gray-500 truncate">{sub}</div>}
    </div>
  );
}

export function Note({ children, tone: t = 'info' }) {
  const Icon = t === 'warn' ? AlertTriangle : Info;
  const cls = t === 'warn' ? 'text-amber-500 border-amber-500/30 bg-amber-500/10'
    : 'text-gray-400 border-surface-3 bg-surface-2/40';
  return (
    <div className={`flex items-start gap-1.5 rounded-lg border px-2.5 py-1.5 text-[11.5px] ${cls}`}>
      <Icon className="w-3.5 h-3.5 shrink-0 mt-px" />
      <span>{children}</span>
    </div>
  );
}

/** Relative strength as a 1–99 rating: how the stock ranks against everything scanned today. */
export function RsPill({ value }) {
  if (value == null) return <span className="text-[11px] text-gray-500">RS —</span>;
  const strong = value >= 85, ok = value >= 70;
  return (
    <span className={`px-1.5 py-0.5 rounded text-[10.5px] font-bold ${strong ? 'bg-emerald-500/20 text-emerald-300'
      : ok ? 'bg-emerald-500/10 text-emerald-400' : 'bg-surface-3 text-gray-400'}`}>RS {value}</span>
  );
}
