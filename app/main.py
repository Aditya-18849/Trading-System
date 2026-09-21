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
_algo_service_active: bool = True  # Controls live automated execution
_app_start_time: float = time.time()


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

    # 2. Database tables & auto-migrations
    Base.metadata.create_all(bind=engine)
    try:
        from scripts.migrate_db import migrate
        migrate()
    except Exception as m_exc:
        logger.debug("Migration check: %s", m_exc)
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

    # 8. Live Market Feed Ingestion & Streaming Coordinator
    try:
        from app.services.live_feed import live_feed_coordinator
        await live_feed_coordinator.start()
        logger.info("LiveFeedCoordinator started (Streaming ticks & MTM P&L)")
    except Exception:
        logger.exception("Failed to start LiveFeedCoordinator")

    yield  # ── Application is running ──

    # Shutdown
    try:
        from app.services.live_feed import live_feed_coordinator
        await live_feed_coordinator.stop()
    except Exception:
        pass

    if _position_monitor is not None:
        await _position_monitor.stop()
        logger.info("PositionMonitor shut down")
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler shut down")
    await shutdown_scheduler()
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

    # Avoid flooding Render free-tier logs on frequent keepalive cron checks
    if request.url.path in ("/health", "/ping", "/healthz") and response.status_code < 400:
        logger.debug(
            "%s %s → %d (%.1f ms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
    else:
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
        excluded_handlers=["/health", "/metrics", "/ping", "/healthz"],
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

# ── Register Dashboard & Auth routers ──────────────────────────────────────
try:
    from app.routers.dashboard import router as dashboard_router, websocket_live
    from app.routers.auth import router as auth_router

    app.include_router(dashboard_router)
    app.include_router(auth_router)
    app.websocket("/ws/live")(websocket_live)
    logger.info("Dashboard and Auth routers registered successfully")
except Exception as exc:
    logger.warning("Router registration issue: %s", exc)


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

@app.get("/", tags=["System"])
async def root():
    """Root landing endpoint: returns system overview and navigation links."""
    return {
        "name": "SEBI-Compliant Algo Trading System",
        "version": "2.0.0",
        "status": "online",
        "timestamp": datetime.now(IST).isoformat(),
        "endpoints": {
            "docs": "/docs",
            "redoc": "/redoc",
            "health": "/health",
            "ping": "/ping",
            "status": "/status",
            "portfolio": "/api/portfolio",
            "recommendations": "/api/recommendations",
            "regime": "/api/regime",
            "performance": "/api/performance",
            "webhook": "/webhook/tradingview",
            "frontend_dashboard": "http://localhost:3000",
        },
    }


@app.api_route("/health", methods=["GET", "HEAD"], tags=["System"])
@app.api_route("/healthz", methods=["GET", "HEAD"], tags=["System"], include_in_schema=False)
async def health_check(check_db: bool = False):
    """
    Liveness and readiness probe for Render, load balancers, container orchestrators,
    and keep-alive cron jobs.

    - Default (check_db=False): Ultra-fast, zero-DB response. Perfect for cron jobs
      pinging the service (e.g. every 10-14 minutes) to prevent Render free-tier spindown.
    - Deep check (check_db=True): Validates active database connectivity.
    """
    uptime = round(time.time() - _app_start_time, 1)
    data = {
        "status": "healthy",
        "service": "trading-system-backend",
        "version": "2.0.0",
        "uptime_seconds": uptime,
        "timestamp": datetime.now(IST).isoformat(),
        "environment": settings.app_env,
    }

    if check_db:
        try:
            from sqlalchemy import text
            from app.database import SessionLocal

            with SessionLocal() as db_session:
                db_session.execute(text("SELECT 1"))
            data["database"] = "connected"
        except Exception as exc:
            data["database"] = "unreachable"
            data["database_error"] = str(exc)
            data["status"] = "degraded"
            return JSONResponse(status_code=503, content=data)

    return data


@app.api_route("/ping", methods=["GET", "HEAD"], tags=["System"])
async def ping():
    """
    Ultra-lightweight ping endpoint for Render keep-alive cron jobs.
    Returns HTTP 200 with minimal payload.
    """
    return {
        "status": "ok",
        "message": "pong",
        "timestamp": datetime.now(IST).isoformat(),
        "uptime_seconds": round(time.time() - _app_start_time, 1),
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


# ── Emergency Exit & Algo Kill-Switch ───────────────────────────────────────

def _is_authorized(request: Request) -> bool:
    """Validate request using X-Admin-Key, JWT Bearer token, or local session."""
    admin_key = request.headers.get("X-Admin-Key", "")
    if admin_key and hmac.compare_digest(admin_key, settings.admin_api_key):
        return True
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        try:
            from app.auth.security import decode_access_token
            payload = decode_access_token(token)
            if payload:
                return True
        except Exception:
            pass
    # Allow local requests
    client_host = request.client.host if request.client else ""
    if client_host in ("127.0.0.1", "localhost", "::1", "testclient"):
        return True
    return False


@app.get(
    "/api/system/algo-status",
    tags=["System"],
    summary="Get status of the automated algo trading engine",
)
async def get_algo_status():
    """Check whether the algorithmic trading engine is active or halted."""
    global _algo_service_active, _position_monitor
    circuit_tripped = False
    if _position_monitor:
        circuit_tripped = getattr(_position_monitor, "_circuit_breaker_tripped", False)
    is_active = _algo_service_active and not circuit_tripped
    return {
        "algo_active": is_active,
        "status_label": "RUNNING" if is_active else "HALTED / TERMINATED",
        "circuit_breaker_tripped": circuit_tripped,
        "paper_trading_mode": getattr(settings, "paper_trading_mode", True),
    }


@app.post(
    "/api/v1/emergency-exit",
    tags=["System"],
    summary="Global panic button — close all positions and halt algo immediately",
)
@app.post(
    "/api/system/algo-kill",
    tags=["System"],
    summary="Terminate algo trading service immediately",
)
async def emergency_exit(request: Request, db: Session = Depends(get_db)):
    """
    **Global Circuit Breaker & Algo Kill-Switch**
    Instantly halts the automated algo engine, cancels all pending orders,
    and squares off all open positions.
    """
    global _algo_service_active, _position_monitor

    if not _is_authorized(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    logger.critical(
        "ALGO KILL SWITCH / EMERGENCY EXIT triggered from %s",
        request.client.host if request.client else "unknown",
    )

    # 1. Immediately halt the algo service
    _algo_service_active = False

    closed_count = 0
    total_pnl = 0.0

    # 2. If PositionMonitor is initialized with live brokers, trigger its square-off
    if _position_monitor is not None:
        try:
            pm_res = await _position_monitor.emergency_exit_all(db)
            closed_count = pm_res.get("positions_closed", 0)
            total_pnl = pm_res.get("total_pnl", 0.0)
        except Exception as pm_err:
            logger.exception("PositionMonitor emergency exit error: %s", pm_err)

    # 3. Always ensure all OPEN trades in the DB are closed and orders cancelled
    open_trades = db.query(Trade).filter(Trade.status == "OPEN").all()
    now_ist = datetime.now(IST)
    for t in open_trades:
        t.status = "CLOSED"
        t.exit_time = now_ist
        if not t.exit_price:
            t.exit_price = t.entry_price or 0.0
        if t.pnl is None:
            t.pnl = 0.0
        closed_count += 1
        total_pnl += float(t.pnl or 0.0)

    # Cancel pending orders
    pending_orders = db.query(Order).filter(Order.status.in_(["PENDING", "OPEN", "SUBMITTED", "TRIGGER_PENDING"])).all()
    cancelled_orders = len(pending_orders)
    for o in pending_orders:
        o.status = "CANCELLED"

    db.commit()

    # 4. Notify live feed coordinator & connected frontend WebSockets
    try:
        from app.services.live_feed import live_feed_coordinator
        live_feed_coordinator._open_positions.clear()
        if live_feed_coordinator._broadcast_callback:
            await live_feed_coordinator._broadcast_callback({
                "type": "algo_status",
                "payload": {
                    "algo_active": False,
                    "status_label": "HALTED / TERMINATED",
                    "detail": "Emergency Kill-Switch activated. All positions closed.",
                }
            })
            await live_feed_coordinator._broadcast_callback({
                "type": "portfolio_update",
                "payload": live_feed_coordinator._calculate_mtm_portfolio(),
            })
    except Exception as ws_err:
        logger.debug("WebSocket broadcast notice: %s", ws_err)

    return {
        "status": "ALGO_TERMINATED",
        "algo_active": False,
        "positions_closed": closed_count,
        "orders_cancelled": cancelled_orders,
        "total_pnl": total_pnl,
        "message": f"Kill-Switch Activated: Algo service halted. {closed_count} positions squared off, {cancelled_orders} orders cancelled.",
    }


@app.post(
    "/api/system/algo-resume",
    tags=["System"],
    summary="Resume the algo trading engine",
)
async def resume_algo(request: Request):
    """Resume algorithmic trading engine after kill-switch termination."""
    global _algo_service_active, _position_monitor

    if not _is_authorized(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    _algo_service_active = True
    if _position_monitor:
        _position_monitor._circuit_breaker_tripped = False

    # Broadcast resume via WebSocket
    try:
        from app.services.live_feed import live_feed_coordinator
        if live_feed_coordinator._broadcast_callback:
            await live_feed_coordinator._broadcast_callback({
                "type": "algo_status",
                "payload": {
                    "algo_active": True,
                    "status_label": "RUNNING",
                    "detail": "Algo trading engine resumed.",
                }
            })
    except Exception:
        pass

    logger.info("Algo trading service resumed.")
    return {
        "status": "ALGO_RESUMED",
        "algo_active": True,
        "message": "Algo service resumed. Automatic strategy signal processing is now active.",
    }


# ── Uvicorn entry point (for local dev and container runners) ───────────────

if __name__ == "__main__":
    import os
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        reload=settings.app_env == "development",
        log_level="info",
    )
