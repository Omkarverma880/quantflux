import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  LineChart, Line, XAxis, YAxis, ResponsiveContainer,
  ReferenceLine, Tooltip, CartesianGrid, ReferenceDot,
} from 'recharts';
import {
  Play, Square, Settings2, ChevronDown, ChevronUp,
  Shield, Target, TrendingUp, Zap, Pencil, Move,
  CheckCircle2, XCircle, Clock, AlertCircle, Info, Trash2,
} from 'lucide-react';
import { api } from '../api';

const REFRESH_MS = 1_000;
const SPOT_HISTORY_LIMIT = 500;

const STATE_STYLE = {
  IDLE:          { bg: 'bg-gray-600/20',   text: 'text-gray-400',   label: 'Idle' },
  ORDER_PLACED:  { bg: 'bg-yellow-600/20', text: 'text-yellow-400', label: 'Order Placed' },
  POSITION_OPEN: { bg: 'bg-blue-600/20',   text: 'text-blue-400',   label: 'Position Open' },
  COMPLETED:     { bg: 'bg-green-600/20',  text: 'text-green-400',  label: 'Completed' },
};

function signalColor(sig) {
  if (sig === 'BUY_CALL') return 'text-green-400';
  if (sig === 'BUY_PUT') return 'text-red-400';
  return 'text-yellow-400';
}
function signalBg(sig) {
  if (sig === 'BUY_CALL') return 'bg-green-500/15 border-green-500/30';
  if (sig === 'BUY_PUT') return 'bg-red-500/15 border-red-500/30';
  return 'bg-yellow-500/15 border-yellow-500/30';
}

