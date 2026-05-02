import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import {
  TrendingUp,
  Play,
  Pause,
  Settings,
  Plus,
  ChevronDown,
  ChevronUp,
  Cpu,
  Zap,
  ExternalLink,
} from 'lucide-react';

// Map strategy names to their dedicated pages
const STRATEGY_PAGES = {
  strategy1_gann_cv: '/strategy1-trade',
  strategy2_option_sell: '/strategy2-trade',
  strategy3_cv_vwap_ema_adx: '/strategy3-trade',
  strategy4_high_low_retest: '/strategy4-trade',
  strategy5_gann_range: '/strategy5-trade',
};

export default function Strategies() {
  const [data, setData] = useState({ strategies: [], active: [] });
  const [expanded, setExpanded] = useState(null);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  const fetchStrategies = async () => {
    try {
      const res = await api.getStrategies();
      setData(res);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchStrategies(); }, []);

  const toggleActive = async (name, isActive) => {
    if (isActive) {
      await api.deactivateStrategy(name);
    } else {
      await api.activateStrategy(name);
    }
    fetchStrategies();
  };

  return (
    <div className="p-4 sm:p-6 space-y-4 sm:space-y-6 max-w-[1400px] mx-auto">
      <div>
        <h1 className="text-xl sm:text-2xl font-bold text-white">Strategies</h1>
        <p className="text-xs sm:text-sm text-gray-500 mt-0.5">Manage your trading strategies</p>
      </div>

      {data.strategies.length === 0 ? (
        <div className="card text-center py-16">
          <div className="w-16 h-16 mx-auto rounded-2xl bg-surface-3 flex items-center justify-center mb-4">
            <Cpu className="w-8 h-8 text-gray-600" />
          </div>
          <h3 className="text-lg font-semibold text-white mb-2">No Strategies Yet</h3>
          <p className="text-gray-500 text-sm max-w-md mx-auto">
            Create a strategy file in <code className="text-brand-400 bg-surface-3 px-1.5 py-0.5 rounded text-xs">strategies/</code> folder
            and register it in <code className="text-brand-400 bg-surface-3 px-1.5 py-0.5 rounded text-xs">registry.py</code>
          </p>
          <div className="mt-6 p-4 rounded-lg bg-surface-2 text-left max-w-lg mx-auto">
            <p className="text-xs text-gray-400 mb-2">Quick start:</p>
            <pre className="text-xs text-gray-300 mono overflow-x-auto">
{`# 1. Create strategies/my_strategy.py
# 2. Add to strategies/registry.py:
from strategies.my_strategy import MyStrategy
STRATEGY_MAP["my_strategy"] = MyStrategy

# 3. Restart the server`}
            </pre>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          {data.strategies.map((s) => (
            <div key={s.name} className="card-hover">
              <div
                className="flex flex-col sm:flex-row sm:items-center gap-3 cursor-pointer"
                onClick={() => setExpanded(expanded === s.name ? null : s.name)}
              >
                <div className="flex items-center gap-3 flex-1 min-w-0">
                  <div className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${
                    s.active ? 'bg-green-500/15' : 'bg-surface-3'
                  }`}>
                    <Zap className={`w-5 h-5 ${s.active ? 'text-green-400' : 'text-gray-500'}`} />
                  </div>
                  <div className="min-w-0">
                    <h3 className="font-semibold text-white text-sm sm:text-base truncate">{s.name}</h3>
                    <p className="text-xs text-gray-500">
                      {s.config?.instruments?.length || 0} instruments &bull;{' '}
                      ₹{(s.config?.capital || 100000).toLocaleString('en-IN')} capital
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-2 sm:gap-3 ml-[52px] sm:ml-0 flex-wrap">
                  {STRATEGY_PAGES[s.name] && (
                    <button
                      onClick={(e) => { e.stopPropagation(); navigate(STRATEGY_PAGES[s.name]); }}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-brand-600/15 text-brand-400 border border-brand-500/20 hover:bg-brand-600/25 transition-all"
                    >
                      <ExternalLink className="w-3 h-3" />
                      Open
                    </button>
                  )}
                  <button
                    onClick={(e) => { e.stopPropagation(); toggleActive(s.name, s.active); }}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                      s.active
                        ? 'bg-green-500/15 text-green-400 border border-green-500/20 hover:bg-green-500/25'
                        : 'bg-surface-3 text-gray-400 border border-surface-4 hover:text-white'
                    }`}
                  >
                    {s.active ? <Play className="w-3 h-3" /> : <Pause className="w-3 h-3" />}
                    {s.active ? 'Active' : 'Inactive'}
                  </button>
                  {expanded === s.name ? (
                    <ChevronUp className="w-4 h-4 text-gray-500" />
                  ) : (
                    <ChevronDown className="w-4 h-4 text-gray-500" />
                  )}
                </div>
              </div>

              {expanded === s.name && (
                <div className="mt-4 pt-4 border-t border-surface-3">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                      <h4 className="text-xs text-gray-500 uppercase tracking-wide mb-2">Instruments</h4>
                      <div className="flex flex-wrap gap-1.5">
                        {(s.config?.instruments || []).length > 0 ? (
                          s.config.instruments.map((inst) => (
                            <span key={inst} className="badge-blue">{inst}</span>
                          ))
                        ) : (
                          <span className="text-xs text-gray-600">No instruments configured</span>
                        )}
                      </div>
                    </div>
                    <div>
                      <h4 className="text-xs text-gray-500 uppercase tracking-wide mb-2">Parameters</h4>
                      {s.config?.params && Object.keys(s.config.params).length > 0 ? (
                        <div className="space-y-1">
                          {Object.entries(s.config.params).map(([k, v]) => (
                            <div key={k} className="flex items-center gap-2 text-xs">
                              <span className="text-gray-400">{k}:</span>
                              <span className="mono text-gray-300">{JSON.stringify(v)}</span>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <span className="text-xs text-gray-600">No custom parameters</span>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
