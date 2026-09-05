import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Layers, Play, Loader2, AlertCircle, Send, BookOpen, Check, X, TrendingUp,
  TrendingDown, ShieldAlert, Target, Activity, Search, Info,
} from 'lucide-react';
import { api } from '../../api';

const sel = 'bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';
const lbl = 'block text-[11px] text-gray-500 uppercase tracking-wide mb-1';
const NUM = (v, d = 2) => (v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
const TF_LABEL = {
  minute: '1 min', '3minute': '3 min', '5minute': '5 min', '10minute': '10 min',
  '15minute': '15 min', '30minute': '30 min', '60minute': '1 hour',
  day: 'Daily', week: 'Weekly', month: 'Monthly',
};
const GREEN = '#10b981'; const RED = '#ef4444';

const ACTION = {
  ENTER_LONG: { text: 'ENTER LONG', cls: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/40', Icon: TrendingUp },
  ENTER_SHORT: { text: 'ENTER SHORT', cls: 'bg-red-500/15 text-red-400 border-red-500/40', Icon: TrendingDown },
  PREPARE_LONG: { text: 'STALK — LONG', cls: 'bg-amber-500/15 text-amber-400 border-amber-500/40', Icon: Target },
  PREPARE_SHORT: { text: 'STALK — SHORT', cls: 'bg-amber-500/15 text-amber-400 border-amber-500/40', Icon: Target },
  AVOID: { text: 'STAY OUT', cls: 'bg-red-500/10 text-red-300 border-red-500/30', Icon: ShieldAlert },
  WAIT: { text: 'NOTHING YET', cls: 'bg-surface-3 text-gray-400 border-surface-4', Icon: Info },
};
const EV_COLOR = {
  SC: '#22c55e', PS: '#4ade80', AR: '#38bdf8', ST: '#a78bfa', SPRING: '#facc15',
  TEST: '#fbbf24', SOS: '#10b981', LPS: '#34d399',
  BC: '#ef4444', PSY: '#f87171', ARD: '#38bdf8', STD: '#a78bfa', UTAD: '#facc15',
  SOW: '#ef4444', LPSY: '#f87171',
};

function Meter({ value }) {
  const tone = value >= 70 ? 'bg-emerald-500' : value >= 45 ? 'bg-amber-500' : 'bg-red-500';
  return (
    <div className="flex items-center gap-2">
      <div className="w-24 h-1.5 rounded-full bg-surface-4 overflow-hidden"><div className={`h-full ${tone}`} style={{ width: `${value}%` }} /></div>
      <span className="text-xs font-bold text-gray-300 tabular-nums">{value}%</span>
    </div>
  );
}

function Verdict({ read, stance }) {
  const g = read?.guidance || {};
  const a = ACTION[g.action] || ACTION.WAIT;
  const { Icon } = a;
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border font-bold text-sm ${a.cls}`}>
          <Icon className="w-4 h-4" />{a.text}
        </span>
        {stance && (
          <span className={`px-2.5 py-1 rounded-lg text-xs font-bold border ${stance.tone === 'bull' ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30' : stance.tone === 'bear' ? 'bg-red-500/15 text-red-400 border-red-500/30' : 'bg-surface-3 text-gray-400 border-surface-4'}`}>
            {stance.stance}
          </span>
        )}
        <span className="text-sm text-gray-300">{g.headline}</span>
        <div className="ml-auto flex items-center gap-3">
          <span className="text-xs text-gray-500">{g.tests_passed}/{g.tests_total} tests · risk {g.risk}</span>
          <Meter value={g.confidence || 0} />
        </div>
      </div>
      {stance?.note && <p className="text-xs text-gray-400">{stance.note}</p>}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <div>
          <div className="text-[11px] uppercase tracking-wide text-emerald-400/80 mb-1">Why this reads the way it does</div>
          {(g.reasons || []).length === 0 ? <div className="text-xs text-gray-600">—</div> : (
            <ul className="space-y-1">{g.reasons.map((r, i) => <li key={i} className="text-xs text-gray-300 flex gap-2"><Check className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />{r}</li>)}</ul>
          )}
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wide text-red-400/80 mb-1">Reasons to stay out</div>
          {(g.avoid || []).length === 0 ? <div className="text-xs text-gray-600">Nothing disqualifying.</div> : (
            <ul className="space-y-1">{g.avoid.map((r, i) => <li key={i} className="text-xs text-gray-300 flex gap-2"><X className="w-3.5 h-3.5 text-red-400 shrink-0 mt-0.5" />{r}</li>)}</ul>
          )}
        </div>
      </div>
      {(g.trigger != null || g.invalidation != null) && (
        <div className="flex flex-wrap gap-4 pt-2 border-t border-surface-3 text-xs">
          <span className="text-gray-400">Acts above <strong className="text-emerald-400">₹{NUM(g.trigger)}</strong></span>
          <span className="text-gray-400">Read is wrong below <strong className="text-red-400">₹{NUM(g.invalidation)}</strong></span>
          <span className="text-gray-600">Levels are structural reference points — this page places no orders.</span>
        </div>
      )}
    </div>
  );
}

