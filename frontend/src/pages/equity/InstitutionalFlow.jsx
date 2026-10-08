import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Landmark, RefreshCw, Loader2, AlertTriangle, Info, CheckCircle2, Download,
  TrendingUp, TrendingDown, ExternalLink, ArrowUp, ArrowDown, ArrowUpDown, Activity,
} from 'lucide-react';
import { api } from '../../api';
import { ImportList } from '../../components/institutional/StockActivity';

/**
 * FII_DII_Equity Activity Watcher.
 *
 * An institutional-flow terminal, read top to bottom: what the two sides did this session, the
 * same figures by date, which stocks the published lists name, and how those names have behaved
 * since. Panels with no real source say so in place of a number — a plausible fabrication is
 * worse here than a blank.
 *
 * Two rules shape the layout. The session ledger sits high, because the by-date figures are the
 * thing most often wanted and there is only one of it now: the old screen carried the same seven
 * columns twice, once as "by session" and again as "historical", which is a redundancy, not a
 * second view. And the stock lists are labelled for what they are — a change in FII
 * *shareholding*, as a percentage, on a quarterly clock — never as a day's buying.
 *
 * It does not reset at the close. At 15:31 the same data is relabelled as the session's final
 * picture and stays until the next one opens.
 */

const CR = (v, d = 0) => (v == null || Number.isNaN(Number(v)) ? '—'
  : `₹${Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d })} Cr`);
// nets read better with their direction stated; magnitudes must never carry a sign
const CRS = (v, d = 0) => (v == null || Number.isNaN(Number(v)) ? '—'
  : `${Number(v) < 0 ? '−' : '+'}₹${Math.abs(Number(v)).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d })} Cr`);
const N = (v, d = 2) => (v == null || Number.isNaN(Number(v)) ? '—'
  : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
const N0 = (v) => (v == null ? '—' : Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 }));
const PCT = (v, d = 2) => (v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(d)}%`);
const PP = (v, d = 2) => (v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(d)}`);
const tone = (v) => (v == null ? 'text-gray-400' : v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-gray-300');

const DAY = (iso) => {
  if (!iso) return '—';
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime()) ? iso
    : d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short' });
};

const TONE_DOT = { live: 'bg-emerald-500', closed: 'bg-red-500', delayed: 'bg-amber-500' };
const TONE_TEXT = { live: 'text-emerald-400', closed: 'text-red-400', delayed: 'text-amber-400' };

const LEDGER_SPANS = [['10', '10 sessions'], ['22', '1 month'], ['30', '30 sessions'],
  ['66', '3 months'], ['132', '6 months'], ['260', '1 year']];

const MOVER_SOURCES = [
  ['FII_BOUGHT', 'FII raised holding'],
  ['FII_SOLD', 'FII cut holding'],
  ['MF_BOUGHT', 'Funds raised holding'],
  ['MF_SOLD', 'Funds cut holding'],
];

const DELIV_UNIVERSES = [
  ['ALL', 'All equities'],
  ['NIFTY50', 'NIFTY 50'],
  ['FNO', 'F&O names'],
  ['FII_LIST', 'On an FII list'],
  ['WATCHLIST', 'My watchlist'],
];

const DELIV_RANKS = [
  ['surge', 'Delivery vs its own norm'],
  ['delivery', 'Highest delivery %'],
  ['value', 'Largest delivered value'],
  ['gain', 'Biggest gainers'],
  ['loss', 'Biggest fallers'],
  ['turnover', 'Highest turnover'],
];

const RANKS = [
  ['rvol', 'Highest relative volume'],
  ['gain', 'Highest price gain'],
  ['loss', 'Highest price fall'],
  ['confirmed', 'Institutional + price confirmation'],
  ['symbol', 'Name'],
];

