import React from 'react';
import { Crosshair, AlertTriangle, PauseCircle, History, Compass, CheckCircle2, XCircle, ShieldCheck } from 'lucide-react';
import { usePalette, fmtNum, fmtInt, fmtPct, fmtRupee, isNum, Section, GradeBadge, Tip, Empty } from './ui';

/**
 * Entry zones — priced setups from the live walls, scored on validated odds, plus
 * the model read (how far spot can still travel) and the most similar past sessions.
 */

const STATUS = {
  ACTIVE: ['badge-green', 'In zone now'],
  WAIT: ['badge-blue', 'Waiting'],
  EXTENDED: ['badge-yellow', 'Missed — extended'],
  INVALID: ['badge-red', 'Invalidated'],
};

function Validation({ v }) {
  if (!v) return null;
  return (
    <Section title={<span className="flex items-center gap-1.5"><ShieldCheck className="w-4 h-4 text-brand-400" />How these setups did on unseen sessions</span>}
      tip="The whole live engine was replayed minute-by-minute on past sessions the models never saw, and each in-zone setup was followed to its target, stop or the close.">
      <div className="text-[11.5px] text-gray-500 mb-2">{v.sample} · {v.setups_in_zone} setups in their entry zone.</div>
      <ul className="text-[12px] text-gray-300 space-y-1">
        <li>• <span className="text-gray-100 font-medium">Odds:</span> {v.odds}</li>
        <li>• <span className="text-gray-100 font-medium">Edge score:</span> {v.score}</li>
        <li>• <span className="text-gray-100 font-medium">Range odds:</span> {v.range}</li>
      </ul>
      <div className="mt-2 text-[12px] text-amber-300/90">{v.takeaway}</div>
    </Section>
  );
}

function LevelMap({ snap }) {
  const pal = usePalette();
  const S = snap.setups.setups.filter((s) => s.id !== 'RANGE_WRITE');
  const levels = S.flatMap((s) => [...s.entry_zone, s.stop, ...(s.targets || [])]).concat([snap.spot]).filter(isNum);
  if (!levels.length) return null;
  const lo = Math.min(...levels), hi = Math.max(...levels);
  const pad = (hi - lo) * 0.04 || 10;
  const x = (v) => `${((v - (lo - pad)) / (hi - lo + 2 * pad)) * 100}%`;
  const sup = snap.analysis.zones.support?.[0], res = snap.analysis.zones.resistance?.[0];
  return (
    <Section title="Entry map" tip="Every setup on one price scale. Bar = entry zone, red tick = stop, green ticks = targets. Blue line = spot now. Shaded columns = support (aqua) and resistance (orange) walls.">
      <div className="relative">
        <div className="absolute inset-y-0 w-px z-10" style={{ left: x(snap.spot), background: pal.spot }}>
          <span className="absolute -top-4 -translate-x-1/2 mono text-[10px] font-semibold whitespace-nowrap" style={{ color: pal.spot }}>spot {fmtInt(snap.spot)}</span>
        </div>
        {sup && <div className="absolute inset-y-0" style={{ left: x(sup.low - 5), width: `calc(${x(sup.high + 5)} - ${x(sup.low - 5)})`, background: `${pal.put}22` }} />}
        {res && <div className="absolute inset-y-0" style={{ left: x(res.low - 5), width: `calc(${x(res.high + 5)} - ${x(res.low - 5)})`, background: `${pal.call}22` }} />}
        <div className="space-y-2 pt-5 relative">
          {S.map((s) => (
            <div key={s.id} className="grid grid-cols-[180px_1fr] items-center gap-2">
              <span className={`text-[11px] truncate ${s.id === snap.setups.best ? 'text-white font-semibold' : 'text-gray-400'}`}>{s.title}</span>
              <div className="relative h-5">
                <div className="absolute inset-y-[7px] left-0 right-0 bg-surface-3/40 rounded" />
                <div className="absolute inset-y-1 rounded" style={{ left: x(s.entry_zone[0]), width: `calc(${x(s.entry_zone[1])} - ${x(s.entry_zone[0])})`, background: s.direction === 'long' ? pal.put : pal.call, opacity: s.status === 'INVALID' ? 0.3 : 0.9 }} />
                <div className="absolute inset-y-0 w-0.5 bg-red-500" style={{ left: x(s.stop) }} title={`Stop ${fmtInt(s.stop)}`} />
                {(s.targets || []).map((t, i) => <div key={i} className="absolute inset-y-0 w-0.5 bg-green-500" style={{ left: x(t), opacity: i ? 0.55 : 1 }} title={`Target ${i + 1} ${fmtInt(t)}`} />)}
              </div>
            </div>
          ))}
        </div>
        <div className="flex justify-between text-[10px] text-gray-500 mono mt-1 pl-[188px]"><span>{fmtInt(lo)}</span><span>{fmtInt(hi)}</span></div>
      </div>
    </Section>
  );
}

