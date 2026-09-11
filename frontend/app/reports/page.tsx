'use client';

import React, { useState, useEffect } from 'react';
import { 
  FileText, 
  Sparkles, 
  Calendar, 
  Award, 
  TrendingUp, 
  TrendingDown, 
  RefreshCw,
  BarChart3,
  Layers
} from 'lucide-react';
import { formatINR, formatPercent } from '@/lib/utils';
import { api } from '@/lib/api';

export default function ReportsPage() {
  const [reports, setReports] = useState<any[]>([]);
  const [selectedReport, setSelectedReport] = useState<any>(null);
  const [loading, setLoading] = useState<boolean>(true);

  const fetchReports = async () => {
    try {
      setLoading(true);
      const res = await api.get('/api/daily-report?limit=30').catch(() => ({ data: [] }));
      const data = Array.isArray(res.data) ? res.data : [res.data].filter(Boolean);
      setReports(data);
      if (data.length > 0) {
        setSelectedReport(data[0]);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchReports();
  }, []);

  const totalPnl = selectedReport?.total_pnl || 0;
  const isProfit = totalPnl >= 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
            <FileText className="w-5 h-5 text-emerald-400" />
            Daily Compliance &amp; PnL Reports
          </h1>
          <p className="text-xs text-slate-400 mt-0.5">
            SEBI mandated end-of-day reports, AI post-market commentary, and strategy logs
          </p>
        </div>

        <button
          onClick={fetchReports}
          className="flex items-center gap-2 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 rounded-lg text-xs font-semibold transition-colors"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin text-emerald-400' : ''}`} />
          <span>Refresh Reports</span>
        </button>
      </div>

      {/* Grid: Left Date Selector, Right Report Viewer */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Date Index */}
        <div className="bg-slate-900/90 border border-slate-800 rounded-xl overflow-hidden shadow-sm h-fit">
          <div className="px-4 py-3 border-b border-slate-800 bg-slate-950/40 font-semibold text-xs text-slate-300 flex items-center gap-2">
            <Calendar className="w-4 h-4 text-emerald-400" />
            <span>Trading Sessions ({reports.length})</span>
          </div>

          <div className="p-2 space-y-1 max-h-[500px] overflow-y-auto divide-y divide-slate-800/40">
            {reports.length === 0 ? (
              <div className="text-center py-8 text-xs text-slate-500 font-mono">
                No past daily reports found. EOD scheduler generates reports automatically at 15:30 IST.
              </div>
            ) : (
              reports.map((rep) => {
                const repProfit = (rep.total_pnl || 0) >= 0;
                const isSelected = selectedReport?.report_date === rep.report_date;

                return (
                  <button
                    key={rep.report_date}
                    onClick={() => setSelectedReport(rep)}
                    className={`w-full p-3 rounded-lg text-left transition-all flex items-center justify-between ${
                      isSelected
                        ? 'bg-emerald-500/10 border border-emerald-500/30'
                        : 'hover:bg-slate-800/60 border border-transparent'
                    }`}
                  >
                    <div>
                      <div className="font-bold text-xs text-white font-mono">{rep.report_date}</div>
                      <div className="text-[11px] text-slate-400 mt-0.5">{rep.trades_count || 0} Trades</div>
                    </div>
                    <div className={`font-mono font-bold text-xs ${repProfit ? 'text-emerald-400' : 'text-rose-400'}`}>
                      {repProfit ? '+' : ''}{formatINR(rep.total_pnl)}
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </div>

        {/* Right: Detailed Report Viewer */}
        <div className="lg:col-span-2 space-y-4">
          {selectedReport ? (
            <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-5 space-y-5 shadow-sm">
              {/* Header */}
              <div className="flex items-center justify-between border-b border-slate-800 pb-4">
                <div>
                  <h3 className="font-bold text-base text-white font-mono">
                    Report: {selectedReport.report_date}
                  </h3>
                  <p className="text-xs text-slate-400 mt-0.5">Automated EOD Reconciliation Snapshot</p>
                </div>
                <div className={`px-3 py-1 rounded font-mono font-bold text-sm ${isProfit ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-rose-500/10 text-rose-400 border border-rose-500/20'}`}>
                  {isProfit ? '+' : ''}{formatINR(totalPnl)}
                </div>
              </div>

              {/* Stats Summary */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="bg-slate-950/60 p-3 rounded-lg border border-slate-800">
                  <div className="text-[10px] text-slate-400">Total Trades</div>
                  <div className="text-sm font-bold font-mono text-white mt-1">
                    {selectedReport.trades_count || 0}
                  </div>
                </div>
                <div className="bg-slate-950/60 p-3 rounded-lg border border-slate-800">
                  <div className="text-[10px] text-slate-400">Win / Loss</div>
                  <div className="text-sm font-bold font-mono text-emerald-400 mt-1">
                    {selectedReport.wins || 0}W / {selectedReport.losses || 0}L
                  </div>
                </div>
                <div className="bg-slate-950/60 p-3 rounded-lg border border-slate-800">
                  <div className="text-[10px] text-slate-400">Best Trade</div>
                  <div className="text-sm font-bold font-mono text-emerald-400 mt-1">
                    {formatINR(selectedReport.best_trade_pnl || 0)}
                  </div>
                </div>
                <div className="bg-slate-950/60 p-3 rounded-lg border border-slate-800">
                  <div className="text-[10px] text-slate-400">Worst Trade</div>
                  <div className="text-sm font-bold font-mono text-rose-400 mt-1">
                    {formatINR(selectedReport.worst_trade_pnl || 0)}
                  </div>
                </div>
              </div>

              {/* AI Narrative Commentary */}
              {selectedReport.ai_summary && (
                <div className="bg-emerald-950/20 border border-emerald-500/20 rounded-lg p-4 space-y-1.5">
                  <div className="text-xs font-bold text-emerald-400 flex items-center gap-1.5 uppercase tracking-wider">
                    <Sparkles className="w-3.5 h-3.5" />
                    <span>AI Post-Market Intelligence</span>
                  </div>
                  <p className="text-xs text-slate-300 leading-relaxed">
                    {selectedReport.ai_summary}
                  </p>
                </div>
              )}
            </div>
          ) : (
            <div className="bg-slate-900/90 border border-slate-800 rounded-xl p-12 text-center text-slate-500 text-xs font-mono">
              Select a date from the left to view the session report.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}