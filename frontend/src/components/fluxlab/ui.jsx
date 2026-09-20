import React from 'react';
import { AlertTriangle, Info } from 'lucide-react';

/** Shared display pieces for the Flux Strategy Test Lab. */

export const N = (v, d = 2) => (v == null || Number.isNaN(Number(v)) ? '—'
  : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
export const N0 = (v) => N(v, 0);
export const PCT = (v, d = 1) => (v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(d)}%`);
export const RS = (v) => (v == null ? '—' : `${Number(v) < 0 ? '−' : ''}₹${Math.abs(Number(v)).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`);
export const PTS = (v, d = 1) => (v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(d)} pts`);
export const tone = (v) => (v == null ? 'text-gray-400' : v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-gray-300');

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
    <label className="block">
      <div className="text-[10px] uppercase tracking-wider text-gray-500">{label}</div>
      {children}
      {hint && <div className="text-[10.5px] text-gray-500 mt-0.5">{hint}</div>}
    </label>
  );
}

export const input = 'input-field !py-1.5 w-full text-[12.5px]';

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

/** A statistics table over any of the engine's breakdowns. */
export function StatTable({ rows = [], keyLabel = 'Group', columns, empty = 'nothing to show' }) {
  const cols = columns || [
    ['trades', 'Trades'], ['win_rate', 'Win %', PCT], ['net_pnl', 'Net P&L', RS],
    ['avg_trade', 'Avg trade', RS], ['profit_factor', 'PF', (v) => N(v, 2)],
    ['avg_spot_mfe', 'Avg MFE', (v) => PTS(v)], ['avg_spot_mae', 'Avg MAE', (v) => PTS(v)],
    ['avg_hold_min', 'Hold', (v) => (v == null ? '—' : `${Math.round(v)}m`)],
  ];
  if (!rows.length) return <div className="py-6 text-center text-[12px] text-gray-500">{empty}</div>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px] min-w-[620px]">
        <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
          <th className="text-left px-2 py-1 font-medium">{keyLabel}</th>
          {cols.map(([k, l]) => <th key={k} className="text-right px-2 py-1 font-medium">{l}</th>)}
        </tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.key ?? i} className="border-b border-surface-3/40">
              <td className="px-2 py-1.5 text-gray-200">{r.key ?? r.variant ?? r.window ?? i + 1}</td>
              {cols.map(([k, , fmt]) => (
                <td key={k} className={`px-2 py-1.5 text-right mono ${k === 'net_pnl' ? tone(r[k]) : 'text-gray-300'}`}>
                  {fmt ? fmt(r[k]) : (r[k] ?? '—')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Equity and drawdown, drawn from the realised trade sequence. */
export function EquityCurve({ curve = [], height = 160 }) {
  if (curve.length < 2) return <div className="py-8 text-center text-[12px] text-gray-500">not enough trades to draw a curve</div>;
  const w = 720;
  const eq = curve.map((p) => p.equity);
  const dd = curve.map((p) => p.drawdown ?? 0);
  const lo = Math.min(...eq), hi = Math.max(...eq);
  const pad = (hi - lo) * 0.08 || 1;
  const y = (v) => height - ((v - (lo - pad)) / ((hi + pad) - (lo - pad))) * height;
  const x = (i) => (i / (curve.length - 1)) * w;
  const path = eq.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const ddMin = Math.min(...dd, -1);
  const ddH = 44;
  const ddPath = dd.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${(ddH - (v / ddMin) * ddH).toFixed(1)}`).join(' ');
  const up = eq[eq.length - 1] >= eq[0];
  return (
    <div className="space-y-1">
      <svg viewBox={`0 0 ${w} ${height}`} className="w-full" style={{ height }}>
        <path d={`${path} L${w},${height} L0,${height} Z`} fill={up ? '#10b98118' : '#ef444418'} />
        <path d={path} fill="none" stroke={up ? '#10b981' : '#ef4444'} strokeWidth="1.6" />
      </svg>
      <div className="text-[10px] uppercase tracking-wider text-gray-500">Drawdown</div>
      <svg viewBox={`0 0 ${w} ${ddH}`} className="w-full" style={{ height: ddH }}>
        <path d={`${ddPath} L${w},0 L0,0 Z`} fill="#ef444422" />
        <path d={ddPath} fill="none" stroke="#ef4444" strokeWidth="1.2" />
      </svg>
    </div>
  );
}

/** MFE against MAE, one dot per trade — where the stops and targets should live. */
export function MfeMaeScatter({ scatter = [], height = 220 }) {
  if (!scatter.length) return <div className="py-8 text-center text-[12px] text-gray-500">no trades to plot</div>;
  const w = 420;
  const maxX = Math.max(...scatter.map((s) => Math.abs(s.mae || 0)), 10);
  const maxY = Math.max(...scatter.map((s) => s.mfe || 0), 10);
  const x = (v) => (Math.abs(v) / maxX) * (w - 40) + 30;
  const y = (v) => height - 24 - (v / maxY) * (height - 40);
  return (
    <svg viewBox={`0 0 ${w} ${height}`} className="w-full" style={{ height }}>
      <line x1={30} y1={height - 24} x2={w - 6} y2={height - 24} stroke="#ffffff22" />
      <line x1={30} y1={10} x2={30} y2={height - 24} stroke="#ffffff22" />
      {scatter.map((s, i) => (
        <circle key={i} cx={x(s.mae)} cy={y(s.mfe)} r={2.4}
          fill={s.pnl > 0 ? '#10b981' : '#ef4444'} opacity="0.7">
          <title>{`MFE +${s.mfe} pts · MAE ${s.mae} pts · ${s.reason} · ₹${s.pnl}`}</title>
        </circle>
      ))}
      <text x={w / 2} y={height - 6} fontSize="9" fill="#6b7280" textAnchor="middle">adverse move before exit (index points)</text>
      <text x={8} y={16} fontSize="9" fill="#6b7280">favourable (pts)</text>
    </svg>
  );
}
