"""
Dashboard Router — API endpoints for the Portfolio Dashboard.

Provides:
- GET /api/portfolio — Current positions, capital, live P&L
- GET /api/recommendations — Latest ranked recommendations with AI summary
- GET /api/regime — Current market regime per watched instrument
- POST /api/execute — Manual execution of a recommendation
- GET /api/performance — Equity curve, win rate, per-strategy metrics, drawdown
- GET /api/daily-report — Latest daily report
- WS /ws/live — WebSocket for live P&L/position updates
"""

import logging
import uuid
from datetime import date, datetime, timezone, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.config import settings
from app.database import get_db
from app.models import (
    User, Trade, Order, Recommendation as RecModel,
    MarketData, RegimeSnapshot, PerformanceSnapshot, Watchlist
)
from app.schemas import (
    PortfolioResponse, PortfolioPosition, Recommendation,
    PerformanceResponse, PerformanceMetrics, DailyReportResponse,
    ExecuteRequest, RegimeResult, RegimeType
)
from app.broker.kite import KiteAdapter
from app.broker.kite_data import KiteDataFetcher
from app.risk_engine import RiskEngine
from app.analytics.performance import get_performance_analytics
from app.strategy_engine.recommender import get_recommender
from app.strategy_engine.regime import get_regime_detector
from app.strategy_engine.ranker import get_ranker
from app.strategy_engine.strategies import create_all_strategies

logger = logging.getLogger(__name__)

# IST timezone
IST = timezone(timedelta(hours=5, minutes=30))

router = APIRouter(prefix="/api", tags=["Dashboard"])


# ---------------------------------------------------------------------------
# Dependency: Get current user (in production, use auth)
# ---------------------------------------------------------------------------

def get_current_user(db: Session = Depends(get_db)) -> User:
    """Get the primary active user (for single-user deployment)."""
    user = db.query(User).filter(
        User.broker_client_id == settings.kite_user_id,
        User.is_active == True,
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="Active user not found")
    return user


def get_broker_adapter(user: User = Depends(get_current_user)) -> KiteAdapter:
    """Get broker adapter for the current user."""
    from app.main import _get_broker
    return _get_broker(user)


def get_kite_fetcher(user: User = Depends(get_current_user)) -> KiteDataFetcher:
    """Get Kite data fetcher for the current user."""
    if not user.kite_access_token:
        raise HTTPException(status_code=503, detail="No access token available")
    return KiteDataFetcher(
        api_key=settings.kite_api_key,
        api_secret=settings.kite_api_secret,
        access_token=user.kite_access_token,
    )


# ---------------------------------------------------------------------------
# Portfolio Endpoint
# ---------------------------------------------------------------------------

@router.get("/portfolio", response_model=PortfolioResponse)
async def get_portfolio(
    user: User = Depends(get_current_user),
    broker: KiteAdapter = Depends(get_broker_adapter),
    db: Session = Depends(get_db),
):
    """Get current portfolio: positions, capital, live unrealized P&L."""
    try:
        # Get broker positions
        positions_data = broker.get_positions()

        # Get user's open trades from DB
        open_trades = db.query(Trade).filter(
            Trade.user_id == user.id,
            Trade.status == "OPEN",
        ).all()

        # Build position list
        positions = []
        total_unrealized = 0.0
        deployed_capital = 0.0

        for pos in positions_data:
            symbol = pos.get("tradingsymbol", "")
            exchange = pos.get("exchange", "NSE")
            quantity = pos.get("quantity", 0)
            avg_price = pos.get("average_price", 0)
            ltp = pos.get("last_price", 0)
            product = pos.get("product", "MIS")

            if quantity == 0:
                continue

            unrealized = (ltp - avg_price) * quantity
            total_unrealized += unrealized
            deployed_capital += abs(avg_price * quantity)

            positions.append(PortfolioPosition(
                symbol=symbol,
                exchange=exchange,
                quantity=quantity,
                avg_price=avg_price,
                ltp=ltp,
                unrealized_pnl=round(unrealized, 2),
                product=product,
            ))

        # Available margin (from broker)
        try:
            margins = broker.kite.margins()
            available_margin = margins.get("equity", {}).get("available", {}).get("cash", 0)
        except Exception:
            available_margin = float(user.total_capital or 0) - deployed_capital + total_unrealized

        return PortfolioResponse(
            positions=positions,
            total_capital=float(user.total_capital or 0),
            deployed_capital=round(deployed_capital, 2),
            available_margin=round(available_margin, 2),
            total_unrealized_pnl=round(total_unrealized, 2),
            timestamp=datetime.now(IST),
        )

    except Exception as e:
        logger.exception("Failed to fetch portfolio")
        raise HTTPException(status_code=500, detail=f"Portfolio fetch failed: {e}")


# ---------------------------------------------------------------------------
# Recommendations Endpoint
# ---------------------------------------------------------------------------

