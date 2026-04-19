import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem('app_token'));
  const [user, setUser] = useState(() => {
    try { return JSON.parse(localStorage.getItem('app_user')); } catch { return null; }
  });
  const [validating, setValidating] = useState(() => Boolean(localStorage.getItem('app_token')));

  const login = useCallback((accessToken, username, userData = {}) => {
    localStorage.setItem('app_token', accessToken);
    const userObj = { username, ...userData };
    localStorage.setItem('app_user', JSON.stringify(userObj));
    // Snapshot current server boot_id so we detect restarts later
    fetch('/api/boot_id').then(r => r.json()).then(d => {
      if (d.boot_id) localStorage.setItem('server_boot_id', d.boot_id);
    }).catch(() => {});
    setToken(accessToken);
    setUser(userObj);
    setValidating(false);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem('app_token');
    localStorage.removeItem('app_user');
    localStorage.removeItem('server_boot_id');
    setToken(null);
    setUser(null);
    setValidating(false);
  }, []);

  const completeOnboarding = useCallback(() => {
    setUser(prev => {
      const updated = { ...prev, is_onboarded: true };
      localStorage.setItem('app_user', JSON.stringify(updated));
      return updated;
    });
  }, []);

  // Validate token on mount — if expired/invalid or server restarted, force re-login
  useEffect(() => {
    if (!token) { setValidating(false); return; }
    setValidating(true);

    // Check if server was restarted (boot_id changed)
    const checkBootAndValidate = async () => {
      try {
        const bootRes = await fetch('/api/boot_id');
        if (bootRes.ok) {
          const { boot_id } = await bootRes.json();
          const savedBoot = localStorage.getItem('server_boot_id');
          if (savedBoot && savedBoot !== boot_id) {
            // Server restarted — force re-login
            logout();
            return;
          }
          localStorage.setItem('server_boot_id', boot_id);
        }
      } catch {
        // Server unreachable — clear session
        logout();
        return;
      }

      // Validate JWT token
      try {
        const res = await fetch('/api/auth/me', {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.status === 401) { logout(); return; }
        if (res.ok) {
          const data = await res.json();
          if (data) {
            const updated = { ...user, ...data };
            setUser(updated);
            localStorage.setItem('app_user', JSON.stringify(updated));
          }
        }
      } catch {
        logout();
        return;
      }
      setValidating(false);
    };

    checkBootAndValidate();
  }, [token]);

  const isAuthenticated = Boolean(token) && !validating;
  const isOnboarded = user?.is_onboarded !== false;

  if (validating) {
    return (
      <div className="flex items-center justify-center h-screen bg-surface-0">
        <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-brand-500" />
      </div>
    );
  }

  return (
    <AuthContext.Provider value={{ token, user, isAuthenticated, isOnboarded, login, logout, completeOnboarding }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}
