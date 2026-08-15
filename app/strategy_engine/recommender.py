"""
Strategy Recommender — Combines ranked signals, AI summary, and risk engine
into final Recommendation objects for the dashboard.
"""

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Recommendation as RecommendationModel, User, StrategySignal as StrategySignalModel
from app.schemas import (
    RankedSignal, Recommendation, RegimeResult, RiskCalculationResult
)
from app.risk_engine import calculate_risk_for_signal, RiskCalculationResult as RiskCalcResult
from app.strategy_engine.ai_summary import AISummaryGenerator, get_ai_summary_generator

logger = logging.getLogger(__name__)

# IST timezone
IST = timezone(timedelta(hours=5, minutes=30))


class StrategyRecommender:
    """Builds final recommendations from ranked signals."""

    def __init__(
        self,
        db: Session,
        user: User,
        ai_generator: Optional[AISummaryGenerator] = None,
    ):
        """Initialize the recommender.

        Args:
            db: Database session.
            user: User for risk calculations.
            ai_generator: Optional AI summary generator.
        """
        self.db = db
        self.user = user
        self.ai_generator = ai_generator or get_ai_summary_generator()

    def build_recommendations(
        self,
        ranked_signals: List[RankedSignal],
        regime_results: Dict[str, RegimeResult],
        max_recommendations: int = 3,
    ) -> List[Recommendation]:
        """Build final recommendations from ranked signals.

        Args:
            ranked_signals: Pre-ranked signals from SignalRanker.
            regime_results: Current regime for each symbol.
            max_recommendations: Maximum number of recommendations to produce.

        Returns:
            List of Recommendation objects (persisted to DB).
        """
        if not ranked_signals:
            logger.info("No signals to build recommendations from")
            return []

        recommendations = []

        for ranked in ranked_signals[:max_recommendations]:
            signal = ranked.signal
            regime = regime_results.get(signal.symbol)

            if not regime:
                logger.warning("No regime data for %s, skipping", signal.symbol)
                continue

            # Calculate risk metrics using existing risk engine
            risk_result = calculate_risk_for_signal(
                db=self.db,
                user=self.user,
                symbol=signal.symbol,
                exchange=signal.exchange,
                direction=signal.action,
                entry_price=signal.entry_price,
                stoploss_price=signal.stoploss_price,
                target_price=signal.target_price,
            )

            if not risk_result.approved:
                logger.info(
                    "Risk engine rejected %s %s: %s",
                    signal.action, signal.symbol, risk_result.reason
                )
                continue

            # Generate AI summary for top signals
            ai_summary = self.ai_generator.generate_summary(
                ranked_signals[:max_recommendations],
                regime_results,
                top_n=max_recommendations,
            )

            # Build recommendation
            rec = Recommendation(
                id=uuid.uuid4(),
                symbol=signal.symbol,
                exchange=signal.exchange,
                direction=signal.action,
                entry_price=risk_result.entry_price,
                stoploss_price=risk_result.stoploss_price,
                target_price=risk_result.target_price,
                quantity=risk_result.quantity,
                capital_at_risk=risk_result.capital_at_risk,
                risk_reward_ratio=risk_result.risk_reward_ratio,
                confidence_score=ranked.rank_score,
                regime=regime.regime.value,
                top_strategy_name=signal.strategy_name,
                ai_summary=ai_summary.get("rationale"),
                ai_key_risks=ai_summary.get("key_risks"),
                ai_invalidation=ai_summary.get("invalidation"),
                status="PENDING",
                expires_at=datetime.now(IST) + timedelta(hours=6),  # Expire EOD
            )

            # Persist to database
            self._persist_recommendation(rec, signal, risk_result, ai_summary)
            recommendations.append(rec)

            logger.info(
                "Built recommendation: %s %s @ %.2f (qty=%d, risk=₹%.2f, R:R=%.2f)",
                rec.direction, rec.symbol, rec.entry_price,
                rec.quantity, rec.capital_at_risk, rec.risk_reward_ratio or 0
            )

        return recommendations

    def _persist_recommendation(
        self,
        rec: Recommendation,
        signal,
        risk_result: RiskCalcResult,
        ai_summary: Dict[str, Any],
    ):
        """Persist recommendation to database."""
        db_rec = RecommendationModel(
            id=rec.id,
            symbol=rec.symbol,
            exchange=rec.exchange,
            direction=rec.direction,
            entry_price=rec.entry_price,
            stoploss_price=rec.stoploss_price,
            target_price=rec.target_price,
            quantity=rec.quantity,
            capital_at_risk=rec.capital_at_risk,
            risk_reward_ratio=rec.risk_reward_ratio,
            confidence_score=rec.confidence_score,
            regime=rec.regime,
            top_strategy_name=rec.top_strategy_name,
            ai_summary=rec.ai_summary,
            ai_key_risks=rec.ai_key_risks,
            ai_invalidation=rec.ai_invalidation,
            status=rec.status,
            expires_at=rec.expires_at,
        )
        self.db.add(db_rec)

        # Also persist the raw signal for audit
        db_signal = StrategySignalModel(
            strategy_name=signal.strategy_name,
            symbol=signal.symbol,
            exchange=signal.exchange,
            action=signal.action,
            confidence_score=signal.confidence_score,
            entry_price=signal.entry_price,
            stoploss_price=signal.stoploss_price,
            target_price=signal.target_price,
            suggested_quantity=signal.suggested_quantity,
            regime_at_signal=signal.regime_at_signal,
            metadata=signal.metadata,
        )
        self.db.add(db_signal)

        self.db.commit()

    def get_pending_recommendations(self) -> List[Recommendation]:
        """Get all pending recommendations from database."""
        db_recs = self.db.query(RecommendationModel).filter(
            RecommendationModel.status == "PENDING",
            RecommendationModel.expires_at > datetime.now(IST),
        ).order_by(RecommendationModel.created_at.desc()).all()

        return [self._db_to_schema(r) for r in db_recs]

    def mark_executed(self, recommendation_id: uuid.UUID, trade_id: uuid.UUID):
        """Mark a recommendation as executed."""
        db_rec = self.db.query(RecommendationModel).filter(
            RecommendationModel.id == recommendation_id
        ).first()

        if db_rec:
            db_rec.status = "EXECUTED"
            db_rec.executed_at = datetime.now(IST)
            db_rec.executed_trade_id = trade_id
            self.db.commit()
            logger.info("Marked recommendation %s as executed (trade=%s)",
                        recommendation_id, trade_id)

    def mark_rejected(self, recommendation_id: uuid.UUID, reason: str):
        """Mark a recommendation as rejected."""
        db_rec = self.db.query(RecommendationModel).filter(
            RecommendationModel.id == recommendation_id
        ).first()

        if db_rec:
            db_rec.status = "REJECTED"
            db_rec.ai_summary = (db_rec.ai_summary or "") + f"\n\n[Rejected: {reason}]"
            self.db.commit()

    def expire_old_recommendations(self):
        """Expire recommendations past their expiry time."""
        expired = self.db.query(RecommendationModel).filter(
            RecommendationModel.status == "PENDING",
            RecommendationModel.expires_at <= datetime.now(IST),
        ).all()

        for rec in expired:
            rec.status = "EXPIRED"
            logger.info("Expired recommendation %s for %s", rec.id, rec.symbol)

        if expired:
            self.db.commit()

    @staticmethod
    def _db_to_schema(db_rec: RecommendationModel) -> Recommendation:
        """Convert DB model to Pydantic schema."""
        return Recommendation(
            id=db_rec.id,
            symbol=db_rec.symbol,
            exchange=db_rec.exchange,
            direction=db_rec.direction,
            entry_price=float(db_rec.entry_price),
            stoploss_price=float(db_rec.stoploss_price),
            target_price=float(db_rec.target_price),
            quantity=db_rec.quantity,
            capital_at_risk=float(db_rec.capital_at_risk),
            risk_reward_ratio=float(db_rec.risk_reward_ratio) if db_rec.risk_reward_ratio else None,
            confidence_score=float(db_rec.confidence_score),
            regime=db_rec.regime,
            top_strategy_name=db_rec.top_strategy_name,
            ai_summary=db_rec.ai_summary,
            ai_key_risks=db_rec.ai_key_risks,
            ai_invalidation=db_rec.ai_invalidation,
            status=db_rec.status,
            created_at=db_rec.created_at,
            expires_at=db_rec.expires_at,
        )


def get_recommender(db: Session, user: User) -> StrategyRecommender:
    """Factory function to create a StrategyRecommender."""
    return StrategyRecommender(db, user)