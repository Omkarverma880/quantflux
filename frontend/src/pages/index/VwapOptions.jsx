import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  Activity, Loader2, Play, X, Save, Download, Info, TrendingUp, TrendingDown,
  FlaskConical, Wallet, Radio, RefreshCw, SlidersHorizontal, LineChart, Plus, Trash2,
} from 'lucide-react';
import { api } from '../../api';
import CandleChart from '../../components/CandleChart';

const sel = 'bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';
const lbl = 'block text-[11px] text-gray-500 uppercase tracking-wide mb-1';
const NUM = (v, d = 2) => (v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
const INT = (v) => (v == null ? '—' : Number(v).toLocaleString('en-IN'));
const pc = (v) => (v == null ? 'text-gray-500' : v >= 0 ? 'text-emerald-400' : 'text-red-400');

const LINE_COLORS = {
  day_vwap: '#38bdf8', prev_day_vwap: '#f59e0b', prev_week_vwap: '#a78bfa',
  prev_month_vwap: '#ec4899', roll_15d_vwap: '#22d3ee', roll_90d_vwap: '#84cc16',
};
const srcBadge = (s) => (s === 'REAL'
  ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40'
  : 'bg-amber-500/15 text-amber-300 border-amber-500/40');
const stColor = (s) => ({ TARGET: 'text-emerald-400', STOP: 'text-red-400', OPEN: 'text-amber-400', SQUAREOFF: 'text-gray-400', EXPIRY: 'text-gray-400' }[s] || 'text-gray-400');

export default function VwapOptions() {
  const [tab, setTab] = useState('chart');
  const [cfg, setCfg] = useState(null);
  const [meta, setMeta] = useState({ lines: {}, events: {}, actions: {}, timeframes: [] });
  const [err, setErr] = useState(''); const [msg, setMsg] = useState('');
  const showErr = (m) => { setErr(m); setTimeout(() => setErr(''), 7000); };
  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(''), 2500); };
  const patch = (k, v) => setCfg((c) => ({ ...c, [k]: v }));

  useEffect(() => {
    api.voConfig().then((r) => {
      if (r.status === 'ok') {
        setCfg(r.config);
        setMeta({ lines: r.lines, events: r.events, actions: r.actions, timeframes: r.timeframes });
      }
    }).catch(() => setCfg({}));
  }, []);

  const saveCfg = async () => {
    const r = await api.voConfigSave(cfg);
    if (r.status === 'ok') { setCfg(r.config); flash('Settings saved'); } else showErr(r.message);
  };

  if (!cfg) return <div className="p-6 text-gray-500 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>;

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-[1700px] mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Activity className="w-6 h-6 text-brand-400" />
            <h1 className="text-xl font-bold text-gray-100">VWAP Options Engine</h1>
            <span className="text-[11px] px-2 py-0.5 rounded-full bg-brand-500/15 text-brand-300 border border-brand-500/25">NIFTY</span>
            <span className={`text-[11px] px-2 py-0.5 rounded-full border font-semibold ${cfg.paper_trade ? 'bg-amber-500/15 text-amber-300 border-amber-500/40' : 'bg-red-500/15 text-red-300 border-red-500/40'}`}>
              {cfg.paper_trade ? 'PAPER MODE' : 'LIVE MODE ARMED'}
            </span>
          </div>
          <p className="text-sm text-gray-500 mt-0.5">
            Pick VWAP lines → trigger (touch / cross) → buy CE or PE → strike, expiry, target/SL. One engine drives chart, backtest and live.
          </p>
        </div>
      </div>

      <div className="flex items-center gap-1 border-b border-surface-3 overflow-x-auto">
        {[['chart', 'Chart', LineChart], ['backtest', 'Backtest', FlaskConical], ['positions', 'Positions', Wallet], ['settings', 'Settings', SlidersHorizontal], ['info', 'Info', Info]].map(([id, label, Icon]) => (
          <button key={id} onClick={() => setTab(id)} className={`flex items-center gap-1.5 px-3 py-2 text-sm font-medium border-b-2 -mb-px whitespace-nowrap ${tab === id ? 'border-brand-500 text-brand-300' : 'border-transparent text-gray-400 hover:text-gray-200'}`}>
            <Icon className="w-4 h-4" /> {label}
          </button>
        ))}
      </div>

      {err && <div className="text-sm text-red-400 bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2">{err}</div>}
      {msg && <div className="text-sm text-emerald-400 bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-3 py-2">{msg}</div>}

      {tab === 'chart' && <ChartTab cfg={cfg} patch={patch} meta={meta} showErr={showErr} />}
      {tab === 'backtest' && <BacktestTab cfg={cfg} setCfg={setCfg} meta={meta} showErr={showErr} flash={flash} saveCfg={saveCfg} />}
      {tab === 'positions' && <PositionsTab cfg={cfg} patch={patch} saveCfg={saveCfg} showErr={showErr} flash={flash} />}
      {tab === 'settings' && <SettingsTab cfg={cfg} patch={patch} setCfg={setCfg} meta={meta} saveCfg={saveCfg} showErr={showErr} />}
      {tab === 'info' && <InfoTab />}
    </div>
  );
}

/* last non-null value in a VWAP series */
const lastVal = (rows, key) => {
  for (let i = (rows || []).length - 1; i >= 0; i -= 1) {
    const v = rows[i]?.[key];
    if (v != null) return v;
  }
  return null;
};

/* A rolling N-day VWAP needs N *completed* sessions before it means anything.
   When one is blank, say how short it is rather than a bare "warming up" — a
   short window must never be shown as if it were the real line. */
const rollDays = (key) => {
  const m = /^roll_(\d+)d_vwap$/.exec(key || '');
  return m ? Number(m[1]) : null;
};
const seedHave = (info, key, side) => {
  const need = rollDays(key);
  if (!info || need == null) return null;
  const have = side === 'index' ? info.index_sessions : info.futures_sessions;
  return (have == null) ? null : { have, need };
};
const seedShort = (info, key, side) => {
  const s = seedHave(info, key, side);
  if (!s) return '—';
  return `${s.have}/${s.need} sessions`;
};
const seedNote = (info, key, side) => {
  const s = seedHave(info, key, side);
  if (!s) return 'No value for this bar.';
  return `Needs ${s.need} completed sessions; only ${s.have} of history are available`
    + (side === 'futures'
      ? '. NIFTY futures contracts are listed for ~3 months, so futures history cannot reach far back — switch VWAP source to Index for the longer windows.'
      : '.');
};

