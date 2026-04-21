"""MarketAssessor - VIX-based market regime assessment."""

from typing import Dict, Optional

from quant.features.cio.weight_allocator import (
    REGIME_HIGH_VOL,
    REGIME_LOW_VOL,
    REGIME_MEDIUM_VOL,
)


class MarketAssessor:
    def __init__(
        self,
        vix_bull_threshold: float = 15.0,
        vix_bear_threshold: float = 25.0,
        vix_lookback: int = 20,
    ):
        self.vix_bull_threshold = vix_bull_threshold
        self.vix_bear_threshold = vix_bear_threshold
        self.vix_lookback = vix_lookback
        self._vix_history: list = []

    def update_vix(self, value: float) -> None:
        """Append VIX value to internal history."""
        self._vix_history.append(value)
        if len(self._vix_history) > self.vix_lookback:
            self._vix_history.pop(0)

    def assess(self, indicators: Optional[Dict] = None) -> Dict:
        """Assess market regime and return assessment dict."""
        indicators = indicators or {}
        regime = self._determine_regime()
        score = self._compute_score(regime, indicators)
        return {
            "regime": regime,
            "score": score,
            "indicators": indicators,
        }

    def _determine_regime(self) -> str:
        """Determine regime based on VIX levels."""
        if not self._vix_history:
            return REGIME_MEDIUM_VOL
        current_vix = self._vix_history[-1]
        if current_vix < self.vix_bull_threshold:
            return REGIME_LOW_VOL
        elif current_vix > self.vix_bear_threshold:
            return REGIME_HIGH_VOL
        return REGIME_MEDIUM_VOL

    def _compute_score(self, regime: str, indicators: Dict) -> float:
        """Compute 0-100 score from regime and indicators."""
        base_score = 50.0
        if regime == REGIME_LOW_VOL:
            base_score = 70.0
        elif regime == REGIME_HIGH_VOL:
            base_score = 30.0
        return base_score
