"""
Angel One SmartAPI broker adapter.

Implements :class:`~app.broker.base.BrokerAdapter` using the official
``smartapi-python`` SDK (``SmartApi.SmartConnect``).  Every order placed
through this adapter carries the SEBI-mandated Algo-ID tag.

Angel One's API differs from Kite Connect in several ways:

* Order types use different naming (``STOPLOSS_LIMIT`` instead of ``SL``,
  ``STOPLOSS_MARKET`` instead of ``SL-M``).
* Product types are labelled differently (``INTRADAY`` / ``DELIVERY`` /
  ``CARRYFORWARD`` instead of ``MIS`` / ``CNC`` / ``NRML``).
* Every order requires a ``symboltoken`` in addition to the trading symbol.
  This adapter includes a ``_get_symbol_token`` helper that attempts to
  resolve the token via the API, falling back to the raw symbol string.
* Numeric fields (price, trigger price, quantity) must be passed as strings.
"""

import logging

from SmartApi import SmartConnect

from app.broker.base import BrokerAdapter

logger = logging.getLogger(__name__)

# ====================================================================== #
#  Mapping tables – canonical names → Angel One API names                 #
# ====================================================================== #

_ORDER_TYPE_MAP: dict[str, str] = {
    "MARKET": "MARKET",
    "LIMIT": "LIMIT",
    "SL": "STOPLOSS_LIMIT",
    "SL-M": "STOPLOSS_MARKET",
}

_PRODUCT_MAP: dict[str, str] = {
    "MIS": "INTRADAY",
    "CNC": "DELIVERY",
    "NRML": "CARRYFORWARD",
}


