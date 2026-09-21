"""
License Guard Middleware — Subscription Kill-Switch
====================================================
Guards against unpaid or expired client installations.
Supports both:
1. Local Manual Kill-Switch (via .license_lock file or SYSTEM_LOCKED=true)
2. Remote Master Server Kill-Switch (via LICENSE_VERIFY_URL + LICENSE_KEY)

DATA PRESERVATION GUARANTEE:
When the kill-switch is triggered, NO client data, trades, or databases are modified
or erased. Inbound API and trading requests are simply suspended with HTTP 402
(Payment Required) until the switch is deactivated.
"""

import os
import logging
from pathlib import Path
import httpx
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────
LOCK_FILE = Path(".license_lock")
MASTER_AUTH_URL = os.getenv(
    "LICENSE_VERIFY_URL",
    "",
)
LICENSE_KEY = os.getenv("LICENSE_KEY", "")
LICENSE_CHECK_TIMEOUT = float(os.getenv("LICENSE_CHECK_TIMEOUT", "2.0"))

# Statuses that should block the request
_BLOCKED_STATUSES = frozenset({"unpaid", "expired", "locked", "suspended", "inactive"})

# Paths that bypass the license check (health probes, docs)
_EXEMPT_PATHS = frozenset({"/health", "/healthz", "/ping", "/metrics", "/docs", "/redoc", "/openapi.json"})


class LicenseGuardMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware enforcing local & remote payment subscription guards."""

    async def dispatch(self, request: Request, call_next):
        # ── Skip check for exempt paths ─────────────────────────────────
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        # ── 1. Check Local Manual Kill-Switch ────────────────────────────
        # Can be triggered via `python scripts/manage_license.py lock` or SYSTEM_LOCKED=true
        system_locked_env = os.getenv("SYSTEM_LOCKED", "false").lower() in ("true", "1", "yes")
        if LOCK_FILE.exists() or system_locked_env:
            logger.warning("Workstation access SUSPENDED via manual license kill-switch.")
            return JSONResponse(
                status_code=402,
                content={
                    "status": "SUSPENDED",
                    "code": "PAYMENT_REQUIRED",
                    "detail": (
                        "Workstation access has been suspended due to an outstanding subscription payment. "
                        "All client data, trade history, and settings remain securely preserved. "
                        "Please contact your vendor/administrator to reactivate your license."
                    ),
                },
            )

        # ── 2. Check Remote Master Auth Server (if configured) ───────────
        if MASTER_AUTH_URL and LICENSE_KEY:
            try:
                async with httpx.AsyncClient(timeout=LICENSE_CHECK_TIMEOUT) as client:
                    response = await client.get(
                        MASTER_AUTH_URL,
                        params={"key": LICENSE_KEY},
                    )

                if response.status_code == 200:
                    try:
                        payload = response.json()
                        status = str(payload.get("status", "")).lower().strip()
                        if status in _BLOCKED_STATUSES:
                            logger.warning("License check BLOCKED by master server: status=%s", status)
                            return JSONResponse(
                                status_code=402,
                                content={
                                    "status": "SUSPENDED",
                                    "code": "PAYMENT_REQUIRED",
                                    "detail": (
                                        f"Subscription is currently {status}. "
                                        "Please renew your subscription to reactivate workstation access."
                                    ),
                                },
                            )
                    except (ValueError, TypeError):
                        logger.warning("License server returned non-JSON body — failing open")

            except Exception as exc:
                # Fail open on network blips so legitimate clients are not blocked by internet glitches
                logger.debug("Remote license verification check: %s — failing open", exc)

        return await call_next(request)
