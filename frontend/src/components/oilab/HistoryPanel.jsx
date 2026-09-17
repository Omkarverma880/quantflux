import React, { useEffect, useMemo, useState } from 'react';
import { RefreshCw, Database, CheckCircle2, XCircle } from 'lucide-react';
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, Legend, ReferenceLine } from 'recharts';
import { api } from '../../api';
import { useTheme } from '../../ThemeContext';
import { usePalette, fmtNum, fmtInt, fmtPct, isNum, Section, GradeBadge, Empty } from './ui';

/**
 * History & Expiry — the 3-year NIFTY study, with every model's out-of-sample report card.
 */

// sequential ramp (one hue) for days-to-expiry, which is ordinal; expiry day gets the strongest step per theme
const DTE_RAMP = { dark: ['#1c5aa6', '#2a78d6', '#5598e7', '#86b6ef', '#b7d3f6'], light: ['#86b6ef', '#5598e7', '#2a78d6', '#1c5aa6', '#104281'] };

function Calibration({ rows }) {
  if (!rows?.length) return null;
  return (
    <div className="space-y-1">
      <div className="grid grid-cols-[1fr_1fr_40px] text-[9.5px] uppercase tracking-wider text-gray-500"><span>Predicted</span><span>Happened</span><span className="text-right">n</span></div>
      {rows.map((r, i) => (
        <div key={i} className="grid grid-cols-[1fr_1fr_40px] items-center gap-2 text-[11px] mono">
          <div className="flex items-center gap-1.5"><div className="h-1.5 rounded bg-brand-500/70" style={{ width: `${r.predicted * 100}%` }} /><span className="text-gray-400">{fmtPct(r.predicted)}</span></div>
          <div className="flex items-center gap-1.5"><div className="h-1.5 rounded bg-gray-300/70" style={{ width: `${r.actual * 100}%` }} /><span className="text-gray-200">{fmtPct(r.actual)}</span></div>
          <span className="text-right text-gray-500">{r.n}</span>
        </div>
      ))}
    </div>
  );
}

function ModelCard({ m }) {
  const o = m.out_of_sample || {};
  return (
    <div className="rounded-xl border border-surface-3 bg-surface-2 px-3.5 py-3 space-y-2">
      <div className="flex items-start justify-between gap-2">
        <div className="text-[12.5px] font-semibold text-gray-100 leading-snug">{m.target}</div>
        <GradeBadge grade={m.verdict?.grade} />
      </div>
      <div className="grid grid-cols-3 gap-2 text-[11px]">
        <div><div className="text-gray-500">AUC unseen</div><div className="mono text-[15px] text-white">{o.auc ?? '—'}</div></div>
        <div><div className="text-gray-500">Base rate</div><div className="mono text-gray-200">{isNum(o.base_rate) ? fmtPct(o.base_rate) : '—'}</div></div>
        <div><div className="text-gray-500">Test sessions</div><div className="mono text-gray-200">{m.test_sessions}</div></div>
      </div>
      <div className="text-[11px] text-gray-400">{m.verdict?.text}</div>
      <Calibration rows={o.calibration} />
      {m.drivers?.length > 0 && (
        <div className="text-[10.5px] text-gray-500">
          Biggest drivers: {m.drivers.slice(0, 3).map((d) => `${d.label} (${d.coef > 0 ? '+' : ''}${d.coef.toFixed(2)})`).join(' · ')}
        </div>
      )}
    </div>
  );
}

