import React, { useState } from 'react';
import { useAuth } from '../AuthContext';
import { useToast } from '../ToastContext';
import { api } from '../api';
import { Key, Shield, ArrowRight, Loader2, ExternalLink } from 'lucide-react';

export default function Onboarding() {
  const { completeOnboarding } = useAuth();
  const toast = useToast();
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!apiKey || !apiSecret) return;
    setLoading(true);
    try {
      await api.onboard(apiKey, apiSecret);
      completeOnboarding();
      toast.success('Zerodha API keys saved! You can now login to Zerodha.');
    } catch (err) {
      toast.error(err.message || 'Failed to save API keys');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface-0 px-4">
      <div className="w-full max-w-lg">
        {/* Header */}
        <div className="text-center mb-8">
          <img
            src="/quantflux.png"
            alt="QuantFlux"
            className="w-16 h-16 rounded-2xl mx-auto mb-4 shadow-lg shadow-brand-500/20 object-contain"
          />
          <h1 className="text-2xl font-bold text-white">Connect Your Broker</h1>
          <p className="text-sm text-gray-400 mt-2">
            Enter your Zerodha Kite Connect API credentials to start trading
          </p>
        </div>

        <div className="card">
          <div className="flex items-center gap-2 mb-6">
            <Shield className="w-5 h-5 text-brand-400" />
            <h2 className="text-lg font-semibold text-white">Zerodha API Setup</h2>
          </div>

          {/* Info box */}
          <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-4 mb-6">
            <p className="text-sm text-blue-300">
              You need a Kite Connect API subscription from Zerodha.
              Get your API Key and Secret from{' '}
              <a
                href="https://developers.kite.trade/"
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-400 underline inline-flex items-center gap-1"
              >
                developers.kite.trade <ExternalLink className="w-3 h-3" />
              </a>
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-5">
            {/* API Key */}
            <div className="space-y-2">
              <label className="block text-sm font-medium text-gray-300">API Key</label>
              <div className="relative">
                <Key className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                <input
                  type="text"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="Your Kite Connect API Key"
                  className="input-field w-full pl-10"
                  autoFocus
                />
              </div>
            </div>

            {/* API Secret */}
            <div className="space-y-2">
              <label className="block text-sm font-medium text-gray-300">API Secret</label>
              <div className="relative">
                <Shield className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                <input
                  type="password"
                  value={apiSecret}
                  onChange={(e) => setApiSecret(e.target.value)}
                  placeholder="Your Kite Connect API Secret"
                  className="input-field w-full pl-10"
                />
              </div>
            </div>

            <button
              type="submit"
              disabled={loading || !apiKey || !apiSecret}
              className="w-full btn-primary flex items-center justify-center gap-2 py-3 text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <>
                  Save & Continue
                  <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </form>

          <p className="text-xs text-gray-600 text-center mt-5">
            Your credentials are encrypted and stored securely in the database
          </p>
        </div>
      </div>
    </div>
  );
}
