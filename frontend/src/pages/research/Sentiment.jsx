import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  RefreshCw, Loader2, AlertCircle, AlertTriangle, Activity, TrendingUp, TrendingDown,
  Gauge, ListChecks, Lightbulb, Play, Pause, Settings2, Save, ChevronDown, ChevronUp, CheckCircle2,
  XCircle, Globe, Target,
} from 'lucide-react';
import { api } from '../../api';

const POS = '#34d399'; const NEG = '#f87171';
const GCOL = { macro: 'text-sky-400', derivative: 'text-violet-400', technical: 'text-amber-400' };
const GABBR = { macro: 'MAC', derivative: 'DRV', technical: 'TEC' };

const SENT = {
  'Strong Bullish': { c: 'text-emerald-400', bg: 'bg-emerald-500/15', ring: 'ring-emerald-500/50', dot: 'bg-emerald-400', icon: TrendingUp },
  Bullish:          { c: 'text-emerald-400', bg: 'bg-emerald-500/10', ring: 'ring-emerald-500/30', dot: 'bg-emerald-400', icon: TrendingUp },
  Neutral:          { c: 'text-gray-300',    bg: 'bg-gray-500/10',    ring: 'ring-gray-500/30',    dot: 'bg-gray-400',    icon: Activity },
  Bearish:          { c: 'text-red-400',     bg: 'bg-red-500/10',     ring: 'ring-red-500/30',     dot: 'bg-red-400',     icon: TrendingDown },
  'Strong Bearish': { c: 'text-red-400',     bg: 'bg-red-500/15',     ring: 'ring-red-500/50',     dot: 'bg-red-400',     icon: TrendingDown },
};
const sigColor = (s) => (s === 'Bullish' ? 'text-emerald-400' : s === 'Bearish' ? 'text-red-400' : 'text-gray-400');

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

function ScoreBar({ score, label }) {
  const s = Math.max(-10, Math.min(10, score || 0));
  const col = s >= 2 ? POS : s <= -2 ? NEG : '#94a3b8';
  const w = (Math.abs(s) / 10) * 50;
  const left = s >= 0 ? 50 : 50 - w;
  return (
    <div>
      <div className="flex items-center justify-between text-xs mb-1">
        <span className="text-gray-400">{label}</span>
        <span className="font-semibold" style={{ color: col }}>{s >= 0 ? '+' : ''}{s}</span>
      </div>
      <div className="h-2 bg-surface-3 rounded-full relative">
        <div className="absolute top-0 bottom-0 w-px bg-gray-600" style={{ left: '50%' }} />
        <div className="h-2 rounded-full absolute top-0" style={{ left: `${left}%`, width: `${w}%`, background: col }} />
      </div>
    </div>
  );
}

// Semicircular speedometer for the −10..+10 final score.
function GaugeMeter({ score = 0, blink }) {
  const cx = 100, cy = 100, r = 78;
  const s = Math.max(-10, Math.min(10, score || 0));
  const col = s >= 3 ? POS : s <= -3 ? NEG : '#cbd5e1';
  const ang = (v) => 90 - Math.max(-10, Math.min(10, v)) * 9;
  const polar = (rr, a) => { const k = (a * Math.PI) / 180; return [cx + rr * Math.cos(k), cy - rr * Math.sin(k)]; };
  const arc = (s0, s1, rr = r) => {
    const [x0, y0] = polar(rr, ang(s0)); const [x1, y1] = polar(rr, ang(s1));
    return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${rr} ${rr} 0 0 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
  };
  const [nx, ny] = polar(r - 14, ang(s));
  return (
    <svg viewBox="0 0 200 118" className="w-full max-w-[250px] mx-auto">
      <path d={arc(-10, -3)} stroke={NEG} strokeWidth="12" fill="none" strokeLinecap="round" opacity="0.85" />
      <path d={arc(-3, 3)} stroke="#94a3b8" strokeWidth="12" fill="none" opacity="0.45" />
      <path d={arc(3, 10)} stroke={POS} strokeWidth="12" fill="none" strokeLinecap="round" opacity="0.85" />
      <line x1={cx} y1={cy} x2={nx.toFixed(2)} y2={ny.toFixed(2)} stroke={col} strokeWidth="3.5" strokeLinecap="round" className={blink ? 'animate-pulse' : ''} />
      <circle cx={cx} cy={cy} r="6" fill={col} />
      <text x="20" y="115" fontSize="9" fill="#64748b" textAnchor="middle">−10</text>
      <text x="100" y="12" fontSize="9" fill="#64748b" textAnchor="middle">0</text>
      <text x="180" y="115" fontSize="9" fill="#64748b" textAnchor="middle">+10</text>
      <text x="100" y="90" fontSize="25" fontWeight="700" fill={col} textAnchor="middle">{s >= 0 ? '+' : ''}{s}</text>
    </svg>
  );
}

