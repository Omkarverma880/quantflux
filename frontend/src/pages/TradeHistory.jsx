import React, { useState, useEffect } from 'react';
import { api } from '../api';
import {
  History,
  Calendar,
  RefreshCw,
  ArrowUpCircle,
  ArrowDownCircle,
  TrendingUp,
  TrendingDown,
  IndianRupee,
} from 'lucide-react';

/* Derive strategy label from the order tag.
 *
 * Strategy modules tag orders as S{N}ENTRY / S{N}SL / S{N}TGT / S{N}SLIP /
 * S{N}EXIT etc. The manual-trading and exit-all paths use MANUAL / EXITALL.
 * Anything else is treated as Manual (legacy or external orders). */
const getStrategyLabel = (tag) => {
  if (!tag) return 'Manual';
  const t = String(tag).toUpperCase();
  // Match S<digit>... at the start of the tag — covers S1..S9 entry/SL/TGT/SLIP/EXIT.
  const m = t.match(/^S(\d)/);
  if (m) return `Strategy ${m[1]}`;
  if (t.startsWith('EXITALL')) return 'Exit All';
  if (t.startsWith('MANUAL')) return 'Manual';
  return 'Manual';
};

const strategyBadge = (label) => {
  switch (label) {
    case 'Strategy 1': return 'bg-purple-600/20 text-purple-400 border border-purple-500/30';
    case 'Strategy 2': return 'bg-amber-600/20 text-amber-400 border border-amber-500/30';
    case 'Strategy 3': return 'bg-cyan-600/20 text-cyan-400 border border-cyan-500/30';
    case 'Strategy 4': return 'bg-rose-600/20 text-rose-400 border border-rose-500/30';
    case 'Strategy 5': return 'bg-emerald-600/20 text-emerald-400 border border-emerald-500/30';
    case 'Strategy 6': return 'bg-blue-600/20 text-blue-400 border border-blue-500/30';
    case 'Strategy 7': return 'bg-indigo-600/20 text-indigo-400 border border-indigo-500/30';
    case 'Strategy 8': return 'bg-pink-600/20 text-pink-400 border border-pink-500/30';
    case 'Strategy 9': return 'bg-teal-600/20 text-teal-400 border border-teal-500/30';
    case 'Exit All':   return 'bg-red-600/20 text-red-400 border border-red-500/30';
    default:           return 'bg-gray-600/20 text-gray-400 border border-gray-500/30';
  }
};

/*
 * Compute day P&L correctly — per symbol, FIFO-matched.
 *
 * Why this matters:
 *   Naive `sellValue − buyValue` is wrong whenever total BUY qty != SELL qty.
 *   Any unmatched BUY quantity is an OPEN position, not realized loss. Including
 *   its full acquisition cost in P&L makes losses look huge (and hides true P&L
 *   when duplicate entries or MTM-still-open legs are present).
 *
 * Algorithm:
 *   1. Group COMPLETE orders by symbol.
 *   2. Within each symbol, walk chronologically and FIFO-match opposing legs.
 *      For options we can have either side opening (BUY to open, or SELL to open).
 *      FIFO across both sides gives the correct realized P&L either way.
 *   3. Realized P&L = sum over matched fills of (sell_px − buy_px) × qty.
 *   4. Unmatched remaining qty = open position (reported separately, excluded).
 */
