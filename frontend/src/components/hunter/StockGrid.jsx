import React, { useEffect, useRef, useState } from 'react';
import {
  Check, Minus, Search, ChevronDown, ChevronRight, Download, Copy, LayoutGrid, List as ListIcon, Eye, LineChart,
} from 'lucide-react';
import { api } from '../../api';
import CandleChart from './CandleChart';
import { Measure, N, N0, PCT, RS_, RsPill, STAGE_STYLE, tone } from './ui';

/**
 * The names behind a stage. Each card carries the chart with its ceiling and base drawn on it, the
 * measures that justify the setup, and — on click — every check with the value it had.
 */

export const MEASURES = [
  ['rs_rating', 'RS rating', 'How this stock ranks against everything scanned today: 99 means it has outperformed 99% of them over the last year.', (r) => r.rs_rating ?? '—'],
  ['from_pivot', 'Now vs ceiling', 'How far today\'s close sits below (or above) the ceiling it must clear.', (r) => PCT(r.stage === 'FORMING' ? (r.base || {}).from_pivot_pct : r.extended_pct)],
  ['tightening', 'Tightening (ATR)', 'Daily range in the last third of the base versus the first third. Under 1× means the stock is calming down as it coils.', (r) => (r.tightening != null ? `${r.tightening}×` : '—')],
  ['dry_up', 'Volume dry-up', 'Volume over the last 10 sessions against the base\'s own average. Under 1× means trading has dried up while it rests.', (r) => ((r.base || {}).volume_ratio != null ? `${r.base.volume_ratio}×` : '—')],
  ['up_down', 'Up/down volume', 'Volume on rising days minus falling days over 50 sessions, as a share of the total. Positive means buyers have been more active.', (r) => (r.up_down_volume != null ? r.up_down_volume.toFixed(2) : '—')],
  ['from_high', 'From 52-week high', 'Distance from the highest price of the last year.', (r) => PCT(r.from_52w_high_pct)],
  ['base_len', 'Base length', 'How long the current range has lasted.', (r) => ((r.base || {}).length ? `${r.base_weeks ?? (r.base.length / 5).toFixed(1)} wks` : '—')],
  ['depth', 'Base depth', 'From the top of the base to its lowest point. Shallow bases are tidier.', (r) => ((r.base || {}).depth_pct != null ? `${r.base.depth_pct}%` : '—')],
  ['turnover', 'Turnover', 'Median value traded a day over 50 sessions — whether you could get size in and out.', (r) => (r.turnover_cr != null ? `₹${N0(r.turnover_cr)} cr` : '—')],
  ['above50', 'Above 50-day', 'How far the price is above its 50-day average. Far above means it is extended.', (r) => PCT(r.above_50dma_pct)],
  ['gain', 'Since breakout', 'Gain from the breakout close to today.', (r) => PCT((r.breakout || {}).gain_pct)],
  ['stop', 'Stop', 'Where the setup is wrong: 8% under the breakout, or the base low while it is still forming.', (r) => RS_(r.stop)],
];
const DEFAULT_MEASURES = ['rs_rating', 'from_pivot', 'tightening', 'dry_up', 'up_down', 'from_high'];

function Tag({ children, tone: t = 'gray' }) {
  const cls = { gold: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
    green: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
    gray: 'bg-surface-2 text-gray-400 border-surface-3' }[t];
  return <span className={`px-1.5 py-px rounded border text-[10px] font-semibold ${cls}`}>{children}</span>;
}

