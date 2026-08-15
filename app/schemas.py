"""
Pydantic schemas for request/response validation.

- ``TradingViewAlert`` — inbound webhook payload from TradingView.
- ``OrderUpdatePayload`` — broker postback payload (defined in ``order_manager.py``).
- ``RiskCheckResult`` — output of the risk engine evaluation.
- ``WebhookResponse`` — standard webhook response envelope.
- ``AuditRecord`` — schema for creating SEBI audit trail entries.
- ``RegimeResult`` — market regime detection output.
- ``StrategySignal`` — strategy engine signal output.
- ``RankedSignal`` — ranked strategy signal with score.
- ``Recommendation`` — final risk-adjusted recommendation.
- ``PortfolioResponse`` — portfolio dashboard data.
- ``PerformanceResponse`` — performance analytics data.
- ``DailyReportResponse`` — daily report data.
- ``ExecuteRequest`` — manual execution request.
"""
from enum import Enum
from typing import Literal, Optional, List
from uuid import UUID
from datetime import date, datetime, timezone, timedelta

from pydantic import BaseModel, Field, field_validator


class TradingViewAlert(BaseModel):
    """
    Expected TradingView alert JSON body. Configure the alert message in
    TradingView as raw JSON, e.g.:

    {
      "secret": "{{strategy.order.alert_message_secret}}",   // or a hardcoded shared secret
      "webhook_token": "your-per-strategy-token",
      "symbol": "{{ticker}}",
      "exchange": "NSE",
      "direction": "BUY",
      "quantity": 1,
      "price": {{close}},
      "signal_id": "{{strategy.order.id}}"
    }
    """

    secret: str = Field(..., description="Shared secret embedded in the alert JSON — validated before anything else runs")
    webhook_token: str = Field(..., description="Per-strategy token identifying which registered strategy fired")
    symbol: str
    exchange: str = "NSE"
    direction: Literal["BUY", "SELL"]
    quantity: Optional[int] = None  # if omitted, risk engine computes position size
    price: Optional[float] = None   # last traded price hint from TradingView; broker LTP is authoritative
    signal_id: Optional[str] = None

    @field_validator("symbol")
    @classmethod
    def upper_symbol(cls, v: str) -> str:
        return v.strip().upper()


class RiskCheckResult(BaseModel):
    """Output of the risk engine evaluation."""
    approved: bool
    reason: Optional[str] = None
    quantity: Optional[int] = None
    stoploss_price: Optional[float] = None
    target_price: Optional[float] = None


class WebhookResponse(BaseModel):
    """Standard response envelope for webhook endpoints."""
    status: Literal["EXECUTED", "REJECTED", "ERROR"]
    detail: str
    trade_id: Optional[str] = None


class AuditRecord(BaseModel):
    """Schema for creating a SEBI audit trail entry.

    Used by the ``AuditLogger`` to validate data before writing to the
    ``audit_trail`` table.  All fields are optional except ``action``
    because different event types populate different columns.
    """
    action: str  # ORDER_PLACED / ORDER_FILLED / RISK_APPROVED / RISK_REJECTED / etc.
    user_id: Optional[UUID] = None
    algo_id: Optional[str] = None
    symbol: Optional[str] = None
    exchange: Optional[str] = None
    direction: Optional[str] = None
    quantity: Optional[int] = None
    price: Optional[float] = None
    broker_order_id: Optional[str] = None
    risk_decision: Optional[str] = None
    risk_reason: Optional[str] = None
    raw_payload: Optional[dict] = None
    source: Optional[str] = None
    trade_id: Optional[UUID] = None
    order_id: Optional[UUID] = None
    ip_address: Optional[str] = None
    extra: Optional[dict] = None


# =============================================================================
# Strategy Engine Schemas
# =============================================================================

