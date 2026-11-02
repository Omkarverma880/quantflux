import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ResponsiveContainer, ComposedChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, Legend,
  ReferenceLine,
} from 'recharts';
import { Loader2, RefreshCw, Download, Circle } from 'lucide-react';
import { api } from '../../api';
import { usePalette, fmtNum, fmtInt, fmtSignedPct, isNum, tone, Section, Stat, Empty } from './ui';

/**
 * Bias Tape — the X-Ray's "who is in control" score, recorded every minute and kept.
 *
 * The score on the X-Ray is computed from the live chain and discarded on refresh, so there
 * is no way to ask afterwards whether it led price or merely followed it. This reads the
 * stored tape: pick a date, pick a resolution, and see the score beside what the index
 * actually did.
 *
 * The chart uses two Y axes, which the other OI Lab charts deliberately avoid. It is the one
 * place it is warranted: the whole point is comparing a −100..+100 score against a ~23,000
 * price, and forcing them onto one axis would flatten one of them into a straight line.
 */

const TIMEFRAMES = [
  [1, '1 min'], [5, '5 min'], [15, '15 min'], [30, '30 min'], [60, '1 hour'],
];

const scoreTone = (v) => (!isNum(v) ? 'text-gray-400'
  : v >= 35 ? 'text-emerald-400' : v >= 12 ? 'text-green-400'
    : v > -12 ? 'text-gray-300' : v > -35 ? 'text-red-400' : 'text-red-500');

const labelOf = (v) => (!isNum(v) ? '—'
  : v >= 35 ? 'Strong bullish' : v >= 12 ? 'Bullish'
    : v > -12 ? 'Neutral' : v > -35 ? 'Bearish' : 'Strong bearish');

