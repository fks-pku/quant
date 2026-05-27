"""CSI300 proxy index-enhanced multi-factor A-share strategy."""

from __future__ import annotations

from typing import Any, List, Optional, Sequence

from quant.features.strategies._large_cap_forum_common import AShareLargeCapForumCompositeStrategy
from quant.features.strategies._mid_cap_common import ScoreSpec
from quant.features.strategies.registry import strategy


STRATEGY_NAME = "ashare_csi300_index_enhanced_multifactor"


@strategy(STRATEGY_NAME)
class AShareCsi300IndexEnhancedMultifactorStrategy(AShareLargeCapForumCompositeStrategy):
    def __init__(self, symbols: Optional[List[str]] = None, **kwargs: Any):
        defaults = {
            "holding_days": 20,
            "max_positions": 40,
            "target_weight_slots": 40,
            "max_position_pct": 0.95,
            "cap_percentile_low": 0.60,
            "cap_percentile_high": 1.00,
            "min_price": 5.0,
            "min_turnover": 200_000.0,
            "use_market_timing": False,
            "symbol_trend_ma": 0,
            "min_long_momentum": -0.40,
            "min_recent_momentum": -0.30,
            "max_volatility": 1.20,
            "min_drawdown": -0.60,
            "max_pb": 30.0,
            "max_ps_ttm": 50.0,
            "min_roe": 0.0,
            "max_debt_to_assets": 0.0,
            "min_dividend_yield": 0.0,
            "score_profile": "csi300_index_enhanced_multifactor",
            "max_replacements_per_rebalance": 10,
        }
        defaults.update(kwargs)
        super().__init__(STRATEGY_NAME, symbols=symbols, **defaults)

    @property
    def required_fields(self) -> List[str]:
        return [
            "total_mv",
            "circ_mv",
            "pe_ttm",
            "pb",
            "ps_ttm",
            "dv_ttm",
            "roe",
            "adj_close",
        ]

    @property
    def score_specs(self) -> Sequence[ScoreSpec]:
        return [
            ("momentum", 0.22, True),
            ("recent_momentum", 0.12, True),
            ("roe", 0.16, True),
            ("volatility", 0.14, False),
            ("pb", 0.12, False),
            ("pe_ttm", 0.10, False),
            ("turnover_rate", 0.08, False),
            ("dv_ttm", 0.06, True),
        ]
