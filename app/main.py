"""
SEBI-Compliant Algo Trading System — FastAPI Application
=========================================================
Webhook ingestion, risk management orchestration, broker order execution,
and global error handling with Telegram alerts.

Phase 2 additions:
  - Angel One broker support (auto-selected per user's ``broker`` field)
  - Order update postback endpoint (``/webhook/kite/postback``)
  - Admin dashboard & reports routers (``/admin/*``, ``/reports/*``)
  - CORS middleware
  - Rate limiting on webhook endpoints (``slowapi``)
  - Prometheus metrics instrumentation
  - Request logging middleware
"""

import hmac
import logging
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from uuid import UUID

from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base, engine, get_db
from app.models import User, Strategy, Trade, Order, Log, Notification
from app.schemas import TradingViewAlert, RiskCheckResult, WebhookResponse
from app.risk_engine import RiskEngine
from app.broker.kite import KiteAdapter
from app.notifications.telegram import TelegramNotifier
from app.utils.logging import setup_logging, DBLogWriter, get_logger
from app.middleware.license_guard import LicenseGuardMiddleware
from app.services.monitor import PositionMonitor
from app.routers import dashboard_router
from app.scheduler import start_scheduler, shutdown_scheduler

# ── Module-level logger ────────────────────────────────────────────────────
logger = get_logger(__name__)

# IST timezone constant
IST = timezone(timedelta(hours=5, minutes=30))

# ── Rate limiter ───────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

# ── Shared singletons (initialised in lifespan) ───────────────────────────
_brokers: dict[str, "KiteAdapter | object"] = {}   # user_client_id → adapter
_default_broker: KiteAdapter | None = None
_notifier: TelegramNotifier | None = None
_scheduler = None  # APScheduler instance
_auth_service = None  # AuthService instance
_position_monitor: PositionMonitor | None = None


def _get_broker(user: User | None = None) -> "KiteAdapter | object":
    """Return the broker adapter for a given user, falling back to default.

    Supports both Zerodha and Angel One based on the user's ``broker`` field.
    """
    if user and user.broker_client_id in _brokers:
        return _brokers[user.broker_client_id]
    if _default_broker is not None:
        return _default_broker
    raise RuntimeError("Broker adapter not initialised — has the app started?")


def _get_notifier() -> TelegramNotifier:
    """Return the Telegram notifier singleton."""
    if _notifier is None:
        raise RuntimeError("Telegram notifier not initialised — has the app started?")
    return _notifier


