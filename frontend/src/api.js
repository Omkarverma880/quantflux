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

  // Strategy 4 — Previous-Day First-Hour High/Low Retest
  getStrategy4TradeStatus: () => request('/strategy4-trade/status'),
  getStrategy4Levels: () => request('/strategy4-trade/levels'),
  getStrategy4Intraday: () => request('/strategy4-trade/intraday'),
  strategy4TradeStart: (config) =>
    request('/strategy4-trade/start', { method: 'POST', body: JSON.stringify(config) }),
  strategy4TradeStop: () => request('/strategy4-trade/stop', { method: 'POST' }),
  strategy4TradeCheck: () => request('/strategy4-trade/check', { method: 'POST' }),
  strategy4TradeUpdateConfig: (config) =>
    request('/strategy4-trade/config', { method: 'PUT', body: JSON.stringify(config) }),
  strategy4TradeHistory: () =>
    request('/strategy4-trade/history'),
  strategy4TradeBacktest: (date) =>
    request('/strategy4-trade/backtest', {
      method: 'POST',
      body: JSON.stringify(date ? { date } : {}),
    }),
  strategy4TradeBacktestMulti: (days) =>
    request('/strategy4-trade/backtest-multi', {
      method: 'POST',
      body: JSON.stringify({ days: days || 30 }),
    }),

  // Strategy 5 — Dynamic Gann Level Range Retest
  getStrategy5TradeStatus: () => request('/strategy5-trade/status'),
  getStrategy5Levels: () => request('/strategy5-trade/levels'),
  getStrategy5Intraday: () => request('/strategy5-trade/intraday'),
  strategy5TradeStart: (config) =>
    request('/strategy5-trade/start', { method: 'POST', body: JSON.stringify(config) }),
  strategy5TradeStop: () => request('/strategy5-trade/stop', { method: 'POST' }),
  strategy5TradeCheck: () => request('/strategy5-trade/check', { method: 'POST' }),
  strategy5TradeUpdateConfig: (config) =>
    request('/strategy5-trade/config', { method: 'PUT', body: JSON.stringify(config) }),
  strategy5TradeHistory: () =>
    request('/strategy5-trade/history'),
  strategy5TradeBacktest: (date) =>
    request('/strategy5-trade/backtest', {
      method: 'POST',
      body: JSON.stringify(date ? { date } : {}),
    }),
  strategy5TradeBacktestMulti: (days) =>
    request('/strategy5-trade/backtest-multi', {
      method: 'POST',
      body: JSON.stringify({ days: days || 30 }),
    }),

  // Strategy 6 — Manual CALL / PUT Line Touch Entry
  getStrategy6TradeStatus: () => request('/strategy6-trade/status'),
  getStrategy6Intraday: () => request('/strategy6-trade/intraday'),
  strategy6TradeStart: (config) =>
    request('/strategy6-trade/start', { method: 'POST', body: JSON.stringify(config) }),
  strategy6TradeStop: () => request('/strategy6-trade/stop', { method: 'POST' }),
  strategy6TradeCheck: () => request('/strategy6-trade/check', { method: 'POST' }),
  strategy6TradeUpdateConfig: (config) =>
    request('/strategy6-trade/config', { method: 'PUT', body: JSON.stringify(config) }),
  strategy6UpdateLines: (lines) =>
    request('/strategy6-trade/lines', { method: 'POST', body: JSON.stringify(lines) }),
  strategy6TradeHistory: () =>
    request('/strategy6-trade/history'),

  // Portfolio Analytics (independent module — holdings/watchlist/research)
  getPortfolioHoldings: () => request('/portfolio/holdings'),
  getPortfolioWatchlists: () => request('/portfolio/watchlists'),
  createWatchlist: (name) =>
    request('/portfolio/watchlists', { method: 'POST', body: JSON.stringify({ name }) }),
  deleteWatchlist: (id) =>
    request(`/portfolio/watchlists/${id}`, { method: 'DELETE' }),
  addWatchlistItem: (id, item) =>
    request(`/portfolio/watchlists/${id}/items`, { method: 'POST', body: JSON.stringify(item) }),
  deleteWatchlistItem: (itemId) =>
    request(`/portfolio/watchlists/items/${itemId}`, { method: 'DELETE' }),
  getResearchEntries: () => request('/portfolio/research'),
  createResearchEntry: (body) =>
    request('/portfolio/research', { method: 'POST', body: JSON.stringify(body) }),
  updateResearchEntry: (id, body) =>
    request(`/portfolio/research/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteResearchEntry: (id) =>
    request(`/portfolio/research/${id}`, { method: 'DELETE' }),
  getHoldingExitLevels: () => request('/portfolio/holdings/exit-levels'),
  upsertHoldingExitLevel: (body) =>
    request('/portfolio/holdings/exit-levels', { method: 'PUT', body: JSON.stringify(body) }),
  deleteHoldingExitLevel: (symbol, exchange = 'NSE') =>
    request(`/portfolio/holdings/exit-levels/${encodeURIComponent(symbol)}?exchange=${encodeURIComponent(exchange)}`, { method: 'DELETE' }),
  getPortfolioQuote: (symbol, exchange = 'NSE') =>
    request(`/portfolio/quote?symbol=${encodeURIComponent(symbol)}&exchange=${encodeURIComponent(exchange)}`),

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
