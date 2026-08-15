"""RSI Mean Reversion Strategy - Mean Reversion.

Classic RSI mean reversion with Bollinger Band middle as target.
BUY when RSI crosses above 30 from oversold,
SELL when RSI crosses below 70 from overbought.
Best suited for range-bound regimes.
"""

import logging
from typing import Optional

import pandas as pd

from app.schemas import StrategySignal, RegimeType
from app.strategy_engine.base import BaseStrategy, StrategyConfig, StrategyContext

logger = logging.getLogger(__name__)


class RSIMeanReversionConfig(StrategyConfig):
    """Configuration for RSI Mean Reversion strategy."""
    name: str = "RSI_MEAN_REVERSION"
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    bb_period: int = 20
    bb_std: float = 2.0
    suitable_regimes: list = [
        RegimeType.RANGE_BOUND,
    ]
    min_confidence: float = 0.55


class RSIMeanReversionStrategy(BaseStrategy):
    """RSI mean reversion strategy.

    - BUY: RSI crosses above 30 (exiting oversold) + price near lower BB
    - SELL: RSI crosses below 70 (exiting overbought) + price near upper BB
    """

    def __init__(self, config: Optional[RSIMeanReversionConfig] = None):
        config = config or RSIMeanReversionConfig()
        super().__init__(config)
        self.rsi_period = config.rsi_period
        self.rsi_oversold = config.rsi_oversold
        self.rsi_overbought = config.rsi_overbought
        self.bb_period = config.bb_period
        self.bb_std = config.bb_std

    def evaluate(self, context: StrategyContext) -> Optional[StrategySignal]:
        """Evaluate RSI mean reversion conditions."""
        # Check regime suitability
        if not self.is_suitable_regime(context.regime):
            return None

        # Check daily signal limit
        if not self.can_emit_signal(context.symbol):
            return None

        df = context.candles
        if len(df) < max(self.rsi_period, self.bb_period) + 5:
            return None

        # Calculate RSI
        rsi = self.rsi(df['close'], self.rsi_period)

        # Calculate Bollinger Bands
        upper, middle, lower = self.bollinger_bands(
            df['close'], self.bb_period, self.bb_std
        )

        # Current and previous values
        rsi_curr = float(rsi.iloc[-1])
        rsi_prev = float(rsi.iloc[-2])
        close = float(df['close'].iloc[-1])
        upper_curr = float(upper.iloc[-1])
        lower_curr = float(lower.iloc[-1])
        middle_curr = float(middle.iloc[-1])

        if pd.isna(rsi_curr) or pd.isna(rsi_prev):
            return None

        # RSI crossover conditions
        rsi_cross_up = rsi_prev <= self.rsi_oversold and rsi_curr > self.rsi_oversold
        rsi_cross_down = rsi_prev >= self.rsi_overbought and rsi_curr < self.rsi_overbought

        # Price location confirmation
        near_lower = close < middle_curr  # Below middle band
        near_upper = close > middle_curr  # Above middle band

        buy_signal = rsi_cross_up and near_lower
        sell_signal = rsi_cross_down and near_upper

        if not (buy_signal or sell_signal):
            return None

        action = "BUY" if buy_signal else "SELL"

        # ATR for stop/target
        atr = self.atr(df['high'], df['low'], df['close'], 14).iloc[-1]
        atr_val = float(atr) if not pd.isna(atr) else context.current_price * 0.01

        entry_price = context.current_price

        if action == "BUY":
            stoploss_price = round(entry_price - 1.5 * atr_val, 2)
            target_price = round(middle_curr, 2)  # Target middle band
        else:
            stoploss_price = round(entry_price + 1.5 * atr_val, 2)
            target_price = round(middle_curr, 2)  # Target middle band

        # Confidence based on RSI distance from extreme and BB position
        if action == "BUY":
            rsi_distance = (rsi_curr - self.rsi_oversold) / (50 - self.rsi_oversold)
            bb_position = (close - lower_curr) / (middle_curr - lower_curr) if middle_curr != lower_curr else 0.5
        else:
            rsi_distance = (self.rsi_overbought - rsi_curr) / (self.rsi_overbought - 50)
            bb_position = (upper_curr - close) / (upper_curr - middle_curr) if upper_curr != middle_curr else 0.5

        confidence = min(0.8, 0.5 + rsi_distance * 0.2 + (1 - bb_position) * 0.15)
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
                "rsi": round(rsi_curr, 1),
                "rsi_cross": "up" if buy_signal else "down",
                "upper_band": round(upper_curr, 2),
                "middle_band": round(middle_curr, 2),
                "lower_band": round(lower_curr, 2),
                "atr": round(atr_val, 2),
            },
        )