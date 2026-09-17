import React, { useMemo, useState } from 'react';
import { Shield, TrendingUp, TrendingDown, Swords, Target, Activity } from 'lucide-react';
import {
  usePalette, fmtQty, fmtSignedQty, fmtNum, fmtInt, fmtPct, fmtSignedPct, tone, isNum,
  Section, Stat, Meter, GradeBadge, Tip, Empty,
} from './ui';

/**
 * X-Ray tab — who holds each strike, where the walls are, and the plain-English read.
 */

const WINNER = {
  PUT_WRITERS: { label: 'Put writers', cls: 'text-emerald-400 bg-emerald-500/10 border-emerald-500/25' },
  CALL_WRITERS: { label: 'Call writers', cls: 'text-orange-400 bg-orange-500/10 border-orange-500/25' },
  BALANCED: { label: 'Balanced', cls: 'text-gray-400 bg-surface-3/40 border-surface-3' },
};
const ROLE = {
  SUPPORT: 'Support', RESISTANCE: 'Resistance', BATTLEGROUND: 'ATM battle', CONTESTED: 'Contested',
  BULLISH_WRITERS: 'Puts above spot', BEARISH_WRITERS: 'Calls below spot',
};
const BUILDUP_CLS = {
  'Long Buildup': 'text-green-400', 'Short Covering': 'text-green-300',
  'Short Buildup': 'text-red-400', 'Long Unwinding': 'text-red-300',
};

function BiasBar({ score }) {
  const pal = usePalette();
  const pos = 50 + Math.max(-100, Math.min(100, score || 0)) / 2;
  return (
    <div className="relative h-3 rounded-full overflow-hidden bg-surface-3/60">
      <div className="absolute inset-y-0 left-0 w-1/2" style={{ background: `linear-gradient(90deg, ${pal.call}55, transparent)` }} />
      <div className="absolute inset-y-0 right-0 w-1/2" style={{ background: `linear-gradient(270deg, ${pal.put}55, transparent)` }} />
      <div className="absolute inset-y-0 left-1/2 w-px bg-gray-500/60" />
      <div className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-3.5 h-3.5 rounded-full border-2 border-white bg-surface-0 shadow"
        style={{ left: `${pos}%` }} />
    </div>
  );
}

function OddsTile({ label, p, grade, strike }) {
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-lg px-2.5 py-2 min-w-0">
      <div className="text-[10px] uppercase tracking-wider text-gray-500 truncate" title={`${label} holds until the close`}>{label}</div>
      <div className="flex items-baseline gap-1.5">
        <span className="mono text-xl font-semibold text-gray-100">{isNum(p) ? fmtPct(p) : '—'}</span>
        {strike && <span className="mono text-[11px] text-gray-400">{strike}</span>}
      </div>
      <GradeBadge grade={grade} />
    </div>
  );
}

