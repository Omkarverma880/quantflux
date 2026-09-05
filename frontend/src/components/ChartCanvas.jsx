import React, { useRef, useEffect, useState, useCallback } from 'react';

/**
 * A dependency-free candlestick chart on <canvas>.
 *
 * Handles thousands of bars at 60fps, with pan (drag), zoom (wheel), a crosshair
 * readout, stacked sub-panes for volume / cumulative volume / open interest, an
 * overlay rack driven by the server's aligned series, and saved horizontal
 * levels. Nothing here fetches or mutates — it draws what it is given and calls
 * back when the user clicks a price.
 */

export const OVERLAYS = {
  vwap_day: { label: 'VWAP (D)', color: '#22d3ee' },
  vwap_week: { label: 'VWAP (W)', color: '#a78bfa' },
  vwap_month: { label: 'VWAP (M)', color: '#f472b6' },
  pvwap_day: { label: 'Prev-D VWAP', color: '#0ea5e9', dash: [5, 4] },
  pvwap_week: { label: 'Prev-W VWAP', color: '#8b5cf6', dash: [5, 4] },
  pvwap_month: { label: 'Prev-M VWAP', color: '#ec4899', dash: [5, 4] },
  ema_fast: { label: 'EMA fast', color: '#fbbf24' },
  ema_slow: { label: 'EMA slow', color: '#f97316' },
};
const PIVOT_STYLE = {
  pp: { label: 'PP', color: '#94a3b8' },
  r1: { label: 'R1', color: '#f87171' }, r2: { label: 'R2', color: '#ef4444' }, r3: { label: 'R3', color: '#dc2626' },
  s1: { label: 'S1', color: '#4ade80' }, s2: { label: 'S2', color: '#22c55e' }, s3: { label: 'S3', color: '#16a34a' },
};
const SUB = {
  fh_high: { label: 'FH high', color: '#34d399', dash: [4, 3] },
  fh_low: { label: 'FH low', color: '#fb7185', dash: [4, 3] },
  prev_high: { label: 'PDH', color: '#64748b', dash: [2, 3] },
  prev_low: { label: 'PDL', color: '#64748b', dash: [2, 3] },
};
const UP = '#26a69a'; const DOWN = '#ef5350';
const GRID = 'rgba(148,163,184,0.10)'; const AXIS = '#64748b'; const BG = '#0b1220';

