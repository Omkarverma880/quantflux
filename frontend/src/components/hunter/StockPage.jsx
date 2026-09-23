import React, { useEffect, useState } from 'react';
import { X, Check, Minus, Loader2, ExternalLink } from 'lucide-react';
import { api } from '../../api';
import CandleChart from './CandleChart';
import { MEASURES } from './StockGrid';
import { Measure, N, N0, PCT, RS_, RsPill, STAGE_STYLE, tone } from './ui';

/**
 * One stock, full size: the chart with its base, ceiling, moving averages and past breakouts, and
 * every measure and check behind the setup. Opened from "Tech chart" on a card.
 */
const RANGES = [['6 months', 130, 'day'], ['1 year', 250, 'day'], ['2 years', 500, 'day'], ['5 years, weekly', 1250, 'week']];

export default function StockPage({ symbol, onClose }) {
  const [row, setRow] = useState(null);
  const [chart, setChart] = useState(null);
  const [range, setRange] = useState(1);
  const [err, setErr] = useState('');

  useEffect(() => {
    if (!symbol) return;
    setRow(null);
    api.huStock(symbol).then((r) => (r.status === 'ok' ? setRow(r.stock) : setErr(r.message)));
  }, [symbol]);

  useEffect(() => {
    if (!symbol) return;
    setChart(null);
    const [, bars, tf] = RANGES[range];
    api.huChart(symbol, bars, tf).then((c) => (c.status === 'ok' ? setChart(c.chart) : setErr(c.message)));
  }, [symbol, range]);

  if (!symbol) return null;
  const st = STAGE_STYLE[row?.stage] || STAGE_STYLE.PLAYED_OUT;
  const day = row?.day || {};
  const bo = row?.breakout || {};

  return (
    <div className="fixed inset-0 z-50 bg-black/70 p-2 sm:p-6 overflow-y-auto" onClick={onClose}>
      <div className="max-w-[1180px] mx-auto card !p-4 space-y-3" onClick={(e) => e.stopPropagation()}>
        {!row ? (
          <div className="py-16 text-center text-gray-500 text-sm">
            {err ? <span className="text-red-400">{err}</span> : <><Loader2 className="w-5 h-5 animate-spin mx-auto mb-2" />Loading {symbol}…</>}
          </div>
        ) : (
          <>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <h2 className="text-[19px] font-bold text-white truncate">{row.name || row.symbol}</h2>
                  <span className="text-[12px] text-gray-500">{row.symbol}</span>
                  <RsPill value={row.rs_rating} />
                  <span className={`px-1.5 py-0.5 rounded text-[10.5px] font-bold ${st.ring} ${st.text}`}>
                    {row.stage.replace('_', ' ')}
                  </span>
                </div>
                <div className="text-[11.5px] text-gray-500">{row.industry} · {row.exchange}</div>
              </div>
              <div className="flex items-start gap-3">
                <div className="text-right">
                  <div className="text-[19px] font-semibold mono text-gray-100">{RS_(row.close)}</div>
                  <div className={`text-[12px] mono ${tone(day.change_pct)}`}>{PCT(day.change_pct)}</div>
                </div>
                <button onClick={onClose} className="text-gray-500 hover:text-gray-200"><X className="w-5 h-5" /></button>
              </div>
            </div>

            <div className="text-[12.5px] text-gray-300">{row.why}</div>
            <div className="text-[11px] text-gray-500 mono">
              {day.date} · O {N(day.open)} H {N(day.high)} L {N(day.low)} C {N(day.close)} · Vol {N0((day.volume || 0) / 1000)}K
            </div>

            <div className="flex items-center gap-1.5">
              {RANGES.map(([label], i) => (
                <button key={label} onClick={() => setRange(i)}
                  className={`px-2 py-1 rounded-lg border text-[11.5px] ${range === i
                    ? 'border-brand-500/60 bg-brand-500/15 text-brand-300' : 'border-surface-3 text-gray-400 hover:text-gray-200'}`}>
                  {label}
                </button>
              ))}
              <div className="ml-auto flex items-center gap-3 text-[10.5px] text-gray-500">
                <span className="flex items-center gap-1"><span className="w-3 h-px bg-[#60a5fa] inline-block" />50-day</span>
                <span className="flex items-center gap-1"><span className="w-3 h-px bg-[#f59e0b] inline-block" />150-day</span>
                <span className="flex items-center gap-1"><span className="w-3 h-px bg-[#a78bfa] inline-block" />200-day</span>
                <span className="flex items-center gap-1"><span className="w-3 h-px bg-[#10b981] inline-block" style={{ borderTop: '1px dashed' }} />ceiling</span>
              </div>
            </div>

            <div className="rounded-xl border border-surface-3 bg-surface-2/20 p-2">
              {chart ? <CandleChart chart={chart} height={420} showMas /> :
                <div className="h-[420px] flex items-center justify-center text-[12px] text-gray-600">loading the chart…</div>}
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
              {MEASURES.map(([k, label, tip, fn]) => (
                <div key={k} title={tip} className="cursor-help">
                  <Measure label={label} value={fn(row)} />
                </div>
              ))}
            </div>

            <div className="grid lg:grid-cols-2 gap-3">
              <div className="rounded-xl border border-surface-3 p-3">
                <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-1.5">Why it qualifies</div>
                {(row.trend_checks || []).map((c, i) => (
                  <div key={i} className="flex items-start gap-1.5 py-0.5">
                    {c.passed ? <Check className="w-3.5 h-3.5 text-emerald-400 mt-0.5 shrink-0" />
                      : <Minus className="w-3.5 h-3.5 text-gray-600 mt-0.5 shrink-0" />}
                    <span className="text-[11.5px] text-gray-300">{c.label}</span>
                    <span className="text-[11px] text-gray-500 mono ml-auto">{c.detail}</span>
                  </div>
                ))}
                <div className="flex flex-wrap gap-1 mt-2">
                  {(row.screen_hits || []).map((k) => (
                    <span key={k} className="px-1.5 py-px rounded border border-brand-500/30 bg-brand-500/10 text-[10.5px] text-brand-300">
                      {k.replace('_', ' ')}
                    </span>
                  ))}
                </div>
              </div>

              <div className="rounded-xl border border-surface-3 p-3">
                <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-1.5">
                  Breakouts this year {row.bases_this_year ? `· ${row.bases_this_year}` : ''}
                </div>
                {!(chart?.history || []).length ? (
                  <div className="text-[11.5px] text-gray-500 py-2">No breakout has cleared a ceiling this year.</div>
                ) : (
                  <table className="w-full text-[11.5px]">
                    <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500">
                      <th className="text-left py-0.5 font-medium">When</th>
                      <th className="text-right py-0.5 font-medium">Broke at</th>
                      <th className="text-right py-0.5 font-medium">Since then</th>
                    </tr></thead>
                    <tbody>
                      {(chart.history || []).map((h, i) => (
                        <tr key={i} className="border-t border-surface-3/40">
                          <td className="py-1 mono text-gray-300">{h.date}</td>
                          <td className="py-1 mono text-right text-gray-300">{RS_(h.price)}</td>
                          <td className={`py-1 mono text-right ${tone(((row.close / h.price) - 1) * 100)}`}>
                            {PCT(((row.close / h.price) - 1) * 100)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {bo.date && (
                  <div className="text-[11px] text-gray-500 mt-2">
                    Latest: broke out {bo.date} at {RS_(bo.price)} on {bo.volume_x}× volume · stop {RS_(bo.stop)} ·
                    {' '}peak {PCT(bo.peak_gain_pct)} · {bo.alive ? 'still alive' : 'stop lost'}
                  </div>
                )}
              </div>
            </div>

            <div className="text-[11px] text-gray-500 flex items-center gap-1">
              <ExternalLink className="w-3 h-3" />
              Screener view only — nothing here places an order.
            </div>
          </>
        )}
      </div>
    </div>
  );
}
