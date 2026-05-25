"""A-share small-cap quality reversal."""

import math
from typing import Any, List, Optional

from quant.features.strategies._small_cap_common import AShareSmallCapRotationBase
from quant.features.strategies.registry import strategy


@strategy("ashare_small_cap_quality_reversal")
class AShareSmallCapQualityReversalStrategy(AShareSmallCapRotationBase):
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        max_positions: int = 20,
        rebalance_interval: int = 10,
        min_price: float = 5.0,
        min_adv_value: float = 20000.0,
        lot_size: int = 100,
    ):
        super().__init__(
            "ashare_small_cap_quality_reversal",
            symbols=symbols,
            max_positions=max_positions,
            rebalance_interval=rebalance_interval,
            min_price=min_price,
            min_adv_value=min_adv_value,
            lot_size=lot_size,
        )

    def _candidate_score(self, symbol: str, bar: Any) -> float:
        market_cap = max(self._market_cap(bar), 1e-9)
        score = -math.log(market_cap)

        ret5 = self._return(symbol, 5)
        if ret5 is not None:
            if -0.12 <= ret5 <= -0.02:
                score += 1.0
            elif -0.25 <= ret5 < -0.12:
                score += 0.2
            elif ret5 > 0.20:
                score -= 1.2
            elif ret5 > 0.05:
                score -= 0.4
            elif ret5 < -0.35:
                score -= 1.0

        ret20 = self._return(symbol, 20)
        if ret20 is not None:
            if -0.25 <= ret20 <= 0.25:
                score += 0.2
            elif ret20 > 0.45:
                score -= 0.8
            elif ret20 < -0.35:
                score -= 0.5

        ret60 = self._return(symbol, 60)
        if ret60 is not None:
            if -0.20 <= ret60 <= 0.60:
                score += 0.3
            elif ret60 < -0.40:
                score -= 0.7
            elif ret60 > 0.90:
                score -= 0.5

        pb = self._float_value(self._value(bar, "pb", None), 0.0)
        if pb > 0:
            score -= 0.35 * math.log(max(pb, 0.05))
        else:
            score -= 0.3

        ps = self._float_value(self._value(bar, "ps", None), 0.0)
        if ps > 0:
            score -= 0.30 * math.log(max(ps, 0.05))
        else:
            score -= 0.2

        pe = self._float_value(self._value(bar, "pe_ttm", None), 0.0)
        if pe <= 0:
            pe = self._float_value(self._value(bar, "pe", None), 0.0)
        if 0 < pe <= 60:
            score += 0.2
        elif pe > 100 or pe <= 0:
            score -= 0.6

        turnover_rate = self._float_value(self._value(bar, "turnover_rate_f", None), 0.0)
        if turnover_rate <= 0:
            turnover_rate = self._float_value(self._value(bar, "turnover_rate", None), 0.0)
        if turnover_rate > 25:
            score -= 0.8
        elif 1 <= turnover_rate <= 12:
            score += 0.1

        volume_ratio = self._float_value(self._value(bar, "volume_ratio", None), 0.0)
        if volume_ratio > 3:
            score -= 0.5
        elif 0 < volume_ratio <= 2:
            score += 0.1

        return score
