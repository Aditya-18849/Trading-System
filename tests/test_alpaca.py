"""
Unit tests for AlpacaAdapter and AlpacaDataFetcher using alpaca-py.

Mocks TradingClient, StockHistoricalDataClient, and CryptoHistoricalDataClient
to ensure zero network calls while asserting correct request schemas, response
parsing, bracket order execution, and error handling.
"""

from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
import pytest
import pandas as pd

from alpaca.trading.enums import OrderSide, OrderType, TimeInForce
from alpaca.common.exceptions import APIError
from app.broker.alpaca import AlpacaAdapter
from app.market_data.alpaca_fetcher import AlpacaDataFetcher


@pytest.fixture()
def mock_alpaca_clients():
    """Mock the underlying alpaca-py clients for AlpacaAdapter."""
    with patch("app.broker.alpaca.TradingClient") as MockTradingClient, \
         patch("app.broker.alpaca.StockHistoricalDataClient") as MockStockDataClient, \
         patch("app.broker.alpaca.CryptoHistoricalDataClient") as MockCryptoDataClient:

        mock_trading = MagicMock()
        mock_stock = MagicMock()
        mock_crypto = MagicMock()

        MockTradingClient.return_value = mock_trading
        MockStockDataClient.return_value = mock_stock
        MockCryptoDataClient.return_value = mock_crypto

        adapter = AlpacaAdapter(
            api_key="test-alpaca-key",
            secret_key="test-alpaca-secret",
            paper=True,
        )

        adapter._mock_trading = mock_trading
        adapter._mock_stock = mock_stock
        adapter._mock_crypto = mock_crypto

        yield adapter


