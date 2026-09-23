import React from 'react';

/**
 * The setup, drawn: daily candles with volume underneath, the ceiling the stock is coiling under,
 * the base itself boxed with its length in weeks, and a flag on every breakout it has already had
 * this year. Pure SVG — no chart library, no network beyond the candles themselves.
 */
const UP = '#10b981';
const DOWN = '#ef4444';

export default function CandleChart({ chart, height = 190 }) {
  const c = chart?.candles || [];
  if (c.length < 5) {
    return <div className="h-[190px] flex items-center justify-center text-[11.5px] text-gray-600">
      no candles cached yet
    </div>;
  }
  const W = 640;
  const padR = 46, padB = 16;
  const volH = Math.round(height * 0.22);
  const priceH = height - volH - padB;
  const lo = Math.min(...c.map((x) => x.l), chart.pivot ?? Infinity);
  const hi = Math.max(...c.map((x) => x.h), chart.pivot ?? -Infinity);
  const pad = (hi - lo) * 0.06 || 1;
  const y = (v) => priceH - ((v - (lo - pad)) / ((hi + pad) - (lo - pad))) * priceH;
  const step = (W - padR) / c.length;
  const x = (i) => i * step + step / 2;
  const bw = Math.max(1.2, Math.min(6, step * 0.62));
  const vMax = Math.max(...c.map((x_) => x_.v), 1);
  const vy = (v) => volH - (v / vMax) * volH;

  const ticks = [lo, (lo + hi) / 2, hi].map((v) => ({ v, y: y(v) }));
  const dateAt = [0, Math.floor(c.length / 2), c.length - 1];
  const sma = (chart.sma50 || []).map((v, i) => (v == null ? null : `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`))
    .filter(Boolean).join(' ').replace(/L/, 'M');

  return (
    <svg viewBox={`0 0 ${W} ${height}`} className="w-full" style={{ height }} preserveAspectRatio="none">
      {/* the base, boxed around its own range */}
      {chart.base_from != null && (() => {
        const w = c.slice(chart.base_from, chart.base_to + 1);
        const bHi = Math.max(...w.map((k) => k.h));
        const bLo = Math.min(...w.map((k) => k.l));
        const weeks = ((chart.base_to - chart.base_from + 1) / 5).toFixed(1);
        return (
          <g>
            <rect x={x(chart.base_from) - bw} y={y(bHi) - 2} width={(chart.base_to - chart.base_from) * step + bw * 2}
              height={Math.max(4, y(bLo) - y(bHi) + 4)} fill="#10b98114" stroke="#10b98166" strokeWidth="1" rx="2" />
            <text x={x(chart.base_from)} y={Math.max(9, y(bHi) - 5)} fontSize="9" fill="#34d399">{weeks} wks</text>
          </g>
        );
      })()}
      {/* where the breakout happened, and where it is wrong */}
      {chart.breakout_i != null && chart.breakout_i >= 0 && (
        <line x1={x(chart.breakout_i)} x2={x(chart.breakout_i)} y1="0" y2={priceH} stroke="#38bdf8"
          strokeWidth="1" strokeDasharray="3 3" opacity="0.7" />
      )}
      {chart.stop != null && chart.stop > lo && (
        <line x1="0" x2={W - padR} y1={y(chart.stop)} y2={y(chart.stop)} stroke="#ef4444" strokeWidth="1"
          strokeDasharray="4 4" opacity="0.6" />
      )}
      {/* the ceiling */}
      {chart.pivot != null && (
        <>
          <line x1="0" x2={W - padR} y1={y(chart.pivot)} y2={y(chart.pivot)} stroke="#10b981" strokeWidth="1"
            strokeDasharray="5 4" opacity="0.85" />
          <rect x="0" y={Math.max(0, y(chart.pivot) - 8)} width="86" height="14" rx="3" fill="#10b981" opacity="0.9" />
          <text x="4" y={Math.max(10, y(chart.pivot) + 2)} fontSize="9.5" fill="#04231a" fontWeight="700">
            ceiling ₹{Number(chart.pivot).toLocaleString('en-IN', { maximumFractionDigits: 2 })}
          </text>
        </>
      )}
      {/* the 50-day average */}
      {sma && <path d={sma} fill="none" stroke="#60a5fa" strokeWidth="1" opacity="0.5" />}
      {/* candles */}
      {c.map((k, i) => {
        const up = k.c >= k.o;
        const col = up ? UP : DOWN;
        const top = y(Math.max(k.o, k.c));
        const h = Math.max(1, Math.abs(y(k.o) - y(k.c)));
        return (
          <g key={i}>
            <line x1={x(i)} x2={x(i)} y1={y(k.h)} y2={y(k.l)} stroke={col} strokeWidth="0.9" opacity="0.9" />
            <rect x={x(i) - bw / 2} y={top} width={bw} height={h} fill={col} opacity={up ? 0.9 : 0.85} />
          </g>
        );
      })}
      {/* breakouts already taken this year */}
      {(chart.flags || []).map((f, i) => (
        <g key={`f${i}`} transform={`translate(${x(f.i)}, 6)`}>
          <line x1="0" x2="0" y1="0" y2="9" stroke="#f59e0b" strokeWidth="1.1" />
          <path d="M0,0 L7,2.6 L0,5.2 Z" fill="#f59e0b" />
        </g>
      ))}
      {/* volume */}
      <g transform={`translate(0, ${priceH + 6})`}>
        {c.map((k, i) => (
          <rect key={`v${i}`} x={x(i) - bw / 2} y={vy(k.v)} width={bw} height={Math.max(0.6, volH - vy(k.v))}
            fill={k.c >= k.o ? UP : DOWN} opacity="0.35" />
        ))}
      </g>
      {/* axes */}
      {ticks.map((t, i) => (
        <text key={`t${i}`} x={W - padR + 4} y={Math.min(priceH, Math.max(8, t.y + 3))} fontSize="9" fill="#6b7280">
          {Number(t.v).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
        </text>
      ))}
      {dateAt.map((i, n) => (
        <text key={`d${n}`} x={Math.min(W - padR - 30, Math.max(0, x(i) - 18))} y={height - 3} fontSize="8.5" fill="#6b7280">
          {c[i]?.d?.slice(2)}
        </text>
      ))}
    </svg>
  );
}
