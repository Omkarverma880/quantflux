import React, {
  useRef, useEffect, useState, useCallback, forwardRef, useImperativeHandle,
} from 'react';

/**
 * A dependency-free candlestick chart on <canvas>.
 *
 * Pan by dragging, zoom the time axis with the wheel, zoom the price axis by
 * dragging it (or wheeling over it), double-click to auto-fit. Levels and text
 * notes are draggable; the crosshair reports the bar under the cursor. Nothing
 * here fetches or mutates — it draws what it is given and calls back.
 */

export const DEFAULT_COLORS = {
  vwap_day: '#22d3ee', vwap_week: '#a78bfa', vwap_month: '#f472b6',
  pvwap_day: '#0ea5e9', pvwap_week: '#8b5cf6', pvwap_month: '#ec4899',
  ema_fast: '#fbbf24', ema_slow: '#f97316',
  pivots: '#94a3b8', first_hour: '#34d399', first_hour_prev: '#2dd4bf',
  first_hour_stats: '#facc15', prev_day_hl: '#64748b',
  fourth_candle: '#22d3ee', hammer: '#eab308',
  volume: '#26a69a', volume_ma: '#eab308', cum_volume: '#38bdf8', oi: '#c084fc',
};
export const OVERLAYS = {
  vwap_day: { label: 'VWAP (D)' }, vwap_week: { label: 'VWAP (W)' }, vwap_month: { label: 'VWAP (M)' },
  pvwap_day: { label: 'Prev-D VWAP', dash: [5, 4] },
  pvwap_week: { label: 'Prev-W VWAP', dash: [5, 4] },
  pvwap_month: { label: 'Prev-M VWAP', dash: [5, 4] },
  ema_fast: { label: 'EMA fast' }, ema_slow: { label: 'EMA slow' },
};
const PIVOT_KEYS = ['pp', 'r1', 'r2', 'r3', 's1', 's2', 's3'];
const MULTI = {                        // key → the sub-series it draws, with a suffix label
  first_hour: [['fh_high', 'FH H'], ['fh_low', 'FH L']],
  first_hour_prev: [['fhp_high', 'PFH H'], ['fhp_low', 'PFH L']],
  first_hour_stats: [['fhs_max_high', 'FH max'], ['fhs_min_low', 'FH min'],
    ['fhs_avg_high', 'FH avg H'], ['fhs_avg_low', 'FH avg L']],
  prev_day_hl: [['prev_high', 'PDH'], ['prev_low', 'PDL']],
};
const UP = '#26a69a'; const DOWN = '#ef5350';
const GRID = 'rgba(148,163,184,0.10)'; const AXIS = '#64748b'; const BG = '#0b1220';
const PANE_BG = 'rgba(148,163,184,0.04)';

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

