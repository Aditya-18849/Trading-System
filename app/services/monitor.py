"""
Real-Time Position Monitoring & Exit Automation Service.

Maintains WebSocket (KiteTicker / SmartWebSocket) connections for live price
feeds, continuously compares LTP against target, fixed stop-loss, and trailing
stop-loss levels, and automatically triggers market-order exits when thresholds
are breached.

Includes a global circuit breaker that monitors aggregate daily P&L across
all users and force-liquidates every position if ``MAX_DAILY_LOSS`` is exceeded.

Architecture
------------
::

    WebSocket Ticker ──→ _price_cache dict
                              │
    REST Polling (fallback) ──┘
                              │
                    ┌─────────▼──────────┐
                    │   _monitor_loop()  │  (asyncio task)
                    │                    │
                    │  For each OPEN     │
                    │  trade:            │
                    │   • target hit?    │
                    │   • SL hit?        │
                    │   • trailing SL    │
                    │   • circuit breaker│
                    └────────────────────┘

This module is designed to be started once during application lifespan and
stopped gracefully on shutdown.
"""

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.broker.base import BrokerAdapter
from app.config import settings
from app.database import get_db
from app.models import Trade, Order, AuditTrail, User
from app.notifications.telegram import TelegramNotifier

logger = logging.getLogger(__name__)

# IST timezone offset (UTC+05:30)
IST = timezone(timedelta(hours=5, minutes=30))


# ------------------------------------------------------------------ #
#  Position Monitor                                                    #
# ------------------------------------------------------------------ #

