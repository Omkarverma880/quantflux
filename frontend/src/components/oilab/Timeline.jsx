import React, { useMemo } from 'react';
import {
  ResponsiveContainer, LineChart, Line, ComposedChart, Area, XAxis, YAxis, Tooltip, CartesianGrid, Legend, ReferenceLine,
} from 'recharts';
import { usePalette, fmtQty, fmtNum, fmtInt, isNum, Section, Empty } from './ui';

/**
 * Flow & timeline — how the walls, OI and premiums evolved through today, and how
 * today's straddle decay compares with every past session at the same days-to-expiry.
 * One measure per chart (no dual axes).
 */

function useChartStyle() {
  const pal = usePalette();
  return {
    pal,
    axis: { stroke: pal.axis, fontSize: 10.5, tickLine: false, axisLine: false },
    tip: {
      contentStyle: { background: 'rgb(var(--surface-1))', border: '1px solid rgb(var(--surface-3))', borderRadius: 8, fontSize: 11.5 },
      labelStyle: { color: pal.axis, marginBottom: 2 },
      cursor: { stroke: pal.axis, strokeDasharray: '3 3' },
    },
    legend: { wrapperStyle: { fontSize: 11, paddingTop: 4 }, iconType: 'plainline' },
  };
}

function ChartBox({ title, tip, children, h = 'h-60' }) {
  return (
    <Section title={title} tip={tip}>
      <div className={h}><ResponsiveContainer>{children}</ResponsiveContainer></div>
    </Section>
  );
}

