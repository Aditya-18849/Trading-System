"""
Zerodha Kite Connect broker adapter.

Implements :class:`~app.broker.base.BrokerAdapter` using the official
``kiteconnect`` Python SDK.  Every order placed through this adapter
carries the SEBI-mandated Algo-ID tag.
"""

import logging

from kiteconnect import KiteConnect

from app.broker.base import BrokerAdapter

logger = logging.getLogger(__name__)


class KiteAdapter(BrokerAdapter):
    """Concrete broker adapter for Zerodha Kite Connect."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        access_token: str,
        algo_id: str,
    ) -> None:
        """Initialise a KiteConnect session.

        Args:
            api_key: Kite Connect API key.
            api_secret: Kite Connect API secret (stored but not used after
                session generation).
            access_token: Valid access token obtained via the daily login flow.
            algo_id: Exchange-assigned Algo-ID for SEBI compliance tagging.
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.algo_id = algo_id

        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)
        logger.info("KiteAdapter initialised for api_key=%s", api_key)

    # ------------------------------------------------------------------ #
    #  Order placement                                                    #
    # ------------------------------------------------------------------ #

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
        """Place an order via Kite Connect.

        Args:
            symbol: Trading symbol (e.g. ``"RELIANCE"``).
            exchange: Exchange segment (``"NSE"``, ``"NFO"``, …).
            transaction_type: ``"BUY"`` or ``"SELL"``.
            quantity: Number of shares / lots.
            price: Limit price (``0`` for market / SL-M).
            trigger_price: Trigger price for SL / SL-M orders, else ``None``.
            order_type: ``"MARKET"``, ``"LIMIT"``, ``"SL"``, ``"SL-M"``.
            product: ``"MIS"``, ``"CNC"``, or ``"NRML"``.
            tag: Algo-ID tag – **mandatory** for SEBI compliance.

        Returns:
            Broker-assigned order ID as a string.

        Raises:
            Exception: Propagated from the Kite SDK on API / network errors.
        """
        try:
            order_id = self.kite.place_order(
                variety="regular",
                tradingsymbol=symbol,
                exchange=exchange,
                transaction_type=transaction_type,
                quantity=quantity,
                price=price,
                trigger_price=trigger_price,
                order_type=order_type,
                product=product,
                tag=tag,
            )
            logger.info(
                "Order placed | order_id=%s symbol=%s exchange=%s txn=%s qty=%d "
                "price=%.2f trigger=%.2f type=%s product=%s tag=%s",
                order_id,
                symbol,
                exchange,
                transaction_type,
                quantity,
                price,
                trigger_price or 0.0,
                order_type,
                product,
                tag,
            )
            return str(order_id)
        except Exception:
            logger.exception(
                "place_order FAILED | symbol=%s exchange=%s txn=%s qty=%d "
                "price=%.2f trigger=%.2f type=%s product=%s tag=%s",
                symbol,
                exchange,
                transaction_type,
                quantity,
                price,
                trigger_price or 0.0,
                order_type,
                product,
                tag,
            )
            raise

    def modify_order(
        self,
        order_id: str,
        quantity: int | None = None,
        price: float | None = None,
        trigger_price: float | None = None,
        order_type: str | None = None,
    ) -> bool:
        """Modify a pending order via Kite Connect.

        Args:
            order_id: Broker-assigned order ID to modify.
            quantity: New quantity (optional).
            price: New limit price (optional).
            trigger_price: New trigger price (optional).
            order_type: New order type (optional).

        Returns:
            ``True`` if modification succeeded, ``False`` on any error.
        """
        try:
            params: dict = {}
            if quantity is not None:
                params["quantity"] = quantity
            if price is not None:
                params["price"] = price
            if trigger_price is not None:
                params["trigger_price"] = trigger_price
            if order_type is not None:
                params["order_type"] = order_type

            self.kite.modify_order(
                variety="regular",
                order_id=order_id,
                **params,
            )
            logger.info(
                "Order modified | order_id=%s params=%s",
                order_id,
                params,
            )
            return True
        except Exception:
            logger.exception("modify_order FAILED | order_id=%s", order_id)
            return False

    # ------------------------------------------------------------------ #
    #  Market data                                                        #
    # ------------------------------------------------------------------ #

    def get_ltp(self, symbol: str, exchange: str) -> float:
        """Fetch the last traded price for a symbol.

        Args:
            symbol: Trading symbol.
            exchange: Exchange segment.

        Returns:
            Last traded price as a float.

        Raises:
            Exception: On API / network failures.
        """
        instrument_key = f"{exchange}:{symbol}"
        try:
            data = self.kite.ltp(instrument_key)
            ltp: float = data[instrument_key]["last_price"]
            logger.debug("LTP fetched | %s = %.2f", instrument_key, ltp)
            return ltp
        except Exception:
            logger.exception("get_ltp FAILED | instrument=%s", instrument_key)
            raise

    # ------------------------------------------------------------------ #
    #  Positions                                                          #
    # ------------------------------------------------------------------ #

    def get_positions(self) -> list[dict]:
        """Retrieve current net positions.

        Returns:
            List of position dicts from the ``net`` book.

        Raises:
            Exception: On API / network failures.
        """
        try:
            positions = self.kite.positions()
            net_positions: list[dict] = positions["net"]
            logger.info("Fetched %d net positions", len(net_positions))
            return net_positions
        except Exception:
            logger.exception("get_positions FAILED")
            raise

    # ------------------------------------------------------------------ #
    #  Order cancellation                                                 #
    # ------------------------------------------------------------------ #

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order.

        Args:
            order_id: Broker-assigned order ID to cancel.

        Returns:
            ``True`` if cancellation succeeded, ``False`` on any error.
        """
        try:
            self.kite.cancel_order(variety="regular", order_id=order_id)
            logger.info("Order cancelled | order_id=%s", order_id)
            return True
        except Exception:
            logger.exception("cancel_order FAILED | order_id=%s", order_id)
            return False

    # ------------------------------------------------------------------ #
    #  Instrument token resolution                                        #
    # ------------------------------------------------------------------ #

    def get_instrument_tokens(
        self, symbols_with_exchange: list[str]
    ) -> dict[str, int]:
        """Batch-resolve ``"EXCHANGE:SYMBOL"`` to Kite numeric instrument tokens.

        Fetches the instrument list from Kite Connect (cached per session)
        and maps each ``tradingsymbol`` to its numeric ``instrument_token``.

        Args:
            symbols_with_exchange: List of ``"EXCHANGE:SYMBOL"`` strings.

        Returns:
            Dict mapping resolved symbols to their integer instrument tokens.
        """
        # Build instrument cache on first call
        if not hasattr(self, "_instrument_cache"):
            self._instrument_cache: dict[str, dict[str, int]] = {}

        result: dict[str, int] = {}

        # Group by exchange to minimise API calls
        exchange_symbols: dict[str, list[str]] = {}
        for key in symbols_with_exchange:
            parts = key.split(":", 1)
            if len(parts) != 2:
                continue
            exchange, symbol = parts
            exchange_symbols.setdefault(exchange, []).append(symbol)

        for exchange, symbols in exchange_symbols.items():
            # Fetch instrument list for this exchange if not cached
            if exchange not in self._instrument_cache:
                try:
                    instruments = self.kite.instruments(exchange)
                    self._instrument_cache[exchange] = {
                        inst["tradingsymbol"]: inst["instrument_token"]
                        for inst in instruments
                    }
                    logger.info(
                        "Kite instrument cache built | exchange=%s count=%d",
                        exchange,
                        len(self._instrument_cache[exchange]),
                    )
                except Exception:
                    logger.exception(
                        "Failed to fetch Kite instruments for exchange=%s",
                        exchange,
                    )
                    continue

            cache = self._instrument_cache.get(exchange, {})
            for symbol in symbols:
                token = cache.get(symbol)
                if token is not None:
                    result[f"{exchange}:{symbol}"] = token
                else:
                    logger.warning(
                        "Instrument token not found | exchange=%s symbol=%s",
                        exchange,
                        symbol,
                    )

        return result
