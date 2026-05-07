import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  LineChart, Line, XAxis, YAxis, ResponsiveContainer,
  ReferenceLine, Tooltip, CartesianGrid,
} from 'recharts';
import {
  Play, Square, Settings2, ChevronDown, ChevronUp,
  Shield, Target, TrendingUp, Zap, Pencil, RefreshCw,
  CheckCircle2, XCircle, Clock, Info, Trash2,
  Crosshair, ArrowLeftRight, Repeat, Flame,
} from 'lucide-react';
import { api } from '../api';
import BacktestPanel from '../components/BacktestPanel';

const REFRESH_MS = 1_000;

const STATE_STYLE = {
  IDLE:          { bg: 'bg-gray-600/20',   text: 'text-gray-400',   label: 'Idle' },
  ORDER_PLACED:  { bg: 'bg-yellow-600/20', text: 'text-yellow-400', label: 'Order Placed' },
  POSITION_OPEN: { bg: 'bg-blue-600/20',   text: 'text-blue-400',   label: 'Position Open' },
  COMPLETED:     { bg: 'bg-green-600/20',  text: 'text-green-400',  label: 'Completed' },
};

function signalColor(s) {
  if (s === 'REVERSE_BUY_CALL') return 'text-emerald-400';
  if (s === 'REVERSE_BUY_PUT') return 'text-rose-400';
  return 'text-yellow-400';
}
function signalBg(s) {
  if (s === 'REVERSE_BUY_CALL') return 'bg-emerald-500/15 border-emerald-500/30';
  if (s === 'REVERSE_BUY_PUT') return 'bg-rose-500/15 border-rose-500/30';
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

function fmt(n, d = 2) {
  const v = Number(n);
  if (!Number.isFinite(v)) return '—';
  return v.toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
}

/* ─────────────────────────────────────────────────────
   Side panel — same as S7 but the touch direction is
   coloured to communicate REVERSE entry intent.
   ───────────────────────────────────────────────────── */
function SidePanel({
  side, strikes, selectedStrike, onPickStrike,
  ltp, line, draft, setDraft, onCommitLine, onClearLine,
  series, reverseLabel, triggerActive,
}) {
  const isCall = side === 'CE';
  const accent = isCall ? 'green' : 'red';
  const accentText = isCall ? 'text-green-400' : 'text-red-400';
  const accentBorder = isCall ? 'border-green-500/40' : 'border-red-500/40';
  const accentBg = isCall ? 'bg-green-500/10' : 'bg-red-500/10';
  const accentRing = isCall ? 'ring-green-500/40' : 'ring-red-500/40';
  const lineColor = isCall ? '#22c55e' : '#ef4444';

  const wrapRef = useRef(null);
  const [drag, setDrag] = useState(false);
  const [pickMode, setPickMode] = useState(false);
  const yRangeRef = useRef({ min: 0, max: 0 });

  const data = useMemo(
    () => (series || []).map((p, i) => ({ x: i, t: p.t, y: Number(p.y) })),
    [series],
  );
  const [yMin, yMax] = useMemo(() => {
    const vals = data.map((d) => d.y).filter((v) => Number.isFinite(v) && v > 0);
    if (line > 0) vals.push(line);
    if (ltp > 0) vals.push(ltp);
    if (!vals.length) return [0, 1];
    let lo = Math.min(...vals); let hi = Math.max(...vals);
    const pad = Math.max(2, (hi - lo) * 0.1);
    lo = Math.max(0, lo - pad); hi = hi + pad;
    yRangeRef.current = { min: lo, max: hi };
    return [lo, hi];
  }, [data, line, ltp]);

  const priceFromMouse = useCallback((evt) => {
    const wrap = wrapRef.current; if (!wrap) return null;
    const rect = wrap.getBoundingClientRect();
    const y = evt.clientY - rect.top;
    const { min, max } = yRangeRef.current;
    if (max <= min) return null;
    const frac = 1 - y / rect.height;
    return min + frac * (max - min);
  }, []);

  const handleClick = useCallback((evt) => {
    if (!pickMode) return;
    const price = priceFromMouse(evt);
    if (price == null) return;
    setDraft(price.toFixed(2));
    setPickMode(false);
    onCommitLine(Number(price.toFixed(2)));
  }, [pickMode, priceFromMouse, setDraft, onCommitLine]);

  useEffect(() => {
    if (!drag) return;
    const onMove = (e) => {
      const price = priceFromMouse(e);
      if (price != null) setDraft(price.toFixed(2));
    };
    const onUp = (e) => {
      const price = priceFromMouse(e);
      setDrag(false);
      if (price != null) onCommitLine(Number(price.toFixed(2)));
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [drag, priceFromMouse, setDraft, onCommitLine]);

  const sym = isCall ? 'ce_symbol' : 'pe_symbol';
  const opts = strikes || [];

  return (
    <div className={`bg-surface-2 border ${accentBorder} rounded-xl p-3 flex flex-col relative ${triggerActive ? 'ring-2 ring-amber-400/60 animate-pulse' : ''}`}>
      <div className="flex items-center justify-between mb-2">
        <div className={`flex items-center gap-2 text-xs font-bold uppercase tracking-wider ${accentText}`}>
          {isCall ? <TrendingUp className="w-3.5 h-3.5" /> : <Shield className="w-3.5 h-3.5" />}
          {isCall ? 'CALL Side (CE)' : 'PUT Side (PE)'}
          <span className="ml-1 text-[10px] font-normal text-gray-500">monitored</span>
        </div>
        <div className="text-[11px] text-gray-500">LTP {fmt(ltp)}</div>
      </div>

      {reverseLabel && (
        <div className="mb-2 flex items-center gap-1.5 px-2 py-1 rounded bg-amber-500/10 border border-amber-500/30 text-[10px] text-amber-300">
          <ArrowLeftRight className="w-3 h-3" />
          On touch <span className="font-semibold">→ BUY {reverseLabel}</span>
        </div>
      )}

      <div className="grid grid-cols-11 gap-1 mb-2">
        {opts.map((s) => {
          const active = s.strike === selectedStrike;
          return (
            <button
              key={`${side}-${s.strike}`}
              onClick={() => onPickStrike(s)}
              disabled={!s[sym]}
              title={s[sym] || 'unavailable'}
              className={`text-[10px] py-1 rounded border transition
                ${active
                  ? `${accentBg} ${accentText} ${accentBorder} ring-1 ${accentRing}`
                  : 'bg-surface-3 text-gray-400 border-surface-3 hover:border-gray-500'}
                ${s.is_atm && !active ? 'border-yellow-500/40 text-yellow-300' : ''}
                ${!s[sym] ? 'opacity-40 cursor-not-allowed' : ''}`}
            >
              {s.strike}
              {s.is_atm && <span className="block text-[8px] opacity-70">ATM</span>}
            </button>
          );
        })}
      </div>

      <div className="flex items-center gap-2 mb-2">
        <input
          type="number" step="0.05" value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              const v = parseFloat(draft);
              if (Number.isFinite(v)) onCommitLine(v);
            }
          }}
          placeholder={`${isCall ? 'CALL' : 'PUT'} line price`}
          className="flex-1 bg-surface-3 border border-surface-3 rounded px-2 py-1 text-xs text-white"
        />
        <button
          onClick={() => { const v = parseFloat(draft); if (Number.isFinite(v)) onCommitLine(v); }}
          className={`text-[11px] px-2 py-1 rounded border ${accentBorder} ${accentText} hover:${accentBg}`}
        >Set</button>
        <button
          onClick={() => setPickMode((p) => !p)}
          title="Click on chart to set the line"
          className={`text-[11px] px-2 py-1 rounded border ${pickMode ? `${accentBg} ${accentBorder} ${accentText}` : 'border-surface-3 text-gray-400 hover:text-white'}`}
        >
          <Crosshair className="w-3 h-3 inline mr-1" /> Pick
        </button>
        <button
          disabled={!line}
          onClick={() => { setDraft(''); onClearLine(); }}
          title="Delete line"
          className="text-[11px] px-2 py-1 rounded border border-surface-3 text-gray-400 hover:text-rose-400 disabled:opacity-30 disabled:cursor-not-allowed"
        >
          <Trash2 className="w-3 h-3" />
        </button>
      </div>

      <div
        ref={wrapRef}
        onClick={handleClick}
        className={`relative h-56 select-none ${pickMode ? 'cursor-crosshair' : 'cursor-default'}`}
      >
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 6, right: 8, left: 4, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis dataKey="t" hide />
            <YAxis domain={[yMin, yMax]} tick={{ fontSize: 10, fill: '#9ca3af' }} width={48} />
            <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #1f2937', fontSize: 11 }}
              formatter={(v) => [fmt(v), 'LTP']} />
            <Line type="monotone" dataKey="y" stroke={lineColor} strokeWidth={1.6} dot={false} isAnimationActive={false} />
            {line > 0 && (
              <ReferenceLine y={line} stroke={lineColor} strokeDasharray="6 4" strokeWidth={1.5}
                label={{ value: `${isCall ? 'CALL' : 'PUT'} ${fmt(line)}`, position: 'right', fill: lineColor, fontSize: 11 }} />
            )}
            {ltp > 0 && (
              <ReferenceLine y={ltp} stroke="#94a3b8" strokeDasharray="2 4" strokeWidth={1} />
            )}
          </LineChart>
        </ResponsiveContainer>

        {line > 0 && (
          <div className="absolute right-2 top-2 flex items-center gap-1 z-10">
            <button
              onMouseDown={(e) => { e.stopPropagation(); setDrag(true); }}
              title="Drag the line"
              className={`p-1 rounded border ${accentBorder} ${accentBg} ${accentText}`}
            ><Pencil className="w-3 h-3" /></button>
            <button
              onClick={(e) => { e.stopPropagation(); setDraft(''); onClearLine(); }}
              title="Delete line"
              className="p-1 rounded border border-rose-500/40 bg-rose-500/10 text-rose-400"
            ><XCircle className="w-3 h-3" /></button>
          </div>
        )}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────
   Manual reverse-strike picker (only used in MANUAL mode)
   ───────────────────────────────────────────────────── */
function ReverseStrikePicker({ strikes, currentManualPe, currentManualCe, onCommit }) {
  const [pe, setPe] = useState(currentManualPe || 0);
  const [ce, setCe] = useState(currentManualCe || 0);
  useEffect(() => { setPe(currentManualPe || 0); }, [currentManualPe]);
  useEffect(() => { setCe(currentManualCe || 0); }, [currentManualCe]);

  const peStrikes = (strikes || []).filter((s) => s.pe_symbol);
  const ceStrikes = (strikes || []).filter((s) => s.ce_symbol);

  const handleSave = () => {
    const peObj = peStrikes.find((s) => s.strike === Number(pe));
    const ceObj = ceStrikes.find((s) => s.strike === Number(ce));
    onCommit({
      manual_pe: peObj ? { strike: peObj.strike, tradingsymbol: peObj.pe_symbol, token: peObj.pe_token } : null,
      manual_ce: ceObj ? { strike: ceObj.strike, tradingsymbol: ceObj.ce_symbol, token: ceObj.ce_token } : null,
    });
  };

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-xs">
      <label className="flex flex-col gap-1">
        <span className="text-gray-400">Reverse PUT (used on CALL trigger → BUY this PUT)</span>
        <select value={pe} onChange={(e) => setPe(Number(e.target.value))}
          className="bg-surface-3 border border-surface-3 rounded px-2 py-1 text-white">
          <option value={0}>— pick —</option>
          {peStrikes.map((s) => (
            <option key={s.strike} value={s.strike}>{s.strike} PE — {s.pe_symbol}</option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1">
        <span className="text-gray-400">Reverse CALL (used on PUT trigger → BUY this CALL)</span>
        <select value={ce} onChange={(e) => setCe(Number(e.target.value))}
          className="bg-surface-3 border border-surface-3 rounded px-2 py-1 text-white">
          <option value={0}>— pick —</option>
          {ceStrikes.map((s) => (
            <option key={s.strike} value={s.strike}>{s.strike} CE — {s.ce_symbol}</option>
          ))}
        </select>
      </label>
      <div className="flex items-end">
        <button onClick={handleSave}
          className="text-xs px-3 py-1.5 rounded bg-amber-600/20 border border-amber-500/40 text-amber-300 hover:bg-amber-600/30">
          Save reverse strikes
        </button>
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────
   Main page
   ───────────────────────────────────────────────────── */
export default function Strategy8() {
  const [status, setStatus] = useState(null);
  const [strikesData, setStrikesData] = useState({ atm: 0, strikes: [] });
  const [ceSeries, setCeSeries] = useState([]);
  const [peSeries, setPeSeries] = useState([]);
  const [callDraft, setCallDraft] = useState('');
  const [putDraft, setPutDraft] = useState('');
  const [trades, setTrades] = useState([]);
  const [configOpen, setConfigOpen] = useState(false);
  const [docOpen, setDocOpen] = useState(false);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [config, setConfig] = useState({
    sl_points: 30, target_points: 60,
    lot_size: 65, lots: 1, strike_interval: 50,
    sl_proximity: 5, target_proximity: 5,
    max_trades_per_day: 3, max_entry_slippage: 8,
    index_name: 'NIFTY',
  });
  const configSeededRef = useRef(false);
  const timerRef = useRef(null);
  const [flashSide, setFlashSide] = useState(null);

  /* ── Fetchers ── */
  const fetchStatus = useCallback(async () => {
    try {
      const res = await api.getStrategy8TradeStatus();
      setStatus(res);
      if (res?.config && !configSeededRef.current) {
        setConfig((c) => ({ ...c, ...res.config }));
        configSeededRef.current = true;
      }
    } catch (e) { console.error('s8 status', e); }
  }, []);

  const fetchStrikes = useCallback(async () => {
    try {
      const res = await api.getStrategy8Strikes();
      if (res?.status === 'ok') setStrikesData(res);
    } catch (e) { console.error('s8 strikes', e); }
  }, []);

  const fetchSeries = useCallback(async () => {
    try {
      if (status?.strikes?.ce_symbol) {
        const r = await api.getStrategy8Intraday('CE');
        if (r?.status === 'ok') setCeSeries(r.series || []);
      }
      if (status?.strikes?.pe_symbol) {
        const r = await api.getStrategy8Intraday('PE');
        if (r?.status === 'ok') setPeSeries(r.series || []);
      }
    } catch (e) { console.error('s8 series', e); }
  }, [status?.strikes?.ce_symbol, status?.strikes?.pe_symbol]);

  const fetchHistory = useCallback(async () => {
    try {
      const r = await api.strategy8TradeHistory();
      setTrades(r?.trades || []);
    } catch (e) { console.error('s8 history', e); }
  }, []);

  useEffect(() => {
    fetchStatus(); fetchStrikes(); fetchHistory();
  }, [fetchStatus, fetchStrikes, fetchHistory]);

  useEffect(() => {
    timerRef.current = setInterval(() => {
      fetchStatus();
      if ((Date.now() / 1000) % 30 < 1) {
        fetchStrikes(); fetchHistory();
      }
    }, REFRESH_MS);
    return () => clearInterval(timerRef.current);
  }, [fetchStatus, fetchStrikes, fetchHistory]);

  useEffect(() => {
    fetchSeries();
    const id = setInterval(fetchSeries, 5_000);
    return () => clearInterval(id);
  }, [fetchSeries]);

  // Append live ticks to chart
  useEffect(() => {
    const ce = status?.ltp?.ce ?? 0;
    if (ce > 0) {
      const t = new Date().toLocaleTimeString('en-IN', { hour12: false });
      setCeSeries((s) => (s.length && s[s.length - 1].y === ce) ? s : [...s.slice(-499), { t, y: ce }]);
    }
  }, [status?.ltp?.ce]);
  useEffect(() => {
    const pe = status?.ltp?.pe ?? 0;
    if (pe > 0) {
      const t = new Date().toLocaleTimeString('en-IN', { hour12: false });
      setPeSeries((s) => (s.length && s[s.length - 1].y === pe) ? s : [...s.slice(-499), { t, y: pe }]);
    }
  }, [status?.ltp?.pe]);

  // Flash animation when a trigger fires
  useEffect(() => {
    const side = status?.trigger?.last_side;
    const at = status?.trigger?.last_at;
    if (!side || !at) return;
    const ts = new Date(at).getTime();
    if (Date.now() - ts < 8000) {
      setFlashSide(side);
      const id = setTimeout(() => setFlashSide(null), 6000);
      return () => clearTimeout(id);
    }
  }, [status?.trigger?.last_at, status?.trigger?.last_side]);

  /* ── Mutations ── */
  const pickStrike = useCallback(async (side, s) => {
    try {
      const payload = side === 'CE'
        ? { ce: { strike: s.strike, tradingsymbol: s.ce_symbol, token: s.ce_token } }
        : { pe: { strike: s.strike, tradingsymbol: s.pe_symbol, token: s.pe_token } };
      await api.strategy8SetStrikes(payload);
      if (side === 'CE') setCeSeries([]); else setPeSeries([]);
      await fetchStatus();
    } catch (e) { alert(`Failed to set strike: ${e.message || e}`); }
  }, [fetchStatus]);

  const commitCallLine = useCallback(async (v) => {
    try { await api.strategy8UpdateLines({ call_line: v }); await fetchStatus(); }
    catch (e) { alert(`Failed: ${e.message || e}`); }
  }, [fetchStatus]);
  const commitPutLine = useCallback(async (v) => {
    try { await api.strategy8UpdateLines({ put_line: v }); await fetchStatus(); }
    catch (e) { alert(`Failed: ${e.message || e}`); }
  }, [fetchStatus]);
  const clearCallLine = useCallback(() => commitCallLine(0), [commitCallLine]);
  const clearPutLine = useCallback(() => commitPutLine(0), [commitPutLine]);

  const setReverseMode = useCallback(async (mode) => {
    try { await api.strategy8SetReverseMode(mode); await fetchStatus(); }
    catch (e) { alert(`Reverse mode failed: ${e.message || e}`); }
  }, [fetchStatus]);

  const saveReverseStrikes = useCallback(async (payload) => {
    try { await api.strategy8SetReverseStrikes(payload); await fetchStatus(); }
    catch (e) { alert(`Save reverse strikes failed: ${e.message || e}`); }
  }, [fetchStatus]);

  const onStart = useCallback(async () => {
    if (!status?.strikes?.ce_symbol && !status?.strikes?.pe_symbol) {
      alert('Pick CE and/or PE strike(s) first.'); return;
    }
    if (!(status?.lines?.call_line > 0) && !(status?.lines?.put_line > 0)) {
      alert('Set at least one CALL or PUT line.'); return;
    }
    setStarting(true);
    try {
      await api.strategy8TradeStart({
        ...config,
        call_line: status?.lines?.call_line ?? 0,
        put_line: status?.lines?.put_line ?? 0,
        ce_strike: status?.strikes?.ce_strike ?? 0,
        ce_symbol: status?.strikes?.ce_symbol ?? '',
        ce_token: status?.strikes?.ce_token ?? 0,
        pe_strike: status?.strikes?.pe_strike ?? 0,
        pe_symbol: status?.strikes?.pe_symbol ?? '',
        pe_token: status?.strikes?.pe_token ?? 0,
        reverse_mode: status?.reverse?.mode ?? 'AUTO',
        reverse_offset: status?.reverse?.offset ?? 200,
        manual_pe_strike: status?.reverse?.manual_pe_strike ?? 0,
        manual_pe_symbol: status?.reverse?.manual_pe_symbol ?? '',
        manual_ce_strike: status?.reverse?.manual_ce_strike ?? 0,
        manual_ce_symbol: status?.reverse?.manual_ce_symbol ?? '',
      });
      await fetchStatus();
    } catch (e) { alert(`Start failed: ${e.message || e}`); }
    finally { setStarting(false); }
  }, [config, status, fetchStatus]);

  const onStop = useCallback(async () => {
    setStopping(true);
    try { await api.strategy8TradeStop(); await fetchStatus(); }
    catch (e) { alert(`Stop failed: ${e.message || e}`); }
    finally { setStopping(false); }
  }, [fetchStatus]);

  const saveConfig = useCallback(async () => {
    try { await api.strategy8TradeUpdateConfig(config); await fetchStatus(); setConfigOpen(false); }
    catch (e) { alert(`Config save failed: ${e.message || e}`); }
  }, [config, fetchStatus]);

  /* ── Derived ── */
  const stateKey = status?.state || 'IDLE';
  const stStyle = STATE_STYLE[stateKey] || STATE_STYLE.IDLE;
  const sig = status?.signal || 'NO_TRADE';
  const trade = status?.trade || {};
  const orders = status?.orders || {};
  const lines = status?.lines || {};
  const strikes = status?.strikes || {};
  const ltp = status?.ltp || {};
  const tradesToday = status?.trades_today ?? 0;
  const reverse = status?.reverse || {};
  const isManual = (reverse.mode || 'AUTO') === 'MANUAL';
  const previewCall = reverse.preview_on_call_trigger || null;  // PE bought on CALL trigger
  const previewPut  = reverse.preview_on_put_trigger || null;   // CE bought on PUT trigger

  return (
    <div className="p-4 space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">
            <Repeat className="w-5 h-5 text-amber-400" />
            Strategy 8 — CE/PE Reverse Line Touch
            <span className="text-[10px] font-normal text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded px-1.5 py-0.5">
              REVERSE
            </span>
          </h1>
          <p className="text-xs text-gray-400 mt-0.5">
            Monitor CE & PE strike LTPs. <b className="text-emerald-400">CALL touch → BUY PUT</b>.
            <b className="text-rose-400"> PUT touch → BUY CALL</b>. Auto 200-pt ITM or manual reverse strike.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setDocOpen((v) => !v)}
            className="text-xs px-3 py-1.5 rounded border border-surface-3 text-gray-300 hover:text-white"
          >
            <Info className="w-3.5 h-3.5 inline mr-1" /> How it works
          </button>
          <button
            onClick={() => setConfigOpen((v) => !v)}
            className="text-xs px-3 py-1.5 rounded border border-surface-3 text-gray-300 hover:text-white"
          >
            <Settings2 className="w-3.5 h-3.5 inline mr-1" /> Config
            {configOpen ? <ChevronUp className="w-3 h-3 inline ml-1" /> : <ChevronDown className="w-3 h-3 inline ml-1" />}
          </button>
          {status?.is_active ? (
            <button
              onClick={onStop} disabled={stopping}
              className="text-xs px-3 py-1.5 rounded bg-rose-600/20 border border-rose-500/40 text-rose-300 hover:bg-rose-600/30 disabled:opacity-50"
            >
              <Square className="w-3.5 h-3.5 inline mr-1" /> {stopping ? 'Stopping…' : 'Stop'}
            </button>
          ) : (
            <button
              onClick={onStart} disabled={starting}
              className="text-xs px-3 py-1.5 rounded bg-emerald-600/20 border border-emerald-500/40 text-emerald-300 hover:bg-emerald-600/30 disabled:opacity-50"
            >
              <Play className="w-3.5 h-3.5 inline mr-1" /> {starting ? 'Starting…' : 'Start'}
            </button>
          )}
        </div>
      </div>

      {docOpen && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 text-xs text-gray-300 leading-relaxed">
          <p><span className="text-amber-400 font-semibold">What is reverse?</span> Unlike Strategy 7 which buys the SAME side on touch, Strategy 8 buys the OPPOSITE side. Touching the CALL line on CE LTP signals strength → we BUY a PUT (fade). Touching the PUT line on PE LTP signals weakness → we BUY a CALL.</p>
          <p className="mt-2"><span className="text-amber-400 font-semibold">Auto mode:</span> The reverse strike is computed as <code className="text-white">monitored ± 200</code>. Example: monitoring <code className="text-emerald-400">24300CE</code> → on CALL touch we BUY <code className="text-rose-400">24500PE</code>. Monitoring <code className="text-rose-400">24300PE</code> → on PUT touch we BUY <code className="text-emerald-400">24100CE</code>.</p>
          <p className="mt-2"><span className="text-amber-400 font-semibold">Manual mode:</span> You explicitly pick the reverse PE (used on CALL trigger) and the reverse CE (used on PUT trigger).</p>
          <p className="mt-2"><span className="text-amber-400 font-semibold">No index data:</span> Charts and triggers use option strike LTPs only — no NIFTY index feed appears anywhere in the trigger chain.</p>
        </div>
      )}

      {configOpen && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 grid grid-cols-2 md:grid-cols-5 gap-3 text-xs">
          {[
            ['SL points', 'sl_points'], ['Target points', 'target_points'],
            ['Lot size', 'lot_size'], ['Lots', 'lots'],
            ['Strike interval', 'strike_interval'],
            ['SL proximity', 'sl_proximity'], ['Target proximity', 'target_proximity'],
            ['Max trades / day', 'max_trades_per_day'],
            ['Max entry slippage', 'max_entry_slippage'],
          ].map(([label, key]) => (
            <label key={key} className="flex flex-col gap-1">
              <span className="text-gray-400">{label}</span>
              <input
                type="number" step="any" value={config[key]}
                onChange={(e) => setConfig((c) => ({ ...c, [key]: e.target.value === '' ? '' : Number(e.target.value) }))}
                className="bg-surface-3 border border-surface-3 rounded px-2 py-1 text-white"
              />
            </label>
          ))}
          <div className="col-span-full flex justify-end">
            <button onClick={saveConfig}
              className="text-xs px-3 py-1.5 rounded bg-emerald-600/20 border border-emerald-500/40 text-emerald-300 hover:bg-emerald-600/30">
              Save config
            </button>
          </div>
        </div>
      )}

      {/* Top stat strip */}
      <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
        <Card title="State" icon={Clock}>
          <span className={`px-2 py-1 rounded text-xs font-semibold ${stStyle.bg} ${stStyle.text}`}>{stStyle.label}</span>
        </Card>
        <Card title="Signal" icon={Zap}>
          <span className={`px-2 py-1 rounded text-xs font-semibold border ${signalBg(sig)} ${signalColor(sig)}`}>{sig}</span>
        </Card>
        <Card title="ATM Reference" icon={Crosshair}>
          <div className="text-lg font-bold text-white">{strikesData.atm || '—'}</div>
          <div className="text-[10px] text-gray-500">Strike picker reference (no index trigger)</div>
        </Card>
        <Card title="CE LTP" icon={TrendingUp}>
          <div className="text-lg font-bold text-green-400">{fmt(ltp.ce)}</div>
          <div className="text-[10px] text-gray-500 truncate">{strikes.ce_symbol || '— select CE strike —'}</div>
        </Card>
        <Card title="PE LTP" icon={Shield}>
          <div className="text-lg font-bold text-red-400">{fmt(ltp.pe)}</div>
          <div className="text-[10px] text-gray-500 truncate">{strikes.pe_symbol || '— select PE strike —'}</div>
        </Card>
        <Card title="Trades today" icon={Target}>
          <div className="text-lg font-bold text-white">{tradesToday} / {config.max_trades_per_day}</div>
          <div className="text-[10px] text-gray-500 truncate">{status?.scenario || '—'}</div>
        </Card>
      </div>

      {/* Reverse-mode panel */}
      <div className="bg-surface-2 border border-amber-500/30 rounded-xl p-4 space-y-3">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <Repeat className="w-4 h-4 text-amber-400" />
            <h3 className="text-sm font-semibold text-white">Reverse Strike Selection</h3>
            <span className="text-[10px] text-gray-500">only one mode active at a time</span>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <button
              onClick={() => setReverseMode('AUTO')}
              className={`px-3 py-1.5 rounded border transition ${
                !isManual
                  ? 'bg-amber-500/20 border-amber-500/40 text-amber-300'
                  : 'border-surface-3 text-gray-400 hover:text-white'
              }`}
            >
              <input type="radio" readOnly checked={!isManual} className="mr-1.5 accent-amber-500" />
              Auto 200 ITM
            </button>
            <button
              onClick={() => setReverseMode('MANUAL')}
              className={`px-3 py-1.5 rounded border transition ${
                isManual
                  ? 'bg-amber-500/20 border-amber-500/40 text-amber-300'
                  : 'border-surface-3 text-gray-400 hover:text-white'
              }`}
            >
              <input type="radio" readOnly checked={isManual} className="mr-1.5 accent-amber-500" />
              Manual Reverse Strikes
            </button>
          </div>
        </div>

        {!isManual && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
            <div className="bg-surface-3 rounded p-2">
              <div className="text-[10px] text-gray-500 uppercase mb-1">On CALL line touch</div>
              <div className="text-white">
                {strikes.ce_strike ? (
                  <>Monitoring <span className="text-emerald-400 font-semibold">{strikes.ce_strike} CE</span> → BUY <span className="text-rose-400 font-semibold">{previewCall?.strike || (strikes.ce_strike + (reverse.offset || 200))} PE</span></>
                ) : 'Select a CE strike to preview the reverse PUT'}
              </div>
              {previewCall?.tradingsymbol && (
                <div className="text-[10px] text-gray-500 mt-0.5">{previewCall.tradingsymbol}</div>
              )}
            </div>
            <div className="bg-surface-3 rounded p-2">
              <div className="text-[10px] text-gray-500 uppercase mb-1">On PUT line touch</div>
              <div className="text-white">
                {strikes.pe_strike ? (
                  <>Monitoring <span className="text-rose-400 font-semibold">{strikes.pe_strike} PE</span> → BUY <span className="text-emerald-400 font-semibold">{previewPut?.strike || (strikes.pe_strike - (reverse.offset || 200))} CE</span></>
                ) : 'Select a PE strike to preview the reverse CALL'}
              </div>
              {previewPut?.tradingsymbol && (
                <div className="text-[10px] text-gray-500 mt-0.5">{previewPut.tradingsymbol}</div>
              )}
            </div>
          </div>
        )}

        {isManual && (
          <ReverseStrikePicker
            strikes={strikesData.strikes}
            currentManualPe={reverse.manual_pe_strike}
            currentManualCe={reverse.manual_ce_strike}
            onCommit={saveReverseStrikes}
          />
        )}
      </div>

      {/* Strategy Status Panel */}
      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-gray-400">
            <Flame className="w-3.5 h-3.5 text-amber-400" /> Strategy Status
          </div>
          <div className="text-[10px] text-gray-500">live</div>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-xs">
          <div>
            <div className="text-gray-500">Reverse mode</div>
            <div className="text-white font-semibold">{reverse.mode || 'AUTO'}</div>
          </div>
          <div>
            <div className="text-gray-500">Monitored CALL</div>
            <div className="text-emerald-400 font-semibold">
              {strikes.ce_symbol || '—'}
            </div>
            <div className="text-[10px] text-gray-500">line {fmt(lines.call_line)}</div>
          </div>
          <div>
            <div className="text-gray-500">Monitored PUT</div>
            <div className="text-rose-400 font-semibold">
              {strikes.pe_symbol || '—'}
            </div>
            <div className="text-[10px] text-gray-500">line {fmt(lines.put_line)}</div>
          </div>
          <div>
            <div className="text-gray-500">Last trigger</div>
            <div className="text-white font-semibold">{status?.trigger?.last_side || '—'}</div>
            <div className="text-[10px] text-gray-500">
              {status?.trigger?.last_at ? new Date(status.trigger.last_at).toLocaleTimeString() : '—'}
            </div>
          </div>
          <div>
            <div className="text-gray-500">Active position</div>
            <div className={`font-semibold ${trade.signal_type === 'CE' ? 'text-emerald-400' : trade.signal_type === 'PE' ? 'text-rose-400' : 'text-gray-400'}`}>
              {trade.option_symbol || '—'}
            </div>
            <div className="text-[10px] text-gray-500">{trade.fill_price ? `@ ${fmt(trade.fill_price)}` : ''}</div>
          </div>
        </div>
      </div>

      {/* Side-by-side chart panels */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <SidePanel
          side="CE" strikes={strikesData.strikes}
          selectedStrike={strikes.ce_strike || 0}
          onPickStrike={(s) => pickStrike('CE', s)}
          ltp={ltp.ce || 0} line={lines.call_line || 0}
          draft={callDraft} setDraft={setCallDraft}
          onCommitLine={commitCallLine} onClearLine={clearCallLine}
          series={ceSeries}
          reverseLabel={previewCall ? `${previewCall.strike} PE` : (isManual ? 'manual PE' : (strikes.ce_strike ? `${strikes.ce_strike + (reverse.offset || 200)} PE` : null))}
          triggerActive={flashSide === 'CALL'}
        />
        <SidePanel
          side="PE" strikes={strikesData.strikes}
          selectedStrike={strikes.pe_strike || 0}
          onPickStrike={(s) => pickStrike('PE', s)}
          ltp={ltp.pe || 0} line={lines.put_line || 0}
          draft={putDraft} setDraft={setPutDraft}
          onCommitLine={commitPutLine} onClearLine={clearPutLine}
          series={peSeries}
          reverseLabel={previewPut ? `${previewPut.strike} CE` : (isManual ? 'manual CE' : (strikes.pe_strike ? `${strikes.pe_strike - (reverse.offset || 200)} CE` : null))}
          triggerActive={flashSide === 'PUT'}
        />
      </div>

      {/* Active trade panel */}
      <Card title="Active Trade" icon={Target}>
        {stateKey === 'POSITION_OPEN' || stateKey === 'ORDER_PLACED' ? (
          <div className="grid grid-cols-2 md:grid-cols-6 gap-3 text-xs">
            <div>
              <div className="text-gray-500">Bought</div>
              <div className={`font-semibold ${trade.signal_type === 'CE' ? 'text-green-400' : 'text-red-400'}`}>
                {trade.signal_type || '—'}
              </div>
              <div className="text-[10px] text-gray-500">trigger {trade.trigger_side || '—'}</div>
            </div>
            <div>
              <div className="text-gray-500">Symbol</div>
              <div className="text-white font-medium truncate">{trade.option_symbol || '—'}</div>
            </div>
            <div>
              <div className="text-gray-500">Fill</div>
              <div className="text-white font-medium">{fmt(trade.fill_price)}</div>
            </div>
            <div>
              <div className="text-gray-500">Current LTP</div>
              <div className="text-white font-medium">{fmt(trade.current_ltp || trade.option_ltp)}</div>
            </div>
            <div>
              <div className="text-gray-500">SL / Target</div>
              <div className="text-white font-medium">
                <span className="text-rose-400">{fmt(trade.sl_price)}</span> / <span className="text-emerald-400">{fmt(trade.target_price)}</span>
              </div>
              <div className="text-[10px] text-gray-500">
                {orders.sl_shadow ? 'SL shadow' : 'SL real'} · {orders.target_shadow ? 'TGT shadow' : 'TGT real'}
              </div>
            </div>
            <div>
              <div className="text-gray-500">Unrealized PnL</div>
              <div className={`font-bold ${(trade.unrealized_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                ₹ {fmt(trade.unrealized_pnl, 2)}
              </div>
            </div>
          </div>
        ) : (
          <div className="text-xs text-gray-400">{status?.scenario || 'Idle — pick strikes and draw lines to arm.'}</div>
        )}
      </Card>

      {/* Trade history */}
      <Card title="Trade History" icon={CheckCircle2}>
        {trades.length === 0 ? (
          <div className="text-xs text-gray-500">No trades yet.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-gray-500 border-b border-surface-3">
                  <th className="py-1">Date</th><th>Trigger</th><th>Bought</th>
                  <th>Option</th><th>Entry</th><th>Exit</th>
                  <th>Type</th><th>Time</th><th className="text-right">PnL</th>
                </tr>
              </thead>
              <tbody>
                {trades.slice().reverse().slice(0, 50).map((t, i) => (
                  <tr key={i} className="border-b border-surface-3/50">
                    <td className="py-1 text-gray-400">{t.date}</td>
                    <td className={t.trigger_side === 'CALL' ? 'text-emerald-400' : 'text-rose-400'}>{t.trigger_side || '—'}</td>
                    <td className={t.signal === 'CE' ? 'text-green-400' : 'text-red-400'}>{t.signal}</td>
                    <td className="text-white truncate max-w-[180px]">{t.option}</td>
                    <td>{fmt(t.entry_price)}</td>
                    <td>{fmt(t.exit_price)}</td>
                    <td className="text-gray-300">{t.exit_type}</td>
                    <td className="text-gray-500">{t.exit_time}</td>
                    <td className={`text-right font-semibold ${t.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                      ₹ {fmt(t.pnl, 2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Backtest */}
      <BacktestPanel
        strategy="S8"
        strikes={{
          ce_strike: strikes.ce_strike, ce_token: strikes.ce_token,
          pe_strike: strikes.pe_strike, pe_token: strikes.pe_token,
        }}
        lines={lines}
        reverseTokens={{
          reverse_ce_token: reverse.preview_on_put_trigger?.token || 0,
          reverse_pe_token: reverse.preview_on_call_trigger?.token || 0,
        }}
        manualReverse={{
          manual_pe_strike: reverse.manual_pe_strike,
          manual_ce_strike: reverse.manual_ce_strike,
        }}
        reverseOffset={reverse.offset || 200}
        defaultConfig={config}
        runBacktest={(payload) => api.strategy8Backtest(payload)}
      />
    </div>
  );
}
