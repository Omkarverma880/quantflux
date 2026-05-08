import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';

/*
 * RiskPanel — shared risk-management widget for Strategy 6 / 7 / 8 / 9.
 *
 * Props:
 *   strategyNum   : 6 | 7 | 8 | 9
 *   risk          : current `risk` payload from the strategy /status response
 *   onChange      : optional () => void  (parent should refresh status)
 *   compact       : optional bool — reduces padding for narrow side-rails
 */
export default function RiskPanel({ strategyNum, risk, onChange, compact = false }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [editing, setEditing] = useState(false);
  const [cfgDraft, setCfgDraft] = useState(null);
  const cooldownTickRef = useRef(null);
  const [, force] = useState(0);

  // Tick cooldown countdown each second so the badge updates locally
  useEffect(() => {
    if (cooldownTickRef.current) clearInterval(cooldownTickRef.current);
    if (risk?.cooldown_remaining_s > 0) {
      cooldownTickRef.current = setInterval(() => force((n) => n + 1), 1000);
    }
    return () => cooldownTickRef.current && clearInterval(cooldownTickRef.current);
  }, [risk?.cooldown_remaining_s]);

  const cfg = risk?.config || {};
  const cooldownLeft = useMemo(() => {
    const base = risk?.cooldown_remaining_s || 0;
    return Math.max(0, base);
  }, [risk?.cooldown_remaining_s]);

  if (!risk) return null;

  const mode = risk.mode || 'ACTIVE';
  const modeMeta = {
    ACTIVE:                { color: 'bg-emerald-500',  text: 'ACTIVE',                  ring: 'ring-emerald-400/30' },
    COOLDOWN:              { color: 'bg-amber-500',    text: `COOLDOWN ${cooldownLeft}s`, ring: 'ring-amber-400/30' },
    PAUSED_AFTER_SL:       { color: 'bg-orange-600',   text: 'PAUSED AFTER SL',         ring: 'ring-orange-400/30' },
    AWAITING_CONFIRMATION: { color: 'bg-rose-600',     text: 'AWAITING CONFIRMATION',   ring: 'ring-rose-400/30' },
    HALTED:                { color: 'bg-red-700',      text: 'HALTED',                  ring: 'ring-red-400/30' },
  }[mode] || { color: 'bg-slate-500', text: mode, ring: 'ring-slate-400/30' };

  const startEdit = () => {
    setCfgDraft({ ...cfg });
    setEditing(true);
    setErr('');
  };

  const action = async (fn) => {
    setBusy(true); setErr('');
    try {
      await fn();
      onChange?.();
    } catch (e) {
      setErr(e?.message || 'Action failed');
    } finally {
      setBusy(false);
    }
  };

  const saveConfig = () =>
    action(async () => {
      await api.updateRiskConfig(strategyNum, cfgDraft);
      setEditing(false);
    });

  const resume   = () => action(() => api.resumeRisk(strategyNum));
  const pause    = () => action(() => api.pauseRisk(strategyNum));
  const reset    = () => action(() => api.resetRiskCounters(strategyNum));

  return (
    <div className={`rounded-xl border border-slate-800 bg-slate-900/60 ${compact ? 'p-3' : 'p-4'}`}>
      {/* Header */}
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          <h3 className="text-sm font-semibold text-slate-200">Risk &amp; Re-entry Control</h3>
          <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-bold text-white ${modeMeta.color} ring-2 ${modeMeta.ring}`}>
            <span className={`w-1.5 h-1.5 rounded-full bg-white ${mode === 'ACTIVE' ? 'animate-pulse' : ''}`} />
            {modeMeta.text}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          {!editing && (
            <button onClick={startEdit} className="px-2 py-1 text-[11px] rounded bg-slate-700 hover:bg-slate-600 text-slate-100">
              Edit Rules
            </button>
          )}
          <button onClick={reset} disabled={busy} className="px-2 py-1 text-[11px] rounded bg-slate-700 hover:bg-slate-600 text-slate-100 disabled:opacity-50">
            Reset Counters
          </button>
        </div>
      </div>

      {/* Awaiting-confirmation banner */}
      {risk.awaiting_confirmation && (
        <div className="mt-3 rounded-lg border border-rose-500/40 bg-rose-950/40 p-3">
          <div className="flex items-start gap-3">
            <div className="text-rose-400 text-lg leading-none">!</div>
            <div className="flex-1 min-w-0">
              <div className="text-rose-200 font-semibold text-sm">Stop-loss hit — manual confirmation required</div>
              <div className="text-rose-300/80 text-xs mt-1">
                Strategy will not auto re-enter until you click <b>Resume</b>.
                {risk.last_block_reason ? <span className="block mt-1 text-rose-200/70">Last block: {risk.last_block_reason}</span> : null}
              </div>
              <div className="mt-2 flex gap-2">
                <button onClick={resume} disabled={busy} className="px-3 py-1.5 text-xs rounded bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-50">
                  Resume Strategy
                </button>
                <button onClick={pause} disabled={busy} className="px-3 py-1.5 text-xs rounded bg-slate-700 hover:bg-slate-600 text-slate-100 disabled:opacity-50">
                  Keep Paused
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Halted banner */}
      {risk.halted && !risk.awaiting_confirmation && (
        <div className="mt-3 rounded-lg border border-red-500/40 bg-red-950/40 p-3">
          <div className="flex items-start gap-2">
            <div className="text-red-400 text-lg leading-none">×</div>
            <div className="flex-1 min-w-0">
              <div className="text-red-200 font-semibold text-sm">Strategy HALTED — daily limit reached</div>
              <div className="text-red-300/80 text-xs mt-1">{risk.halt_reason || 'Limit exceeded'}</div>
              <div className="mt-2 flex gap-2">
                <button onClick={reset} disabled={busy} className="px-3 py-1.5 text-xs rounded bg-amber-600 hover:bg-amber-500 text-white disabled:opacity-50">
                  Reset &amp; Resume
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Counters grid */}
      <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2">
        <Stat label="SL Hits Today"    value={`${risk.sl_hits_today ?? 0} / ${cfg.max_sl_hits_per_day ?? 0}`}     tone={risk.sl_hits_today >= (cfg.max_sl_hits_per_day || 99) ? 'danger' : 'ok'} />
        <Stat label="Consec. Losses"   value={`${risk.consecutive_losses ?? 0} / ${cfg.max_consecutive_losses ?? 0}`} tone={risk.consecutive_losses >= (cfg.max_consecutive_losses || 99) ? 'danger' : 'ok'} />
        <Stat label="Re-entries"       value={`${risk.reentries_today ?? 0} / ${cfg.max_reentries_per_day ?? 0}`}   tone={risk.reentries_today >= (cfg.max_reentries_per_day || 99) ? 'warn' : 'ok'} />
        <Stat label="Cooldown"         value={cooldownLeft > 0 ? `${cooldownLeft}s` : '—'} tone={cooldownLeft > 0 ? 'warn' : 'ok'} />
      </div>

      {/* Last exit + fresh-crossover row */}
      <div className="mt-2 flex items-center justify-between gap-3 text-[11px] text-slate-400 flex-wrap">
        <div>
          Last exit:{' '}
          <span className={`font-semibold ${risk.last_exit_type === 'SL_HIT' ? 'text-rose-400' : risk.last_exit_type === 'TARGET_HIT' ? 'text-emerald-400' : 'text-slate-300'}`}>
            {risk.last_exit_type || '—'}
          </span>
          {risk.last_exit_side ? <span className="text-slate-500"> · {risk.last_exit_side} @ {Number(risk.last_exit_line || 0).toFixed(2)}</span> : null}
        </div>
        <div>
          Fresh crossover:{' '}
          <span className={risk.fresh_crossover_armed ? 'text-emerald-400' : 'text-amber-400'}>
            {risk.fresh_crossover_armed ? 'ARMED' : 'WAITING'}
          </span>
        </div>
      </div>

      {risk.last_block_reason && !risk.awaiting_confirmation && !risk.halted && (
        <div className="mt-2 text-[11px] text-amber-300 bg-amber-950/30 border border-amber-700/30 rounded px-2 py-1">
          Blocked: {risk.last_block_reason}
        </div>
      )}

      {/* Config editor */}
      {editing && cfgDraft && (
        <div className="mt-3 rounded-lg border border-slate-700 bg-slate-950/40 p-3 space-y-2">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <Toggle label="Allow re-entry after TARGET"    value={cfgDraft.allow_reentry_after_target}    onChange={(v) => setCfgDraft((d) => ({ ...d, allow_reentry_after_target: v }))} />
            <Toggle label="Allow re-entry after SL"        value={cfgDraft.allow_reentry_after_sl}        onChange={(v) => setCfgDraft((d) => ({ ...d, allow_reentry_after_sl: v }))} />
            <Toggle label="Manual confirmation after SL"   value={cfgDraft.require_manual_confirmation_after_sl} onChange={(v) => setCfgDraft((d) => ({ ...d, require_manual_confirmation_after_sl: v }))} />
            <Toggle label="Auto-pause after SL"            value={cfgDraft.auto_pause_after_sl}           onChange={(v) => setCfgDraft((d) => ({ ...d, auto_pause_after_sl: v }))} />
            <Toggle label="Require fresh crossover"        value={cfgDraft.require_fresh_crossover}       onChange={(v) => setCfgDraft((d) => ({ ...d, require_fresh_crossover: v }))} />
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
            <Num label="Max SL hits / day"        value={cfgDraft.max_sl_hits_per_day}        onChange={(v) => setCfgDraft((d) => ({ ...d, max_sl_hits_per_day: v }))} />
            <Num label="Max consec. losses"      value={cfgDraft.max_consecutive_losses}     onChange={(v) => setCfgDraft((d) => ({ ...d, max_consecutive_losses: v }))} />
            <Num label="Max re-entries / day"    value={cfgDraft.max_reentries_per_day}      onChange={(v) => setCfgDraft((d) => ({ ...d, max_reentries_per_day: v }))} />
            <Num label="Cooldown (sec)"          value={cfgDraft.entry_cooldown_seconds}     onChange={(v) => setCfgDraft((d) => ({ ...d, entry_cooldown_seconds: v }))} />
            <Num label="Fresh-crossover dist."   value={cfgDraft.fresh_crossover_distance}   step="0.1" onChange={(v) => setCfgDraft((d) => ({ ...d, fresh_crossover_distance: v }))} />
          </div>
          {err && <div className="text-xs text-rose-400">{err}</div>}
          <div className="flex gap-2 pt-1">
            <button onClick={saveConfig} disabled={busy} className="px-3 py-1.5 text-xs rounded bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50">Save Rules</button>
            <button onClick={() => { setEditing(false); setErr(''); }} disabled={busy} className="px-3 py-1.5 text-xs rounded bg-slate-700 hover:bg-slate-600 text-slate-100 disabled:opacity-50">Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, tone = 'ok' }) {
  const toneCls =
    tone === 'danger' ? 'text-rose-300 border-rose-700/40 bg-rose-950/30'
    : tone === 'warn' ? 'text-amber-300 border-amber-700/40 bg-amber-950/30'
    : 'text-slate-200 border-slate-700/60 bg-slate-900/60';
  return (
    <div className={`rounded border px-2.5 py-1.5 ${toneCls}`}>
      <div className="text-[10px] uppercase tracking-wide opacity-70">{label}</div>
      <div className="text-sm font-semibold leading-tight">{value}</div>
    </div>
  );
}

function Toggle({ label, value, onChange }) {
  return (
    <label className="flex items-center justify-between gap-2 px-2 py-1.5 rounded bg-slate-900/40 border border-slate-700/50 cursor-pointer">
      <span className="text-[11px] text-slate-300">{label}</span>
      <input
        type="checkbox"
        checked={!!value}
        onChange={(e) => onChange(e.target.checked)}
        className="accent-blue-600"
      />
    </label>
  );
}

function Num({ label, value, onChange, step = '1' }) {
  return (
    <label className="flex flex-col gap-1 px-2 py-1 rounded bg-slate-900/40 border border-slate-700/50">
      <span className="text-[10px] uppercase tracking-wide text-slate-400">{label}</span>
      <input
        type="number"
        step={step}
        value={value ?? 0}
        onChange={(e) => onChange(Number(e.target.value))}
        className="bg-transparent text-sm text-slate-100 outline-none w-full"
      />
    </label>
  );
}
