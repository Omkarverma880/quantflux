import React, { useEffect, useState } from 'react';
import { X, Scale, Gauge, Layers, LineChart as LineIcon } from 'lucide-react';
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid } from 'recharts';
import { api } from '../../api';
import { usePalette, fmtQty, fmtSignedQty, fmtNum, fmtInt, fmtSignedPct, fmtRupee, tone, isNum, Tip, Empty } from './ui';

/**
 * One strike, dissected: order book, buyer-vs-seller fight, greeks, OI story and
 * the intraday premium + OI path for the call and the put.
 */

function Kv({ k, v, cls = '', tip }) {
  return (
    <div className="flex items-center justify-between gap-2 py-1 border-b border-surface-3/40 last:border-0">
      <span className="text-[11.5px] text-gray-500 flex items-center gap-1">{k}{tip && <Tip text={tip} />}</span>
      <span className={`mono text-[12px] ${cls || 'text-gray-200'}`}>{v}</span>
    </div>
  );
}

function DepthLadder({ c, color }) {
  const buy = c.depth?.buy || [];
  const sell = c.depth?.sell || [];
  const max = Math.max(1, ...buy.map((d) => d.qty || 0), ...sell.map((d) => d.qty || 0));
  const tb = c.buy_qty || 0, ts = c.sell_qty || 0;
  const bidShare = tb + ts ? (tb / (tb + ts)) * 100 : 50;
  return (
    <div className="space-y-3">
      <div>
        <div className="flex justify-between text-[11px] mb-1">
          <span className="text-green-400">Buyers {fmtQty(tb)}</span>
          <span className="text-gray-500">pending quantity</span>
          <span className="text-red-400">Sellers {fmtQty(ts)}</span>
        </div>
        <div className="flex h-2.5 rounded-full overflow-hidden bg-surface-3">
          <div className="bg-green-500/80" style={{ width: `${bidShare}%` }} />
          <div className="bg-red-500/80 flex-1" />
        </div>
        <div className="text-[11px] text-gray-400 mt-1">
          {bidShare >= 55 ? 'Buyers are stacking bids — demand for this contract.' : bidShare <= 45 ? 'Sellers are stacking offers — supply overhead.' : 'Order book balanced.'}
          <span className="text-gray-600"> Pending orders can be pulled; treat as a hint, not proof.</span>
        </div>
      </div>
      <table className="w-full text-[11.5px] mono">
        <thead><tr className="text-[10px] text-gray-500">
          <th className="text-left font-medium">Orders</th><th className="text-right font-medium">Bid qty</th><th className="text-right font-medium pr-2">Bid</th>
          <th className="text-left font-medium pl-2">Ask</th><th className="text-left font-medium">Ask qty</th><th className="text-right font-medium">Orders</th>
        </tr></thead>
        <tbody>
          {Array.from({ length: 5 }).map((_, i) => {
            const b = buy[i] || {}, s = sell[i] || {};
            return (
              <tr key={i}>
                <td className="text-gray-500">{b.orders ?? ''}</td>
                <td className="relative text-right text-gray-300">
                  <div className="absolute inset-y-0.5 right-0 bg-green-500/15 rounded-l" style={{ width: `${((b.qty || 0) / max) * 100}%` }} />
                  <span className="relative">{b.qty ? fmtInt(b.qty) : ''}</span>
                </td>
                <td className="text-right pr-2 text-green-400">{b.price ? fmtNum(b.price) : ''}</td>
                <td className="pl-2 text-red-400">{s.price ? fmtNum(s.price) : ''}</td>
                <td className="relative text-gray-300">
                  <div className="absolute inset-y-0.5 left-0 bg-red-500/15 rounded-r" style={{ width: `${((s.qty || 0) / max) * 100}%` }} />
                  <span className="relative">{s.qty ? fmtInt(s.qty) : ''}</span>
                </td>
                <td className="text-right text-gray-500">{s.orders ?? ''}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="text-[11px] text-gray-400">
        Spread <span className="mono text-gray-200">{fmtNum(c.spread)}</span>
        {isNum(c.spread_pct) && <span className={c.spread_pct > 2 ? 'text-amber-400' : ''}> ({fmtNum(c.spread_pct, 2)}% of premium)</span>}
        {' · '}last {fmtInt(c.last_qty)} @ {c.last_trade_time ? String(c.last_trade_time).slice(11, 19) : '—'}
      </div>
    </div>
  );
}

function Series({ token, color, prevOi }) {
  const pal = usePalette();
  const [data, setData] = useState(null);
  useEffect(() => {
    let live = true;
    const load = () => api.oiLabContractSeries(token).then((r) => { if (live) setData(r); }).catch(() => {});
    load();
    const t = setInterval(load, 30000);
    return () => { live = false; clearInterval(t); };
  }, [token]);
  if (!data) return <Empty>Loading intraday path…</Empty>;
  if (data.status !== 'ok' || !data.bars?.length) return <Empty>Intraday OI path is still loading from Zerodha (rate-limited). It fills in within a minute.</Empty>;
  const bars = data.bars;
  const axis = { stroke: pal.axis, fontSize: 10, tickLine: false, axisLine: false };
  const tip = { contentStyle: { background: 'rgb(var(--surface-1))', border: '1px solid rgb(var(--surface-3))', borderRadius: 8, fontSize: 11 }, labelStyle: { color: pal.axis } };
  return (
    <div className="space-y-2">
      <div className="text-[11px] text-gray-500">Premium (5-min close)</div>
      <div className="h-28">
        <ResponsiveContainer>
          <LineChart data={bars} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={pal.grid} vertical={false} />
            <XAxis dataKey="time" {...axis} minTickGap={40} />
            <YAxis {...axis} width={44} domain={['auto', 'auto']} />
            <Tooltip {...tip} formatter={(v) => [fmtNum(v), 'Premium']} />
            <Line type="monotone" dataKey="close" stroke={color} strokeWidth={2} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="text-[11px] text-gray-500">Open interest{isNum(prevOi) ? ` (previous close ${fmtQty(prevOi)})` : ''}</div>
      <div className="h-28">
        <ResponsiveContainer>
          <LineChart data={bars} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid stroke={pal.grid} vertical={false} />
            <XAxis dataKey="time" {...axis} minTickGap={40} />
            <YAxis {...axis} width={44} tickFormatter={fmtQty} domain={['auto', 'auto']} />
            <Tooltip {...tip} formatter={(v) => [fmtQty(v), 'OI']} />
            <Line type="stepAfter" dataKey="oi" stroke={color} strokeWidth={2} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function ContractView({ c, side, snap, battle }) {
  const pal = usePalette();
  const color = side === 'ce' ? pal.call : pal.put;
  const lot = c.lot_size || snap.lot_size || 1;
  const conv = battle?.[`${side}_conviction`];
  const writerWins = isNum(c.writer_pnl_pct) ? c.writer_pnl_pct > 0 : null;
  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between">
        <div>
          <div className="text-[11px] text-gray-500">{c.symbol}</div>
          <div className="mono text-2xl font-bold text-white">{fmtNum(c.ltp)}</div>
          <div className={`text-[12px] ${tone(c.change)}`}>{isNum(c.change) ? `${c.change > 0 ? '+' : ''}${fmtNum(c.change)} (${fmtSignedPct(c.change_pct)})` : '—'}</div>
        </div>
        <div className="text-right text-[11.5px] text-gray-400">
          <div>Lot {lot} · ₹{fmtInt((c.ltp || 0) * lot)} per lot</div>
          <div>VWAP {fmtNum(c.vwap)} · Vol {fmtQty(c.volume)}</div>
        </div>
      </div>

      <div className="rounded-lg border border-surface-3 bg-surface-2 px-3 py-2 text-[12px] text-gray-300 space-y-1">
        <div className="font-semibold text-gray-100 flex items-center gap-1.5"><Scale className="w-4 h-4" style={{ color }} />Who is winning this contract</div>
        {c.buildup && <div><span className="font-medium">{c.buildup}</span> — {{
          'Short Buildup': 'price down while OI rises: fresh WRITING. Sellers are in charge here.',
          'Long Buildup': 'price up while OI rises: fresh BUYING. Buyers are in charge here.',
          'Short Covering': 'price up while OI falls: writers are exiting at a loss.',
          'Long Unwinding': 'price down while OI falls: buyers are exiting.',
        }[c.buildup]}</div>}
        {writerWins !== null && (
          <div>Everyone who {side === 'ce' ? 'wrote this call' : 'wrote this put'} today at the average price is
            <span className={writerWins ? ' text-green-400' : ' text-red-400'}> {writerWins ? 'in profit' : 'under water'} ({c.writer_pnl_pct > 0 ? '+' : ''}{fmtNum(c.writer_pnl_pct, 1)}%)</span>;
            today's buyers are the mirror image.</div>
        )}
        {conv && <div>Writer conviction <span className="mono text-white">{conv.score}</span>/100{isNum(conv.off_peak_pct) && conv.off_peak_pct > 5 ? ` · OI ${fmtNum(conv.off_peak_pct, 0)}% below today's peak (covering)` : ''}</div>}
      </div>

      <div>
        <div className="text-[12px] font-semibold text-gray-100 mb-2 flex items-center gap-1.5"><Layers className="w-4 h-4 text-brand-400" />Market depth — buyer vs seller fight</div>
        <DepthLadder c={c} color={color} />
      </div>

      <div className="grid grid-cols-2 gap-x-4">
        <div>
          <div className="text-[12px] font-semibold text-gray-100 mb-1 flex items-center gap-1.5"><Gauge className="w-4 h-4 text-brand-400" />Greeks</div>
          <Kv k="IV" v={isNum(c.iv) ? `${fmtNum(c.iv, 2)}%` : '—'} tip="Implied volatility backed out of the live premium." />
          <Kv k="Delta" v={fmtNum(c.delta, 3)} tip="Premium change for a 1-point spot move." />
          <Kv k="Gamma" v={fmtNum(c.gamma, 5)} tip="How fast delta changes. Highest at the money near expiry." />
          <Kv k="Theta / day" v={fmtNum(c.theta, 2)} cls="text-red-400" tip="Premium lost per day from time alone." />
          <Kv k="Theta / lot" v={isNum(c.theta) ? fmtRupee(c.theta * lot) : '—'} cls="text-red-400" />
          <Kv k="Vega" v={fmtNum(c.vega, 2)} tip="Premium change for a 1% move in IV." />
          <Kv k="Intrinsic · time" v={`${fmtNum(c.intrinsic)} · ${fmtNum(c.time_value)}`} />
        </div>
        <div>
          <div className="text-[12px] font-semibold text-gray-100 mb-1">Open interest</div>
          <Kv k="OI" v={fmtQty(c.oi)} />
          <Kv k="vs prev close" v={`${fmtSignedQty(c.oi_chg)}${isNum(c.oi_chg_pct) ? ` (${c.oi_chg_pct > 0 ? '+' : ''}${c.oi_chg_pct}%)` : ''}`} cls={tone(c.oi_chg)} />
          <Kv k="since 09:20" v={fmtSignedQty(c.oi_since_open)} cls={tone(c.oi_since_open)} />
          <Kv k="Day high / low" v={`${fmtQty(c.oi_day_high)} / ${fmtQty(c.oi_day_low)}`} />
          <Kv k="OHLC" v={`${fmtNum(c.open, 1)} ${fmtNum(c.high, 1)} ${fmtNum(c.low, 1)}`} />
          <Kv k="Prev close" v={fmtNum(c.prev_close)} />
        </div>
      </div>

      <div>
        <div className="text-[12px] font-semibold text-gray-100 mb-1 flex items-center gap-1.5"><LineIcon className="w-4 h-4 text-brand-400" />Today's path</div>
        <Series token={c.token} color={color} prevOi={c.prev_oi} />
      </div>
    </div>
  );
}

export default function ContractDrawer({ row, snap, onClose }) {
  const pal = usePalette();
  const [side, setSide] = useState(row?.strike >= snap?.spot ? 'ce' : 'pe');
  useEffect(() => { if (row) setSide(row.strike >= snap.spot ? 'ce' : 'pe'); }, [row?.strike]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  if (!row) return null;
  const live = snap.rows.find((r) => r.strike === row.strike) || row;       // follow auto-refresh
  const battle = snap.analysis.battle.find((b) => b.strike === row.strike);
  const c = live[side];
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <aside className="relative w-full sm:w-[520px] h-full bg-surface-1 border-l border-surface-3 overflow-y-auto p-4 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-[11px] uppercase tracking-wider text-gray-500">{snap.index} · {snap.expiry}</div>
            <div className="text-lg font-bold text-white">Strike {fmtInt(row.strike)}</div>
            {battle && <div className="text-[12px] text-gray-400">{battle.note}</div>}
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white" aria-label="Close"><X className="w-5 h-5" /></button>
        </div>
        <div className="flex gap-1 p-1 rounded-lg bg-surface-2 border border-surface-3">
          {['ce', 'pe'].map((s) => (
            <button key={s} onClick={() => setSide(s)}
              className={`flex-1 py-1.5 rounded-md text-[12.5px] font-semibold transition ${side === s ? 'bg-surface-3 text-white' : 'text-gray-400 hover:text-gray-200'}`}>
              <span className="inline-block w-2 h-2 rounded-full mr-1.5" style={{ background: s === 'ce' ? pal.call : pal.put }} />
              {s === 'ce' ? 'Call (CE)' : 'Put (PE)'}
            </button>
          ))}
        </div>
        {c ? <ContractView c={c} side={side} snap={snap} battle={battle} /> : <Empty>No {side.toUpperCase()} listed at this strike.</Empty>}
      </aside>
    </div>
  );
}
