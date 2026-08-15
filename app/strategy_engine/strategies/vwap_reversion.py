"""VWAP Reversion Strategy - Mean Reversion.

Trades reversions to VWAP with Bollinger Band extremes and RSI confirmation.
BUY when price is below VWAP - 2σ and RSI < 30,
SELL when price is above VWAP + 2σ and RSI > 70.
Best suited for range-bound regimes.
"""

import logging
from typing import Optional

import pandas as pd

from app.schemas import StrategySignal, RegimeType
from app.strategy_engine.base import BaseStrategy, StrategyConfig, StrategyContext

logger = logging.getLogger(__name__)


class VWAPReversionConfig(StrategyConfig):
    """Configuration for VWAP Reversion strategy."""
    name: str = "VWAP_REVERSION"
    vwap_std: float = 2.0
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    suitable_regimes: list = [
        RegimeType.RANGE_BOUND,
    ]
    min_confidence: float = 0.55


class VWAPReversionStrategy(BaseStrategy):
    """VWAP mean reversion strategy.

    - BUY: Price below VWAP - 2σ AND RSI < 30 (oversold)
    - SELL: Price above VWAP + 2σ AND RSI > 70 (overbought)
    """

    def __init__(self, config: Optional[VWAPReversionConfig] = None):
        config = config or VWAPReversionConfig()
        super().__init__(config)
        self.vwap_std = config.vwap_std
        self.rsi_period = config.rsi_period
        self.rsi_oversold = config.rsi_oversold
        self.rsi_overbought = config.rsi_overbought

    def evaluate(self, context: StrategyContext) -> Optional[StrategySignal]:
        """Evaluate VWAP reversion conditions."""
        # Check regime suitability
        if not self.is_suitable_regime(context.regime):
            return None

        # Check daily signal limit
        if not self.can_emit_signal(context.symbol):
            return None

        df = context.candles
        if len(df) < self.rsi_period + 20:
            return None

        # Calculate VWAP
        vwap = self.vwap(df['high'], df['low'], df['close'], df['volume'])

        # Calculate VWAP bands (using rolling std of typical price)
        typical_price = (df['high'] + df['low'] + df['close']) / 3
        vwap_std = typical_price.rolling(window=20).std()

        upper_band = vwap + self.vwap_std * vwap_std
        lower_band = vwap - self.vwap_std * vwap_std

        # Calculate RSI
        rsi = self.rsi(df['close'], self.rsi_period)

        # Current values
        close = float(df['close'].iloc[-1])
        vwap_curr = float(vwap.iloc[-1])
        upper_curr = float(upper_band.iloc[-1])
        lower_curr = float(lower_band.iloc[-1])
        rsi_curr = float(rsi.iloc[-1])

        if pd.isna(vwap_curr) or pd.isna(rsi_curr):
            return None

        # Check conditions
        price_below_lower = close < lower_curr
        price_above_upper = close > upper_curr
        rsi_oversold = rsi_curr < self.rsi_oversold
        rsi_overbought = rsi_curr > self.rsi_overbought

        buy_signal = price_below_lower and rsi_oversold
        sell_signal = price_above_upper and rsi_overbought

        if not (buy_signal or sell_signal):
            return None

        action = "BUY" if buy_signal else "SELL"

        # ATR for stop/target
        atr = self.atr(df['high'], df['low'], df['close'], 14).iloc[-1]
        atr_val = float(atr) if not pd.isna(atr) else context.current_price * 0.01

        entry_price = context.current_price

        if action == "BUY":
            stoploss_price = round(entry_price - 1.5 * atr_val, 2)
            target_price = round(vwap_curr, 2)  # Target VWAP
        else:
            stoploss_price = round(entry_price + 1.5 * atr_val, 2)
            target_price = round(vwap_curr, 2)  # Target VWAP

        # Confidence based on distance from band and RSI extreme
        if action == "BUY":
            band_distance = (lower_curr - close) / lower_curr
            rsi_factor = (self.rsi_oversold - rsi_curr) / self.rsi_oversold
        else:
            band_distance = (close - upper_curr) / upper_curr
            rsi_factor = (rsi_curr - self.rsi_overbought) / (100 - self.rsi_overbought)

        confidence = min(0.85, 0.5 + band_distance * 5 + rsi_factor * 0.3)
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
                "vwap": round(vwap_curr, 2),
                "upper_band": round(upper_curr, 2),
                "lower_band": round(lower_curr, 2),
                "rsi": round(rsi_curr, 1),
                "atr": round(atr_val, 2),
                "band_distance_pct": round(band_distance * 100, 2),
            },
        )