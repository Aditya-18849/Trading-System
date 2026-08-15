"""
Strategy Engine Base — Abstract Base Class for All Strategies.

Defines the common interface that all strategy implementations must follow.
Each strategy evaluates market data and regime to produce a StrategySignal.
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any

import pandas as pd
from pydantic import BaseModel, Field

from app.schemas import StrategySignal, RegimeType

logger = logging.getLogger(__name__)

# IST timezone
IST = timezone(timedelta(hours=5, minutes=30))


class StrategyConfig(BaseModel):
    """Base configuration for a strategy.

    Extend this in concrete strategies for strategy-specific parameters.
    """
    name: str
    enabled: bool = True
    # Regimes this strategy is suitable for
    suitable_regimes: List[RegimeType] = Field(
        default_factory=lambda: [
            RegimeType.TRENDING_UP,
            RegimeType.TRENDING_DOWN,
            RegimeType.RANGE_BOUND,
            RegimeType.HIGH_VOLATILITY,
        ]
    )
    # Minimum confidence to emit a signal
    min_confidence: float = 0.6
    # Maximum signals per day per symbol
    max_signals_per_day: int = 3
    # Additional strategy-specific params
    params: Dict[str, Any] = Field(default_factory=dict)


class StrategyContext(BaseModel):
    """Context passed to strategy evaluate() method.

    Contains all data a strategy needs to make a decision.
    """
    symbol: str
    exchange: str = "NSE"
    candles: pd.DataFrame  # OHLCV data with timestamp index
    regime: RegimeType
    regime_confidence: float
    regime_metadata: Dict[str, Any] = Field(default_factory=dict)
    current_price: float
    timestamp: datetime = Field(default_factory=lambda: datetime.now(IST))


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies.

    All strategies must implement the evaluate() method.
    The base class provides common utilities like indicator calculations.
    """

    def __init__(self, config: StrategyConfig):
        """Initialize the strategy with configuration.

        Args:
            config: StrategyConfig instance with parameters.
        """
        self.config = config
        self.name = config.name
        self._signal_count_today: Dict[str, int] = {}  # symbol -> count
        logger.info("Strategy initialized: %s", self.name)

    @abstractmethod
    def evaluate(self, context: StrategyContext) -> Optional[StrategySignal]:
        """Evaluate the strategy against current market data.

        This is the main entry point called by the strategy engine.
        Strategies should:
        1. Check if the current regime is suitable
        2. Calculate indicators from candles
        3. Generate a signal if conditions are met
        4. Return None if no signal (HOLD)

        Args:
            context: StrategyContext with market data and regime info.

        Returns:
            StrategySignal if a trade setup is detected, None otherwise.
        """
        pass

    def is_suitable_regime(self, regime: RegimeType) -> bool:
        """Check if the current regime is suitable for this strategy."""
        return regime in self.config.suitable_regimes

    def can_emit_signal(self, symbol: str) -> bool:
        """Check if we can emit another signal for this symbol today."""
        count = self._signal_count_today.get(symbol, 0)
        return count < self.config.max_signals_per_day

    def increment_signal_count(self, symbol: str):
        """Increment the signal counter for a symbol."""
        self._signal_count_today[symbol] = self._signal_count_today.get(symbol, 0) + 1

    def reset_daily_counters(self):
        """Reset daily signal counters (call at market open)."""
        self._signal_count_today.clear()

    # ------------------------------------------------------------------------
    # Common Indicator Helpers (can be used by concrete strategies)
    # ------------------------------------------------------------------------

    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        """Exponential Moving Average."""
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def sma(series: pd.Series, period: int) -> pd.Series:
        """Simple Moving Average."""
        return series.rolling(window=period).mean()

    @staticmethod
    def rsi(close: pd.Series, period: int = 14) -> pd.Series:
        """Relative Strength Index (Wilder's smoothing)."""
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)

        avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

        rs = avg_gain / avg_loss.replace(0, float('nan'))
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def macd(
        close: pd.Series,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """MACD line, signal line, histogram."""
        macd_line = BaseStrategy.ema(close, fast) - BaseStrategy.ema(close, slow)
        signal_line = BaseStrategy.ema(macd_line, signal)
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def bollinger_bands(
        close: pd.Series,
        period: int = 20,
        num_std: float = 2.0,
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """Bollinger Bands: upper, middle, lower."""
        middle = BaseStrategy.sma(close, period)
        std = close.rolling(window=period).std()
        upper = middle + num_std * std
        lower = middle - num_std * std
        return upper, middle, lower

    @staticmethod
    def atr(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        period: int = 14,
    ) -> pd.Series:
        """Average True Range (Wilder's smoothed)."""
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    @staticmethod
    def supertrend(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        period: int = 10,
        multiplier: float = 3.0,
    ) -> tuple[pd.Series, pd.Series]:
        """Supertrend indicator.

        Returns:
            (supertrend_line, direction) where direction: +1=bullish, -1=bearish
        """
        atr_vals = BaseStrategy.atr(high, low, close, period)
        hl2 = (high + low) / 2.0

        upper_band = hl2 + multiplier * atr_vals
        lower_band = hl2 - multiplier * atr_vals

        n = len(close)
        supertrend = pd.Series(index=close.index, dtype=float)
        direction = pd.Series(index=close.index, dtype=float)

        supertrend[:] = float('nan')
        direction[:] = float('nan')

        start = period
        if start >= n:
            return supertrend, direction

        supertrend.iloc[start] = upper_band.iloc[start]
        direction.iloc[start] = -1

        for i in range(start + 1, n):
            # Adjust bands
            if lower_band.iloc[i] > lower_band.iloc[i - 1] or close.iloc[i - 1] < lower_band.iloc[i - 1]:
                pass
            else:
                lower_band.iloc[i] = lower_band.iloc[i - 1]

            if upper_band.iloc[i] < upper_band.iloc[i - 1] or close.iloc[i - 1] > upper_band.iloc[i - 1]:
                pass
            else:
                upper_band.iloc[i] = upper_band.iloc[i - 1]

            # Determine direction
            if direction.iloc[i - 1] == -1:
                if close.iloc[i] > upper_band.iloc[i]:
                    direction.iloc[i] = 1
                    supertrend.iloc[i] = lower_band.iloc[i]
                else:
                    direction.iloc[i] = -1
                    supertrend.iloc[i] = upper_band.iloc[i]
            else:
                if close.iloc[i] < lower_band.iloc[i]:
                    direction.iloc[i] = -1
                    supertrend.iloc[i] = upper_band.iloc[i]
                else:
                    direction.iloc[i] = 1
                    supertrend.iloc[i] = lower_band.iloc[i]

        return supertrend, direction

    @staticmethod
    def adx(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        period: int = 14,
    ) -> pd.Series:
        """Average Directional Index."""
        plus_dm = high.diff()
        minus_dm = low.diff()

        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm > 0] = 0
        minus_dm = minus_dm.abs()

        tr = BaseStrategy.atr(high, low, close, period)

        plus_di = 100 * (plus_dm.ewm(alpha=1/period, adjust=False).mean() / tr)
        minus_di = 100 * (minus_dm.ewm(alpha=1/period, adjust=False).mean() / tr)

        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
        adx = dx.ewm(alpha=1/period, adjust=False).mean()

        return adx

    @staticmethod
    def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
        """Volume Weighted Average Price (session VWAP)."""
        typical_price = (high + low + close) / 3
        return (typical_price * volume).cumsum() / volume.cumsum()

    @staticmethod
    def donchian_channel(
        high: pd.Series,
        low: pd.Series,
        period: int = 20,
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """Donchian Channel: upper, middle, lower."""
        upper = high.rolling(window=period).max()
        lower = low.rolling(window=period).min()
        middle = (upper + lower) / 2
        return upper, middle, lower

    @staticmethod
    def keltner_channel(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        period: int = 20,
        multiplier: float = 2.0,
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """Keltner Channel: upper, middle (EMA), lower."""
        middle = BaseStrategy.ema(close, period)
        atr_val = BaseStrategy.atr(high, low, close, period)
        upper = middle + multiplier * atr_val
        lower = middle - multiplier * atr_val
        return upper, middle, lower

    @staticmethod
    def stochastic_rsi(
        close: pd.Series,
        period: int = 14,
        smooth_k: int = 3,
        smooth_d: int = 3,
    ) -> tuple[pd.Series, pd.Series]:
        """Stochastic RSI: %K, %D."""
        rsi_vals = BaseStrategy.rsi(close, period)
        min_rsi = rsi_vals.rolling(window=period).min()
        max_rsi = rsi_vals.rolling(window=period).max()

        stoch_rsi = (rsi_vals - min_rsi) / (max_rsi - min_rsi)
        k = stoch_rsi.rolling(window=smooth_k).mean() * 100
        d = k.rolling(window=smooth_d).mean()

        return k, d