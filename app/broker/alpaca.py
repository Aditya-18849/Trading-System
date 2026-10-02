"""
Alpaca Trading API Broker Adapter (US Equities & Crypto).

Implements :class:`~app.broker.base.BrokerAdapter` using the official
`alpaca-py` SDK (`alpaca.trading.client.TradingClient`).

Supports:
- Account balance and buying power inspection.
- Active position fetching and normalization.
- Market, Limit, Stop, and Stop-Limit order execution.
- Native multi-leg Bracket orders (Entry + Stop Loss + Take Profit).
- Graceful API rate-limit (HTTP 429) backoff and PostgreSQL audit trail logging.
- Decoupled from SEBI-mandated Algo-ID tagging for global US markets.
"""

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    LimitOrderRequest,
    StopOrderRequest,
    StopLimitOrderRequest,
    TakeProfitRequest,
    StopLossRequest,
    ReplaceOrderRequest,
    GetOrdersRequest,
)
from alpaca.trading.enums import (
    OrderSide,
    TimeInForce,
    OrderType,
    QueryOrderStatus,
    OrderClass,
)
from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
from alpaca.data.requests import (
    StockLatestQuoteRequest,
    CryptoLatestQuoteRequest,
    StockLatestTradeRequest,
    CryptoLatestTradeRequest,
)
from alpaca.common.exceptions import APIError

from sqlalchemy.orm import Session
from app.broker.base import BrokerAdapter
from app.models import AuditTrail, Log

logger = logging.getLogger(__name__)

# Default timezones
UTC = timezone.utc
NY_TZ = timezone(timedelta(hours=-4))  # Eastern Daylight Time default / America/New_York