const computeDaySummary = (orders) => {
  const filled = orders.filter((o) => o.status === 'COMPLETE');

  let totalBuyValue = 0, totalSellValue = 0, totalBuyQty = 0, totalSellQty = 0;
  let realizedPnl = 0;
  let openQty = 0;  // absolute remaining unmatched qty across all symbols

  // Group by symbol
  const bySymbol = {};
  filled.forEach((o) => {
    const sym = o.tradingsymbol || 'UNKNOWN';
    if (!bySymbol[sym]) bySymbol[sym] = [];
    bySymbol[sym].push(o);

    const price = Number(o.average_price) || Number(o.price) || 0;
    const qty = Number(o.quantity) || 0;
    const value = price * qty;
    if (o.transaction_type === 'BUY') {
      totalBuyValue += value;
      totalBuyQty += qty;
    } else {
      totalSellValue += value;
      totalSellQty += qty;
    }
  });

  // FIFO-match within each symbol
  Object.values(bySymbol).forEach((arr) => {
    // Chronological order (trade history already sorted; re-sort defensively)
    const sorted = [...arr].sort((a, b) => String(a.time || '').localeCompare(String(b.time || '')));

    // Two FIFO queues: entries still waiting to be closed.
    // Each queue item: { qty, price }
    const buyQueue = [];
    const sellQueue = [];

    sorted.forEach((o) => {
      let qty = Number(o.quantity) || 0;
      const price = Number(o.average_price) || Number(o.price) || 0;
      if (qty <= 0) return;

      if (o.transaction_type === 'BUY') {
        // Close any open SELLs first (short covering)
        while (qty > 0 && sellQueue.length > 0) {
          const head = sellQueue[0];
          const matched = Math.min(qty, head.qty);
          realizedPnl += (head.price - price) * matched;  // sold high, bought to cover
          head.qty -= matched;
          qty -= matched;
          if (head.qty === 0) sellQueue.shift();
        }
        if (qty > 0) buyQueue.push({ qty, price });
      } else {
        // SELL — close any open BUYs first (long exits)
        while (qty > 0 && buyQueue.length > 0) {
          const head = buyQueue[0];
          const matched = Math.min(qty, head.qty);
          realizedPnl += (price - head.price) * matched;
          head.qty -= matched;
          qty -= matched;
          if (head.qty === 0) buyQueue.shift();
        }
        if (qty > 0) sellQueue.push({ qty, price });
      }
    });

    // Whatever remains in either queue is an open position today
    openQty += buyQueue.reduce((s, x) => s + x.qty, 0);
    openQty += sellQueue.reduce((s, x) => s + x.qty, 0);
  });

  const totalTraded = totalBuyValue + totalSellValue;
  return {
    totalBuyValue,
    totalSellValue,
    totalBuyQty,
    totalSellQty,
    pnl: realizedPnl,
    openQty,
    totalTraded,
    filledCount: filled.length,
  };
};

