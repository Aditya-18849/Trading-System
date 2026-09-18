'use client';

import React, { useState, useEffect } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { 
  LayoutDashboard, 
  Briefcase, 
  Sparkles, 
  LineChart, 
  FileText, 
  Settings, 
  Activity,
  ChevronRight,
  LogOut,
  User as UserIcon,
  ShieldCheck
} from 'lucide-react';
import { getUser, removeToken, AuthUser } from '@/lib/auth';

const NAV_ITEMS = [
  { label: 'Overview', href: '/', icon: LayoutDashboard },
  { label: 'Portfolio & Positions', href: '/portfolio', icon: Briefcase },
  { label: 'Strategy Signals', href: '/recommendations', icon: Sparkles },
  { label: 'Analytics & PnL', href: '/performance', icon: LineChart },
  { label: 'Daily Reports', href: '/reports', icon: FileText },
  { label: 'Risk & Settings', href: '/settings', icon: Settings },
];

export function Sidebar() {
  const pathname = usePathname();
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);

  useEffect(() => {
    setCurrentUser(getUser());
  }, []);

  const handleLogout = () => {
    removeToken();
    window.location.href = '/login';
  };

  return (
    <aside className="w-64 bg-slate-900 border-r border-slate-800 text-slate-300 flex flex-col flex-shrink-0 min-h-screen select-none">
      {/* Brand Header - Clickable Link to Home */}
      <Link 
        href="/" 
        className="h-16 px-6 border-b border-slate-800 flex items-center gap-3 bg-slate-950/40 hover:bg-slate-950/70 transition-colors cursor-pointer"
        title="Go to Overview Dashboard"
      >
        <div className="w-9 h-9 rounded-lg bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center text-emerald-400 font-bold shadow-lg shadow-emerald-500/5">
          <Activity className="w-5 h-5 text-emerald-400" />
        </div>
        <div>
          <div className="font-bold text-white tracking-wide text-sm flex items-center gap-1.5">
            ALGO TRADE <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 font-mono">PRO</span>
          </div>
          <div className="text-[11px] text-slate-400">SEBI Compliant v2.0</div>
        </div>
      </Link>

      {/* Navigation List */}
      <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
        <div className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-400">
          Navigation
        </div>
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const isActive = pathname === item.href || (item.href !== '/' && pathname.startsWith(item.href));

          return (
            <Link
              key={item.href}
              href={item.href}
              prefetch={true}
              className={`flex items-center justify-between px-3.5 py-2.5 rounded-lg text-xs font-medium transition-all group cursor-pointer ${
                isActive
                  ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/35 shadow-sm'
                  : 'text-slate-400 hover:text-slate-100 hover:bg-slate-800/80 border border-transparent'
              }`}
            >
              <div className="flex items-center gap-3">
                <Icon className={`w-4 h-4 transition-colors ${isActive ? 'text-emerald-400' : 'text-slate-500 group-hover:text-slate-200'}`} />
                <span>{item.label}</span>
              </div>
              {isActive && <ChevronRight className="w-3.5 h-3.5 text-emerald-400/70" />}
            </Link>
          );
        })}
      </nav>

      {/* User Profile & Logout Section */}
      <div className="p-3 border-t border-slate-800 bg-slate-950/40 space-y-2">
        <div className="flex items-center justify-between px-2 py-1">
          <Link
            href="/settings"
            className="flex items-center gap-2 min-w-0 hover:opacity-80 transition-opacity cursor-pointer"
            title="Open Settings & Broker Details"
          >
            <div className="w-7 h-7 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center text-slate-300 flex-shrink-0">
              <UserIcon className="w-3.5 h-3.5 text-emerald-400" />
            </div>
            <div className="min-w-0">
              <div className="text-xs font-semibold text-white truncate">
                {currentUser?.full_name || 'Primary Trader'}
              </div>
              <div className="text-[10px] font-mono text-slate-400 truncate">
                ID: {currentUser?.broker_client_id || 'DEV001'}
              </div>
            </div>
          </Link>

          <button
            onClick={handleLogout}
            title="Sign out of trading workstation"
            className="p-1.5 rounded-md text-slate-400 hover:text-rose-400 hover:bg-rose-500/10 transition-colors cursor-pointer"
          >
            <LogOut className="w-4 h-4" />
          </button>
        </div>

        {/* Regulatory Risk Safeguard Status */}
        <div className="pt-2 border-t border-slate-800/60 flex items-center justify-between text-[11px] px-2 text-slate-400">
          <span className="flex items-center gap-1">
            <ShieldCheck className="w-3 h-3 text-emerald-400" />
            Risk Circuit
          </span>
          <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-emerald-400">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
            GUARDED
          </span>
        </div>
      </div>
    </aside>
  );
}
