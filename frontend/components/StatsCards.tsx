'use client';

import React from 'react';
import { 
  Wallet, 
  TrendingUp, 
  TrendingDown, 
  ShieldCheck, 
  PieChart, 
  Layers 
} from 'lucide-react';
import { formatINR, formatPercent } from '@/lib/utils';
import { useTradingStore } from '@/lib/store';

interface StatsCardsProps {
  totalCapital?: number;
  deployedCapital?: number;
  availableMargin?: number;
  totalUnrealizedPnl?: number;
  realizedPnl?: number;
  tradesToday?: number;
  maxTrades?: number;
  positionsCount?: number;
}

export function StatsCards({
  totalCapital: initCapital = 100000,
  deployedCapital: initDeployed = 0,
  availableMargin: initMargin = 100000,
  totalUnrealizedPnl: initUnrealized = 0,
  realizedPnl = 0,
  tradesToday = 0,
  maxTrades = 3,
  positionsCount: initCount = 0,
}: StatsCardsProps) {
  const livePortfolio = useTradingStore((s) => s.portfolio);

  const totalCapital = livePortfolio?.total_capital ?? initCapital;
  const deployedCapital = livePortfolio?.deployed_capital ?? initDeployed;
  const availableMargin = livePortfolio?.available_margin ?? initMargin;
  const totalUnrealizedPnl = livePortfolio?.total_unrealized_pnl ?? initUnrealized;
  const positionsCount = livePortfolio?.positions ? livePortfolio.positions.length : initCount;

  const totalPnl = (realizedPnl || 0) + (totalUnrealizedPnl || 0);
  const pnlPercent = totalCapital > 0 ? (totalPnl / totalCapital) * 100 : 0;
  const isPnlPositive = totalPnl >= 0;

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {/* Total Capital */}
      <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-4 shadow-sm relative overflow-hidden">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-slate-400">Total Capital</span>
          <div className="p-2 rounded-lg bg-blue-500/10 text-blue-400">
            <Wallet className="w-4 h-4" />
          </div>
        </div>
        <div className="mt-2 flex items-baseline gap-2">
          <span className="text-2xl font-bold font-mono text-white tracking-tight">
            {formatINR(totalCapital)}
          </span>
        </div>
        <div className="mt-2 text-[11px] text-slate-400 flex items-center justify-between border-t border-slate-800/80 pt-2">
          <span>Margin: <strong className="text-slate-300 font-mono">{formatINR(availableMargin)}</strong></span>
          <span>Deployed: <strong className="text-slate-300 font-mono">{formatINR(deployedCapital)}</strong></span>
        </div>
      </div>

      {/* Net Today's P&L */}
      <div className={`border rounded-xl p-4 shadow-sm relative overflow-hidden ${
        isPnlPositive 
          ? 'bg-emerald-950/20 border-emerald-500/30' 
          : 'bg-rose-950/20 border-rose-500/30'
      }`}>
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-slate-400">Total Day P&L</span>
          <div className={`p-2 rounded-lg ${isPnlPositive ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400'}`}>
            {isPnlPositive ? <TrendingUp className="w-4 h-4" /> : <TrendingDown className="w-4 h-4" />}
          </div>
        </div>
        <div className="mt-2 flex items-baseline gap-2">
          <span className={`text-2xl font-bold font-mono tracking-tight ${isPnlPositive ? 'text-emerald-400' : 'text-rose-400'}`}>
            {isPnlPositive ? '+' : ''}{formatINR(totalPnl)}
          </span>
          <span className={`text-xs font-semibold font-mono ${isPnlPositive ? 'text-emerald-400' : 'text-rose-400'}`}>
            ({formatPercent(pnlPercent)})
          </span>
        </div>
        <div className="mt-2 text-[11px] text-slate-400 flex items-center justify-between border-t border-slate-800/80 pt-2">
          <span>Realized: <strong className="text-slate-300 font-mono">{formatINR(realizedPnl)}</strong></span>
          <span>Unrealized: <strong className="text-slate-300 font-mono">{formatINR(totalUnrealizedPnl)}</strong></span>
        </div>
      </div>

      {/* Open Positions */}
      <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-4 shadow-sm relative overflow-hidden">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-slate-400">Open Positions</span>
          <div className="p-2 rounded-lg bg-indigo-500/10 text-indigo-400">
            <Layers className="w-4 h-4" />
          </div>
        </div>
        <div className="mt-2 flex items-baseline gap-2">
          <span className="text-2xl font-bold font-mono text-white tracking-tight">
            {positionsCount}
          </span>
          <span className="text-xs text-slate-400 font-medium">active legs</span>
        </div>
        <div className="mt-2 text-[11px] text-slate-400 flex items-center justify-between border-t border-slate-800/80 pt-2">
          <span>Product: <strong className="text-slate-300">MIS (Intraday)</strong></span>
          <span className="text-emerald-400 font-medium">Auto-Square 15:15</span>
        </div>
      </div>

      {/* Daily Trade Quota */}
      <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-4 shadow-sm relative overflow-hidden">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-slate-400">SEBI Daily Quota</span>
          <div className="p-2 rounded-lg bg-purple-500/10 text-purple-400">
            <ShieldCheck className="w-4 h-4" />
          </div>
        </div>
        <div className="mt-2 flex items-baseline gap-2">
          <span className="text-2xl font-bold font-mono text-white tracking-tight">
            {tradesToday} <span className="text-slate-400 font-normal text-sm">/ {maxTrades}</span>
          </span>
          <span className="text-xs text-slate-400 font-medium">trades</span>
        </div>
        <div className="mt-2 text-[11px] text-slate-400 flex items-center justify-between border-t border-slate-800/80 pt-2">
          <span>Remaining: <strong className="text-slate-300 font-mono">{Math.max(0, maxTrades - tradesToday)}</strong></span>
          <span className="text-emerald-400 font-medium">Cooldown: 20m</span>
        </div>
      </div>
    </div>
  );
}