# ── Lifespan: startup / shutdown logic ─────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    On startup:
      1. Configure structured logging.
      2. Create DB tables (dev convenience; use migrations in prod).
      3. Initialise broker clients from active users' stored tokens.
      4. Initialise Telegram notifier.
      5. Start APScheduler for daily token refresh (08:45 IST, Mon-Fri).
    """
    global _default_broker, _notifier, _scheduler, _auth_service

    # 1. Logging
    setup_logging(level="DEBUG" if settings.app_env == "development" else "INFO")
    logger.info("Starting SEBI-Compliant Algo Trading System v2.0.0")

    # 2. Database tables
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables verified / created")

    # 3. Broker — initialise adapters for all active users
    db = next(get_db())
    try:
        # Primary: Zerodha Kite Connect (default broker)
        user = (
            db.query(User)
            .filter(User.broker_client_id == settings.kite_user_id, User.is_active.is_(True))
            .first()
        )
        access_token = user.kite_access_token if user else None
        if access_token:
            kite_adapter = KiteAdapter(
                api_key=settings.kite_api_key,
                api_secret=settings.kite_api_secret,
                access_token=access_token,
                algo_id=settings.kite_algo_id,
            )
            _default_broker = kite_adapter
            _brokers[settings.kite_user_id] = kite_adapter
            logger.info("Broker adapter initialised (Zerodha Kite Connect)")
        else:
            logger.warning(
                "No valid access token found in DB for user %s — "
                "run scripts/daily_token_refresh.py first",
                settings.kite_user_id,
            )

        # Secondary: Angel One SmartAPI (if credentials provided)
        if settings.angel_api_key and settings.angel_client_id:
            try:
                from app.broker.angel import AngelAdapter

                angel_user = (
                    db.query(User)
                    .filter(
                        User.broker_client_id == settings.angel_client_id,
                        User.is_active.is_(True),
                    )
                    .first()
                )
                angel_token = angel_user.kite_access_token if angel_user else None
                if angel_token:
                    angel_adapter = AngelAdapter(
                        api_key=settings.angel_api_key,
                        client_id=settings.angel_client_id,
                        password=settings.angel_password or "",
                        totp_secret=settings.angel_totp_secret or "",
                        access_token=angel_token,
                        algo_id=settings.angel_algo_id or "",
                    )
                    _brokers[settings.angel_client_id] = angel_adapter
                    logger.info("Broker adapter initialised (Angel One SmartAPI)")
                else:
                    logger.warning(
                        "No access token for Angel One client %s",
                        settings.angel_client_id,
                    )
            except ImportError:
                logger.warning("Angel One SDK (smartapi-python) not installed — skipping")
    finally:
        db.close()

    # 4. Telegram
    _notifier = TelegramNotifier(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
    )
    logger.info("Telegram notifier initialised")

    await _notifier.send_message("✅ <b>Algo Trading System started</b>\n\n"
                                  f"🕐 {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')} IST")

    # 5. APScheduler — daily token refresh at 08:45 IST (Mon-Fri)
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from app.services.auth import AuthService

        _auth_service = AuthService(notifier=_notifier)
        _scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
        _scheduler.add_job(
            _auth_service.refresh_all_tokens,
            trigger="cron",
            day_of_week="mon-fri",
            hour=8,
            minute=45,
            id="daily_token_refresh",
            name="Daily OAuth Token Refresh (08:45 IST)",
            misfire_grace_time=900,  # 15-min grace for misfires
            replace_existing=True,
        )
        _scheduler.start()
        logger.info(
            "APScheduler started — daily token refresh scheduled at 08:45 IST (Mon-Fri)"
        )
    except ImportError:
        logger.warning(
            "APScheduler or AuthService not available — "
            "daily token refresh will NOT run automatically"
        )
    except Exception:
        logger.exception("Failed to start APScheduler for daily token refresh")

    # 6. Position Monitor — real-time trailing SL & circuit breaker
    global _position_monitor
    if _brokers:
        try:
            _position_monitor = PositionMonitor(
                db_factory=get_db,
                brokers=_brokers,
                notifier=_notifier,
            )
            await _position_monitor.start()
            logger.info("PositionMonitor started with %d broker(s)", len(_brokers))
        except Exception:
            logger.exception("Failed to start PositionMonitor")
    else:
        logger.warning("No brokers initialised — PositionMonitor will NOT start")

    # 7. Strategy Engine Scheduler — market open/close triggers
    try:
        await start_scheduler()
        logger.info("Strategy Engine Scheduler started")
    except Exception:
        logger.exception("Failed to start Strategy Engine Scheduler")

    yield  # ── Application is running ──

    # Shutdown
    if _position_monitor is not None:
        await _position_monitor.stop()
        logger.info("PositionMonitor shut down")
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler shut down")
    await shutdown_scheduler()
    logger.info("Shutting down Algo Trading System")  # ── Application is running ──

    # Shutdown
    if _position_monitor is not None:
        await _position_monitor.stop()
        logger.info("PositionMonitor shut down")
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler shut down")
    logger.info("Shutting down Algo Trading System")


# ── FastAPI app ────────────────────────────────────────────────────────────

app = FastAPI(
    title="SEBI-Compliant Algo Trading System",
    version="2.0.0",
    description="Automated retail algo-trading platform for Indian markets with "
                "TradingView webhook ingestion, SEBI Algo-ID tagging, risk management, "
                "multi-broker support (Zerodha + Angel One), and admin dashboard.",
    lifespan=lifespan,
)

# ── Middleware: CORS ───────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Middleware: License Guard (Subscription Kill-Switch) ───────────────────
# Runs before any route; blocks requests if the client's subscription is
# unpaid or expired. Fails open on timeout / server errors.
app.add_middleware(LicenseGuardMiddleware)

# ── Middleware: Rate Limiting ──────────────────────────────────────────────
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """Return 429 when rate limit is exceeded on webhook endpoints."""
    return JSONResponse(
        status_code=429,
        content={"status": "ERROR", "detail": "Rate limit exceeded. Try again later."},
    )


# ── Middleware: Request Logging ────────────────────────────────────────────

@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Log every incoming request with method, path, and response time."""
    start_time = time.perf_counter()
    response: Response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start_time) * 1000

    logger.info(
        "%s %s → %d (%.1f ms)",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
    return response


# ── Prometheus Metrics ─────────────────────────────────────────────────────
try:
    from prometheus_fastapi_instrumentator import Instrumentator

    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/health", "/metrics"],
    ).instrument(app).expose(app, include_in_schema=True, tags=["Metrics"])
    logger.info("Prometheus metrics enabled at /metrics")