function RangeCard({ read }) {
  const r = read?.range || {};
  if (r.low == null) return null;
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-semibold text-gray-200">Trading range</span>
        <span className="text-[11px] text-gray-500">{r.duration} bars · {r.width_pct}% wide · from {r.start_at}</span>
      </div>
      <div className="relative h-9 rounded-lg bg-surface-3 overflow-hidden">
        <div className="absolute inset-y-0 left-0 right-0 flex items-center justify-between px-3 text-[11px] text-gray-500">
          <span>₹{NUM(r.low)}</span><span className="text-gray-600">mid ₹{NUM(r.mid)}</span><span>₹{NUM(r.high)}</span>
        </div>
        <div className="absolute top-0 bottom-0 w-0.5 bg-brand-400" style={{ left: `${Math.min(99, Math.max(0, r.position_pct))}%` }} />
      </div>
      <div className="text-[11px] text-gray-500 mt-1.5">Price sits <strong className="text-gray-300">{r.position_pct}%</strong> up the range · ATR {NUM(r.atr)}</div>
    </div>
  );
}

function Tests({ read }) {
  const t = read?.tests;
  if (!t) return null;
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-semibold text-gray-200">Wyckoff's nine {t.side === 'accum' ? 'buying' : 'selling'} tests</span>
        <span className="text-xs text-gray-400"><strong className={t.passed >= 5 ? 'text-emerald-400' : 'text-amber-400'}>{t.passed}</strong>/{t.total} passed</span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-1.5">
        {t.items.map((i) => (
          <div key={i.key} className={`flex items-center gap-2 px-2.5 py-1.5 rounded-lg border text-xs ${i.ok ? 'bg-emerald-500/5 border-emerald-500/25 text-gray-200' : 'bg-surface-3/40 border-surface-3 text-gray-500'}`}>
            {i.ok ? <Check className="w-3.5 h-3.5 text-emerald-400 shrink-0" /> : <X className="w-3.5 h-3.5 text-gray-600 shrink-0" />}
            {i.label}
          </div>
        ))}
      </div>
    </div>
  );
}

