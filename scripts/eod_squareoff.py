"""
End-of-Day (EOD) Position Squareoff Script.

Closes all open MIS positions before market close at 3:15 PM IST by
placing market exit orders and cancelling any pending SL/Target orders.

Run via cron at 15:10 IST (09:40 UTC) on trading days::

    40 9 * * 1-5 cd /path/to/project && python -m scripts.eod_squareoff

Flow
----
1. Load application settings from ``app.config``.
2. Obtain a database session.
3. Locate the active user.
4. Initialise the broker adapter using the stored ``kite_access_token``.
5. Initialise the Telegram notifier.
6. Create an :class:`OrderManager` and call
   :meth:`~OrderManager.close_all_positions`.
7. Send a Telegram summary of all closures.
8. Log results and exit.
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone, timedelta

from app.config import settings
from app.database import SessionLocal
from app.models import User
from app.broker.kite import KiteAdapter
from app.notifications.telegram import TelegramNotifier
from app.order_manager import OrderManager

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)

# IST timezone offset (UTC+05:30)
IST = timezone(timedelta(hours=5, minutes=30))


async def main() -> None:
    """Orchestrate end-of-day squareoff of all open MIS positions."""

    now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    logger.info("EOD squareoff started at %s", now_ist)

    # ------------------------------------------------------------------ #
    # 1. Database session                                                 #
    # ------------------------------------------------------------------ #
    db = SessionLocal()

    try:
        # -------------------------------------------------------------- #
        # 2. Find the active user                                         #
        # -------------------------------------------------------------- #
        user = db.query(User).filter(User.is_active == True).first()  # noqa: E712
        if user is None:
            logger.error("No active user found – aborting EOD squareoff")
            sys.exit(1)

        logger.info(
            "Active user found | user_id=%s name=%s broker_client_id=%s",
            user.id,
            user.full_name,
            user.broker_client_id,
        )

        # -------------------------------------------------------------- #
        # 3. Validate access token                                        #
        # -------------------------------------------------------------- #
        if not user.kite_access_token:
            logger.error(
                "No access token for user %s – run daily_token_refresh first",
                user.id,
            )
            sys.exit(1)

        # -------------------------------------------------------------- #
        # 4. Initialise broker adapter                                    #
        # -------------------------------------------------------------- #
        broker = KiteAdapter(
            api_key=settings.kite_api_key,
            api_secret=settings.kite_api_secret,
            access_token=user.kite_access_token,
            algo_id=settings.kite_algo_id,
        )

        logger.info("Broker adapter initialised for %s", user.broker_client_id)

        # -------------------------------------------------------------- #
        # 5. Initialise Telegram notifier                                 #
        # -------------------------------------------------------------- #
        notifier = TelegramNotifier(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )

        # -------------------------------------------------------------- #
        # 6. Create OrderManager and close all positions                  #
        # -------------------------------------------------------------- #
        order_manager = OrderManager(db=db, broker=broker, notifier=notifier)

        results = await order_manager.close_all_positions(user.id)

        # -------------------------------------------------------------- #
        # 7. Log results                                                  #
        # -------------------------------------------------------------- #
        closed = [r for r in results if r["status"] == "CLOSED"]
        failed = [r for r in results if r["status"] == "FAILED"]
        total_pnl = sum(r.get("pnl", 0) or 0 for r in closed)

        logger.info(
            "EOD squareoff complete | total=%d closed=%d failed=%d pnl=%.2f",
            len(results),
            len(closed),
            len(failed),
            total_pnl,
        )

        if failed:
            logger.warning(
                "Some positions failed to close: %s",
                [f["symbol"] for f in failed],
            )
            await notifier.send_message(
                "🚨 <b>EOD Squareoff – Failures</b>\n"
                "\n"
                f"⚠️ {len(failed)} position(s) failed to close:\n"
                + "\n".join(f"  • {f['symbol']} {f['direction']}" for f in failed)
                + "\n\n⚠️ Manual intervention required."
            )

        # -------------------------------------------------------------- #
        # 8. Final Telegram notification                                  #
        # -------------------------------------------------------------- #
        end_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        pnl_emoji = "📈" if total_pnl >= 0 else "📉"

        await notifier.send_message(
            f"🏁 <b>EOD Squareoff Finished</b>\n"
            f"\n"
            f"🕐 {end_ist}\n"
            f"📊 Closed: {len(closed)} | Failed: {len(failed)}\n"
            f"{pnl_emoji} Day P&L: ₹{total_pnl:.2f}\n"
            f"👤 {user.full_name} ({user.broker_client_id})"
        )

        logger.info("EOD squareoff script finished successfully")

    except Exception:
        logger.exception("Unhandled error during EOD squareoff")

        # Attempt to send error notification
        try:
            error_notifier = TelegramNotifier(
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
            )
            await error_notifier.send_message(
                "🚨 <b>EOD Squareoff FAILED</b>\n"
                "\n"
                "An unhandled error occurred during the squareoff process.\n"
                "Check server logs for details.\n"
                f"🕐 {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}"
            )
        except Exception:
            logger.exception("Failed to send error notification via Telegram")

        sys.exit(1)

    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
