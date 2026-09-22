import React, { useState } from 'react';
import { AlertTriangle, Info, ChevronDown, ChevronRight } from 'lucide-react';

/** Shared display pieces for the Flux Strategy Test Lab (failed-breakout iron fly). */

export const N = (v, d = 2) => (v == null || Number.isNaN(Number(v)) ? '—'
  : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
export const N0 = (v) => N(v, 0);
export const PCT = (v, d = 1) => (v == null ? '—' : `${Number(v).toFixed(d)}%`);
export const RS = (v) => (v == null ? '—' : `${Number(v) < 0 ? '−' : Number(v) > 0 ? '+' : ''}₹${Math.abs(Number(v)).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`);
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
    <label className="block">
      <div className="text-[10px] uppercase tracking-wider text-gray-500">{label}</div>
      {children}
      {hint && <div className="text-[10.5px] text-gray-500 mt-0.5">{hint}</div>}
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
  EOD: 'bg-surface-3 text-gray-300', 'EOD-LATE': 'bg-amber-500/15 text-amber-500',
};
export function Reason({ r }) {
  return <span className={`px-1.5 py-px rounded text-[10.5px] font-semibold ${REASON_CLS[r] || 'bg-surface-3 text-gray-300'}`}>{r || 'OPEN'}</span>;
}

/** The four legs of a fly with their fills (and live prices when present). */
export function Legs({ legs = [], live = false }) {
  return (
    <table className="w-full text-[11.5px]">
      <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500">
        <th className="text-left py-0.5 font-medium">Leg</th>
        <th className="text-left py-0.5 font-medium">Contract</th>
        <th className="text-right py-0.5 font-medium">Entry</th>
        {live ? <><th className="text-right py-0.5 font-medium">Bid</th><th className="text-right py-0.5 font-medium">Ask</th></>
          : <th className="text-right py-0.5 font-medium">Exit</th>}
      </tr></thead>
      <tbody>
        {legs.map((l, i) => (
          <tr key={i} className="border-t border-surface-3/40">
            <td className="py-1">
              <span className={`px-1 rounded text-[10px] font-bold ${l.q < 0 ? 'bg-red-500/15 text-red-400' : 'bg-emerald-500/15 text-emerald-400'}`}>
                {l.q < 0 ? 'SELL' : 'BUY'}
              </span>
            </td>
            <td className="py-1 mono text-gray-300">{l.symbol || `${l.type} ${N0(l.strike)}`}</td>
            <td className="py-1 mono text-right text-gray-300">{N(l.entry)}</td>
            {live ? <><td className="py-1 mono text-right text-gray-400">{N(l.bid)}</td><td className="py-1 mono text-right text-gray-400">{N(l.ask)}</td></>
              : <td className="py-1 mono text-right text-gray-300">{N(l.exit)}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** One trade per row; click to see the legs and exactly how the signal formed. */
export function TradeTable({ trades = [], empty = 'No trades yet.' }) {
  const [open, setOpen] = useState(null);
  if (!trades.length) return <div className="py-6 text-center text-[12px] text-gray-500">{empty}</div>;
  return (
    <div className="overflow-x-auto max-h-[560px] overflow-y-auto">
      <table className="w-full text-[12px] min-w-[760px]">
        <thead className="sticky top-0 bg-surface-1"><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
          {['', 'Date', 'Signal', 'Entry', 'Exit', 'Reason', 'ATM', 'Credit', 'Close cost', 'Lots', 'Net P&L'].map((h) => (
            <th key={h} className={`px-2 py-1 font-medium ${['Date', 'Signal', 'Entry', 'Exit', 'Reason', ''].includes(h) ? 'text-left' : 'text-right'}`}>{h}</th>))}
        </tr></thead>
        <tbody>
          {trades.map((t, i) => {
            const k = t.id ?? `${t.date}-${i}`;
            const isOpen = open === k;
            return (
              <React.Fragment key={k}>
                <tr onClick={() => setOpen(isOpen ? null : k)} className="border-b border-surface-3/40 cursor-pointer hover:bg-surface-2/60">
                  <td className="px-2 py-1 text-gray-500">{isOpen ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}</td>
                  <td className="px-2 py-1 mono text-gray-300">{t.date}</td>
                  <td className="px-2 py-1 mono text-gray-400">{t.signal_time}</td>
                  <td className="px-2 py-1 mono text-gray-400">{t.entry_time}</td>
                  <td className="px-2 py-1 mono text-gray-400">{t.exit_time || '—'}</td>
                  <td className="px-2 py-1"><Reason r={t.exit_reason} /></td>
                  <td className="px-2 py-1 mono text-right text-gray-300">{N0(t.atm)}</td>
                  <td className="px-2 py-1 mono text-right text-gray-300">{N(t.credit)}</td>
                  <td className="px-2 py-1 mono text-right text-gray-300">{N(t.debit)}</td>
                  <td className="px-2 py-1 mono text-right text-gray-400">{t.lots}</td>
                  <td className={`px-2 py-1 mono text-right font-semibold ${tone(t.pnl ?? t.unrealised)}`}>{RS(t.pnl ?? t.unrealised)}</td>
                </tr>
                {isOpen && (
                  <tr className="bg-surface-2/30"><td colSpan={11} className="px-3 py-2">
                    <div className="grid md:grid-cols-2 gap-4">
                      <div>
                        <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Legs · expiry {t.expiry} ({t.dte}d) · qty {t.qty}</div>
                        <Legs legs={t.legs} />
                        <div className="text-[11px] text-gray-500 mt-1">
                          Credit and close cost are premium points per unit. Charges {RS(-(t.charges || 0))}.
                        </div>
                      </div>
                      <div>
                        <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">How the signal formed</div>
                        {(t.events || []).map((e, j) => (
                          <div key={j} className="text-[11.5px] text-gray-300 py-0.5">{typeof e === 'string' ? e : `${e.time} ${e.text}`}</div>
                        ))}
                        <div className="text-[11px] text-gray-500 mt-1">
                          NIFTY {N(t.spot_entry)} at entry{t.spot_exit ? ` → ${N(t.spot_exit)} at exit` : ''}.
                        </div>
                      </div>
                    </div>
                  </td></tr>
                )}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Month-by-month P&L as coloured tiles — the view the strategy is judged on. */
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

/** Cumulative P&L drawn from the trade sequence. */
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
