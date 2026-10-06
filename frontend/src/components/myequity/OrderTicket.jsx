import React, { useEffect, useMemo, useState } from 'react';
import { X, Loader2, AlertTriangle, CheckCircle2, TrendingUp, Layers } from 'lucide-react';
import { api } from '../../api';
import { N } from './ui';

/**
 * Buy or sell straight from the workspace — the stock itself, or an option on it.
 *
 * It posts to the same desk Manual Trading uses (`/api/manual/order`), so the risk fence, the
 * order log and the position views all behave exactly as they do there — this is a shortcut to
 * that desk, not a second one. A real order needs the confirm step; nothing is sent until then.
 *
 * The Options tab appears only when the stock actually has listed contracts. Quantity there is
 * in LOTS, because that is the only quantity an exchange will accept.
 */

const EQ_PRODUCTS = [['CNC', 'Delivery (CNC)'], ['MIS', 'Intraday (MIS)']];
const FO_PRODUCTS = [['NRML', 'Hold (NRML)'], ['MIS', 'Intraday (MIS)']];
const TYPES = [['MARKET', 'Market'], ['LIMIT', 'Limit']];

export default function OrderTicket({ row, side, onClose, onPlaced }) {
  const [tab, setTab] = useState('equity');
  const [qty, setQty] = useState(1);
  const [product, setProduct] = useState(row.category === 'INVESTMENT' ? 'CNC' : 'MIS');
  const [type, setType] = useState('LIMIT');
  const [price, setPrice] = useState(row.ltp ? Number(row.ltp).toFixed(2) : '');
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [done, setDone] = useState(null);

  // options
  const [fno, setFno] = useState(null);         // null = still looking, {fno:false} = none listed
  const [leg, setLeg] = useState(null);         // {symbol, strike, type, ltp, lot_size}
  const [lots, setLots] = useState(1);
  const [foProduct, setFoProduct] = useState('MIS');
  const [foType, setFoType] = useState('LIMIT');
  const [foPrice, setFoPrice] = useState('');

  useEffect(() => {
    let live = true;
    api.meFno(row.symbol, row.ltp).then((d) => live && setFno(d)).catch(() => live && setFno({ fno: false }));
    return () => { live = false; };
  }, [row.symbol, row.ltp]);

  const buy = side === 'BUY';
  const onEquity = tab === 'equity';
  const limit = onEquity ? type === 'LIMIT' : foType === 'LIMIT';

  const pickLeg = (strike, kind, o) => {
    setLeg({ ...o, strike, type: kind });
    setFoPrice(o.ltp ? Number(o.ltp).toFixed(2) : '');
    setConfirm(false);
  };

  const lotSize = leg?.lot_size || fno?.lot_size || 0;
  const foQty = lots * lotSize;
  const value = useMemo(() => (onEquity
    ? (Number(qty) || 0) * (Number(type === 'LIMIT' ? price : row.ltp) || 0)
    : foQty * (Number(foType === 'LIMIT' ? foPrice : leg?.ltp) || 0)),
    [onEquity, qty, price, type, row.ltp, foQty, foPrice, foType, leg]);

  const ready = onEquity
    ? Boolean(qty) && (type !== 'LIMIT' || Boolean(price))
    : Boolean(leg) && lots > 0 && (foType !== 'LIMIT' || Boolean(foPrice));

  const place = async () => {
    setBusy(true); setErr('');
    try {
      const body = onEquity
        ? {
          tradingsymbol: row.symbol, exchange: row.exchange || 'NSE', side,
          quantity: Number(qty), order_type: type, product,
          price: type === 'LIMIT' ? Number(price) : 0,
        }
        : {
          tradingsymbol: leg.symbol, exchange: 'NFO', side,
          quantity: foQty, order_type: foType, product: foProduct,
          price: foType === 'LIMIT' ? Number(foPrice) : 0,
        };
      const r = await api.equityOrder({ ...body, trigger_price: 0, tag: 'my-equity', mode: 'LIVE' });
      setDone(r.order_ids?.join(', ') || 'placed');
      onPlaced?.(r);
    } catch (e) {
      setErr(String(e.message || e));
      setConfirm(false);
    } finally { setBusy(false); }
  };

  const what = onEquity
    ? `${side} ${qty} ${row.symbol} ${type === 'LIMIT' ? `at ${N(price)}` : 'at market'} (${product})`
    : `${side} ${lots} lot${lots === 1 ? '' : 's'} (${foQty}) of ${leg?.symbol} `
      + `${foType === 'LIMIT' ? `at ${N(foPrice)}` : 'at market'} (${foProduct})`;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="w-full max-w-lg card !p-4 space-y-3 max-h-[92vh] overflow-y-auto"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <span className={`px-2 py-0.5 rounded text-[11px] font-bold ${buy ? 'bg-emerald-500 text-white' : 'bg-red-500 text-white'}`}>
                {side}
              </span>
              <span className="text-[15px] font-bold text-white">{row.symbol}</span>
              <span className="text-[10px] text-gray-500 border border-surface-3 rounded px-1">{row.exchange}</span>
            </div>
            <div className="text-[11.5px] text-gray-500 mt-0.5">{row.company} · last trade {N(row.ltp)}</div>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-200"><X className="w-4 h-4" /></button>
        </div>

        {done ? (
          <div className="space-y-3">
            <div className="flex items-start gap-2 text-[12.5px] text-emerald-400">
              <CheckCircle2 className="w-4 h-4 shrink-0 mt-px" />
              <span>Order sent to Zerodha — id {done}. Track it in Orders or Manual Trading.</span>
            </div>
            <button onClick={onClose} className="btn-secondary !py-1.5 !px-3 text-[12.5px] w-full">Close</button>
          </div>
        ) : (
          <>
            <div className="flex gap-1 border-b border-surface-3">
              <button onClick={() => { setTab('equity'); setConfirm(false); }}
                className={`px-3 py-1.5 text-[12.5px] flex items-center gap-1.5 border-b-2 -mb-px transition-colors ${
                  onEquity ? 'border-brand-500 text-brand-400' : 'border-transparent text-gray-400 hover:text-white'}`}>
                <TrendingUp className="w-3.5 h-3.5" />Stock
              </button>
              {fno?.fno && (
                <button onClick={() => { setTab('options'); setConfirm(false); }}
                  className={`px-3 py-1.5 text-[12.5px] flex items-center gap-1.5 border-b-2 -mb-px transition-colors ${
                    !onEquity ? 'border-brand-500 text-brand-400' : 'border-transparent text-gray-400 hover:text-white'}`}>
                  <Layers className="w-3.5 h-3.5" />Options
                </button>
              )}
              {fno === null && <span className="px-3 py-1.5 text-[11.5px] text-gray-600 flex items-center gap-1">
                <Loader2 className="w-3 h-3 animate-spin" />checking F&amp;O…</span>}
            </div>

            {onEquity ? (
              <div className="grid grid-cols-2 gap-3">
                <Field label="Quantity">
                  <input type="number" min={1} value={qty}
                    onChange={(e) => { setQty(e.target.value); setConfirm(false); }}
                    className="input-field !py-1.5 mt-1 w-full mono" />
                </Field>
                <Field label="Product">
                  <select value={product} onChange={(e) => { setProduct(e.target.value); setConfirm(false); }}
                    className="input-field !py-1.5 mt-1 w-full">
                    {EQ_PRODUCTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                  </select>
                </Field>
                <Field label="Order type">
                  <select value={type} onChange={(e) => { setType(e.target.value); setConfirm(false); }}
                    className="input-field !py-1.5 mt-1 w-full">
                    {TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                  </select>
                </Field>
                <Field label="Limit price">
                  <input type="number" step="0.05" value={type === 'LIMIT' ? price : ''} disabled={type !== 'LIMIT'}
                    onChange={(e) => { setPrice(e.target.value); setConfirm(false); }}
                    className="input-field !py-1.5 mt-1 w-full mono disabled:opacity-40" />
                </Field>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="text-[11.5px] text-gray-500">
                  Expiry <span className="mono text-gray-300">{fno?.expiry}</span> ·
                  lot <span className="mono text-gray-300">{fno?.lot_size}</span> ·
                  spot <span className="mono text-gray-300">{N(fno?.spot)}</span>
                </div>
                <div className="border border-surface-3 rounded-lg overflow-hidden">
                  <div className="grid grid-cols-3 text-[10px] uppercase tracking-wider text-gray-400
                                  bg-surface-3/70 border-b border-surface-4 font-semibold">
                    <div className="px-2 py-1.5 text-center">Call</div>
                    <div className="px-2 py-1.5 text-center">Strike</div>
                    <div className="px-2 py-1.5 text-center">Put</div>
                  </div>
                  {(fno?.chain || []).map((c) => (
                    <div key={c.strike}
                      className={`grid grid-cols-3 border-b border-surface-3 last:border-0 ${
                        c.atm ? 'bg-brand-500/10' : ''}`}>
                      <StrikeCell o={c.ce} kind="CE" picked={leg?.symbol === c.ce?.symbol}
                        onPick={() => c.ce && pickLeg(c.strike, 'CE', c.ce)} />
                      <div className={`px-2 py-1.5 text-center mono text-[12px] ${
                        c.atm ? 'text-brand-300 font-bold' : 'text-gray-300'}`}>{N(c.strike, 0)}</div>
                      <StrikeCell o={c.pe} kind="PE" picked={leg?.symbol === c.pe?.symbol}
                        onPick={() => c.pe && pickLeg(c.strike, 'PE', c.pe)} />
                    </div>
                  ))}
                </div>

                {leg ? (
                  <>
                    <div className="text-[12px] text-gray-300">
                      Buying <span className="mono text-white">{leg.symbol}</span>
                      <span className="text-gray-500"> · {leg.type} {N(leg.strike, 0)} · last {N(leg.ltp)}</span>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <Field label="Lots" hint={`1 lot = ${lotSize} qty`}>
                        <div className="flex items-stretch gap-1 mt-1">
                          <button type="button" onClick={() => { setLots(Math.max(1, lots - 1)); setConfirm(false); }}
                            className="w-9 shrink-0 rounded-lg border border-surface-3 bg-surface-2 text-lg leading-none text-gray-300 hover:text-white">−</button>
                          <input type="number" min={1} value={lots}
                            onChange={(e) => { setLots(Math.max(1, Number(e.target.value) || 1)); setConfirm(false); }}
                            className="input-field !py-1.5 w-full mono text-center" />
                          <button type="button" onClick={() => { setLots(lots + 1); setConfirm(false); }}
                            className="w-9 shrink-0 rounded-lg border border-surface-3 bg-surface-2 text-lg leading-none text-gray-300 hover:text-white">+</button>
                        </div>
                      </Field>
                      <Field label="Product" hint="intraday or carry it">
                        <select value={foProduct} onChange={(e) => { setFoProduct(e.target.value); setConfirm(false); }}
                          className="input-field !py-1.5 mt-1 w-full">
                          {FO_PRODUCTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                        </select>
                      </Field>
                      <Field label="Order type">
                        <select value={foType} onChange={(e) => { setFoType(e.target.value); setConfirm(false); }}
                          className="input-field !py-1.5 mt-1 w-full">
                          {TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                        </select>
                      </Field>
                      <Field label="Limit price">
                        <input type="number" step="0.05" value={foType === 'LIMIT' ? foPrice : ''}
                          disabled={foType !== 'LIMIT'}
                          onChange={(e) => { setFoPrice(e.target.value); setConfirm(false); }}
                          className="input-field !py-1.5 mt-1 w-full mono disabled:opacity-40" />
                      </Field>
                    </div>
                    <div className="text-[11.5px] text-gray-500">
                      {lots} lot{lots === 1 ? '' : 's'} = <span className="mono text-gray-300">{foQty}</span> quantity
                    </div>
                  </>
                ) : (
                  <div className="text-[12px] text-gray-500">Pick a call or a put above.</div>
                )}
              </div>
            )}

            <div className="flex items-center justify-between text-[12px] text-gray-400 border-t border-surface-3 pt-2">
              <span>Order value</span>
              <span className="mono text-gray-100">₹ {N(value)}</span>
            </div>

            {err && <div className="text-[12px] text-red-400 flex items-start gap-1.5">
              <AlertTriangle className="w-4 h-4 shrink-0 mt-px" />{err}</div>}

            {confirm ? (
              <div className="space-y-2">
                <div className="text-[12px] text-amber-500 flex items-start gap-1.5">
                  <AlertTriangle className="w-4 h-4 shrink-0 mt-px" />This places a real order: {what}.
                </div>
                <div className="flex gap-2">
                  <button onClick={place} disabled={busy}
                    className={`flex-1 !py-2 text-[13px] font-semibold rounded-lg text-white disabled:opacity-50 ${buy ? 'bg-emerald-600 hover:bg-emerald-700' : 'bg-red-600 hover:bg-red-700'}`}>
                    {busy ? <Loader2 className="w-4 h-4 animate-spin mx-auto" /> : `Yes, ${side.toLowerCase()} it`}
                  </button>
                  <button onClick={() => setConfirm(false)} className="btn-secondary !py-2 !px-3 text-[12.5px]">Back</button>
                </div>
              </div>
            ) : (
              <button onClick={() => setConfirm(true)} disabled={!ready}
                className={`w-full !py-2 text-[13px] font-semibold rounded-lg text-white disabled:opacity-50 ${buy ? 'bg-emerald-600 hover:bg-emerald-700' : 'bg-red-600 hover:bg-red-700'}`}>
                Review {side.toLowerCase()} order
              </button>
            )}
            <div className="text-[10.5px] text-gray-500">
              Goes through the same risk fence and order log as Manual Trading.
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Field({ label, hint, children }) {
  return (
    <label className="block">
      <div className="flex h-4 items-baseline justify-between gap-2 overflow-hidden whitespace-nowrap">
        <span className="text-[10px] uppercase tracking-wider text-gray-500">{label}</span>
        {hint && <span className="text-[10px] text-gray-600 truncate">{hint}</span>}
      </div>
      {children}
    </label>
  );
}

function StrikeCell({ o, kind, picked, onPick }) {
  if (!o) return <div className="px-2 py-1.5 text-center text-[11px] text-gray-700">—</div>;
  return (
    <button onClick={onPick}
      className={`px-2 py-1.5 text-center transition-colors ${picked
        ? 'bg-brand-500/25 ring-1 ring-inset ring-brand-500'
        : 'hover:bg-surface-2'}`}>
      <div className={`mono text-[12px] ${kind === 'CE' ? 'text-emerald-400' : 'text-red-400'}`}>
        {o.ltp == null ? '—' : N(o.ltp)}
      </div>
    </button>
  );
}
