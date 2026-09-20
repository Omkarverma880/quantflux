import React, { useMemo, useState } from 'react';
import { X, Loader2, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { api } from '../../api';
import { N } from './ui';

/**
 * Buy or sell a stock straight from the workspace.
 *
 * It posts to the same desk Manual Trading uses (`/api/manual/order`), so the risk fence, the
 * order log and the position views all behave exactly as they do there — this is a shortcut to
 * that desk, not a second one. A real order needs the confirm step; nothing is sent until then.
 */

const PRODUCTS = [['CNC', 'Delivery (CNC)'], ['MIS', 'Intraday (MIS)']];
const TYPES = [['MARKET', 'Market'], ['LIMIT', 'Limit']];

export default function OrderTicket({ row, side, onClose, onPlaced }) {
  const [qty, setQty] = useState(1);
  const [product, setProduct] = useState(row.category === 'INVESTMENT' ? 'CNC' : 'MIS');
  const [type, setType] = useState('LIMIT');
  const [price, setPrice] = useState(row.ltp ? Number(row.ltp).toFixed(2) : '');
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [done, setDone] = useState(null);

  const buy = side === 'BUY';
  const limit = type === 'LIMIT';
  const value = useMemo(() => (Number(qty) || 0) * (Number(limit ? price : row.ltp) || 0), [qty, price, type, row.ltp, limit]);

  const place = async () => {
    setBusy(true); setErr('');
    try {
      const r = await api.equityOrder({
        tradingsymbol: row.symbol, exchange: row.exchange || 'NSE', side,
        quantity: Number(qty), order_type: type, product,
        price: limit ? Number(price) : 0, trigger_price: 0,
        tag: 'my-equity', mode: 'LIVE',
      });
      setDone(r.order_ids?.join(', ') || 'placed');
      onPlaced?.(r);
    } catch (e) {
      setErr(String(e.message || e));
      setConfirm(false);
    } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="w-full max-w-md card !p-4 space-y-3" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <span className={`px-2 py-0.5 rounded text-[11px] font-bold ${buy ? 'bg-emerald-500 text-white' : 'bg-red-500 text-white'}`}>
                {side}
              </span>
              <span className="text-[15px] font-bold text-white">{row.symbol}</span>
              <span className="text-[10px] text-gray-500 border border-surface-3 rounded px-1">{row.exchange}</span>
            </div>
            <div className="text-[11.5px] text-gray-500 mt-0.5">
              {row.company} · last trade {N(row.ltp)}
            </div>
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
            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <div className="text-[10px] uppercase tracking-wider text-gray-500">Quantity</div>
                <input type="number" min={1} value={qty} onChange={(e) => { setQty(e.target.value); setConfirm(false); }}
                  className="input-field !py-1.5 mt-1 w-full mono" />
              </label>
              <label className="block">
                <div className="text-[10px] uppercase tracking-wider text-gray-500">Product</div>
                <select value={product} onChange={(e) => { setProduct(e.target.value); setConfirm(false); }}
                  className="input-field !py-1.5 mt-1 w-full">
                  {PRODUCTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
              </label>
              <label className="block">
                <div className="text-[10px] uppercase tracking-wider text-gray-500">Order type</div>
                <select value={type} onChange={(e) => { setType(e.target.value); setConfirm(false); }}
                  className="input-field !py-1.5 mt-1 w-full">
                  {TYPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
              </label>
              <label className="block">
                <div className="text-[10px] uppercase tracking-wider text-gray-500">Limit price</div>
                <input type="number" step="0.05" value={limit ? price : ''} disabled={!limit}
                  onChange={(e) => { setPrice(e.target.value); setConfirm(false); }}
                  className="input-field !py-1.5 mt-1 w-full mono disabled:opacity-40" />
              </label>
            </div>

            <div className="flex items-center justify-between text-[12px] text-gray-400 border-t border-surface-3 pt-2">
              <span>Order value</span>
              <span className="mono text-gray-100">₹ {N(value)}</span>
            </div>

            {err && <div className="text-[12px] text-red-400 flex items-start gap-1.5"><AlertTriangle className="w-4 h-4 shrink-0 mt-px" />{err}</div>}

            {confirm ? (
              <div className="space-y-2">
                <div className="text-[12px] text-amber-500 flex items-start gap-1.5">
                  <AlertTriangle className="w-4 h-4 shrink-0 mt-px" />
                  This places a real order: {side} {qty} {row.symbol} {type === 'LIMIT' ? `at ${N(price)}` : 'at market'} ({product}).
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
              <button onClick={() => setConfirm(true)} disabled={!qty || (limit && !price)}
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
