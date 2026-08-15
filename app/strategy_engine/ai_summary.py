"""
AI Summary Generator — Uses OpenAI to generate structured rationale for recommendations.

Calls OpenAI API with ranked signals + regime context, asks for JSON summary
with rationale, key risks, and invalidation conditions.
"""

import json
import logging
import os
from typing import List, Optional, Dict, Any

from openai import OpenAI

from app.config import settings
from app.schemas import RankedSignal, RegimeResult

logger = logging.getLogger(__name__)


class AISummaryGenerator:
    """Generates AI-powered summaries for trade recommendations."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",  # Low-cost model
        temperature: float = 0.3,
        max_tokens: int = 500,
    ):
        """Initialize the AI summary generator.

        Args:
            api_key: OpenAI API key (defaults to settings.openai_api_key or env).
            model: OpenAI model to use.
            temperature: Sampling temperature (lower = more deterministic).
            max_tokens: Maximum tokens in response.
        """
        self.api_key = api_key or getattr(settings, 'openai_api_key', None) or os.getenv('OPENAI_API_KEY')
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

        if self.api_key:
            self.client = OpenAI(api_key=self.api_key)
        else:
            self.client = None
            logger.warning("OpenAI API key not configured — AI summaries will be disabled")

    def generate_summary(
        self,
        ranked_signals: List[RankedSignal],
        regime_results: Dict[str, RegimeResult],
        top_n: int = 3,
    ) -> Dict[str, Any]:
        """Generate a structured JSON summary for the top recommendations.

        Args:
            ranked_signals: List of ranked signals (already sorted by rank).
            regime_results: Dict of symbol -> RegimeResult.
            top_n: Number of top signals to include in summary.

        Returns:
            Dict with keys: rationale, key_risks, invalidation, confidence_assessment
        """
        if not self.client:
            return self._fallback_summary(ranked_signals[:top_n])

        if not ranked_signals:
            return self._empty_summary()

        top_signals = ranked_signals[:top_n]

        # Build prompt
        prompt = self._build_prompt(top_signals, regime_results)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            return self._parse_response(content)

        except Exception as e:
            logger.error("OpenAI API call failed: %s", e)
            return self._fallback_summary(top_signals)

    def _system_prompt(self) -> str:
        return """You are a professional quantitative trading analyst. Analyze the provided 
ranked trade signals and market regime data to produce a concise, structured JSON summary 
for a human trader's review.

Your output MUST be valid JSON with exactly these keys:
- "rationale": Plain-English explanation of why the top signal(s) make sense given the regime
- "key_risks": Specific risks that could cause the trade to fail
- "invalidation": Specific price action or conditions that would invalidate the setup
- "confidence_assessment": Overall confidence level (high/medium/low) with brief justification

Be concise (2-3 sentences per field). Focus on actionable insights."""

    def _build_prompt(
        self,
        signals: List[RankedSignal],
        regime_results: Dict[str, RegimeResult],
    ) -> str:
        lines = ["=== TOP RANKED SIGNALS ==="]

        for i, rs in enumerate(signals, 1):
            s = rs.signal
            lines.append(f"\n{i}. {s.strategy_name} — {s.action} {s.symbol}")
            lines.append(f"   Confidence: {s.confidence_score:.2f} | Rank Score: {rs.rank_score:.3f}")
            lines.append(f"   Entry: ₹{s.entry_price:.2f} | SL: ₹{s.stoploss_price:.2f} | Target: ₹{s.target_price:.2f}")
            lines.append(f"   Regime at signal: {s.regime_at_signal}")
            if rs.historical_performance is not None:
                lines.append(f"   Historical Win Rate: {rs.historical_performance:.1%}")
            if rs.regime_fit is not None:
                lines.append(f"   Regime Fit: {rs.regime_fit:.2f}")

        lines.append("\n=== MARKET REGIME CONTEXT ===")
        for symbol, regime in regime_results.items():
            lines.append(f"{symbol}: {regime.regime.value} (ADX: {regime.adx}, "
                         f"ATR: {regime.atr}, BB Width: {regime.bb_bandwidth}, "
                         f"Confidence: {regime.confidence:.2f})")

        lines.append("\nGenerate JSON summary with: rationale, key_risks, invalidation, confidence_assessment")
        return "\n".join(lines)

    def _parse_response(self, content: str) -> Dict[str, Any]:
        """Parse and validate OpenAI JSON response."""
        try:
            # Strip markdown fences if present
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            data = json.loads(content)

            # Validate required keys
            required = ["rationale", "key_risks", "invalidation", "confidence_assessment"]
            for key in required:
                if key not in data:
                    data[key] = "Not provided"

            return data

        except json.JSONDecodeError as e:
            logger.error("Failed to parse OpenAI JSON response: %s", e)
            logger.debug("Raw response: %s", content)
            return self._fallback_summary([])

    def _fallback_summary(self, signals: List[RankedSignal]) -> Dict[str, Any]:
        """Generate a basic summary without AI."""
        if not signals:
            return self._empty_summary()

        top = signals[0].signal
        return {
            "rationale": (
                f"{top.strategy_name} signal for {top.action} {top.symbol} with "
                f"{top.confidence_score:.0%} confidence. "
                f"Entry at ₹{top.entry_price:.2f} with SL ₹{top.stoploss_price:.2f} "
                f"and target ₹{top.target_price:.2f}."
            ),
            "key_risks": (
                "Market regime may shift against the strategy. "
                "Liquidity risk in low-volume periods. "
                "Gap risk overnight for MIS positions."
            ),
            "invalidation": (
                f"Price moves beyond stop-loss at ₹{top.stoploss_price:.2f}. "
                "Regime changes to opposing type. "
                "Major news event invalidates technical setup."
            ),
            "confidence_assessment": (
                f"Medium — Based on {top.strategy_name} confidence score of {top.confidence_score:.0%}. "
                "No AI enhancement available."
            ),
        }

    def _empty_summary(self) -> Dict[str, Any]:
        return {
            "rationale": "No signals generated.",
            "key_risks": "N/A",
            "invalidation": "N/A",
            "confidence_assessment": "Low — No actionable signals.",
        }


def get_ai_summary_generator() -> AISummaryGenerator:
    """Factory function to create AI summary generator."""
    return AISummaryGenerator()