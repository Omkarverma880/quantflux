import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  LineChart, Line, XAxis, YAxis, ResponsiveContainer,
  ReferenceLine, Tooltip, CartesianGrid,
} from 'recharts';
import {
  Play, Square, Settings2, ChevronDown, ChevronUp,
  Shield, Target, TrendingUp, Zap, Pencil,
  CheckCircle2, XCircle, Clock, Info, Trash2,
  Crosshair, Anchor,
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
  if (s === 'BUY_CALL') return 'text-emerald-400';
  if (s === 'BUY_PUT')  return 'text-rose-400';
  return 'text-yellow-400';
}
function signalBg(s) {
  if (s === 'BUY_CALL') return 'bg-emerald-500/15 border-emerald-500/30';
  if (s === 'BUY_PUT')  return 'bg-rose-500/15 border-rose-500/30';
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
   Per-line row: number input + Set + Pick + Delete.
   Each of the 3 lines per side uses one of these.
   ───────────────────────────────────────────────────── */
function LineRow({ label, color, value, draft, setDraft, onCommit, onClear, onPickToggle, picking }) {
  return (
    <div className="flex items-center gap-1.5 text-[11px]">
      <span className={`w-12 font-bold uppercase tracking-wider`} style={{ color }}>{label}</span>
      <input
        type="number" step="0.05" value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            const v = parseFloat(draft);
            if (Number.isFinite(v)) onCommit(v);
          }
        }}
        placeholder={`${label} price`}
        className="flex-1 bg-surface-3 border border-surface-3 rounded px-2 py-1 text-white"
      />
      <button
        onClick={() => { const v = parseFloat(draft); if (Number.isFinite(v)) onCommit(v); }}
        className="px-2 py-1 rounded border border-surface-3 text-gray-300 hover:text-white"
        title="Set"
      >Set</button>
      <button
        onClick={onPickToggle}
        title="Click on chart to set the line"
        className={`px-2 py-1 rounded border ${picking ? 'bg-cyan-500/20 border-cyan-500/40 text-cyan-300' : 'border-surface-3 text-gray-400 hover:text-white'}`}
      ><Crosshair className="w-3 h-3" /></button>
      <button
        disabled={!value}
        onClick={() => { setDraft(''); onClear(); }}
        title="Delete line"
        className="px-2 py-1 rounded border border-surface-3 text-gray-400 hover:text-rose-400 disabled:opacity-30 disabled:cursor-not-allowed"
      ><Trash2 className="w-3 h-3" /></button>
      <span className="w-14 text-right text-gray-500">{value > 0 ? fmt(value) : '—'}</span>
    </div>
  );
}

/* ─────────────────────────────────────────────────────
   Side panel — CE or PE — with 3 lines (BUY/TARGET/SL).
   ───────────────────────────────────────────────────── */
