"""
Order Lifecycle State Machine for SEBI-Compliant Algo Trading System.

Manages the full lifecycle of trades after orders are placed on the exchange.
Responsibilities include:

- Processing order-update postbacks from the broker (Kite / Angel One)
- Transitioning Order and Trade statuses through their state machines
- Auto-closing trades when a stoploss or target exit leg fills
- Cancelling the opposite exit leg when one fills (SL fills → cancel target,
  and vice-versa)
- Computing realized P&L and persisting it
- Sending real-time Telegram notifications for every lifecycle event

State Machines
--------------
**Order**::

    PENDING → PLACED → COMPLETE | REJECTED | CANCELLED

**Trade**::

    PENDING → OPEN → CLOSED | CANCELLED
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.broker.base import BrokerAdapter
from app.models import Trade, Order
from app.notifications.telegram import TelegramNotifier

logger = logging.getLogger(__name__)

# IST timezone offset (UTC+05:30)
IST = timezone(timedelta(hours=5, minutes=30))


# ------------------------------------------------------------------ #
#  Pydantic schema for broker postback payload                        #
# ------------------------------------------------------------------ #

class OrderUpdatePayload(BaseModel):
    """Schema for incoming order-update postbacks from the broker.

    Attributes:
        order_id: Broker-assigned order ID that maps to
            ``Order.broker_order_id``.
        status: Terminal order status – ``COMPLETE``, ``REJECTED``, or
            ``CANCELLED``.
        filled_quantity: Number of shares / lots filled (may differ from
            the original order quantity for partial fills).
        average_price: Volume-weighted average fill price.
        transaction_type: ``BUY`` or ``SELL``.
        tradingsymbol: Exchange trading symbol (e.g. ``RELIANCE``).
        exchange: Exchange segment (``NSE``, ``NFO``, etc.).
        order_type: Broker order type string (``MARKET``, ``LIMIT``,
            ``SL``, ``SL-M``).
        checksum: Optional HMAC signature for verifying postback
            authenticity.
    """

    order_id: str
    status: str  # COMPLETE, REJECTED, CANCELLED
    filled_quantity: Optional[int] = None
    average_price: Optional[float] = None
    transaction_type: Optional[str] = None  # BUY / SELL
    tradingsymbol: Optional[str] = None
    exchange: Optional[str] = None
    order_type: Optional[str] = None
    checksum: Optional[str] = None  # for signature validation


# ------------------------------------------------------------------ #
#  OrderManager – core lifecycle engine                               #
# ------------------------------------------------------------------ #

class OrderManager:
    """Manages the full order / trade lifecycle for the algo trading system.

    This class acts as the central state-machine controller.  It receives
    raw broker postbacks, correlates them with internal ``Order`` and
    ``Trade`` records, and orchestrates status transitions, exit-leg
    cancellations, P&L computation, and notifications.

    Args:
        db: Active SQLAlchemy session.
        broker: Concrete :class:`BrokerAdapter` instance for the user's
            broker.
        notifier: :class:`TelegramNotifier` for sending lifecycle alerts.
    """

    def __init__(self, db: Session, broker: BrokerAdapter, notifier: TelegramNotifier):
        self.db = db
        self.broker = broker
        self.notifier = notifier

    # ------------------------------------------------------------------ #
    #  Public API                                                         #
    # ------------------------------------------------------------------ #

    async def process_order_update(self, order_update: dict) -> None:
        """Process a raw order-update postback from the broker.

        This is the primary entry point for all broker callbacks.  The
        method validates the payload, looks up the corresponding
        ``Order`` record, and routes to the appropriate handler based on
        the order type (``ENTRY``, ``STOPLOSS``, ``TARGET``).

        Args:
            order_update: Raw dict from the broker postback webhook.
                Must conform to :class:`OrderUpdatePayload`.
        """
        payload = OrderUpdatePayload(**order_update)

        logger.info(
            "Processing order update | broker_order_id=%s status=%s",
            payload.order_id,
            payload.status,
        )

        # --- Look up the internal Order record ------------------------ #
        order: Order | None = (
            self.db.query(Order)
            .filter(Order.broker_order_id == payload.order_id)
            .first()
        )
        if order is None:
            logger.warning(
                "Received postback for unknown broker_order_id=%s – ignoring",
                payload.order_id,
            )
            return

        # --- Update raw order fields ---------------------------------- #
        order.status = payload.status
        if payload.average_price is not None:
            order.price = payload.average_price
        if payload.filled_quantity is not None:
            order.quantity = payload.filled_quantity
        self.db.commit()

        logger.info(
            "Order updated | order_id=%s type=%s new_status=%s",
            order.id,
            order.order_type,
            payload.status,
        )

        # --- Fetch the parent Trade ----------------------------------- #
        trade: Trade | None = (
            self.db.query(Trade)
            .filter(Trade.id == order.trade_id)
            .first()
        )
        if trade is None:
            logger.error(
                "Order %s references non-existent trade_id=%s",
                order.id,
                order.trade_id,
            )
            return

        # --- Route by order type and status --------------------------- #
        if payload.status == "COMPLETE":
            await self._handle_order_complete(order, trade, payload)

        elif payload.status == "REJECTED":
            await self._handle_order_rejected(order, trade, payload)

        elif payload.status == "CANCELLED":
            logger.info(
                "Order cancelled | order_id=%s trade_id=%s type=%s",
                order.id,
                trade.id,
                order.order_type,
            )

    # ------------------------------------------------------------------ #
    #  Status handlers                                                    #
    # ------------------------------------------------------------------ #

    async def _handle_order_complete(
        self,
        order: Order,
        trade: Trade,
        payload: OrderUpdatePayload,
    ) -> None:
        """Handle a COMPLETE postback for an order.

        - **ENTRY** complete → mark trade OPEN, update entry_price.
        - **STOPLOSS / TARGET** complete → cancel opposite leg, close
          trade, compute P&L, notify.

        Args:
            order: The matched ``Order`` record.
            trade: The parent ``Trade`` record.
            payload: Validated postback data.
        """
        if order.order_type == "ENTRY":
            # Entry filled – trade is now live
            trade.status = "OPEN"
            if payload.average_price is not None:
                trade.entry_price = payload.average_price
            self.db.commit()

            logger.info(
                "Trade OPEN | trade_id=%s symbol=%s entry_price=%s",
                trade.id,
                trade.symbol,
                trade.entry_price,
            )

            await self.notifier.send_message(
                f"✅ <b>Entry Filled</b>\n"
                f"📊 {trade.symbol} {trade.direction}\n"
                f"💰 Entry: ₹{trade.entry_price}\n"
                f"📦 Qty: {trade.quantity}\n"
                f"🏷️ Algo-ID: {trade.algo_id}"
            )

        elif order.order_type in ("STOPLOSS", "TARGET"):
            # Exit leg filled – cancel the opposite leg and close trade
            exit_price = float(payload.average_price or order.price or 0)
            exit_type = order.order_type  # "STOPLOSS" or "TARGET"

            logger.info(
                "%s filled | trade_id=%s exit_price=%.2f",
                exit_type,
                trade.id,
                exit_price,
            )

            # Cancel the opposite exit order
            self._cancel_opposite_leg(trade, exit_type)

            # Close the trade and compute P&L
            self._close_trade(trade, exit_price, exit_type)

            # Notify via Telegram
            pnl_emoji = "📈" if float(trade.pnl) >= 0 else "📉"
            exit_emoji = "🛑" if exit_type == "STOPLOSS" else "🎯"
            await self.notifier.send_message(
                f"{exit_emoji} <b>Trade Closed – {exit_type}</b>\n"
                f"\n"
                f"📊 {trade.symbol} {trade.direction}\n"
                f"💰 Entry: ₹{trade.entry_price}\n"
                f"💰 Exit: ₹{trade.exit_price}\n"
                f"📦 Qty: {trade.quantity}\n"
                f"{pnl_emoji} P&L: ₹{trade.pnl}\n"
                f"🏷️ Algo-ID: {trade.algo_id}"
            )

    async def _handle_order_rejected(
        self,
        order: Order,
        trade: Trade,
        payload: OrderUpdatePayload,
    ) -> None:
        """Handle a REJECTED postback.

        If the **entry** order is rejected, cancel all related exit orders
        and mark the trade as CANCELLED.  If an exit order is rejected, log
        the event and notify (the trade remains open for manual handling).

        Args:
            order: The matched ``Order`` record.
            trade: The parent ``Trade`` record.
            payload: Validated postback data.
        """
        if order.order_type == "ENTRY":
            logger.warning(
                "ENTRY order REJECTED | trade_id=%s symbol=%s – cancelling trade",
                trade.id,
                trade.symbol,
            )

            # Cancel all exit orders for this trade
            exit_orders = (
                self.db.query(Order)
                .filter(
                    Order.trade_id == trade.id,
                    Order.order_type.in_(["STOPLOSS", "TARGET"]),
                    Order.status.in_(["PENDING", "PLACED"]),
                )
                .all()
            )
            for exit_order in exit_orders:
                try:
                    self.broker.cancel_order(exit_order.broker_order_id)
                    exit_order.status = "CANCELLED"
                    logger.info(
                        "Cancelled exit order | order_id=%s type=%s",
                        exit_order.id,
                        exit_order.order_type,
                    )
                except Exception:
                    logger.exception(
                        "Failed to cancel exit order %s for rejected entry",
                        exit_order.id,
                    )

            trade.status = "CANCELLED"
            trade.closed_at = datetime.now(IST)
            self.db.commit()

            await self.notifier.send_message(
                f"⛔ <b>Entry Rejected – Trade Cancelled</b>\n"
                f"\n"
                f"📊 {trade.symbol} {trade.direction}\n"
                f"📦 Qty: {trade.quantity}\n"
                f"🏷️ Algo-ID: {trade.algo_id}"
            )

        else:
            # Exit order rejected – needs manual attention
            logger.error(
                "%s order REJECTED | trade_id=%s – manual intervention required",
                order.order_type,
                trade.id,
            )
            await self.notifier.send_message(
                f"🚨 <b>{order.order_type} Order Rejected</b>\n"
                f"\n"
                f"📊 {trade.symbol} {trade.direction}\n"
                f"⚠️ Trade remains OPEN – manual exit required\n"
                f"🏷️ Algo-ID: {trade.algo_id}"
            )

    # ------------------------------------------------------------------ #
    #  Trade closure helpers                                              #
    # ------------------------------------------------------------------ #

    def _close_trade(self, trade: Trade, exit_price: float, exit_type: str) -> None:
        """Close a trade and compute realized P&L.

        Sets the trade's exit price, status, close timestamp, and
        computes the profit/loss based on direction.

        Args:
            trade: The ``Trade`` record to close.
            exit_price: The price at which the position was exited.
            exit_type: How the trade was closed – ``STOPLOSS``,
                ``TARGET``, or ``MARKET`` (for EOD squareoff).
        """
        trade.exit_price = exit_price
        trade.status = "CLOSED"
        trade.closed_at = datetime.now(IST)

        # Compute realized P&L
        entry = float(trade.entry_price or 0)
        qty = int(trade.quantity or 0)

        if trade.direction == "BUY":
            pnl = (exit_price - entry) * qty
        else:  # SELL
            pnl = (entry - exit_price) * qty

        trade.pnl = pnl
        self.db.commit()

        logger.info(
            "Trade CLOSED | trade_id=%s symbol=%s exit_type=%s "
            "exit_price=%.2f pnl=%.2f",
            trade.id,
            trade.symbol,
            exit_type,
            exit_price,
            pnl,
        )

    def _cancel_opposite_leg(self, trade: Trade, filled_order_type: str) -> None:
        """Cancel the opposite exit leg after one side fills.

        When the stoploss fills, the target order is no longer needed and
        must be cancelled (and vice-versa).  This prevents double-exits
        and orphaned orders on the exchange.

        Args:
            trade: The parent ``Trade`` whose exit leg should be cancelled.
            filled_order_type: The order type that just filled –
                ``STOPLOSS`` or ``TARGET``.
        """
        # Determine which order type to cancel
        if filled_order_type == "STOPLOSS":
            cancel_type = "TARGET"
        elif filled_order_type == "TARGET":
            cancel_type = "STOPLOSS"
        else:
            logger.warning(
                "Unexpected filled_order_type=%s – skipping opposite cancel",
                filled_order_type,
            )
            return

        opposite_orders = (
            self.db.query(Order)
            .filter(
                Order.trade_id == trade.id,
                Order.order_type == cancel_type,
                Order.status.in_(["PENDING", "PLACED"]),
            )
            .all()
        )

        for opp_order in opposite_orders:
            try:
                success = self.broker.cancel_order(opp_order.broker_order_id)
                if success:
                    opp_order.status = "CANCELLED"
                    logger.info(
                        "Cancelled %s order | order_id=%s broker_order_id=%s",
                        cancel_type,
                        opp_order.id,
                        opp_order.broker_order_id,
                    )
                else:
                    logger.warning(
                        "Broker returned False for cancel of %s order %s",
                        cancel_type,
                        opp_order.broker_order_id,
                    )
            except Exception:
                logger.exception(
                    "Failed to cancel %s order %s via broker",
                    cancel_type,
                    opp_order.broker_order_id,
                )

        self.db.commit()

    # ------------------------------------------------------------------ #
    #  Query helpers                                                      #
    # ------------------------------------------------------------------ #

    def get_open_trades(self, user_id: UUID) -> list[Trade]:
        """Retrieve all open trades for a given user.

        Args:
            user_id: UUID of the user.

        Returns:
            List of ``Trade`` records with ``status='OPEN'``.
        """
        return (
            self.db.query(Trade)
            .filter(Trade.user_id == user_id, Trade.status == "OPEN")
            .all()
        )

    # ------------------------------------------------------------------ #
    #  Bulk position closure (used by EOD squareoff)                      #
    # ------------------------------------------------------------------ #

    async def close_all_positions(self, user_id: UUID) -> list[dict]:
        """Close all open positions for a user with market orders.

        Intended for end-of-day (EOD) squareoff or emergency exits.  For
        each open trade:

        1. Place a market order to exit the position.
        2. Cancel any pending SL / Target orders.
        3. Close the trade with the market exit price.

        Args:
            user_id: UUID of the user whose positions should be closed.

        Returns:
            List of dicts summarising each closure, with keys ``symbol``,
            ``direction``, ``quantity``, ``exit_price``, ``pnl``,
            ``status``.
        """
        open_trades = self.get_open_trades(user_id)
        results: list[dict] = []

        if not open_trades:
            logger.info("No open trades to close for user_id=%s", user_id)
            await self.notifier.send_message(
                "📋 <b>EOD Squareoff</b>\n\nNo open positions to close."
            )
            return results

        logger.info(
            "Closing %d open trades for user_id=%s",
            len(open_trades),
            user_id,
        )

        total_pnl = 0.0

        for trade in open_trades:
            closure_result: dict = {
                "symbol": trade.symbol,
                "direction": trade.direction,
                "quantity": int(trade.quantity),
                "exit_price": None,
                "pnl": None,
                "status": "FAILED",
            }

            try:
                # --- 1. Cancel pending exit orders -------------------- #
                pending_orders = (
                    self.db.query(Order)
                    .filter(
                        Order.trade_id == trade.id,
                        Order.order_type.in_(["STOPLOSS", "TARGET"]),
                        Order.status.in_(["PENDING", "PLACED"]),
                    )
                    .all()
                )
                for pending in pending_orders:
                    try:
                        self.broker.cancel_order(pending.broker_order_id)
                        pending.status = "CANCELLED"
                        logger.info(
                            "EOD: Cancelled %s order %s for trade %s",
                            pending.order_type,
                            pending.broker_order_id,
                            trade.id,
                        )
                    except Exception:
                        logger.exception(
                            "EOD: Failed to cancel %s order %s",
                            pending.order_type,
                            pending.broker_order_id,
                        )

                self.db.commit()

                # --- 2. Place market exit order ----------------------- #
                exit_txn = "SELL" if trade.direction == "BUY" else "BUY"
                exit_order_id = self.broker.place_order(
                    symbol=trade.symbol,
                    exchange=trade.exchange,
                    transaction_type=exit_txn,
                    quantity=int(trade.quantity),
                    price=0,
                    trigger_price=None,
                    order_type="MARKET",
                    product="MIS",
                    tag=trade.algo_id,
                )

                logger.info(
                    "EOD: Market exit placed | trade_id=%s symbol=%s order_id=%s",
                    trade.id,
                    trade.symbol,
                    exit_order_id,
                )

                # Persist the exit order in the DB
                exit_order = Order(
                    trade_id=trade.id,
                    broker_order_id=exit_order_id,
                    order_type="EXIT",
                    transaction_type=exit_txn,
                    product="MIS",
                    quantity=int(trade.quantity),
                    price=0,
                    status="PLACED",
                    algo_id=trade.algo_id,
                )
                self.db.add(exit_order)
                self.db.commit()

                # --- 3. Get market price and close trade -------------- #
                exit_price = self.broker.get_ltp(trade.symbol, trade.exchange)
                self._close_trade(trade, exit_price, "MARKET")

                closure_result["exit_price"] = exit_price
                closure_result["pnl"] = float(trade.pnl)
                closure_result["status"] = "CLOSED"
                total_pnl += float(trade.pnl)

                logger.info(
                    "EOD: Trade closed | trade_id=%s symbol=%s pnl=%.2f",
                    trade.id,
                    trade.symbol,
                    float(trade.pnl),
                )

            except Exception:
                logger.exception(
                    "EOD: Failed to close trade %s (%s)",
                    trade.id,
                    trade.symbol,
                )

            results.append(closure_result)

        # --- Send Telegram summary ------------------------------------ #
        now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        pnl_emoji = "📈" if total_pnl >= 0 else "📉"

        summary_lines = [
            "📋 <b>EOD Squareoff Complete</b>",
            "",
            f"🕐 {now_ist}",
            f"📊 Positions closed: {len(results)}",
            "",
        ]
        for r in results:
            status_icon = "✅" if r["status"] == "CLOSED" else "❌"
            pnl_str = f"₹{r['pnl']:.2f}" if r["pnl"] is not None else "N/A"
            summary_lines.append(
                f"{status_icon} {r['symbol']} {r['direction']} "
                f"x{r['quantity']} → {pnl_str}"
            )

        summary_lines.extend([
            "",
            f"{pnl_emoji} <b>Total P&L: ₹{total_pnl:.2f}</b>",
        ])

        await self.notifier.send_message("\n".join(summary_lines))

        logger.info(
            "EOD squareoff complete | trades_closed=%d total_pnl=%.2f",
            len(results),
            total_pnl,
        )

        return results
