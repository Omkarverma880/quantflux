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
    <div className="h-screen flex relative overflow-hidden bg-gradient-to-br from-black via-[#050807] to-surface-1">
      {/* Ambient glows + grid span the whole page — one cohesive canvas, no hard seam */}
      <div className="absolute -top-32 -left-24 w-[32rem] h-[32rem] rounded-full bg-brand-500/10 blur-3xl animate-pulse pointer-events-none" />
      <div className="absolute top-1/3 left-[46%] -translate-x-1/2 w-[38rem] h-[38rem] rounded-full bg-brand-600/10 blur-3xl animate-pulse pointer-events-none" style={{ animationDelay: '1.2s' }} />
      <div
        className="absolute inset-0 opacity-[0.035] pointer-events-none"
        style={{
          backgroundImage: 'linear-gradient(#fff 1px, transparent 1px), linear-gradient(90deg, #fff 1px, transparent 1px)',
          backgroundSize: '46px 46px',
        }}
      />

      {/* ══ Left hero (desktop only) ══ */}
      <div className="hidden lg:flex lg:w-[54%] relative z-10">
        <div className="flex flex-col justify-between p-10 w-full h-full overflow-hidden">
          {/* Devotional blessing — ✦ divider above and below */}
          <div className="text-center">
            <div className="flex items-center justify-center gap-3 mb-3">
              <span className="h-px w-14 bg-gradient-to-r from-transparent to-amber-400/50" />
              <span className="text-amber-300/80 text-sm">✦</span>
              <span className="h-px w-14 bg-gradient-to-l from-transparent to-amber-400/50" />
            </div>
            <p className="text-2xl font-semibold tracking-wide bg-gradient-to-r from-amber-200 via-yellow-100 to-amber-300 bg-clip-text text-transparent drop-shadow-[0_0_14px_rgba(251,191,36,0.35)]">
              Jae Shri Radhe Govinda
            </p>
            <div className="flex items-center justify-center gap-3 mt-3">
              <span className="h-px w-14 bg-gradient-to-r from-transparent to-amber-400/50" />
              <span className="text-amber-300/80 text-sm">✦</span>
              <span className="h-px w-14 bg-gradient-to-l from-transparent to-amber-400/50" />
            </div>
          </div>

          {/* Illustration — flexes to fill remaining height so the page never scrolls */}
          <div className="flex-1 min-h-0 flex items-center justify-center py-3">
            <svg viewBox="0 0 400 360" className="h-full w-auto max-h-[42vh] drop-shadow-2xl" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                <defs>
                  <linearGradient id="qfGreenF" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0" stopColor="#bbf7d0" />
                    <stop offset="0.5" stopColor="#22c55e" />
                    <stop offset="1" stopColor="#14532d" />
                  </linearGradient>
                  <linearGradient id="qfGreenT" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0" stopColor="#f0fdf4" />
                    <stop offset="1" stopColor="#4ade80" />
                  </linearGradient>
                  <linearGradient id="qfGreenS" x1="0" y1="0" x2="1" y2="0">
                    <stop offset="0" stopColor="#166534" />
                    <stop offset="1" stopColor="#052e16" />
                  </linearGradient>
                  <linearGradient id="qfSilver" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0" stopColor="#f9fafb" />
                    <stop offset="0.5" stopColor="#9ca3af" />
                    <stop offset="1" stopColor="#374151" />
                  </linearGradient>
                  <linearGradient id="qfPed" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0" stopColor="#e5e7eb" />
                    <stop offset="0.5" stopColor="#9ca3af" />
                    <stop offset="1" stopColor="#1f2937" />
                  </linearGradient>
                  <radialGradient id="qfSphere" cx="0.35" cy="0.3" r="0.75">
                    <stop offset="0" stopColor="#ffffff" />
                    <stop offset="0.45" stopColor="#9ca3af" />
                    <stop offset="1" stopColor="#111827" />
                  </radialGradient>
                  <radialGradient id="qfGlow" cx="0.5" cy="0.5" r="0.5">
                    <stop offset="0" stopColor="#22c55e" stopOpacity="0.4" />
                    <stop offset="1" stopColor="#22c55e" stopOpacity="0" />
                  </radialGradient>
                </defs>

                {/* ambient glow */}
                <ellipse cx="200" cy="180" rx="185" ry="175" fill="url(#qfGlow)" />

                {/* tilted orbit ring (behind) */}
                <g transform="rotate(-22 205 200)">
                  <ellipse cx="205" cy="200" rx="168" ry="58" stroke="url(#qfSilver)" strokeWidth="6" opacity="0.92" />
                  <ellipse cx="205" cy="200" rx="168" ry="58" stroke="#000000" strokeWidth="1" opacity="0.2" />
                </g>

                {/* thin metallic rods (behind the bars) */}
                <g stroke="url(#qfSilver)" strokeWidth="3" strokeLinecap="round">
                  <line x1="168" y1="300" x2="168" y2="70" />
                  <line x1="250" y1="300" x2="250" y2="95" />
                  <line x1="300" y1="300" x2="300" y2="132" />
                </g>

                {/* reflective pedestal */}
                <ellipse cx="200" cy="320" rx="122" ry="26" fill="#000000" opacity="0.35" />
                <ellipse cx="200" cy="309" rx="120" ry="26" fill="url(#qfPed)" />
                <ellipse cx="200" cy="303" rx="106" ry="20" fill="#d1d5db" opacity="0.6" />
                <ellipse cx="200" cy="301" rx="88" ry="15" fill="#f3f4f6" opacity="0.5" />

                {/* Bar A — green */}
                <polygon points="150,150 163,140 199,140 186,150" fill="url(#qfGreenT)" />
                <polygon points="186,150 199,140 199,286 186,296" fill="url(#qfGreenS)" />
                <rect x="150" y="150" width="36" height="146" fill="url(#qfGreenF)" />
                <rect x="156" y="156" width="6" height="132" rx="3" fill="#ffffff" opacity="0.22" />

                {/* Bar B — green (tallest) */}
                <polygon points="200,108 213,98 249,98 236,108" fill="url(#qfGreenT)" />
                <polygon points="236,108 249,98 249,286 236,296" fill="url(#qfGreenS)" />
                <rect x="200" y="108" width="36" height="188" fill="url(#qfGreenF)" />
                <rect x="206" y="114" width="6" height="174" rx="3" fill="#ffffff" opacity="0.22" />

                {/* Bar C — clear glass (short) */}
                <polygon points="250,205 261,197 289,197 278,205" fill="#e2e8f0" opacity="0.35" />
                <polygon points="278,205 289,197 289,286 278,294" fill="#94a3b8" opacity="0.35" />
                <rect x="250" y="205" width="28" height="91" fill="#e2e8f0" opacity="0.16" />
                <rect x="250" y="205" width="28" height="91" fill="none" stroke="#e2e8f0" strokeOpacity="0.5" />
                <rect x="255" y="211" width="5" height="79" rx="2" fill="#ffffff" opacity="0.3" />

                {/* chrome spheres atop the rods */}
                <circle cx="168" cy="70" r="8" fill="url(#qfSphere)" />
                <circle cx="250" cy="95" r="7" fill="url(#qfSphere)" />
                <circle cx="300" cy="132" r="6" fill="url(#qfSphere)" />

                {/* floating spheres */}
                <circle cx="112" cy="250" r="10" fill="url(#qfSphere)" />
                <circle cx="322" cy="232" r="8" fill="url(#qfSphere)" />
              </svg>
          </div>

          {/* Headline */}
          <div className="shrink-0">
            <span className="inline-block px-3 py-1 rounded-full bg-brand-600/15 text-brand-400 text-xs font-semibold border border-brand-500/25">
              Automated Trading &amp; Research
            </span>
            <h2 className="text-3xl xl:text-4xl font-extrabold leading-tight text-white mt-3">
              Trade with{' '}
              <span className="bg-gradient-to-r from-brand-400 to-brand-600 bg-clip-text text-transparent">QuantFlux</span>
            </h2>
            <p className="text-sm text-gray-400 mt-2 max-w-md leading-relaxed">
              Automated strategies, deep Option-Chain &amp; VWAP research, backtesting and live risk controls — one precision platform.
            </p>
            <div className="flex flex-wrap gap-2 mt-4">
              {['Automated Strategies', 'VWAP Research', 'Backtesting', 'Risk Analysis'].map((c) => (
                <span key={c} className="px-3 py-1.5 rounded-lg bg-surface-2 border border-surface-3 text-xs text-gray-300">
                  {c}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* ══ Right: sign-in column ══ */}
      <div className="flex-1 flex justify-center overflow-y-auto px-4 relative z-10">
        <div className="w-full max-w-md my-auto py-8">
          {/* Devotional blessing (mobile only — hero shows it on desktop) */}
          <div className="text-center mb-6 lg:hidden">
            <div className="flex items-center justify-center gap-3 mb-2">
              <span className="h-px w-10 bg-gradient-to-r from-transparent to-amber-400/50" />
              <span className="text-amber-300/80 text-sm">✦</span>
              <span className="h-px w-10 bg-gradient-to-l from-transparent to-amber-400/50" />
            </div>
            <p className="text-xl font-semibold tracking-wide bg-gradient-to-r from-amber-200 via-yellow-100 to-amber-300 bg-clip-text text-transparent drop-shadow-[0_0_12px_rgba(251,191,36,0.3)]">
              Jae Shri Radhe Govinda
            </p>
            <div className="flex items-center justify-center gap-3 mt-2">
              <span className="h-px w-10 bg-gradient-to-r from-transparent to-amber-400/50" />
              <span className="text-amber-300/80 text-sm">✦</span>
              <span className="h-px w-10 bg-gradient-to-l from-transparent to-amber-400/50" />
            </div>
          </div>

          {/* Brand — sits atop the sign-in panel */}
          <div className="flex items-center justify-center gap-3 mb-8">
            <LogoIcon size={46} className="shadow-lg shadow-brand-500/20" />
            <div className="leading-tight text-left">
              <div className="text-2xl font-bold bg-gradient-to-r from-brand-400 to-brand-600 bg-clip-text text-transparent">
                QuantFlux
              </div>
              <div className="text-[11px] text-gray-500 tracking-wide">Multi-User Automated Trading</div>
            </div>
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
