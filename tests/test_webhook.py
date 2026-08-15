"""
Tests for the ``/webhook/tradingview`` POST endpoint in ``app.main``.

Covers:
- Invalid webhook secret → 401
- Unknown strategy/webhook_token → REJECTED
- Risk engine rejects trade → REJECTED response
- Full happy-path: mocked broker + risk engine → EXECUTED with trade_id
- Inactive user → REJECTED

All broker (``_broker``) and notifier (``_notifier``) module-level singletons
are mocked so no real network calls are made.
"""

import uuid
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.schemas import RiskCheckResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _alert_payload(**overrides) -> dict:
    """Return a minimal valid TradingViewAlert JSON body, with overrides."""
    base = {
        "secret": "test-webhook-secret",
        "webhook_token": "test-webhook-token",
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "direction": "BUY",
        "quantity": None,
        "price": 2500.0,
        "signal_id": "sig-001",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTradingViewWebhook:
    """Tests for ``POST /webhook/tradingview``."""

    def test_invalid_secret(self, test_client):
        """A request with the wrong shared secret should return HTTP 401."""
        payload = _alert_payload(secret="wrong-secret")
        response = test_client.post("/webhook/tradingview", json=payload)

        assert response.status_code == 401

    def test_unknown_strategy(self, test_client):
        """A valid secret but unknown webhook_token should return REJECTED."""
        payload = _alert_payload(webhook_token="unknown-token-xyz")
        response = test_client.post("/webhook/tradingview", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "REJECTED"
        assert "unknown" in data["detail"].lower() or "inactive" in data["detail"].lower()

    @patch("app.main._get_notifier")
    @patch("app.main._get_broker")
    def test_risk_rejected(self, mock_broker_fn, mock_notifier_fn, test_client):
        """When the risk engine rejects a trade, the endpoint should return
        status=REJECTED with the rejection reason.
        """
        # Mock broker to return LTP
        mock_broker = MagicMock()
        mock_broker.get_ltp.return_value = 2500.0
        mock_broker_fn.return_value = mock_broker

        # Mock notifier
        mock_notifier = MagicMock()
        mock_notifier.send_rejection_alert = AsyncMock(return_value=True)
        mock_notifier_fn.return_value = mock_notifier

        # Patch RiskEngine.evaluate to return rejected
        with patch("app.main.RiskEngine") as MockRiskEngine:
            mock_engine = MagicMock()
            mock_engine.evaluate.return_value = RiskCheckResult(
                approved=False,
                reason="Daily loss limit breached. Current P&L: ₹-1200.00",
            )
            MockRiskEngine.return_value = mock_engine

            payload = _alert_payload()
            response = test_client.post("/webhook/tradingview", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "REJECTED"
        assert "Daily loss limit breached" in data["detail"]

    @patch("app.main._get_notifier")
    @patch("app.main._get_broker")
    def test_successful_trade(self, mock_broker_fn, mock_notifier_fn, test_client):
        """Happy path: broker places orders successfully, response is EXECUTED
        and includes a ``trade_id``.
        """
        # Mock broker
        mock_broker = MagicMock()
        mock_broker.get_ltp.return_value = 2500.0
        mock_broker.place_entry_with_sl_target.return_value = {
            "entry_order_id": "ORD-001",
            "sl_order_id": "ORD-002",
            "target_order_id": "ORD-003",
        }
        mock_broker_fn.return_value = mock_broker

        # Mock notifier
        mock_notifier = MagicMock()
        mock_notifier.send_trade_notification = AsyncMock(return_value=True)
        mock_notifier_fn.return_value = mock_notifier

        # Patch RiskEngine.evaluate to return approved
        with patch("app.main.RiskEngine") as MockRiskEngine:
            mock_engine = MagicMock()
            mock_engine.evaluate.return_value = RiskCheckResult(
                approved=True,
                quantity=80,
                stoploss_price=2487.50,
                target_price=2525.00,
            )
            mock_engine.get_daily_summary.return_value = {
                "trade_count": 1,
                "total_pnl": 0.0,
                "capital": 100000,
            }
            MockRiskEngine.return_value = mock_engine

            payload = _alert_payload()
            response = test_client.post("/webhook/tradingview", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "EXECUTED"
        assert data["trade_id"] is not None
        assert len(data["trade_id"]) > 0

    def test_inactive_user(self, db_session, test_strategy, mock_settings):
        """When the user is inactive (``is_active=False``), the endpoint
        should return REJECTED.
        """
        from app.models import User
        from app.database import get_db
        from app.main import app

        # Create an inactive user
        inactive_user = User(
            id=uuid.uuid4(),
            full_name="Inactive Trader",
            email="inactive@example.com",
            broker="zerodha",
            broker_client_id="INACTIVE01",
            total_capital=100000,
            risk_per_trade_pct=1.0,
            max_daily_loss=1000,
            max_trades_per_day=3,
            cooldown_minutes=20,
            is_active=False,
        )
        db_session.add(inactive_user)
        db_session.commit()
        db_session.refresh(inactive_user)

        # Update the strategy to point to inactive user
        from app.models import Strategy
        inactive_strategy = Strategy(
            id=uuid.uuid4(),
            user_id=inactive_user.id,
            name="Inactive Strategy",
            algo_id="TEST-ALGO-002",
            webhook_token="inactive-webhook-token",
            default_stoploss_pct=0.5,
            default_target_pct=1.0,
            is_active=True,
        )
        db_session.add(inactive_strategy)
        db_session.commit()

        def _override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = _override_get_db

        with TestClient(app, raise_server_exceptions=False) as client:
            payload = _alert_payload(webhook_token="inactive-webhook-token")
            response = client.post("/webhook/tradingview", json=payload)

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "REJECTED"
        assert "inactive" in data["detail"].lower() or "not found" in data["detail"].lower()
