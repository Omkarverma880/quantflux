import React, { useEffect, useRef, useState } from 'react';
import { ArrowUp, ArrowDown, Check, Minus, ChevronDown, Loader2, Bell, BellOff, Target, ShieldAlert } from 'lucide-react';

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

export const CATEGORY_STYLE = {
  INVESTMENT: 'bg-violet-500/15 text-violet-300 border-violet-500/40',
  SWING: 'bg-sky-500/15 text-sky-300 border-sky-500/40',
};
const CATEGORY_LABEL = { INVESTMENT: 'Investment', SWING: 'Swing' };

/** A read-only badge. Use ``CategoryPicker`` wherever it can be changed. */
export function CategoryChip({ category, small }) {
  const key = category === 'INVESTMENT' ? 'INVESTMENT' : 'SWING';
  return (
    <span className={`px-1.5 py-0.5 rounded border font-semibold whitespace-nowrap ${small ? 'text-[10px]' : 'text-[11px]'} ${CATEGORY_STYLE[key]}`}>
      {CATEGORY_LABEL[key]}
    </span>
  );
}

/**
 * Investment or Swing, picked from a menu rather than toggled blindly.
 *
 * The caret says it opens, both options are shown with the current one ticked, and the menu is
 * positioned fixed so the table's own scrolling never clips it.
 */
export function CategoryPicker({ category, onChange, small, disabled }) {
  const key = category === 'INVESTMENT' ? 'INVESTMENT' : 'SWING';
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [at, setAt] = useState({ top: 0, left: 0 });
  const btn = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const close = () => setOpen(false);
    const esc = (e) => { if (e.key === 'Escape') setOpen(false); };
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    window.addEventListener('keydown', esc);
    document.addEventListener('click', close);
    return () => {
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
      window.removeEventListener('keydown', esc);
      document.removeEventListener('click', close);
    };
  }, [open]);

  const toggle = (e) => {
    e.stopPropagation();
    if (disabled) return;
    const r = btn.current?.getBoundingClientRect();
    if (r) setAt({ top: r.bottom + 4, left: Math.min(r.left, window.innerWidth - 150) });
    setOpen((o) => !o);
  };

  const pick = async (e, value) => {
    e.stopPropagation();
    setOpen(false);
    if (value === key) return;
    setBusy(true);
    try { await onChange?.(value); } finally { setBusy(false); }
  };

  return (
    <>
      <button ref={btn} onClick={toggle} disabled={disabled} title="Investment or swing trade — click to change"
        className={`inline-flex items-center gap-0.5 pl-1.5 pr-1 py-0.5 rounded border font-semibold whitespace-nowrap
          ${small ? 'text-[10px]' : 'text-[11px]'} ${CATEGORY_STYLE[key]} hover:brightness-125 disabled:opacity-50`}>
        {busy ? <Loader2 className="w-2.5 h-2.5 animate-spin" /> : null}
        {CATEGORY_LABEL[key]}
        <ChevronDown className="w-2.5 h-2.5 opacity-70" />
      </button>
      {open && (
        <div style={{ position: 'fixed', top: at.top, left: at.left, zIndex: 60 }}
          onClick={(e) => e.stopPropagation()}
          className="w-[150px] rounded-lg border border-surface-3 bg-surface-1 shadow-xl overflow-hidden">
          {['SWING', 'INVESTMENT'].map((v) => (
            <button key={v} onClick={(e) => pick(e, v)}
              className="w-full flex items-center justify-between px-2.5 py-1.5 text-[12px] text-gray-200 hover:bg-surface-2">
              <span>{v === 'INVESTMENT' ? 'Investment' : 'Swing trade'}</span>
              {v === key && <Check className="w-3 h-3 text-emerald-400" />}
            </button>
          ))}
          <div className="px-2.5 py-1 text-[10px] text-gray-500 border-t border-surface-3">
            sets the horizon: 60 sessions or 10
          </div>
        </div>
      )}
    </>
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
      className={`flex items-center justify-between gap-2 px-1.5 py-0.5 rounded border bg-surface-0/85 ${border}
        ${row.track === false ? 'opacity-45' : ''} ${onToggle ? 'cursor-pointer' : ''} whitespace-nowrap`}>
      <span className="mono text-[11px] text-gray-100">{N(row.level, 2)}</span>
      <span className="inline-flex items-center gap-0.5">
        {row.target && <Target className="w-2.5 h-2.5 text-gray-500" />}
        {d != null && (
          <span className={`inline-flex items-center text-[10px] mono font-semibold ${up ? 'text-emerald-400' : 'text-red-400'}`}>
            {up ? <ArrowUp className="w-2.5 h-2.5" /> : <ArrowDown className="w-2.5 h-2.5" />}
            {Math.abs(d).toFixed(1)}%
          </span>
        )}
        {hit && <Check className="w-2.5 h-2.5 text-emerald-400" />}
      </span>
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
  // stacked, one level per line: easier to scan down a column than to read along a row
  return (
    <div className="flex flex-col items-stretch gap-1 w-[122px]">
      {rows.map((r) => <LevelChip key={r.level} row={r} onToggle={onToggle} />)}
    </div>
  );
}

/** How far a triggered level has travelled from the entry towards the target you wrote down. */
export function TargetProgress({ row, width = 108 }) {
  if (!row?.target) return null;
  const pct = Math.max(0, Math.min(100, row.progress_pct ?? 0));
  const done = row.state === 'target reached';
  const dead = row.state === 'stopped out';
  return (
    <div className="mt-0.5" title={`entry ${N(row.level)} → target ${N(row.target)}${row.stop ? ` · stop ${N(row.stop)}` : ''}`}>
      <div className="relative h-1 rounded-full bg-surface-4 overflow-hidden" style={{ width }}>
        <div className={`absolute inset-y-0 left-0 rounded-full ${dead ? 'bg-red-500' : done ? 'bg-emerald-500' : 'bg-brand-500'}`}
          style={{ width: `${done ? 100 : pct}%` }} />
      </div>
      <div className="text-[9.5px] text-gray-500 mt-0.5 whitespace-nowrap">
        {dead ? <span className="text-red-400">stopped out at {LVL(row.stop)}</span>
          : done ? <span className="text-emerald-400">target {LVL(row.target)} reached</span>
            : <>{pct.toFixed(0)}% to {LVL(row.target)}</>}
      </div>
    </div>
  );
}

/** Alerts for one stock — the bell is the switch. */
export function AlertBell({ on, onToggle, busy }) {
  return (
    <button onClick={(e) => { e.stopPropagation(); onToggle?.(!on); }} disabled={busy}
      title={on ? 'alerts on — click to mute this stock' : 'alerts muted — click to turn them on'}
      className={`p-1 transition ${on ? 'text-brand-400 hover:text-brand-300' : 'text-gray-600 hover:text-gray-400'}`}>
      {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
        : on ? <Bell className="w-3.5 h-3.5" /> : <BellOff className="w-3.5 h-3.5" />}
    </button>
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
      <div className="flex items-center gap-1">
        <span className={`text-[13px] font-bold mono ${signTone(p.pnl_pct)}`}>{PCT(p.pnl_pct)}</span>
        {p.state === 'stopped out' && <ShieldAlert className="w-3 h-3 text-red-400" />}
        {p.state === 'target reached' && <Check className="w-3 h-3 text-emerald-400" />}
      </div>
      <div className="text-[10.5px] text-gray-500">
        from {LVL(p.level)} · {DAYS(p.days_since)}
        {extra.length > 0 && <span className="ml-1 text-brand-400">+{extra.length}</span>}
      </div>
      <TargetProgress row={p} />
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
