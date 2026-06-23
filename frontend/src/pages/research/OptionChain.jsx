import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Download, RefreshCw, Loader2, AlertCircle, Layers, Activity, Pause, Play,
} from 'lucide-react';
import { api } from '../../api';

const INR = (v, d = 2) =>
  (v ?? 0).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });

const fmtOI = (v) => (v ? (v / 1e5).toFixed(2) : '—');           // lakh
const fmtOIChg = (v) => (v == null || v === 0 ? null : `${v >= 0 ? '+' : ''}${(v / 1e5).toFixed(2)}L`);

const BUILDUP = {
  'Long Buildup':   { c: 'text-emerald-400', t: '▲', title: 'Long Buildup — price ↑, OI ↑' },
  'Short Buildup':  { c: 'text-red-400',     t: '▼', title: 'Short Buildup — price ↓, OI ↑' },
  'Short Covering': { c: 'text-blue-400',    t: '◹', title: 'Short Covering — price ↑, OI ↓' },
  'Long Unwinding': { c: 'text-orange-400',  t: '◺', title: 'Long Unwinding — price ↓, OI ↓' },
};
function Buildup({ v }) {
  const s = BUILDUP[v];
  if (!s) return <span className="text-gray-700">—</span>;
  return <span className={`${s.c} font-bold`} title={s.title}>{s.t}</span>;
}
function OICell({ cell, align }) {
  const chg = fmtOIChg(cell?.oi_change);
  return (
    <div className={align}>
      <div className="text-gray-400">{fmtOI(cell?.oi)}</div>
      {chg && <div className={`text-[9px] ${cell.oi_change >= 0 ? 'text-emerald-500' : 'text-red-500'}`}>{chg}</div>}
    </div>
  );
}
const fmtVol = (v) => {
  if (!v) return '—';
  if (v >= 1e7) return `${(v / 1e7).toFixed(2)}Cr`;
  if (v >= 1e5) return `${(v / 1e5).toFixed(2)}L`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return `${v}`;
};

