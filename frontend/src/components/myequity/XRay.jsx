import React, { useCallback, useEffect, useState } from 'react';
import {
  X, Loader2, RefreshCw, Newspaper, AlertTriangle, ExternalLink, ArrowUp, ArrowDown,
} from 'lucide-react';
import { api } from '../../api';
import {
  N, PCT, COMPACT, DAYS, signTone, Section, Stat, SensitivityMeter, CategoryChip, Empty,
} from './ui';

/**
 * The X-ray: everything about one stock that is worth knowing before you act on it.
 *
 * No price chart by design — your broker draws better ones. What lives here is what a chart
 * cannot tell you: what your own levels have actually done, what the business earns, how the
 * stock behaves, who is queued in the order book, and where price is heading next.
 */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
/** Money the way Indian filings quote it: lakh crore, crore, lakh. */
const RUPEE = (v) => {
  if (v == null) return '—';
  const n = Math.abs(Number(v));
  if (!Number.isFinite(n)) return '—';
  if (n >= 1e12) return `₹ ${(v / 1e12).toFixed(2)} lakh Cr`;   // 1 lakh crore = 1e12
  if (n >= 1e7) return `₹ ${(v / 1e7).toLocaleString('en-IN', { maximumFractionDigits: 0 })} Cr`;
  return `₹ ${COMPACT(v)}`;
};
const RATIO = (v, suffix = '', d = 2) => (v == null ? '—' : `${Number(v).toFixed(d)}${suffix}`);

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
      {!st.tested && <span className="ml-1 text-amber-500">· too few to trust</span>}
    </span>
  );
}

/** Your levels: what triggered, when, and what it has earned since. */
function LevelLedger({ watch, horizon }) {
  const rows = watch?.rows || [];
  if (!rows.length) return <Empty title="No research levels on this stock yet" hint="Add them from the table to start tracking." />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-[12px]">
        <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
          {['Level', 'Status', 'Triggered', 'Held', 'P&L now', 'Best', 'Worst'].map((h) => <th key={h} className="text-left px-2 py-1 font-medium">{h}</th>)}
        </tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.level} className={`border-b border-surface-3/40 ${r.near ? 'bg-amber-500/10' : ''}`}>
              <td className="px-2 py-1.5 mono text-gray-100">
                {N(r.level)}
                {r.track === false && <span className="ml-1.5 text-[9.5px] text-gray-500 border border-surface-4 rounded px-1">not tracked</span>}
              </td>
              <td className="px-2 py-1.5">
                {r.triggered
                  ? <span className="text-[11px] text-emerald-400">triggered</span>
                  : r.near ? <span className="text-[11px] text-amber-500 font-semibold">at it now</span>
                    : (
                      <span className={`text-[11px] inline-flex items-center gap-0.5 ${r.side === 'above' ? 'text-emerald-400' : 'text-red-400'}`}>
                        {r.side === 'above' ? <ArrowUp className="w-3 h-3" /> : <ArrowDown className="w-3 h-3" />}
                        price {Math.abs(r.distance_pct).toFixed(1)}% {r.side}
                      </span>
                    )}
              </td>
              <td className="px-2 py-1.5 mono text-gray-300">{r.triggered_on || '—'}</td>
              <td className="px-2 py-1.5 text-gray-400">{r.triggered ? `${DAYS(r.days_since)} · ${r.sessions_since} sessions` : '—'}</td>
              <td className={`px-2 py-1.5 mono font-semibold ${signTone(r.pnl_pct)}`}>
                {r.triggered ? `${PCT(r.pnl_pct)}` : '—'}
                {r.triggered && r.pnl_per_share != null && (
                  <span className="text-[10px] text-gray-500 ml-1">{r.pnl_per_share > 0 ? '+' : ''}{N(r.pnl_per_share)}/sh</span>
                )}
              </td>
              <td className="px-2 py-1.5 mono text-emerald-400/80">{r.triggered ? PCT(r.max_gain_pct) : '—'}</td>
              <td className="px-2 py-1.5 mono text-red-400/80">{r.triggered ? PCT(r.max_drawdown_pct) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {watch.primary && (
        <div className="text-[11px] text-gray-500 mt-2">
          The P&L runs from the level to the last trade — the trade you would have had if you bought your own
          level on the day price reached it. {watch.extra?.length ? 'A second tracked level that triggered keeps its own row and its own numbers.' : ''}
        </div>
      )}
    </div>
  );
}

