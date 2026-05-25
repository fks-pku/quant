"""Large-cap low-turnover multi-factor A-share strategy."""

from __future__ import annotations

from typing import Any, List, Optional

from quant.features.strategies._large_cap_forum_common import AShareLargeCapForumCompositeStrategy
from quant.features.strategies.registry import strategy


STRATEGY_NAME = "ashare_csi300_low_turnover_multifactor"


@strategy(STRATEGY_NAME)
class AShareCsi300LowTurnoverMultifactorStrategy(AShareLargeCapForumCompositeStrategy):
    def __init__(self, symbols: Optional[List[str]] = None, **kwargs: Any):
        defaults = {
            "holding_days": 20,
            "max_positions": 7,
            "target_weight_slots": 7,
            "max_position_pct": 0.95,
            "cap_percentile_low": 0.90,
            "cap_percentile_high": 1.00,
            "min_price": 5.0,
            "min_turnover": 120_000.0,
            "use_market_timing": False,
            "symbol_trend_ma": 60,
            "symbol_exit_buffer": 0.95,
            "min_long_momentum": -0.20,
            "min_recent_momentum": -0.12,
            "max_volatility": 0.80,
            "min_drawdown": -0.45,
            "max_pb": 20.0,
            "max_ps_ttm": 30.0,
            "score_profile": "csi300_low_turnover_multifactor",
            "max_replacements_per_rebalance": 1,
        }
        defaults.update(kwargs)
        super().__init__(STRATEGY_NAME, symbols=symbols, **defaults)
