'use client';

import React, { useState, useEffect } from 'react';
import { 
  Sparkles, 
  Filter, 
  RefreshCw, 
  Sliders, 
  CheckCircle2, 
  XCircle, 
  Clock 
} from 'lucide-react';
import { StrategySignalsWidget, StrategySignal } from '@/components/StrategySignalsWidget';
import { api } from '@/lib/api';

export default function RecommendationsPage() {
  const [recommendations, setRecommendations] = useState<StrategySignal[]>([]);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [statusFilter, setStatusFilter] = useState<'PENDING' | 'EXECUTED' | 'REJECTED' | 'ALL'>('PENDING');

  const fetchData = async () => {
    try {
      setIsRefreshing(true);
      const status = statusFilter === 'ALL' ? '' : statusFilter;
      const res = await api.get(`/api/recommendations?status=${status}&limit=50`).catch(() => ({ data: [] }));
      setRecommendations(res.data || []);
    } catch (err) {
      console.debug('Recommendations fetch:', err);
    } finally {
      setIsRefreshing(false);
    }
  };

  useEffect(() => {
    fetchData();
    window.addEventListener('manual_refresh', fetchData);
    return () => window.removeEventListener('manual_refresh', fetchData);
  }, [statusFilter]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-emerald-400" />
            Strategy Engine Signals &amp; AI Thesis
          </h1>
          <p className="text-xs text-slate-400 mt-0.5">
            Multi-strategy algorithmic scanner with regime alignment scoring and automated risk checks
          </p>
        </div>

        {/* Filter & Refresh */}
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1.5 bg-slate-900 border border-slate-800 p-1 rounded-lg">
            {(['PENDING', 'EXECUTED', 'REJECTED', 'ALL'] as const).map((st) => (
              <button
                key={st}
                onClick={() => setStatusFilter(st)}
                className={`px-3 py-1 rounded text-xs font-semibold transition-all ${
                  statusFilter === st
                    ? 'bg-emerald-500 text-slate-950 shadow-sm'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {st}
              </button>
            ))}
          </div>

          <button
            onClick={fetchData}
            className="p-2 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 rounded-lg text-xs font-semibold transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? 'animate-spin text-emerald-400' : ''}`} />
          </button>
        </div>
      </div>

      {/* Main Signals List */}
      <StrategySignalsWidget recommendations={recommendations} onRefresh={fetchData} />
    </div>
  );
}