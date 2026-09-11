'use client';

import React, { useState } from 'react';
import { 
  Sparkles, 
  ArrowUpRight, 
  ArrowDownRight, 
  ShieldCheck, 
  Target, 
  Zap, 
  Clock, 
  CheckCircle2, 
  AlertCircle 
} from 'lucide-react';
import { formatINR, formatPercent, formatISTTime } from '@/lib/utils';
import { api } from '@/lib/api';

export interface StrategySignal {
  id: string;
  symbol: string;
  exchange: string;
  direction: 'BUY' | 'SELL';
  entry_price: number;
  stoploss_price: number;
  target_price: number;
  quantity: number;
  capital_at_risk: number;
  risk_reward_ratio?: number | null;
  confidence_score: number;
  regime: string;
  top_strategy_name: string;
  ai_summary?: string | null;
  ai_key_risks?: string | null;
  ai_invalidation?: string | null;
  status: string;
  created_at: string;
}

interface StrategySignalsWidgetProps {
  recommendations: StrategySignal[];
  onRefresh?: () => void;
}

export function StrategySignalsWidget({
  recommendations = [],
  onRefresh,
}: StrategySignalsWidgetProps) {
  const [executingId, setExecutingId] = useState<string | null>(null);
  const [selectedSignal, setSelectedSignal] = useState<StrategySignal | null>(null);

  const handleExecute = async (rec: StrategySignal) => {
    if (!confirm(`Execute ${rec.direction} ${rec.symbol} x${rec.quantity} @ ₹${rec.entry_price}?`)) {
      return;
    }
    setExecutingId(rec.id);
    try {
      await api.post('/api/execute', { recommendation_id: rec.id, confirm: true });
      if (onRefresh) onRefresh();
    } catch (err: any) {
      alert('Execution failed: ' + (err.response?.data?.detail || err.message));
    } finally {
      setExecutingId(null);
    }
  };

  return (
    <div className="bg-slate-900/90 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
      {/* Card Header */}
      <div className="px-5 py-3.5 border-b border-slate-800 flex items-center justify-between bg-slate-950/40">
        <div className="flex items-center gap-2.5">
          <Sparkles className="w-4 h-4 text-emerald-400" />
          <h3 className="font-semibold text-sm text-white">Algorithmic Signals & AI Recommendations</h3>
          <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-purple-500/10 text-purple-300 border border-purple-500/20">
            AI RANKED
          </span>
        </div>
        <div className="text-xs text-slate-400">
          Ranker Engine: <strong className="text-slate-200">10 Strategies</strong>
        </div>
      </div>

      {/* Signals List */}
      <div className="p-4 space-y-3">
        {recommendations.length === 0 ? (
          <div className="text-center py-6 text-slate-500 text-xs">
            No pending strategy signals at this moment. Engine continuously evaluates multi-timeframe regimes.
          </div>
        ) : (
          recommendations.map((rec) => {
            const isBuy = rec.direction === 'BUY';
            const confidencePct = Math.round(rec.confidence_score * 100);

            return (
              <div 
                key={rec.id}
                className="bg-slate-950/60 border border-slate-800 hover:border-slate-700 rounded-lg p-4 transition-all space-y-3"
              >
                {/* Top Row: Symbol, Strategy, Confidence */}
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-2.5">
                    <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded font-bold text-xs font-mono ${
                      isBuy 
                        ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                        : 'bg-rose-500/10 text-rose-400 border border-rose-500/30'
                    }`}>
                      {isBuy ? <ArrowUpRight className="w-3.5 h-3.5" /> : <ArrowDownRight className="w-3.5 h-3.5" />}
                      {rec.direction}
                    </span>
                    <div>
                      <div className="font-bold text-sm text-white font-mono flex items-center gap-2">
                        {rec.symbol}
                        <span className="text-[10px] font-normal text-slate-400 px-1.5 py-0.2 rounded bg-slate-800 border border-slate-700">
                          {rec.top_strategy_name}
                        </span>
                      </div>
                      <div className="text-[11px] text-slate-400 flex items-center gap-2 mt-0.5">
                        <span>Regime: <strong className="text-slate-300 font-sans">{rec.regime}</strong></span>
                        <span>•</span>
                        <span>Time: {formatISTTime(rec.created_at)}</span>
                      </div>
                    </div>
                  </div>

                  {/* Confidence Gauge */}
                  <div className="text-right">
                    <div className="text-[11px] text-slate-400">Model Confidence</div>
                    <div className="text-sm font-bold font-mono text-emerald-400 flex items-center justify-end gap-1.5">
                      <div className="w-16 h-1.5 bg-slate-800 rounded-full overflow-hidden">
                        <div 
                          className="h-full bg-emerald-500 rounded-full"
                          style={{ width: `${confidencePct}%` }}
                        />
                      </div>
                      <span>{confidencePct}%</span>
                    </div>
                  </div>
                </div>

                {/* Pricing / Risk Matrix */}
                <div className="grid grid-cols-4 gap-2 bg-slate-900/60 p-2.5 rounded-lg border border-slate-800/80 text-xs">
                  <div>
                    <div className="text-[10px] text-slate-400">Entry</div>
                    <div className="font-mono font-bold text-slate-200">{formatINR(rec.entry_price)}</div>
                  </div>
                  <div>
                    <div className="text-[10px] text-slate-400">Stop-Loss</div>
                    <div className="font-mono font-bold text-rose-400">{formatINR(rec.stoploss_price)}</div>
                  </div>
                  <div>
                    <div className="text-[10px] text-slate-400">Target</div>
                    <div className="font-mono font-bold text-emerald-400">{formatINR(rec.target_price)}</div>
                  </div>
                  <div>
                    <div className="text-[10px] text-slate-400">Qty / R:R</div>
                    <div className="font-mono font-bold text-slate-300">
                      {rec.quantity}x <span className="text-[10px] text-slate-400 font-normal">({rec.risk_reward_ratio ? `1:${rec.risk_reward_ratio.toFixed(1)}` : '1:2'})</span>
                    </div>
                  </div>
                </div>

                {/* AI Summary Thesis */}
                {rec.ai_summary && (
                  <div className="text-xs text-slate-300 bg-emerald-950/20 border border-emerald-500/20 rounded p-2.5">
                    <div className="text-[10px] font-bold text-emerald-400 uppercase tracking-wider mb-1 flex items-center gap-1">
                      <Sparkles className="w-3 h-3" /> AI Trade Rationale
                    </div>
                    <p className="line-clamp-2 text-slate-300 leading-relaxed">{rec.ai_summary}</p>
                  </div>
                )}

                {/* Action Buttons */}
                <div className="flex items-center justify-between pt-1">
                  <div className="text-[11px] text-slate-400">
                    Max Capital Risk: <strong className="text-slate-300 font-mono">{formatINR(rec.capital_at_risk || 500)}</strong>
                  </div>
                  <button
                    onClick={() => handleExecute(rec)}
                    disabled={executingId === rec.id}
                    className="flex items-center gap-1.5 px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-xs font-bold transition-all shadow-sm shadow-emerald-900/30"
                  >
                    <Zap className="w-3.5 h-3.5" />
                    <span>{executingId === rec.id ? 'Placing Order...' : 'Execute Signal'}</span>
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
