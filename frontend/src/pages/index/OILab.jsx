import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ScanSearch, LineChart as LineIcon, Crosshair, Database, BookOpen, RefreshCw, Pause, Play, WifiOff, Wallet, ChevronDown } from 'lucide-react';
import { api } from '../../api';
import { fmtNum, fmtInt, fmtSignedPct, tone, isNum, Empty } from '../../components/oilab/ui';
import { Verdict, KpiStrip, BattleMap, Zones, StrikeTable } from '../../components/oilab/XRay';
import ContractDrawer from '../../components/oilab/ContractDrawer';
import Timeline from '../../components/oilab/Timeline';
import EntryZones from '../../components/oilab/EntryZones';
import HistoryPanel from '../../components/oilab/HistoryPanel';
import SignalDesk from '../../components/oilab/SignalDesk';
import PaperPanel from '../../components/oilab/PaperPanel';
import Guide from '../../components/oilab/Guide';

/**
 * OI Lab — an X-ray of index option open interest: who is writing where, the support and
 * resistance it builds, the buyer/seller fight in every contract, and entry zones scored on
 * odds tested against 3 years of NIFTY history. Read-only research.
 */

const TABS = [
  ['xray', 'OI X-Ray', ScanSearch],
  ['flow', 'Flow & Timeline', LineIcon],
  ['entry', 'AI Entry Zones', Crosshair],
  ['paper', 'Paper Trades', Wallet],
  ['history', 'History & Expiry', Database],
  ['guide', 'How to read', BookOpen],
];
const REFRESH_MS = 20000;
const LIVE_TABS = new Set(['xray', 'flow', 'entry']);
const TRADE_INDICES = new Set(['NIFTY', 'SENSEX']);

function IndexStrip({ overview, active, onPick, indices }) {
  const cards = overview?.indices?.length ? overview.indices : indices.map((i) => ({ index: i.key, label: i.label }));
  return (
    <div className="flex gap-2 overflow-x-auto pb-1 -mx-1 px-1">
      {cards.map((c) => (
        <button key={c.index} onClick={() => onPick(c.index)}
          className={`shrink-0 text-left rounded-xl border px-3 py-2 min-w-[168px] transition ${active === c.index
            ? 'border-brand-500/60 bg-brand-600/10' : 'border-surface-3 bg-surface-1 hover:border-brand-500/30'}`}>
          <div className="flex items-center justify-between gap-2">
            <span className={`text-[12px] font-semibold ${active === c.index ? 'text-brand-400' : 'text-gray-200'}`}>{c.label}</span>
            {isNum(c.change_pct) && <span className={`mono text-[11px] ${tone(c.change_pct)}`}>{fmtSignedPct(c.change_pct)}</span>}
          </div>
          {isNum(c.spot) ? (
            <>
              <div className="mono text-[14px] text-white">{fmtNum(c.spot)}</div>
              <div className="text-[10.5px] text-gray-500 mono">S {fmtInt(c.support)} · R {fmtInt(c.resistance)} · PCR {c.pcr ?? '—'}</div>
              <div className="text-[10.5px] text-gray-400">{c.tilt}{isNum(c.dte) ? ` · ${c.dte === 0 ? 'expiry today' : `${c.dte}d to expiry`}` : ''}</div>
            </>
          ) : <div className="text-[11px] text-gray-500 mt-1">tap to load</div>}
        </button>
      ))}
    </div>
  );
}

