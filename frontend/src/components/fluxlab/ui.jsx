import React from 'react';
import { AlertTriangle, Info, Check, Minus } from 'lucide-react';

/** Shared display pieces for the Flux Strategy Test Lab (hammer / inverted hammer). */

export const N = (v, d = 2) => (v == null || Number.isNaN(Number(v)) ? '—'
  : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
export const N0 = (v) => N(v, 0);
export const PCT = (v, d = 1) => (v == null ? '—' : `${Number(v).toFixed(d)}%`);
export const RS = (v) => (v == null ? '—' : `${Number(v) < 0 ? '−' : Number(v) > 0 ? '+' : ''}₹${Math.abs(Number(v)).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`);
export const PTS = (v, d = 1) => (v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(d)} pts`);
export const tone = (v) => (v == null ? 'text-gray-400' : v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-gray-300');
export const input = 'input-field !py-1.5 w-full text-[12.5px]';

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

export function Stat({ label, value, sub, tone: t = 'text-gray-100', title }) {
  return (
    <div className="min-w-0" title={title}>
      <div className="text-[10px] uppercase tracking-wider text-gray-500">{label}</div>
      <div className={`text-[15px] font-semibold mono truncate ${t}`}>{value}</div>
      {sub && <div className="text-[10.5px] text-gray-500 truncate">{sub}</div>}
    </div>
  );
}

export function Field({ label, hint, children }) {
  return (
    <label className="block" title={hint}>
      <div className="text-[10px] uppercase tracking-wider text-gray-500">{label}</div>
      {children}
      {hint && <div className="text-[10px] text-gray-600 mt-0.5 leading-tight">{hint}</div>}
    </label>
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

const REASON_CLS = {
  TARGET: 'bg-emerald-500/15 text-emerald-400', STOP: 'bg-red-500/15 text-red-400',
  TIME: 'bg-surface-3 text-gray-300', EOD: 'bg-surface-3 text-gray-400',
};
export function Reason({ r }) {
  return <span className={`px-1.5 py-px rounded text-[10.5px] font-semibold ${REASON_CLS[r] || 'bg-surface-3 text-gray-300'}`}>{r || '—'}</span>;
}

export function SideTag({ side }) {
  const bull = side === 'BULLISH';
  return (
    <span className={`px-1.5 py-px rounded text-[10.5px] font-bold ${bull
      ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'}`}>
      {bull ? 'Hammer' : 'Inv. hammer'}
    </span>
  );
}

/** Every condition of both indicators, with the value it had on that candle. */
export function Checks({ checks = {} }) {
  const groups = [['rejection', 'Long-tail rejection'], ['shape', 'Body & filters']];
  return (
    <div className="grid sm:grid-cols-2 gap-3">
      {groups.map(([key, title]) => (
        <div key={key}>
          <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">{title}</div>
          {(checks[key] || []).map((c, i) => (
            <div key={i} className="flex items-start gap-1.5 py-0.5">
              {c.passed ? <Check className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-px" />
                : <Minus className="w-3.5 h-3.5 text-gray-600 shrink-0 mt-px" />}
              <span className="text-[11.5px] text-gray-300">{c.label}</span>
              <span className="text-[11px] text-gray-500 mono ml-auto whitespace-nowrap">{c.detail}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

/** Month-by-month P&L as coloured tiles. */
export function MonthTiles({ months = [] }) {
  if (!months.length) return <div className="py-6 text-center text-[12px] text-gray-500">No months yet.</div>;
  const max = Math.max(...months.map((m) => Math.abs(m.net || 0)), 1);
  return (
    <div className="grid grid-cols-3 sm:grid-cols-6 lg:grid-cols-12 gap-1.5">
      {months.map((m) => {
        const a = Math.min(1, Math.abs(m.net || 0) / max);
        const bg = (m.net || 0) >= 0 ? `rgba(16,185,129,${0.12 + a * 0.5})` : `rgba(239,68,68,${0.12 + a * 0.5})`;
        return (
          <div key={m.month} className="rounded-md px-1.5 py-1.5 text-center" style={{ background: bg }}
            title={`${m.month}: ${m.trades} trades, ${m.wins} won`}>
            <div className="text-[10px] text-gray-300">{m.month}</div>
            <div className="text-[11.5px] font-semibold mono text-white">{RS(m.net)}</div>
            <div className="text-[9.5px] text-gray-300">{m.wins}/{m.trades} won</div>
          </div>
        );
      })}
    </div>
  );
}

/** Cumulative P&L across the trade sequence. */
export function EquityCurve({ trades = [], height = 150 }) {
  if (trades.length < 2) return null;
  let run = 0;
  const eq = [0, ...trades.map((t) => (run += t.pnl || 0))];
  const w = 720;
  const lo = Math.min(...eq), hi = Math.max(...eq);
  const pad = (hi - lo) * 0.08 || 1;
  const y = (v) => height - ((v - (lo - pad)) / ((hi + pad) - (lo - pad))) * height;
  const x = (i) => (i / (eq.length - 1)) * w;
  const path = eq.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const up = eq[eq.length - 1] >= 0;
  return (
    <svg viewBox={`0 0 ${w} ${height}`} className="w-full" style={{ height }} preserveAspectRatio="none">
      <line x1="0" x2={w} y1={y(0)} y2={y(0)} stroke="#ffffff22" strokeDasharray="4 4" />
      <path d={path} fill="none" stroke={up ? '#10b981' : '#ef4444'} strokeWidth="1.6" />
    </svg>
  );
}
