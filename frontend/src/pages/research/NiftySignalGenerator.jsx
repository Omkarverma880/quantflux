import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  RefreshCw, Loader2, AlertCircle, Activity, Pause, Play, Save, Signal, Info, Download,
} from 'lucide-react';
import { api } from '../../api';

// Fixed set of instruments shown on the page — each with its natural ATM grid.
// Two independent tables (NIFTY on top, BANK NIFTY below); nothing is mixed.
const MARKETS = [
  { key: 'NIFTY', label: 'NIFTY', interval: 50 },
  { key: 'BANKNIFTY', label: 'BANK NIFTY', interval: 100 },
];

const TIMEFRAMES = [
  ['1m', '1 Minute'], ['3m', '3 Minutes'], ['5m', '5 Minutes'], ['10m', '10 Minutes'],
  ['15m', '15 Minutes'], ['30m', '30 Minutes'], ['1h', '1 Hour'], ['2h', '2 Hours'],
  ['4h', '4 Hours'], ['1d', '1 Day'], ['1w', '1 Week'], ['1M', '1 Month'],
];

const selCls =
  'bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';

const INT = (v) => (v == null ? '—' : Number(v).toLocaleString('en-IN'));
const NUM = (v, d = 2) =>
  v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });

// ── CSV export (offline research / archival) ──────────────────────
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
const CSV_HEADERS = [
  { key: 'date', label: 'Date' }, { key: 'time', label: 'Time' },
  { key: 'atm', label: 'ATM' }, { key: 'call_oi', label: 'Call OI' },
  { key: 'put_oi', label: 'Put OI' }, { key: 'diff', label: 'Diff' },
  { key: 'pcr', label: 'PCR' }, { key: 'option_signal', label: 'Option Signal' },
  { key: 'vwap', label: 'VWAP' }, { key: 'previous_vwap', label: 'Previous VWAP' },
  { key: 'price', label: 'Nifty Price' }, { key: 'vwap_signal', label: 'VWAP Signal' },
];

// Solid signal cell (BUY green / SELL red / NEUTRAL orange) — resembles the reference sheet.
const CELL = { green: 'bg-emerald-600 text-white', red: 'bg-red-600 text-white', orange: 'bg-orange-500 text-white' };
function SignalCell({ color, children }) {
  return <td className={`px-3 py-1 text-center font-semibold ${CELL[color] || 'text-gray-300'}`}>{children ?? '—'}</td>;
}

