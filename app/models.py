import uuid
from datetime import datetime

from sqlalchemy import (
    Column, String, Numeric, Integer, Boolean, Float,
    ForeignKey, DateTime, JSON, Text, text, Index, Date
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class User(Base):
    """Trader account with broker credentials, risk parameters, and capital.

    Each user is linked to a single broker (Zerodha/Angel One) via
    ``broker`` and ``broker_client_id``.  Risk parameters can be
    overridden per-user; if ``None`` or zero, the global defaults from
    ``app.config.settings`` are used by the :class:`RiskEngine`.
    """
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    full_name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    broker = Column(String(50), nullable=False, default="zerodha")
    broker_client_id = Column(String(50), nullable=False)
    kite_access_token = Column(Text)
    kite_token_generated_at = Column(DateTime(timezone=True))
    total_capital = Column(Numeric(14, 2), default=0)
    risk_per_trade_pct = Column(Numeric(5, 2), default=1.0)
    max_daily_loss = Column(Numeric(14, 2), default=1000)
    max_trades_per_day = Column(Integer, default=3)
    cooldown_minutes = Column(Integer, default=20)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))

    strategies = relationship("Strategy", back_populates="user")
    trades = relationship("Trade", back_populates="user")


class Strategy(Base):
    """Registered TradingView webhook strategy with SEBI Algo-ID.

    Each strategy has a unique ``webhook_token`` used to correlate
    incoming TradingView alerts with the correct user and risk params.
    The ``algo_id`` is the exchange-assigned algorithm identifier
    mandated by SEBI for every automated order.
    """
    __tablename__ = "strategies"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    algo_id = Column(String(100), nullable=False)  # exchange-assigned Algo-ID
    broker_strategy_ref = Column(String(255))
    webhook_token = Column(String(255), unique=True, nullable=False)
    default_stoploss_pct = Column(Numeric(5, 2), default=0.5)
    default_target_pct = Column(Numeric(5, 2), default=1.0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))

    user = relationship("User", back_populates="strategies")


class Trade(Base):
    """Trade lifecycle record tracking entry, exit, and P&L.

    Status transitions::

        PENDING → OPEN → CLOSED | CANCELLED

    A trade is created as ``OPEN`` when a webhook is accepted and orders
    are placed.  It transitions to ``CLOSED`` when an exit leg (SL or
    target) fills, or ``CANCELLED`` if the entry is rejected.
    """
    __tablename__ = "trades"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    strategy_id = Column(UUID(as_uuid=True), ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False)
    symbol = Column(String(50), nullable=False)
    exchange = Column(String(10), default="NSE")
    direction = Column(String(4), nullable=False)  # BUY / SELL
    quantity = Column(Integer, nullable=False)
    entry_price = Column(Numeric(14, 2))
    stoploss_price = Column(Numeric(14, 2))
    target_price = Column(Numeric(14, 2))
    exit_price = Column(Numeric(14, 2))
    pnl = Column(Numeric(14, 2), default=0)
    status = Column(String(20), default="OPEN")  # OPEN / CLOSED / CANCELLED
    algo_id = Column(String(100), nullable=False)  # SEBI Algo-ID tag
    opened_at = Column(DateTime(timezone=True), server_default=text("now()"))
    closed_at = Column(DateTime(timezone=True))

    # Trailing stop-loss state (managed by PositionMonitor)
    trailing_sl_price = Column(Numeric(14, 2))              # Current trailing SL level
    trailing_sl_activated = Column(Boolean, default=False)   # Whether trailing has been activated
    highest_price_since_entry = Column(Numeric(14, 2))       # High watermark (BUY trades)
    lowest_price_since_entry = Column(Numeric(14, 2))        # Low watermark (SELL trades)

    user = relationship("User", back_populates="trades")
    orders = relationship("Order", back_populates="trade")


