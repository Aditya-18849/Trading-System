"""
Daily Report Generator — End-of-day report with AI narrative.

Aggregates the day's trades, P&L, strategy performance, and regime history
into a structured report; sends via Telegram and persists to DB.
"""

import logging
from datetime import datetime, date, timezone, timedelta
from typing import List, Dict, Optional, Any
from collections import defaultdict

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.config import settings
from app.models import (
    Trade, User, DailyReport, PerformanceSnapshot, RegimeSnapshot, Strategy
)
from app.analytics.performance import PerformanceAnalytics, get_performance_analytics
from app.notifications.telegram import TelegramNotifier
from app.strategy_engine.ai_summary import AISummaryGenerator, get_ai_summary_generator

logger = logging.getLogger(__name__)

# IST timezone
IST = timezone(timedelta(hours=5, minutes=30))


class DailyReportGenerator:
    """Generates end-of-day trading reports."""

    def __init__(
        self,
        db: Session,
        user: User,
        notifier: Optional[TelegramNotifier] = None,
        ai_generator: Optional[AISummaryGenerator] = None,
    ):
        """Initialize the report generator.

        Args:
            db: Database session.
            user: User to generate report for.
            notifier: Telegram notifier for sending report.
            ai_generator: AI summary generator for narrative.
        """
        self.db = db
        self.user = user
        self.notifier = notifier
        self.ai_generator = ai_generator or get_ai_summary_generator()
        self.analytics = get_performance_analytics(db, str(user.id))

    def generate_report(self, report_date: Optional[date] = None) -> DailyReport:
        """Generate a complete daily report.

        Args:
            report_date: Date to generate report for (default: today).

        Returns:
            Persisted DailyReport object.
        """
        report_date = report_date or datetime.now(IST).date()

        # Check if report already exists
        existing = self.db.query(DailyReport).filter(
            DailyReport.user_id == self.user.id,
            DailyReport.report_date == report_date,
        ).first()

        if existing:
            logger.info("Report for %s already exists", report_date)
            return existing

        # Gather data
        trades = self._get_trades_for_date(report_date)
        regime_summary = self._get_regime_summary(report_date)
        strategy_performance = self._get_strategy_performance(report_date)
        portfolio_metrics = self.analytics.get_portfolio_metrics(
            start_date=report_date, end_date=report_date
        )

        # Calculate aggregates
        total_pnl = sum(float(t.pnl or 0) for t in trades if t.status == "CLOSED")
        trades_count = len([t for t in trades if t.status == "CLOSED"])
        wins = len([t for t in trades if t.status == "CLOSED" and float(t.pnl or 0) > 0])
        losses = trades_count - wins

        best_trade = max(trades, key=lambda t: float(t.pnl or 0)) if trades else None
        worst_trade = min(trades, key=lambda t: float(t.pnl or 0)) if trades else None

        # Generate AI narrative
        ai_summary = self._generate_ai_narrative(
            trades, regime_summary, strategy_performance, portfolio_metrics
        )

        # Create report
        report = DailyReport(
            user_id=self.user.id,
            report_date=report_date,
            total_pnl=total_pnl,
            trades_count=trades_count,
            wins=wins,
            losses=losses,
            max_drawdown=portfolio_metrics.get("max_drawdown", 0),
            best_trade_pnl=float(best_trade.pnl) if best_trade else 0,
            worst_trade_pnl=float(worst_trade.pnl) if worst_trade else 0,
            best_strategy=strategy_performance[0]["strategy"] if strategy_performance else None,
            worst_strategy=strategy_performance[-1]["strategy"] if strategy_performance else None,
            regime_summary=regime_summary,
            strategy_performance=strategy_performance,
            ai_summary=ai_summary,
            telegram_sent=False,
        )

        self.db.add(report)
        self.db.commit()
        self.db.refresh(report)

        logger.info("Generated daily report for %s: P&L=₹%.2f, Trades=%d",
                    report_date, total_pnl, trades_count)

        return report

    def send_telegram_report(self, report: DailyReport) -> bool:
        """Send the daily report via Telegram."""
        if not self.notifier:
            logger.warning("No notifier configured, skipping Telegram")
            return False

        try:
            message = self._format_telegram_message(report)
            success = self.notifier.send_message(message)

            if success:
                report.telegram_sent = True
                self.db.commit()
                logger.info("Daily report sent via Telegram for %s", report.report_date)

            return success

        except Exception as e:
            logger.error("Failed to send daily report via Telegram: %s", e)
            return False

    def _get_trades_for_date(self, report_date: date) -> List[Trade]:
        """Get all trades opened on a specific date."""
        return self.db.query(Trade).filter(
            Trade.user_id == self.user.id,
            func.date(Trade.opened_at) == report_date,
        ).order_by(Trade.opened_at).all()

    def _get_regime_summary(self, report_date: date) -> Dict:
        """Get regime distribution for the day."""
        regimes = self.db.query(RegimeSnapshot).filter(
            func.date(RegimeSnapshot.timestamp) == report_date,
        ).all()

        regime_counts = defaultdict(int)
        for r in regimes:
            regime_counts[r.regime] += 1

        total = sum(regime_counts.values())
        return {
            "distribution": dict(regime_counts),
            "percentages": {
                k: round(v / total * 100, 1) for k, v in regime_counts.items()
            } if total > 0 else {},
            "dominant": max(regime_counts.items(), key=lambda x: x[1])[0] if regime_counts else "unknown",
        }

    def _get_strategy_performance(self, report_date: date) -> List[Dict]:
        """Get per-strategy performance for the day."""
        trades = self._get_trades_for_date(report_date)

        strat_data = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0, "losses": 0})

        for t in trades:
            if t.status != "CLOSED":
                continue
            strat = self.db.query(Strategy).filter(Strategy.id == t.strategy_id).first()
            name = strat.name if strat else str(t.strategy_id)
            pnl = float(t.pnl or 0)
            strat_data[name]["pnl"] += pnl
            strat_data[name]["trades"] += 1
            if pnl > 0:
                strat_data[name]["wins"] += 1
            else:
                strat_data[name]["losses"] += 1

        result = []
        for name, d in strat_data.items():
            win_rate = (d["wins"] / d["trades"] * 100) if d["trades"] > 0 else 0
            result.append({
                "strategy": name,
                "total_pnl": round(d["pnl"], 2),
                "trades": d["trades"],
                "wins": d["wins"],
                "losses": d["losses"],
                "win_rate": round(win_rate, 1),
            })

        # Sort by P&L
        result.sort(key=lambda x: x["total_pnl"], reverse=True)
        return result

    def _generate_ai_narrative(
        self,
        trades: List[Trade],
        regime_summary: Dict,
        strategy_performance: List[Dict],
        portfolio_metrics: Dict,
    ) -> str:
        """Generate AI narrative for the daily report."""
        if not self.ai_generator.client:
            return self._fallback_narrative(trades, regime_summary, strategy_performance)

        try:
            prompt = self._build_report_prompt(
                trades, regime_summary, strategy_performance, portfolio_metrics
            )

            response = self.ai_generator.client.chat.completions.create(
                model=self.ai_generator.model,
                messages=[
                    {"role": "system", "content": self._report_system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
                max_tokens=400,
            )

            return response.choices[0].message.content.strip()

        except Exception as e:
            logger.error("AI narrative generation failed: %s", e)
            return self._fallback_narrative(trades, regime_summary, strategy_performance)

    def _report_system_prompt(self) -> str:
        return """You are a professional trading performance analyst. Write a concise 
end-of-day trading report narrative (3-4 sentences) summarizing the day's results, 
key drivers, and notable observations. Be specific with numbers. Professional tone."""

    def _build_report_prompt(
        self,
        trades: List[Trade],
        regime_summary: Dict,
        strategy_performance: List[Dict],
        portfolio_metrics: Dict,
    ) -> str:
        lines = [f"=== DAILY TRADING REPORT - {datetime.now(IST).strftime('%Y-%m-%d')} ==="]
        lines.append(f"\nPortfolio P&L: ₹{portfolio_metrics.get('total_pnl', 0):.2f}")
        lines.append(f"Trades: {portfolio_metrics.get('total_trades', 0)} (W: {portfolio_metrics.get('win_rate', 0):.1f}%)")
        lines.append(f"Max Drawdown: {portfolio_metrics.get('max_drawdown_pct', 0):.2f}%")

        lines.append(f"\nRegime: {regime_summary.get('dominant', 'unknown')}")
        lines.append(f"Distribution: {regime_summary.get('percentages', {})}")

        lines.append("\nStrategy Performance:")
        for sp in strategy_performance[:5]:
            lines.append(f"  {sp['strategy']}: ₹{sp['total_pnl']:.2f} ({sp['trades']} trades, {sp['win_rate']:.1f}% WR)")

        lines.append("\nWrite a concise 3-4 sentence narrative.")
        return "\n".join(lines)

    def _fallback_narrative(
        self,
        trades: List[Trade],
        regime_summary: Dict,
        strategy_performance: List[Dict],
    ) -> str:
        total_pnl = sum(float(t.pnl or 0) for t in trades if t.status == "CLOSED")
        trades_count = len([t for t in trades if t.status == "CLOSED"])
        wins = len([t for t in trades if t.status == "CLOSED" and float(t.pnl or 0) > 0])

        if trades_count == 0:
            return "No trades executed today. System monitored markets but no setups met entry criteria."

        best = strategy_performance[0] if strategy_performance else None
        regime = regime_summary.get('dominant', 'unknown')

        return (
            f"Today's session closed with {trades_count} trades ({(wins/trades_count*100):.0f}% win rate) "
            f"for a net P&L of ₹{total_pnl:.2f}. "
            f"Market regime was predominantly {regime.replace('-', ' ')}. "
            f"{'Best performing strategy was ' + best['strategy'] + ' at ₹' + f'{best['total_pnl']:.2f}' if best else 'No single strategy dominated.'} "
            f"Risk controls remained within limits."
        )

    def _format_telegram_message(self, report: DailyReport) -> str:
        """Format report for Telegram delivery."""
        pnl_emoji = "📈" if report.total_pnl >= 0 else "📉"

        lines = [
            f"{pnl_emoji} <b>Daily Trading Report</b>",
            f"📅 {report.report_date.strftime('%Y-%m-%d')}",
            "",
            f"💰 <b>Net P&L: ₹{report.total_pnl:.2f}</b>",
            f"📊 Trades: {report.trades_count} (W: {report.wins} / L: {report.losses})",
            f"📉 Max DD: {report.max_drawdown:.2f}%",
            "",
        ]

        if report.best_trade_pnl or report.worst_trade_pnl:
            lines.append(f"🏆 Best Trade: ₹{report.best_trade_pnl:.2f}")
            lines.append(f"📉 Worst Trade: ₹{report.worst_trade_pnl:.2f}")

        if report.best_strategy or report.worst_strategy:
            lines.append(f"⭐ Best Strategy: {report.best_strategy}")
            lines.append(f"⚠️ Worst Strategy: {report.worst_strategy}")

        if report.regime_summary:
            regime_dist = report.regime_summary.get('percentages', {})
            if regime_dist:
                lines.append("")
                lines.append("🌡️ Regime Distribution:")
                for r, pct in regime_dist.items():
                    lines.append(f"  {r}: {pct}%")

        if report.ai_summary:
            lines.append("")
            lines.append(f"🤖 <b>AI Summary:</b>")
            lines.append(report.ai_summary)

        return "\n".join(lines)


def generate_daily_report_for_all_users(db: Session, notifier: TelegramNotifier) -> List[DailyReport]:
    """Generate daily reports for all active users (called by scheduler)."""
    users = db.query(User).filter(User.is_active == True).all()
    reports = []

    for user in users:
        try:
            generator = DailyReportGenerator(db, user, notifier)
            report = generator.generate_report()
            generator.send_telegram_report(report)
            reports.append(report)
        except Exception as e:
            logger.error("Failed to generate report for user %s: %s", user.id, e)

    return reports