class PositionMonitor:
    """Real-time position monitoring service with trailing SL and circuit breaker.

    Connects to broker WebSocket feeds (KiteTicker / SmartWebSocket) for
    tick-by-tick LTP updates and continuously evaluates exit conditions
    for all open trades.  Falls back to REST polling if WebSocket is
    unavailable.

    Args:
        db_factory: Callable that returns a new SQLAlchemy ``Session``.
            Defaults to :func:`app.database.get_db` (a generator).
        brokers: Dict mapping ``broker_client_id`` → ``BrokerAdapter``.
        notifier: :class:`TelegramNotifier` for sending lifecycle alerts.
    """

    def __init__(
        self,
        db_factory,
        brokers: dict[str, BrokerAdapter],
        notifier: TelegramNotifier,
    ) -> None:
        self._db_factory = db_factory
        self._brokers = brokers
        self._notifier = notifier

        # In-memory LTP cache: "EXCHANGE:SYMBOL" → latest price
        self._price_cache: dict[str, float] = {}
        self._price_cache_lock = threading.Lock()  # Thread-safe writes

        # Reverse map: numeric instrument_token → "EXCHANGE:SYMBOL"
        self._token_to_symbol_map: dict[int, str] = {}

        # WebSocket ticker instances (one per broker)
        self._tickers: dict[str, object] = {}
        self._ticker_threads: dict[str, threading.Thread] = {}

        # asyncio control
        self._monitor_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._running = False

        # Circuit breaker state
        self._circuit_breaker_tripped = False

        # Config shortcuts
        self._trailing_sl_pct = settings.trailing_sl_pct
        self._trailing_sl_activation_pct = settings.trailing_sl_activation_pct
        self._poll_interval = settings.monitor_poll_interval_sec
        self._pnl_check_interval = settings.monitor_pnl_check_interval_sec

        # WebSocket reconnect settings
        self._ws_reconnect_max_retries = settings.ws_reconnect_max_retries
        self._ws_reconnect_base_delay = settings.ws_reconnect_base_delay_sec

        # Heartbeat settings
        self._heartbeat_interval = settings.heartbeat_interval_sec
        self._heartbeat_telegram_every_n = settings.heartbeat_telegram_every_n

        # Auto square-off
        self._auto_square_off_time = settings.auto_square_off_time

    # ------------------------------------------------------------------ #
    #  Lifecycle                                                           #
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        """Start the position monitor.

        1. Attempts to establish WebSocket ticker connections.
        2. Launches the async monitoring loop as a background task.
        """
        if self._running:
            logger.warning("PositionMonitor.start() called but already running")
            return

        self._running = True
        self._circuit_breaker_tripped = False

        logger.info("Starting PositionMonitor...")

        # --- Attempt WebSocket ticker connections ---------------------- #
        self._start_websocket_tickers()

        # --- Launch async monitor loop -------------------------------- #
        self._monitor_task = asyncio.create_task(
            self._monitor_loop(), name="position_monitor_loop"
        )

        # --- Launch heartbeat loop ------------------------------------ #
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name="position_monitor_heartbeat"
        )

        logger.info("PositionMonitor started successfully")
        await self._notifier.send_message(
            "🔍 <b>Position Monitor Started</b>\n\n"
            f"🕐 {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')} IST\n"
            f"📡 WebSocket tickers: {len(self._tickers)}\n"
            f"⏱️ Poll fallback: {self._poll_interval}s\n"
            f"🔄 Trailing SL: {self._trailing_sl_pct}% (activates at {self._trailing_sl_activation_pct}%)\n"
            f"💓 Heartbeat: every {self._heartbeat_interval}s\n"
            f"⏰ Auto square-off: {self._auto_square_off_time} IST"
        )

    async def stop(self) -> None:
        """Gracefully stop the position monitor.

        Cancels the monitoring task and disconnects all WebSocket tickers.
        """
        if not self._running:
            return

        self._running = False
        logger.info("Stopping PositionMonitor...")

        # Cancel the monitor loop
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        # Cancel the heartbeat loop
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        # Disconnect WebSocket tickers
        self._stop_websocket_tickers()

        logger.info("PositionMonitor stopped")

    # ------------------------------------------------------------------ #
    #  WebSocket Ticker Management                                         #
    # ------------------------------------------------------------------ #

    def _start_websocket_tickers(self) -> None:
        """Attempt to start WebSocket ticker connections for each broker.

        Zerodha uses ``KiteTicker``, Angel One uses ``SmartWebSocket``.
        Failures are non-fatal — the monitor falls back to REST polling.
        """
        for client_id, broker in self._brokers.items():
            try:
                self._start_ticker_for_broker(client_id, broker)
            except Exception:
                logger.warning(
                    "Failed to start WebSocket ticker for broker %s — "
                    "will use REST polling fallback",
                    client_id,
                    exc_info=True,
                )

    def _start_ticker_for_broker(
        self, client_id: str, broker: BrokerAdapter
    ) -> None:
        """Start a WebSocket ticker for a specific broker adapter.

        Args:
            client_id: Broker client ID (used as key in ``_tickers``).
            broker: Concrete broker adapter instance.
        """
        # --- Zerodha KiteTicker --------------------------------------- #
        if hasattr(broker, "kite"):
            try:
                from kiteconnect import KiteTicker

                api_key = broker.api_key
                access_token = broker.kite.access_token

                ticker = KiteTicker(api_key, access_token)

                def on_ticks(ws, ticks):
                    self._handle_ticks(ticks)

                def on_connect(ws, response):
                    logger.info(
                        "KiteTicker connected for broker %s", client_id
                    )
                    # Subscribe to instruments for open trades
                    self._subscribe_open_trade_instruments(client_id, ws)

                def on_close(ws, code, reason):
                    logger.warning(
                        "KiteTicker disconnected | broker=%s code=%s reason=%s",
                        client_id,
                        code,
                        reason,
                    )
                    # Auto-reconnect with exponential backoff
                    self._attempt_reconnect_kite(client_id, broker)

                def on_error(ws, code, reason):
                    logger.error(
                        "KiteTicker error | broker=%s code=%s reason=%s",
                        client_id,
                        code,
                        reason,
                    )

                ticker.on_ticks = on_ticks
                ticker.on_connect = on_connect
                ticker.on_close = on_close
                ticker.on_error = on_error

                ticker.connect(threaded=True)
                self._tickers[client_id] = ticker

                logger.info(
                    "KiteTicker started for broker %s (threaded)", client_id
                )
                return

            except ImportError:
                logger.info("KiteTicker SDK not available — skipping WebSocket")
            except Exception:
                logger.warning(
                    "Failed to initialise KiteTicker for %s", client_id, exc_info=True
                )

        # --- Angel One SmartWebSocket --------------------------------- #
        if hasattr(broker, "smart"):
            try:
                from SmartApi.smartWebSocketV2 import SmartWebSocketV2

                auth_token = broker.smart.access_token
                api_key = broker.api_key
                client_code = broker.client_id
                feed_token = broker.smart.feed_token

                sws = SmartWebSocketV2(auth_token, api_key, client_code, feed_token)

                def on_data(wsapp, message):
                    if isinstance(message, dict) and "last_traded_price" in message:
                        token = message.get("token", "")
                        exchange = message.get("exchange_type", "NSE")
                        symbol = message.get("tradingsymbol", token)
                        ltp = float(message["last_traded_price"]) / 100  # SmartAPI sends paise
                        instrument_key = f"{exchange}:{symbol}"
                        with self._price_cache_lock:
                            self._price_cache[instrument_key] = ltp

                def on_open(wsapp):
                    logger.info("SmartWebSocket connected for broker %s", client_id)
                    # Subscribe to instruments for open trades
                    self._subscribe_angel_instruments(client_id, sws)

                def on_error(wsapp, error):
                    logger.error(
                        "SmartWebSocket error | broker=%s error=%s",
                        client_id,
                        error,
                    )

                def on_close(wsapp):
                    logger.warning(
                        "SmartWebSocket disconnected | broker=%s", client_id
                    )
                    # Auto-reconnect with exponential backoff
                    self._attempt_reconnect_angel(client_id, broker)

                sws.on_data = on_data
                sws.on_open = on_open
                sws.on_error = on_error
                sws.on_close = on_close

                t = threading.Thread(
                    target=sws.connect, daemon=True, name=f"angel_ws_{client_id}"
                )
                t.start()
                self._tickers[client_id] = sws
                self._ticker_threads[client_id] = t

                logger.info(
                    "SmartWebSocket started for broker %s (threaded)", client_id
                )
                return

            except ImportError:
                logger.info("SmartWebSocket SDK not available — skipping WebSocket")
            except Exception:
                logger.warning(
                    "Failed to initialise SmartWebSocket for %s",
                    client_id,
                    exc_info=True,
                )

    def _stop_websocket_tickers(self) -> None:
        """Disconnect and clean up all WebSocket ticker connections."""
        for client_id, ticker in self._tickers.items():
            try:
                if hasattr(ticker, "close"):
                    ticker.close()
                elif hasattr(ticker, "disconnect"):
                    ticker.disconnect()
                logger.info("WebSocket ticker stopped for broker %s", client_id)
            except Exception:
                logger.warning(
                    "Error stopping ticker for broker %s",
                    client_id,
                    exc_info=True,
                )
        self._tickers.clear()
        self._ticker_threads.clear()

    def _handle_ticks(self, ticks: list[dict]) -> None:
        """Process incoming WebSocket ticks and update the price cache.

        Called by KiteTicker's ``on_ticks`` callback from the ticker thread.
        Writes are guarded by ``_price_cache_lock`` for thread safety.

        Args:
            ticks: List of tick dicts from KiteTicker.  Each tick contains
                at minimum ``instrument_token`` and ``last_price``.
        """
        for tick in ticks:
            try:
                instrument_token = tick.get("instrument_token")
                ltp = tick.get("last_price")

                if ltp is None:
                    continue

                # Try reverse-map first (token → "EXCHANGE:SYMBOL")
                instrument_key = self._token_to_symbol_map.get(instrument_token)

                if not instrument_key:
                    # Fallback: use tradingsymbol from tick if available
                    tradingsymbol = tick.get("tradingsymbol", "")
                    exchange = tick.get("exchange", "")
                    if tradingsymbol:
                        instrument_key = f"{exchange}:{tradingsymbol}"
                    elif instrument_token:
                        instrument_key = str(instrument_token)
                    else:
                        continue

                with self._price_cache_lock:
                    self._price_cache[instrument_key] = float(ltp)

            except Exception:
                logger.debug("Error processing tick: %s", tick, exc_info=True)

    def _subscribe_open_trade_instruments(
        self, client_id: str, ws
    ) -> None:
        """Subscribe the KiteTicker WebSocket to instruments for all open trades.

        Resolves trading symbols to numeric instrument tokens via the broker
        adapter's ``get_instrument_tokens()`` method, then calls
        ``ws.subscribe()`` and ``ws.set_mode(ws.MODE_LTP, tokens)``.

        Args:
            client_id: Broker client identifier.
            ws: KiteTicker WebSocket connection object.
        """
        try:
            db = next(self._db_factory())
            try:
                open_trades = (
                    db.query(Trade)
                    .join(User, Trade.user_id == User.id)
                    .filter(
                        Trade.status == "OPEN",
                        User.broker_client_id == client_id,
                    )
                    .all()
                )

                if not open_trades:
                    logger.info("No open trades for broker %s — nothing to subscribe", client_id)
                    return

                # Collect unique instrument keys
                instrument_keys = list(set(
                    f"{t.exchange}:{t.symbol}" for t in open_trades
                ))
                logger.info(
                    "Subscribing to %d instruments for broker %s: %s",
                    len(instrument_keys),
                    client_id,
                    instrument_keys,
                )

                # Resolve to numeric tokens via broker adapter
                broker = self._brokers.get(client_id)
                if not broker:
                    logger.error("No broker adapter for client_id=%s", client_id)
                    return

                token_map = broker.get_instrument_tokens(instrument_keys)
                if not token_map:
                    logger.warning(
                        "No instrument tokens resolved for broker %s — "
                        "seeding cache via REST fallback",
                        client_id,
                    )
                    # Seed price cache via REST as fallback
                    for key in instrument_keys:
                        parts = key.split(":", 1)
                        if len(parts) == 2:
                            try:
                                ltp = broker.get_ltp(parts[1], parts[0])
                                with self._price_cache_lock:
                                    self._price_cache[key] = ltp
                            except Exception:
                                pass
                    return

                # Build reverse map for tick processing
                for sym_key, token in token_map.items():
                    self._token_to_symbol_map[token] = sym_key

                tokens = list(token_map.values())
                logger.info(
                    "KiteTicker subscribing to tokens: %s (broker=%s)",
                    tokens,
                    client_id,
                )

                # Subscribe and set LTP mode
                ws.subscribe(tokens)
                ws.set_mode(ws.MODE_LTP, tokens)

                logger.info(
                    "KiteTicker subscription complete | broker=%s tokens=%d",
                    client_id,
                    len(tokens),
                )

            finally:
                db.close()
        except Exception:
            logger.warning(
                "Failed to subscribe instruments for broker %s",
                client_id,
                exc_info=True,
            )

    def _subscribe_angel_instruments(
        self, client_id: str, sws
    ) -> None:
        """Subscribe the Angel One SmartWebSocket to instruments for open trades.

        Resolves trading symbols to Angel token IDs and subscribes via
        ``sws.subscribe()`` with the correct correlation ID format.

        Args:
            client_id: Broker client identifier.
            sws: SmartWebSocketV2 connection object.
        """
        try:
            db = next(self._db_factory())
            try:
                open_trades = (
                    db.query(Trade)
                    .join(User, Trade.user_id == User.id)
                    .filter(
                        Trade.status == "OPEN",
                        User.broker_client_id == client_id,
                    )
                    .all()
                )

                if not open_trades:
                    logger.info("No open trades for Angel broker %s — nothing to subscribe", client_id)
                    return

                instrument_keys = list(set(
                    f"{t.exchange}:{t.symbol}" for t in open_trades
                ))

                broker = self._brokers.get(client_id)
                if not broker:
                    logger.error("No broker adapter for Angel client_id=%s", client_id)
                    return

                token_map = broker.get_instrument_tokens(instrument_keys)
                if not token_map:
                    logger.warning(
                        "No Angel tokens resolved for broker %s", client_id
                    )
                    return

                # SmartWebSocketV2 expects subscription in format:
                # [{"exchangeType": 1, "tokens": ["token1", "token2"]}]
                # Exchange type mapping: NSE=1, NFO=2, BSE=3, MCX=5
                exchange_type_map = {
                    "NSE": 1, "NFO": 2, "BSE": 3, "BFO": 4, "MCX": 5, "CDS": 13,
                }

                # Group tokens by exchange type
                exchange_tokens: dict[int, list[str]] = {}
                for sym_key, token in token_map.items():
                    exchange = sym_key.split(":", 1)[0]
                    ex_type = exchange_type_map.get(exchange, 1)
                    exchange_tokens.setdefault(ex_type, []).append(str(token))
                    self._token_to_symbol_map[token] = sym_key

                token_list = [
                    {"exchangeType": ex_type, "tokens": tokens}
                    for ex_type, tokens in exchange_tokens.items()
                ]

                correlation_id = f"monitor_{client_id}"
                sws.subscribe(correlation_id, 1, token_list)  # mode 1 = LTP

                logger.info(
                    "SmartWebSocket subscription complete | broker=%s groups=%d",
                    client_id,
                    len(token_list),
                )

            finally:
                db.close()
        except Exception:
            logger.warning(
                "Failed to subscribe Angel instruments for broker %s",
                client_id,
                exc_info=True,
            )

    # ------------------------------------------------------------------ #
    #  WebSocket Auto-Reconnect                                            #
    # ------------------------------------------------------------------ #

    def _attempt_reconnect_kite(
        self, client_id: str, broker: BrokerAdapter
    ) -> None:
        """Attempt to reconnect a dropped KiteTicker with exponential backoff.

        Runs in a background daemon thread so it doesn't block the ticker
        thread.  On successful reconnect, re-subscribes all open trade
        instruments.

        Args:
            client_id: Broker client identifier.
            broker: The KiteAdapter instance.
        """
        if not self._running:
            return

        def _reconnect():
            delay = self._ws_reconnect_base_delay
            for attempt in range(1, self._ws_reconnect_max_retries + 1):
                if not self._running:
                    return
                logger.info(
                    "KiteTicker reconnect attempt %d/%d for broker %s "
                    "(delay %.1fs)",
                    attempt,
                    self._ws_reconnect_max_retries,
                    client_id,
                    delay,
                )
                time.sleep(delay)

                try:
                    self._start_ticker_for_broker(client_id, broker)
                    logger.info(
                        "KiteTicker reconnected successfully | broker=%s "
                        "attempt=%d",
                        client_id,
                        attempt,
                    )
                    # Send Telegram reconnect alert (fire-and-forget)
                    try:
                        asyncio.run_coroutine_threadsafe(
                            self._notifier.send_message(
                                f"🔄 <b>KiteTicker Reconnected</b>\n\n"
                                f"📡 Broker: {client_id}\n"
                                f"🔁 Attempt: {attempt}/{self._ws_reconnect_max_retries}\n"
                                f"🕐 {datetime.now(IST).strftime('%H:%M:%S')} IST"
                            ),
                            asyncio.get_event_loop(),
                        )
                    except Exception:
                        pass
                    return
                except Exception:
                    logger.warning(
                        "KiteTicker reconnect attempt %d failed for broker %s",
                        attempt,
                        client_id,
                        exc_info=True,
                    )

                delay = min(delay * 2, 60)  # Cap at 60s

            # All retries exhausted
            logger.error(
                "KiteTicker reconnect FAILED after %d attempts | broker=%s",
                self._ws_reconnect_max_retries,
                client_id,
            )
            try:
                asyncio.run_coroutine_threadsafe(
                    self._notifier.send_message(
                        f"🚨 <b>KiteTicker Reconnect Failed</b>\n\n"
                        f"📡 Broker: {client_id}\n"
                        f"❌ All {self._ws_reconnect_max_retries} attempts exhausted\n"
                        f"⚠️ Falling back to REST polling\n"
                        f"🕐 {datetime.now(IST).strftime('%H:%M:%S')} IST"
                    ),
                    asyncio.get_event_loop(),
                )
            except Exception:
                pass

        t = threading.Thread(
            target=_reconnect, daemon=True,
            name=f"kite_reconnect_{client_id}",
        )
        t.start()

    def _attempt_reconnect_angel(
        self, client_id: str, broker: BrokerAdapter
    ) -> None:
        """Attempt to reconnect a dropped SmartWebSocket with exponential backoff.

        Runs in a background daemon thread.

        Args:
            client_id: Broker client identifier.
            broker: The AngelAdapter instance.
        """
        if not self._running:
            return

        def _reconnect():
            delay = self._ws_reconnect_base_delay
            for attempt in range(1, self._ws_reconnect_max_retries + 1):
                if not self._running:
                    return
                logger.info(
                    "SmartWebSocket reconnect attempt %d/%d for broker %s "
                    "(delay %.1fs)",
                    attempt,
                    self._ws_reconnect_max_retries,
                    client_id,
                    delay,
                )
                time.sleep(delay)

                try:
                    self._start_ticker_for_broker(client_id, broker)
                    logger.info(
                        "SmartWebSocket reconnected successfully | broker=%s "
                        "attempt=%d",
                        client_id,
                        attempt,
                    )
                    try:
                        asyncio.run_coroutine_threadsafe(
                            self._notifier.send_message(
                                f"🔄 <b>SmartWebSocket Reconnected</b>\n\n"
                                f"📡 Broker: {client_id}\n"
                                f"🔁 Attempt: {attempt}/{self._ws_reconnect_max_retries}\n"
                                f"🕐 {datetime.now(IST).strftime('%H:%M:%S')} IST"
                            ),
                            asyncio.get_event_loop(),
                        )
                    except Exception:
                        pass
                    return
                except Exception:
                    logger.warning(
                        "SmartWebSocket reconnect attempt %d failed for broker %s",
                        attempt,
                        client_id,
                        exc_info=True,
                    )

                delay = min(delay * 2, 60)

            logger.error(
                "SmartWebSocket reconnect FAILED after %d attempts | broker=%s",
                self._ws_reconnect_max_retries,
                client_id,
            )
            try:
                asyncio.run_coroutine_threadsafe(
                    self._notifier.send_message(
                        f"🚨 <b>SmartWebSocket Reconnect Failed</b>\n\n"
                        f"📡 Broker: {client_id}\n"
                        f"❌ All {self._ws_reconnect_max_retries} attempts exhausted\n"
                        f"⚠️ Falling back to REST polling\n"
                        f"🕐 {datetime.now(IST).strftime('%H:%M:%S')} IST"
                    ),
                    asyncio.get_event_loop(),
                )
            except Exception:
                pass

        t = threading.Thread(
            target=_reconnect, daemon=True,
            name=f"angel_reconnect_{client_id}",
        )
        t.start()

    # ------------------------------------------------------------------ #
    #  Main Monitor Loop                                                   #
    # ------------------------------------------------------------------ #

    async def _monitor_loop(self) -> None:
        """Main async monitoring loop.

        Runs continuously while ``_running`` is ``True``.  On each
        iteration:

        1. Fetches all open trades from the database.
        2. For each trade, gets the latest LTP (from cache or REST).
        3. Evaluates exit conditions (target, SL, trailing SL).
        4. Periodically checks daily P&L circuit breaker.
        5. Checks auto square-off time.
        """
        logger.info("Monitor loop started")
        pnl_check_counter = 0

        while self._running:
            try:
                # --- Auto square-off time check ------------------------ #
                now_ist = datetime.now(IST)
                try:
                    sq_hour, sq_min = map(int, self._auto_square_off_time.split(":"))
                    if now_ist.hour > sq_hour or (
                        now_ist.hour == sq_hour and now_ist.minute >= sq_min
                    ):
                        logger.warning(
                            "Auto square-off time reached (%s IST) — "
                            "closing all MIS positions",
                            self._auto_square_off_time,
                        )
                        db = next(self._db_factory())
                        try:
                            # Only close MIS (intraday) positions
                            open_trades = (
                                db.query(Trade)
                                .filter(Trade.status == "OPEN")
                                .all()
                            )
                            mis_trades = []
                            for t in open_trades:
                                entry_order = (
                                    db.query(Order)
                                    .filter(
                                        Order.trade_id == t.id,
                                        Order.order_type == "ENTRY",
                                    )
                                    .first()
                                )
                                product = entry_order.product if entry_order else "MIS"
                                if product == "MIS":
                                    mis_trades.append(t)

                            for t in mis_trades:
                                instrument_key = f"{t.exchange}:{t.symbol}"
                                with self._price_cache_lock:
                                    ltp = self._price_cache.get(instrument_key)
                                if ltp is None:
                                    try:
                                        broker = next(iter(self._brokers.values()), None)
                                        if broker:
                                            ltp = broker.get_ltp(t.symbol, t.exchange)
                                    except Exception:
                                        ltp = float(t.entry_price or 0)
                                if ltp is None:
                                    ltp = float(t.entry_price or 0)
                                await self._execute_exit(t, "AUTO_SQUARE_OFF", ltp, db)

                            if mis_trades:
                                await self._notifier.send_message(
                                    f"⏰ <b>Auto Square-Off Complete</b>\n\n"
                                    f"🕐 {now_ist.strftime('%H:%M:%S')} IST\n"
                                    f"📊 Positions closed: {len(mis_trades)}"
                                )
                        finally:
                            db.close()
                        # Stop monitoring after square-off
                        self._running = False
                        break
                except ValueError:
                    pass  # Invalid time format, skip

                db = next(self._db_factory())
                try:
                    await self._process_all_open_trades(db)

                    # Periodic daily P&L circuit breaker check
                    pnl_check_counter += self._poll_interval
                    if pnl_check_counter >= self._pnl_check_interval:
                        pnl_check_counter = 0
                        await self._check_daily_pnl_circuit_breaker(db)

                finally:
                    db.close()

            except asyncio.CancelledError:
                logger.info("Monitor loop cancelled")
                break
            except Exception:
                logger.exception("Error in monitor loop iteration")

            # Sleep before next iteration
            try:
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                break

        logger.info("Monitor loop exited")

    async def _heartbeat_loop(self) -> None:
        """Periodic heartbeat loop for health monitoring.

        Logs the monitor's status at regular intervals and optionally
        sends a Telegram heartbeat every Nth beat.
        """
        logger.info("Heartbeat loop started (interval=%ds)", self._heartbeat_interval)
        beat_count = 0

        while self._running:
            try:
                await asyncio.sleep(self._heartbeat_interval)
            except asyncio.CancelledError:
                break

            if not self._running:
                break

            beat_count += 1

            # Gather health stats
            with self._price_cache_lock:
                cache_size = len(self._price_cache)

            ws_status = {}
            for cid, ticker in self._tickers.items():
                connected = hasattr(ticker, 'is_connected') and ticker.is_connected()
                ws_status[cid] = "connected" if connected else "unknown"

            # Count open trades
            try:
                db = next(self._db_factory())
                try:
                    open_count = db.query(Trade).filter(Trade.status == "OPEN").count()
                finally:
                    db.close()
            except Exception:
                open_count = -1

            logger.info(
                "💓 Heartbeat #%d | positions=%d | ws_tickers=%s | "
                "price_cache=%d | circuit_breaker=%s",
                beat_count,
                open_count,
                ws_status,
                cache_size,
                self._circuit_breaker_tripped,
            )

            # Send Telegram heartbeat every Nth beat
            if self._heartbeat_telegram_every_n > 0 and beat_count % self._heartbeat_telegram_every_n == 0:
                try:
                    await self._notifier.send_message(
                        f"💓 <b>Monitor Heartbeat #{beat_count}</b>\n\n"
                        f"🕐 {datetime.now(IST).strftime('%H:%M:%S')} IST\n"
                        f"📊 Open positions: {open_count}\n"
                        f"📡 WS tickers: {len(self._tickers)}\n"
                        f"💾 Price cache: {cache_size} instruments\n"
                        f"🔌 Circuit breaker: {'⚡ TRIPPED' if self._circuit_breaker_tripped else '✅ OK'}"
                    )
                except Exception:
                    logger.debug("Failed to send Telegram heartbeat", exc_info=True)

        logger.info("Heartbeat loop exited")

    async def _process_all_open_trades(self, db: Session) -> None:
        """Evaluate exit conditions for every open trade.

        Args:
            db: Active SQLAlchemy session.
        """
        open_trades = (
            db.query(Trade)
            .filter(Trade.status == "OPEN")
            .all()
        )

        if not open_trades:
            return

        for trade in open_trades:
            try:
                ltp = await self._get_ltp_for_trade(trade, db)
                if ltp is None:
                    continue

                await self._check_exit_conditions(trade, ltp, db)

            except Exception:
                logger.exception(
                    "Error processing trade %s (%s)", trade.id, trade.symbol
                )

    async def _get_ltp_for_trade(
        self, trade: Trade, db: Session
    ) -> Optional[float]:
        """Get the latest LTP for a trade's instrument.

        Checks the in-memory price cache first; falls back to REST API
        if the cache doesn't have a price for this instrument.

        Args:
            trade: The ``Trade`` record.
            db: Active SQLAlchemy session (for resolving broker).

        Returns:
            LTP as float, or ``None`` if unavailable.
        """
        instrument_key = f"{trade.exchange}:{trade.symbol}"

        # Check cache first (populated by WebSocket ticks) — snapshot under lock
        with self._price_cache_lock:
            cached = self._price_cache.get(instrument_key)
        if cached is not None:
            return cached

        # Fallback: REST polling
        try:
            user = db.query(User).filter(User.id == trade.user_id).first()
            if not user:
                return None

            broker = self._brokers.get(user.broker_client_id)
            if not broker:
                # Try first available broker
                broker = next(iter(self._brokers.values()), None)
            if not broker:
                return None

            ltp = broker.get_ltp(trade.symbol, trade.exchange)
            with self._price_cache_lock:
                self._price_cache[instrument_key] = ltp  # Cache it
            return ltp

        except Exception:
            logger.debug(
                "REST LTP fetch failed for %s", instrument_key, exc_info=True
            )
            return None

    # ------------------------------------------------------------------ #
    #  Exit Condition Evaluation                                           #
    # ------------------------------------------------------------------ #

    async def _check_exit_conditions(
        self, trade: Trade, ltp: float, db: Session
    ) -> None:
        """Evaluate target, fixed SL, and trailing SL against live LTP.

        Execution priority:
        1. Target hit → exit at market
        2. Trailing SL hit → exit at market
        3. Fixed SL hit (broker SL-M should handle this, but double-check)
        4. Update trailing SL watermark / modify broker order if needed

        Args:
            trade: The ``Trade`` record being evaluated.
            ltp: Current last traded price.
            db: Active SQLAlchemy session.
        """
        entry_price = float(trade.entry_price or 0)
        target_price = float(trade.target_price or 0)
        stoploss_price = float(trade.stoploss_price or 0)
        direction = trade.direction  # "BUY" or "SELL"

        if entry_price == 0:
            return

        # --- 1. Target hit check -------------------------------------- #
        if target_price > 0:
            target_hit = (
                (direction == "BUY" and ltp >= target_price) or
                (direction == "SELL" and ltp <= target_price)
            )
            if target_hit:
                logger.info(
                    "TARGET HIT | trade=%s symbol=%s ltp=%.2f target=%.2f",
                    trade.id, trade.symbol, ltp, target_price,
                )
                await self._execute_exit(trade, "TARGET_HIT", ltp, db)
                return

        # --- 2. Trailing SL update & check ---------------------------- #
        trailing_exit = await self._update_trailing_sl(trade, ltp, db)
        if trailing_exit:
            return

        # --- 3. Fixed SL check (safety net) --------------------------- #
        if stoploss_price > 0:
            sl_hit = (
                (direction == "BUY" and ltp <= stoploss_price) or
                (direction == "SELL" and ltp >= stoploss_price)
            )
            if sl_hit:
                logger.info(
                    "STOPLOSS HIT (safety net) | trade=%s symbol=%s ltp=%.2f sl=%.2f",
                    trade.id, trade.symbol, ltp, stoploss_price,
                )
                await self._execute_exit(trade, "STOPLOSS_HIT", ltp, db)
                return

    # ------------------------------------------------------------------ #
    #  Trailing Stop-Loss Logic                                            #
    # ------------------------------------------------------------------ #

    async def _update_trailing_sl(
        self, trade: Trade, ltp: float, db: Session
    ) -> bool:
        """Update trailing stop-loss watermark and check for exit.

        For **BUY** trades:
        - Track ``highest_price_since_entry`` (high watermark).
        - Activate trailing when price moves up by ``trailing_sl_activation_pct``.
        - Compute ``new_sl = highest * (1 - trailing_sl_pct / 100)``.
        - Only move SL upward (ratchet).
        - Exit when ``ltp <= trailing_sl_price``.

        For **SELL** trades: Mirror logic with ``lowest_price_since_entry``.

        Args:
            trade: The ``Trade`` record.
            ltp: Current last traded price.
            db: Active SQLAlchemy session.

        Returns:
            ``True`` if the trade was exited due to trailing SL hit.
        """
        entry_price = float(trade.entry_price or 0)
        direction = trade.direction

        if entry_price == 0:
            return False

        if direction == "BUY":
            return await self._trailing_sl_buy(trade, ltp, entry_price, db)
        else:
            return await self._trailing_sl_sell(trade, ltp, entry_price, db)

    async def _trailing_sl_buy(
        self, trade: Trade, ltp: float, entry_price: float, db: Session
    ) -> bool:
        """Trailing SL logic for BUY trades.

        Args:
            trade: Trade record.
            ltp: Current LTP.
            entry_price: Entry price.
            db: DB session.

        Returns:
            ``True`` if trade was exited.
        """
        # Update high watermark
        current_high = float(trade.highest_price_since_entry or entry_price)
        if ltp > current_high:
            trade.highest_price_since_entry = ltp
            current_high = ltp

        # Check activation
        favorable_move_pct = ((current_high - entry_price) / entry_price) * 100
        if favorable_move_pct < self._trailing_sl_activation_pct:
            db.commit()
            return False

        # Activate trailing if not already
        if not trade.trailing_sl_activated:
            trade.trailing_sl_activated = True
            logger.info(
                "Trailing SL ACTIVATED (BUY) | trade=%s symbol=%s "
                "entry=%.2f high=%.2f move=%.2f%%",
                trade.id, trade.symbol, entry_price, current_high,
                favorable_move_pct,
            )

        # Compute new trailing SL (ratchet upward only)
        new_trailing_sl = round(
            current_high * (1 - self._trailing_sl_pct / 100), 2
        )
        current_trailing_sl = float(trade.trailing_sl_price or 0)

        if new_trailing_sl > current_trailing_sl:
            trade.trailing_sl_price = new_trailing_sl
            db.commit()

            logger.info(
                "Trailing SL MOVED UP | trade=%s symbol=%s "
                "old_sl=%.2f new_sl=%.2f high=%.2f ltp=%.2f",
                trade.id, trade.symbol,
                current_trailing_sl, new_trailing_sl,
                current_high, ltp,
            )

            # Modify the SL order on the exchange
            await self._modify_sl_order_on_exchange(
                trade, new_trailing_sl, db
            )
        else:
            db.commit()

        # Check if trailing SL is hit
        trailing_sl = float(trade.trailing_sl_price or 0)
        if trailing_sl > 0 and ltp <= trailing_sl:
            logger.info(
                "TRAILING SL HIT (BUY) | trade=%s symbol=%s "
                "ltp=%.2f trailing_sl=%.2f",
                trade.id, trade.symbol, ltp, trailing_sl,
            )
            await self._execute_exit(trade, "TRAILING_SL_HIT", ltp, db)
            return True

        return False

    async def _trailing_sl_sell(
        self, trade: Trade, ltp: float, entry_price: float, db: Session
    ) -> bool:
        """Trailing SL logic for SELL trades (mirror of BUY logic).

        Args:
            trade: Trade record.
            ltp: Current LTP.
            entry_price: Entry price.
            db: DB session.

        Returns:
            ``True`` if trade was exited.
        """
        # Update low watermark
        current_low = float(trade.lowest_price_since_entry or entry_price)
        if ltp < current_low or current_low == 0:
            trade.lowest_price_since_entry = ltp
            current_low = ltp

        # Check activation
        favorable_move_pct = ((entry_price - current_low) / entry_price) * 100
        if favorable_move_pct < self._trailing_sl_activation_pct:
            db.commit()
            return False

        # Activate trailing if not already
        if not trade.trailing_sl_activated:
            trade.trailing_sl_activated = True
            logger.info(
                "Trailing SL ACTIVATED (SELL) | trade=%s symbol=%s "
                "entry=%.2f low=%.2f move=%.2f%%",
                trade.id, trade.symbol, entry_price, current_low,
                favorable_move_pct,
            )

        # Compute new trailing SL (ratchet downward only for SELL)
        new_trailing_sl = round(
            current_low * (1 + self._trailing_sl_pct / 100), 2
        )
        current_trailing_sl = float(trade.trailing_sl_price or 0)

        if current_trailing_sl == 0 or new_trailing_sl < current_trailing_sl:
            trade.trailing_sl_price = new_trailing_sl
            db.commit()

            logger.info(
                "Trailing SL MOVED DOWN | trade=%s symbol=%s "
                "old_sl=%.2f new_sl=%.2f low=%.2f ltp=%.2f",
                trade.id, trade.symbol,
                current_trailing_sl, new_trailing_sl,
                current_low, ltp,
            )

            # Modify the SL order on the exchange
            await self._modify_sl_order_on_exchange(
                trade, new_trailing_sl, db
            )
        else:
            db.commit()

        # Check if trailing SL is hit
        trailing_sl = float(trade.trailing_sl_price or 0)
        if trailing_sl > 0 and ltp >= trailing_sl:
            logger.info(
                "TRAILING SL HIT (SELL) | trade=%s symbol=%s "
                "ltp=%.2f trailing_sl=%.2f",
                trade.id, trade.symbol, ltp, trailing_sl,
            )
            await self._execute_exit(trade, "TRAILING_SL_HIT", ltp, db)
            return True

        return False

    async def _modify_sl_order_on_exchange(
        self, trade: Trade, new_trigger_price: float, db: Session
    ) -> None:
        """Modify the existing SL-M order on the exchange to trail the stop.

        Finds the active STOPLOSS order for this trade and calls
        ``broker.modify_order()`` to update the trigger price.

        Args:
            trade: The ``Trade`` record.
            new_trigger_price: New trigger price for the SL-M order.
            db: Active SQLAlchemy session.
        """
        sl_order = (
            db.query(Order)
            .filter(
                Order.trade_id == trade.id,
                Order.order_type == "STOPLOSS",
                Order.status.in_(["PENDING", "PLACED"]),
            )
            .first()
        )

        if not sl_order or not sl_order.broker_order_id:
            logger.warning(
                "No active SL order found for trade %s — cannot modify",
                trade.id,
            )
            return

        # Resolve broker adapter
        user = db.query(User).filter(User.id == trade.user_id).first()
        if not user:
            return

        broker = self._brokers.get(user.broker_client_id)
        if not broker:
            broker = next(iter(self._brokers.values()), None)
        if not broker:
            return

        try:
            success = broker.modify_order(
                order_id=sl_order.broker_order_id,
                trigger_price=new_trigger_price,
            )
            if success:
                sl_order.trigger_price = new_trigger_price
                db.commit()
                logger.info(
                    "SL order modified on exchange | order_id=%s "
                    "broker_order_id=%s new_trigger=%.2f",
                    sl_order.id,
                    sl_order.broker_order_id,
                    new_trigger_price,
                )
            else:
                logger.warning(
                    "Broker returned False for SL modify | "
                    "broker_order_id=%s",
                    sl_order.broker_order_id,
                )
        except Exception:
            logger.exception(
                "Failed to modify SL order %s on exchange",
                sl_order.broker_order_id,
            )

    # ------------------------------------------------------------------ #
    #  Trade Exit Execution                                                #
    # ------------------------------------------------------------------ #

    async def _execute_exit(
        self,
        trade: Trade,
        exit_reason: str,
        ltp: float,
        db: Session,
    ) -> None:
        """Place a market exit order and close the trade.

        1. Cancel all pending SL / Target orders for this trade.
        2. Place a market exit order (opposite direction).
        3. Persist exit order in DB.
        4. Close the trade with P&L computation.
        5. Write audit trail record.
        6. Send Telegram notification.

        Args:
            trade: The ``Trade`` record to exit.
            exit_reason: Human-readable reason (``TARGET_HIT``,
                ``STOPLOSS_HIT``, ``TRAILING_SL_HIT``, ``CIRCUIT_BREAKER``,
                ``EMERGENCY_EXIT``).
            ltp: Current last traded price (used as approximate exit price).
            db: Active SQLAlchemy session.
        """
        # Resolve broker
        user = db.query(User).filter(User.id == trade.user_id).first()
        if not user:
            logger.error("User not found for trade %s", trade.id)
            return

        broker = self._brokers.get(user.broker_client_id)
        if not broker:
            broker = next(iter(self._brokers.values()), None)
        if not broker:
            logger.error("No broker available to exit trade %s", trade.id)
            return

        # --- 1. Cancel pending exit orders ----------------------------- #
        pending_orders = (
            db.query(Order)
            .filter(
                Order.trade_id == trade.id,
                Order.order_type.in_(["STOPLOSS", "TARGET"]),
                Order.status.in_(["PENDING", "PLACED"]),
            )
            .all()
        )
        for pending in pending_orders:
            try:
                broker.cancel_order(pending.broker_order_id)
                pending.status = "CANCELLED"
                logger.info(
                    "Monitor: Cancelled %s order %s for trade %s",
                    pending.order_type,
                    pending.broker_order_id,
                    trade.id,
                )
            except Exception:
                logger.exception(
                    "Monitor: Failed to cancel %s order %s",
                    pending.order_type,
                    pending.broker_order_id,
                )
        db.commit()

        # --- 2. Place market exit order -------------------------------- #
        exit_txn = "SELL" if trade.direction == "BUY" else "BUY"

        # Resolve product type from the original ENTRY order (not hardcoded)
        entry_order = (
            db.query(Order)
            .filter(
                Order.trade_id == trade.id,
                Order.order_type == "ENTRY",
            )
            .first()
        )
        product = entry_order.product if entry_order else "MIS"

        try:
            exit_order_id = broker.place_order(
                symbol=trade.symbol,
                exchange=trade.exchange,
                transaction_type=exit_txn,
                quantity=int(trade.quantity),
                price=0,
                trigger_price=None,
                order_type="MARKET",
                product=product,
                tag=trade.algo_id,
            )
            logger.info(
                "Monitor: EXIT order placed | trade=%s symbol=%s "
                "reason=%s product=%s order_id=%s",
                trade.id, trade.symbol, exit_reason, product, exit_order_id,
            )
        except Exception:
            logger.exception(
                "Monitor: Failed to place EXIT order for trade %s (%s)",
                trade.id, trade.symbol,
            )
            await self._notifier.send_message(
                f"🚨 <b>EXIT ORDER FAILED</b>\n\n"
                f"📊 {trade.symbol} {trade.direction}\n"
                f"❌ Reason: {exit_reason}\n"
                f"⚠️ Manual intervention required!"
            )
            return

        # --- 3. Persist exit order ------------------------------------- #
        exit_order = Order(
            trade_id=trade.id,
            broker_order_id=exit_order_id,
            order_type="EXIT",
            transaction_type=exit_txn,
            product=product,
            quantity=int(trade.quantity),
            price=0,
            status="PLACED",
            algo_id=trade.algo_id,
        )
        db.add(exit_order)

        # --- 4. Close the trade with P&L ------------------------------- #
        trade.exit_price = ltp
        trade.status = "CLOSED"
        trade.closed_at = datetime.now(IST)

        entry = float(trade.entry_price or 0)
        qty = int(trade.quantity or 0)

        if trade.direction == "BUY":
            pnl = (ltp - entry) * qty
        else:
            pnl = (entry - ltp) * qty

        trade.pnl = pnl
        db.commit()

        logger.info(
            "Monitor: Trade CLOSED | trade=%s symbol=%s reason=%s "
            "exit_price=%.2f pnl=%.2f",
            trade.id, trade.symbol, exit_reason, ltp, pnl,
        )

        # --- 5. Audit trail -------------------------------------------- #
        try:
            audit = AuditTrail(
                user_id=user.id,
                algo_id=trade.algo_id,
                action="POSITION_CLOSED",
                symbol=trade.symbol,
                exchange=trade.exchange,
                direction=exit_txn,
                quantity=int(trade.quantity),
                price=ltp,
                broker_order_id=exit_order_id,
                source="MONITOR",
                trade_id=trade.id,
                extra={
                    "exit_reason": exit_reason,
                    "trailing_sl_price": float(trade.trailing_sl_price or 0),
                    "trailing_sl_activated": trade.trailing_sl_activated,
                },
            )
            db.add(audit)
            db.commit()
        except Exception:
            logger.exception("Failed to write audit trail for trade %s", trade.id)

        # --- 6. Telegram notification ---------------------------------- #
        reason_emoji = {
            "TARGET_HIT": "🎯",
            "STOPLOSS_HIT": "🛑",
            "TRAILING_SL_HIT": "📐",
            "CIRCUIT_BREAKER": "🔌",
            "EMERGENCY_EXIT": "🚨",
            "AUTO_SQUARE_OFF": "⏰",
        }
        emoji = reason_emoji.get(exit_reason, "📊")
        pnl_emoji = "📈" if pnl >= 0 else "📉"

        await self._notifier.send_message(
            f"{emoji} <b>Monitor Exit — {exit_reason}</b>\n"
            f"\n"
            f"📊 {trade.symbol} {trade.direction}\n"
            f"💰 Entry: ₹{entry:.2f}\n"
            f"💰 Exit: ₹{ltp:.2f}\n"
            f"📦 Qty: {qty}\n"
            f"{pnl_emoji} P&L: ₹{pnl:.2f}\n"
            f"🏷️ Algo-ID: {trade.algo_id}"
        )

        # Remove from price cache (no longer needed)
        instrument_key = f"{trade.exchange}:{trade.symbol}"
        with self._price_cache_lock:
            self._price_cache.pop(instrument_key, None)

    # ------------------------------------------------------------------ #
    #  Daily P&L Circuit Breaker                                           #
    # ------------------------------------------------------------------ #

    async def _check_daily_pnl_circuit_breaker(self, db: Session) -> None:
        """Check aggregate daily P&L and trip circuit breaker if exceeded.

        Queries total realized + unrealized P&L across all active users.
        If the aggregate loss exceeds any user's ``MAX_DAILY_LOSS``,
        triggers an emergency exit for that user's positions.

        Args:
            db: Active SQLAlchemy session.
        """
        if self._circuit_breaker_tripped:
            return

        try:
            active_users = (
                db.query(User)
                .filter(User.is_active.is_(True))
                .all()
            )

            for user in active_users:
                max_daily_loss = float(
                    user.max_daily_loss or settings.max_daily_loss
                )

                # Sum today's realized P&L
                today_start = datetime.now(IST).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                today_trades = (
                    db.query(Trade)
                    .filter(
                        Trade.user_id == user.id,
                        Trade.opened_at >= today_start,
                        Trade.status.in_(["OPEN", "CLOSED"]),
                    )
                    .all()
                )

                realized_pnl = sum(
                    float(t.pnl or 0) for t in today_trades if t.status == "CLOSED"
                )

                # Estimate unrealized P&L from open trades
                unrealized_pnl = 0.0
                for t in today_trades:
                    if t.status != "OPEN":
                        continue
                    instrument_key = f"{t.exchange}:{t.symbol}"
                    with self._price_cache_lock:
                        ltp = self._price_cache.get(instrument_key)
                    if ltp is None:
                        continue
                    entry = float(t.entry_price or 0)
                    qty = int(t.quantity or 0)
                    if t.direction == "BUY":
                        unrealized_pnl += (ltp - entry) * qty
                    else:
                        unrealized_pnl += (entry - ltp) * qty

                total_daily_pnl = realized_pnl + unrealized_pnl

                if total_daily_pnl <= -max_daily_loss:
                    logger.critical(
                        "CIRCUIT BREAKER TRIPPED | user=%s daily_pnl=%.2f "
                        "max_daily_loss=%.2f — liquidating all positions",
                        user.id,
                        total_daily_pnl,
                        max_daily_loss,
                    )

                    await self._notifier.send_message(
                        f"🔌 <b>CIRCUIT BREAKER TRIPPED</b>\n\n"
                        f"👤 User: {user.full_name}\n"
                        f"📉 Daily P&L: ₹{total_daily_pnl:.2f}\n"
                        f"🚫 Max allowed loss: ₹{max_daily_loss:.2f}\n\n"
                        f"⚡ Liquidating ALL open positions..."
                    )

                    # Close all open trades for this user
                    open_trades = [
                        t for t in today_trades if t.status == "OPEN"
                    ] + list(
                        db.query(Trade)
                        .filter(
                            Trade.user_id == user.id,
                            Trade.status == "OPEN",
                            Trade.opened_at < today_start,
                        )
                        .all()
                    )

                    for trade in open_trades:
                        with self._price_cache_lock:
                            ltp = self._price_cache.get(
                                f"{trade.exchange}:{trade.symbol}"
                            )
                        if ltp is None:
                            try:
                                broker = self._brokers.get(user.broker_client_id)
                                if broker:
                                    ltp = broker.get_ltp(trade.symbol, trade.exchange)
                            except Exception:
                                ltp = float(trade.entry_price or 0)

                        await self._execute_exit(
                            trade, "CIRCUIT_BREAKER", ltp or 0, db
                        )

                    # Write audit trail for circuit breaker event
                    audit = AuditTrail(
                        user_id=user.id,
                        algo_id="SYSTEM",
                        action="SYSTEM_EVENT",
                        source="MONITOR",
                        extra={
                            "event": "CIRCUIT_BREAKER_TRIPPED",
                            "daily_pnl": total_daily_pnl,
                            "max_daily_loss": max_daily_loss,
                            "positions_closed": len(open_trades),
                        },
                    )
                    db.add(audit)
                    db.commit()

        except Exception:
            logger.exception("Error in daily P&L circuit breaker check")

    # ------------------------------------------------------------------ #
    #  Emergency Exit (Panic Button)                                       #
    # ------------------------------------------------------------------ #

    async def emergency_exit_all(self, db: Session) -> dict:
        """Cancel all pending orders and close all open positions.

        This is the handler for the ``POST /api/v1/emergency-exit`` endpoint.
        It force-closes every open position across all users with market
        orders.

        Args:
            db: Active SQLAlchemy session.

        Returns:
            Dict with summary: ``positions_closed``, ``total_pnl``,
            ``results`` list.
        """
        self._circuit_breaker_tripped = True

        logger.critical("EMERGENCY EXIT triggered — closing ALL positions")

        await self._notifier.send_message(
            "🚨 <b>EMERGENCY EXIT ACTIVATED</b>\n\n"
            "⚡ Closing ALL open positions across ALL users...\n"
            f"🕐 {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')} IST"
        )

        all_open_trades = (
            db.query(Trade)
            .filter(Trade.status == "OPEN")
            .all()
        )

        results: list[dict] = []
        total_pnl = 0.0

        for trade in all_open_trades:
            try:
                # Get LTP
                instrument_key = f"{trade.exchange}:{trade.symbol}"
                with self._price_cache_lock:
                    ltp = self._price_cache.get(instrument_key)
                if ltp is None:
                    user = db.query(User).filter(User.id == trade.user_id).first()
                    broker = self._brokers.get(
                        user.broker_client_id if user else ""
                    )
                    if not broker:
                        broker = next(iter(self._brokers.values()), None)
                    if broker:
                        try:
                            ltp = broker.get_ltp(trade.symbol, trade.exchange)
                        except Exception:
                            ltp = float(trade.entry_price or 0)
                    else:
                        ltp = float(trade.entry_price or 0)

                await self._execute_exit(trade, "EMERGENCY_EXIT", ltp, db)

                # Refresh trade to get updated pnl
                db.refresh(trade)
                trade_pnl = float(trade.pnl or 0)
                total_pnl += trade_pnl

                results.append({
                    "trade_id": str(trade.id),
                    "symbol": trade.symbol,
                    "direction": trade.direction,
                    "quantity": int(trade.quantity),
                    "exit_price": float(trade.exit_price or 0),
                    "pnl": trade_pnl,
                    "status": "CLOSED",
                })

            except Exception:
                logger.exception(
                    "Emergency exit failed for trade %s (%s)",
                    trade.id, trade.symbol,
                )
                results.append({
                    "trade_id": str(trade.id),
                    "symbol": trade.symbol,
                    "direction": trade.direction,
                    "quantity": int(trade.quantity),
                    "status": "FAILED",
                })

        # Write system audit trail
        try:
            audit = AuditTrail(
                algo_id="SYSTEM",
                action="SYSTEM_EVENT",
                source="EMERGENCY_EXIT",
                extra={
                    "event": "EMERGENCY_EXIT",
                    "positions_closed": len(
                        [r for r in results if r["status"] == "CLOSED"]
                    ),
                    "total_pnl": total_pnl,
                    "timestamp": datetime.now(IST).isoformat(),
                },
            )
            db.add(audit)
            db.commit()
        except Exception:
            logger.exception("Failed to write emergency exit audit trail")

        # Telegram summary
        pnl_emoji = "📈" if total_pnl >= 0 else "📉"
        closed_count = len([r for r in results if r["status"] == "CLOSED"])
        failed_count = len([r for r in results if r["status"] == "FAILED"])

        summary_lines = [
            "🚨 <b>Emergency Exit Complete</b>",
            "",
            f"🕐 {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')} IST",
            f"✅ Closed: {closed_count}",
        ]
        if failed_count:
            summary_lines.append(f"❌ Failed: {failed_count}")

        summary_lines.append("")
        for r in results:
            icon = "✅" if r["status"] == "CLOSED" else "❌"
            pnl_str = f"₹{r.get('pnl', 0):.2f}" if "pnl" in r else "N/A"
            summary_lines.append(
                f"{icon} {r['symbol']} {r['direction']} "
                f"x{r['quantity']} → {pnl_str}"
            )

        summary_lines.extend([
            "",
            f"{pnl_emoji} <b>Total P&L: ₹{total_pnl:.2f}</b>",
        ])

        await self._notifier.send_message("\n".join(summary_lines))

        logger.info(
            "Emergency exit complete | closed=%d failed=%d total_pnl=%.2f",
            closed_count, failed_count, total_pnl,
        )

        return {
            "positions_closed": closed_count,
            "positions_failed": failed_count,
            "total_pnl": total_pnl,
            "results": results,
            "timestamp": datetime.now(IST).isoformat(),
        }

    # ------------------------------------------------------------------ #
    #  Utility: Refresh Subscriptions                                      #
    # ------------------------------------------------------------------ #

    async def refresh_subscriptions(self, db: Session) -> None:
        """Re-scan open trades and update WebSocket instrument subscriptions.

        Call this after a new trade is opened to ensure the ticker is
        receiving price updates for the new instrument.

        Args:
            db: Active SQLAlchemy session.
        """
        for client_id, ticker in self._tickers.items():
            try:
                broker = self._brokers.get(client_id)
                # Determine ticker type and call appropriate subscribe method
                if broker and hasattr(broker, "kite"):
                    self._subscribe_open_trade_instruments(client_id, ticker)
                elif broker and hasattr(broker, "smart"):
                    self._subscribe_angel_instruments(client_id, ticker)
                else:
                    self._subscribe_open_trade_instruments(client_id, ticker)
            except Exception:
                logger.warning(
                    "Failed to refresh subscriptions for broker %s",
                    client_id,
                    exc_info=True,
                )

    @property
    def is_running(self) -> bool:
        """Whether the monitor loop is currently active."""
        return self._running

    @property
    def price_cache(self) -> dict[str, float]:
        """Read-only snapshot of the in-memory price cache (thread-safe)."""
        with self._price_cache_lock:
            return dict(self._price_cache)

    @property
    def circuit_breaker_tripped(self) -> bool:
        """Whether the daily P&L circuit breaker has been triggered."""
        return self._circuit_breaker_tripped