export default function OILab() {
  const [tab, setTab] = useState('xray');
  const [meta, setMeta] = useState(null);
  const [index, setIndex] = useState(() => { try { return localStorage.getItem('oilab_index') || 'NIFTY'; } catch { return 'NIFTY'; } });
  const [expiry, setExpiry] = useState('');
  const [span, setSpan] = useState(7);
  const [snap, setSnap] = useState(null);
  const [overview, setOverview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [auto, setAuto] = useState(true);
  const [picked, setPicked] = useState(null);
  const reqId = useRef(0);

  useEffect(() => { api.oiLabMeta().then(setMeta).catch(() => {}); }, []);
  useEffect(() => { try { localStorage.setItem('oilab_index', index); } catch { /* ignore */ } }, [index]);

  const load = useCallback(async (quiet = false) => {
    const id = ++reqId.current;
    if (!quiet) setBusy(true);
    try {
      const r = await api.oiLabSnapshot({ index, expiry: expiry || null, window: span });
      if (id !== reqId.current) return;
      if (r.status === 'ok') { setSnap(r); setErr(null); } else { setErr(r); if (!quiet) setSnap(null); }
    } catch (e) {
      if (id === reqId.current) setErr({ message: String(e.message || e) });
    } finally {
      if (id === reqId.current) setBusy(false);
    }
  }, [index, expiry, span]);

  const loadRef = useRef(load);
  loadRef.current = load;

  const loadOverview = useCallback(() => {
    api.oiLabOverview().then((r) => { if (r.status === 'ok') setOverview(r); }).catch(() => {});
  }, []);

  useEffect(() => { if (LIVE_TABS.has(tab)) load(); }, [index, expiry, span]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (LIVE_TABS.has(tab) && !snap && !busy) load(); }, [tab]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    loadOverview();
    const t = setInterval(() => { if (!document.hidden) loadOverview(); }, 60000);
    const onConn = () => { loadOverview(); loadRef.current(); };
    window.addEventListener('zerodha_connected', onConn);
    return () => { clearInterval(t); window.removeEventListener('zerodha_connected', onConn); };
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  // Refresh while the market is live. The first few refreshes also fill in the OI history tape.
  useEffect(() => {
    if (!auto || !LIVE_TABS.has(tab)) return undefined;
    const tapeFilling = snap && snap.tape && snap.tape.ready < snap.tape.total;
    if (snap && !snap.live_session && !tapeFilling) return undefined;
    const t = setInterval(() => { if (!document.hidden) load(true); }, tapeFilling ? 8000 : REFRESH_MS);
    return () => clearInterval(t);
  }, [auto, tab, snap, load]);

  const pickIndex = (k) => { setIndex(k); setExpiry(''); setPicked(null); };
  const notConnected = err?.code === 'not_connected';

  return (
    <div className="p-4 sm:p-6 space-y-4 max-w-[1500px] mx-auto">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold text-white">OI Lab</h1>
          <p className="text-xs sm:text-sm text-gray-500 mt-0.5">
            Open-interest X-ray · writer battle · accumulation zones · buyer/seller fight · entry zones tested on 3 years of NIFTY
          </p>
        </div>
        {snap && LIVE_TABS.has(tab) && (
          <div className="text-[11.5px] text-gray-500 flex items-center gap-2">
            <span className={`w-2 h-2 rounded-full ${snap.live_session ? 'bg-green-500 animate-pulse' : 'bg-gray-500'}`} />
            {snap.live_session ? `Live · ${snap.mins_left} min left` : `Market closed · ${snap.session} session`} · updated {snap.fetched_at}
            {snap.tape && snap.tape.ready < snap.tape.total && <span className="text-amber-400">· loading OI history {snap.tape.ready}/{snap.tape.total}</span>}
          </div>
        )}
      </div>

      <IndexStrip overview={overview} active={index} onPick={pickIndex} indices={meta?.indices || [{ key: 'NIFTY', label: 'NIFTY 50' }]} />

      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-surface-3">
        <div className="flex gap-1 overflow-x-auto">
          {TABS.map(([id, label, Icon]) => (
            <button key={id} onClick={() => setTab(id)}
              className={`flex items-center gap-1.5 px-3.5 py-2 text-sm font-semibold border-b-2 -mb-px transition whitespace-nowrap ${
                tab === id ? 'border-brand-500 text-brand-400' : 'border-transparent text-gray-400 hover:text-gray-200'}`}>
              <Icon className="w-4 h-4" />{label}
            </button>
          ))}
        </div>
        {LIVE_TABS.has(tab) && (
          <div className="flex flex-wrap items-center gap-2 pb-1.5">
            <select className="input-field !py-1.5 !text-[12.5px]" value={expiry} onChange={(e) => setExpiry(e.target.value)} aria-label="Expiry">
              <option value="">Nearest expiry{snap && !expiry ? ` (${snap.expiry})` : ''}</option>
              {(snap?.expiries || []).map((e) => <option key={e} value={e}>{e}</option>)}
            </select>
            <select className="input-field !py-1.5 !text-[12.5px]" value={span} onChange={(e) => setSpan(Number(e.target.value))} aria-label="Strikes around ATM">
              {[5, 7, 10, 12].map((n) => <option key={n} value={n}>ATM ±{n}</option>)}
            </select>
            <button className="btn-ghost !px-2.5 !py-1.5" onClick={() => setAuto((a) => !a)} title={auto ? 'Pause auto-refresh' : 'Resume auto-refresh'}>
              {auto ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
            </button>
            <button className="btn-primary !px-3 !py-1.5 text-[12.5px] flex items-center gap-1.5" onClick={() => load()} disabled={busy}>
              <RefreshCw className={`w-3.5 h-3.5 ${busy ? 'animate-spin' : ''}`} />Refresh
            </button>
          </div>
        )}
      </div>

      {LIVE_TABS.has(tab) && (
        notConnected ? (
          <div className="card text-center py-10 space-y-2">
            <WifiOff className="w-8 h-8 text-gray-500 mx-auto" />
            <div className="text-gray-200 font-semibold">Zerodha is not connected</div>
            <div className="text-[12.5px] text-gray-400">{err.message}</div>
            <button className="btn-secondary !py-1.5 text-[12.5px]" onClick={() => setTab('history')}>Open History & Expiry</button>
          </div>
        ) : err && !snap ? (
          <div className="card text-center py-8 space-y-2">
            <div className="text-red-400 text-[13px]">{err.message}</div>
            <button className="btn-secondary !py-1.5 text-[12.5px]" onClick={() => load()}>Try again</button>
          </div>
        ) : !snap ? (
          <Empty>{busy ? `Reading the ${index} option chain…` : 'Loading…'}</Empty>
        ) : (
          <>
            {err && <div className="text-[12px] text-amber-400">Last refresh failed: {err.message} — showing {snap.fetched_at} data.</div>}
            {tab === 'xray' && (
              <div className="space-y-4">
                <KpiStrip snap={snap} />
                <Verdict snap={snap} />
                <div className="grid gap-4 xl:grid-cols-[1.15fr_1fr]">
                  <BattleMap snap={snap} onPick={setPicked} />
                  <Zones snap={snap} />
                </div>
                <StrikeTable snap={snap} onPick={setPicked} />
              </div>
            )}
            {tab === 'flow' && <Timeline snap={snap} />}
            {tab === 'entry' && (
              TRADE_INDICES.has(index) ? (
                <div className="space-y-4">
                  <SignalDesk index={index} active={tab === 'entry'} onPaper={() => {}} />
                  <details className="group" open={false}>
                    <summary className="cursor-pointer list-none flex items-center gap-2 text-[13px] font-semibold text-gray-200 card !py-3">
                      <ChevronDown className="w-4 h-4 transition group-open:rotate-180" />
                      OI wall setups (planning view) — bounce, rejection, breakout and range levels from the live chain
                    </summary>
                    <div className="mt-4"><EntryZones snap={snap} /></div>
                  </details>
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="rounded-lg border border-surface-3 bg-surface-2 px-3 py-2 text-[12.5px] text-gray-400">
                    The timed Signal Desk and paper trading run on NIFTY and SENSEX. {index} shows the OI wall planning view below.
                  </div>
                  <EntryZones snap={snap} />
                </div>
              )
            )}
          </>
        )
      )}
      {tab === 'history' && <HistoryPanel underlying={index} />}
      {tab === 'paper' && <PaperPanel active={tab === 'paper'} />}
      {tab === 'guide' && <Guide />}

      {picked && snap && <ContractDrawer row={picked} snap={snap} onClose={() => setPicked(null)} />}
    </div>
  );
}