class Order(Base):
    """Individual broker order linked to a parent trade.

    Status transitions::

        PENDING → PLACED → COMPLETE | REJECTED | CANCELLED

    Every order carries the SEBI ``algo_id`` tag and stores the raw
    broker response for post-trade audit.
    """
    __tablename__ = "orders"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    trade_id = Column(UUID(as_uuid=True), ForeignKey("trades.id", ondelete="CASCADE"), nullable=False)
    broker_order_id = Column(String(100))
    order_type = Column(String(20), nullable=False)  # ENTRY / STOPLOSS / TARGET / EXIT / MODIFY / CANCEL
    transaction_type = Column(String(4), nullable=False)  # BUY / SELL
    product = Column(String(10), default="MIS")  # MIS / CNC / NRML
    quantity = Column(Integer, nullable=False)
    price = Column(Numeric(14, 2))
    trigger_price = Column(Numeric(14, 2))
    status = Column(String(20), default="PENDING")  # PENDING / PLACED / COMPLETE / REJECTED / CANCELLED
    algo_id = Column(String(100), nullable=False)  # SEBI Algo-ID tag
    raw_response = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))

    trade = relationship("Trade", back_populates="orders")


class Log(Base):
    """Structured application log entry persisted to the database.

    Severity levels follow Python's logging convention:
    ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``, ``CRITICAL``.
    """
    __tablename__ = "logs"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    level = Column(String(10), default="INFO")
    source = Column(String(100), nullable=False)
    message = Column(Text, nullable=False)
    context = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))


class Notification(Base):
    """Outbound notification record for delivery tracking.

    Supports multiple channels (currently Telegram).  The ``delivered``
    flag tracks whether the message was successfully sent.
    """
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    channel = Column(String(50), default="telegram")
    category = Column(String(50), nullable=False)  # TRADE_EXECUTED / ERROR / DAILY_SUMMARY / TOKEN_REFRESH
    payload = Column(JSON, nullable=False)
    delivered = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))


class AuditTrail(Base):
    """Immutable SEBI compliance audit record.

    Every automated action (order placement, modification, cancellation,
    risk decision) generates an audit record.  Records are append-only
    and timestamped with nanosecond-precision IST timestamps.

    This table satisfies the SEBI circular requirement for maintaining
    a complete audit trail of all algorithmic trading activity, including:

    - Order lifecycle events with broker response
    - Risk engine decisions (approve/reject with reason)
    - Signal source and raw webhook payload
    - Exchange-assigned Algo-ID linkage

    Columns are intentionally denormalized for fast export to CSV/JSON
    during regulatory inspections.
    """
    __tablename__ = "audit_trail"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    timestamp = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    algo_id = Column(String(100))  # SEBI-mandated Algo-ID
    action = Column(String(50), nullable=False)  # ORDER_PLACED / ORDER_FILLED / ORDER_REJECTED / RISK_APPROVED / RISK_REJECTED / POSITION_CLOSED / SYSTEM_EVENT
    symbol = Column(String(50))
    exchange = Column(String(10))
    direction = Column(String(4))  # BUY / SELL
    quantity = Column(Integer)
    price = Column(Numeric(14, 2))
    broker_order_id = Column(String(100))
    risk_decision = Column(String(20))  # APPROVED / REJECTED
    risk_reason = Column(Text)  # Reason for rejection (if any)
    raw_payload = Column(JSON)  # Full webhook payload or broker response
    source = Column(String(100))  # WEBHOOK / POSTBACK / EOD_SQUAREOFF / MANUAL
    trade_id = Column(UUID(as_uuid=True), nullable=True)  # Reference to trades table (not FK for immutability)
    order_id = Column(UUID(as_uuid=True), nullable=True)  # Reference to orders table (not FK for immutability)
    ip_address = Column(String(45))  # Client IP for the triggering request
    extra = Column(JSON)  # Any additional context data

    __table_args__ = (
        Index("idx_audit_user_date", "user_id", "timestamp"),
        Index("idx_audit_action", "action", "timestamp"),
        Index("idx_audit_algo_id", "algo_id"),
        Index("idx_audit_symbol", "symbol", "timestamp"),
    )