const fmtNum = (v, d = 2) => (v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
const compact = (v) => {
  if (v == null) return '—';
  const n = Math.abs(Number(v));
  const s = Number(v) < 0 ? '-' : '';
  if (n >= 1e7) return `${s}${(n / 1e7).toFixed(2)}Cr`;
  if (n >= 1e5) return `${s}${(n / 1e5).toFixed(2)}L`;
  if (n >= 1e3) return `${s}${(n / 1e3).toFixed(1)}K`;
  return `${s}${n.toFixed(0)}`;
};

export default function ChartCanvas({
  candles = [], series = {}, enabled = [], levels = [], height = 620,
  addMode = false, onAddLevel, onCrosshair, ltp,
}) {
  const wrapRef = useRef(null);
  const canvasRef = useRef(null);
  const [size, setSize] = useState({ w: 900, h: height });
  const [view, setView] = useState({ start: 0, count: 180 });
  const [cross, setCross] = useState(null);
  const drag = useRef(null);
  const n = candles.length;

  // keep the viewport pinned to the right edge as new bars arrive
  useEffect(() => {
    setView((v) => {
      const count = Math.min(Math.max(20, v.count), Math.max(20, n));
      return { count, start: Math.max(0, n - count) };
    });
  }, [n]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: height }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: height });
    return () => ro.disconnect();
  }, [height]);

  const has = useCallback((k) => enabled.includes(k), [enabled]);
  const panes = { volume: has('volume') || has('volume_ma'), cv: has('cum_volume'), oi: has('oi') && series.oi };

  const geom = useCallback(() => {
    const padL = 8, padR = 74, padT = 10, padB = 26;
    const subCount = [panes.volume, panes.cv, panes.oi].filter(Boolean).length;
    const subH = subCount ? Math.min(86, Math.max(46, (size.h - padT - padB) * 0.16)) : 0;
    const priceH = size.h - padT - padB - subH * subCount;
    return { padL, padR, padT, padB, subH, priceH, plotW: Math.max(10, size.w - padL - padR) };
  }, [size, panes.volume, panes.cv, panes.oi]);

  // ── draw ──
  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv || !n) return;
    const dpr = window.devicePixelRatio || 1;
    cv.width = size.w * dpr; cv.height = size.h * dpr;
    cv.style.width = `${size.w}px`; cv.style.height = `${size.h}px`;
    const g = cv.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, size.w, size.h);
    g.fillStyle = BG; g.fillRect(0, 0, size.w, size.h);

    const { padL, padR, padT, padB, subH, priceH, plotW } = geom();
    const start = Math.max(0, Math.min(view.start, n - 1));
    const count = Math.max(10, Math.min(view.count, n - start));
    const vis = candles.slice(start, start + count);
    const bw = plotW / count;
    const x = (i) => padL + (i - start) * bw + bw / 2;

    // price scale, including any visible overlay values
    let lo = Infinity, hi = -Infinity;
    vis.forEach((c) => { lo = Math.min(lo, c.l); hi = Math.max(hi, c.h); });
    const scan = (arr) => { if (!arr) return; for (let i = start; i < start + count; i += 1) { const v = arr[i]; if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } } };
    Object.keys(OVERLAYS).forEach((k) => { if (has(k)) scan(series[k]); });
    if (has('pivots') && series.pivots) Object.values(series.pivots).forEach(scan);
    if (has('first_hour') && series.first_hour) Object.values(series.first_hour).forEach(scan);
    if (has('prev_day_hl') && series.prev_day_hl) Object.values(series.prev_day_hl).forEach(scan);
    if (has('fourth_candle') && series.fourth_candle) { scan(series.fourth_candle.high); scan(series.fourth_candle.low); }
    if (has('hammer') && series.hammer) scan(series.hammer.trigger);
    levels.forEach((l) => { if (l.price) { lo = Math.min(lo, l.price); hi = Math.max(hi, l.price); } });
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return;
    const padP = (hi - lo) * 0.06 || 1; lo -= padP; hi += padP;
    const y = (p) => padT + ((hi - p) / (hi - lo)) * priceH;

    // grid + price axis
    g.font = '10px ui-sans-serif, system-ui';
    g.textBaseline = 'middle';
    const ticks = 6;
    for (let i = 0; i <= ticks; i += 1) {
      const p = lo + ((hi - lo) * i) / ticks;
      const yy = y(p);
      g.strokeStyle = GRID; g.lineWidth = 1;
      g.beginPath(); g.moveTo(padL, yy); g.lineTo(padL + plotW, yy); g.stroke();
      g.fillStyle = AXIS; g.textAlign = 'left';
      g.fillText(fmtNum(p, p > 500 ? 0 : 2), padL + plotW + 6, yy);
    }
    // time axis
    const step = Math.max(1, Math.floor(count / 8));
    g.textAlign = 'center';
    for (let i = start; i < start + count; i += step) {
      const t = candles[i]?.t || '';
      const short = t.length > 10 ? t.slice(5).replace(' ', ' ') : t;
      g.strokeStyle = GRID;
      g.beginPath(); g.moveTo(x(i), padT); g.lineTo(x(i), padT + priceH); g.stroke();
      g.fillStyle = AXIS;
      g.fillText(short, x(i), size.h - padB / 2);
    }

    // overlays (below the candles)
    const line = (arr, color, dash) => {
      if (!arr) return;
      g.save(); g.strokeStyle = color; g.lineWidth = 1.3;
      if (dash) g.setLineDash(dash);
      g.beginPath();
      let pen = false;
      for (let i = start; i < start + count; i += 1) {
        const v = arr[i];
        if (v == null) { pen = false; continue; }
        const px = x(i); const py = y(v);
        if (!pen) { g.moveTo(px, py); pen = true; } else g.lineTo(px, py);
      }
      g.stroke(); g.restore();
    };
    Object.entries(OVERLAYS).forEach(([k, s]) => { if (has(k)) line(series[k], s.color, s.dash); });
    if (has('pivots') && series.pivots) Object.entries(PIVOT_STYLE).forEach(([k, s]) => line(series.pivots[k], s.color, [6, 4]));
    if (has('first_hour') && series.first_hour) { line(series.first_hour.fh_high, SUB.fh_high.color, SUB.fh_high.dash); line(series.first_hour.fh_low, SUB.fh_low.color, SUB.fh_low.dash); }
    if (has('prev_day_hl') && series.prev_day_hl) { line(series.prev_day_hl.prev_high, SUB.prev_high.color, SUB.prev_high.dash); line(series.prev_day_hl.prev_low, SUB.prev_low.color, SUB.prev_low.dash); }
    if (has('fourth_candle') && series.fourth_candle) { line(series.fourth_candle.high, '#10b981', [2, 3]); line(series.fourth_candle.low, '#ef4444', [2, 3]); }
    if (has('hammer') && series.hammer) line(series.hammer.trigger, '#eab308', [3, 3]);

    // candles
    const body = Math.max(1, Math.min(14, bw * 0.68));
    for (let i = start; i < start + count; i += 1) {
      const c = candles[i];
      const col = c.c >= c.o ? UP : DOWN;
      g.strokeStyle = col; g.fillStyle = col; g.lineWidth = 1;
      const px = Math.round(x(i)) + 0.5;
      g.beginPath(); g.moveTo(px, y(c.h)); g.lineTo(px, y(c.l)); g.stroke();
      const top = Math.min(y(c.o), y(c.c));
      const hgt = Math.max(1, Math.abs(y(c.o) - y(c.c)));
      g.fillRect(px - body / 2, top, body, hgt);
    }

    // strategy marks
    const drawMarks = (marks, color) => {
      if (!marks) return;
      g.save(); g.fillStyle = color; g.font = 'bold 9px ui-sans-serif, system-ui'; g.textAlign = 'center';
      marks.forEach((m) => {
        const i = m.idx;
        if (i < start || i >= start + count) return;
        const c = candles[i];
        const yy = y(c.l) + 12;
        g.beginPath(); g.moveTo(x(i), y(c.l) + 3); g.lineTo(x(i) - 4, y(c.l) + 9); g.lineTo(x(i) + 4, y(c.l) + 9); g.closePath(); g.fill();
        g.fillText(m.type, x(i), yy + 8);
      });
      g.restore();
    };
    if (has('fourth_candle')) drawMarks(series.fourth_candle?.marks, '#22d3ee');
    if (has('hammer')) drawMarks(series.hammer?.marks, '#eab308');

    // saved levels
    levels.forEach((l) => {
      if (!l.price) return;
      const yy = y(l.price);
      if (yy < padT - 4 || yy > padT + priceH + 4) return;
      const hot = l.state === 'TOUCHED' || l.state === 'NEAR';
      g.save();
      g.strokeStyle = l.color || '#f59e0b'; g.lineWidth = hot ? 1.8 : 1.2; g.setLineDash(hot ? [] : [7, 4]);
      g.beginPath(); g.moveTo(padL, yy); g.lineTo(padL + plotW, yy); g.stroke();
      g.setLineDash([]);
      const text = `${l.label || 'level'} ${fmtNum(l.price)}`;
      g.font = 'bold 9px ui-sans-serif, system-ui';
      const w = g.measureText(text).width + 10;
      g.fillStyle = l.color || '#f59e0b';
      g.fillRect(padL + 2, yy - 7, w, 14);
      g.fillStyle = '#04121f'; g.textAlign = 'left'; g.textBaseline = 'middle';
      g.fillText(text, padL + 7, yy);
      g.restore();
    });

    // last price tag
    if (ltp != null) {
      const yy = y(ltp);
      if (yy > padT && yy < padT + priceH) {
        g.save();
        g.strokeStyle = '#38bdf8'; g.lineWidth = 1; g.setLineDash([2, 3]);
        g.beginPath(); g.moveTo(padL, yy); g.lineTo(padL + plotW, yy); g.stroke();
        g.setLineDash([]);
        g.fillStyle = '#38bdf8'; g.fillRect(padL + plotW + 2, yy - 8, padR - 6, 16);
        g.fillStyle = '#04121f'; g.font = 'bold 10px ui-sans-serif, system-ui'; g.textAlign = 'left';
        g.fillText(fmtNum(ltp, ltp > 500 ? 0 : 2), padL + plotW + 6, yy);
        g.restore();
      }
    }

    // sub-panes
    let top = padT + priceH;
    const paneBox = (title) => {
      g.strokeStyle = GRID; g.beginPath(); g.moveTo(padL, top); g.lineTo(padL + plotW, top); g.stroke();
      g.fillStyle = AXIS; g.font = '9px ui-sans-serif, system-ui'; g.textAlign = 'left';
      g.fillText(title, padL + 4, top + 9);
    };
    if (panes.volume) {
      paneBox('Volume');
      let vmax = 0;
      for (let i = start; i < start + count; i += 1) vmax = Math.max(vmax, (series.volume ? series.volume[i] : candles[i].v) || 0);
      vmax = vmax || 1;
      for (let i = start; i < start + count; i += 1) {
        const v = (series.volume ? series.volume[i] : candles[i].v) || 0;
        const hgt = (v / vmax) * (subH - 12);
        g.fillStyle = candles[i].c >= candles[i].o ? 'rgba(38,166,154,0.55)' : 'rgba(239,83,80,0.55)';
        g.fillRect(x(i) - body / 2, top + subH - hgt, body, hgt);
      }
      if (has('volume_ma') && series.volume_ma) {
        g.save(); g.strokeStyle = '#eab308'; g.lineWidth = 1.2; g.beginPath();
        let pen = false;
        for (let i = start; i < start + count; i += 1) {
          const v = series.volume_ma[i];
          if (v == null) { pen = false; continue; }
          const py = top + subH - (v / vmax) * (subH - 12);
          if (!pen) { g.moveTo(x(i), py); pen = true; } else g.lineTo(x(i), py);
        }
        g.stroke(); g.restore();
      }
      g.fillStyle = AXIS; g.textAlign = 'left';
      g.fillText(compact(vmax), padL + plotW + 6, top + 10);
      top += subH;
    }
    if (panes.cv && series.cum_volume) {
      paneBox('Cumulative volume');
      let mn = Infinity, mx = -Infinity;
      for (let i = start; i < start + count; i += 1) { const v = series.cum_volume[i]; if (v != null) { mn = Math.min(mn, v); mx = Math.max(mx, v); } }
      if (mn === mx) { mx = mn + 1; }
      const yy = (v) => top + subH - ((v - mn) / (mx - mn)) * (subH - 12);
      g.save(); g.strokeStyle = '#38bdf8'; g.lineWidth = 1.4; g.beginPath();
      let pen = false;
      for (let i = start; i < start + count; i += 1) {
        const v = series.cum_volume[i];
        if (v == null) { pen = false; continue; }
        if (!pen) { g.moveTo(x(i), yy(v)); pen = true; } else g.lineTo(x(i), yy(v));
      }
      g.stroke(); g.restore();
      g.fillStyle = AXIS; g.fillText(compact(mx), padL + plotW + 6, top + 10);
      top += subH;
    }
    if (panes.oi && series.oi) {
      paneBox('Open interest');
      let mn = Infinity, mx = -Infinity;
      for (let i = start; i < start + count; i += 1) { const v = series.oi[i]; if (v != null) { mn = Math.min(mn, v); mx = Math.max(mx, v); } }
      if (mn === mx) { mx = mn + 1; }
      const yy = (v) => top + subH - ((v - mn) / (mx - mn)) * (subH - 12);
      g.save(); g.strokeStyle = '#c084fc'; g.lineWidth = 1.4; g.beginPath();
      let pen = false;
      for (let i = start; i < start + count; i += 1) {
        const v = series.oi[i];
        if (v == null) { pen = false; continue; }
        if (!pen) { g.moveTo(x(i), yy(v)); pen = true; } else g.lineTo(x(i), yy(v));
      }
      g.stroke(); g.restore();
      g.fillStyle = AXIS; g.fillText(compact(mx), padL + plotW + 6, top + 10);
      top += subH;
    }

    // crosshair
    if (cross && cross.i >= start && cross.i < start + count) {
      g.save();
      g.strokeStyle = 'rgba(148,163,184,0.5)'; g.setLineDash([3, 3]); g.lineWidth = 1;
      g.beginPath(); g.moveTo(x(cross.i), padT); g.lineTo(x(cross.i), size.h - padB); g.stroke();
      if (cross.y > padT && cross.y < padT + priceH) {
        g.beginPath(); g.moveTo(padL, cross.y); g.lineTo(padL + plotW, cross.y); g.stroke();
        g.setLineDash([]);
        const p = hi - ((cross.y - padT) / priceH) * (hi - lo);
        g.fillStyle = '#334155'; g.fillRect(padL + plotW + 2, cross.y - 8, padR - 6, 16);
        g.fillStyle = '#e2e8f0'; g.font = '10px ui-sans-serif, system-ui'; g.textAlign = 'left';
        g.fillText(fmtNum(p, p > 500 ? 0 : 2), padL + plotW + 6, cross.y);
      }
      g.restore();
    }
  }, [candles, series, enabled, levels, view, size, cross, ltp, geom, has, panes.volume, panes.cv, panes.oi, n]);

  // ── interaction ──
  const idxAt = (clientX) => {
    const cv = canvasRef.current;
    if (!cv) return 0;
    const rect = cv.getBoundingClientRect();
    const { padL, plotW } = geom();
    const rel = clientX - rect.left - padL;
    const bw = plotW / view.count;
    return Math.max(0, Math.min(n - 1, view.start + Math.floor(rel / bw)));
  };
  const priceAt = (clientY) => {
    const cv = canvasRef.current;
    if (!cv) return null;
    const rect = cv.getBoundingClientRect();
    const { padT, priceH } = geom();
    const relY = clientY - rect.top;
    if (relY < padT || relY > padT + priceH) return null;
    const start = view.start; const count = Math.min(view.count, n - start);
    const vis = candles.slice(start, start + count);
    if (!vis.length) return null;
    let lo = Infinity, hi = -Infinity;
    vis.forEach((c) => { lo = Math.min(lo, c.l); hi = Math.max(hi, c.h); });
    levels.forEach((l) => { if (l.price) { lo = Math.min(lo, l.price); hi = Math.max(hi, l.price); } });
    const padP = (hi - lo) * 0.06 || 1; lo -= padP; hi += padP;
    return hi - ((relY - padT) / priceH) * (hi - lo);
  };

  const onWheel = (e) => {
    e.preventDefault();
    const at = idxAt(e.clientX);
    const factor = e.deltaY > 0 ? 1.15 : 1 / 1.15;
    setView((v) => {
      const count = Math.max(20, Math.min(n, Math.round(v.count * factor)));
      const ratio = (at - v.start) / Math.max(1, v.count);
      const start = Math.max(0, Math.min(n - count, Math.round(at - ratio * count)));
      return { start, count };
    });
  };
  const onDown = (e) => { drag.current = { x: e.clientX, start: view.start }; };
  const onUp = () => { drag.current = null; };
  const onMove = (e) => {
    const i = idxAt(e.clientX);
    const cv = canvasRef.current;
    const rect = cv.getBoundingClientRect();
    setCross({ i, y: e.clientY - rect.top });
    onCrosshair?.(candles[i], i);
    if (drag.current) {
      const { padL, plotW } = geom();
      const bw = plotW / view.count;
      const shift = Math.round((e.clientX - drag.current.x) / bw);
      setView((v) => ({ ...v, start: Math.max(0, Math.min(n - v.count, drag.current.start - shift)) }));
    }
  };
  const onLeave = () => { setCross(null); drag.current = null; onCrosshair?.(null); };
  const onClick = (e) => {
    if (!addMode) return;
    const p = priceAt(e.clientY);
    if (p != null) onAddLevel?.(Math.round(p * 100) / 100);
  };

  return (
    <div ref={wrapRef} className="w-full relative select-none" style={{ height }}>
      <canvas
        ref={canvasRef}
        className={`block rounded-lg ${addMode ? 'cursor-crosshair' : 'cursor-grab active:cursor-grabbing'}`}
        onWheel={onWheel} onMouseDown={onDown} onMouseUp={onUp} onMouseMove={onMove}
        onMouseLeave={onLeave} onClick={onClick}
      />
      {!n && <div className="absolute inset-0 flex items-center justify-center text-gray-600 text-sm">No candles</div>}
    </div>
  );
}

export { fmtNum, compact };
