import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Maximize2, Minimize2, CandlestickChart, Activity, BarChart3, Ruler } from 'lucide-react';

/**
 * The working chart: candles or a line, volume underneath, the ceiling and base drawn on it, a
 * crosshair that reads out the bar under the pointer, wheel zoom, drag to pan, log or linear
 * scale, and the moving averages on a switch. Plain SVG — no chart library.
 *
 * Everything is derived from the visible window [a, b), so zooming and panning only change those
 * two numbers and the rest follows.
 */
const UP = '#10b981';
const DOWN = '#ef4444';
const MA_COLOURS = { sma50: '#60a5fa', sma150: '#f59e0b', sma200: '#a78bfa' };
const fmt = (v, d = 2) => (v == null || Number.isNaN(v) ? '—'
  : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
const vol = (v) => (v >= 1e7 ? `${(v / 1e7).toFixed(2)}Cr` : v >= 1e5 ? `${(v / 1e5).toFixed(2)}L` : `${Math.round(v / 1000)}K`);

export default function ProChart({ chart, height = 380, onFullscreen, fullscreen = false }) {
  const candles = chart?.candles || [];
  const n = candles.length;
  const [win, setWin] = useState([0, n]);
  const [type, setType] = useState('candles');
  const [logScale, setLogScale] = useState(false);
  const [showVol, setShowVol] = useState(true);
  const [mas, setMas] = useState({ sma50: true, sma150: true, sma200: true });
  const [hover, setHover] = useState(null);
  const drag = useRef(null);
  const svgRef = useRef(null);

  useEffect(() => { setWin([0, n]); }, [n, chart?.symbol, chart?.timeframe]);

  const [a, b] = [Math.max(0, Math.min(win[0], n - 10)), Math.min(n, Math.max(win[1], 10))];
  const view = useMemo(() => candles.slice(a, b), [candles, a, b]);
  const W = 1000;
  const padR = 56, padB = 18;
  const volH = showVol ? Math.round(height * 0.18) : 0;
  const priceH = height - volH - padB;

  const lo = Math.min(...view.map((k) => k.l), chart?.pivot ?? Infinity);
  const hi = Math.max(...view.map((k) => k.h), chart?.pivot ?? -Infinity);
  const pad = (hi - lo) * 0.06 || 1;
  const yMin = Math.max(logScale ? 0.01 : -Infinity, lo - pad);
  const yMax = hi + pad;
  const y = useCallback((v) => {
    if (v == null || Number.isNaN(v)) return null;
    if (logScale) {
      const l0 = Math.log(Math.max(yMin, 0.01)), l1 = Math.log(yMax);
      return priceH - ((Math.log(Math.max(v, 0.01)) - l0) / (l1 - l0)) * priceH;
    }
    return priceH - ((v - yMin) / (yMax - yMin)) * priceH;
  }, [logScale, yMin, yMax, priceH]);
  const step = (W - padR) / Math.max(view.length, 1);
  const x = (i) => i * step + step / 2;
  const bw = Math.max(1, Math.min(9, step * 0.64));
  const vMax = Math.max(...view.map((k) => k.v), 1);

  const line = (key) => {
    const src = chart?.[key];
    if (!src) return '';
    return src.slice(a, b).map((v, i) => (v == null ? null : [x(i), y(v)]))
      .filter(Boolean).map((pt, i) => `${i ? 'L' : 'M'}${pt[0].toFixed(1)},${pt[1].toFixed(1)}`).join(' ');
  };
  const closeLine = view.map((k, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(k.c).toFixed(1)}`).join(' ');

  const toIndex = (clientX) => {
    const r = svgRef.current?.getBoundingClientRect();
    if (!r) return null;
    const px = ((clientX - r.left) / r.width) * W;
    return Math.max(0, Math.min(view.length - 1, Math.round((px - step / 2) / step)));
  };
  const onMove = (e) => {
    if (drag.current != null) {
      const r = svgRef.current.getBoundingClientRect();
      const moved = Math.round(((drag.current.x - e.clientX) / r.width) * W / step);
      const width = drag.current.b - drag.current.a;
      let na = drag.current.a + moved;
      na = Math.max(0, Math.min(n - width, na));
      setWin([na, na + width]);
      return;
    }
    const i = toIndex(e.clientX);
    setHover(i == null ? null : { i, k: view[i] });
  };
  const zoom = useCallback((factor, clientX) => {
    const i = (clientX != null ? toIndex(clientX) : null) ?? Math.floor(view.length / 2);
    const anchor = a + i;
    const width = b - a;
    const next = Math.max(20, Math.min(n, Math.round(width * factor)));
    let na = Math.round(anchor - (i / Math.max(view.length, 1)) * next);
    na = Math.max(0, Math.min(n - next, na));
    setWin([na, na + next]);
  }, [a, b, n, view.length, step]);

  useEffect(() => {
    const el = svgRef.current;
    if (!el) return undefined;
    const handler = (e) => { e.preventDefault(); zoom(e.deltaY > 0 ? 1.18 : 0.85, e.clientX); };
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, [zoom]);

  const h = hover?.k;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => (logScale
    ? Math.exp(Math.log(Math.max(yMin, 0.01)) + f * (Math.log(yMax) - Math.log(Math.max(yMin, 0.01))))
    : yMin + f * (yMax - yMin)));
  const dateIdx = [0, Math.floor(view.length / 3), Math.floor((2 * view.length) / 3), view.length - 1];

  const Btn = ({ on, onClick, children, title }) => (
    <button title={title} onClick={onClick}
      className={`px-1.5 py-1 rounded text-[11px] flex items-center gap-1 border ${on
        ? 'border-brand-500/60 bg-brand-500/15 text-brand-300' : 'border-surface-3 text-gray-400 hover:text-gray-200'}`}>
      {children}
    </button>
  );

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <Btn on={type === 'candles'} onClick={() => setType(type === 'candles' ? 'line' : 'candles')}
          title="Candles or a closing line">
          {type === 'candles' ? <CandlestickChart className="w-3.5 h-3.5" /> : <Activity className="w-3.5 h-3.5" />}
          {type === 'candles' ? 'Candles' : 'Line'}
        </Btn>
        <Btn on={logScale} onClick={() => setLogScale(!logScale)} title="Log scale spaces equal percentage moves equally">
          <Ruler className="w-3.5 h-3.5" />{logScale ? 'Log' : 'Linear'}
        </Btn>
        <Btn on={showVol} onClick={() => setShowVol(!showVol)} title="Volume panel"><BarChart3 className="w-3.5 h-3.5" />Volume</Btn>
        {Object.keys(MA_COLOURS).map((k) => (
          <Btn key={k} on={mas[k]} onClick={() => setMas({ ...mas, [k]: !mas[k] })} title={`${k.replace('sma', '')}-day average`}>
            <span className="w-3 h-px inline-block" style={{ background: MA_COLOURS[k] }} />{k.replace('sma', '')}
          </Btn>
        ))}
        <Btn on={false} onClick={() => zoom(0.7)} title="Zoom in (or scroll on the chart)">+</Btn>
        <Btn on={false} onClick={() => zoom(1.45)} title="Zoom out">−</Btn>
        <Btn on={false} onClick={() => setWin([0, n])} title="Show everything again">Reset</Btn>
        {onFullscreen && (
          <Btn on={fullscreen} onClick={onFullscreen} title="Full screen">
            {fullscreen ? <Minimize2 className="w-3.5 h-3.5" /> : <Maximize2 className="w-3.5 h-3.5" />}
          </Btn>
        )}
        <span className="ml-auto text-[10.5px] text-gray-500">
          {view.length} of {n} bars · scroll to zoom, drag to pan
        </span>
      </div>

      <div className="relative">
        {h && (
          <div className="absolute left-2 top-1 z-10 text-[10.5px] mono text-gray-300 bg-surface-1/85 rounded px-1.5 py-0.5 pointer-events-none">
            {h.d} · O {fmt(h.o)} H {fmt(h.h)} L {fmt(h.l)} C {fmt(h.c)}
            <span className={h.c >= h.o ? 'text-emerald-400' : 'text-red-400'}> ({fmt(((h.c / h.o) - 1) * 100, 2)}%)</span>
            {showVol && <> · Vol {vol(h.v)}</>}
          </div>
        )}
        <svg ref={svgRef} viewBox={`0 0 ${W} ${height}`} className="w-full select-none" style={{ height }}
          preserveAspectRatio="none" onMouseMove={onMove} onMouseLeave={() => { setHover(null); drag.current = null; }}
          onMouseDown={(e) => { drag.current = { x: e.clientX, a, b }; }} onMouseUp={() => { drag.current = null; }}>
          {ticks.map((t, i) => (
            <g key={`g${i}`}>
              <line x1="0" x2={W - padR} y1={y(t)} y2={y(t)} stroke="#ffffff10" strokeWidth="1" />
              <text x={W - padR + 4} y={Math.min(priceH - 1, Math.max(8, y(t) + 3))} fontSize="10" fill="#6b7280">{fmt(t, 0)}</text>
            </g>
          ))}

          {chart?.base_from != null && chart.base_from >= a && chart.base_to <= b && (() => {
            const w = candles.slice(chart.base_from, chart.base_to + 1);
            const bHi = Math.max(...w.map((k) => k.h)), bLo = Math.min(...w.map((k) => k.l));
            return (
              <g>
                <rect x={x(chart.base_from - a) - bw} y={y(bHi) - 2}
                  width={(chart.base_to - chart.base_from) * step + bw * 2}
                  height={Math.max(4, y(bLo) - y(bHi) + 4)} fill="#10b98112" stroke="#10b98155" rx="2" />
                <text x={x(chart.base_from - a)} y={Math.max(9, y(bHi) - 5)} fontSize="10" fill="#34d399">
                  {((chart.base_to - chart.base_from + 1) / 5).toFixed(1)} wks
                </text>
              </g>
            );
          })()}
          {chart?.pivot != null && (
            <>
              <line x1="0" x2={W - padR} y1={y(chart.pivot)} y2={y(chart.pivot)} stroke="#10b981" strokeDasharray="5 4" opacity="0.85" />
              <text x="4" y={Math.max(10, y(chart.pivot) - 3)} fontSize="10" fill="#34d399" fontWeight="700">
                ceiling {fmt(chart.pivot)}
              </text>
            </>
          )}
          {chart?.stop != null && chart.stop > yMin && (
            <line x1="0" x2={W - padR} y1={y(chart.stop)} y2={y(chart.stop)} stroke="#ef4444" strokeDasharray="4 4" opacity="0.55" />
          )}

          {Object.entries(MA_COLOURS).filter(([k]) => mas[k]).map(([k, col]) => {
            const d = line(k);
            return d ? <path key={k} d={d} fill="none" stroke={col} strokeWidth="1.2" opacity="0.7" /> : null;
          })}

          {type === 'line' ? (
            <path d={closeLine} fill="none" stroke="#38bdf8" strokeWidth="1.5" />
          ) : view.map((k, i) => {
            const up = k.c >= k.o;
            const col = up ? UP : DOWN;
            return (
              <g key={i}>
                <line x1={x(i)} x2={x(i)} y1={y(k.h)} y2={y(k.l)} stroke={col} strokeWidth="1" opacity="0.9" />
                <rect x={x(i) - bw / 2} y={y(Math.max(k.o, k.c))} width={bw}
                  height={Math.max(1, Math.abs(y(k.o) - y(k.c)))} fill={col} opacity="0.92" />
              </g>
            );
          })}

          {(chart?.flags || []).filter((f) => f.i >= a && f.i < b).map((f, i) => (
            <g key={`f${i}`} transform={`translate(${x(f.i - a)}, 4)`}>
              <line x1="0" x2="0" y1="0" y2="10" stroke="#f59e0b" strokeWidth="1.2" />
              <path d="M0,0 L8,3 L0,6 Z" fill="#f59e0b" />
              <title>breakout {f.date}</title>
            </g>
          ))}

          {showVol && (
            <g transform={`translate(0, ${priceH + 6})`}>
              {view.map((k, i) => (
                <rect key={`v${i}`} x={x(i) - bw / 2} y={volH - (k.v / vMax) * volH} width={bw}
                  height={Math.max(0.5, (k.v / vMax) * volH)} fill={k.c >= k.o ? UP : DOWN} opacity="0.32" />
              ))}
            </g>
          )}

          {hover && (
            <g pointerEvents="none">
              <line x1={x(hover.i)} x2={x(hover.i)} y1="0" y2={priceH + volH + 4} stroke="#ffffff33" strokeDasharray="3 3" />
              <line x1="0" x2={W - padR} y1={y(hover.k.c)} y2={y(hover.k.c)} stroke="#ffffff22" strokeDasharray="3 3" />
              <rect x={W - padR} y={y(hover.k.c) - 8} width={padR} height="15" fill="#1f2937" />
              <text x={W - padR + 4} y={y(hover.k.c) + 3} fontSize="10" fill="#e5e7eb">{fmt(hover.k.c, 1)}</text>
            </g>
          )}

          {dateIdx.map((i, k) => view[i] && (
            <text key={`d${k}`} x={Math.min(W - padR - 34, Math.max(2, x(i) - 20))} y={height - 4} fontSize="9.5" fill="#6b7280">
              {view[i].d}
            </text>
          ))}
        </svg>
      </div>
    </div>
  );
}
