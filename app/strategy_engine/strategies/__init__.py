"""
Strategy Engine Strategies Package.

Exports all available strategy classes for easy importing.
"""

from app.strategy_engine.strategies.ema_crossover import EMACrossoverStrategy, EMACrossoverConfig
from app.strategy_engine.strategies.supertrend import SupertrendStrategy, SupertrendConfig
from app.strategy_engine.strategies.vwap_reversion import VWAPReversionStrategy, VWAPReversionConfig
from app.strategy_engine.strategies.bollinger_breakout import BollingerBreakoutStrategy, BollingerBreakoutConfig
from app.strategy_engine.strategies.rsi_mean_reversion import RSIMeanReversionStrategy, RSIMeanReversionConfig
from app.strategy_engine.strategies.macd_momentum import MACDMomentumStrategy, MACDMomentumConfig
from app.strategy_engine.strategies.donchian_breakout import DonchianBreakoutStrategy, DonchianBreakoutConfig
from app.strategy_engine.strategies.keltner_trend import KeltnerTrendStrategy, KeltnerTrendConfig
from app.strategy_engine.strategies.stoch_rsi_reversion import StochRSIReversionStrategy, StochRSIReversionConfig
from app.strategy_engine.strategies.multi_tf_trend import MultiTimeframeTrendStrategy, MultiTimeframeTrendConfig

# Strategy registry for dynamic loading
STRATEGY_REGISTRY = {
    "EMA_CROSSOVER": (EMACrossoverStrategy, EMACrossoverConfig),
    "SUPERTREND_FOLLOW": (SupertrendStrategy, SupertrendConfig),
    "VWAP_REVERSION": (VWAPReversionStrategy, VWAPReversionConfig),
    "BOLLINGER_BREAKOUT": (BollingerBreakoutStrategy, BollingerBreakoutConfig),
    "RSI_MEAN_REVERSION": (RSIMeanReversionStrategy, RSIMeanReversionConfig),
    "MACD_MOMENTUM": (MACDMomentumStrategy, MACDMomentumConfig),
    "DONCHIAN_BREAKOUT": (DonchianBreakoutStrategy, DonchianBreakoutConfig),
    "KELTNER_TREND": (KeltnerTrendStrategy, KeltnerTrendConfig),
    "STOCH_RSI_REVERSION": (StochRSIReversionStrategy, StochRSIReversionConfig),
    "MULTI_TF_TREND": (MultiTimeframeTrendStrategy, MultiTimeframeTrendConfig),
}

# List of all strategy names in priority order (for default loading)
DEFAULT_STRATEGIES = [
    "EMA_CROSSOVER",
    "SUPERTREND_FOLLOW",
    "MACD_MOMENTUM",
    "KELTNER_TREND",
    "DONCHIAN_BREAKOUT",
    "BOLLINGER_BREAKOUT",
    "VWAP_REVERSION",
    "RSI_MEAN_REVERSION",
    "STOCH_RSI_REVERSION",
    "MULTI_TF_TREND",
]


def get_strategy_class(name: str):
    """Get strategy class by name."""
    if name in STRATEGY_REGISTRY:
        return STRATEGY_REGISTRY[name][0]
    raise ValueError(f"Unknown strategy: {name}")


def get_strategy_config_class(name: str):
    """Get strategy config class by name."""
    if name in STRATEGY_REGISTRY:
        return STRATEGY_REGISTRY[name][1]
    raise ValueError(f"Unknown strategy: {name}")


def create_strategy(name: str, config=None):
    """Create a strategy instance by name."""
    strategy_class = get_strategy_class(name)
    config_class = get_strategy_config_class(name)

    if config is None:
        config = config_class()
    elif isinstance(config, dict):
        config = config_class(**config)

    return strategy_class(config)


def create_all_strategies(configs: dict = None) -> list:
    """Create all default strategies with optional config overrides.

    Args:
        configs: Dict mapping strategy name to config dict.

    Returns:
        List of initialized strategy instances.
    """
    strategies = []
    configs = configs or {}

    for name in DEFAULT_STRATEGIES:
        try:
            config_dict = configs.get(name, {})
            strategy = create_strategy(name, config_dict)
            strategies.append(strategy)
        except Exception as e:
            logger.error(f"Failed to create strategy {name}: {e}")

    return strategies


__all__ = [
    "STRATEGY_REGISTRY",
    "DEFAULT_STRATEGIES",
    "get_strategy_class",
    "get_strategy_config_class",
    "create_strategy",
    "create_all_strategies",
    # Strategy classes
    "EMACrossoverStrategy", "EMACrossoverConfig",
    "SupertrendStrategy", "SupertrendConfig",
    "VWAPReversionStrategy", "VWAPReversionConfig",
    "BollingerBreakoutStrategy", "BollingerBreakoutConfig",
    "RSIMeanReversionStrategy", "RSIMeanReversionConfig",
    "MACDMomentumStrategy", "MACDMomentumConfig",
    "DonchianBreakoutStrategy", "DonchianBreakoutConfig",
    "KeltnerTrendStrategy", "KeltnerTrendConfig",
    "StochRSIReversionStrategy", "StochRSIReversionConfig",
    "MultiTimeframeTrendStrategy", "MultiTimeframeTrendConfig",
]