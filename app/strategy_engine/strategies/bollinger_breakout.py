"""Bollinger Breakout Strategy - Breakout.

Trades breakouts from Bollinger Band squeeze (low volatility).
BUY when price breaks above upper band after squeeze,
SELL when price breaks below lower band after squeeze.
Best suited for high-volatility / breakout regimes.
"""

import logging
from typing import Optional

import pandas as pd

from app.schemas import StrategySignal, RegimeType
from app.strategy_engine.base import BaseStrategy, StrategyConfig, StrategyContext

logger = logging.getLogger(__name__)


class BollingerBreakoutConfig(StrategyConfig):
    """Configuration for Bollinger Breakout strategy."""
    name: str = "BOLLINGER_BREAKOUT"
    bb_period: int = 20
    bb_std: float = 2.0
    squeeze_threshold: float = 0.1  # Bandwidth < 10% = squeeze
    volume_multiplier: float = 1.5  # Volume must be 1.5x average
    suitable_regimes: list = [
        RegimeType.HIGH_VOLATILITY,
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
    ]
    min_confidence: float = 0.6


class BollingerBreakoutStrategy(BaseStrategy):
    """Bollinger Band breakout strategy.

    - BUY: Price breaks above upper band after squeeze + volume confirmation
    - SELL: Price breaks below lower band after squeeze + volume confirmation
    """

    def __init__(self, config: Optional[BollingerBreakoutConfig] = None):
        config = config or BollingerBreakoutConfig()
        super().__init__(config)
        self.bb_period = config.bb_period
        self.bb_std = config.bb_std
        self.squeeze_threshold = config.squeeze_threshold
        self.volume_multiplier = config.volume_multiplier

    def evaluate(self, context: StrategyContext) -> Optional[StrategySignal]:
        """Evaluate Bollinger breakout conditions."""
        # Check regime suitability
        if not self.is_suitable_regime(context.regime):
            return None

        # Check daily signal limit
        if not self.can_emit_signal(context.symbol):
            return None

        df = context.candles
        if len(df) < self.bb_period + 10:
            return None

        # Calculate Bollinger Bands
        upper, middle, lower = self.bollinger_bands(
            df['close'], self.bb_period, self.bb_std
        )

        # Calculate bandwidth
        bandwidth = (upper - lower) / middle

        # Current and previous values
        close = float(df['close'].iloc[-1])
        close_prev = float(df['close'].iloc[-2])
        upper_curr = float(upper.iloc[-1])
        upper_prev = float(upper.iloc[-2])
        lower_curr = float(lower.iloc[-1])
        lower_prev = float(lower.iloc[-2])
        bw_curr = float(bandwidth.iloc[-1])
        bw_prev = float(bandwidth.iloc[-2])

        if pd.isna(bw_curr) or pd.isna(bw_prev):
            return None

        # Check for squeeze (low bandwidth)
        was_squeeze = bw_prev < self.squeeze_threshold

        # Volume confirmation
        avg_volume = df['volume'].rolling(window=20).mean().iloc[-1]
        curr_volume = float(df['volume'].iloc[-1])
        volume_surge = curr_volume > avg_volume * self.volume_multiplier

        # Breakout conditions
        buy_breakout = (
            close_prev <= upper_prev and  # Was at or below upper band
            close > upper_curr and        # Now above upper band
            was_squeeze and               # Was in squeeze
            volume_surge                  # Volume confirmation
        )

        sell_breakout = (
            close_prev >= lower_prev and  # Was at or above lower band
            close < lower_curr and        # Now below lower band
            was_squeeze and               # Was in squeeze
            volume_surge                  # Volume confirmation
        )

        if not (buy_breakout or sell_breakout):
            return None

        action = "BUY" if buy_breakout else "SELL"

        # ATR for stop/target
        atr = self.atr(df['high'], df['low'], df['close'], 14).iloc[-1]
        atr_val = float(atr) if not pd.isna(atr) else context.current_price * 0.01

        entry_price = context.current_price

        if action == "BUY":
            stoploss_price = round(middle.iloc[-1], 2)  # Middle band as SL
            target_price = round(entry_price + 3.0 * atr_val, 2)
        else:
            stoploss_price = round(middle.iloc[-1], 2)  # Middle band as SL
            target_price = round(entry_price - 3.0 * atr_val, 2)

        # Confidence based on squeeze tightness and volume
        squeeze_tightness = 1 - (bw_prev / self.squeeze_threshold)
        volume_factor = min(1.0, curr_volume / (avg_volume * self.volume_multiplier))
        confidence = min(0.9, 0.55 + squeeze_tightness * 0.2 + volume_factor * 0.15)
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
                "upper_band": round(upper_curr, 2),
                "lower_band": round(lower_curr, 2),
                "middle_band": round(float(middle.iloc[-1]), 2),
                "bandwidth": round(bw_curr, 4),
                "squeeze": was_squeeze,
                "volume_ratio": round(curr_volume / avg_volume, 2),
                "atr": round(atr_val, 2),
            },
        )