import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  Loader2, ListChecks, Layers, User, Settings2, Upload, Download, Trash2, Plus, X, Save, Check,
} from 'lucide-react';
import { api } from '../api';

/**
 * Universe selector for the research modules:
 *   Single Stock (type-ahead) · My Watchlist (DB-backed) · All F&O
 * Calls `onChange({ mode, symbol, symbols })` whenever the selection changes.
 * Includes a full watchlist manager (create / edit / add / remove / upload /
 * download / delete) persisted to the database.
 */
export default function WatchlistBar({ universe = [], count = 0, onChange }) {
  const names = universe.map((u) => (typeof u === 'string' ? u : u.name)).filter(Boolean);
  const [mode, setMode] = useState('all');            // single | watchlist | all
  const [symbol, setSymbol] = useState('');
  const [watchlists, setWatchlists] = useState([]);
  const [activeId, setActiveId] = useState('');
  const [active, setActive] = useState(null);         // {id,name,symbols}
  const [manageOpen, setManageOpen] = useState(false);
  const [sugg, setSugg] = useState([]);               // server search results (NSE+BSE)
  const searchTimer = useRef(null);

  const emit = useCallback((next) => {
    const m = next.mode ?? mode;
    onChange?.({
      mode: m,
      symbol: m === 'single' ? (next.symbol ?? symbol) : null,
      symbols: m === 'watchlist' ? (next.symbols ?? active?.symbols ?? []) : null,
    });
  }, [mode, symbol, active, onChange]);

  const loadWatchlists = useCallback(async (selectId) => {
    try {
      const r = await api.researchWatchlists();
      if (r.status === 'ok') {
        setWatchlists(r.watchlists || []);
        const pick = selectId || activeId || r.watchlists?.[0]?.id;
        if (pick) setActiveId(String(pick));
      }
    } catch { /* ignore */ }
  }, [activeId]);

  useEffect(() => { loadWatchlists(); /* eslint-disable-next-line */ }, []);

  // Load the active watchlist's symbols whenever it changes.
  useEffect(() => {
    if (!activeId) { setActive(null); return; }
    api.researchWatchlistGet(activeId).then((r) => {
      if (r.status === 'ok') {
        setActive(r.watchlist);
        if (mode === 'watchlist') emit({ symbols: r.watchlist.symbols });
      }
    }).catch(() => {});
    // eslint-disable-next-line
  }, [activeId]);

  const pick = (m) => { setMode(m); emit({ mode: m }); };

  // Single-stock type-ahead over the full NSE + BSE equity universe (debounced).
  const onSymbolInput = (raw) => {
    const v = raw.toUpperCase();
    setSymbol(v); emit({ symbol: v });
    if (searchTimer.current) clearTimeout(searchTimer.current);
    if (v.trim().length < 2) { setSugg([]); return; }
    searchTimer.current = setTimeout(async () => {
      try {
        const r = await api.researchSymbolSearch(v.trim());
        if (r.status === 'ok') setSugg(r.results || []);
      } catch { /* ignore transient */ }
    }, 220);
  };
  // Suggestions: live NSE/BSE results once typing, else the F&O list as a starter.
  const suggestions = sugg.length ? sugg : names.map((n) => ({ symbol: n, exchange: 'NSE' }));

  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        {/* Mode toggle */}
        <div className="flex rounded-lg overflow-hidden border border-surface-4">
          {[['single', 'Single Stock', User], ['watchlist', 'My Watchlist', ListChecks], ['all', `All F&O${count ? ` (${count})` : ''}`, Layers]].map(([id, label, Icon]) => (
            <button key={id} onClick={() => pick(id)}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold ${mode === id ? 'bg-brand-600 text-white' : 'bg-surface-3 text-gray-400 hover:text-gray-200'}`}>
              <Icon className="w-3.5 h-3.5" /> {label}
            </button>
          ))}
        </div>

        {/* Single-stock type-ahead — searches ALL NSE + BSE stocks */}
        {mode === 'single' && (
          <>
            <input list="wl-search-list" value={symbol}
              onChange={(e) => onSymbolInput(e.target.value)}
              placeholder="Search any NSE/BSE stock… e.g. RELIANCE"
              className="bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60 w-64" />
            <datalist id="wl-search-list">
              {suggestions.map((s) => (
                <option key={`${s.symbol}:${s.exchange}`} value={s.symbol}>{s.exchange}</option>
              ))}
            </datalist>
            <span className="text-[11px] text-gray-500">any listed stock — not just F&amp;O</span>
          </>
        )}

        {/* Watchlist picker + manage */}
        {mode === 'watchlist' && (
          <>
            <select value={activeId} onChange={(e) => setActiveId(e.target.value)}
              className="bg-surface-3 border border-surface-4 rounded-lg px-3 py-1.5 text-sm text-gray-200 focus:outline-none focus:border-brand-500/60">
              {watchlists.length === 0 && <option value="">No watchlists yet</option>}
              {watchlists.map((w) => <option key={w.id} value={w.id}>{w.name} ({w.count})</option>)}
            </select>
            <button onClick={() => setManageOpen(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border bg-surface-3 text-gray-300 border-surface-4 hover:text-white transition">
              <Settings2 className="w-3.5 h-3.5" /> Manage
            </button>
            {active && <span className="text-xs text-gray-500">{active.symbols?.length || 0} stocks</span>}
          </>
        )}
      </div>

      {manageOpen && (
        <WatchlistManager
          names={names}
          watchlists={watchlists}
          activeId={activeId}
          onClose={() => setManageOpen(false)}
          onChanged={(selectId) => loadWatchlists(selectId)}
          onActiveChange={(id) => setActiveId(String(id))}
        />
      )}
    </>
  );
}

// ── Manage modal ──────────────────────────────────────────────────
function WatchlistManager({ names, watchlists, activeId, onClose, onChanged, onActiveChange }) {
  const [wid, setWid] = useState(activeId || '');
  const [wl, setWl] = useState(null);
  const [text, setText] = useState('');
  const [newName, setNewName] = useState('');
  const [addSym, setAddSym] = useState('');
  const [uploadName, setUploadName] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  const flash = (m) => { setMsg(m); setTimeout(() => setMsg(''), 2000); };
  const fail = (m) => { setErr(m); setTimeout(() => setErr(''), 4000); };

  const loadOne = useCallback((id) => {
    if (!id) { setWl(null); setText(''); return; }
    api.researchWatchlistGet(id).then((r) => {
      if (r.status === 'ok') { setWl(r.watchlist); setText((r.watchlist.symbols || []).join('\n')); }
    }).catch(() => {});
  }, []);

  useEffect(() => { loadOne(wid); }, [wid, loadOne]);

  const refresh = async (selectId) => { await onChanged?.(selectId); };

  const create = async () => {
    if (!newName.trim()) return;
    setBusy(true); setErr('');
    try {
      const r = await api.researchWatchlistCreate(newName.trim(), []);
      if (r.status === 'ok') { await refresh(r.watchlist.id); setWid(String(r.watchlist.id)); setNewName(''); flash('Created'); }
      else fail(r.message || 'Create failed');
    } catch (e) { fail(e.message); } finally { setBusy(false); }
  };

  const saveSymbols = async () => {
    if (!wid) return;
    setBusy(true); setErr('');
    try {
      const symbols = text.split(/[\s,;]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
      const r = await api.researchWatchlistUpdate(wid, { symbols });
      if (r.status === 'ok') { setWl(r.watchlist); setText((r.watchlist.symbols || []).join('\n')); await refresh(wid); flash('Saved'); }
      else fail(r.message || 'Save failed');
    } catch (e) { fail(e.message); } finally { setBusy(false); }
  };

  const rename = async () => {
    if (!wid || !wl?.name) return;
    setBusy(true);
    try {
      const r = await api.researchWatchlistUpdate(wid, { name: wl.name });
      if (r.status === 'ok') { await refresh(wid); flash('Renamed'); } else fail(r.message);
    } catch (e) { fail(e.message); } finally { setBusy(false); }
  };

  const addOne = async () => {
    const s = addSym.trim().toUpperCase();
    if (!s || !wid) return;
    const r = await api.researchWatchlistModify(wid, [s], null);
    if (r.status === 'ok') { setWl(r.watchlist); setText((r.watchlist.symbols || []).join('\n')); setAddSym(''); await refresh(wid); }
    else fail(r.message);
  };

  const removeOne = async (s) => {
    const r = await api.researchWatchlistModify(wid, null, [s]);
    if (r.status === 'ok') { setWl(r.watchlist); setText((r.watchlist.symbols || []).join('\n')); await refresh(wid); }
  };

  const del = async () => {
    if (!wid) return;
    setBusy(true);
    try {
      await api.researchWatchlistDelete(wid);
      setWid(''); setWl(null); setText(''); await refresh();
      flash('Deleted');
    } catch (e) { fail(e.message); } finally { setBusy(false); }
  };

  const download = () => {
    const body = (wl?.symbols || []).join('\n') + '\n';
    const blob = new Blob([body], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = `${wl?.name || 'watchlist'}.txt`;
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  };

  const upload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const name = (uploadName.trim() || file.name.replace(/\.[^.]+$/, '')).slice(0, 80);
    setBusy(true); setErr('');
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('name', name);
      const r = await api.researchWatchlistUpload(fd);
      if (r.status === 'ok') { await refresh(r.watchlist.id); setWid(String(r.watchlist.id)); setUploadName(''); flash(`Uploaded ${r.watchlist.count} symbols`); }
      else fail(r.message || 'Upload failed');
    } catch (e2) { fail(e2.message); } finally { setBusy(false); e.target.value = ''; }
  };

  useEffect(() => { if (wid) onActiveChange?.(wid); /* eslint-disable-next-line */ }, [wid]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="bg-surface-1 border border-surface-3 rounded-xl w-full max-w-3xl max-h-[85vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-surface-3">
          <div className="flex items-center gap-2 text-gray-100 font-semibold"><ListChecks className="w-4 h-4 text-brand-400" /> Manage Watchlists</div>
          <button onClick={onClose} className="text-gray-500 hover:text-white"><X className="w-4 h-4" /></button>
        </div>

        {(msg || err) && (
          <div className={`px-4 py-2 text-xs ${err ? 'text-red-400 bg-red-500/10' : 'text-emerald-400 bg-emerald-500/10'}`}>{err || msg}</div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-[220px_1fr] gap-0 flex-1 min-h-0">
          {/* Left: list + create + upload */}
          <div className="border-r border-surface-3 p-3 space-y-3 overflow-y-auto">
            <div className="space-y-1">
              {watchlists.length === 0 && <p className="text-xs text-gray-500">No watchlists yet.</p>}
              {watchlists.map((w) => (
                <button key={w.id} onClick={() => setWid(String(w.id))}
                  className={`w-full text-left px-2.5 py-1.5 rounded-lg text-sm ${String(w.id) === String(wid) ? 'bg-brand-600/20 text-brand-300 border border-brand-500/30' : 'text-gray-300 hover:bg-surface-3 border border-transparent'}`}>
                  {w.name} <span className="text-gray-500 text-xs">({w.count})</span>
                </button>
              ))}
            </div>
            <div className="pt-2 border-t border-surface-3 space-y-2">
              <div className="flex gap-1.5">
                <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="New watchlist name"
                  className="flex-1 bg-surface-3 border border-surface-4 rounded-lg px-2 py-1.5 text-xs text-gray-200" />
                <button onClick={create} disabled={busy || !newName.trim()} className="p-1.5 rounded-lg bg-brand-600 hover:bg-brand-700 text-white disabled:opacity-50"><Plus className="w-4 h-4" /></button>
              </div>
              <div>
                <input value={uploadName} onChange={(e) => setUploadName(e.target.value)} placeholder="Name for uploaded file (optional)"
                  className="w-full bg-surface-3 border border-surface-4 rounded-lg px-2 py-1.5 text-xs text-gray-200 mb-1.5" />
                <label className="flex items-center justify-center gap-1.5 px-2 py-1.5 text-xs rounded-lg border border-dashed border-surface-4 text-gray-300 hover:text-white cursor-pointer">
                  <Upload className="w-3.5 h-3.5" /> Upload TXT/CSV
                  <input type="file" accept=".txt,.csv" onChange={upload} className="hidden" />
                </label>
                <p className="text-[10px] text-gray-600 mt-1">Same name replaces that list (download → edit → re-upload).</p>
              </div>
            </div>
          </div>

          {/* Right: editor */}
          <div className="p-3 overflow-y-auto">
            {!wl ? (
              <div className="text-gray-500 text-sm py-10 text-center">Select or create a watchlist to edit.</div>
            ) : (
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <input value={wl.name} onChange={(e) => setWl({ ...wl, name: e.target.value })}
                    className="flex-1 bg-surface-3 border border-surface-4 rounded-lg px-2.5 py-1.5 text-sm text-gray-100 font-semibold" />
                  <button onClick={rename} disabled={busy} className="p-1.5 rounded-lg bg-surface-3 border border-surface-4 text-gray-300 hover:text-white" title="Rename"><Check className="w-4 h-4" /></button>
                  <button onClick={download} className="p-1.5 rounded-lg bg-surface-3 border border-surface-4 text-gray-300 hover:text-white" title="Download"><Download className="w-4 h-4" /></button>
                  <button onClick={del} disabled={busy} className="p-1.5 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 hover:bg-red-500/20" title="Delete"><Trash2 className="w-4 h-4" /></button>
                </div>

                {/* Quick add */}
                <div className="flex gap-1.5">
                  <input list="wl-mng-list" value={addSym} onChange={(e) => setAddSym(e.target.value.toUpperCase())}
                    onKeyDown={(e) => { if (e.key === 'Enter') addOne(); }}
                    placeholder="Add a stock… (Enter)" className="flex-1 bg-surface-3 border border-surface-4 rounded-lg px-2.5 py-1.5 text-sm text-gray-200" />
                  <datalist id="wl-mng-list">{names.map((n) => <option key={n} value={n} />)}</datalist>
                  <button onClick={addOne} className="px-3 py-1.5 text-xs rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold">Add</button>
                </div>

                {/* Chips */}
                <div className="flex flex-wrap gap-1.5 max-h-32 overflow-y-auto">
                  {(wl.symbols || []).map((s) => (
                    <span key={s} className="flex items-center gap-1 px-2 py-0.5 rounded-md bg-surface-3 border border-surface-4 text-xs text-gray-300">
                      {s}
                      <button onClick={() => removeOne(s)} className="text-gray-500 hover:text-red-400"><X className="w-3 h-3" /></button>
                    </span>
                  ))}
                  {(!wl.symbols || wl.symbols.length === 0) && <span className="text-xs text-gray-600">Empty — add stocks or edit below.</span>}
                </div>

                {/* Bulk edit */}
                <div>
                  <label className="block text-[10px] text-gray-500 uppercase tracking-wide mb-1">Bulk edit (one symbol per line / comma-separated)</label>
                  <textarea value={text} onChange={(e) => setText(e.target.value)} rows={8}
                    className="w-full bg-surface-3 border border-surface-4 rounded-lg px-2.5 py-2 text-xs text-gray-200 font-mono resize-y" />
                  <div className="flex justify-end mt-2">
                    <button onClick={saveSymbols} disabled={busy} className="flex items-center gap-1.5 px-4 py-1.5 text-sm rounded-lg bg-brand-600 hover:bg-brand-700 text-white font-semibold disabled:opacity-50">
                      {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} Save Symbols
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
