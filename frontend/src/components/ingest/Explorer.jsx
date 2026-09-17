import React, { useEffect, useState } from 'react';
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid } from 'recharts';
import { api } from '../../api';
import CandleChart from '../CandleChart';
import { usePalette, fmtQty, fmtNum, fmtInt, Empty } from '../oilab/ui';

/**
 * Look at what was uploaded: index candles, one option contract's candles with its OI, or the
 * whole stored chain at a chosen minute.
 */

const toCandles = (bars) => bars.map((b) => ({ t: String(b.timestamp).slice(5, 16).replace('T', ' '), open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume }));

function Controls({ children }) {
  return <div className="flex flex-wrap items-end gap-2">{children}</div>;
}

function Field({ label, children }) {
  return <label className="block"><span className="block text-[10.5px] uppercase tracking-wider text-gray-500 mb-0.5">{label}</span>{children}</label>;
}

export default function Explorer({ coverage }) {
  const pal = usePalette();
  const unds = (coverage?.underlyings || []).map((u) => u);
  const [und, setUnd] = useState((unds.find((u) => u.underlying === 'NIFTY') || unds[0])?.underlying || 'NIFTY');
  const cov = (coverage?.series || []).filter((s) => s.underlying === und);
  const lastDay = cov.map((s) => s.last_day).sort().slice(-1)[0] || '';
  const [view, setView] = useState('spot');
  const [day, setDay] = useState(lastDay);
  const [minutes, setMinutes] = useState(5);
  const [at, setAt] = useState('11:00');
  const [data, setData] = useState(null);
  const [contracts, setContracts] = useState(null);
  const [expiry, setExpiry] = useState('');
  const [contract, setContract] = useState('');
  const [err, setErr] = useState('');

  useEffect(() => { setDay(lastDay); }, [und, lastDay]);
  const hasKind = (k) => cov.some((s) => s.kind === k);

  useEffect(() => {
    if (view !== 'option' || !day) return;
    api.diExploreContracts({ underlying: und, day }).then((r) => {
      setContracts(r);
      const e = r.expiries?.[0];
      setExpiry(e?.expiry || '');
      const mid = e?.contracts?.[Math.floor((e?.contracts?.length || 0) / 2)];
      setContract(mid?.contract || '');
    }).catch((e) => setErr(String(e.message || e)));
  }, [view, und, day]);

  const load = async () => {
    setErr(''); setData(null);
    try {
      let r;
      if (view === 'spot') r = await api.diExploreSpot({ underlying: und, start: day, minutes });
      else if (view === 'option') r = await api.diExploreOption({ underlying: und, contract, start: day, minutes });
      else r = await api.diExploreChain({ underlying: und, day, at, ...(expiry ? { expiry } : {}) });
      if (r.status !== 'ok') setErr(r.message); else setData({ view, ...r });
    } catch (e) { setErr(String(e.message || e)); }
  };
  useEffect(() => { if (day && (view !== 'option' || contract)) load(); }, [view, und, day, minutes, contract]);   // eslint-disable-line react-hooks/exhaustive-deps

  if (!unds.length) return <Empty>Nothing stored yet — upload files first.</Empty>;
  const exp = contracts?.expiries?.find((e) => e.expiry === expiry);
  const maxOi = data?.view === 'chain' ? Math.max(1, ...data.rows.flatMap((r) => [r.ce?.oi || 0, r.pe?.oi || 0])) : 1;

  return (
    <div className="space-y-4">
      <div className="card !p-3">
        <Controls>
          <Field label="Underlying">
            <select value={und} onChange={(e) => setUnd(e.target.value)} className="input-field !py-1.5">{unds.map((u) => <option key={u.underlying}>{u.underlying}</option>)}</select>
          </Field>
          <Field label="View">
            <div className="flex gap-1 p-0.5 rounded-lg bg-surface-2 border border-surface-3">
              {[['spot', 'Index candles', hasKind('spot')], ['option', 'Option contract', hasKind('options')], ['chain', 'Chain at a minute', hasKind('options')]].map(([v, label, ok]) => (
                <button key={v} disabled={!ok} onClick={() => setView(v)} className={`px-2.5 py-1 rounded-md text-[12px] disabled:opacity-40 ${view === v ? 'bg-surface-3 text-white' : 'text-gray-400'}`}>{label}</button>
              ))}
            </div>
          </Field>
          <Field label="Day"><input type="date" value={day} onChange={(e) => setDay(e.target.value)} className="input-field !py-1.5" /></Field>
          {view !== 'chain' && (
            <Field label="Candle">
              <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} className="input-field !py-1.5">{[1, 5, 15].map((m) => <option key={m} value={m}>{m} min</option>)}</select>
            </Field>
          )}
          {view !== 'spot' && contracts?.expiries?.length > 0 && (
            <Field label="Expiry">
              <select value={expiry} onChange={(e) => setExpiry(e.target.value)} className="input-field !py-1.5">{contracts.expiries.map((e) => <option key={e.expiry}>{e.expiry}</option>)}</select>
            </Field>
          )}
          {view === 'option' && exp && (
            <Field label="Contract">
              <select value={contract} onChange={(e) => setContract(e.target.value)} className="input-field !py-1.5">
                {exp.contracts.map((c) => <option key={c.contract} value={c.contract}>{fmtInt(c.strike)} {c.option_type} · {c.bars} bars</option>)}
              </select>
            </Field>
          )}
          {view === 'chain' && (
            <>
              <Field label="At"><input type="time" value={at} min="09:15" max="15:29" onChange={(e) => setAt(e.target.value)} className="input-field !py-1.5" /></Field>
              <button onClick={load} className="btn-primary !py-1.5 text-[12.5px]">Show chain</button>
            </>
          )}
        </Controls>
        {cov.length > 0 && <div className="text-[11px] text-gray-500 mt-2">{cov.map((s) => `${s.kind}: ${s.first_day} → ${s.last_day}`).join(' · ')}</div>}
      </div>
      {err && <div className="text-[12px] text-red-400">{err}</div>}
      {!data ? <Empty>Pick a day to load.</Empty> : data.view === 'chain' ? (
        !data.rows.length ? <Empty>No option bars stored for that day and time.</Empty> : (
          <div className="card !p-4 space-y-2">
            <div className="text-[12.5px] text-gray-400">{und} {data.expiry} expiry at {data.at} on {data.day} · spot {fmtNum(data.spot, 1)} · {data.rows.length} strikes stored</div>
            <div className="grid grid-cols-[1fr_auto_1fr] text-[10px] uppercase tracking-wider text-gray-500"><span>← PE OI</span><span className="w-24 text-center">Strike</span><span className="text-right">CE OI →</span></div>
            {[...data.rows].reverse().map((r) => (
              <div key={r.strike} className="grid grid-cols-[1fr_auto_1fr] items-center gap-2" title={`CE ${fmtNum(r.ce?.close)} · OI ${fmtQty(r.ce?.oi)} | PE ${fmtNum(r.pe?.close)} · OI ${fmtQty(r.pe?.oi)}`}>
                <div className="relative h-4"><div className="absolute right-0 inset-y-0.5 rounded" style={{ width: `${((r.pe?.oi || 0) / maxOi) * 100}%`, background: pal.put }} /></div>
                <div className="w-24 text-center mono text-[11.5px] text-gray-200">{fmtInt(r.strike)}</div>
                <div className="relative h-4"><div className="absolute left-0 inset-y-0.5 rounded" style={{ width: `${((r.ce?.oi || 0) / maxOi) * 100}%`, background: pal.call }} /></div>
              </div>
            ))}
          </div>
        )
      ) : !data.bars?.length ? <Empty>No bars stored for that selection.</Empty> : (
        <div className="card !p-4 space-y-3">
          <div className="text-[12.5px] text-gray-400">{data.view === 'option' ? data.contract : `${und} index`} · {data.bars.length} {data.minutes}-min candles · {data.start}</div>
          <CandleChart candles={toCandles(data.bars)} height={320} />
          {data.view === 'option' && (
            <div className="h-36">
              <div className="text-[11px] text-gray-500">Open interest</div>
              <ResponsiveContainer>
                <LineChart data={data.bars.map((b) => ({ t: String(b.timestamp).slice(11, 16), oi: b.oi }))} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke={pal.grid} vertical={false} />
                  <XAxis dataKey="t" stroke={pal.axis} fontSize={10} tickLine={false} axisLine={false} minTickGap={40} />
                  <YAxis stroke={pal.axis} fontSize={10} tickLine={false} axisLine={false} width={50} tickFormatter={fmtQty} domain={['auto', 'auto']} />
                  <Tooltip contentStyle={{ background: 'rgb(var(--surface-1))', border: '1px solid rgb(var(--surface-3))', borderRadius: 8, fontSize: 11 }} formatter={(v) => [fmtQty(v), 'OI']} />
                  <Line type="stepAfter" dataKey="oi" stroke={String(data.contract).endsWith('CE') ? pal.call : pal.put} strokeWidth={2} dot={false} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
