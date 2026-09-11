'use client';

import React, { useState, useEffect } from 'react';
import { ShieldCheck, Zap, AlertTriangle } from 'lucide-react';
import { api } from '@/lib/api';

export function TradingModeToggle() {
  const [isPaper, setIsPaper] = useState<boolean>(true);
  const [loading, setLoading] = useState<boolean>(false);
  const [showConfirm, setShowConfirm] = useState<boolean>(false);

  useEffect(() => {
    api.get('/api/trading-mode')
      .then((res) => {
        if (typeof res.data?.paper_trading_mode === 'boolean') {
          setIsPaper(res.data.paper_trading_mode);
        }
      })
      .catch(() => {});
  }, []);

  const handleToggle = async (newMode: boolean) => {
    if (!newMode) {
      // Switching to LIVE REAL MONEY -> Show Confirmation
      setShowConfirm(true);
      return;
    }

    // Switch to Paper immediately
    setLoading(true);
    try {
      await api.post('/api/trading-mode', { paper_trading_mode: true });
      setIsPaper(true);
    } finally {
      setLoading(false);
    }
  };

  const confirmLiveTrading = async () => {
    setLoading(true);
    try {
      await api.post('/api/trading-mode', { paper_trading_mode: false });
      setIsPaper(false);
      setShowConfirm(false);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <div className="flex items-center gap-1 bg-slate-900 border border-slate-800 p-1 rounded-lg">
        <button
          onClick={() => handleToggle(true)}
          disabled={loading}
          className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-bold transition-all ${
            isPaper
              ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40 shadow-sm'
              : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
          <span>PAPER (DEMO)</span>
        </button>

        <button
          onClick={() => handleToggle(false)}
          disabled={loading}
          className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-bold transition-all ${
            !isPaper
              ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40 shadow-sm'
              : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          <span className="w-2 h-2 rounded-full bg-rose-500" />
          <span>LIVE (REAL ₹)</span>
        </button>
      </div>

      {/* Confirmation Modal when switching to LIVE TRADING */}
      {showConfirm && (
        <div className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="w-full max-w-md bg-slate-900 border border-rose-500/40 rounded-2xl p-6 shadow-2xl space-y-4 animate-in fade-in zoom-in-95">
            <div className="flex items-center gap-3 text-rose-400">
              <div className="p-3 bg-rose-500/10 border border-rose-500/30 rounded-xl">
                <AlertTriangle className="w-6 h-6 text-rose-400" />
              </div>
              <div>
                <h3 className="font-bold text-base text-white">Switch to LIVE Real-Money Trading?</h3>
                <p className="text-xs text-slate-400">Broker order routing will be activated</p>
              </div>
            </div>

            <div className="p-3.5 bg-slate-950/60 border border-slate-800 rounded-xl text-xs text-slate-300 space-y-2">
              <p>
                ⚠️ All strategy signals and webhook triggers will place <strong>real orders with real capital</strong> via your linked broker account.
              </p>
              <p className="text-slate-400">
                SEBI risk limits, position sizing, and maximum daily loss caps will continue to protect your account.
              </p>
            </div>

            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                onClick={() => setShowConfirm(false)}
                className="px-4 py-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-semibold transition-colors"
              >
                Cancel (Keep Paper)
              </button>
              <button
                onClick={confirmLiveTrading}
                className="px-4 py-2 bg-rose-600 hover:bg-rose-500 text-white rounded-lg text-xs font-bold transition-colors shadow-lg shadow-rose-950 flex items-center gap-1.5"
              >
                <Zap className="w-3.5 h-3.5" />
                <span>Confirm Live Broker Execution</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