function Card({ r, measures, onOpen }) {
  const [chart, setChart] = useState(null);
  const [open, setOpen] = useState(false);
  const st = STAGE_STYLE[r.stage] || STAGE_STYLE.PLAYED_OUT;
  const day = r.day || {};
  const fb = r.first_breakout;
  const ref = useRef(null);

  useEffect(() => {                       // only fetch a chart once the card is actually on screen
    const el = ref.current;
    if (!el || chart) return undefined;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        io.disconnect();
        api.huChart(r.symbol).then((c) => c.status === 'ok' && setChart(c.chart)).catch(() => {});
      }
    }, { rootMargin: '200px' });
    io.observe(el);
    return () => io.disconnect();
  }, [r.symbol, chart]);

  return (
    <div ref={ref} className={`rounded-xl border ${st.ring} p-3`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-[14.5px] font-bold text-white truncate">{r.name || r.symbol}</span>
            <span className="text-[11px] text-gray-500">{r.symbol}</span>
            <RsPill value={r.rs_rating} />
          </div>
          <div className="text-[10.5px] text-gray-500 truncate">{r.industry}</div>
          <div className="flex items-center gap-1 mt-1 flex-wrap">
            {r.blue_sky && <Tag tone="green">Blue sky</Tag>}
            {r.quiet_day && <Tag tone="gold">Quiet day</Tag>}
            {r.powering_up && <Tag tone="green">Powering up</Tag>}
            {r.bases_this_year > 1 && <Tag>{r.bases_this_year} bases this year</Tag>}
          </div>
        </div>
        <div className="text-right shrink-0">
          <button onClick={() => onOpen?.(r.symbol)}
            className="text-[11px] text-brand-400 hover:text-brand-300 flex items-center gap-1 ml-auto mb-0.5">
            <LineChart className="w-3 h-3" />Tech chart ›
          </button>
          <div className="text-[15px] font-semibold mono text-gray-100">{RS_(r.close)}</div>
          <div className={`text-[11px] mono ${tone(day.change_pct)}`}>{PCT(day.change_pct)}</div>
        </div>
      </div>

      <div className="text-[10.5px] text-gray-500 mono mt-1.5">
        {day.date} · O {N(day.open)} H {N(day.high)} L {N(day.low)} C {N(day.close)} · Vol {N0((day.volume || 0) / 1000)}K
      </div>
      <div className="text-[11.5px] text-gray-300 mt-1 leading-snug">{r.why}</div>

      <div className="mt-2 -mx-1">
        {chart ? <CandleChart chart={chart} /> : <div className="h-[190px] flex items-center justify-center text-[11px] text-gray-600">loading the chart…</div>}
      </div>

      <div className="grid grid-cols-3 gap-x-3 gap-y-1.5 mt-2">
        {MEASURES.filter(([k]) => measures.includes(k)).map(([k, label, tip, fn]) => (
          <div key={k} title={tip} className="min-w-0 cursor-help">
            <div className="text-[9.5px] uppercase tracking-wider text-gray-500 truncate">{label}</div>
            <div className="text-[12.5px] font-semibold mono text-gray-100 truncate">{fn(r)}</div>
          </div>
        ))}
      </div>

      {fb && (
        <div className="mt-2 rounded-lg bg-amber-500/10 border border-amber-500/25 px-2 py-1 text-[11px] text-amber-300">
          <b>{r.bases_this_year}{['th', 'st', 'nd', 'rd'][(r.bases_this_year % 10 > 3 || [11, 12, 13].includes(r.bases_this_year % 100)) ? 0 : r.bases_this_year % 10] || 'th'} base this year</b>
          {' '}— first broke out {fb.date}, {PCT(fb.held_pct)} held since
        </div>
      )}

      <button onClick={() => setOpen(!open)} className="mt-2 flex items-center gap-1 text-[11px] text-gray-500 hover:text-gray-300">
        {open ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
        {open ? 'hide the checks' : 'why it qualifies'}
      </button>
      {open && (
        <div className="mt-1.5 border-t border-surface-3 pt-1.5 space-y-0.5">
          {(r.trend_checks || []).map((c, i) => (
            <div key={i} className="flex items-start gap-1.5">
              {c.passed ? <Check className="w-3 h-3 text-emerald-400 mt-0.5 shrink-0" />
                : <Minus className="w-3 h-3 text-gray-600 mt-0.5 shrink-0" />}
              <span className="text-[11px] text-gray-300">{c.label}</span>
              <span className="text-[10.5px] text-gray-500 mono ml-auto">{c.detail}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ListTable({ rows, measures, onOpen }) {
  const cols = MEASURES.filter(([k]) => measures.includes(k));
  return (
    <div className="overflow-x-auto max-h-[720px] overflow-y-auto">
      <table className="w-full text-[12px]">
        <thead className="sticky top-0 bg-surface-1">
          <tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
            <th className="text-left px-2 py-1 font-medium">Stock</th>
            <th className="text-right px-2 py-1 font-medium">Price</th>
            <th className="text-right px-2 py-1 font-medium">Day</th>
            {cols.map(([k, label, tip]) => <th key={k} title={tip} className="text-right px-2 py-1 font-medium cursor-help">{label}</th>)}
            <th className="text-left px-2 py-1 font-medium">Industry</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.symbol} onClick={() => onOpen?.(r.symbol)}
              className="border-b border-surface-3/40 hover:bg-surface-2/60 cursor-pointer">
              <td className="px-2 py-1.5">
                <div className="font-semibold text-gray-100">{r.symbol}</div>
                <div className="text-[10px] text-gray-500 truncate max-w-[220px]">{r.name}</div>
              </td>
              <td className="px-2 py-1.5 mono text-right text-gray-200">{RS_(r.close)}</td>
              <td className={`px-2 py-1.5 mono text-right ${tone((r.day || {}).change_pct)}`}>{PCT((r.day || {}).change_pct)}</td>
              {cols.map(([k, , , fn]) => <td key={k} className="px-2 py-1.5 mono text-right text-gray-300">{fn(r)}</td>)}
              <td className="px-2 py-1.5 text-[11px] text-gray-500 truncate max-w-[180px]">{r.industry}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function StockGrid({ rows = [], total = 0, industries = [], filters, setFilters, stageLabel, blurb, onOpen }) {
  const [view, setView] = useState(() => localStorage.getItem('hunter_view') || 'cards');
  const [measures, setMeasures] = useState(() => {
    try { return JSON.parse(localStorage.getItem('hunter_measures')) || DEFAULT_MEASURES; } catch { return DEFAULT_MEASURES; }
  });
  const [pickerOpen, setPickerOpen] = useState(false);
  const [shown, setShown] = useState(4);   // two rows of two, then ask for more
  const f = filters;

  useEffect(() => { localStorage.setItem('hunter_view', view); }, [view]);
  useEffect(() => { localStorage.setItem('hunter_measures', JSON.stringify(measures)); }, [measures]);
  useEffect(() => { setShown(4); }, [filters]);

  const csv = () => {
    const cols = MEASURES.filter(([k]) => measures.includes(k));
    const head = ['Symbol', 'Name', 'Industry', 'Stage', 'Close', 'Day %', ...cols.map(([, l]) => l)];
    const lines = [head.join(',')].concat(rows.map((r) => [
      r.symbol, `"${(r.name || '').replace(/"/g, "'")}"`, `"${r.industry || ''}"`, r.stage, r.close,
      (r.day || {}).change_pct ?? '', ...cols.map(([, , , fn]) => String(fn(r)).replace(/[₹,]/g, '')),
    ].join(',')));
    return lines.join('\n');
  };
  const download = () => {
    const b = new Blob([csv()], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(b);
    a.download = `hunter-${f.stage || 'all'}-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };
  const copy = () => navigator.clipboard?.writeText(rows.map((r) => r.symbol).join(', '));

  return (
    <div className="card !p-4">
      <div className="flex flex-wrap items-center gap-2 mb-1">
        <div className="flex items-center gap-2 mr-auto">
          <span className={`w-2 h-2 rounded-full ${(STAGE_STYLE[f.stage] || STAGE_STYLE.PLAYED_OUT).dot}`} />
          <h3 className="text-[15px] font-bold text-white">{stageLabel}</h3>
          <span className="text-[12px] text-gray-500">{total}</span>
        </div>
        <button onClick={download} className="btn-secondary !py-1 !px-2 text-[11.5px] flex items-center gap-1">
          <Download className="w-3 h-3" />Download
        </button>
        <button onClick={copy} className="btn-secondary !py-1 !px-2 text-[11.5px] flex items-center gap-1">
          <Copy className="w-3 h-3" />Copy
        </button>
        <div className="relative">
          <Search className="w-3.5 h-3.5 text-gray-500 absolute left-2 top-1/2 -translate-y-1/2" />
          <input value={f.q} onChange={(e) => setFilters({ ...f, q: e.target.value })} placeholder="Find a stock"
            className="input-field !py-1.5 !pl-7 text-[12px] w-[140px]" />
        </div>
        <div className="flex rounded-lg border border-surface-3 overflow-hidden">
          {[['cards', LayoutGrid], ['list', ListIcon]].map(([v, Icon]) => (
            <button key={v} onClick={() => setView(v)}
              className={`px-2 py-1.5 text-[11.5px] flex items-center gap-1 ${view === v ? 'bg-brand-500/20 text-brand-300' : 'text-gray-400 hover:text-gray-200'}`}>
              <Icon className="w-3.5 h-3.5" />{v === 'cards' ? 'Cards' : 'List'}
            </button>
          ))}
        </div>
        <div className="relative">
          <button onClick={() => setPickerOpen(!pickerOpen)}
            className="btn-secondary !py-1 !px-2 text-[11.5px] flex items-center gap-1"><Eye className="w-3 h-3" />Choose measures</button>
          {pickerOpen && (
            <div className="absolute right-0 top-[130%] z-20 w-[260px] card !p-2 max-h-[300px] overflow-y-auto">
              {MEASURES.map(([k, label, tip]) => (
                <label key={k} title={tip} className="flex items-center gap-2 px-1 py-1 rounded hover:bg-surface-2 cursor-pointer">
                  <input type="checkbox" checked={measures.includes(k)}
                    onChange={() => setMeasures(measures.includes(k) ? measures.filter((m) => m !== k) : [...measures, k])} />
                  <span className="text-[12px] text-gray-300">{label}</span>
                </label>
              ))}
            </div>
          )}
        </div>
        <select value={f.industry} onChange={(e) => setFilters({ ...f, industry: e.target.value })}
          className="bg-surface-2 border border-surface-3 rounded px-2 py-1.5 text-[12px] text-gray-300 max-w-[170px]">
          <option value="">All industries</option>
          {industries.map((i) => <option key={i} value={i}>{i}</option>)}
        </select>
        <select value={f.sort} onChange={(e) => setFilters({ ...f, sort: e.target.value })}
          className="bg-surface-2 border border-surface-3 rounded px-2 py-1.5 text-[12px] text-gray-300">
          <option value="rs_rating">Sort: RS rating</option>
          <option value="from_pivot">Sort: closest to the ceiling</option>
          <option value="gain">Sort: gain since breakout</option>
          <option value="turnover">Sort: most traded</option>
          <option value="symbol">Sort: name</option>
        </select>
      </div>
      {blurb && <p className="text-[12px] text-gray-500 mb-3 max-w-[700px]">{blurb}</p>}

      {!rows.length ? (
        <div className="py-10 text-center text-[12.5px] text-gray-500">Nothing in this stage right now.</div>
      ) : view === 'list' ? (
        <ListTable rows={rows} measures={measures} onOpen={onOpen} />
      ) : (
        <>
          <div className="grid lg:grid-cols-2 gap-3">
            {rows.slice(0, shown).map((r) => <Card key={r.symbol} r={r} measures={measures} onOpen={onOpen} />)}
          </div>
          {rows.length > shown && (
            <div className="text-center mt-3">
              <button onClick={() => setShown(shown + 4)} className="btn-secondary !py-1.5 !px-3 text-[12px]">
                Show more · {rows.length - shown} left
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
