interface RegimeIndicatorProps {
  regimes: Array<{
    symbol: string;
    regime: string;
    confidence: number;
  }>;
}

const regimeColors: Record<string, { bg: string; text: string; dot: string }> = {
  'trending-up': { bg: 'bg-primary-50', text: 'text-primary-700', dot: 'bg-primary-500' },
  'trending-down': { bg: 'bg-danger-50', text: 'text-danger-700', dot: 'bg-danger-500' },
  'range-bound': { bg: 'bg-blue-50', text: 'text-blue-700', dot: 'bg-blue-500' },
  'high-volatility': { bg: 'bg-amber-50', text: 'text-amber-700', dot: 'bg-amber-500' },
};

const regimeLabels: Record<string, string> = {
  'trending-up': '📈 Trending Up',
  'trending-down': '📉 Trending Down',
  'range-bound': '📊 Range Bound',
  'high-volatility': '⚡ High Volatility',
};

export function RegimeIndicator({ regimes }: RegimeIndicatorProps) {
  if (!regimes || regimes.length === 0) {
    return (
      <div className="flex items-center gap-2 px-3 py-1.5 bg-dark-100 dark:bg-dark-900 rounded-lg text-sm text-dark-500">
        <span className="w-2 h-2 rounded-full bg-dark-400"></span>
        No regime data
      </div>
    );
  }

  // Show dominant regime
  const dominant = regimes.reduce((a, b) => a.confidence > b.confidence ? a : b);
  const colors = regimeColors[dominant.regime] || regimeColors['range-bound'];

  return (
    <div className="flex items-center gap-3">
      <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg ${colors.bg} dark:bg-dark-900`}>
        <span className={`w-2 h-2 rounded-full ${colors.dot} animate-pulse`}></span>
        <span className={`text-sm font-medium ${colors.text} dark:text-dark-300`}>
          {regimeLabels[dominant.regime] || dominant.regime}
        </span>
      </div>
      {/* Compact view for other symbols */}
      <div className="hidden md:flex items-center gap-1.5">
        {regimes.filter(r => r.symbol !== dominant.symbol).slice(0, 3).map((r) => {
          const c = regimeColors[r.regime] || regimeColors['range-bound'];
          return (
            <span
              key={r.symbol}
              className="px-2 py-1 text-xs rounded bg-dark-100 dark:bg-dark-900"
              title={`${r.symbol}: ${regimeLabels[r.regime] || r.regime} (${(r.confidence * 100).toFixed(0)}%)`}
            >
              {r.symbol}: <span className={c.text} font-medium>{r.regime.split('-')[0]}</span>
            </span>
          );
        })}
      </div>
    </div>
  );
}