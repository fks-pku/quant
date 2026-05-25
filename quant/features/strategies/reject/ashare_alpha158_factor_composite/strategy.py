"""Large-cap alpha158-style factor composite for A-shares."""

from __future__ import annotations

from typing import Any, List, Optional

from quant.features.strategies._large_cap_forum_common import AShareLargeCapForumCompositeStrategy
from quant.features.strategies.registry import strategy


STRATEGY_NAME = "ashare_alpha158_factor_composite"


@strategy(STRATEGY_NAME)
class AShareAlpha158FactorCompositeStrategy(AShareLargeCapForumCompositeStrategy):
    def __init__(self, symbols: Optional[List[str]] = None, **kwargs: Any):
        defaults = {
            "holding_days": 20,
            "max_positions": 20,
            "target_weight_slots": 20,
            "max_position_pct": 0.95,
            "cap_percentile_low": 0.80,
            "cap_percentile_high": 1.00,
            "min_price": 5.0,
            "min_turnover": 100_000.0,
            "use_market_timing": True,
            "timing_ma": 120,
            "timing_exit_buffer": 0.96,
            "timing_momentum_lookback": 60,
            "min_timing_momentum": -0.10,
            "symbol_trend_ma": 60,
            "symbol_exit_buffer": 0.95,
            "min_long_momentum": -0.12,
            "min_recent_momentum": -0.08,
            "max_volatility": 0.75,
            "min_drawdown": -0.45,
            "score_profile": "alpha158_factor_composite",
            "stop_loss_pct": 0.12,
            "take_profit_pct": 0.0,
            "trailing_stop_pct": 0.0,
        }
        defaults.update(kwargs)
        super().__init__(STRATEGY_NAME, symbols=symbols, **defaults)
