"""
Fully Automated Daily OAuth + TOTP Authentication Service.

Provides :class:`AuthService` — an in-process token refresher that replaces
the standalone ``scripts/daily_token_refresh.py`` for production use.

Capabilities
------------
* **Zerodha Kite Connect** — HTTP-based login flow (``requests.Session``)
  with automatic Playwright headless Chromium fallback if the primary
  flow fails (e.g. CAPTCHA, DOM changes).
* **Angel One SmartAPI** — Native SDK ``generateSession()`` with pyotp TOTP.
* **Tenacity retries** — Each broker refresh is retried up to 3 times with
  exponential backoff for transient network failures.
* **ORM persistence** — Writes fresh tokens to the ``users`` table via
  SQLAlchemy ORM (consistent with the rest of the application).
* **Hot-reload** — Updates live broker adapter singletons in ``app.main``
  so trading continues without an application restart.
* **Telegram notifications** — Success confirmations and failure alerts.

Scheduling
----------
Intended to be registered as an APScheduler cron job at **08:45 IST**
Monday–Friday via ``app.main.lifespan``.  Can also be triggered manually
via the ``POST /admin/refresh-tokens`` endpoint.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import pyotp
import requests
from kiteconnect import KiteConnect
from sqlalchemy import text as sa_text
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    before_log,
    after_log,
    retry_if_exception_type,
)

from app.config import settings
from app.database import SessionLocal
from app.models import User

if TYPE_CHECKING:
    from app.notifications.telegram import TelegramNotifier

logger = logging.getLogger(__name__)

# IST timezone constant (UTC+05:30)
IST = timezone(timedelta(hours=5, minutes=30))


# ====================================================================== #
#  AuthService                                                            #
# ====================================================================== #


class AuthService:
    """Automated daily OAuth + TOTP token refresher for all configured brokers.

    Args:
        notifier: An initialised :class:`TelegramNotifier` instance for
            sending success/failure alerts.  May be ``None`` during testing.
    """

    def __init__(self, notifier: TelegramNotifier | None = None) -> None:
        self._notifier = notifier

    # ------------------------------------------------------------------ #
    #  Public orchestrator                                                #
    # ------------------------------------------------------------------ #

    async def refresh_all_tokens(self) -> dict[str, bool]:
        """Refresh access tokens for every configured broker.

        Returns:
            Dict mapping broker name to success flag, e.g.
            ``{"zerodha": True, "angelone": True}``.
        """
        results: dict[str, bool] = {}
        now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        logger.info("═" * 60)
        logger.info("Daily token refresh started at %s", now_ist)
        logger.info("═" * 60)

        # ── Zerodha Kite Connect ──────────────────────────────────────
        try:
            await self._refresh_kite_token()
            results["zerodha"] = True
            logger.info("✅ Zerodha Kite token refresh SUCCEEDED")
        except Exception as exc:
            results["zerodha"] = False
            logger.critical(
                "🚨 Zerodha Kite token refresh FAILED: %s", exc, exc_info=True
            )
            await self._send_notification(
                "🚨 <b>Zerodha Token Refresh FAILED</b>\n\n"
                f"⚠️ {type(exc).__name__}: {exc}\n"
                f"👤 Client: {settings.kite_user_id}\n"
                f"🕐 Time: {now_ist}\n\n"
                "Manual intervention required."
            )

        # ── Angel One SmartAPI (only if configured) ───────────────────
        if settings.angel_api_key and settings.angel_client_id:
            try:
                await self._refresh_angel_token()
                results["angelone"] = True
                logger.info("✅ Angel One token refresh SUCCEEDED")
            except Exception as exc:
                results["angelone"] = False
                logger.critical(
                    "🚨 Angel One token refresh FAILED: %s", exc, exc_info=True
                )
                await self._send_notification(
                    "🚨 <b>Angel One Token Refresh FAILED</b>\n\n"
                    f"⚠️ {type(exc).__name__}: {exc}\n"
                    f"👤 Client: {settings.angel_client_id}\n"
                    f"🕐 Time: {now_ist}\n\n"
                    "Manual intervention required."
                )

        # ── Summary notification ──────────────────────────────────────
        all_ok = all(results.values())
        if all_ok:
            brokers_str = ", ".join(
                f"{name} ✅" for name, ok in results.items() if ok
            )
            await self._send_notification(
                "🔑 <b>Daily Token Refresh Complete</b>\n\n"
                f"📋 Brokers: {brokers_str}\n"
                f"🕐 Time: {now_ist}\n\n"
                "All broker sessions are ready for trading."
            )

        logger.info(
            "Daily token refresh finished — results: %s",
            {k: ("OK" if v else "FAILED") for k, v in results.items()},
        )
        return results

    # ------------------------------------------------------------------ #
    #  Zerodha Kite Connect                                               #
    # ------------------------------------------------------------------ #

    async def _refresh_kite_token(self) -> None:
        """Refresh the Zerodha Kite Connect access token.

        Tries the requests-based HTTP login first, then falls back to
        Playwright headless Chromium if that fails.  On success, persists
        the token and hot-reloads the broker adapter.

        Raises:
            Exception: If both login flows and session generation fail.
        """
        kite = KiteConnect(api_key=settings.kite_api_key)
        request_token: str | None = None

        # ── Attempt 1: requests-based HTTP flow ───────────────────────
        try:
            request_token = self._login_kite_via_requests(kite)
            logger.info("Kite request_token obtained via requests flow")
        except Exception:
            logger.warning(
                "Kite requests-based login failed — falling back to Playwright",
                exc_info=True,
            )

        # ── Attempt 2: Playwright headless fallback ───────────────────
        if request_token is None:
            try:
                request_token = await self._login_kite_via_playwright(kite)
                logger.info("Kite request_token obtained via Playwright fallback")
            except Exception:
                logger.error("Kite Playwright fallback also failed", exc_info=True)
                raise RuntimeError(
                    f"All Kite login flows failed for user {settings.kite_user_id}. "
                    "Manual intervention required."
                )

        # ── Generate session ──────────────────────────────────────────
        session_data = kite.generate_session(
            request_token, api_secret=settings.kite_api_secret
        )
        access_token: str = session_data["access_token"]
        logger.info("Kite session generated — access_token obtained")

        # ── Persist + hot-reload ──────────────────────────────────────
        self._persist_token(
            broker_client_id=settings.kite_user_id,
            access_token=access_token,
            broker_name="zerodha",
        )
        self._hot_reload_broker(
            broker_name="zerodha",
            broker_client_id=settings.kite_user_id,
            access_token=access_token,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_exception_type((requests.RequestException, ConnectionError, TimeoutError)),
        before=before_log(logger, logging.DEBUG),
        after=after_log(logger, logging.DEBUG),
        reraise=True,
    )
    def _login_kite_via_requests(self, kite: KiteConnect) -> str:
        """HTTP-based Kite login: credentials → TOTP → request_token.

        Args:
            kite: Un-authenticated KiteConnect instance.

        Returns:
            The ``request_token`` needed for session generation.

        Raises:
            requests.RequestException: On HTTP failures.
            ValueError: If request_token cannot be extracted.
        """
        totp = pyotp.TOTP(settings.kite_totp_secret).now()
        login_url = kite.login_url()

        session = requests.Session()
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        })

        # Step 1 — GET the login page
        logger.debug("Kite login: GET %s", login_url)
        session.get(login_url, allow_redirects=True, timeout=30)

        # Step 2 — POST credentials
        resp_login = session.post(
            "https://kite.zerodha.com/api/login",
            data={
                "user_id": settings.kite_user_id,
                "password": settings.kite_password,
            },
            timeout=30,
        )
        resp_login.raise_for_status()
        login_data = resp_login.json()
        request_id = login_data["data"]["request_id"]
        logger.debug("Kite login step 1 — request_id=%s", request_id)

        # Step 3 — POST TOTP 2FA
        resp_twofa = session.post(
            "https://kite.zerodha.com/api/twofa",
            data={
                "user_id": settings.kite_user_id,
                "request_id": request_id,
                "twofa_value": totp,
                "twofa_type": "totp",
            },
            allow_redirects=False,
            timeout=30,
        )

        # Extract request_token from redirect or response body
        if resp_twofa.status_code in (301, 302, 303):
            redirect_url = resp_twofa.headers.get("Location", "")
            return self._extract_request_token(redirect_url)

        resp_twofa.raise_for_status()
        twofa_data = resp_twofa.json()

        if "request_token" in twofa_data.get("data", {}):
            return twofa_data["data"]["request_token"]

        redirect_url = twofa_data.get("data", {}).get("redirect_url", "")
        return self._extract_request_token(redirect_url)

    async def _login_kite_via_playwright(self, kite: KiteConnect) -> str:
        """Headless Chromium fallback for Kite login.

        Launches a Playwright browser, automates the web-login + TOTP entry,
        and intercepts the redirect URL to capture the ``request_token``.

        Args:
            kite: Un-authenticated KiteConnect instance.

        Returns:
            The ``request_token`` needed for session generation.

        Raises:
            RuntimeError: If Playwright is not installed.
            Exception: On browser automation or parsing failure.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright is not installed. Install with: "
                "pip install playwright && playwright install chromium"
            )

        totp = pyotp.TOTP(settings.kite_totp_secret).now()
        login_url = kite.login_url()

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page = await context.new_page()

            try:
                # Navigate to Kite login
                logger.info("Playwright: navigating to Kite login")
                await page.goto(login_url, wait_until="networkidle", timeout=30_000)

                # Fill credentials and submit
                await page.fill('input[type="text"]', settings.kite_user_id)
                await page.fill('input[type="password"]', settings.kite_password)
                await page.click('button[type="submit"]')
                logger.info("Playwright: credentials submitted, waiting for TOTP input")

                # Wait for TOTP input field
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
                logger.info("Playwright: TOTP submitted, waiting for redirect")

                # Wait for redirect containing request_token
                await page.wait_for_url("**/request_token=*", timeout=20_000)
                redirect_url = page.url
                request_token = self._extract_request_token(redirect_url)
                logger.info("Playwright: request_token captured from redirect URL")
                return request_token

            finally:
                await browser.close()

    # ------------------------------------------------------------------ #
    #  Angel One SmartAPI                                                  #
    # ------------------------------------------------------------------ #

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, Exception)),
        before=before_log(logger, logging.DEBUG),
        after=after_log(logger, logging.DEBUG),
        reraise=True,
    )
    async def _refresh_angel_token(self) -> None:
        """Refresh the Angel One SmartAPI access token.

        Uses the SmartAPI SDK's native ``generateSession()`` which accepts
        TOTP directly — no browser automation needed.

        Raises:
            ImportError: If ``smartapi-python`` is not installed.
            Exception: On authentication or persistence failure.
        """
        try:
            from SmartApi import SmartConnect
        except ImportError:
            raise ImportError(
                "smartapi-python is not installed. Install with: "
                "pip install smartapi-python"
            )

        totp = pyotp.TOTP(settings.angel_totp_secret).now()
        logger.info("Angel One: generating session for client %s", settings.angel_client_id)

        smart = SmartConnect(api_key=settings.angel_api_key)
        session_data = smart.generateSession(
            clientCode=settings.angel_client_id,
            password=settings.angel_password,
            totp=totp,
        )

        if not session_data or not session_data.get("data"):
            raise RuntimeError(
                f"Angel One generateSession returned empty response: {session_data}"
            )

        access_token: str = session_data["data"].get("jwtToken", "")
        refresh_token: str = session_data["data"].get("refreshToken", "")

        if not access_token:
            raise RuntimeError(
                f"Angel One jwtToken missing from session data: {session_data}"
            )

        logger.info("Angel One session generated — jwtToken obtained")

        # Persist + hot-reload
        self._persist_token(
            broker_client_id=settings.angel_client_id,
            access_token=access_token,
            broker_name="angelone",
        )
        self._hot_reload_broker(
            broker_name="angelone",
            broker_client_id=settings.angel_client_id,
            access_token=access_token,
        )

    # ------------------------------------------------------------------ #
    #  Token persistence (ORM-based)                                      #
    # ------------------------------------------------------------------ #

    def _persist_token(
        self,
        broker_client_id: str,
        access_token: str,
        broker_name: str,
    ) -> None:
        """Persist a fresh access token to the ``users`` table.

        Args:
            broker_client_id: The broker client ID to match in the DB.
            access_token: The new access token to store.
            broker_name: Human-readable broker name for logging.

        Raises:
            Exception: On database write failure (rolls back transaction).
        """
        db = SessionLocal()
        try:
            user = (
                db.query(User)
                .filter(User.broker_client_id == broker_client_id)
                .first()
            )

            if user is None:
                logger.warning(
                    "No user found with broker_client_id=%s — "
                    "creating a placeholder row is not supported; "
                    "ensure the user exists in the database.",
                    broker_client_id,
                )
                return

            user.kite_access_token = access_token
            user.kite_token_generated_at = datetime.now(timezone.utc)
            db.commit()

            logger.info(
                "Access token persisted | broker=%s client_id=%s user=%s",
                broker_name,
                broker_client_id,
                user.full_name,
            )
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to persist access token | broker=%s client_id=%s",
                broker_name,
                broker_client_id,
            )
            raise
        finally:
            db.close()

    # ------------------------------------------------------------------ #
    #  Hot-reload broker singletons                                       #
    # ------------------------------------------------------------------ #

    def _hot_reload_broker(
        self,
        broker_name: str,
        broker_client_id: str,
        access_token: str,
    ) -> None:
        """Update the live broker adapter singleton with a fresh access token.

        This avoids requiring an application restart after daily token
        refresh.  Reaches into ``app.main._brokers`` and
        ``app.main._default_broker`` to replace the adapter in-place.

        Args:
            broker_name: ``"zerodha"`` or ``"angelone"``.
            broker_client_id: Broker client identifier.
            access_token: Freshly generated access token.
        """
        try:
            import app.main as main_module

            if broker_name == "zerodha":
                from app.broker.kite import KiteAdapter

                new_adapter = KiteAdapter(
                    api_key=settings.kite_api_key,
                    api_secret=settings.kite_api_secret,
                    access_token=access_token,
                    algo_id=settings.kite_algo_id,
                )
                main_module._brokers[broker_client_id] = new_adapter
                main_module._default_broker = new_adapter
                logger.info(
                    "Hot-reloaded Zerodha KiteAdapter for client_id=%s",
                    broker_client_id,
                )

            elif broker_name == "angelone":
                from app.broker.angel import AngelAdapter

                new_adapter = AngelAdapter(
                    api_key=settings.angel_api_key,
                    client_id=settings.angel_client_id,
                    password=settings.angel_password or "",
                    totp_secret=settings.angel_totp_secret or "",
                    access_token=access_token,
                    algo_id=settings.angel_algo_id or "",
                )
                main_module._brokers[broker_client_id] = new_adapter
                logger.info(
                    "Hot-reloaded Angel One adapter for client_id=%s",
                    broker_client_id,
                )

            else:
                logger.warning("Unknown broker name '%s' — skipping hot-reload", broker_name)

        except Exception:
            logger.exception(
                "Hot-reload failed for broker=%s client_id=%s — "
                "the app will use the old adapter until restarted",
                broker_name,
                broker_client_id,
            )

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_request_token(url: str) -> str:
        """Parse ``request_token`` from a redirect URL query string.

        Args:
            url: Full URL expected to contain a ``request_token`` parameter.

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

    async def _send_notification(self, message: str) -> None:
        """Send a Telegram notification, silently ignoring failures.

        Args:
            message: HTML-formatted message body.
        """
        if self._notifier is None:
            logger.debug("No notifier configured — skipping Telegram message")
            return
        try:
            await self._notifier.send_message(message)
        except Exception:
            logger.warning("Failed to send Telegram notification", exc_info=True)