function Odds({ label, p, tip }) {
  return (
    <div className="text-center">
      <div className="text-[10px] uppercase tracking-wider text-gray-500 flex items-center justify-center gap-1">{label}{tip && <Tip text={tip} />}</div>
      <div className="mono text-[14px] font-semibold text-gray-100">{isNum(p) ? fmtPct(p) : '—'}</div>
    </div>
  );
}

function SetupCard({ s, best, lot }) {
  const pal = usePalette();
  const [cls, label] = STATUS[s.status] || ['badge', s.status];
  const color = s.direction === 'long' ? pal.put : s.direction === 'short' ? pal.call : pal.spot;
  const c = s.contract || {};
  const lotSize = c.lot_size || lot || 1;
  const pr = s.premium || {};
  return (
    <div className={`rounded-xl border px-4 py-3 space-y-3 bg-surface-1 ${best ? 'border-brand-500/60 shadow-lg shadow-brand-900/20' : 'border-surface-3'}`}>
      <div className="flex items-start justify-between gap-2">
        <div>
          {best && <div className="text-[10px] uppercase tracking-wider text-brand-400 font-semibold mb-0.5">★ Best-placed setup now</div>}
          <div className="text-[14px] font-semibold text-gray-100 flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full" style={{ background: color }} />{s.title}
          </div>
          <div className="text-[11.5px] text-gray-400">{s.trigger}</div>
        </div>
        <div className="text-right shrink-0">
          <span className={cls}>{label}{s.status === 'WAIT' && isNum(s.distance_pts) ? ` · ${fmtInt(s.distance_pts)} pts` : ''}</span>
        </div>
      </div>

      {s.id !== 'RANGE_WRITE' ? (
        <>
          <div className="grid grid-cols-4 gap-2 text-[12px]">
            <div><div className="text-[10px] uppercase tracking-wider text-gray-500">Entry (spot)</div><div className="mono text-gray-100">{fmtInt(s.entry_zone[0])}–{fmtInt(s.entry_zone[1])}</div></div>
            <div><div className="text-[10px] uppercase tracking-wider text-gray-500">Stop</div><div className="mono text-red-400">{fmtInt(s.stop)}</div></div>
            <div><div className="text-[10px] uppercase tracking-wider text-gray-500">Targets</div><div className="mono text-green-400">{s.targets.map(fmtInt).join(' / ')}</div></div>
            <div><div className="text-[10px] uppercase tracking-wider text-gray-500">Reward : risk</div><div className="mono text-gray-100">{isNum(s.rr) ? `${fmtNum(s.rr, 2)} : 1` : '—'}</div></div>
          </div>
          <div className="grid grid-cols-4 gap-2 rounded-lg bg-surface-2 border border-surface-3 py-2">
            <Odds label="Target" p={s.p_target} tip="Odds spot travels from the entry zone to target 1 before the close, from the tested reach model (or implied volatility without history)." />
            <Odds label="Stop" p={s.p_stop} tip="Odds spot travels from the entry zone to the stop before the close. Target and stop odds can both be low: then time decay is the likely outcome." />
            <Odds label="Fills today" p={s.p_fill} tip="Odds price reaches the entry zone at all before the close." />
            <div className="text-center">
              <div className="text-[10px] uppercase tracking-wider text-gray-500 flex items-center justify-center gap-1">Est. edge<Tip text="Estimated expected value in spot points: target × odds − stop × odds − time decay if neither is reached. On unseen sessions this estimate did not rank outcomes — use it to reject clearly negative trades, not to pick winners." /></div>
              <div className={`mono text-[14px] font-semibold ${s.ev_pts > 0 ? 'text-green-400' : 'text-red-400'}`}>{isNum(s.ev_pts) ? `${s.ev_pts > 0 ? '+' : ''}${fmtNum(s.ev_pts, 1)}` : '—'}</div>
            </div>
          </div>
          {c.symbol && (
            <div className="rounded-lg border border-surface-3 px-3 py-2 text-[12px]">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-semibold text-gray-100">Buy {c.symbol}</span>
                <span className="text-gray-400">LTP <span className="mono text-gray-100">{fmtNum(c.ltp)}</span> · Δ {fmtNum(c.delta, 2)} · θ/lot <span className="text-red-400">{fmtRupee((c.theta || 0) * lotSize)}</span></span>
              </div>
              <div className="grid grid-cols-4 gap-2 mt-1.5 mono text-[11.5px]">
                <div><span className="text-gray-500">entry ≈ </span>{fmtNum(pr.entry)}</div>
                <div><span className="text-gray-500">stop ≈ </span><span className="text-red-400">{fmtNum(pr.stop)}</span></div>
                <div><span className="text-gray-500">T1 ≈ </span><span className="text-green-400">{fmtNum(pr.t1)}</span></div>
                <div><span className="text-gray-500">T2 ≈ </span><span className="text-green-400">{fmtNum(pr.t2)}</span></div>
              </div>
              <div className="text-[10.5px] text-gray-500 mt-1">Premiums estimated from delta & gamma at today's IV; real fills drift with time decay and IV. Risk per lot ≈ {fmtRupee(Math.max(0, (pr.entry || 0) - (pr.stop || 0)) * lotSize)}.</div>
            </div>
          )}
        </>
      ) : (
        <div className="grid grid-cols-3 gap-2 text-[12px]">
          <div><div className="text-[10px] uppercase tracking-wider text-gray-500">Short strikes</div><div className="mono text-gray-100">{fmtInt(s.entry_zone[0])} PE · {fmtInt(s.entry_zone[1])} CE</div></div>
          <div><div className="text-[10px] uppercase tracking-wider text-gray-500">Credit / unit</div><div className="mono text-green-400">{fmtNum(s.credit)}</div></div>
          <Odds label="Both walls hold" p={s.p_target} />
        </div>
      )}
      <ul className="text-[11.5px] text-gray-400 space-y-0.5">
        {s.why.map((w, i) => <li key={i}>• {w}</li>)}
      </ul>
    </div>
  );
}

