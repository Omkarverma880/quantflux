import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  RefreshCw, Loader2, AlertCircle, Activity, Pause, Play, Save, Signal, Info,
} from 'lucide-react';
import { api } from '../../api';

// User-facing timeframe options — keys match the backend constants.TIMEFRAMES.
const TIMEFRAMES = [
  ['1m', '1 Minute'], ['3m', '3 Minutes'], ['5m', '5 Minutes'], ['10m', '10 Minutes'],
  ['15m', '15 Minutes'], ['30m', '30 Minutes'], ['1h', '1 Hour'], ['2h', '2 Hours'],
  ['4h', '4 Hours'], ['1d', '1 Day'], ['1w', '1 Week'], ['1M', '1 Month'],
];
const STRIKE_INTERVALS = [25, 50, 100];
const MARKETS = [['NIFTY', 'NIFTY', true], ['BANKNIFTY', 'Bank Nifty', false],
  ['FINNIFTY', 'Fin Nifty', false], ['MIDCPNIFTY', 'Midcap Nifty', false]];

const selCls =
  'bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';

const INT = (v) => (v == null ? '—' : Number(v).toLocaleString('en-IN'));
const NUM = (v, d = 2) =>
  v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });

// Solid signal cell (BUY green / SELL red / NEUTRAL orange) — resembles the reference sheet.
const CELL = {
  green: 'bg-emerald-600 text-white',
  red: 'bg-red-600 text-white',
  orange: 'bg-orange-500 text-white',
};
function SignalCell({ color, children, className = '' }) {
  const c = CELL[color] || 'text-gray-300';
  return <td className={`px-3 py-1.5 text-center font-semibold ${c} ${className}`}>{children ?? '—'}</td>;
}