class AngelAdapter(BrokerAdapter):
    """Concrete broker adapter for Angel One SmartAPI."""

    def __init__(
        self,
        api_key: str,
        client_id: str,
        password: str,
        totp_secret: str,
        access_token: str,
        algo_id: str,
    ) -> None:
        """Initialise an Angel One SmartAPI session.

        Args:
            api_key: SmartAPI application key.
            client_id: Angel One client / login ID.
            password: Trading account password (stored for potential
                re-authentication but not used after initial session setup).
            totp_secret: TOTP seed used for two-factor authentication
                (stored for potential re-authentication).
            access_token: Valid JWT access token obtained via the daily
                login / token-refresh flow.
            algo_id: Exchange-assigned Algo-ID for SEBI compliance tagging.
        """
        self.api_key = api_key
        self.client_id = client_id
        self.password = password
        self.totp_secret = totp_secret
        self.algo_id = algo_id

        # Initialise the SDK and attach the pre-generated session token.
        self.smart = SmartConnect(api_key=api_key)
        self.smart.setAccessToken(access_token)

        logger.info(
            "AngelAdapter initialised for client_id=%s api_key=%s",
            client_id,
            api_key,
        )

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _get_symbol_token(self, symbol: str, exchange: str) -> str:
        """Resolve the Angel One *symboltoken* for a given trading symbol.

        The SmartAPI requires a numeric ``symboltoken`` alongside the human-
        readable ``tradingsymbol``.  This helper first attempts to fetch it
        via the search endpoint; on failure it falls back to using the raw
        ``symbol`` string so that order placement can still be attempted.

        Args:
            symbol: Trading symbol (e.g. ``"RELIANCE-EQ"``).
            exchange: Exchange segment (e.g. ``"NSE"``).

        Returns:
            Symbol token string (numeric ID, or the raw *symbol* as
            fallback).
        """
        try:
            search_result = self.smart.searchScrip(exchange, symbol)
            if search_result and search_result.get("data"):
                token: str = search_result["data"][0]["symboltoken"]
                logger.debug(
                    "Symbol token resolved | symbol=%s exchange=%s token=%s",
                    symbol,
                    exchange,
                    token,
                )
                return token
        except Exception:
            logger.warning(
                "Symbol token lookup failed, using symbol as fallback | "
                "symbol=%s exchange=%s",
                symbol,
                exchange,
            )

        # Fallback – use the raw symbol string.
        return symbol

    # ------------------------------------------------------------------ #
    #  Order placement                                                     #
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
        """Place an order via Angel One SmartAPI.

        Args:
            symbol: Trading symbol (e.g. ``"RELIANCE-EQ"``).
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
            Exception: Propagated from the SmartAPI SDK on API / network
                errors.
        """
        mapped_order_type = _ORDER_TYPE_MAP.get(order_type, order_type)
        mapped_product = _PRODUCT_MAP.get(product, product)
        symbol_token = self._get_symbol_token(symbol, exchange)

        orderparams: dict = {
            "variety": "NORMAL",
            "tradingsymbol": symbol,
            "symboltoken": symbol_token,
            "transactiontype": transaction_type,
            "exchange": exchange,
            "ordertype": mapped_order_type,
            "producttype": mapped_product,
            "duration": "DAY",
            "price": str(price),
            "triggerprice": str(trigger_price) if trigger_price else "0",
            "quantity": str(quantity),
            "tag": tag,
        }

        try:
            order_id = self.smart.placeOrder(orderparams)
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
                mapped_order_type,
                mapped_product,
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
                mapped_order_type,
                mapped_product,
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
        """Modify a pending order via Angel One SmartAPI.

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
            orderparams: dict = {
                "variety": "NORMAL",
                "orderid": order_id,
            }
            if quantity is not None:
                orderparams["quantity"] = str(quantity)
            if price is not None:
                orderparams["price"] = str(price)
            if trigger_price is not None:
                orderparams["triggerprice"] = str(trigger_price)
            if order_type is not None:
                mapped = _ORDER_TYPE_MAP.get(order_type, order_type)
                orderparams["ordertype"] = mapped

            self.smart.modifyOrder(orderparams)
            logger.info(
                "Order modified | order_id=%s params=%s",
                order_id,
                {k: v for k, v in orderparams.items() if k != "variety"},
            )
            return True
        except Exception:
            logger.exception("modify_order FAILED | order_id=%s", order_id)
            return False

    # ------------------------------------------------------------------ #
    #  Market data                                                         #
    # ------------------------------------------------------------------ #

    def get_ltp(self, symbol: str, exchange: str) -> float:
        """Fetch the last traded price for a symbol via SmartAPI.

        Args:
            symbol: Trading symbol.
            exchange: Exchange segment.

        Returns:
            Last traded price as a float.

        Raises:
            Exception: On API / network failures.
        """
        symbol_token = self._get_symbol_token(symbol, exchange)
        try:
            data = self.smart.ltpData(exchange, symbol, symbol_token)
            ltp: float = float(data["data"]["ltp"])
            logger.debug(
                "LTP fetched | symbol=%s exchange=%s ltp=%.2f",
                symbol,
                exchange,
                ltp,
            )
            return ltp
        except Exception:
            logger.exception(
                "get_ltp FAILED | symbol=%s exchange=%s", symbol, exchange
            )
            raise

    # ------------------------------------------------------------------ #
    #  Positions                                                           #
    # ------------------------------------------------------------------ #

    def get_positions(self) -> list[dict]:
        """Retrieve current positions from Angel One.

        Returns:
            List of position dicts as returned by the SmartAPI SDK.

        Raises:
            Exception: On API / network failures.
        """
        try:
            response = self.smart.position()
            positions: list[dict] = response.get("data", []) or []
            logger.info("Fetched %d positions", len(positions))
            return positions
        except Exception:
            logger.exception("get_positions FAILED")
            raise

    # ------------------------------------------------------------------ #
    #  Order cancellation                                                  #
    # ------------------------------------------------------------------ #

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order on Angel One.

        Args:
            order_id: Broker-assigned order ID to cancel.

        Returns:
            ``True`` if cancellation succeeded, ``False`` on any error.
        """
        try:
            self.smart.cancelOrder(order_id, variety="NORMAL")
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
        """Batch-resolve ``"EXCHANGE:SYMBOL"`` to Angel One symbol tokens.

        Uses :meth:`_get_symbol_token` for each symbol.  Returns only
        symbols that resolved to a numeric token successfully.

        Args:
            symbols_with_exchange: List of ``"EXCHANGE:SYMBOL"`` strings.

        Returns:
            Dict mapping resolved symbols to their integer instrument tokens.
        """
        result: dict[str, int] = {}

        for key in symbols_with_exchange:
            parts = key.split(":", 1)
            if len(parts) != 2:
                continue
            exchange, symbol = parts

            try:
                token_str = self._get_symbol_token(symbol, exchange)
                # _get_symbol_token may return the raw symbol as fallback;
                # only include if it resolved to a numeric value.
                if token_str.isdigit():
                    result[key] = int(token_str)
                else:
                    logger.warning(
                        "Angel token not numeric | exchange=%s symbol=%s token=%s",
                        exchange,
                        symbol,
                        token_str,
                    )
            except Exception:
                logger.warning(
                    "Angel token resolution failed | exchange=%s symbol=%s",
                    exchange,
                    symbol,
                    exc_info=True,
                )

        return result
