import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Dice5, Loader2, Play, Square, RefreshCw, ShieldCheck, AlertTriangle, Radio, BarChart3, BookOpen, Activity, Crosshair,
} from 'lucide-react';
import { api } from '../../api';
import { Note, Section, Stat, Field, input, N, N0, RS, PCT, tone } from '../../components/fluxlab/ui';

/**
 * CAS Game Play — a bounded bet on the closing-auction dislocation.
 *
 * Start it whenever you like: outside the window it only watches the chain and shows the ₹1
 * contracts it would buy. Paper by default; live orders only when you switch the mode and the app
 * itself is out of paper mode.
 */
const TABS = [['desk', 'Live desk', Radio], ['backtest', 'Backtest', BarChart3], ['rules', 'The bet', BookOpen]];

function Picks({ picks = [], considered = {} }) {
  if (!picks.length) return <Note>Nothing in the price band right now.</Note>;
  return (
    <div className="space-y-2">
      {picks.map((p, i) => (
        <div key={i} className="rounded-lg border border-surface-3 bg-surface-2/40 px-3 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`px-1.5 py-px rounded text-[10.5px] font-bold ${p.option_type === 'CE'
              ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'}`}>{p.option_type}</span>
            <span className="text-[13px] font-semibold text-white mono">{N0(p.strike)}</span>
            <span className="text-[11.5px] text-gray-500">{p.symbol || ''}</span>
            <span className="text-[11.5px] text-gray-400 ml-auto mono">
              bid {N(p.bid)} / ask {N(p.ask)} · {N0(p.gap)} pts from spot
            </span>
          </div>
          <div className="text-[11.5px] text-gray-400 mt-1">
            {p.lots
              ? <>buy <span className="mono text-gray-200">{p.lots} lots ({N0(p.qty)} qty)</span> at
                 {' '}<span className="mono text-gray-200">{N(p.entry)}</span> for
                 {' '}<span className="mono text-gray-200">₹{N0(p.cost)}</span> · target
                 {' '}<span className="mono text-emerald-400">{N(p.target)}</span></>
              : <span className="text-amber-500">{p.skipped}</span>}
          </div>
        </div>
      ))}
      <div className="text-[11px] text-gray-500">
        Considered: {Object.entries(considered).map(([k, v]) => `${v.length} ${k}`).join(' · ')} in the band.
      </div>
    </div>
  );
}

