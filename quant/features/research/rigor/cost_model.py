import math
from typing import Mapping, Optional

from quant.features.research.models import CostEstimate


class CostModel:
    def __init__(self, config: Optional[Mapping] = None):
        self.config = dict(config or {})

    def estimate_trade(
        self,
        trade_value: float,
        average_daily_volume: float,
        price: float,
        volatility: float,
        participation_rate: Optional[float] = None,
    ) -> CostEstimate:
        trade_value = abs(float(trade_value or 0.0))
        average_daily_volume = float(average_daily_volume or 0.0)
        price = float(price or 0.0)
        volatility = max(0.0, float(volatility or 0.0))

        max_adv_pct = float(self.config.get("max_adv_pct", 0.05))
        commission_bps = float(self.config.get("commission_bps", 0.0))
        spread_bps = float(self.config.get("spread_bps", 2.0))
        impact_coefficient = float(self.config.get("impact_coefficient", 10.0))

        if average_daily_volume <= 0 or price <= 0:
            participation = 1.0
            capacity_adv_pct = 1.0
            capacity_ok = False
        else:
            participation = (
                max(0.0, float(participation_rate))
                if participation_rate is not None
                else trade_value / average_daily_volume
            )
            capacity_adv_pct = participation
            capacity_ok = capacity_adv_pct <= max_adv_pct

        market_impact_bps = impact_coefficient * volatility * math.sqrt(max(participation, 0.0)) * 100.0
        total_bps = commission_bps + spread_bps + market_impact_bps
        return CostEstimate(
            commission=commission_bps,
            spread_cost=spread_bps,
            market_impact=market_impact_bps,
            total_bps=total_bps,
            capacity_adv_pct=capacity_adv_pct,
            capacity_ok=capacity_ok,
        )
