/**
 * Portfolio Analytics — independent module.
 *
 * Strict scope:
 *   - Read-only against Zerodha holdings (never modifies positions)
 *   - User-owned watchlists & research entries (own DB tables)
 *   - Pure visual proximity indicators, no automation
 *
 * Never imports from strategy pages and never calls any trading mutation
 * endpoints — see app/routes/portfolio_routes.py for the mirror invariant.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Briefcase, RefreshCw, TrendingUp, TrendingDown, ArrowUpRight,
  ArrowDownRight, PieChart as PieIcon, Plus, Trash2, Pencil,
  Search, X, Target, Bookmark, FlaskConical, AlertCircle, ChevronDown,
  ChevronUp, Filter, IndianRupee, Layers, Zap,
} from 'lucide-react';
import {
  ResponsiveContainer, PieChart, Pie, Cell, Tooltip, Legend,
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
} from 'recharts';
import { api } from '../api';

const REFRESH_MS = 15_000;

const SECTOR_COLORS = [
  '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6',
  '#ec4899', '#14b8a6', '#f97316', '#6366f1', '#84cc16',
  '#06b6d4', '#a855f7', '#eab308', '#22c55e', '#64748b',
];

const fmtINR = (v, frac = 2) =>
  Number(v ?? 0).toLocaleString('en-IN', {
    minimumFractionDigits: frac, maximumFractionDigits: frac,
  });

const cls = (...xs) => xs.filter(Boolean).join(' ');

/* ─── Card / sub-components ─────────────────────────────────── */

function Stat({ icon: Icon, label, value, sub, tone = 'default' }) {
  const toneCls = {
    default: 'text-white',
    green: 'text-emerald-400',
    red: 'text-rose-400',
    blue: 'text-blue-400',
  }[tone] || 'text-white';
  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
      <div className="flex items-center gap-2 text-[11px] text-gray-500 uppercase tracking-wider font-medium">
        {Icon && <Icon className="w-3.5 h-3.5" />}
        {label}
      </div>
      <p className={`mt-1.5 text-xl font-bold mono ${toneCls}`}>{value}</p>
      {sub && <p className="text-[11px] text-gray-500 mt-0.5">{sub}</p>}
    </div>
  );
}

function ProximityDot({ label, color = 'amber' }) {
  const map = {
    amber: 'bg-amber-400 shadow-amber-400/50',
    emerald: 'bg-emerald-400 shadow-emerald-400/50',
    rose: 'bg-rose-400 shadow-rose-400/50',
  };
  return (
    <span title={label} className="inline-flex items-center gap-1">
      <span className={`w-2 h-2 rounded-full animate-pulse shadow-[0_0_8px] ${map[color]}`} />
    </span>
  );
}

/* ─── Holdings panel ────────────────────────────────────────── */

