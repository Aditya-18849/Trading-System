'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { PortfolioCard } from '@/components/PortfolioCard';
import { RegimeIndicator } from '@/components/RegimeIndicator';
import { KillSwitch } from '@/components/KillSwitch';
import { RecommendationCard } from '@/components/RecommendationCard';
import { EquityCurveChart } from '@/components/EquityCurveChart';
import { api } from '@/lib/api';
import { ws } from '@/lib/ws';

interface PortfolioData {
  positions: Array<{
    symbol: string;
    exchange: string;
    quantity: number;
    avg_price: number;
    ltp: number;
    unrealized_pnl: number;
    product: string;
  }>;
  total_capital: number;
  deployed_capital: number;
  available_margin: number;
  total_unrealized_pnl: number;
  timestamp: string;
}

interface Recommendation {
  id: string;
  symbol: string;
  exchange: string;
  direction: 'BUY' | 'SELL';
  entry_price: number;
  stoploss_price: number;
  target_price: number;
  quantity: number;
  capital_at_risk: number;
  risk_reward_ratio: number | null;
  confidence_score: number;
  regime: string;
  top_strategy_name: string;
  ai_summary: string | null;
  ai_key_risks: string | null;
  ai_invalidation: string | null;
  status: string;
  created_at: string;
  expires_at: string | null;
}

interface RegimeData {
  symbol: string;
  exchange: string;
  regime: string;
  adx: number | null;
  atr: number | null;
  bb_bandwidth: number | null;
  ema_slope: number | null;
  confidence: number;
  metadata: Record<string, any>;
  timestamp: string;
}

interface PerformanceData {
  equity_curve: Array<{ date: string; equity: number; pnl: number; trades_count: number; win_rate: number; max_drawdown_pct: number }>;
  daily_pnl: Array<{ date: string; pnl: number; realized_pnl: number; unrealized_pnl: number }>;
  strategy_metrics: Array<{
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
  }>;
  portfolio_metrics: Record<string, any>;
}

