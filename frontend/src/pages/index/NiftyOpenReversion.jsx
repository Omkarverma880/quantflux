import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Target, Play, Loader2, AlertCircle, Upload, Download, Save, RefreshCw, Radio,
  Check, X, FlaskConical, Wallet, FileText, SlidersHorizontal, TrendingUp, TrendingDown,
} from 'lucide-react';
import { api } from '../../api';

const sel = 'bg-surface-3 border border-surface-4 rounded-lg px-2.5 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';
const lbl = 'block text-[10px] text-gray-500 uppercase tracking-wide mb-1';
const NUM = (v, d = 2) => (v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
const INR = (v, d = 0) => (v == null ? '—' : `₹${Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d })}`);
const PCT = (v) => (v == null ? '—' : `${Number(v).toFixed(2)}%`);
const ARTEFACTS = ['trade_log.csv', 'daily_summary.csv', 'monthly_summary.csv', 'yearly_summary.csv',
  'equity_curve.png', 'return_curve.png', 'drawdown_curve.png', 'lot_scaling.png',
  'trade_pnl_distribution.png', 'final_report.txt'];

function Stat({ label, value, tone = '' }) {
  return (
    <div className="bg-surface-3/40 border border-surface-3 rounded-lg px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-gray-500">{label}</div>
      <div className={`text-sm font-semibold ${tone || 'text-gray-100'}`}>{value}</div>
    </div>
  );
}

function EquityChart({ curve }) {
  if (!curve?.length) return null;
  const w = 1000, h = 260, padL = 8, padR = 74, padT = 12, padB = 22;
  const plotW = w - padL - padR, plotH = h - padT - padB;
  const eq = curve.map((c) => c.equity);
  let lo = Math.min(...eq), hi = Math.max(...eq);
  const pad = (hi - lo) * 0.08 || 1; lo -= pad; hi += pad;
  const x = (i) => padL + (i / Math.max(1, curve.length - 1)) * plotW;
  const y = (v) => padT + ((hi - v) / (hi - lo)) * plotH;
  const d = curve.map((c, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(c.equity).toFixed(1)}`).join(' ');
  const marks = [];
  for (let i = 1; i < curve.length; i += 1) {
    if (curve[i].lots !== curve[i - 1].lots) marks.push({ i, lots: curve[i].lots, date: curve[i].date });
  }
  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full" style={{ minWidth: 640 }}>
        <rect x={0} y={0} width={w} height={h} fill="#0b1220" rx="8" />
        {[0, 0.25, 0.5, 0.75, 1].map((f) => {
          const v = lo + (hi - lo) * f;
          return (
            <g key={f}>
              <line x1={padL} x2={padL + plotW} y1={y(v)} y2={y(v)} stroke="rgba(148,163,184,0.12)" />
              <text x={padL + plotW + 6} y={y(v) + 3} fontSize="9" fill="#64748b">{Math.round(v).toLocaleString('en-IN')}</text>
            </g>
          );
        })}
        <path d={d} fill="none" stroke="#38bdf8" strokeWidth="1.6" />
        {marks.map((m) => (
          <g key={m.i}>
            <line x1={x(m.i)} x2={x(m.i)} y1={padT} y2={padT + plotH} stroke="#f59e0b" strokeDasharray="3 3" opacity="0.8" />
            <text x={x(m.i) + 3} y={padT + 12} fontSize="9" fill="#f59e0b">{m.lots} lots</text>
          </g>
        ))}
        <text x={padL} y={h - 6} fontSize="9" fill="#64748b">{curve[0].date}</text>
        <text x={padL + plotW} y={h - 6} fontSize="9" fill="#64748b" textAnchor="end">{curve[curve.length - 1].date}</text>
      </svg>
    </div>
  );
}

export default function NiftyOpenReversion() {
  const [meta, setMeta] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [tab, setTab] = useState('backtest');
  const [res, setRes] = useState(null);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState(''); const [msg, setMsg] = useState('');
  const [status, setStatus] = useState(null);
  const [positions, setPositions] = useState([]);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef(null);
  const pollRef = useRef(null);

  const showErr = (m) => { setErr(m); setTimeout(() => setErr(''), 8000); };
  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(''), 3000); };
  const patch = (k, v) => setCfg((c) => ({ ...c, [k]: v }));
  const patchCost = (k, v) => setCfg((c) => ({ ...c, costs: { ...(c.costs || {}), [k]: v } }));

  useEffect(() => {
    api.norMeta().then((r) => { if (r.status === 'ok') { setMeta(r); setCfg(r.config); } })
      .catch(() => showErr('Could not load the strategy'));
    api.norLast().then((r) => { if (r?.status === 'ok') setRes(r); }).catch(() => {});
  }, []);

  const run = useCallback(async () => {
    setRunning(true); setErr('');
    try {
      const r = await api.norBacktest(cfg, true);
      if (r.status === 'ok') { setRes(r); flash(`Backtest done — ${r.trade_count} trades`); }
      else showErr(r.message || 'Backtest failed');
    } catch (e) { showErr(e.message); } finally { setRunning(false); }
  }, [cfg]);

  const upload = async (file) => {
    if (!file) return;
    setUploading(true); setErr('');
    try {
      const r = await api.norUpload(file);
      if (r.status === 'ok') {
        patch('csv_path', r.path);
        setMeta((m) => ({ ...m, datasets: [{ name: r.name, path: r.path, size: r.size }, ...(m?.datasets || [])] }));
        flash(`${r.name} uploaded (${(r.size / 1e6).toFixed(1)} MB)`);
      } else showErr(r.message);
    } catch (e) { showErr(e.message); } finally { setUploading(false); }
  };

  const download = async (name) => {
    try {
      const blob = await api.norFile(name);
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = name; a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) { showErr(`${name}: ${e.message}`); }
  };

  const loadLive = useCallback(async () => {
    try {
      const [s, p] = await Promise.all([api.norStatus(), api.norPositions()]);
      if (s.status === 'ok') setStatus(s);
      if (p.status === 'ok') setPositions(p.positions || []);
    } catch { /* */ }
  }, []);
  useEffect(() => {
    if (tab !== 'live') return undefined;
    loadLive();
    pollRef.current = setInterval(loadLive, 5000);
    return () => clearInterval(pollRef.current);
  }, [tab, loadLive]);

  const startLive = async () => {
    const r = await api.norStart(cfg);
    if (r.status === 'ok') { setStatus(r); flash('Engine started'); } else showErr(r.message);
  };
  const stopLive = async () => {
    const r = await api.norStop();
    if (r.status === 'ok') { setStatus(r); flash('Stopped'); } else showErr(r.message);
  };
  const saveCfg = async () => {
    const r = await api.norConfigSave(cfg);
    if (r.status === 'ok') { setCfg(r.config); flash('Defaults saved'); } else showErr(r.message);
  };

  if (!cfg || !meta) return <div className="p-6 text-gray-500 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>;
  const num = (k, step = 1, min = 0) => (
    <input type="number" step={step} min={min} value={cfg[k] ?? ''} onChange={(e) => patch(k, e.target.value)} className={`w-full ${sel}`} />
  );
  const s = res?.summary;
  const unit = cfg.level_mode === 'percent' ? '%' : 'pts';

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-[1700px] mx-auto">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Target className="w-6 h-6 text-brand-400" />
            <h1 className="text-xl font-bold text-gray-100">NIFTY Open ±{cfg.entry_offset}{unit === '%' ? '%' : ''} Mean Reversion</h1>
            <span className="text-[11px] px-2 py-0.5 rounded-full bg-brand-500/15 text-brand-300 border border-brand-500/25">Index Strategy</span>
          </div>
          <p className="text-sm text-gray-500 mt-0.5">
            BUY at open −{cfg.entry_offset}, SELL at open +{cfg.entry_offset}, {cfg.stop_loss} stop / {cfg.target} target,
            entries until {cfg.entry_cutoff}, flat by {cfg.market_close}. Progressive lot scaling on realised profit.
          </p>
        </div>
        {status && (
          <div className={`flex items-center gap-1.5 text-xs ${status.is_active ? 'text-emerald-400' : 'text-gray-500'}`}>
            <Radio className={`w-4 h-4 ${status.is_active ? 'animate-pulse' : ''}`} />
            {status.is_active ? 'LIVE ON' : 'LIVE OFF'} · {status.paper_trade ? 'Paper' : 'REAL'}
          </div>
        )}
      </div>

      <div className="flex gap-1 border-b border-surface-3">
        {[['backtest', 'Backtest', FlaskConical], ['live', 'Live / Paper', Wallet], ['report', 'Report', FileText]].map(([id, label, Icon]) => (
          <button key={id} onClick={() => setTab(id)} className={`flex items-center gap-1.5 px-4 py-2 text-sm font-semibold border-b-2 -mb-px transition ${tab === id ? 'border-brand-500 text-brand-400' : 'border-transparent text-gray-400 hover:text-gray-200'}`}>
            <Icon className="w-4 h-4" /> {label}
          </button>
        ))}
      </div>

      {err && <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-red-400 text-sm"><AlertCircle className="w-4 h-4" /> {err}</div>}
      {msg && <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-4 py-2 text-emerald-400 text-sm">{msg}</div>}

      {/* ── parameters, shared by both tabs ── */}
      {tab !== 'report' && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
          {tab === 'backtest' && (
            <div className="flex flex-wrap items-end gap-2.5 pb-3 border-b border-surface-3">
              <div className="flex-1 min-w-[280px]">
                <label className={lbl}>Data — 1-minute OHLC</label>
                <div className="flex gap-2">
                  <select value={cfg.csv_path || ''} onChange={(e) => patch('csv_path', e.target.value)} className={`${sel} flex-1`}>
                    <option value="">Pull from Zerodha (needs a live session)</option>
                    {(meta.datasets || []).map((d) => <option key={d.path} value={d.path}>{d.name} ({(d.size / 1e6).toFixed(1)} MB)</option>)}
                  </select>
                  <input ref={fileRef} type="file" accept=".csv" className="hidden"
                    onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ''; }} />
                  <button onClick={() => fileRef.current?.click()} disabled={uploading}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-50">
                    {uploading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />} Upload CSV
                  </button>
                </div>
              </div>
              <div><label className={lbl}>From</label><input type="date" value={cfg.start_date || ''} onChange={(e) => patch('start_date', e.target.value)} className={sel} /></div>
              <div><label className={lbl}>To</label><input type="date" value={cfg.end_date || ''} onChange={(e) => patch('end_date', e.target.value)} className={sel} /></div>
              <button onClick={run} disabled={running} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">
                {running ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />} Run Backtest
              </button>
              <button onClick={saveCfg} className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white"><Save className="w-3.5 h-3.5" /> Save</button>
            </div>
          )}

          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
            <div><label className={lbl}>Level mode</label>
              <select value={cfg.level_mode} onChange={(e) => patch('level_mode', e.target.value)} className={`w-full ${sel}`}>
                <option value="points">Points</option><option value="percent">Percent of open</option>
              </select>
            </div>
            <div><label className={lbl}>Entry offset ({unit})</label>{num('entry_offset', 5)}</div>
            <div><label className={lbl}>Stop loss ({unit})</label>{num('stop_loss', 5)}</div>
            <div><label className={lbl}>Target ({unit})</label>{num('target', 5)}</div>
            <div><label className={lbl}>Entry from</label><input value={cfg.entry_start} onChange={(e) => patch('entry_start', e.target.value)} className={`w-full ${sel}`} /></div>
            <div><label className={lbl}>Entry cutoff</label><input value={cfg.entry_cutoff} onChange={(e) => patch('entry_cutoff', e.target.value)} className={`w-full ${sel}`} /></div>
            <div><label className={lbl}>Square-off</label><input value={cfg.market_close} onChange={(e) => patch('market_close', e.target.value)} className={`w-full ${sel}`} /></div>

            <div><label className={lbl}>Instrument</label>
              <select value={cfg.instrument_mode} onChange={(e) => patch('instrument_mode', e.target.value)} className={`w-full ${sel}`}>
                {(meta.instrument_modes || []).map((m) => <option key={m.key} value={m.key}>{m.name}</option>)}
              </select>
            </div>
            {cfg.instrument_mode !== 'spot' && (
              <>
                <div><label className={lbl}>Strike from ATM</label>
                  <select value={cfg.strike_offset} onChange={(e) => patch('strike_offset', Number(e.target.value))} className={`w-full ${sel}`}>
                    {[0, 50, 100, 150, 200].map((v) => <option key={v} value={v}>{v === 0 ? 'ATM' : `${v} OTM`}</option>)}
                  </select>
                </div>
                <div><label className={lbl}>Expiry</label>
                  <select value={cfg.expiry_type} onChange={(e) => patch('expiry_type', e.target.value)} className={`w-full ${sel}`}>
                    <option value="weekly">Weekly</option><option value="monthly">Monthly</option>
                  </select>
                </div>
              </>
            )}
            <div><label className={lbl}>Capital ₹</label>{num('starting_capital', 50000)}</div>
            <div><label className={lbl}>Lot size</label>{num('lot_size', 5, 1)}</div>
            <div><label className={lbl}>Base lots</label>{num('base_lots', 1, 1)}</div>
            <div><label className={lbl}>Max lots</label>{num('max_lots', 1, 1)}</div>
            <div><label className={lbl}>Max / side / day</label>{num('max_per_side_per_day', 1, 1)}</div>
            <div><label className={lbl}>Max trades / day</label>{num('max_trades_per_day', 1, 1)}</div>
            <div><label className={lbl}>2 lots above ₹</label>{num('profit_threshold_2', 50000)}</div>
            <div><label className={lbl}>3 lots above ₹</label>{num('profit_threshold_3', 50000)}</div>
            <div><label className={lbl}>4 lots above ₹</label>{num('profit_threshold_4', 50000)}</div>
            <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer self-end"><input type="checkbox" checked={cfg.scale_enabled} onChange={(e) => patch('scale_enabled', e.target.checked)} className="accent-brand-500" /> Progressive scaling</label>
            <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer self-end"><input type="checkbox" checked={cfg.trade_buy} onChange={(e) => patch('trade_buy', e.target.checked)} className="accent-brand-500" /> BUY side</label>
            <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer self-end"><input type="checkbox" checked={cfg.trade_sell} onChange={(e) => patch('trade_sell', e.target.checked)} className="accent-brand-500" /> SELL side</label>
          </div>

          <details className="pt-2 border-t border-surface-3">
            <summary className="text-xs text-gray-400 cursor-pointer flex items-center gap-1.5"><SlidersHorizontal className="w-3.5 h-3.5" /> Transaction costs — all zero by default (pure point result)</summary>
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3 mt-3">
              {[['brokerage_per_order', 'Brokerage / order ₹'], ['slippage_points', 'Slippage (pts/side)'],
                ['stt_pct', 'STT %'], ['exchange_pct', 'Exchange %'], ['gst_pct', 'GST %'],
                ['sebi_pct', 'SEBI %'], ['stamp_pct', 'Stamp %']].map(([k, label]) => (
                <div key={k}><label className={lbl}>{label}</label>
                  <input type="number" step="0.001" value={cfg.costs?.[k] ?? 0} onChange={(e) => patchCost(k, e.target.value)} className={`w-full ${sel}`} />
                </div>
              ))}
            </div>
          </details>
        </div>
      )}

      {/* ── backtest results ── */}
      {tab === 'backtest' && res?.status === 'ok' && s && (
        <div className="space-y-4">
          <div className="text-xs text-gray-500">
            {res.source} · {res.bars?.toLocaleString('en-IN')} bars · {res.first_day} → {res.last_day}
            {res.rows_dropped ? ` · ${res.rows_dropped} invalid rows dropped` : ''}
            {res.duplicates_removed ? ` · ${res.duplicates_removed} duplicates removed` : ''}
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2">
            <Stat label="Trades" value={s.total_trades.toLocaleString('en-IN')} />
            <Stat label="Win rate" value={PCT(s.win_rate)} tone={s.win_rate >= 50 ? 'text-emerald-400' : 'text-amber-400'} />
            <Stat label="NIFTY points" value={NUM(s.total_points, 0)} tone={s.total_points >= 0 ? 'text-emerald-400' : 'text-red-400'} />
            <Stat label="Net P&L" value={INR(s.total_pnl)} tone={s.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'} />
            <Stat label="Final equity" value={INR(s.final_equity)} />
            <Stat label="Return" value={PCT(s.total_return_pct)} tone={s.total_return_pct >= 0 ? 'text-emerald-400' : 'text-red-400'} />
            <Stat label="Max drawdown" value={INR(s.max_drawdown)} tone="text-red-400" />
            <Stat label="Profit factor" value={s.profit_factor == null ? '—' : NUM(s.profit_factor)} />
            <Stat label="Avg win / loss" value={`${INR(s.avg_win)} / ${INR(s.avg_loss)}`} />
            <Stat label="Streaks W/L" value={`${s.max_win_streak} / ${s.max_loss_streak}`} />
            <Stat label="Exits T/SL/EOD" value={`${s.target_exits}/${s.sl_exits}/${s.eod_exits}`} />
            <Stat label="BUY / SELL" value={`${s.buy_trades} / ${s.sell_trades}`} />
            <Stat label="Trading days" value={`${s.days_with_trades}/${s.trading_days}`} />
            <Stat label="Costs" value={INR(s.total_costs)} />
          </div>

          <div className="bg-surface-2 border border-surface-3 rounded-xl p-3">
            <div className="text-sm font-semibold text-gray-200 mb-2 px-1">Equity curve</div>
            <EquityChart curve={res.equity_curve} />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
              <div className="text-sm font-semibold text-gray-200 mb-2">Position scaling</div>
              <table className="w-full text-xs">
                <thead className="text-gray-400"><tr><th className="text-left py-1">Lots</th><th className="text-left">From</th><th className="text-left">To</th><th className="text-right">Trades</th><th className="text-right">Win%</th><th className="text-right">P&L</th></tr></thead>
                <tbody>{(res.scaling || []).map((r) => (
                  <tr key={r.lots} className="border-t border-surface-3/40">
                    <td className="py-1 text-brand-300 font-semibold">{r.lots} × {r.quantity}</td>
                    <td className="text-gray-400">{r.start_date}</td>
                    <td className="text-gray-400">{r.end_date}</td>
                    <td className="text-right text-gray-300">{r.trades}</td>
                    <td className="text-right text-gray-300">{r.win_rate}</td>
                    <td className={`text-right font-semibold ${r.net_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{INR(r.net_pnl)}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
              <div className="text-sm font-semibold text-gray-200 mb-2">Year by year</div>
              <table className="w-full text-xs">
                <thead className="text-gray-400"><tr><th className="text-left py-1">Year</th><th className="text-right">Trades</th><th className="text-right">Win%</th><th className="text-right">Points</th><th className="text-right">Net P&L</th><th className="text-right">Max DD</th></tr></thead>
                <tbody>{(res.yearly || []).map((y) => (
                  <tr key={y.year} className="border-t border-surface-3/40">
                    <td className="py-1 text-gray-200">{y.year}</td>
                    <td className="text-right text-gray-300">{y.trades}</td>
                    <td className="text-right text-gray-300">{y.win_rate}</td>
                    <td className={`text-right ${y.points >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{NUM(y.points, 0)}</td>
                    <td className={`text-right font-semibold ${y.net_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{INR(y.net_pnl)}</td>
                    <td className="text-right text-red-400">{INR(y.max_drawdown)}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </div>

          <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
            <div className="text-sm font-semibold text-gray-200 mb-2">Integrity checks</div>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1.5">
              {Object.entries(res.checks || {}).map(([k, v]) => (
                <div key={k} className={`flex items-start gap-2 px-2.5 py-1.5 rounded-lg border text-[11px] ${v.ok ? 'bg-emerald-500/5 border-emerald-500/25' : 'bg-red-500/10 border-red-500/40'}`}>
                  {v.ok ? <Check className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" /> : <X className="w-3.5 h-3.5 text-red-400 shrink-0 mt-0.5" />}
                  <span><span className="text-gray-200">{k}</span><span className="block text-gray-500">{v.detail}</span></span>
                </div>
              ))}
            </div>
          </div>

          {res.option_summary && (
            <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
              <div className="text-sm font-semibold text-gray-200 mb-1">Option overlay — real premium in / out</div>
              <p className="text-[11px] text-gray-500 mb-2">{res.option_summary.note}</p>
              <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                <Stat label="Legs priced" value={res.option_summary.trades} />
                <Stat label="Win rate" value={PCT(res.option_summary.win_rate)} />
                <Stat label="Premium points" value={NUM(res.option_summary.total_premium_points, 0)} />
                <Stat label="Net P&L" value={INR(res.option_summary.net_pnl)} tone={res.option_summary.net_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'} />
                <Stat label="Return" value={PCT(res.option_summary.return_pct)} />
              </div>
              {res.option_skipped?.length > 0 && (
                <div className="text-[11px] text-amber-400/80 mt-2">{res.option_skipped.length} leg(s) could not be priced — e.g. {res.option_skipped[0].reason}</div>
              )}
            </div>
          )}
          {res.option_error && <div className="text-xs text-amber-400/90 bg-amber-500/10 border border-amber-500/30 rounded-lg px-3 py-2">{res.option_error}</div>}

          <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
            <div className="px-3 py-2 border-b border-surface-3 flex items-center justify-between">
              <span className="text-sm font-semibold text-gray-200">Trade log <span className="text-gray-500">(last {res.trades?.length || 0} of {res.trade_count})</span></span>
              <button onClick={() => download('trade_log.csv')} className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white"><Download className="w-3.5 h-3.5" /> Full CSV</button>
            </div>
            <div className="overflow-x-auto max-h-[420px]">
              <table className="w-full text-xs whitespace-nowrap">
                <thead className="bg-surface-3 text-gray-300 sticky top-0"><tr>
                  {['#', 'Date', 'Side', 'Open', 'Entry@', 'Entry', 'SL', 'TP', 'Exit@', 'Exit', 'Why', 'Points', 'Lots', 'Qty', 'Net ₹', 'Cum ₹', 'Equity', 'DD'].map((h) => <th key={h} className="px-2 py-1.5 text-right first:text-left font-semibold">{h}</th>)}
                </tr></thead>
                <tbody>{(res.trades || []).slice().reverse().map((t) => (
                  <tr key={t.trade_no} className="border-t border-surface-3/40">
                    <td className="px-2 py-1 text-left text-gray-500">{t.trade_no}</td>
                    <td className="px-2 py-1 text-right text-gray-400">{t.date}</td>
                    <td className={`px-2 py-1 text-right font-semibold ${t.side === 'BUY' ? 'text-emerald-400' : 'text-red-400'}`}>{t.side}</td>
                    <td className="px-2 py-1 text-right text-gray-400">{NUM(t.daily_open, 0)}</td>
                    <td className="px-2 py-1 text-right text-gray-500">{t.entry_time.slice(11)}</td>
                    <td className="px-2 py-1 text-right text-gray-300">{NUM(t.entry_price, 0)}</td>
                    <td className="px-2 py-1 text-right text-red-400/80">{NUM(t.stop_loss, 0)}</td>
                    <td className="px-2 py-1 text-right text-emerald-400/80">{NUM(t.target, 0)}</td>
                    <td className="px-2 py-1 text-right text-gray-500">{t.exit_time.slice(11)}</td>
                    <td className="px-2 py-1 text-right text-gray-300">{NUM(t.exit_price, 0)}</td>
                    <td className={`px-2 py-1 text-right ${t.exit_reason === 'TARGET' ? 'text-emerald-400' : t.exit_reason === 'SL' ? 'text-red-400' : 'text-gray-400'}`}>{t.exit_reason}</td>
                    <td className={`px-2 py-1 text-right ${t.nifty_points >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{NUM(t.nifty_points, 1)}</td>
                    <td className="px-2 py-1 text-right text-brand-300">{t.lots}</td>
                    <td className="px-2 py-1 text-right text-gray-500">{t.quantity}</td>
                    <td className={`px-2 py-1 text-right font-semibold ${t.net_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{NUM(t.net_pnl, 0)}</td>
                    <td className="px-2 py-1 text-right text-gray-400">{NUM(t.cumulative_profit, 0)}</td>
                    <td className="px-2 py-1 text-right text-gray-300">{NUM(t.equity, 0)}</td>
                    <td className="px-2 py-1 text-right text-red-400/80">{NUM(t.drawdown, 0)}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {tab === 'backtest' && !res && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-10 text-center text-gray-500 text-sm">
          Upload your 1-minute NIFTY CSV (or connect Zerodha) and hit Run Backtest.
        </div>
      )}

      {/* ── live / paper ── */}
      {tab === 'live' && (
        <div className="space-y-4">
          <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
            <div className="flex flex-wrap items-center gap-3">
              <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer"><input type="checkbox" checked={cfg.paper_trade} onChange={(e) => patch('paper_trade', e.target.checked)} className="accent-brand-500" /> Paper mode</label>
              <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer"><input type="checkbox" checked={cfg.auto_start} onChange={(e) => patch('auto_start', e.target.checked)} className="accent-brand-500" /> Auto-start</label>
              <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer"><input type="checkbox" checked={cfg.telegram_alerts} onChange={(e) => patch('telegram_alerts', e.target.checked)} className="accent-brand-500" /> Telegram</label>
              <select value={cfg.telegram_bot} onChange={(e) => patch('telegram_bot', e.target.value)} className={`${sel} py-1`}><option value="a">Bot A</option><option value="b">Bot B</option></select>
              <div className="ml-auto flex items-center gap-2">
                {status?.is_active
                  ? <button onClick={stopLive} className="px-3 py-1.5 text-sm rounded-lg bg-red-600/80 hover:bg-red-600 text-white font-semibold">Stop</button>
                  : <button onClick={startLive} className="px-3 py-1.5 text-sm rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white font-semibold">Start</button>}
                <button onClick={saveCfg} className="px-3 py-1.5 text-sm rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white flex items-center gap-1"><Save className="w-3.5 h-3.5" /> Save</button>
                <button onClick={loadLive} className="text-gray-400 hover:text-white"><RefreshCw className="w-4 h-4" /></button>
              </div>
            </div>
            {!cfg.paper_trade && (
              <p className="text-[11px] text-amber-400">⚠ REAL mode also needs the global PAPER_TRADE off and TRADING_ENABLED on. The index itself cannot be traded — pick an option mode for real orders.</p>
            )}
            {cfg.instrument_mode === 'spot' && (
              <p className="text-[11px] text-gray-500">Spot mode tracks the signal on the index only (no order is possible on an index). Choose option buying or selling to place real legs.</p>
            )}
          </div>

          {status && (
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2">
              <Stat label="Armed" value={status.armed ? 'Yes' : 'Waiting for 09:15'} tone={status.armed ? 'text-emerald-400' : 'text-amber-400'} />
              <Stat label="Daily open" value={NUM(status.daily_open, 2)} />
              <Stat label="BUY level" value={NUM(status.buy_level, 2)} tone="text-emerald-400" />
              <Stat label="SELL level" value={NUM(status.sell_level, 2)} tone="text-red-400" />
              <Stat label="Index LTP" value={NUM(status.index_ltp, 2)} />
              <Stat label="Taken today" value={`${status.taken_today?.BUY || 0} BUY / ${status.taken_today?.SELL || 0} SELL`} />
              <Stat label="Next size" value={`${status.lots_next} lot(s)`} tone="text-brand-300" />
              <Stat label="Realised" value={INR(status.realised_profit)} tone={(status.realised_profit || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'} />
              <Stat label="Equity" value={INR(status.equity)} />
              <Stat label="Open legs" value={status.open_positions} />
            </div>
          )}
          {status?.last_error && <div className="text-xs text-amber-400/90">{status.last_error}</div>}

          <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
            <div className="px-3 py-2 border-b border-surface-3 text-sm font-semibold text-gray-200">Positions <span className="text-gray-500">({positions.length})</span></div>
            {!positions.length ? (
              <div className="px-4 py-8 text-center text-gray-500 text-sm">No legs yet — one opens when the index touches a level inside the entry window.</div>
            ) : (
              <div className="overflow-x-auto"><table className="w-full text-xs whitespace-nowrap">
                <thead className="bg-surface-3 text-gray-300"><tr>
                  {['Date', 'Signal', 'Instrument', 'Action', 'Lots', 'Qty', 'Entry', 'Index SL', 'Index TP', 'LTP', 'MTM', 'Status', 'Exit@'].map((h) => <th key={h} className="px-2.5 py-2 text-right first:text-left font-semibold">{h}</th>)}
                </tr></thead>
                <tbody>{positions.map((p) => (
                  <tr key={p.id} className="border-t border-surface-3/40">
                    <td className="px-2.5 py-1.5 text-left text-gray-400">{p.date}</td>
                    <td className={`px-2.5 py-1.5 text-right font-semibold ${p.side === 'BUY' ? 'text-emerald-400' : 'text-red-400'}`}>
                      <span className="inline-flex items-center gap-1">{p.side === 'BUY' ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}{p.side}</span>
                    </td>
                    <td className="px-2.5 py-1.5 text-right text-brand-300">{p.tradingsymbol}{p.paper && <span className="ml-1 text-[9px] px-1 rounded bg-surface-3 text-gray-500 border border-surface-4">paper</span>}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-300">{p.action}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-300">{p.lots}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-500">{p.qty}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-200">{NUM(p.entry_price)}</td>
                    <td className="px-2.5 py-1.5 text-right text-red-400/80">{NUM(p.stop_loss, 0)}</td>
                    <td className="px-2.5 py-1.5 text-right text-emerald-400/80">{NUM(p.target, 0)}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-200">{NUM(p.ltp)}</td>
                    <td className={`px-2.5 py-1.5 text-right font-semibold ${(p.mtm || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{NUM(p.mtm, 0)}</td>
                    <td className={`px-2.5 py-1.5 text-right ${p.status === 'OPEN' ? 'text-amber-400' : p.status === 'TARGET' ? 'text-emerald-400' : p.status === 'SL' ? 'text-red-400' : 'text-gray-400'}`}>{p.status}</td>
                    <td className="px-2.5 py-1.5 text-right text-gray-500">{p.exit_time || '—'}</td>
                  </tr>
                ))}</tbody>
              </table></div>
            )}
          </div>
        </div>
      )}

      {/* ── report ── */}
      {tab === 'report' && (
        <div className="space-y-3">
          <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
            <div className="text-sm font-semibold text-gray-200 mb-2">Artefacts</div>
            <div className="flex flex-wrap gap-2">
              {ARTEFACTS.map((f) => (
                <button key={f} onClick={() => download(f)} className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white">
                  <Download className="w-3.5 h-3.5" /> {f}
                </button>
              ))}
            </div>
            <p className="text-[11px] text-gray-600 mt-2">Written to the server on every run; charts are the same ones the CLI produces.</p>
          </div>
          <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
            <pre className="text-[11px] text-gray-300 whitespace-pre-wrap font-mono leading-relaxed max-h-[70vh] overflow-y-auto">
              {res?.report || 'Run a backtest to produce the report.'}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}
