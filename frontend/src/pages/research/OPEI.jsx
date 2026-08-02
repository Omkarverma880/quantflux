import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Activity, Loader2, AlertCircle, Play, Pause, Zap, Send, Save, Download, Radio, Info,
  TrendingUp, TrendingDown, BarChart3, Settings2, ScrollText,
} from 'lucide-react';
import { api } from '../../api';

const selCls = 'bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';
const lbl = 'block text-[10px] text-gray-500 uppercase tracking-wide mb-1';
const STRIKES = ['ATM', '100 ITM', '200 ITM', '300 ITM', '100 OTM', '200 OTM', '300 OTM'];
const TIMEFRAMES = [['1m', '1 Min'], ['3m', '3 Min'], ['5m', '5 Min'], ['15m', '15 Min']];
const CATS = [['trend', 'Trend'], ['momentum', 'Momentum'], ['vwap', 'VWAP'], ['volume', 'Volume'], ['oi', 'Open Interest'],
  ['volatility', 'Volatility'], ['liquidity', 'Liquidity'], ['breadth', 'Breadth'], ['premium_structure', 'Premium Struct'], ['time', 'Time']];
const NUM = (v, d = 2) => (v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));

const scoreColor = (s) => (s >= 95 ? '#22c55e' : s >= 90 ? '#4ade80' : s >= 80 ? '#a3e635' : s >= 70 ? '#eab308' : '#6b7280');

function ScoreGauge({ score, band }) {
  const c = scoreColor(score);
  const inst = score >= 95;
  return (
    <div className={`relative rounded-xl px-4 py-3 text-center border ${inst ? 'animate-pulse' : ''}`}
      style={{ borderColor: c, boxShadow: inst ? `0 0 22px ${c}66` : `0 0 8px ${c}22`, background: `${c}12` }}>
      <div className="text-4xl font-extrabold" style={{ color: c }}>{score}</div>
      <div className="text-[11px] font-semibold uppercase tracking-wide" style={{ color: c }}>{band}</div>
    </div>
  );
}

