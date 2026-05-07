import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import {
  LineChart, Line, XAxis, YAxis, ResponsiveContainer,
  ReferenceLine, Tooltip, CartesianGrid,
} from 'recharts';
import {
  Play, Square, Settings2, ChevronDown, ChevronUp,
  Shield, Target, TrendingUp, Zap, Pencil,
  CheckCircle2, XCircle, Clock, AlertCircle, Info, Trash2,
  Crosshair,
} from 'lucide-react';
import { api } from '../api';

const REFRESH_MS = 1_000;

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

function fmt(n, d = 2) {
  const v = Number(n);
  if (!Number.isFinite(v)) return '—';
  return v.toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
}

/* ─────────────────────────────────────────────────────
   Single side panel (CE or PE) — strike picker + chart
   with draggable / pickable / deletable horizontal line
   ───────────────────────────────────────────────────── */
function SidePanel({
  side,                  // 'CE' | 'PE'
  strikes,               // [{strike, ce_symbol, pe_symbol, is_atm}]
  selectedStrike,        // int
  onPickStrike,          // (strike) => void
  ltp,                   // current option LTP
  line,                  // current horizontal line price
  draft, setDraft,       // draft state for numeric input
  onCommitLine,          // () => commit draft to backend
  onClearLine,           // () => set line=0
  series,                // [{t, y}]
  positionMarker,        // { y, label, color } | null
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

  const data = useMemo(() => {
    return (series || []).map((p, i) => ({ x: i, t: p.t, y: Number(p.y) }));
  }, [series]);

  const [yMin, yMax] = useMemo(() => {
    const vals = data.map((d) => d.y).filter((v) => Number.isFinite(v) && v > 0);
    if (line > 0) vals.push(line);
    if (ltp > 0) vals.push(ltp);
    if (!vals.length) return [0, 1];
    let lo = Math.min(...vals);
    let hi = Math.max(...vals);
    const pad = Math.max(2, (hi - lo) * 0.1);
    lo = Math.max(0, lo - pad);
    hi = hi + pad;
    yRangeRef.current = { min: lo, max: hi };
    return [lo, hi];
  }, [data, line, ltp]);

  const priceFromMouse = useCallback((evt) => {
    const wrap = wrapRef.current;
    if (!wrap) return null;
    const rect = wrap.getBoundingClientRect();
    const y = evt.clientY - rect.top;
    const { min, max } = yRangeRef.current;
    if (max <= min) return null;
    const frac = 1 - y / rect.height;
    return min + frac * (max - min);
  }, []);

  const handleClick = useCallback(
    (evt) => {
      if (!pickMode) return;
      const price = priceFromMouse(evt);
      if (price == null) return;
      setDraft(price.toFixed(2));
      setPickMode(false);
      onCommitLine(Number(price.toFixed(2)));
    },
    [pickMode, priceFromMouse, setDraft, onCommitLine],
  );

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
    <div className={`bg-surface-2 border ${accentBorder} rounded-xl p-3 flex flex-col`}>
      <div className="flex items-center justify-between mb-2">
        <div className={`flex items-center gap-2 text-xs font-bold uppercase tracking-wider ${accentText}`}>
          {isCall ? <TrendingUp className="w-3.5 h-3.5" /> : <Shield className="w-3.5 h-3.5" />}
          {isCall ? 'CALL Side (CE)' : 'PUT Side (PE)'}
        </div>
        <div className="text-[11px] text-gray-500">LTP {fmt(ltp)}</div>
      </div>

      {/* Strike selector */}
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
                ${!s[sym] ? 'opacity-40 cursor-not-allowed' : ''}
              `}
            >
              {s.strike}
              {s.is_atm && <span className="block text-[8px] opacity-70">ATM</span>}
            </button>
          );
        })}
      </div>

      {/* Line controls */}
      <div className="flex items-center gap-2 mb-2">
        <input
          type="number"
          step="0.05"
          value={draft}
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
          onClick={() => {
            const v = parseFloat(draft);
            if (Number.isFinite(v)) onCommitLine(v);
          }}
          className={`text-[11px] px-2 py-1 rounded border ${accentBorder} ${accentText} hover:${accentBg}`}
        >
          Set
        </button>
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

      {/* Chart */}
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
            <Tooltip
              contentStyle={{ background: '#0f172a', border: '1px solid #1f2937', fontSize: 11 }}
              formatter={(v) => [fmt(v), 'LTP']}
            />
            <Line type="monotone" dataKey="y" stroke={lineColor} strokeWidth={1.6} dot={false} isAnimationActive={false} />
            {line > 0 && (
              <ReferenceLine
                y={line}
                stroke={lineColor}
                strokeDasharray="6 4"
                strokeWidth={1.5}
                label={{ value: `${isCall ? 'CALL' : 'PUT'} ${fmt(line)}`, position: 'right', fill: lineColor, fontSize: 11 }}
              />
            )}
            {ltp > 0 && (
              <ReferenceLine y={ltp} stroke="#94a3b8" strokeDasharray="2 4" strokeWidth={1} />
            )}
            {positionMarker && (
              <ReferenceLine
                y={positionMarker.y}
                stroke={positionMarker.color}
                strokeDasharray="4 2"
                label={{ value: positionMarker.label, position: 'left', fill: positionMarker.color, fontSize: 10 }}
              />
            )}
          </LineChart>
        </ResponsiveContainer>

        {line > 0 && (
          <div className="absolute right-2 top-2 flex items-center gap-1 z-10">
            <button
              onMouseDown={(e) => { e.stopPropagation(); setDrag(true); }}
              title="Drag the line"
              className={`p-1 rounded border ${accentBorder} ${accentBg} ${accentText}`}
            >
              <Pencil className="w-3 h-3" />
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); setDraft(''); onClearLine(); }}
              title="Delete line"
              className="p-1 rounded border border-rose-500/40 bg-rose-500/10 text-rose-400"
            >
              <XCircle className="w-3 h-3" />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────
   Main page
   ───────────────────────────────────────────────────── */
export default function Strategy7() {
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
    sl_points: 30,
    target_points: 60,
    lot_size: 65,
    lots: 1,
    strike_interval: 50,
    sl_proximity: 5,
    target_proximity: 5,
    max_trades_per_day: 3,
    max_entry_slippage: 8,
    index_name: 'NIFTY',
  });
  const configSeededRef = useRef(false);
  const timerRef = useRef(null);

  /* ── Fetch status + strikes ── */
  const fetchStatus = useCallback(async () => {
    try {
      const res = await api.getStrategy7TradeStatus();
      setStatus(res);
      if (res?.config && !configSeededRef.current) {
        setConfig((c) => ({ ...c, ...res.config }));
        configSeededRef.current = true;
      }
    } catch (e) {
      console.error('s7 status', e);
    }
  }, []);

  const fetchStrikes = useCallback(async () => {
    try {
      const res = await api.getStrategy7Strikes();
      if (res?.status === 'ok') setStrikesData(res);
    } catch (e) {
      console.error('s7 strikes', e);
    }
  }, []);

  const fetchSeries = useCallback(async () => {
    try {
      if (status?.strikes?.ce_symbol) {
        const r = await api.getStrategy7Intraday('CE');
        if (r?.status === 'ok') setCeSeries(r.series || []);
      }
      if (status?.strikes?.pe_symbol) {
        const r = await api.getStrategy7Intraday('PE');
        if (r?.status === 'ok') setPeSeries(r.series || []);
      }
    } catch (e) {
      console.error('s7 series', e);
    }
  }, [status?.strikes?.ce_symbol, status?.strikes?.pe_symbol]);

  const fetchHistory = useCallback(async () => {
    try {
      const r = await api.strategy7TradeHistory();
      setTrades(r?.trades || []);
    } catch (e) {
      console.error('s7 history', e);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    fetchStrikes();
    fetchHistory();
  }, [fetchStatus, fetchStrikes, fetchHistory]);

  // Polling loop
  useEffect(() => {
    timerRef.current = setInterval(() => {
      fetchStatus();
      // Refresh strikes every 30s, history every 30s
      if ((Date.now() / 1000) % 30 < 1) {
        fetchStrikes();
        fetchHistory();
      }
    }, REFRESH_MS);
    return () => clearInterval(timerRef.current);
  }, [fetchStatus, fetchStrikes, fetchHistory]);

  // Refresh series whenever strike selection changes
  useEffect(() => {
    fetchSeries();
    const id = setInterval(fetchSeries, 5_000);
    return () => clearInterval(id);
  }, [fetchSeries]);

  // Append live tick to chart series for ultra-low latency
  useEffect(() => {
    const ce = status?.ltp?.ce ?? 0;
    if (ce > 0) {
      const t = new Date().toLocaleTimeString('en-IN', { hour12: false });
      setCeSeries((s) => {
        if (s.length && s[s.length - 1].y === ce) return s;
        return [...s.slice(-499), { t, y: ce }];
      });
    }
  }, [status?.ltp?.ce]);
  useEffect(() => {
    const pe = status?.ltp?.pe ?? 0;
    if (pe > 0) {
      const t = new Date().toLocaleTimeString('en-IN', { hour12: false });
      setPeSeries((s) => {
        if (s.length && s[s.length - 1].y === pe) return s;
        return [...s.slice(-499), { t, y: pe }];
      });
    }
  }, [status?.ltp?.pe]);

  /* ── Mutations ── */
  const pickStrike = useCallback(async (side, s) => {
    try {
      const payload = side === 'CE'
        ? { ce: { strike: s.strike, tradingsymbol: s.ce_symbol, token: s.ce_token } }
        : { pe: { strike: s.strike, tradingsymbol: s.pe_symbol, token: s.pe_token } };
      await api.strategy7SetStrikes(payload);
      if (side === 'CE') setCeSeries([]); else setPeSeries([]);
      await fetchStatus();
    } catch (e) {
      alert(`Failed to set strike: ${e.message || e}`);
    }
  }, [fetchStatus]);

  const commitCallLine = useCallback(async (v) => {
    try {
      await api.strategy7UpdateLines({ call_line: v });
      await fetchStatus();
    } catch (e) {
      alert(`Failed to update CALL line: ${e.message || e}`);
    }
  }, [fetchStatus]);
  const commitPutLine = useCallback(async (v) => {
    try {
      await api.strategy7UpdateLines({ put_line: v });
      await fetchStatus();
    } catch (e) {
      alert(`Failed to update PUT line: ${e.message || e}`);
    }
  }, [fetchStatus]);
  const clearCallLine = useCallback(async () => commitCallLine(0), [commitCallLine]);
  const clearPutLine = useCallback(async () => commitPutLine(0), [commitPutLine]);

  const onStart = useCallback(async () => {
    if (!status?.strikes?.ce_symbol && !status?.strikes?.pe_symbol) {
      alert('Select at least one strike (CE or PE) before starting.');
      return;
    }
    if (!(status?.lines?.call_line > 0) && !(status?.lines?.put_line > 0)) {
      alert('Set at least one CALL or PUT line before starting.');
      return;
    }
    setStarting(true);
    try {
      await api.strategy7TradeStart({
        ...config,
        call_line: status?.lines?.call_line ?? 0,
        put_line: status?.lines?.put_line ?? 0,
        ce_strike: status?.strikes?.ce_strike ?? 0,
        ce_symbol: status?.strikes?.ce_symbol ?? '',
        ce_token: status?.strikes?.ce_token ?? 0,
        pe_strike: status?.strikes?.pe_strike ?? 0,
        pe_symbol: status?.strikes?.pe_symbol ?? '',
        pe_token: status?.strikes?.pe_token ?? 0,
      });
      await fetchStatus();
    } catch (e) {
      alert(`Start failed: ${e.message || e}`);
    } finally {
      setStarting(false);
    }
  }, [config, status, fetchStatus]);

  const onStop = useCallback(async () => {
    setStopping(true);
    try {
      await api.strategy7TradeStop();
      await fetchStatus();
    } catch (e) {
      alert(`Stop failed: ${e.message || e}`);
    } finally {
      setStopping(false);
    }
  }, [fetchStatus]);

  const saveConfig = useCallback(async () => {
    try {
      await api.strategy7TradeUpdateConfig(config);
      await fetchStatus();
      setConfigOpen(false);
    } catch (e) {
      alert(`Config save failed: ${e.message || e}`);
    }
  }, [config, fetchStatus]);

  /* ── Derived UI values ── */
  const stateKey = status?.state || 'IDLE';
  const stStyle = STATE_STYLE[stateKey] || STATE_STYLE.IDLE;
  const sig = status?.signal || 'NO_TRADE';
  const trade = status?.trade || {};
  const orders = status?.orders || {};
  const lines = status?.lines || {};
  const strikes = status?.strikes || {};
  const ltp = status?.ltp || {};
  const spot = status?.spot?.price ?? 0;
  const tradesToday = status?.trades_today ?? 0;

  const positionMarker = useMemo(() => {
    if (stateKey !== 'POSITION_OPEN' || !trade.fill_price) return null;
    return {
      ce: trade.option_symbol === strikes.ce_symbol
        ? { y: trade.fill_price, color: '#60a5fa', label: `Entry ${fmt(trade.fill_price)}` }
        : null,
      pe: trade.option_symbol === strikes.pe_symbol
        ? { y: trade.fill_price, color: '#60a5fa', label: `Entry ${fmt(trade.fill_price)}` }
        : null,
    };
  }, [stateKey, trade, strikes.ce_symbol, strikes.pe_symbol]);

  return (
    <div className="p-4 space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">
            <Zap className="w-5 h-5 text-amber-400" />
            Strategy 7 — CE/PE Strike Line Touch
          </h1>
          <p className="text-xs text-gray-400 mt-0.5">
            Pick a CE & PE strike (5 above / 5 below ATM). Draw horizontal lines on each.
            Touch on CE LTP → BUY CE. Touch on PE LTP → BUY PE.
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
              onClick={onStop}
              disabled={stopping}
              className="text-xs px-3 py-1.5 rounded bg-rose-600/20 border border-rose-500/40 text-rose-300 hover:bg-rose-600/30 disabled:opacity-50"
            >
              <Square className="w-3.5 h-3.5 inline mr-1" /> {stopping ? 'Stopping…' : 'Stop'}
            </button>
          ) : (
            <button
              onClick={onStart}
              disabled={starting}
              className="text-xs px-3 py-1.5 rounded bg-emerald-600/20 border border-emerald-500/40 text-emerald-300 hover:bg-emerald-600/30 disabled:opacity-50"
            >
              <Play className="w-3.5 h-3.5 inline mr-1" /> {starting ? 'Starting…' : 'Start'}
            </button>
          )}
        </div>
      </div>

      {docOpen && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 text-xs text-gray-300 leading-relaxed">
          <p><span className="text-amber-400 font-semibold">Strikes:</span> 5 strikes above and below the ATM are listed for both CE and PE. Click a strike on either side to monitor it — its live LTP feed and intraday chart start instantly.</p>
          <p className="mt-2"><span className="text-amber-400 font-semibold">Lines:</span> Use Pick to click on the chart, drag the pencil button, type a price, or press Enter — the horizontal line snaps to that price. Click the trash icon to delete a line.</p>
          <p className="mt-2"><span className="text-amber-400 font-semibold">Triggers:</span> When the selected CE strike's LTP touches the CALL line → BUY that CE strike. Same for PE / PUT line.</p>
          <p className="mt-2"><span className="text-amber-400 font-semibold">Exits:</span> Shadow SL / Target promote on proximity. Auto square-off at 15:15 IST. Up to <span className="text-white">{config.max_trades_per_day}</span> trades / day.</p>
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
                type="number"
                step="any"
                value={config[key]}
                onChange={(e) => setConfig((c) => ({ ...c, [key]: e.target.value === '' ? '' : Number(e.target.value) }))}
                className="bg-surface-3 border border-surface-3 rounded px-2 py-1 text-white"
              />
            </label>
          ))}
          <div className="col-span-full flex justify-end">
            <button
              onClick={saveConfig}
              className="text-xs px-3 py-1.5 rounded bg-emerald-600/20 border border-emerald-500/40 text-emerald-300 hover:bg-emerald-600/30"
            >
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
        <Card title="NIFTY Spot" icon={TrendingUp}>
          <div className="text-lg font-bold text-white">{fmt(spot, 2)}</div>
          <div className="text-[10px] text-gray-500">ATM {strikesData.atm || '—'}</div>
        </Card>
        <Card title="CE LTP" icon={TrendingUp}>
          <div className="text-lg font-bold text-green-400">{fmt(ltp.ce)}</div>
          <div className="text-[10px] text-gray-500">{strikes.ce_symbol || '— select CE strike —'}</div>
        </Card>
        <Card title="PE LTP" icon={Shield}>
          <div className="text-lg font-bold text-red-400">{fmt(ltp.pe)}</div>
          <div className="text-[10px] text-gray-500">{strikes.pe_symbol || '— select PE strike —'}</div>
        </Card>
        <Card title="Trades today" icon={Target}>
          <div className="text-lg font-bold text-white">{tradesToday} / {config.max_trades_per_day}</div>
          <div className="text-[10px] text-gray-500 truncate">{status?.scenario || '—'}</div>
        </Card>
      </div>

      {/* Side-by-side chart panels */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <SidePanel
          side="CE"
          strikes={strikesData.strikes}
          selectedStrike={strikes.ce_strike || 0}
          onPickStrike={(s) => pickStrike('CE', s)}
          ltp={ltp.ce || 0}
          line={lines.call_line || 0}
          draft={callDraft}
          setDraft={setCallDraft}
          onCommitLine={commitCallLine}
          onClearLine={clearCallLine}
          series={ceSeries}
          positionMarker={positionMarker?.ce || null}
        />
        <SidePanel
          side="PE"
          strikes={strikesData.strikes}
          selectedStrike={strikes.pe_strike || 0}
          onPickStrike={(s) => pickStrike('PE', s)}
          ltp={ltp.pe || 0}
          line={lines.put_line || 0}
          draft={putDraft}
          setDraft={setPutDraft}
          onCommitLine={commitPutLine}
          onClearLine={clearPutLine}
          series={peSeries}
          positionMarker={positionMarker?.pe || null}
        />
      </div>

      {/* Active trade panel */}
      <Card title="Active Trade" icon={Target}>
        {stateKey === 'POSITION_OPEN' || stateKey === 'ORDER_PLACED' ? (
          <div className="grid grid-cols-2 md:grid-cols-6 gap-3 text-xs">
            <div>
              <div className="text-gray-500">Side</div>
              <div className={`font-semibold ${trade.signal_type === 'CE' ? 'text-green-400' : 'text-red-400'}`}>
                {trade.signal_type || '—'}
              </div>
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
          <div className="text-xs text-gray-400">
            {status?.scenario || 'Idle — pick strikes and draw lines to arm.'}
          </div>
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
                  <th className="py-1">Date</th>
                  <th>Side</th>
                  <th>Option</th>
                  <th>Entry</th>
                  <th>Exit</th>
                  <th>Type</th>
                  <th>Time</th>
                  <th className="text-right">PnL</th>
                </tr>
              </thead>
              <tbody>
                {trades.slice().reverse().slice(0, 50).map((t, i) => (
                  <tr key={i} className="border-b border-surface-3/50">
                    <td className="py-1 text-gray-400">{t.date}</td>
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
    </div>
  );
}
