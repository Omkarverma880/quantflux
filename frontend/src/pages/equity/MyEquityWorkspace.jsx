import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Briefcase, RefreshCw, Loader2, AlertTriangle, Info } from 'lucide-react';
import { api } from '../../api';
import AddStock from '../../components/myequity/AddStock';
import StockTable from '../../components/myequity/StockTable';
import Toolbar, { Summary } from '../../components/myequity/Toolbar';
import OrderTicket from '../../components/myequity/OrderTicket';
import XRay from '../../components/myequity/XRay';

/**
 * My Equity Workspace — the stocks you researched, the levels you are waiting for, what those
 * levels have earned since they triggered, and a per-stock X-ray. Orders go through the same
 * desk (and risk fence) as Manual Trading; nothing else here touches the market.
 */

const AUTO_MS = 30000;
const VIEW_KEY = 'qf.myequity.view';
const DEFAULT_VIEW = { q: '', filter: 'all', sector: '', sort: 'added_on', dir: 'desc', group: 'none', dense: false };

const readView = () => {
  try { return { ...DEFAULT_VIEW, ...JSON.parse(localStorage.getItem(VIEW_KEY) || '{}') }; } catch { return { ...DEFAULT_VIEW }; }
};

const pnlOf = (r) => r.watch?.primary?.pnl_pct ?? null;
const distanceOf = (r) => {
  const d = r.watch?.nearest?.distance_pct;
  return d == null ? null : Math.abs(d);
};

const MATCHERS = {
  all: () => true,
  at_level: (r) => !!r.watch?.touched,
  triggered: (r) => !!r.watch?.primary,
  waiting: (r) => r.watch?.status === 'waiting',
  profit: (r) => (pnlOf(r) ?? 0) > 0,
  loss: (r) => (pnlOf(r) ?? 0) < 0,
  oversold: (r) => r.rsi != null && r.rsi <= 30,
  overbought: (r) => r.rsi != null && r.rsi >= 80,
  investment: (r) => r.category === 'INVESTMENT',
  swing: (r) => r.category === 'SWING',
};

const SORTERS = {
  added_on: (r) => r.added_on || '',
  pnl: (r) => pnlOf(r),
  distance: (r) => (distanceOf(r) == null ? null : -distanceOf(r)),   // closest first on "desc"
  rsi: (r) => r.rsi,
  change_pct: (r) => r.change_pct,
  from_52w_high: (r) => r.from_52w_high,
  symbol: (r) => r.symbol,
};

const GROUPERS = {
  none: () => '',
  sector: (r) => r.sector || 'Sector not set',
  category: (r) => (r.category === 'INVESTMENT' ? 'Investment' : 'Swing trades'),
  status: (r) => (r.watch?.touched ? 'At a research level now'
    : r.watch?.primary ? 'Triggered' : r.watch?.status === 'waiting' ? 'Waiting for a level' : 'No levels set'),
};

