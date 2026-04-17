"""WeightAllocator - Strategy weight allocation based on market regime."""

from typing import Dict, List, Optional

REGIME_LOW_VOL = "low_vol_bull"
REGIME_MEDIUM_VOL = "medium_vol_chop"
REGIME_HIGH_VOL = "high_vol_bear"


class WeightAllocator:
    REGIME_WEIGHTS: Dict[str, Dict[str, float]] = {
        REGIME_LOW_VOL: {
            "volatility_regime": 0.40,
            "simple_momentum": 0.35,
            "cross_sectional_mr": 0.15,
            "dual_momentum": 0.10,
        },
        REGIME_MEDIUM_VOL: {
            "volatility_regime": 0.35,
            "simple_momentum": 0.20,
            "cross_sectional_mr": 0.30,
            "dual_momentum": 0.15,
        },
        REGIME_HIGH_VOL: {
            "volatility_regime": 0.50,
            "simple_momentum": 0.10,
            "cross_sectional_mr": 0.25,
            "dual_momentum": 0.15,
        },
    }

    def __init__(self, custom_weights: Optional[Dict[str, Dict[str, float]]] = None):
        self.custom_weights = custom_weights

    def allocate(
        self, regime: str, enabled_strategies: Optional[List[str]] = None
    ) -> Dict[str, float]:
        """Allocate weights for strategies based on regime."""
        weights = self.custom_weights or self.REGIME_WEIGHTS
        regime_weights = weights.get(regime, weights[REGIME_MEDIUM_VOL])
        if enabled_strategies is not None:
            regime_weights = {
                k: v for k, v in regime_weights.items() if k in enabled_strategies
            }
        return self._normalize(regime_weights)

    def _normalize(self, weights: Dict[str, float]) -> Dict[str, float]:
        """Normalize weights to sum to 1.0."""
        total = sum(weights.values())
        if total == 0:
            return weights
        return {k: v / total for k, v in weights.items()}
