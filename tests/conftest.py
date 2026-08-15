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
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from sqlalchemy import create_engine, event, String
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
                sd_text = str(column.server_default.arg) if hasattr(column.server_default, "arg") else ""
                if "gen_random_uuid" in sd_text or "uuid_generate" in sd_text:
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
    "kite_api_key": "test-api-key",
    "kite_api_secret": "test-api-secret",
    "kite_user_id": "TEST01",
    "kite_password": "test-password",
    "kite_totp_secret": "test-totp",
    "kite_algo_id": "TEST-ALGO-001",
    "total_capital": 100000,
    "risk_per_trade_pct": 1.0,
    "max_daily_loss": 1000,
    "max_trades_per_day": 3,
    "cooldown_minutes_after_loss": 20,
    "default_stoploss_pct": 0.5,
    "default_target_pct": 1.0,
    "telegram_bot_token": "test-bot-token",
    "telegram_chat_id": "test-chat-id",
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
    engine = create_engine("sqlite:///:memory:", echo=False)
    return engine


@pytest.fixture(scope="session", autouse=True)
def _create_tables(test_engine):
    """Create all ORM tables once for the test session.

    The UUID-to-String patching is applied before table creation so that
    SQLite can handle the PostgreSQL UUID column type.
    """
    from app.database import Base  # noqa: E402 – import after engine ready

    _patch_uuid_for_sqlite(Base.metadata, test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture()
def db_session(test_engine):
    """Yield a fresh, isolated SQLAlchemy session per test.

    A transaction is begun before the test and rolled back afterwards so that
    each test starts with a clean slate.
    """
    connection = test_engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


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
def test_client(db_session, test_user, test_strategy, mock_settings):
    """Yield a FastAPI ``TestClient`` with the DB dependency overridden."""
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.main import app

    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

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
