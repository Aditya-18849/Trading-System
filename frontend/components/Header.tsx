'use client';

import React, { useState, useEffect } from 'react';
import { 
  Clock, 
  Wifi, 
  WifiOff, 
  ShieldAlert, 
  Radio, 
  CheckCircle2, 
  AlertTriangle,
  RefreshCw,
  Power
} from 'lucide-react';
import { api } from '@/lib/api';
import { useTradingStore } from '@/lib/store';
import { TradingModeToggle } from '@/components/TradingModeToggle';

interface HeaderProps {
  onRefresh?: () => void;
  isRefreshing?: boolean;
}

export function Header({ onRefresh, isRefreshing = false }: HeaderProps) {
  const [time, setTime] = useState<string>('');
  const [marketStatus, setMarketStatus] = useState<{ label: string; isOpen: boolean; color: string }>({
    label: 'CHECKING',
    isOpen: false,
    color: 'slate',
  });
  const wsConnected = useTradingStore((s) => s.wsConnected);
  const wsLatency = useTradingStore((s) => s.wsLatency);
  const [killSwitchOpen, setKillSwitchOpen] = useState<boolean>(false);
  const [killLoading, setKillLoading] = useState<boolean>(false);
  const [killSuccess, setKillSuccess] = useState<string | null>(null);

  // Update IST clock & market session
  useEffect(() => {
    const updateTimeAndStatus = () => {
      const now = new Date();
      // IST time string
      const istString = now.toLocaleTimeString('en-IN', {
        timeZone: 'Asia/Kolkata',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      });
      setTime(istString);

      // Check IST hours
      const istDate = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
      const hours = istDate.getHours();
      const mins = istDate.getMinutes();
      const totalMins = hours * 60 + mins;
      const day = istDate.getDay(); // 0 = Sun, 6 = Sat

      if (day === 0 || day === 6) {
        setMarketStatus({ label: 'MARKET CLOSED (WEEKEND)', isOpen: false, color: 'rose' });
      } else if (totalMins >= 540 && totalMins < 555) {
        setMarketStatus({ label: 'PRE-MARKET (09:00 - 09:15)', isOpen: true, color: 'amber' });
      } else if (totalMins >= 555 && totalMins < 930) {
        setMarketStatus({ label: 'NSE LIVE (09:15 - 15:30)', isOpen: true, color: 'emerald' });
      } else if (totalMins >= 930 && totalMins < 960) {
        setMarketStatus({ label: 'POST-MARKET (15:30 - 16:00)', isOpen: false, color: 'amber' });
      } else {
        setMarketStatus({ label: 'MARKET CLOSED', isOpen: false, color: 'slate' });
      }
    };

    updateTimeAndStatus();
    const timer = setInterval(updateTimeAndStatus, 1000);
    return () => clearInterval(timer);
  }, []);

  const triggerEmergencyExit = async () => {
    setKillLoading(true);
    setKillSuccess(null);
    try {
      const res = await api.post('/api/v1/emergency-exit');
      setKillSuccess(res.data?.detail || 'All positions squared off and auto-trading disabled.');
      setTimeout(() => {
        setKillSwitchOpen(false);
        setKillSuccess(null);
        if (onRefresh) onRefresh();
      }, 2500);
    } catch (err: any) {
      alert('Emergency exit failed: ' + (err.response?.data?.detail || err.message));
    } finally {
      setKillLoading(false);
    }
  };

  return (
    <>
      <header className="h-16 bg-slate-900/95 backdrop-blur border-b border-slate-800 px-6 flex items-center justify-between sticky top-0 z-30">
        {/* Left: Clock & Market Session Badge */}
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 font-mono text-sm text-slate-200 bg-slate-800/80 px-3 py-1.5 rounded-md border border-slate-700/60">
            <Clock className="w-4 h-4 text-emerald-400" />
            <span className="font-semibold tracking-wider">{time || '--:--:--'}</span>
            <span className="text-[10px] text-slate-400 font-sans">IST</span>
          </div>

          <div className="flex items-center gap-2">
            <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold border ${
              marketStatus.color === 'emerald'
                ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30'
                : marketStatus.color === 'amber'
                ? 'bg-amber-500/10 text-amber-300 border-amber-500/30'
                : marketStatus.color === 'rose'
                ? 'bg-rose-500/10 text-rose-300 border-rose-500/30'
                : 'bg-slate-800 text-slate-300 border-slate-700'
            }`}>
              <span className={`w-2 h-2 rounded-full ${
                marketStatus.isOpen ? 'bg-emerald-400 animate-ping' : 'bg-slate-500'
              }`} />
              {marketStatus.label}
            </span>
          </div>
        </div>

        {/* Right: Telemetry, Refresh & Emergency Button */}
        <div className="flex items-center gap-3">
          {/* Live WS Status */}
          <div className="hidden md:flex items-center gap-1.5 text-xs text-slate-400 px-2.5 py-1 bg-slate-800/50 rounded-md border border-slate-700/40">
            {wsConnected ? (
              <>
                <Radio className="w-3.5 h-3.5 text-emerald-400 animate-pulse" />
                <span className="text-slate-300">Live WS {wsLatency}ms</span>
              </>
            ) : (
              <>
                <WifiOff className="w-3.5 h-3.5 text-rose-400" />
                <span className="text-rose-300">WS Reconnecting...</span>
              </>
            )}
          </div>

          {/* Paper / Live Trading Mode Switch */}
          <TradingModeToggle />

          {/* Refresh Button */}
          {onRefresh && (
            <button
              onClick={onRefresh}
              disabled={isRefreshing}
              className="p-2 text-slate-400 hover:text-slate-200 bg-slate-800/60 hover:bg-slate-800 border border-slate-700/60 rounded-lg transition-colors"
              title="Refresh telemetry"
            >
              <RefreshCw className={`w-4 h-4 ${isRefreshing ? 'animate-spin text-emerald-400' : ''}`} />
            </button>
          )}

          {/* SEBI Emergency Panic Button */}
          <button
            onClick={() => setKillSwitchOpen(true)}
            className="flex items-center gap-2 px-3.5 py-1.5 bg-rose-600/15 hover:bg-rose-600/25 text-rose-300 border border-rose-500/40 hover:border-rose-500/70 rounded-lg text-xs font-semibold transition-all shadow-sm shadow-rose-950"
            title="SEBI Mandated Panic Square-Off"
          >
            <ShieldAlert className="w-4 h-4 text-rose-400" />
            <span>EMERGENCY EXIT</span>
          </button>
        </div>
      </header>

      {/* Emergency Exit Confirmation Modal */}
      {killSwitchOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4">
          <div className="bg-slate-900 border border-rose-500/40 rounded-xl max-w-md w-full p-6 shadow-2xl space-y-4 animate-in fade-in zoom-in duration-150">
            <div className="flex items-center gap-3 text-rose-400">
              <div className="w-10 h-10 rounded-full bg-rose-500/20 flex items-center justify-center">
                <AlertTriangle className="w-6 h-6 text-rose-400" />
              </div>
              <div>
                <h3 className="font-bold text-lg text-white">Emergency Kill-Switch</h3>
                <p className="text-xs text-rose-300/80">SEBI Regulated Risk Interlock</p>
              </div>
            </div>

            <div className="text-xs text-slate-300 space-y-2 bg-slate-950/60 p-3.5 rounded-lg border border-slate-800">
              <p>Triggering emergency exit will immediately:</p>
              <ul className="list-disc pl-4 space-y-1 text-slate-400">
                <li>Send market square-off orders for <strong>ALL open positions</strong> across Zerodha & Angel One.</li>
                <li>Cancel all pending entry, stop-loss, and target orders.</li>
                <li><strong>Freeze the automated risk engine</strong> from accepting new signals today.</li>
              </ul>
            </div>

            {killSuccess && (
              <div className="p-3 bg-emerald-500/10 border border-emerald-500/30 rounded-lg text-emerald-300 text-xs flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0" />
                <span>{killSuccess}</span>
              </div>
            )}

            <div className="flex items-center justify-end gap-3 pt-2">
              <button
                onClick={() => setKillSwitchOpen(false)}
                disabled={killLoading}
                className="px-4 py-2 text-xs font-medium text-slate-300 hover:bg-slate-800 rounded-lg transition-colors border border-slate-700"
              >
                Cancel
              </button>
              <button
                onClick={triggerEmergencyExit}
                disabled={killLoading || !!killSuccess}
                className="flex items-center gap-2 px-4 py-2 text-xs font-bold bg-rose-600 hover:bg-rose-500 text-white rounded-lg transition-all shadow-lg shadow-rose-900/40 disabled:opacity-50"
              >
                {killLoading ? (
                  <>
                    <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                    <span>Executing Square-Off...</span>
                  </>
                ) : (
                  <>
                    <Power className="w-3.5 h-3.5" />
                    <span>CONFIRM EMERGENCY EXIT</span>
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