export default function NiftySignalGenerator() {
  const [cfg, setCfg] = useState({
    timeframe: '15m', strike_interval: 50, strike_count: 2, market: 'NIFTY',
    expiry_type: 'weekly', refresh_interval: 30, show_previous_vwap: true,
  });
  const [date, setDate] = useState('');
  const [auto, setAuto] = useState(false);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [savedMsg, setSavedMsg] = useState('');
  const timer = useRef(null);

  const showErr = (m) => { setError(m); setTimeout(() => setError(''), 5000); };
  const patch = (k, v) => setCfg((c) => ({ ...c, [k]: v }));

  // Load persisted config once.
  useEffect(() => {
    api.researchSignalGeneratorConfig()
      .then((r) => { if (r.status === 'ok' && r.config) setCfg((c) => ({ ...c, ...r.config })); })
      .catch(() => {});
  }, []);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const res = await api.researchSignalGenerator({
        timeframe: cfg.timeframe, strike_interval: cfg.strike_interval,
        strike_count: cfg.strike_count, market: cfg.market,
        expiry_type: cfg.expiry_type, date: date || null,
      });
      if (res.status === 'ok') setData(res);
      else if (!silent) showErr(res.message || 'Failed to generate signals');
    } catch (e) { if (!silent) showErr(e.message || 'Failed to generate signals'); }
    finally { if (!silent) setLoading(false); }
  }, [cfg.timeframe, cfg.strike_interval, cfg.strike_count, cfg.market, cfg.expiry_type, date]);

  // Regenerate whenever a config knob changes (spec: changing timeframe rebuilds table).
  useEffect(() => { load(false); /* eslint-disable-next-line */ }, [
    cfg.timeframe, cfg.strike_interval, cfg.strike_count, cfg.market, cfg.expiry_type, date,
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
      const r = await api.researchSignalGeneratorConfigSave(cfg);
      if (r.status === 'ok') {
        setCfg((c) => ({ ...c, ...r.config }));
        setSavedMsg('Saved as default'); setTimeout(() => setSavedMsg(''), 2500);
      } else showErr(r.message || 'Save failed');
    } catch (e) { showErr(e.message || 'Save failed'); }
    finally { setSaving(false); }
  };

  const showPrev = cfg.show_previous_vwap;
  const rows = data?.rows ? [...data.rows].reverse() : [];   // newest on top (like the reference)
  const cols = 10 - (showPrev ? 0 : 1);

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
            Option-Chain (PCR) &amp; VWAP signals per completed candle. Read-only — never places trades.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setAuto((a) => !a)}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border transition ${
              auto ? 'bg-emerald-600/20 text-emerald-400 border-emerald-500/40' : 'bg-surface-3 text-gray-400 border-surface-4'
            }`}>
            {auto ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />} Live {auto ? 'ON' : 'OFF'}
          </button>
          <button onClick={saveDefaults} disabled={saving}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-50 transition">
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            {savedMsg || 'Save as default'}
          </button>
        </div>
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
            <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">Timeframe</label>
            <select value={cfg.timeframe} onChange={(e) => patch('timeframe', e.target.value)} className={`w-full ${selCls}`}>
              {TIMEFRAMES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">Strike Interval</label>
            <select value={cfg.strike_interval} onChange={(e) => patch('strike_interval', parseInt(e.target.value))} className={`w-full ${selCls}`}>
              {STRIKE_INTERVALS.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">Strikes ± ATM</label>
            <input type="number" min="1" max="20" value={cfg.strike_count}
              onChange={(e) => patch('strike_count', Math.max(1, Math.min(20, parseInt(e.target.value) || 1)))}
              className={`w-full ${selCls}`} />
          </div>
          <div>
            <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">Market</label>
            <select value={cfg.market} onChange={(e) => patch('market', e.target.value)} className={`w-full ${selCls}`}>
              {MARKETS.map(([v, l, on]) => <option key={v} value={v} disabled={!on}>{l}{on ? '' : ' (soon)'}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">Expiry (OI)</label>
            <select value={cfg.expiry_type} onChange={(e) => patch('expiry_type', e.target.value)} className={`w-full ${selCls}`}>
              <option value="weekly">Weekly</option>
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
            <input type="checkbox" checked={showPrev} onChange={(e) => patch('show_previous_vwap', e.target.checked)}
              className="accent-brand-500" />
            Show Previous VWAP column
          </label>
          <span className="text-[11px] text-gray-500">Total selected strikes = (strikes × 2) + 1 = <strong className="text-gray-300">{cfg.strike_count * 2 + 1}</strong></span>
          <button onClick={() => load(false)} disabled={loading}
            className="ml-auto flex items-center justify-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50 transition">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />} Generate
          </button>
        </div>
      </div>

      {/* Summary bar */}
      {data && (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm bg-surface-2 border border-surface-3 rounded-xl px-4 py-2.5">
          <span className="text-gray-400">Market <strong className="text-brand-400">{data.market_label}</strong></span>
          <span className="text-gray-400">Timeframe <strong className="text-gray-100">{data.timeframe_label}</strong></span>
          <span className="text-gray-400">Session <strong className="text-gray-100">{data.session_day}</strong></span>
          <span className="text-gray-400">Expiry <strong className="text-gray-100">{data.expiry}</strong> <span className="text-gray-600">({data.expiry_type})</span></span>
          <span className="text-gray-400">Strikes <strong className="text-gray-100">{data.total_strikes}</strong></span>
          <span className="text-gray-400">Candles <strong className="text-gray-100">{data.rows?.length ?? 0}</strong></span>
          <span className="text-gray-500 text-xs ml-auto flex items-center gap-1"><Activity className="w-3 h-3" /> {data.fetched_at}</span>
        </div>
      )}

      {data?.oi_limited && (
        <div className="flex items-center gap-2 bg-amber-500/10 border border-amber-500/30 rounded-lg px-4 py-2 text-amber-400 text-xs">
          <Info className="w-4 h-4" /> Price moved across many strikes — OI sums may be limited to the most recent candles.
        </div>
      )}

      {/* Table */}
      {!data && !loading && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-12 text-center text-gray-500 text-sm">
          Choose a configuration and click <strong>Generate</strong> to build the signal table.
        </div>
      )}

      {rows.length > 0 && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
          <div className="py-2 text-center text-sm font-bold text-emerald-300 bg-emerald-600/15 border-b border-surface-3">
            NIFTY SIGNAL GENERATOR — {data.timeframe_label}
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs whitespace-nowrap">
              <thead>
                <tr className="text-gray-300 bg-surface-3/60 border-b border-surface-3">
                  {['Time', 'Call OI', 'Put OI', 'Diff', 'PCR', 'Option Signal', 'VWAP',
                    ...(showPrev ? ['Previous VWAP'] : []), 'Nifty Price', 'VWAP Signal'].map((h) => (
                    <th key={h} className="px-3 py-2 font-semibold text-center border-r border-surface-3 last:border-r-0">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.datetime} className="border-b border-surface-3/40 text-center hover:bg-surface-3/10">
                    <td className="px-3 py-1.5 font-semibold text-gray-200">{data.timeframe.match(/[dwM]/) ? r.date : r.time}</td>
                    <td className="px-3 py-1.5 text-gray-300">{INT(r.call_oi)}</td>
                    <td className="px-3 py-1.5 text-gray-300">{INT(r.put_oi)}</td>
                    <SignalCell color={r.option_color}>{INT(r.diff)}</SignalCell>
                    <SignalCell color={r.option_color}>{r.pcr == null ? '—' : NUM(r.pcr, 2)}</SignalCell>
                    <SignalCell color={r.option_color}>{r.option_signal || '—'}</SignalCell>
                    <td className="px-3 py-1.5 text-gray-300">{NUM(r.vwap, 2)}</td>
                    {showPrev && <td className="px-3 py-1.5 text-gray-400">{NUM(r.previous_vwap, 2)}</td>}
                    <SignalCell color={r.vwap_color}>{NUM(r.price, 2)}</SignalCell>
                    <SignalCell color={r.vwap_color}>{r.vwap_signal || '—'}</SignalCell>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-[11px] text-gray-500 border-t border-surface-3">
            <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded inline-block bg-emerald-600" /> BUY</span>
            <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded inline-block bg-red-600" /> SELL</span>
            <span className="flex items-center gap-1"><span className="w-2.5 h-2.5 rounded inline-block bg-orange-500" /> NEUTRAL</span>
            <span>Option Signal: PCR &gt; 1 BUY · &lt; 1 SELL · = 1 NEUTRAL</span>
            <span>VWAP Signal: LTP &gt; VWAP BUY · &lt; VWAP SELL</span>
            <span className="ml-auto">Call/Put OI = summed OI of {data.total_strikes} strikes around ATM</span>
          </div>
        </div>
      )}
    </div>
  );
}
