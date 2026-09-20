import React, { useCallback, useEffect, useState } from 'react';
import { Loader2, RefreshCw, ShieldCheck, AlertTriangle, Activity } from 'lucide-react';
import { api } from '../../api';
import { Section, Stat, StatTable, Note, N, N0, PCT, RS, tone } from './ui';

/**
 * Live paper trading — the same engine, fed by the live session instead of stored candles.
 *
 * PAPER is the only mode here. The backend module that runs this has no order path in it at
 * all, and the banner says so on every screen, because the one mistake this lab must never make
 * is sending a real order while you believe you are testing.
 */

export default function Paper({ cfg, runId }) {
  const [data, setData] = useState(null);
  const [signals, setSignals] = useState([]);
  const [compare, setCompare] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  const load = useCallback(async () => {
    try {
      const [d, s] = await Promise.all([api.flMePaper(), api.flPaperSignals(60)]);
      if (d.status === 'ok') setData(d);
      if (s.status === 'ok') setSignals(s.signals || []);
    } catch (e) { setMsg(String(e.message || e)); }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, [load]);

  const save = async (patch) => {
    setBusy(true); setMsg('');
    try {
      const r = await api.flPaperConfig(patch);
      if (r.status === 'ok') await load(); else setMsg(r.message || 'could not save');
    } finally { setBusy(false); }
  };

  const tick = async () => {
    setBusy(true); setMsg('');
    try {
      const r = await api.flPaperCheck();
      setMsg(r.status === 'ok'
        ? `checked: ${r.result?.skipped || (r.result?.opened ? `opened ${r.result.opened.contract} at ${r.result.opened.entry}` : r.result?.signal ? 'signal, no fill' : 'no signal on the last settled bar')}`
        : r.message || 'could not run');
      await load();
    } finally { setBusy(false); }
  };

  const loadCompare = async () => {
    if (!runId) return;
    const r = await api.flCompare(runId);
    setCompare(r);
  };

  const st = data?.today_stats || {};
  const open = data?.open_positions || [];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2">
        <ShieldCheck className="w-4 h-4 text-emerald-400 shrink-0" />
        <span className="text-[12.5px] text-emerald-300 font-semibold">PAPER MODE</span>
        <span className="text-[12px] text-gray-300">
          No order reaches the broker. Positions here are recorded by the lab, priced from live quotes.
        </span>
      </div>

      <Section title="Desk" right={
        <div className="flex items-center gap-2">
          <button onClick={tick} disabled={busy} className="btn-secondary !py-1.5 !px-3 text-[12px] flex items-center gap-1.5">
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Activity className="w-3.5 h-3.5" />}Check now
          </button>
          <button onClick={load} className="btn-secondary !py-1.5 !px-2"><RefreshCw className="w-3.5 h-3.5" /></button>
        </div>}>
        <div className="grid gap-3 grid-cols-2 sm:grid-cols-4 lg:grid-cols-6">
          <Stat label="Paper trading" value={data?.enabled ? 'ON' : 'OFF'}
            tone={data?.enabled ? 'text-emerald-400' : 'text-gray-400'} />
          <Stat label="Market" value={data?.market_open ? 'open' : 'closed'} />
          <Stat label="Strategy" value={data?.strategy || '—'} sub={`${data?.side || ''} · ${data?.timeframe || ''}m`} />
          <Stat label="NIFTY" value={N(data?.spot)} />
          <Stat label="Today" value={`${st.trades || 0} trades`} sub={`limit ${data?.max_trades_per_day ?? '—'}`} />
          <Stat label="Realised" value={RS(st.realised)} tone={tone(st.realised)}
            sub={st.win_rate == null ? '' : `${PCT(st.win_rate, 0)} of ${st.closed}`} />
        </div>
        <div className="flex flex-wrap items-center gap-3 mt-3 pt-3 border-t border-surface-3">
          <label className="flex items-center gap-2 text-[12.5px] text-gray-300">
            <input type="checkbox" checked={!!data?.enabled} disabled={busy}
              onChange={(e) => save({ enabled: e.target.checked })} className="accent-brand-500" />
            run this strategy on live data (paper)
          </label>
          <label className="flex items-center gap-1.5 text-[12px] text-gray-400">
            max trades/day
            <input type="number" min={1} value={data?.max_trades_per_day ?? 3} disabled={busy}
              onChange={(e) => save({ max_trades_per_day: Number(e.target.value) })}
              className="input-field !py-1 !px-2 w-16 text-[12px]" />
          </label>
          <label className="flex items-center gap-1.5 text-[12px] text-gray-400">
            lots
            <input type="number" min={1} value={data?.lots ?? 1} disabled={busy}
              onChange={(e) => save({ lots: Number(e.target.value) })}
              className="input-field !py-1 !px-2 w-14 text-[12px]" />
          </label>
          <button onClick={() => save({ config: cfg })} disabled={busy}
            className="btn-secondary !py-1.5 !px-3 text-[12px]">Use the configuration from Setup</button>
        </div>
        {!data?.conditions?.length && (
          <Note tone="warn">No strategy is loaded into the paper desk yet — press “Use the
            configuration from Setup”, then switch it on.</Note>
        )}
        {!!data?.conditions?.length && (
          <div className="flex flex-wrap gap-1.5 mt-2">
            {data.conditions.map((c) => (
              <span key={c} className="px-1.5 py-0.5 rounded border border-surface-3 bg-surface-2/60 text-[11px] text-gray-400">{c}</span>
            ))}
          </div>
        )}
        {msg && <div className="text-[12px] text-gray-400 mt-2">{msg}</div>}
      </Section>

      <Section title={`Open position${open.length === 1 ? '' : 's'} · ${open.length}`}>
        {!open.length ? <Note>Nothing open. A signal on a settled bar opens one.</Note> : (
          <div className="grid gap-3 sm:grid-cols-2">
            {open.map((p) => (
              <div key={p.id} className="rounded-lg border border-brand-500/30 bg-brand-500/5 p-3">
                <div className="flex items-center justify-between">
                  <span className="text-[13px] font-semibold text-gray-100">{p.side} {p.contract}</span>
                  <span className={`text-[13px] font-bold mono ${tone(p.unrealised)}`}>{RS(p.unrealised)}</span>
                </div>
                <div className="grid grid-cols-3 gap-2 mt-2">
                  <Stat label="Entry" value={N(p.option_entry)} sub={p.entry_time} />
                  <Stat label="Now" value={N(p.ltp)} />
                  <Stat label="Target / stop" value={`${N(p.ladder?.target, 0)} / ${N(p.ladder?.stop, 0)}`} />
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section title="Today's paper trades">
        <StatTable rows={[]} empty="" />
        <div className="overflow-x-auto">
          <table className="w-full text-[12px] min-w-[760px]">
            <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
              {['Signal', 'Side', 'Contract', 'Entry', 'Exit', 'Reason', 'Index move', 'Net P&L', 'Status'].map((h) => (
                <th key={h} className="text-left px-2 py-1 font-medium">{h}</th>))}
            </tr></thead>
            <tbody>
              {(data?.today || []).map((t) => (
                <tr key={t.id} className="border-b border-surface-3/40">
                  <td className="px-2 py-1 mono text-gray-400">{t.time}</td>
                  <td className="px-2 py-1">{t.side}</td>
                  <td className="px-2 py-1 mono text-gray-300">{t.contract}</td>
                  <td className="px-2 py-1 mono text-gray-300">{N(t.option_entry)}</td>
                  <td className="px-2 py-1 mono text-gray-300">{N(t.option_exit)}</td>
                  <td className="px-2 py-1 text-gray-400">{t.exit_reason || '—'}</td>
                  <td className={`px-2 py-1 mono ${tone(t.spot_move_pts)}`}>{t.spot_move_pts == null ? '—' : `${N(t.spot_move_pts, 1)} pts`}</td>
                  <td className={`px-2 py-1 mono font-semibold ${tone(t.pnl)}`}>{RS(t.pnl)}</td>
                  <td className="px-2 py-1 text-[11px] text-gray-400">{t.status}</td>
                </tr>
              ))}
              {!(data?.today || []).length && (
                <tr><td colSpan={9} className="px-2 py-4 text-center text-gray-500 text-[12px]">nothing yet today</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </Section>

      <Section title="Live signal audit" right={<span className="text-[10.5px] text-gray-500">
        every decision the engine made, taken or not</span>}>
        <div className="overflow-x-auto max-h-[300px] overflow-y-auto">
          <table className="w-full text-[12px] min-w-[680px]">
            <thead className="sticky top-0 bg-surface-1"><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
              {['When', 'Bar', 'Spot', 'Fired', 'Acted', 'Why not', 'Conditions met'].map((h) => (
                <th key={h} className="text-left px-2 py-1 font-medium">{h}</th>))}
            </tr></thead>
            <tbody>
              {signals.map((s) => (
                <tr key={s.id} className="border-b border-surface-3/40">
                  <td className="px-2 py-1 mono text-gray-500">{s.at}</td>
                  <td className="px-2 py-1 mono text-gray-400">{s.bar}</td>
                  <td className="px-2 py-1 mono text-gray-300">{N(s.spot)}</td>
                  <td className={`px-2 py-1 ${s.fired ? 'text-emerald-400' : 'text-gray-600'}`}>{s.fired ? 'yes' : 'no'}</td>
                  <td className={`px-2 py-1 ${s.acted ? 'text-brand-300' : 'text-gray-600'}`}>{s.acted ? 'yes' : 'no'}</td>
                  <td className="px-2 py-1 text-[11px] text-gray-500">{s.skip_reason || ''}</td>
                  <td className="px-2 py-1 text-[11px] text-gray-400">
                    {(s.reasons || []).filter((r) => r.passed).length}/{(s.reasons || []).length}
                  </td>
                </tr>
              ))}
              {!signals.length && <tr><td colSpan={7} className="px-2 py-4 text-center text-gray-500 text-[12px]">no live signals logged yet</td></tr>}
            </tbody>
          </table>
        </div>
      </Section>

      <Section title="Paper against backtest" right={
        <button onClick={loadCompare} disabled={!runId} className="btn-secondary !py-1 !px-2 text-[11.5px] disabled:opacity-50">
          compare with the last stored run
        </button>}>
        {!compare ? <Note>Run a backtest, then compare it with what paper trading actually did.</Note>
          : compare.paper_trades === 0 ? <Note tone="warn">{compare.message}</Note> : (
            <>
              <div className="grid gap-4 sm:grid-cols-2">
                {[['Backtest', compare.backtest], ['Paper', compare.paper]].map(([label, b]) => (
                  <div key={label} className="rounded-lg border border-surface-3 p-3">
                    <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-1">{label}</div>
                    <div className="grid grid-cols-2 gap-2">
                      <Stat label="Trades" value={N0(b?.trades)} />
                      <Stat label="Win rate" value={PCT(b?.win_rate)} />
                      <Stat label="Net P&L" value={RS(b?.net_pnl)} tone={tone(b?.net_pnl)} />
                      <Stat label="Avg trade" value={RS(b?.avg_trade)} />
                      <Stat label="Avg index MFE" value={b?.avg_spot_mfe == null ? '—' : `${N(b.avg_spot_mfe, 1)} pts`} />
                      <Stat label="Avg hold" value={b?.avg_hold_min == null ? '—' : `${Math.round(b.avg_hold_min)} min`} />
                    </div>
                  </div>
                ))}
              </div>
              <Note tone="warn">{compare.note}</Note>
            </>
          )}
      </Section>
    </div>
  );
}
