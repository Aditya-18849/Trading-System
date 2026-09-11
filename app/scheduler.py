"""
APScheduler Integration for Market Open/Close Triggers.

Manages scheduled jobs for:
- Market open (09:15 IST): Start strategy engine pipeline
- Market close (15:30 IST): End-of-day performance logging + daily report
- Market data polling: Every 1-5 minutes during market hours
- NSE trading holiday awareness
"""

import logging
from datetime import datetime, time, timezone, timedelta
from typing import Optional, List, Callable, Awaitable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

# IST timezone
IST = timezone(timedelta(hours=5, minutes=30))

# Market hours (IST)
MARKET_OPEN_TIME = time(9, 15)
MARKET_CLOSE_TIME = time(15, 30)
PRE_MARKET_TIME = time(9, 0)  # Pre-market data fetch

# NSE 2024-2025 Trading Holidays (extend as needed)
# Format: (month, day) - these are fixed holidays
NSE_HOLIDAYS_2024 = [
    (1, 26),   # Republic Day
    (3, 8),    # Mahashivratri
    (3, 25),   # Holi
    (3, 29),   # Good Friday
    (4, 11),   # Id-Ul-Fitr
    (4, 17),   # Ram Navami
    (5, 1),    # Maharashtra Day
    (6, 17),   # Bakri Id
    (7, 17),   # Muharram
    (8, 15),   # Independence Day
    (10, 2),   # Gandhi Jayanti
    (10, 12),  # Dussehra
    (11, 1),   # Diwali Laxmi Pujan
    (11, 15),  # Gurunanak Jayanti
    (12, 25),  # Christmas
]

NSE_HOLIDAYS_2025 = [
    (1, 26),   # Republic Day
    (2, 26),   # Mahashivratri
    (3, 14),   # Holi
    (3, 31),   # Id-Ul-Fitr
    (4, 10),   # Ram Navami
    (4, 14),   # Dr. Ambedkar Jayanti
    (4, 18),   # Good Friday
    (5, 1),    # Maharashtra Day
    (6, 7),    # Bakri Id
    (7, 6),    # Muharram
    (8, 15),   # Independence Day
    (10, 2),   # Gandhi Jayanti
    (10, 21),  # Diwali Laxmi Pujan
    (11, 5),   # Gurunanak Jayanti
    (12, 25),  # Christmas
]


