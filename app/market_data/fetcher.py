"""
Market Data Fetcher — Watchlist Polling Service.

Polls live market data for configured watchlist symbols at regular intervals
during market hours and persists raw candles to the market_data table.
"""

import logging
import asyncio
from datetime import datetime, time, timezone, timedelta
from typing import List, Dict, Optional
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import Watchlist, MarketData, User
from app.broker.kite_data import KiteDataFetcher

logger = logging.getLogger(__name__)

# IST timezone
IST = timezone(timedelta(hours=5, minutes=30))

# Market hours (IST)
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)

# Default polling intervals per priority
DEFAULT_INTERVALS = {
    0: 300,    # Low priority: 5 min
    1: 120,    # Medium: 2 min
    2: 60,     # High: 1 min
    3: 30,     # Critical: 30 sec
}


class MarketDataFetcher:
    """Service for polling and persisting live market data."""

    def __init__(
        self,
        db: Session,
        kite_fetcher: KiteDataFetcher,
        user_id: Optional[str] = None,
    ):
        """Initialize the market data fetcher.

        Args:
            db: Database session.
            kite_fetcher: Initialized KiteDataFetcher instance.
            user_id: Optional user ID to filter watchlist (None = all active users).
        """
        self.db = db
        self.kite = kite_fetcher
        self.user_id = user_id
        self._running = False
        self._tasks: Dict[str, asyncio.Task] = {}

    def is_market_open(self, now: Optional[datetime] = None) -> bool:
        """Check if market is currently open (IST)."""
        now = now or datetime.now(IST)
        # Check if weekday (Mon-Fri)
        if now.weekday() >= 5:  # Sat=5, Sun=6
            return False
        current_time = now.time()
        return MARKET_OPEN <= current_time <= MARKET_CLOSE

    def get_active_watchlist(self) -> List[Watchlist]:
        """Get active watchlist items for the configured user(s)."""
        query = self.db.query(Watchlist).filter(Watchlist.is_active == True)
        if self.user_id:
            query = query.filter(Watchlist.user_id == self.user_id)
        return query.order_by(Watchlist.priority.desc()).all()

    def get_poll_interval(self, priority: int) -> int:
        """Get poll interval in seconds based on priority."""
        return DEFAULT_INTERVALS.get(priority, 300)

    async def fetch_and_persist(self, watchlist_item: Watchlist) -> int:
        """Fetch latest candle for a watchlist item and persist to DB.

        Args:
            watchlist_item: Watchlist ORM object.

        Returns:
            Number of candles persisted (0 or 1 typically).
        """
        symbol = watchlist_item.symbol
        exchange = watchlist_item.exchange
        intervals = watchlist_item.intervals.split(",")

        persisted_count = 0

        for interval in intervals:
            interval = interval.strip()
            if not interval:
                continue

            try:
                # Map interval to Kite format
                kite_interval = self._map_interval(interval)
                if not kite_interval:
                    logger.warning("Unknown interval format: %s", interval)
                    continue

                # Fetch latest candle
                df = self.kite.get_latest_candles(
                    symbol=symbol,
                    exchange=exchange,
                    interval=kite_interval,
                    lookback_days=1,  # Just get today's data
                )

                if df.empty:
                    logger.debug("No data returned for %s %s %s", symbol, exchange, interval)
                    continue

                # Get the latest completed candle (not the current forming one)
                # Kite returns data up to the latest completed interval
                latest = df.iloc[-1]

                # Check if we already have this candle
                existing = self.db.query(MarketData).filter(
                    MarketData.symbol == symbol,
                    MarketData.exchange == exchange,
                    MarketData.interval == interval,
                    MarketData.timestamp == latest["timestamp"],
                ).first()

                if existing:
                    continue  # Already persisted

                # Persist new candle
                candle = MarketData(
                    symbol=symbol,
                    exchange=exchange,
                    interval=interval,
                    open=float(latest["open"]),
                    high=float(latest["high"]),
                    low=float(latest["low"]),
                    close=float(latest["close"]),
                    volume=int(latest["volume"]),
                    timestamp=latest["timestamp"],
                    source="kite",
                )
                self.db.add(candle)
                self.db.commit()
                persisted_count += 1
                logger.debug(
                    "Persisted candle: %s %s %s @ %s",
                    symbol, exchange, interval, latest["timestamp"]
                )

            except Exception as e:
                logger.error(
                    "Failed to fetch/persist for %s %s %s: %s",
                    symbol, exchange, interval, e
                )
                self.db.rollback()

        return persisted_count

    def _map_interval(self, interval: str) -> Optional[str]:
        """Map our interval format to Kite's interval format."""
        mapping = {
            "1m": "minute",
            "1min": "minute",
            "minute": "minute",
            "3m": "3minute",
            "3min": "3minute",
            "5m": "5minute",
            "5min": "5minute",
            "10m": "10minute",
            "10min": "10minute",
            "15m": "15minute",
            "15min": "15minute",
            "30m": "30minute",
            "30min": "30minute",
            "1h": "60minute",
            "60m": "60minute",
            "60min": "60minute",
            "1d": "day",
            "day": "day",
        }
        return mapping.get(interval.lower())

    async def poll_watchlist(self) -> Dict[str, int]:
        """Poll all active watchlist items once.

        Returns:
            Dict mapping symbol to number of candles persisted.
        """
        watchlist = self.get_active_watchlist()
        if not watchlist:
            logger.debug("No active watchlist items found")
            return {}

        results = {}
        for item in watchlist:
            key = f"{item.exchange}:{item.symbol}"
            count = await self.fetch_and_persist(item)
            results[key] = count

        return results

    async def run_polling_loop(self, shutdown_event: asyncio.Event):
        """Run the continuous polling loop during market hours.

        This method runs until shutdown_event is set.
        It respects market hours and adjusts polling frequency by priority.
        """
        self._running = True
        logger.info("Market data polling loop started")

        # Track last poll time per symbol
        last_poll: Dict[str, datetime] = {}

        while not shutdown_event.is_set():
            try:
                now = datetime.now(IST)

                if not self.is_market_open(now):
                    # Market closed - sleep longer and check again
                    await asyncio.sleep(60)
                    continue

                watchlist = self.get_active_watchlist()
                for item in watchlist:
                    key = f"{item.exchange}:{item.symbol}"
                    interval_sec = self.get_poll_interval(item.priority)

                    # Check if it's time to poll this symbol
                    last = last_poll.get(key)
                    if last is None or (now - last).total_seconds() >= interval_sec:
                        # Run fetch in background to not block other symbols
                        asyncio.create_task(self._poll_symbol(key, item))
                        last_poll[key] = now

                # Sleep a bit before next check
                await asyncio.sleep(10)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Error in polling loop: %s", e)
                await asyncio.sleep(30)

        self._running = False
        logger.info("Market data polling loop stopped")

    async def _poll_symbol(self, key: str, item: Watchlist):
        """Poll a single symbol (fire and forget)."""
        try:
            await self.fetch_and_persist(item)
        except Exception as e:
            logger.error("Background poll failed for %s: %s", key, e)

    def start_background_polling(self):
        """Start the polling loop as a background task."""
        loop = asyncio.get_event_loop()
        shutdown_event = asyncio.Event()
        self._shutdown_event = shutdown_event
        self._poll_task = loop.create_task(self.run_polling_loop(shutdown_event))
        return shutdown_event

    def stop_background_polling(self):
        """Stop the background polling loop."""
        if hasattr(self, '_shutdown_event'):
            self._shutdown_event.set()
        if hasattr(self, '_poll_task'):
            self._poll_task.cancel()


# ---------------------------------------------------------------------------
# Standalone function for scheduler integration
# ---------------------------------------------------------------------------

async def run_market_data_fetch_once():
    """Run a single market data fetch cycle for all active users.

    This is designed to be called by the scheduler at regular intervals
    during market hours (e.g., every 1-5 minutes).
    """
    db = SessionLocal()
    try:
        # Get all active users with brokers
        users = db.query(User).filter(User.is_active == True).all()

        for user in users:
            # Initialize Kite fetcher for this user
            if not user.kite_access_token:
                logger.warning("No access token for user %s", user.id)
                continue

            kite_fetcher = KiteDataFetcher(
                api_key=settings.kite_api_key,
                api_secret=settings.kite_api_secret,
                access_token=user.kite_access_token,
            )

            fetcher = MarketDataFetcher(db, kite_fetcher, user_id=str(user.id))
            results = await fetcher.poll_watchlist()

            total_candles = sum(results.values())
            if total_candles > 0:
                logger.info(
                    "Market data fetch complete for user %s: %d candles persisted",
                    user.id, total_candles
                )

    finally:
        db.close()


if __name__ == "__main__":
    # For testing
    asyncio.run(run_market_data_fetch_once())