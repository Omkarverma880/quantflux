import React, { useState, useCallback, useEffect, useRef } from 'react';
import {
  LineChart, Line, BarChart, Bar, Cell, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts';
import {
  Play, RotateCcw, Download, Upload, Loader2, AlertCircle, CheckCircle2,
  FileText, BarChart3, Info, Database, Settings2,
} from 'lucide-react';
import { api } from '../../api';

const INR = (v, d = 0) => (v ?? 0).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
const GRID = 'rgba(120,130,150,0.15)'; const AXIS = '#94a3b8'; const POS = '#34d399'; const NEG = '#f87171';

const INDEXES = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'SENSEX'];
const INTERVALS = [['minute', '1 min'], ['3minute', '3 min'], ['5minute', '5 min']];
const MODES = [['breakout', 'Breakout'], ['vwap_retest', 'VWAP Retest'], ['confluence', 'Confluence']];
const STRIKES = [['ATM', 'ATM'], ['ITM', '1 ITM'], ['OTM', '1 OTM'], ['MANUAL', 'Manual']];
const INDEX_LOT = { NIFTY: 65, BANKNIFTY: 35, FINNIFTY: 65, SENSEX: 20 };  // qty per 1 lot
const DL_COLORS = { d1: '#f472b6', d2: '#a78bfa', d3: '#60a5fa', d4: '#34d399', d5: '#fbbf24' };

const inp = 'w-full bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';

function downloadText(name, text, type = 'text/csv;charset=utf-8') {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
}

function Card({ title, icon: Icon, children, right }) {
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
      {(title || right) && (
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2 text-gray-400 text-xs font-medium uppercase tracking-wider">{Icon && <Icon className="w-3.5 h-3.5" />} {title}</div>
          {right}
        </div>
      )}
      {children}
    </div>
  );
}
function Field({ label, children }) {
  return <div><label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">{label}</label>{children}</div>;
}
function Stat({ label, value, color = 'text-gray-100' }) {
  return <div className="bg-surface-3/40 rounded-lg px-3 py-2.5 text-center"><div className={`text-base font-bold ${color}`}>{value}</div><div className="text-gray-500 text-[11px] mt-0.5">{label}</div></div>;
}

