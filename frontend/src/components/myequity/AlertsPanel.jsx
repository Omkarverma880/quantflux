import React, { useEffect, useState } from 'react';
import { X, Loader2, Bell, Send, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { api } from '../../api';

/**
 * Telegram alerts for the workspace.
 *
 * Off until you turn them on, and silent unless Telegram is configured in Settings. The times
 * are yours: the morning digest lists what is close to a level, the closing one what actually
 * happened. Individual stocks are muted with the bell on their row.
 */

const Row = ({ label, hint, children }) => (
  <div className="flex items-start justify-between gap-3 py-2 border-b border-surface-3/60 last:border-0">
    <div className="min-w-0">
      <div className="text-[12.5px] text-gray-200">{label}</div>
      {hint && <div className="text-[11px] text-gray-500">{hint}</div>}
    </div>
    <div className="shrink-0">{children}</div>
  </div>
);

const Toggle = ({ on, onChange, disabled }) => (
  <button onClick={() => onChange(!on)} disabled={disabled}
    className={`w-9 h-5 rounded-full transition relative ${on ? 'bg-brand-500' : 'bg-surface-4'} disabled:opacity-40`}>
    <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${on ? 'left-[18px]' : 'left-0.5'}`} />
  </button>
);

export default function AlertsPanel({ onClose }) {
  const [cfg, setCfg] = useState(null);
  const [ready, setReady] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  useEffect(() => {
    api.meAlertsConfig().then((r) => {
      if (r.status === 'ok') { setCfg(r.config); setReady(r.telegram_ready); }
    }).catch(() => {});
  }, []);

  const save = async (patch) => {
    const next = { ...cfg, ...patch };
    setCfg(next);
    setBusy(true);
    try {
      const r = await api.meAlertsSave(patch);
      if (r.status === 'ok') { setCfg(r.config); setReady(r.telegram_ready); }
    } finally { setBusy(false); }
  };

  const test = async () => {
    setBusy(true); setMsg(null);
    try {
      const r = await api.meAlertsTest();
      setMsg(r.status === 'ok'
        ? { tone: 'ok', text: 'Sent — check Telegram.', preview: r.preview }
        : { tone: 'err', text: r.message || 'could not send' });
    } catch (e) { setMsg({ tone: 'err', text: String(e.message || e) }); } finally { setBusy(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div className="w-full max-w-lg card !p-4 space-y-3 max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between">
          <div>
            <div className="text-[14px] font-semibold text-gray-100 flex items-center gap-2">
              <Bell className="w-4 h-4 text-brand-400" />Alerts
            </div>
            <div className="text-[11.5px] text-gray-500">
              Told to you on Telegram while the market is open, so you do not have to watch the screen.
            </div>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-200"><X className="w-4 h-4" /></button>
        </div>

        {!cfg ? <div className="py-8 text-center text-gray-500"><Loader2 className="w-5 h-5 animate-spin mx-auto" /></div> : (
          <>
            {!ready && (
              <div className="text-[12px] text-amber-500 flex items-start gap-1.5 border border-amber-500/30 bg-amber-500/10 rounded-lg p-2">
                <AlertTriangle className="w-4 h-4 shrink-0 mt-px" />
                Telegram is not connected yet — add the bot token and chat id in Settings, then come back. Everything
                here stays silent until then.
              </div>
            )}

            <div className="rounded-lg border border-surface-3 px-3">
              <Row label="Send alerts" hint="the master switch for this workspace">
                <Toggle on={!!cfg.enabled} onChange={(v) => save({ enabled: v })} disabled={busy} />
              </Row>
              <Row label="Level reached" hint="price reaches, or comes back to, a level you wrote down">
                <Toggle on={!!cfg.level_alerts} onChange={(v) => save({ level_alerts: v })} disabled={busy || !cfg.enabled} />
              </Row>
              <Row label="Target and stop" hint="a triggered level runs to your target, or breaks your stop">
                <Toggle on={!!cfg.exit_alerts} onChange={(v) => save({ exit_alerts: v })} disabled={busy || !cfg.enabled} />
              </Row>
              <Row label="Morning digest" hint="what is close to a level today">
                <div className="flex items-center gap-2">
                  <input type="time" value={cfg.morning_at} onChange={(e) => save({ morning_at: e.target.value })}
                    disabled={busy || !cfg.enabled} className="input-field !py-1 !px-2 text-[12px]" />
                  <Toggle on={!!cfg.morning_digest} onChange={(v) => save({ morning_digest: v })} disabled={busy || !cfg.enabled} />
                </div>
              </Row>
              <Row label="Closing digest" hint="what triggered, what hit target, what stopped out">
                <div className="flex items-center gap-2">
                  <input type="time" value={cfg.close_at} onChange={(e) => save({ close_at: e.target.value })}
                    disabled={busy || !cfg.enabled} className="input-field !py-1 !px-2 text-[12px]" />
                  <Toggle on={!!cfg.close_digest} onChange={(v) => save({ close_digest: v })} disabled={busy || !cfg.enabled} />
                </div>
              </Row>
              <Row label="“Close to a level” means" hint="used by the morning digest">
                <div className="flex items-center gap-1">
                  <input type="number" step="0.5" min="0.1" max="20" value={cfg.near_pct}
                    onChange={(e) => save({ near_pct: Number(e.target.value) || 2 })}
                    disabled={busy || !cfg.enabled} className="input-field !py-1 !px-2 text-[12px] w-16" />
                  <span className="text-[12px] text-gray-500">%</span>
                </div>
              </Row>
            </div>

            <div className="flex items-center gap-2">
              <button onClick={test} disabled={busy || !ready}
                className="btn-secondary !py-1.5 !px-3 text-[12.5px] flex items-center gap-1.5 disabled:opacity-50">
                {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
                Send me the morning digest now
              </button>
              {msg && (
                <span className={`text-[12px] flex items-center gap-1 ${msg.tone === 'ok' ? 'text-emerald-400' : 'text-red-400'}`}>
                  {msg.tone === 'ok' ? <CheckCircle2 className="w-3.5 h-3.5" /> : <AlertTriangle className="w-3.5 h-3.5" />}
                  {msg.text}
                </span>
              )}
            </div>
            {msg?.preview && (
              <pre className="text-[11px] text-gray-400 bg-surface-2/60 border border-surface-3 rounded-lg p-2 whitespace-pre-wrap">
                {msg.preview.replace(/<[^>]+>/g, '')}
              </pre>
            )}

            <div className="text-[11px] text-gray-500">
              Each level speaks at most once a day, so a stock hovering on your price cannot flood your phone.
              Mute any single stock with the bell on its row.
            </div>
          </>
        )}
      </div>
    </div>
  );
}
