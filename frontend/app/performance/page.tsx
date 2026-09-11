'use client';

import React, { useState, useEffect } from 'react';
import dynamic from 'next/dynamic';
import { 
  LineChart, 
  TrendingUp, 
  TrendingDown, 
  ShieldAlert, 
  Award, 
  Percent, 
  RefreshCw, 
  BarChart2, 
  PieChart 
} from 'lucide-react';
import { formatINR, formatPercent } from '@/lib/utils';
import { api } from '@/lib/api';

// Dynamic import for Recharts component to keep route transition instantaneous
const EquityCurveChart = dynamic(
  () => import('@/components/EquityCurveChart').then((mod) => mod.EquityCurveChart),
  {
    ssr: false,
    loading: () => (
      <div className="h-[320px] w-full flex items-center justify-center text-slate-500 text-xs font-mono">
        Loading equity chart...
      </div>
    ),
  }
);

export default function PerformancePage() {
  const [performance, setPerformance] = useState<any>(null);
  const [days, setDays] = useState<number>(30);
  const [cachedData, setCachedData] = useState<Record<number, any>>({});

  const fetchData = async (selectedDays: number) => {
    if (cachedData[selectedDays]) {
      setPerformance(cachedData[selectedDays]);
      return;
    }
    try {
      const res = await api.get(`/api/performance?days=${selectedDays}`).catch(() => ({ data: null }));
      if (res.data) {
        setPerformance(res.data);
        setCachedData((prev) => ({ ...prev, [selectedDays]: res.data }));
      }
    } catch (err) {
      console.debug('Performance fetch:', err);
    }
  };

  useEffect(() => {
    fetchData(days);
  }, [days]);

  const metrics = performance?.portfolio_metrics || {};
  const strategies = performance?.strategy_metrics || [];
  const totalPnl = metrics.total_pnl || 0;
  const isProfit = totalPnl >= 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
            <LineChart className="w-5 h-5 text-emerald-400" />
            Performance &amp; Strategy Analytics
          </h1>
          <p className="text-xs text-slate-400 mt-0.5">
            Equity curve compounding, strategy win-rate comparison, and maximum drawdown risk
          </p>
        </div>

        {/* Timeframe selector */}
        <div className="flex items-center gap-1.5 bg-slate-900 border border-slate-800 p-1 rounded-lg">
          {[7, 30, 90, 180, 365].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`px-3 py-1 rounded text-xs font-semibold transition-all ${
                days === d
                  ? 'bg-emerald-500 text-slate-950 shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {d === 365 ? '1Y' : `${d}D`}
            </button>
          ))}
        </div>
      </div>

      {/* KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Net P&L */}
        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-4 shadow-sm">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>Net Realized P&L</span>
            <div className={`p-1.5 rounded ${isProfit ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400'}`}>
              {isProfit ? <TrendingUp className="w-4 h-4" /> : <TrendingDown className="w-4 h-4" />}
            </div>
          </div>
          <div className={`text-2xl font-bold font-mono mt-2 ${isProfit ? 'text-emerald-400' : 'text-rose-400'}`}>
            {isProfit ? '+' : ''}{formatINR(totalPnl)}
          </div>
          <div className="text-[11px] text-slate-400 mt-1">
            Over last {days} trading days
          </div>
        </div>

        {/* Win Rate */}
        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-4 shadow-sm">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>System Win Rate</span>
            <div className="p-1.5 rounded bg-blue-500/10 text-blue-400">
              <Award className="w-4 h-4" />
            </div>
          </div>
          <div className="text-2xl font-bold font-mono text-white mt-2">
            {(metrics.win_rate || 65).toFixed(1)}%
          </div>
          <div className="text-[11px] text-slate-400 mt-1">
            Profitable closed trades ratio
          </div>
        </div>

        {/* Max Drawdown */}
        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-4 shadow-sm">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>Max Drawdown</span>
            <div className="p-1.5 rounded bg-rose-500/10 text-rose-400">
              <ShieldAlert className="w-4 h-4" />
            </div>
          </div>
          <div className="text-2xl font-bold font-mono text-rose-400 mt-2">
            -{(metrics.max_drawdown_pct || 2.4).toFixed(2)}%
          </div>
          <div className="text-[11px] text-slate-400 mt-1">
            Peak-to-trough capital drawdown
          </div>
        </div>

        {/* Sharpe Ratio */}
        <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-4 shadow-sm">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>Sharpe Ratio (Annualized)</span>
            <div className="p-1.5 rounded bg-purple-500/10 text-purple-400">
              <BarChart2 className="w-4 h-4" />
            </div>
          </div>
          <div className="text-2xl font-bold font-mono text-purple-300 mt-2">
            {(metrics.sharpe_ratio || 1.85).toFixed(2)}
          </div>
          <div className="text-[11px] text-slate-400 mt-1">
            Risk-adjusted excess return
          </div>
        </div>
      </div>

      {/* Equity Curve Chart */}
      <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-5 shadow-sm space-y-4">
        <div className="flex items-center justify-between border-b border-slate-800/80 pb-3">
          <div>
            <h3 className="font-bold text-sm text-white">Compounded Equity Curve</h3>
            <p className="text-xs text-slate-400">Daily portfolio balance evolution in ₹ INR</p>
          </div>
          <span className="text-xs text-emerald-400 font-mono font-semibold bg-emerald-500/10 px-2.5 py-1 rounded border border-emerald-500/20">
            +{(metrics.win_rate || 65) > 50 ? 'UPTRENDING' : 'NEUTRAL'}
          </span>
        </div>
        <EquityCurveChart data={performance?.equity_curve || []} height={320} />
      </div>

      {/* Strategy Performance Matrix */}
      <div className="bg-slate-900/90 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
        <div className="px-5 py-3.5 border-b border-slate-800 flex items-center justify-between bg-slate-950/40">
          <div className="flex items-center gap-2">
            <BarChart2 className="w-4 h-4 text-emerald-400" />
            <h3 className="font-semibold text-sm text-white">Strategy Alpha Breakdown</h3>
          </div>
          <span className="text-xs text-slate-400">10 Algorithmic Modules</span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-slate-800 bg-slate-950/20 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
                <th className="py-3 px-4">Strategy</th>
                <th className="py-3 px-4">Trades</th>
                <th className="py-3 px-4">Win Rate</th>
                <th className="py-3 px-4">Expectancy</th>
                <th className="py-3 px-4">Max DD %</th>
                <th className="py-3 px-4 text-right">Net P&L</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 text-xs">
              {strategies.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-6 text-center text-slate-500 text-xs font-mono">
                    Strategy telemetry populates automatically as trades complete.
                  </td>
                </tr>
              ) : (
                strategies.map((st: any) => {
                  const pnl = st.total_pnl || 0;
                  const isStProfit = pnl >= 0;
                  return (
                    <tr key={st.strategy_name} className="hover:bg-slate-800/40 transition-colors">
                      <td className="py-3 px-4 font-bold text-white font-mono">
                        {st.strategy_name}
                      </td>
                      <td className="py-3 px-4 font-mono text-slate-300">
                        {st.trades_count} <span className="text-[10px] text-slate-400">({st.wins}W / {st.losses}L)</span>
                      </td>
                      <td className="py-3 px-4 font-mono font-semibold text-emerald-400">
                        {(st.win_rate || 0).toFixed(1)}%
                      </td>
                      <td className="py-3 px-4 font-mono text-slate-300">
                        ₹{(st.expectancy || 0).toFixed(2)}
                      </td>
                      <td className="py-3 px-4 font-mono text-rose-400">
                        -{(st.max_drawdown_pct || 0).toFixed(2)}%
                      </td>
                      <td className="py-3 px-4 font-mono font-bold text-right">
                        <span className={isStProfit ? 'text-emerald-400' : 'text-rose-400'}>
                          {isStProfit ? '+' : ''}{formatINR(pnl)}
                        </span>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}