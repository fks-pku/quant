"""A-share dividend low-volatility quality enhanced strategy."""

from __future__ import annotations

from typing import Any, List, Optional, Sequence

from quant.features.strategies._large_cap_forum_common import AShareLargeCapForumCompositeStrategy
from quant.features.strategies._mid_cap_common import ScoreSpec
from quant.features.strategies.registry import strategy


STRATEGY_NAME = "ashare_dividend_low_vol_quality_enhanced"


@strategy(STRATEGY_NAME)
class AShareDividendLowVolQualityEnhancedStrategy(AShareLargeCapForumCompositeStrategy):
    def __init__(self, symbols: Optional[List[str]] = None, **kwargs: Any):
        defaults = {
            "holding_days": 60,
            "max_positions": 10,
            "target_weight_slots": 10,
            "max_position_pct": 0.95,
            "cap_percentile_low": 0.50,
            "cap_percentile_high": 1.00,
            "min_price": 5.0,
            "min_turnover": 80_000.0,
            "use_market_timing": True,
            "timing_ma": 200,
            "timing_exit_buffer": 0.95,
            "timing_momentum_lookback": 60,
            "min_timing_momentum": -0.12,
            "symbol_trend_ma": 0,
            "min_long_momentum": -0.20,
            "min_recent_momentum": -0.15,
            "max_volatility": 0.60,
            "min_drawdown": -0.45,
            "max_pb": 15.0,
            "max_ps_ttm": 25.0,
            "min_roe": 6.0,
            "max_debt_to_assets": 95.0,
            "min_dividend_yield": 1.0,
            "score_profile": "dividend_low_vol_quality_enhanced",
        }
        defaults.update(kwargs)
        super().__init__(STRATEGY_NAME, symbols=symbols, **defaults)

    @property
    def score_specs(self) -> Sequence[ScoreSpec]:
        return [
            ("dv_ttm", 0.28, True),
            ("volatility", 0.20, False),
            ("roe", 0.16, True),
            ("grossprofit_margin", 0.10, True),
            ("debt_to_assets", 0.10, False),
            ("pb", 0.08, False),
            ("drawdown", 0.05, True),
            ("recent_momentum", 0.03, True),
        ]
