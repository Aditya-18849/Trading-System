"""
Tests for health check, ping, and keep-alive endpoints in ``app.main``.

Ensures:
- ``/health`` returns 200 with uptime, environment, version, and healthy status
- ``/health`` supports HEAD requests (used by load balancers and uptime checkers)
- ``/health?check_db=true`` verifies database connectivity
- ``/ping`` returns 200 with lightweight pong response (for Render cron keep-alive)
- ``/ping`` supports HEAD requests
- ``/healthz`` alias returns 200
- Endpoints remain accessible and exempt from license guard suspension
"""

from fastapi.testclient import TestClient


def test_health_check_endpoint(test_client: TestClient):
    """Verify /health returns 200 and expected payload structure."""
    response = test_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "trading-system-backend"
    assert data["version"] == "2.0.0"
    assert "uptime_seconds" in data
    assert isinstance(data["uptime_seconds"], (int, float))
    assert "timestamp" in data
    assert "environment" in data


def test_health_check_head_request(test_client: TestClient):
    """Verify /health accepts HEAD requests for lightweight uptime monitors."""
    response = test_client.head("/health")
    assert response.status_code == 200
    assert response.text == ""


def test_health_check_with_db(test_client: TestClient):
    """Verify /health?check_db=true successfully queries DB and returns connected."""
    response = test_client.get("/health?check_db=true")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"


def test_ping_endpoint(test_client: TestClient):
    """Verify /ping returns 200 with pong payload for cron keep-alive."""
    response = test_client.get("/ping")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["message"] == "pong"
    assert "uptime_seconds" in data
    assert "timestamp" in data


def test_ping_head_request(test_client: TestClient):
    """Verify /ping accepts HEAD requests."""
    response = test_client.head("/ping")
    assert response.status_code == 200
    assert response.text == ""


def test_healthz_alias(test_client: TestClient):
    """Verify /healthz alias works as expected."""
    response = test_client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


def test_root_includes_health_and_ping(test_client: TestClient):
    """Verify root / endpoint advertises /health and /ping."""
    response = test_client.get("/")
    assert response.status_code == 200
    data = response.json()
    endpoints = data.get("endpoints", {})
    assert endpoints.get("health") == "/health"
    assert endpoints.get("ping") == "/ping"


def test_license_guard_exempts_health_and_ping(test_client: TestClient, monkeypatch):
    """Verify /health and /ping bypass license kill-switch even when system is locked."""
    monkeypatch.setenv("SYSTEM_LOCKED", "true")
    # Verify that protected route returns 402 Payment Required
    resp_protected = test_client.get("/api/portfolio")
    assert resp_protected.status_code == 402

    # Verify that health and ping endpoints bypass and return 200
    resp_health = test_client.get("/health")
    assert resp_health.status_code == 200

    resp_ping = test_client.get("/ping")
    assert resp_ping.status_code == 200
