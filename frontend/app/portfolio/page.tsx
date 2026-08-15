'use client';

import { useState, useEffect } from 'react';
import { api } from '@/lib/api';
import { PortfolioCard } from '@/components/PortfolioCard';

interface Position {
  symbol: string;
  exchange: string;
  quantity: number;
  avg_price: number;
  ltp: number;
  unrealized_pnl: number;
  product: string;
}

interface PortfolioData {
  positions: Position[];
  total_capital: number;
  deployed_capital: number;
  available_margin: number;
  total_unrealized_pnl: number;
  timestamp: string;
}

export default function PortfolioPage() {
  const [portfolio, setPortfolio] = useState<PortfolioData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const res = await api.get('/api/portfolio');
        setPortfolio(res.data);
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
    const interval = setInterval(fetchData, 10000);
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return <div className="p-8 text-center">Loading...</div>;
  }

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-dark-900 dark:text-dark-50">Portfolio</h1>
          <p className="text-dark-500">Real-time positions and margin</p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <PortfolioCard title="Total Capital" value={portfolio?.total_capital ?? 0} prefix="₹" color="primary" />
        <PortfolioCard title="Deployed Capital" value={portfolio?.deployed_capital ?? 0} prefix="₹" color="blue" />
        <PortfolioCard title="Available Margin" value={portfolio?.available_margin ?? 0} prefix="₹" color="amber" />
        <PortfolioCard
          title="Unrealized P&L"
          value={portfolio?.total_unrealized_pnl ?? 0}
          prefix="₹"
          color={portfolio && portfolio.total_unrealized_pnl >= 0 ? 'primary' : 'danger'}
        />
      </div>

      <div className="bg-white dark:bg-dark-800 rounded-xl border border-dark-200 dark:border-dark-700 overflow-hidden">
        <div className="p-6 border-b border-dark-200 dark:border-dark-700">
          <h2 className="text-lg font-semibold text-dark-900 dark:text-dark-50">
            Open Positions ({portfolio?.positions.length ?? 0})
          </h2>
        </div>
        {portfolio?.positions.length === 0 ? (
          <div className="p-12 text-center text-dark-500">No open positions</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-dark-500 border-b border-dark-200 dark:border-dark-700">
                  <th className="p-4 font-medium">Symbol</th>
                  <th className="p-4 font-medium">Exchange</th>
                  <th className="p-4 font-medium">Quantity</th>
                  <th className="p-4 font-medium">Avg Price</th>
                  <th className="p-4 font-medium">LTP</th>
                  <th className="p-4 font-medium">Unrealized P&L</th>
                  <th className="p-4 font-medium">Product</th>
                </tr>
              </thead>
              <tbody>
                {portfolio?.positions.map((pos) => (
                  <tr key={`${pos.symbol}-${pos.exchange}`} className="border-b border-dark-100 dark:border-dark-800 hover:bg-dark-50 dark:hover:bg-dark-900/50">
                    <td className="p-4 font-medium text-dark-900 dark:text-dark-50">{pos.symbol}</td>
                    <td className="p-4 text-dark-600 dark:text-dark-400">{pos.exchange}</td>
                    <td className="p-4 tabular-nums">{pos.quantity}</td>
                    <td className="p-4 tabular-nums">₹{pos.avg_price.toFixed(2)}</td>
                    <td className="p-4 tabular-nums">₹{pos.ltp.toFixed(2)}</td>
                    <td className="p-4 font-medium tabular-nums">
                      <span className={pos.unrealized_pnl >= 0 ? 'text-primary-600' : 'text-danger-600'}>
                        ₹{pos.unrealized_pnl.toFixed(2)}
                      </span>
                    </td>
                    <td className="p-4">{pos.product}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}