const ChartCanvas = forwardRef(function ChartCanvas({
  candles = [], series = {}, enabled = [], levels = [], colors = {}, height = 620,
  addMode = null, onAddLevel, onMoveLevel, onCrosshair, ltp,
}, ref) {
  const wrapRef = useRef(null);
  const canvasRef = useRef(null);
  const [size, setSize] = useState({ w: 900, h: height });
  const [view, setView] = useState({ start: 0, count: 180 });
  const [yz, setYz] = useState({ mult: 1, shift: 0 });     // price-axis zoom / offset
  const [cross, setCross] = useState(null);
  const [drift, setDrift] = useState(null);                // level being dragged
  const pan = useRef(null);
  const axis = useRef(null);
  const grab = useRef(null);
  const pinned = useRef(true);                             // stuck to the right edge?
  const n = candles.length;
  const col = useCallback((k) => colors[k] || DEFAULT_COLORS[k] || '#94a3b8', [colors]);

  useImperativeHandle(ref, () => ({
    fit: () => { setYz({ mult: 1, shift: 0 }); setView((v) => ({ count: v.count, start: Math.max(0, n - v.count) })); pinned.current = true; },
    goLive: () => { setView((v) => ({ count: v.count, start: Math.max(0, n - v.count) })); pinned.current = true; },
    lastPrice: () => (n ? candles[n - 1].c : null),
    crosshairPrice: () => cross?.price ?? null,
  }), [n, candles, cross]);

  // A refresh brings new bars: keep the right edge only if the user is already
  // there — never yank the viewport back while they are reading history.
  useEffect(() => {
    setView((v) => {
      const count = Math.max(20, Math.min(v.count, Math.max(20, n)));
      if (pinned.current) return { count, start: Math.max(0, n - count) };
      return { count, start: Math.max(0, Math.min(v.start, n - count)) };
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
  const panes = {
    volume: has('volume') || has('volume_ma'),
    cv: has('cum_volume') && series.cum_volume,
    oi: has('oi') && series.oi,
  };

  const geom = useCallback(() => {
    const padL = 8, padR = 78, padT = 10, padB = 24;
    const subCount = [panes.volume, panes.cv, panes.oi].filter(Boolean).length;
    const avail = size.h - padT - padB;
    const subH = subCount ? Math.max(52, Math.min(96, avail * 0.18)) : 0;
    const priceH = Math.max(120, avail - subH * subCount);
    return { padL, padR, padT, padB, subH, priceH, plotW: Math.max(10, size.w - padL - padR) };
  }, [size, panes.volume, panes.cv, panes.oi]);

  // price range for the current viewport, with the manual zoom applied
  const range = useCallback(() => {
    const start = Math.max(0, Math.min(view.start, Math.max(0, n - 1)));
    const count = Math.max(10, Math.min(view.count, n - start));
    let lo = Infinity, hi = -Infinity;
    for (let i = start; i < start + count; i += 1) {
      const c = candles[i];
      if (!c) continue;
      lo = Math.min(lo, c.l); hi = Math.max(hi, c.h);
    }
    const scan = (arr) => { if (!arr) return; for (let i = start; i < start + count; i += 1) { const v = arr[i]; if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } } };
    Object.keys(OVERLAYS).forEach((k) => { if (has(k)) scan(series[k]); });
    if (has('pivots') && series.pivots) PIVOT_KEYS.forEach((k) => scan(series.pivots[k]));
    Object.entries(MULTI).forEach(([k, subs]) => { if (has(k) && series[k]) subs.forEach(([sk]) => scan(series[k][sk])); });
    if (has('fourth_candle') && series.fourth_candle) { scan(series.fourth_candle.high); scan(series.fourth_candle.low); }
    if (has('hammer') && series.hammer) scan(series.hammer.trigger);
    levels.forEach((l) => { if (l.price) { lo = Math.min(lo, Number(l.price)); hi = Math.max(hi, Number(l.price)); } });
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
    const pad = (hi - lo) * 0.06 || 1;
    lo -= pad; hi += pad;
    const mid = (lo + hi) / 2 + yz.shift * (hi - lo);
    const half = ((hi - lo) / 2) * yz.mult;
    return { lo: mid - half, hi: mid + half, start, count };
  }, [candles, series, levels, view, yz, n, has]);

  // ── draw ──
  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv || !n) return;
    const r = range();
    if (!r) return;
    const dpr = window.devicePixelRatio || 1;
    cv.width = size.w * dpr; cv.height = size.h * dpr;
    cv.style.width = `${size.w}px`; cv.style.height = `${size.h}px`;
    const g = cv.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = BG; g.fillRect(0, 0, size.w, size.h);

    const { padL, padR, padT, padB, subH, priceH, plotW } = geom();
    const { lo, hi, start, count } = r;
    const bw = plotW / count;
    const x = (i) => padL + (i - start) * bw + bw / 2;
    const y = (p) => padT + ((hi - p) / (hi - lo)) * priceH;
    const dec = hi > 500 ? 1 : 2;

    g.font = '10px ui-sans-serif, system-ui';
    g.textBaseline = 'middle';
    for (let i = 0; i <= 6; i += 1) {
      const p = lo + ((hi - lo) * i) / 6;
      const yy = y(p);
      g.strokeStyle = GRID; g.lineWidth = 1;
      g.beginPath(); g.moveTo(padL, yy); g.lineTo(padL + plotW, yy); g.stroke();
      g.fillStyle = AXIS; g.textAlign = 'left';
      g.fillText(fmtNum(p, dec), padL + plotW + 6, yy);
    }
    const step = Math.max(1, Math.floor(count / 8));
    g.textAlign = 'center';
    for (let i = start; i < start + count; i += step) {
      const t = candles[i]?.t || '';
      g.strokeStyle = GRID;
      g.beginPath(); g.moveTo(x(i), padT); g.lineTo(x(i), padT + priceH); g.stroke();
      g.fillStyle = AXIS;
      g.fillText(t.length > 10 ? t.slice(5) : t, x(i), size.h - padB / 2);
    }

    const line = (arr, color, dash, label) => {
      if (!arr) return;
      g.save(); g.strokeStyle = color; g.lineWidth = 1.4;
      if (dash) g.setLineDash(dash);
      g.beginPath();
      let pen = false; let lastY = null;
      for (let i = start; i < start + count; i += 1) {
        const v = arr[i];
        if (v == null) { pen = false; continue; }
        const py = y(v);
        lastY = py;
        if (!pen) { g.moveTo(x(i), py); pen = true; } else g.lineTo(x(i), py);
      }
      g.stroke();
      if (label && lastY != null && lastY > padT && lastY < padT + priceH) {
        g.setLineDash([]); g.font = '9px ui-sans-serif, system-ui'; g.textAlign = 'right';
        g.fillStyle = color; g.globalAlpha = 0.9;
        g.fillText(label, padL + plotW - 3, lastY - 6);
      }
      g.restore();
    };
    Object.entries(OVERLAYS).forEach(([k, s]) => { if (has(k)) line(series[k], col(k), s.dash, s.label); });
    if (has('pivots') && series.pivots) {
      PIVOT_KEYS.forEach((k) => line(series.pivots[k], col('pivots'), [6, 4], k.toUpperCase()));
    }
    Object.entries(MULTI).forEach(([k, subs]) => {
      if (!has(k) || !series[k]) return;
      subs.forEach(([sk, lab]) => line(series[k][sk], col(k), [4, 3], lab));
    });
    if (has('fourth_candle') && series.fourth_candle) {
      line(series.fourth_candle.high, col('fourth_candle'), [2, 3], '4C H');
      line(series.fourth_candle.low, col('fourth_candle'), [2, 3], '4C L');
    }
    if (has('hammer') && series.hammer) line(series.hammer.trigger, col('hammer'), [3, 3], 'HAM');

    // candles
    const body = Math.max(1, Math.min(16, bw * 0.7));
    for (let i = start; i < start + count; i += 1) {
      const c = candles[i];
      if (!c) continue;
      const color = c.c >= c.o ? UP : DOWN;
      g.strokeStyle = color; g.fillStyle = color; g.lineWidth = 1;
      const px = Math.round(x(i)) + 0.5;
      g.beginPath(); g.moveTo(px, y(c.h)); g.lineTo(px, y(c.l)); g.stroke();
      const top = Math.min(y(c.o), y(c.c));
      g.fillRect(px - body / 2, top, body, Math.max(1, Math.abs(y(c.o) - y(c.c))));
    }

    const drawMarks = (marks, color) => {
      if (!marks) return;
      g.save(); g.fillStyle = color; g.font = 'bold 9px ui-sans-serif, system-ui'; g.textAlign = 'center';
      marks.forEach((m) => {
        const i = m.idx;
        if (i < start || i >= start + count || !candles[i]) return;
        const base = y(candles[i].l);
        g.beginPath(); g.moveTo(x(i), base + 3); g.lineTo(x(i) - 4, base + 9); g.lineTo(x(i) + 4, base + 9); g.closePath(); g.fill();
        g.fillText(m.type, x(i), base + 20);
      });
      g.restore();
    };
    if (has('fourth_candle')) drawMarks(series.fourth_candle?.marks, col('fourth_candle'));
    if (has('hammer')) drawMarks(series.hammer?.marks, col('hammer'));

    // saved levels + text notes
    levels.forEach((l) => {
      const price = drift && drift.id === l.id ? drift.price : Number(l.price);
      if (!price) return;
      const yy = y(price);
      if (yy < padT - 20 || yy > padT + priceH + 20) return;
      const color = l.color || '#f59e0b';
      if (l.type === 'text') {
        let i = start + count - 6;
        if (l.anchor) {
          const at = candles.findIndex((c) => c.t === l.anchor);
          if (at >= 0) i = at;
        }
        const text = l.label || 'note';
        g.save();
        g.font = 'bold 10px ui-sans-serif, system-ui'; g.textAlign = 'left'; g.textBaseline = 'middle';
        const w = g.measureText(text).width + 12;
        g.fillStyle = `${color}22`; g.strokeStyle = color; g.lineWidth = 1;
        const bx = Math.max(padL + 2, Math.min(padL + plotW - w - 2, x(i) - w / 2));
        g.beginPath(); g.roundRect?.(bx, yy - 9, w, 18, 4); if (!g.roundRect) g.rect(bx, yy - 9, w, 18);
        g.fill(); g.stroke();
        g.fillStyle = color; g.fillText(text, bx + 6, yy);
        g.restore();
        return;
      }
      const hot = l.state === 'TOUCHED' || l.state === 'NEAR';
      g.save();
      g.strokeStyle = color; g.lineWidth = (drift && drift.id === l.id) ? 2.4 : hot ? 1.8 : 1.2;
      g.setLineDash(hot || (drift && drift.id === l.id) ? [] : [7, 4]);
      g.beginPath(); g.moveTo(padL, yy); g.lineTo(padL + plotW, yy); g.stroke();
      g.setLineDash([]);
      const text = `${l.label || 'level'} ${fmtNum(price)}`;
      g.font = 'bold 9px ui-sans-serif, system-ui'; g.textAlign = 'left'; g.textBaseline = 'middle';
      const w = g.measureText(text).width + 10;
      g.fillStyle = color; g.fillRect(padL + 2, yy - 7, w, 14);
      g.fillStyle = '#04121f'; g.fillText(text, padL + 7, yy);
      g.restore();
    });

    // last price
    if (ltp != null) {
      const yy = y(ltp);
      if (yy > padT && yy < padT + priceH) {
        g.save();
        g.strokeStyle = '#38bdf8'; g.lineWidth = 1; g.setLineDash([2, 3]);
        g.beginPath(); g.moveTo(padL, yy); g.lineTo(padL + plotW, yy); g.stroke();
        g.setLineDash([]);
        g.fillStyle = '#38bdf8'; g.fillRect(padL + plotW + 2, yy - 8, padR - 6, 16);
        g.fillStyle = '#04121f'; g.font = 'bold 10px ui-sans-serif, system-ui'; g.textAlign = 'left';
        g.fillText(fmtNum(ltp, dec), padL + plotW + 6, yy);
        g.restore();
      }
    }

    // ── sub-panes ──
    let top = padT + priceH;
    const openPane = (title) => {
      g.fillStyle = PANE_BG; g.fillRect(padL, top, plotW, subH);
      g.strokeStyle = GRID; g.lineWidth = 1;
      g.beginPath(); g.moveTo(padL, top + 0.5); g.lineTo(padL + plotW, top + 0.5); g.stroke();
      g.fillStyle = AXIS; g.font = '9px ui-sans-serif, system-ui'; g.textAlign = 'left';
      g.fillText(title, padL + 5, top + 10);
    };
    if (panes.volume) {
      openPane('Volume');
      let vmax = 0;
      for (let i = start; i < start + count; i += 1) {
        const v = (series.volume ? series.volume[i] : candles[i]?.v) || 0;
        vmax = Math.max(vmax, v);
      }
      vmax = vmax || 1;
      for (let i = start; i < start + count; i += 1) {
        const v = (series.volume ? series.volume[i] : candles[i]?.v) || 0;
        const hgt = (v / vmax) * (subH - 16);
        g.fillStyle = candles[i] && candles[i].c >= candles[i].o ? 'rgba(38,166,154,0.6)' : 'rgba(239,83,80,0.6)';
        g.fillRect(x(i) - body / 2, top + subH - hgt - 2, body, hgt);
      }
      if (has('volume_ma') && series.volume_ma) {
        g.save(); g.strokeStyle = col('volume_ma'); g.lineWidth = 1.3; g.beginPath();
        let pen = false;
        for (let i = start; i < start + count; i += 1) {
          const v = series.volume_ma[i];
          if (v == null) { pen = false; continue; }
          const py = top + subH - (v / vmax) * (subH - 16) - 2;
          if (!pen) { g.moveTo(x(i), py); pen = true; } else g.lineTo(x(i), py);
        }
        g.stroke(); g.restore();
      }
      g.fillStyle = AXIS; g.textAlign = 'left'; g.fillText(compact(vmax), padL + plotW + 6, top + 10);
      top += subH;
    }
    const linePane = (title, arr, color) => {
      openPane(title);
      let mn = Infinity, mx = -Infinity;
      for (let i = start; i < start + count; i += 1) { const v = arr[i]; if (v != null) { mn = Math.min(mn, v); mx = Math.max(mx, v); } }
      if (!Number.isFinite(mn)) { top += subH; return; }
      if (mn === mx) mx = mn + 1;
      const yy = (v) => top + subH - ((v - mn) / (mx - mn)) * (subH - 18) - 4;
      g.save(); g.strokeStyle = color; g.lineWidth = 1.4; g.beginPath();
      let pen = false;
      for (let i = start; i < start + count; i += 1) {
        const v = arr[i];
        if (v == null) { pen = false; continue; }
        if (!pen) { g.moveTo(x(i), yy(v)); pen = true; } else g.lineTo(x(i), yy(v));
      }
      g.stroke(); g.restore();
      g.fillStyle = AXIS; g.textAlign = 'left';
      g.fillText(compact(mx), padL + plotW + 6, top + 10);
      g.fillText(compact(mn), padL + plotW + 6, top + subH - 8);
      top += subH;
    };
    if (panes.cv) linePane('Cumulative volume', series.cum_volume, col('cum_volume'));
    if (panes.oi) linePane('Open interest', series.oi, col('oi'));

    // crosshair
    if (cross && cross.i >= start && cross.i < start + count) {
      g.save();
      g.strokeStyle = 'rgba(148,163,184,0.55)'; g.setLineDash([3, 3]); g.lineWidth = 1;
      g.beginPath(); g.moveTo(x(cross.i), padT); g.lineTo(x(cross.i), size.h - padB); g.stroke();
      if (cross.y > padT && cross.y < padT + priceH) {
        g.beginPath(); g.moveTo(padL, cross.y); g.lineTo(padL + plotW, cross.y); g.stroke();
        g.setLineDash([]);
        const p = hi - ((cross.y - padT) / priceH) * (hi - lo);
        g.fillStyle = '#334155'; g.fillRect(padL + plotW + 2, cross.y - 8, padR - 6, 16);
        g.fillStyle = '#e2e8f0'; g.font = '10px ui-sans-serif, system-ui'; g.textAlign = 'left';
        g.fillText(fmtNum(p, dec), padL + plotW + 6, cross.y);
      }
      g.restore();
    }
  }, [candles, series, enabled, levels, view, size, cross, ltp, geom, has, range,
      panes.volume, panes.cv, panes.oi, n, drift, col]);

  // ── hit testing ──
  const rel = (e) => {
    const rect = canvasRef.current.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  };
  const idxAt = (px) => {
    const { padL, plotW } = geom();
    const bw = plotW / view.count;
    return Math.max(0, Math.min(n - 1, view.start + Math.floor((px - padL) / bw)));
  };
  const priceAt = (py) => {
    const r = range();
    if (!r) return null;
    const { padT, priceH } = geom();
    return r.hi - ((py - padT) / priceH) * (r.hi - r.lo);
  };
  const levelAt = (py) => {
    const r = range();
    if (!r) return null;
    const { padT, priceH } = geom();
    const yOf = (p) => padT + ((r.hi - p) / (r.hi - r.lo)) * priceH;
    return levels.find((l) => l.price && Math.abs(yOf(Number(l.price)) - py) <= 5) || null;
  };
  const onAxis = (px) => {
    const { padL, plotW } = geom();
    return px > padL + plotW;
  };

  // ── interaction ──
  const onWheel = (e) => {
    e.preventDefault();
    const { x: px } = rel(e);
    if (onAxis(px)) {                                  // wheel over the axis → price zoom
      setYz((z) => ({ ...z, mult: Math.max(0.15, Math.min(8, z.mult * (e.deltaY > 0 ? 1.12 : 1 / 1.12))) }));
      return;
    }
    const at = idxAt(px);
    const factor = e.deltaY > 0 ? 1.15 : 1 / 1.15;
    setView((v) => {
      const count = Math.max(20, Math.min(n, Math.round(v.count * factor)));
      const ratio = (at - v.start) / Math.max(1, v.count);
      const start = Math.max(0, Math.min(n - count, Math.round(at - ratio * count)));
      pinned.current = start + count >= n - 1;
      return { start, count };
    });
  };
  const onDown = (e) => {
    const { x: px, y: py } = rel(e);
    if (onAxis(px)) { axis.current = { y: e.clientY, mult: yz.mult }; return; }
    const lv = levelAt(py);
    if (lv) { grab.current = { id: lv.id }; setDrift({ id: lv.id, price: Number(lv.price) }); return; }
    pan.current = { x: e.clientX, y: e.clientY, start: view.start, shift: yz.shift };
  };
  const onMove = (e) => {
    const { x: px, y: py } = rel(e);
    const i = idxAt(px);
    setCross({ i, y: py, price: priceAt(py) });
    onCrosshair?.(candles[i], i);

    if (axis.current) {
      const dy = e.clientY - axis.current.y;
      setYz((z) => ({ ...z, mult: Math.max(0.15, Math.min(8, axis.current.mult * (1 + dy / 260))) }));
      return;
    }
    if (grab.current) {
      const p = priceAt(py);
      if (p != null) setDrift({ id: grab.current.id, price: Math.round(p * 100) / 100 });
      return;
    }
    const d = pan.current;                       // snapshot — it can be cleared mid-flight
    if (d) {
      const { padL, plotW } = geom();
      const bw = plotW / view.count;
      const shiftBars = Math.round((e.clientX - d.x) / bw);
      setView((v) => {
        const start = Math.max(0, Math.min(n - v.count, d.start - shiftBars));
        pinned.current = start + v.count >= n - 1;
        return { ...v, start };
      });
      if (e.shiftKey) {
        const r = range();
        if (r) setYz((z) => ({ ...z, shift: d.shift - ((e.clientY - d.y) / geom().priceH) }));
      }
    }
  };
  const finish = () => {
    if (grab.current && drift) {
      const id = grab.current.id; const price = drift.price;
      grab.current = null;
      setDrift(null);
      onMoveLevel?.(id, price);
    }
    grab.current = null; pan.current = null; axis.current = null;
  };
  const onLeave = () => { setCross(null); onCrosshair?.(null); finish(); };
  const onClick = (e) => {
    if (!addMode) return;
    const { y: py } = rel(e);
    const p = priceAt(py);
    if (p == null) return;
    const i = idxAt(rel(e).x);
    onAddLevel?.(Math.round(p * 100) / 100, addMode, candles[i]?.t);
  };
  const onDouble = () => { setYz({ mult: 1, shift: 0 }); };

  const cursor = addMode ? 'crosshair' : grab.current ? 'ns-resize' : 'grab';
  return (
    <div ref={wrapRef} className="w-full relative select-none" style={{ height }}>
      <canvas
        ref={canvasRef} className="block rounded-lg" style={{ cursor }}
        onWheel={onWheel} onMouseDown={onDown} onMouseUp={finish} onMouseMove={onMove}
        onMouseLeave={onLeave} onClick={onClick} onDoubleClick={onDouble}
      />
      {!n && <div className="absolute inset-0 flex items-center justify-center text-gray-600 text-sm">No candles — pick an instrument and hit Load</div>}
    </div>
  );
});

export default ChartCanvas;
export { fmtNum, compact };
