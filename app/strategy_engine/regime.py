"""
Market Regime Detection Engine.

Classifies the current market regime per instrument/index using technical indicators:
- ADX for trend strength
- ATR / Bollinger Bandwidth for volatility
- EMA slope / crossover for direction

Outputs a RegimeResult consumed by the strategy engine and dashboard.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, List

import numpy as np
import pandas as pd

from app.schemas import RegimeResult, RegimeType
from app.strategy_engine.base import BaseStrategy

logger = logging.getLogger(__name__)

# IST timezone
IST = timezone(timedelta(hours=5, minutes=30))


class RegimeDetector:
    """Detects market regime from OHLCV data."""

    def __init__(
        self,
        adx_period: int = 14,
        adx_trend_threshold: float = 25.0,
        atr_period: int = 14,
        bb_period: int = 20,
        bb_std: float = 2.0,
        ema_fast: int = 20,
        ema_slow: int = 50,
        volatility_lookback: int = 20,
        high_vol_percentile: float = 75.0,
    ):
        """Initialize the regime detector with parameters.

        Args:
            adx_period: Period for ADX calculation.
            adx_trend_threshold: ADX value above which trend is considered strong.
            atr_period: Period for ATR calculation.
            bb_period: Period for Bollinger Bands.
            bb_std: Standard deviations for Bollinger Bands.
            ema_fast: Fast EMA period for trend direction.
            ema_slow: Slow EMA period for trend direction.
            volatility_lookback: Lookback for volatility percentile calculation.
            high_vol_percentile: Percentile threshold for high volatility regime.
        """
        self.adx_period = adx_period
        self.adx_trend_threshold = adx_trend_threshold
        self.atr_period = atr_period
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.volatility_lookback = volatility_lookback
        self.high_vol_percentile = high_vol_percentile

        # Cache for volatility percentiles
        self._volatility_history: Dict[str, List[float]] = {}

    def detect(self, symbol: str, exchange: str, candles: pd.DataFrame) -> RegimeResult:
        """Detect the current market regime for a symbol.

        Args:
            symbol: Trading symbol.
            exchange: Exchange segment.
            candles: DataFrame with OHLCV data (must have columns: open, high, low, close, volume, timestamp).

        Returns:
            RegimeResult with regime classification and indicator values.
        """
        if len(candles) < max(self.adx_period, self.ema_slow, self.bb_period) + 5:
            logger.warning(
                "Insufficient data for regime detection: %s (%d candles)", symbol, len(candles)
            )
            return self._default_regime(symbol, exchange)

        # Ensure required columns
        df = candles.copy()
        if 'timestamp' in df.columns:
            df = df.set_index('timestamp')

        # Calculate indicators
        adx = self._calculate_adx(df)
        atr = self._calculate_atr(df)
        bb_bandwidth = self._calculate_bb_bandwidth(df)
        ema_slope = self._calculate_ema_slope(df)

        # Get latest values
        latest_adx = adx.iloc[-1] if not adx.empty else np.nan
        latest_atr = atr.iloc[-1] if not atr.empty else np.nan
        latest_bb_bw = bb_bandwidth.iloc[-1] if not bb_bandwidth.empty else np.nan
        latest_ema_slope = ema_slope.iloc[-1] if not ema_slope.empty else np.nan

        # Update volatility history for percentile calculation
        self._update_volatility_history(symbol, atr)

        # Classify regime
        regime, confidence = self._classify_regime(
            latest_adx, latest_bb_bw, latest_ema_slope, symbol
        )

        # Build metadata
        metadata = {
            "adx": round(float(latest_adx), 2) if not np.isnan(latest_adx) else None,
            "atr": round(float(latest_atr), 2) if not np.isnan(latest_atr) else None,
            "bb_bandwidth": round(float(latest_bb_bw), 4) if not np.isnan(latest_bb_bw) else None,
            "ema_slope": round(float(latest_ema_slope), 6) if not np.isnan(latest_ema_slope) else None,
            "volatility_percentile": self._get_volatility_percentile(symbol),
        }

        return RegimeResult(
            symbol=symbol,
            exchange=exchange,
            regime=regime,
            adx=metadata["adx"],
            atr=metadata["atr"],
            bb_bandwidth=metadata["bb_bandwidth"],
            ema_slope=metadata["ema_slope"],
            confidence=confidence,
            metadata=metadata,
            timestamp=datetime.now(IST),
        )

    def _calculate_adx(self, df: pd.DataFrame) -> pd.Series:
        """Calculate ADX using base strategy helper."""
        return BaseStrategy.adx(
            df['high'], df['low'], df['close'], self.adx_period
        )

    def _calculate_atr(self, df: pd.DataFrame) -> pd.Series:
        """Calculate ATR."""
        return BaseStrategy.atr(
            df['high'], df['low'], df['close'], self.atr_period
        )

    def _calculate_bb_bandwidth(self, df: pd.DataFrame) -> pd.Series:
        """Calculate Bollinger Band bandwidth (normalized width)."""
        upper, middle, lower = BaseStrategy.bollinger_bands(
            df['close'], self.bb_period, self.bb_std
        )
        # Bandwidth = (upper - lower) / middle
        bandwidth = (upper - lower) / middle
        return bandwidth

    def _calculate_ema_slope(self, df: pd.DataFrame) -> pd.Series:
        """Calculate EMA slope (rate of change)."""
        ema_fast = BaseStrategy.ema(df['close'], self.ema_fast)
        ema_slow = BaseStrategy.ema(df['close'], self.ema_slow)

        # Slope = (current - previous) / previous
        fast_slope = ema_fast.pct_change()
        slow_slope = ema_slow.pct_change()

        # Combined slope (weighted)
        combined_slope = (fast_slope * 0.6 + slow_slope * 0.4)
        return combined_slope

    def _classify_regime(
        self,
        adx: float,
        bb_bandwidth: float,
        ema_slope: float,
        symbol: str,
    ) -> tuple[RegimeType, float]:
        """Classify regime based on indicator values.

        Logic:
        1. High volatility: BB bandwidth > percentile threshold OR ATR spike
        2. Trending: ADX > threshold AND ema_slope confirms direction
        3. Range-bound: ADX < threshold AND not high volatility
        """
        # Check for high volatility first
        vol_percentile = self._get_volatility_percentile(symbol)
        is_high_vol = vol_percentile >= self.high_vol_percentile

        # Check for trend
        is_trending = adx >= self.adx_trend_threshold

        if is_high_vol:
            # In high vol, check if there's also a trend
            if is_trending:
                if ema_slope > 0:
                    return RegimeType.TRENDING_UP, 0.75
                else:
                    return RegimeType.TRENDING_DOWN, 0.75
            else:
                return RegimeType.HIGH_VOLATILITY, 0.8

        if is_trending:
            if ema_slope > 0:
                return RegimeType.TRENDING_UP, min(0.9, 0.5 + (adx - self.adx_trend_threshold) / 50)
            else:
                return RegimeType.TRENDING_DOWN, min(0.9, 0.5 + (adx - self.adx_trend_threshold) / 50)

        # Default to range-bound
        return RegimeType.RANGE_BOUND, 0.6

    def _update_volatility_history(self, symbol: str, atr_series: pd.Series):
        """Update rolling volatility history for percentile calculation."""
        valid_atr = atr_series.dropna().tolist()
        if valid_atr:
            self._volatility_history[symbol] = [float(x) for x in valid_atr[-self.volatility_lookback:]]

    def _get_volatility_percentile(self, symbol: str) -> float:
        """Calculate current volatility percentile."""
        history = self._volatility_history.get(symbol, [])
        if len(history) < 5:
            return 50.0  # Default to median

        current = history[-1]
        percentile = (np.sum(np.array(history) <= current) / len(history)) * 100
        return round(percentile, 1)

    def _default_regime(self, symbol: str, exchange: str) -> RegimeResult:
        """Return default regime when insufficient data."""
        return RegimeResult(
            symbol=symbol,
            exchange=exchange,
            regime=RegimeType.RANGE_BOUND,
            adx=None,
            atr=None,
            bb_bandwidth=None,
            ema_slope=None,
            confidence=0.3,
            metadata={"reason": "insufficient_data"},
            timestamp=datetime.now(IST),
        )

    def detect_batch(
        self,
        symbols_data: Dict[str, pd.DataFrame],
        exchange: str = "NSE",
    ) -> Dict[str, RegimeResult]:
        """Detect regime for multiple symbols at once.

        Args:
            symbols_data: Dict mapping symbol to OHLCV DataFrame.
            exchange: Exchange segment.

        Returns:
            Dict mapping symbol to RegimeResult.
        """
        results = {}
        for symbol, candles in symbols_data.items():
            try:
                results[symbol] = self.detect(symbol, exchange, candles)
            except Exception as e:
                logger.error("Regime detection failed for %s: %s", symbol, e)
                results[symbol] = self._default_regime(symbol, exchange)
        return results


# Global detector instance
_detector: Optional[RegimeDetector] = None


def get_regime_detector() -> RegimeDetector:
    """Get or create the global regime detector instance."""
    global _detector
    if _detector is None:
        _detector = RegimeDetector()
    return _detector