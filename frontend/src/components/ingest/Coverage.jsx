import React, { useState } from 'react';
import { CheckCircle2, XCircle, Lock, Trash2, CalendarX } from 'lucide-react';
import { api } from '../../api';

/**
 * What the Market Store holds, per underlying: readiness for the OI Lab, a month grid, missing
 * weekdays (holidays or not uploaded yet) and removal of a wrongly uploaded month.
 */

// India VIX and futures are stored as bars only — they are context, not an OI Lab underlying
const REFERENCE = /^(INDIAVIX|.*FUT)$/;
const N = (v) => Number(v || 0).toLocaleString('en-IN');
const MB = (b) => `${(Number(b || 0) / 1e6).toFixed(1)} MB`;

function DeleteDialog({ target, onClose, onDone }) {
  const want = `${target.underlying} ${target.label}`;
  const [text, setText] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    const r = await api.diDeletePartition({ kind: target.kind, underlying: target.underlying, year: target.year, month: target.month, confirm: text });
    setBusy(false);
    if (r.status === 'ok') onDone(); else setMsg(r.message);
  };
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative card max-w-md w-full space-y-3">
        <div className="text-[14px] font-semibold text-gray-100">Remove {target.underlying} {target.kind} for {target.label}?</div>
        <div className="text-[12.5px] text-gray-400">
          Deletes {N(target.rows)} bars from this server's disk and from the database copy. Other months are untouched.
          You can upload the month again afterwards. This cannot be undone.
        </div>
        <label className="block text-[12px] text-gray-300">Type <span className="mono text-white">{want}</span> to confirm
          <input value={text} onChange={(e) => setText(e.target.value)} className="input-field w-full mt-1" autoFocus />
        </label>
        {msg && <div className="text-[12px] text-red-400">{msg}</div>}
        <div className="flex justify-end gap-2">
          <button onClick={onClose} className="btn-ghost !py-1.5 text-[12.5px]">Cancel</button>
          <button onClick={go} disabled={busy || text.trim().toUpperCase() !== want} className="btn-danger !py-1.5 text-[12.5px] disabled:opacity-50">Remove month</button>
        </div>
      </div>
    </div>
  );
}

export default function Coverage({ data, reload }) {
  const [del, setDel] = useState(null);
  if (!data) return <div className="text-[12.5px] text-gray-500 py-6 text-center">Scanning the Market Store…</div>;
  const series = data.series || [];
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {(data.underlyings || []).map((u) => (
          <div key={u.underlying} className="card !p-3 space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-[14px] font-bold text-gray-100">{u.underlying}</span>
              {u.oi_lab_ready ? <span className="badge-green !text-[10px]">OI Lab ready</span>
                : <span className="badge !text-[10px] text-gray-400">{REFERENCE.test(u.underlying) ? 'reference series' : 'no options'}</span>}
            </div>
            {[['Index / spot', u.has_spot, u.sessions_spot], ['Options', u.has_options, u.sessions_options]].map(([k, ok, n]) => (
              <div key={k} className="flex items-center gap-1.5 text-[12px]">
                {ok ? <CheckCircle2 className="w-3.5 h-3.5 text-green-400" /> : <XCircle className="w-3.5 h-3.5 text-gray-600" />}
                <span className="text-gray-300">{k}</span><span className="ml-auto mono text-gray-400">{ok ? `${N(n)} sessions` : '—'}</span>
              </div>
            ))}
            {u.has_options && !u.has_spot && <div className="text-[11px] text-amber-500">Upload the index file too — spot highs/lows sharpen the wall tests.</div>}
          </div>
        ))}
      </div>
      {series.map((s) => {
        const byYear = {};
        s.months.forEach((m) => { (byYear[m.year] = byYear[m.year] || {})[m.month] = m; });
        return (
          <div key={`${s.underlying}-${s.kind}`} className="card !p-4 space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="text-[13px] font-semibold text-gray-100">{s.underlying} · {s.kind === 'options' ? 'options' : 'index / spot'}</div>
              <div className="text-[11.5px] text-gray-500 mono">{N(s.rows)} bars · {N(s.sessions)} sessions · {s.first_day} → {s.last_day} · {MB(s.bytes)}</div>
            </div>
            <div className="overflow-x-auto">
              <table className="text-[11px]">
                <thead><tr><th />{['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'].map((m, i) => <th key={i} className="px-1 font-medium text-gray-500 w-14">{m}</th>)}</tr></thead>
                <tbody>{Object.keys(byYear).sort().map((y) => (
                  <tr key={y}>
                    <td className="pr-2 text-gray-400 mono">{y}</td>
                    {Array.from({ length: 12 }, (_, i) => byYear[y][i + 1]).map((m, i) => (
                      <td key={i} className="p-0.5">
                        {m ? (
                          <div className="group relative rounded-md bg-brand-600/20 border border-brand-500/25 px-1 py-1 text-center" title={`${m.label}: ${N(m.rows)} bars, ${m.sessions} sessions${m.contracts ? `, ${m.contracts} contracts` : ''}`}>
                            <div className="mono text-gray-100">{m.sessions}d</div>
                            {m.bundled ? <Lock className="w-3 h-3 text-gray-500 absolute top-0.5 right-0.5" aria-label="bundled" />
                              : <button onClick={() => setDel({ ...m, kind: s.kind, underlying: s.underlying })} className="absolute top-0 right-0 p-0.5 opacity-0 group-hover:opacity-100 text-red-400" aria-label={`Remove ${m.label}`}><Trash2 className="w-3 h-3" /></button>}
                          </div>
                        ) : <div className="rounded-md border border-dashed border-surface-3 h-7" />}
                      </td>
                    ))}
                  </tr>
                ))}</tbody>
              </table>
            </div>
            {s.weekday_gap_count > 0 && (
              <details>
                <summary className="text-[11.5px] text-gray-400 cursor-pointer flex items-center gap-1.5"><CalendarX className="w-3.5 h-3.5" />{s.weekday_gap_count} weekdays with no bars between first and last day (holidays, or not uploaded yet)</summary>
                <div className="flex flex-wrap gap-1 mt-1.5">{s.weekday_gaps.map((d) => <span key={d} className="text-[10.5px] mono px-1.5 py-0.5 rounded bg-surface-2 text-gray-400">{d}</span>)}</div>
              </details>
            )}
          </div>
        );
      })}
      <div className="text-[11px] text-gray-500 flex items-center gap-1.5"><Lock className="w-3.5 h-3.5" />Locked months are the bundled 3-year history shipped with the app; they are restored automatically and can't be removed.</div>
      {del && <DeleteDialog target={del} onClose={() => setDel(null)} onDone={() => { setDel(null); reload(); }} />}
    </div>
  );
}
