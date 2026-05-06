import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { useAuth } from './AuthContext';
import { ErrorBoundary } from './components/ErrorBoundary';
import Layout from './components/Layout';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import Strategies from './pages/Strategies';
import Orders from './pages/Orders';
import Settings from './pages/Settings';
import CumulativeVolume from './pages/CumulativeVolume';
import Strategy1 from './pages/Strategy1';
import Strategy2 from './pages/Strategy2';
import Strategy3 from './pages/Strategy3';
import Strategy4 from './pages/Strategy4';
import Strategy5 from './pages/Strategy5';
import Strategy6 from './pages/Strategy6';
import TradeHistory from './pages/TradeHistory';
import ManualTrading from './pages/ManualTrading';

export default function App() {
  const { isAuthenticated } = useAuth();

  if (!isAuthenticated) {
    return (
      <ErrorBoundary>
        <Login />
      </ErrorBoundary>
    );
  }

  return (
    <ErrorBoundary>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="strategy1" element={<CumulativeVolume />} />
          <Route path="strategy1-trade" element={<Strategy1 />} />
          <Route path="strategy2-trade" element={<Strategy2 />} />
          <Route path="strategy3-trade" element={<Strategy3 />} />
          <Route path="strategy4-trade" element={<Strategy4 />} />
          <Route path="strategy5-trade" element={<Strategy5 />} />
          <Route path="strategy6-trade" element={<Strategy6 />} />
          <Route path="strategies" element={<Strategies />} />
          <Route path="orders" element={<Orders />} />
          <Route path="settings" element={<Settings />} />
          <Route path="history" element={<TradeHistory />} />
          <Route path="manual-trading" element={<ManualTrading />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </ErrorBoundary>
  );
}