export default function InstitutionalFlow() {
  const [meta, setMeta] = useState(null);
  const [snap, setSnap] = useState(null);
  const [flow, setFlow] = useState(null);
  const [deriv, setDeriv] = useState(null);
  const [fiiStocks, setFiiStocks] = useState(null);
  const [diiNote, setDiiNote] = useState(null);
  const [yday, setYday] = useState(null);
  const [side, setSide] = useState('bought');
  const [mfSide, setMfSide] = useState('bought');
  const [moveRank, setMoveRank] = useState('confirming');
  const [deliv, setDeliv] = useState(null);
  const [delivOpts, setDelivOpts] = useState({ universe: 'ALL', rank: 'surge', min_turnover_cr: 25 });
  const [delivBusy, setDelivBusy] = useState(false);
  const [movers, setMovers] = useState(null);
  const [moverSrc, setMoverSrc] = useState('FII_BOUGHT');
  const moverTimer = useRef(null);
  const [ledgerView, setLedgerView] = useState('cash');
  const [showImport, setShowImport] = useState(false);
  const [universe, setUniverse] = useState('NIFTY50');
  const [span, setSpan] = useState('22');
  const [rank, setRank] = useState('rvol');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const timer = useRef(null);

  const load = useCallback(async () => {
    try {
      const s = await api.instSnapshot({ universe, limit: 50, refresh: 1 });
      if (s.status === 'error') throw new Error(s.message);
      setSnap(s);
      setErr('');
    } catch (e) { setErr(String(e.message || e)); }
  }, [universe]);

  const loadStocks = useCallback(() => {
    api.instFiiStocks().then(setFiiStocks).catch(() => {});
    api.instDiiStocks().then(setDiiNote).catch(() => {});
    api.instYesterdayTracker(25).then(setYday).catch(() => {});
  }, []);

  useEffect(() => { api.instMeta().then(setMeta).catch(() => {}); }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { api.instFlowTable(span).then(setFlow).catch(() => {}); }, [span]);
  useEffect(() => { api.instDerivatives(60).then(setDeriv).catch(() => {}); }, []);
  useEffect(() => { loadStocks(); }, [loadStocks]);
  useEffect(() => {
    let dead = false;
    setDelivBusy(true);
    api.instDeliveryScreen({ ...delivOpts, lookback: 11, limit: 60 })
      .then((d) => { if (!dead) setDeliv(d); })
      .catch(() => {})
      .finally(() => { if (!dead) setDelivBusy(false); });
    return () => { dead = true; };
  }, [delivOpts]);

  // The live card is the one panel that genuinely wants a fast cadence: a per-minute volume
  // rate only exists between two readings, so the first refresh after opening it is always
  // blank and the second is the first useful one. Out of hours it is read once and left alone.
  const loadMovers = useCallback(() => {
    api.instLiveMovers({ source: moverSrc, limit: 10 })
      .then(setMovers).catch(() => {});
  }, [moverSrc]);

  useEffect(() => { loadMovers(); }, [loadMovers]);
  useEffect(() => {
    clearInterval(moverTimer.current);
    if (!snap?.market?.should_poll) return undefined;
    moverTimer.current = setInterval(loadMovers, 20000);
    return () => clearInterval(moverTimer.current);
  }, [snap?.market?.should_poll, loadMovers]);

  // poll only while the market is actually open — never out of hours, which is where most
  // dashboards quietly burn their rate limit
  useEffect(() => {
    clearInterval(timer.current);
    if (!snap?.market?.should_poll) return undefined;
    timer.current = setInterval(() => load(), 60000);
    return () => clearInterval(timer.current);
  }, [snap?.market?.should_poll, load]);

  const sync = async () => {
    setBusy(true); setMsg('');
    try {
      const r = await api.instSync();
      await load();
      api.instFlowTable(span).then(setFlow).catch(() => {});
      api.instDerivatives(60).then(setDeriv).catch(() => {});
      loadStocks();
      const b = r?.backfilled, c = r?.stock_lists;
      setMsg([b?.saved ? `${b.added} session(s) backfilled` : null,
        c?.saved ? `${c.rows} stock rows captured` : null].filter(Boolean).join(' · '));
    } catch (e) { setErr(String(e.message || e)); } finally { setBusy(false); }
  };

  const m = snap?.market || meta?.market;
  const agg = snap?.aggregate;
  const fii = agg?.fii, dii = agg?.dii;

  // The index reaction travels on the ledger rows themselves, sourced from NSE's close archive.
  // It used to come from the Moneycontrol history call, which meant a 403 there blanked the
  // column as well as the derivatives view.
  const latestIdx = useMemo(
    () => (flow?.rows || []).find((r) => r.published && r.nifty_close != null) || null,
    [flow],
  );

  const block = fiiStocks?.sides?.[side];
  const ledger = useSort(flow?.rows, 'trading_date', 'desc');
  const derivRows = useSort((deriv?.rows || []).slice(0, Number(span)), 'trading_date', 'desc');
  const screen = useSort(deliv?.rows, null, 'desc');
  const watch = useSort(snap?.stocks, null, 'desc');
  const mf = diiNote?.mutual_funds;
  const mfBlock = mf?.sides?.[mfSide];

  // what each name has done today, read off whichever price source we have. "Confirming first"
  // puts the names whose move agrees with the list at the top of each column — the ones worth a
  // second look — while "biggest movers" ignores direction and just ranks by size.
  const movesOf = (blk, bullish) => {
    const rows = (blk?.rows || []).map((r) => {
      const pct = r.price_change_pct ?? r.et_change_pct ?? null;
      const abs = r.price_move ?? r.et_price_change ?? null;
      return { ...r, today_pct: pct, today_abs: abs,
        agrees: pct == null ? null : (pct > 0) === bullish };
    }).filter((r) => r.today_pct != null);
    const by = moveRank === 'movers'
      ? (a, b) => Math.abs(b.today_pct) - Math.abs(a.today_pct)
      : bullish ? (a, b) => b.today_pct - a.today_pct : (a, b) => a.today_pct - b.today_pct;
    return rows.sort(by);
  };
  const moves = useMemo(() => ({
    bought: movesOf(fiiStocks?.sides?.bought, true),
    sold: movesOf(fiiStocks?.sides?.sold, false),
  // movesOf closes over moveRank, which is the only other input
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [fiiStocks, moveRank]);

  const sinceBySymbol = useMemo(() => {
    const out = {};
    [...(yday?.increased || []), ...(yday?.decreased || [])].forEach((r) => {
      if (r.symbol && r.price_then != null) out[r.symbol] = r.price_then;
    });
    return out;
  }, [yday]);

  const stocks = useMemo(() => {
    const rows = [...(snap?.stocks || [])];
    const by = {
      rvol: (a, b) => (b.relative_volume ?? -1) - (a.relative_volume ?? -1),
      gain: (a, b) => (b.price_change_pct ?? -999) - (a.price_change_pct ?? -999),
      loss: (a, b) => (a.price_change_pct ?? 999) - (b.price_change_pct ?? 999),
      confirmed: (a, b) => Number(String(b.confirmation).startsWith('Possible'))
        - Number(String(a.confirmation).startsWith('Possible')),
      symbol: (a, b) => String(a.symbol).localeCompare(String(b.symbol)),
    };
    return rows.sort(by[rank] || by.rvol);
  }, [snap, rank]);

  const exportLedger = () => {
    const rows = flow?.rows || [];
    const head = ['Date', 'FII buy (Cr)', 'FII sell (Cr)', 'FII net (Cr)',
      'DII buy (Cr)', 'DII sell (Cr)', 'DII net (Cr)', 'NIFTY %', 'State', 'Source'];
    const body = rows.map((r) => [r.trading_date, r.fii_buy ?? '', r.fii_sell ?? '', r.fii_net ?? '',
      r.dii_buy ?? '', r.dii_sell ?? '', r.dii_net ?? '',
      r.nifty_change_pct ?? '',
      r.published ? (r.backfilled ? 'published (net only)' : 'published') : r.state,
      r.source || ''].join(','));
    const blob = new Blob([[head.join(','), ...body].join('\n')], { type: 'text/csv' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `fii-dii-ledger-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className="space-y-4">
      {/* ── header ────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold text-white flex items-center gap-2">
            <Landmark className="w-5 h-5 text-brand-400" />FII_DII_Equity Activity Watcher
          </h1>
          <p className="text-xs sm:text-sm text-gray-500 mt-0.5 max-w-4xl">
            What the institutions did, what the tape did, and what is not published. The session
            on screen stays after the close — it is relabelled, never cleared.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill market={m} />
          <button disabled={busy} onClick={sync}
            className="btn-secondary !py-1.5 !px-3 text-[12.5px] flex items-center gap-1.5">
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
            Sync now
          </button>
        </div>
      </div>

      {err && <Note tone="bad"><AlertTriangle className="w-3.5 h-3.5 inline mr-1" />{err}</Note>}
      {msg && <Note tone="ok"><CheckCircle2 className="w-3.5 h-3.5 inline mr-1.5 -mt-0.5" />{msg}</Note>}

      {/* ── the session at a glance ───────────────────────────────── */}
      {agg?.available ? (
        <div className="space-y-2">
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
            <Side label="FII / FPI" rec={fii} icon={(fii?.net ?? 0) >= 0 ? TrendingUp : TrendingDown} />
            <Side label="DII" rec={dii} icon={(dii?.net ?? 0) >= 0 ? TrendingUp : TrendingDown} />
            <Tile label="Institutional balance" value={CRS(agg.balance)} valueTone={tone(agg.balance)}
              foot={agg.balance_label} />
            <Tile label={`Index reaction · ${DAY(latestIdx?.trading_date)}`}
              value={latestIdx ? PCT(latestIdx.nifty_change_pct) : '—'}
              valueTone={tone(latestIdx?.nifty_change_pct)}
              foot={latestIdx
                ? `NIFTY ${N(latestIdx.nifty_close)} · BANK NIFTY ${N(latestIdx.banknifty_close)} (${PCT(latestIdx.banknifty_change_pct)})`
                : 'Index close unavailable.'} />
          </div>

          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-gray-500 px-1">
            <span>
              Figures for <b className="text-gray-300">{agg.data_date || 'an unknown session'}</b>
              {' · '}{agg.source}
              {agg.retrieved_at ? ` · read ${String(agg.retrieved_at).slice(11, 19)} IST` : ''}
              {agg.age?.text ? ` · ${agg.age.text}` : ''}
            </span>
            {agg.cross_check && (
              <span className={`px-1.5 py-px rounded border text-[10.5px] ${
                agg.cross_check.agrees === true
                  ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                  : agg.cross_check.agrees === false
                    ? 'border-red-500/40 bg-red-500/10 text-red-300'
                    : 'border-surface-4 bg-surface-3 text-gray-400'}`}>
                {agg.cross_check.message}
              </span>
            )}
            {agg.fallback_from && (
              <span className="px-1.5 py-px rounded border border-amber-500/40 bg-amber-500/10 text-amber-300 text-[10.5px]">
                {agg.fallback_from} did not answer — figures are {agg.source}
              </span>
            )}
          </div>

          {agg.stale && (
            <Note tone="warn">
              This is the last successful reading, not a fresh one.
              {agg.error ? ` The latest attempt failed: ${agg.error}.` : ''}
              {' '}Shown because stale-and-labelled beats blank-or-invented.
            </Note>
          )}
        </div>
      ) : (
        <Note tone="warn">
          <b>Data unavailable.</b>{' '}
          {agg?.error ? `No source answered: ${agg.error}.` : 'No source answered.'}
          {agg?.retrieved_at ? ` Last attempt ${agg.retrieved_at}.` : ''}
          {' '}No figure is shown rather than a zero, which would read as "no flow".
        </Note>
      )}

      {/* ── the session ledger: one table, not two ────────────────── */}
      <Card title="Session ledger — FII / DII cash flow by date"
        right={
          <div className="flex flex-wrap items-center gap-2">
            <Switch value={ledgerView} onChange={setLedgerView}
              options={[['cash', 'Cash'], ['deriv', 'FII derivatives']]} />
            <select value={span} onChange={(e) => setSpan(e.target.value)} className={SEL}>
              {LEDGER_SPANS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
            <button onClick={exportLedger} disabled={!flow?.rows?.length}
              className="btn-secondary !py-1 !px-2 text-[11.5px] flex items-center gap-1 disabled:opacity-40">
              <Download className="w-3 h-3" />CSV
            </button>
          </div>
        }>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-500 mb-2">
          {flow?.pending ? <Chip tone="warn">{flow.pending} awaiting publication</Chip> : null}
          {flow?.backfilled ? <Chip>{flow.backfilled} net only</Chip> : null}
          {flow?.not_captured ? <Chip>{flow.not_captured} before any source</Chip> : null}
          <span>{ledgerView === 'cash'
            ? '₹ crore. Net = buy − sell.'
            : '₹ crore, net, per book. A positive figure is net buying in that book.'}</span>
        </div>

        {ledgerView === 'cash' ? (
          <Table cols={['Date', 'FII buy', 'FII sell', 'FII net', 'DII buy', 'DII sell', 'DII net', 'NIFTY']}
            align={['left', 'right', 'right', 'right', 'right', 'right', 'right', 'right']}
            sortKeys={['trading_date', 'fii_buy', 'fii_sell', 'fii_net',
              'dii_buy', 'dii_sell', 'dii_net', 'nifty_change_pct']}
            sortKey={ledger.sortKey} sortDir={ledger.sortDir} onSort={ledger.toggle}>
            {ledger.sorted.map((r) => (
                <tr key={r.trading_date}
                  className={`border-b border-surface-3/40 hover:bg-surface-2/40 ${r.published ? '' : 'opacity-75'}`}>
                  <td className="px-2 py-1.5 whitespace-nowrap">
                    <span className="mono text-gray-200">{DAY(r.trading_date)}</span>
                    <span className="mono text-gray-600 text-[10.5px] ml-1">{String(r.trading_date).slice(0, 4)}</span>
                    {!r.published && (
                      <span className={`ml-2 text-[10px] px-1 py-px rounded border ${
                        r.state === 'pending'
                          ? 'bg-amber-500/15 border-amber-500/40 text-amber-300'
                          : 'bg-surface-3 border-surface-4 text-gray-500'}`}>
                        {r.state === 'pending' ? 'pending' : 'not captured'}
                      </span>
                    )}
                  </td>
                  {r.published ? (
                    <>
                      <td className="px-2 py-1.5 text-right mono text-gray-400">{CR(r.fii_buy, 2)}</td>
                      <td className="px-2 py-1.5 text-right mono text-gray-400">{CR(r.fii_sell, 2)}</td>
                      <td className={`px-2 py-1.5 text-right mono font-semibold ${tone(r.fii_net)}`}>{CRS(r.fii_net, 2)}</td>
                      <td className="px-2 py-1.5 text-right mono text-gray-400">{CR(r.dii_buy, 2)}</td>
                      <td className="px-2 py-1.5 text-right mono text-gray-400">{CR(r.dii_sell, 2)}</td>
                      <td className={`px-2 py-1.5 text-right mono font-semibold ${tone(r.dii_net)}`}>{CRS(r.dii_net, 2)}</td>
                      <td className={`px-2 py-1.5 text-right mono ${tone(r.nifty_change_pct)}`}>
                        {r.nifty_change_pct == null ? '—' : PCT(r.nifty_change_pct)}
                      </td>
                    </>
                  ) : (
                    <td colSpan={7} className="px-2 py-1.5 text-[11.5px] text-gray-500 italic">{r.note}</td>
                  )}
                </tr>
            ))}
          </Table>
        ) : deriv?.available ? (
          <Table cols={['Date', 'FII cash', 'Index futures', 'Index options', 'Stock futures', 'Stock options', 'NIFTY', 'SENSEX']}
            align={['left', 'right', 'right', 'right', 'right', 'right', 'right', 'right']}
            sortKeys={['trading_date', 'fii_net', 'fii_index_futures', 'fii_index_options',
              'fii_stock_futures', 'fii_stock_options', 'nifty_change_pct', 'sensex_change_pct']}
            sortKey={derivRows.sortKey} sortDir={derivRows.sortDir} onSort={derivRows.toggle}>
            {derivRows.sorted.map((r) => (
              <tr key={r.trading_date} className="border-b border-surface-3/40 hover:bg-surface-2/40">
                <td className="px-2 py-1.5 whitespace-nowrap mono text-gray-200">{DAY(r.trading_date)}
                  <span className="mono text-gray-600 text-[10.5px] ml-1">{String(r.trading_date).slice(0, 4)}</span>
                </td>
                <td className={`px-2 py-1.5 text-right mono font-semibold ${tone(r.fii_net)}`}>{CRS(r.fii_net, 2)}</td>
                <td className={`px-2 py-1.5 text-right mono ${tone(r.fii_index_futures)}`}>{CRS(r.fii_index_futures, 2)}</td>
                <td className={`px-2 py-1.5 text-right mono ${tone(r.fii_index_options)}`}>{CRS(r.fii_index_options, 2)}</td>
                <td className={`px-2 py-1.5 text-right mono ${tone(r.fii_stock_futures)}`}>{CRS(r.fii_stock_futures, 2)}</td>
                <td className={`px-2 py-1.5 text-right mono ${tone(r.fii_stock_options)}`}>{CRS(r.fii_stock_options, 2)}</td>
                <td className={`px-2 py-1.5 text-right mono ${tone(r.nifty_change_pct)}`}>{PCT(r.nifty_change_pct)}</td>
                <td className={`px-2 py-1.5 text-right mono ${tone(r.sensex_change_pct)}`}>{PCT(r.sensex_change_pct)}</td>
              </tr>
            ))}
          </Table>
        ) : (
          <Note tone="warn">
            <b>FII derivative positions are unavailable.</b>{' '}
            {deriv?.error || 'The source did not answer.'}{' '}
            This view is the one part of the screen with a single source — the cash figures,
            index closes and stock lists above all come from elsewhere and are unaffected.
          </Note>
        )}

        <p className="text-[11px] text-gray-600 mt-2">
          {ledgerView === 'cash' ? flow?.note : deriv?.note}
        </p>
      </Card>

      {/* ── live: the named stocks, as they trade ─────────────────── */}
      <Card title="Live tracker — the named stocks as they trade"
        right={
          <div className="flex flex-wrap items-center gap-2">
            {movers?.available && snap?.market?.live && (
              <span className="flex items-center gap-1.5 text-[10.5px] text-emerald-400">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
                every 20s
              </span>
            )}
            <select value={moverSrc} onChange={(e) => setMoverSrc(e.target.value)} className={SEL}>
              {MOVER_SOURCES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </div>
        }>
        {!movers ? <Loading /> : movers.available ? (
          <>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-500 mb-2">
              <Chip tone="good">{movers.counts.up} up</Chip>
              <Chip tone="bad">{movers.counts.down} down</Chip>
              {movers.counts.flat ? <Chip>{movers.counts.flat} unchanged</Chip> : null}
              <span>of {movers.quoted} names on the list</span>
              {!movers.market?.live && <Chip tone="warn">market closed — last traded prices</Chip>}
              {movers.volume_rate_ready === 0 && (
                <Chip tone="warn">volume rate appears on the next refresh</Chip>
              )}
              {movers.retrieved_at && (
                <span className="mono">· {String(movers.retrieved_at).slice(11, 19)} IST</span>
              )}
            </div>
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
              <MoverList title="Top 10 rising" rows={movers.risers} up />
              <MoverList title="Top 10 falling" rows={movers.fallers} />
            </div>
            <p className="text-[11px] text-gray-600 mt-2">{movers.note}</p>
          </>
        ) : (
          <Note tone={movers.connected === false ? 'warn' : 'info'}>{movers.message}</Note>
        )}
      </Card>

      {/* ── which stocks the published lists name ─────────────────── */}
      <Card title="FII shareholding activity by stock"
        right={
          <div className="flex flex-wrap items-center gap-2">
            <Switch value={side} onChange={setSide}
              options={[['bought', 'Holding increased'], ['sold', 'Holding decreased']]} />
            <button onClick={() => setShowImport(true)}
              className="btn-secondary !py-1 !px-2 text-[11.5px]">Import a list</button>
          </div>
        }>
        {!fiiStocks ? (
          <Loading />
        ) : block?.available ? (
          <>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-500 mb-2">
              <Chip tone={side === 'bought' ? 'good' : 'bad'}>{block.count} names</Chip>
              <span>
                {block.source}
                {block.source_url && (
                  <a href={block.source_url} target="_blank" rel="noreferrer"
                    className="ml-1 text-brand-400 hover:underline inline-flex items-center gap-0.5">
                    source<ExternalLink className="w-2.5 h-2.5" />
                  </a>
                )}
              </span>
              {fiiStocks.unresolved ? <Chip tone="warn">{fiiStocks.unresolved} unmatched to a symbol</Chip>
                : <Chip tone="good">all {fiiStocks.resolved} matched to NSE symbols</Chip>}
              {block.stale ? <Chip tone="warn">last good reading</Chip> : null}
            </div>

            <Note tone="warn">
              <b>Read this as shareholding, not trading.</b> {block.measure}
              <span className="block mt-1 text-amber-300/70">{fiiStocks.ranked_by}</span>
            </Note>

            <div className="mt-2">
              <HoldingTable rows={block.rows} heldLabel="FII held" />
            </div>

            <p className="text-[11px] text-gray-600 mt-2">
              {fiiStocks.caveat}
              {!fiiStocks.connected && ' Price and % change are the source\'s own — connect Zerodha for live quotes, volume and relative volume.'}
            </p>
          </>
        ) : (
          <Note tone="warn">
            <b>The list could not be read.</b> {block?.error || 'The source did not answer.'}
            {' '}Nothing is shown in its place.
          </Note>
        )}
      </Card>

      {/* ── DII: the aggregate is published, the stock list is not ─ */}
      <Card title="Domestic institutions by stock"
        right={mf?.available ? (
          <Switch value={mfSide} onChange={setMfSide}
            options={[['bought', 'Holding increased'], ['sold', 'Holding decreased']]} />
        ) : null}>
        <Note tone="info">{diiNote?.message || snap?.dii_stocks?.message
          || 'Security-level DII activity requires a stock-level institutional-data provider.'}</Note>

        {mfBlock?.available ? (
          <div className="mt-3">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-500 mb-2">
              <Chip tone={mfSide === 'bought' ? 'good' : 'bad'}>{mfBlock.count} names</Chip>
              <span>
                {mfBlock.source}
                {mfBlock.source_url && (
                  <a href={mfBlock.source_url} target="_blank" rel="noreferrer"
                    className="ml-1 text-brand-400 hover:underline inline-flex items-center gap-0.5">
                    source<ExternalLink className="w-2.5 h-2.5" />
                  </a>
                )}
              </span>
            </div>
            <Note tone="warn">
              <b>Mutual funds are not all of DII.</b> {diiNote?.mutual_funds_caveat}
            </Note>
            <div className="mt-2">
              <HoldingTable rows={mfBlock.rows} heldLabel="MF held" />
            </div>
          </div>
        ) : diiNote ? (
          <p className="text-[11px] text-gray-600 mt-2">
            Mutual fund shareholding is normally published by stock and would appear here, but
            that list could not be read{mfBlock?.error ? `: ${mfBlock.error}` : '.'}
          </p>
        ) : null}
      </Card>

      {/* ── the lists against today's tape, for trading ──────────── */}
      <Card title="FII lists → today's move"
        right={
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10.5px] text-gray-500">
              {fiiStocks?.connected ? 'live from your broker' : "the source's own prices"}
            </span>
            <Switch value={moveRank} onChange={setMoveRank}
              options={[['confirming', 'Confirming first'], ['movers', 'Biggest movers']]} />
          </div>
        }>
        {!fiiStocks ? <Loading /> : fiiStocks.available ? (
          <>
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
              <MoveList title="FII raised holding" sub="going up today agrees with the list"
                rows={moves.bought} up since={sinceBySymbol} />
              <MoveList title="FII cut holding" sub="going down today agrees with the list"
                rows={moves.sold} since={sinceBySymbol} />
            </div>
            <p className="text-[11px] text-gray-600 mt-2">
              The names come from the quarterly shareholding lists; the move beside each one is
              today&apos;s. Agreement between the two is a description of what has happened, not
              a prediction and not a recommendation — and a quarterly filing says nothing about
              what any institution did today.
              {yday?.available && ` "Since" compares today's price with the one stored on ${yday.previous_session}.`}
            </p>
          </>
        ) : (
          <Note tone="warn">The lists could not be read, so there is nothing to measure today against.</Note>
        )}
      </Card>

      {/* ── delivery: the daily per-stock number that does exist ─── */}
      <Card title="Delivery accumulation screen"
        right={
          <div className="flex flex-wrap items-center gap-2">
            {delivBusy && <Loader2 className="w-3.5 h-3.5 animate-spin text-gray-500" />}
            <select value={delivOpts.universe} className={SEL}
              onChange={(e) => setDelivOpts((o) => ({ ...o, universe: e.target.value }))}>
              {DELIV_UNIVERSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
            <select value={delivOpts.rank} className={SEL}
              onChange={(e) => setDelivOpts((o) => ({ ...o, rank: e.target.value }))}>
              {DELIV_RANKS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
            <select value={delivOpts.min_turnover_cr} className={SEL}
              onChange={(e) => setDelivOpts((o) => ({ ...o, min_turnover_cr: Number(e.target.value) }))}>
              {[5, 25, 50, 100, 250].map((v) => (
                <option key={v} value={v}>{`min ₹${v} Cr turnover`}</option>
              ))}
            </select>
          </div>
        }>
        {!deliv ? <Loading /> : deliv.available ? (
          <>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-500 mb-2">
              <Chip>{deliv.session}</Chip>
              <Chip tone="good">{deliv.shown} of {deliv.count} names</Chip>
              <span>baseline: each stock&apos;s own mean over {deliv.baseline_sessions?.length || 0} prior sessions</span>
              {deliv.context?.same_session && (
                <span>
                  · that session FII were{' '}
                  <b className={tone(deliv.context.fii_net)}>{CRS(deliv.context.fii_net)}</b>, DII{' '}
                  <b className={tone(deliv.context.dii_net)}>{CRS(deliv.context.dii_net)}</b>
                </span>
              )}
            </div>

            <Note tone="warn">
              <b>Delivery, not attribution.</b> {deliv.caveat}
            </Note>

            <div className="mt-2">
              <Table cols={['Stock', 'Close', 'Chg %', 'Delivery %', 'Its norm', 'Surge',
                'Delivered', 'Turnover', 'Reading']}
                align={['left', 'right', 'right', 'right', 'right', 'right', 'right', 'right', 'left']}
                sortKeys={['symbol', 'close', 'change_pct', 'delivery_pct',
                  'delivery_baseline_pct', 'delivery_surge', 'delivery_value_cr',
                  'turnover_cr', 'reading']}
                sortKey={screen.sortKey} sortDir={screen.sortDir} onSort={screen.toggle}>
                {screen.sorted.map((r) => (
                  <tr key={r.symbol} className="border-b border-surface-3/40 hover:bg-surface-2/40">
                    <td className="px-2 py-1.5 whitespace-nowrap">
                      <span className="font-semibold text-gray-100">{r.symbol}</span>
                      {r.company && (
                        <span className="text-[10.5px] text-gray-500 ml-1.5">{r.company.slice(0, 28)}</span>
                      )}
                    </td>
                    <td className="px-2 py-1.5 text-right mono text-gray-200">{N(r.close)}</td>
                    <td className={`px-2 py-1.5 text-right mono ${tone(r.change_pct)}`}>{PCT(r.change_pct)}</td>
                    <td className="px-2 py-1.5 text-right mono text-gray-200">
                      {r.delivery_pct == null ? '—' : `${N(r.delivery_pct)}%`}
                    </td>
                    <td className="px-2 py-1.5 text-right mono text-gray-500">
                      {r.delivery_baseline_pct == null ? '—' : `${N(r.delivery_baseline_pct)}%`}
                    </td>
                    <td className={`px-2 py-1.5 text-right mono font-semibold ${
                      r.delivery_surge == null ? 'text-gray-500'
                        : r.delivery_surge >= 1.25 ? 'text-amber-300'
                          : r.delivery_surge <= 0.8 ? 'text-gray-500' : 'text-gray-300'}`}>
                      {r.delivery_surge == null ? '—' : `${N(r.delivery_surge, 2)}x`}
                    </td>
                    <td className="px-2 py-1.5 text-right mono text-gray-400">
                      {r.delivery_value_cr == null ? '—' : `₹${N0(r.delivery_value_cr)} Cr`}
                    </td>
                    <td className="px-2 py-1.5 text-right mono text-gray-500">
                      {r.turnover_cr == null ? '—' : `₹${N0(r.turnover_cr)} Cr`}
                    </td>
                    <td className={`px-2 py-1.5 text-[11.5px] ${
                      String(r.reading).startsWith('Delivery surge on a rising') ? 'text-emerald-400'
                        : String(r.reading).startsWith('Delivery surge into') ? 'text-red-400'
                          : String(r.reading).startsWith('Delivery surge') ? 'text-amber-300'
                            : 'text-gray-400'}`}>{r.reading}</td>
                  </tr>
                ))}
              </Table>
            </div>

            <p className="text-[11px] text-gray-600 mt-2">
              {deliv.measure} ETFs and liquid funds are excluded — they settle almost entirely to
              delivery and would own the top of this table without meaning anything there.
              {' '}{deliv.source}.
            </p>
          </>
        ) : (
          <Note tone="warn">
            <b>Not available.</b> {deliv.message || deliv.error}
          </Note>
        )}
      </Card>

      {/* ── conflict and intraday, side by side ───────────────────── */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Card title="Institutional conflict">
          {snap?.conflict?.available ? (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <span className={`px-2 py-1 rounded text-[12.5px] font-semibold ${snap.conflict.divergence
                  ? 'bg-amber-500/15 text-amber-300 border border-amber-500/40'
                  : 'bg-surface-3 text-gray-300'}`}>
                  {snap.conflict.divergence ? 'FII ↔ DII divergence' : 'No divergence'}
                </span>
                <span className="text-[13px] text-gray-200">{snap.conflict.headline}</span>
              </div>
              <div className="text-[12px] mono text-gray-400 mt-1.5">
                FII {CRS(snap.conflict.fii_net)} · DII {CRS(snap.conflict.dii_net)} · gap {CR(snap.conflict.gap)}
              </div>
              <p className="text-[11px] text-gray-600 mt-2">{snap.conflict.note}</p>
            </>
          ) : <Note tone="info">{snap?.conflict?.message || 'Needs both net figures.'}</Note>}
        </Card>

        <Card title="Intraday institutional flow">
          <Note tone="info">{snap?.intraday?.message
            || 'Intraday institutional transaction values are not available from the current source.'}</Note>
          <p className="text-[11px] text-gray-600 mt-2">
            A daily total divided into hourly buckets would look like a flow chart and carry no
            information, so none is drawn.
          </p>
        </Card>
      </div>

      {/* ── the universe, with real price and volume ──────────────── */}
      <Card title="Stock watch"
        right={
          <div className="flex flex-wrap items-center gap-2">
            <select value={universe} onChange={(e) => setUniverse(e.target.value)} className={SEL}>
              {(meta?.universes || [{ key: 'NIFTY50', label: 'NIFTY 50' }]).map((u) => (
                <option key={u.key} value={u.key}>{u.label}</option>
              ))}
            </select>
            <select value={rank} onChange={(e) => setRank(e.target.value)} className={SEL}>
              {RANKS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </div>
        }>
        {snap?.universe?.available === false ? (
          <Note tone="info">{snap.universe.error}</Note>
        ) : !snap?.connected ? (
          <Note tone="warn">
            Connect Zerodha to price this list. The institutional figures above come from NSE,
            Moneycontrol and the Economic Times and do not need it; price, volume and relative
            volume do.
          </Note>
        ) : (
          <>
            <div className="text-[11px] text-gray-500 mb-2">
              {snap?.universe?.source} · {snap?.stocks?.length || 0} names ·
              relative volume is today against a 20-day average from the app's own daily cache
            </div>
            <Table
              cols={['Stock', 'Price', '% change', 'Volume', 'vs 20d avg', 'Institutional activity', 'Confirmation']}
              align={['left', 'right', 'right', 'right', 'right', 'left', 'left']}
              sortKeys={['symbol', 'price', 'price_change_pct', 'volume', 'relative_volume',
                null, 'confirmation']}
              sortKey={watch.sortKey} sortDir={watch.sortDir} onSort={watch.toggle}>
              {(watch.sortKey ? watch.sorted : stocks).map((s) => (
                <tr key={s.symbol} className="border-b border-surface-3/40 hover:bg-surface-2/40">
                  <td className="px-2 py-1.5">
                    <span className="font-semibold text-gray-100">{s.symbol}</span>
                    {s.company && <span className="text-[10.5px] text-gray-500 ml-1.5">{s.company}</span>}
                  </td>
                  <td className="px-2 py-1.5 text-right mono text-gray-200">{N(s.price)}</td>
                  <td className={`px-2 py-1.5 text-right mono ${tone(s.price_change_pct)}`}>{PCT(s.price_change_pct)}</td>
                  <td className="px-2 py-1.5 text-right mono text-gray-400">{N0(s.volume)}</td>
                  <td className={`px-2 py-1.5 text-right mono ${s.relative_volume > 1 ? 'text-amber-300' : 'text-gray-400'}`}>
                    {s.relative_volume == null ? '—' : `${N(s.relative_volume, 2)}x`}
                  </td>
                  <td className="px-2 py-1.5 text-[11.5px] text-gray-500">not published daily</td>
                  <td className="px-2 py-1.5 text-[11.5px] text-gray-500">{s.confirmation}</td>
                </tr>
              ))}
            </Table>
          </>
        )}
      </Card>

      {/* ── what this screen can and cannot know ──────────────────── */}
      <Card title="What this screen can and cannot know">
        <ul className="space-y-1.5 text-[12.5px] text-gray-300 list-disc list-inside">
          <li><b>Published daily:</b> aggregate FII/FPI and DII cash-market buy, sell and net —
            NSE, with Moneycontrol as a cross-check and as the fallback when NSE blocks us. Earlier
            sessions come from Moneycontrol's history, which carries net only.</li>
          <li><b>Published quarterly, by stock:</b> FII shareholding as a percentage of equity, and
            its change. That is the stock table. It is <b>not</b> a day's buying, and no rupee
            value exists for it.</li>
          <li><b>Published daily, by stock:</b> how much of each stock&apos;s volume settled to
            delivery rather than being squared off intraday. That is the delivery screen. NSE
            attributes it to nobody, so it is <b>not</b> FII activity — it is the closest daily,
            per-stock figure that exists, and it describes intent, not identity.</li>
          <li><b>Live from your broker:</b> price, volume and 20-day relative volume — and nothing
            institutional. Zerodha does not publish FII/DII activity.</li>
          <li><b>Not published anywhere free:</b> intraday institutional flow, and which stocks
            DIIs bought or sold. Both are marked unavailable rather than estimated, and a list of
            names is never inferred from an aggregate figure.</li>
        </ul>
        {!!meta?.sources?.length && (
          <div className="mt-3 text-[11px] text-gray-500 space-y-0.5">
            {meta.sources.map((s) => (
              <div key={s.name}>
                • {s.url
                  ? <a href={s.url} target="_blank" rel="noreferrer" className="text-gray-400 hover:text-brand-400 hover:underline">{s.name}</a>
                  : s.name} — {s.provides} ({s.cadence})
              </div>
            ))}
            {meta?.symbol_index?.count ? (
              <div className="pt-1 text-gray-600">
                Company names are matched to NSE symbols against {meta.symbol_index.count} listed
                equities. A name with no unambiguous match is shown as unmatched rather than guessed.
              </div>
            ) : null}
          </div>
        )}
      </Card>

      {showImport && (
        <ImportList onClose={() => setShowImport(false)}
          onDone={() => { setShowImport(false); loadStocks(); }} />
      )}
    </div>
  );
}

const SEL = 'input-field !py-1 !px-2 text-[12px]';

function Loading() {
  return <div className="text-[12px] text-gray-500 flex items-center gap-1.5">
    <Loader2 className="w-3.5 h-3.5 animate-spin" />Loading…</div>;
}

function StatusPill({ market }) {
  if (!market) return null;
  const dot = TONE_DOT[market.tone] || 'bg-gray-500';
  const text = TONE_TEXT[market.tone] || 'text-gray-400';
  return (
    <div className="flex items-center gap-2 px-2.5 py-1 rounded-lg border border-surface-3 bg-surface-2/60">
      <span className={`w-2 h-2 rounded-full ${dot} ${market.live ? 'animate-pulse' : ''}`} />
      <div className="leading-tight">
        <div className={`text-[12px] font-semibold ${text}`}>{market.label}</div>
        <div className="text-[10.5px] text-gray-500 mono">{market.detail}</div>
      </div>
    </div>
  );
}

function Switch({ value, onChange, options }) {
  return (
    <div className="flex rounded-lg border border-surface-3 overflow-hidden">
      {options.map(([k, l]) => (
        <button key={k} onClick={() => onChange(k)}
          className={`px-2.5 py-1 text-[11.5px] whitespace-nowrap ${value === k
            ? 'bg-brand-500 text-white' : 'text-gray-400 hover:text-white'}`}>{l}</button>
      ))}
    </div>
  );
}

function Chip({ children, tone: t = 'plain' }) {
  const cls = {
    plain: 'border-surface-4 bg-surface-3 text-gray-400',
    good: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
    bad: 'border-red-500/40 bg-red-500/10 text-red-300',
    warn: 'border-amber-500/40 bg-amber-500/10 text-amber-300',
  }[t];
  return <span className={`px-1.5 py-px rounded border text-[10.5px] ${cls}`}>{children}</span>;
}

function Tile({ label, value, valueTone = 'text-gray-100', foot }) {
  return (
    <div className="rounded-lg border border-surface-3 bg-surface-2/40 p-3">
      <div className="text-[10px] uppercase tracking-wider text-gray-500">{label}</div>
      <div className={`text-[22px] font-bold mono mt-1 ${valueTone}`}>{value}</div>
      {foot && <div className="text-[11px] text-gray-500 mt-1 leading-snug">{foot}</div>}
    </div>
  );
}

function Side({ label, rec, icon: Icon }) {
  const net = rec?.net;
  return (
    <div className="rounded-lg border border-surface-3 bg-surface-2/40 p-3">
      <div className="flex items-center justify-between">
        <div className="text-[10px] uppercase tracking-wider text-gray-500">{label}</div>
        {Icon && <Icon className={`w-3.5 h-3.5 ${tone(net)}`} />}
      </div>
      <div className={`text-[22px] font-bold mono mt-1 ${tone(net)}`}>{CRS(net)}</div>
      <div className="flex items-center gap-4 mt-1.5 text-[11.5px]">
        <span className="text-gray-500">Buy <span className="mono text-gray-300">{CR(rec?.buy, 2)}</span></span>
        <span className="text-gray-500">Sell <span className="mono text-gray-300">{CR(rec?.sell, 2)}</span></span>
      </div>
      <div className="text-[10.5px] text-gray-600 mt-1">net = buy − sell · cash market</div>
    </div>
  );
}

function Card({ title, right, children }) {
  return (
    <div className="card !p-4">
      <div className="flex flex-wrap items-start justify-between gap-2 mb-2.5">
        <div className="text-[12px] font-semibold uppercase tracking-wider text-gray-400">{title}</div>
        {right}
      </div>
      {children}
    </div>
  );
}

function Note({ tone: t = 'info', children }) {
  const cls = {
    info: 'border-surface-3 text-gray-400',
    warn: 'border-amber-500/40 text-amber-300 bg-amber-500/5',
    ok: 'border-emerald-500/40 text-emerald-300 bg-emerald-500/5',
    bad: 'border-red-500/40 text-red-300 bg-red-500/5',
  }[t];
  return (
    <div className={`text-[12px] rounded-lg border px-3 py-2 leading-snug ${cls}`}>
      {t === 'info' && <Info className="w-3.5 h-3.5 inline mr-1.5 -mt-0.5 text-gray-500" />}
      {children}
    </div>
  );
}

/**
 * Sort a list of rows by a named field, with a stable idea of where blanks go.
 *
 * A missing value always sorts last, in both directions. Ranking "no data" above a real figure
 * because null happens to compare low would put the least informative rows at the top of a table
 * someone is scanning for the most informative ones.
 */
function useSort(rows, initialKey = null, initialDir = 'desc') {
  const [sortKey, setSortKey] = useState(initialKey);
  const [sortDir, setSortDir] = useState(initialDir);
  const toggle = useCallback((key) => {
    if (!key) return;
    setSortKey((prev) => {
      if (prev === key) {
        setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
        return key;
      }
      // numbers are most useful largest-first; names read better A to Z
      setSortDir(typeof (rows || []).find((r) => r?.[key] != null)?.[key] === 'string'
        ? 'asc' : 'desc');
      return key;
    });
  }, [rows]);

  const sorted = useMemo(() => {
    if (!sortKey) return rows || [];
    const sign = sortDir === 'asc' ? 1 : -1;
    return [...(rows || [])].sort((a, b) => {
      const x = a?.[sortKey]; const y = b?.[sortKey];
      const xb = x == null || x === ''; const yb = y == null || y === '';
      if (xb && yb) return 0;
      if (xb) return 1;            // blanks last, whichever way we are sorting
      if (yb) return -1;
      if (typeof x === 'string' || typeof y === 'string') {
        return sign * String(x).localeCompare(String(y));
      }
      return sign * (Number(x) - Number(y));
    });
  }, [rows, sortKey, sortDir]);

  return { sorted, sortKey, sortDir, toggle };
}

function Table({ cols, align = [], children, sortKeys, sortKey, sortDir, onSort }) {
  return (
    <div className="overflow-x-auto max-h-[480px]">
      <table className="w-full text-[12px]">
        <thead className="sticky top-0 z-10">
          <tr className="bg-surface-3/90 backdrop-blur-sm text-[10px] uppercase tracking-[0.12em]
                         text-gray-300 border-b-2 border-surface-4">
            {cols.map((c, i) => {
              const key = sortKeys?.[i];
              const on = key && key === sortKey;
              const right = align[i] === 'right';
              return (
                <th key={c}
                  onClick={key ? () => onSort?.(key) : undefined}
                  title={key ? `Sort by ${c}` : undefined}
                  className={`px-2 py-1.5 font-semibold whitespace-nowrap
                    ${right ? 'text-right' : 'text-left'}
                    ${key ? 'cursor-pointer select-none hover:text-white' : ''}
                    ${on ? 'text-brand-300' : ''}`}>
                  <span className={`inline-flex items-center gap-1 ${right ? 'flex-row-reverse' : ''}`}>
                    {c}
                    {key && (on
                      ? (sortDir === 'asc' ? <ArrowUp className="w-2.5 h-2.5" />
                        : <ArrowDown className="w-2.5 h-2.5" />)
                      : <ArrowUpDown className="w-2.5 h-2.5 opacity-25" />)}
                  </span>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

/**
 * One institution's shareholding list, beside today's tape.
 *
 * The holding columns and the price columns come from different places on different clocks —
 * a quarterly filing and a live quote — so they are kept visibly apart rather than blended
 * into a single "activity" number that would imply the source said something it did not.
 *
 * The DII column only exists on the FII page, so it is dropped when nothing in the list
 * carries one instead of rendering a column of dashes.
 */
function HoldingTable({ rows, heldLabel }) {
  const showDii = (rows || []).some((r) => r.dii_holding_pct != null);
  const cols = ['Company', 'Symbol', heldLabel, 'QoQ change', 'YoY change',
    ...(showDii ? ['DII held'] : []),
    'Price', 'Day %', 'Volume', 'vs 20d', 'Reaction'];
  const align = ['left', 'left', 'right', 'right', 'right',
    ...(showDii ? ['right'] : []),
    'right', 'right', 'right', 'right', 'left'];
  const keys = ['company', 'symbol', 'holding_pct', 'holding_qoq_change_pct',
    'holding_yoy_change_pct',
    ...(showDii ? ['dii_holding_pct'] : []),
    'shown_price', 'shown_change_pct', 'volume', 'relative_volume', 'reaction'];
  // The price columns fall back to the source's own figures when no broker is connected, so
  // the sort has to key on the value actually on screen. Sorting by the broker field alone
  // would silently do nothing in exactly the case where the fallback is being displayed.
  const priced = useMemo(() => (rows || []).map((r) => ({
    ...r,
    shown_price: r.price ?? r.et_price ?? null,
    shown_change_pct: r.price_change_pct ?? r.et_change_pct ?? null,
  })), [rows]);
  const { sorted, sortKey, sortDir, toggle } = useSort(priced, 'holding_qoq_change_pct', 'desc');
  return (
    <Table cols={cols} align={align} sortKeys={keys}
      sortKey={sortKey} sortDir={sortDir} onSort={toggle}>
      {sorted.map((r) => (
        <tr key={`${r.company}-${r.symbol}`} className="border-b border-surface-3/40 hover:bg-surface-2/40">
          <td className="px-2 py-1.5 text-gray-100 font-medium whitespace-nowrap">{r.company}</td>
          <td className="px-2 py-1.5 mono text-[11.5px]">
            {r.symbol
              ? <span className="text-brand-300"
                  title={r.listed_as ? `${r.listed_as} — matched by ${r.resolved_via}` : undefined}>
                  {r.symbol}
                </span>
              : <span className="text-gray-600"
                  title="No unambiguous NSE match — shown without one rather than guessed">unmatched</span>}
          </td>
          <td className="px-2 py-1.5 text-right mono text-gray-300">
            {r.holding_pct == null ? '—' : `${N(r.holding_pct)}%`}
          </td>
          <td className={`px-2 py-1.5 text-right mono font-semibold ${tone(r.holding_qoq_change_pct)}`}>
            {PP(r.holding_qoq_change_pct)}
          </td>
          <td className={`px-2 py-1.5 text-right mono ${tone(r.holding_yoy_change_pct)}`}>
            {PP(r.holding_yoy_change_pct)}
          </td>
          {showDii && (
            <td className="px-2 py-1.5 text-right mono text-gray-500">
              {r.dii_holding_pct == null ? '—' : `${N(r.dii_holding_pct)}%`}
            </td>
          )}
          <td className="px-2 py-1.5 text-right mono text-gray-200">{N(r.shown_price)}</td>
          <td className={`px-2 py-1.5 text-right mono ${tone(r.shown_change_pct)}`}>
            {PCT(r.shown_change_pct)}
          </td>
          <td className="px-2 py-1.5 text-right mono text-gray-400">{N0(r.volume)}</td>
          <td className={`px-2 py-1.5 text-right mono ${r.relative_volume > 1 ? 'text-amber-300' : 'text-gray-400'}`}>
            {r.relative_volume == null ? '—' : `${N(r.relative_volume, 2)}x`}
          </td>
          <td className="px-2 py-1.5 text-[11.5px] text-gray-300">{r.reaction}</td>
        </tr>
      ))}
    </Table>
  );
}


/**
 * One side of a shareholding list, ranked by what those names have done today.
 *
 * The trading question this answers is narrow: of the stocks a published list names, which are
 * moving with it and which against. That is an observation about two numbers, and the header
 * says which direction counts as agreement so the colour coding cannot be read as advice.
 *
 * Rows with no price today are dropped upstream rather than shown as zero — a stock that did
 * not trade has not disagreed with anything.
 */
function MoveList({ title, sub, rows, up, since }) {
  const agreeing = (rows || []).filter((r) => r.agrees).length;
  const { sorted, sortKey, sortDir, toggle } = useSort(rows, null, 'desc');
  // "Since" only means something once a previous session's prices are on file, which takes a
  // day to become true. Until then the column is dropped rather than shown full of dashes.
  const showSince = (rows || []).some((r) => since?.[r.symbol] != null && r.price != null);
  const cols = ['Stock', "Today's change", 'Today %',
    ...(showSince ? ['Since'] : []), 'vs 20d', 'Agrees'];
  const align = ['left', 'right', 'right',
    ...(showSince ? ['right'] : []), 'right', 'left'];
  const keys = ['company', 'today_abs', 'today_pct',
    ...(showSince ? [null] : []), 'relative_volume', 'agrees'];
  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-2 mb-1.5">
        <span className={`text-[11px] font-semibold uppercase tracking-wider ${up ? 'text-emerald-400' : 'text-red-400'}`}>
          {title}
        </span>
        <span className="text-[10.5px] text-gray-500">{sub}</span>
        {rows?.length ? (
          <span className="text-[10.5px] text-gray-400 ml-auto">
            <b className={up ? 'text-emerald-400' : 'text-red-400'}>{agreeing}</b>
            {` of ${rows.length} agree`}
          </span>
        ) : null}
      </div>
      {rows?.length ? (
        <Table cols={cols} align={align} sortKeys={keys}
          sortKey={sortKey} sortDir={sortDir} onSort={toggle}>
          {sorted.map((r) => {
            const then = since?.[r.symbol];
            const drift = (then != null && r.price != null) ? r.price - then : null;
            return (
              <tr key={`${r.company}-${r.symbol}`} className="border-b border-surface-3/40 hover:bg-surface-2/40">
                <td className="px-2 py-1.5 whitespace-nowrap">
                  <span className="font-semibold text-gray-100">{r.company}</span>
                  {r.symbol && (
                    <span className="text-[10.5px] mono text-brand-300/70 ml-1.5"
                      title={r.listed_as ? `${r.listed_as} — matched by ${r.resolved_via}` : undefined}>
                      {r.symbol}
                    </span>
                  )}
                </td>
                <td className={`px-2 py-1.5 text-right mono ${tone(r.today_abs)}`}>
                  {r.today_abs == null ? '—'
                    : `${r.today_abs < 0 ? '−' : '+'}₹${N(Math.abs(r.today_abs))}`}
                </td>
                <td className={`px-2 py-1.5 text-right mono font-semibold ${tone(r.today_pct)}`}>
                  {PCT(r.today_pct)}
                </td>
                {showSince && (
                  <td className={`px-2 py-1.5 text-right mono ${tone(drift)}`}>
                    {drift == null ? '—' : `${drift < 0 ? '−' : '+'}₹${N(Math.abs(drift))}`}
                  </td>
                )}
                <td className={`px-2 py-1.5 text-right mono ${r.relative_volume > 1 ? 'text-amber-300' : 'text-gray-400'}`}>
                  {r.relative_volume == null ? '—' : `${N(r.relative_volume, 2)}x`}
                </td>
                <td className="px-2 py-1.5">
                  <span className={`text-[10.5px] px-1.5 py-px rounded border ${r.agrees
                    ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                    : 'border-surface-4 bg-surface-3 text-gray-500'}`}>
                    {r.agrees ? 'with the list' : 'against'}
                  </span>
                </td>
              </tr>
            );
          })}
        </Table>
      ) : <div className="text-[12px] text-gray-500">No prices available for these names yet.</div>}
    </div>
  );
}


/**
 * One side of the live tracker: the named stocks moving hardest, with the book behind them.
 *
 * Three numbers per row answer three different questions. The change says where it has gone.
 * Volume per minute says whether anyone is still trading it — a big move on a trickle is a
 * different thing from the same move on a flood. And the bid/ask split says which side is
 * currently carrying more size.
 *
 * The last of those is the easiest to over-read, so the bar is drawn without a verdict attached:
 * pending orders can be pulled, and a heavy side is the book at one instant, not a forecast.
 */
function MoverList({ title, rows, up }) {
  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-2 mb-1.5">
        <span className={`text-[11px] font-semibold uppercase tracking-wider flex items-center gap-1.5
          ${up ? 'text-emerald-400' : 'text-red-400'}`}>
          <Activity className="w-3 h-3" />{title}
        </span>
        <span className="text-[10.5px] text-gray-500">{(rows || []).length} shown</span>
      </div>
      {(rows || []).length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <thead>
              <tr className="bg-surface-3/90 text-[10px] uppercase tracking-[0.12em]
                             text-gray-300 border-b-2 border-surface-4">
                {['Stock', 'Price', 'Change', 'Vol / min', 'vs 20d', 'Book (bid vs ask)'].map((c, i) => (
                  <th key={c} className={`px-2 py-1.5 font-semibold whitespace-nowrap
                    ${i >= 1 && i <= 4 ? 'text-right' : 'text-left'}`}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.symbol} className="border-b border-surface-3/40 hover:bg-surface-2/40">
                  <td className="px-2 py-1.5 whitespace-nowrap">
                    <span className="font-semibold text-gray-100">{r.symbol}</span>
                    {r.holding_qoq_change_pct != null && (
                      <span className={`text-[10px] mono ml-1.5 ${tone(r.holding_qoq_change_pct)}`}
                        title="Quarter-on-quarter change in holding — why this name is on the list">
                        {PP(r.holding_qoq_change_pct)}
                      </span>
                    )}
                  </td>
                  <td className="px-2 py-1.5 text-right mono text-gray-200">{N(r.price)}</td>
                  <td className={`px-2 py-1.5 text-right mono font-semibold ${tone(r.change_pct)}`}>
                    {PCT(r.change_pct)}
                    <span className="block text-[10px] opacity-70">
                      {r.change < 0 ? '−' : '+'}₹{N(Math.abs(r.change))}
                    </span>
                  </td>
                  <td className="px-2 py-1.5 text-right mono text-gray-300">
                    {r.volume_per_min == null
                      ? <span className="text-gray-600" title="Measured between two readings — appears on the next refresh">—</span>
                      : N0(r.volume_per_min)}
                  </td>
                  <td className={`px-2 py-1.5 text-right mono ${r.relative_volume > 1 ? 'text-amber-300' : 'text-gray-400'}`}>
                    {r.relative_volume == null ? '—' : `${N(r.relative_volume, 2)}x`}
                  </td>
                  <td className="px-2 py-1.5">
                    <BookBar share={r.demand_share} bid={r.bid_quantity} ask={r.ask_quantity}
                      label={r.book} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="text-[12px] text-gray-500">
          None on this list {up ? 'is up' : 'is down'} right now.
        </div>
      )}
    </div>
  );
}

function BookBar({ share, bid, ask, label }) {
  if (share == null) return <span className="text-[11px] text-gray-600">no book</span>;
  const pct = Math.max(2, Math.min(98, share * 100));
  return (
    <div className="min-w-[132px]" title={`${N0(bid)} pending to buy · ${N0(ask)} pending to sell`}>
      <div className="h-1.5 rounded-full overflow-hidden bg-red-500/30 flex">
        <div className="bg-emerald-500/70" style={{ width: `${pct}%` }} />
      </div>
      <div className="text-[10px] text-gray-500 mt-0.5 flex justify-between gap-2">
        <span className={share >= 0.55 ? 'text-emerald-400' : share <= 0.45 ? 'text-red-400' : ''}>
          {label}
        </span>
        <span className="mono">{Math.round(share * 100)}%</span>
      </div>
    </div>
  );
}