function Reach({ snap }) {
  const P = snap.model?.prediction;
  if (!P) return null;
  const up = P.reach?.up, dn = P.reach?.down;
  const probs = P.probabilities || {};
  return (
    <Section title={<span className="flex items-center gap-1.5"><Compass className="w-4 h-4 text-brand-400" />Model read — where spot can still travel today</span>}
      tip="Ridge models trained on 3 years of NIFTY chain features predict how far spot will still rise and fall before the close. Tested on sessions after Dec 2025 they were never trained on.">
      <div className="grid sm:grid-cols-2 gap-3">
        {[['Further upside', up, +1, 'text-emerald-400'], ['Further downside', dn, -1, 'text-orange-400']].map(([label, r, sign, cls]) => r && (
          <div key={label} className="rounded-lg bg-surface-2 border border-surface-3 px-3 py-2.5">
            <div className="text-[10.5px] uppercase tracking-wider text-gray-500">{label} before close</div>
            <div className={`mono text-xl font-semibold ${cls}`}>{sign > 0 ? '+' : '−'}{fmtInt(r.median_pts)} pts <span className="text-[12px] text-gray-400">median</span></div>
            <div className="text-[11.5px] text-gray-400">
              Likely range {fmtInt(r.p20_pts)}–{fmtInt(r.p80_pts)} pts → {fmtInt(snap.spot + sign * r.p20_pts)} to {fmtInt(snap.spot + sign * r.p80_pts)}
            </div>
            {isNum(r.oos_corr) && <div className="text-[10.5px] text-gray-500">Unseen-data correlation {fmtNum(r.oos_corr, 2)}</div>}
          </div>
        ))}
      </div>
      <div className="mt-3 grid sm:grid-cols-2 gap-2">
        {['close', '60m'].map((k) => probs[k] && (
          <div key={k} className="flex items-center justify-between rounded-lg border border-surface-3 px-3 py-2 text-[12px]">
            <span className="text-gray-400">{k === 'close' ? 'P(closes above now)' : 'P(higher in 60 min)'}</span>
            <span className="flex items-center gap-2"><span className="mono text-gray-200">{fmtPct(probs[k].p)}</span><GradeBadge grade={probs[k].grade} /></span>
          </div>
        ))}
      </div>
      <div className="text-[11px] text-gray-500 mt-2">
        Direction models are shown for honesty: on unseen sessions they were no better than a coin flip (AUC {probs.close?.oos_auc ?? '—'} / {probs['60m']?.oos_auc ?? '—'}),
        so no setup relies on them. Ranges and wall-hold odds are where OI carries real information.
      </div>
    </Section>
  );
}

