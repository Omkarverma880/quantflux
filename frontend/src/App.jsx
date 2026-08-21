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
import Strategy7 from './pages/Strategy7';
import Strategy8 from './pages/Strategy8';
import Strategy9 from './pages/Strategy9';
import Strategy10 from './pages/Strategy10';
import PortfolioAnalytics from './pages/PortfolioAnalytics';
import AnalyticsWorld from './pages/AnalyticsWorld';
import TradeHistory from './pages/TradeHistory';
import ManualTrading from './pages/ManualTrading';
import Strategy11 from './pages/Strategy11';
import Strategy12 from './pages/Strategy12';
import VwapPvwapResearch from './pages/research/VwapPvwap';
import OptionChainResearch from './pages/research/OptionChain';
import HlVwapLab from './pages/research/HlVwapLab';
import Sentiment from './pages/research/Sentiment';
import NiftySentiment from './pages/research/NiftySentiment';
import MarketDashboard from './pages/research/MarketDashboard';
import NiftySignalGenerator from './pages/research/NiftySignalGenerator';
import PMVwapStraddle from './pages/research/PMVwapStraddle';
import PMVwapEquity from './pages/research/PMVwapEquity';
import OPEI from './pages/research/OPEI';
import QMIE from './pages/research/QMIE';
import DataDownloader from './pages/research/DataDownloader';
import DemandSupply from './pages/research/DemandSupply';
import QMRE from './pages/research/QMRE';
import MarketHub from './pages/research/MarketHub';
import FourthCandle from './pages/equity/FourthCandle';
import FourthCandleEquity from './pages/equity/FourthCandleEquity';
import EquityPMVwapHolding from './pages/equity/PMVwapHolding';

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
          <Route path="strategy7-trade" element={<Strategy7 />} />
          <Route path="strategy8-trade" element={<Strategy8 />} />
          <Route path="strategy9-trade" element={<Strategy9 />} />
          <Route path="strategy10-trade" element={<Strategy10 />} />
          <Route path="strategy11-trade" element={<Strategy11 />} />
          <Route path="strategy12-trade" element={<Strategy12 />} />
          <Route path="research/vwap-pvwap" element={<VwapPvwapResearch />} />
          <Route path="research/option-chain" element={<OptionChainResearch />} />
          <Route path="research/hl-vwap" element={<HlVwapLab />} />
          <Route path="research/sentiment" element={<Sentiment />} />
          <Route path="research/nifty-sentiment" element={<NiftySentiment />} />
          <Route path="research/nifty-signal-generator" element={<NiftySignalGenerator />} />
          <Route path="research/pmvwap-straddle" element={<PMVwapStraddle />} />
          <Route path="research/pmvwap-equity" element={<PMVwapEquity />} />
          <Route path="research/opei" element={<OPEI />} />
          <Route path="research/qmie" element={<QMIE />} />
          <Route path="research/data-downloader" element={<DataDownloader />} />
          <Route path="research/demand-supply" element={<DemandSupply />} />
          <Route path="research/qmre" element={<QMRE />} />
          <Route path="research/market-hub" element={<MarketHub />} />
          <Route path="equity-strategy/pmvwap-holding" element={<EquityPMVwapHolding />} />
          <Route path="equity-strategy/fourth-candle" element={<FourthCandle />} />
          <Route path="equity-strategy/fourth-candle-cash" element={<FourthCandleEquity />} />
          <Route path="research/market-dashboard" element={<MarketDashboard />} />
          <Route path="portfolio" element={<PortfolioAnalytics />} />
          <Route path="portfolio/analytics-world" element={<AnalyticsWorld />} />
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
