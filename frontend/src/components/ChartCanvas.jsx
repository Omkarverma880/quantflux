import React, {
  useRef, useEffect, useState, useCallback, forwardRef, useImperativeHandle,
} from 'react';

/**
 * A dependency-free candlestick chart on <canvas>.
 *
 * Pan by dragging, zoom the time axis with the wheel, zoom the price axis by
 * dragging it (or wheeling over it), double-click to auto-fit. Levels and text
 * notes are draggable; the crosshair lives on its own layer so hovering never
 * repaints the candles. Nothing here fetches or mutates.
 */

// ── the line registry ────────────────────────────────────────────────
// Every drawable line in one place: which series it reads, what it is called,
// its own default colour and dash. An indicator that draws two or more lines
// gets a distinct colour per line — a high and a low must never look alike.
export const LINE_GROUPS = {
  volume: [{ k: 'volume', path: [], label: 'Volume', color: '#26a69a', pane: 'volume' }],
  volume_ma: [{ k: 'volume_ma', path: ['volume_ma'], label: 'Vol MA', color: '#eab308', pane: 'volume' }],
  cum_volume: [{ k: 'cum_volume', path: ['cum_volume'], label: 'CV', color: '#38bdf8', pane: 'cv' }],
  oi: [{ k: 'oi', path: ['oi'], label: 'OI', color: '#c084fc', pane: 'oi' }],
  vwap_day: [{ k: 'vwap_day', path: ['vwap_day'], label: 'VWAP D', color: '#22d3ee' }],
  vwap_week: [{ k: 'vwap_week', path: ['vwap_week'], label: 'VWAP W', color: '#a78bfa' }],
  vwap_month: [{ k: 'vwap_month', path: ['vwap_month'], label: 'VWAP M', color: '#f472b6' }],
  pvwap_day: [{ k: 'pvwap_day', path: ['pvwap_day'], label: 'pVWAP D', color: '#0ea5e9', dash: [5, 4] }],
  pvwap_week: [{ k: 'pvwap_week', path: ['pvwap_week'], label: 'pVWAP W', color: '#8b5cf6', dash: [5, 4] }],
  pvwap_month: [{ k: 'pvwap_month', path: ['pvwap_month'], label: 'pVWAP M', color: '#ec4899', dash: [5, 4] }],
  ema_fast: [{ k: 'ema_fast', path: ['ema_fast'], label: 'EMA fast', color: '#fbbf24' }],
  ema_slow: [{ k: 'ema_slow', path: ['ema_slow'], label: 'EMA slow', color: '#f97316' }],
  pivots: [
    { k: 'piv_pp', path: ['pivots', 'pp'], label: 'PP', color: '#e2e8f0', dash: [6, 4] },
    { k: 'piv_r1', path: ['pivots', 'r1'], label: 'R1', color: '#fca5a5', dash: [6, 4] },
    { k: 'piv_r2', path: ['pivots', 'r2'], label: 'R2', color: '#f87171', dash: [6, 4] },
    { k: 'piv_r3', path: ['pivots', 'r3'], label: 'R3', color: '#dc2626', dash: [6, 4] },
    { k: 'piv_s1', path: ['pivots', 's1'], label: 'S1', color: '#86efac', dash: [6, 4] },
    { k: 'piv_s2', path: ['pivots', 's2'], label: 'S2', color: '#22c55e', dash: [6, 4] },
    { k: 'piv_s3', path: ['pivots', 's3'], label: 'S3', color: '#15803d', dash: [6, 4] },
  ],
  first_hour: [
    { k: 'fh_high', path: ['first_hour', 'fh_high'], label: 'FH H', color: '#22c55e', dash: [4, 3] },
    { k: 'fh_low', path: ['first_hour', 'fh_low'], label: 'FH L', color: '#ef4444', dash: [4, 3] },
  ],
  first_hour_prev: [
    { k: 'fhp_high', path: ['first_hour_prev', 'fhp_high'], label: 'PFH H', color: '#4ade80', dash: [2, 3] },
    { k: 'fhp_low', path: ['first_hour_prev', 'fhp_low'], label: 'PFH L', color: '#fb7185', dash: [2, 3] },
  ],
  first_hour_stats: [
    { k: 'fhs_max_high', path: ['first_hour_stats', 'fhs_max_high'], label: 'FH max', color: '#22d3ee', dash: [8, 4] },
    { k: 'fhs_avg_high', path: ['first_hour_stats', 'fhs_avg_high'], label: 'FH avgH', color: '#a3e635', dash: [3, 3] },
    { k: 'fhs_avg_low', path: ['first_hour_stats', 'fhs_avg_low'], label: 'FH avgL', color: '#fb923c', dash: [3, 3] },
    { k: 'fhs_min_low', path: ['first_hour_stats', 'fhs_min_low'], label: 'FH min', color: '#f59e0b', dash: [8, 4] },
  ],
  prev_day_hl: [
    { k: 'pdh', path: ['prev_day_hl', 'prev_high'], label: 'PDH', color: '#38bdf8', dash: [2, 3] },
    { k: 'pdl', path: ['prev_day_hl', 'prev_low'], label: 'PDL', color: '#fb923c', dash: [2, 3] },
  ],
  fourth_candle: [
    { k: 'fc_high', path: ['fourth_candle', 'high'], label: '4C H', color: '#10b981', dash: [2, 3] },
    { k: 'fc_low', path: ['fourth_candle', 'low'], label: '4C L', color: '#ef4444', dash: [2, 3] },
  ],
  hammer: [{ k: 'ham', path: ['hammer', 'trigger'], label: 'HAM', color: '#eab308', dash: [3, 3] }],
};
export const MARK_COLORS = { fourth_candle: '#22d3ee', hammer: '#eab308' };
export const lineColor = (line, colors = {}) => colors[line.k] || line.color;

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
const dig = (obj, path) => path.reduce((a, k) => (a == null ? a : a[k]), obj);