class RegimeType(str, Enum):
    """Market regime classification."""
    TRENDING_UP = "trending-up"
    TRENDING_DOWN = "trending-down"
    RANGE_BOUND = "range-bound"
    HIGH_VOLATILITY = "high-volatility"


class RegimeResult(BaseModel):
    """Market regime detection output."""
    symbol: str
    exchange: str
    regime: RegimeType
    adx: Optional[float] = None
    atr: Optional[float] = None
    bb_bandwidth: Optional[float] = None
    ema_slope: Optional[float] = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    metadata: dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=5, minutes=30))))


class StrategySignal(BaseModel):
    """Signal emitted by a strategy after evaluation."""
    symbol: str
    exchange: str = "NSE"
    action: Literal["BUY", "SELL", "HOLD", "EXIT"]
    confidence_score: float = Field(ge=0.0, le=1.0)
    entry_price: Optional[float] = None
    stoploss_price: Optional[float] = None
    target_price: Optional[float] = None
    suggested_quantity: Optional[int] = None
    strategy_name: str
    regime_at_signal: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=5, minutes=30))))

    @field_validator("symbol")
    @classmethod
    def upper_symbol(cls, v: str) -> str:
        return v.strip().upper()


class RankedSignal(BaseModel):
    """Strategy signal with ranking score."""
    signal: StrategySignal
    rank_score: float = Field(ge=0.0)
    historical_performance: Optional[float] = None
    regime_fit: Optional[float] = None


class Recommendation(BaseModel):
    """Final risk-adjusted trade recommendation."""
    id: Optional[UUID] = None
    symbol: str
    exchange: str = "NSE"
    direction: Literal["BUY", "SELL"]
    entry_price: float
    stoploss_price: float
    target_price: float
    quantity: int
    capital_at_risk: float
    risk_reward_ratio: Optional[float] = None
    confidence_score: float = Field(ge=0.0, le=1.0)
    regime: str
    top_strategy_name: str
    ai_summary: Optional[str] = None
    ai_key_risks: Optional[str] = None
    ai_invalidation: Optional[str] = None
    status: Literal["PENDING", "EXECUTED", "REJECTED", "EXPIRED", "CANCELLED"] = "PENDING"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=5, minutes=30))))
    expires_at: Optional[datetime] = None


class PortfolioPosition(BaseModel):
    """Single position in portfolio view."""
    symbol: str
    exchange: str
    quantity: int
    avg_price: float
    ltp: float
    unrealized_pnl: float
    product: str


class PortfolioResponse(BaseModel):
    """Portfolio dashboard response."""
    positions: List[PortfolioPosition]
    total_capital: float
    deployed_capital: float
    available_margin: float
    total_unrealized_pnl: float
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone(timedelta(hours=5, minutes=30))))


class PerformanceMetrics(BaseModel):
    """Per-strategy performance metrics."""
    strategy_name: str
    total_pnl: float
    trades_count: int
    wins: int
    losses: int
    win_rate: float
    expectancy: float
    max_drawdown: float
    max_drawdown_pct: float
    sharpe_ratio: Optional[float] = None


class PerformanceResponse(BaseModel):
    """Performance dashboard response."""
    equity_curve: List[dict]  # [{date, equity, pnl}]
    daily_pnl: List[dict]     # [{date, pnl}]
    strategy_metrics: List[PerformanceMetrics]
    portfolio_metrics: dict


class DailyReportResponse(BaseModel):
    """Daily report response."""
    report_date: date
    total_pnl: float
    trades_count: int
    wins: int
    losses: int
    max_drawdown: float
    best_trade_pnl: float
    worst_trade_pnl: float
    best_strategy: Optional[str] = None
    worst_strategy: Optional[str] = None
    regime_summary: dict
    strategy_performance: dict
    ai_summary: Optional[str] = None
    created_at: datetime


class ExecuteRequest(BaseModel):
    """Manual execution request from dashboard."""
    recommendation_id: UUID
    confirm: bool = False