class TradingScheduler:
    """Manages all scheduled jobs for the trading system."""

    def __init__(self, timezone_str: str = "Asia/Kolkata"):
        """Initialize the scheduler.

        Args:
            timezone_str: IANA timezone string (default Asia/Kolkata for IST).
        """
        self.scheduler = AsyncIOScheduler(timezone=timezone_str)
        self._market_open_callbacks: List[Callable[[], Awaitable[None]]] = []
        self._market_close_callbacks: List[Callable[[], Awaitable[None]]] = []
        self._polling_callbacks: List[Callable[[], Awaitable[None]]] = []
        self._started = False

    def is_trading_day(self, dt: Optional[datetime] = None) -> bool:
        """Check if a given date is an NSE trading day.

        Args:
            dt: Datetime to check (default: now in IST).

        Returns:
            True if it's a trading day (weekday and not a holiday).
        """
        dt = dt or datetime.now(IST)
        # Weekend check
        if dt.weekday() >= 5:  # Sat=5, Sun=6
            return False

        # Holiday check
        date_tuple = (dt.month, dt.day)
        year = dt.year
        holidays = NSE_HOLIDAYS_2024 if year == 2024 else NSE_HOLIDAYS_2025
        if date_tuple in holidays:
            return False

        return True

    def add_market_open_callback(self, callback: Callable[[], Awaitable[None]]):
        """Register a callback to run at market open (09:15 IST)."""
        self._market_open_callbacks.append(callback)

    def add_market_close_callback(self, callback: Callable[[], Awaitable[None]]):
        """Register a callback to run at market close (15:30 IST)."""
        self._market_close_callbacks.append(callback)

    def add_polling_callback(self, callback: Callable[[], Awaitable[None]]):
        """Register a callback for periodic polling during market hours."""
        self._polling_callbacks.append(callback)

    async def _run_market_open(self):
        """Execute all market open callbacks."""
        if not self.is_trading_day():
            logger.info("Skipping market open - not a trading day")
            return

        logger.info("=== MARKET OPEN: Running strategy engine pipeline ===")
        for callback in self._market_open_callbacks:
            try:
                await callback()
            except Exception as e:
                logger.exception("Market open callback failed: %s", e)

    async def _run_market_close(self):
        """Execute all market close callbacks."""
        logger.info("=== MARKET CLOSE: Running EOD tasks ===")
        for callback in self._market_close_callbacks:
            try:
                await callback()
            except Exception as e:
                logger.exception("Market close callback failed: %s", e)

        # Automated daily database snapshot
        try:
            from scripts.backup_db import backup_database
            backup_database()
            logger.info("Daily database backup completed successfully.")
        except Exception as b_err:
            logger.warning("Daily database backup notice: %s", b_err)

    async def _run_polling(self):
        """Execute all polling callbacks (only during market hours)."""
        now = datetime.now(IST)

        # Only run during market hours on trading days
        if not self.is_trading_day(now):
            return

        current_time = now.time()
        if not (MARKET_OPEN_TIME <= current_time <= MARKET_CLOSE_TIME):
            return

        for callback in self._polling_callbacks:
            try:
                await callback()
            except Exception as e:
                logger.exception("Polling callback failed: %s", e)

    def start(self):
        """Start the scheduler with all configured jobs."""
        if self._started:
            logger.warning("Scheduler already started")
            return

        # Market open job (09:15 IST, Mon-Fri, excluding holidays)
        self.scheduler.add_job(
            self._run_market_open,
            CronTrigger(
                day_of_week="mon-fri",
                hour=MARKET_OPEN_TIME.hour,
                minute=MARKET_OPEN_TIME.minute,
                timezone="Asia/Kolkata",
            ),
            id="market_open",
            name="Market Open - Strategy Engine Pipeline",
            misfire_grace_time=300,  # 5 min grace
            replace_existing=True,
        )

        # Market close job (15:30 IST, Mon-Fri)
        self.scheduler.add_job(
            self._run_market_close,
            CronTrigger(
                day_of_week="mon-fri",
                hour=MARKET_CLOSE_TIME.hour,
                minute=MARKET_CLOSE_TIME.minute,
                timezone="Asia/Kolkata",
            ),
            id="market_close",
            name="Market Close - EOD Tasks",
            misfire_grace_time=300,
            replace_existing=True,
        )

        # Polling job (every 60 seconds during market hours)
        # The callback itself checks market hours
        self.scheduler.add_job(
            self._run_polling,
            IntervalTrigger(seconds=60, timezone="Asia/Kolkata"),
            id="market_polling",
            name="Market Data Polling",
            misfire_grace_time=120,
            replace_existing=True,
        )

        # Pre-market data fetch (09:00 IST) - warm up data
        self.scheduler.add_job(
            self._run_polling,
            CronTrigger(
                day_of_week="mon-fri",
                hour=PRE_MARKET_TIME.hour,
                minute=PRE_MARKET_TIME.minute,
                timezone="Asia/Kolkata",
            ),
            id="pre_market_fetch",
            name="Pre-Market Data Fetch",
            misfire_grace_time=300,
            replace_existing=True,
        )

        self.scheduler.start()
        self._started = True
        logger.info(
            "TradingScheduler started with jobs: market_open (09:15), "
            "market_close (15:30), polling (60s), pre_market (09:00)"
        )

    def shutdown(self, wait: bool = True):
        """Shutdown the scheduler."""
        if self._started:
            self.scheduler.shutdown(wait=wait)
            self._started = False
            logger.info("TradingScheduler shut down")

    def get_jobs(self) -> List[dict]:
        """Get list of scheduled jobs with next run times."""
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                "trigger": str(job.trigger),
            })
        return jobs


# Global scheduler instance
_trading_scheduler: Optional[TradingScheduler] = None


def get_trading_scheduler() -> TradingScheduler:
    """Get or create the global trading scheduler instance."""
    global _trading_scheduler
    if _trading_scheduler is None:
        _trading_scheduler = TradingScheduler()
    return _trading_scheduler


async def start_scheduler():
    """Start the global trading scheduler."""
    scheduler = get_trading_scheduler()
    scheduler.start()


async def shutdown_scheduler():
    """Shutdown the global trading scheduler."""
    global _trading_scheduler
    if _trading_scheduler:
        _trading_scheduler.shutdown()
        _trading_scheduler = None