class AlpacaAdapter(BrokerAdapter):
    """Concrete broker adapter for Alpaca Trading API (US Equities & Crypto)."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        paper: bool = True,
        db_factory: Optional[Any] = None,
        db: Optional[Session] = None,
    ) -> None:
        """Initialise Alpaca TradingClient and Historical Data Clients.

        Args:
            api_key: Alpaca API key ID.
            secret_key: Alpaca Secret key.
            paper: True for Paper trading simulation, False for Live trading.
            db_factory: Optional callable returning a SQLAlchemy Session for audit logging.
            db: Optional active SQLAlchemy session.
        """
        self.api_key = api_key
        self.secret_key = secret_key
        self.paper = paper
        self.db_factory = db_factory
        self.db = db

        # 1. Initialise Alpaca Trading Client
        self.trading_client = TradingClient(
            api_key=api_key,
            secret_key=secret_key,
            paper=paper,
        )

        # 2. Initialise Data Clients for LTP / Quotes
        self.stock_data_client = StockHistoricalDataClient(
            api_key=api_key,
            secret_key=secret_key,
        )
        self.crypto_data_client = CryptoHistoricalDataClient(
            api_key=api_key,
            secret_key=secret_key,
        )

        logger.info(
            "AlpacaAdapter initialised in %s mode for api_key=%s...",
            "PAPER" if paper else "LIVE",
            api_key[:6] if api_key else "UNKNOWN",
        )

    # ------------------------------------------------------------------ #
    #  Audit Trail & Failure Logging                                      #
    # ------------------------------------------------------------------ #

    def _get_db_session(self) -> Optional[Session]:
        """Obtain a valid database session for audit logging."""
        if self.db is not None:
            return self.db
        if self.db_factory is not None:
            try:
                return self.db_factory()
            except Exception:
                pass
        return None

    def _log_audit(
        self,
        action: str,
        symbol: str,
        exchange: str = "NASDAQ",
        direction: Optional[str] = None,
        quantity: Optional[int] = None,
        price: Optional[float] = None,
        broker_order_id: Optional[str] = None,
        risk_decision: str = "APPROVED",
        risk_reason: Optional[str] = None,
        raw_payload: Optional[dict] = None,
        extra: Optional[dict] = None,
    ) -> None:
        """Record compliance audit record in PostgreSQL / SQLite audit_trail table."""
        session = self._get_db_session()
        if session is None:
            return

        try:
            audit_entry = AuditTrail(
                action=action,
                symbol=symbol,
                exchange=exchange or "NASDAQ",
                direction=direction,
                quantity=quantity,
                price=price,
                broker_order_id=broker_order_id,
                risk_decision=risk_decision,
                risk_reason=risk_reason,
                raw_payload=raw_payload,
                source="ALPACA_BROKER_ADAPTER",
                extra=extra or {},
            )
            session.add(audit_entry)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.warning("Failed to persist audit trail entry: %s", e)

    # ------------------------------------------------------------------ #
    #  Account & Balance Inspection                                       #
    # ------------------------------------------------------------------ #

    def get_account_balance(self) -> Dict[str, Any]:
        """Fetch real-time buying power, cash balance, and equity.

        Returns:
            Dict containing cash, buying_power, portfolio_value, equity,
            currency, pattern_day_trader flag, and account status.
        """
        try:
            account = self.trading_client.get_account()
            buying_power = float(account.buying_power or 0.0)
            cash = float(account.cash or 0.0)
            portfolio_val = float(account.portfolio_value or 0.0)
            equity = float(account.equity or 0.0)

            non_margin_bp = getattr(account, "non_marginable_buying_power", None) or getattr(account, "non_margin_buying_power", None) or 0.0

            balance_info = {
                "buying_power": buying_power,
                "cash": cash,
                "available_cash": cash,
                "portfolio_value": portfolio_val,
                "equity": equity,
                "net": buying_power or portfolio_val,
                "currency": str(getattr(account, "currency", "USD")),
                "pattern_day_trader": bool(getattr(account, "pattern_day_trader", False)),
                "status": str(getattr(account, "status", "ACTIVE")),
                "non_margin_buying_power": float(non_margin_bp),
                "daytrade_count": int(getattr(account, "daytrade_count", 0) or 0),
            }
            logger.debug("Alpaca account balance: %s", balance_info)
            return balance_info
        except APIError as api_err:
            logger.error("Alpaca API error in get_account_balance (status %s): %s", api_err.code, api_err)
            self._log_audit(
                action="ACCOUNT_QUERY_FAILED",
                symbol="ACCOUNT",
                risk_decision="REJECTED",
                risk_reason=str(api_err),
                raw_payload={"error_code": api_err.code, "detail": str(api_err)},
            )
            raise
        except Exception as exc:
            logger.exception("Unexpected error in get_account_balance: %s", exc)
            raise

    # ------------------------------------------------------------------ #
    #  Positions Retrieval                                                #
    # ------------------------------------------------------------------ #

    def get_positions(self) -> List[Dict[str, Any]]:
        """Retrieve all active positions and normalize to platform standard.

        Returns:
            List of standardized position dicts.
        """
        try:
            alpaca_positions = self.trading_client.get_all_positions()
            standardized: List[Dict[str, Any]] = []

            for pos in alpaca_positions:
                standardized.append({
                    "tradingsymbol": str(pos.symbol),
                    "symbol": str(pos.symbol),
                    "exchange": str(pos.exchange or "US"),
                    "quantity": int(float(pos.qty)),
                    "net_quantity": int(float(pos.qty)),
                    "available_quantity": int(float(pos.qty_available or pos.qty)),
                    "side": str(pos.side).upper(),
                    "average_price": float(pos.avg_entry_price or 0.0),
                    "current_price": float(pos.current_price or 0.0),
                    "last_price": float(pos.current_price or 0.0),
                    "pnl": float(pos.unrealized_pl or 0.0),
                    "market_value": float(pos.market_value or 0.0),
                    "cost_basis": float(pos.cost_basis or 0.0),
                    "unrealized_pnl": float(pos.unrealized_pl or 0.0),
                    "unrealized_pnl_pct": float(pos.unrealized_plpc or 0.0) * 100.0,
                    "change_today_pct": float(pos.change_today or 0.0) * 100.0,
                    "asset_class": str(pos.asset_class or "us_equity"),
                })

            return standardized
        except APIError as api_err:
            logger.error("Alpaca API error in get_positions: %s", api_err)
            raise
        except Exception as exc:
            logger.exception("Unexpected error in get_positions: %s", exc)
            raise

    # ------------------------------------------------------------------ #
    #  Order Placement & Translation                                      #
    # ------------------------------------------------------------------ #

    def place_order(
        self,
        symbol: str,
        exchange: str,
        transaction_type: str,
        quantity: int,
        price: float = 0.0,
        trigger_price: Optional[float] = None,
        order_type: str = "MARKET",
        product: str = "CNC",
        tag: Optional[str] = None,
    ) -> str:
        """Translate internal trading signal into Alpaca order request and execute.

        Args:
            symbol: Trading ticker (e.g. ``"AAPL"``, ``"TSLA"``, ``"BTC/USD"``).
            exchange: Exchange segment (e.g. ``"NASDAQ"``, ``"NYSE"``, ``"CRYPTO"``).
            transaction_type: ``"BUY"`` or ``"SELL"``.
            quantity: Number of shares / units.
            price: Limit price (``0`` or ``None`` for market orders).
            trigger_price: Stop/Trigger price for SL/Stop-Limit orders.
            order_type: ``"MARKET"``, ``"LIMIT"``, ``"SL"``, ``"SL-M"``, ``"STOP_LIMIT"``.
            product: Position product type (e.g. ``"CNC"``, ``"MIS"``).
            tag: Optional strategy reference identifier (SEBI algo-tag requirement bypassed).

        Returns:
            Broker-assigned order ID string.
        """
        # Map Side
        side = OrderSide.BUY if transaction_type.upper() == "BUY" else OrderSide.SELL

        # Normalize client order id (clean string max 128 chars)
        clean_tag = tag.strip().replace(" ", "_") if tag else "SIGNAL"
        client_order_id = f"{clean_tag}_{uuid.uuid4().hex[:12]}"

        # Clean symbol formatting for crypto if needed
        clean_symbol = symbol.upper().strip()

        order_req: Any = None
        normalized_order_type = order_type.upper().strip()
        has_trigger = trigger_price is not None and float(trigger_price) > 0.0

        try:
            # 1. Market Order
            if normalized_order_type == "MARKET" or (price <= 0 and not has_trigger):
                order_req = MarketOrderRequest(
                    symbol=clean_symbol,
                    qty=quantity,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=client_order_id,
                )

            # 2. Limit Order
            elif normalized_order_type == "LIMIT" and not has_trigger:
                order_req = LimitOrderRequest(
                    symbol=clean_symbol,
                    qty=quantity,
                    side=side,
                    limit_price=round(float(price), 2),
                    time_in_force=TimeInForce.DAY,
                    client_order_id=client_order_id,
                )

            # 3. Stop Market (SL-M) Order
            elif normalized_order_type in ("SL-M", "STOP") and trigger_price is not None:
                order_req = StopOrderRequest(
                    symbol=clean_symbol,
                    qty=quantity,
                    side=side,
                    stop_price=round(float(trigger_price), 2),
                    time_in_force=TimeInForce.DAY,
                    client_order_id=client_order_id,
                )

            # 4. Stop Limit (SL) Order
            elif normalized_order_type in ("SL", "STOP_LIMIT") and trigger_price is not None:
                order_req = StopLimitOrderRequest(
                    symbol=clean_symbol,
                    qty=quantity,
                    side=side,
                    stop_price=round(float(trigger_price), 2),
                    limit_price=round(float(price if price > 0 else trigger_price), 2),
                    time_in_force=TimeInForce.DAY,
                    client_order_id=client_order_id,
                )
            else:
                # Default fallback to Market
                order_req = MarketOrderRequest(
                    symbol=clean_symbol,
                    qty=quantity,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=client_order_id,
                )

            # Submit order to Alpaca
            order = self.trading_client.submit_order(order_data=order_req)
            order_id = str(order.id)

            logger.info(
                "Alpaca Order Placed | order_id=%s symbol=%s side=%s qty=%d type=%s client_order_id=%s",
                order_id, clean_symbol, side, quantity, normalized_order_type, client_order_id
            )

            # Persist successful placement to audit trail
            self._log_audit(
                action="ORDER_PLACED",
                symbol=clean_symbol,
                exchange=exchange or "US",
                direction=transaction_type.upper(),
                quantity=quantity,
                price=price if price > 0 else trigger_price,
                broker_order_id=order_id,
                risk_decision="APPROVED",
                raw_payload={
                    "client_order_id": client_order_id,
                    "order_type": normalized_order_type,
                    "status": str(order.status),
                },
            )

            return order_id

        except APIError as api_err:
            error_code = getattr(api_err, "code", "UNKNOWN")
            error_msg = str(api_err)
            logger.error("Alpaca API Error during order placement (code %s): %s", error_code, error_msg)

            # Specific rate limit handling (HTTP 429)
            if str(error_code) == "429" or "rate limit" in error_msg.lower():
                logger.critical("Alpaca API RATE LIMIT (429) encountered for symbol %s!", clean_symbol)

            # Record failure in PostgreSQL audit trail table
            self._log_audit(
                action="ORDER_REJECTED",
                symbol=clean_symbol,
                exchange=exchange or "US",
                direction=transaction_type.upper(),
                quantity=quantity,
                price=price,
                risk_decision="REJECTED",
                risk_reason=f"Alpaca API Error [{error_code}]: {error_msg}",
                raw_payload={"error_code": error_code, "detail": error_msg},
            )
            raise RuntimeError(f"Alpaca order execution failed: {error_msg}") from api_err

        except Exception as exc:
            logger.exception("Unexpected execution error in place_order: %s", exc)
            self._log_audit(
                action="ORDER_REJECTED",
                symbol=clean_symbol,
                exchange=exchange or "US",
                direction=transaction_type.upper(),
                quantity=quantity,
                price=price,
                risk_decision="REJECTED",
                risk_reason=f"Unexpected error: {str(exc)}",
            )
            raise

    # ------------------------------------------------------------------ #
    #  Bracket Order Support (Entry + SL + TP)                            #
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
        product: str = "CNC",
        algo_id: str = "ALPACA_BRACKET",
    ) -> Dict[str, Optional[str]]:
        """Place an advanced 3-leg Bracket Order natively supported by Alpaca.

        Leverages Alpaca's native bracket order routing where Take Profit and
        Stop Loss legs are held on broker server side and automatically cancel
        the other when one executes (OCO).

        Returns:
            Dict with keys ``entry_order_id``, ``sl_order_id``, ``target_order_id``.
        """
        side = OrderSide.BUY if direction.upper() == "BUY" else OrderSide.SELL
        clean_symbol = symbol.upper().strip()
        client_order_id = f"BRK_{uuid.uuid4().hex[:12]}"

        result: Dict[str, Optional[str]] = {
            "entry_order_id": None,
            "sl_order_id": None,
            "target_order_id": None,
        }

        try:
            # Native Alpaca Bracket Order Request
            bracket_req = LimitOrderRequest(
                symbol=clean_symbol,
                qty=quantity,
                side=side,
                limit_price=round(float(entry_price), 2),
                time_in_force=TimeInForce.DAY,
                order_class=OrderClass.BRACKET,
                take_profit=TakeProfitRequest(limit_price=round(float(target_price), 2)),
                stop_loss=StopLossRequest(stop_price=round(float(stoploss_price), 2)),
                client_order_id=client_order_id,
            )

            parent_order = self.trading_client.submit_order(order_data=bracket_req)
            order_id = str(parent_order.id)
            result["entry_order_id"] = order_id
            result["sl_order_id"] = f"{order_id}_sl"
            result["target_order_id"] = f"{order_id}_tp"

            logger.info(
                "Alpaca Native Bracket Placed | parent_id=%s symbol=%s side=%s qty=%d entry=%.2f sl=%.2f tp=%.2f",
                order_id, clean_symbol, side, quantity, entry_price, stoploss_price, target_price
            )

            self._log_audit(
                action="BRACKET_ORDER_PLACED",
                symbol=clean_symbol,
                exchange=exchange or "US",
                direction=direction.upper(),
                quantity=quantity,
                price=entry_price,
                broker_order_id=order_id,
                risk_decision="APPROVED",
                raw_payload={
                    "entry_price": entry_price,
                    "stoploss_price": stoploss_price,
                    "target_price": target_price,
                    "order_class": "bracket",
                },
            )

            return result

        except Exception as exc:
            logger.warning(
                "Native bracket placement failed (%s), falling back to sequential 3-leg placement",
                exc
            )
            # Fallback to base class sequential leg placement
            return super().place_entry_with_sl_target(
                symbol=symbol,
                exchange=exchange,
                direction=direction,
                quantity=quantity,
                entry_price=entry_price,
                stoploss_price=stoploss_price,
                target_price=target_price,
                product=product,
                algo_id=algo_id,
            )

    # ------------------------------------------------------------------ #
    #  Last Traded Price (LTP) & Quotes                                   #
    # ------------------------------------------------------------------ #

    def get_ltp(self, symbol: str, exchange: str = "US") -> float:
        """Fetch latest market price for a given stock or crypto ticker."""
        clean_symbol = symbol.upper().strip()

        # Crypto Quote
        if "/" in clean_symbol or "BTC" in clean_symbol or "ETH" in clean_symbol:
            try:
                req = CryptoLatestQuoteRequest(symbol_or_symbols=clean_symbol)
                quotes = self.crypto_data_client.get_crypto_latest_quote(req)
                if clean_symbol in quotes:
                    return float(quotes[clean_symbol].ask_price or quotes[clean_symbol].bid_price or 0.0)
            except Exception as e:
                logger.debug("Crypto quote fallback: %s", e)

        # Equity Trade / Quote
        try:
            req_trade = StockLatestTradeRequest(symbol_or_symbols=clean_symbol)
            trades = self.stock_data_client.get_stock_latest_trade(req_trade)
            if clean_symbol in trades and trades[clean_symbol].price:
                return float(trades[clean_symbol].price)
        except Exception as e:
            logger.debug("Stock trade lookup fallback: %s", e)

        try:
            req = StockLatestQuoteRequest(symbol_or_symbols=clean_symbol)
            quotes = self.stock_data_client.get_stock_latest_quote(req)
            if clean_symbol in quotes:
                quote = quotes[clean_symbol]
                # Return midpoint or ask price
                if quote.ask_price and quote.bid_price:
                    return round((float(quote.ask_price) + float(quote.bid_price)) / 2.0, 2)
                return float(quote.ask_price or quote.bid_price or 0.0)
        except Exception as e:
            logger.error("Failed to fetch LTP for %s via Alpaca data client: %s", clean_symbol, e)

        # Fallback to position current_price if held
        try:
            pos = self.trading_client.get_open_position(clean_symbol)
            return float(pos.current_price or 0.0)
        except Exception:
            pass

        return 0.0

    # ------------------------------------------------------------------ #
    #  Order Modifications & Cancellations                                #
    # ------------------------------------------------------------------ #

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open Alpaca order by ID."""
        try:
            self.trading_client.cancel_order_by_id(order_id)
            logger.info("Alpaca order cancelled: %s", order_id)
            self._log_audit(
                action="ORDER_CANCELLED",
                symbol="",
                broker_order_id=order_id,
                risk_decision="APPROVED",
            )
            return True
        except APIError as api_err:
            logger.error("Alpaca API error cancelling order %s: %s", order_id, api_err)
            return False
        except Exception as exc:
            logger.exception("Error cancelling order %s: %s", order_id, exc)
            return False

    def modify_order(
        self,
        order_id: str,
        quantity: Optional[int] = None,
        price: Optional[float] = None,
        trigger_price: Optional[float] = None,
        order_type: Optional[str] = None,
    ) -> bool:
        """Modify / Replace an open order (useful for trailing stop-loss updates)."""
        try:
            replace_req = ReplaceOrderRequest(
                qty=quantity,
                limit_price=round(float(price), 2) if price else None,
                stop_price=round(float(trigger_price), 2) if trigger_price else None,
            )
            self.trading_client.replace_order_by_id(order_id, replace_req)
            logger.info("Alpaca order %s replaced with new parameters", order_id)
            self._log_audit(
                action="ORDER_MODIFIED",
                symbol="",
                price=price or trigger_price,
                quantity=quantity,
                broker_order_id=order_id,
            )
            return True
        except Exception as exc:
            logger.error("Failed to modify Alpaca order %s: %s", order_id, exc)
            return False

    def get_instrument_tokens(self, symbols_with_exchange: List[str]) -> Dict[str, int]:
        """Symbol mapping for WebSocket subscription telemetry."""
        # For Alpaca, symbols are strings directly (e.g. AAPL, TSLA). We provide deterministic integer hashes.
        mapping: Dict[str, int] = {}
        for item in symbols_with_exchange:
            sym = item.split(":")[-1] if ":" in item else item
            mapping[item] = abs(hash(sym)) % (10 ** 8)
        return mapping
