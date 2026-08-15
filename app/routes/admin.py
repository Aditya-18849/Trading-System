"""
Admin Dashboard API — User, Strategy & Trade Management
========================================================
CRUD endpoints for managing users, strategies, and viewing trades.
All endpoints are prefixed with ``/admin`` and designed for internal
dashboard consumption (not exposed to TradingView or external callers).
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Strategy, Trade, Order

router = APIRouter(prefix="/admin", tags=["Admin"])


# ── Pydantic Schemas ───────────────────────────────────────────────────────

# --- User Schemas ---

class UserCreate(BaseModel):
    """Schema for creating a new user."""
    full_name: str
    email: str
    broker: str = "zerodha"
    broker_client_id: str
    total_capital: float = 100000
    risk_per_trade_pct: float = 1.0
    max_daily_loss: float = 1000
    max_trades_per_day: int = 3
    cooldown_minutes: int = 20


class UserUpdate(BaseModel):
    """Schema for partially updating an existing user."""
    full_name: Optional[str] = None
    total_capital: Optional[float] = None
    risk_per_trade_pct: Optional[float] = None
    max_daily_loss: Optional[float] = None
    max_trades_per_day: Optional[int] = None
    cooldown_minutes: Optional[int] = None
    is_active: Optional[bool] = None


class UserResponse(BaseModel):
    """Serialised user returned by the API."""
    id: str
    full_name: str
    email: str
    broker: str
    broker_client_id: str
    total_capital: float
    risk_per_trade_pct: float
    max_daily_loss: float
    max_trades_per_day: int
    cooldown_minutes: int
    is_active: bool
    created_at: str
    model_config = ConfigDict(from_attributes=True)


# --- Strategy Schemas ---

class StrategyCreate(BaseModel):
    """Schema for registering a new strategy."""
    user_id: str
    name: str
    algo_id: str
    webhook_token: str
    default_stoploss_pct: float = 0.5
    default_target_pct: float = 1.0


class StrategyUpdate(BaseModel):
    """Schema for partially updating an existing strategy."""
    name: Optional[str] = None
    default_stoploss_pct: Optional[float] = None
    default_target_pct: Optional[float] = None
    is_active: Optional[bool] = None


class StrategyResponse(BaseModel):
    """Serialised strategy returned by the API."""
    id: str
    user_id: str
    name: str
    algo_id: str
    webhook_token: str
    default_stoploss_pct: float
    default_target_pct: float
    is_active: bool
    created_at: str
    model_config = ConfigDict(from_attributes=True)


# ── Helper: ORM → Response dict ───────────────────────────────────────────

def _user_to_response(user: User) -> dict:
    """Convert a User ORM instance to a dict suitable for UserResponse."""
    return {
        "id": str(user.id),
        "full_name": user.full_name,
        "email": user.email,
        "broker": user.broker,
        "broker_client_id": user.broker_client_id,
        "total_capital": float(user.total_capital or 0),
        "risk_per_trade_pct": float(user.risk_per_trade_pct or 0),
        "max_daily_loss": float(user.max_daily_loss or 0),
        "max_trades_per_day": int(user.max_trades_per_day or 0),
        "cooldown_minutes": int(user.cooldown_minutes or 0),
        "is_active": user.is_active,
        "created_at": user.created_at.isoformat() if user.created_at else "",
    }


def _strategy_to_response(strategy: Strategy) -> dict:
    """Convert a Strategy ORM instance to a dict suitable for StrategyResponse."""
    return {
        "id": str(strategy.id),
        "user_id": str(strategy.user_id),
        "name": strategy.name,
        "algo_id": strategy.algo_id,
        "webhook_token": strategy.webhook_token,
        "default_stoploss_pct": float(strategy.default_stoploss_pct or 0),
        "default_target_pct": float(strategy.default_target_pct or 0),
        "is_active": strategy.is_active,
        "created_at": strategy.created_at.isoformat() if strategy.created_at else "",
    }


# ═══════════════════════════════════════════════════════════════════════════
# USER ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/users", response_model=List[UserResponse])
def list_users(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
    db: Session = Depends(get_db),
):
    """List all registered users with pagination.

    Returns a paginated list of users ordered by creation date (newest first).
    """
    users = (
        db.query(User)
        .order_by(User.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [_user_to_response(u) for u in users]


@router.get("/users/{user_id}", response_model=UserResponse)
def get_user(user_id: UUID, db: Session = Depends(get_db)):
    """Retrieve a single user by their UUID.

    Raises 404 if the user does not exist.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return _user_to_response(user)


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, db: Session = Depends(get_db)):
    """Create a new user.

    The email must be unique; a 400 error is returned if a duplicate is detected.
    """
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"User with email '{payload.email}' already exists",
        )

    user = User(**payload.model_dump())
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_to_response(user)


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(user_id: UUID, payload: UserUpdate, db: Session = Depends(get_db)):
    """Partially update a user's fields.

    Only the fields present in the request body are updated; all others
    remain unchanged.  Returns 404 if the user does not exist.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(user, field, value)

    db.commit()
    db.refresh(user)
    return _user_to_response(user)


@router.delete("/users/{user_id}", response_model=UserResponse)
def delete_user(user_id: UUID, db: Session = Depends(get_db)):
    """Soft-delete a user by setting ``is_active`` to False.

    The record is not physically removed from the database. Returns 404
    if the user does not exist.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.is_active = False
    db.commit()
    db.refresh(user)
    return _user_to_response(user)


