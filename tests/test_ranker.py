"""
Tests for Signal Ranker.
"""

import pytest
from datetime import date, datetime, timezone, timedelta
from unittest.mock import Mock, MagicMock
from uuid import uuid4

from app.strategy_engine.ranker import SignalRanker
from app.schemas import StrategySignal, RankedSignal, RegimeType
from app.models import PerformanceSnapshot


IST = timezone(timedelta(hours=5, minutes=30))


def create_mock_db():
    """Create a mock database session."""
    return Mock()


def create_sample_signal(
    strategy_name: str = "EMA_CROSSOVER",
    symbol: str = "RELIANCE",
    action: str = "BUY",
    confidence: float = 0.7,
    regime: str = "trending-up",
) -> StrategySignal:
    """Create a sample StrategySignal for testing."""
    return StrategySignal(
        symbol=symbol,
        exchange="NSE",
        action=action,
        confidence_score=confidence,
        entry_price=2500.0,
        stoploss_price=2475.0,
        target_price=2550.0,
        suggested_quantity=10,
        strategy_name=strategy_name,
        regime_at_signal=regime,
        metadata={"adx": 30, "atr": 25},
        timestamp=datetime.now(IST),
    )


class TestSignalRanker:
    """Tests for SignalRanker class."""

    def setup_method(self):
        """Setup test fixtures."""
        self.db = create_mock_db()
        self.user_id = str(uuid4())
        self.ranker = SignalRanker(self.db, self.user_id)

    def test_rank_empty_signals(self):
        """Test ranking empty signal list."""
        result = self.ranker.rank_signals([])
        assert result == []

    def test_rank_single_signal(self):
        """Test ranking a single signal."""
        signal = create_sample_signal()
        result = self.ranker.rank_signals([signal])

        assert len(result) == 1
        assert isinstance(result[0], RankedSignal)
        assert result[0].signal == signal
        assert result[0].rank_score > 0

    def test_rank_multiple_signals_sorts_descending(self):
        """Test that signals are sorted by rank score descending."""
        signals = [
            create_sample_signal(strategy_name="LOW_CONF", confidence=0.5),
            create_sample_signal(strategy_name="HIGH_CONF", confidence=0.9),
            create_sample_signal(strategy_name="MED_CONF", confidence=0.7),
        ]

        result = self.ranker.rank_signals(signals)

        assert len(result) == 3
        assert result[0].signal.strategy_name == "HIGH_CONF"
        assert result[1].signal.strategy_name == "MED_CONF"
        assert result[2].signal.strategy_name == "LOW_CONF"
        assert result[0].rank_score >= result[1].rank_score >= result[2].rank_score

    def test_historical_performance_loading(self):
        """Test loading historical performance from database."""
        # Mock PerformanceSnapshot data
        mock_perf = [
            Mock(
                strategy_name="EMA_CROSSOVER",
                trades_count=10,
                wins=7,
                losses=3,
                total_pnl=5000,
                win_rate=70.0,
                expectancy=250.0,
            ),
            Mock(
                strategy_name="RSI_MEAN_REVERSION",
                trades_count=5,
                wins=2,
                losses=3,
                total_pnl=-1000,
                win_rate=40.0,
                expectancy=-100.0,
            ),
        ]

        # Configure mock query chain
        mock_query = self.db.query.return_value
        mock_filter = mock_query.filter.return_value
        mock_filter.all.return_value = mock_perf

        # Reload cache
        self.ranker._load_performance_cache(["EMA_CROSSOVER", "RSI_MEAN_REVERSION"])

        assert "EMA_CROSSOVER" in self.ranker._perf_cache
        assert "RSI_MEAN_REVERSION" in self.ranker._perf_cache

        ema_perf = self.ranker._perf_cache["EMA_CROSSOVER"]
        assert ema_perf["trades"] == 10
        assert ema_perf["win_rate"] == 0.7  # Converted to 0-1
        assert ema_perf["expectancy"] == 250.0

    def test_insufficient_sample_size_neutral(self):
        """Test that strategies with <5 trades get neutral performance."""
        mock_perf = [
            Mock(
                strategy_name="NEW_STRATEGY",
                trades_count=3,
                wins=2,
                losses=1,
                total_pnl=1000,
                win_rate=66.7,
                expectancy=200.0,
            ),
        ]

        mock_query = self.db.query.return_value
        mock_filter = mock_query.filter.return_value
        mock_filter.all.return_value = mock_perf

        self.ranker._load_performance_cache(["NEW_STRATEGY"])

        perf = self.ranker._perf_cache["NEW_STRATEGY"]
        # Should be neutral (0.5) due to insufficient sample
        assert perf["win_rate"] == 0.5

    def test_regime_fit_perfect_match(self):
        """Test regime fit score for perfect match."""
        signal = create_sample_signal(regime="trending-up")

        # Mock strategy with suitable regimes
        mock_strategy = Mock()
        mock_strategy.config.suitable_regimes = [
            RegimeType.TRENDING_UP,
            RegimeType.TRENDING_DOWN,
        ]

        # Patch get_strategy_class
        import app.strategy_engine.ranker as ranker_module
        original = ranker_module.get_strategy_class
        ranker_module.get_strategy_class = Mock(return_value=lambda: mock_strategy)

        try:
            fit = self.ranker._calculate_regime_fit(signal)
            assert fit == 1.0
        finally:
            ranker_module.get_strategy_class = original

    def test_regime_fit_adjacent(self):
        """Test regime fit score for adjacent regime."""
        signal = create_sample_signal(regime="high-volatility")

        mock_strategy = Mock()
        mock_strategy.config.suitable_regimes = [
            RegimeType.TRENDING_UP,
            RegimeType.TRENDING_DOWN,
        ]

        import app.strategy_engine.ranker as ranker_module
        original = ranker_module.get_strategy_class
        ranker_module.get_strategy_class = Mock(return_value=lambda: mock_strategy)

        try:
            fit = self.ranker._calculate_regime_fit(signal)
            assert fit == 0.5  # Adjacent
        finally:
            ranker_module.get_strategy_class = original

    def test_regime_fit_poor(self):
        """Test regime fit score for poor match."""
        signal = create_sample_signal(regime="range-bound")

        mock_strategy = Mock()
        mock_strategy.config.suitable_regimes = [
            RegimeType.TRENDING_UP,
            RegimeType.TRENDING_DOWN,
        ]

        import app.strategy_engine.ranker as ranker_module
        original = ranker_module.get_strategy_class
        ranker_module.get_strategy_class = Mock(return_value=lambda: mock_strategy)

        try:
            fit = self.ranker._calculate_regime_fit(signal)
            assert fit == 0.2  # Poor fit
        finally:
            ranker_module.get_strategy_class = original

    def test_rank_score_weights(self):
        """Test that rank score respects configured weights."""
        # Create ranker with custom weights
        ranker = SignalRanker(
            self.db,
            self.user_id,
            weight_confidence=0.8,
            weight_historical_perf=0.1,
            weight_regime_fit=0.1,
        )

        signal = create_sample_signal(confidence=0.9)
        ranker._perf_cache = {"EMA_CROSSOVER": {"win_rate": 0.5, "sample_size": 10}}

        rank_score, hist_perf, regime_fit = ranker._calculate_rank_score(signal)

        # With high confidence weight, score should be close to confidence
        assert rank_score > 0.8


if __name__ == "__main__":
    pytest.main([__file__, "-v"])