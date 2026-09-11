import { create } from 'zustand';

export interface LiveTick {
  symbol: string;
  exchange: string;
  ltp: number;
  prev_close?: number;
  open?: number;
  high?: number;
  low?: number;
  change: number;
  change_pct: number;
  volume?: number;
  timestamp: string;
  isUptick?: boolean;
}

export interface LivePosition {
  id?: string;
  symbol: string;
  exchange: string;
  direction?: 'BUY' | 'SELL';
  quantity: number;
  avg_price: number;
  ltp: number;
  stoploss_price?: number | null;
  target_price?: number | null;
  trailing_sl_price?: number | null;
  unrealized_pnl: number;
  product?: string;
}

export interface LivePortfolio {
  total_capital: number;
  deployed_capital: number;
  available_margin: number;
  total_unrealized_pnl: number;
  positions: LivePosition[];
  timestamp: string;
}

interface TradingState {
  ticks: Record<string, LiveTick>;
  portfolio: LivePortfolio | null;
  wsConnected: boolean;
  wsLatency: number;
  lastUpdated: string | null;

  // Actions
  updateTick: (tick: LiveTick) => void;
  updatePortfolio: (portfolio: LivePortfolio) => void;
  setWsConnected: (connected: boolean) => void;
  setWsLatency: (latency: number) => void;
}

export const useTradingStore = create<TradingState>((set) => ({
  ticks: {},
  portfolio: null,
  wsConnected: false,
  wsLatency: 45,
  lastUpdated: null,

  updateTick: (tick) =>
    set((state) => {
      const prev = state.ticks[tick.symbol];
      const isUptick = prev ? tick.ltp >= prev.ltp : true;
      return {
        ticks: {
          ...state.ticks,
          [tick.symbol]: {
            ...tick,
            isUptick,
          },
        },
        lastUpdated: tick.timestamp || new Date().toISOString(),
      };
    }),

  updatePortfolio: (portfolio) =>
    set(() => ({
      portfolio,
      lastUpdated: portfolio.timestamp || new Date().toISOString(),
    })),

  setWsConnected: (connected) =>
    set(() => ({
      wsConnected: connected,
    })),

  setWsLatency: (latency) =>
    set(() => ({
      wsLatency: latency,
    })),
}));