function OrderBook({ book }) {
  if (!book?.available) {
    return <Empty title="The order book is closed" hint="It fills in during market hours, straight from your Zerodha feed." />;
  }
  const maxQty = Math.max(...[...book.buy, ...book.sell].map((r) => r.quantity || 0), 1);
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="Best bid" value={N(book.bid)} tone="text-emerald-400" />
        <Stat label="Best ask" value={N(book.ask)} tone="text-red-400" />
        <Stat label="Spread" value={`${N(book.spread)}`} sub={PCT(book.spread_pct, 2)} />
        <Stat label="Buy pressure" value={book.pressure == null ? '—' : `${book.pressure}%`}
          sub={`${COMPACT(book.buy_quantity)} vs ${COMPACT(book.sell_quantity)}`}
          tone={(book.pressure || 50) >= 55 ? 'text-emerald-400' : (book.pressure || 50) <= 45 ? 'text-red-400' : 'text-gray-100'} />
      </div>
      <div className="grid grid-cols-2 gap-3">
        {[['buy', 'Bids', 'emerald'], ['sell', 'Offers', 'red']].map(([k, label, colour]) => (
          <div key={k}>
            <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">{label}</div>
            {book[k].map((r, i) => (
              <div key={i} className="relative flex justify-between text-[11.5px] mono px-1.5 py-0.5">
                <div className={`absolute inset-y-0 ${k === 'buy' ? 'right-0' : 'left-0'} ${colour === 'emerald' ? 'bg-emerald-500/15' : 'bg-red-500/15'} rounded`}
                  style={{ width: `${((r.quantity || 0) / maxQty) * 100}%` }} />
                <span className={`relative ${colour === 'emerald' ? 'text-emerald-300' : 'text-red-300'}`}>{N(r.price)}</span>
                <span className="relative text-gray-400">{COMPACT(r.quantity)}<span className="text-gray-600 ml-1">×{r.orders}</span></span>
              </div>
            ))}
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-x-4 text-[11px] text-gray-500">
        {book.average_price != null && <span>Day VWAP {N(book.average_price)}</span>}
        {book.upper_circuit != null && <span>Circuit {N(book.lower_circuit, 0)} – {N(book.upper_circuit, 0)}</span>}
        {book.last_trade_time && <span>Last trade {String(book.last_trade_time).slice(0, 19)}</span>}
      </div>
    </div>
  );
}

function Fundamentals({ f, ltp }) {
  if (!f?.available) {
    return <Empty title="Company data is not available" hint={f?.message || 'The public source did not answer for this symbol.'} />;
  }
  const upside = f.target_mean && ltp ? ((f.target_mean - ltp) / ltp) * 100 : null;
  const cells = [
    ['Market cap', RUPEE(f.market_cap)],
    ['P/E (TTM)', RATIO(f.pe), f.forward_pe ? `forward ${RATIO(f.forward_pe)}` : ''],
    ['EPS (TTM)', RATIO(f.eps), f.forward_eps ? `forward ${RATIO(f.forward_eps)}` : ''],
    ['Book value', RATIO(f.book_value), f.pb ? `P/B ${RATIO(f.pb)}` : ''],
    ['Revenue (TTM)', RUPEE(f.revenue), f.revenue_growth != null ? `growth ${PCT(f.revenue_growth, 1)}` : ''],
    ['Profit margin', f.profit_margin == null ? '—' : `${f.profit_margin}%`, f.operating_margin != null ? `operating ${f.operating_margin}%` : ''],
    ['Return on equity', f.roe == null ? '—' : `${f.roe}%`, f.roa != null ? `on assets ${f.roa}%` : ''],
    ['Debt / equity', RATIO(f.debt_to_equity, '', 1), f.current_ratio ? `current ratio ${RATIO(f.current_ratio, '', 2)}` : ''],
    ['Dividend yield', f.dividend_yield == null ? '—' : `${f.dividend_yield}%`, f.payout_ratio != null ? `payout ${f.payout_ratio}%` : ''],
    ['Beta', RATIO(f.beta), 'vs the market'],
    ['Promoter / insider', f.held_insiders == null ? '—' : `${f.held_insiders}%`, f.held_institutions != null ? `institutions ${f.held_institutions}%` : ''],
    ['Free cash flow', RUPEE(f.free_cashflow), f.total_debt ? `debt ${RUPEE(f.total_debt)}` : ''],
  ];
  return (
    <div className="space-y-3">
      <div className="grid gap-2 grid-cols-2 sm:grid-cols-3 lg:grid-cols-4">
        {cells.map(([label, value, sub]) => <Card key={label} label={label} value={value} sub={sub} />)}
      </div>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-[11.5px] text-gray-400">
        {f.target_mean != null && (
          <span>Analyst target <span className="mono text-gray-100">{N(f.target_mean)}</span>
            {upside != null && <span className={`ml-1 mono ${signTone(upside)}`}>({PCT(upside, 1)})</span>}
            {f.analysts ? <span className="text-gray-500 ml-1">from {f.analysts} analysts</span> : null}
          </span>
        )}
        {f.recommendation && <span>Consensus <span className="text-gray-100 capitalize">{f.recommendation.replace('_', ' ')}</span></span>}
        {f.earnings_date && <span>Next results <span className="mono text-gray-100">{f.earnings_date}</span></span>}
        {f.ex_dividend_date && <span>Ex-dividend <span className="mono text-gray-100">{f.ex_dividend_date}</span></span>}
        {f.employees && <span>{Number(f.employees).toLocaleString('en-IN')} employees</span>}
      </div>
      {f.summary && <p className="text-[11.5px] text-gray-400 leading-relaxed">{f.summary}</p>}
      <div className="text-[10.5px] text-gray-600">Source: {f.source}{f.fetched_at ? ` · ${f.fetched_at}` : ''}</div>
    </div>
  );
}

export default function XRay({ stockId, onClose, onChanged }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [savingDate, setSavingDate] = useState(false);

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

  const setDate = async (value) => {
    setSavingDate(true);
    try {
      await api.meUpdate(stockId, { added_on: value });
      onChanged?.();
      await load(1);
    } finally { setSavingDate(false); }
  };

  const setCategory = async () => {
    const next = data?.category === 'INVESTMENT' ? 'SWING' : 'INVESTMENT';
    await api.meUpdate(stockId, { category: next });
    onChanged?.();
    load(1);
  };

  const s = data?.snapshot || {};
  const ext = data?.extremes || {};
  const entry = data?.entry || {};
  const risk = data?.risk || {};
  const perf = data?.performance || {};

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/60" onClick={onClose}>
      <div className="w-full lg:w-[80%] xl:w-[72%] h-full bg-surface-0 border-l border-surface-3 overflow-y-auto"
        onClick={(e) => e.stopPropagation()}>
        <div className="sticky top-0 z-10 bg-surface-0/95 backdrop-blur border-b border-surface-3 px-4 py-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[18px] font-bold text-white">{data?.stock?.symbol || '…'}</span>
                <span className="text-[10px] text-gray-500 border border-surface-3 rounded px-1">{data?.stock?.exchange}</span>
                {data && <CategoryChip category={data.category} onClick={setCategory} />}
                {data?.ltp != null && (
                  <>
                    <span className="text-[17px] font-semibold mono text-gray-100">{N(data.ltp)}</span>
                    <span className={`text-[12.5px] mono ${signTone(data.change_pct)}`}>{PCT(data.change_pct)}</span>
                  </>
                )}
              </div>
              <div className="text-[11.5px] text-gray-500 truncate">
                {data?.company || data?.stock?.company}
                {data?.stock?.sector ? ` · ${data.stock.sector}` : ''}
                {data?.stock?.industry ? ` · ${data.stock.industry}` : ''}
              </div>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {data?.sensitivity && <SensitivityMeter sens={data.sensitivity} />}
              <button onClick={() => load(1)} disabled={loading} className="btn-secondary !py-1.5 !px-2.5 text-[12px]">
                {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              </button>
              <button onClick={onClose} className="btn-secondary !py-1.5 !px-2.5 text-[12px]"><X className="w-3.5 h-3.5" /></button>
            </div>
          </div>
          {data && (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-1.5 text-[11.5px] text-gray-400">
              <label className="flex items-center gap-1.5">
                <span className="text-gray-500">Researched on</span>
                <input type="date" value={data.stock.added_on || ''} max={new Date().toISOString().slice(0, 10)}
                  onChange={(e) => setDate(e.target.value)} disabled={savingDate}
                  className="input-field !py-0.5 !px-1.5 text-[11.5px]" />
                {savingDate && <Loader2 className="w-3 h-3 animate-spin" />}
              </label>
              {data.stock.note && <span className="italic">“{data.stock.note}”</span>}
            </div>
          )}
        </div>

        {err && <div className="m-4 text-[12.5px] text-red-400 flex items-center gap-2"><AlertTriangle className="w-4 h-4" />{err}</div>}
        {loading && !data && <div className="p-10 text-center text-gray-500 text-[13px]"><Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />Building the X-ray…</div>}

        {data && (
          <div className="p-4 space-y-4">
            <Section title="Your research levels"
              right={<span className="text-[10.5px] text-gray-500">P&L measured from the level, per share</span>}>
              <LevelLedger watch={data.watch} horizon={data.horizon} />
            </Section>

            <div className="grid gap-2 grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
              <Card label="RSI (14)" value={s.rsi == null ? '—' : s.rsi.toFixed(1)}
                sub={s.rsi <= 30 ? 'heavily sold off' : s.rsi >= 80 ? 'very stretched' : 'neutral zone'}
                tone={s.rsi <= 30 ? 'text-emerald-400' : s.rsi >= 80 ? 'text-red-400' : 'text-gray-100'}
                blink={s.rsi != null && s.rsi <= 30} />
              <Card label="200 EMA" value={N(s.ema200)} sub={`price ${PCT(s.vs_ema200)}`}
                tone={(s.vs_ema200 || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'} />
              <Card label="ADX (14)" value={s.adx == null ? '—' : s.adx.toFixed(1)}
                sub={`+DI ${N(s.plus_di, 1)} · −DI ${N(s.minus_di, 1)}`}
                tone={(s.adx || 0) >= 25 ? 'text-brand-300' : 'text-gray-400'}
                title="Above 25 the move has direction; below 20 it is chop." />
              <Card label="20-day VWAP" value={N(s.vwap)} sub={`band ${N(s.vwap_lower, 0)} – ${N(s.vwap_upper, 0)}`}
                tone={(s.vs_vwap || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'} />
              <Card label="Bollinger %B" value={s.bb_pctb == null ? '—' : `${s.bb_pctb.toFixed(0)}%`}
                sub={s.bb_squeeze ? 'coiled — daily range very tight' : `${N(s.bb_lower, 0)} – ${N(s.bb_upper, 0)}`}
                tone={s.bb_pctb <= 0 ? 'text-red-400' : s.bb_pctb >= 100 ? 'text-emerald-400' : 'text-gray-100'} />
              <Card label="ATR (14)" value={N(s.atr)} sub={risk.atr_pct ? `${risk.atr_pct}% of price a day` : ''} />
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <Section title="Performance">
                <div className="grid grid-cols-4 sm:grid-cols-7 gap-2">
                  {(perf.windows || []).map((w) => (
                    <div key={w.label} className="text-center rounded-lg border border-surface-3 bg-surface-2/40 py-1.5">
                      <div className="text-[10px] text-gray-500">{w.label}</div>
                      <div className={`text-[12.5px] font-semibold mono ${signTone(w.pct)}`}>{PCT(w.pct, 1)}</div>
                    </div>
                  ))}
                </div>
                <div className="flex flex-wrap gap-x-5 gap-y-1 mt-2 text-[11.5px] text-gray-400">
                  {perf.ytd != null && <span>This year <span className={`mono ${signTone(perf.ytd)}`}>{PCT(perf.ytd, 1)}</span></span>}
                  {perf.since_listing != null && <span>Since the first session in the data <span className={`mono ${signTone(perf.since_listing)}`}>{PCT(perf.since_listing, 0)}</span></span>}
                </div>
                {!!(data.relative || []).length && (
                  <div className="mt-3">
                    <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Against NIFTY</div>
                    {data.relative.map((r) => (
                      <div key={r.label} className="flex items-center justify-between text-[11.5px] py-0.5">
                        <span className="text-gray-400">{r.label}</span>
                        <span className="mono text-gray-300">{PCT(r.stock, 1)} vs {PCT(r.index, 1)}</span>
                        <span className={`mono font-semibold ${signTone(r.excess)}`}>{PCT(r.excess, 1)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </Section>

              <Section title="How it behaves">
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                  <Stat label="Volatility (1y)" value={risk.volatility_annual == null ? '—' : `${risk.volatility_annual.toFixed(1)}%`} sub="annualised" />
                  <Stat label="Average day" value={risk.avg_daily_move == null ? '—' : `${risk.avg_daily_move.toFixed(2)}%`} sub="absolute move" />
                  <Stat label="Up days" value={risk.up_days_pct == null ? '—' : `${risk.up_days_pct.toFixed(0)}%`} sub="of the last year" />
                  <Stat label="Deepest fall" value={risk.max_drawdown == null ? '—' : `${risk.max_drawdown.toFixed(1)}%`} sub="from any peak" tone="text-red-400" />
                  <Stat label="Below its peak" value={risk.drawdown_now == null ? '—' : `${risk.drawdown_now.toFixed(1)}%`} sub="right now" />
                  <Stat label="Gaps (1y)" value={`${risk.gap_ups ?? '—'} up · ${risk.gap_downs ?? '—'} down`} sub="opened outside yesterday" />
                </div>
                {risk.best_day && (
                  <div className="text-[11.5px] text-gray-400 mt-2">
                    Best day <span className="mono text-emerald-400">{PCT(risk.best_day.pct, 1)}</span> on {risk.best_day.date} ·
                    worst <span className="mono text-red-400">{PCT(risk.worst_day.pct, 1)}</span> on {risk.worst_day.date}
                  </div>
                )}
              </Section>
            </div>

            <Section title="Company fundamentals"
              right={<span className="text-[10.5px] text-gray-500">{data.fundamentals?.industry || ''}</span>}>
              <Fundamentals f={data.fundamentals} ltp={data.ltp} />
            </Section>

            <div className="grid gap-4 lg:grid-cols-2">
              <Section title="Order book">
                <OrderBook book={data.order_book} />
              </Section>

              <Section title="Where it sits">
                <div className="space-y-3">
                  <RangeBar low={ext.low_52w} high={ext.high_52w} value={data.ltp} label="52-week range" />
                  <RangeBar low={ext.low_life} high={ext.high_life} value={data.ltp} label="lifetime range" />
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-3">
                  <Stat label="52w high" value={N(ext.high_52w)} sub={PCT(ext.from_52w_high, 1)} />
                  <Stat label="52w low" value={N(ext.low_52w)} sub={PCT(ext.from_52w_low, 1)} />
                  <Stat label="Lifetime high" value={N(ext.high_life)} sub={ext.high_life_on} />
                  <Stat label="Lifetime low" value={N(ext.low_life)} sub={ext.low_life_on} />
                </div>
                <div className="grid grid-cols-3 gap-3 mt-3">
                  <Stat label="Latest volume" value={COMPACT(data.volume?.latest)}
                    sub={data.volume?.vs_avg20 ? `${data.volume.vs_avg20}× the 20-day average` : ''} />
                  <Stat label="5-day average" value={COMPACT(data.volume?.avg5)} />
                  <Stat label="On up days" value={data.volume?.buy_share_20d == null ? '—' : `${data.volume.buy_share_20d}%`} sub="of 20-day volume" />
                </div>
                <div className="text-[11px] text-gray-500 mt-2">
                  {ext.sessions?.toLocaleString('en-IN')} daily sessions from {ext.first_session}
                </div>
              </Section>
            </div>

            <Section title="Entry zone"
              right={<span className="text-[10.5px] text-gray-500">
                tested over {data.horizon} sessions — the horizon for a {data.category === 'INVESTMENT' ? 'long-term holding' : 'swing trade'}</span>}>
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
                </div>
              )}
            </Section>

            <Section title="Levels price is heading into">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] text-[12px]">
                  <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                    {['Zone', 'Range', 'Distance', 'What usually happened there'].map((h) => <th key={h} className="text-left px-2 py-1 font-medium">{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {(data.zones || []).slice(0, 10).map((z, i) => (
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
            </Section>

            <div className="grid gap-4 lg:grid-cols-2">
              <Section title="Today's pivots">
                {!data.pivots?.pivot ? <Empty title="No pivots yet" /> : (
                  <>
                    <div className="grid grid-cols-4 gap-2 text-center">
                      {['s3', 's2', 's1', 'pivot', 'r1', 'r2', 'r3'].map((k) => (
                        <div key={k} className={`rounded-lg border py-1.5 ${k === 'pivot' ? 'border-brand-500/40 bg-brand-500/10' : 'border-surface-3 bg-surface-2/40'}`}>
                          <div className="text-[10px] uppercase text-gray-500">{k}</div>
                          <div className="text-[12px] mono text-gray-100">{N(data.pivots[k], 1)}</div>
                        </div>
                      ))}
                    </div>
                    <div className="text-[11px] text-gray-500 mt-2">From the {data.pivots.date} session.</div>
                  </>
                )}
              </Section>

              <Section title="Month by month" right={<span className="text-[10.5px] text-gray-500">average of the last 10 years</span>}>
                {!(data.seasonality || []).length ? <Empty title="Not enough history for a seasonal read" /> : (
                  <div className="flex items-end gap-1 h-24">
                    {data.seasonality.map((m) => {
                      const max = Math.max(...data.seasonality.map((x) => Math.abs(x.avg || 0)), 0.5);
                      const h = (Math.abs(m.avg || 0) / max) * 42;
                      const up = (m.avg || 0) >= 0;
                      return (
                        <div key={m.month} className="flex-1 h-full flex flex-col items-center justify-center"
                          title={`${MONTHS[m.month - 1]}: average ${PCT(m.avg, 1)}, positive in ${m.positive_pct}% of ${m.samples} years`}>
                          <div className="flex-1 flex items-end w-full justify-center">
                            {up && <div className="w-full bg-emerald-500/70 rounded-t" style={{ height: `${h}px` }} />}
                          </div>
                          <div className="w-full h-px bg-surface-4" />
                          <div className="flex-1 flex items-start w-full justify-center">
                            {!up && <div className="w-full bg-red-500/70 rounded-b" style={{ height: `${h}px` }} />}
                          </div>
                          <div className="text-[8.5px] text-gray-500 mt-0.5">{MONTHS[m.month - 1][0]}</div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </Section>
            </div>

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

            <div className="text-[11px] text-gray-500">
              Research and education only — nothing here is investment advice. Prices and the order book come from your
              Zerodha session; company data from a public source; the level record from the stored daily history.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
