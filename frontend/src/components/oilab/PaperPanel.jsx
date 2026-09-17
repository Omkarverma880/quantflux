import React, { useCallback, useEffect, useState } from 'react';
import { Wallet, Settings2, X, History, AlertTriangle, Save } from 'lucide-react';
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine, Cell } from 'recharts';
import { api } from '../../api';
import { usePalette, fmtNum, fmtInt, fmtRupee, isNum, Section, Stat, Empty } from './ui';

/**
 * Paper Trades — the OI Lab signal desk traded on paper with live prices.
 * The engine runs server-side while you're logged in to Zerodha, with or without this page open.
 * PAPER ONLY: nothing here can place an order.
 */

const REASON = {
  TARGET: 'text-green-400', STOP: 'text-red-400', BREAKEVEN: 'text-gray-300', TIME: 'text-gray-300',
  SQUAREOFF: 'text-gray-300', MANUAL: 'text-gray-300', MISSED_SQUAREOFF: 'text-amber-400',
};

function Toggle({ on, onChange, label }) {
  return (
    <button type="button" role="switch" aria-checked={on} onClick={() => onChange(!on)} className="flex items-center gap-2">
      <span className={`relative w-10 h-5 rounded-full transition ${on ? 'bg-green-500' : 'bg-surface-3'}`}>
        <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition ${on ? 'left-5' : 'left-0.5'}`} />
      </span>
      <span className="text-[12.5px] text-gray-200">{label}</span>
    </button>
  );
}

function Config({ onSaved }) {
  const [meta, setMeta] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [msg, setMsg] = useState('');
  useEffect(() => { api.oiLabPaperConfig().then((r) => { if (r.status === 'ok') { setMeta(r); setCfg(r.config); } }).catch(() => {}); }, []);
  if (!cfg) return null;
  const set = (k, v) => { setCfg((c) => ({ ...c, [k]: v })); setMsg(''); };
  const toggleIn = (k, v) => set(k, cfg[k].includes(v) ? cfg[k].filter((x) => x !== v) : [...cfg[k], v]);
  const save = async (next = cfg) => {
    const r = await api.oiLabPaperConfigSave(next);
    if (r.status === 'ok') { setCfg(r.config); setMsg('Saved'); onSaved?.(); } else setMsg(r.message);
  };
  return (
    <Section title={<span className="flex items-center gap-1.5"><Settings2 className="w-4 h-4 text-brand-400" />Auto paper trading</span>}
      right={<span className="badge-yellow !text-[10px]">paper only — never places orders</span>}>
      <div className="grid gap-4 lg:grid-cols-[auto_1fr]">
        <div className="space-y-3">
          <Toggle on={cfg.auto} label={cfg.auto ? 'ON — taking fresh signals' : 'OFF — manual paper trades only'}
            onChange={(v) => { const next = { ...cfg, auto: v }; setCfg(next); save(next); }} />
          <div className="text-[11.5px] text-gray-500 max-w-xs">
            Runs on the server while you're logged in to Zerodha, even with this page closed. Signals older than {cfg.signal_max_age_s}s are never chased.
          </div>
        </div>
        <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-3 text-[12px]">
          <div>
            <div className="text-[10.5px] uppercase tracking-wider text-gray-500 mb-1">Indices</div>
            {meta.indices.map((i) => (
              <label key={i} className="flex items-center gap-2 text-gray-200"><input type="checkbox" checked={cfg.indices.includes(i)} onChange={() => toggleIn('indices', i)} />{i}</label>
            ))}
          </div>
          <div>
            <div className="text-[10.5px] uppercase tracking-wider text-gray-500 mb-1">Mode</div>
            {meta.modes.map((m) => (
              <label key={m} className="flex items-center gap-2 text-gray-200"><input type="radio" name="paper-mode" checked={cfg.mode === m} onChange={() => set('mode', m)} />{m === 'SWING' ? 'Swing (spot Gann target, alerts)' : 'Scalp (premium Gann grid)'}</label>
            ))}
          </div>
          <div className="space-y-2">
            <label className="block"><span className="text-[10.5px] uppercase tracking-wider text-gray-500">Lots per trade</span>
              <input type="number" min={1} max={50} value={cfg.lots} onChange={(e) => set('lots', Number(e.target.value) || 1)} className="input-field !py-1 w-full mt-0.5" /></label>
            <label className="block"><span className="text-[10.5px] uppercase tracking-wider text-gray-500">Max trades / day per index</span>
              <input type="number" min={1} max={20} value={cfg.max_trades_per_day} onChange={(e) => set('max_trades_per_day', Number(e.target.value) || 1)} className="input-field !py-1 w-full mt-0.5" /></label>
          </div>
          <div>
            <div className="text-[10.5px] uppercase tracking-wider text-gray-500 mb-1">Setups</div>
            {Object.entries(meta.setups).map(([k, v]) => (
              <label key={k} className="flex items-center gap-2 text-gray-200"><input type="checkbox" checked={cfg.setups.includes(k)} onChange={() => toggleIn('setups', k)} />{v.label} <span className="text-gray-500">{v.side}</span></label>
            ))}
            <label className="flex items-center gap-2 text-gray-300 mt-1.5"><input type="checkbox" checked={cfg.skip_losing_setups} onChange={(e) => set('skip_losing_setups', e.target.checked)} />skip setups that lost on unseen data</label>
          </div>
        </div>
      </div>
      <div className="flex items-center gap-3 mt-3">
        <button onClick={() => save()} className="btn-primary !py-1.5 text-[12.5px] flex items-center gap-1.5"><Save className="w-3.5 h-3.5" />Save settings</button>
        {msg && <span className="text-[12px] text-gray-400">{msg}</span>}
      </div>
    </Section>
  );
}

function OpenPositions({ rows, onClose, busyId }) {
  if (!rows.length) return <Empty>No open paper positions.</Empty>;
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {rows.map((p) => {
        const pnl = isNum(p.ltp) && isNum(p.entry_price) ? (p.ltp - p.entry_price) * p.qty : null;
        return (
          <div key={p.id} className="rounded-xl border border-surface-3 bg-surface-2 px-3.5 py-3 space-y-2">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="text-[13.5px] font-semibold text-gray-100">{p.symbol} <span className="text-gray-500 font-normal">× {p.qty}</span></div>
                <div className="text-[11.5px] text-gray-400">{p.label} · {p.mode} · {p.source} · in {p.entry_time} @ {fmtNum(p.entry_price)}</div>
              </div>
              <div className="text-right">
                <div className={`mono text-[17px] font-bold ${pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>{fmtRupee(pnl)}</div>
                <div className="mono text-[11.5px] text-gray-400">LTP {fmtNum(p.ltp)}</div>
              </div>
            </div>
            <div className="grid grid-cols-3 gap-2 text-[11.5px]">
              <div><div className="text-gray-500">Stop</div><div className="mono text-red-400">{fmtNum(p.premium_stop)}</div></div>
              <div><div className="text-gray-500">{p.mode === 'SCALP' ? 'Premium target' : 'Spot target'}</div>
                <div className="mono text-green-400">{p.mode === 'SCALP' ? fmtNum(p.premium_target) : fmtNum(p.spot_target, 1)}</div></div>
              <div><div className="text-gray-500">Spot now</div><div className="mono text-gray-200">{fmtNum(p.spot_ltp, 1)}</div></div>
            </div>
            {p.alerts?.length > 0 && (
              <div className="space-y-0.5">
                {p.alerts.slice(-3).map((a, i) => (
                  <div key={i} className="text-[11px] text-amber-500 flex gap-1.5"><AlertTriangle className="w-3.5 h-3.5 shrink-0" />{a.time} {a.text}</div>
                ))}
              </div>
            )}
            <button onClick={() => onClose(p.id)} disabled={busyId === p.id} className="btn-secondary !py-1 !px-3 text-[12px] flex items-center gap-1.5 disabled:opacity-50">
              <X className="w-3.5 h-3.5" />Close at market (paper)
            </button>
          </div>
        );
      })}
    </div>
  );
}

