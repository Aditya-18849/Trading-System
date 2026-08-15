"""
Tests for Market Regime Detection.
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

from app.strategy_engine.regime import RegimeDetector, RegimeType


IST = timezone(timedelta(hours=5, minutes=30))


def create_sample_candles(trend: str = "up", length: int = 100) -> pd.DataFrame:
    """Create synthetic OHLCV candles for testing."""
    np.random.seed(42)
    dates = pd.date_range(
        start=datetime.now(IST) - timedelta(days=length),
        periods=length,
        freq="5min",
        tz=IST
    )

    base_price = 2500.0
    if trend == "up":
        drift = np.linspace(0, 50, length)  # 50 point uptrend
    elif trend == "down":
        drift = np.linspace(0, -50, length)
    elif trend == "volatile":
        drift = np.random.randn(length).cumsum() * 2
    else:  # range
        drift = np.sin(np.linspace(0, 4*np.pi, length)) * 20

    noise = np.random.randn(length) * 5
    close = base_price + drift + noise
    high = close + np.abs(np.random.randn(length) * 3)
    low = close - np.abs(np.random.randn(length) * 3)
    open_ = close + np.random.randn(length) * 2
    volume = np.random.randint(100000, 1000000, length)

    df = pd.DataFrame({
        "timestamp": dates,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })
    return df


class TestRegimeDetector:
    """Tests for RegimeDetector class."""

    def test_detect_trending_up(self):
        """Test detection of trending up regime."""
        detector = RegimeDetector()
        candles = create_sample_candles("up", 200)

        result = detector.detect("RELIANCE", "NSE", candles)

        assert result.symbol == "RELIANCE"
        assert result.exchange == "NSE"
        assert result.regime in [RegimeType.TRENDING_UP, RegimeType.HIGH_VOLATILITY]
        assert result.confidence > 0.5
        assert result.adx is not None
        assert result.atr is not None

    def test_detect_trending_down(self):
        """Test detection of trending down regime."""
        detector = RegimeDetector()
        candles = create_sample_candles("down", 200)

        result = detector.detect("RELIANCE", "NSE", candles)

        assert result.regime in [RegimeType.TRENDING_DOWN, RegimeType.HIGH_VOLATILITY]
        assert result.confidence > 0.5

    def test_detect_range_bound(self):
        """Test detection of range-bound regime."""
        detector = RegimeDetector()
        candles = create_sample_candles("range", 200)

        result = detector.detect("RELIANCE", "NSE", candles)

        # Range-bound should have low ADX
        assert result.adx is not None
        assert result.regime in [RegimeType.RANGE_BOUND, RegimeType.HIGH_VOLATILITY]

    def test_detect_high_volatility(self):
        """Test detection of high volatility regime."""
        detector = RegimeDetector()
        candles = create_sample_candles("volatile", 200)

        result = detector.detect("RELIANCE", "NSE", candles)

        assert result.regime == RegimeType.HIGH_VOLATILITY
        assert result.confidence > 0.7

    def test_insufficient_data_returns_default(self):
        """Test that insufficient data returns default regime."""
        detector = RegimeDetector()
        candles = create_sample_candles("up", 10)  # Too few candles

        result = detector.detect("RELIANCE", "NSE", candles)

        assert result.regime == RegimeType.RANGE_BOUND
        assert result.confidence == 0.3
        assert result.metadata.get("reason") == "insufficient_data"

    def test_detect_batch(self):
        """Test batch detection for multiple symbols."""
        detector = RegimeDetector()
        candles_dict = {
            "RELIANCE": create_sample_candles("up", 200),
            "TCS": create_sample_candles("down", 200),
            "INFY": create_sample_candles("range", 200),
        }

        results = detector.detect_batch(candles_dict, "NSE")

        assert len(results) == 3
        assert "RELIANCE" in results
        assert "TCS" in results
        assert "INFY" in results
        for symbol, result in results.items():
            assert result.symbol == symbol
            assert result.exchange == "NSE"

    def test_adx_calculation(self):
        """Test ADX calculation internally."""
        detector = RegimeDetector()
        candles = create_sample_candles("up", 200)

        adx = detector._calculate_adx(candles)

        assert len(adx) == len(candles)
        assert not adx.iloc[-1] != adx.iloc[-1]  # Not NaN
        assert adx.iloc[-1] > 0

    def test_atr_calculation(self):
        """Test ATR calculation."""
        detector = RegimeDetector()
        candles = create_sample_candles("up", 200)

        atr = detector._calculate_atr(candles)

        assert len(atr) == len(candles)
        assert not atr.iloc[-1] != atr.iloc[-1]
        assert atr.iloc[-1] > 0

    def test_bb_bandwidth_calculation(self):
        """Test Bollinger Band bandwidth calculation."""
        detector = RegimeDetector()
        candles = create_sample_candles("range", 200)

        bw = detector._calculate_bb_bandwidth(candles)

        assert len(bw) == len(candles)
        assert not bw.iloc[-1] != bw.iloc[-1]
        assert bw.iloc[-1] > 0

    def test_ema_slope_calculation(self):
        """Test EMA slope calculation."""
        detector = RegimeDetector()
        candles = create_sample_candles("up", 200)

        slope = detector._calculate_ema_slope(candles)

        assert len(slope) == len(candles)
        # Uptrend should have positive slope
        assert slope.iloc[-1] > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])