function Card({ title, icon: Icon, children, className = '', right = null }) {
  return (
    <div className={`bg-surface-2 border border-surface-3 rounded-xl p-4 ${className}`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2 text-gray-400 text-xs font-medium uppercase tracking-wider">
          {Icon && <Icon className="w-3.5 h-3.5" />}
          {title}
        </div>
        {right}
      </div>
      {children}
    </div>
  );
}

export default function Strategy6() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [configOpen, setConfigOpen] = useState(false);
  const [docOpen, setDocOpen] = useState(false);
  const [config, setConfig] = useState({
    sl_points: 30,
    target_points: 60,
    lot_size: 65,
    lots: 1,
    strike_interval: 50,
    sl_proximity: 5,
    target_proximity: 5,
    max_trades_per_day: 3,
    itm_offset: 100,
    max_entry_slippage: 8,
    index_name: 'NIFTY',
  });
  const [callDraft, setCallDraft] = useState('');
  const [putDraft, setPutDraft] = useState('');
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [spotHistory, setSpotHistory] = useState([]);
  const [dragMode, setDragMode] = useState(null); // 'CALL' | 'PUT' | null
  const [pickMode, setPickMode] = useState(null); // 'CALL' | 'PUT' | null
  const tickRef = useRef(0);
  const timerRef = useRef(null);
  const chartWrapRef = useRef(null);
  const yRangeRef = useRef({ min: 0, max: 0 });
  const configSeededRef = useRef(false);

  /* ── Data fetching ─────────────────────────── */
  const fetchStatus = useCallback(async () => {
    try {
      setLoading(true);
      const res = await api.getStrategy6TradeStatus();
      setStatus(res);
      if (res?.config && !configSeededRef.current) {
        setConfig((c) => ({ ...c, ...res.config }));
        configSeededRef.current = true;
      }
      const s = res?.spot?.price ?? 0;
      if (s > 0) {
        tickRef.current += 1;
        const tStr = new Date().toLocaleTimeString('en-IN', { hour12: false });
        setSpotHistory((h) => {
          const next = [...h, { x: tickRef.current, y: s, t: tStr }];
          return next.slice(-SPOT_HISTORY_LIMIT);
        });
      }
    } catch (e) {
      console.error('s6 status', e);
    } finally {
      setLoading(false);
    }
  }, []);

  const triggerCheck = useCallback(async () => {
    try {
      const res = await api.strategy6TradeCheck();
      setStatus(res);
      const s = res?.spot?.price ?? 0;
      if (s > 0) {
        tickRef.current += 1;
        const tStr = new Date().toLocaleTimeString('en-IN', { hour12: false });
        setSpotHistory((h) => {
          const next = [...h, { x: tickRef.current, y: s, t: tStr }];
          return next.slice(-SPOT_HISTORY_LIMIT);
        });
      }
    } catch (e) {
      console.error('s6 check', e);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    timerRef.current = setInterval(() => {
      // Always poll check() so the engine ticks even before user starts —
      // this keeps the spot price live and lets the line touch detector
      // begin tracking immediately when the user hits Start.
      if (status?.is_active) triggerCheck();
      else fetchStatus();
    }, REFRESH_MS);
    return () => clearInterval(timerRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.is_active]);

  // Seed spotHistory with today's intraday minute candles
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.getStrategy6Intraday();
        if (cancelled) return;
        const series = Array.isArray(res?.series) ? res.series : [];
        if (!series.length) return;
        tickRef.current = series.length;
        setSpotHistory((h) => {
          const seeded = series.map((p, i) => ({ x: i + 1, y: p.y, t: p.t }));
          const tail = h.slice(-Math.max(0, SPOT_HISTORY_LIMIT - seeded.length));
          const renumbered = tail.map((p, i) => ({ ...p, x: seeded.length + i + 1 }));
          tickRef.current = seeded.length + renumbered.length;
          return [...seeded, ...renumbered].slice(-SPOT_HISTORY_LIMIT);
        });
      } catch (e) {
        console.warn('s6 intraday seed failed', e);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Sync line drafts from server status when not actively editing
  useEffect(() => {
    const cl = status?.lines?.call_line ?? 0;
    const pl = status?.lines?.put_line ?? 0;
    setCallDraft((cur) => (cur === '' || Number(cur) !== cl) ? (cl ? String(cl) : '') : cur);
    setPutDraft((cur) => (cur === '' || Number(cur) !== pl) ? (pl ? String(pl) : '') : cur);
    // We intentionally DO want this to run on every status update so the
    // server-side authoritative value (e.g. after a drag) flows back in.
    // The conditional inside the setter keeps the user's mid-typing edit
    // intact when the server returns the same value they just typed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.lines?.call_line, status?.lines?.put_line]);

  /* ── Controls ──────────────────────────────── */
  const start = async () => {
    if (starting) return;
    setStarting(true);
    try {
      await api.strategy6TradeStart({
        ...config,
        call_line: status?.lines?.call_line ?? 0,
        put_line: status?.lines?.put_line ?? 0,
      });
      await fetchStatus();
    } catch (e) { alert(e.message || 'Start failed'); }
    finally { setStarting(false); }
  };
  const stop = async () => {
    if (stopping) return;
    setStopping(true);
    try { await api.strategy6TradeStop(); await fetchStatus(); }
    catch (e) { alert(e.message || 'Stop failed'); }
    finally { setStopping(false); }
  };
  const saveConfig = async () => {
    try {
      await api.strategy6TradeUpdateConfig(config);
      configSeededRef.current = false;
      await fetchStatus();
      alert('Config saved');
    } catch (e) {
      alert(e.message || 'Config save failed');
    }
  };

  const updateLines = useCallback(async (call, put) => {
    try {
      await api.strategy6UpdateLines({
        call_line: call !== undefined ? Number(call) : undefined,
        put_line: put !== undefined ? Number(put) : undefined,
      });
      await fetchStatus();
    } catch (e) {
      alert(e.message || 'Line update failed');
    }
  }, [fetchStatus]);

  const commitCall = () => {
    const v = Number(callDraft);
    if (Number.isFinite(v) && v >= 0) updateLines(v, undefined);
  };
  const commitPut = () => {
    const v = Number(putDraft);
    if (Number.isFinite(v) && v >= 0) updateLines(undefined, v);
  };
  const setLineAtSpot = (which) => {
    const s = status?.spot?.price ?? 0;
    if (!s) return;
    if (which === 'CALL') updateLines(s, undefined);
    else updateLines(undefined, s);
  };
  const editLineViaPrompt = (which) => {
    const cur = which === 'CALL' ? status?.lines?.call_line : status?.lines?.put_line;
    const raw = window.prompt(`Set ${which} line price`, cur ? String(cur) : '');
    if (raw == null) return;
    const v = Number(raw);
    if (!Number.isFinite(v) || v < 0) return;
    if (which === 'CALL') updateLines(v, undefined);
    else updateLines(undefined, v);
  };

  /* ── Drag-to-move + click-to-pick handlers ──
   *
   * recharts doesn't expose a drag API for ReferenceLine, so we capture
   * mouse events on a wrapper div. On mousedown we read the current
   * Y-axis domain from the rendered <YAxis> (via DOM) and compute the
   * price corresponding to the cursor's Y pixel. We update the line
   * locally (as a "ghost") on every mousemove for instant feedback,
   * then commit to the backend on mouseup.
   *
   * For pick mode (one-shot click-to-set), the same Y→price math is
   * used on a single click.
   */
  const computePriceFromMouse = useCallback((evt) => {
    const wrap = chartWrapRef.current;
    if (!wrap) return null;
    const svg = wrap.querySelector('svg.recharts-surface');
    if (!svg) return null;
    const rect = svg.getBoundingClientRect();
    const yPix = evt.clientY - rect.top;
    // Find plot area: recharts emits axis labels — the actual plot is the
    // area inside the YAxis ticks. Use the tick positions to derive the
    // pixel-to-price scale.
    const ticks = wrap.querySelectorAll('.recharts-yAxis .recharts-cartesian-axis-tick');
    if (ticks.length < 2) return null;
    const tickInfo = [];
    ticks.forEach((t) => {
      const txt = t.querySelector('text');
      const y = Number(t.getAttribute('y') ?? (txt && txt.getAttribute('y')) ?? NaN);
      const v = Number(txt && txt.textContent);
      if (Number.isFinite(y) && Number.isFinite(v)) tickInfo.push({ y, v });
    });
    if (tickInfo.length < 2) return null;
    tickInfo.sort((a, b) => a.y - b.y);
    const top = tickInfo[0];
    const bot = tickInfo[tickInfo.length - 1];
    if (top.y === bot.y) return null;
    const slope = (bot.v - top.v) / (bot.y - top.y);
    const price = top.v + slope * (yPix - top.y);
    return Math.round(price * 100) / 100;
  }, []);

  const [ghostLines, setGhostLines] = useState({ call: null, put: null });

  const onChartMouseDown = (evt) => {
    if (!dragMode) return;
    evt.preventDefault();
    const onMove = (e) => {
      const p = computePriceFromMouse(e);
      if (p == null) return;
      setGhostLines((g) => ({
        ...g,
        [dragMode === 'CALL' ? 'call' : 'put']: p,
      }));
    };
    const onUp = (e) => {
      const p = computePriceFromMouse(e);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      setGhostLines({ call: null, put: null });
      if (p != null && p > 0) {
        if (dragMode === 'CALL') updateLines(p, undefined);
        else updateLines(undefined, p);
      }
      setDragMode(null);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    // Fire one immediate update so the line snaps to mouse-down position
    onMove(evt);
  };

  const onChartClick = (evt) => {
    if (!pickMode || dragMode) return;
    const p = computePriceFromMouse(evt);
    if (p == null || p <= 0) return;
    if (pickMode === 'CALL') updateLines(p, undefined);
    else updateLines(undefined, p);
    setPickMode(null);
  };

  /* ── Derived ───────────────────────────────── */
  const stateMeta = STATE_STYLE[status?.state] || STATE_STYLE.IDLE;
  const callLine = status?.lines?.call_line || 0;
  const putLine = status?.lines?.put_line || 0;
  const spot = status?.spot?.price || 0;
  const signal = status?.signal || 'NO_TRADE';
  const scenario = status?.scenario || '—';

  const displayCall = ghostLines.call ?? callLine;
  const displayPut = ghostLines.put ?? putLine;

  const entryDot = useMemo(() => {
    if (!status?.trade?.fill_price || !spotHistory.length) return null;
    const last = spotHistory[spotHistory.length - 1];
    return { x: last.t, y: spot, fill: status.trade.signal_type === 'CE' ? '#22c55e' : '#ef4444' };
  }, [status?.trade?.fill_price, status?.trade?.signal_type, spot, spotHistory]);

  const cursorClass =
    dragMode ? 'cursor-grabbing' : pickMode ? 'cursor-crosshair' : '';

  /* ── Render ────────────────────────────────── */
  return (
    <div className="p-4 sm:p-6 max-w-[1400px] mx-auto space-y-4 sm:space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold text-white flex items-center gap-2">
            <TrendingUp className="w-5 h-5 text-brand-400" />
            Strategy 6 — Manual CALL / PUT Lines
          </h1>
          <p className="text-xs sm:text-sm text-gray-500 mt-0.5">
            Drag two horizontal lines on NIFTY spot — touch the CALL line to BUY ITM CE, touch the PUT line to BUY ITM PE.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`px-3 py-1.5 rounded-lg text-xs font-medium ${stateMeta.bg} ${stateMeta.text}`}>
            {stateMeta.label}
          </span>
          {status?.is_active ? (
            <button onClick={stop} disabled={stopping}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-red-600/20 text-red-400 border border-red-500/30 hover:bg-red-600/30 disabled:opacity-50">
              <Square className="w-3 h-3" /> Stop
            </button>
          ) : (
            <button onClick={start} disabled={starting}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-green-600/20 text-green-400 border border-green-500/30 hover:bg-green-600/30 disabled:opacity-50">
              <Play className="w-3 h-3" /> Start
            </button>
          )}
          <button onClick={() => setConfigOpen((v) => !v)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-surface-3 text-gray-300 hover:bg-surface-4">
            <Settings2 className="w-3 h-3" /> Config {configOpen ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          </button>
          <button onClick={() => setDocOpen((v) => !v)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-surface-3 text-gray-300 hover:bg-surface-4">
            <Info className="w-3 h-3" /> How it works
          </button>
        </div>
      </div>

      {docOpen && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 text-sm text-gray-300 space-y-2">
          <p>• <strong>CALL Line</strong>: When NIFTY spot crosses up through this level → BUY ITM CALL.</p>
          <p>• <strong>PUT Line</strong>: When NIFTY spot crosses down through this level → BUY ITM PUT.</p>
          <p>• <strong>No retest, no confirmation</strong> — direct touch entries.</p>
          <p>• <strong>Drag</strong>: click "Drag CALL" or "Drag PUT" then drag on the chart. <strong>Edit</strong>: double-click the line label OR type a price in the inputs and press Enter.</p>
          <p>• <strong>Position management</strong>: only one trade at a time. After SL or Target hits, the next entry is allowed up to your daily cap.</p>
          <p>• <strong>Auto square-off</strong>: all positions closed at 15:15 IST.</p>
        </div>
      )}

      {configOpen && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
            {[
              ['sl_points', 'SL (option pts)'],
              ['target_points', 'Target (option pts)'],
              ['lots', 'Lots'],
              ['strike_interval', 'Strike Interval'],
              ['sl_proximity', 'SL Proximity'],
              ['target_proximity', 'Target Proximity'],
              ['max_trades_per_day', 'Max Trades / Day'],
              ['itm_offset', 'ITM Offset (idx pts)'],
              ['max_entry_slippage', 'Max Entry Slippage (₹)'],
            ].map(([k, label]) => (
              <label key={k} className="text-xs text-gray-400">
                {label}
                <input type="number"
                  className="mt-1 w-full bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5 text-white text-sm"
                  value={config[k] ?? 0}
                  onChange={(e) => setConfig((c) => ({ ...c, [k]: Number(e.target.value) }))} />
              </label>
            ))}
          </div>
          <div className="mt-3 flex items-center justify-between">
            <p className="text-[11px] text-gray-500">
              NIFTY lot size = <span className="text-gray-300">{config.lot_size || 65}</span>; order qty = <span className="text-gray-300">{(Number(config.lots) || 1) * (Number(config.lot_size) || 65)}</span>.
            </p>
            <button onClick={saveConfig}
              className="px-3 py-1.5 rounded-lg text-xs font-medium bg-brand-600 hover:bg-brand-700 text-white">
              Save Config
            </button>
          </div>
        </div>
      )}

      {/* Lines control panel */}
      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* CALL line */}
          <div className="bg-surface-3/40 border border-emerald-500/20 rounded-lg p-3">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <span className="w-3 h-0.5 bg-emerald-400" />
                <span className="text-sm font-semibold text-emerald-400">CALL Line</span>
              </div>
              <span className="text-[10px] text-gray-500">
                {callLine > 0 && spot > 0 ? `Δ ${(callLine - spot).toFixed(1)}` : ''}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <input type="number" step="0.05"
                className="flex-1 bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5 text-white text-sm font-mono"
                placeholder="Price"
                value={callDraft}
                onChange={(e) => setCallDraft(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && commitCall()}
                onBlur={commitCall} />
              <button onClick={() => setLineAtSpot('CALL')}
                disabled={!spot}
                title="Set at current spot"
                className="px-2 py-1.5 rounded-md text-[11px] font-medium bg-emerald-600/15 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-600/25 disabled:opacity-40">
                @ Spot
              </button>
              <button onClick={() => setDragMode(dragMode === 'CALL' ? null : 'CALL')}
                className={`px-2 py-1.5 rounded-md text-[11px] font-medium border ${
                  dragMode === 'CALL'
                    ? 'bg-emerald-500/30 text-emerald-200 border-emerald-400'
                    : 'bg-surface-3 text-gray-300 border-surface-4 hover:bg-surface-4'
                }`}>
                <Move className="w-3 h-3 inline -mt-0.5" /> Drag
              </button>
              <button onClick={() => setPickMode(pickMode === 'CALL' ? null : 'CALL')}
                className={`px-2 py-1.5 rounded-md text-[11px] font-medium border ${
                  pickMode === 'CALL'
                    ? 'bg-emerald-500/30 text-emerald-200 border-emerald-400'
                    : 'bg-surface-3 text-gray-300 border-surface-4 hover:bg-surface-4'
                }`}>
                Pick
              </button>
              <button
                onClick={() => { setCallDraft(''); updateLines(0, undefined); }}
                disabled={!callLine}
                title="Clear CALL line"
                className="px-2 py-1.5 rounded-md text-[11px] font-medium border bg-rose-600/10 text-rose-300 border-rose-500/30 hover:bg-rose-600/20 disabled:opacity-40 disabled:cursor-not-allowed">
                <Trash2 className="w-3 h-3" />
              </button>
            </div>
          </div>

          {/* PUT line */}
          <div className="bg-surface-3/40 border border-rose-500/20 rounded-lg p-3">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <span className="w-3 h-0.5 bg-rose-400" />
                <span className="text-sm font-semibold text-rose-400">PUT Line</span>
              </div>
              <span className="text-[10px] text-gray-500">
                {putLine > 0 && spot > 0 ? `Δ ${(spot - putLine).toFixed(1)}` : ''}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <input type="number" step="0.05"
                className="flex-1 bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5 text-white text-sm font-mono"
                placeholder="Price"
                value={putDraft}
                onChange={(e) => setPutDraft(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && commitPut()}
                onBlur={commitPut} />
              <button onClick={() => setLineAtSpot('PUT')}
                disabled={!spot}
                title="Set at current spot"
                className="px-2 py-1.5 rounded-md text-[11px] font-medium bg-rose-600/15 text-rose-300 border border-rose-500/30 hover:bg-rose-600/25 disabled:opacity-40">
                @ Spot
              </button>
              <button onClick={() => setDragMode(dragMode === 'PUT' ? null : 'PUT')}
                className={`px-2 py-1.5 rounded-md text-[11px] font-medium border ${
                  dragMode === 'PUT'
                    ? 'bg-rose-500/30 text-rose-200 border-rose-400'
                    : 'bg-surface-3 text-gray-300 border-surface-4 hover:bg-surface-4'
                }`}>
                <Move className="w-3 h-3 inline -mt-0.5" /> Drag
              </button>
              <button onClick={() => setPickMode(pickMode === 'PUT' ? null : 'PUT')}
                className={`px-2 py-1.5 rounded-md text-[11px] font-medium border ${
                  pickMode === 'PUT'
                    ? 'bg-rose-500/30 text-rose-200 border-rose-400'
                    : 'bg-surface-3 text-gray-300 border-surface-4 hover:bg-surface-4'
                }`}>
                Pick
              </button>
              <button
                onClick={() => { setPutDraft(''); updateLines(undefined, 0); }}
                disabled={!putLine}
                title="Clear PUT line"
                className="px-2 py-1.5 rounded-md text-[11px] font-medium border bg-rose-600/10 text-rose-300 border-rose-500/30 hover:bg-rose-600/20 disabled:opacity-40 disabled:cursor-not-allowed">
                <Trash2 className="w-3 h-3" />
              </button>
            </div>
          </div>
        </div>

        {(dragMode || pickMode) && (
          <div className="mt-3 text-[11px] text-amber-300">
            {dragMode && <>↕ Drag mode active for <b>{dragMode}</b> — click & hold on the chart to move the line. Release to commit.</>}
            {pickMode && <>＋ Pick mode active for <b>{pickMode}</b> — click anywhere on the chart to place the line.</>}
          </div>
        )}
      </div>

      {/* Live Signal Panel */}
      <div className={`rounded-xl border p-4 ${signalBg(signal)}`}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Zap className={`w-5 h-5 ${signalColor(signal)}`} />
            <div>
              <p className={`text-lg font-bold ${signalColor(signal)}`}>{signal.replace('_', ' ')}</p>
              <p className="text-xs text-gray-400">{scenario}</p>
            </div>
          </div>
          <div className="flex items-center gap-6 text-xs">
            <div>
              <p className="text-gray-500">Spot</p>
              <p className="font-mono text-white text-sm">{spot ? spot.toFixed(2) : '—'}</p>
            </div>
            <div>
              <p className="text-emerald-500">CALL</p>
              <p className="font-mono text-emerald-300 text-sm">{callLine ? callLine.toFixed(2) : '—'}</p>
            </div>
            <div>
              <p className="text-rose-500">PUT</p>
              <p className="font-mono text-rose-300 text-sm">{putLine ? putLine.toFixed(2) : '—'}</p>
            </div>
            <div>
              <p className="text-gray-500">Trades</p>
              <p className="font-mono text-gray-300 text-sm">{status?.trades_today ?? 0} / {config.max_trades_per_day}</p>
            </div>
          </div>
        </div>
      </div>

      {/* Main Live Chart */}
      <Card title="Live Spot vs CALL / PUT Lines" icon={TrendingUp}>
        <div
          ref={chartWrapRef}
          className={`relative ${cursorClass}`}
          onMouseDown={onChartMouseDown}
          onClick={onChartClick}
          style={dragMode || pickMode ? { userSelect: 'none' } : undefined}
        >
          <ResponsiveContainer width="100%" height={380}>
            <LineChart data={spotHistory} margin={{ top: 10, right: 70, left: 0, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
              <XAxis dataKey="t" stroke="#475569" fontSize={10} minTickGap={40} />
              <YAxis
                domain={[
                  (dataMin) => {
                    // Guard: recharts can pass +/-Infinity when the data array
                    // is empty. Math.min(...[]) is also Infinity. Coerce every
                    // candidate through isFinite before reducing.
                    const cands = [dataMin, spot, displayCall, displayPut]
                      .map(Number)
                      .filter((v) => Number.isFinite(v) && v > 0);
                    if (!cands.length) return 0;
                    const lo = Math.min(...cands);
                    return Math.floor(lo - Math.max(15, lo * 0.0015));
                  },
                  (dataMax) => {
                    const cands = [dataMax, spot, displayCall, displayPut]
                      .map(Number)
                      .filter((v) => Number.isFinite(v) && v > 0);
                    if (!cands.length) return 100;
                    const hi = Math.max(...cands);
                    return Math.ceil(hi + Math.max(15, hi * 0.0015));
                  },
                ]}
                stroke="#475569" fontSize={10} width={60}
                allowDataOverflow={false}
                tickFormatter={(v) => Number(v).toFixed(0)}
              />
              <Tooltip
                contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 6 }}
                labelStyle={{ color: '#94a3b8', fontSize: 11 }}
                formatter={(val) => [`₹ ${Number(val).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`, 'Spot']}
                labelFormatter={(t) => `Time: ${t}`}
              />
              {displayCall > 0 && (
                <ReferenceLine
                  y={displayCall}
                  stroke="#22c55e"
                  strokeDasharray={ghostLines.call != null ? '2 2' : '6 4'}
                  strokeWidth={ghostLines.call != null ? 2 : 1.5}
                  label={{
                    value: `CALL ${Number(displayCall).toFixed(2)}`,
                    fill: '#22c55e', fontSize: 11, position: 'right',
                    onDoubleClick: () => editLineViaPrompt('CALL'),
                  }}
                />
              )}
              {displayPut > 0 && (
                <ReferenceLine
                  y={displayPut}
                  stroke="#ef4444"
                  strokeDasharray={ghostLines.put != null ? '2 2' : '6 4'}
                  strokeWidth={ghostLines.put != null ? 2 : 1.5}
                  label={{
                    value: `PUT ${Number(displayPut).toFixed(2)}`,
                    fill: '#ef4444', fontSize: 11, position: 'right',
                    onDoubleClick: () => editLineViaPrompt('PUT'),
                  }}
                />
              )}
              <Line type="monotone" dataKey="y" stroke="#3b82f6" strokeWidth={2} dot={false} isAnimationActive={false} />
              {spotHistory.length > 0 && (
                <ReferenceDot
                  x={spotHistory[spotHistory.length - 1].t}
                  y={spotHistory[spotHistory.length - 1].y}
                  r={5} fill="#3b82f6" stroke="#fff" strokeWidth={1.5}
                  label={{
                    value: `LTP ${Number(spotHistory[spotHistory.length - 1].y).toFixed(2)}`,
                    position: 'right', fill: '#60a5fa', fontSize: 11,
                  }}
                />
              )}
              {entryDot && <ReferenceDot x={entryDot.x} y={entryDot.y} r={6} fill={entryDot.fill} stroke="#fff" />}
            </LineChart>
          </ResponsiveContainer>

          {/* Double-click overlays on the lines for "Edit price" UX.
              Recharts label onDoubleClick is unreliable; this overlay
              gives a guaranteed double-click hit area on the right edge
              of each line. */}
          {(callLine > 0 || putLine > 0) && (
            <div className="absolute top-2 right-2 flex flex-col gap-1 pointer-events-auto">
              {callLine > 0 && (
                <div className="flex items-center gap-1">
                  <button
                    onDoubleClick={() => editLineViaPrompt('CALL')}
                    onClick={(e) => e.stopPropagation()}
                    className="text-[10px] px-2 py-0.5 rounded bg-emerald-600/20 text-emerald-300 border border-emerald-500/30 hover:bg-emerald-600/30"
                    title="Double-click to edit CALL price">
                    <Pencil className="w-2.5 h-2.5 inline -mt-0.5" /> CALL
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); setCallDraft(''); updateLines(0, undefined); }}
                    title="Remove CALL line"
                    className="text-[10px] px-1.5 py-0.5 rounded bg-rose-600/15 text-rose-300 border border-rose-500/30 hover:bg-rose-600/25">
                    <XCircle className="w-2.5 h-2.5" />
                  </button>
                </div>
              )}
              {putLine > 0 && (
                <div className="flex items-center gap-1">
                  <button
                    onDoubleClick={() => editLineViaPrompt('PUT')}
                    onClick={(e) => e.stopPropagation()}
                    className="text-[10px] px-2 py-0.5 rounded bg-rose-600/20 text-rose-300 border border-rose-500/30 hover:bg-rose-600/30"
                    title="Double-click to edit PUT price">
                    <Pencil className="w-2.5 h-2.5 inline -mt-0.5" /> PUT
                  </button>
                  <button
                    onClick={(e) => { e.stopPropagation(); setPutDraft(''); updateLines(undefined, 0); }}
                    title="Remove PUT line"
                    className="text-[10px] px-1.5 py-0.5 rounded bg-rose-600/15 text-rose-300 border border-rose-500/30 hover:bg-rose-600/25">
                    <XCircle className="w-2.5 h-2.5" />
                  </button>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Live snapshot footer: ITM CE/PE preview */}
        {spot > 0 && (() => {
          const atm = Math.round(spot / (config.strike_interval || 50)) * (config.strike_interval || 50);
          const itm = Number(config.itm_offset ?? 100);
          const ceStrike = atm - itm;
          const peStrike = atm + itm;
          return (
            <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px]">
              <div className="bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5">
                <div className="text-gray-500">LTP</div>
                <div className="text-white font-semibold">₹ {spot.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
              </div>
              <div className="bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5">
                <div className="text-gray-500">ATM</div>
                <div className="text-white font-semibold">{atm}</div>
              </div>
              <div className="bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5">
                <div className="text-gray-500">ITM CE (BUY CALL)</div>
                <div className="text-emerald-400 font-semibold">{ceStrike} CE  <span className="text-gray-500 text-[10px]">(ATM − {itm})</span></div>
              </div>
              <div className="bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5">
                <div className="text-gray-500">ITM PE (BUY PUT)</div>
                <div className="text-rose-400 font-semibold">{peStrike} PE  <span className="text-gray-500 text-[10px]">(ATM + {itm})</span></div>
              </div>
            </div>
          );
        })()}
      </Card>

      {/* Trade & Orders */}
      {status?.trade?.option_symbol && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <Card title="Entry" icon={CheckCircle2}>
            <div className="space-y-1 text-sm">
              <Row label="Option" value={status.trade.option_symbol} />
              <Row label="Strike" value={status.trade.strike || status.trade.atm_strike} />
              <Row label="ATM (ref)" value={status.trade.atm_strike} />
              <Row label="Fill" value={status.trade.fill_price?.toFixed(2)} />
              <Row label="Reason" value={status.trade.entry_reason || '—'} />
            </div>
          </Card>
          <Card title="Risk" icon={Shield}>
            <div className="space-y-1 text-sm">
              <Row label="SL" value={status.trade.sl_price?.toFixed(2)} color="text-red-400" />
              <Row label="Target" value={status.trade.target_price?.toFixed(2)} color="text-green-400" />
              <Row label="LTP" value={status.trade.current_ltp?.toFixed(2)} />
              <Row label="Unrealized PnL" value={`₹${(status.trade.unrealized_pnl ?? 0).toFixed(2)}`}
                   color={(status.trade.unrealized_pnl ?? 0) >= 0 ? 'text-green-400' : 'text-red-400'} />
            </div>
          </Card>
          <Card title="Orders" icon={Target}>
            <OrderRow label="Entry" order={status.orders?.entry} icon={CheckCircle2} color="text-blue-400" />
            <OrderRow label="SL" order={status.orders?.sl} icon={Shield} color="text-red-400" />
            <OrderRow label="Target" order={status.orders?.target} icon={Target} color="text-green-400" />
          </Card>
        </div>
      )}

      {/* Trade log */}
      {status?.trade_log?.length > 0 && (
        <Card title="Today's Trade Log" icon={Clock}>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-gray-500 uppercase">
                <tr>
                  <th className="text-left px-2 py-1">Time</th>
                  <th className="text-left px-2 py-1">Scenario</th>
                  <th className="text-left px-2 py-1">Option</th>
                  <th className="text-right px-2 py-1">Entry</th>
                  <th className="text-right px-2 py-1">Exit</th>
                  <th className="text-left px-2 py-1">Result</th>
                  <th className="text-right px-2 py-1">PnL</th>
                </tr>
              </thead>
              <tbody className="text-gray-300">
                {status.trade_log.slice().reverse().map((t, i) => (
                  <tr key={i} className="border-t border-surface-3">
                    <td className="px-2 py-1">{t.exit_time}</td>
                    <td className="px-2 py-1">{t.scenario}</td>
                    <td className="px-2 py-1 font-mono">{t.option}</td>
                    <td className="px-2 py-1 text-right font-mono">{Number(t.entry_price).toFixed(2)}</td>
                    <td className="px-2 py-1 text-right font-mono">{Number(t.exit_price).toFixed(2)}</td>
                    <td className={`px-2 py-1 ${t.exit_type === 'TARGET_HIT' ? 'text-green-400' : t.exit_type === 'SL_HIT' ? 'text-red-400' : 'text-yellow-400'}`}>
                      {t.exit_type}
                    </td>
                    <td className={`px-2 py-1 text-right font-mono ${t.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      ₹{Number(t.pnl).toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

function Row({ label, value, color = 'text-white' }) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-gray-500">{label}</span>
      <span className={`font-mono ${color}`}>{value ?? '—'}</span>
    </div>
  );
}

function OrderRow({ label, order, icon: Icon, color }) {
  if (!order) return <div className="text-xs text-gray-600 py-1">{label}: —</div>;
  const st = order.status || '—';
  const isFilled = st === 'COMPLETE';
  const isFailed = st === 'CANCELLED' || st === 'REJECTED';
  const isShadow = st === 'SHADOW';
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-surface-3 last:border-0">
      <div className="flex items-center gap-2">
        <Icon className={`w-4 h-4 ${color}`} />
        <span className="text-sm text-gray-300">{label}</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-sm font-mono text-white">{order.price?.toFixed?.(2) ?? '—'}</span>
        {isFilled && <CheckCircle2 className="w-4 h-4 text-green-400" />}
        {isFailed && <XCircle className="w-4 h-4 text-red-400" />}
        {isShadow && <Shield className="w-4 h-4 text-purple-400" />}
        {!isFilled && !isFailed && !isShadow && <Clock className="w-4 h-4 text-yellow-400 animate-pulse" />}
        <span className={`text-xs ${isFilled ? 'text-green-400' : isFailed ? 'text-red-400' : isShadow ? 'text-purple-400' : 'text-yellow-400'}`}>
          {isShadow ? 'HIDDEN' : st}
        </span>
      </div>
    </div>
  );
}
