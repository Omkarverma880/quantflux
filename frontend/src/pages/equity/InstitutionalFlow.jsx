import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Landmark, RefreshCw, Loader2, AlertTriangle, ArrowUpDown, Info,
} from 'lucide-react';
import { api } from '../../api';

/**
 * FII_DII_Equity Activity Watcher.
 *
 * An institutional-flow terminal: what the two sides did, what the tape did, and what is simply
 * not published. The last of those matters as much as the first — every panel that has no real
 * source says so in place of a number, because a plausible-looking fabrication is worse here
 * than a blank.
 *
 * It does not reset at the close. At 15:31 the same data is relabelled as the session's final
 * picture and stays on screen until the next one opens.
 */

const CR = (v, d = 0) => (v == null || Number.isNaN(Number(v)) ? '—'
  : `₹${Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d })} Cr`);
const N = (v, d = 2) => (v == null || Number.isNaN(Number(v)) ? '—'
  : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
const N0 = (v) => (v == null ? '—' : Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 }));
const PCT = (v, d = 2) => (v == null ? '—' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(d)}%`);
const tone = (v) => (v == null ? 'text-gray-400' : v > 0 ? 'text-emerald-400' : v < 0 ? 'text-red-400' : 'text-gray-300');

const TONE_DOT = { live: 'bg-emerald-500', closed: 'bg-red-500', delayed: 'bg-amber-500' };
const TONE_TEXT = { live: 'text-emerald-400', closed: 'text-red-400', delayed: 'text-amber-400' };

const HISTORY_SPANS = [['5', '5 sessions'], ['10', '10 sessions'], ['22', '1 month'],
  ['66', '3 months'], ['132', '6 months'], ['260', '1 year']];

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
  const [hist, setHist] = useState(null);
  const [yday, setYday] = useState(null);
  const [universe, setUniverse] = useState('NIFTY50');
  const [span, setSpan] = useState('10');
  const [rank, setRank] = useState('rvol');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const timer = useRef(null);

  const load = useCallback(async (opts = {}) => {
    try {
      const s = await api.instSnapshot({ universe, limit: 50, refresh: opts.force ? 1 : 1 });
      if (s.status === 'error') throw new Error(s.message);
      setSnap(s);
      setErr('');
    } catch (e) { setErr(String(e.message || e)); }
  }, [universe]);

  useEffect(() => { api.instMeta().then(setMeta).catch(() => {}); }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { api.instHistory(span).then(setHist).catch(() => {}); }, [span]);
  useEffect(() => { api.instYesterday(universe).then(setYday).catch(() => {}); }, [universe]);

  // poll only while the market is actually open — never out of hours, which is where most
  // dashboards quietly burn their rate limit
  useEffect(() => {
    clearInterval(timer.current);
    if (!snap?.market?.should_poll) return undefined;
    timer.current = setInterval(() => load(), 60000);
    return () => clearInterval(timer.current);
  }, [snap?.market?.should_poll, load]);

  const sync = async () => {
    setBusy(true);
    try {
      await api.instSync();
      await load({ force: true });
      api.instHistory(span).then(setHist).catch(() => {});
    } catch (e) { setErr(String(e.message || e)); } finally { setBusy(false); }
  };

  const m = snap?.market || meta?.market;
  const agg = snap?.aggregate;
  const fii = agg?.fii, dii = agg?.dii;

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

  return (
    <div className="space-y-4">
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

      {/* ── the aggregate ─────────────────────────────────────────── */}
      <Card title="FII / FPI and DII — aggregate cash-market flow"
        right={<Provenance d={agg} />}>
        {agg?.available ? (
          <>
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
              <Side label="FII / FPI" rec={fii} />
              <Side label="DII" rec={dii} />
              <div className="rounded-lg border border-surface-3 bg-surface-2/40 p-3">
                <div className="text-[10px] uppercase tracking-wider text-gray-500">Institutional balance</div>
                <div className={`text-[22px] font-bold mono mt-1 ${tone(agg.balance)}`}>{CR(agg.balance)}</div>
                <div className="text-[11px] text-gray-500 mt-1 leading-snug">{agg.balance_label}</div>
              </div>
            </div>
            {agg.stale && (
              <div className="mt-3"><Note tone="warn">
                This is the last successful reading, not a fresh one.
                {agg.error ? ` The latest attempt failed: ${agg.error}.` : ''}
                {' '}Shown because stale-and-labelled beats blank-or-invented.
              </Note></div>
            )}
          </>
        ) : (
          <Note tone="warn">
            <b>Data unavailable.</b>{' '}
            {agg?.error ? `The source did not answer: ${agg.error}.` : 'The source did not answer.'}
            {agg?.retrieved_at ? ` Last attempt ${agg.retrieved_at}.` : ''}
            {' '}No figure is shown rather than a zero, which would read as "no flow".
          </Note>
        )}
      </Card>

      {/* ── intraday, which does not exist publicly ───────────────── */}
      <Card title="Intraday institutional flow">
        <Note tone="info">{snap?.intraday?.message
          || 'Intraday institutional transaction values are not available from the current source.'}</Note>
        <p className="text-[11px] text-gray-600 mt-2">
          A daily total divided into hourly buckets would look like a flow chart and carry no
          information, so none is drawn.
        </p>
      </Card>

      {/* ── stock-level attribution, likewise ─────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card title="FII stock activity">
          <Note tone="info">{snap?.fii_stocks?.message}</Note>
        </Card>
        <Card title="DII stock activity">
          <Note tone="info">{snap?.dii_stocks?.message}</Note>
        </Card>
      </div>

      {/* ── conflict ─────────────────────────────────────────────── */}
      <Card title="Institutional conflict">
        {snap?.conflict?.available ? (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <span className={`px-2 py-1 rounded text-[12.5px] font-semibold ${snap.conflict.divergence
                ? 'bg-amber-500/15 text-amber-300 border border-amber-500/40'
                : 'bg-surface-3 text-gray-300'}`}>
                {snap.conflict.divergence ? 'FII ↔ DII divergence' : 'No divergence'}
              </span>
              <span className="text-[13px] text-gray-200">{snap.conflict.headline}</span>
              <span className="text-[12px] mono text-gray-400">
                FII {CR(snap.conflict.fii_net)} · DII {CR(snap.conflict.dii_net)} · gap {CR(snap.conflict.gap)}
              </span>
            </div>
            <p className="text-[11px] text-gray-600 mt-2">{snap.conflict.note}</p>
          </>
        ) : <Note tone="info">{snap?.conflict?.message || 'Needs both net figures.'}</Note>}
      </Card>

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
            Connect Zerodha to price this list. The institutional figures above come from NSE and
            do not need it; price, volume and relative volume do.
          </Note>
        ) : (
          <>
            <div className="text-[11px] text-gray-500 mb-2">
              {snap?.universe?.source} · {snap?.stocks?.length || 0} names ·
              relative volume is today against a 20-day average from the app's own daily cache
            </div>
            <Table
              cols={['Stock', 'Price', '% change', 'Volume', 'vs 20d avg', 'Institutional activity', 'Confirmation']}
              align={['left', 'right', 'right', 'right', 'right', 'left', 'left']}>
              {stocks.map((s) => (
                <tr key={s.symbol} className="border-b border-surface-3/40">
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

      {/* ── yesterday → today ─────────────────────────────────────── */}
      <Card title="Yesterday's institutional activity → today's reaction">
        {yday?.available ? (
          <Table cols={['Stock', 'Yesterday', 'Date', 'Price', 'Today %', 'Volume', 'vs 20d avg', 'Reaction']}
            align={['left', 'left', 'left', 'right', 'right', 'right', 'right', 'left']}>
            {(yday.rows || []).map((r) => (
              <tr key={r.symbol} className="border-b border-surface-3/40">
                <td className="px-2 py-1.5 font-semibold text-gray-100">{r.symbol}</td>
                <td className="px-2 py-1.5 text-[11.5px] text-gray-400">{r.institution} {r.yesterday_activity}</td>
                <td className="px-2 py-1.5 mono text-gray-500">{r.yesterday_date}</td>
                <td className="px-2 py-1.5 text-right mono text-gray-200">{N(r.price)}</td>
                <td className={`px-2 py-1.5 text-right mono ${tone(r.price_change_pct)}`}>{PCT(r.price_change_pct)}</td>
                <td className="px-2 py-1.5 text-right mono text-gray-400">{N0(r.volume)}</td>
                <td className="px-2 py-1.5 text-right mono text-gray-400">
                  {r.relative_volume == null ? '—' : `${N(r.relative_volume, 2)}x`}</td>
                <td className="px-2 py-1.5 text-[11.5px] text-gray-300">{r.reaction}</td>
              </tr>
            ))}
          </Table>
        ) : <Note tone="info">{yday?.message || 'Nothing recorded for the previous session yet.'}</Note>}
      </Card>

      {/* ── history ──────────────────────────────────────────────── */}
      <Card title="Historical institutional flow"
        right={
          <select value={span} onChange={(e) => setSpan(e.target.value)} className={SEL}>
            {HISTORY_SPANS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        }>
        {hist?.rows?.length ? (
          <Table cols={['Date', 'FII buy', 'FII sell', 'FII net', 'DII buy', 'DII sell', 'DII net', 'Final']}
            align={['left', 'right', 'right', 'right', 'right', 'right', 'right', 'left']}>
            {hist.rows.map((r) => (
              <tr key={r.trading_date} className="border-b border-surface-3/40">
                <td className="px-2 py-1.5 mono text-gray-300">{r.trading_date}</td>
                <td className="px-2 py-1.5 text-right mono text-gray-400">{CR(r.fii_buy)}</td>
                <td className="px-2 py-1.5 text-right mono text-gray-400">{CR(r.fii_sell)}</td>
                <td className={`px-2 py-1.5 text-right mono font-semibold ${tone(r.fii_net)}`}>{CR(r.fii_net)}</td>
                <td className="px-2 py-1.5 text-right mono text-gray-400">{CR(r.dii_buy)}</td>
                <td className="px-2 py-1.5 text-right mono text-gray-400">{CR(r.dii_sell)}</td>
                <td className={`px-2 py-1.5 text-right mono font-semibold ${tone(r.dii_net)}`}>{CR(r.dii_net)}</td>
                <td className="px-2 py-1.5 text-[11px] text-gray-500">{r.is_final ? 'final' : 'provisional'}</td>
              </tr>
            ))}
          </Table>
        ) : (
          <Note tone="info">
            No sessions stored yet. Each reading is written down as it arrives, so this fills in
            from the day the module is first used.
          </Note>
        )}
      </Card>

      {/* ── what this screen can and cannot know ──────────────────── */}
      <Card title="What this screen can and cannot know">
        <ul className="space-y-1.5 text-[12.5px] text-gray-300 list-disc list-inside">
          <li><b>Published daily:</b> aggregate FII/FPI and DII cash-market buy, sell and net, by
            NSE, once after the close. That is the figure at the top.</li>
          <li><b>Live from your broker:</b> price, volume and the 20-day relative volume for the
            chosen universe.</li>
          <li><b>Not published:</b> intraday institutional flow, and daily per-stock FII/DII
            attribution. Both are marked unavailable rather than estimated.</li>
          <li><b>A different thing entirely:</b> shareholding changes are quarterly filings. A
            rise in FII shareholding is not "FII bought today", and is never shown as such.</li>
        </ul>
        {!!meta?.sources?.length && (
          <div className="mt-3 text-[11px] text-gray-500">
            {meta.sources.map((s) => (
              <div key={s.name}>• {s.name} — {s.provides} ({s.cadence})</div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

const SEL = 'input-field !py-1 !px-2 text-[12px]';

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

function Provenance({ d }) {
  if (!d) return null;
  return (
    <div className="text-[10.5px] text-gray-500 text-right leading-tight">
      <div>{d.source || 'source unknown'}</div>
      <div className="mono">
        {d.data_date ? `data for ${d.data_date}` : 'no data date'}
        {d.retrieved_at ? ` · read ${String(d.retrieved_at).slice(11, 19)} IST` : ''}
        {d.age?.text ? ` · ${d.age.text}` : ''}
        {d.is_final ? ' · final' : ''}
      </div>
    </div>
  );
}

function Side({ label, rec }) {
  const net = rec?.net;
  return (
    <div className="rounded-lg border border-surface-3 bg-surface-2/40 p-3">
      <div className="text-[10px] uppercase tracking-wider text-gray-500">{label}</div>
      <div className={`text-[22px] font-bold mono mt-1 ${tone(net)}`}>{CR(net)}</div>
      <div className="flex items-center gap-4 mt-1.5 text-[11.5px]">
        <span className="text-gray-500">Buy <span className="mono text-gray-300">{CR(rec?.buy)}</span></span>
        <span className="text-gray-500">Sell <span className="mono text-gray-300">{CR(rec?.sell)}</span></span>
      </div>
      <div className="text-[10.5px] text-gray-600 mt-1">net = buy − sell</div>
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
  const cls = { info: 'border-surface-3 text-gray-400', warn: 'border-amber-500/40 text-amber-300 bg-amber-500/5',
    bad: 'border-red-500/40 text-red-300 bg-red-500/5' }[t];
  return (
    <div className={`text-[12px] rounded-lg border px-3 py-2 leading-snug ${cls}`}>
      {t === 'info' && <Info className="w-3.5 h-3.5 inline mr-1.5 -mt-0.5 text-gray-500" />}
      {children}
    </div>
  );
}

function Table({ cols, align = [], children }) {
  return (
    <div className="overflow-x-auto max-h-[480px]">
      <table className="w-full text-[12px]">
        <thead className="sticky top-0 z-10">
          <tr className="bg-surface-3/80 backdrop-blur-sm text-[10px] uppercase tracking-[0.12em]
                         text-gray-300 border-b-2 border-surface-4">
            {cols.map((c, i) => (
              <th key={c} className={`px-2 py-1.5 font-semibold whitespace-nowrap
                ${align[i] === 'right' ? 'text-right' : 'text-left'}`}>
                <span className="inline-flex items-center gap-1">{c}
                  <ArrowUpDown className="w-2.5 h-2.5 opacity-30" /></span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
