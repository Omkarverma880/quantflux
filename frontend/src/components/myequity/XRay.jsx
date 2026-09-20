import React, { useCallback, useEffect, useState } from 'react';
import { X, Loader2, RefreshCw, Newspaper, AlertTriangle, ExternalLink } from 'lucide-react';
import { api } from '../../api';
import CandleChart from '../CandleChart';
import { N, PCT, COMPACT, signTone, Section, Stat, SensitivityMeter } from './ui';

/**
 * Everything known about one stock: the chart with its bands, the indicators that matter,
 * the zones price is heading into with what has historically happened there, and the news.
 */

const RAIL = { research: '#f59e0b', entry: '#22d3ee', stop: '#ef4444', target: '#10b981' };

function Card({ label, value, sub, tone = 'text-gray-100', blink = false, title }) {
  return (
    <div className={`rounded-lg border px-3 py-2 ${blink ? 'border-amber-500/40 bg-amber-500/10 row-blink' : 'border-surface-3 bg-surface-2/50'}`} title={title}>
      <div className="text-[10px] uppercase tracking-wider text-gray-500">{label}</div>
      <div className={`text-[15px] font-semibold mono ${tone}`}>{value}</div>
      {sub && <div className="text-[10.5px] text-gray-500">{sub}</div>}
    </div>
  );
}

function RangeBar({ low, high, value, label }) {
  if (low == null || high == null || value == null || high <= low) return null;
  const pos = Math.max(0, Math.min(100, ((value - low) / (high - low)) * 100));
  return (
    <div>
      <div className="flex justify-between text-[10px] text-gray-500 mono"><span>{N(low)}</span><span>{label}</span><span>{N(high)}</span></div>
      <div className="relative h-1.5 rounded-full bg-surface-4 mt-1">
        <div className="absolute inset-y-0 left-0 rounded-full bg-brand-500/40" style={{ width: `${pos}%` }} />
        <div className="absolute -top-0.5 w-1 h-2.5 rounded bg-brand-300" style={{ left: `calc(${pos}% - 2px)` }} />
      </div>
    </div>
  );
}

function StatsLine({ st, horizon }) {
  if (!st || !st.samples) return <span className="text-[11px] text-gray-500">no past trigger on this stock</span>;
  const tone = st.median_return > 0 ? 'text-emerald-400' : st.median_return < 0 ? 'text-red-400' : 'text-gray-400';
  return (
    <span className="text-[11px] text-gray-400">
      <span className="text-gray-200 mono">{st.samples}</span> triggers ·{' '}
      <span className={`mono ${tone}`}>{st.win_rate}%</span> higher after {horizon}d ·
      median <span className={`mono ${tone}`}>{PCT(st.median_return)}</span>
      {st.median_mae != null && <> · dip first <span className="mono text-gray-300">{PCT(st.median_mae)}</span></>}
      {!st.tested && <span className="ml-1 text-amber-500">· too few to trust</span>}
    </span>
  );
}