export default function Timeline({ snap }) {
  const { pal, axis, tip, legend } = useChartStyle();
  const tl = snap.timeline || [];
  const curve = snap.model?.expiry_curve;

  const decay = useMemo(() => {
    if (!curve?.history) return [];
    const today = Object.fromEntries((curve.today || []).map((t) => [t.cp, t]));
    return curve.history.filter((h) => h.cp <= 925).map((h) => ({
      time: h.time,
      band: [h.decay_p25 * 100, h.decay_p75 * 100],
      median: h.decay_med * 100,
      today: isNum(today[h.cp]?.decay) ? today[h.cp].decay * 100 : null,
      move_med: h.move_med,
      move_today: today[h.cp]?.move ?? null,
    }));
  }, [curve]);

  if (!tl.length) {
    return <Empty>The intraday timeline builds from 5-minute OI history, fetched in the background (Zerodha allows ~3 requests/second). It appears after the first refresh or two.</Empty>;
  }
  const dteLabel = curve ? (curve.dte_bucket === '0' ? 'expiry days' : `sessions ${curve.dte_bucket} day(s) before expiry`) : '';
  const sessions = curve?.history?.[0]?.sessions;

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <ChartBox title="Spot against the walls" tip="Biggest call-OI strike at/above ATM (resistance) and biggest put-OI strike at/below ATM (support), every 5 minutes. A wall stepping up is a bullish shift; stepping down is bearish.">
        <LineChart data={tl} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={pal.grid} vertical={false} />
          <XAxis dataKey="time" {...axis} minTickGap={36} />
          <YAxis {...axis} width={58} domain={['auto', 'auto']} tickFormatter={fmtInt} />
          <Tooltip {...tip} formatter={(v, n) => [fmtNum(v, n === 'Spot' ? 2 : 0), n]} />
          <Legend {...legend} />
          <Line name="Call wall (CE)" type="stepAfter" dataKey="ce_wall" stroke={pal.call} strokeWidth={2} dot={false} isAnimationActive={false} />
          <Line name="Spot" type="monotone" dataKey="spot" stroke={pal.spot} strokeWidth={2} dot={false} isAnimationActive={false} />
          <Line name="Put wall (PE)" type="stepAfter" dataKey="pe_wall" stroke={pal.put} strokeWidth={2} dot={false} isAnimationActive={false} />
        </LineChart>
      </ChartBox>

      <ChartBox title="OI added since previous close — calls vs puts" tip="Who is writing more as the day goes on. Put OI rising faster = writers underwriting the downside (supportive); call OI rising faster = writers capping the upside.">
        <LineChart data={tl} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={pal.grid} vertical={false} />
          <XAxis dataKey="time" {...axis} minTickGap={36} />
          <YAxis {...axis} width={58} tickFormatter={fmtQty} />
          <Tooltip {...tip} formatter={(v, n) => [fmtQty(v), n]} />
          <Legend {...legend} />
          <ReferenceLine y={0} stroke={pal.axis} strokeOpacity={0.5} />
          <Line name="CE OI change" type="monotone" dataKey="ce_oi_chg" stroke={pal.call} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
          <Line name="PE OI change" type="monotone" dataKey="pe_oi_chg" stroke={pal.put} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
        </LineChart>
      </ChartBox>

      <ChartBox title="Put–call ratio through the day" tip="PE OI ÷ CE OI across the strikes shown. A rising PCR means put writing is outpacing call writing." h="h-48">
        <LineChart data={tl} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={pal.grid} vertical={false} />
          <XAxis dataKey="time" {...axis} minTickGap={36} />
          <YAxis {...axis} width={40} domain={['auto', 'auto']} />
          <Tooltip {...tip} formatter={(v) => [fmtNum(v, 2), 'PCR']} />
          <ReferenceLine y={1} stroke={pal.axis} strokeDasharray="4 4" strokeOpacity={0.6} />
          <Line name="PCR" type="monotone" dataKey="pcr" stroke={pal.spot} strokeWidth={2} dot={false} isAnimationActive={false} />
        </LineChart>
      </ChartBox>

      <ChartBox title="ATM straddle through the day" tip="Call + put at the money at each checkpoint (the ATM strike moves with spot). Falling = sellers winning on time decay; rising = a move or volatility is being bought." h="h-48">
        <LineChart data={tl} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
          <CartesianGrid stroke={pal.grid} vertical={false} />
          <XAxis dataKey="time" {...axis} minTickGap={36} />
          <YAxis {...axis} width={48} domain={['auto', 'auto']} />
          <Tooltip {...tip} formatter={(v) => [fmtNum(v), 'Straddle']} />
          <Line name="Straddle" type="monotone" dataKey="straddle" stroke={pal.spot} strokeWidth={2} dot={false} isAnimationActive={false} />
        </LineChart>
      </ChartBox>

      {decay.length > 0 && (
        <Section className="xl:col-span-2" title="Expiry tracker — today's straddle decay vs history"
          tip="Shaded: middle 50% of past NIFTY sessions at the same days-to-expiry; dashed: their median; solid: today. Today above the band = premiums richer than usual (a move is being priced); below = decaying faster than usual.">
          <div className="text-[11.5px] text-gray-400 mb-2">
            Compared with {sessions ? `${sessions} past ` : ''}{dteLabel} (3-year NIFTY Market Store). % change of the ATM straddle since 09:20.
          </div>
          <div className="h-64">
            <ResponsiveContainer>
              <ComposedChart data={decay} margin={{ top: 6, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid stroke={pal.grid} vertical={false} />
                <XAxis dataKey="time" {...axis} minTickGap={40} />
                <YAxis {...axis} width={44} tickFormatter={(v) => `${Math.round(v)}%`} />
                <Tooltip {...tip} formatter={(v, n) => [Array.isArray(v) ? `${v[0].toFixed(0)}% to ${v[1].toFixed(0)}%` : `${Number(v).toFixed(1)}%`, n]} />
                <Legend {...legend} />
                <ReferenceLine y={0} stroke={pal.axis} strokeOpacity={0.5} />
                <Area name="History, middle 50%" type="monotone" dataKey="band" stroke="none" fill={pal.band} isAnimationActive={false} />
                <Line name="History median" type="monotone" dataKey="median" stroke={pal.axis} strokeDasharray="5 4" strokeWidth={1.5} dot={false} isAnimationActive={false} />
                <Line name="Today" type="monotone" dataKey="today" stroke={pal.spot} strokeWidth={2.5} dot={false} connectNulls isAnimationActive={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </Section>
      )}
    </div>
  );
}
