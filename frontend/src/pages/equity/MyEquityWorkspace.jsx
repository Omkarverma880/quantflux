import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Briefcase, RefreshCw, Loader2, AlertTriangle, Info } from 'lucide-react';
import { api } from '../../api';
import AddStock from '../../components/myequity/AddStock';
import StockTable from '../../components/myequity/StockTable';
import XRay from '../../components/myequity/XRay';

/**
 * My Equity Workspace — the stocks you researched, the levels you are waiting for, and what
 * the daily data says about each of them. Read-only: it never places an order.
 */

const AUTO_MS = 30000;

export default function MyEquityWorkspace() {
  const [rows, setRows] = useState([]);
  const [meta, setMeta] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [at, setAt] = useState('');
  const [auto, setAuto] = useState(true);
  const [open, setOpen] = useState(null);
  const timer = useRef(null);
  const first = useRef(true);

  const load = useCallback(async ({ refresh = 1, force = 0 } = {}) => {
    setLoading(true);
    try {
      const r = await api.meStocks({ refresh, force });
      if (r.status !== 'ok') { setErr(r.message || 'could not load'); return; }
      setErr('');
      setRows(r.rows || []);
      setAt(r.at || '');
      setMeta((m) => ({ ...(m || {}), connected: r.connected }));
    } catch (e) { setErr(String(e.message || e)); } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    api.meMeta().then((r) => { if (r.status === 'ok') setMeta(r); }).catch(() => {});
    // first paint from the cache (instant), then a live refresh
    load({ refresh: 0 }).then(() => { first.current = false; load({ refresh: 1, force: 1 }); });
    return () => clearTimeout(timer.current);
  }, [load]);

  useEffect(() => {
    clearTimeout(timer.current);
    if (!auto || open) return undefined;
    timer.current = setTimeout(() => load({ refresh: 1 }), AUTO_MS);
    return () => clearTimeout(timer.current);
  }, [auto, open, at, load]);

  const touched = rows.filter((r) => r.watch?.touched);

  return (
    <div className="p-4 sm:p-6 space-y-4 max-w-[1500px] mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <h1 className="text-xl sm:text-2xl font-bold text-white flex items-center gap-2">
            <Briefcase className="w-5 h-5 text-brand-400" />My Equity Workspace
          </h1>
          <p className="text-xs sm:text-sm text-gray-500 mt-0.5">
            The stocks you research, the levels you are waiting for, and the daily picture behind each one —
            click any row for its full X-ray
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
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
          Zerodha is not connected — you are seeing what was cached at the last refresh. Log in to update prices and add stocks.
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

      <StockTable rows={rows} loading={loading} onOpen={(r) => setOpen(r.id)} onChanged={() => load({ refresh: 1, force: 1 })} />

      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-gray-500">
        <span>{rows.length} stock{rows.length === 1 ? '' : 's'}{at ? ` · updated ${at}` : ''}</span>
        <span>Research only — nothing here places an order.</span>
      </div>

      <div className="card !p-4 text-[12px] text-gray-400 space-y-1.5">
        <div className="flex items-center gap-1.5 text-gray-200 font-semibold"><Info className="w-4 h-4 text-brand-400" />How this works</div>
        <div>• <span className="text-gray-200">Research levels</span> are the prices you decided on. When the last trade comes within your tolerance of one, the whole row blinks and the stock is listed at the top.</div>
        <div>• <span className="text-gray-200">Sensitivity</span> scores six readings — price against the 200 EMA, the 20/50 EMA stack, RSI, ADX direction, the share of volume trading on up days, and the position in the 52-week range.</div>
        <div>• <span className="text-gray-200">Volume</span> shows the last twelve sessions as bars with the latest highlighted, and how it compares with the 20-day average.</div>
        <div>• <span className="text-gray-200">The X-ray</span> adds the chart with its bands, the entry zones price is heading into with what historically happened there, and the recent headlines.</div>
        <div>• Daily history is pulled once per stock and then kept up to date, so a refresh costs one request per stock — the lifetime high and low come from every session Zerodha has.</div>
      </div>

      {open != null && <XRay stockId={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
