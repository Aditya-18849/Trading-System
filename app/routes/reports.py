"""
Reports API — P&L, Performance & Risk Summaries
=================================================
Read-only endpoints for the admin dashboard providing daily P&L,
historical P&L trends, per-strategy performance breakdowns, and
real-time risk status.  All date/time calculations use IST (UTC+05:30).
"""

from collections import defaultdict
from datetime import datetime, timezone, timedelta, date
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, cast, Date
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Trade, Strategy
from app.risk_engine import RiskEngine

router = APIRouter(prefix="/reports", tags=["Reports"])

# IST timezone offset (UTC+05:30)
IST = timezone(timedelta(hours=5, minutes=30))


# ── Helper: fetch and validate user ───────────────────────────────────────

def _get_user_or_404(db: Session, user_id: UUID) -> User:
    """Fetch a user by UUID or raise a 404 HTTPException."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


# ═══════════════════════════════════════════════════════════════════════════
# DAILY P&L
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/daily-pnl")
def daily_pnl(
    user_id: UUID = Query(..., description="User UUID (required)"),
    date: Optional[str] = Query(None, description="Date in YYYY-MM-DD format (IST). Defaults to today."),
    db: Session = Depends(get_db),
):
    """Daily P&L report for a given user and date.

    Returns a summary including trade count, total P&L, winning/losing
    trade breakdown, and win rate. If ``date`` is omitted, today's date
    in IST is used.

    Query Parameters:
        user_id: The user's UUID (required).
        date: Optional date string (YYYY-MM-DD). Defaults to today IST.

    Returns:
        JSON with ``date``, ``trade_count``, ``total_pnl``,
        ``winning_trades``, ``losing_trades``, ``win_rate``, and ``capital``.
    """
    user = _get_user_or_404(db, user_id)

    # Resolve target date
    if date:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date format. Use YYYY-MM-DD.",
            )
    else:
        target_date = datetime.now(IST).date()

    # Fetch all trades for this user on the target date
    trades = (
        db.query(Trade)
        .filter(
            Trade.user_id == user_id,
            cast(Trade.opened_at, Date) == target_date,
        )
        .all()
    )

    trade_count = len(trades)
    total_pnl = sum(float(t.pnl or 0) for t in trades)
    winning_trades = sum(1 for t in trades if float(t.pnl or 0) > 0)
    losing_trades = sum(1 for t in trades if float(t.pnl or 0) < 0)
    win_rate = round((winning_trades / trade_count * 100), 2) if trade_count > 0 else 0.0

    return {
        "date": target_date.isoformat(),
        "trade_count": trade_count,
        "total_pnl": round(total_pnl, 2),
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": win_rate,
        "capital": float(user.total_capital or 0),
    }


# ═══════════════════════════════════════════════════════════════════════════
# P&L HISTORY
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/pnl-history")
def pnl_history(
    user_id: UUID = Query(..., description="User UUID (required)"),
    days: int = Query(30, ge=1, le=365, description="Number of days to look back"),
    db: Session = Depends(get_db),
):
    """P&L history over time for a given user.

    Returns a list of daily P&L summaries for the last ``days`` trading
    days, grouped by IST date.  Each entry includes trade count, daily
    P&L, and a running cumulative P&L.

    Query Parameters:
        user_id: The user's UUID (required).
        days: Number of days to look back (default: 30, max: 365).

    Returns:
        JSON list of daily summaries sorted chronologically, each with
        ``date``, ``trade_count``, ``daily_pnl``, and ``cumulative_pnl``.
    """
    user = _get_user_or_404(db, user_id)

    today_ist = datetime.now(IST).date()
    start_date = today_ist - timedelta(days=days)

    # Aggregate P&L grouped by date using database-level grouping
    results = (
        db.query(
            cast(Trade.opened_at, Date).label("trade_date"),
            func.count(Trade.id).label("trade_count"),
            func.coalesce(func.sum(Trade.pnl), 0).label("daily_pnl"),
        )
        .filter(
            Trade.user_id == user_id,
            cast(Trade.opened_at, Date) >= start_date,
            cast(Trade.opened_at, Date) <= today_ist,
        )
        .group_by(cast(Trade.opened_at, Date))
        .order_by(cast(Trade.opened_at, Date).asc())
        .all()
    )

    # Build response with cumulative P&L
    history = []
    cumulative_pnl = 0.0
    for row in results:
        daily_pnl = float(row.daily_pnl)
        cumulative_pnl += daily_pnl
        history.append({
            "date": row.trade_date.isoformat() if hasattr(row.trade_date, "isoformat") else str(row.trade_date),
            "trade_count": int(row.trade_count),
            "daily_pnl": round(daily_pnl, 2),
            "cumulative_pnl": round(cumulative_pnl, 2),
        })

    return history


# ═══════════════════════════════════════════════════════════════════════════
# STRATEGY PERFORMANCE
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/strategy-performance")
def strategy_performance(
    user_id: UUID = Query(..., description="User UUID (required)"),
    days: int = Query(30, ge=1, le=365, description="Number of days to look back"),
    db: Session = Depends(get_db),
):
    """Per-strategy performance breakdown for a given user.

    Groups trades by strategy over the specified lookback period and
    computes aggregate statistics per strategy.

    Query Parameters:
        user_id: The user's UUID (required).
        days: Number of days to look back (default: 30, max: 365).

    Returns:
        JSON list with per-strategy metrics: ``strategy_id``,
        ``strategy_name``, ``trade_count``, ``total_pnl``, ``avg_pnl``,
        ``winning_trades``, ``losing_trades``, and ``win_rate``.
    """
    user = _get_user_or_404(db, user_id)

    today_ist = datetime.now(IST).date()
    start_date = today_ist - timedelta(days=days)

    # Fetch all trades in the window joined with strategy name
    trades = (
        db.query(Trade, Strategy.name.label("strategy_name"))
        .join(Strategy, Trade.strategy_id == Strategy.id)
        .filter(
            Trade.user_id == user_id,
            cast(Trade.opened_at, Date) >= start_date,
            cast(Trade.opened_at, Date) <= today_ist,
        )
        .all()
    )

    # Group by strategy
    strategy_stats: dict[str, dict] = defaultdict(lambda: {
        "strategy_id": "",
        "strategy_name": "",
        "trade_count": 0,
        "total_pnl": 0.0,
        "winning_trades": 0,
        "losing_trades": 0,
    })

    for trade, strategy_name in trades:
        sid = str(trade.strategy_id)
        entry = strategy_stats[sid]
        entry["strategy_id"] = sid
        entry["strategy_name"] = strategy_name
        entry["trade_count"] += 1

        pnl = float(trade.pnl or 0)
        entry["total_pnl"] += pnl
        if pnl > 0:
            entry["winning_trades"] += 1
        elif pnl < 0:
            entry["losing_trades"] += 1

    # Compute derived metrics
    result = []
    for stats in strategy_stats.values():
        tc = stats["trade_count"]
        avg_pnl = round(stats["total_pnl"] / tc, 2) if tc > 0 else 0.0
        win_rate = round((stats["winning_trades"] / tc * 100), 2) if tc > 0 else 0.0
        result.append({
            "strategy_id": stats["strategy_id"],
            "strategy_name": stats["strategy_name"],
            "trade_count": tc,
            "total_pnl": round(stats["total_pnl"], 2),
            "avg_pnl": avg_pnl,
            "winning_trades": stats["winning_trades"],
            "losing_trades": stats["losing_trades"],
            "win_rate": win_rate,
        })

    # Sort by total P&L descending (best-performing first)
    result.sort(key=lambda x: x["total_pnl"], reverse=True)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# RISK SUMMARY
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/risk-summary")
def risk_summary(
    user_id: UUID = Query(..., description="User UUID (required)"),
    db: Session = Depends(get_db),
):
    """Current risk status for a given user.

    Instantiates the ``RiskEngine`` to compute today's real-time risk
    metrics including daily P&L, trade count, remaining capacity, and
    whether a post-loss cooldown is currently active.

    Query Parameters:
        user_id: The user's UUID (required).

    Returns:
        JSON with ``daily_pnl``, ``trades_today``, ``max_trades``,
        ``pnl_limit``, ``cooldown_active``, ``cooldown_remaining_minutes``,
        and ``remaining_capacity``.
    """
    user = _get_user_or_404(db, user_id)

    risk = RiskEngine(db, user)
    daily = risk.get_daily_summary()

    cooldown_remaining = risk._get_cooldown_remaining()

    return {
        "daily_pnl": round(daily["total_pnl"], 2),
        "trades_today": daily["trade_count"],
        "max_trades": risk.max_trades_per_day,
        "pnl_limit": round(-risk.max_daily_loss, 2),
        "cooldown_active": cooldown_remaining is not None,
        "cooldown_remaining_minutes": cooldown_remaining or 0,
        "remaining_capacity": max(0, risk.max_trades_per_day - daily["trade_count"]),
        "capital": daily["capital"],
    }