/* ── Custom SVG candlestick with HL/VWAP overlays + signal markers ── */
function CandleChart({ chart }) {
  if (!chart || !chart.candles?.length) return <div className="text-gray-500 text-sm py-10 text-center">No candles for this day.</div>;
  const c = chart.candles;
  const W = Math.max(880, c.length * 4.2), H = 340, padL = 52, padR = 56, padT = 10, padB = 22;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const lv = Object.values(chart.dlevels || {}).filter((x) => x != null);
  const ys = c.flatMap((x) => [x.high, x.low]).concat(lv).concat(chart.prev_vwap != null ? [chart.prev_vwap] : []);
  const ymin = Math.min(...ys), ymax = Math.max(...ys), span = ymax - ymin || 1;
  const yPx = (v) => padT + plotH - ((v - ymin) / span) * plotH;
  const cw = plotW / c.length;
  const xPx = (i) => padL + i * cw + cw / 2;
  const idxOf = (t) => c.findIndex((x) => x.t === t);
  const yLabels = Array.from({ length: 5 }, (_, k) => ymin + (span * k) / 4);
  const xTicks = []; const step = Math.max(1, Math.floor(c.length / 8));
  for (let i = 0; i < c.length; i += step) xTicks.push(i);

  return (
    <div className="overflow-x-auto">
      <svg width={W} height={H} className="block">
        {yLabels.map((v, k) => (
          <g key={k}>
            <line x1={padL} x2={W - padR} y1={yPx(v)} y2={yPx(v)} stroke={GRID} />
            <text x={4} y={yPx(v) + 3} fill={AXIS} fontSize="10">{INR(v, 0)}</text>
          </g>
        ))}
        {/* confluence zones */}
        {(chart.confluence || []).map((z, k) => (
          <rect key={`z${k}`} x={padL} width={plotW} y={yPx(z.high)} height={Math.max(2, yPx(z.low) - yPx(z.high))} fill="#fbbf24" opacity="0.07" />
        ))}
        {/* D levels */}
        {['d1', 'd2', 'd3', 'd4', 'd5'].map((d) => ['high', 'low'].map((hl) => {
          const v = chart.dlevels?.[`${d}_${hl}`]; if (v == null) return null;
          return <g key={`${d}${hl}`}>
            <line x1={padL} x2={W - padR} y1={yPx(v)} y2={yPx(v)} stroke={DL_COLORS[d]} strokeWidth="1" strokeDasharray="4 4" opacity="0.6" />
            <text x={W - padR + 2} y={yPx(v) + 3} fill={DL_COLORS[d]} fontSize="9">{d.toUpperCase()}{hl === 'high' ? 'H' : 'L'}</text>
          </g>;
        }))}
        {/* prev-day VWAP */}
        {chart.prev_vwap != null && (
          <g><line x1={padL} x2={W - padR} y1={yPx(chart.prev_vwap)} y2={yPx(chart.prev_vwap)} stroke="#f59e0b" strokeWidth="1.5" strokeDasharray="6 3" />
            <text x={W - padR + 2} y={yPx(chart.prev_vwap) + 3} fill="#f59e0b" fontSize="9">pVWAP</text></g>
        )}
        {/* candles */}
        {c.map((x, i) => {
          const up = x.close >= x.open; const col = up ? POS : NEG;
          const bx = padL + i * cw + cw * 0.15, bw = Math.max(1, cw * 0.7);
          const yo = yPx(x.open), yc = yPx(x.close);
          return <g key={i}>
            <line x1={xPx(i)} x2={xPx(i)} y1={yPx(x.high)} y2={yPx(x.low)} stroke={col} strokeWidth="1" />
            <rect x={bx} width={bw} y={Math.min(yo, yc)} height={Math.max(1, Math.abs(yc - yo))} fill={col} />
          </g>;
        })}
        {/* today VWAP polyline */}
        <polyline fill="none" stroke="#818cf8" strokeWidth="1.5" points={c.map((x, i) => `${xPx(i)},${yPx(x.vwap)}`).join(' ')} />
        {/* markers */}
        {(chart.markers || []).map((m, k) => {
          const i = idxOf(m.t); if (i < 0) return null;
          const x = xPx(i), y = yPx(m.price);
          if (m.kind === 'CE') return <polygon key={k} points={`${x},${y - 7} ${x - 5},${y + 3} ${x + 5},${y + 3}`} fill={POS} stroke="#0f172a" />;
          if (m.kind === 'PE') return <polygon key={k} points={`${x},${y + 7} ${x - 5},${y - 3} ${x + 5},${y - 3}`} fill={NEG} stroke="#0f172a" />;
          return <g key={k}><line x1={x - 4} x2={x + 4} y1={y - 4} y2={y + 4} stroke="#cbd5e1" strokeWidth="1.5" /><line x1={x - 4} x2={x + 4} y1={y + 4} y2={y - 4} stroke="#cbd5e1" strokeWidth="1.5" /></g>;
        })}
        {xTicks.map((i) => <text key={i} x={xPx(i)} y={H - 6} fill={AXIS} fontSize="9" textAnchor="middle">{c[i].t}</text>)}
      </svg>
    </div>
  );
}

const SPOT_TMPL = 'datetime,open,high,low,close,volume\n2026-06-01 09:15,24890,24910,24870,24905,124500\n2026-06-01 09:16,24905,24920,24895,24910,85200\n';
const OPT_TMPL = 'datetime,expiry,strike,type,open,high,low,close,volume,oi\n2026-06-01 09:15,2026-06-04,25000,CE,122,126,120,124,1500,52000\n2026-06-01 09:16,2026-06-04,25000,CE,124,130,123,129,1800,52500\n';