function LevelsTable({ levels, premium }) {
  if (!levels?.length) return <div className="text-gray-600 text-xs px-2 py-4">No levels.</div>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs whitespace-nowrap">
        <thead><tr className="text-gray-500">
          {['Entry', 'Conf%', 'Move%', 'Momentum', 'Risk', 'Hist%', 'Hold', 'SL', 'Targets'].map((h) => <th key={h} className="px-2 py-1.5 text-right font-medium">{h}</th>)}
        </tr></thead>
        <tbody>
          {levels.map((lv, i) => {
            const active = premium >= lv.level;
            const c = scoreColor(lv.confidence);
            return (
              <tr key={i} className={`border-t border-surface-3/40 ${lv.is_best ? 'bg-amber-500/5' : ''} ${active ? 'bg-emerald-500/5' : ''}`}
                style={lv.is_best ? { boxShadow: 'inset 0 0 0 1px #f59e0b66' } : {}}>
                <td className="px-2 py-1.5 text-right font-bold text-gray-100">
                  ₹{NUM(lv.level)}{lv.is_best && <span className="ml-1 text-[9px] px-1 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40">BEST</span>}
                  {active && <span className="ml-1 text-[9px] px-1 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/40">ACTIVE {(premium - lv.level) >= 0 ? '+' : ''}{NUM(premium - lv.level)}pts</span>}
                </td>
                <td className="px-2 py-1.5 text-right font-semibold" style={{ color: c }}>{lv.confidence}</td>
                <td className="px-2 py-1.5 text-right text-gray-400">{lv.expected_move_pct}%</td>
                <td className="px-2 py-1.5 text-right text-gray-400">{lv.expected_momentum}</td>
                <td className="px-2 py-1.5 text-right text-gray-400">{lv.risk}</td>
                <td className="px-2 py-1.5 text-right text-gray-400">{lv.historical_success_pct}</td>
                <td className="px-2 py-1.5 text-right text-gray-500">{lv.expected_hold_min}m</td>
                <td className="px-2 py-1.5 text-right text-red-400">₹{NUM(lv.sl)}</td>
                <td className="px-2 py-1.5 text-right text-emerald-400">{(lv.targets || []).map((t) => NUM(t, 0)).join(' · ')}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function CategoryBars({ categories }) {
  if (!categories) return null;
  return (
    <div className="space-y-1">
      {CATS.map(([k, label]) => {
        const s = categories[k]?.score ?? 0;
        const c = scoreColor(s);
        return (
          <div key={k} className="flex items-center gap-2">
            <span className="text-[10px] text-gray-500 w-24 shrink-0">{label}</span>
            <div className="flex-1 h-2 rounded bg-surface-3 overflow-hidden">
              <div className="h-full rounded" style={{ width: `${s}%`, background: c }} />
            </div>
            <span className="text-[10px] text-gray-400 w-8 text-right">{Math.round(s)}</span>
          </div>
        );
      })}
    </div>
  );
}

function SidePanel({ side, data }) {
  if (!data) return null;
  const Icon = side === 'CE' ? TrendingUp : TrendingDown;
  const f = data.features || {};
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-3">
        <div className="flex items-center gap-2">
          <Icon className={`w-5 h-5 ${side === 'CE' ? 'text-emerald-400' : 'text-red-400'}`} />
          <div>
            <div className="text-sm font-bold text-gray-100">{data.symbol}</div>
            <div className="text-[11px] text-gray-500">Strike {data.strike} · Premium ₹{NUM(data.premium)} · {f.buildup || '—'}</div>
          </div>
        </div>
        <ScoreGauge score={data.overall} band={data.band} />
      </div>

      {data.entry_active && (
        <div className="flex items-center gap-2 px-4 py-2 bg-emerald-500/10 text-emerald-300 text-xs border-b border-surface-3">
          <Zap className="w-4 h-4" /> ENTRY ACTIVE @ ₹{NUM(data.entry_active.level)} — premium crossed. Conf {data.entry_active.confidence}%, exp. move {data.entry_active.expected_move_pct}%.
        </div>
      )}

      <div className="p-3 space-y-3">
        <div className="text-xs font-semibold text-gray-300">Recommended Entry Levels</div>
        <LevelsTable levels={data.levels} premium={data.premium} />

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 pt-1">
          <div>
            <div className="text-xs font-semibold text-gray-300 mb-1.5">Confidence Breakdown</div>
            <CategoryBars categories={data.categories} />
          </div>
          <div>
            <div className="text-xs font-semibold text-gray-300 mb-1.5">Aligned Confluences</div>
            <div className="flex flex-wrap gap-1.5">
              {(data.reasons || []).slice(0, 14).map((r, i) => (
                <span key={i} className="px-2 py-0.5 rounded-md bg-surface-3 border border-surface-4 text-[11px] text-gray-300">✅ {r}</span>
              ))}
              {(!data.reasons || !data.reasons.length) && <span className="text-xs text-gray-600">No strong confluences.</span>}
            </div>
            <div className="grid grid-cols-3 gap-1.5 mt-3 text-[11px]">
              {[['RSI', f.rsi], ['ADX', f.adx], ['ROC', f.roc], ['RelVol', f.rel_volume], ['IV', f.iv], ['Spread%', f.spread_pct], ['DepthImb', f.depth_imbalance], ['OI', f.oi], ['Chg%', f.change_pct]].map(([k, v]) => (
                <div key={k} className="bg-surface-3 rounded px-2 py-1"><span className="text-gray-500">{k}</span> <span className="text-gray-200 float-right">{v == null ? '—' : v}</span></div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Heatmap({ sides }) {
  if (!sides) return null;
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-3 overflow-x-auto">
      <div className="text-xs font-semibold text-gray-200 mb-2 flex items-center gap-1"><BarChart3 className="w-3.5 h-3.5" /> Confluence Heatmap</div>
      <table className="text-[11px] border-collapse">
        <thead><tr><th className="px-2 py-1 text-gray-500"></th>{CATS.map(([k, l]) => <th key={k} className="px-2 py-1 text-gray-500 font-medium">{l}</th>)}<th className="px-2 py-1 text-gray-400 font-semibold">Overall</th></tr></thead>
        <tbody>
          {['CE', 'PE'].map((side) => (
            <tr key={side}>
              <td className={`px-2 py-1 font-bold ${side === 'CE' ? 'text-emerald-400' : 'text-red-400'}`}>{side}</td>
              {CATS.map(([k]) => { const s = sides[side]?.categories?.[k]?.score ?? 0; return <td key={k} className="px-2 py-1 text-center text-gray-100" style={{ background: `${scoreColor(s)}33` }}>{Math.round(s)}</td>; })}
              <td className="px-2 py-1 text-center font-bold text-gray-100" style={{ background: `${scoreColor(sides[side]?.overall ?? 0)}55` }}>{sides[side]?.overall ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function OPEI() {
  const [cfg, setCfg] = useState(null);
  const [data, setData] = useState(null);
  const [tab, setTab] = useState('live');
  const [auto, setAuto] = useState(true);
  const [err, setErr] = useState('');
  const [msg, setMsg] = useState('');
  const [log, setLog] = useState([]);
  const timer = useRef(null);
  const showErr = (m) => { setErr(m); setTimeout(() => setErr(''), 6000); };
  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(''), 2500); };
  const patch = (k, v) => setCfg((c) => ({ ...c, [k]: v }));

  useEffect(() => { api.researchOPEIConfig().then((r) => { if (r.status === 'ok') setCfg(r.config); }).catch(() => setCfg({})); }, []);

  const load = useCallback(async (silent = false) => {
    if (!cfg) return;
    try {
      const r = await api.researchOPEISnapshot({ strike: cfg.strike, timeframe: cfg.timeframe, expiry_type: cfg.expiry_type });
      if (r.status === 'ok') setData(r);
      else if (!silent) showErr(r.message || 'Snapshot failed');
    } catch (e) { if (!silent) showErr(e.message); }
  }, [cfg]);

  useEffect(() => { if (cfg) load(false); /* eslint-disable-next-line */ }, [cfg?.strike, cfg?.timeframe, cfg?.expiry_type]);
  useEffect(() => {
    if (timer.current) clearInterval(timer.current);
    if (auto && cfg) timer.current = setInterval(() => load(true), Math.max(2, cfg.refresh_interval || 3) * 1000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [auto, cfg, load]);

  const saveCfg = async () => { const r = await api.researchOPEIConfigSave(cfg); if (r.status === 'ok') { setCfg(r.config); flash('Saved'); } else showErr(r.message); };
  const testTg = async () => { const r = await api.researchOPEITelegramTest(cfg.telegram_bot_token, cfg.telegram_chat_id); if (r.ok) flash('Telegram OK — check your chat'); else showErr(r.error || 'Telegram failed'); };
  const loadLog = async () => { const r = await api.researchOPEILog(); if (r.status === 'ok') setLog(r.rows || []); };
  useEffect(() => { if (tab === 'log') loadLog(); }, [tab]);

  const downloadCSV = () => {
    const cols = ['date', 'time', 'side', 'symbol', 'strike', 'premium', 'level', 'confidence', 'band', 'sl', 'target1', 'result', 'mfe', 'mae', 'duration_min', 'triggered', 'target_hit', 'sl_hit'];
    const esc = (v) => { const s = v == null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const lines = [cols.join(',')].concat(log.map((r) => cols.map((c) => esc(r[c])).join(',')));
    const blob = new Blob([lines.join('\n') + '\n'], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'opei_log.csv'; a.click();
  };

  if (!cfg) return <div className="p-6 text-gray-500 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Loading…</div>;

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-[1500px] mx-auto">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Zap className="w-5 h-5 text-brand-400" />
            <h1 className="text-xl font-bold text-gray-100 tracking-wide">Option Premium Entry Intelligence Engine</h1>
            <span className="px-2 py-0.5 rounded-full bg-brand-600/15 text-brand-400 text-xs font-semibold border border-brand-500/20">Research Only · No Orders</span>
          </div>
          <p className="text-gray-500 text-sm mt-0.5">Highest-probability premium entry levels for CE &amp; PE via a weighted live-confluence engine.</p>
        </div>
        <div className="flex items-center gap-2">
          <select value={cfg.strike} onChange={(e) => patch('strike', e.target.value)} className={selCls}>{STRIKES.map((s) => <option key={s} value={s}>{s}</option>)}</select>
          <select value={cfg.timeframe} onChange={(e) => patch('timeframe', e.target.value)} className={selCls}>{TIMEFRAMES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
          <select value={cfg.expiry_type} onChange={(e) => patch('expiry_type', e.target.value)} className={selCls}><option value="weekly">Weekly</option><option value="monthly">Monthly</option></select>
          <button onClick={() => setAuto((a) => !a)} className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border transition ${auto ? 'bg-emerald-600/20 text-emerald-400 border-emerald-500/40' : 'bg-surface-3 text-gray-400 border-surface-4'}`}>{auto ? <Radio className="w-3.5 h-3.5 animate-pulse" /> : <Play className="w-3.5 h-3.5" />} Live {auto ? 'ON' : 'OFF'}</button>
        </div>
      </div>

      {err && <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-red-400 text-sm"><AlertCircle className="w-4 h-4" /> {err}</div>}
      {msg && <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-4 py-2 text-emerald-400 text-sm">{msg}</div>}

      {data && (
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm bg-surface-2 border border-surface-3 rounded-xl px-4 py-2.5">
          <span className="text-gray-400">Spot <strong className="text-gray-100">₹{NUM(data.spot)}</strong></span>
          <span className="text-gray-400">ATM <strong className="text-brand-400">{data.atm}</strong></span>
          <span className="text-gray-400">Expiry <strong className="text-gray-100">{data.expiry}</strong></span>
          <span className="text-gray-400">VIX <strong className={data.vix_change >= 0 ? 'text-emerald-400' : 'text-red-400'}>{data.vix} ({data.vix_change >= 0 ? '+' : ''}{data.vix_change})</strong></span>
          <span className="text-gray-400">PCR <strong className="text-gray-100">{data.pcr ?? '—'}</strong></span>
          <span className="text-gray-400">Breadth <strong className={data.breadth >= 0 ? 'text-emerald-400' : 'text-red-400'}>{data.breadth}%</strong></span>
          <span className="text-gray-500 text-xs ml-auto flex items-center gap-1"><Activity className="w-3 h-3" /> {data.fetched_at}</span>
        </div>
      )}

      <div className="flex gap-1 border-b border-surface-3">
        {[['live', 'Live', Zap], ['settings', 'Weights & Telegram', Settings2], ['log', 'Log', ScrollText]].map(([id, label, Icon]) => (
          <button key={id} onClick={() => setTab(id)} className={`flex items-center gap-1.5 px-4 py-2 text-sm font-semibold border-b-2 -mb-px transition ${tab === id ? 'border-brand-500 text-brand-400' : 'border-transparent text-gray-400 hover:text-gray-200'}`}><Icon className="w-4 h-4" /> {label}</button>
        ))}
      </div>

      {tab === 'live' && (
        <div className="space-y-4">
          {!data && <div className="bg-surface-2 border border-surface-3 rounded-xl p-12 text-center text-gray-500 text-sm flex items-center justify-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Fetching live confluence…</div>}
          {data && (
            <>
              <Heatmap sides={data.sides} />
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                <SidePanel side="CE" data={data.sides?.CE} />
                <SidePanel side="PE" data={data.sides?.PE} />
              </div>
            </>
          )}
        </div>
      )}

      {tab === 'settings' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
            <div className="text-sm font-semibold text-gray-200 mb-3">Confluence Weights (configurable)</div>
            <div className="grid grid-cols-2 gap-2">
              {CATS.map(([k, label]) => (
                <div key={k}><label className={lbl}>{label}</label>
                  <input type="number" min="0" step="1" value={cfg.weights?.[k] ?? 0} onChange={(e) => setCfg((c) => ({ ...c, weights: { ...c.weights, [k]: parseFloat(e.target.value) || 0 } }))} className={`w-full ${selCls}`} />
                </div>
              ))}
            </div>
            <button onClick={saveCfg} className="mt-3 flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold"><Save className="w-3.5 h-3.5" /> Save Weights</button>
          </div>

          <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
            <div className="text-sm font-semibold text-gray-200 mb-3 flex items-center gap-1.5"><Send className="w-4 h-4 text-brand-400" /> Telegram Alerts</div>
            <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer mb-2"><input type="checkbox" checked={cfg.telegram_enabled} onChange={(e) => patch('telegram_enabled', e.target.checked)} className="accent-brand-500" /> Enable Telegram</label>
            <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer mb-2"><input type="checkbox" checked={cfg.alert_on_institutional} onChange={(e) => patch('alert_on_institutional', e.target.checked)} className="accent-brand-500" /> Auto-send alerts for qualifying best-levels</label>
            <div className="mb-3"><label className={lbl}>Min confidence to log &amp; alert</label>
              <input type="number" min="50" max="100" value={cfg.alert_min_confidence ?? 95} onChange={(e) => patch('alert_min_confidence', parseInt(e.target.value) || 95)} className={`w-full ${selCls}`} />
              <p className="text-[10px] text-gray-600 mt-0.5">95 = Institutional only. Lower it (e.g. 90) for more alerts.</p>
            </div>
            <div className="space-y-2">
              <div><label className={lbl}>Bot Token</label><input value={cfg.telegram_bot_token} onChange={(e) => patch('telegram_bot_token', e.target.value)} placeholder="123456:ABC-..." className={`w-full ${selCls}`} /></div>
              <div><label className={lbl}>Chat ID</label><input value={cfg.telegram_chat_id} onChange={(e) => patch('telegram_chat_id', e.target.value)} placeholder="-100..." className={`w-full ${selCls}`} /></div>
            </div>
            <div className="flex items-center gap-2 mt-3">
              <button onClick={testTg} className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white"><Send className="w-3.5 h-3.5" /> Test Connection</button>
              <button onClick={saveCfg} className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold"><Save className="w-3.5 h-3.5" /> Save</button>
            </div>
            <p className="text-[11px] text-gray-600 mt-2"><Info className="w-3 h-3 inline" /> Create a bot via @BotFather, get the token, then your chat id (e.g. via @userinfobot).</p>
          </div>
        </div>
      )}

      {tab === 'log' && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
          <div className="flex items-center gap-3 px-3 py-2 border-b border-surface-3">
            <span className="text-sm font-semibold text-gray-200">Recommendation Log <span className="text-gray-500">({log.length})</span></span>
            <button onClick={loadLog} className="text-xs text-gray-400 hover:text-white">Refresh</button>
            <button onClick={downloadCSV} disabled={!log.length} className="ml-auto flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-40"><Download className="w-3.5 h-3.5" /> CSV</button>
          </div>
          {log.length === 0 ? <div className="px-4 py-8 text-center text-gray-500 text-sm">No logged recommendations yet (institutional-grade entries are logged automatically).</div> : (
            <div className="overflow-auto max-h-[520px]">
              <table className="w-full text-xs whitespace-nowrap">
                <thead className="sticky top-0 z-10"><tr className="text-gray-300 bg-surface-3">{['Date', 'Time', 'Side', 'Symbol', 'Premium', 'Entry', 'Conf', 'Result', 'MFE', 'MAE', 'Dur', 'SL', 'T1'].map((h) => <th key={h} className="px-2.5 py-2 font-semibold text-center border-r border-surface-2 last:border-r-0">{h}</th>)}</tr></thead>
                <tbody>{log.map((r, i) => {
                  const rc = r.result === 'TARGET' ? 'text-emerald-400' : r.result === 'SL' ? 'text-red-400' : r.result === 'OPEN' ? 'text-amber-400' : 'text-gray-500';
                  return (
                  <tr key={i} className="border-b border-surface-3/40 text-center hover:bg-surface-3/10">
                    <td className="px-2.5 py-1 text-gray-400">{r.date}</td><td className="px-2.5 py-1 text-gray-200">{r.time}</td>
                    <td className={`px-2.5 py-1 font-semibold ${r.side === 'CE' ? 'text-emerald-400' : 'text-red-400'}`}>{r.side}</td>
                    <td className="px-2.5 py-1 text-gray-300">{r.symbol}</td><td className="px-2.5 py-1 text-gray-300">{NUM(r.premium)}</td>
                    <td className="px-2.5 py-1 text-gray-100 font-semibold">{NUM(r.level)}</td><td className="px-2.5 py-1" style={{ color: scoreColor(r.confidence) }}>{r.confidence}</td>
                    <td className={`px-2.5 py-1 font-semibold ${rc}`}>{r.result}</td>
                    <td className="px-2.5 py-1 text-emerald-400">{r.mfe == null ? '—' : `+${NUM(r.mfe)}`}</td>
                    <td className="px-2.5 py-1 text-red-400">{r.mae == null ? '—' : NUM(r.mae)}</td>
                    <td className="px-2.5 py-1 text-gray-500">{r.duration_min == null ? '—' : `${r.duration_min}m`}</td>
                    <td className="px-2.5 py-1 text-red-400">{NUM(r.sl)}</td><td className="px-2.5 py-1 text-emerald-400">{NUM(r.target1)}</td>
                  </tr>
                ); })}</tbody>
              </table>
            </div>
          )}
        </div>
      )}

      <p className="text-[11px] text-gray-600 flex items-center gap-1"><Info className="w-3 h-3" /> Decision-support only — never places orders. "Historical success %" is a model estimate that calibrates as outcomes are logged. Replay backtest & adaptive weights are on the roadmap.</p>
    </div>
  );
}
