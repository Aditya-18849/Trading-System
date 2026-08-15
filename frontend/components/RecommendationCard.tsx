'use client';

import { useState } from 'react';

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

interface RecommendationCardProps {
  recommendation: Recommendation;
  onExecute: () => void;
  autoExecute?: boolean;
}

const regimeColors: Record<string, string> = {
  'trending-up': 'bg-primary-100 text-primary-700 dark:bg-primary-900/30 dark:text-primary-400',
  'trending-down': 'bg-danger-100 text-danger-700 dark:bg-danger-900/30 dark:text-danger-400',
  'range-bound': 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  'high-volatility': 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400',
};

export function RecommendationCard({
  recommendation,
  onExecute,
  autoExecute = false,
}: RecommendationCardProps) {
  const [expanded, setExpanded] = useState(false);
  const regimeColor = regimeColors[recommendation.regime] || regimeColors['range-bound'];

  const formatTime = (iso: string) => {
    return new Date(iso).toLocaleTimeString('en-IN', {
      hour: '2-digit',
      minute: '2-digit',
      timeZone: 'Asia/Kolkata',
    });
  };

  const getConfidenceColor = (score: number) => {
    if (score >= 0.8) return 'text-primary-600';
    if (score >= 0.6) return 'text-amber-600';
    return 'text-danger-600';
  };

  return (
    <div className="bg-white dark:bg-dark-800 rounded-lg border border-dark-200 dark:border-dark-700 overflow-hidden">
      {/* Main Card */}
      <div className="p-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className={`font-mono font-bold text-lg ${recommendation.direction === 'BUY' ? 'text-primary-600' : 'text-danger-600'}`}>
                {recommendation.direction}
              </span>
              <span className="font-mono font-semibold text-dark-900 dark:text-dark-50">
                {recommendation.symbol}
              </span>
              <span className={`px-2 py-0.5 text-xs font-medium rounded ${regimeColor}`}>
                {recommendation.regime.replace('-', ' ')}
              </span>
              <span className="px-2 py-0.5 text-xs font-medium bg-dark-100 dark:bg-dark-900 text-dark-600 dark:text-dark-400 rounded">
                {recommendation.top_strategy_name}
              </span>
            </div>
            <div className="mt-2 flex items-center gap-4 text-sm text-dark-500 flex-wrap">
              <span>Entry: <span className="font-mono tabular-nums text-dark-900 dark:text-dark-50">₹{recommendation.entry_price.toFixed(2)}</span></span>
              <span>SL: <span className="font-mono tabular-nums text-danger-600">₹{recommendation.stoploss_price.toFixed(2)}</span></span>
              <span>Target: <span className="font-mono tabular-nums text-primary-600">₹{recommendation.target_price.toFixed(2)}</span></span>
              <span>Qty: <span className="font-mono tabular-nums">{recommendation.quantity}</span></span>
              <span>Risk: <span className="font-mono tabular-nums">₹{recommendation.capital_at_risk.toFixed(0)}</span></span>
              {recommendation.risk_reward_ratio && (
                <span>R:R: <span className="font-mono tabular-nums">{recommendation.risk_reward_ratio.toFixed(1)}</span></span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <div className="text-right">
              <p className={`font-bold tabular-nums ${getConfidenceColor(recommendation.confidence_score)}`}>
                {(recommendation.confidence_score * 100).toFixed(0)}%
              </p>
              <p className="text-xs text-dark-500">Confidence</p>
            </div>
            <button
              onClick={onExecute}
              disabled={autoExecute}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                autoExecute
                  ? 'bg-primary-500 text-white cursor-not-allowed opacity-75'
                  : 'bg-primary-50 text-primary-700 hover:bg-primary-100 dark:bg-primary-900/30 dark:text-primary-400'
              }`}
            >
              {autoExecute ? 'Auto' : 'Execute'}
            </button>
          </div>
        </div>

        {/* Expandable AI Summary */}
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full px-4 py-2 text-left text-sm text-dark-500 hover:text-dark-700 dark:hover:text-dark-300 flex items-center justify-between border-t border-dark-100 dark:border-dark-800 transition-colors"
        >
          <span>AI Analysis</span>
          <span className={`transition-transform ${expanded ? 'rotate-180' : ''}`}>▼</span>
        </button>

        {expanded && (
          <div className="px-4 pb-4 border-t border-dark-100 dark:border-dark-800 bg-dark-50 dark:bg-dark-900/50 animate-in slide-in-from-top-2 duration-200">
            {recommendation.ai_summary && (
              <div className="mt-3 space-y-3">
                <div>
                  <p className="text-xs font-medium text-dark-500 uppercase tracking-wider mb-1">Rationale</p>
                  <p className="text-sm text-dark-700 dark:text-dark-300">{recommendation.ai_summary}</p>
                </div>
                {recommendation.ai_key_risks && (
                  <div className="p-3 bg-danger-50 dark:bg-danger-900/20 rounded-lg border border-danger-100 dark:border-danger-800">
                    <p className="text-xs font-medium text-danger-700 dark:text-danger-400 uppercase tracking-wider mb-1">Key Risks</p>
                    <p className="text-sm text-danger-600 dark:text-danger-500">{recommendation.ai_key_risks}</p>
                  </div>
                )}
                {recommendation.ai_invalidation && (
                  <div className="p-3 bg-amber-50 dark:bg-amber-900/20 rounded-lg border border-amber-100 dark:border-amber-800">
                    <p className="text-xs font-medium text-amber-700 dark:text-amber-400 uppercase tracking-wider mb-1">Invalidation</p>
                    <p className="text-sm text-amber-600 dark:text-amber-500">{recommendation.ai_invalidation}</p>
                  </div>
                )}
              </div>
            )}
            {!recommendation.ai_summary && (
              <p className="mt-3 text-sm text-dark-500 italic">AI summary not available</p>
            )}
            <div className="mt-3 pt-3 border-t border-dark-200 dark:border-dark-700 flex items-center justify-between text-xs text-dark-500">
              <span>Created: {formatTime(recommendation.created_at)}</span>
              {recommendation.expires_at && (
                <span>Expires: {formatTime(recommendation.expires_at)}</span>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}