@router.get("/recommendations", response_model=List[Recommendation])
async def get_recommendations(
    status: Optional[str] = Query("PENDING", description="Filter by status"),
    limit: int = Query(10, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get latest recommendations."""
    recommender = get_recommender(db, user)

    if status:
        recs = db.query(RecModel).filter(
            RecModel.status == status.upper(),
        ).order_by(RecModel.created_at.desc()).limit(limit).all()
    else:
        recs = recommender.get_pending_recommendations()

    return [recommender._db_to_schema(r) for r in recs[:limit]]


# ---------------------------------------------------------------------------
# Regime Endpoint
# ---------------------------------------------------------------------------

@router.get("/regime", response_model=List[RegimeResult])
async def get_regime(
    symbols: Optional[str] = Query(None, description="Comma-separated symbols"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    fetcher: KiteDataFetcher = Depends(get_kite_fetcher),
):
    """Get current market regime for watched symbols."""
    # Get symbols from watchlist if not provided
    if symbols:
        symbol_list = [s.strip().upper() for s in symbols.split(",")]
    else:
        watchlist = db.query(Watchlist).filter(
            Watchlist.user_id == user.id,
            Watchlist.is_active == True,
        ).all()
        symbol_list = [w.symbol for w in watchlist]

    if not symbol_list:
        return []

    detector = get_regime_detector()

    # Fetch latest candles for each symbol
    try:
        candles_dict = fetcher.get_multiple_latest_candles(
            symbols=symbol_list,
            exchange="NSE",
            interval="5minute",
            lookback_days=5,
        )

        regime_results = detector.detect_batch(candles_dict, "NSE")

        # Persist regime snapshots
        for symbol, regime in regime_results.items():
            db_regime = RegimeSnapshot(
                symbol=symbol,
                exchange="NSE",
                regime=regime.regime.value,
                adx=regime.adx,
                atr=regime.atr,
                bb_bandwidth=regime.bb_bandwidth,
                ema_slope=regime.ema_slope,
                confidence=regime.confidence,
                metadata=regime.metadata,
            )
            db.add(db_regime)
        db.commit()

        return list(regime_results.values())

    except Exception as e:
        logger.exception("Regime detection failed")
        raise HTTPException(status_code=500, detail=f"Regime detection failed: {e}")


# ---------------------------------------------------------------------------
# Execute Endpoint
# ---------------------------------------------------------------------------

@router.post("/execute")
async def execute_recommendation(
    request: ExecuteRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    broker: KiteAdapter = Depends(get_broker_adapter),
):
    """Manually execute a recommendation via the existing broker pipeline."""
    if not request.confirm:
        raise HTTPException(
            status_code=400,
            detail="Confirmation required. Send confirm=true to execute."
        )

    # Get recommendation
    rec = db.query(RecModel).filter(RecModel.id == request.recommendation_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    if rec.status != "PENDING":
        raise HTTPException(status_code=400, detail=f"Recommendation status is {rec.status}")

    if rec.expires_at and rec.expires_at < datetime.now(IST):
        raise HTTPException(status_code=400, detail="Recommendation has expired")

    # Get strategy for algo_id
    strategy = db.query(Strategy).filter(
        Strategy.name == rec.top_strategy_name,
        Strategy.user_id == user.id,
    ).first()

    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found for recommendation")

    algo_id = strategy.algo_id

    # Run risk engine one more time (defense in depth)
    risk = RiskEngine(db, user)
    risk_result = risk.evaluate(
        symbol=rec.symbol,
        exchange=rec.exchange,
        direction=rec.direction,
        entry_price=rec.entry_price,
    )

    if not risk_result.approved:
        # Mark recommendation as rejected
        recommender = get_recommender(db, user)
        recommender.mark_rejected(rec.id, risk_result.reason)
        raise HTTPException(status_code=400, detail=f"Risk check failed: {risk_result.reason}")

    # Use risk engine's computed values
    quantity = risk_result.quantity
    stoploss_price = risk_result.stoploss_price
    target_price = risk_result.target_price

    try:
        # Place orders via broker (same as webhook path)
        order_ids = broker.place_entry_with_sl_target(
            symbol=rec.symbol,
            exchange=rec.exchange,
            direction=rec.direction,
            quantity=quantity,
            entry_price=rec.entry_price,
            stoploss_price=stoploss_price,
            target_price=target_price,
            product="MIS",
            algo_id=algo_id,
        )

        # Persist trade + orders
        trade = Trade(
            user_id=user.id,
            strategy_id=strategy.id,
            symbol=rec.symbol,
            exchange=rec.exchange,
            direction=rec.direction,
            quantity=quantity,
            entry_price=rec.entry_price,
            stoploss_price=stoploss_price,
            target_price=target_price,
            status="OPEN",
            algo_id=algo_id,
        )
        db.add(trade)
        db.flush()

        # Order records
        opposite_txn = "SELL" if rec.direction == "BUY" else "BUY"
        order_records = [
            Order(trade_id=trade.id, broker_order_id=order_ids.get("entry_order_id"),
                  order_type="ENTRY", transaction_type=rec.direction, product="MIS",
                  quantity=quantity, price=rec.entry_price, status="PLACED", algo_id=algo_id),
            Order(trade_id=trade.id, broker_order_id=order_ids.get("sl_order_id"),
                  order_type="STOPLOSS", transaction_type=opposite_txn, product="MIS",
                  quantity=quantity, trigger_price=stoploss_price, status="PLACED", algo_id=algo_id),
            Order(trade_id=trade.id, broker_order_id=order_ids.get("target_order_id"),
                  order_type="TARGET", transaction_type=opposite_txn, product="MIS",
                  quantity=quantity, price=target_price, status="PLACED", algo_id=algo_id),
        ]
        db.add_all(order_records)
        db.commit()

        # Mark recommendation executed
        recommender = get_recommender(db, user)
        recommender.mark_executed(rec.id, trade.id)

        return {
            "status": "EXECUTED",
            "trade_id": str(trade.id),
            "order_ids": order_ids,
            "quantity": quantity,
            "entry_price": rec.entry_price,
            "stoploss_price": stoploss_price,
            "target_price": target_price,
        }

    except Exception as e:
        logger.exception("Execution failed")
        recommender = get_recommender(db, user)
        recommender.mark_rejected(rec.id, str(e))
        raise HTTPException(status_code=500, detail=f"Execution failed: {e}")


# ---------------------------------------------------------------------------
# Performance Endpoint
# ---------------------------------------------------------------------------

@router.get("/performance", response_model=PerformanceResponse)
async def get_performance(
    days: int = Query(30, ge=1, le=365),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get performance analytics: equity curve, strategy metrics, drawdown."""
    end_date = datetime.now(IST).date()
    start_date = end_date - timedelta(days=days)

    analytics = get_performance_analytics(db, str(user.id))

    equity_curve = analytics.get_equity_curve(start_date, end_date)
    daily_pnl = analytics.get_daily_pnl(start_date, end_date)
    strategy_metrics = analytics.get_strategy_performance(start_date, end_date)
    portfolio_metrics = analytics.get_portfolio_metrics(start_date, end_date)
    live_metrics = analytics.get_live_metrics()

    return PerformanceResponse(
        equity_curve=equity_curve,
        daily_pnl=daily_pnl,
        strategy_metrics=strategy_metrics,
        portfolio_metrics={**portfolio_metrics, **live_metrics},
    )


# ---------------------------------------------------------------------------
# Daily Report Endpoint
# ---------------------------------------------------------------------------

@router.get("/daily-report", response_model=DailyReportResponse)
async def get_daily_report(
    report_date: Optional[date] = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the latest daily report."""
    if report_date is None:
        report_date = datetime.now(IST).date()

    report = db.query(DailyReport).filter(
        DailyReport.user_id == user.id,
        DailyReport.report_date == report_date,
    ).first()

    if not report:
        # Try to find the most recent report
        report = db.query(DailyReport).filter(
            DailyReport.user_id == user.id,
        ).order_by(DailyReport.report_date.desc()).first()

    if not report:
        raise HTTPException(status_code=404, detail="No daily report found")

    return DailyReportResponse(
        report_date=report.report_date,
        total_pnl=float(report.total_pnl),
        trades_count=report.trades_count,
        wins=report.wins,
        losses=report.losses,
        max_drawdown=float(report.max_drawdown),
        best_trade_pnl=float(report.best_trade_pnl),
        worst_trade_pnl=float(report.worst_trade_pnl),
        best_strategy=report.best_strategy,
        worst_strategy=report.worst_strategy,
        regime_summary=report.regime_summary or {},
        strategy_performance=report.strategy_performance or [],
        ai_summary=report.ai_summary,
        created_at=report.created_at,
    )


# ---------------------------------------------------------------------------
# WebSocket for Live Updates
# ---------------------------------------------------------------------------

class ConnectionManager:
    """Manages WebSocket connections for live updates."""
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                pass


manager = ConnectionManager()


@router.websocket("/ws/live")
async def websocket_live(websocket: WebSocket, db: Session = Depends(get_db)):
    """WebSocket endpoint for live P&L and position updates."""
    await manager.connect(websocket)
    try:
        while True:
            # Send periodic updates (every 5 seconds)
            await websocket.receive_text()  # Keep alive / receive ping
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


# Background task for pushing live updates
async def push_live_updates():
    """Background task to push live updates via WebSocket."""
    while True:
        await asyncio.sleep(5)
        # This would be called from the scheduler or a background task
        # Implementation depends on how you want to trigger updates


# Import asyncio for the background task
import asyncio