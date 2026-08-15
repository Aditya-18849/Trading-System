"""
Tests for Risk Engine Extension (calculate_risk_for_signal).
"""

import pytest
from datetime import date, datetime, timezone, timedelta
from unittest.mock import Mock, MagicMock
from decimal import Decimal

from app.risk_engine import (
    RiskEngine, calculate_risk_for_signal, RiskCalculationResult, IST
)
from app.models import User, Trade
from app.config import settings


class TestRiskEngineExtension:
    """Tests for calculate_risk_for_signal function."""

    def setup_method(self):
        """Setup test fixtures."""
        self.db = Mock()
        self.user = Mock(spec=User)
        self.user.id = "test-user-id"
        self.user.total_capital = Decimal("100000")
        self.user.risk_per_trade_pct = Decimal("1.0")
        self.user.max_daily_loss = Decimal("1000")
        self.user.max_trades_per_day = 3
        self.user.cooldown_minutes = 20

    def test_approved_signal(self):
        """Test approved risk calculation."""
        # Mock DB queries
        self.db.query.return_value.filter.return_value.scalar.return_value = 0  # P&L
        self.db.query.return_value.filter.return_value.scalar.return_value = 0  # Trade count
        self.db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None  # No recent loss

        result = calculate_risk_for_signal(
            db=self.db,
            user=self.user,
            symbol="RELIANCE",
            exchange="NSE",
            direction="BUY",
            entry_price=2500.0,
            stoploss_price=2475.0,  # 1% SL
            target_price=2550.0,    # 2% target
        )

        assert result.approved is True
        assert result.quantity > 0
        assert result.capital_at_risk > 0
        assert result.risk_reward_ratio == 2.0  # 2% / 1%
        assert result.max_loss_if_sl_hit > 0
        assert result.max_profit_if_target_hit > 0

    def test_daily_loss_limit_rejection(self):
        """Test rejection when daily loss limit breached."""
        self.db.query.return_value.filter.return_value.scalar.return_value = -1500.0  # Exceeds 1000 limit

        result = calculate_risk_for_signal(
            db=self.db,
            user=self.user,
            symbol="RELIANCE",
            exchange="NSE",
            direction="BUY",
            entry_price=2500.0,
            stoploss_price=2475.0,
            target_price=2550.0,
        )

        assert result.approved is False
        assert "Daily loss limit breached" in result.reason
        assert result.quantity == 0

    def test_trade_count_limit_rejection(self):
        """Test rejection when daily trade limit reached."""
        self.db.query.return_value.filter.return_value.scalar.side_effect = [0, 3]  # P&L=0, count=3

        result = calculate_risk_for_signal(
            db=self.db,
            user=self.user,
            symbol="RELIANCE",
            exchange="NSE",
            direction="BUY",
            entry_price=2500.0,
            stoploss_price=2475.0,
            target_price=2550.0,
        )

        assert result.approved is False
        assert "Daily trade limit reached" in result.reason

    def test_cooldown_rejection(self):
        """Test rejection when post-loss cooldown active."""
        self.db.query.return_value.filter.return_value.scalar.side_effect = [0, 0]  # P&L=0, count=0

        # Recent loss within cooldown period
        recent_loss = Mock()
        recent_loss.closed_at = datetime.now(IST) - timedelta(minutes=10)
        recent_loss.pnl = -500
        self.db.query.return_value.filter.return_value.order_by.return_value.first.return_value = recent_loss

        result = calculate_risk_for_signal(
            db=self.db,
            user=self.user,
            symbol="RELIANCE",
            exchange="NSE",
            direction="BUY",
            entry_price=2500.0,
            stoploss_price=2475.0,
            target_price=2550.0,
        )

        assert result.approved is False
        assert "Post-loss cooldown active" in result.reason

    def test_invalid_stoploss_price(self):
        """Test rejection when stoploss equals entry price."""
        self.db.query.return_value.filter.return_value.scalar.side_effect = [0, 0]
        self.db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = calculate_risk_for_signal(
            db=self.db,
            user=self.user,
            symbol="RELIANCE",
            exchange="NSE",
            direction="BUY",
            entry_price=2500.0,
            stoploss_price=2500.0,  # Same as entry
            target_price=2550.0,
        )

        assert result.approved is False
        assert "Invalid stop-loss price" in result.reason

    def test_sell_direction_calculation(self):
        """Test risk calculation for SELL direction."""
        self.db.query.return_value.filter.return_value.scalar.side_effect = [0, 0]
        self.db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = calculate_risk_for_signal(
            db=self.db,
            user=self.user,
            symbol="RELIANCE",
            exchange="NSE",
            direction="SELL",
            entry_price=2500.0,
            stoploss_price=2525.0,  # 1% above for SELL
            target_price=2450.0,    # 2% below for SELL
        )

        assert result.approved is True
        assert result.risk_reward_ratio == 2.0

    def test_position_sizing_formula(self):
        """Test that position sizing uses correct formula."""
        # Capital: 100,000, Risk per trade: 1% = 1,000
        # Entry: 2500, SL: 2475, Risk per share: 25
        # Expected quantity: floor(1000 / 25) = 40
        self.db.query.return_value.filter.return_value.scalar.side_effect = [0, 0]
        self.db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = calculate_risk_for_signal(
            db=self.db,
            user=self.user,
            symbol="RELIANCE",
            exchange="NSE",
            direction="BUY",
            entry_price=2500.0,
            stoploss_price=2475.0,
            target_price=2550.0,
        )

        assert result.approved is True
        assert result.quantity == 40
        assert result.capital_at_risk == 1000.0  # 40 * 25

    def test_user_overrides_global_settings(self):
        """Test that user-specific risk params override globals."""
        self.user.total_capital = Decimal("50000")
        self.user.risk_per_trade_pct = Decimal("2.0")
        self.user.max_daily_loss = Decimal("500")
        self.user.max_trades_per_day = 5
        self.user.cooldown_minutes = 30

        self.db.query.return_value.filter.return_value.scalar.side_effect = [0, 0]
        self.db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        result = calculate_risk_for_signal(
            db=self.db,
            user=self.user,
            symbol="RELIANCE",
            exchange="NSE",
            direction="BUY",
            entry_price=2500.0,
            stoploss_price=2475.0,
            target_price=2550.0,
        )

        # 50,000 * 2% = 1,000 risk, 1,000 / 25 = 40 shares
        assert result.quantity == 40
        assert result.capital_at_risk == 1000.0


