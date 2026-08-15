"""
License Guard Middleware — Subscription Kill-Switch
====================================================
Validates the client's LICENSE_KEY against a remote master auth server
before allowing any API request through. This ensures unpaid or expired
subscriptions are blocked at the middleware layer, before any route logic
executes.

**Fail-open policy**: If the master server is unreachable, times out, or
returns a non-JSON / unexpected response, the request is allowed through
so that legitimate trades are never blocked by an auth-server blip.
"""

import os
import logging

import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────
MASTER_AUTH_URL = os.getenv(
    "LICENSE_VERIFY_URL",
    "https://my-master-auth-server.com/api/verify",
)
LICENSE_KEY = os.getenv("LICENSE_KEY", "")
LICENSE_CHECK_TIMEOUT = float(os.getenv("LICENSE_CHECK_TIMEOUT", "2.0"))

# Statuses that should block the request
_BLOCKED_STATUSES = frozenset({"unpaid", "expired"})

# Paths that bypass the license check (health probes, metrics, docs)
_EXEMPT_PATHS = frozenset({"/health", "/metrics", "/docs", "/redoc", "/openapi.json"})


class LicenseGuardMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware that verifies the deployment's license key against
    a remote master server on every inbound request.

    * **402 Payment Required** — if the master server confirms the license
      is ``unpaid`` or ``expired``.
    * **Fail-open** — on network errors, timeouts, or unexpected responses
      the request is allowed through to avoid blocking live trades.
    """

    async def dispatch(self, request: Request, call_next):
        # ── Skip check for exempt paths ─────────────────────────────────
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        # ── Skip if no license key is configured (dev / local mode) ─────
        if not LICENSE_KEY:
            logger.debug("LICENSE_KEY not set — skipping license verification")
            return await call_next(request)

        # ── Call master auth server ─────────────────────────────────────
        try:
            async with httpx.AsyncClient(timeout=LICENSE_CHECK_TIMEOUT) as client:
                response = await client.get(
                    MASTER_AUTH_URL,
                    params={"key": LICENSE_KEY},
                )

            # Only act on a successful HTTP response with valid JSON
            if response.status_code == 200:
                try:
                    payload = response.json()
                except (ValueError, TypeError):
                    # Non-JSON body — fail open
                    logger.warning(
                        "License server returned non-JSON body (status %d) — failing open",
                        response.status_code,
                    )
                    return await call_next(request)

                status = str(payload.get("status", "")).lower().strip()

                if status in _BLOCKED_STATUSES:
                    logger.warning(
                        "License check BLOCKED: key=%s…%s status=%s",
                        LICENSE_KEY[:4],
                        LICENSE_KEY[-4:] if len(LICENSE_KEY) > 8 else "****",
                        status,
                    )
                    return JSONResponse(
                        status_code=402,
                        content={
                            "status": "BLOCKED",
                            "detail": (
                                f"Subscription {status}. "
                                "Please renew your subscription to continue using this service."
                            ),
                        },
                    )
            else:
                # Non-200 response from auth server — fail open
                logger.warning(
                    "License server returned HTTP %d — failing open",
                    response.status_code,
                )

        except httpx.TimeoutException:
            logger.warning(
                "License verification timed out (%.1fs) — failing open",
                LICENSE_CHECK_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "License verification HTTP error: %s — failing open", exc,
            )
        except Exception as exc:
            # Catch-all: never let the license check itself crash the app
            logger.error(
                "Unexpected error during license verification: %s — failing open",
                exc,
                exc_info=True,
            )

        return await call_next(request)
