import React, { useState, useEffect } from 'react';
import { api } from '../api';
import { useToast } from '../ToastContext';
import { TableSkeleton } from '../components/ErrorBoundary';
import {
  ClipboardList,
  ArrowUpCircle,
  ArrowDownCircle,
  RefreshCw,
  Package,
  Briefcase,
  XCircle,
  Edit3,
  X,
  Loader2,
} from 'lucide-react';

function Tab({ active, onClick, children }) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
        active
          ? 'bg-brand-600/15 text-brand-400 border border-brand-500/20'
          : 'text-gray-400 hover:text-white hover:bg-surface-3 border border-transparent'
      }`}
    >
      {children}
    </button>
  );
}

export default function Orders() {
  const [tab, setTab] = useState('positions');
  const [positions, setPositions] = useState([]);
  const [holdings, setHoldings] = useState([]);
  const [orders, setOrders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modifyModal, setModifyModal] = useState(null);
  const [modifyForm, setModifyForm] = useState({ price: '', quantity: '' });
  const [actionLoading, setActionLoading] = useState(null);
  const toast = useToast();

  const fetchData = async () => {
    setLoading(true);
    try {
      const [p, h, o] = await Promise.all([
        api.getPositions().catch(() => ({ positions: [] })),
        api.getHoldings().catch(() => ({ holdings: [] })),
        api.getOrders().catch(() => ({ orders: [] })),
      ]);
      setPositions(p.positions || []);
      setHoldings(h.holdings || []);
      setOrders(o.orders || []);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 10000);
    const onConnected = () => fetchData();
    const onDisconnected = () => { setPositions([]); setHoldings([]); setOrders([]); };
    window.addEventListener('zerodha_connected', onConnected);
    window.addEventListener('zerodha_disconnected', onDisconnected);
    return () => { clearInterval(interval); window.removeEventListener('zerodha_connected', onConnected); window.removeEventListener('zerodha_disconnected', onDisconnected); };
  }, []);

  const canCancel = (status) => ['OPEN', 'TRIGGER PENDING', 'AMO REQ RECEIVED'].includes(status);
  const canModify = (status) => ['OPEN', 'TRIGGER PENDING'].includes(status);

  const handleCancel = async (orderId) => {
    setActionLoading(orderId);
    try {
      await api.cancelOrder(orderId);
      toast.success('Order cancelled');
      await fetchData();
    } catch (e) {
      toast.error(e.message || 'Cancel failed');
    } finally {
      setActionLoading(null);
    }
  };

  const openModify = (order) => {
    setModifyModal(order);
    setModifyForm({
      price: order.price || order.trigger_price || '',
      quantity: order.quantity || '',
    });
  };

  const handleModify = async () => {
    if (!modifyModal) return;
    setActionLoading(modifyModal.order_id);
    try {
      await api.modifyOrder({
        order_id: modifyModal.order_id,
        price: parseFloat(modifyForm.price) || undefined,
        quantity: parseInt(modifyForm.quantity, 10) || undefined,
      });
      toast.success('Order modified');
      setModifyModal(null);
      await fetchData();
    } catch (e) {
      toast.error(e.message || 'Modify failed');
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div className="p-3 sm:p-6 space-y-4 sm:space-y-6 max-w-[1400px] mx-auto">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold text-white">Orders & Positions</h1>
          <p className="text-xs sm:text-sm text-gray-500 mt-0.5">Track your trades in real time</p>
        </div>
        <button onClick={fetchData} className="btn-ghost flex items-center gap-2" disabled={loading}>
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 flex-wrap">
        <Tab active={tab === 'positions'} onClick={() => setTab('positions')}>
          <span className="flex items-center gap-1.5"><Package className="w-4 h-4" /> Positions ({positions.filter(p => p.quantity !== 0).length})</span>
        </Tab>
        <Tab active={tab === 'holdings'} onClick={() => setTab('holdings')}>
          <span className="flex items-center gap-1.5"><Briefcase className="w-4 h-4" /> Holdings ({holdings.length})</span>
        </Tab>
        <Tab active={tab === 'orders'} onClick={() => setTab('orders')}>
          <span className="flex items-center gap-1.5"><ClipboardList className="w-4 h-4" /> Orders ({orders.length})</span>
        </Tab>
      </div>

      {/* Positions Table */}
      {tab === 'positions' && (
        <div className="card overflow-x-auto p-0">
          {positions.filter(p => p.quantity !== 0).length === 0 ? (
            <div className="text-center py-12">
              <Package className="w-10 h-10 mx-auto text-gray-600 mb-3" />
              <p className="text-gray-500">No open positions</p>
            </div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b border-surface-3">
                  <th className="text-left text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">Symbol</th>
                  <th className="text-right text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">Qty</th>
                  <th className="text-right text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">Avg Price</th>
                  <th className="text-right text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">P&L</th>
                  <th className="text-center text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">Product</th>
                </tr>
              </thead>
              <tbody>
                {positions.filter(p => p.quantity !== 0).map((p, i) => (
                  <tr key={i} className="border-b border-surface-3/50 hover:bg-surface-2 transition-colors">
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2">
                        {p.quantity > 0 ? (
                          <ArrowUpCircle className="w-4 h-4 text-green-400" />
                        ) : (
                          <ArrowDownCircle className="w-4 h-4 text-red-400" />
                        )}
                        <span className="font-medium text-white">{p.tradingsymbol}</span>
                        <span className="text-xs text-gray-500">{p.exchange}</span>
                      </div>
                    </td>
                    <td className={`px-5 py-3 text-right mono font-medium ${p.quantity > 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {p.quantity > 0 ? '+' : ''}{p.quantity}
                    </td>
                    <td className="px-5 py-3 text-right mono text-gray-300">₹{p.average_price?.toFixed(2)}</td>
                    <td className={`px-5 py-3 text-right mono font-semibold ${p.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {p.pnl >= 0 ? '+' : ''}₹{p.pnl?.toFixed(2)}
                    </td>
                    <td className="px-5 py-3 text-center">
                      <span className="badge-blue">{p.product}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Holdings Table */}
      {tab === 'holdings' && (
        <div className="card overflow-x-auto p-0">
          {holdings.length === 0 ? (
            <div className="text-center py-12">
              <Briefcase className="w-10 h-10 mx-auto text-gray-600 mb-3" />
              <p className="text-gray-500">No holdings</p>
            </div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b border-surface-3">
                  <th className="text-left text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">Symbol</th>
                  <th className="text-right text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">Qty</th>
                  <th className="text-right text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">Avg Price</th>
                  <th className="text-right text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">LTP</th>
                  <th className="text-right text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">P&L</th>
                </tr>
              </thead>
              <tbody>
                {holdings.map((h, i) => (
                  <tr key={i} className="border-b border-surface-3/50 hover:bg-surface-2 transition-colors">
                    <td className="px-5 py-3">
                      <span className="font-medium text-white">{h.tradingsymbol}</span>
                      <span className="text-xs text-gray-500 ml-2">{h.exchange}</span>
                    </td>
                    <td className="px-5 py-3 text-right mono text-gray-300">{h.quantity}</td>
                    <td className="px-5 py-3 text-right mono text-gray-300">₹{h.average_price?.toFixed(2)}</td>
                    <td className="px-5 py-3 text-right mono text-gray-300">₹{h.last_price?.toFixed(2)}</td>
                    <td className={`px-5 py-3 text-right mono font-semibold ${h.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                      {h.pnl >= 0 ? '+' : ''}₹{h.pnl?.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Orders Table */}
      {tab === 'orders' && (
        <div className="card overflow-x-auto p-0">
          {loading ? <TableSkeleton /> : orders.length === 0 ? (
            <div className="text-center py-12">
              <ClipboardList className="w-10 h-10 mx-auto text-gray-600 mb-3" />
              <p className="text-gray-500">No orders today</p>
            </div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b border-surface-3">
                  <th className="text-left text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">Time</th>
                  <th className="text-left text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">Symbol</th>
                  <th className="text-center text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">Side</th>
                  <th className="text-right text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">Qty</th>
                  <th className="text-right text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">Price</th>
                  <th className="text-center text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">Status</th>
                  <th className="text-center text-xs text-gray-500 font-medium uppercase tracking-wide px-5 py-3">Actions</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o, i) => (
                  <tr key={i} className="border-b border-surface-3/50 hover:bg-surface-2 transition-colors">
                    <td className="px-5 py-3 text-xs mono text-gray-400">
                      {o.order_timestamp || o.exchange_timestamp || '—'}
                    </td>
                    <td className="px-5 py-3 font-medium text-white">{o.tradingsymbol}</td>
                    <td className="px-5 py-3 text-center">
                      <span className={o.transaction_type === 'BUY' ? 'badge-green' : 'badge-red'}>
                        {o.transaction_type}
                      </span>
                    </td>
                    <td className="px-5 py-3 text-right mono text-gray-300">{o.quantity}</td>
                    <td className="px-5 py-3 text-right mono text-gray-300">
                      ₹{o.average_price?.toFixed(2) || o.price?.toFixed(2) || '—'}
                    </td>
                    <td className="px-5 py-3 text-center">
                      <span className={
                        o.status === 'COMPLETE' ? 'badge-green' :
                        o.status === 'REJECTED' ? 'badge-red' :
                        'badge-yellow'
                      }>
                        {o.status}
                      </span>
                    </td>
                    <td className="px-5 py-3 text-center">
                      <div className="flex items-center justify-center gap-1.5">
                        {canModify(o.status) && (
                          <button
                            onClick={() => openModify(o)}
                            disabled={actionLoading === o.order_id}
                            className="p-1.5 rounded-md text-gray-400 hover:text-brand-400 hover:bg-brand-500/10 transition-colors"
                            title="Modify order"
                          >
                            <Edit3 className="w-3.5 h-3.5" />
                          </button>
                        )}
                        {canCancel(o.status) && (
                          <button
                            onClick={() => handleCancel(o.order_id)}
                            disabled={actionLoading === o.order_id}
                            className="p-1.5 rounded-md text-gray-400 hover:text-red-400 hover:bg-red-500/10 transition-colors"
                            title="Cancel order"
                          >
                            {actionLoading === o.order_id ? (
                              <Loader2 className="w-3.5 h-3.5 animate-spin" />
                            ) : (
                              <XCircle className="w-3.5 h-3.5" />
                            )}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Modify Order Modal */}
      {modifyModal && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
          <div className="bg-surface-1 border border-surface-3 rounded-xl p-6 max-w-sm w-full space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-white">Modify Order</h3>
              <button
                onClick={() => setModifyModal(null)}
                className="text-gray-500 hover:text-white"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <p className="text-sm text-gray-400">
              {modifyModal.tradingsymbol} · {modifyModal.transaction_type} · {modifyModal.order_type}
            </p>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-gray-500 mb-1">Price</label>
                <input
                  type="number"
                  step="0.05"
                  value={modifyForm.price}
                  onChange={(e) => setModifyForm((f) => ({ ...f, price: e.target.value }))}
                  className="input-field w-full mono"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Quantity</label>
                <input
                  type="number"
                  value={modifyForm.quantity}
                  onChange={(e) => setModifyForm((f) => ({ ...f, quantity: e.target.value }))}
                  className="input-field w-full mono"
                />
              </div>
            </div>
            <div className="flex gap-2 pt-2">
              <button
                onClick={() => setModifyModal(null)}
                className="btn-ghost flex-1 text-sm"
              >
                Cancel
              </button>
              <button
                onClick={handleModify}
                disabled={actionLoading === modifyModal.order_id}
                className="btn-primary flex-1 text-sm flex items-center justify-center gap-1.5"
              >
                {actionLoading === modifyModal.order_id ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Edit3 className="w-4 h-4" />
                )}
                Modify
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
