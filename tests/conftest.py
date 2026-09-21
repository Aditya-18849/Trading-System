"""
Shared pytest fixtures for the SEBI-Compliant Algo Trading System test suite.

Provides:
- In-memory SQLite engine & session (overrides ``get_db``)
- Auto-creation of all tables from ``app.database.Base``
- ``test_user`` fixture with preconfigured risk parameters
- ``test_strategy`` fixture linked to the test user
- ``test_client`` fixture (FastAPI TestClient with DB override)
- Mock ``settings`` fixture patching ``app.config.settings``

UUID Compatibility:
    The production models use ``sqlalchemy.dialects.postgresql.UUID``.  SQLite
    does not support this type natively.  We handle this by registering a
    SQLAlchemy ``before_create`` listener that swaps the PostgreSQL UUID
    column type to ``CHAR(32)`` when the dialect is SQLite, and by assigning
    ``uuid.uuid4()`` explicitly on model creation.
"""

import uuid
import sqlite3
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

sqlite3.register_adapter(uuid.UUID, lambda u: str(u))

import pytest
from sqlalchemy import create_engine, event, String
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# UUID-to-SQLite compatibility hack
# ---------------------------------------------------------------------------
# PostgreSQL UUID columns are not supported in SQLite.  We intercept the DDL
# creation event and replace UUID columns with CHAR(36) equivalents so that
# ``Base.metadata.create_all`` succeeds on the in-memory SQLite engine.

from sqlalchemy.dialects.postgresql import UUID as PG_UUID


def _patch_uuid_for_sqlite(base_metadata, engine):
    """Replace PostgreSQL UUID columns with String(36) for SQLite."""
    if engine.dialect.name != "sqlite":
        return

    for table in base_metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, PG_UUID):
                column.type = String(36)
            # Remove server_default that relies on PostgreSQL-only functions
            if column.server_default is not None:
                column.server_default = None


# IST timezone constant (UTC+05:30)
IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------------------
# Test Settings Mock
# ---------------------------------------------------------------------------

_TEST_SETTINGS = {
    "database_url": "sqlite:///:memory:",
    "supabase_url": None,
    "supabase_service_role_key": None,
    "tradingview_webhook_secret": "test-webhook-secret",
    "admin_api_key": "test-admin-key",
    "allowed_webhook_ips": None,
    "kite_api_key": "test-api-key",
    "kite_api_secret": "test-api-secret",
    "kite_user_id": "TEST01",
    "kite_password": "test-password",
    "kite_totp_secret": "test-totp",
    "kite_algo_id": "TEST-ALGO-001",
    "angel_api_key": None,
    "angel_client_id": None,
    "angel_password": None,
    "angel_totp_secret": None,
    "angel_algo_id": None,
    "total_capital": 100000,
    "risk_per_trade_pct": 1.0,
    "max_daily_loss": 1000,
    "max_trades_per_day": 3,
    "cooldown_minutes_after_loss": 20,
    "default_stoploss_pct": 0.5,
    "default_target_pct": 1.0,
    "trailing_sl_pct": 0.3,
    "trailing_sl_activation_pct": 0.3,
    "monitor_poll_interval_sec": 5,
    "monitor_pnl_check_interval_sec": 10,
    "ws_reconnect_max_retries": 5,
    "ws_reconnect_base_delay_sec": 2.0,
    "heartbeat_interval_sec": 60,
    "heartbeat_telegram_every_n": 10,
    "auto_square_off_time": "15:15",
    "telegram_bot_token": "test-bot-token",
    "telegram_chat_id": "test-chat-id",
    "rate_limit": "30/minute",
    "cors_origins": "*",
    "app_env": "test",
}


class _MockSettings:
    """Lightweight stand-in for ``app.config.Settings`` during tests."""

    def __init__(self, overrides: dict | None = None):
        values = {**_TEST_SETTINGS, **(overrides or {})}
        for key, value in values.items():
            setattr(self, key, value)