export default function MyEquityWorkspace() {
  const [rows, setRows] = useState([]);
  const [summary, setSummary] = useState(null);
  const [sectors, setSectors] = useState([]);
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [at, setAt] = useState('');
  const [auto, setAuto] = useState(true);
  const [open, setOpen] = useState(null);
  const [ticket, setTicket] = useState(null);
  const [view, setViewRaw] = useState(readView);
  const timer = useRef(null);

  const setView = (v) => {
    setViewRaw(v);
    try { localStorage.setItem(VIEW_KEY, JSON.stringify(v)); } catch { /* private window */ }
  };

  const load = useCallback(async ({ refresh = 1, force = 0 } = {}) => {
    setLoading(true);
    try {
      const r = await api.meStocks({ refresh, force });
      if (r.status !== 'ok') { setErr(r.message || 'could not load'); return; }
      setErr('');
      setRows(r.rows || []);
      setSummary(r.summary || null);
      setSectors(r.sectors || []);
      setAt(r.at || '');
      setMeta((m) => ({ ...(m || {}), connected: r.connected }));
    } catch (e) { setErr(String(e.message || e)); } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    api.meMeta().then((r) => { if (r.status === 'ok') setMeta(r); }).catch(() => {});
    load({ refresh: 0 }).then(() => load({ refresh: 1, force: 1 }));
    return () => clearTimeout(timer.current);
  }, [load]);

  useEffect(() => {
    clearTimeout(timer.current);
    if (!auto || open || ticket) return undefined;
    timer.current = setTimeout(() => load({ refresh: 1 }), AUTO_MS);
    return () => clearTimeout(timer.current);
  }, [auto, open, ticket, at, load]);

  const counts = useMemo(() => {
    const out = {};
    Object.entries(MATCHERS).forEach(([k, fn]) => { out[k] = rows.filter(fn).length; });
    return out;
  }, [rows]);

  const visible = useMemo(() => {
    const q = view.q.trim().toLowerCase();
    let out = rows.filter(MATCHERS[view.filter] || MATCHERS.all);
    if (view.sector) out = out.filter((r) => r.sector === view.sector);
    if (q) {
      out = out.filter((r) => [r.symbol, r.company, r.sector, r.industry, r.note]
        .some((v) => (v || '').toLowerCase().includes(q)));
    }
    const dir = view.dir === 'asc' ? 1 : -1;
    const key = SORTERS[view.sort] || SORTERS.added_on;
    return [...out].sort((a, b) => {
      const av = key(a); const bv = key(b);
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === 'string') return av < bv ? -dir : av > bv ? dir : 0;
      return (Number(av) - Number(bv)) * dir;
    });
  }, [rows, view]);

  const groups = useMemo(() => {
    if (view.group === 'none') return [['', visible]];
    const by = GROUPERS[view.group];
    const map = new Map();
    visible.forEach((r) => {
      const k = by(r);
      if (!map.has(k)) map.set(k, []);
      map.get(k).push(r);
    });
    return [...map.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [visible, view.group]);

  const touched = rows.filter((r) => r.watch?.touched);

  return (
    <div className="p-4 sm:p-6 space-y-4 max-w-[1600px] mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3">
        {/* on a phone the title takes the whole row and the controls wrap underneath it */}
        <div className="w-full sm:w-auto sm:flex-1 min-w-0">
          <h1 className="text-xl sm:text-2xl font-bold text-white flex items-center gap-2">
            <Briefcase className="w-5 h-5 text-brand-400" />My Equity Workspace
          </h1>
          <p className="text-xs sm:text-sm text-gray-500 mt-0.5">
            Your research, tracked: the levels you are waiting for, what they earned once price reached them,
            and a full X-ray behind every row
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap shrink-0">
          <label className="flex items-center gap-1.5 text-[12px] text-gray-400 cursor-pointer">
            <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} className="accent-brand-500" />
            auto-refresh
          </label>
          <button onClick={() => load({ refresh: 1, force: 1 })} disabled={loading}
            className="btn-secondary !py-2 !px-3 text-[12.5px] flex items-center gap-1.5">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}Refresh
          </button>
          <AddStock connected={meta?.connected} onAdded={() => load({ refresh: 1, force: 1 })} />
        </div>
      </div>

      {meta && meta.connected === false && (
        <div className="text-[12px] text-amber-500 flex items-start gap-1.5">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-px" />
          Zerodha is not connected — prices and orders are unavailable, but your list, levels and their history still load.
        </div>
      )}
      {err && <div className="text-[12px] text-red-400">{err}</div>}

      {!!touched.length && (
        <div className="card !p-3 border-amber-500/30 bg-amber-500/5">
          <div className="text-[12.5px] text-amber-500 font-semibold">
            {touched.length === 1 ? '1 stock is at a research level' : `${touched.length} stocks are at a research level`}
            <span className="text-gray-400 font-normal ml-1">
              — {touched.map((r) => `${r.symbol} at ${r.watch.nearest.level}`).join(' · ')}
            </span>
          </div>
        </div>
      )}

      <Summary summary={summary} />

      {rows.length > 0 && (
        <Toolbar view={view} setView={setView} sectors={sectors} counts={counts}
          shown={visible.length} total={rows.length} />
      )}

      <div className="space-y-4">
        {groups.map(([label, list]) => (
          <div key={label || 'all'} className="space-y-1.5">
            {label && (
              <div className="flex items-center gap-2 px-0.5">
                <span className="text-[12px] font-semibold text-gray-300">{label}</span>
                <span className="text-[11px] text-gray-500">{list.length}</span>
                <span className="flex-1 h-px bg-surface-3" />
              </div>
            )}
            <StockTable rows={list} loading={loading && !label} dense={view.dense}
              onOpen={(r) => setOpen(r.id)} onTrade={(r, side) => setTicket({ row: r, side })}
              onChanged={() => load({ refresh: 1, force: 1 })} />
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-gray-500">
        <span>{rows.length} stock{rows.length === 1 ? '' : 's'}{at ? ` · updated ${at}` : ''}</span>
        <span>Research tool — orders are placed only when you confirm them.</span>
      </div>

      <div className="card !p-4 text-[12px] text-gray-400 space-y-1.5">
        <div className="flex items-center gap-1.5 text-gray-200 font-semibold"><Info className="w-4 h-4 text-brand-400" />How this works</div>
        <div>• <span className="text-gray-200">Research levels</span> carry an arrow showing where the last trade sits against them. A level triggers on the first session after your research date whose range contains it — so back-dated research is reconstructed from the stored history.</div>
        <div>• <span className="text-gray-200">P&L since trigger</span> runs from the level to the last trade, per share, with the days held. Tracking several levels is fine: the first to trigger leads, and any other tracked level that triggers keeps its own row in the X-ray. Click a level chip to stop tracking it.</div>
        <div>• <span className="text-gray-200">Row colour</span> means one thing at a time — amber and blinking at a level, green at RSI 30 or below, red at RSI 80 or above.</div>
        <div>• <span className="text-gray-200">Investment or Swing</span> changes the horizon the entry zones are tested over: 60 sessions against 10. Click the chip to switch.</div>
        <div>• <span className="text-gray-200">BUY and SELL</span> open an order ticket that goes to the same desk as Manual Trading, with its risk fence and order log. Nothing is sent until you confirm.</div>
      </div>

      {open != null && <XRay stockId={open} onClose={() => setOpen(null)} onChanged={() => load({ refresh: 1, force: 1 })} />}
      {ticket && (
        <OrderTicket row={ticket.row} side={ticket.side} onClose={() => setTicket(null)}
          onPlaced={() => load({ refresh: 1, force: 1 })} />
      )}
    </div>
  );
}
