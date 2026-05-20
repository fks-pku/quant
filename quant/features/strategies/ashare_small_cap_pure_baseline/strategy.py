"""A-share pure small-cap baseline."""

from typing import List, Optional

from quant.features.strategies._small_cap_common import AShareSmallCapRotationBase
from quant.features.strategies.registry import strategy


@strategy("ashare_small_cap_pure_baseline")
class AShareSmallCapPureBaselineStrategy(AShareSmallCapRotationBase):
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
            "ashare_small_cap_pure_baseline",
            symbols=symbols,
            max_positions=max_positions,
            rebalance_interval=rebalance_interval,
            min_price=min_price,
            min_adv_value=min_adv_value,
            lot_size=lot_size,
        )