function downloadCSV(filename, headers, rows) {
  const esc = (v) => {
    const s = v == null ? '' : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [headers.map((h) => esc(h.label)).join(',')];
  rows.forEach((r) => lines.push(headers.map((h) => esc(r[h.key])).join(',')));
  const blob = new Blob([lines.join('\n') + '\n'], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(url);
}

const CANDLE_HEADERS = [
  { key: 'datetime', label: 'Datetime' }, { key: 'open', label: 'Open' },
  { key: 'high', label: 'High' }, { key: 'low', label: 'Low' },
  { key: 'close', label: 'Close' }, { key: 'volume', label: 'Volume' },
  { key: 'oi', label: 'OI' }, { key: 'vwap', label: 'VWAP' },
];

const selCls = 'bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';

export default function OptionChainResearch() {
  const [expiryType, setExpiryType] = useState('weekly');
  const [expiry, setExpiry] = useState('');
  const [expiries, setExpiries] = useState([]);
  const [count, setCount] = useState(12);
  const [interval, setIntervalStep] = useState(50);
  const [dlDate, setDlDate] = useState('');
  const [auto, setAuto] = useState(false);

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [dlBusy, setDlBusy] = useState('');
  const timer = useRef(null);

  const showErr = (m) => { setError(m); setTimeout(() => setError(''), 4500); };

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const res = await api.researchOptionChainSnapshot({
        expiry_type: expiryType, count, interval, expiry: expiry || null,
      });
      if (res.status === 'ok') setData(res);
      else if (!silent) showErr(res.message || 'Failed to load chain');
    } catch (e) { if (!silent) showErr(e.message || 'Failed to load chain'); }
    finally { if (!silent) setLoading(false); }
  }, [expiryType, count, interval, expiry]);

  useEffect(() => {
    api.researchOptionChainExpiries().then((r) => { if (r.status === 'ok') setExpiries(r.expiries || []); }).catch(() => {});
  }, []);

  // auto-refresh
  useEffect(() => {
    if (timer.current) clearInterval(timer.current);
    if (auto) timer.current = setInterval(() => load(true), 5000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [auto, load]);

  const handleDownload = async (cell) => {
    if (!cell) return;
    setDlBusy(cell.symbol);
    try {
      const res = await api.researchOptionChainDownload(cell.token, cell.symbol, dlDate || null);
      if (res.status === 'ok') {
        if (!res.rows?.length) { showErr(`No 1-min data for ${cell.symbol} on that date`); return; }
        downloadCSV(`${cell.symbol}_${res.date}_1min.csv`, CANDLE_HEADERS, res.rows);
      } else showErr(res.message || 'Download failed');
    } catch (e) { showErr(e.message || 'Download failed'); }
    finally { setDlBusy(''); }
  };

  const rows = data?.rows || [];

  const ChangePct = ({ v }) => (
    <span className={v >= 0 ? 'text-emerald-500' : 'text-red-500'}>{v >= 0 ? '+' : ''}{v}%</span>
  );

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-[1500px] mx-auto">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold text-gray-100">Option Chain Data</h1>
            <span className="px-2 py-0.5 rounded-full bg-brand-600/15 text-brand-400 text-xs font-semibold border border-brand-500/20">
              Research · NIFTY
            </span>
          </div>
          <p className="text-gray-500 text-sm mt-0.5">
            Live CE/PE chain with LTP, OHLC, Volume, OI, VWAP, IV &amp; Greeks. Download 1-minute candles per strike.
          </p>
        </div>
        <button onClick={() => setAuto((a) => !a)}
          className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border transition ${
            auto ? 'bg-emerald-600/20 text-emerald-400 border-emerald-500/40' : 'bg-surface-3 text-gray-400 border-surface-4'
          }`}>
          {auto ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />} Auto-refresh {auto ? 'ON' : 'OFF'}
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-red-400 text-sm">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      )}

      {/* Controls */}
      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 items-end">
          <div>
            <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">Expiry Type</label>
            <select value={expiryType} onChange={(e) => { setExpiryType(e.target.value); setExpiry(''); }} className={`w-full ${selCls}`}>
              <option value="weekly">Weekly (nearest)</option>
              <option value="monthly">Monthly</option>
            </select>
          </div>
          <div>
            <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">Or pick expiry</label>
            <select value={expiry} onChange={(e) => setExpiry(e.target.value)} className={`w-full ${selCls}`}>
              <option value="">— auto —</option>
              {expiries.map((e) => <option key={e} value={e}>{e}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">Strikes / side</label>
            <input type="number" min="1" max="40" value={count}
              onChange={(e) => setCount(Math.max(1, Math.min(40, parseInt(e.target.value) || 1)))} className={`w-full ${selCls}`} />
          </div>
          <div>
            <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">Strike interval</label>
            <input type="number" min="50" step="50" value={interval}
              onChange={(e) => setIntervalStep(Math.max(50, parseInt(e.target.value) || 50))} className={`w-full ${selCls}`} />
          </div>
          <div>
            <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">Download date</label>
            <input type="date" value={dlDate} onChange={(e) => setDlDate(e.target.value)} className={`w-full ${selCls}`} />
          </div>
          <button onClick={() => load(false)} disabled={loading}
            className="flex items-center justify-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50 transition">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />} Load Chain
          </button>
        </div>
      </div>

      {/* Summary bar */}
      {data && (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm bg-surface-2 border border-surface-3 rounded-xl px-4 py-2.5">
          <span className="text-gray-400">Spot <strong className="text-gray-100">₹{INR(data.spot, 2)}</strong></span>
          <span className="text-gray-400">ATM <strong className="text-brand-400">{data.atm}</strong></span>
          <span className="text-gray-400">Expiry <strong className="text-gray-100">{data.expiry}</strong> <span className="text-gray-600">({data.expiry_type}, {data.days_to_expiry}d)</span></span>
          <span className="text-gray-400">PCR <strong className={data.pcr >= 1 ? 'text-emerald-400' : 'text-red-400'}>{data.pcr ?? '—'}</strong></span>
          <span className="text-gray-400">Max Pain <strong className="text-amber-400">{data.max_pain ?? '—'}</strong></span>
          <span className="text-gray-400">CE OI <strong className="text-gray-100">{fmtOI(data.total_ce_oi)}L</strong></span>
          <span className="text-gray-400">PE OI <strong className="text-gray-100">{fmtOI(data.total_pe_oi)}L</strong></span>
          <span className="text-gray-500 text-xs ml-auto flex items-center gap-1"><Activity className="w-3 h-3" /> {data.fetched_at}</span>
        </div>
      )}

      {/* Chain table */}
      {!data && !loading && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-12 text-center text-gray-500 text-sm">
          Choose an expiry and click <strong>Load Chain</strong> to view the live NIFTY option chain.
        </div>
      )}

      {rows.length > 0 && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
          <div className="grid grid-cols-2 text-center text-xs font-semibold">
            <div className="py-1.5 bg-red-500/10 text-red-300 border-r border-surface-3">CALLS</div>
            <div className="py-1.5 bg-emerald-500/10 text-emerald-300">PUTS</div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs whitespace-nowrap">
              <thead>
                <tr className="text-gray-500 border-y border-surface-3">
                  {['B/U', 'OI(L)', 'Vol', 'VWAP', 'IV', 'Δ', 'LTP', ''].map((h, i) => <th key={`c${h}${i}`} className="px-2 py-2 text-right font-medium">{h}</th>)}
                  <th className="px-2 py-2 text-center font-semibold text-gray-300 bg-surface-3/40">STRIKE</th>
                  {['', 'LTP', 'Δ', 'IV', 'VWAP', 'Vol', 'OI(L)', 'B/U'].map((h, i) => <th key={`p${h}${i}`} className="px-2 py-2 text-left font-medium">{h}</th>)}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const ce = r.ce, pe = r.pe;
                  const isAtm = r.strike === data.atm;
                  return (
                    <tr key={r.strike} className={`border-b border-surface-3/40 ${isAtm ? 'bg-brand-600/10' : 'hover:bg-surface-3/20'}`}>
                      {/* CALL side */}
                      <td className={`px-2 py-1.5 text-center ${r.ce_itm ? 'bg-amber-500/5' : ''}`}><Buildup v={ce?.buildup} /></td>
                      <td className={`px-2 py-1.5 text-right ${r.ce_itm ? 'bg-amber-500/5' : ''}`}><OICell cell={ce} align="text-right" /></td>
                      <td className={`px-2 py-1.5 text-right text-gray-400 ${r.ce_itm ? 'bg-amber-500/5' : ''}`}>{fmtVol(ce?.volume)}</td>
                      <td className={`px-2 py-1.5 text-right text-gray-400 ${r.ce_itm ? 'bg-amber-500/5' : ''}`}>{ce?.vwap ? `₹${INR(ce.vwap)}` : '—'}</td>
                      <td className={`px-2 py-1.5 text-right text-gray-400 ${r.ce_itm ? 'bg-amber-500/5' : ''}`}>{ce?.iv != null ? `${ce.iv}` : '—'}</td>
                      <td className={`px-2 py-1.5 text-right text-gray-400 ${r.ce_itm ? 'bg-amber-500/5' : ''}`}>{ce?.delta != null ? ce.delta : '—'}</td>
                      <td className={`px-2 py-1.5 text-right ${r.ce_itm ? 'bg-amber-500/5' : ''}`}>
                        {ce ? <span className="font-semibold text-gray-100">₹{INR(ce.ltp)}</span> : '—'}
                        {ce && <div className="text-[10px]"><ChangePct v={ce.change_pct} /></div>}
                      </td>
                      <td className="px-1 py-1.5 text-center">
                        {ce && <button onClick={() => handleDownload(ce)} title={`Download ${ce.symbol} 1-min`}
                          className="p-1 rounded bg-surface-3 hover:bg-brand-600/30 text-gray-300">
                          {dlBusy === ce.symbol ? <Loader2 className="w-3 h-3 animate-spin" /> : <Download className="w-3 h-3" />}
                        </button>}
                      </td>
                      {/* STRIKE */}
                      <td className={`px-2 py-1.5 text-center font-bold ${isAtm ? 'text-brand-300' : 'text-gray-200'} bg-surface-3/40`}>{r.strike}</td>
                      {/* PUT side */}
                      <td className="px-1 py-1.5 text-center">
                        {pe && <button onClick={() => handleDownload(pe)} title={`Download ${pe.symbol} 1-min`}
                          className="p-1 rounded bg-surface-3 hover:bg-emerald-600/30 text-gray-300">
                          {dlBusy === pe.symbol ? <Loader2 className="w-3 h-3 animate-spin" /> : <Download className="w-3 h-3" />}
                        </button>}
                      </td>
                      <td className={`px-2 py-1.5 text-left ${r.pe_itm ? 'bg-amber-500/5' : ''}`}>
                        {pe ? <span className="font-semibold text-gray-100">₹{INR(pe.ltp)}</span> : '—'}
                        {pe && <div className="text-[10px]"><ChangePct v={pe.change_pct} /></div>}
                      </td>
                      <td className={`px-2 py-1.5 text-left text-gray-400 ${r.pe_itm ? 'bg-amber-500/5' : ''}`}>{pe?.delta != null ? pe.delta : '—'}</td>
                      <td className={`px-2 py-1.5 text-left text-gray-400 ${r.pe_itm ? 'bg-amber-500/5' : ''}`}>{pe?.iv != null ? `${pe.iv}` : '—'}</td>
                      <td className={`px-2 py-1.5 text-left text-gray-400 ${r.pe_itm ? 'bg-amber-500/5' : ''}`}>{pe?.vwap ? `₹${INR(pe.vwap)}` : '—'}</td>
                      <td className={`px-2 py-1.5 text-left text-gray-400 ${r.pe_itm ? 'bg-amber-500/5' : ''}`}>{fmtVol(pe?.volume)}</td>
                      <td className={`px-2 py-1.5 text-left ${r.pe_itm ? 'bg-amber-500/5' : ''}`}><OICell cell={pe} align="text-left" /></td>
                      <td className={`px-2 py-1.5 text-center ${r.pe_itm ? 'bg-amber-500/5' : ''}`}><Buildup v={pe?.buildup} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-[11px] text-gray-500 border-t border-surface-3">
            <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded inline-block bg-amber-500/30" /> ITM</span>
            <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded inline-block bg-brand-600/30" /> ATM</span>
            <span><span className="text-emerald-400">▲</span> Long Buildup · <span className="text-red-400">▼</span> Short Buildup · <span className="text-blue-400">◹</span> Short Covering · <span className="text-orange-400">◺</span> Long Unwinding</span>
            <span>B/U = buildup · OI(L) lakhs (+Δ chg) · IV % · Δ delta · VWAP day-avg</span>
            <span className="ml-auto flex items-center gap-1"><Download className="w-3 h-3" /> per-strike = 1-min OHLCV + OI + VWAP CSV</span>
          </div>
        </div>
      )}
    </div>
  );
}
