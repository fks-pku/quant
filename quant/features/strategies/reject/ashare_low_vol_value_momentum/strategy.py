"""Large-cap low-volatility value momentum strategy."""

from __future__ import annotations

from typing import Any, List, Optional

from quant.features.strategies._large_cap_forum_common import AShareLargeCapForumCompositeStrategy
from quant.features.strategies.registry import strategy


STRATEGY_NAME = "ashare_low_vol_value_momentum"


@strategy(STRATEGY_NAME)
class AShareLowVolValueMomentumStrategy(AShareLargeCapForumCompositeStrategy):
    def __init__(self, symbols: Optional[List[str]] = None, **kwargs: Any):
        defaults = {
            "holding_days": 20,
            "max_positions": 25,
            "target_weight_slots": 25,
            "max_position_pct": 0.95,
            "cap_percentile_low": 0.70,
            "cap_percentile_high": 1.00,
            "min_price": 5.0,
            "min_turnover": 100_000.0,
            "use_market_timing": True,
            "timing_ma": 120,
            "timing_exit_buffer": 0.96,
            "timing_momentum_lookback": 40,
            "min_timing_momentum": -0.10,
            "symbol_trend_ma": 60,
            "symbol_exit_buffer": 0.95,
            "min_long_momentum": -0.10,
            "min_recent_momentum": -0.08,
            "max_volatility": 0.70,
            "min_drawdown": -0.40,
            "max_pb": 12.0,
            "max_ps_ttm": 18.0,
            "score_profile": "low_vol_value_momentum",
            "stop_loss_pct": 0.10,
            "take_profit_pct": 0.0,
            "trailing_stop_pct": 0.0,
        }
        defaults.update(kwargs)
        super().__init__(STRATEGY_NAME, symbols=symbols, **defaults)