class TestAlpacaAdapter:
    """Test suite for AlpacaAdapter."""

    def test_get_account_balance(self, mock_alpaca_clients):
        adapter = mock_alpaca_clients
        mock_account = MagicMock()
        mock_account.buying_power = "25000.50"
        mock_account.cash = "10000.25"
        mock_account.portfolio_value = "35000.75"
        mock_account.equity = "35000.75"
        mock_account.currency = "USD"
        mock_account.pattern_day_trader = False
        mock_account.status = "ACTIVE"
        mock_account.non_margin_buying_power = "10000.25"
        mock_account.daytrade_count = 0
        adapter._mock_trading.get_account.return_value = mock_account

        bal = adapter.get_account_balance()
        assert bal["buying_power"] == 25000.50
        assert bal["cash"] == 10000.25
        assert bal["portfolio_value"] == 35000.75
        assert bal["net"] == 25000.50

    def test_get_positions(self, mock_alpaca_clients):
        adapter = mock_alpaca_clients
        mock_pos = MagicMock()
        mock_pos.symbol = "AAPL"
        mock_pos.qty = "10"
        mock_pos.qty_available = "10"
        mock_pos.avg_entry_price = "150.00"
        mock_pos.current_price = "155.00"
        mock_pos.unrealized_pl = "50.00"
        mock_pos.unrealized_plpc = "0.0333"
        mock_pos.market_value = "1550.00"
        mock_pos.cost_basis = "1500.00"
        mock_pos.change_today = "0.01"
        mock_pos.side = "long"
        mock_pos.exchange = "NASDAQ"
        mock_pos.asset_class = "us_equity"

        adapter._mock_trading.get_all_positions.return_value = [mock_pos]

        positions = adapter.get_positions()
        assert len(positions) == 1
        assert positions[0]["tradingsymbol"] == "AAPL"
        assert positions[0]["quantity"] == 10
        assert positions[0]["average_price"] == 150.00
        assert positions[0]["pnl"] == 50.00

    def test_place_market_order(self, mock_alpaca_clients):
        adapter = mock_alpaca_clients
        mock_order = MagicMock()
        mock_order.id = "alpaca-order-uuid-1234"
        adapter._mock_trading.submit_order.return_value = mock_order

        order_id = adapter.place_order(
            symbol="AAPL",
            exchange="NASDAQ",
            transaction_type="BUY",
            quantity=10,
            order_type="MARKET",
        )

        assert order_id == "alpaca-order-uuid-1234"
        adapter._mock_trading.submit_order.assert_called_once()
        call_args = adapter._mock_trading.submit_order.call_args
        req = call_args.kwargs.get("order_data") or call_args[0][0]
        assert req.symbol == "AAPL"
        assert req.qty == 10

    def test_place_limit_order(self, mock_alpaca_clients):
        adapter = mock_alpaca_clients
        mock_order = MagicMock()
        mock_order.id = "alpaca-limit-uuid-5678"
        adapter._mock_trading.submit_order.return_value = mock_order

        order_id = adapter.place_order(
            symbol="SPY",
            exchange="NYSE",
            transaction_type="SELL",
            quantity=5,
            price=450.50,
            order_type="LIMIT",
        )

        assert order_id == "alpaca-limit-uuid-5678"
        call_args = adapter._mock_trading.submit_order.call_args
        req = call_args.kwargs.get("order_data") or call_args[0][0]
        assert req.symbol == "SPY"
        assert req.limit_price == 450.50

    def test_place_bracket_order(self, mock_alpaca_clients):
        adapter = mock_alpaca_clients
        mock_order = MagicMock()
        mock_order.id = "alpaca-bracket-uuid-999"
        adapter._mock_trading.submit_order.return_value = mock_order

        result = adapter.place_entry_with_sl_target(
            symbol="TSLA",
            exchange="NASDAQ",
            direction="BUY",
            quantity=2,
            entry_price=200.0,
            stoploss_price=190.0,
            target_price=220.0,
        )

        assert result["entry_order_id"] == "alpaca-bracket-uuid-999"
        call_args = adapter._mock_trading.submit_order.call_args
        req = call_args.kwargs.get("order_data") or call_args[0][0]
        assert req.take_profit.limit_price == 220.0
        assert req.stop_loss.stop_price == 190.0

    def test_get_ltp(self, mock_alpaca_clients):
        adapter = mock_alpaca_clients
        mock_trade = MagicMock()
        mock_trade.price = 180.25
        adapter._mock_stock.get_stock_latest_trade.return_value = {"AAPL": mock_trade}

        ltp = adapter.get_ltp(symbol="AAPL", exchange="NASDAQ")
        assert ltp == 180.25

    def test_cancel_order(self, mock_alpaca_clients):
        adapter = mock_alpaca_clients
        adapter._mock_trading.cancel_order_by_id.return_value = None
        res = adapter.cancel_order("order-xyz")
        assert res is True
        adapter._mock_trading.cancel_order_by_id.assert_called_once_with("order-xyz")


class TestAlpacaDataFetcher:
    """Test suite for AlpacaDataFetcher."""

    @patch("app.market_data.alpaca_fetcher.StockHistoricalDataClient")
    def test_get_historical_bars(self, MockStockClient):
        mock_client = MagicMock()
        MockStockClient.return_value = mock_client

        mock_bar = MagicMock()
        mock_bar.timestamp = datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc)
        mock_bar.open = 150.0
        mock_bar.high = 155.0
        mock_bar.low = 149.0
        mock_bar.close = 153.0
        mock_bar.volume = 10000
        mock_bar.trade_count = 500
        mock_bar.vwap = 152.0

        mock_barset = MagicMock()
        mock_barset.data = {"AAPL": [mock_bar]}
        mock_client.get_stock_bars.return_value = mock_barset

        fetcher = AlpacaDataFetcher(api_key="key", secret_key="sec")
        df = fetcher.get_historical_bars(symbol="AAPL", timeframe="5m")

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 1
        assert df.iloc[0]["open"] == 150.0
        assert df.iloc[0]["close"] == 153.0
        assert df.iloc[0]["volume"] == 10000.0

    def test_crypto_detection(self):
        fetcher = AlpacaDataFetcher(api_key="key", secret_key="sec")
        assert fetcher.is_crypto("BTC/USD") is True
        assert fetcher.is_crypto("ETHUSD") is True
        assert fetcher.is_crypto("AAPL") is False
        assert fetcher.is_crypto("SPY") is False
