'use client';

import React from 'react';
import { 
  Compass, 
  TrendingUp, 
  TrendingDown, 
  Activity, 
  Zap, 
  Sliders,
  CheckCircle2
} from 'lucide-react';
import { formatINR, formatPercent } from '@/lib/utils';
import { useTradingStore } from '@/lib/store';

export interface RegimeItem {
  symbol: string;
  exchange: string;
  regime: string;
  adx: number | null;
  atr: number | null;
  bb_bandwidth: number | null;
  ema_slope: number | null;
  confidence: number;
}

interface RegimeRadarWidgetProps {
  regimes: RegimeItem[];
}

export function RegimeRadarWidget({ regimes = [] }: RegimeRadarWidgetProps) {
  const liveTicks = useTradingStore((s) => s.ticks);

  return (
    <div className="bg-slate-900/90 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
      {/* Header */}
      <div className="px-5 py-3.5 border-b border-slate-800 flex items-center justify-between bg-slate-950/40">
        <div className="flex items-center gap-2.5">
          <Compass className="w-4 h-4 text-emerald-400" />
          <h3 className="font-semibold text-sm text-white">Market Regime Radar</h3>
          <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-blue-500/10 text-blue-300 border border-blue-500/20">
            MULTI-INDICATOR
          </span>
        </div>
        <div className="text-xs text-slate-400">
          Threshold: <strong className="text-slate-300 font-mono">ADX &gt; 25</strong>
        </div>
      </div>

      {/* Regimes Grid */}
      <div className="p-4 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {regimes.length === 0 ? (
          <div className="col-span-full text-center py-6 text-slate-500 text-xs font-mono">
            Scanning watched symbols for regime transitions...
          </div>
        ) : (
          regimes.map((item) => {
            const isTrendingUp = item.regime === 'trending-up';
            const isTrendingDown = item.regime === 'trending-down';
            const isHighVol = item.regime === 'high-volatility';
            const isRange = item.regime === 'range-bound';

            const tick = liveTicks[item.symbol];
            const ltp = tick ? tick.ltp : null;
            const changePct = tick ? tick.change_pct : null;
            const isUptick = tick?.isUptick ?? true;

            return (
              <div 
                key={item.symbol}
                className="bg-slate-950/50 border border-slate-800 rounded-lg p-3.5 space-y-2.5 hover:border-slate-700 transition-all"
              >
                {/* Header: Symbol, Live Price & Regime Badge */}
                <div className="flex items-start justify-between">
                  <div>
                    <div className="font-bold text-white font-mono text-sm flex items-center gap-1.5">
                      {item.symbol}
                      {ltp !== null && (
                        <span className={`text-xs font-mono px-1.5 py-0.2 rounded transition-colors ${
                          isUptick ? 'text-emerald-400 bg-emerald-500/10' : 'text-rose-400 bg-rose-500/10'
                        }`}>
                          {formatINR(ltp)}
                        </span>
                      )}
                    </div>
                    {changePct !== null && (
                      <div className={`text-[10px] font-mono mt-0.5 ${changePct >= 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                        {formatPercent(changePct)} today
                      </div>
                    )}
                  </div>

                  <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase ${
                    isTrendingUp
                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                      : isTrendingDown
                      ? 'bg-rose-500/10 text-rose-400 border border-rose-500/30'
                      : isHighVol
                      ? 'bg-purple-500/10 text-purple-400 border border-purple-500/30'
                      : 'bg-amber-500/10 text-amber-400 border border-amber-500/30'
                  }`}>
                    {isTrendingUp && <TrendingUp className="w-3 h-3" />}
                    {isTrendingDown && <TrendingDown className="w-3 h-3" />}
                    {isHighVol && <Zap className="w-3 h-3" />}
                    {isRange && <Activity className="w-3 h-3" />}
                    {item.regime}
                  </span>
                </div>

                {/* Metrics Breakdown */}
                <div className="grid grid-cols-2 gap-2 text-xs bg-slate-900/60 p-2 rounded border border-slate-800/60">
                  <div>
                    <div className="text-[10px] text-slate-400">ADX Trend</div>
                    <div className="font-mono font-semibold text-slate-200">
                      {item.adx !== null ? item.adx.toFixed(1) : '--'}
                      <span className="text-[10px] text-slate-500 ml-1">
                        {item.adx && item.adx >= 25 ? '(Strong)' : '(Weak)'}
                      </span>
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] text-slate-400">ATR Volatility</div>
                    <div className="font-mono font-semibold text-slate-200">
                      {item.atr !== null ? `₹${item.atr.toFixed(2)}` : '--'}
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] text-slate-400">BB Width</div>
                    <div className="font-mono font-semibold text-slate-200">
                      {item.bb_bandwidth !== null ? `${(item.bb_bandwidth * 100).toFixed(2)}%` : '--'}
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] text-slate-400">EMA Slope</div>
                    <div className={`font-mono font-semibold ${
                      item.ema_slope && item.ema_slope > 0 ? 'text-emerald-400' : 'text-rose-400'
                    }`}>
                      {item.ema_slope !== null ? (item.ema_slope > 0 ? `+${(item.ema_slope * 1000).toFixed(2)}` : `${(item.ema_slope * 1000).toFixed(2)}`) : '--'}
                    </div>
                  </div>
                </div>

                {/* Confidence Bar */}
                <div className="flex items-center justify-between text-[11px] text-slate-400">
                  <span>Classification Confidence</span>
                  <span className="font-mono font-bold text-slate-300">
                    {Math.round(item.confidence * 100)}%
                  </span>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