function SidePanel({
  side, strikes, selectedStrike, onPickStrike,
  ltp, lines, drafts, setDraft, onCommitLine, onClearLine,
  series, triggerActive,
}) {
  const isCall = side === 'CE';
  const accentText = isCall ? 'text-green-400' : 'text-red-400';
  const accentBorder = isCall ? 'border-green-500/40' : 'border-red-500/40';
  const accentBg = isCall ? 'bg-green-500/10' : 'bg-red-500/10';
  const accentRing = isCall ? 'ring-green-500/40' : 'ring-red-500/40';
  const seriesColor = isCall ? '#22c55e' : '#ef4444';

  const COLORS = { buy: '#3b82f6', target: '#10b981', sl: '#ef4444' };
  const LABELS = { buy: 'BUY', target: 'TARGET', sl: 'SL' };

  const wrapRef = useRef(null);
  const [pickKind, setPickKind] = useState(null); // 'buy' | 'target' | 'sl' | null
  const [dragKind, setDragKind] = useState(null);
  const yRangeRef = useRef({ min: 0, max: 0 });

  const data = useMemo(
    () => (series || []).map((p, i) => ({ x: i, t: p.t, y: Number(p.y) })),
    [series],
  );
  const [yMin, yMax] = useMemo(() => {
    const vals = data.map((d) => d.y).filter((v) => Number.isFinite(v) && v > 0);
    ['buy', 'target', 'sl'].forEach((k) => { if (lines?.[k] > 0) vals.push(lines[k]); });
    if (ltp > 0) vals.push(ltp);
    if (!vals.length) return [0, 1];
    let lo = Math.min(...vals); let hi = Math.max(...vals);
    const pad = Math.max(2, (hi - lo) * 0.1);
    lo = Math.max(0, lo - pad); hi = hi + pad;
    yRangeRef.current = { min: lo, max: hi };
    return [lo, hi];
  }, [data, lines, ltp]);

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
    if (!pickKind) return;
    const price = priceFromMouse(evt);
    if (price == null) return;
    const v = Number(price.toFixed(2));
    setDraft(pickKind, v.toFixed(2));
    onCommitLine(pickKind, v);
    setPickKind(null);
  }, [pickKind, priceFromMouse, setDraft, onCommitLine]);

  useEffect(() => {
    if (!dragKind) return;
    const onMove = (e) => {
      const price = priceFromMouse(e);
      if (price != null) setDraft(dragKind, price.toFixed(2));
    };
    const onUp = (e) => {
      const price = priceFromMouse(e);
      const kind = dragKind;
      setDragKind(null);
      if (price != null) onCommitLine(kind, Number(price.toFixed(2)));
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, [dragKind, priceFromMouse, setDraft, onCommitLine]);

  const sym = isCall ? 'ce_symbol' : 'pe_symbol';
  const opts = strikes || [];

  return (
    <div className={`bg-surface-2 border ${accentBorder} rounded-xl p-3 flex flex-col relative ${triggerActive ? 'ring-2 ring-amber-400/60 animate-pulse' : ''}`}>
      <div className="flex items-center justify-between mb-2">
        <div className={`flex items-center gap-2 text-xs font-bold uppercase tracking-wider ${accentText}`}>
          {isCall ? <TrendingUp className="w-3.5 h-3.5" /> : <Shield className="w-3.5 h-3.5" />}
          {isCall ? 'CALL Side (CE)' : 'PUT Side (PE)'}
          <span className="ml-1 text-[10px] font-normal text-gray-500">3 lines · BUY / TARGET / SL</span>
        </div>
        <div className="text-[11px] text-gray-500">LTP {fmt(ltp)}</div>
      </div>

      {/* Strike picker */}
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

      {/* 3 line rows */}
      <div className="flex flex-col gap-1.5 mb-2">
        {['buy', 'target', 'sl'].map((k) => (
          <LineRow
            key={k}
            label={LABELS[k]}
            color={COLORS[k]}
            value={lines?.[k] || 0}
            draft={drafts[k]}
            setDraft={(v) => setDraft(k, v)}
            onCommit={(v) => onCommitLine(k, v)}
            onClear={() => onClearLine(k)}
            picking={pickKind === k}
            onPickToggle={() => setPickKind((p) => (p === k ? null : k))}
          />
        ))}
      </div>

      {/* Chart */}
      <div
        ref={wrapRef}
        onClick={handleClick}
        className={`relative h-56 select-none ${pickKind ? 'cursor-crosshair' : 'cursor-default'}`}
      >
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 6, right: 8, left: 4, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
            <XAxis dataKey="t" hide />
            <YAxis domain={[yMin, yMax]} tick={{ fontSize: 10, fill: '#9ca3af' }} width={48} />
            <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #1f2937', fontSize: 11 }}
              formatter={(v) => [fmt(v), 'LTP']} />
            <Line type="monotone" dataKey="y" stroke={seriesColor} strokeWidth={1.6} dot={false} isAnimationActive={false} />
            {['buy', 'target', 'sl'].map((k) => (
              lines?.[k] > 0 && (
                <ReferenceLine
                  key={k}
                  y={lines[k]}
                  stroke={COLORS[k]}
                  strokeDasharray="6 4"
                  strokeWidth={1.5}
                  label={{ value: `${LABELS[k]} ${fmt(lines[k])}`, position: 'right', fill: COLORS[k], fontSize: 11 }}
                />
              )
            ))}
            {ltp > 0 && (
              <ReferenceLine y={ltp} stroke="#94a3b8" strokeDasharray="2 4" strokeWidth={1} />
            )}
          </LineChart>
        </ResponsiveContainer>

        {/* Per-line drag/clear handles */}
        <div className="absolute right-2 top-2 flex flex-col gap-1 z-10">
          {['buy', 'target', 'sl'].map((k) => (
            lines?.[k] > 0 && (
              <div key={k} className="flex items-center gap-1">
                <span className="text-[9px] uppercase font-bold" style={{ color: COLORS[k] }}>{LABELS[k]}</span>
                <button
                  onMouseDown={(e) => { e.stopPropagation(); setDragKind(k); }}
                  title={`Drag ${LABELS[k]} line`}
                  className="p-1 rounded border border-surface-3 bg-surface-2 text-gray-300 hover:text-white"
                  style={{ borderColor: COLORS[k], color: COLORS[k] }}
                ><Pencil className="w-3 h-3" /></button>
                <button
                  onClick={(e) => { e.stopPropagation(); onClearLine(k); }}
                  title={`Delete ${LABELS[k]} line`}
                  className="p-1 rounded border border-rose-500/40 bg-rose-500/10 text-rose-400"
                ><XCircle className="w-3 h-3" /></button>
              </div>
            )
          ))}
        </div>
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────
   Main page
   ───────────────────────────────────────────────────── */
export default function Strategy9() {
  const [status, setStatus] = useState(null);
  const [strikesData, setStrikesData] = useState({ atm: 0, strikes: [] });
  const [ceSeries, setCeSeries] = useState([]);
  const [peSeries, setPeSeries] = useState([]);
  const [ceDrafts, setCeDrafts] = useState({ buy: '', target: '', sl: '' });
  const [peDrafts, setPeDrafts] = useState({ buy: '', target: '', sl: '' });
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
      const res = await api.getStrategy9TradeStatus();
      setStatus(res);
      if (res?.config && !configSeededRef.current) {
        setConfig((c) => ({ ...c, ...res.config }));
        configSeededRef.current = true;
      }
    } catch (e) { console.error('s9 status', e); }
  }, []);

  const fetchStrikes = useCallback(async () => {
    try {
      const res = await api.getStrategy9Strikes();
      if (res?.status === 'ok') setStrikesData(res);
    } catch (e) { console.error('s9 strikes', e); }
  }, []);

  const fetchSeries = useCallback(async () => {
    try {
      if (status?.strikes?.ce_symbol) {
        const r = await api.getStrategy9Intraday('CE');
        if (r?.status === 'ok') setCeSeries(r.series || []);
      }
      if (status?.strikes?.pe_symbol) {
        const r = await api.getStrategy9Intraday('PE');
        if (r?.status === 'ok') setPeSeries(r.series || []);
      }
    } catch (e) { console.error('s9 series', e); }
  }, [status?.strikes?.ce_symbol, status?.strikes?.pe_symbol]);

  const fetchHistory = useCallback(async () => {
    try {
      const r = await api.strategy9TradeHistory();
      setTrades(r?.trades || []);
    } catch (e) { console.error('s9 history', e); }
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
      await api.strategy9SetStrikes(payload);
      if (side === 'CE') setCeSeries([]); else setPeSeries([]);
      await fetchStatus();
    } catch (e) { alert(`Failed to set strike: ${e.message || e}`); }
  }, [fetchStatus]);

  const KEY_TO_FIELD = {
    CE: { buy: 'ce_buy_line', target: 'ce_target_line', sl: 'ce_sl_line' },
    PE: { buy: 'pe_buy_line', target: 'pe_target_line', sl: 'pe_sl_line' },
  };

  const commitLine = useCallback(async (side, kind, value) => {
    try {
      const field = KEY_TO_FIELD[side][kind];
      await api.strategy9UpdateLines({ [field]: value });
      await fetchStatus();
    } catch (e) { alert(`Failed: ${e.message || e}`); }
  }, [fetchStatus]);

  const clearLine = useCallback((side, kind) => commitLine(side, kind, 0), [commitLine]);

  const setCeDraftKey = useCallback((k, v) => setCeDrafts((d) => ({ ...d, [k]: v })), []);
  const setPeDraftKey = useCallback((k, v) => setPeDrafts((d) => ({ ...d, [k]: v })), []);

  const onStart = useCallback(async () => {
    if (!status?.strikes?.ce_symbol && !status?.strikes?.pe_symbol) {
      alert('Pick CE and/or PE strike(s) first.'); return;
    }
    const l = status?.lines || { ce: {}, pe: {} };
    const hasAny = (l.ce?.buy > 0) || (l.pe?.buy > 0);
    if (!hasAny) { alert('Set at least one BUY line (CE or PE).'); return; }
    setStarting(true);
    try {
      await api.strategy9TradeStart({
        ...config,
        ce_buy_line:    l.ce?.buy    ?? 0,
        ce_target_line: l.ce?.target ?? 0,
        ce_sl_line:     l.ce?.sl     ?? 0,
        pe_buy_line:    l.pe?.buy    ?? 0,
        pe_target_line: l.pe?.target ?? 0,
        pe_sl_line:     l.pe?.sl     ?? 0,
        ce_strike: status?.strikes?.ce_strike ?? 0,
        ce_symbol: status?.strikes?.ce_symbol ?? '',
        ce_token:  status?.strikes?.ce_token  ?? 0,
        pe_strike: status?.strikes?.pe_strike ?? 0,
        pe_symbol: status?.strikes?.pe_symbol ?? '',
        pe_token:  status?.strikes?.pe_token  ?? 0,
      });
      await fetchStatus();
    } catch (e) { alert(`Start failed: ${e.message || e}`); }
    finally { setStarting(false); }
  }, [config, status, fetchStatus]);

  const onStop = useCallback(async () => {
    setStopping(true);
    try { await api.strategy9TradeStop(); await fetchStatus(); }
    catch (e) { alert(`Stop failed: ${e.message || e}`); }
    finally { setStopping(false); }
  }, [fetchStatus]);

  const saveConfig = useCallback(async () => {
    try { await api.strategy9TradeUpdateConfig(config); await fetchStatus(); setConfigOpen(false); }
    catch (e) { alert(`Config save failed: ${e.message || e}`); }
  }, [config, fetchStatus]);

  /* ── Derived ── */
  const stateKey = status?.state || 'IDLE';
  const stStyle = STATE_STYLE[stateKey] || STATE_STYLE.IDLE;
  const sig = status?.signal || 'NO_TRADE';
  const trade = status?.trade || {};
  const orders = status?.orders || {};
  const lines = status?.lines || { ce: {}, pe: {} };
  const strikes = status?.strikes || {};
  const ltp = status?.ltp || {};
  const tradesToday = status?.trades_today ?? 0;

  return (
    <div className="p-4 space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">
            <Anchor className="w-5 h-5 text-cyan-400" />
            Strategy 9 — Line Of Control
            <span className="text-[10px] font-normal text-cyan-300 bg-cyan-500/10 border border-cyan-500/30 rounded px-1.5 py-0.5">
              LOC
            </span>
          </h1>
          <p className="text-xs text-gray-400 mt-0.5">
            Direct option trading. <b className="text-emerald-400">CE BUY touch → BUY CALL</b>.
            <b className="text-rose-400"> PE BUY touch → BUY PUT</b>. TARGET / SL lines are live-draggable during the open trade.
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
          <p><span className="text-cyan-400 font-semibold">Pure line-driven trading.</span> Each side (CE & PE) has 3 lines: <b className="text-blue-400">BUY</b> (entry trigger), <b className="text-emerald-400">TARGET</b> (profit exit), <b className="text-rose-400">SL</b> (stop-loss exit).</p>
          <p className="mt-2"><span className="text-cyan-400 font-semibold">Direct entry:</span> When CE LTP ≥ CE BUY line → we BUY the CE strike. When PE LTP ≥ PE BUY line → we BUY the PE strike. <em>No reverse logic.</em></p>
          <p className="mt-2"><span className="text-cyan-400 font-semibold">Live exits:</span> The TARGET and SL lines remain editable while the position is open. Drag, click-pick or type a new value — the next 1s tick re-evaluates exits against the latest line values.</p>
          <p className="mt-2"><span className="text-cyan-400 font-semibold">Single-cycle:</span> One entry per cycle. After target or SL hits, the strategy returns to IDLE and waits for the next BUY-line touch.</p>
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

      {/* Strategy Status Panel */}
      <div className="bg-surface-2 border border-cyan-500/30 rounded-xl p-4">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-gray-400">
            <Anchor className="w-3.5 h-3.5 text-cyan-400" /> Line Of Control
          </div>
          <div className="text-[10px] text-gray-500">live · drag-aware</div>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          <div className="bg-surface-3 rounded p-2">
            <div className="text-emerald-400 font-semibold mb-1">CE — {strikes.ce_symbol || '—'}</div>
            <div className="flex flex-col gap-0.5 text-[11px]">
              <div><span className="text-blue-400">BUY</span>     <span className="text-white ml-2">{fmt(lines.ce?.buy)}</span></div>
              <div><span className="text-emerald-400">TARGET</span> <span className="text-white ml-2">{fmt(lines.ce?.target)}</span></div>
              <div><span className="text-rose-400">SL</span>      <span className="text-white ml-2">{fmt(lines.ce?.sl)}</span></div>
            </div>
          </div>
          <div className="bg-surface-3 rounded p-2">
            <div className="text-rose-400 font-semibold mb-1">PE — {strikes.pe_symbol || '—'}</div>
            <div className="flex flex-col gap-0.5 text-[11px]">
              <div><span className="text-blue-400">BUY</span>     <span className="text-white ml-2">{fmt(lines.pe?.buy)}</span></div>
              <div><span className="text-emerald-400">TARGET</span> <span className="text-white ml-2">{fmt(lines.pe?.target)}</span></div>
              <div><span className="text-rose-400">SL</span>      <span className="text-white ml-2">{fmt(lines.pe?.sl)}</span></div>
            </div>
          </div>
          <div>
            <div className="text-gray-500">Last trigger</div>
            <div className="text-white font-semibold">{status?.trigger?.last_side || '—'}</div>
            <div className="text-[10px] text-gray-500">
              {status?.trigger?.last_at ? new Date(status.trigger.last_at).toLocaleTimeString() : '—'}
              {status?.trigger?.last_price ? ` @ ${fmt(status.trigger.last_price)}` : ''}
            </div>
          </div>
          <div>
            <div className="text-gray-500">Active position</div>
            <div className={`font-semibold ${trade.signal_type === 'CE' ? 'text-emerald-400' : trade.signal_type === 'PE' ? 'text-rose-400' : 'text-gray-400'}`}>
              {trade.option_symbol || '—'}
            </div>
            <div className="text-[10px] text-gray-500">
              {trade.entry_time ? `entry ${trade.entry_time}` : ''}
              {trade.fill_price ? ` @ ${fmt(trade.fill_price)}` : ''}
            </div>
          </div>
        </div>
      </div>

      {/* Side-by-side chart panels */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <SidePanel
          side="CE" strikes={strikesData.strikes}
          selectedStrike={strikes.ce_strike || 0}
          onPickStrike={(s) => pickStrike('CE', s)}
          ltp={ltp.ce || 0}
          lines={lines.ce || {}}
          drafts={ceDrafts} setDraft={setCeDraftKey}
          onCommitLine={(k, v) => commitLine('CE', k, v)}
          onClearLine={(k) => clearLine('CE', k)}
          series={ceSeries}
          triggerActive={flashSide === 'CALL'}
        />
        <SidePanel
          side="PE" strikes={strikesData.strikes}
          selectedStrike={strikes.pe_strike || 0}
          onPickStrike={(s) => pickStrike('PE', s)}
          ltp={ltp.pe || 0}
          lines={lines.pe || {}}
          drafts={peDrafts} setDraft={setPeDraftKey}
          onCommitLine={(k, v) => commitLine('PE', k, v)}
          onClearLine={(k) => clearLine('PE', k)}
          series={peSeries}
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
              <div className="text-gray-500">Fill / Entry time</div>
              <div className="text-white font-medium">{fmt(trade.fill_price)}</div>
              <div className="text-[10px] text-gray-500">{trade.entry_time || '—'}</div>
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
                {orders.sl ? 'SL real' : 'SL pending'} · {orders.target ? 'TGT real' : 'TGT pending'}
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
          <div className="text-xs text-gray-400">{status?.scenario || 'Idle — pick strikes and draw the 6 lines (3 per side) to arm.'}</div>
        )}
      </Card>

      {/* Backtest */}
      <BacktestPanel
        strategy="S9"
        strikes={{
          ce_strike: strikes.ce_strike || 0,
          ce_token:  strikes.ce_token  || 0,
          pe_strike: strikes.pe_strike || 0,
          pe_token:  strikes.pe_token  || 0,
        }}
        s9Lines={lines}
        defaultConfig={config}
        runBacktest={api.strategy9Backtest}
      />

      {/* Trade history */}
      <Card title="Trade History" icon={CheckCircle2}>
        {trades.length === 0 ? (
          <div className="text-xs text-gray-500">No trades yet.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-gray-400 border-b border-surface-3">
                <tr>
                  <th className="text-left py-1.5 px-2">Date</th>
                  <th className="text-left px-2">Trigger</th>
                  <th className="text-left px-2">Bought</th>
                  <th className="text-left px-2">Symbol</th>
                  <th className="text-right px-2">Entry</th>
                  <th className="text-right px-2">Exit</th>
                  <th className="text-left px-2">Type</th>
                  <th className="text-right px-2">PnL</th>
                </tr>
              </thead>
              <tbody>
                {trades.slice().reverse().map((t, i) => (
                  <tr key={i} className="border-b border-surface-3/50 text-gray-200">
                    <td className="py-1.5 px-2">{t.date || '—'}</td>
                    <td className="px-2">{t.trigger_side || '—'}</td>
                    <td className={`px-2 ${t.signal === 'CE' ? 'text-emerald-400' : t.signal === 'PE' ? 'text-rose-400' : ''}`}>{t.signal || '—'}</td>
                    <td className="px-2 truncate max-w-[180px]">{t.option || '—'}</td>
                    <td className="px-2 text-right">{fmt(t.entry_price)}</td>
                    <td className="px-2 text-right">{fmt(t.exit_price)}</td>
                    <td className={`px-2 ${t.exit_type === 'TARGET' ? 'text-emerald-400' : t.exit_type === 'SL' ? 'text-rose-400' : 'text-gray-400'}`}>{t.exit_type || '—'}</td>
                    <td className={`px-2 text-right font-semibold ${(t.pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>₹ {fmt(t.pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
