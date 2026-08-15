'use client';

import { useState, useEffect } from 'react';
import { api } from '@/lib/api';
import { format } from 'date-fns';

interface DailyReport {
  report_date: string;
  total_pnl: number;
  trades_count: number;
  wins: number;
  losses: number;
  max_drawdown: number;
  best_trade_pnl: number;
  worst_trade_pnl: number;
  best_strategy: string | null;
  worst_strategy: string | null;
  regime_summary: Record<string, any>;
  strategy_performance: Array<{ strategy: string; total_pnl: number; trades: number; wins: number; losses: number; win_rate: number }>;
  ai_summary: string | null;
  created_at: string;
}

interface StatBoxProps {
  label: string;
  value: number | string;
  prefix?: string;
  suffix?: string;
  color?: 'primary' | 'danger' | 'blue' | 'amber' | 'purple';
}

function StatBox({ label, value, prefix = '', suffix = '', color = 'primary' }: StatBoxProps) {
  const colorClasses: Record<string, string> = {
    primary: 'text-primary-600 dark:text-primary-400',
    danger: 'text-danger-600 dark:text-danger-400',
    blue: 'text-blue-600 dark:text-blue-400',
    amber: 'text-amber-600 dark:text-amber-400',
    purple: 'text-purple-600 dark:text-purple-400',
  };

  return (
    <div className="p-4 bg-dark-50 dark:bg-dark-900/50 rounded-lg">
      <p className="text-xs text-dark-500 uppercase tracking-wider">{label}</p>
      <p className="mt-1 font-bold tabular-nums">
        <span className={colorClasses[color]}>{prefix}{value}{suffix}</span>
      </p>
    </div>
  );
}