/* ── strike picker: manual ladder, or auto-pick by moneyness ── */
function StrikePicker({ cfg, patch, ladder }) {
  const auto = cfg.strike_mode === 'auto';
  const preview = (side) => {
    if (!auto) return null;
    const steps = Math.round((Number(cfg.auto_points) || 0) / 50);
    if (!steps || cfg.auto_moneyness === 'ATM') return 'ATM';
    const sign = cfg.auto_moneyness === 'OTM' ? (side === 'CE' ? 1 : -1) : (side === 'CE' ? -1 : 1);
    const off = sign * steps * 50;
    return `ATM${off > 0 ? '+' : '-'}${Math.abs(off)}`;
  };
  return (
    <div className="col-span-2">
      <div className="flex items-center justify-between mb-1">
        <label className={`${lbl} !mb-0`}>Strike</label>
        <label className="flex items-center gap-1.5 text-[11px] text-gray-400 cursor-pointer"
          title="Auto-pick resolves the strike per option type by moneyness, so one setting means the same thing for CALLs and PUTs.">
          <input type="checkbox" checked={auto} onChange={(e) => patch('strike_mode', e.target.checked ? 'auto' : 'fixed')} className="accent-brand-500" />
          Auto-pick
        </label>
      </div>
      {!auto ? (
        <select value={cfg.strike_offset_steps} onChange={(e) => patch('strike_offset_steps', Number(e.target.value))} className={`w-full ${sel}`}>
          {(ladder?.strikes || []).map((k) => <option key={k.offset_steps} value={k.offset_steps}>{k.label}{ladder?.spot ? ` (${k.strike})` : ''}</option>)}
          {!ladder && Array.from({ length: 51 }, (_, i) => i - 25).map((i) => (
            <option key={i} value={i}>{i === 0 ? 'ATM' : `ATM${i > 0 ? '+' : '-'}${Math.abs(i) * 50}`}</option>
          ))}
        </select>
      ) : (
        <div className="flex gap-2">
          <select value={cfg.auto_moneyness} onChange={(e) => patch('auto_moneyness', e.target.value)} className={`flex-1 ${sel}`}>
            <option value="OTM">OTM</option><option value="ATM">ATM</option><option value="ITM">ITM</option>
          </select>
          <select value={cfg.auto_points} onChange={(e) => patch('auto_points', Number(e.target.value))} className={`flex-1 ${sel}`} disabled={cfg.auto_moneyness === 'ATM'}>
            {[0, 50, 100, 150, 200, 250, 300, 400, 500].map((v) => <option key={v} value={v}>{v === 0 ? 'ATM' : `${v} pts`}</option>)}
          </select>
        </div>
      )}
      {auto && (
        <div className="text-[10px] text-gray-500 mt-1">
          CALL → <span className="text-emerald-400">{preview('CE')}</span> · PUT → <span className="text-red-400">{preview('PE')}</span>
          {ladder?.spot ? ` (spot ${ladder.spot})` : ''}
        </div>
      )}
    </div>
  );
}

