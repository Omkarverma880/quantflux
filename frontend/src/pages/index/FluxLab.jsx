import React, { useEffect, useState } from 'react';
import { FlaskConical, Loader2, Radio, BarChart3, BookOpen } from 'lucide-react';
import { api } from '../../api';
import Paper from '../../components/fluxlab/Paper';
import Backtest from '../../components/fluxlab/Backtest';
import { Section, Note } from '../../components/fluxlab/ui';

/**
 * Flux Strategy Test Lab — one frozen strategy: sell a hedged iron fly after a failed
 * opening-range breakout. Backtest it on the stored history, paper-trade it on live data.
 * No order is ever placed from this page.
 */

const TABS = [
  ['paper', 'Paper trading', Radio],
  ['backtest', 'Backtest', BarChart3],
  ['rule', 'The rule', BookOpen],
];

const EVIDENCE = [
  ['Tested', '48 fade setups (3 level types × 2 break sizes × 2 time windows × 4 trade types) on 739 NIFTY sessions, Sep 2023 – Sep 2026, real option prices and charges.'],
  ['Buying the fade', 'Lost money in every version — breakout fades traded directionally do not pay after costs.'],
  ['Credit spreads', 'Lost money in every version.'],
  ['Iron fly after a failed breakout', 'Made money in all 12 versions (₹1.19–1.59 won per ₹1 lost). Opening-range and first-5-minute levels worked; previous-day levels barely.'],
  ['This rule', 'Opening range · 15-pt break · back inside within 15 min: 357 trades, +₹1.09 lakh per lot, 23 of 37 months green (62%). Last 12 months: 10 of 12 green.'],
  ['Honest caveats', 'It was the best of the 12 in hindsight, so its own numbers are flattered. Picking settings month by month from past data only gave ~60% green months. Paper trading forward is the real test.'],
];

export default function FluxLab() {
  const [tab, setTab] = useState('paper');
  const [meta, setMeta] = useState(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    api.flMeta().then((m) => (m.status === 'ok' ? setMeta(m) : setErr(m.message || 'could not load the lab')))
      .catch((e) => setErr(String(e.message || e)));
  }, []);

  if (!meta) {
    return (
      <div className="p-6 text-center text-gray-500 text-sm">
        {err ? <span className="text-red-400">{err}</span> : <><Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" />Loading the lab…</>}
      </div>
    );
  }

  return (
    <div className="p-4 sm:p-6 space-y-4 max-w-[1600px] mx-auto">
      <div>
        <h1 className="text-xl sm:text-2xl font-bold text-white flex items-center gap-2">
          <FlaskConical className="w-5 h-5 text-brand-400" />Flux Strategy Test Lab
        </h1>
        <p className="text-xs sm:text-sm text-gray-500 mt-0.5">
          {meta.strategy} — backtested on stored NIFTY history, paper-traded on live data. Paper only: no orders are placed.
        </p>
      </div>

      <div className="flex gap-1 border-b border-surface-3 overflow-x-auto">
        {TABS.map(([id, label, Icon]) => (
          <button key={id} onClick={() => setTab(id)}
            className={`flex items-center gap-1.5 px-3.5 py-2 text-sm font-semibold border-b-2 -mb-px transition whitespace-nowrap ${tab === id
              ? 'border-brand-500 text-brand-400' : 'border-transparent text-gray-400 hover:text-gray-200'}`}>
            <Icon className="w-4 h-4" />{label}
          </button>
        ))}
      </div>

      {tab === 'paper' && <Paper />}
      {tab === 'backtest' && <Backtest meta={meta} />}
      {tab === 'rule' && (
        <div className="grid lg:grid-cols-2 gap-4">
          <Section title="The rule (frozen)">
            <ol className="space-y-2 list-decimal pl-4">
              {(meta.rules || []).map((r, i) => <li key={i} className="text-[13px] text-gray-200">{r}</li>)}
            </ol>
            <div className="mt-3">
              <Note>
                The rule is fixed on purpose. Changing it after looking at results is how backtests start lying —
                any change should be re-tested and paper-traded again before it is trusted.
              </Note>
            </div>
          </Section>
          <Section title="Why this rule — the research behind it">
            <div className="space-y-2">
              {EVIDENCE.map(([k, v]) => (
                <div key={k}>
                  <div className="text-[11px] uppercase tracking-wider text-gray-500">{k}</div>
                  <div className="text-[12.5px] text-gray-300">{v}</div>
                </div>
              ))}
            </div>
          </Section>
        </div>
      )}
    </div>
  );
}