// ── One instrument's table (compact + vertically scrollable) ──────
function SignalTable({ label, data, loading, error, showPrev }) {
  const rows = data?.rows ? [...data.rows].reverse() : [];  // newest on top
  const isDateTf = data?.timeframe?.match(/[dwM]/);
  const latest = data?.rows?.[data.rows.length - 1];

  const handleDownload = () => {
    if (!data?.rows?.length) return;
    downloadCSV(`nifty_signal_generator_${data.market}_${data.timeframe}_${data.session_day}.csv`, CSV_HEADERS, data.rows);
  };

  const th = 'px-3 py-2 font-semibold text-center border-r border-surface-3 last:border-r-0 bg-surface-3';

  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
      {/* Title bar */}
      <div className="flex items-center justify-between gap-3 px-3 py-2 bg-emerald-600/15 border-b border-surface-3">
        <span className="text-sm font-bold text-emerald-300 tracking-wide">
          {label}{data ? ` — ${data.timeframe_label}` : ''}
        </span>
        <div className="flex items-center gap-3">
          {data && (
            <span className="text-[11px] text-gray-500 flex items-center gap-1">
              <Activity className="w-3 h-3" /> {data.fetched_at}
            </span>
          )}
          <button onClick={handleDownload} disabled={!data?.rows?.length}
            title={`Download ${label} table as CSV`}
            className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-40 transition">
            <Download className="w-3.5 h-3.5" /> CSV
          </button>
        </div>
      </div>

      {/* Per-instrument summary: ATM + selected strikes */}
      {data && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-3 py-2 text-sm border-b border-surface-3">
          <span className="text-gray-400">ATM <strong className="text-brand-300 text-base">{INT(data.atm)}</strong></span>
          <span className="text-gray-400">Expiry <strong className="text-gray-100">{data.expiry}</strong> <span className="text-gray-600">({data.expiry_type})</span></span>
          <span className="text-gray-400">Candles <strong className="text-gray-100">{data.rows?.length ?? 0}</strong></span>
          <span className="text-gray-500 text-xs">Strikes ({data.total_strikes}):</span>
          <div className="flex flex-wrap items-center gap-1.5">
            {(data.strikes || []).map((s) => (
              <span key={s}
                className={`px-2 py-0.5 rounded-md text-xs font-semibold border ${
                  s === data.atm ? 'bg-brand-600/20 text-brand-300 border-brand-500/40'
                                 : 'bg-surface-3 text-gray-300 border-surface-4'}`}>
                {INT(s)}{s === data.atm ? ' · ATM' : ''}
              </span>
            ))}
          </div>
        </div>
      )}

      {error && (
        <div className="flex items-center gap-2 px-4 py-3 text-red-400 text-sm">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      )}
      {!data && !error && (
        <div className="px-4 py-10 text-center text-gray-500 text-sm">
          {loading ? <span className="flex items-center justify-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Loading {label}…</span>
                   : `Click Generate to build the ${label} table.`}
        </div>
      )}

      {/* Scrollable table (sticky header, newest on top) */}
      {rows.length > 0 && (
        <div className="overflow-auto max-h-[360px]">
          <table className="w-full text-xs whitespace-nowrap">
            <thead className="sticky top-0 z-10">
              <tr className="text-gray-300 border-b border-surface-3">
                {['Time', 'Call OI', 'Put OI', 'Diff', 'PCR', 'Option Signal', 'VWAP',
                  ...(showPrev ? ['Previous VWAP'] : []), 'Nifty Price', 'VWAP Signal'].map((h) => (
                  <th key={h} className={th}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.datetime} className="border-b border-surface-3/40 text-center hover:bg-surface-3/10">
                  <td className="px-3 py-1 font-semibold text-gray-200">{isDateTf ? r.date : r.time}</td>
                  <td className="px-3 py-1 text-gray-300">{INT(r.call_oi)}</td>
                  <td className="px-3 py-1 text-gray-300">{INT(r.put_oi)}</td>
                  <SignalCell color={r.option_color}>{INT(r.diff)}</SignalCell>
                  <SignalCell color={r.option_color}>{r.pcr == null ? '—' : NUM(r.pcr, 2)}</SignalCell>
                  <SignalCell color={r.option_color}>{r.option_signal || '—'}</SignalCell>
                  <td className="px-3 py-1 text-gray-300">{NUM(r.vwap, 2)}</td>
                  {showPrev && <td className="px-3 py-1 text-gray-400">{NUM(r.previous_vwap, 2)}</td>}
                  <SignalCell color={r.vwap_color}>{NUM(r.price, 2)}</SignalCell>
                  <SignalCell color={r.vwap_color}>{r.vwap_signal || '—'}</SignalCell>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function NiftySignalGenerator() {
  const [cfg, setCfg] = useState({
    timeframe: '15m', strike_count: 2, expiry_type: 'weekly',
    refresh_interval: 30, show_previous_vwap: true,
  });
  const [date, setDate] = useState('');
  const [auto, setAuto] = useState(false);
  const [results, setResults] = useState({});   // { NIFTY: {data,error}, BANKNIFTY: {data,error} }
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState('');
  const timer = useRef(null);

  const patch = (k, v) => setCfg((c) => ({ ...c, [k]: v }));

  useEffect(() => {
    api.researchSignalGeneratorConfig()
      .then((r) => { if (r.status === 'ok' && r.config) setCfg((c) => ({ ...c, ...r.config })); })
      .catch(() => {});
  }, []);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    await Promise.all(MARKETS.map(async (m) => {
      try {
        const res = await api.researchSignalGenerator({
          market: m.key, timeframe: cfg.timeframe, strike_interval: m.interval,
          strike_count: cfg.strike_count, expiry_type: cfg.expiry_type, date: date || null,
        });
        setResults((prev) => ({
          ...prev,
          [m.key]: res.status === 'ok'
            ? { data: res, error: '' }
            : { data: silent ? prev[m.key]?.data : null, error: res.message || 'Failed to generate' },
        }));
      } catch (e) {
        setResults((prev) => ({ ...prev, [m.key]: { data: silent ? prev[m.key]?.data : null, error: e.message || 'Failed to generate' } }));
      }
    }));
    if (!silent) setLoading(false);
  }, [cfg.timeframe, cfg.strike_count, cfg.expiry_type, date]);

  // Regenerate on any config change.
  useEffect(() => { load(false); /* eslint-disable-next-line */ }, [
    cfg.timeframe, cfg.strike_count, cfg.expiry_type, date,
  ]);

  // Auto-refresh (live append — backend caches completed candles).
  useEffect(() => {
    if (timer.current) clearInterval(timer.current);
    if (auto) timer.current = setInterval(() => load(true), Math.max(3, cfg.refresh_interval) * 1000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [auto, cfg.refresh_interval, load]);

  const saveDefaults = async () => {
    setSaving(true);
    try {
      const r = await api.researchSignalGeneratorConfigSave({
        timeframe: cfg.timeframe, strike_count: cfg.strike_count,
        expiry_type: cfg.expiry_type, show_previous_vwap: cfg.show_previous_vwap,
      });
      if (r.status === 'ok') { setCfg((c) => ({ ...c, ...r.config })); setSavedMsg('Saved'); setTimeout(() => setSavedMsg(''), 2500); }
    } catch { /* ignore */ }
    finally { setSaving(false); }
  };

  const showPrev = cfg.show_previous_vwap;

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-[1400px] mx-auto">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Signal className="w-5 h-5 text-brand-400" />
            <h1 className="text-xl font-bold text-gray-100 tracking-wide">NIFTY SIGNAL GENERATOR</h1>
            <span className="px-2 py-0.5 rounded-full bg-brand-600/15 text-brand-400 text-xs font-semibold border border-brand-500/20">
              Research Purpose Only
            </span>
          </div>
          <p className="text-gray-500 text-sm mt-0.5">
            Option-Chain (PCR) &amp; VWAP signals per completed candle — NIFTY &amp; BANK NIFTY side by side. Read-only.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setAuto((a) => !a)}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border transition ${
              auto ? 'bg-emerald-600/20 text-emerald-400 border-emerald-500/40' : 'bg-surface-3 text-gray-400 border-surface-4'}`}>
            {auto ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />} Live {auto ? 'ON' : 'OFF'}
          </button>
          <button onClick={saveDefaults} disabled={saving}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-50 transition">
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            {savedMsg || 'Save as default'}
          </button>
        </div>
      </div>

      {/* Controls (shared by both tables — no market/interval mixup: each uses its natural grid) */}
      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3 items-end">
          <div>
            <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">Timeframe</label>
            <select value={cfg.timeframe} onChange={(e) => patch('timeframe', e.target.value)} className={`w-full ${selCls}`}>
              {TIMEFRAMES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">Strikes ± ATM</label>
            <input type="number" min="1" max="20" value={cfg.strike_count}
              onChange={(e) => patch('strike_count', Math.max(1, Math.min(20, parseInt(e.target.value) || 1)))}
              className={`w-full ${selCls}`} />
          </div>
          <div>
            <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">Expiry (OI)</label>
            <select value={cfg.expiry_type} onChange={(e) => patch('expiry_type', e.target.value)} className={`w-full ${selCls}`}>
              <option value="weekly">Weekly (nearest)</option>
              <option value="monthly">Monthly</option>
            </select>
          </div>
          <div>
            <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">Session date</label>
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} className={`w-full ${selCls}`} />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3 mt-3">
          <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer">
            <input type="checkbox" checked={showPrev} onChange={(e) => patch('show_previous_vwap', e.target.checked)} className="accent-brand-500" />
            Show Previous VWAP column
          </label>
          <span className="text-[11px] text-gray-500">
            Selected strikes = (strikes × 2) + 1 = <strong className="text-gray-300">{cfg.strike_count * 2 + 1}</strong>
            <span className="text-gray-600"> · grid: NIFTY 50 · BANK NIFTY 100</span>
          </span>
          <button onClick={() => load(false)} disabled={loading}
            className="ml-auto flex items-center justify-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50 transition">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />} Generate
          </button>
        </div>
      </div>

      {/* Two independent, scrollable tables */}
      {MARKETS.map((m) => (
        <SignalTable key={m.key} label={m.label}
          data={results[m.key]?.data} error={results[m.key]?.error}
          loading={loading} showPrev={showPrev} />
      ))}

      {/* Legend */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-1 text-[11px] text-gray-500">
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded inline-block bg-emerald-600" /> BUY</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded inline-block bg-red-600" /> SELL</span>
        <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded inline-block bg-orange-500" /> NEUTRAL</span>
        <span>Option Signal: PCR &gt; 1 BUY · &lt; 1 SELL · = 1 NEUTRAL</span>
        <span>VWAP Signal: LTP &gt; VWAP BUY · &lt; VWAP SELL</span>
        <span className="flex items-center gap-1"><Info className="w-3 h-3" /> Call/Put OI = summed OI of the strikes around each candle's ATM. Tables scroll for older candles.</span>
      </div>
    </div>
  );
}