function Events({ read, meta }) {
  const ev = read?.events || [];
  if (!ev.length) return null;
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
      <div className="text-sm font-semibold text-gray-200 mb-2">Event sequence</div>
      <div className="flex flex-wrap gap-2">
        {ev.map((e, i) => (
          <div key={i} className="group relative">
            <span className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg border text-[11px] font-bold"
              style={{ color: EV_COLOR[e.type] || '#9ca3af', borderColor: `${EV_COLOR[e.type] || '#9ca3af'}55`, background: `${EV_COLOR[e.type] || '#9ca3af'}15` }}>
              {e.type}<span className="font-normal text-gray-500">{e.at} · ₹{NUM(e.price)}</span>
            </span>
            <div className="hidden group-hover:block absolute z-20 top-full left-0 mt-1 w-72 bg-surface-1 border border-surface-3 rounded-lg p-2.5 shadow-2xl">
              <div className="text-xs font-semibold text-gray-100">{e.name}</div>
              <div className="text-[11px] text-gray-400 mt-0.5">{e.meaning}</div>
              <div className="text-[11px] text-brand-300 mt-1">{e.note}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function EffortResult({ read }) {
  const e = read?.effort;
  if (!e) return null;
  const tone = e.verdict === 'ABSORPTION' ? 'text-emerald-400' : e.verdict === 'SUPPLY' ? 'text-red-400' : 'text-gray-400';
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
      <div className="flex items-center gap-2 mb-1">
        <Activity className="w-4 h-4 text-brand-400" />
        <span className="text-sm font-semibold text-gray-200">Effort vs result</span>
        <span className={`text-xs font-bold ${tone}`}>{e.verdict}</span>
      </div>
      <p className="text-xs text-gray-400">{e.note}</p>
      {e.available && e.bars?.length > 0 && (
        <div className="flex gap-0.5 mt-2 items-end h-10">
          {e.bars.map((b, i) => (
            <div key={i} className="flex-1 relative group" title={`${b.at} · effort ${b.effort}× · result ${b.result}× ATR${b.flag ? ` · ${b.flag}` : ''}`}>
              <div className={`w-full ${b.flag === 'absorption' ? 'bg-emerald-500' : b.flag === 'supply' ? 'bg-red-500' : b.flag ? 'bg-amber-500' : 'bg-surface-4'}`}
                style={{ height: `${Math.min(100, b.effort * 30)}%`, minHeight: 2 }} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function OiCard({ oi }) {
  if (!oi || !oi.available) return null;
  const good = ['LONG BUILD-UP', 'SHORT BUILD-UP'].includes(oi.verdict);
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
      <div className="flex items-center gap-2 mb-1">
        <Layers className="w-4 h-4 text-brand-400" />
        <span className="text-sm font-semibold text-gray-200">Open interest</span>
        <span className={`text-xs font-bold ${good ? 'text-emerald-400' : 'text-amber-400'}`}>{oi.verdict}</span>
      </div>
      <p className="text-xs text-gray-400">{oi.note}</p>
      <div className="text-[11px] text-gray-500 mt-1">OI {Number(oi.oi || 0).toLocaleString('en-IN')} · ΔOI {oi.oi_change_pct}% · Δprice {oi.price_change_pct}%</div>
    </div>
  );
}

function Chart({ read }) {
  const s = read?.series || [];
  if (!s.length) return null;
  const n = s.length;
  const step = Math.max(5, Math.min(14, Math.floor(900 / n)));
  const cw = Math.max(2, step - 4);
  const padL = 6, padR = 66, padT = 16, padB = 26, plotH = 300;
  const width = padL + n * step + padR, height = padT + plotH + padB;
  const r = read.range || {};
  const extra = [r.low, r.high].filter((v) => v != null);
  let lo = Math.min(...s.map((c) => c.low), ...extra);
  let hi = Math.max(...s.map((c) => c.high), ...extra);
  const pad = (hi - lo) * 0.05 || 1; lo -= pad; hi += pad;
  const y = (p) => padT + ((hi - p) / (hi - lo)) * plotH;
  const xc = (i) => padL + i * step + step / 2;
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-3">
      <div className="flex items-center justify-between mb-1 px-1">
        <span className="text-sm font-semibold text-gray-200">Structure</span>
        <span className="text-[11px] text-gray-500">shaded band = the trading range · letters mark the Wyckoff events</span>
      </div>
      <div className="overflow-x-auto">
        <svg width={width} height={height} className="block" style={{ minWidth: '100%' }}>
          {r.low != null && (
            <>
              <rect x={padL} y={y(r.high)} width={n * step} height={Math.max(1, y(r.low) - y(r.high))} fill="#38bdf80f" />
              <line x1={padL} x2={padL + n * step} y1={y(r.high)} y2={y(r.high)} stroke="#38bdf8" strokeWidth="1" strokeDasharray="5 3" />
              <line x1={padL} x2={padL + n * step} y1={y(r.low)} y2={y(r.low)} stroke="#38bdf8" strokeWidth="1" strokeDasharray="5 3" />
              <text x={padL + n * step + 4} y={y(r.high) + 3} fontSize="9" fill="#38bdf8">{NUM(r.high, 1)}</text>
              <text x={padL + n * step + 4} y={y(r.low) + 3} fontSize="9" fill="#38bdf8">{NUM(r.low, 1)}</text>
            </>
          )}
          {s.map((c, i) => {
            const col = c.close >= c.open ? GREEN : RED;
            const top = Math.min(y(c.open), y(c.close));
            const h = Math.max(Math.abs(y(c.open) - y(c.close)), 1);
            return (
              <g key={i}>
                <line x1={xc(i)} x2={xc(i)} y1={y(c.high)} y2={y(c.low)} stroke={col} strokeWidth="1" />
                <rect x={xc(i) - cw / 2} y={top} width={cw} height={h} fill={col} />
                <rect x={xc(i) - step / 2} y={padT} width={step} height={plotH} fill="transparent">
                  <title>{`${c.at}  O ${c.open}  H ${c.high}  L ${c.low}  C ${c.close}`}</title>
                </rect>
                {c.marks?.map((mk, j) => (
                  <g key={j}>
                    <line x1={xc(i)} x2={xc(i)} y1={y(c.low)} y2={y(c.low) + 8 + j * 11} stroke={EV_COLOR[mk] || '#9ca3af'} strokeWidth="0.8" opacity="0.6" />
                    <text x={xc(i)} y={y(c.low) + 16 + j * 11} fontSize="8" fontWeight="700" textAnchor="middle" fill={EV_COLOR[mk] || '#9ca3af'}>{mk}</text>
                  </g>
                ))}
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

function Report({ read, stance, oi }) {
  if (!read) return null;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="px-2.5 py-1 rounded-lg bg-brand-500/15 text-brand-300 border border-brand-500/25 font-bold text-xs">PHASE {read.phase}</span>
        <span className="text-gray-200 font-semibold">{read.bias}</span>
        <span className="text-xs text-gray-500">{read.phase_label}</span>
      </div>
      {read.phase_note && <p className="text-xs text-gray-400 -mt-1">{read.phase_note}</p>}
      <Verdict read={read} stance={stance} />
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <RangeCard read={read} />
        <EffortResult read={read} />
      </div>
      <OiCard oi={oi || read.oi} />
      <Events read={read} />
      <Tests read={read} />
      <Chart read={read} />
    </div>
  );
}

export default function Wyckoff() {
  const [meta, setMeta] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [tab, setTab] = useState('equity');
  const [err, setErr] = useState(''); const [msg, setMsg] = useState('');
  const [loading, setLoading] = useState(false);
  const showErr = (m) => { setErr(m); setTimeout(() => setErr(''), 7000); };
  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(''), 3000); };
  const patch = (k, v) => setCfg((c) => ({ ...c, [k]: v }));

  const [symbol, setSymbol] = useState('');
  const [sugg, setSugg] = useState([]); const suggTimer = useRef(null);
  const [eq, setEq] = useState(null);
  const [fno, setFno] = useState(null);
  const [opt, setOpt] = useState(null);
  const [optMode, setOptMode] = useState('index');
  const [optType, setOptType] = useState('CE');
  const [strike, setStrike] = useState('');
  const [expiryType, setExpiryType] = useState('weekly');
  const [index, setIndex] = useState('NIFTY');

  useEffect(() => {
    api.wyMeta().then((r) => { if (r.status === 'ok') { setMeta(r); setCfg(r.config); setIndex(r.config.index_name || 'NIFTY'); } })
      .catch(() => showErr('Could not load the Wyckoff desk'));
  }, []);

  useEffect(() => {
    if (suggTimer.current) clearTimeout(suggTimer.current);
    if (!symbol || symbol.trim().length < 2) { setSugg([]); return; }
    suggTimer.current = setTimeout(async () => {
      try { const r = await api.researchSymbolSearch(symbol.trim()); setSugg(r.status === 'ok' ? (r.results || []).slice(0, 8) : []); }
      catch { setSugg([]); }
    }, 250);
    return () => suggTimer.current && clearTimeout(suggTimer.current);
  }, [symbol]);

  const run = useCallback(async (which, telegram = false) => {
    setLoading(true); setErr('');
    try {
      let r;
      if (which === 'equity') {
        if (!symbol.trim()) { showErr('Enter a stock symbol'); return; }
        r = await api.wyEquity({ symbol: symbol.trim().toUpperCase(), overrides: cfg, telegram });
        if (r.status === 'ok') setEq(r);
      } else if (which === 'fno') {
        if (!symbol.trim()) { showErr('Enter an F&O stock symbol'); return; }
        r = await api.wyFno({ symbol: symbol.trim().toUpperCase(), overrides: cfg, telegram });
        if (r.status === 'ok') setFno(r);
      } else {
        r = await api.wyOptions({
          index, mode: optMode, opt_type: optType, expiry_type: expiryType,
          strike: strike ? Number(strike) : null, overrides: cfg, telegram,
        });
        if (r.status === 'ok') setOpt(r);
      }
      if (r.status !== 'ok') showErr(r.message || 'Analysis failed');
      else {
        if (r.telegram_sent) flash('Sent to Telegram');
        if (r.telegram_error) showErr(r.telegram_error);
        if (r.option_error) showErr(r.option_error);
      }
    } catch (e) { showErr(e.message); } finally { setLoading(false); }
  }, [symbol, cfg, index, optMode, optType, expiryType, strike]);

  if (!cfg || !meta) return <div className="p-6 text-gray-500 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Loading Wyckoff desk…</div>;

  const symbolInput = (placeholder) => (
    <div className="relative">
      <label className={lbl}>Stock</label>
      <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} placeholder={placeholder}
        className={`${sel} w-56`} onKeyDown={(e) => { if (e.key === 'Enter') { setSugg([]); run(tab); } }} />
      {sugg.length > 0 && (
        <div className="absolute z-20 mt-1 w-56 max-h-56 overflow-auto bg-surface-2 border border-surface-3 rounded-lg shadow-2xl">
          {sugg.map((s) => (
            <button key={`${s.symbol}:${s.exchange}`} onClick={() => { setSymbol(s.symbol); setSugg([]); }}
              className="w-full text-left px-3 py-1.5 hover:bg-surface-3/40 flex items-center gap-2 border-b border-surface-3/40 last:border-0">
              <span className="text-sm text-gray-100">{s.symbol}</span><span className="text-[10px] text-gray-500 ml-auto">{s.exchange}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );

  const tfPicker = () => (
    <div>
      <label className={lbl}>Candle</label>
      <select value={cfg.timeframe} onChange={(e) => patch('timeframe', e.target.value)} className={sel}>
        {(meta.timeframes || []).map((t) => <option key={t} value={t}>{TF_LABEL[t] || t}</option>)}
      </select>
    </div>
  );

  const runButtons = (which) => (
    <>
      <button onClick={() => run(which)} disabled={loading} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">
        {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />} Read structure
      </button>
      <button onClick={() => run(which, true)} disabled={loading} className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white disabled:opacity-50">
        <Send className="w-3.5 h-3.5" /> Send
      </button>
      <div className="flex items-center gap-2 text-xs text-gray-400">Bot
        <select value={cfg.telegram_bot} onChange={(e) => patch('telegram_bot', e.target.value)} className={`${sel} py-1`}>
          <option value="a">A</option><option value="b">B</option>
        </select>
      </div>
    </>
  );

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-[1800px] mx-auto">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Layers className="w-6 h-6 text-brand-400" />
            <h1 className="text-xl font-bold text-gray-100">Wyckoff Method</h1>
            <span className="text-[11px] px-2 py-0.5 rounded-full bg-brand-500/15 text-brand-300 border border-brand-500/25">Structure desk</span>
          </div>
          <p className="text-sm text-gray-500 mt-0.5">Supply &amp; demand, cause &amp; effect, effort vs result — read the range, name the phase, and know whether to act or stand aside. Analysis only: no orders.</p>
        </div>
      </div>

      <div className="flex gap-1 border-b border-surface-3">
        {[['equity', 'Equity', TrendingUp], ['options', 'NIFTY Options', Target], ['fno', 'Equity F&O', Layers], ['learn', 'Learn', BookOpen]].map(([id, label, Icon]) => (
          <button key={id} onClick={() => setTab(id)} className={`flex items-center gap-1.5 px-4 py-2 text-sm font-semibold border-b-2 -mb-px transition ${tab === id ? 'border-brand-500 text-brand-400' : 'border-transparent text-gray-400 hover:text-gray-200'}`}>
            <Icon className="w-4 h-4" /> {label}
          </button>
        ))}
      </div>

      {err && <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-red-400 text-sm"><AlertCircle className="w-4 h-4" /> {err}</div>}
      {msg && <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-4 py-2 text-emerald-400 text-sm">{msg}</div>}

      {tab === 'equity' && (
        <div className="space-y-4">
          <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 flex flex-wrap items-end gap-3">
            {symbolInput('e.g. RELIANCE')}
            {tfPicker()}
            {runButtons('equity')}
            <span className="text-[11px] text-gray-600">Cash equity — the cleanest Wyckoff read, because real volume drives the effort/result law.</span>
          </div>
          {eq ? (
            <>
              <div className="text-sm text-gray-300"><strong className="text-brand-300">{eq.symbol}</strong> · ₹{NUM(eq.ltp)} · {TF_LABEL[eq.timeframe]} · {eq.read?.bars} bars</div>
              <Report read={eq.read} />
            </>
          ) : <div className="bg-surface-2 border border-surface-3 rounded-xl p-10 text-center text-gray-500 text-sm">Type a stock and read its structure.</div>}
        </div>
      )}

      {tab === 'options' && (
        <div className="space-y-4">
          <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 flex flex-wrap items-end gap-3">
            <div><label className={lbl}>Index</label>
              <select value={index} onChange={(e) => setIndex(e.target.value)} className={sel}>
                {(meta.indices || ['NIFTY']).map((i) => <option key={i} value={i}>{i}</option>)}
              </select>
            </div>
            {tfPicker()}
            <div><label className={lbl}>Read</label>
              <select value={optMode} onChange={(e) => setOptMode(e.target.value)} className={sel}>
                <option value="index">Index only</option>
                <option value="premium">Index + option premium</option>
              </select>
            </div>
            {optMode === 'premium' && (
              <>
                <div><label className={lbl}>Type</label>
                  <select value={optType} onChange={(e) => setOptType(e.target.value)} className={sel}><option value="CE">CE</option><option value="PE">PE</option></select>
                </div>
                <div><label className={lbl}>Strike (blank = ATM)</label>
                  <input value={strike} onChange={(e) => setStrike(e.target.value)} placeholder="ATM" className={`${sel} w-28`} />
                </div>
                <div><label className={lbl}>Expiry</label>
                  <select value={expiryType} onChange={(e) => setExpiryType(e.target.value)} className={sel}><option value="weekly">Weekly</option><option value="monthly">Monthly</option></select>
                </div>
              </>
            )}
            {runButtons('options')}
          </div>

          {opt ? (
            <>
              <div className="text-sm text-gray-300"><strong className="text-brand-300">{opt.index}</strong> spot {NUM(opt.spot)} · {TF_LABEL[opt.timeframe]}</div>
              <Report read={opt.read} stance={opt.stance} />
              {opt.option && (
                <div className="space-y-3 pt-2 border-t border-surface-3">
                  <div className="flex flex-wrap items-center gap-3">
                    <span className="text-sm font-semibold text-gray-200">Premium read — {opt.option.tradingsymbol}</span>
                    <span className="text-xs text-gray-500">₹{NUM(opt.option.ltp)} · strike {opt.option.strike} {opt.option.opt_type} · exp {opt.option.expiry}</span>
                  </div>
                  {opt.confirmation && (
                    <div className={`text-xs rounded-lg px-3 py-2 border ${opt.confirmation.agree ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300' : 'bg-amber-500/10 border-amber-500/30 text-amber-300'}`}>
                      {opt.confirmation.note}
                    </div>
                  )}
                  <Report read={opt.option.read} />
                </div>
              )}
              {opt.option_error && <div className="text-xs text-amber-400/90">{opt.option_error}</div>}
            </>
          ) : <div className="bg-surface-2 border border-surface-3 rounded-xl p-10 text-center text-gray-500 text-sm">Read the index structure first — the option stance follows from it.</div>}
        </div>
      )}

      {tab === 'fno' && (
        <div className="space-y-4">
          <div className="bg-surface-2 border border-surface-3 rounded-xl p-4 flex flex-wrap items-end gap-3">
            {symbolInput('e.g. TATASTEEL')}
            {tfPicker()}
            {runButtons('fno')}
            <span className="text-[11px] text-gray-600">Stock + front-month future: open interest turns effort-vs-result into something you can measure.</span>
          </div>
          {fno ? (
            <>
              <div className="flex flex-wrap items-center gap-3 text-sm text-gray-300">
                <strong className="text-brand-300">{fno.symbol}</strong> ₹{NUM(fno.ltp)}
                <span className="text-xs text-gray-500">{fno.future?.tradingsymbol} · exp {fno.future?.expiry} · lot {fno.future?.lot_size}</span>
              </div>
              {fno.confirmation && (
                <div className={`text-xs rounded-lg px-3 py-2 border ${fno.confirmation.agree ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300' : 'bg-amber-500/10 border-amber-500/30 text-amber-300'}`}>
                  {fno.confirmation.note}
                </div>
              )}
              <Report read={fno.read} oi={fno.oi} />
              {fno.future_read && (
                <div className="pt-2 border-t border-surface-3 space-y-3">
                  <div className="text-sm font-semibold text-gray-200">Futures structure — {fno.future?.tradingsymbol}</div>
                  <Report read={fno.future_read} />
                </div>
              )}
            </>
          ) : <div className="bg-surface-2 border border-surface-3 rounded-xl p-10 text-center text-gray-500 text-sm">Type an F&amp;O stock to read cash and futures together.</div>}
        </div>
      )}

      {tab === 'learn' && (
        <div className="space-y-4">
          <div className="bg-surface-2 border border-surface-3 rounded-xl p-5 space-y-2 text-sm text-gray-300">
            <h3 className="font-semibold text-gray-100">The three laws</h3>
            <p><strong>Supply &amp; demand</strong> — price rises when demand exceeds supply. Everything below is a way of measuring which side is in control.</p>
            <p><strong>Cause &amp; effect</strong> — the sideways range is the cause; the move that follows is the effect. A range too small or too short cannot pay for the risk, which is why this desk refuses to call an entry on one.</p>
            <p><strong>Effort vs result</strong> — volume is effort, bar spread is result. Heavy volume that produces no progress at a range edge is absorption, and it is the single most useful tell Wyckoff left us.</p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2">
            {Object.entries(meta.events || {}).map(([k, v]) => (
              <div key={k} className="bg-surface-2 border border-surface-3 rounded-xl px-3 py-2.5 flex gap-2.5">
                <span className="mt-0.5 shrink-0 w-12 h-7 flex items-center justify-center text-[11px] font-bold rounded-lg border"
                  style={{ color: EV_COLOR[k] || '#9ca3af', borderColor: `${EV_COLOR[k] || '#9ca3af'}55`, background: `${EV_COLOR[k] || '#9ca3af'}15` }}>{k}</span>
                <div className="min-w-0">
                  <div className="text-xs font-semibold text-gray-200">{v.name} <span className="text-gray-600 font-normal">· {v.side === 'accum' ? 'accumulation' : 'distribution'}</span></div>
                  <div className="text-[11px] text-gray-500 leading-snug mt-0.5">{v.meaning}</div>
                </div>
              </div>
            ))}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {Object.entries(meta.phases || {}).map(([k, v]) => (
              <div key={k} className="bg-surface-2 border border-surface-3 rounded-xl px-3 py-2.5 flex gap-2.5">
                <span className="mt-0.5 shrink-0 w-8 h-7 flex items-center justify-center text-xs font-bold rounded-lg bg-brand-500/15 text-brand-300 border border-brand-500/25">{k}</span>
                <div className="text-[11px] text-gray-400">{v}</div>
              </div>
            ))}
          </div>
          <div className="bg-surface-2 border border-surface-3 rounded-xl p-5 text-sm text-gray-300 space-y-2">
            <h3 className="font-semibold text-gray-100">Where the money is — and isn't</h3>
            <p><strong className="text-emerald-400">Act</strong> in Phase C (a spring confirmed by its test) or Phase D (a sign of strength followed by the last point of support). Both give you a tight, structural invalidation level.</p>
            <p><strong className="text-red-400">Stand aside</strong> in Phase A (the trend has only been stopped) and Phase B (the middle of the range, where most traders bleed). Stand aside once price has run far out of the range — that is chasing, not Wyckoff.</p>
            <p className="text-[12px] text-gray-500">On options: a premium series decays, trades thin and is one-sided, so it can confirm a read but must never lead it. This desk reads the <strong>index</strong> for direction and shows the premium's own structure beside it as a cross-check.</p>
          </div>
        </div>
      )}
    </div>
  );
}
