import React, { useState } from 'react';
import { useAuth } from '../AuthContext';
import { useToast } from '../ToastContext';
import { Lock, User, Eye, EyeOff, Loader2, ArrowRight, Mail, UserPlus } from 'lucide-react';
import { LogoIcon } from '../components/QuantFluxLogo';

export default function Login() {
  const { login } = useAuth();
  const toast = useToast();
  const [mode, setMode] = useState('login'); // 'login' | 'register' | 'forgot' | 'reset'
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [email, setEmail] = useState('');
  const [fullName, setFullName] = useState('');
  const [showPwd, setShowPwd] = useState(false);
  const [loading, setLoading] = useState(false);
  const [resetToken, setResetToken] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  const handleLogin = async (e) => {
    e.preventDefault();
    if (!username || !password) return;
    setLoading(true);
    try {
      const res = await fetch('/api/auth/app-login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      const data = await res.json();
      if (!res.ok) {
        toast.error(data.error || 'Login failed');
        return;
      }
      login(data.access_token, username, {
        full_name: data.user?.full_name || username,
        email: data.user?.email || '',
      });
      toast.success('Welcome back!');
    } catch (err) {
      toast.error('Connection error. Is the server running?');
    } finally {
      setLoading(false);
    }
  };

  const handleRegister = async (e) => {
    e.preventDefault();
    if (!username || !password || !email) return;
    setLoading(true);
    try {
      const res = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password, email, full_name: fullName }),
      });
      const data = await res.json();
      if (!res.ok) {
        toast.error(data.detail || data.error || 'Registration failed');
        return;
      }
      login(data.access_token, data.user?.username || username, {
        full_name: data.user?.full_name || fullName || username,
        email: data.user?.email || email,
      });
      toast.success('Account created! Welcome to QuantFlux.');
    } catch (err) {
      toast.error('Connection error. Is the server running?');
    } finally {
      setLoading(false);
    }
  };

  const handleForgot = async (e) => {
    e.preventDefault();
    if (!username || !email) return;
    setLoading(true);
    try {
      const res = await fetch('/api/auth/forgot-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, email }),
      });
      const data = await res.json();
      if (!res.ok) {
        toast.error(data.detail || data.error || 'Verification failed');
        return;
      }
      setResetToken(data.reset_token);
      setMode('reset');
      toast.success('Identity verified! Set your new password.');
    } catch (err) {
      toast.error('Connection error. Is the server running?');
    } finally {
      setLoading(false);
    }
  };

  const handleReset = async (e) => {
    e.preventDefault();
    if (!newPassword || !confirmPassword) return;
    if (newPassword !== confirmPassword) {
      toast.error('Passwords do not match');
      return;
    }
    if (newPassword.length < 6) {
      toast.error('Password must be at least 6 characters');
      return;
    }
    setLoading(true);
    try {
      const res = await fetch('/api/auth/reset-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: resetToken, new_password: newPassword }),
      });
      const data = await res.json();
      if (!res.ok) {
        toast.error(data.detail || data.error || 'Reset failed');
        return;
      }
      toast.success('Password reset! Please sign in with your new password.');
      setMode('login');
      setPassword('');
      setNewPassword('');
      setConfirmPassword('');
      setResetToken('');
    } catch (err) {
      toast.error('Connection error. Is the server running?');
    } finally {
      setLoading(false);
    }
  };

  const isLogin = mode === 'login';

  return (
    <div className="min-h-screen flex bg-surface-0">
      {/* ══ Left hero (desktop only) ══ */}
      <div className="hidden lg:flex lg:w-[52%] relative overflow-hidden border-r border-surface-3 bg-gradient-to-br from-black via-surface-0 to-surface-1">
        {/* ambient glows */}
        <div className="absolute -top-24 -left-24 w-96 h-96 rounded-full bg-brand-500/10 blur-3xl animate-pulse" />
        <div className="absolute -bottom-20 right-0 w-[30rem] h-[30rem] rounded-full bg-brand-600/10 blur-3xl animate-pulse" style={{ animationDelay: '1.2s' }} />
        {/* faint grid */}
        <div
          className="absolute inset-0 opacity-[0.04]"
          style={{
            backgroundImage: 'linear-gradient(#fff 1px, transparent 1px), linear-gradient(90deg, #fff 1px, transparent 1px)',
            backgroundSize: '42px 42px',
          }}
        />

        <div className="relative z-10 flex flex-col justify-between p-12 w-full">
          {/* Brand */}
          <div className="flex items-center gap-3">
            <LogoIcon size={40} className="shadow-lg shadow-brand-500/20" />
            <span className="text-2xl font-bold bg-gradient-to-r from-brand-400 to-brand-600 bg-clip-text text-transparent">
              QuantFlux
            </span>
          </div>

          {/* Illustration + headline */}
          <div className="py-8">
            <div className="flex justify-center mb-10">
              <svg viewBox="0 0 360 300" className="w-full max-w-md" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                <defs>
                  <linearGradient id="barA" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0" stopColor="#86efac" />
                    <stop offset="1" stopColor="#16a34a" />
                  </linearGradient>
                  <linearGradient id="barB" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0" stopColor="#4ade80" />
                    <stop offset="1" stopColor="#15803d" />
                  </linearGradient>
                  <linearGradient id="ring" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0" stopColor="#e5e7eb" />
                    <stop offset="0.5" stopColor="#9ca3af" />
                    <stop offset="1" stopColor="#4b5563" />
                  </linearGradient>
                  <radialGradient id="glowg" cx="0.5" cy="0.5" r="0.5">
                    <stop offset="0" stopColor="#22c55e" stopOpacity="0.35" />
                    <stop offset="1" stopColor="#22c55e" stopOpacity="0" />
                  </radialGradient>
                </defs>
                <ellipse cx="180" cy="150" rx="155" ry="150" fill="url(#glowg)" />
                {/* orbit ring */}
                <ellipse cx="180" cy="160" rx="150" ry="54" stroke="url(#ring)" strokeWidth="3" transform="rotate(-18 180 160)" opacity="0.75" />
                <circle cx="304" cy="118" r="7" fill="url(#ring)" />
                <circle cx="66" cy="196" r="6" fill="url(#ring)" />
                {/* bars */}
                <rect x="118" y="150" width="24" height="60" rx="6" fill="url(#barB)" />
                <rect x="150" y="95" width="24" height="115" rx="6" fill="url(#barA)" />
                <rect x="182" y="125" width="24" height="85" rx="6" fill="url(#barB)" />
                <rect x="214" y="70" width="24" height="140" rx="6" fill="url(#barA)" />
                <rect x="246" y="140" width="24" height="70" rx="6" fill="url(#barB)" />
                {/* pedestal */}
                <ellipse cx="180" cy="214" rx="112" ry="15" fill="#000" opacity="0.55" />
                <rect x="72" y="209" width="216" height="6" rx="3" fill="#1f2937" />
              </svg>
            </div>

            <span className="inline-block px-3 py-1 rounded-full bg-brand-600/15 text-brand-400 text-xs font-semibold border border-brand-500/25">
              Automated Trading &amp; Research
            </span>
            <h2 className="text-4xl font-extrabold leading-tight text-white mt-4">
              Trade with{' '}
              <span className="bg-gradient-to-r from-brand-400 to-brand-600 bg-clip-text text-transparent">QuantFlux</span>
            </h2>
            <p className="text-gray-400 mt-3 max-w-md leading-relaxed">
              Automated strategies, deep Option-Chain &amp; VWAP research, backtesting and live risk controls — one precision platform.
            </p>
            <div className="flex flex-wrap gap-2 mt-6">
              {['Automated Strategies', 'VWAP Research', 'Backtesting', 'Risk Analysis'].map((c) => (
                <span key={c} className="px-3 py-1.5 rounded-lg bg-surface-2 border border-surface-3 text-xs text-gray-300">
                  {c}
                </span>
              ))}
            </div>
          </div>

          <p className="text-xs text-gray-600">Multi-User Automated Trading System</p>
        </div>
      </div>

      {/* ══ Right: form column ══ */}
      <div className="flex-1 flex items-center justify-center px-4 py-10">
        <div className="w-full max-w-md">
        {/* Logo (mobile / compact — hero shows branding on desktop) */}
        <div className="text-center mb-8 lg:hidden">
          <div className="flex justify-center mb-4">
            <LogoIcon size={56} className="shadow-lg shadow-brand-500/20" />
          </div>
          <h1 className="text-3xl font-bold bg-gradient-to-r from-brand-400 to-brand-600 bg-clip-text text-transparent">
            QuantFlux
          </h1>
          <p className="text-sm text-gray-500 mt-2">Multi-User Automated Trading System</p>
        </div>

        {/* Card */}
        <div className="card">
          {/* Tab toggle — only for login/register */}
          {(mode === 'login' || mode === 'register') && (
            <div className="flex rounded-lg bg-surface-2 p-1 mb-6">
              <button
                onClick={() => setMode('login')}
                className={`flex-1 py-2 text-sm font-medium rounded-md transition-all ${
                  isLogin
                    ? 'bg-brand-600 text-white shadow'
                    : 'text-gray-400 hover:text-white'
                }`}
              >
                Sign In
              </button>
              <button
                onClick={() => setMode('register')}
                className={`flex-1 py-2 text-sm font-medium rounded-md transition-all ${
                  mode === 'register'
                    ? 'bg-brand-600 text-white shadow'
                    : 'text-gray-400 hover:text-white'
                }`}
              >
                Register
              </button>
            </div>
          )}

          {/* Forgot Password heading */}
          {mode === 'forgot' && (
            <div className="mb-6">
              <h2 className="text-lg font-semibold text-white">Forgot Password</h2>
              <p className="text-xs text-gray-500 mt-1">
                Enter your username and registered email to verify your identity.
              </p>
            </div>
          )}

          {/* Reset Password heading */}
          {mode === 'reset' && (
            <div className="mb-6">
              <h2 className="text-lg font-semibold text-white">Set New Password</h2>
              <p className="text-xs text-gray-500 mt-1">
                Choose a new password (min 6 characters).
              </p>
            </div>
          )}

          {/* ── Login / Register form ── */}
          {(mode === 'login' || mode === 'register') && (
            <form onSubmit={isLogin ? handleLogin : handleRegister} className="space-y-4">
              {/* Full Name (register only) */}
              {!isLogin && (
                <div className="space-y-2">
                  <label className="block text-sm font-medium text-gray-300">Full Name</label>
                  <div className="relative">
                    <UserPlus className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                    <input
                      type="text"
                      value={fullName}
                      onChange={(e) => setFullName(e.target.value)}
                      placeholder="John Doe"
                      className="input-field w-full pl-10"
                    />
                  </div>
                </div>
              )}

              {/* Email (register only) */}
              {!isLogin && (
                <div className="space-y-2">
                  <label className="block text-sm font-medium text-gray-300">Email</label>
                  <div className="relative">
                    <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                    <input
                      type="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="you@example.com"
                      className="input-field w-full pl-10"
                      required={!isLogin}
                    />
                  </div>
                </div>
              )}

              {/* Username */}
              <div className="space-y-2">
                <label className="block text-sm font-medium text-gray-300">Username</label>
                <div className="relative">
                  <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                  <input
                    type="text"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder="Enter username"
                    autoComplete="username"
                    className="input-field w-full pl-10"
                    autoFocus
                  />
                </div>
              </div>

              {/* Password */}
              <div className="space-y-2">
                <label className="block text-sm font-medium text-gray-300">Password</label>
                <div className="relative">
                  <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                  <input
                    type={showPwd ? 'text' : 'password'}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder={isLogin ? 'Enter password' : 'Min 6 characters'}
                    autoComplete={isLogin ? 'current-password' : 'new-password'}
                    className="input-field w-full pl-10 pr-10"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPwd(!showPwd)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-white transition"
                  >
                    {showPwd ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
              </div>

              {/* Forgot Password link (login only) */}
              {isLogin && (
                <div className="flex justify-end">
                  <button
                    type="button"
                    onClick={() => setMode('forgot')}
                    className="text-xs text-brand-400 hover:text-brand-300 transition"
                  >
                    Forgot Password?
                  </button>
                </div>
              )}

              {/* Registration note */}
              {!isLogin && (
                <div className="rounded-lg border border-brand-500/20 bg-brand-500/5 px-3 py-2">
                  <p className="text-xs text-brand-400">
                    <span className="font-semibold">Important:</span> Remember your username and email — you'll need both to reset your password if you forget it.
                  </p>
                </div>
              )}

              {/* Submit */}
              <button
                type="submit"
                disabled={loading || !username || !password || (!isLogin && !email)}
                className="w-full btn-primary flex items-center justify-center gap-2 py-3 text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {loading ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <>
                    {isLogin ? 'Sign In' : 'Create Account'}
                    <ArrowRight className="w-4 h-4" />
                  </>
                )}
              </button>
            </form>
          )}

          {/* ── Forgot Password form ── */}
          {mode === 'forgot' && (
            <form onSubmit={handleForgot} className="space-y-4">
              <div className="space-y-2">
                <label className="block text-sm font-medium text-gray-300">Username</label>
                <div className="relative">
                  <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                  <input
                    type="text"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder="Enter your username"
                    className="input-field w-full pl-10"
                    autoFocus
                  />
                </div>
              </div>

              <div className="space-y-2">
                <label className="block text-sm font-medium text-gray-300">Registered Email</label>
                <div className="relative">
                  <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="you@example.com"
                    className="input-field w-full pl-10"
                    required
                  />
                </div>
              </div>

              <button
                type="submit"
                disabled={loading || !username || !email}
                className="w-full btn-primary flex items-center justify-center gap-2 py-3 text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {loading ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <>
                    Verify Identity
                    <ArrowRight className="w-4 h-4" />
                  </>
                )}
              </button>

              <div className="flex justify-center">
                <button
                  type="button"
                  onClick={() => setMode('login')}
                  className="text-xs text-gray-400 hover:text-white transition"
                >
                  ← Back to Sign In
                </button>
              </div>
            </form>
          )}

          {/* ── Reset Password form ── */}
          {mode === 'reset' && (
            <form onSubmit={handleReset} className="space-y-4">
              <div className="space-y-2">
                <label className="block text-sm font-medium text-gray-300">New Password</label>
                <div className="relative">
                  <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                  <input
                    type={showPwd ? 'text' : 'password'}
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    placeholder="Min 6 characters"
                    autoComplete="new-password"
                    className="input-field w-full pl-10 pr-10"
                    autoFocus
                  />
                  <button
                    type="button"
                    onClick={() => setShowPwd(!showPwd)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-white transition"
                  >
                    {showPwd ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
              </div>

              <div className="space-y-2">
                <label className="block text-sm font-medium text-gray-300">Confirm Password</label>
                <div className="relative">
                  <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                  <input
                    type={showPwd ? 'text' : 'password'}
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="Re-enter password"
                    autoComplete="new-password"
                    className="input-field w-full pl-10"
                  />
                </div>
              </div>

              {newPassword && confirmPassword && newPassword !== confirmPassword && (
                <p className="text-xs text-red-400">Passwords do not match</p>
              )}

              <button
                type="submit"
                disabled={loading || !newPassword || !confirmPassword || newPassword !== confirmPassword}
                className="w-full btn-primary flex items-center justify-center gap-2 py-3 text-sm font-semibold disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {loading ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <>
                    Reset Password
                    <ArrowRight className="w-4 h-4" />
                  </>
                )}
              </button>

              <div className="flex justify-center">
                <button
                  type="button"
                  onClick={() => { setMode('login'); setResetToken(''); }}
                  className="text-xs text-gray-400 hover:text-white transition"
                >
                  ← Back to Sign In
                </button>
              </div>
            </form>
          )}
        </div>

        {/* Footer */}
        <p className="text-center text-xs text-gray-600 mt-6">
          QuantFlux · Multi-User Automated Trading
        </p>
        </div>
      </div>
    </div>
  );
}