function Analogs({ snap }) {
  const A = snap.model?.analogs;
  if (!A) return null;
  return (
    <Section title={<span className="flex items-center gap-1.5"><History className="w-4 h-4 text-brand-400" />Most similar past sessions</span>}
      tip="Past NIFTY sessions at the same time of day and days-to-expiry whose chain (PCR, fresh writing, wall distances, straddle decay, move so far) looked most like now. Moves are rescaled to today's straddle.">
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 mb-3 text-[12px]">
        {[
          ['Closed higher', `${fmtNum(A.pct_up, 0)}%`],
          ['Median move to close', `${A.median_move_pts > 0 ? '+' : ''}${fmtInt(A.median_move_pts)} pts`],
          ['Typical further high / low', `+${fmtInt(A.median_max_up_pts)} / −${fmtInt(A.median_max_down_pts)}`],
          ['Call wall held', `${fmtNum(A.ce_wall_held_pct, 0)}%`],
          ['Put wall held', `${fmtNum(A.pe_wall_held_pct, 0)}%`],
        ].map(([k, v]) => (
          <div key={k} className="rounded-lg bg-surface-2 border border-surface-3 px-2.5 py-2">
            <div className="text-[10px] uppercase tracking-wider text-gray-500">{k}</div>
            <div className="mono text-gray-100">{v}</div>
          </div>
        ))}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-[12px]">
          <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
            <th className="text-left px-2 py-1 font-medium">Date</th><th className="text-right px-2 py-1 font-medium">Similarity</th>
            <th className="text-right px-2 py-1 font-medium">Move to close</th><th className="text-right px-2 py-1 font-medium">Went up / down</th>
            <th className="text-center px-2 py-1 font-medium">Call wall</th><th className="text-center px-2 py-1 font-medium">Put wall</th>
          </tr></thead>
          <tbody>
            {A.days.map((d) => (
              <tr key={d.date} className="border-b border-surface-3/40">
                <td className="px-2 py-1.5 mono text-gray-300">{d.date} <span className="text-gray-500">{d.time}</span></td>
                <td className="px-2 py-1.5 text-right mono text-gray-400">{fmtNum(d.similarity, 0)}</td>
                <td className={`px-2 py-1.5 text-right mono ${d.close_move_pts > 0 ? 'text-green-400' : 'text-red-400'}`}>{d.close_move_pts > 0 ? '+' : ''}{fmtInt(d.close_move_pts)}</td>
                <td className="px-2 py-1.5 text-right mono text-gray-400">+{fmtInt(d.max_up_pts)} / −{fmtInt(d.max_down_pts)}</td>
                <td className="px-2 py-1.5 text-center">{d.ce_held ? <CheckCircle2 className="w-4 h-4 text-green-400 inline" aria-label="held" /> : <XCircle className="w-4 h-4 text-red-400 inline" aria-label="broke" />}</td>
                <td className="px-2 py-1.5 text-center">{d.pe_held ? <CheckCircle2 className="w-4 h-4 text-green-400 inline" aria-label="held" /> : <XCircle className="w-4 h-4 text-red-400 inline" aria-label="broke" />}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="text-[10.5px] text-gray-500 mt-2">From {A.used} closest of {A.candidates} candidate sessions. Their direction split did not predict direction on unseen data; their ranges and wall outcomes are the useful part.</div>
    </Section>
  );
}

export default function EntryZones({ snap }) {
  const S = snap.setups;
  if (!S?.setups?.length) return <Empty>{S?.warnings?.[0] || 'No setups — walls could not be identified on both sides.'}</Empty>;
  const best = S.setups.find((s) => s.id === S.best);
  const rest = S.setups.filter((s) => s.id !== S.best);
  return (
    <div className="space-y-4">
      {S.warnings?.length > 0 && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 space-y-1">
          {S.warnings.map((w, i) => <div key={i} className="text-[12px] text-amber-300 flex gap-2"><AlertTriangle className="w-4 h-4 shrink-0" />{w}</div>)}
        </div>
      )}
      {!best && S.stand_aside && (
        <div className="rounded-xl border border-surface-3 bg-surface-2 px-4 py-3 flex gap-3">
          <PauseCircle className="w-6 h-6 text-brand-400 shrink-0" />
          <div><div className="text-[14px] font-semibold text-gray-100">Stand aside for now</div><div className="text-[12.5px] text-gray-400">{S.stand_aside}</div></div>
        </div>
      )}
      <div className="grid gap-4 xl:grid-cols-[1.1fr_1fr]">
        <div className="space-y-4">
          {best && <SetupCard s={best} best lot={snap.lot_size} />}
          <LevelMap snap={snap} />
        </div>
        <div className="space-y-4">
          <Reach snap={snap} />
          <Validation v={S.validation} />
        </div>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {rest.map((s) => <SetupCard key={s.id} s={s} lot={snap.lot_size} />)}
      </div>
      <Analogs snap={snap} />
      <div className="text-[11px] text-gray-500 flex gap-2">
        <Crosshair className="w-4 h-4 shrink-0" />
        Setups are research output, not advice. Odds come from {S.odds_source === 'history' ? '3 years of NIFTY Market Store sessions, tested on unseen data' : "the option market's implied volatility (history not loaded yet)"}.
        Only setups with a positive estimated edge are ever called "best-placed"; when none has one, the page says stand aside.
      </div>
    </div>
  );
}