const ChartCanvas = forwardRef(function ChartCanvas({
  candles = [], series = {}, enabled = [], levels = [], colors = {}, height = 620,
  addMode = null, onAddLevel, onMoveLevel, onCrosshair, ltp,
}, ref) {
  const wrapRef = useRef(null);
  const canvasRef = useRef(null);
  const overlayRef = useRef(null);
  const [size, setSize] = useState({ w: 900, h: height });
  const [view, setView] = useState({ start: 0, count: 180 });
  const [yz, setYz] = useState({ mult: 1, shift: 0 });
  const [cross, setCross] = useState(null);
  const [drift, setDrift] = useState(null);
  const pan = useRef(null);
  const axis = useRef(null);
  const grab = useRef(null);
  const pinned = useRef(true);
  const lastBar = useRef(-1);
  const n = candles.length;

  // TradingView-style right margin: the last bar can be scrolled off the edge.
  const maxStart = useCallback((count) => Math.max(0, n - count) + Math.floor(count * 0.35), [n]);
  const clampStart = useCallback((s, count) => Math.max(0, Math.min(maxStart(count), s)), [maxStart]);
  const priceLines = useCallback(
    () => enabled.flatMap((k) => (LINE_GROUPS[k] || []).filter((l) => !l.pane)), [enabled],
  );

  useImperativeHandle(ref, () => ({
    fit: () => { setYz({ mult: 1, shift: 0 }); setView((v) => ({ count: v.count, start: Math.max(0, n - v.count) })); pinned.current = true; },
    goLive: () => { setView((v) => ({ count: v.count, start: Math.max(0, n - v.count) })); pinned.current = true; },
  }), [n]);

  useEffect(() => {
    setView((v) => {
      const count = Math.max(20, Math.min(v.count, Math.max(20, n)));
      if (pinned.current) return { count, start: Math.max(0, n - count) };
      return { count, start: clampStart(v.start, count) };
    });
  }, [n, clampStart]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const apply = () => setSize((s0) => {
      const w = el.clientWidth;
      return (s0.w === w && s0.h === height) ? s0 : { w, h: height };   // no-op resizes must not repaint
    });
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    apply();
    return () => ro.disconnect();
  }, [height]);

  const has = useCallback((k) => enabled.includes(k), [enabled]);
  const panes = {
    volume: has('volume') || has('volume_ma'),
    cv: has('cum_volume') && series.cum_volume,
    oi: has('oi') && series.oi,
  };

  const geom = useCallback(() => {
    const padL = 8, padR = 82, padT = 10, padB = 24;
    const subCount = [panes.volume, panes.cv, panes.oi].filter(Boolean).length;
    const avail = size.h - padT - padB;
    const subH = subCount ? Math.max(52, Math.min(96, avail * 0.18)) : 0;
    const priceH = Math.max(120, avail - subH * subCount);
    return { padL, padR, padT, padB, subH, priceH, plotW: Math.max(10, size.w - padL - padR) };
  }, [size, panes.volume, panes.cv, panes.oi]);

  const range = useCallback(() => {
    const start = Math.max(0, view.start);
    const count = Math.max(10, view.count);
    let lo = Infinity, hi = -Infinity;
    for (let i = start; i < start + count; i += 1) {
      const c = candles[i];
      if (!c) continue;
      lo = Math.min(lo, c.l); hi = Math.max(hi, c.h);
    }
    const scan = (arr) => { if (!arr) return; for (let i = start; i < start + count; i += 1) { const v = arr[i]; if (v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } } };
    priceLines().forEach((l) => scan(dig(series, l.path)));
    // Deliberately NOT the saved levels: a line parked 10% away would squash
    // every candle into a sliver at the top. Like TradingView, the scale fits
    // the bars (and the indicators drawn on them); a far level simply sits
    // off-screen until you scroll or zoom out to it.
    if (!Number.isFinite(lo) || !Number.isFinite(hi)) return null;
    const pad = (hi - lo) * 0.06 || 1;
    lo -= pad; hi += pad;
    const mid = (lo + hi) / 2 + yz.shift * (hi - lo);
    const half = ((hi - lo) / 2) * yz.mult;
    return { lo: mid - half, hi: mid + half, start, count };
  }, [candles, series, levels, view, yz, priceLines]);

  // ── base layer ──
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
      const t = candles[i]?.t;
      g.strokeStyle = GRID;
      g.beginPath(); g.moveTo(x(i), padT); g.lineTo(x(i), padT + priceH); g.stroke();
      if (t) { g.fillStyle = AXIS; g.fillText(t.length > 10 ? t.slice(5) : t, x(i), size.h - padB / 2); }
    }

    const drawLine = (arr, color, dash) => {
      if (!arr) return null;
      g.save(); g.strokeStyle = color; g.lineWidth = 1.4;
      if (dash) g.setLineDash(dash);
      g.beginPath();
      let pen = false; let lastV = null;
      for (let i = start; i < start + count; i += 1) {
        const v = arr[i];
        if (v == null) { pen = false; continue; }
        lastV = v;
        const py = y(v);
        if (!pen) { g.moveTo(x(i), py); pen = true; } else g.lineTo(x(i), py);
      }
      g.stroke(); g.restore();
      return lastV;
    };
    const tags = [];
    priceLines().forEach((l) => {
      const color = lineColor(l, colors);
      const lastV = drawLine(dig(series, l.path), color, l.dash);
      if (lastV != null) tags.push({ y: y(lastV), text: `${l.label} ${fmtNum(lastV, dec)}`, color });
    });

    // candles
    const body = Math.max(1, Math.min(16, bw * 0.7));
    for (let i = start; i < start + count; i += 1) {
      const c = candles[i];
      if (!c) continue;
      const color = c.c >= c.o ? UP : DOWN;
      g.strokeStyle = color; g.fillStyle = color; g.lineWidth = 1;
      const px = Math.round(x(i)) + 0.5;
      g.beginPath(); g.moveTo(px, y(c.h)); g.lineTo(px, y(c.l)); g.stroke();
      const top0 = Math.min(y(c.o), y(c.c));
      g.fillRect(px - body / 2, top0, body, Math.max(1, Math.abs(y(c.o) - y(c.c))));
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
    if (has('fourth_candle')) drawMarks(series.fourth_candle?.marks, colors.fc_high || MARK_COLORS.fourth_candle);
    if (has('hammer')) drawMarks(series.hammer?.marks, colors.ham || MARK_COLORS.hammer);

    // saved levels + notes
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
        const bx = Math.max(padL + 2, Math.min(padL + plotW - w - 2, x(i) - w / 2));
        g.fillStyle = `${color}22`; g.strokeStyle = color; g.lineWidth = 1;
        g.beginPath();
        if (g.roundRect) g.roundRect(bx, yy - 9, w, 18, 4); else g.rect(bx, yy - 9, w, 18);
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

    // right-edge tags last, with collision avoidance so R1/R2/S1 stay readable
    const taken = [];
    tags.sort((a, b) => a.y - b.y).forEach((t) => {
      if (t.y < padT + 6 || t.y > padT + priceH - 6) return;
      if (taken.some((ty) => Math.abs(ty - t.y) < 13)) return;
      taken.push(t.y);
      g.save();
      g.font = 'bold 9px ui-sans-serif, system-ui'; g.textAlign = 'left'; g.textBaseline = 'middle';
      const w = g.measureText(t.text).width + 8;
      g.fillStyle = t.color; g.globalAlpha = 0.95;
      g.fillRect(padL + plotW - w - 2, t.y - 7, w, 14);
      g.globalAlpha = 1; g.fillStyle = '#04121f';
      g.fillText(t.text, padL + plotW - w + 2, t.y);
      g.restore();
    });

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
      for (let i = start; i < start + count; i += 1) vmax = Math.max(vmax, (series.volume ? series.volume[i] : candles[i]?.v) || 0);
      vmax = vmax || 1;
      for (let i = start; i < start + count; i += 1) {
        const v = (series.volume ? series.volume[i] : candles[i]?.v) || 0;
        const hgt = (v / vmax) * (subH - 16);
        g.fillStyle = candles[i] && candles[i].c >= candles[i].o ? 'rgba(38,166,154,0.6)' : 'rgba(239,83,80,0.6)';
        g.fillRect(x(i) - body / 2, top + subH - hgt - 2, body, hgt);
      }
      if (has('volume_ma') && series.volume_ma) {
        g.save(); g.strokeStyle = colors.volume_ma || '#eab308'; g.lineWidth = 1.3; g.beginPath();
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
    if (panes.cv) linePane('Cumulative volume', series.cum_volume, colors.cum_volume || '#38bdf8');
    if (panes.oi) linePane('Open interest', series.oi, colors.oi || '#c084fc');
  }, [candles, series, enabled, levels, colors, view, size, ltp, geom, has, range,
      panes.volume, panes.cv, panes.oi, n, drift, priceLines]);

  // ── crosshair layer ──
  useEffect(() => {
    const cv = overlayRef.current;
    if (!cv) return;
    const dpr = window.devicePixelRatio || 1;
    if (cv.width !== size.w * dpr || cv.height !== size.h * dpr) {
      cv.width = size.w * dpr; cv.height = size.h * dpr;
      cv.style.width = `${size.w}px`; cv.style.height = `${size.h}px`;
    }
    const g = cv.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, size.w, size.h);
    const r = range();
    if (!r || !cross) return;
    const { padL, padR, padT, padB, priceH, plotW } = geom();
    const { lo, hi, start, count } = r;
    if (cross.i < start || cross.i >= start + count) return;
    const bw = plotW / count;
    const cx = padL + (cross.i - start) * bw + bw / 2;
    const dec = hi > 500 ? 1 : 2;
    g.strokeStyle = 'rgba(148,163,184,0.55)'; g.setLineDash([3, 3]); g.lineWidth = 1;
    g.beginPath(); g.moveTo(cx, padT); g.lineTo(cx, size.h - padB); g.stroke();
    if (cross.y > padT && cross.y < padT + priceH) {
      g.beginPath(); g.moveTo(padL, cross.y); g.lineTo(padL + plotW, cross.y); g.stroke();
      g.setLineDash([]);
      const p = hi - ((cross.y - padT) / priceH) * (hi - lo);
      g.fillStyle = '#334155'; g.fillRect(padL + plotW + 2, cross.y - 8, padR - 6, 16);
      g.fillStyle = '#e2e8f0'; g.font = '10px ui-sans-serif, system-ui';
      g.textAlign = 'left'; g.textBaseline = 'middle';
      g.fillText(fmtNum(p, dec), padL + plotW + 6, cross.y);
    }
  }, [cross, size, range, geom]);

  // ── interaction ──
  const rel = (e) => {
    const rect = overlayRef.current.getBoundingClientRect();
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

  const onWheel = (e) => {
    e.preventDefault();
    const { x: px } = rel(e);
    if (onAxis(px)) {
      setYz((z) => ({ ...z, mult: Math.max(0.15, Math.min(8, z.mult * (e.deltaY > 0 ? 1.12 : 1 / 1.12))) }));
      return;
    }
    const at = idxAt(px);
    const factor = e.deltaY > 0 ? 1.15 : 1 / 1.15;
    setView((v) => {
      const count = Math.max(20, Math.min(n, Math.round(v.count * factor)));
      const ratio = (at - v.start) / Math.max(1, v.count);
      const start = clampStart(Math.round(at - ratio * count), count);
      pinned.current = start + count >= n;
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
    setCross({ i, y: py });
    if (lastBar.current !== i) { lastBar.current = i; onCrosshair?.(candles[i], i); }

    const ax = axis.current;                  // snapshot: mouseup can clear it
    if (ax) {
      const dy = e.clientY - ax.y;
      setYz((z) => ({ ...z, mult: Math.max(0.15, Math.min(8, ax.mult * (1 + dy / 260))) }));
      return;
    }
    const gr = grab.current;
    if (gr) {
      const p = priceAt(py);
      if (p != null) setDrift({ id: gr.id, price: Math.round(p * 100) / 100 });
      return;
    }
    const d = pan.current;
    if (d) {
      const { plotW, priceH } = geom();
      const bw = plotW / view.count;
      const shiftBars = Math.round((e.clientX - d.x) / bw);
      setView((v) => {
        const start = clampStart(d.start - shiftBars, v.count);
        pinned.current = start + v.count >= n;
        return { ...v, start };
      });
      // Vertical drag moves the price scale too — no modifier key, the way a
      // chart should feel. Double-click (or Fit) snaps back to auto.
      // Drag DOWN (dy > 0) must raise the price window so the candles follow the
      // cursor downward — hence +, not −.
      const dy = e.clientY - d.y;
      if (Math.abs(dy) > 1) setYz((z) => ({ ...z, shift: d.shift + (dy / priceH) }));
    }
  };
  const finish = () => {
    if (grab.current && drift) {
      const { id } = grab.current; const { price } = drift;
      grab.current = null; setDrift(null);
      onMoveLevel?.(id, price);
    }
    grab.current = null; pan.current = null; axis.current = null;
  };
  const onLeave = () => { setCross(null); onCrosshair?.(null); finish(); };
  const onClick = (e) => {
    if (!addMode) return;
    const { x: px, y: py } = rel(e);
    const p = priceAt(py);
    if (p == null) return;
    onAddLevel?.(Math.round(p * 100) / 100, addMode, candles[idxAt(px)]?.t);
  };

  const cursor = addMode ? 'crosshair' : 'grab';
  return (
    <div ref={wrapRef} className="w-full relative select-none" style={{ height }}>
      <canvas ref={canvasRef} className="block rounded-lg absolute inset-0" />
      <canvas
        ref={overlayRef} className="block rounded-lg absolute inset-0" style={{ cursor }}
        onWheel={onWheel} onMouseDown={onDown} onMouseUp={finish} onMouseMove={onMove}
        onMouseLeave={onLeave} onClick={onClick} onDoubleClick={() => setYz({ mult: 1, shift: 0 })}
      />
      {!n && <div className="absolute inset-0 flex items-center justify-center text-gray-600 text-sm">No candles — pick an instrument and hit Load</div>}
    </div>
  );
});

export default ChartCanvas;
export { fmtNum, compact };