export default function BiasTape({ index = 'NIFTY', active = false }) {
  const pal = usePalette();
  const [tf, setTf] = useState(5);
  const [day, setDay] = useState('');
  const [sessions, setSessions] = useState([]);
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [meta, setMeta] = useState(null);

  useEffect(() => { if (active) api.oiBiasMeta().then(setMeta).catch(() => {}); }, [active]);

  const loadSessions = useCallback(async () => {
    try {
      const d = await api.oiBiasSessions(index);
      const list = d?.sessions || [];
      setSessions(list);
      setDay((cur) => (cur && list.some((s) => s.trading_date === cur)
        ? cur : (list[0]?.trading_date || '')));
    } catch (e) { setErr(String(e.message || e)); }
  }, [index]);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const d = await api.oiBiasSeries(index, day || undefined, tf);
      if (d?.status === 'error') { setErr(d.message); setData(null); }
      else { setErr(''); setData(d); if (!day && d?.trading_date) setDay(d.trading_date); }
    } catch (e) { setErr(String(e.message || e)); } finally { setBusy(false); }
  }, [index, day, tf]);

  useEffect(() => { if (active) loadSessions(); }, [active, loadSessions]);
  useEffect(() => { if (active) load(); }, [active, load]);

  // while the market is recording, keep the latest session moving
  useEffect(() => {
    if (!active || !data?.available) return undefined;
    const isLatest = sessions.length && day === sessions[0]?.trading_date;
    if (!isLatest) return undefined;
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [active, data, day, sessions, load]);

  const bars = data?.bars || [];
  const chart = useMemo(() => bars.map((b) => ({
    time: b.time, score: b.score_close, spot: b.spot,
  })), [bars]);

  const csv = () => {
    const head = ['time', 'score_open', 'score_high', 'score_low', 'score_close', 'score_avg',
      'label', 'spot', 'score_change', 'spot_change', 'pcr_oi', 'max_pain', 'straddle',
      'atm_iv', 'ticks'];
    const lines = [head.join(',')].concat(bars.map((b) => head.map((h) => {
      const v = b[h] ?? (h === 'label' ? labelOf(b.score_close) : '');
      return typeof v === 'string' && v.includes(',') ? `"${v}"` : v;
    }).join(',')));
    const url = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `bias-tape-${index}-${data?.trading_date || 'session'}-${tf}m.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const sm = data?.summary;
  const corr = sm?.correlation;

  return (
    <div className="space-y-4">
      <Section
        title={`Bias tape — ${index}`}
        tip="The 'who is in control' score from the OI X-Ray, stored once a minute. It is read from the X-Ray's own reading, not recomputed, so the tape and the screen can never disagree."
        right={(
          <div className="flex flex-wrap items-center gap-2">
            <select className="input-field !py-1 !px-2 text-[12px]" value={day}
              onChange={(e) => setDay(e.target.value)} disabled={!sessions.length}>
              {!sessions.length && <option value="">no recordings yet</option>}
              {sessions.map((s) => (
                <option key={s.trading_date} value={s.trading_date}>
                  {s.trading_date} · {s.minutes}m ({s.from}–{s.to})
                </option>
              ))}
            </select>
            <div className="flex rounded-lg border border-surface-3 overflow-hidden">
              {TIMEFRAMES.map(([v, lbl]) => (
                <button key={v} onClick={() => setTf(v)}
                  className={`px-2 py-1 text-[11.5px] ${tf === v
                    ? 'bg-brand-500/20 text-brand-300' : 'text-gray-400 hover:text-white'}`}>
                  {lbl}
                </button>
              ))}
            </div>
            <button className="btn-secondary !py-1 !px-2 text-[12px] flex items-center gap-1"
              onClick={load} disabled={busy}>
              {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                : <RefreshCw className="w-3.5 h-3.5" />}
            </button>
            {bars.length > 0 && (
              <button className="btn-secondary !py-1 !px-2 text-[12px] flex items-center gap-1"
                onClick={csv}><Download className="w-3.5 h-3.5" />CSV</button>
            )}
          </div>
        )}>
        {err && <div className="text-[12px] text-red-400 mb-2">{err}</div>}

        {!data?.available ? (
          <Empty>
            {data?.message
              || (busy ? 'Reading the tape…' : 'Nothing recorded yet.')}
            {meta && !meta.connected && (
              <div className="mt-2 text-[11.5px] text-amber-400">
                Zerodha is not connected, so nothing is being recorded right now.
              </div>
            )}
          </Empty>
        ) : (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2">
              <Stat label="Score now" value={fmtNum(sm.close, 1)}
                valueClass={scoreTone(sm.close)} sub={labelOf(sm.close)} />
              <Stat label="Opened at" value={fmtNum(sm.open, 1)} valueClass={scoreTone(sm.open)} />
              <Stat label="High" value={fmtNum(sm.high, 1)} sub={sm.high_at}
                valueClass="text-emerald-400" />
              <Stat label="Low" value={fmtNum(sm.low, 1)} sub={sm.low_at}
                valueClass="text-red-400" />
              <Stat label="Average" value={fmtNum(sm.avg, 1)} valueClass={scoreTone(sm.avg)} />
              <Stat label="Sign flips" value={fmtInt(sm.sign_flips)}
                tip="How many times the score crossed zero — bullish to bearish or back."
                sub="crossed zero" />
              <Stat label={`${index} move`} value={fmtSignedPct(sm.spot_move_pct)}
                valueClass={tone(sm.spot_move_pct)}
                sub={isNum(sm.spot_close) ? fmtNum(sm.spot_close, 2) : '—'} />
            </div>
            <div className="mt-2 text-[11px] text-gray-500">
              {data.count} bars at {tf} min, from {data.ticks} recorded minutes · {data.note}
            </div>
          </>
        )}
      </Section>

      {data?.available && (
        <Section title={`Score against ${index}`}
          tip="Two Y axes on purpose: a −100..+100 score and a ~23,000 index cannot share one scale without flattening the score into a straight line.">
          <div className="h-80">
            <ResponsiveContainer>
              <ComposedChart data={chart} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid stroke={pal.grid} vertical={false} />
                <XAxis dataKey="time" stroke={pal.axis} fontSize={10.5} tickLine={false}
                  axisLine={false} minTickGap={34} />
                <YAxis yAxisId="score" stroke={pal.axis} fontSize={10.5} tickLine={false}
                  axisLine={false} width={44} domain={[-100, 100]} />
                <YAxis yAxisId="price" orientation="right" stroke={pal.axis} fontSize={10.5}
                  tickLine={false} axisLine={false} width={64} domain={['auto', 'auto']}
                  tickFormatter={fmtInt} />
                <Tooltip
                  contentStyle={{ background: 'rgb(var(--surface-1))', border: '1px solid rgb(var(--surface-3))', borderRadius: 8, fontSize: 11.5 }}
                  labelStyle={{ color: pal.axis, marginBottom: 2 }}
                  cursor={{ stroke: pal.axis, strokeDasharray: '3 3' }}
                  formatter={(v, n) => [fmtNum(v, n === 'Bias score' ? 1 : 2), n]} />
                <Legend wrapperStyle={{ fontSize: 11, paddingTop: 4 }} iconType="plainline" />
                <ReferenceLine yAxisId="score" y={0} stroke={pal.axis} strokeDasharray="4 4" />
                <Line yAxisId="score" name="Bias score" type="monotone" dataKey="score"
                  stroke={pal.put} strokeWidth={2} dot={false} isAnimationActive={false} />
                <Line yAxisId="price" name={index} type="monotone" dataKey="spot"
                  stroke={pal.spot} strokeWidth={2} dot={false} isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          {corr ? (
            <div className="mt-2 text-[11.5px] text-gray-400">
              Bar-to-bar changes moved together <b className="text-gray-200">
                {corr.same_direction_pct}%</b> of the time (r = {fmtNum(corr.r, 2)}, {corr.bars} bars).
              <span className="text-gray-500"> {corr.note}</span>
            </div>
          ) : (
            <div className="mt-2 text-[11px] text-gray-500">
              Not enough movement in this session to compute a correlation.
            </div>
          )}
        </Section>
      )}

      {data?.available && (
        <Section title="Every bar" tip="Score shown the way a candle shows price — first, high, low and last reading inside each bucket. An average alone would hide the swing.">
          <div className="overflow-auto max-h-[26rem]">
            <table className="w-full text-[11.5px]">
              <thead className="text-[10px] uppercase tracking-wider text-gray-500 sticky top-0 bg-surface-1">
                <tr className="border-b border-surface-3">
                  <th className="text-left px-2 py-1.5">Time</th>
                  <th className="text-right px-2 py-1.5">Open</th>
                  <th className="text-right px-2 py-1.5">High</th>
                  <th className="text-right px-2 py-1.5">Low</th>
                  <th className="text-right px-2 py-1.5">Close</th>
                  <th className="text-right px-2 py-1.5">Δ</th>
                  <th className="text-left px-2 py-1.5">Read</th>
                  <th className="text-right px-2 py-1.5">{index}</th>
                  <th className="text-right px-2 py-1.5">Δ pts</th>
                  <th className="text-right px-2 py-1.5">PCR</th>
                  <th className="text-right px-2 py-1.5">Max pain</th>
                  <th className="text-right px-2 py-1.5">IV</th>
                </tr>
              </thead>
              <tbody>
                {[...bars].reverse().map((b) => (
                  <tr key={b.minute} className="border-b border-surface-3/40 hover:bg-surface-2/40">
                    <td className="px-2 py-1 mono text-gray-300">{b.time}</td>
                    <td className="px-2 py-1 text-right mono text-gray-500">{fmtNum(b.score_open, 1)}</td>
                    <td className="px-2 py-1 text-right mono text-emerald-400/80">{fmtNum(b.score_high, 1)}</td>
                    <td className="px-2 py-1 text-right mono text-red-400/80">{fmtNum(b.score_low, 1)}</td>
                    <td className={`px-2 py-1 text-right mono font-semibold ${scoreTone(b.score_close)}`}>
                      {fmtNum(b.score_close, 1)}
                    </td>
                    <td className={`px-2 py-1 text-right mono ${tone(b.score_change)}`}>
                      {isNum(b.score_change) ? `${b.score_change > 0 ? '+' : ''}${fmtNum(b.score_change, 1)}` : '—'}
                    </td>
                    <td className="px-2 py-1 whitespace-nowrap">
                      <Circle className={`w-2 h-2 inline mr-1 ${scoreTone(b.score_close)}`}
                        fill="currentColor" />
                      <span className="text-gray-400">{b.label || labelOf(b.score_close)}</span>
                    </td>
                    <td className="px-2 py-1 text-right mono text-gray-300">{fmtNum(b.spot, 2)}</td>
                    <td className={`px-2 py-1 text-right mono ${tone(b.spot_change)}`}>
                      {isNum(b.spot_change) ? `${b.spot_change > 0 ? '+' : ''}${fmtNum(b.spot_change, 1)}` : '—'}
                    </td>
                    <td className="px-2 py-1 text-right mono text-gray-500">{fmtNum(b.pcr_oi, 2)}</td>
                    <td className="px-2 py-1 text-right mono text-gray-500">{fmtInt(b.max_pain)}</td>
                    <td className="px-2 py-1 text-right mono text-gray-500">
                      {isNum(b.atm_iv) ? `${fmtNum(b.atm_iv, 1)}%` : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      <div className="text-[11px] text-gray-500 px-1">
        {meta?.note || ''}
      </div>
    </div>
  );
}