/* ── rule builder shared by Backtest + Settings ── */
function RuleBuilder({ cfg, setCfg, meta }) {
  const rules = cfg.rules || [];
  const update = (i, k, v) => setCfg((c) => {
    const rs = [...(c.rules || [])];
    rs[i] = { ...rs[i], [k]: v };
    return { ...c, rules: rs };
  });
  const add = () => setCfg((c) => ({ ...c, rules: [...(c.rules || []), { line: 'prev_day_vwap', event: 'touch', action: 'BUY_CE', enabled: true }] }));
  const del = (i) => setCfg((c) => ({ ...c, rules: (c.rules || []).filter((_, j) => j !== i) }));
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className={lbl}>Signal rules — when this VWAP line is hit, buy this option</span>
        <button onClick={add} className="flex items-center gap-1 text-xs px-2 py-1 rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white"><Plus className="w-3.5 h-3.5" /> Add rule</button>
      </div>
      {rules.map((r, i) => (
        <div key={i} className="flex flex-wrap items-center gap-2 bg-surface-3/30 border border-surface-3 rounded-lg px-2 py-1.5">
          <input type="checkbox" checked={!!r.enabled} onChange={(e) => update(i, 'enabled', e.target.checked)} className="accent-brand-500" title="Enable this rule" />
          <span className="w-2.5 h-2.5 rounded-full" style={{ background: LINE_COLORS[r.line] || '#888' }} />
          <select value={r.line} onChange={(e) => update(i, 'line', e.target.value)} className={`${sel} py-1 text-xs`}>
            {Object.entries(meta.lines || {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <select value={r.event} onChange={(e) => update(i, 'event', e.target.value)} className={`${sel} py-1 text-xs`}>
            {Object.entries(meta.events || {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <span className="text-xs text-gray-500">→</span>
          <select value={r.action} onChange={(e) => update(i, 'action', e.target.value)} className={`${sel} py-1 text-xs`}>
            {Object.entries(meta.actions || {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <button onClick={() => del(i)} className="ml-auto text-gray-500 hover:text-red-400"><Trash2 className="w-3.5 h-3.5" /></button>
        </div>
      ))}
      {!rules.length && <div className="text-xs text-gray-500 py-2">No rules — add at least one to generate signals.</div>}
    </div>
  );
}

function ChartTab({ cfg, patch, meta, showErr }) {
  const [day, setDay] = useState('');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [shown, setShown] = useState(() => Object.keys(LINE_COLORS).reduce((a, k) => ({ ...a, [k]: k === 'day_vwap' || k === 'prev_day_vwap' }), {}));
  const [auto, setAuto] = useState(true);
  const [showIndex, setShowIndex] = useState(true);
  const [lastAt, setLastAt] = useState(null);
  const abortRef = useRef(null);
  const inFlight = useRef(false);
  const pollRef = useRef(null);
  const today = new Date().toISOString().slice(0, 10);
  const isToday = day === today;

  useEffect(() => { setDay(today); }, []); // eslint-disable-line
  const load = useCallback(async (d, silent = false) => {
    if (inFlight.current) return;                 // never stack refreshes
    inFlight.current = true;
    const ac = new AbortController(); abortRef.current = ac;
    if (!silent) setLoading(true);
    try {
      const r = await api.voChart({ date: d || null, overrides: cfg }, ac.signal);
      if (r.status === 'ok') { setData(r); setLastAt(new Date()); }
      else if (!silent) { setData(null); showErr(r.message); }
    } catch (e) { if (e.name !== 'AbortError' && !silent) showErr(e.message); }
    finally { inFlight.current = false; if (!silent) setLoading(false); }
  }, [cfg]); // eslint-disable-line
  useEffect(() => { if (day) load(day); }, [day, cfg.timeframe, cfg.vwap_source]); // eslint-disable-line

  // Live auto-refresh — only meaningful for today; polls each bar bucket.
  useEffect(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    if (!auto || !isToday || !day) return undefined;
    const secs = cfg.timeframe === 'minute' ? 20 : cfg.timeframe === '3minute' ? 30 : 60;
    pollRef.current = setInterval(() => load(day, true), secs * 1000);
    return () => pollRef.current && clearInterval(pollRef.current);
  }, [auto, isToday, day, cfg.timeframe]); // eslint-disable-line

  const overlays = useMemo(() => Object.keys(meta.lines || {})
    .filter((k) => shown[k])
    .map((k) => ({ key: k, label: meta.lines[k], color: LINE_COLORS[k] || '#888',
      values: (data?.series || []).map((s) => s[k]) })), [data, shown, meta]);

  const idxOverlays = useMemo(() => Object.keys(meta.lines || {})
    .filter((k) => shown[k])
    .map((k) => ({ key: `i_${k}`, label: meta.lines[k], color: LINE_COLORS[k] || '#888',
      values: (data?.index_series || []).map((x) => x[k]) })), [data, shown, meta]);

  const markers = useMemo(() => (data?.signals || []).map((s) => ({
    index: (data.candles || []).findIndex((c) => c.t === s.time),
    color: s.action === 'BUY_CE' ? '#10b981' : '#ef4444',
    side: s.action === 'BUY_CE' ? 'up' : 'down',
  })), [data]);

  return (
    <div className="space-y-3">
      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <div><label className={lbl}>Date</label><input type="date" value={day} onChange={(e) => setDay(e.target.value)} className={sel} /></div>
          <div><label className={lbl}>Timeframe</label>
            <select value={cfg.timeframe} onChange={(e) => patch('timeframe', e.target.value)} className={sel}>
              {(meta.timeframes || []).map((t) => <option key={t} value={t}>{t}</option>)}
            </select></div>
          <div><label className={lbl}>VWAP source</label>
            <select value={cfg.vwap_source} onChange={(e) => patch('vwap_source', e.target.value)} className={sel}>
              <option value="futures">NIFTY Futures (true VWAP)</option>
              <option value="index">Index (HLC3 average)</option>
            </select></div>
          <button onClick={() => load(day)} disabled={loading} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />} Load
          </button>
          {loading && <button onClick={() => abortRef.current?.abort()} className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg bg-red-600/80 hover:bg-red-600 text-white font-semibold"><X className="w-4 h-4" /> Cancel</button>}
          <label className={`flex items-center gap-2 text-sm cursor-pointer ${isToday ? 'text-gray-300' : 'text-gray-600'}`}
            title={isToday ? 'Refreshes automatically while the market is open' : 'Auto-refresh only applies to today'}>
            <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} disabled={!isToday} className="accent-brand-500" /> Auto-refresh
          </label>
          <span className="ml-auto flex items-center gap-2 text-xs">
            {isToday && auto
              ? <span className="flex items-center gap-1 text-emerald-400"><Radio className="w-3.5 h-3.5 animate-pulse" /> LIVE</span>
              : <span className="text-gray-500">{isToday ? 'Paused' : 'Historical'}</span>}
            {lastAt && <span className="text-gray-500">Updated {lastAt.toLocaleTimeString('en-IN', { hour12: false })}</span>}
          </span>
        </div>
        {/* VWAP checkboxes */}
        <div className="flex flex-wrap gap-3 pt-2 border-t border-surface-3">
          {Object.entries(meta.lines || {}).map(([k, label]) => (
            <label key={k} className="flex items-center gap-1.5 text-xs text-gray-300 cursor-pointer">
              <input type="checkbox" checked={!!shown[k]} onChange={(e) => setShown((s) => ({ ...s, [k]: e.target.checked }))} className="accent-brand-500" />
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: LINE_COLORS[k] }} /> {label}
            </label>
          ))}
        </div>
        {data && (() => {
          const fut = data.candles?.length ? data.candles[data.candles.length - 1].close : null;
          const idx = data.index_candles?.length ? data.index_candles[data.index_candles.length - 1].close : null;
          const on = Object.keys(meta.lines || {}).filter((k) => shown[k]);
          return (
            <div className="pt-2 border-t border-surface-3 space-y-1.5">
              <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs">
                <span className="text-gray-400">NIFTY {data.meta?.source === 'futures' ? 'FUT' : 'INDEX'} <strong className="text-gray-100 text-sm">{NUM(fut)}</strong></span>
                {idx != null && <span className="text-gray-400">INDEX <strong className="text-gray-100 text-sm">{NUM(idx)}</strong></span>}
                {data.basis != null && (
                  <span className="text-gray-400">Basis <strong className={data.basis >= 0 ? 'text-emerald-400' : 'text-red-400'}>{data.basis >= 0 ? '+' : ''}{NUM(data.basis, 1)}</strong></span>
                )}
              </div>
              {!!on.length && (
                <div className="overflow-x-auto">
                  <table className="text-[11px] whitespace-nowrap">
                    <thead className="text-gray-500">
                      <tr>
                        <th className="pr-4 py-0.5 text-left font-medium">VWAP line</th>
                        <th className="pr-4 py-0.5 text-right font-medium">Futures</th>
                        <th className="pr-4 py-0.5 text-right font-medium">Index</th>
                        <th className="pr-4 py-0.5 text-right font-medium">Fut vs line</th>
                      </tr>
                    </thead>
                    <tbody>
                      {on.map((k) => {
                        const fv = lastVal(data.series, k);
                        const iv = lastVal(data.index_series, k);
                        const diff = (fut != null && fv != null) ? fut - fv : null;
                        return (
                          <tr key={k}>
                            <td className="pr-4 py-0.5 text-gray-300">
                              <span className="inline-block w-2 h-2 rounded-full mr-1.5 align-middle" style={{ background: LINE_COLORS[k] }} />
                              {meta.lines[k]}
                            </td>
                            <td className="pr-4 py-0.5 text-right text-gray-200">{fv == null ? <span className="text-gray-600" title={seedNote(data.seed_info, k, 'futures')}>{seedShort(data.seed_info, k, 'futures')}</span> : NUM(fv)}</td>
                            <td className="pr-4 py-0.5 text-right text-gray-200">{iv == null ? <span className="text-gray-600" title={seedNote(data.seed_info, k, 'index')}>{seedShort(data.seed_info, k, 'index')}</span> : NUM(iv)}</td>
                            <td className={`pr-4 py-0.5 text-right ${diff == null ? 'text-gray-600' : diff >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                              {diff == null ? '—' : `${diff >= 0 ? '+' : ''}${NUM(diff, 1)}`}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          );
        })()}
        {data?.meta?.note && <div className="text-[11px] text-amber-300/80">{data.meta.note}</div>}
      </div>

      {data && !data.quality?.ok && !!(data.quality?.warnings || []).length && (
        <div className="text-xs text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2 space-y-1">
          <div className="font-semibold">Data quality warning — these candles look suspect</div>
          {data.quality.warnings.map((w, i) => <div key={i} className="text-amber-300/85">• {w}</div>)}
        </div>
      )}

      {data && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-3">
          <div className="flex items-center justify-between mb-2 px-1">
            <div className="text-sm font-semibold text-gray-200">
              NIFTY {data.meta?.source === 'futures' ? 'FUTURES' : 'INDEX'} · {data.timeframe} · {data.date}
              <span className="ml-2 text-[10px] font-normal text-gray-500">
                {data.meta?.source === 'futures'
                  ? 'candles & VWAP from futures · strikes anchored to index spot'
                  : 'index candles · HLC3 average (no volume)'}
              </span>
            </div>
            <div className="text-[11px] text-gray-500">{(data.signals || []).length} rule hit(s) · {data.candles.length} candles</div>
          </div>
          <CandleChart candles={data.candles} overlays={overlays} markers={markers} />
        </div>
      )}

      {data && showIndex && !!(data.index_candles || []).length && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-3">
          <div className="flex items-center justify-between mb-2 px-1">
            <div className="text-sm font-semibold text-gray-200">
              NIFTY INDEX · {data.timeframe} · {data.date}
              <span className="ml-2 text-[10px] font-normal text-gray-500">
                spot — strikes are struck here · HLC3 average (index has no volume)
              </span>
            </div>
            {data.basis != null && (
              <div className="text-[11px] text-gray-400">
                Basis <strong className={data.basis >= 0 ? 'text-emerald-400' : 'text-red-400'}>
                  {data.basis >= 0 ? '+' : ''}{NUM(data.basis, 1)}
                </strong> <span className="text-gray-600">(futures − index)</span>
              </div>
            )}
          </div>
          <CandleChart candles={data.index_candles} overlays={idxOverlays} markers={markers} height={240} />
        </div>
      )}

      {data && !!(data.signals || []).length && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
          <div className="px-3 py-2 border-b border-surface-3 text-sm font-semibold text-gray-200">Rule hits on this day</div>
          <div className="overflow-x-auto"><table className="w-full text-xs whitespace-nowrap">
            <thead className="bg-surface-3 text-gray-300"><tr>{['Time', 'Rule', 'NIFTY', 'VWAP level', 'Action'].map((h, i) => <th key={h} className={`px-2.5 py-2 font-semibold ${i < 2 ? 'text-left' : 'text-right'}`}>{h}</th>)}</tr></thead>
            <tbody>{data.signals.map((s, i) => (
              <tr key={i} className="border-t border-surface-3/40">
                <td className="px-2.5 py-1.5 text-left text-gray-300">{s.time}</td>
                <td className="px-2.5 py-1.5 text-left text-gray-400">{s.rule}</td>
                <td className="px-2.5 py-1.5 text-right text-gray-200">{NUM(s.index_price)}</td>
                <td className="px-2.5 py-1.5 text-right text-gray-300">{NUM(s.level)}</td>
                <td className={`px-2.5 py-1.5 text-right font-semibold ${s.action === 'BUY_CE' ? 'text-emerald-400' : 'text-red-400'}`}>{s.action === 'BUY_CE' ? 'BUY CALL' : 'BUY PUT'}</td>
              </tr>
            ))}</tbody>
          </table></div>
        </div>
      )}
      <Disclaimer />
    </div>
  );
}

function BacktestTab({ cfg, setCfg, meta, showErr, flash, saveCfg }) {
  const [start, setStart] = useState(''); const [end, setEnd] = useState('');
  const [data, setData] = useState(null); const [loading, setLoading] = useState(false);
  const [ladder, setLadder] = useState(null);
  const [showSkips, setShowSkips] = useState(false);
  const abortRef = useRef(null);
  const patch = (k, v) => setCfg((c) => ({ ...c, [k]: v }));

  useEffect(() => { const t = new Date().toISOString().slice(0, 10); setStart(t); setEnd(t); }, []);
  useEffect(() => { api.voLadder({ overrides: cfg }).then((r) => { if (r.status === 'ok') setLadder(r); }).catch(() => {}); }, []); // eslint-disable-line

  const run = async () => {
    const ac = new AbortController(); abortRef.current = ac; setLoading(true);
    try {
      const r = await api.voBacktest({ start, end, overrides: cfg }, ac.signal);
      if (r.status === 'ok') setData(r); else showErr(r.message);
    } catch (e) { if (e.name !== 'AbortError') showErr(e.message); } finally { setLoading(false); }
  };

  const exportCSV = () => {
    const rows = data?.trades || [];
    if (!rows.length) return;
    const cols = ['date', 'signal_time', 'rule', 'index_price', 'vwap_level', 'opt_type', 'strike', 'symbol', 'expiry', 'premium_source', 'entry_time', 'qty', 'entry', 'target', 'sl', 'exit', 'exit_time', 'exit_reason', 'gross_mtm', 'cost', 'mtm', 'mfe', 'mae'];
    const esc = (v) => { const x = v == null ? '' : String(v); return /[",\n]/.test(x) ? `"${x.replace(/"/g, '""')}"` : x; };
    const lines = [cols.join(',')].concat(rows.map((t) => cols.map((c) => esc(t[c])).join(',')));
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' }));
    a.download = `vwap_options_backtest_${start}_${end}.csv`; a.click();
  };

  const s = data?.stats;
  const num = (k, step = 1) => <input type="number" step={step} value={cfg[k] ?? ''} onChange={(e) => patch(k, e.target.value === '' ? '' : Number(e.target.value))} className={`w-full ${sel}`} />;

  return (
    <div className="space-y-4">
      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
        <RuleBuilder cfg={cfg} setCfg={setCfg} meta={meta} />
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 pt-2 border-t border-surface-3">
          <div><label className={lbl}>Timeframe</label>
            <select value={cfg.timeframe} onChange={(e) => patch('timeframe', e.target.value)} className={`w-full ${sel}`}>
              {(meta.timeframes || []).map((t) => <option key={t} value={t}>{t}</option>)}
            </select></div>
          <div><label className={lbl}>VWAP source</label>
            <select value={cfg.vwap_source} onChange={(e) => patch('vwap_source', e.target.value)} className={`w-full ${sel}`}>
              <option value="futures">Futures (true VWAP)</option>
              <option value="index">Index (HLC3 avg)</option>
            </select></div>
          <div><label className={lbl}>Expiry</label>
            <select value={cfg.expiry_type} onChange={(e) => patch('expiry_type', e.target.value)} className={`w-full ${sel}`}>
              <option value="weekly">Weekly</option><option value="monthly">Monthly</option>
            </select></div>
          <StrikePicker cfg={cfg} patch={patch} ladder={ladder} />
          <div><label className={lbl}>Lots</label>{num('lots')}</div>
          <div><label className={lbl}>Touch buffer (pts)</label>{num('touch_buffer_pts', 0.5)}</div>
          <div><label className={lbl}>Target {cfg.target_mode === 'points' ? '(pts)' : '%'}</label>{num('target_value', 1)}</div>
          <div><label className={lbl}>Target by</label>
            <select value={cfg.target_mode} onChange={(e) => patch('target_mode', e.target.value)} className={`w-full ${sel}`}><option value="percent">Percent</option><option value="points">Points</option></select></div>
          <div><label className={lbl}>SL {cfg.sl_mode === 'points' ? '(pts)' : '%'}</label>{num('sl_value', 1)}</div>
          <div><label className={lbl}>SL by</label>
            <select value={cfg.sl_mode} onChange={(e) => patch('sl_mode', e.target.value)} className={`w-full ${sel}`}><option value="percent">Percent</option><option value="points">Points</option></select></div>
          <div><label className={lbl}>Entry from</label><input value={cfg.entry_start} onChange={(e) => patch('entry_start', e.target.value)} className={`w-full ${sel}`} /></div>
          <div><label className={lbl}>Entry cutoff</label><input value={cfg.entry_cutoff} onChange={(e) => patch('entry_cutoff', e.target.value)} className={`w-full ${sel}`} /></div>
        </div>
        <div className="flex flex-wrap items-end gap-3 pt-2 border-t border-surface-3">
          <div><label className={lbl}>From</label><input type="date" value={start} onChange={(e) => setStart(e.target.value)} className={sel} /></div>
          <div><label className={lbl}>To</label><input type="date" value={end} onChange={(e) => setEnd(e.target.value)} className={sel} /></div>
          <button onClick={run} disabled={loading} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />} Run Backtest
          </button>
          {loading && <button onClick={() => abortRef.current?.abort()} className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg bg-red-600/80 hover:bg-red-600 text-white font-semibold"><X className="w-4 h-4" /> Cancel</button>}
          <button onClick={saveCfg} className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white"><Save className="w-3.5 h-3.5" /> Save as default</button>
          <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer"><input type="checkbox" checked={!!cfg.apply_costs} onChange={(e) => patch('apply_costs', e.target.checked)} className="accent-brand-500" /> Net of costs</label>
          <label className="flex items-center gap-2 text-xs text-gray-400 cursor-pointer" title="When a contract has expired, price it with Black-Scholes instead of skipping the signal."><input type="checkbox" checked={!!cfg.allow_modelled} onChange={(e) => patch('allow_modelled', e.target.checked)} className="accent-brand-500" /> Allow modelled premiums</label>
        </div>
      </div>

      {s && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
            {[['Trades', s.trades, 'text-gray-100'], ['Win%', `${s.win_rate}%`, 'text-emerald-400'],
              ['Net P&L', `₹${NUM(s.total_mtm, 0)}`, s.total_mtm >= 0 ? 'text-emerald-400' : 'text-red-400'],
              ['Avg/trade', `₹${NUM(s.avg, 0)}`, s.avg >= 0 ? 'text-emerald-400' : 'text-red-400'],
              ['Profit factor', s.profit_factor, 'text-sky-300'], ['Expectancy', `₹${NUM(s.expectancy, 0)}`, s.expectancy >= 0 ? 'text-emerald-400' : 'text-red-400'],
              ['Best', `₹${NUM(s.best, 0)}`, 'text-emerald-400'], ['Worst', `₹${NUM(s.worst, 0)}`, 'text-red-400'],
              ['Max DD', `₹${NUM(s.max_drawdown, 0)}`, 'text-red-400'], ['Costs', `₹${NUM(s.total_cost, 0)}`, 'text-gray-300'],
              ['Sessions', data.sessions, 'text-gray-300'], ['Skipped', (data.skipped || []).length, 'text-amber-400']].map(([k, v, c]) => (
              <div key={k} className="bg-surface-2 border border-surface-3 rounded-xl px-3 py-2"><div className="text-[10px] uppercase tracking-wide text-gray-500">{k}</div><div className={`text-base font-bold ${c}`}>{v}</div></div>
            ))}
          </div>

          {/* REAL vs MODELLED split — the honesty panel */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {['REAL', 'MODELLED'].map((k) => {
              const b = data.by_source[k];
              return (
                <div key={k} className={`rounded-xl border px-4 py-3 ${k === 'REAL' ? 'border-emerald-500/30 bg-emerald-500/5' : 'border-amber-500/30 bg-amber-500/5'}`}>
                  <div className="flex items-center justify-between mb-1">
                    <span className={`text-xs font-bold px-2 py-0.5 rounded-full border ${srcBadge(k)}`}>{k} PREMIUMS</span>
                    <span className="text-xs text-gray-400">{b.trades} trades</span>
                  </div>
                  <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-gray-400">
                    <span>Win% <strong className="text-gray-200">{b.win_rate}%</strong></span>
                    <span>Net <strong className={b.total_mtm >= 0 ? 'text-emerald-400' : 'text-red-400'}>₹{NUM(b.total_mtm, 0)}</strong></span>
                    <span>PF <strong className="text-gray-200">{b.profit_factor}</strong></span>
                    <span>Expectancy <strong className="text-gray-200">₹{NUM(b.expectancy, 0)}</strong></span>
                  </div>
                  {k === 'MODELLED' && b.trades > 0 && (
                    <div className="text-[10px] text-amber-300/80 mt-1">Black-Scholes estimates for expired contracts — no volatility smile. Treat separately from REAL results.</div>
                  )}
                </div>
              );
            })}
          </div>

          <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
            <div className="px-3 py-2 border-b border-surface-3 flex items-center justify-between">
              <span className="text-sm font-semibold text-gray-200">Trade log ({data.trades.length})</span>
              <div className="flex items-center gap-2">
                <label className="flex items-center gap-1.5 text-xs text-gray-400 cursor-pointer"><input type="checkbox" checked={showSkips} onChange={(e) => setShowSkips(e.target.checked)} className="accent-brand-500" /> Show skipped ({(data.skipped || []).length})</label>
                <button onClick={exportCSV} disabled={!data.trades.length} className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-40"><Download className="w-3.5 h-3.5" /> CSV</button>
              </div>
            </div>
            <div className="overflow-x-auto max-h-[520px]"><table className="w-full text-xs whitespace-nowrap">
              <thead className="bg-surface-3 text-gray-300 sticky top-0"><tr>{['Date', 'Signal', 'Rule', 'Index', 'Signal px', 'Level', 'Opt', 'Strike', 'Src', 'Entry@', 'Qty', 'Entry', 'Target', 'SL', 'Exit', 'Exit@', 'Reason', 'Net P&L', 'MFE', 'MAE'].map((h, i) => <th key={h} className={`px-2.5 py-1.5 font-semibold ${i < 3 ? 'text-left' : 'text-right'}`}>{h}</th>)}</tr></thead>
              <tbody>
                {data.trades.map((t, i) => (
                  <tr key={i} className="border-t border-surface-3/40 hover:bg-surface-3/20">
                    <td className="px-2.5 py-1 text-left text-gray-400">{t.date}</td>
                    <td className="px-2.5 py-1 text-left text-gray-300">{t.signal_time}</td>
                    <td className="px-2.5 py-1 text-left text-gray-500 max-w-[200px] truncate" title={t.rule}>{t.rule}</td>
                    <td className="px-2.5 py-1 text-right text-gray-300">{NUM(t.index_price, 0)}</td>
                    <td className="px-2.5 py-1 text-right text-gray-400" title={t.basis ? `basis ${t.basis > 0 ? '+' : ''}${t.basis}` : ''}>{NUM(t.signal_price, 0)}</td>
                    <td className="px-2.5 py-1 text-right text-gray-400">{NUM(t.vwap_level, 0)}</td>
                    <td className={`px-2.5 py-1 text-right font-semibold ${t.opt_type === 'CE' ? 'text-emerald-400' : 'text-red-400'}`}>{t.opt_type}</td>
                    <td className="px-2.5 py-1 text-right text-gray-300">{INT(t.strike)}</td>
                    <td className="px-2.5 py-1 text-right"><span className={`text-[9px] px-1.5 py-0.5 rounded border ${srcBadge(t.premium_source)}`}>{t.premium_source}</span></td>
                    <td className="px-2.5 py-1 text-right text-gray-500">{t.entry_time}</td>
                    <td className="px-2.5 py-1 text-right text-gray-400">{INT(t.qty)}</td>
                    <td className="px-2.5 py-1 text-right text-gray-200">₹{NUM(t.entry)}</td>
                    <td className="px-2.5 py-1 text-right text-emerald-400">₹{NUM(t.target)}</td>
                    <td className="px-2.5 py-1 text-right text-red-400">₹{NUM(t.sl)}</td>
                    <td className="px-2.5 py-1 text-right text-gray-300">{t.exit == null ? '—' : `₹${NUM(t.exit)}`}</td>
                    <td className="px-2.5 py-1 text-right text-gray-500">{t.exit_time || '—'}</td>
                    <td className={`px-2.5 py-1 text-right font-semibold ${stColor(t.exit_reason)}`}>{t.exit_reason}</td>
                    <td className={`px-2.5 py-1 text-right font-semibold ${pc(t.mtm)}`}>{NUM(t.mtm, 0)}</td>
                    <td className="px-2.5 py-1 text-right text-emerald-400">{NUM(t.mfe, 0)}</td>
                    <td className="px-2.5 py-1 text-right text-red-400">{NUM(t.mae, 0)}</td>
                  </tr>
                ))}
                {showSkips && (data.skipped || []).map((k, i) => (
                  <tr key={`sk${i}`} className="border-t border-surface-3/40 bg-surface-3/10 text-gray-500">
                    <td className="px-2.5 py-1 text-left">{k.date}</td>
                    <td className="px-2.5 py-1 text-left">{k.signal_time}</td>
                    <td className="px-2.5 py-1 text-left truncate max-w-[200px]">{k.rule}</td>
                    <td className="px-2.5 py-1 text-right">{NUM(k.index_price, 0)}</td>
                    <td className="px-2.5 py-1 text-right">—</td>
                    <td className="px-2.5 py-1 text-right">{NUM(k.vwap_level, 0)}</td>
                    <td className="px-2.5 py-1 text-left text-amber-400/80 italic" colSpan={15}>SKIPPED — {k.reason}</td>
                  </tr>
                ))}
                {!data.trades.length && !showSkips && <tr><td colSpan={20} className="px-4 py-8 text-center text-gray-500">No trades. {(data.skipped || []).length > 0 && 'Tick “Show skipped” to see why signals produced no trade.'}</td></tr>}
              </tbody>
            </table></div>
          </div>
          {data.meta?.note && <div className="text-[11px] text-gray-500">{data.meta.note}</div>}
        </>
      )}
      <Disclaimer />
    </div>
  );
}

function PositionsTab({ cfg, patch, saveCfg, showErr, flash }) {
  const [status, setStatus] = useState(null);
  const [rows, setRows] = useState([]);
  const pollRef = useRef(null);
  const load = useCallback(async () => {
    try {
      const [st, ps] = await Promise.all([api.voStatus(), api.voPositions()]);
      if (st.status === 'ok') setStatus(st);
      if (ps.status === 'ok') setRows(ps.positions || []);
    } catch { /* transient */ }
  }, []);
  useEffect(() => { load(); pollRef.current = setInterval(load, 5000); return () => clearInterval(pollRef.current); }, [load]);

  const start = async () => { const r = await api.voStart(cfg); if (r.status === 'ok') { setStatus(r); flash('Strategy started'); load(); } else showErr(r.message); };
  const stop = async () => { const r = await api.voStop(); if (r.status === 'ok') { setStatus(r); flash('Stopped'); } else showErr(r.message); };

  const totals = rows.reduce((a, p) => {
    a.mtm += p.mtm || 0; a.mfe += p.mfe || 0; a.mae += p.mae || 0;
    if (p.status === 'OPEN') a.open += 1; else { a.closed += 1; a.realized += p.mtm || 0; }
    return a;
  }, { mtm: 0, mfe: 0, mae: 0, open: 0, closed: 0, realized: 0 });

  return (
    <div className="space-y-4">
      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer"><input type="checkbox" checked={!!cfg.paper_trade} onChange={(e) => patch('paper_trade', e.target.checked)} className="accent-brand-500" /> Paper mode</label>
          <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer"><input type="checkbox" checked={!!cfg.auto_start} onChange={(e) => patch('auto_start', e.target.checked)} className="accent-brand-500" /> Auto-start on login</label>
          {status && <span className="text-xs text-gray-500">Open {status.open_positions}/{status.max_positions} · engine v{status.engine_version}</span>}
          <div className="ml-auto flex items-center gap-2">
            {status?.is_active
              ? <button onClick={stop} className="px-3 py-1.5 text-sm rounded-lg bg-red-600/80 hover:bg-red-600 text-white font-semibold">Stop</button>
              : <button onClick={start} className="px-3 py-1.5 text-sm rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white font-semibold">Start</button>}
            <button onClick={saveCfg} className="px-3 py-1.5 text-sm rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white flex items-center gap-1"><Save className="w-3.5 h-3.5" /> Save</button>
            <button onClick={load} className="text-gray-400 hover:text-white"><RefreshCw className="w-4 h-4" /></button>
          </div>
        </div>
        {!cfg.paper_trade && <p className="text-[11px] text-amber-400">⚠ Live mode also needs the global gate (PAPER_TRADE=False + TRADING_ENABLED=True). Orders are NRML on NFO.</p>}
        {status && <div className={`flex items-center gap-1.5 text-xs ${status.is_active ? 'text-emerald-400' : 'text-gray-500'}`}><Radio className={`w-4 h-4 ${status.is_active ? 'animate-pulse' : ''}`} /> {status.is_active ? 'RUNNING' : 'STOPPED'} · {status.paper_trade ? 'Paper' : 'REAL'}</div>}
      </div>

      {rows.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm bg-surface-2 border border-surface-3 rounded-xl px-4 py-2.5">
          <span className="text-gray-400">Positions <strong className="text-gray-100">{rows.length}</strong> <span className="text-gray-600">({totals.open} open · {totals.closed} closed)</span></span>
          <span className="text-gray-400">Total MTM <strong className={pc(totals.mtm)}>₹{NUM(totals.mtm, 0)}</strong></span>
          <span className="text-gray-400">Realized <strong className={pc(totals.realized)}>₹{NUM(totals.realized, 0)}</strong></span>
          <span className="text-gray-400">Σ MFE <strong className="text-emerald-400">₹{NUM(totals.mfe, 0)}</strong></span>
          <span className="text-gray-400">Σ MAE <strong className="text-red-400">₹{NUM(totals.mae, 0)}</strong></span>
        </div>
      )}

      <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
        <div className="px-3 py-2 border-b border-surface-3 text-sm font-semibold text-gray-200">Positions (today) <span className="text-gray-500">({rows.length})</span></div>
        {!rows.length ? <div className="px-4 py-8 text-center text-gray-500 text-sm">No positions yet — the engine opens them when a rule fires while running.</div> : (
          <div className="overflow-x-auto"><table className="w-full text-xs whitespace-nowrap">
            <thead className="bg-surface-3 text-gray-300"><tr>{['Rule', 'Symbol', 'Opt', 'Strike', 'Entry@', 'Qty', 'Entry', 'Target', 'SL', 'LTP', 'MTM', 'MFE', 'MAE', 'Status'].map((h, i) => <th key={h} className={`px-2.5 py-2 font-semibold ${i < 2 ? 'text-left' : 'text-right'}`}>{h}</th>)}</tr></thead>
            <tbody>{rows.map((p) => (
              <tr key={p.id} className="border-t border-surface-3/40">
                <td className="px-2.5 py-1.5 text-left text-gray-500 max-w-[180px] truncate" title={p.rule}>{p.rule}</td>
                <td className="px-2.5 py-1.5 text-left text-brand-300 font-semibold">{p.symbol}{p.paper && <span className="ml-1 text-[9px] px-1 rounded bg-surface-3 text-gray-500 border border-surface-4">paper</span>}</td>
                <td className={`px-2.5 py-1.5 text-right font-semibold ${p.opt_type === 'CE' ? 'text-emerald-400' : 'text-red-400'}`}>{p.opt_type}</td>
                <td className="px-2.5 py-1.5 text-right text-gray-300">{INT(p.strike)}</td>
                <td className="px-2.5 py-1.5 text-right text-gray-500">{p.entry_time}</td>
                <td className="px-2.5 py-1.5 text-right text-gray-400">{INT(p.qty)}</td>
                <td className="px-2.5 py-1.5 text-right text-gray-200">₹{NUM(p.entry_price)}</td>
                <td className="px-2.5 py-1.5 text-right text-emerald-400">₹{NUM(p.target)}</td>
                <td className="px-2.5 py-1.5 text-right text-red-400">₹{NUM(p.sl)}</td>
                <td className="px-2.5 py-1.5 text-right text-gray-200">₹{NUM(p.ltp)}</td>
                <td className={`px-2.5 py-1.5 text-right font-semibold ${pc(p.mtm)}`}>{NUM(p.mtm, 0)}</td>
                <td className="px-2.5 py-1.5 text-right text-emerald-400">{NUM(p.mfe, 0)}</td>
                <td className="px-2.5 py-1.5 text-right text-red-400">{NUM(p.mae, 0)}</td>
                <td className={`px-2.5 py-1.5 text-right font-semibold ${stColor(p.status)}`}>{p.status}</td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </div>
      <Disclaimer />
    </div>
  );
}

function SettingsTab({ cfg, patch, setCfg, meta, saveCfg }) {
  const num = (k, step = 1) => <input type="number" step={step} value={cfg[k] ?? ''} onChange={(e) => patch(k, e.target.value === '' ? '' : Number(e.target.value))} className={`w-full ${sel}`} />;
  return (
    <div className="space-y-4 max-w-5xl">
      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4"><RuleBuilder cfg={cfg} setCfg={setCfg} meta={meta} /></div>

      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
        <h3 className="text-sm font-semibold text-gray-200 mb-2">Data & contract</h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          <div><label className={lbl}>VWAP source</label>
            <select value={cfg.vwap_source} onChange={(e) => patch('vwap_source', e.target.value)} className={`w-full ${sel}`}>
              <option value="futures">NIFTY Futures — true VWAP</option>
              <option value="index">NIFTY Index — HLC3 average</option>
            </select></div>
          <div><label className={lbl}>Timeframe</label>
            <select value={cfg.timeframe} onChange={(e) => patch('timeframe', e.target.value)} className={`w-full ${sel}`}>
              {(meta.timeframes || []).map((t) => <option key={t} value={t}>{t}</option>)}
            </select></div>
          <div><label className={lbl}>Expiry</label>
            <select value={cfg.expiry_type} onChange={(e) => patch('expiry_type', e.target.value)} className={`w-full ${sel}`}><option value="weekly">Weekly</option><option value="monthly">Monthly</option></select></div>
          <div><label className={lbl}>Min days to expiry</label>{num('min_days_to_expiry')}</div>
          <StrikePicker cfg={cfg} patch={patch} ladder={null} />
          <div><label className={lbl}>Lots</label>{num('lots')}</div>
        </div>
        <p className="text-[11px] text-gray-500 mt-2">
          {cfg.vwap_source === 'index'
            ? 'Index mode has no traded volume — the line is an HLC3 average price, not a true VWAP.'
            : 'Futures mode uses real contract volume; monthly contract rolls are stitched automatically.'}
        </p>
      </div>

      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
        <h3 className="text-sm font-semibold text-gray-200 mb-2">Entries, exits & fills</h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          <div><label className={lbl}>Entry from</label><input value={cfg.entry_start} onChange={(e) => patch('entry_start', e.target.value)} className={`w-full ${sel}`} /></div>
          <div><label className={lbl}>Entry cutoff</label><input value={cfg.entry_cutoff} onChange={(e) => patch('entry_cutoff', e.target.value)} className={`w-full ${sel}`} /></div>
          <div><label className={lbl}>Square-off</label><input value={cfg.square_off_time} onChange={(e) => patch('square_off_time', e.target.value)} className={`w-full ${sel}`} /></div>
          <div><label className={lbl}>Fill mode</label>
            <select value={cfg.fill_mode} onChange={(e) => patch('fill_mode', e.target.value)} className={`w-full ${sel}`}>
              <option value="next_bar_open">Next bar open (realistic)</option>
              <option value="signal_bar_close">Signal bar close</option>
            </select></div>
          <div><label className={lbl}>Exit on</label>
            <select value={cfg.exit_on} onChange={(e) => patch('exit_on', e.target.value)} className={`w-full ${sel}`}>
              <option value="premium">Option premium</option><option value="index_points">Index points</option>
            </select></div>
          <div><label className={lbl}>Max trades/day</label>{num('max_trades_per_day')}</div>
          <div><label className={lbl}>Max positions</label>{num('max_positions')}</div>
          <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer self-end"><input type="checkbox" checked={!!cfg.one_signal_per_day} onChange={(e) => patch('one_signal_per_day', e.target.checked)} className="accent-brand-500" /> One signal per rule/day</label>
        </div>
      </div>

      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
        <h3 className="text-sm font-semibold text-gray-200 mb-2">Costs & modelled pricing</h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          <div><label className={lbl}>Slippage (bps)</label>{num('slippage_bps', 1)}</div>
          <div><label className={lbl}>Brokerage/order ₹</label>{num('brokerage_per_order', 1)}</div>
          <div><label className={lbl}>Charges %</label>{num('charges_pct', 0.01)}</div>
          <div><label className={lbl}>IV source</label>
            <select value={cfg.iv_source} onChange={(e) => patch('iv_source', e.target.value)} className={`w-full ${sel}`}><option value="vix">India VIX</option><option value="fixed">Fixed</option></select></div>
          <div><label className={lbl}>Fixed IV %</label>{num('iv_fixed_pct', 0.5)}</div>
          <div><label className={lbl}>Risk-free %</label>{num('risk_free_pct', 0.1)}</div>
          <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer self-end"><input type="checkbox" checked={!!cfg.apply_costs} onChange={(e) => patch('apply_costs', e.target.checked)} className="accent-brand-500" /> Apply costs</label>
          <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer self-end"><input type="checkbox" checked={!!cfg.allow_modelled} onChange={(e) => patch('allow_modelled', e.target.checked)} className="accent-brand-500" /> Allow modelled premiums</label>
        </div>
      </div>

      <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
        <h3 className="text-sm font-semibold text-gray-200 mb-2">Alerts</h3>
        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer"><input type="checkbox" checked={!!cfg.telegram_alerts} onChange={(e) => patch('telegram_alerts', e.target.checked)} className="accent-brand-500" /> Telegram alerts</label>
          <div className="flex items-center gap-2 text-xs text-gray-400">Bot
            <select value={cfg.telegram_bot} onChange={(e) => patch('telegram_bot', e.target.value)} className={`${sel} py-1`}><option value="a">Bot A</option><option value="b">Bot B</option></select>
          </div>
          <span className="text-[11px] text-gray-600">Configure bots in Settings → Telegram.</span>
        </div>
      </div>

      <button onClick={saveCfg} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold"><Save className="w-4 h-4" /> Save Settings</button>
      <Disclaimer />
    </div>
  );
}

function InfoTab() {
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-5 space-y-3 text-sm text-gray-300 max-w-3xl">
      <h3 className="font-semibold text-gray-100">How the VWAP Options Engine works</h3>
      <p>Every VWAP flavour is pre-computed on the chosen underlying path, then your rules decide what to trade:</p>
      <ul className="list-disc pl-5 space-y-1 text-gray-400">
        <li><strong>Lines</strong> — current-day, previous-day, previous-week, previous-month, rolling 15-day and 90-day VWAP.</li>
        <li><strong>Triggers</strong> — touch (within a points buffer), cross up, cross down, or either.</li>
        <li><strong>Action</strong> — buy an ATM-relative CE or PE at the chosen offset and expiry.</li>
        <li><strong>Exits</strong> — target/SL in % or points on the premium (or on index points), plus a square-off time.</li>
      </ul>
      <p>The <strong>same</strong> signal and simulation code runs the chart, the backtest and the live/paper engine — so what you test is what runs.</p>
      <h4 className="font-semibold text-gray-100 pt-2">Data honesty</h4>
      <ul className="list-disc pl-5 space-y-1 text-gray-400">
        <li><strong>Index mode is not a true VWAP.</strong> The NIFTY index has no traded volume, so that line is an HLC3 average. Futures mode uses real volume.</li>
        <li><strong>REAL vs MODELLED premiums.</strong> Kite lists only currently tradable contracts, so expired options have no price history. Those trades are priced with Black-Scholes (India VIX as IV) and clearly flagged; stats are reported split by source. Black-Scholes ignores the volatility smile, so deep ITM/OTM strikes are the least reliable.</li>
        <li><strong>Skipped signals are logged</strong> with the reason, so nothing disappears silently.</li>
        <li><strong>No look-ahead.</strong> Every value at a bar uses only that bar and earlier; fills default to the next bar's open.</li>
      </ul>
      <Disclaimer />
    </div>
  );
}

function Disclaimer() {
  return (
    <div className="text-[11px] text-gray-500 bg-surface-2/60 border border-surface-3 rounded-lg px-3 py-2 flex items-start gap-2">
      <Info className="w-4 h-4 mt-0.5 shrink-0 text-amber-400" />
      <span><strong className="text-amber-300">Paper by default — research output, not investment advice.</strong> Backtested results include modelled premiums where real option history is unavailable; past performance does not guarantee future results.</span>
    </div>
  );
}
