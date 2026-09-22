import React, { useCallback, useEffect, useState } from 'react';
import { Loader2, RefreshCw, Power, ShieldCheck, Radio } from 'lucide-react';
import { api } from '../../api';
import {
  Section, Stat, Note, Legs, TradeTable, MonthTiles, N, N0, RS, PCT, tone, input,
} from './ui';

/**
 * Live PAPER trading of the failed-breakout iron fly. The background loop checks every 20 seconds
 * while this is switched on; this panel just shows what it is doing. No order is ever sent.
 */
export default function Paper() {
  const [d, setD] = useState(null);
  const [signals, setSignals] = useState([]);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [lots, setLots] = useState(1);

  const load = useCallback(async () => {
    try {
      const [r, s] = await Promise.all([api.flMePaper(), api.flPaperSignals(50)]);
      if (r.status !== 'ok') throw new Error(r.message || 'could not load paper desk');
      setD(r);
      setLots(r.lots || 1);
      setSignals(s.signals || []);
      setErr('');
    } catch (e) { setErr(String(e.message || e)); }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  const save = async (patch) => {
    setBusy(true);
    try {
      const r = await api.flPaperConfig(patch);
      if (r.status !== 'ok') throw new Error(r.message);
      await load();
    } catch (e) { setErr(String(e.message || e)); } finally { setBusy(false); }
  };

  const checkNow = async () => {
    setBusy(true);
    try {
      const r = await api.flPaperCheck();
      if (r.status !== 'ok') throw new Error(r.message);
      await load();
    } catch (e) { setErr(String(e.message || e)); } finally { setBusy(false); }
  };

  if (!d) {
    return <div className="py-10 text-center text-gray-500 text-sm">
      {err ? <span className="text-red-400">{err}</span> : <><Loader2 className="w-5 h-5 animate-spin mx-auto mb-2" />Loading the paper desk…</>}
    </div>;
  }

  const t = d.today || {};
  const pos = (d.open_positions || [])[0];
  const bt = d.backtest;
  const tot = d.totals || {};
  const expectMonth = bt?.summary?.avg_month != null && bt?.lots ? (bt.summary.avg_month / bt.lots) * (d.lots || 1) : null;
  const closed = (d.trades || []).filter((x) => x.status === 'CLOSED');
  const progress = pos && pos.unrealised != null && pos.target_rs && pos.stop_rs
    ? Math.max(0, Math.min(100, ((pos.unrealised - pos.stop_rs) / (pos.target_rs - pos.stop_rs)) * 100)) : null;

  return (
    <div className="space-y-4">
      <Section title="Paper trading" right={
        <div className="flex flex-wrap items-center gap-2">
          <span className={`flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full ${d.connected ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'}`}>
            <Radio className="w-3 h-3" />{d.connected ? 'Zerodha connected' : 'Zerodha not connected'}
          </span>
          <span className={`text-[11px] px-2 py-0.5 rounded-full ${d.market_open ? 'bg-emerald-500/15 text-emerald-400' : 'bg-surface-3 text-gray-400'}`}>
            {d.market_open ? 'market open' : 'market closed'}
          </span>
          <span className="text-[11px] text-gray-500 mono">{d.now}</span>
        </div>}>
        <div className="flex flex-wrap items-end gap-3">
          <button disabled={busy} onClick={() => save({ enabled: !d.enabled })}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12.5px] font-semibold ${d.enabled
              ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40' : 'bg-surface-2 text-gray-300 border border-surface-3'}`}>
            <Power className="w-4 h-4" />{d.enabled ? 'Paper trading ON' : 'Paper trading OFF'}
          </button>
          <div className="w-24">
            <label className="block text-[10px] uppercase tracking-wider text-gray-500">Lots</label>
            <input type="number" min={1} max={50} value={lots} onChange={(e) => setLots(e.target.value)}
              onBlur={() => Number(lots) !== d.lots && save({ lots: Number(lots) || 1 })} className={input} />
          </div>
          <button disabled={busy || !d.connected} onClick={checkNow}
            className="btn-secondary !py-1.5 !px-3 text-[12px] flex items-center gap-1.5">
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}Check now
          </button>
          <div className="flex items-center gap-1.5 text-[11.5px] text-gray-400 ml-auto">
            <ShieldCheck className="w-4 h-4 text-emerald-400" />Paper only — this lab has no order path; nothing is sent to Zerodha.
          </div>
        </div>
        {err && <div className="text-[12px] text-red-400 mt-2">{err}</div>}
      </Section>

      <div className="grid lg:grid-cols-2 gap-4">
        <Section title="Today">
          <div className="text-[13.5px] text-gray-100 font-medium mb-2">{t.state || '—'}</div>
          <div className="grid grid-cols-3 gap-3 mb-2">
            <Stat label="Range high" value={N(t.or_high)} />
            <Stat label="Range low" value={N(t.or_low)} />
            <Stat label="NIFTY" value={N(t.spot)} sub={t.last_minute ? `close of ${t.last_minute}` : null} />
          </div>
          {(t.events || []).length > 0 && (
            <div className="border-t border-surface-3 pt-2 space-y-0.5">
              {t.events.map((e, i) => (
                <div key={i} className="text-[11.5px] text-gray-300"><span className="mono text-gray-500 mr-2">{e.time}</span>{e.text}</div>
              ))}
            </div>
          )}
        </Section>

        <Section title="Open position">
          {!pos ? <Note>No open paper position.</Note> : (
            <div className="space-y-2">
              <div className="grid grid-cols-3 gap-3">
                <Stat label="Credit taken" value={`${N(pos.credit)} pts`} sub={`${pos.lots} lot · qty ${pos.qty}`} />
                <Stat label="Cost to close now" value={pos.debit_now != null ? `${N(pos.debit_now)} pts` : '—'} />
                <Stat label="Unrealised" value={RS(pos.unrealised)} tone={tone(pos.unrealised)} sub="before charges" />
              </div>
              {progress != null && (
                <div>
                  <div className="flex justify-between text-[10.5px] text-gray-500 mb-0.5">
                    <span>stop {RS(pos.stop_rs)}</span><span>target {RS(pos.target_rs)}</span>
                  </div>
                  <div className="h-2 rounded-full bg-surface-3 relative overflow-hidden">
                    <div className="absolute inset-y-0 left-1/2 w-px bg-white/30" />
                    <div className={`h-full ${pos.unrealised >= 0 ? 'bg-emerald-500' : 'bg-red-500'}`} style={{ width: `${progress}%` }} />
                  </div>
                </div>
              )}
              <Legs legs={pos.legs} live />
              <div className="text-[11px] text-gray-500">
                Opened {pos.entry_time} on the {pos.signal_time} signal · expiry {pos.expiry} · exits at 60% of credit captured,
                60% lost, or 15:15.
              </div>
            </div>
          )}
        </Section>
      </div>

      <Section title="Paper results so far">
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-3">
          <Stat label="Trades" value={N0(tot.trades)} />
          <Stat label="Win rate" value={tot.trades ? PCT((tot.wins / tot.trades) * 100, 0) : '—'} />
          <Stat label="Net P&L" value={RS(tot.net)} tone={tone(tot.net)} sub="after charges" />
          <Stat label="Green months" value={tot.months ? `${tot.green_months}/${tot.months}` : '—'} />
          <Stat label="Backtest expects" value={expectMonth != null ? `${RS(expectMonth)}/mo` : '—'}
            sub={bt ? `avg month · run #${bt.id} scaled to ${d.lots} lot` : 'run a backtest to compare'} />
        </div>
        <MonthTiles months={[...(d.monthly || [])].reverse()} />
        <div className="text-[11px] text-gray-500 mt-2">
          Paper fills use the live order book (sold at the bid, bought at the ask) and real charges, so they should
          come in a little below the backtest. A large gap after a few months means the edge is not holding live.
        </div>
      </Section>

      <Section title={`Paper trades · ${closed.length}`}>
        <TradeTable trades={d.trades || []} empty="No paper trades yet — they appear here as signals fire." />
      </Section>

      <Section title="Signal log">
        {!signals.length ? <Note>No signals recorded yet.</Note> : (
          <div className="overflow-x-auto">
            <table className="w-full text-[12px] min-w-[560px]">
              <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                {['Date', 'Signal', 'NIFTY', 'What happened', 'Outcome'].map((h) => <th key={h} className="text-left px-2 py-1 font-medium">{h}</th>)}
              </tr></thead>
              <tbody>
                {signals.map((s) => (
                  <tr key={s.id} className="border-b border-surface-3/40">
                    <td className="px-2 py-1 mono text-gray-300">{s.date}</td>
                    <td className="px-2 py-1 mono text-gray-400">{s.time}</td>
                    <td className="px-2 py-1 mono text-gray-300">{N(s.spot)}</td>
                    <td className="px-2 py-1 text-gray-300">{s.direction}</td>
                    <td className={`px-2 py-1 ${s.acted ? 'text-emerald-400' : 'text-amber-500'}`}>{s.acted ? 'paper fly opened' : s.skip_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}
