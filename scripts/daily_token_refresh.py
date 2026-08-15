"""
Daily Kite Connect access-token refresh script.

Run via cron at **08:45 IST** every trading day::

    45 8 * * 1-5  cd /path/to/project && python -m scripts.daily_token_refresh

Flow
----
1. Generate TOTP from ``settings.kite_totp_secret``.
2. Automate the Kite web-login flow using ``requests.Session``.
3. If the requests-based flow fails (e.g. CAPTCHA), fall back to
   Playwright headless Chromium.
4. Call ``kite.generate_session`` to obtain an ``access_token``.
5. Persist the token in the ``users`` table.
6. Send a Telegram confirmation (or error alert).
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pyotp
import requests
from kiteconnect import KiteConnect

from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)


# ------------------------------------------------------------------ #
#  Telegram helpers                                                   #
# ------------------------------------------------------------------ #

async def _send_telegram(message: str) -> None:
    """Send a message via the Telegram Bot API.

    Args:
        message: Text content to send.
    """
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {"chat_id": settings.telegram_chat_id, "text": message}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            logger.info("Telegram message sent successfully")
    except Exception:
        logger.exception("Failed to send Telegram message")


# ------------------------------------------------------------------ #
#  Token persistence                                                  #
# ------------------------------------------------------------------ #

def _persist_token(access_token: str) -> None:
    """Write the fresh access token to the database.

    Args:
        access_token: New Kite access token.
    """
    db = SessionLocal()
    try:
        db.execute(
            # Using text() for a raw SQL UPDATE
            __import__("sqlalchemy").text(
                "UPDATE users "
                "SET kite_access_token = :token, kite_token_generated_at = :ts "
                "WHERE broker_client_id = :client_id"
            ),
            {
                "token": access_token,
                "ts": datetime.now(timezone.utc),
                "client_id": settings.kite_user_id,
            },
        )
        db.commit()
        logger.info(
            "Access token persisted for broker_client_id=%s", settings.kite_user_id
        )
    except Exception:
        db.rollback()
        logger.exception("Failed to persist access token")
        raise
    finally:
        db.close()


# ------------------------------------------------------------------ #
#  Request-token extraction helpers                                   #
# ------------------------------------------------------------------ #

def _extract_request_token_from_url(url: str) -> str:
    """Parse ``request_token`` from a redirect URL query string.

    Args:
        url: Full redirect URL containing ``request_token`` param.

    Returns:
        The extracted request token string.

    Raises:
        ValueError: If the token is not present in the URL.
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    tokens = params.get("request_token")
    if not tokens:
        raise ValueError(f"request_token not found in URL: {url}")
    return tokens[0]


# ------------------------------------------------------------------ #
#  Primary login flow – requests-based                                #
# ------------------------------------------------------------------ #

def _login_via_requests(kite: KiteConnect) -> str:
    """Perform the Kite login flow using plain HTTP requests.

    Args:
        kite: An initialised (but un-authenticated) KiteConnect instance.

    Returns:
        The ``request_token`` needed for session generation.

    Raises:
        Exception: On any HTTP or parsing failure.
    """
    totp = pyotp.TOTP(settings.kite_totp_secret).now()
    login_url = kite.login_url()

    session = requests.Session()

    # Step 1 – GET the login page (follow redirects to land on Kite)
    logger.info("GET login_url: %s", login_url)
    session.get(login_url, allow_redirects=True)

    # Step 2 – POST credentials
    login_payload = {
        "user_id": settings.kite_user_id,
        "password": settings.kite_password,
    }
    resp_login = session.post(
        "https://kite.zerodha.com/api/login",
        data=login_payload,
    )
    resp_login.raise_for_status()
    login_data = resp_login.json()
    request_id = login_data["data"]["request_id"]
    logger.info("Login step 1 complete – request_id=%s", request_id)

    # Step 3 – POST TOTP for two-factor auth
    twofa_payload = {
        "user_id": settings.kite_user_id,
        "request_id": request_id,
        "twofa_value": totp,
    }
    resp_twofa = session.post(
        "https://kite.zerodha.com/api/twofa",
        data=twofa_payload,
        allow_redirects=False,
    )

    # The twofa endpoint may redirect with request_token in the URL
    if resp_twofa.status_code in (301, 302, 303):
        redirect_url = resp_twofa.headers.get("Location", "")
        request_token = _extract_request_token_from_url(redirect_url)
    else:
        # Some API versions return the token in the JSON body
        resp_twofa.raise_for_status()
        twofa_data = resp_twofa.json()
        if "request_token" in twofa_data.get("data", {}):
            request_token = twofa_data["data"]["request_token"]
        else:
            # Follow the redirect chain manually
            redirect_url = twofa_data.get("data", {}).get("redirect_url", "")
            request_token = _extract_request_token_from_url(redirect_url)

    logger.info("Obtained request_token via requests flow")
    return request_token