function WallGrid({ grid, distOrder, dteOrder, side }) {
  const pal = usePalette();
  const color = side === 'ce' ? pal.call : pal.put;
  const cell = (dte, dist) => grid.find((g) => g.dte === dte && g.dist === dist);
  return (
    <div>
      <div className="text-[11.5px] font-semibold mb-1.5" style={{ color }}>{side === 'ce' ? 'Call wall (resistance) held to close' : 'Put wall (support) held to close'}</div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[380px] text-[11px]">
          <thead><tr className="text-[10px] text-gray-500">
            <th className="text-left font-medium px-1.5 py-1">Distance →<br />Days to expiry ↓</th>
            {distOrder.map((d) => <th key={d} className="font-medium px-1.5 py-1 text-center">{d} straddle</th>)}
          </tr></thead>
          <tbody>
            {dteOrder.map((dte) => (
              <tr key={dte}>
                <td className="px-1.5 py-1 text-gray-400 whitespace-nowrap">{dte}</td>
                {distOrder.map((dist) => {
                  const c = cell(dte, dist);
                  const a = c ? 0.04 + Math.pow(c.held_pct / 100, 1.6) * 0.86 : 0;
                  const ink = a > 0.45 ? { color: '#ffffff' } : undefined;   // inline so the light-theme text overrides don't apply
                  return (
                    <td key={dist} className="p-0.5">
                      <div className="rounded-md px-1 py-1.5 text-center" style={{ background: c ? `rgba(42,120,214,${a})` : 'transparent' }}
                        title={c ? `${dte}, wall ${dist} straddles away: held ${c.held_pct}% of the time (${c.sessions} sessions)` : 'no data'}>
                        <div className="mono font-semibold text-gray-200" style={ink}>{c ? `${Math.round(c.held_pct)}%` : '—'}</div>
                        <div className="text-[9.5px] text-gray-400" style={ink ? { color: 'rgba(255,255,255,0.8)' } : undefined}>{c ? `${c.sessions}d` : ''}</div>
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DecayCurves({ curves }) {
  const pal = usePalette();
  const { theme } = useTheme();
  const keys = ['4+', '3', '2', '1', '0'];
  const data = useMemo(() => {
    const byCp = {};
    keys.forEach((k) => (curves[k] || []).forEach((p) => {
      if (p.cp > 925) return;
      byCp[p.cp] = byCp[p.cp] || { time: p.time };
      byCp[p.cp][k] = p.decay_med * 100;
    }));
    return Object.keys(byCp).sort((a, b) => a - b).map((cp) => byCp[cp]);
  }, [curves]);   // eslint-disable-line react-hooks/exhaustive-deps
  const ramp = DTE_RAMP[theme === 'light' ? 'light' : 'dark'];
  const axis = { stroke: pal.axis, fontSize: 10.5, tickLine: false, axisLine: false };
  return (
    <div className="h-64">
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={pal.grid} vertical={false} />
          <XAxis dataKey="time" {...axis} minTickGap={40} />
          <YAxis {...axis} width={44} tickFormatter={(v) => `${Math.round(v)}%`} />
          <Tooltip contentStyle={{ background: 'rgb(var(--surface-1))', border: '1px solid rgb(var(--surface-3))', borderRadius: 8, fontSize: 11.5 }}
            formatter={(v, n) => [`${Number(v).toFixed(1)}%`, n]} />
          <Legend wrapperStyle={{ fontSize: 11 }} iconType="plainline" />
          <ReferenceLine y={0} stroke={pal.axis} strokeOpacity={0.5} />
          {keys.map((k, i) => (
            <Line key={k} name={k === '0' ? 'Expiry day' : `${k} day${k === '1' ? '' : 's'} before`} type="monotone" dataKey={k}
              stroke={ramp[i]} strokeWidth={k === '0' ? 2.5 : 2} dot={false} isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function HistoryPanel() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState('');
  const load = () => api.oiLabHistory().then((r) => { setData(r); setErr(r.status === 'error' ? r.message : ''); }).catch((e) => setErr(String(e.message || e)));
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (data?.status !== 'pending') return undefined;
    const t = setTimeout(load, 3000);
    return () => clearTimeout(t);
  }, [data]);

  if (err) return <Empty>{err}</Empty>;
  if (!data) return <Empty>Loading…</Empty>;
  const h = data.history || {};
  if (data.status === 'pending') {
    return (
      <div className="card text-center py-10 space-y-2">
        <Database className="w-8 h-8 text-brand-400 mx-auto animate-pulse" />
        <div className="text-gray-200 font-semibold">{h.status === 'error' ? 'History could not be built' : 'Building the 3-year OI study…'}</div>
        <div className="text-[12.5px] text-gray-400">{h.status === 'error' ? h.error : `${h.done || 0} / ${h.total || '…'} months processed from the Market Store`}</div>
      </div>
    );
  }
  const R = data.report;
  const walls = ['ce_held', 'pe_held', 'inside'].map((k) => R.models[k]).filter(Boolean);
  const dirs = ['close', '60m'].map((k) => R.models[k]).filter(Boolean);
  const E = R.expiry_summary || {};

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-[12.5px] text-gray-400">
          <span className="text-gray-100 font-semibold">{R.underlying}</span> · {R.sessions} sessions ({R.first} → {R.last}) · every 5 minutes ·
          models trained before <span className="mono">{R.holdout_start}</span>, scored only on later sessions
        </div>
        <button className="btn-ghost !py-1.5 text-[12px] flex items-center gap-1.5" onClick={() => api.oiLabHistoryRebuild().then(() => setTimeout(load, 800))}>
          <RefreshCw className="w-3.5 h-3.5" />Rebuild
        </button>
      </div>

      <Section title="What OI can predict — wall models" tip="Logistic regressions on the 12 chain features. AUC 0.5 = coin flip, 1.0 = perfect. Calibration compares the predicted probability with how often it actually happened, on sessions the model never saw.">
        <div className="grid gap-3 lg:grid-cols-3">{walls.map((m) => <ModelCard key={m.target} m={m} />)}</div>
      </Section>

      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <Section title="What OI cannot predict — direction" tip="Shown so the limits are visible. These carry no weight in the entry engine.">
          <div className="grid gap-3 md:grid-cols-2">{dirs.map((m) => <ModelCard key={m.target} m={m} />)}</div>
        </Section>
        <Section title="Remaining travel models" tip="How far spot still rises / falls before the close, in ATM straddles. Band coverage should be near 60% (20th–80th percentile).">
          <div className="space-y-2">
            {Object.entries(R.reach_models || {}).map(([k, m]) => (
              <div key={k} className="rounded-lg bg-surface-2 border border-surface-3 px-3 py-2 text-[12px]">
                <div className="text-gray-100 font-medium">{m.target}</div>
                <div className="grid grid-cols-3 gap-2 mono mt-1">
                  <div><div className="text-[10px] text-gray-500 font-sans">Correlation</div>{m.out_of_sample?.corr ?? '—'}</div>
                  <div><div className="text-[10px] text-gray-500 font-sans">20–80% band</div>{isNum(m.out_of_sample?.band_coverage) ? fmtPct(m.out_of_sample.band_coverage) : '—'}</div>
                  <div><div className="text-[10px] text-gray-500 font-sans">Median error</div>{fmtNum(m.out_of_sample?.median_abs_error, 2)}</div>
                </div>
              </div>
            ))}
          </div>
        </Section>
      </div>

      <Section title="How often OI walls held until the close" tip="Every 5-minute checkpoint over 3 years. Distance is measured in ATM straddles (the market's own expected move), so it compares fairly across volatility regimes. Darker = held more often.">
        <div className="grid gap-4 lg:grid-cols-2">
          <WallGrid grid={R.wall_grid.ce} distOrder={R.dist_order} dteOrder={R.dte_order} side="ce" />
          <WallGrid grid={R.wall_grid.pe} distOrder={R.dist_order} dteOrder={R.dte_order} side="pe" />
        </div>
      </Section>

      <div className="grid gap-4 2xl:grid-cols-2">
        <Section title="Straddle decay by days to expiry" tip="Median % change of the ATM straddle since 09:20. Expiry-day premium melts fastest — the reason late-day option buying on expiry rarely pays.">
          <DecayCurves curves={R.expiry_curves} />
        </Section>
        <Section title={`Expiry days (${E.expiry_days ?? 0})`} tip="Walls taken at 10:00 on each weekly expiry day.">
          <div className="grid grid-cols-2 gap-2 mb-3">
            {[
              ['Closed between 10:00 walls', isNum(E.close_inside_pct) ? `${E.close_inside_pct}%` : '—'],
              ['Both walls untouched all day', isNum(E.both_walls_held_pct) ? `${E.both_walls_held_pct}%` : '—'],
              ['Median move 10:00 → close', isNum(E.median_abs_move_from_1000) ? `±${fmtInt(E.median_abs_move_from_1000)} pts` : '—'],
              ['Median straddle decay 10:00 → 15:25', isNum(E.median_straddle_decay_pct) ? `${E.median_straddle_decay_pct}%` : '—'],
            ].map(([k, v]) => (
              <div key={k} className="rounded-lg bg-surface-2 border border-surface-3 px-2.5 py-2">
                <div className="text-[10px] uppercase tracking-wider text-gray-500">{k}</div>
                <div className="mono text-[15px] text-gray-100">{v}</div>
              </div>
            ))}
          </div>
          <div className="overflow-x-auto max-h-72">
            <table className="w-full min-w-[560px] text-[11.5px]">
              <thead className="sticky top-0 bg-surface-1"><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                <th className="text-left px-2 py-1 font-medium">Expiry</th><th className="text-right px-2 py-1 font-medium">10:00</th>
                <th className="text-right px-2 py-1 font-medium">PE / CE wall</th><th className="text-right px-2 py-1 font-medium">Close</th>
                <th className="text-right px-2 py-1 font-medium">Straddle</th><th className="text-center px-2 py-1 font-medium">Walls held</th>
              </tr></thead>
              <tbody>
                {R.recent_expiries.map((e) => (
                  <tr key={e.date} className="border-b border-surface-3/40">
                    <td className="px-2 py-1.5 mono text-gray-300">{e.date}</td>
                    <td className="px-2 py-1.5 text-right mono text-gray-400">{fmtInt(e.spot_1000)}</td>
                    <td className="px-2 py-1.5 text-right mono text-gray-300">{fmtInt(e.pe_wall_1000)} / {fmtInt(e.ce_wall_1000)}</td>
                    <td className={`px-2 py-1.5 text-right mono ${e.move_from_1000 >= 0 ? 'text-green-400' : 'text-red-400'}`}>{fmtInt(e.close)} ({e.move_from_1000 >= 0 ? '+' : ''}{fmtInt(e.move_from_1000)})</td>
                    <td className="px-2 py-1.5 text-right mono text-gray-400">{fmtNum(e.straddle_1000, 0)} → {fmtNum(e.straddle_1525, 0)}</td>
                    <td className="px-2 py-1.5 text-center whitespace-nowrap">
                      {e.pe_held ? <CheckCircle2 className="w-3.5 h-3.5 text-green-400 inline" aria-label="put wall held" /> : <XCircle className="w-3.5 h-3.5 text-red-400 inline" aria-label="put wall broke" />}
                      {' '}
                      {e.ce_held ? <CheckCircle2 className="w-3.5 h-3.5 text-green-400 inline" aria-label="call wall held" /> : <XCircle className="w-3.5 h-3.5 text-red-400 inline" aria-label="call wall broke" />}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      </div>
    </div>
  );
}
