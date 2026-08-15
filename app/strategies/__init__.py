"""
Advanced Strategy Engine package.

Provides technical-indicator-based signal generation (MA crossover,
mean reversion, momentum + trend filters) with structured Pydantic
output consumed by the risk engine and order manager.
"""

from app.strategies.advanced_engine import (  # noqa: F401
    AdvancedStrategyEngine,
    StrategySignal,
    SignalAction,
)