class MarketData(Base):
    """Raw OHLCV candle data for watched instruments.

    Persisted by the market data fetcher at configurable intervals.
    Used by regime detection and strategy evaluation.
    """
    __tablename__ = "market_data"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    symbol = Column(String(50), nullable=False)
    exchange = Column(String(10), default="NSE", nullable=False)
    interval = Column(String(10), nullable=False)  # 1m, 5m, 15m, 1h, 1d
    open = Column(Numeric(14, 2), nullable=False)
    high = Column(Numeric(14, 2), nullable=False)
    low = Column(Numeric(14, 2), nullable=False)
    close = Column(Numeric(14, 2), nullable=False)
    volume = Column(Integer, default=0, nullable=False)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    source = Column(String(50), default="kite", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))

    __table_args__ = (
        Index("idx_market_data_symbol_interval_ts", "symbol", "exchange", "interval", "timestamp"),
        Index("uq_market_data_candle", "symbol", "exchange", "interval", "timestamp", unique=True),
    )


class RegimeSnapshot(Base):
    """Market regime classification per instrument at a point in time.

    Generated by the regime detector; consumed by strategy engine and dashboard.
    """
    __tablename__ = "regime_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    symbol = Column(String(50), nullable=False)
    exchange = Column(String(10), default="NSE", nullable=False)
    regime = Column(String(30), nullable=False)  # trending-up / trending-down / range-bound / high-volatility
    adx = Column(Numeric(8, 2))
    atr = Column(Numeric(14, 2))
    bb_bandwidth = Column(Numeric(8, 4))
    ema_slope = Column(Numeric(14, 6))
    confidence = Column(Numeric(4, 2), default=0.5)
    
    # CHANGED FROM: metadata = Column(JSON)
    snapshot_metadata = Column(JSON)
    
    timestamp = Column(DateTime(timezone=True), server_default=text("now()"), index=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))

    __table_args__ = (
        Index("idx_regime_symbol_ts", "symbol", "exchange", "timestamp"),
    )


class StrategySignal(Base):
    """Raw signal emitted by a strategy before ranking/risk adjustment.

    Persisted for audit trail and performance analysis.
    """
    __tablename__ = "strategy_signals"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    strategy_name = Column(String(100), nullable=False)
    symbol = Column(String(50), nullable=False)
    exchange = Column(String(10), default="NSE", nullable=False)
    action = Column(String(10), nullable=False)  # BUY / SELL / HOLD / EXIT
    confidence_score = Column(Numeric(4, 2), nullable=False)
    entry_price = Column(Numeric(14, 2))
    stoploss_price = Column(Numeric(14, 2))
    target_price = Column(Numeric(14, 2))
    suggested_quantity = Column(Integer)
    regime_at_signal = Column(String(30))
    
    # CHANGED FROM: metadata = Column(JSON)
    signal_metadata = Column(JSON)
    
    timestamp = Column(DateTime(timezone=True), server_default=text("now()"), index=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))

    __table_args__ = (
        Index("idx_strategy_signals_strategy_ts", "strategy_name", "timestamp"),
        Index("idx_strategy_signals_symbol_ts", "symbol", "exchange", "timestamp"),
    )