except ImportError:
    logger.warning("prometheus-fastapi-instrumentator not installed — metrics disabled")


# ── Register Admin & Reports routers ───────────────────────────────────────
try:
    from app.routes import admin_router, reports_router

    app.include_router(admin_router)
    app.include_router(reports_router)
except ImportError:
    logger.warning("Admin/Reports routes not available — skipping router registration")

# ── Register Dashboard router ──────────────────────────────────────────────
try:
    app.include_router(dashboard_router)
    logger.info("Dashboard router registered at /api")
except ImportError:
    logger.warning("Dashboard router not available — skipping")


# ── Global exception handler → Telegram alert ─────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Catch any unhandled exception, send a Telegram alert, and return a
    generic 500 response. This ensures no silent failures in production.
    """
    error_detail = f"{type(exc).__name__}: {exc}"
    tb = traceback.format_exc()
    logger.critical("Unhandled exception: %s\n%s", error_detail, tb)

    try:
        notifier = _get_notifier()
        await notifier.send_error_alert(exc, context=f"Endpoint: {request.url.path}")
    except Exception:
        logger.error("Failed to send Telegram error alert", exc_info=True)

    return JSONResponse(
        status_code=500,
        content={"status": "ERROR", "detail": "Internal server error — team has been notified"},
    )


# ── Health / Status endpoints ──────────────────────────────────────────────

@app.get("/health", tags=["System"])
async def health_check():
    """Liveness probe for load balancers and container orchestrators."""
    return {
        "status": "healthy",
        "timestamp": datetime.now(IST).isoformat(),
        "version": "2.0.0",
    }


@app.get("/status", tags=["System"])
async def daily_status(db: Session = Depends(get_db)):
    """
    Returns today's trading summary: P&L, trade count, and remaining
    capacity. Useful for dashboards and monitoring.
    """
    user = (
        db.query(User)
        .filter(User.broker_client_id == settings.kite_user_id, User.is_active.is_(True))
        .first()
    )
    if not user:
        return {"status": "no_user", "detail": "Active user not found"}

    risk = RiskEngine(db, user)
    summary = risk.get_daily_summary()

    return {
        "status": "ok",
        "timestamp": datetime.now(IST).isoformat(),
        "daily_pnl": summary["total_pnl"],
        "trades_today": summary["trade_count"],
        "max_trades": int(user.max_trades_per_day or settings.max_trades_per_day),
        "capital": summary["capital"],
        "broker_connected": _default_broker is not None,
        "active_brokers": list(_brokers.keys()),
    }


# ── Webhook ingestion endpoint ─────────────────────────────────────────────

@app.post(
    "/webhook/tradingview",
    response_model=WebhookResponse,
    tags=["Webhook"],
    summary="Receive TradingView alert and execute trade",
)
@limiter.limit(settings.rate_limit)
async def tradingview_webhook(
    request: Request,
    alert: TradingViewAlert,
    db: Session = Depends(get_db),
):
    """
    **TradingView Webhook Handler**

    Receives a JSON alert from TradingView, validates the shared secret,
    runs all risk management checks, and if approved, places entry + SL +
    target orders via the broker API. Every order is tagged with the
    exchange-assigned SEBI Algo-ID.

    Flow:
      1. Validate shared secret (constant-time comparison).
      2. Look up strategy by webhook_token → get user and algo_id.
      3. Select correct broker adapter based on user's ``broker`` field.
      4. Fetch broker LTP for accurate pricing.
      5. Run risk engine evaluation.
      6. Place orders via broker adapter.
      7. Persist trade + order records in DB.
      8. Send Telegram notification.
    """
    db_log = DBLogWriter(db)
    notifier = _get_notifier()

    # ── Step 1: Validate shared secret ──────────────────────────────────
    if not hmac.compare_digest(alert.secret, settings.tradingview_webhook_secret):
        logger.warning("Webhook secret validation failed — rejecting")
        db_log.warning("webhook", "Invalid webhook secret received", context={
            "symbol": alert.symbol, "ip": "redacted"
        })
        raise HTTPException(status_code=401, detail="Invalid webhook secret")

    logger.info("Webhook received: %s %s %s", alert.direction, alert.symbol, alert.exchange)

    # ── Step 2: Look up strategy ────────────────────────────────────────
    strategy = (
        db.query(Strategy)
        .filter(Strategy.webhook_token == alert.webhook_token, Strategy.is_active.is_(True))
        .first()
    )
    if not strategy:
        logger.warning("No active strategy found for webhook_token=%s", alert.webhook_token)
        return WebhookResponse(status="REJECTED", detail="Unknown or inactive strategy")

    user = db.query(User).filter(User.id == strategy.user_id, User.is_active.is_(True)).first()
    if not user:
        logger.warning("User %s not found or inactive", strategy.user_id)
        return WebhookResponse(status="REJECTED", detail="User not found or inactive")

    algo_id = strategy.algo_id  # SEBI-mandated Algo-ID

    # ── Step 3: Select broker adapter ───────────────────────────────────
    broker = _get_broker(user)

    # ── Step 4: Fetch broker LTP ────────────────────────────────────────
    try:
        ltp = broker.get_ltp(alert.symbol, alert.exchange)
        logger.info("Broker LTP for %s:%s = ₹%.2f", alert.exchange, alert.symbol, ltp)
    except Exception as e:
        logger.error("Failed to fetch LTP: %s", e)
        # Fall back to TradingView price hint if broker LTP unavailable
        if alert.price:
            ltp = alert.price
            logger.info("Using TradingView price hint: ₹%.2f", ltp)
        else:
            await notifier.send_error_alert(e, context=f"LTP fetch failed for {alert.symbol}")
            return WebhookResponse(status="ERROR", detail=f"Cannot determine price for {alert.symbol}")

    # ── Step 5: Risk engine evaluation ──────────────────────────────────
    risk = RiskEngine(db, user)
    stoploss_pct = float(strategy.default_stoploss_pct) if strategy.default_stoploss_pct else None
    target_pct = float(strategy.default_target_pct) if strategy.default_target_pct else None

    risk_result: RiskCheckResult = risk.evaluate(
        symbol=alert.symbol,
        exchange=alert.exchange,
        direction=alert.direction,
        entry_price=ltp,
        stoploss_pct=stoploss_pct,
        target_pct=target_pct,
    )

    if not risk_result.approved:
        logger.info("Trade REJECTED by risk engine: %s", risk_result.reason)
        db_log.info("risk_engine", f"Trade rejected: {risk_result.reason}", context={
            "symbol": alert.symbol, "direction": alert.direction, "reason": risk_result.reason
        }, user_id=user.id)

        await notifier.send_rejection_alert(alert.symbol, alert.direction, risk_result.reason)
        return WebhookResponse(status="REJECTED", detail=risk_result.reason)

    # Use risk engine's computed values (or override quantity if alert specifies it)
    quantity = alert.quantity if alert.quantity else risk_result.quantity
    stoploss_price = risk_result.stoploss_price
    target_price = risk_result.target_price

    logger.info(
        "Risk APPROVED: %s %s qty=%d entry=%.2f SL=%.2f TGT=%.2f",
        alert.direction, alert.symbol, quantity, ltp, stoploss_price, target_price,
    )

    # ── Step 6: Place orders via broker ─────────────────────────────────
    try:
        order_ids = broker.place_entry_with_sl_target(
            symbol=alert.symbol,
            exchange=alert.exchange,
            direction=alert.direction,
            quantity=quantity,
            entry_price=ltp,
            stoploss_price=stoploss_price,
            target_price=target_price,
            product="MIS",
            algo_id=algo_id,
        )
        logger.info("Orders placed: %s", order_ids)
    except Exception as e:
        logger.error("Broker order placement failed: %s", e, exc_info=True)
        db_log.error("broker", f"Order placement failed: {e}", context={
            "symbol": alert.symbol, "direction": alert.direction
        }, user_id=user.id)
        await notifier.send_error_alert(e, context=f"Order placement for {alert.symbol} {alert.direction}")
        return WebhookResponse(status="ERROR", detail=f"Order placement failed: {e}")

    # ── Step 7: Persist trade + order records ───────────────────────────
    trade = Trade(
        user_id=user.id,
        strategy_id=strategy.id,
        symbol=alert.symbol,
        exchange=alert.exchange,
        direction=alert.direction,
        quantity=quantity,
        entry_price=ltp,
        stoploss_price=stoploss_price,
        target_price=target_price,
        status="OPEN",
        algo_id=algo_id,
    )
    db.add(trade)
    db.flush()  # get trade.id without committing

    # Persist individual order records
    order_records = [
        Order(
            trade_id=trade.id,
            broker_order_id=order_ids.get("entry_order_id"),
            order_type="ENTRY",
            transaction_type=alert.direction,
            product="MIS",
            quantity=quantity,
            price=ltp,
            trigger_price=None,
            status="PLACED",
            algo_id=algo_id,
            raw_response={"broker_order_id": order_ids.get("entry_order_id")},
        ),
        Order(
            trade_id=trade.id,
            broker_order_id=order_ids.get("sl_order_id"),
            order_type="STOPLOSS",
            transaction_type="SELL" if alert.direction == "BUY" else "BUY",
            product="MIS",
            quantity=quantity,
            price=None,
            trigger_price=stoploss_price,
            status="PLACED",
            algo_id=algo_id,
            raw_response={"broker_order_id": order_ids.get("sl_order_id")},
        ),
        Order(
            trade_id=trade.id,
            broker_order_id=order_ids.get("target_order_id"),
            order_type="TARGET",
            transaction_type="SELL" if alert.direction == "BUY" else "BUY",
            product="MIS",
            quantity=quantity,
            price=target_price,
            trigger_price=None,
            status="PLACED",
            algo_id=algo_id,
            raw_response={"broker_order_id": order_ids.get("target_order_id")},
        ),
    ]
    db.add_all(order_records)
    db.commit()
    db.refresh(trade)

    logger.info("Trade %s persisted with 3 orders", trade.id)
    db_log.info("trade", "Trade executed successfully", context={
        "trade_id": str(trade.id),
        "symbol": alert.symbol,
        "direction": alert.direction,
        "quantity": quantity,
        "entry_price": float(ltp),
        "stoploss_price": float(stoploss_price),
        "target_price": float(target_price),
    }, user_id=user.id)

    # ── Step 8: Telegram notification ───────────────────────────────────
    daily = risk.get_daily_summary()
    await notifier.send_trade_notification({
        "symbol": alert.symbol,
        "direction": alert.direction,
        "quantity": quantity,
        "entry_price": ltp,
        "stoploss_price": stoploss_price,
        "target_price": target_price,
        "algo_id": algo_id,
        "daily_pnl": daily["total_pnl"],
        "trade_count": daily["trade_count"],
        "max_trades": int(user.max_trades_per_day or settings.max_trades_per_day),
    })

    return WebhookResponse(
        status="EXECUTED",
        detail=f"{alert.direction} {alert.symbol} x{quantity} @ ₹{ltp:.2f}",
        trade_id=str(trade.id),
    )


# ── Order update postback endpoint ─────────────────────────────────────────

@app.post("/webhook/kite/postback", tags=["Webhook"], summary="Receive broker order update")
async def kite_order_postback(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    **Broker Order Update Postback**

    Receives order status updates from the broker (Zerodha Kite or Angel One).
    Processes the update through the OrderManager to auto-close trades,
    cancel opposite exit legs, and compute P&L.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    logger.info("Order postback received: %s", payload)

    try:
        from app.order_manager import OrderManager, OrderUpdatePayload

        # Parse and validate payload
        update = OrderUpdatePayload(
            order_id=str(payload.get("order_id", "")),
            status=payload.get("status", ""),
            filled_quantity=payload.get("filled_quantity"),
            average_price=payload.get("average_price"),
            transaction_type=payload.get("transaction_type"),
            tradingsymbol=payload.get("tradingsymbol"),
            exchange=payload.get("exchange"),
            order_type=payload.get("order_type"),
            checksum=payload.get("checksum"),
        )

        notifier = _get_notifier()
        broker = _default_broker
        if broker is None:
            logger.error("Cannot process postback — no broker adapter initialised")
            return {"status": "ERROR", "detail": "Broker not initialised"}

        order_mgr = OrderManager(db=db, broker=broker, notifier=notifier)
        await order_mgr.process_order_update(update.model_dump())

        return {"status": "OK", "detail": f"Processed order update for {update.order_id}"}

    except ImportError:
        logger.warning("OrderManager not available — postback ignored")
        return {"status": "SKIPPED", "detail": "Order lifecycle module not loaded"}
    except Exception as e:
        logger.error("Order postback processing failed: %s", e, exc_info=True)
        return {"status": "ERROR", "detail": str(e)}


# ── Manual token refresh endpoint ──────────────────────────────────────────

@app.post("/admin/refresh-tokens", tags=["Admin"], summary="Manually trigger token refresh")
async def manual_token_refresh(request: Request):
    """
    **Manual Token Refresh**

    Triggers an immediate token refresh for all configured brokers.
    Protected by the admin API key (``X-Admin-Key`` header).

    Use this when you need to refresh tokens outside the scheduled
    08:45 IST window (e.g. after a token expiry or system restart).
    """
    # Authenticate
    admin_key = request.headers.get("X-Admin-Key", "")
    if not hmac.compare_digest(admin_key, settings.admin_api_key):
        raise HTTPException(status_code=401, detail="Invalid admin API key")

    if _auth_service is None:
        raise HTTPException(
            status_code=503,
            detail="AuthService not initialised — check startup logs",
        )

    logger.info("Manual token refresh triggered via /admin/refresh-tokens")
    results = await _auth_service.refresh_all_tokens()

    all_ok = all(results.values())
    return {
        "status": "OK" if all_ok else "PARTIAL_FAILURE",
        "results": {k: ("success" if v else "failed") for k, v in results.items()},
        "timestamp": datetime.now(IST).isoformat(),
    }


# ── Emergency Exit (Panic Button) ──────────────────────────────────────────

@app.post(
    "/api/v1/emergency-exit",
    tags=["System"],
    summary="Global panic button — close all positions immediately",
)
async def emergency_exit(request: Request, db: Session = Depends(get_db)):
    """
    **Global Circuit Breaker / Panic Button**

    Instantly cancels all pending orders and closes all open positions
    across all users with market orders.  Protected by the admin API key.

    Use this in extreme scenarios (flash crash, broker issues, manual
    override) to immediately flatten all exposure.
    """
    # Authenticate
    admin_key = request.headers.get("X-Admin-Key", "")
    if not hmac.compare_digest(admin_key, settings.admin_api_key):
        raise HTTPException(status_code=401, detail="Invalid admin API key")

    if _position_monitor is None:
        raise HTTPException(
            status_code=503,
            detail="PositionMonitor not initialised — check startup logs",
        )

    logger.critical(
        "EMERGENCY EXIT triggered via /api/v1/emergency-exit from %s",
        request.client.host if request.client else "unknown",
    )

    result = await _position_monitor.emergency_exit_all(db)

    return {
        "status": "EMERGENCY_EXIT_COMPLETE",
        **result,
    }


# ── Uvicorn entry point (for local dev) ────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.app_env == "development",
        log_level="info",
    )
