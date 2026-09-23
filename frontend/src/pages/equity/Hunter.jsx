import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Crosshair, Loader2, RefreshCw, BookOpen, Radio, LayoutGrid, BarChart3, Layers } from 'lucide-react';
import { api } from '../../api';
import Board from '../../components/hunter/Board';
import StockGrid from '../../components/hunter/StockGrid';
import Evidence from '../../components/hunter/Evidence';
import { Note, Section } from '../../components/hunter/ui';

/**
 * Hunter — every NIFTY 500 stock scanned each day for one structural setup: a strong stock
 * resting in a tight base, the breakout that starts it, and what happens after. Screener only.
 */
const BLURB = {
  FORMING: 'Strong stocks pausing in a tight, quiet range just under a ceiling — the trading drying up as they coil. A close above sets one off.',
  BREAKOUT: 'Cleared the ceiling within the last five sessions on heavy volume, and still holding above it.',
  CLIMBING: 'Broke out earlier and is still running above its trailing stop.',
  PLAYED_OUT: 'Broke out in the last year and has since closed below its stop — that setup is finished.',
};

export default function Hunter() {
  const [meta, setMeta] = useState(null);
  const [data, setData] = useState(null);
  const [filters, setFilters] = useState({ stage: 'FORMING', industry: '', q: '', sort: 'rs_rating', screens: [], combine: 'any' });
  const [tab, setTab] = useState('board');
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState('');
  const [err, setErr] = useState('');
  const [showHow, setShowHow] = useState(false);
  const poll = useRef(null);

  const loadBoard = useCallback(async (f) => {
    const r = await api.huBoard({ stage: f.screens.length ? '' : f.stage, industry: f.industry, q: f.q,
      sort: f.sort, screens: f.screens.join(','), combine: f.combine });
    if (r.status === 'ok') setData(r); else setErr(r.message || 'could not load the board');
  }, []);

  useEffect(() => {
    api.huMeta().then((m) => (m.status === 'ok' ? setMeta(m) : setErr(m.message)));
  }, []);
  useEffect(() => { loadBoard(filters); }, [filters, loadBoard]);
  useEffect(() => () => poll.current && clearInterval(poll.current), []);

  const scan = async () => {
    setErr(''); setBusy(true); setProgress('starting');
    try {
      const r = await api.huScan({});
      if (r.status !== 'ok') throw new Error(r.message);
      poll.current = setInterval(async () => {
        const j = await api.huJob(r.job.id);
        const job = j.job || {};
        setProgress(job.progress || '');
        if (job.status === 'done' || job.status === 'error') {
          clearInterval(poll.current);
          setBusy(false);
          if (job.status === 'error') setErr(job.error || 'scan failed');
          else { await loadBoard(filters); api.huMeta().then((m) => m.status === 'ok' && setMeta(m)); }
        }
      }, 2000);
    } catch (e) { setBusy(false); setErr(String(e.message || e)); }
  };

  if (!meta) {
    return <div className="p-6 text-center text-gray-500 text-sm">
      {err ? <span className="text-red-400">{err}</span> : <><Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />Loading the Hunter…</>}
    </div>;
  }

  const board = data?.board || [];
  const counts = Object.fromEntries(board.map((b) => [b.stage, b.count]));
  const label = (board.find((b) => b.stage === filters.stage) || {}).label || 'All setups';
  const headline = counts.FORMING != null
    ? `${counts.FORMING} stocks are forming a base.`
    : 'No scan yet.';
  const scanned = data?.meta?.scan_date;

  return (
    <div className="p-4 sm:p-6 space-y-4 max-w-[1600px] mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl sm:text-2xl font-bold text-white flex items-center gap-2">
            <Crosshair className="w-5 h-5 text-brand-400" />Hunter
          </h1>
          <p className="text-xs sm:text-sm text-gray-500 mt-0.5">
            Swing setups across the NIFTY 500 — strong stocks coiling under a ceiling, the breakouts
            that follow, and where each one stands. Screener only: no entry, no target, no orders.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full ${meta.connected
            ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'}`}>
            <Radio className="w-3 h-3" />{meta.connected ? 'Zerodha connected' : 'Zerodha not connected'}
          </span>
          {scanned && <span className="text-[11px] text-gray-500">Scanned · {scanned}</span>}
          <button onClick={scan} disabled={busy || !meta.connected}
            className="btn-primary !py-1.5 !px-3 text-[12.5px] flex items-center gap-1.5">
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
            {busy ? 'Scanning…' : 'Scan now'}
          </button>
        </div>
      </div>

      {busy && <Note>Scanning {meta.universe?.count || 500} stocks — {progress}. The first run fetches a year of
        candles for each name, so it takes a few minutes; later scans only fetch what is new.</Note>}
      {err && <div className="text-[12px] text-red-400">{err}</div>}
      {!meta.connected && <Note tone="warn">Connect Zerodha to scan — the Hunter needs daily candles for the universe.</Note>}

      <div className="flex gap-1 border-b border-surface-3 overflow-x-auto">
        {[['board', 'The board', LayoutGrid], ['evidence', 'Evidence', BarChart3]].map(([id, label, Icon]) => (
          <button key={id} onClick={() => setTab(id)}
            className={`flex items-center gap-1.5 px-3.5 py-2 text-sm font-semibold border-b-2 -mb-px transition whitespace-nowrap ${tab === id
              ? 'border-brand-500 text-brand-400' : 'border-transparent text-gray-400 hover:text-gray-200'}`}>
            <Icon className="w-4 h-4" />{label}
          </button>
        ))}
      </div>

      {tab === 'evidence' ? <Evidence /> : data?.empty ? (
        <Section title="No scan yet">
          <div className="py-6 text-center">
            <div className="text-[13px] text-gray-300 mb-2">Nothing has been scanned yet.</div>
            <button onClick={scan} disabled={busy || !meta.connected} className="btn-primary !py-1.5 !px-3 text-[12.5px]">
              Run the first scan
            </button>
            <div className="text-[11.5px] text-gray-500 mt-2">
              After this, it scans itself every trading day once the market closes.
            </div>
          </div>
        </Section>
      ) : (
        <>
          <Board board={board} changes={data?.changes || []} stage={filters.screens.length ? '' : filters.stage}
            setStage={(s) => setFilters({ ...filters, stage: s, screens: [] })} headline={headline} />

          <div className="card !p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="flex items-center gap-1.5 text-[11.5px] font-semibold uppercase tracking-wider text-gray-400">
                <Layers className="w-3.5 h-3.5" />Screens
              </span>
              {Object.entries(meta.screens || {}).map(([k, sc]) => {
                const on = filters.screens.includes(k);
                const count = (data?.screens || {})[k]?.count;
                return (
                  <button key={k} title={sc.blurb}
                    onClick={() => setFilters({ ...filters,
                      screens: on ? filters.screens.filter((x) => x !== k) : [...filters.screens, k] })}
                    className={`px-2 py-1 rounded-lg border text-[11.5px] ${on
                      ? 'border-brand-500/60 bg-brand-500/15 text-brand-300' : 'border-surface-3 bg-surface-2/50 text-gray-300 hover:text-white'}`}>
                    {sc.name}{count != null && <span className="text-gray-500 ml-1">{count}</span>}
                  </button>
                );
              })}
              {filters.screens.length > 1 && (
                <div className="flex rounded-lg border border-surface-3 overflow-hidden ml-1">
                  {[['any', 'match any'], ['all', 'match all']].map(([v, l]) => (
                    <button key={v} onClick={() => setFilters({ ...filters, combine: v })}
                      className={`px-2 py-1 text-[11px] ${filters.combine === v ? 'bg-brand-500/20 text-brand-300' : 'text-gray-400 hover:text-gray-200'}`}>{l}</button>
                  ))}
                </div>
              )}
              {filters.screens.length > 0 && (
                <button onClick={() => setFilters({ ...filters, screens: [] })}
                  className="text-[11px] text-gray-500 hover:text-gray-300 ml-auto">back to the stages</button>
              )}
            </div>
          </div>
          <StockGrid rows={data?.rows || []} total={data?.total || 0} industries={data?.industries || []}
            filters={filters} setFilters={setFilters}
            blurb={filters.screens.length === 1 ? (meta.screens[filters.screens[0]] || {}).blurb : filters.screens.length ? null : BLURB[filters.stage]}
            stageLabel={filters.screens.length
              ? `${filters.screens.map((k) => (meta.screens[k] || {}).name).join(filters.combine === 'all' ? ' + ' : ' or ')} — the names`
              : `${label} — the names`} />
        </>
      )}

      <Section title="How these are found" right={
        <button onClick={() => setShowHow(!showHow)} className="text-[11.5px] text-gray-400 hover:text-gray-200 flex items-center gap-1">
          <BookOpen className="w-3.5 h-3.5" />{showHow ? 'hide' : 'show'}
        </button>}>
        {showHow ? (
          <ol className="space-y-1.5 list-decimal pl-4">
            {(meta.how || []).map((h, i) => <li key={i} className="text-[12.5px] text-gray-300">{h}</li>)}
            <li className="text-[12.5px] text-gray-300">
              Universe: {meta.universe?.source} ({meta.universe?.count} stocks, list refreshed {meta.universe?.fetched}).
              {meta.universe?.note ? ` ${meta.universe.note}` : ''}
            </li>
            <li className="text-[12.5px] text-gray-300">
              Every stock needs at least ₹{meta.params?.min_turnover_cr} crore of median daily turnover, so what
              you see can actually be traded in size.
            </li>
          </ol>
        ) : (
          <div className="text-[12px] text-gray-500">
            Strong stocks only · tight base under a ceiling · breakout on {meta.params?.breakout_volume}× volume ·
            stop {meta.params?.stop_pct}% below the entry · scanned daily after the close.
          </div>
        )}
      </Section>
    </div>
  );
}
