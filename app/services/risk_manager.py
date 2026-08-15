"""
Risk Manager Façade for the Automated Execution Pipeline.

Wraps :class:`~app.risk_engine.RiskEngine` to accept
:class:`~app.strategies.advanced_engine.StrategySignal` objects directly,
translating ATR-derived stop-loss / target prices into the percentage-based
interface that the core risk engine expects.

Returns a :class:`RiskDecision` with all values the executor needs to
proceed (or reject) the signal.

Design rationale
~~~~~~~~~~~~~~~~
The existing ``RiskEngine`` is a well-tested, synchronous module that
operates on raw floats (entry_price, stoploss_pct, target_pct).  Rather
than modifying it — risking regressions in the live webhook flow — this
façade adapts the ``StrategySignal`` contract to the risk engine's contract
and enriches the result with signal-level metadata (``signal_id``,
``strategy_name``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User
from app.risk_engine import RiskEngine
from app.strategies.advanced_engine import StrategySignal

logger = logging.getLogger(__name__)

# IST timezone offset (UTC+05:30) — consistent with the rest of the codebase
IST = timezone(timedelta(hours=5, minutes=30))


# ─────────────────────────────────────────────────────────────────────────────
# Output model
# ─────────────────────────────────────────────────────────────────────────────

class RiskDecision(BaseModel):
    """Result of passing a ``StrategySignal`` through the risk manager.

    Attributes:
        approved:              ``True`` if the signal passed all risk checks.
        reason:                Human-readable rejection reason (``None`` when
                               approved).
        signal_id:             Echoed back from the incoming signal for
                               correlation.
        strategy_name:         Echoed back from the incoming signal.
        final_quantity:        Position size computed by the risk engine (may
                               differ from the signal's suggested quantity).
        final_stoploss_price:  Stop-loss price validated / recomputed by the
                               risk engine.
        final_target_price:    Target price validated / recomputed by the risk
                               engine.
        timestamp:             IST-aware timestamp of the decision.
    """

    approved: bool
    reason: Optional[str] = None
    signal_id: str
    strategy_name: str
    final_quantity: Optional[int] = None
    final_stoploss_price: Optional[float] = None
    final_target_price: Optional[float] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(IST))


# ─────────────────────────────────────────────────────────────────────────────
# Risk Manager
# ─────────────────────────────────────────────────────────────────────────────

class RiskManager:
    """StrategySignal-aware risk validation façade.

    Translates a :class:`StrategySignal`'s absolute price levels into the
    percentage-based parameters expected by :class:`~app.risk_engine.RiskEngine`,
    delegates the evaluation, and wraps the result in a :class:`RiskDecision`.

    Args:
        db:   Active SQLAlchemy session.
        user: The ``User`` ORM instance governing risk parameters.
    """

    def __init__(self, db: Session, user: User) -> None:
        self.db = db
        self.user = user
        self._engine = RiskEngine(db, user)

    # ------------------------------------------------------------------ #
    #  Public API                                                         #
    # ------------------------------------------------------------------ #

    def validate_signal(self, signal: StrategySignal) -> RiskDecision:
        """Evaluate a strategy signal against all risk controls.

        The method converts the signal's absolute ``stop_loss`` and
        ``target_price`` into percentages relative to ``entry_price``
        so that the underlying :class:`RiskEngine` can apply its standard
        position-sizing formula.

        If the signal specifies ``action=EXIT``, the risk manager
        auto-approves (exit signals reduce risk, they don't add it).

        Args:
            signal: A ``StrategySignal`` from the strategy engine or
                TradingView webhook adapter.

        Returns:
            A :class:`RiskDecision` indicating approval or rejection with
            full pricing / sizing details.
        """
        logger.info(
            "RiskManager: evaluating signal | signal_id=%s symbol=%s "
            "action=%s entry=%.2f SL=%.2f TGT=%.2f strategy=%s",
            signal.signal_id,
            signal.symbol,
            signal.action,
            signal.entry_price,
            signal.stop_loss,
            signal.target_price,
            signal.strategy_name,
        )

        # EXIT signals are always approved — they reduce exposure
        if signal.action == "EXIT":
            logger.info(
                "RiskManager: EXIT signal auto-approved | signal_id=%s",
                signal.signal_id,
            )
            return RiskDecision(
                approved=True,
                signal_id=signal.signal_id,
                strategy_name=signal.strategy_name,
                final_quantity=signal.quantity,
                final_stoploss_price=signal.stop_loss,
                final_target_price=signal.target_price,
            )

        # --- Convert absolute prices → percentages for the risk engine ---
        stoploss_pct = self._price_to_pct(
            signal.entry_price,
            signal.stop_loss,
            signal.action,
            is_stoploss=True,
        )
        target_pct = self._price_to_pct(
            signal.entry_price,
            signal.target_price,
            signal.action,
            is_stoploss=False,
        )

        # Map signal action to direction string expected by risk engine
        direction = "BUY" if signal.action == "BUY" else "SELL"

        # --- Delegate to core risk engine ---
        risk_result = self._engine.evaluate(
            symbol=signal.symbol,
            exchange="NSE",  # default exchange; signal doesn't carry this
            direction=direction,
            entry_price=signal.entry_price,
            stoploss_pct=stoploss_pct,
            target_pct=target_pct,
        )

        if not risk_result.approved:
            logger.info(
                "RiskManager: signal REJECTED | signal_id=%s reason=%s",
                signal.signal_id,
                risk_result.reason,
            )
            return RiskDecision(
                approved=False,
                reason=risk_result.reason,
                signal_id=signal.signal_id,
                strategy_name=signal.strategy_name,
            )

        logger.info(
            "RiskManager: signal APPROVED | signal_id=%s qty=%d SL=%.2f TGT=%.2f",
            signal.signal_id,
            risk_result.quantity,
            risk_result.stoploss_price,
            risk_result.target_price,
        )

        return RiskDecision(
            approved=True,
            signal_id=signal.signal_id,
            strategy_name=signal.strategy_name,
            final_quantity=risk_result.quantity,
            final_stoploss_price=risk_result.stoploss_price,
            final_target_price=risk_result.target_price,
        )

    def get_daily_summary(self) -> dict:
        """Proxy to the underlying risk engine's daily summary.

        Returns:
            Dict with ``trade_count``, ``total_pnl``, ``capital``.
        """
        return self._engine.get_daily_summary()

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _price_to_pct(
        entry_price: float,
        target_or_sl_price: float,
        action: str,
        *,
        is_stoploss: bool,
    ) -> float:
        """Convert an absolute price level to a percentage distance.

        For a BUY:
          - SL is below entry  → pct = (entry - sl) / entry × 100
          - Target is above    → pct = (target - entry) / entry × 100

        For a SELL:
          - SL is above entry  → pct = (sl - entry) / entry × 100
          - Target is below    → pct = (entry - target) / entry × 100

        The result is always a positive percentage that the
        :class:`RiskEngine` uses for position sizing and price
        calculation.

        Args:
            entry_price:         Expected entry price.
            target_or_sl_price:  Absolute SL or target price.
            action:              ``"BUY"`` or ``"SELL"``.
            is_stoploss:         ``True`` for stop-loss conversion,
                                 ``False`` for target conversion.

        Returns:
            Positive percentage (e.g. ``0.5`` for 0.5%).
        """
        if entry_price <= 0:
            return settings.default_stoploss_pct if is_stoploss else settings.default_target_pct

        if action == "BUY":
            if is_stoploss:
                pct = abs(entry_price - target_or_sl_price) / entry_price * 100
            else:
                pct = abs(target_or_sl_price - entry_price) / entry_price * 100
        else:  # SELL
            if is_stoploss:
                pct = abs(target_or_sl_price - entry_price) / entry_price * 100
            else:
                pct = abs(entry_price - target_or_sl_price) / entry_price * 100

        # Guard against zero / nonsensical values — fall back to defaults
        if pct <= 0:
            return settings.default_stoploss_pct if is_stoploss else settings.default_target_pct

        return round(pct, 4)