# ═══════════════════════════════════════════════════════════════════════════
# STRATEGY ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/strategies", response_model=List[StrategyResponse])
def list_strategies(
    user_id: Optional[UUID] = Query(None, description="Filter strategies by user UUID"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
    db: Session = Depends(get_db),
):
    """List all strategies, optionally filtered by user.

    Supports pagination via ``skip`` and ``limit`` query parameters.
    When ``user_id`` is provided, only that user's strategies are returned.
    """
    query = db.query(Strategy)
    if user_id:
        query = query.filter(Strategy.user_id == user_id)
    strategies = (
        query
        .order_by(Strategy.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )
    return [_strategy_to_response(s) for s in strategies]


@router.get("/strategies/{strategy_id}", response_model=StrategyResponse)
def get_strategy(strategy_id: UUID, db: Session = Depends(get_db)):
    """Retrieve a single strategy by its UUID.

    Raises 404 if the strategy does not exist.
    """
    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
    return _strategy_to_response(strategy)


@router.post("/strategies", response_model=StrategyResponse, status_code=status.HTTP_201_CREATED)
def create_strategy(payload: StrategyCreate, db: Session = Depends(get_db)):
    """Register a new strategy for a user.

    Validates that the referenced user exists. The ``webhook_token`` must
    be unique across all strategies; a 400 error is returned on duplicates.
    """
    # Validate that the user exists
    user = db.query(User).filter(User.id == UUID(payload.user_id)).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{payload.user_id}' not found",
        )

    # Ensure webhook_token uniqueness
    existing = db.query(Strategy).filter(Strategy.webhook_token == payload.webhook_token).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Strategy with webhook_token '{payload.webhook_token}' already exists",
        )

    strategy = Strategy(**payload.model_dump())
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    return _strategy_to_response(strategy)


@router.patch("/strategies/{strategy_id}", response_model=StrategyResponse)
def update_strategy(strategy_id: UUID, payload: StrategyUpdate, db: Session = Depends(get_db)):
    """Partially update a strategy's fields.

    Only the fields present in the request body are updated; all others
    remain unchanged.  Returns 404 if the strategy does not exist.
    """
    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(strategy, field, value)

    db.commit()
    db.refresh(strategy)
    return _strategy_to_response(strategy)


@router.delete("/strategies/{strategy_id}", response_model=StrategyResponse)
def delete_strategy(strategy_id: UUID, db: Session = Depends(get_db)):
    """Soft-delete a strategy by setting ``is_active`` to False.

    The record is not physically removed from the database. Returns 404
    if the strategy does not exist.
    """
    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")

    strategy.is_active = False
    db.commit()
    db.refresh(strategy)
    return _strategy_to_response(strategy)


