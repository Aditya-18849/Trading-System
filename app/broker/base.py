"""
Abstract base class for broker adapters.

All broker integrations (Zerodha Kite, Angel One, etc.) must implement
this interface. The concrete `place_entry_with_sl_target` method handles
the common 3-leg order pattern (entry + stoploss + target) used by most
intraday/positional strategies.

Every order placed through any adapter MUST include the `tag` parameter
set to the exchange-assigned Algo-ID for SEBI compliance.
"""

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class BrokerAdapter(ABC):
    """Abstract broker adapter that all concrete broker implementations must extend."""

    # ------------------------------------------------------------------ #
    #  Abstract methods – must be implemented by every concrete adapter   #
    # ------------------------------------------------------------------ #

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        exchange: str,
        transaction_type: str,
        quantity: int,
        price: float,
        trigger_price: float | None,
        order_type: str,
        product: str,
        tag: str,
    ) -> str:
        """Place a single order on the exchange.

        Args:
            symbol: Trading symbol (e.g. ``"RELIANCE"``).
            exchange: Exchange segment (e.g. ``"NSE"``, ``"NFO"``).
            transaction_type: ``"BUY"`` or ``"SELL"``.
            quantity: Number of shares / lots.
            price: Limit price (``0`` for market orders).
            trigger_price: Trigger price for SL / SL-M orders, else ``None``.
            order_type: ``"MARKET"``, ``"LIMIT"``, ``"SL"``, ``"SL-M"``.
            product: ``"MIS"`` (intraday), ``"CNC"`` (delivery), ``"NRML"`` (F&O).
            tag: Exchange-assigned Algo-ID – **mandatory** for SEBI compliance.

        Returns:
            Broker-assigned order ID as a string.
        """

    @abstractmethod
    def get_ltp(self, symbol: str, exchange: str) -> float:
        """Get the last traded price for a given instrument.

        Args:
            symbol: Trading symbol.
            exchange: Exchange segment.

        Returns:
            Last traded price as a float.
        """

    @abstractmethod
    def get_positions(self) -> list[dict]:
        """Retrieve all current positions from the broker.

        Returns:
            List of position dicts as returned by the broker SDK.
        """

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order.

        Args:
            order_id: Broker-assigned order ID.

        Returns:
            ``True`` if cancellation succeeded, ``False`` otherwise.
        """

    @abstractmethod
    def modify_order(
        self,
        order_id: str,
        quantity: int | None = None,
        price: float | None = None,
        trigger_price: float | None = None,
        order_type: str | None = None,
    ) -> bool:
        """Modify a pending order on the exchange.

        Only non-``None`` parameters will be updated.  Typically used to
        trail stop-loss trigger prices on live SL-M orders.

        Args:
            order_id: Broker-assigned order ID to modify.
            quantity: New quantity (optional).
            price: New limit price (optional).
            trigger_price: New trigger price (optional).
            order_type: New order type (optional).

        Returns:
            ``True`` if the modification succeeded, ``False`` otherwise.
        """

    @abstractmethod
    def get_instrument_tokens(
        self, symbols_with_exchange: list[str]
    ) -> dict[str, int]:
        """Batch-resolve ``"EXCHANGE:SYMBOL"`` strings to numeric instrument tokens.

        Used by :class:`~app.services.monitor.PositionMonitor` to subscribe
        WebSocket tickers to the correct numeric tokens required by the
        broker SDK (KiteTicker, SmartWebSocket).

        Args:
            symbols_with_exchange: List of ``"EXCHANGE:SYMBOL"`` strings,
                e.g. ``["NSE:RELIANCE", "NFO:NIFTY23AUGFUT"]``.

        Returns:
            Dict mapping each ``"EXCHANGE:SYMBOL"`` to its numeric
            instrument token.  Symbols that could not be resolved are
            omitted from the result.
        """

    # ------------------------------------------------------------------ #
    #  Concrete helper – 3-leg bracket entry                              #
    # ------------------------------------------------------------------ #

    def place_entry_with_sl_target(
        self,
        symbol: str,
        exchange: str,
        direction: str,
        quantity: int,
        entry_price: float,
        stoploss_price: float,
        target_price: float,
        product: str,
        algo_id: str,
    ) -> dict:
        """Place a 3-leg order set: entry + stoploss + target.

        The stoploss and target legs use the *opposite* transaction type so
        they act as exit orders.

        Args:
            symbol: Trading symbol.
            exchange: Exchange segment.
            direction: ``"BUY"`` or ``"SELL"`` for the entry leg.
            quantity: Order quantity (same for all three legs).
            entry_price: Limit price for the entry order.
            stoploss_price: Trigger price for the SL-M exit order.
            target_price: Limit price for the target exit order.
            product: Product type (``"MIS"``, ``"CNC"``, ``"NRML"``).
            algo_id: SEBI-mandated Algo-ID tag attached to every order.

        Returns:
            Dict with keys ``entry_order_id``, ``sl_order_id``,
            ``target_order_id``.  Any leg that failed will have value
            ``None``.
        """

        opposite_txn = "SELL" if direction == "BUY" else "BUY"

        result: dict[str, str | None] = {
            "entry_order_id": None,
            "sl_order_id": None,
            "target_order_id": None,
        }

        # --- 1. Entry order (LIMIT) ----------------------------------- #
        try:
            entry_order_id = self.place_order(
                symbol=symbol,
                exchange=exchange,
                transaction_type=direction,
                quantity=quantity,
                price=entry_price,
                trigger_price=None,
                order_type="LIMIT",
                product=product,
                tag=algo_id,
            )
            result["entry_order_id"] = entry_order_id
            logger.info(
                "Entry order placed | symbol=%s direction=%s qty=%d price=%.2f order_id=%s",
                symbol, direction, quantity, entry_price, entry_order_id,
            )
        except Exception:
            logger.exception(
                "Failed to place ENTRY order | symbol=%s direction=%s qty=%d price=%.2f",
                symbol, direction, quantity, entry_price,
            )

        # --- 2. Stoploss order (SL-M) --------------------------------- #
        try:
            sl_order_id = self.place_order(
                symbol=symbol,
                exchange=exchange,
                transaction_type=opposite_txn,
                quantity=quantity,
                price=0,
                trigger_price=stoploss_price,
                order_type="SL-M",
                product=product,
                tag=algo_id,
            )
            result["sl_order_id"] = sl_order_id
            logger.info(
                "Stoploss order placed | symbol=%s txn=%s qty=%d trigger=%.2f order_id=%s",
                symbol, opposite_txn, quantity, stoploss_price, sl_order_id,
            )
        except Exception:
            logger.exception(
                "Failed to place STOPLOSS order | symbol=%s txn=%s qty=%d trigger=%.2f",
                symbol, opposite_txn, quantity, stoploss_price,
            )

        # --- 3. Target order (LIMIT) ---------------------------------- #
        try:
            target_order_id = self.place_order(
                symbol=symbol,
                exchange=exchange,
                transaction_type=opposite_txn,
                quantity=quantity,
                price=target_price,
                trigger_price=None,
                order_type="LIMIT",
                product=product,
                tag=algo_id,
            )
            result["target_order_id"] = target_order_id
            logger.info(
                "Target order placed | symbol=%s txn=%s qty=%d price=%.2f order_id=%s",
                symbol, opposite_txn, quantity, target_price, target_order_id,
            )
        except Exception:
            logger.exception(
                "Failed to place TARGET order | symbol=%s txn=%s qty=%d price=%.2f",
                symbol, opposite_txn, quantity, target_price,
            )

        return result
