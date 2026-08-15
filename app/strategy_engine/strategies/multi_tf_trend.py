"""Multi-Timeframe Trend Strategy - Trend Following.

Analyzes trend alignment across multiple timeframes (15m, 1h, 4h).
BUY when all timeframes show bullish alignment (EMA 50 > EMA 200),
SELL when all timeframes show bearish alignment.
Best suited for trending regimes.
"""

import logging
from typing import Optional, Dict

import pandas as pd

from app.schemas import StrategySignal, RegimeType
from app.strategy_engine.base import BaseStrategy, StrategyConfig, StrategyContext

logger = logging.getLogger(__name__)


class MultiTimeframeTrendConfig(StrategyConfig):
    """Configuration for Multi-Timeframe Trend strategy."""
    name: str = "MULTI_TF_TREND"
    ema_fast: int = 50
    ema_slow: int = 200
    # Timeframes to check (in minutes)
    timeframes: list = [15, 60, 240]  # 15m, 1h, 4h
    suitable_regimes: list = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
    ]
    min_confidence: float = 0.7  # Higher threshold for multi-TF confirmation


class MultiTimeframeTrendStrategy(BaseStrategy):
    """Multi-timeframe trend alignment strategy.

    Requires trend alignment across multiple timeframes.
    This strategy needs pre-fetched multi-timeframe data in context.
    """

    def __init__(self, config: Optional[MultiTimeframeTrendConfig] = None):
        config = config or MultiTimeframeTrendConfig()
        super().__init__(config)
        self.ema_fast = config.ema_fast
        self.ema_slow = config.ema_slow
        self.timeframes = config.timeframes

    def evaluate(self, context: StrategyContext) -> Optional[StrategySignal]:
        """Evaluate multi-timeframe trend alignment."""
        # Check regime suitability
        if not self.is_suitable_regime(context.regime):
            return None

        # Check daily signal limit
        if not self.can_emit_signal(context.symbol):
            return None

        # Get multi-timeframe candles from context metadata
        mtf_candles = context.regime_metadata.get('mtf_candles', {})

        if not mtf_candles:
            logger.debug("%s: No multi-timeframe data available for %s",
                         self.name, context.symbol)
            return None

        # Check each timeframe
        bullish_count = 0
        bearish_count = 0
        tf_details = {}

        for tf in self.timeframes:
            tf_key = f"{tf}m" if tf < 60 else f"{tf//60}h"
            df = mtf_candles.get(tf_key)

            if df is None or len(df) < self.ema_slow + 5:
                tf_details[tf_key] = "insufficient_data"
                continue

            close = df['close']
            ema_fast = self.ema(close, self.ema_fast)
            ema_slow = self.ema(close, self.ema_slow)

            if len(ema_fast) < 2 or pd.isna(ema_fast.iloc[-1]) or pd.isna(ema_slow.iloc[-1]):
                tf_details[tf_key] = "insufficient_data"
                continue

            fast_curr = float(ema_fast.iloc[-1])
            slow_curr = float(ema_slow.iloc[-1])

            if fast_curr > slow_curr:
                bullish_count += 1
                tf_details[tf_key] = "bullish"
            elif fast_curr < slow_curr:
                bearish_count += 1
                tf_details[tf_key] = "bearish"
            else:
                tf_details[tf_key] = "neutral"

        # Need all timeframes aligned
        total_tfs = len([k for k, v in tf_details.items() if v != "insufficient_data"])

        if total_tfs < 2:  # Need at least 2 timeframes
            return None

        # All bullish or all bearish
        all_bullish = bullish_count == total_tfs
        all_bearish = bearish_count == total_tfs

        if not (all_bullish or all_bearish):
            return None

        action = "BUY" if all_bullish else "SELL"

        # Use the base timeframe (context.candles) for entry/exit
        df = context.candles
        atr = self.atr(df['high'], df['low'], df['close'], 14).iloc[-1]
        atr_val = float(atr) if not pd.isna(atr) else context.current_price * 0.01

        entry_price = context.current_price

        if action == "BUY":
            stoploss_price = round(entry_price - 2.0 * atr_val, 2)
            target_price = round(entry_price + 4.0 * atr_val, 2)
        else:
            stoploss_price = round(entry_price + 2.0 * atr_val, 2)
            target_price = round(entry_price - 4.0 * atr_val, 2)

        # High confidence for multi-TF alignment
        confidence = min(0.95, 0.7 + (total_tfs / len(self.timeframes)) * 0.2)
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
                "timeframe_alignment": tf_details,
                "bullish_tfs": bullish_count,
                "bearish_tfs": bearish_count,
                "total_tfs": total_tfs,
                "atr": round(atr_val, 2),
            },
        )