# ---------------------------------------------------------------------------
# Core database fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def test_engine():
    """Create a single in-memory SQLite engine for the entire test session."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.database import Base
    import app.models  # ensure models are registered on Base.metadata before create_all
    _patch_uuid_for_sqlite(Base.metadata, engine)
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture()
def db_session(test_engine):
    """Yield a fresh SQLAlchemy session per test."""
    from app.database import Base
    Session = sessionmaker(bind=test_engine)
    session = Session()

    yield session

    session.rollback()
    # Clean up all table rows between tests
    for table in reversed(Base.metadata.sorted_tables):
        test_engine.execute(table.delete()) if hasattr(test_engine, 'execute') else session.execute(table.delete())
    session.commit()
    session.close()


@pytest.fixture(autouse=True)
def mock_db_engine(test_engine, db_session):
    """Ensure engine, SessionLocal, and get_db throughout app use test_engine."""
    import app.database
    import app.main

    TestSession = sessionmaker(bind=test_engine)

    def _get_test_db():
        yield db_session

    with patch.object(app.database, "engine", test_engine), \
         patch.object(app.main, "engine", test_engine), \
         patch.object(app.database, "SessionLocal", TestSession), \
         patch.object(app.database, "get_db", _get_test_db), \
         patch.object(app.main, "get_db", _get_test_db):
        yield


# ---------------------------------------------------------------------------
# Domain-model fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_user(db_session):
    """Create and return a ``User`` with known risk parameters."""
    from app.models import User

    user = User(
        id=uuid.uuid4(),
        full_name="Test Trader",
        email="test@example.com",
        broker="zerodha",
        broker_client_id="TEST01",
        kite_access_token="fake-access-token",
        total_capital=100000,
        risk_per_trade_pct=1.0,
        max_daily_loss=1000,
        max_trades_per_day=3,
        cooldown_minutes=20,
        is_active=True,
        created_at=datetime.now(IST),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def test_strategy(db_session, test_user):
    """Create and return a ``Strategy`` linked to ``test_user``."""
    from app.models import Strategy

    strategy = Strategy(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Test Strategy",
        algo_id="TEST-ALGO-001",
        webhook_token="test-webhook-token",
        default_stoploss_pct=0.5,
        default_target_pct=1.0,
        is_active=True,
        created_at=datetime.now(IST),
    )
    db_session.add(strategy)
    db_session.commit()
    db_session.refresh(strategy)
    return strategy


# ---------------------------------------------------------------------------
# FastAPI TestClient fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_client(db_session, test_user, test_strategy, mock_settings, test_engine):
    """Yield a FastAPI ``TestClient`` with the DB dependency overridden."""
    from fastapi.testclient import TestClient
    from app.database import get_db
    import app.main
    from app.main import app

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    mock_notifier = AsyncMock()

    with patch("app.main.engine", test_engine), \
         patch("app.database.engine", test_engine), \
         patch("app.main.get_db", _override_get_db), \
         patch("app.main._get_notifier", return_value=mock_notifier), \
         patch("app.notifications.telegram.TelegramNotifier.send_message", new_callable=AsyncMock), \
         patch("app.notifications.telegram.TelegramNotifier.send_trade_notification", new_callable=AsyncMock), \
         patch("app.notifications.telegram.TelegramNotifier.send_rejection_alert", new_callable=AsyncMock), \
         patch("app.notifications.telegram.TelegramNotifier.send_error_alert", new_callable=AsyncMock), \
         patch("app.main.start_scheduler", new_callable=AsyncMock), \
         patch("app.main.shutdown_scheduler", new_callable=AsyncMock):
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Mock settings fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_settings():
    """Patch ``app.config.settings`` with deterministic test values.

    This is ``autouse=True`` so every test gets consistent settings
    without needing to request the fixture explicitly.
    """
    mock = _MockSettings()
    with patch("app.config.settings", mock), \
         patch("app.risk_engine.settings", mock), \
         patch("app.main.settings", mock):
        yield mock
