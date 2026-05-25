"""Large-cap dividend low-volatility smart-beta strategy."""

from __future__ import annotations

from typing import Any, List, Optional

from quant.features.strategies._large_cap_forum_common import AShareLargeCapForumCompositeStrategy
from quant.features.strategies.registry import strategy


STRATEGY_NAME = "ashare_dividend_low_vol_smart_beta"


@strategy(STRATEGY_NAME)
class AShareDividendLowVolSmartBetaStrategy(AShareLargeCapForumCompositeStrategy):
    def __init__(self, symbols: Optional[List[str]] = None, **kwargs: Any):
        defaults = {
            "holding_days": 60,
            "max_positions": 30,
            "target_weight_slots": 30,
            "max_position_pct": 0.95,
            "cap_percentile_low": 0.60,
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
            "min_dividend_yield": 0.5,
            "score_profile": "dividend_low_vol_smart_beta",
        }
        defaults.update(kwargs)
        super().__init__(STRATEGY_NAME, symbols=symbols, **defaults)
