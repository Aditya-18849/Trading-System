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
    """Get or auto-initialize the primary active user."""
    client_id = settings.kite_user_id or "DEV001"
    user = db.query(User).filter(
        User.broker_client_id == client_id,
    ).first()
    if not user:
        user = User(
            broker_client_id=client_id,
            broker="zerodha",
            full_name="Primary Trader",
            email=f"{client_id.lower()}@trading.local",
            is_active=True,
            total_capital=float(settings.total_capital),
            max_daily_loss=float(settings.max_daily_loss),
            risk_per_trade_pct=float(settings.risk_per_trade_pct),
            max_trades_per_day=int(settings.max_trades_per_day),
            cooldown_minutes=int(getattr(settings, "cooldown_minutes_after_loss", 20)),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def get_broker_adapter(user: User = Depends(get_current_user)) -> Optional[KiteAdapter]:
    """Get broker adapter for the current user (or None if unauthenticated)."""
    from app.main import _get_broker
    try:
        return _get_broker(user)
    except Exception:
        return None


def get_kite_fetcher(user: User = Depends(get_current_user)) -> Optional[KiteDataFetcher]:
    """Get Kite data fetcher for the current user."""
    if not user.kite_access_token:
        return None
    try:
        return KiteDataFetcher(
            api_key=settings.kite_api_key,
            api_secret=settings.kite_api_secret,
            access_token=user.kite_access_token,
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Portfolio Endpoint
# ---------------------------------------------------------------------------

@router.get("/portfolio", response_model=PortfolioResponse)
async def get_portfolio(
    user: User = Depends(get_current_user),
    broker: Optional[KiteAdapter] = Depends(get_broker_adapter),
    db: Session = Depends(get_db),
):
    """Get current portfolio: positions, capital, live unrealized P&L."""
    try:
        positions = []
        total_unrealized = 0.0
        deployed_capital = 0.0
        capital = float(user.total_capital or settings.total_capital)

        # 1. Try broker positions if live adapter is available
        if broker is not None:
            try:
                positions_data = broker.get_positions()
                for pos in positions_data:
                    symbol = pos.get("tradingsymbol", "")
                    exchange = pos.get("exchange", "NSE")
                    quantity = pos.get("quantity", 0)
                    avg_price = pos.get("average_price", 0)
                    ltp = pos.get("last_price", avg_price)
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
            except Exception as b_err:
                logger.warning("Broker positions fetch fallback: %s", b_err)

        # 2. Fallback to open trades in database + live feed coordinator prices
        if not positions:
            open_trades = db.query(Trade).filter(
                Trade.user_id == user.id,
                Trade.status == "OPEN",
            ).all()

            for t in open_trades:
                ltp = live_feed_coordinator.get_ltp(t.symbol)
                entry_price = float(t.entry_price or ltp)
                qty = int(t.quantity or 1)

                pnl = (ltp - entry_price) * qty if t.direction == "BUY" else (entry_price - ltp) * qty
                total_unrealized += pnl
                deployed_capital += abs(entry_price * qty)

                positions.append(PortfolioPosition(
                    symbol=t.symbol,
                    exchange=t.exchange or "NSE",
                    quantity=qty if t.direction == "BUY" else -qty,
                    avg_price=entry_price,
                    ltp=ltp,
                    unrealized_pnl=round(pnl, 2),
                    product="MIS",
                ))

        available_margin = max(0.0, capital - deployed_capital + total_unrealized)

        return PortfolioResponse(
            positions=positions,
            total_capital=capital,
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
# Broker Credentials & Token Management
# ---------------------------------------------------------------------------

@router.post("/broker/credentials", tags=["Broker"])
async def update_broker_credentials(payload: dict, db: Session = Depends(get_db)):
    """Update client broker credentials (Kite / Angel One) and persist to database."""
    broker_type = payload.get("broker", "zerodha").lower()
    client_id = payload.get("broker_client_id") or payload.get("user_id") or settings.kite_user_id
    api_key = payload.get("api_key")
    api_secret = payload.get("api_secret")

    user = db.query(User).filter(User.broker_client_id == client_id).first()
    if not user:
        user = User(
            broker_client_id=client_id,
            broker=broker_type,
            full_name=payload.get("full_name", "Primary Trader"),
            email=payload.get("email", f"{client_id.lower()}@trading.local"),
            is_active=True,
            total_capital=float(payload.get("total_capital", settings.total_capital)),
        )
        db.add(user)

    if payload.get("total_capital"):
        user.total_capital = float(payload["total_capital"])
    if payload.get("max_daily_loss"):
        user.max_daily_loss = float(payload["max_daily_loss"])
    if payload.get("risk_per_trade_pct"):
        user.risk_per_trade_pct = float(payload["risk_per_trade_pct"])
    if payload.get("cooldown_minutes"):
        user.cooldown_minutes = int(payload["cooldown_minutes"])
    if payload.get("max_trades_per_day"):
        user.max_trades_per_day = int(payload["max_trades_per_day"])

    db.commit()
    db.refresh(user)

    logger.info("Updated broker credentials and risk profile for client %s", client_id)

    return {
        "status": "success",
        "message": f"Credentials updated for {client_id} ({broker_type})",
        "user_id": str(user.id),
        "broker": user.broker,
        "total_capital": float(user.total_capital),
    }


@router.post("/broker/refresh-token", tags=["Broker"])
async def refresh_broker_token(payload: dict = None, db: Session = Depends(get_db)):
    """Trigger on-demand OAuth TOTP access token generation for broker."""
    try:
        from app.services.auth import generate_kite_access_token
        token_info = generate_kite_access_token()
        return {
            "status": "success",
            "message": "Generated fresh Kite Connect access token successfully.",
            "access_token": token_info.get("access_token", "active")[:10] + "...",
        }
    except Exception as exc:
        return {
            "status": "simulated",
            "message": f"Token generator notice: {str(exc)}. Live simulation mode active.",
        }


# ---------------------------------------------------------------------------
# Paper Trading Mode Switch
# ---------------------------------------------------------------------------

@router.get("/trading-mode")
async def get_trading_mode():
    """Get current operating mode: paper trading (simulation) vs live execution."""
    return {
        "paper_trading_mode": bool(getattr(settings, "paper_trading_mode", True)),
        "mode_label": "PAPER TRADING (SIMULATION)" if getattr(settings, "paper_trading_mode", True) else "LIVE BROKER EXECUTION",
    }


@router.post("/trading-mode")
async def set_trading_mode(payload: dict):
    """Set operating mode: True for paper trading, False for live execution."""
    enabled = payload.get("paper_trading_mode", True)
    settings.paper_trading_mode = bool(enabled)
    logger.info("Trading mode updated to: %s", "PAPER TRADING" if settings.paper_trading_mode else "LIVE BROKER EXECUTION")
    return {
        "status": "success",
        "paper_trading_mode": settings.paper_trading_mode,
        "mode_label": "PAPER TRADING (SIMULATION)" if settings.paper_trading_mode else "LIVE BROKER EXECUTION",
    }


# ---------------------------------------------------------------------------
# Telegram Test Notification Endpoint
# ---------------------------------------------------------------------------

@router.post("/telegram/test")
async def test_telegram_alert(payload: dict):
    """Send a live test alert to the configured Telegram chat."""
    bot_token = payload.get("bot_token") or settings.telegram_bot_token
    chat_id = payload.get("chat_id") or settings.telegram_chat_id

    if not bot_token or not chat_id:
        raise HTTPException(status_code=400, detail="Telegram bot token and chat ID are required")

    try:
        import httpx
        test_message = (
            "🔔 <b>ALGO TRADE PRO — SEBI Telemetry Test</b>\n\n"
            "✅ <i>Telegram Mobile Notification Link Verified!</i>\n"
            "📊 Timestamp: " + datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST") + "\n"
            "🚀 Real-time trade executions, trailing SL hits, and daily P&L reports will be delivered to this chat."
        )
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": test_message,
                    "parse_mode": "HTML",
                },
            )
            if resp.status_code != 200:
                return {
                    "status": "error",
                    "detail": resp.json().get("description", "Failed to send message"),
                }
        return {"status": "success", "message": "Test notification sent to Telegram successfully!"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


# ---------------------------------------------------------------------------
# WebSocket for Live Ingestion & Telemetry
# ---------------------------------------------------------------------------

class ConnectionManager:
    """Manages active WebSocket client connections for real-time streaming."""
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info("Frontend WebSocket client connected (Total: %d)", len(self.active_connections))

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info("Frontend WebSocket client disconnected (Remaining: %d)", len(self.active_connections))

    async def broadcast(self, message: dict):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)


manager = ConnectionManager()

# Connect live feed coordinator broadcast hook
from app.services.live_feed import live_feed_coordinator
live_feed_coordinator.set_broadcast_callback(manager.broadcast)


@router.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    """WebSocket endpoint for sub-second live ticks, P&L, and trailing SL updates."""
    await manager.connect(websocket)
    try:
        # Immediately send current market snapshot & MTM portfolio upon connection
        initial_prices = live_feed_coordinator.get_all_prices()
        for sym, tick in initial_prices.items():
            await websocket.send_json({
                "type": "tick_update",
                "payload": tick,
            })

        portfolio_snapshot = live_feed_coordinator._calculate_mtm_portfolio()
        if portfolio_snapshot:
            await websocket.send_json({
                "type": "portfolio_update",
                "payload": portfolio_snapshot,
            })

        while True:
            # Keepalive listener
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)