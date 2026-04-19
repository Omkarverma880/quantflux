import React, { useState, useEffect, useCallback } from 'react';
import { api } from '../api';
import { useToast } from '../ToastContext';
import {
  Settings as SettingsIcon,
  Shield,
  Key,
  Monitor,
  Save,
  AlertTriangle,
  CheckCircle,
  Copy,
  Eye,
  EyeOff,
  Loader2,
  DollarSign,
  ToggleLeft,
  ToggleRight,
  RefreshCw,
  Layers,
} from 'lucide-react';

export default function Settings() {
  const [auth, setAuth] = useState({ authenticated: false, profile: null });
  const [cfg, setCfg] = useState(null);
  const [dirty, setDirty] = useState({});
  const [saving, setSaving] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [showSecret, setShowSecret] = useState(false);
  const [copied, setCopied] = useState(false);
  const toast = useToast();

  const load = useCallback(async () => {
    try {
      const [a, s] = await Promise.all([api.getAuthStatus(), api.getSettings()]);
      setAuth(a);
      setCfg(s);
      setDirty({});
    } catch (e) {
      console.error(e);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  /* Listen for login-success from the Zerodha popup */
  useEffect(() => {
    const onMessage = (e) => {
      if (e.data?.type === 'zerodha_login_success') {
        load();               // re-fetch auth + settings
        toast.success('Logged in successfully');
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [load]);

  /* Also poll auth status while popup might be open (fallback) */
  useEffect(() => {
    if (auth.authenticated) return;
    const id = setInterval(() => {
      api.getAuthStatus().then((a) => {
        if (a.authenticated) {
          setAuth(a);
          toast.success('Logged in successfully');
          clearInterval(id);
        }
      }).catch(() => {});
    }, 3000);
    return () => clearInterval(id);
  }, [auth.authenticated]);

  const handleLogin = async () => {
    try {
      const { login_url } = await api.getLoginUrl();
      window.open(login_url, '_blank', 'width=600,height=700');
    } catch (e) {
      console.error(e);
    }
  };

  /* update local state for a field */
  const set = (field, value) => {
    setCfg((prev) => ({ ...prev, [field]: value }));
    setDirty((prev) => ({ ...prev, [field]: true }));
  };

  /* save only changed fields */
  const handleSave = async () => {
    const payload = {};
    for (const key of Object.keys(dirty)) {
      payload[key] = cfg[key];
    }
    if (Object.keys(payload).length === 0) return;
    setSaving(true);
    try {
      await api.updateSettings(payload);
      setDirty({});
      toast.success('Settings saved successfully');
      // Re-fetch to confirm persisted values
      const fresh = await api.getSettings();
      setCfg(fresh);
    } catch (e) {
      toast.error(e.message || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const copyRedirect = () => {
    navigator.clipboard.writeText(cfg?.kite_redirect_url || '');
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (!cfg) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-6 h-6 text-brand-400 animate-spin" />
      </div>
    );
  }

  const hasDirty = Object.keys(dirty).length > 0;

  return (
    <div className="p-3 sm:p-6 space-y-4 sm:space-y-6 max-w-[1000px] mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl sm:text-2xl font-bold text-white">Settings</h1>
          <p className="text-xs sm:text-sm text-gray-500 mt-0.5">
            System configuration &mdash; changes are saved to .env and applied instantly
          </p>
        </div>
        <button
          onClick={handleSave}
          disabled={!hasDirty || saving}
          className={`btn-primary flex items-center gap-2 text-sm ${
            !hasDirty ? 'opacity-40 cursor-not-allowed' : ''
          }`}
        >
          {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          Save Changes
        </button>
      </div>

      {/* ── Zerodha Connection ────────────────── */}
      <div className="card space-y-4">
        <div className="flex items-center gap-2">
          <Key className="w-5 h-5 text-brand-400" />
          <h3 className="font-semibold text-white">Zerodha Connection</h3>
        </div>

        {auth.authenticated ? (
          <div className="p-4 rounded-lg bg-green-500/5 border border-green-500/20">
            <div className="flex items-center gap-2 mb-3">
              <CheckCircle className="w-5 h-5 text-green-400" />
              <span className="font-medium text-green-400">Connected</span>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
              <div>
                <span className="text-gray-500">Name:</span>
                <span className="text-white ml-2">{auth.profile?.name}</span>
              </div>
              <div>
                <span className="text-gray-500">User ID:</span>
                <span className="mono text-white ml-2">{auth.profile?.user_id}</span>
              </div>
              <div>
                <span className="text-gray-500">Email:</span>
                <span className="text-white ml-2">{auth.profile?.email}</span>
              </div>
              <div>
                <span className="text-gray-500">Broker:</span>
                <span className="text-white ml-2">{auth.profile?.broker}</span>
              </div>
            </div>
          </div>
        ) : (
          <div className="p-4 rounded-lg bg-yellow-500/5 border border-yellow-500/20">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <AlertTriangle className="w-5 h-5 text-yellow-400" />
                <span className="text-yellow-400 font-medium">Not connected</span>
              </div>
              <button onClick={handleLogin} className="btn-primary text-sm">
                Login to Zerodha
              </button>
            </div>
            <p className="text-xs text-gray-500 mt-2">
              Login is required once per trading day. Token is automatically reused.
            </p>
          </div>
        )}
      </div>

      {/* ── API Credentials ──────────────────── */}
      <div className="card space-y-5">
        <div className="flex items-center gap-2 pb-2 border-b border-surface-3/60">
          <Key className="w-5 h-5 text-brand-400" />
          <h3 className="font-semibold text-white">Kite API Credentials</h3>
        </div>

        {/* API Key */}
        <div className="space-y-2">
          <label className="block text-sm font-medium text-gray-300">API Key</label>
          <div className="relative">
            <input
              type={showKey ? 'text' : 'password'}
              value={cfg.kite_api_key}
              onChange={(e) => set('kite_api_key', e.target.value)}
              placeholder="Enter Kite API key"
              className="input-field w-full pr-12 mono tracking-wide"
            />
            <button
              type="button"
              onClick={() => setShowKey(!showKey)}
              className="absolute right-3 top-1/2 -translate-y-1/2 p-1 rounded text-gray-400 hover:text-white hover:bg-surface-3 transition-colors"
            >
              {showKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
          </div>
        </div>

        {/* API Secret */}
        <div className="space-y-2">
          <label className="block text-sm font-medium text-gray-300">API Secret</label>
          <div className="relative">
            <input
              type={showSecret ? 'text' : 'password'}
              value={cfg.kite_api_secret}
              onChange={(e) => set('kite_api_secret', e.target.value)}
              placeholder="Enter Kite API secret"
              className="input-field w-full pr-12 mono tracking-wide"
            />
            <button
              type="button"
              onClick={() => setShowSecret(!showSecret)}
              className="absolute right-3 top-1/2 -translate-y-1/2 p-1 rounded text-gray-400 hover:text-white hover:bg-surface-3 transition-colors"
            >
              {showSecret ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
          </div>
        </div>

        {/* Redirect URL (read-only + copy) */}
        <div className="space-y-2">
          <label className="block text-sm font-medium text-gray-300">Redirect URL</label>
          <div className="flex items-center gap-2">
            <input
              type="text"
              readOnly
              value="https://quantflux-production.up.railway.app/api/auth/callback"
              className="input-field flex-1 text-gray-400 cursor-default"
            />
            <button
              onClick={copyRedirect}
              className={`btn-secondary flex items-center gap-1.5 text-sm whitespace-nowrap transition-all ${
                copied ? 'border-green-500/40 text-green-400' : ''
              }`}
            >
              {copied ? <CheckCircle className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
              {copied ? 'Copied!' : 'Copy'}
            </button>
          </div>
          <div className="flex items-start gap-1.5 mt-1.5 p-2.5 rounded-lg bg-yellow-500/5 border border-yellow-500/15">
            <AlertTriangle className="w-3.5 h-3.5 text-yellow-400 mt-0.5 flex-shrink-0" />
            <p className="text-xs text-yellow-400/90 leading-relaxed">
              Copy this URL and paste it as the <strong>Redirect URL</strong> in your{' '}
              <a
                href="https://developers.kite.trade/apps"
                target="_blank"
                rel="noopener noreferrer"
                className="underline font-medium hover:text-yellow-300"
              >
                Zerodha Kite Developer Console
              </a>
            </p>
          </div>
        </div>
      </div>

      {/* ── Trading Controls ─────────────────── */}
      <div className="card space-y-5">
        <div className="flex items-center gap-2 pb-2 border-b border-surface-3/60">
          <Monitor className="w-5 h-5 text-brand-400" />
          <h3 className="font-semibold text-white">Trading Controls</h3>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
          {/* Trading Enabled */}
          <ToggleCard
            label="Trading Enabled"
            description="Master switch — controls whether any order can be placed"
            value={cfg.trading_enabled}
            onChange={(v) => set('trading_enabled', v)}
            activeColor="green"
          />
          {/* Paper Trade */}
          <ToggleCard
            label="Paper Trade"
            description="Simulates orders without hitting the exchange"
            value={cfg.paper_trade}
            onChange={(v) => set('paper_trade', v)}
            activeColor="yellow"
          />
        </div>

        {!cfg.paper_trade && cfg.trading_enabled && (
          <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-sm text-red-400 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 flex-shrink-0" />
            <span>
              <strong>LIVE MODE</strong> — Real orders will be placed on the exchange. Make sure
              your strategies are tested in paper mode first.
            </span>
          </div>
        )}
      </div>

      {/* ── Risk Limits ──────────────────────── */}
      <div className="card space-y-5">
        <div className="flex items-center gap-2 pb-2 border-b border-surface-3/60">
          <Shield className="w-5 h-5 text-brand-400" />
          <h3 className="font-semibold text-white">Risk Limits</h3>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
          <NumberField
            label="Max Loss / Day"
            prefix="₹"
            value={cfg.max_loss_per_day}
            onChange={(v) => set('max_loss_per_day', v)}
          />
          <NumberField
            label="Max Trades / Day"
            value={cfg.max_trades_per_day}
            onChange={(v) => set('max_trades_per_day', v)}
            integer
          />
          <NumberField
            label="Max Position Size"
            prefix="₹"
            value={cfg.max_position_size}
            onChange={(v) => set('max_position_size', v)}
          />
          <NumberField
            label="Max Single Order Value"
            prefix="₹"
            value={cfg.max_single_order_value}
            onChange={(v) => set('max_single_order_value', v)}
          />
        </div>
      </div>

      {/* ── Active Strategies ────────────────── */}
      <div className="card space-y-5">
        <div className="flex items-center gap-2 pb-2 border-b border-surface-3/60">
          <Layers className="w-5 h-5 text-brand-400" />
          <h3 className="font-semibold text-white">Active Strategies</h3>
        </div>
        <p className="text-xs text-gray-500">Select which strategies the engine will run automatically.</p>
        <div className="space-y-2">
          {[
            { key: 'strategy1_gann_cv', label: 'Strategy 1 — Gann CV' },
            { key: 'strategy2_option_sell', label: 'Strategy 2 — Option Selling' },
            { key: 'strategy3_cv_vwap_ema_adx', label: 'Strategy 3 — CV + VWAP + EMA + ADX' },
          ].map(({ key, label }) => {
            const active = (cfg.active_strategies || '').split(',').map(s => s.trim()).filter(Boolean);
            const checked = active.includes(key);
            const toggle = () => {
              const next = checked
                ? active.filter(s => s !== key)
                : [...active, key];
              set('active_strategies', next.join(','));
            };
            return (
              <button
                key={key}
                type="button"
                onClick={toggle}
                className={`w-full flex items-center gap-3 p-3.5 rounded-lg border text-left transition-all ${
                  checked
                    ? 'border-brand-500/30 bg-brand-500/5'
                    : 'border-surface-3 bg-surface-2/60 hover:border-surface-4'
                }`}
              >
                <div className={`w-5 h-5 rounded flex items-center justify-center border transition-colors ${
                  checked ? 'bg-brand-600 border-brand-500' : 'border-gray-600 bg-surface-3'
                }`}>
                  {checked && (
                    <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                    </svg>
                  )}
                </div>
                <div>
                  <span className="text-sm font-medium text-white">{label}</span>
                  <p className="text-[10px] text-gray-500">{key}</p>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Sticky Save bar (mobile-friendly) */}
      {hasDirty && (
        <div className="fixed bottom-0 left-0 right-0 z-50 p-4 bg-surface-1/95 backdrop-blur border-t border-surface-3 flex items-center justify-between sm:hidden">
          <span className="text-sm text-yellow-400">Unsaved changes</span>
          <button onClick={handleSave} disabled={saving} className="btn-primary text-sm flex items-center gap-1.5">
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            Save
          </button>
        </div>
      )}
    </div>
  );
}

/* ── Sub-components ─────────────────────────────── */

function ToggleCard({ label, description, value, onChange, activeColor = 'green' }) {
  const border = {
    green: value ? 'border-green-500/30' : 'border-surface-3',
    yellow: value ? 'border-yellow-500/30' : 'border-surface-3',
  };
  const bg = {
    green: value ? 'bg-green-500/5' : 'bg-surface-2/60',
    yellow: value ? 'bg-yellow-500/5' : 'bg-surface-2/60',
  };
  const pill = {
    green: value
      ? 'bg-green-500/20 text-green-400 border-green-500/30'
      : 'bg-surface-3 text-gray-500 border-surface-4',
    yellow: value
      ? 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30'
      : 'bg-surface-3 text-gray-500 border-surface-4',
  };

  return (
    <button
      type="button"
      onClick={() => onChange(!value)}
      className={`group p-5 rounded-xl border text-left transition-all hover:shadow-md
        ${border[activeColor]} ${bg[activeColor]}`}
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-semibold text-white">{label}</span>
        <span className={`text-[11px] font-bold tracking-wider px-2.5 py-0.5 rounded-full border ${pill[activeColor]}`}>
          {value ? 'ON' : 'OFF'}
        </span>
      </div>
      <p className="text-xs text-gray-500 leading-relaxed">{description}</p>
    </button>
  );
}

function NumberField({ label, prefix, value, onChange, integer }) {
  const handleChange = (e) => {
    const raw = e.target.value;
    if (raw === '') { onChange(0); return; }
    const num = integer ? parseInt(raw, 10) : parseFloat(raw);
    if (!isNaN(num)) onChange(num);
  };

  return (
    <div className="space-y-2">
      <label className="block text-sm font-medium text-gray-300">{label}</label>
      <div className="relative">
        {prefix && (
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 text-sm font-medium">
            {prefix}
          </span>
        )}
        <input
          type="number"
          value={value}
          onChange={handleChange}
          className={`input-field w-full mono ${prefix ? 'pl-8' : ''}`}
        />
      </div>
    </div>
  );
}