class Recommendation(Base):
    """Risk-adjusted, ranked trade recommendation for dashboard display and execution.

    This is the final output of the strategy engine pipeline.
    """
    __tablename__ = "recommendations"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    symbol = Column(String(50), nullable=False)
    exchange = Column(String(10), default="NSE", nullable=False)
    direction = Column(String(4), nullable=False)  # BUY / SELL
    entry_price = Column(Numeric(14, 2), nullable=False)
    stoploss_price = Column(Numeric(14, 2), nullable=False)
    target_price = Column(Numeric(14, 2), nullable=False)
    quantity = Column(Integer, nullable=False)
    capital_at_risk = Column(Numeric(14, 2), nullable=False)
    risk_reward_ratio = Column(Numeric(6, 2))
    confidence_score = Column(Numeric(4, 2), nullable=False)
    regime = Column(String(30), nullable=False)
    top_strategy_name = Column(String(100), nullable=False)
    ai_summary = Column(Text)
    ai_key_risks = Column(Text)
    ai_invalidation = Column(Text)
    status = Column(String(20), default="PENDING")  # PENDING / EXECUTED / REJECTED / EXPIRED / CANCELLED
    executed_at = Column(DateTime(timezone=True))
    executed_trade_id = Column(UUID(as_uuid=True))
    expires_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), server_default=text("now()"))

    __table_args__ = (
        Index("idx_recommendations_status_ts", "status", "created_at"),
        Index("idx_recommendations_symbol_ts", "symbol", "exchange", "created_at"),
    )


class PerformanceSnapshot(Base):
    """Daily equity curve and per-strategy performance metrics.

    One row per user per strategy per symbol per day (NULL strategy/symbol = portfolio aggregate).
    """
    __tablename__ = "performance_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    strategy_name = Column(String(100))  # NULL = portfolio aggregate
    symbol = Column(String(50))  # NULL = aggregate across symbols
    date = Column(Date, nullable=False)
    starting_capital = Column(Numeric(14, 2), nullable=False)
    ending_capital = Column(Numeric(14, 2), nullable=False)
    realized_pnl = Column(Numeric(14, 2), default=0)
    unrealized_pnl = Column(Numeric(14, 2), default=0)
    trades_count = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    max_drawdown = Column(Numeric(14, 2), default=0)
    max_drawdown_pct = Column(Numeric(6, 2), default=0)
    sharpe_ratio = Column(Numeric(6, 3))
    expectancy = Column(Numeric(14, 2))
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), server_default=text("now()"))

    user = relationship("User")

    __table_args__ = (
        Index("idx_perf_user_date", "user_id", "date"),
        Index("idx_perf_strategy_date", "strategy_name", "date"),
        Index("uq_perf_user_strategy_symbol_date", "user_id", "strategy_name", "symbol", "date", unique=True),
    )


class Watchlist(Base):
    """Configurable instrument watchlist for market data fetching.

    Each user can watch multiple symbols with configurable intervals and priority.
    """
    __tablename__ = "watchlist"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    symbol = Column(String(50), nullable=False)
    exchange = Column(String(10), default="NSE", nullable=False)
    instrument_token = Column(Integer)
    intervals = Column(String(100), default="5m,15m,1h")
    is_active = Column(Boolean, default=True)
    priority = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), server_default=text("now()"))

    user = relationship("User")

    __table_args__ = (
        Index("idx_watchlist_user_active", "user_id", "is_active"),
        Index("uq_watchlist_user_symbol_exchange", "user_id", "symbol", "exchange", unique=True),
    )


class DailyReport(Base):
    """Archived end-of-day report with AI-generated narrative.

    Generated by the daily report job at market close.
    """
    __tablename__ = "daily_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    report_date = Column(Date, nullable=False)
    total_pnl = Column(Numeric(14, 2), default=0)
    trades_count = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    max_drawdown = Column(Numeric(14, 2), default=0)
    best_trade_pnl = Column(Numeric(14, 2), default=0)
    worst_trade_pnl = Column(Numeric(14, 2), default=0)
    best_strategy = Column(String(100))
    worst_strategy = Column(String(100))
    regime_summary = Column(JSON)
    strategy_performance = Column(JSON)
    ai_summary = Column(Text)
    telegram_sent = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"))

    user = relationship("User")

    __table_args__ = (
        Index("idx_daily_reports_user_date", "user_id", "report_date"),
        Index("uq_daily_reports_user_date", "user_id", "report_date", unique=True),
    )