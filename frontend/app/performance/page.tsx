'use client';

import { useState, useEffect } from 'react';
import { api } from '@/lib/api';
import { EquityCurveChart } from '@/components/EquityCurveChart';

interface StrategyMetric {
  strategy_name: string;
  total_pnl: number;
  trades_count: number;
  wins: number;
  losses: number;
  win_rate: number;
  expectancy: number;
  max_drawdown: number;
  max_drawdown_pct: number;
  sharpe_ratio: number | null;
}

interface PerformanceData {
  equity_curve: Array<{ date: string; equity: number; pnl: number; trades_count: number; win_rate: number; max_drawdown_pct: number }>;
  daily_pnl: Array<{ date: string; pnl: number; realized_pnl: number; unrealized_pnl: number }>;
  strategy_metrics: StrategyMetric[];
  portfolio_metrics: Record<string, any>;
}

export default function PerformancePage() {
  const [performance, setPerformance] = useState<PerformanceData | null>(null);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(30);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const res = await api.get(`/api/performance?days=${days}`);
        setPerformance(res.data);
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, [days]);

  if (loading) {
    return <div className="p-8 text-center">Loading...</div>;
  }

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-dark-900 dark:text-dark-50">Performance Analytics</h1>
          <p className="text-dark-500">Equity curve, strategy metrics & drawdown analysis</p>
        </div>
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="px-3 py-2 border border-dark-300 dark:border-dark-600 rounded-lg bg-white dark:bg-dark-800"
        >
          <option value={7}>7 Days</option>
          <option value={30}>30 Days</option>
          <option value={90}>90 Days</option>
          <option value={180}>180 Days</option>
          <option value={365}>1 Year</option>
        </select>
      </div>

      {/* Portfolio Metrics */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <MetricCard
          title="Total P&L"
          value={performance?.portfolio_metrics?.total_pnl ?? 0}
          prefix="₹"
          color={performance && performance.portfolio_metrics?.total_pnl >= 0 ? 'primary' : 'danger'}
        />
        <MetricCard
          title="Win Rate"
          value={performance?.portfolio_metrics?.win_rate ?? 0}
          suffix="%"
          color="blue"
        />
        <MetricCard
          title="Max Drawdown"
          value={performance?.portfolio_metrics?.max_drawdown_pct ?? 0}
          suffix="%"
          color="danger"
        />
        <MetricCard
          title="Sharpe Ratio"
          value={performance?.portfolio_metrics?.sharpe_ratio ?? 0}
          color="purple"
        />
      </div>

      {/* Equity Curve */}
      <div className="bg-white dark:bg-dark-800 rounded-xl border border-dark-200 dark:border-dark-700 p-6">
        <h2 className="text-lg font-semibold text-dark-900 dark:text-dark-50 mb-4">Equity Curve</h2>
        <EquityCurveChart data={performance?.equity_curve ?? []} height={350} />
      </div>

      {/* Daily P&L Bar Chart */}
      <div className="bg-white dark:bg-dark-800 rounded-xl border border-dark-200 dark:border-dark-700 p-6">
        <h2 className="text-lg font-semibold text-dark-900 dark:text-dark-50 mb-4">Daily P&L</h2>
        <DailyPnLChart data={performance?.daily_pnl ?? []} height={250} />
      </div>

      {/* Strategy Performance Table */}
      <div className="bg-white dark:bg-dark-800 rounded-xl border border-dark-200 dark:border-dark-700 overflow-hidden">
        <div className="p-6 border-b border-dark-200 dark:border-dark-700">
          <h2 className="text-lg font-semibold text-dark-900 dark:text-dark-50">Strategy Performance</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-dark-500 border-b border-dark-200 dark:border-dark-700">
                <th className="p-4 font-medium">Strategy</th>
                <th className="p-4 font-medium">P&L</th>
                <th className="p-4 font-medium">Trades</th>
                <th className="p-4 font-medium">Wins</th>
                <th className="p-4 font-medium">Losses</th>
                <th className="p-4 font-medium">Win Rate</th>
                <th className="p-4 font-medium">Expectancy</th>
                <th className="p-4 font-medium">Max DD</th>
                <th className="p-4 font-medium">Sharpe</th>
              </tr>
            </thead>
            <tbody>
              {performance?.strategy_metrics.map((strat) => (
                <tr key={strat.strategy_name} className="border-b border-dark-100 dark:border-dark-800 hover:bg-dark-50 dark:hover:bg-dark-900/50">
                  <td className="p-4 font-medium text-dark-900 dark:text-dark-50">{strat.strategy_name}</td>
                  <td className="p-4 tabular-nums">
                    <span className={strat.total_pnl >= 0 ? 'text-primary-600' : 'text-danger-600'}>
                      ₹{strat.total_pnl.toFixed(2)}
                    </span>
                  </td>
                  <td className="p-4 tabular-nums">{strat.trades_count}</td>
                  <td className="p-4 tabular-nums text-primary-600">{strat.wins}</td>
                  <td className="p-4 tabular-nums text-danger-600">{strat.losses}</td>
                  <td className="p-4 tabular-nums">{strat.win_rate.toFixed(1)}%</td>
                  <td className="p-4 tabular-nums">₹{strat.expectancy.toFixed(2)}</td>
                  <td className="p-4 tabular-nums text-danger-600">{strat.max_drawdown_pct.toFixed(1)}%</td>
                  <td className="p-4 tabular-nums">{strat.sharpe_ratio?.toFixed(2) ?? 'N/A'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function MetricCard({ title, value, prefix = '', suffix = '', color = 'primary' }: {
  title: string;
  value: number;
  prefix?: string;
  suffix?: string;
  color?: 'primary' | 'danger' | 'blue' | 'amber' | 'purple';
}) {
  const colorClasses: Record<string, string> = {
    primary: 'text-primary-600 dark:text-primary-400',
    danger: 'text-danger-600 dark:text-danger-400',
    blue: 'text-blue-600 dark:text-blue-400',
    amber: 'text-amber-600 dark:text-amber-400',
    purple: 'text-purple-600 dark:text-purple-400',
  };

  return (
    <div className="bg-white dark:bg-dark-800 rounded-xl border border-dark-200 dark:border-dark-700 p-5">
      <p className="text-sm text-dark-500">{title}</p>
      <p className="mt-1 text-2xl font-bold tabular-nums">
        <span className={colorClasses[color]}>{prefix}{value.toFixed(suffix === '%' ? 2 : 2)}{suffix}</span>
      </p>
    </div>
  );
}

function DailyPnLChart({ data, height = 250 }: { data: Array<{ date: string; pnl: number; realized_pnl: number; unrealized_pnl: number }>; height?: number }) {
  // Simple bar chart using CSS
  if (!data || data.length === 0) {
    return <div className="h-full flex items-center justify-center text-dark-500">No data</div>;
  }

  const maxPnL = Math.max(...data.map(d => Math.abs(d.pnl)));
  
  return (
    <div className="h-[{height}px] flex items-end justify-center gap-1 px-2" style={{ height }}>
      {data.map((d) => (
        <div
          key={d.date}
          className="flex-1 max-w-[30px] transition-all hover:opacity-80"
          title={`${new Date(d.date).toLocaleDateString()}: ₹${d.pnl.toFixed(2)}`}
        >
          <div
            className={`rounded-t transition-all ${d.pnl >= 0 ? 'bg-primary-500' : 'bg-danger-500'}`}
            style={{
              height: `${Math.max((Math.abs(d.pnl) / maxPnL) * 100, 2)}%`,
              minHeight: '2px',
            }}
          />
        </div>
      ))}
    </div>
  );
}