class TestRiskEngineClass:
    """Tests for RiskEngine class (existing functionality)."""

    def setup_method(self):
        self.db = Mock()
        self.user = Mock(spec=User)
        self.user.id = "test-user"
        self.user.total_capital = Decimal("100000")
        self.user.risk_per_trade_pct = Decimal("1.0")
        self.user.max_daily_loss = Decimal("1000")
        self.user.max_trades_per_day = 3
        self.user.cooldown_minutes = 20

    def test_evaluate_buy(self):
        """Test RiskEngine.evaluate for BUY."""
        self.db.query.return_value.filter.return_value.scalar.side_effect = [0, 0]
        self.db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        engine = RiskEngine(self.db, self.user)
        result = engine.evaluate(
            symbol="RELIANCE",
            exchange="NSE",
            direction="BUY",
            entry_price=2500.0,
            stoploss_pct=1.0,
            target_pct=2.0,
        )

        assert result.approved is True
        assert result.quantity > 0
        assert result.stoploss_price == 2475.0  # 2500 * 0.99
        assert result.target_price == 2550.0    # 2500 * 1.02

    def test_evaluate_sell(self):
        """Test RiskEngine.evaluate for SELL."""
        self.db.query.return_value.filter.return_value.scalar.side_effect = [0, 0]
        self.db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        engine = RiskEngine(self.db, self.user)
        result = engine.evaluate(
            symbol="RELIANCE",
            exchange="NSE",
            direction="SELL",
            entry_price=2500.0,
            stoploss_pct=1.0,
            target_pct=2.0,
        )

        assert result.approved is True
        assert result.stoploss_price == 2525.0  # 2500 * 1.01
        assert result.target_price == 2450.0    # 2500 * 0.98

    def test_get_daily_summary(self):
        """Test daily summary retrieval."""
        self.db.query.return_value.filter.return_value.scalar.side_effect = [-200.0, 2]

        engine = RiskEngine(self.db, self.user)
        summary = engine.get_daily_summary()

        assert summary["total_pnl"] == -200.0
        assert summary["trade_count"] == 2
        assert summary["capital"] == 100000.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])