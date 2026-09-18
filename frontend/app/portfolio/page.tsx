'use client';

import React, { useState, useEffect } from 'react';
import { 
  Briefcase, 
  Layers, 
  Clock, 
  ShieldCheck, 
  RefreshCw, 
  History, 
  ArrowUpRight, 
  ArrowDownRight,
  TrendingUp
} from 'lucide-react';
import { StatsCards } from '@/components/StatsCards';
import { LivePositionsTable, Position } from '@/components/LivePositionsTable';
import { formatINR, formatPercent, formatISTTime } from '@/lib/utils';
import { api } from '@/lib/api';
import { useTradingStore } from '@/lib/store';

export default function PortfolioPage() {
  const livePortfolio = useTradingStore((s) => s.portfolio);
  const [positions, setPositions] = useState<Position[]>([]);
  const [statusSummary, setStatusSummary] = useState<any>(null);
  const [tab, setTab] = useState<'positions' | 'orders'>('positions');

  const fetchData = async () => {
    try {
      const [portRes, statusRes] = await Promise.all([
        api.get('/api/portfolio').catch(() => ({ data: { total_capital: 100000, available_margin: 100000, deployed_capital: 0, total_unrealized_pnl: 0, positions: [] } })),
        api.get('/status').catch(() => ({ data: { trades_today: 0, max_trades: 3, daily_pnl: 0, capital: 100000 } })),
      ]);

      const pData = portRes.data || {};
      if (pData.positions) setPositions(pData.positions);
      if (statusRes.data) setStatusSummary(statusRes.data);
    } catch (err) {
      console.debug('Error fetching portfolio:', err);
    }
  };

  useEffect(() => {
    fetchData();
    window.addEventListener('manual_refresh', fetchData);
    return () => window.removeEventListener('manual_refresh', fetchData);
  }, []);

  const activePositions = livePortfolio?.positions || positions;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
            <Briefcase className="w-5 h-5 text-emerald-400" />
            Portfolio &amp; Position Manager
          </h1>
          <p className="text-xs text-slate-400 mt-0.5">
            Real-time capital deployment, active legs, and trade execution ledger
          </p>
        </div>
        <button
          onClick={fetchData}
          className="flex items-center gap-2 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 rounded-lg text-xs font-semibold transition-colors"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          <span>Refresh</span>
        </button>
      </div>

      {/* Stats Cards */}
      <StatsCards
        totalCapital={livePortfolio?.total_capital || 100000}
        deployedCapital={livePortfolio?.deployed_capital || 0}
        availableMargin={livePortfolio?.available_margin || 100000}
        totalUnrealizedPnl={livePortfolio?.total_unrealized_pnl || 0}
        realizedPnl={statusSummary?.daily_pnl || 0}
        tradesToday={statusSummary?.trades_today || 0}
        maxTrades={statusSummary?.max_trades || 3}
        positionsCount={activePositions.length}
      />

      {/* Tab Controls */}
      <div className="flex items-center gap-2 border-b border-slate-800 pb-2">
        <button
          onClick={() => setTab('positions')}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
            tab === 'positions'
              ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 shadow-sm'
              : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900 border border-transparent'
          }`}
        >
          <Layers className="w-4 h-4" />
          <span>Open Positions ({activePositions.length})</span>
        </button>
        <button
          onClick={() => setTab('orders')}
          className={`flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all ${
            tab === 'orders'
              ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 shadow-sm'
              : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900 border border-transparent'
          }`}
        >
          <History className="w-4 h-4" />
          <span>Execution Ledger &amp; Orders</span>
        </button>
      </div>

      {/* Tab Content */}
      {tab === 'positions' ? (
        <LivePositionsTable positions={activePositions} onRefresh={fetchData} />
      ) : (
        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-8 text-center space-y-3">
          <History className="w-10 h-10 text-slate-600 mx-auto" />
          <div>
            <h4 className="text-sm font-semibold text-slate-300">Order Execution Audit Trail</h4>
            <p className="text-xs text-slate-500 max-w-sm mx-auto mt-1">
              All broker orders placed with exchange Algo-IDs are persisted and synchronized with SEBI compliance records.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}