export function Verdict({ snap }) {
  const A = snap.analysis;
  const probs = snap.model?.prediction?.probabilities;
  const walls = snap.model?.walls;
  const hist = snap.model?.history;
  return (
    <div className="card !p-4 grid gap-4 lg:grid-cols-[minmax(0,380px)_1fr]">
      <div className="space-y-3">
        <div>
          <div className="flex items-center justify-between">
            <span className="text-[11px] uppercase tracking-wider text-gray-500 flex items-center gap-1">
              Who is in control <Tip text="Positioning bias from writers' OI, fresh writing, premium momentum, order book and buildups. It DESCRIBES the current balance of power. Tested on 3 years of NIFTY, OI positioning did not forecast direction to the close — use the walls and odds for decisions." />
            </span>
            <span className={`text-sm font-bold ${A.bias.score >= 12 ? 'text-emerald-400' : A.bias.score <= -12 ? 'text-orange-400' : 'text-gray-300'}`}>
              {A.bias.label} <span className="mono text-xs text-gray-500">{A.bias.score > 0 ? '+' : ''}{A.bias.score}</span>
            </span>
          </div>
          <div className="mt-2"><BiasBar score={A.bias.score} /></div>
          <div className="flex justify-between text-[10px] text-gray-500 mt-1"><span>Call writers</span><span>Put writers</span></div>
        </div>
        <div className="rounded-lg border border-brand-500/25 bg-brand-500/5 px-3 py-2">
          <div className="text-[12.5px] font-semibold text-brand-400">{A.regime.label}</div>
          <div className="text-[11.5px] text-gray-400 mt-0.5">{A.regime.text}</div>
        </div>
        {probs ? (
          <div className="space-y-1">
          <div className="text-[10.5px] text-gray-500">Tested odds each holds until the close</div>
          <div className="grid grid-cols-3 gap-2">
            <OddsTile label="Call wall" p={probs.ce_held?.p} grade={probs.ce_held?.grade} strike={walls?.ce_wall ? fmtInt(walls.ce_wall) : null} />
            <OddsTile label="Put wall" p={probs.pe_held?.p} grade={probs.pe_held?.grade} strike={walls?.pe_wall ? fmtInt(walls.pe_wall) : null} />
            <OddsTile label="Range day" p={probs.inside?.p} grade={probs.inside?.grade} />
          </div>
          </div>
        ) : (
          <div className="text-[11.5px] text-gray-500">
            {hist?.status === 'building' ? `Loading 3-year history for tested odds… ${hist.done || 0}/${hist.total || '?'} months`
              : hist?.status === 'error' ? `History unavailable: ${hist.error}` : 'Tested odds appear once history is loaded.'}
          </div>
        )}
        {snap.model?.note && <div className="text-[10.5px] text-amber-400/90">{snap.model.note}</div>}
      </div>
      <div>
        <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-2">The read, in plain words</div>
        <ul className="space-y-1.5">
          {A.narrative.map((t, i) => (
            <li key={i} className="flex gap-2 text-[12.5px] leading-snug text-gray-300">
              <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-brand-500 shrink-0" />
              <span>{t}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export function KpiStrip({ snap }) {
  const A = snap.analysis;
  const T = A.totals;
  const em = A.expected_move;
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 gap-2">
      <Stat label={snap.label} value={fmtNum(snap.spot)} valueClass="text-white"
        sub={<span className={tone(snap.spot_change)}>{isNum(snap.spot_change) ? `${snap.spot_change > 0 ? '+' : ''}${fmtNum(snap.spot_change)} (${fmtSignedPct(snap.spot_change_pct)})` : '—'}</span>} />
      <Stat label="ATM · Expiry" value={fmtInt(snap.atm)} sub={`${snap.expiry} · ${snap.dte === 0 ? 'expiry day' : `${snap.dte}d left`}`} />
      <Stat label="PCR (OI)" value={T.pcr_oi ?? '—'} tip="Put OI ÷ call OI across the strikes shown. Above 1: more put writing (supportive). Below 1: more call writing (capping)."
        sub={`ΔOI ${T.pcr_chg ?? '—'} · Vol ${T.pcr_vol ?? '—'}`} valueClass={T.pcr_oi >= 1 ? 'text-emerald-400' : 'text-orange-400'} />
      <Stat label="Max pain" value={fmtInt(A.max_pain)} sub={isNum(A.max_pain) ? `${A.max_pain - snap.spot > 0 ? '+' : ''}${fmtInt(A.max_pain - snap.spot)} pts from spot` : ''}
        tip="The expiry price at which option buyers, in total, lose the most. Pull matters most on expiry day." />
      <Stat label="ATM straddle" value={fmtNum(snap.straddle)} sub={em.straddle_lower ? `${fmtInt(em.straddle_lower)} – ${fmtInt(em.straddle_upper)}` : ''}
        tip="Call + put at the money. The market's price for the move until expiry." />
      <Stat label="1σ rest of day" value={isNum(em.intraday_sigma_pts) ? `±${fmtInt(em.intraday_sigma_pts)}` : '—'}
        sub={em.lower_1s ? `${fmtInt(em.lower_1s)} – ${fmtInt(em.upper_1s)}` : ''} tip="How far spot typically moves by the close (≈68% of days stay inside), from ATM implied volatility and minutes left." />
      <Stat label="ATM IV · VIX" value={isNum(snap.atm_iv) ? `${fmtNum(snap.atm_iv, 1)}%` : '—'} sub={`India VIX ${fmtNum(snap.vix, 2)}`} />
      <Stat label="Fresh OI today" value={<span className="text-orange-400">CE {fmtSignedQty(T.ce_oi_chg)}</span>}
        sub={<span className="mono text-emerald-400">PE {fmtSignedQty(T.pe_oi_chg)}</span>} tip="Open interest added (+) or cut (−) since the previous close, strikes shown." />
    </div>
  );
}

/* ── Battle map: butterfly of PE (left) vs CE (right) OI per strike ── */
export function BattleMap({ snap, onPick }) {
  const pal = usePalette();
  const [hover, setHover] = useState(null);
  const rows = useMemo(() => [...snap.rows].sort((a, b) => b.strike - a.strike), [snap.rows]);
  const battle = useMemo(() => Object.fromEntries(snap.analysis.battle.map((b) => [b.strike, b])), [snap.analysis.battle]);
  const maxOi = Math.max(1, ...rows.flatMap((r) => [r.ce?.oi || 0, r.pe?.oi || 0]));
  const sup = snap.analysis.zones.support?.[0];
  const res = snap.analysis.zones.resistance?.[0];
  const magnet = snap.analysis.gamma?.magnet;
  const inZone = (k) => (sup && k >= sup.low && k <= sup.high ? 'support' : res && k >= res.low && k <= res.high ? 'resistance' : null);
  const spotIdx = rows.findIndex((r) => r.strike < snap.spot);

  const bar = (cell, side) => {
    const color = side === 'ce' ? pal.call : pal.put;
    const oi = cell?.oi || 0;
    const chg = cell?.oi_chg ?? cell?.oi_since_open;
    const w = (oi / maxOi) * 100;
    const add = isNum(chg) && chg > 0 ? (Math.min(chg, oi) / maxOi) * 100 : 0;
    const cut = isNum(chg) && chg < 0 ? (Math.min(-chg, maxOi) / maxOi) * 100 : 0;
    const dir = side === 'ce' ? 'left-0' : 'right-0';
    return (
      <div className="relative h-5 flex-1">
        <div className={`absolute top-0.5 bottom-0.5 ${dir} rounded`} style={{ width: `${w}%`, background: color, opacity: 0.35 }} />
        {add > 0 && <div className={`absolute top-0.5 bottom-0.5 ${dir} rounded`} style={{ width: `${add}%`, background: color }} />}
        {cut > 0 && <div className={`absolute top-1 bottom-1 ${dir} rounded border border-dashed`} style={{ width: `${cut}%`, borderColor: color, ...(side === 'ce' ? { left: `${w}%` } : { right: `${w}%` }) }} />}
      </div>
    );
  };

  return (
    <Section title={<span className="flex items-center gap-1.5"><Swords className="w-4 h-4 text-brand-400" />Writer battle map</span>}
      tip="Each row is a strike. Left: put OI (put writers = support). Right: call OI (call writers = resistance). Solid = OI added today, faded = OI already there, dashed outline = OI cut today. Click a row to dissect both contracts."
      right={<div className="hidden sm:flex items-center gap-3 text-[10.5px] text-gray-400">
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm" style={{ background: pal.put }} />PE OI</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm" style={{ background: pal.call }} />CE OI</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-gray-400/40" />held</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm bg-gray-300" />added today</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded-sm border border-dashed border-gray-400" />cut today</span>
      </div>}>
      <div className="grid grid-cols-[1fr_auto_1fr] text-[10px] uppercase tracking-wider text-gray-500 mb-1 px-1">
        <span>← Put writers (support)</span><span className="text-center w-[132px]">Strike</span><span className="text-right">Call writers (resistance) →</span>
      </div>
      <div className="relative" onMouseLeave={() => setHover(null)}>
        {rows.map((r, i) => {
          const b = battle[r.strike];
          const z = inZone(r.strike);
          const w = WINNER[b?.winner] || WINNER.BALANCED;
          return (
            <React.Fragment key={r.strike}>
              {i === spotIdx && (
                <div className="flex items-center gap-2 my-0.5">
                  <div className="flex-1 h-px" style={{ background: pal.spot }} />
                  <span className="mono text-[10.5px] font-semibold px-1.5 rounded" style={{ color: pal.spot }}>SPOT {fmtNum(snap.spot)}</span>
                  <div className="flex-1 h-px" style={{ background: pal.spot }} />
                </div>
              )}
              <button type="button" onClick={() => onPick?.(r)} onMouseEnter={() => setHover(r.strike)}
                className={`w-full grid grid-cols-[1fr_auto_1fr] items-center gap-2 px-1 rounded transition-colors
                  ${hover === r.strike ? 'bg-surface-3/50' : z === 'support' ? 'bg-emerald-500/[0.06]' : z === 'resistance' ? 'bg-orange-500/[0.06]' : ''}`}>
                {bar(r.pe, 'pe')}
                <div className="w-[132px] flex items-center justify-center gap-1.5">
                  <span className={`mono text-[12px] ${r.strike === snap.atm ? 'text-brand-400 font-bold' : 'text-gray-200'}`}>{fmtInt(r.strike)}</span>
                  {r.strike === magnet && <Tip text="Gamma magnet — largest gamma × OI. Price tends to stick here, most of all on expiry day."><Target className="w-3 h-3 text-brand-400" /></Tip>}
                  <span className={`text-[9.5px] px-1 rounded border ${w.cls}`}>{w.label.split(' ')[0]}</span>
                </div>
                {bar(r.ce, 'ce')}
              </button>
              {hover === r.strike && b && (
                <div className="grid grid-cols-2 gap-3 text-[11px] bg-surface-2 border border-surface-3 rounded-lg px-3 py-2 my-1">
                  {['pe', 'ce'].map((s) => (
                    <div key={s} className={s === 'ce' ? 'text-right' : ''}>
                      <div className="font-semibold" style={{ color: s === 'ce' ? pal.call : pal.put }}>{s.toUpperCase()} {fmtInt(r.strike)}</div>
                      <div className="text-gray-300">OI {fmtQty(r[s]?.oi)} · today {fmtSignedQty(r[s]?.oi_chg ?? r[s]?.oi_since_open)}</div>
                      <div className="text-gray-400">LTP {fmtNum(r[s]?.ltp)} · writer conviction {b[`${s}_conviction`]?.score}</div>
                      {r[s]?.buildup && <div className={BUILDUP_CLS[r[s].buildup]}>{r[s].buildup}</div>}
                    </div>
                  ))}
                  <div className="col-span-2 text-gray-400 border-t border-surface-3 pt-1">
                    <span className="text-gray-200 font-medium">{ROLE[b.role]}:</span> {b.note}
                  </div>
                </div>
              )}
            </React.Fragment>
          );
        })}
      </div>
    </Section>
  );
}

function ZoneCard({ z, kind, odds, idx }) {
  const pal = usePalette();
  const color = kind === 'support' ? pal.put : pal.call;
  const Icon = kind === 'support' ? Shield : TrendingDown;
  return (
    <div className="rounded-lg border border-surface-3 bg-surface-2 px-3 py-2.5 space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <Icon className="w-4 h-4" style={{ color }} />
          <span className="mono text-[15px] font-bold text-gray-100">{fmtInt(z.anchor)}</span>
          <span className="text-[11px] text-gray-400">{z.label}</span>
        </div>
        <span className="text-[11px] text-gray-400 whitespace-nowrap">{fmtInt(z.distance_pts)} pts away</span>
      </div>
      <div className="text-[10.5px] text-gray-500">
        {idx > 0 ? 'Second line · ' : 'Main wall · '}{z.low !== z.high ? `zone ${fmtInt(z.low)}–${fmtInt(z.high)}` : 'single strike'}
      </div>
      <Meter value={z.strength} color={color} />
      {odds && (
        <div className="text-[11.5px] text-gray-300">
          Tested odds it holds to the close: <span className="mono font-semibold text-white">{fmtPct(odds.p)}</span> <GradeBadge grade={odds.grade} />
        </div>
      )}
      <ul className="text-[11.5px] text-gray-400 space-y-0.5">
        {z.reasons.map((t, i) => <li key={i}>• {t}</li>)}
      </ul>
    </div>
  );
}

export function Zones({ snap }) {
  const Z = snap.analysis.zones;
  const probs = snap.model?.prediction?.probabilities;
  const walls = snap.model?.walls || {};
  return (
    <Section title={<span className="flex items-center gap-1.5"><Activity className="w-4 h-4 text-brand-400" />Accumulation zones</span>}
      tip="Where writers have stacked OI and are still adding. Strength combines size, today's fresh writing and share of that strike's OI, and is cut when writers cover from today's OI peak.">
      <div className="grid md:grid-cols-2 gap-3">
        <div className="space-y-2">
          <div className="text-[11px] uppercase tracking-wider text-gray-500 flex items-center gap-1"><TrendingDown className="w-3.5 h-3.5" />Resistance — call writers</div>
          {(Z.resistance || []).length ? Z.resistance.map((z, i) => (
            <ZoneCard key={z.anchor} z={z} kind="resistance" idx={i} odds={probs && z.anchor === walls.ce_wall ? probs.ce_held : null} />
          )) : <Empty>No call-writer wall above spot.</Empty>}
        </div>
        <div className="space-y-2">
          <div className="text-[11px] uppercase tracking-wider text-gray-500 flex items-center gap-1"><TrendingUp className="w-3.5 h-3.5" />Support — put writers</div>
          {(Z.support || []).length ? Z.support.map((z, i) => (
            <ZoneCard key={z.anchor} z={z} kind="support" idx={i} odds={probs && z.anchor === walls.pe_wall ? probs.pe_held : null} />
          )) : <Empty>No put-writer wall below spot.</Empty>}
        </div>
      </div>
      <div className="mt-3 rounded-lg bg-surface-2 border border-surface-3 px-3 py-2 text-[12px] text-gray-300">
        <span className="font-semibold text-gray-100">Buyer vs seller fight near the money: </span>{snap.analysis.flow.text}
      </div>
    </Section>
  );
}

/* ── Strike dissection table ── */
function Side({ c, side, mirror }) {
  const cells = [
    <td key="oi" className="px-2 py-1.5 text-right mono">{fmtQty(c?.oi)}</td>,
    <td key="chg" className={`px-2 py-1.5 text-right mono ${tone(c?.oi_chg ?? c?.oi_since_open)}`}>{fmtSignedQty(c?.oi_chg ?? c?.oi_since_open)}</td>,
    <td key="bu" className={`px-2 py-1.5 text-[11px] whitespace-nowrap ${BUILDUP_CLS[c?.buildup] || 'text-gray-600'}`}>{c?.buildup || '—'}</td>,
    <td key="vol" className="px-2 py-1.5 text-right mono text-gray-400">{fmtQty(c?.volume)}</td>,
    <td key="iv" className="px-2 py-1.5 text-right mono text-gray-400">{isNum(c?.iv) ? fmtNum(c.iv, 1) : '—'}</td>,
    <td key="d" className="px-2 py-1.5 text-right mono text-gray-400">{isNum(c?.delta) ? fmtNum(c.delta, 2) : '—'}</td>,
    <td key="th" className="px-2 py-1.5 text-right mono text-gray-400">{isNum(c?.theta) ? fmtNum(c.theta, 1) : '—'}</td>,
    <td key="bk" className={`px-2 py-1.5 text-right mono ${tone(c?.book_imbalance)}`}>{isNum(c?.book_imbalance) ? `${c.book_imbalance > 0 ? '+' : ''}${Math.round(c.book_imbalance * 100)}%` : '—'}</td>,
    <td key="ltp" className="px-2 py-1.5 text-right mono">
      <div className="text-gray-100 font-semibold">{fmtNum(c?.ltp)}</div>
      <div className={`text-[10px] ${tone(c?.change_pct)}`}>{fmtSignedPct(c?.change_pct, 1)}</div>
    </td>,
  ];
  return mirror ? cells.reverse() : cells;
}

export function StrikeTable({ snap, onPick }) {
  const battle = useMemo(() => Object.fromEntries(snap.analysis.battle.map((b) => [b.strike, b])), [snap.analysis.battle]);
  const rows = [...snap.rows].sort((a, b) => b.strike - a.strike);
  const heads = ['OI', 'Δ OI', 'Buildup', 'Volume', 'IV', 'Delta', 'Theta', 'Bid/Ask', 'LTP'];
  return (
    <Section title="Strike dissection (ATM ± window)" tip="Every contract, both sides. Δ OI is versus the previous close (or since 09:20 until that loads). Bid/Ask = pending buy vs sell quantity imbalance. Click a row for depth, greeks and the intraday OI path.">
      <div className="overflow-x-auto -mx-1">
        <table className="w-full min-w-[1180px] text-[12px] whitespace-nowrap">
          <thead>
            <tr className="text-[10px] uppercase tracking-wider">
              <th colSpan={9} className="text-left px-2 pb-1 text-orange-400">Calls (CE)</th>
              <th className="px-2 pb-1 text-gray-400">Strike · winner</th>
              <th colSpan={9} className="text-right px-2 pb-1 text-emerald-400">Puts (PE)</th>
            </tr>
            <tr className="text-[10px] text-gray-500 border-b border-surface-3">
              {[...heads].reverse().map((h) => <th key={`c${h}`} className="px-2 py-1 text-right font-medium">{h}</th>)}
              <th className="px-2 py-1 font-medium" />
              {heads.map((h) => <th key={`p${h}`} className="px-2 py-1 text-right font-medium">{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const b = battle[r.strike];
              const w = WINNER[b?.winner] || WINNER.BALANCED;
              const atm = r.strike === snap.atm;
              return (
                <tr key={r.strike} onClick={() => onPick?.(r)}
                  className={`border-b border-surface-3/40 cursor-pointer hover:bg-surface-3/40 ${atm ? 'bg-brand-500/[0.07]' : ''}`}>
                  <Side c={r.ce} side="ce" mirror />
                  <td className="px-2 py-1.5 text-center whitespace-nowrap">
                    <div className={`mono font-bold ${atm ? 'text-brand-400' : 'text-gray-100'}`}>{fmtInt(r.strike)}</div>
                    <span className={`text-[9.5px] px-1.5 rounded border ${w.cls}`} title={b?.note}>{w.label}{b?.edge >= 10 ? ` +${Math.round(b.edge)}` : ''}</span>
                  </td>
                  <Side c={r.pe} side="pe" />
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Section>
  );
}
