"""White-horse quality strategy with market-temperature timing."""

from __future__ import annotations

from typing import Any, List, Optional

from quant.features.strategies._large_cap_forum_common import AShareLargeCapForumCompositeStrategy
from quant.features.strategies.registry import strategy


STRATEGY_NAME = "ashare_white_horse_market_temperature"


@strategy(STRATEGY_NAME)
class AShareWhiteHorseMarketTemperatureStrategy(AShareLargeCapForumCompositeStrategy):
    def __init__(self, symbols: Optional[List[str]] = None, **kwargs: Any):
        defaults = {
            "holding_days": 20,
            "max_positions": 18,
            "target_weight_slots": 18,
            "max_position_pct": 0.90,
            "cap_percentile_low": 0.85,
            "cap_percentile_high": 1.00,
            "min_price": 5.0,
            "min_turnover": 120_000.0,
            "use_market_timing": True,
            "timing_ma": 200,
            "timing_exit_buffer": 0.97,
            "timing_momentum_lookback": 60,
            "min_timing_momentum": -0.08,
            "symbol_trend_ma": 120,
            "symbol_exit_buffer": 0.96,
            "min_long_momentum": -0.18,
            "min_recent_momentum": -0.10,
            "max_volatility": 0.65,
            "min_drawdown": -0.40,
            "max_pb": 18.0,
            "max_ps_ttm": 25.0,
            "min_roe": 5.0,
            "max_debt_to_assets": 75.0,
            "score_profile": "white_horse_temperature",
            "stop_loss_pct": 0.10,
            "take_profit_pct": 0.0,
            "trailing_stop_pct": 0.0,
        }
        defaults.update(kwargs)
        super().__init__(STRATEGY_NAME, symbols=symbols, **defaults)
