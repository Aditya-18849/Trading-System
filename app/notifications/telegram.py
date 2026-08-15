"""
Telegram Notification Module for SEBI-Compliant Algo Trading System.

Provides async methods to send rich HTML-formatted messages to a Telegram
chat via the Bot API.  All timestamps use IST (Asia/Kolkata, UTC+05:30).
"""

import logging
from datetime import datetime, timezone, timedelta

import httpx

logger = logging.getLogger(__name__)

# IST timezone offset (UTC+05:30)
IST = timezone(timedelta(hours=5, minutes=30))


class TelegramNotifier:
    """Async Telegram bot notifier.

    Uses ``httpx.AsyncClient`` to communicate with the Telegram Bot API.
    Each public method formats a specific message category and delegates
    delivery to :meth:`send_message`.
    """

    def __init__(self, bot_token: str, chat_id: str):
        """Initialise the notifier with Telegram credentials.

        Args:
            bot_token: Telegram Bot API token.
            chat_id: Target chat / channel ID for outgoing messages.
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{bot_token}"

    # ------------------------------------------------------------------
    # Core send method
    # ------------------------------------------------------------------

    async def send_message(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a text message to the configured Telegram chat.

        Args:
            text: Message body (may contain HTML tags when ``parse_mode``
                is ``'HTML'``).
            parse_mode: Telegram parse mode (``HTML`` or ``Markdown``).

        Returns:
            ``True`` if the API responded with ``ok: true``, ``False``
            on any error.
        """
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.api_url}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": parse_mode,
                    },
                )
                data = response.json()
                if data.get("ok"):
                    return True

                logger.error(
                    "Telegram API error: %s",
                    data.get("description", "Unknown error"),
                )
                return False
        except Exception as exc:
            logger.error("Failed to send Telegram message: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Domain-specific notifications
    # ------------------------------------------------------------------

    async def send_trade_notification(self, trade_details: dict) -> bool:
        """Send a trade-executed notification.

        Args:
            trade_details: Dict with keys ``symbol``, ``direction``,
                ``quantity``, ``entry_price``, ``stoploss_price``,
                ``target_price``, ``algo_id``, ``daily_pnl``,
                ``trade_count``, ``max_trades``.

        Returns:
            Delivery success flag.
        """
        message = (
            "🔔 <b>Trade Executed</b>\n"
            "\n"
            f"📊 Symbol: {trade_details.get('symbol', 'N/A')}\n"
            f"↕️ Direction: {trade_details.get('direction', 'N/A')}\n"
            f"📦 Quantity: {trade_details.get('quantity', 'N/A')}\n"
            f"💰 Entry: ₹{trade_details.get('entry_price', 'N/A')}\n"
            f"🛑 Stop-Loss: ₹{trade_details.get('stoploss_price', 'N/A')}\n"
            f"🎯 Target: ₹{trade_details.get('target_price', 'N/A')}\n"
            f"🏷️ Algo-ID: {trade_details.get('algo_id', 'N/A')}\n"
            "\n"
            f"📈 Daily P&L: ₹{trade_details.get('daily_pnl', 0)}\n"
            f"📊 Trades Today: {trade_details.get('trade_count', 0)}"
            f"/{trade_details.get('max_trades', 0)}"
        )
        return await self.send_message(message)

    async def send_error_alert(self, error: Exception, context: str = "") -> bool:
        """Send a system-error alert.

        Args:
            error: The exception that was raised.
            context: Optional description of where the error occurred.

        Returns:
            Delivery success flag.
        """
        now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        error_type = type(error).__name__
        error_message = str(error)

        message = (
            "🚨 <b>SYSTEM ERROR</b>\n"
            "\n"
            f"⚠️ {error_type}: {error_message}\n"
            f"📍 Context: {context}\n"
            f"🕐 Time: {now_ist}"
        )
        return await self.send_message(message)

    async def send_daily_summary(
        self, pnl: float, trade_count: int, capital: float
    ) -> bool:
        """Send an end-of-day P&L summary.

        Args:
            pnl: Net profit/loss for the day.
            trade_count: Number of trades executed today.
            capital: Current capital balance.

        Returns:
            Delivery success flag.
        """
        today_ist = datetime.now(IST).strftime("%Y-%m-%d")
        pnl_emoji = "📈" if pnl >= 0 else "📉"

        message = (
            "📋 <b>Daily Summary</b>\n"
            "\n"
            f"💰 P&L: ₹{pnl:.2f} {pnl_emoji}\n"
            f"📊 Trades: {trade_count}\n"
            f"🏦 Capital: ₹{capital:.2f}\n"
            f"📅 Date: {today_ist}"
        )
        return await self.send_message(message)

    async def send_rejection_alert(
        self, symbol: str, direction: str, reason: str
    ) -> bool:
        """Send a trade-rejection alert.

        Args:
            symbol: Trading symbol that was rejected.
            direction: Intended trade direction.
            reason: Human-readable rejection reason from the risk engine.

        Returns:
            Delivery success flag.
        """
        message = (
            "⛔ <b>Trade Rejected</b>\n"
            "\n"
            f"📊 {symbol} {direction}\n"
            f"❌ Reason: {reason}"
        )
        return await self.send_message(message)
