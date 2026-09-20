import React from 'react';
import { ArrowUp, ArrowDown, Check, Minus } from 'lucide-react';

/** Shared display pieces for My Equity Workspace — formatting, level chips, meters, pills. */

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
/** A price the way a trader writes it: paise for penny stocks, whole rupees for big ones. */
export const LVL = (v) => (v == null ? '—' : N(v, Math.abs(Number(v)) < 100 ? 2 : 0));
export const DAYS = (n) => (n == null ? '' : n === 0 ? 'today' : n === 1 ? '1 day' : `${n} days`);

/** Row tint: a level touched right now wins; then a deeply oversold or very overbought RSI. */
export function rowTone(row) {
  if (row?.watch?.touched) return 'blink';
  const rsi = row?.rsi;
  if (rsi != null && rsi <= 30) return 'oversold';
  if (rsi != null && rsi >= 80) return 'overbought';
  return 'plain';
}

export const ROW_CLASS = {
  blink: 'row-blink',
  oversold: 'bg-emerald-500/10 hover:bg-emerald-500/15',
  overbought: 'bg-red-500/10 hover:bg-red-500/15',
  plain: 'hover:bg-surface-2/60',
};

// A tinted background and a coloured dot carry the meaning; the text itself uses the theme's
// own grey, so a pill reads the same on a white page as on a dark one.
export const TONE_CLASS = {
  alert: 'bg-amber-500/15 border-amber-500/40',
  warn: 'bg-amber-500/10 border-amber-500/25',
  good: 'bg-emerald-500/12 border-emerald-500/30',
  info: 'bg-brand-500/12 border-brand-500/30',
};
export const TONE_DOT = {
  alert: 'bg-amber-500', warn: 'bg-amber-500', good: 'bg-emerald-500', info: 'bg-brand-500',
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

export function CategoryChip({ category, onClick, small }) {
  const inv = category === 'INVESTMENT';
  return (
    <button onClick={onClick} disabled={!onClick} title={onClick ? 'click to switch' : ''}
      className={`px-1.5 py-0.5 rounded border font-semibold whitespace-nowrap ${small ? 'text-[9.5px]' : 'text-[11px]'} ${inv
        ? 'bg-violet-500/15 text-violet-300 border-violet-500/30'
        : 'bg-sky-500/15 text-sky-300 border-sky-500/30'} ${onClick ? 'hover:brightness-125' : ''}`}>
      {inv ? 'Investment' : 'Swing'}
    </button>
  );
}

/**
 * One research level. The arrow says where the last trade is relative to it, and the chip keeps
 * its own dark background with a coloured border and an explicit ▲/▼ glyph, so it stays legible
 * on a green, red or blinking row — colour is never the only signal.
 */
export function LevelChip({ row, onToggle }) {
  const d = row.distance_pct;
  const up = d != null && d >= 0;
  const hit = row.triggered;
  const near = row.near;
  const border = hit ? (row.pnl_pct >= 0 ? 'border-emerald-500/60' : 'border-red-500/60')
    : near ? 'border-amber-500/70' : 'border-surface-4';
  return (
    <span title={hit
      ? `triggered ${row.triggered_on} · ${PCT(row.pnl_pct)} since`
      : d == null ? '' : `last trade is ${PCT(d)} ${up ? 'above' : 'below'} this level`}
      onClick={onToggle ? (e) => { e.stopPropagation(); onToggle(row.level); } : undefined}
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border bg-surface-0/85 ${border}
        ${row.track === false ? 'opacity-45' : ''} ${onToggle ? 'cursor-pointer' : ''} whitespace-nowrap`}>
      <span className="mono text-[11px] text-gray-100">{N(row.level, 2)}</span>
      {d != null && (
        <span className={`inline-flex items-center text-[10px] mono font-semibold ${up ? 'text-emerald-400' : 'text-red-400'}`}>
          {up ? <ArrowUp className="w-2.5 h-2.5" /> : <ArrowDown className="w-2.5 h-2.5" />}
          {Math.abs(d).toFixed(1)}%
        </span>
      )}
      {hit && <Check className="w-2.5 h-2.5 text-emerald-400" />}
    </span>
  );
}

export function LevelChips({ watch, onEdit, onToggle }) {
  const rows = watch?.rows || [];
  if (!rows.length) {
    return (
      <button onClick={(e) => { e.stopPropagation(); onEdit?.(); }}
        className="text-[11px] text-gray-500 hover:text-brand-400 underline decoration-dotted">add a level</button>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-1">
      {rows.map((r) => <LevelChip key={r.level} row={r} onToggle={onToggle} />)}
    </div>
  );
}

/** The P&L a research level has produced since it triggered. */
export function PnLCell({ watch }) {
  const p = watch?.primary;
  if (!p) {
    if (watch?.status === 'waiting' && watch.waiting_distance_pct != null) {
      const d = watch.waiting_distance_pct;
      return (
        <div className="whitespace-nowrap">
          <div className="text-[11.5px] text-gray-400">not triggered</div>
          <div className="text-[10.5px] text-gray-500 mono">{Math.abs(d).toFixed(1)}% {d > 0 ? 'above' : 'below'} {LVL(watch.waiting_for)}</div>
        </div>
      );
    }
    return <span className="text-gray-700">—</span>;
  }
  const extra = watch.extra || [];
  return (
    <div className="whitespace-nowrap">
      <div className={`text-[13px] font-bold mono ${signTone(p.pnl_pct)}`}>{PCT(p.pnl_pct)}</div>
      <div className="text-[10.5px] text-gray-500">
        from {LVL(p.level)} · {DAYS(p.days_since)}
        {extra.length > 0 && <span className="ml-1 text-brand-400">+{extra.length}</span>}
      </div>
    </div>
  );
}

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

export function VolumeSparkline({ bars = [], width = 84, height = 26 }) {
  if (!bars.length) return <span className="text-gray-600">—</span>;
  const vals = bars.map((b) => Number(b.volume) || 0);
  const max = Math.max(...vals, 1);
  const bw = width / bars.length;
  return (
    <svg width={width} height={height} className="block">
      {bars.map((b, i) => {
        const h = Math.max(1.5, (vals[i] / max) * (height - 2));
        return (
          <rect key={b.date || i} x={i * bw} y={height - h} width={Math.max(1.5, bw - 1.5)} height={h} rx={1}
            className={i === bars.length - 1 ? 'fill-brand-400' : 'fill-gray-600/70'}>
            <title>{`${b.date}: ${COMPACT(b.volume)}`}</title>
          </rect>
        );
      })}
    </svg>
  );
}

export function NotePills({ notes = [], max = 2 }) {
  if (!notes.length) return <span className="text-gray-700">—</span>;
  return (
    <div className="flex flex-col gap-1">
      {notes.slice(0, max).map((a) => (
        <span key={a.key}
          className={`inline-flex items-start gap-1.5 px-1.5 py-0.5 rounded border text-[10.5px] leading-tight text-gray-200 ${TONE_CLASS[a.tone] || TONE_CLASS.info}`}>
          <span className={`mt-1 w-1 h-1 rounded-full shrink-0 ${TONE_DOT[a.tone] || TONE_DOT.info}`} />
          {a.text}
        </span>
      ))}
      {notes.length > max && <span className="text-[10px] text-gray-500 pl-0.5">+{notes.length - max} more</span>}
    </div>
  );
}

export function Empty({ icon: Icon = Minus, title, hint }) {
  return (
    <div className="py-8 text-center">
      <Icon className="w-5 h-5 text-gray-600 mx-auto mb-1.5" />
      <div className="text-[13px] text-gray-300">{title}</div>
      {hint && <div className="text-[11.5px] text-gray-500 mt-0.5">{hint}</div>}
    </div>
  );
}
