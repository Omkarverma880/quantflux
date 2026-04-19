import { useRef, useEffect, useCallback } from 'react';

const BASE = '/api';

function getAuthHeaders() {
  const token = localStorage.getItem('app_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders(), ...options.headers },
    ...options,
  });
  if (res.status === 401) {
    // token expired or invalid — clear and reload to login
    localStorage.removeItem('app_token');
    localStorage.removeItem('app_user');
    window.location.reload();
    throw new Error('Session expired');
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || err.detail || res.statusText);
  }
  return res.json();
}

export const api = {
  // App Auth
  appLogin: (username, password) =>
    request('/auth/app-login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  register: (username, email, password, full_name) =>
    request('/auth/register', { method: 'POST', body: JSON.stringify({ username, email, password, full_name }) }),
  forgotPassword: (username, email) =>
    request('/auth/forgot-password', { method: 'POST', body: JSON.stringify({ username, email }) }),
  resetPassword: (token, new_password) =>
    request('/auth/reset-password', { method: 'POST', body: JSON.stringify({ token, new_password }) }),
  forgotPassword: (username, email) =>
    request('/auth/forgot-password', { method: 'POST', body: JSON.stringify({ username, email }) }),
  resetPassword: (token, new_password) =>
    request('/auth/reset-password', { method: 'POST', body: JSON.stringify({ token, new_password }) }),
  getMe: () => request('/auth/me'),
  onboard: (kite_api_key, kite_api_secret) =>
    request('/auth/onboard', { method: 'POST', body: JSON.stringify({ kite_api_key, kite_api_secret }) }),

  // Zerodha Auth
  getLoginUrl: () => request('/auth/login'),
  getAuthStatus: () => request('/auth/status'),
  logout: () => request('/auth/logout', { method: 'POST' }),

  // Dashboard
  getSummary: () => request('/dashboard/summary'),
  getLtp: (instruments) => request(`/dashboard/ltp?instruments=${instruments}`),

  // Trading
  getEngineStatus: () => request('/trading/engine/status'),
  startEngine: () => request('/trading/engine/start', { method: 'POST' }),
  stopEngine: () => request('/trading/engine/stop', { method: 'POST' }),
  getPositions: () => request('/trading/positions'),
  getHoldings: () => request('/trading/holdings'),
  getOrders: () => request('/trading/orders'),
  getMargins: () => request('/trading/margins'),
  placeOrder: (order) => request('/trading/order', { method: 'POST', body: JSON.stringify(order) }),
  cancelOrder: (orderId) => request(`/trading/order/cancel/${orderId}`, { method: 'POST' }),
  modifyOrder: (data) => request('/trading/order/modify', { method: 'PUT', body: JSON.stringify(data) }),

  // Strategies
  getStrategies: () => request('/strategies/'),
  getStrategy: (name) => request(`/strategies/${name}`),
  updateStrategyConfig: (name, config) =>
    request(`/strategies/${name}/config`, { method: 'PUT', body: JSON.stringify(config) }),
  activateStrategy: (name) => request(`/strategies/${name}/activate`, { method: 'POST' }),
  deactivateStrategy: (name) => request(`/strategies/${name}/deactivate`, { method: 'POST' }),

  // Strategy 1 — Cumulative Volume
  getCumulativeVolumeData: () => request('/strategy1/data'),
  getCumulativeVolumeConfig: () => request('/strategy1/config'),
  updateCumulativeVolumeConfig: (config) =>
    request('/strategy1/config', { method: 'PUT', body: JSON.stringify(config) }),

  // Strategy 1 — Gann CV Trading
  getStrategy1TradeStatus: () => request('/strategy1-trade/status'),
  strategy1TradeStart: (config) =>
    request('/strategy1-trade/start', { method: 'POST', body: JSON.stringify(config) }),
  strategy1TradeStop: () => request('/strategy1-trade/stop', { method: 'POST' }),
  strategy1TradeCheck: () => request('/strategy1-trade/check', { method: 'POST' }),
  strategy1TradeUpdateConfig: (config) =>
    request('/strategy1-trade/config', { method: 'PUT', body: JSON.stringify(config) }),
  strategy1TradeBacktest: (config) =>
    request('/strategy1-trade/backtest', { method: 'POST', body: JSON.stringify(config) }),
  strategy1TradeBacktestLatest: () =>
    request('/strategy1-trade/backtest/latest'),
  strategy1TradeHistory: () =>
    request('/strategy1-trade/history'),

  // Strategy 2 — Option Selling (Gann CV)
  getStrategy2TradeStatus: () => request('/strategy2-trade/status'),
  strategy2TradeStart: (config) =>
    request('/strategy2-trade/start', { method: 'POST', body: JSON.stringify(config) }),
  strategy2TradeStop: () => request('/strategy2-trade/stop', { method: 'POST' }),
  strategy2TradeCheck: () => request('/strategy2-trade/check', { method: 'POST' }),
  strategy2TradeUpdateConfig: (config) =>
    request('/strategy2-trade/config', { method: 'PUT', body: JSON.stringify(config) }),
  strategy2TradeHistory: () =>
    request('/strategy2-trade/history'),

  // Strategy 3 — CV + VWAP + EMA200 + ADX
  getStrategy3TradeStatus: () => request('/strategy3-trade/status'),
  strategy3TradeStart: (config) =>
    request('/strategy3-trade/start', { method: 'POST', body: JSON.stringify(config) }),
  strategy3TradeStop: () => request('/strategy3-trade/stop', { method: 'POST' }),
  strategy3TradeCheck: () => request('/strategy3-trade/check', { method: 'POST' }),
  strategy3TradeUpdateConfig: (config) =>
    request('/strategy3-trade/config', { method: 'PUT', body: JSON.stringify(config) }),
  strategy3TradeHistory: () =>
    request('/strategy3-trade/history'),

  // Settings
  getSettings: () => request('/settings/'),
  updateSettings: (data) =>
    request('/settings/', { method: 'PUT', body: JSON.stringify(data) }),
};

export function useWebSocket(onMessage) {
  const wsRef = useRef(null);
  const cbRef = useRef(onMessage);
  cbRef.current = onMessage;

  const reconnect = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState <= 1) return; // already open/connecting
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws`);
    wsRef.current = ws;

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        cbRef.current(msg);
      } catch {}
    };

    ws.onclose = () => {
      setTimeout(reconnect, 3000);
    };
  }, []);

  useEffect(() => {
    reconnect();
    return () => {
      if (wsRef.current) wsRef.current.close();
    };
  }, [reconnect]);

  return wsRef;
}
