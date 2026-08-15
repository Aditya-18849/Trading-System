"""Supertrend Follow Strategy - Trend Following.

Follows the Supertrend indicator for trend direction.
BUY when price closes above Supertrend (bullish flip),
SELL when price closes below Supertrend (bearish flip).
Best suited for trending regimes.
"""

import logging
from typing import Optional

import pandas as pd

from app.schemas import StrategySignal, RegimeType
from app.strategy_engine.base import BaseStrategy, StrategyConfig, StrategyContext

logger = logging.getLogger(__name__)


class SupertrendConfig(StrategyConfig):
    """Configuration for Supertrend Follow strategy."""
    name: str = "SUPERTREND_FOLLOW"
    period: int = 10
    multiplier: float = 3.0
    ema_filter_period: int = 50  # EMA filter for additional confirmation
    suitable_regimes: list = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
    ]
    min_confidence: float = 0.6


class SupertrendStrategy(BaseStrategy):
    """Supertrend trend-following strategy.

    - BUY: Price closes above Supertrend line (bullish) + EMA filter
    - SELL: Price closes below Supertrend line (bearish) + EMA filter
    """

    def __init__(self, config: Optional[SupertrendConfig] = None):
        config = config or SupertrendConfig()
        super().__init__(config)
        self.period = config.period
        self.multiplier = config.multiplier
        self.ema_filter_period = config.ema_filter_period

    def evaluate(self, context: StrategyContext) -> Optional[StrategySignal]:
        """Evaluate Supertrend conditions."""
        # Check regime suitability
        if not self.is_suitable_regime(context.regime):
            return None

        # Check daily signal limit
        if not self.can_emit_signal(context.symbol):
            return None

        df = context.candles
        if len(df) < max(self.period, self.ema_filter_period) + 5:
            return None

        # Calculate Supertrend
        st_line, st_dir = self.supertrend(
            df['high'], df['low'], df['close'],
            self.period, self.multiplier
        )

        # Calculate EMA filter
        ema_filter = self.ema(df['close'], self.ema_filter_period)

        if len(st_line) < 2 or len(ema_filter) < 2:
            return None

        # Current and previous values
        st_curr = float(st_line.iloc[-1])
        st_prev = float(st_line.iloc[-2])
        dir_curr = float(st_dir.iloc[-1])
        dir_prev = float(st_dir.iloc[-2])
        close_curr = float(df['close'].iloc[-1])
        close_prev = float(df['close'].iloc[-2])
        ema_curr = float(ema_filter.iloc[-1])

        # Check for Supertrend flip
        bullish_flip = dir_prev == -1 and dir_curr == 1
        bearish_flip = dir_prev == 1 and dir_curr == -1

        if not (bullish_flip or bearish_flip):
            return None

        # EMA filter confirmation
        if bullish_flip and close_curr < ema_curr:
            logger.debug("%s: Bullish flip but price below EMA filter for %s",
                         self.name, context.symbol)
            return None
        if bearish_flip and close_curr > ema_curr:
            logger.debug("%s: Bearish flip but price above EMA filter for %s",
                         self.name, context.symbol)
            return None

        # ATR for stop/target
        atr = self.atr(df['high'], df['low'], df['close'], 14).iloc[-1]
        atr_val = float(atr) if not pd.isna(atr) else context.current_price * 0.01

        entry_price = context.current_price
        action = "BUY" if bullish_flip else "SELL"

        if action == "BUY":
            stoploss_price = round(entry_price - 2.0 * atr_val, 2)
            target_price = round(entry_price + 4.0 * atr_val, 2)  # 1:2 R:R
        else:
            stoploss_price = round(entry_price + 2.0 * atr_val, 2)
            target_price = round(entry_price - 4.0 * atr_val, 2)

        # Confidence based on regime and ADX
        adx = context.regime_metadata.get('adx', 25)
        confidence = min(0.9, 0.6 + (adx or 25) / 100)
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
                "supertrend_line": round(st_curr, 2),
                "supertrend_dir": int(dir_curr),
                "ema_filter": round(ema_curr, 2),
                "atr": round(atr_val, 2),
                "flip_type": "bullish" if bullish_flip else "bearish",
            },
        )