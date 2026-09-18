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
  Power,
  PlayCircle,
  StopCircle
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

  // Algo Service State & Kill-Switch
  const [algoActive, setAlgoActive] = useState<boolean>(true);
  const [killSwitchOpen, setKillSwitchOpen] = useState<boolean>(false);
  const [actionLoading, setActionLoading] = useState<boolean>(false);
  const [feedbackMsg, setFeedbackMsg] = useState<{ text: string; success: boolean } | null>(null);

  // Fetch initial Algo Status
  const checkAlgoStatus = async () => {
    try {
      const res = await api.get('/api/system/algo-status');
      if (typeof res.data?.algo_active === 'boolean') {
        setAlgoActive(res.data.algo_active);
      }
    } catch {
      // Ignore background check failure
    }
  };

  useEffect(() => {
    checkAlgoStatus();

    // Listen to WebSocket broadcasts or manual refresh
    const handleRefresh = () => {
      checkAlgoStatus();
    };
    window.addEventListener('manual_refresh', handleRefresh);
    return () => window.removeEventListener('manual_refresh', handleRefresh);
  }, []);

  // Update IST clock & market session
  useEffect(() => {
    const updateTimeAndStatus = () => {
      const now = new Date();
      const istString = now.toLocaleTimeString('en-IN', {
        timeZone: 'Asia/Kolkata',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      });
      setTime(istString);

      const istDate = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Kolkata' }));
      const hours = istDate.getHours();
      const mins = istDate.getMinutes();
      const totalMins = hours * 60 + mins;
      const day = istDate.getDay();

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

  const handleTerminateAlgo = async () => {
    setActionLoading(true);
    setFeedbackMsg(null);
    try {
      const res = await api.post('/api/system/algo-kill');
      setAlgoActive(false);
      setFeedbackMsg({
        text: res.data?.message || 'Algo service terminated: Engine halted and open positions squared off.',
        success: true,
      });
      if (onRefresh) onRefresh();
      window.dispatchEvent(new CustomEvent('manual_refresh'));
    } catch (err: any) {
      setFeedbackMsg({
        text: 'Action error: ' + (err.response?.data?.detail || err.message),
        success: false,
      });
    } finally {
      setActionLoading(false);
    }
  };

  const handleResumeAlgo = async () => {
    setActionLoading(true);
    setFeedbackMsg(null);
    try {
      const res = await api.post('/api/system/algo-resume');
      setAlgoActive(true);
      setFeedbackMsg({
        text: res.data?.message || 'Algo service resumed: Engine active and processing signals.',
        success: true,
      });
      if (onRefresh) onRefresh();
      window.dispatchEvent(new CustomEvent('manual_refresh'));
    } catch (err: any) {
      setFeedbackMsg({
        text: 'Action error: ' + (err.response?.data?.detail || err.message),
        success: false,
      });
    } finally {
      setActionLoading(false);
    }
  };

  const handleRefreshClick = () => {
    if (onRefresh) onRefresh();
    window.dispatchEvent(new CustomEvent('manual_refresh'));
    checkAlgoStatus();
  };

  return (
    <>
      <header className="h-16 bg-slate-900/95 backdrop-blur border-b border-slate-800 px-6 flex items-center justify-between sticky top-0 z-30">
        {/* Left: Clock, Market Session & Algo Status */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 font-mono text-sm text-slate-200 bg-slate-800/80 px-3 py-1.5 rounded-md border border-slate-700/60">
            <Clock className="w-4 h-4 text-emerald-400" />
            <span className="font-semibold tracking-wider">{time || '--:--:--'}</span>
            <span className="text-[10px] text-slate-400 font-sans">IST</span>
          </div>

          <div className="hidden sm:flex items-center gap-2">
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

          {/* Algo Engine State Badge */}
          <div className="flex items-center">
            <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-bold border transition-colors ${
              algoActive
                ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'
                : 'bg-rose-500/20 text-rose-300 border-rose-500/40 animate-pulse'
            }`}>
              <span className={`w-2 h-2 rounded-full ${algoActive ? 'bg-emerald-400' : 'bg-rose-500'}`} />
              <span>{algoActive ? 'ALGO: ACTIVE' : 'ALGO: HALTED'}</span>
            </span>
          </div>
        </div>

        {/* Right: Telemetry, Paper/Live Toggle, Refresh & Kill-Switch Button */}
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
          <button
            onClick={handleRefreshClick}
            disabled={isRefreshing}
            className="p-2 text-slate-400 hover:text-slate-200 bg-slate-800/60 hover:bg-slate-800 border border-slate-700/60 rounded-lg transition-colors cursor-pointer"
            title="Refresh all dashboard data"
          >
            <RefreshCw className={`w-4 h-4 ${isRefreshing ? 'animate-spin text-emerald-400' : ''}`} />
          </button>

          {/* Primary Algo Kill-Switch / Resume Button */}
          {algoActive ? (
            <button
              onClick={() => { setKillSwitchOpen(true); setFeedbackMsg(null); }}
              className="flex items-center gap-2 px-3.5 py-1.5 bg-rose-600/20 hover:bg-rose-600/30 text-rose-300 border border-rose-500/40 hover:border-rose-500/70 rounded-lg text-xs font-bold transition-all shadow-sm shadow-rose-950 cursor-pointer"
              title="Terminate Algo Trading Service Immediately"
            >
              <StopCircle className="w-4 h-4 text-rose-400" />
              <span>TERMINATE ALGO (KILL SWITCH)</span>
            </button>
          ) : (
            <button
              onClick={() => { setKillSwitchOpen(true); setFeedbackMsg(null); }}
              className="flex items-center gap-2 px-3.5 py-1.5 bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-500/40 hover:border-emerald-500/70 rounded-lg text-xs font-bold transition-all shadow-sm shadow-emerald-950 cursor-pointer"
              title="Resume Algo Trading Service"
            >
              <PlayCircle className="w-4 h-4 text-emerald-400" />
              <span>RESUME ALGO SERVICE</span>
            </button>
          )}
        </div>
      </header>

      {/* Algo Kill-Switch / Resume Management Modal */}
      {killSwitchOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
          <div className="bg-slate-900 border border-slate-700 rounded-2xl max-w-lg w-full p-6 shadow-2xl space-y-4 animate-in fade-in zoom-in duration-150">
            {algoActive ? (
              // Terminate Modal View
              <>
                <div className="flex items-center gap-3 text-rose-400">
                  <div className="w-12 h-12 rounded-2xl bg-rose-500/10 border border-rose-500/30 flex items-center justify-center">
                    <ShieldAlert className="w-7 h-7 text-rose-400" />
                  </div>
                  <div>
                    <h3 className="font-bold text-lg text-white">Terminate Algo Trading Service?</h3>
                    <p className="text-xs text-rose-300">SEBI Regulated Instant Kill-Switch</p>
                  </div>
                </div>

                <div className="text-xs text-slate-300 space-y-2.5 bg-slate-950/80 p-4 rounded-xl border border-slate-800">
                  <p className="font-semibold text-white">Activating the Kill-Switch will immediately:</p>
                  <ul className="list-disc pl-4 space-y-1.5 text-slate-300">
                    <li><strong>Halt the Strategy Engine</strong> — No new automated buy or sell orders will be placed.</li>
                    <li><strong>Square Off All Open Positions</strong> — Market exit orders sent across linked broker accounts.</li>
                    <li><strong>Cancel All Pending Orders</strong> — Entry, stoploss, and target orders cancelled immediately.</li>
                    <li><strong>Trip Risk Circuit Breaker</strong> — Trading is frozen until manually resumed by you.</li>
                  </ul>
                  <p className="text-[11px] text-emerald-400/90 pt-1 border-t border-slate-800">
                    ✓ <strong>Data Preservation:</strong> All historical trades, reports, and accounts remain completely safe in the database.
                  </p>
                </div>

                {feedbackMsg && (
                  <div className={`p-3 rounded-lg text-xs flex items-center gap-2 border ${
                    feedbackMsg.success
                      ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
                      : 'bg-rose-500/10 border-rose-500/30 text-rose-300'
                  }`}>
                    {feedbackMsg.success ? <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0" /> : <AlertTriangle className="w-4 h-4 text-rose-400 flex-shrink-0" />}
                    <span>{feedbackMsg.text}</span>
                  </div>
                )}

                <div className="flex items-center justify-end gap-3 pt-2">
                  <button
                    onClick={() => setKillSwitchOpen(false)}
                    disabled={actionLoading}
                    className="px-4 py-2 text-xs font-semibold text-slate-300 hover:bg-slate-800 rounded-lg transition-colors border border-slate-700"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleTerminateAlgo}
                    disabled={actionLoading}
                    className="flex items-center gap-2 px-5 py-2.5 text-xs font-bold bg-rose-600 hover:bg-rose-500 text-white rounded-lg transition-all shadow-lg shadow-rose-950 disabled:opacity-50"
                  >
                    {actionLoading ? (
                      <>
                        <RefreshCw className="w-4 h-4 animate-spin" />
                        <span>Terminating Service...</span>
                      </>
                    ) : (
                      <>
                        <StopCircle className="w-4 h-4" />
                        <span>CONFIRM: HALT &amp; TERMINATE ALGO</span>
                      </>
                    )}
                  </button>
                </div>
              </>
            ) : (
              // Resume Modal View
              <>
                <div className="flex items-center gap-3 text-emerald-400">
                  <div className="w-12 h-12 rounded-2xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center">
                    <PlayCircle className="w-7 h-7 text-emerald-400" />
                  </div>
                  <div>
                    <h3 className="font-bold text-lg text-white">Resume Algo Trading Service</h3>
                    <p className="text-xs text-emerald-300">Reactivate Automated Strategy Pipeline</p>
                  </div>
                </div>

                <div className="text-xs text-slate-300 space-y-2 bg-slate-950/80 p-4 rounded-xl border border-slate-800">
                  <p>Resuming will reset the risk circuit breaker and restore automated signal scanner checks and broker order routing.</p>
                </div>

                {feedbackMsg && (
                  <div className={`p-3 rounded-lg text-xs flex items-center gap-2 border ${
                    feedbackMsg.success
                      ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300'
                      : 'bg-rose-500/10 border-rose-500/30 text-rose-300'
                  }`}>
                    {feedbackMsg.success ? <CheckCircle2 className="w-4 h-4 text-emerald-400 flex-shrink-0" /> : <AlertTriangle className="w-4 h-4 text-rose-400 flex-shrink-0" />}
                    <span>{feedbackMsg.text}</span>
                  </div>
                )}

                <div className="flex items-center justify-end gap-3 pt-2">
                  <button
                    onClick={() => setKillSwitchOpen(false)}
                    disabled={actionLoading}
                    className="px-4 py-2 text-xs font-semibold text-slate-300 hover:bg-slate-800 rounded-lg transition-colors border border-slate-700"
                  >
                    Close
                  </button>
                  <button
                    onClick={handleResumeAlgo}
                    disabled={actionLoading}
                    className="flex items-center gap-2 px-5 py-2.5 text-xs font-bold bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg transition-all shadow-lg shadow-emerald-950 disabled:opacity-50"
                  >
                    {actionLoading ? (
                      <>
                        <RefreshCw className="w-4 h-4 animate-spin" />
                        <span>Resuming Engine...</span>
                      </>
                    ) : (
                      <>
                        <PlayCircle className="w-4 h-4" />
                        <span>CONFIRM: RESUME ALGO SERVICE</span>
                      </>
                    )}
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
