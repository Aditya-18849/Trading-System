"""
Risk Management Engine for SEBI-Compliant Algo Trading System.

Evaluates trade requests against user-specific risk parameters including
daily P&L limits, trade count caps, post-loss cooldowns, and position sizing.
All timestamps use IST (Asia/Kolkata, UTC+05:30).

Extended with calculate_risk_for_signal() for the strategy engine pipeline
to compute position size, capital at risk, and SL/target in rupee terms.
"""

from math import floor
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import func, cast, Date
from pydantic import BaseModel

from app.config import settings
from app.models import Trade, User
from app.schemas import RiskCheckResult

# IST timezone offset (UTC+05:30)
IST = timezone(timedelta(hours=5, minutes=30))


class RiskEngine:
    """Synchronous risk management engine.

    Performs sequential risk checks (daily P&L, trade count, cooldown,
    position sizing) and returns an approved/rejected RiskCheckResult.
    """

    def __init__(self, db: Session, user: User):
        """Initialise the risk engine with a DB session and user context.

        User-specific risk parameters are read from the User model.
        If any value is None or zero, the corresponding global setting
        from ``app.config.settings`` is used as a fallback.

        Args:
            db: Active SQLAlchemy session.
            user: The User ORM instance whose risk params govern this engine.
        """
        self.db = db
        self.user = user

        # Resolve user-level overrides, falling back to global settings
        self.total_capital: float = float(user.total_capital or 0) or settings.total_capital
        self.risk_per_trade_pct: float = float(user.risk_per_trade_pct or 0) or settings.risk_per_trade_pct
        self.max_daily_loss: float = float(user.max_daily_loss or 0) or settings.max_daily_loss
        self.max_trades_per_day: int = int(user.max_trades_per_day or 0) or settings.max_trades_per_day
        self.cooldown_minutes: int = int(user.cooldown_minutes or 0) or settings.cooldown_minutes_after_loss

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        symbol: str,
        exchange: str,
        direction: str,
        entry_price: float,
        stoploss_pct: float | None = None,
        target_pct: float | None = None,
    ) -> RiskCheckResult:
        """Run all risk checks and return an approval/rejection result.

        Checks are executed in the following order:
        1. Daily P&L limit
        2. Daily trade count limit
        3. Post-loss cooldown
        4. Position sizing & SL/target calculation

        Args:
            symbol: Trading symbol (e.g. ``RELIANCE``).
            exchange: Exchange code (e.g. ``NSE``).
            direction: ``BUY`` or ``SELL``.
            entry_price: Expected entry price.
            stoploss_pct: Optional override for stop-loss percentage.
            target_pct: Optional override for target percentage.

        Returns:
            A ``RiskCheckResult`` with approval status, computed quantity,
            stop-loss price, and target price.
        """
        # --- 1. Daily P&L Check ---
        total_pnl = self._get_today_pnl()
        if total_pnl <= -self.max_daily_loss:
            return RiskCheckResult(
                approved=False,
                reason=(
                    f"Daily loss limit breached. Current P&L: ₹{total_pnl:.2f}, "
                    f"Max allowed loss: ₹{self.max_daily_loss:.2f}"
                ),
            )

        # --- 2. Trade Count Check ---
        trade_count = self._get_today_trade_count()
        if trade_count >= self.max_trades_per_day:
            return RiskCheckResult(
                approved=False,
                reason=(
                    f"Daily trade limit reached. Trades today: {trade_count}, "
                    f"Max allowed: {self.max_trades_per_day}"
                ),
            )

        # --- 3. Cooldown Check ---
        cooldown_remaining = self._get_cooldown_remaining()
        if cooldown_remaining is not None:
            return RiskCheckResult(
                approved=False,
                reason=(
                    f"Post-loss cooldown active. {cooldown_remaining} minute(s) remaining "
                    f"before next trade is allowed."
                ),
            )

        # --- 4. Position Sizing ---
        sl_pct = stoploss_pct or settings.default_stoploss_pct
        tgt_pct = target_pct or settings.default_target_pct

        risk_amount = self.total_capital * (self.risk_per_trade_pct / 100)
        risk_per_share = entry_price * (sl_pct / 100)
        quantity = max(1, floor(risk_amount / risk_per_share))

        # --- 5. SL / Target Calculation ---
        direction_upper = direction.upper()
        if direction_upper == "BUY":
            stoploss_price = round(entry_price * (1 - sl_pct / 100), 2)
            target_price = round(entry_price * (1 + tgt_pct / 100), 2)
        else:  # SELL
            stoploss_price = round(entry_price * (1 + sl_pct / 100), 2)
            target_price = round(entry_price * (1 - tgt_pct / 100), 2)

        return RiskCheckResult(
            approved=True,
            quantity=quantity,
            stoploss_price=stoploss_price,
            target_price=target_price,
        )

    def get_daily_summary(self) -> dict:
        """Return a snapshot of today's trading activity.

        Returns:
            A dict with keys ``trade_count``, ``total_pnl``, and ``capital``.
        """
        return {
            "trade_count": self._get_today_trade_count(),
            "total_pnl": self._get_today_pnl(),
            "capital": self.total_capital,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_today_pnl(self) -> float:
        """Query the total P&L for this user's trades opened today (IST).

        Only trades with status ``CLOSED`` or ``OPEN`` are included.

        Returns:
            Cumulative P&L as a float (negative means loss).
        """
        today_ist = datetime.now(IST).date()

        result = (
            self.db.query(func.coalesce(func.sum(Trade.pnl), 0))
            .filter(
                Trade.user_id == self.user.id,
                cast(Trade.opened_at, Date) == today_ist,
                Trade.status.in_(["CLOSED", "OPEN"]),
            )
            .scalar()
        )
        return float(result)

    def _get_today_trade_count(self) -> int:
        """Count all trades opened today (IST) for this user, regardless of status.

        Returns:
            Number of trades placed today.
        """
        today_ist = datetime.now(IST).date()

        count = (
            self.db.query(func.count(Trade.id))
            .filter(
                Trade.user_id == self.user.id,
                cast(Trade.opened_at, Date) == today_ist,
            )
            .scalar()
        )
        return int(count or 0)

    def _get_cooldown_remaining(self) -> int | None:
        """Check whether the post-loss cooldown is still active.

        Looks at the most recent closed trade with negative P&L.  If its
        ``closed_at`` timestamp is within ``cooldown_minutes`` of now (IST),
        returns the number of remaining minutes.  Otherwise returns ``None``.

        Returns:
            Remaining cooldown minutes, or ``None`` if no cooldown applies.
        """
        last_loss = (
            self.db.query(Trade)
            .filter(
                Trade.user_id == self.user.id,
                Trade.pnl < 0,
                Trade.status == "CLOSED",
            )
            .order_by(Trade.closed_at.desc())
            .first()
        )

        if last_loss is None or last_loss.closed_at is None:
return None


# =============================================================================
# Strategy Engine Integration
# =============================================================================

class RiskCalculationResult(BaseModel):
    """Result of risk calculation for a strategy signal.

    Used by the strategy engine pipeline to get position sizing and
    rupee-level risk metrics before creating a recommendation.
    """
    approved: bool
    reason: Optional[str] = None
    quantity: int
    entry_price: float
    stoploss_price: float
    target_price: float
    capital_at_risk: float
    risk_reward_ratio: float
    max_loss_if_sl_hit: float
    max_profit_if_target_hit: float


def calculate_risk_for_signal(
    db: Session,
    user: User,
    symbol: str,
    exchange: str,
    direction: str,
    entry_price: float,
    stoploss_price: float,
    target_price: float,
) -> RiskCalculationResult:
    """Calculate position size and risk metrics for a pre-priced signal.

    This is the single source of truth for risk calculations — both the
    webhook path and the strategy-engine path call this function (or the
    RiskEngine.evaluate method which uses the same logic).

    Unlike RiskEngine.evaluate(), this accepts absolute SL/target prices
    (not percentages) and returns rupee-level risk metrics.

    Args:
        db: Active SQLAlchemy session.
        user: User ORM instance.
        symbol: Trading symbol.
        exchange: Exchange code.
        direction: "BUY" or "SELL".
        entry_price: Expected entry price.
        stoploss_price: Absolute stop-loss price.
        target_price: Absolute target price.

    Returns:
        RiskCalculationResult with position size and risk metrics.
    """
    # Resolve user-level risk params (same as RiskEngine.__init__)
    total_capital = float(user.total_capital or 0) or settings.total_capital
    risk_per_trade_pct = float(user.risk_per_trade_pct or 0) or settings.risk_per_trade_pct
    max_daily_loss = float(user.max_daily_loss or 0) or settings.max_daily_loss
    max_trades_per_day = int(user.max_trades_per_day or 0) or settings.max_trades_per_day
    cooldown_minutes = int(user.cooldown_minutes or 0) or settings.cooldown_minutes_after_loss

    # --- 1. Daily P&L Check ---
    today_ist = datetime.now(IST).date()
    total_pnl = (
        db.query(func.coalesce(func.sum(Trade.pnl), 0))
        .filter(
            Trade.user_id == user.id,
            cast(Trade.opened_at, Date) == today_ist,
            Trade.status.in_(["CLOSED", "OPEN"]),
        )
        .scalar()
    )
    total_pnl = float(total_pnl)
    if total_pnl <= -max_daily_loss:
        return RiskCalculationResult(
            approved=False,
            reason=(
                f"Daily loss limit breached. Current P&L: ₹{total_pnl:.2f}, "
                f"Max allowed loss: ₹{max_daily_loss:.2f}"
            ),
            quantity=0,
            entry_price=entry_price,
            stoploss_price=stoploss_price,
            target_price=target_price,
            capital_at_risk=0,
            risk_reward_ratio=0,
            max_loss_if_sl_hit=0,
            max_profit_if_target_hit=0,
        )

    # --- 2. Trade Count Check ---
    trade_count = (
        db.query(func.count(Trade.id))
        .filter(
            Trade.user_id == user.id,
            cast(Trade.opened_at, Date) == today_ist,
        )
        .scalar()
    )
    trade_count = int(trade_count or 0)
    if trade_count >= max_trades_per_day:
        return RiskCalculationResult(
            approved=False,
            reason=(
                f"Daily trade limit reached. Trades today: {trade_count}, "
                f"Max allowed: {max_trades_per_day}"
            ),
            quantity=0,
            entry_price=entry_price,
            stoploss_price=stoploss_price,
            target_price=target_price,
            capital_at_risk=0,
            risk_reward_ratio=0,
            max_loss_if_sl_hit=0,
            max_profit_if_target_hit=0,
        )

    # --- 3. Cooldown Check ---
    last_loss = (
        db.query(Trade)
        .filter(
            Trade.user_id == user.id,
            Trade.pnl < 0,
            Trade.status == "CLOSED",
        )
        .order_by(Trade.closed_at.desc())
        .first()
    )
    if last_loss is not None and last_loss.closed_at is not None:
        closed_at = last_loss.closed_at
        if closed_at.tzinfo is None:
            closed_at = closed_at.replace(tzinfo=IST)
        now_ist = datetime.now(IST)
        elapsed_minutes = (now_ist - closed_at).total_seconds() / 60
        if elapsed_minutes < cooldown_minutes:
            remaining = int(cooldown_minutes - elapsed_minutes) + 1
            return RiskCalculationResult(
                approved=False,
                reason=(
                    f"Post-loss cooldown active. {remaining} minute(s) remaining "
                    f"before next trade is allowed."
                ),
                quantity=0,
                entry_price=entry_price,
                stoploss_price=stoploss_price,
                target_price=target_price,
                capital_at_risk=0,
                risk_reward_ratio=0,
                max_loss_if_sl_hit=0,
                max_profit_if_target_hit=0,
            )

    # --- 4. Position Sizing ---
    # Risk amount in rupees
    risk_amount = total_capital * (risk_per_trade_pct / 100)
    # Risk per share
    risk_per_share = abs(entry_price - stoploss_price)
    if risk_per_share <= 0:
        return RiskCalculationResult(
            approved=False,
            reason="Invalid stop-loss price (must differ from entry price)",
            quantity=0,
            entry_price=entry_price,
            stoploss_price=stoploss_price,
            target_price=target_price,
            capital_at_risk=0,
            risk_reward_ratio=0,
            max_loss_if_sl_hit=0,
            max_profit_if_target_hit=0,
        )
    quantity = max(1, floor(risk_amount / risk_per_share))

    # --- 5. Capital at Risk & R:R ---
    capital_at_risk = quantity * risk_per_share
    target_distance = abs(target_price - entry_price)
    risk_reward_ratio = round(target_distance / risk_per_share, 2) if risk_per_share > 0 else 0
    max_loss_if_sl_hit = capital_at_risk
    max_profit_if_target_hit = quantity * target_distance

    return RiskCalculationResult(
        approved=True,
        quantity=quantity,
        entry_price=entry_price,
        stoploss_price=stoploss_price,
        target_price=target_price,
        capital_at_risk=round(capital_at_risk, 2),
        risk_reward_ratio=risk_reward_ratio,
        max_loss_if_sl_hit=round(max_loss_if_sl_hit, 2),
        max_profit_if_target_hit=round(max_profit_if_target_hit, 2),
    )

        now_ist = datetime.now(IST)

        # Ensure closed_at is timezone-aware (IST) for comparison
        closed_at = last_loss.closed_at
        if closed_at.tzinfo is None:
            closed_at = closed_at.replace(tzinfo=IST)

        elapsed = now_ist - closed_at
        elapsed_minutes = elapsed.total_seconds() / 60

        if elapsed_minutes < self.cooldown_minutes:
            remaining = int(self.cooldown_minutes - elapsed_minutes) + 1  # ceiling
            return remaining

        return None