export default function HlVwapLab() {
  const [source, setSource] = useState('zerodha');         // zerodha | csv
  const [p, setP] = useState({
    instrument: 'NIFTY', start: '', end: '', interval: 'minute', data_type: 'spot',
    expiry: '', option_type: 'CE', strike_mode: 'ATM', strike: '',
    session_start_hour: 9, session_start_min: 15, first_hour_minutes: 60,
    strategy_mode: 'breakout', stop_loss: 30, target: 60, capital_per_trade: 0, lots: 1,
  });
  const [meta, setMeta] = useState(null);
  const [spotRows, setSpotRows] = useState(null);
  const [optRows, setOptRows] = useState(null);
  const [csvInfo, setCsvInfo] = useState('');
  const [result, setResult] = useState(null);
  const [chart, setChart] = useState(null);
  const [chartDay, setChartDay] = useState('');
  const [tab, setTab] = useState('spot');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [ok, setOk] = useState('');
  const spotRef = useRef(null), optRef = useRef(null);

  const msg = (m, isErr) => { if (isErr) { setError(m); setTimeout(() => setError(''), 5000); } else { setOk(m); setTimeout(() => setOk(''), 3000); } };
  const set = (k, v) => setP((s) => ({ ...s, [k]: v }));
  const perLot = meta?.lot ?? INDEX_LOT[p.instrument] ?? 65;
  const lotsNum = Math.max(1, parseInt(p.lots) || 1);

  const loadMeta = useCallback(async () => {
    try { const r = await api.researchHlVwapMeta(p.instrument); if (r.status === 'ok') setMeta(r); } catch {}
  }, [p.instrument]);
  useEffect(() => { if (source === 'zerodha') loadMeta(); }, [source, loadMeta]);

  const upload = async (file, kind) => {
    const fd = new FormData(); fd.append('file', file); fd.append('kind', kind);
    try {
      const r = await api.researchHlVwapUpload(fd);
      if (r.status === 'ok') {
        if (kind === 'spot') setSpotRows(r.rows); else setOptRows(r.rows);
        setCsvInfo(`${kind} CSV: ${r.count} valid rows loaded.`); msg(`${kind} CSV validated (${r.count} rows).`);
      } else { msg(`${r.message}${r.errors ? ' — ' + r.errors.slice(0, 3).join('; ') : ''}`, true); }
    } catch (e) { msg(e.message || 'Upload failed', true); }
  };

  const run = useCallback(async () => {
    setLoading(true); setError(''); setResult(null); setChart(null);
    try {
      const params = { ...p, mode: source, lots: lotsNum, lot_size: lotsNum * perLot };
      if (source === 'csv') {
        if (!spotRows?.length) { msg('Upload a Spot CSV first', true); setLoading(false); return; }
        params.spot_rows = spotRows; params.option_rows = optRows || [];
        params.data_type = optRows?.length ? 'options' : 'spot';
      }
      const r = await api.researchHlVwapRun(params);
      if (r.status === 'ok') {
        setResult(r); setChart(r.chart); setChartDay(r.chart?.date || '');
        setTab(r.option ? 'spot' : 'spot');
      } else msg(r.message || 'Run failed', true);
    } catch (e) { msg(e.message || 'Run failed', true); }
    finally { setLoading(false); }
  }, [p, source, spotRows, optRows]);

  const switchDay = async (day) => {
    setChartDay(day);
    try { const r = await api.researchHlVwapChart(day); if (r.status === 'ok') setChart(r.chart); } catch {}
  };

  const reset = () => { setResult(null); setChart(null); setSpotRows(null); setOptRows(null); setCsvInfo(''); };

  const exportTrades = () => {
    const t = tab === 'option' ? result?.option?.trades : result?.spot?.trades;
    if (!t?.length) { msg('No trades to export', true); return; }
    const cols = tab === 'option'
      ? ['date', 'entry_time', 'exit_time', 'signal_type', 'strike', 'option_type', 'premium_buy', 'premium_sell', 'lot', 'option_pnl', 'exit_reason']
      : ['date', 'entry_time', 'exit_time', 'signal_type', 'reason', 'entry_price', 'exit_price', 'points', 'qty', 'pnl', 'exit_reason'];
    const esc = (v) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    downloadText(`hlvwap_${tab}_trades.csv`, [cols.join(','), ...t.map((r) => cols.map((cc) => esc(r[cc])).join(','))].join('\n'));
  };

  const an = tab === 'option' ? result?.option : result?.spot;
  const trades = an?.trades || [];

  const dist = React.useMemo(() => {
    const r = {};
    trades.forEach((t) => { const k = t.exit_reason || '?'; r[k] = (r[k] || 0) + 1; });
    return Object.entries(r).map(([reason, count]) => ({ reason, count }));
  }, [trades]);
  const DIST_COLOR = { TARGET: POS, SL: NEG, EOD: '#60a5fa' };

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-[1500px] mx-auto">
      <div>
        <div className="flex items-center gap-2">
          <h1 className="text-xl font-bold text-gray-100">HL + VWAP Research Lab</h1>
          <span className="px-2 py-0.5 rounded-full bg-brand-600/15 text-brand-400 text-xs font-semibold border border-brand-500/20">Research</span>
        </div>
        <p className="text-gray-500 text-sm mt-0.5">First-hour range · rolling 5-day levels · today &amp; prev-day VWAP · breakout / retest / confluence · option PnL. Read-only.</p>
      </div>

      {error && <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-red-400 text-sm"><AlertCircle className="w-4 h-4" /> {error}</div>}
      {ok && <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-4 py-2 text-emerald-400 text-sm"><CheckCircle2 className="w-4 h-4" /> {ok}</div>}

      {/* Section 1 — Data source */}
      <Card title="Data Source" icon={Database}>
        <div className="flex gap-2">
          {[['zerodha', 'Zerodha Historical'], ['csv', 'CSV Upload']].map(([v, l]) => (
            <button key={v} onClick={() => setSource(v)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium border transition ${source === v ? 'bg-brand-600/20 text-brand-400 border-brand-500/40' : 'bg-surface-3 text-gray-400 border-surface-4'}`}>{l}</button>
          ))}
        </div>
      </Card>

      {/* Section 2 — Data controls */}
      <Card title="Data Controls" icon={Settings2}>
        {source === 'zerodha' ? (
          <div className="space-y-3">
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
              <Field label="Instrument"><select value={p.instrument} onChange={(e) => set('instrument', e.target.value)} className={inp}>{INDEXES.map((i) => <option key={i}>{i}</option>)}</select></Field>
              <Field label="Start Date"><input type="date" value={p.start} onChange={(e) => set('start', e.target.value)} className={inp} /></Field>
              <Field label="End Date"><input type="date" value={p.end} onChange={(e) => set('end', e.target.value)} className={inp} /></Field>
              <Field label="Interval"><select value={p.interval} onChange={(e) => set('interval', e.target.value)} className={inp}>{INTERVALS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
              <Field label="Data Type"><select value={p.data_type} onChange={(e) => set('data_type', e.target.value)} className={inp}><option value="spot">Spot</option><option value="options">Options</option></select></Field>
              {meta && <Field label="Spot / ATM"><div className="text-sm text-gray-300 pt-1.5">₹{INR(meta.spot, 2)} · {meta.atm}</div></Field>}
            </div>
            {p.data_type === 'options' && (
              <>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  <Field label="Expiry"><select value={p.expiry} onChange={(e) => set('expiry', e.target.value)} className={inp}><option value="">auto (nearest)</option>{(meta?.expiries || []).map((e) => <option key={e}>{e}</option>)}</select></Field>
                  <Field label="Strike Mode"><select value={p.strike_mode} onChange={(e) => set('strike_mode', e.target.value)} className={inp}>{STRIKES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
                  {p.strike_mode === 'MANUAL' && <Field label="Strike"><input type="number" step={meta?.step || 50} value={p.strike} onChange={(e) => set('strike', e.target.value)} className={inp} /></Field>}
                  <Field label={`Lots (×${perLot}) = ${lotsNum*perLot}`}><input type="number" min="1" value={p.lots} onChange={(e) => set('lots', e.target.value)} className={inp} /></Field>
                </div>
                <div className="flex items-start gap-2 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2 text-amber-300 text-xs">
                  <Info className="w-4 h-4 shrink-0 mt-0.5" /> Expired option contracts are not reliably available via broker historical APIs. Use CSV upload mode for expired option research.
                </div>
              </>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              <button onClick={() => downloadText('spot_template.csv', SPOT_TMPL)} className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-brand-600/15 text-brand-400 border border-brand-500/30"><Download className="w-3.5 h-3.5" /> Download Spot CSV Template</button>
              <button onClick={() => downloadText('option_template.csv', OPT_TMPL)} className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-brand-600/15 text-brand-400 border border-brand-500/30"><Download className="w-3.5 h-3.5" /> Download Option CSV Template</button>
            </div>
            <div className="bg-surface-3/30 rounded-lg p-3 text-[11px] text-gray-400 space-y-1">
              <div><strong className="text-gray-200">Spot CSV columns:</strong> <code className="text-brand-300">datetime, open, high, low, close, volume</code> — e.g. <code>2026-06-01 09:15,24890,24910,24870,24905,124500</code></div>
              <div><strong className="text-gray-200">Option CSV columns:</strong> <code className="text-brand-300">datetime, expiry, strike, type, open, high, low, close, volume, oi</code> — <code>type</code> = CE/PE, <code>expiry</code> = YYYY-MM-DD, <code>datetime</code> = YYYY-MM-DD HH:MM</div>
              <div className="text-gray-500">Download a template above, fill it in the same format, and upload it back. The <strong>Spot CSV is required</strong> (signals are computed on it); the <strong>Option CSV is optional</strong> — add it to get real expired-option premium PnL (the engine auto-matches the nearest available strike per signal).</div>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <input ref={spotRef} type="file" accept=".csv" className="hidden" onChange={(e) => e.target.files[0] && upload(e.target.files[0], 'spot')} />
                <button onClick={() => spotRef.current?.click()} className="w-full flex items-center justify-center gap-1.5 px-3 py-2 text-sm rounded-lg bg-surface-3 hover:bg-surface-4 text-gray-200 border border-surface-4"><Upload className="w-4 h-4" /> Upload Spot CSV {spotRows ? `(${spotRows.length})` : ''}</button>
              </div>
              <div>
                <input ref={optRef} type="file" accept=".csv" className="hidden" onChange={(e) => e.target.files[0] && upload(e.target.files[0], 'option')} />
                <button onClick={() => optRef.current?.click()} className="w-full flex items-center justify-center gap-1.5 px-3 py-2 text-sm rounded-lg bg-surface-3 hover:bg-surface-4 text-gray-200 border border-surface-4"><Upload className="w-4 h-4" /> Upload Option CSV (optional) {optRows ? `(${optRows.length})` : ''}</button>
              </div>
            </div>
            {csvInfo && <div className="text-xs text-gray-400 flex items-center gap-1.5"><FileText className="w-3.5 h-3.5" /> {csvInfo}</div>}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <Field label="Instrument"><select value={p.instrument} onChange={(e) => set('instrument', e.target.value)} className={inp}>{INDEXES.map((i) => <option key={i}>{i}</option>)}</select></Field>
              <Field label="Strike Mode"><select value={p.strike_mode} onChange={(e) => set('strike_mode', e.target.value)} className={inp}>{STRIKES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
              {p.strike_mode === 'MANUAL' && <Field label="Strike"><input type="number" value={p.strike} onChange={(e) => set('strike', e.target.value)} className={inp} /></Field>}
              <Field label={`Lots (×${perLot}) = ${lotsNum*perLot}`}><input type="number" min="1" value={p.lots} onChange={(e) => set('lots', e.target.value)} className={inp} /></Field>
            </div>
          </div>
        )}
      </Card>

      {/* Section 3 — Strategy params */}
      <Card title="Strategy Parameters" icon={Settings2}>
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3">
          <Field label="Sess Hr"><input type="number" value={p.session_start_hour} onChange={(e) => set('session_start_hour', e.target.value)} className={inp} /></Field>
          <Field label="Sess Min"><input type="number" value={p.session_start_min} onChange={(e) => set('session_start_min', e.target.value)} className={inp} /></Field>
          <Field label="1st Hr (min)"><input type="number" value={p.first_hour_minutes} onChange={(e) => set('first_hour_minutes', e.target.value)} className={inp} /></Field>
          <Field label="Mode"><select value={p.strategy_mode} onChange={(e) => set('strategy_mode', e.target.value)} className={inp}>{MODES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
          <Field label="Stop Loss"><input type="number" value={p.stop_loss} onChange={(e) => set('stop_loss', e.target.value)} className={inp} /></Field>
          <Field label="Target"><input type="number" value={p.target} onChange={(e) => set('target', e.target.value)} className={inp} /></Field>
          <Field label="Capital/Trade"><input type="number" value={p.capital_per_trade} onChange={(e) => set('capital_per_trade', e.target.value)} className={inp} /></Field>
          <Field label={`Lots (×${perLot}) = ${lotsNum*perLot}`}><input type="number" min="1" value={p.lots} onChange={(e) => set('lots', e.target.value)} className={inp} /></Field>
        </div>
      </Card>

      {/* Section 4 — Actions */}
      <div className="flex flex-wrap gap-2">
        <button onClick={run} disabled={loading} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50 transition">{loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />} Run Research</button>
        <button onClick={reset} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-surface-3 hover:bg-surface-4 text-gray-200 border border-surface-4"><RotateCcw className="w-4 h-4" /> Reset</button>
        <button onClick={exportTrades} disabled={!trades.length} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-surface-3 hover:bg-surface-4 text-gray-200 border border-surface-4 disabled:opacity-40"><Download className="w-4 h-4" /> Export Trades</button>
      </div>

      {loading && <Card><div className="flex items-center justify-center gap-2 py-10 text-gray-400 text-sm"><Loader2 className="w-5 h-5 animate-spin" /> Fetching data &amp; backtesting…</div></Card>}

      {result && (
        <>
          {/* tabs spot/option */}
          {result.option && (
            <div className="flex gap-2">
              {[['spot', 'Spot Backtest'], ['option', 'Option Backtest']].map(([v, l]) => (
                <button key={v} onClick={() => setTab(v)} className={`px-3 py-1.5 rounded-lg text-sm font-medium border ${tab === v ? 'bg-brand-600/20 text-brand-400 border-brand-500/40' : 'bg-surface-3 text-gray-400 border-surface-4'}`}>{l}</button>
              ))}
            </div>
          )}

          {/* Analytics cards */}
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3">
            <Stat label="Total Trades" value={an?.total_trades ?? 0} />
            <Stat label="Wins" value={an?.wins ?? 0} color="text-emerald-400" />
            <Stat label="Losses" value={an?.losses ?? 0} color="text-red-400" />
            <Stat label="Win Rate" value={`${an?.win_rate ?? 0}%`} color={(an?.win_rate ?? 0) >= 50 ? 'text-emerald-400' : 'text-gray-200'} />
            <Stat label="Net P&L" value={`₹${INR(an?.net_pnl)}`} color={(an?.net_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'} />
            <Stat label="Avg Profit" value={`₹${INR(an?.avg_profit)}`} color="text-emerald-400" />
            <Stat label="Max DD" value={`₹${INR(an?.max_drawdown)}`} color="text-red-400" />
            <Stat label="Profit Factor" value={an?.profit_factor ?? '∞'} />
          </div>

          {/* Candlestick chart */}
          <Card title={`Candles + Levels — ${chart?.date || ''}`} icon={BarChart3}
            right={result.chart_dates?.length > 1 && (
              <select value={chartDay} onChange={(e) => switchDay(e.target.value)} className="bg-surface-3 border border-surface-4 rounded-lg px-2 py-1 text-xs text-gray-200">
                {result.chart_dates.map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
            )}>
            <CandleChart chart={chart} />
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-2 text-[11px] text-gray-500">
              <span><span style={{ color: '#818cf8' }}>—</span> today VWAP</span>
              <span><span style={{ color: '#f59e0b' }}>--</span> prev VWAP</span>
              {Object.entries(DL_COLORS).map(([d, c]) => <span key={d} style={{ color: c }}>{d.toUpperCase()} H/L</span>)}
              <span><span className="text-emerald-400">▲</span> CE · <span className="text-red-400">▼</span> PE · ✕ exit · <span className="text-amber-400">▮</span> confluence</span>
            </div>
          </Card>

          {/* Equity / Drawdown / Daily */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <Card title="Equity Curve" icon={BarChart3}>
              <ResponsiveContainer width="100%" height={200}><LineChart data={(an?.equity_curve || []).map((v, i) => ({ i: i + 1, v }))}>
                <CartesianGrid stroke={GRID} strokeDasharray="3 3" /><XAxis dataKey="i" tick={{ fill: AXIS, fontSize: 10 }} /><YAxis tick={{ fill: AXIS, fontSize: 10 }} width={52} />
                <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }} /><ReferenceLine y={0} stroke={AXIS} strokeOpacity={0.4} />
                <Line type="monotone" dataKey="v" stroke="#818cf8" strokeWidth={2} dot={false} /></LineChart></ResponsiveContainer>
            </Card>
            <Card title="Drawdown" icon={BarChart3}>
              <ResponsiveContainer width="100%" height={200}><LineChart data={(an?.drawdown_curve || []).map((v, i) => ({ i: i + 1, v }))}>
                <CartesianGrid stroke={GRID} strokeDasharray="3 3" /><XAxis dataKey="i" tick={{ fill: AXIS, fontSize: 10 }} /><YAxis tick={{ fill: AXIS, fontSize: 10 }} width={52} />
                <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }} />
                <Line type="monotone" dataKey="v" stroke={NEG} strokeWidth={2} dot={false} /></LineChart></ResponsiveContainer>
            </Card>
            <Card title="Daily P&L" icon={BarChart3}>
              <ResponsiveContainer width="100%" height={200}><BarChart data={an?.daily_pnl || []}>
                <CartesianGrid stroke={GRID} strokeDasharray="3 3" /><XAxis dataKey="date" tick={{ fill: AXIS, fontSize: 9 }} /><YAxis tick={{ fill: AXIS, fontSize: 10 }} width={52} />
                <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }} /><ReferenceLine y={0} stroke={AXIS} strokeOpacity={0.4} />
                <Bar dataKey="pnl">{(an?.daily_pnl || []).map((d, i) => <Cell key={i} fill={d.pnl >= 0 ? POS : NEG} />)}</Bar></BarChart></ResponsiveContainer>
            </Card>
          </div>

          {/* Win/Loss distribution + Confluence zones */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card title="Win / Loss Distribution (by exit)" icon={BarChart3}>
              {dist.length === 0 ? <p className="text-gray-500 text-sm py-6 text-center">No trades.</p> : (
                <ResponsiveContainer width="100%" height={200}><BarChart data={dist}>
                  <CartesianGrid stroke={GRID} strokeDasharray="3 3" /><XAxis dataKey="reason" tick={{ fill: AXIS, fontSize: 11 }} /><YAxis allowDecimals={false} tick={{ fill: AXIS, fontSize: 10 }} width={36} />
                  <Tooltip contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }} />
                  <Bar dataKey="count">{dist.map((d, i) => <Cell key={i} fill={DIST_COLOR[d.reason] || '#94a3b8'} />)}</Bar></BarChart></ResponsiveContainer>
              )}
            </Card>
            <Card title={`Confluence Zones — ${chart?.date || ''}`} icon={Info}>
              {!chart?.confluence?.length ? (
                <p className="text-gray-500 text-sm py-6 text-center">No confluence zones on this day (levels not clustered).</p>
              ) : (
                <div className="overflow-x-auto max-h-52 overflow-y-auto">
                  <table className="w-full text-xs">
                    <thead><tr className="border-b border-surface-3 text-gray-500"><th className="text-left pb-2 pr-3">Zone (₹)</th><th className="text-left pb-2 pr-3">Width</th><th className="text-left pb-2">Overlapping levels</th></tr></thead>
                    <tbody>
                      {chart.confluence.map((z, i) => (
                        <tr key={i} className="border-b border-surface-3/30">
                          <td className="py-1.5 pr-3 text-amber-400 font-medium">{INR(z.low, 2)} – {INR(z.high, 2)}</td>
                          <td className="py-1.5 pr-3 text-gray-400">{INR(z.high - z.low, 2)}</td>
                          <td className="py-1.5 text-gray-300">{z.members.map((m) => m.replace('prev_vwap', 'pVWAP').replace('_high', 'H').replace('_low', 'L').replace('d', 'D').toUpperCase()).join(' · ')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>

          {/* Trade log */}
          <Card title={`Trade Log — ${tab} (${trades.length})`} icon={FileText}>
            {trades.length === 0 ? <p className="text-gray-500 text-sm py-4 text-center">No trades generated for these parameters.</p> : (
              <div className="overflow-x-auto max-h-96 overflow-y-auto">
                <table className="w-full text-xs">
                  <thead className="sticky top-0 bg-surface-2"><tr className="border-b border-surface-3 text-gray-500">
                    {(tab === 'option'
                      ? ['Date', 'Entry', 'Exit', 'Dir', 'Strike', 'Buy', 'Sell', 'Lot', 'P&L', 'Reason']
                      : ['Date', 'Entry', 'Exit', 'Dir', 'Setup', 'Entry ₹', 'Exit ₹', 'Pts', 'Qty', 'P&L', 'Reason']
                    ).map((h) => <th key={h} className="text-left font-medium pb-2 pr-3 whitespace-nowrap">{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {trades.map((t, i) => (
                      <tr key={i} className="border-b border-surface-3/30 hover:bg-surface-3/20">
                        <td className="py-1.5 pr-3 text-gray-400">{t.date}</td>
                        <td className="py-1.5 pr-3 text-gray-300">{t.entry_time}</td>
                        <td className="py-1.5 pr-3 text-gray-300">{t.exit_time}</td>
                        <td className="py-1.5 pr-3"><span className={t.signal_type === 'CE' ? 'text-emerald-400' : 'text-red-400'}>{t.signal_type}</span></td>
                        {tab === 'option' ? <>
                          <td className="py-1.5 pr-3 text-gray-300">{t.strike}</td>
                          <td className="py-1.5 pr-3 text-gray-300">₹{INR(t.premium_buy, 2)}</td>
                          <td className="py-1.5 pr-3 text-gray-300">₹{INR(t.premium_sell, 2)}</td>
                          <td className="py-1.5 pr-3 text-gray-500">{t.lot}</td>
                          <td className={`py-1.5 pr-3 font-medium ${(t.option_pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{(t.option_pnl ?? 0) >= 0 ? '+' : ''}₹{INR(t.option_pnl)}</td>
                        </> : <>
                          <td className="py-1.5 pr-3 text-gray-500">{t.reason}</td>
                          <td className="py-1.5 pr-3 text-gray-300">{INR(t.entry_price, 2)}</td>
                          <td className="py-1.5 pr-3 text-gray-300">{INR(t.exit_price, 2)}</td>
                          <td className={`py-1.5 pr-3 ${t.points >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{t.points}</td>
                          <td className="py-1.5 pr-3 text-gray-500">{t.qty}</td>
                          <td className={`py-1.5 pr-3 font-medium ${(t.pnl ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{(t.pnl ?? 0) >= 0 ? '+' : ''}₹{INR(t.pnl)}</td>
                        </>}
                        <td className="py-1.5 pr-3 text-gray-400">{t.exit_reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}

      {!result && !loading && (
        <Card><div className="text-center py-12 text-gray-500 text-sm">Configure the data source &amp; parameters, then click <strong>Run Research</strong>.</div></Card>
      )}
    </div>
  );
}
