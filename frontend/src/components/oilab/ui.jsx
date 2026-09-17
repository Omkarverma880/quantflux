import React, { useState } from 'react';
import { Info } from 'lucide-react';
import { useTheme } from '../../ThemeContext';

/**
 * Shared bits for the OI Lab. Calls/puts use validated categorical hues (orange / aqua,
 * CVD-safe in both themes), never the app's green/red, which stay reserved for profit/loss.
 * Every coloured mark is paired with a CE/PE label, so identity is never colour alone.
 */
const PALETTE = {
  dark: { call: '#d95926', put: '#199e70', spot: '#3987e5', grid: '#232f43', axis: '#6b7280', band: 'rgba(57,135,229,0.14)' },
  light: { call: '#eb6834', put: '#1baf7a', spot: '#2a78d6', grid: '#e5e7eb', axis: '#6b7280', band: 'rgba(42,120,214,0.12)' },
};

export function usePalette() {
  const { theme } = useTheme();
  return PALETTE[theme === 'light' ? 'light' : 'dark'];
}

export const isNum = (v) => v !== null && v !== undefined && Number.isFinite(Number(v));

export function fmtQty(v) {
  if (!isNum(v)) return '—';
  const a = Math.abs(v), s = v < 0 ? '-' : '';
  if (a >= 1e7) return `${s}${(a / 1e7).toFixed(2)} Cr`;
  if (a >= 1e5) return `${s}${(a / 1e5).toFixed(1)} L`;
  if (a >= 1e3) return `${s}${(a / 1e3).toFixed(1)} K`;
  return `${s}${a.toFixed(0)}`;
}
export const fmtSignedQty = (v) => (!isNum(v) ? '—' : `${v >= 0 ? '+' : ''}${fmtQty(v)}`);
export const fmtNum = (v, d = 2) => (!isNum(v) ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
export const fmtInt = (v) => (!isNum(v) ? '—' : Math.round(Number(v)).toLocaleString('en-IN'));
export const fmtPct = (v, d = 0) => (!isNum(v) ? '—' : `${(Number(v) * 100).toFixed(d)}%`);
export const fmtSignedPct = (v, d = 2) => (!isNum(v) ? '—' : `${v >= 0 ? '+' : ''}${Number(v).toFixed(d)}%`);
export const fmtRupee = (v) => (!isNum(v) ? '—' : `${v < 0 ? '−' : ''}₹${Math.round(Math.abs(Number(v))).toLocaleString('en-IN')}`);
export const tone = (v) => (!isNum(v) ? 'text-gray-400' : v > 0 ? 'text-green-400' : v < 0 ? 'text-red-400' : 'text-gray-300');

export function Tip({ text, children }) {
  const [open, setOpen] = useState(false);
  return (
    <span className="relative inline-flex items-center" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)} onBlur={() => setOpen(false)} tabIndex={0}>
      {children || <Info className="w-3.5 h-3.5 text-gray-500 hover:text-gray-300" />}
      {open && (
        <span className="absolute z-50 left-1/2 -translate-x-1/2 top-full mt-1.5 w-64 rounded-lg border border-surface-3 bg-surface-1 px-3 py-2 text-[11.5px] leading-snug text-gray-300 shadow-xl normal-case tracking-normal font-normal">
          {text}
        </span>
      )}
    </span>
  );
}

export function Section({ title, tip, right, children, className = '' }) {
  return (
    <div className={`card !p-4 ${className}`}>
      {(title || right) && (
        <div className="flex items-center justify-between gap-2 mb-3">
          <div className="flex items-center gap-1.5 text-[13px] font-semibold text-gray-100">
            {title}{tip && <Tip text={tip} />}
          </div>
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

export function Stat({ label, value, sub, valueClass = '', tip }) {
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-lg px-3 py-2 min-w-0">
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wider text-gray-500">{label}{tip && <Tip text={tip} />}</div>
      <div className={`mono text-[15px] font-semibold truncate ${valueClass || 'text-gray-100'}`}>{value}</div>
      {sub && <div className="text-[10.5px] text-gray-500 truncate">{sub}</div>}
    </div>
  );
}

/** Horizontal 0–100 meter. */
export function Meter({ value, color, height = 6 }) {
  const w = Math.max(0, Math.min(100, Number(value) || 0));
  return (
    <div className="w-full rounded-full bg-surface-3/70 overflow-hidden" style={{ height }}>
      <div className="h-full rounded-full" style={{ width: `${w}%`, background: color }} />
    </div>
  );
}

/** Probability pill that states how much the number can be trusted. */
export function GradeBadge({ grade }) {
  const map = {
    strong: ['badge-green', 'tested: strong'],
    useful: ['badge-blue', 'tested: useful'],
    weak: ['badge-yellow', 'tested: weak'],
    none: ['badge-red', 'tested: no edge'],
  };
  const [cls, label] = map[grade] || ['badge', 'untested'];
  return <span className={`${cls} !text-[10px] !px-1.5`}>{label}</span>;
}

export function Empty({ children }) {
  return <div className="text-[12.5px] text-gray-500 py-6 text-center">{children}</div>;
}

export const SIDE_LABEL = { ce: 'CE', pe: 'PE' };