export default function TradeHistory() {
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchHistory = () => {
    setLoading(true);
    api.strategy1TradeHistory()
      .then((data) => setHistory(Array.isArray(data) ? data : []))
      .catch(() => setHistory([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => { fetchHistory(); }, []);

  const today = new Date().toISOString().slice(0, 10);

  const statusBadge = (status) => {
    switch (status) {
      case 'COMPLETE': return 'bg-green-600/20 text-green-400 border border-green-500/30';
      case 'CANCELLED': return 'bg-gray-600/20 text-gray-400 border border-gray-500/30';
      case 'REJECTED': return 'bg-red-600/20 text-red-400 border border-red-500/30';
      case 'OPEN': case 'TRIGGER PENDING': return 'bg-yellow-600/20 text-yellow-400 border border-yellow-500/30';
      default: return 'bg-gray-600/20 text-gray-400 border border-gray-500/30';
    }
  };

  // Count total orders across all days
  const totalOrders = history.reduce((s, d) => s + (d.orders?.length || 0), 0);

  return (
    <div className="p-3 sm:p-6 space-y-4 sm:space-y-6 max-w-[1400px] mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold text-white">Trade History</h1>
          <p className="text-xs sm:text-sm text-gray-500 mt-0.5">
            Past orders across all trading days
            {totalOrders > 0 && <span className="ml-1">— {totalOrders} total orders across {history.length} day{history.length > 1 ? 's' : ''}</span>}
          </p>
        </div>
        <button onClick={fetchHistory} className="btn-secondary flex items-center gap-2 text-sm">
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Days */}
      {loading ? (
        <div className="card text-center py-12 text-gray-500">Loading…</div>
      ) : history.length === 0 ? (
        <div className="card text-center py-12">
          <History className="w-10 h-10 text-gray-600 mx-auto mb-3" />
          <p className="text-gray-400 font-medium">No trade history yet</p>
          <p className="text-xs text-gray-600 mt-1">Orders will be stored here at end of each trading day</p>
        </div>
      ) : (
        history.map((day) => {
          const isToday = day.date === today;
          const orders = day.orders || [];
          const completedOrders = orders.filter((o) => o.status === 'COMPLETE');
          const summary = computeDaySummary(orders);

          return (
            <div key={day.date} className={`card space-y-3 ${isToday ? 'border border-brand-500/30' : ''}`}>
              {/* Date header */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Calendar className={`w-4 h-4 ${isToday ? 'text-brand-400' : 'text-gray-500'}`} />
                  <h3 className="font-semibold text-white">
                    {isToday ? 'Today' : day.date}
                    {isToday && <span className="ml-2 text-xs text-gray-500">({day.date})</span>}
                  </h3>
                  <span className="text-xs text-gray-500 ml-1">
                    {orders.length} order{orders.length !== 1 ? 's' : ''}
                    {completedOrders.length !== orders.length && ` (${completedOrders.length} filled)`}
                  </span>
                </div>
              </div>

              {/* Day P&L Summary */}
              {summary.filledCount > 0 && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  <div className="bg-surface-2 rounded-lg px-4 py-2.5">
                    <p className="text-[11px] text-gray-500 uppercase tracking-wide mb-0.5">Buy Value</p>
                    <div className="flex items-center gap-1">
                      <ArrowUpCircle className="w-3.5 h-3.5 text-blue-400" />
                      <span className="text-sm font-semibold text-blue-400 mono">₹{summary.totalBuyValue.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                    </div>
                    <p className="text-[10px] text-gray-600 mt-0.5">{summary.totalBuyQty} qty bought</p>
                  </div>
                  <div className="bg-surface-2 rounded-lg px-4 py-2.5">
                    <p className="text-[11px] text-gray-500 uppercase tracking-wide mb-0.5">Sell Value</p>
                    <div className="flex items-center gap-1">
                      <ArrowDownCircle className="w-3.5 h-3.5 text-orange-400" />
                      <span className="text-sm font-semibold text-orange-400 mono">₹{summary.totalSellValue.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                    </div>
                    <p className="text-[10px] text-gray-600 mt-0.5">{summary.totalSellQty} qty sold</p>
                  </div>
                  <div className="bg-surface-2 rounded-lg px-4 py-2.5">
                    <p className="text-[11px] text-gray-500 uppercase tracking-wide mb-0.5">Day P&L</p>
                    <div className="flex items-center gap-1">
                      {summary.pnl >= 0
                        ? <TrendingUp className="w-3.5 h-3.5 text-green-400" />
                        : <TrendingDown className="w-3.5 h-3.5 text-red-400" />}
                      <span className={`text-sm font-semibold mono ${summary.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                        {summary.pnl >= 0 ? '+' : ''}₹{summary.pnl.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                      </span>
                    </div>
                    <p className="text-[10px] text-gray-600 mt-0.5">
                      Realized · {summary.filledCount} filled
                      {summary.openQty > 0 && (
                        <span className="text-yellow-500/80"> · {summary.openQty} open</span>
                      )}
                    </p>
                  </div>
                  <div className="bg-surface-2 rounded-lg px-4 py-2.5">
                    <p className="text-[11px] text-gray-500 uppercase tracking-wide mb-0.5">Total Traded</p>
                    <div className="flex items-center gap-1">
                      <IndianRupee className="w-3.5 h-3.5 text-gray-400" />
                      <span className="text-sm font-semibold text-white mono">₹{summary.totalTraded.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
                    </div>
                    <p className="text-[10px] text-gray-600 mt-0.5">Invested value</p>
                  </div>
                </div>
              )}

              {/* Orders table */}
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-surface-3">
                      <th className="text-left text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">Time</th>
                      <th className="text-left text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">Symbol</th>
                      <th className="text-center text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">Strategy</th>
                      <th className="text-center text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">Side</th>
                      <th className="text-right text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">Qty</th>
                      <th className="text-right text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">Price</th>
                      <th className="text-center text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orders.map((o, i) => {
                      const time = o.time ? String(o.time) : '—';
                      const isBuy = o.transaction_type === 'BUY';
                      const displayPrice = o.average_price || o.price || 0;
                      const strat = getStrategyLabel(o.tag);

                      return (
                        <tr key={i} className="border-b border-surface-3/50 hover:bg-surface-2 transition-colors">
                          <td className="px-5 py-3 text-sm text-gray-400 mono">{time}</td>
                          <td className="px-5 py-3">
                            <span className="font-medium text-white">{o.tradingsymbol}</span>
                          </td>
                          <td className="px-5 py-3 text-center">
                            <span className={`px-2 py-0.5 rounded text-xs font-medium ${strategyBadge(strat)}`}>
                              {strat}
                            </span>
                          </td>
                          <td className="px-5 py-3 text-center">
                            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${
                              isBuy ? 'bg-blue-600/20 text-blue-400' : 'bg-orange-600/20 text-orange-400'
                            }`}>
                              {isBuy
                                ? <ArrowUpCircle className="w-3 h-3" />
                                : <ArrowDownCircle className="w-3 h-3" />
                              }
                              {o.transaction_type}
                            </span>
                          </td>
                          <td className="px-5 py-3 text-right mono text-gray-300">{o.quantity}</td>
                          <td className="px-5 py-3 text-right mono text-white">₹{displayPrice?.toFixed(2)}</td>
                          <td className="px-5 py-3 text-center">
                            <span className={`px-2.5 py-0.5 rounded text-xs font-semibold ${statusBadge(o.status)}`}>
                              {o.status}
                            </span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          );
        })
      )}
    </div>
  );
}
