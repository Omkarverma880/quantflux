import React, { useMemo, useState } from 'react';

/**
 * Reusable SVG candlestick chart with overlay lines and event markers.
 * Self-contained (no chart library) so it stays light and theme-consistent.
 *
 * props:
 *   candles  [{t, open, high, low, close, volume}]
 *   overlays [{key, label, color, values:[number|null]}]   — same length as candles
 *   markers  [{index, label, color, side:'up'|'down'}]
 *   rails    [{price, color, label, dash}]                 — horizontal levels
 *   height   plot height in px (default 380)
 */
const GREEN = '#10b981';
const RED = '#ef4444';

export default function CandleChart({ candles = [], overlays = [], markers = [], rails = [], height = 380 }) {
  const [hover, setHover] = useState(null);
  const n = candles.length;

  const geom = useMemo(() => {
    if (!n) return null;
    const step = Math.max(6, Math.min(18, Math.floor(1100 / n)));
    const cw = Math.max(2, step - 6);
    const padL = 6, padR = 78, padT = 12, padB = 26;
    const width = padL + n * step + padR;
    const vals = [];
    candles.forEach((c) => { vals.push(c.high, c.low); });
    overlays.forEach((o) => (o.values || []).forEach((v) => { if (v != null) vals.push(v); }));
    rails.forEach((r) => { if (r.price != null) vals.push(r.price); });
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo) * 0.05 || 1;
    lo -= pad; hi += pad;
    return { step, cw, padL, padR, padT, padB, width, lo, hi,
      y: (p) => padT + ((hi - p) / (hi - lo)) * height,
      xc: (i) => padL + i * step + step / 2 };
  }, [candles, overlays, rails, n, height]);

  if (!n || !geom) {
    return <div className="py-16 text-center text-gray-500 text-sm">No candles to plot.</div>;
  }
  const { step, cw, padL, padT, padB, width, lo, hi, y, xc } = geom;
  const totalH = padT + height + padB;
  const ticks = Array.from({ length: 5 }, (_, k) => lo + ((hi - lo) * k) / 4);

  const pathFor = (values) => {
    let d = '', pen = false;
    values.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      d += `${pen ? 'L' : 'M'}${xc(i)},${y(v)} `;
      pen = true;
    });
    return d.trim();
  };

  return (
    <div className="overflow-x-auto">
      <svg width={width} height={totalH} className="block" style={{ minWidth: '100%' }}>
        {/* price gridlines */}
        {ticks.map((p, k) => (
          <g key={k}>
            <line x1={padL} x2={padL + n * step} y1={y(p)} y2={y(p)} stroke="#ffffff10" />
            <text x={padL + n * step + 6} y={y(p) + 3} fontSize="10" fill="#6b7280">
              {p.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
            </text>
          </g>
        ))}

        {/* horizontal rails (entry / target / stop) */}
        {rails.filter((r) => r.price != null).map((r, k) => (
          <g key={`rail${k}`}>
            <line x1={padL} x2={padL + n * step} y1={y(r.price)} y2={y(r.price)}
              stroke={r.color} strokeWidth="1.2" strokeDasharray={r.dash || '4 3'} />
            <rect x={padL + n * step + 2} y={y(r.price) - 8} width={74} height={15} rx={3} fill={r.color} />
            <text x={padL + n * step + 5} y={y(r.price) + 3} fontSize="9" fill="#04121f" fontWeight="700">
              {r.label} {Number(r.price).toLocaleString('en-IN', { maximumFractionDigits: 1 })}
            </text>
          </g>
        ))}

        {/* candles */}
        {candles.map((c, i) => {
          const up = c.close >= c.open;
          const col = up ? GREEN : RED;
          const top = Math.min(y(c.open), y(c.close));
          const h = Math.max(Math.abs(y(c.open) - y(c.close)), 1);
          return (
            <g key={i}>
              <line x1={xc(i)} x2={xc(i)} y1={y(c.high)} y2={y(c.low)} stroke={col} strokeWidth="1" />
              <rect x={xc(i) - cw / 2} y={top} width={cw} height={h} fill={col} rx={0.5} />
              <rect x={xc(i) - step / 2} y={padT} width={step} height={height} fill="transparent"
                onMouseEnter={() => setHover({ i, c })} onMouseLeave={() => setHover(null)} />
              {i % Math.max(1, Math.round(n / 10)) === 0 && (
                <text x={xc(i)} y={totalH - 8} fontSize="9" fill="#6b7280" textAnchor="middle">{c.t}</text>
              )}
            </g>
          );
        })}

        {/* overlay VWAP lines */}
        {overlays.filter((o) => o.values?.some((v) => v != null)).map((o) => (
          <path key={o.key} d={pathFor(o.values)} fill="none" stroke={o.color}
            strokeWidth="1.6" strokeLinejoin="round" opacity="0.95" />
        ))}

        {/* signal markers */}
        {markers.map((m, k) => (
          m.index >= 0 && m.index < n ? (
            <g key={`mk${k}`}>
              <line x1={xc(m.index)} x2={xc(m.index)} y1={padT} y2={padT + height}
                stroke={m.color} strokeWidth="1" strokeDasharray="3 3" opacity="0.65" />
              <polygon
                points={m.side === 'down'
                  ? `${xc(m.index) - 5},${padT + 2} ${xc(m.index) + 5},${padT + 2} ${xc(m.index)},${padT + 11}`
                  : `${xc(m.index) - 5},${padT + height - 2} ${xc(m.index) + 5},${padT + height - 2} ${xc(m.index)},${padT + height - 11}`}
                fill={m.color} />
            </g>
          ) : null
        ))}

        {/* hover readout */}
        {hover && (
          <g>
            <rect x={Math.min(xc(hover.i) + 8, width - 190)} y={padT + 6} width={182} height={46}
              rx={4} fill="#0f172aee" stroke="#334155" />
            <text x={Math.min(xc(hover.i) + 14, width - 184)} y={padT + 22} fontSize="10" fill="#e2e8f0">
              {hover.c.t} · O {hover.c.open} H {hover.c.high}
            </text>
            <text x={Math.min(xc(hover.i) + 14, width - 184)} y={padT + 36} fontSize="10" fill="#e2e8f0">
              L {hover.c.low} C {hover.c.close}
            </text>
            <text x={Math.min(xc(hover.i) + 14, width - 184)} y={padT + 48} fontSize="9" fill="#94a3b8">
              Vol {Number(hover.c.volume || 0).toLocaleString('en-IN')}
            </text>
          </g>
        )}
      </svg>
    </div>
  );
}
