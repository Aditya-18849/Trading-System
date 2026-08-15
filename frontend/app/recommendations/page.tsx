'use client';

import { useState, useEffect } from 'react';
import { api } from '@/lib/api';
import { RecommendationCard } from '@/components/RecommendationCard';

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

const regimeColors: Record<string, string> = {
  'trending-up': 'bg-primary-100 text-primary-700 dark:bg-primary-900/30 dark:text-primary-400',
  'trending-down': 'bg-danger-100 text-danger-700 dark:bg-danger-900/30 dark:text-danger-400',
  'range-bound': 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  'high-volatility': 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
};

export default function RecommendationsPage() {
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<'PENDING' | 'EXECUTED' | 'REJECTED' | 'EXPIRED' | 'ALL'>('PENDING');

  useEffect(() => {
    const fetchData = async () => {
      try {
        const status = statusFilter === 'ALL' ? '' : statusFilter;
        const res = await api.get(`/api/recommendations?status=${status}&limit=50`);
        setRecommendations(res.data);
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, [statusFilter]);

  const handleExecute = async (rec: Recommendation) => {
    if (!confirm(`Execute ${rec.direction} ${rec.symbol} x${rec.quantity} @ ₹${rec.entry_price}?`)) return;
    try {
      await api.post('/api/execute', { recommendation_id: rec.id, confirm: true });
      setRecommendations(prev => prev.filter(r => r.id !== rec.id));
    } catch (err) {
      alert('Execution failed');
    }
  };

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-dark-900 dark:text-dark-50">Recommendations</h1>
          <p className="text-dark-500">AI-ranked trade signals with rationale</p>
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as any)}
          className="px-3 py-2 border border-dark-300 dark:border-dark-600 rounded-lg bg-white dark:bg-dark-800 text-dark-900 dark:text-dark-50"
        >
          <option value="PENDING">Pending</option>
          <option value="EXECUTED">Executed</option>
          <option value="REJECTED">Rejected</option>
          <option value="EXPIRED">Expired</option>
          <option value="ALL">All</option>
        </select>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="animate-spin rounded-full h-8 w-8 border-4 border-primary-500 border-t-transparent"></div>
        </div>
      ) : recommendations.length === 0 ? (
        <div className="text-center py-12 text-dark-500">
          <p className="text-lg">No recommendations found</p>
          <p className="mt-2">Strategy engine runs at market open (09:15 IST)</p>
        </div>
      ) : (
        <div className="space-y-4">
          {recommendations.map((rec) => (
            <RecommendationCard
              key={rec.id}
              recommendation={rec}
              onExecute={() => handleExecute(rec)}
            />
          ))}
        </div>
      )}
    </div>
  );
}