export default function CasGame() {
  const [tab, setTab] = useState('desk');
  const [meta, setMeta] = useState(null);
  const [desk, setDesk] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [params, setParams] = useState(null);
  const [bt, setBt] = useState({ start: '', end: '', result: null, progress: '', running: false, runs: [] });
  const poll = useRef(null);

  const loadDesk = useCallback(async () => {
    const d = await api.casDesk();
    if (d.status === 'ok') setDesk(d); else setErr(d.message || 'could not load the desk');
  }, []);

  useEffect(() => {
    api.casMeta().then((m) => {
      if (m.status !== 'ok') { setErr(m.message); return; }
      setMeta(m);
      setParams(m.config?.params || m.defaults);
      const today = new Date().toISOString().slice(0, 10);
      setBt((b) => ({ ...b, start: `${today.slice(0, 4)}-01-01`, end: today }));
    });
    loadDesk();
    const t = setInterval(loadDesk, 10000);
    return () => { clearInterval(t); if (poll.current) clearInterval(poll.current); };
  }, [loadDesk]);

  useEffect(() => { api.casRuns().then((r) => r.status === 'ok' && setBt((b) => ({ ...b, runs: r.runs }))); }, [bt.result]);

  const act = async (fn) => {
    setBusy(true); setErr('');
    try { const r = await fn(); if (r.status !== 'ok') throw new Error(r.message); await loadDesk(); }
    catch (e) { setErr(String(e.message || e)); } finally { setBusy(false); }
  };
  const saveParams = (patch) => {
    const next = { ...params, ...patch };
    setParams(next);
    api.casConfig({ params: next }).then(() => loadDesk());
  };

  const runBacktest = async () => {
    setBt((b) => ({ ...b, running: true, progress: 'starting' })); setErr('');
    try {
      const r = await api.casBacktest({ config: { start: bt.start, end: bt.end, params } });
      if (r.status !== 'ok') throw new Error(r.message);
      poll.current = setInterval(async () => {
        const j = await api.casJob(r.job.id);
        const job = j.job || {};
        setBt((b) => ({ ...b, progress: job.progress || '' }));
        if (job.status === 'done') {
          clearInterval(poll.current);
          setBt((b) => ({ ...b, running: false, result: job.result }));
        } else if (job.status === 'error') {
          clearInterval(poll.current);
          setBt((b) => ({ ...b, running: false }));
          setErr(job.error || 'backtest failed');
        }
      }, 1500);
    } catch (e) { setBt((b) => ({ ...b, running: false })); setErr(String(e.message || e)); }
  };

  if (!meta || !params) {
    return <div className="p-6 text-center text-gray-500 text-sm">
      {err ? <span className="text-red-400">{err}</span> : <><Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />Loading…</>}
    </div>;
  }
  const s = bt.result?.summary;
  const live = desk?.mode === 'live';
  const isPair = (params?.structure || 'strangle') === 'strangle';

  return (
    <div className="p-4 sm:p-6 space-y-4 max-w-[1500px] mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl sm:text-2xl font-bold text-white flex items-center gap-2">
            <Dice5 className="w-5 h-5 text-brand-400" />CAS Game Play
          </h1>
          <p className="text-xs sm:text-sm text-gray-500 mt-0.5">
            A fixed, small bet on the closing-auction dislocation: buy the ~₹1 options and sell if they run.
            {' '}{meta.schedule?.cash_auction}; {meta.schedule?.derivatives_close}.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className={`text-[11px] px-2 py-0.5 rounded-full ${desk?.connected
            ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'}`}>
            {desk?.connected ? 'Zerodha connected' : 'not connected'}
          </span>
          <span className={`text-[11px] px-2 py-0.5 rounded-full ${live
            ? 'bg-red-500/20 text-red-300 font-semibold' : 'bg-surface-3 text-gray-300'}`}>
            {live ? 'LIVE ORDERS' : 'paper'}
          </span>
          <span className="text-[11px] text-gray-500 mono">{desk?.now}</span>
          {desk?.running
            ? <button disabled={busy} onClick={() => act(api.casStop)} className="btn-secondary !py-1.5 !px-3 text-[12.5px] flex items-center gap-1.5">
                <Square className="w-3.5 h-3.5" />Stop</button>
            : <button disabled={busy || !desk?.connected} onClick={() => act(api.casStart)} className="btn-secondary !py-1.5 !px-3 text-[12.5px] flex items-center gap-1.5">
                <Play className="w-3.5 h-3.5" />Start watching</button>}
          {/* skips the scheduled window: entry opens from this minute until square-off */}
          {desk?.window?.hunting_now
            ? <span className="text-[11px] px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-300 font-semibold">
                hunting since {desk?.window?.from}</span>
            : <button disabled={busy || !desk?.connected} onClick={() => act(api.casHuntNow)}
                      title="Start hunting from right now instead of waiting for the window"
                      className="btn-primary !py-1.5 !px-3 text-[12.5px] flex items-center gap-1.5">
                <Crosshair className="w-3.5 h-3.5" />Hunt now</button>}
        </div>
      </div>

      {err && <div className="text-[12px] text-red-400">{err}</div>}

      <div className="flex gap-1 border-b border-surface-3 overflow-x-auto">
        {TABS.map(([id, label, Icon]) => (
          <button key={id} onClick={() => setTab(id)}
            className={`flex items-center gap-1.5 px-3.5 py-2 text-sm font-semibold border-b-2 -mb-px transition whitespace-nowrap ${tab === id
              ? 'border-brand-500 text-brand-400' : 'border-transparent text-gray-400 hover:text-gray-200'}`}>
            <Icon className="w-4 h-4" />{label}
          </button>
        ))}
      </div>

      {tab === 'desk' && (
        <div className="space-y-4">
          <Section title="Today" right={
            <div className="flex items-center gap-2">
              <button disabled={busy || !desk?.connected} onClick={() => act(api.casScan)}
                className="btn-secondary !py-1 !px-2 text-[11.5px] flex items-center gap-1">
                <RefreshCw className="w-3 h-3" />Scan now
              </button>
              <select value={desk?.mode || 'paper'} disabled={desk?.running}
                onChange={(e) => act(() => api.casConfig({ mode: e.target.value }))}
                className="bg-surface-2 border border-surface-3 rounded px-2 py-1 text-[11.5px] text-gray-300">
                <option value="paper">paper</option>
                <option value="live">live orders</option>
              </select>
            </div>}>
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-3">
              <Stat label="Window" value={`${desk?.window?.from}–${desk?.window?.to}`}
                    sub={desk?.window?.hunting_now
                      ? `hunting now · scheduled ${desk?.window?.scheduled?.from}–${desk?.window?.scheduled?.to}`
                      : desk?.window?.state} />
              <Stat label="Square-off" value={desk?.window?.squareoff} sub="before the 15:40 close" />
              <Stat label="Spot" value={N(desk?.scan?.spot)} sub={desk?.scan?.expiry ? `expiry ${desk.scan.expiry}` : ''} />
              <Stat label="Staked" value={RS(desk?.totals?.spent)} sub={`${desk?.totals?.tickets || 0} tickets`} />
              <Stat label="P&L" value={RS((desk?.totals?.realised || 0) + (desk?.totals?.unrealised || 0))}
                tone={tone((desk?.totals?.realised || 0) + (desk?.totals?.unrealised || 0))}
                sub={`${RS(desk?.totals?.realised)} booked`} />
            </div>
            {live && <div className="mb-3"><Note tone="warn">
              Live mode: the next entry goes to Zerodha as a real order. It still refuses if the app is in paper
              mode{meta.app_paper_mode ? ' — which it currently is, so nothing will be sent' : ''} or the risk fence is up.
            </Note></div>}
            <div className="text-[12px] text-gray-400 mb-2">{desk?.scan?.why}</div>
            <Picks picks={desk?.scan?.picks} considered={desk?.scan?.considered} />
          </Section>

          {!!(desk?.tickets || []).length && (
            <Section title="Tickets">
              <table className="w-full text-[12px]">
                <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                  {['Taken', 'Contract', 'Lots', 'Entry', 'Now', 'Target', 'Exit', 'P&L'].map((h) => (
                    <th key={h} className="px-2 py-1 font-medium text-left">{h}</th>))}
                </tr></thead>
                <tbody>
                  {desk.tickets.map((t, i) => (
                    <tr key={i} className="border-b border-surface-3/40">
                      <td className="px-2 py-1 mono text-gray-400">{t.taken_at}</td>
                      <td className="px-2 py-1 mono text-gray-200">{t.option_type} {N0(t.strike)}</td>
                      <td className="px-2 py-1 mono text-gray-300">{t.lots}</td>
                      <td className="px-2 py-1 mono text-gray-300">{N(t.entry)}</td>
                      <td className="px-2 py-1 mono text-gray-300">{N(t.ltp)}</td>
                      <td className="px-2 py-1 mono text-emerald-400">{N(t.target)}</td>
                      <td className="px-2 py-1 text-gray-400">{t.exit_reason || '—'}</td>
                      <td className={`px-2 py-1 mono font-semibold ${tone(t.pnl ?? t.unrealised)}`}>{RS(t.pnl ?? t.unrealised)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Section>
          )}

          <Section title="Settings"
                   right={<button disabled={busy}
                     onClick={() => { if (window.confirm('Replace your saved settings with the shipped rule?')) act(api.casReset); }}
                     className="btn-secondary !py-1 !px-2 text-[11.5px]">Reset to the shipped rule</button>}>
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
              <Field label="Structure" hint="how the bet is placed">
                <select value={params.structure || 'strangle'}
                        onChange={(e) => saveParams({ structure: e.target.value })} className={input}>
                  <option value="strangle">call + put, each with a stop</option>
                  <option value="cheap">the old ₹1 lottery</option>
                </select>
              </Field>
              <Field label="Target (points)" hint="0 = let the winner run">
                <input type="number" value={params.target_points ?? 0}
                       onChange={(e) => saveParams({ target_points: Number(e.target.value) })} className={input} />
              </Field>
              <Field label="Stop per leg (%)" hint="of what that leg cost">
                <input type="number" value={params.stop_pct ?? 35}
                       onChange={(e) => saveParams({ stop_pct: Number(e.target.value) })} className={input} />
              </Field>
              <Field label="Strike" hint="relative to the money">
                <select value={String(params.moneyness ?? 0)}
                        onChange={(e) => saveParams({ moneyness: Number(e.target.value) })} className={input}>
                  <option value="-2">ATM − 2 (deeper in)</option>
                  <option value="-1">ATM − 1 (in the money)</option>
                  <option value="0">ATM (at the money)</option>
                  <option value="1">ATM + 1 (out of the money)</option>
                  <option value="2">ATM + 2 (further out)</option>
                </select>
              </Field>
              <Field label="Lead before auction" hint="minutes; will not open inside this">
                <input type="number" min="0" value={params.min_lead_min ?? 20}
                       onChange={(e) => saveParams({ min_lead_min: Number(e.target.value) })} className={input} />
              </Field>
              <Field label="Skip above (₹)" hint="too dear a leg, 0 = off">
                <input type="number" value={params.max_premium ?? 0}
                       onChange={(e) => saveParams({ max_premium: Number(e.target.value) })} className={input} />
              </Field>
              <Field label="Index">
                <select value={params.index} onChange={(e) => saveParams({ index: e.target.value })} className={input}>
                  {meta.indices.map((i) => <option key={i} value={i}>{i}</option>)}
                </select>
              </Field>
              <Field label="Price from" hint={isPair ? 'only for the ₹1 rule' : 'the cheap end of the band'}>
                <input type="number" step="0.05" value={params.price_min} disabled={isPair}
                       onChange={(e) => saveParams({ price_min: Number(e.target.value) })}
                       className={`${input} ${isPair ? 'opacity-40' : ''}`} />
              </Field>
              <Field label="Price to">
                <input type="number" disabled={isPair} step="0.05" value={params.price_max} onChange={(e) => saveParams({ price_max: Number(e.target.value) })} className={`${input} ${isPair ? 'opacity-40' : ''}`} />
              </Field>
              <Field label="Budget (₹)" hint="the whole risk">
                <input type="number" step="500" value={params.budget} onChange={(e) => saveParams({ budget: Number(e.target.value) })} className={input} />
              </Field>
              <Field label="Target (points)">
                <input type="number" step="1" value={params.target_points} onChange={(e) => saveParams({ target_points: Number(e.target.value) })} className={input} />
              </Field>
              <Field label="Sides">
                <select value={params.sides} onChange={(e) => saveParams({ sides: e.target.value })} className={input}>
                  <option value="both">call and put</option>
                  <option value="call">calls only</option>
                  <option value="put">puts only</option>
                </select>
              </Field>
              <Field label="Per side" hint="how many contracts">
                <input type="number" min="1" max="10" value={params.per_side} onChange={(e) => saveParams({ per_side: Number(e.target.value) })} className={input} />
              </Field>
              <Field label="Window from"><input type="number" value={params.entry_from} onChange={(e) => saveParams({ entry_from: Number(e.target.value) })} className={input} /></Field>
              <Field label="Window to"><input type="number" value={params.entry_to} onChange={(e) => saveParams({ entry_to: Number(e.target.value) })} className={input} /></Field>
              <Field label="Square-off"><input type="number" value={params.squareoff} onChange={(e) => saveParams({ squareoff: Number(e.target.value) })} className={input} /></Field>
            </div>
            <div className="text-[11px] text-gray-500 mt-2">
              Times are minutes from midnight: 15:15 is 915, 15:35 is 935, 15:39 is 939.
            </div>
          </Section>

          {!!(desk?.log || []).length && (
            <Section title="What it did">
              <div className="space-y-0.5 max-h-[220px] overflow-y-auto">
                {desk.log.slice().reverse().map((l, i) => <div key={i} className="text-[11.5px] text-gray-400 mono">{l}</div>)}
              </div>
            </Section>
          )}
        </div>
      )}

      {tab === 'backtest' && (
        <div className="space-y-4">
          <Section title="Test it on the stored chain">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 items-end">
              <Field label="From"><input type="date" value={bt.start} onChange={(e) => setBt({ ...bt, start: e.target.value })} className={input} /></Field>
              <Field label="To"><input type="date" value={bt.end} onChange={(e) => setBt({ ...bt, end: e.target.value })} className={input} /></Field>
              <div className="text-[11.5px] text-gray-500">Uses the settings on the Live desk tab, expiry days only.</div>
              <button disabled={bt.running} onClick={runBacktest} className="btn-primary !py-1.5 text-[12.5px] flex items-center justify-center gap-1.5">
                {bt.running ? <Loader2 className="w-4 h-4 animate-spin" /> : <Activity className="w-4 h-4" />}
                {bt.running ? 'Running…' : 'Run backtest'}
              </button>
            </div>
            {bt.running && <div className="text-[11.5px] text-gray-500 mt-2">{bt.progress}</div>}
          </Section>

          {s && (
            <Section title="Result">
              <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3 mb-3">
                <Stat label="Expiry sessions" value={N0(s.sessions)} sub={`${s.days_traded || 0} had a ticket`} />
                <Stat label="Tickets" value={N0(s.tickets)} />
                <Stat label="Staked" value={RS(s.spent)} />
                <Stat label="Net" value={RS(s.net)} tone={tone(s.net)} />
                <Stat label="Hit the target" value={N0(s.hit_target)} sub={`of ${s.tickets || 0}`} />
                <Stat label="Best ticket" value={RS(s.best_ticket)} tone="text-emerald-400" />
                <Stat label="Best day / worst" value={`${RS(s.best_day)} / ${RS(s.worst_day)}`} />
              </div>
              <div className="text-[11.5px] text-gray-400">
                Median best move while held: {N(s.median_peak_gain_pts)} points · best {N(s.best_peak_gain_pts)} points ·
                exits {Object.entries(s.exits || {}).map(([k, v]) => `${k} ${v}`).join(' · ')}
              </div>
              {s.note && <div className="mt-2"><Note tone="warn">{s.note}</Note></div>}
              {s.timing && (
                <div className="mt-3 text-[11.5px] text-gray-400">
                  <b className="text-gray-300">Timing</b> — entered {s.timing.median_entry} (first
                  {' '}{s.timing.first_entry}, last {s.timing.last_entry}), typical exit
                  {' '}{s.timing.median_exit}. A position is held {s.timing.median_held_min} min on a
                  typical day and up to {s.timing.longest_held_min} min on the days that run;
                  a stopped leg is gone in {s.timing.median_minutes_to_stop} min.
                </div>
              )}

              {!!(s.by_month || []).length && (
                <div className="overflow-x-auto mt-3">
                  <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Month by month</div>
                  <table className="w-full text-[12px] min-w-[560px]">
                    <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                      {['Month', 'Sessions', 'Green', 'Staked', 'Best day', 'Worst day', 'Net']
                        .map((h) => <th key={h} className="px-2 py-1 text-left font-medium">{h}</th>)}
                    </tr></thead>
                    <tbody>
                      {s.by_month.map((m) => (
                        <tr key={m.month} className="border-b border-surface-3/40">
                          <td className="px-2 py-1 mono text-gray-300">{m.month}</td>
                          <td className="px-2 py-1 mono text-gray-400">{m.sessions}</td>
                          <td className="px-2 py-1 mono text-gray-400">{m.green_days}/{m.sessions}</td>
                          <td className="px-2 py-1 mono text-gray-400">{RS(m.spent)}</td>
                          <td className="px-2 py-1 mono text-emerald-400">{RS(m.best_day)}</td>
                          <td className="px-2 py-1 mono text-red-400">{RS(m.worst_day)}</td>
                          <td className={`px-2 py-1 mono font-semibold ${tone(m.net)}`}>{RS(m.net)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {!!(s.by_day || []).length && (
                <div className="overflow-x-auto mt-3 max-h-[420px]">
                  <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">Day by day</div>
                  <table className="w-full text-[12px] min-w-[720px]">
                    <thead className="sticky top-0 bg-surface-1">
                      <tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                        {['Expiry day', 'In', 'Out', 'Lots', 'Staked', 'Winner', 'Loser', 'Exits', 'Net']
                          .map((h) => <th key={h} className="px-2 py-1 text-left font-medium">{h}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {s.by_day.map((d) => (
                        <tr key={d.date} className="border-b border-surface-3/40">
                          <td className="px-2 py-1 mono text-gray-300">{d.date}</td>
                          <td className="px-2 py-1 mono text-gray-400">{d.entry_time}</td>
                          <td className="px-2 py-1 mono text-gray-400">{d.exit_time}</td>
                          <td className="px-2 py-1 mono text-gray-400">{d.lots ?? '—'}</td>
                          <td className="px-2 py-1 mono text-gray-400">{RS(d.spent)}</td>
                          <td className="px-2 py-1 mono text-emerald-400">{d.winner} {RS(d.winner_pnl)}</td>
                          <td className="px-2 py-1 mono text-red-400">{d.loser} {RS(d.loser_pnl)}</td>
                          <td className="px-2 py-1 text-[10.5px] text-gray-500">{d.exits}</td>
                          <td className={`px-2 py-1 mono font-semibold ${tone(d.net)}`}>{RS(d.net)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Section>
          )}
        </div>
      )}

      {tab === 'rules' && (
        <div className="space-y-4">
          <Section title="The bet, in words">
            <ol className="space-y-2 list-decimal pl-4">
              {(desk?.rules || meta.rules || []).map((r, i) => <li key={i} className="text-[12.5px] text-gray-300">{r}</li>)}
            </ol>
          </Section>
          <Section title="Why this window">
            <div className="text-[12.5px] text-gray-300 space-y-1.5">
              <p>{meta.schedule?.why}</p>
              <p className="text-gray-400">{meta.schedule?.cash_auction}</p>
              <p className="text-gray-400">{meta.schedule?.derivatives_close}</p>
            </div>
          </Section>
          <Section title="What you are risking">
            <div className="space-y-1.5">
              {[
                'Nearly every ticket expires worthless — that is the shape of the bet, not a fault in it.',
                'The budget is the loss. If the premium goes to zero you lose what you staked, and nothing more.',
                'A ₹1 option has a wide spread: paying one tick more is already 5%, and the fill can be worse in a fast auction.',
                'Large quantities may exceed the exchange freeze limit and need splitting into several orders.',
                'The backtest can only see the strikes your ingestion stored, and older sessions stop at 15:29 — before the minutes that matter.',
              ].map((t, i) => (
                <div key={i} className="flex items-start gap-1.5">
                  <AlertTriangle className="w-3.5 h-3.5 text-amber-500 shrink-0 mt-0.5" />
                  <span className="text-[12px] text-gray-300">{t}</span>
                </div>
              ))}
              <div className="flex items-start gap-1.5 pt-1">
                <ShieldCheck className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
                <span className="text-[12px] text-gray-300">
                  In paper mode nothing is sent to the broker. Live mode also refuses while the app itself is in
                  paper mode or the risk fence is up.
                </span>
              </div>
            </div>
          </Section>
        </div>
      )}
    </div>
  );
}