# ------------------------------------------------------------------ #
#  Fallback login flow – Playwright headless                          #
# ------------------------------------------------------------------ #

async def _login_via_playwright(kite: KiteConnect) -> str:
    """Perform the Kite login flow using Playwright headless Chromium.

    This is the fallback path when the requests-based flow fails (e.g.
    due to CAPTCHA challenges or DOM changes).

    Args:
        kite: An initialised (but un-authenticated) KiteConnect instance.

    Returns:
        The ``request_token`` needed for session generation.

    Raises:
        Exception: On any browser automation or parsing failure.
    """
    from playwright.async_api import async_playwright  # lazy import

    totp = pyotp.TOTP(settings.kite_totp_secret).now()
    login_url = kite.login_url()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            # Navigate to login
            logger.info("Playwright: navigating to %s", login_url)
            await page.goto(login_url, wait_until="networkidle")

            # Fill credentials and submit
            await page.fill('input[type="text"]', settings.kite_user_id)
            await page.fill('input[type="password"]', settings.kite_password)
            await page.click('button[type="submit"]')
            logger.info("Playwright: credentials submitted")

            # Wait for TOTP input to appear
            await page.wait_for_selector(
                'input[type="text"], input[type="number"], input[label*="totp" i]',
                timeout=15_000,
            )

            # Fill TOTP and submit
            totp_input = page.locator(
                'input[type="text"], input[type="number"]'
            ).first
            await totp_input.fill(totp)
            await page.click('button[type="submit"]')
            logger.info("Playwright: TOTP submitted")

            # Wait for redirect containing request_token
            await page.wait_for_url("**/request_token=*", timeout=20_000)
            redirect_url = page.url
            request_token = _extract_request_token_from_url(redirect_url)
            logger.info("Playwright: obtained request_token from redirect URL")
            return request_token

        finally:
            await browser.close()


# ------------------------------------------------------------------ #
#  Main orchestrator                                                  #
# ------------------------------------------------------------------ #

async def main() -> None:
    """Orchestrate the daily Kite access-token refresh."""

    kite = KiteConnect(api_key=settings.kite_api_key)
    request_token: str | None = None

    # --- Attempt 1: requests-based flow -------------------------------- #
    try:
        request_token = _login_via_requests(kite)
    except Exception:
        logger.exception("Requests-based login failed – falling back to Playwright")

    # --- Attempt 2: Playwright headless fallback ----------------------- #
    if request_token is None:
        try:
            request_token = await _login_via_playwright(kite)
        except Exception:
            error_msg = (
                "🚨 Token Refresh FAILED\n"
                f"Client: {settings.kite_user_id}\n"
                "Both requests and Playwright flows failed. Manual intervention required."
            )
            logger.exception("Playwright-based login also failed")
            await _send_telegram(error_msg)
            sys.exit(1)

    # --- Generate session & persist ------------------------------------ #
    try:
        session_data = kite.generate_session(
            request_token, api_secret=settings.kite_api_secret
        )
        access_token: str = session_data["access_token"]
        logger.info("Session generated – access_token obtained")

        _persist_token(access_token)

        success_msg = (
            "✅ Kite Token Refreshed\n"
            f"Client: {settings.kite_user_id}\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        await _send_telegram(success_msg)

    except Exception:
        error_msg = (
            "🚨 Token Refresh FAILED\n"
            f"Client: {settings.kite_user_id}\n"
            "Session generation or DB persistence failed."
        )
        logger.exception("Session generation / persistence failed")
        await _send_telegram(error_msg)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