function HoldingsTable({ rows, onSetExit, sortKey, sortDir, onSort, filter, onFilter }) {
  const sorted = useMemo(() => {
    const arr = (rows || []).filter((r) =>
      !filter || r.tradingsymbol.toLowerCase().includes(filter.toLowerCase())
    );
    arr.sort((a, b) => {
      const av = a[sortKey] ?? 0; const bv = b[sortKey] ?? 0;
      if (typeof av === 'string') return sortDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
      return sortDir === 'asc' ? av - bv : bv - av;
    });
    return arr;
  }, [rows, filter, sortKey, sortDir]);

  const Th = ({ k, children, align = 'left' }) => (
    <th
      onClick={() => onSort(k)}
      className={cls(
        'cursor-pointer select-none px-3 py-2 text-[11px] uppercase tracking-wider text-gray-500 font-medium hover:text-gray-300',
        align === 'right' && 'text-right',
        align === 'center' && 'text-center',
      )}
    >
      <span className="inline-flex items-center gap-1">
        {children}
        {sortKey === k && (sortDir === 'asc' ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />)}
      </span>
    </th>
  );

  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between gap-2 p-3 border-b border-surface-3">
        <div className="flex items-center gap-2 text-sm text-gray-300">
          <Layers className="w-4 h-4 text-brand-400" />
          <span className="font-semibold">Holdings</span>
          <span className="text-[11px] text-gray-500">({rows?.length || 0})</span>
        </div>
        <div className="relative">
          <Search className="w-3.5 h-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="text" placeholder="Filter symbol…"
            value={filter} onChange={(e) => onFilter(e.target.value)}
            className="pl-7 pr-2 py-1 text-xs bg-surface-3 border border-surface-4 rounded-md text-white w-44"
          />
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-surface-3/40">
            <tr>
              <Th k="tradingsymbol">Symbol</Th>
              <Th k="sector">Sector</Th>
              <Th k="quantity" align="right">Qty</Th>
              <Th k="average_price" align="right">Avg</Th>
              <Th k="last_price" align="right">LTP</Th>
              <Th k="invested" align="right">Invested</Th>
              <Th k="current_value" align="right">Current</Th>
              <Th k="pnl" align="right">P&amp;L</Th>
              <Th k="pnl_pct" align="right">%</Th>
              <Th k="allocation_pct" align="right">Alloc</Th>
              <th className="px-3 py-2 text-[11px] uppercase tracking-wider text-gray-500 font-medium text-center">Exit</th>
            </tr>
          </thead>
          <tbody>
            {sorted.length === 0 ? (
              <tr><td colSpan={11} className="px-4 py-8 text-center text-gray-500 text-sm">
                No holdings to show. Connect your Zerodha account or buy a stock to see it here.
              </td></tr>
            ) : sorted.map((h) => (
              <tr key={`${h.exchange}:${h.tradingsymbol}`}
                  className="border-t border-surface-3 hover:bg-surface-3/30">
                <td className="px-3 py-2 font-medium text-white">
                  <div className="flex items-center gap-2">
                    {h.tradingsymbol}
                    {h.near_exit && <ProximityDot label="Near exit level" color="amber" />}
                  </div>
                </td>
                <td className="px-3 py-2 text-xs text-gray-400">{h.sector}</td>
                <td className="px-3 py-2 text-right mono">{h.quantity}</td>
                <td className="px-3 py-2 text-right mono">{fmtINR(h.average_price)}</td>
                <td className="px-3 py-2 text-right mono text-white">{fmtINR(h.last_price)}</td>
                <td className="px-3 py-2 text-right mono text-gray-300">₹{fmtINR(h.invested, 0)}</td>
                <td className="px-3 py-2 text-right mono text-white">₹{fmtINR(h.current_value, 0)}</td>
                <td className={cls('px-3 py-2 text-right mono font-medium',
                  h.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400')}>
                  {h.pnl >= 0 ? '+' : ''}₹{fmtINR(h.pnl, 0)}
                </td>
                <td className={cls('px-3 py-2 text-right mono',
                  h.pnl_pct >= 0 ? 'text-emerald-400' : 'text-rose-400')}>
                  {h.pnl_pct >= 0 ? '+' : ''}{Number(h.pnl_pct).toFixed(2)}%
                </td>
                <td className="px-3 py-2 text-right mono text-gray-400">{Number(h.allocation_pct).toFixed(1)}%</td>
                <td className="px-3 py-2 text-center">
                  <button onClick={() => onSetExit(h)}
                    className="text-[11px] px-2 py-0.5 rounded bg-surface-3 border border-surface-4 hover:bg-surface-4 text-gray-300">
                    {h.exit_level ? `@${fmtINR(h.exit_level, 2)}` : '+ Set'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ─── Watchlist panel ───────────────────────────────────────── */

function WatchlistsPanel({ watchlists, onCreate, onDelete, onAddItem, onRemoveItem }) {
  const [activeId, setActiveId] = useState(null);
  const [newName, setNewName] = useState('');
  const [newSym, setNewSym] = useState('');
  const [newExch, setNewExch] = useState('NSE');

  useEffect(() => {
    if (watchlists.length && !watchlists.find((w) => w.id === activeId)) {
      setActiveId(watchlists[0].id);
    }
  }, [watchlists, activeId]);

  const active = watchlists.find((w) => w.id === activeId);

  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <Bookmark className="w-4 h-4 text-brand-400" />
        <h3 className="text-sm font-semibold text-white">Watchlists</h3>
        <span className="ml-auto text-[10px] text-gray-500">{watchlists.length} list(s)</span>
      </div>

      {/* Create new watchlist */}
      <div className="flex items-center gap-2 mb-3">
        <input type="text" placeholder="New watchlist name…"
          value={newName} onChange={(e) => setNewName(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && newName.trim() && (onCreate(newName.trim()), setNewName(''))}
          className="flex-1 bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5 text-sm text-white" />
        <button
          onClick={() => { if (newName.trim()) { onCreate(newName.trim()); setNewName(''); } }}
          className="px-3 py-1.5 text-xs font-medium rounded-md bg-brand-600 hover:bg-brand-700 text-white inline-flex items-center gap-1">
          <Plus className="w-3 h-3" /> Create
        </button>
      </div>

      {/* Tabs */}
      {watchlists.length > 0 && (
        <div className="flex flex-wrap gap-1.5 border-b border-surface-3 pb-2 mb-3">
          {watchlists.map((w) => (
            <button key={w.id} onClick={() => setActiveId(w.id)}
              className={cls(
                'group inline-flex items-center gap-2 px-2.5 py-1 text-xs rounded-md border',
                activeId === w.id
                  ? 'bg-brand-600/15 text-brand-300 border-brand-500/30'
                  : 'bg-surface-3 text-gray-400 border-surface-4 hover:bg-surface-4'
              )}>
              {w.name}
              <span className="text-[10px] text-gray-500">{w.items.length}</span>
              <span
                role="button"
                tabIndex={0}
                onClick={(e) => {
                  e.stopPropagation();
                  if (window.confirm(`Delete watchlist "${w.name}"?`)) onDelete(w.id);
                }}
                className="text-gray-500 hover:text-rose-400 ml-1">
                <X className="w-3 h-3" />
              </span>
            </button>
          ))}
        </div>
      )}

      {/* Active watchlist content */}
      {active ? (
        <>
          <div className="flex items-center gap-2 mb-2">
            <input type="text" placeholder="Symbol (e.g. RELIANCE)" value={newSym}
              onChange={(e) => setNewSym(e.target.value.toUpperCase())}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && newSym.trim()) {
                  onAddItem(active.id, { tradingsymbol: newSym.trim(), exchange: newExch });
                  setNewSym('');
                }
              }}
              className="flex-1 bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5 text-sm text-white mono" />
            <select value={newExch} onChange={(e) => setNewExch(e.target.value)}
              className="bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5 text-xs text-white">
              <option value="NSE">NSE</option>
              <option value="BSE">BSE</option>
            </select>
            <button
              onClick={() => {
                if (newSym.trim()) {
                  onAddItem(active.id, { tradingsymbol: newSym.trim(), exchange: newExch });
                  setNewSym('');
                }
              }}
              className="px-3 py-1.5 text-xs font-medium rounded-md bg-brand-600 hover:bg-brand-700 text-white inline-flex items-center gap-1">
              <Plus className="w-3 h-3" /> Add
            </button>
          </div>

          {active.items.length === 0 ? (
            <p className="text-xs text-gray-500 italic py-3 text-center">No symbols yet — add one above.</p>
          ) : (
            <ul className="divide-y divide-surface-3">
              {active.items.map((it) => (
                <li key={it.id} className="flex items-center justify-between py-2">
                  <div className="flex items-center gap-3">
                    <span className="text-sm font-medium text-white mono">{it.tradingsymbol}</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-3 text-gray-400">{it.exchange}</span>
                    <span className="text-[10px] text-gray-500">{it.sector}</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-sm text-white mono">
                      {it.last_price > 0 ? `₹${fmtINR(it.last_price)}` : '—'}
                    </span>
                    <button onClick={() => onRemoveItem(it.id)}
                      className="text-gray-500 hover:text-rose-400">
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      ) : (
        <p className="text-xs text-gray-500 italic py-6 text-center">Create your first watchlist above.</p>
      )}
    </div>
  );
}

/* ─── Research panel ────────────────────────────────────────── */

function ResearchPanel({ entries, onCreate, onUpdate, onDelete }) {
  const empty = {
    tradingsymbol: '', exchange: 'NSE',
    entry_level: '', target_level: '', stop_level: '',
    proximity_pct: 1, note: '',
  };
  const [draft, setDraft] = useState(empty);
  const [editingId, setEditingId] = useState(null);

  const startEdit = (e) => {
    setEditingId(e.id);
    setDraft({
      tradingsymbol: e.tradingsymbol, exchange: e.exchange || 'NSE',
      entry_level: e.entry_level, target_level: e.target_level,
      stop_level: e.stop_level ?? '', proximity_pct: e.proximity_pct ?? 1,
      note: e.note || '',
    });
  };

  const submit = async () => {
    const sym = (draft.tradingsymbol || '').trim().toUpperCase();
    if (!sym) return alert('Symbol is required');
    if (!draft.entry_level || !draft.target_level) return alert('Entry and target levels are required');
    const body = {
      tradingsymbol: sym,
      exchange: (draft.exchange || 'NSE').toUpperCase(),
      entry_level: Number(draft.entry_level),
      target_level: Number(draft.target_level),
      stop_level: draft.stop_level === '' || draft.stop_level == null
        ? null : Number(draft.stop_level),
      proximity_pct: Number(draft.proximity_pct) || 1,
      note: draft.note || '',
    };
    if (editingId) await onUpdate(editingId, body);
    else await onCreate(body);
    setDraft(empty);
    setEditingId(null);
  };

  return (
    <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <FlaskConical className="w-4 h-4 text-brand-400" />
        <h3 className="text-sm font-semibold text-white">Research Tracker</h3>
        <span className="ml-auto text-[10px] text-gray-500">
          Reference only — no orders are placed
        </span>
      </div>

      {/* Form */}
      <div className="grid grid-cols-2 sm:grid-cols-7 gap-2 mb-3">
        <input className="col-span-2 sm:col-span-2 bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5 text-sm text-white mono"
          placeholder="Symbol" value={draft.tradingsymbol}
          onChange={(e) => setDraft({ ...draft, tradingsymbol: e.target.value.toUpperCase() })} />
        <input className="bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5 text-sm text-white mono"
          placeholder="Entry" type="number" value={draft.entry_level}
          onChange={(e) => setDraft({ ...draft, entry_level: e.target.value })} />
        <input className="bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5 text-sm text-white mono"
          placeholder="Target" type="number" value={draft.target_level}
          onChange={(e) => setDraft({ ...draft, target_level: e.target.value })} />
        <input className="bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5 text-sm text-white mono"
          placeholder="Stop (opt)" type="number" value={draft.stop_level}
          onChange={(e) => setDraft({ ...draft, stop_level: e.target.value })} />
        <input className="bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5 text-sm text-white mono"
          placeholder="Prox %" type="number" step="0.1" value={draft.proximity_pct}
          onChange={(e) => setDraft({ ...draft, proximity_pct: e.target.value })} />
        <button onClick={submit}
          className="px-3 py-1.5 text-xs font-medium rounded-md bg-brand-600 hover:bg-brand-700 text-white inline-flex items-center justify-center gap-1">
          {editingId ? <Pencil className="w-3 h-3" /> : <Plus className="w-3 h-3" />}
          {editingId ? 'Update' : 'Add'}
        </button>
      </div>
      {editingId && (
        <button onClick={() => { setEditingId(null); setDraft(empty); }}
          className="mb-3 text-[11px] text-gray-400 underline hover:text-gray-200">
          Cancel edit
        </button>
      )}

      {/* List */}
      {entries.length === 0 ? (
        <p className="text-xs text-gray-500 italic py-4 text-center">No research entries yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-surface-3/40">
              <tr>
                {['Symbol', 'LTP', 'Entry', 'Target', 'Stop', 'Prox %', 'Status', 'Note', ''].map((h) => (
                  <th key={h} className="px-2 py-1.5 text-[11px] uppercase tracking-wider text-gray-500 font-medium text-left">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id} className="border-t border-surface-3 hover:bg-surface-3/30">
                  <td className="px-2 py-1.5 mono text-white">
                    <div className="flex items-center gap-2">
                      {e.tradingsymbol}
                      {e.near_entry && <ProximityDot label="Near entry" color="emerald" />}
                      {e.near_target && <ProximityDot label="Near target" color="amber" />}
                      {e.near_stop && <ProximityDot label="Near stop" color="rose" />}
                    </div>
                  </td>
                  <td className="px-2 py-1.5 mono text-white">{e.last_price > 0 ? `₹${fmtINR(e.last_price)}` : '—'}</td>
                  <td className="px-2 py-1.5 mono text-emerald-300">₹{fmtINR(e.entry_level)}</td>
                  <td className="px-2 py-1.5 mono text-blue-300">₹{fmtINR(e.target_level)}</td>
                  <td className="px-2 py-1.5 mono text-rose-300">{e.stop_level ? `₹${fmtINR(e.stop_level)}` : '—'}</td>
                  <td className="px-2 py-1.5 mono text-gray-400">{Number(e.proximity_pct).toFixed(2)}%</td>
                  <td className="px-2 py-1.5 text-xs">
                    {e.near_entry && <span className="text-emerald-400">Near entry</span>}
                    {e.near_target && <span className="text-amber-400">{e.near_entry ? ' · ' : ''}Near target</span>}
                    {e.near_stop && <span className="text-rose-400">{(e.near_entry || e.near_target) ? ' · ' : ''}Near stop</span>}
                    {!e.near_entry && !e.near_target && !e.near_stop && <span className="text-gray-500">—</span>}
                  </td>
                  <td className="px-2 py-1.5 text-xs text-gray-400 max-w-[16ch] truncate" title={e.note}>{e.note || '—'}</td>
                  <td className="px-2 py-1.5 text-right whitespace-nowrap">
                    <button onClick={() => startEdit(e)} className="text-gray-400 hover:text-blue-400 mr-1">
                      <Pencil className="w-3.5 h-3.5" />
                    </button>
                    <button onClick={() => window.confirm(`Delete ${e.tradingsymbol}?`) && onDelete(e.id)}
                      className="text-gray-400 hover:text-rose-400">
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/* ─── Exit-level modal ──────────────────────────────────────── */

function ExitLevelModal({ holding, onClose, onSave, onDelete }) {
  const [price, setPrice] = useState(holding?.exit_level ?? '');
  const [prox, setProx] = useState(holding?.exit_proximity_pct ?? 1);
  if (!holding) return null;
  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-surface-2 border border-surface-3 rounded-xl p-5 w-full max-w-sm" onClick={(e) => e.stopPropagation()}>
        <h3 className="text-sm font-semibold text-white mb-1">Set Exit Level</h3>
        <p className="text-xs text-gray-500 mb-4">
          {holding.tradingsymbol} · LTP ₹{fmtINR(holding.last_price)} · Avg ₹{fmtINR(holding.average_price)}
        </p>
        <label className="text-xs text-gray-400">Exit Price
          <input type="number" value={price} onChange={(e) => setPrice(e.target.value)}
            className="mt-1 w-full bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5 text-white text-sm mono" />
        </label>
        <label className="text-xs text-gray-400 mt-3 block">Proximity Window (%)
          <input type="number" step="0.1" value={prox} onChange={(e) => setProx(e.target.value)}
            className="mt-1 w-full bg-surface-3 border border-surface-4 rounded-md px-2 py-1.5 text-white text-sm mono" />
        </label>
        <p className="text-[11px] text-gray-500 mt-2">
          Visual notification only — no order will be placed automatically.
        </p>
        <div className="flex items-center justify-between gap-2 mt-4">
          {holding.exit_level && (
            <button onClick={() => onDelete(holding)} className="text-xs text-rose-400 hover:text-rose-300">
              Remove
            </button>
          )}
          <div className="ml-auto flex gap-2">
            <button onClick={onClose} className="px-3 py-1.5 text-xs rounded-md bg-surface-3 text-gray-300 hover:bg-surface-4">
              Cancel
            </button>
            <button onClick={() => onSave({ exit_level: Number(price), proximity_pct: Number(prox) || 1 })}
              className="px-3 py-1.5 text-xs rounded-md bg-brand-600 hover:bg-brand-700 text-white">
              Save
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─── Main page ─────────────────────────────────────────────── */

export default function PortfolioAnalytics() {
  const [data, setData] = useState(null);     // holdings response
  const [watchlists, setWatchlists] = useState([]);
  const [research, setResearch] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [refreshing, setRefreshing] = useState(false);

  const [filter, setFilter] = useState('');
  const [sortKey, setSortKey] = useState('current_value');
  const [sortDir, setSortDir] = useState('desc');
  const [exitModal, setExitModal] = useState(null);
  const timerRef = useRef(null);

  const fetchAll = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setRefreshing(true);
    try {
      const [h, w, r] = await Promise.all([
        api.getPortfolioHoldings().catch((e) => { setErr(String(e.message || e)); return null; }),
        api.getPortfolioWatchlists().catch(() => []),
        api.getResearchEntries().catch(() => []),
      ]);
      if (h) setData(h);
      setWatchlists(Array.isArray(w) ? w : []);
      setResearch(Array.isArray(r) ? r : []);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    timerRef.current = setInterval(() => fetchAll(true), REFRESH_MS);
    return () => clearInterval(timerRef.current);
  }, [fetchAll]);

  const handleSort = (k) => {
    if (k === sortKey) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    else { setSortKey(k); setSortDir('desc'); }
  };

  /* Watchlist handlers */
  const createWatchlist = async (name) => {
    try { await api.createWatchlist(name); await fetchAll(true); }
    catch (e) { alert(e.message || 'Failed'); }
  };
  const deleteWatchlist = async (id) => {
    try { await api.deleteWatchlist(id); await fetchAll(true); }
    catch (e) { alert(e.message || 'Failed'); }
  };
  const addWatchlistItem = async (id, item) => {
    try { await api.addWatchlistItem(id, item); await fetchAll(true); }
    catch (e) { alert(e.message || 'Failed'); }
  };
  const removeWatchlistItem = async (itemId) => {
    try { await api.deleteWatchlistItem(itemId); await fetchAll(true); }
    catch (e) { alert(e.message || 'Failed'); }
  };

  /* Research handlers */
  const createResearch = async (body) => {
    try { await api.createResearchEntry(body); await fetchAll(true); }
    catch (e) { alert(e.message || 'Failed'); }
  };
  const updateResearch = async (id, body) => {
    try { await api.updateResearchEntry(id, body); await fetchAll(true); }
    catch (e) { alert(e.message || 'Failed'); }
  };
  const deleteResearch = async (id) => {
    try { await api.deleteResearchEntry(id); await fetchAll(true); }
    catch (e) { alert(e.message || 'Failed'); }
  };

  /* Exit level handlers */
  const saveExit = async (form) => {
    try {
      await api.upsertHoldingExitLevel({
        tradingsymbol: exitModal.tradingsymbol,
        exchange: exitModal.exchange || 'NSE',
        ...form,
      });
      setExitModal(null);
      await fetchAll(true);
    } catch (e) { alert(e.message || 'Failed'); }
  };
  const deleteExit = async (h) => {
    try {
      await api.deleteHoldingExitLevel(h.tradingsymbol, h.exchange || 'NSE');
      setExitModal(null);
      await fetchAll(true);
    } catch (e) { alert(e.message || 'Failed'); }
  };

  /* Derived */
  const summary = data?.summary || {};
  const sectors = data?.sector_allocation || [];
  const holdings = data?.holdings || [];
  const topGainer = data?.top_gainer;
  const topLoser = data?.top_loser;

  const allocationBars = useMemo(() => {
    return holdings.slice().sort((a, b) => b.allocation_pct - a.allocation_pct).slice(0, 12)
      .map((h) => ({ symbol: h.tradingsymbol, pct: h.allocation_pct, current: h.current_value }));
  }, [holdings]);

  /* Render */
  return (
    <div className="p-3 sm:p-6 space-y-5 max-w-[1400px] mx-auto">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold text-white flex items-center gap-2">
            <Briefcase className="w-5 h-5 text-brand-400" />
            Portfolio Analytics
          </h1>
          <p className="text-xs sm:text-sm text-gray-500 mt-0.5">
            Holdings, watchlists, and research — independent from intraday strategies. Read-only on Zerodha holdings.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {data?.as_of && (
            <span className="text-[11px] text-gray-500">As of {data.as_of}</span>
          )}
          <button onClick={() => fetchAll()} disabled={refreshing}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-surface-3 text-gray-300 hover:bg-surface-4 disabled:opacity-50">
            <RefreshCw className={cls('w-3 h-3', refreshing && 'animate-spin')} /> Refresh
          </button>
        </div>
      </div>

      {err && (
        <div className="bg-rose-500/10 border border-rose-500/30 rounded-lg p-3 text-rose-300 text-sm flex items-center gap-2">
          <AlertCircle className="w-4 h-4" /> {err}
        </div>
      )}

      {/* Top stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        <Stat icon={IndianRupee} label="Invested" value={`₹${fmtINR(summary.total_invested, 0)}`} sub={`${summary.holdings_count || 0} holdings`} />
        <Stat icon={Layers} label="Current Value" value={`₹${fmtINR(summary.total_current, 0)}`} />
        <Stat
          icon={summary.total_pnl >= 0 ? TrendingUp : TrendingDown}
          label="Overall P&L"
          value={`${summary.total_pnl >= 0 ? '+' : ''}₹${fmtINR(summary.total_pnl, 0)}`}
          sub={`${summary.total_pnl_pct >= 0 ? '+' : ''}${Number(summary.total_pnl_pct || 0).toFixed(2)}%`}
          tone={summary.total_pnl >= 0 ? 'green' : 'red'}
        />
        <Stat
          icon={ArrowUpRight}
          label="Top Gainer"
          value={topGainer ? topGainer.tradingsymbol : '—'}
          sub={topGainer ? `+₹${fmtINR(topGainer.pnl, 0)} · +${Number(topGainer.pnl_pct).toFixed(2)}%` : 'No data'}
          tone="green"
        />
        <Stat
          icon={ArrowDownRight}
          label="Top Loser"
          value={topLoser ? topLoser.tradingsymbol : '—'}
          sub={topLoser ? `₹${fmtINR(topLoser.pnl, 0)} · ${Number(topLoser.pnl_pct).toFixed(2)}%` : 'No data'}
          tone="red"
        />
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Sector pie */}
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-2">
            <PieIcon className="w-4 h-4 text-brand-400" />
            <h3 className="text-sm font-semibold text-white">Sector Allocation</h3>
          </div>
          {sectors.length === 0 ? (
            <p className="text-xs text-gray-500 italic py-12 text-center">No data.</p>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <PieChart>
                <Pie
                  data={sectors}
                  dataKey="current_value"
                  nameKey="sector"
                  cx="50%" cy="50%"
                  outerRadius={90}
                  innerRadius={45}
                  paddingAngle={1}
                  isAnimationActive={false}
                >
                  {sectors.map((_, i) => (
                    <Cell key={i} fill={SECTOR_COLORS[i % SECTOR_COLORS.length]} stroke="#0f172a" />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 6 }}
                  formatter={(v, _n, p) => [`₹${fmtINR(v, 0)} (${p.payload.allocation_pct}%)`, p.payload.sector]} />
                <Legend wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
              </PieChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Capital allocation bars */}
        <div className="bg-surface-2 border border-surface-3 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-2">
            <Zap className="w-4 h-4 text-brand-400" />
            <h3 className="text-sm font-semibold text-white">Top Capital Allocations</h3>
          </div>
          {allocationBars.length === 0 ? (
            <p className="text-xs text-gray-500 italic py-12 text-center">No data.</p>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={allocationBars} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                <XAxis dataKey="symbol" tick={{ fill: '#94a3b8', fontSize: 10 }} angle={-30} textAnchor="end" height={50} />
                <YAxis tick={{ fill: '#94a3b8', fontSize: 10 }} tickFormatter={(v) => `${v}%`} />
                <Tooltip
                  contentStyle={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 6 }}
                  formatter={(v, _, p) => [`${Number(v).toFixed(2)}% (₹${fmtINR(p.payload.current, 0)})`, 'Allocation']} />
                <Bar dataKey="pct" fill="#3b82f6" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* Holdings table */}
      <HoldingsTable
        rows={holdings} onSetExit={setExitModal}
        sortKey={sortKey} sortDir={sortDir} onSort={handleSort}
        filter={filter} onFilter={setFilter}
      />

      {/* Watchlists & Research */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <WatchlistsPanel
          watchlists={watchlists}
          onCreate={createWatchlist}
          onDelete={deleteWatchlist}
          onAddItem={addWatchlistItem}
          onRemoveItem={removeWatchlistItem}
        />
        <ResearchPanel
          entries={research}
          onCreate={createResearch}
          onUpdate={updateResearch}
          onDelete={deleteResearch}
        />
      </div>

      {loading && (
        <div className="text-center text-xs text-gray-500 py-2">Loading…</div>
      )}

      {exitModal && (
        <ExitLevelModal
          holding={exitModal}
          onClose={() => setExitModal(null)}
          onSave={saveExit}
          onDelete={deleteExit}
        />
      )}
    </div>
  );
}