export default function PaperPanel({ active = true }) {
  const pal = usePalette();
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [err, setErr] = useState('');
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(async () => {
    try {
      const r = await api.oiLabPaperPositions(days);
      if (r.status === 'ok') { setData(r); setErr(''); } else setErr(r.message);
    } catch (e) { setErr(String(e.message || e)); }
  }, [days]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!active) return undefined;
    const t = setInterval(() => { if (!document.hidden) load(); }, data?.open?.length ? 5000 : 30000);
    return () => clearInterval(t);
  }, [active, data, load]);

  const close = async (id) => {
    setBusyId(id);
    try { const r = await api.oiLabPaperClose(id); if (r.status !== 'ok') setErr(r.message); await load(); } finally { setBusyId(null); }
  };

  const S = data?.stats || {};
  const closed = (data?.positions || []).filter((p) => p.status === 'CLOSED');
  return (
    <div className="space-y-4">
      <Config onSaved={load} />
      {err && <div className="text-[12px] text-red-400">{err}</div>}
      <Section title={<span className="flex items-center gap-1.5"><Wallet className="w-4 h-4 text-brand-400" />Open paper positions</span>}>
        {data ? <OpenPositions rows={data.open} onClose={close} busyId={busyId} /> : <Empty>Loading…</Empty>}
      </Section>
      <div className="flex items-center justify-between">
        <div className="text-[13px] font-semibold text-gray-100">Journal</div>
        <div className="flex gap-1">
          {[7, 30, 90].map((d) => (
            <button key={d} onClick={() => setDays(d)} className={`px-2.5 py-1 rounded-md text-[12px] ${days === d ? 'bg-surface-3 text-white' : 'text-gray-400 hover:text-gray-200'}`}>{d}d</button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-2">
        <Stat label="Closed trades" value={fmtInt(S.trades || 0)} />
        <Stat label="Win rate" value={isNum(S.win_rate) ? `${fmtNum(S.win_rate, 0)}%` : '—'} />
        <Stat label="Net P&L" value={fmtRupee(S.total_pnl)} valueClass={(S.total_pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400'} sub="after charges" />
        <Stat label="Avg / trade" value={fmtRupee(S.avg_pnl)} valueClass={(S.avg_pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400'} />
        <Stat label="Profit factor" value={S.profit_factor ?? '—'} />
        <Stat label="Best / worst" value={<span><span className={(S.best || 0) >= 0 ? 'text-green-400' : 'text-red-400'}>{fmtRupee(S.best)}</span> <span className="text-gray-600">/</span> <span className={(S.worst || 0) >= 0 ? 'text-green-400' : 'text-red-400'}>{fmtRupee(S.worst)}</span></span>} />
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Section title="Daily paper P&L">
          {data?.daily?.length ? (
            <div className="h-48">
              <ResponsiveContainer>
                <BarChart data={data.daily} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke={pal.grid} vertical={false} />
                  <XAxis dataKey="date" stroke={pal.axis} fontSize={10} tickLine={false} axisLine={false} minTickGap={30} />
                  <YAxis stroke={pal.axis} fontSize={10} tickLine={false} axisLine={false} width={56} tickFormatter={(v) => fmtRupee(v)}
                    domain={[(lo) => Math.min(0, lo), (hi) => Math.max(0, hi)]} />
                  <Tooltip contentStyle={{ background: 'rgb(var(--surface-1))', border: '1px solid rgb(var(--surface-3))', borderRadius: 8, fontSize: 11 }}
                    formatter={(v, n, p) => [`${fmtRupee(v)} (${p.payload.trades} trades)`, 'P&L']} cursor={{ fill: 'rgba(127,127,127,0.08)' }} />
                  <ReferenceLine y={0} stroke={pal.axis} />
                  <Bar dataKey="pnl" radius={[4, 4, 0, 0]} maxBarSize={28} isAnimationActive={false}>
                    {data.daily.map((d) => <Cell key={d.date} fill={d.pnl >= 0 ? '#22c55e' : '#ef4444'} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : <Empty>No closed paper trades in this range yet.</Empty>}
        </Section>
        <Section title="By setup">
          {data?.by_setup?.length ? (
            <table className="w-full text-[12px]">
              <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                <th className="text-left px-2 py-1 font-medium">Setup</th><th className="text-left px-2 py-1 font-medium">Mode</th>
                <th className="text-right px-2 py-1 font-medium">Trades</th><th className="text-right px-2 py-1 font-medium">Win</th><th className="text-right px-2 py-1 font-medium">Net</th>
              </tr></thead>
              <tbody>{data.by_setup.map((r) => (
                <tr key={`${r.underlying}${r.setup}${r.mode}`} className="border-b border-surface-3/40">
                  <td className="px-2 py-1.5 text-gray-200">{r.underlying} · {r.label}</td><td className="px-2 py-1.5 text-gray-400">{r.mode}</td>
                  <td className="px-2 py-1.5 text-right mono">{r.trades}</td><td className="px-2 py-1.5 text-right mono">{fmtNum(r.win_rate, 0)}%</td>
                  <td className={`px-2 py-1.5 text-right mono ${r.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>{fmtRupee(r.total_pnl)}</td>
                </tr>
              ))}</tbody>
            </table>
          ) : <Empty>Per-setup results appear after the first closed trade.</Empty>}
        </Section>
      </div>
      <Section title={<span className="flex items-center gap-1.5"><History className="w-4 h-4 text-brand-400" />Closed paper trades</span>}>
        {closed.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[860px] text-[12px] whitespace-nowrap">
              <thead><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                {['Date', 'Contract', 'Setup · mode', 'In', 'Out', 'Exit reason', 'Points', 'Charges', 'Net P&L'].map((h) => (
                  <th key={h} className={`px-2 py-1 font-medium ${['Points', 'Charges', 'Net P&L'].includes(h) ? 'text-right' : 'text-left'}`}>{h}</th>
                ))}
              </tr></thead>
              <tbody>{closed.map((p) => (
                <tr key={p.id} className="border-b border-surface-3/40">
                  <td className="px-2 py-1.5 mono text-gray-400">{p.trade_date}</td>
                  <td className="px-2 py-1.5 text-gray-200">{p.symbol} × {p.qty}</td>
                  <td className="px-2 py-1.5 text-gray-400">{p.label} · {p.mode} · {p.source}</td>
                  <td className="px-2 py-1.5 mono">{p.entry_time} @ {fmtNum(p.entry_price)}</td>
                  <td className="px-2 py-1.5 mono">{p.exit_time} @ {fmtNum(p.exit_price)}</td>
                  <td className={`px-2 py-1.5 ${REASON[p.exit_reason] || (p.exit_reason?.startsWith('ALERT') ? 'text-amber-500' : 'text-gray-300')}`} title={(p.alerts || []).map((a) => `${a.time} ${a.text}`).join('\n')}>{p.exit_reason}</td>
                  <td className={`px-2 py-1.5 text-right mono ${p.pnl_points >= 0 ? 'text-green-400' : 'text-red-400'}`}>{fmtNum(p.pnl_points)}</td>
                  <td className="px-2 py-1.5 text-right mono text-gray-500">{fmtRupee(p.charges)}</td>
                  <td className={`px-2 py-1.5 text-right mono ${p.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>{fmtRupee(p.pnl)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : <Empty>Closed trades will be listed here.</Empty>}
      </Section>
    </div>
  );
}
