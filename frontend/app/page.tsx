'use client';

import React, { useState, useEffect } from 'react';
import Link from 'next/link';
import { 
  RefreshCw, 
  ArrowUpRight, 
  TrendingUp, 
  Activity, 
  ShieldCheck, 
  AlertCircle,
  BarChart3,
  Sliders
} from 'lucide-react';
import { StatsCards } from '@/components/StatsCards';
import { LivePositionsTable, Position } from '@/components/LivePositionsTable';
import { StrategySignalsWidget, StrategySignal } from '@/components/StrategySignalsWidget';
import { RegimeRadarWidget, RegimeItem } from '@/components/RegimeRadarWidget';
import { api } from '@/lib/api';
import { ws } from '@/lib/ws';
import { useTradingStore } from '@/lib/store';

export default function DashboardHome() {
  const livePortfolio = useTradingStore((s) => s.portfolio);
  const [positions, setPositions] = useState<Position[]>([]);
  const [recommendations, setRecommendations] = useState<StrategySignal[]>([]);
  const [regimes, setRegimes] = useState<RegimeItem[]>([]);
  const [statusSummary, setStatusSummary] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchData = async () => {
    try {
      setError(null);
      const [portfolioRes, recsRes, regimesRes, statusRes] = await Promise.all([
        api.get('/api/portfolio').catch(() => ({ data: { total_capital: 100000, available_margin: 100000, deployed_capital: 0, total_unrealized_pnl: 0, positions: [] } })),
        api.get('/api/recommendations?status=PENDING&limit=6').catch(() => ({ data: [] })),
        api.get('/api/regime').catch(() => ({ data: [] })),
        api.get('/status').catch(() => ({ data: { trades_today: 0, max_trades: 3, daily_pnl: 0, capital: 100000 } })),
      ]);

      const portData = portfolioRes.data || {};
      if (portData.positions) setPositions(portData.positions);
      if (recsRes.data) setRecommendations(recsRes.data);
      if (regimesRes.data) setRegimes(regimesRes.data);
      if (statusRes.data) setStatusSummary(statusRes.data);
    } catch (err: any) {
      console.debug('Dashboard background fetch:', err);
    }
  };

  useEffect(() => {
    fetchData();
    // Connect WebSocket
    ws.connect('/ws/live', (data) => {
      if (data.type === 'recommendation_update') {
        setRecommendations((prev) => [data.payload, ...prev.filter((r) => r.id !== data.payload.id)]);
      }
    });

    window.addEventListener('manual_refresh', fetchData);

    const interval = setInterval(fetchData, 20000);
    return () => {
      clearInterval(interval);
      window.removeEventListener('manual_refresh', fetchData);
    };
  }, []);

  return (
    <div className="space-y-6">
      {/* Top Banner / Error Notice if offline */}
      {error && (
        <div className="p-3.5 bg-amber-500/10 border border-amber-500/30 rounded-xl flex items-center justify-between text-amber-300 text-xs">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-amber-400 flex-shrink-0" />
            <span>{error}</span>
          </div>
          <button 
            onClick={fetchData}
            className="px-3 py-1 bg-amber-500/20 hover:bg-amber-500/30 rounded font-semibold text-amber-200 transition-colors"
          >
            Retry
          </button>
        </div>
      )}

      {/* Row 1: Real-time Stats Cards */}
      <StatsCards
        totalCapital={livePortfolio?.total_capital || statusSummary?.capital || 100000}
        deployedCapital={livePortfolio?.deployed_capital || 0}
        availableMargin={livePortfolio?.available_margin || 100000}
        totalUnrealizedPnl={livePortfolio?.total_unrealized_pnl || 0}
        realizedPnl={statusSummary?.daily_pnl || 0}
        tradesToday={statusSummary?.trades_today || 0}
        maxTrades={statusSummary?.max_trades || 3}
        positionsCount={livePortfolio?.positions ? livePortfolio.positions.length : positions.length}
      />

      {/* Row 2: Live Open Positions Table */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-bold text-white tracking-tight flex items-center gap-2">
            <Activity className="w-4 h-4 text-emerald-400" />
            Execution Telemetry &amp; Open Positions
          </h2>
          <Link
            href="/portfolio"
            prefetch={true}
            className="text-xs text-emerald-400 hover:text-emerald-300 font-medium flex items-center gap-1 transition-colors"
          >
            View Full Portfolio &rarr;
          </Link>
        </div>
        <LivePositionsTable 
          positions={livePortfolio?.positions || positions} 
          onRefresh={fetchData} 
        />
      </div>

      {/* Row 3: Strategy Signals & Market Regime Radar */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left: AI Ranked Strategy Recommendations */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-bold text-white tracking-tight flex items-center gap-2">
              <BarChart3 className="w-4 h-4 text-emerald-400" />
              Strategy Engine Signals
            </h2>
            <Link
              href="/recommendations"
              prefetch={true}
              className="text-xs text-emerald-400 hover:text-emerald-300 font-medium flex items-center gap-1 transition-colors"
            >
              All Signals &rarr;
            </Link>
          </div>
          <StrategySignalsWidget recommendations={recommendations} onRefresh={fetchData} />
        </div>

        {/* Right: Market Regime Radar */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-bold text-white tracking-tight flex items-center gap-2">
              <Sliders className="w-4 h-4 text-blue-400" />
              Market Regime Classification
            </h2>
            <span className="text-xs text-slate-400">Real-time Technicals</span>
          </div>
          <RegimeRadarWidget regimes={regimes} />
        </div>
      </div>
    </div>
  );
}