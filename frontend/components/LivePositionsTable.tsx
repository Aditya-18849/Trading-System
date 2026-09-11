'use client';

import React, { useState } from 'react';
import { 
  ArrowUpRight, 
  ArrowDownRight, 
  Target, 
  ShieldAlert, 
  XCircle, 
  Layers,
  Sparkles,
  TrendingUp
} from 'lucide-react';
import { formatINR, formatPercent } from '@/lib/utils';
import { api } from '@/lib/api';
import { useTradingStore } from '@/lib/store';

export interface Position {
  id?: string;
  symbol: string;
  exchange: string;
  quantity: number;
  avg_price: number;
  ltp: number;
  unrealized_pnl: number;
  product?: string;
  direction?: 'BUY' | 'SELL';
  stoploss_price?: number | null;
  target_price?: number | null;
  trailing_sl_price?: number | null;
}

interface LivePositionsTableProps {
  positions?: Position[];
  onRefresh?: () => void;
}

export function LivePositionsTable({ positions: initialPositions = [], onRefresh }: LivePositionsTableProps) {
  const [exitingSymbol, setExitingSymbol] = useState<string | null>(null);

  // Subscribe to live Zustand store ticks & MTM portfolio
  const liveTicks = useTradingStore((s) => s.ticks);
  const livePortfolio = useTradingStore((s) => s.portfolio);

  // Merge server positions with live MTM portfolio if available
  const positions = livePortfolio?.positions && livePortfolio.positions.length > 0
    ? livePortfolio.positions
    : initialPositions;

  const handleSquareOff = async (pos: any) => {
    if (!confirm(`Confirm manual square-off for ${pos.symbol} (${pos.quantity} Qty @ LTP ₹${pos.ltp})?`)) {
      return;
    }
    setExitingSymbol(pos.symbol);
    try {
      await api.post('/api/execute', {
        symbol: pos.symbol,
        exchange: pos.exchange || 'NSE',
        direction: pos.quantity > 0 ? 'SELL' : 'BUY',
        quantity: Math.abs(pos.quantity),
        is_squareoff: true,
      });
      if (onRefresh) onRefresh();
    } catch (err: any) {
      alert('Square-off failed: ' + (err.response?.data?.detail || err.message));
    } finally {
      setExitingSymbol(null);
    }
  };

  if (!positions || positions.length === 0) {
    return (
      <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-8 text-center space-y-3">
        <div className="w-12 h-12 rounded-full bg-slate-800 flex items-center justify-center mx-auto text-slate-500">
          <Layers className="w-6 h-6" />
        </div>
        <div>
          <h4 className="text-sm font-semibold text-slate-200">No Active Positions</h4>
          <p className="text-xs text-slate-400 mt-1 max-w-sm mx-auto">
            The automated risk engine is continuously streaming live ticks and monitoring multi-timeframe regimes.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-slate-900/90 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
      <div className="px-5 py-3.5 border-b border-slate-800 flex items-center justify-between bg-slate-950/40">
        <div className="flex items-center gap-2.5">
          <Layers className="w-4 h-4 text-emerald-400" />
          <h3 className="font-semibold text-sm text-white">Live Open Positions</h3>
          <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/10 text-emerald-300 border border-emerald-500/20">
            {positions.length} ACTIVE
          </span>
        </div>
        <div className="text-xs text-slate-400 font-mono flex items-center gap-2">
          <span>Auto-trailing SL: <strong className="text-emerald-400">ACTIVE (1.5%)</strong></span>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="border-b border-slate-800 bg-slate-950/20 text-[11px] font-semibold text-slate-400 uppercase tracking-wider">
              <th className="py-3 px-4">Instrument</th>
              <th className="py-3 px-4">Side / Qty</th>
              <th className="py-3 px-4">Avg Entry</th>
              <th className="py-3 px-4">Live LTP</th>
              <th className="py-3 px-4">Trailing SL</th>
              <th className="py-3 px-4">Unrealized P&L</th>
              <th className="py-3 px-4 text-right">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60 text-xs">
            {positions.map((pos, idx) => {
              const isLong = pos.quantity > 0 || pos.direction === 'BUY';
              const tick = liveTicks[pos.symbol];
              const ltp = tick ? tick.ltp : pos.ltp;
              const isUptick = tick?.isUptick ?? true;

              // Dynamic MTM PnL
              const pnl = isLong 
                ? (ltp - pos.avg_price) * Math.abs(pos.quantity)
                : (pos.avg_price - ltp) * Math.abs(pos.quantity);

              const pnlPercent = pos.avg_price > 0 
                ? (pnl / (pos.avg_price * Math.abs(pos.quantity))) * 100 
                : 0;

              const isProfit = pnl >= 0;

              return (
                <tr key={`${pos.symbol}-${idx}`} className="hover:bg-slate-800/40 transition-colors">
                  {/* Instrument */}
                  <td className="py-3 px-4">
                    <div className="font-bold text-white font-mono flex items-center gap-1.5">
                      {pos.symbol}
                      <span className="text-[10px] font-normal text-slate-400 px-1.5 py-0.5 rounded bg-slate-800 border border-slate-700">
                        {pos.exchange || 'NSE'}
                      </span>
                    </div>
                    <div className="text-[11px] text-slate-400 mt-0.5">{pos.product || 'MIS'} Intraday</div>
                  </td>

                  {/* Side & Quantity */}
                  <td className="py-3 px-4">
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded font-bold text-[11px] font-mono ${
                      isLong 
                        ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                        : 'bg-rose-500/10 text-rose-400 border border-rose-500/30'
                    }`}>
                      {isLong ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
                      {isLong ? 'BUY' : 'SELL'} {Math.abs(pos.quantity)}
                    </span>
                  </td>

                  {/* Avg Entry Price */}
                  <td className="py-3 px-4 font-mono font-medium text-slate-300">
                    {formatINR(pos.avg_price)}
                  </td>

                  {/* Live LTP with Dynamic Price Flashing */}
                  <td className="py-3 px-4 font-mono font-bold">
                    <span className={`px-2 py-1 rounded transition-colors duration-300 ${
                      isUptick ? 'text-emerald-400 bg-emerald-500/10' : 'text-rose-400 bg-rose-500/10'
                    }`}>
                      {formatINR(ltp)}
                    </span>
                  </td>

                  {/* Trailing SL */}
                  <td className="py-3 px-4 font-mono text-xs">
                    {pos.trailing_sl_price ? (
                      <span className="text-amber-400 flex items-center gap-1">
                        <Target className="w-3 h-3" />
                        {formatINR(pos.trailing_sl_price)}
                      </span>
                    ) : (
                      <span className="text-slate-400">Fixed SL {formatINR(pos.stoploss_price || (pos.avg_price * 0.995))}</span>
                    )}
                  </td>

                  {/* Unrealized PnL */}
                  <td className="py-3 px-4 font-mono font-bold">
                    <div className={isProfit ? 'text-emerald-400' : 'text-rose-400'}>
                      {isProfit ? '+' : ''}{formatINR(pnl)}
                    </div>
                    <div className={`text-[10px] font-medium ${isProfit ? 'text-emerald-400' : 'text-rose-400'}`}>
                      {formatPercent(pnlPercent)}
                    </div>
                  </td>

                  {/* Action */}
                  <td className="py-3 px-4 text-right">
                    <button
                      onClick={() => handleSquareOff(pos)}
                      disabled={exitingSymbol === pos.symbol}
                      className="px-2.5 py-1 bg-rose-600/15 hover:bg-rose-600/30 text-rose-300 border border-rose-500/30 hover:border-rose-500/60 rounded text-[11px] font-semibold transition-all"
                    >
                      {exitingSymbol === pos.symbol ? 'Exiting...' : 'Exit'}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