export default function ReportsPage() {
  const [reports, setReports] = useState<DailyReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedReport, setSelectedReport] = useState<DailyReport | null>(null);

  useEffect(() => {
    const fetchReports = async () => {
      try {
        const res = await api.get('/api/daily-report?limit=30');
        setReports(Array.isArray(res.data) ? res.data : [res.data].filter(Boolean));
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    };
    fetchReports();
  }, []);

  if (loading) {
    return <div className="p-8 text-center">Loading...</div>;
  }

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-dark-900 dark:text-dark-50">Daily Reports</h1>
          <p className="text-dark-500">End-of-day summaries with AI narrative</p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Report List */}
        <div className="lg:col-span-1">
          <div className="bg-white dark:bg-dark-800 rounded-xl border border-dark-200 dark:border-dark-700 overflow-hidden">
            <div className="p-4 border-b border-dark-200 dark:border-dark-700">
              <h2 className="font-semibold text-dark-900 dark:text-dark-50">Reports ({reports.length})</h2>
            </div>
            <div className="max-h-[600px] overflow-y-auto">
              {reports.length === 0 ? (
                <div className="p-8 text-center text-dark-500">No reports available</div>
              ) : (
                <ul className="divide-y divide-dark-100 dark:divide-dark-800">
                  {reports.map((report) => (
                    <li key={report.report_date}>
                      <button
                        onClick={() => setSelectedReport(report)}
                        className={`w-full p-4 text-left transition-colors ${selectedReport?.report_date === report.report_date
                            ? 'bg-primary-50 dark:bg-primary-900/20'
                            : 'hover:bg-dark-50 dark:hover:bg-dark-900/50'
                          }`}
                      >
                        <div className="flex items-center justify-between">
                          <div>
                            <p className="font-medium text-dark-900 dark:text-dark-50">
                              {format(new Date(report.report_date), 'MMM dd, yyyy')}
                            </p>
                            <p className="text-sm text-dark-500">{report.trades_count} trades</p>
                          </div>
                          <span className={`font-bold tabular-nums ${report.total_pnl >= 0 ? 'text-primary-600' : 'text-danger-600'}`}>
                            ₹{report.total_pnl.toFixed(2)}
                          </span>
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>

        {/* Report Detail */}
        <div className="lg:col-span-2">
          {selectedReport ? (
            <div className="bg-white dark:bg-dark-800 rounded-xl border border-dark-200 dark:border-dark-700 overflow-hidden">
              <div className="p-6 border-b border-dark-200 dark:border-dark-700">
                <div className="flex items-center justify-between">
                  <div>
                    <h2 className="text-xl font-bold text-dark-900 dark:text-dark-50">
                      {format(new Date(selectedReport.report_date), 'EEEE, MMMM dd, yyyy')}
                    </h2>
                    <p className="text-dark-500 mt-1">Generated: {format(new Date(selectedReport.created_at), 'HH:mm:ss')}</p>
                  </div>
                  <div className={`text-3xl font-bold tabular-nums ${selectedReport.total_pnl >= 0 ? 'text-primary-600' : 'text-danger-600'}`}>
                    ₹{selectedReport.total_pnl.toFixed(2)}
                  </div>
                </div>
              </div>

              <div className="p-6 space-y-6">
                {/* Summary Grid */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <StatBox label="Total P&L" value={selectedReport.total_pnl} prefix="₹" color={selectedReport.total_pnl >= 0 ? 'primary' : 'danger'} />
                  <StatBox label="Trades" value={selectedReport.trades_count} color="blue" />
                  <StatBox label="Wins" value={selectedReport.wins} color="primary" />
                  <StatBox label="Losses" value={selectedReport.losses} color="danger" />
                  <StatBox label="Win Rate" value={selectedReport.trades_count > 0 ? (selectedReport.wins / selectedReport.trades_count * 100).toFixed(1) : 0} suffix="%" color="purple" />
                  <StatBox label="Max DD" value={selectedReport.max_drawdown} prefix="₹" color="danger" />
                  <StatBox label="Best Trade" value={selectedReport.best_trade_pnl} prefix="₹" color="primary" />
                  <StatBox label="Worst Trade" value={selectedReport.worst_trade_pnl} prefix="₹" color="danger" />
                </div>

                {/* Strategy Performance */}
                {selectedReport.strategy_performance && selectedReport.strategy_performance.length > 0 && (
                  <div>
                    <h3 className="text-lg font-semibold text-dark-900 dark:text-dark-50 mb-3">Strategy Performance</h3>
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="text-left text-dark-500 border-b border-dark-200 dark:border-dark-700">
                            <th className="p-3 font-medium">Strategy</th>
                            <th className="p-3 font-medium">P&L</th>
                            <th className="p-3 font-medium">Trades</th>
                            <th className="p-3 font-medium">Wins</th>
                            <th className="p-3 font-medium">Losses</th>
                            <th className="p-3 font-medium">Win Rate</th>
                          </tr>
                        </thead>
                        <tbody>
                          {selectedReport.strategy_performance.map((s) => (
                            <tr key={s.strategy} className="border-b border-dark-100 dark:border-dark-800">
                              <td className="p-3 font-medium text-dark-900 dark:text-dark-50">{s.strategy}</td>
                              <td className="p-3 tabular-nums">
                                <span className={s.total_pnl >= 0 ? 'text-primary-600' : 'text-danger-600'}>
                                  ₹{s.total_pnl.toFixed(2)}
                                </span>
                              </td>
                              <td className="p-3 tabular-nums">{s.trades}</td>
                              <td className="p-3 tabular-nums text-primary-600">{s.wins}</td>
                              <td className="p-3 tabular-nums text-danger-600">{s.losses}</td>
                              <td className="p-3 tabular-nums">{s.win_rate.toFixed(1)}%</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                {/* Regime Summary */}
                {selectedReport.regime_summary && Object.keys(selectedReport.regime_summary).length > 0 && (
                  <div>
                    <h3 className="text-lg font-semibold text-dark-900 dark:text-dark-50 mb-3">Regime Distribution</h3>
                    <div className="flex flex-wrap gap-2">
                      {Object.entries(selectedReport.regime_summary.percentages || {}).map(([regime, pct]) => (
                        <span key={regime} className="px-3 py-1 bg-dark-100 dark:bg-dark-900 rounded-lg text-sm">
                          {regime}: {String(pct)}%
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {/* AI Summary */}
                {selectedReport.ai_summary && (
                  <div className="p-4 bg-primary-50 dark:bg-primary-900/20 border border-primary-100 dark:border-primary-800 rounded-lg">
                    <h3 className="text-lg font-semibold text-primary-700 dark:text-primary-400 mb-2 flex items-center gap-2">
                      🤖 AI Summary
                    </h3>
                    <p className="text-primary-600 dark:text-primary-400">{selectedReport.ai_summary}</p>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="bg-white dark:bg-dark-800 rounded-xl border border-dark-200 dark:border-dark-700 p-12 text-center text-dark-500">
              <p className="text-lg">Select a report from the list to view details</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}