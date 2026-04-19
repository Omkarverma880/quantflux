import React, { useState, useEffect, useRef, useCallback } from 'react';
import { api } from '../api';
import {
  TrendingUp,
  TrendingDown,
  Minus,
  RefreshCw,
  BarChart3,
  Activity,
  Download,
  Settings2,
} from 'lucide-react';

const REFRESH_MS = 60_000; // 1 minute

export default function CumulativeVolume() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [countdown, setCountdown] = useState(60);
  const [configOpen, setConfigOpen] = useState(false);
  const [config, setConfig] = useState({
    futures_instrument: '',
    futures_token: 0,
    spot_instrument: '',
    threshold: 50000,
  });
  const timerRef = useRef(null);
  const countdownRef = useRef(null);

  const fetchData = useCallback(async () => {
    try {
      setLoading(true);
      const result = await api.getCumulativeVolumeData();
      setData(result);
      setError(result.error || null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
      setCountdown(60);
    }
  }, []);

  // Initial load + auto-refresh
  useEffect(() => {
    fetchData();
    timerRef.current = setInterval(fetchData, REFRESH_MS);
    countdownRef.current = setInterval(() => setCountdown((c) => Math.max(0, c - 1)), 1000);
    const onConnected = () => fetchData();
    const onDisconnected = () => { setData(null); fetchData(); };
    window.addEventListener('zerodha_connected', onConnected);
    window.addEventListener('zerodha_disconnected', onDisconnected);
    return () => {
      clearInterval(timerRef.current);
      clearInterval(countdownRef.current);
      window.removeEventListener('zerodha_connected', onConnected);
      window.removeEventListener('zerodha_disconnected', onDisconnected);
    };
  }, [fetchData]);

  // Load config when panel opens
  useEffect(() => {
    if (configOpen) {
      api.getCumulativeVolumeConfig().then((c) => {
        if (c.params) setConfig(c.params);
      }).catch(() => {});
    }
  }, [configOpen]);

  const saveConfig = async () => {
    await api.updateCumulativeVolumeConfig({
      instruments: [config.futures_instrument],
      capital: 0,
      max_positions: 0,
      params: config,
    });
    setConfigOpen(false);
    fetchData();
  };

  const downloadCSV = useCallback(() => {
    if (!data?.rows?.length) return;
    const headers = ['Time', 'Open', 'Close', 'Raw Volume', 'Signed Volume', 'Cumulative Volume', 'Spot Price', 'Trend Bias'];
    const csvRows = [headers.join(',')];
    for (const r of data.rows) {
      csvRows.push([
        r.time,
        r.open,
        r.close,
        r.raw_volume,
        r.signed_volume,
        r.cumulative_volume,
        r.spot_price,
        r.highlight || '',
      ].join(','));
    }
    const blob = new Blob([csvRows.join('\n')], { type: 'text/csv;charset=utf-8;' });
    const symbol = (data.symbol || 'CumVol').replace(/[^a-zA-Z0-9_-]/g, '_');
    const dateStr = data.data_date || new Date().toISOString().slice(0, 10);
    const filename = `CumVol_${symbol}_${dateStr}.csv`;
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    link.click();
    URL.revokeObjectURL(link.href);
  }, [data]);

  const biasColor =
    data?.trend_bias === 'Bullish'
      ? 'text-green-400'
      : data?.trend_bias === 'Bearish'
      ? 'text-red-400'
      : 'text-gray-400';

  const BiasIcon =
    data?.trend_bias === 'Bullish'
      ? TrendingUp
      : data?.trend_bias === 'Bearish'
      ? TrendingDown
      : Minus;

  return (
    <div className="p-6 space-y-6 max-w-[1400px] mx-auto">
      {/* ── Header ──────────────────────────────── */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <BarChart3 className="w-6 h-6 text-brand-400" />
            Cumulative Volume Data
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            {data?.symbol || '—'} &middot; Spot: {data?.spot_instrument || '—'}
            {data?.data_date && (
              <span className="ml-2 px-2 py-0.5 rounded text-[10px] font-semibold bg-surface-3 text-gray-300">
                Data: {data.data_date}
              </span>
            )}
            {data?.as_of && (
              <span className="ml-2 px-2 py-0.5 rounded text-[10px] font-medium bg-surface-3 text-gray-400">
                As of: {data.as_of}
              </span>
            )}
            {data?.is_demo && (
              <span className="ml-2 px-2 py-0.5 rounded text-[10px] font-bold uppercase bg-amber-500/15 text-amber-400 border border-amber-500/30">
                Demo — Login for live data
              </span>
            )}
          </p>
        </div>

        <div className="flex items-center gap-3">
          {/* Trend bias badge */}
          {data && (
            <div
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-semibold ${
                data.trend_bias === 'Bullish'
                  ? 'bg-green-500/15 text-green-400 border border-green-500/30'
                  : data.trend_bias === 'Bearish'
                  ? 'bg-red-500/15 text-red-400 border border-red-500/30'
                  : 'bg-gray-500/15 text-gray-400 border border-gray-500/30'
              }`}
            >
              <BiasIcon className="w-4 h-4" />
              {data.trend_bias}
            </div>
          )}

          <button
            onClick={downloadCSV}
            disabled={!data?.rows?.length}
            className="flex items-center gap-2 px-3 py-2 bg-surface-2 text-gray-300 hover:text-white rounded-lg border border-surface-3 text-sm transition disabled:opacity-40 disabled:cursor-not-allowed"
            title="Download as CSV"
          >
            <Download className="w-4 h-4" />
            CSV
          </button>

          <button
            onClick={() => setConfigOpen(!configOpen)}
            className="p-2 rounded-lg bg-surface-2 text-gray-400 hover:text-white border border-surface-3 transition"
            title="Settings"
          >
            <Settings2 className="w-4 h-4" />
          </button>

          <button
            onClick={fetchData}
            disabled={loading}
            className="flex items-center gap-2 px-3 py-2 bg-surface-2 text-gray-300 hover:text-white rounded-lg border border-surface-3 text-sm transition disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            {countdown}s
          </button>
        </div>
      </div>

      {/* ── Config Panel ───────────────────────── */}
      {configOpen && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-5 space-y-4">
          <h3 className="text-sm font-semibold text-white">Configuration</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Futures Instrument</label>
              <input
                className="w-full px-3 py-2 rounded-lg bg-surface-1 border border-surface-3 text-white text-sm"
                value={config.futures_instrument}
                onChange={(e) => setConfig({ ...config, futures_instrument: e.target.value })}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Futures Token (int)</label>
              <input
                type="number"
                className="w-full px-3 py-2 rounded-lg bg-surface-1 border border-surface-3 text-white text-sm"
                value={config.futures_token}
                onChange={(e) => setConfig({ ...config, futures_token: Number(e.target.value) })}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Spot Instrument</label>
              <input
                className="w-full px-3 py-2 rounded-lg bg-surface-1 border border-surface-3 text-white text-sm"
                value={config.spot_instrument}
                onChange={(e) => setConfig({ ...config, spot_instrument: e.target.value })}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Threshold</label>
              <input
                type="number"
                className="w-full px-3 py-2 rounded-lg bg-surface-1 border border-surface-3 text-white text-sm"
                value={config.threshold}
                onChange={(e) => setConfig({ ...config, threshold: Number(e.target.value) })}
              />
            </div>
          </div>
          <div className="flex gap-2 pt-1">
            <button
              onClick={saveConfig}
              className="px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded-lg transition"
            >
              Save &amp; Reload
            </button>
            <button
              onClick={() => setConfigOpen(false)}
              className="px-4 py-2 bg-surface-3 text-gray-300 text-sm rounded-lg hover:text-white transition"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* ── Summary Cards ──────────────────────── */}
      {data && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <SummaryCard label="Spot Price" value={data.spot_price?.toLocaleString('en-IN') || '—'} icon={Activity} />
          <SummaryCard
            label="Cumulative Vol"
            value={data.last_cumulative_volume?.toLocaleString('en-IN') || '0'}
            icon={BarChart3}
            color={
              data.last_cumulative_volume > data.threshold
                ? 'text-green-400'
                : data.last_cumulative_volume < -data.threshold
                ? 'text-red-400'
                : 'text-gray-300'
            }
          />
          <SummaryCard label="Candles" value={data.candle_count} icon={BarChart3} />
          <SummaryCard
            label="Trend Bias"
            value={data.trend_bias}
            icon={BiasIcon}
            color={biasColor}
          />
        </div>
      )}

      {/* ── Error ──────────────────────────────── */}
      {error && (
        <div className="bg-red-500/10 border border-red-500/30 text-red-400 text-sm rounded-xl px-4 py-3">
          {error}
        </div>
      )}

      {/* ── Data Table ─────────────────────────── */}
      <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
        <div className="overflow-x-auto max-h-[65vh] overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10">
              <tr className="bg-surface-1 text-gray-400 text-xs uppercase tracking-wider">
                <th className="px-4 py-3 text-left">Time</th>
                <th className="px-4 py-3 text-right">Open</th>
                <th className="px-4 py-3 text-right">Close</th>
                <th className="px-4 py-3 text-right">Raw Vol</th>
                <th className="px-4 py-3 text-right">Signed Vol</th>
                <th className="px-4 py-3 text-right font-semibold">Cum. Vol</th>
                <th className="px-4 py-3 text-right">Spot Price</th>
              </tr>
            </thead>
            <tbody>
              {data?.rows?.length > 0 ? (
                [...data.rows].reverse().map((r, i) => (
                  <tr
                    key={i}
                    className={`border-t border-surface-3 transition-colors ${rowBg(r.highlight)}`}
                  >
                    <td className="px-4 py-2 font-mono text-gray-300">{r.time}</td>
                    <td className="px-4 py-2 text-right text-gray-300">{r.open.toLocaleString('en-IN')}</td>
                    <td className="px-4 py-2 text-right text-gray-300">{r.close.toLocaleString('en-IN')}</td>
                    <td className="px-4 py-2 text-right text-gray-300">{r.raw_volume.toLocaleString('en-IN')}</td>
                    <td className={`px-4 py-2 text-right font-medium ${r.signed_volume > 0 ? 'text-green-400' : r.signed_volume < 0 ? 'text-red-400' : 'text-gray-500'}`}>
                      {r.signed_volume.toLocaleString('en-IN')}
                    </td>
                    <td className={`px-4 py-2 text-right font-bold ${cumVolColor(r.highlight)}`}>
                      {r.cumulative_volume.toLocaleString('en-IN')}
                    </td>
                    <td className="px-4 py-2 text-right text-gray-400">{r.spot_price.toLocaleString('en-IN')}</td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={7} className="px-4 py-12 text-center text-gray-500">
                    {loading ? 'Loading…' : 'No data available. Market may be closed or instrument not configured.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ── Helper Components ── */

function SummaryCard({ label, value, icon: Icon, color = 'text-white' }) {
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
      <div className="flex items-center gap-2 text-gray-500 text-xs mb-1">
        <Icon className="w-3.5 h-3.5" />
        {label}
      </div>
      <p className={`text-lg font-bold ${color}`}>{value}</p>
    </div>
  );
}

function rowBg(highlight) {
  if (highlight === 'green') return 'bg-green-500/8';
  if (highlight === 'red') return 'bg-red-500/8';
  return '';
}

function cumVolColor(highlight) {
  if (highlight === 'green') return 'text-green-400';
  if (highlight === 'red') return 'text-red-400';
  return 'text-gray-300';
}
