"""EMA Crossover Strategy - Trend Following.

Generates BUY when fast EMA crosses above slow EMA (golden cross),
SELL when fast EMA crosses below slow EMA (death cross).
Best suited for trending regimes.
"""

import logging
from typing import Optional

import pandas as pd

from app.schemas import StrategySignal, RegimeType
from app.strategy_engine.base import BaseStrategy, StrategyConfig, StrategyContext

logger = logging.getLogger(__name__)


class EMACrossoverConfig(StrategyConfig):
    """Configuration for EMA Crossover strategy."""
    name: str = "EMA_CROSSOVER"
    fast_period: int = 9
    slow_period: int = 21
    adx_filter: float = 25.0  # Require ADX > threshold for trend confirmation
    suitable_regimes: list = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
    ]
    min_confidence: float = 0.65


class EMACrossoverStrategy(BaseStrategy):
    """EMA Crossover trend-following strategy.

    - BUY: Fast EMA crosses above Slow EMA with ADX confirmation
    - SELL: Fast EMA crosses below Slow EMA with ADX confirmation
    """

    def __init__(self, config: Optional[EMACrossoverConfig] = None):
        config = config or EMACrossoverConfig()
        super().__init__(config)
        self.fast_period = config.fast_period
        self.slow_period = config.slow_period
        self.adx_filter = config.adx_filter

    def evaluate(self, context: StrategyContext) -> Optional[StrategySignal]:
        """Evaluate EMA crossover conditions."""
        # Check regime suitability
        if not self.is_suitable_regime(context.regime):
            return None

        # Check daily signal limit
        if not self.can_emit_signal(context.symbol):
            return None

        df = context.candles
        if len(df) < self.slow_period + 5:
            return None

        # Calculate EMAs
        close = df['close']
        ema_fast = self.ema(close, self.fast_period)
        ema_slow = self.ema(close, self.slow_period)

        # Need at least 2 bars for crossover detection
        if len(ema_fast) < 2 or pd.isna(ema_fast.iloc[-1]) or pd.isna(ema_slow.iloc[-1]):
            return None

        # Current and previous values
        fast_curr = float(ema_fast.iloc[-1])
        fast_prev = float(ema_fast.iloc[-2])
        slow_curr = float(ema_slow.iloc[-1])
        slow_prev = float(ema_slow.iloc[-2])

        # Check for crossover
        golden_cross = fast_prev <= slow_prev and fast_curr > slow_curr
        death_cross = fast_prev >= slow_prev and fast_curr < slow_curr

        if not (golden_cross or death_cross):
            return None

        # ADX confirmation (from regime metadata)
        adx = context.regime_metadata.get('adx')
        if adx is not None and adx < self.adx_filter:
            logger.debug(
                "%s: ADX filter failed (%.1f < %.1f) for %s",
                self.name, adx, self.adx_filter, context.symbol
            )
            return None

        # Calculate ATR for stop/target
        atr = self.atr(df['high'], df['low'], df['close'], 14).iloc[-1]
        atr_val = float(atr) if not pd.isna(atr) else context.current_price * 0.01

        entry_price = context.current_price
        action = "BUY" if golden_cross else "SELL"

        if action == "BUY":
            stoploss_price = round(entry_price - 1.5 * atr_val, 2)
            target_price = round(entry_price + 3.0 * atr_val, 2)  # 1:2 R:R
        else:
            stoploss_price = round(entry_price + 1.5 * atr_val, 2)
            target_price = round(entry_price - 3.0 * atr_val, 2)

        # Confidence based on ADX and EMA separation
        ema_separation = abs(fast_curr - slow_curr) / slow_curr
        confidence = min(0.95, 0.6 + (adx or 25) / 100 + ema_separation * 10)
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
            suggested_quantity=1,  # Will be sized by risk engine
            strategy_name=self.name,
            regime_at_signal=context.regime.value,
            metadata={
                "ema_fast": round(fast_curr, 2),
                "ema_slow": round(slow_curr, 2),
                "ema_separation_pct": round(ema_separation * 100, 2),
                "adx": adx,
                "atr": round(atr_val, 2),
                "crossover_type": "golden" if golden_cross else "death",
            },
        )