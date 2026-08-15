"""MACD Momentum Strategy - Momentum.

Trades MACD histogram flips with signal line confirmation.
BUY when MACD histogram turns positive and MACD > signal,
SELL when MACD histogram turns negative and MACD < signal.
Best suited for trending regimes.
"""

import logging
from typing import Optional

import pandas as pd

from app.schemas import StrategySignal, RegimeType
from app.strategy_engine.base import BaseStrategy, StrategyConfig, StrategyContext

logger = logging.getLogger(__name__)


class MACDMomentumConfig(StrategyConfig):
    """Configuration for MACD Momentum strategy."""
    name: str = "MACD_MOMENTUM"
    fast_period: int = 12
    slow_period: int = 26
    signal_period: int = 9
    histogram_threshold: float = 0.0  # Minimum histogram magnitude
    suitable_regimes: list = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
    ]
    min_confidence: float = 0.6


class MACDMomentumStrategy(BaseStrategy):
    """MACD Momentum strategy.

    - BUY: MACD histogram flips positive AND MACD > Signal line
    - SELL: MACD histogram flips negative AND MACD < Signal line
    """

    def __init__(self, config: Optional[MACDMomentumConfig] = None):
        config = config or MACDMomentumConfig()
        super().__init__(config)
        self.fast_period = config.fast_period
        self.slow_period = config.slow_period
        self.signal_period = config.signal_period
        self.histogram_threshold = config.histogram_threshold

    def evaluate(self, context: StrategyContext) -> Optional[StrategySignal]:
        """Evaluate MACD momentum conditions."""
        # Check regime suitability
        if not self.is_suitable_regime(context.regime):
            return None

        # Check daily signal limit
        if not self.can_emit_signal(context.symbol):
            return None

        df = context.candles
        if len(df) < self.slow_period + self.signal_period + 5:
            return None

        # Calculate MACD
        macd_line, signal_line, histogram = self.macd(
            df['close'],
            self.fast_period,
            self.slow_period,
            self.signal_period
        )

        # Current and previous values
        macd_curr = float(macd_line.iloc[-1])
        macd_prev = float(macd_line.iloc[-2])
        signal_curr = float(signal_line.iloc[-1])
        signal_prev = float(signal_line.iloc[-2])
        hist_curr = float(histogram.iloc[-1])
        hist_prev = float(histogram.iloc[-2])

        if pd.isna(macd_curr) or pd.isna(signal_curr) or pd.isna(hist_curr):
            return None

        # MACD histogram flip conditions
        bullish_flip = hist_prev <= 0 and hist_curr > self.histogram_threshold and macd_curr > signal_curr
        bearish_flip = hist_prev >= 0 and hist_curr < -self.histogram_threshold and macd_curr < signal_curr

        if not (bullish_flip or bearish_flip):
            return None

        action = "BUY" if bullish_flip else "SELL"

        # ATR for stop/target
        atr = self.atr(df['high'], df['low'], df['close'], 14).iloc[-1]
        atr_val = float(atr) if not pd.isna(atr) else context.current_price * 0.01

        entry_price = context.current_price

        if action == "BUY":
            stoploss_price = round(entry_price - 1.5 * atr_val, 2)
            target_price = round(entry_price + 3.0 * atr_val, 2)
        else:
            stoploss_price = round(entry_price + 1.5 * atr_val, 2)
            target_price = round(entry_price - 3.0 * atr_val, 2)

        # Confidence based on histogram magnitude and MACD separation
        hist_magnitude = abs(hist_curr) / max(abs(macd_curr), 0.001)
        macd_separation = abs(macd_curr - signal_curr) / max(abs(signal_curr), 0.001)

        confidence = min(0.85, 0.55 + hist_magnitude * 0.2 + macd_separation * 0.15)
        confidence = round(confidence, 2)

        if confidence < self.config.min_confidence:
            return None

        self.increment_signal_count(context.symbol)

        return StrategySignal(
            symbol=context.symbol,
            exchange=context.exchange,
            action=action,
            confidence_score=confidence,
            entry_price=entry_price,
            stoploss_price=stoploss_price,
            target_price=target_price,
            suggested_quantity=1,
            strategy_name=self.name,
            regime_at_signal=context.regime.value,
            metadata={
                "macd": round(macd_curr, 4),
                "signal": round(signal_curr, 4),
                "histogram": round(hist_curr, 4),
                "macd_separation_pct": round(macd_separation * 100, 2),
                "atr": round(atr_val, 2),
                "flip_type": "bullish" if bullish_flip else "bearish",
            },
        )