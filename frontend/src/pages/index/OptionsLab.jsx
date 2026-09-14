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
        <div className="card space-y-2 max-w-3xl">
          <h2 className="text-sm font-semibold text-gray-100">No live or paper engine is armed from the lab</h2>
          <p className="text-[13px] text-gray-400">
            Three years of real option prices were tested for both buying and selling. No setup survived the sealed
            holdout, realistic costs and margin, and the data-window checks — so there is nothing here that has earned
            a place in live trading, and placing orders from an unvalidated rule is exactly how losses happen.
          </p>
          <p className="text-[13px] text-gray-400">
            Use the Backtest tab to test your own ideas on real prices. If a rule clears t &gt; 2, holds after the
            holdout date, and does not depend on unobserved legs, that is the point to build a paper engine for it.
            The How it works tab has the full record of what was tried.
          </p>
        </div>
      )}
    </div>
  );
}
