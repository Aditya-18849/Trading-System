"""Stochastic RSI Reversion Strategy - Mean Reversion.

Uses Stochastic RSI for mean reversion signals.
BUY when Stoch RSI %K crosses above %D from oversold (<20),
SELL when Stoch RSI %K crosses below %D from overbought (>80).
Best suited for range-bound regimes.
"""

import logging
from typing import Optional

import pandas as pd

from app.schemas import StrategySignal, RegimeType
from app.strategy_engine.base import BaseStrategy, StrategyConfig, StrategyContext

logger = logging.getLogger(__name__)


class StochRSIReversionConfig(StrategyConfig):
    """Configuration for Stochastic RSI Reversion strategy."""
    name: str = "STOCH_RSI_REVERSION"
    rsi_period: int = 14
    stoch_period: int = 14
    smooth_k: int = 3
    smooth_d: int = 3
    oversold: float = 20.0
    overbought: float = 80.0
    bb_period: int = 20
    suitable_regimes: list = [
        RegimeType.RANGE_BOUND,
    ]
    min_confidence: float = 0.55


class StochRSIReversionStrategy(BaseStrategy):
    """Stochastic RSI mean reversion strategy.

    - BUY: %K crosses above %D from oversold territory
    - SELL: %K crosses below %D from overbought territory
    """

    def __init__(self, config: Optional[StochRSIReversionConfig] = None):
        config = config or StochRSIReversionConfig()
        super().__init__(config)
        self.rsi_period = config.rsi_period
        self.stoch_period = config.stoch_period
        self.smooth_k = config.smooth_k
        self.smooth_d = config.smooth_d
        self.oversold = config.oversold
        self.overbought = config.overbought
        self.bb_period = config.bb_period

    def evaluate(self, context: StrategyContext) -> Optional[StrategySignal]:
        """Evaluate Stochastic RSI reversion conditions."""
        # Check regime suitability
        if not self.is_suitable_regime(context.regime):
            return None

        # Check daily signal limit
        if not self.can_emit_signal(context.symbol):
            return None

        df = context.candles
        if len(df) < max(self.rsi_period, self.stoch_period) + self.smooth_k + self.smooth_d + 5:
            return None

        # Calculate Stochastic RSI
        k, d = self.stochastic_rsi(
            df['close'],
            self.rsi_period,
            self.smooth_k,
            self.smooth_d
        )

        # Calculate Bollinger Bands for target
        upper, middle, lower = self.bollinger_bands(
            df['close'], self.bb_period, 2.0
        )

        # Current and previous values
        k_curr = float(k.iloc[-1])
        k_prev = float(k.iloc[-2])
        d_curr = float(d.iloc[-1])
        d_prev = float(d.iloc[-2])
        close = float(df['close'].iloc[-1])
        middle_curr = float(middle.iloc[-1])

        if pd.isna(k_curr) or pd.isna(k_prev) or pd.isna(d_curr) or pd.isna(d_prev):
            return None

        # Crossover conditions
        bullish_cross = k_prev <= d_prev and k_curr > d_curr and k_prev < self.oversold
        bearish_cross = k_prev >= d_prev and k_curr < d_curr and k_prev > self.overbought

        if not (bullish_cross or bearish_cross):
            return None

        action = "BUY" if bullish_cross else "SELL"

        # ATR for stop/target
        atr = self.atr(df['high'], df['low'], df['close'], 14).iloc[-1]
        atr_val = float(atr) if not pd.isna(atr) else context.current_price * 0.01

        entry_price = context.current_price

        if action == "BUY":
            stoploss_price = round(entry_price - 1.5 * atr_val, 2)
            target_price = round(middle_curr, 2)
        else:
            stoploss_price = round(entry_price + 1.5 * atr_val, 2)
            target_price = round(middle_curr, 2)

        # Confidence based on distance from extreme and crossover strength
        if action == "BUY":
            distance_from_extreme = (self.oversold - k_prev) / self.oversold
        else:
            distance_from_extreme = (k_prev - self.overbought) / (100 - self.overbought)

        crossover_strength = abs(k_curr - d_curr) / 100

        confidence = min(0.8, 0.5 + distance_from_extreme * 0.2 + crossover_strength * 0.2)
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
                "stoch_k": round(k_curr, 1),
                "stoch_d": round(d_curr, 1),
                "crossover": "bullish" if bullish_cross else "bearish",
                "middle_band": round(middle_curr, 2),
                "atr": round(atr_val, 2),
            },
        )