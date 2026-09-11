"""
Signal Ranker — Scores and ranks strategy signals.

Combines confidence, historical performance, and regime fit into a single rank score.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from app.models import StrategySignal as StrategySignalModel, PerformanceSnapshot
from app.schemas import StrategySignal, RankedSignal, RegimeType
from app.strategy_engine.base import BaseStrategy
from app.strategy_engine.strategies import get_strategy_class

logger = logging.getLogger(__name__)

# IST timezone
IST = timezone(timedelta(hours=5, minutes=30))


class SignalRanker:
    """Ranks strategy signals using multiple factors."""

    def __init__(
        self,
        db: Session,
        user_id: str,
        # Weights for ranking components (must sum to 1.0)
        weight_confidence: float = 0.4,
        weight_historical_perf: float = 0.35,
        weight_regime_fit: float = 0.25,
    ):
        """Initialize the ranker.

        Args:
            db: Database session.
            user_id: User ID for fetching historical performance.
            weight_confidence: Weight for strategy confidence score.
            weight_historical_perf: Weight for historical win rate/expectancy.
            weight_regime_fit: Weight for regime suitability.
        """
        self.db = db
        self.user_id = user_id
        self.weight_confidence = weight_confidence
        self.weight_historical_perf = weight_historical_perf
        self.weight_regime_fit = weight_regime_fit

        # Cache for historical performance
        self._perf_cache: Dict[str, Dict] = {}

    def rank_signals(self, signals: List[StrategySignal]) -> List[RankedSignal]:
        """Rank a list of signals from best to worst.

        Args:
            signals: List of StrategySignal objects.

        Returns:
            List of RankedSignal sorted by rank_score (descending).
        """
        if not signals:
            return []

        # Load historical performance for all strategies in signals
        strategy_names = list(set(s.strategy_name for s in signals))
        self._load_performance_cache(strategy_names)

        ranked = []
        for signal in signals:
            rank_score, hist_perf, regime_fit = self._calculate_rank_score(signal)
            ranked.append(RankedSignal(
                signal=signal,
                rank_score=rank_score,
                historical_performance=hist_perf,
                regime_fit=regime_fit,
            ))

        # Sort by rank score descending
        ranked.sort(key=lambda x: x.rank_score, reverse=True)

        logger.info(
            "Ranked %d signals for user %s. Top: %s (%.3f)",
            len(ranked), self.user_id,
            ranked[0].signal.strategy_name if ranked else "none",
            ranked[0].rank_score if ranked else 0
        )

        return ranked

    def _load_performance_cache(self, strategy_names: List[str]):
        """Load recent performance metrics for strategies."""
        # Get last 30 days of performance
        cutoff_date = datetime.now(IST).date() - timedelta(days=30)

        for name in strategy_names:
            try:
                perf = self.db.query(PerformanceSnapshot).filter(
                    PerformanceSnapshot.user_id == self.user_id,
                    PerformanceSnapshot.strategy_name == name,
                    PerformanceSnapshot.date >= cutoff_date,
                ).all()
            except Exception:
                perf = None

            if perf and isinstance(perf, (list, tuple)):
                matching = [p for p in perf if getattr(p, "strategy_name", name) == name]
                if matching:
                    total_trades = sum(getattr(p, "trades_count", 0) for p in matching)
                    total_wins = sum(getattr(p, "wins", 0) for p in matching)
                    total_pnl = sum(float(getattr(p, "total_pnl", 0)) for p in matching)
                    avg_win_rate = sum(float(getattr(p, "win_rate", 0)) for p in matching) / len(matching) if matching else 0
                    avg_expectancy = sum(float(getattr(p, "expectancy", 0) or 0) for p in matching) / len(matching) if matching else 0

                    win_rate = 0.5 if total_trades < 5 else avg_win_rate / 100

                    self._perf_cache[name] = {
                        "trades": total_trades,
                        "win_rate": win_rate,
                        "total_pnl": total_pnl,
                        "expectancy": avg_expectancy,
                        "sample_size": len(matching),
                    }
                    continue

            self._perf_cache[name] = {
                "trades": 0,
                "win_rate": 0.5,  # Neutral
                "total_pnl": 0,
                "expectancy": 0,
                "sample_size": 0,
            }

    def _calculate_rank_score(
        self,
        signal: StrategySignal
    ) -> Tuple[float, Optional[float], Optional[float]]:
        """Calculate composite rank score for a signal.

        Returns:
            Tuple of (rank_score, historical_performance, regime_fit)
        """
        # 1. Confidence component (0-1)
        confidence_score = signal.confidence_score

        # 2. Historical performance component
        perf = self._perf_cache.get(signal.strategy_name, {})
        hist_perf = perf.get("win_rate", 0.5)

        # Adjust for sample size (less reliable with few trades)
        sample_size = perf.get("sample_size", 0)
        if sample_size < 5:
            hist_perf = 0.5  # Neutral for insufficient data
        elif sample_size < 20:
            # Blend with neutral
            hist_perf = hist_perf * 0.7 + 0.5 * 0.3

        # 3. Regime fit component
        regime_fit = self._calculate_regime_fit(signal)

        # Weighted composite
        rank_score = (
            self.weight_confidence * confidence_score +
            self.weight_historical_perf * hist_perf +
            self.weight_regime_fit * regime_fit
        )

        return round(rank_score, 4), round(hist_perf, 4), round(regime_fit, 4)

    def _calculate_regime_fit(self, signal: StrategySignal) -> float:
        """Calculate how well the signal fits the current regime.

        Returns a score 0-1 where 1 = perfect fit.
        """
        # Get the strategy class to check suitable regimes
        try:
            strategy_class = get_strategy_class(signal.strategy_name)
            # Create temporary instance to check regimes
            temp_strategy = strategy_class()
            suitable = temp_strategy.config.suitable_regimes
        except Exception:
            # Default: all regimes suitable
            suitable = list(RegimeType)

        current_regime = signal.regime_at_signal
        if not current_regime:
            return 0.5

        # Check if current regime is in suitable list
        suitable_values = [(r.value if hasattr(r, "value") else str(r)) for r in suitable]
        if current_regime in suitable_values:
            return 1.0

        # Partial score for adjacent regimes
        regime_adjacency = {
            RegimeType.TRENDING_UP.value: [RegimeType.HIGH_VOLATILITY.value],
            RegimeType.TRENDING_DOWN.value: [RegimeType.HIGH_VOLATILITY.value],
            RegimeType.RANGE_BOUND.value: [RegimeType.HIGH_VOLATILITY.value],
            RegimeType.HIGH_VOLATILITY.value: [
                RegimeType.TRENDING_UP.value,
                RegimeType.TRENDING_DOWN.value,
                RegimeType.RANGE_BOUND.value,
            ],
        }

        adjacent = regime_adjacency.get(current_regime, [])
        if any(v in adjacent for v in suitable_values):
            return 0.5

        return 0.2  # Poor fit


def get_ranker(db: Session, user_id: str) -> SignalRanker:
    """Factory function to create a SignalRanker instance."""
    return SignalRanker(db, user_id)