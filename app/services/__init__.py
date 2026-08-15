"""
Services package — automated order execution and risk management.

Exports:
    - ``OrderExecutor``    — end-to-end signal-to-order execution engine.
    - ``ExecutionResult``  — structured result of an execution attempt.
    - ``RiskManager``      — StrategySignal-aware risk validation façade.
    - ``RiskDecision``     — risk evaluation outcome model.
    - ``AuthService``      — automated daily OAuth + TOTP token refresher.
    - ``PositionMonitor``  — real-time position monitoring & exit automation.
"""

from app.services.executor import ExecutionResult, OrderExecutor
from app.services.risk_manager import RiskDecision, RiskManager
from app.services.auth import AuthService
from app.services.monitor import PositionMonitor

__all__ = [
    "OrderExecutor",
    "ExecutionResult",
    "RiskManager",
    "RiskDecision",
    "AuthService",
    "PositionMonitor",
]

