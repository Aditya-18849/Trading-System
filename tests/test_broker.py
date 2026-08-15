"""
Tests for the broker adapters: ``KiteAdapter`` (concrete) and
``BrokerAdapter.place_entry_with_sl_target`` (base-class concrete method).

Covers:
- ``place_order`` success and failure
- ``get_ltp`` price retrieval
- ``get_positions`` position list
- ``cancel_order`` success and failure
- ``place_entry_with_sl_target`` 3-leg order orchestration

All tests mock the ``kiteconnect.KiteConnect`` SDK so no real broker
calls are made.
"""

from unittest.mock import patch, MagicMock, call

import pytest

from app.broker.kite import KiteAdapter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def kite_adapter():
    """Create a ``KiteAdapter`` with a mocked ``KiteConnect`` instance."""
    with patch("app.broker.kite.KiteConnect") as MockKite:
        mock_kite_instance = MagicMock()
        MockKite.return_value = mock_kite_instance

        adapter = KiteAdapter(
            api_key="test-api-key",
            api_secret="test-api-secret",
            access_token="test-access-token",
            algo_id="TEST-ALGO-001",
        )

        # Expose the mock for assertions
        adapter._mock_kite = mock_kite_instance
        yield adapter


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestKiteAdapter:
    """Unit tests for the Zerodha Kite Connect adapter."""

    def test_place_order_success(self, kite_adapter):
        """When ``kite.place_order`` returns an order ID, the adapter should
        return it as a string.
        """
        kite_adapter._mock_kite.place_order.return_value = "ORD-12345"

        order_id = kite_adapter.place_order(
            symbol="RELIANCE",
            exchange="NSE",
            transaction_type="BUY",
            quantity=10,
            price=2500.0,
            trigger_price=None,
            order_type="LIMIT",
            product="MIS",
            tag="TEST-ALGO-001",
        )

        assert order_id == "ORD-12345"
        kite_adapter._mock_kite.place_order.assert_called_once_with(
            variety="regular",
            tradingsymbol="RELIANCE",
            exchange="NSE",
            transaction_type="BUY",
            quantity=10,
            price=2500.0,
            trigger_price=None,
            order_type="LIMIT",
            product="MIS",
            tag="TEST-ALGO-001",
        )

    def test_place_order_failure(self, kite_adapter):
        """When ``kite.place_order`` raises an exception, the adapter should
        propagate it.
        """
        kite_adapter._mock_kite.place_order.side_effect = Exception("Insufficient funds")

        with pytest.raises(Exception, match="Insufficient funds"):
            kite_adapter.place_order(
                symbol="RELIANCE",
                exchange="NSE",
                transaction_type="BUY",
                quantity=10,
                price=2500.0,
                trigger_price=None,
                order_type="LIMIT",
                product="MIS",
                tag="TEST-ALGO-001",
            )

    def test_get_ltp(self, kite_adapter):
        """``get_ltp`` should extract the ``last_price`` from the Kite LTP
        response dict and return it as a float.
        """
        kite_adapter._mock_kite.ltp.return_value = {
            "NSE:RELIANCE": {"last_price": 2531.45}
        }

        price = kite_adapter.get_ltp("RELIANCE", "NSE")

        assert price == 2531.45
        kite_adapter._mock_kite.ltp.assert_called_once_with("NSE:RELIANCE")

    def test_get_positions(self, kite_adapter):
        """``get_positions`` should return the ``net`` positions list."""
        expected_positions = [
            {"tradingsymbol": "RELIANCE", "quantity": 10, "pnl": 150.0},
            {"tradingsymbol": "INFY", "quantity": -5, "pnl": -30.0},
        ]
        kite_adapter._mock_kite.positions.return_value = {
            "net": expected_positions,
            "day": [],
        }

        positions = kite_adapter.get_positions()

        assert positions == expected_positions
        assert len(positions) == 2
        kite_adapter._mock_kite.positions.assert_called_once()

    def test_cancel_order_success(self, kite_adapter):
        """When ``kite.cancel_order`` succeeds, the adapter should return True."""
        kite_adapter._mock_kite.cancel_order.return_value = None  # SDK returns None on success

        result = kite_adapter.cancel_order("ORD-12345")

        assert result is True
        kite_adapter._mock_kite.cancel_order.assert_called_once_with(
            variety="regular", order_id="ORD-12345"
        )

    def test_cancel_order_failure(self, kite_adapter):
        """When ``kite.cancel_order`` raises, the adapter should catch the
        exception and return False.
        """
        kite_adapter._mock_kite.cancel_order.side_effect = Exception("Order already executed")

        result = kite_adapter.cancel_order("ORD-12345")

        assert result is False

    def test_place_entry_with_sl_target(self, kite_adapter):
        """``place_entry_with_sl_target`` should place exactly 3 orders:
        1. ENTRY (LIMIT, direction=BUY)
        2. STOPLOSS (SL-M, opposite direction=SELL, trigger_price=SL)
        3. TARGET (LIMIT, opposite direction=SELL, price=target)

        And return a dict with all three order IDs.
        """
        # Each call returns a different order ID
        kite_adapter._mock_kite.place_order.side_effect = [
            "ENTRY-001",
            "SL-002",
            "TGT-003",
        ]

        result = kite_adapter.place_entry_with_sl_target(
            symbol="RELIANCE",
            exchange="NSE",
            direction="BUY",
            quantity=80,
            entry_price=2500.0,
            stoploss_price=2487.50,
            target_price=2525.00,
            product="MIS",
            algo_id="TEST-ALGO-001",
        )

        # Verify result dict
        assert result["entry_order_id"] == "ENTRY-001"
        assert result["sl_order_id"] == "SL-002"
        assert result["target_order_id"] == "TGT-003"

        # Verify exactly 3 place_order calls were made
        assert kite_adapter._mock_kite.place_order.call_count == 3

        # Verify entry order params
        entry_call = kite_adapter._mock_kite.place_order.call_args_list[0]
        assert entry_call == call(
            variety="regular",
            tradingsymbol="RELIANCE",
            exchange="NSE",
            transaction_type="BUY",
            quantity=80,
            price=2500.0,
            trigger_price=None,
            order_type="LIMIT",
            product="MIS",
            tag="TEST-ALGO-001",
        )

        # Verify SL order uses opposite transaction type and SL-M order type
        sl_call = kite_adapter._mock_kite.place_order.call_args_list[1]
        assert sl_call == call(
            variety="regular",
            tradingsymbol="RELIANCE",
            exchange="NSE",
            transaction_type="SELL",   # opposite of BUY
            quantity=80,
            price=0,                   # SL-M has price=0
            trigger_price=2487.50,
            order_type="SL-M",
            product="MIS",
            tag="TEST-ALGO-001",
        )

        # Verify target order uses opposite transaction type and LIMIT
        target_call = kite_adapter._mock_kite.place_order.call_args_list[2]
        assert target_call == call(
            variety="regular",
            tradingsymbol="RELIANCE",
            exchange="NSE",
            transaction_type="SELL",   # opposite of BUY
            quantity=80,
            price=2525.00,
            trigger_price=None,
            order_type="LIMIT",
            product="MIS",
            tag="TEST-ALGO-001",
        )
