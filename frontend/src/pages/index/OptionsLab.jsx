import React, { useState } from 'react';
import { Database, FlaskConical, Wallet, Info } from 'lucide-react';
import MarketStorePanel from '../../components/MarketStorePanel';
import OptionsLabBacktest from '../../components/OptionsLabBacktest';
import OptionsLabInfo from '../../components/OptionsLabInfo';

/**
 * Options Lab — research and backtesting on REAL option prices.
 *
 * Everything here reads from the Market Store (actual traded contracts, never a
 * rolling ATM series and never a modelled premium). Data is the foundation, so it
 * is the first tab.
 */
const TABS = [
  ['data', 'Data', Database],
  ['backtest', 'Backtest', FlaskConical],
  ['live', 'Live / Paper', Wallet],
  ['info', 'How it works', Info],
];

export default function OptionsLab() {
  const [tab, setTab] = useState('data');

  return (
    <div className="p-4 sm:p-6 space-y-4 max-w-[1400px] mx-auto">
      <div>
        <h1 className="text-xl sm:text-2xl font-bold text-white">Options Lab</h1>
        <p className="text-xs sm:text-sm text-gray-500 mt-0.5">
          Backtests on actual traded option contracts from your Market Store
        </p>
      </div>

      <div className="flex gap-1 border-b border-surface-3 overflow-x-auto">
        {TABS.map(([id, label, Icon]) => (
          <button key={id} onClick={() => setTab(id)}
            className={`flex items-center gap-1.5 px-4 py-2 text-sm font-semibold border-b-2 -mb-px transition whitespace-nowrap ${
              tab === id ? 'border-brand-500 text-brand-400' : 'border-transparent text-gray-400 hover:text-gray-200'}`}>
            <Icon className="w-4 h-4" />{label}
          </button>
        ))}
      </div>

      {tab === 'data' && <MarketStorePanel />}
      {tab === 'backtest' && <OptionsLabBacktest />}
      {tab === 'info' && <OptionsLabInfo />}
      {tab === 'live' && (
        <div className="card space-y-3 max-w-3xl">
          <h2 className="text-sm font-semibold text-gray-100">Nothing trades from this page yet — on purpose</h2>
          <p className="text-[13px] text-gray-300">
            This tab only arms a strategy that has passed its tests. None has so far, so no live or paper orders are
            placed from here. Your other strategies (Strategies page, VWAP Options Engine, NIFTY Open ±50, Index
            Straddle) are not affected and run exactly as before.
          </p>
          <div className="text-[12.5px] text-gray-400 space-y-1">
            <div className="font-semibold text-gray-300">What a strategy must show in the Backtest tab first</div>
            <div>1. Profit over years, not days — a few days can look great by luck (t-stat above 2).</div>
            <div>2. Still profitable in the “From 2025-12-01” row, the period it was never tuned on.</div>
            <div>3. Profit not coming from 2–3 lucky trades, and no legs priced without a real trade.</div>
          </div>
          <p className="text-[12.5px] text-gray-400">
            The strongest candidate from the machine-learning research (a model picking option buys using India VIX,
            first-hour option flow and previous-day/week/month levels) made money on its unseen period but has not yet
            cleared rule 1. The next step for it is a paper-only engine that logs every signal so it can be judged on
            real forward results before any money is used.
          </p>
        </div>
      )}
    </div>
  );
}