// Tailwind needs literal class names → map action colour to fixed classes.
const AMAP = {
  green: { bg: 'bg-emerald-500/10', br: 'border-emerald-500/40', tx: 'text-emerald-400', chip: 'bg-emerald-500/15 text-emerald-300', Icon: TrendingUp },
  red:   { bg: 'bg-red-500/10',     br: 'border-red-500/40',     tx: 'text-red-400',     chip: 'bg-red-500/15 text-red-300',         Icon: TrendingDown },
  amber: { bg: 'bg-amber-500/10',   br: 'border-amber-500/40',   tx: 'text-amber-300',   chip: 'bg-amber-500/15 text-amber-200',     Icon: Pause },
};

const cinp = 'w-full bg-surface-3 border border-surface-4 rounded-lg px-2.5 py-1 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60';

export default function Sentiment() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [okMsg, setOkMsg] = useState('');
  const [auto, setAuto] = useState(true);
  const [cfg, setCfg] = useState(null);
  const [cfgOpen, setCfgOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const timer = useRef(null);

  const load = useCallback(async (force = false, silent = false) => {
    if (!silent) setLoading(true);
    try {
      const r = await api.researchSentiment(force);
      if (r.status === 'ok') setData(r);
      else if (!silent) { setError(r.message || 'Failed'); setTimeout(() => setError(''), 4000); }
    } catch (e) { if (!silent) { setError(e.message || 'Failed'); setTimeout(() => setError(''), 4000); } }
    finally { if (!silent) setLoading(false); }
  }, []);

  useEffect(() => { load(true); }, [load]);
  useEffect(() => { api.researchSentimentConfig().then((r) => { if (r.status === 'ok') setCfg(r.config); }).catch(() => {}); }, []);
  useEffect(() => {
    if (timer.current) clearInterval(timer.current);
    if (auto) timer.current = setInterval(() => load(false, true), 60000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [auto, load]);

  const flash = (m) => { setOkMsg(m); setTimeout(() => setOkMsg(''), 3000); };
  const gv = (path) => path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), cfg);
  const sv = (path, val) => setCfg((prev) => {
    const c = structuredClone(prev || {}); let o = c; const ks = path.split('.');
    ks.slice(0, -1).forEach((k) => { o[k] = o[k] || {}; o = o[k]; });
    o[ks[ks.length - 1]] = val; return c;
  });
  const numIn = (path, step) => (
    <input type="number" step={step || 'any'} value={gv(path) ?? ''}
      onChange={(e) => sv(path, e.target.value === '' ? null : parseFloat(e.target.value))} className={cinp} />
  );
  const saveCfg = async () => {
    setSaving(true);
    try {
      const r = await api.researchSentimentConfigSave(cfg);
      if (r.status === 'ok') { setCfg(r.config); if (r.snapshot?.status === 'ok') setData(r.snapshot); flash('Config saved & sentiment recomputed.'); }
      else { setError(r.message || 'Save failed'); setTimeout(() => setError(''), 4000); }
    } catch (e) { setError(e.message || 'Save failed'); setTimeout(() => setError(''), 4000); }
    finally { setSaving(false); }
  };

  const meta = data ? (SENT[data.sentiment] || SENT.Neutral) : SENT.Neutral;
  const live = data?.market_status === 'Open';
  const Icon = meta.icon;
  const rows = data?.indicators || [];

  // Additive driver decomposition: each indicator's weighted push on the final
  // score = score × group_weight ÷ (indicators in that group). They sum to the
  // headline score, so the longest bars are what's actually moving sentiment.
  const drivers = useMemo(() => {
    if (!data) return null;
    const avail = (data.indicators || []).filter((r) => r.available && r.score);
    const counts = {};
    avail.forEach((r) => { counts[r.group] = (counts[r.group] || 0) + 1; });
    const w = data.weights || {};
    const list = avail.map((r) => ({
      name: r.indicator, group: r.group,
      contrib: (r.score || 0) * (w[r.group] || 0) / (counts[r.group] || 1),
    })).filter((d) => Math.abs(d.contrib) >= 0.005);
    const bull = list.filter((d) => d.contrib > 0).reduce((s, d) => s + d.contrib, 0);
    const bear = list.filter((d) => d.contrib < 0).reduce((s, d) => s + d.contrib, 0);
    const top = [...list].sort((a, b) => Math.abs(b.contrib) - Math.abs(a.contrib))
      .slice(0, 12).sort((a, b) => b.contrib - a.contrib);
    const maxAbs = Math.max(0.01, ...top.map((d) => Math.abs(d.contrib)));
    return { top, bull, bear, net: bull + bear, maxAbs };
  }, [data]);

  return (
    <div className="p-4 md:p-6 space-y-4 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold text-gray-100">Sentiment Analyzer</h1>
            <span className="px-2 py-0.5 rounded-full bg-brand-600/15 text-brand-400 text-xs font-semibold border border-brand-500/20">Research</span>
          </div>
          <p className="text-gray-500 text-sm mt-0.5">Global + domestic + derivative + technical → overall NIFTY sentiment, confidence &amp; trade bias.</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setAuto((a) => !a)} className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border ${auto ? 'bg-emerald-600/20 text-emerald-400 border-emerald-500/40' : 'bg-surface-3 text-gray-400 border-surface-4'}`}>
            {auto ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />} Live {auto ? 'ON' : 'OFF'}
          </button>
          <button onClick={() => load(true)} disabled={loading} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />} Refresh
          </button>
        </div>
      </div>

      {error && <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-4 py-2 text-red-400 text-sm"><AlertCircle className="w-4 h-4" /> {error}</div>}
      {okMsg && <div className="flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/30 rounded-lg px-4 py-2 text-emerald-400 text-sm"><CheckCircle2 className="w-4 h-4" /> {okMsg}</div>}

      {/* Event risk banner */}
      {data?.event_risk === 'High' && (
        <div className="flex items-center gap-2 bg-amber-500/10 border border-amber-500/40 rounded-lg px-4 py-2.5 text-amber-300 text-sm animate-pulse">
          <AlertTriangle className="w-4 h-4 shrink-0" /> <strong>High Event Risk Today</strong> — {data.event_label}
        </div>
      )}

      {!data && loading && <Card><div className="flex items-center justify-center gap-2 py-12 text-gray-400 text-sm"><Loader2 className="w-5 h-5 animate-spin" /> Reading the market…</div></Card>}

      {data && (
        <>
          {/* A. Summary + breakdown */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <div className={`rounded-xl border border-surface-3 p-5 ring-1 ${meta.ring} ${meta.bg} flex flex-col`}>
              <div className="flex items-center justify-between">
                <span className="text-[11px] uppercase tracking-wide text-gray-400">Market Sentiment</span>
                <span className={`flex items-center gap-1.5 text-[11px] ${live ? 'text-emerald-400' : 'text-gray-500'}`}>
                  <span className={`w-2 h-2 rounded-full ${live ? 'bg-emerald-400 animate-pulse' : 'bg-gray-500'}`} /> {data.market_status}
                </span>
              </div>
              <GaugeMeter score={data.final_score} blink={live} />
              <div className="flex items-center justify-center gap-2 -mt-1 mb-2">
                <span className={`w-3 h-3 rounded-full ${meta.dot} ${live ? 'animate-pulse' : ''}`} />
                <span className={`text-2xl font-extrabold ${meta.c} ${live ? 'animate-pulse' : ''}`}>{data.sentiment}</span>
                <Icon className={`w-6 h-6 ${meta.c}`} />
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-gray-400">Confidence <strong className="text-gray-100">{data.confidence}%</strong></span>
                <span className="text-gray-400">Score <strong className={meta.c}>{data.final_score >= 0 ? '+' : ''}{data.final_score}</strong></span>
              </div>
              <div className="mt-2 h-1.5 bg-surface-3 rounded-full overflow-hidden"><div className={`h-1.5 ${data.confidence >= 60 ? 'bg-emerald-400' : 'bg-amber-400'}`} style={{ width: `${data.confidence}%` }} /></div>
              <div className="text-[11px] text-gray-500 mt-2">Updated {data.updated_at}</div>
            </div>

            <Card title="Sentiment Breakdown" icon={Gauge}>
              <div className="space-y-3 pt-1">
                <ScoreBar score={data.macro_score} label={`Macro (${Math.round((data.weights?.macro || 0) * 100)}%)`} />
                <ScoreBar score={data.derivative_score} label={`Derivative (${Math.round((data.weights?.derivative || 0) * 100)}%)`} />
                <ScoreBar score={data.technical_score} label={`Technical (${Math.round((data.weights?.technical || 0) * 100)}%)`} />
                <div className="pt-1 border-t border-surface-3"><ScoreBar score={data.final_score} label="Final (weighted)" /></div>
              </div>
            </Card>

            <Card title="Trade Bias" icon={Lightbulb}>
              {data.trade_bias?.length ? (
                <ul className="space-y-1.5 text-sm text-gray-300">
                  {data.trade_bias.map((b, i) => <li key={i} className="flex items-start gap-2"><span className={meta.c}>•</span> {b}</li>)}
                </ul>
              ) : <p className="text-gray-500 text-sm">No bias rules configured.</p>}
            </Card>
          </div>

          {/* Action — what to actually do */}
          {data.action && (() => {
            const A = data.action; const ac = AMAP[A.color] || AMAP.amber; const AIcon = ac.Icon;
            return (
              <div className={`rounded-xl border ${ac.br} ${ac.bg} p-4 md:p-5`}>
                <div className="flex flex-col lg:flex-row lg:items-stretch gap-4">
                  {/* Decision badge */}
                  <div className="flex items-center gap-3 lg:w-72 shrink-0">
                    <div className={`flex items-center justify-center w-14 h-14 rounded-xl bg-surface-3/60 ${ac.tx} ${live ? 'animate-pulse' : ''}`}>
                      <AIcon className="w-7 h-7" />
                    </div>
                    <div>
                      <div className="text-[11px] uppercase tracking-wide text-gray-400 mb-0.5 flex items-center gap-1"><Target className="w-3 h-3" /> Suggested Action</div>
                      <div className={`text-xl font-extrabold ${ac.tx} leading-tight`}>{A.label}</div>
                      {A.strength !== '—' && <span className={`inline-block mt-1 px-2 py-0.5 rounded-full text-[10px] font-semibold ${ac.chip}`}>{A.strength} conviction</span>}
                    </div>
                  </div>
                  {/* Rationale + checklist */}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-gray-200 leading-relaxed mb-3">{A.headline}</p>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-5 gap-y-1.5">
                      {A.checklist?.map((c, i) => (
                        <div key={i} className="flex items-center gap-2 text-xs">
                          {c.ok ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 shrink-0" /> : <XCircle className="w-3.5 h-3.5 text-gray-500 shrink-0" />}
                          <span className={c.ok ? 'text-gray-300' : 'text-gray-500'}>{c.label}</span>
                          <span className={`ml-auto font-medium ${c.ok ? 'text-gray-200' : 'text-gray-500'}`}>{c.detail}</span>
                        </div>
                      ))}
                    </div>
                    {A.thresholds && (
                      <p className="text-[11px] text-gray-600 mt-3 pt-2 border-t border-surface-3/60">
                        Entry gate: directional |score| ≥ <b className="text-gray-400">{A.thresholds.moderate}</b> (strong ≥ {A.thresholds.strong}) · confidence ≥ <b className="text-gray-400">{A.thresholds.min_confidence}%</b> · India VIX &lt; <b className="text-gray-400">{A.thresholds.vix_block}</b>. Educational only — not financial advice.
                      </p>
                    )}
                  </div>
                </div>
              </div>
            );
          })()}

          {/* D. Reasoning */}
          <Card title="Reasoning" icon={ListChecks}>
            {data.reasons?.length ? (
              <div className="flex flex-wrap gap-2">
                {data.reasons.map((r, i) => <span key={i} className="px-2.5 py-1 rounded-lg bg-surface-3/50 text-xs text-gray-300 border border-surface-4">{r}</span>)}
              </div>
            ) : <p className="text-gray-500 text-sm">No standout drivers right now.</p>}
          </Card>

          {/* Global market sessions (IST) → link to India */}
          {data.markets?.length > 0 && (
            <Card title="Global Market Sessions (IST) → impact on India" icon={Globe}>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {data.markets.map((m, i) => (
                  <div key={i} className="flex items-start gap-2.5 rounded-lg border border-surface-3 bg-surface-3/30 px-3 py-2">
                    <span className={`mt-1 w-2.5 h-2.5 rounded-full shrink-0 ${m.status === 'Open' ? 'bg-emerald-400 animate-pulse' : 'bg-gray-600'}`} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-sm text-gray-200 font-medium truncate">{m.name}</span>
                        <span className={`text-[10px] font-semibold shrink-0 ${m.status === 'Open' ? 'text-emerald-400' : 'text-gray-500'}`}>{m.status}</span>
                      </div>
                      <div className="text-[11px] text-gray-500">{m.open}–{m.close} IST · {m.region}</div>
                      <div className="text-[11px] text-gray-600 mt-0.5 leading-snug">{m.relation}</div>
                    </div>
                  </div>
                ))}
              </div>
              <p className="text-[11px] text-gray-600 mt-2">Green = currently trading. Asian peers (Nikkei/KOSPI/Hang Seng) run alongside our morning; the US close &amp; GIFT Nifty drive the overnight/pre-open gap.</p>
            </Card>
          )}

          {/* B. Indicator table */}
          <Card title={`Indicators (${rows.filter((r) => r.available).length} live)`} icon={Activity}>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead><tr className="border-b border-surface-3 text-gray-500">
                  {['Group', 'Indicator', 'Value', 'Change %', 'Signal', 'Score'].map((h) => <th key={h} className="text-left font-medium pb-2 pr-3 whitespace-nowrap">{h}</th>)}
                </tr></thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i} className={`border-b border-surface-3/30 ${r.available ? '' : 'opacity-40'}`}>
                      <td className="py-1.5 pr-3 text-gray-500 capitalize">{r.group}</td>
                      <td className="py-1.5 pr-3 text-gray-200">{r.indicator}</td>
                      <td className="py-1.5 pr-3 text-gray-300">{r.value ?? '—'}</td>
                      <td className={`py-1.5 pr-3 ${r.change_pct == null ? 'text-gray-600' : r.change_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{r.change_pct == null ? '—' : `${r.change_pct >= 0 ? '+' : ''}${r.change_pct}%`}</td>
                      <td className={`py-1.5 pr-3 font-medium ${sigColor(r.signal)}`}>{r.signal}</td>
                      <td className={`py-1.5 pr-3 font-medium ${r.score >= 2 ? 'text-emerald-400' : r.score <= -2 ? 'text-red-400' : 'text-gray-400'}`}>{r.available ? (r.score >= 0 ? '+' : '') + r.score : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-[11px] text-gray-600 mt-2">Global indices/crude/yields/USDINR via Yahoo (best-effort); India VIX/NIFTY &amp; derivative (PCR/Max Pain/IV) via Zerodha. FII/DII auto-fetched from NSE; GIFT Nifty auto-derived from the near-month NIFTY future (tag shows <code>auto</code>/<code>manual</code>). Greyed rows = data unavailable — override either in the config below.</p>
          </Card>

          {/* Score drivers — what's actually moving sentiment */}
          {drivers && drivers.top.length > 0 && (
            <Card title="Score Drivers — what's moving NIFTY sentiment" icon={Activity}
              right={<span className="text-[11px] text-gray-500 whitespace-nowrap">Bulls <b className="text-emerald-400">+{drivers.bull.toFixed(2)}</b> · Bears <b className="text-red-400">{drivers.bear.toFixed(2)}</b> · Net <b className={drivers.net >= 0 ? 'text-emerald-400' : 'text-red-400'}>{drivers.net >= 0 ? '+' : ''}{drivers.net.toFixed(2)}</b></span>}>
              <div className="space-y-1.5">
                {drivers.top.map((d, i) => {
                  const pos = d.contrib >= 0;
                  const wp = Math.min(50, (Math.abs(d.contrib) / drivers.maxAbs) * 50);
                  return (
                    <div key={i} className="flex items-center gap-2 text-xs">
                      <span className={`w-8 shrink-0 text-[9px] font-bold ${GCOL[d.group] || 'text-gray-500'}`}>{GABBR[d.group] || ''}</span>
                      <span className="w-32 sm:w-44 shrink-0 truncate text-gray-300 text-right">{d.name}</span>
                      <div className="flex-1 h-4 bg-surface-3/40 rounded relative min-w-[80px]">
                        <div className="absolute top-0 bottom-0 w-px bg-gray-600" style={{ left: '50%' }} />
                        <div className="absolute top-1 bottom-1 rounded-sm" style={{ [pos ? 'left' : 'right']: '50%', width: `${wp}%`, background: pos ? POS : NEG }} />
                      </div>
                      <span className={`w-12 shrink-0 text-right font-semibold ${pos ? 'text-emerald-400' : 'text-red-400'}`}>{pos ? '+' : ''}{d.contrib.toFixed(2)}</span>
                    </div>
                  );
                })}
              </div>
              <p className="text-[11px] text-gray-600 mt-3">Each bar is that indicator's weighted push on the final score (group weight ÷ indicators in group) — they add up to the headline score, so the longest bars are what you're really trading. <span className="text-sky-400 font-semibold">MAC</span> macro · <span className="text-violet-400 font-semibold">DRV</span> derivative · <span className="text-amber-400 font-semibold">TEC</span> technical.</p>
            </Card>
          )}

          {/* Config editor */}
          {cfg && (
            <Card title="Engine Config (live-editable)" icon={Settings2}
              right={<button onClick={() => setCfgOpen((o) => !o)} className="text-gray-500 hover:text-gray-300">{cfgOpen ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}</button>}>
              {cfgOpen && (
                <div className="space-y-4">
                  <div>
                    <div className="text-[11px] text-gray-500 uppercase tracking-wide mb-1.5">Group Weights (should sum to 1.0)</div>
                    <div className="grid grid-cols-3 gap-2">
                      <label className="text-xs text-gray-400">Macro{numIn('weights.macro', '0.05')}</label>
                      <label className="text-xs text-gray-400">Derivative{numIn('weights.derivative', '0.05')}</label>
                      <label className="text-xs text-gray-400">Technical{numIn('weights.technical', '0.05')}</label>
                    </div>
                    <div className={`text-[11px] mt-1 ${Math.abs((gv('weights.macro') || 0) + (gv('weights.derivative') || 0) + (gv('weights.technical') || 0) - 1) < 0.001 ? 'text-gray-600' : 'text-amber-400'}`}>
                      sum = {((gv('weights.macro') || 0) + (gv('weights.derivative') || 0) + (gv('weights.technical') || 0)).toFixed(2)}
                    </div>
                  </div>
                  <div>
                    <div className="text-[11px] text-gray-500 uppercase tracking-wide mb-1.5">Classification thresholds (final score)</div>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                      <label className="text-xs text-gray-400">Strong Bull ≥{numIn('classification.strong_bull')}</label>
                      <label className="text-xs text-gray-400">Bull ≥{numIn('classification.bull')}</label>
                      <label className="text-xs text-gray-400">Neutral &gt;{numIn('classification.neutral')}</label>
                      <label className="text-xs text-gray-400">Bear &gt;{numIn('classification.bear')}</label>
                    </div>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div>
                      <div className="text-[11px] text-gray-500 uppercase tracking-wide mb-1.5">India VIX thresholds</div>
                      <div className="grid grid-cols-3 gap-2">
                        <label className="text-xs text-gray-400">High{numIn('vix.high')}</label>
                        <label className="text-xs text-gray-400">Elevated{numIn('vix.elevated')}</label>
                        <label className="text-xs text-gray-400">Low{numIn('vix.low')}</label>
                      </div>
                    </div>
                    <div>
                      <div className="text-[11px] text-gray-500 uppercase tracking-wide mb-1.5">Confidence range (%)</div>
                      <div className="grid grid-cols-2 gap-2">
                        <label className="text-xs text-gray-400">Min{numIn('confidence.min')}</label>
                        <label className="text-xs text-gray-400">Max{numIn('confidence.max')}</label>
                      </div>
                    </div>
                  </div>
                  <div>
                    <div className="text-[11px] text-gray-500 uppercase tracking-wide mb-1.5">Entry gate (Buy CE / PE / Wait)</div>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                      <label className="text-xs text-gray-400">Strong |score| ≥{numIn('action.strong_score', '0.5')}</label>
                      <label className="text-xs text-gray-400">Min |score| ≥{numIn('action.moderate_score', '0.5')}</label>
                      <label className="text-xs text-gray-400">Min confidence %{numIn('action.min_confidence')}</label>
                      <label className="text-xs text-gray-400">VIX block ≥{numIn('action.high_vix_block')}</label>
                    </div>
                    <p className="text-[11px] text-gray-600 mt-1">Below Min |score| or below Min confidence → <b>Wait</b>. At/above Strong with calm VIX &amp; no event → <b>Strong</b> conviction.</p>
                  </div>
                  <div>
                    <div className="text-[11px] text-gray-500 uppercase tracking-wide mb-1.5">Manual overrides (blank = auto-fetch)</div>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
                      <label className="text-xs text-gray-400">FII net (₹cr){numIn('fii_dii.fii_net_cr')}</label>
                      <label className="text-xs text-gray-400">DII net (₹cr){numIn('fii_dii.dii_net_cr')}</label>
                      <label className="text-xs text-gray-400">GIFT Nifty chg %{numIn('gift_nifty_change_pct', '0.1')}</label>
                    </div>
                    <p className="text-[11px] text-gray-600 mt-1">FII/DII blank → auto-fetched from NSE. GIFT Nifty has no free feed, so set it manually pre-open.</p>
                  </div>
                  <div className="flex justify-end">
                    <button onClick={saveCfg} disabled={saving} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">
                      {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} Save &amp; Recompute
                    </button>
                  </div>
                </div>
              )}
            </Card>
          )}
        </>
      )}
    </div>
  );
}