# ═══════════════════════════════════════════════════════════════════════════
# TRADE ENDPOINTS (read-only)
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/trades")
def list_trades(
    user_id: Optional[UUID] = Query(None, description="Filter by user UUID"),
    symbol: Optional[str] = Query(None, description="Filter by trading symbol (e.g. RELIANCE)"),
    trade_status: Optional[str] = Query(None, alias="status", description="Filter by trade status (OPEN, CLOSED, etc.)"),
    date_from: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD, IST)"),
    date_to: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD, IST)"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
    db: Session = Depends(get_db),
):
    """List trades with optional filters.

    Supports filtering by ``user_id``, ``symbol``, ``status``, and date
    range (``date_from`` / ``date_to`` in YYYY-MM-DD format, interpreted
    as IST). Results are paginated and ordered newest-first.
    """
    from sqlalchemy import cast, Date

    query = db.query(Trade)

    if user_id:
        query = query.filter(Trade.user_id == user_id)
    if symbol:
        query = query.filter(Trade.symbol == symbol.strip().upper())
    if trade_status:
        query = query.filter(Trade.status == trade_status.strip().upper())
    if date_from:
        query = query.filter(cast(Trade.opened_at, Date) >= datetime.strptime(date_from, "%Y-%m-%d").date())
    if date_to:
        query = query.filter(cast(Trade.opened_at, Date) <= datetime.strptime(date_to, "%Y-%m-%d").date())

    trades = (
        query
        .order_by(Trade.opened_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    return [
        {
            "id": str(t.id),
            "user_id": str(t.user_id),
            "strategy_id": str(t.strategy_id),
            "symbol": t.symbol,
            "exchange": t.exchange,
            "direction": t.direction,
            "quantity": t.quantity,
            "entry_price": float(t.entry_price or 0),
            "stoploss_price": float(t.stoploss_price or 0),
            "target_price": float(t.target_price or 0),
            "exit_price": float(t.exit_price or 0),
            "pnl": float(t.pnl or 0),
            "status": t.status,
            "algo_id": t.algo_id,
            "opened_at": t.opened_at.isoformat() if t.opened_at else None,
            "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        }
        for t in trades
    ]


@router.get("/trades/{trade_id}")
def get_trade(trade_id: UUID, db: Session = Depends(get_db)):
    """Retrieve a single trade with all associated orders.

    Returns the full trade detail including an ``orders`` array containing
    every order placed for this trade. Raises 404 if the trade does not exist.
    """
    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade not found")

    orders = db.query(Order).filter(Order.trade_id == trade.id).order_by(Order.created_at.asc()).all()

    return {
        "id": str(trade.id),
        "user_id": str(trade.user_id),
        "strategy_id": str(trade.strategy_id),
        "symbol": trade.symbol,
        "exchange": trade.exchange,
        "direction": trade.direction,
        "quantity": trade.quantity,
        "entry_price": float(trade.entry_price or 0),
        "stoploss_price": float(trade.stoploss_price or 0),
        "target_price": float(trade.target_price or 0),
        "exit_price": float(trade.exit_price or 0),
        "pnl": float(trade.pnl or 0),
        "status": trade.status,
        "algo_id": trade.algo_id,
        "opened_at": trade.opened_at.isoformat() if trade.opened_at else None,
        "closed_at": trade.closed_at.isoformat() if trade.closed_at else None,
        "orders": [
            {
                "id": str(o.id),
                "broker_order_id": o.broker_order_id,
                "order_type": o.order_type,
                "transaction_type": o.transaction_type,
                "product": o.product,
                "quantity": o.quantity,
                "price": float(o.price or 0),
                "trigger_price": float(o.trigger_price or 0),
                "status": o.status,
                "algo_id": o.algo_id,
                "raw_response": o.raw_response,
                "created_at": o.created_at.isoformat() if o.created_at else None,
            }
            for o in orders
        ],
    }
