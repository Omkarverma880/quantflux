import React, { useRef, useState } from 'react';
import { X, Loader2, Upload, Download, FileSpreadsheet, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { api, API_BASE } from '../../api';

/**
 * The workspace as a spreadsheet, both ways.
 *
 * Import always runs as a dry run first: you see exactly which stocks would be added, which
 * updated and which refused, before a single row is written. A column the file does not carry
 * is left alone rather than blanked — an import can add to your research, never quietly erase it.
 */

const ACTION_TONE = { add: 'text-emerald-400', update: 'text-brand-300', error: 'text-red-400' };

export default function Transfer({ onClose, onImported }) {
  const [text, setText] = useState('');
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [done, setDone] = useState(null);
  const fileRef = useRef(null);

  const download = (path, filename) => {
    const token = localStorage.getItem('app_token');
    fetch(`${API_BASE}${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then((r) => r.blob())
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = filename; a.click();
        URL.revokeObjectURL(url);
      })
      .catch((e) => setErr(String(e.message || e)));
  };

  const run = async (dry, body) => {
    setBusy(true); setErr('');
    try {
      const r = body instanceof File
        ? await api.meImportFile(body, dry)
        : await api.meImport({ text: body, dry_run: dry });
      if (r.status !== 'ok') { setErr(r.message || 'could not read that file'); setPreview(null); return; }
      if (dry) setPreview(r); else { setDone(r); onImported?.(); }
    } catch (e) { setErr(String(e.message || e)); } finally { setBusy(false); }
  };

  const pick = (file) => {
    if (!file) return;
    setPreview(null); setDone(null);
    const reader = new FileReader();
    reader.onload = () => setText(String(reader.result || ''));
    reader.readAsText(file);
    run(true, file);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="w-full max-w-3xl card !p-4 space-y-3 max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between">
          <div>
            <div className="text-[14px] font-semibold text-gray-100 flex items-center gap-2">
              <FileSpreadsheet className="w-4 h-4 text-brand-400" />Import and export
            </div>
            <div className="text-[11.5px] text-gray-500">
              Bring a watchlist in from Excel, or take your whole workspace out.
            </div>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-200"><X className="w-4 h-4" /></button>
        </div>

        <div className="flex flex-wrap gap-2">
          <button onClick={() => download('/equity-strategy/my-workspace/export.csv', 'my-equity-workspace.csv')}
            className="btn-secondary !py-1.5 !px-3 text-[12.5px] flex items-center gap-1.5">
            <Download className="w-3.5 h-3.5" />Export my workspace
          </button>
          <button onClick={() => download('/equity-strategy/my-workspace/import/template.csv', 'workspace-template.csv')}
            className="btn-secondary !py-1.5 !px-3 text-[12.5px] flex items-center gap-1.5">
            <Download className="w-3.5 h-3.5" />Download the template
          </button>
          <button onClick={() => fileRef.current?.click()}
            className="btn-primary !py-1.5 !px-3 text-[12.5px] flex items-center gap-1.5">
            <Upload className="w-3.5 h-3.5" />Choose a CSV
          </button>
          <input ref={fileRef} type="file" accept=".csv,.txt" className="hidden"
            onChange={(e) => pick(e.target.files?.[0])} />
        </div>

        <div>
          <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">…or paste the rows</div>
          <textarea value={text} onChange={(e) => { setText(e.target.value); setPreview(null); setDone(null); }}
            rows={5} spellCheck={false}
            placeholder={'symbol,category,researched_on,levels,targets,stops,note\nBSE,SWING,2026-09-01,3100|2900,3400|3250,2950|2800,breakout retest'}
            className="input-field w-full text-[12px] mono" />
          <div className="flex items-center gap-2 mt-2">
            <button onClick={() => run(true, text)} disabled={busy || !text.trim()}
              className="btn-secondary !py-1.5 !px-3 text-[12.5px] disabled:opacity-50">
              {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : 'Check it'}
            </button>
            <span className="text-[11px] text-gray-500">
              Headings can be yours — “Stock”, “Entry Levels”, “Stop Loss”, “Research Date” are all understood.
            </span>
          </div>
        </div>

        {err && <div className="text-[12px] text-red-400 flex items-start gap-1.5"><AlertTriangle className="w-4 h-4 shrink-0 mt-px" />{err}</div>}

        {preview && !done && (
          <div className="space-y-2">
            <div className="text-[12.5px] text-gray-200">
              <span className="text-emerald-400 font-semibold">{preview.added}</span> to add ·{' '}
              <span className="text-brand-300 font-semibold">{preview.updated}</span> to update
              {preview.failed > 0 && <> · <span className="text-red-400 font-semibold">{preview.failed}</span> refused</>}
            </div>
            <div className="max-h-56 overflow-y-auto rounded-lg border border-surface-3">
              <table className="w-full text-[12px]">
                <thead className="sticky top-0 bg-surface-1"><tr className="text-[10px] uppercase tracking-wider text-gray-500 border-b border-surface-3">
                  {['Line', 'Symbol', 'Type', 'Researched', 'Levels', 'What happens'].map((h) => <th key={h} className="text-left px-2 py-1 font-medium">{h}</th>)}
                </tr></thead>
                <tbody>
                  {preview.rows.map((r) => (
                    <tr key={r.line} className="border-b border-surface-3/40">
                      <td className="px-2 py-1 text-gray-500 mono">{r.line}</td>
                      <td className="px-2 py-1 text-gray-100 font-semibold">{r.symbol}</td>
                      <td className="px-2 py-1 text-gray-400">{r.category === 'INVESTMENT' ? 'Investment' : 'Swing'}</td>
                      <td className="px-2 py-1 mono text-gray-400">{r.added_on || 'today'}</td>
                      <td className="px-2 py-1 mono text-gray-300">
                        {(r.levels || []).map((l) => `${l.price}${l.target ? `→${l.target}` : ''}${l.stop ? `↓${l.stop}` : ''}`).join('  ') || '—'}
                      </td>
                      <td className={`px-2 py-1 ${ACTION_TONE[r.action] || 'text-gray-400'}`}>
                        {r.action === 'error' ? r.message : r.action}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {(preview.problems || []).map((p, i) => (
              <div key={i} className="text-[11.5px] text-amber-500 flex items-start gap-1.5">
                <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" />{p}
              </div>
            ))}
            <button onClick={() => run(false, text)} disabled={busy || (!preview.added && !preview.updated)}
              className="btn-primary !py-1.5 !px-4 text-[12.5px] disabled:opacity-50">
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : `Import ${preview.added + preview.updated} stock${preview.added + preview.updated === 1 ? '' : 's'}`}
            </button>
          </div>
        )}

        {done && (
          <div className="space-y-2">
            <div className="text-[12.5px] text-emerald-400 flex items-center gap-1.5">
              <CheckCircle2 className="w-4 h-4" />
              Imported — {done.added} added, {done.updated} updated{done.failed ? `, ${done.failed} refused` : ''}.
            </div>
            <button onClick={onClose} className="btn-secondary !py-1.5 !px-3 text-[12.5px]">Close</button>
          </div>
        )}

        <div className="text-[11px] text-gray-500 border-t border-surface-3 pt-2">
          Levels, targets and stops line up by position: <span className="mono">3100|2900</span> with
          <span className="mono"> 3400|3250</span> and <span className="mono">2950|2800</span> means 3100 aims at 3400 with a
          stop at 2950. A symbol that is not listed on NSE or BSE is refused rather than guessed.
        </div>
      </div>
    </div>
  );
}
