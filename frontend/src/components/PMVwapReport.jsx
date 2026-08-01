import React, { useState, useEffect, useCallback } from 'react';
import { Loader2, BarChart3, Download, GitCompare, RefreshCw } from 'lucide-react';
import { api } from '../api';

// Reusable Research Summary Report for the Prev-Month-VWAP modules.
// `kind` = 'straddle' | 'equity' selects the right API endpoints + MTM label.
const API = {
  straddle: { runs: api.researchPMVwapStraddleRuns, report: api.researchPMVwapStraddleReport, compare: api.researchPMVwapStraddleCompare },
  equity: { runs: api.researchPMVwapEquityRuns, report: api.researchPMVwapEquityReport, compare: api.researchPMVwapEquityCompare },
};

const NUM = (v, d = 2) => (v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
const pos = (v) => (v >= 0 ? 'text-emerald-400' : 'text-red-400');

function Stat({ label, value, tone }) {
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl px-3 py-2">
      <div className="text-[10px] text-gray-500 uppercase tracking-wide">{label}</div>
      <div className={`text-lg font-bold ${tone === 'up' ? 'text-emerald-400' : tone === 'down' ? 'text-red-400' : 'text-gray-100'}`}>{value}</div>
    </div>
  );
}

function RankTable({ title, items, keyLabel }) {
  if (!items?.length) return null;
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
      <div className="px-3 py-2 text-xs font-semibold text-gray-200 border-b border-surface-3">{title}</div>
      <div className="overflow-auto max-h-[280px]">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-surface-3">
            <tr className="text-gray-400">
              <th className="px-2.5 py-1.5 text-left font-medium">{keyLabel}</th>
              <th className="px-2.5 py-1.5 text-right font-medium">Sig</th>
              <th className="px-2.5 py-1.5 text-right font-medium">Win%</th>
              <th className="px-2.5 py-1.5 text-right font-medium">Total MTM</th>
            </tr>
          </thead>
          <tbody>
            {items.map((x) => (
              <tr key={x.key} className="border-b border-surface-3/40">
                <td className="px-2.5 py-1 text-gray-200">{x.key}</td>
                <td className="px-2.5 py-1 text-right text-gray-400">{x.signals}</td>
                <td className="px-2.5 py-1 text-right text-gray-400">{x.win_rate}%</td>
                <td className={`px-2.5 py-1 text-right font-semibold ${pos(x.total_mtm)}`}>{NUM(x.total_mtm)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Histogram({ bins }) {
  if (!bins?.length) return null;
  const max = Math.max(...bins.map((b) => b.count), 1);
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-3">
      <div className="text-xs font-semibold text-gray-200 mb-2">P&amp;L Distribution</div>
      <div className="flex items-end gap-1 h-32">
        {bins.map((b, i) => {
          const mid = (b.lo + b.hi) / 2;
          return (
            <div key={i} className="flex-1 flex flex-col items-center justify-end group relative" title={`${b.label} → ${b.count}`}>
              <div className={`w-full rounded-t ${mid >= 0 ? 'bg-emerald-600/70' : 'bg-red-600/70'}`} style={{ height: `${(b.count / max) * 100}%` }} />
              <div className="text-[8px] text-gray-600 mt-1 rotate-0">{Math.round(b.lo)}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function EquityCurve({ curve }) {
  if (!curve || curve.length < 2) return null;
  const w = 600, h = 150, pad = 10;
  const ys = curve.map((p) => p.pnl);
  const min = Math.min(0, ...ys), max = Math.max(0, ...ys);
  const span = (max - min) || 1;
  const x = (i) => pad + (i / (curve.length - 1)) * (w - 2 * pad);
  const y = (v) => h - pad - ((v - min) / span) * (h - 2 * pad);
  const pts = curve.map((p, i) => `${x(i).toFixed(1)},${y(p.pnl).toFixed(1)}`).join(' ');
  const area = `${x(0).toFixed(1)},${y(0).toFixed(1)} ${pts} ${x(curve.length - 1).toFixed(1)},${y(0).toFixed(1)}`;
  const last = ys[ys.length - 1];
  const col = last >= 0 ? '#10b981' : '#ef4444';
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-3">
      <div className="text-xs font-semibold text-gray-200 mb-2">Equity Curve — cumulative realised P&amp;L</div>
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full" preserveAspectRatio="none">
        <line x1="0" y1={y(0)} x2={w} y2={y(0)} stroke="#374151" strokeWidth="1" strokeDasharray="3 3" />
        <polygon points={area} fill={col} opacity="0.12" />
        <polyline points={pts} fill="none" stroke={col} strokeWidth="2" />
      </svg>
      <div className="flex justify-between text-[10px] text-gray-600 mt-1">
        <span>{curve[0].t}</span><span>{curve[curve.length - 1].t}</span>
      </div>
    </div>
  );
}

function Heatmap({ heatmap }) {
  if (!heatmap?.rows?.length) return null;
  const all = heatmap.rows.flatMap((r) => r.cells.map((c) => Math.abs(c.total_mtm)));
  const max = Math.max(...all, 1);
  const color = (v) => {
    if (!v) return 'transparent';
    const a = Math.min(Math.abs(v) / max, 1) * 0.85 + 0.1;
    return v >= 0 ? `rgba(16,185,129,${a})` : `rgba(239,68,68,${a})`;
  };
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-3 overflow-x-auto">
      <div className="text-xs font-semibold text-gray-200 mb-2">Heatmap — Weekday × Entry Hour (Total MTM)</div>
      <table className="text-[11px] border-collapse">
        <thead>
          <tr><th className="px-2 py-1 text-gray-500"></th>{heatmap.hours.map((h) => <th key={h} className="px-2 py-1 text-gray-500 font-medium">{h}:00</th>)}</tr>
        </thead>
        <tbody>
          {heatmap.rows.map((r) => (
            <tr key={r.weekday}>
              <td className="px-2 py-1 text-gray-400 font-medium">{r.weekday}</td>
              {r.cells.map((c, i) => (
                <td key={i} className="px-2 py-1 text-center text-gray-200" style={{ background: color(c.total_mtm) }} title={`${c.count} signals · ${NUM(c.total_mtm)}`}>
                  {c.count ? Math.round(c.total_mtm) : ''}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function exportXls(filename, report) {
  const tbl = (title, headers, rows) =>
    `<h3>${title}</h3><table border=1><tr>${headers.map((h) => `<th>${h}</th>`).join('')}</tr>` +
    rows.map((r) => `<tr>${r.map((c) => `<td>${c ?? ''}</td>`).join('')}</tr>`).join('') + '</table><br/>';
  const o = report.overall;
  const rank = (items) => items.map((x) => [x.key, x.signals, x.win_rate, x.total_mtm]);
  const html =
    `<html><head><meta charset="utf-8"></head><body>` +
    tbl('Overall', ['Signals', 'Win %', 'Capital Used', 'Total MTM', 'ROI on Capital %', 'CAGR %', 'Max Drawdown %', 'NIFTY B&H %', 'Avg MTM', 'Best', 'Worst', 'Profit Factor'],
        [[o.signals, o.win_rate, o.total_capital, o.total_mtm, o.roi_pct, report.cagr_pct, report.max_drawdown_pct, report.benchmark?.return_pct ?? '', o.avg_mtm, o.best, o.worst, o.profit_factor]]) +
    tbl('Stock Ranking', ['Stock', 'Signals', 'Win %', 'Total MTM'], rank(report.stock_ranking)) +
    tbl('Day of Week', ['Day', 'Signals', 'Win %', 'Total MTM'], rank(report.day_of_week)) +
    tbl('Time of Day', ['Slot', 'Signals', 'Win %', 'Total MTM'], rank(report.time_of_day)) +
    tbl('Gap Performance', ['Gap', 'Signals', 'Win %', 'Total MTM'], rank(report.gap_performance)) +
    tbl('Sector Performance', ['Sector', 'Signals', 'Win %', 'Total MTM'], rank(report.sector_performance)) +
    `</body></html>`;
  const blob = new Blob([html], { type: 'application/vnd.ms-excel' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = url; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
}

export default function PMVwapReport({ kind }) {
  const apis = API[kind];
  const [runs, setRuns] = useState([]);
  const [runId, setRunId] = useState('');
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [cmpA, setCmpA] = useState('');
  const [cmpB, setCmpB] = useState('');
  const [cmp, setCmp] = useState(null);
  const [err, setErr] = useState('');

  const loadRuns = useCallback(() => {
    apis.runs().then((r) => {
      if (r.status === 'ok') {
        setRuns(r.runs || []);
        if (r.runs?.[0]) { setRunId((v) => v || r.runs[0].run_id); setCmpA((v) => v || r.runs[0].run_id); setCmpB((v) => v || (r.runs[1]?.run_id || r.runs[0].run_id)); }
      }
    }).catch(() => {});
  }, [apis]);

  useEffect(() => { loadRuns(); }, [loadRuns]);

  const loadReport = useCallback(async (rid) => {
    if (!rid) return;
    setLoading(true); setErr('');
    try {
      const r = await apis.report(rid);
      if (r.status === 'ok') setReport(r.report); else setErr(r.message || 'Report failed');
    } catch (e) { setErr(e.message || 'Report failed'); }
    finally { setLoading(false); }
  }, [apis]);

  useEffect(() => { if (runId) loadReport(runId); }, [runId, loadReport]);

  const runCompare = async () => {
    if (!cmpA || !cmpB) return;
    try {
      const r = await apis.compare(cmpA, cmpB);
      if (r.status === 'ok') setCmp(r.comparison); else setErr(r.message || 'Compare failed');
    } catch (e) { setErr(e.message || 'Compare failed'); }
  };

  const runLabel = (r) => `${r.run_id.slice(0, 8)} · ${r.mode} · ${r.start}${r.end !== r.start ? `→${r.end}` : ''} · ${r.signals} sig`;
  const o = report?.overall;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <BarChart3 className="w-4 h-4 text-brand-400" />
        <span className="text-sm font-semibold text-gray-200">Summary Report</span>
        <select value={runId} onChange={(e) => setRunId(e.target.value)}
          className="bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 max-w-[360px]">
          {runs.length === 0 && <option value="">No saved runs yet — run a backtest</option>}
          {runs.map((r) => <option key={r.run_id} value={r.run_id}>{runLabel(r)}</option>)}
        </select>
        <button onClick={loadRuns} title="Refresh runs" className="p-1.5 rounded-lg bg-surface-3 border border-surface-4 text-gray-400 hover:text-white"><RefreshCw className="w-3.5 h-3.5" /></button>
        {report && (
          <button onClick={() => exportXls(`pmvwap_${kind}_report_${runId.slice(0, 8)}.xls`, report)}
            className="ml-auto flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white transition">
            <Download className="w-3.5 h-3.5" /> Export Excel
          </button>
        )}
      </div>

      {err && <div className="text-red-400 text-sm">{err}</div>}
      {loading && <div className="text-gray-500 flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Building report…</div>}

      {o && !loading && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2">
            <Stat label="Signals" value={o.signals} />
            <Stat label="Win %" value={`${o.win_rate}%`} tone={o.win_rate >= 50 ? 'up' : 'down'} />
            {o.total_capital > 0 && <Stat label="Capital Used" value={`₹${NUM(o.total_capital, 0)}`} />}
            <Stat label="Total MTM" value={NUM(o.total_mtm)} tone={o.total_mtm >= 0 ? 'up' : 'down'} />
            {o.total_capital > 0 && <Stat label="ROI on Capital" value={`${o.roi_pct}%`} tone={o.roi_pct >= 0 ? 'up' : 'down'} />}
            <Stat label="Avg MTM" value={NUM(o.avg_mtm)} tone={o.avg_mtm >= 0 ? 'up' : 'down'} />
            <Stat label="Profit Factor" value={o.profit_factor} tone={o.profit_factor >= 1 ? 'up' : 'down'} />
            <Stat label="Max Drawdown" value={`${report.max_drawdown_pct}%`} tone="down" />
            {report.cagr_pct != null && <Stat label="CAGR" value={`${report.cagr_pct}%`} tone={report.cagr_pct >= 0 ? 'up' : 'down'} />}
            <Stat label="Best" value={NUM(o.best)} tone="up" />
            <Stat label="Worst" value={NUM(o.worst)} tone="down" />
          </div>

          {/* Benchmark vs strategy */}
          {report.benchmark && (
            <div className="bg-surface-2 border border-surface-3 rounded-xl px-4 py-2.5 flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
              <span className="text-gray-400">{report.benchmark.label} <strong className={report.benchmark.return_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}>{report.benchmark.return_pct}%</strong></span>
              {o.total_capital > 0 && (
                <span className="text-gray-400">Strategy ROI on capital <strong className={o.roi_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}>{o.roi_pct}%</strong></span>
              )}
              {o.total_capital > 0 && (
                <span className="text-gray-400">Edge vs NIFTY <strong className={(o.roi_pct - report.benchmark.return_pct) >= 0 ? 'text-emerald-400' : 'text-red-400'}>{NUM(o.roi_pct - report.benchmark.return_pct)}%</strong></span>
              )}
              <span className="text-gray-600 text-xs">{report.benchmark.start} → {report.benchmark.end}</span>
            </div>
          )}

          <EquityCurve curve={report.equity_curve} />

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <Histogram bins={report.pnl_distribution} />
            <Heatmap heatmap={report.heatmap} />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            <RankTable title="Stock Ranking" items={report.stock_ranking} keyLabel="Stock" />
            <RankTable title="Day of Week" items={report.day_of_week} keyLabel="Day" />
            <RankTable title="Time of Day" items={report.time_of_day} keyLabel="Slot" />
            <RankTable title="Gap-up vs Gap-down" items={report.gap_performance} keyLabel="Gap" />
            <RankTable title="Sector Performance" items={report.sector_performance} keyLabel="Sector" />
          </div>
        </>
      )}

      {/* Run comparison */}
      {runs.length >= 1 && (
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-3 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <GitCompare className="w-4 h-4 text-brand-400" />
            <span className="text-sm font-semibold text-gray-200">Compare two runs</span>
            <select value={cmpA} onChange={(e) => setCmpA(e.target.value)} className="bg-surface-3 border border-surface-4 rounded-lg px-2 py-1 text-xs text-gray-200 max-w-[300px]">
              {runs.map((r) => <option key={r.run_id} value={r.run_id}>{runLabel(r)}</option>)}
            </select>
            <span className="text-gray-500 text-xs">vs</span>
            <select value={cmpB} onChange={(e) => setCmpB(e.target.value)} className="bg-surface-3 border border-surface-4 rounded-lg px-2 py-1 text-xs text-gray-200 max-w-[300px]">
              {runs.map((r) => <option key={r.run_id} value={r.run_id}>{runLabel(r)}</option>)}
            </select>
            <button onClick={runCompare} className="px-3 py-1 text-xs rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold">Compare</button>
          </div>
          {cmp && (
            <div className="overflow-x-auto">
              <table className="text-xs w-full max-w-[720px]">
                <thead><tr className="text-gray-400 border-b border-surface-3">
                  <th className="px-3 py-1.5 text-left">Metric</th>
                  <th className="px-3 py-1.5 text-right">{cmp.label_a}</th>
                  <th className="px-3 py-1.5 text-right">{cmp.label_b}</th>
                  <th className="px-3 py-1.5 text-right">Δ</th>
                </tr></thead>
                <tbody>
                  {['signals', 'win_rate', 'total_mtm', 'avg_mtm', 'best', 'worst', 'profit_factor'].map((k) => (
                    <tr key={k} className="border-b border-surface-3/40">
                      <td className="px-3 py-1 text-gray-300">{k}</td>
                      <td className="px-3 py-1 text-right text-gray-200">{NUM(cmp.a[k])}</td>
                      <td className="px-3 py-1 text-right text-gray-200">{NUM(cmp.b[k])}</td>
                      <td className={`px-3 py-1 text-right font-semibold ${pos(cmp.delta[k] ?? 0)}`}>{NUM(cmp.delta[k])}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
