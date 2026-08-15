"""Donchian Channel Breakout Strategy - Breakout.

Classic Donchian Channel (20-period) breakout strategy.
BUY when price breaks above 20-period high,
SELL when price breaks below 20-period low.
Best suited for trending and breakout regimes.
"""

import logging
from typing import Optional

import pandas as pd

from app.schemas import StrategySignal, RegimeType
from app.strategy_engine.base import BaseStrategy, StrategyConfig, StrategyContext

logger = logging.getLogger(__name__)


class DonchianBreakoutConfig(StrategyConfig):
    """Configuration for Donchian Channel Breakout strategy."""
    name: str = "DONCHIAN_BREAKOUT"
    period: int = 20
    atr_period: int = 14
    atr_sl_multiplier: float = 2.0
    atr_tp_multiplier: float = 3.0
    suitable_regimes: list = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.HIGH_VOLATILITY,
    ]
    min_confidence: float = 0.6


class DonchianBreakoutStrategy(BaseStrategy):
    """Donchian Channel breakout strategy.

    - BUY: Price breaks above 20-period high
    - SELL: Price breaks below 20-period low
    """

    def __init__(self, config: Optional[DonchianBreakoutConfig] = None):
        config = config or DonchianBreakoutConfig()
        super().__init__(config)
        self.period = config.period
        self.atr_period = config.atr_period
        self.atr_sl_multiplier = config.atr_sl_multiplier
        self.atr_tp_multiplier = config.atr_tp_multiplier

    def evaluate(self, context: StrategyContext) -> Optional[StrategySignal]:
        """Evaluate Donchian breakout conditions."""
        # Check regime suitability
        if not self.is_suitable_regime(context.regime):
            return None

        # Check daily signal limit
        if not self.can_emit_signal(context.symbol):
            return None

        df = context.candles
        if len(df) < self.period + 5:
            return None

        # Calculate Donchian Channel
        upper, middle, lower = self.donchian_channel(
            df['high'], df['low'], self.period
        )

        # Current and previous values
        close = float(df['close'].iloc[-1])
        close_prev = float(df['close'].iloc[-2])
        upper_curr = float(upper.iloc[-1])
        upper_prev = float(upper.iloc[-2])
        lower_curr = float(lower.iloc[-1])
        lower_prev = float(lower.iloc[-2])

        if pd.isna(upper_curr) or pd.isna(lower_curr):
            return None

        # Breakout conditions
        buy_breakout = close_prev <= upper_prev and close > upper_curr
        sell_breakout = close_prev >= lower_prev and close < lower_curr

        if not (buy_breakout or sell_breakout):
            return None

        action = "BUY" if buy_breakout else "SELL"

        # ATR for stop/target
        atr = self.atr(df['high'], df['low'], df['close'], self.atr_period).iloc[-1]
        atr_val = float(atr) if not pd.isna(atr) else context.current_price * 0.01

        entry_price = context.current_price

        if action == "BUY":
            stoploss_price = round(entry_price - self.atr_sl_multiplier * atr_val, 2)
            target_price = round(entry_price + self.atr_tp_multiplier * atr_val, 2)
        else:
            stoploss_price = round(entry_price + self.atr_sl_multiplier * atr_val, 2)
            target_price = round(entry_price - self.atr_tp_multiplier * atr_val, 2)

        # Confidence based on breakout strength and volatility
        channel_width = (upper_curr - lower_curr) / middle_curr if middle_curr != 0 else 0
        breakout_strength = abs(close - (upper_curr if action == "BUY" else lower_curr)) / atr_val

        confidence = min(0.85, 0.55 + breakout_strength * 0.1 + channel_width * 0.15)
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
                "middle_channel": round(float(middle.iloc[-1]), 2),
                "lower_channel": round(lower_curr, 2),
                "channel_width_pct": round(channel_width * 100, 2),
                "breakout_strength": round(breakout_strength, 2),
                "atr": round(atr_val, 2),
            },
        )