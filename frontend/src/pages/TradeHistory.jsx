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

/* Derive strategy label from the order tag */
const getStrategyLabel = (tag) => {
  if (!tag) return 'Manual';
  const t = tag.toUpperCase();
  if (t.startsWith('S1')) return 'Strategy 1';
  if (t.startsWith('S2')) return 'Strategy 2';
  if (t.startsWith('S3')) return 'Strategy 3';
  if (t.startsWith('MANUAL')) return 'Manual';
  return 'Manual';
};

const strategyBadge = (label) => {
  switch (label) {
    case 'Strategy 1': return 'bg-purple-600/20 text-purple-400 border border-purple-500/30';
    case 'Strategy 2': return 'bg-amber-600/20 text-amber-400 border border-amber-500/30';
    case 'Strategy 3': return 'bg-cyan-600/20 text-cyan-400 border border-cyan-500/30';
    default:           return 'bg-gray-600/20 text-gray-400 border border-gray-500/30';
  }
};

/* Compute P&L stats for a day using only COMPLETE orders */
const computeDaySummary = (orders) => {
  const filled = orders.filter((o) => o.status === 'COMPLETE');
  let totalBuyValue = 0, totalSellValue = 0, totalBuyQty = 0, totalSellQty = 0;

  filled.forEach((o) => {
    const price = o.average_price || o.price || 0;
    const value = price * o.quantity;
    if (o.transaction_type === 'BUY') {
      totalBuyValue += value;
      totalBuyQty += o.quantity;
    } else {
      totalSellValue += value;
      totalSellQty += o.quantity;
    }
  });

  const pnl = totalSellValue - totalBuyValue;
  const totalTraded = totalBuyValue + totalSellValue;
  return { totalBuyValue, totalSellValue, totalBuyQty, totalSellQty, pnl, totalTraded, filledCount: filled.length };
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
                    <p className="text-[10px] text-gray-600 mt-0.5">{summary.filledCount} filled orders</p>
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
