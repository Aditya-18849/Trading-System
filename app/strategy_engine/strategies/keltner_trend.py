"""Keltner Channel Trend Strategy - Trend Following.

Uses Keltner Channels (EMA + ATR) for trend following.
BUY when price pulls back to middle EMA in uptrend (channel sloping up),
SELL when price pulls back to middle EMA in downtrend (channel sloping down).
Best suited for trending regimes.
"""

import logging
from typing import Optional

import pandas as pd

from app.schemas import StrategySignal, RegimeType
from app.strategy_engine.base import BaseStrategy, StrategyConfig, StrategyContext

logger = logging.getLogger(__name__)


class KeltnerTrendConfig(StrategyConfig):
    """Configuration for Keltner Channel Trend strategy."""
    name: str = "KELTNER_TREND"
    period: int = 20
    multiplier: float = 2.0
    ema_slope_period: int = 10  # Period for EMA slope calculation
    pullback_threshold: float = 0.005  # 0.5% pullback to middle
    suitable_regimes: list = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
    ]
    min_confidence: float = 0.6


class KeltnerTrendStrategy(BaseStrategy):
    """Keltner Channel trend following strategy.

    - BUY: Uptrend (channel sloping up) + price pulls back to middle EMA
    - SELL: Downtrend (channel sloping down) + price pulls back to middle EMA
    """

    def __init__(self, config: Optional[KeltnerTrendConfig] = None):
        config = config or KeltnerTrendConfig()
        super().__init__(config)
        self.period = config.period
        self.multiplier = config.multiplier
        self.ema_slope_period = config.ema_slope_period
        self.pullback_threshold = config.pullback_threshold

    def evaluate(self, context: StrategyContext) -> Optional[StrategySignal]:
        """Evaluate Keltner trend conditions."""
        # Check regime suitability
        if not self.is_suitable_regime(context.regime):
            return None

        # Check daily signal limit
        if not self.can_emit_signal(context.symbol):
            return None

        df = context.candles
        if len(df) < self.period + self.ema_slope_period + 5:
            return None

        # Calculate Keltner Channel
        upper, middle, lower = self.keltner_channel(
            df['high'], df['low'], df['close'],
            self.period, self.multiplier
        )

        # Calculate EMA slope (trend direction)
        middle_slope = middle.pct_change(self.ema_slope_period)

        # Current values
        close = float(df['close'].iloc[-1])
        upper_curr = float(upper.iloc[-1])
        middle_curr = float(middle.iloc[-1])
        lower_curr = float(lower.iloc[-1])
        slope_curr = float(middle_slope.iloc[-1])

        if pd.isna(middle_curr) or pd.isna(slope_curr):
            return None

        # Trend direction
        uptrend = slope_curr > 0
        downtrend = slope_curr < 0

        # Pullback to middle
        pullback_to_middle = abs(close - middle_curr) / middle_curr < self.pullback_threshold

        # Price within channel
        in_channel = lower_curr <= close <= upper_curr

        buy_signal = uptrend and pullback_to_middle and in_channel and close > middle_curr
        sell_signal = downtrend and pullback_to_middle and in_channel and close < middle_curr

        if not (buy_signal or sell_signal):
            return None

        action = "BUY" if buy_signal else "SELL"

        # ATR for stop/target
        atr = self.atr(df['high'], df['low'], df['close'], 14).iloc[-1]
        atr_val = float(atr) if not pd.isna(atr) else context.current_price * 0.01

        entry_price = context.current_price

        if action == "BUY":
            stoploss_price = round(lower_curr, 2)  # Lower channel as SL
            target_price = round(upper_curr, 2)    # Upper channel as target
        else:
            stoploss_price = round(upper_curr, 2)  # Upper channel as SL
            target_price = round(lower_curr, 2)    # Lower channel as target

        # Confidence based on trend strength and channel position
        trend_strength = abs(slope_curr) * 100  # Convert to percentage
        channel_position = (close - middle_curr) / (upper_curr - lower_curr) if upper_curr != lower_curr else 0.5

        confidence = min(0.85, 0.55 + trend_strength * 2 + (0.5 - abs(channel_position - 0.5)) * 0.2)
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
                "upper_channel": round(upper_curr, 2),
                "middle_channel": round(middle_curr, 2),
                "lower_channel": round(lower_curr, 2),
                "channel_slope_pct": round(slope_curr * 100, 4),
                "trend": "up" if uptrend else "down",
                "atr": round(atr_val, 2),
            },
        )