export default function XRay({ stockId, onClose }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [tab, setTab] = useState('daily');

  const load = useCallback(async (refresh = 1) => {
    setLoading(true); setErr('');
    try {
      const r = await api.meXray(stockId, { refresh });
      if (r.status !== 'ok') { setErr(r.message || 'could not load'); setData(null); }
      else setData(r);
    } catch (e) { setErr(String(e.message || e)); } finally { setLoading(false); }
  }, [stockId]);

  useEffect(() => { load(1); }, [load]);
  useEffect(() => {
    const esc = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', esc);
    return () => window.removeEventListener('keydown', esc);
  }, [onClose]);

  const s = data?.snapshot || {};
  const ext = data?.extremes || {};
  const entry = data?.entry || {};
  const chart = tab === 'intraday' ? data?.intraday : data?.daily;
  const rails = [];
  (data?.stock?.levels || []).forEach((lv) => rails.push({ price: lv, color: RAIL.research, label: 'level' }));
  if (entry.status === 'ok') {
    rails.push({ price: entry.entry_high, color: RAIL.entry, label: 'entry' });
    rails.push({ price: entry.stop, color: RAIL.stop, label: 'stop' });
    rails.push({ price: entry.target, color: RAIL.target, label: 'target' });
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/60" onClick={onClose}>
      <div className="w-full lg:w-[78%] xl:w-[70%] h-full bg-surface-0 border-l border-surface-3 overflow-y-auto"
        onClick={(e) => e.stopPropagation()}>
        {/* header */}
        <div className="sticky top-0 z-10 bg-surface-0/95 backdrop-blur border-b border-surface-3 px-4 py-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[18px] font-bold text-white">{data?.stock?.symbol || '…'}</span>
                <span className="text-[10px] text-gray-500 border border-surface-3 rounded px-1">{data?.stock?.exchange}</span>
                {data?.ltp != null && (
                  <>
                    <span className="text-[17px] font-semibold mono text-gray-100">{N(data.ltp)}</span>
                    <span className={`text-[12.5px] mono ${signTone(data.change_pct)}`}>{PCT(data.change_pct)}</span>
                  </>
                )}
              </div>
              <div className="text-[11.5px] text-gray-500 truncate">{data?.company || data?.stock?.company}</div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {data?.sensitivity && <SensitivityMeter sens={data.sensitivity} />}
              <button onClick={() => load(1)} disabled={loading} className="btn-secondary !py-1.5 !px-2.5 text-[12px] flex items-center gap-1.5">
                {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              </button>
              <button onClick={onClose} className="btn-secondary !py-1.5 !px-2.5 text-[12px]"><X className="w-3.5 h-3.5" /></button>
            </div>
          </div>
          {data?.stock?.note && <div className="text-[11.5px] text-gray-400 mt-1.5 italic">“{data.stock.note}”</div>}
        </div>

        {err && <div className="m-4 text-[12.5px] text-red-400 flex items-center gap-2"><AlertTriangle className="w-4 h-4" />{err}</div>}
        {loading && !data && <div className="p-10 text-center text-gray-500 text-[13px]"><Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />Building the X-ray…</div>}

        {data && (
          <div className="p-4 space-y-4">
            {/* indicator cards */}
            <div className="grid gap-2 grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
              <Card label="RSI (14)" value={s.rsi == null ? '—' : s.rsi.toFixed(1)}
                sub={s.rsi < 30 ? 'oversold' : s.rsi > 70 ? 'overbought' : 'neutral zone'}
                tone={s.rsi < 30 ? 'text-red-400' : s.rsi > 70 ? 'text-emerald-400' : 'text-gray-100'}
                blink={s.rsi != null && s.rsi < 30} />
              <Card label="200 EMA" value={N(s.ema200)} sub={`price ${PCT(s.vs_ema200)}`}
                tone={(s.vs_ema200 || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'} />
              <Card label="ADX (14)" value={s.adx == null ? '—' : s.adx.toFixed(1)}
                sub={`+DI ${N(s.plus_di, 1)} · −DI ${N(s.minus_di, 1)}`}
                tone={(s.adx || 0) >= 25 ? 'text-brand-300' : 'text-gray-400'}
                title="Above 25 the move has direction; below 20 it is chop." />
              <Card label="20-day VWAP" value={N(s.vwap)} sub={`band ${N(s.vwap_lower, 0)} – ${N(s.vwap_upper, 0)}`}
                tone={(s.vs_vwap || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'} />
              <Card label="Bollinger %B" value={s.bb_pctb == null ? '—' : `${s.bb_pctb.toFixed(0)}%`}
                sub={s.bb_squeeze ? 'squeeze — bands tight' : `${N(s.bb_lower, 0)} – ${N(s.bb_upper, 0)}`}
                tone={s.bb_pctb <= 0 ? 'text-red-400' : s.bb_pctb >= 100 ? 'text-emerald-400' : 'text-gray-100'} />
              <Card label="ATR (14)" value={N(s.atr)} sub={data.ltp ? `${((s.atr / data.ltp) * 100).toFixed(1)}% of price` : ''} />
            </div>

            {/* ranges */}
            <Section title="Where it sits">
              <div className="grid gap-4 md:grid-cols-2">
                <RangeBar low={ext.low_52w} high={ext.high_52w} value={data.ltp} label="52-week range" />
                <RangeBar low={ext.low_life} high={ext.high_life} value={data.ltp} label="lifetime range" />
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-3">
                <Stat label="52w high" value={N(ext.high_52w)} sub={PCT(ext.from_52w_high, 1)} />
                <Stat label="52w low" value={N(ext.low_52w)} sub={PCT(ext.from_52w_low, 1)} />
                <Stat label="Lifetime high" value={N(ext.high_life)} sub={ext.high_life_on} />
                <Stat label="Lifetime low" value={N(ext.low_life)} sub={ext.low_life_on} />
              </div>
              <div className="text-[11px] text-gray-500 mt-2">
                {ext.sessions?.toLocaleString('en-IN')} daily sessions from {ext.first_session}
                {data.cache?.last && ` · cached to ${data.cache.last}`}
              </div>
            </Section>

            {/* entry zone */}
            <Section title="AI entry zone" right={<span className="text-[10.5px] text-gray-500">measured on this stock’s own history</span>}>
              {entry.status !== 'ok' ? (
                <div className="text-[12.5px] text-gray-400">{entry.message || 'nothing to act on right now'}</div>
              ) : (
                <div className="space-y-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`px-2 py-0.5 rounded text-[11px] font-semibold border ${entry.waiting
                      ? 'bg-brand-500/10 text-brand-300 border-brand-500/30' : 'bg-amber-500/15 text-amber-500 border-amber-500/30 row-blink'}`}>
                      {entry.waiting ? 'waiting for price' : 'price is in the zone now'}
                    </span>
                    <span className="text-[12.5px] text-gray-200 font-semibold">{entry.zone?.label}</span>
                    {entry.tested
                      ? <span className="text-[10.5px] text-emerald-400 border border-emerald-500/30 rounded px-1.5 py-0.5">tested setup</span>
                      : <span className="text-[10.5px] text-amber-500 border border-amber-500/30 rounded px-1.5 py-0.5">untested</span>}
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    <Stat label="Entry zone" value={`${N(entry.entry_low)} – ${N(entry.entry_high)}`} tone="text-brand-300" />
                    <Stat label="Stop below" value={N(entry.stop)} sub={`risk ${PCT(-entry.risk_pct, 1)}`} tone="text-red-400" />
                    <Stat label="Target" value={N(entry.target)} sub="median favourable move" tone="text-emerald-400" />
                    <Stat label="Reward : risk" value={entry.rr == null ? '—' : `${entry.rr}×`} />
                  </div>
                  <ul className="space-y-1">
                    {(entry.reasons || []).map((t, i) => (
                      <li key={i} className="text-[12px] text-gray-300 flex gap-2"><span className="text-gray-600">•</span><span>{t}</span></li>
                    ))}
                  </ul>
                  <div className="text-[11px] text-gray-500">
                    Research, not advice — the odds are counts from this stock’s past, and the past is not the next trade.
                  </div>
                </div>
              )}
            </Section>

            {/* chart */}
            <Section title="Chart"
              right={(
                <div className="flex items-center gap-1">
                  {[['daily', 'Daily'], ['intraday', '15-min']].map(([k, l]) => (
                    <button key={k} onClick={() => setTab(k)}
                      className={`px-2 py-0.5 rounded text-[11.5px] font-semibold border ${tab === k
                        ? 'border-brand-500 bg-brand-500 text-white' : 'border-surface-3 text-gray-400 hover:text-gray-200'}`}>{l}</button>
                  ))}
                </div>
              )}>
              {tab === 'intraday' && !data.intraday?.available ? (
                <div className="py-8 text-center text-[12.5px] text-gray-500">{data.intraday?.message || 'no intraday candles'}</div>
              ) : (
                <>
                  <CandleChart candles={chart?.candles || []} overlays={chart?.overlays || []} rails={rails} height={320} />
                  <div className="flex flex-wrap gap-3 mt-2 text-[10.5px]">
                    {(chart?.overlays || []).map((o) => (
                      <span key={o.key} className="flex items-center gap-1 text-gray-400">
                        <span className="w-3 h-0.5 rounded" style={{ background: o.color }} />{o.label}
                      </span>
                    ))}
                    {!!(data.stock?.levels || []).length && (
                      <span className="flex items-center gap-1 text-gray-400">
                        <span className="w-3 h-0.5 rounded" style={{ background: RAIL.research }} />your research levels
                      </span>
                    )}
                  </div>
                </>
              )}
            </Section>

            {/* zones */}
            <Section title="Levels price is heading into">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] text-[12px]">
                  <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                    {['Zone', 'Range', 'Distance', 'What usually happened there'].map((h) => <th key={h} className="text-left px-2 py-1 font-medium">{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {(data.zones || []).map((z, i) => (
                      <tr key={`${z.kind}-${i}`} className={`border-b border-surface-3/40 ${z.inside ? 'bg-amber-500/5' : ''}`}>
                        <td className="px-2 py-1.5">
                          <div className="flex items-center gap-1.5">
                            {z.kind === 'research' && <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />}
                            <span className="text-gray-200">{z.label}</span>
                          </div>
                          <div className="text-[10.5px] text-gray-500">{z.why}</div>
                        </td>
                        <td className="px-2 py-1.5 mono text-gray-300 whitespace-nowrap">{N(z.low)} – {N(z.high)}</td>
                        <td className={`px-2 py-1.5 mono whitespace-nowrap ${z.distance_pct > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                          {z.inside ? <span className="text-amber-500 font-semibold">here now</span> : PCT(z.distance_pct)}
                        </td>
                        <td className="px-2 py-1.5"><StatsLine st={z.stats} horizon={data.horizon} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="text-[11px] text-gray-500 mt-2">
                The record belongs to the setup, not to the individual level: every swing support shares the
                “retest of a support” history, every resistance the “break above resistance” history.
              </div>
            </Section>

            {/* sensitivity breakdown + volume */}
            <div className="grid gap-4 lg:grid-cols-2">
              <Section title="Why it reads that way">
                <div className="space-y-1.5">
                  {(data.sensitivity?.parts || []).map((p) => (
                    <div key={p.key} className="flex items-center gap-2">
                      <span className="text-[11.5px] text-gray-400 w-44 shrink-0">{p.label}</span>
                      <span className="text-[11.5px] mono text-gray-200 w-16 text-right">{p.value == null ? '—' : `${p.value}${p.unit}`}</span>
                      <div className="flex-1 h-1.5 rounded-full bg-surface-4 relative min-w-0">
                        <div className={`absolute inset-y-0 rounded-full ${p.points >= 0 ? 'bg-emerald-500/70 left-1/2' : 'bg-red-500/70 right-1/2'}`}
                          style={{ width: `${Math.min(50, Math.abs(p.points) / p.weight * 50)}%` }} />
                        <div className="absolute inset-y-0 left-1/2 w-px bg-gray-600" />
                      </div>
                      <span className={`text-[11px] mono w-12 text-right ${p.points > 0 ? 'text-emerald-400' : p.points < 0 ? 'text-red-400' : 'text-gray-500'}`}>
                        {p.points > 0 ? '+' : ''}{p.points}
                      </span>
                    </div>
                  ))}
                </div>
              </Section>

              <Section title="Volume — latest against older">
                <div className="grid grid-cols-3 gap-3">
                  <Stat label="Latest session" value={COMPACT(data.volume?.latest)} />
                  <Stat label="5-day average" value={COMPACT(data.volume?.avg5)} sub={data.volume?.vs_avg5 ? `${data.volume.vs_avg5}×` : ''} />
                  <Stat label="20-day average" value={COMPACT(data.volume?.avg20)} sub={data.volume?.vs_avg20 ? `${data.volume.vs_avg20}×` : ''} />
                </div>
                <div className="flex items-end gap-1 h-20 mt-3">
                  {(data.volume?.bars || []).map((b, i, arr) => {
                    const max = Math.max(...arr.map((x) => x.volume || 0), 1);
                    return (
                      <div key={b.date} className="flex-1 h-full flex flex-col justify-end" title={`${b.date}: ${COMPACT(b.volume)}`}>
                        <div className={`${i === arr.length - 1 ? 'bg-brand-400' : 'bg-gray-600/70'} rounded-t`}
                          style={{ height: `${Math.max(3, (b.volume / max) * 100)}%` }} />
                      </div>
                    );
                  })}
                </div>
                <div className="text-[11px] text-gray-500 mt-2">
                  {data.volume?.buy_share_20d}% of the last 20 sessions’ volume traded on up days.
                </div>
              </Section>
            </div>

            {/* news */}
            <Section title={<span className="flex items-center gap-1.5"><Newspaper className="w-3.5 h-3.5" />Recent news</span>}
              right={data.news?.bias && <span className={`text-[10.5px] px-1.5 py-0.5 rounded border ${data.news.bias === 'Bullish'
                ? 'text-emerald-400 border-emerald-500/30' : data.news.bias === 'Bearish'
                  ? 'text-red-400 border-red-500/30' : 'text-gray-400 border-surface-3'}`}>{data.news.bias} headlines</span>}>
              {!data.news?.available ? (
                <div className="text-[12.5px] text-gray-500">{data.news?.message || 'no headlines found'}</div>
              ) : (
                <ul className="space-y-1.5">
                  {data.news.items.map((h, i) => (
                    <li key={i} className="flex items-start gap-2">
                      <span className={`mt-1 w-1.5 h-1.5 rounded-full shrink-0 ${h.tone === 'pos' ? 'bg-emerald-500' : h.tone === 'neg' ? 'bg-red-500' : 'bg-gray-600'}`} />
                      <a href={h.link} target="_blank" rel="noreferrer" className="text-[12.5px] text-gray-300 hover:text-brand-300 leading-snug">
                        {h.title}
                        <span className="text-[10.5px] text-gray-500 ml-1.5">{h.source}{h.age ? ` · ${h.age}` : ''}</span>
                        <ExternalLink className="w-3 h-3 inline ml-1 opacity-50" />
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </Section>
          </div>
        )}
      </div>
    </div>
  );
}
