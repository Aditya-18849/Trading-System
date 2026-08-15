'use client';

import {
  LineChart,
  Line,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';

interface EquityCurveChartProps {
  data: Array<{
    date: string;
    equity: number;
    pnl: number;
    trades_count: number;
    win_rate: number;
    max_drawdown_pct: number;
  }>;
  height?: number;
}

export function EquityCurveChart({ data, height = 300 }: EquityCurveChartProps) {
  if (!data || data.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-dark-500">
        <p>No equity curve data available</p>
      </div>
    );
  }

  const chartData = data.map((d) => ({
    date: new Date(d.date).toLocaleDateString('en-IN', { month: 'short', day: 'numeric' }),
    equity: d.equity,
    pnl: d.pnl,
  }));

  const minEquity = Math.min(...chartData.map(d => d.equity));
  const maxEquity = Math.max(...chartData.map(d => d.equity));
  const padding = (maxEquity - minEquity) * 0.1 || 1000;

  return (
    <div className="w-full" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartData} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 11, fill: '#64748b' }}
            axisLine={{ stroke: '#e2e8f0' }}
            tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fontSize: 11, fill: '#64748b' }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(value) => `₹${(value / 100000).toFixed(1)}L`}
            domain={[minEquity - padding, maxEquity + padding]}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: '#1e293b',
              border: 'none',
              borderRadius: '8px',
              color: '#f8fafc',
              boxShadow: '0 10px 15px -3px rgba(0, 0, 0, 0.1)',
            }}
            formatter={(value: number) => [`₹${value.toLocaleString('en-IN')}`, 'Equity']}
            labelFormatter={(date) => `Date: ${date}`}
          />
          <Legend />
          <Area
            type="monotone"
            dataKey="equity"
            stroke="#22c55e"
            strokeWidth={2}
            fillOpacity={0.1}
            fill="#22c55e"
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="equity"
            stroke="#22c55e"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}