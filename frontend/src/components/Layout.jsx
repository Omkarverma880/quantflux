import React, { useState, useEffect } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { api } from '../api';
import { useTheme } from '../ThemeContext';
import { useAuth } from '../AuthContext';
import { ErrorBoundary } from './ErrorBoundary';
import { LogoIcon, LogoFull } from './QuantFluxLogo';
import {
  LayoutDashboard,
  TrendingUp,
  ClipboardList,
  Settings,
  LogIn,
  LogOut,
  Activity,
  Radio,
  ChevronLeft,
  Menu,
  BarChart3,
  History,
  Sun,
  Moon,
  X,
  Power,
  UserCircle,
} from 'lucide-react';

const NAV = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/strategy1', icon: BarChart3, label: 'Cum. Volume' },
  { to: '/strategies', icon: TrendingUp, label: 'Strategies' },
  { to: '/orders', icon: ClipboardList, label: 'Orders' },
  { to: '/manual-trading', icon: Activity, label: 'Manual Trading' },
  { to: '/settings', icon: Settings, label: 'Settings' },
  { to: '/history', icon: History, label: 'Trade History' },
];

export default function Layout() {
  const [auth, setAuth] = useState({ authenticated: false, profile: null });
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const { theme, toggle: toggleTheme } = useTheme();
  const { logout: appLogout, user } = useAuth();

  // Close mobile sidebar on route change
  useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    api.getAuthStatus().then(setAuth).catch(() => {});
  }, [location.pathname]);

  // Auto-refresh auth when Zerodha login popup completes
  useEffect(() => {
    const onMessage = (e) => {
      if (e.data?.type === 'zerodha_login_success') {
        api.getAuthStatus().then(setAuth).catch(() => {});
        // Notify all pages to re-fetch data
        window.dispatchEvent(new Event('zerodha_connected'));
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);

  const handleLogin = async () => {
    try {
      const { login_url } = await api.getLoginUrl();
      // Reuse the same popup window — prevents multiple login tabs
      window.open(login_url, 'zerodha_login', 'width=600,height=700');
    } catch (e) {
      const msg = e?.response?.data?.error || e?.message || '';
      if (msg.toLowerCase().includes('credentials not configured') || msg.toLowerCase().includes('api key')) {
        alert('Please configure your Zerodha API Key and Secret in Settings first.');
        navigate('/settings');
      } else {
        console.error(e);
        alert('Failed to get Zerodha login URL. Check console for details.');
      }
    }
  };

  const handleLogout = async () => {
    await api.logout();
    setAuth({ authenticated: false, profile: null });
    window.dispatchEvent(new Event('zerodha_disconnected'));
  };

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Mobile overlay */}
      {mobileOpen && (
        <div
          className="fixed inset-0 bg-black/60 z-40 lg:hidden"
          onClick={() => setMobileOpen(false)}
        />
      )}

      {/* Mobile top bar */}
      <div className="fixed top-0 left-0 right-0 h-14 bg-surface-1 border-b border-surface-3 flex items-center px-4 z-30 lg:hidden">
        <button onClick={() => setMobileOpen(true)} className="text-gray-400 hover:text-white">
          <Menu className="w-5 h-5" />
        </button>
        <div className="flex items-center gap-2.5 ml-3">
          <LogoIcon size={28} />
          <span className="text-sm font-bold bg-gradient-to-r from-brand-400 to-brand-600 bg-clip-text text-transparent">
            QuantFlux
          </span>
        </div>
      </div>

      {/* Sidebar */}
      <aside
        className={`
          fixed lg:relative z-50
          ${mobileOpen ? 'translate-x-0' : '-translate-x-full'} lg:translate-x-0
          ${collapsed ? 'w-[68px]' : 'w-60'}
          flex flex-col bg-surface-1 border-r border-surface-3 transition-all duration-300 shrink-0
          h-full
        `}
      >
        {/* Logo */}
        <div className="flex items-center h-16 border-b border-surface-3 px-3 overflow-hidden">
          {collapsed ? (
            <div className="mx-auto">
              <LogoIcon size={36} />
            </div>
          ) : (
            <>
              <LogoFull height={30} className="shrink-0" />
              {/* Mobile close button */}
              <button
                onClick={() => setMobileOpen(false)}
                className="ml-auto text-gray-400 hover:text-white lg:hidden"
              >
                <X className="w-5 h-5" />
              </button>
            </>
          )}
        </div>

        {/* Nav Links */}
        <nav className="flex-1 py-4 px-2 space-y-1">
          {NAV.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-150 group ${
                  isActive
                    ? 'bg-brand-600/15 text-brand-400 border border-brand-500/20'
                    : 'text-gray-400 hover:text-white hover:bg-surface-3 border border-transparent'
                }`
              }
            >
              <Icon className="w-5 h-5 shrink-0" />
              {!collapsed && <span className="font-medium text-sm">{label}</span>}
            </NavLink>
          ))}
        </nav>

        {/* Auth + Collapse */}
        <div className="p-3 border-t border-surface-3 space-y-2">
          {auth.authenticated ? (
            <div
              className={`flex items-center ${collapsed ? 'justify-center' : 'gap-3 px-2'}`}
            >
              <div className="w-8 h-8 rounded-full bg-green-500/20 flex items-center justify-center shrink-0">
                <Radio className="w-4 h-4 text-green-400" />
              </div>
              {!collapsed && (
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium text-white truncate">
                    {auth.profile?.name || 'Connected'}
                  </p>
                  <p className="text-[10px] text-gray-500">{auth.profile?.user_id}</p>
                </div>
              )}
              {!collapsed && (
                <button onClick={handleLogout} className="text-gray-500 hover:text-red-400" title="Logout">
                  <LogOut className="w-4 h-4" />
                </button>
              )}
            </div>
          ) : (
            <button
              onClick={handleLogin}
              className="flex items-center gap-2 w-full px-3 py-2 rounded-lg bg-brand-600 hover:bg-brand-700 text-white text-sm font-medium transition-colors justify-center"
            >
              <LogIn className="w-4 h-4" />
              {!collapsed && 'Login to Zerodha'}
            </button>
          )}

          <button
            onClick={() => setCollapsed(!collapsed)}
            className="w-full flex items-center justify-center py-1.5 text-gray-500 hover:text-white transition-colors"
          >
            {collapsed ? <Menu className="w-4 h-4" /> : <ChevronLeft className="w-4 h-4" />}
          </button>
        </div>

        {/* Theme toggle + User info (bottom of sidebar) */}
        <div className="px-3 pb-3 space-y-1">
          {/* Logged-in user identity */}
          {user && (
            <div className={`flex items-center ${collapsed ? 'justify-center' : 'gap-2.5 px-3'} py-2 rounded-lg bg-surface-2 border border-surface-3 mb-2`}>
              <div className="w-7 h-7 rounded-full bg-brand-500/20 flex items-center justify-center shrink-0">
                <UserCircle className="w-4 h-4 text-brand-400" />
              </div>
              {!collapsed && (
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold text-white truncate">
                    {user.full_name || user.username}
                  </p>
                  <p className="text-[10px] text-gray-500 truncate">{user.email || user.username}</p>
                </div>
              )}
            </div>
          )}
          <button
            onClick={toggleTheme}
            className={`w-full flex items-center ${collapsed ? 'justify-center' : 'gap-2.5 px-3'} py-2 rounded-lg
              text-gray-500 hover:text-white hover:bg-surface-3 transition-colors`}
            title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
          >
            {theme === 'light' ? <Moon className="w-4 h-4" /> : <Sun className="w-4 h-4" />}
            {!collapsed && (
              <span className="text-xs font-medium">
                {theme === 'light' ? 'Light Mode' : 'Dark Mode'}
              </span>
            )}
          </button>
          <button
            onClick={appLogout}
            className={`w-full flex items-center ${collapsed ? 'justify-center' : 'gap-2.5 px-3'} py-2 rounded-lg
              text-gray-500 hover:text-red-400 hover:bg-red-500/10 transition-colors`}
            title="Sign out"
          >
            <Power className="w-4 h-4" />
            {!collapsed && <span className="text-xs font-medium">Sign Out</span>}
          </button>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 overflow-auto pt-14 lg:pt-0">
        <ErrorBoundary>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  );
}
