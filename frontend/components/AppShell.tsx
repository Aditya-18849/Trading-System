'use client';

import React, { useState, useEffect } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { Sidebar } from '@/components/Sidebar';
import { Header } from '@/components/Header';
import { ws } from '@/lib/ws';
import { isAuthenticated } from '@/lib/auth';
import { ShieldAlert, RefreshCw } from 'lucide-react';

interface AppShellProps {
  children: React.ReactNode;
}

export function AppShell({ children }: AppShellProps) {
  const pathname = usePathname();
  const router = useRouter();
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [checkedAuth, setCheckedAuth] = useState<boolean>(false);
  const [suspendedMsg, setSuspendedMsg] = useState<string | null>(null);

  useEffect(() => {
    // Listen for vendor license kill-switch events (HTTP 402)
    const handleSuspension = (e: any) => {
      setSuspendedMsg(e.detail || 'Workstation access has been suspended due to an outstanding subscription payment.');
    };
    window.addEventListener('license_suspended', handleSuspension);

    // If on login page, skip auth redirect
    if (pathname === '/login') {
      setCheckedAuth(true);
      return () => window.removeEventListener('license_suspended', handleSuspension);
    }

    // Route Guard
    if (!isAuthenticated()) {
      router.push('/login');
    } else {
      setCheckedAuth(true);
      ws.connect('/ws/live');
    }

    return () => window.removeEventListener('license_suspended', handleSuspension);
  }, [pathname, router]);

  const handleManualRefresh = () => {
    setIsRefreshing(true);
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('manual_refresh'));
    }
    setTimeout(() => setIsRefreshing(false), 600);
  };

  // If on login page, render purely without sidebar/header
  if (pathname === '/login') {
    return <>{children}</>;
  }

  // Subscription Kill-Switch Active Overlay
  if (suspendedMsg) {
    return (
      <div className="h-screen w-screen bg-slate-950 flex items-center justify-center p-6 text-slate-100">
        <div className="max-w-md w-full bg-slate-900 border border-rose-500/40 rounded-2xl p-8 shadow-2xl space-y-6 text-center">
          <div className="mx-auto w-16 h-16 bg-rose-500/10 border border-rose-500/30 rounded-2xl flex items-center justify-center text-rose-400">
            <ShieldAlert className="w-8 h-8" />
          </div>

          <div className="space-y-2">
            <h2 className="text-xl font-bold text-white tracking-tight">Workstation Access Suspended</h2>
            <p className="text-xs text-rose-300/90 font-medium">Subscription Payment Required</p>
          </div>

          <div className="p-4 bg-slate-950/80 border border-slate-800 rounded-xl text-xs text-slate-400 text-left space-y-2">
            <p className="text-slate-300 font-semibold">🔒 System Safeguard Active</p>
            <p>{suspendedMsg}</p>
            <p className="text-[11px] text-emerald-400/90 pt-1">
              ✓ <strong>Data Preservation Notice:</strong> All client data, trade history, performance logs, and account configurations remain securely preserved in the database.
            </p>
          </div>

          <button
            onClick={() => window.location.reload()}
            className="w-full py-3 bg-slate-800 hover:bg-slate-700 text-white font-bold rounded-xl text-xs transition-colors flex items-center justify-center gap-2 border border-slate-700"
          >
            <RefreshCw className="w-4 h-4" />
            <span>Check Subscription Status &amp; Retry</span>
          </button>
        </div>
      </div>
    );
  }

  // Prevent flicker before auth check
  if (!checkedAuth) {
    return (
      <div className="h-screen w-screen bg-slate-950 flex items-center justify-center text-slate-500 text-xs font-mono">
        Authenticating workstation session...
      </div>
    );
  }

  return (
    <div className="flex h-screen bg-slate-950 text-slate-100 antialiased overflow-hidden selection:bg-emerald-500/20 selection:text-emerald-300">
      {/* Persistent Sidebar */}
      <Sidebar />

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Top Telemetry Header */}
        <Header onRefresh={handleManualRefresh} isRefreshing={isRefreshing} />

        {/* Page Viewport */}
        <main className="flex-1 overflow-y-auto p-6 space-y-6 scrollbar-thin">
          <div className="max-w-7xl mx-auto space-y-6">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
