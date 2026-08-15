"""
Automated Order Execution Engine for SEBI-Compliant Algo Trading System.

Connects strategy signals to live broker order placement via the full
pipeline::

    StrategySignal → RiskManager → Broker (Kite / Angel) → DB Audit → Telegram

This module is the **single entry point** for converting
:class:`~app.strategies.advanced_engine.StrategySignal` objects into
executed broker orders.  It supports:

- MARKET and LIMIT order modes with automatic SL + Target exit legs.
- EXIT signal handling (closes open positions for the signal's symbol).
- SEBI Algo-ID tagging on **every** order via the ``Strategy.algo_id`` field.
- Full database audit trail (``Trade``, ``Order``, ``AuditTrail``, ``Log``).
- Asynchronous Telegram notifications for placements, fills, and rejections.

Usage
~~~~~
.. code-block:: python

    from app.services.executor import OrderExecutor

    executor = OrderExecutor(
        db=session,
        broker=kite_adapter,
        notifier=telegram_notifier,
        user=user,
        strategy=strategy,
    )
    result = await executor.execute(signal, order_mode="MARKET")

Design notes
~~~~~~~~~~~~
* Fully async public API — Telegram calls use ``asyncio.create_task`` for
  fire-and-forget delivery so broker latency is not compounded.
* The executor never modifies ``main.py``'s webhook flow — it is a parallel
  execution path that can be wired into scheduled loops, CLI scripts, or
  future WebSocket-driven pipelines.
* All broker errors are caught, logged, and surfaced in the
  :class:`ExecutionResult` without crashing the caller.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.broker.base import BrokerAdapter
from app.config import settings
from app.models import (
    AuditTrail,
    Log,
    Order,
    Strategy,
    Trade,
    User,
)
from app.notifications.telegram import TelegramNotifier
from app.services.risk_manager import RiskDecision, RiskManager
from app.strategies.advanced_engine import StrategySignal

logger = logging.getLogger(__name__)

# IST timezone offset (UTC+05:30)
IST = timezone(timedelta(hours=5, minutes=30))


# ─────────────────────────────────────────────────────────────────────────────
# Output model
# ─────────────────────────────────────────────────────────────────────────────

class ExecutionResult(BaseModel):
    """Structured result returned by :meth:`OrderExecutor.execute`.

    Attributes:
        signal_id:        Echoed from the input ``StrategySignal`` for
                          correlation.
        status:           Terminal status — ``EXECUTED``, ``REJECTED``, or
                          ``ERROR``.
        trade_id:         UUID of the persisted ``Trade`` record (``None`` on
                          rejection / error).
        entry_order_id:   Broker-assigned entry order ID.
        sl_order_id:      Broker-assigned stop-loss order ID.
        target_order_id:  Broker-assigned target order ID.
        rejection_reason: Human-readable reason when ``status == "REJECTED"``.
        error_detail:     Exception description when ``status == "ERROR"``.
        timestamp:        IST-aware timestamp of this result.
    """

    signal_id: str
    status: Literal["EXECUTED", "REJECTED", "ERROR"]
    trade_id: Optional[str] = None
    entry_order_id: Optional[str] = None
    sl_order_id: Optional[str] = None
    target_order_id: Optional[str] = None
    rejection_reason: Optional[str] = None
    error_detail: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(IST))


# ─────────────────────────────────────────────────────────────────────────────
# Order Executor
# ─────────────────────────────────────────────────────────────────────────────

class OrderExecutor:
    """End-to-end automated order execution engine.

    Orchestrates the full lifecycle from signal intake to broker order
    placement, database persistence, SEBI audit logging, and Telegram
    notifications.

    Args:
        db:        Active SQLAlchemy session.
        broker:    Concrete :class:`~app.broker.base.BrokerAdapter` (Kite
                   or Angel One) for the target broker.
        notifier:  :class:`~app.notifications.telegram.TelegramNotifier`
                   for async alerts.
        user:      The ``User`` ORM instance on whose behalf orders are
                   placed.
        strategy:  The ``Strategy`` ORM instance that sourced the signal
                   (provides the SEBI ``algo_id``).
    """

    def __init__(
        self,
        db: Session,
        broker: BrokerAdapter,
        notifier: TelegramNotifier,
        user: User,
        strategy: Strategy,
    ) -> None:
        self.db = db
        self.broker = broker
        self.notifier = notifier
        self.user = user
        self.strategy = strategy
        self._risk_manager = RiskManager(db, user)

    # ------------------------------------------------------------------ #
    #  Public API                                                         #
    # ------------------------------------------------------------------ #

    async def execute(
        self,
        signal: StrategySignal,
        order_mode: str = "MARKET",
    ) -> ExecutionResult:
        """Execute the full signal-to-order pipeline.

        Steps:
          1. Log incoming signal.
          2. Validate against risk manager.
          3. Fetch broker LTP.
          4. Assert SEBI Algo-ID is present.
          5. Place 3-leg order set (entry + SL + target).
          6. Persist ``Trade`` and ``Order`` records.
          7. Write ``AuditTrail`` record.
          8. Send Telegram notification.

        For EXIT signals, delegates to :meth:`_handle_exit_signal`.

        Args:
            signal:      A :class:`StrategySignal` to execute.
            order_mode:  ``"MARKET"`` or ``"LIMIT"`` — controls how the
                         entry order is placed.

        Returns:
            An :class:`ExecutionResult` with execution outcome and IDs.
        """
        algo_id = self.strategy.algo_id
        exchange = "NSE"  # Default exchange for equity signals

        # ── Step 1: Log incoming signal ──────────────────────────────────
        self._log_to_db(
            level="INFO",
            source="executor",
            message=f"Signal received: {signal.action} {signal.symbol} "
                    f"@ ₹{signal.entry_price:.2f} | strategy={signal.strategy_name}",
            context={
                "signal_id": signal.signal_id,
                "symbol": signal.symbol,
                "action": signal.action,
                "entry_price": signal.entry_price,
                "stop_loss": signal.stop_loss,
                "target_price": signal.target_price,
                "quantity": signal.quantity,
                "strategy_name": signal.strategy_name,
                "confidence_score": signal.confidence_score,
                "order_mode": order_mode,
            },
        )

        # ── Handle EXIT signals separately ───────────────────────────────
        if signal.action == "EXIT":
            return await self._handle_exit_signal(signal, algo_id, exchange)

        # ── Step 2: Risk validation ──────────────────────────────────────
        try:
            decision: RiskDecision = self._risk_manager.validate_signal(signal)
        except Exception as exc:
            logger.exception(
                "Risk validation failed unexpectedly | signal_id=%s",
                signal.signal_id,
            )
            return self._error_result(signal, f"Risk validation error: {exc}")

        if not decision.approved:
            return await self._handle_rejection(signal, decision, algo_id, exchange)

        # ── Step 3: Fetch broker LTP ─────────────────────────────────────
        try:
            ltp = self.broker.get_ltp(signal.symbol, exchange)
            logger.info(
                "Broker LTP fetched | %s:%s = ₹%.2f",
                exchange, signal.symbol, ltp,
            )
        except Exception as exc:
            logger.warning(
                "LTP fetch failed, using signal entry_price | error=%s", exc,
            )
            ltp = signal.entry_price

        # ── Step 4: Assert SEBI Algo-ID ──────────────────────────────────
        if not algo_id:
            error_msg = (
                "SEBI compliance violation: strategy.algo_id is empty. "
                "Cannot place orders without an exchange-assigned Algo-ID."
            )
            logger.critical(error_msg)
            self._log_to_db("ERROR", "executor", error_msg, context={
                "signal_id": signal.signal_id,
                "strategy_id": str(self.strategy.id),
            })
            return self._error_result(signal, error_msg)

        # Resolved pricing from risk decision
        quantity = decision.final_quantity
        stoploss_price = decision.final_stoploss_price
        target_price = decision.final_target_price
        direction = "BUY" if signal.action == "BUY" else "SELL"

        logger.info(
            "Executing %s | %s %s qty=%d entry=%.2f SL=%.2f TGT=%.2f "
            "mode=%s algo_id=%s",
            signal.signal_id, direction, signal.symbol, quantity,
            ltp, stoploss_price, target_price, order_mode, algo_id,
        )

        # ── Step 5: Place orders via broker ──────────────────────────────
        try:
            if order_mode == "MARKET":
                order_ids = self._place_market_orders(
                    signal.symbol, exchange, direction, quantity,
                    stoploss_price, target_price, algo_id,
                )
            else:
                order_ids = self._place_limit_orders(
                    signal.symbol, exchange, direction, quantity,
                    ltp, stoploss_price, target_price, algo_id,
                )
        except Exception as exc:
            logger.exception(
                "Broker order placement failed | signal_id=%s", signal.signal_id,
            )
            self._write_audit(
                action="ORDER_FAILED",
                symbol=signal.symbol,
                exchange=exchange,
                direction=direction,
                quantity=quantity,
                price=ltp,
                algo_id=algo_id,
                risk_decision="APPROVED",
                source="EXECUTOR",
                raw_payload={
                    "signal_id": signal.signal_id,
                    "error": str(exc),
                    "order_mode": order_mode,
                },
            )
            # Fire-and-forget Telegram error alert
            asyncio.create_task(
                self.notifier.send_error_alert(
                    exc,
                    context=f"Order placement for {signal.symbol} {direction}",
                )
            )
            return self._error_result(signal, f"Broker order placement failed: {exc}")

        # ── Step 6: Persist Trade + Order records ────────────────────────
        trade, order_records = self._persist_trade_and_orders(
            signal=signal,
            direction=direction,
            exchange=exchange,
            quantity=quantity,
            entry_price=ltp,
            stoploss_price=stoploss_price,
            target_price=target_price,
            algo_id=algo_id,
            order_ids=order_ids,
        )

        # ── Step 7: Write AuditTrail ─────────────────────────────────────
        self._write_audit(
            action="ORDER_PLACED",
            symbol=signal.symbol,
            exchange=exchange,
            direction=direction,
            quantity=quantity,
            price=ltp,
            algo_id=algo_id,
            risk_decision="APPROVED",
            source="EXECUTOR",
            trade_id=trade.id,
            broker_order_id=order_ids.get("entry_order_id"),
            raw_payload={
                "signal_id": signal.signal_id,
                "strategy_name": signal.strategy_name,
                "confidence_score": signal.confidence_score,
                "order_mode": order_mode,
                "entry_order_id": order_ids.get("entry_order_id"),
                "sl_order_id": order_ids.get("sl_order_id"),
                "target_order_id": order_ids.get("target_order_id"),
                "stoploss_price": stoploss_price,
                "target_price": target_price,
            },
        )

        # ── Step 8: Telegram notification (fire-and-forget) ──────────────
        daily = self._risk_manager.get_daily_summary()
        asyncio.create_task(
            self.notifier.send_trade_notification({
                "symbol": signal.symbol,
                "direction": direction,
                "quantity": quantity,
                "entry_price": ltp,
                "stoploss_price": stoploss_price,
                "target_price": target_price,
                "algo_id": algo_id,
                "daily_pnl": daily["total_pnl"],
                "trade_count": daily["trade_count"],
                "max_trades": int(
                    self.user.max_trades_per_day or settings.max_trades_per_day
                ),
            })
        )

        logger.info(
            "Execution complete | signal_id=%s trade_id=%s",
            signal.signal_id, trade.id,
        )

        return ExecutionResult(
            signal_id=signal.signal_id,
            status="EXECUTED",
            trade_id=str(trade.id),
            entry_order_id=order_ids.get("entry_order_id"),
            sl_order_id=order_ids.get("sl_order_id"),
            target_order_id=order_ids.get("target_order_id"),
        )

    async def execute_batch(
        self,
        signals: list[StrategySignal],
        order_mode: str = "MARKET",
    ) -> list[ExecutionResult]:
        """Execute multiple signals sequentially.

        Sequential processing ensures that the risk engine's daily state
        (trade count, cumulative P&L) correctly reflects earlier
        executions within the same batch.

        Args:
            signals:     List of ``StrategySignal`` objects to process.
            order_mode:  ``"MARKET"`` or ``"LIMIT"``.

        Returns:
            List of :class:`ExecutionResult` in the same order as inputs.
        """
        results: list[ExecutionResult] = []
        for idx, signal in enumerate(signals, 1):
            logger.info(
                "Batch execution %d/%d | signal_id=%s symbol=%s",
                idx, len(signals), signal.signal_id, signal.symbol,
            )
            result = await self.execute(signal, order_mode=order_mode)
            results.append(result)
        return results

    # ------------------------------------------------------------------ #
    #  EXIT signal handler                                                #
    # ------------------------------------------------------------------ #

    async def _handle_exit_signal(
        self,
        signal: StrategySignal,
        algo_id: str,
        exchange: str,
    ) -> ExecutionResult:
        """Close open positions for the signal's symbol.

        Finds the user's open trade for the given symbol, cancels pending
        SL/Target orders, places a market exit order, and closes the trade.

        Args:
            signal:   The EXIT ``StrategySignal``.
            algo_id:  SEBI Algo-ID for the exit order.
            exchange: Exchange segment.

        Returns:
            An :class:`ExecutionResult` reflecting the closure outcome.
        """
        logger.info(
            "Processing EXIT signal | signal_id=%s symbol=%s",
            signal.signal_id, signal.symbol,
        )

        # Find the open trade for this symbol
        open_trade: Trade | None = (
            self.db.query(Trade)
            .filter(
                Trade.user_id == self.user.id,
                Trade.symbol == signal.symbol,
                Trade.status == "OPEN",
            )
            .order_by(Trade.opened_at.desc())
            .first()
        )

        if open_trade is None:
            msg = f"No open trade found for {signal.symbol} — EXIT ignored"
            logger.warning(msg)
            return ExecutionResult(
                signal_id=signal.signal_id,
                status="REJECTED",
                rejection_reason=msg,
            )

        # Cancel pending SL / Target orders
        pending_orders = (
            self.db.query(Order)
            .filter(
                Order.trade_id == open_trade.id,
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
                    "EXIT: Cancelled %s order %s",
                    pending.order_type, pending.broker_order_id,
                )
            except Exception:
                logger.exception(
                    "EXIT: Failed to cancel %s order %s",
                    pending.order_type, pending.broker_order_id,
                )
        self.db.commit()

        # Place market exit order
        exit_txn = "SELL" if open_trade.direction == "BUY" else "BUY"
        try:
            exit_order_id = self.broker.place_order(
                symbol=signal.symbol,
                exchange=exchange,
                transaction_type=exit_txn,
                quantity=int(open_trade.quantity),
                price=0,
                trigger_price=None,
                order_type="MARKET",
                product="MIS",
                tag=algo_id,
            )
        except Exception as exc:
            logger.exception(
                "EXIT: Market exit order failed | trade_id=%s", open_trade.id,
            )
            asyncio.create_task(
                self.notifier.send_error_alert(
                    exc, context=f"EXIT order for {signal.symbol}",
                )
            )
            return self._error_result(signal, f"EXIT order failed: {exc}")

        # Persist exit order
        exit_order = Order(
            trade_id=open_trade.id,
            broker_order_id=exit_order_id,
            order_type="EXIT",
            transaction_type=exit_txn,
            product="MIS",
            quantity=int(open_trade.quantity),
            price=0,
            status="PLACED",
            algo_id=algo_id,
        )
        self.db.add(exit_order)

        # Close the trade with LTP
        try:
            exit_price = self.broker.get_ltp(signal.symbol, exchange)
        except Exception:
            exit_price = signal.entry_price

        entry = float(open_trade.entry_price or 0)
        qty = int(open_trade.quantity or 0)
        if open_trade.direction == "BUY":
            pnl = (exit_price - entry) * qty
        else:
            pnl = (entry - exit_price) * qty

        open_trade.exit_price = exit_price
        open_trade.pnl = pnl
        open_trade.status = "CLOSED"
        open_trade.closed_at = datetime.now(IST)
        self.db.commit()

        # Audit trail
        self._write_audit(
            action="POSITION_CLOSED",
            symbol=signal.symbol,
            exchange=exchange,
            direction=exit_txn,
            quantity=qty,
            price=exit_price,
            algo_id=algo_id,
            source="EXECUTOR",
            trade_id=open_trade.id,
            broker_order_id=exit_order_id,
            raw_payload={
                "signal_id": signal.signal_id,
                "exit_type": "SIGNAL_EXIT",
                "pnl": pnl,
            },
        )

        # Telegram
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        asyncio.create_task(
            self.notifier.send_message(
                f"🚪 <b>Position Closed — Signal EXIT</b>\n"
                f"\n"
                f"📊 {signal.symbol} {open_trade.direction}\n"
                f"💰 Entry: ₹{entry:.2f}\n"
                f"💰 Exit: ₹{exit_price:.2f}\n"
                f"📦 Qty: {qty}\n"
                f"{pnl_emoji} P&L: ₹{pnl:.2f}\n"
                f"🏷️ Algo-ID: {algo_id}"
            )
        )

        logger.info(
            "EXIT complete | trade_id=%s pnl=%.2f", open_trade.id, pnl,
        )

        return ExecutionResult(
            signal_id=signal.signal_id,
            status="EXECUTED",
            trade_id=str(open_trade.id),
            entry_order_id=exit_order_id,
        )

    # ------------------------------------------------------------------ #
    #  Rejection handler                                                  #
    # ------------------------------------------------------------------ #

    async def _handle_rejection(
        self,
        signal: StrategySignal,
        decision: RiskDecision,
        algo_id: str,
        exchange: str,
    ) -> ExecutionResult:
        """Log and notify a risk-rejected signal.

        Args:
            signal:    The rejected ``StrategySignal``.
            decision:  The ``RiskDecision`` with rejection reason.
            algo_id:   SEBI Algo-ID for audit logging.
            exchange:  Exchange segment.

        Returns:
            An :class:`ExecutionResult` with ``status="REJECTED"``.
        """
        logger.info(
            "Signal REJECTED | signal_id=%s reason=%s",
            signal.signal_id, decision.reason,
        )

        self._log_to_db(
            level="WARNING",
            source="executor",
            message=f"Signal rejected: {decision.reason}",
            context={
                "signal_id": signal.signal_id,
                "symbol": signal.symbol,
                "action": signal.action,
                "reason": decision.reason,
            },
        )

        self._write_audit(
            action="RISK_REJECTED",
            symbol=signal.symbol,
            exchange=exchange,
            direction="BUY" if signal.action == "BUY" else "SELL",
            quantity=signal.quantity,
            price=signal.entry_price,
            algo_id=algo_id,
            risk_decision="REJECTED",
            risk_reason=decision.reason,
            source="EXECUTOR",
            raw_payload={
                "signal_id": signal.signal_id,
                "strategy_name": signal.strategy_name,
                "confidence_score": signal.confidence_score,
            },
        )

        # Fire-and-forget Telegram rejection alert
        direction = "BUY" if signal.action == "BUY" else "SELL"
        asyncio.create_task(
            self.notifier.send_rejection_alert(
                signal.symbol, direction, decision.reason,
            )
        )

        return ExecutionResult(
            signal_id=signal.signal_id,
            status="REJECTED",
            rejection_reason=decision.reason,
        )

    # ------------------------------------------------------------------ #
    #  Order placement helpers                                            #
    # ------------------------------------------------------------------ #

    def _place_market_orders(
        self,
        symbol: str,
        exchange: str,
        direction: str,
        quantity: int,
        stoploss_price: float,
        target_price: float,
        algo_id: str,
    ) -> dict[str, str | None]:
        """Place a MARKET entry with SL-M and LIMIT target exit legs.

        Args:
            symbol:          Trading symbol.
            exchange:        Exchange segment.
            direction:       ``"BUY"`` or ``"SELL"`` for the entry leg.
            quantity:        Position size.
            stoploss_price:  Trigger price for the SL-M exit order.
            target_price:    Limit price for the target exit order.
            algo_id:         SEBI-mandated Algo-ID tag.

        Returns:
            Dict with ``entry_order_id``, ``sl_order_id``,
            ``target_order_id``.
        """
        opposite_txn = "SELL" if direction == "BUY" else "BUY"
        result: dict[str, str | None] = {
            "entry_order_id": None,
            "sl_order_id": None,
            "target_order_id": None,
        }

        # Entry — MARKET
        entry_order_id = self.broker.place_order(
            symbol=symbol,
            exchange=exchange,
            transaction_type=direction,
            quantity=quantity,
            price=0,
            trigger_price=None,
            order_type="MARKET",
            product="MIS",
            tag=algo_id,
        )
        result["entry_order_id"] = str(entry_order_id)
        logger.info(
            "MARKET entry placed | symbol=%s dir=%s qty=%d order_id=%s",
            symbol, direction, quantity, entry_order_id,
        )

        # Stoploss — SL-M
        try:
            sl_order_id = self.broker.place_order(
                symbol=symbol,
                exchange=exchange,
                transaction_type=opposite_txn,
                quantity=quantity,
                price=0,
                trigger_price=stoploss_price,
                order_type="SL-M",
                product="MIS",
                tag=algo_id,
            )
            result["sl_order_id"] = str(sl_order_id)
            logger.info(
                "SL-M order placed | symbol=%s trigger=%.2f order_id=%s",
                symbol, stoploss_price, sl_order_id,
            )
        except Exception:
            logger.exception(
                "Failed to place SL-M order | symbol=%s trigger=%.2f",
                symbol, stoploss_price,
            )

        # Target — LIMIT
        try:
            target_order_id = self.broker.place_order(
                symbol=symbol,
                exchange=exchange,
                transaction_type=opposite_txn,
                quantity=quantity,
                price=target_price,
                trigger_price=None,
                order_type="LIMIT",
                product="MIS",
                tag=algo_id,
            )
            result["target_order_id"] = str(target_order_id)
            logger.info(
                "Target LIMIT placed | symbol=%s price=%.2f order_id=%s",
                symbol, target_price, target_order_id,
            )
        except Exception:
            logger.exception(
                "Failed to place TARGET order | symbol=%s price=%.2f",
                symbol, target_price,
            )

        return result

    def _place_limit_orders(
        self,
        symbol: str,
        exchange: str,
        direction: str,
        quantity: int,
        entry_price: float,
        stoploss_price: float,
        target_price: float,
        algo_id: str,
    ) -> dict[str, str | None]:
        """Place a LIMIT entry with SL-M and LIMIT target exit legs.

        Delegates to ``BrokerAdapter.place_entry_with_sl_target()`` which
        handles the 3-leg pattern with LIMIT entry.

        Args:
            symbol:          Trading symbol.
            exchange:        Exchange segment.
            direction:       ``"BUY"`` or ``"SELL"`` for the entry leg.
            quantity:        Position size.
            entry_price:     Limit price for the entry order.
            stoploss_price:  Trigger price for the SL-M exit.
            target_price:    Limit price for the target exit.
            algo_id:         SEBI-mandated Algo-ID tag.

        Returns:
            Dict with ``entry_order_id``, ``sl_order_id``,
            ``target_order_id``.
        """
        return self.broker.place_entry_with_sl_target(
            symbol=symbol,
            exchange=exchange,
            direction=direction,
            quantity=quantity,
            entry_price=entry_price,
            stoploss_price=stoploss_price,
            target_price=target_price,
            product="MIS",
            algo_id=algo_id,
        )

    # ------------------------------------------------------------------ #
    #  Database persistence                                               #
    # ------------------------------------------------------------------ #

    def _persist_trade_and_orders(
        self,
        signal: StrategySignal,
        direction: str,
        exchange: str,
        quantity: int,
        entry_price: float,
        stoploss_price: float,
        target_price: float,
        algo_id: str,
        order_ids: dict[str, str | None],
    ) -> tuple[Trade, list[Order]]:
        """Create Trade and Order records in the database.

        Args:
            signal:          The source ``StrategySignal``.
            direction:       ``"BUY"`` or ``"SELL"``.
            exchange:        Exchange segment.
            quantity:        Executed quantity.
            entry_price:     Entry price used.
            stoploss_price:  Stop-loss price.
            target_price:    Target price.
            algo_id:         SEBI Algo-ID.
            order_ids:       Dict of broker order IDs from placement.

        Returns:
            Tuple of ``(Trade, [Order, Order, Order])``.
        """
        opposite_txn = "SELL" if direction == "BUY" else "BUY"

        trade = Trade(
            user_id=self.user.id,
            strategy_id=self.strategy.id,
            symbol=signal.symbol,
            exchange=exchange,
            direction=direction,
            quantity=quantity,
            entry_price=entry_price,
            stoploss_price=stoploss_price,
            target_price=target_price,
            status="OPEN",
            algo_id=algo_id,
        )
        self.db.add(trade)
        self.db.flush()  # materialise trade.id without committing

        order_records = [
            Order(
                trade_id=trade.id,
                broker_order_id=order_ids.get("entry_order_id"),
                order_type="ENTRY",
                transaction_type=direction,
                product="MIS",
                quantity=quantity,
                price=entry_price,
                trigger_price=None,
                status="PLACED",
                algo_id=algo_id,
                raw_response={"broker_order_id": order_ids.get("entry_order_id")},
            ),
            Order(
                trade_id=trade.id,
                broker_order_id=order_ids.get("sl_order_id"),
                order_type="STOPLOSS",
                transaction_type=opposite_txn,
                product="MIS",
                quantity=quantity,
                price=None,
                trigger_price=stoploss_price,
                status="PLACED" if order_ids.get("sl_order_id") else "PENDING",
                algo_id=algo_id,
                raw_response={"broker_order_id": order_ids.get("sl_order_id")},
            ),
            Order(
                trade_id=trade.id,
                broker_order_id=order_ids.get("target_order_id"),
                order_type="TARGET",
                transaction_type=opposite_txn,
                product="MIS",
                quantity=quantity,
                price=target_price,
                trigger_price=None,
                status="PLACED" if order_ids.get("target_order_id") else "PENDING",
                algo_id=algo_id,
                raw_response={"broker_order_id": order_ids.get("target_order_id")},
            ),
        ]
        self.db.add_all(order_records)
        self.db.commit()
        self.db.refresh(trade)

        logger.info(
            "Trade persisted | trade_id=%s with %d orders",
            trade.id, len(order_records),
        )

        return trade, order_records

    # ------------------------------------------------------------------ #
    #  Audit trail                                                        #
    # ------------------------------------------------------------------ #

    def _write_audit(
        self,
        action: str,
        symbol: str | None = None,
        exchange: str | None = None,
        direction: str | None = None,
        quantity: int | None = None,
        price: float | None = None,
        algo_id: str | None = None,
        risk_decision: str | None = None,
        risk_reason: str | None = None,
        source: str | None = None,
        trade_id: UUID | None = None,
        order_id: UUID | None = None,
        broker_order_id: str | None = None,
        raw_payload: dict | None = None,
    ) -> None:
        """Write an immutable SEBI audit trail record.

        Args:
            action:           Audit action type (e.g. ``ORDER_PLACED``).
            symbol:           Trading symbol.
            exchange:         Exchange segment.
            direction:        ``"BUY"`` or ``"SELL"``.
            quantity:         Order quantity.
            price:            Order price.
            algo_id:          SEBI Algo-ID.
            risk_decision:    ``"APPROVED"`` or ``"REJECTED"``.
            risk_reason:      Rejection reason text.
            source:           Event source (``"EXECUTOR"``).
            trade_id:         Reference to trades table.
            order_id:         Reference to orders table.
            broker_order_id:  Broker-assigned order ID.
            raw_payload:      Full context dict for regulatory export.
        """
        try:
            audit = AuditTrail(
                user_id=self.user.id,
                algo_id=algo_id,
                action=action,
                symbol=symbol,
                exchange=exchange,
                direction=direction,
                quantity=quantity,
                price=price,
                broker_order_id=broker_order_id,
                risk_decision=risk_decision,
                risk_reason=risk_reason,
                raw_payload=raw_payload,
                source=source,
                trade_id=trade_id,
                order_id=order_id,
            )
            self.db.add(audit)
            self.db.commit()
        except Exception:
            logger.exception("Failed to write audit trail record")

    # ------------------------------------------------------------------ #
    #  Logging helper                                                     #
    # ------------------------------------------------------------------ #

    def _log_to_db(
        self,
        level: str,
        source: str,
        message: str,
        context: dict | None = None,
    ) -> None:
        """Persist a structured log entry to the ``logs`` table.

        Args:
            level:   Log severity (``INFO``, ``WARNING``, ``ERROR``).
            source:  Subsystem identifier.
            message: Human-readable log message.
            context: Optional JSON-serialisable context dict.
        """
        try:
            log = Log(
                user_id=self.user.id,
                level=level,
                source=source,
                message=message,
                context=context,
            )
            self.db.add(log)
            self.db.commit()
        except Exception:
            logger.exception("Failed to write log to DB")

    # ------------------------------------------------------------------ #
    #  Result helpers                                                     #
    # ------------------------------------------------------------------ #

    def _error_result(
        self,
        signal: StrategySignal,
        error_detail: str,
    ) -> ExecutionResult:
        """Build an ERROR execution result.

        Also persists the error to the log table.

        Args:
            signal:       The signal that triggered the error.
            error_detail: Human-readable error description.

        Returns:
            An :class:`ExecutionResult` with ``status="ERROR"``.
        """
        self._log_to_db(
            level="ERROR",
            source="executor",
            message=f"Execution error: {error_detail}",
            context={"signal_id": signal.signal_id, "symbol": signal.symbol},
        )
        return ExecutionResult(
            signal_id=signal.signal_id,
            status="ERROR",
            error_detail=error_detail,
        )