export default function DashboardHome() {
  const [portfolio, setPortfolio] = useState<PortfolioData | null>(null);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [regimes, setRegimes] = useState<RegimeData[]>([]);
  const [performance, setPerformance] = useState<PerformanceData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [autoExecute, setAutoExecute] = useState(false);

  const fetchData = async () => {
    try {
      setError(null);
      const [portfolioRes, recsRes, regimesRes, perfRes] = await Promise.all([
        api.get('/api/portfolio'),
        api.get('/api/recommendations?status=PENDING&limit=5'),
        api.get('/api/regime'),
        api.get('/api/performance?days=30'),
      ]);
      setPortfolio(portfolioRes.data);
      setRecommendations(recsRes.data);
      setRegimes(regimesRes.data);
      setPerformance(perfRes.data);
    } catch (err) {
      setError('Failed to load dashboard data');
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30000); // Refresh every 30s
    return () => clearInterval(interval);
  }, []);

  // WebSocket for live updates
  useEffect(() => {
    ws.connect('/ws/live', (data) => {
      if (data.type === 'portfolio_update') {
        setPortfolio(data.payload);
      } else if (data.type === 'recommendation_update') {
        setRecommendations(prev => [...prev.filter(r => r.id !== data.payload.id), data.payload]);
      } else if (data.type === 'trade_update') {
        // Trigger portfolio refresh
        fetchData();
      }
    });
    return () => ws.disconnect();
  }, []);

  const handleExecute = async (rec: Recommendation) => {
    if (!confirm(`Execute ${rec.direction} ${rec.symbol} x${rec.quantity} @ ₹${rec.entry_price}?`)) return;
    try {
      await api.post('/api/execute', { recommendation_id: rec.id, confirm: true });
      fetchData();
    } catch (err) {
      alert('Execution failed');
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="animate-spin rounded-full h-12 w-12 border-4 border-primary-500 border-t-transparent"></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-8 text-center text-danger-500">
        {error}
        <button onClick={fetchData} className="mt-4 px-4 py-2 bg-primary-500 text-white rounded">
          Retry
        </button>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header with Kill Switch */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-dark-900 dark:text-dark-50">Trading Dashboard</h1>
          <p className="text-dark-500 dark:text-dark-400">
            SEBI-Compliant Algo Trading System • {new Date().toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' })} IST
          </p>
        </div>
        <div className="flex items-center gap-4">
          <KillSwitch />
          <RegimeIndicator regimes={regimes} />
        </div>
      </div>

      {/* Portfolio Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <PortfolioCard
          title="Total Capital"
          value={portfolio?.total_capital ?? 0}
          prefix="₹"
          color="primary"
        />
        <PortfolioCard
          title="Deployed Capital"
          value={portfolio?.deployed_capital ?? 0}
          prefix="₹"
          color="blue"
        />
        <PortfolioCard
          title="Available Margin"
          value={portfolio?.available_margin ?? 0}
          prefix="₹"
          color="amber"
        />
        <PortfolioCard
          title="Unrealized P&L"
          value={portfolio?.total_unrealized_pnl ?? 0}
          prefix="₹"
          color={portfolio && portfolio.total_unrealized_pnl >= 0 ? 'primary' : 'danger'}
        />
      </div>

      {/* Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Column - Recommendations & Positions */}
        <div className="lg:col-span-2 space-y-6">
          {/* Recommendations Panel */}
          <div className="bg-white dark:bg-dark-800 rounded-xl border border-dark-200 dark:border-dark-700 p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-dark-900 dark:text-dark-50">
                AI Recommendations
                <span className="ml-2 text-sm font-normal text-dark-500">({recommendations.length})</span>
              </h2>
              <label className="flex items-center gap-2 text-sm text-dark-500">
                <input
                  type="checkbox"
                  checked={autoExecute}
                  onChange={(e) => setAutoExecute(e.target.checked)}
                  className="w-4 h-4 rounded border-dark-300 text-primary-500 focus:ring-primary-500"
                />
                Auto-Execute
              </label>
            </div>
            {recommendations.length === 0 ? (
              <div className="text-center py-8 text-dark-500">
                No pending recommendations. Strategy engine runs at market open (09:15 IST).
              </div>
            ) : (
              <div className="space-y-3 max-h-96 overflow-y-auto scrollbar-thin">
                {recommendations.map((rec) => (
                  <RecommendationCard
                    key={rec.id}
                    recommendation={rec}
                    onExecute={() => handleExecute(rec)}
                    autoExecute={autoExecute}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Current Positions */}
          <div className="bg-white dark:bg-dark-800 rounded-xl border border-dark-200 dark:border-dark-700 p-6">
            <h2 className="text-lg font-semibold text-dark-900 dark:text-dark-50 mb-4">
              Open Positions
              <span className="ml-2 text-sm font-normal text-dark-500">({portfolio?.positions.length ?? 0})</span>
            </h2>
            {portfolio?.positions.length === 0 ? (
              <div className="text-center py-8 text-dark-500">No open positions</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-dark-500 border-b border-dark-200 dark:border-dark-700">
                      <th className="pb-2 font-medium">Symbol</th>
                      <th className="pb-2 font-medium">Qty</th>
                      <th className="pb-2 font-medium">Avg Price</th>
                      <th className="pb-2 font-medium">LTP</th>
                      <th className="pb-2 font-medium">Unrealized P&L</th>
                      <th className="pb-2 font-medium">Product</th>
                    </tr>
                  </thead>
                  <tbody>
                    {portfolio?.positions.map((pos) => (
                      <tr key={`${pos.symbol}-${pos.exchange}`} className="border-b border-dark-100 dark:border-dark-800">
                        <td className="py-2 font-medium">{pos.symbol}</td>
                        <td className="py-2">{pos.quantity}</td>
                        <td className="py-2">₹{pos.avg_price.toFixed(2)}</td>
                        <td className="py-2">₹{pos.ltp.toFixed(2)}</td>
                        <td className="py-2 font-medium tabular-nums">
                          <span className={pos.unrealized_pnl >= 0 ? 'text-primary-600' : 'text-danger-600'}>
                            ₹{pos.unrealized_pnl.toFixed(2)}
                          </span>
                        </td>
                        <td className="py-2">{pos.product}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>

        {/* Right Column - Performance & Regime */}
        <div className="space-y-6">
          {/* Equity Curve */}
          <div className="bg-white dark:bg-dark-800 rounded-xl border border-dark-200 dark:border-dark-700 p-6">
            <h2 className="text-lg font-semibold text-dark-900 dark:text-dark-50 mb-4">Equity Curve (30D)</h2>
            <EquityCurveChart
              data={performance?.equity_curve ?? []}
              height={250}
            />
          </div>

          {/* Strategy Performance */}
          <div className="bg-white dark:bg-dark-800 rounded-xl border border-dark-200 dark:border-dark-700 p-6">
            <h2 className="text-lg font-semibold text-dark-900 dark:text-dark-50 mb-4">Strategy Performance</h2>
            <div className="space-y-2 max-h-64 overflow-y-auto scrollbar-thin">
              {performance?.strategy_metrics.map((strat) => (
                <div key={strat.strategy_name} className="flex items-center justify-between p-3 bg-dark-50 dark:bg-dark-900 rounded-lg">
                  <div>
                    <p className="font-medium text-dark-900 dark:text-dark-50">{strat.strategy_name}</p>
                    <p className="text-xs text-dark-500">{strat.trades_count} trades • {strat.win_rate.toFixed(1)}% WR</p>
                  </div>
                  <div className="text-right">
                    <p className={`font-medium tabular-nums ${strat.total_pnl >= 0 ? 'text-primary-600' : 'text-danger-600'}`}>
                      ₹{strat.total_pnl.toFixed(2)}
                    </p>
                    <p className="text-xs text-dark-500">DD: {strat.max_drawdown_pct.toFixed(1)}%</p>
                  </div>
                </div>
              ))}
              {(!performance?.strategy_metrics || performance.strategy_metrics.length === 0) && (
                <p className="text-center text-dark-500 py-4">No strategy performance data</p>
              )}
            </div>
          </div>

          {/* Quick Stats */}
          <div className="bg-white dark:bg-dark-800 rounded-xl border border-dark-200 dark:border-dark-700 p-6">
            <h2 className="text-lg font-semibold text-dark-900 dark:text-dark-50 mb-4">Portfolio Metrics</h2>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-sm text-dark-500">Total P&L (30D)</p>
                <p className={`font-bold tabular-nums ${performance?.portfolio_metrics?.total_pnl >= 0 ? 'text-primary-600' : 'text-danger-600'}`}>
                  ₹{performance?.portfolio_metrics?.total_pnl?.toFixed(2) ?? '0.00'}
                </p>
              </div>
              <div>
                <p className="text-sm text-dark-500">Win Rate</p>
                <p className="font-bold tabular-nums">{performance?.portfolio_metrics?.win_rate?.toFixed(1) ?? '0'}%</p>
              </div>
              <div>
                <p className="text-sm text-dark-500">Max Drawdown</p>
                <p className="font-bold tabular-nums text-danger-600">
                  {performance?.portfolio_metrics?.max_drawdown_pct?.toFixed(2) ?? '0'}%
                </p>
              </div>
              <div>
                <p className="text-sm text-dark-500">Sharpe Ratio</p>
                <p className="font-bold tabular-nums">{performance?.portfolio_metrics?.sharpe_ratio?.toFixed(2) ?? 'N/A'}</p>
              </div>
              <div>
                <p className="text-sm text-dark-500">Total Trades</p>
                <p className="font-bold tabular-nums">{performance?.portfolio_metrics?.total_trades ?? 0}</p>
              </div>
              <div>
                <p className="text-sm text-dark-500">Best Strategy</p>
                <p className="font-medium text-dark-900 dark:text-dark-50">{performance?.portfolio_metrics?.best_strategy ?? 'N/A'}</p>
              </div>
              <div>
                <p className="text-sm text-dark-500">Worst Strategy</p>
                <p className="font-medium text-dark-900 dark:text-dark-50">{performance?.portfolio_metrics?.worst_strategy ?? 'N/A'}</p>
              </div>
              <div>
                <p className="text-sm text-dark-500">Today's P&L</p>
                <p className={`font-bold tabular-nums ${performance?.portfolio_metrics?.today_realized_pnl >= 0 ? 'text-primary-600' : 'text-danger-600'}`}>
                  ₹{performance?.portfolio_metrics?.today_realized_pnl?.toFixed(2) ?? '0.00'}
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Navigation Links */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 pt-4">
        <Link href="/portfolio" className="p-4 bg-white dark:bg-dark-800 rounded-xl border border-dark-200 dark:border-dark-700 hover:border-primary-500 transition-colors">
          <h3 className="font-semibold text-dark-900 dark:text-dark-50">Portfolio</h3>
          <p className="text-sm text-dark-500 mt-1">Detailed positions & margin</p>
        </Link>
        <Link href="/recommendations" className="p-4 bg-white dark:bg-dark-800 rounded-xl border border-dark-200 dark:border-dark-700 hover:border-primary-500 transition-colors">
          <h3 className="font-semibold text-dark-900 dark:text-dark-50">Recommendations</h3>
          <p className="text-sm text-dark-500 mt-1">All signals with AI rationale</p>
        </Link>
        <Link href="/performance" className="p-4 bg-white dark:bg-dark-800 rounded-xl border border-dark-200 dark:border-dark-700 hover:border-primary-500 transition-colors">
          <h3 className="font-semibold text-dark-900 dark:text-dark-50">Performance</h3>
          <p className="text-sm text-dark-500 mt-1">Equity curve & analytics</p>
        </Link>
        <Link href="/reports" className="p-4 bg-white dark:bg-dark-800 rounded-xl border border-dark-200 dark:border-dark-700 hover:border-primary-500 transition-colors">
          <h3 className="font-semibold text-dark-900 dark:text-dark-50">Daily Reports</h3>
          <p className="text-sm text-dark-500 mt-1">EOD summaries & history</p>
        </Link>
      </div>
    </div>
  );
}