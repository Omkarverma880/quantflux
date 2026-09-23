import React, { useEffect, useState } from 'react';
import { SlidersHorizontal, Loader2 } from 'lucide-react';
import { api } from '../../api';

/**
 * How wide the net is. Every number here is a real gate in the scan, so loosening one shows up as
 * more names on the board — and the page says which gate let them in.
 */
const FIELDS = [
  ['min_rs', 'RS floor', 'A forming base only counts in a stock stronger than this % of the universe. Lower it to see more names.', 0, 95, 5],
  ['base_max_depth', 'Deepest base (%)', 'How far a base may fall from its own high before it stops being a rest and becomes a decline.', 10, 60, 5],
  ['near_pivot_pct', 'How near the ceiling (%)', 'A stock further below the ceiling than this is still building, not ready.', 3, 30, 1],
  ['base_min', 'Shortest base (days)', 'Fewer sessions than this is a pause, not a base.', 5, 30, 1],
  ['base_max', 'Longest base (days)', 'The longest range the scan will treat as one base.', 20, 120, 5],
  ['min_turnover_cr', 'Turnover floor (₹ cr)', 'Median value traded a day. Raise it to keep only names you can size into.', 0.5, 25, 0.5],
];

export default function Tuning({ config, onSaved, onRescan, busy }) {
  const [open, setOpen] = useState(false);
  const [c, setC] = useState(config || {});
  const [saving, setSaving] = useState(false);
  useEffect(() => setC(config || {}), [config]);

  const save = async (rescan) => {
    setSaving(true);
    try {
      const r = await api.huConfig(c);
      if (r.status === 'ok') {
        onSaved?.(r.config);
        setOpen(false);
        if (rescan) onRescan?.();
      }
    } finally { setSaving(false); }
  };

  return (
    <div className="relative">
      <button onClick={() => setOpen(!open)} className="btn-secondary !py-1.5 !px-3 text-[12px] flex items-center gap-1.5">
        <SlidersHorizontal className="w-3.5 h-3.5" />Tuning
      </button>
      {open && (
        <div className="absolute right-0 top-[130%] z-30 w-[340px] card !p-3 space-y-2.5">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-gray-500">Universe</div>
            <select value={c.universe || 'nifty500'} onChange={(e) => setC({ ...c, universe: e.target.value })}
              className="input-field !py-1.5 w-full text-[12px] mt-0.5">
              <option value="nifty500">NIFTY 500 — about 3 minutes a scan</option>
              <option value="nse_liquid">Every liquid NSE stock — first scan 15–20 minutes</option>
            </select>
            <div className="text-[10.5px] text-gray-500 mt-0.5">
              The wider list is where most base setups are: smaller names build them more often.
            </div>
          </div>
          {FIELDS.map(([k, label, tip, min, max, step]) => (
            <label key={k} title={tip} className="block cursor-help">
              <div className="flex items-center justify-between">
                <span className="text-[11.5px] text-gray-300">{label}</span>
                <span className="text-[11.5px] mono text-gray-100">{c[k]}</span>
              </div>
              <input type="range" min={min} max={max} step={step} value={c[k] ?? min}
                onChange={(e) => setC({ ...c, [k]: Number(e.target.value) })} className="w-full" />
            </label>
          ))}
          <label className="flex items-center gap-2 cursor-pointer"
            title="On: a base must both calm down and dry up. Off: either one is enough, which is how most screeners do it.">
            <input type="checkbox" checked={!!c.strict_base} onChange={(e) => setC({ ...c, strict_base: e.target.checked })} />
            <span className="text-[11.5px] text-gray-300">Strict bases (contraction and dry-up)</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer" title="Scan by itself every trading day after the close.">
            <input type="checkbox" checked={c.auto_scan !== false} onChange={(e) => setC({ ...c, auto_scan: e.target.checked })} />
            <span className="text-[11.5px] text-gray-300">Scan automatically after the close</span>
          </label>
          <div className="flex gap-2 pt-1">
            <button onClick={() => save(true)} disabled={saving || busy} className="btn-primary !py-1.5 !px-3 text-[12px] flex-1 flex items-center justify-center gap-1.5">
              {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}Save and re-scan
            </button>
            <button onClick={() => save(false)} disabled={saving} className="btn-secondary !py-1.5 !px-3 text-[12px]">Save</button>
          </div>
        </div>
      )}
    </div>
  );
}
