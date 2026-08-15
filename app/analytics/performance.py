"""
Performance Analytics — Equity curve, win rate, drawdown calculations.

Provides metrics for both the ranker (historical performance) and dashboard display.
"""

import logging
from datetime import datetime, date, timezone, timedelta
from typing import List, Dict, Optional, Tuple
from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy import func, and_, desc

from app.models import Trade, PerformanceSnapshot, User
from app.schemas import PerformanceMetrics

logger = logging.getLogger(__name__)

# IST timezone
IST = timezone(timedelta(hours=5, minutes=30))


class PerformanceAnalytics:
    """Calculates performance metrics from trade data."""

    def __init__(self, db: Session, user_id: str):
        """Initialize analytics engine.

        Args:
            db: Database session.
            user_id: User ID to analyze.
        """
        self.db = db
        self.user_id = user_id

    # ------------------------------------------------------------------------
    # Equity Curve
    # ------------------------------------------------------------------------

    def get_equity_curve(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        strategy_name: Optional[str] = None,
    ) -> List[Dict]:
        """Get daily equity curve data.

        Args:
            start_date: Start date (inclusive).
            end_date: End date (inclusive).
            strategy_name: Optional strategy filter.

        Returns:
            List of dicts with date, equity, pnl, trades_count, win_rate, max_drawdown_pct.
        """
        query = self.db.query(PerformanceSnapshot).filter(
            PerformanceSnapshot.user_id == self.user_id,
            PerformanceSnapshot.strategy_name.is_(None) if strategy_name is None
            else PerformanceSnapshot.strategy_name == strategy_name,
            PerformanceSnapshot.symbol.is_(None),  # Portfolio level only
        )

        if start_date:
            query = query.filter(PerformanceSnapshot.date >= start_date)
        if end_date:
            query = query.filter(PerformanceSnapshot.date <= end_date)

        snapshots = query.order_by(PerformanceSnapshot.date).all()

        return [
            {
                "date": s.date.isoformat(),
                "equity": float(s.ending_capital),
                "pnl": float(s.total_pnl),
                "trades_count": s.trades_count,
                "win_rate": float(s.win_rate),
                "max_drawdown_pct": float(s.max_drawdown_pct),
            }
            for s in snapshots
        ]

    def get_daily_pnl(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[Dict]:
        """Get daily P&L series for charting."""
        query = self.db.query(PerformanceSnapshot).filter(
            PerformanceSnapshot.user_id == self.user_id,
            PerformanceSnapshot.strategy_name.is_(None),
            PerformanceSnapshot.symbol.is_(None),
        )

        if start_date:
            query = query.filter(PerformanceSnapshot.date >= start_date)
        if end_date:
            query = query.filter(PerformanceSnapshot.date <= end_date)

        snapshots = query.order_by(PerformanceSnapshot.date).all()

        return [
            {
                "date": s.date.isoformat(),
                "pnl": float(s.total_pnl),
                "realized_pnl": float(s.realized_pnl),
                "unrealized_pnl": float(s.unrealized_pnl),
            }
            for s in snapshots
        ]

    # ------------------------------------------------------------------------
    # Strategy Performance
    # ------------------------------------------------------------------------

    def get_strategy_performance(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[PerformanceMetrics]:
        """Get performance metrics per strategy.

        Args:
            start_date: Start date for analysis.
            end_date: End date for analysis.

        Returns:
            List of PerformanceMetrics per strategy.
        """
        # Default to last 30 days
        if not end_date:
            end_date = datetime.now(IST).date()
        if not start_date:
            start_date = end_date - timedelta(days=30)

        # Get all strategy snapshots in date range
        snapshots = self.db.query(PerformanceSnapshot).filter(
            PerformanceSnapshot.user_id == self.user_id,
            PerformanceSnapshot.strategy_name.isnot(None),
            PerformanceSnapshot.symbol.is_(None),
            PerformanceSnapshot.date >= start_date,
            PerformanceSnapshot.date <= end_date,
        ).all()

        # Aggregate by strategy
        strategy_data: Dict[str, Dict] = {}

        for s in snapshots:
            name = s.strategy_name
            if name not in strategy_data:
                strategy_data[name] = {
                    "total_pnl": 0.0,
                    "trades_count": 0,
                    "wins": 0,
                    "losses": 0,
                    "max_drawdown": 0.0,
                    "max_drawdown_pct": 0.0,
                    "daily_returns": [],
                    "expectancy_sum": 0.0,
                    "expectancy_count": 0,
                }

            d = strategy_data[name]
            d["total_pnl"] += float(s.total_pnl)
            d["trades_count"] += s.trades_count
            d["wins"] += s.wins
            d["losses"] += s.losses
            d["max_drawdown"] = max(d["max_drawdown"], float(s.max_drawdown))
            d["max_drawdown_pct"] = max(d["max_drawdown_pct"], float(s.max_drawdown_pct))
            if s.expectancy is not None:
                d["expectancy_sum"] += float(s.expectancy)
                d["expectancy_count"] += 1
            d["daily_returns"].append(float(s.total_pnl))

        # Build metrics
        metrics = []
        for name, d in strategy_data.items():
            win_rate = (d["wins"] / d["trades_count"] * 100) if d["trades_count"] > 0 else 0
            expectancy = d["expectancy_sum"] / d["expectancy_count"] if d["expectancy_count"] > 0 else 0

            # Calculate Sharpe from daily returns
            sharpe = self._calculate_sharpe(d["daily_returns"])

            metrics.append(PerformanceMetrics(
                strategy_name=name,
                total_pnl=d["total_pnl"],
                trades_count=d["trades_count"],
                wins=d["wins"],
                losses=d["losses"],
                win_rate=round(win_rate, 2),
                expectancy=round(expectancy, 2),
                max_drawdown=d["max_drawdown"],
                max_drawdown_pct=round(d["max_drawdown_pct"], 2),
                sharpe_ratio=sharpe,
            ))

        # Sort by total P&L descending
        metrics.sort(key=lambda x: x.total_pnl, reverse=True)
        return metrics

    def get_symbol_performance(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[Dict]:
        """Get performance per symbol."""
        if not end_date:
            end_date = datetime.now(IST).date()
        if not start_date:
            start_date = end_date - timedelta(days=30)

        snapshots = self.db.query(PerformanceSnapshot).filter(
            PerformanceSnapshot.user_id == self.user_id,
            PerformanceSnapshot.strategy_name.is_(None),
            PerformanceSnapshot.symbol.isnot(None),
            PerformanceSnapshot.date >= start_date,
            PerformanceSnapshot.date <= end_date,
        ).all()

        symbol_data: Dict[str, Dict] = {}

        for s in snapshots:
            sym = s.symbol
            if sym not in symbol_data:
                symbol_data[sym] = {"pnl": 0.0, "trades": 0, "wins": 0, "losses": 0}

            d = symbol_data[sym]
            d["pnl"] += float(s.total_pnl)
            d["trades"] += s.trades_count
            d["wins"] += s.wins
            d["losses"] += s.losses

        return [
            {
                "symbol": sym,
                "total_pnl": d["pnl"],
                "trades_count": d["trades"],
                "wins": d["wins"],
                "losses": d["losses"],
                "win_rate": round(d["wins"] / d["trades"] * 100, 2) if d["trades"] > 0 else 0,
            }
            for sym, d in symbol_data.items()
        ]

    # ------------------------------------------------------------------------
    # Portfolio Metrics
    # ------------------------------------------------------------------------

    def get_portfolio_metrics(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Dict:
        """Get aggregate portfolio metrics."""
        equity_curve = self.get_equity_curve(start_date, end_date)
        strategy_metrics = self.get_strategy_performance(start_date, end_date)

        if not equity_curve:
            return {
                "total_pnl": 0,
                "total_trades": 0,
                "win_rate": 0,
                "max_drawdown": 0,
                "max_drawdown_pct": 0,
                "sharpe_ratio": 0,
                "current_equity": 0,
                "best_strategy": None,
                "worst_strategy": None,
            }

        total_pnl = sum(e["pnl"] for e in equity_curve)
        total_trades = sum(e["trades_count"] for e in equity_curve)
        total_wins = sum(
            m.wins for m in strategy_metrics
        )
        win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0

        max_dd = max(e["max_drawdown_pct"] for e in equity_curve)
        current_equity = equity_curve[-1]["equity"] if equity_curve else 0

        daily_returns = [e["pnl"] for e in equity_curve]
        sharpe = self._calculate_sharpe(daily_returns)

        best_strat = max(strategy_metrics, key=lambda x: x.total_pnl) if strategy_metrics else None
        worst_strat = min(strategy_metrics, key=lambda x: x.total_pnl) if strategy_metrics else None

        return {
            "total_pnl": round(total_pnl, 2),
            "total_trades": total_trades,
            "win_rate": round(win_rate, 2),
            "max_drawdown": round(max(e["max_drawdown"] for e in equity_curve) if equity_curve else 0, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe_ratio": sharpe,
            "current_equity": round(current_equity, 2),
            "best_strategy": best_strat.strategy_name if best_strat else None,
            "worst_strategy": worst_strat.strategy_name if worst_strat else None,
        }

    # ------------------------------------------------------------------------
    # Real-time / Live Metrics
    # ------------------------------------------------------------------------

    def get_live_metrics(self) -> Dict:
        """Get current live metrics from open trades."""
        open_trades = self.db.query(Trade).filter(
            Trade.user_id == self.user_id,
            Trade.status == "OPEN",
        ).all()

        total_unrealized = sum(float(t.pnl or 0) for t in open_trades)
        open_count = len(open_trades)

        # Today's realized P&L
        today = datetime.now(IST).date()
        today_trades = self.db.query(Trade).filter(
            Trade.user_id == self.user_id,
            func.date(Trade.opened_at) == today,
            Trade.status == "CLOSED",
        ).all()

        today_realized = sum(float(t.pnl or 0) for t in today_trades)
        today_count = len(today_trades)
        today_wins = sum(1 for t in today_trades if float(t.pnl or 0) > 0)

        return {
            "open_positions": open_count,
            "unrealized_pnl": round(total_unrealized, 2),
            "today_realized_pnl": round(today_realized, 2),
            "today_trades": today_count,
            "today_wins": today_wins,
            "today_win_rate": round(today_wins / today_count * 100, 2) if today_count > 0 else 0,
        }

    # ------------------------------------------------------------------------
    # Snapshot Generation (for EOD job)
    # ------------------------------------------------------------------------

    def generate_daily_snapshot(self, snapshot_date: date) -> List[PerformanceSnapshot]:
        """Generate performance snapshots for a specific date.

        Creates portfolio-level and per-strategy snapshots.
        Should be called at end of day.

        Args:
            snapshot_date: Date to generate snapshot for.

        Returns:
            List of created PerformanceSnapshot objects.
        """
        # Get all trades for this date
        trades = self.db.query(Trade).filter(
            Trade.user_id == self.user_id,
            func.date(Trade.opened_at) == snapshot_date,
        ).all()

        if not trades:
            logger.info("No trades for %s, skipping snapshot", snapshot_date)
            return []

        # Get user capital
        user = self.db.query(User).filter(User.id == self.user_id).first()
        starting_capital = float(user.total_capital) if user else 100000

        # Calculate metrics
        closed_trades = [t for t in trades if t.status == "CLOSED"]
        open_trades = [t for t in trades if t.status == "OPEN"]

        realized_pnl = sum(float(t.pnl or 0) for t in closed_trades)
        unrealized_pnl = sum(float(t.pnl or 0) for t in open_trades)
        total_pnl = realized_pnl + unrealized_pnl

        ending_capital = starting_capital + total_pnl

        wins = sum(1 for t in closed_trades if float(t.pnl or 0) > 0)
        losses = sum(1 for t in closed_trades if float(t.pnl or 0) < 0)
        trades_count = len(closed_trades)

        # Calculate drawdown (simplified - would need equity curve history)
        max_drawdown = 0.0
        max_drawdown_pct = 0.0

        # Expectancy
        avg_win = sum(float(t.pnl or 0) for t in closed_trades if float(t.pnl or 0) > 0) / wins if wins > 0 else 0
        avg_loss = abs(sum(float(t.pnl or 0) for t in closed_trades if float(t.pnl or 0) < 0) / losses) if losses > 0 else 0
        win_rate = wins / trades_count if trades_count > 0 else 0
        expectancy = (avg_win * win_rate) - (avg_loss * (1 - win_rate))

        # Create portfolio snapshot
        portfolio_snap = PerformanceSnapshot(
            user_id=self.user_id,
            strategy_name=None,
            symbol=None,
            date=snapshot_date,
            starting_capital=starting_capital,
            ending_capital=ending_capital,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            trades_count=trades_count,
            wins=wins,
            losses=losses,
            max_drawdown=max_drawdown,
            max_drawdown_pct=max_drawdown_pct,
            expectancy=expectancy,
        )
        self.db.add(portfolio_snap)

        # Per-strategy snapshots
        strategy_snapshots = []
        from collections import defaultdict
        strat_trades = defaultdict(list)
        for t in trades:
            strat_trades[t.strategy_id].append(t)

        for strat_id, tlist in strat_trades.items():
            closed = [t for t in tlist if t.status == "CLOSED"]
            open_t = [t for t in tlist if t.status == "OPEN"]

            strat_realized = sum(float(t.pnl or 0) for t in closed)
            strat_unrealized = sum(float(t.pnl or 0) for t in open_t)
            strat_wins = sum(1 for t in closed if float(t.pnl or 0) > 0)
            strat_losses = sum(1 for t in closed if float(t.pnl or 0) < 0)
            strat_count = len(closed)

            strat_win_rate = strat_wins / strat_count if strat_count > 0 else 0
            strat_avg_win = sum(float(t.pnl or 0) for t in closed if float(t.pnl or 0) > 0) / strat_wins if strat_wins > 0 else 0
            strat_avg_loss = abs(sum(float(t.pnl or 0) for t in closed if float(t.pnl or 0) < 0) / strat_losses) if strat_losses > 0 else 0
            strat_expectancy = (strat_avg_win * strat_win_rate) - (strat_avg_loss * (1 - strat_win_rate))

            # Get strategy name
            from app.models import Strategy
            strat = self.db.query(Strategy).filter(Strategy.id == strat_id).first()
            strat_name = strat.name if strat else str(strat_id)

            snap = PerformanceSnapshot(
                user_id=self.user_id,
                strategy_name=strat_name,
                symbol=None,
                date=snapshot_date,
                starting_capital=starting_capital,  # Same starting capital
                ending_capital=starting_capital + strat_realized + strat_unrealized,
                realized_pnl=strat_realized,
                unrealized_pnl=strat_unrealized,
                trades_count=strat_count,
                wins=strat_wins,
                losses=strat_losses,
                max_drawdown=0.0,
                max_drawdown_pct=0.0,
                expectancy=strat_expectancy,
            )
            self.db.add(snap)
            strategy_snapshots.append(snap)

        self.db.commit()
        logger.info("Generated daily snapshot for %s: %d strategies", snapshot_date, len(strategy_snapshots) + 1)

        return [portfolio_snap] + strategy_snapshots

    # ------------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------------

    @staticmethod
    def _calculate_sharpe(daily_returns: List[float], risk_free_rate: float = 0.0) -> Optional[float]:
        """Calculate annualized Sharpe ratio from daily returns."""
        if len(daily_returns) < 2:
            return None

        import numpy as np
        returns = np.array(daily_returns)
        excess_returns = returns - risk_free_rate / 252  # Daily risk-free

        if np.std(excess_returns) == 0:
            return None

        sharpe = np.mean(excess_returns) / np.std(excess_returns) * np.sqrt(252)
        return round(float(sharpe), 3)


def get_performance_analytics(db: Session, user_id: str) -> PerformanceAnalytics:
    """Factory function to create PerformanceAnalytics instance."""
    return PerformanceAnalytics(db, user_id)